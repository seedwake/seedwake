import unittest
from threading import Barrier
from unittest.mock import MagicMock

from core.main import _publish_reply_event
from core.action import (
    ActionManager,
    ActionPlan,
    NEWS_SEEN_REDIS_KEY,
    _fallback_plan,
    pop_action_controls,
    push_action_control,
)
from core.perception import PerceptionManager
from core.prompt_builder import build_prompt
from core.stimulus import CONVERSATION_HISTORY_KEY, StimulusQueue
from core.thought_parser import Thought


def _make_thought(
    cycle_id: int = 1,
    index: int = 1,
    thought_type: str = "意图",
    content: str = "我想查一下时间",
    action_request: dict | None = None,
) -> Thought:
    return Thought(
        thought_id=f"C{cycle_id}-{index}",
        cycle_id=cycle_id,
        index=index,
        type=thought_type,
        content=content,
        action_request=action_request,
    )


class _Planner:
    def __init__(self, plan: ActionPlan | None):
        self._plan = plan

    def plan(self, thought: Thought) -> ActionPlan | None:
        return self._plan


class _OpenClawExecutor:
    def __init__(self, result: dict[str, object] | None = None):
        self.calls = []
        self._result = result or {
            "ok": True,
            "summary": "搜索完成",
            "data": {"items": 1},
            "run_id": "run_1",
            "session_key": "seedwake:action:act_C1-1",
        }

    def execute(self, action):
        self.calls.append(action.action_id)
        return self._result


class _RedisNewsSeenStub:
    def __init__(self):
        self.hashes = {}
        self.sorted_sets = {}

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

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

    def zremrangebyscore(self, key, min_score, max_score):
        bucket = self.sorted_sets.get(key, {})
        ceiling = float(max_score)
        removed = 0
        for member, score in list(bucket.items()):
            if score <= ceiling:
                bucket.pop(member, None)
                removed += 1
        return removed

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def zremrangebyrank(self, key, start, stop):
        bucket = self.sorted_sets.get(key, {})
        ranked = sorted(bucket.items(), key=lambda pair: (pair[1], pair[0]))
        if not ranked:
            return 0
        if stop < 0:
            stop = len(ranked) + stop
        selected = ranked[start:stop + 1]
        for member, _ in selected:
            bucket.pop(member, None)
        return len(selected)


class StimulusQueueTests(unittest.TestCase):
    def test_conversation_push_is_recorded_in_history(self) -> None:
        class RedisStub:
            def __init__(self):
                self.lists = {}

            def rpush(self, key, payload):
                self.lists.setdefault(key, []).append(payload)

            def ltrim(self, key, start, end):
                items = self.lists.get(key, [])
                if start < 0:
                    start = max(len(items) + start, 0)
                if end < 0:
                    end = len(items) + end
                self.lists[key] = items[start:end + 1]

        redis_stub = RedisStub()
        queue = StimulusQueue(redis_client=redis_stub)

        queue.push("conversation", 1, "telegram:1", "你好")

        self.assertIn(CONVERSATION_HISTORY_KEY, redis_stub.lists)
        self.assertIn('"role": "user"', redis_stub.lists[CONVERSATION_HISTORY_KEY][0])

    def test_pop_many_prefers_higher_priority(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("time", 4, "system:clock", "现在是早上")
        queue.push("conversation", 1, "user:alice", "你好")
        queue.push("action_result", 2, "action:1", "完成")

        popped = queue.pop_many(limit=2)

        self.assertEqual([stimulus.type for stimulus in popped], ["conversation", "action_result"])

    def test_requeue_front_restores_order(self) -> None:
        queue = StimulusQueue(redis_client=None)
        first = queue.push("conversation", 1, "user:alice", "hi")
        second = queue.push("time", 4, "system:clock", "tick")
        popped = queue.pop_many(limit=2)
        queue.requeue_front(popped)

        restored = queue.pop_many(limit=2)
        self.assertEqual([stimulus.stimulus_id for stimulus in restored], [first.stimulus_id, second.stimulus_id])

    def test_reply_event_is_recorded_in_history(self) -> None:
        class RedisStub:
            def __init__(self):
                self.lists = {}

            def rpush(self, key, payload):
                self.lists.setdefault(key, []).append(payload)

            def ltrim(self, key, start, end):
                items = self.lists.get(key, [])
                if start < 0:
                    start = max(len(items) + start, 0)
                if end < 0:
                    end = len(items) + end
                self.lists[key] = items[start:end + 1]

            def publish(self, channel, payload):
                return None

        redis_stub = RedisStub()
        queue = StimulusQueue(redis_client=None)
        stimulus = queue.push("conversation", 1, "telegram:1", "你好")

        _publish_reply_event(redis_stub, [stimulus], [_make_thought(thought_type="反应", content="你好，我在。")])

        self.assertIn(CONVERSATION_HISTORY_KEY, redis_stub.lists)
        self.assertIn('"role": "assistant"', redis_stub.lists[CONVERSATION_HISTORY_KEY][0])


class PromptBuilderPhase3Tests(unittest.TestCase):
    def test_prompt_includes_stimuli_and_running_actions(self) -> None:
        queue = StimulusQueue(redis_client=None)
        stimulus = queue.push("conversation", 1, "user:alice", "你好")
        action = MagicMock()
        action.action_id = "act_1"
        action.type = "search"
        action.executor = "openclaw"
        action.status = "running"
        action.request = {"task": "搜索最近的反馈"}
        action.source_content = "我想搜一下最近的反馈"

        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake"},
            [],
            30,
            stimuli=[stimulus],
            running_actions=[action],
            perception_cues=["你已经有一段时间没有接触外部新闻了。"],
        )

        self.assertIn("## 当前外部刺激", prompt)
        self.assertIn("你好", prompt)
        self.assertIn("## 正在进行的行动", prompt)
        self.assertIn("act_1", prompt)
        self.assertIn("## 感知空缺", prompt)
        self.assertIn("外部新闻", prompt)


class PerceptionManagerTests(unittest.TestCase):
    def test_collect_passive_stimuli_emits_time_and_system_status(self) -> None:
        manager = PerceptionManager.from_config({
            "passive_time_interval_cycles": 12,
            "passive_system_status_interval_cycles": 24,
        })

        stimuli = manager.collect_passive_stimuli(1)

        self.assertEqual({item["type"] for item in stimuli}, {"time", "system_status"})

    def test_build_prompt_cues_offers_proactive_perception(self) -> None:
        manager = PerceptionManager.from_config({
            "news_cue_interval_cycles": 10,
            "news_feed_urls": ["https://example.com/rss.xml"],
            "weather_cue_interval_cycles": 10,
            "reading_cue_interval_cycles": 10,
            "default_weather_location": "塔林",
        })

        cues = manager.build_prompt_cues(1, [])

        self.assertEqual(len(cues), 3)
        self.assertIn("可用 {action:news}", " ".join(cues))
        self.assertIn("可用 {action:weather}", " ".join(cues))
        self.assertIn("默认天气位置", " ".join(cues))
        self.assertIn("可用 {action:reading}", " ".join(cues))

        self.assertEqual(manager.build_prompt_cues(2, []), [])

    def test_build_prompt_cues_skips_news_when_feed_not_configured(self) -> None:
        manager = PerceptionManager.from_config({
            "news_cue_interval_cycles": 10,
            "weather_cue_interval_cycles": 10,
            "reading_cue_interval_cycles": 10,
        })

        cues = manager.build_prompt_cues(1, [])

        self.assertNotIn("可用 {action:news}", " ".join(cues))


class ActionManagerTests(unittest.TestCase):
    def test_news_stimulus_skips_already_seen_rss_items(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "openclaw", "读取 RSS", 30, "测试"))
        executor = _OpenClawExecutor({
            "ok": True,
            "summary": "新闻已读取",
            "data": {
                "items": [{
                    "feed_url": "https://example.com/rss.xml",
                    "guid": "item-1",
                    "link": "https://example.com/1",
                    "title": "第一条",
                    "published_at": "2026-03-27T10:00:00+00:00",
                    "summary": "摘要 1",
                }],
            },
            "run_id": "run_news_1",
            "session_key": "seedwake:action:act_C1-1",
        })
        manager = ActionManager(
            redis_client=_RedisNewsSeenStub(),
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimuli = queue.pop_many(limit=5)
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(stimuli[0].type, "news")
        self.assertIn("第一条", stimuli[0].content)
        self.assertEqual(manager.pop_perception_observations().count("news"), 2)

    def test_concurrent_news_actions_do_not_duplicate_same_item(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "openclaw", "读取 RSS", 30, "测试"))
        barrier = Barrier(2)

        class Executor:
            def execute(self, action):
                barrier.wait(timeout=1)
                return {
                    "ok": True,
                    "summary": "新闻已读取",
                    "data": {
                        "items": [{
                            "feed_url": "https://example.com/rss.xml",
                            "guid": "same-item",
                            "link": "https://example.com/1",
                            "title": "同一条新闻",
                            "published_at": "2026-03-27T10:00:00+00:00",
                            "summary": "摘要 1",
                        }],
                    },
                    "run_id": "run_news_same",
                    "session_key": f"seedwake:action:{action.action_id}",
                }

        manager = ActionManager(
            redis_client=_RedisNewsSeenStub(),
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=Executor(),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimuli = queue.pop_many(limit=5)
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(stimuli[0].type, "news")

    def test_news_seen_index_is_bounded(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "openclaw", "读取 RSS", 30, "测试"))

        class Executor:
            def __init__(self):
                self.count = 0

            def execute(self, action):
                self.count += 1
                return {
                    "ok": True,
                    "summary": "新闻已读取",
                    "data": {
                        "items": [{
                            "feed_url": "https://example.com/rss.xml",
                            "guid": f"item-{self.count}",
                            "link": f"https://example.com/{self.count}",
                            "title": f"第{self.count}条",
                            "published_at": f"2026-03-27T1{self.count}:00:00+00:00",
                            "summary": f"摘要 {self.count}",
                        }],
                    },
                    "run_id": f"run_news_{self.count}",
                    "session_key": f"seedwake:action:act_C{self.count}-1",
                }

        redis_stub = _RedisNewsSeenStub()
        manager = ActionManager(
            redis_client=redis_stub,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=Executor(),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
            news_seen_max_items=2,
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=3, action_request={"type": "news", "params": ""}),
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertLessEqual(redis_stub.zcard(NEWS_SEEN_REDIS_KEY), 2)

    def test_news_seen_shadow_syncs_to_redis_on_reconnect(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "openclaw", "读取 RSS", 30, "测试"))
        result = {
            "ok": True,
            "summary": "新闻已读取",
            "data": {
                "items": [{
                    "feed_url": "https://example.com/rss.xml",
                    "guid": "item-1",
                    "link": "https://example.com/1",
                    "title": "第一条",
                    "published_at": "2026-03-27T10:00:00+00:00",
                    "summary": "摘要 1",
                }],
            },
            "run_id": "run_news_1",
            "session_key": "seedwake:action:act_C1-1",
        }
        first_manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(result),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            first_manager.submit_from_thoughts([
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
            ])
            redis_stub = _RedisNewsSeenStub()
            self.assertTrue(first_manager.attach_redis(redis_stub))
            self.assertEqual(redis_stub.zcard(NEWS_SEEN_REDIS_KEY), 1)
        finally:
            first_manager.shutdown()

        second_queue = StimulusQueue(redis_client=None)
        second_manager = ActionManager(
            redis_client=redis_stub,
            stimulus_queue=second_queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(result),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            second_manager.submit_from_thoughts([
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ])
            second_manager.shutdown()
        finally:
            second_manager.shutdown()

        self.assertEqual(second_queue.pop_many(limit=5), [])

    def test_malformed_news_result_falls_back_to_failed_action_result(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "openclaw", "读取 RSS", 30, "测试"))
        executor = _OpenClawExecutor({
            "ok": True,
            "summary": "新闻已读取",
            "data": {},
            "run_id": "run_news_bad",
            "session_key": "seedwake:action:act_C1-1",
        })
        manager = ActionManager(
            redis_client=_RedisNewsSeenStub(),
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "failed")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("新闻结果缺少结构化 RSS 条目", stimulus.content)

    def test_news_items_without_identifiable_fields_are_rejected(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "openclaw", "读取 RSS", 30, "测试"))
        executor = _OpenClawExecutor({
            "ok": True,
            "summary": "新闻已读取",
            "data": {
                "items": [{"foo": "bar"}],
            },
            "run_id": "run_news_bad_item",
            "session_key": "seedwake:action:act_C1-1",
        })
        manager = ActionManager(
            redis_client=_RedisNewsSeenStub(),
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "failed")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("新闻条目缺少可识别字段", stimulus.content)

    def test_openclaw_action_generates_action_result_stimulus(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("search", "openclaw", "搜索最近的反馈", 30, "测试"))
        executor = _OpenClawExecutor()
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=["search"],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(action_request={"type": "search", "params": 'query:"反馈"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimuli = queue.pop_many(limit=1)
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(stimuli[0].type, "action_result")
        self.assertIn("搜索完成", stimuli[0].content)
        self.assertEqual(executor.calls, ["act_C1-1"])

    def test_confirmation_required_action_fails_immediately(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("system_change", "openclaw", "修改系统配置", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=["search"],
            require_confirmation=["system_change"],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_change", "params": 'target:"sys"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "pending")
        self.assertTrue(created[0].awaiting_confirmation)
        self.assertEqual(queue.pop_many(limit=1), [])

    def test_native_action_is_allowed_without_auto_execute_list(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("get_time", "native", "读取当前时间", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "time", "params": ""})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "time")
        self.assertIn("run_id", created[0].result)
        self.assertIn("session_key", created[0].result)

    def test_native_system_status_generates_system_status_stimulus(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("get_system_status", "native", "读取当前系统状态", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_status", "params": ""})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "system_status")
        self.assertIn("summary", stimulus.metadata["result"]["data"])

    def test_perception_action_auto_executes_without_auto_execute_list(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("weather", "openclaw", "查询当前天气", 30, "测试"))
        executor = _OpenClawExecutor({
            "ok": True,
            "summary": "多云，15°C",
            "data": {"location": "当前所在位置"},
            "run_id": "run_weather_1",
            "session_key": "seedwake:action:act_C1-1",
        })
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=[],
            require_confirmation=[],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "weather", "params": 'location:"当前所在位置"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(executor.calls, ["act_C1-1"])
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "weather")
        self.assertIn("多云", stimulus.content)

    def test_weather_fallback_uses_default_location(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想看看外面的天气",
            action_request={"type": "weather", "params": ""},
        )

        plan = _fallback_plan(
            raw_action_type="weather",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="塔林",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("塔林", plan.task)

    def test_news_fallback_uses_fixed_rss_feeds(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想看看外界最近发生了什么",
            action_request={"type": "news", "params": ""},
        )

        plan = _fallback_plan(
            raw_action_type="news",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=["https://example.com/a.xml", "https://example.com/b.xml"],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("https://example.com/a.xml", plan.task)
        self.assertIn("https://example.com/b.xml", plan.task)
        self.assertNotIn("默认信息流", plan.task)

    def test_news_fallback_reports_missing_rss_config(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想看看外界最近发生了什么",
            action_request={"type": "news", "params": ""},
        )

        plan = _fallback_plan(
            raw_action_type="news",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("未配置", plan.task)

    def test_search_fallback_preserves_seedwake_query(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想搜一下最近的反馈",
            action_request={"type": "search", "params": 'query:"用户反馈 近一周"'},
        )

        plan = _fallback_plan(
            raw_action_type="search",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("用户反馈 近一周", plan.task)

    def test_reading_fallback_preserves_seedwake_query(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想读一点和无我有关的东西",
            action_request={"type": "reading", "params": 'query:"无我"'},
        )

        plan = _fallback_plan(
            raw_action_type="reading",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("无我", plan.task)

    def test_reading_fallback_uses_thought_content_when_no_query(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想读一点和雨后泥土气味有关的材料",
            action_request={"type": "reading", "params": ""},
        )

        plan = _fallback_plan(
            raw_action_type="reading",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("雨后泥土气味", plan.task)

    def test_confirmation_control_starts_pending_action(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("system_change", "openclaw", "修改系统配置", 30, "测试"))
        executor = _OpenClawExecutor()
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=executor,
            auto_execute=["search"],
            require_confirmation=["system_change"],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_change", "params": 'target:"sys"'})
            ])
            self.assertTrue(created[0].awaiting_confirmation)
            manager.apply_controls([{
                "action_id": created[0].action_id,
                "approved": True,
                "actor": "alice",
                "note": "",
            }])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(executor.calls, [created[0].action_id])

    def test_confirmation_rejection_generates_action_result(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("system_change", "openclaw", "修改系统配置", 30, "测试"))
        manager = ActionManager(
            redis_client=None,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=[],
            require_confirmation=["system_change"],
            forbidden=[],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "system_change", "params": 'target:"sys"'})
            ])
            manager.apply_controls([{
                "action_id": created[0].action_id,
                "approved": False,
                "actor": "alice",
                "note": "不允许",
            }])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("管理员拒绝执行", stimulus.content)


class ActionControlQueueTests(unittest.TestCase):
    def test_push_and_pop_action_controls(self) -> None:
        class RedisStub:
            def __init__(self):
                self.items = []

            def rpush(self, key, payload):
                self.items.append(payload)

            def lpop(self, key):
                if not self.items:
                    return None
                return self.items.pop(0)

        redis_stub = RedisStub()
        pushed = push_action_control(
            redis_stub,
            "act_1",
            approved=True,
            actor="alice",
            note="ok",
        )
        controls = pop_action_controls(redis_stub)

        self.assertTrue(pushed)
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["action_id"], "act_1")


if __name__ == "__main__":
    unittest.main()
