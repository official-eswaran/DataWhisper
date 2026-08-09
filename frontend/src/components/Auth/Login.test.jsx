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

// ── The failure message is wrong on purpose — see the issue ──────────────────

test("every backend failure reason collapses into one message (known defect)", async () => {
  // NOT a specification — this pins current behaviour, tracked as issue #77.
  // It keeps the defect visible in the suite rather than buried in the
  // component, and makes fixing it a deliberate edit to this test rather than a
  // surprise red run. When #77 lands, replace this with per-status assertions.
  //
  // `backend/app/api/routes/auth.py` distinguishes four outcomes on purpose:
  //
  //   401 "Invalid username or password (3 attempt(s) remaining)"
  //   401 "Invalid credentials. Account locked for 15 minutes."
  //   403 "Account is disabled"
  //   429 "Account locked due to too many failed attempts. Try again later."
  //
  // The component's `catch` ignores the error entirely and shows the same
  // string for all of them. A locked-out user and an admin-disabled user both
  // read "Invalid credentials" and retry forever, and the attempts-remaining
  // warning — which exists precisely to prevent lockout — never arrives.
  //
  // This is the same defect that was found and fixed in Signup, whose failures
  // used to collapse into "Please check your details and try again".
  const outcomes = [
    httpError(401, { detail: "Invalid username or password (3 attempt(s) remaining)" }),
    httpError(401, { detail: "Invalid credentials. Account locked for 15 minutes." }),
    httpError(403, { detail: "Account is disabled" }),
    httpError(429, { detail: "Account locked due to too many failed attempts." }),
  ];

  const seen = new Set();
  for (const outcome of outcomes) {
    vi.clearAllMocks();
    login.mockRejectedValue(outcome);
    const { unmount } = render(
      <MemoryRouter>
        <Login onLogin={vi.fn()} />
      </MemoryRouter>
    );
    await fillAndSubmit();
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    seen.add(toast.error.mock.calls[0][0]);
    unmount();
  }

  // Four distinct causes, one message. When this is fixed, `seen.size` becomes
  // 4 and this test should be replaced by per-status assertions.
  expect(seen.size).toBe(1);
});
