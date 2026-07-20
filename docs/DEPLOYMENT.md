# Production Deployment (Kubernetes)

This guide deploys DataWhisper to a Kubernetes cluster with managed Postgres and
Redis. For a single-box demo use `docker compose` instead (see the README).

## Architecture

```
                 Internet
                    │  HTTPS (cert-manager)
             ┌──────▼───────┐
             │  Ingress-nginx│  /api → backend   (SSE, buffering off)
             │              │  /    → frontend
             └───┬──────┬───┘
        ┌────────▼─┐  ┌─▼─────────┐
        │ frontend │  │  backend  │  2+ replicas, HPA 2–10, PDB
        │ (nginx)  │  │ (gunicorn)│  probes: /health/live, /health/ready
        └──────────┘  └──┬─────┬──┘
                 ┌────────┘     └────────┐
          ┌──────▼─────┐          ┌──────▼──────┐        ┌───────────┐
          │  Postgres  │          │    Redis    │        │  Ollama   │
          │ (RDS, HA)  │          │(ElastiCache)│        │ (GPU pool)│
          └────────────┘          └─────────────┘        └───────────┘
```

## 1. Provision infrastructure (Terraform)

```bash
cd deploy/terraform
terraform init
terraform apply \
  -var="vpc_id=vpc-…" \
  -var="private_subnet_ids=[\"subnet-a\",\"subnet-b\"]" \
  -var="app_security_group_id=sg-…" \
  -var="db_password=$(openssl rand -base64 24)"
# Outputs: database_url, redis_url, backup_bucket
```

RDS is Multi-AZ with 14-day PITR; ElastiCache is a 2-node replication group with
automatic failover; the S3 bucket is versioned, encrypted, and public-access
blocked.

## 2. Build & push images

```bash
docker build -t $REGISTRY/datawhisper-backend:$TAG ./backend
docker build -t $REGISTRY/datawhisper-frontend:$TAG ./frontend
docker push $REGISTRY/datawhisper-backend:$TAG
docker push $REGISTRY/datawhisper-frontend:$TAG
# Update image references in deploy/k8s/*.yaml (or via a kustomize image override).
```

## 3. Create the Secret

Use a secrets manager in production (External Secrets Operator / Sealed Secrets).
For a manual bootstrap, copy `secret.example.yaml`, fill in the Terraform
outputs (`database_url`, `redis_url`) and a generated `SECRET_KEY`, then apply.

## 4. Migrate, then deploy

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/config.yaml
kubectl apply -f <your-filled-in-secret>.yaml

# Run migrations to completion FIRST (single Job avoids multi-replica races).
kubectl apply -f deploy/k8s/migration-job.yaml
kubectl -n datawhisper wait --for=condition=complete job/datawhisper-migrate --timeout=300s

# Then the rest of the stack.
kubectl apply -k deploy/k8s
```

Pull the model into Ollama once it is running:

```bash
kubectl -n datawhisper exec deploy/ollama -- ollama pull llama3.2:3b
```

## 5. Verify

```bash
kubectl -n datawhisper get pods
kubectl -n datawhisper rollout status deploy/datawhisper-backend
# Readiness gate + audit integrity
curl -fsS https://data.example.com/health/ready
```

## Scaling & HA notes

- The backend is stateless between requests (state is in Postgres + Redis), so it
  scales horizontally; the HPA targets 70% CPU across 2–10 replicas, and the PDB
  keeps ≥1 pod during disruptions.
- `WEB_CONCURRENCY` (gunicorn workers) is safe to raise because Redis backs the
  conversation store and rate limiter.
- Per-session DuckDB files sit on a ReadWriteMany volume so any replica can serve
  any session. The follow-up to remove even this coupling is migrating datasets
  to object storage (tracked in the backlog).
- Ollama is the throughput bottleneck — schedule it on GPU nodes and scale its
  replicas independently of the backend.

## Rollback

```bash
kubectl -n datawhisper rollout undo deploy/datawhisper-backend
```

Migrations are additive and backward-compatible within a release; if a rollback
crosses a schema change, restore from backup per `docs/DISASTER_RECOVERY.md`.
