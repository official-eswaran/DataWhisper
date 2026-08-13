import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import ErrorBoundary from "./ErrorBoundary";

// The last component in #70, and the only thing standing between a render-time
// exception and a blank white screen. It sat at 94.44% because two tests lived
// in `App.test.jsx` from before this file existed — they are here now, with the
// rest of the behaviour they never reached.
//
// The uncovered part was `handleReload`, which is the only way out of the
// fallback. A boundary that catches but cannot recover is half a boundary.

/** Throws on every render. */
function Boom() {
  throw new Error("boom");
}

// Flipped by the test rather than counted, because React re-renders a failed
// subtree in development to rebuild the stack — a render counter here would be
// measuring that, not the component.
let broken = true;

/** Throws until the test declares the underlying problem fixed. */
function Flaky() {
  if (broken) throw new Error("transient boom");
  return <div>recovered</div>;
}

let consoleError;
let originalLocation;

beforeEach(() => {
  broken = true;
  // React logs every caught error itself, on top of the component's own
  // `componentDidCatch` log. Spy rather than silence, so the assertions below
  // can still see what the component reported.
  consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  originalLocation = window.location;
});

afterEach(() => {
  consoleError.mockRestore();
  // Restore whatever the reload tests swapped out, or the next file in the run
  // inherits a fake location.
  window.location = originalLocation;
});

/** The component's own log line, separated from React's. */
const ownLogs = () =>
  consoleError.mock.calls.filter((args) => args[0] === "Unhandled UI error:");

function stubReload() {
  const reload = vi.fn();
  // jsdom refuses real navigation, so swap location for a plain object — the
  // same trick `BillingCard.test.jsx` uses for the Stripe redirect.
  delete window.location;
  window.location = { ...originalLocation, reload };
  return reload;
}

// ── The happy path ────────────────────────────────────────────────────────────

test("renders its children when nothing throws", () => {
  render(
    <ErrorBoundary>
      <div>hello world</div>
    </ErrorBoundary>,
  );
  expect(screen.getByText("hello world")).toBeInTheDocument();
  expect(screen.queryByText(/Something went wrong/i)).not.toBeInTheDocument();
});

test("a healthy tree logs nothing", () => {
  render(
    <ErrorBoundary>
      <div>hello world</div>
    </ErrorBoundary>,
  );
  expect(ownLogs()).toHaveLength(0);
});

// ── Catching ──────────────────────────────────────────────────────────────────

test("a child that throws gets the recovery screen instead of a white page", () => {
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  );
  expect(screen.getByRole("heading", { name: /Something went wrong/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Reload app/i })).toBeInTheDocument();
});

test("the crashed subtree is gone, not merely covered up", () => {
  render(
    <ErrorBoundary>
      <div>still here?</div>
      <Boom />
    </ErrorBoundary>,
  );
  expect(screen.queryByText("still here?")).not.toBeInTheDocument();
});

test("it catches from anywhere in the tree, not just a direct child", () => {
  render(
    <ErrorBoundary>
      <div>
        <section>
          <Boom />
        </section>
      </div>
    </ErrorBoundary>,
  );
  expect(screen.getByRole("heading", { name: /Something went wrong/i })).toBeInTheDocument();
});

test("the copy tells the user their data survived and what to do", () => {
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  );
  // Both halves matter: a bare "something went wrong" leaves a user who has
  // just uploaded a file assuming they lost it.
  expect(screen.getByText(/Your data is safe/i)).toBeInTheDocument();
  expect(screen.getByText(/reloading\s+usually fixes it/i)).toBeInTheDocument();
});

// ── Reporting ─────────────────────────────────────────────────────────────────

test("the error is reported to the console with its stack info", () => {
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  );
  // Without this the fallback is the only trace a crash leaves, and the screen
  // deliberately says nothing technical.
  expect(ownLogs()).toHaveLength(1);
  const [, error, info] = ownLogs()[0];
  expect(error).toBeInstanceOf(Error);
  expect(error.message).toBe("boom");
  expect(info).toBeTruthy();
});

// ── Recovering ────────────────────────────────────────────────────────────────

test("Reload app reloads the page", () => {
  const reload = stubReload();
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  );

  screen.getByRole("button", { name: /Reload app/i }).click();
  expect(reload).toHaveBeenCalledTimes(1);
});

test("the boundary re-arms before reloading, so a blocked reload is not a dead end", async () => {
  // `reload()` normally ends the story, and everything after it is invisible.
  // It is stubbed here, which is exactly the case that matters: if the reload
  // is slow, blocked, or the user is offline, resetting `hasError` is the
  // difference between the app coming back and the error screen being terminal.
  stubReload();
  render(
    <ErrorBoundary>
      <Flaky />
    </ErrorBoundary>,
  );
  expect(screen.getByRole("heading", { name: /Something went wrong/i })).toBeInTheDocument();

  broken = false; // whatever caused the crash has cleared
  await userEvent.click(screen.getByRole("button", { name: /Reload app/i }));

  expect(screen.getByText("recovered")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /Something went wrong/i })).not.toBeInTheDocument();
});

test("a child that keeps throwing goes straight back to the recovery screen", async () => {
  stubReload();
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  );

  await userEvent.click(screen.getByRole("button", { name: /Reload app/i }));

  // Re-arming does not mean pretending the error is gone.
  expect(screen.getByRole("heading", { name: /Something went wrong/i })).toBeInTheDocument();
});
