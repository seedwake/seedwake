"""Shared structured types and utilities."""

import time
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
    submitted_at: NotRequired[str]
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


# noinspection DuplicatedCode
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
    origin: NotRequired[str]
    action_type: NotRequired[str]
    source_thought_id: NotRequired[str]


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


class RecentConversationMessage(TypedDict):
    role: str
    speaker_name: str
    content: str
    timestamp: str


class RecentConversationPrompt(TypedDict):
    source: str
    source_name: str
    source_label: str
    summary: str
    last_timestamp: str
    messages: list[RecentConversationMessage]


class RecentActionEchoRecord(TypedDict):
    cycle_id: int
    stimulus: StimulusRecord


class EmotionSnapshot(TypedDict):
    dimensions: dict[str, float]
    dominant: str
    summary: str
    updated_at: str


class HabitControlSignal(TypedDict):
    type: str
    action_type: NotRequired[str]


class HabitPromptEntry(TypedDict):
    id: int
    pattern: str
    category: str
    strength: float
    activation_score: NotRequired[float]
    manifested: NotRequired[bool]
    signal: NotRequired[HabitControlSignal]


# noinspection DuplicatedCode
class ManasPromptState(TypedDict):
    self_coherence_score: float
    consecutive_disruptions: int
    session_context: str
    warning: str
    identity_notice: str
    reflection_requested: bool


class AttentionPromptEntry(TypedDict):
    thought_id: str
    weight: float
    reason: str
    content: str


class PrefrontalPromptState(TypedDict):
    goal_stack: list[str]
    guidance: list[str]
    inhibition_notes: list[str]
    plan_mode: bool


class ReflectionPromptEntry(TypedDict):
    thought_id: str
    cycle_id: int
    content: str
    created_at: str


class SleepStateSnapshot(TypedDict):
    energy: float
    mode: str
    last_light_sleep_cycle: int
    last_deep_sleep_cycle: int
    last_deep_sleep_at: str
    summary: str


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


def elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def coerce_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [coerce_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): coerce_json_value(item) for key, item in value.items()}
    return str(value)


def coerce_json_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return {str(key): coerce_json_value(item) for key, item in value.items()}


def bigram_similarity(left: str, right: str) -> float:
    """Jaccard similarity of character bigrams between two strings."""
    if len(left) < 2 or len(right) < 2:
        return 0.0
    grams_left = {left[i:i + 2] for i in range(len(left) - 1)}
    grams_right = {right[i:i + 2] for i in range(len(right) - 1)}
    union = len(grams_left | grams_right)
    if union == 0:
        return 0.0
    return len(grams_left & grams_right) / union
