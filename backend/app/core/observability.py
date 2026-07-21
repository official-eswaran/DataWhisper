"""Metrics and logging setup.

* Prometheus counters/histograms for HTTP traffic, exposed at ``/metrics``.
* Optional JSON log formatter so logs can be shipped to Loki/ELK/CloudWatch and
  queried by field (request id, path, status, latency).

Path labels use the matched *route template* (e.g. ``/api/export/pdf/{session_id}``)
rather than the raw URL, to keep metric cardinality bounded.
"""
from __future__ import annotations

import contextvars
import json
import logging

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Per-request id, set by the HTTP middleware and stamped onto every log record
# emitted while handling that request (see CorrelationFilter).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
LLM_FAILURES = Counter("llm_call_failures_total", "Total failed LLM calls")
LLM_CALLS = Counter("llm_calls_total", "Total LLM calls")
LLM_CACHE_HITS = Counter("llm_cache_hits_total", "LLM response cache hits")
LLM_CACHE_MISSES = Counter("llm_cache_misses_total", "LLM response cache misses")


def record_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(duration_seconds)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


class CorrelationFilter(logging.Filter):
    """Stamp each log record with the current request id and, when OpenTelemetry
    tracing is active, the active trace/span ids — so logs and traces can be
    joined in the backend (Loki ↔ Tempo/Jaeger)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            rid = request_id_var.get()
            if rid:
                record.request_id = rid

        from app.core.telemetry import tracing_enabled

        if tracing_enabled():
            try:
                from opentelemetry import trace

                ctx = trace.get_current_span().get_span_context()
                if ctx.is_valid:
                    record.trace_id = format(ctx.trace_id, "032x")
                    record.span_id = format(ctx.span_id, "016x")
            except Exception:
                pass
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("request_id", "trace_id", "span_id", "path", "method", "status"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


def setup_logging(debug: bool, json_logs: bool) -> None:
    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
        )
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
