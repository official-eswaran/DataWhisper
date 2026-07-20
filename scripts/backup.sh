#!/usr/bin/env bash
#
# DataWhisper backup — captures the metadata database (Postgres or SQLite) and
# the per-session DuckDB datasets + uploads into a single timestamped archive,
# with an optional push to object storage.
#
# Usage:
#   ./scripts/backup.sh
#
# Configuration (environment variables):
#   DATABASE_URL            postgresql+psycopg://user:pass@host:5432/db
#                           (or empty to back up the SQLite file at DATA_DIR/app.db)
#   DATA_DIR                Directory holding *.duckdb files and uploads   [./backend/data]
#   BACKUP_ROOT             Where to write local backups                   [./backups]
#   BACKUP_S3_URI           Optional s3://bucket/prefix to upload to       [unset]
#   BACKUP_RETENTION_DAYS   Delete local backups older than N days         [14]
#
set -euo pipefail

DATA_DIR="${DATA_DIR:-./backend/data}"
BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
DATABASE_URL="${DATABASE_URL:-}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
workdir="$(mktemp -d)"
archive="${BACKUP_ROOT}/datawhisper-backup-${timestamp}.tar.gz"

log() { printf '[backup %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
cleanup() { rm -rf "${workdir}"; }
trap cleanup EXIT

mkdir -p "${BACKUP_ROOT}"

# ── 1. Metadata database ──────────────────────────────────────────────────────
if [[ -n "${DATABASE_URL}" && "${DATABASE_URL}" == postgres* ]]; then
    # pg_dump understands a URI but not SQLAlchemy's "+psycopg" driver suffix.
    pg_uri="${DATABASE_URL/+psycopg/}"
    log "Dumping Postgres database"
    pg_dump --format=custom --no-owner --file="${workdir}/metadata.pgdump" "${pg_uri}"
    db_kind="postgres"
else
    sqlite_path="${DATA_DIR}/app.db"
    if [[ -f "${sqlite_path}" ]]; then
        log "Copying SQLite database (consistent snapshot via .backup)"
        if command -v sqlite3 >/dev/null 2>&1; then
            sqlite3 "${sqlite_path}" ".backup '${workdir}/app.db'"
        else
            cp "${sqlite_path}" "${workdir}/app.db"
        fi
        db_kind="sqlite"
    else
        log "WARNING: no Postgres URL and no SQLite file found at ${sqlite_path}"
        db_kind="none"
    fi
fi

# ── 2. Dataset files (DuckDB) + uploads ───────────────────────────────────────
if [[ -d "${DATA_DIR}" ]]; then
    log "Archiving dataset files from ${DATA_DIR}"
    tar -czf "${workdir}/data.tar.gz" -C "${DATA_DIR}" . 2>/dev/null || true
fi

# ── 3. Manifest ───────────────────────────────────────────────────────────────
cat > "${workdir}/manifest.json" <<EOF
{
  "created_at": "${timestamp}",
  "database": "${db_kind}",
  "data_dir": "${DATA_DIR}",
  "tool": "datawhisper/scripts/backup.sh"
}
EOF

# ── 4. Bundle + checksum ──────────────────────────────────────────────────────
log "Creating archive ${archive}"
tar -czf "${archive}" -C "${workdir}" .
sha256sum "${archive}" > "${archive}.sha256"

# ── 5. Optional upload to object storage ──────────────────────────────────────
if [[ -n "${BACKUP_S3_URI:-}" ]]; then
    if command -v aws >/dev/null 2>&1; then
        log "Uploading to ${BACKUP_S3_URI}"
        aws s3 cp "${archive}"        "${BACKUP_S3_URI%/}/$(basename "${archive}")"
        aws s3 cp "${archive}.sha256" "${BACKUP_S3_URI%/}/$(basename "${archive}").sha256"
    else
        log "WARNING: BACKUP_S3_URI set but 'aws' CLI not found — skipping upload"
    fi
fi

# ── 6. Local retention ────────────────────────────────────────────────────────
log "Pruning local backups older than ${BACKUP_RETENTION_DAYS} days"
find "${BACKUP_ROOT}" -name 'datawhisper-backup-*.tar.gz*' -type f \
    -mtime "+${BACKUP_RETENTION_DAYS}" -delete 2>/dev/null || true

log "Backup complete: ${archive}"
echo "${archive}"
