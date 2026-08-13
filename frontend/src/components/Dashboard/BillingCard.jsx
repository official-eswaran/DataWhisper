import React, { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { FiAlertTriangle, FiCreditCard, FiExternalLink } from "react-icons/fi";
import { getInvoices, openBillingPortal, startCheckout } from "../../services/api";
import "./BillingCard.css";

// Plans the user can check out for, in upgrade order. "free" is absent on
// purpose: downgrading is a cancel action in the Stripe portal, not a checkout.
const PAID_PLANS = [
  { id: "pro", label: "Pro", blurb: "50,000 queries and 5,000 uploads a month" },
  { id: "enterprise", label: "Enterprise", blurb: "Unlimited queries and uploads" },
];

// Stripe reports amounts in the currency's minor unit, and how many minor units
// make a major one is currency-specific: 100 for USD, **1 for JPY**. Dividing by
// 100 unconditionally prints ¥29 for a ¥2,900 invoice, which is why the API
// deliberately hands over the raw integer and the currency code.
//
// Intl already knows the answer, so ask it rather than keeping a list of
// zero-decimal currencies that would go stale.
function money(minorUnits, currency) {
  const code = (currency || "usd").toUpperCase();
  try {
    const formatter = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code,
    });
    const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
    return formatter.format(minorUnits / 10 ** digits);
  } catch {
    // An unknown currency code must not take the whole billing page down. The
    // 100 here is a guess, and the code is shown so the number is readable as
    // one rather than trusted as exact.
    return `${(minorUnits / 100).toFixed(2)} ${code}`;
  }
}

function invoiceDate(unixSeconds) {
  if (!unixSeconds) return "";
  return new Date(unixSeconds * 1000).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// Owner-only, because the route is: what the workspace pays and when is the
// owner's business rather than every member's.
function InvoiceHistory() {
  const [state, setState] = useState({ status: "loading", invoices: [] });

  useEffect(() => {
    let live = true;
    getInvoices()
      .then((res) => {
        if (live) setState({ status: "ready", invoices: res.data.invoices || [] });
      })
      .catch(() => {
        // Deliberately not a toast: a failed history load is not worth
        // interrupting someone who came here to upgrade, and the section says
        // so itself. It also must not read as "you have no invoices" (#82).
        if (live) setState({ status: "error", invoices: [] });
      });
    return () => {
      live = false;
    };
  }, []);

  return (
    <section className="billing-invoices" aria-labelledby="invoices-heading">
      <h4 id="invoices-heading">Billing history</h4>

      {state.status === "loading" && <p className="admin-muted">Loading invoices…</p>}

      {state.status === "error" && (
        <p className="billing-warning" role="alert">
          <FiAlertTriangle aria-hidden="true" />
          We couldn&apos;t load your invoices just now — this doesn&apos;t mean
          you have none. Try again shortly, or check the Stripe portal.
        </p>
      )}

      {state.status === "ready" && state.invoices.length === 0 && (
        <p className="admin-muted">No invoices yet.</p>
      )}

      {state.status === "ready" && state.invoices.length > 0 && (
        <table className="billing-invoice-table">
          <caption className="sr-only">Recent invoices for this workspace</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Amount</th>
              <th scope="col">Status</th>
              <th scope="col">
                <span className="sr-only">Invoice</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {state.invoices.map((inv) => (
              <tr key={inv.id}>
                <td>{invoiceDate(inv.created)}</td>
                <td>{money(inv.amount_paid || inv.amount_due, inv.currency)}</td>
                <td>{inv.status}</td>
                <td>
                  {inv.hosted_invoice_url ? (
                    <a
                      href={inv.hosted_invoice_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      View{inv.number ? ` ${inv.number}` : ""}
                      <FiExternalLink aria-hidden="true" />
                    </a>
                  ) : (
                    // A draft has no hosted page yet. A dead link is worse than
                    // no link.
                    <span className="admin-muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

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

      {isOwner && <InvoiceHistory />}

      <p className="billing-fineprint">
        Payments are handled by Stripe. Card details are never entered in or
        stored by DataWhisper.
      </p>
    </section>
  );
}

export default BillingCard;
