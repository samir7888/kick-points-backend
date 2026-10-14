"""Structured logging configuration."""

import logging
import re
import sys
from typing import Any

import structlog
from structlog.contextvars import clear_contextvars
from structlog.stdlib import BoundLogger

from .config import get_settings

_SENSITIVE_KEYS = (
    "token",
    "secret",
    "authorization",
    "password",
    "cookie",
    "api_key",
    "code_verifier",
    "oauth_code",
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")


def configure_logging() -> None:
    """Configure structured logging with structlog."""
    settings = get_settings()

    # Clear any existing context
    clear_contextvars()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_sensitive_data,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info
            if not settings.is_production
            else structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
            if settings.is_production
            else structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.INFO if settings.is_production else logging.DEBUG
        ),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if settings.is_production else logging.DEBUG,
    )

    # Filter out noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


def redact_sensitive_data(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Redact sensitive fields recursively before records reach any renderer."""

    def redact(value: Any, key: str = "") -> Any:
        normalized_key = key.lower()
        if any(part in normalized_key for part in _SENSITIVE_KEYS):
            if normalized_key not in {"token_health", "token_status"}:
                return "[REDACTED]"
        if isinstance(value, dict):
            return {name: redact(item, str(name)) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return _BEARER_RE.sub(r"\1[REDACTED]", value)
        return value

    return redact(event_dict)
