# Billing (Stripe)

DataWhisper bills through **Stripe hosted Checkout + Billing Portal**. Card
details never reach this backend, which keeps PCI scope at SAQ-A.

Billing is **opt-in**. With `STRIPE_SECRET_KEY` unset, `/api/billing/*` returns
503 and nothing else changes — plans stay manually settable through
`PUT /api/usage/plan`. That is the supported configuration for self-hosting.

Once billing **is** configured, Stripe becomes the single source of truth for a
plan: the webhook rewrites `organizations.plan` on every subscription change.
The manual `PUT /api/usage/plan` therefore returns **409** while billing is on —
a hand-set plan would be a free upgrade that the next webhook silently reverts.
Upgrades go through checkout, downgrades/cancellations through the portal.

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

### The drill (#19)

```bash
./scripts/stripe_drill.sh                       # webhook path, no account needed
STRIPE_API_BASE=http://localhost:12111 ./scripts/stripe_drill.sh   # + stripe-mock
```

CI runs both on every PR. The webhook half signs Stripe-shaped payloads with a
real HMAC and posts them to a running app, then checks what the plan actually
did — accepted, applied, deduplicated on replay, `past_due` keeps the plan,
deletion drops it, and a wrong signature or tampered body changes nothing. The
outbound half puts the SDK's requests on the wire against stripe-mock, which
validates them against Stripe's own OpenAPI spec.

**Neither half is the round trip #19 asks for.** No browser completes a hosted
Checkout, and no event Stripe actually generated is ever received.

### The real round trip — the #19 checklist

Needs test keys. Work through it in order and record the result of each line;
this is the whole of what #19 is asking for.

```bash
export STRIPE_SECRET_KEY=sk_test_…  STRIPE_PRICE_PRO=price_…
stripe listen --forward-to localhost:8000/api/billing/webhook   # prints whsec_…
export STRIPE_WEBHOOK_SECRET=whsec_…
./scripts/stripe_drill.sh          # runs the SDK half against real Stripe first
```

- [ ] `POST /api/billing/checkout` as an owner returns a `checkout.stripe.com`
      URL, and the session's `client_reference_id` is that org's id
- [ ] Paying with `4242 4242 4242 4242` in the browser completes and redirects
      to `STRIPE_SUCCESS_URL`
- [ ] `checkout.session.completed` arrives, is **signature-verified**, and the
      org moves to `pro` — check the org row, not the UI
- [ ] The plan moved because of the *webhook*, not the redirect: repeat with the
      success URL never opened and confirm the upgrade still happens
- [ ] `stripe trigger customer.subscription.updated` with a `past_due` status
      leaves the org on `pro` (the grace rule)
- [ ] Cancelling through the Billing Portal drops the org to `free`
- [ ] Replaying any delivery from the Stripe dashboard returns 200 and changes
      nothing (`stripe_events` dedup)
- [ ] **Compare a real payload against `_stripe_shaped_event` in the tests.**
      Any field the real event has and the fixture does not is a place the suite
      is guessing. This is how #93 stayed hidden.

### Local testing

```bash
stripe listen --forward-to localhost:8000/api/billing/webhook   # prints whsec_…
stripe trigger customer.subscription.updated
```

Use test keys (`sk_test_…`) and Stripe's test cards. Nothing in the test suite
touches Stripe — outbound calls are stubbed and most webhook tests are fed
directly to the handler.

**Except the ones that matter.** `tests/test_billing.py` now also signs
Stripe-shaped payloads with a real HMAC and posts them through the route with
nothing patched, because the stubs were hiding #93. If you add a webhook
behaviour, add it there too — a test that patches `verify_event` cannot tell you
whether a real event would have arrived.

---

## Things worth knowing before you touch this code

- **Never hand a Stripe SDK object to code that expects a dict (#93).**
  `StripeObject` was a `dict` subclass in older SDKs and is not in v15, and the
  recursive-conversion helper has been renamed. `verify_event` therefore returns
  `json.loads(payload)` — the bytes `construct_event` has just authenticated —
  which has no SDK version surface at all. Where an SDK object is unavoidable
  (`Subscription.retrieve`), `_as_dict` normalises it. This bug 500'd **every**
  real webhook from the day billing shipped until 2026-08-13, and the suite
  could not see it because it stubbed the exact function that failed.
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

- **Usage-based / metered billing.** `rows_processed` is capped per plan, but
  the count is never reported to Stripe as metered usage — hitting the ceiling
  blocks work rather than adding to the bill. Plans are flat-rate only.
- **Proration handling on plan switches** is left to Stripe's defaults.
- **Dunning emails** — configure them in the Stripe dashboard, not here.
