"""Emotion baseline manager for Phase 4.

Uses auxiliary LLM for semantic emotion inference instead of keyword matching.
Structural signals (stimulus types, action status) still use rule-based adjustment.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

import redis as redis_lib

from core.action import ActionRecord
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.common_types import EmotionSnapshot, elapsed_ms

EMOTION_STATE_KEY = "seedwake:emotion_state"
DEFAULT_EMOTION_DIMENSIONS = ["curiosity", "calm", "frustration", "satisfaction", "concern"]
EMOTION_LABELS = {
    "curiosity": "好奇",
    "calm": "平静",
    "frustration": "挫败",
    "satisfaction": "满足",
    "concern": "牵挂",
}
EMOTION_INFERENCE_SYSTEM_PROMPT = (
    "你是我的情绪感知模块。"
    "根据以下念头和刺激，判断此刻的情绪状态。"
    "返回一行 JSON，格式：{\"curiosity\":0.5,\"calm\":0.3,\"frustration\":0.1,\"satisfaction\":0.0,\"concern\":0.1}"
    "\n每个维度 0.0-1.0，所有维度之和不必为 1。只输出 JSON，不要解释。"
)
EMOTION_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
logger = logging.getLogger(__name__)


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
        auxiliary_client: ModelClient | None = None,
        auxiliary_model_config: dict | None = None,
        inhibited_actions: int = 0,
        degeneration_alert: bool = False,
    ) -> EmotionSnapshot:
        # Structural signals that don't need LLM
        structural = _structural_emotion_signals(
            stimuli, running_actions,
            inhibited_actions=inhibited_actions,
            degeneration_alert=degeneration_alert,
        )
        # LLM-based semantic inference from thoughts + stimuli
        llm_inferred = _infer_emotion_via_llm(
            auxiliary_client, auxiliary_model_config,
            thoughts, stimuli, self._dimensions,
        )
        # Adaptive fusion: LLM weight depends on inference quality
        llm_weight = _llm_fusion_weight(llm_inferred, self._dimensions)
        struct_weight = 1.0 - llm_weight
        inferred = dict.fromkeys(self._dimensions, 0.0)
        for dimension in self._dimensions:
            llm_value = llm_inferred.get(dimension, 0.0)
            struct_value = structural.get(dimension, 0.0)
            inferred[dimension] = _clamp_emotion(llm_value * llm_weight + struct_value * struct_weight)

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
            decoded = _decode_redis_value(raw)
            if decoded is None:
                return
            payload = json.loads(decoded)
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
        dimensions = dict.fromkeys(self._dimensions, 0.0)
        dominant = self._dimensions[0]
        return {
            "dimensions": dimensions,
            "dominant": dominant,
            "summary": _emotion_summary(dimensions),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def _structural_emotion_signals(
    stimuli: list[Stimulus],
    running_actions: list[ActionRecord],
    *,
    inhibited_actions: int,
    degeneration_alert: bool,
) -> dict[str, float]:
    signals: dict[str, float] = {}
    for stimulus in stimuli:
        if stimulus.type == "conversation":
            signals["concern"] = signals.get("concern", 0.0) + 0.55
            signals["curiosity"] = signals.get("curiosity", 0.0) + 0.25
        elif stimulus.type in {"reading", "search", "news", "web_fetch"}:
            signals["curiosity"] = signals.get("curiosity", 0.0) + 0.40
        elif stimulus.type in {"time", "weather", "system_status"}:
            signals["calm"] = signals.get("calm", 0.0) + 0.10
        status = str(stimulus.metadata.get("status") or "").strip()
        if status == "failed":
            signals["frustration"] = signals.get("frustration", 0.0) + 0.50
        elif status == "succeeded":
            signals["satisfaction"] = signals.get("satisfaction", 0.0) + 0.35
    if running_actions:
        signals["concern"] = signals.get("concern", 0.0) + min(0.35, 0.08 * len(running_actions))
    if inhibited_actions > 0:
        signals["frustration"] = signals.get("frustration", 0.0) + 0.20
    if degeneration_alert:
        signals["frustration"] = signals.get("frustration", 0.0) + 0.25
        signals["concern"] = signals.get("concern", 0.0) + 0.15
    return signals


def _infer_emotion_via_llm(
    client: ModelClient | None,
    model_config: dict | None,
    thoughts: list[Thought],
    stimuli: list[Stimulus],
    dimensions: list[str],
) -> dict[str, float]:
    if client is None or model_config is None:
        return {}
    user_text = _build_emotion_inference_input(thoughts, stimuli)
    if not user_text.strip():
        return {}
    started_at = time.perf_counter()
    try:
        response = client.chat(
            model=str(model_config.get("name") or model_config["name"]),
            messages=[
                {"role": "system", "content": EMOTION_INFERENCE_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            options={"temperature": 0.1, "max_tokens": 80},
        )
    except MODEL_CLIENT_EXCEPTIONS as exc:
        logger.warning("emotion LLM inference failed: %s", exc)
        return {}
    finally:
        logger.info("emotion LLM inference finished in %.1f ms", elapsed_ms(started_at))
    message = response.get("message")
    raw_content = str(message.get("content") or "").strip() if isinstance(message, dict) else ""
    return _parse_emotion_json(raw_content, dimensions)


def _build_emotion_inference_input(thoughts: list[Thought], stimuli: list[Stimulus]) -> str:
    parts: list[str] = []
    for thought in thoughts:
        clean = _strip_noise(thought.content)
        if clean:
            parts.append(f"[{thought.type}] {clean}")
    for stimulus in stimuli:
        parts.append(_stimulus_emotion_summary(stimulus))
    return "\n".join(line for line in parts if line)


def _strip_noise(text: str) -> str:
    cleaned = re.sub(r"\{action:[^}]+\}", "", text)
    # Remove trigger refs
    cleaned = re.sub(r"\(←\s*[^)]+\)", "", cleaned)
    return " ".join(cleaned.split()).strip()


def _stimulus_emotion_summary(stimulus: Stimulus) -> str:
    if stimulus.type == "conversation":
        content = " ".join(stimulus.content.split())[:80]
        return f"[有人对我说话] {content}"
    if stimulus.type in {"time", "system_status"}:
        return ""
    status = str(stimulus.metadata.get("status") or "").strip()
    action_type = str(stimulus.metadata.get("action_type") or stimulus.type).strip()
    if status == "failed":
        return f"[行动失败] {action_type}"
    if status == "succeeded":
        return f"[行动完成] {action_type}"
    content = " ".join(stimulus.content.split())[:60]
    return f"[{stimulus.type}] {content}"


def _parse_emotion_json(raw: str, dimensions: list[str]) -> dict[str, float]:
    cleaned = raw.strip()
    # Strip markdown fences if present
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("emotion LLM returned unparseable JSON: %s", cleaned[:200])
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, float] = {}
    for dimension in dimensions:
        value = parsed.get(dimension)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[dimension] = _clamp_emotion(float(value))
    return result


def _llm_fusion_weight(llm_inferred: dict[str, float], dimensions: list[str]) -> float:
    if not llm_inferred:
        return 0.0
    values = [
        _clamp_emotion(float(llm_inferred[dimension]))
        for dimension in dimensions
        if dimension in llm_inferred
    ]
    if not values:
        return 0.0
    filled = len(values)
    coverage = filled / max(1, len(dimensions))
    peak = max(values)
    total = sum(values)
    if peak < 0.05 and total < 0.15:
        return 0.0
    if coverage >= 0.8 and peak >= 0.18 and total >= 0.40:
        return 0.8
    if coverage >= 0.5 and peak >= 0.10 and total >= 0.20:
        return 0.5
    return 0.2


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


def _decode_redis_value(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return None
