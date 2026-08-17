import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import CaptchaWidget, { PROVIDERS } from "./CaptchaWidget";

// The captcha widget wraps a third-party script (issue #21). What is worth
// pinning is everything around that script: which URL is loaded, that an
// unknown provider loads nothing at all, that a token reaches the parent, that
// expiry clears it, and that a load failure says so instead of leaving a dead
// submit button under an empty box.

/** Stand in for hcaptcha/turnstile: same explicit-render API, in-process. */
function fakeProvider() {
  const api = {
    render: vi.fn(() => "widget-1"),
    reset: vi.fn(),
    opts: null,
  };
  api.render.mockImplementation((el, opts) => {
    api.opts = opts;
    return "widget-1";
  });
  return api;
}

/** Resolve or reject the next injected <script>. */
function captureScripts({ succeed = true } = {}) {
  const injected = [];
  const original = document.head.appendChild.bind(document.head);
  vi.spyOn(document.head, "appendChild").mockImplementation((el) => {
    if (el.tagName === "SCRIPT") {
      injected.push(el);
      // The browser fires these asynchronously; a microtask is close enough and
      // keeps the tests free of timers.
      Promise.resolve().then(() => (succeed ? el.onload?.() : el.onerror?.()));
      return el;
    }
    return original(el);
  });
  return injected;
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.hcaptcha;
  delete window.turnstile;
});

const renderWidget = async (props = {}) => {
  const onToken = props.onToken ?? vi.fn();
  const result = render(
    <CaptchaWidget provider="hcaptcha" siteKey="site-key" onToken={onToken} {...props} />
  );
  await act(async () => {});
  return { ...result, onToken };
};

// ── Nothing is loaded unless there is something to load ──────────────────────

test("an unknown provider renders nothing and loads no script", async () => {
  // The API names the provider, so this is the guard against being told to
  // fetch script from somewhere the SPA does not already trust.
  const injected = captureScripts();
  const { container } = await renderWidget({ provider: "recaptcha" });

  expect(container).toBeEmptyDOMElement();
  expect(injected).toHaveLength(0);
});

test("a missing site key renders nothing", async () => {
  const injected = captureScripts();
  const { container } = await renderWidget({ siteKey: "" });

  expect(container).toBeEmptyDOMElement();
  expect(injected).toHaveLength(0);
});

// ── Loading and rendering ────────────────────────────────────────────────────

test("the provider's own script URL is what gets loaded", async () => {
  const injected = captureScripts();
  window.hcaptcha = fakeProvider();
  await renderWidget();

  expect(injected).toHaveLength(1);
  expect(injected[0].src).toBe(PROVIDERS.hcaptcha.src);
  expect(injected[0].async).toBe(true);
});

test("turnstile loads its own script, not hCaptcha's", async () => {
  const injected = captureScripts();
  window.turnstile = fakeProvider();
  await renderWidget({ provider: "turnstile" });

  expect(injected[0].src).toBe(PROVIDERS.turnstile.src);
  expect(injected[0].src).not.toContain("hcaptcha");
});

test("the widget is rendered with the site key the server supplied", async () => {
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  await renderWidget();

  await waitFor(() => expect(api.render).toHaveBeenCalled());
  expect(api.opts.sitekey).toBe("site-key");
  expect(screen.getByTestId("captcha-widget")).toBeInTheDocument();
});

test("a solved challenge hands its token to the parent", async () => {
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  const { onToken } = await renderWidget();

  await waitFor(() => expect(api.render).toHaveBeenCalled());
  act(() => api.opts.callback("solved-token"));

  expect(onToken).toHaveBeenCalledWith("solved-token");
});

test("expiry clears the token instead of leaving a stale one", async () => {
  // Submitting an expired token fails at the server, and the user is shown a
  // captcha error for a challenge they did solve.
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  const { onToken } = await renderWidget();

  await waitFor(() => expect(api.render).toHaveBeenCalled());
  act(() => api.opts.callback("solved-token"));
  act(() => api.opts["expired-callback"]());

  expect(onToken).toHaveBeenLastCalledWith("");
});

test("a widget error clears the token too", async () => {
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  const { onToken } = await renderWidget();

  await waitFor(() => expect(api.render).toHaveBeenCalled());
  act(() => api.opts.callback("solved-token"));
  act(() => api.opts["error-callback"]());

  expect(onToken).toHaveBeenLastCalledWith("");
});

// ── Reset: the single-use-token problem ──────────────────────────────────────

test("a bumped reset signal asks for a fresh challenge and clears the token", async () => {
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  const onToken = vi.fn();
  const { rerender } = await renderWidget({ onToken });

  await waitFor(() => expect(api.render).toHaveBeenCalled());
  act(() => api.opts.callback("solved-token"));
  onToken.mockClear();

  await act(async () => {
    rerender(
      <CaptchaWidget provider="hcaptcha" siteKey="site-key" onToken={onToken} resetSignal={1} />
    );
  });

  expect(api.reset).toHaveBeenCalledWith("widget-1");
  expect(onToken).toHaveBeenCalledWith("");
});

test("the initial render does not reset a widget that was just drawn", async () => {
  // resetSignal starts at 0; treating that as a reset would throw away the
  // first challenge the moment it appeared.
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  await renderWidget();

  await waitFor(() => expect(api.render).toHaveBeenCalled());
  expect(api.reset).not.toHaveBeenCalled();
});

test("a new onToken identity does not re-render the widget", async () => {
  // The parent re-renders on every keystroke. Re-rendering the widget each time
  // would discard a challenge the user has already solved.
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;
  const { rerender } = await renderWidget();

  await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));
  await act(async () => {
    rerender(
      <CaptchaWidget provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />
    );
  });

  expect(api.render).toHaveBeenCalledTimes(1);
});

// ── Races and half-loaded scripts ────────────────────────────────────────────

test("unmounting before the script resolves renders no widget", async () => {
  // The user navigates away while the provider is still loading. Rendering
  // into a detached node throws inside the provider's own code.
  const Fresh = await freshWidget();
  const scripts = [];
  vi.spyOn(document.head, "appendChild").mockImplementation((el) => {
    scripts.push(el);
    return el;
  });
  const api = fakeProvider();
  window.hcaptcha = api;

  const { unmount } = render(
    <Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />
  );
  unmount();
  await act(async () => {
    scripts[0].onload();
  });

  expect(api.render).not.toHaveBeenCalled();
});

test("a script that loads without exposing its global renders nothing and does not throw", async () => {
  // Observed with content blockers that answer the request with an empty body:
  // onload fires, window.hcaptcha never appears.
  const Fresh = await freshWidget();
  captureScripts();

  await act(async () => {
    render(<Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />);
  });

  expect(screen.getByTestId("captcha-widget")).toBeEmptyDOMElement();
});

test("a reset before the widget exists is a no-op", async () => {
  // The signup can fail (a 409) while the challenge is still loading, which
  // bumps resetSignal with no widget id to reset.
  const Fresh = await freshWidget();
  captureScripts({ succeed: false });
  const onToken = vi.fn();

  const { rerender } = render(
    <Fresh provider="hcaptcha" siteKey="site-key" onToken={onToken} />
  );
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

  await act(async () => {
    rerender(
      <Fresh provider="hcaptcha" siteKey="site-key" onToken={onToken} resetSignal={1} />
    );
  });

  expect(onToken).not.toHaveBeenCalled();
});

// ── The script is loaded once per page, not once per mount ───────────────────

/** A module with an empty script cache — the state of a fresh page load.
 *
 * The cache lives at module scope on purpose, so any test that wants to observe
 * a *first* load has to re-import rather than reuse the resolved promise the
 * tests above left behind. */
async function freshWidget() {
  vi.resetModules();
  return (await import("./CaptchaWidget")).default;
}

test("a second mount reuses the script already in the document", async () => {
  // Two mounts — a route change and back — must not inject a second copy of
  // the provider's script.
  const Fresh = await freshWidget();
  const injected = captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;

  const { unmount } = render(
    <Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />
  );
  await waitFor(() => expect(injected).toHaveLength(1));
  await waitFor(() => expect(api.render).toHaveBeenCalledTimes(1));
  unmount();

  await act(async () => {
    render(<Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />);
  });
  expect(injected).toHaveLength(1);
  // ...but it does draw a fresh widget: the old one went with the unmounted
  // node, and a remount with no challenge is a form that cannot be submitted.
  expect(api.render).toHaveBeenCalledTimes(2);
});

test("StrictMode's double mount still draws exactly one widget", async () => {
  // index.jsx wraps the app in StrictMode, so every effect here runs twice in
  // development. Two widgets in one container is the failure that guards
  // against.
  const Fresh = await freshWidget();
  captureScripts();
  const api = fakeProvider();
  window.hcaptcha = api;

  await act(async () => {
    render(
      <React.StrictMode>
        <Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />
      </React.StrictMode>
    );
  });

  expect(api.render).toHaveBeenCalledTimes(1);
});

// ── Failure ──────────────────────────────────────────────────────────────────

test("a script that will not load says so rather than showing an empty box", async () => {
  // Ad blockers block these scripts routinely. Without this the user sees a
  // blank space and a permanently disabled button, with nothing to act on.
  const Fresh = await freshWidget();
  captureScripts({ succeed: false });

  const { container } = render(
    <Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />
  );

  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/i);
  expect(container.querySelector("[data-testid='captcha-widget']")).toBeNull();
});

test("a failed load is retried on the next mount rather than cached forever", async () => {
  // A cached rejection would make one blocked request permanent for the tab,
  // so the failed promise is dropped and a remount gets a real attempt.
  const Fresh = await freshWidget();
  const failing = captureScripts({ succeed: false });

  const { unmount } = render(
    <Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />
  );
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  unmount();
  expect(failing).toHaveLength(1);

  vi.restoreAllMocks();
  const retried = captureScripts();
  window.hcaptcha = fakeProvider();
  await act(async () => {
    render(<Fresh provider="hcaptcha" siteKey="site-key" onToken={vi.fn()} />);
  });

  await waitFor(() => expect(retried).toHaveLength(1));
});
