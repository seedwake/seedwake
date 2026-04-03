"""Provider-aware model clients for thought generation, planning, and embeddings."""

import json
import logging
import os
import time
from collections.abc import Iterator
from typing import Protocol
from urllib import error, request

from ollama import (
    ChatResponse,
    Client,
    GenerateResponse,
    RequestError as OllamaRequestError,
    ResponseError as OllamaResponseError,
)
from core.common_types import JsonObject, JsonValue, elapsed_ms

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
logger = logging.getLogger(__name__)


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
        messages: list[JsonObject],
        tools: list[JsonObject] | None = None,
        options: dict | None = None,
    ) -> JsonObject:
        raise NotImplementedError

    def embed_text(self, text: str, model: str) -> list[float]:
        raise NotImplementedError

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        raise NotImplementedError


def build_generation_request_log(client: ModelClient, prompt: str) -> str:
    if client.provider not in {"openclaw", "openai_compatible"}:
        return prompt
    return _format_generation_messages_for_log(_openai_generate_messages(prompt))


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
        started_at = time.perf_counter()
        status = "failed"
        think_enabled = _resolve_generate_think_flag(model_config.get("think"))
        try:
            response = _normalize_ollama_generate_response(self._client.generate(
                model=model_config["name"],
                prompt=prompt,
                options={
                    "num_predict": model_config.get("num_predict", 2048),
                    "num_ctx": model_config.get("num_ctx", 32768),
                    "temperature": model_config.get("temperature", 0.8),
                },
                think=think_enabled,
            ))
            text = _ollama_generate_text(response)
            status = "ok"
            return text
        finally:
            _log_model_call(
                provider=self.provider,
                operation="generate",
                model=model_config["name"],
                started_at=started_at,
                status=status,
                detail=f"prompt_chars={len(prompt)}, think={think_enabled}",
            )

    def chat(
        self,
        *,
        model: str,
        messages: list[JsonObject],
        tools: list[JsonObject] | None = None,
        options: dict | None = None,
    ) -> JsonObject:
        started_at = time.perf_counter()
        status = "failed"
        try:
            payload: JsonObject = {
                "model": model,
                "messages": messages,
                "think": False,
            }
            if tools:
                payload["tools"] = tools
            if options:
                payload["options"] = _normalize_ollama_chat_options(options)
            response = _normalize_ollama_chat_response(self._client.chat(**payload))  # type: ignore[arg-type]
            message = _ollama_chat_message(response)
            normalized = {
                "message": {
                    "content": str(message.get("content") or ""),
                    "tool_calls": message.get("tool_calls") or [],
                },
            }
            status = "ok"
            return normalized
        finally:
            _log_model_call(
                provider=self.provider,
                operation="chat",
                model=model,
                started_at=started_at,
                status=status,
                detail=f"messages={len(messages)}, tools={len(tools or [])}",
            )

    def embed_text(self, text: str, model: str) -> list[float]:
        started_at = time.perf_counter()
        status = "failed"
        try:
            response = self._client.embed(model=model, input=text)
            embedding = list(response.embeddings[0])
            status = "ok"
            return embedding
        finally:
            _log_model_call(
                provider=self.provider,
                operation="embed",
                model=model,
                started_at=started_at,
                status=status,
                detail=f"texts=1, chars={len(text)}",
            )

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        started_at = time.perf_counter()
        status = "failed"
        try:
            response = self._client.embed(model=model, input=texts)
            embeddings = [list(vector) for vector in response.embeddings]
            status = "ok"
            return embeddings
        finally:
            _log_model_call(
                provider=self.provider,
                operation="embed",
                model=model,
                started_at=started_at,
                status=status,
                detail=f"texts={len(texts)}, chars={sum(len(text) for text in texts)}",
            )


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
        started_at = time.perf_counter()
        status = "failed"
        try:
            body = self._chat_completions(
                model=model_config["name"],
                messages=_openai_generate_messages(prompt),
                tools=None,
                options={
                    "temperature": model_config.get("temperature", 0.8),
                    "max_tokens": model_config.get("num_predict", 2048),
                },
            )
            response = _normalize_openai_chat_response(body)
            text = _openai_message_content(response)
            status = "ok"
            return text
        finally:
            _log_model_call(
                provider=self.provider,
                operation="generate",
                model=model_config["name"],
                started_at=started_at,
                status=status,
                detail=f"prompt_chars={len(prompt)}",
            )

    def chat(
        self,
        *,
        model: str,
        messages: list[JsonObject],
        tools: list[JsonObject] | None = None,
        options: dict | None = None,
    ) -> JsonObject:
        started_at = time.perf_counter()
        status = "failed"
        try:
            body = self._chat_completions(
                model=model,
                messages=messages,
                tools=tools,
                options=options,
            )
            response = _normalize_openai_chat_response(body)
            status = "ok"
            return response
        finally:
            _log_model_call(
                provider=self.provider,
                operation="chat",
                model=model,
                started_at=started_at,
                status=status,
                detail=f"messages={len(messages)}, tools={len(tools or [])}",
            )

    def embed_text(self, text: str, model: str) -> list[float]:
        return self.embed_texts([text], model)[0]

    def embed_texts(self, texts: list[str], model: str) -> list[list[float]]:
        started_at = time.perf_counter()
        status = "failed"
        try:
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
            status = "ok"
            return embeddings
        finally:
            _log_model_call(
                provider=self.provider,
                operation="embed",
                model=model,
                started_at=started_at,
                status=status,
                detail=f"texts={len(texts)}, chars={sum(len(text) for text in texts)}",
            )

    def _chat_completions(
        self,
        *,
        model: str,
        messages: list[JsonObject],
        tools: list[JsonObject] | None,
        options: dict | None,
    ) -> JsonObject:
        payload: JsonObject = {
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
        return self._post_json("/v1/chat/completions", payload)

    def _post_json(self, path: str, payload: JsonObject) -> JsonObject:
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
        return _coerce_json_object(raw)

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


def _normalize_ollama_chat_options(options: dict) -> dict:
    normalized = dict(options)
    if "num_predict" not in normalized and "max_tokens" in normalized:
        normalized["num_predict"] = normalized.pop("max_tokens")
    return normalized


class _OllamaGenerateResponse(Protocol):
    response: str | None


class _OllamaChatResponse(Protocol):
    message: JsonValue


def _normalize_ollama_generate_response(
    response: _OllamaGenerateResponse | JsonObject | Iterator[GenerateResponse],
) -> _OllamaGenerateResponse | JsonObject:
    if isinstance(response, dict):
        return response
    if hasattr(response, "response"):
        return response  # type: ignore[return-value]
    raise RuntimeError("ollama generate streaming response is not supported")


def _normalize_ollama_chat_response(
    response: _OllamaChatResponse | JsonObject | Iterator[ChatResponse],
) -> _OllamaChatResponse | JsonObject:
    if isinstance(response, dict):
        return response
    if hasattr(response, "message"):
        return response  # type: ignore[return-value]
    raise RuntimeError("ollama chat streaming response is not supported")


def _ollama_generate_text(response: _OllamaGenerateResponse | JsonObject) -> str:
    raw_text = getattr(response, "response", None)
    if raw_text is None and isinstance(response, dict):
        raw_text = response.get("response")
    return str(raw_text or "")


def _ollama_chat_message(response: _OllamaChatResponse | JsonObject) -> JsonObject:
    message = getattr(response, "message", None)
    if message is None and isinstance(response, dict):
        message = response.get("message")
    if message is None:
        raise RuntimeError(f"ollama chat response has no message field: {type(response).__name__}")
    if isinstance(message, dict):
        return _coerce_json_object(message)
    # Ollama Message object — extract fields manually
    return {
        "content": str(getattr(message, "content", "") or ""),
        "tool_calls": _normalize_ollama_tool_calls(getattr(message, "tool_calls", None)),
    }


def _normalize_ollama_tool_calls(raw_tool_calls: object) -> list[JsonObject]:
    if not raw_tool_calls:
        return []
    if not isinstance(raw_tool_calls, list):
        return []
    normalized: list[JsonObject] = []
    for item in raw_tool_calls:
        if isinstance(item, dict):
            normalized.append(_coerce_json_object(item))
            continue
        # Ollama ToolCall object
        function = getattr(item, "function", None)
        if function is None:
            continue
        normalized.append({
            "function": {
                "name": str(getattr(function, "name", "") or ""),
                "arguments": getattr(function, "arguments", None) or {},
            },
        })
    return normalized


def _log_model_call(
    *,
    provider: str,
    operation: str,
    model: str,
    started_at: float,
    status: str,
    detail: str,
) -> None:
    logger.info(
        "model call [%s/%s] %s finished in %.1f ms (status=%s, %s)",
        provider,
        operation,
        model,
        elapsed_ms(started_at),
        status,
        detail,
    )


def _openai_generate_messages(prompt: str) -> list[JsonObject]:
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


def _openai_message_content(response: JsonObject) -> str:
    message = response.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "")


def _format_generation_messages_for_log(messages: list[JsonObject]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "message").upper()
        content = _generation_log_message_content(str(message.get("content") or ""))
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _generation_log_message_content(content: str) -> str:
    if content == OPENAI_COMPAT_GENERATE_USER_MARKER:
        return "\\u200b"
    return content


def _normalize_provider(raw_provider: JsonValue) -> str:
    provider = str(raw_provider or "ollama").strip().lower().replace("-", "_")
    if provider not in SUPPORTED_MODEL_PROVIDERS:
        raise RuntimeError(f"不支持的模型 provider：{provider or '空'}")
    return provider


def _resolve_tool_call_capability(provider: str, raw_value: JsonValue) -> bool:
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


def _resolve_generate_think_flag(raw_value: JsonValue) -> bool:
    if raw_value is None:
        return False
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"models.*.think 配置无效：{raw_value}")


def _normalize_openai_chat_response(body: JsonObject) -> JsonObject:
    message = _openai_chat_message(body)
    return {
        "message": {
            "content": _extract_openai_message_text(message.get("content")),
            "tool_calls": _normalize_openai_tool_calls(message.get("tool_calls")),
        },
    }


def _openai_chat_message(body: JsonObject) -> JsonObject:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("chat response missing choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise RuntimeError("chat response choice is invalid")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("chat response missing message")
    return _coerce_json_object(message)


def _normalize_openai_tool_calls(raw_tool_calls: JsonValue) -> list[JsonObject]:
    if not isinstance(raw_tool_calls, list):
        return []
    normalized: list[JsonObject] = []
    for item in raw_tool_calls:
        normalized_call = _normalize_openai_tool_call(item)
        if normalized_call:
            normalized.append(normalized_call)
    return normalized


def _normalize_openai_tool_call(item: JsonValue) -> JsonObject | None:
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


def _extract_openai_message_text(content: JsonValue) -> str:
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


def _coerce_json_object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise RuntimeError(
            f"model provider returned non-object JSON: type={type(value).__name__}, "
            f"repr={repr(value)[:200]}"
        )
    return {str(key): item for key, item in value.items()}
