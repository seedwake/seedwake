"""Single thought-generation cycle: build prompt, call LLM, parse output."""

import logging
import time
from typing import TextIO

from core.action import ActionRecord
from core.model_client import ModelClient, build_generation_request_log
from core.prompt_builder import build_prompt
from core.stimulus import Stimulus
from core.thought_parser import Thought, fallback_thought, parse_thoughts
from core.types import RecentConversationPrompt

logger = logging.getLogger(__name__)


def run_cycle(
    client: ModelClient,
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    model_config: dict,
    long_term_context: list[str] | None = None,
    stimuli: list[Stimulus] | None = None,
    running_actions: list[ActionRecord] | None = None,
    perception_cues: list[str] | None = None,
    recent_conversations: list[RecentConversationPrompt] | None = None,
    prompt_log_file: TextIO | None = None,
) -> list[Thought]:
    """Execute one cycle and return parsed thoughts."""
    build_started_at = time.perf_counter()
    prompt = build_prompt(
        cycle_id, identity, recent_thoughts, context_window,
        long_term_context=long_term_context,
        stimuli=stimuli,
        running_actions=running_actions,
        perception_cues=perception_cues,
        recent_conversations=recent_conversations,
    )
    build_elapsed_ms = _elapsed_ms(build_started_at)
    logger.info("cycle C%s prompt built in %.1f ms (chars=%d)", cycle_id, build_elapsed_ms, len(prompt))
    write_prompt_log_block(
        prompt_log_file,
        title=f"PROMPT C{cycle_id}",
        prompt=build_generation_request_log(client, prompt),
        emoji="🟢",
    )
    generation_started_at = time.perf_counter()
    raw_output = _call_ollama(client, prompt, model_config)
    generation_elapsed_ms = _elapsed_ms(generation_started_at)
    logger.info("cycle C%s generation finished in %.1f ms (chars=%d)", cycle_id, generation_elapsed_ms, len(raw_output))
    parse_started_at = time.perf_counter()
    thoughts = parse_thoughts(raw_output, cycle_id)
    parse_elapsed_ms = _elapsed_ms(parse_started_at)
    logger.info("cycle C%s thought parsing finished in %.1f ms (count=%d)", cycle_id, parse_elapsed_ms, len(thoughts))

    if not thoughts:
        thoughts = [fallback_thought(raw_output, cycle_id)]
        logger.info("cycle C%s used fallback thought", cycle_id)

    return thoughts


def _call_generation_model(client: ModelClient, prompt: str, model_config: dict) -> str:
    return client.generate_text(prompt, model_config)


def _call_ollama(client: ModelClient, prompt: str, model_config: dict) -> str:
    """Backward-compatible alias for older tests and patches."""
    return _call_generation_model(client, prompt, model_config)


def write_prompt_log_block(
    prompt_log_file: TextIO | None,
    *,
    title: str,
    prompt: str,
    emoji: str,
) -> None:
    if prompt_log_file is None:
        return
    start_banner = emoji * 24
    end_banner = "🔴" * 24
    prompt_log_file.write(
        "\n"
        + start_banner
        + "\n"
        + f"{emoji}{emoji}{emoji} {title} START {emoji}{emoji}{emoji}\n"
        + start_banner
        + "\n"
    )
    prompt_log_file.write(prompt)
    prompt_log_file.write("\n" + end_banner + "\n" + f"🔴🔴🔴 {title} END 🔴🔴🔴\n" + end_banner + "\n\n")
    prompt_log_file.flush()


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0
