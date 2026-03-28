"""Shared helpers for test doubles."""


def slice_window(items: list, start: int, end: int) -> list:
    if start < 0:
        start = max(len(items) + start, 0)
    if end < 0:
        end = len(items) + end
    return items[start:end + 1]


class ListRedisStub:
    def __init__(self):
        self.lists = {}
        self.hashes = {}

    def rpush(self, key, payload):
        self.lists.setdefault(key, []).append(payload)

    def lpop(self, key):
        items = self.lists.get(key, [])
        if not items:
            return None
        return items.pop(0)

    def lrange(self, key, start, end):
        return slice_window(self.lists.get(key, []), start, end)

    def ltrim(self, key, start, end):
        self.lists[key] = slice_window(self.lists.get(key, []), start, end)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    @staticmethod
    def publish(_channel, _payload):
        return None
