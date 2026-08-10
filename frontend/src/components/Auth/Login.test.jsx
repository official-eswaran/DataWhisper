import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
import Login from "./Login";
import { login } from "../../services/api";

// Login is the front door: every session in the product starts here, and it is
// the only component that turns a credential into an authenticated session.
// Coverage was 25% of functions — `handleSubmit`, which is the whole component,
// had no test at all.
//
// The tests below deliberately do *not* assert the text of the failure toast.
// See "the failure message is wrong on purpose" at the bottom: the component
// collapses four distinct backend outcomes into one message, and pinning that
// text here would quietly promote a defect to a specification.

vi.mock("../../services/api", () => ({ login: vi.fn() }));
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const toast = (await import("react-hot-toast")).default;

const renderLogin = (onLogin = vi.fn()) => {
  render(
    <MemoryRouter>
      <Login onLogin={onLogin} />
    </MemoryRouter>
  );
  return onLogin;
};

async function fillAndSubmit({ username = "acme_owner", password = "Str0ngPassw0rd" } = {}) {
  if (username) await userEvent.type(screen.getByLabelText(/username/i), username);
  if (password) await userEvent.type(screen.getByLabelText(/password/i), password);
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  });
  return { username, password };
}

/** An axios-shaped rejection, which is the shape the component receives. */
const httpError = (status, data) => ({ response: { status, data } });

beforeEach(() => {
  vi.clearAllMocks();
});

// ── Accessibility: the form has to be usable without sight of the placeholder ─

test("both fields have real labels, not just placeholders", () => {
  // The visible affordance is a placeholder, which disappears on focus and is
  // not reliably announced. Each input carries an sr-only <label htmlFor>, and
  // getByLabelText is what proves the association actually resolves.
  renderLogin();
  expect(screen.getByLabelText(/username/i)).toHaveAttribute("id", "username");
  expect(screen.getByLabelText(/password/i)).toHaveAttribute("id", "password");
});

test("the password field is masked and the form is named", () => {
  renderLogin();
  expect(screen.getByLabelText(/password/i)).toHaveAttribute("type", "password");
  expect(screen.getByRole("form", { name: /sign in/i })).toBeInTheDocument();
});

test("the fields advertise the autocomplete tokens password managers expect", () => {
  // "current-password" (not "new-password") is what tells a manager to offer a
  // saved credential rather than to generate one.
  renderLogin();
  expect(screen.getByLabelText(/username/i)).toHaveAttribute("autoComplete", "username");
  expect(screen.getByLabelText(/password/i)).toHaveAttribute(
    "autoComplete",
    "current-password"
  );
});

test("a new user is offered the way out to signup", () => {
  renderLogin();
  expect(screen.getByRole("link", { name: /create a workspace/i })).toHaveAttribute(
    "href",
    "/signup"
  );
});

// ── Success ──────────────────────────────────────────────────────────────────

test("a successful login hands the whole session payload to onLogin", async () => {
  // The session carries the role, which decides whether the admin console is
  // reachable. Forwarding only the token would silently demote every admin.
  const session = { access_token: "tok", role: "owner", expires_in: 3600 };
  login.mockResolvedValue({ data: session });

  const onLogin = renderLogin();
  const { username, password } = await fillAndSubmit();

  expect(login).toHaveBeenCalledWith(username, password);
  expect(onLogin).toHaveBeenCalledWith(session);
  expect(toast.success).toHaveBeenCalled();
});

test("what the user typed is what gets submitted", async () => {
  // Guards the controlled-input wiring: a value/onChange mismatch still renders
  // and still submits, just with the wrong or empty credential.
  login.mockResolvedValue({ data: {} });

  renderLogin();
  await fillAndSubmit({ username: "someone_else", password: "different-secret" });

  expect(login).toHaveBeenCalledWith("someone_else", "different-secret");
});

test("submitting is prevented from doing a native form post", async () => {
  // Without preventDefault the browser navigates on submit: the SPA reloads,
  // the in-memory access token (#22 keeps it out of localStorage) is wiped, and
  // the credentials land in the URL. jsdom does not implement form navigation,
  // so nothing else in this file would notice — the listener has to be on
  // `document`, below React's root handler in the bubble path, to observe the
  // flag after React's own handler has run.
  login.mockResolvedValue({ data: {} });
  renderLogin();

  let submitted = null;
  const capture = (e) => { submitted = e; };
  document.addEventListener("submit", capture);
  try {
    await fillAndSubmit();
  } finally {
    document.removeEventListener("submit", capture);
  }

  expect(submitted).not.toBeNull();
  expect(submitted.defaultPrevented).toBe(true);
});

// ── In-flight state ──────────────────────────────────────────────────────────

test("the button is disabled and busy while the request is in flight", async () => {
  // Without this, an impatient double-click sends two login attempts — and the
  // backend counts failed attempts toward a lockout.
  let resolve;
  login.mockReturnValue(new Promise((r) => { resolve = r; }));

  renderLogin();
  await fillAndSubmit();

  const button = screen.getByRole("button", { name: /signing in/i });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("aria-busy", "true");

  await act(async () => {
    resolve({ data: { access_token: "t" } });
  });
  await waitFor(() => expect(button).not.toBeDisabled());
});

test("the button is re-enabled after a failure so the user can retry", async () => {
  // The `finally` branch. If this regressed, one typo would leave the form
  // permanently dead and the only recovery would be a page reload.
  login.mockRejectedValue(httpError(401, {}));

  renderLogin();
  await fillAndSubmit();

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /sign in/i })).not.toBeDisabled()
  );
});

// ── Failure: the behaviour that is correct today ─────────────────────────────

test("a failed login does not start a session", async () => {
  // The single most important assertion in this file.
  login.mockRejectedValue(httpError(401, { detail: "Invalid username or password" }));

  const onLogin = renderLogin();
  await fillAndSubmit();

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onLogin).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});

test("a network failure with no response is still reported to the user", async () => {
  // `catch {}` with no binding swallows non-HTTP errors too. The user must not
  // be left staring at a form that appears to have done nothing.
  login.mockRejectedValue(new Error("Network Error"));

  const onLogin = renderLogin();
  await fillAndSubmit();

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onLogin).not.toHaveBeenCalled();
});

test("the typed password is never echoed into the failure toast", async () => {
  // #32 was exactly this bug on the backend: a 422 echoed the submitted
  // password back in the response body. The same mistake is available here to
  // anyone "improving" the error message by including the request.
  login.mockRejectedValue(httpError(401, { detail: "Invalid username or password" }));

  renderLogin();
  await fillAndSubmit({ password: "hunter2-in-the-clear" });

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  for (const [message] of toast.error.mock.calls) {
    expect(String(message)).not.toContain("hunter2-in-the-clear");
  }
});

// ── Each failure reason reaches the user distinctly (#77) ────────────────────
//
// These replace a characterization test that pinned the old behaviour: every
// failure below used to render as "Invalid credentials". Three of the four are
// not credential problems at all, and presenting them as one meant a
// locked-out user retried — which is exactly what the lockout defends against.

/** Submit against a rejection and return the message the user was shown. */
async function messageFor(rejection) {
  vi.clearAllMocks();
  login.mockRejectedValue(rejection);
  const { unmount } = render(
    <MemoryRouter>
      <Login onLogin={vi.fn()} />
    </MemoryRouter>
  );
  await fillAndSubmit();
  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  const [message] = toast.error.mock.calls[0];
  unmount();
  return message;
}

test("a 401 passes through the attempts-remaining warning", async () => {
  // The count exists only in the response, and the warning exists precisely so
  // the user can stop before lockout. A fixed string cannot carry it.
  const message = await messageFor(
    httpError(401, { detail: "Invalid username or password (3 attempt(s) remaining)" })
  );
  expect(message).toMatch(/3 attempt/i);
});

test("a 401 that reports a lockout says so, rather than 'wrong password'", async () => {
  const message = await messageFor(
    httpError(401, { detail: "Invalid credentials. Account locked for 15 minutes." })
  );
  expect(message).toMatch(/locked/i);
  expect(message).toMatch(/15 minutes/i);
});

test("a 401 with no detail still says something specific", async () => {
  const message = await messageFor(httpError(401, {}));
  expect(message).toMatch(/invalid username or password/i);
});

test("a blank detail falls back instead of showing an empty toast", async () => {
  // `detail: ""` already falls through on falsiness, but `"   "` is truthy and
  // would render a toast with nothing in it. The `.trim()` is what stops that.
  const message = await messageFor(httpError(401, { detail: "   " }));
  expect(message).toMatch(/invalid username or password/i);
});

test("a 403 tells the user the account is disabled and who to ask", async () => {
  // Retrying can never succeed, so the message has to point somewhere else.
  // The UI owns this wording: the API's "Account is disabled" is accurate but
  // gives the user nothing to do.
  const message = await messageFor(httpError(403, { detail: "Account is disabled" }));
  expect(message).toMatch(/disabled/i);
  expect(message).toMatch(/administrator/i);
});

test("a 429 distinguishes an account lockout from an IP rate limit", async () => {
  // Both arrive as 429 — the lockout from the login route, the other from
  // slowapi — and they call for different responses from the user. Only the
  // detail tells them apart, which is why it is passed through.
  const locked = await messageFor(
    httpError(429, { detail: "Account locked due to too many failed attempts. Try again later." })
  );
  const throttled = await messageFor(
    httpError(429, { detail: "Too many requests. Please slow down." })
  );

  expect(locked).toMatch(/account locked/i);
  expect(throttled).toMatch(/slow down/i);
  expect(locked).not.toBe(throttled);
});

test("a 429 with no detail still tells the user to wait", async () => {
  const message = await messageFor(httpError(429, {}));
  expect(message).toMatch(/wait|too many/i);
});

test("a network failure blames the connection, not the credentials", async () => {
  // No `response` at all. Telling this user their password is wrong sends them
  // to reset a credential that was never the problem.
  const message = await messageFor(new Error("Network Error"));
  expect(message).toMatch(/could not reach the server|connection/i);
  expect(message).not.toMatch(/password|credential/i);
});

test("an unrecognised status falls back without echoing the API body", async () => {
  // A 500's detail is not written for end users and may describe internals.
  const message = await messageFor(
    httpError(500, { detail: "psycopg2.OperationalError: FATAL: too many connections" })
  );
  expect(message).toMatch(/could not sign you in/i);
  expect(message).not.toMatch(/psycopg2|OperationalError/i);
});

test("the four backend outcomes now produce four different messages", async () => {
  // The direct inverse of the characterization test this replaces, which
  // asserted `seen.size === 1`.
  const seen = new Set([
    await messageFor(httpError(401, { detail: "Invalid username or password (3 attempt(s) remaining)" })),
    await messageFor(httpError(401, { detail: "Invalid credentials. Account locked for 15 minutes." })),
    await messageFor(httpError(403, { detail: "Account is disabled" })),
    await messageFor(httpError(429, { detail: "Account locked due to too many failed attempts." })),
  ]);
  expect(seen.size).toBe(4);
});
