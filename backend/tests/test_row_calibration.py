"""Row-ceiling calibration from measured uploads (issue #24).

The plan row ceilings and the ~100 B/row constant used to sanity-check them were
both picked without data. These tests cover the replacement: real uploads are
measured, the check uses the measurement once there is enough of it, and it says
which basis it used either way.
"""
import io

import pytest

from app.core import quota
from app.core.database import (
    get_upload_shape_stats,
    record_upload_shape,
    reset_upload_shape_stats,
)

CSV = b"region,revenue\nNorth,100\nSouth,250\n"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def clean_stats(client):
    """The calibration is global, so every test starts and ends from empty."""
    reset_upload_shape_stats()
    yield
    reset_upload_shape_stats()


def _sample(uploads: int, bytes_per_row: float, rows: int = 100_000) -> None:
    for _ in range(uploads):
        record_upload_shape(int(rows * bytes_per_row), rows)


# ── Recording ──────────────────────────────────────────────────────────────

def test_records_mean_and_minimum():
    record_upload_shape(400_000, 10_000)   # 40 B/row
    record_upload_shape(2_000_000, 10_000)  # 200 B/row
    stats = get_upload_shape_stats()
    assert stats["uploads"] == 2
    assert stats["mean_bytes_per_row"] == pytest.approx(120)
    assert stats["min_bytes_per_row"] == pytest.approx(40)


def test_small_uploads_are_not_sampled():
    """A 2-row CSV's bytes-per-row is mostly header — it would bias the minimum up."""
    record_upload_shape(len(CSV), 2)
    assert get_upload_shape_stats()["uploads"] == 0


def test_degenerate_inputs_ignored():
    record_upload_shape(0, 50_000)
    record_upload_shape(1_000_000, 0)
    record_upload_shape(-5, 50_000)
    assert get_upload_shape_stats()["uploads"] == 0


def test_empty_stats_report_no_measurement():
    stats = get_upload_shape_stats()
    assert stats == {"uploads": 0, "mean_bytes_per_row": None, "min_bytes_per_row": None}


# ── Estimate selection ─────────────────────────────────────────────────────

def test_falls_back_before_enough_samples():
    _sample(quota._MIN_SAMPLES - 1, 40)
    est = quota.bytes_per_row_estimate()
    assert est["source"] == "fallback"
    assert est["bytes_per_row"] == quota._FALLBACK_BYTES_PER_ROW


def test_uses_measurement_once_the_sample_is_big_enough():
    _sample(quota._MIN_SAMPLES, 40)
    est = quota.bytes_per_row_estimate()
    assert est["source"] == "measured"
    assert est["bytes_per_row"] == pytest.approx(40)


def test_estimate_uses_the_narrowest_file_not_the_mean():
    """Narrow rows are the failure case: more of them fit in one max-size upload."""
    _sample(quota._MIN_SAMPLES, 500)
    record_upload_shape(20 * 100_000, 100_000)  # one 20 B/row file
    est = quota.bytes_per_row_estimate()
    assert est["bytes_per_row"] == pytest.approx(20)
    assert est["mean_bytes_per_row"] > 20


# ── The drift check that consumes it ───────────────────────────────────────

def test_narrow_rows_surface_a_ceiling_the_guess_would_have_missed(monkeypatch):
    """The bug the old constant hid: 100 B/row said 'fine', 10 B/row does not."""
    monkeypatch.setattr(quota.settings, "MAX_UPLOAD_SIZE_MB", 500)
    monkeypatch.setitem(quota.PLAN_LIMITS["free"], quota.ROWS_PROCESSED, 10_000_000)
    assert quota.check_limits_are_reachable() == []  # 500 MB / 100 B = 5.2M rows

    _sample(quota._MIN_SAMPLES, 10)
    warnings = quota.check_limits_are_reachable()  # 500 MB / 10 B = 52M rows
    assert any("free" in w for w in warnings)


def test_warning_states_which_basis_it_used(monkeypatch):
    monkeypatch.setitem(quota.PLAN_LIMITS["free"], quota.ROWS_PROCESSED, 1_000)
    assert any("assumed" in w for w in quota.check_limits_are_reachable())

    _sample(quota._MIN_SAMPLES, 40)
    assert any("measured uploads" in w for w in quota.check_limits_are_reachable())


# ── Operator endpoint ──────────────────────────────────────────────────────

def test_limits_endpoint_reports_calibration(client, admin_token):
    r = client.get("/api/usage/limits", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bytes_per_row_source"] == "fallback"
    assert body["uploads_measured"] == 0
    assert body["samples_needed"] == quota._MIN_SAMPLES
    assert body["rows_in_one_max_upload"] > 0
    assert body["plans"]["enterprise"]["rows_limit"] is None
    assert body["plans"]["free"]["max_uploads_at_ceiling"] >= 0


def test_limits_endpoint_reflects_measurements(client, admin_token):
    _sample(quota._MIN_SAMPLES, 40)
    body = client.get("/api/usage/limits", headers=_auth(admin_token)).json()
    assert body["bytes_per_row_source"] == "measured"
    assert body["bytes_per_row"] == pytest.approx(40)
    assert body["uploads_measured"] == quota._MIN_SAMPLES


def test_limits_endpoint_requires_admin(client):
    assert client.get("/api/usage/limits").status_code in (401, 403)


# ── End to end through a real upload ───────────────────────────────────────

def test_real_upload_is_measured(client, admin_token):
    """A file big enough to sample lands in the calibration."""
    rows = "\n".join(f"North,{i}" for i in range(2_000))
    payload = f"region,revenue\n{rows}\n".encode()
    files = {"file": ("wide.csv", io.BytesIO(payload), "text/csv")}
    r = client.post("/api/upload/", headers=_auth(admin_token), files=files)
    assert r.status_code == 200, r.text

    stats = get_upload_shape_stats()
    assert stats["uploads"] == 1
    assert stats["min_bytes_per_row"] == pytest.approx(len(payload) / 2_000)
