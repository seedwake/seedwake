"""Conversation history and action confirmation routes."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from backend.auth import resolve_admin_from_header
from core.action import push_action_control
from core.stimulus import load_conversation_history

router = APIRouter(prefix="/api")


class ActionConfirmBody(BaseModel):
    action_id: str
    approved: bool = True
    note: str = ""


def _resolve_admin_header(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    return resolve_admin_from_header(request.app.state.config, authorization)


def _require_redis(request: Request):
    redis_client = request.app.state.redis
    if redis_client is None:
        raise HTTPException(status_code=503, detail="redis unavailable")
    return redis_client


@router.get("/conversation")
def get_conversation_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    admin_username: str = Depends(_resolve_admin_header),
) -> dict[str, object]:
    redis_client = _require_redis(request)
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
    admin_username: str = Depends(_resolve_admin_header),
) -> dict[str, object]:
    redis_client = _require_redis(request)
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
