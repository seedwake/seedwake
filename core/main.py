"""Seedwake — thought-stream engine.

Usage: python -m core.main [--config config.yml] [--log data/test.txt]
"""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import psycopg
import redis as redis_lib
import yaml
from dotenv import load_dotenv

from core.cycle import create_client, run_cycle
from core.embedding import embed_text
from core.memory.identity import load_identity
from core.memory.long_term import LongTermMemory
from core.memory.short_term import ShortTermMemory
from core.thought_parser import Thought

# Terminal colors
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_TYPE = {
    "思考": "\033[36m",    # cyan
    "意图": "\033[33m",    # yellow
    "反应": "\033[32m",    # green
}


def main() -> None:
    load_dotenv()
    args = _parse_args()
    config = _load_config(args.config)
    log_file = _open_log(args.log)

    # Connections — each may be None (graceful degradation)
    ollama_client = create_client(
        os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        os.environ.get("OLLAMA_AUTH_HEADER", ""),
        os.environ.get("OLLAMA_AUTH_VALUE", ""),
    )
    redis_client = _connect_redis()
    pg_conn = _connect_pg()

    # Core config
    model_config = config["models"]["primary"]
    embedding_model = config["models"]["embedding"]["name"]
    context_window = config["short_term_memory"]["context_window_size"]
    buffer_size = config.get("short_term_memory", {}).get("buffer_size", 500)
    retrieval_top_k = config.get("long_term_memory", {}).get("retrieval_top_k", 5)
    runtime = config.get("runtime", {})
    retry_delay = float(runtime.get("error_retry_delay_seconds", 1.0))
    max_retry_delay = float(runtime.get("max_error_retry_delay_seconds", 10.0))
    reconnect_interval = 5.0
    bootstrap_identity = config["bootstrap"]["identity"]

    # Identity — from PostgreSQL if available, else from config bootstrap
    identity = load_identity(pg_conn, bootstrap_identity)

    # Memory stores
    stm = ShortTermMemory(redis_client, context_window, buffer_size)
    ltm = LongTermMemory(pg_conn, retrieval_top_k)

    _install_signal_handler(log_file)

    _output(log_file, "Seedwake v0.2 — 心相续引擎启动")
    _output(log_file, f"模型: {model_config['name']}  上下文窗口: {context_window} 轮")
    _output(log_file, f"Redis: {'已连接' if redis_client else '未连接（使用内存）'}")
    _output(log_file, f"PostgreSQL: {'已连接' if pg_conn else '未连接（跳过长期记忆）'}")
    _output(log_file, "─" * 60)

    cycle_id = 0
    current_retry_delay = retry_delay
    last_redis_reconnect = 0.0
    last_pg_reconnect = 0.0

    while True:
        cycle_id += 1
        now = time.monotonic()
        last_redis_reconnect = _maybe_reconnect_redis(
            log_file, stm, now, last_redis_reconnect, reconnect_interval,
        )
        identity, last_pg_reconnect = _maybe_reconnect_pg(
            log_file, ltm, identity, bootstrap_identity,
            now, last_pg_reconnect, reconnect_interval,
        )
        try:
            # Retrieve long-term associations via embedding
            ltm_context = _retrieve_associations(
                ltm, ollama_client, stm, embedding_model,
            )

            new_thoughts = run_cycle(
                ollama_client, cycle_id, identity,
                stm.get_context(), context_window, model_config,
                long_term_context=ltm_context,
            )
            stm.append(new_thoughts)
            _store_to_ltm(ltm, ollama_client, new_thoughts, embedding_model, cycle_id)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            _print_error(log_file, cycle_id, e, current_retry_delay)
            time.sleep(current_retry_delay)
            current_retry_delay = min(current_retry_delay * 2, max_retry_delay)
            continue

        _print_cycle(log_file, cycle_id, new_thoughts)
        current_retry_delay = retry_delay


# -- Long-term memory read/write ------------------------------------------

def _retrieve_associations(
    ltm: LongTermMemory,
    ollama_client,
    stm: ShortTermMemory,
    embedding_model: str,
) -> list[str] | None:
    """Embed the latest thought and retrieve related long-term memories.

    # TODO: SPECS §14.2 requires Embedding fallback to time-ordered retrieval.
    # Current implementation skips LTM entirely on embed failure. Deferred
    # because early-stage LTM data overlaps heavily with STM context window.
    """
    if not ltm.available:
        return None
    context = stm.get_context()
    if not context:
        return None
    anchor = context[-1]
    try:
        vec = embed_text(ollama_client, anchor.content, embedding_model)
    except Exception:
        return None
    try:
        entries = ltm.search(vec)
        if not entries:
            return None
        ltm.mark_accessed([e.id for e in entries])
        return [e.content for e in entries]
    except Exception:
        ltm.disconnect()
        return None


def _store_to_ltm(
    ltm: LongTermMemory,
    ollama_client,
    thoughts: list[Thought],
    embedding_model: str,
    cycle_id: int,
) -> None:
    """Embed and store new thoughts into long-term memory."""
    if not ltm.available:
        return
    for t in thoughts:
        try:
            vec = embed_text(ollama_client, t.content, embedding_model)
        except Exception:
            continue
        try:
            ltm.store(
                content=t.content,
                memory_type="episodic",
                embedding=vec,
                source_cycle_id=cycle_id,
            )
        except Exception:
            ltm.disconnect()
            return


def _maybe_reconnect_redis(
    log_file,
    stm: ShortTermMemory,
    now: float,
    last_attempt: float,
    interval: float,
) -> float:
    if stm.redis_available or now - last_attempt < interval:
        return last_attempt
    client = _connect_redis()
    if client and stm.attach_redis(client):
        _output(log_file, "Redis 已恢复")
    return now


def _maybe_reconnect_pg(
    log_file,
    ltm: LongTermMemory,
    identity: dict[str, str],
    bootstrap_identity: dict[str, str],
    now: float,
    last_attempt: float,
    interval: float,
) -> tuple[dict[str, str], float]:
    if ltm.available or now - last_attempt < interval:
        return identity, last_attempt
    conn = _connect_pg()
    if conn is None:
        return identity, now
    ltm.attach_connection(conn)
    _output(log_file, "PostgreSQL 已恢复")
    return load_identity(conn, bootstrap_identity), now


# -- Connections -----------------------------------------------------------

def _connect_redis():
    """Try to connect to Redis using env vars. Returns None on failure."""
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    try:
        client = redis_lib.Redis(host=host, port=port, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def _connect_pg():
    """Try to connect to PostgreSQL using env vars. Returns None on failure."""
    db_password = os.environ.get("DB_PASSWORD", "")
    if not db_password:
        return None
    try:
        conn = psycopg.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5432")),
            dbname=os.environ.get("DB_NAME", "seedwake"),
            user=os.environ.get("DB_USER", "seedwake"),
            password=db_password,
        )
        return conn
    except Exception:
        return None


# -- Terminal output -------------------------------------------------------

def _print_cycle(log_file, cycle_id: int, thoughts: list[Thought]) -> None:
    print(f"\n{C_DIM}── C{cycle_id} ──{C_RESET}")
    for t in thoughts:
        color = C_TYPE.get(t.type, "")
        trigger = f" {C_DIM}(← {t.trigger_ref}){C_RESET}" if t.trigger_ref else ""
        print(f"  {color}[{t.type}]{C_RESET} {t.content}{trigger}")

    if log_file:
        log_file.write(f"\n── C{cycle_id} ──\n")
        for t in thoughts:
            trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
            log_file.write(f"  [{t.type}] {t.content}{trigger}\n")
        log_file.flush()


def _output(log_file, text: str) -> None:
    print(text)
    if log_file:
        log_file.write(text + "\n")
        log_file.flush()


def _print_error(log_file, cycle_id: int, error: Exception, retry_delay: float) -> None:
    msg = f"── C{cycle_id} ERROR: {error} (retry in {retry_delay:.1f}s)"
    print(f"\n\033[31m{msg}\033[0m", file=sys.stderr)
    if log_file:
        log_file.write(f"\n{msg}\n")
        log_file.flush()


# -- Utilities -------------------------------------------------------------

def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        print(f"配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seedwake thought-stream engine")
    parser.add_argument("--config", default="config.yml", help="Path to config.yml")
    parser.add_argument("--log", default=None, help="Path to plain-text log file")
    return parser.parse_args()


def _open_log(path: str | None):
    if not path:
        return None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return open(path, "w", encoding="utf-8")


def _install_signal_handler(log_file) -> None:
    def handler(sig, frame):
        print(f"\n\n{C_DIM}心相续止息。{C_RESET}")
        if log_file:
            log_file.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


if __name__ == "__main__":
    main()
