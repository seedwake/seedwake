"""Perception helpers for passive sensing and proactive cues."""

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone

from core.types import MemorySnapshot, PerceptionStimulusPayload, SystemStatusSnapshot


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

    def __init__(self, config: PerceptionConfig):
        self._config = config
        self._last_seen_cycle: dict[str, int] = {}
        self._last_cue_cycle: dict[str, int] = {}

    @classmethod
    def from_config(cls, raw_config: dict | None) -> "PerceptionManager":
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
                "content": (
                    f"现在是 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}，"
                    f"UTC 时间 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                ),
                "metadata": {
                    "local_iso": now.isoformat(),
                    "utc_iso": datetime.now(timezone.utc).isoformat(),
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
            stimuli.append({
                "type": "system_status",
                "priority": 3 if warnings else 4,
                "source": "system:status",
                "content": str(system_status.get("summary") or "系统状态已更新"),
                "metadata": system_status,
            })
            self._last_seen_cycle["system_status"] = cycle_id

        return stimuli

    def build_prompt_cues(self, cycle_id: int, running_actions: list[object]) -> list[str]:
        cues = []

        if self._should_offer("weather", cycle_id, self._config.weather_cue_interval_cycles, running_actions):
            location_hint = ""
            if self._config.default_weather_location:
                location_hint = "；若不写 location，将使用默认天气位置"
            cues.append(
                f"你已经有一段时间没有感知外部天气了；如果自然想知道，可用 {{action:weather}}{location_hint}。"
            )

        if (
            self._config.news_feed_urls
            and self._should_offer("news", cycle_id, self._config.news_cue_interval_cycles, running_actions)
        ):
            cues.append(
                "你已经有一段时间没有接触外部新闻了；如果自然想知道外界发生了什么，可用 {action:news} 从固定 RSS 信息流里取回内容。"
            )

        if self._should_offer("reading", cycle_id, self._config.reading_cue_interval_cycles, running_actions):
            cues.append(
                "你已经有一段时间没有阅读外部材料了；如果自然想读一点东西，可用 {action:reading}；如果心里已经有想读的方向，也可以自己加 query。"
            )

        return cues

    def observe_stimuli(self, cycle_id: int, stimuli: list[object]) -> None:
        for stimulus in stimuli:
            stimulus_type = str(getattr(stimulus, "type", "")).strip()
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
        running_actions: list[object],
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

    def _is_due_from(
        self,
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
    cpu_count = os.cpu_count() or 1
    load_1m = load_5m = load_15m = 0.0
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except OSError:
        pass

    disk = shutil.disk_usage("/")
    disk_used_ratio = 0.0 if disk.total <= 0 else disk.used / disk.total

    memory_snapshot = _read_memory_snapshot()
    memory_used_ratio = memory_snapshot.get("used_ratio") if memory_snapshot else None

    warnings = []
    load_ratio = load_1m / max(1, cpu_count)
    if load_ratio >= warn_load_ratio:
        warnings.append("CPU 负载偏高")
    if memory_used_ratio is not None and memory_used_ratio >= warn_memory_ratio:
        warnings.append("内存占用偏高")
    if disk_used_ratio >= warn_disk_ratio:
        warnings.append("磁盘占用偏高")

    summary_parts = [
        f"1 分钟负载 {load_1m:.2f} / CPU {cpu_count}",
        f"磁盘 {disk_used_ratio:.0%}",
    ]
    if memory_used_ratio is not None:
        summary_parts.append(f"内存 {memory_used_ratio:.0%}")

    summary = "；".join(summary_parts)
    if warnings:
        summary = f"{'，'.join(warnings)}。{summary}"

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
    except Exception:
        return None

    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    if not total_kb or available_kb is None:
        return None

    used_ratio = max(0.0, min(1.0, (total_kb - available_kb) / total_kb))
    return {
        "total_kb": float(total_kb),
        "available_kb": float(available_kb),
        "used_ratio": used_ratio,
    }


def _has_running_perception_action(running_actions: list[object], stimulus_type: str) -> bool:
    action_type = _stimulus_to_action_type(stimulus_type)
    for action in running_actions:
        if str(getattr(action, "type", "")).strip() == action_type:
            return True
    return False


def _stimulus_to_action_type(stimulus_type: str) -> str:
    if stimulus_type == "time":
        return "get_time"
    if stimulus_type == "system_status":
        return "get_system_status"
    return stimulus_type
