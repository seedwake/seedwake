"""Redis-backed short-term memory with in-memory deque fallback.

Three-layer structure per SPECS §4.1:
  Layer 1 — context window: recent N rounds, fed into prompt
  Layer 2 — buffer: older rounds, used by sleep/degeneration/frontend
  Layer 3 — expiry: beyond buffer limit, deleted (after sleep archival)
"""

import json
from collections import deque
from datetime import datetime

from core.thought_parser import Thought

REDIS_KEY = "seedwake:thoughts"
REDIS_CHANNEL = "seedwake:stream"


class ShortTermMemory:
    """Manages short-term thought storage.

    When Redis is available, uses a Sorted Set (score = timestamp).
    Falls back to an in-memory deque when Redis is None or unreachable.
    """

    def __init__(
        self,
        redis_client,
        context_window: int = 30,
        buffer_size: int = 500,
    ):
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
                self._trim()
                self._publish(thoughts)
            except Exception:
                self._redis = None

    def get_context(self) -> list[Thought]:
        """Return the most recent context_window * 3 thoughts."""
        limit = self._context_window * 3
        if self._redis:
            try:
                return self._redis_recent(limit)
            except Exception:
                self._redis = None
        return list(self._deque)[-limit:]

    @property
    def redis_available(self) -> bool:
        return self._redis is not None

    @property
    def redis_client(self):
        return self._redis

    def attach_redis(self, redis_client) -> bool:
        """Reattach Redis and repopulate it from the in-memory shadow copy."""
        self._redis = redis_client
        try:
            self._sync_to_redis()
        except Exception:
            self._redis = None
        return self.redis_available

    # -- Redis operations --------------------------------------------------

    def _redis_append(self, t: Thought) -> None:
        score = t.timestamp.timestamp()
        value = json.dumps(_thought_to_dict(t), ensure_ascii=False)
        self._redis.zadd(REDIS_KEY, {value: score})

    def _redis_recent(self, limit: int) -> list[Thought]:
        raw = self._redis.zrange(REDIS_KEY, -limit, -1)
        return [_dict_to_thought(json.loads(item)) for item in raw]

    def _trim(self) -> None:
        """Keep only the most recent buffer_size * 3 entries."""
        max_entries = self._buffer_size * 3
        total = self._redis.zcard(REDIS_KEY)
        if total > max_entries:
            self._redis.zremrangebyrank(REDIS_KEY, 0, total - max_entries - 1)

    def _publish(self, thoughts: list[Thought]) -> None:
        """Publish new thoughts to Redis Pub/Sub for SSE consumers."""
        payload = json.dumps(
            [_thought_to_dict(t) for t in thoughts],
            ensure_ascii=False,
        )
        self._redis.publish(REDIS_CHANNEL, payload)

    def _sync_to_redis(self) -> None:
        """Merge the in-memory shadow copy back into Redis after recovery."""
        for t in self._deque:
            self._redis_append(t)
        self._trim()


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
