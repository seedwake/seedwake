"""Habit seed persistence and activation."""

from dataclasses import dataclass
from datetime import datetime
import re
import time

import psycopg

from core.thought_parser import Thought, thought_action_requests
from core.types import HabitPromptEntry, elapsed_ms

logger_name = __name__
HABIT_MIN_PATTERN_LENGTH = 4
HABIT_TEXT_STOPWORDS = {"刚才", "现在", "继续", "已经", "不是", "只是", "一个", "没有"}


@dataclass(frozen=True)
class HabitSeed:
    id: int
    pattern: str
    category: str
    strength: float
    activation_count: int
    last_activated: datetime | None


class HabitMemory:
    def __init__(
        self,
        pg_conn: psycopg.Connection | None,
        *,
        max_active_in_prompt: int,
        decay_rate: float,
    ) -> None:
        self._conn = pg_conn
        self._max_active_in_prompt = max_active_in_prompt
        self._decay_rate = decay_rate

    @property
    def available(self) -> bool:
        return self._conn is not None

    def attach_connection(self, pg_conn: psycopg.Connection | None) -> None:
        self._conn = pg_conn

    def top_active(self) -> list[HabitPromptEntry]:
        conn = self._conn
        if conn is None:
            return []
        started_at = time.perf_counter()
        status = "failed"
        rows: list[tuple] = []
        try:
            with conn.cursor() as cur:
                # Fetch more than needed for category-diverse selection
                cur.execute(
                    """
                    SELECT id, pattern, category, strength
                    FROM habit_seeds
                    WHERE strength > 0.01
                    ORDER BY strength DESC, activation_count DESC, updated_at DESC
                    LIMIT %s
                    """,
                    (self._max_active_in_prompt * 3,),
                )
                rows = cur.fetchall()
            status = "ok"
        except psycopg.Error:
            conn.rollback()
            raise
        finally:
            import logging

            logging.getLogger(logger_name).info(
                "habit top_active finished in %.1f ms (status=%s, count=%d)",
                elapsed_ms(started_at),
                status,
                len(rows),
            )
        all_entries = [
            {
                "id": int(row[0]),
                "pattern": str(row[1]),
                "category": str(row[2] or "cognitive"),
                "strength": float(row[3]),
            }
            for row in rows
        ]
        return _diverse_selection(all_entries, self._max_active_in_prompt)

    def ensure_bootstrap_seeds(self, items: list[dict]) -> None:
        for item in items:
            pattern = str(item.get("pattern") or "").strip()
            if not pattern:
                continue
            category = str(item.get("category") or "cognitive").strip() or "cognitive"
            strength = float(item.get("strength") or 0.1)
            self._upsert_seed(pattern, category, strength)

    def activate_for_cycle(self) -> list[HabitPromptEntry]:
        """Return top active habits for prompt display.

        Does NOT update activation timestamps — display alone is not activation.
        Real strengthening happens only during light sleep (strengthen_from_sleep).
        """
        return self.top_active()

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

    def strengthen_from_sleep(self, thoughts: list[Thought]) -> list[HabitPromptEntry]:
        conn = self._conn
        if conn is None:
            return []
        extracted = _extract_habit_patterns(thoughts)
        if not extracted:
            return []
        created: list[HabitPromptEntry] = []
        for pattern, category, strength in extracted:
            habit_id = self._upsert_seed(pattern, category, strength)
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

    def _upsert_seed(self, pattern: str, category: str, strength: float) -> int | None:
        conn = self._conn
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, strength
                    FROM habit_seeds
                    WHERE pattern = %s
                    LIMIT 1
                    """,
                    (pattern,),
                )
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        """
                        UPDATE habit_seeds
                        SET category = %s,
                            strength = LEAST(1.0, GREATEST(strength, %s) + 0.05),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (category, strength, int(row[0])),
                    )
                    habit_id = int(row[0])
                else:
                    cur.execute(
                        """
                        INSERT INTO habit_seeds (pattern, category, strength)
                        VALUES (%s, %s, %s)
                        RETURNING id
                        """,
                        (pattern, category, strength),
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
