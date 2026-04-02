"""Prefrontal executive control for Phase 4."""

import json

import redis as redis_lib

from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import HabitPromptEntry, PrefrontalPromptState, SleepStateSnapshot

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
        goal_stack = _build_goal_stack(identity, note_text)
        guidance: list[str] = []
        if sleep_state["mode"] != "awake":
            guidance.append(f"我现在偏{sleep_state['mode']}，需要把行动收得更谨慎。")
        if active_habits:
            guidance.append(f"眼下容易被这些倾向带偏：{', '.join(habit['pattern'] for habit in active_habits[:2])}。")
        if emotion_summary:
            guidance.append(f"情绪底色是：{emotion_summary}")
        plan_mode = cycle_id % self._check_interval == 0
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
        note_text: str,
        sleep_state: SleepStateSnapshot,
    ) -> tuple[list[Thought], list[str]]:
        if not self._inhibition_enabled:
            return thoughts, []
        conversation_foreground = any(stimulus.type == "conversation" for stimulus in stimuli)
        recent_action_types = [
            str(thought.action_request.get("type") or "").strip()
            for thought in recent_thoughts[-9:]
            if thought.action_request is not None
        ]
        notes: list[str] = []
        for thought in thoughts:
            action_request = thought.action_request
            if action_request is None:
                continue
            action_type = str(action_request.get("type") or "").strip()
            if not action_type:
                continue
            if conversation_foreground and action_type in {"reading", "search", "web_fetch", "news"}:
                thought.action_request = None
                notes.append(f"当前有人在说话，抑制 {action_type}，优先回应前景对话。")
                continue
            if sleep_state["mode"] != "awake" and action_type in HEAVY_ACTION_TYPES:
                thought.action_request = None
                notes.append(f"当前精力偏低，抑制高负荷行动 {action_type}。")
                continue
            if action_type in recent_action_types[-2:] and action_type in {"reading", "search"}:
                thought.action_request = None
                notes.append(f"最近连续重复 {action_type}，先压住这类冲动。")
                continue
            if "别 reading" in note_text or "不要 reading" in note_text:
                if action_type == "reading":
                    thought.action_request = None
                    notes.append("笔记里明确要求停止 reading，已抑制本轮 reading 冲动。")
        self._last_state["inhibition_notes"] = notes[:3]
        self._sync_to_redis()
        return thoughts, notes

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


def _build_goal_stack(identity: dict[str, str], note_text: str) -> list[str]:
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
