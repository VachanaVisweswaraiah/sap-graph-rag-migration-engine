"""Structured JSON logging that doubles as the GxP audit trail, plus metrics-as-log-events.

Every emitted line carries a fixed envelope (event, component, status, run_id, duration_ms,
count, error_type/error_detail, and — when the event concerns specific graph content —
node_id/edge_id/confidence/source_doc/source_ref) so the log is greppable and a future metrics
backend can tail lines where event=='metric' without any change to call sites.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import structlog

_configured = False


def configure_logging(*, json: bool = True) -> None:
    """Configure structlog once. Safe to call repeatedly (no-op after the first call)."""
    global _configured
    if _configured:
        return
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ]
    )
    _configured = True


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a JSON-rendering structlog logger bound to `component` (e.g. "db.loader")."""
    configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger().bind(component=component)
    return logger


def new_run_id() -> str:
    """A uuid4 hex string correlating all events from one CLI invocation."""
    return uuid.uuid4().hex


@contextmanager
def timed_event(
    logger: structlog.stdlib.BoundLogger, event: str, **extra: Any
) -> Iterator[dict[str, Any]]:
    """Yield a mutable dict the caller can add fields to (e.g. count=); on exit, log
    `event` with status=ok/error and duration_ms. Never swallows the exception."""
    ctx: dict[str, Any] = {}
    start = time.monotonic()
    try:
        yield ctx
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        logger.error(
            event,
            status="error",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error_detail=str(exc),
            **extra,
            **ctx,
        )
        raise
    else:
        duration_ms = (time.monotonic() - start) * 1000
        logger.info(event, status="ok", duration_ms=duration_ms, **extra, **ctx)


def metric(
    logger: structlog.stdlib.BoundLogger,
    name: str,
    value: float,
    *,
    unit: str = "count",
    **tags: str,
) -> None:
    """Emit a structured metric event. This IS the metrics system for now — a future
    exporter can tail lines where event=='metric' without touching any call site."""
    logger.info("metric", metric_name=name, value=value, unit=unit, **tags)


def bind_run_id(
    logger: structlog.stdlib.BoundLogger, run_id: str | None = None
) -> tuple[structlog.stdlib.BoundLogger, str]:
    """Bind a run_id (generating one if not given) and return (bound_logger, run_id)."""
    run_id = run_id or new_run_id()
    return logger.bind(run_id=run_id), run_id


def log_fact(
    logger: structlog.stdlib.BoundLogger,
    event: str,
    *,
    node_id: str | None = None,
    edge_id: str | None = None,
    confidence: str | None = None,
    source_doc: str | None = None,
    source_ref: str | None = None,
    **extra: Any,
) -> None:
    """Log an event about a specific graph element — this is what makes the log
    double as the GxP audit trail (traceable node/edge, its confidence, its source)."""
    fields: Mapping[str, Any] = {
        k: v
        for k, v in {
            "node_id": node_id,
            "edge_id": edge_id,
            "confidence": confidence,
            "source_doc": source_doc,
            "source_ref": source_ref,
        }.items()
        if v is not None
    }
    logger.info(event, **fields, **extra)
