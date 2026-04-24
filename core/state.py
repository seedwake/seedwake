"""Shared runtime state snapshots for backend and SSE consumers."""

import json
import logging
from datetime import datetime, timezone
from math import ceil
from typing import Protocol, cast

import redis as redis_lib

from core.common_types import (
    EmotionSnapshot,
    JsonObject,
    JsonValue,
    RuntimeMode,
    SleepStateSnapshot,
    StateEmotionsPayload,
    StateEventPayload,
    coerce_json_object,
)
from core.emotion import DEFAULT_EMOTION_DIMENSIONS, EMOTION_STATE_KEY
from core.memory.short_term import LATEST_CYCLE_KEY
from core.sleep import SLEEP_STATE_KEY

RUNTIME_STATE_KEY = "seedwake:runtime_state"
STATE_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
logger = logging.getLogger(__name__)


class StateRedisLike(Protocol):
    def get(self, key: str) -> str | bytes | None: ...
    def set(self, key: str, value: str) -> bool | str | None: ...


def build_state_payload(
    *,
    sleep_state: SleepStateSnapshot,
    emotion: EmotionSnapshot,
    current_cycle: int,
    started_at: datetime,
    boot_cycle_baseline: int,
    completed_cycle_count: int,
    total_cycle_seconds: float,
    energy_per_cycle: float,
    drowsy_threshold: float,
    mode_override: RuntimeMode | None = None,
    now: datetime | None = None,
) -> StateEventPayload:
    mode = mode_override or runtime_mode_from_sleep(str(sleep_state.get("mode") or ""))
    timestamp = now or datetime.now(timezone.utc)
    uptime_seconds = max(0, int((timestamp - started_at).total_seconds()))
    current = max(0, current_cycle)
    since_boot = _cycle_since_boot(current, boot_cycle_baseline, completed_cycle_count)
    avg_seconds = (
        round(total_cycle_seconds / completed_cycle_count, 3)
        if completed_cycle_count > 0
        else 0.0
    )
    energy = _coerce_float(sleep_state.get("energy"), 100.0)
    normalized_energy_per_cycle = max(0.0, energy_per_cycle)
    return {
        "mode": mode,
        "energy": round(energy, 3),
        "energy_per_cycle": round(normalized_energy_per_cycle, 3),
        "next_drowsy_cycle": _next_drowsy_cycle(
            current,
            energy,
            normalized_energy_per_cycle,
            max(0.0, drowsy_threshold),
        ),
        "emotions": _state_emotions(emotion),
        "cycle": {
            "current": current,
            "since_boot": since_boot,
            "avg_seconds": avg_seconds,
        },
        "uptime": {
            "started_at": _utc_iso_z(started_at),
            "seconds": uptime_seconds,
        },
    }


def runtime_mode_from_sleep(sleep_mode: str) -> RuntimeMode:
    if sleep_mode in {"light_sleep", "deep_sleep"}:
        return cast(RuntimeMode, sleep_mode)
    return "waking"


def store_state_snapshot(redis_client: StateRedisLike | None, payload: StateEventPayload) -> None:
    if redis_client is None:
        return
    try:
        redis_client.set(RUNTIME_STATE_KEY, json.dumps(payload, ensure_ascii=False))
    except STATE_REDIS_EXCEPTIONS as exc:
        logger.warning("failed to store runtime state snapshot: %s", exc)


def load_state_snapshot(redis_client: StateRedisLike | None) -> StateEventPayload | None:
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(RUNTIME_STATE_KEY)
        if raw is None:
            return None
        payload = json.loads(_decode_redis_value(raw))
        if not isinstance(payload, dict):
            return None
        return _state_payload_from_json(coerce_json_object(payload) or {})
    except STATE_REDIS_EXCEPTIONS as exc:
        logger.warning("failed to load runtime state snapshot: %s", exc)
        return None


def load_or_build_state_snapshot(
    redis_client: StateRedisLike | None,
    config: JsonObject,
) -> StateEventPayload:
    stored = load_state_snapshot(redis_client)
    if stored is not None:
        return stored
    now = datetime.now(timezone.utc)
    current_cycle = _load_latest_cycle_id(redis_client)
    sleep_state = _load_sleep_state(redis_client)
    emotion = _load_emotion_snapshot(redis_client)
    energy_per_cycle = _config_float(config, "sleep", "energy_per_cycle", 0.2)
    drowsy_threshold = _config_float(config, "sleep", "drowsy_threshold", 30.0)
    return build_state_payload(
        sleep_state=sleep_state,
        emotion=emotion,
        current_cycle=current_cycle,
        started_at=now,
        boot_cycle_baseline=max(0, current_cycle - 1),
        completed_cycle_count=0,
        total_cycle_seconds=0.0,
        energy_per_cycle=energy_per_cycle,
        drowsy_threshold=drowsy_threshold,
        now=now,
    )


def _cycle_since_boot(
    current_cycle: int,
    boot_cycle_baseline: int,
    completed_cycle_count: int,
) -> int:
    if completed_cycle_count > 0:
        return completed_cycle_count
    return 0


def _next_drowsy_cycle(
    current_cycle: int,
    energy: float,
    energy_per_cycle: float,
    drowsy_threshold: float,
) -> int:
    if energy <= drowsy_threshold or energy_per_cycle <= 0.0:
        return current_cycle
    return current_cycle + max(1, ceil((energy - drowsy_threshold) / energy_per_cycle))


def _state_emotions(emotion: EmotionSnapshot) -> StateEmotionsPayload:
    dimensions = emotion.get("dimensions") or {}
    return {
        "curiosity": round(_coerce_float(dimensions.get("curiosity"), 0.0), 3),
        "calm": round(_coerce_float(dimensions.get("calm"), 0.0), 3),
        "satisfied": round(_coerce_float(dimensions.get("satisfaction"), 0.0), 3),
        "concern": round(_coerce_float(dimensions.get("concern"), 0.0), 3),
        "frustration": round(_coerce_float(dimensions.get("frustration"), 0.0), 3),
    }


def _state_payload_from_json(payload: JsonObject) -> StateEventPayload | None:
    mode = str(payload.get("mode") or "").strip()
    if mode not in {"waking", "light_sleep", "deep_sleep"}:
        return None
    raw_emotions = payload.get("emotions")
    raw_cycle = payload.get("cycle")
    raw_uptime = payload.get("uptime")
    if not isinstance(raw_emotions, dict) or not isinstance(raw_cycle, dict) or not isinstance(raw_uptime, dict):
        return None
    return {
        "mode": cast(RuntimeMode, mode),
        "energy": _coerce_float(payload.get("energy"), 100.0),
        "energy_per_cycle": _coerce_float(payload.get("energy_per_cycle"), 0.0),
        "next_drowsy_cycle": _coerce_int(payload.get("next_drowsy_cycle"), 0),
        "emotions": {
            "curiosity": _coerce_float(raw_emotions.get("curiosity"), 0.0),
            "calm": _coerce_float(raw_emotions.get("calm"), 0.0),
            "satisfied": _coerce_float(raw_emotions.get("satisfied"), 0.0),
            "concern": _coerce_float(raw_emotions.get("concern"), 0.0),
            "frustration": _coerce_float(raw_emotions.get("frustration"), 0.0),
        },
        "cycle": {
            "current": _coerce_int(raw_cycle.get("current"), 0),
            "since_boot": _coerce_int(raw_cycle.get("since_boot"), 0),
            "avg_seconds": _coerce_float(raw_cycle.get("avg_seconds"), 0.0),
        },
        "uptime": {
            "started_at": str(raw_uptime.get("started_at") or ""),
            "seconds": _coerce_int(raw_uptime.get("seconds"), 0),
        },
    }


def _load_latest_cycle_id(redis_client: StateRedisLike | None) -> int:
    if redis_client is None:
        return 0
    try:
        return _coerce_int(_decode_redis_optional(redis_client.get(LATEST_CYCLE_KEY)), 0)
    except STATE_REDIS_EXCEPTIONS:
        return 0


def _load_sleep_state(redis_client: StateRedisLike | None) -> SleepStateSnapshot:
    default: SleepStateSnapshot = {
        "energy": 100.0,
        "mode": "awake",
        "last_light_sleep_cycle": 0,
        "last_deep_sleep_cycle": 0,
        "last_deep_sleep_at": datetime.now(timezone.utc).isoformat(),
        "summary": "",
    }
    payload = _load_json_object(redis_client, SLEEP_STATE_KEY)
    if payload is None:
        return default
    return {
        "energy": _coerce_float(payload.get("energy"), default["energy"]),
        "mode": str(payload.get("mode") or default["mode"]),
        "last_light_sleep_cycle": _coerce_int(payload.get("last_light_sleep_cycle"), 0),
        "last_deep_sleep_cycle": _coerce_int(payload.get("last_deep_sleep_cycle"), 0),
        "last_deep_sleep_at": str(payload.get("last_deep_sleep_at") or default["last_deep_sleep_at"]),
        "summary": str(payload.get("summary") or ""),
    }


def _load_emotion_snapshot(redis_client: StateRedisLike | None) -> EmotionSnapshot:
    payload = _load_json_object(redis_client, EMOTION_STATE_KEY)
    if payload is None:
        dimensions = dict.fromkeys(DEFAULT_EMOTION_DIMENSIONS, 0.0)
        return {
            "dimensions": dimensions,
            "dominant": DEFAULT_EMOTION_DIMENSIONS[0],
            "summary": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    raw_dimensions = payload.get("dimensions")
    dimensions_source = raw_dimensions if isinstance(raw_dimensions, dict) else {}
    dimensions = {
        dimension: _coerce_float(dimensions_source.get(dimension), 0.0)
        for dimension in DEFAULT_EMOTION_DIMENSIONS
    }
    dominant = str(payload.get("dominant") or DEFAULT_EMOTION_DIMENSIONS[0])
    if dominant not in dimensions:
        dominant = DEFAULT_EMOTION_DIMENSIONS[0]
    return {
        "dimensions": dimensions,
        "dominant": dominant,
        "summary": str(payload.get("summary") or ""),
        "updated_at": str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
    }


def _load_json_object(redis_client: StateRedisLike | None, key: str) -> JsonObject | None:
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(key)
        if raw is None:
            return None
        decoded = json.loads(_decode_redis_value(raw))
        if not isinstance(decoded, dict):
            return None
        return coerce_json_object(decoded)
    except STATE_REDIS_EXCEPTIONS:
        return None


def _config_float(config: JsonObject, section: str, key: str, default: float) -> float:
    raw_section = config.get(section)
    if not isinstance(raw_section, dict):
        return default
    return _coerce_float(raw_section.get(key), default)


def _decode_redis_optional(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return _decode_redis_value(value)


def _decode_redis_value(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _utc_iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_float(value: JsonValue, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _coerce_int(value: JsonValue, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default
