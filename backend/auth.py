"""Backend authentication helpers."""

from typing import Annotated

from fastapi import Header, HTTPException, Query

AuthorizationHeader = Annotated[str | None, Header()]
QueryToken = Annotated[str | None, Query()]
ApiTokenHeader = Annotated[str | None, Header(alias="X-API-Token")]


def resolve_admin_from_header(config: dict, authorization: AuthorizationHeader = None) -> str:
    token = _extract_bearer_token(authorization)
    return _resolve_admin(config, token)


def resolve_admin_from_query(config: dict, token: QueryToken = None) -> str:
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    return _resolve_admin(config, token)


def resolve_api_client_from_header(api_token: str, header_token: ApiTokenHeader = None) -> str:
    if not api_token:
        raise HTTPException(status_code=503, detail="backend api token not configured")
    if not header_token:
        raise HTTPException(status_code=401, detail="missing api token")
    if header_token.strip() != api_token:
        raise HTTPException(status_code=403, detail="invalid api token")
    return "backend_api"


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="invalid authorization")
    return token.strip()


def _resolve_admin(config: dict, token: str) -> str:
    for admin in config.get("admins", []):
        if str(admin.get("token", "")).strip() == token:
            return str(admin.get("username", "")).strip()
    raise HTTPException(status_code=403, detail="invalid token")
