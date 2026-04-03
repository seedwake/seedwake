"""SSE stream route."""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.deps import resolve_api_client
from core.memory.short_term import REDIS_CHANNEL as THOUGHT_CHANNEL
from core.common_types import EventEnvelope, EventPayload, StatusEventPayload

router = APIRouter(prefix="/api")
EVENT_CHANNEL = "seedwake:events"
logger = logging.getLogger(__name__)
ApiClient = Annotated[str, Depends(resolve_api_client)]


@router.get("/stream")
def stream_events(
    request: Request,
    api_client: ApiClient,
) -> StreamingResponse:
    redis_client = request.app.state.redis
    if redis_client is None:
        return StreamingResponse(
            iter(["event: status\ndata: {\"message\": \"redis unavailable\"}\n\n"]),
            media_type="text/event-stream",
        )
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(THOUGHT_CHANNEL, EVENT_CHANNEL)

    def generate():
        try:
            yield _format_sse("status", _stream_status_payload("stream_connected", api_client))
            while True:
                try:
                    yield _stream_next_chunk(pubsub)
                except (json.JSONDecodeError, TypeError, KeyError):
                    logger.exception("malformed SSE event payload")
                    continue
        # noinspection PyBroadException
        except Exception as exc:
            logger.exception("unexpected SSE stream failure: %s", exc)
            yield _format_sse("status", _stream_status_payload("stream_error"))
        finally:
            pubsub.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _decode_channel(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _decode_data(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _parse_event_envelope(raw: str) -> EventEnvelope:
    return json.loads(raw)


def _stream_next_chunk(pubsub) -> str:
    message = pubsub.get_message(timeout=15.0)
    if message is None:
        return ": keepalive\n\n"
    channel = _decode_channel(message.get("channel"))
    data = _decode_data(message.get("data"))
    if channel == THOUGHT_CHANNEL:
        return _raw_sse("thought", data)
    if channel == EVENT_CHANNEL:
        envelope = _parse_event_envelope(data)
        return _format_sse(envelope["type"], envelope["payload"])
    return ": keepalive\n\n"


def _raw_sse(event_name: str, raw_json: str) -> str:
    return f"event: {event_name}\ndata: {raw_json}\n\n"


def _format_sse(event_name: str, payload: EventPayload) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_status_payload(message: str, username: str | None = None) -> StatusEventPayload:
    payload: StatusEventPayload = {"message": message}
    if username:
        payload["username"] = username
    return payload
