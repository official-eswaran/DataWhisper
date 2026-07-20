"""Tests for the per-session dataset storage abstraction (issue #3).

Exercises both backends: the local filesystem backend and the S3 backend
(via moto), including the download-on-demand path that lets a second replica
serve a session it never wrote locally.
"""
import pathlib
import tempfile

import pytest

from app.core.dataset_storage import (
    LocalDatasetStorage,
    S3DatasetStorage,
    build_dataset_storage,
)


def _write_duckdb(path: pathlib.Path, table: str = "t") -> None:
    import duckdb

    conn = duckdb.connect(str(path))
    conn.execute(f"CREATE TABLE {table} AS SELECT 1 AS a, 2 AS b")
    conn.close()


def _read_first_row(path: pathlib.Path, table: str = "t"):
    import duckdb

    conn = duckdb.connect(str(path))
    try:
        return conn.execute(f"SELECT a, b FROM {table}").fetchone()
    finally:
        conn.close()


# ── Local backend ──────────────────────────────────────────────────────────

def test_local_write_commit_read_delete():
    with tempfile.TemporaryDirectory() as d:
        store = LocalDatasetStorage(pathlib.Path(d))
        sid = "sess-local"

        assert not store.exists(sid)
        with pytest.raises(FileNotFoundError):
            store.ensure_local(sid)

        path = store.path_for_write(sid)
        _write_duckdb(path)
        store.commit(sid)  # no-op for local

        assert store.exists(sid)
        assert store.ensure_local(sid) == path
        assert _read_first_row(store.ensure_local(sid)) == (1, 2)

        store.delete(sid)
        assert not store.exists(sid)
        with pytest.raises(FileNotFoundError):
            store.ensure_local(sid)


# ── S3 backend (moto) ──────────────────────────────────────────────────────

@pytest.fixture
def s3_bucket(monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="dw-test")
        yield "dw-test"


def test_s3_write_commit_read_delete(s3_bucket):
    with tempfile.TemporaryDirectory() as d:
        store = S3DatasetStorage(s3_bucket, "datasets/", pathlib.Path(d) / "cache")
        sid = "sess-s3"

        assert not store.exists(sid)
        with pytest.raises(FileNotFoundError):
            store.ensure_local(sid)

        _write_duckdb(store.path_for_write(sid))
        store.commit(sid)  # uploads to S3

        assert store.exists(sid)
        store.delete(sid)
        assert not store.exists(sid)


def test_s3_downloads_on_demand_for_a_fresh_replica(s3_bucket):
    """A pod that never wrote the session downloads it from S3 (the #3 point)."""
    with tempfile.TemporaryDirectory() as d:
        writer = S3DatasetStorage(s3_bucket, "datasets/", pathlib.Path(d) / "writer")
        _write_duckdb(writer.path_for_write("shared"))
        writer.commit("shared")

        # A separate cache dir simulates a different replica with cold local disk.
        reader = S3DatasetStorage(s3_bucket, "datasets/", pathlib.Path(d) / "reader")
        assert not (pathlib.Path(d) / "reader" / "shared.duckdb").exists()

        local = reader.ensure_local("shared")   # triggers download
        assert local.exists()
        assert _read_first_row(local) == (1, 2)


def test_s3_delete_removes_object_and_local_cache(s3_bucket):
    with tempfile.TemporaryDirectory() as d:
        store = S3DatasetStorage(s3_bucket, "datasets/", pathlib.Path(d) / "cache")
        _write_duckdb(store.path_for_write("gone"))
        store.commit("gone")
        cached = store.ensure_local("gone")
        assert cached.exists()

        store.delete("gone")
        assert not cached.exists()      # local cache cleared
        assert not store.exists("gone")  # object removed


# ── Backend selection ──────────────────────────────────────────────────────

def test_build_dataset_storage_selects_backend(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATASET_STORAGE_BACKEND", "local")
    assert isinstance(build_dataset_storage(), LocalDatasetStorage)

    monkeypatch.setattr(settings, "DATASET_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "DATASET_S3_BUCKET", "")
    with pytest.raises(RuntimeError, match="DATASET_S3_BUCKET"):
        build_dataset_storage()
