import asyncio
import json
import unittest
from collections.abc import AsyncIterable
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import redis as redis_lib
from fastapi import Request
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routes.stream import PUBSUB_POLL_TIMEOUT_SECONDS, _stream_next_chunk, stream_events
from core.action import ACTION_CONTROL_KEY
from core.emotion import EMOTION_STATE_KEY
from core.memory.short_term import LATEST_CYCLE_KEY
from core.sleep import SLEEP_STATE_KEY
from core.state import (
    BOOT_MARKER_KEY,
    COMPLETED_CYCLES_KEY,
    RUNTIME_STATE_KEY,
    load_or_build_state_snapshot,
)
from core.stimulus import (
    CONVERSATION_HISTORY_KEY,
    RECENT_ACTION_ECHO_KEY,
    REDIS_KEY as STIMULUS_REDIS_KEY,
)
from test_support import slice_window


async def _read_first_stream_chunk(iterator: AsyncIterable[str | bytes | memoryview]) -> str | bytes | memoryview:
    first_chunk = await anext(aiter(iterator))
    return first_chunk


async def _read_stream_chunks(
    iterator: AsyncIterable[str | bytes | memoryview],
    count: int,
) -> list[str]:
    chunks: list[str] = []
    async_iterator = aiter(iterator)
    for _ in range(count):
        chunk = await anext(async_iterator)
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        chunks.append(str(chunk))
    return chunks


def _as_request(value: Request | SimpleNamespace) -> Request:
    return cast(Request, value)


def _conversation_history_entry(
    *,
    entry_id: str,
    role: str,
    content: str,
    source: str = "telegram:1",
    timestamp: str = "2026-03-27T12:00:00+00:00",
    stimulus_id: str = "stim_1",
) -> str:
    return json.dumps({
        "entry_id": entry_id,
        "role": role,
        "source": source,
        "content": content,
        "timestamp": timestamp,
        "stimulus_id": stimulus_id,
        "metadata": {},
    }, ensure_ascii=False)


def _recent_action_echo_record(
    *,
    cycle_id: int,
    stimulus_id: str,
    action_id: str,
    content: str,
    timestamp: str,
    action_type: str = "weather",
) -> str:
    return json.dumps({
        "cycle_id": cycle_id,
        "stimulus": {
            "stimulus_id": stimulus_id,
            "type": "action_result",
            "priority": 2,
            "source": f"action:{action_id}",
            "content": content,
            "timestamp": timestamp,
            "action_id": action_id,
            "metadata": {
                "origin": "action",
                "action_type": action_type,
                "status": "succeeded",
            },
        },
    }, ensure_ascii=False)


def _stimulus_queue_record(
    *,
    stimulus_id: str,
    stimulus_type: str,
    content: str,
    timestamp: str,
    source: str,
    priority: int = 1,
    action_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    return json.dumps({
        "stimulus_id": stimulus_id,
        "type": stimulus_type,
        "priority": priority,
        "source": source,
        "content": content,
        "timestamp": timestamp,
        "action_id": action_id,
        "metadata": metadata or {},
    }, ensure_ascii=False)


def _action_result_queue_record(
    *,
    stimulus_id: str,
    action_id: str,
    content: str,
    timestamp: str,
    action_type: str = "weather",
) -> str:
    return _stimulus_queue_record(
        stimulus_id=stimulus_id,
        stimulus_type="action_result",
        priority=2,
        source=f"action:{action_id}",
        content=content,
        timestamp=timestamp,
        action_id=action_id,
        metadata={
            "origin": "action",
            "action_type": action_type,
            "status": "succeeded",
        },
    )


class FakePubSub:
    def __init__(self, messages):
        self._messages = messages
        self.closed = False
        self.timeouts = []

    @staticmethod
    def subscribe(*channels):
        _ = channels
        return None

    def get_message(self, timeout=0):
        self.timeouts.append(timeout)
        if not self._messages:
            return None
        return self._messages.pop(0)

    def close(self):
        self.closed = True
        return None


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.messages = []
        self.sorted_sets = {}
        self.hashes = {}
        self.strings = {}

    @staticmethod
    def ping():
        return True

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lpop(self, key):
        items = self.lists.get(key, [])
        if not items:
            return None
        return items.pop(0)

    def lrange(self, key, start, end):
        return slice_window(self.lists.get(key, []), start, end)

    def ltrim(self, key, start, end):
        self.lists[key] = slice_window(self.lists.get(key, []), start, end)

    def publish(self, channel, payload):
        self.messages.append((channel, payload))

    def set(self, key, value):
        self.strings[key] = value
        return True

    def incr(self, key):
        value = int(self.strings.get(key) or 0) + 1
        self.strings[key] = str(value)
        return value

    def get(self, key):
        return self.strings.get(key)

    def zrange(self, key, start, end):
        return slice_window(self.sorted_sets.get(key, []), start, end)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    @staticmethod
    def pubsub(ignore_subscribe_messages=True):
        _ = ignore_subscribe_messages
        return FakePubSub([
            {
                "channel": "seedwake:events",
                "data": json.dumps({
                    "type": "status",
                    "payload": {"message": {"key": "status.core_started", "params": {}}},
                }),
            }
        ])


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict("os.environ", {"BACKEND_API_TOKEN": "token_backend"})
        self.env_patch.start()
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
        self.env_patch.stop()

    def test_create_app_localizes_missing_backend_token_error(self) -> None:
        with patch("backend.main.load_dotenv", return_value=None):
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "BACKEND_API_TOKEN not configured"):
                    create_app(
                        config={"language": "en", "admins": []},
                        redis_client=cast(redis_lib.Redis, cast(object, FakeRedis())),
                    )

    def test_conversation_history_is_read_only(self) -> None:
        response = self.client.post(
            "/api/conversation",
            headers={"X-API-Token": "token_backend"},
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
                "metadata": {
                    "telegram_chat_id": "1",
                    "telegram_username": "alice",
                    "telegram_full_name": "Alice",
                    "telegram_message_id": "294",
                },
            }, ensure_ascii=False),
        )
        self.redis.rpush(
            CONVERSATION_HISTORY_KEY,
            _conversation_history_entry(
                entry_id="conv_2",
                role="assistant",
                content="你好，我在。",
                timestamp="2026-03-27T12:00:01+00:00",
            ),
        )

        response = self.client.get(
            "/api/conversation?limit=10",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["items"][0]["role"], "user")
        self.assertEqual(body["items"][1]["role"], "assistant")
        self.assertEqual(body["items"][0]["direction"], "inbound")
        self.assertEqual(body["items"][0]["speaker_name"], "Alice")
        self.assertEqual(body["items"][0]["chat_id"], "1")
        self.assertEqual(body["items"][0]["username"], "alice")
        self.assertEqual(body["items"][0]["full_name"], "Alice")
        self.assertEqual(body["items"][0]["message_id"], "294")
        self.assertEqual(body["items"][1]["direction"], "outbound")
        self.assertEqual(body["items"][1]["speaker_name"], "Seedwake")

    def test_state_query_returns_stored_runtime_state(self) -> None:
        self.redis.set(
            RUNTIME_STATE_KEY,
            json.dumps({
                "mode": "waking",
                "energy": 68.2,
                "energy_per_cycle": 0.2,
                "next_drowsy_cycle": 1832,
                "emotions": {
                    "curiosity": 0.72,
                    "calm": 0.58,
                    "satisfied": 0.46,
                    "concern": 0.28,
                    "frustration": 0.11,
                },
                "cycle": {"current": 1641, "since_boot": 12, "avg_seconds": 11.4},
                "uptime": {"started_at": "2020-01-01T00:00:00+00:00", "seconds": 1},
            }, ensure_ascii=False),
        )

        response = self.client.get(
            "/api/state",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "waking")
        self.assertEqual(body["energy"], 68.2)
        self.assertEqual(body["emotions"]["satisfied"], 0.46)
        self.assertEqual(body["cycle"]["current"], 1641)
        self.assertEqual(body["uptime"]["started_at"], "2020-01-01T00:00:00Z")
        self.assertGreater(body["uptime"]["seconds"], 1)

    def test_state_fallback_uses_boot_marker_and_completed_cycles(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "120")
        self.redis.set(
            BOOT_MARKER_KEY,
            json.dumps({
                "boot_id": "boot_test",
                "started_at": "2020-01-01T00:00:00Z",
                "baseline_cycle": 100,
            }),
        )
        self.redis.set(COMPLETED_CYCLES_KEY, "7")

        payload = load_or_build_state_snapshot(self.redis, {})

        self.assertEqual(payload["uptime"]["started_at"], "2020-01-01T00:00:00Z")
        self.assertGreater(payload["uptime"]["seconds"], 0)
        self.assertEqual(payload["cycle"]["current"], 120)
        self.assertEqual(payload["cycle"]["since_boot"], 7)

    def test_state_fallback_uses_boot_marker_when_completed_cycles_missing(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "120")
        self.redis.set(
            BOOT_MARKER_KEY,
            json.dumps({
                "boot_id": "boot_test",
                "started_at": "2020-01-01T00:00:00Z",
                "baseline_cycle": 100,
            }),
        )

        payload = load_or_build_state_snapshot(self.redis, {})

        self.assertEqual(payload["uptime"]["started_at"], "2020-01-01T00:00:00Z")
        self.assertGreater(payload["uptime"]["seconds"], 0)
        self.assertEqual(payload["cycle"]["current"], 120)
        self.assertEqual(payload["cycle"]["since_boot"], 20)

    def test_state_fallback_uses_cold_empty_defaults_without_boot_marker(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "120")

        payload = load_or_build_state_snapshot(self.redis, {})

        self.assertLessEqual(payload["uptime"]["seconds"], 1)
        self.assertEqual(payload["cycle"]["current"], 120)
        self.assertEqual(payload["cycle"]["since_boot"], 0)

    def test_state_query_builds_snapshot_from_component_state_when_runtime_state_missing(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "99")
        self.redis.set(
            SLEEP_STATE_KEY,
            json.dumps({
                "energy": 55.0,
                "mode": "drowsy",
                "last_light_sleep_cycle": 0,
                "last_deep_sleep_cycle": 0,
                "last_deep_sleep_at": "2026-04-24T00:00:00+00:00",
                "summary": "drowsy",
            }),
        )
        self.redis.set(
            EMOTION_STATE_KEY,
            json.dumps({
                "dimensions": {
                    "curiosity": 0.7,
                    "calm": 0.2,
                    "satisfaction": 0.4,
                    "concern": 0.1,
                    "frustration": 0.3,
                },
                "dominant": "curiosity",
                "summary": "curious",
                "updated_at": "2026-04-24T00:00:00+00:00",
            }),
        )

        response = self.client.get(
            "/api/state",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "waking")
        self.assertEqual(body["energy"], 55.0)
        self.assertEqual(body["cycle"]["current"], 99)
        self.assertEqual(body["emotions"]["satisfied"], 0.4)

    def test_stimuli_query_reads_pending_queue(self) -> None:
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            json.dumps({
                "stimulus_id": "stim_late",
                "type": "time",
                "priority": 4,
                "source": "system:time",
                "content": "later",
                "timestamp": "2026-03-27T12:00:02+00:00",
                "action_id": None,
                "metadata": {},
            }, ensure_ascii=False),
        )
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            json.dumps({
                "stimulus_id": "stim_urgent",
                "type": "conversation",
                "priority": 1,
                "source": "telegram:1",
                "content": "hello",
                "timestamp": "2026-03-27T12:00:01+00:00",
                "action_id": None,
                "metadata": {},
            }, ensure_ascii=False),
        )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["stimulus_id"], "stim_late")
        self.assertEqual(body["items"][0]["bucket"], "noticed")
        self.assertEqual(body["items"][0]["summary"], "later")

    def test_stimuli_query_buckets_current_action_echoes(self) -> None:
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            json.dumps({
                "stimulus_id": "stim_current_echo",
                "type": "action_result",
                "priority": 2,
                "source": "action:act_1",
                "content": "current action result",
                "timestamp": "2026-03-27T12:00:02+00:00",
                "action_id": "act_1",
                "metadata": {
                    "origin": "action",
                    "action_type": "weather",
                    "status": "succeeded",
                },
            }, ensure_ascii=False),
        )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["stimulus_id"], "stim_current_echo")
        self.assertEqual(body["items"][0]["bucket"], "echo_current")
        self.assertEqual(body["items"][0]["summary"], "current action result")

    def test_stimuli_query_reads_consumed_action_echoes(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "10")
        self.redis.rpush(
            RECENT_ACTION_ECHO_KEY,
            _recent_action_echo_record(
                cycle_id=9,
                stimulus_id="stim_echo",
                action_id="act_1",
                content="weather returned",
                timestamp="2026-03-27T12:00:03+00:00",
            ),
        )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["stimulus_id"], "stim_echo")
        self.assertEqual(body["items"][0]["bucket"], "echo_recent")
        self.assertEqual(body["items"][0]["summary"], "weather returned")

    def test_stimuli_query_dedupes_current_and_recent_echo_for_same_action(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "10")
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            _action_result_queue_record(
                stimulus_id="stim_current_echo",
                action_id="act_1",
                content="current action result",
                timestamp="2026-03-27T12:00:01+00:00",
            ),
        )
        self.redis.rpush(
            RECENT_ACTION_ECHO_KEY,
            _recent_action_echo_record(
                cycle_id=9,
                stimulus_id="stim_recent_echo",
                action_id="act_1",
                content="newer cached action result",
                timestamp="2026-03-27T12:00:03+00:00",
            ),
        )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["stimulus_id"], "stim_current_echo")
        self.assertEqual(body["items"][0]["bucket"], "echo_current")
        self.assertEqual(body["items"][0]["source"], "action:act_1")

    def test_stimuli_query_dedupes_multiple_actions_across_echo_buckets(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "10")
        for action_id in ["act_1", "act_2"]:
            self.redis.rpush(
                STIMULUS_REDIS_KEY,
                _action_result_queue_record(
                    stimulus_id=f"stim_current_{action_id}",
                    action_id=action_id,
                    content=f"current {action_id}",
                    timestamp=f"2026-03-27T12:00:0{1 if action_id == 'act_1' else 2}+00:00",
                ),
            )
            self.redis.rpush(
                RECENT_ACTION_ECHO_KEY,
                _recent_action_echo_record(
                    cycle_id=9,
                    stimulus_id=f"stim_recent_{action_id}",
                    action_id=action_id,
                    content=f"recent {action_id}",
                    timestamp=f"2026-03-27T12:00:0{3 if action_id == 'act_1' else 4}+00:00",
                ),
            )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(
            {item["source"]: item["bucket"] for item in body["items"]},
            {"action:act_1": "echo_current", "action:act_2": "echo_current"},
        )
        self.assertEqual(
            {item["stimulus_id"] for item in body["items"]},
            {"stim_current_act_1", "stim_current_act_2"},
        )

    def test_stimuli_query_dedupes_passive_by_stimulus_id_only(self) -> None:
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            _stimulus_queue_record(
                stimulus_id="stim_time",
                stimulus_type="time",
                source="system:time",
                content="older time",
                timestamp="2026-03-27T12:00:01+00:00",
            ),
        )
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            _stimulus_queue_record(
                stimulus_id="stim_time",
                stimulus_type="time",
                source="system:time",
                content="newer time",
                timestamp="2026-03-27T12:00:03+00:00",
            ),
        )
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            _stimulus_queue_record(
                stimulus_id="stim_time_other",
                stimulus_type="time",
                source="system:time",
                content="other time",
                timestamp="2026-03-27T12:00:02+00:00",
            ),
        )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(
            [(item["stimulus_id"], item["summary"]) for item in body["items"]],
            [("stim_time", "newer time"), ("stim_time_other", "other time")],
        )

    def test_stimuli_query_merges_pending_and_consumed_by_timestamp(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "10")
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            json.dumps({
                "stimulus_id": "stim_pending",
                "type": "time",
                "priority": 1,
                "source": "system:time",
                "content": "pending message",
                "timestamp": "2026-03-27T12:00:01+00:00",
                "action_id": None,
                "metadata": {},
            }, ensure_ascii=False),
        )
        self.redis.rpush(
            RECENT_ACTION_ECHO_KEY,
            _recent_action_echo_record(
                cycle_id=9,
                stimulus_id="stim_echo",
                action_id="act_1",
                content="newer action echo",
                timestamp="2026-03-27T12:00:03+00:00",
            ),
        )

        response = self.client.get(
            "/api/stimuli?limit=20",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(
            [(item["stimulus_id"], item["bucket"]) for item in body["items"]],
            [("stim_echo", "echo_recent"), ("stim_pending", "noticed")],
        )

    def test_stimuli_query_limits_merged_items_after_sort(self) -> None:
        self.redis.set(LATEST_CYCLE_KEY, "10")
        self.redis.rpush(
            STIMULUS_REDIS_KEY,
            json.dumps({
                "stimulus_id": "stim_pending",
                "type": "time",
                "priority": 1,
                "source": "system:time",
                "content": "pending message",
                "timestamp": "2026-03-27T12:00:01+00:00",
                "action_id": None,
                "metadata": {},
            }, ensure_ascii=False),
        )
        self.redis.rpush(
            RECENT_ACTION_ECHO_KEY,
            _recent_action_echo_record(
                cycle_id=9,
                stimulus_id="stim_echo",
                action_id="act_1",
                content="newer action echo",
                timestamp="2026-03-27T12:00:03+00:00",
            ),
        )

        response = self.client.get(
            "/api/stimuli?limit=1",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["stimulus_id"], "stim_echo")
        self.assertEqual(body["items"][0]["bucket"], "echo_recent")

    def test_conversation_history_skips_malformed_items(self) -> None:
        self.redis.rpush(CONVERSATION_HISTORY_KEY, "{bad json")
        self.redis.rpush(
            CONVERSATION_HISTORY_KEY,
            _conversation_history_entry(
                entry_id="conv_2",
                role="assistant",
                content="你好，我在。",
                timestamp="2026-03-27T12:00:01+00:00",
            ),
        )

        response = self.client.get(
            "/api/conversation?limit=10",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["role"], "assistant")

    def test_action_confirm_pushes_control_message(self) -> None:
        response = self.client.post(
            "/api/action/confirm",
            headers={
                "X-API-Token": "token_backend",
                "Authorization": "Bearer token_alice",
            },
            json={"action_id": "act_1", "approved": True, "note": "go"},
        )

        self.assertEqual(response.status_code, 200)
        stored = self.redis.lists[ACTION_CONTROL_KEY][0]
        self.assertIn('"action_id": "act_1"', stored)
        self.assertIn('"approved": true', stored)

    def test_action_confirm_returns_503_when_enqueue_fails(self) -> None:
        class FailingRedis(FakeRedis):
            def rpush(self, key, value):
                raise redis_lib.exceptions.ConnectionError("boom")

        app = create_app(
            config={"admins": [{"username": "alice", "token": "token_alice"}]},
            redis_client=FailingRedis(),
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/action/confirm",
                headers={
                    "X-API-Token": "token_backend",
                    "Authorization": "Bearer token_alice",
                },
                json={"action_id": "act_1", "approved": True},
            )

        self.assertEqual(response.status_code, 503)

    def test_stream_requires_api_token(self) -> None:
        response = self.client.get("/api/stream")

        self.assertEqual(response.status_code, 401)

    def test_stream_yields_status_chunk(self) -> None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={"admins": [{"username": "alice", "token": "token_alice"}]},
                    backend_api_token="token_backend",
                    redis=self.redis,
                ),
            ),
        )

        response = stream_events(request=_as_request(request), api_client="backend_api")
        first_chunk = asyncio.run(_read_first_stream_chunk(response.body_iterator))
        if isinstance(first_chunk, bytes):
            first_chunk = first_chunk.decode("utf-8")

        self.assertIn("event: status", first_chunk)

    def test_stream_yields_initial_snapshots(self) -> None:
        self.redis.set(
            RUNTIME_STATE_KEY,
            json.dumps({
                "mode": "waking",
                "energy": 80.0,
                "energy_per_cycle": 0.2,
                "next_drowsy_cycle": 300,
                "emotions": {
                    "curiosity": 0.1,
                    "calm": 0.2,
                    "satisfied": 0.3,
                    "concern": 0.4,
                    "frustration": 0.5,
                },
                "cycle": {"current": 10, "since_boot": 10, "avg_seconds": 1.5},
                "uptime": {"started_at": "2026-04-24T04:48:00+00:00", "seconds": 60},
            }, ensure_ascii=False),
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={"admins": [{"username": "alice", "token": "token_alice"}]},
                    backend_api_token="token_backend",
                    redis=self.redis,
                ),
            ),
        )

        response = stream_events(request=_as_request(request), api_client="backend_api")
        chunks = asyncio.run(_read_stream_chunks(response.body_iterator, 6))

        self.assertIn("event: status", chunks[0])
        self.assertIn("event: state", chunks[1])
        self.assertIn('"mode": "waking"', chunks[1])
        self.assertIn("event: thoughts", chunks[2])
        self.assertIn("event: actions", chunks[3])
        self.assertIn("event: conversation", chunks[4])
        self.assertIn("event: stimuli", chunks[5])

    def test_stream_stops_and_closes_pubsub_after_disconnect(self) -> None:
        pubsub = FakePubSub([])

        class ClosingRedis(FakeRedis):
            @staticmethod
            def pubsub(ignore_subscribe_messages=True):
                _ = ignore_subscribe_messages
                return pubsub

        class DisconnectAfterInitialChunks:
            def __init__(self) -> None:
                self.calls = 0

            async def is_disconnected(self) -> bool:
                self.calls += 1
                return self.calls > 6

        disconnect = DisconnectAfterInitialChunks()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={"admins": [{"username": "alice", "token": "token_alice"}]},
                    backend_api_token="token_backend",
                    redis=ClosingRedis(),
                ),
            ),
            is_disconnected=disconnect.is_disconnected,
        )

        response = stream_events(request=_as_request(request), api_client="backend_api")

        async def collect_until_disconnect() -> list[str]:
            chunks: list[str] = []
            iterator = aiter(response.body_iterator)
            for _ in range(6):
                chunks.append(str(await anext(iterator)))
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)
            return chunks

        chunks = asyncio.run(collect_until_disconnect())

        self.assertIn("event: status", chunks[0])
        self.assertTrue(pubsub.closed)
        self.assertEqual(pubsub.timeouts, [])

    def test_stream_pubsub_poll_uses_short_timeout(self) -> None:
        pubsub = FakePubSub([])

        chunk = asyncio.run(_stream_next_chunk(pubsub))

        self.assertIsNone(chunk)
        self.assertEqual(pubsub.timeouts, [PUBSUB_POLL_TIMEOUT_SECONDS])

    def test_recent_thoughts_query(self) -> None:
        self.redis.sorted_sets["seedwake:thoughts"] = [
            json.dumps({"thought_id": "C1-1", "content": "a"}, ensure_ascii=False),
            json.dumps({"thought_id": "C1-2", "content": "b"}, ensure_ascii=False),
        ]

        response = self.client.get(
            "/api/thoughts?limit=2",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["items"][0]["thought_id"], "C1-1")

    def test_recent_thoughts_query_skips_malformed_items(self) -> None:
        self.redis.sorted_sets["seedwake:thoughts"] = [
            "{bad json",
            json.dumps({"thought_id": "C1-2", "content": "b"}, ensure_ascii=False),
        ]

        response = self.client.get(
            "/api/thoughts?limit=2",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["thought_id"], "C1-2")

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
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["action_id"], "act_1")
        self.assertEqual(body["items"][0]["summary"]["key"], "action.running_status")

    def test_actions_query_skips_malformed_action_record(self) -> None:
        self.redis.hset("seedwake:actions", "bad", "{bad json")
        self.redis.hset(
            "seedwake:actions",
            "act_1",
            json.dumps({
                "action_id": "act_1",
                "status": "running",
                "submitted_at": "2026-03-27T12:00:00+00:00",
            }),
        )

        response = self.client.get(
            "/api/actions",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["action_id"], "act_1")
        self.assertEqual(body["items"][0]["summary"]["key"], "action.running_status")

    def test_actions_query_exposes_public_summary_only(self) -> None:
        self.redis.hset(
            "seedwake:actions",
            "act_1",
            json.dumps({
                "action_id": "act_1",
                "status": "succeeded",
                "submitted_at": "2026-03-27T12:00:00+00:00",
                "result": {
                    "ok": True,
                    "summary": "done",
                    "summary_key": "action.completed_with_summary",
                    "summary_params": {"summary": "done"},
                    "data": {},
                    "error": None,
                    "run_id": None,
                    "session_key": None,
                    "transport": "native",
                },
            }, ensure_ascii=False),
        )

        response = self.client.get(
            "/api/actions",
            headers={"X-API-Token": "token_backend"},
        )

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["summary"]["key"], "action.completed_with_summary")
        self.assertEqual(item["summary"]["params"], {"summary": "done"})
        self.assertNotIn("summary_key", item["result"])
        self.assertNotIn("summary_params", item["result"])
