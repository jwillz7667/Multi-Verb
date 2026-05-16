"""Structured logging via structlog.

Every tick-boundary log line is JSON in non-development environments
(brief §13 — observability). In development we render a human-readable
console format with timestamps for fast iteration.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from verbio_engine.config import LogLevel


def configure_logging(*, level: LogLevel, json_format: bool) -> None:
    """Initialise both stdlib logging and structlog.

    Idempotent — safe to call from tests and from FastAPI startup.
    """
    log_level = logging.getLevelNamesMapping()[level.upper()]
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_format
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Bound logger for module-level use. `name` typically `__name__`."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
