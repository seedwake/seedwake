"""Sleep and energy management for Phase 4."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import redis as redis_lib

from core.embedding import embed_text
from core.memory.habit import HabitMemory
from core.memory.long_term import LongTermMemory
from core.memory.short_term import ShortTermMemory
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.stimulus import Stimulus
from core.thought_parser import Thought
from core.types import EmotionSnapshot, SleepStateSnapshot

SLEEP_STATE_KEY = "seedwake:sleep_state"
SLEEP_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)


@dataclass(frozen=True)
class SleepRunResult:
    state: SleepStateSnapshot
    archived_count: int
    created_habits: int
    cooled_memories: int
    deep_summary: str


class SleepManager:
    def __init__(
        self,
        redis_client: redis_lib.Redis | None,
        *,
        energy_per_cycle: float,
        drowsy_threshold: float,
        light_sleep_recovery: float,
        deep_sleep_trigger_hours: float,
        archive_importance_threshold: float,
    ) -> None:
        self._redis = redis_client
        self._energy_per_cycle = max(0.0, energy_per_cycle)
        self._drowsy_threshold = max(0.0, drowsy_threshold)
        self._light_sleep_recovery = max(self._drowsy_threshold, light_sleep_recovery)
        self._deep_sleep_trigger_hours = max(1.0, deep_sleep_trigger_hours)
        self._archive_importance_threshold = archive_importance_threshold
        self._shadow = self._default_state()
        self._restore_from_redis()

    def attach_redis(self, redis_client: redis_lib.Redis | None) -> bool:
        self._redis = redis_client
        self._sync_to_redis()
        return self._redis is not None

    def current(self) -> SleepStateSnapshot:
        return _copy_sleep_state(self._shadow)

    def consume_cycle(
        self,
        cycle_id: int,
        stimuli: list[Stimulus],
        *,
        failure_count: int,
        degeneration_alert: bool,
    ) -> SleepStateSnapshot:
        penalty = self._energy_per_cycle
        if not stimuli:
            penalty += 0.1
        if failure_count > 0:
            penalty += 0.3 * failure_count
        if degeneration_alert:
            penalty += 0.8
        energy = max(0.0, self._shadow["energy"] - penalty)
        mode = "awake"
        if energy <= self._drowsy_threshold:
            mode = "drowsy"
        self._shadow = {
            **self._shadow,
            "energy": round(energy, 3),
            "mode": mode,
            "summary": _sleep_summary(energy, mode),
        }
        _ = cycle_id
        self._sync_to_redis()
        return self.current()

    def should_light_sleep(self, *, degeneration_alert: bool, buffer_thoughts: list[Thought]) -> bool:
        if degeneration_alert:
            return True
        if self._shadow["energy"] <= self._drowsy_threshold:
            return True
        return len(buffer_thoughts) >= 90

    def should_deep_sleep(self, *, now: datetime) -> bool:
        last = self._shadow["last_deep_sleep_at"]
        if not last:
            return False
        try:
            last_time = datetime.fromisoformat(last)
        except ValueError:
            return False
        elapsed_hours = (now - last_time).total_seconds() / 3600.0
        return elapsed_hours >= self._deep_sleep_trigger_hours

    def run_light_sleep(
        self,
        *,
        cycle_id: int,
        stm: ShortTermMemory,
        ltm: LongTermMemory,
        habit_memory: HabitMemory,
        embedding_client: ModelClient,
        embedding_model: str,
    ) -> SleepRunResult:
        buffer_thoughts = stm.buffer_thoughts()
        archived = _archive_candidates(buffer_thoughts, self._archive_importance_threshold)
        archived_count = 0
        archived_ids: list[str] = []
        for thought in archived:
            try:
                embedding = embed_text(embedding_client, thought.content, embedding_model)
            except MODEL_CLIENT_EXCEPTIONS:
                continue
            entry_id = ltm.store(
                content=thought.content,
                memory_type="episodic" if thought.type != "反思" else "semantic",
                embedding=embedding,
                source_cycle_id=thought.cycle_id,
                importance=_sleep_importance(thought),
            )
            if entry_id is None:
                continue
            archived_count += 1
            archived_ids.append(thought.thought_id)
        if archived_ids:
            stm.forget_thought_ids(archived_ids)
        created_habits = len(habit_memory.strengthen_from_sleep(archived))
        habit_memory.decay_inactive()
        cooled = ltm.cool_inactive_memories()
        self._shadow["energy"] = min(100.0, self._light_sleep_recovery)
        self._shadow["mode"] = "awake"
        self._shadow["last_light_sleep_cycle"] = cycle_id
        self._shadow["summary"] = _sleep_summary(self._shadow["energy"], "awake")
        self._sync_to_redis()
        return SleepRunResult(
            state=self.current(),
            archived_count=archived_count,
            created_habits=created_habits,
            cooled_memories=cooled,
            deep_summary="",
        )

    def run_deep_sleep(
        self,
        *,
        cycle_id: int,
        stm: ShortTermMemory,
        ltm: LongTermMemory,
        habit_memory: HabitMemory,
        embedding_client: ModelClient,
        embedding_model: str,
        auxiliary_client: ModelClient,
        auxiliary_model_config: dict,
        emotion: EmotionSnapshot,
    ) -> SleepRunResult:
        light_result = self.run_light_sleep(
            cycle_id=cycle_id,
            stm=stm,
            ltm=ltm,
            habit_memory=habit_memory,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
        )
        merged = ltm.merge_exact_duplicates()
        pruned = ltm.prune_low_importance()
        deep_summary = _generate_deep_sleep_summary(
            auxiliary_client,
            auxiliary_model_config,
            cycle_id=cycle_id,
            emotion=emotion,
            archived_count=light_result.archived_count,
            merged_count=merged,
            pruned_count=pruned,
        )
        now = datetime.now(timezone.utc).isoformat()
        self._shadow["energy"] = 100.0
        self._shadow["mode"] = "awake"
        self._shadow["last_light_sleep_cycle"] = cycle_id
        self._shadow["last_deep_sleep_cycle"] = cycle_id
        self._shadow["last_deep_sleep_at"] = now
        self._shadow["summary"] = _sleep_summary(100.0, "awake")
        self._sync_to_redis()
        return SleepRunResult(
            state=self.current(),
            archived_count=light_result.archived_count,
            created_habits=light_result.created_habits,
            cooled_memories=light_result.cooled_memories + merged + pruned,
            deep_summary=deep_summary,
        )

    def _restore_from_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            raw = redis_client.get(SLEEP_STATE_KEY)
            if raw is None:
                return
            payload = json.loads(_decode_redis_value(raw))
            if not isinstance(payload, dict):
                return
            self._shadow = {
                "energy": float(payload.get("energy") or 100.0),
                "mode": str(payload.get("mode") or "awake"),
                "last_light_sleep_cycle": int(payload.get("last_light_sleep_cycle") or 0),
                "last_deep_sleep_cycle": int(payload.get("last_deep_sleep_cycle") or 0),
                "last_deep_sleep_at": str(payload.get("last_deep_sleep_at") or ""),
                "summary": str(payload.get("summary") or _sleep_summary(100.0, "awake")),
            }
        except SLEEP_REDIS_EXCEPTIONS:
            self._redis = None

    def _sync_to_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            redis_client.set(SLEEP_STATE_KEY, json.dumps(self._shadow, ensure_ascii=False))
        except SLEEP_REDIS_EXCEPTIONS:
            self._redis = None

    def _default_state(self) -> SleepStateSnapshot:
        return {
            "energy": 100.0,
            "mode": "awake",
            "last_light_sleep_cycle": 0,
            "last_deep_sleep_cycle": 0,
            "last_deep_sleep_at": "",
            "summary": _sleep_summary(100.0, "awake"),
        }


def _archive_candidates(thoughts: list[Thought], threshold: float) -> list[Thought]:
    return [
        thought
        for thought in thoughts
        if _sleep_importance(thought) >= threshold or thought.type == "反思"
    ]


def _sleep_importance(thought: Thought) -> float:
    importance = 0.15 + max(0.0, thought.attention_weight) * 0.55
    if thought.type == "意图":
        importance += 0.08
    if thought.type == "反应":
        importance += 0.05
    if thought.type == "反思":
        importance += 0.25
    if thought.action_request is not None:
        importance += 0.12
    if thought.trigger_ref:
        importance += 0.06
    return min(1.0, round(importance, 4))


def _generate_deep_sleep_summary(
    client: ModelClient,
    model_config: dict,
    *,
    cycle_id: int,
    emotion: EmotionSnapshot,
    archived_count: int,
    merged_count: int,
    pruned_count: int,
) -> str:
    prompt = (
        "请用一句中文总结这次深睡整理的意义，只输出一句自然语言。\n"
        f"cycle={cycle_id}\n"
        f"emotion={emotion['summary']}\n"
        f"archived={archived_count}\n"
        f"merged={merged_count}\n"
        f"pruned={pruned_count}\n"
    )
    try:
        response = client.chat(
            model=str(model_config["name"]),
            messages=[
                {"role": "system", "content": "你在总结 Seedwake 的一次深睡整理。只输出一句中文总结。"},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "max_tokens": 80},
        )
    except MODEL_CLIENT_EXCEPTIONS:
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    return " ".join(str(message.get("content") or "").split())


def _sleep_summary(energy: float, mode: str) -> str:
    if mode == "drowsy":
        return f"精力 {energy:.1f}/100，开始发困，适合进入浅睡整理。"
    return f"精力 {energy:.1f}/100，当前仍清醒。"


def _decode_redis_value(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _copy_sleep_state(state: SleepStateSnapshot) -> SleepStateSnapshot:
    return {
        "energy": state["energy"],
        "mode": state["mode"],
        "last_light_sleep_cycle": state["last_light_sleep_cycle"],
        "last_deep_sleep_cycle": state["last_deep_sleep_cycle"],
        "last_deep_sleep_at": state["last_deep_sleep_at"],
        "summary": state["summary"],
    }
