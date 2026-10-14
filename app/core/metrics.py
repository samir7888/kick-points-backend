"""Application metrics shared by API and worker processes."""

from __future__ import annotations

import os
import time
from typing import Any

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

KICK_API_CALLS = Counter(
    "kick_api_calls_total",
    "Kick API calls by operation and result",
    ("operation", "result"),
)
KICK_API_LATENCY = Histogram(
    "kick_api_call_duration_seconds",
    "Kick API call latency by operation",
    ("operation",),
)
VOTE_OUTCOMES = Counter(
    "vote_outcomes_total",
    "Votes finalized by outcome",
    ("status",),
)
QUEUE_DEPTH = Gauge(
    "arq_queue_depth",
    "Number of jobs waiting in the arq queue",
    multiprocess_mode="livemostrecent",
)
LISTENER_COUNT = Gauge(
    "kick_listeners_active",
    "Number of active channel listeners",
    multiprocess_mode="livemostrecent",
)
CIRCUIT_BREAKER_OPEN = Gauge(
    "kick_circuit_breaker_open",
    "Whether the Kick circuit breaker is open",
    multiprocess_mode="livemostrecent",
)


def render_metrics() -> bytes:
    """Render process-local or shared multiprocess metrics."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return generate_latest(registry)


def mark_metrics_process_dead() -> None:
    """Remove live gauge samples after graceful process shutdown."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(os.getpid())


async def track_kick_call(operation: str, awaitable: Any) -> Any:
    """Observe one Kick API call without changing its return or error behavior."""
    started = time.perf_counter()
    result_label = "success"
    try:
        result = await awaitable
        status_code = getattr(result, "status_code", None)
        if status_code is not None:
            result_label = str(status_code)
        elif isinstance(result, bool) and not result:
            result_label = "failure"
        return result
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        result_label = (
            str(status_code)
            if isinstance(status_code, int) and 100 <= status_code <= 599
            else "error"
        )
        raise
    finally:
        KICK_API_CALLS.labels(operation, result_label).inc()
        KICK_API_LATENCY.labels(operation).observe(time.perf_counter() - started)
