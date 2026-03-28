"""Phase 3 action planning and execution."""

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib import error, request

from ollama import RequestError as OllamaRequestError, ResponseError as OllamaResponseError
from redis import exceptions as redis_exceptions

from core.openclaw_gateway import OpenClawUnavailableError
from core.perception import collect_system_status_snapshot
from core.rss import RSS_READ_EXCEPTIONS, read_news_result, summarize_news_items
from core.stimulus import Stimulus, StimulusQueue, append_conversation_history
from core.thought_parser import Thought
from core.types import (
    ActionControl,
    ActionRequestPayload,
    ActionResultEnvelope,
    JsonObject,
    NewsItem,
    RawActionRequest,
)

ACTION_REDIS_KEY = "seedwake:actions"
ACTION_CONTROL_KEY = "seedwake:action_control"
NEWS_SEEN_REDIS_KEY = "seedwake:news_seen"
TELEGRAM_SOURCE_PREFIX = "telegram:"
OPENCLAW_ACTION_TYPES = {"search", "web_fetch", "system_change", "custom", "weather", "reading", "file_modify"}
PERCEPTION_AUTO_EXECUTE_TYPES = {"news", "weather", "reading"}
OPS_ACTION_TYPES = {"system_change", "file_modify"}
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
    *RSS_READ_EXCEPTIONS,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)
TELEGRAM_SEND_EXCEPTIONS = (
    OSError,
    TimeoutError,
    ValueError,
    TypeError,
    json.JSONDecodeError,
)
logger = logging.getLogger(__name__)
ACTION_MARKER_PATTERN = re.compile(r"\s*\{action:[^}]+}\s*$")


@dataclass
class ActionPlan:
    action_type: str
    executor: str
    task: str
    timeout_seconds: int
    reason: str
    news_feed_urls: list[str] = field(default_factory=list)
    worker_agent_id: str = ""
    target_source: str = ""
    target_entity: str = ""
    message_text: str = ""


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
    retry_after: datetime | None = None
    dispatch_started_at: datetime | None = None


@dataclass
class ActionCallbacks:
    log: Callable[[str], None] | None = None
    event: Callable[[str, JsonObject], None] | None = None


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
        news_reader=read_news_result,
        contact_resolver=None,
        openclaw_retry_delay_seconds: float = 5.0,
        callbacks: ActionCallbacks | None = None,
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
        self._news_reader = news_reader
        self._contact_resolver = contact_resolver
        self._openclaw_retry_delay_seconds = max(0.0, openclaw_retry_delay_seconds)
        callbacks = callbacks or ActionCallbacks()
        self._log_callback = callbacks.log
        self._event_callback = callbacks.event
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="seedwake-action")
        self._lock = Lock()
        self._actions: dict[str, ActionRecord] = {}
        self._news_seen_shadow: dict[str, float] = {}
        self._perception_observations: list[str] = []
        self._futures: set[Future] = set()
        if self._redis is not None:
            try:
                self._restore_from_redis()
            except ACTION_REDIS_EXCEPTIONS:
                self._redis = None

    def submit_from_thoughts(
        self,
        thoughts: list[Thought],
        *,
        stimuli: list[Stimulus] | None = None,
    ) -> list[ActionRecord]:
        created: list[ActionRecord] = []
        conversation_source = _latest_conversation_source(stimuli or [])
        for thought in thoughts:
            action = self._plan_submitted_action(
                thought,
                conversation_source=conversation_source,
            )
            if action is None:
                continue
            self._upsert_action(action)
            created.append(action)
            self._dispatch_submitted_action(action)

        return created

    def _plan_submitted_action(
        self,
        thought: Thought,
        *,
        conversation_source: str | None,
    ) -> ActionRecord | None:
        if not thought.action_request:
            return None
        try:
            plan = self._planner.plan(thought, conversation_source=conversation_source)
        except PLANNER_EXCEPTIONS as exc:
            self._emit(f"行动规划失败 {thought.thought_id}: {exc}")
            return None
        if not plan:
            return None
        return _action_from_plan(
            thought=thought,
            plan=plan,
            conversation_source=conversation_source,
        )

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
                result=_failure_result(summary, "rejected", transport=action.executor),
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
            self._restore_from_redis()
            self._sync_to_redis()
        except ACTION_REDIS_EXCEPTIONS:
            self._redis = None
        return self.redis_available

    @property
    def redis_available(self) -> bool:
        return self._redis is not None

    def shutdown(self) -> None:
        self.shutdown_with_timeout()

    def shutdown_with_timeout(self, wait_timeout_seconds: float | None = None) -> bool:
        self._pool.shutdown(wait=False, cancel_futures=False)
        futures = self._snapshot_futures()
        if not futures:
            return True
        done, not_done = wait(futures, timeout=wait_timeout_seconds)
        if not_done:
            self._pool.shutdown(wait=False, cancel_futures=True)
            return False
        return len(done) == len(futures)

    def retry_deferred_actions(self) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            retry_ids = [
                action.action_id
                for action in self._actions.values()
                if (
                    action.status == "pending"
                    and not action.awaiting_confirmation
                    and (
                        action.executor == "native"
                        or action.retry_after is None
                        or action.retry_after <= now
                    )
                )
            ]
        for action_id in retry_ids:
            self._start_action(action_id)

    def _start_action(self, action_id: str) -> None:
        action = self._get_action(action_id)
        self._emit(f"行动提交 {action.action_id} [{action.type}/{action.executor}]")
        self._publish_action_event(action, "pending", "已提交")
        self._update_action(action_id, status="running", retry_after=None)
        future = self._pool.submit(self._run_action, action_id)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def _run_action(self, action_id: str) -> None:
        action = self._get_action(action_id)
        try:
            self._publish_action_event(action, "running", "执行中")

            if action.executor == "native":
                result = self._run_native_action(action_id)
            else:
                result = self._openclaw_executor.execute(action)
        except OpenClawUnavailableError as exc:
            self._defer_openclaw_action(action_id, str(exc))
            return
        except TimeoutError:
            self._safe_finalize_action(
                action_id,
                status="timeout",
                result=_failure_result("行动超时", "timeout", transport=action.executor),
            )
            return
        except ACTION_EXECUTION_EXCEPTIONS as exc:
            self._safe_finalize_action(
                action_id,
                status="failed",
                result=_failure_result(f"行动失败：{exc}", str(exc), transport=action.executor),
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

    def _run_native_action(self, action_id: str) -> ActionResultEnvelope:
        action = self._get_action(action_id)
        if action.type != "send_message":
            return _run_native_action(
                action,
                news_reader=self._news_reader,
                contact_resolver=self._contact_resolver,
            )

        target_source, target_entity, message_text, failure = _prepare_send_message(
            action,
            contact_resolver=self._contact_resolver,
        )
        if failure is not None:
            return failure
        if not self._mark_dispatch_started(action_id):
            return _failure_result(
                "消息发送前无法持久化状态",
                "delivery_state_unavailable",
                transport="native",
            )
        send_error = _send_telegram_message(
            target_source,
            message_text,
            timeout_seconds=action.timeout_seconds,
        )
        if send_error:
            self._update_action(action_id, dispatch_started_at=None)
            return _failure_result(f"Telegram 发送失败：{send_error}", send_error, transport="native")
        return _build_action_result(
            ok=True,
            summary=f"已发送消息到 {target_source}",
            data={
                "source": target_source,
                "target_entity": target_entity,
                "message": message_text,
            },
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
        )

    def _finalize_action(self, action_id: str, *, status: str, result: ActionResultEnvelope) -> None:
        action = self._get_action(action_id)
        result = _normalize_action_result(result, action)
        status, result, should_emit_stimulus = self._prepare_result_for_stimulus(action, status, result)
        action = self._update_action(
            action_id,
            status=status,
            result=result,
            dispatch_started_at=None,
        )
        if isinstance(result.get("run_id"), str):
            action.run_id = str(result["run_id"])
        if isinstance(result.get("session_key"), str):
            action.session_key = str(result["session_key"])
        self._upsert_action(action)

        summary = str(result.get("summary") or "行动完成")
        self._emit(f"行动结束 {action.action_id} [{status}] {summary}")
        self._publish_action_event(action, status, summary)
        self._publish_native_message(action, status, result)
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

    def _dispatch_submitted_action(self, action: ActionRecord) -> None:
        policy = self._classify_policy(action)
        if policy == "auto":
            self._start_action(action.action_id)
            return
        if policy == "confirmation":
            action = self._update_action(action.action_id, awaiting_confirmation=True)
            self._emit(f"行动等待确认 {action.action_id}")
            self._publish_action_event(action, "pending", "等待确认")
            return
        if policy == "forbidden":
            self._emit(f"行动被禁止 {action.action_id}")
            self._finalize_action(
                action.action_id,
                status="failed",
                result=_failure_result("行动被禁止", "forbidden", transport=action.executor),
            )
            return
        self._emit(f"行动未获自动执行许可 {action.action_id}")
        self._finalize_action(
            action.action_id,
            status="failed",
            result=_failure_result("行动需要人工批准", "not_auto_execute", transport=action.executor),
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
        action = self._get_action(action_id)
        fallback_result = _failure_result(summary, summary, transport=action.executor)
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

    def _defer_openclaw_action(self, action_id: str, reason: str) -> None:
        retry_after = datetime.now(timezone.utc) + timedelta(seconds=self._openclaw_retry_delay_seconds)
        action = self._update_action(
            action_id,
            status="pending",
            result=None,
            run_id=None,
            session_key=None,
            retry_after=retry_after,
        )
        self._emit(f"OpenClaw 不可用，行动排队等待恢复 {action.action_id}: {reason}")
        self._publish_action_event(action, "pending", "等待 OpenClaw 恢复")

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

    def _publish_native_message(
        self,
        action: ActionRecord,
        status: str,
        result: ActionResultEnvelope,
    ) -> None:
        if action.type != "send_message" or status != "succeeded" or not bool(result.get("ok", True)):
            return
        data = result.get("data")
        if not isinstance(data, dict):
            return
        source = str(data.get("source") or "").strip()
        message = str(data.get("message") or "").strip()
        if not source or not message:
            return
        try:
            append_conversation_history(
                self._redis,
                role="assistant",
                source=source,
                content=message,
                metadata={"action_id": action.action_id},
            )
        except ACTION_REDIS_EXCEPTIONS as exc:
            logger.warning("failed to persist native message history for %s: %s", action.action_id, exc)
            self._redis = None
        except Exception as exc:
            logger.exception("unexpected native message history failure for %s: %s", action.action_id, exc)
        if not self._event_callback:
            return
        try:
            self._event_callback("reply", {
                "source": source,
                "message": message,
                "stimulus_id": None,
            })
        except Exception as exc:
            logger.exception("unexpected native message event failure for %s: %s", action.action_id, exc)

    def _snapshot_futures(self) -> list[Future]:
        with self._lock:
            return list(self._futures)

    def _discard_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

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
            malformed = _copy_action_result(
                result,
                ok=False,
                summary="新闻结果缺少结构化 RSS 条目",
                error_detail="malformed_news_result",
            )
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
        deduped_result = _copy_action_result(result, data=deduped_data)
        if invalid_items and not new_items:
            deduped_result["ok"] = False
            deduped_result["summary"] = "新闻条目缺少可识别字段"
            deduped_result["error"] = "malformed_news_items"
            return deduped_result, True
        deduped_result["summary"] = summarize_news_items(new_items)
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

    def _mark_dispatch_started(self, action_id: str) -> bool:
        dispatch_started_at = datetime.now(timezone.utc)
        with self._lock:
            action = self._actions[action_id]
            action.dispatch_started_at = dispatch_started_at
            redis_client = self._redis
        if redis_client is None:
            with self._lock:
                self._actions[action_id].dispatch_started_at = None
            return False
        try:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            redis_client.hset(ACTION_REDIS_KEY, action.action_id, payload)
        except ACTION_REDIS_EXCEPTIONS:
            with self._lock:
                self._actions[action_id].dispatch_started_at = None
                self._redis = None
            return False
        return True

    def _sync_to_redis(self) -> None:
        with self._lock:
            actions = list(self._actions.values())
            seen_items = dict(self._news_seen_shadow)
        for action in actions:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            self._redis.hset(ACTION_REDIS_KEY, action.action_id, payload)
        self._sync_news_seen_to_redis(seen_items)

    def _restore_from_redis(self) -> None:
        now = datetime.now(timezone.utc)
        for item in load_action_items(self._redis):
            action = _action_from_json_object(item, now=now)
            if action is None:
                continue
            with self._lock:
                if action.action_id in self._actions:
                    continue
                self._actions[action.action_id] = action
            if str(item.get("status") or "") != action.status:
                self._persist_action(action)

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
        worker_agent_id: str,
        ops_worker_agent_id: str,
    ):
        self._client = client
        self._model_name = model_config["name"]
        self._default_timeout_seconds = default_timeout_seconds
        self._default_weather_location = default_weather_location.strip()
        self._news_feed_urls = [item.strip() for item in news_feed_urls if item.strip()]
        self._worker_agent_id = worker_agent_id.strip()
        self._ops_worker_agent_id = ops_worker_agent_id.strip()
        self._options = {
            "num_ctx": model_config.get("num_ctx", 32768),
            "temperature": 0.1,
        }

    def plan(self, thought: Thought, *, conversation_source: str | None = None) -> ActionPlan | None:
        action_request = thought.action_request or {}
        response = self._client.chat(
            model=self._model_name,
            messages=_planner_messages(thought, conversation_source=conversation_source),
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
                self._worker_agent_id,
                self._ops_worker_agent_id,
                conversation_source,
            )
        return _fallback_plan(
            raw_action_type=str(action_request.get("type") or "custom"),
            thought=thought,
            default_timeout_seconds=self._default_timeout_seconds,
            default_weather_location=self._default_weather_location,
            news_feed_urls=self._news_feed_urls,
            worker_agent_id=self._worker_agent_id,
            ops_worker_agent_id=self._ops_worker_agent_id,
            conversation_source=conversation_source,
        )


def create_action_manager(
    redis_client,
    stimulus_queue: StimulusQueue,
    ollama_client,
    model_config: dict,
    action_config: dict,
    *,
    contact_resolver=None,
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
        str(action_config.get("worker_agent_id", "seedwake-worker")),
        str(action_config.get("ops_worker_agent_id", "seedwake-ops")),
    )
    from core.openclaw_gateway import OpenClawGatewayExecutor

    openclaw_executor = OpenClawGatewayExecutor(
        gateway_url=_read_env("OPENCLAW_GATEWAY_URL"),
        gateway_token=_read_env("OPENCLAW_GATEWAY_TOKEN"),
        worker_agent_id=str(action_config.get("worker_agent_id", "seedwake-worker")),
        ops_worker_agent_id=str(action_config.get("ops_worker_agent_id", "seedwake-ops")),
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
        news_reader=read_news_result,
        contact_resolver=contact_resolver,
        openclaw_retry_delay_seconds=float(action_config.get("openclaw_retry_delay_seconds", 5.0)),
        callbacks=ActionCallbacks(log=log_callback, event=event_callback),
    )


def _planner_messages(thought: Thought, *, conversation_source: str | None = None) -> list[dict[str, str]]:
    action_request = thought.action_request or {}
    user_prompt = "\n".join([
        f"thought_id: {thought.thought_id}",
        f"thought_type: {thought.type}",
        f"thought_content: {thought.content}",
        f"raw_action_type: {action_request.get('type', '')}",
        f"raw_action_params: {action_request.get('params', '')}",
        f"conversation_source: {conversation_source or ''}",
    ])
    return [
        {
            "role": "system",
            "content": (
                "你是 Seedwake 的前额叶行动规划器。"
                "不要执行动作，只能通过一个 tool call 返回结构化决定。"
                "纯本地、无副作用、一次函数调用即可完成的时间读取、系统状态读取、固定 RSS 新闻读取，以及 Telegram 消息发送可选 native。"
                "天气、阅读、网页搜索、网页抓取、浏览器和多步探索委托普通 OpenClaw worker。"
                "系统变更和文件修改委托 OpenClaw ops worker。"
                "news 只读取配置里的固定 RSS feed 列表，不需要 topic，也不委托 OpenClaw。"
                "reading 的阅读方向由 Seedwake 自己决定；如果原始 action 带了 query/topic/keywords，就保留它。"
                "如果 reading 没带参数，也应围绕原始念头内容组织任务，不要把阅读主题交给 OpenClaw 自己决定。"
                "weather 不写 location 时使用配置中的默认位置；只有想查特定地点时才带 location。"
                "send_message 只有在真的想发消息时才使用，不需要因为收到对话刺激而强制回复。"
                "send_message 优先发送到当前 conversation_source；只有明确给了 target/chat_id/source 时才覆盖。"
                "如果想联系某个已知实体，可以使用 target_entity，例如 person:alice。"
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
                "name": "native_read_news",
                "description": "Read the configured RSS feeds and return structured news items.",
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
                "name": "native_send_message",
                "description": (
                    "Send a Telegram message to the current conversation target, "
                    "explicit telegram target, or resolved entity contact."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "target": {"type": "string"},
                        "target_entity": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                        "reason": {"type": "string"},
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
    worker_agent_id: str = "seedwake-worker",
    ops_worker_agent_id: str = "seedwake-ops",
    conversation_source: str | None = None,
) -> ActionPlan | None:
    if tool_name == "ignore_action":
        return None

    timeout_seconds = _clamp_timeout(arguments.get("timeout_seconds"), default_timeout_seconds)
    reason = str(arguments.get("reason") or thought.content)
    native_plan = _plan_native_tool_call(
        tool_name=tool_name,
        arguments=arguments,
        thought=thought,
        timeout_seconds=timeout_seconds,
        reason=reason,
        news_feed_urls=news_feed_urls,
        conversation_source=conversation_source,
    )
    if native_plan is not None:
        return native_plan
    if tool_name != "delegate_openclaw":
        return None
    return _plan_delegate_tool_call(
        arguments=arguments,
        thought=thought,
        timeout_seconds=timeout_seconds,
        reason=reason,
        default_weather_location=default_weather_location,
        news_feed_urls=news_feed_urls,
        worker_agent_id=worker_agent_id,
        ops_worker_agent_id=ops_worker_agent_id,
        conversation_source=conversation_source,
    )


def _fallback_plan(
    *,
    raw_action_type: str,
    thought: Thought,
    default_timeout_seconds: int,
    default_weather_location: str,
    news_feed_urls: list[str],
    worker_agent_id: str = "seedwake-worker",
    ops_worker_agent_id: str = "seedwake-ops",
    conversation_source: str | None = None,
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
    if action_type == "news":
        return _native_news_plan(
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
            news_feed_urls=news_feed_urls,
        )
    if action_type == "system_status":
        return ActionPlan(
            action_type="get_system_status",
            executor="native",
            task="读取当前系统状态",
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
        )
    if action_type == "send_message":
        return _native_send_message_plan(
            raw_params=str((thought.action_request or {}).get("params") or ""),
            thought=thought,
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
            conversation_source=conversation_source,
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
            ),
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
            worker_agent_id=_resolve_worker_agent_id(action_type, worker_agent_id, ops_worker_agent_id),
        )
    return None


def _run_native_action(
    action: ActionRecord,
    *,
    news_reader=read_news_result,
    contact_resolver=None,
) -> ActionResultEnvelope:
    if action.type == "get_time":
        now = datetime.now().astimezone()
        return _build_action_result(
            ok=True,
            summary=now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            data={
                "local_iso": now.isoformat(),
                "utc_iso": datetime.now(timezone.utc).isoformat(),
            },
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
        )
    if action.type == "news":
        feed_urls = _coerce_news_feed_urls(action.request.get("news_feed_urls"))
        return news_reader(feed_urls, timeout_seconds=action.timeout_seconds)
    if action.type == "send_message":
        target_source, target_entity, message_text, failure = _prepare_send_message(
            action,
            contact_resolver=contact_resolver,
        )
        if failure is not None:
            return failure
        return _build_action_result(
            ok=True,
            summary=f"准备发送消息到 {target_source}",
            data={
                "source": target_source,
                "target_entity": target_entity,
                "message": message_text,
            },
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
        )
    if action.type == "get_system_status":
        snapshot = collect_system_status_snapshot()
        return _build_action_result(
            ok=True,
            summary=str(snapshot.get("summary") or "系统状态已更新"),
            data=dict(snapshot),
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
        )
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
) -> str:
    raw_params = str((thought.action_request or {}).get("params") or "")
    if action_type == "search":
        return _build_search_task(raw_params, explicit_task, thought)
    if action_type == "reading":
        return _build_reading_task(raw_params, explicit_task, thought)
    if action_type == "weather":
        return _build_weather_task(raw_params, default_weather_location)
    if action_type == "file_modify":
        return _build_file_modify_task(raw_params, explicit_task, thought)
    if action_type == "system_change":
        return _build_system_change_task(raw_params, explicit_task, thought)
    if explicit_task:
        return explicit_task
    return thought.content


def _build_search_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    search_query = _extract_action_first_param(raw_params, "query", "keywords", "topic")
    if search_query:
        return f"围绕“{search_query}”进行搜索，返回按相关性整理的简洁结果。"
    if explicit_task:
        return explicit_task
    return thought.content


def _build_reading_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    reading_query = _extract_action_first_param(raw_params, "query", "topic", "keywords")
    if reading_query:
        return f"围绕“{reading_query}”寻找一小段值得阅读的外部材料，返回原文片段和简短说明。"
    if explicit_task:
        return explicit_task
    return f"围绕这条念头当前真正想读的方向寻找一小段外部材料：{thought.content}"


def _build_weather_task(raw_params: str, default_weather_location: str) -> str:
    location = _extract_action_param(raw_params, "location") or default_weather_location
    if location:
        return f"查询 {location} 的当前天气，返回简洁概况。"
    return "查询默认位置的当前天气；如果缺少默认位置，请明确说明无法确定位置。"


def _build_file_modify_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    path = _extract_action_first_param(raw_params, "path", "file")
    instruction = _extract_action_first_param(raw_params, "instruction", "edit", "change")
    if path and instruction:
        return f"修改文件 {path}。修改要求：{instruction}。只做必要改动，并返回修改摘要。"
    if path:
        return f"修改文件 {path}。修改要求围绕这条念头展开：{thought.content}"
    if explicit_task:
        return explicit_task
    return thought.content


def _build_system_change_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    instruction = _extract_action_first_param(raw_params, "instruction", "task", "change")
    if instruction:
        return f"执行系统变更：{instruction}。返回变更摘要、影响范围和结果。"
    if explicit_task:
        return explicit_task
    return thought.content


def _extract_action_first_param(raw_params: str, *keys: str) -> str | None:
    for key in keys:
        value = _extract_action_param(raw_params, key)
        if value:
            return value
    return None


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


def _resolve_worker_agent_id(action_type: str, worker_agent_id: str, ops_worker_agent_id: str) -> str:
    if action_type in OPS_ACTION_TYPES:
        return ops_worker_agent_id
    return worker_agent_id


def _build_action_result(
    *,
    ok: bool,
    summary: str,
    data: JsonObject,
    error_detail,
    run_id: str | None,
    session_key: str | None,
    transport: str,
    raw_text: str | None = None,
) -> ActionResultEnvelope:
    result: ActionResultEnvelope = {
        "ok": ok,
        "summary": summary,
        "data": data,
        "error": error_detail,
        "run_id": run_id,
        "session_key": session_key,
        "transport": transport,
    }
    if raw_text is not None:
        result["raw_text"] = raw_text
    return result


def _failure_result(summary: str, error_detail, *, transport: str) -> ActionResultEnvelope:
    return _build_action_result(
        ok=False,
        summary=summary,
        data={},
        error_detail=error_detail,
        run_id=None,
        session_key=None,
        transport=transport,
    )


def _copy_action_result(
    result: ActionResultEnvelope,
    *,
    ok: bool | None = None,
    summary: str | None = None,
    data: JsonObject | None = None,
    error_detail=None,
) -> ActionResultEnvelope:
    copied_data = _result_data_or_default(result, data)
    copied = _build_action_result(
        ok=bool(result.get("ok", True)) if ok is None else ok,
        summary=str(result.get("summary") or "") if summary is None else summary,
        data=copied_data,
        error_detail=result.get("error") if error_detail is None else error_detail,
        run_id=result.get("run_id") if isinstance(result.get("run_id"), str) else None,
        session_key=result.get("session_key") if isinstance(result.get("session_key"), str) else None,
        transport=str(result.get("transport") or ""),
        raw_text=result.get("raw_text") if isinstance(result.get("raw_text"), str) else None,
    )
    if data is not None:
        copied["data"] = data
    return copied


def _result_data_or_default(result: ActionResultEnvelope, data: JsonObject | None) -> JsonObject:
    if data is not None:
        return data
    existing = result.get("data")
    if isinstance(existing, dict):
        return existing
    return {}


def _action_to_dict(action: ActionRecord) -> dict:
    payload = asdict(action)
    payload["submitted_at"] = action.submitted_at.isoformat()
    payload["retry_after"] = action.retry_after.isoformat() if action.retry_after else None
    payload["dispatch_started_at"] = (
        action.dispatch_started_at.isoformat()
        if action.dispatch_started_at
        else None
    )
    return payload


def _normalize_action_result(result: ActionResultEnvelope, action: ActionRecord) -> ActionResultEnvelope:
    summary = str(result.get("summary") or "行动完成")
    data = result.get("data")
    raw_text = result.get("raw_text")
    return _build_action_result(
        ok=bool(result.get("ok", True)),
        summary=summary,
        data=data if isinstance(data, dict) else {},
        error_detail=result.get("error"),
        run_id=result.get("run_id") if isinstance(result.get("run_id"), str) else action.run_id,
        session_key=(
            result.get("session_key")
            if isinstance(result.get("session_key"), str)
            else action.session_key
        ),
        transport=str(result.get("transport") or action.executor),
        raw_text=raw_text if isinstance(raw_text, str) else None,
    )


def _is_structured_news_result(result: ActionResultEnvelope) -> bool:
    data = result.get("data")
    return isinstance(data, dict) and isinstance(data.get("items"), list)


def _normalize_news_item(item: JsonObject) -> NewsItem:
    return {
        "feed_url": _stringify_json_field(item.get("feed_url")),
        "guid": _stringify_json_field(item.get("guid")),
        "link": _stringify_json_field(item.get("link")),
        "title": _stringify_json_field(item.get("title")),
        "published_at": _stringify_json_field(item.get("published_at")),
        "summary": _stringify_json_field(item.get("summary")),
    }


def _stringify_json_field(value) -> str:
    return str(value).strip() if value is not None else ""


def _build_action_request_payload(
    *,
    task: str,
    reason: str,
    raw_action,
    news_feed_urls: list[str],
    worker_agent_id: str = "",
    target_source: str = "",
    target_entity: str = "",
    message_text: str = "",
) -> ActionRequestPayload:
    payload: ActionRequestPayload = {
        "task": task,
        "reason": reason,
        "raw_action": raw_action,
    }
    if news_feed_urls:
        payload["news_feed_urls"] = list(news_feed_urls)
    if worker_agent_id:
        payload["worker_agent_id"] = worker_agent_id
    if target_source:
        payload["target_source"] = target_source
    if target_entity:
        payload["target_entity"] = target_entity
    if message_text:
        payload["message_text"] = message_text
    return payload


def _action_from_plan(
    *,
    thought: Thought,
    plan: ActionPlan,
    conversation_source: str | None,
) -> ActionRecord:
    request_payload = _build_action_request_payload(
        task=plan.task,
        reason=plan.reason,
        raw_action=_coerce_raw_action_request(thought.action_request or {}),
        news_feed_urls=plan.news_feed_urls,
        worker_agent_id=plan.worker_agent_id,
        target_source=plan.target_source or str(conversation_source or "").strip(),
        target_entity=plan.target_entity,
        message_text=plan.message_text,
    )
    return ActionRecord(
        action_id=f"act_{thought.thought_id}",
        type=plan.action_type,
        request=request_payload,
        executor=plan.executor,
        status="pending",
        source_thought_id=thought.thought_id,
        source_content=thought.content,
        timeout_seconds=plan.timeout_seconds,
    )


def _native_news_plan(
    *,
    timeout_seconds: int,
    reason: str,
    news_feed_urls: list[str],
) -> ActionPlan:
    return ActionPlan(
        action_type="news",
        executor="native",
        task="读取固定 RSS 信息流",
        timeout_seconds=timeout_seconds,
        reason=reason,
        news_feed_urls=list(news_feed_urls),
    )


def _latest_conversation_source(stimuli: list[Stimulus]) -> str | None:
    for stimulus in reversed(stimuli):
        if stimulus.type == "conversation":
            return stimulus.source
    return None


def _native_send_message_plan(
    *,
    raw_params: str,
    thought: Thought,
    timeout_seconds: int,
    reason: str,
    conversation_source: str | None,
    explicit_message: str = "",
    explicit_target: str = "",
    explicit_target_entity: str = "",
) -> ActionPlan:
    message_text = explicit_message or _build_send_message_text(raw_params, thought)
    target_source = explicit_target or _build_send_message_target(raw_params)
    target_entity = explicit_target_entity or _build_send_message_target_entity(raw_params)
    if not target_source and not target_entity:
        target_source = str(conversation_source or "").strip()
    target_label = target_source or target_entity or "当前 Telegram 对话"
    task = f"向 {target_label} 发送消息：{message_text or thought.content}"
    return ActionPlan(
        action_type="send_message",
        executor="native",
        task=task,
        timeout_seconds=timeout_seconds,
        reason=reason,
        target_source=target_source,
        target_entity=target_entity,
        message_text=message_text,
    )


def _plan_native_tool_call(
    *,
    tool_name: str,
    arguments: dict,
    thought: Thought,
    timeout_seconds: int,
    reason: str,
    news_feed_urls: list[str],
    conversation_source: str | None,
) -> ActionPlan | None:
    if tool_name == "native_get_time":
        return _native_time_plan(timeout_seconds=timeout_seconds, reason=reason)
    if tool_name == "native_system_status":
        return _native_system_status_plan(timeout_seconds=timeout_seconds, reason=reason)
    if tool_name == "native_read_news":
        return _native_news_plan(
            timeout_seconds=timeout_seconds,
            reason=reason,
            news_feed_urls=news_feed_urls,
        )
    if tool_name != "native_send_message":
        return None
    return _native_send_message_plan(
        raw_params=_raw_action_params(thought),
        thought=thought,
        timeout_seconds=timeout_seconds,
        reason=reason,
        conversation_source=conversation_source,
        explicit_message=str(arguments.get("message") or "").strip(),
        explicit_target=str(arguments.get("target") or "").strip(),
        explicit_target_entity=str(arguments.get("target_entity") or "").strip(),
    )


def _plan_delegate_tool_call(
    *,
    arguments: dict,
    thought: Thought,
    timeout_seconds: int,
    reason: str,
    default_weather_location: str,
    news_feed_urls: list[str],
    worker_agent_id: str,
    ops_worker_agent_id: str,
    conversation_source: str | None,
) -> ActionPlan:
    explicit_task = str(arguments.get("task") or "").strip()
    action_type = _delegated_action_type(arguments, thought)
    if action_type == "news":
        return _native_news_plan(
            timeout_seconds=timeout_seconds,
            reason=reason,
            news_feed_urls=news_feed_urls,
        )
    if action_type == "send_message":
        return _native_send_message_plan(
            raw_params=_raw_action_params(thought),
            thought=thought,
            timeout_seconds=timeout_seconds,
            reason=reason,
            conversation_source=conversation_source,
            explicit_message=explicit_task,
            explicit_target_entity=str(arguments.get("target_entity") or "").strip(),
        )
    return ActionPlan(
        action_type=action_type,
        executor="openclaw",
        task=_build_openclaw_task(
            action_type=action_type,
            explicit_task=explicit_task,
            thought=thought,
            default_weather_location=default_weather_location,
        ),
        timeout_seconds=timeout_seconds,
        reason=reason,
        worker_agent_id=_resolve_worker_agent_id(action_type, worker_agent_id, ops_worker_agent_id),
    )


def _native_time_plan(*, timeout_seconds: int, reason: str) -> ActionPlan:
    return ActionPlan(
        action_type="get_time",
        executor="native",
        task="读取当前时间",
        timeout_seconds=timeout_seconds,
        reason=reason,
    )


def _native_system_status_plan(*, timeout_seconds: int, reason: str) -> ActionPlan:
    return ActionPlan(
        action_type="get_system_status",
        executor="native",
        task="读取当前系统状态",
        timeout_seconds=timeout_seconds,
        reason=reason,
    )


def _delegated_action_type(arguments: dict, thought: Thought) -> str:
    return str(
        arguments.get("action_type")
        or (thought.action_request or {}).get("type")
        or "custom"
    )


def _raw_action_params(thought: Thought) -> str:
    return str((thought.action_request or {}).get("params") or "")


def _build_send_message_target(raw_params: str) -> str:
    target = _extract_action_first_param(raw_params, "target", "source", "chat_id", "chat")
    if target:
        if target.startswith(TELEGRAM_SOURCE_PREFIX):
            return target
        if target.isdigit() or (target.startswith("-") and target[1:].isdigit()):
            return f"{TELEGRAM_SOURCE_PREFIX}{target}"
    return ""


def _build_send_message_target_entity(raw_params: str) -> str:
    return _extract_action_first_param(raw_params, "target_entity", "entity") or ""


def _build_send_message_text(raw_params: str, thought: Thought) -> str:
    explicit = _extract_action_first_param(raw_params, "message", "text", "body", "content")
    if explicit:
        return explicit
    return _strip_action_marker(thought.content)


def _strip_action_marker(content: str) -> str:
    return ACTION_MARKER_PATTERN.sub("", content).strip()


def _send_telegram_message(target_source: str, message_text: str, *, timeout_seconds: int) -> str | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return "telegram_token_missing"
    chat_id = _telegram_chat_id_from_source(target_source)
    if chat_id is None:
        return "invalid_telegram_target"
    body = json.dumps({
        "chat_id": chat_id,
        "text": message_text,
    }).encode("utf-8")
    req = request.Request(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=max(1, timeout_seconds)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        return f"http_{exc.code}"
    except TELEGRAM_SEND_EXCEPTIONS as exc:
        return str(exc)
    if not isinstance(payload, dict) or not bool(payload.get("ok")):
        description = ""
        if isinstance(payload, dict):
            description = str(payload.get("description") or "").strip()
        return description or "telegram_send_failed"
    return None


def _telegram_chat_id_from_source(source: str) -> str | None:
    if not source.startswith(TELEGRAM_SOURCE_PREFIX):
        return None
    chat_id = source.removeprefix(TELEGRAM_SOURCE_PREFIX).strip()
    if chat_id.isdigit() or (chat_id.startswith("-") and chat_id[1:].isdigit()):
        return chat_id
    return None


def _prepare_send_message(
    action: ActionRecord,
    *,
    contact_resolver=None,
) -> tuple[str, str, str, ActionResultEnvelope | None]:
    target_source = str(action.request.get("target_source") or "").strip()
    target_entity = str(action.request.get("target_entity") or "").strip()
    message_text = str(action.request.get("message_text") or "").strip()
    if not target_source and target_entity and contact_resolver:
        target_source = str(contact_resolver(target_entity) or "").strip()
    if not target_source:
        if target_entity:
            return "", target_entity, message_text, _failure_result(
                f"无法解析实体 {target_entity} 的 Telegram 联系方式",
                "unresolved_target_entity",
                transport="native",
            )
        return "", target_entity, message_text, _failure_result(
            "缺少消息目标",
            "missing_target",
            transport="native",
        )
    if not target_source.startswith(TELEGRAM_SOURCE_PREFIX):
        return target_source, target_entity, message_text, _failure_result(
            "仅支持 Telegram 原生发送",
            "unsupported_target",
            transport="native",
        )
    if not message_text:
        return target_source, target_entity, message_text, _failure_result(
            "缺少消息内容",
            "missing_message",
            transport="native",
        )
    return target_source, target_entity, message_text, None


def _coerce_news_feed_urls(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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


def load_action_items(redis_client) -> list[JsonObject]:
    if redis_client is None:
        return []
    try:
        raw_items = redis_client.hvals(ACTION_REDIS_KEY)
    except AttributeError:
        try:
            raw_items = list(redis_client.hgetall(ACTION_REDIS_KEY).values())
        except AttributeError:
            return []
    items = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("skipping malformed action record: %s", exc)
            continue
        if not isinstance(item, dict):
            logger.warning("skipping non-object action record")
            continue
        items.append(item)
    return items


def pop_action_controls(redis_client, limit: int = 20) -> list[ActionControl]:
    if redis_client is None or limit <= 0:
        return []
    controls = []
    for _ in range(limit):
        try:
            raw_items = redis_client.lrange(ACTION_CONTROL_KEY, 0, 0)
        except ACTION_REDIS_EXCEPTIONS:
            return controls
        if not raw_items:
            break
        raw = raw_items[0]
        try:
            control = json.loads(raw)
        except (TypeError, ValueError):
            try:
                redis_client.ltrim(ACTION_CONTROL_KEY, 1, -1)
            except ACTION_REDIS_EXCEPTIONS:
                return controls
            continue
        controls.append(control)
        try:
            redis_client.ltrim(ACTION_CONTROL_KEY, 1, -1)
        except ACTION_REDIS_EXCEPTIONS:
            return controls
    return controls


def _action_from_json_object(item: JsonObject, *, now: datetime) -> ActionRecord | None:
    header = _parse_action_record_header(item)
    if header is None:
        return None
    if _should_skip_restored_action(header.status, header.awaiting_confirmation):
        return None
    request_payload = _coerce_action_request_payload(header.raw_request, header.source_content)
    restored_status, restored_result, dispatch_started_at = _restore_action_state(
        item,
        action_type=header.action_type,
        executor=header.executor,
    )
    action = ActionRecord(
        action_id=header.action_id,
        type=header.action_type,
        request=request_payload,
        executor=header.executor,
        status=restored_status,
        source_thought_id=header.source_thought_id,
        source_content=header.source_content,
        submitted_at=_parse_action_datetime(item.get("submitted_at")) or now,
        timeout_seconds=_clamp_timeout(item.get("timeout_seconds"), 300),
        run_id=_stringify_json_field(item.get("run_id")) or None,
        session_key=_stringify_json_field(item.get("session_key")) or None,
        awaiting_confirmation=header.awaiting_confirmation,
        retry_after=_parse_action_datetime(item.get("retry_after")),
        dispatch_started_at=dispatch_started_at,
    )
    if restored_result is not None:
        action.result = _normalize_action_result(restored_result, action)
    if action.status == "pending" and not action.awaiting_confirmation and action.executor == "openclaw":
        action.retry_after = action.retry_after or now
    return action


@dataclass
class _RestoredActionHeader:
    action_id: str
    action_type: str
    executor: str
    source_thought_id: str
    source_content: str
    status: str
    raw_request: JsonObject
    awaiting_confirmation: bool


def _parse_action_record_header(item: JsonObject) -> _RestoredActionHeader | None:
    action_id = _stringify_json_field(item.get("action_id"))
    action_type = _stringify_json_field(item.get("type"))
    executor = _stringify_json_field(item.get("executor"))
    source_thought_id = _stringify_json_field(item.get("source_thought_id"))
    source_content = _stringify_json_field(item.get("source_content"))
    status = _stringify_json_field(item.get("status")) or "pending"
    raw_request = item.get("request")
    if not (
        action_id
        and action_type
        and executor
        and source_thought_id
        and source_content
        and isinstance(raw_request, dict)
    ):
        logger.warning("skipping incomplete action record: %s", action_id or "<unknown>")
        return None
    return _RestoredActionHeader(
        action_id=action_id,
        action_type=action_type,
        executor=executor,
        source_thought_id=source_thought_id,
        source_content=source_content,
        status=status,
        raw_request=raw_request,
        awaiting_confirmation=bool(item.get("awaiting_confirmation")),
    )


def _should_skip_restored_action(status: str, awaiting_confirmation: bool) -> bool:
    return status not in {"pending", "running"} and not awaiting_confirmation


def _restore_action_state(
    item: JsonObject,
    *,
    action_type: str,
    executor: str,
) -> tuple[str, ActionResultEnvelope | None, datetime | None]:
    dispatch_started_at = _parse_action_datetime(item.get("dispatch_started_at"))
    status = _stringify_json_field(item.get("status")) or "pending"
    if status == "running" and action_type == "send_message" and dispatch_started_at is not None:
        return (
            "failed",
            _failure_result(
                "消息发送状态未知，为避免重复发送，未自动重试",
                "delivery_status_unknown",
                transport=executor,
            ),
            dispatch_started_at,
        )
    restored_status = "pending" if status == "running" else status
    restored_result = _coerce_restored_action_result(item.get("result"))
    return restored_status, restored_result, dispatch_started_at


def _coerce_action_request_payload(value: JsonObject, source_content: str) -> ActionRequestPayload:
    raw_action = _coerce_raw_action_request(value.get("raw_action"))
    payload: ActionRequestPayload = {
        "task": _stringify_json_field(value.get("task")) or source_content,
        "reason": _stringify_json_field(value.get("reason")) or "restored",
        "raw_action": raw_action,
    }
    news_feed_urls = _coerce_news_feed_urls(value.get("news_feed_urls"))
    if news_feed_urls:
        payload["news_feed_urls"] = news_feed_urls
    worker_agent_id = _stringify_json_field(value.get("worker_agent_id"))
    if worker_agent_id:
        payload["worker_agent_id"] = worker_agent_id
    target_source = _stringify_json_field(value.get("target_source"))
    if target_source:
        payload["target_source"] = target_source
    target_entity = _stringify_json_field(value.get("target_entity"))
    if target_entity:
        payload["target_entity"] = target_entity
    message_text = _stringify_json_field(value.get("message_text"))
    if message_text:
        payload["message_text"] = message_text
    return payload


def _coerce_restored_action_result(value) -> ActionResultEnvelope | None:
    if not isinstance(value, dict):
        return None
    restored_data = _coerce_json_object(value.get("data"))
    restored: ActionResultEnvelope = {
        "ok": bool(value.get("ok", False)),
        "summary": _stringify_json_field(value.get("summary")) or "",
        "data": restored_data,
        "error": value.get("error"),
        "run_id": _stringify_json_field(value.get("run_id")) or None,
        "session_key": _stringify_json_field(value.get("session_key")) or None,
        "transport": _stringify_json_field(value.get("transport")) or "",
    }
    raw_text = value.get("raw_text")
    if isinstance(raw_text, str):
        restored["raw_text"] = raw_text
    return restored


def _coerce_raw_action_request(value) -> RawActionRequest | None:
    if not isinstance(value, dict):
        return None
    action_type = _stringify_json_field(value.get("type"))
    params = _stringify_json_field(value.get("params"))
    if not action_type or params is None:
        return None
    raw_action: RawActionRequest = {
        "type": action_type,
        "params": params,
    }
    return raw_action


def _parse_action_datetime(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _coerce_json_object(value) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return value
