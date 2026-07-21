import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";

// Vite/Vitest resolves react-router-dom v7 (ESM) natively, so — unlike under
// CRA's frozen Jest — we can now smoke-test the full App + router, not just the
// router-free ErrorBoundary.

function Boom() {
  throw new Error("boom");
}

test("renders children normally", () => {
  render(
    <ErrorBoundary>
      <div>hello world</div>
    </ErrorBoundary>
  );
  expect(screen.getByText("hello world")).toBeInTheDocument();
});

test("shows a recovery UI when a child throws", () => {
  // Silence the expected React error log for this test.
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>
  );
  expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Reload app/i })).toBeInTheDocument();
  spy.mockRestore();
});

test("unauthenticated visit renders the login screen", () => {
  localStorage.clear();
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>
  );
  // Redirects to /login and shows the sign-in form.
  expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
});
