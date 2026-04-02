import json
import io
import unittest
from concurrent.futures import wait
from email.message import Message
from threading import Barrier
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from urllib import error

import redis as redis_lib

# noinspection PyProtectedMember
from core.action import (
    ACTION_REDIS_KEY,
    NOTE_REDIS_KEY,
    ActionPlanner,
    ActionCallbacks,
    ActionManager,
    ActionPlan,
    ActionRedisLike,
    NEWS_SEEN_REDIS_KEY,
    PlannerLike,
    _coerce_action_request_payload,
    _fallback_plan,
    _native_send_message_plan,
    _plan_delegate_tool_call,
    _planner_json_messages,
    _planner_tools,
    _send_telegram_message,
    pop_action_controls,
    push_action_control,
)
# noinspection PyProtectedMember
from core.main import (
    _print_stimuli,
    RECENT_CONVERSATION_SUMMARY_BATCH_MAX_CHARS,
    _recent_conversation_summary_batches,
    _sanitize_cycle_trigger_refs,
    _select_cycle_stimuli,
    _summarize_recent_conversation,
)
from core.model_client import ModelClient
from core.openclaw_gateway import OpenClawGatewayExecutor, OpenClawUnavailableError
from core.perception import PerceptionManager
from core.prompt_builder import build_prompt
from core.rss import read_news_result, summarize_news_items
from core.stimulus import (
    CONVERSATION_HISTORY_KEY,
    RECENT_ACTION_ECHO_KEY,
    CONVERSATION_SUMMARY_KEY,
    ConversationRedisLike,
    RECENT_CONVERSATION_SUMMARY_MAX_CHARS,
    Stimulus,
    StimulusQueue,
    append_conversation_history,
    load_recent_action_echoes,
    load_conversation_history,
    load_recent_conversations,
    remember_recent_action_echoes,
)
from core.types import ConversationEntry, JsonObject, JsonValue, RawActionRequest, RecentConversationPrompt
from core.thought_parser import Thought
from core.types import ActionControl, ActionResultEnvelope, NewsItem
from test_support import ListRedisStub


def _make_thought(
    cycle_id: int = 1,
    index: int = 1,
    thought_type: str = "意图",
    content: str = "我想查一下时间",
    action_request: RawActionRequest | JsonObject | None = None,
) -> Thought:
    return Thought(
        thought_id=f"C{cycle_id}-{index}",
        cycle_id=cycle_id,
        index=index,
        type=thought_type,
        content=content,
        action_request=_as_raw_action_request(action_request),
    )


def _conversation_stimulus(
    source: str = "telegram:1",
    content: str = "你好",
    *,
    message_id: int | None = None,
    metadata: JsonObject | None = None,
) -> Stimulus:
    base_metadata = dict(metadata or {})
    if message_id is not None:
        base_metadata["telegram_message_id"] = message_id
    return Stimulus(
        stimulus_id="stim_conv_1",
        type="conversation",
        priority=1,
        source=source,
        content=content,
        metadata=base_metadata,
    )


class _Planner(PlannerLike):
    def __init__(self, plan: ActionPlan | tuple[None, str | None] | None):
        self._plan = plan

    def plan(
        self,
        thought: Thought,
        *,
        conversation_source: str | None = None,
    ) -> ActionPlan | tuple[None, str | None] | None:
        _ = thought, conversation_source
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


def _as_action_redis(
    value: "redis_lib.Redis | ListRedisStub | _RedisNewsSeenStub | None",
) -> ActionRedisLike | None:
    return value  # type: ignore[return-value]


def _as_conversation_redis(
    value: "redis_lib.Redis | ListRedisStub | _RedisNewsSeenStub | None",
) -> ConversationRedisLike | None:
    return value  # type: ignore[return-value]


def _as_action_plan(plan: ActionPlan | tuple[None, str | None] | None) -> ActionPlan:
    assert isinstance(plan, ActionPlan)
    return plan


def _as_json_object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def _as_json_object_list(value: JsonValue) -> list[JsonObject]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return [cast(JsonObject, item) for item in value]


def _as_string_list(value: JsonValue) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in value]


def _as_raw_action_request(value: RawActionRequest | JsonObject | None) -> RawActionRequest | None:
    if value is None:
        return None
    return cast(RawActionRequest, value)


class _JsonPlannerClient(ModelClient):
    def __init__(self, content: str):
        super().__init__(provider="openai_compatible", supports_tool_calls=False)
        self._content = content

    def generate_text(self, prompt: str, model_config: dict) -> str:
        _ = prompt, model_config
        return ""

    def chat(
        self,
        *,
        model: str,
        messages: list[JsonObject],
        tools: list[JsonObject] | None = None,
        options: dict | None = None,
    ) -> JsonObject:
        _ = model, messages, tools, options
        return {"message": {"content": self._content, "tool_calls": []}}

    def embed_text(self, text: str, model: str) -> list[float]:
        _ = text, model
        return [0.0]

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        _ = model
        return [[0.0] for _ in texts]


def _conversation_summary_stub(
    source_name: str,
    existing_summary: str,
    entries: list[ConversationEntry],
) -> str:
    addition = "；".join(" ".join(str(entry.get("content") or "").split()) for entry in entries if entry.get("content"))
    if existing_summary and addition:
        return f"{existing_summary}｜{addition}"
    if addition:
        return f"{source_name} 摘要：{addition}"
    return existing_summary


def _null_summary_builder(
    source_name: str,
    existing_summary: str,
    entries: list[ConversationEntry],
) -> None:
    _ = source_name, existing_summary, entries
    return None


def _rebuilt_summary_builder(
    source_name: str,
    existing_summary: str,
    entries: list[ConversationEntry],
) -> str:
    _ = source_name, existing_summary, entries
    return "重建后的摘要"


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
    data: JsonObject,
    ok: bool = True,
    error_detail: JsonValue | None = None,
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


def _telegram_http_error(
    *,
    code: int = 403,
    msg: str = "forbidden",
    description: str = "Forbidden: bot was blocked by the user",
) -> error.HTTPError:
    return error.HTTPError(
        url="https://api.telegram.org",
        code=code,
        msg=msg,
        hdrs=Message(),
        fp=io.BytesIO(
            json.dumps({"ok": False, "description": description}, ensure_ascii=False).encode("utf-8")
        ),
    )


def _urlopen_success_response() -> MagicMock:
    response = MagicMock()
    response.read.return_value = b'{"ok": true}'
    return MagicMock(__enter__=MagicMock(return_value=response), __exit__=MagicMock(return_value=None))


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
            created = manager.submit_from_thoughts(thoughts, stimuli=stimuli)
            manager.shutdown_with_timeout(1.0)
            return created


def _build_action_manager(
    queue: StimulusQueue,
    planner: PlannerLike,
    *,
    redis_client: "redis_lib.Redis | ListRedisStub | _RedisNewsSeenStub | None" = None,
    openclaw_executor: _OpenClawExecutor | _UnavailableOpenClawExecutor | None = None,
    news_reader=None,
    contact_resolver=None,
    event_callback=None,
    auto_execute=None,
    require_confirmation=None,
    forbidden=None,
    news_seen_max_items: int = 5000,
) -> ActionManager:
    resolved_redis = _as_action_redis(redis_client)
    return ActionManager(
        redis_client=resolved_redis,
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


def _submit_planner_feedback(
    planner_result: ActionPlan | tuple[None, str | None] | None,
) -> tuple[list, Stimulus]:
    queue = StimulusQueue(redis_client=None)
    manager = _build_action_manager(
        queue,
        _Planner(planner_result),
        auto_execute=["news"],
    )
    try:
        created = manager.submit_from_thoughts([
            _make_thought(action_request={"type": "news", "params": ""})
        ])
    finally:
        manager.shutdown()
    return created, queue.pop_many(limit=1)[0]


def _mock_http_fallback_payload(summary: str = "ok") -> bytes:
    output_text = json.dumps(
        {"ok": True, "summary": summary, "data": {}, "error": None},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return json.dumps(
        {"output": [{"type": "output_text", "text": output_text}]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


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
) -> Stimulus:
    _submit_and_shutdown(manager, thoughts)
    stimuli = queue.pop_many(limit=5)
    test_case.assertEqual(len(stimuli), 1)
    test_case.assertEqual(stimuli[0].type, "news")
    if expected_text:
        test_case.assertIn(expected_text, stimuli[0].content)
    return stimuli[0]


def _assert_news_stimuli_contents(
    test_case: unittest.TestCase,
    queue: StimulusQueue,
    manager: ActionManager,
    thoughts: list[Thought],
    expected_fragments: list[str],
) -> list[Stimulus]:
    _submit_and_shutdown(manager, thoughts)
    stimuli = queue.pop_many(limit=10)
    test_case.assertEqual(len(stimuli), len(expected_fragments))
    test_case.assertTrue(all(stimulus.type == "news" for stimulus in stimuli))
    actual_contents = [stimulus.content for stimulus in stimuli]
    for fragment in expected_fragments:
        test_case.assertTrue(
            any(fragment in content for content in actual_contents),
            msg=f"missing news stimulus fragment: {fragment!r} in {actual_contents!r}",
        )
    return stimuli


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


class _RedisNewsSeenStub(ActionRedisLike):
    def __init__(self):
        self.lists = {}
        self.hashes = {}
        self.sorted_sets = {}
        self.values = {}

    def hset(self, key, hash_field, value):
        self.hashes.setdefault(key, {})[hash_field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value
        return True

    def rpush(self, key, payload):
        self.lists.setdefault(key, []).append(payload)

    def ltrim(self, key, start, end):
        _ = end
        self.lists[key] = self.lists.get(key, [])[start:]

    def lrange(self, key, start, end):
        _ = end
        return self.lists.get(key, [])[start:]

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


class StimulusQueueTests(unittest.TestCase):
    def test_conversation_push_is_recorded_in_history(self) -> None:
        redis_stub = ListRedisStub()
        queue = StimulusQueue(redis_client=_as_conversation_redis(redis_stub))

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

    def test_attach_redis_does_not_duplicate_existing_conversation_history(self) -> None:
        redis_stub = ListRedisStub()
        queue = StimulusQueue(redis_client=None)
        stimulus = queue.push(
            "conversation",
            1,
            "telegram:1",
            "你好",
            metadata={"telegram_full_name": "Alice"},
        )
        append_conversation_history(
            _as_conversation_redis(redis_stub),
            role="user",
            source=stimulus.source,
            content=stimulus.content,
            stimulus_id=stimulus.stimulus_id,
            metadata=stimulus.metadata,
            timestamp=stimulus.timestamp,
        )

        self.assertTrue(queue.attach_redis(_as_conversation_redis(redis_stub)))

        history = load_conversation_history(_as_conversation_redis(redis_stub), limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["stimulus_id"], stimulus.stimulus_id)
        self.assertEqual(history[0]["content"], "你好")

    def test_attach_redis_rehydrates_merged_conversation_history_per_message(self) -> None:
        redis_stub = ListRedisStub()
        queue = StimulusQueue(redis_client=None)
        queue.push(
            "conversation",
            1,
            "telegram:1",
            "第一句",
            metadata={"telegram_full_name": "Alice", "telegram_message_id": 101},
        )
        queue.push(
            "conversation",
            1,
            "telegram:1",
            "第二句",
            metadata={"telegram_full_name": "Alice", "telegram_message_id": 102},
        )

        merged = _select_cycle_stimuli(queue)
        queue.requeue_front(merged)

        self.assertTrue(queue.attach_redis(_as_conversation_redis(redis_stub)))

        history = load_conversation_history(_as_conversation_redis(redis_stub), limit=10)
        self.assertEqual([entry["content"] for entry in history], ["第一句", "第二句"])
        self.assertEqual(
            [entry["stimulus_id"] for entry in history],
            _as_string_list(merged[0].metadata["merged_stimulus_ids"]),
        )
        self.assertNotIn("第一句\n第二句", [entry["content"] for entry in history])

    def test_select_cycle_stimuli_merges_same_source_conversation(self) -> None:
        queue = StimulusQueue(redis_client=None)
        first = queue.push(
            "conversation",
            1,
            "telegram:1",
            "你好",
            metadata={"telegram_message_id": 101},
        )
        queue.push(
            "conversation",
            1,
            "telegram:1",
            "你在做什么？",
            metadata={"telegram_message_id": 102},
        )
        queue.push(
            "conversation",
            1,
            "telegram:1",
            "你是谁？",
            metadata={"telegram_message_id": 103},
        )

        selected = _select_cycle_stimuli(queue)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].source, "telegram:1")
        self.assertEqual(selected[0].stimulus_id, first.stimulus_id)
        self.assertEqual(selected[0].content, "你好\n你在做什么？\n你是谁？")
        self.assertEqual(selected[0].metadata["merged_count"], 3)
        self.assertEqual(len(_as_string_list(selected[0].metadata["merged_stimulus_ids"])), 3)
        self.assertEqual(selected[0].metadata["telegram_message_id"], 103)
        self.assertEqual(len(_as_json_object_list(selected[0].metadata["merged_messages"])), 3)

    def test_select_cycle_stimuli_preserves_merged_messages_across_retry(self) -> None:
        queue = StimulusQueue(redis_client=None)
        first = queue.push(
            "conversation",
            1,
            "telegram:1",
            "第一句",
            metadata={"telegram_message_id": 101},
        )
        second = queue.push(
            "conversation",
            1,
            "telegram:1",
            "第二句",
            metadata={"telegram_message_id": 102},
        )

        first_round = _select_cycle_stimuli(queue)
        queue.requeue_front(first_round)
        second_round = _select_cycle_stimuli(queue)

        self.assertEqual(len(second_round), 1)
        self.assertEqual(second_round[0].content, "第一句\n第二句")
        self.assertEqual(second_round[0].metadata["merged_count"], 2)
        self.assertEqual(
            _as_string_list(second_round[0].metadata["merged_stimulus_ids"]),
            [first.stimulus_id, second.stimulus_id],
        )
        self.assertEqual(
            [message["content"] for message in _as_json_object_list(second_round[0].metadata["merged_messages"])],
            ["第一句", "第二句"],
        )
        self.assertEqual(second_round[0].metadata["telegram_message_id"], 102)

    def test_select_cycle_stimuli_merges_retry_conversation_with_new_same_source_message(self) -> None:
        queue = StimulusQueue(redis_client=None)
        first = queue.push(
            "conversation",
            1,
            "telegram:1",
            "第一句",
            metadata={"telegram_message_id": 101},
        )
        second = queue.push(
            "conversation",
            1,
            "telegram:1",
            "第二句",
            metadata={"telegram_message_id": 102},
        )

        first_round = _select_cycle_stimuli(queue)
        queue.requeue_front(first_round)
        third = queue.push(
            "conversation",
            1,
            "telegram:1",
            "第三句",
            metadata={"telegram_message_id": 103},
        )

        second_round = _select_cycle_stimuli(queue)

        self.assertEqual(len(second_round), 1)
        self.assertEqual(second_round[0].content, "第一句\n第二句\n第三句")
        self.assertEqual(second_round[0].metadata["merged_count"], 3)
        self.assertEqual(
            _as_string_list(second_round[0].metadata["merged_stimulus_ids"]),
            [first.stimulus_id, second.stimulus_id, third.stimulus_id],
        )
        self.assertEqual(
            [message["content"] for message in _as_json_object_list(second_round[0].metadata["merged_messages"])],
            ["第一句", "第二句", "第三句"],
        )
        self.assertEqual(second_round[0].metadata["telegram_message_id"], 103)

    def test_prompt_keeps_reply_context_on_only_the_replied_message_after_merge(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push(
            "conversation",
            1,
            "telegram:1",
            "第一句",
            metadata={
                "telegram_full_name": "Alice",
                "telegram_message_id": 101,
            },
        )
        queue.push(
            "conversation",
            1,
            "telegram:1",
            "第二句",
            metadata={
                "telegram_full_name": "Alice",
                "telegram_message_id": 102,
                "reply_to_message_id": 98,
                "reply_to_preview": "之前那句",
                "reply_to_from_self": True,
            },
        )

        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake。"},
            [],
            30,
            stimuli=_select_cycle_stimuli(queue),
        )

        self.assertIn(
            '[Alice](telegram:1) [msg:101] 说：\n第一句\n\n[Alice](telegram:1) [msg:102] 引用了我之前说的 [msg:98]：“之前那句” 说：\n第二句',
            prompt,
        )
        self.assertNotIn(
            '[Alice](telegram:1) [msg:101] 引用了我之前说的 [msg:98]：“之前那句” 说：\n第一句',
            prompt,
        )

    def test_select_cycle_stimuli_keeps_other_sources_for_later_rounds(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("conversation", 1, "telegram:1", "Alice 1")
        queue.push("conversation", 1, "telegram:2", "Bob 1")
        queue.push("conversation", 1, "telegram:1", "Alice 2")

        first_round = _select_cycle_stimuli(queue)
        second_round = _select_cycle_stimuli(queue)

        self.assertEqual(len(first_round), 1)
        self.assertEqual(first_round[0].source, "telegram:1")
        self.assertEqual(first_round[0].content, "Alice 1\nAlice 2")
        self.assertEqual(len(second_round), 1)
        self.assertEqual(second_round[0].source, "telegram:2")
        self.assertEqual(second_round[0].content, "Bob 1")

    def test_select_cycle_stimuli_keeps_one_conversation_and_one_non_conversation(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("conversation", 1, "telegram:1", "你好")
        queue.push("conversation", 1, "telegram:1", "你在做什么？")
        queue.push("conversation", 1, "telegram:2", "别人的消息")
        queue.push("weather", 2, "action:weather", "塔林，多云，5C")

        first_round = _select_cycle_stimuli(queue)
        second_round = _select_cycle_stimuli(queue)

        self.assertEqual([stimulus.type for stimulus in first_round], ["conversation", "weather"])
        self.assertEqual(first_round[0].content, "你好\n你在做什么？")
        self.assertEqual(first_round[1].content, "塔林，多云，5C")
        self.assertEqual(len(second_round), 1)
        self.assertEqual(second_round[0].source, "telegram:2")

    def test_print_stimuli_flattens_merged_conversation_for_logs(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("conversation", 1, "telegram:1", "你好")
        queue.push("conversation", 1, "telegram:1", "你在做什么？")
        selected = _select_cycle_stimuli(queue)

        log_buffer = io.StringIO()
        with patch("sys.stdout", new=io.StringIO()):
            _print_stimuli(log_buffer, selected)

        output = log_buffer.getvalue()
        self.assertIn("[conversation] 你好 | 你在做什么？", output)
        self.assertNotIn("[conversation] 你好\n你在做什么？", output)

    def test_select_cycle_stimuli_drops_background_passive_stimuli_during_conversation(self) -> None:
        queue = StimulusQueue(redis_client=None)
        queue.push("conversation", 1, "telegram:1", "我之前问你最近有什么新闻？")
        queue.push("system_status", 4, "system:status", "1 分钟负载 0.71 / CPU 32；磁盘 20%；内存 22%")
        queue.push("time", 4, "system:clock", "现在是晚上")

        selected = _select_cycle_stimuli(queue)
        next_round = _select_cycle_stimuli(queue)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].type, "conversation")
        self.assertEqual(next_round, [])


class PromptBuilderPhase3Tests(unittest.TestCase):
    def test_prompt_includes_reordered_sections_and_sanitized_action_summaries(self) -> None:
        redis_client = ListRedisStub()
        append_conversation_history(
            redis_client,
            role="user",
            source="telegram:1",
            content="刚刚那个建议我记住了。",
            metadata={"telegram_full_name": "Alice"},
            timestamp=_make_thought(1, 1).timestamp,
        )
        append_conversation_history(
            redis_client,
            role="assistant",
            source="telegram:1",
            content="好，那我就接着陪你聊。",
            timestamp=_make_thought(1, 2).timestamp,
        )
        queue = StimulusQueue(redis_client=None)
        stimulus = queue.push(
            "conversation",
            1,
            "telegram:1",
            "谢谢你",
            metadata={
                "telegram_full_name": "Alice",
                "telegram_message_id": 305,
                "reply_to_message_id": 298,
                "reply_to_preview": "好，我自己找一篇关于有氧锻炼的文章",
                "reply_to_from_self": True,
            },
        )
        passive = Stimulus(
            stimulus_id="stim_time",
            type="time",
            priority=4,
            source="system:clock",
            content="现在是晚上",
        )
        action_echo = Stimulus(
            stimulus_id="stim_search",
            type="action_result",
            priority=2,
            source="action:act_1",
            content="搜索完成\n1. 标题 (https://example.com) —— 摘要",
            metadata={"origin": "action", "action_type": "search"},
        )
        action = MagicMock()
        action.action_id = "act_1"
        action.type = "search"
        action.executor = "openclaw"
        action.status = "running"
        action.request = {
            "task": (
                "围绕“反馈”进行搜索，返回按相关性整理的简洁结果。\n\n"
                '严格按以下 JSON 返回：{"results":[{"title":"","url":"","snippet":""}]}'
            )
        }
        action.source_content = '我想搜一下最近的反馈 {action:search, query:"反馈"}'
        completed = MagicMock()
        completed.action_id = "act_2"
        completed.type = "send_message"
        completed.executor = "native"
        completed.status = "succeeded"
        completed.request = {
            "task": "向 telegram:1 发送消息：我在。",
            "target_source": "telegram:1",
            "message_text": "我在。",
        }
        completed.source_content = "我想赶紧回一句"
        recent = [_make_thought(cycle_id=2, index=1, thought_type="思考", content="之前的念头")]

        prompt = build_prompt(
            3,
            {
                "self_description": "我是 Seedwake。",
                "core_goals": "探索和学习。",
                "self_understanding": "我会在经验里慢慢形成自己。",
            },
            recent,
            30,
            long_term_context=["之前某次读到过关于雨后气味的解释。"],
            note_text="记下：不要把刚刚的直觉弄丢。",
            stimuli=[stimulus, passive, action_echo],
            running_actions=[action, completed],
            perception_cues=["了解外界动态——最近发生了什么？"],
            recent_conversations=load_recent_conversations(
                redis_client,
                include_sources={"telegram:1"},
            ),
        )

        self.assertIn("## 最近的念头", prompt)
        self.assertIn("## 浮上来的记忆", prompt)
        self.assertIn("## 我的笔记", prompt)
        self.assertIn("## 好像有一阵子没有……", prompt)
        self.assertIn("## 我已经发起、正在等回音的事", prompt)
        self.assertIn("## 此刻我注意到", prompt)
        self.assertIn("## 行动有了回音", prompt)
        self.assertIn("## 最近的对话", prompt)
        self.assertIn("## 有人对我说话了", prompt)
        self.assertIn("## 接下来的念头", prompt)
        self.assertLess(prompt.index("## 最近的念头"), prompt.index("## 浮上来的记忆"))
        self.assertLess(prompt.index("## 浮上来的记忆"), prompt.index("## 我的笔记"))
        self.assertLess(prompt.index("## 我的笔记"), prompt.index("## 好像有一阵子没有……"))
        self.assertLess(prompt.index("## 好像有一阵子没有……"), prompt.index("## 行动有了回音"))
        self.assertLess(prompt.index("## 行动有了回音"), prompt.index("## 我已经发起、正在等回音的事"))
        self.assertLess(prompt.index("## 我已经发起、正在等回音的事"), prompt.index("## 此刻我注意到"))
        self.assertLess(prompt.index("## 此刻我注意到"), prompt.index("## 最近的对话"))
        self.assertLess(prompt.index("## 最近的对话"), prompt.index("## 有人对我说话了"))
        self.assertLess(prompt.index("## 行动有了回音"), prompt.index("## 有人对我说话了"))
        self.assertIn('[Alice](telegram:1) [msg:305] 引用了我之前说的 [msg:298]：“好，我自己找一篇关于有氧锻炼的文章” 说：谢谢你', prompt)
        self.assertIn("与 [Alice](telegram:1) 的近期对话（最后一条消息时间：", prompt)
        self.assertIn("我：好，那我就接着陪你聊。", prompt)
        self.assertIn("如果我决定回应，需要用 {action:send_message} 真正把话发出去", prompt)
        self.assertIn("[时间感] 现在是晚上", prompt)
        self.assertIn("[搜索结果] 搜索完成 1. 标题 (https://example.com) —— 摘要", prompt)
        self.assertIn("[search/running] 围绕“反馈”进行搜索，返回按相关性整理的简洁结果。", prompt)
        self.assertNotIn("[send_message/succeeded]", prompt)
        self.assertNotIn('{"results":[{"title":"","url":"","snippet":""}]}', prompt)
        self.assertNotIn("我想搜一下最近的反馈", prompt)
        self.assertIn("好像有一阵子没有", prompt)
        self.assertIn("外界动态", prompt)
        self.assertIn("探索和学习。", prompt)
        self.assertIn("我会在经验里慢慢形成自己。", prompt)
        self.assertIn("记下：不要把刚刚的直觉弄丢。", prompt)
        self.assertIn("{action:web_fetch", prompt)
        self.assertIn("{action:system_change", prompt)
        self.assertIn("{action:note_rewrite", prompt)
        self.assertIn("不要发明未列出的 action 名称", prompt)
        self.assertIn("历史里出现的 [思考-CX-Y]、[意图-CX-Y]、[反应-CX-Y] 是系统记录用编号", prompt)
        self.assertIn("- [思考] — 思维、分析、联想、好奇", prompt)
        self.assertNotIn("- [思考-CX-Y] — 思维、分析、联想、好奇", prompt)
        self.assertIn("我想说的话", prompt)
        self.assertIn("我自己想读的内容", prompt)
        self.assertIn("这种回应必须外化成 {action:send_message, ...}", prompt)
        self.assertIn("对话是前景，时间感和身体感觉只是背景", prompt)
        self.assertNotIn("你想发出的内容", prompt)

    def test_prompt_hides_note_section_when_empty(self) -> None:
        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake。"},
            [],
            30,
            note_text="",
        )

        self.assertNotIn("## 我的笔记", prompt)

    def test_action_echo_section_lists_recent_before_current(self) -> None:
        recent_echo = Stimulus(
            stimulus_id="stim_recent_search",
            type="action_result",
            priority=2,
            source="action:act_old",
            content="搜索完成\n1. 旧结果",
            action_id="act_old",
            metadata={"origin": "action", "action_type": "search", "status": "succeeded"},
        )
        current_echo = Stimulus(
            stimulus_id="stim_current_news",
            type="news",
            priority=4,
            source="action:act_new",
            content="已查看 RSS，没有新的新闻条目",
            action_id="act_new",
            metadata={"origin": "action", "action_type": "news", "status": "succeeded"},
        )

        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake。"},
            [],
            30,
            stimuli=[current_echo],
            recent_action_echoes=[recent_echo],
        )

        self.assertIn("## 行动有了回音", prompt)
        self.assertIn("最近的行动回音：", prompt)
        self.assertIn("刚刚收到的行动回音：", prompt)
        self.assertLess(prompt.index("最近的行动回音："), prompt.index("刚刚收到的行动回音："))
        self.assertIn("[搜索结果] 搜索完成 1. 旧结果", prompt)
        self.assertIn("[外界消息] 已查看 RSS，没有新的新闻条目", prompt)

    def test_load_recent_conversations_builds_summary_and_keeps_recent_raw_lines(self) -> None:
        redis_client = ListRedisStub()
        for index in range(12):
            append_conversation_history(
                _as_conversation_redis(redis_client),
                role="user" if index % 2 == 0 else "assistant",
                source="telegram:1",
                content=f"第{index + 1}句",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=_conversation_summary_stub,
        )

        self.assertEqual(len(conversations), 1)
        conversation = conversations[0]
        self.assertEqual(conversation["source_name"], "Alice")
        self.assertEqual(conversation["source_label"], "[Alice](telegram:1)")
        self.assertEqual(len(conversation["messages"]), 10)
        self.assertEqual(conversation["summary"], "Alice 摘要：第1句；第2句")
        self.assertEqual(conversation["messages"][0]["content"], "第3句")
        self.assertEqual(conversation["messages"][0]["speaker_name"], "Alice")
        stored_summary = json.loads(redis_client.hashes[CONVERSATION_SUMMARY_KEY]["telegram:1"])
        self.assertEqual(stored_summary["version"], 2)
        self.assertEqual(stored_summary["summary"], conversation["summary"])
        self.assertTrue(stored_summary["absorbed_until"])

    def test_prompt_formats_recent_conversation_with_inline_timestamp_and_short_names(self) -> None:
        recent_conversations: list[RecentConversationPrompt] = [{
            "source": "telegram:1",
            "source_name": "Alice",
            "source_label": "[Alice](telegram:1)",
            "summary": "Alice说“更早那句”，我回应“收到。”",
            "last_timestamp": "2026-03-30T01:43:00+00:00",
            "messages": [
                {
                    "role": "user",
                    "speaker_name": "Alice",
                    "content": "最近这句",
                    "timestamp": "2026-03-30T01:42:00+00:00",
                },
                {
                    "role": "assistant",
                    "speaker_name": "我",
                    "content": "我在。",
                    "timestamp": "2026-03-30T01:43:00+00:00",
                },
            ],
        }]
        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake。"},
            [],
            30,
            recent_conversations=recent_conversations,
        )

        self.assertIn("与 [Alice](telegram:1) 的近期对话（最后一条消息时间：", prompt)
        self.assertIn("更早的对话摘要：Alice说“更早那句”，我回应“收到。”", prompt)
        self.assertIn("Alice：最近这句", prompt)
        self.assertIn("我：我在。", prompt)
        self.assertNotIn("[Alice](telegram:1)：最近这句", prompt)

    def test_load_recent_conversations_does_not_reabsorb_same_old_messages(self) -> None:
        redis_client = ListRedisStub()
        for index in range(12):
            append_conversation_history(
                _as_conversation_redis(redis_client),
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        summary_calls: list[list[str]] = []

        def summary_builder(
            source_name: str,
            existing_summary: str,
            entries: list[ConversationEntry],
        ) -> str | None:
            _ = source_name, existing_summary
            summary_calls.append([str(entry.get("content") or "") for entry in entries])
            return _conversation_summary_stub(source_name, existing_summary, entries)

        first = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=summary_builder,
        )
        second = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=summary_builder,
        )

        self.assertEqual(first[0]["summary"], second[0]["summary"])
        self.assertEqual(summary_calls, [["第1句", "第2句"]])

    def test_load_recent_conversations_excludes_current_cycle_messages(self) -> None:
        redis_client = ListRedisStub()
        append_conversation_history(
            redis_client,
            role="user",
            source="telegram:1",
            content="更早那句",
            stimulus_id="stim_old",
            metadata={"telegram_full_name": "Alice"},
            timestamp=_make_thought(1, 1).timestamp,
        )
        append_conversation_history(
            redis_client,
            role="user",
            source="telegram:1",
            content="当前这句",
            stimulus_id="stim_current",
            metadata={"telegram_full_name": "Alice"},
            timestamp=_make_thought(1, 2).timestamp,
        )

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            exclude_stimulus_ids={"stim_current"},
        )

        self.assertEqual([message["content"] for message in conversations[0]["messages"]], ["更早那句"])

    def test_load_recent_conversations_keeps_persistent_summary_when_hidden_messages_shift_window(self) -> None:
        redis_client = ListRedisStub()
        for index in range(20):
            append_conversation_history(
                _as_conversation_redis(redis_client),
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                stimulus_id=f"stim_{index + 1}",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=_conversation_summary_stub,
        )

        builder_calls: list[tuple[str, str, list[str]]] = []

        def prompt_summary_builder(
            source_name: str,
            existing_summary: str,
            entries: list[ConversationEntry],
        ) -> str:
            builder_calls.append((
                source_name,
                existing_summary,
                [str(entry.get("content") or "") for entry in entries],
            ))
            return "不该触发的新摘要"

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            exclude_stimulus_ids={"stim_19", "stim_20"},
            summary_builder=prompt_summary_builder,
        )

        self.assertEqual(
            conversations[0]["summary"],
            "Alice 摘要：" + "；".join(f"第{i}句" for i in range(1, 11)),
        )
        self.assertEqual(
            [message["content"] for message in conversations[0]["messages"]],
            [f"第{i}句" for i in range(9, 19)],
        )
        self.assertEqual(builder_calls, [])

    def test_load_recent_conversations_refreshes_persistent_summary_from_full_history(self) -> None:
        redis_client = ListRedisStub()
        for index in range(12):
            append_conversation_history(
                redis_client,
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                stimulus_id=f"stim_{index + 1}",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=_conversation_summary_stub,
        )

        for index in range(12, 14):
            append_conversation_history(
                redis_client,
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                stimulus_id=f"stim_{index + 1}",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        builder_calls: list[tuple[str, str, list[str]]] = []

        def prompt_summary_builder(
            source_name: str,
            existing_summary: str,
            entries: list[ConversationEntry],
        ) -> str:
            builder_calls.append((
                source_name,
                existing_summary,
                [str(entry.get("content") or "") for entry in entries],
            ))
            return _conversation_summary_stub(source_name, existing_summary, entries)

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            exclude_stimulus_ids={"stim_13", "stim_14"},
            summary_builder=prompt_summary_builder,
        )

        self.assertEqual(conversations[0]["summary"], "Alice 摘要：第1句；第2句｜第3句；第4句")
        self.assertEqual(
            builder_calls,
            [("Alice", "Alice 摘要：第1句；第2句", ["第3句", "第4句"])],
        )

    def test_recent_action_echo_cache_keeps_info_results_for_following_cycles(self) -> None:
        redis_client = ListRedisStub()
        current_echo = Stimulus(
            stimulus_id="stim_search",
            type="action_result",
            priority=2,
            source="action:act_search",
            content="搜索完成\n1. 标题",
            action_id="act_search",
            metadata={"origin": "action", "action_type": "search", "status": "succeeded"},
        )
        remember_recent_action_echoes(_as_conversation_redis(redis_client), 10, [current_echo])

        recent = load_recent_action_echoes(
            _as_conversation_redis(redis_client),
            current_cycle_id=11,
            exclude_action_ids=set(),
        )

        self.assertEqual([stimulus.action_id for stimulus in recent], ["act_search"])
        stored = redis_client.lrange(RECENT_ACTION_ECHO_KEY, 0, -1)
        self.assertEqual(len(stored), 1)

    def test_recent_action_echo_cache_excludes_current_action_ids(self) -> None:
        redis_client = ListRedisStub()
        current_echo = Stimulus(
            stimulus_id="stim_search",
            type="action_result",
            priority=2,
            source="action:act_search",
            content="搜索完成\n1. 标题",
            action_id="act_search",
            metadata={"origin": "action", "action_type": "search", "status": "succeeded"},
        )
        remember_recent_action_echoes(_as_conversation_redis(redis_client), 10, [current_echo])

        recent = load_recent_action_echoes(
            _as_conversation_redis(redis_client),
            current_cycle_id=11,
            exclude_action_ids={"act_search"},
        )

        self.assertEqual(recent, [])

    def test_load_recent_conversations_skips_empty_recent_block_for_current_only_source(self) -> None:
        redis_client = ListRedisStub()
        append_conversation_history(
            redis_client,
            role="user",
            source="telegram:1",
            content="当前这句",
            stimulus_id="stim_current",
            metadata={"telegram_full_name": "Alice"},
            timestamp=_make_thought(1, 1).timestamp,
        )

        conversations = load_recent_conversations(
            redis_client,
            include_sources={"telegram:1"},
            exclude_stimulus_ids={"stim_current"},
        )

        self.assertEqual(conversations, [])

    def test_load_recent_conversations_does_not_upgrade_summary_when_rebuild_fails(self) -> None:
        redis_client = ListRedisStub()
        redis_client.hset(
            CONVERSATION_SUMMARY_KEY,
            "telegram:1",
            json.dumps({"summary": "旧摘要", "absorbed_until": "", "version": 1}, ensure_ascii=False),
        )
        for index in range(12):
            append_conversation_history(
                redis_client,
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=_null_summary_builder,
        )

        self.assertEqual(conversations[0]["summary"], "旧摘要")
        stored_summary = json.loads(redis_client.hashes[CONVERSATION_SUMMARY_KEY]["telegram:1"])
        self.assertEqual(stored_summary["version"], 1)
        self.assertEqual(stored_summary["absorbed_until"], "")

    def test_load_recent_conversations_rebuild_ignores_legacy_summary_text(self) -> None:
        redis_client = ListRedisStub()
        redis_client.hset(
            CONVERSATION_SUMMARY_KEY,
            "telegram:1",
            json.dumps({"summary": "旧的脏摘要", "absorbed_until": "", "version": 1}, ensure_ascii=False),
        )
        for index in range(12):
            append_conversation_history(
                redis_client,
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        builder_calls: list[tuple[str, str, list[str]]] = []

        def summary_builder(
            source_name: str,
            existing_summary: str,
            entries: list[ConversationEntry],
        ) -> str:
            builder_calls.append((
                source_name,
                existing_summary,
                [str(entry.get("content") or "") for entry in entries],
            ))
            return "新的干净摘要"

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=summary_builder,
        )

        self.assertEqual(conversations[0]["summary"], "新的干净摘要")
        self.assertEqual(len(builder_calls), 1)
        self.assertEqual(builder_calls[0][0], "Alice")
        self.assertEqual(builder_calls[0][1], "")
        self.assertEqual(builder_calls[0][2], ["第1句", "第2句"])

    def test_load_recent_conversations_tolerates_malformed_summary_version(self) -> None:
        redis_client = ListRedisStub()
        redis_client.hset(
            CONVERSATION_SUMMARY_KEY,
            "telegram:1",
            json.dumps({"summary": "旧摘要", "absorbed_until": "", "version": "v2"}, ensure_ascii=False),
        )
        for index in range(12):
            append_conversation_history(
                redis_client,
                role="user",
                source="telegram:1",
                content=f"第{index + 1}句",
                metadata={"telegram_full_name": "Alice"},
                timestamp=_make_thought(1, index + 1).timestamp,
            )

        conversations = load_recent_conversations(
            _as_conversation_redis(redis_client),
            include_sources={"telegram:1"},
            summary_builder=_rebuilt_summary_builder,
        )

        self.assertEqual(conversations[0]["summary"], "重建后的摘要")
        stored_summary = json.loads(redis_client.hashes[CONVERSATION_SUMMARY_KEY]["telegram:1"])
        self.assertEqual(stored_summary["version"], 2)
        self.assertEqual(stored_summary["summary"], "重建后的摘要")

    def test_recent_conversation_summary_batches_clip_single_oversized_message(self) -> None:
        long_content = "很长的内容" * 2000
        entries: list[ConversationEntry] = [{
            "entry_id": "1",
            "role": "user",
            "source": "telegram:1",
            "content": long_content,
            "timestamp": "",
            "stimulus_id": None,
            "metadata": {},
        }]
        batches = _recent_conversation_summary_batches(entries, "Jam")

        self.assertEqual(len(batches), 1)
        self.assertLessEqual(len(batches[0]), RECENT_CONVERSATION_SUMMARY_BATCH_MAX_CHARS)
        self.assertIn("Jam：", batches[0])
        self.assertIn("...", batches[0])
        self.assertGreaterEqual(len(batches[0]), RECENT_CONVERSATION_SUMMARY_MAX_CHARS)
        self.assertNotIn(long_content, batches[0])

    def test_summarize_recent_conversation_uses_model_output(self) -> None:
        client = MagicMock()
        client.chat.return_value = {"message": {"content": "  摘要：Jam 先打了个招呼，我回应了。  "}}
        entries: list[ConversationEntry] = [
            {
                "entry_id": "1",
                "role": "user",
                "source": "telegram:1",
                "content": "你好",
                "timestamp": "",
                "stimulus_id": None,
                "metadata": {},
            },
            {
                "entry_id": "2",
                "role": "assistant",
                "source": "telegram:1",
                "content": "你好，我在。",
                "timestamp": "",
                "stimulus_id": None,
                "metadata": {},
            },
        ]

        summary = _summarize_recent_conversation(
            client,
            {"name": "openclaw/main"},
            1,
            "Jam",
            "",
            entries,
            None,
        )

        self.assertEqual(summary, "Jam 先打了个招呼，我回应了。")
        client.chat.assert_called_once()
        request_text = client.chat.call_args.kwargs["messages"][1]["content"]
        self.assertIn("已有摘要：", request_text)
        self.assertIn("Jam：你好", request_text)
        self.assertIn("我：你好，我在。", request_text)

    def test_summarize_recent_conversation_batches_cover_full_history(self) -> None:
        client = MagicMock()
        call_count = {"value": 0}

        def chat_side_effect(**kwargs) -> dict:
            _ = kwargs
            call_count["value"] += 1
            return {"message": {"content": f"摘要：第{call_count['value']}段总结。"}}

        client.chat.side_effect = chat_side_effect
        entries: list[ConversationEntry] = [
            {
                "entry_id": str(i),
                "role": "user",
                "source": "telegram:1",
                "content": f"第{i}句 " + ("很长的内容 " * 30),
                "timestamp": "",
                "stimulus_id": None,
                "metadata": {},
            }
            for i in range(1, 31)
        ]

        summary = _summarize_recent_conversation(
            client,
            {"name": "openclaw/main"},
            1,
            "Jam",
            "",
            entries,
            None,
        )

        self.assertEqual(summary, f"第{client.chat.call_count}段总结。")
        self.assertGreater(client.chat.call_count, 1)
        first_request = client.chat.call_args_list[0].kwargs["messages"][1]["content"]
        last_request = client.chat.call_args_list[-1].kwargs["messages"][1]["content"]
        self.assertIn("Jam：第1句", first_request)
        self.assertIn("Jam：第30句", last_request)

    def test_summarize_recent_conversation_writes_summary_prompt_to_prompt_log(self) -> None:
        client = MagicMock()
        client.chat.return_value = {"message": {"content": "摘要：Jam 先打了个招呼。"}}
        prompt_log = io.StringIO()
        entries: list[ConversationEntry] = [{
            "entry_id": "1",
            "role": "user",
            "source": "telegram:1",
            "content": "你好",
            "timestamp": "",
            "stimulus_id": None,
            "metadata": {},
        }]

        summary = _summarize_recent_conversation(
            client,
            {"name": "openclaw/main"},
            12,
            "Jam",
            "",
            entries,
            prompt_log,
        )

        self.assertEqual(summary, "Jam 先打了个招呼。")
        logged_prompt = prompt_log.getvalue()
        self.assertIn("SUMMARY PROMPT C12 Jam B1/1", logged_prompt)
        self.assertIn("[SYSTEM]", logged_prompt)
        self.assertIn("[USER]", logged_prompt)

    def test_running_send_message_summary_uses_request_not_source_content(self) -> None:
        action = MagicMock()
        action.action_id = "act_1"
        action.type = "send_message"
        action.executor = "native"
        action.status = "running"
        action.request = {
            "task": "向 telegram:1 发送消息：我在。",
            "target_source": "telegram:1",
            "message_text": "我在。",
        }
        action.source_content = '那句"你怎么不说话"又把我往前推了一步'

        prompt = build_prompt(
            3,
            {
                "self_description": "我是 Seedwake。",
                "core_goals": "探索和学习。",
                "self_understanding": "我会在经验里慢慢形成自己。",
            },
            [_make_thought(cycle_id=2, index=1, thought_type="思考", content="之前的念头")],
            30,
            running_actions=[action],
        )

        self.assertIn('[send_message/running] 给 telegram:1 发送消息：“我在。”', prompt)
        self.assertNotIn("你怎么不说话", prompt)

    def test_send_message_action_echo_uses_known_person_label(self) -> None:
        recent_conversations: list[RecentConversationPrompt] = [{
            "source": "telegram:1",
            "source_name": "Alice",
            "source_label": "[Alice](telegram:1)",
            "summary": "",
            "last_timestamp": "2026-03-30T01:43:00+00:00",
            "messages": [],
        }]
        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake。"},
            [],
            30,
            stimuli=[
                Stimulus(
                    stimulus_id="stim_send_echo",
                    type="action_result",
                    priority=2,
                    source="action:act_send",
                    content='已成功发送给 telegram:1：“我在。”',
                    metadata={
                        "origin": "action",
                        "action_type": "send_message",
                        "result": {
                            "data": {"source": "telegram:1", "message": "我在。"},
                        },
                    },
                ),
            ],
            recent_conversations=recent_conversations,
        )

        self.assertIn('[发信结果] 已成功发送给 [Alice](telegram:1)：“我在。”', prompt)

    def test_send_message_failed_action_echo_includes_message_and_reason(self) -> None:
        recent_conversations: list[RecentConversationPrompt] = [{
            "source": "telegram:1",
            "source_name": "Alice",
            "source_label": "[Alice](telegram:1)",
            "summary": "",
            "last_timestamp": "2026-03-30T01:43:00+00:00",
            "messages": [],
        }]
        prompt = build_prompt(
            3,
            {"self_description": "我是 Seedwake。"},
            [],
            30,
            stimuli=[
                Stimulus(
                    stimulus_id="stim_send_failed",
                    type="action_result",
                    priority=2,
                    source="action:act_send_failed",
                    content="发送给 telegram:1 失败：“我在。” （Telegram 发送失败：http_400）",
                    metadata={
                        "origin": "action",
                        "action_type": "send_message",
                        "result": {
                            "ok": False,
                            "summary": "Telegram 发送失败：http_400",
                            "data": {"source": "telegram:1", "message": "我在。"},
                        },
                    },
                ),
            ],
            recent_conversations=recent_conversations,
        )

        self.assertIn(
            '[发信结果] 发送给 [Alice](telegram:1) 失败：“我在。” （Telegram 发送失败：http_400）',
            prompt,
        )


class TriggerValidationTests(unittest.TestCase):
    def test_sanitize_cycle_trigger_refs_strips_forward_reference(self) -> None:
        recent = [_make_thought(cycle_id=235, index=1, content="之前的念头")]
        thoughts = [
            Thought(
                thought_id="C236-1",
                cycle_id=236,
                index=1,
                type="思考",
                content="有效引用",
                trigger_ref="C235-1",
            ),
            Thought(
                thought_id="C236-2",
                cycle_id=236,
                index=2,
                type="反应",
                content="无效引用",
                trigger_ref="C237-1",
            ),
        ]

        _sanitize_cycle_trigger_refs(thoughts, recent)

        self.assertEqual(thoughts[0].trigger_ref, "C235-1")
        self.assertIsNone(thoughts[1].trigger_ref)

    def test_sanitize_cycle_trigger_refs_keeps_same_cycle_backward_reference(self) -> None:
        recent = [_make_thought(cycle_id=235, index=1, content="之前的念头")]
        thoughts = [
            Thought(
                thought_id="C236-1",
                cycle_id=236,
                index=1,
                type="反应",
                content="第一条",
            ),
            Thought(
                thought_id="C236-2",
                cycle_id=236,
                index=2,
                type="思考",
                content="第二条",
                trigger_ref="C236-1",
            ),
            Thought(
                thought_id="C236-3",
                cycle_id=236,
                index=3,
                type="反应",
                content="第三条",
                trigger_ref="C236-4",
            ),
        ]

        _sanitize_cycle_trigger_refs(thoughts, recent)

        self.assertIsNone(thoughts[0].trigger_ref)
        self.assertEqual(thoughts[1].trigger_ref, "C236-1")
        self.assertIsNone(thoughts[2].trigger_ref)


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
        data = _as_json_object(result["data"])
        self.assertEqual(len(cast(list, data["items"])), 1)
        self.assertIn("errors", data)

    def test_read_news_result_summary_clips_long_title_label(self) -> None:
        long_title = "超长标题" * 80
        result = _action_result(
            summary=summarize_news_items([{
                "feed_url": "https://example.com/rss.xml",
                "guid": "item-1",
                "link": "https://example.com/1",
                "title": long_title,
                "published_at": "2026-03-27T10:00:00+00:00",
                "summary": "",
            }]),
            data={"items": []},
            run_id="run_news_summary_title_clip",
            session_key="seedwake:action:act_C1-1",
        )

        self.assertIn("...", result["summary"])
        self.assertNotIn(long_title, result["summary"])


class PerceptionManagerTests(unittest.TestCase):
    def test_collect_passive_stimuli_emits_time_and_system_status(self) -> None:
        manager = PerceptionManager.from_config({
            "passive_time_interval_cycles": 12,
            "passive_system_status_interval_cycles": 24,
        })

        stimuli = manager.collect_passive_stimuli(1)

        self.assertEqual({item["type"] for item in stimuli}, {"time", "system_status"})
        time_payload = next(item for item in stimuli if item["type"] == "time")
        self.assertIn("现在是 ", time_payload["content"])
        self.assertNotIn("UTC 时间", time_payload["content"])
        system_payload = next(item for item in stimuli if item["type"] == "system_status")
        self.assertIn("1 分钟负载", system_payload["content"])
        self.assertIn("核", system_payload["content"])
        self.assertNotIn("/ CPU", system_payload["content"])

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
        joined = " ".join(cues)
        self.assertIn("外界动态", joined)
        self.assertIn("天气", joined)
        self.assertIn("读", joined)

        self.assertEqual(manager.build_prompt_cues(2, []), [])

    def test_build_prompt_cues_skips_news_when_feed_not_configured(self) -> None:
        manager = PerceptionManager.from_config({
            "news_cue_interval_cycles": 10,
            "weather_cue_interval_cycles": 10,
            "reading_cue_interval_cycles": 10,
        })

        cues = manager.build_prompt_cues(1, [])

        self.assertNotIn("外界动态", " ".join(cues))


class ActionManagerTests(unittest.TestCase):
    def test_note_rewrite_native_action_overwrites_note_and_persists_to_redis(self) -> None:
        queue = StimulusQueue(redis_client=None)
        redis_stub = ListRedisStub()
        first_content = "第一版笔记" * 200
        second_content = "改成第二版"
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("note_rewrite", "native", "覆写我的笔记", 30, "测试", message_text=first_content)),
            redis_client=redis_stub,
        )

        try:
            created_first = manager.submit_from_thoughts([
                _make_thought(
                    action_request={"type": "note_rewrite", "params": f'content:"{first_content}"'}
                )
            ])
            manager._planner = _Planner(
                ActionPlan("note_rewrite", "native", "覆写我的笔记", 30, "测试", message_text=second_content)
            )
            created_second = manager.submit_from_thoughts([
                _make_thought(
                    cycle_id=2,
                    action_request={"type": "note_rewrite", "params": f'content:"{second_content}"'}
                )
            ])
            manager.shutdown()
        finally:
            manager.shutdown()

        expected_first = first_content[:800].rstrip() + "..."
        self.assertEqual(created_first[0].status, "succeeded")
        assert created_first[0].result is not None
        self.assertEqual(created_first[0].result["data"]["content"], expected_first)
        self.assertEqual(created_second[0].status, "succeeded")
        self.assertEqual(manager.current_note(), second_content)
        self.assertEqual(redis_stub.get(NOTE_REDIS_KEY), second_content)
        self.assertEqual(queue.pop_many(limit=5), [])
        prompt_echoes = manager.pop_prompt_echoes()
        self.assertEqual(len(prompt_echoes), 2)
        self.assertTrue(all(stimulus.metadata["action_type"] == "note_rewrite" for stimulus in prompt_echoes))
        self.assertEqual([stimulus.content for stimulus in prompt_echoes], ["我的笔记已覆写", "我的笔记已覆写"])
        self.assertEqual(manager.pop_prompt_echoes(), [])

    def test_attach_redis_keeps_local_note_written_while_disconnected(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("note_rewrite", "native", "覆写我的笔记", 30, "测试", message_text="断线期间的新笔记")),
            redis_client=None,
        )
        redis_stub = ListRedisStub()
        redis_stub.set(NOTE_REDIS_KEY, "旧笔记")

        try:
            created = manager.submit_from_thoughts([
                _make_thought(
                    action_request={"type": "note_rewrite", "params": 'content:"断线期间的新笔记"'}
                )
            ])
            self.assertTrue(manager.attach_redis(redis_stub))
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(manager.current_note(), "断线期间的新笔记")
        self.assertEqual(redis_stub.get(NOTE_REDIS_KEY), "断线期间的新笔记")

    def test_requeue_prompt_echoes_restores_pending_note_echoes(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("note_rewrite", "native", "覆写我的笔记", 30, "测试", message_text="新的笔记")),
            redis_client=None,
        )

        try:
            manager.submit_from_thoughts([
                _make_thought(action_request={"type": "note_rewrite", "params": 'content:"新的笔记"'})
            ])
            prompt_echoes = manager.pop_prompt_echoes()
            self.assertEqual([stimulus.content for stimulus in prompt_echoes], ["我的笔记已覆写"])
            manager.requeue_prompt_echoes(prompt_echoes)
            restored = manager.pop_prompt_echoes()
        finally:
            manager.shutdown()

        self.assertEqual([stimulus.content for stimulus in restored], ["我的笔记已覆写"])

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
        _assert_news_stimuli_contents(
            self,
            queue,
            manager,
            [
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ],
            expected_fragments=[
                "已查看 RSS，没有新的新闻条目",
                "第一条",
            ],
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
        _assert_news_stimuli_contents(
            self,
            queue,
            manager,
            [
                _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
                _make_thought(cycle_id=2, action_request={"type": "news", "params": ""}),
            ],
            expected_fragments=[
                "已查看 RSS，没有新的新闻条目",
                "同一条新闻",
            ],
        )

    def test_news_stimulus_falls_back_to_summary_when_title_missing(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=_constant_news_reader(_action_result(
                summary="新闻已读取",
                data={
                    "items": [{
                        "feed_url": "https://example.com/rss.xml",
                        "guid": "item-summary-only",
                        "link": "https://example.com/summary-only",
                        "title": "",
                        "published_at": "2026-03-27T10:00:00+00:00",
                        "summary": "只有摘要，没有标题",
                    }],
                },
                run_id="run_news_summary_only",
                session_key="seedwake:action:act_C1-1",
            )),
        )

        _submit_and_shutdown(
            manager,
            [_make_thought(cycle_id=1, action_request={"type": "news", "params": ""})],
        )
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "news")
        self.assertIn("- 只有摘要，没有标题", stimulus.content)

    def test_news_stimulus_clips_long_summary_when_title_missing(self) -> None:
        queue = StimulusQueue(redis_client=None)
        long_summary = "长摘要" * 80
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=_constant_news_reader(_action_result(
                summary="新闻已读取",
                data={
                    "items": [{
                        "feed_url": "https://example.com/rss.xml",
                        "guid": "item-long-summary",
                        "link": "https://example.com/summary-only",
                        "title": "",
                        "published_at": "2026-03-27T10:00:00+00:00",
                        "summary": long_summary,
                    }],
                },
                run_id="run_news_long_summary",
                session_key="seedwake:action:act_C1-1",
            )),
        )

        stimulus = _assert_single_news_stimulus(self, queue, manager, [
            _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
        ])
        self.assertIn("- ", stimulus.content)
        self.assertIn("...", stimulus.content)
        self.assertNotIn(long_summary, stimulus.content)

    def test_news_stimulus_clips_long_link_when_title_and_summary_missing(self) -> None:
        queue = StimulusQueue(redis_client=None)
        long_link = "https://example.com/" + ("path/" * 60)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=_constant_news_reader(_action_result(
                summary="新闻已读取",
                data={
                    "items": [{
                        "feed_url": "https://example.com/rss.xml",
                        "guid": "item-long-link",
                        "link": long_link,
                        "title": "",
                        "published_at": "2026-03-27T10:00:00+00:00",
                        "summary": "",
                    }],
                },
                run_id="run_news_long_link",
                session_key="seedwake:action:act_C1-1",
            )),
        )

        stimulus = _assert_single_news_stimulus(self, queue, manager, [
            _make_thought(cycle_id=1, action_request={"type": "news", "params": ""}),
        ])
        self.assertIn("- https://example.com/", stimulus.content)
        self.assertIn("...", stimulus.content)
        self.assertNotIn(long_link, stimulus.content)

    def test_news_stimulus_clips_long_title(self) -> None:
        queue = StimulusQueue(redis_client=None)
        long_title = "超长标题" * 80
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=_constant_news_reader(_action_result(
                summary="新闻已读取",
                data={
                    "items": [{
                        "feed_url": "https://example.com/rss.xml",
                        "guid": "item-long-title",
                        "link": "https://example.com/title",
                        "title": long_title,
                        "published_at": "2026-03-27T10:00:00+00:00",
                        "summary": "短摘要",
                    }],
                },
                run_id="run_news_long_title",
                session_key="seedwake:action:act_C1-1",
            )),
        )

        _submit_and_shutdown(
            manager,
            [_make_thought(cycle_id=1, action_request={"type": "news", "params": ""})],
        )
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "news")
        self.assertIn("- ", stimulus.content)
        self.assertIn("...", stimulus.content)
        self.assertNotIn(long_title, stimulus.content)

    def test_news_stimulus_remaining_count_uses_displayable_entries(self) -> None:
        queue = StimulusQueue(redis_client=None)
        items = []
        for index in range(1, 7):
            items.append({
                "feed_url": "https://example.com/rss.xml",
                "guid": f"item-{index}",
                "link": f"https://example.com/{index}",
                "title": "" if index <= 5 else "第六条",
                "published_at": f"2026-03-27T1{index}:00:00+00:00",
                "summary": f"摘要 {index}",
            })
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=_constant_news_reader(_action_result(
                summary="新闻已读取",
                data={"items": items},
                run_id="run_news_many",
                session_key="seedwake:action:act_C1-1",
            )),
        )

        _submit_and_shutdown(
            manager,
            [_make_thought(cycle_id=1, action_request={"type": "news", "params": ""})],
        )
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "news")
        self.assertIn("- 摘要 1", stimulus.content)
        self.assertIn("- 摘要 5", stimulus.content)
        self.assertNotIn("- 第六条", stimulus.content)
        self.assertIn("（另有 1 条未展示）", stimulus.content)

    def test_news_stimulus_emits_explicit_empty_feedback_when_no_new_items(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("news", "native", "读取 RSS", 30, "测试", ["https://example.com/rss.xml"])),
            redis_client=_RedisNewsSeenStub(),
            news_reader=_constant_news_reader(_action_result(
                summary="新闻已读取",
                data={"items": []},
                run_id="run_news_empty",
                session_key="seedwake:action:act_C1-1",
                transport="native",
            )),
        )

        _submit_and_shutdown(
            manager,
            [_make_thought(cycle_id=1, action_request={"type": "news", "params": ""})],
        )
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "news")
        self.assertEqual(stimulus.metadata["origin"], "action")
        self.assertEqual(stimulus.content, "已查看 RSS，没有新的新闻条目")

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

        stimuli = second_queue.pop_many(limit=5)
        self.assertEqual(len(stimuli), 1)
        self.assertEqual(stimuli[0].type, "news")
        self.assertEqual(stimuli[0].content, "已查看 RSS，没有新的新闻条目")

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

    def test_openclaw_search_action_stimulus_contains_results(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("search", "openclaw", "搜索最近的反馈", 30, "测试"))
        executor = _OpenClawExecutor(_action_result(
            summary="搜索完成",
            data={
                "results": [{
                    "title": "Example Result",
                    "url": "https://example.com/result",
                    "snippet": "这是结果摘要。",
                }],
            },
            run_id="run_search_1",
            session_key="seedwake:action:act_C1-1",
        ))
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

        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.type, "action_result")
        self.assertEqual(stimulus.metadata["origin"], "action")
        self.assertEqual(stimulus.metadata["action_type"], "search")
        self.assertIn("Example Result", stimulus.content)
        self.assertIn("https://example.com/result", stimulus.content)
        self.assertIn("这是结果摘要。", stimulus.content)

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
        result = created[0].result
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("run_id", result)
        self.assertIn("session_key", result)

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
        result_metadata = _as_json_object(stimulus.metadata["result"])
        result_data = _as_json_object(result_metadata["data"])
        self.assertIn("summary", result_data)

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
        stimulus = queue.pop_many(limit=1)[0]
        self.assertEqual(stimulus.metadata["action_type"], "send_message")
        self.assertIn("已成功发送给 telegram:1", stimulus.content)
        self.assertIn("我在。", stimulus.content)

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

    def test_reading_stimulus_simplifies_to_source_and_original_text(self) -> None:
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
        self.assertEqual(
            stimulus.content,
            "来源：Example Article (https://example.com/article)\n原文：The answer lies in the logs.",
        )

    def test_reading_stimulus_truncates_long_excerpt_without_note_or_extra_hint(self) -> None:
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
        self.assertIn("来源：Long Article (https://example.com/long)", stimulus.content)
        self.assertIn("原文：", stimulus.content)
        self.assertNotIn(long_excerpt, stimulus.content)
        self.assertNotIn("TAIL", stimulus.content)
        self.assertNotIn("笔记：", stimulus.content)
        self.assertNotIn("说明：", stimulus.content)
        self.assertNotIn("找到一段很长的材料", stimulus.content)

    def test_reading_stimulus_uses_summary_label_when_excerpt_missing(self) -> None:
        queue = StimulusQueue(redis_client=None)
        planner = _Planner(ActionPlan("reading", "openclaw", "阅读外部材料", 30, "测试"))
        executor = _OpenClawExecutor(_action_result(
            summary="这段材料讨论了意识与自我边界的关系。",
            data={
                "source": {
                    "title": "Mind Notes",
                    "url": "https://example.com/mind",
                },
                "excerpt_original": "",
            },
            run_id="run_reading_3",
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
        self.assertEqual(
            stimulus.content,
            "来源：Mind Notes (https://example.com/mind)\n摘要：这段材料讨论了意识与自我边界的关系。",
        )

    def test_planner_ignore_emits_feedback_stimulus(self) -> None:
        created, stimulus = _submit_planner_feedback(None)
        self.assertEqual(created, [])
        self.assertEqual(stimulus.type, "action_result")
        self.assertEqual(stimulus.source, "planner:C1-1")
        self.assertIn("我刚才想 news", stimulus.content)
        self.assertEqual(stimulus.metadata["status"], "ignored")
        result_metadata = _as_json_object(stimulus.metadata["result"])
        self.assertEqual(result_metadata["error"], "ignored_by_planner")

    def test_planner_ignore_preserves_reason_in_feedback_stimulus(self) -> None:
        created, stimulus = _submit_planner_feedback((None, "参数不足，先不执行"))
        self.assertEqual(created, [])
        self.assertEqual(stimulus.type, "action_result")
        self.assertIn("参数不足，先不执行", stimulus.content)
        self.assertIn("我刚才想 news", stimulus.content)

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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("塔林", plan.task)
        self.assertIn('"location"', plan.task)
        self.assertIn('"condition"', plan.task)

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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "native")
        self.assertEqual(plan.target_source, "telegram:42")
        self.assertEqual(plan.message_text, "我已经收到")

    def test_native_send_message_uses_current_conversation_reply_to_by_default(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("send_message", "native", "发送消息", 30, "测试", message_text="我在。")),
            redis_client=ListRedisStub(),
            auto_execute=["send_message"],
        )

        try:
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
                with patch("core.action.request.urlopen") as mock_urlopen:
                    response = MagicMock()
                    response.read.return_value = b'{"ok": true}'
                    mock_urlopen.return_value.__enter__.return_value = response
                    created = manager.submit_from_thoughts(
                        [_make_thought(action_request={"type": "send_message", "params": 'message:"我在。"'})],
                        stimuli=[_conversation_stimulus(message_id=103)],
                    )
                    manager.shutdown_with_timeout(1.0)
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(created[0].request["reply_to_message_id"], "103")
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["reply_parameters"]["message_id"], 103)

    def test_send_telegram_message_retries_transient_network_error(self) -> None:
        transient_error = error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            with patch("core.action.request.urlopen", side_effect=[transient_error, _urlopen_success_response()]) as mock_urlopen:
                with patch("core.action.time.sleep") as mock_sleep:
                    send_error, delivered_reply_to = _send_telegram_message(
                        "telegram:1",
                        "我在。",
                        timeout_seconds=30,
                        reply_to_message_id="",
                    )

        self.assertIsNone(send_error)
        self.assertEqual(delivered_reply_to, "")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(30.0)
        self.assertEqual(mock_urlopen.call_args_list[0].kwargs["timeout"], 10)
        self.assertEqual(mock_urlopen.call_args_list[1].kwargs["timeout"], 10)

    def test_send_telegram_message_stops_after_max_transient_retries(self) -> None:
        transient_error = error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            with patch("core.action.request.urlopen", side_effect=[transient_error] * 11) as mock_urlopen:
                with patch("core.action.time.sleep") as mock_sleep:
                    send_error, delivered_reply_to = _send_telegram_message(
                        "telegram:1",
                        "我在。",
                        timeout_seconds=30,
                        reply_to_message_id="",
                    )

        self.assertIn("Connection refused", str(send_error))
        self.assertEqual(delivered_reply_to, "")
        self.assertEqual(mock_urlopen.call_count, 11)
        self.assertEqual(mock_sleep.call_count, 10)

    def test_send_telegram_message_does_not_retry_ambiguous_reset_error(self) -> None:
        transient_error = error.URLError(ConnectionResetError(104, "Connection reset by peer"))
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            with patch("core.action.request.urlopen", side_effect=[transient_error]) as mock_urlopen:
                with patch("core.action.time.sleep") as mock_sleep:
                    send_error, delivered_reply_to = _send_telegram_message(
                        "telegram:1",
                        "我在。",
                        timeout_seconds=30,
                        reply_to_message_id="",
                    )

        self.assertIn("Connection reset by peer", str(send_error))
        self.assertEqual(delivered_reply_to, "")
        self.assertEqual(mock_urlopen.call_count, 1)
        mock_sleep.assert_not_called()

    def test_send_telegram_message_retries_then_drops_missing_reply_to(self) -> None:
        transient_error = error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
            with patch(
                "core.action.request.urlopen",
                side_effect=[
                    transient_error,
                    _telegram_http_error(
                        code=400,
                        msg="bad request",
                        description="Bad Request: message to be replied not found",
                    ),
                    _urlopen_success_response(),
                ],
            ) as mock_urlopen:
                with patch("core.action.time.sleep") as mock_sleep:
                    send_error, delivered_reply_to = _send_telegram_message(
                        "telegram:1",
                        "我在。",
                        timeout_seconds=30,
                        reply_to_message_id="103",
                    )

        self.assertIsNone(send_error)
        self.assertEqual(delivered_reply_to, "")
        self.assertEqual(mock_urlopen.call_count, 3)
        mock_sleep.assert_called_once_with(30.0)
        first_body = json.loads(mock_urlopen.call_args_list[0][0][0].data.decode("utf-8"))
        second_body = json.loads(mock_urlopen.call_args_list[1][0][0].data.decode("utf-8"))
        third_body = json.loads(mock_urlopen.call_args_list[2][0][0].data.decode("utf-8"))
        self.assertEqual(first_body["reply_parameters"]["message_id"], 103)
        self.assertEqual(second_body["reply_parameters"]["message_id"], 103)
        self.assertNotIn("reply_parameters", third_body)

    def test_native_send_message_retries_without_reply_to_when_reply_target_missing(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("send_message", "native", "发送消息", 30, "测试", message_text="我在。")),
            redis_client=ListRedisStub(),
            auto_execute=["send_message"],
        )

        try:
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
                with patch("core.action.request.urlopen") as mock_urlopen:
                    response = MagicMock()
                    response.read.return_value = b'{"ok": true}'
                    mock_urlopen.side_effect = [
                        _telegram_http_error(
                            code=400,
                            msg="bad request",
                            description="Bad Request: message to be replied not found",
                        ),
                        MagicMock(__enter__=MagicMock(return_value=response), __exit__=MagicMock(return_value=None)),
                    ]
                    created = manager.submit_from_thoughts(
                        [_make_thought(action_request={"type": "send_message", "params": 'message:"我在。"'})],
                        stimuli=[_conversation_stimulus(message_id=103)],
                    )
                    manager.shutdown_with_timeout(1.0)
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(created[0].status, "succeeded")
        self.assertEqual(mock_urlopen.call_count, 2)
        first_body = json.loads(mock_urlopen.call_args_list[0][0][0].data.decode("utf-8"))
        second_body = json.loads(mock_urlopen.call_args_list[1][0][0].data.decode("utf-8"))
        self.assertEqual(first_body["reply_parameters"]["message_id"], 103)
        self.assertNotIn("reply_parameters", second_body)

    def test_native_send_message_retry_without_reply_to_records_dedup_without_reply(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan("send_message", "native", "发送消息", 30, "测试", message_text="我在。")),
            redis_client=ListRedisStub(),
            auto_execute=["send_message"],
        )

        try:
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
                with patch("core.action.request.urlopen") as mock_urlopen:
                    response = MagicMock()
                    response.read.return_value = b'{"ok": true}'
                    mock_urlopen.side_effect = [
                        _telegram_http_error(
                            code=400,
                            msg="bad request",
                            description="Bad Request: message to be replied not found",
                        ),
                        MagicMock(__enter__=MagicMock(return_value=response), __exit__=MagicMock(return_value=None)),
                    ]
                    first_created = manager.submit_from_thoughts(
                        [_make_thought(action_request={"type": "send_message", "params": 'message:"我在。"'})],
                        stimuli=[_conversation_stimulus(message_id=103)],
                    )
                    wait(manager._snapshot_futures(), timeout=1.0)
                    second_created = manager.submit_from_thoughts(
                        [_make_thought(index=2, action_request={"type": "send_message", "params": 'message:"我在。"'})],
                        stimuli=[_conversation_stimulus()],
                    )
                    manager.shutdown_with_timeout(1.0)
            manager.shutdown()
        finally:
            manager.shutdown()

        self.assertEqual(first_created[0].status, "succeeded")
        self.assertEqual(second_created[0].status, "failed")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(manager._recent_sent_messages[-1], ("telegram:1", "我在。", ""))

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

        plan = _as_action_plan(plan)
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
        self.assertIn("你好", stimulus.content)
        self.assertIn("Telegram 发送失败", stimulus.content)
        self.assertIn("Forbidden: bot was blocked by the user", stimulus.content)

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

    def test_native_send_message_dedup_allows_same_text_with_different_reply_to(self) -> None:
        queue = StimulusQueue(redis_client=None)
        manager = _build_action_manager(
            queue,
            _Planner(ActionPlan(
                "send_message",
                "native",
                "发送消息",
                30,
                "测试",
                target_source="telegram:42",
                message_text="收到",
                reply_to_message_id="101",
            )),
            redis_client=ListRedisStub(),
            auto_execute=["send_message"],
        )

        try:
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}):
                with patch("core.action.request.urlopen") as mock_urlopen:
                    response = MagicMock()
                    response.read.return_value = b'{"ok": true}'
                    mock_urlopen.return_value.__enter__.return_value = response
                    first = manager.submit_from_thoughts([
                        _make_thought(
                            cycle_id=1,
                            index=1,
                            action_request={
                                "type": "send_message",
                                "params": 'chat_id:"42", reply_to:"101", '
                                'message:"收到"',
                            },
                        )
                    ])
                    manager._planner = _Planner(ActionPlan(
                        "send_message",
                        "native",
                        "发送消息",
                        30,
                        "测试",
                        target_source="telegram:42",
                        message_text="收到",
                        reply_to_message_id="102",
                    ))
                    second = manager.submit_from_thoughts([
                        _make_thought(
                            cycle_id=1,
                            index=2,
                            action_request={
                                "type": "send_message",
                                "params": 'chat_id:"42", reply_to:"102", '
                                'message:"收到"',
                            },
                        )
                    ])
                    self.assertTrue(manager.shutdown_with_timeout(1.0))
        finally:
            manager.shutdown()

        self.assertEqual(first[0].status, "succeeded")
        self.assertEqual(second[0].status, "succeeded")
        self.assertEqual(mock_urlopen.call_count, 2)

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

    def test_restore_action_request_payload_preserves_reply_to_message_id(self) -> None:
        payload = _coerce_action_request_payload(
            {
                "task": "向 telegram:42 发送消息：我在。",
                "reason": "测试",
                "raw_action": {"type": "send_message", "params": 'message:"我在。"'},
                "reply_to_message_id": "101",
            },
            "fallback",
        )

        self.assertEqual(payload["reply_to_message_id"], "101")

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

        plan = _as_action_plan(plan)
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

        plan = _as_action_plan(plan)
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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("用户反馈 近一周", plan.task)
        self.assertIn('"results"', plan.task)
        self.assertIn('"snippet"', plan.task)

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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("https://example.com/a", plan.task)
        self.assertNotIn("{action:web_fetch", plan.task)
        self.assertIn('"source"', plan.task)
        self.assertIn('"excerpt_original"', plan.task)
        self.assertIn('"brief_note"', plan.task)

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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertEqual(plan.worker_agent_id, "seedwake-ops")
        self.assertIn("config.yml", plan.task)
        self.assertIn('"path"', plan.task)
        self.assertIn('"change_summary"', plan.task)

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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("无我", plan.task)
        self.assertIn('"source"', plan.task)
        self.assertIn('"excerpt_original"', plan.task)

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

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertIn("雨后泥土气味", plan.task)
        self.assertIn('"source"', plan.task)
        self.assertIn('"excerpt_original"', plan.task)
        self.assertNotIn('"brief_note"', plan.task)

    def test_system_change_fallback_includes_structured_result_contract(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想调整系统服务配置",
            action_request={"type": "system_change", "params": 'instruction:"重启并检查 nginx 服务"'},
        )

        plan = _fallback_plan(
            raw_action_type="system_change",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
        )

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "openclaw")
        self.assertEqual(plan.worker_agent_id, "seedwake-ops")
        self.assertIn('"status"', plan.task)
        self.assertIn('"impact_scope"', plan.task)

    def test_custom_delegate_plan_uses_generic_result_contract(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想让 OpenClaw 帮我整理这轮实验观察",
            action_request={"type": "search", "params": 'query:"实验观察"'},
        )

        plan = _plan_delegate_tool_call(
            arguments={
                "action_type": "custom",
                "task": "整理这轮实验观察，提炼结构化要点。",
            },
            thought=thought,
            timeout_seconds=30,
            reason="测试",
            default_weather_location="",
            news_feed_urls=[],
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
            conversation_source=None,
        )

        plan = _as_action_plan(plan)
        self.assertEqual(plan.action_type, "custom")
        self.assertEqual(plan.executor, "openclaw")
        self.assertEqual(plan.worker_agent_id, "seedwake-worker")
        self.assertIn("整理这轮实验观察", plan.task)
        self.assertIn('"details"', plan.task)
        self.assertIn("不要在 data 下新增 details 之外的同级字段。", plan.task)

    def test_delegate_plan_rejects_unknown_action_type(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content="我想试试一个不支持的委托动作",
            action_request={"type": "search", "params": 'query:"实验观察"'},
        )

        plan = _plan_delegate_tool_call(
            arguments={
                "action_type": "foo",
                "task": "做点什么。",
            },
            thought=thought,
            timeout_seconds=30,
            reason="测试",
            default_weather_location="",
            news_feed_urls=[],
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
            conversation_source=None,
        )

        self.assertEqual(plan, (None, "不支持的 delegated action：foo"))

    def test_delegate_openclaw_tool_restricts_action_type_enum(self) -> None:
        delegate_tool = next(
            tool for tool in _planner_tools()
            if _as_json_object(tool["function"]).get("name") == "delegate_openclaw"
        )
        function_schema = _as_json_object(delegate_tool["function"])
        parameters_schema = _as_json_object(function_schema["parameters"])
        properties_schema = _as_json_object(parameters_schema["properties"])
        action_type_schema = _as_json_object(properties_schema["action_type"])

        self.assertIn("enum", action_type_schema)
        self.assertEqual(
            sorted(cast(list, action_type_schema["enum"])),
            ["custom", "file_modify", "reading", "search", "system_change", "weather", "web_fetch"],
        )

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


class PlannerProviderTests(unittest.TestCase):
    def test_json_planner_prompt_includes_complete_tool_contracts(self) -> None:
        messages = _planner_json_messages(
            _make_thought(action_request={"type": "send_message", "params": 'message:"我在"'}),
            conversation_source="telegram:1",
        )

        system_prompt = messages[0]["content"]
        self.assertIn('{"tool":"<tool_name>","arguments":{...}}', system_prompt)
        self.assertIn("delegate_openclaw", system_prompt)
        self.assertIn("action_type（必填", system_prompt)
        self.assertIn("task（必填", system_prompt)
        self.assertIn("native_send_message", system_prompt)
        self.assertIn("message（可选", system_prompt)
        self.assertIn("target（可选", system_prompt)
        self.assertIn("target_entity（可选", system_prompt)
        self.assertIn("reply_to（可选", system_prompt)
        self.assertIn("native_note_rewrite", system_prompt)
        self.assertIn("content（可选", system_prompt)
        self.assertIn("ignore_action", system_prompt)
        self.assertIn("为什么本轮不执行该动作", system_prompt)

    def test_note_rewrite_fallback_builds_native_overwrite_plan(self) -> None:
        thought = _make_thought(
            thought_type="意图",
            content='我想记下来 {action:note_rewrite, content:"把这句记下"}',
            action_request={"type": "note_rewrite", "params": 'content:"把这句记下"'},
        )

        plan = _fallback_plan(
            raw_action_type="note_rewrite",
            thought=thought,
            default_timeout_seconds=30,
            default_weather_location="",
            news_feed_urls=[],
        )

        plan = _as_action_plan(plan)
        self.assertEqual(plan.executor, "native")
        self.assertEqual(plan.action_type, "note_rewrite")
        self.assertEqual(plan.message_text, "把这句记下")

    def test_json_planner_client_can_build_native_plan(self) -> None:
        planner = ActionPlanner(
            _JsonPlannerClient('{"tool":"native_get_time","arguments":{"reason":"测试"}}'),
            {"name": "openclaw/main", "provider": "openclaw"},
            30,
            "Tallinn",
            [],
            "seedwake-worker",
            "seedwake-ops",
        )

        plan = planner.plan(
            _make_thought(action_request={"type": "time", "params": ""}),
            conversation_source="telegram:1",
        )

        assert isinstance(plan, ActionPlan)
        self.assertEqual(plan.action_type, "get_time")
        self.assertEqual(plan.executor, "native")

    def test_json_planner_client_accepts_stringified_arguments(self) -> None:
        planner = ActionPlanner(
            _JsonPlannerClient(
                '{"tool":"native_send_message","arguments":"{\\"message\\":\\"我在\\",\\"target\\":\\"telegram:1\\"}"}'
            ),
            {"name": "openclaw/main", "provider": "openclaw"},
            30,
            "Tallinn",
            [],
            "seedwake-worker",
            "seedwake-ops",
        )

        plan = planner.plan(
            _make_thought(action_request={"type": "send_message", "params": 'message:"我在"'}),
            conversation_source="telegram:9",
        )

        assert isinstance(plan, ActionPlan)
        self.assertEqual(plan.action_type, "send_message")
        self.assertEqual(plan.target_source, "telegram:1")
        self.assertEqual(plan.message_text, "我在")

    def test_json_planner_client_can_ignore_action_with_reason(self) -> None:
        planner = ActionPlanner(
            _JsonPlannerClient('{"tool":"ignore_action","arguments":{"reason":"暂时不需要"}}'),
            {"name": "openclaw/main", "provider": "openclaw"},
            30,
            "Tallinn",
            [],
            "seedwake-worker",
            "seedwake-ops",
        )

        plan = planner.plan(
            _make_thought(action_request={"type": "news", "params": ""}),
            conversation_source=None,
        )

        self.assertEqual(plan, (None, "暂时不需要"))

    def test_json_planner_logs_decision_timing(self) -> None:
        planner = ActionPlanner(
            _JsonPlannerClient('{"tool":"ignore_action","arguments":{"reason":"暂时不需要"}}'),
            {"name": "openclaw/main", "provider": "openclaw"},
            30,
            "Tallinn",
            [],
            "seedwake-worker",
            "seedwake-ops",
        )

        with self.assertLogs("core.action", level="INFO") as logs:
            planner.plan(
                _make_thought(action_request={"type": "news", "params": ""}),
                conversation_source=None,
            )

        output = "\n".join(logs.output)
        self.assertIn("planner decision finished in", output)
        self.assertIn("mode=json", output)


class OpenClawHttpFallbackTests(unittest.TestCase):
    def test_http_fallback_adds_scopes_header(self) -> None:
        executor = OpenClawGatewayExecutor(
            gateway_url="ws://127.0.0.1:18789",
            gateway_token="gateway-token",
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
            session_key_prefix="seedwake:action",
            http_base_url="http://127.0.0.1:18789",
            use_http_fallback=True,
        )
        requests = []

        class _Action:
            action_id = "act_C1-1"
            type = "search"
            timeout_seconds = 30
            source_content = "查一下资料"
            request = {"task": "查一下资料"}

        def fake_urlopen(req, timeout):
            _ = timeout
            requests.append(req)
            response = MagicMock()
            response.read.return_value = _mock_http_fallback_payload()
            cm = MagicMock()
            cm.__enter__.return_value = response
            return cm

        with patch("core.openclaw_gateway.request.urlopen", side_effect=fake_urlopen):
            # noinspection PyTypeChecker
            result = executor._execute_http(_Action(), RuntimeError("ws down"))

        self.assertTrue(result["ok"])
        self.assertIn(
            ("X-openclaw-scopes", "operator.read, operator.write"),
            requests[0].header_items(),
        )

    def test_execute_logs_failed_duration_when_ws_transport_fails(self) -> None:
        executor = OpenClawGatewayExecutor(
            gateway_url="ws://127.0.0.1:18789",
            gateway_token="gateway-token",
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
            session_key_prefix="seedwake:action",
            use_http_fallback=False,
        )

        class _Action:
            action_id = "act_C1-1"
            type = "reading"

        with patch.object(executor, "_execute_ws", AsyncMock(side_effect=OSError("boom"))):
            with self.assertLogs("core.openclaw_gateway", level="INFO") as logs:
                with self.assertRaises(OpenClawUnavailableError):
                    # noinspection PyTypeChecker
                    executor.execute(_Action())

        output = "\n".join(logs.output)
        self.assertIn("openclaw action act_C1-1 [reading] finished", output)
        self.assertIn("status=failed", output)
        self.assertIn("transport=ws", output)

    def test_execute_logs_failed_when_ws_returns_failure_envelope(self) -> None:
        executor = OpenClawGatewayExecutor(
            gateway_url="ws://127.0.0.1:18789",
            gateway_token="gateway-token",
            worker_agent_id="seedwake-worker",
            ops_worker_agent_id="seedwake-ops",
            session_key_prefix="seedwake:action",
            use_http_fallback=False,
        )

        class _Action:
            action_id = "act_C1-2"
            type = "reading"

        with patch.object(
            executor,
            "_execute_ws",
            AsyncMock(return_value={"ok": False, "summary": "行动超时", "data": {}, "error": "timeout"}),
        ):
            with self.assertLogs("core.openclaw_gateway", level="INFO") as logs:
                # noinspection PyTypeChecker
                result = executor.execute(_Action())

        self.assertFalse(result["ok"])
        output = "\n".join(logs.output)
        self.assertIn("openclaw action act_C1-2 [reading] finished", output)
        self.assertIn("status=failed", output)
        self.assertIn("transport=ws", output)


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
            redis_stub,  # type: ignore[arg-type]
            "act_1",
            approved=True,
            actor="alice",
            note="ok",
        )
        controls = pop_action_controls(redis_stub)  # type: ignore[arg-type]

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

        controls = pop_action_controls(redis_stub)  # type: ignore[arg-type]

        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["action_id"], "act_2")


if __name__ == "__main__":
    unittest.main()
