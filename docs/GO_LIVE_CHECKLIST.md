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

### Outbound mail (#21)

Open signup is an abuse vector until a verification mail actually arrives. With
`SMTP_HOST` unset the app is a working self-hosted deployment whose verification
links go to the log — fine for a private install, **not** for public signup.

- [ ] `SMTP_HOST`, `SMTP_PORT` (587 for STARTTLS, 465 for implicit TLS)
- [ ] `SMTP_USERNAME` / `SMTP_PASSWORD` in the Secret, never in config
- [ ] `SMTP_FROM` — an address the provider lets you send as, or delivery fails
      with a confusing 5xx. Empty falls back to `SMTP_USERNAME`.
- [ ] `APP_BASE_URL` — the SPA's public origin. **Without it the mail contains a
      bare token instead of a link**, which is honest but unusable.
- [ ] Leave `SMTP_STARTTLS=true`. The mailer refuses to authenticate over an
      unencrypted connection, so turning it off with credentials set means mail
      silently stops rather than leaking the password.
- [ ] **Verify — the transport has never sent to a real server:**

      ```bash
      python3 - <<'EOF'
      from app.core import mailer
      print("configured:", mailer.configured())
      print("sent:", mailer.send_verification_email("you@yourdomain.com", "drill-token"))
      EOF
      ```

      Run it from `backend/` with the deployment's env. `sent: True` and a mail
      in the inbox is the whole check. `sent: False` means the reason is in the
      log — the mailer never raises, by design, so nothing else will tell you.

- [ ] **Then verify the flow, not just the transport:** register a real address,
      follow the link, confirm the account can query. The gate is per-org keyed
      on the *owner*, so test with a fresh org rather than an existing one.

### Signup captcha (#21)

The other half of the same abuse path, and independent of mail: verification
puts a cost on each *identity*, a captcha puts one on each *signup attempt*.
Unset, there is no challenge — the default, and unchanged for self-hosting.

- [ ] Create an [hCaptcha](https://www.hcaptcha.com/) or
      [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/) site.
      Both speak the same siteverify contract; `CAPTCHA_PROVIDER` picks which.
- [ ] `CAPTCHA_SITE_KEY` in config (it is public — the page renders it),
      `CAPTCHA_SECRET` in the Secret
- [ ] **Set both or neither.** Only the secret → the widget cannot render and
      *every signup is refused*. Only the site key → a challenge is shown and
      its answer never checked. The app logs a warning at startup for both,
      which is the first thing to check if signup breaks after enabling this.
- [ ] **Verify — no challenge has ever been solved against a real provider:**

      ```bash
      curl -s https://your-domain/api/auth/signup-config | jq
      ```

      Expect `captcha.site_key` to match the dashboard and `captcha.provider` to
      be right. The secret must **not** appear — if it does, stop and check
      which variable was set where.

- [ ] **Then verify the flow:** load `/signup` in a browser, confirm the widget
      renders and the submit button stays disabled until it is solved, and
      complete a real registration. Then submit with a tampered token (edit it
      in devtools) and confirm the API answers **400**, not 201.
- [ ] **Verify it fails closed:** block the provider's domain at the firewall
      and confirm registration returns **503** rather than succeeding. This is
      the property worth checking by hand — an abuse control that opens when the
      provider is unreachable is not a control, and it is the failure mode that
      would go unnoticed.

## 4. Database migration

- [ ] Build and push images (backend + frontend) to your registry; update the
      image tags in `deploy/k8s/*.yaml`
- [ ] Run the migration Job **before** serving traffic:
      `kubectl apply -f deploy/k8s/migration-job.yaml`
- [ ] **Verify:** `kubectl logs job/datawhisper-migrate` ends with no errors at
      the revision `cd backend && alembic heads` reports for the tag you
      deployed — check the two against each other rather than against a number
      written here. This line used to name `c7e1a2f4b9d0`, which went stale four
      migrations ago, and a checklist that certifies the wrong head is worse than
      one that certifies nothing: it signs off a database missing the Stripe
      billing, upload-calibration and audit-checkpoint tables. (As of 2026-07-28
      the head is `a81e5f30c6d2` — confirm, don't trust this line.)

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
- [ ] Demo accounts (`ceo` / `manager`) are **not seeded** in production —
      seeding is auto-off when `DEBUG=false` (issue #23). Create the first org
      via `/api/auth/register`. Only if you deliberately set `SEED_DEMO_DATA=true`
      do these exist; then rotate their passwords immediately.
- [ ] **Decide the signup model.** Public signup is open by default and gives
      every new org a free LLM quota (per-org quotas don't stop "make another
      org"). Registration is rate-limited to `RATE_LIMIT_REGISTER` (5/hour/IP),
      but that only slows single-IP abuse. Either set `SIGNUPS_OPEN=false`
      (invite-only) or add email verification / captcha before opening to the
      public — see issue #21. The rate limit + kill switch are in; verification
      is not.
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
- [ ] **Stripe billing** — the *code* is done (hosted Checkout + Billing Portal +
      signature-verified webhooks; see [BILLING.md](BILLING.md)). What remains is
      account setup, and it is genuinely non-code:
  - [ ] Create the Stripe account; complete business verification
  - [ ] Create one recurring price per paid plan (`pro`, `enterprise`)
  - [ ] Set `STRIPE_PRICE_*`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL` in config
  - [ ] Put `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` in the Secret
  - [ ] Register the webhook endpoint and subscribe the four subscription events
  - [ ] **Verify:** a test-mode checkout moves the org's plan, and cancelling in
        the portal drops it back to `free`

---

## Sign-off

Go live only when every box above is ticked and:

- [ ] A DR restore has been performed and timed
- [ ] Alerts are firing to a real on-call rotation
- [ ] Someone owns the pager for launch week
