import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
import redis as redis_lib

# noinspection PyProtectedMember
from core.main import (
    _maybe_reconnect_pg,
    _maybe_reconnect_redis,
    _next_cycle_id,
    _open_log,
    _open_prompt_log,
)
from core.memory.short_term import LATEST_CYCLE_KEY
from core.memory.identity import load_identity
from core.memory.long_term import LongTermMemory
# noinspection PyProtectedMember
from core.memory.short_term import ShortTermMemory, _thought_to_dict, _dict_to_thought
from core.thought_parser import Thought


def _make_thought(cycle_id: int = 1, index: int = 1, content: str = "test") -> Thought:
    return Thought(
        thought_id=f"C{cycle_id}-{index}",
        cycle_id=cycle_id,
        index=index,
        type="思考",
        content=content,
    )


class ShortTermMemoryFallbackTests(unittest.TestCase):
    """Test in-memory deque fallback (redis_client=None)."""

    def test_append_and_get_context(self) -> None:
        stm = ShortTermMemory(redis_client=None, context_window=2)
        t1 = [_make_thought(1, i, f"c1-{i}") for i in range(1, 4)]
        t2 = [_make_thought(2, i, f"c2-{i}") for i in range(1, 4)]
        t3 = [_make_thought(3, i, f"c3-{i}") for i in range(1, 4)]
        stm.append(t1)
        stm.append(t2)
        stm.append(t3)

        context = stm.get_context()
        # context_window=2 → last 6 thoughts
        self.assertEqual(len(context), 6)
        self.assertEqual(context[0].content, "c2-1")
        self.assertEqual(context[-1].content, "c3-3")

    def test_empty_context(self) -> None:
        stm = ShortTermMemory(redis_client=None, context_window=10)
        self.assertEqual(stm.get_context(), [])


def _seed_existing_history(redis_client: MagicMock, *, latest_cycle_id: str | None) -> None:
    redis_client.get.return_value = latest_cycle_id
    redis_client.zrange.return_value = [json.dumps(_thought_to_dict(_make_thought(8, 1, "old")))]
    redis_client.eval.return_value = 9


class ShortTermMemoryRedisDegradationTests(unittest.TestCase):
    """Test that Redis failures degrade to deque instead of crashing."""

    def test_append_degrades_on_redis_error(self) -> None:
        mock_redis = MagicMock()
        mock_redis.zadd.side_effect = redis_lib.exceptions.ConnectionError("Redis gone")
        stm = ShortTermMemory(redis_client=mock_redis, context_window=10)

        thoughts = [_make_thought(1, 1, "should survive")]
        stm.append(thoughts)  # must not raise

        self.assertIsNone(stm._redis)  # degraded
        context = stm.get_context()
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0].content, "should survive")

    def test_get_context_degrades_on_redis_error(self) -> None:
        mock_redis = MagicMock()
        mock_redis.zrange.side_effect = redis_lib.exceptions.ConnectionError("Redis gone")
        stm = ShortTermMemory(redis_client=mock_redis, context_window=10)

        # Pre-populate deque
        stm._deque.append(_make_thought(1, 1, "from deque"))

        context = stm.get_context()  # must not raise
        self.assertIsNone(stm._redis)  # degraded
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0].content, "from deque")

    def test_attach_redis_rehydrates_shadow_copy(self) -> None:
        mock_redis = MagicMock()
        mock_redis.zcard.return_value = 1
        stm = ShortTermMemory(redis_client=None, context_window=10)
        stm.append([_make_thought(1, 1, "from deque")])

        attached = stm.attach_redis(mock_redis)

        self.assertTrue(attached)
        mock_redis.zadd.assert_called_once()
        mock_redis.delete.assert_not_called()


class ThoughtSerializationTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        t = _make_thought(5, 2, "序列化测试")
        d = _thought_to_dict(t)
        restored = _dict_to_thought(d)

        self.assertEqual(restored.thought_id, "C5-2")
        self.assertEqual(restored.content, "序列化测试")
        self.assertEqual(restored.type, "思考")


class IdentityTests(unittest.TestCase):
    def test_fallback_to_bootstrap_when_no_pg(self) -> None:
        bootstrap = {"self_description": "我是测试"}
        identity = load_identity(None, bootstrap)
        self.assertEqual(identity, bootstrap)

    def test_load_from_db(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("self_description", "我是数据库版本"),
            ("core_goals", "探索"),
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        identity = load_identity(mock_conn, {"self_description": "我是配置版本"})

        self.assertEqual(identity["self_description"], "我是数据库版本")
        self.assertEqual(identity["core_goals"], "探索")

    def test_missing_sections_filled_from_bootstrap(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("self_description", "DB版本"),
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        bootstrap = {
            "self_description": "配置版本",
            "core_goals": "探索",
            "self_understanding": "理解",
        }
        identity = load_identity(mock_conn, bootstrap)

        self.assertEqual(identity["self_description"], "DB版本")
        self.assertEqual(identity["core_goals"], "探索")
        self.assertEqual(identity["self_understanding"], "理解")

    def test_fallback_to_bootstrap_on_db_error(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = psycopg.DatabaseError("table does not exist")
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        bootstrap = {"self_description": "我是配置版本"}
        identity = load_identity(mock_conn, bootstrap)

        self.assertEqual(identity, bootstrap)


class LongTermMemoryTests(unittest.TestCase):
    def test_unavailable_when_no_connection(self) -> None:
        ltm = LongTermMemory(None)
        self.assertFalse(ltm.available)
        self.assertEqual(ltm.search([0.1, 0.2]), [])
        self.assertIsNone(ltm.store("test", "episodic", [0.1]))

    def test_store_rolls_back_on_error(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = psycopg.DatabaseError("insert failed")
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        ltm = LongTermMemory(mock_conn)

        with self.assertRaises(psycopg.Error):
            ltm.store("test", "episodic", [0.1])

        mock_conn.rollback.assert_called_once()

    def test_search_rolls_back_on_error(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = psycopg.DatabaseError("select failed")
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        ltm = LongTermMemory(mock_conn)

        with self.assertRaises(psycopg.Error):
            ltm.search([0.1, 0.2])

        mock_conn.rollback.assert_called_once()

    def test_resolve_telegram_target_for_entity(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ('关系: 管理员。Telegram chat id: 123456。',),
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        ltm = LongTermMemory(mock_conn)

        target = ltm.resolve_telegram_target_for_entity("person:alice")

        self.assertEqual(target, "telegram:123456")

    def test_resolve_telegram_target_accepts_legacy_entity_prefix(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [],
            [('关系: 管理员。telegram:123456。',)],
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        ltm = LongTermMemory(mock_conn)

        target = ltm.resolve_telegram_target_for_entity("person:alice")

        self.assertEqual(target, "telegram:123456")


class RecoveryTests(unittest.TestCase):
    @patch("core.main._connect_redis")
    def test_maybe_reconnect_redis_restores_client(self, mock_connect) -> None:
        mock_redis = MagicMock()
        mock_redis.zcard.return_value = 1
        mock_connect.return_value = mock_redis
        stm = ShortTermMemory(redis_client=None, context_window=10)
        stm.append([_make_thought(1, 1, "from deque")])

        last_attempt = _maybe_reconnect_redis(
            None, stm, now=10.0, last_attempt=0.0, interval=5.0,
        )

        self.assertEqual(last_attempt, 10.0)
        self.assertTrue(stm.redis_available)
        mock_redis.delete.assert_not_called()

    @patch("core.main.load_identity", return_value={"self_description": "DB版本"})
    @patch("core.main._connect_pg")
    def test_maybe_reconnect_pg_restores_connection(
        self,
        mock_connect,
        mock_load_identity,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        ltm = LongTermMemory(None)

        identity, last_attempt = _maybe_reconnect_pg(
            None,
            ltm,
            {"self_description": "配置版本"},
            {"self_description": "配置版本"},
            now=10.0,
            last_attempt=0.0,
            interval=5.0,
        )

        self.assertEqual(identity, {"self_description": "DB版本"})
        self.assertEqual(last_attempt, 10.0)
        self.assertTrue(ltm.available)
        mock_load_identity.assert_called_once_with(mock_conn, {"self_description": "配置版本"})


class CoreLogHandleTests(unittest.TestCase):
    def test_open_log_skips_duplicate_core_log_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = {"runtime": {"logging": {"directory": tmp_dir}}}
            log_path = str(Path(tmp_dir) / "core.log")

            handle = _open_log(log_path, config)

            self.assertIsNone(handle)

    def test_open_log_opens_distinct_plain_text_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = {"runtime": {"logging": {"directory": tmp_dir}}}
            plain_log = Path(tmp_dir) / "cycles.txt"

            handle = _open_log(str(plain_log), config)

            self.assertIsNotNone(handle)
            assert handle is not None
            handle.write("ok\n")
            handle.close()
            self.assertTrue(plain_log.exists())

    def test_open_prompt_log_defaults_to_prompt_txt_and_truncates_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = {"runtime": {"logging": {"directory": tmp_dir}}}
            prompt_path = Path(tmp_dir) / "prompt.txt"
            prompt_path.write_text("old", encoding="utf-8")

            handle = _open_prompt_log(config, plain_log_path=None)

            handle.write("new\n")
            handle.close()
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), "new\n")


class CycleCounterTests(unittest.TestCase):
    def test_next_cycle_id_uses_redis_counter(self) -> None:
        redis_client = MagicMock()
        redis_client.get.return_value = "0"
        redis_client.zrange.return_value = []
        redis_client.eval.return_value = 7
        stm = ShortTermMemory(redis_client=redis_client, context_window=10)

        cycle_id = _next_cycle_id(stm, 3)

        self.assertEqual(cycle_id, 7)
        redis_client.eval.assert_called_once()

    def test_next_cycle_id_repairs_redis_counter_when_it_lags_local_state(self) -> None:
        redis_client = MagicMock()
        redis_client.get.return_value = "0"
        redis_client.zrange.return_value = []
        redis_client.eval.return_value = 6
        stm = ShortTermMemory(redis_client=redis_client, context_window=10)

        cycle_id = _next_cycle_id(stm, 5)

        self.assertEqual(cycle_id, 6)
        redis_client.eval.assert_called_once()

    def test_next_cycle_id_falls_back_to_local_increment_without_redis(self) -> None:
        stm = ShortTermMemory(redis_client=None, context_window=10)

        cycle_id = _next_cycle_id(stm, 9)

        self.assertEqual(cycle_id, 10)

    def test_next_cycle_id_bootstraps_from_existing_redis_history_when_counter_missing(self) -> None:
        redis_client = MagicMock()
        _seed_existing_history(redis_client, latest_cycle_id=None)
        stm = ShortTermMemory(redis_client=redis_client, context_window=10)

        cycle_id = _next_cycle_id(stm, 0)

        self.assertEqual(cycle_id, 9)
        self.assertEqual(redis_client.eval.call_args.args[3], LATEST_CYCLE_KEY)
        self.assertEqual(redis_client.eval.call_args.args[4], 8)

    def test_next_cycle_id_prefers_existing_history_when_latest_key_is_stale(self) -> None:
        redis_client = MagicMock()
        _seed_existing_history(redis_client, latest_cycle_id="5")
        stm = ShortTermMemory(redis_client=redis_client, context_window=10)

        cycle_id = _next_cycle_id(stm, 0)

        self.assertEqual(cycle_id, 9)
        self.assertEqual(redis_client.eval.call_args.args[4], 8)

    def test_next_cycle_id_does_not_use_stale_redis_client_after_degradation(self) -> None:
        redis_client = MagicMock()
        redis_client.get.return_value = None
        redis_client.zrange.side_effect = ValueError("bad json")
        stm = ShortTermMemory(redis_client=redis_client, context_window=10)

        cycle_id = _next_cycle_id(stm, 3)

        self.assertEqual(cycle_id, 4)
        self.assertFalse(stm.redis_available)
        redis_client.eval.assert_not_called()


if __name__ == "__main__":
    unittest.main()
