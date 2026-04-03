"""Prefrontal executive control for Phase 4.

Structural gating only — no keyword/string matching on thought text.
The model still owns semantic interpretation; the prefrontal layer only applies
scored control signals based on action structure, social urgency, repetition,
deferrability, and recent execution trajectory.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
import logging
import re

import redis as redis_lib

from core.stimulus import Stimulus
from core.thought_parser import Thought, thought_action_requests
from core.common_types import (
    ActionRequestPayload,
    DegenerationIntervention,
    HabitPromptEntry,
    PrefrontalPromptState,
    RawActionRequest,
    SleepStateSnapshot,
)

PREFRONTAL_STATE_KEY = "seedwake:prefrontal_state"
PREFRONTAL_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
HEAVY_ACTION_TYPES = {"reading", "search", "web_fetch", "file_modify", "system_change"}
HABIT_IMPULSE_ACTION_TYPES = {"reading", "news", "weather", "web_fetch", "search"}
ACTION_IMPULSE_SIGNAL_TYPE = "action_impulse"
INFO_GATHERING_ACTION_TYPES = {"reading", "news", "weather", "web_fetch", "search"}
SEND_MESSAGE_ACTION_TYPE = "send_message"
INHIBITION_THRESHOLD = 1.0
SEND_MESSAGE_REPEAT_MIN_CHARS = 6
SEND_MESSAGE_SIMILARITY_THRESHOLD = 0.94
RECENT_ACTION_CONTEXT_WINDOW = timedelta(hours=1)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlFactor:
    code: str
    score: float


@dataclass(frozen=True)
class ConversationSignal:
    source: str
    urgency: float


@dataclass(frozen=True)
class RecentActionContext:
    action_type: str
    signature: str
    params: dict[str, str]
    timestamp: datetime


class PrefrontalManager:
    def __init__(
        self,
        redis_client: redis_lib.Redis | None,
        *,
        check_interval: int,
        inhibition_enabled: bool,
    ) -> None:
        self._redis = redis_client
        self._check_interval = max(1, check_interval)
        self._inhibition_enabled = inhibition_enabled
        self._last_state: PrefrontalPromptState = {
            "goal_stack": [],
            "guidance": [],
            "inhibition_notes": [],
            "plan_mode": False,
        }
        self._restore_from_redis()

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        self._redis = redis_client
        self._sync_to_redis()
        return self._redis is not None

    def current_state(
        self,
        cycle_id: int,
        identity: dict[str, str],
        active_habits: list[HabitPromptEntry],
        sleep_state: SleepStateSnapshot,
        degeneration_intervention: DegenerationIntervention | None = None,
    ) -> PrefrontalPromptState:
        goal_stack = build_goal_stack(identity)
        guidance: list[str] = []
        manifested_habits = [habit for habit in active_habits if habit.get("manifested")]
        if sleep_state["mode"] != "awake":
            guidance.append(f"我现在偏{sleep_state['mode']}，需要把行动收得更谨慎。")
        if manifested_habits:
            guidance.append("此刻有旧的惯性正在浮现，留意是否在重复旧模式。")
        if degeneration_intervention is not None:
            guidance.extend(_degeneration_guidance(degeneration_intervention))
        plan_mode = cycle_id % self._check_interval == 0 or bool(manifested_habits) or degeneration_intervention is not None
        if plan_mode:
            guidance.append("这一轮我需要多留意：是否偏题、是否重复、是否该压住冲动。")
        guidance_limit = 6 if degeneration_intervention is not None else 3
        self._last_state = {
            "goal_stack": goal_stack,
            "guidance": guidance[:guidance_limit],
            "inhibition_notes": [],
            "plan_mode": plan_mode,
        }
        self._sync_to_redis()
        return _copy_state(self._last_state)

    def review_thoughts(
        self,
        thoughts: list[Thought],
        recent_thoughts: list[Thought],
        stimuli: list[Stimulus],
        sleep_state: SleepStateSnapshot,
        active_habits: list[HabitPromptEntry],
        recent_send_message_requests: list[ActionRequestPayload] | None = None,
    ) -> tuple[list[Thought], list[str]]:
        if not self._inhibition_enabled:
            return thoughts, []
        conversation_signal = _foreground_conversation_signal(stimuli)
        recent_actions = _recent_action_contexts(
            recent_thoughts,
            recent_send_message_requests or [],
        )
        manifested_impulses = _manifested_impulse_scores(active_habits)
        if conversation_signal:
            logger.info(
                "prefrontal context: conversation_foreground=%s urgency=%.2f, recent_actions=%d, impulses=%s",
                conversation_signal.source,
                conversation_signal.urgency,
                len(recent_actions),
                manifested_impulses or "none",
            )
        notes: list[str] = []
        reviewed_thoughts: list[Thought] = []
        for thought in thoughts:
            thought_requests = thought_action_requests(thought)
            kept_requests: list[RawActionRequest] = []
            for action_request in thought_requests:
                inhibition_note = _inhibition_note(
                    action_request,
                    conversation_signal=conversation_signal,
                    sleep_mode=str(sleep_state["mode"]),
                    recent_actions=recent_actions,
                    manifested_impulses=manifested_impulses,
                    thought_requests=thought_requests,
                )
                if inhibition_note is not None:
                    notes.append(inhibition_note)
                    continue
                kept_requests.append(action_request)
            reviewed_thoughts.append(_replace_thought_action_requests(thought, kept_requests))
        self._last_state["inhibition_notes"] = notes[:3]
        self._sync_to_redis()
        return reviewed_thoughts, notes

    def _restore_from_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            raw = redis_client.get(PREFRONTAL_STATE_KEY)
            if raw is None:
                return
            decoded = _decode_redis_value(raw)
            if decoded is None:
                return
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                return
            goal_stack = payload.get("goal_stack")
            guidance = payload.get("guidance")
            inhibition_notes = payload.get("inhibition_notes")
            plan_mode = bool(payload.get("plan_mode"))
            if (
                not isinstance(goal_stack, list)
                or not isinstance(guidance, list)
                or not isinstance(inhibition_notes, list)
            ):
                return
            self._last_state = {
                "goal_stack": [str(item) for item in goal_stack],
                "guidance": [str(item) for item in guidance],
                "inhibition_notes": [str(item) for item in inhibition_notes],
                "plan_mode": plan_mode,
            }
        except PREFRONTAL_REDIS_EXCEPTIONS:
            self._redis = None

    def _sync_to_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            redis_client.set(PREFRONTAL_STATE_KEY, json.dumps(self._last_state, ensure_ascii=False))
        except PREFRONTAL_REDIS_EXCEPTIONS:
            self._redis = None


def build_goal_stack(identity: dict[str, str]) -> list[str]:
    goals: list[str] = []
    for line in str(identity.get("core_goals") or "").splitlines():
        goal = line.strip(" -")
        if goal:
            goals.append(goal)
    seen: set[str] = set()
    ordered: list[str] = []
    for goal in goals:
        if goal in seen:
            continue
        seen.add(goal)
        ordered.append(goal)
    return ordered[:5]


def _decode_redis_value(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return None


def _copy_state(state: PrefrontalPromptState) -> PrefrontalPromptState:
    return {
        "goal_stack": list(state["goal_stack"]),
        "guidance": list(state["guidance"]),
        "inhibition_notes": list(state["inhibition_notes"]),
        "plan_mode": state["plan_mode"],
    }


def _degeneration_guidance(intervention: DegenerationIntervention) -> list[str]:
    guidance = [
        f"上一轮我已经在打转：{intervention['summary']}",
        f"这轮必须完成的转向：{intervention['required_shift']}",
    ]
    if intervention["must_externalize"]:
        guidance.append("这一轮至少要把一个念头外化成真正动作；note_rewrite、time、system_status 不算。")
    guidance.extend(f"可行方向：{suggestion}" for suggestion in intervention["suggestions"][:2])
    retry_feedback = str(intervention.get("retry_feedback") or "").strip()
    if retry_feedback:
        guidance.append(f"上一稿仍未过关：{retry_feedback}")
    return guidance


def _replace_thought_action_requests(thought: Thought, action_requests: list[RawActionRequest]) -> Thought:
    return Thought(
        thought_id=thought.thought_id,
        cycle_id=thought.cycle_id,
        index=thought.index,
        type=thought.type,
        content=thought.content,
        trigger_ref=thought.trigger_ref,
        action_request=action_requests[0] if action_requests else None,
        additional_action_requests=action_requests[1:],
        attention_weight=thought.attention_weight,
        timestamp=thought.timestamp,
    )


def _recent_action_contexts(
    recent_thoughts: list[Thought],
    recent_send_message_requests: list[ActionRequestPayload],
) -> list[RecentActionContext]:
    """Build recent action contexts from recent thoughts for gating."""
    cutoff = datetime.now(timezone.utc) - RECENT_ACTION_CONTEXT_WINDOW
    request_contexts = _recent_request_action_contexts(recent_send_message_requests)
    thought_contexts = _recent_thought_action_contexts(recent_thoughts, request_contexts, cutoff)
    return sorted([*thought_contexts, *request_contexts], key=lambda context: context.timestamp)[-9:]


def _recent_request_action_contexts(
    recent_send_message_requests: list[ActionRequestPayload],
) -> list[RecentActionContext]:
    request_contexts: list[RecentActionContext] = []
    for request in recent_send_message_requests[-9:]:
        context = _send_message_context_from_request(request)
        if context is not None:
            request_contexts.append(context)
    return request_contexts


def _recent_thought_action_contexts(
    recent_thoughts: list[Thought],
    request_contexts: list[RecentActionContext],
    cutoff: datetime,
) -> list[RecentActionContext]:
    contexts: list[RecentActionContext] = []
    for thought in recent_thoughts[-9:]:
        if thought.timestamp < cutoff:
            continue
        for action_request in thought_action_requests(thought):
            context = _thought_action_context(thought, action_request, request_contexts)
            if context is not None:
                contexts.append(context)
    return contexts


def _thought_action_context(
    thought: Thought,
    action_request: RawActionRequest,
    request_contexts: list[RecentActionContext],
) -> RecentActionContext | None:
    sig = _action_signature(action_request)
    if not sig:
        return None
    action_type = str(action_request.get("type") or "").strip()
    context = RecentActionContext(
        action_type=action_type,
        signature=sig,
        params=_action_params_map(str(action_request.get("params") or "")),
        timestamp=thought.timestamp,
    )
    if action_type == SEND_MESSAGE_ACTION_TYPE and any(
        _same_submitted_send_message(context, request_context)
        for request_context in request_contexts
    ):
        return None
    return context


def _action_signature(action_request: RawActionRequest) -> str:
    action_type = str(action_request.get("type") or "").strip()
    if not action_type:
        return ""
    canonical = _canonical_params(str(action_request.get("params") or ""))
    return f"{action_type}:{canonical}"


def _canonical_params(params: str) -> str:
    """Parse key:"value" pairs from params, sort by key, return canonical form."""
    pairs = _action_params_map(params)
    if not pairs:
        # Fallback: treat entire params as a single normalized value
        return " ".join(params.split())[:80]
    return _canonical_params_from_map(pairs)


def _canonical_params_from_map(pairs: dict[str, str]) -> str:
    return "|".join(f"{key}={value}" for key, value in sorted(pairs.items()))


def _action_params_map(params: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for match in re.finditer(r'(\w+)\s*:\s*"((?:[^"\\]|\\.)*)"', params):
        key = match.group(1).strip().lower()
        value = " ".join(match.group(2).strip().split())
        pairs[key] = value
    return pairs


def _manifested_impulse_scores(active_habits: list[HabitPromptEntry]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for habit in active_habits:
        if not habit.get("manifested"):
            continue
        signal = habit.get("signal")
        if not isinstance(signal, dict):
            continue
        if str(signal.get("type") or "").strip() != ACTION_IMPULSE_SIGNAL_TYPE:
            continue
        action_type = str(signal.get("action_type") or "").strip()
        if action_type not in HABIT_IMPULSE_ACTION_TYPES:
            continue
        score = max(
            float(habit.get("activation_score") or 0.0),
            float(habit.get("strength") or 0.0),
        )
        current = scores.get(action_type, 0.0)
        if score > current:
            scores[action_type] = score
    return scores


def _foreground_conversation_signal(stimuli: list[Stimulus]) -> ConversationSignal | None:
    conversation_stimuli = [stimulus for stimulus in stimuli if stimulus.type == "conversation"]
    if not conversation_stimuli:
        return None
    latest = max(conversation_stimuli, key=lambda stimulus: stimulus.timestamp)
    content = latest.content.strip()
    merged_count = _merged_conversation_count(latest)
    urgency = 0.35
    if merged_count > 1:
        urgency += min(0.25, 0.08 * (merged_count - 1))
    if "?" in content or "？" in content:
        urgency += 0.2
    if content and len(content) <= 24:
        urgency += 0.1
    if str(latest.metadata.get("reply_to_message_id") or "").strip():
        urgency += 0.1
    return ConversationSignal(
        source=str(latest.source or "").strip(),
        urgency=min(1.0, urgency),
    )


def _merged_conversation_count(stimulus: Stimulus) -> int:
    merged_count = stimulus.metadata.get("merged_count")
    if isinstance(merged_count, int) and merged_count > 0:
        return merged_count
    return 1


def _recent_same_action_type_count(recent_actions: list[RecentActionContext], action_type: str) -> int:
    return sum(
        1
        for action in recent_actions[-4:]
        if action.action_type == action_type
    )


def _supports_foreground_reply(
    action_request: RawActionRequest,
    conversation_signal: ConversationSignal,
) -> bool:
    if str(action_request.get("type") or "").strip() != SEND_MESSAGE_ACTION_TYPE:
        return False
    params = _action_params_map(str(action_request.get("params") or ""))
    explicit_target = _explicit_send_message_target_key(params)
    reply_to_message_id = str(params.get("reply_to") or "").strip()
    if reply_to_message_id:
        if explicit_target:
            return explicit_target == conversation_signal.source
        return True
    target_key = _send_message_target_key(params, conversation_signal)
    return target_key == conversation_signal.source


def _send_message_target_key(
    params: dict[str, str],
    conversation_signal: ConversationSignal | None,
) -> str:
    explicit_target = _explicit_send_message_target_key(params)
    if explicit_target:
        return explicit_target
    if conversation_signal is not None:
        return conversation_signal.source
    return ""


def _explicit_send_message_target_key(params: dict[str, str]) -> str:
    target = _normalize_send_message_target(
        str(params.get("target") or params.get("target_source") or "").strip()
    )
    if target:
        return target
    chat_id = _normalize_send_message_target(
        str(params.get("chat_id") or "").strip(),
        from_chat_id=True,
    )
    if chat_id:
        return chat_id
    return _normalize_send_message_target(str(params.get("target_entity") or "").strip())


def _normalize_send_message_target(raw_target: str, *, from_chat_id: bool = False) -> str:
    target = raw_target.strip()
    if not target:
        return ""
    if target.startswith("telegram:"):
        return target
    if from_chat_id or re.fullmatch(r"-?\d+", target):
        return f"telegram:{target}"
    return target


def _normalized_send_message_text(params: dict[str, str]) -> str:
    message = str(params.get("message") or "").strip().lower()
    if not message:
        return ""
    compact = re.sub(r"\s+", "", message)
    normalized = re.sub(r"[\W_]+", "", compact, flags=re.UNICODE)
    if len(normalized) < SEND_MESSAGE_REPEAT_MIN_CHARS:
        return ""
    return normalized


def _send_message_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(a=left, b=right).ratio()


def _send_message_context_from_request(
    request: ActionRequestPayload,
) -> RecentActionContext | None:
    raw_action = request.get("raw_action")
    if not isinstance(raw_action, dict):
        return None
    if str(raw_action.get("type") or "").strip() != SEND_MESSAGE_ACTION_TYPE:
        return None
    params = _action_params_map(str(raw_action.get("params") or ""))
    target_source = _normalize_send_message_target(str(request.get("target_source") or "").strip())
    if target_source:
        params["target_source"] = target_source
    target_entity = _normalize_send_message_target(str(request.get("target_entity") or "").strip())
    if target_entity:
        params["target_entity"] = target_entity
    message_text = " ".join(str(request.get("message_text") or "").strip().split())
    if message_text:
        params["message"] = message_text
    reply_to_message_id = str(request.get("reply_to_message_id") or "").strip()
    if reply_to_message_id:
        params["reply_to"] = reply_to_message_id
    return RecentActionContext(
        action_type=SEND_MESSAGE_ACTION_TYPE,
        signature=f"{SEND_MESSAGE_ACTION_TYPE}:{_canonical_params_from_map(params)}",
        params=params,
        timestamp=_request_submitted_at(request),
    )


def _request_submitted_at(request: ActionRequestPayload) -> datetime:
    raw_timestamp = str(request.get("submitted_at") or "").strip()
    if not raw_timestamp:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _same_submitted_send_message(
    thought_context: RecentActionContext,
    request_context: RecentActionContext,
) -> bool:
    if (
        thought_context.action_type != SEND_MESSAGE_ACTION_TYPE
        or request_context.action_type != SEND_MESSAGE_ACTION_TYPE
    ):
        return False
    if abs((request_context.timestamp - thought_context.timestamp).total_seconds()) > 30:
        return False
    thought_message = " ".join(str(thought_context.params.get("message") or "").split())
    request_message = " ".join(str(request_context.params.get("message") or "").split())
    if thought_message != request_message:
        return False
    thought_target = _explicit_send_message_target_key(thought_context.params)
    request_target = _explicit_send_message_target_key(request_context.params)
    if thought_target and request_target:
        return thought_target == request_target
    thought_reply_to = str(thought_context.params.get("reply_to") or "").strip()
    request_reply_to = str(request_context.params.get("reply_to") or "").strip()
    if thought_reply_to and request_reply_to:
        return thought_reply_to == request_reply_to
    if not thought_target and not thought_reply_to and request_target:
        return True
    return thought_context.signature == request_context.signature


def _recent_similar_send_message_count(
    recent_actions: list[RecentActionContext],
    target_key: str,
    normalized_message: str,
) -> int:
    if not target_key or not normalized_message:
        return 0
    recent_matches = 0
    for action in recent_actions:
        if action.action_type != SEND_MESSAGE_ACTION_TYPE:
            continue
        if _explicit_send_message_target_key(action.params) != target_key:
            continue
        if (
            _send_message_similarity(
                _normalized_send_message_text(action.params),
                normalized_message,
            )
            < SEND_MESSAGE_SIMILARITY_THRESHOLD
        ):
            continue
        recent_matches += 1
    return recent_matches


def _inhibition_note(
    action_request: RawActionRequest,
    *,
    conversation_signal: ConversationSignal | None,
    sleep_mode: str,
    recent_actions: list[RecentActionContext],
    manifested_impulses: dict[str, float],
    thought_requests: list[RawActionRequest],
) -> str | None:
    action_type = str(action_request.get("type") or "").strip()
    if not action_type:
        return None
    factors = _control_factors(
        action_request=action_request,
        conversation_signal=conversation_signal,
        sleep_mode=sleep_mode,
        recent_actions=recent_actions,
        manifested_impulses=manifested_impulses,
        thought_requests=thought_requests,
    )
    total_score = sum(factor.score for factor in factors)
    if factors:
        factor_summary = ", ".join(f"{f.code}={f.score:+.2f}" for f in factors)
        logger.info(
            "prefrontal gate %s: score=%.2f threshold=%.2f %s [%s]",
            action_type,
            total_score,
            INHIBITION_THRESHOLD,
            "INHIBIT" if total_score >= INHIBITION_THRESHOLD else "PASS",
            factor_summary,
        )
    if total_score < INHIBITION_THRESHOLD:
        return None
    return _compose_inhibition_note(action_type, factors)


def _control_factors(
    *,
    action_request: RawActionRequest,
    conversation_signal: ConversationSignal | None,
    sleep_mode: str,
    recent_actions: list[RecentActionContext],
    manifested_impulses: dict[str, float],
    thought_requests: list[RawActionRequest],
) -> list[ControlFactor]:
    action_type = str(action_request.get("type") or "").strip()
    signature = _action_signature(action_request)
    send_message_params = _action_params_map(str(action_request.get("params") or ""))
    repeated_send_message_candidate = bool(_normalized_send_message_text(send_message_params))
    exact_duplicate_factors = _exact_duplicate_factors(
        action_type=action_type,
        signature=signature,
        recent_actions=recent_actions,
        repeated_send_message_candidate=repeated_send_message_candidate,
    )
    if exact_duplicate_factors is not None:
        return exact_duplicate_factors

    factors: list[ControlFactor] = []
    if sleep_mode != "awake" and action_type in HEAVY_ACTION_TYPES:
        factors.append(ControlFactor("low_energy_heavy", 1.1))

    if action_type == SEND_MESSAGE_ACTION_TYPE:
        factors.extend(
            _send_message_factors(
                action_request=action_request,
                conversation_signal=conversation_signal,
                recent_actions=recent_actions,
            )
        )
        return factors

    if action_type in INFO_GATHERING_ACTION_TYPES:
        factors.extend(
            _info_gathering_factors(
                action_type=action_type,
                conversation_signal=conversation_signal,
                recent_actions=recent_actions,
                manifested_impulses=manifested_impulses,
                thought_requests=thought_requests,
                action_request=action_request,
            )
        )
    return factors


def _exact_duplicate_factors(
    *,
    action_type: str,
    signature: str,
    recent_actions: list[RecentActionContext],
    repeated_send_message_candidate: bool,
) -> list[ControlFactor] | None:
    if not signature or signature not in {action.signature for action in recent_actions[-2:]}:
        return None
    if action_type == SEND_MESSAGE_ACTION_TYPE and not repeated_send_message_candidate:
        return None
    return [ControlFactor("exact_duplicate", 1.25)]


def _send_message_factors(
    *,
    action_request: RawActionRequest,
    conversation_signal: ConversationSignal | None,
    recent_actions: list[RecentActionContext],
) -> list[ControlFactor]:
    params = _action_params_map(str(action_request.get("params") or ""))
    target_key = _send_message_target_key(params, conversation_signal)
    repeated_message_count = _recent_similar_send_message_count(
        recent_actions,
        _explicit_send_message_target_key(params) or target_key,
        _normalized_send_message_text(params),
    )
    factors: list[ControlFactor] = []
    if conversation_signal is not None and _supports_foreground_reply(action_request, conversation_signal):
        factors.append(
            ControlFactor(
                "foreground_reply",
                -(0.55 + 0.35 * conversation_signal.urgency),
            )
        )
    elif conversation_signal is not None and target_key and target_key != conversation_signal.source:
        factors.append(
            ControlFactor(
                "off_context_outreach",
                0.15 + 0.2 * conversation_signal.urgency,
            )
        )
    if repeated_message_count >= 1:
        factors.append(ControlFactor("repeated_send_message", 1.95))
    return factors


def _info_gathering_factors(
    *,
    action_request: RawActionRequest,
    action_type: str,
    conversation_signal: ConversationSignal | None,
    recent_actions: list[RecentActionContext],
    manifested_impulses: dict[str, float],
    thought_requests: list[RawActionRequest],
) -> list[ControlFactor]:
    factors: list[ControlFactor] = []
    repeat_count = _recent_same_action_type_count(recent_actions, action_type)
    habit_impulse_score = manifested_impulses.get(action_type, 0.0)

    if conversation_signal is not None:
        factors.append(
            ControlFactor(
                "conversation_defer",
                0.15 + 0.35 * conversation_signal.urgency,
            )
        )
        if _action_supports_current_reply(action_request, thought_requests, conversation_signal):
            factors.append(
                ControlFactor(
                    "supports_current_reply",
                    -(0.2 + 0.25 * conversation_signal.urgency),
                )
            )
    if repeat_count > 0:
        factors.append(
            ControlFactor(
                "recent_same_type_repeat",
                min(0.75, 0.25 * repeat_count),
            )
        )
    if habit_impulse_score > 0.0 and action_type in HABIT_IMPULSE_ACTION_TYPES:
        factors.append(
            ControlFactor(
                "habit_impulse",
                0.2 + 0.35 * min(1.0, habit_impulse_score),
            )
        )
    return factors


def _action_supports_current_reply(
    action_request: RawActionRequest,
    thought_requests: list[RawActionRequest],
    conversation_signal: ConversationSignal,
) -> bool:
    if str(action_request.get("type") or "").strip() not in INFO_GATHERING_ACTION_TYPES:
        return False
    info_requests = [
        request
        for request in thought_requests
        if str(request.get("type") or "").strip() in INFO_GATHERING_ACTION_TYPES
    ]
    if len(info_requests) != 1:
        return False
    if _action_signature(info_requests[0]) != _action_signature(action_request):
        return False
    return any(
        _supports_foreground_reply(request, conversation_signal)
        for request in thought_requests
    )


def _compose_inhibition_note(action_type: str, factors: list[ControlFactor]) -> str:
    positive_codes = {factor.code for factor in factors if factor.score > 0}
    negative_codes = {factor.code for factor in factors if factor.score < 0}
    direct_note = _direct_inhibition_note(action_type, positive_codes, negative_codes)
    if direct_note is not None:
        return direct_note
    info_note = _info_gathering_inhibition_note(action_type, positive_codes, negative_codes)
    if info_note is not None:
        return info_note
    return f"最近 {action_type} 做得有点频繁，这次先不做。"


def _direct_inhibition_note(
    action_type: str,
    positive_codes: set[str],
    negative_codes: set[str],
) -> str | None:
    if "exact_duplicate" in positive_codes:
        return f"刚做过一样的 {action_type}，不必再来一次。"
    if "repeated_send_message" in positive_codes:
        if "foreground_reply" in negative_codes:
            return "这句刚对眼前这个人说过类似的话，这次别重复。"
        return "这句刚对同一处说过类似的话，这次别重复。"
    if "low_energy_heavy" in positive_codes:
        return f"现在有点累了，{action_type} 太耗精力，先不做。"
    if "off_context_outreach" in positive_codes:
        return "眼前的对话还没结束，先别分心去联系别人。"
    return None


def _info_gathering_inhibition_note(
    action_type: str,
    positive_codes: set[str],
    negative_codes: set[str],
) -> str | None:
    if (
        "conversation_defer" in positive_codes
        and "habit_impulse" in positive_codes
        and "recent_same_type_repeat" in positive_codes
    ):
        if "supports_current_reply" in negative_codes:
            return f"{action_type} 虽然是在回应对话，但最近做得太频繁了，这次先停一下。"
        return f"有人在说话，而且最近已经连续做了好几次 {action_type}，这次先放一放。"
    if "habit_impulse" in positive_codes and "recent_same_type_repeat" in positive_codes:
        return f"最近已经连续做了好几次 {action_type}，这次先缓一缓。"
    if "conversation_defer" in positive_codes and "recent_same_type_repeat" in positive_codes:
        return f"有人在说话，{action_type} 也已经连续做了好几次，先回应眼前的人。"
    if "conversation_defer" in positive_codes and "habit_impulse" in positive_codes:
        return f"有人在说话，先放下 {action_type}，回应眼前的人。"
    return None
