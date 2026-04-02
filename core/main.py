"""Seedwake — thought-stream engine.

Usage: python -m core.main [--config config.yml] [--log data/test.txt]
"""

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import TextIO

import psycopg
import redis as redis_lib
from dotenv import load_dotenv

from core.action import (
    ActionManager,
    ActionRecord,
    ActionRedisLike,
    create_action_manager,
    pop_action_controls,
)
from core.attention import evaluate_attention, select_attention_anchor
from core.cycle import run_cycle, write_prompt_log_block
from core.embedding import embed_text
from core.emotion import EmotionManager
from core.logging import resolve_log_path, setup_logging
from core.memory.habit import HabitMemory
from core.memory.identity import load_identity
from core.memory.long_term import LongTermEntry, LongTermMemory
from core.memory.short_term import LATEST_CYCLE_KEY, ShortTermMemory
from core.metacognition import MetacognitionManager
from core.model_client import MODEL_CLIENT_EXCEPTIONS, ModelClient, create_model_client
from core.perception import PerceptionManager
from core.prefrontal import PrefrontalManager
from core.prompt_builder import PromptBuildContext
from core.runtime import connect_redis_from_env, load_yaml_config
from core.sleep import SleepManager
from core.stimulus import (
    ConversationRedisLike,
    RECENT_CONVERSATION_SUMMARY_MAX_CHARS,
    Stimulus,
    StimulusQueue,
    load_recent_action_echoes,
    load_recent_conversations,
    remember_recent_action_echoes,
)
from core.thought_parser import Thought
from core.types import (
    ConversationEntry,
    EventPayload,
    JsonObject,
    PerceptionStimulusPayload,
    JsonValue,
    RecentConversationPrompt,
    StatusEventPayload,
    elapsed_ms,
)

# Terminal colors
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_TYPE = {
    "思考": "\033[36m",    # cyan
    "意图": "\033[33m",    # yellow
    "反应": "\033[32m",    # green
    "反思": "\033[35m",    # magenta
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
RECENT_CONVERSATION_SUMMARY_TARGET_CHARS = 280
RECENT_CONVERSATION_SUMMARY_SYSTEM_PROMPT = (
    "你在为 Seedwake 压缩更早的对话历史。"
    "根据已有摘要和补充消息，写一段新的中文自然语言摘要，替换旧摘要。"
    "只概括不会直接展示的更早消息，不要把最近会直接展示的消息再写进去。"
    "不要逐条复读，不要项目符号，不要时间戳，不要消息编号。"
    "对方用名字称呼，assistant 用“我”。"
    f"控制在 {RECENT_CONVERSATION_SUMMARY_TARGET_CHARS} 字以内，只输出摘要正文。"
)
RECENT_CONVERSATION_SUMMARY_BATCH_MAX_CHARS = 2400
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
CONVERSATION_MERGE_SEPARATOR = "\n"
LTM_ACTION_MARKER_PATTERN = re.compile(r"\s*\{action:[^}]+\}\s*$")
LTM_RETRIEVAL_OVERSAMPLE_FACTOR = 3
MERGED_TELEGRAM_METADATA_KEYS = (
    "telegram_user_id",
    "telegram_chat_id",
    "telegram_username",
    "telegram_full_name",
    "telegram_message_id",
    "reply_to_message_id",
    "reply_to_preview",
    "reply_to_user_id",
    "reply_to_from_self",
    "reply_to_username",
    "reply_to_full_name",
)
logger = logging.getLogger(__name__)


@dataclass
class EngineRuntime:
    primary_client: ModelClient
    auxiliary_client: ModelClient
    embedding_client: ModelClient
    stm: ShortTermMemory
    ltm: LongTermMemory
    habit_memory: HabitMemory
    stimulus_queue: StimulusQueue
    perception: PerceptionManager
    action_manager: ActionManager
    emotion: EmotionManager
    prefrontal: PrefrontalManager
    metacognition: MetacognitionManager
    sleep: SleepManager
    model_config: dict
    auxiliary_model_config: dict
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

    primary_client, auxiliary_client, embedding_client, redis_client, pg_conn = _create_connections(config)
    runtime, identity = _build_runtime_components(
        config,
        log_file,
        primary_client,
        auxiliary_client,
        embedding_client,
        redis_client,
        pg_conn,
    )

    _install_signal_handler(log_file, prompt_log_file, runtime.action_manager)
    _emit_startup(log_file, runtime.model_config, runtime.context_window,
                  redis_client, pg_conn)
    _run_engine_loop(log_file, prompt_log_file, runtime, identity)


def _create_connections(
    config: dict,
) -> tuple[ModelClient, ModelClient, ModelClient, redis_lib.Redis | None, psycopg.Connection | None]:
    models_config = config.get("models", {})
    primary_client = create_model_client(dict(models_config.get("primary") or {}))
    auxiliary_client = create_model_client(dict(models_config.get("auxiliary") or {}))
    embedding_client = create_model_client(dict(models_config.get("embedding") or {}))
    return primary_client, auxiliary_client, embedding_client, _connect_redis(), _connect_pg()


def _build_runtime_components(
    config: dict,
    log_file: TextIO | None,
    primary_client: ModelClient,
    auxiliary_client: ModelClient,
    embedding_client: ModelClient,
    redis_client: redis_lib.Redis | None,
    pg_conn: psycopg.Connection | None,
) -> tuple[EngineRuntime, dict[str, str]]:
    model_config = config["models"]["primary"]
    auxiliary_model_config = config["models"]["auxiliary"]
    embedding_model = config["models"]["embedding"]["name"]
    retry_delay, max_retry_delay, reconnect_interval = _runtime_retry_settings(config)
    bootstrap_identity = config["bootstrap"]["identity"]
    context_window = config["short_term_memory"]["context_window_size"]
    buffer_size = config.get("short_term_memory", {}).get("buffer_size", 500)
    retrieval_top_k = config.get("long_term_memory", {}).get("retrieval_top_k", 5)

    identity = load_identity(pg_conn, bootstrap_identity)
    stm = ShortTermMemory(redis_client, context_window, buffer_size)
    ltm = LongTermMemory(
        pg_conn,
        retrieval_top_k,
        time_decay_factor=float(config.get("long_term_memory", {}).get("time_decay_factor", 0.95)),
        importance_threshold=float(config.get("long_term_memory", {}).get("importance_threshold", 0.1)),
    )
    habit_memory = HabitMemory(
        pg_conn,
        max_active_in_prompt=int(config.get("habits", {}).get("max_active_in_prompt", 3)),
        decay_rate=float(config.get("habits", {}).get("decay_rate", 0.01)),
    )
    habit_memory.ensure_bootstrap_seeds(list(config.get("bootstrap", {}).get("habits", []) or []))
    stimulus_queue = StimulusQueue(_as_conversation_redis(redis_client))
    perception = PerceptionManager.from_config(_perception_config(config))
    emotion = EmotionManager(
        redis_client,
        inertia=float(config.get("emotion", {}).get("inertia", 0.7)),
        dimensions=[str(item) for item in (config.get("emotion", {}).get("dimensions", []) or [])],
    )
    prefrontal = PrefrontalManager(
        redis_client,
        check_interval=int(config.get("prefrontal", {}).get("check_interval", 6)),
        inhibition_enabled=bool(config.get("prefrontal", {}).get("inhibition_enabled", True)),
    )
    metacognition = MetacognitionManager(
        redis_client,
        reflection_interval=int(config.get("metacognition", {}).get("reflection_interval", 50)),
    )
    sleep = SleepManager(
        redis_client,
        energy_per_cycle=float(config.get("sleep", {}).get("energy_per_cycle", 0.2)),
        drowsy_threshold=float(config.get("sleep", {}).get("drowsy_threshold", 30)),
        light_sleep_recovery=float(config.get("sleep", {}).get("light_sleep_recovery", 70)),
        deep_sleep_trigger_hours=float(config.get("sleep", {}).get("deep_sleep_trigger_hours", 24)),
        archive_importance_threshold=float(config.get("long_term_memory", {}).get("importance_threshold", 0.1)),
    )
    action_manager = create_action_manager(
        _as_action_redis(redis_client),
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
        auxiliary_client=auxiliary_client,
        embedding_client=embedding_client,
        stm=stm,
        ltm=ltm,
        habit_memory=habit_memory,
        stimulus_queue=stimulus_queue,
        perception=perception,
        action_manager=action_manager,
        emotion=emotion,
        prefrontal=prefrontal,
        metacognition=metacognition,
        sleep=sleep,
        model_config=model_config,
        auxiliary_model_config=auxiliary_model_config,
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


def _emit_startup(
    log_file: TextIO | None,
    model_config: dict,
    context_window: int,
    redis_client: redis_lib.Redis | None,
    pg_conn: psycopg.Connection | None,
) -> None:
    model_name = str(model_config.get("name") or "")
    provider = str(model_config.get("provider") or "ollama")
    _output(log_file, "Seedwake v0.2 — 心相续引擎启动")
    _output(log_file, f"模型: {model_name} [{provider}]  上下文窗口: {context_window} 轮")
    _output(log_file, f"Redis: {'已连接' if redis_client else '未连接（使用内存）'}")
    _output(log_file, f"PostgreSQL: {'已连接' if pg_conn else '未连接（跳过长期记忆）'}")
    _output(log_file, "─" * 60)
    _publish_event(redis_client, "status", _status_payload("core_started"))


def _run_engine_loop(
    log_file: TextIO | None,
    prompt_log_file: TextIO | None,
    runtime: EngineRuntime,
    identity: dict[str, str],
) -> None:
    last_completed_cycle_id = 0
    pending_cycle_id: int | None = None
    current_retry_delay = runtime.retry_delay
    last_redis_reconnect = 0.0
    last_pg_reconnect = 0.0

    while True:
        loop_started_at = time.perf_counter()
        retry_sleep_ms = 0.0
        if pending_cycle_id is None:
            pending_cycle_id = _next_cycle_id(runtime.stm, last_completed_cycle_id)
        assert pending_cycle_id is not None
        cycle_id = pending_cycle_id
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
            new_thoughts, degeneration_alert = _execute_cycle(
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
            retry_sleep_ms = current_retry_delay * 1000.0
            current_retry_delay = _handle_cycle_failure(
                log_file,
                cycle_id,
                stimuli,
                runtime.stimulus_queue,
                exc,
                current_retry_delay,
                runtime.max_retry_delay,
            )
            _log_cycle_loop_finished(
                cycle_id,
                started_at=loop_started_at,
                status="failed",
                stimuli_count=len(stimuli),
                thought_count=0,
                retry_sleep_ms=retry_sleep_ms,
            )
            continue
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("unexpected main loop failure at cycle %s: %s", cycle_id, exc)
            retry_sleep_ms = current_retry_delay * 1000.0
            current_retry_delay = _handle_cycle_failure(
                log_file,
                cycle_id,
                stimuli,
                runtime.stimulus_queue,
                exc,
                current_retry_delay,
                runtime.max_retry_delay,
            )
            _log_cycle_loop_finished(
                cycle_id,
                started_at=loop_started_at,
                status="failed",
                stimuli_count=len(stimuli),
                thought_count=0,
                retry_sleep_ms=retry_sleep_ms,
            )
            continue

        _finish_cycle(log_file, cycle_id, stimuli, new_thoughts)
        _post_cycle_phase4(runtime, cycle_id, stimuli, new_thoughts, degeneration_alert)
        last_completed_cycle_id = cycle_id
        pending_cycle_id = None
        current_retry_delay = runtime.retry_delay
        _log_cycle_loop_finished(
            cycle_id,
            started_at=loop_started_at,
            status="ok",
            stimuli_count=len(stimuli),
            thought_count=len(new_thoughts),
            retry_sleep_ms=retry_sleep_ms,
        )


def _log_cycle_loop_finished(
    cycle_id: int,
    *,
    started_at: float,
    status: str,
    stimuli_count: int,
    thought_count: int,
    retry_sleep_ms: float,
) -> None:
    logger.info(
        "cycle C%s loop finished in %.1f ms (status=%s, stimuli=%d, thoughts=%d, retry_sleep_ms=%.1f)",
        cycle_id,
        elapsed_ms(started_at),
        status,
        stimuli_count,
        thought_count,
            retry_sleep_ms,
        )


def _post_cycle_phase4(
    runtime: EngineRuntime,
    cycle_id: int,
    stimuli: list[Stimulus],
    thoughts: list[Thought],
    degeneration_alert: bool,
) -> None:
    failure_count = _failed_action_echo_count(stimuli)
    sleep_state = runtime.sleep.consume_cycle(
        cycle_id,
        stimuli,
        failure_count=failure_count,
        degeneration_alert=degeneration_alert,
    )
    logger.info(
        "cycle C%s sleep state update finished (energy=%.1f, mode=%s)",
        cycle_id,
        sleep_state["energy"],
        sleep_state["mode"],
    )
    buffer_thoughts = runtime.stm.buffer_thoughts()
    if runtime.sleep.should_deep_sleep(now=datetime.now(timezone.utc)):
        result = runtime.sleep.run_deep_sleep(
            cycle_id=cycle_id,
            stm=runtime.stm,
            ltm=runtime.ltm,
            habit_memory=runtime.habit_memory,
            embedding_client=runtime.embedding_client,
            embedding_model=runtime.embedding_model,
            auxiliary_client=runtime.auxiliary_client,
            auxiliary_model_config=runtime.auxiliary_model_config,
            emotion=runtime.emotion.current(),
        )
        logger.info(
            "cycle C%s deep sleep finished (archived=%d, habits=%d, cooled=%d, summary=%s)",
            cycle_id,
            result.archived_count,
            result.created_habits,
            result.cooled_memories,
            bool(result.deep_summary),
        )
        return
    if runtime.sleep.should_light_sleep(degeneration_alert=degeneration_alert, buffer_thoughts=buffer_thoughts):
        result = runtime.sleep.run_light_sleep(
            cycle_id=cycle_id,
            stm=runtime.stm,
            ltm=runtime.ltm,
            habit_memory=runtime.habit_memory,
            embedding_client=runtime.embedding_client,
            embedding_model=runtime.embedding_model,
        )
        logger.info(
            "cycle C%s light sleep finished (archived=%d, habits=%d, cooled=%d)",
            cycle_id,
            result.archived_count,
            result.created_habits,
            result.cooled_memories,
        )


def _prepare_cycle(
    log_file: TextIO | None,
    cycle_id: int,
    runtime: EngineRuntime,
    identity: dict[str, str],
    bootstrap_identity: dict[str, str],
    reconnect_interval: float,
    last_redis_reconnect: float,
    last_pg_reconnect: float,
) -> tuple[dict[str, str], float, float, list[Stimulus], list[ActionRecord], list[str]]:
    prepare_started_at = time.perf_counter()
    now = time.monotonic()
    recovery_started_at = time.perf_counter()
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
    logger.info(
        "cycle C%s runtime recovery finished in %.1f ms",
        cycle_id,
        elapsed_ms(recovery_started_at),
    )
    passive_started_at = time.perf_counter()
    passive_stimuli = runtime.perception.collect_passive_stimuli(cycle_id)
    _push_passive_stimuli(runtime.stimulus_queue, passive_stimuli)
    logger.info(
        "cycle C%s passive stimuli finished in %.1f ms (count=%d)",
        cycle_id,
        elapsed_ms(passive_started_at),
        len(passive_stimuli),
    )
    controls_started_at = time.perf_counter()
    controls = pop_action_controls(_as_action_redis(runtime.stm.redis_client))
    runtime.action_manager.apply_controls(controls)
    runtime.action_manager.retry_deferred_actions()
    logger.info(
        "cycle C%s action controls finished in %.1f ms (count=%d)",
        cycle_id,
        elapsed_ms(controls_started_at),
        len(controls),
    )
    selection_started_at = time.perf_counter()
    stimuli = _select_cycle_stimuli(runtime.stimulus_queue)
    logger.info(
        "cycle C%s stimulus selection finished in %.1f ms (count=%d)",
        cycle_id,
        elapsed_ms(selection_started_at),
        len(stimuli),
    )
    remember_recent_action_echoes(
        _as_conversation_redis(runtime.stm.redis_client),
        cycle_id,
        stimuli,
    )
    perception_started_at = time.perf_counter()
    runtime.perception.observe_stimuli(cycle_id, stimuli)
    runtime.perception.observe_types(cycle_id, runtime.action_manager.pop_perception_observations())
    running_actions = runtime.action_manager.running_actions()
    perception_cues = runtime.perception.build_prompt_cues(cycle_id, running_actions)
    logger.info(
        "cycle C%s perception update finished in %.1f ms (running=%d, cues=%d)",
        cycle_id,
        elapsed_ms(perception_started_at),
        len(running_actions),
        len(perception_cues),
    )
    logger.info(
        "cycle C%s prepare finished in %.1f ms (stimuli=%d)",
        cycle_id,
        elapsed_ms(prepare_started_at),
        len(stimuli),
    )
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
        if _collect_matching_conversation_stimulus(
            stimulus,
            conversation_source,
            conversation_group,
        ):
            if first_conversation_index is None:
                first_conversation_index = index
            continue
        if stimulus.type == "conversation":
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


def _collect_matching_conversation_stimulus(
    stimulus: Stimulus,
    conversation_source: str,
    conversation_group: list[Stimulus],
) -> bool:
    if stimulus.type != "conversation" or stimulus.source != conversation_source:
        return False
    conversation_group.append(stimulus)
    return True


def _merge_conversation_stimuli(conversation_group: list[Stimulus]) -> Stimulus:
    first = conversation_group[0]
    flattened_pairs = [
        pair
        for stimulus in conversation_group
        for pair in _conversation_message_pairs(stimulus)
    ]
    merged_stimulus_ids = [stimulus_id for stimulus_id, _ in flattened_pairs if stimulus_id]
    merged_messages = [message for _, message in flattened_pairs]
    last_message = merged_messages[-1]
    merged_metadata = {
        key: value
        for key, value in first.metadata.items()
        if key not in {"merged_count", "merged_stimulus_ids", "merged_messages", *MERGED_TELEGRAM_METADATA_KEYS}
    }
    merged_metadata["merged_count"] = len(merged_messages)
    merged_metadata["merged_stimulus_ids"] = merged_stimulus_ids
    merged_metadata["merged_messages"] = merged_messages
    latest_message_id = last_message.get("telegram_message_id")
    if latest_message_id is not None:
        merged_metadata["telegram_message_id"] = latest_message_id
    for key in MERGED_TELEGRAM_METADATA_KEYS:
        if key in last_message:
            merged_metadata[key] = last_message[key]
    return Stimulus(
        stimulus_id=first.stimulus_id,
        type=first.type,
        priority=first.priority,
        source=first.source,
        content=CONVERSATION_MERGE_SEPARATOR.join(
            str(message.get("content") or "")
            for message in merged_messages
        ),
        timestamp=first.timestamp,
        action_id=first.action_id,
        metadata=merged_metadata,
    )


def _compact_conversation_text(content: str) -> str:
    return " ".join(content.split())


def _merged_conversation_message(stimulus: Stimulus) -> JsonObject:
    payload: JsonObject = {
        "source": stimulus.source,
        "content": _compact_conversation_text(stimulus.content),
        "timestamp": stimulus.timestamp.isoformat(),
    }
    for key in MERGED_TELEGRAM_METADATA_KEYS:
        if key in stimulus.metadata:
            payload[key] = stimulus.metadata[key]
    return payload


def _conversation_message_pairs(stimulus: Stimulus) -> list[tuple[str, JsonObject]]:
    merged_messages = stimulus.metadata.get("merged_messages")
    merged_ids = stimulus.metadata.get("merged_stimulus_ids")
    if isinstance(merged_messages, list) and isinstance(merged_ids, list) and len(merged_messages) == len(merged_ids):
        pairs: list[tuple[str, JsonObject]] = []
        for index, raw_message in enumerate(merged_messages):
            message = _normalize_merged_conversation_message(raw_message, stimulus)
            pairs.append((str(merged_ids[index] or "").strip(), message))
        return pairs
    return [(stimulus.stimulus_id, _merged_conversation_message(stimulus))]


def _normalize_merged_conversation_message(message: JsonValue, stimulus: Stimulus) -> JsonObject:
    if not isinstance(message, dict):
        return _merged_conversation_message(stimulus)
    normalized: JsonObject = {str(key): value for key, value in message.items()}
    normalized["source"] = str(message.get("source") or stimulus.source)
    normalized["content"] = _compact_conversation_text(str(message.get("content") or ""))
    normalized["timestamp"] = str(message.get("timestamp") or stimulus.timestamp.isoformat())
    return normalized


def _recover_runtime_services(
    log_file: TextIO | None,
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
    if _redis_recovered(
        runtime.stm,
        runtime.stimulus_queue,
        runtime.action_manager,
        runtime.emotion,
        runtime.prefrontal,
        runtime.metacognition,
        runtime.sleep,
        had_redis,
    ):
        _publish_event(runtime.stm.redis_client, "status", _status_payload("redis_recovered"))
    identity, last_pg_reconnect = _maybe_reconnect_pg(
        log_file, runtime.ltm, identity, bootstrap_identity,
        now, last_pg_reconnect, reconnect_interval,
        habit_memory=runtime.habit_memory,
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
        next_cycle_id = redis_client.eval(
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
        if not isinstance(next_cycle_id, (str, int)):
            raise TypeError(f"unexpected cycle counter result: {type(next_cycle_id).__name__}")
        return int(next_cycle_id)
    except CYCLE_COUNTER_EXCEPTIONS:
        return fallback_cycle_id


def _redis_recovered(
    stm: ShortTermMemory,
    stimulus_queue: StimulusQueue,
    action_manager: ActionManager,
    emotion: EmotionManager,
    prefrontal: PrefrontalManager,
    metacognition: MetacognitionManager,
    sleep: SleepManager,
    had_redis: bool,
) -> bool:
    if not stm.redis_available or stm.redis_client is None:
        return False
    if had_redis and stimulus_queue.redis_available and action_manager.redis_available:
        return False
    queue_ok = stimulus_queue.attach_redis(stm.redis_client)  # type: ignore[arg-type]
    action_ok = action_manager.attach_redis(stm.redis_client)  # type: ignore[arg-type]
    emotion_ok = emotion.attach_redis(stm.redis_client)
    prefrontal_ok = prefrontal.attach_redis(stm.redis_client)
    metacognition_ok = metacognition.attach_redis(stm.redis_client)
    sleep_ok = sleep.attach_redis(stm.redis_client)
    return queue_ok and action_ok and emotion_ok and prefrontal_ok and metacognition_ok and sleep_ok


def _execute_cycle(
    runtime: EngineRuntime,
    cycle_id: int,
    identity: dict[str, str],
    stimuli: list[Stimulus],
    running_actions: list[ActionRecord],
    perception_cues: list[str],
    prompt_log_file: TextIO | None,
) -> tuple[list[Thought], bool]:
    cycle_started_at = time.perf_counter()
    cycle_status = "failed"
    pending_prompt_echoes: list[Stimulus] = []
    context_started_at = time.perf_counter()
    recent_thoughts = runtime.stm.get_context()
    logger.info(
        "cycle C%s stm get_context finished in %.1f ms (count=%d)",
        cycle_id,
        elapsed_ms(context_started_at),
        len(recent_thoughts),
    )
    try:
        note_text = runtime.action_manager.current_note()
        current_emotion = runtime.emotion.current()
        current_sleep_state = runtime.sleep.current()
        active_habits = runtime.habit_memory.activate_for_cycle(recent_thoughts, stimuli)
        prefrontal_state = runtime.prefrontal.current_state(
            cycle_id,
            identity,
            note_text,
            active_habits,
            current_sleep_state,
            current_emotion["summary"],
        )
        recent_reflections = runtime.metacognition.recent_reflections()
        ltm_started_at = time.perf_counter()
        ltm_context = _retrieve_associations(
            runtime.ltm,
            runtime.embedding_client,
            recent_thoughts,
            runtime.embedding_model,
        )
        logger.info(
            "cycle C%s association retrieval finished in %.1f ms (count=%d)",
            cycle_id,
            elapsed_ms(ltm_started_at),
            len(ltm_context or []),
        )
        conversations_started_at = time.perf_counter()
        recent_conversations = _load_recent_conversations(runtime, cycle_id, stimuli, prompt_log_file)
        logger.info(
            "cycle C%s recent conversations loaded in %.1f ms (count=%d)",
            cycle_id,
            elapsed_ms(conversations_started_at),
            len(recent_conversations),
        )
        recent_action_echoes = load_recent_action_echoes(
            _as_conversation_redis(runtime.stm.redis_client),
            current_cycle_id=cycle_id,
            exclude_action_ids=_action_echo_action_ids(stimuli),
        )
        pending_prompt_echoes = runtime.action_manager.pop_prompt_echoes()
        if pending_prompt_echoes:
            stimuli = [*stimuli, *pending_prompt_echoes]
        thought_cycle_started_at = time.perf_counter()
        thoughts = run_cycle(
            runtime.primary_client,
            cycle_id,
            identity,
            recent_thoughts,
            runtime.context_window,
            runtime.model_config,
            prompt_context=PromptBuildContext(
                goal_stack=prefrontal_state["goal_stack"],
                emotion=current_emotion,
                sleep_state=current_sleep_state,
                active_habits=active_habits,
                prefrontal_state=prefrontal_state,
                recent_reflections=recent_reflections,
                long_term_context=ltm_context,
                note_text=note_text,
                stimuli=stimuli,
                recent_action_echoes=recent_action_echoes,
                running_actions=running_actions,
                perception_cues=perception_cues,
                recent_conversations=recent_conversations,
            ),
            prompt_log_file=prompt_log_file,
        )
        logger.info(
            "cycle C%s thought generation finished in %.1f ms (count=%d)",
            cycle_id,
            elapsed_ms(thought_cycle_started_at),
            len(thoughts),
        )
        attention_started_at = time.perf_counter()
        attention_result = evaluate_attention(
            thoughts,
            recent_thoughts,
            stimuli,
            current_emotion,
            prefrontal_state["goal_stack"],
            note_text,
            active_habits,
        )
        thoughts = attention_result.thoughts
        logger.info(
            "cycle C%s attention evaluation finished in %.1f ms (anchor=%s)",
            cycle_id,
            elapsed_ms(attention_started_at),
            attention_result.anchor_thought_id,
        )
        inhibition_started_at = time.perf_counter()
        thoughts, inhibition_notes = runtime.prefrontal.review_thoughts(
            thoughts,
            recent_thoughts,
            stimuli,
            note_text,
            current_sleep_state,
        )
        logger.info(
            "cycle C%s prefrontal review finished in %.1f ms (inhibited=%d)",
            cycle_id,
            elapsed_ms(inhibition_started_at),
            len(inhibition_notes),
        )
        failure_count = _failed_action_echo_count(stimuli)
        degeneration_alert = _detect_runtime_degeneration(recent_thoughts, thoughts)
        reflection_started_at = time.perf_counter()
        reflection = None
        if runtime.metacognition.should_reflect(
            cycle_id,
            current_emotion,
            degeneration_alert=degeneration_alert,
            failure_count=failure_count,
            stimuli_changed=bool(stimuli),
        ):
            reflection = runtime.metacognition.generate_reflection(
                runtime.auxiliary_client,
                runtime.auxiliary_model_config,
                cycle_id=cycle_id,
                recent_thoughts=[*recent_thoughts[-9:], *thoughts],
                emotion=current_emotion,
                goals=prefrontal_state["goal_stack"],
                habits=active_habits,
                prefrontal_state=prefrontal_state,
                failure_count=failure_count,
                degeneration_alert=degeneration_alert,
            )
            if reflection is not None:
                thoughts = [*thoughts, reflection]
        logger.info(
            "cycle C%s metacognition finished in %.1f ms (generated=%s)",
            cycle_id,
            elapsed_ms(reflection_started_at),
            reflection is not None,
        )
        sanitize_started_at = time.perf_counter()
        _sanitize_cycle_trigger_refs(thoughts, recent_thoughts)
        logger.info(
            "cycle C%s trigger_ref sanitize finished in %.1f ms (count=%d)",
            cycle_id,
            elapsed_ms(sanitize_started_at),
            len(thoughts),
        )
        emotion_started_at = time.perf_counter()
        runtime.emotion.apply_cycle(
            cycle_id,
            thoughts,
            stimuli,
            running_actions,
            inhibited_actions=len(inhibition_notes),
            degeneration_alert=degeneration_alert,
        )
        logger.info(
            "cycle C%s emotion update finished in %.1f ms",
            cycle_id,
            elapsed_ms(emotion_started_at),
        )
        stm_started_at = time.perf_counter()
        runtime.stm.append(thoughts)
        logger.info("cycle C%s stm append finished in %.1f ms", cycle_id, elapsed_ms(stm_started_at))
        action_submit_started_at = time.perf_counter()
        created_actions = runtime.action_manager.submit_from_thoughts(thoughts, stimuli=stimuli)
        logger.info(
            "cycle C%s action submission finished in %.1f ms (created=%d)",
            cycle_id,
            elapsed_ms(action_submit_started_at),
            len(created_actions),
        )
        cycle_status = "ok"
        return thoughts, degeneration_alert
    except Exception:
        if pending_prompt_echoes:
            runtime.action_manager.requeue_prompt_echoes(pending_prompt_echoes)
        raise
    finally:
        logger.info(
            "cycle C%s total execution finished in %.1f ms (status=%s)",
            cycle_id,
            elapsed_ms(cycle_started_at),
            cycle_status,
        )


def _load_recent_conversations(
    runtime: EngineRuntime,
    cycle_id: int,
    stimuli: list[Stimulus],
    prompt_log_file: TextIO | None,
) -> list[RecentConversationPrompt]:
    return load_recent_conversations(
        _as_conversation_redis(runtime.stm.redis_client),
        include_sources={stimulus.source for stimulus in stimuli if stimulus.type == "conversation"},
        exclude_stimulus_ids=_conversation_stimulus_ids(stimuli),
        summary_builder=lambda source_name, existing_summary, entries: _summarize_recent_conversation(
            runtime.primary_client,
            runtime.model_config,
            cycle_id,
            source_name,
            existing_summary,
            entries,
            prompt_log_file,
        ),
    )


def _summarize_recent_conversation(
    client: ModelClient,
    model_config: dict,
    cycle_id: int,
    source_name: str,
    existing_summary: str,
    entries: list[ConversationEntry],
    prompt_log_file: TextIO | None,
) -> str | None:
    existing = str(existing_summary or "").strip()
    transcripts = _recent_conversation_summary_batches(entries, source_name)
    if not transcripts:
        return existing
    current_summary = existing
    total_started_at = time.perf_counter()
    total_batches = len(transcripts)
    for index, transcript in enumerate(transcripts, start=1):
        user_prompt = _recent_conversation_summary_request(
            source_name,
            current_summary,
            transcript,
        )
        _write_recent_conversation_summary_prompt_log(
            prompt_log_file,
            cycle_id,
            source_name,
            index,
            total_batches,
            RECENT_CONVERSATION_SUMMARY_SYSTEM_PROMPT,
            user_prompt,
        )
        batch_started_at = time.perf_counter()
        try:
            response = client.chat(
                model=str(model_config["name"]),
                messages=[
                    {"role": "system", "content": RECENT_CONVERSATION_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": 0.2, "max_tokens": 180},
            )
        except MODEL_CLIENT_EXCEPTIONS as exc:
            logger.warning("recent conversation summary failed for %s: %s", source_name, exc)
            return None
        message = response.get("message")
        summary = _clean_recent_conversation_summary(
            message.get("content") if isinstance(message, dict) else None,
        )
        logger.info(
            "cycle C%s recent conversation summary batch %d/%d for %s finished in "
            "%.1f ms (input_chars=%d, output_chars=%d)",
            cycle_id,
            index,
            total_batches,
            source_name,
            elapsed_ms(batch_started_at),
            len(user_prompt),
            len(summary or ""),
        )
        if not summary:
            logger.warning("recent conversation summary returned empty text for %s", source_name)
            return None
        current_summary = summary
    logger.info(
        "cycle C%s recent conversation summary for %s finished in %.1f ms (batches=%d, output_chars=%d)",
        cycle_id,
        source_name,
        elapsed_ms(total_started_at),
        total_batches,
        len(current_summary),
    )
    return current_summary


def _write_recent_conversation_summary_prompt_log(
    prompt_log_file: TextIO | None,
    cycle_id: int,
    source_name: str,
    batch_index: int,
    total_batches: int,
    system_prompt: str,
    user_prompt: str,
) -> None:
    if prompt_log_file is None:
        return
    write_prompt_log_block(
        prompt_log_file,
        title=f"SUMMARY PROMPT C{cycle_id} {source_name} B{batch_index}/{total_batches}",
        prompt=f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_prompt}",
        emoji="🟣",
    )


def _handle_cycle_failure(
    log_file: TextIO | None,
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


def _finish_cycle(
    log_file: TextIO | None,
    cycle_id: int,
    stimuli: list[Stimulus],
    thoughts: list[Thought],
) -> None:
    _print_stimuli(log_file, stimuli)
    _print_cycle(log_file, cycle_id, thoughts)


def _sanitize_cycle_trigger_refs(thoughts: list[Thought], recent_thoughts: list[Thought]) -> None:
    valid_ids = {thought.thought_id for thought in recent_thoughts}
    for thought in thoughts:
        trigger_ref = str(thought.trigger_ref or "").strip()
        if not trigger_ref:
            thought.trigger_ref = None
        elif trigger_ref not in valid_ids:
            logger.warning("dropping invalid trigger_ref for %s: %s", thought.thought_id, trigger_ref)
            thought.trigger_ref = None
        valid_ids.add(thought.thought_id)


# -- Long-term memory read/write ------------------------------------------

def _retrieve_associations(
    ltm: LongTermMemory,
    embedding_client: ModelClient,
    recent_thoughts: list[Thought],
    embedding_model: str,
) -> list[str] | None:
    """Embed the latest thought and retrieve related long-term memories.

    # NOTE: SPECS §14.2 requires Embedding fallback to time-ordered retrieval.
    # Current implementation skips LTM entirely on embed failure. Deferred
    # because early-stage LTM data overlaps heavily with STM context window.
    """
    if not ltm.available:
        return None
    if not recent_thoughts:
        return None
    anchor = select_attention_anchor(recent_thoughts) or recent_thoughts[-1]
    result_limit = _ltm_result_limit(ltm)
    exclude_cycle_ids = _stm_cycle_ids(recent_thoughts)
    try:
        vec = embed_text(embedding_client, anchor.content, embedding_model)
    except EMBEDDING_EXCEPTIONS:
        return None
    try:
        entries = ltm.search(
            vec,
            top_k=_ltm_search_limit(ltm),
            exclude_cycle_ids=exclude_cycle_ids,
        )
        if not entries:
            return None
        memory_ids, contents = _dedupe_ltm_contents(entries, limit=result_limit)
        if not contents:
            return None
        ltm.mark_accessed(memory_ids)
        return contents
    except LTM_EXCEPTIONS:
        ltm.disconnect()
        return None


def _store_to_ltm(
    ltm: LongTermMemory,
    embedding_client: ModelClient,
    thoughts: list[Thought],
    embedding_model: str,
    cycle_id: int,
) -> None:
    """Embed and store new thoughts into long-term memory."""
    if not ltm.available:
        return
    seen_contents: set[str] = set()
    for t in thoughts:
        clean_content = _sanitize_ltm_content(t.content)
        if not clean_content or clean_content in seen_contents:
            continue
        seen_contents.add(clean_content)
        try:
            vec = embed_text(embedding_client, clean_content, embedding_model)
        except EMBEDDING_EXCEPTIONS:
            continue
        try:
            ltm.store(
                content=clean_content,
                memory_type="episodic",
                embedding=vec,
                source_cycle_id=cycle_id,
            )
        except LTM_EXCEPTIONS:
            ltm.disconnect()
            return


def _dedupe_ltm_contents(
    entries: list[LongTermEntry],
    *,
    limit: int | None = None,
) -> tuple[list[int], list[str]]:
    memory_ids: list[int] = []
    contents: list[str] = []
    seen_contents: set[str] = set()
    for entry in entries:
        clean_content = _sanitize_ltm_content(entry.content)
        if not clean_content or clean_content in seen_contents:
            continue
        seen_contents.add(clean_content)
        memory_ids.append(entry.id)
        contents.append(clean_content)
        if limit is not None and len(contents) >= limit:
            break
    return memory_ids, contents


def _ltm_result_limit(ltm: LongTermMemory) -> int:
    retrieval_top_k = ltm.retrieval_top_k
    if not isinstance(retrieval_top_k, int) or retrieval_top_k <= 0:
        return 5
    return retrieval_top_k


def _ltm_search_limit(ltm: LongTermMemory) -> int:
    return _ltm_result_limit(ltm) * LTM_RETRIEVAL_OVERSAMPLE_FACTOR


def _stm_cycle_ids(context: list[Thought]) -> list[int]:
    cycle_ids = sorted({thought.cycle_id for thought in context if isinstance(thought.cycle_id, int)})
    return cycle_ids


def _conversation_stimulus_ids(stimuli: list[Stimulus]) -> set[str]:
    stimulus_ids: set[str] = set()
    for stimulus in stimuli:
        if stimulus.type != "conversation":
            continue
        if stimulus.stimulus_id:
            stimulus_ids.add(stimulus.stimulus_id)
        merged_ids = stimulus.metadata.get("merged_stimulus_ids")
        if isinstance(merged_ids, list):
            stimulus_ids.update(str(item).strip() for item in merged_ids if str(item).strip())
    return stimulus_ids


def _action_echo_action_ids(stimuli: list[Stimulus]) -> set[str]:
    action_ids: set[str] = set()
    for stimulus in stimuli:
        if str(stimulus.metadata.get("origin") or "").strip() != "action":
            continue
        action_id = str(stimulus.action_id or "").strip()
        if action_id:
            action_ids.add(action_id)
            continue
        source = str(stimulus.source or "").strip()
        if source.startswith("action:"):
            source_action_id = source.removeprefix("action:").strip()
            if source_action_id:
                action_ids.add(source_action_id)
    return action_ids


def _failed_action_echo_count(stimuli: list[Stimulus]) -> int:
    return sum(
        1
        for stimulus in stimuli
        if str(stimulus.metadata.get("origin") or "").strip() == "action"
        and str(stimulus.metadata.get("status") or "").strip() == "failed"
    )


def _detect_runtime_degeneration(recent_thoughts: list[Thought], current_thoughts: list[Thought]) -> bool:
    grouped: dict[int, list[Thought]] = {}
    for thought in [*recent_thoughts, *current_thoughts]:
        grouped.setdefault(thought.cycle_id, []).append(thought)
    recent_cycle_ids = sorted(grouped.keys())[-3:]
    if len(recent_cycle_ids) < 3:
        return False
    cycle_texts = [
        " ".join(
            _normalize_cycle_text(thought.content)
            for thought in grouped[cycle_id]
            if _normalize_cycle_text(thought.content)
        )
        for cycle_id in recent_cycle_ids
    ]
    if any(not text for text in cycle_texts):
        return False
    pairwise = [
        _cycle_text_similarity(cycle_texts[0], cycle_texts[1]),
        _cycle_text_similarity(cycle_texts[0], cycle_texts[2]),
        _cycle_text_similarity(cycle_texts[1], cycle_texts[2]),
    ]
    return all(value >= 0.6 for value in pairwise)


def _normalize_cycle_text(content: str) -> str:
    normalized = re.sub(r"\{action:[^}]+\}", "", str(content))
    normalized = re.sub(r"\(←\s*[^)]+\)", "", normalized)
    return " ".join(normalized.split())


def _cycle_text_similarity(left: str, right: str) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    grams_left = {left[index:index + 2] for index in range(len(left) - 1)}
    grams_right = {right[index:index + 2] for index in range(len(right) - 1)}
    union = len(grams_left | grams_right)
    if union == 0:
        return 0.0
    return len(grams_left & grams_right) / union


def _recent_conversation_summary_request(
    source_name: str,
    existing_summary: str,
    transcript: str,
) -> str:
    existing = existing_summary or "（无）"
    return (
        f"对方名字：{source_name}\n\n"
        f"已有摘要：\n{existing}\n\n"
        f"需要并入的新旧消息（按时间顺序）：\n{transcript}\n\n"
        "请输出一段新的摘要，用来完整替换上面的旧摘要。"
    )


def _recent_conversation_summary_batches(
    entries: list[ConversationEntry],
    source_name: str,
) -> list[str]:
    batches: list[str] = []
    current_lines: list[str] = []
    current_chars = 0
    for entry in entries:
        line = _recent_conversation_summary_line(entry, source_name)
        if not line:
            continue
        line_length = len(line) + 1
        if current_lines and current_chars + line_length > RECENT_CONVERSATION_SUMMARY_BATCH_MAX_CHARS:
            batches.append("\n".join(current_lines))
            current_lines = [line]
            current_chars = len(line)
            continue
        current_lines.append(line)
        current_chars += line_length
    if current_lines:
        batches.append("\n".join(current_lines))
    return batches


def _recent_conversation_summary_line(
    entry: ConversationEntry,
    source_name: str,
) -> str:
    role = str(entry.get("role") or "").strip()
    speaker = "我" if role == "assistant" else source_name
    content = " ".join(str(entry.get("content") or "").split())
    if not content:
        return ""
    max_content_chars = max(1, RECENT_CONVERSATION_SUMMARY_BATCH_MAX_CHARS - len(speaker) - 1)
    content = _clip_recent_conversation_summary_content(content, max_content_chars)
    return f"{speaker}：{content}"


def _clip_recent_conversation_summary_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    if max_chars <= 3:
        return content[:max_chars]
    return content[: max_chars - 3].rstrip() + "..."


def _clean_recent_conversation_summary(raw_summary: JsonValue) -> str:
    summary = " ".join(str(raw_summary or "").split()).strip()
    for prefix in ("摘要：", "对话摘要：", "新的摘要："):
        if summary.startswith(prefix):
            summary = summary[len(prefix):].strip()
    if len(summary) <= RECENT_CONVERSATION_SUMMARY_MAX_CHARS:
        return summary
    return summary[: RECENT_CONVERSATION_SUMMARY_MAX_CHARS - 3].rstrip() + "..."


def _sanitize_ltm_content(content: str) -> str:
    stripped = LTM_ACTION_MARKER_PATTERN.sub("", str(content).strip())
    return " ".join(stripped.split())


def _maybe_reconnect_redis(
    log_file: TextIO | None,
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
    log_file: TextIO | None,
    ltm: LongTermMemory,
    identity: dict[str, str],
    bootstrap_identity: dict[str, str],
    now: float,
    last_attempt: float,
    interval: float,
    habit_memory: HabitMemory | None = None,
) -> tuple[dict[str, str], float]:
    if ltm.available or now - last_attempt < interval:
        return identity, last_attempt
    conn = _connect_pg()
    if conn is None:
        return identity, now
    ltm.attach_connection(conn)
    if habit_memory is not None:
        habit_memory.attach_connection(conn)
    _output(log_file, "PostgreSQL 已恢复")
    return load_identity(conn, bootstrap_identity), now


# -- Connections -----------------------------------------------------------

def _connect_redis() -> redis_lib.Redis | None:
    """Try to connect to Redis using env vars. Returns None on failure."""
    return connect_redis_from_env()


def _connect_pg() -> psycopg.Connection | None:
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

def _print_cycle(log_file: TextIO | None, cycle_id: int, thoughts: list[Thought]) -> None:
    print(f"\n{C_DIM}── C{cycle_id} ──{C_RESET}")
    lines = [f"── C{cycle_id} ──"]
    for t in thoughts:
        color = C_TYPE.get(t.type, "")
        trigger = f" {C_DIM}(← {t.trigger_ref}){C_RESET}" if t.trigger_ref else ""
        print(f"  {color}[{t.type}]{C_RESET} {t.content}{trigger}")
        plain_trigger = f" (← {t.trigger_ref})" if t.trigger_ref else ""
        lines.append(f"  [{t.type}] {t.content}{plain_trigger}")
    _write_log_message(log_file, "\n".join(lines))


def _print_stimuli(log_file: TextIO | None, stimuli: list[Stimulus]) -> None:
    if not stimuli:
        return

    print(f"{C_DIM}刺激{C_RESET}")
    lines = ["刺激"]
    for stimulus in stimuli:
        display_content = _stimulus_display_content(stimulus)
        print(f"  {C_DIM}[{stimulus.type}]{C_RESET} {display_content}")
        lines.append(f"  [{stimulus.type}] {display_content}")
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


def _output(log_file: TextIO | None, text: str) -> None:
    print(text)
    _write_log_message(log_file, text)


def _stimulus_display_content(stimulus: Stimulus) -> str:
    merged_messages = stimulus.metadata.get("merged_messages")
    if stimulus.type != "conversation" or not isinstance(merged_messages, list) or len(merged_messages) <= 1:
        return stimulus.content
    return " | ".join(_merged_message_log_text(message) for message in merged_messages)


def _merged_message_log_text(message: JsonValue) -> str:
    if isinstance(message, dict):
        return _compact_conversation_text(str(message.get("content") or ""))
    return _compact_conversation_text(str(message or ""))


def _print_error(
    log_file: TextIO | None,
    cycle_id: int,
    error: Exception,
    retry_delay: float,
) -> None:
    msg = f"── C{cycle_id} ERROR: {error} (retry in {retry_delay:.1f}s)"
    print(f"\n\033[31m{msg}\033[0m", file=sys.stderr)
    _write_log_message(log_file, msg, level=logging.WARNING, exc_info=True)


def _write_log_message(
    log_file: TextIO | None,
    text: str,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
) -> None:
    logger.log(level, text, exc_info=exc_info)
    if log_file is None:
        return
    log_file.write(text + "\n")
    log_file.flush()


def _publish_event(
    redis_client: redis_lib.Redis | None,
    event_type: str,
    payload: EventPayload,
) -> None:
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


def _as_action_redis(redis_client: redis_lib.Redis | None) -> ActionRedisLike | None:
    return redis_client  # type: ignore[return-value]


def _as_conversation_redis(redis_client: redis_lib.Redis | None) -> ConversationRedisLike | None:
    return redis_client  # type: ignore[return-value]


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


def _open_log(path: str | None, config: dict) -> TextIO | None:
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    if target == resolve_log_path(config, component="core"):
        logger.info("manual cycle log path matches core logger output; reusing logging handler only")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("w", encoding="utf-8")


def _open_prompt_log(config: dict, *, plain_log_path: str | None) -> TextIO:
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


def _install_signal_handler(
    log_file: TextIO | None,
    prompt_log_file: TextIO | None,
    action_manager: ActionManager,
) -> None:
    shutting_down = False

    def handler(sig: int, frame: FrameType | None) -> None:
        nonlocal shutting_down
        _ = sig, frame
        if shutting_down:
            return
        shutting_down = True
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
