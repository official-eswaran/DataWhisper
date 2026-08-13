import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, vi } from "vitest";
import App from "./App";

// App now boots by calling /auth/refresh to recover a session from the httpOnly
// cookie (#22). In tests there's no backend, so stub fetch to reject — that
// resolves boot to "no session" and the app renders the login/signup routes.
// Because boot is async, the App tests use findBy* to wait past the boot gate.
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("no backend"))));
});

// Vite/Vitest resolves react-router-dom v7 (ESM) natively, so — unlike under
// CRA's frozen Jest — we can now smoke-test the full App + router, not just the
// router-free ErrorBoundary.
//
// The two ErrorBoundary tests that lived here until 2026-08-13 have moved to
// `components/ErrorBoundary.test.jsx`, alongside the rest of that component's
// behaviour (#70). This file is about the router.

test("unauthenticated visit renders the login screen", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>
  );
  // After boot finds no session, it redirects to /login and shows the form.
  expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
});

test("the signup route renders the onboarding form", async () => {
  render(
    <MemoryRouter initialEntries={["/signup"]}>
      <App />
    </MemoryRouter>
  );
  expect(
    await screen.findByRole("button", { name: /create workspace/i })
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/organization name/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
});
