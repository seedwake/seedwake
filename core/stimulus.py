"""Stimulus queue for external events and action results."""

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from core.types import ConversationEntry, JsonObject, StimulusRecord

REDIS_KEY = "seedwake:stimuli"
CONVERSATION_HISTORY_KEY = "seedwake:conversation_history"
CONVERSATION_HISTORY_LIMIT = 500


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

    def __init__(self, redis_client):
        self._redis = redis_client
        self._deque: deque[Stimulus] = deque()

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
        self._deque.append(stimulus)
        if self._redis:
            try:
                self._redis.rpush(REDIS_KEY, _stimulus_to_json(stimulus))
                if stimulus_type == "conversation":
                    append_conversation_history(
                        self._redis,
                        role="user",
                        source=source,
                        content=content,
                        stimulus_id=stimulus.stimulus_id,
                        metadata=stimulus.metadata,
                        timestamp=stimulus.timestamp,
                    )
            except Exception:
                self._redis = None
        return stimulus

    def pop_many(self, limit: int = 2) -> list[Stimulus]:
        if limit <= 0:
            return []
        if self._redis:
            try:
                return self._redis_pop_many(limit)
            except Exception:
                self._redis = None
        return self._shadow_pop_many(limit)

    def requeue_front(self, stimuli: list[Stimulus]) -> None:
        if not stimuli:
            return
        for stimulus in reversed(stimuli):
            self._deque.appendleft(stimulus)
        if self._redis:
            try:
                payloads = [_stimulus_to_json(stimulus) for stimulus in reversed(stimuli)]
                self._redis.lpush(REDIS_KEY, *payloads)
            except Exception:
                self._redis = None

    @property
    def redis_available(self) -> bool:
        return self._redis is not None

    def attach_redis(self, redis_client) -> bool:
        self._redis = redis_client
        try:
            self._sync_to_redis()
        except Exception:
            self._redis = None
        return self.redis_available

    def _redis_pop_many(self, limit: int) -> list[Stimulus]:
        raw_items = self._redis.lrange(REDIS_KEY, 0, -1)
        if not raw_items:
            return []
        parsed = [_stimulus_from_dict(json.loads(item)) for item in raw_items]
        chosen_pairs = _select_ranked(parsed, limit)
        chosen_items = [parsed[index] for index, _ in chosen_pairs]

        for index, _ in chosen_pairs:
            self._redis.lrem(REDIS_KEY, 1, raw_items[index])

        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _shadow_pop_many(self, limit: int) -> list[Stimulus]:
        items = list(self._deque)
        chosen_pairs = _select_ranked(items, limit)
        chosen_items = [items[index] for index, _ in chosen_pairs]
        self._drop_shadow_items([stimulus.stimulus_id for stimulus in chosen_items])
        return chosen_items

    def _drop_shadow_items(self, stimulus_ids: list[str]) -> None:
        if not stimulus_ids:
            return
        id_set = set(stimulus_ids)
        self._deque = deque(
            stimulus for stimulus in self._deque
            if stimulus.stimulus_id not in id_set
        )

    def _sync_to_redis(self) -> None:
        existing = self._redis.lrange(REDIS_KEY, 0, -1)
        existing_ids = {
            json.loads(item)["stimulus_id"]
            for item in existing
        }
        for stimulus in self._deque:
            if stimulus.stimulus_id in existing_ids:
                continue
            self._redis.rpush(REDIS_KEY, _stimulus_to_json(stimulus))
            if stimulus.type == "conversation":
                append_conversation_history(
                    self._redis,
                    role="user",
                    source=stimulus.source,
                    content=stimulus.content,
                    stimulus_id=stimulus.stimulus_id,
                    metadata=stimulus.metadata,
                    timestamp=stimulus.timestamp,
                )


def append_conversation_history(
    redis_client,
    *,
    role: str,
    source: str,
    content: str,
    stimulus_id: str | None = None,
    metadata: JsonObject | None = None,
    timestamp: datetime | None = None,
) -> ConversationEntry:
    entry = {
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


def load_conversation_history(redis_client, limit: int = 100) -> list[ConversationEntry]:
    if redis_client is None or limit <= 0:
        return []
    raw_items = redis_client.lrange(CONVERSATION_HISTORY_KEY, -limit, -1)
    return [json.loads(item) for item in raw_items]


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
