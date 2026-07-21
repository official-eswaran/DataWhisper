# Billing (Stripe)

DataWhisper bills through **Stripe hosted Checkout + Billing Portal**. Card
details never reach this backend, which keeps PCI scope at SAQ-A.

Billing is **opt-in**. With `STRIPE_SECRET_KEY` unset, `/api/billing/*` returns
503 and nothing else changes — plans stay manually settable through
`PUT /api/usage/plan`. That is the supported configuration for self-hosting.

---

## How entitlements flow

```
owner clicks Upgrade
  → POST /api/billing/checkout        (owner-only, org id from the JWT)
  → Stripe hosted Checkout page
  → customer pays
  → Stripe POSTs customer.subscription.* to /api/billing/webhook
  → signature verified → org plan updated → PLAN_LIMITS apply on next request
```

The **webhook is the source of truth**, not the browser redirect. The success
URL is just a landing page; a user who closes the tab, replays the redirect, or
hand-crafts the URL changes nothing. Never grant a plan from the redirect.

Quota enforcement itself is unchanged — `app/core/quota.py` reads
`organizations.plan` and applies `PLAN_LIMITS`. Stripe only decides which plan
an org is on.

### Status handling

| Stripe status | Effect |
|---|---|
| `active`, `trialing` | Paid plan applies |
| `past_due` | **Paid plan kept** — Stripe is still retrying the card |
| `canceled`, `unpaid` | Downgraded to `free`, subscription id cleared |
| unrecognised price | Downgraded to `free` (never entitle an unknown price) |

Grace on `past_due` is deliberate: an expiring card should not instantly cut off
a paying customer mid-month.

---

## Setup

1. **Create products/prices** in the Stripe dashboard — one recurring price per
   paid plan (`pro`, `enterprise`). Copy the `price_…` ids.
2. **Set config** (`deploy/k8s/config.yaml`): `STRIPE_PRICE_PRO`,
   `STRIPE_PRICE_ENTERPRISE`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`.
3. **Set secrets** (`datawhisper-secrets`): `STRIPE_SECRET_KEY`.
4. **Register the webhook**: dashboard → Developers → Webhooks → add
   `https://<your-domain>/api/billing/webhook`, subscribed to:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`

   Copy the signing secret (`whsec_…`) into `STRIPE_WEBHOOK_SECRET`. This is a
   **different value** from the API key — a common setup mistake.
5. **Run migrations** — `e3d9b5c1a740` adds the Stripe columns and the
   `stripe_events` idempotency table.
6. **Verify**: `GET /api/billing/` as an owner returns `"enabled": true`.

### Local testing

```bash
stripe listen --forward-to localhost:8000/api/billing/webhook   # prints whsec_…
stripe trigger customer.subscription.updated
```

Use test keys (`sk_test_…`) and Stripe's test cards. Nothing in the test suite
touches Stripe — outbound calls are stubbed and webhooks are fed directly to the
handler.

---

## Things worth knowing before you touch this code

- **The webhook needs the raw request body.** Signature verification is an HMAC
  over the exact bytes Stripe sent. Parsing to JSON and re-serialising changes
  the bytes and every signature fails. `stripe_webhook` reads
  `await request.body()` for this reason — don't "clean it up" into a Pydantic
  model.
- **Webhooks are delivered more than once.** Stripe retries until it gets a 2xx
  and can duplicate deliveries. Event ids are claimed in `stripe_events` via a
  primary-key insert, which is what makes deduplication atomic across replicas.
- **A non-2xx makes Stripe retry.** The handler returns 200 for events it
  deliberately ignores, and only 400 for genuinely unverifiable payloads.
- **Org id comes from the JWT, never the request body**, so an owner of org A
  cannot start a checkout that upgrades org B. The webhook cross-checks
  `client_reference_id` / subscription metadata, falling back to a customer-id
  lookup for subscriptions created directly in the dashboard.
- **`price → plan` is authoritative** over metadata. Metadata is only a fallback
  because it can be edited in the dashboard without changing what's charged.

---

## The UI

`BillingCard` (admin console → Plan & billing) is the only billing surface:

- It **renders nothing** when `GET /api/billing/` reports `enabled: false`, so a
  self-hosted deployment without Stripe shows no dead upgrade buttons.
- Upgrade actions are **owner-only** in the UI, matching the backend's 403.
  Non-owners see a note pointing at their owner instead.
- It offers every paid plan **except the current one**. Downgrading is a cancel
  in the Stripe portal, not a checkout.
- `past_due` warns that payment failed *while stating the plan is still active*,
  because that is what the backend actually does.
- The success redirect says the plan "will appear shortly" rather than
  confirming the upgrade — the webhook is what changes the plan and it can lag
  the user's return by a moment. Don't reword this into a promise.

`Dashboard` reads the `?status=` marker Stripe appends, lands the user on the
admin tab, toasts, and strips the param so a refresh doesn't replay it.

## Not implemented

- **Usage-based / metered billing.** `rows_processed` is metered in
  `usage_counters` but is not reported to Stripe and has no hard limit. Plans
  are flat-rate subscriptions only.
- **Proration handling on plan switches** is left to Stripe's defaults.
- **Dunning emails** — configure them in the Stripe dashboard, not here.
