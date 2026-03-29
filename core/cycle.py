"""Single thought-generation cycle: build prompt, call LLM, parse output."""

from core.action import ActionRecord
from core.model_client import ModelClient
from core.prompt_builder import build_prompt
from core.stimulus import Stimulus
from core.thought_parser import Thought, fallback_thought, parse_thoughts


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
    prompt_log_file=None,
) -> list[Thought]:
    """Execute one cycle and return parsed thoughts."""
    prompt = build_prompt(
        cycle_id, identity, recent_thoughts, context_window,
        long_term_context=long_term_context,
        stimuli=stimuli,
        running_actions=running_actions,
        perception_cues=perception_cues,
    )
    _write_prompt_log(prompt_log_file, cycle_id, prompt)
    raw_output = _call_ollama(client, prompt, model_config)
    thoughts = parse_thoughts(raw_output, cycle_id)

    if not thoughts:
        thoughts = [fallback_thought(raw_output, cycle_id)]

    return thoughts


def _call_generation_model(client: ModelClient, prompt: str, model_config: dict) -> str:
    return client.generate_text(prompt, model_config)


def _call_ollama(client: ModelClient, prompt: str, model_config: dict) -> str:
    """Backward-compatible alias for older tests and patches."""
    return _call_generation_model(client, prompt, model_config)


def _write_prompt_log(prompt_log_file, cycle_id: int, prompt: str) -> None:
    if prompt_log_file is None:
        return
    banner = "🔥" * 24
    prompt_log_file.write(
        "\n"
        + banner
        + "\n"
        + f"🔥🔥🔥 PROMPT C{cycle_id} 🔥🔥🔥\n"
        + banner
        + "\n"
    )
    prompt_log_file.write(prompt)
    prompt_log_file.write("\n" + banner + "\n\n")
    prompt_log_file.flush()
