"""Read/query routes for recent thoughts and action state."""

import json
import logging
from typing import Annotated

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.deps import require_redis, resolve_admin, resolve_api_client
from core.action import load_action_items
from core.memory.short_term import REDIS_KEY as THOUGHT_REDIS_KEY
from core.types import ActionsResponse, JsonObject, ThoughtsResponse

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
REDIS_ROUTE_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)
AdminUsername = Annotated[str, Depends(resolve_admin)]
ApiClient = Annotated[str, Depends(resolve_api_client)]
ThoughtLimit = Annotated[int, Query(ge=1, le=300)]
ActionLimit = Annotated[int, Query(ge=1, le=300)]
ActionStatusFilter = Annotated[str | None, Query()]
REDIS_UNAVAILABLE_RESPONSE = {
    503: {"description": "Redis unavailable"},
}


@router.get("/thoughts", responses=REDIS_UNAVAILABLE_RESPONSE)
def list_recent_thoughts(
    request: Request,
    api_client: ApiClient,
    limit: ThoughtLimit = 60,
) -> ThoughtsResponse:
    redis_client = require_redis(request)
    items = _load_recent_thoughts(redis_client, limit)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": api_client,
    }


@router.get("/actions", responses=REDIS_UNAVAILABLE_RESPONSE)
def list_actions(
    request: Request,
    api_client: ApiClient,
    admin_username: AdminUsername,
    limit: ActionLimit = 100,
    status: ActionStatusFilter = None,
) -> ActionsResponse:
    _ = api_client
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
        return load_action_items(redis_client)
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc


def _load_recent_thoughts(redis_client, limit: int) -> list[JsonObject]:
    try:
        raw_items = redis_client.zrange(THOUGHT_REDIS_KEY, -limit, -1)
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    items = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError) as exc:
            logger.warning("skipping malformed thought record: %s", exc)
            continue
        if not isinstance(item, dict):
            logger.warning("skipping non-object thought record")
            continue
        items.append(item)
    return items
