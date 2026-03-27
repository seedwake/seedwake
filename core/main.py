"""Seedwake — thought-stream engine.

Usage: python -m core.main [--config config.yml] [--log data/test.txt]
"""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import psycopg
import redis as redis_lib
import yaml
from dotenv import load_dotenv

from core.action import create_action_manager, pop_action_controls
from core.cycle import create_client, run_cycle
from core.embedding import embed_text
from core.memory.identity import load_identity
from core.memory.long_term import LongTermMemory
from core.memory.short_term import ShortTermMemory
from core.perception import PerceptionManager
from core.stimulus import Stimulus, StimulusQueue, append_conversation_history
from core.thought_parser import Thought

# Terminal colors
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_TYPE = {
    "思考": "\033[36m",    # cyan
    "意图": "\033[33m",    # yellow
    "反应": "\033[32m",    # green
}
EVENT_CHANNEL = "seedwake:events"


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
    stimulus_queue = StimulusQueue(redis_client)
    perception_config = dict(config.get("perception") or {})
    if not perception_config.get("default_weather_location"):
        perception_config["default_weather_location"] = str(
            (config.get("actions") or {}).get("default_weather_location", "")
        ).strip()
    perception = PerceptionManager.from_config(perception_config)
    action_manager = create_action_manager(
        redis_client,
        stimulus_queue,
        ollama_client,
        model_config,
        config.get("actions", {}),
        news_feed_urls=list((config.get("perception", {}) or {}).get("news_feed_urls", [])),
        news_seen_ttl_hours=int((config.get("perception", {}) or {}).get("news_seen_ttl_hours", 720)),
        news_seen_max_items=int((config.get("perception", {}) or {}).get("news_seen_max_items", 5000)),
        log_callback=lambda text: _output(log_file, text),
        event_callback=lambda event_type, payload: _publish_event(stm.redis_client, event_type, payload),
    )

    _install_signal_handler(log_file, action_manager)

    _output(log_file, "Seedwake v0.2 — 心相续引擎启动")
    _output(log_file, f"模型: {model_config['name']}  上下文窗口: {context_window} 轮")
    _output(log_file, f"Redis: {'已连接' if redis_client else '未连接（使用内存）'}")
    _output(log_file, f"PostgreSQL: {'已连接' if pg_conn else '未连接（跳过长期记忆）'}")
    _output(log_file, "─" * 60)
    _publish_event(redis_client, "status", {"message": "core_started"})

    cycle_id = 0
    current_retry_delay = retry_delay
    last_redis_reconnect = 0.0
    last_pg_reconnect = 0.0

    while True:
        cycle_id += 1
        now = time.monotonic()
        had_redis = stm.redis_available
        had_pg = ltm.available
        last_redis_reconnect = _maybe_reconnect_redis(
            log_file, stm, now, last_redis_reconnect, reconnect_interval,
        )
        if stm.redis_available and stm.redis_client is not None and (
            not had_redis
            or not stimulus_queue.redis_available
            or not action_manager.redis_available
        ):
            stimulus_queue.attach_redis(stm.redis_client)
            action_manager.attach_redis(stm.redis_client)
            _publish_event(stm.redis_client, "status", {"message": "redis_recovered"})
        identity, last_pg_reconnect = _maybe_reconnect_pg(
            log_file, ltm, identity, bootstrap_identity,
            now, last_pg_reconnect, reconnect_interval,
        )
        if not had_pg and ltm.available:
            _publish_event(stm.redis_client, "status", {"message": "postgres_recovered"})

        _push_passive_stimuli(stimulus_queue, perception.collect_passive_stimuli(cycle_id))
        controls = pop_action_controls(stm.redis_client)
        action_manager.apply_controls(controls)
        stimuli = stimulus_queue.pop_many(limit=2)
        perception.observe_stimuli(cycle_id, stimuli)
        perception.observe_types(cycle_id, action_manager.pop_perception_observations())
        running_actions = action_manager.running_actions()
        perception_cues = perception.build_prompt_cues(cycle_id, running_actions)
        try:
            # Retrieve long-term associations via embedding
            ltm_context = _retrieve_associations(
                ltm, ollama_client, stm, embedding_model,
            )

            new_thoughts = run_cycle(
                ollama_client, cycle_id, identity,
                stm.get_context(), context_window, model_config,
                long_term_context=ltm_context,
                stimuli=stimuli,
                running_actions=running_actions,
                perception_cues=perception_cues,
            )
            stm.append(new_thoughts)
            _store_to_ltm(ltm, ollama_client, new_thoughts, embedding_model, cycle_id)
            action_manager.submit_from_thoughts(new_thoughts)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            stimulus_queue.requeue_front(stimuli)
            _print_error(log_file, cycle_id, e, current_retry_delay)
            time.sleep(current_retry_delay)
            current_retry_delay = min(current_retry_delay * 2, max_retry_delay)
            continue

        _print_stimuli(log_file, stimuli)
        _print_cycle(log_file, cycle_id, new_thoughts)
        _publish_reply_event(stm.redis_client, stimuli, new_thoughts)
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
        client = redis_lib.Redis(
            host=host,
            port=port,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
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


def _print_stimuli(log_file, stimuli: list[Stimulus]) -> None:
    if not stimuli:
        return

    print(f"{C_DIM}刺激{C_RESET}")
    for stimulus in stimuli:
        print(f"  {C_DIM}[{stimulus.type}]{C_RESET} {stimulus.content}")

    if log_file:
        log_file.write("刺激\n")
        for stimulus in stimuli:
            log_file.write(f"  [{stimulus.type}] {stimulus.content}\n")
        log_file.flush()


def _publish_reply_event(redis_client, stimuli: list[Stimulus], thoughts: list[Thought]) -> None:
    conversation = next((stimulus for stimulus in stimuli if stimulus.type == "conversation"), None)
    if conversation is None:
        return
    reply = _select_reply_text(thoughts)
    if not reply:
        return
    append_conversation_history(
        redis_client,
        role="assistant",
        source=conversation.source,
        content=reply,
        stimulus_id=conversation.stimulus_id,
    )
    _publish_event(redis_client, "reply", {
        "source": conversation.source,
        "message": reply,
        "stimulus_id": conversation.stimulus_id,
    })


def _push_passive_stimuli(stimulus_queue: StimulusQueue, stimuli: list[dict[str, object]]) -> None:
    for stimulus in stimuli:
        stimulus_queue.push(
            str(stimulus["type"]),
            int(stimulus["priority"]),
            str(stimulus["source"]),
            str(stimulus["content"]),
            metadata=dict(stimulus.get("metadata") or {}),
        )


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


def _publish_event(redis_client, event_type: str, payload: dict[str, object]) -> None:
    if redis_client is None:
        return
    try:
        redis_client.publish(EVENT_CHANNEL, json.dumps({
            "type": event_type,
            "payload": payload,
        }, ensure_ascii=False))
    except Exception:
        return


def _select_reply_text(thoughts: list[Thought]) -> str | None:
    reactive = next((thought for thought in thoughts if thought.type == "反应"), None)
    if reactive:
        return reactive.content
    if thoughts:
        return thoughts[0].content
    return None


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


def _install_signal_handler(log_file, action_manager) -> None:
    def handler(sig, frame):
        print(f"\n\n{C_DIM}心相续止息。{C_RESET}")
        action_manager.shutdown()
        if log_file:
            log_file.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


if __name__ == "__main__":
    main()
