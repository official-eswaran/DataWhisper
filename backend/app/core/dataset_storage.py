"""Per-session dataset storage.

Uploaded datasets are DuckDB files, one per session. DuckDB needs a *local*
file, so both backends expose the same shape:

- ``path_for_write`` — a local path to create/write the ``.duckdb`` into.
- ``commit`` — persist that local file to the durable backing store.
- ``ensure_local`` — make the file available locally (downloading if needed)
  and return its path, or raise ``FileNotFoundError``.
- ``exists`` / ``delete``.

``local`` keeps files directly on ``DATABASE_DIR`` (a ReadWriteMany volume when
horizontally scaled). ``s3`` keeps them in object storage and materialises them
to a local cache on demand, removing the shared-filesystem requirement — the
last thing blocking clean multi-replica scaling (issue #3).
"""
from __future__ import annotations

import threading
from pathlib import Path

from app.core.config import settings


def _not_found(session_id: str) -> FileNotFoundError:
    return FileNotFoundError(f"Session '{session_id}' not found. Please upload data first.")


class LocalDatasetStorage:
    """Datasets live directly on ``DATABASE_DIR``."""

    def __init__(self, root: Path):
        self._root = root

    def _path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.duckdb"

    def path_for_write(self, session_id: str) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        return self._path(session_id)

    def commit(self, session_id: str) -> None:
        # Already on the durable volume — nothing to upload.
        pass

    def ensure_local(self, session_id: str) -> Path:
        path = self._path(session_id)
        if not path.exists():
            raise _not_found(session_id)
        return path

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).exists()

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)


class S3DatasetStorage:
    """Datasets live in S3/GCS, materialised to a local cache dir on demand."""

    def __init__(
        self,
        bucket: str,
        prefix: str,
        cache_dir: Path,
        endpoint_url: str = "",
        region: str = "",
    ):
        import boto3

        self._bucket = bucket
        self._prefix = prefix
        self._cache = cache_dir
        self._lock = threading.Lock()
        client_kwargs: dict = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if region:
            client_kwargs["region_name"] = region
        self._s3 = boto3.client("s3", **client_kwargs)

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}.duckdb"

    def _local(self, session_id: str) -> Path:
        return self._cache / f"{session_id}.duckdb"

    def path_for_write(self, session_id: str) -> Path:
        self._cache.mkdir(parents=True, exist_ok=True)
        return self._local(session_id)

    def commit(self, session_id: str) -> None:
        self._s3.upload_file(str(self._local(session_id)), self._bucket, self._key(session_id))

    def ensure_local(self, session_id: str) -> Path:
        from botocore.exceptions import ClientError

        path = self._local(session_id)
        if path.exists():
            return path
        self._cache.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock:
                if not path.exists():  # re-check under lock
                    self._s3.download_file(self._bucket, self._key(session_id), str(path))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NoSuchBucket"):
                raise _not_found(session_id) from exc
            raise
        return path

    def exists(self, session_id: str) -> bool:
        from botocore.exceptions import ClientError

        if self._local(session_id).exists():
            return True
        try:
            self._s3.head_object(Bucket=self._bucket, Key=self._key(session_id))
            return True
        except ClientError:
            return False

    def delete(self, session_id: str) -> None:
        from botocore.exceptions import ClientError

        self._local(session_id).unlink(missing_ok=True)
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=self._key(session_id))
        except ClientError:
            pass  # best-effort; a missing object is already the desired state


def build_dataset_storage():
    if settings.DATASET_STORAGE_BACKEND == "s3":
        if not settings.DATASET_S3_BUCKET:
            raise RuntimeError("DATASET_S3_BUCKET must be set when DATASET_STORAGE_BACKEND=s3")
        return S3DatasetStorage(
            bucket=settings.DATASET_S3_BUCKET,
            prefix=settings.DATASET_S3_PREFIX,
            cache_dir=settings.DATABASE_DIR / "dataset-cache",
            endpoint_url=settings.DATASET_S3_ENDPOINT_URL,
            region=settings.DATASET_S3_REGION,
        )
    return LocalDatasetStorage(settings.DATABASE_DIR)


dataset_storage = build_dataset_storage()
