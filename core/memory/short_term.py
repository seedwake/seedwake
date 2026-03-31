"""Redis-backed short-term memory with in-memory deque fallback.

Three-layer structure per SPECS §4.1:
  Layer 1 — context window: recent N rounds, fed into prompt
  Layer 2 — buffer: older rounds, used by sleep/degeneration/frontend
  Layer 3 — expiry: beyond buffer limit, deleted (after sleep archival)
"""

import json
from collections import deque
from datetime import datetime

import redis as redis_lib

from core.thought_parser import Thought
from core.types import JsonValue

REDIS_KEY = "seedwake:thoughts"
REDIS_CHANNEL = "seedwake:stream"
LATEST_CYCLE_KEY = "seedwake:latest_cycle_id"
SHORT_TERM_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    ConnectionError,
    TimeoutError,
    OSError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)


class ShortTermMemory:
    """Manages short-term thought storage.

    When Redis is available, uses a Sorted Set (score = timestamp).
    Falls back to an in-memory deque when Redis is None or unreachable.
    """

    def __init__(
        self,
        redis_client: redis_lib.Redis | None,
        context_window: int = 30,
        buffer_size: int = 500,
    ) -> None:
        self._redis = redis_client
        self._context_window = context_window
        self._buffer_size = buffer_size
        self._deque: deque[Thought] = deque(maxlen=buffer_size * 3)

    def append(self, thoughts: list[Thought]) -> None:
        """Store thoughts from one cycle."""
        for t in thoughts:
            self._deque.append(t)

        if self._redis:
            try:
                for t in thoughts:
                    self._redis_append(t)
                if thoughts:
                    self._update_latest_cycle_id(max(t.cycle_id for t in thoughts))
                self._trim()
                self._publish(thoughts)
            except SHORT_TERM_REDIS_EXCEPTIONS:
                self._redis = None

    def get_context(self) -> list[Thought]:
        """Return the most recent context_window * 3 thoughts."""
        limit = self._context_window * 3
        if self._redis:
            try:
                thoughts = self._redis_recent(limit)
                if _sanitize_trigger_refs(thoughts):
                    self._sync_shadow_trigger_refs(thoughts)
                    self._rewrite_recent_redis_thoughts(thoughts)
                return thoughts
            except SHORT_TERM_REDIS_EXCEPTIONS:
                self._redis = None
        thoughts = list(self._deque)[-limit:]
        _sanitize_trigger_refs(thoughts)
        return thoughts

    def latest_cycle_id(self) -> int:
        latest_from_deque = self._deque[-1].cycle_id if self._deque else 0
        if self._redis:
            try:
                latest_from_key = _coerce_cycle_id(self._redis.get(LATEST_CYCLE_KEY))
                recent = self._redis_recent(1)
            except SHORT_TERM_REDIS_EXCEPTIONS:
                self._redis = None
            else:
                latest_from_recent = recent[-1].cycle_id if recent else 0
                return max(latest_from_deque, latest_from_key, latest_from_recent)
        return latest_from_deque

    @property
    def redis_available(self) -> bool:
        return self._redis is not None

    @property
    def redis_client(self) -> redis_lib.Redis | None:
        return self._redis

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        """Reattach Redis and repopulate it from the in-memory shadow copy."""
        self._redis = redis_client
        try:
            self._sync_to_redis()
        except SHORT_TERM_REDIS_EXCEPTIONS:
            self._redis = None
        return self.redis_available

    # -- Redis operations --------------------------------------------------

    def _redis_append(self, t: Thought) -> None:
        redis_client = self._redis
        assert redis_client is not None
        score = t.timestamp.timestamp()
        value = json.dumps(_thought_to_dict(t), ensure_ascii=False)
        _redis_zadd(redis_client, REDIS_KEY, {value: score})

    def _redis_recent(self, limit: int) -> list[Thought]:
        redis_client = self._redis
        assert redis_client is not None
        raw_items = _redis_payloads(redis_client.zrange(REDIS_KEY, -limit, -1))
        return [_dict_to_thought(json.loads(item)) for item in raw_items]

    def _trim(self) -> None:
        """Keep only the most recent buffer_size * 3 entries."""
        redis_client = self._redis
        assert redis_client is not None
        max_entries = self._buffer_size * 3
        total = _redis_int(redis_client.zcard(REDIS_KEY))
        if total > max_entries:
            redis_client.zremrangebyrank(REDIS_KEY, 0, total - max_entries - 1)

    def _publish(self, thoughts: list[Thought]) -> None:
        """Publish new thoughts to Redis Pub/Sub for SSE consumers."""
        redis_client = self._redis
        assert redis_client is not None
        payload = json.dumps(
            [_thought_to_dict(t) for t in thoughts],
            ensure_ascii=False,
        )
        redis_client.publish(REDIS_CHANNEL, payload)

    def _rewrite_recent_redis_thoughts(self, thoughts: list[Thought]) -> None:
        redis_client = self._redis
        assert redis_client is not None
        raw_items = _redis_payloads(redis_client.zrange(REDIS_KEY, -len(thoughts), -1))
        if raw_items:
            redis_client.zrem(REDIS_KEY, *raw_items)
        for thought in thoughts:
            self._redis_append(thought)

    def _sync_shadow_trigger_refs(self, thoughts: list[Thought]) -> None:
        trigger_refs = {thought.thought_id: thought.trigger_ref for thought in thoughts}
        for thought in self._deque:
            if thought.thought_id in trigger_refs:
                thought.trigger_ref = trigger_refs[thought.thought_id]

    def _sync_to_redis(self) -> None:
        """Merge the in-memory shadow copy back into Redis after recovery."""
        for t in self._deque:
            self._redis_append(t)
        if self._deque:
            self._update_latest_cycle_id(max(t.cycle_id for t in self._deque))
        self._trim()

    def _update_latest_cycle_id(self, cycle_id: int) -> None:
        redis_client = self._redis
        assert redis_client is not None
        redis_client.eval(
            """
            local current = tonumber(redis.call("GET", KEYS[1]) or "0")
            local incoming = tonumber(ARGV[1]) or 0
            if incoming > current then
              redis.call("SET", KEYS[1], incoming)
              return incoming
            end
            return current
            """,
            1,
            LATEST_CYCLE_KEY,
            cycle_id,
        )


def _thought_to_dict(t: Thought) -> dict:
    return {
        "thought_id": t.thought_id,
        "cycle_id": t.cycle_id,
        "index": t.index,
        "type": t.type,
        "content": t.content,
        "trigger_ref": t.trigger_ref,
        "action_request": t.action_request,
        "attention_weight": t.attention_weight,
        "timestamp": t.timestamp.isoformat(),
    }


def _dict_to_thought(d: dict) -> Thought:
    return Thought(
        thought_id=d["thought_id"],
        cycle_id=d["cycle_id"],
        index=d["index"],
        type=d["type"],
        content=d["content"],
        trigger_ref=d.get("trigger_ref"),
        action_request=d.get("action_request"),
        attention_weight=d.get("attention_weight", 0.0),
        timestamp=datetime.fromisoformat(d["timestamp"]),
    )


def _coerce_cycle_id(value: JsonValue | bytes) -> int:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, int):
        return value
    return 0


def _redis_int(value: JsonValue | bytes) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"unexpected redis integer result: {type(value).__name__}")
    return value


def _redis_payloads(value: JsonValue | bytes) -> list[str | bytes | bytearray]:
    if not isinstance(value, list):
        raise TypeError(f"unexpected redis list result: {type(value).__name__}")
    payloads: list[str | bytes | bytearray] = []
    for item in value:
        if isinstance(item, (str, bytes, bytearray)):
            payloads.append(item)
            continue
        raise TypeError(f"unexpected redis payload result: {type(item).__name__}")
    return payloads


def _redis_zadd(
    redis_client: redis_lib.Redis,
    key: str,
    mapping: dict[str, float],
) -> int:
    # redis-py supports mapping-based ZADD, but the bundled IDE stub still models
    # the legacy score/member signature.
    # noinspection PyArgumentList
    return int(redis_client.zadd(key, mapping))


def _sanitize_trigger_refs(thoughts: list[Thought]) -> bool:
    changed = False
    valid_ids: set[str] = set()
    for thought in thoughts:
        trigger_ref = str(thought.trigger_ref or "").strip()
        if _should_clear_trigger_ref(trigger_ref, thought, valid_ids):
            thought.trigger_ref = None
            changed = True
        valid_ids.add(thought.thought_id)
    return changed


def _should_clear_trigger_ref(
    trigger_ref: str,
    thought: Thought,
    valid_ids: set[str],
) -> bool:
    return (
        bool(trigger_ref) and trigger_ref not in valid_ids
    ) or (
        not trigger_ref and thought.trigger_ref is not None
    )
