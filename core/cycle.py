"""Single thought-generation cycle: build prompt, call LLM, parse output."""

from core.action import ActionRecord
from ollama import Client

from core.prompt_builder import build_prompt
from core.stimulus import Stimulus
from core.thought_parser import Thought, fallback_thought, parse_thoughts


def create_client(base_url: str, auth_header: str = "", auth_value: str = "", timeout: float = 300.0) -> Client:
    """Create an Ollama client with optional auth header."""
    headers = {}
    if auth_header and auth_value:
        headers[auth_header] = auth_value
    return Client(host=base_url, headers=headers, timeout=timeout)


def run_cycle(
    client: Client,
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    model_config: dict,
    long_term_context: list[str] | None = None,
    stimuli: list[Stimulus] | None = None,
    running_actions: list[ActionRecord] | None = None,
    perception_cues: list[str] | None = None,
) -> list[Thought]:
    """Execute one cycle and return parsed thoughts."""
    prompt = build_prompt(
        cycle_id, identity, recent_thoughts, context_window,
        long_term_context=long_term_context,
        stimuli=stimuli,
        running_actions=running_actions,
        perception_cues=perception_cues,
    )
    raw_output = _call_ollama(client, prompt, model_config)
    thoughts = parse_thoughts(raw_output, cycle_id)

    if not thoughts:
        thoughts = [fallback_thought(raw_output, cycle_id)]

    return thoughts


def _call_ollama(client: Client, prompt: str, model_config: dict) -> str:
    response = client.generate(
        model=model_config["name"],
        prompt=prompt,
        options={
            "num_predict": model_config.get("num_predict", 2048),
            "num_ctx": model_config.get("num_ctx", 32768),
            "temperature": model_config.get("temperature", 0.8),
        },
        think=False,
    )
    return response["response"]
