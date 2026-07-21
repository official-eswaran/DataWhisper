# Go-live checklist (issue #5)

All engineering work is merged. What remains is **operational and business
work that cannot be committed as code** — provisioning real infrastructure,
holding real secrets, and signing real documents. Work top to bottom; each
item says who does it and how to verify it's actually done.

Related: [DEPLOYMENT.md](DEPLOYMENT.md) · [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md)

---

## 1. Provision infrastructure

Terraform in `deploy/terraform` provisions RDS (Postgres, Multi-AZ),
ElastiCache (Redis), and the S3 dataset bucket.

- [ ] `cd deploy/terraform && terraform init && terraform plan`
- [ ] Review the plan (instance sizes, retention, Multi-AZ) — then `terraform apply`
- [ ] Record outputs: DB endpoint, Redis endpoint, S3 bucket name
- [ ] **Verify:** `terraform output` shows all three; RDS says Multi-AZ = yes

## 2. Secrets management

Never put real secrets in `deploy/k8s/secret.example.yaml` — it is an example only.

- [ ] Generate a production `SECRET_KEY`:
      `python3 -c "import secrets; print(secrets.token_hex(32))"`
- [ ] Install External Secrets Operator / Sealed Secrets / Vault CSI
- [ ] Store and sync: `SECRET_KEY`, `DATABASE_URL`, `ADMIN_PASSWORD`,
      `MANAGER_PASSWORD`, and (optional) `SENTRY_DSN`
- [ ] **Verify:** `kubectl get secret datawhisper-secrets -o jsonpath='{.data}' | jq keys`
      lists every key; nothing secret is in git

## 3. Configure the app for your environment

Edit `deploy/k8s/config.yaml` before applying:

- [ ] `ALLOWED_ORIGINS` — your real domain, **never** `*` (the app refuses to
      start with `*` when `DEBUG=false`)
- [ ] `DATASET_S3_BUCKET` — the bucket from step 1
- [ ] Grant pods S3 access via IRSA (EKS) / Workload Identity (GKE), or AWS
      creds in the Secret
- [ ] **Verify:** a pod can `aws s3 ls s3://<bucket>` (or the app's upload path works)

## 4. Database migration

- [ ] Build and push images (backend + frontend) to your registry; update the
      image tags in `deploy/k8s/*.yaml`
- [ ] Run the migration Job **before** serving traffic:
      `kubectl apply -f deploy/k8s/migration-job.yaml`
- [ ] **Verify:** `kubectl logs job/datawhisper-migrate` ends at revision
      `c7e1a2f4b9d0` (org plan + usage_counters) with no errors

## 5. Deploy

- [ ] `kubectl apply -k deploy/k8s`
- [ ] **Verify:** all pods Ready; `/health/ready` returns 200 with
      `components.database == "ok"`
- [ ] **Verify:** HPA present (`kubectl get hpa`), PDB present, ingress resolves

## 6. LLM (Ollama)

- [ ] Schedule Ollama on GPU nodes (see `deploy/k8s/ollama.yaml`)
- [ ] Pull the model: `kubectl exec deploy/ollama -- ollama pull llama3.2:3b`
- [ ] **Verify:** `/health/ready` reports `components.ollama == "ok"`

## 7. Observability

- [ ] Point Prometheus at the pods (they already carry `prometheus.io/scrape`
      annotations); confirm `http_requests_total` and `llm_cache_hits_total` appear
- [ ] Import/build Grafana dashboards for latency, error rate, LLM cache hit ratio
- [ ] Set `SENTRY_DSN` (Secret) to enable error tracking — no-op if unset
- [ ] Optional tracing: set `OTEL_EXPORTER_OTLP_ENDPOINT` to your collector
      (Tempo/Jaeger) — no-op if unset
- [ ] **Alerts** (page someone): `/health/ready` failing, 5xx rate > 1%,
      p95 query latency > SLO, DB connections saturated, disk/PVC pressure
- [ ] **Verify:** trigger a test alert and confirm it reaches the on-call channel

## 8. Capacity baseline

- [ ] Run the k6 suite against staging:
      `BASE_URL=https://staging.example.com k6 run loadtest/k6-login-upload-query.js`
- [ ] Record the numbers in `loadtest/README.md` (the baseline template is there)
- [ ] Tune the query p95 threshold to your real hardware — the committed 8s is a
      placeholder
- [ ] **Verify:** thresholds pass at your expected peak concurrency

## 9. Disaster recovery drill

Do **not** skip this — an untested backup is not a backup.

- [ ] Confirm the backup CronJob runs (`deploy/k8s/backup-cronjob.yaml`)
- [ ] Restore a **real** backup into a scratch environment using
      `scripts/restore.sh` (see [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md))
- [ ] Time it — confirm RPO ≤ 15 min and RTO ≤ 1 h actually hold
- [ ] **Verify:** the restored app serves a known query with expected data

## 10. Security review

- [ ] Confirm CI is green on `master` (pip-audit, npm audit, Trivy, coverage gate)
- [ ] Confirm TLS terminates at the ingress and HTTP redirects to HTTPS
- [ ] Rotate the seeded demo accounts (`ceo` / `manager`) or disable them
- [ ] Consider an external penetration test before onboarding enterprise customers
- [ ] **Verify:** `/api/audit/verify` returns an intact hash chain

---

## Business / compliance (not engineering)

These block *selling*, not *deploying*. Get legal counsel — the items below are
a checklist, not legal advice.

- [ ] **Privacy Policy** — what you collect, why, retention, sub-processors
- [ ] **Terms of Service**
- [ ] **DPA** (Data Processing Agreement) — required by most EU/UK customers
- [ ] **SOC 2** — only if selling to enterprise. Pick an auditor, map controls
      (the hash-chained audit log, RBAC, and DR runbook already cover several),
      then run the observation window (Type II is typically 3–12 months)
- [ ] **Stripe billing** — only if charging. Per-tenant quotas and usage metering
      already exist (`/api/usage`, `usage_counters`), so wiring Stripe means:
      map plans → `PLAN_LIMITS`, subscribe to webhooks, and call `set_org_plan`
      on subscription change

---

## Sign-off

Go live only when every box above is ticked and:

- [ ] A DR restore has been performed and timed
- [ ] Alerts are firing to a real on-call rotation
- [ ] Someone owns the pager for launch week
