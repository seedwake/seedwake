"""Emotion baseline manager for Phase 4."""

import json
from datetime import datetime, timezone

import redis as redis_lib

from core.action import ActionRecord
from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import EmotionSnapshot

EMOTION_STATE_KEY = "seedwake:emotion_state"
DEFAULT_EMOTION_DIMENSIONS = ["curiosity", "calm", "frustration", "satisfaction", "concern"]
EMOTION_LABELS = {
    "curiosity": "好奇",
    "calm": "平静",
    "frustration": "挫败",
    "satisfaction": "满足",
    "concern": "牵挂",
}
EMOTION_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)


class EmotionManager:
    def __init__(
        self,
        redis_client: redis_lib.Redis | None,
        *,
        inertia: float,
        dimensions: list[str],
    ) -> None:
        self._redis = redis_client
        self._inertia = max(0.0, min(1.0, inertia))
        self._dimensions = dimensions or DEFAULT_EMOTION_DIMENSIONS
        self._shadow = self._default_snapshot()
        self._restore_from_redis()

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        self._redis = redis_client
        self._sync_to_redis()
        return self._redis is not None

    def current(self) -> EmotionSnapshot:
        return _copy_snapshot(self._shadow)

    def apply_cycle(
        self,
        cycle_id: int,
        thoughts: list[Thought],
        stimuli: list[Stimulus],
        running_actions: list[ActionRecord],
        *,
        inhibited_actions: int = 0,
        degeneration_alert: bool = False,
    ) -> EmotionSnapshot:
        inferred = {dimension: 0.0 for dimension in self._dimensions}
        self._infer_from_stimuli(inferred, stimuli)
        self._infer_from_running_actions(inferred, running_actions)
        self._infer_from_thoughts(inferred, thoughts)
        if inhibited_actions > 0:
            inferred["frustration"] = inferred.get("frustration", 0.0) + 0.20
        if degeneration_alert:
            inferred["frustration"] = inferred.get("frustration", 0.0) + 0.25
            inferred["concern"] = inferred.get("concern", 0.0) + 0.15

        previous = self._shadow["dimensions"]
        next_dimensions = {
            dimension: _clamp_emotion(
                previous.get(dimension, 0.0) * self._inertia
                + inferred.get(dimension, 0.0) * (1.0 - self._inertia)
            )
            for dimension in self._dimensions
        }
        dominant = max(next_dimensions.items(), key=lambda item: item[1])[0]
        self._shadow = {
            "dimensions": next_dimensions,
            "dominant": dominant,
            "summary": _emotion_summary(next_dimensions),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _ = cycle_id
        self._sync_to_redis()
        return self.current()

    def _restore_from_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            raw = redis_client.get(EMOTION_STATE_KEY)
            if raw is None:
                return
            payload = json.loads(_decode_redis_value(raw))
            if not isinstance(payload, dict):
                return
            dimensions = payload.get("dimensions")
            if not isinstance(dimensions, dict):
                return
            normalized_dimensions = {
                dimension: _clamp_emotion(_coerce_float(dimensions.get(dimension)))
                for dimension in self._dimensions
            }
            dominant = str(payload.get("dominant") or "")
            if dominant not in normalized_dimensions:
                dominant = max(normalized_dimensions.items(), key=lambda item: item[1])[0]
            updated_at = str(payload.get("updated_at") or "")
            self._shadow = {
                "dimensions": normalized_dimensions,
                "dominant": dominant,
                "summary": _emotion_summary(normalized_dimensions),
                "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
            }
        except EMOTION_REDIS_EXCEPTIONS:
            self._redis = None

    def _sync_to_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            redis_client.set(
                EMOTION_STATE_KEY,
                json.dumps(self._shadow, ensure_ascii=False),
            )
        except EMOTION_REDIS_EXCEPTIONS:
            self._redis = None

    def _default_snapshot(self) -> EmotionSnapshot:
        dimensions = {dimension: 0.0 for dimension in self._dimensions}
        dominant = self._dimensions[0]
        return {
            "dimensions": dimensions,
            "dominant": dominant,
            "summary": _emotion_summary(dimensions),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _infer_from_stimuli(self, inferred: dict[str, float], stimuli: list[Stimulus]) -> None:
        for stimulus in stimuli:
            if stimulus.type == "conversation":
                inferred["concern"] = inferred.get("concern", 0.0) + 0.55
                inferred["curiosity"] = inferred.get("curiosity", 0.0) + 0.25
                continue
            if stimulus.type in {"reading", "search", "news", "web_fetch"}:
                inferred["curiosity"] = inferred.get("curiosity", 0.0) + 0.40
                continue
            if stimulus.type in {"time", "weather", "system_status"}:
                inferred["calm"] = inferred.get("calm", 0.0) + 0.10
            status = str(stimulus.metadata.get("status") or "").strip()
            if status == "failed":
                inferred["frustration"] = inferred.get("frustration", 0.0) + 0.50
            elif status == "succeeded":
                inferred["satisfaction"] = inferred.get("satisfaction", 0.0) + 0.35

    def _infer_from_running_actions(
        self,
        inferred: dict[str, float],
        running_actions: list[ActionRecord],
    ) -> None:
        if not running_actions:
            return
        inferred["concern"] = inferred.get("concern", 0.0) + min(0.35, 0.08 * len(running_actions))

    def _infer_from_thoughts(self, inferred: dict[str, float], thoughts: list[Thought]) -> None:
        for thought in thoughts:
            text = thought.content
            if thought.type == "意图":
                inferred["curiosity"] = inferred.get("curiosity", 0.0) + 0.15
            if thought.type == "反应":
                inferred["concern"] = inferred.get("concern", 0.0) + 0.08
            if any(token in text for token in ("想", "为什么", "好奇", "看看", "研究", "读")):
                inferred["curiosity"] = inferred.get("curiosity", 0.0) + 0.12
            if any(token in text for token in ("松", "静", "安", "稳", "宁")):
                inferred["calm"] = inferred.get("calm", 0.0) + 0.10
            if any(token in text for token in ("失败", "卡住", "等", "抱歉", "没回", "困住")):
                inferred["frustration"] = inferred.get("frustration", 0.0) + 0.12
                inferred["concern"] = inferred.get("concern", 0.0) + 0.08
            if any(token in text for token in ("成功", "做到", "终于", "发出去", "接住")):
                inferred["satisfaction"] = inferred.get("satisfaction", 0.0) + 0.12


def _clamp_emotion(value: float) -> float:
    return max(0.0, min(1.0, value))


def _coerce_float(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _copy_snapshot(snapshot: EmotionSnapshot) -> EmotionSnapshot:
    return {
        "dimensions": dict(snapshot["dimensions"]),
        "dominant": snapshot["dominant"],
        "summary": snapshot["summary"],
        "updated_at": snapshot["updated_at"],
    }


def _emotion_summary(dimensions: dict[str, float]) -> str:
    ranked = sorted(dimensions.items(), key=lambda item: item[1], reverse=True)
    visible = [
        f"{EMOTION_LABELS.get(name, name)} {value:.2f}"
        for name, value in ranked
        if value >= 0.08
    ][:3]
    if not visible:
        return "情绪平稳，波动很轻。"
    return "，".join(visible)


def _decode_redis_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
