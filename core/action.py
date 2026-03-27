"""Phase 3 action planning and execution."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock

from core.stimulus import StimulusQueue
from core.thought_parser import Thought

ACTION_REDIS_KEY = "seedwake:actions"
ACTION_CONTROL_KEY = "seedwake:action_control"
OPENCLAW_ACTION_TYPES = {"search", "web_fetch", "system_change", "custom"}


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
    request: dict[str, object]
    executor: str
    status: str
    source_thought_id: str
    source_content: str
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timeout_seconds: int = 300
    result: dict[str, object] | None = None
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
        self._log_callback = log_callback
        self._event_callback = event_callback
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="seedwake-action")
        self._lock = Lock()
        self._actions: dict[str, ActionRecord] = {}

    def submit_from_thoughts(self, thoughts: list[Thought]) -> list[ActionRecord]:
        created: list[ActionRecord] = []
        for thought in thoughts:
            if not thought.action_request:
                continue

            try:
                plan = self._planner.plan(thought)
            except Exception as exc:
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

    def apply_controls(self, controls: list[dict[str, object]]) -> None:
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

    def attach_redis(self, redis_client) -> bool:
        self._redis = redis_client
        try:
            self._sync_to_redis()
        except Exception:
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
        action = self._get_action(action_id)
        self._update_action(action_id, status="running")
        self._publish_action_event(action, "running", "执行中")

        try:
            if action.executor == "native":
                result = _run_native_action(action)
            else:
                result = self._openclaw_executor.execute(action)
        except TimeoutError:
            self._finalize_action(
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
        except Exception as exc:
            self._finalize_action(
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

        result = _normalize_action_result(result, action)
        status = "succeeded" if result.get("ok", True) else "failed"
        self._finalize_action(action_id, status=status, result=result)

    def _finalize_action(self, action_id: str, *, status: str, result: dict[str, object]) -> None:
        result = _normalize_action_result(result, self._get_action(action_id))
        action = self._update_action(action_id, status=status, result=result)
        if isinstance(result.get("run_id"), str):
            action.run_id = str(result["run_id"])
        if isinstance(result.get("session_key"), str):
            action.session_key = str(result["session_key"])
        self._upsert_action(action)

        summary = str(result.get("summary") or "行动完成")
        self._emit(f"行动结束 {action.action_id} [{status}] {summary}")
        self._publish_action_event(action, status, summary)
        self._stimulus_queue.push(
            "action_result",
            2,
            f"action:{action.action_id}",
            f"{action.type} {status}: {summary}",
            action_id=action.action_id,
            metadata={
                "status": status,
                "executor": action.executor,
                "result": result,
            },
        )

    def _classify_policy(self, action: ActionRecord) -> str:
        if action.type in self._forbidden:
            return "forbidden"
        if action.type in self._require_confirmation:
            return "confirmation"
        if action.executor == "native":
            return "auto"
        if self._auto_execute and action.type not in self._auto_execute:
            return "rejected"
        return "auto"

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
        except Exception:
            self._redis = None

    def _sync_to_redis(self) -> None:
        with self._lock:
            actions = list(self._actions.values())
        for action in actions:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            self._redis.hset(ACTION_REDIS_KEY, action.action_id, payload)


class OllamaActionPlanner:
    """Second-pass planner using Ollama chat+tools."""

    def __init__(self, client, model_config: dict, default_timeout_seconds: int):
        self._client = client
        self._model_name = model_config["name"]
        self._default_timeout_seconds = default_timeout_seconds
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
            )
        return _fallback_plan(
            raw_action_type=str(action_request.get("type") or "custom"),
            thought=thought,
            default_timeout_seconds=self._default_timeout_seconds,
        )


def create_action_manager(
    redis_client,
    stimulus_queue: StimulusQueue,
    ollama_client,
    model_config: dict,
    action_config: dict,
    *,
    log_callback=None,
    event_callback=None,
) -> ActionManager:
    planner = OllamaActionPlanner(
        ollama_client,
        model_config,
        int(action_config.get("default_timeout_seconds", 300)),
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
                "纯本地、无副作用、一次函数调用即可完成的时间读取可选 native。"
                "网页搜索、网页抓取、系统变更、浏览器/命令行/文件修改或多步探索一律委托 OpenClaw。"
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
    if tool_name == "delegate_openclaw":
        action_type = str(
            arguments.get("action_type")
            or (thought.action_request or {}).get("type")
            or "custom"
        )
        task = str(arguments.get("task") or thought.content)
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
    if action_type in OPENCLAW_ACTION_TYPES or action_type:
        return ActionPlan(
            action_type=action_type,
            executor="openclaw",
            task=thought.content,
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
        )
    return None


def _run_native_action(action: ActionRecord) -> dict[str, object]:
    if action.type != "get_time":
        raise RuntimeError(f"不支持的 native action: {action.type}")

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


def _clamp_timeout(raw_value, default_timeout_seconds: int) -> int:
    if isinstance(raw_value, int):
        return max(1, raw_value)
    return max(1, default_timeout_seconds)


def _read_env(name: str) -> str:
    import os

    return os.environ.get(name, "")


def _action_to_dict(action: ActionRecord) -> dict:
    payload = asdict(action)
    payload["submitted_at"] = action.submitted_at.isoformat()
    return payload


def _normalize_action_result(result: dict[str, object], action: ActionRecord) -> dict[str, object]:
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
    except Exception:
        return False
    return True


def pop_action_controls(redis_client, limit: int = 20) -> list[dict[str, object]]:
    if redis_client is None or limit <= 0:
        return []
    controls = []
    for _ in range(limit):
        try:
            raw = redis_client.lpop(ACTION_CONTROL_KEY)
        except Exception:
            return controls
        if raw is None:
            break
        controls.append(json.loads(raw))
    return controls
