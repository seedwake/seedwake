"""Habit seed persistence and activation."""

from dataclasses import dataclass
from datetime import datetime
import logging
import re
import time

import psycopg

from core.embedding import embed_text
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient
from core.stimulus import Stimulus
from core.thought_parser import Thought, thought_action_requests
from core.types import HabitPromptEntry, elapsed_ms

logger = logging.getLogger(__name__)
HABIT_MIN_PATTERN_LENGTH = 4
HABIT_TEXT_STOPWORDS = {"刚才", "现在", "继续", "已经", "不是", "只是", "一个", "没有"}
HABIT_EMBEDDING_DIMENSIONS = 4096


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
                    f"""
                    ALTER TABLE habit_seeds
                    ADD COLUMN IF NOT EXISTS embedding vector({HABIT_EMBEDDING_DIMENSIONS})
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
                    ALTER COLUMN category SET NOT NULL
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
                        SELECT id, pattern, category, strength, activation_count, last_activated, embedding
                        FROM habit_seeds
                        WHERE strength > 0.01
                        ORDER BY updated_at DESC, activation_count DESC, strength DESC
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, pattern, category, strength, activation_count, last_activated, embedding
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
        for pattern, category, strength in extracted:
            embedding = _embed_habit_pattern(embedding_client, embedding_model, pattern)
            habit_id = self._upsert_seed(pattern, category, strength, embedding=embedding)
            if habit_id is None:
                continue
            created.append(
                {
                    "id": habit_id,
                    "pattern": pattern,
                    "category": category,
                    "strength": strength,
                }
            )
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
                semantic_similarity=float(row[7] or 0.0),
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
    ) -> int | None:
        conn = self._conn
        if conn is None:
            return None
        vec_literal = _format_vector(embedding) if embedding is not None else None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO habit_seeds (pattern, category, strength, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (pattern, category)
                    DO UPDATE SET
                        strength = LEAST(1.0, GREATEST(habit_seeds.strength, EXCLUDED.strength) + 0.05),
                        embedding = COALESCE(habit_seeds.embedding, EXCLUDED.embedding),
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (pattern, category, strength, vec_literal),
                )
                inserted = cur.fetchone()
                habit_id = int(inserted[0]) if inserted is not None else None
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
    ) -> int | None:
        conn = self._conn
        if conn is None:
            return None
        vec_literal = _format_vector(embedding) if embedding is not None else None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO habit_seeds (pattern, category, strength, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (pattern, category)
                    DO UPDATE SET
                        embedding = COALESCE(habit_seeds.embedding, EXCLUDED.embedding),
                        updated_at = CASE
                            WHEN habit_seeds.embedding IS NULL AND EXCLUDED.embedding IS NOT NULL
                                THEN NOW()
                            ELSE habit_seeds.updated_at
                        END
                    RETURNING id
                    """,
                    (pattern, category, strength, vec_literal),
                )
                inserted = cur.fetchone()
                habit_id = int(inserted[0]) if inserted is not None else None
            conn.commit()
            return habit_id
        except psycopg.Error:
            conn.rollback()
            raise


def _extract_habit_patterns(thoughts: list[Thought]) -> list[tuple[str, str, float]]:
    patterns: dict[tuple[str, str], tuple[int, float]] = {}
    for thought in thoughts:
        for pattern, category in _habit_patterns_from_thought(thought):
            key = (pattern, category)
            count, strength = patterns.get(key, (0, 0.18))
            patterns[key] = (count + 1, min(0.95, strength + 0.08))
    return [
        (pattern, category, strength)
        for (pattern, category), (count, strength) in patterns.items()
        if count >= 2 or strength >= 0.34
    ]


def _activation_patterns_from_thoughts(thoughts: list[Thought]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for thought in thoughts:
        for pattern, category in _habit_patterns_from_thought(thought):
            key = (pattern, category)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return ordered


def _habit_patterns_from_thought(thought: Thought) -> list[tuple[str, str]]:
    action_requests = thought_action_requests(thought)
    if action_requests:
        seen_action_types: set[str] = set()
        patterns: list[tuple[str, str]] = []
        for action_request in action_requests:
            action_type = str(action_request.get("type") or "").strip()
            if not action_type or action_type in seen_action_types:
                continue
            seen_action_types.add(action_type)
            patterns.append((f"经常会冒出 {action_type} 行动冲动", "behavioral"))
        if patterns:
            return patterns
    normalized = _normalize_habit_text(thought.content)
    if len(normalized) < HABIT_MIN_PATTERN_LENGTH:
        return []
    clauses = re.split(r"[，。！？；,.!?;]+", normalized)
    for clause in clauses:
        compact = clause.strip()
        if len(compact) < HABIT_MIN_PATTERN_LENGTH:
            continue
        if compact in HABIT_TEXT_STOPWORDS:
            continue
        return [(compact[:48], _habit_category(thought))]
    return []


def _normalize_habit_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())


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
    if seed.semantic_similarity > 0.0:
        entry["activation_score"] = round(seed.semantic_similarity, 4)
        entry["manifested"] = seed.semantic_similarity >= threshold
    return entry


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
    return _text_similarity(normalized_pattern, normalized_context)


def _habit_recency_bonus(last_activated: datetime | None) -> float:
    if last_activated is None:
        return 0.0
    tz = last_activated.tzinfo or datetime.now().astimezone().tzinfo
    age_days = max(0.0, (datetime.now(tz) - last_activated).total_seconds() / 86400.0)
    return max(0.0, 1.0 - min(age_days / 7.0, 1.0))


def _text_similarity(a: str, b: str) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    grams_a = {a[index:index + 2] for index in range(len(a) - 1)}
    grams_b = {b[index:index + 2] for index in range(len(b) - 1)}
    union = len(grams_a | grams_b)
    if union == 0:
        return 0.0
    return len(grams_a & grams_b) / union


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
