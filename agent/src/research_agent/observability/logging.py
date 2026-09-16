"""structlog setup: JSON to stdout, correlated, redacted.

The Go dispatcher logs the same field names through `log/slog`, so a run reads as one timeline
across both processes (architecture v0.6 §14): `service`, `run_id`, `span_id`, `node`, `event`,
`error_code`.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
from structlog.typing import EventDict, WrappedLogger

from research_agent.config.schema import LoggingSettings
from research_agent.observability.redaction import redact

# structlog filters on the stdlib numeric levels; spelled out so we do not reach into a private
# lookup table that could be renamed.
_LEVELS: Final[dict[str, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def redaction_processor(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Last processor before rendering: no secret or sensitive identifier reaches stdout.

    The whole event dict goes through `redact`, so the key-name rule applies to top-level fields
    (`api_key=...`) and not only to values nested inside them.
    """
    redacted: EventDict = redact(dict(event_dict))
    return redacted


def configure_logging(settings: LoggingSettings | None = None, *, service: str = "agent") -> None:
    """Install the processor chain. Idempotent, so entrypoints and tests can both call it."""
    settings = settings or LoggingSettings()
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redaction_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVELS[settings.level]),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    clear_contextvars()
    bind_contextvars(service=service)


def bind_context(**fields: Any) -> None:
    """Attach correlation fields (`run_id`, `span_id`, `node`, `iteration`) to this task."""
    bind_contextvars(**fields)


def clear_context() -> None:
    clear_contextvars()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


class best_effort:
    """Run a side job whose failure must not affect the caller - but say so in the log.

    For telemetry and caching: a Langfuse outage or a cache write error is worth a warning,
    never a failed run. A class rather than `@contextmanager` so type checkers know that
    exceptions are swallowed (`__exit__` returns `bool`).
    """

    def __init__(self, what: str, **fields: Any) -> None:
        self._what = what
        self._fields = fields

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        get_logger("best_effort").warning(
            f"{self._what} failed",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            **self._fields,
        )
        return True
