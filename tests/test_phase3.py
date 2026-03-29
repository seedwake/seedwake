import json
import unittest
from email.message import Message
from threading import Barrier
from unittest.mock import MagicMock, patch
from urllib import error

# noinspection PyProtectedMember
from core.action import (
    ACTION_REDIS_KEY,
    ActionCallbacks,
    ActionManager,
    ActionPlan,
    NEWS_SEEN_REDIS_KEY,
    _fallback_plan,
    _native_send_message_plan,
    pop_action_controls,
    push_action_control,
)
# noinspection PyProtectedMember
from core.main import _select_cycle_stimuli
from core.openclaw_gateway import OpenClawUnavailableError
from core.perception import PerceptionManager
from core.prompt_builder import build_prompt
from core.rss import read_news_result
from core.stimulus import CONVERSATION_HISTORY_KEY, Stimulus, StimulusQueue
from core.thought_parser import Thought
from core.types import ActionControl, ActionResultEnvelope, NewsItem
from test_support import ListRedisStub


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


def _conversation_stimulus(source: str = "telegram:1", content: str = "你好") -> Stimulus:
    return Stimulus(
        stimulus_id="stim_conv_1",
        type="conversation",
        priority=1,
        source=source,
        content=content,
    )


class _Planner:
    def __init__(self, plan: ActionPlan | tuple[None, str | None] | None):
        self._plan = plan

    def plan(
        self,
        _thought: Thought,
        *,
        conversation_source: str | None = None,
    ) -> ActionPlan | tuple[None, str | None] | None:
        _ = conversation_source
        return self._plan


class _OpenClawExecutor:
    def __init__(self, result: ActionResultEnvelope | None = None):
        self.calls = []
        self._result = result or _action_result(
            summary="搜索完成",
            data={"items": 1},
            run_id="run_1",
            session_key="seedwake:action:act_C1-1",
        )

    def execute(self, action):
        self.calls.append(action.action_id)
        return self._result


class _UnavailableOpenClawExecutor:
    def __init__(self, message: str = "gateway unavailable"):
        self.calls = []
        self._message = message

    def execute(self, action):
        self.calls.append(action.action_id)
        raise OpenClawUnavailableError(self._message)


def _news_result(
    *,
    guid: str = "item-1",
    title: str = "第一条",
    link: str = "https://example.com/1",
    published_at: str = "2026-03-27T10:00:00+00:00",
    summary: str = "摘要 1",
) -> ActionResultEnvelope:
    return _action_result(
        summary="新闻已读取",
        data={
            "items": [{
                "feed_url": "https://example.com/rss.xml",
                "guid": guid,
                "link": link,
                "title": title,
                "published_at": published_at,
                "summary": summary,
            }],
        },
        run_id=f"run_news_{guid}",
        session_key="seedwake:action:act_C1-1",
    )


def _action_result(
    *,
    summary: str,
    data: dict,
    ok: bool = True,
    error_detail=None,
    run_id: str | None = None,
    session_key: str | None = None,
    transport: str = "openclaw",
) -> ActionResultEnvelope:
    return {
        "ok": ok,
        "summary": summary,
        "data": data,
        "error": error_detail,
        "run_id": run_id,
        "session_key": session_key,
        "transport": transport,
    }


def _action_control(action_id: str, *, approved: bool, actor: str, note: str) -> ActionControl:
    return {
        "action_id": action_id,
        "approved": approved,
        "actor": actor,
        "note": note,
        "timestamp": "2026-03-28T00:00:00+00:00",
    }


def _constant_news_reader(result: ActionResultEnvelope):
    def reader(_feed_urls: list[str], *, timeout_seconds: int) -> ActionResultEnvelope:
        _ = timeout_seconds
        return result

    return reader


def _news_items(result: ActionResultEnvelope) -> list[NewsItem]:
    raw_items = result["data"].get("items")
    assert isinstance(raw_items, list)
    items: list[NewsItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        items.append({
            "feed_url": str(item.get("feed_url") or ""),
            "guid": str(item.get("guid") or ""),
            "link": str(item.get("link") or ""),
            "title": str(item.get("title") or ""),
            "published_at": str(item.get("published_at") or ""),
            "summary": str(item.get("summary") or ""),
        })
    return items


def _target_entity_message_params() -> str:
    return 'target_entity:"person:alice", message:"你好"'


def _telegram_http_error() -> error.HTTPError:
    return error.HTTPError(
        url="https://api.telegram.org",
        code=403,
        msg="forbidden",
        hdrs=Message(),
        fp=None,
    )


def _submit_send_message_success(
    manager: ActionManager,
    thoughts: list[Thought],
    *,
    stimuli: list[Stimulus] | None = None,
) -> list:
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
        with patch("core.action.request.urlopen") as mock_urlopen:
            response = MagicMock()
            response.read.return_value = b'{"ok": true}'
            mock_urlopen.return_value.__enter__.return_value = response
            return manager.submit_from_thoughts(thoughts, stimuli=stimuli)


def _build_action_manager(
    queue: StimulusQueue,
    planner: _Planner,
    *,
    redis_client=None,
    openclaw_executor=None,
    news_reader=None,
    contact_resolver=None,
    event_callback=None,
    auto_execute=None,
    require_confirmation=None,
    forbidden=None,
    news_seen_max_items: int = 5000,
) -> ActionManager:
    return ActionManager(
        redis_client=redis_client,
        stimulus_queue=queue,
        planner=planner,
        openclaw_executor=openclaw_executor or _OpenClawExecutor(),
        auto_execute=auto_execute or [],
        require_confirmation=require_confirmation or [],
        forbidden=forbidden or [],
        news_seen_max_items=news_seen_max_items,
        news_reader=news_reader,
        contact_resolver=contact_resolver,
        callbacks=ActionCallbacks(event=event_callback),
    )


def _submit_and_shutdown(manager: ActionManager, thoughts: list[Thought]) -> list:
    try:
        return manager.submit_from_thoughts(thoughts)
    finally:
        manager.shutdown()


def _submit_and_shutdown_with_stimuli(
    manager: ActionManager,
    thoughts: list[Thought],
    *,
    stimuli: list[Stimulus],
) -> list:
    try:
        return _submit_send_message_success(manager, thoughts, stimuli=stimuli)
    finally:
        manager.shutdown()


def _assert_failed_news_action(
    test_case: unittest.TestCase,
    result: ActionResultEnvelope,
    expected_message: str,
) -> None:
    queue = StimulusQueue(redis_client=None)
    manager = _build_action_manager(
        queue,
        _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
        redis_client=_RedisNewsSeenStub(),
        news_reader=_constant_news_reader(result),
    )
    created = _submit_and_shutdown(
        manager,
        [_make_thought(cycle_id=1, action_request={"type": "news", "params": ""})],
    )

    test_case.assertEqual(created[0].status, "failed")
    stimulus = queue.pop_many(limit=1)[0]
    test_case.assertEqual(stimulus.type, "action_result")
    test_case.assertIn(expected_message, stimulus.content)


def _assert_single_news_stimulus(
    test_case: unittest.TestCase,
    queue: StimulusQueue,
    manager: ActionManager,
    thoughts: list[Thought],
    *,
    expected_text: str | None = None,
) -> None:
    _submit_and_shutdown(manager, thoughts)
    stimuli = queue.pop_many(limit=5)
    test_case.assertEqual(len(stimuli), 1)
    test_case.assertEqual(stimuli[0].type, "news")
    if expected_text:
        test_case.assertIn(expected_text, stimuli[0].content)


def _stored_action_payload(
    *,
    action_id: str = "act_C1-1",
    action_type: str = "search",
    executor: str = "openclaw",
    status: str = "pending",
    awaiting_confirmation: bool = False,
) -> str:
    return json.dumps({
        "action_id": action_id,
        "type": action_type,
        "request": {
            "task": "搜索资料",
            "reason": "测试",
            "raw_action": {"type": action_type, "params": 'query:"Seedwake"'},
        },
        "executor": executor,
        "status": status,
        "source_thought_id": "C1-1",
        "source_content": "我想搜一下 Seedwake",
        "submitted_at": "2026-03-27T12:00:00+00:00",
        "timeout_seconds": 30,
        "result": None,
        "run_id": None,
        "session_key": "seedwake:action:act_C1-1",
        "awaiting_confirmation": awaiting_confirmation,
        "retry_after": None,
    }, ensure_ascii=False)


class _RedisNewsSeenStub:
    def __init__(self):
        self.hashes = {}
        self.sorted_sets = {}

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

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
        _ = min_score
        bucket = self.sorted_sets.get(key, {})
        ceiling = float(max_score)
        removed = 0
        for member, score in tuple(bucket.items()):
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
        redis_stub = ListRedisStub()
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

    def test_select_cycle_stimuli_keeps_single_conversation_per_round(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("conversation", 1, "telegram:1", "Alice")
        queue.push("conversation", 1, "telegram:2", "Bob")

        first_round = _select_cycle_stimuli(queue)
        second_round = _select_cycle_stimuli(queue)

        self.assertEqual(len(first_round), 1)
        self.assertEqual(first_round[0].source, "telegram:1")
        self.assertEqual(len(second_round), 1)
        self.assertEqual(second_round[0].source, "telegram:2")


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
            perception_cues=["我已经有一段时间没有接触外部新闻了。"],
        )

        self.assertIn("## 当前外部刺激", prompt)
        self.assertIn("你好", prompt)
        self.assertIn("## 正在进行的行动", prompt)
        self.assertIn("act_1", prompt)
        self.assertIn("## 感知空缺", prompt)
        self.assertIn("外部新闻", prompt)
        self.assertIn("{action:web_fetch", prompt)
        self.assertIn("{action:system_change", prompt)
        self.assertIn("不要发明未列出的 action 名称", prompt)
        self.assertIn("我想发出的内容", prompt)
        self.assertIn("我自己想读的内容", prompt)
        self.assertNotIn("你想发出的内容", prompt)


class NativeNewsReaderTests(unittest.TestCase):
    def test_read_news_result_parses_rss_items(self) -> None:
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Example Feed</title>
            <item>
              <guid>item-1</guid>
              <link>https://example.com/1</link>
              <title>第一条</title>
              <pubDate>Fri, 27 Mar 2026 10:00:00 +0000</pubDate>
              <description><![CDATA[<p>摘要 <b>一</b></p>]]></description>
            </item>
          </channel>
        </rss>
        """

        with patch("core.rss._fetch_feed_text", return_value=xml_text):
            result = read_news_result(["https://example.com/rss.xml"])

        self.assertTrue(result["ok"])
        items = _news_items(result)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["feed_url"], "https://example.com/rss.xml")
        self.assertEqual(items[0]["guid"], "item-1")
        self.assertEqual(items[0]["title"], "第一条")
        self.assertEqual(items[0]["summary"], "摘要 一")

    def test_read_news_result_returns_partial_success_when_some_feeds_fail(self) -> None:
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <guid>item-1</guid>
              <link>https://example.com/1</link>
              <title>第一条</title>
            </item>
          </channel>
        </rss>
        """

        def fetch_feed_text(feed_url: str, *, timeout_seconds: float) -> str:
            _ = timeout_seconds
            if feed_url.endswith("broken.xml"):
                raise OSError("boom")
            return xml_text

        with patch("core.rss._fetch_feed_text", side_effect=fetch_feed_text):
            result = read_news_result([
                "https://example.com/broken.xml",
                "https://example.com/rss.xml",
            ])

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["items"]), 1)
        self.assertIn("errors", result["data"])


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
        self.assertIn("我已经有一段时间没有接触外部新闻了", " ".join(cues))
        self.assertIn("我已经有一段时间没有感知外部天气了", " ".join(cues))
        self.assertIn("我已经有一段时间没有阅读外部材料了", " ".join(cues))
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
        seen_feed_urls = []
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=lambda feed_urls, *, timeout_seconds: (
                seen_feed_urls.append(list(feed_urls)) or _news_result()
            ),
        )
        _assert_single_news_stimulus(
            self,
            queue,
            manager,
            [
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ],
            expected_text="第一条",
        )
        self.assertEqual(seen_feed_urls[0], ["https://example.com/rss.xml"])
        self.assertEqual(manager.pop_perception_observations().count("news"), 2)

    def test_concurrent_news_actions_do_not_duplicate_same_item(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"]))
        barrier = Barrier(2)

        def news_reader(_feed_urls: list[str], *, timeout_seconds: int):
            _ = timeout_seconds
            barrier.wait(timeout=1)
            return _action_result(
                summary="新闻已读取",
                data={
                    "items": [{
                        "feed_url": "https://example.com/rss.xml",
                        "guid": "same-item",
                        "link": "https://example.com/1",
                        "title": "同一条新闻",
                        "published_at": "2026-03-27T10:00:00+00:00",
                        "summary": "摘要 1",
                    }],
                },
                run_id=None,
                session_key=None,
                transport="native",
            )

        manager = _build_action_manager(
            queue,
            planner,
            redis_client=_RedisNewsSeenStub(),
            news_reader=news_reader,
        )
        _assert_single_news_stimulus(
            self,
            queue,
            manager,
            [
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ],
        )

    def test_news_seen_index_is_bounded(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"]))
        count = {"value": 0}

        def news_reader(_feed_urls: list[str], *, timeout_seconds: int):
            _ = timeout_seconds
            count["value"] += 1
            return _action_result(
                summary="新闻已读取",
                data={
                    "items": [{
                        "feed_url": "https://example.com/rss.xml",
                        "guid": f"item-{count['value']}",
                        "link": f"https://example.com/{count['value']}",
                        "title": f"第{count['value']}条",
                        "published_at": f"2026-03-27T1{count['value']}:00:00+00:00",
                        "summary": f"摘要 {count['value']}",
                    }],
                },
                run_id=None,
                session_key=None,
                transport="native",
            )

        redis_stub = _RedisNewsSeenStub()
        manager = _build_action_manager(
            queue,
            planner,
            redis_client=redis_stub,
            news_reader=news_reader,
            news_seen_max_items=2,
        )
        _submit_and_shutdown(manager, [
            _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
            _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            _make_thought(cycle_id=3, action_request={"type": "news", "params": ""}),
        ])

        self.assertLessEqual(redis_stub.zcard(NEWS_SEEN_REDIS_KEY), 2)

    def test_news_seen_shadow_syncs_to_redis_on_reconnect(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"]))
        result = _news_result()
        first_manager = _build_action_manager(
            queue,
            planner,
            redis_client=None,
            news_reader=_constant_news_reader(result),
        )

        try:
            first_manager.submit_from_thoughts([_make_thought(cycle_id=1,
                                                              action_request={"type": "news", "params": ""})])
            redis_stub = _RedisNewsSeenStub()
            self.assertTrue(first_manager.attach_redis(redis_stub))
            self.assertEqual(redis_stub.zcard(NEWS_SEEN_REDIS_KEY), 1)
        finally:
            first_manager.shutdown()

        second_queue = StimulusQueue(redis_client=None)
        second_manager = _build_action_manager(
            second_queue,
            planner,
            redis_client=redis_stub,
            news_reader=_constant_news_reader(result),
        )
        _submit_and_shutdown(second_manager, [
            _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
        ])

        self.assertEqual(second_queue.pop_many(limit=5), [])

    def test_malformed_news_result_falls_back_to_failed_action_result(self) -> None:
        _assert_failed_news_action(
            self,
            _action_result(
                summary="新闻已读取",
                data={},
                run_id="run_news_bad",
                session_key="seedwake:action:act_C1-1",
            ),
            "新闻结果缺少结构化 RSS 条目",
        )

    def test_news_items_without_identifiable_fields_are_rejected(self) -> None:
        _assert_failed_news_action(
            self,
            _action_result(
                summary="新闻已读取",
                data={
                    "items": [{"foo": "bar"}],
                },
                run_id="run_news_bad_item",
                session_key="seedwake:action:act_C1-1",
            ),
            "新闻条目缺少可识别字段",
        )

    def test_openclaw_unavailable_actions_are_deferred_until_retry(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("search", "openclaw", "搜索资料", 30, "测试"))
        manager = _build_action_manager(
            queue,
            planner,
            openclaw_executor=_UnavailableOpenClawExecutor(),
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "search", "params": 'query:"Seedwake"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "pending")
        self.assertIsNotNone(created[0].retry_after)
        self.assertEqual(queue.pop_many(limit=5), [])

    def test_restored_confirmation_action_can_be_approved_after_restart(self) -> None:
        queue = StimulusQueue(redis_client=None)
        redis_stub = _RedisNewsSeenStub()
        redis_stub.hset("seedwake:actions", "act_C1-1", _stored_action_payload(awaiting_confirmation=True))
        executor = _OpenClawExecutor()
        manager = ActionManager(
            redis_client=redis_stub,
            stimulus_queue=queue,
            planner=_Planner(None),
            openclaw_executor=executor,
            auto_execute=[],
            require_confirmation=["search"],
            forbidden=[],
        )

        try:
            manager.apply_controls([
                _action_control("act_C1-1", approved=True, actor="alice", note=""),
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(executor.calls, ["act_C1-1"])
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")

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

    def test_native_send_message_uses_current_conversation_source(self) -> None:
        redis_stub = ListRedisStub()
        queue = StimulusQueue(redis_client=None)
        events = []
        planner = _Planner(ActionPlan("send_message", "native", "发送消息", 30, "测试", message_text="我在。"))
        manager = ActionManager(
            redis_client=redis_stub,
            stimulus_queue=queue,
            planner=planner,
            openclaw_executor=_OpenClawExecutor(),
            auto_execute=["send_message"],
            require_confirmation=[],
            forbidden=[],
            callbacks=ActionCallbacks(event=lambda event_type, payload: events.append((event_type, payload))),
        )

        created = _submit_and_shutdown_with_stimuli(
            manager,
            [
                _make_thought(
                    action_request={"type": "send_message", "params": 'message:"我在。"'}
                )
            ],
            stimuli=[_conversation_stimulus()],
        )

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(events[-1][0], "reply")
        self.assertEqual(events[-1][1]["source"], "telegram:1")
        self.assertEqual(events[-1][1]["message"], "我在。")
        self.assertIn(CONVERSATION_HISTORY_KEY, redis_stub.lists)
        self.assertIn('"role": "assistant"', redis_stub.lists[CONVERSATION_HISTORY_KEY][0])

    def test_perception_action_auto_executes_without_auto_execute_list(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("weather", "openclaw", "查询当前天气", 30, "测试"))
        executor = _OpenClawExecutor(_action_result(
            summary="多云，15°C",
            data={"location": "当前所在位置"},
            run_id="run_weather_1",
            session_key="seedwake:action:act_C1-1",
        ))
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

    def test_reading_stimulus_contains_excerpt_and_note(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("reading", "openclaw", "阅读外部材料", 30, "测试"))
        executor = _OpenClawExecutor(_action_result(
            summary="找到一段贴题材料",
            data={
                "source": {
                    "title": "Example Article",
                    "url": "https://example.com/article",
                },
                "excerpt_original": "The answer lies in the logs.",
                "brief_note": "这段适合当前主题。",
            },
            run_id="run_reading_1",
            session_key="agent:seedwake-worker:seedwake:action:act_C1-1",
        ))
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
            manager.submit_from_thoughts([
                _make_thought(action_request={"type": "reading", "params": 'query:"意识"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "reading")
        self.assertIn("找到一段贴题材料", stimulus.content)
        self.assertIn("来源：Example Article (https://example.com/article)", stimulus.content)
        self.assertIn("原文片段：The answer lies in the logs.", stimulus.content)
        self.assertIn("笔记：这段适合当前主题。", stimulus.content)

    def test_reading_stimulus_truncates_long_excerpt_and_marks_continuation(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("reading", "openclaw", "阅读外部材料", 30, "测试"))
        long_excerpt = "A" * 1800 + "TAIL"
        executor = _OpenClawExecutor(_action_result(
            summary="找到一段很长的材料",
            data={
                "source": {
                    "title": "Long Article",
                    "url": "https://example.com/long",
                },
                "excerpt_original": long_excerpt,
                "brief_note": "这段很长，需要节选。",
            },
            run_id="run_reading_2",
            session_key="agent:seedwake-worker:seedwake:action:act_C1-1",
        ))
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
            manager.submit_from_thoughts([
                _make_thought(action_request={"type": "reading", "params": 'query:"长文"'})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        stimulus = queue.pop_many(limit=1)[0]
        self.assertIn("原文片段（节选）：", stimulus.content)
        self.assertIn("说明：这里只展示节选；如果我还想继续读，可以再次使用 {action:reading} 或 {action:web_fetch}。", stimulus.content)
        self.assertNotIn(long_excerpt, stimulus.content)
        self.assertNotIn("TAIL", stimulus.content)
        self.assertIn("笔记：这段很长，需要节选。", stimulus.content)

    def test_planner_ignore_emits_feedback_stimulus(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(None),
            auto_execute=["news"],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "news", "params": ""})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created, [])
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertEqual(stimulus.source, "planner:C1-1")
        self.assertIn("news 未执行", stimulus.content)
        self.assertEqual(stimulus.metadata["status"], "ignored")
        self.assertEqual(stimulus.metadata["result"]["error"], "ignored_by_planner")

    def test_planner_ignore_preserves_reason_in_feedback_stimulus(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner((None, "参数不足，先不执行")),
            auto_execute=["news"],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(action_request={"type": "news", "params": ""})
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created, [])
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("参数不足，先不执行", stimulus.content)
        self.assertEqual(stimulus.metadata["result"]["summary"], "参数不足，先不执行")

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

    def test_send_message_fallback_uses_conversation_source(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="告诉她我已经收到 {action:send_message, message:\"我已经收到\"}",
            action_request={"type": "send_message", "params": 'message:"我已经收到"'},
        )

        plan = _fallback_plan(
            raw_action_type="send_message",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
            conversation_source="telegram:42",
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "native")
        self.assertEqual(plan.target_source, "telegram:42")
        self.assertEqual(plan.message_text, "我已经收到")

    def test_send_message_fallback_preserves_target_entity(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="告诉 Alice 我已经看到 {action:send_message, target_entity:\"person:alice\", message:\"我已经看到\"}",
            action_request={"type": "send_message", "params": 'target_entity:"person:alice", message:"我已经看到"'},
        )

        plan = _fallback_plan(
            raw_action_type="send_message",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "native")
        self.assertEqual(plan.target_entity, "person:alice")
        self.assertEqual(plan.message_text, "我已经看到")

    def test_native_send_message_plan_normalizes_explicit_numeric_target(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="请回复我",
            action_request={"type": "send_message", "params": 'message:"你好"'},
        )

        plan = _native_send_message_plan(
            raw_params='message:"你好"',
            thought=thought,
            timeout_seconds=30,
            reason="测试",
            conversation_source=None,
            explicit_message="你好",
            explicit_target="8469901143",
        )

        self.assertEqual(plan.target_source, "telegram:8469901143")
        self.assertEqual(plan.message_text, "你好")

    def test_native_send_message_resolves_target_entity(self) -> None:
        queue = StimulusQueue(redis_client=None)
        events = []
        planner = _Planner(ActionPlan(
            "send_message",
            "native",
            "发送消息",
            30,
            "测试",
            target_entity="person:alice",
            message_text="你好",
        ))
        manager = _build_action_manager(
            queue,
            planner,
            redis_client=ListRedisStub(),
            contact_resolver=lambda entity: "telegram:99" if entity == "person:alice" else None,
            event_callback=lambda event_type, payload: events.append((event_type, payload)),
            auto_execute=["send_message"],
        )

        try:
            created = _submit_send_message_success(
                manager,
                [
                    _make_thought(
                        action_request={"type": "send_message", "params": _target_entity_message_params()}
                    )
                ],
            )
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(events[-1][1]["source"], "telegram:99")

    def test_native_send_message_fails_when_telegram_send_fails(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan(
            "send_message",
            "native",
            "发送消息",
            30,
            "测试",
            target_source="telegram:42",
            message_text="你好",
        ))
        manager = _build_action_manager(
            queue,
            planner,
            redis_client=ListRedisStub(),
            auto_execute=["send_message"],
        )

        try:
            telegram_error = _telegram_http_error()
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
                with patch("core.action.request.urlopen", side_effect=telegram_error):
                    created = manager.submit_from_thoughts([
                        _make_thought(
                            action_request={"type": "send_message", "params": 'chat_id:"42", message:"你好"'}
                        )
                    ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "failed")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("Telegram 发送失败", stimulus.content)

    def test_native_send_message_fails_when_target_entity_cannot_be_resolved(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan(
            "send_message",
            "native",
            "发送消息",
            30,
            "测试",
            target_entity="person:alice",
            message_text="你好",
        ))
        manager = _build_action_manager(
            queue,
            planner,
            redis_client=None,
            contact_resolver=lambda entity: None,
            auto_execute=["send_message"],
        )

        try:
            created = manager.submit_from_thoughts([
                _make_thought(
                    action_request={"type": "send_message", "params": _target_entity_message_params()}
                )
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "failed")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("无法解析实体 person:alice", stimulus.content)

    def test_native_send_message_history_failure_keeps_action_succeeded(self) -> None:
        class HistoryFailingRedis(ListRedisStub):
            def rpush(self, key, payload):
                if key == CONVERSATION_HISTORY_KEY:
                    raise OSError("history down")
                super().rpush(key, payload)

        redis_stub = HistoryFailingRedis()
        events = []
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("send_message", "native", "发送消息", 30, "测试", message_text="我在。")),
            redis_client=redis_stub,
            event_callback=lambda event_type, payload: events.append((event_type, payload)),
            auto_execute=["send_message"],
        )

        created = _submit_and_shutdown_with_stimuli(
            manager,
            [
                _make_thought(
                    action_request={"type": "send_message", "params": 'message:"我在。"'}
                )
            ],
            stimuli=[_conversation_stimulus()],
        )

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(events[-1][0], "reply")
        self.assertNotIn(CONVERSATION_HISTORY_KEY, redis_stub.lists)

    def test_native_send_message_does_not_send_when_dispatch_marker_cannot_persist(self) -> None:
        class DispatchStateFailingRedis(ListRedisStub):
            def hset(self, key, field, value):
                _ = field, value
                if key == ACTION_REDIS_KEY:
                    raise OSError("action redis down")
                super().hset(key, field, value)

        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("send_message", "native", "发送消息", 30, "测试", message_text="我在。")),
            redis_client=DispatchStateFailingRedis(),
            auto_execute=["send_message"],
        )

        try:
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
                with patch("core.action.request.urlopen") as mock_urlopen:
                    created = manager.submit_from_thoughts(
                        [_make_thought(action_request={"type": "send_message", "params": 'message:"我在。"'})],
                        stimuli=[_conversation_stimulus()],
                    )
            manager.shutdown()
        finally:
            manager.shutdown()

        mock_urlopen.assert_not_called()
        self.assertEqual(created[0].status, "failed")
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("消息发送前无法持久化状态", stimulus.content)

    def test_restore_running_send_message_with_started_dispatch_does_not_resend(self) -> None:
        redis_stub = ListRedisStub()
        redis_stub.hset(
            ACTION_REDIS_KEY,
            "act_C1-1",
            json.dumps({
                "action_id": "act_C1-1",
                "type": "send_message",
                "executor": "native",
                "status": "running",
                "source_thought_id": "C1-1",
                "source_content": "告诉她我在",
                "submitted_at": "2026-03-29T12:00:00+00:00",
                "timeout_seconds": 30,
                "result": None,
                "run_id": None,
                "session_key": None,
                "awaiting_confirmation": False,
                "retry_after": None,
                "dispatch_started_at": "2026-03-29T12:00:01+00:00",
                "request": {
                    "task": "向 telegram:42 发送消息：我在。",
                    "reason": "测试",
                    "raw_action": {"type": "send_message", "params": 'chat_id:"42", message:"我在。"'},
                    "target_source": "telegram:42",
                    "message_text": "我在。",
                },
            }, ensure_ascii=False),
        )

        with patch("core.action.request.urlopen") as mock_urlopen:
            manager = _build_action_manager(
                StimulusQueue(redis_client=None),
                _Planner(None),
                redis_client=redis_stub,
            )
            try:
                manager.retry_deferred_actions()
                manager.shutdown()
            finally:
                manager.shutdown()

        mock_urlopen.assert_not_called()
        restored = json.loads(redis_stub.hvals(ACTION_REDIS_KEY)[0])
        self.assertEqual(restored["status"], "failed")
        self.assertEqual(restored["result"]["error"], "delivery_status_unknown")

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
        self.assertEqual(plan.executor, "native")
        self.assertEqual(plan.news_feed_urls, ["https://example.com/a.xml", "https://example.com/b.xml"])

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
        self.assertEqual(plan.executor, "native")
        self.assertEqual(plan.news_feed_urls, [])

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

    def test_web_fetch_fallback_preserves_url(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content='我想抓取这个页面 {action:web_fetch, url:"https://example.com/a"}',
            action_request={"type": "web_fetch", "params": 'url:"https://example.com/a"'},
        )

        plan = _fallback_plan(
            raw_action_type="web_fetch",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("https://example.com/a", plan.task)
        self.assertNotIn("{action:web_fetch", plan.task)

    def test_unknown_action_fallback_is_rejected(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content='我想试试一个不存在的动作 {action:foo}',
            action_request={"type": "foo", "params": ""},
        )

        plan = _fallback_plan(
            raw_action_type="foo",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        self.assertEqual(plan, (None, "未知 action：foo；当前不可用。"))

    def test_file_modify_fallback_routes_to_ops_worker(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想改一下配置文件",
            action_request={"type": "file_modify", "params": 'path:"config.yml", instruction:"把日志级别改成 DEBUG"'},
        )

        plan = _fallback_plan(
            raw_action_type="file_modify",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertEqual(plan.worker_agent_id, "seedwake-ops")
        self.assertIn("config.yml", plan.task)

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
            manager.apply_controls([
                _action_control(created[0].action_id, approved=True, actor="alice", note="")
            ])
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
            manager.apply_controls([
                _action_control(created[0].action_id, approved=False, actor="alice", note="不允许")
            ])
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
                _ = key
                self.items.append(payload)

            def lrange(self, key, start, stop):
                _ = key
                _ = start
                _ = stop
                if not self.items:
                    return []
                return [self.items[0]]

            def ltrim(self, key, start, stop):
                _ = key
                _ = stop
                if start <= 0:
                    return
                self.items = self.items[start:]

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

    def test_pop_action_controls_skips_invalid_json_without_losing_following_item(self) -> None:
        class RedisStub:
            def __init__(self):
                self.items = [
                    "{bad json",
                    json.dumps(
                        _action_control("act_2", approved=True, actor="alice", note=""),
                        ensure_ascii=False,
                    ),
                ]

            def lrange(self, key, start, stop):
                _ = key
                _ = start
                _ = stop
                if not self.items:
                    return []
                return [self.items[0]]

            def ltrim(self, key, start, stop):
                _ = key
                _ = stop
                if start <= 0:
                    return
                self.items = self.items[start:]

        redis_stub = RedisStub()

        controls = pop_action_controls(redis_stub)

        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["action_id"], "act_2")


if __name__ == "__main__":
    unittest.main()
