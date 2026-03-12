"""Single thought-generation cycle: build prompt, call LLM, parse output."""

import os

from ollama import Client

from core.prompt_builder import build_prompt
from core.thought_parser import Thought, fallback_thought, parse_thoughts

_client: Client | None = None


def get_client() -> Client:
    """Lazy-init Ollama client with optional custom auth header."""
    global _client
    if _client is not None:
        return _client

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    auth_header = os.environ.get("OLLAMA_AUTH_HEADER", "")
    auth_value = os.environ.get("OLLAMA_AUTH_VALUE", "")

    headers = {}
    if auth_header and auth_value:
        headers[auth_header] = auth_value

    _client = Client(host=base_url, headers=headers, timeout=120.0)
    return _client


def run_cycle(
    cycle_id: int,
    identity: dict[str, str],
    recent_thoughts: list[Thought],
    context_window: int,
    model_config: dict,
) -> list[Thought]:
    """Execute one cycle and return parsed thoughts."""
    prompt = build_prompt(cycle_id, identity, recent_thoughts, context_window)
    raw_output = _call_ollama(prompt, model_config)
    thoughts = parse_thoughts(raw_output, cycle_id)

    if not thoughts:
        thoughts = [fallback_thought(raw_output, cycle_id)]

    return thoughts


def _call_ollama(prompt: str, model_config: dict) -> str:
    client = get_client()
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
