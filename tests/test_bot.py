import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import redis as redis_lib
from telegram.error import TelegramError
from telegram import Update
from telegram.ext import Application, ContextTypes

# noinspection PyProtectedMember
from bot.main import (
    create_application,
    _dispatch_event,
    _ensure_redis_client,
    _handle_action_callback,
    _handle_actions,
    _handle_approve,
    _handle_text_message,
)
from bot.helpers import (
    extract_telegram_chat_id,
    format_action_event,
    format_status_event,
    format_thought_event,
    format_thought_event_chunks,
    load_admin_user_ids,
    load_allowed_user_ids,
    load_notification_chat_ids,
)
from core.action import ACTION_CONTROL_KEY
from core.i18n import init as _init_i18n
from core.stimulus import CONVERSATION_HISTORY_KEY, REDIS_KEY as STIMULUS_REDIS_KEY
from core.common_types import ActionEventPayload, EventEnvelope, ThoughtEventPayload
from test_support import slice_window


# noinspection PyPep8Naming
def setUpModule() -> None:
    _init_i18n("zh")


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.hashes = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        self.lists[key] = slice_window(self.lists.get(key, []), start, end)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())


class FakeTelegramMessage(SimpleNamespace):
    text: str
    reply_text: AsyncMock


class FakeTelegramBot(SimpleNamespace):
    send_message: AsyncMock


class FakeTelegramApplication(SimpleNamespace):
    bot_data: dict
    bot: FakeTelegramBot


class FakeTelegramContext(SimpleNamespace):
    application: FakeTelegramApplication
    args: list[str]


def _as_update(value: Update | SimpleNamespace) -> Update:
    return cast(Update, value)


def _as_application(value: Application | SimpleNamespace) -> Application:
    return cast(Application, value)


def _as_context(value: ContextTypes.DEFAULT_TYPE | SimpleNamespace) -> ContextTypes.DEFAULT_TYPE:
    return cast(ContextTypes.DEFAULT_TYPE, value)


def _reply_text_mock(update: Update) -> AsyncMock:
    message = update.effective_message
    assert isinstance(message, FakeTelegramMessage)
    return message.reply_text


def _send_message_mock(context: ContextTypes.DEFAULT_TYPE) -> AsyncMock:
    application = context.application
    assert isinstance(application, FakeTelegramApplication)
    return application.bot.send_message


def _awaited_text(mock: AsyncMock) -> str:
    await_args = mock.await_args
    assert await_args is not None
    return str(await_args.args[0])


def _make_update(
    *,
    user_id: int = 1,
    chat_id: int = 1,
    chat_type: str = "private",
    text: str = "你好",
    username: str = "alice",
    reply_to_message=None,
) -> Update:
    message = FakeTelegramMessage(
        text=text,
        reply_text=AsyncMock(),
        message_id=1001,
        reply_to_message=reply_to_message,
    )
    return _as_update(SimpleNamespace(
        effective_user=SimpleNamespace(
            id=user_id,
            username=username,
            full_name=username,
        ),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_message=message,
        callback_query=None,
    ))


def _make_context(
    redis_client=None,
    *,
    allowed_user_ids=None,
    admin_user_ids=None,
    notification_chat_ids=None,
    args=None,
) -> ContextTypes.DEFAULT_TYPE:
    allowed = set(allowed_user_ids or {1})
    admins = set(admin_user_ids or set())
    notifications = list(notification_chat_ids or sorted(admins))
    application = FakeTelegramApplication(
        bot_data={
            "redis": redis_client,
            "allowed_user_ids": allowed,
            "admin_user_ids": admins,
            "notification_chat_ids": notifications,
        },
        bot=FakeTelegramBot(send_message=AsyncMock()),
    )
    return _as_context(FakeTelegramContext(application=application, args=args or []))


def _action_envelope() -> EventEnvelope:
    payload: ActionEventPayload = {
        "action_id": "act_1",
        "type": "system_change",
        "executor": "openclaw",
        "status": "pending",
        "summary": "需要管理员确认",
        "run_id": None,
        "session_key": None,
        "awaiting_confirmation": True,
    }
    return {"type": "action", "payload": payload}


def _thought_envelope() -> EventEnvelope:
    payload: ThoughtEventPayload = {
        "cycle_id": 42,
        "lines": [
            "[思考] 我先想一下。",
            "[意图] 我想回一句。",
            "[反应] 刚才那句确实接住了。",
        ],
    }
    return {"type": "thoughts", "payload": payload}


def _store_running_action(redis_client: FakeRedis, action_id: str = "act_1") -> None:
    redis_client.hset(
        "seedwake:actions",
        action_id,
        (
            f'{{"action_id":"{action_id}","type":"search","executor":"openclaw","status":"running",'
            '"submitted_at":"2026-03-27T00:00:00+00:00"}'
        ),
    )


async def _assert_live_actions_output(
    test_case: unittest.TestCase,
    redis_client: FakeRedis,
    *,
    include_malformed: bool = False,
) -> None:
    if include_malformed:
        redis_client.hset("seedwake:actions", "bad", "{bad json")
    _store_running_action(redis_client)
    update = _make_update()
    context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={1})

    await _handle_actions(update, context)

    reply_mock = _reply_text_mock(update)
    reply_mock.assert_awaited_once()
    test_case.assertIn("act_1", _awaited_text(reply_mock))


class TelegramBotHelpersTests(unittest.TestCase):
    def test_create_application_initializes_i18n_from_config(self) -> None:
        config = {
            "language": "en",
            "telegram": {
                "allowed_user_ids": [123],
                "admin_user_ids": [123],
            },
        }
        with patch("bot.main._read_env", return_value="12345:secret"):
            create_application(
                config=config,
                redis_client=cast(redis_lib.Redis, cast(object, FakeRedis())),
            )
        try:
            self.assertEqual(format_status_event({"message": "ok"}), "System status: ok")
        finally:
            _init_i18n("zh")

    def test_load_allowed_user_ids(self) -> None:
        config = {"telegram": {"allowed_user_ids": [123, "456", "bad", 123]}}
        self.assertEqual(load_allowed_user_ids(config), [123, 456])

    def test_load_admin_user_ids(self) -> None:
        config = {"telegram": {"admin_user_ids": [123, "456", "bad", 123]}}
        self.assertEqual(load_admin_user_ids(config), [123, 456])

    def test_load_notification_chat_ids_uses_channel_when_configured(self) -> None:
        config = {"telegram": {"notification_channel_id": "-1001234567890"}}
        self.assertEqual(load_notification_chat_ids(config, [123, 456]), [-1001234567890])

    def test_load_notification_chat_ids_falls_back_to_admins(self) -> None:
        config = {"telegram": {"notification_channel_id": "bad"}}
        self.assertEqual(load_notification_chat_ids(config, [123, 456]), [123, 456])

    def test_extract_telegram_chat_id(self) -> None:
        self.assertEqual(extract_telegram_chat_id("telegram:12345"), 12345)
        self.assertIsNone(extract_telegram_chat_id("user:alice"))

    def test_format_action_event(self) -> None:
        payload: ActionEventPayload = {
            "action_id": "act_1",
            "type": "system_change",
            "executor": "openclaw",
            "status": "pending",
            "summary": "需要管理员确认",
            "run_id": None,
            "session_key": None,
            "awaiting_confirmation": True,
        }
        text = format_action_event(payload)
        self.assertIn("需要确认的行动", text)
        self.assertIn("act_1", text)

    def test_format_action_event_extracts_summary_from_embedded_json_text(self) -> None:
        payload: ActionEventPayload = {
            "action_id": "act_2",
            "type": "reading",
            "executor": "openclaw",
            "status": "succeeded",
            "summary": (
                '{"ok":true,"summary":"选了 Virginia Woolf《The Waves》的开篇，和目标意象很贴近。",'
                '"data":{"source":{"title":"The waves","url":"https://example.com"},"excerpt_original":"x"}}'
            ),
            "run_id": None,
            "session_key": None,
            "awaiting_confirmation": False,
        }

        text = format_action_event(payload)

        self.assertIn("行动更新", text)
        self.assertIn("act_2", text)
        self.assertIn("选了 Virginia Woolf《The Waves》的开篇，和目标意象很贴近。", text)
        self.assertNotIn('{"ok":true', text)

    def test_format_thought_event(self) -> None:
        payload: ThoughtEventPayload = {
            "cycle_id": 42,
            "lines": [
                "[思考] 我先想一下。",
                "[意图] 我想回一句。",
            ],
        }

        text = format_thought_event(payload)

        self.assertIn("── C42 ──", text)
        self.assertIn("[思考] 我先想一下。", text)
        self.assertIn("[意图] 我想回一句。", text)

    def test_format_thought_event_chunks_splits_long_body(self) -> None:
        long_line = "[思考] " + ("很长的念头" * 900)
        payload: ThoughtEventPayload = {
            "cycle_id": 42,
            "lines": [long_line],
        }

        chunks = format_thought_event_chunks(payload)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.startswith("── C42 ──\n") for chunk in chunks))
        self.assertTrue(all(len(chunk) <= 4096 for chunk in chunks))


class TelegramBotAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_text_message_ignores_missing_effective_message(self) -> None:
        redis_client = FakeRedis()
        update = _as_update(SimpleNamespace(
            effective_user=SimpleNamespace(id=1, username="alice", full_name="alice"),
            effective_chat=SimpleNamespace(id=1, type="private"),
            effective_message=None,
            callback_query=None,
        ))
        context = _make_context(redis_client, allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertEqual(redis_client.lists, {})

    async def test_handle_text_message_pushes_stimulus(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1})

        await _handle_text_message(update, context)

        stored = redis_client.lists[STIMULUS_REDIS_KEY][0]
        self.assertIn('"type": "conversation"', stored)
        self.assertIn('"source": "telegram:1"', stored)
        history = redis_client.lists[CONVERSATION_HISTORY_KEY][0]
        self.assertIn('"role": "user"', history)
        self.assertIn('"content": "你好"', history)
        _reply_text_mock(update).assert_not_awaited()

    async def test_handle_text_message_stores_reply_context_metadata(self) -> None:
        redis_client = FakeRedis()
        reply_to = SimpleNamespace(
            message_id=998,
            text="好，我自己找一篇关于有氧锻炼的文章",
            caption="",
            from_user=SimpleNamespace(id=12345, username="seedwake_bot", full_name="Seedwake"),
        )
        update = _make_update(reply_to_message=reply_to, text="谢谢你")
        context = _make_context(redis_client, allowed_user_ids={1})

        with patch("bot.main._read_env", return_value="12345:secret"):
            await _handle_text_message(update, context)

        stored = redis_client.lists[STIMULUS_REDIS_KEY][0]
        self.assertIn('"telegram_message_id": 1001', stored)
        self.assertIn('"reply_to_message_id": 998', stored)
        self.assertIn('"reply_to_preview": "好，我自己找一篇关于有氧锻炼的文章"', stored)
        self.assertIn('"reply_to_from_self": true', stored)
        self.assertIn('"telegram_full_name": "alice"', stored)

    async def test_handle_text_message_rejects_unauthorized_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update(user_id=2)
        context = _make_context(redis_client, allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertNotIn(STIMULUS_REDIS_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无权限", _awaited_text(reply_mock))

    async def test_handle_text_message_rejects_admin_only_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update(user_id=2)
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={2})

        await _handle_text_message(update, context)

        self.assertNotIn(STIMULUS_REDIS_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无权限", _awaited_text(reply_mock))

    async def test_handle_text_message_rejects_group_chat(self) -> None:
        redis_client = FakeRedis()
        update = _make_update(chat_type="group")
        context = _make_context(redis_client, allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertNotIn(STIMULUS_REDIS_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("仅支持私聊", _awaited_text(reply_mock))

    async def test_dispatch_reply_event_is_ignored(self) -> None:
        context = _make_context(FakeRedis(), allowed_user_ids={1})
        self.assertIsNotNone(context.application)

        await _dispatch_event(
            context.application,
            {"type": "reply", "payload": {"source": "telegram:99", "message": "你好", "stimulus_id": None}},
        )

        send_mock = _send_message_mock(context)
        send_mock.assert_not_awaited()

    async def test_dispatch_action_event_broadcasts_confirmation(self) -> None:
        context = _make_context(FakeRedis(), allowed_user_ids={1, 2}, admin_user_ids={2})

        await _dispatch_event(context.application, _action_envelope())

        send_mock = _send_message_mock(context)
        self.assertEqual(send_mock.await_count, 1)
        first_kwargs = send_mock.await_args_list[0].kwargs
        self.assertEqual(first_kwargs["chat_id"], 2)
        self.assertIsNotNone(first_kwargs["reply_markup"])

    async def test_dispatch_action_event_broadcasts_to_notification_channel(self) -> None:
        context = _make_context(
            FakeRedis(),
            allowed_user_ids={1, 2},
            admin_user_ids={2},
            notification_chat_ids=[-1001234567890],
        )

        await _dispatch_event(context.application, _action_envelope())

        send_mock = _send_message_mock(context)
        self.assertEqual(send_mock.await_count, 1)
        first_kwargs = send_mock.await_args_list[0].kwargs
        self.assertEqual(first_kwargs["chat_id"], -1001234567890)
        self.assertIsNotNone(first_kwargs["reply_markup"])

    async def test_dispatch_action_event_continues_after_send_failure(self) -> None:
        context = _make_context(FakeRedis(), allowed_user_ids={1, 2}, admin_user_ids={1, 2})
        context.application.bot.send_message = AsyncMock(side_effect=[TelegramError("boom"), None])

        await _dispatch_event(context.application, _action_envelope())

        self.assertEqual(_send_message_mock(context).await_count, 2)

    async def test_dispatch_thought_event_broadcasts_to_notification_channel(self) -> None:
        context = _make_context(
            FakeRedis(),
            allowed_user_ids={1, 2},
            admin_user_ids={2},
            notification_chat_ids=[-1001234567890],
        )

        await _dispatch_event(context.application, _thought_envelope())

        send_mock = _send_message_mock(context)
        send_mock.assert_awaited_once()
        first_kwargs = send_mock.await_args_list[0].kwargs
        self.assertEqual(first_kwargs["chat_id"], -1001234567890)
        self.assertIn("── C42 ──", str(first_kwargs["text"]))
        self.assertIn("[思考] 我先想一下。", str(first_kwargs["text"]))

    async def test_dispatch_thought_event_splits_long_message(self) -> None:
        context = _make_context(
            FakeRedis(),
            allowed_user_ids={1, 2},
            admin_user_ids={2},
            notification_chat_ids=[-1001234567890],
        )
        long_line = "[思考] " + ("很长的念头" * 900)
        envelope: EventEnvelope = {
            "type": "thoughts",
            "payload": {"cycle_id": 42, "lines": [long_line]},
        }

        await _dispatch_event(context.application, envelope)

        send_mock = _send_message_mock(context)
        self.assertGreater(send_mock.await_count, 1)
        texts = [str(call.kwargs["text"]) for call in send_mock.await_args_list]
        self.assertTrue(all(text.startswith("── C42 ──\n") for text in texts))
        self.assertTrue(all(len(text) <= 4096 for text in texts))

    async def test_handle_approve_pushes_action_control(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={1}, args=["act_1", "ok"])

        await _handle_approve(update, context)

        stored = redis_client.lists[ACTION_CONTROL_KEY][0]
        self.assertIn('"action_id": "act_1"', stored)
        _reply_text_mock(update).assert_awaited_once()

    async def test_handle_action_callback_accepts_admin_click_in_channel(self) -> None:
        redis_client = FakeRedis()
        query = SimpleNamespace(
            data="approve:act_1",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        update = _as_update(SimpleNamespace(
            effective_user=SimpleNamespace(id=1, username="alice", full_name="alice"),
            effective_chat=SimpleNamespace(id=-1001234567890, type="channel"),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=query,
        ))
        context = _make_context(
            redis_client,
            allowed_user_ids={1},
            admin_user_ids={1},
            notification_chat_ids=[-1001234567890],
        )

        await _handle_action_callback(update, context)

        stored = redis_client.lists[ACTION_CONTROL_KEY][0]
        self.assertIn('"action_id": "act_1"', stored)
        query.answer.assert_awaited_once_with("已提交")
        query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)

    async def test_handle_approve_rejects_non_admin_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids=set(), args=["act_1"])

        await _handle_approve(update, context)

        self.assertNotIn(ACTION_CONTROL_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无管理权限", _awaited_text(reply_mock))

    async def test_handle_actions_reads_live_actions(self) -> None:
        redis_client = FakeRedis()
        await _assert_live_actions_output(self, redis_client)

    async def test_handle_actions_skips_malformed_action_record(self) -> None:
        redis_client = FakeRedis()
        await _assert_live_actions_output(self, redis_client, include_malformed=True)

    async def test_handle_actions_marks_redis_unavailable_on_read_error(self) -> None:
        class FailingRedis(FakeRedis):
            def hvals(self, key):
                raise redis_lib.exceptions.ConnectionError("boom")

        update = _make_update()
        context = _make_context(FailingRedis(), allowed_user_ids={1}, admin_user_ids={1})

        await _handle_actions(update, context)

        self.assertIsNone(context.application.bot_data["redis"])
        self.assertIn("Redis 不可用", _awaited_text(_reply_text_mock(update)))

    async def test_handle_actions_rejects_non_admin_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids=set())

        await _handle_actions(update, context)

        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无管理权限", _awaited_text(reply_mock))

    async def test_handle_text_message_reports_redis_write_failure(self) -> None:
        class FailingRedis(FakeRedis):
            def rpush(self, key, value):
                raise redis_lib.exceptions.ConnectionError("boom")

        update = _make_update()
        context = _make_context(FailingRedis(), allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertIsNone(context.application.bot_data["redis"])
        self.assertIn("Redis 不可用", _awaited_text(_reply_text_mock(update)))


class TelegramBotRedisRecoveryTests(unittest.TestCase):
    def test_ensure_redis_client_reconnects_when_missing(self) -> None:
        redis_client = FakeRedis()
        application = _as_application(SimpleNamespace(bot_data={"redis": None}))

        with patch("bot.main.connect_redis_from_env", return_value=redis_client):
            recovered = _ensure_redis_client(application)

        self.assertIs(recovered, redis_client)
        self.assertIs(application.bot_data["redis"], redis_client)
