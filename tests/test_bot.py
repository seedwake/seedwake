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
    _dispatch_event,
    _ensure_redis_client,
    _handle_actions,
    _handle_approve,
    _handle_text_message,
)
from bot.helpers import (
    extract_telegram_chat_id,
    format_action_event,
    load_admin_user_ids,
    load_allowed_user_ids,
)
from core.action import ACTION_CONTROL_KEY
from core.stimulus import CONVERSATION_HISTORY_KEY, REDIS_KEY as STIMULUS_REDIS_KEY
from core.types import ActionEventPayload, EventEnvelope
from test_support import slice_window


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


def _as_update(value: object) -> Update:
    return cast(Update, value)


def _as_application(value: object) -> Application:
    return cast(Application, value)


def _as_context(value: object) -> ContextTypes.DEFAULT_TYPE:
    return cast(ContextTypes.DEFAULT_TYPE, value)


def _reply_text_mock(update: Update) -> AsyncMock:
    message = update.effective_message
    assert isinstance(message, FakeTelegramMessage)
    return message.reply_text


def _send_message_mock(context: ContextTypes.DEFAULT_TYPE) -> AsyncMock:
    application = context.application
    assert isinstance(application, FakeTelegramApplication)
    return application.bot.send_message


def _make_update(
    *,
    user_id: int = 1,
    chat_id: int = 1,
    chat_type: str = "private",
    text: str = "你好",
    username: str = "alice",
 ) -> Update:
    message = FakeTelegramMessage(
        text=text,
        reply_text=AsyncMock(),
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
    args=None,
) -> ContextTypes.DEFAULT_TYPE:
    allowed = set(allowed_user_ids or {1})
    admins = set(admin_user_ids or set())
    application = FakeTelegramApplication(
        bot_data={
            "redis": redis_client,
            "allowed_user_ids": allowed,
            "admin_user_ids": admins,
            "notification_user_ids": sorted(admins),
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


class TelegramBotHelpersTests(unittest.TestCase):
    def test_load_allowed_user_ids(self) -> None:
        config = {"telegram": {"allowed_user_ids": [123, "456", "bad", 123]}}
        self.assertEqual(load_allowed_user_ids(config), [123, 456])

    def test_load_admin_user_ids(self) -> None:
        config = {"telegram": {"admin_user_ids": [123, "456", "bad", 123]}}
        self.assertEqual(load_admin_user_ids(config), [123, 456])

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


class TelegramBotAsyncTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_handle_text_message_rejects_unauthorized_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update(user_id=2)
        context = _make_context(redis_client, allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertNotIn(STIMULUS_REDIS_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无权限", reply_mock.await_args.args[0])

    async def test_handle_text_message_rejects_admin_only_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update(user_id=2)
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={2})

        await _handle_text_message(update, context)

        self.assertNotIn(STIMULUS_REDIS_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无权限", reply_mock.await_args.args[0])

    async def test_handle_text_message_rejects_group_chat(self) -> None:
        redis_client = FakeRedis()
        update = _make_update(chat_type="group")
        context = _make_context(redis_client, allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertNotIn(STIMULUS_REDIS_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("仅支持私聊", reply_mock.await_args.args[0])

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

    async def test_dispatch_action_event_continues_after_send_failure(self) -> None:
        context = _make_context(FakeRedis(), allowed_user_ids={1, 2}, admin_user_ids={1, 2})
        context.application.bot.send_message = AsyncMock(side_effect=[TelegramError("boom"), None])

        await _dispatch_event(context.application, _action_envelope())

        self.assertEqual(_send_message_mock(context).await_count, 2)

    async def test_handle_approve_pushes_action_control(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={1}, args=["act_1", "ok"])

        await _handle_approve(update, context)

        stored = redis_client.lists[ACTION_CONTROL_KEY][0]
        self.assertIn('"action_id": "act_1"', stored)
        _reply_text_mock(update).assert_awaited_once()

    async def test_handle_approve_rejects_non_admin_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids=set(), args=["act_1"])

        await _handle_approve(update, context)

        self.assertNotIn(ACTION_CONTROL_KEY, redis_client.lists)
        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无管理权限", reply_mock.await_args.args[0])

    async def test_handle_actions_reads_live_actions(self) -> None:
        redis_client = FakeRedis()
        redis_client.hset(
            "seedwake:actions",
            "act_1",
            ('{"action_id":"act_1","type":"search","executor":"openclaw","status":"running",'
             '"submitted_at":"2026-03-27T00:00:00+00:00"}'),
        )
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={1})

        await _handle_actions(update, context)

        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("act_1", reply_mock.await_args.args[0])

    async def test_handle_actions_skips_malformed_action_record(self) -> None:
        redis_client = FakeRedis()
        redis_client.hset("seedwake:actions", "bad", "{bad json")
        redis_client.hset(
            "seedwake:actions",
            "act_1",
            ('{"action_id":"act_1","type":"search","executor":"openclaw","status":"running",'
             '"submitted_at":"2026-03-27T00:00:00+00:00"}'),
        )
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids={1})

        await _handle_actions(update, context)

        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("act_1", reply_mock.await_args.args[0])

    async def test_handle_actions_marks_redis_unavailable_on_read_error(self) -> None:
        class FailingRedis(FakeRedis):
            def hvals(self, key):
                raise redis_lib.exceptions.ConnectionError("boom")

        update = _make_update()
        context = _make_context(FailingRedis(), allowed_user_ids={1}, admin_user_ids={1})

        await _handle_actions(update, context)

        self.assertIsNone(context.application.bot_data["redis"])
        self.assertIn("Redis 不可用", _reply_text_mock(update).await_args.args[0])

    async def test_handle_actions_rejects_non_admin_user(self) -> None:
        redis_client = FakeRedis()
        update = _make_update()
        context = _make_context(redis_client, allowed_user_ids={1}, admin_user_ids=set())

        await _handle_actions(update, context)

        reply_mock = _reply_text_mock(update)
        reply_mock.assert_awaited_once()
        self.assertIn("无管理权限", reply_mock.await_args.args[0])

    async def test_handle_text_message_reports_redis_write_failure(self) -> None:
        class FailingRedis(FakeRedis):
            def rpush(self, key, value):
                raise redis_lib.exceptions.ConnectionError("boom")

        update = _make_update()
        context = _make_context(FailingRedis(), allowed_user_ids={1})

        await _handle_text_message(update, context)

        self.assertIsNone(context.application.bot_data["redis"])
        self.assertIn("Redis 不可用", _reply_text_mock(update).await_args.args[0])


class TelegramBotRedisRecoveryTests(unittest.TestCase):
    def test_ensure_redis_client_reconnects_when_missing(self) -> None:
        redis_client = FakeRedis()
        application = _as_application(SimpleNamespace(bot_data={"redis": None}))

        with patch("bot.main.connect_redis_from_env", return_value=redis_client):
            recovered = _ensure_redis_client(application)

        self.assertIs(recovered, redis_client)
        self.assertIs(application.bot_data["redis"], redis_client)
