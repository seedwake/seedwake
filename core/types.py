"""Shared structured types."""

from typing import NotRequired, TypedDict


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class RawActionRequest(TypedDict):
    type: str
    params: str


class ActionRequestPayload(TypedDict):
    task: str
    reason: str
    raw_action: RawActionRequest | None
    news_feed_urls: NotRequired[list[str]]
    worker_agent_id: NotRequired[str]
    target_source: NotRequired[str]
    target_entity: NotRequired[str]
    message_text: NotRequired[str]
    reply_to_message_id: NotRequired[str]


class ActionControl(TypedDict):
    action_id: str
    approved: bool
    actor: str
    note: str
    timestamp: str


class NewsItem(TypedDict):
    feed_url: str
    guid: str
    link: str
    title: str
    published_at: str
    summary: str


class NewsDedupedMeta(TypedDict):
    total_items: int
    new_items: int
    dropped_items: int
    invalid_items: int


class ActionResultEnvelope(TypedDict):
    ok: bool
    summary: str
    data: JsonObject
    error: JsonValue
    run_id: str | None
    session_key: str | None
    transport: str
    raw_text: NotRequired[str]


class StimulusMetadata(TypedDict):
    status: str
    executor: str
    result: ActionResultEnvelope


class StimulusRecord(TypedDict):
    stimulus_id: str
    type: str
    priority: int
    source: str
    content: str
    timestamp: str
    action_id: str | None
    metadata: JsonObject


class ConversationEntry(TypedDict):
    entry_id: str
    role: str
    source: str
    content: str
    timestamp: str
    stimulus_id: str | None
    metadata: JsonObject


class PerceptionStimulusPayload(TypedDict):
    type: str
    priority: int
    source: str
    content: str
    metadata: JsonObject


class SystemStatusSnapshot(TypedDict):
    summary: str
    warnings: list[str]
    cpu_count: int
    load_1m: float
    load_5m: float
    load_15m: float
    load_ratio: float
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    disk_used_ratio: float
    memory_total_kb: float | None
    memory_available_kb: float | None
    memory_used_ratio: float | None


class MemorySnapshot(TypedDict):
    total_kb: float
    available_kb: float
    used_ratio: float


class ActionEventPayload(TypedDict):
    action_id: str
    type: str
    executor: str
    status: str
    source_thought_id: NotRequired[str]
    summary: str
    run_id: str | None
    session_key: str | None
    awaiting_confirmation: bool


class ReplyEventPayload(TypedDict):
    source: str
    message: str
    stimulus_id: str | None


class StatusEventPayload(TypedDict):
    message: str
    username: NotRequired[str]


type EventPayload = ActionEventPayload | ReplyEventPayload | StatusEventPayload


class EventEnvelope(TypedDict):
    type: str
    payload: EventPayload


class AuthorizedTelegramUser(TypedDict):
    user_id: int
    chat_id: int
    username: str
    full_name: str


class HealthResponse(TypedDict):
    ok: bool
    redis: bool
    admins: int


class ConversationHistoryResponse(TypedDict):
    ok: bool
    items: list[ConversationEntry]
    count: int
    requested_by: str


class ActionConfirmResponse(TypedDict):
    ok: bool
    action_id: str
    approved: bool


class ThoughtsResponse(TypedDict):
    ok: bool
    items: list[JsonObject]
    count: int
    requested_by: str


class ActionsResponse(TypedDict):
    ok: bool
    items: list[JsonObject]
    count: int
    requested_by: str
