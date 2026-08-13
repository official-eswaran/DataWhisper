#!/usr/bin/env bash
#
# DataWhisper Stripe drill — exercises the money path end to end. Issue #19.
#
# The suite has 27 billing tests and none of them touch Stripe: events are fed
# straight to `handle_event` with signature verification stubbed, and outbound
# calls are monkeypatched. So two things were never executed by anything:
#
#   1. a genuinely signed webhook arriving over HTTP, through the raw-body path
#   2. the Stripe SDK actually putting a request on the wire
#
# This script does (1) with no Stripe account at all, and (2) against either
# stripe-mock or real test-mode keys.
#
# Usage:
#   ./scripts/stripe_drill.sh                        # webhook path only
#   STRIPE_API_BASE=http://localhost:12111 \
#     ./scripts/stripe_drill.sh                      # + SDK calls vs stripe-mock
#   STRIPE_SECRET_KEY=sk_test_… STRIPE_PRICE_PRO=price_… \
#     ./scripts/stripe_drill.sh                      # + SDK calls vs real Stripe
#
# ## What this proves
#   * a correctly signed event is accepted, applies, and is deduplicated on
#     replay
#   * a wrong signature and a tampered body are both rejected with 400, and
#     neither moves the plan
#   * past_due keeps the paid plan; a deleted subscription drops it to free
#   * (API modes) the SDK's requests are accepted by something that is not our
#     own stub
#
# ## What this does not prove
#   * that a human can complete a hosted Checkout — no browser is involved
#   * that Stripe's real event payloads match the shapes used here. Only a
#     `stripe trigger` against a real account settles that; see BILLING.md.
#   * anything about proration, dunning or invoices (#31)
#
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
drill_root="$(mktemp -d)"
port="${STRIPE_DRILL_PORT:-8099}"
base_url="http://127.0.0.1:${port}"
server_pid=""

log() { printf '\n[stripe-drill %s] == %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { printf '[stripe-drill] ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
    [[ -n "${server_pid}" ]] && kill "${server_pid}" 2>/dev/null || true
    rm -rf "${drill_root}"
}
trap cleanup EXIT

export DATABASE_DIR="${drill_root}/data"
export DATA_DIR="${drill_root}/data"
export DEBUG=true
export SEED_DEMO_DATA=false
export SECRET_KEY="${SECRET_KEY:-$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
# A real whsec_-shaped secret. The drill signs with it and the app verifies with
# it; neither side is stubbed, which is the whole point.
export STRIPE_WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-whsec_drill_$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
# Billing must be *enabled* for the webhook route to exist, so a key is always
# set. In webhook-only mode nothing ever calls out with it.
export STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY:-sk_test_drill_placeholder}"
export STRIPE_PRICE_PRO="${STRIPE_PRICE_PRO:-price_drill_pro}"
export STRIPE_SUCCESS_URL="${STRIPE_SUCCESS_URL:-https://example.invalid/billing/success}"
export STRIPE_CANCEL_URL="${STRIPE_CANCEL_URL:-https://example.invalid/billing/cancel}"

mkdir -p "${DATABASE_DIR}"

fixture() {
    (cd "${repo_root}/backend" && PYTHONPATH=. python3 \
        "${repo_root}/scripts/stripe_drill_fixture.py" "$@")
}

log "Creating schema (alembic upgrade head)"
(cd "${repo_root}/backend" && python3 -m alembic upgrade head >/dev/null) \
    || die "alembic upgrade failed"

log "Starting the app on ${base_url}"
(cd "${repo_root}/backend" && PYTHONPATH=. python3 -m uvicorn app.main:app \
    --host 127.0.0.1 --port "${port}" --log-level warning) &
server_pid=$!

for _ in $(seq 1 60); do
    if curl -fsS "${base_url}/health" >/dev/null 2>&1; then break; fi
    kill -0 "${server_pid}" 2>/dev/null || die "the app exited during startup"
    sleep 0.5
done
curl -fsS "${base_url}/health" >/dev/null 2>&1 || die "the app never became healthy on ${base_url}"

log "Webhook path (no Stripe account required)"
fixture webhooks "${base_url}" "${STRIPE_WEBHOOK_SECRET}"

if [[ -n "${STRIPE_API_BASE:-}" ]]; then
    log "Outbound SDK calls against stripe-mock (${STRIPE_API_BASE})"
    fixture api
elif [[ "${STRIPE_SECRET_KEY}" == sk_test_* && "${STRIPE_SECRET_KEY}" != sk_test_drill_placeholder ]]; then
    log "Outbound SDK calls against live Stripe test mode"
    fixture api
else
    log "Outbound SDK calls SKIPPED — set STRIPE_API_BASE (stripe-mock) or real sk_test_ keys"
fi

log "STRIPE DRILL PASSED"
