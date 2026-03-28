"""Shared runtime helpers for config loading and Redis connections."""

import os
from pathlib import Path

import redis as redis_lib
import yaml


def load_yaml_config(path: str, *, required: bool = False) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    with open(config_path, encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def connect_redis_from_env():
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
