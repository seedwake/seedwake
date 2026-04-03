"""Conversation history and action confirmation routes."""

import json
from typing import Annotated

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.deps import require_redis, resolve_admin, resolve_api_client
from core.action import push_action_control
from core.stimulus import load_conversation_history
from core.common_types import ActionConfirmResponse, ConversationHistoryResponse

router = APIRouter(prefix="/api")
REDIS_ROUTE_EXCEPTIONS = (
    redis_lib.RedisError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
)
AdminUsername = Annotated[str, Depends(resolve_admin)]
ApiClient = Annotated[str, Depends(resolve_api_client)]
ConversationLimit = Annotated[int, Query(ge=1, le=300)]
REDIS_UNAVAILABLE_RESPONSE = {
    503: {"description": "Redis unavailable or action control unavailable"},
}


class ActionConfirmBody(BaseModel):
    action_id: str
    approved: bool = True
    note: str = ""


@router.get("/conversation", responses=REDIS_UNAVAILABLE_RESPONSE)
def get_conversation_history(
    request: Request,
    api_client: ApiClient,
    limit: ConversationLimit = 100,
) -> ConversationHistoryResponse:
    redis_client = require_redis(request)
    try:
        items = load_conversation_history(redis_client, limit)
    except REDIS_ROUTE_EXCEPTIONS as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": api_client,
    }


@router.post("/action/confirm", responses=REDIS_UNAVAILABLE_RESPONSE)
def confirm_action(
    body: ActionConfirmBody,
    request: Request,
    api_client: ApiClient,
    admin_username: AdminUsername,
) -> ActionConfirmResponse:
    _ = api_client
    redis_client = require_redis(request)
    pushed = push_action_control(
        redis_client,
        body.action_id,
        approved=body.approved,
        actor=admin_username,
        note=body.note,
    )
    if not pushed:
        raise HTTPException(status_code=503, detail="action control unavailable")
    return {"ok": True, "action_id": body.action_id, "approved": body.approved}
