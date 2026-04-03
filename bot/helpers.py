import json
import re

from core.common_types import ActionEventPayload, StatusEventPayload


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
    prefix = "需要确认的行动" if bool(payload.get("awaiting_confirmation")) else "行动更新"
    return (
        f"{prefix}\n"
        f"{action_id} [{action_type}/{executor}] {status}\n"
        f"{summary}"
    ).strip()


def format_status_event(payload: StatusEventPayload) -> str:
    message = str(payload.get("message") or "").strip()
    if not message:
        return ""
    return f"系统状态：{message}"


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
