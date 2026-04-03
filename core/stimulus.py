"""Stimulus queue for external events and action results."""

import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Protocol, TypeGuard
from redis import exceptions as redis_exceptions
from uuid import uuid4

from core.common_types import (
    ConversationEntry,
    JsonObject,
    JsonValue,
    RecentActionEchoRecord,
    RecentConversationMessage,
    RecentConversationPrompt,
    StimulusRecord,
    elapsed_ms,
)

REDIS_KEY = "seedwake:stimuli"
CONVERSATION_HISTORY_KEY = "seedwake:conversation_history"
CONVERSATION_HISTORY_LIMIT = 500
ACTION_RESULT_HISTORY_KEY = "seedwake:action_result_history"
ACTION_RESULT_HISTORY_LIMIT = 500
CONVERSATION_SUMMARY_KEY = "seedwake:conversation_summaries"
RECENT_ACTION_ECHO_KEY = "seedwake:recent_action_echoes"
RECENT_ACTION_ECHO_LIMIT = 100
RECENT_ACTION_ECHO_RETAIN_CYCLES = 8
RECENT_ACTION_ECHO_RETAIN_SECONDS = 5400
RECENT_CONVERSATION_RAW_LIMIT = 10
RECENT_CONVERSATION_WINDOW_HOURS = 24
RECENT_CONVERSATION_SUMMARY_VERSION = 2
RECENT_CONVERSATION_SUMMARY_MAX_CHARS = 500
RECENT_ACTION_ECHO_ACTION_TYPES = {"news", "search", "reading", "web_fetch", "weather", "send_message"}
MERGED_CONVERSATION_HISTORY_METADATA_KEYS = (
    "telegram_user_id",
    "telegram_chat_id",
    "telegram_username",
    "telegram_full_name",
    "telegram_message_id",
    "reply_to_message_id",
    "reply_to_preview",
    "reply_to_user_id",
    "reply_to_from_self",
    "reply_to_username",
    "reply_to_full_name",
)
STIMULUS_REDIS_EXCEPTIONS = (
    redis_exceptions.RedisError,
    ConnectionError,
    TimeoutError,
    OSError,
    RuntimeError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)
SLOW_REDIS_OPERATION_THRESHOLD_MS = 10.0
logger = logging.getLogger(__name__)


class ConversationRedisLike(Protocol):
    def rpush(self, key: str, payload: str) -> int: ...
    def lpush(self, key: str, *values: str) -> int: ...
    def lrange(self, key: str, start: int, end: int) -> list[str]: ...
    def ltrim(self, key: str, start: int, end: int) -> bool: ...
    def lrem(self, key: str, count: int, value: str) -> int: ...
    def hset(self, key: str, hash_field: str, value: str) -> int: ...
    def hgetall(self, key: str) -> dict[str, str]: ...


@dataclass
class Stimulus:
    stimulus_id: str
    type: str
    priority: int
    source: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    action_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)


class StimulusQueue:
    """Priority queue backed by Redis List with in-memory fallback."""

    def __init__(self, redis_client: ConversationRedisLike | None) -> None:
        self._redis = redis_client
        self._deque: deque[Stimulus] = deque()
        self._lock = RLock()

    def push(
        self,
        stimulus_type: str,
        priority: int,
        source: str,
        content: str,
        *,
        action_id: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Stimulus:
        stimulus = Stimulus(
            stimulus_id=f"stim_{uuid4().hex}",
            type=stimulus_type,
            priority=priority,
            source=source,
            content=content,
            action_id=action_id,
            metadata=metadata or {},
        )
        with self._lock:
            self._deque.append(stimulus)
            redis_client = self._redis
        if redis_client:
            try:
                queue_started_at = time.perf_counter()
                redis_client.rpush(REDIS_KEY, _stimulus_to_json(stimulus))
                _log_redis_operation("rpush", queue_started_at, f"key={REDIS_KEY}, count=1")
                if stimulus_type == "conversation":
                    append_conversation_history(
                        redis_client,
                        role="user",
                        source=source,
                        content=content,
                        stimulus_id=stimulus.stimulus_id,
                        metadata=stimulus.metadata,
                        timestamp=stimulus.timestamp,
                    )
            except STIMULUS_REDIS_EXCEPTIONS:
                with self._lock:
                    self._redis = None
        return stimulus

    def pop_many(self, limit: int = 2) -> list[Stimulus]:
        if limit <= 0:
            return []
        with self._lock:
            redis_client = self._redis
        if redis_client:
            try:
                return self._redis_pop_many(redis_client, limit)
            except STIMULUS_REDIS_EXCEPTIONS:
                with self._lock:
                    self._redis = None
        return self._shadow_pop_many(limit)

    def pop_all(self) -> list[Stimulus]:
        with self._lock:
            redis_client = self._redis
        if redis_client:
            try:
                return self._redis_pop_all(redis_client)
            except STIMULUS_REDIS_EXCEPTIONS:
                with self._lock:
                    self._redis = None
        return self._shadow_pop_all()

    def requeue_front(self, stimuli: list[Stimulus]) -> None:
        if not stimuli:
            return
        with self._lock:
            for stimulus in reversed(stimuli):
                self._deque.appendleft(stimulus)
            redis_client = self._redis
        if redis_client:
            try:
                payloads = [_stimulus_to_json(stimulus) for stimulus in reversed(stimuli)]
                redis_client.lpush(REDIS_KEY, *payloads)
            except STIMULUS_REDIS_EXCEPTIONS:
                with self._lock:
                    self._redis = None

    @property
    def redis_available(self) -> bool:
        with self._lock:
            return self._redis is not None

    def attach_redis(self, redis_client: ConversationRedisLike | None) -> bool:
        with self._lock:
            self._redis = redis_client
        try:
            self._sync_to_redis()
        except STIMULUS_REDIS_EXCEPTIONS:
            with self._lock:
                self._redis = None
        return self.redis_available

    def _redis_pop_many(self, redis_client: ConversationRedisLike, limit: int) -> list[Stimulus]:
        return self._redis_select_and_remove(redis_client, limit)

    def _shadow_pop_many(self, limit: int) -> list[Stimulus]:
        with self._lock:
            items = list(self._deque)
        chosen_pairs = _select_ranked(items, limit)
        chosen_items = [items[index] for index, _ in chosen_pairs]
        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _redis_pop_all(self, redis_client: ConversationRedisLike) -> list[Stimulus]:
        return self._redis_select_and_remove(redis_client, limit=None)

    def _redis_select_and_remove(
        self,
        redis_client: ConversationRedisLike,
        limit: int | None,
    ) -> list[Stimulus]:
        range_started_at = time.perf_counter()
        raw_items = redis_client.lrange(REDIS_KEY, 0, -1)
        _log_redis_operation("lrange", range_started_at, f"key={REDIS_KEY}, count={len(raw_items)}")
        if not raw_items:
            return []
        parsed = [_stimulus_from_dict(json.loads(item)) for item in raw_items]
        chosen_pairs = _select_ranked(parsed, limit if limit is not None else len(parsed))
        chosen_items = [parsed[index] for index, _ in chosen_pairs]
        chosen_indices = {index for index, _ in chosen_pairs}
        for index in sorted(chosen_indices, reverse=True):
            remove_started_at = time.perf_counter()
            redis_client.lrem(REDIS_KEY, 1, raw_items[index])
            _log_redis_operation("lrem", remove_started_at, f"key={REDIS_KEY}, count=1")
        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _shadow_pop_all(self) -> list[Stimulus]:
        with self._lock:
            items = list(self._deque)
        chosen_pairs = _select_ranked(items, len(items))
        chosen_items = [items[index] for index, _ in chosen_pairs]
        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _drop_shadow_items(self, stimulus_ids: list[str]) -> None:
        if not stimulus_ids:
            return
        id_set = set(stimulus_ids)
        with self._lock:
            self._deque = deque(
                stimulus for stimulus in self._deque
                if stimulus.stimulus_id not in id_set
            )

    def _sync_to_redis(self) -> None:
        with self._lock:
            redis_client = self._redis
            shadow_items = list(self._deque)
        if redis_client is None:
            return
        range_started_at = time.perf_counter()
        existing = redis_client.lrange(REDIS_KEY, 0, -1)
        _log_redis_operation("lrange", range_started_at, f"key={REDIS_KEY}, count={len(existing)}")
        existing_ids = {
            json.loads(item)["stimulus_id"]
            for item in existing
        }
        history_ids = _conversation_history_stimulus_ids(redis_client)
        for stimulus in shadow_items:
            if stimulus.stimulus_id in existing_ids:
                continue
            push_started_at = time.perf_counter()
            redis_client.rpush(REDIS_KEY, _stimulus_to_json(stimulus))
            _log_redis_operation("rpush", push_started_at, f"key={REDIS_KEY}, count=1")
            if stimulus.type == "conversation":
                _sync_conversation_history(redis_client, stimulus, history_ids)


def append_conversation_history(
    redis_client: ConversationRedisLike | None,
    *,
    role: str,
    source: str,
    content: str,
    stimulus_id: str | None = None,
    metadata: JsonObject | None = None,
    timestamp: datetime | None = None,
) -> ConversationEntry:
    entry: ConversationEntry = {
        "entry_id": f"conv_{uuid4().hex}",
        "role": role,
        "source": source,
        "content": content,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
        "stimulus_id": stimulus_id,
        "metadata": metadata or {},
    }
    if redis_client is not None:
        push_started_at = time.perf_counter()
        redis_client.rpush(CONVERSATION_HISTORY_KEY, json.dumps(entry, ensure_ascii=False))
        _log_redis_operation("rpush", push_started_at, f"key={CONVERSATION_HISTORY_KEY}, count=1")
        trim_started_at = time.perf_counter()
        redis_client.ltrim(CONVERSATION_HISTORY_KEY, -CONVERSATION_HISTORY_LIMIT, -1)
        _log_redis_operation(
            "ltrim",
            trim_started_at,
            f"key={CONVERSATION_HISTORY_KEY}, limit={CONVERSATION_HISTORY_LIMIT}",
        )
    return entry


def append_action_result_history(
    redis_client: ConversationRedisLike | None,
    stimulus: Stimulus,
) -> None:
    if redis_client is None:
        return
    push_started_at = time.perf_counter()
    redis_client.rpush(ACTION_RESULT_HISTORY_KEY, _stimulus_to_json(stimulus))
    _log_redis_operation("rpush", push_started_at, f"key={ACTION_RESULT_HISTORY_KEY}, count=1")
    trim_started_at = time.perf_counter()
    redis_client.ltrim(ACTION_RESULT_HISTORY_KEY, -ACTION_RESULT_HISTORY_LIMIT, -1)
    _log_redis_operation(
        "ltrim",
        trim_started_at,
        f"key={ACTION_RESULT_HISTORY_KEY}, limit={ACTION_RESULT_HISTORY_LIMIT}",
    )


def load_action_result_history(
    redis_client: ConversationRedisLike | None,
    limit: int = ACTION_RESULT_HISTORY_LIMIT,
) -> list[Stimulus]:
    if redis_client is None or limit <= 0:
        return []
    started_at = time.perf_counter()
    raw_items = redis_client.lrange(ACTION_RESULT_HISTORY_KEY, -limit, -1)
    _log_redis_operation("lrange", started_at, f"key={ACTION_RESULT_HISTORY_KEY}, count={len(raw_items)}")
    stimuli: list[Stimulus] = []
    for raw_item in raw_items:
        try:
            payload = json.loads(raw_item)
        except (TypeError, ValueError) as exc:
            logger.warning("skipping malformed action result history record: %s", exc)
            continue
        if not isinstance(payload, dict):
            logger.warning("skipping non-object action result history record")
            continue
        try:
            stimuli.append(_stimulus_from_dict(payload))  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("skipping invalid action result history record: %s", exc)
    return stimuli


def forget_action_result_history_ids(
    redis_client: ConversationRedisLike | None,
    stimulus_ids: list[str],
) -> None:
    if redis_client is None or not stimulus_ids:
        return
    id_set = {stimulus_id for stimulus_id in stimulus_ids if stimulus_id}
    if not id_set:
        return
    started_at = time.perf_counter()
    raw_items = redis_client.lrange(ACTION_RESULT_HISTORY_KEY, 0, -1)
    _log_redis_operation("lrange", started_at, f"key={ACTION_RESULT_HISTORY_KEY}, count={len(raw_items)}")
    for raw_item in raw_items:
        try:
            payload = json.loads(raw_item)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        stimulus_id = str(payload.get("stimulus_id") or "").strip()
        if stimulus_id not in id_set:
            continue
        remove_started_at = time.perf_counter()
        redis_client.lrem(ACTION_RESULT_HISTORY_KEY, 1, raw_item)
        _log_redis_operation("lrem", remove_started_at, f"key={ACTION_RESULT_HISTORY_KEY}, count=1")


def _sync_conversation_history(
    redis_client: ConversationRedisLike,
    stimulus: Stimulus,
    existing_history_ids: set[str],
) -> None:
    merged_messages = stimulus.metadata.get("merged_messages")
    merged_ids = stimulus.metadata.get("merged_stimulus_ids")
    if _has_complete_merged_history_payload(merged_messages, merged_ids):
        assert isinstance(merged_ids, list)
        _sync_merged_conversation_history(
            redis_client,
            stimulus,
            existing_history_ids,
            merged_messages,
            merged_ids,
        )
        return
    _sync_single_conversation_history(redis_client, stimulus, existing_history_ids)


def _has_complete_merged_history_payload(
    merged_messages: JsonValue,
    merged_ids: JsonValue,
) -> TypeGuard[list[JsonValue]]:
    return (
        isinstance(merged_messages, list)
        and isinstance(merged_ids, list)
        and len(merged_messages) == len(merged_ids)
    )


def _sync_merged_conversation_history(
    redis_client: ConversationRedisLike,
    stimulus: Stimulus,
    existing_history_ids: set[str],
    merged_messages: list[JsonValue],
    merged_ids: list[JsonValue],
) -> None:
    for index, message in enumerate(merged_messages):
        history_stimulus_id = str(merged_ids[index] or "").strip()
        if history_stimulus_id and history_stimulus_id in existing_history_ids:
            continue
        _append_merged_conversation_history_entry(
            redis_client,
            stimulus,
            message,
            history_stimulus_id or None,
        )
        if history_stimulus_id:
            existing_history_ids.add(history_stimulus_id)


def _sync_single_conversation_history(
    redis_client: ConversationRedisLike,
    stimulus: Stimulus,
    existing_history_ids: set[str],
) -> None:
    history_stimulus_id = str(stimulus.stimulus_id or "").strip()
    if history_stimulus_id and history_stimulus_id in existing_history_ids:
        return
    append_conversation_history(
        redis_client,
        role="user",
        source=stimulus.source,
        content=stimulus.content,
        stimulus_id=stimulus.stimulus_id,
        metadata=stimulus.metadata,
        timestamp=stimulus.timestamp,
    )
    if history_stimulus_id:
        existing_history_ids.add(history_stimulus_id)


def _append_merged_conversation_history_entry(
    redis_client: ConversationRedisLike,
    stimulus: Stimulus,
    message: JsonValue,
    stimulus_id: str | None,
) -> None:
    if not isinstance(message, dict):
        append_conversation_history(
            redis_client,
            role="user",
            source=stimulus.source,
            content=str(message or ""),
            stimulus_id=stimulus_id,
            metadata={},
            timestamp=stimulus.timestamp,
        )
        return
    append_conversation_history(
        redis_client,
        role="user",
        source=str(message.get("source") or stimulus.source),
        content=str(message.get("content") or ""),
        stimulus_id=stimulus_id,
        metadata=_conversation_history_metadata_from_merged_message(message),
        timestamp=_conversation_history_timestamp_from_merged_message(message, stimulus.timestamp),
    )


def _conversation_history_metadata_from_merged_message(message: dict) -> JsonObject:
    metadata: JsonObject = {}
    for key in MERGED_CONVERSATION_HISTORY_METADATA_KEYS:
        if key in message:
            metadata[key] = message[key]
    return metadata


def _conversation_history_timestamp_from_merged_message(
    message: dict,
    fallback: datetime,
) -> datetime:
    raw_timestamp = str(message.get("timestamp") or "").strip()
    if not raw_timestamp:
        return fallback
    try:
        return datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return fallback


def _conversation_history_stimulus_ids(redis_client: ConversationRedisLike) -> set[str]:
    history = load_conversation_history(redis_client, limit=CONVERSATION_HISTORY_LIMIT)
    return {
        stimulus_id
        for stimulus_id in (
            str(entry.get("stimulus_id") or "").strip()
            for entry in history
        )
        if stimulus_id
    }


def load_conversation_history(
    redis_client: ConversationRedisLike | None,
    limit: int = 100,
) -> list[ConversationEntry]:
    if redis_client is None or limit <= 0:
        return []
    range_started_at = time.perf_counter()
    raw_items = redis_client.lrange(CONVERSATION_HISTORY_KEY, -limit, -1)
    _log_redis_operation("lrange", range_started_at, f"key={CONVERSATION_HISTORY_KEY}, count={len(raw_items)}")
    items = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("skipping malformed conversation history record: %s", exc)
            continue
        if not isinstance(item, dict):
            logger.warning("skipping non-object conversation history record")
            continue
        items.append(item)
    return items


def load_recent_conversations(
    redis_client: ConversationRedisLike | None,
    *,
    include_sources: set[str] | None = None,
    exclude_stimulus_ids: set[str] | None = None,
    summary_builder: Callable[[str, str, list[ConversationEntry]], str | None] | None = None,
    raw_limit: int = RECENT_CONVERSATION_RAW_LIMIT,
    within_hours: int = RECENT_CONVERSATION_WINDOW_HOURS,
) -> list[RecentConversationPrompt]:
    if redis_client is None:
        return []
    history = load_conversation_history(redis_client, limit=CONVERSATION_HISTORY_LIMIT)
    if not history:
        return []
    grouped: dict[str, list[ConversationEntry]] = {}
    for entry in history:
        source = str(entry.get("source") or "").strip()
        if not source:
            continue
        grouped.setdefault(source, []).append(entry)
    stored_summaries = _load_conversation_summaries(redis_client)
    recent_conversations: list[tuple[datetime, RecentConversationPrompt]] = []
    forced_sources = include_sources or set()
    hidden_stimulus_ids = exclude_stimulus_ids or set()
    for source, entries in grouped.items():
        prompt_item = _build_recent_conversation_prompt(
            redis_client,
            stored_summaries,
            source,
            entries,
            forced_sources,
            hidden_stimulus_ids,
            summary_builder,
            raw_limit,
            within_hours,
        )
        if prompt_item is None:
            continue
        recent_conversations.append(prompt_item)
    recent_conversations.sort(key=lambda item: item[0])
    return [item for _, item in recent_conversations]


def remember_recent_action_echoes(
    redis_client: ConversationRedisLike | None,
    cycle_id: int,
    stimuli: list[Stimulus],
) -> None:
    if redis_client is None:
        return
    retained = [
        stimulus
        for stimulus in stimuli
        if _should_retain_recent_action_echo(stimulus)
    ]
    if not retained:
        return
    for stimulus in retained:
        started_at = time.perf_counter()
        redis_client.rpush(
            RECENT_ACTION_ECHO_KEY,
            json.dumps(_recent_action_echo_record(cycle_id, stimulus), ensure_ascii=False),
        )
        _log_redis_operation("rpush", started_at, f"key={RECENT_ACTION_ECHO_KEY}, count=1")
    trim_started_at = time.perf_counter()
    redis_client.ltrim(RECENT_ACTION_ECHO_KEY, -RECENT_ACTION_ECHO_LIMIT, -1)
    _log_redis_operation(
        "ltrim",
        trim_started_at,
        f"key={RECENT_ACTION_ECHO_KEY}, limit={RECENT_ACTION_ECHO_LIMIT}",
    )


def load_recent_action_echoes(
    redis_client: ConversationRedisLike | None,
    *,
    current_cycle_id: int,
    exclude_action_ids: set[str] | None = None,
) -> list[Stimulus]:
    if redis_client is None:
        return []
    started_at = time.perf_counter()
    raw_items = redis_client.lrange(RECENT_ACTION_ECHO_KEY, 0, -1)
    _log_redis_operation("lrange", started_at, f"key={RECENT_ACTION_ECHO_KEY}, count={len(raw_items)}")
    if not raw_items:
        return []
    excluded = exclude_action_ids or set()
    recent: list[Stimulus] = []
    seen_action_ids: set[str] = set()
    for raw_item in reversed(raw_items):
        record = _recent_action_echo_record_from_raw(raw_item)
        if record is None or not _recent_action_echo_is_visible(record, current_cycle_id):
            continue
        stimulus = _stimulus_from_dict(record["stimulus"])
        action_id = str(stimulus.action_id or "").strip()
        if not action_id or action_id in excluded or action_id in seen_action_ids:
            continue
        if not _should_retain_recent_action_echo(stimulus):
            continue
        seen_action_ids.add(action_id)
        recent.append(stimulus)
    recent.reverse()
    return recent


def _build_recent_conversation_prompt(
    redis_client: ConversationRedisLike,
    stored_summaries: dict[str, tuple[str, str, bool]],
    source: str,
    entries: list[ConversationEntry],
    forced_sources: set[str],
    hidden_stimulus_ids: set[str],
    summary_builder: Callable[[str, str, list[ConversationEntry]], str | None] | None,
    raw_limit: int,
    within_hours: int,
) -> tuple[datetime, RecentConversationPrompt] | None:
    last_timestamp = _conversation_timestamp(entries[-1])
    if last_timestamp is None:
        return None
    if not _conversation_is_recent(source, last_timestamp, forced_sources, within_hours):
        return None
    display_entries = _conversation_display_entries(entries, hidden_stimulus_ids)
    metadata = _latest_named_metadata(entries)
    source_name = _conversation_source_name(source, metadata)
    source_label = _conversation_source_label(source, metadata)
    existing_summary, absorbed_until, summary_current = stored_summaries.get(source, ("", "", False))
    summary = _refresh_conversation_summary(
        redis_client,
        source,
        source_name,
        existing_summary,
        absorbed_until,
        summary_current,
        entries,
        summary_builder,
        raw_limit,
    )
    recent_entries = display_entries[-raw_limit:] if raw_limit > 0 else []
    if not recent_entries and not summary:
        return None
    prompt: RecentConversationPrompt = {
        "source": source,
        "source_name": source_name,
        "source_label": source_label,
        "summary": summary,
        "last_timestamp": last_timestamp.isoformat(),
        "messages": [
            _recent_conversation_message(entry, source_name) for entry in recent_entries
        ],
    }
    return last_timestamp, prompt


def _load_conversation_summaries(redis_client: ConversationRedisLike) -> dict[str, tuple[str, str, bool]]:
    try:
        started_at = time.perf_counter()
        raw_map = redis_client.hgetall(CONVERSATION_SUMMARY_KEY)
        _log_redis_operation("hgetall", started_at, f"key={CONVERSATION_SUMMARY_KEY}, count={len(raw_map)}")
    except STIMULUS_REDIS_EXCEPTIONS:
        return {}
    summaries: dict[str, tuple[str, str, bool]] = {}
    for raw_source, raw_value in raw_map.items():
        source = str(raw_source or "").strip()
        if source:
            summaries[source] = _conversation_summary_state(raw_value)
    return summaries


def _refresh_conversation_summary(
    redis_client: ConversationRedisLike,
    source: str,
    source_name: str,
    existing_summary: str,
    absorbed_until: str,
    summary_current: bool,
    entries: list[ConversationEntry],
    summary_builder: Callable[[str, str, list[ConversationEntry]], str | None] | None,
    raw_limit: int,
) -> str:
    older_entries = entries[:-raw_limit] if len(entries) > raw_limit else []
    incremental_entries = _incremental_conversation_entries(older_entries, absorbed_until)
    original_summary = str(existing_summary or "").strip()
    entries_to_summarize = _conversation_entries_to_summarize(
        older_entries,
        incremental_entries,
        summary_current,
    )
    summary = _next_conversation_summary(
        source_name,
        original_summary,
        summary_current,
        entries_to_summarize,
        summary_builder,
    )
    if summary is None:
        return original_summary
    next_absorbed_until = _next_conversation_absorbed_until(older_entries, absorbed_until)
    if _conversation_summary_unchanged(entries_to_summarize, next_absorbed_until, absorbed_until):
        return summary
    try:
        _store_conversation_summary(redis_client, source, summary, next_absorbed_until)
    except STIMULUS_REDIS_EXCEPTIONS:
        return summary
    return summary


def _incremental_conversation_entries(
    older_entries: list[ConversationEntry],
    absorbed_until: str,
) -> list[ConversationEntry]:
    return [
        entry for entry in older_entries
        if _conversation_entry_is_newer(entry, absorbed_until)
    ]


def _conversation_entries_to_summarize(
    older_entries: list[ConversationEntry],
    incremental_entries: list[ConversationEntry],
    summary_current: bool,
) -> list[ConversationEntry]:
    if older_entries and not summary_current:
        return older_entries
    return incremental_entries


def _next_conversation_summary(
    source_name: str,
    existing_summary: str,
    summary_current: bool,
    entries_to_summarize: list[ConversationEntry],
    summary_builder: Callable[[str, str, list[ConversationEntry]], str | None] | None,
) -> str | None:
    if not entries_to_summarize:
        return existing_summary
    if summary_builder is None:
        return existing_summary
    summary_seed = existing_summary if summary_current else ""
    next_summary = summary_builder(source_name, summary_seed, entries_to_summarize)
    if next_summary is None:
        return None
    return str(next_summary).strip()


def _next_conversation_absorbed_until(
    older_entries: list[ConversationEntry],
    absorbed_until: str,
) -> str:
    if not older_entries:
        return absorbed_until
    return str(older_entries[-1].get("timestamp") or "").strip()


def _conversation_summary_unchanged(
    entries_to_summarize: list[ConversationEntry],
    next_absorbed_until: str,
    absorbed_until: str,
) -> bool:
    return not entries_to_summarize and next_absorbed_until == absorbed_until


def _store_conversation_summary(
    redis_client: ConversationRedisLike,
    source: str,
    summary: str,
    absorbed_until: str,
) -> None:
    started_at = time.perf_counter()
    redis_client.hset(
        CONVERSATION_SUMMARY_KEY,
        source,
        json.dumps({
            "version": RECENT_CONVERSATION_SUMMARY_VERSION,
            "summary": summary,
            "absorbed_until": absorbed_until,
        }, ensure_ascii=False),
    )
    _log_redis_operation("hset", started_at, f"key={CONVERSATION_SUMMARY_KEY}, source={source}")


def _recent_conversation_message(
    entry: ConversationEntry,
    source_name: str,
) -> RecentConversationMessage:
    role = str(entry.get("role") or "").strip()
    speaker_name = "我" if role == "assistant" else source_name
    return {
        "role": role,
        "speaker_name": speaker_name,
        "content": " ".join(str(entry.get("content") or "").split()),
        "timestamp": str(entry.get("timestamp") or ""),
    }


def _conversation_display_entries(
    entries: list[ConversationEntry],
    exclude_stimulus_ids: set[str],
) -> list[ConversationEntry]:
    if not exclude_stimulus_ids:
        return list(entries)
    filtered = [
        entry for entry in entries
        if str(entry.get("stimulus_id") or "").strip() not in exclude_stimulus_ids
    ]
    return filtered


def _conversation_is_recent(
    source: str,
    timestamp: datetime,
    include_sources: set[str],
    within_hours: int,
) -> bool:
    if source in include_sources:
        return True
    threshold = datetime.now(timezone.utc) - timedelta(hours=max(1, within_hours))
    return timestamp >= threshold


def _conversation_timestamp(entry: ConversationEntry) -> datetime | None:
    raw_timestamp = str(entry.get("timestamp") or "").strip()
    if not raw_timestamp:
        return None
    try:
        return datetime.fromisoformat(raw_timestamp)
    except ValueError:
        logger.warning("skipping malformed conversation timestamp: %s", raw_timestamp)
        return None


def _conversation_entry_is_newer(entry: ConversationEntry, absorbed_until: str) -> bool:
    if not absorbed_until:
        return True
    entry_timestamp = _conversation_timestamp(entry)
    if entry_timestamp is None:
        return False
    try:
        absorbed_timestamp = datetime.fromisoformat(absorbed_until)
    except ValueError:
        return True
    return entry_timestamp > absorbed_timestamp


def _log_redis_operation(operation: str, started_at: float, detail: str) -> None:
    elapsed = elapsed_ms(started_at)
    if elapsed < SLOW_REDIS_OPERATION_THRESHOLD_MS:
        return
    logger.info("stimulus redis %s finished in %.1f ms (%s)", operation, elapsed, detail)


def _latest_named_metadata(entries: list[ConversationEntry]) -> JsonObject:
    for entry in reversed(entries):
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        full_name = str(metadata.get("telegram_full_name") or "").strip()
        username = str(metadata.get("telegram_username") or "").strip()
        if full_name or username:
            return metadata
    metadata = entries[-1].get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _conversation_source_label(source: str, metadata: JsonObject) -> str:
    display_name = _conversation_source_name(source, metadata)
    if source.startswith("telegram:"):
        return f"[{display_name}]({source})"
    return display_name


def _conversation_source_name(source: str, metadata: JsonObject) -> str:
    full_name = str(metadata.get("telegram_full_name") or "").strip()
    username = str(metadata.get("telegram_username") or "").strip()
    return full_name or username or source


def _conversation_summary_state(raw_value: JsonValue | bytes) -> tuple[str, str, bool]:
    text = str(raw_value or "").strip()
    if not text:
        return "", "", False
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text, "", False
    if not isinstance(payload, dict):
        return text, "", False
    summary = str(payload.get("summary") or "").strip()
    absorbed_until = str(payload.get("absorbed_until") or "").strip()
    try:
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        logger.warning("skipping malformed conversation summary version: %r", payload.get("version"))
        version = 0
    return summary, absorbed_until, version == RECENT_CONVERSATION_SUMMARY_VERSION


def _recent_action_echo_record(cycle_id: int, stimulus: Stimulus) -> RecentActionEchoRecord:
    return {
        "cycle_id": cycle_id,
        "stimulus": _stimulus_to_dict(stimulus),
    }


def _recent_action_echo_record_from_raw(raw_value: str) -> RecentActionEchoRecord | None:
    try:
        payload = json.loads(raw_value)
    except (TypeError, ValueError) as exc:
        logger.warning("skipping malformed recent action echo record: %s", exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("skipping non-object recent action echo record")
        return None
    raw_cycle_id = payload.get("cycle_id")
    raw_stimulus = payload.get("stimulus")
    if not isinstance(raw_cycle_id, int) or not isinstance(raw_stimulus, dict):
        logger.warning("skipping incomplete recent action echo record")
        return None
    stimulus_record: StimulusRecord = raw_stimulus  # type: ignore[assignment]
    record: RecentActionEchoRecord = {
        "cycle_id": raw_cycle_id,
        "stimulus": stimulus_record,
    }
    return record


def _recent_action_echo_is_visible(record: RecentActionEchoRecord, current_cycle_id: int) -> bool:
    recorded_cycle_id = record["cycle_id"]
    cycle_visible = (
        recorded_cycle_id < current_cycle_id
        and current_cycle_id - recorded_cycle_id <= RECENT_ACTION_ECHO_RETAIN_CYCLES
    )
    if cycle_visible:
        return True
    recorded_at = _recent_action_echo_timestamp(record)
    if recorded_at is None:
        return False
    return datetime.now(timezone.utc) - recorded_at <= timedelta(seconds=RECENT_ACTION_ECHO_RETAIN_SECONDS)


def _recent_action_echo_timestamp(record: RecentActionEchoRecord) -> datetime | None:
    raw_timestamp = record["stimulus"].get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        return None
    try:
        return datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return None


def _should_retain_recent_action_echo(stimulus: Stimulus) -> bool:
    if not _is_information_action_echo(stimulus):
        return False
    status = str(stimulus.metadata.get("status") or "").strip()
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    if action_type == "send_message":
        return status in {"succeeded", "failed"}
    return status == "succeeded"


def _is_information_action_echo(stimulus: Stimulus) -> bool:
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    if action_type not in RECENT_ACTION_ECHO_ACTION_TYPES:
        return False
    return str(stimulus.metadata.get("origin") or "").strip() == "action"


def _select_ranked(items: list[Stimulus], limit: int) -> list[tuple[int, Stimulus]]:
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (pair[1].priority, pair[1].timestamp, pair[0]),
    )
    return ranked[:limit]


def _stimulus_to_dict(stimulus: Stimulus) -> StimulusRecord:
    return {
        "stimulus_id": stimulus.stimulus_id,
        "type": stimulus.type,
        "priority": stimulus.priority,
        "source": stimulus.source,
        "content": stimulus.content,
        "timestamp": stimulus.timestamp.isoformat(),
        "action_id": stimulus.action_id,
        "metadata": stimulus.metadata,
    }


def _stimulus_to_json(stimulus: Stimulus) -> str:
    return json.dumps(_stimulus_to_dict(stimulus), ensure_ascii=False)


def _stimulus_from_dict(data: StimulusRecord) -> Stimulus:
    return Stimulus(
        stimulus_id=data["stimulus_id"],
        type=data["type"],
        priority=data["priority"],
        source=data["source"],
        content=data["content"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        action_id=data.get("action_id"),
        metadata=data.get("metadata") or {},
    )
