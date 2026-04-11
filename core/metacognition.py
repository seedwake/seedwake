"""Metacognition and reflection generation for Phase 4."""

import json
import logging
import re
from datetime import datetime, timezone

import redis as redis_lib

from core.i18n import t
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.thought_parser import Thought
from core.common_types import (
    EmotionSnapshot, HabitPromptEntry, ManasPromptState,
    PrefrontalPromptState, ReflectionPromptEntry,
)

REFLECTIONS_KEY = "seedwake:reflections"
REFLECTION_STATE_KEY = "seedwake:reflection_state"
MAX_REFLECTIONS = 20
METACOGNITION_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
logger = logging.getLogger(__name__)

# Lazy-initialized pattern (built on first use from i18n label)
_reflection_header_pat: re.Pattern | None = None
_reflection_header_label: str | None = None


def _reflection_header_pattern() -> re.Pattern:
    global _reflection_header_pat, _reflection_header_label
    label = t("metacognition.reflection_header_label")
    if _reflection_header_pat is None or _reflection_header_label != label:
        _reflection_header_label = label
        _reflection_header_pat = re.compile(
            rf"^\[{re.escape(label)}(?:-C\d+-\d+)?]\s*(?P<content>.+)$", re.MULTILINE,
        )
    return _reflection_header_pat


def _reflection_system_prompt() -> str:
    from core.i18n import prompt_block
    return str(prompt_block("REFLECTION_SYSTEM_PROMPT"))


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
        if not isinstance(raw_items, list):
            return []
        records: list[ReflectionPromptEntry] = []
        for raw_item in raw_items:
            decoded = _decode_redis_value(raw_item)
            if decoded is None:
                continue
            try:
                payload = json.loads(decoded)
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
        cycles_since = cycle_id - self._last_reflection_cycle
        dominant = emotion["dominant"]
        dominant_strength = emotion["dimensions"].get(dominant, 0.0)
        reason = ""
        if manas_reflection_requested and _manas_reflection_due(
            cycle_id,
            self._last_reflection_cycle,
            self._reflection_interval,
        ):
            reason = "manas_requested"
        elif cycles_since >= self._reflection_interval:
            reason = f"interval_reached ({cycles_since} >= {self._reflection_interval})"
        elif dominant_strength >= 0.75:
            reason = f"strong_emotion ({dominant}={dominant_strength:.2f})"
        elif degeneration_alert or failure_count >= 2:
            reason = f"degeneration={degeneration_alert} failures={failure_count}"
        elif stimuli_changed and dominant_strength >= 0.65:
            reason = f"stimuli_changed + emotion ({dominant}={dominant_strength:.2f})"
        if reason:
            logger.info(
                "metacognition should_reflect=True (reason=%s, last_reflect=C%d)",
                reason,
                self._last_reflection_cycle,
            )
            return True
        logger.info(
            "metacognition should_reflect=False (cycles_since=%d, %s=%.2f, stimuli=%s, degeneration=%s)",
            cycles_since,
            dominant,
            dominant_strength,
            stimuli_changed,
            degeneration_alert,
        )
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
                    {"role": "system", "content": _reflection_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.2, "max_tokens": 200},
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
            type="reflection",
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
            decoded = _decode_redis_value(raw)
            if decoded is None:
                return
            payload = json.loads(decoded)
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
    none = t("metacognition.none")
    goals_text = "；".join(goals) if goals else none
    habits_text = "；".join(habit["pattern"] for habit in habits[:3]) if habits else none
    guidance_text = "；".join(prefrontal_state["guidance"]) if prefrontal_state["guidance"] else none
    manas_text = none
    if manas_state is not None:
        pieces = []
        if manas_state["warning"]:
            pieces.append(manas_state["warning"])
        if manas_state["session_context"]:
            pieces.append(t("metacognition.transition_context", context=manas_state["session_context"]))
        if manas_state["identity_notice"]:
            pieces.append(manas_state["identity_notice"])
        if pieces:
            manas_text = "；".join(pieces)
    degen_value = t("metacognition.yes") if degeneration_alert else t("metacognition.no")
    return (
        t("metacognition.recent_thoughts_label") + "\n" + (recent_lines or none) + "\n\n"
        + t("metacognition.emotion_label", summary=emotion["summary"]) + "\n"
        + t("metacognition.goals_label", text=goals_text) + "\n"
        + t("metacognition.habits_label", text=habits_text) + "\n"
        + t("metacognition.manas_label", text=manas_text) + "\n"
        + t("metacognition.prefrontal_label", text=guidance_text) + "\n"
        + t("metacognition.failures_label", count=failure_count) + "\n"
        + t("metacognition.degeneration_label", value=degen_value) + "\n"
    )


def _extract_reflection_content(raw_text: str) -> str:
    match = _reflection_header_pattern().search(raw_text)
    if match is not None:
        return " ".join(match.group("content").split())
    compact = " ".join(raw_text.split())
    prefix = t("metacognition.reflection_prefix")
    if compact.startswith(prefix):
        compact = compact.removeprefix(prefix).strip()
    return compact[:240]


def _manas_reflection_due(
    cycle_id: int,
    last_reflection_cycle: int,
    reflection_interval: int,
) -> bool:
    minimum_gap = max(2, reflection_interval // 2)
    return cycle_id - last_reflection_cycle >= minimum_gap


def _decode_redis_value(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return None
