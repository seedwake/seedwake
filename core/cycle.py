"""Single thought-generation cycle: build prompt, call LLM, parse output."""

import logging
import time
from typing import TextIO

from core.model_client import ModelClient, build_generation_request_log
from core.prompt_builder import PromptBuildContext, build_prompt
from core.thought_parser import Thought, fallback_thought, parse_thoughts
from core.common_types import (
    elapsed_ms,
)

logger = logging.getLogger(__name__)


def run_cycle(
    client: ModelClient,
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    model_config: dict,
    prompt_context: PromptBuildContext | None = None,
    prompt_log_file: TextIO | None = None,
    images: list[str] | None = None,
) -> list[Thought]:
    """Execute one cycle and return parsed thoughts."""
    resolved_context = prompt_context or PromptBuildContext()
    build_started_at = time.perf_counter()
    prompt = build_prompt(
        cycle_id,
        identity,
        recent_thoughts,
        context_window,
        prompt_context=resolved_context,
    )
    build_elapsed_ms = elapsed_ms(build_started_at)
    logger.info("cycle C%s prompt built in %.1f ms (chars=%d)", cycle_id, build_elapsed_ms, len(prompt))
    write_prompt_log_block(
        prompt_log_file,
        title=f"PROMPT C{cycle_id}",
        prompt=build_generation_request_log(client, prompt, images=images),
        emoji="🟢",
    )
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        generation_started_at = time.perf_counter()
        raw_output = _call_ollama(client, prompt, model_config, images=images)
        generation_elapsed_ms = elapsed_ms(generation_started_at)
        logger.info(
            "cycle C%s generation finished in %.1f ms (chars=%d, attempt=%d)",
            cycle_id, generation_elapsed_ms, len(raw_output), attempt,
        )
        parse_started_at = time.perf_counter()
        thoughts = parse_thoughts(raw_output, cycle_id)
        parse_elapsed_ms = elapsed_ms(parse_started_at)
        logger.info(
            "cycle C%s thought parsing finished in %.1f ms (count=%d)",
            cycle_id, parse_elapsed_ms, len(thoughts),
        )
        if thoughts:
            return thoughts
        if not raw_output.strip():
            fallback_reason = "empty_response"
        elif len(raw_output) < 10:
            fallback_reason = f"too_short ({len(raw_output)} chars)"
        else:
            fallback_reason = f"no_thought_headers_parsed ({len(raw_output)} chars)"
        if attempt < max_attempts:
            logger.warning(
                "cycle C%s generation attempt %d failed (reason=%s), retrying",
                cycle_id, attempt, fallback_reason,
            )
            continue
        logger.warning(
            "cycle C%s used fallback thought after %d attempts (reason=%s, raw_preview=%s)",
            cycle_id, max_attempts, fallback_reason,
            repr(raw_output[:200]) if raw_output else "(empty)",
        )
        return [fallback_thought(raw_output, cycle_id)]
    return [fallback_thought("", cycle_id)]


def _call_generation_model(
    client: ModelClient,
    prompt: str,
    model_config: dict,
    images: list[str] | None = None,
) -> str:
    return client.generate_text(prompt, model_config, images=images)


def _call_ollama(
    client: ModelClient,
    prompt: str,
    model_config: dict,
    images: list[str] | None = None,
) -> str:
    """Backward-compatible alias for older tests and patches."""
    return _call_generation_model(client, prompt, model_config, images=images)


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
