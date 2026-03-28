"""Shared FastAPI dependencies for backend routes."""

from typing import Annotated

from fastapi import Header, HTTPException, Request

from backend.auth import resolve_admin_from_header

AuthorizationHeader = Annotated[str | None, Header()]


def resolve_admin(
    request: Request,
    authorization: AuthorizationHeader = None,
) -> str:
    return resolve_admin_from_header(request.app.state.config, authorization)


def require_redis(request: Request):
    redis_client = request.app.state.redis
    if redis_client is None:
        raise HTTPException(status_code=503, detail="redis unavailable")
    return redis_client
