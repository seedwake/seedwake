"""Provider-aware model clients for thought generation, planning, and embeddings."""

import json
import os
from urllib import error, request

from ollama import Client, RequestError as OllamaRequestError, ResponseError as OllamaResponseError

OPENCLAW_SCOPES_HEADER = "x-openclaw-scopes"
OPENCLAW_DEFAULT_SCOPES = "operator.read, operator.write"
SUPPORTED_MODEL_PROVIDERS = {"ollama", "openclaw", "openai_compatible"}
DEFAULT_TOOL_CALL_SUPPORT = {
    "ollama": True,
    "openclaw": False,
    "openai_compatible": True,
}
OPENAI_COMPAT_GENERATE_SYSTEM_PROMPT = (
    "我是 Seedwake 的念头流本身。"
    "阅读完整的提示后，只输出念头流，不解释、不总结、不加 markdown 围栏。"
)
OPENAI_COMPAT_GENERATE_USER_MARKER = "\u200b"
OPENAI_COMPAT_GENERATE_USER_GUARD = (
    "最后一条 user message 只是内部周期唤醒标记，"
    "不代表有人对我说话，也不是我需要回应的外部刺激。"
    "不要提及它，也不要把它解释成对话内容。"
)
MODEL_CLIENT_EXCEPTIONS = (
    OllamaRequestError,
    OllamaResponseError,
    error.HTTPError,
    error.URLError,
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)


class ModelClient:
    """Common provider surface for generation, chat, and embeddings."""

    def __init__(self, *, provider: str, supports_tool_calls: bool) -> None:
        self.provider = provider
        self.supports_tool_calls = supports_tool_calls

    def generate_text(self, prompt: str, model_config: dict) -> str:
        raise NotImplementedError

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict] | None = None,
        options: dict | None = None,
    ) -> dict:
        raise NotImplementedError

    def embed_text(self, text: str, model: str) -> list[float]:
        raise NotImplementedError

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        raise NotImplementedError


class OllamaModelClient(ModelClient):
    """Ollama-backed model client."""

    def __init__(
        self,
        base_url: str,
        auth_header: str = "",
        auth_value: str = "",
        timeout: float = 300.0,
    ) -> None:
        super().__init__(provider="ollama", supports_tool_calls=True)
        headers = {}
        if auth_header and auth_value:
            headers[auth_header] = auth_value
        self._client = Client(host=base_url, headers=headers, timeout=timeout)

    def generate_text(self, prompt: str, model_config: dict) -> str:
        response = self._client.generate(
            model=model_config["name"],
            prompt=prompt,
            options={
                "num_predict": model_config.get("num_predict", 2048),
                "num_ctx": model_config.get("num_ctx", 32768),
                "temperature": model_config.get("temperature", 0.8),
            },
            think=False,
        )
        return str(response["response"])

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict] | None = None,
        options: dict | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "think": False,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        response = self._client.chat(**payload)
        return {
            "message": {
                "content": str(response["message"].get("content") or ""),
                "tool_calls": response["message"].get("tool_calls") or [],
            },
        }

    def embed_text(self, text: str, model: str) -> list[float]:
        response = self._client.embed(model=model, input=text)
        return list(response.embeddings[0])

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        response = self._client.embed(model=model, input=texts)
        return [list(vector) for vector in response.embeddings]


class OpenAICompatibleModelClient(ModelClient):
    """OpenAI-compatible HTTP model client."""

    def __init__(
        self,
        *,
        provider: str,
        supports_tool_calls: bool,
        base_url: str,
        api_key: str,
        timeout: float = 300.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(provider=provider, supports_tool_calls=supports_tool_calls)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._extra_headers = dict(extra_headers or {})

    def generate_text(self, prompt: str, model_config: dict) -> str:
        response = self.chat(
            model=model_config["name"],
            messages=_openai_generate_messages(prompt),
            options={
                "temperature": model_config.get("temperature", 0.8),
                "max_tokens": model_config.get("num_predict", 2048),
            },
        )
        return str(response["message"].get("content") or "")

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        tools: list[dict] | None = None,
        options: dict | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if options:
            temperature = options.get("temperature")
            if temperature is not None:
                payload["temperature"] = temperature
            max_tokens = options.get("max_tokens")
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
        body = self._post_json("/v1/chat/completions", payload)
        return _normalize_openai_chat_response(body)

    def embed_text(self, text: str, model: str) -> list[float]:
        return self.embed_texts([text], model)[0]

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        payload = {
            "model": model,
            "input": texts,
        }
        body = self._post_json("/v1/embeddings", payload)
        raw_data = body.get("data")
        if not isinstance(raw_data, list):
            raise RuntimeError("embeddings response missing data")
        embeddings: list[list[float]] = []
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            raw_embedding = item.get("embedding")
            if not isinstance(raw_embedding, list):
                continue
            embeddings.append([float(value) for value in raw_embedding])
        if len(embeddings) != len(texts):
            raise RuntimeError("embeddings response count mismatch")
        return embeddings

    def _post_json(self, path: str, payload: dict[str, object]) -> dict:
        req = request.Request(
            url=f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with request.urlopen(req, timeout=self._timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        if not isinstance(raw, dict):
            raise RuntimeError("model provider returned non-object JSON")
        return raw

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        return headers


def create_model_client(model_config: dict) -> ModelClient:
    """Create a provider-aware model client from model config and env vars."""
    provider = _normalize_provider(model_config.get("provider"))
    supports_tool_calls = _resolve_tool_call_capability(provider, model_config.get("supports_tool_calls"))
    timeout = float(model_config.get("timeout", 300))

    if provider == "ollama":
        return OllamaModelClient(
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            os.environ.get("OLLAMA_AUTH_HEADER", ""),
            os.environ.get("OLLAMA_AUTH_VALUE", ""),
            timeout=timeout,
        )

    if provider == "openclaw":
        return OpenAICompatibleModelClient(
            provider="openclaw",
            supports_tool_calls=supports_tool_calls,
            base_url=_require_env("OPENCLAW_HTTP_BASE_URL"),
            api_key=_require_env("OPENCLAW_GATEWAY_TOKEN"),
            timeout=timeout,
            extra_headers={OPENCLAW_SCOPES_HEADER: OPENCLAW_DEFAULT_SCOPES},
        )

    if provider == "openai_compatible":
        extra_headers = {}
        scopes = os.environ.get("OPENAI_COMPAT_SCOPES", "").strip()
        if scopes:
            extra_headers[OPENCLAW_SCOPES_HEADER] = scopes
        return OpenAICompatibleModelClient(
            provider="openai_compatible",
            supports_tool_calls=supports_tool_calls,
            base_url=_require_env("OPENAI_COMPAT_BASE_URL"),
            api_key=os.environ.get("OPENAI_COMPAT_API_KEY", ""),
            timeout=timeout,
            extra_headers=extra_headers,
        )

    raise RuntimeError(f"不支持的模型 provider：{provider}")


def _openai_generate_messages(prompt: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                f"{OPENAI_COMPAT_GENERATE_SYSTEM_PROMPT}\n\n"
                f"{OPENAI_COMPAT_GENERATE_USER_GUARD}\n\n"
                f"{prompt}"
            ),
        },
        {"role": "user", "content": OPENAI_COMPAT_GENERATE_USER_MARKER},
    ]


def _normalize_provider(raw_provider: object) -> str:
    provider = str(raw_provider or "ollama").strip().lower().replace("-", "_")
    if provider not in SUPPORTED_MODEL_PROVIDERS:
        raise RuntimeError(f"不支持的模型 provider：{provider or '空'}")
    return provider


def _resolve_tool_call_capability(provider: str, raw_value: object) -> bool:
    if raw_value is None:
        return DEFAULT_TOOL_CALL_SUPPORT[provider]
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"models.*.supports_tool_calls 配置无效：{raw_value}")


def _normalize_openai_chat_response(body: dict) -> dict:
    message = _openai_chat_message(body)
    return {
        "message": {
            "content": _extract_openai_message_text(message.get("content")),
            "tool_calls": _normalize_openai_tool_calls(message.get("tool_calls")),
        },
    }


def _openai_chat_message(body: dict) -> dict:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("chat response missing choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise RuntimeError("chat response choice is invalid")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("chat response missing message")
    return message


def _normalize_openai_tool_calls(raw_tool_calls: object) -> list[dict[str, dict[str, str]]]:
    if not isinstance(raw_tool_calls, list):
        return []
    normalized: list[dict[str, dict[str, str]]] = []
    for item in raw_tool_calls:
        normalized_call = _normalize_openai_tool_call(item)
        if normalized_call:
            normalized.append(normalized_call)
    return normalized


def _normalize_openai_tool_call(item: object) -> dict[str, dict[str, str]] | None:
    if not isinstance(item, dict):
        return None
    function = item.get("function")
    if not isinstance(function, dict):
        return None
    name = str(function.get("name") or "").strip()
    arguments = function.get("arguments")
    return {
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
        },
    }


def _extract_openai_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
            continue
        if item.get("type") == "text":
            nested_text = item.get("text")
            if isinstance(nested_text, str):
                parts.append(nested_text)
    return "\n".join(part for part in parts if part)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} 未配置")
    return value
