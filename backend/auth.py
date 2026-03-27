"""Backend authentication helpers."""

from fastapi import Header, HTTPException, Query


def resolve_admin_from_header(config: dict, authorization: str | None = Header(default=None)) -> str:
    token = _extract_bearer_token(authorization)
    return _resolve_admin(config, token)


def resolve_admin_from_query(config: dict, token: str | None = Query(default=None)) -> str:
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    return _resolve_admin(config, token)


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
