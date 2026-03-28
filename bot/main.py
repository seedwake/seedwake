"""Telegram bot bridge for Seedwake human dialogue."""

import asyncio
import json
from contextlib import suppress

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.action import ACTION_REDIS_KEY, push_action_control
from core.runtime import connect_redis_from_env, load_yaml_config
from core.stimulus import StimulusQueue
from core.types import (
    ActionEventPayload,
    AuthorizedTelegramUser,
    EventEnvelope,
    JsonObject,
    StatusEventPayload,
)

EVENT_CHANNEL = "seedwake:events"
REDIS_RECONNECT_DELAY_SECONDS = 2.0


def main() -> None:
    application = create_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def create_application(config: dict | None = None, redis_client=None) -> Application:
    load_dotenv()
    cfg = config or load_yaml_config("config.yml")
    token = _read_env("TELEGRAM_BOT_TOKEN").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 未配置")

    allowed_user_ids = _load_allowed_user_ids(cfg)
    if not allowed_user_ids:
        raise RuntimeError("config.yml 缺少 telegram.allowed_user_ids")

    async def post_init(application: Application) -> None:
        task = asyncio.create_task(_forward_events(application), name="seedwake-telegram-events")
        application.bot_data["event_forwarder"] = task

    async def post_shutdown(application: Application) -> None:
        task = application.bot_data.get("event_forwarder")
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
        "allowed_user_ids": allowed_user_ids,
        "notification_user_ids": allowed_user_ids,
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
    if not await _ensure_authorized(update, context):
        return
    await _reply_text(
        update,
        "Seedwake Telegram 通道已连接。\n"
        "直接发送文本即可对话。\n"
        "命令：/status /actions /approve <action_id> /reject <action_id>",
    )


async def _handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update, context):
        return
    redis_client = _ensure_redis_client(context.application)
    if redis_client is None:
        await _reply_text(update, "Redis: unavailable\n进行中行动: 0")
        return
    try:
        actions = _load_actions(redis_client)
    except Exception:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, "Redis: unavailable\n进行中行动: 0")
        return
    live_count = sum(1 for action in actions if str(action.get("status")) in {"pending", "running"})
    await _reply_text(
        update,
        "Redis: ok\n"
        f"进行中行动: {live_count}",
    )


async def _handle_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update, context):
        return
    redis_client = _ensure_redis_client(context.application)
    if redis_client is None:
        await _reply_text(update, "Redis 不可用，无法查询行动状态。")
        return
    try:
        actions = _load_actions(redis_client)
    except Exception:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, "Redis 不可用，无法查询行动状态。")
        return
    live = [
        action for action in actions
        if str(action.get("status")) in {"pending", "running"}
    ]
    if not live:
        await _reply_text(update, "当前没有进行中的行动。")
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
    if not await _ensure_authorized(update, context):
        return
    await _handle_control_command(update, context, approved=True)


async def _handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update, context):
        return
    await _handle_control_command(update, context, approved=False)


async def _handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _ensure_authorized(update, context)
    if not user:
        return
    redis_client = _ensure_redis_client(context.application)
    if redis_client is None:
        await _reply_text(update, "Redis 不可用，当前无法与 Seedwake 对话。")
        return
    message = update.effective_message
    text = str(message.text or "").strip()
    if not text:
        return
    queue = StimulusQueue(redis_client)
    queue.push(
        "conversation",
        1,
        f"telegram:{user['chat_id']}",
        text,
        metadata={
            "telegram_user_id": user["user_id"],
            "telegram_chat_id": user["chat_id"],
            "telegram_username": user["username"],
            "telegram_full_name": user["full_name"],
        },
    )
    if not queue.redis_available:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, "Redis 不可用，当前无法与 Seedwake 对话。")
        return
    await _reply_text(update, "已收到，稍后回复。")


async def _handle_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _ensure_authorized(update, context)
    if not user:
        return
    query = update.callback_query
    if query is None:
        return
    action, _, action_id = str(query.data or "").partition(":")
    approved = action == "approve"
    redis_client = _ensure_redis_client(context.application)
    pushed = push_action_control(
        redis_client,
        action_id,
        approved=approved,
        actor=f"telegram:{user['user_id']}",
    )
    if pushed:
        await query.answer("已提交")
        with suppress(Exception):
            await query.edit_message_reply_markup(reply_markup=None)
        return
    _mark_redis_unavailable(context.application)
    await query.answer("提交失败", show_alert=True)


async def _forward_events(application: Application) -> None:
    while True:
        redis_client = _ensure_redis_client(application)
        if redis_client is None:
            await asyncio.sleep(REDIS_RECONNECT_DELAY_SECONDS)
            continue

        pubsub = None
        try:
            pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            await asyncio.to_thread(pubsub.subscribe, EVENT_CHANNEL)
            while True:
                message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0.2)
                    continue
                raw = _decode_pubsub_value(message.get("data"))
                if not raw:
                    continue
                envelope = json.loads(raw)
                await _dispatch_event(application, envelope)
        except asyncio.CancelledError:
            raise
        except Exception:
            _mark_redis_unavailable(application)
            await asyncio.sleep(REDIS_RECONNECT_DELAY_SECONDS)
        finally:
            if pubsub is not None:
                with suppress(Exception):
                    await asyncio.to_thread(pubsub.close)


async def _dispatch_event(application: Application, envelope: EventEnvelope) -> None:
    event_type = str(envelope.get("type") or "")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return
    if event_type == "reply":
        chat_id = _extract_telegram_chat_id(str(payload.get("source") or ""))
        text = str(payload.get("message") or "").strip()
        if chat_id is None or not text:
            return
        await _safe_send_message(application, chat_id=chat_id, text=text)
        return
    if event_type == "action":
        await _broadcast_action_event(application, payload)
        return
    if event_type == "status":
        text = _format_status_event(payload)
        if not text:
            return
        await _broadcast_text(application, text)


async def _broadcast_action_event(application: Application, payload: ActionEventPayload) -> None:
    text = _format_action_event(payload)
    if not text:
        return
    reply_markup = None
    if bool(payload.get("awaiting_confirmation")):
        action_id = str(payload.get("action_id") or "").strip()
        if action_id:
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("批准", callback_data=f"approve:{action_id}"),
                InlineKeyboardButton("拒绝", callback_data=f"reject:{action_id}"),
            ]])
    for chat_id in application.bot_data["notification_user_ids"]:
        await _safe_send_message(application, chat_id=chat_id, text=text, reply_markup=reply_markup)


async def _broadcast_text(application: Application, text: str) -> None:
    for chat_id in application.bot_data["notification_user_ids"]:
        await _safe_send_message(application, chat_id=chat_id, text=text)


async def _handle_control_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    approved: bool,
) -> None:
    if not context.args:
        usage = "/approve <action_id> [note]" if approved else "/reject <action_id> [note]"
        await _reply_text(update, f"用法：{usage}")
        return
    action_id = context.args[0].strip()
    note = " ".join(context.args[1:]).strip()
    redis_client = _ensure_redis_client(context.application)
    user_id = update.effective_user.id if update.effective_user else 0
    pushed = push_action_control(
        redis_client,
        action_id,
        approved=approved,
        actor=f"telegram:{user_id}",
        note=note,
    )
    if not pushed:
        _mark_redis_unavailable(context.application)
        await _reply_text(update, "提交失败，Redis 不可用。")
        return
    await _reply_text(update, f"{'批准' if approved else '拒绝'}已提交：{action_id}")


async def _ensure_authorized(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> AuthorizedTelegramUser | None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return None
    allowed = context.application.bot_data["allowed_user_ids"]
    if user.id not in allowed:
        await _reply_text(update, "无权限。")
        return None
    return {
        "user_id": user.id,
        "chat_id": chat.id,
        "username": user.username or "",
        "full_name": user.full_name,
    }


async def _reply_text(update: Update, text: str) -> None:
    message = update.effective_message
    if message is None:
        query = update.callback_query
        if query is not None:
            await query.answer(text, show_alert=True)
        return
    await message.reply_text(text)


def _ensure_redis_client(application: Application):
    redis_client = application.bot_data.get("redis")
    if redis_client is not None:
        return redis_client
    redis_client = connect_redis_from_env()
    application.bot_data["redis"] = redis_client
    return redis_client


def _mark_redis_unavailable(application: Application) -> None:
    application.bot_data["redis"] = None


def _read_env(name: str) -> str:
    import os

    return os.environ.get(name, "")


def _load_allowed_user_ids(config: dict) -> list[int]:
    raw_ids = config.get("telegram", {}).get("allowed_user_ids", [])
    allowed = []
    for raw in raw_ids:
        try:
            allowed.append(int(raw))
        except (TypeError, ValueError):
            continue
    return sorted(set(allowed))


def _decode_pubsub_value(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value or "")


def _extract_telegram_chat_id(source: str) -> int | None:
    prefix = "telegram:"
    if not source.startswith(prefix):
        return None
    try:
        return int(source[len(prefix):])
    except ValueError:
        return None


def _load_actions(redis_client) -> list[JsonObject]:
    if redis_client is None:
        return []
    try:
        raw_items = redis_client.hvals(ACTION_REDIS_KEY)
    except AttributeError:
        raw_items = list(redis_client.hgetall(ACTION_REDIS_KEY).values())
    except Exception as exc:
        raise RuntimeError("redis unavailable") from exc
    return [json.loads(item) for item in raw_items]


async def _safe_send_message(
    application: Application,
    *,
    chat_id: int,
    text: str,
    reply_markup=None,
) -> bool:
    try:
        await application.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Exception:
        return False
    return True


def _format_action_event(payload: ActionEventPayload) -> str:
    action_id = str(payload.get("action_id") or "").strip()
    action_type = str(payload.get("type") or "").strip()
    executor = str(payload.get("executor") or "").strip()
    status = str(payload.get("status") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    if not action_id:
        return ""
    prefix = "需要确认的行动" if bool(payload.get("awaiting_confirmation")) else "行动更新"
    return (
        f"{prefix}\n"
        f"{action_id} [{action_type}/{executor}] {status}\n"
        f"{summary}"
    ).strip()


def _format_status_event(payload: StatusEventPayload) -> str:
    message = str(payload.get("message") or "").strip()
    if not message:
        return ""
    return f"系统状态：{message}"


if __name__ == "__main__":
    main()
