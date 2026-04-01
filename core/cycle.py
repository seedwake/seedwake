"""Single thought-generation cycle: build prompt, call LLM, parse output."""

from dataclasses import dataclass
import logging
import time
from typing import TextIO

from core.action import ActionRecord
from core.model_client import ModelClient, build_generation_request_log
from core.prompt_builder import build_prompt
from core.stimulus import Stimulus
from core.thought_parser import Thought, fallback_thought, parse_thoughts
from core.types import RecentConversationPrompt, elapsed_ms

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CyclePromptContext:
    long_term_context: list[str] | None = None
    note_text: str = ""
    stimuli: list[Stimulus] | None = None
    recent_action_echoes: list[Stimulus] | None = None
    running_actions: list[ActionRecord] | None = None
    perception_cues: list[str] | None = None
    recent_conversations: list[RecentConversationPrompt] | None = None


def run_cycle(
    client: ModelClient,
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    model_config: dict,
    prompt_context: CyclePromptContext | None = None,
    prompt_log_file: TextIO | None = None,
) -> list[Thought]:
    """Execute one cycle and return parsed thoughts."""
    resolved_context = prompt_context or CyclePromptContext()
    build_started_at = time.perf_counter()
    prompt = build_prompt(
        cycle_id, identity, recent_thoughts, context_window,
        long_term_context=resolved_context.long_term_context,
        note_text=resolved_context.note_text,
        stimuli=resolved_context.stimuli,
        recent_action_echoes=resolved_context.recent_action_echoes,
        running_actions=resolved_context.running_actions,
        perception_cues=resolved_context.perception_cues,
        recent_conversations=resolved_context.recent_conversations,
    )
    build_elapsed_ms = elapsed_ms(build_started_at)
    logger.info("cycle C%s prompt built in %.1f ms (chars=%d)", cycle_id, build_elapsed_ms, len(prompt))
    write_prompt_log_block(
        prompt_log_file,
        title=f"PROMPT C{cycle_id}",
        prompt=build_generation_request_log(client, prompt),
        emoji="🟢",
    )
    generation_started_at = time.perf_counter()
    raw_output = _call_ollama(client, prompt, model_config)
    generation_elapsed_ms = elapsed_ms(generation_started_at)
    logger.info("cycle C%s generation finished in %.1f ms (chars=%d)", cycle_id, generation_elapsed_ms, len(raw_output))
    parse_started_at = time.perf_counter()
    thoughts = parse_thoughts(raw_output, cycle_id)
    parse_elapsed_ms = elapsed_ms(parse_started_at)
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
