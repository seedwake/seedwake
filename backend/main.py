"""FastAPI backend for history, admin actions, and SSE."""

import logging

from dotenv import load_dotenv
from fastapi import FastAPI

from backend.routes.conversation import router as conversation_router
from backend.routes.query import router as query_router
from backend.routes.stream import router as stream_router
from core.logging import setup_logging
from core.runtime import connect_redis_from_env, load_yaml_config
from core.types import HealthResponse

logger = logging.getLogger(__name__)


def create_app(config: dict | None = None, redis_client=None) -> FastAPI:
    load_dotenv()
    app = FastAPI(title="Seedwake Backend")
    app.state.config = config or load_yaml_config("config.yml")
    setup_logging(app.state.config, component="backend")
    app.state.redis = redis_client if redis_client is not None else connect_redis_from_env()

    app.include_router(conversation_router)
    app.include_router(query_router)
    app.include_router(stream_router)

    @app.get("/health")
    def health() -> HealthResponse:
        return {
            "ok": True,
            "redis": app.state.redis is not None,
            "admins": len(app.state.config.get("admins", [])),
        }

    logger.info("backend application initialized")
    return app
