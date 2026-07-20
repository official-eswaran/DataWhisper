# Disaster Recovery Runbook

Scope: recovering DataWhisper after data loss, corruption, or a region/cluster
failure. Owner: platform on-call. Review cadence: quarterly (test a restore).

## Recovery objectives

| Objective | Target | How it's met |
|---|---|---|
| **RPO** (max data loss) | ≤ 15 minutes | Managed Postgres PITR (continuous WAL) + hourly dataset backups |
| **RTO** (max downtime) | ≤ 1 hour | Restore from latest backup + redeploy from images |

## What must be backed up

| Asset | Store | Backup method |
|---|---|---|
| Metadata DB (users, orgs, audit, sessions, tokens) | Postgres | `pg_dump` (hourly) **+** managed PITR/snapshots |
| Per-session datasets (`*.duckdb`) + uploads | `DATA_DIR` volume / object storage | `backup.sh` archive (hourly) |
| Secrets (`SECRET_KEY`, DB creds) | Secrets manager | Managed by the secrets store; documented separately |
| Container images | Registry | Immutable tags per release |

> The audit log is **hash-chained** (`/api/audit/verify`). After any restore,
> run verify per org to confirm the chain is intact end-to-end.

## Backup

```bash
# One-off / cron on a host with pg_dump + network access to the DB
DATABASE_URL="postgresql+psycopg://user:pass@host:5432/datawhisper" \
DATA_DIR=/var/lib/datawhisper/data \
BACKUP_S3_URI=s3://acme-datawhisper-backups/prod \
./scripts/backup.sh
```

Schedule hourly via cron or the Kubernetes CronJob
(`deploy/k8s/backup-cronjob.yaml`). Retention defaults to 14 local days plus
whatever lifecycle policy the object-storage bucket enforces.

## Restore

```bash
# DESTRUCTIVE — overwrites the target DB and dataset files.
DATABASE_URL="postgresql+psycopg://user:pass@host:5432/datawhisper" \
DATA_DIR=/var/lib/datawhisper/data \
./scripts/restore.sh /path/to/datawhisper-backup-YYYYMMDDTHHMMSSZ.tar.gz --yes
```

The script verifies the SHA-256 checksum before restoring and refuses to run
without `--yes`.

## Recovery procedures

### 1. Corrupted / lost metadata database
1. Provision a fresh Postgres (or use PITR to a timestamp just before the incident).
2. If using a `pg_dump` archive: `restore.sh <archive> --yes`.
3. `alembic upgrade head` (no-op if the dump is already at head).
4. Roll the backend (`kubectl rollout restart deploy/datawhisper-backend`).
5. Verify: `GET /health/ready` is 200; `GET /api/audit/verify` returns `valid:true`.

### 2. Lost dataset files (DuckDB/uploads)
1. `restore.sh` extracts `data.tar.gz` into `DATA_DIR`.
2. Sessions whose files predate the last backup may be missing — users re-upload.
   (Migrating datasets to versioned object storage removes this gap; see backlog.)

### 3. Full region / cluster loss
1. Stand up infra in the recovery region with Terraform (`deploy/terraform`).
2. Restore Postgres from cross-region snapshot / PITR.
3. Restore dataset archive from the object-storage bucket (replicated).
4. Apply `deploy/k8s` manifests; point DNS at the new ingress.
5. Run the post-restore verification checklist below.

## Post-restore verification checklist
- [ ] `GET /health/ready` → 200, `database: ok`
- [ ] Login with a known account succeeds
- [ ] `GET /api/audit/verify` → `valid: true` for sampled orgs
- [ ] A representative session can be queried and exported
- [ ] Metrics scrape (`/metrics`) shows traffic; error rate nominal

## Testing
Perform a **restore drill** into a staging environment quarterly. Record the
measured RTO and any deviation from this runbook. A backup that has never been
restored is not a backup.
