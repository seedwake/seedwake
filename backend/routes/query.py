"""Read/query routes for recent thoughts and action state."""

import json

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.deps import require_redis, resolve_admin
from core.action import ACTION_REDIS_KEY
from core.memory.short_term import REDIS_KEY as THOUGHT_REDIS_KEY
from core.types import ActionsResponse, JsonObject, ThoughtsResponse

router = APIRouter(prefix="/api")
REDIS_ROUTE_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)


@router.get("/thoughts")
def list_recent_thoughts(
    request: Request,
    limit: int = Query(default=60, ge=1, le=300),
    admin_username: str = Depends(resolve_admin),
) -> ThoughtsResponse:
    redis_client = require_redis(request)
    try:
        raw_items = redis_client.zrange(THOUGHT_REDIS_KEY, -limit, -1)
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    items = [json.loads(item) for item in raw_items]
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": admin_username,
    }


@router.get("/actions")
def list_actions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    status: str | None = Query(default=None),
    admin_username: str = Depends(resolve_admin),
) -> ActionsResponse:
    redis_client = require_redis(request)
    items = _load_action_items(redis_client)
    if status:
        allowed = {part.strip() for part in status.split(",") if part.strip()}
        items = [item for item in items if str(item.get("status") or "") in allowed]
    items.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
    items = items[:limit]
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": admin_username,
    }


def _load_action_items(redis_client) -> list[JsonObject]:
    try:
        raw_items = redis_client.hvals(ACTION_REDIS_KEY)
    except AttributeError:
        raw_items = list(redis_client.hgetall(ACTION_REDIS_KEY).values())
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    return [json.loads(item) for item in raw_items]
