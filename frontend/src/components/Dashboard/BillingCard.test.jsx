import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import toast from "react-hot-toast";
import BillingCard from "./BillingCard";
import { getInvoices, openBillingPortal, startCheckout } from "../../services/api";

vi.mock("../../services/api", () => ({
  startCheckout: vi.fn(),
  openBillingPortal: vi.fn(),
  getInvoices: vi.fn(),
}));

vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const enabled = (over = {}) => ({
  enabled: true,
  plan: "free",
  status: "none",
  has_subscription: false,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  // The invoice section (#31) loads on mount for owners. Default to an empty
  // list so the tests above it stay about checkout and the portal.
  getInvoices.mockResolvedValue({ data: { invoices: [] } });
});

test("renders nothing when the deployment has no Stripe configured", () => {
  const { container } = render(
    <BillingCard billing={{ enabled: false, plan: "free" }} isOwner />
  );
  expect(container).toBeEmptyDOMElement();
});

test("renders nothing before billing status has loaded", () => {
  const { container } = render(<BillingCard billing={null} isOwner />);
  expect(container).toBeEmptyDOMElement();
});

test("an owner on free is offered both paid plans", () => {
  render(<BillingCard billing={enabled()} isOwner />);
  expect(screen.getByRole("button", { name: /upgrade to pro/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /upgrade to enterprise/i })).toBeInTheDocument();
});

test("the current plan is not offered as an upgrade", () => {
  render(<BillingCard billing={enabled({ plan: "pro" })} isOwner />);
  expect(screen.queryByRole("button", { name: /upgrade to pro/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /upgrade to enterprise/i })).toBeInTheDocument();
});

test("non-owners see no billing actions", () => {
  render(<BillingCard billing={enabled()} isOwner={false} />);
  expect(screen.queryByRole("button", { name: /upgrade/i })).not.toBeInTheDocument();
  expect(screen.getByText(/ask your workspace owner/i)).toBeInTheDocument();
});

test("manage billing shows only once there is a subscription", () => {
  const { rerender } = render(<BillingCard billing={enabled()} isOwner />);
  expect(screen.queryByRole("button", { name: /manage billing/i })).not.toBeInTheDocument();

  rerender(
    <BillingCard billing={enabled({ plan: "pro", has_subscription: true })} isOwner />
  );
  expect(screen.getByRole("button", { name: /manage billing/i })).toBeInTheDocument();
});

test("past_due warns without claiming the plan was lost", () => {
  render(<BillingCard billing={enabled({ plan: "pro", status: "past_due" })} isOwner />);
  expect(screen.getByText(/couldn't process your last payment/i)).toBeInTheDocument();
  expect(screen.getByText(/still active/i)).toBeInTheDocument();
});

test("cancelled subscriptions are reported as back on free", () => {
  render(<BillingCard billing={enabled({ status: "canceled" })} isOwner />);
  expect(screen.getByText(/cancelled/i)).toBeInTheDocument();
});

test("clicking upgrade redirects the browser to Stripe", async () => {
  startCheckout.mockResolvedValue({ data: { checkout_url: "https://checkout.stripe.com/x" } });
  // jsdom refuses real navigation, so swap location for a plain object.
  delete window.location;
  window.location = { href: "" };

  render(<BillingCard billing={enabled()} isOwner />);
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /upgrade to pro/i }));
  });

  await waitFor(() => expect(window.location.href).toBe("https://checkout.stripe.com/x"));
  expect(startCheckout).toHaveBeenCalledWith("pro");
});

test("manage billing redirects to the Stripe portal", async () => {
  openBillingPortal.mockResolvedValue({ data: { portal_url: "https://billing.stripe.com/y" } });
  delete window.location;
  window.location = { href: "" };

  render(<BillingCard billing={enabled({ plan: "pro", has_subscription: true })} isOwner />);
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /manage billing/i }));
  });

  await waitFor(() => expect(window.location.href).toBe("https://billing.stripe.com/y"));
});

test("a failed checkout surfaces an error and re-enables the button", async () => {
  const toast = (await import("react-hot-toast")).default;
  startCheckout.mockRejectedValue({ response: { status: 403 } });

  render(<BillingCard billing={enabled()} isOwner />);
  const btn = screen.getByRole("button", { name: /upgrade to pro/i });
  await act(async () => {
    await userEvent.click(btn);
  });

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Only the workspace owner can manage billing")
  );
  expect(btn).not.toBeDisabled();
});

// ── Billing history (#31) ─────────────────────────────────────────────────────

const INVOICE = {
  id: "in_1",
  number: "DW-0001",
  status: "paid",
  amount_due: 2900,
  amount_paid: 2900,
  currency: "usd",
  created: 1_760_000_000,
  hosted_invoice_url: "https://invoice.stripe.com/i/1",
  invoice_pdf: "https://invoice.stripe.com/i/1.pdf",
};

async function renderWithInvoices(invoices, props = {}) {
  getInvoices.mockResolvedValue({ data: { invoices } });
  render(<BillingCard billing={enabled()} isOwner {...props} />);
  await screen.findByRole("heading", { name: /billing history/i });
}

test("an owner's invoices are listed", async () => {
  await renderWithInvoices([INVOICE]);
  const row = await screen.findByRole("row", { name: /DW-0001/ });
  expect(row).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /View DW-0001/ })).toHaveAttribute(
    "href",
    "https://invoice.stripe.com/i/1",
  );
});

test("amounts are formatted from the minor unit", async () => {
  await renderWithInvoices([INVOICE]);
  // 2900 cents, not 2900 dollars — the API deliberately does not divide.
  expect(await screen.findByText("$29.00")).toBeInTheDocument();
});

test("a zero-decimal currency is not divided by 100 on screen", async () => {
  await renderWithInvoices([{ ...INVOICE, currency: "jpy", amount_paid: 2900 }]);
  // Intl knows JPY has no minor unit; a hand-rolled /100 would print ¥29.
  expect(await screen.findByText(/2,900/)).toBeInTheDocument();
});

test("an unknown currency code does not take the page down", async () => {
  await renderWithInvoices([{ ...INVOICE, currency: "notacurrency" }]);
  expect(await screen.findByRole("row", { name: /DW-0001/ })).toBeInTheDocument();
});

test("a draft invoice shows no link rather than a dead one", async () => {
  await renderWithInvoices([
    { ...INVOICE, status: "draft", number: "", hosted_invoice_url: "" },
  ]);
  const row = await screen.findByRole("row", { name: /draft/ });

  // Asserted on the element, not the role: `<a href="">` is not exposed as a
  // link at all, so a role query would pass against exactly the dead anchor
  // this is meant to rule out.
  expect(row.querySelector("a")).toBeNull();
  expect(row).toHaveTextContent("—");
});

test("the invoice link cannot be used for reverse tabnabbing", async () => {
  await renderWithInvoices([INVOICE]);
  const link = await screen.findByRole("link", { name: /View DW-0001/ });
  // target=_blank without noopener hands the opened Stripe page a live
  // window.opener back into this app.
  expect(link).toHaveAttribute("target", "_blank");
  expect(link.getAttribute("rel")).toMatch(/noopener/);
  expect(link.getAttribute("rel")).toMatch(/noreferrer/);
});

test("the date comes from unix seconds, not milliseconds", async () => {
  // 1_760_000_000 is October 2025. Read as milliseconds it lands in January
  // 1970, which is the mistake this pins — asserted at month precision so the
  // runner's timezone cannot flip the day and fail the build.
  await renderWithInvoices([INVOICE]);
  const row = await screen.findByRole("row", { name: /DW-0001/ });
  expect(row).toHaveTextContent(/Oct/);
  expect(row).toHaveTextContent(/2025/);
});

test("an org with no invoices is told so", async () => {
  await renderWithInvoices([]);
  expect(screen.getByText("No invoices yet.")).toBeInTheDocument();
});

test("a failed load does not read as an empty history", async () => {
  // The #82 lesson: "we don't know" and "there is nothing" are different
  // answers, and only one of them is true after a failed fetch.
  getInvoices.mockRejectedValue(new Error("502"));
  render(<BillingCard billing={enabled()} isOwner />);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/couldn't load your invoices/i);
  expect(alert).toHaveTextContent(/doesn't mean you have none/i);
  expect(screen.queryByText("No invoices yet.")).not.toBeInTheDocument();
});

test("a failed load does not interrupt with a toast", async () => {
  getInvoices.mockRejectedValue(new Error("502"));
  render(<BillingCard billing={enabled()} isOwner />);
  await screen.findByRole("alert");
  expect(toast.error).not.toHaveBeenCalled();
});

test("a non-owner is not shown billing history and it is not fetched", async () => {
  render(<BillingCard billing={enabled()} isOwner={false} />);
  await screen.findByText(/billing is owner-only/i);
  expect(screen.queryByRole("heading", { name: /billing history/i })).not.toBeInTheDocument();
  expect(getInvoices).not.toHaveBeenCalled();
});

test("the table is announced with column headers", async () => {
  await renderWithInvoices([INVOICE]);
  const table = await screen.findByRole("table", { name: /recent invoices/i });
  const headers = within(table)
    .getAllByRole("columnheader")
    .map((h) => h.textContent.trim());
  expect(headers).toEqual(["Date", "Amount", "Status", "Invoice"]);
});

test("an unpaid invoice shows what is owed", async () => {
  // amount_paid is 0 until it settles, so the row would read "$0.00" if the
  // amount fell back the other way.
  await renderWithInvoices([
    { ...INVOICE, status: "open", amount_paid: 0, amount_due: 4500 },
  ]);
  expect(await screen.findByText("$45.00")).toBeInTheDocument();
});

test("an invoice with a page but no number still links", async () => {
  await renderWithInvoices([{ ...INVOICE, number: "" }]);
  const link = await screen.findByRole("link", { name: /^view$/i });
  expect(link).toHaveAttribute("href", INVOICE.hosted_invoice_url);
});

test("an invoice with no created date renders a blank cell, not 1970", async () => {
  await renderWithInvoices([{ ...INVOICE, created: 0 }]);
  const row = await screen.findByRole("row", { name: /DW-0001/ });
  expect(row).not.toHaveTextContent(/1970/);
});

test("a response without an invoices key is treated as none", async () => {
  getInvoices.mockResolvedValue({ data: {} });
  render(<BillingCard billing={enabled()} isOwner />);
  expect(await screen.findByText("No invoices yet.")).toBeInTheDocument();
});

test("an invoice with no currency is still shown", async () => {
  await renderWithInvoices([{ ...INVOICE, currency: "" }]);
  expect(await screen.findByRole("row", { name: /DW-0001/ })).toBeInTheDocument();
});
