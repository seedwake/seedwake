"""Shared helpers for test doubles."""

from core.action import ActionRedisLike
from core.sleep import SleepRedisLike


def slice_window(items: list, start: int, end: int) -> list:
    if start < 0:
        start = max(len(items) + start, 0)
    if end < 0:
        end = len(items) + end
    return items[start:end + 1]


class ListRedisStub(ActionRedisLike, SleepRedisLike):
    def __init__(self):
        self.lists = {}
        self.hashes = {}
        self.sorted_sets = {}
        self.values = {}

    def rpush(self, key, payload):
        self.lists.setdefault(key, []).append(payload)

    def lpop(self, key):
        items = self.lists.get(key, [])
        if not items:
            return None
        return items.pop(0)

    def lpush(self, key, *values):
        bucket = self.lists.setdefault(key, [])
        for value in reversed(values):
            bucket.insert(0, value)
        return len(bucket)

    def lrange(self, key, start, end):
        return slice_window(self.lists.get(key, []), start, end)

    def ltrim(self, key, start, end):
        self.lists[key] = slice_window(self.lists.get(key, []), start, end)

    def lrem(self, key, count, value):
        bucket = self.lists.get(key, [])
        removed = 0
        kept = []
        for item in bucket:
            if item == value and (count <= 0 or removed < count):
                removed += 1
                continue
            kept.append(item)
        self.lists[key] = kept
        return removed

    def hset(self, key, hash_field, value):
        self.hashes.setdefault(key, {})[hash_field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def get(self, key: str) -> str | bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True

    @staticmethod
    def publish(_channel, _payload):
        return None

    def zscore(self, key, member):
        return self.sorted_sets.get(key, {}).get(member)

    def zadd(self, key, mapping, nx=False):
        bucket = self.sorted_sets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if nx and member in bucket:
                continue
            bucket[member] = float(score)
            added += 1
        return added

    def zrem(self, key, member):
        self.sorted_sets.get(key, {}).pop(member, None)

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def zremrangebyscore(self, key, min_score, max_score):
        _ = min_score
        bucket = self.sorted_sets.get(key, {})
        ceiling = float(max_score)
        removed = 0
        for member, score in tuple(bucket.items()):
            if score <= ceiling:
                bucket.pop(member, None)
                removed += 1
        return removed

    def zremrangebyrank(self, key, start, end):
        bucket = self.sorted_sets.get(key, {})
        ranked = sorted(bucket.items(), key=lambda pair: (pair[1], pair[0]))
        if not ranked:
            return 0
        if end < 0:
            end = len(ranked) + end
        selected = ranked[start:end + 1]
        for member, _ in selected:
            bucket.pop(member, None)
        return len(selected)
