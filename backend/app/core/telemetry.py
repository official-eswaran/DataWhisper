"""Error tracking (Sentry) and distributed tracing (OpenTelemetry).

Both are strictly opt-in. With no env configured they are complete no-ops, so
local/dev and test runs are unaffected. In production set ``SENTRY_DSN`` and/or
``OTEL_EXPORTER_OTLP_ENDPOINT`` to turn them on. Trace/request-id correlation in
logs is handled by ``app.core.observability.CorrelationFilter``.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("datawhisper")

# Set once tracing is wired so the log correlation filter knows to enrich records
# with trace/span ids without probing OTel on every log line.
_tracing_enabled = False


def tracing_enabled() -> bool:
    return _tracing_enabled


def init_sentry() -> bool:
    """Initialise Sentry if ``SENTRY_DSN`` is set. Returns whether it was enabled."""
    if not settings.SENTRY_DSN:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or None,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        send_default_pii=False,  # never ship request bodies / PII to Sentry
    )
    logger.info("Sentry error tracking enabled")
    return True


def init_tracing(app) -> bool:
    """Instrument FastAPI + outbound requests with OpenTelemetry if an OTLP
    endpoint is set. Returns whether tracing was enabled."""
    global _tracing_enabled

    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    RequestsInstrumentor().instrument()

    _tracing_enabled = True
    logger.info("OpenTelemetry tracing enabled → %s", endpoint)
    return True
