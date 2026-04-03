"""PostgreSQL + pgvector long-term memory store.

Handles semantic vector retrieval and memory lifecycle.
Gracefully degrades when PostgreSQL is unavailable.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
import time

import psycopg
from psycopg import sql

from core.common_types import JsonObject, coerce_json_object, elapsed_ms

logger = logging.getLogger(__name__)

type LongTermQueryParam = str | int | list[int] | list[str]


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
        emotion_context: JsonObject | None = None,
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
                    WHERE is_active = TRUE AND content = %s AND memory_type = %s
                    LIMIT 1
                    """,
                    (normalized_content, memory_type),
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
                             source_cycle_id, importance, emotion_context)
                        VALUES (%s, %s, %s::vector, %s, %s, %s, %s::jsonb)
                        RETURNING id
                        """,
                        (
                            normalized_content,
                            memory_type,
                            vec_literal,
                            tags,
                            source_cycle_id,
                            importance,
                            _jsonb_or_none(emotion_context),
                        ),
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

    def existing_contents(self, contents: list[str], memory_type: str) -> set[str]:
        """Return the subset of *contents* that already exist in active LTM for the given type."""
        if not self.available or not contents:
            return set()
        conn = self._conn
        if conn is None:
            return set()
        unique = list({c.strip() for c in contents if c.strip()})
        if not unique:
            return set()
        started_at = time.perf_counter()
        found: set[str] = set()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content
                    FROM long_term_memory
                    WHERE is_active = TRUE
                      AND memory_type = %s
                      AND content = ANY(%s)
                    """,
                    (memory_type, unique),
                )
                for row in cur.fetchall():
                    found.add(str(row[0]))
            return found
        except psycopg.Error:
            conn.rollback()
            raise
        finally:
            logger.info(
                "ltm existing_contents finished in %.1f ms (checked=%d, found=%d, type=%s)",
                elapsed_ms(started_at),
                len(unique),
                len(found),
                memory_type,
            )

    def search(
        self,
        query_embedding: list[float],
        top_k: int | None = None,
        entity_filter: str | None = None,
        exclude_cycle_ids: list[int] | None = None,
        memory_types: list[str] | None = None,
    ) -> list[LongTermEntry]:
        """Retrieve semantically similar memories."""
        if not self.available:
            return []
        conn = self._conn
        if conn is None:
            return []
        k = top_k or self._top_k
        vec_literal = _format_vector(query_embedding)
        filters, params = _query_filters(
            entity_filter=entity_filter,
            exclude_cycle_ids=exclude_cycle_ids,
            memory_types=memory_types,
        )
        params.insert(0, vec_literal)
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
                "ltm search finished in %.1f ms"
                " (status=%s, top_k=%d, results=%d,"
                " entity_filter=%s, types=%d, exclude_cycles=%d)",
                elapsed_ms(started_at),
                status,
                k,
                len(rows) if status == "ok" else 0,
                bool(entity_filter),
                len(memory_types or []),
                len(exclude_cycle_ids or []),
            )

        entries = [
            _entry_from_search_row(r)
            for r in rows
        ]
        for memory in entries:
            memory.weighted_score = _weighted_memory_score(
                memory.similarity,
                memory.importance,
                memory.created_at,
                self._time_decay_factor,
            )
        entries.sort(key=lambda memory: memory.weighted_score, reverse=True)
        return entries

    def recent_by_time(
        self,
        *,
        top_k: int | None = None,
        entity_filter: str | None = None,
        exclude_cycle_ids: list[int] | None = None,
        memory_types: list[str] | None = None,
    ) -> list[LongTermEntry]:
        """Retrieve most recent memories as fallback when semantic retrieval is unavailable."""
        if not self.available:
            return []
        conn = self._conn
        if conn is None:
            return []
        k = top_k or self._top_k
        filters, params = _query_filters(
            entity_filter=entity_filter,
            exclude_cycle_ids=exclude_cycle_ids,
            memory_types=memory_types,
        )
        params.append(k)
        query = sql.SQL("""
            SELECT id, content, memory_type, source_cycle_id,
                   importance, created_at, entity_tags,
                   emotion_context, access_count, last_accessed
            FROM long_term_memory
            WHERE {filters}
            ORDER BY created_at DESC
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
                "ltm recent_by_time finished in %.1f ms"
                " (status=%s, top_k=%d, results=%d,"
                " entity_filter=%s, types=%d, exclude_cycles=%d)",
                elapsed_ms(started_at),
                status,
                k,
                len(rows) if status == "ok" else 0,
                bool(entity_filter),
                len(memory_types or []),
                len(exclude_cycle_ids or []),
            )
        return [_entry_from_recent_row(row) for row in rows]

    def active_count(self) -> int:
        if not self.available:
            return 0
        conn = self._conn
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM long_term_memory
                    WHERE is_active = TRUE
                    """
                )
                row = cur.fetchone()
        except psycopg.Error:
            conn.rollback()
            raise
        count = _row_first_int(row)
        return int(count or 0)

    def purge_inactive_memories(self, *, older_than_days: int) -> int:
        if not self.available:
            return 0
        conn = self._conn
        if conn is None:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM long_term_memory
                    WHERE is_active = FALSE
                      AND updated_at < NOW() - (%s * INTERVAL '1 day')
                    """,
                    (max(1, older_than_days),),
                )
                affected = cur.rowcount
            conn.commit()
            return int(affected or 0)
        except psycopg.Error:
            conn.rollback()
            raise

    def run_deep_sleep_maintenance(self) -> int:
        if not self.available:
            return 0
        conn = self._conn
        if conn is None:
            return 0
        old_autocommit = conn.autocommit
        operations = 0
        try:
            if not old_autocommit:
                conn.commit()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("VACUUM ANALYZE long_term_memory")
                operations += 1
                cur.execute("REINDEX TABLE long_term_memory")
                operations += 1
            return operations
        finally:
            conn.autocommit = old_autocommit

    def upsert_impression(
        self,
        *,
        entity_tag: str,
        content: str,
        embedding: list[float],
        source_cycle_id: int | None,
        importance: float,
        emotion_context: JsonObject | None = None,
    ) -> int | None:
        if not self.available:
            return None
        conn = self._conn
        if conn is None:
            return None
        normalized_content = content.strip()
        normalized_tag = entity_tag.strip()
        if not normalized_content or not normalized_tag:
            return None
        vec_literal = _format_vector(embedding)
        try:
            entry_id: int | None = None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE long_term_memory
                    SET is_active = FALSE,
                        updated_at = NOW()
                    WHERE is_active = TRUE
                      AND memory_type = 'impression'
                      AND %s = ANY(entity_tags)
                      AND '_impression' = ANY(entity_tags)
                    """,
                    (normalized_tag,),
                )
                cur.execute(
                    """
                    INSERT INTO long_term_memory
                        (content, memory_type, embedding, entity_tags,
                         source_cycle_id, importance, emotion_context)
                    VALUES (%s, 'impression', %s::vector, %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        normalized_content,
                        vec_literal,
                        [normalized_tag, "_impression"],
                        source_cycle_id,
                        importance,
                        _jsonb_or_none(emotion_context),
                    ),
                )
                row = cur.fetchone()
                entry_id = _row_first_int(row)
            conn.commit()
            return entry_id
        except psycopg.Error:
            conn.rollback()
            raise

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
                    SELECT content, memory_type, ARRAY_AGG(id ORDER BY id) AS ids
                    FROM long_term_memory
                    WHERE is_active = TRUE
                    GROUP BY content, memory_type
                    HAVING COUNT(*) > 1
                    """
                )
                rows = cur.fetchall()
                for content, memory_type, ids in rows:
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
                    _ = (content, memory_type)
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


def _query_filters(
    *,
    entity_filter: str | None,
    exclude_cycle_ids: list[int] | None,
    memory_types: list[str] | None,
) -> tuple[list[sql.SQL], list[LongTermQueryParam]]:
    filters = [sql.SQL("is_active = TRUE")]
    params: list[LongTermQueryParam] = []
    if entity_filter:
        filters.append(sql.SQL("%s = ANY(entity_tags)"))
        params.append(entity_filter)
    if exclude_cycle_ids:
        filters.append(sql.SQL("(source_cycle_id IS NULL OR source_cycle_id <> ALL(%s))"))
        params.append(exclude_cycle_ids)
    if memory_types:
        filters.append(sql.SQL("memory_type = ANY(%s)"))
        params.append(memory_types)
    return filters, params


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


def _entry_from_search_row(row: tuple) -> LongTermEntry:
    return LongTermEntry(
        id=row[0],
        content=row[1],
        memory_type=row[2],
        source_cycle_id=row[3],
        importance=row[4],
        created_at=row[5],
        similarity=row[6],
        entity_tags=list(row[7] or []),
        emotion_context=coerce_json_object(row[8]),
        access_count=int(row[9] or 0),
        last_accessed=row[10],
    )


def _entry_from_recent_row(row: tuple) -> LongTermEntry:
    entry = LongTermEntry(
        id=row[0],
        content=row[1],
        memory_type=row[2],
        source_cycle_id=row[3],
        importance=row[4],
        created_at=row[5],
        similarity=0.0,
        entity_tags=list(row[6] or []),
        emotion_context=coerce_json_object(row[7]),
        access_count=int(row[8] or 0),
        last_accessed=row[9],
    )
    entry.weighted_score = entry.importance
    return entry


def _weighted_memory_score(
    similarity: float,
    importance: float,
    created_at: datetime,
    time_decay_factor: float,
) -> float:
    created = (
        created_at.astimezone(timezone.utc)
        if created_at.tzinfo is not None
        else created_at.replace(tzinfo=timezone.utc)
    )
    age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400.0)
    time_decay = time_decay_factor ** age_days
    return round(max(0.0, similarity) * max(0.0, importance) * time_decay, 6)


def _jsonb_or_none(value: JsonObject | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


TELEGRAM_TARGET_PATTERNS = (
    re.compile(r"telegram:(-?\d+)"),
    re.compile(r"telegram(?:\s+chat)?(?:\s+id|_id)?\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"telegram_chat_id\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"chat_id\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r'"telegram"\s*:\s*"(-?\d+)"', re.IGNORECASE),
    re.compile(r'"telegram_chat_id"\s*:\s*(-?\d+)', re.IGNORECASE),
)
