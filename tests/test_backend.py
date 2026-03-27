import asyncio
import json
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routes.stream import stream_events
from core.action import ACTION_CONTROL_KEY
from core.stimulus import CONVERSATION_HISTORY_KEY


async def _read_first_stream_chunk(iterator) -> str | bytes:
    first_chunk = await iterator.__anext__()
    await iterator.aclose()
    return first_chunk


class FakePubSub:
    def __init__(self, messages):
        self._messages = messages

    def subscribe(self, *channels):
        return None

    def get_message(self, timeout=0):
        if not self._messages:
            return None
        return self._messages.pop(0)

    def close(self):
        return None


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.messages = []
        self.sorted_sets = {}
        self.hashes = {}

    def ping(self):
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lpop(self, key):
        items = self.lists.get(key, [])
        if not items:
            return None
        return items.pop(0)

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if start < 0:
            start = max(len(items) + start, 0)
        if end < 0:
            end = len(items) + end
        return items[start:end + 1]

    def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        if start < 0:
            start = max(len(items) + start, 0)
        if end < 0:
            end = len(items) + end
        self.lists[key] = items[start:end + 1]

    def publish(self, channel, payload):
        self.messages.append((channel, payload))

    def zrange(self, key, start, end):
        items = self.sorted_sets.get(key, [])
        if start < 0:
            start = max(len(items) + start, 0)
        if end < 0:
            end = len(items) + end
        return items[start:end + 1]

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def pubsub(self, ignore_subscribe_messages=True):
        return FakePubSub([
            {
                "channel": "seedwake:events",
                "data": json.dumps({"type": "status", "payload": {"message": "ready"}}),
            }
        ])


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.app = create_app(
            config={
                "admins": [
                    {"username": "alice", "token": "token_alice"},
                ]
            },
            redis_client=self.redis,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_conversation_history_is_read_only(self) -> None:
        response = self.client.post(
            "/api/conversation",
            headers={"Authorization": "Bearer token_alice"},
            json={"username": "alice", "message": "你好"},
        )

        self.assertEqual(response.status_code, 405)

    def test_conversation_history_query(self) -> None:
        self.redis.rpush(
            CONVERSATION_HISTORY_KEY,
            json.dumps({
                "entry_id": "conv_1",
                "role": "user",
                "source": "telegram:1",
                "content": "你好",
                "timestamp": "2026-03-27T12:00:00+00:00",
                "stimulus_id": "stim_1",
                "metadata": {},
            }, ensure_ascii=False),
        )
        self.redis.rpush(
            CONVERSATION_HISTORY_KEY,
            json.dumps({
                "entry_id": "conv_2",
                "role": "assistant",
                "source": "telegram:1",
                "content": "你好，我在。",
                "timestamp": "2026-03-27T12:00:01+00:00",
                "stimulus_id": "stim_1",
                "metadata": {},
            }, ensure_ascii=False),
        )

        response = self.client.get(
            "/api/conversation?limit=10",
            headers={"Authorization": "Bearer token_alice"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["items"][0]["role"], "user")
        self.assertEqual(body["items"][1]["role"], "assistant")

    def test_action_confirm_pushes_control_message(self) -> None:
        response = self.client.post(
            "/api/action/confirm",
            headers={"Authorization": "Bearer token_alice"},
            json={"action_id": "act_1", "approved": True, "note": "go"},
        )

        self.assertEqual(response.status_code, 200)
        stored = self.redis.lists[ACTION_CONTROL_KEY][0]
        self.assertIn('"action_id": "act_1"', stored)
        self.assertIn('"approved": true', stored)

    def test_action_confirm_returns_503_when_enqueue_fails(self) -> None:
        class FailingRedis(FakeRedis):
            def rpush(self, key, value):
                raise RuntimeError("boom")

        app = create_app(
            config={"admins": [{"username": "alice", "token": "token_alice"}]},
            redis_client=FailingRedis(),
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/action/confirm",
                headers={"Authorization": "Bearer token_alice"},
                json={"action_id": "act_1", "approved": True},
            )

        self.assertEqual(response.status_code, 503)

    def test_stream_requires_query_token(self) -> None:
        response = self.client.get("/api/stream")

        self.assertEqual(response.status_code, 401)

    def test_stream_yields_status_chunk(self) -> None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={"admins": [{"username": "alice", "token": "token_alice"}]},
                    redis=self.redis,
                ),
            ),
        )

        response = stream_events(request=request, admin_username="alice")
        first_chunk = asyncio.run(_read_first_stream_chunk(response.body_iterator))
        if isinstance(first_chunk, bytes):
            first_chunk = first_chunk.decode("utf-8")

        self.assertIn("event: status", first_chunk)

    def test_recent_thoughts_query(self) -> None:
        self.redis.sorted_sets["seedwake:thoughts"] = [
            json.dumps({"thought_id": "C1-1", "content": "a"}, ensure_ascii=False),
            json.dumps({"thought_id": "C1-2", "content": "b"}, ensure_ascii=False),
        ]

        response = self.client.get(
            "/api/thoughts?limit=2",
            headers={"Authorization": "Bearer token_alice"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["items"][0]["thought_id"], "C1-1")

    def test_actions_query(self) -> None:
        self.redis.hset(
            "seedwake:actions",
            "act_1",
            json.dumps({
                "action_id": "act_1",
                "status": "running",
                "submitted_at": "2026-03-27T12:00:00+00:00",
            }),
        )
        self.redis.hset(
            "seedwake:actions",
            "act_2",
            json.dumps({
                "action_id": "act_2",
                "status": "succeeded",
                "submitted_at": "2026-03-27T11:00:00+00:00",
            }),
        )

        response = self.client.get(
            "/api/actions?status=running",
            headers={"Authorization": "Bearer token_alice"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["action_id"], "act_1")
