"""FastAPI backend for history, admin actions, and SSE."""

import os
from pathlib import Path

import redis as redis_lib
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI

from backend.routes.conversation import router as conversation_router
from backend.routes.query import router as query_router
from backend.routes.stream import router as stream_router


def create_app(config: dict | None = None, redis_client=None) -> FastAPI:
    load_dotenv()
    app = FastAPI(title="Seedwake Backend")
    app.state.config = config or _load_config("config.yml")
    app.state.redis = redis_client if redis_client is not None else _connect_redis()

    app.include_router(conversation_router)
    app.include_router(query_router)
    app.include_router(stream_router)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "redis": app.state.redis is not None,
            "admins": len(app.state.config.get("admins", [])),
        }

    return app

def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _connect_redis():
    try:
        client = redis_lib.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
        return client
    except Exception:
        return None
