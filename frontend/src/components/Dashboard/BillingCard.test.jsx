import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import BillingCard from "./BillingCard";
import { openBillingPortal, startCheckout } from "../../services/api";

vi.mock("../../services/api", () => ({
  startCheckout: vi.fn(),
  openBillingPortal: vi.fn(),
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
