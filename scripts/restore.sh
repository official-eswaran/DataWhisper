#!/usr/bin/env bash
#
# DataWhisper restore — restores a backup produced by backup.sh.
#
# THIS IS DESTRUCTIVE: it overwrites the current database and dataset files.
# You must pass --yes (or set CONFIRM=yes) to proceed.
#
# Usage:
#   ./scripts/restore.sh /path/to/datawhisper-backup-YYYYMMDDTHHMMSSZ.tar.gz --yes
#
# Configuration (environment variables):
#   DATABASE_URL   Target Postgres URL (or empty to restore the SQLite file)
#   DATA_DIR       Directory to restore *.duckdb files + uploads into  [./backend/data]
#
set -euo pipefail

DATA_DIR="${DATA_DIR:-./backend/data}"
DATABASE_URL="${DATABASE_URL:-}"
CONFIRM="${CONFIRM:-no}"

archive="${1:-}"
[[ "${2:-}" == "--yes" ]] && CONFIRM="yes"

log() { printf '[restore %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

[[ -n "${archive}" ]] || die "Usage: restore.sh <backup.tar.gz> --yes"
[[ -f "${archive}" ]] || die "Backup file not found: ${archive}"
[[ "${CONFIRM}" == "yes" ]] || die "Refusing to run without --yes (this overwrites live data)"

# ── Verify checksum if present ────────────────────────────────────────────────
if [[ -f "${archive}.sha256" ]]; then
    log "Verifying checksum"
    (cd "$(dirname "${archive}")" && sha256sum -c "$(basename "${archive}").sha256") \
        || die "Checksum verification failed — aborting"
fi

workdir="$(mktemp -d)"
cleanup() { rm -rf "${workdir}"; }
trap cleanup EXIT

log "Extracting archive"
tar -xzf "${archive}" -C "${workdir}"

# ── Restore metadata database ─────────────────────────────────────────────────
if [[ -f "${workdir}/metadata.pgdump" ]]; then
    [[ -n "${DATABASE_URL}" ]] || die "Backup contains a Postgres dump but DATABASE_URL is not set"
    pg_uri="${DATABASE_URL/+psycopg/}"
    log "Restoring Postgres database (clean + create)"
    pg_restore --clean --if-exists --no-owner --dbname="${pg_uri}" "${workdir}/metadata.pgdump"
elif [[ -f "${workdir}/app.db" ]]; then
    log "Restoring SQLite database to ${DATA_DIR}/app.db"
    mkdir -p "${DATA_DIR}"
    cp "${workdir}/app.db" "${DATA_DIR}/app.db"
else
    log "WARNING: no database payload found in backup"
fi

# ── Restore dataset files ─────────────────────────────────────────────────────
if [[ -f "${workdir}/data.tar.gz" ]]; then
    log "Restoring dataset files into ${DATA_DIR}"
    mkdir -p "${DATA_DIR}"
    tar -xzf "${workdir}/data.tar.gz" -C "${DATA_DIR}"
fi

log "Restore complete. Restart the backend so it reconnects to the restored state."
