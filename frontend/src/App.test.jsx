import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { bootstrapSession, login, logout, tokens } from "./services/api";

// Vite/Vitest resolves react-router-dom v7 (ESM) natively, so — unlike under
// CRA's frozen Jest — we can smoke-test the full App + router, not just the
// router-free ErrorBoundary.
//
// The two ErrorBoundary tests that lived here until 2026-08-13 have moved to
// `components/ErrorBoundary.test.jsx`, alongside the rest of that component's
// behaviour (#70).
//
// What this file owns is the part no component test can reach: **the session
// lifecycle**. App is the only place that decides whether a visitor is logged
// in, and #22 made that decision asynchronous — the access token lives in
// memory, so a reload starts with none and has to re-mint one from the httpOnly
// refresh cookie before any route renders. The boot gate that covers that
// window, and the login/logout transitions on either side of it, were the
// uncovered part of this file.
//
// `services/api` is mocked because its own behaviour is pinned in
// `services/api.test.js`; here it stands in as "does a session exist?".
// `Dashboard` is mocked because it is a composition root of its own with a
// dozen API calls — what App needs from it is that it receives the right auth
// and can call back to log out. Login and Signup stay real: they are cheap, and
// the login transition is worth testing through the form a user actually uses.

vi.mock("./services/api", () => ({
  tokens: { set: vi.fn(), clear: vi.fn(), access: null, role: null },
  bootstrapSession: vi.fn(),
  logout: vi.fn(() => Promise.resolve()),
  login: vi.fn(),
  register: vi.fn(),
  getSignupConfig: vi.fn(() =>
    Promise.resolve({ data: { signups_open: true, captcha: null } })
  ),
}));

vi.mock("./components/Dashboard/Dashboard", () => ({
  default: ({ auth, onLogout }) => (
    <div>
      <h1>Dashboard</h1>
      <span data-testid="auth-token">{auth.token}</span>
      <span data-testid="auth-role">{auth.role}</span>
      <button type="button" onClick={onLogout}>
        Log out
      </button>
    </div>
  ),
}));

vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const renderApp = (path = "/") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  );

beforeEach(() => {
  vi.clearAllMocks();
  // No session is the default: boot resolves to null and the app lands on the
  // login screen.
  bootstrapSession.mockResolvedValue(null);
});

// ── Routing when there is no session ─────────────────────────────────────────

test("unauthenticated visit renders the login screen", async () => {
  renderApp("/");

  // After boot finds no session, it redirects to /login and shows the form.
  expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
});

test("the signup route renders the onboarding form", async () => {
  renderApp("/signup");

  expect(
    await screen.findByRole("button", { name: /create workspace/i })
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/organization name/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
});

// ── The boot gate (issue #22) ────────────────────────────────────────────────

test("neither screen renders until boot has answered", async () => {
  // The reason the gate exists: the token is in memory only, so on every reload
  // there is a window where the app does not yet know whether the user is
  // logged in. Rendering routes during it flashes the login screen at someone
  // who is signed in — and, worse, renders the dashboard's requests without a
  // token if the default went the other way.
  let finishBoot;
  bootstrapSession.mockReturnValue(new Promise((r) => { finishBoot = r; }));

  renderApp("/");

  expect(screen.getByRole("status")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
  expect(screen.queryByText("Dashboard")).toBeNull();

  await act(async () => finishBoot(null));

  expect(screen.queryByRole("status")).toBeNull();
  expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
});

test("the boot gate announces itself to a screen reader", async () => {
  // A silent blank page is indistinguishable from a broken one. `role=status`
  // with `aria-live=polite` says "working on it" without interrupting.
  bootstrapSession.mockReturnValue(new Promise(() => {}));

  renderApp("/");

  const gate = screen.getByRole("status");
  expect(gate).toHaveAttribute("aria-live", "polite");
  expect(gate).toHaveTextContent(/loading/i);
});

test("a recovered session goes straight to the dashboard", async () => {
  // The whole point of the gate: a signed-in user reloading the page must not
  // see the login screen at any point.
  bootstrapSession.mockResolvedValue({ token: "recovered", role: "owner" });

  renderApp("/");

  expect(await screen.findByText("Dashboard")).toBeInTheDocument();
  expect(screen.getByTestId("auth-token")).toHaveTextContent("recovered");
  expect(screen.getByTestId("auth-role")).toHaveTextContent("owner");
  expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
});

test("boot runs once, not once per route", async () => {
  bootstrapSession.mockResolvedValue({ token: "t", role: "owner" });

  renderApp("/");
  await screen.findByText("Dashboard");

  expect(bootstrapSession).toHaveBeenCalledTimes(1);
});

// **There is deliberately no test for the effect's `active` flag.** One was
// written, asserting that unmounting mid-boot produced no `console.error`, and
// it passed with the flag deleted — React 18 removed the "setState on an
// unmounted component" warning, and a state update on a dead component is now a
// silent no-op. So the guard has no observable behaviour to assert, and the
// test asserted nothing. It is left in `App.jsx` as the standard idiom, which
// becomes load-bearing again the moment that effect gains a dependency; see the
// comment there. Recorded here so the next person does not write it again.

// ── Logging in ───────────────────────────────────────────────────────────────

test("a successful login hands the raw payload to the token store", async () => {
  // `tokens.set` must receive what the API returned, not App's reshaped `auth`
  // — the store reads `access_token` and `role`, and a reshaped object would
  // leave every subsequent request unauthenticated.
  const payload = { access_token: "abc", role: "manager", expires_in: 3600 };
  login.mockResolvedValue({ data: payload });

  renderApp("/login");
  await screen.findByRole("button", { name: /sign in/i });

  await userEvent.type(screen.getByLabelText(/username/i), "ceo");
  await userEvent.type(screen.getByLabelText(/password/i), "Str0ngPass1");
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  });

  expect(tokens.set).toHaveBeenCalledWith(payload);
});

test("a successful login moves the user to the dashboard", async () => {
  login.mockResolvedValue({
    data: { access_token: "abc", role: "manager", expires_in: 3600 },
  });

  renderApp("/login");
  await screen.findByRole("button", { name: /sign in/i });

  await userEvent.type(screen.getByLabelText(/username/i), "ceo");
  await userEvent.type(screen.getByLabelText(/password/i), "Str0ngPass1");
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  });

  expect(await screen.findByText("Dashboard")).toBeInTheDocument();
  // Both fields, not just the role: `auth.token` is read from `access_token`,
  // and a wrong field name here hands the dashboard an undefined token while
  // everything still looks logged in.
  expect(screen.getByTestId("auth-token")).toHaveTextContent("abc");
  expect(screen.getByTestId("auth-role")).toHaveTextContent("manager");
});

test("a failed login leaves the user on the login screen", async () => {
  login.mockRejectedValue({ response: { status: 401, data: {} } });

  renderApp("/login");
  await screen.findByRole("button", { name: /sign in/i });

  await userEvent.type(screen.getByLabelText(/username/i), "ceo");
  await userEvent.type(screen.getByLabelText(/password/i), "wrong");
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
  });

  expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.queryByText("Dashboard")).toBeNull();
  expect(tokens.set).not.toHaveBeenCalled();
});

// ── Logging out ──────────────────────────────────────────────────────────────

test("logging out revokes the session server-side before clearing it locally", async () => {
  // Order matters: `apiLogout` needs the cookie to revoke the refresh token.
  // Clearing first would leave a valid refresh token alive on the server with
  // no way left to revoke it.
  bootstrapSession.mockResolvedValue({ token: "t", role: "owner" });
  const order = [];
  logout.mockImplementation(async () => order.push("revoke"));
  tokens.clear.mockImplementation(() => order.push("clear"));

  renderApp("/");
  await screen.findByText("Dashboard");

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /log out/i }));
  });

  expect(order).toEqual(["revoke", "clear"]);
});

test("logging out returns the user to the login screen", async () => {
  bootstrapSession.mockResolvedValue({ token: "t", role: "owner" });

  renderApp("/");
  await screen.findByText("Dashboard");

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /log out/i }));
  });

  expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.queryByText("Dashboard")).toBeNull();
});

// ── Routes that should not be reachable with a session ───────────────────────

test("an authenticated user is redirected away from /login", async () => {
  // Otherwise the browser's back button lands a signed-in user on a sign-in
  // form, which reads as having been logged out.
  bootstrapSession.mockResolvedValue({ token: "t", role: "owner" });

  renderApp("/login");

  expect(await screen.findByText("Dashboard")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /sign in/i })).toBeNull();
});

test("an authenticated user is redirected away from /signup", async () => {
  bootstrapSession.mockResolvedValue({ token: "t", role: "owner" });

  renderApp("/signup");

  expect(await screen.findByText("Dashboard")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /create workspace/i })).toBeNull();
});

test("an unknown path with a session still renders the dashboard", async () => {
  // The catch-all route is what lets Dashboard own its own sub-routing.
  bootstrapSession.mockResolvedValue({ token: "t", role: "owner" });

  renderApp("/audit");

  expect(await screen.findByText("Dashboard")).toBeInTheDocument();
});

test("an unknown path without a session goes to login, not a blank page", async () => {
  renderApp("/audit");

  expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
});

// ── Failure ──────────────────────────────────────────────────────────────────

test("a boot that rejects still lets the user reach the login screen", async () => {
  // `bootstrapSession` swallows its own failures today, so this pins the
  // property rather than the implementation: whatever happens during boot, the
  // gate must lift. A boot that can hang is an app that never renders.
  bootstrapSession.mockRejectedValue(new Error("network down"));

  renderApp("/");

  expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.queryByRole("status")).toBeNull();
});
