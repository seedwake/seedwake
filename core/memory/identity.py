"""Identity document management.

On first run, bootstraps identity from config.yml into PostgreSQL.
On subsequent runs, loads from PostgreSQL (which may have evolved).
Falls back to config bootstrap when PostgreSQL is unavailable or broken.
"""

import logging

import psycopg

logger = logging.getLogger(__name__)


def load_identity(pg_conn, bootstrap: dict[str, str]) -> dict[str, str]:
    """Load identity from PostgreSQL, bootstrap if empty, fallback to config.

    DB results override bootstrap per-section, but missing sections
    are always filled from bootstrap to guarantee completeness.
    """
    if pg_conn is None:
        return bootstrap

    db_identity = _read_from_db(pg_conn)
    if not db_identity:
        _bootstrap_to_db(pg_conn, bootstrap)
        return bootstrap

    merged = dict(bootstrap)
    merged.update(db_identity)
    return merged


def _read_from_db(conn) -> dict[str, str]:
    """Read all identity sections. Returns empty dict on any DB error."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT section, content FROM identity ORDER BY id")
            rows = cur.fetchall()
        return dict(rows) if rows else {}
    except psycopg.Error as exc:
        logger.warning("identity read failed, falling back to bootstrap: %s", exc)
        conn.rollback()
        return {}


def _bootstrap_to_db(conn, bootstrap: dict[str, str]) -> None:
    """Write initial identity from config into PostgreSQL. Silently skips on error."""
    try:
        with conn.cursor() as cur:
            for section, content in bootstrap.items():
                cur.execute(
                    """
                    INSERT INTO identity (section, content)
                    VALUES (%s, %s)
                    ON CONFLICT (section) DO NOTHING
                    """,
                    (section, content.strip()),
                )
        conn.commit()
    except psycopg.Error as exc:
        logger.warning("identity bootstrap write failed: %s", exc)
        conn.rollback()
