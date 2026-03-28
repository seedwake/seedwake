"""Conversation history and action confirmation routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.deps import require_redis, resolve_admin
from core.action import push_action_control
from core.stimulus import load_conversation_history
from core.types import ActionConfirmResponse, ConversationHistoryResponse

router = APIRouter(prefix="/api")


class ActionConfirmBody(BaseModel):
    action_id: str
    approved: bool = True
    note: str = ""


@router.get("/conversation")
def get_conversation_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    admin_username: str = Depends(resolve_admin),
) -> ConversationHistoryResponse:
    redis_client = require_redis(request)
    try:
        items = load_conversation_history(redis_client, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"redis read failed: {exc}") from exc
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "requested_by": admin_username,
    }


@router.post("/action/confirm")
def confirm_action(
    body: ActionConfirmBody,
    request: Request,
    admin_username: str = Depends(resolve_admin),
) -> ActionConfirmResponse:
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
