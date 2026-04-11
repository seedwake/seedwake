"""FastAPI backend for history, admin actions, and SSE."""

import logging
import os
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI

from backend.deps import resolve_api_client
from backend.routes.conversation import router as conversation_router
from backend.routes.query import router as query_router
from backend.routes.stream import router as stream_router
from core.i18n import init as init_i18n, t
from core.logging_setup import setup_logging
from core.runtime import connect_redis_from_env, load_yaml_config
from core.common_types import HealthResponse

logger = logging.getLogger(__name__)
ApiClient = Annotated[str, Depends(resolve_api_client)]


def create_app(config: dict | None = None, redis_client=None) -> FastAPI:
    load_dotenv()
    app = FastAPI(title="Seedwake Backend")
    app.state.config = config or load_yaml_config("config.yml")
    init_i18n(str(app.state.config.get("language", "zh")))
    app.state.backend_api_token = os.environ.get("BACKEND_API_TOKEN", "").strip()
    setup_logging(app.state.config, component="backend")
    app.state.redis = redis_client if redis_client is not None else connect_redis_from_env()
    if not app.state.backend_api_token:
        raise RuntimeError(t("backend.token_not_configured"))

    app.include_router(conversation_router)
    app.include_router(query_router)
    app.include_router(stream_router)

    @app.get("/health")
    def health(api_client: ApiClient) -> HealthResponse:
        _ = api_client
        return {
            "ok": True,
            "redis": app.state.redis is not None,
            "admins": len(app.state.config.get("admins", [])),
        }

    logger.info("backend application initialized")
    return app
