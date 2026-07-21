import React, { useState } from "react";
import toast from "react-hot-toast";
import { FiAlertTriangle, FiCreditCard, FiExternalLink } from "react-icons/fi";
import { openBillingPortal, startCheckout } from "../../services/api";
import "./BillingCard.css";

// Plans the user can check out for, in upgrade order. "free" is absent on
// purpose: downgrading is a cancel action in the Stripe portal, not a checkout.
const PAID_PLANS = [
  { id: "pro", label: "Pro", blurb: "50,000 queries and 5,000 uploads a month" },
  { id: "enterprise", label: "Enterprise", blurb: "Unlimited queries and uploads" },
];

// Stripe keeps retrying a failed card for a while before giving up, so past_due
// still has the paid plan active — we warn rather than announce a downgrade.
function StatusNotice({ status }) {
  if (status === "past_due") {
    return (
      <p className="billing-warning" role="status">
        <FiAlertTriangle aria-hidden="true" />
        We couldn&apos;t process your last payment. Your plan is still active —
        update your card to avoid losing access.
      </p>
    );
  }
  if (status === "canceled") {
    return (
      <p className="billing-warning" role="status">
        <FiAlertTriangle aria-hidden="true" />
        Your subscription was cancelled, so you&apos;re back on the free plan.
      </p>
    );
  }
  return null;
}

function BillingCard({ billing, isOwner, onChange }) {
  const [busy, setBusy] = useState("");

  if (!billing) return null;

  // No Stripe keys on this deployment (self-hosted, or billing not set up yet).
  // Showing upgrade buttons that 503 would be worse than showing nothing.
  if (!billing.enabled) return null;

  const plan = billing.plan || "free";
  const upgrades = PAID_PLANS.filter((p) => p.id !== plan);

  // Sending the browser to Stripe rather than opening a tab: popup blockers eat
  // window.open on an async callback, and checkout should own the whole flow.
  const go = async (action, label) => {
    setBusy(label);
    try {
      const res = await action();
      const url = res.data.checkout_url || res.data.portal_url;
      if (!url) throw new Error("no redirect url");
      window.location.href = url;
    } catch (err) {
      const status = err?.response?.status;
      if (status === 403) toast.error("Only the workspace owner can manage billing");
      else if (status === 503) toast.error("Billing isn't configured on this deployment");
      else if (status === 400) toast.error("That plan isn't available for checkout");
      else toast.error("Couldn't reach Stripe — please try again");
      setBusy("");
      onChange?.();
    }
  };

  return (
    <section className="admin-section" aria-labelledby="billing-heading">
      <h3 id="billing-heading">
        <FiCreditCard aria-hidden="true" /> Plan &amp; billing
      </h3>

      <p className="admin-muted">
        You&apos;re on the <strong>{plan}</strong> plan
        {billing.status && billing.status !== "none" && ` (${billing.status})`}.
      </p>

      <StatusNotice status={billing.status} />

      {!isOwner ? (
        <p className="admin-muted">
          Ask your workspace owner to change the plan — billing is owner-only.
        </p>
      ) : (
        <div className="billing-actions">
          {upgrades.map((p) => (
            <button
              key={p.id}
              className="btn-primary"
              disabled={!!busy}
              aria-busy={busy === p.id}
              onClick={() => go(() => startCheckout(p.id), p.id)}
            >
              {busy === p.id ? "Opening Stripe…" : `Upgrade to ${p.label}`}
              <span className="billing-blurb">{p.blurb}</span>
            </button>
          ))}

          {billing.has_subscription && (
            <button
              className="btn-small"
              disabled={!!busy}
              aria-busy={busy === "portal"}
              onClick={() => go(openBillingPortal, "portal")}
            >
              {busy === "portal" ? "Opening…" : "Manage billing"}
              <FiExternalLink aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      <p className="billing-fineprint">
        Payments are handled by Stripe. Card details are never entered in or
        stored by DataWhisper.
      </p>
    </section>
  );
}

export default BillingCard;
