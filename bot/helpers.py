import json
import re

from core.common_types import (
    ActionEventPayload,
    I18nTextPayload,
    JsonObject,
    SerializedThought,
    StatusEventPayload,
    ThoughtEventPayload,
)
from core.i18n import localized_thought_type, t

TELEGRAM_MESSAGE_MAX_CHARS = 4096


def load_allowed_user_ids(config: dict) -> list[int]:
    return _load_numeric_user_ids(config, "allowed_user_ids")


def load_admin_user_ids(config: dict) -> list[int]:
    return _load_numeric_user_ids(config, "admin_user_ids")


def load_notification_chat_ids(config: dict, default_chat_ids: list[int]) -> list[int]:
    raw_chat_id = config.get("telegram", {}).get("notification_channel_id")
    if raw_chat_id is None or raw_chat_id == "":
        return list(default_chat_ids)
    try:
        return [int(str(raw_chat_id))]
    except (TypeError, ValueError):
        return list(default_chat_ids)


def extract_telegram_chat_id(source: str) -> int | None:
    prefix = "telegram:"
    if not source.startswith(prefix):
        return None
    try:
        return int(source[len(prefix):])
    except ValueError:
        return None


def format_action_event(payload: ActionEventPayload) -> str:
    action_id = str(payload.get("action_id") or "").strip()
    action_type = str(payload.get("type") or "").strip()
    executor = str(payload.get("executor") or "").strip()
    status = str(payload.get("status") or "").strip()
    summary = _format_i18n_text(payload.get("summary"))
    if not action_id:
        return ""
    prefix = (
        t("bot.action_confirm_prefix")
        if bool(payload.get("awaiting_confirmation"))
        else t("bot.action_update_prefix")
    )
    return (
        f"{prefix}\n"
        f"{action_id} [{action_type}/{executor}] {status}\n"
        f"{summary}"
    ).strip()


def format_status_event(payload: StatusEventPayload) -> str:
    message = _format_i18n_text(payload.get("message"))
    if not message:
        return ""
    return t("bot.system_status_prefix", message=message)


def format_thought_event(payload: ThoughtEventPayload) -> str:
    cycle_id = _thought_event_cycle_id(payload)
    if cycle_id is None:
        return ""
    normalized_lines = _thought_event_lines(payload)
    if not normalized_lines:
        return ""
    return "\n".join([f"── C{cycle_id} ──", *normalized_lines]).strip()


def format_thought_event_chunks(payload: ThoughtEventPayload) -> list[str]:
    cycle_id = _thought_event_cycle_id(payload)
    if cycle_id is None:
        return []
    normalized_lines = _thought_event_lines(payload)
    if not normalized_lines:
        return []
    header = f"── C{cycle_id} ──"
    available_chars = TELEGRAM_MESSAGE_MAX_CHARS - len(header) - 1
    if available_chars <= 0:
        return []
    body = "\n".join(normalized_lines)
    body_chunks = _split_telegram_body(body, available_chars)
    return [f"{header}\n{chunk}".strip() for chunk in body_chunks if chunk.strip()]


def _thought_event_cycle_id(payload: ThoughtEventPayload) -> int | None:
    if not payload:
        return None
    cycle_id = payload[0].get("cycle_id")
    return cycle_id if isinstance(cycle_id, int) else None


def _thought_event_lines(payload: ThoughtEventPayload) -> list[str]:
    return [
        line
        for line in (_thought_event_line(thought) for thought in payload)
        if line
    ]


def _thought_event_line(thought: SerializedThought) -> str:
    content = str(thought.get("content") or "").strip()
    if not content:
        return ""
    display_type = localized_thought_type(str(thought.get("type") or ""))
    trigger_ref = str(thought.get("trigger_ref") or "").strip()
    suffix = f" (← {trigger_ref})" if trigger_ref else ""
    return f"[{display_type}] {content}{suffix}"


def _load_numeric_user_ids(config: dict, key: str) -> list[int]:
    raw_ids = config.get("telegram", {}).get(key, [])
    user_ids = []
    for raw in raw_ids:
        try:
            user_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return sorted(set(user_ids))


def _event_summary_text(summary: str) -> str:
    normalized = summary.strip()
    if not normalized.startswith("{"):
        return normalized
    extracted = _extract_embedded_summary(normalized)
    return extracted or normalized


def _extract_embedded_summary(summary: str) -> str | None:
    try:
        payload = json.loads(summary)
    except json.JSONDecodeError:
        match = re.search(r'"summary"\s*:\s*"((?:\\.|[^"\\])*)"', summary, re.DOTALL)
        if not match:
            return None
        raw_value = match.group(1)
        try:
            normalized = json.loads(f'"{raw_value}"')
        except json.JSONDecodeError:
            return raw_value.strip() or None
        return str(normalized).strip() or None
    if not isinstance(payload, dict):
        return None
    extracted = str(payload.get("summary") or "").strip()
    return extracted or None


def _format_i18n_text(payload: I18nTextPayload | object) -> str:
    if not isinstance(payload, dict):
        return ""
    key = str(payload.get("key") or "").strip()
    if not key:
        return ""
    params = _i18n_text_params(payload.get("params"))
    try:
        return t(key, **params)
    except KeyError:
        return key


def _i18n_text_params(value: object) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    params: JsonObject = {
        str(key): item
        for key, item in value.items()
    }
    summary = params.get("summary")
    if isinstance(summary, str):
        params["summary"] = _event_summary_text(summary)
    return params


def _split_telegram_body(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_chars + 1)
        if split_at <= 0:
            split_at = max_chars
        chunk = remaining[:split_at].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].lstrip("\n")
    return chunks
