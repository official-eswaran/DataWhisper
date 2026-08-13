#!/usr/bin/env bash
#
# DataWhisper restore drill — runs backup.sh and restore.sh end to end against a
# throwaway database and data directory, and fails if the round trip loses
# anything. Issue #18.
#
# "An untested backup is not a backup" has been in DISASTER_RECOVERY.md since
# the runbook was written, and until this script existed neither backup.sh nor
# restore.sh had ever been executed. This is not the production drill — see
# "What this does not prove" below — but it is the part that can run on every
# commit, and it is what turns the quarterly drill into "execute a checklist"
# rather than "find out whether the scripts work while the site is down".
#
# Usage:
#   ./scripts/restore_drill.sh                 # SQLite path (no services needed)
#   DRILL_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/drill \
#     ./scripts/restore_drill.sh               # the path production uses
#
# Everything is created under a temporary directory and removed on exit. The
# drill refuses to touch a database that already holds its fixture org.
#
# ## What this proves
#   * backup.sh produces a restorable archive, and restore.sh restores it
#   * the metadata DB round-trips: org, owner, and the audit hash chain still
#     verifies after the restore (the runbook's headline post-restore check)
#   * dataset files come back byte-identical
#   * the destruction step really destroyed something first, so a no-op restore
#     cannot pass
#   * restore.sh refuses to run without --yes, and aborts on a bad checksum
#
# ## What this does not prove
#   * Postgres PITR / managed snapshots — the RPO ≤ 15 min target rests on those
#   * cross-region recovery, DNS, or the k8s rollout in procedure 3
#   * the real RTO. The elapsed time printed below is for a fixture of a dozen
#     rows; it says nothing about restoring a production-sized database.
#
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
drill_root="$(mktemp -d)"
data_dir="${drill_root}/data"
backup_root="${drill_root}/backups"
state_file="${drill_root}/state.json"
started_at="$(date +%s)"

log()  { printf '\n[drill %s] == %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die()  { printf '[drill] ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { rm -rf "${drill_root}"; }
trap cleanup EXIT

# The app reads DATABASE_DIR; the shell scripts read DATA_DIR. They must point
# at the same place or backup.sh's SQLite branch cannot find app.db.
export DATA_DIR="${data_dir}"
export DATABASE_DIR="${data_dir}"
export BACKUP_ROOT="${backup_root}"
export DATABASE_URL="${DRILL_DATABASE_URL:-}"
export DEBUG="${DEBUG:-true}"
export SECRET_KEY="${SECRET_KEY:-$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
# Nothing here should invent accounts of its own; the fixture is the only state.
export SEED_DEMO_DATA=false

mkdir -p "${data_dir}" "${backup_root}"

fixture() {
    (cd "${repo_root}/backend" && PYTHONPATH=. python3 \
        "${repo_root}/scripts/restore_drill_fixture.py" "$1" "${state_file}")
}

if [[ -n "${DATABASE_URL}" ]]; then
    log "Mode: Postgres — ${DATABASE_URL%%:*}://…  (the path production uses)"
    command -v pg_dump    >/dev/null || die "pg_dump not found; install postgresql-client"
    command -v pg_restore >/dev/null || die "pg_restore not found; install postgresql-client"
else
    log "Mode: SQLite (set DRILL_DATABASE_URL to drill the Postgres path)"
fi

# ── 1. Schema ─────────────────────────────────────────────────────────────────
log "Creating schema (alembic upgrade head)"
(cd "${repo_root}/backend" && python3 -m alembic upgrade head >/dev/null) \
    || die "alembic upgrade failed — the drill cannot seed a schema-less database"

# ── 2. Seed known state ───────────────────────────────────────────────────────
log "Seeding fixture state"
fixture seed

# ── 3. Back it up ─────────────────────────────────────────────────────────────
log "Running backup.sh"
archive="$("${repo_root}/scripts/backup.sh" | tail -n 1)"
[[ -f "${archive}" ]] || die "backup.sh did not produce an archive"
[[ -f "${archive}.sha256" ]] || die "backup.sh produced no checksum file"
log "Archive: ${archive} ($(du -h "${archive}" | cut -f1))"

# ── 4. Destroy it ─────────────────────────────────────────────────────────────
# The step that makes the rest mean anything.
log "Destroying the live state"
if [[ -n "${DATABASE_URL}" ]]; then
    psql "${DATABASE_URL/+psycopg/}" -q -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
else
    rm -f "${data_dir}/app.db" "${data_dir}/app.db-wal" "${data_dir}/app.db-shm"
fi
rm -rf "${data_dir:?}/restore-drill-session.duckdb" "${data_dir:?}/uploads"

log "Confirming the destruction was real"
fixture assert-destroyed

# ── 5. Safety properties of restore.sh, checked before the real restore ───────
log "restore.sh must refuse without --yes"
if "${repo_root}/scripts/restore.sh" "${archive}" >/dev/null 2>&1; then
    die "restore.sh ran a destructive restore without --yes"
fi

log "restore.sh must abort on a tampered archive"
cp "${archive}" "${drill_root}/tampered.tar.gz"
cp "${archive}.sha256" "${drill_root}/tampered.tar.gz.sha256"
# Keep the checksum file pointing at the original name so it fails on content,
# not on a missing file.
sed -i "s|$(basename "${archive}")|tampered.tar.gz|" "${drill_root}/tampered.tar.gz.sha256"
printf 'corruption' >> "${drill_root}/tampered.tar.gz"
if "${repo_root}/scripts/restore.sh" "${drill_root}/tampered.tar.gz" --yes >/dev/null 2>&1; then
    die "restore.sh restored an archive whose checksum did not match"
fi

# ── 6. Restore for real ───────────────────────────────────────────────────────
log "Running restore.sh"
"${repo_root}/scripts/restore.sh" "${archive}" --yes

# ── 7. Verify ─────────────────────────────────────────────────────────────────
log "Verifying the restored state"
fixture verify

elapsed=$(( $(date +%s) - started_at ))
log "DRILL PASSED in ${elapsed}s (fixture-sized; not a production RTO)"
