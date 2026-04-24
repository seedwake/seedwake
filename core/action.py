"""Phase 3 action planning and execution."""

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib import error, request
from typing import Protocol
from redis import exceptions as redis_exceptions

from core.i18n import prompt_block, t
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.openclaw_gateway import OpenClawGatewayExecutor, OpenClawUnavailableError
from core.perception import collect_system_status_snapshot
from core.rss import RSS_READ_EXCEPTIONS, read_news_result, summarize_news_items
from core.stimulus import (
    Stimulus,
    StimulusQueue,
    append_action_result_history,
    append_conversation_history,
    load_conversation_history,
    remember_recent_action_echoes,
)
from core.thought_parser import Thought, thought_action_requests
from core.common_types import (
    ActionControl,
    ActionEventPayload,
    ActionRequestPayload,
    ActionResultEnvelope,
    I18nTextPayload,
    JsonObject,
    JsonValue,
    NewsItem,
    PerceptionStimulusPayload,
    RawActionRequest,
    ReplyFocusPromptState,
    coerce_json_value,
    elapsed_ms,
)

ACTION_REDIS_KEY = "seedwake:actions"
ACTION_CONTROL_KEY = "seedwake:action_control"
NEWS_SEEN_REDIS_KEY = "seedwake:news_seen"
NOTE_REDIS_KEY = "seedwake:note"
NOTE_MAX_CHARS = 2000
TELEGRAM_SEND_FINISHED_LOG = "telegram send finished in %.1f ms (target=%s, status=%s)"
TELEGRAM_SOURCE_PREFIX = "telegram:"
OPENCLAW_ACTION_TYPES = {"search", "web_fetch", "system_change", "custom", "weather", "reading", "file_modify"}
DELEGATED_TOOL_COMPAT_ACTION_TYPES = {"news", "send_message"}
PERCEPTION_AUTO_EXECUTE_TYPES = {"news", "weather", "reading"}
OPS_ACTION_TYPES = {"system_change", "file_modify"}
THOUGHT_ACTION_TYPES = {
    "time",
    "system_status",
    "news",
    "weather",
    "reading",
    "search",
    "web_fetch",
    "send_message",
    "note_rewrite",
    "file_modify",
    "system_change",
}
READING_STIMULUS_EXCERPT_MAX_CHARS = 600
SEARCH_STIMULUS_MAX_RESULTS = 5
SEARCH_STIMULUS_TITLE_MAX_CHARS = 120
SEARCH_STIMULUS_URL_MAX_CHARS = 160
SEARCH_STIMULUS_SNIPPET_MAX_CHARS = 200
SEND_MESSAGE_SUMMARY_MAX_CHARS = 120
RECENT_SEND_MESSAGE_WINDOW = timedelta(hours=1)
RECENT_SEND_MESSAGE_COUNTABLE_STATUSES = {"pending", "running", "succeeded"}
REPLY_FOCUS_WINDOW = timedelta(minutes=10)
TELEGRAM_SEND_RETRY_DELAY_SECONDS = 30.0
TELEGRAM_SEND_RETRY_ATTEMPTS = 10
TELEGRAM_SEND_REQUEST_TIMEOUT_SECONDS = 10
ACTION_COMPLETED_DEFAULT_KEY = "action.completed_default"
ACTION_PLANNER_TIMEOUT_DESC_KEY = "action.planner_timeout_desc"
SEARCH_RESULT_DATA_SHAPE = '{"results":[{"title":"","url":"","snippet":""}]}'
WEB_FETCH_RESULT_DATA_SHAPE = '{"source":{"title":"","url":""},"excerpt_original":"","brief_note":""}'
READING_SOURCE_RESULT_DATA_SHAPE = '{"source":{"title":"","url":""},"excerpt_original":""}'
WEATHER_RESULT_DATA_SHAPE = (
    '{"location":"","condition":"","temperature_c":"","feels_like_c":"",'
    '"humidity_pct":"","wind_kph":""}'
)
FILE_MODIFY_RESULT_DATA_SHAPE = '{"path":"","applied":false,"changed":false,"change_summary":""}'
SYSTEM_CHANGE_RESULT_DATA_SHAPE = '{"applied":false,"status":"","change_summary":"","impact_scope":""}'
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
    *MODEL_CLIENT_EXCEPTIONS,
)
ACTION_EXECUTION_EXCEPTIONS = (
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
ACTION_MARKER_PATTERN = re.compile(r"\s*\{action:[^}]+\}", re.DOTALL)
THOUGHT_CYCLE_ID_PATTERN = re.compile(r"^C(?P<cycle_id>\d+)-\d+$")
type ActionUpdateValue = JsonValue | datetime | ActionResultEnvelope | None


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
    reply_to_message_id: str = ""


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


class PlannerLike(Protocol):
    def plan(
        self,
        thought: Thought,
        *,
        conversation_source: str | None = None,
    ) -> ActionPlan | tuple[None, str | None] | None: ...


class OpenClawExecutorLike(Protocol):
    def execute(self, action: ActionRecord) -> ActionResultEnvelope: ...


class ActionRedisLike(Protocol):
    def hset(self, key: str, hash_field: str, value: str) -> int: ...
    def hvals(self, key: str) -> list[str]: ...
    def hgetall(self, key: str) -> dict[str, str]: ...
    def get(self, key: str) -> str | bytes | None: ...
    def set(self, key: str, value: str) -> bool | str | None: ...
    def rpush(self, key: str, payload: str) -> int: ...
    def ltrim(self, key: str, start: int, end: int) -> bool: ...
    def lrange(self, key: str, start: int, end: int) -> list[str]: ...
    def zscore(self, key: str, member: str) -> float | None: ...
    def zadd(self, key: str, mapping: dict[str, float], nx: bool = False) -> int: ...
    def zrem(self, key: str, member: str) -> int: ...
    def zremrangebyscore(self, key: str, min_score: str | float, max_score: str | float) -> int: ...
    def zcard(self, key: str) -> int: ...
    def zremrangebyrank(self, key: str, start: int, end: int) -> int: ...


class ActionManager:
    """Owns action planning, execution, state, and result stimuli."""

    def __init__(
        self,
        redis_client: ActionRedisLike | None,
        stimulus_queue: StimulusQueue,
        planner: PlannerLike,
        openclaw_executor: OpenClawExecutorLike,
        *,
        auto_execute: list[str],
        require_confirmation: list[str],
        forbidden: list[str],
        news_seen_ttl_hours: int = 720,
        news_seen_max_items: int = 5000,
        news_reader: Callable[..., ActionResultEnvelope] = read_news_result,
        contact_resolver: Callable[[str], str | None] | None = None,
        openclaw_retry_delay_seconds: float = 5.0,
        callbacks: ActionCallbacks | None = None,
    ) -> None:
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
        resolved_callbacks = callbacks if callbacks is not None else ActionCallbacks()
        self._log_callback = resolved_callbacks.log
        self._event_callback = resolved_callbacks.event
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="seedwake-action")
        self._lock = Lock()
        self._actions: dict[str, ActionRecord] = {}
        self._news_seen_shadow: dict[str, float] = {}
        self._note_shadow = ""
        self._note_shadow_dirty = False
        self._pending_prompt_echoes: list[Stimulus] = []
        self._perception_observations: list[str] = []
        self._futures: set[Future] = set()
        self._recent_sent_messages: list[tuple[str, str, str]] = []  # (target, message, reply_to) dedup window
        self._reply_focus_source = ""
        self._reply_focus_updated_at: datetime | None = None
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
        conversation_reply_to_message_id = _latest_conversation_message_id(stimuli or [])
        if conversation_source:
            self._remember_reply_focus(conversation_source)
        else:
            conversation_source = self._current_reply_focus_source()
            conversation_reply_to_message_id = None
        for thought in thoughts:
            for action_index, action_thought in enumerate(self._submitted_action_thoughts(thought)):
                action = self._plan_submitted_action(
                    action_thought,
                    conversation_source=conversation_source,
                    conversation_reply_to_message_id=conversation_reply_to_message_id,
                )
                if action is None:
                    continue
                action.action_id = self._submitted_action_id(thought.thought_id, action_index)
                self._upsert_action(action)
                created.append(action)
                self._dispatch_submitted_action(action)

        return created

    def _plan_submitted_action(
        self,
        thought: Thought,
        *,
        conversation_source: str | None,
        conversation_reply_to_message_id: str | None,
    ) -> ActionRecord | None:
        if not thought.action_request:
            return None
        try:
            plan_result = self._planner.plan(thought, conversation_source=conversation_source)
        except PLANNER_EXCEPTIONS as exc:
            self._emit(t("action.plan_failed", thought_id=thought.thought_id, error=exc))
            return None
        plan, skip_reason = _coerce_planner_result(plan_result)
        if not plan:
            raw_action_type = str((thought.action_request or {}).get("type") or "custom")
            logger.info("planner returned no plan for %s (action_type=%s)",
                        thought.thought_id, raw_action_type)
            self._emit_planner_feedback(thought, raw_action_type, skip_reason)
            return None
        action = _action_from_plan(
            thought=thought,
            plan=plan,
            conversation_source=conversation_source,
            conversation_reply_to_message_id=conversation_reply_to_message_id,
        )
        self._log_submitted_action_plan(thought, action, conversation_source)
        return action

    @staticmethod
    def _submitted_action_thoughts(thought: Thought) -> list[Thought]:
        action_requests = thought_action_requests(thought)
        if not action_requests:
            return [thought]
        stripped_content = _strip_action_marker(thought.content) or thought.content
        return [
            replace(
                thought,
                content=stripped_content,
                action_request=action_request,
                additional_action_requests=[],
            )
            for action_request in action_requests
        ]

    @staticmethod
    def _submitted_action_id(thought_id: str, action_index: int) -> str:
        if action_index == 0:
            return f"act_{thought_id}"
        return f"act_{thought_id}-{action_index + 1}"

    @staticmethod
    def _log_submitted_action_plan(
        thought: Thought,
        action: ActionRecord,
        conversation_source: str | None,
    ) -> None:
        raw_action_type = str((thought.action_request or {}).get("type") or "custom")
        action_request_payload = action.request
        logger.info(
            "planner produced action for %s (raw_type=%s, action_type=%s, executor=%s, "
            "conversation_source=%s, target_source=%s, target_entity=%s, reply_to=%s)",
            thought.thought_id,
            raw_action_type,
            action.type,
            action.executor,
            str(conversation_source or "-"),
            str(action_request_payload.get("target_source") or "-"),
            str(action_request_payload.get("target_entity") or "-"),
            str(action_request_payload.get("reply_to_message_id") or "-"),
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
                self._emit(t("action.confirmed", action_id=action_id, actor=actor))
                action = self._update_action(action_id, awaiting_confirmation=False)
                self._publish_action_event(
                    action,
                    "pending",
                    "action.confirmed_status",
                    {"actor": actor},
                )
                self._start_action(action_id)
                continue

            summary = t("action.rejected_summary", actor=actor)
            if note:
                summary = f"{summary}: {note}"
            self._emit(t("action.rejected", action_id=action_id, actor=actor))
            self._update_action(action_id, awaiting_confirmation=False)
            self._finalize_action(
                action_id,
                status="failed",
                result=_failure_result(
                    summary,
                    "rejected",
                    transport=action.executor,
                    summary_key="action.rejected_summary",
                    summary_params={"actor": actor},
                ),
            )

    def running_actions(self) -> list[ActionRecord]:
        """Return snapshot copies of pending/running actions.

        Uses dataclasses.replace to freeze status at capture time,
        preventing race conditions where an action completes between
        capture and prompt rendering.
        """
        with self._lock:
            actions = [
                replace(action)
                for action in self._actions.values()
                if action.status in {"pending", "running"}
            ]
        return sorted(actions, key=lambda action: action.submitted_at)

    def recent_send_message_requests(self, limit: int = 9) -> list[ActionRequestPayload]:
        cutoff = datetime.now(timezone.utc) - RECENT_SEND_MESSAGE_WINDOW
        with self._lock:
            entries = [
                (
                    _request_payload_with_recent_metadata(
                        action.request,
                        submitted_at=action.submitted_at,
                        status=action.status,
                    ),
                    action.status in RECENT_SEND_MESSAGE_COUNTABLE_STATUSES,
                )
                for action in sorted(self._actions.values(), key=lambda item: item.submitted_at)
                if action.type == "send_message" and action.submitted_at >= cutoff
            ]
        if limit <= 0:
            return [payload for payload, is_countable in entries if not is_countable]
        countable_indexes = [
            index
            for index, (_, is_countable) in enumerate(entries)
            if is_countable
        ]
        selected_countable_indexes = set(countable_indexes[-limit:])
        return [
            payload
            for index, (payload, is_countable) in enumerate(entries)
            if (not is_countable) or index in selected_countable_indexes
        ]

    def pop_perception_observations(self) -> list[str]:
        with self._lock:
            observations = list(self._perception_observations)
            self._perception_observations.clear()
        return observations

    def pop_prompt_echoes(self) -> list[Stimulus]:
        with self._lock:
            echoes = list(self._pending_prompt_echoes)
            self._pending_prompt_echoes.clear()
        return echoes

    def requeue_prompt_echoes(self, echoes: list[Stimulus]) -> None:
        if not echoes:
            return
        with self._lock:
            self._pending_prompt_echoes = [*echoes, *self._pending_prompt_echoes]

    def current_note(self) -> str:
        redis_client = self._redis
        if redis_client is not None:
            try:
                raw_value = redis_client.get(NOTE_REDIS_KEY)
            except ACTION_REDIS_EXCEPTIONS:
                self._redis = None
            else:
                note = _normalize_note_content(raw_value)
                with self._lock:
                    self._note_shadow = note
                    self._note_shadow_dirty = False
                return note
        with self._lock:
            return self._note_shadow

    def attach_redis(self, redis_client: ActionRedisLike | None) -> bool:
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
        self._emit(t("action.submitted", action_id=action.action_id, type=action.type, executor=action.executor))
        self._publish_action_event(
            action,
            "pending",
            "action.submitted_status",
        )
        self._update_action(action_id, status="running", retry_after=None)
        future = self._pool.submit(self._run_action, action_id)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def _run_action(self, action_id: str) -> None:
        action = self._get_action(action_id)
        started_at = time.perf_counter()
        terminal_status = "unknown"
        try:
            self._publish_action_event(
                action,
                "running",
                "action.running_status",
            )

            if action.executor == "native":
                result = self._run_native_action(action_id)
            else:
                result = self._openclaw_executor.execute(action)
            result = _normalize_action_result(result, action)
            status = "succeeded" if result.get("ok", True) else "failed"
            terminal_status = status
            self._safe_finalize_action(action_id, status=status, result=result)
            return
        except OpenClawUnavailableError as exc:
            terminal_status = "deferred"
            self._defer_openclaw_action(action_id, str(exc))
            return
        except TimeoutError:
            terminal_status = "timeout"
            self._safe_finalize_action(
                action_id,
                status="timeout",
                result=_failure_result(
                    t("action.timeout"),
                    "timeout",
                    transport=action.executor,
                    summary_key="action.timeout",
                ),
            )
            return
        except ACTION_EXECUTION_EXCEPTIONS as exc:
            terminal_status = "failed"
            self._safe_finalize_action(
                action_id,
                status="failed",
                result=_failure_result(
                    t("action.failed", error=exc),
                    str(exc),
                    transport=action.executor,
                    summary_key="action.failed",
                    summary_params={"error": str(exc)},
                ),
            )
            return
        # noinspection PyBroadException
        except Exception as exc:
            terminal_status = "failed"
            logger.exception("unexpected action worker failure: %s", action_id)
            self._force_fail_action(action_id, t("action.internal_error", error=exc))
            return
        finally:
            logger.info(
                "action %s [%s/%s] finished in %.1f ms (status=%s)",
                action.action_id,
                action.type,
                action.executor,
                elapsed_ms(started_at),
                terminal_status,
            )

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
            return _send_message_failure_result(
                target_source=target_source,
                target_entity=target_entity,
                message_text=message_text,
                summary=str(failure.get("summary") or t("action.send_failed")),
                error_detail=failure.get("error"),
                summary_key=str(failure.get("summary_key") or ""),
                summary_params=_json_object_or_none(failure.get("summary_params")),
            )
        reply_to = str(action.request.get("reply_to_message_id") or "").strip()
        if self._is_duplicate_message(target_source, message_text, reply_to):
            return _send_message_failure_result(
                target_source=target_source,
                target_entity=target_entity,
                message_text=message_text,
                summary=t("action.send_duplicate"),
                error_detail="duplicate_message",
                summary_key="action.send_duplicate",
            )
        if not self._mark_dispatch_started(action_id):
            return _send_message_failure_result(
                target_source=target_source,
                target_entity=target_entity,
                message_text=message_text,
                summary=t("action.send_persist_failed"),
                error_detail="delivery_state_unavailable",
                summary_key="action.send_persist_failed",
            )
        send_error, delivered_reply_to = _send_telegram_message(
            target_source,
            message_text,
            timeout_seconds=action.timeout_seconds,
            reply_to_message_id=reply_to,
        )
        if send_error:
            self._update_action(action_id, dispatch_started_at=None)
            return _send_message_failure_result(
                target_source=target_source,
                target_entity=target_entity,
                message_text=message_text,
                summary=t("action.telegram_send_failed", error=send_error),
                error_detail=send_error,
                summary_key="action.telegram_send_failed",
                summary_params={"error": send_error},
            )
        self._record_sent_message(target_source, message_text, delivered_reply_to)
        self._refresh_reply_focus(target_source)
        return _build_action_result(
            ok=True,
            summary=_send_message_success_summary(target_source, message_text),
            data=_send_message_result_data(
                target_source=target_source,
                target_entity=target_entity,
                message_text=message_text,
            ),
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
            summary_key=_send_message_success_summary_key(message_text),
            summary_params=_send_message_success_summary_params(target_source, message_text),
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

        summary = str(result.get("summary") or t(ACTION_COMPLETED_DEFAULT_KEY))
        self._maybe_update_note_shadow(action, status, result)
        self._emit(t("action.completed_log", action_id=action.action_id, status=status, summary=summary))
        self._publish_action_event(
            action,
            status,
            _result_summary_key(result, summary),
            _result_summary_params(result, summary),
        )
        self._publish_native_message(action, status, result)
        self._record_perception_observation(action, status, result)
        stimulus_payload = _build_result_stimulus(action, status, result)
        history_stimulus = _stimulus_from_payload(action.action_id, stimulus_payload)
        append_action_result_history(self._redis, history_stimulus)  # type: ignore[arg-type]
        self._remember_recent_action_echo(action, history_stimulus)
        if not should_emit_stimulus:
            return
        stimulus = stimulus_payload
        if _should_emit_prompt_echo_directly(action, status, result):
            self._remember_prompt_echo(action.action_id, stimulus)
            return
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
            self._emit(t("action.awaiting_confirmation", action_id=action.action_id))
            self._publish_action_event(
                action,
                "pending",
                "action.awaiting_status",
            )
            return
        if policy == "forbidden":
            self._emit(t("action.forbidden", action_id=action.action_id))
            self._finalize_action(
                action.action_id,
                status="failed",
                result=_failure_result(
                    t("action.forbidden_summary"),
                    "forbidden",
                    transport=action.executor,
                    summary_key="action.forbidden_summary",
                ),
            )
            return
        self._emit(t("action.not_auto", action_id=action.action_id))
        self._finalize_action(
            action.action_id,
            status="failed",
            result=_failure_result(
                t("action.not_auto_summary"),
                "not_auto_execute",
                transport=action.executor,
                summary_key="action.not_auto_summary",
            ),
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
            self._force_fail_action(action_id, t("action.finalize_error", error=exc))

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
        self._emit(t("action.openclaw_queued", action_id=action.action_id, reason=reason))
        self._publish_action_event(
            action,
            "pending",
            "action.openclaw_queued_status",
        )

    def _emit(self, text: str) -> None:
        if self._log_callback:
            self._log_callback(text)

    def _remember_prompt_echo(self, action_id: str, stimulus: PerceptionStimulusPayload) -> None:
        pending = Stimulus(
            stimulus_id=f"prompt_{action_id}",
            type=stimulus["type"],
            priority=stimulus["priority"],
            source=stimulus["source"],
            content=stimulus["content"],
            action_id=action_id,
            metadata=dict(stimulus["metadata"]),
        )
        with self._lock:
            self._pending_prompt_echoes.append(pending)

    def _emit_planner_feedback(self, thought: Thought, raw_action_type: str, reason: str | None) -> None:
        reason_text = (reason or "").strip()
        if reason_text:
            summary = t("action.skipped_reason", action_type=raw_action_type, reason=reason_text)
        else:
            summary = t("action.skipped_inhibited", action_type=raw_action_type)
        result = _failure_result(summary, "ignored_by_planner", transport="planner")
        self._emit(t("action.skipped_log", thought_id=thought.thought_id, action_type=raw_action_type))
        metadata: JsonObject = {
            "origin": "action",
            "action_type": raw_action_type,
            "status": "ignored",
            "executor": "planner",
            "source_thought_id": thought.thought_id,
            "result": _action_result_to_json_object(result),
        }
        self._stimulus_queue.push(
            "action_result",
            2,
            f"planner:{thought.thought_id}",
            summary,
            metadata=metadata,
        )

    def _publish_action_event(
        self,
        action: ActionRecord,
        status: str,
        summary_key: str,
        summary_params: JsonObject | None = None,
    ) -> None:
        if not self._event_callback:
            return
        payload: ActionEventPayload = {
            "action_id": action.action_id,
            "type": action.type,
            "executor": action.executor,
            "status": status,
            "source_thought_id": action.source_thought_id,
            "summary": _i18n_text(summary_key, summary_params),
            "run_id": action.run_id,
            "session_key": action.session_key,
            "awaiting_confirmation": action.awaiting_confirmation,
        }
        self._event_callback("action", payload)

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
        target_name = _conversation_target_name(self._redis, source)
        try:
            # ActionRedisLike covers rpush/ltrim used by append_conversation_history
            append_conversation_history(
                self._redis,  # type: ignore[arg-type]
                role="assistant",
                source=source,
                content=message,
                metadata={"action_id": action.action_id, "target_name": target_name},
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
                "target_source": source,
                "target_name": target_name,
            })
        except Exception as exc:
            logger.exception("unexpected native message event failure for %s: %s", action.action_id, exc)

    def _remember_recent_action_echo(self, action: ActionRecord, stimulus: Stimulus) -> None:
        cycle_id = _thought_cycle_id(action.source_thought_id)
        if cycle_id is None:
            return
        try:
            remember_recent_action_echoes(
                self._redis,  # type: ignore[arg-type]
                cycle_id,
                [stimulus],
            )
        except ACTION_REDIS_EXCEPTIONS as exc:
            logger.warning("failed to persist recent action echo for %s: %s", action.action_id, exc)
            self._redis = None
        except Exception as exc:
            logger.exception("unexpected recent action echo failure for %s: %s", action.action_id, exc)

    def _snapshot_futures(self) -> list[Future]:
        with self._lock:
            return list(self._futures)

    def _discard_future(self, future: Future) -> None:
        with self._lock:
            self._futures.discard(future)

    _SENT_MESSAGE_DEDUP_WINDOW = 5

    def reply_focus_prompt_state(self) -> ReplyFocusPromptState | None:
        source = self._current_reply_focus_source()
        if source is None:
            return None
        return ReplyFocusPromptState(source=source)

    def _remember_reply_focus(self, source: str) -> None:
        normalized_source = str(source).strip()
        if not normalized_source:
            return
        with self._lock:
            self._reply_focus_source = normalized_source
            self._reply_focus_updated_at = datetime.now(timezone.utc)

    def _current_reply_focus_source(self) -> str | None:
        with self._lock:
            source = self._reply_focus_source
            updated_at = self._reply_focus_updated_at
        if not source or updated_at is None:
            return None
        if datetime.now(timezone.utc) - updated_at > REPLY_FOCUS_WINDOW:
            return None
        return source

    def _refresh_reply_focus(self, source: str) -> None:
        normalized_source = str(source).strip()
        if not normalized_source:
            return
        with self._lock:
            if self._reply_focus_source != normalized_source:
                return
            self._reply_focus_updated_at = datetime.now(timezone.utc)

    def _is_duplicate_message(self, target: str, message: str, reply_to_message_id: str) -> bool:
        key = (target.strip(), message.strip(), reply_to_message_id.strip())
        with self._lock:
            return key in self._recent_sent_messages

    def _record_sent_message(self, target: str, message: str, reply_to_message_id: str) -> None:
        key = (target.strip(), message.strip(), reply_to_message_id.strip())
        with self._lock:
            if key not in self._recent_sent_messages:
                self._recent_sent_messages.append(key)
            if len(self._recent_sent_messages) > self._SENT_MESSAGE_DEDUP_WINDOW:
                self._recent_sent_messages = self._recent_sent_messages[-self._SENT_MESSAGE_DEDUP_WINDOW:]

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
                summary=t("action.news_missing_entries"),
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
            deduped_result["summary"] = t("action.news_unrecognizable")
            deduped_result["error"] = "malformed_news_items"
            return deduped_result, True
        deduped_result["summary"] = summarize_news_items(new_items)
        if not new_items:
            deduped_result["summary"] = t("action.news_no_new")
            return deduped_result, True
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
                    added = _redis_zadd_nx(
                        self._redis,
                        NEWS_SEEN_REDIS_KEY,
                        {item_key: expires_at},
                    )
                    if not added:
                        score = self._redis.zscore(NEWS_SEEN_REDIS_KEY, item_key)
                        if score is not None and float(score) > now_ts:
                            self._news_seen_shadow[item_key] = float(score)
                            self._trim_news_seen_shadow_locked()
                            self._prune_news_seen_redis(self._redis, now_ts)
                            return False
                    self._news_seen_shadow[item_key] = expires_at
                    self._trim_news_seen_shadow_locked()
                    self._prune_news_seen_redis(self._redis, now_ts)
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
        redis_client = self._redis
        if redis_client is not None:
            try:
                self._prune_news_seen_redis(redis_client, now_ts)
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

    def _prune_news_seen_redis(self, redis_client: ActionRedisLike, now_ts: float) -> None:
        redis_client.zremrangebyscore(NEWS_SEEN_REDIS_KEY, "-inf", now_ts)
        total = int(redis_client.zcard(NEWS_SEEN_REDIS_KEY) or 0)
        extra = total - self._news_seen_max_items
        if extra > 0:
            redis_client.zremrangebyrank(NEWS_SEEN_REDIS_KEY, 0, extra - 1)

    def _get_action(self, action_id: str) -> ActionRecord:
        with self._lock:
            return self._actions[action_id]

    def _update_action(self, action_id: str, **changes: ActionUpdateValue) -> ActionRecord:
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
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            redis_client.hset(ACTION_REDIS_KEY, action.action_id, payload)
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
            note_text = self._note_shadow
            redis_client = self._redis
        if redis_client is None:
            return
        for action in actions:
            payload = json.dumps(_action_to_dict(action), ensure_ascii=False)
            redis_client.hset(ACTION_REDIS_KEY, action.action_id, payload)
        self._sync_news_seen_to_redis(seen_items)
        redis_client.set(NOTE_REDIS_KEY, note_text)
        with self._lock:
            self._note_shadow_dirty = False

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
        redis_client = self._redis
        if redis_client is None:
            return
        with self._lock:
            note_shadow_dirty = self._note_shadow_dirty
        if note_shadow_dirty:
            return
        with self._lock:
            self._note_shadow = _normalize_note_content(redis_client.get(NOTE_REDIS_KEY))
            self._note_shadow_dirty = False

    def _sync_news_seen_to_redis(self, seen_items: dict[str, float]) -> None:
        if not seen_items:
            return
        redis_client = self._redis
        if redis_client is None:
            return
        now_ts = datetime.now(timezone.utc).timestamp()
        valid_items = {
            item_key: expires_at
            for item_key, expires_at in seen_items.items()
            if expires_at > now_ts
        }
        if not valid_items:
            return
        _redis_zadd(redis_client, NEWS_SEEN_REDIS_KEY, valid_items)
        self._prune_news_seen_redis(redis_client, now_ts)

    def _maybe_update_note_shadow(
        self,
        action: ActionRecord,
        status: str,
        result: ActionResultEnvelope,
    ) -> None:
        if action.type != "note_rewrite" or status != "succeeded" or not bool(result.get("ok", True)):
            return
        data = result.get("data")
        note_text = _normalize_note_content(data.get("content") if isinstance(data, dict) else None)
        with self._lock:
            self._note_shadow = note_text
            self._note_shadow_dirty = True
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            redis_client.set(NOTE_REDIS_KEY, note_text)
            with self._lock:
                self._note_shadow_dirty = False
        except ACTION_REDIS_EXCEPTIONS:
            self._redis = None


class ActionPlanner:
    """Second-pass planner using the configured chat provider."""

    def __init__(
        self,
        client: ModelClient,
        model_config: dict,
        default_timeout_seconds: int,
        default_weather_location: str,
        news_feed_urls: list[str],
        worker_agent_id: str,
        ops_worker_agent_id: str,
        prompt_log_callback: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self._client = client
        self._model_name = model_config["name"]
        self._default_timeout_seconds = default_timeout_seconds
        self._default_weather_location = default_weather_location.strip()
        self._news_feed_urls = [item.strip() for item in news_feed_urls if item.strip()]
        self._worker_agent_id = worker_agent_id.strip()
        self._ops_worker_agent_id = ops_worker_agent_id.strip()
        self._prompt_log_callback = prompt_log_callback
        self._options = {
            "num_ctx": model_config.get("num_ctx", 32768),
            "temperature": 0.1,
        }

    def _log_planner_prompt(
        self,
        thought_id: str,
        messages: list[dict[str, str]],
        mode: str,
    ) -> None:
        if self._prompt_log_callback is None:
            return
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            parts.append(f"[{role}]\n{content}")
        self._prompt_log_callback(
            f"PLANNER {thought_id} ({mode})",
            "\n\n".join(parts),
            "🔵",
        )

    def plan(
        self,
        thought: Thought,
        *,
        conversation_source: str | None = None,
    ) -> ActionPlan | tuple[None, str | None] | None:
        started_at = time.perf_counter()
        action_request = thought.action_request or {}
        raw_action_type = str(action_request.get("type") or "")
        if self._client.supports_tool_calls:
            messages = _planner_messages(thought, conversation_source=conversation_source)
            self._log_planner_prompt(thought.thought_id, messages, mode="tool")
            response = self._client.chat(
                model=self._model_name,
                messages=messages,
                tools=_planner_tools(),
                options=self._options,
            )
            message = response.get("message")
            tool_calls = message.get("tool_calls") if isinstance(message, dict) else []
            if not isinstance(tool_calls, list):
                tool_calls = []
            if tool_calls:
                tool_name = tool_calls[0]["function"]["name"]
                plan = _plan_from_tool_call(
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
                logger.info(
                    "planner decision finished in %.1f ms (thought_id=%s, mode=tool, raw_type=%s, tool=%s)",
                    elapsed_ms(started_at),
                    thought.thought_id,
                    raw_action_type,
                    tool_name,
                )
                return plan
            plan = _fallback_plan(
                raw_action_type=str(action_request.get("type") or "custom"),
                thought=thought,
                default_timeout_seconds=self._default_timeout_seconds,
                default_weather_location=self._default_weather_location,
                news_feed_urls=self._news_feed_urls,
                worker_agent_id=self._worker_agent_id,
                ops_worker_agent_id=self._ops_worker_agent_id,
                conversation_source=conversation_source,
            )
            logger.info(
                "planner decision finished in %.1f ms (thought_id=%s, mode=fallback, raw_type=%s)",
                elapsed_ms(started_at),
                thought.thought_id,
                raw_action_type,
            )
            return plan

        json_messages = _planner_json_messages(thought, conversation_source=conversation_source)
        self._log_planner_prompt(thought.thought_id, json_messages, mode="json")
        response = self._client.chat(
            model=self._model_name,
            messages=json_messages,
            options={**self._options, "max_tokens": 512},
        )
        message = response.get("message")
        plan = _plan_from_json_reply(
            raw_content=str(message.get("content") or "") if isinstance(message, dict) else "",
            thought=thought,
            default_timeout_seconds=self._default_timeout_seconds,
            default_weather_location=self._default_weather_location,
            news_feed_urls=self._news_feed_urls,
            worker_agent_id=self._worker_agent_id,
            ops_worker_agent_id=self._ops_worker_agent_id,
            conversation_source=conversation_source,
        )
        logger.info(
            "planner decision finished in %.1f ms (thought_id=%s, mode=json, raw_type=%s)",
            elapsed_ms(started_at),
            thought.thought_id,
            raw_action_type,
        )
        return plan


def create_action_manager(
    redis_client: ActionRedisLike | None,
    stimulus_queue: StimulusQueue,
    planner_client: ModelClient,
    model_config: dict,
    action_config: dict,
    *,
    contact_resolver: Callable[[str], str | None] | None = None,
    news_feed_urls: list[str] | None = None,
    news_seen_ttl_hours: int = 720,
    news_seen_max_items: int = 5000,
    log_callback: Callable[[str], None] | None = None,
    event_callback: Callable[[str, JsonObject], None] | None = None,
    prompt_log_callback: Callable[[str, str, str], None] | None = None,
) -> ActionManager:
    planner = ActionPlanner(
        planner_client,
        model_config,
        int(action_config.get("default_timeout_seconds", 300)),
        str(action_config.get("default_weather_location", "")),
        list(news_feed_urls or []),
        str(action_config.get("worker_agent_id", "seedwake-worker")),
        str(action_config.get("ops_worker_agent_id", "seedwake-ops")),
        prompt_log_callback=prompt_log_callback,
    )
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
            "content": _planner_system_prompt()
        },
        {"role": "user", "content": user_prompt},
    ]


def _planner_json_messages(thought: Thought, *, conversation_source: str | None = None) -> list[dict[str, str]]:
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
            "content": _planner_json_system_prompt(),
        },
        {"role": "user", "content": user_prompt},
    ]


def _planner_system_prompt() -> str:
    return str(prompt_block("PLANNER_SYSTEM_PROMPT"))


def _planner_json_system_prompt() -> str:
    return (
        _planner_system_prompt()
        + str(prompt_block("PLANNER_OUTPUT_FORMAT"))
        + _planner_json_tool_contract()
    )


def _planner_json_tool_contract() -> str:
    parts = [t("action.tool_list_header")]
    for tool in _planner_tools():
        entry = _planner_json_tool_contract_entry(tool)
        if entry:
            parts.append(entry)
    return "".join(parts)


def _planner_json_tool_contract_entry(tool: dict) -> str:
    function = tool.get("function") or {}
    if not isinstance(function, dict):
        return ""
    name = str(function.get("name") or "").strip()
    if not name:
        return ""
    description = str(function.get("description") or "").strip()
    field_contracts = _planner_json_tool_field_contracts(function.get("parameters"))
    if not field_contracts:
        return t("action.tool_no_args", name=name, description=description)
    joined_fields = "；".join(field_contracts)
    return t("action.tool_with_args", name=name, description=description, fields=joined_fields)


def _planner_json_tool_field_contracts(parameters: JsonValue) -> list[str]:
    if not isinstance(parameters, dict):
        return []
    required_fields = {
        str(item).strip()
        for item in (parameters.get("required") or [])
        if str(item).strip()
    }
    properties = parameters.get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        return []
    field_contracts: list[str] = []
    for field_name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        field_contracts.append(_planner_json_field_contract(
            field_name=str(field_name),
            schema=schema,
            required=field_name in required_fields,
        ))
    return [item for item in field_contracts if item]


def _planner_json_field_contract(*, field_name: str, schema: JsonObject, required: bool) -> str:
    required_label = t("action.field_required") if required else t("action.field_optional")
    type_label = str(schema.get("type") or "any").strip()
    description = str(schema.get("description") or "").strip()
    enum_values = schema.get("enum")
    enum_label = ""
    if isinstance(enum_values, list) and enum_values:
        enum_items = [str(item).strip() for item in enum_values if str(item).strip()]
        if enum_items:
            enum_label = t("action.field_enum_label", values=", ".join(enum_items))
    detail = t(
        "action.field_detail",
        field_name=field_name,
        required_label=required_label,
        type_label=type_label,
        enum_label=enum_label,
    )
    if description:
        return t("action.field_detail_with_description", detail=detail, description=description)
    return detail


def _plan_from_json_reply(
    *,
    raw_content: str,
    thought: Thought,
    default_timeout_seconds: int,
    default_weather_location: str,
    news_feed_urls: list[str],
    worker_agent_id: str,
    ops_worker_agent_id: str,
    conversation_source: str | None,
) -> ActionPlan | tuple[None, str | None] | None:
    payload = _parse_planner_json_payload(raw_content)
    if payload is None:
        return _fallback_plan(
            raw_action_type=str((thought.action_request or {}).get("type") or "custom"),
            thought=thought,
            default_timeout_seconds=default_timeout_seconds,
            default_weather_location=default_weather_location,
            news_feed_urls=news_feed_urls,
            worker_agent_id=worker_agent_id,
            ops_worker_agent_id=ops_worker_agent_id,
            conversation_source=conversation_source,
        )
    tool_name = str(payload.get("tool") or "").strip()
    arguments = _coerce_planner_arguments(payload.get("arguments"))
    return _plan_from_tool_call(
        tool_name,
        arguments,
        thought,
        default_timeout_seconds,
        default_weather_location,
        news_feed_urls,
        worker_agent_id,
        ops_worker_agent_id,
        conversation_source,
    )


def _parse_planner_json_payload(raw_content: str) -> JsonObject | None:
    content = raw_content.strip()
    if not content:
        return None
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            content = "\n".join(lines[1:-1]).strip()
            if content.lower().startswith("json"):
                content = content[4:].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("planner returned non-json content: %s", raw_content)
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_planner_arguments(raw_arguments: JsonValue) -> JsonObject:
    if isinstance(raw_arguments, dict):
        return {str(key): value for key, value in raw_arguments.items()}
    if not isinstance(raw_arguments, str):
        return {}
    content = raw_arguments.strip()
    if not content:
        return {}
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("planner returned non-json arguments: %s", raw_arguments)
        return {}
    return _coerce_json_object(payload)


def _planner_tools() -> list[JsonObject]:
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
                        "action_type": {
                            "type": "string",
                            "enum": sorted(OPENCLAW_ACTION_TYPES),
                            "description": t("action.tool.openclaw_action_type"),
                        },
                        "task": {
                            "type": "string",
                            "description": t("action.tool.openclaw_task"),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": t(ACTION_PLANNER_TIMEOUT_DESC_KEY),
                        },
                        "reason": {
                            "type": "string",
                            "description": t("action.tool.openclaw_reason"),
                        },
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
                        "reason": {"type": "string", "description": t("action.tool.time_reason")},
                        "timeout_seconds": {
                            "type": "integer",
                            "description": t(ACTION_PLANNER_TIMEOUT_DESC_KEY),
                        },
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
                        "reason": {"type": "string", "description": t("action.tool.system_status_reason")},
                        "timeout_seconds": {
                            "type": "integer",
                            "description": t(ACTION_PLANNER_TIMEOUT_DESC_KEY),
                        },
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
                        "reason": {"type": "string", "description": t("action.tool.news_reason")},
                        "timeout_seconds": {
                            "type": "integer",
                            "description": t(ACTION_PLANNER_TIMEOUT_DESC_KEY),
                        },
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
                        "message": {
                            "type": "string",
                            "description": t("action.tool.message_body"),
                        },
                        "target": {
                            "type": "string",
                            "description": t("action.tool.message_target"),
                        },
                        "target_entity": {
                            "type": "string",
                            "description": t("action.tool.message_target_entity"),
                        },
                        "reply_to": {
                            "type": "string",
                            "description": t("action.tool.message_reply_to"),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": t(ACTION_PLANNER_TIMEOUT_DESC_KEY),
                        },
                        "reason": {
                            "type": "string",
                            "description": t("action.tool.message_reason"),
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "native_note_rewrite",
                "description": "Rewrite the private note scratchpad in full.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": t("action.tool.note_content"),
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "description": t(ACTION_PLANNER_TIMEOUT_DESC_KEY),
                        },
                        "reason": {
                            "type": "string",
                            "description": t("action.tool.note_reason"),
                        },
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
                        "reason": {
                            "type": "string",
                            "description": t("action.tool.skip_reason"),
                        },
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
) -> ActionPlan | tuple[None, str | None] | None:
    if tool_name == "ignore_action":
        reason = str(arguments.get("reason") or "").strip() or None
        return None, reason

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


def _coerce_planner_result(
    plan_result: ActionPlan | tuple[ActionPlan | None, str | None] | None,
) -> tuple[ActionPlan | None, str | None]:
    if isinstance(plan_result, tuple) and len(plan_result) == 2:
        plan, reason = plan_result
        normalized_reason = str(reason or "").strip() or None
        return plan if isinstance(plan, ActionPlan) else None, normalized_reason
    if isinstance(plan_result, ActionPlan):
        return plan_result, None
    return None, None


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
) -> ActionPlan | tuple[None, str | None] | None:
    action_type = str(raw_action_type or "").strip()
    if action_type not in THOUGHT_ACTION_TYPES:
        return None, t("action.unknown_action", action_type=action_type or t("action.empty_fallback"))
    if action_type == "time":
        return ActionPlan(
            action_type="get_time",
            executor="native",
            task=t("action.task_get_time"),
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
            task=t("action.task_get_system_status"),
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
    if action_type == "note_rewrite":
        return _native_note_rewrite_plan(
            raw_params=str((thought.action_request or {}).get("params") or ""),
            thought=thought,
            timeout_seconds=default_timeout_seconds,
            reason="fallback",
        )
    if action_type in OPENCLAW_ACTION_TYPES:
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
    news_reader: Callable[..., ActionResultEnvelope] = read_news_result,
    contact_resolver: Callable[[str], str | None] | None = None,
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
            summary_key="action.result_time",
            summary_params={"local_time": now.strftime("%Y-%m-%d %H:%M:%S %Z")},
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
            summary=t("action.send_summary", target=target_source),
            data={
                "source": target_source,
                "target_entity": target_entity,
                "message": message_text,
            },
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
            summary_key="action.send_summary",
            summary_params={"target": target_source},
        )
    if action.type == "note_rewrite":
        note_text = _normalize_note_content(action.request.get("message_text"))
        return _build_action_result(
            ok=True,
            summary=t("action.note_rewrite_summary"),
            data={
                "content": note_text,
                "length": len(note_text),
            },
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
            summary_key="action.note_rewrite_summary",
        )
    if action.type == "get_system_status":
        snapshot = collect_system_status_snapshot()
        return _build_action_result(
            ok=True,
            summary=str(snapshot.get("summary") or t("perception.system_status_default")),
            data=dict(snapshot),
            error_detail=None,
            run_id=None,
            session_key=None,
            transport="native",
            summary_key="action.result_system_status",
            summary_params={"summary": str(snapshot.get("summary") or t("perception.system_status_default"))},
        )
    raise RuntimeError(t("action.unsupported_native", action_type=action.type))


def _clamp_timeout(raw_value: JsonValue, default_timeout_seconds: int) -> int:
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
    if action_type == "web_fetch":
        return _build_web_fetch_task(raw_params, explicit_task, thought)
    if action_type == "reading":
        return _build_reading_task(raw_params, explicit_task, thought)
    if action_type == "weather":
        return _build_weather_task(raw_params, default_weather_location)
    if action_type == "file_modify":
        return _build_file_modify_task(raw_params, explicit_task, thought)
    if action_type == "system_change":
        return _build_system_change_task(raw_params, explicit_task, thought)
    if action_type == "custom":
        return _build_custom_task(explicit_task, thought)
    if explicit_task:
        return explicit_task
    return thought.content


def _build_search_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    search_query = _extract_action_first_param(raw_params, "query", "keywords", "topic")
    if search_query:
        return _search_result_contract(t("action.task_search", query=search_query))
    if explicit_task:
        return _search_result_contract(explicit_task)
    return _search_result_contract(thought.content)


def _build_web_fetch_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    url = _extract_action_first_param(raw_params, "url", "link")
    if url:
        return _web_fetch_result_contract(t("action.task_web_fetch", url=url))
    if explicit_task:
        return _web_fetch_result_contract(explicit_task)
    return _web_fetch_result_contract(thought.content)


def _build_reading_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    reading_query = _extract_action_first_param(raw_params, "query", "topic", "keywords")
    if reading_query:
        return _reading_result_contract(t("action.task_reading_query", query=reading_query))
    if explicit_task:
        return _reading_result_contract(explicit_task)
    return _reading_result_contract(
        t("action.task_reading_thought", content=thought.content)
    )


def _build_weather_task(raw_params: str, default_weather_location: str) -> str:
    location = _extract_action_param(raw_params, "location") or default_weather_location
    if location:
        return _with_openclaw_result_contract(
            t("action.task_weather_location", location=location),
            data_shape=WEATHER_RESULT_DATA_SHAPE,
            requirements=[t("action.weather_field_req")],
        )
    return _with_openclaw_result_contract(
        t("action.task_weather_default"),
        data_shape=WEATHER_RESULT_DATA_SHAPE,
        requirements=[t("action.weather_field_req")],
    )


def _build_file_modify_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    path = _extract_action_first_param(raw_params, "path", "file")
    instruction = _extract_action_first_param(raw_params, "instruction", "edit", "change")
    if path and instruction:
        return _file_modify_result_contract(t("action.task_file_modify", path=path, instruction=instruction))
    if path:
        return _file_modify_result_contract(t("action.task_file_modify_thought", path=path, content=thought.content))
    if explicit_task:
        return _file_modify_result_contract(explicit_task)
    return _file_modify_result_contract(thought.content)


def _build_system_change_task(raw_params: str, explicit_task: str, thought: Thought) -> str:
    instruction = _extract_action_first_param(raw_params, "instruction", "task", "change")
    if instruction:
        return _system_change_result_contract(t("action.task_system_change", instruction=instruction))
    if explicit_task:
        return _system_change_result_contract(explicit_task)
    return _system_change_result_contract(thought.content)


def _search_result_contract(task: str) -> str:
    return _with_openclaw_result_contract(
        task,
        data_shape=SEARCH_RESULT_DATA_SHAPE,
        requirements=[t("action.search_field_req")],
    )


def _web_fetch_result_contract(task: str) -> str:
    return _with_openclaw_result_contract(
        task,
        data_shape=WEB_FETCH_RESULT_DATA_SHAPE,
        requirements=[t("action.web_fetch_field_req")],
    )


def _reading_result_contract(task: str) -> str:
    return _with_openclaw_result_contract(
        task,
        data_shape=READING_SOURCE_RESULT_DATA_SHAPE,
        requirements=[t("action.reading_field_req")],
    )


def _file_modify_result_contract(task: str) -> str:
    return _with_openclaw_result_contract(
        task,
        data_shape=FILE_MODIFY_RESULT_DATA_SHAPE,
        requirements=[t("action.file_modify_field_req")],
    )


def _system_change_result_contract(task: str) -> str:
    return _with_openclaw_result_contract(
        task,
        data_shape=SYSTEM_CHANGE_RESULT_DATA_SHAPE,
        requirements=[
            t("action.system_change_field_req"),
            t("action.system_change_status_req"),
        ],
    )


def _build_custom_task(explicit_task: str, thought: Thought) -> str:
    task = explicit_task or thought.content
    return _with_openclaw_result_contract(
        task,
        data_shape='{"details":{}}',
        requirements=[str(prompt_block("PLANNER_RESULT_CONTRACT_PREFIX"))],
    )


def _with_openclaw_result_contract(task: str, *, data_shape: str, requirements: list[str]) -> str:
    lines = [
        task,
        "",
        str(prompt_block("PLANNER_RESULT_JSON_INSTRUCTION")),
        f'{{"ok": true, "summary": "...", "data": {data_shape}, "error": null}}',
        str(prompt_block("PLANNER_RESULT_FIELD_INSTRUCTION")),
    ]
    for requirement in requirements:
        lines.append(f"- {requirement}")
    return "\n".join(lines)


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


def _i18n_text(key: str, params: JsonObject | None = None) -> I18nTextPayload:
    return {
        "key": key,
        "params": params or {},
    }


def _result_summary_key(result: ActionResultEnvelope, summary: str) -> str:
    key = str(result.get("summary_key") or "").strip()
    if key:
        return key
    if summary.strip():
        return "action.completed_with_summary"
    return ACTION_COMPLETED_DEFAULT_KEY


def _result_summary_params(result: ActionResultEnvelope, summary: str) -> JsonObject:
    params = _json_object_or_none(result.get("summary_params"))
    if params is not None:
        return params
    if summary.strip():
        return {"summary": summary}
    return {}


def _json_object_or_none(value: JsonValue) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _build_action_result(
    *,
    ok: bool,
    summary: str,
    data: JsonObject,
    error_detail: JsonValue,
    run_id: str | None,
    session_key: str | None,
    transport: str,
    raw_text: str | None = None,
    summary_key: str = "",
    summary_params: JsonObject | None = None,
) -> ActionResultEnvelope:
    result: ActionResultEnvelope = {
        "ok": ok,
        "summary": summary,
        "data": data,
        "error": coerce_json_value(error_detail),
        "run_id": run_id,
        "session_key": session_key,
        "transport": transport,
    }
    if summary_key:
        result["summary_key"] = summary_key
        result["summary_params"] = summary_params or {}
    if raw_text is not None:
        result["raw_text"] = raw_text
    return result


def _failure_result(
    summary: str,
    error_detail: JsonValue,
    *,
    transport: str,
    summary_key: str = "",
    summary_params: JsonObject | None = None,
) -> ActionResultEnvelope:
    return _build_action_result(
        ok=False,
        summary=summary,
        data={},
        error_detail=error_detail,
        run_id=None,
        session_key=None,
        transport=transport,
        summary_key=summary_key,
        summary_params=summary_params,
    )


def _copy_action_result(
    result: ActionResultEnvelope,
    *,
    ok: bool | None = None,
    summary: str | None = None,
    data: JsonObject | None = None,
    error_detail: JsonValue | None = None,
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
        summary_key=str(result.get("summary_key") or ""),
        summary_params=_json_object_or_none(result.get("summary_params")),
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
    summary = str(result.get("summary") or t(ACTION_COMPLETED_DEFAULT_KEY))
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
        summary_key=str(result.get("summary_key") or ""),
        summary_params=_json_object_or_none(result.get("summary_params")),
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


def _stringify_json_field(value: JsonValue) -> str:
    return str(value).strip() if value is not None else ""


def _build_action_request_payload(
    *,
    task: str,
    reason: str,
    raw_action: RawActionRequest | None,
    news_feed_urls: list[str],
    worker_agent_id: str = "",
    target_source: str = "",
    target_entity: str = "",
    message_text: str = "",
    reply_to_message_id: str = "",
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
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return payload


def _action_from_plan(
    *,
    thought: Thought,
    plan: ActionPlan,
    conversation_source: str | None,
    conversation_reply_to_message_id: str | None,
) -> ActionRecord:
    implicit_target_source = ""
    if not plan.target_source and not plan.target_entity:
        implicit_target_source = str(conversation_source or "").strip()
    reply_to_message_id = plan.reply_to_message_id
    if (
        not reply_to_message_id
        and plan.action_type == "send_message"
        and not plan.target_source
        and not plan.target_entity
    ):
        reply_to_message_id = str(conversation_reply_to_message_id or "").strip()
    request_payload = _build_action_request_payload(
        task=plan.task,
        reason=plan.reason,
        raw_action=thought.action_request,
        news_feed_urls=plan.news_feed_urls,
        worker_agent_id=plan.worker_agent_id,
        target_source=plan.target_source or implicit_target_source,
        target_entity=plan.target_entity,
        message_text=plan.message_text,
        reply_to_message_id=reply_to_message_id,
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
        task=t("action.task_rss"),
        timeout_seconds=timeout_seconds,
        reason=reason,
        news_feed_urls=list(news_feed_urls),
    )


def _latest_conversation_source(stimuli: list[Stimulus]) -> str | None:
    for stimulus in reversed(stimuli):
        if stimulus.type == "conversation":
            return stimulus.source
    return None


def _latest_conversation_message_id(stimuli: list[Stimulus]) -> str | None:
    for stimulus in reversed(stimuli):
        if stimulus.type != "conversation":
            continue
        message_id = stimulus.metadata.get("telegram_message_id")
        if message_id is None:
            return None
        return str(message_id).strip() or None
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
    explicit_reply_to: str = "",
) -> ActionPlan:
    message_text = explicit_message or _build_send_message_text(raw_params, thought)
    target_source = _normalize_telegram_target(explicit_target) or _build_send_message_target(raw_params)
    target_entity = explicit_target_entity or _build_send_message_target_entity(raw_params)
    reply_to = explicit_reply_to or _extract_action_first_param(raw_params, "reply_to") or ""
    if not target_source and not target_entity:
        target_source = str(conversation_source or "").strip()
    target_label = target_source or target_entity or t("action.default_target_label")
    task = t("action.task_send_message", target=target_label, message=message_text or thought.content)
    return ActionPlan(
        action_type="send_message",
        executor="native",
        task=task,
        timeout_seconds=timeout_seconds,
        reason=reason,
        target_source=target_source,
        target_entity=target_entity,
        message_text=message_text,
        reply_to_message_id=reply_to,
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
    if tool_name == "native_note_rewrite":
        return _native_note_rewrite_plan(
            raw_params=_raw_action_params(thought),
            thought=thought,
            timeout_seconds=timeout_seconds,
            reason=reason,
            explicit_content=str(arguments.get("content") or "").strip(),
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
        explicit_reply_to=str(arguments.get("reply_to") or "").strip(),
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
) -> ActionPlan | tuple[None, str | None]:
    explicit_task = str(arguments.get("task") or "").strip()
    action_type = _delegated_action_type(arguments, thought)
    if action_type not in OPENCLAW_ACTION_TYPES and action_type not in DELEGATED_TOOL_COMPAT_ACTION_TYPES:
        return None, t("action.unsupported_delegated", action_type=action_type or t("action.empty_fallback"))
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
        task=t("action.task_get_time_delegated"),
        timeout_seconds=timeout_seconds,
        reason=reason,
    )


def _native_system_status_plan(*, timeout_seconds: int, reason: str) -> ActionPlan:
    return ActionPlan(
        action_type="get_system_status",
        executor="native",
        task=t("action.task_get_system_status_delegated"),
        timeout_seconds=timeout_seconds,
        reason=reason,
    )


def _native_note_rewrite_plan(
    *,
    raw_params: str,
    thought: Thought,
    timeout_seconds: int,
    reason: str,
    explicit_content: str = "",
) -> ActionPlan:
    content = explicit_content or _build_note_rewrite_content(raw_params, thought)
    task = t("action.task_note_rewrite", content=_note_excerpt(content))
    return ActionPlan(
        action_type="note_rewrite",
        executor="native",
        task=task,
        timeout_seconds=timeout_seconds,
        reason=reason,
        message_text=content,
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
    return _normalize_telegram_target(target or "")


def _normalize_telegram_target(target: str) -> str:
    target = target.strip()
    if not target:
        return ""
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


def _build_note_rewrite_content(raw_params: str, thought: Thought) -> str:
    explicit = _extract_action_first_param(raw_params, "content", "text", "body")
    if explicit:
        return explicit
    return _strip_action_marker(thought.content)


def _strip_action_marker(content: str) -> str:
    return ACTION_MARKER_PATTERN.sub("", content).strip()


def _send_telegram_message(
    target_source: str,
    message_text: str,
    *,
    timeout_seconds: int,
    reply_to_message_id: str = "",
) -> tuple[str | None, str]:
    started_at = time.perf_counter()
    logger.info(
        "telegram send started (target=%s, reply_to=%s, chars=%d, timeout=%ds)",
        target_source,
        reply_to_message_id or "-",
        len(message_text),
        timeout_seconds,
    )
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        _log_telegram_send_finished(started_at, target_source, "telegram_token_missing")
        return "telegram_token_missing", reply_to_message_id
    chat_id = _telegram_chat_id_from_source(target_source)
    if chat_id is None:
        _log_telegram_send_finished(started_at, target_source, "invalid_telegram_target")
        return "invalid_telegram_target", reply_to_message_id
    send_error = _send_telegram_message_with_retry_policy(
        token,
        chat_id,
        message_text,
        timeout_seconds=_telegram_request_timeout_seconds(timeout_seconds),
        target_source=target_source,
        reply_to_message_id=reply_to_message_id,
    )
    delivered_reply_to = reply_to_message_id
    if reply_to_message_id and send_error and _should_retry_without_reply_to(send_error):
        logger.warning(
            "telegram reply target missing for %s (reply_to=%s), retrying without reply",
            target_source,
            reply_to_message_id,
        )
        send_error = _send_telegram_message_with_retry_policy(
            token,
            chat_id,
            message_text,
            timeout_seconds=_telegram_request_timeout_seconds(timeout_seconds),
            target_source=target_source,
            reply_to_message_id="",
        )
        delivered_reply_to = ""
    _log_telegram_send_finished(started_at, target_source, send_error or "ok")
    return send_error, delivered_reply_to


def _send_telegram_message_with_retry_policy(
    token: str,
    chat_id: str,
    message_text: str,
    *,
    timeout_seconds: int,
    target_source: str,
    reply_to_message_id: str,
) -> str | None:
    send_error = _send_telegram_message_once(
        token,
        chat_id,
        message_text,
        timeout_seconds=timeout_seconds,
        reply_to_message_id=reply_to_message_id,
    )
    if send_error and _should_retry_transient_telegram_send_error(send_error):
        return _retry_transient_telegram_send_error(
            token,
            chat_id,
            message_text,
            timeout_seconds=timeout_seconds,
            target_source=target_source,
            initial_error=send_error,
            reply_to_message_id=reply_to_message_id,
        )
    return send_error


def _send_telegram_message_once(
    token: str,
    chat_id: str,
    message_text: str,
    *,
    timeout_seconds: int,
    reply_to_message_id: str,
) -> str | None:
    body = json.dumps(
        _telegram_send_body(chat_id, message_text, reply_to_message_id)
    ).encode("utf-8")
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
        return _telegram_http_error_detail(exc)
    except TELEGRAM_SEND_EXCEPTIONS as exc:
        return str(exc)
    if not isinstance(payload, dict) or not bool(payload.get("ok")):
        description = ""
        if isinstance(payload, dict):
            description = str(payload.get("description") or "").strip()
        return description or "telegram_send_failed"
    return None


def _telegram_send_body(chat_id: str, message_text: str, reply_to_message_id: str) -> JsonObject:
    body_dict: JsonObject = {
        "chat_id": chat_id,
        "text": message_text,
    }
    if reply_to_message_id.strip().isdigit():
        body_dict["reply_parameters"] = {"message_id": int(reply_to_message_id)}
    return body_dict


def _telegram_chat_id_from_source(source: str) -> str | None:
    if not source.startswith(TELEGRAM_SOURCE_PREFIX):
        return None
    chat_id = source.removeprefix(TELEGRAM_SOURCE_PREFIX).strip()
    if chat_id.isdigit() or (chat_id.startswith("-") and chat_id[1:].isdigit()):
        return chat_id
    return None


def _telegram_http_error_detail(exc: error.HTTPError) -> str:
    description = ""
    try:
        raw_body = exc.read()
    except TELEGRAM_SEND_EXCEPTIONS:
        raw_body = b""
    if raw_body:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            description = raw_body.decode("utf-8", errors="replace").strip()
        else:
            if isinstance(payload, dict):
                description = str(payload.get("description") or "").strip()
    if not description:
        description = str(exc.reason or exc.msg or "").strip()
    if description:
        return f"http_{exc.code}: {description}"
    return f"http_{exc.code}"


def _should_retry_without_reply_to(send_error: str) -> bool:
    return "message to be replied not found" in str(send_error or "").lower()


def _should_retry_transient_telegram_send_error(send_error: str) -> bool:
    lowered = str(send_error or "").lower()
    if not lowered:
        return False
    if lowered.startswith("http_"):
        return False
    if lowered in {"telegram_token_missing", "invalid_telegram_target", "telegram_send_failed"}:
        return False
    transient_markers = (
        "temporary failure",
        "temporarily unavailable",
        "connection reset by peer",
        "connection refused",
        "network is unreachable",
        "no route to host",
        "name or service not known",
        "nodename nor servname provided",
        "failed to establish a new connection",
    )
    return any(marker in lowered for marker in transient_markers)


def _telegram_request_timeout_seconds(timeout_seconds: int) -> int:
    return max(1, min(timeout_seconds, TELEGRAM_SEND_REQUEST_TIMEOUT_SECONDS))


def _retry_transient_telegram_send_error(
    token: str,
    chat_id: str,
    message_text: str,
    *,
    timeout_seconds: int,
    target_source: str,
    initial_error: str,
    reply_to_message_id: str,
) -> str | None:
    send_error = initial_error
    for attempt in range(1, TELEGRAM_SEND_RETRY_ATTEMPTS + 1):
        logger.warning(
            "telegram send transient failure for %s, retrying in %.1f s "
            "(attempt %d/%d, reply_to=%s, error=%s)",
            target_source,
            TELEGRAM_SEND_RETRY_DELAY_SECONDS,
            attempt,
            TELEGRAM_SEND_RETRY_ATTEMPTS,
            reply_to_message_id or "-",
            send_error,
        )
        time.sleep(TELEGRAM_SEND_RETRY_DELAY_SECONDS)
        send_error = _send_telegram_message_once(
            token,
            chat_id,
            message_text,
            timeout_seconds=_telegram_request_timeout_seconds(timeout_seconds),
            reply_to_message_id=reply_to_message_id,
        )
        if not send_error or not _should_retry_transient_telegram_send_error(send_error):
            return send_error
    return send_error


def _log_telegram_send_finished(
    started_at: float,
    target_source: str,
    status: str,
) -> None:
    logger.info(
        TELEGRAM_SEND_FINISHED_LOG,
        elapsed_ms(started_at),
        target_source,
        status,
    )


def _prepare_send_message(
    action: ActionRecord,
    *,
    contact_resolver: Callable[[str], str | None] | None = None,
) -> tuple[str, str, str, ActionResultEnvelope | None]:
    target_source = str(action.request.get("target_source") or "").strip()
    target_entity = str(action.request.get("target_entity") or "").strip()
    message_text = str(action.request.get("message_text") or "").strip()
    if not target_source and target_entity and contact_resolver:
        target_source = str(contact_resolver(target_entity) or "").strip()
    if not target_source:
        if target_entity:
            return "", target_entity, message_text, _failure_result(
                t("action.unresolved_entity", entity=target_entity),
                "unresolved_target_entity",
                transport="native",
                summary_key="action.unresolved_entity",
                summary_params={"entity": target_entity},
            )
        return "", target_entity, message_text, _failure_result(
            t("action.missing_target"),
            "missing_target",
            transport="native",
            summary_key="action.missing_target",
        )
    if not target_source.startswith(TELEGRAM_SOURCE_PREFIX):
        return target_source, target_entity, message_text, _failure_result(
            t("action.unsupported_target"),
            "unsupported_target",
            transport="native",
            summary_key="action.unsupported_target",
        )
    if not message_text:
        return target_source, target_entity, message_text, _failure_result(
            t("action.missing_content"),
            "missing_message",
            transport="native",
            summary_key="action.missing_content",
        )
    return target_source, target_entity, message_text, None


def _coerce_news_feed_urls(value: JsonValue) -> list[str]:
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
) -> PerceptionStimulusPayload:
    stimulus_type = _infer_stimulus_type(action, status, result)
    return {
        "type": stimulus_type,
        "priority": _stimulus_priority(stimulus_type, result),
        "source": f"action:{action.action_id}",
        "content": _stimulus_content(stimulus_type, action, status, result),
        "metadata": {
            "origin": "action",
            "action_type": action.type,
            "status": status,
            "executor": action.executor,
            "result": _action_result_to_json_object(result),
        },
    }


def _stimulus_from_payload(action_id: str, payload: PerceptionStimulusPayload) -> Stimulus:
    return Stimulus(
        stimulus_id=f"stim_{action_id}",
        type=payload["type"],
        priority=payload["priority"],
        source=payload["source"],
        content=payload["content"],
        action_id=action_id,
        metadata=dict(payload["metadata"]),
        timestamp=datetime.now(timezone.utc),
    )


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
    summary = str(result.get("summary") or t(ACTION_COMPLETED_DEFAULT_KEY))
    if action.type == "send_message":
        return _send_message_stimulus_content(summary, result.get("data"), _action_result_succeeded(status, result))
    if _action_result_succeeded(status, result) and action.type == "search":
        return _search_stimulus_content(summary, result.get("data"))
    if _action_result_succeeded(status, result) and action.type == "note_rewrite":
        return summary
    if _action_result_succeeded(status, result) and action.type in {"reading", "web_fetch"}:
        return _reading_stimulus_content(action, summary, result.get("data"))
    if _action_result_succeeded(status, result) and action.type == "news":
        return _news_stimulus_content(summary, result.get("data"))
    if stimulus_type == "action_result":
        return f"{action.type} {status}: {summary}"
    return summary


def _action_result_succeeded(status: str, result: ActionResultEnvelope) -> bool:
    return status == "succeeded" and bool(result.get("ok", True))


def _should_emit_prompt_echo_directly(
    action: ActionRecord,
    status: str,
    result: ActionResultEnvelope,
) -> bool:
    return action.type == "note_rewrite" and _action_result_succeeded(status, result)


def _search_stimulus_content(summary: str, data: JsonValue) -> str:
    if not isinstance(data, dict):
        return summary
    raw_results = data.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        return summary
    parts = [summary]
    for index, item in enumerate(raw_results[:SEARCH_STIMULUS_MAX_RESULTS], start=1):
        entry = _search_stimulus_entry(index, item)
        if entry:
            parts.append(entry)
    return "\n".join(parts)


def _search_stimulus_entry(index: int, item: JsonValue) -> str:
    if not isinstance(item, dict):
        return ""
    title, _ = _clip_prompt_text(
        str(item.get("title") or "").strip(),
        SEARCH_STIMULUS_TITLE_MAX_CHARS,
    )
    url, _ = _clip_prompt_text(
        str(item.get("url") or "").strip(),
        SEARCH_STIMULUS_URL_MAX_CHARS,
    )
    snippet, _ = _clip_prompt_text(
        str(item.get("snippet") or "").strip(),
        SEARCH_STIMULUS_SNIPPET_MAX_CHARS,
    )
    head = title or url
    if not head:
        return ""
    entry = f"{index}. {head}"
    if title and url:
        entry += f" ({url})"
    if snippet:
        entry += f" —— {snippet}"
    return entry


def _send_message_success_summary(target_source: str, message_text: str) -> str:
    excerpt, _ = _clip_prompt_text(message_text.strip(), SEND_MESSAGE_SUMMARY_MAX_CHARS)
    if excerpt:
        return t("action.send_success_with_excerpt", target=target_source, excerpt=excerpt)
    return t("action.send_success", target=target_source)


def _send_message_success_summary_key(message_text: str) -> str:
    excerpt, _ = _clip_prompt_text(message_text.strip(), SEND_MESSAGE_SUMMARY_MAX_CHARS)
    if excerpt:
        return "action.send_success_with_excerpt"
    return "action.send_success"


def _send_message_success_summary_params(target_source: str, message_text: str) -> JsonObject:
    excerpt, _ = _clip_prompt_text(message_text.strip(), SEND_MESSAGE_SUMMARY_MAX_CHARS)
    params: JsonObject = {"target": target_source}
    if excerpt:
        params["excerpt"] = excerpt
    return params


def _send_message_result_data(
    *,
    target_source: str,
    target_entity: str,
    message_text: str,
) -> JsonObject:
    data: JsonObject = {}
    if target_source:
        data["source"] = target_source
    if target_entity:
        data["target_entity"] = target_entity
    if message_text:
        data["message"] = message_text
    return data


def _conversation_target_name(
    redis_client: ActionRedisLike | None,
    target_source: str,
) -> str:
    source = target_source.strip()
    if not source:
        return ""
    try:
        history = load_conversation_history(redis_client, limit=200)
    except ACTION_REDIS_EXCEPTIONS as exc:
        logger.warning("failed to load conversation history for reply target name: %s", exc)
        return _target_name_from_source(source)
    for entry in reversed(history):
        if str(entry.get("source") or "").strip() != source:
            continue
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        full_name = str(metadata.get("telegram_full_name") or "").strip()
        username = str(metadata.get("telegram_username") or "").strip()
        target_name = full_name or username
        if target_name:
            return target_name
    return _target_name_from_source(source)


def _target_name_from_source(source: str) -> str:
    if source.startswith(TELEGRAM_SOURCE_PREFIX):
        return source.removeprefix(TELEGRAM_SOURCE_PREFIX)
    return source


def _send_message_failure_result(
    *,
    target_source: str,
    target_entity: str,
    message_text: str,
    summary: str,
    error_detail: JsonValue,
    summary_key: str = "",
    summary_params: JsonObject | None = None,
) -> ActionResultEnvelope:
    return _build_action_result(
        ok=False,
        summary=summary,
        data=_send_message_result_data(
            target_source=target_source,
            target_entity=target_entity,
            message_text=message_text,
        ),
        error_detail=error_detail,
        run_id=None,
        session_key=None,
        transport="native",
        summary_key=summary_key,
        summary_params=summary_params,
    )


def _send_message_stimulus_content(summary: str, data: JsonValue, succeeded: bool) -> str:
    if not isinstance(data, dict):
        return summary
    target = str(data.get("source") or data.get("target_entity") or "").strip()
    message = str(data.get("message") or "").strip()
    excerpt, _ = _clip_prompt_text(message, SEND_MESSAGE_SUMMARY_MAX_CHARS)
    if succeeded:
        return summary
    if target and excerpt:
        return t("action.send_fail_target_excerpt", target=target, excerpt=excerpt, summary=summary)
    if excerpt:
        return t("action.send_fail_excerpt", excerpt=excerpt, summary=summary)
    if target:
        return t("action.send_fail_target", target=target, summary=summary)
    return summary


READING_STIMULUS_INTENT_MAX_CHARS = 120


def _reading_stimulus_content(action: ActionRecord, summary: str, data: JsonValue) -> str:
    if not isinstance(data, dict):
        return summary
    excerpt, _ = _clip_prompt_text(
        str(data.get("excerpt_original") or data.get("excerpt") or "").strip(),
        READING_STIMULUS_EXCERPT_MAX_CHARS,
    )
    parts: list[str] = []
    intent_line = _reading_stimulus_intent_line(action)
    if intent_line:
        parts.append(intent_line)
    source_line = _reading_source_line(data)
    if source_line:
        parts.append(source_line)
    if excerpt:
        parts.append(t("action.result_original", excerpt=excerpt))
    elif summary.strip():
        parts.append(t("action.result_summary", summary=summary.strip()))
    if not parts:
        return summary
    return "\n".join(parts)


def _reading_stimulus_intent_line(action: ActionRecord) -> str:
    if action.type == "reading":
        focus = _reading_request_focus(action.request)
        if focus:
            clipped, _ = _clip_prompt_text(focus, READING_STIMULUS_INTENT_MAX_CHARS)
            return t("action.reading_intent_focus", focus=clipped)
        return t("action.reading_intent_default")
    if action.type == "web_fetch":
        url = _web_fetch_request_url(action.request)
        if url:
            clipped, _ = _clip_prompt_text(url, READING_STIMULUS_INTENT_MAX_CHARS)
            return t("action.web_fetch_intent_url", url=clipped)
        return t("action.web_fetch_intent_default")
    return ""


def _reading_request_focus(request_payload: ActionRequestPayload) -> str:
    raw_params = _request_raw_params(request_payload)
    focus = _extract_action_first_param(raw_params, "query", "topic", "keywords")
    if focus:
        return _compact_prompt_text(focus)
    task = str(request_payload.get("task") or "").strip()
    prefix = t("action.reading_focus_prefix")
    suffix = t("action.reading_focus_suffix")
    if task.startswith(prefix):
        start = len(prefix)
        end = task.find(suffix, start)
        if end > start:
            return _compact_prompt_text(task[start:end])
    return ""


def _web_fetch_request_url(request_payload: ActionRequestPayload) -> str:
    raw_params = _request_raw_params(request_payload)
    url = _extract_action_first_param(raw_params, "url", "link")
    if url:
        return _compact_prompt_text(url)
    task = str(request_payload.get("task") or "").strip()
    fallback_url = _first_url_in_text(task)
    if fallback_url:
        return _compact_prompt_text(fallback_url)
    return ""


def _request_raw_params(request_payload: ActionRequestPayload) -> str:
    raw_action = request_payload.get("raw_action")
    if not isinstance(raw_action, dict):
        return ""
    return str(raw_action.get("params") or "").strip()


def _first_url_in_text(text: str) -> str:
    match = re.search(r"https?://[^\s\u3002\uff0c\uff1b\uff1a\uff09\uff08]+", text)
    if not match:
        return ""
    return match.group(0).rstrip(".,;:!?)]}>\u3002\uff0c\uff1b\uff1a")


NEWS_STIMULUS_MAX_ITEMS = 5
NEWS_STIMULUS_SUMMARY_MAX_CHARS = 200


def _news_stimulus_content(summary: str, data: JsonValue) -> str:
    if not isinstance(data, dict):
        return summary
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return summary
    parts = [summary]
    shown_count = 0
    displayable_count = 0
    for item in items:
        entry = _news_stimulus_entry(item)
        if not entry:
            continue
        displayable_count += 1
        if shown_count >= NEWS_STIMULUS_MAX_ITEMS:
            continue
        parts.append(entry)
        shown_count += 1
    remaining = displayable_count - shown_count
    if remaining > 0:
        parts.append(t("action.result_remaining", count=remaining))
    return "\n".join(parts)


def _reading_source_title_and_url(data: dict) -> tuple[str, str]:
    source_info = data.get("source")
    if isinstance(source_info, dict):
        return (
            str(source_info.get("title") or "").strip(),
            str(source_info.get("url") or "").strip(),
        )
    return (
        str(data.get("title") or "").strip(),
        str(data.get("url") or "").strip(),
    )


def _reading_source_line(data: dict) -> str:
    title, url = _reading_source_title_and_url(data)
    if title and url:
        return t("action.result_source_title_url", title=title, url=url)
    if title:
        return t("action.result_source_title", title=title)
    if url:
        return t("action.result_source_url", url=url)
    return ""


def _news_stimulus_entry(item: JsonValue) -> str:
    if not isinstance(item, dict):
        return ""
    title, item_summary, link = _news_item_headline_parts(item)
    headline = title or item_summary or link
    if not headline:
        return ""
    entry = f"- {headline}"
    if title and item_summary:
        entry += f"\n  {item_summary}"
    return entry


def _news_item_headline_parts(item: dict) -> tuple[str, str, str]:
    title, _ = _clip_prompt_text(
        str(item.get("title") or "").strip(),
        NEWS_STIMULUS_SUMMARY_MAX_CHARS,
    )
    item_summary, _ = _clip_prompt_text(
        str(item.get("summary") or "").strip(),
        NEWS_STIMULUS_SUMMARY_MAX_CHARS,
    )
    link, _ = _clip_prompt_text(
        str(item.get("link") or "").strip(),
        NEWS_STIMULUS_SUMMARY_MAX_CHARS,
    )
    return title, item_summary, link


def _clip_prompt_text(text: str, limit: int) -> tuple[str, bool]:
    if not text or limit <= 0:
        return "", False
    if len(text) <= limit:
        return text, False
    clipped = text[:limit].rstrip()
    return f"{clipped}...", True


def _compact_prompt_text(text: str) -> str:
    return " ".join(text.split())


def _normalize_note_content(value: JsonValue | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    text = str(value or "").strip()
    clipped, _ = _clip_prompt_text(text, NOTE_MAX_CHARS)
    return clipped


def _note_excerpt(content: str) -> str:
    excerpt, _ = _clip_prompt_text(_compact_prompt_text(content), 80)
    return excerpt or t("action.result_empty")


def push_action_control(
    redis_client: ActionRedisLike | None,
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


def load_action_items(redis_client: ActionRedisLike | None) -> list[JsonObject]:
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
        item_object = _coerce_json_object(item)
        if not item_object:
            logger.warning("skipping non-object action record")
            continue
        items.append(item_object)
    return items


def pop_action_controls(redis_client: ActionRedisLike | None, limit: int = 20) -> list[ActionControl]:
    if redis_client is None or limit <= 0:
        return []
    controls = []
    for _ in range(limit):
        try:
            raw = _pop_next_action_control_payload(redis_client)
        except ACTION_REDIS_EXCEPTIONS:
            return controls
        if raw is None:
            break
        parsed_control = _parse_action_control_payload(raw)
        if parsed_control is not None:
            controls.append(parsed_control)
    return controls


def _pop_next_action_control_payload(redis_client: ActionRedisLike) -> str | None:
    raw_items = redis_client.lrange(ACTION_CONTROL_KEY, 0, 0)
    if not raw_items:
        return None
    redis_client.ltrim(ACTION_CONTROL_KEY, 1, -1)
    return raw_items[0]


def _parse_action_control_payload(raw: str) -> ActionControl | None:
    try:
        control = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return _coerce_action_control(control)


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
                t("action.send_status_unknown"),
                "delivery_status_unknown",
                transport=executor,
                summary_key="action.send_status_unknown",
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
    submitted_at = _stringify_json_field(value.get("submitted_at"))
    if submitted_at:
        payload["submitted_at"] = submitted_at
    status = _stringify_json_field(value.get("status"))
    if status:
        payload["status"] = status
    target_source = _stringify_json_field(value.get("target_source"))
    if target_source:
        payload["target_source"] = target_source
    target_entity = _stringify_json_field(value.get("target_entity"))
    if target_entity:
        payload["target_entity"] = target_entity
    message_text = _stringify_json_field(value.get("message_text"))
    if message_text:
        payload["message_text"] = message_text
    reply_to = _stringify_json_field(value.get("reply_to_message_id"))
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return payload


def _request_payload_with_recent_metadata(
    request_payload: ActionRequestPayload,
    *,
    submitted_at: datetime,
    status: str,
) -> ActionRequestPayload:
    payload = _clone_action_request_payload(request_payload)
    payload["submitted_at"] = submitted_at.isoformat()
    if status:
        payload["status"] = status
    return payload


def _clone_action_request_payload(request_payload: ActionRequestPayload) -> ActionRequestPayload:
    payload: ActionRequestPayload = {
        "task": request_payload["task"],
        "reason": request_payload["reason"],
        "raw_action": request_payload["raw_action"],
    }
    news_feed_urls = request_payload.get("news_feed_urls")
    if news_feed_urls:
        payload["news_feed_urls"] = list(news_feed_urls)
    worker_agent_id = request_payload.get("worker_agent_id")
    if worker_agent_id:
        payload["worker_agent_id"] = worker_agent_id
    submitted_at = request_payload.get("submitted_at")
    if submitted_at:
        payload["submitted_at"] = submitted_at
    status = request_payload.get("status")
    if status:
        payload["status"] = status
    target_source = request_payload.get("target_source")
    if target_source:
        payload["target_source"] = target_source
    target_entity = request_payload.get("target_entity")
    if target_entity:
        payload["target_entity"] = target_entity
    message_text = request_payload.get("message_text")
    if message_text:
        payload["message_text"] = message_text
    reply_to_message_id = request_payload.get("reply_to_message_id")
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return payload


def _coerce_restored_action_result(value: JsonValue) -> ActionResultEnvelope | None:
    if not isinstance(value, dict):
        return None
    restored_data = _coerce_json_object(value.get("data"))
    restored: ActionResultEnvelope = {
        "ok": bool(value.get("ok", False)),
        "summary": _stringify_json_field(value.get("summary")) or "",
        "data": restored_data,
        "error": coerce_json_value(value.get("error")),
        "run_id": _stringify_json_field(value.get("run_id")) or None,
        "session_key": _stringify_json_field(value.get("session_key")) or None,
        "transport": _stringify_json_field(value.get("transport")) or "",
    }
    raw_text = value.get("raw_text")
    if isinstance(raw_text, str):
        restored["raw_text"] = raw_text
    summary_key = _stringify_json_field(value.get("summary_key"))
    if summary_key:
        restored["summary_key"] = summary_key
        restored["summary_params"] = _coerce_json_object(value.get("summary_params"))
    return restored


def _coerce_raw_action_request(value: JsonValue) -> RawActionRequest | None:
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


def _coerce_action_control(value: JsonValue) -> ActionControl | None:
    if not isinstance(value, dict):
        return None
    action_id = _stringify_json_field(value.get("action_id"))
    actor = _stringify_json_field(value.get("actor"))
    note = _stringify_json_field(value.get("note"))
    timestamp = _stringify_json_field(value.get("timestamp"))
    approved = value.get("approved")
    if (
        not action_id
        or not actor
        or note is None
        or not timestamp
        or not isinstance(approved, bool)
    ):
        return None
    control: ActionControl = {
        "action_id": action_id,
        "approved": approved,
        "actor": actor,
        "note": note,
        "timestamp": timestamp,
    }
    return control


def _parse_action_datetime(value: JsonValue) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _action_result_to_json_object(result: ActionResultEnvelope) -> JsonObject:
    payload: JsonObject = {}
    for key, value in result.items():
        payload[key] = coerce_json_value(value)
    return payload


def _thought_cycle_id(thought_id: str) -> int | None:
    match = THOUGHT_CYCLE_ID_PATTERN.match(thought_id.strip())
    if not match:
        return None
    return int(match.group("cycle_id"))


def _coerce_json_object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return {str(key): coerce_json_value(item) for key, item in value.items()}


def _redis_zadd(redis_client: ActionRedisLike, key: str, mapping: dict[str, float]) -> int:
    # redis-py supports mapping-based ZADD, but the bundled IDE stub still models
    # the legacy score/member signature.
    # noinspection PyArgumentList
    return int(redis_client.zadd(key, mapping))


def _redis_zadd_nx(redis_client: ActionRedisLike, key: str, mapping: dict[str, float]) -> int:
    # redis-py supports mapping-based ZADD with NX, but the bundled IDE stub still
    # models the legacy score/member signature.
    # noinspection PyArgumentList
    return int(redis_client.zadd(key, mapping, nx=True))
