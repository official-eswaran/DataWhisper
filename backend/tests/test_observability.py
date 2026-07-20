def test_liveness(client):
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


def test_readiness_reports_components(client):
    resp = client.get("/health/ready")
    # DB is up in tests → ready 200. Ollama is not running → reported degraded.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["components"]["database"] == "ok"
    assert body["components"]["ollama"] in ("ok", "degraded")


def test_readiness_fails_when_db_down(client, monkeypatch):
    monkeypatch.setattr("app.core.health.check_database", lambda: False)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"


def test_metrics_endpoint(client):
    # Generate at least one request so a counter exists.
    client.get("/health/live")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text
