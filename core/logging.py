"""Application logging with timed rotation."""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_HANDLERS: list[logging.Handler] = []
_SIGNATURE: tuple | None = None


def setup_logging(config: dict | None, *, component: str) -> logging.Logger:
    runtime = dict((config or {}).get("runtime") or {})
    logging_config = dict(runtime.get("logging") or {})
    log_path = resolve_log_path(config, component=component)
    level_name = str(logging_config.get("level") or "INFO").upper()
    rotate_when = str(logging_config.get("rotate_when") or "midnight").strip() or "midnight"
    backup_count = max(1, int(logging_config.get("backup_count", 30)))
    console = bool(logging_config.get("console", False))
    signature = (component, str(log_path), level_name, rotate_when, backup_count, console)

    global _SIGNATURE
    if _SIGNATURE == signature and _HANDLERS:
        return logging.getLogger(__name__)

    root = logging.getLogger()
    for handler in _HANDLERS:
        root.removeHandler(handler)
        handler.close()
    _HANDLERS.clear()

    level = _resolve_level(level_name)
    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        log_path,
        when=rotate_when,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    _HANDLERS.append(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        _HANDLERS.append(console_handler)

    for handler in _HANDLERS:
        root.addHandler(handler)

    # Suppress noisy third-party loggers so the cycle log stays readable.
    for noisy_logger in ("httpx", "httpcore", "ollama"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    _SIGNATURE = signature
    logger = logging.getLogger(__name__)
    logger.info("logging configured for %s at %s", component, log_path)
    return logger


def resolve_log_path(config: dict | None, *, component: str) -> Path:
    runtime = dict((config or {}).get("runtime") or {})
    logging_config = dict(runtime.get("logging") or {})
    directory = str(logging_config.get("directory") or "data/logs").strip() or "data/logs"
    return (Path(directory) / f"{component}.log").expanduser().resolve()


def _resolve_level(level_name: str) -> int:
    level = getattr(logging, level_name, None)
    if isinstance(level, int):
        return level
    return logging.INFO
