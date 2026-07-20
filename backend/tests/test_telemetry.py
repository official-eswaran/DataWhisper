"""Tests for Sentry + OpenTelemetry wiring and log correlation (issue #4).

The key guarantee: both integrations are complete no-ops unless their env vars
are set, so dev/test are unaffected. We also verify they *do* initialise when
configured, and that the log correlation filter stamps the request id.
"""
import logging

from app.core import telemetry
from app.core.config import settings
from app.core.observability import CorrelationFilter, request_id_var

# ── No-op unless configured ────────────────────────────────────────────────

def test_init_sentry_noop_without_dsn(monkeypatch):
    monkeypatch.setattr(settings, "SENTRY_DSN", "")
    assert telemetry.init_sentry() is False


def test_init_tracing_noop_without_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
    assert telemetry.init_tracing(object()) is False


# ── Enabled when configured ────────────────────────────────────────────────

def test_init_sentry_initialises_when_dsn_set(monkeypatch):
    captured = {}

    import sentry_sdk

    def fake_init(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://pub@example.ingest.sentry.io/1")
    monkeypatch.setattr(settings, "SENTRY_ENVIRONMENT", "prod")

    assert telemetry.init_sentry() is True
    assert captured["dsn"].endswith("/1")
    assert captured["environment"] == "prod"
    assert captured["send_default_pii"] is False   # never ship PII


def test_init_tracing_instruments_app_when_endpoint_set(monkeypatch):
    from fastapi import FastAPI

    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    app = FastAPI()
    try:
        assert telemetry.init_tracing(app) is True
        assert telemetry.tracing_enabled() is True
    finally:
        # Reset global + uninstrument so other tests aren't affected.
        telemetry._tracing_enabled = False
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        FastAPIInstrumentor.uninstrument_app(app)
        RequestsInstrumentor().uninstrument()


# ── Log correlation ────────────────────────────────────────────────────────

def _record() -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)


def test_correlation_filter_stamps_request_id():
    token = request_id_var.set("abc123")
    try:
        rec = _record()
        assert CorrelationFilter().filter(rec) is True
        assert rec.request_id == "abc123"
    finally:
        request_id_var.reset(token)


def test_correlation_filter_no_request_id_outside_request():
    rec = _record()
    CorrelationFilter().filter(rec)
    assert not hasattr(rec, "request_id")


def test_request_id_header_present_on_response(client):
    resp = client.get("/health/live")
    assert resp.headers.get("X-Request-ID")
