"""PostgreSQL + pgvector long-term memory store.

Handles semantic vector retrieval and memory lifecycle.
Gracefully degrades when PostgreSQL is unavailable.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
import time

import psycopg
from psycopg import sql

from core.types import JsonObject, coerce_json_object, elapsed_ms

logger = logging.getLogger(__name__)


@dataclass
class LongTermEntry:
    id: int
    content: str
    memory_type: str
    source_cycle_id: int | None
    importance: float
    created_at: datetime
    similarity: float = 0.0
    entity_tags: list[str] = field(default_factory=list)
    emotion_context: JsonObject | None = None
    access_count: int = 0
    last_accessed: datetime | None = None
    weighted_score: float = 0.0


class LongTermMemory:
    """Long-term memory backed by PostgreSQL + pgvector."""

    def __init__(
        self,
        pg_conn: psycopg.Connection | None,
        retrieval_top_k: int = 5,
        time_decay_factor: float = 0.95,
        importance_threshold: float = 0.1,
    ) -> None:
        self._conn = pg_conn
        self._top_k = retrieval_top_k
        self._time_decay_factor = time_decay_factor
        self._importance_threshold = importance_threshold

    @property
    def available(self) -> bool:
        return self._conn is not None

    @property
    def retrieval_top_k(self) -> int:
        return self._top_k

    @property
    def time_decay_factor(self) -> float:
        return self._time_decay_factor

    @property
    def importance_threshold(self) -> float:
        return self._importance_threshold

    def store(
        self,
        content: str,
        memory_type: str,
        embedding: list[float],
        source_cycle_id: int | None = None,
        entity_tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> int | None:
        """Write a memory entry. Returns the new row id."""
        if not self.available:
            return None
        normalized_content = content.strip()
        if not normalized_content:
            return None
        conn = self._conn
        if conn is None:
            return None
        tags = entity_tags or []
        vec_literal = _format_vector(embedding)
        started_at = time.perf_counter()
        status = "failed"
        duplicate_hit = False
        try:
            entry_id: int | None = None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM long_term_memory
                    WHERE is_active = TRUE AND content = %s
                    LIMIT 1
                    """,
                    (normalized_content,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    entry_id = _row_first_int(existing)
                    duplicate_hit = True
                else:
                    cur.execute(
                        """
                        INSERT INTO long_term_memory
                            (content, memory_type, embedding, entity_tags,
                             source_cycle_id, importance)
                        VALUES (%s, %s, %s::vector, %s, %s, %s)
                        RETURNING id
                        """,
                        (normalized_content, memory_type, vec_literal, tags,
                         source_cycle_id, importance),
                    )
                    row = cur.fetchone()
                    entry_id = _row_first_int(row)
            conn.commit()
            status = "ok"
            return entry_id
        except psycopg.Error:
            conn.rollback()
            raise
        finally:
            logger.info(
                "ltm store finished in %.1f ms (status=%s, duplicate=%s, chars=%d)",
                elapsed_ms(started_at),
                status,
                duplicate_hit,
                len(normalized_content),
            )

    def search(
        self,
        query_embedding: list[float],
        top_k: int | None = None,
        entity_filter: str | None = None,
        exclude_cycle_ids: list[int] | None = None,
    ) -> list[LongTermEntry]:
        """Retrieve semantically similar memories."""
        if not self.available:
            return []
        conn = self._conn
        if conn is None:
            return []
        k = top_k or self._top_k
        vec_literal = _format_vector(query_embedding)
        filters = [sql.SQL("is_active = TRUE")]
        params: list[str | int | list[int]] = [vec_literal]
        if entity_filter:
            filters.append(sql.SQL("%s = ANY(entity_tags)"))
            params.append(entity_filter)
        if exclude_cycle_ids:
            filters.append(sql.SQL("(source_cycle_id IS NULL OR source_cycle_id <> ALL(%s))"))
            params.append(exclude_cycle_ids)
        params.extend([vec_literal, k])
        query = sql.SQL("""
            SELECT id, content, memory_type, source_cycle_id,
                   importance, created_at,
                   1 - (embedding <=> %s::vector) AS similarity,
                   entity_tags, emotion_context, access_count, last_accessed
            FROM long_term_memory
            WHERE {filters}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """).format(filters=sql.SQL(" AND ").join(filters))

        started_at = time.perf_counter()
        status = "failed"
        rows: list[tuple] = []
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
            status = "ok"
        except psycopg.Error:
            conn.rollback()
            raise
        finally:
            logger.info(
                "ltm search finished in %.1f ms (status=%s, top_k=%d, results=%d, entity_filter=%s, exclude_cycles=%d)",
                elapsed_ms(started_at),
                status,
                k,
                len(rows) if status == "ok" else 0,
                bool(entity_filter),
                len(exclude_cycle_ids or []),
            )

        entries = [
            LongTermEntry(
                id=r[0],
                content=r[1],
                memory_type=r[2],
                source_cycle_id=r[3],
                importance=r[4],
                created_at=r[5],
                similarity=r[6],
                entity_tags=list(r[7] or []),
                emotion_context=coerce_json_object(r[8]),
                access_count=int(r[9] or 0),
                last_accessed=r[10],
            )
            for r in rows
        ]
        for entry in entries:
            entry.weighted_score = _weighted_memory_score(
                entry.similarity,
                entry.importance,
                entry.created_at,
                self._time_decay_factor,
            )
        entries.sort(key=lambda entry: entry.weighted_score, reverse=True)
        return entries

    def mark_accessed(self, memory_ids: list[int]) -> None:
        """Bump access_count and last_accessed for retrieved memories."""
        if not self.available or not memory_ids:
            return
        conn = self._conn
        if conn is None:
            return
        started_at = time.perf_counter()
        status = "failed"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE long_term_memory
                    SET access_count = access_count + 1,
                        last_accessed = NOW()
                    WHERE id = ANY(%s)
                    """,
                    (memory_ids,),
                )
            conn.commit()
            status = "ok"
        except psycopg.Error:
            conn.rollback()
            raise
        finally:
            logger.info(
                "ltm mark_accessed finished in %.1f ms (status=%s, count=%d)",
                elapsed_ms(started_at),
                status,
                len(memory_ids),
            )

    def resolve_telegram_target_for_entity(self, entity_tag: str) -> str | None:
        """Resolve a Telegram chat target from semantic/impression entity memories."""
        if not self.available or not entity_tag:
            return None
        conn = self._conn
        if conn is None:
            return None
        candidate_tags = _entity_tag_candidates(entity_tag)
        try:
            rows = []
            with conn.cursor() as cur:
                for candidate_tag in candidate_tags:
                    cur.execute(
                        """
                        SELECT content
                        FROM long_term_memory
                        WHERE is_active = TRUE
                          AND %s = ANY(entity_tags)
                          AND memory_type = ANY(%s)
                        ORDER BY created_at DESC
                        LIMIT 20
                        """,
                        (candidate_tag, ["semantic", "impression"]),
                    )
                    rows.extend(cur.fetchall())
        except psycopg.Error:
            conn.rollback()
            raise
        for row in rows:
            target = _extract_telegram_target(str(row[0] or ""))
            if target:
                return target
        return None

    def attach_connection(self, pg_conn: psycopg.Connection | None) -> None:
        self._conn = pg_conn

    def disconnect(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except (psycopg.Error, OSError):
            pass
        self._conn = None

    def cool_inactive_memories(self, cooling_rate: float = 0.03) -> int:
        if not self.available:
            return 0
        conn = self._conn
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE long_term_memory
                    SET importance = GREATEST(0.0, importance - %s),
                        updated_at = NOW()
                    WHERE is_active = TRUE
                      AND COALESCE(last_accessed, created_at) < NOW() - INTERVAL '7 days'
                    """,
                    (cooling_rate,),
                )
                affected = cur.rowcount
            conn.commit()
            return int(affected or 0)
        except psycopg.Error:
            conn.rollback()
            raise

    def merge_exact_duplicates(self) -> int:
        if not self.available:
            return 0
        conn = self._conn
        if conn is None:
            return 0
        try:
            merged = 0
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content, ARRAY_AGG(id ORDER BY id) AS ids
                    FROM long_term_memory
                    WHERE is_active = TRUE
                    GROUP BY content
                    HAVING COUNT(*) > 1
                    """
                )
                rows = cur.fetchall()
                for content, ids in rows:
                    if not isinstance(ids, list) or len(ids) < 2:
                        continue
                    keep_id = int(ids[0])
                    duplicate_ids = [int(item) for item in ids[1:]]
                    cur.execute(
                        """
                        UPDATE long_term_memory
                        SET importance = LEAST(
                                1.0,
                                importance + COALESCE(
                                    (SELECT SUM(importance) FROM long_term_memory WHERE id = ANY(%s)),
                                    0.0
                                ) * 0.5
                            ),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (duplicate_ids, keep_id),
                    )
                    cur.execute(
                        """
                        UPDATE long_term_memory
                        SET is_active = FALSE,
                            updated_at = NOW()
                        WHERE id = ANY(%s)
                        """,
                        (duplicate_ids,),
                    )
                    merged += len(duplicate_ids)
                    _ = content
            conn.commit()
            return merged
        except psycopg.Error:
            conn.rollback()
            raise

    def prune_low_importance(self) -> int:
        if not self.available:
            return 0
        conn = self._conn
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE long_term_memory
                    SET is_active = FALSE,
                        updated_at = NOW()
                    WHERE is_active = TRUE
                      AND importance < %s
                      AND created_at < NOW() - INTERVAL '14 days'
                    """,
                    (self._importance_threshold,),
                )
                affected = cur.rowcount
            conn.commit()
            return int(affected or 0)
        except psycopg.Error:
            conn.rollback()
            raise


def _format_vector(vec: list[float]) -> str:
    """Format a Python list as a pgvector literal '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def _extract_telegram_target(content: str) -> str | None:
    for pattern in TELEGRAM_TARGET_PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        chat_id = match.group(1).strip()
        if chat_id:
            return f"telegram:{chat_id}"
    return None


def _entity_tag_candidates(entity_tag: str) -> list[str]:
    normalized = entity_tag.strip()
    if not normalized:
        return []
    if normalized.startswith("entity:"):
        legacy = normalized
        canonical = normalized.removeprefix("entity:")
        return [canonical, legacy]
    return [normalized, f"entity:{normalized}"]


def _row_first_int(row: tuple[int | None, ...] | list[int | None] | None) -> int | None:
    if not row:
        return None
    value = row[0]
    return value if isinstance(value, int) else None


def _weighted_memory_score(
    similarity: float,
    importance: float,
    created_at: datetime,
    time_decay_factor: float,
) -> float:
    created = created_at.astimezone(timezone.utc) if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400.0)
    time_decay = time_decay_factor ** age_days
    return round(max(0.0, similarity) * max(0.0, importance) * time_decay, 6)


TELEGRAM_TARGET_PATTERNS = (
    re.compile(r"telegram:(-?\d+)"),
    re.compile(r"telegram(?:\s+chat)?(?:\s+id|_id)?\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"telegram_chat_id\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"chat_id\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r'"telegram"\s*:\s*"(-?\d+)"', re.IGNORECASE),
    re.compile(r'"telegram_chat_id"\s*:\s*(-?\d+)', re.IGNORECASE),
)
