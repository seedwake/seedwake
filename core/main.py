"""Seedwake — thought-stream engine.

Usage: python -m core.main [--config config.yml] [--log data/test.txt]
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
import redis as redis_lib
from dotenv import load_dotenv

from core.action import ActionManager, ActionRecord, create_action_manager, pop_action_controls
from core.cycle import run_cycle
from core.embedding import embed_text
from core.logging import resolve_log_path, setup_logging
from core.memory.identity import load_identity
from core.memory.long_term import LongTermMemory
from core.memory.short_term import LATEST_CYCLE_KEY, ShortTermMemory
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient, create_model_client
from core.perception import PerceptionManager
from core.runtime import connect_redis_from_env, load_yaml_config
from core.stimulus import Stimulus, StimulusQueue
from core.thought_parser import Thought
from core.types import EventPayload, PerceptionStimulusPayload, StatusEventPayload

# Terminal colors
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_TYPE = {
    "思考": "\033[36m",    # cyan
    "意图": "\033[33m",    # yellow
    "反应": "\033[32m",    # green
}
EVENT_CHANNEL = "seedwake:events"
MAIN_LOOP_EXCEPTIONS = (
    *MODEL_CLIENT_EXCEPTIONS,
    redis_lib.RedisError,
    psycopg.Error,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)
EMBEDDING_EXCEPTIONS = (
    *MODEL_CLIENT_EXCEPTIONS,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
)
LTM_EXCEPTIONS = (
    psycopg.Error,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
)
PG_CONNECT_EXCEPTIONS = (
    psycopg.Error,
    OSError,
    ValueError,
)
REDIS_EVENT_EXCEPTIONS = (
    redis_lib.RedisError,
    TypeError,
    ValueError,
)
CYCLE_COUNTER_EXCEPTIONS = (
    redis_lib.RedisError,
    TypeError,
    ValueError,
    OSError,
)
CYCLE_COUNTER_KEY = "seedwake:cycle_counter"
CONVERSATION_MERGE_SEPARATOR = " / "
logger = logging.getLogger(__name__)


@dataclass
class EngineRuntime:
    primary_client: ModelClient
    embedding_client: ModelClient
    stm: ShortTermMemory
    ltm: LongTermMemory
    stimulus_queue: StimulusQueue
    perception: PerceptionManager
    action_manager: ActionManager
    model_config: dict
    context_window: int
    embedding_model: str
    retry_delay: float
    max_retry_delay: float
    reconnect_interval: float
    bootstrap_identity: dict[str, str]


def main() -> None:
    load_dotenv()
    args = _parse_args()
    config = _load_config(args.config)
    setup_logging(config, component="core")
    log_file = _open_log(args.log, config)
    prompt_log_file = _open_prompt_log(config, plain_log_path=args.log)

    primary_client, embedding_client, redis_client, pg_conn = _create_connections(config)
    runtime, identity = _build_runtime_components(
        config,
        log_file,
        primary_client,
        embedding_client,
        redis_client,
        pg_conn,
    )

    _install_signal_handler(log_file, prompt_log_file, runtime.action_manager)
    _emit_startup(log_file, runtime.model_config, runtime.context_window,
                  redis_client, pg_conn)
    _run_engine_loop(log_file, prompt_log_file, runtime, identity)


def _create_connections(config: dict):
    models_config = config.get("models", {})
    primary_client = create_model_client(dict(models_config.get("primary") or {}))
    embedding_client = create_model_client(dict(models_config.get("embedding") or {}))
    return primary_client, embedding_client, _connect_redis(), _connect_pg()


def _build_runtime_components(
    config: dict,
    log_file,
    primary_client: ModelClient,
    embedding_client: ModelClient,
    redis_client,
    pg_conn,
) -> tuple[EngineRuntime, dict[str, str]]:
    model_config = config["models"]["primary"]
    embedding_model = config["models"]["embedding"]["name"]
    retry_delay, max_retry_delay, reconnect_interval = _runtime_retry_settings(config)
    bootstrap_identity = config["bootstrap"]["identity"]
    context_window = config["short_term_memory"]["context_window_size"]
    buffer_size = config.get("short_term_memory", {}).get("buffer_size", 500)
    retrieval_top_k = config.get("long_term_memory", {}).get("retrieval_top_k", 5)

    identity = load_identity(pg_conn, bootstrap_identity)
    stm = ShortTermMemory(redis_client, context_window, buffer_size)
    ltm = LongTermMemory(pg_conn, retrieval_top_k)
    stimulus_queue = StimulusQueue(redis_client)
    perception = PerceptionManager.from_config(_perception_config(config))
    action_manager = create_action_manager(
        redis_client,
        stimulus_queue,
        primary_client,
        model_config,
        config.get("actions", {}),
        contact_resolver=ltm.resolve_telegram_target_for_entity,
        news_feed_urls=list((config.get("perception", {}) or {}).get("news_feed_urls", [])),
        news_seen_ttl_hours=int((config.get("perception", {}) or {}).get("news_seen_ttl_hours", 720)),
        news_seen_max_items=int((config.get("perception", {}) or {}).get("news_seen_max_items", 5000)),
        log_callback=lambda text: _output(log_file, text),
        event_callback=lambda event_type, payload: _publish_event(stm.redis_client, event_type, payload),
    )
    runtime = EngineRuntime(
        primary_client=primary_client,
        embedding_client=embedding_client,
        stm=stm,
        ltm=ltm,
        stimulus_queue=stimulus_queue,
        perception=perception,
        action_manager=action_manager,
        model_config=model_config,
        context_window=context_window,
        embedding_model=embedding_model,
        retry_delay=retry_delay,
        max_retry_delay=max_retry_delay,
        reconnect_interval=reconnect_interval,
        bootstrap_identity=bootstrap_identity,
    )
    return runtime, identity


def _runtime_retry_settings(config: dict) -> tuple[float, float, float]:
    runtime = config.get("runtime", {})
    retry_delay = float(runtime.get("error_retry_delay_seconds", 1.0))
    max_retry_delay = float(runtime.get("max_error_retry_delay_seconds", 10.0))
    reconnect_interval = 5.0
    return retry_delay, max_retry_delay, reconnect_interval


def _perception_config(config: dict) -> dict:
    perception_config = dict(config.get("perception") or {})
    if not perception_config.get("default_weather_location"):
        perception_config["default_weather_location"] = str(
            (config.get("actions") or {}).get("default_weather_location", "")
        ).strip()
    return perception_config


def _emit_startup(log_file, model_config: dict, context_window: int, redis_client, pg_conn) -> None:
    model_name = str(model_config.get("name") or "")
    provider = str(model_config.get("provider") or "ollama")
    _output(log_file, "Seedwake v0.2 — 心相续引擎启动")
    _output(log_file, f"模型: {model_name} [{provider}]  上下文窗口: {context_window} 轮")
    _output(log_file, f"Redis: {'已连接' if redis_client else '未连接（使用内存）'}")
    _output(log_file, f"PostgreSQL: {'已连接' if pg_conn else '未连接（跳过长期记忆）'}")
    _output(log_file, "─" * 60)
    _publish_event(redis_client, "status", _status_payload("core_started"))


def _run_engine_loop(log_file, prompt_log_file, runtime: EngineRuntime, identity: dict[str, str]) -> None:
    cycle_id = 0
    current_retry_delay = runtime.retry_delay
    last_redis_reconnect = 0.0
    last_pg_reconnect = 0.0

    while True:
        cycle_id = _next_cycle_id(runtime.stm, cycle_id)
        (
            identity,
            last_redis_reconnect,
            last_pg_reconnect,
            stimuli,
            running_actions,
            perception_cues,
        ) = _prepare_cycle(
            log_file,
            cycle_id,
            runtime,
            identity,
            runtime.bootstrap_identity,
            runtime.reconnect_interval,
            last_redis_reconnect,
            last_pg_reconnect,
        )
        try:
            new_thoughts = _execute_cycle(
                runtime,
                cycle_id,
                identity,
                stimuli,
                running_actions,
                perception_cues,
                prompt_log_file,
            )
        except KeyboardInterrupt:
            raise
        except MAIN_LOOP_EXCEPTIONS as exc:
            current_retry_delay = _handle_cycle_failure(
                log_file,
                cycle_id,
                stimuli,
                runtime.stimulus_queue,
                exc,
                current_retry_delay,
                runtime.max_retry_delay,
            )
            continue
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("unexpected main loop failure at cycle %s: %s", cycle_id, exc)
            current_retry_delay = _handle_cycle_failure(
                log_file,
                cycle_id,
                stimuli,
                runtime.stimulus_queue,
                exc,
                current_retry_delay,
                runtime.max_retry_delay,
            )
            continue

        _finish_cycle(log_file, cycle_id, stimuli, new_thoughts)
        current_retry_delay = runtime.retry_delay


def _prepare_cycle(
    log_file,
    cycle_id: int,
    runtime: EngineRuntime,
    identity: dict[str, str],
    bootstrap_identity: dict[str, str],
    reconnect_interval: float,
    last_redis_reconnect: float,
    last_pg_reconnect: float,
) -> tuple[dict[str, str], float, float, list[Stimulus], list[ActionRecord], list[str]]:
    now = time.monotonic()
    identity, last_redis_reconnect, last_pg_reconnect = _recover_runtime_services(
        log_file,
        runtime,
        identity,
        bootstrap_identity,
        now,
        reconnect_interval,
        last_redis_reconnect,
        last_pg_reconnect,
    )
    _push_passive_stimuli(runtime.stimulus_queue, runtime.perception.collect_passive_stimuli(cycle_id))
    controls = pop_action_controls(runtime.stm.redis_client)
    runtime.action_manager.apply_controls(controls)
    runtime.action_manager.retry_deferred_actions()
    stimuli = _select_cycle_stimuli(runtime.stimulus_queue)
    runtime.perception.observe_stimuli(cycle_id, stimuli)
    runtime.perception.observe_types(cycle_id, runtime.action_manager.pop_perception_observations())
    running_actions = runtime.action_manager.running_actions()
    perception_cues = runtime.perception.build_prompt_cues(cycle_id, running_actions)
    return identity, last_redis_reconnect, last_pg_reconnect, stimuli, running_actions, perception_cues


def _select_cycle_stimuli(stimulus_queue: StimulusQueue) -> list[Stimulus]:
    ranked_stimuli = stimulus_queue.pop_all()
    if len(ranked_stimuli) <= 1:
        return ranked_stimuli
    conversation_source = _first_conversation_source(ranked_stimuli)
    if conversation_source is None:
        selected = ranked_stimuli[:2]
        deferred = ranked_stimuli[2:]
        if deferred:
            stimulus_queue.requeue_front(deferred)
        return selected

    selected, deferred = _partition_cycle_stimuli(ranked_stimuli, conversation_source)
    if deferred:
        stimulus_queue.requeue_front(deferred)
    return selected


def _first_conversation_source(stimuli: list[Stimulus]) -> str | None:
    for stimulus in stimuli:
        if stimulus.type == "conversation":
            return stimulus.source
    return None


def _partition_cycle_stimuli(
    ranked_stimuli: list[Stimulus],
    conversation_source: str,
) -> tuple[list[Stimulus], list[Stimulus]]:
    conversation_group: list[Stimulus] = []
    first_conversation_index: int | None = None
    selected_non_conversation: tuple[int, Stimulus] | None = None
    deferred: list[Stimulus] = []

    for index, stimulus in enumerate(ranked_stimuli):
        if stimulus.type == "conversation":
            if stimulus.source == conversation_source:
                if first_conversation_index is None:
                    first_conversation_index = index
                conversation_group.append(stimulus)
            else:
                deferred.append(stimulus)
            continue
        if _is_background_stimulus_during_conversation(stimulus):
            continue
        if selected_non_conversation is None:
            selected_non_conversation = (index, stimulus)
            continue
        deferred.append(stimulus)

    merged_conversation = _merge_conversation_stimuli(conversation_group)
    selected: list[tuple[int, Stimulus]] = [(
        0 if first_conversation_index is None else first_conversation_index,
        merged_conversation,
    )]
    if selected_non_conversation is not None:
        selected.append(selected_non_conversation)
    selected.sort(key=lambda pair: pair[0])
    return [stimulus for _, stimulus in selected], deferred


def _is_background_stimulus_during_conversation(stimulus: Stimulus) -> bool:
    return stimulus.type in {"time", "system_status"}


def _merge_conversation_stimuli(conversation_group: list[Stimulus]) -> Stimulus:
    first = conversation_group[0]
    last = conversation_group[-1]
    merged_metadata = dict(first.metadata)
    merged_metadata["merged_count"] = len(conversation_group)
    merged_metadata["merged_stimulus_ids"] = [
        stimulus.stimulus_id for stimulus in conversation_group
    ]
    latest_message_id = last.metadata.get("telegram_message_id")
    if latest_message_id is not None:
        merged_metadata["telegram_message_id"] = latest_message_id
    return Stimulus(
        stimulus_id=first.stimulus_id,
        type=first.type,
        priority=first.priority,
        source=first.source,
        content=CONVERSATION_MERGE_SEPARATOR.join(
            _compact_conversation_text(stimulus.content) for stimulus in conversation_group
        ),
        timestamp=first.timestamp,
        action_id=first.action_id,
        metadata=merged_metadata,
    )


def _compact_conversation_text(content: str) -> str:
    return " ".join(content.split())


def _recover_runtime_services(
    log_file,
    runtime: EngineRuntime,
    identity: dict[str, str],
    bootstrap_identity: dict[str, str],
    now: float,
    reconnect_interval: float,
    last_redis_reconnect: float,
    last_pg_reconnect: float,
) -> tuple[dict[str, str], float, float]:
    had_redis = runtime.stm.redis_available
    had_pg = runtime.ltm.available
    last_redis_reconnect = _maybe_reconnect_redis(
        log_file, runtime.stm, now, last_redis_reconnect, reconnect_interval,
    )
    if _redis_recovered(runtime.stm, runtime.stimulus_queue, runtime.action_manager, had_redis):
        _publish_event(runtime.stm.redis_client, "status", _status_payload("redis_recovered"))
    identity, last_pg_reconnect = _maybe_reconnect_pg(
        log_file, runtime.ltm, identity, bootstrap_identity,
        now, last_pg_reconnect, reconnect_interval,
    )
    if not had_pg and runtime.ltm.available:
        _publish_event(runtime.stm.redis_client, "status", _status_payload("postgres_recovered"))
    return identity, last_redis_reconnect, last_pg_reconnect


def _next_cycle_id(stm: ShortTermMemory, last_cycle_id: int) -> int:
    baseline_cycle_id = max(last_cycle_id, stm.latest_cycle_id())
    fallback_cycle_id = baseline_cycle_id + 1
    redis_client = stm.redis_client
    if redis_client is None:
        return fallback_cycle_id
    try:
        return int(
            redis_client.eval(
                """
                local counter = tonumber(redis.call("GET", KEYS[1]) or "0")
                local latest = tonumber(redis.call("GET", KEYS[2]) or "0")
                local baseline = tonumber(ARGV[1]) or 0
                if latest > baseline then
                  baseline = latest
                end
                if counter > baseline then
                  baseline = counter
                end
                local nextv = baseline + 1
                redis.call("SET", KEYS[1], nextv)
                if nextv > latest then
                  redis.call("SET", KEYS[2], nextv)
                end
                return nextv
                """,
                2,
                CYCLE_COUNTER_KEY,
                LATEST_CYCLE_KEY,
                baseline_cycle_id,
            )
        )
    except CYCLE_COUNTER_EXCEPTIONS:
        return fallback_cycle_id


def _redis_recovered(
    stm: ShortTermMemory,
    stimulus_queue: StimulusQueue,
    action_manager,
    had_redis: bool,
) -> bool:
    if not stm.redis_available or stm.redis_client is None:
        return False
    if had_redis and stimulus_queue.redis_available and action_manager.redis_available:
        return False
    queue_ok = stimulus_queue.attach_redis(stm.redis_client)
    action_ok = action_manager.attach_redis(stm.redis_client)
    return queue_ok and action_ok


def _execute_cycle(
    runtime: EngineRuntime,
    cycle_id: int,
    identity: dict[str, str],
    stimuli: list[Stimulus],
    running_actions: list[ActionRecord],
    perception_cues: list[str],
    prompt_log_file,
) -> list[Thought]:
    recent_thoughts = runtime.stm.get_context()
    ltm_context = _retrieve_associations(
        runtime.ltm,
        runtime.embedding_client,
        runtime.stm,
        runtime.embedding_model,
    )
    thoughts = run_cycle(
        runtime.primary_client,
        cycle_id,
        identity,
        recent_thoughts,
        runtime.context_window,
        runtime.model_config,
        long_term_context=ltm_context,
        stimuli=stimuli,
        running_actions=running_actions,
        perception_cues=perception_cues,
        prompt_log_file=prompt_log_file,
    )
    _sanitize_cycle_trigger_refs(thoughts, recent_thoughts)
    runtime.stm.append(thoughts)
    _store_to_ltm(runtime.ltm, runtime.embedding_client, thoughts, runtime.embedding_model, cycle_id)
    runtime.action_manager.submit_from_thoughts(thoughts, stimuli=stimuli)
    return thoughts


def _handle_cycle_failure(
    log_file,
    cycle_id: int,
    stimuli: list[Stimulus],
    stimulus_queue: StimulusQueue,
    exc: Exception,
    retry_delay: float,
    max_retry_delay: float,
) -> float:
    stimulus_queue.requeue_front(stimuli)
    _print_error(log_file, cycle_id, exc, retry_delay)
    time.sleep(retry_delay)
    return min(retry_delay * 2, max_retry_delay)


def _finish_cycle(log_file, cycle_id: int, stimuli: list[Stimulus], thoughts: list[Thought]) -> None:
    _print_stimuli(log_file, stimuli)
    _print_cycle(log_file, cycle_id, thoughts)


def _sanitize_cycle_trigger_refs(thoughts: list[Thought], recent_thoughts: list[Thought]) -> None:
    valid_ids = {thought.thought_id for thought in recent_thoughts}
    for thought in thoughts:
        trigger_ref = str(thought.trigger_ref or "").strip()
        if not trigger_ref:
            thought.trigger_ref = None
        elif trigger_ref in valid_ids:
            pass
        else:
            logger.warning("dropping invalid trigger_ref for %s: %s", thought.thought_id, trigger_ref)
            thought.trigger_ref = None
        valid_ids.add(thought.thought_id)


# -- Long-term memory read/write ------------------------------------------

def _retrieve_associations(
    ltm: LongTermMemory,
    embedding_client,
    stm: ShortTermMemory,
    embedding_model: str,
) -> list[str] | None:
    """Embed the latest thought and retrieve related long-term memories.

    # NOTE: SPECS §14.2 requires Embedding fallback to time-ordered retrieval.
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
        vec = embed_text(embedding_client, anchor.content, embedding_model)
    except EMBEDDING_EXCEPTIONS:
        return None
    try:
        entries = ltm.search(vec)
        if not entries:
            return None
        ltm.mark_accessed([e.id for e in entries])
        return [e.content for e in entries]
    except LTM_EXCEPTIONS:
        ltm.disconnect()
        return None


def _store_to_ltm(
    ltm: LongTermMemory,
    embedding_client,
    thoughts: list[Thought],
    embedding_model: str,
    cycle_id: int,
) -> None:
    """Embed and store new thoughts into long-term memory."""
    if not ltm.available:
        return
    for t in thoughts:
        try:
            vec = embed_text(embedding_client, t.content, embedding_model)
        except EMBEDDING_EXCEPTIONS:
            continue
        try:
            ltm.store(
                content=t.content,
                memory_type="episodic",
                embedding=vec,
                source_cycle_id=cycle_id,
            )
        except LTM_EXCEPTIONS:
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
    return connect_redis_from_env()


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
    except PG_CONNECT_EXCEPTIONS:
        return None


# -- Terminal output -------------------------------------------------------

def _print_cycle(log_file, cycle_id: int, thoughts: list[Thought]) -> None:
    print(f"\n{C_DIM}── C{cycle_id} ──{C_RESET}")
    lines = [f"── C{cycle_id} ──"]
    for t in thoughts:
        color = C_TYPE.get(t.type, "")
        trigger = f" {C_DIM}(← {t.trigger_ref}){C_RESET}" if t.trigger_ref else ""
        print(f"  {color}[{t.type}]{C_RESET} {t.content}{trigger}")
        plain_trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
        lines.append(f"  [{t.type}] {t.content}{plain_trigger}")
    _write_log_message(log_file, "\n".join(lines))


def _print_stimuli(log_file, stimuli: list[Stimulus]) -> None:
    if not stimuli:
        return

    print(f"{C_DIM}刺激{C_RESET}")
    lines = ["刺激"]
    for stimulus in stimuli:
        print(f"  {C_DIM}[{stimulus.type}]{C_RESET} {stimulus.content}")
        lines.append(f"  [{stimulus.type}] {stimulus.content}")
    _write_log_message(log_file, "\n".join(lines))


def _push_passive_stimuli(stimulus_queue: StimulusQueue, stimuli: list[PerceptionStimulusPayload]) -> None:
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
    _write_log_message(log_file, text)


def _print_error(log_file, cycle_id: int, error: Exception, retry_delay: float) -> None:
    msg = f"── C{cycle_id} ERROR: {error} (retry in {retry_delay:.1f}s)"
    print(f"\n\033[31m{msg}\033[0m", file=sys.stderr)
    _write_log_message(log_file, msg, level=logging.WARNING, exc_info=True)


def _write_log_message(log_file, text: str, *, level: int = logging.INFO, exc_info: bool = False) -> None:
    logger.log(level, text, exc_info=exc_info)
    if log_file is None:
        return
    log_file.write(text + "\n")
    log_file.flush()


def _publish_event(redis_client, event_type: str, payload: EventPayload) -> None:
    if redis_client is None:
        return
    try:
        redis_client.publish(EVENT_CHANNEL, json.dumps({
            "type": event_type,
            "payload": payload,
        }, ensure_ascii=False))
    except REDIS_EVENT_EXCEPTIONS:
        return


def _status_payload(message: str) -> StatusEventPayload:
    return {"message": message}


# -- Utilities -------------------------------------------------------------


def _load_config(path: str) -> dict:
    try:
        return load_yaml_config(path, required=True)
    except FileNotFoundError:
        print(f"配置文件不存在: {path}", file=sys.stderr)
        sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seedwake thought-stream engine")
    parser.add_argument("--config", default="config.yml", help="Path to config.yml")
    parser.add_argument("--log", default=None, help="Path to plain-text log file")
    return parser.parse_args()


def _open_log(path: str | None, config: dict):
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    if target == resolve_log_path(config, component="core"):
        logger.info("manual cycle log path matches core logger output; reusing logging handler only")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("w", encoding="utf-8")


def _open_prompt_log(config: dict, *, plain_log_path: str | None):
    target = _resolve_prompt_log_path(config)
    reserved_paths = {resolve_log_path(config, component="core")}
    if plain_log_path:
        reserved_paths.add(Path(plain_log_path).expanduser().resolve())
    if target in reserved_paths:
        logger.warning("prompt log path %s conflicts with other logs; using sibling prompt.txt", target)
        target = target.with_name("prompt.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("w", encoding="utf-8")


def _resolve_prompt_log_path(config: dict) -> Path:
    runtime = dict((config or {}).get("runtime") or {})
    logging_config = dict(runtime.get("logging") or {})
    configured_path = str(logging_config.get("prompt_path") or "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    directory = str(logging_config.get("directory") or "data/logs").strip() or "data/logs"
    return (Path(directory) / "prompt.txt").expanduser().resolve()


def _install_signal_handler(log_file, prompt_log_file, action_manager) -> None:
    def handler(sig, frame):
        _ = sig, frame
        print(f"\n\n{C_DIM}心相续止息。{C_RESET}")
        drained = action_manager.shutdown_with_timeout(wait_timeout_seconds=5.0)
        if log_file:
            log_file.close()
        if prompt_log_file:
            prompt_log_file.close()
        if not drained:
            logger.warning("forced exit with running actions still active")
            os._exit(0)
        sys.exit(0)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


if __name__ == "__main__":
    main()
