"""Telegram bot bridge for Seedwake human dialogue."""

import asyncio
import json
import logging
from contextlib import suppress
from typing import Protocol, cast

import redis as redis_lib
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.helpers import (
    format_action_event,
    format_status_event,
    format_thought_event_chunks,
    load_admin_user_ids,
    load_allowed_user_ids,
    load_notification_chat_ids,
)
from core.action import ActionRedisLike, load_action_items, push_action_control
from core.logging_setup import setup_logging
from core.runtime import connect_redis_from_env, load_yaml_config
from core.stimulus import ConversationRedisLike, StimulusQueue
from core.common_types import (
    ActionEventPayload,
    AuthorizedTelegramUser,
    EventEnvelope,
    JsonObject,
    JsonValue,
    StatusEventPayload,
    ThoughtEventPayload,
)
from core.i18n import init as init_i18n, t

EVENT_CHANNEL = "seedwake:events"
REDIS_RECONNECT_DELAY_SECONDS = 2.0
BOT_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    ConnectionError,
    TimeoutError,
    OSError,
    json.JSONDecodeError,
    RuntimeError,
    TypeError,
    ValueError,
)


class _RedisPubSub(Protocol):
    def subscribe(self, *channels: str) -> int: ...

    def get_message(self, timeout: float = 0.0) -> dict[str, JsonValue] | None: ...

    def close(self) -> None: ...


BOT_SEND_EXCEPTIONS = (
    TelegramError,
    RuntimeError,
    OSError,
)
logger = logging.getLogger(__name__)


def main() -> None:
    application = create_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def create_application(
    config: dict | None = None,
    redis_client: redis_lib.Redis | None = None,
) -> Application:
    load_dotenv()
    cfg = config or load_yaml_config("config.yml")
    init_i18n(str(cfg.get("language", "zh")))
    setup_logging(cfg, component="bot")
    token = _read_env("TELEGRAM_BOT_TOKEN").strip()
    if not token:
        raise RuntimeError(t("bot.token_not_configured"))

    allowed_user_ids = load_allowed_user_ids(cfg)
    if not allowed_user_ids:
        raise RuntimeError(t("bot.missing_allowed_ids"))
    admin_user_ids = load_admin_user_ids(cfg)
    notification_chat_ids = load_notification_chat_ids(cfg, admin_user_ids)

    async def post_init(app: Application) -> None:
        await _start_event_forwarder(app)

    async def post_shutdown(app: Application) -> None:
        task = app.bot_data.get("event_forwarder")
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    application = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data.update({
        "config": cfg,
        "redis": redis_client if redis_client is not None else connect_redis_from_env(),
        "allowed_user_ids": set(allowed_user_ids),
        "admin_user_ids": set(admin_user_ids),
        "notification_chat_ids": notification_chat_ids,
    })
    application.add_handler(CommandHandler("start", _handle_start))
    application.add_handler(CommandHandler("status", _handle_status))
    application.add_handler(CommandHandler("actions", _handle_actions))
    application.add_handler(CommandHandler("approve", _handle_approve))
    application.add_handler(CommandHandler("reject", _handle_reject))
    application.add_handler(CallbackQueryHandler(_handle_action_callback, pattern=r"^(approve|reject):"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text_message))
    return application


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _ensure_chat_user(update, context)
    if not user:
        return
    lines = [
        t("bot.welcome_line1"),
        t("bot.welcome_line2"),
    ]
    if _is_admin_user(context.application, user["user_id"]):
        lines.append(t("bot.welcome_admin"))
    await _reply_text(update, "\n".join(lines))


async def _handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin_user(update, context):
        return
    redis_client = _ensure_redis_client(context.application)
    if redis_client is None:
        await _reply_text(update, t("bot.redis_unavailable_status"))
        return
    try:
        actions = _load_actions(redis_client)
    except RuntimeError:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, t("bot.redis_unavailable_status"))
        return
    live_count = sum(1 for action in actions if str(action.get("status")) in {"pending", "running"})
    await _reply_text(
        update,
        "Redis: ok\n"
        + t("bot.live_actions", count=live_count),
    )


async def _handle_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin_user(update, context):
        return
    redis_client = _ensure_redis_client(context.application)
    if redis_client is None:
        await _reply_text(update, t("bot.redis_unavailable_actions"))
        return
    try:
        actions = _load_actions(redis_client)
    except RuntimeError:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, t("bot.redis_unavailable_actions"))
        return
    live = [
        action for action in actions
        if str(action.get("status")) in {"pending", "running"}
    ]
    if not live:
        await _reply_text(update, t("bot.no_actions"))
        return
    lines = []
    for action in sorted(live, key=lambda item: str(item.get("submitted_at") or ""), reverse=True)[:10]:
        lines.append(
            f"{action.get('action_id')} "
            f"[{action.get('type')}/{action.get('executor')}] "
            f"{action.get('status')}"
        )
    await _reply_text(update, "\n".join(lines))


async def _handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin_user(update, context):
        return
    await _handle_control_command(update, context, approved=True)


async def _handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin_user(update, context):
        return
    await _handle_control_command(update, context, approved=False)


async def _handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _ensure_chat_user(update, context)
    if not user:
        return
    redis_client = _ensure_redis_client(context.application)
    if redis_client is None:
        await _reply_text(update, t("bot.redis_unavailable_chat"))
        return
    message = update.effective_message
    if message is None:
        return
    text = str(message.text or "").strip()
    if not text:
        return
    content = _build_conversation_content(text)
    queue = StimulusQueue(_as_conversation_redis(redis_client))
    queue.push(
        "conversation",
        1,
        f"telegram:{user['chat_id']}",
        content,
        metadata=_build_conversation_metadata(message, user),
    )
    if not queue.redis_available:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, t("bot.redis_unavailable_chat"))


async def _handle_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    effective_user = update.effective_user
    if effective_user is None or not _is_admin_user(context.application, effective_user.id):
        await query.answer(t("bot.no_admin"), show_alert=True)
        return
    action, _, action_id = str(query.data or "").partition(":")
    approved = action == "approve"
    redis_client = _ensure_redis_client(context.application)
    pushed = push_action_control(
        _as_action_redis(redis_client),
        action_id,
        approved=approved,
        actor=f"telegram:{effective_user.id}",
    )
    if pushed:
        await query.answer(t("bot.submitted"))
        with suppress(*BOT_SEND_EXCEPTIONS):
            await query.edit_message_reply_markup(reply_markup=None)
        return
    _mark_redis_unavailable(context.application)
    await query.answer(t("bot.submit_failed"), show_alert=True)


async def _forward_events(application: Application) -> None:
    while True:
        redis_client = _ensure_redis_client(application)
        if redis_client is None:
            await asyncio.sleep(REDIS_RECONNECT_DELAY_SECONDS)
            continue

        pubsub = None
        try:
            pubsub = await _open_event_pubsub(redis_client)
            await _forward_event_messages(application, pubsub)
        except asyncio.CancelledError:
            raise
        except BOT_REDIS_EXCEPTIONS:
            _mark_redis_unavailable(application)
            await asyncio.sleep(REDIS_RECONNECT_DELAY_SECONDS)
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("unexpected telegram event forwarder failure: %s", exc)
            await asyncio.sleep(REDIS_RECONNECT_DELAY_SECONDS)
        finally:
            if pubsub is not None:
                with suppress(redis_lib.RedisError, RuntimeError, OSError):
                    await asyncio.to_thread(pubsub.close)


async def _dispatch_event(application: Application, envelope: EventEnvelope) -> None:
    event_type = str(envelope.get("type") or "")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return
    if event_type == "action":
        await _dispatch_action_update(application, payload)
        return
    if event_type == "status":
        await _dispatch_status_update(application, payload)
        return
    if event_type == "thoughts":
        await _dispatch_thought_update(application, payload)


async def _start_event_forwarder(application: Application) -> None:
    application.bot_data["event_forwarder"] = asyncio.create_task(
        _forward_events(application),
        name="seedwake-telegram-events",
    )
    await asyncio.sleep(0)


async def _open_event_pubsub(redis_client: redis_lib.Redis) -> _RedisPubSub:
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    await asyncio.to_thread(pubsub.subscribe, EVENT_CHANNEL)
    return pubsub


async def _forward_event_messages(
    application: Application,
    pubsub: _RedisPubSub,
) -> None:
    while True:
        envelope = await _read_event_envelope(pubsub)
        if envelope is None:
            await asyncio.sleep(0.2)
            continue
        await _dispatch_event(application, envelope)


async def _read_event_envelope(pubsub: _RedisPubSub) -> EventEnvelope | None:
    message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
    if message is None:
        return None
    raw = _decode_pubsub_value(message.get("data"))
    if not raw:
        return None
    return json.loads(raw)


async def _dispatch_action_update(application: Application, payload: JsonObject) -> None:
    action_payload = _coerce_action_payload(payload)
    if action_payload is None:
        return
    await _broadcast_action_event(application, action_payload)


async def _dispatch_status_update(application: Application, payload: JsonObject) -> None:
    status_payload = _coerce_status_payload(payload)
    if status_payload is None:
        return
    text = format_status_event(status_payload)
    if not text:
        return
    await _broadcast_text(application, text)


async def _dispatch_thought_update(application: Application, payload: JsonObject) -> None:
    thought_payload = _coerce_thought_payload(payload)
    if thought_payload is None:
        return
    texts = format_thought_event_chunks(thought_payload)
    if not texts:
        return
    for text in texts:
        await _broadcast_text(application, text)


async def _broadcast_action_event(application: Application, payload: ActionEventPayload) -> None:
    text = format_action_event(payload)
    if not text:
        return
    reply_markup = None
    if bool(payload.get("awaiting_confirmation")):
        action_id = str(payload.get("action_id") or "").strip()
        if action_id:
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(t("bot.approve_button"), callback_data=f"approve:{action_id}"),
                InlineKeyboardButton(t("bot.reject_button"), callback_data=f"reject:{action_id}"),
            ]])
    for chat_id in application.bot_data["notification_chat_ids"]:
        await _safe_send_message(application, chat_id=chat_id, text=text, reply_markup=reply_markup)


async def _broadcast_text(application: Application, text: str) -> None:
    for chat_id in application.bot_data["notification_chat_ids"]:
        await _safe_send_message(application, chat_id=chat_id, text=text)


async def _handle_control_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    approved: bool,
) -> None:
    effective_user = update.effective_user
    if effective_user is None:
        await _reply_text(update, t("bot.sender_unknown"))
        return
    if not context.args:
        usage = "/approve <action_id> [note]" if approved else "/reject <action_id> [note]"
        await _reply_text(update, t("bot.usage", usage=usage))
        return
    action_id = context.args[0].strip()
    note = " ".join(context.args[1:]).strip()
    redis_client = _ensure_redis_client(context.application)
    pushed = push_action_control(
        _as_action_redis(redis_client),
        action_id,
        approved=approved,
        actor=f"telegram:{effective_user.id}",
        note=note,
    )
    if not pushed:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, t("bot.redis_submit_failed"))
        return
    decision_label = t("bot.decision_approve") if approved else t("bot.decision_reject")
    await _reply_text(
        update,
        t("bot.decision_submitted", decision=decision_label, action_id=action_id),
    )


async def _ensure_chat_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> AuthorizedTelegramUser | None:
    user = await _ensure_private_user(update)
    if user is None:
        return None
    if user["user_id"] in context.application.bot_data["allowed_user_ids"]:
        return user
    await _reply_text(update, t("bot.no_permission"))
    return None


async def _ensure_admin_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> AuthorizedTelegramUser | None:
    user = await _ensure_private_user(update)
    if user is None:
        return None
    if user["user_id"] in context.application.bot_data["admin_user_ids"]:
        return user
    await _reply_text(update, t("bot.no_admin_permission"))
    return None


async def _ensure_private_user(update: Update) -> AuthorizedTelegramUser | None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return None
    if chat.type != "private":
        await _reply_text(update, t("bot.private_only"))
        return None
    authorized_user: AuthorizedTelegramUser = {
        "user_id": user.id,
        "chat_id": chat.id,
        "username": user.username or "",
        "full_name": user.full_name,
    }
    return authorized_user


def _is_admin_user(application: Application, user_id: int) -> bool:
    return user_id in application.bot_data["admin_user_ids"]


async def _reply_text(update: Update, text: str) -> None:
    message = update.effective_message
    if message is None:
        query = update.callback_query
        if query is not None:
            await query.answer(text, show_alert=True)
        return
    await message.reply_text(text)


def _ensure_redis_client(application: Application) -> redis_lib.Redis | None:
    redis_client = application.bot_data.get("redis")
    if redis_client is not None:
        return redis_client
    redis_client = connect_redis_from_env()
    application.bot_data["redis"] = redis_client
    return redis_client


def _as_action_redis(redis_client: redis_lib.Redis | None) -> ActionRedisLike | None:
    return cast(ActionRedisLike | None, redis_client)


def _as_conversation_redis(redis_client: redis_lib.Redis | None) -> ConversationRedisLike | None:
    return cast(ConversationRedisLike | None, redis_client)


def _mark_redis_unavailable(application: Application) -> None:
    application.bot_data["redis"] = None


def _build_conversation_content(text: str) -> str:
    return text.strip()


def _build_conversation_metadata(message: Message, user: AuthorizedTelegramUser) -> JsonObject:
    metadata: JsonObject = {
        "telegram_user_id": user["user_id"],
        "telegram_chat_id": user["chat_id"],
        "telegram_username": user["username"],
        "telegram_full_name": user["full_name"],
        "telegram_message_id": message.message_id,
    }
    reply = message.reply_to_message
    if reply is None:
        return metadata
    reply_message_id = reply.message_id
    if isinstance(reply_message_id, int):
        metadata["reply_to_message_id"] = reply_message_id
    reply_preview = _telegram_message_preview(reply)
    if reply_preview:
        metadata["reply_to_preview"] = reply_preview
    reply_user = reply.from_user
    if reply_user is None:
        return metadata
    bot_user_id = _telegram_bot_user_id_from_token(_read_env("TELEGRAM_BOT_TOKEN"))
    reply_user_id = reply_user.id
    if isinstance(reply_user_id, int):
        metadata["reply_to_user_id"] = reply_user_id
        metadata["reply_to_from_self"] = bool(bot_user_id and reply_user_id == bot_user_id)
    reply_username = str(reply_user.username or "").strip()
    if reply_username:
        metadata["reply_to_username"] = reply_username
    reply_full_name = str(reply_user.full_name or "").strip()
    if reply_full_name:
        metadata["reply_to_full_name"] = reply_full_name
    return metadata


def _telegram_message_preview(message: Message, limit: int = 200) -> str:
    text = str(message.text or message.caption or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _telegram_bot_user_id_from_token(token: str) -> int | None:
    raw = str(token or "").strip()
    prefix, _, _ = raw.partition(":")
    if prefix.isdigit():
        return int(prefix)
    return None


def _read_env(name: str) -> str:
    import os

    return os.environ.get(name, "")


def _decode_pubsub_value(value: JsonValue | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


def _load_actions(redis_client: redis_lib.Redis | None) -> list[JsonObject]:
    if redis_client is None:
        return []
    try:
        return load_action_items(_as_action_redis(redis_client))
    except BOT_REDIS_EXCEPTIONS as exc:
        raise RuntimeError("redis unavailable") from exc


async def _safe_send_message(
    application: Application,
    *,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    try:
        await application.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except BOT_SEND_EXCEPTIONS:
        return False
    # noinspection PyBroadException
    except Exception as exc:
        logger.exception("unexpected telegram send failure for chat %s: %s", chat_id, exc)
        return False
    return True


def _coerce_action_payload(payload: JsonObject) -> ActionEventPayload | None:
    action_id = payload.get("action_id")
    action_type = payload.get("type")
    executor = payload.get("executor")
    status = payload.get("status")
    summary = payload.get("summary")
    if not isinstance(action_id, str):
        return None
    if not isinstance(action_type, str):
        return None
    if not isinstance(executor, str):
        return None
    if not isinstance(status, str):
        return None
    if not isinstance(summary, str):
        return None
    run_id = payload.get("run_id")
    session_key = payload.get("session_key")
    awaiting_confirmation = payload.get("awaiting_confirmation")
    action_payload: ActionEventPayload = {
        "action_id": action_id,
        "type": action_type,
        "executor": executor,
        "status": status,
        "summary": summary,
        "run_id": run_id if isinstance(run_id, str) else None,
        "session_key": session_key if isinstance(session_key, str) else None,
        "awaiting_confirmation": bool(awaiting_confirmation),
    }
    source_thought_id = payload.get("source_thought_id")
    if isinstance(source_thought_id, str):
        action_payload["source_thought_id"] = source_thought_id
    return action_payload


def _coerce_status_payload(payload: JsonObject) -> StatusEventPayload | None:
    message = payload.get("message")
    if not isinstance(message, str):
        return None
    status_payload: StatusEventPayload = {"message": message}
    username = payload.get("username")
    if isinstance(username, str):
        status_payload["username"] = username
    return status_payload


def _coerce_thought_payload(payload: JsonObject) -> ThoughtEventPayload | None:
    cycle_id = payload.get("cycle_id")
    lines = payload.get("lines")
    if not isinstance(cycle_id, int):
        return None
    if not isinstance(lines, list):
        return None
    normalized_lines = [str(line) for line in lines if isinstance(line, str)]
    if len(normalized_lines) != len(lines):
        return None
    thought_payload: ThoughtEventPayload = {
        "cycle_id": cycle_id,
        "lines": normalized_lines,
    }
    return thought_payload


if __name__ == "__main__":
    main()
