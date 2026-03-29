"""PostgreSQL + pgvector long-term memory store.

Handles semantic vector retrieval and memory lifecycle.
Gracefully degrades when PostgreSQL is unavailable.
"""

from dataclasses import dataclass
from datetime import datetime
import re

import psycopg


@dataclass
class LongTermEntry:
    id: int
    content: str
    memory_type: str
    source_cycle_id: int | None
    importance: float
    created_at: datetime
    similarity: float = 0.0


class LongTermMemory:
    """Long-term memory backed by PostgreSQL + pgvector."""

    def __init__(self, pg_conn, retrieval_top_k: int = 5):
        self._conn = pg_conn
        self._top_k = retrieval_top_k

    @property
    def available(self) -> bool:
        return self._conn is not None

    @property
    def retrieval_top_k(self) -> int:
        return self._top_k

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
        tags = entity_tags or []
        vec_literal = _format_vector(embedding)
        try:
            entry_id: int | None = None
            with self._conn.cursor() as cur:
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
                if existing:
                    entry_id = existing[0]
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
                    entry_id = row[0] if row else None
            self._conn.commit()
            return entry_id
        except psycopg.Error:
            self._conn.rollback()
            raise

    # NOTE: SPECS §4.2 requires ranking by similarity × importance × time_decay.
    # Current implementation uses pure vector distance. Weighted sorting deferred
    # until Phase 4 (sleep mechanism) makes importance/time_decay meaningful.
    def search(
        self,
        query_embedding: list[float],
        top_k: int | None = None,
        entity_filter: str | None = None,
    ) -> list[LongTermEntry]:
        """Retrieve semantically similar memories."""
        if not self.available:
            return []
        k = top_k or self._top_k
        vec_literal = _format_vector(query_embedding)

        if entity_filter:
            query = """
                SELECT id, content, memory_type, source_cycle_id,
                       importance, created_at,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM long_term_memory
                WHERE is_active = TRUE AND %s = ANY(entity_tags)
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (vec_literal, entity_filter, vec_literal, k)
        else:
            query = """
                SELECT id, content, memory_type, source_cycle_id,
                       importance, created_at,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM long_term_memory
                WHERE is_active = TRUE
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (vec_literal, vec_literal, k)

        try:
            with self._conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        except psycopg.Error:
            self._conn.rollback()
            raise

        return [
            LongTermEntry(
                id=r[0], content=r[1], memory_type=r[2],
                source_cycle_id=r[3], importance=r[4],
                created_at=r[5], similarity=r[6],
            )
            for r in rows
        ]

    def mark_accessed(self, memory_ids: list[int]) -> None:
        """Bump access_count and last_accessed for retrieved memories."""
        if not self.available or not memory_ids:
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE long_term_memory
                    SET access_count = access_count + 1,
                        last_accessed = NOW()
                    WHERE id = ANY(%s)
                    """,
                    (memory_ids,),
                )
            self._conn.commit()
        except psycopg.Error:
            self._conn.rollback()
            raise

    def resolve_telegram_target_for_entity(self, entity_tag: str) -> str | None:
        """Resolve a Telegram chat target from semantic/impression entity memories."""
        if not self.available or not entity_tag:
            return None
        candidate_tags = _entity_tag_candidates(entity_tag)
        try:
            rows = []
            with self._conn.cursor() as cur:
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
            self._conn.rollback()
            raise
        for row in rows:
            target = _extract_telegram_target(str(row[0] or ""))
            if target:
                return target
        return None

    def attach_connection(self, pg_conn) -> None:
        self._conn = pg_conn

    def disconnect(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except (psycopg.Error, OSError):
            pass
        self._conn = None


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


TELEGRAM_TARGET_PATTERNS = (
    re.compile(r"telegram:(-?\d+)"),
    re.compile(r"telegram(?:\s+chat)?(?:\s+id|_id)?\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"telegram_chat_id\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"chat_id\s*[:=]\s*(-?\d+)", re.IGNORECASE),
    re.compile(r'"telegram"\s*:\s*"(-?\d+)"', re.IGNORECASE),
    re.compile(r'"telegram_chat_id"\s*:\s*(-?\d+)', re.IGNORECASE),
)
