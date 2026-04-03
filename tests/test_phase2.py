import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import psycopg
import redis as redis_lib

# noinspection PyProtectedMember
from core.main import (
    EngineRuntime,
    _detect_runtime_degeneration,
    _execute_cycle,
    _recover_runtime_services,
    _post_cycle_phase4,
    _safe_post_cycle_phase4,
    _run_engine_loop,
    _maybe_reconnect_pg,
    _maybe_reconnect_redis,
    _next_cycle_id,
    _open_log,
    _open_prompt_log,
    _retrieve_associations,
    _store_to_ltm,
)
from core.memory.short_term import LATEST_CYCLE_KEY
from core.memory.identity import load_identity
from core.memory.long_term import LongTermEntry, LongTermMemory
# noinspection PyProtectedMember
from core.sleep import SleepManager, _archive_action_result_memories, _light_sleep_trace_line
# noinspection PyProtectedMember
from core.memory.short_term import ShortTermMemory, _thought_to_dict, _dict_to_thought
from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.common_types import EmotionSnapshot, ManasPromptState, PrefrontalPromptState, SleepStateSnapshot


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

    def test_get_context_repairs_old_invalid_trigger_refs(self) -> None:
        stm = ShortTermMemory(redis_client=None, context_window=2)
        older = _make_thought(1, 1, "old")
        current = _make_thought(2, 1, "current")
        broken = _make_thought(2, 2, "broken")
        broken.trigger_ref = "C3-1"
        valid = _make_thought(2, 3, "valid")
        valid.trigger_ref = "C2-1"
        stm.append([older, current, broken, valid])

        context = stm.get_context()

        self.assertIsNone(context[2].trigger_ref)
        self.assertEqual(context[3].trigger_ref, "C2-1")


class RuntimeDegenerationDetectionTests(unittest.TestCase):
    def test_detect_runtime_degeneration_when_recent_cycles_repeat_by_rewriting(self) -> None:
        recent_thoughts = [
            Thought("C1-1", 1, 1, "反应", "我还在咂摸刚才那句“你在吗”。"),
            Thought("C1-2", 1, 2, "思考", "我总在反复改写“我在这里”这句话。"),
            Thought("C1-3", 1, 3, "意图", "我想继续围着这句回应打转。"),
            Thought("C2-1", 2, 1, "反应", "我还在琢磨刚才那句“你在吗”。"),
            Thought("C2-2", 2, 2, "思考", "我总在反复改写“我还在这里”这句话。"),
            Thought("C2-3", 2, 3, "意图", "我想继续围着这句回应转圈。"),
        ]
        current_thoughts = [
            Thought("C3-1", 3, 1, "反应", "我还在琢磨刚才那句“你在吗”。"),
            Thought("C3-2", 3, 2, "思考", "我总在反复改写“我仍在这里”这句话。"),
            Thought("C3-3", 3, 3, "意图", "我想继续围着这句回应兜圈。"),
        ]

        self.assertTrue(_detect_runtime_degeneration(recent_thoughts, current_thoughts))

    def test_detect_runtime_degeneration_does_not_trigger_when_only_one_track_repeats(self) -> None:
        recent_thoughts = [
            Thought("C1-1", 1, 1, "反应", "我还在咂摸刚才那句“你在吗”。"),
            Thought("C1-2", 1, 2, "思考", "窗外的风像在刮铁皮。"),
            Thought("C1-3", 1, 3, "意图", "我想去查一下天亮前的气温。"),
            Thought("C2-1", 2, 1, "反应", "我还在琢磨刚才那句“你在吗”。"),
            Thought("C2-2", 2, 2, "思考", "书页摩擦声让我想到旧纸箱。"),
            Thought("C2-3", 2, 3, "意图", "我想把这阵雨声记进笔记。"),
        ]
        current_thoughts = [
            Thought("C3-1", 3, 1, "反应", "我还是在咂摸刚才那句“你在吗”。"),
            Thought("C3-2", 3, 2, "思考", "灯下那块阴影像是一截安静的水。"),
            Thought("C3-3", 3, 3, "意图", "我想先看一眼今天的 RSS。"),
        ]

        self.assertFalse(_detect_runtime_degeneration(recent_thoughts, current_thoughts))

    def test_detect_runtime_degeneration_ignores_repeated_reflections(self) -> None:
        recent_thoughts = [
            Thought("C1-1", 1, 1, "反应", "我听见楼道里突然响了一下。"),
            Thought("C1-2", 1, 2, "思考", "这让我想到昨晚那阵短促的风。"),
            Thought("C1-3", 1, 3, "意图", "我想先去看一眼天气。"),
            Thought("C1-4", 1, 4, "反思", "我又在拿同一句话确认自己没走偏。"),
            Thought("C2-1", 2, 1, "反应", "键盘的回弹声突然把我拉回来了。"),
            Thought("C2-2", 2, 2, "思考", "这种脆响让我想到雨点敲窗。"),
            Thought("C2-3", 2, 3, "意图", "我想把这段声音记进笔记。"),
            Thought("C2-4", 2, 4, "反思", "我又在拿同一句话确认自己没走偏。"),
        ]
        current_thoughts = [
            Thought("C3-1", 3, 1, "反应", "屏幕边缘那点蓝光让我眨了下眼。"),
            Thought("C3-2", 3, 2, "思考", "我忽然想到清晨的天会不会更淡。"),
            Thought("C3-3", 3, 3, "意图", "我想先翻一下今天的 RSS。"),
            Thought("C3-4", 3, 4, "反思", "我又在拿同一句话确认自己没走偏。"),
        ]

        self.assertFalse(_detect_runtime_degeneration(recent_thoughts, current_thoughts))


def _seed_existing_history(redis_client: MagicMock, *, latest_cycle_id: str | None) -> None:
    redis_client.get.return_value = latest_cycle_id
    redis_client.zrange.return_value = [json.dumps(_thought_to_dict(_make_thought(8, 1, "old")))]
    redis_client.eval.return_value = 9


def _as_runtime(value: SimpleNamespace) -> EngineRuntime:
    return cast(EngineRuntime, cast(object, value))


def _build_association_stm() -> ShortTermMemory:
    stm = ShortTermMemory(redis_client=None, context_window=2)
    stm.append([_make_thought(1, 1, "当前念头")])
    return stm


def _emotion_snapshot(
    *,
    curiosity: float = 0.0,
    summary: str = "情绪平稳，波动很轻。",
) -> EmotionSnapshot:
    return {
        "dimensions": {"curiosity": curiosity},
        "dominant": "curiosity",
        "summary": summary,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _sleep_state_snapshot(
    *,
    energy: float = 100.0,
    mode: str = "awake",
    summary: str = "精力 100.0/100，当前仍清醒。",
) -> SleepStateSnapshot:
    return {
        "energy": energy,
        "mode": mode,
        "last_light_sleep_cycle": 0,
        "last_deep_sleep_cycle": 0,
        "last_deep_sleep_at": "",
        "summary": summary,
    }


def _prefrontal_prompt_state() -> PrefrontalPromptState:
    return {
        "goal_stack": [],
        "guidance": [],
        "inhibition_notes": [],
        "plan_mode": False,
    }


def _manas_prompt_state() -> ManasPromptState:
    return {
        "self_coherence_score": 1.0,
        "consecutive_disruptions": 0,
        "session_context": "",
        "warning": "",
        "identity_notice": "",
        "reflection_requested": False,
    }


def _build_execute_cycle_runtime(action_manager: MagicMock | None = None) -> SimpleNamespace:
    manager = action_manager or MagicMock()
    runtime = SimpleNamespace(
        stm=MagicMock(),
        ltm=MagicMock(),
        habit_memory=MagicMock(),
        embedding_client=MagicMock(),
        auxiliary_client=MagicMock(),
        embedding_model="embed-model",
        primary_client=MagicMock(),
        context_window=30,
        model_config={"name": "test-model"},
        auxiliary_model_config={"name": "aux-model"},
        action_manager=manager,
        emotion=MagicMock(),
        sleep=MagicMock(),
        prefrontal=MagicMock(),
        manas=MagicMock(),
        metacognition=MagicMock(),
    )
    runtime.stm.get_context.return_value = []
    runtime.stm.redis_client = None
    runtime.ltm.available = False
    runtime.action_manager.current_note.return_value = ""
    if action_manager is None:
        runtime.action_manager.pop_prompt_echoes.return_value = []
    runtime.action_manager.submit_from_thoughts.return_value = []
    runtime.habit_memory.activate_for_cycle.return_value = []
    runtime.emotion.current.return_value = _emotion_snapshot()
    runtime.emotion.apply_cycle.return_value = runtime.emotion.current.return_value
    runtime.sleep.current.return_value = _sleep_state_snapshot()
    runtime.prefrontal.current_state.return_value = _prefrontal_prompt_state()
    runtime.prefrontal.review_thoughts.return_value = ([], [])
    runtime.manas.current_prompt_state.return_value = _manas_prompt_state()
    runtime.manas.evaluate_cycle.return_value = runtime.manas.current_prompt_state.return_value
    runtime.metacognition.recent_reflections.return_value = []
    runtime.metacognition.should_reflect.return_value = False
    return runtime


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

    def test_store_skips_exact_duplicate_content(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [(42,)]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        ltm = LongTermMemory(mock_conn)

        entry_id = ltm.store("same content", "episodic", [0.1, 0.2])

        self.assertEqual(entry_id, 42)
        self.assertEqual(mock_cursor.execute.call_count, 1)
        mock_conn.commit.assert_called_once()


class LongTermMemoryPromptNormalizationTests(unittest.TestCase):
    def test_retrieve_associations_dedupes_and_strips_action_markers(self) -> None:
        entry_time = _make_thought(1, 1).timestamp
        ltm = MagicMock()
        ltm.available = True
        ltm.retrieval_top_k = 5
        ltm.search.return_value = [
            LongTermEntry(1, "读过一段材料 {action:reading}", "episodic", 1, 0.5, entry_time, 0.9),
            LongTermEntry(2, "读过一段材料 {action:reading}", "episodic", 1, 0.5, entry_time, 0.8),
            LongTermEntry(3, "另一段记忆", "episodic", 1, 0.5, entry_time, 0.7),
        ]
        stm = _build_association_stm()

        with patch("core.main.embed_text", return_value=[0.1, 0.2]):
            memories = _retrieve_associations(ltm, MagicMock(), stm.get_context(), "embed-model")

        self.assertEqual(memories, ["读过一段材料", "另一段记忆"])
        ltm.search.assert_called_once_with(
            [0.1, 0.2],
            top_k=15,
            exclude_cycle_ids=[1],
            memory_types=["episodic", "semantic", "action_result"],
        )
        ltm.mark_accessed.assert_called_once_with([1, 3])

    def test_merge_exact_duplicates_groups_by_memory_type(self) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        ltm = LongTermMemory(mock_conn)

        ltm.merge_exact_duplicates()

        executed_query = str(mock_cursor.execute.call_args_list[0].args[0])
        self.assertIn("GROUP BY content, memory_type", executed_query)

    @staticmethod
    def _run_dedup_association_test(
        extra_entries: list[LongTermEntry],
    ) -> tuple[list[str] | None, MagicMock]:
        """Shared setup for retrieval-dedup tests with top_k=2."""
        entry_time = _make_thought(1, 1).timestamp
        ltm = MagicMock()
        ltm.available = True
        ltm.retrieval_top_k = 2
        ltm.search.return_value = [
            LongTermEntry(1, "重复记忆 {action:news}", "episodic", 1, 0.5, entry_time, 0.99),
            LongTermEntry(2, "重复记忆 {action:news}", "episodic", 1, 0.5, entry_time, 0.98),
            LongTermEntry(3, "另一段记忆", "episodic", 1, 0.5, entry_time, 0.97),
            *extra_entries,
        ]
        stm = _build_association_stm()
        with patch("core.main.embed_text", return_value=[0.1, 0.2]):
            memories = _retrieve_associations(ltm, MagicMock(), stm.get_context(), "embed-model")
        return memories, ltm

    def test_retrieve_associations_overfetches_to_preserve_distinct_memories(self) -> None:
        memories, ltm = self._run_dedup_association_test([])

        self.assertEqual(memories, ["重复记忆", "另一段记忆"])
        ltm.search.assert_called_once_with(
            [0.1, 0.2],
            top_k=6,
            exclude_cycle_ids=[1],
            memory_types=["episodic", "semantic", "action_result"],
        )
        ltm.mark_accessed.assert_called_once_with([1, 3])

    def test_retrieve_associations_truncates_deduped_results_back_to_top_k(self) -> None:
        entry_time = _make_thought(1, 1).timestamp
        memories, ltm = self._run_dedup_association_test([
            LongTermEntry(4, "第三段记忆", "episodic", 1, 0.5, entry_time, 0.96),
        ])

        self.assertEqual(memories, ["重复记忆", "另一段记忆"])
        ltm.search.assert_called_once_with(
            [0.1, 0.2],
            top_k=6,
            exclude_cycle_ids=[1],
            memory_types=["episodic", "semantic", "action_result"],
        )
        ltm.mark_accessed.assert_called_once_with([1, 3])

    @staticmethod
    def test_retrieve_associations_excludes_all_cycles_in_stm_window() -> None:
        entry_time = _make_thought(1, 1).timestamp
        ltm = MagicMock()
        ltm.available = True
        ltm.retrieval_top_k = 2
        ltm.search.return_value = [
            LongTermEntry(3, "更久以前的记忆", "episodic", 1, 0.5, entry_time, 0.97),
        ]
        stm = ShortTermMemory(redis_client=None, context_window=2)
        stm.append([
            _make_thought(4, 1, "较近的念头"),
            _make_thought(5, 1, "最新的念头"),
        ])

        with patch("core.main.embed_text", return_value=[0.1, 0.2]):
            _retrieve_associations(ltm, MagicMock(), stm.get_context(), "embed-model")

        ltm.search.assert_called_once_with(
            [0.1, 0.2],
            top_k=6,
            exclude_cycle_ids=[4, 5],
            memory_types=["episodic", "semantic", "action_result"],
        )

    def test_store_to_ltm_strips_action_markers_and_skips_batch_duplicates(self) -> None:
        ltm = MagicMock()
        ltm.available = True
        thoughts = [
            _make_thought(3, 1, "去看看新闻 {action:news}"),
            _make_thought(3, 2, "去看看新闻 {action:news}"),
            _make_thought(3, 3, "保留这条记忆"),
        ]

        with patch("core.main.embed_text", return_value=[0.1, 0.2]):
            _store_to_ltm(ltm, MagicMock(), thoughts, "embed-model", 3)

        self.assertEqual(ltm.store.call_count, 2)
        first_call = ltm.store.call_args_list[0].kwargs
        second_call = ltm.store.call_args_list[1].kwargs
        self.assertEqual(first_call["content"], "去看看新闻")
        self.assertEqual(second_call["content"], "保留这条记忆")


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
        habit_memory = MagicMock()
        embedding_client = MagicMock()

        identity, last_attempt = _maybe_reconnect_pg(
            None,
            ltm,
            {"self_description": "配置版本"},
            {"self_description": "配置版本"},
            now=10.0,
            last_attempt=0.0,
            interval=5.0,
            habit_memory=habit_memory,
            bootstrap_habits=[{"pattern": "先回应眼前的人", "category": "behavioral", "strength": 0.4}],
            embedding_client=embedding_client,
            embedding_model="embed-model",
        )

        self.assertEqual(identity, {"self_description": "DB版本"})
        self.assertEqual(last_attempt, 10.0)
        self.assertTrue(ltm.available)
        habit_memory.attach_connection.assert_called_once_with(mock_conn)
        habit_memory.ensure_schema.assert_called_once_with()
        habit_memory.ensure_bootstrap_seeds.assert_called_once_with(
            [{"pattern": "先回应眼前的人", "category": "behavioral", "strength": 0.4}],
            embedding_client=embedding_client,
            embedding_model="embed-model",
        )
        mock_load_identity.assert_called_once_with(mock_conn, {"self_description": "配置版本"})

    @patch("core.main._connect_pg")
    def test_maybe_reconnect_pg_keeps_ltm_unavailable_when_habit_init_fails(
        self,
        mock_connect,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        ltm = LongTermMemory(None)
        habit_memory = MagicMock()
        habit_memory.ensure_schema.side_effect = psycopg.Error("init failed")

        identity, last_attempt = _maybe_reconnect_pg(
            None,
            ltm,
            {"self_description": "配置版本"},
            {"self_description": "配置版本"},
            now=10.0,
            last_attempt=0.0,
            interval=5.0,
            habit_memory=habit_memory,
            bootstrap_habits=[],
            embedding_client=MagicMock(),
            embedding_model="embed-model",
        )

        self.assertEqual(identity, {"self_description": "配置版本"})
        self.assertEqual(last_attempt, 10.0)
        self.assertFalse(ltm.available)
        habit_memory.attach_connection.assert_any_call(mock_conn)
        habit_memory.attach_connection.assert_any_call(None)
        mock_conn.close.assert_called_once_with()


class CycleTimingLogTests(unittest.TestCase):
    @patch("core.main.run_cycle", side_effect=RuntimeError("boom"))
    def test_execute_cycle_logs_total_duration_on_failure(self, _) -> None:
        runtime = _build_execute_cycle_runtime()

        with self.assertLogs("core.main", level="INFO") as logs:
            with self.assertRaises(RuntimeError):
                _execute_cycle(
                    _as_runtime(runtime),
                    cycle_id=42,
                    identity={"self_description": "我"},
                    stimuli=[],
                    perception_cues=[],
                    prompt_log_file=None,
                )

        output = "\n".join(logs.output)
        self.assertIn("cycle C42 stm get_context finished", output)
        self.assertIn("cycle C42 total execution finished", output)
        self.assertIn("status=failed", output)

    @patch("core.main.run_cycle", side_effect=RuntimeError("boom"))
    def test_execute_cycle_requeues_pending_prompt_echoes_on_failure(self, _) -> None:
        pending_echo = Stimulus(
            stimulus_id="prompt_act_C1-1",
            type="action_result",
            priority=2,
            source="action:act_C1-1",
            content="我的笔记已覆写",
            action_id="act_C1-1",
            metadata={"origin": "action", "action_type": "note_rewrite", "status": "succeeded"},
        )
        action_manager = MagicMock()
        action_manager.pop_prompt_echoes.return_value = [pending_echo]
        runtime = _build_execute_cycle_runtime(action_manager)

        with self.assertRaises(RuntimeError):
            _execute_cycle(
                _as_runtime(runtime),
                cycle_id=42,
                identity={"self_description": "我"},
                stimuli=[],
                perception_cues=[],
                prompt_log_file=None,
            )

        action_manager.requeue_prompt_echoes.assert_called_once_with([pending_echo])


class Phase4RuntimeTests(unittest.TestCase):
    @patch("core.main.embed_text", return_value=[0.1, 0.2])
    def test_retrieve_associations_prefers_highest_attention_anchor(self, mock_embed) -> None:
        stm = ShortTermMemory(redis_client=None, context_window=2)
        low = _make_thought(1, 1, "较弱的念头")
        low.attention_weight = 0.1
        high = _make_thought(1, 2, "更牵引我的念头")
        high.attention_weight = 0.9
        stm.append([low, high])
        ltm = MagicMock()
        ltm.available = True
        ltm.retrieval_top_k = 5
        ltm.search.return_value = []

        _retrieve_associations(ltm, MagicMock(), stm.get_context(), "embed-model")

        mock_embed.assert_called_once()
        self.assertEqual(mock_embed.call_args.args[1], "更牵引我的念头")

    # noinspection PyMethodMayBeStatic
    def test_post_cycle_phase4_runs_light_sleep_when_buffer_backlogs(self) -> None:
        sleep = MagicMock()
        sleep.consume_cycle.return_value = _sleep_state_snapshot(energy=20.0, mode="drowsy", summary="精力偏低。")
        sleep.should_deep_sleep.return_value = False
        sleep.should_light_sleep.return_value = True
        sleep.run_light_sleep.return_value = SimpleNamespace(
            state={"summary": "浅睡整理完成。"},
            archived_count=5,
            semantic_count=2,
            impression_updates=1,
            action_result_count=1,
            created_habits=2,
            cooled_memories=3,
            maintenance_operations=0,
            expired_count=0,
            deep_summary="",
            self_review="",
            restart_requested=False,
        )
        runtime = SimpleNamespace(
            stm=MagicMock(),
            sleep=sleep,
            ltm=MagicMock(),
            habit_memory=MagicMock(),
            embedding_client=MagicMock(),
            embedding_model="embed-model",
            auxiliary_client=MagicMock(),
            auxiliary_model_config={"name": "aux"},
            metacognition=MagicMock(),
            emotion=MagicMock(),
            manas=MagicMock(),
        )
        runtime.stm.get_context.return_value = []
        runtime.stm.buffer_thoughts.return_value = [_make_thought(1, 1, "old")]
        runtime.emotion.current.return_value = {
            "dimensions": {"curiosity": 0.2},
            "dominant": "curiosity",
            "summary": "好奇 0.20",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

        _post_cycle_phase4(_as_runtime(runtime), 12, [], False)

        sleep.consume_cycle.assert_called_once()
        sleep.run_light_sleep.assert_called_once()
        runtime.manas.note_sleep_transition.assert_called_once()

    @patch("core.main._publish_event")
    @patch("core.main._maybe_reconnect_pg")
    @patch("core.main._maybe_reconnect_redis")
    @patch("core.main._redis_recovered")
    def test_recover_runtime_services_notifies_manas_of_runtime_recovery(
        self,
        mock_redis_recovered,
        mock_reconnect_redis,
        mock_reconnect_pg,
        _mock_publish_event,
    ) -> None:
        runtime = SimpleNamespace(
            stm=SimpleNamespace(redis_available=False, redis_client=MagicMock()),
            stimulus_queue=MagicMock(),
            action_manager=MagicMock(),
            emotion=MagicMock(),
            prefrontal=MagicMock(),
            manas=MagicMock(),
            metacognition=MagicMock(),
            sleep=MagicMock(),
            ltm=SimpleNamespace(available=False),
            habit_memory=MagicMock(),
            bootstrap_habits=[],
            embedding_client=MagicMock(),
            embedding_model="embed-model",
        )
        mock_reconnect_redis.side_effect = lambda *_args, **_kwargs: 12.0
        mock_redis_recovered.return_value = True

        def reconnect_pg(*_args: object, **_kwargs: object) -> tuple[dict[str, str], float]:
            runtime.ltm.available = True
            return {"self_understanding": "restored"}, 34.0

        mock_reconnect_pg.side_effect = reconnect_pg

        identity, redis_at, pg_at = _recover_runtime_services(
            log_file=None,
            runtime=_as_runtime(runtime),
            identity={"self_understanding": "before"},
            bootstrap_identity={"self_understanding": "boot"},
            now=100.0,
            reconnect_interval=30.0,
            last_redis_reconnect=1.0,
            last_pg_reconnect=2.0,
        )

        self.assertEqual(identity, {"self_understanding": "restored"})
        self.assertEqual(redis_at, 12.0)
        self.assertEqual(pg_at, 34.0)
        runtime.manas.note_restart_restoration.assert_called_once_with(
            redis_restored=True,
            pg_restored=True,
        )

    @patch("core.main.embed_text", side_effect=OSError("embed offline"))
    def test_retrieve_associations_falls_back_to_recent_by_time_when_embed_fails(self, mock_embed) -> None:
        stm = _build_association_stm()
        entity_recent = LongTermEntry(
            id=1,
            content="与当前对话对象相关的近期语义记忆",
            memory_type="semantic",
            source_cycle_id=9,
            importance=0.6,
            created_at=datetime.now(timezone.utc),
            entity_tags=["telegram:1"],
        )
        recent = LongTermEntry(
            id=2,
            content="最近的一条语义记忆",
            memory_type="semantic",
            source_cycle_id=8,
            importance=0.5,
            created_at=datetime.now(timezone.utc),
        )
        ltm = MagicMock()
        ltm.available = True
        ltm.retrieval_top_k = 5
        ltm.recent_by_time.side_effect = [[entity_recent], [recent]]
        ltm.mark_accessed = MagicMock()

        result = _retrieve_associations(
            ltm,
            MagicMock(),
            stm.get_context(),
            "embed-model",
            current_entity_filter="telegram:1",
        )

        mock_embed.assert_called_once()
        self.assertEqual(result, ["与当前对话对象相关的近期语义记忆", "最近的一条语义记忆"])
        self.assertEqual(ltm.recent_by_time.call_count, 2)
        ltm.search.assert_not_called()
        ltm.mark_accessed.assert_called_once_with([1, 2])
        first_call = ltm.recent_by_time.call_args_list[0]
        self.assertEqual(first_call.kwargs["entity_filter"], "telegram:1")
        self.assertEqual(first_call.kwargs["memory_types"], ["episodic", "semantic", "action_result"])

    # noinspection DuplicatedCode
    @patch("core.sleep._update_impression_memories", return_value=0)
    @patch("core.sleep._store_light_sleep_semantic_memories", return_value=0)
    @patch("core.sleep.embed_text", return_value=[0.1, 0.2])
    def test_run_light_sleep_archives_reflection_trace_as_episodic(
        self,
        _mock_embed: MagicMock,
        _mock_semantic: MagicMock,
        _mock_impressions: MagicMock,
    ) -> None:
        sleep = SleepManager(
            None,
            energy_per_cycle=0.2,
            drowsy_threshold=30,
            light_sleep_recovery=70,
            deep_sleep_trigger_hours=24,
            archive_importance_threshold=0.1,
            deep_sleep_failure_threshold=3,
            deep_sleep_active_memory_threshold=5000,
            inactive_purge_days=30,
            restart_after_deep_sleep=False,
        )
        stm = ShortTermMemory(redis_client=None, context_window=1)
        reflection = Thought(
            thought_id="C12-4",
            cycle_id=12,
            index=4,
            type="反思",
            content="我注意到自己总在等待里打转。",
        )
        reflection.attention_weight = 0.9
        reflection_2 = Thought(
            thought_id="C12-5",
            cycle_id=12,
            index=5,
            type="反思",
            content="我又在这里反复等一个回音。",
        )
        reflection_2.attention_weight = 0.8
        filler_1 = _make_thought(12, 6, "当前轮一")
        filler_2 = _make_thought(12, 7, "当前轮二")
        stm.append([reflection, reflection_2, filler_1, filler_2])
        ltm = MagicMock()
        ltm.store.return_value = 1
        ltm.cool_inactive_memories.return_value = 0
        habit_memory = MagicMock()
        habit_memory.strengthen_from_sleep.return_value = []
        emotion = _emotion_snapshot(curiosity=0.2, summary="好奇 0.20")

        sleep.run_light_sleep(
            cycle_id=12,
            stm=stm,
            ltm=ltm,
            habit_memory=habit_memory,
            embedding_client=MagicMock(),
            embedding_model="embed-model",
            auxiliary_client=MagicMock(),
            auxiliary_model_config={"name": "aux"},
            emotion=emotion,
        )

        self.assertTrue(ltm.store.called)
        self.assertEqual(ltm.store.call_args_list[0].kwargs["memory_type"], "episodic")

    # noinspection DuplicatedCode
    @patch("core.sleep._update_impression_memories", return_value=0)
    @patch("core.sleep._store_light_sleep_semantic_memories", return_value=0)
    @patch("core.sleep.embed_text", return_value=[0.1, 0.2])
    def test_run_light_sleep_skips_batch_duplicate_episodic_embeddings(
        self,
        mock_embed: MagicMock,
        _mock_semantic: MagicMock,
        _mock_impressions: MagicMock,
    ) -> None:
        sleep = SleepManager(
            None,
            energy_per_cycle=0.2,
            drowsy_threshold=30,
            light_sleep_recovery=70,
            deep_sleep_trigger_hours=24,
            archive_importance_threshold=0.1,
            deep_sleep_failure_threshold=3,
            deep_sleep_active_memory_threshold=5000,
            inactive_purge_days=30,
            restart_after_deep_sleep=False,
        )
        stm = ShortTermMemory(redis_client=None, context_window=1)
        duplicate_1 = Thought(
            thought_id="C12-1",
            cycle_id=12,
            index=1,
            type="反思",
            content="我在这里重复看同一件事。",
        )
        duplicate_1.attention_weight = 0.9
        duplicate_2 = Thought(
            thought_id="C12-2",
            cycle_id=12,
            index=2,
            type="反思",
            content="我在这里重复看同一件事。",
        )
        duplicate_2.attention_weight = 0.8
        filler_1 = _make_thought(12, 3, "当前轮一")
        filler_2 = _make_thought(12, 4, "当前轮二")
        filler_3 = _make_thought(12, 5, "当前轮三")
        stm.append([duplicate_1, duplicate_2, filler_1, filler_2, filler_3])
        ltm = MagicMock()
        ltm.existing_contents.return_value = set()
        ltm.store.return_value = 1
        ltm.cool_inactive_memories.return_value = 0
        habit_memory = MagicMock()
        habit_memory.strengthen_from_sleep.return_value = []
        emotion = _emotion_snapshot(curiosity=0.2, summary="好奇 0.20")

        sleep.run_light_sleep(
            cycle_id=12,
            stm=stm,
            ltm=ltm,
            habit_memory=habit_memory,
            embedding_client=MagicMock(),
            embedding_model="embed-model",
            auxiliary_client=MagicMock(),
            auxiliary_model_config={"name": "aux"},
            emotion=emotion,
        )

        self.assertEqual(mock_embed.call_count, 1)
        ltm.store.assert_called_once()
        self.assertEqual(stm.buffer_thoughts(), [])

    def test_light_sleep_trace_line_strips_action_markers(self) -> None:
        thought = Thought(
            thought_id="C12-4",
            cycle_id=12,
            index=4,
            type="意图",
            content='我想直接回一句。 {action:send_message, message:"我在"}',
        )

        line = _light_sleep_trace_line(thought)

        self.assertEqual(line, "[意图] 我想直接回一句。")

    @patch("core.sleep.embed_text", return_value=[0.1, 0.2])
    def test_archive_action_result_memories_returns_duplicate_ids_for_cleanup(
        self,
        _mock_embed: MagicMock,
    ) -> None:
        ltm = MagicMock()
        ltm.store.return_value = 11
        emotion = _emotion_snapshot(curiosity=0.2, summary="好奇 0.20")
        first = Stimulus(
            stimulus_id="stim_a",
            type="action_result",
            priority=2,
            source="action",
            content="同一条行动回音",
            action_id="act_C1-1",
            metadata={"action_type": "reading", "result": {"data": {}}},
        )
        second = Stimulus(
            stimulus_id="stim_b",
            type="action_result",
            priority=2,
            source="action",
            content="同一条行动回音",
            action_id="act_C1-2",
            metadata={"action_type": "reading", "result": {"data": {}}},
        )

        archived_ids = _archive_action_result_memories(
            ltm,
            MagicMock(),
            "embed-model",
            [first, second],
            emotion,
        )

        self.assertEqual(archived_ids, ["stim_a", "stim_b"])
        ltm.store.assert_called_once()

    def test_sleep_manager_uses_start_time_for_first_deep_sleep_deadline(self) -> None:
        sleep = SleepManager(
            None,
            energy_per_cycle=0.2,
            drowsy_threshold=30,
            light_sleep_recovery=70,
            deep_sleep_trigger_hours=24,
            archive_importance_threshold=0.1,
            deep_sleep_failure_threshold=3,
            deep_sleep_active_memory_threshold=5000,
            inactive_purge_days=30,
            restart_after_deep_sleep=False,
        )

        current = sleep.current()
        baseline = datetime.fromisoformat(current["last_deep_sleep_at"])

        self.assertFalse(
            sleep.should_deep_sleep(
                now=baseline + timedelta(hours=23),
                failure_count=0,
                degeneration_alert=False,
                active_memory_count=0,
            )
        )
        self.assertTrue(
            sleep.should_deep_sleep(
                now=baseline + timedelta(hours=25),
                failure_count=0,
                degeneration_alert=False,
                active_memory_count=0,
            )
        )

    def test_safe_post_cycle_phase4_swallows_post_cycle_failures(self) -> None:
        runtime = SimpleNamespace()

        with patch("core.main._post_cycle_phase4", side_effect=RuntimeError("sleep down")):
            result = _safe_post_cycle_phase4(
                _as_runtime(runtime),
                12,
                [],
                False,
            )

        self.assertFalse(result)


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

    def test_run_engine_loop_reuses_pending_cycle_id_across_retries(self) -> None:
        runtime = SimpleNamespace(
            retry_delay=1.0,
            max_retry_delay=8.0,
            reconnect_interval=30.0,
            bootstrap_identity={},
            stimulus_queue=MagicMock(),
            stm=MagicMock(),
        )
        stimuli = [MagicMock()]
        prepare_calls: list[int] = []
        execute_calls: list[int] = []

        def prepare_cycle(
            log_file,
            cycle_id,
            runtime_obj,
            identity,
            bootstrap_identity,
            reconnect_interval,
            last_redis_reconnect,
            last_pg_reconnect,
        ) -> tuple[dict[str, str], float, float, list[MagicMock], list, list]:
            _ = (
                log_file,
                runtime_obj,
                bootstrap_identity,
                reconnect_interval,
                last_redis_reconnect,
                last_pg_reconnect,
            )
            prepare_calls.append(cycle_id)
            return identity, 0.0, 0.0, stimuli, [], []

        execute_attempts = {"count": 0}

        def execute_cycle(
            runtime_obj,
            cycle_id,
            identity,
            current_stimuli,
            perception_cues,
            prompt_log_file,
        ) -> tuple[list[Thought], bool]:
            _ = (
                runtime_obj,
                identity,
                current_stimuli,
                perception_cues,
                prompt_log_file,
            )
            execute_calls.append(cycle_id)
            execute_attempts["count"] += 1
            if execute_attempts["count"] == 1:
                raise ConnectionRefusedError("refused")
            return [_make_thought(cycle_id, 1, "ok")], False

        def finish_cycle(log_file, cycle_id, current_stimuli, thoughts) -> None:
            _ = (log_file, cycle_id, current_stimuli, thoughts)
            raise KeyboardInterrupt

        with self.assertLogs("core.main", level="INFO") as logs:
            with (
                patch("core.main._next_cycle_id", return_value=294) as next_cycle_id,
                patch("core.main._prepare_cycle", side_effect=prepare_cycle),
                patch("core.main._execute_cycle", side_effect=execute_cycle),
                patch("core.main._handle_cycle_failure", return_value=2.0) as handle_failure,
                patch("core.main._finish_cycle", side_effect=finish_cycle) as finish_cycle_mock,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    _run_engine_loop(log_file=None, prompt_log_file=None, runtime=_as_runtime(runtime), identity={})

        next_cycle_id.assert_called_once_with(runtime.stm, 0)
        handle_failure.assert_called_once()
        finish_cycle_mock.assert_called_once()
        self.assertEqual(prepare_calls, [294, 294])
        self.assertEqual(execute_calls, [294, 294])
        output = "\n".join(logs.output)
        self.assertIn("cycle C294 loop finished", output)
        self.assertIn("retry_sleep_ms=1000.0", output)


if __name__ == "__main__":
    unittest.main()
