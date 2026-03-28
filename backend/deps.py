"""Shared FastAPI dependencies for backend routes."""

from typing import Annotated

from fastapi import Header, HTTPException, Request

from backend.auth import resolve_admin_from_header, resolve_api_client_from_header

AuthorizationHeader = Annotated[str | None, Header()]
ApiTokenHeader = Annotated[str | None, Header(alias="X-API-Token")]


def resolve_admin(
    request: Request,
    authorization: AuthorizationHeader = None,
) -> str:
    return resolve_admin_from_header(request.app.state.config, authorization)


def resolve_api_client(
    request: Request,
    x_api_token: ApiTokenHeader = None,
) -> str:
    return resolve_api_client_from_header(request.app.state.backend_api_token, x_api_token)


def require_redis(request: Request):
    redis_client = request.app.state.redis
    if redis_client is None:
        raise HTTPException(status_code=503, detail="redis unavailable")
    return redis_client
