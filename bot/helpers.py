import json
import re

from core.common_types import ActionEventPayload, StatusEventPayload, ThoughtEventPayload
from core.i18n import t

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
    summary = _event_summary_text(str(payload.get("summary") or ""))
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
    message = str(payload.get("message") or "").strip()
    if not message:
        return ""
    return t("bot.system_status_prefix", message=message)


def format_thought_event(payload: ThoughtEventPayload) -> str:
    cycle_id = payload.get("cycle_id")
    lines = payload.get("lines")
    if not isinstance(cycle_id, int):
        return ""
    if not isinstance(lines, list):
        return ""
    normalized_lines = [str(line).strip() for line in lines if str(line).strip()]
    if not normalized_lines:
        return ""
    return "\n".join([f"── C{cycle_id} ──", *normalized_lines]).strip()


def format_thought_event_chunks(payload: ThoughtEventPayload) -> list[str]:
    cycle_id = payload.get("cycle_id")
    lines = payload.get("lines")
    if not isinstance(cycle_id, int):
        return []
    if not isinstance(lines, list):
        return []
    normalized_lines = [str(line).strip() for line in lines if str(line).strip()]
    if not normalized_lines:
        return []
    header = f"── C{cycle_id} ──"
    available_chars = TELEGRAM_MESSAGE_MAX_CHARS - len(header) - 1
    if available_chars <= 0:
        return []
    body = "\n".join(normalized_lines)
    body_chunks = _split_telegram_body(body, available_chars)
    return [f"{header}\n{chunk}".strip() for chunk in body_chunks if chunk.strip()]


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
