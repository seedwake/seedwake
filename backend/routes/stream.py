"""SSE stream route."""

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from backend.auth import resolve_admin_from_query
from core.memory.short_term import REDIS_CHANNEL as THOUGHT_CHANNEL
from core.types import EventEnvelope, EventPayload

router = APIRouter(prefix="/api")
EVENT_CHANNEL = "seedwake:events"


def _resolve_admin_query(
    request: Request,
    token: str | None = Query(default=None),
) -> str:
    return resolve_admin_from_query(request.app.state.config, token)


@router.get("/stream")
def stream_events(
    request: Request,
    admin_username: str = Depends(_resolve_admin_query),
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
            yield _format_sse("status", {"message": "stream_connected", "username": admin_username})
            while True:
                message = pubsub.get_message(timeout=15.0)
                if message is None:
                    yield ": keepalive\n\n"
                    continue
                channel = _decode_channel(message.get("channel"))
                data = _decode_data(message.get("data"))
                if channel == THOUGHT_CHANNEL:
                    yield _raw_sse("thought", data)
                    continue
                if channel == EVENT_CHANNEL:
                    envelope = _parse_event_envelope(data)
                    yield _format_sse(envelope["type"], envelope["payload"])
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


def _raw_sse(event_name: str, raw_json: str) -> str:
    return f"event: {event_name}\ndata: {raw_json}\n\n"


def _format_sse(event_name: str, payload: EventPayload) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
