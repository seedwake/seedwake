"""Stimulus queue for external events and action results."""

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
import redis as redis_lib
from redis import exceptions as redis_exceptions
from uuid import uuid4

from core.types import ConversationEntry, JsonObject, RecentConversationMessage, RecentConversationPrompt, StimulusRecord

REDIS_KEY = "seedwake:stimuli"
CONVERSATION_HISTORY_KEY = "seedwake:conversation_history"
CONVERSATION_HISTORY_LIMIT = 500
CONVERSATION_SUMMARY_KEY = "seedwake:conversation_summaries"
RECENT_CONVERSATION_RAW_LIMIT = 10
RECENT_CONVERSATION_WINDOW_HOURS = 24
RECENT_CONVERSATION_SUMMARY_INPUT_LIMIT = 6
RECENT_CONVERSATION_SUMMARY_MAX_CHARS = 280
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
logger = logging.getLogger(__name__)


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

    def __init__(self, redis_client: redis_lib.Redis | None) -> None:
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
                redis_client.rpush(REDIS_KEY, _stimulus_to_json(stimulus))
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

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        with self._lock:
            self._redis = redis_client
        try:
            self._sync_to_redis()
        except STIMULUS_REDIS_EXCEPTIONS:
            with self._lock:
                self._redis = None
        return self.redis_available

    def _redis_pop_many(self, redis_client: redis_lib.Redis, limit: int) -> list[Stimulus]:
        raw_items = redis_client.lrange(REDIS_KEY, 0, -1)
        if not raw_items:
            return []
        parsed = [_stimulus_from_dict(json.loads(item)) for item in raw_items]
        chosen_pairs = _select_ranked(parsed, limit)
        chosen_items = [parsed[index] for index, _ in chosen_pairs]

        for index, _ in chosen_pairs:
            redis_client.lrem(REDIS_KEY, 1, raw_items[index])

        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _shadow_pop_many(self, limit: int) -> list[Stimulus]:
        with self._lock:
            items = list(self._deque)
        chosen_pairs = _select_ranked(items, limit)
        chosen_items = [items[index] for index, _ in chosen_pairs]
        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _redis_pop_all(self, redis_client: redis_lib.Redis) -> list[Stimulus]:
        raw_items = redis_client.lrange(REDIS_KEY, 0, -1)
        if not raw_items:
            return []
        parsed = [_stimulus_from_dict(json.loads(item)) for item in raw_items]
        chosen_pairs = _select_ranked(parsed, len(parsed))
        chosen_items = [parsed[index] for index, _ in chosen_pairs]
        for raw_item in raw_items:
            redis_client.lrem(REDIS_KEY, 1, raw_item)
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
        existing = redis_client.lrange(REDIS_KEY, 0, -1)
        existing_ids = {
            json.loads(item)["stimulus_id"]
            for item in existing
        }
        for stimulus in shadow_items:
            if stimulus.stimulus_id in existing_ids:
                continue
            redis_client.rpush(REDIS_KEY, _stimulus_to_json(stimulus))
            if stimulus.type == "conversation":
                append_conversation_history(
                    redis_client,
                    role="user",
                    source=stimulus.source,
                    content=stimulus.content,
                    stimulus_id=stimulus.stimulus_id,
                    metadata=stimulus.metadata,
                    timestamp=stimulus.timestamp,
                )


def append_conversation_history(
    redis_client: redis_lib.Redis | None,
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
        redis_client.rpush(CONVERSATION_HISTORY_KEY, json.dumps(entry, ensure_ascii=False))
        redis_client.ltrim(CONVERSATION_HISTORY_KEY, -CONVERSATION_HISTORY_LIMIT, -1)
    return entry


def load_conversation_history(
    redis_client: redis_lib.Redis | None,
    limit: int = 100,
) -> list[ConversationEntry]:
    if redis_client is None or limit <= 0:
        return []
    raw_items = redis_client.lrange(CONVERSATION_HISTORY_KEY, -limit, -1)
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
    redis_client: redis_lib.Redis | None,
    *,
    include_sources: set[str] | None = None,
    exclude_stimulus_ids: set[str] | None = None,
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
        last_timestamp = _conversation_timestamp(entries[-1])
        if last_timestamp is None:
            continue
        if not _conversation_is_recent(source, last_timestamp, forced_sources, within_hours):
            continue
        display_entries = _conversation_display_entries(entries, hidden_stimulus_ids)
        existing_summary, absorbed_until = stored_summaries.get(source, ("", ""))
        summary = _refresh_conversation_summary(
            redis_client,
            source,
            existing_summary,
            absorbed_until,
            display_entries,
            raw_limit,
        )
        recent_entries = display_entries[-raw_limit:]
        if not recent_entries and not summary and source not in forced_sources:
            continue
        source_label = _conversation_source_label(source, _latest_named_metadata(entries))
        recent_conversations.append((
            last_timestamp,
            {
                "source": source,
                "source_label": source_label,
                "summary": summary,
                "last_timestamp": last_timestamp.isoformat(),
                "messages": [
                    _recent_conversation_message(entry, source_label) for entry in recent_entries
                ],
            },
        ))
    recent_conversations.sort(key=lambda item: item[0])
    return [item for _, item in recent_conversations]


def _load_conversation_summaries(redis_client: redis_lib.Redis) -> dict[str, tuple[str, str]]:
    try:
        raw_map = redis_client.hgetall(CONVERSATION_SUMMARY_KEY)
    except STIMULUS_REDIS_EXCEPTIONS:
        return {}
    summaries: dict[str, tuple[str, str]] = {}
    for raw_source, raw_value in raw_map.items():
        source = str(raw_source or "").strip()
        if source:
            summaries[source] = _conversation_summary_state(raw_value)
    return summaries


def _refresh_conversation_summary(
    redis_client: redis_lib.Redis,
    source: str,
    existing_summary: str,
    absorbed_until: str,
    entries: list[ConversationEntry],
    raw_limit: int,
) -> str:
    older_entries = entries[:-raw_limit] if len(entries) > raw_limit else []
    incremental_entries = [
        entry for entry in older_entries
        if _conversation_entry_is_newer(entry, absorbed_until)
    ]
    summary = _conversation_summary(existing_summary, incremental_entries)
    next_absorbed_until = str(older_entries[-1].get("timestamp") or "").strip() if older_entries else absorbed_until
    try:
        redis_client.hset(
            CONVERSATION_SUMMARY_KEY,
            source,
            json.dumps({
                "summary": summary,
                "absorbed_until": next_absorbed_until,
            }, ensure_ascii=False),
        )
    except STIMULUS_REDIS_EXCEPTIONS:
        return summary
    return summary


def _conversation_summary(existing_summary: str, older_entries: list[ConversationEntry]) -> str:
    fragments: list[str] = []
    existing = str(existing_summary or "").strip()
    if existing:
        fragments.append(existing)
    for entry in older_entries[-RECENT_CONVERSATION_SUMMARY_INPUT_LIMIT:]:
        speaker = _conversation_summary_speaker(entry)
        content = _clip_conversation_text(str(entry.get("content") or ""), 48)
        if content:
            fragments.append(f"{speaker}：{content}")
    if not fragments:
        return existing
    merged = "；".join(_dedupe_preserve_order(fragments))
    return _clip_conversation_text(merged, RECENT_CONVERSATION_SUMMARY_MAX_CHARS)


def _recent_conversation_message(
    entry: ConversationEntry,
    source_label: str,
) -> RecentConversationMessage:
    role = str(entry.get("role") or "").strip()
    speaker_label = "我" if role == "assistant" else source_label
    return {
        "role": role,
        "speaker_label": speaker_label,
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
    full_name = str(metadata.get("telegram_full_name") or "").strip()
    username = str(metadata.get("telegram_username") or "").strip()
    display_name = full_name or username or source
    if source.startswith("telegram:"):
        return f"[{display_name}]({source})"
    return display_name


def _conversation_summary_speaker(entry: ConversationEntry) -> str:
    role = str(entry.get("role") or "").strip()
    if role == "assistant":
        return "我"
    source = str(entry.get("source") or "").strip()
    metadata = entry.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    label = _conversation_source_label(source, metadata_dict)
    if label.startswith("[") and "](" in label:
        return label.split("](", 1)[0].lstrip("[")
    return label


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _clip_conversation_text(text: str, max_chars: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _conversation_summary_state(raw_value: object) -> tuple[str, str]:
    text = str(raw_value or "").strip()
    if not text:
        return "", ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text, ""
    if not isinstance(payload, dict):
        return text, ""
    summary = str(payload.get("summary") or "").strip()
    absorbed_until = str(payload.get("absorbed_until") or "").strip()
    return summary, absorbed_until


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
