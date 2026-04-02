"""Metacognition and reflection generation for Phase 4."""

import json
import re
from datetime import datetime, timezone

import redis as redis_lib

from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.thought_parser import Thought
from core.types import EmotionSnapshot, HabitPromptEntry, ManasPromptState, PrefrontalPromptState, ReflectionPromptEntry

REFLECTIONS_KEY = "seedwake:reflections"
REFLECTION_STATE_KEY = "seedwake:reflection_state"
REFLECTION_HEADER_PATTERN = re.compile(r"^\[反思(?:-C\d+-\d+)?]\s*(?P<content>.+)$", re.MULTILINE)
MAX_REFLECTIONS = 20
METACOGNITION_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
REFLECTION_SYSTEM_PROMPT = (
    "你在做一次元认知反思。"
    "回看最近的念头流、情绪、目标、习气和失败情况，"
    "用（我）做主语，写出一条简洁、具体的中文反思。"
    "输出必须只有一行，格式是：[反思] ...。"
    "不要给建议清单，不要解释规则。"
)


class MetacognitionManager:
    def __init__(
        self,
        redis_client: redis_lib.Redis | None,
        *,
        reflection_interval: int,
    ) -> None:
        self._redis = redis_client
        self._reflection_interval = max(1, reflection_interval)
        self._last_reflection_cycle = 0
        self._restore_state()

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        self._redis = redis_client
        self._sync_state()
        return self._redis is not None

    def recent_reflections(self, limit: int = 3) -> list[ReflectionPromptEntry]:
        redis_client = self._redis
        if redis_client is None:
            return []
        try:
            raw_items = redis_client.lrange(REFLECTIONS_KEY, 0, max(0, limit - 1))
        except METACOGNITION_REDIS_EXCEPTIONS:
            self._redis = None
            return []
        records: list[ReflectionPromptEntry] = []
        for raw_item in raw_items:
            try:
                payload = json.loads(_decode_redis_value(raw_item))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            records.append(
                {
                    "thought_id": str(payload.get("thought_id") or ""),
                    "cycle_id": int(payload.get("cycle_id") or 0),
                    "content": str(payload.get("content") or ""),
                    "created_at": str(payload.get("created_at") or ""),
                }
            )
        return [record for record in records if record["content"]]

    def should_reflect(
        self,
        cycle_id: int,
        emotion: EmotionSnapshot,
        *,
        degeneration_alert: bool,
        failure_count: int,
        stimuli_changed: bool,
        manas_reflection_requested: bool = False,
    ) -> bool:
        if manas_reflection_requested and _manas_reflection_due(
            cycle_id,
            self._last_reflection_cycle,
            self._reflection_interval,
        ):
            return True
        if cycle_id - self._last_reflection_cycle >= self._reflection_interval:
            return True
        dominant_strength = emotion["dimensions"].get(emotion["dominant"], 0.0)
        if dominant_strength >= 0.75:
            return True
        if degeneration_alert or failure_count >= 2:
            return True
        if stimuli_changed and dominant_strength >= 0.65:
            return True
        return False

    def generate_reflection(
        self,
        client: ModelClient,
        model_config: dict,
        *,
        cycle_id: int,
        recent_thoughts: list[Thought],
        emotion: EmotionSnapshot,
        goals: list[str],
        habits: list[HabitPromptEntry],
        prefrontal_state: PrefrontalPromptState,
        failure_count: int,
        degeneration_alert: bool,
        manas_state: ManasPromptState | None = None,
    ) -> Thought | None:
        prompt = _reflection_request(
            recent_thoughts=recent_thoughts,
            emotion=emotion,
            goals=goals,
            habits=habits,
            prefrontal_state=prefrontal_state,
            failure_count=failure_count,
            degeneration_alert=degeneration_alert,
            manas_state=manas_state,
        )
        try:
            response = client.chat(
                model=str(model_config["name"]),
                messages=[
                    {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.2, "max_tokens": 160},
            )
        except MODEL_CLIENT_EXCEPTIONS:
            return None
        message = response.get("message")
        content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
        reflection_text = _extract_reflection_content(content)
        if not reflection_text:
            return None
        thought = Thought(
            thought_id=f"C{cycle_id}-4",
            cycle_id=cycle_id,
            index=4,
            type="反思",
            content=reflection_text,
        )
        self._store_reflection(thought)
        self._last_reflection_cycle = cycle_id
        self._sync_state()
        return thought

    def _store_reflection(self, thought: Thought) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        payload = {
            "thought_id": thought.thought_id,
            "cycle_id": thought.cycle_id,
            "content": thought.content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            redis_client.lpush(REFLECTIONS_KEY, json.dumps(payload, ensure_ascii=False))
            redis_client.ltrim(REFLECTIONS_KEY, 0, MAX_REFLECTIONS - 1)
        except METACOGNITION_REDIS_EXCEPTIONS:
            self._redis = None

    def _restore_state(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            raw = redis_client.get(REFLECTION_STATE_KEY)
            if raw is None:
                return
            payload = json.loads(_decode_redis_value(raw))
            if not isinstance(payload, dict):
                return
            self._last_reflection_cycle = int(payload.get("last_reflection_cycle") or 0)
        except METACOGNITION_REDIS_EXCEPTIONS:
            self._redis = None

    def _sync_state(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            redis_client.set(
                REFLECTION_STATE_KEY,
                json.dumps({"last_reflection_cycle": self._last_reflection_cycle}, ensure_ascii=False),
            )
        except METACOGNITION_REDIS_EXCEPTIONS:
            self._redis = None


def _reflection_request(
    *,
    recent_thoughts: list[Thought],
    emotion: EmotionSnapshot,
    goals: list[str],
    habits: list[HabitPromptEntry],
    prefrontal_state: PrefrontalPromptState,
    failure_count: int,
    degeneration_alert: bool,
    manas_state: ManasPromptState | None,
) -> str:
    recent_lines = "\n".join(
        f"[{thought.type}] {thought.content}"
        for thought in recent_thoughts[-9:]
        if thought.content.strip()
    )
    goals_text = "；".join(goals) if goals else "（无）"
    habits_text = "；".join(habit["pattern"] for habit in habits[:3]) if habits else "（无）"
    guidance_text = "；".join(prefrontal_state["guidance"]) if prefrontal_state["guidance"] else "（无）"
    manas_text = "（无）"
    if manas_state is not None:
        pieces = []
        if manas_state["warning"]:
            pieces.append(manas_state["warning"])
        if manas_state["session_context"]:
            pieces.append(f"过渡语境：{manas_state['session_context']}")
        if manas_state["identity_notice"]:
            pieces.append(manas_state["identity_notice"])
        if pieces:
            manas_text = "；".join(pieces)
    return (
        f"最近的念头：\n{recent_lines or '（无）'}\n\n"
        f"当前情绪：{emotion['summary']}\n"
        f"当前目标：{goals_text}\n"
        f"活跃习气：{habits_text}\n"
        f"自我连续性：{manas_text}\n"
        f"前额叶提醒：{guidance_text}\n"
        f"最近失败次数：{failure_count}\n"
        f"是否检测到退化：{'是' if degeneration_alert else '否'}\n"
    )


def _extract_reflection_content(raw_text: str) -> str:
    match = REFLECTION_HEADER_PATTERN.search(raw_text)
    if match is not None:
        return " ".join(match.group("content").split())
    compact = " ".join(raw_text.split())
    if compact.startswith("反思："):
        compact = compact.removeprefix("反思：").strip()
    return compact[:240]


def _manas_reflection_due(
    cycle_id: int,
    last_reflection_cycle: int,
    reflection_interval: int,
) -> bool:
    minimum_gap = max(2, reflection_interval // 2)
    return cycle_id - last_reflection_cycle >= minimum_gap


def _decode_redis_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
