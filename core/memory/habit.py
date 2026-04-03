"""Habit seed persistence and activation."""

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
import time

import psycopg
from psycopg import sql

from core.embedding import embed_text
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.stimulus import Stimulus
from core.thought_parser import Thought, thought_action_requests
from core.common_types import HabitControlSignal, HabitPromptEntry, JsonObject, bigram_similarity, elapsed_ms

logger = logging.getLogger(__name__)
HABIT_MIN_PATTERN_LENGTH = 4
HABIT_TEXT_STOPWORDS = {"刚才", "现在", "继续", "已经", "不是", "只是", "一个", "没有"}
HABIT_EMBEDDING_DIMENSIONS = 4096
ACTION_IMPULSE_SIGNAL_TYPE = "action_impulse"


@dataclass(frozen=True)
class HabitSeed:
    id: int
    pattern: str
    category: str
    strength: float
    activation_count: int
    last_activated: datetime | None
    embedding: list[float] | None = None
    semantic_similarity: float = 0.0
    signal_type: str = ""
    signal_payload: JsonObject | None = None


@dataclass(frozen=True)
class HabitSeedCandidate:
    pattern: str
    category: str
    strength: float
    signal_type: str = ""
    signal_payload: JsonObject | None = None


class HabitMemory:
    def __init__(
        self,
        pg_conn: psycopg.Connection | None,
        *,
        max_active_in_prompt: int,
        decay_rate: float,
        activation_similarity_threshold: float = 0.35,
        activation_candidate_limit: int = 12,
    ) -> None:
        self._conn = pg_conn
        self._max_active_in_prompt = max_active_in_prompt
        self._decay_rate = decay_rate
        self._activation_similarity_threshold = activation_similarity_threshold
        self._activation_candidate_limit = max(1, activation_candidate_limit)

    @property
    def available(self) -> bool:
        return self._conn is not None

    def attach_connection(self, pg_conn: psycopg.Connection | None) -> None:
        self._conn = pg_conn

    def ensure_schema(self) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE habit_seeds
                    SET category = 'cognitive'
                    WHERE category IS NULL OR category = ''
                    """
                )
                cur.execute(
                    sql.SQL("""
                    ALTER TABLE habit_seeds
                    ADD COLUMN IF NOT EXISTS embedding vector({})
                    """).format(sql.Literal(HABIT_EMBEDDING_DIMENSIONS))
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ADD COLUMN IF NOT EXISTS signal_type TEXT DEFAULT ''
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ADD COLUMN IF NOT EXISTS signal_payload JSONB DEFAULT '{}'::jsonb
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ALTER COLUMN category SET DEFAULT 'cognitive'
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ALTER COLUMN signal_type SET DEFAULT ''
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ALTER COLUMN signal_payload SET DEFAULT '{}'::jsonb
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ALTER COLUMN category SET NOT NULL
                    """
                )
                cur.execute(
                    """
                    UPDATE habit_seeds
                    SET signal_type = '',
                        signal_payload = '{}'::jsonb
                    WHERE signal_type IS NULL OR signal_payload IS NULL
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ALTER COLUMN signal_type SET NOT NULL
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE habit_seeds
                    ALTER COLUMN signal_payload SET NOT NULL
                    """
                )
                cur.execute(
                    """
                    UPDATE habit_seeds
                    SET signal_type = 'action_impulse',
                        signal_payload = jsonb_build_object(
                            'action_type',
                            substring(pattern FROM '^经常会冒出 ([a-z_]+) 行动冲动$')
                        )
                    WHERE category = 'behavioral'
                      AND signal_type = ''
                      AND pattern ~ '^经常会冒出 [a-z_]+ 行动冲动$'
                    """
                )
                cur.execute(
                    """
                    WITH ranked AS (
                        SELECT id,
                               pattern,
                               category,
                               strength,
                               activation_count,
                               last_activated,
                               signal_type,
                               signal_payload,
                               created_at,
                               updated_at,
                               source_memories,
                               FIRST_VALUE(id) OVER (
                                   PARTITION BY pattern, category
                                   ORDER BY id
                               ) AS keep_id,
                               COUNT(*) OVER (
                                   PARTITION BY pattern, category
                               ) AS group_count
                        FROM habit_seeds
                    ),
                    grouped AS (
                        SELECT
                            keep_id,
                            MAX(strength) AS merged_strength,
                            SUM(activation_count) AS merged_activation_count,
                            MAX(last_activated) AS merged_last_activated,
                            MIN(created_at) AS merged_created_at,
                            MAX(updated_at) AS merged_updated_at,
                            ARRAY(
                                SELECT DISTINCT source_memory
                                FROM ranked AS source_rows
                                CROSS JOIN LATERAL unnest(
                                    COALESCE(source_rows.source_memories, '{}'::bigint[])
                                ) AS source_memory
                                WHERE source_rows.keep_id = ranked.keep_id
                                ORDER BY source_memory
                            ) AS merged_source_memories,
                            (
                                SELECT signaled.signal_type
                                FROM habit_seeds AS signaled
                                JOIN ranked AS signaled_ranked ON signaled_ranked.id = signaled.id
                                WHERE signaled_ranked.keep_id = ranked.keep_id
                                  AND signaled.signal_type <> ''
                                ORDER BY signaled.id
                                LIMIT 1
                            ) AS merged_signal_type,
                            (
                                SELECT signaled.signal_payload
                                FROM habit_seeds AS signaled
                                JOIN ranked AS signaled_ranked ON signaled_ranked.id = signaled.id
                                WHERE signaled_ranked.keep_id = ranked.keep_id
                                  AND signaled.signal_type <> ''
                                ORDER BY signaled.id
                                LIMIT 1
                            ) AS merged_signal_payload,
                            (
                                SELECT embedded.embedding
                                FROM habit_seeds AS embedded
                                JOIN ranked AS embedded_ranked ON embedded_ranked.id = embedded.id
                                WHERE embedded_ranked.keep_id = ranked.keep_id
                                  AND embedded.embedding IS NOT NULL
                                ORDER BY embedded.id
                                LIMIT 1
                            ) AS merged_embedding
                        FROM ranked
                        GROUP BY keep_id
                    )
                    UPDATE habit_seeds AS keeper
                    SET strength = grouped.merged_strength,
                        activation_count = grouped.merged_activation_count,
                        last_activated = grouped.merged_last_activated,
                        created_at = grouped.merged_created_at,
                        updated_at = grouped.merged_updated_at,
                        embedding = COALESCE(keeper.embedding, grouped.merged_embedding),
                        signal_type = COALESCE(NULLIF(keeper.signal_type, ''), grouped.merged_signal_type, ''),
                        signal_payload = CASE
                            WHEN NULLIF(keeper.signal_type, '') IS NOT NULL
                                THEN keeper.signal_payload
                            WHEN grouped.merged_signal_payload IS NOT NULL
                                THEN grouped.merged_signal_payload
                            ELSE keeper.signal_payload
                        END,
                        source_memories = grouped.merged_source_memories
                    FROM grouped
                    WHERE keeper.id = grouped.keep_id
                    """
                )
                cur.execute(
                    """
                    WITH ranked AS (
                        SELECT id,
                               pattern,
                               category,
                               FIRST_VALUE(id) OVER (
                                   PARTITION BY pattern, category
                                   ORDER BY id
                               ) AS keep_id,
                               COUNT(*) OVER (
                                   PARTITION BY pattern, category
                               ) AS group_count
                        FROM habit_seeds
                    )
                    DELETE FROM habit_seeds AS duplicate
                    USING ranked
                    WHERE duplicate.id = ranked.id
                      AND ranked.group_count > 1
                      AND ranked.id <> ranked.keep_id
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_habit_seeds_pattern_category
                    ON habit_seeds (pattern, category)
                    """
                )
            conn.commit()
        except psycopg.Error:
            conn.rollback()
            raise

    def top_active(self) -> list[HabitPromptEntry]:
        seeds = self._fetch_active_seeds(limit=self._max_active_in_prompt * 4)
        entries = [_habit_entry(seed, self._activation_similarity_threshold) for seed in seeds]
        return _diverse_selection(entries, self._max_active_in_prompt)

    def ensure_bootstrap_seeds(
        self,
        items: list[dict],
        *,
        embedding_client: ModelClient | None = None,
        embedding_model: str = "",
    ) -> None:
        for item in items:
            pattern = str(item.get("pattern") or "").strip()
            if not pattern:
                continue
            category = str(item.get("category") or "cognitive").strip() or "cognitive"
            strength = float(item.get("strength") or 0.1)
            embedding = _embed_habit_pattern(embedding_client, embedding_model, pattern)
            self._ensure_seed(pattern, category, strength, embedding=embedding)

    def activate_for_cycle(
        self,
        *,
        goals: list[str],
        note_text: str,
        stimuli: list[Stimulus],
        emotion_summary: str,
        embedding_client: ModelClient | None = None,
        embedding_model: str = "",
    ) -> list[HabitPromptEntry]:
        """Return habits most relevant to the current context for prompt display."""
        seeds = self._prompt_candidates(
            goals=goals,
            note_text=note_text,
            stimuli=stimuli,
            emotion_summary=emotion_summary,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
        )
        if not seeds:
            return []
        context_text = _habit_context_text(goals, note_text, stimuli, emotion_summary)
        entries = [_habit_entry(seed, self._activation_similarity_threshold) for seed in seeds]
        entries = sorted(
            entries,
            key=lambda entry: _habit_entry_score(entry, context_text),
            reverse=True,
        )
        return _diverse_selection(entries, self._max_active_in_prompt)

    def observe_cycle(self, thoughts: list[Thought]) -> int:
        """Record explicit activation evidence from current thoughts.

        Only existing habits are touched. This updates recency/activation_count
        without strengthening, so repeated real recurrence prevents stale decay
        while keeping "display != strengthening".
        """
        conn = self._conn
        if conn is None:
            return 0
        patterns = _activation_patterns_from_thoughts(thoughts)
        if not patterns:
            return 0
        updated = 0
        try:
            with conn.cursor() as cur:
                for pattern, category in patterns:
                    cur.execute(
                        """
                        UPDATE habit_seeds
                        SET activation_count = activation_count + 1,
                            last_activated = NOW(),
                            updated_at = NOW()
                        WHERE pattern = %s AND category = %s
                        """,
                        (pattern, category),
                    )
                    updated += int(cur.rowcount or 0)
            conn.commit()
            return updated
        except psycopg.Error:
            conn.rollback()
            raise

    def _fetch_active_seeds(self, *, limit: int | None) -> list[HabitSeed]:
        conn = self._conn
        if conn is None:
            return []
        started_at = time.perf_counter()
        status = "failed"
        rows: list[tuple] = []
        try:
            with conn.cursor() as cur:
                if limit is None:
                    cur.execute(
                        """
                        SELECT id, pattern, category, strength,
                               activation_count, last_activated,
                               embedding, signal_type, signal_payload
                        FROM habit_seeds
                        WHERE strength > 0.01
                        ORDER BY updated_at DESC, activation_count DESC, strength DESC
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, pattern, category, strength,
                               activation_count, last_activated,
                               embedding, signal_type, signal_payload
                        FROM habit_seeds
                        WHERE strength > 0.01
                        ORDER BY strength DESC, activation_count DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
            status = "ok"
        except psycopg.Error:
            conn.rollback()
            raise
        finally:
            logger.info(
                "habit fetch_active_seeds finished in %.1f ms (status=%s, count=%d)",
                elapsed_ms(started_at),
                status,
                len(rows),
            )
        return [
            HabitSeed(
                id=int(row[0]),
                pattern=str(row[1]),
                category=str(row[2] or "cognitive"),
                strength=float(row[3]),
                activation_count=int(row[4] or 0),
                last_activated=row[5],
                embedding=_coerce_vector(row[6]) if len(row) >= 7 else None,
                signal_type=str(row[7] or "") if len(row) >= 8 else "",
                signal_payload=_coerce_signal_payload(row[8]) if len(row) >= 9 else None,
            )
            for row in rows
        ]

    def strengthen_from_sleep(
        self,
        thoughts: list[Thought],
        *,
        embedding_client: ModelClient | None = None,
        embedding_model: str = "",
    ) -> list[HabitPromptEntry]:
        conn = self._conn
        if conn is None:
            return []
        extracted = _extract_habit_patterns(thoughts)
        if not extracted:
            return []
        created: list[HabitPromptEntry] = []
        for candidate in extracted:
            embedding = _embed_habit_pattern(embedding_client, embedding_model, candidate.pattern)
            habit_id = self._upsert_seed(
                candidate.pattern,
                candidate.category,
                candidate.strength,
                embedding=embedding,
                signal_type=candidate.signal_type,
                signal_payload=candidate.signal_payload,
            )
            if habit_id is None:
                continue
            entry: HabitPromptEntry = {
                "id": habit_id,
                "pattern": candidate.pattern,
                "category": candidate.category,
                "strength": candidate.strength,
            }
            signal = _habit_control_signal(candidate.signal_type, candidate.signal_payload)
            if signal is not None:
                entry["signal"] = signal
            created.append(entry)
        return created

    def decay_inactive(self) -> int:
        conn = self._conn
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE habit_seeds
                    SET strength = GREATEST(0.0, strength - %s),
                        updated_at = NOW()
                    WHERE COALESCE(last_activated, created_at) < NOW() - INTERVAL '3 days'
                    """,
                    (self._decay_rate,),
                )
                affected = cur.rowcount
            conn.commit()
            return int(affected or 0)
        except psycopg.Error:
            conn.rollback()
            raise

    def _touch_habits(self, habit_ids: list[int]) -> None:
        conn = self._conn
        if conn is None or not habit_ids:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE habit_seeds
                    SET activation_count = activation_count + 1,
                        last_activated = NOW(),
                        strength = LEAST(1.0, strength + 0.03),
                        updated_at = NOW()
                    WHERE id = ANY(%s)
                    """,
                    (habit_ids,),
                )
            conn.commit()
        except psycopg.Error:
            conn.rollback()
            raise

    def _prompt_candidates(
        self,
        *,
        goals: list[str],
        note_text: str,
        stimuli: list[Stimulus],
        emotion_summary: str,
        embedding_client: ModelClient | None,
        embedding_model: str,
    ) -> list[HabitSeed]:
        active_seeds = self._fetch_active_seeds(limit=None)
        if not active_seeds:
            return []
        context_text = _habit_context_text(goals, note_text, stimuli, emotion_summary)
        query_embedding = _embed_habit_pattern(embedding_client, embedding_model, context_text)
        semantic_candidates = self._semantic_candidates(query_embedding) if query_embedding is not None else []
        merged_by_id: dict[int, HabitSeed] = {}
        for seed in [*semantic_candidates, *active_seeds]:
            existing = merged_by_id.get(seed.id)
            if existing is None or seed.semantic_similarity > existing.semantic_similarity:
                merged_by_id[seed.id] = seed
        return list(merged_by_id.values())

    def _semantic_candidates(self, query_embedding: list[float]) -> list[HabitSeed]:
        conn = self._conn
        if conn is None:
            return []
        vec_literal = _format_vector(query_embedding)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id,
                           pattern,
                           category,
                           strength,
                           activation_count,
                           last_activated,
                           embedding,
                           signal_type,
                           signal_payload,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM habit_seeds
                    WHERE strength > 0.01
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        vec_literal,
                        vec_literal,
                        self._activation_candidate_limit,
                    ),
                )
                rows = cur.fetchall()
        except psycopg.Error:
            conn.rollback()
            raise
        return [
            HabitSeed(
                id=int(row[0]),
                pattern=str(row[1]),
                category=str(row[2] or "cognitive"),
                strength=float(row[3]),
                activation_count=int(row[4] or 0),
                last_activated=row[5],
                embedding=_coerce_vector(row[6]),
                signal_type=str(row[7] or ""),
                signal_payload=_coerce_signal_payload(row[8]),
                semantic_similarity=float(row[9] or 0.0),
            )
            for row in rows
        ]

    def _upsert_seed(
        self,
        pattern: str,
        category: str,
        strength: float,
        *,
        embedding: list[float] | None = None,
        signal_type: str = "",
        signal_payload: JsonObject | None = None,
    ) -> int | None:
        conn = self._conn
        if conn is None:
            return None
        vec_literal = _format_vector(embedding) if embedding is not None else None
        signal_payload_text = _jsonb_or_empty(signal_payload)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO habit_seeds (pattern, category, strength, embedding, signal_type, signal_payload)
                    VALUES (%s, %s, %s, %s::vector, %s, %s::jsonb)
                    ON CONFLICT (pattern, category)
                    DO UPDATE SET
                        strength = LEAST(1.0, GREATEST(habit_seeds.strength, EXCLUDED.strength) + 0.05),
                        embedding = COALESCE(habit_seeds.embedding, EXCLUDED.embedding),
                        signal_type = CASE
                            WHEN habit_seeds.signal_type = '' AND EXCLUDED.signal_type <> ''
                                THEN EXCLUDED.signal_type
                            ELSE habit_seeds.signal_type
                        END,
                        signal_payload = CASE
                            WHEN habit_seeds.signal_type = '' AND EXCLUDED.signal_type <> ''
                                THEN EXCLUDED.signal_payload
                            ELSE habit_seeds.signal_payload
                        END,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (pattern, category, strength, vec_literal, signal_type, signal_payload_text),
                )
                habit_id = _row_first_int(cur.fetchone())
            conn.commit()
            return habit_id
        except psycopg.Error:
            conn.rollback()
            raise

    def _ensure_seed(
        self,
        pattern: str,
        category: str,
        strength: float,
        *,
        embedding: list[float] | None = None,
        signal_type: str = "",
        signal_payload: JsonObject | None = None,
    ) -> int | None:
        conn = self._conn
        if conn is None:
            return None
        vec_literal = _format_vector(embedding) if embedding is not None else None
        signal_payload_text = _jsonb_or_empty(signal_payload)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO habit_seeds (pattern, category, strength, embedding, signal_type, signal_payload)
                    VALUES (%s, %s, %s, %s::vector, %s, %s::jsonb)
                    ON CONFLICT (pattern, category)
                    DO UPDATE SET
                        embedding = COALESCE(habit_seeds.embedding, EXCLUDED.embedding),
                        signal_type = CASE
                            WHEN habit_seeds.signal_type = '' AND EXCLUDED.signal_type <> ''
                                THEN EXCLUDED.signal_type
                            ELSE habit_seeds.signal_type
                        END,
                        signal_payload = CASE
                            WHEN habit_seeds.signal_type = '' AND EXCLUDED.signal_type <> ''
                                THEN EXCLUDED.signal_payload
                            ELSE habit_seeds.signal_payload
                        END,
                        updated_at = CASE
                            WHEN habit_seeds.embedding IS NULL AND EXCLUDED.embedding IS NOT NULL
                                 OR (habit_seeds.signal_type = '' AND EXCLUDED.signal_type <> '')
                                THEN NOW()
                            ELSE habit_seeds.updated_at
                        END
                    RETURNING id
                    """,
                    (pattern, category, strength, vec_literal, signal_type, signal_payload_text),
                )
                habit_id = _row_first_int(cur.fetchone())
            conn.commit()
            return habit_id
        except psycopg.Error:
            conn.rollback()
            raise


def _row_first_int(row: tuple[object, ...] | None) -> int | None:
    if row is None or not row:
        return None
    return int(row[0])


def _extract_habit_patterns(thoughts: list[Thought]) -> list[HabitSeedCandidate]:
    patterns: dict[tuple[str, str], tuple[int, HabitSeedCandidate]] = {}
    for thought in thoughts:
        for candidate in _habit_patterns_from_thought(thought):
            key = (candidate.pattern, candidate.category)
            count, existing = patterns.get(
                key,
                (
                    0,
                    HabitSeedCandidate(
                        pattern=candidate.pattern,
                        category=candidate.category,
                        strength=0.18,
                        signal_type=candidate.signal_type,
                        signal_payload=candidate.signal_payload,
                    ),
                ),
            )
            patterns[key] = (
                count + 1,
                HabitSeedCandidate(
                    pattern=existing.pattern,
                    category=existing.category,
                    strength=min(0.95, existing.strength + 0.08),
                    signal_type=existing.signal_type or candidate.signal_type,
                    signal_payload=existing.signal_payload or candidate.signal_payload,
                ),
            )
    return [
        candidate
        for _, (count, candidate) in patterns.items()
        if count >= 2 or candidate.strength >= 0.34
    ]


def _activation_patterns_from_thoughts(thoughts: list[Thought]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for thought in thoughts:
        for candidate in _habit_patterns_from_thought(thought):
            key = (candidate.pattern, candidate.category)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _habit_patterns_from_thought(thought: Thought) -> list[HabitSeedCandidate]:
    action_patterns = _action_habit_patterns(thought)
    if action_patterns:
        return action_patterns
    normalized = _normalize_habit_text(thought.content)
    if len(normalized) < HABIT_MIN_PATTERN_LENGTH:
        return []
    text_pattern = _text_habit_pattern(normalized, thought)
    return [text_pattern] if text_pattern is not None else []


def _normalize_habit_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())


def _action_habit_patterns(thought: Thought) -> list[HabitSeedCandidate]:
    seen_action_types: set[str] = set()
    patterns: list[HabitSeedCandidate] = []
    for action_request in thought_action_requests(thought):
        action_type = str(action_request.get("type") or "").strip()
        if not action_type or action_type in seen_action_types:
            continue
        seen_action_types.add(action_type)
        patterns.append(
            HabitSeedCandidate(
                pattern=f"经常会冒出 {action_type} 行动冲动",
                category="behavioral",
                strength=0.18,
                signal_type=ACTION_IMPULSE_SIGNAL_TYPE,
                signal_payload={"action_type": action_type},
            )
        )
    return patterns


def _text_habit_pattern(normalized: str, thought: Thought) -> HabitSeedCandidate | None:
    clauses = re.split(r"[，。！？；,.!?;]+", normalized)
    for clause in clauses:
        compact = clause.strip()
        if len(compact) < HABIT_MIN_PATTERN_LENGTH:
            continue
        if compact in HABIT_TEXT_STOPWORDS:
            continue
        return HabitSeedCandidate(
            pattern=compact[:48],
            category=_habit_category(thought),
            strength=0.18,
        )
    return None


def _habit_category(thought: Thought) -> str:
    if thought.type == "反应":
        return "emotional"
    return "cognitive"


def _diverse_selection(entries: list[HabitPromptEntry], limit: int) -> list[HabitPromptEntry]:
    """Pick top habits with category diversity — avoid showing all from the same category."""
    if len(entries) <= limit:
        return entries
    selected: list[HabitPromptEntry] = []
    seen_categories: dict[str, int] = {}
    max_per_category = max(1, (limit + 1) // 2)
    # First pass: pick strongest from each category up to limit
    for entry in entries:
        if len(selected) >= limit:
            break
        category = entry["category"]
        if seen_categories.get(category, 0) >= max_per_category:
            continue
        selected.append(entry)
        seen_categories[category] = seen_categories.get(category, 0) + 1
    # Fill remaining slots if diversity left gaps
    if len(selected) < limit:
        selected_ids = {entry["id"] for entry in selected}
        for entry in entries:
            if len(selected) >= limit:
                break
            if entry["id"] not in selected_ids:
                selected.append(entry)
    return selected


def _habit_entry(seed: HabitSeed, threshold: float) -> HabitPromptEntry:
    entry: HabitPromptEntry = {
        "id": seed.id,
        "pattern": seed.pattern,
        "category": seed.category,
        "strength": seed.strength,
    }
    signal = _habit_control_signal(seed.signal_type, seed.signal_payload)
    if signal is not None:
        entry["signal"] = signal
    if seed.semantic_similarity > 0.0:
        entry["activation_score"] = round(seed.semantic_similarity, 4)
        entry["manifested"] = seed.semantic_similarity >= threshold
    return entry


def _habit_control_signal(
    signal_type: str,
    signal_payload: JsonObject | None,
) -> HabitControlSignal | None:
    normalized_signal_type = str(signal_type or "").strip()
    if not normalized_signal_type:
        return None
    signal: HabitControlSignal = {"type": normalized_signal_type}
    if normalized_signal_type == ACTION_IMPULSE_SIGNAL_TYPE and signal_payload:
        action_type = str(signal_payload.get("action_type") or "").strip()
        if action_type:
            signal["action_type"] = action_type
    return signal


def _habit_context_text(
    goals: list[str],
    note_text: str,
    stimuli: list[Stimulus],
    emotion_summary: str,
) -> str:
    parts: list[str] = []
    parts.extend(goal.strip() for goal in goals if goal.strip())
    if note_text.strip():
        parts.append(" ".join(note_text.split()))
    if emotion_summary.strip():
        parts.append(emotion_summary.strip())
    for stimulus in stimuli:
        parts.append(_habit_stimulus_text(stimulus))
    return " ".join(part for part in parts if part).strip()


def _habit_stimulus_text(stimulus: Stimulus) -> str:
    content = " ".join(stimulus.content.split()).strip()
    if not content:
        return stimulus.type
    return f"{stimulus.type} {content[:120]}"


def _habit_entry_score(entry: HabitPromptEntry, context_text: str) -> float:
    relevance = float(entry.get("activation_score") or 0.0)
    if relevance <= 0.0:
        relevance = _habit_context_similarity(entry["pattern"], context_text)
    manifested_bonus = 0.18 if entry.get("manifested") else 0.0
    return round(float(entry["strength"]) * 0.30 + relevance * 0.52 + manifested_bonus * 0.18, 6)


def _habit_context_similarity(pattern: str, context_text: str) -> float:
    normalized_pattern = _normalize_habit_text(pattern)
    normalized_context = _normalize_habit_text(context_text)
    if not normalized_pattern or not normalized_context:
        return 0.0
    return bigram_similarity(normalized_pattern, normalized_context)


def _habit_recency_bonus(last_activated: datetime | None) -> float:
    if last_activated is None:
        return 0.0
    tz = last_activated.tzinfo or datetime.now().astimezone().tzinfo
    age_days = max(0.0, (datetime.now(tz) - last_activated).total_seconds() / 86400.0)
    return max(0.0, 1.0 - min(age_days / 7.0, 1.0))


def _embed_habit_pattern(
    client: ModelClient | None,
    model: str,
    text: str,
) -> list[float] | None:
    compact = " ".join(text.split()).strip()
    if client is None or not model or not compact:
        return None
    try:
        return embed_text(client, compact, model)
    except MODEL_CLIENT_EXCEPTIONS:
        logger.warning("habit embedding unavailable for pattern/context")
        return None


def _jsonb_or_empty(value: JsonObject | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _coerce_signal_payload(value: object) -> JsonObject | None:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            return None
        if isinstance(payload, dict):
            return {str(key): item for key, item in payload.items()}
    return None


def _format_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vec) + "]"


def _coerce_vector(value: object) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(item, (int, float)) for item in value):
        return [float(item) for item in value]
    if isinstance(value, tuple) and all(isinstance(item, (int, float)) for item in value):
        return [float(item) for item in value]
    return None
