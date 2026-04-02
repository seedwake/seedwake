"""Prefrontal executive control for Phase 4.

Structural inhibition rules only — no keyword/string matching on note or thought content.
Semantic understanding of notes and goals is left to the model itself via prompt context.
"""

import json

import redis as redis_lib

from core.stimulus import Stimulus
from core.thought_parser import Thought, thought_action_requests
from core.types import HabitPromptEntry, PrefrontalPromptState, RawActionRequest, SleepStateSnapshot

PREFRONTAL_STATE_KEY = "seedwake:prefrontal_state"
PREFRONTAL_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
HEAVY_ACTION_TYPES = {"reading", "search", "web_fetch", "file_modify", "system_change"}


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
        note_text: str,
        active_habits: list[HabitPromptEntry],
        sleep_state: SleepStateSnapshot,
        emotion_summary: str,
    ) -> PrefrontalPromptState:
        goal_stack = build_goal_stack(identity, note_text)
        guidance: list[str] = []
        manifested_habits = [habit for habit in active_habits if habit.get("manifested")]
        if sleep_state["mode"] != "awake":
            guidance.append(f"我现在偏{sleep_state['mode']}，需要把行动收得更谨慎。")
        if active_habits:
            guidance.append(f"我的惯性倾向：{', '.join(habit['pattern'] for habit in active_habits[:2])}。")
        if manifested_habits:
            guidance.append(f"此刻有旧种子正在现行：{', '.join(habit['pattern'] for habit in manifested_habits[:2])}。")
        if emotion_summary:
            guidance.append(f"情绪底色是：{emotion_summary}")
        plan_mode = cycle_id % self._check_interval == 0 or bool(manifested_habits)
        if plan_mode:
            guidance.append("这一轮前额叶检查已开启：先看是否偏题、是否重复、是否该抑制冲动。")
        self._last_state = {
            "goal_stack": goal_stack,
            "guidance": guidance[:3],
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
    ) -> tuple[list[Thought], list[str]]:
        if not self._inhibition_enabled:
            return thoughts, []
        conversation_foreground = any(stimulus.type == "conversation" for stimulus in stimuli)
        recent_action_types_with_params = _recent_action_signatures(recent_thoughts)
        notes: list[str] = []
        reviewed_thoughts: list[Thought] = []
        for thought in thoughts:
            kept_requests: list[RawActionRequest] = []
            for action_request in thought_action_requests(thought):
                inhibition_note = _inhibition_note(
                    action_request,
                    conversation_foreground=conversation_foreground,
                    sleep_mode=str(sleep_state["mode"]),
                    recent_signatures=recent_action_types_with_params,
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
            payload = json.loads(_decode_redis_value(raw))
            if not isinstance(payload, dict):
                return
            goal_stack = payload.get("goal_stack")
            guidance = payload.get("guidance")
            inhibition_notes = payload.get("inhibition_notes")
            plan_mode = bool(payload.get("plan_mode"))
            if not isinstance(goal_stack, list) or not isinstance(guidance, list) or not isinstance(inhibition_notes, list):
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


def build_goal_stack(identity: dict[str, str], note_text: str) -> list[str]:
    goals: list[str] = []
    for line in str(identity.get("core_goals") or "").splitlines():
        goal = line.strip(" -")
        if goal:
            goals.append(goal)
    for line in note_text.splitlines():
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


def _decode_redis_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _copy_state(state: PrefrontalPromptState) -> PrefrontalPromptState:
    return {
        "goal_stack": list(state["goal_stack"]),
        "guidance": list(state["guidance"]),
        "inhibition_notes": list(state["inhibition_notes"]),
        "plan_mode": state["plan_mode"],
    }


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


def _recent_action_signatures(recent_thoughts: list[Thought]) -> list[str]:
    """Build canonical action signatures from recent thoughts for dedup."""
    signatures: list[str] = []
    for thought in recent_thoughts[-9:]:
        for action_request in thought_action_requests(thought):
            sig = _action_signature(action_request)
            if sig:
                signatures.append(sig)
    return signatures


def _action_signature(action_request: RawActionRequest) -> str:
    action_type = str(action_request.get("type") or "").strip()
    if not action_type:
        return ""
    params = str(action_request.get("params") or "").strip()
    canonical = _canonical_params(params)
    return f"{action_type}:{canonical}"


def _canonical_params(params: str) -> str:
    """Parse key:"value" pairs from params, sort by key, return canonical form."""
    import re
    pairs: dict[str, str] = {}
    for match in re.finditer(r'(\w+)\s*:\s*"((?:[^"\\]|\\.)*)"', params):
        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        # Normalize whitespace in values
        value = " ".join(value.split())
        pairs[key] = value
    if not pairs:
        # Fallback: treat entire params as a single normalized value
        return " ".join(params.split())[:80]
    return "|".join(f"{key}={value}" for key, value in sorted(pairs.items()))


def _inhibition_note(
    action_request: RawActionRequest,
    *,
    conversation_foreground: bool,
    sleep_mode: str,
    recent_signatures: list[str],
) -> str | None:
    action_type = str(action_request.get("type") or "").strip()
    if not action_type:
        return None
    # During conversation, suppress background info-gathering actions
    if conversation_foreground and action_type in {"reading", "search", "web_fetch", "news"}:
        return f"当前有人在说话，抑制 {action_type}，优先回应前景对话。"
    # Low energy: suppress heavy actions
    if sleep_mode != "awake" and action_type in HEAVY_ACTION_TYPES:
        return f"当前精力偏低，抑制高负荷行动 {action_type}。"
    # Exact duplicate action (same canonical signature) in last 2 actions
    signature = _action_signature(action_request)
    if signature and signature in recent_signatures[-2:]:
        return f"最近刚发起过相同的 {action_type}（参数一致），先抑制重复冲动。"
    return None
