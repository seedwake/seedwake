"""Sleep and energy management for Phase 4."""

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Protocol

import redis as redis_lib

from core.embedding import embed_text
from core.memory.habit import HabitMemory
from core.memory.long_term import LongTermEntry, LongTermMemory
from core.memory.short_term import ShortTermMemory
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.stimulus import (
    ConversationRedisLike,
    Stimulus,
    forget_action_result_history_ids,
    load_action_result_history,
    load_conversation_history,
)
from core.thought_parser import Thought, strip_action_markers
from core.common_types import ConversationEntry, EmotionSnapshot, JsonObject, SleepStateSnapshot, elapsed_ms

SLEEP_STATE_KEY = "seedwake:sleep_state"
LIGHT_SLEEP_SEMANTIC_BATCH_MAX_CHARS = 1800
LIGHT_SLEEP_ACTION_RESULT_LIMIT = 80
LIGHT_SLEEP_IMPRESSION_SOURCE_LIMIT = 3
LIGHT_SLEEP_IMPRESSION_ENTRY_LIMIT = 18
DEEP_SLEEP_SUMMARY_MAX_CHARS = 120
DEEP_SLEEP_SELF_REVIEW_MAX_CHARS = 240
SLEEP_REDIS_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    OSError,
)
logger = logging.getLogger(__name__)


class SleepRedisLike(ConversationRedisLike, Protocol):
    def get(self, key: str) -> str | bytes | None: ...
    def set(self, key: str, value: str) -> bool: ...


@dataclass(frozen=True)
class SleepRunResult:
    state: SleepStateSnapshot
    archived_count: int
    semantic_count: int
    impression_updates: int
    action_result_count: int
    created_habits: int
    cooled_memories: int
    maintenance_operations: int
    expired_count: int
    deep_summary: str
    self_review: str
    restart_requested: bool


@dataclass(frozen=True)
class LightSleepArchivePreparation:
    archived: list[Thought]
    new_candidates: list[Thought]
    grouped_new_candidates: dict[str, list[Thought]]
    already_stored: set[str]
    already_stored_count: int
    duplicate_candidates: int


class SleepManager:
    def __init__(
        self,
        redis_client: SleepRedisLike | None,
        *,
        energy_per_cycle: float,
        drowsy_threshold: float,
        light_sleep_recovery: float,
        deep_sleep_trigger_hours: float,
        archive_importance_threshold: float,
        deep_sleep_failure_threshold: int,
        deep_sleep_active_memory_threshold: int,
        inactive_purge_days: int,
        restart_after_deep_sleep: bool,
    ) -> None:
        self._redis = redis_client
        self._energy_per_cycle = max(0.0, energy_per_cycle)
        self._drowsy_threshold = max(0.0, drowsy_threshold)
        self._light_sleep_recovery = max(self._drowsy_threshold, light_sleep_recovery)
        self._deep_sleep_trigger_hours = max(1.0, deep_sleep_trigger_hours)
        self._archive_importance_threshold = archive_importance_threshold
        self._deep_sleep_failure_threshold = max(1, deep_sleep_failure_threshold)
        self._deep_sleep_active_memory_threshold = max(100, deep_sleep_active_memory_threshold)
        self._inactive_purge_days = max(1, inactive_purge_days)
        self._restart_after_deep_sleep = restart_after_deep_sleep
        self._shadow = self._default_state()
        self._restore_from_redis()

    def attach_redis(self, redis_client: SleepRedisLike | None) -> bool:
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
        buffer_count = len(buffer_thoughts)
        if degeneration_alert:
            logger.info("sleep decision: light_sleep=True (reason=degeneration, buffer=%d)", buffer_count)
            return True
        if self._shadow["energy"] <= self._drowsy_threshold:
            logger.info(
                "sleep decision: light_sleep=True (reason=drowsy, energy=%.1f, buffer=%d)",
                self._shadow["energy"], buffer_count,
            )
            return True
        if buffer_count >= 90:
            logger.info("sleep decision: light_sleep=True (reason=buffer_full, buffer=%d)", buffer_count)
            return True
        return False

    def should_deep_sleep(
        self,
        *,
        now: datetime,
        failure_count: int,
        degeneration_alert: bool,
        active_memory_count: int,
    ) -> bool:
        elapsed_triggered, elapsed_hours = _deep_sleep_elapsed_trigger(
            now,
            self._shadow["last_deep_sleep_at"],
            self._deep_sleep_trigger_hours,
        )
        systemic_triggered = (
            degeneration_alert
            or failure_count >= self._deep_sleep_failure_threshold
            or active_memory_count >= self._deep_sleep_active_memory_threshold
        )
        result = elapsed_triggered or systemic_triggered
        if result:
            reasons = _deep_sleep_reasons(
                elapsed_triggered=elapsed_triggered,
                elapsed_hours=elapsed_hours,
                trigger_hours=self._deep_sleep_trigger_hours,
                degeneration_alert=degeneration_alert,
                failure_count=failure_count,
                failure_threshold=self._deep_sleep_failure_threshold,
                active_memory_count=active_memory_count,
                active_memory_threshold=self._deep_sleep_active_memory_threshold,
            )
            logger.info("sleep decision: deep_sleep=True (reasons=%s)", ", ".join(reasons))
        return result

    def run_light_sleep(
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
        # -- 1. episodic archive --
        archive_started_at = time.perf_counter()
        buffer_thoughts = stm.buffer_thoughts()
        archive_preparation = _prepare_light_sleep_archive(
            ltm,
            buffer_thoughts,
            self._archive_importance_threshold,
        )
        logger.info(
            "light sleep archive pre-filter: candidates=%d, already_stored=%d, new=%d, batch_duplicates=%d",
            len(archive_preparation.archived),
            archive_preparation.already_stored_count,
            len(archive_preparation.new_candidates),
            archive_preparation.duplicate_candidates,
        )
        archived_count = 0
        archived_ids: list[str] = []
        archived_texts: list[str] = []
        # Mark already-stored thoughts for STM cleanup only (no semantic re-processing)
        for thought in archive_preparation.archived:
            if thought.content.strip() in archive_preparation.already_stored:
                archived_ids.append(thought.thought_id)
        for thought in archive_preparation.new_candidates:
            content_group = archive_preparation.grouped_new_candidates.get(thought.content.strip(), [thought])
            try:
                embedding = embed_text(embedding_client, thought.content, embedding_model)
            except MODEL_CLIENT_EXCEPTIONS:
                continue
            entry_id = ltm.store(
                content=thought.content,
                memory_type="episodic",
                embedding=embedding,
                source_cycle_id=thought.cycle_id,
                importance=_sleep_importance(thought),
                emotion_context=_emotion_context_json(emotion),
            )
            if entry_id is None:
                continue
            archived_count += 1
            for grouped_thought in content_group:
                archived_ids.append(grouped_thought.thought_id)
                archived_texts.append(_light_sleep_trace_line(grouped_thought))
        if archived_ids:
            stm.forget_thought_ids(archived_ids)
        logger.info(
            "light sleep episodic archive finished in %.1f ms (buffer=%d, archived=%d, skipped=%d, deduplicated=%d)",
            elapsed_ms(archive_started_at),
            len(buffer_thoughts),
            archived_count,
            archive_preparation.already_stored_count,
            archive_preparation.duplicate_candidates,
        )

        # -- 2. action result archive --
        action_result_started_at = time.perf_counter()
        action_result_stimuli = load_action_result_history(self._redis, limit=LIGHT_SLEEP_ACTION_RESULT_LIMIT)
        archived_action_result_ids = _archive_action_result_memories(
            ltm,
            embedding_client,
            embedding_model,
            action_result_stimuli,
            emotion,
        )
        if archived_action_result_ids:
            forget_action_result_history_ids(self._redis, archived_action_result_ids)
            archived_action_result_id_set = set(archived_action_result_ids)
            archived_texts.extend(
                _light_sleep_action_result_line(stimulus)
                for stimulus in action_result_stimuli
                if stimulus.stimulus_id in archived_action_result_id_set
            )
        logger.info(
            "light sleep action result archive finished in %.1f ms (loaded=%d, archived=%d)",
            elapsed_ms(action_result_started_at),
            len(action_result_stimuli),
            len(archived_action_result_ids),
        )

        # -- 3. semantic memory extraction --
        semantic_started_at = time.perf_counter()
        semantic_count = _store_light_sleep_semantic_memories(
            ltm,
            embedding_client,
            embedding_model,
            auxiliary_client,
            auxiliary_model_config,
            cycle_id=cycle_id,
            emotion=emotion,
            trace_lines=archived_texts,
        )
        logger.info(
            "light sleep semantic extraction finished in %.1f ms (stored=%d)",
            elapsed_ms(semantic_started_at),
            semantic_count,
        )

        # -- 4. impression updates --
        impression_started_at = time.perf_counter()
        impression_updates = _update_impression_memories(
            self._redis,
            ltm,
            embedding_client,
            embedding_model,
            auxiliary_client,
            auxiliary_model_config,
            cycle_id=cycle_id,
            emotion=emotion,
        )
        logger.info(
            "light sleep impression updates finished in %.1f ms (updated=%d)",
            elapsed_ms(impression_started_at),
            impression_updates,
        )

        # -- 5. habit strengthening --
        habit_started_at = time.perf_counter()
        created_habits = len(
            habit_memory.strengthen_from_sleep(
                archive_preparation.archived,
                embedding_client=embedding_client,
                embedding_model=embedding_model,
            )
        )
        habit_memory.decay_inactive()
        logger.info(
            "light sleep habit processing finished in %.1f ms (strengthened=%d)",
            elapsed_ms(habit_started_at),
            created_habits,
        )

        # -- 6. memory cooling --
        cool_started_at = time.perf_counter()
        cooled = ltm.cool_inactive_memories()
        logger.info(
            "light sleep memory cooling finished in %.1f ms (cooled=%d)",
            elapsed_ms(cool_started_at),
            cooled,
        )

        self._shadow["energy"] = min(100.0, self._light_sleep_recovery)
        self._shadow["mode"] = "awake"
        self._shadow["last_light_sleep_cycle"] = cycle_id
        self._shadow["summary"] = _sleep_summary(self._shadow["energy"], "awake")
        self._sync_to_redis()
        return SleepRunResult(
            state=self.current(),
            archived_count=archived_count,
            semantic_count=semantic_count,
            impression_updates=impression_updates,
            action_result_count=len(archived_action_result_ids),
            created_habits=created_habits,
            cooled_memories=cooled,
            maintenance_operations=0,
            expired_count=0,
            deep_summary="",
            self_review="",
            restart_requested=False,
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
            auxiliary_client=auxiliary_client,
            auxiliary_model_config=auxiliary_model_config,
            emotion=emotion,
        )

        merge_started_at = time.perf_counter()
        merged = ltm.merge_exact_duplicates()
        logger.info(
            "deep sleep merge duplicates finished in %.1f ms (merged=%d)",
            elapsed_ms(merge_started_at), merged,
        )

        prune_started_at = time.perf_counter()
        pruned = ltm.prune_low_importance()
        logger.info(
            "deep sleep prune low importance finished in %.1f ms (pruned=%d)",
            elapsed_ms(prune_started_at), pruned,
        )

        maintenance_started_at = time.perf_counter()
        maintenance_operations = ltm.run_deep_sleep_maintenance()
        logger.info(
            "deep sleep maintenance finished in %.1f ms (ops=%d)",
            elapsed_ms(maintenance_started_at), maintenance_operations,
        )

        purge_started_at = time.perf_counter()
        expired_count = ltm.purge_inactive_memories(
            older_than_days=self._inactive_purge_days,
        )
        logger.info(
            "deep sleep purge inactive finished in %.1f ms (expired=%d)",
            elapsed_ms(purge_started_at), expired_count,
        )

        summary_started_at = time.perf_counter()
        deep_summary = _generate_deep_sleep_summary(
            auxiliary_client,
            auxiliary_model_config,
            cycle_id=cycle_id,
            emotion=emotion,
            archived_count=light_result.archived_count,
            merged_count=merged,
            pruned_count=pruned,
            maintenance_count=maintenance_operations,
            expired_count=expired_count,
        )
        logger.info(
            "deep sleep summary generation finished in %.1f ms: %s",
            elapsed_ms(summary_started_at), deep_summary or "(empty)",
        )

        review_started_at = time.perf_counter()
        self_review = _generate_deep_sleep_review(
            auxiliary_client,
            auxiliary_model_config,
            cycle_id=cycle_id,
            emotion=emotion,
            sleep_summary=light_result.state["summary"],
            archived_count=light_result.archived_count,
            semantic_count=light_result.semantic_count,
            impression_updates=light_result.impression_updates,
            action_result_count=light_result.action_result_count,
            merged_count=merged,
            pruned_count=pruned,
            expired_count=expired_count,
        )
        logger.info(
            "deep sleep self-review generation finished in %.1f ms: %s",
            elapsed_ms(review_started_at), self_review or "(empty)",
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
            semantic_count=light_result.semantic_count,
            impression_updates=light_result.impression_updates,
            action_result_count=light_result.action_result_count,
            created_habits=light_result.created_habits,
            cooled_memories=light_result.cooled_memories + merged + pruned,
            maintenance_operations=maintenance_operations,
            expired_count=expired_count,
            deep_summary=deep_summary,
            self_review=self_review,
            restart_requested=self._restart_after_deep_sleep,
        )

    def _restore_from_redis(self) -> None:
        redis_client = self._redis
        if redis_client is None:
            return
        try:
            raw = redis_client.get(SLEEP_STATE_KEY)
            if raw is None:
                return
            decoded = _decode_redis_value(raw)
            if decoded is None:
                return
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                return
            last_deep_sleep_at = str(payload.get("last_deep_sleep_at") or "").strip()
            if not last_deep_sleep_at:
                last_deep_sleep_at = datetime.now(timezone.utc).isoformat()
            self._shadow = {
                "energy": float(payload.get("energy") or 100.0),
                "mode": str(payload.get("mode") or "awake"),
                "last_light_sleep_cycle": int(payload.get("last_light_sleep_cycle") or 0),
                "last_deep_sleep_cycle": int(payload.get("last_deep_sleep_cycle") or 0),
                "last_deep_sleep_at": last_deep_sleep_at,
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

    @staticmethod
    def _default_state() -> SleepStateSnapshot:
        return {
            "energy": 100.0,
            "mode": "awake",
            "last_light_sleep_cycle": 0,
            "last_deep_sleep_cycle": 0,
            "last_deep_sleep_at": datetime.now(timezone.utc).isoformat(),
            "summary": _sleep_summary(100.0, "awake"),
        }


def _deep_sleep_elapsed_trigger(
    now: datetime,
    last_deep_sleep_at: str,
    trigger_hours: float,
) -> tuple[bool, float]:
    if not last_deep_sleep_at:
        return False, 0.0
    try:
        last_time = datetime.fromisoformat(last_deep_sleep_at)
    except ValueError:
        return False, 0.0
    elapsed_hours = (now - last_time).total_seconds() / 3600.0
    return elapsed_hours >= trigger_hours, elapsed_hours


def _deep_sleep_reasons(
    *,
    elapsed_triggered: bool,
    elapsed_hours: float,
    trigger_hours: float,
    degeneration_alert: bool,
    failure_count: int,
    failure_threshold: int,
    active_memory_count: int,
    active_memory_threshold: int,
) -> list[str]:
    reasons: list[str] = []
    if elapsed_triggered:
        reasons.append(f"elapsed={elapsed_hours:.1f}h >= {trigger_hours}h")
    if degeneration_alert:
        reasons.append("degeneration")
    if failure_count >= failure_threshold:
        reasons.append(f"failures={failure_count}")
    if active_memory_count >= active_memory_threshold:
        reasons.append(f"active_memories={active_memory_count}")
    return reasons


def _prepare_light_sleep_archive(
    ltm: LongTermMemory,
    buffer_thoughts: list[Thought],
    archive_importance_threshold: float,
) -> LightSleepArchivePreparation:
    archived = _archive_candidates(buffer_thoughts, archive_importance_threshold)
    already_stored = ltm.existing_contents(
        [thought.content for thought in archived],
        memory_type="episodic",
    )
    grouped_new_candidates: dict[str, list[Thought]] = {}
    new_candidates: list[Thought] = []
    already_stored_thought_count = 0
    for thought in archived:
        normalized_content = thought.content.strip()
        if normalized_content in already_stored:
            already_stored_thought_count += 1
            continue
        grouped_new_candidates.setdefault(normalized_content, []).append(thought)
        if len(grouped_new_candidates[normalized_content]) == 1:
            new_candidates.append(thought)
    duplicate_candidates = sum(len(group) - 1 for group in grouped_new_candidates.values())
    return LightSleepArchivePreparation(
        archived=archived,
        new_candidates=new_candidates,
        grouped_new_candidates=grouped_new_candidates,
        already_stored=already_stored,
        already_stored_count=already_stored_thought_count,
        duplicate_candidates=duplicate_candidates,
    )


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


def _emotion_context_json(emotion: EmotionSnapshot) -> JsonObject:
    return {
        "summary": emotion["summary"],
        "dominant": emotion["dominant"],
        "dimensions": {
            name: float(value)
            for name, value in emotion["dimensions"].items()
        },
    }


def _light_sleep_trace_line(thought: Thought) -> str:
    content = " ".join(strip_action_markers(thought.content).split()).strip()
    return f"[{thought.type}] {content}" if content else ""


def _light_sleep_action_result_line(stimulus: Stimulus) -> str:
    action_type = str(stimulus.metadata.get("action_type") or stimulus.type).strip() or stimulus.type
    content = " ".join(stimulus.content.split()).strip()
    return f"[行动结果/{action_type}] {content}" if content else ""


def _archive_action_result_memories(
    ltm: LongTermMemory,
    embedding_client: ModelClient,
    embedding_model: str,
    stimuli: list[Stimulus],
    emotion: EmotionSnapshot,
) -> list[str]:
    archived_ids: list[str] = []
    seen_contents: set[str] = set()
    unique_contents = [" ".join(s.content.split()).strip() for s in stimuli]
    already_stored = ltm.existing_contents(
        [c for c in unique_contents if c],
        memory_type="action_result",
    )
    for stimulus, content in zip(stimuli, unique_contents):
        if not content:
            continue
        if content in seen_contents or content in already_stored:
            archived_ids.append(stimulus.stimulus_id)
            if content not in seen_contents:
                seen_contents.add(content)
            continue
        seen_contents.add(content)
        try:
            embedding = embed_text(embedding_client, content, embedding_model)
        except MODEL_CLIENT_EXCEPTIONS:
            continue
        entry_id = ltm.store(
            content=content,
            memory_type="action_result",
            embedding=embedding,
            source_cycle_id=_action_result_cycle_id(stimulus),
            entity_tags=_action_result_entity_tags(stimulus),
            importance=_action_result_importance(stimulus),
            emotion_context=_emotion_context_json(emotion),
        )
        if entry_id is None:
            continue
        _ = entry_id
        archived_ids.append(stimulus.stimulus_id)
    return archived_ids


def _store_light_sleep_semantic_memories(
    ltm: LongTermMemory,
    embedding_client: ModelClient,
    embedding_model: str,
    auxiliary_client: ModelClient,
    auxiliary_model_config: dict,
    *,
    cycle_id: int,
    emotion: EmotionSnapshot,
    trace_lines: list[str],
) -> int:
    batches = _semantic_sleep_batches(trace_lines)
    stored = 0
    for batch in batches:
        summary = _summarize_light_sleep_batch(
            auxiliary_client,
            auxiliary_model_config,
            emotion=emotion,
            batch=batch,
        )
        if not summary:
            continue
        logger.info("light sleep semantic summary: %s", summary)
        try:
            embedding = embed_text(embedding_client, summary, embedding_model)
        except MODEL_CLIENT_EXCEPTIONS:
            continue
        entry_id = ltm.store(
            content=summary,
            memory_type="semantic",
            embedding=embedding,
            source_cycle_id=cycle_id,
            importance=0.62,
            emotion_context=_emotion_context_json(emotion),
        )
        if entry_id is None:
            continue
        stored += 1
    return stored


def _semantic_sleep_batches(trace_lines: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for line in trace_lines:
        compact = " ".join(line.split()).strip()
        if not compact:
            continue
        line_chars = len(compact) + 1
        if current and current_chars + line_chars > LIGHT_SLEEP_SEMANTIC_BATCH_MAX_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(compact)
        current_chars += line_chars
    if current:
        batches.append(current)
    return batches


def _summarize_light_sleep_batch(
    client: ModelClient,
    model_config: dict,
    *,
    emotion: EmotionSnapshot,
    batch: list[str],
) -> str:
    if not batch:
        return ""
    prompt = (
        "把下面这些我最近的经历压缩成一条更抽象的语义记忆。"
        "用第一人称\"我\"，保留事实、关系、认识或稳定结论，"
        "不要逐条复读，不要项目符号，控制在 180 字以内。\n\n"
        f"当前情绪：{emotion['summary']}\n"
        "经历：\n"
        + "\n".join(f"- {line}" for line in batch)
    )
    try:
        response = client.chat(
            model=str(model_config["name"]),
            messages=[
                {"role": "system", "content": "你在压缩自己的短期经历，用\"我\"做主语，只输出一条中文语义记忆。"},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "max_tokens": 160},
        )
    except MODEL_CLIENT_EXCEPTIONS:
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    return _trim_generated_text(message.get("content"), 180)


def _update_impression_memories(
    redis_client: SleepRedisLike | None,
    ltm: LongTermMemory,
    embedding_client: ModelClient,
    embedding_model: str,
    auxiliary_client: ModelClient,
    auxiliary_model_config: dict,
    *,
    cycle_id: int,
    emotion: EmotionSnapshot,
) -> int:
    history = load_conversation_history(redis_client, limit=240)
    if not history:
        return 0
    grouped = _recent_impression_groups(history)
    updated = 0
    for source, entries in grouped[:LIGHT_SLEEP_IMPRESSION_SOURCE_LIMIT]:
        existing_entry = _existing_impression_entry(ltm, source)
        if existing_entry is not None and not _impression_needs_refresh(existing_entry, entries):
            continue
        impression = _summarize_impression(
            auxiliary_client,
            auxiliary_model_config,
            source=source,
            subject_name=_impression_subject_name(source, entries),
            existing_summary=existing_entry.content if existing_entry is not None else "",
            entries=entries[-LIGHT_SLEEP_IMPRESSION_ENTRY_LIMIT:],
            emotion=emotion,
        )
        if not impression:
            continue
        logger.info("light sleep impression for %s: %s", source, impression)
        try:
            embedding = embed_text(embedding_client, impression, embedding_model)
        except MODEL_CLIENT_EXCEPTIONS:
            continue
        entry_id = ltm.upsert_impression(
            entity_tag=source,
            content=impression,
            embedding=embedding,
            source_cycle_id=cycle_id,
            importance=0.68,
            emotion_context=_emotion_context_json(emotion),
        )
        if entry_id is None:
            continue
        updated += 1
    return updated


def _recent_impression_groups(
    history: list[ConversationEntry],
) -> list[tuple[str, list[ConversationEntry]]]:
    grouped: dict[str, list[ConversationEntry]] = {}
    for entry in history:
        source = str(entry.get("source") or "").strip()
        if not source:
            continue
        grouped.setdefault(source, []).append(entry)
    ranked: list[tuple[datetime, str, list[ConversationEntry]]] = []
    for source, entries in grouped.items():
        last_timestamp = _conversation_entry_timestamp(entries[-1])
        if last_timestamp is None:
            continue
        ranked.append((last_timestamp, source, entries))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [(source, entries) for _, source, entries in ranked]


def _existing_impression_entry(ltm: LongTermMemory, source: str) -> LongTermEntry | None:
    entries = ltm.recent_by_time(top_k=1, entity_filter=source, memory_types=["impression"])
    return entries[0] if entries else None


def _impression_needs_refresh(existing_entry: LongTermEntry, entries: list[ConversationEntry]) -> bool:
    last_entry_time = _conversation_entry_timestamp(entries[-1])
    if last_entry_time is None:
        return False
    created_at = existing_entry.created_at
    impression_time = (
        created_at.astimezone(timezone.utc)
        if created_at.tzinfo is not None
        else created_at.replace(tzinfo=timezone.utc)
    )
    entry_time = (
        last_entry_time.astimezone(timezone.utc)
        if last_entry_time.tzinfo is not None
        else last_entry_time.replace(tzinfo=timezone.utc)
    )
    return entry_time > impression_time


def _summarize_impression(
    client: ModelClient,
    model_config: dict,
    *,
    source: str,
    subject_name: str,
    existing_summary: str,
    entries: list[ConversationEntry],
    emotion: EmotionSnapshot,
) -> str:
    dialogue = "\n".join(
        _impression_entry_line(entry, subject_name)
        for entry in entries
        if _impression_entry_line(entry, subject_name)
    )
    if not dialogue:
        return ""
    contact_hint = _impression_contact_hint(source)
    prompt = (
        "更新我对一个对话对象的印象摘要。"
        "根据已有印象和最近互动，用第一人称写一段中文自然语言摘要。"
        "必须包含：关系、印象、最近互动、情感基调。"
        "如果有可用联系方式，也要自然保留在摘要里。"
        "不要项目符号，不要编造，不超过 180 字。\n\n"
        f"对象：{subject_name}\n"
        f"source={source}\n"
        f"联系方式：{contact_hint or '（无）'}\n"
        f"当前情绪：{emotion['summary']}\n"
        f"已有印象：{existing_summary or '（无）'}\n"
        f"最近互动：\n{dialogue}\n"
    )
    try:
        response = client.chat(
            model=str(model_config["name"]),
            messages=[
                {"role": "system", "content": "你在生成我对某人的印象摘要，用\"我\"做主语，只输出一段中文摘要。"},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "max_tokens": 180},
        )
    except MODEL_CLIENT_EXCEPTIONS:
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    summary = _trim_generated_text(message.get("content"), 300)
    return _ensure_impression_contact(summary, contact_hint)


def _impression_subject_name(source: str, entries: list[ConversationEntry]) -> str:
    for entry in reversed(entries):
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        full_name = str(metadata.get("telegram_full_name") or "").strip()
        username = str(metadata.get("telegram_username") or "").strip()
        if full_name:
            return full_name
        if username:
            return username
    return source


def _impression_contact_hint(source: str) -> str:
    normalized = source.strip()
    if normalized.startswith("telegram:"):
        return normalized
    return ""


def _ensure_impression_contact(summary: str, contact_hint: str) -> str:
    compact = " ".join(summary.split()).strip()
    if not compact or not contact_hint:
        return compact
    if contact_hint in compact:
        return compact
    return _trim_generated_text(f"联系方式: {contact_hint}。{compact}", 300)


def _impression_entry_line(entry: ConversationEntry, subject_name: str) -> str:
    role = str(entry.get("role") or "").strip()
    speaker = "我" if role == "assistant" else subject_name
    content = " ".join(str(entry.get("content") or "").split()).strip()
    if not content:
        return ""
    return f"{speaker}：{content}"


def _conversation_entry_timestamp(entry: ConversationEntry) -> datetime | None:
    raw_timestamp = str(entry.get("timestamp") or "").strip()
    if not raw_timestamp:
        return None
    try:
        return datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return None


def _action_result_cycle_id(stimulus: Stimulus) -> int | None:
    action_id = str(stimulus.action_id or "").strip()
    if not action_id:
        return None
    match = re.search(r"act_C(\d+)-", action_id)
    if match is None:
        return None
    return int(match.group(1))


def _action_result_entity_tags(stimulus: Stimulus) -> list[str]:
    metadata = stimulus.metadata
    if not isinstance(metadata, dict):
        return []
    result = metadata.get("result")
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if not isinstance(data, dict):
        return []
    tags: list[str] = []
    for key in ("source", "target_source", "target_entity"):
        value = str(data.get(key) or "").strip()
        if value and value not in tags:
            tags.append(value)
    return tags


def _action_result_importance(stimulus: Stimulus) -> float:
    status = str(stimulus.metadata.get("status") or "").strip()
    action_type = str(stimulus.metadata.get("action_type") or "").strip()
    importance = 0.28
    if status == "failed":
        importance += 0.22
    elif status == "succeeded":
        importance += 0.08
    if action_type in {"send_message", "reading", "web_fetch", "search", "news"}:
        importance += 0.10
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
    maintenance_count: int,
    expired_count: int,
) -> str:
    prompt = (
        "请用一句中文总结这次深睡整理的意义，只输出一句自然语言。\n"
        f"cycle={cycle_id}\n"
        f"emotion={emotion['summary']}\n"
        f"archived={archived_count}\n"
        f"merged={merged_count}\n"
        f"pruned={pruned_count}\n"
        f"maintenance={maintenance_count}\n"
        f"expired={expired_count}\n"
    )
    try:
        response = client.chat(
            model=str(model_config["name"]),
            messages=[
                {"role": "system", "content": "你在总结自己的一次深睡整理，用\"我\"做主语。只输出一句中文总结。"},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "max_tokens": 80},
        )
    except MODEL_CLIENT_EXCEPTIONS:
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    return _trim_generated_text(message.get("content"), DEEP_SLEEP_SUMMARY_MAX_CHARS)


def _generate_deep_sleep_review(
    client: ModelClient,
    model_config: dict,
    *,
    cycle_id: int,
    emotion: EmotionSnapshot,
    sleep_summary: str,
    archived_count: int,
    semantic_count: int,
    impression_updates: int,
    action_result_count: int,
    merged_count: int,
    pruned_count: int,
    expired_count: int,
) -> str:
    prompt = (
        "这是一次深睡后的自我评估。"
        "请用第一人称，一小段中文总结我的近期状态，并给出一条最值得关注的调整方向。"
        "不要项目符号，不超过 220 字。\n\n"
        f"cycle={cycle_id}\n"
        f"emotion={emotion['summary']}\n"
        f"sleep_summary={sleep_summary}\n"
        f"archived={archived_count}\n"
        f"semantic={semantic_count}\n"
        f"impressions={impression_updates}\n"
        f"action_results={action_result_count}\n"
        f"merged={merged_count}\n"
        f"pruned={pruned_count}\n"
        f"expired={expired_count}\n"
    )
    try:
        response = client.chat(
            model=str(model_config["name"]),
            messages=[
                {"role": "system", "content": "你在做自己的深睡自评，用\"我\"做主语，只输出一段中文总结。"},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "max_tokens": 220},
        )
    except MODEL_CLIENT_EXCEPTIONS:
        return ""
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    return _trim_generated_text(message.get("content"), DEEP_SLEEP_SELF_REVIEW_MAX_CHARS)


def _trim_generated_text(value: object, limit: int) -> str:
    compact = " ".join(str(value or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _sleep_summary(energy: float, mode: str) -> str:
    if mode == "drowsy":
        return f"精力 {energy:.1f}/100，开始发困，适合进入浅睡整理。"
    return f"精力 {energy:.1f}/100，当前仍清醒。"


def _decode_redis_value(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return None


def _copy_sleep_state(state: SleepStateSnapshot) -> SleepStateSnapshot:
    return {
        "energy": state["energy"],
        "mode": state["mode"],
        "last_light_sleep_cycle": state["last_light_sleep_cycle"],
        "last_deep_sleep_cycle": state["last_deep_sleep_cycle"],
        "last_deep_sleep_at": state["last_deep_sleep_at"],
        "summary": state["summary"],
    }
