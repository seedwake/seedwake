"""OpenClaw Gateway transport for delegated action execution."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, AsyncContextManager, Protocol, cast
from urllib import error, request
from uuid import uuid4

from ollama import RequestError as OllamaRequestError, ResponseError as OllamaResponseError
from core.types import ActionResultEnvelope, JsonObject, JsonValue

if TYPE_CHECKING:
    from core.action import ActionRecord

try:
    from websockets import exceptions as ws_exceptions
except ImportError:
    ws_exceptions = None

ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
CONNECT_TIMEOUT_SECONDS = 10
OPENCLAW_TRANSPORT_EXCEPTIONS = (
    OllamaRequestError,
    OllamaResponseError,
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
    json.JSONDecodeError,
)
if ws_exceptions is not None:
    OPENCLAW_TRANSPORT_EXCEPTIONS = (
        *OPENCLAW_TRANSPORT_EXCEPTIONS,
        ws_exceptions.ConnectionClosed,
        ws_exceptions.ConcurrencyError,
        ws_exceptions.NegotiationError,
        ws_exceptions.ProtocolError,
        ws_exceptions.ProxyError,
        ws_exceptions.SecurityError,
        ws_exceptions.WebSocketProtocolError,
    )
RESULT_SYSTEM_PROMPT = """\
You are a worker for Seedwake.
Finish the task and return JSON only:
{"ok": true, "summary": "...", "data": {}, "error": null}
Use the exact data field names required by the task instructions.
Do not rename fields, add sibling fields, or replace the requested shape with a different one.
If a requested field is unavailable, use "", [], {}, false, or null as appropriate instead of inventing a new key.
Do not wrap the JSON in markdown fences.
"""
logger = logging.getLogger(__name__)


class OpenClawUnavailableError(RuntimeError):
    """Transport-level OpenClaw unavailability."""


class _GatewaySocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str: ...


class _WebsocketsModule(Protocol):
    def connect(self, uri: str, *, max_size: int) -> AsyncContextManager[_GatewaySocket]: ...


class OpenClawGatewayExecutor:
    """Delegates actions to OpenClaw over Gateway WS, with optional HTTP fallback."""

    def __init__(
        self,
        gateway_url: str,
        gateway_token: str,
        worker_agent_id: str,
        ops_worker_agent_id: str,
        session_key_prefix: str,
        *,
        http_base_url: str = "",
        use_http_fallback: bool = False,
        device_identity_path: str | None = None,
    ) -> None:
        self._gateway_url = gateway_url.strip()
        self._gateway_token = gateway_token.strip()
        self._worker_agent_id = worker_agent_id.strip()
        self._ops_worker_agent_id = ops_worker_agent_id.strip()
        self._session_key_prefix = session_key_prefix.strip()
        self._http_base_url = http_base_url.strip()
        self._use_http_fallback = use_http_fallback
        self._device_identity_path = device_identity_path or "data/openclaw/device.json"

    def execute(self, action: "ActionRecord") -> ActionResultEnvelope:
        started_at = time.perf_counter()
        transport = "unavailable"
        status = "failed"
        if not self._gateway_url:
            raise OpenClawUnavailableError("OPENCLAW_GATEWAY_URL 未配置")
        if not self._gateway_token:
            raise OpenClawUnavailableError("OPENCLAW_GATEWAY_TOKEN 未配置")

        try:
            transport = "ws"
            result = asyncio.run(self._execute_ws(action))
            status = _result_status(result)
            return result
        except OPENCLAW_TRANSPORT_EXCEPTIONS as exc:
            if not self._use_http_fallback:
                raise OpenClawUnavailableError(str(exc)) from exc
            try:
                transport = "http_fallback"
                result = self._execute_http(action, exc)
                status = _result_status(result)
                return result
            except OPENCLAW_TRANSPORT_EXCEPTIONS as fallback_exc:
                raise OpenClawUnavailableError(str(fallback_exc)) from fallback_exc
        finally:
            logger.info(
                "openclaw action %s [%s] finished in %.1f ms (status=%s, transport=%s)",
                action.action_id,
                action.type,
                _elapsed_ms(started_at),
                status,
                transport,
            )

    async def _execute_ws(self, action: "ActionRecord") -> ActionResultEnvelope:
        websockets = _import_websockets()
        identity = _load_or_create_device_identity(self._device_identity_path)

        async with websockets.connect(self._gateway_url, max_size=25 * 1024 * 1024) as ws:
            client = _GatewayRpcClient(ws)
            try:
                challenge = await client.recv_event("connect.challenge", CONNECT_TIMEOUT_SECONDS)
                nonce = str(challenge.get("payload", {}).get("nonce", "")).strip()
                if not nonce:
                    raise RuntimeError("Gateway connect.challenge 缺少 nonce")

                connect_id = uuid4().hex
                await client.send_request(
                    connect_id,
                    "connect",
                    self._build_connect_params(identity, nonce),
                )
                connect_res = await client.recv_response(connect_id, CONNECT_TIMEOUT_SECONDS)
                if not connect_res.get("ok"):
                    raise RuntimeError(_format_gateway_error(connect_res))

                request_id = uuid4().hex
                worker_agent_id = self._resolve_worker_agent_id(action)
                session_key = f"agent:{worker_agent_id}:{self._session_key_prefix}:{action.action_id}"
                timeout_seconds = int(action.timeout_seconds)
                await client.send_request(
                    request_id,
                    "agent",
                    {
                        "message": str(action.request.get("task") or action.source_content),
                        "agentId": worker_agent_id,
                        "sessionKey": session_key,
                        "idempotencyKey": action.action_id,
                        "timeout": timeout_seconds,
                        "extraSystemPrompt": RESULT_SYSTEM_PROMPT,
                    },
                )
                accepted = await client.recv_response(request_id, CONNECT_TIMEOUT_SECONDS)
                if not accepted.get("ok"):
                    raise RuntimeError(_format_gateway_error(accepted))
                accepted_payload = accepted.get("payload") or {}
                if accepted_payload.get("status") != "accepted":
                    return _normalize_agent_final(
                        accepted,
                        run_id=accepted_payload.get("runId"),
                        session_key=session_key,
                    )

                run_id = str(accepted_payload.get("runId") or "")
                wait_request_id = uuid4().hex
                await client.send_request(
                    wait_request_id,
                    "agent.wait",
                    {
                        "runId": run_id,
                        "timeoutMs": max(1000, (timeout_seconds + 1) * 1000),
                    },
                )
                wait_res = await client.recv_response(wait_request_id, timeout_seconds + 2)
                if not wait_res.get("ok"):
                    raise RuntimeError(_format_gateway_error(wait_res))
                wait_payload = wait_res.get("payload") or {}
                if wait_payload.get("status") == "timeout":
                    await _abort_session(client, session_key, run_id, timeout_seconds=5)
                    return _build_gateway_result(
                        ok=False,
                        summary="行动超时",
                        data={},
                        error_detail="timeout",
                        run_id=run_id or None,
                        session_key=session_key,
                        transport="ws",
                    )

                final_frame = await client.recv_response(request_id, 5)
                return _normalize_agent_final(final_frame, run_id=run_id, session_key=session_key)
            finally:
                await client.close()

    def _execute_http(self, action: "ActionRecord", ws_error: Exception) -> ActionResultEnvelope:
        if not self._http_base_url:
            raise RuntimeError(f"WS 失败且未配置 HTTP fallback: {ws_error}") from ws_error

        worker_agent_id = self._resolve_worker_agent_id(action)
        session_key = f"agent:{worker_agent_id}:{self._session_key_prefix}:{action.action_id}"
        payload = {
            "model": f"openclaw/{worker_agent_id}",
            "instructions": RESULT_SYSTEM_PROMPT,
            "input": str(action.request.get("task") or action.source_content),
        }
        req = request.Request(
            url=f"{self._http_base_url.rstrip('/')}/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._gateway_token}",
                "Content-Type": "application/json",
                "x-openclaw-scopes": "operator.read, operator.write",
                "x-openclaw-session-key": session_key,
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=int(action.timeout_seconds) + 10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return _build_gateway_result(
                ok=False,
                summary=f"OpenClaw HTTP fallback 失败: {exc.code}",
                data={},
                error_detail=detail,
                run_id=None,
                session_key=session_key,
                transport="http",
            )

        text = _extract_responses_api_text(body)
        normalized = _normalize_worker_text(text)
        normalized["session_key"] = session_key
        normalized["transport"] = "http"
        return normalized

    def _resolve_worker_agent_id(self, action: "ActionRecord") -> str:
        request_worker = str(action.request.get("worker_agent_id") or "").strip()
        if request_worker:
            return request_worker
        if action.type in {"system_change", "file_modify"} and self._ops_worker_agent_id:
            return self._ops_worker_agent_id
        return self._worker_agent_id

    def _build_connect_params(self, identity: dict[str, str], nonce: str) -> dict:
        signed_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        payload = _build_device_auth_payload(
            device_id=identity["device_id"],
            client_id="gateway-client",
            client_mode="backend",
            role="operator",
            scopes=["operator.read", "operator.write"],
            signed_at_ms=signed_at_ms,
            token=self._gateway_token,
            nonce=nonce,
            platform=os.uname().sysname.lower(),
        )
        signature = _sign_device_payload(identity["private_key_pem"], payload)
        return {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": "gateway-client",
                "displayName": "Seedwake",
                "version": "0.3.0",
                "platform": os.uname().sysname.lower(),
                "mode": "backend",
            },
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "caps": [],
            "commands": [],
            "permissions": {},
            "auth": {"token": self._gateway_token},
            "userAgent": "seedwake/0.3.0",
            "device": {
                "id": identity["device_id"],
                "publicKey": _public_key_raw_base64url_from_pem(identity["public_key_pem"]),
                "signature": signature,
                "signedAt": signed_at_ms,
                "nonce": nonce,
            },
        }


class _GatewayRpcClient:
    def __init__(self, ws: _GatewaySocket) -> None:
        self._ws = ws
        self._responses: dict[str, asyncio.Queue] = {}
        self._events: dict[str, asyncio.Queue] = {}
        self._sentinel = object()
        self._reader_error: Exception | None = None
        self._reader_task = asyncio.create_task(self._reader())

    async def close(self) -> None:
        if self._reader_task.done():
            return
        self._reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader_task

    async def send_request(self, request_id: str, method: str, params: dict) -> None:
        await self._ws.send(json.dumps({
            "type": "req",
            "id": request_id,
            "method": method,
            "params": params,
        }, ensure_ascii=False))

    async def recv_event(self, event_name: str, timeout_seconds: int) -> dict:
        queue = self._events.setdefault(event_name, asyncio.Queue())
        frame = await self._recv_from_queue(queue, timeout_seconds)
        return frame

    async def recv_response(self, request_id: str, timeout_seconds: int) -> dict:
        queue = self._responses.setdefault(request_id, asyncio.Queue())
        frame = await self._recv_from_queue(queue, timeout_seconds)
        return frame

    async def _recv_from_queue(self, queue: asyncio.Queue, timeout_seconds: int) -> dict:
        frame = await asyncio.wait_for(queue.get(), timeout_seconds)
        if frame is self._sentinel:
            raise RuntimeError("Gateway 连接已关闭") from self._reader_error
        return frame

    async def _reader(self) -> None:
        try:
            while True:
                await self._route_frame(json.loads(await self._ws.recv()))
        except asyncio.CancelledError:
            raise
        except OPENCLAW_TRANSPORT_EXCEPTIONS as exc:
            self._reader_error = exc
        finally:
            for queue in [*self._responses.values(), *self._events.values()]:
                await queue.put(self._sentinel)

    async def _route_frame(self, frame: dict) -> None:
        frame_type = frame.get("type")
        if frame_type == "res":
            await self._queue_response(frame)
            return
        if frame_type == "event":
            await self._queue_event(frame)

    async def _queue_response(self, frame: dict) -> None:
        request_id = str(frame.get("id") or "")
        if not request_id:
            return
        queue = self._responses.setdefault(request_id, asyncio.Queue())
        await queue.put(frame)

    async def _queue_event(self, frame: dict) -> None:
        event_name = str(frame.get("event") or "")
        if not event_name:
            return
        queue = self._events.setdefault(event_name, asyncio.Queue())
        await queue.put(frame)


async def _abort_session(client: _GatewayRpcClient, session_key: str, run_id: str, timeout_seconds: int) -> None:
    if not session_key:
        return
    request_id = uuid4().hex
    await client.send_request(
        request_id,
        "sessions.abort",
        {
            "key": session_key,
            "runId": run_id or None,
        },
    )
    try:
        await client.recv_response(request_id, timeout_seconds)
    except OPENCLAW_TRANSPORT_EXCEPTIONS:
        return


def _normalize_agent_final(frame: JsonObject, *, run_id: str | None, session_key: str) -> ActionResultEnvelope:
    payload = _json_object_or_empty(frame.get("payload"))
    if not frame.get("ok"):
        return _build_gateway_result(
            ok=False,
            summary=str(payload.get("summary") or _format_gateway_error(frame)),
            data={},
            error_detail=_format_gateway_error(frame),
            run_id=str(payload.get("runId") or run_id or "") or None,
            session_key=session_key,
            transport="ws",
        )

    result_payload = _json_object_or_empty(payload.get("result"))
    text = _extract_payload_text(result_payload)
    normalized = _normalize_worker_text(text)
    normalized["run_id"] = payload.get("runId") or run_id
    normalized["session_key"] = session_key
    normalized["transport"] = "ws"
    return normalized


def _extract_payload_text(result_payload: dict) -> str:
    payloads = result_payload.get("payloads") or []
    texts = [
        payload.get("text", "").strip()
        for payload in payloads
        if isinstance(payload, dict) and payload.get("text")
    ]
    return "\n\n".join(texts).strip()


def _extract_responses_api_text(response_body: dict) -> str:
    output = response_body.get("output") or []
    texts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                texts.append(str(part["text"]).strip())
    return "\n\n".join(texts).strip()


def _normalize_worker_text(text: str) -> ActionResultEnvelope:
    parsed = _extract_json_object(text)
    if isinstance(parsed, dict):
        data = parsed.get("data")
        return _build_gateway_result(
            ok=bool(parsed.get("ok", True)),
            summary=str(parsed.get("summary") or text or "OpenClaw 完成任务"),
            data=dict(data) if isinstance(data, dict) else {},
            error_detail=parsed.get("error"),
            run_id=None,
            session_key=None,
            transport="openclaw",
            raw_text=text,
        )
    return _build_gateway_result(
        ok=True,
        summary=text or "OpenClaw 完成任务",
        data={},
        error_detail=None,
        run_id=None,
        session_key=None,
        transport="openclaw",
        raw_text=text,
    )


def _extract_json_object(text: str) -> JsonObject | None:
    candidate = text.strip()
    if not candidate:
        return None
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.replace("json\n", "", 1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_gateway_result(
    *,
    ok: bool,
    summary: str,
    data: JsonObject,
    error_detail: JsonValue,
    run_id: str | None,
    session_key: str | None,
    transport: str,
    raw_text: str | None = None,
) -> ActionResultEnvelope:
    result: ActionResultEnvelope = {
        "ok": ok,
        "summary": summary,
        "data": data,
        "error": _coerce_json_value(error_detail),
        "run_id": run_id,
        "session_key": session_key,
        "transport": transport,
    }
    if raw_text is not None:
        result["raw_text"] = raw_text
    return result


def _format_gateway_error(frame: dict) -> str:
    error_info = frame.get("error") or {}
    message = error_info.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return "OpenClaw Gateway 请求失败"


def _import_websockets() -> _WebsocketsModule:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError(
            "缺少 websockets 依赖，无法使用 OpenClaw WS。可安装依赖或启用 HTTP fallback。"
        ) from exc
    return cast(_WebsocketsModule, websockets)


def _load_or_create_device_identity(path_str: str) -> dict[str, str]:
    identity_path = Path(path_str)
    if identity_path.exists():
        try:
            raw = json.loads(identity_path.read_text(encoding="utf-8"))
            public_key_pem = str(raw["publicKeyPem"])
            private_key_pem = str(raw["privateKeyPem"])
            device_id = _fingerprint_public_key(public_key_pem)
            return {
                "device_id": device_id,
                "public_key_pem": public_key_pem,
                "private_key_pem": private_key_pem,
            }
        except (json.JSONDecodeError, KeyError, OSError, TypeError):
            pass

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise RuntimeError(
            "缺少 cryptography 依赖，无法完成 OpenClaw device auth。"
        ) from exc
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    device_id = _fingerprint_public_key(public_key_pem)
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(json.dumps({
        "version": 1,
        "deviceId": device_id,
        "publicKeyPem": public_key_pem,
        "privateKeyPem": private_key_pem,
        "createdAtMs": int(datetime.now(timezone.utc).timestamp() * 1000),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(identity_path, 0o600)
    return {
        "device_id": device_id,
        "public_key_pem": public_key_pem,
        "private_key_pem": private_key_pem,
    }


def _fingerprint_public_key(public_key_pem: str) -> str:
    raw = _public_key_raw_from_pem(public_key_pem)
    return hashlib.sha256(raw).hexdigest()


def _public_key_raw_base64url_from_pem(public_key_pem: str) -> str:
    return _base64url_encode(_public_key_raw_from_pem(public_key_pem))


def _public_key_raw_from_pem(public_key_pem: str) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise RuntimeError(
            "缺少 cryptography 依赖，无法完成 OpenClaw device auth。"
        ) from exc
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    spki = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if spki.startswith(ED25519_SPKI_PREFIX) and len(spki) == len(ED25519_SPKI_PREFIX) + 32:
        return spki[len(ED25519_SPKI_PREFIX):]
    return spki


def _sign_device_payload(private_key_pem: str, payload: str) -> str:
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise RuntimeError(
            "缺少 cryptography 依赖，无法完成 OpenClaw device auth。"
        ) from exc
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )
    return _base64url_encode(private_key.sign(payload.encode("utf-8")))


def _build_device_auth_payload(
    *,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at_ms: int,
    token: str,
    nonce: str,
    platform: str,
    device_family: str = "",
) -> str:
    return "|".join([
        "v3",
        device_id,
        client_id,
        client_mode,
        role,
        ",".join(scopes),
        str(signed_at_ms),
        token,
        nonce,
        platform or "",
        device_family or "",
    ])


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _result_status(result: "ActionResultEnvelope") -> str:
    return "ok" if bool(result.get("ok", True)) else "failed"


def _json_object_or_empty(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _coerce_json_value(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _coerce_json_value(item) for key, item in value.items()}
    return str(value)
