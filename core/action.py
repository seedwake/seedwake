"""Phase 3 action planning and execution."""

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock

from ollama import RequestError as OllamaRequestError, ResponseError as OllamaResponseError
from redis import exceptions as redis_exceptions

from core.perception import collect_system_status_snapshot
from core.stimulus import StimulusQueue
from core.thought_parser import Thought
from core.types import (
    ActionControl,
    ActionRequestPayload,
    ActionResultEnvelope,
    JsonObject,
    NewsItem,
)

ACTION_REDIS_KEY = "seedwake:actions"
ACTION_CONTROL_KEY = "seedwake:action_control"
NEWS_SEEN_REDIS_KEY = "seedwake:news_seen"
OPENCLAW_ACTION_TYPES = {"search", "web_fetch", "system_change", "custom", "news", "weather", "reading"}
PERCEPTION_AUTO_EXECUTE_TYPES = {"news", "weather", "reading"}
ACTION_REDIS_EXCEPTIONS = (
    redis_exceptions.RedisError,
    ConnectionError,
    TimeoutError,
    OSError,
    RuntimeError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)
PLANNER_EXCEPTIONS = (
    OllamaRequestError,
    OllamaResponseError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)
ACTION_EXECUTION_EXCEPTIONS = (
    OllamaRequestError,
    OllamaResponseError,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)
logger = logging.getLogger(__name__)


@dataclass
class ActionPlan:
    action_type: str
    executor: str
    task: str
    timeout_seconds: int
    reason: str


@dataclass
class ActionRecord:
    action_id: str
    type: str
    request: ActionRequestPayload
    executor: str
    status: str
    source_thought_id: str
    source_content: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timeout_seconds: int = 300
    result: ActionResultEnvelope | None = None
    run_id: str | None = None
    session_key: str | None = None
    awaiting_confirmation: bool = False


class ActionManager:
    """Owns action planning, execution, state, and result stimuli."""

    def __init__(
        self,
        redis_client,
        stimulus_queue: StimulusQueue,
        planner,
        openclaw_executor,
        *,
        auto_execute: list[str],
        require_confirmation: list[str],
        forbidden: list[str],
        news_seen_ttl_hours: int = 720,
        news_seen_max_items: int = 5000,
        log_callback=None,
        event_callback=None,
    ):
        self._redis = redis_client
        self._stimulus_queue = stimulus_queue
        self._planner = planner
        self._openclaw_executor = openclaw_executor
        self._auto_execute = set(auto_execute)
        self._require_confirmation = set(require_confirmation)
        self._forbidden = set(forbidden)
        self._news_seen_ttl_hours = max(1, news_seen_ttl_hours)
        self._news_seen_max_items = max(1, news_seen_max_items)
        self._log_callback = log_callback
        self._event_callback = event_callback
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="seedwake-action")
        self._lock = Lock()
        self._actions: dict[str, ActionRecord] = {}
        self._news_seen_shadow: dict[str, float] = {}
        self._perception_observations: list[str] = []

    def submit_from_thoughts(self, thoughts: list[Thought]) -> list[ActionRecord]:
        created: list[ActionRecord] = []
        for thought in thoughts:
            if not thought.action_request:
                continue

            try:
                plan = self._planner.plan(thought)
            except PLANNER_EXCEPTIONS as exc:
                self._emit(f"行动规划失败 {thought.thought_id}: {exc}")
                continue
            if not plan:
                continue

            action = ActionRecord(
                action_id=f"act_{thought.thought_id}",
                type=plan.action_type,
                request={
                    "task": plan.task,
                    "reason": plan.reason,
                    "raw_action": thought.action_request,
                },
                executor=plan.executor,
                status="pending",
                source_thought_id=thought.thought_id,
                source_content=thought.content,
                timeout_seconds=plan.timeout_seconds,
            )
            self._upsert_action(action)
            created.append(action)

            policy = self._classify_policy(action)
            if policy == "forbidden":
                blocked_reason = "行动类型被安全策略禁止"
                self._finalize_action(
                    action.action_id,
                    status="failed",
                    result={
                        "ok": False,
                        "summary": blocked_reason,
                        "data": {},
                        "error": blocked_reason,
                    },
                )
                continue
            if policy == "confirmation":
                action = self._update_action(action.action_id, awaiting_confirmation=True)
                self._emit(f"行动等待确认 {action.action_id} [{action.type}/{action.executor}]")
                self._publish_action_event(action, "pending", "需要管理员确认")
                continue
            if policy == "rejected":
                blocked_reason = "行动未进入自动执行白名单"
                self._finalize_action(
                    action.action_id,
                    status="failed",
                    result={
                        "ok": False,
                        "summary": blocked_reason,
                        "data": {},
                        "error": blocked_reason,
                    },
                )
                continue

            self._start_action(action.action_id)

        return created

    def apply_controls(self, controls: list[ActionControl]) -> None:
        for control in controls:
            action_id = str(control.get("action_id") or "").strip()
            if not action_id:
                continue
            with self._lock:
                action = self._actions.get(action_id)
            if action is None or not action.awaiting_confirmation:
                continue

            approved = bool(control.get("approved"))
            actor = str(control.get("actor") or "admin")
            note = str(control.get("note") or "").strip()
            if approved:
                self._emit(f"行动已确认 {action_id} by {actor}")
                action = self._update_action(action_id, awaiting_confirmation=False)
                self._publish_action_event(action, "pending", f"已确认，准备执行（{actor}）")
                self._start_action(action_id)
                continue

            summary = f"管理员拒绝执行（{actor}）"
            if note:
                summary = f"{summary}: {note}"
            self._emit(f"行动被拒绝 {action_id} by {actor}")
            self._update_action(action_id, awaiting_confirmation=False)
            self._finalize_action(
                action_id,
                status="failed",
                result={
                    "ok": False,
                    "summary": summary,
                    "data": {},
                    "error": "rejected",
                },
            )

    def running_actions(self) -> list[ActionRecord]:
        with self._lock:
            actions = [
                action
                for action in self._actions.values()
                if action.status in {"pending", "running"}
            ]
        return sorted(actions, key=lambda action: action.submitted_at)

    def pop_perception_observations(self) -> list[str]:
        with self._lock:
            observations = list(self._perception_observations)
            self._perception_observations.clear()
        return observations

    def attach_redis(self, redis_client) -> bool:
        self._redis = redis_client
        try:
            self._sync_to_redis()
        except ACTION_REDIS_EXCEPTIONS:
            self._redis = None
        return self.redis_available

    @property
    def redis_available(self) -> bool:
        return self._redis is not None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)

    def _start_action(self, action_id: str) -> None:
        action = self._get_action(action_id)
        self._emit(f"行动提交 {action.action_id} [{action.type}/{action.executor}]")
        self._publish_action_event(action, "pending", "已提交")
        self._pool.submit(self._run_action, action_id)

    def _run_action(self, action_id: str) -> None:
        try:
            action = self._get_action(action_id)
            self._update_action(action_id, status="running")
            self._publish_action_event(action, "running", "执行中")

            if action.executor == "native":
                result = _run_native_action(action)
            else:
                result = self._openclaw_executor.execute(action)
        except TimeoutError:
            self._safe_finalize_action(
                action_id,
                status="timeout",
                result={
                    "ok": False,
                    "summary": "行动超时",
                    "data": {},
                    "error": "timeout",
                },
            )
            return
        except ACTION_EXECUTION_EXCEPTIONS as exc:
            self._safe_finalize_action(
                action_id,
                status="failed",
                result={
                    "ok": False,
                    "summary": f"行动失败：{exc}",
                    "data": {},
                    "error": str(exc),
                },
            )
            return
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("unexpected action worker failure: %s", action_id)
            self._force_fail_action(action_id, f"行动内部错误：{exc}")
            return

        result = _normalize_action_result(result, action)
        status = "succeeded" if result.get("ok", True) else "failed"
        self._safe_finalize_action(action_id, status=status, result=result)

    def _finalize_action(self, action_id: str, *, status: str, result: ActionResultEnvelope) -> None:
        action = self._get_action(action_id)
        result = _normalize_action_result(result, action)
        status, result, should_emit_stimulus = self._prepare_result_for_stimulus(action, status, result)
        action = self._update_action(action_id, status=status, result=result)
        if isinstance(result.get("run_id"), str):
            action.run_id = str(result["run_id"])
        if isinstance(result.get("session_key"), str):
            action.session_key = str(result["session_key"])
        self._upsert_action(action)

        summary = str(result.get("summary") or "行动完成")
        self._emit(f"行动结束 {action.action_id} [{status}] {summary}")
        self._publish_action_event(action, status, summary)
        self._record_perception_observation(action, status, result)
        if not should_emit_stimulus:
            return
        stimulus = _build_result_stimulus(action, status, result)
        self._stimulus_queue.push(
            stimulus["type"],
            stimulus["priority"],
            stimulus["source"],
            stimulus["content"],
            action_id=action.action_id,
            metadata=stimulus["metadata"],
        )

    def _classify_policy(self, action: ActionRecord) -> str:
        if action.type in self._forbidden:
            return "forbidden"
        if action.type in self._require_confirmation:
            return "confirmation"
        if action.executor == "native":
            return "auto"
        if action.type in PERCEPTION_AUTO_EXECUTE_TYPES:
            return "auto"
        if self._auto_execute and action.type not in self._auto_execute:
            return "rejected"
        return "auto"

    def _safe_finalize_action(
        self,
        action_id: str,
        *,
        status: str,
        result: ActionResultEnvelope,
    ) -> None:
        try:
            self._finalize_action(action_id, status=status, result=result)
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("unexpected action finalization failure: %s", action_id)
            self._force_fail_action(action_id, f"行动收尾失败：{exc}")

    def _force_fail_action(self, action_id: str, summary: str) -> None:
        fallback_result = {
            "ok": False,
            "summary": summary,
            "data": {},
            "error": summary,
        }
        try:
            self._finalize_action(action_id, status="failed", result=fallback_result)
            return
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("failed to finalize forced action failure: %s (%s)", action_id, exc)

        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                return
            action.status = "failed"
            action.result = _normalize_action_result(fallback_result, action)
            self._actions[action_id] = action
        try:
            self._upsert_action(action)
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("failed to persist forced action failure: %s (%s)", action_id, exc)

    def _emit(self, text: str) -> None:
        if self._log_callback:
            self._log_callback(text)

    def _publish_action_event(self, action: ActionRecord, status: str, summary: str) -> None:
        if not self._event_callback:
            return
        self._event_callback("action", {
            "action_id": action.action_id,
            "type": action.type,
            "executor": action.executor,
            "status": status,
            "source_thought_id": action.source_thought_id,
            "summary": summary,
            "run_id": action.run_id,
            "session_key": action.session_key,
            "awaiting_confirmation": action.awaiting_confirmation,
        })

    def _record_perception_observation(
        self,
        action: ActionRecord,
        status: str,
        result: ActionResultEnvelope,
    ) -> None:
        stimulus_type = _infer_stimulus_type(action, status, result)
        if stimulus_type not in {"time", "system_status", "news", "weather", "reading"}:
            return
        with self._lock:
            self._perception_observations.append(stimulus_type)

    def _prepare_result_for_stimulus(
        self,
        action: ActionRecord,
        status: str,
        result: ActionResultEnvelope,
    ) -> tuple[str, ActionResultEnvelope, bool]:
        if status != "succeeded" or action.type != "news" or not bool(result.get("ok", True)):
            return status, result, True
        if not _is_structured_news_result(result):
            malformed = dict(result)
            malformed["ok"] = False
            malformed["summary"] = "新闻结果缺少结构化 RSS 条目"
            malformed["error"] = "malformed_news_result"
            return "failed", malformed, True
        deduped_result, should_emit = self._dedupe_news_result(result)
        if not bool(deduped_result.get("ok", True)):
            return "failed", deduped_result, True
        return status, deduped_result, should_emit

    def _dedupe_news_result(self, result: ActionResultEnvelope) -> tuple[ActionResultEnvelope, bool]:
        data = result.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return result, True

        self._prune_news_seen_index()
        new_items = []
        total_items = 0
        invalid_items = 0
        for item in items:
            if not isinstance(item, dict):
                invalid_items += 1
                continue
            normalized_item = _normalize_news_item(item)
            if not normalized_item:
                invalid_items += 1
                continue
            total_items += 1
            item_key = _news_item_key(normalized_item)
            if not item_key:
                invalid_items += 1
                continue
            if not self._reserve_news_item(item_key):
                continue
            new_items.append(normalized_item)

        deduped_data = dict(data)
        deduped_data["items"] = new_items
        deduped_data["deduped"] = {
            "total_items": total_items,
            "new_items": len(new_items),
            "dropped_items": max(total_items - len(new_items), 0),
            "invalid_items": invalid_items,
        }
        deduped_result = dict(result)
        deduped_result["data"] = deduped_data
        if invalid_items and not new_items:
            deduped_result["ok"] = False
            deduped_result["summary"] = "新闻条目缺少可识别字段"
            deduped_result["error"] = "malformed_news_items"
            return deduped_result, True
        deduped_result["summary"] = _summarize_news_items(new_items)
        if not new_items:
            return deduped_result, False
        return deduped_result, True

    def _reserve_news_item(self, item_key: str) -> bool:
        now_ts = datetime.now(timezone.utc).timestamp()
        expires_at = now_ts + self._news_seen_ttl_hours * 3600
        with self._lock:
            self._prune_news_seen_shadow_locked(now_ts)
            shadow_expires_at = self._news_seen_shadow.get(item_key)
            if shadow_expires_at and shadow_expires_at > now_ts:
                return False
            if self._redis:
                try:
                    self._redis.zremrangebyscore(NEWS_SEEN_REDIS_KEY, "-inf", now_ts)
                    added = self._redis.zadd(
                        NEWS_SEEN_REDIS_KEY,
                        {item_key: expires_at},
                        nx=True,
                    )
                    if not added:
                        score = self._redis.zscore(NEWS_SEEN_REDIS_KEY, item_key)
                        if score is not None and float(score) > now_ts:
                            self._news_seen_shadow[item_key] = float(score)
                            self._trim_news_seen_shadow_locked()
                            self._prune_news_seen_redis(now_ts)
                            return False
                    self._news_seen_shadow[item_key] = expires_at
                    self._trim_news_seen_shadow_locked()
                    self._prune_news_seen_redis(now_ts)
                    return True
                except ACTION_REDIS_EXCEPTIONS:
                    self._redis = None
            self._news_seen_shadow[item_key] = expires_at
            self._trim_news_seen_shadow_locked()
            return True

    def _prune_news_seen_index(self) -> None:
        now_ts = datetime.now(timezone.utc).timestamp()
        with self._lock:
            self._prune_news_seen_shadow_locked(now_ts)
            self._trim_news_seen_shadow_locked()
        if self._redis:
            try:
                self._prune_news_seen_redis(now_ts)
            except ACTION_REDIS_EXCEPTIONS:
                self._redis = None

    def _prune_news_seen_shadow_locked(self, now_ts: float) -> None:
        expired_keys = [
            item_key
            for item_key, expires_at in self._news_seen_shadow.items()
            if expires_at <= now_ts
        ]
        for item_key in expired_keys:
            self._news_seen_shadow.pop(item_key, None)

    def _trim_news_seen_shadow_locked(self) -> None:
        if len(self._news_seen_shadow) <= self._news_seen_max_items:
            return
        ranked = sorted(
            self._news_seen_shadow.items(),
            key=lambda pair: (pair[1], pair[0]),
        )
        extra = len(self._news_seen_shadow) - self._news_seen_max_items
        for item_key, _ in ranked[:extra]:
            self._news_seen_shadow.pop(item_key, None)

    def _prune_news_seen_redis(self, now_ts: float) -> None:
        self._redis.zremrangebyscore(NEWS_SEEN_REDIS_KEY, "-inf", now_ts)
        total = int(self._redis.zcard(NEWS_SEEN_REDIS_KEY) or 0)
        extra = total - self._news_seen_max_items
        if extra > 0:
            self._redis.zremrangebyrank(NEWS_SEEN_REDIS_KEY, 0, extra - 1)

    def _get_action(self, action_id: str) -> ActionRecord:
        with self._lock:
            return self._actions[action_id]

    def _update_action(self, action_id: str, **changes) -> ActionRecord:
        with self._lock:
            action = self._actions[action_id]
            for key, value in changes.items():
                setattr(action, key, value)
        self._persist_action(action)
        return action

    def _upsert_action(self, action: ActionRecord) -> None:
        with self._lock:
            self._actions[action.action_id] = action
        self._persist_action(action)

    def _persist_action(self, action: ActionRecord) -> None:
        if not self._redis:
            return
        try:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            self._redis.hset(ACTION_REDIS_KEY, action.action_id, payload)
        except ACTION_REDIS_EXCEPTIONS:
            self._redis = None

    def _sync_to_redis(self) -> None:
        with self._lock:
            actions = list(self._actions.values())
            seen_items = dict(self._news_seen_shadow)
        for action in actions:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            self._redis.hset(ACTION_REDIS_KEY, action.action_id, payload)
        self._sync_news_seen_to_redis(seen_items)

    def _sync_news_seen_to_redis(self, seen_items: dict[str, float]) -> None:
        if not seen_items:
            return
        now_ts = datetime.now(timezone.utc).timestamp()
        valid_items = {
            item_key: expires_at
            for item_key, expires_at in seen_items.items()
            if expires_at > now_ts
        }
        if not valid_items:
            return
        self._redis.zadd(NEWS_SEEN_REDIS_KEY, valid_items)
        self._prune_news_seen_redis(now_ts)


class OllamaActionPlanner:
    """Second-pass planner using Ollama chat+tools."""

    def __init__(
        self,
        client,
        model_config: dict,
        default_timeout_seconds: int,
        default_weather_location: str,
        news_feed_urls: list[str],
    ):
        self._client = client
        self._model_name = model_config["name"]
        self._default_timeout_seconds = default_timeout_seconds
        self._default_weather_location = default_weather_location.strip()
        self._news_feed_urls = [item.strip() for item in news_feed_urls if item.strip()]
        self._options = {
            "num_ctx": model_config.get("num_ctx", 32768),
            "temperature": 0.1,
        }

    def plan(self, thought: Thought) -> ActionPlan | None:
        action_request = thought.action_request or {}
        response = self._client.chat(
            model=self._model_name,
            messages=_planner_messages(thought),
            tools=_planner_tools(),
            think=False,
            options=self._options,
        )
        tool_calls = response["message"].get("tool_calls") or []
        if tool_calls:
            return _plan_from_tool_call(
                tool_calls[0]["function"]["name"],
                tool_calls[0]["function"]["arguments"],
                thought,
                self._default_timeout_seconds,
                self._default_weather_location,
                self._news_feed_urls,
            )
        return _fallback_plan(
            raw_action_type=str(action_request.get("type") or "custom"),
            thought=thought,
            default_timeout_seconds=self._default_timeout_seconds,
            default_weather_location=self._default_weather_location,
            news_feed_urls=self._news_feed_urls,
        )


def create_action_manager(
    redis_client,
    stimulus_queue: StimulusQueue,
    ollama_client,
    model_config: dict,
    action_config: dict,
    *,
    news_feed_urls: list[str] | None = None,
    news_seen_ttl_hours: int = 720,
    news_seen_max_items: int = 5000,
    log_callback=None,
    event_callback=None,
) -> ActionManager:
    planner = OllamaActionPlanner(
        ollama_client,
        model_config,
        int(action_config.get("default_timeout_seconds", 300)),
        str(action_config.get("default_weather_location", "")),
        list(news_feed_urls or []),
    )
    from core.openclaw_gateway import OpenClawGatewayExecutor

    openclaw_executor = OpenClawGatewayExecutor(
        gateway_url=_read_env("OPENCLAW_GATEWAY_URL"),
        gateway_token=_read_env("OPENCLAW_GATEWAY_TOKEN"),
        worker_agent_id=str(action_config.get("worker_agent_id", "seedwake-worker")),
        session_key_prefix=str(action_config.get("session_key_prefix", "seedwake:action")),
        http_base_url=_read_env("OPENCLAW_HTTP_BASE_URL"),
        use_http_fallback=bool(action_config.get("use_openclaw_http_fallback", False)),
    )
    return ActionManager(
        redis_client,
        stimulus_queue,
        planner,
        openclaw_executor,
        auto_execute=list(action_config.get("auto_execute", [])),
        require_confirmation=list(action_config.get("require_confirmation", [])),
        forbidden=list(action_config.get("forbidden", [])),
        news_seen_ttl_hours=news_seen_ttl_hours,
        news_seen_max_items=news_seen_max_items,
        log_callback=log_callback,
        event_callback=event_callback,
    )


def _planner_messages(thought: Thought) -> list[dict[str, str]]:
    action_request = thought.action_request or {}
    user_prompt = "\n".join([
        f"thought_id: {thought.thought_id}",
        f"thought_type: {thought.type}",
        f"thought_content: {thought.content}",
        f"raw_action_type: {action_request.get('type', '')}",
        f"raw_action_params: {action_request.get('params', '')}",
    ])
    return [
        {
            "role": "system",
            "content": (
                "你是 Seedwake 的前额叶行动规划器。"
                "不要执行动作，只能通过一个 tool call 返回结构化决定。"
                "纯本地、无副作用、一次函数调用即可完成的时间读取和系统状态读取可选 native。"
                "新闻、天气、阅读、网页搜索、网页抓取、系统变更、浏览器/命令行/文件修改或多步探索一律委托 OpenClaw。"
                "news 只读取配置里的固定 RSS feed 列表，不需要 topic。"
                "reading 的阅读方向由 Seedwake 自己决定；如果原始 action 带了 query/topic/keywords，就保留它。"
                "如果 reading 没带参数，也应围绕原始念头内容组织任务，不要把阅读主题交给 OpenClaw 自己决定。"
                "weather 不写 location 时使用配置中的默认位置；只有想查特定地点时才带 location。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def _planner_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "delegate_openclaw",
                "description": "Delegate the task to the dedicated OpenClaw worker.",
                "parameters": {
                    "type": "object",
                    "required": ["action_type", "task"],
                    "properties": {
                        "action_type": {"type": "string"},
                        "task": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "native_get_time",
                "description": "Read the current local and UTC time without side effects.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "native_system_status",
                "description": "Read local CPU, memory, and disk status without side effects.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ignore_action",
                "description": "Do not execute any action for this thought.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    ]


def _plan_from_tool_call(
    tool_name: str,
    arguments: dict,
    thought: Thought,
    default_timeout_seconds: int,
    default_weather_location: str,
    news_feed_urls: list[str],
) -> ActionPlan | None:
    if tool_name == "ignore_action":
        return None

    timeout_seconds = _clamp_timeout(arguments.get("timeout_seconds"), default_timeout_seconds)
    reason = str(arguments.get("reason") or thought.content)
    if tool_name == "native_get_time":
        return ActionPlan(
            action_type="get_time",
            executor="native",
            task="读取当前时间",
            timeout_seconds=timeout_seconds,
            reason=reason,
        )
    if tool_name == "native_system_status":
        return ActionPlan(
            action_type="get_system_status",
            executor="native",
            task="读取当前系统状态",
            timeout_seconds=timeout_seconds,
            reason=reason,
        )
    if tool_name == "delegate_openclaw":
        action_type = str(
            arguments.get("action_type")
            or (thought.action_request or {}).get("type")
            or "custom"
        )
        explicit_task = str(arguments.get("task") or "").strip()
        task = _build_openclaw_task(
            action_type=action_type,
            explicit_task=explicit_task,
            thought=thought,
            default_weather_location=default_weather_location,
            news_feed_urls=news_feed_urls,
        )
        return ActionPlan(
            action_type=action_type,
            executor="openclaw",
            task=task,
            timeout_seconds=timeout_seconds,
            reason=reason,
        )
    return None


def _fallback_plan(
    *,
    raw_action_type: str,
    thought: Thought,
    default_timeout_seconds: int,
    default_weather_location: str,
    news_feed_urls: list[str],
) -> ActionPlan | None:
    action_type = raw_action_type or "custom"
    if action_type == "time":
        return ActionPlan(
            action_type="get_time",
            executor="native",
            task="读取当前时间",
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
        )
    if action_type == "system_status":
        return ActionPlan(
            action_type="get_system_status",
            executor="native",
            task="读取当前系统状态",
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
        )
    if action_type in OPENCLAW_ACTION_TYPES or action_type:
        return ActionPlan(
            action_type=action_type,
            executor="openclaw",
            task=_build_openclaw_task(
                action_type=action_type,
                explicit_task="",
                thought=thought,
                default_weather_location=default_weather_location,
                news_feed_urls=news_feed_urls,
            ),
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
        )
    return None


def _run_native_action(action: ActionRecord) -> ActionResultEnvelope:
    if action.type == "get_time":
        now = datetime.now().astimezone()
        return {
            "ok": True,
            "summary": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "data": {
                "local_iso": now.isoformat(),
                "utc_iso": datetime.now(timezone.utc).isoformat(),
            },
            "error": None,
            "run_id": None,
            "session_key": None,
            "transport": "native",
        }
    if action.type == "get_system_status":
        snapshot = collect_system_status_snapshot()
        return {
            "ok": True,
            "summary": str(snapshot.get("summary") or "系统状态已更新"),
            "data": snapshot,
            "error": None,
            "run_id": None,
            "session_key": None,
            "transport": "native",
        }
    raise RuntimeError(f"不支持的 native action: {action.type}")


def _clamp_timeout(raw_value, default_timeout_seconds: int) -> int:
    if isinstance(raw_value, int):
        return max(1, raw_value)
    return max(1, default_timeout_seconds)


def _build_openclaw_task(
    *,
    action_type: str,
    explicit_task: str,
    thought: Thought,
    default_weather_location: str,
    news_feed_urls: list[str],
) -> str:
    raw_params = str((thought.action_request or {}).get("params") or "")
    search_query = (
        _extract_action_param(raw_params, "query")
        or _extract_action_param(raw_params, "keywords")
        or _extract_action_param(raw_params, "topic")
    )
    reading_query = (
        _extract_action_param(raw_params, "query")
        or _extract_action_param(raw_params, "topic")
        or _extract_action_param(raw_params, "keywords")
    )
    if action_type == "search":
        if search_query:
            return f"围绕“{search_query}”进行搜索，返回按相关性整理的简洁结果。"
        if explicit_task:
            return explicit_task
        return thought.content
    if action_type == "news":
        if news_feed_urls:
            joined = "\n".join(f"- {url}" for url in news_feed_urls)
            return (
                "读取以下固定 RSS feed 列表，按时间顺序提取最新几条内容。"
                "返回 JSON，其中 data.items 是列表；每项尽量包含 feed_url、guid、link、title、published_at、summary，"
                "并保证同一条 RSS 项目在同次结果里只出现一次：\n"
                f"{joined}"
            )
        return "固定 RSS feed 列表未配置，请明确说明当前无法获取新闻。"
    if action_type == "reading":
        if reading_query:
            return f"围绕“{reading_query}”寻找一小段值得阅读的外部材料，返回原文片段和简短说明。"
        if explicit_task:
            return explicit_task
        return f"围绕这条念头当前真正想读的方向寻找一小段外部材料：{thought.content}"
    if action_type == "weather":
        location = _extract_action_param(raw_params, "location") or default_weather_location
        if location:
            return f"查询 {location} 的当前天气，返回简洁概况。"
        return "查询默认位置的当前天气；如果缺少默认位置，请明确说明无法确定位置。"
    if explicit_task:
        return explicit_task
    return thought.content


def _extract_action_param(raw_params: str, key: str) -> str | None:
    pattern = re.compile(rf"{re.escape(key)}\s*:\s*\"([^\"]+)\"")
    match = pattern.search(raw_params)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def _read_env(name: str) -> str:
    import os

    return os.environ.get(name, "")


def _action_to_dict(action: ActionRecord) -> dict:
    payload = asdict(action)
    payload["submitted_at"] = action.submitted_at.isoformat()
    return payload


def _normalize_action_result(result: ActionResultEnvelope, action: ActionRecord) -> ActionResultEnvelope:
    summary = str(result.get("summary") or "行动完成")
    data = result.get("data")
    return {
        "ok": bool(result.get("ok", True)),
        "summary": summary,
        "data": data if isinstance(data, dict) else {},
        "error": result.get("error"),
        "run_id": result.get("run_id") if isinstance(result.get("run_id"), str) else action.run_id,
        "session_key": (
            result.get("session_key")
            if isinstance(result.get("session_key"), str)
            else action.session_key
        ),
        "transport": str(result.get("transport") or action.executor),
        "raw_text": result.get("raw_text"),
    }


def _is_structured_news_result(result: ActionResultEnvelope) -> bool:
    data = result.get("data")
    return isinstance(data, dict) and isinstance(data.get("items"), list)


def _normalize_news_item(item: JsonObject) -> NewsItem:
    normalized = dict(item)
    for key in ("feed_url", "guid", "link", "title", "published_at", "summary"):
        value = normalized.get(key)
        normalized[key] = str(value).strip() if value is not None else ""
    return normalized


def _news_item_key(item: NewsItem) -> str | None:
    feed_url = str(item.get("feed_url") or "").strip()
    guid = str(item.get("guid") or "").strip()
    link = str(item.get("link") or "").strip()
    title = str(item.get("title") or "").strip()
    published_at = str(item.get("published_at") or "").strip()
    if feed_url and guid:
        return f"{feed_url}::{guid}"
    if feed_url and link:
        return f"{feed_url}::{link}"
    if link:
        return f"link::{link}"
    fingerprint_source = "\n".join([feed_url, title, published_at, str(item.get("summary") or "").strip()])
    if not fingerprint_source.strip():
        return None
    digest = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return f"hash::{digest}"


def _summarize_news_items(items: list[NewsItem]) -> str:
    if not items:
        return "RSS 没有新的条目"
    labels = []
    for item in items[:3]:
        title = str(item.get("title") or "").strip()
        feed_url = str(item.get("feed_url") or "").strip()
        if title and feed_url:
            labels.append(f"{title} ({feed_url})")
            continue
        if title:
            labels.append(title)
            continue
        summary = str(item.get("summary") or "").strip()
        if summary:
            labels.append(summary)
    if not labels:
        return f"RSS 新条目 {len(items)} 条"
    return f"RSS 新条目 {len(items)} 条：{'；'.join(labels)}"


def _build_result_stimulus(
    action: ActionRecord,
    status: str,
    result: ActionResultEnvelope,
) -> JsonObject:
    stimulus_type = _infer_stimulus_type(action, status, result)
    return {
        "type": stimulus_type,
        "priority": _stimulus_priority(stimulus_type, result),
        "source": f"action:{action.action_id}",
        "content": _stimulus_content(stimulus_type, action, status, result),
        "metadata": {
            "status": status,
            "executor": action.executor,
            "result": result,
        },
    }


def _infer_stimulus_type(
    action: ActionRecord,
    status: str,
    result: ActionResultEnvelope,
) -> str:
    if status != "succeeded" or not bool(result.get("ok", True)):
        return "action_result"
    if action.type == "get_time":
        return "time"
    if action.type == "get_system_status":
        return "system_status"
    if action.type in {"news", "weather", "reading"}:
        return action.type
    return "action_result"


def _stimulus_priority(stimulus_type: str, result: ActionResultEnvelope) -> int:
    if stimulus_type == "action_result":
        return 2
    if stimulus_type == "system_status":
        warnings = result.get("data", {}).get("warnings")
        return 3 if warnings else 4
    return 4


def _stimulus_content(
    stimulus_type: str,
    action: ActionRecord,
    status: str,
    result: ActionResultEnvelope,
) -> str:
    summary = str(result.get("summary") or "行动完成")
    if stimulus_type == "action_result":
        return f"{action.type} {status}: {summary}"
    return summary


def push_action_control(
    redis_client,
    action_id: str,
    *,
    approved: bool,
    actor: str,
    note: str = "",
) -> bool:
    if redis_client is None:
        return False
    payload = json.dumps({
        "action_id": action_id,
        "approved": approved,
        "actor": actor,
        "note": note,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)
    try:
        redis_client.rpush(ACTION_CONTROL_KEY, payload)
    except ACTION_REDIS_EXCEPTIONS:
        return False
    return True


def pop_action_controls(redis_client, limit: int = 20) -> list[ActionControl]:
    if redis_client is None or limit <= 0:
        return []
    controls = []
    for _ in range(limit):
        try:
            raw = redis_client.lpop(ACTION_CONTROL_KEY)
        except ACTION_REDIS_EXCEPTIONS:
            return controls
        if raw is None:
            break
        controls.append(json.loads(raw))
    return controls
