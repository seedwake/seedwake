"""Perception helpers for passive sensing and proactive cues."""

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from core.i18n import t
from core.stimulus import Stimulus
from core.common_types import MemorySnapshot, PerceptionStimulusPayload, SystemStatusSnapshot

if TYPE_CHECKING:
    from core.action import ActionRecord

logger = logging.getLogger(__name__)
_LOADAVG_WARNING_EMITTED = False


@dataclass
class PerceptionConfig:
    passive_time_interval_cycles: int = 12
    passive_system_status_interval_cycles: int = 24
    news_cue_interval_cycles: int = 90
    news_feed_urls: list[str] | None = None
    weather_cue_interval_cycles: int = 60
    reading_cue_interval_cycles: int = 120
    system_status_warn_load_ratio: float = 1.0
    system_status_warn_memory_ratio: float = 0.9
    system_status_warn_disk_ratio: float = 0.9
    default_weather_location: str = ""


class PerceptionManager:
    """Tracks passive sensing cadence and proactive perception opportunities."""

    def __init__(self, config: PerceptionConfig) -> None:
        self._config = config
        self._last_seen_cycle: dict[str, int] = {}
        self._last_cue_cycle: dict[str, int] = {}

    @classmethod
    def from_config(
        cls: type["PerceptionManager"],
        raw_config: dict | None,
    ) -> "PerceptionManager":
        cfg = raw_config or {}
        return cls(PerceptionConfig(
            passive_time_interval_cycles=max(1, int(cfg.get("passive_time_interval_cycles", 12))),
            passive_system_status_interval_cycles=max(1, int(cfg.get("passive_system_status_interval_cycles", 24))),
            news_cue_interval_cycles=max(1, int(cfg.get("news_cue_interval_cycles", 90))),
            news_feed_urls=[str(item).strip() for item in cfg.get("news_feed_urls", []) if str(item).strip()],
            weather_cue_interval_cycles=max(1, int(cfg.get("weather_cue_interval_cycles", 60))),
            reading_cue_interval_cycles=max(1, int(cfg.get("reading_cue_interval_cycles", 120))),
            system_status_warn_load_ratio=float(cfg.get("system_status_warn_load_ratio", 1.0)),
            system_status_warn_memory_ratio=float(cfg.get("system_status_warn_memory_ratio", 0.9)),
            system_status_warn_disk_ratio=float(cfg.get("system_status_warn_disk_ratio", 0.9)),
            default_weather_location=str(cfg.get("default_weather_location", "")).strip(),
        ))

    def collect_passive_stimuli(self, cycle_id: int) -> list[PerceptionStimulusPayload]:
        stimuli: list[PerceptionStimulusPayload] = []

        if self._is_due("time", cycle_id, self._config.passive_time_interval_cycles):
            now = datetime.now().astimezone()
            stimuli.append({
                "type": "time",
                "priority": 4,
                "source": "system:clock",
                "content": t("perception.time_content", time_str=now.strftime('%Y-%m-%d %H:%M %Z')),
                "metadata": {
                    "local_iso": now.isoformat(),
                },
            })
            self._last_seen_cycle["time"] = cycle_id

        system_status = collect_system_status_snapshot(
            warn_load_ratio=self._config.system_status_warn_load_ratio,
            warn_memory_ratio=self._config.system_status_warn_memory_ratio,
            warn_disk_ratio=self._config.system_status_warn_disk_ratio,
        )
        warnings = list(system_status.get("warnings") or [])
        if warnings or self._is_due("system_status", cycle_id, self._config.passive_system_status_interval_cycles):
            system_stimulus: PerceptionStimulusPayload = {
                "type": "system_status",
                "priority": 3 if warnings else 4,
                "source": "system:status",
                "content": str(system_status.get("summary") or t("perception.system_status_default")),
                "metadata": dict(system_status),
            }
            stimuli.append(system_stimulus)
            self._last_seen_cycle["system_status"] = cycle_id

        return stimuli

    def build_prompt_cues(self, cycle_id: int, running_actions: list["ActionRecord"]) -> list[str]:
        cues = []

        if self._should_offer("weather", cycle_id, self._config.weather_cue_interval_cycles, running_actions):
            cues.append(t("perception.cue.weather"))

        if (
            self._config.news_feed_urls
            and self._should_offer("news", cycle_id, self._config.news_cue_interval_cycles, running_actions)
        ):
            cues.append(t("perception.cue.news"))

        if self._should_offer("reading", cycle_id, self._config.reading_cue_interval_cycles, running_actions):
            cues.append(t("perception.cue.reading"))

        return cues

    def observe_stimuli(self, cycle_id: int, stimuli: list[Stimulus]) -> None:
        for stimulus in stimuli:
            stimulus_type = str(stimulus.type).strip()
            self.observe_types(cycle_id, [stimulus_type])

    def observe_types(self, cycle_id: int, stimulus_types: list[str]) -> None:
        for stimulus_type in stimulus_types:
            normalized = str(stimulus_type).strip()
            if normalized in {"time", "system_status", "news", "weather", "reading"}:
                self._last_seen_cycle[normalized] = cycle_id

    def _should_offer(
        self,
        stimulus_type: str,
        cycle_id: int,
        interval_cycles: int,
        running_actions: list["ActionRecord"],
    ) -> bool:
        if not self._is_due(stimulus_type, cycle_id, interval_cycles):
            return False
        if not self._is_due_from(self._last_cue_cycle, stimulus_type, cycle_id, interval_cycles):
            return False
        if _has_running_perception_action(running_actions, stimulus_type):
            return False
        self._last_cue_cycle[stimulus_type] = cycle_id
        return True

    def _is_due(self, stimulus_type: str, cycle_id: int, interval_cycles: int) -> bool:
        return self._is_due_from(self._last_seen_cycle, stimulus_type, cycle_id, interval_cycles)

    @staticmethod
    def _is_due_from(
        store: dict[str, int],
        stimulus_type: str,
        cycle_id: int,
        interval_cycles: int,
    ) -> bool:
        last_cycle = store.get(stimulus_type)
        if last_cycle is None:
            return True
        return cycle_id - last_cycle >= interval_cycles


def collect_system_status_snapshot(
    *,
    warn_load_ratio: float = 1.0,
    warn_memory_ratio: float = 0.9,
    warn_disk_ratio: float = 0.9,
) -> SystemStatusSnapshot:
    global _LOADAVG_WARNING_EMITTED
    cpu_count = os.cpu_count() or 1
    load_1m = load_5m = load_15m = 0.0
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except OSError as exc:
        if not _LOADAVG_WARNING_EMITTED:
            logger.warning("os.getloadavg unavailable; using zero load snapshot: %s", exc)
            _LOADAVG_WARNING_EMITTED = True

    disk = shutil.disk_usage("/")
    disk_used_ratio = 0.0 if disk.total <= 0 else disk.used / disk.total

    memory_snapshot = _read_memory_snapshot()
    memory_used_ratio = memory_snapshot.get("used_ratio") if memory_snapshot else None

    warnings = []
    load_ratio = load_1m / max(1, cpu_count)
    if load_ratio >= warn_load_ratio:
        warnings.append(t("perception.status.cpu_high"))
    if memory_used_ratio is not None and memory_used_ratio >= warn_memory_ratio:
        warnings.append(t("perception.status.memory_high"))
    if disk_used_ratio >= warn_disk_ratio:
        warnings.append(t("perception.status.disk_high"))

    summary_parts = [
        t("perception.summary.load", load_1m=load_1m, cpu_count=cpu_count),
        t("perception.summary.disk", disk_used_ratio=disk_used_ratio),
    ]
    if memory_used_ratio is not None:
        summary_parts.append(t("perception.summary.memory", memory_used_ratio=memory_used_ratio))

    summary = t("perception.summary.separator").join(summary_parts)
    if warnings:
        summary = t(
            "perception.summary.warning_prefix",
            warnings=t("perception.summary.warning_separator").join(warnings),
            summary=summary,
        )

    return {
        "summary": summary,
        "warnings": warnings,
        "cpu_count": cpu_count,
        "load_1m": load_1m,
        "load_5m": load_5m,
        "load_15m": load_15m,
        "load_ratio": load_ratio,
        "disk_total_bytes": disk.total,
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
        "disk_used_ratio": disk_used_ratio,
        "memory_total_kb": memory_snapshot.get("total_kb") if memory_snapshot else None,
        "memory_available_kb": memory_snapshot.get("available_kb") if memory_snapshot else None,
        "memory_used_ratio": memory_used_ratio,
    }


def _read_memory_snapshot() -> MemorySnapshot | None:
    meminfo_path = "/proc/meminfo"
    if not os.path.exists(meminfo_path):
        return None

    values: dict[str, int] = {}
    try:
        with open(meminfo_path, encoding="utf-8") as file:
            for line in file:
                if ":" not in line:
                    continue
                key, raw_value = line.split(":", 1)
                value = raw_value.strip().split()[0]
                values[key] = int(value)
    except (OSError, ValueError, IndexError):
        return None

    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    if not total_kb or available_kb is None:
        return None

    used_ratio = max(0.0, min(1.0, (total_kb - available_kb) / total_kb))
    memory_snapshot: MemorySnapshot = {
        "total_kb": float(total_kb),
        "available_kb": float(available_kb),
        "used_ratio": used_ratio,
    }
    return memory_snapshot


def _has_running_perception_action(running_actions: list["ActionRecord"], stimulus_type: str) -> bool:
    action_type = _stimulus_to_action_type(stimulus_type)
    for action in running_actions:
        if action.type == action_type:
            return True
    return False


def _stimulus_to_action_type(stimulus_type: str) -> str:
    if stimulus_type == "time":
        return "get_time"
    if stimulus_type == "system_status":
        return "get_system_status"
    return stimulus_type
