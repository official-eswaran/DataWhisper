import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// `services/api.js` is the last part of the frontend with no suite of its own.
// It was described as "thin wrappers exercised through the components that call
// them", which is true of about half the file and not at all true of the rest:
// the token store, the single-flight refresh, the 401 retry-once interceptor and
// the SSE parser are the app's session machinery, and nothing asserted any of it.
//
// Two things are tested here that no component test could reach:
//   * concurrent 401s must share ONE refresh call, not one each
//   * an SSE event split across two network chunks must survive the boundary
//
// The module keeps state at module scope (the access token, the in-flight
// refresh promise), so every test re-imports it — see `loadApi`.

/** Axios calls a custom adapter and expects it to settle the promise itself:
 *  resolve for a passing status, reject with `.response`/`.config` set for a
 *  failing one. Without that, a 401 would resolve and the response interceptor
 *  under test would never run. */
function makeAdapter(handler) {
  return async (config) => {
    const raw = (await handler(config)) ?? {};
    const response = {
      status: 200,
      statusText: "OK",
      headers: {},
      data: null,
      config,
      ...raw,
    };
    const ok = config.validateStatus
      ? config.validateStatus(response.status)
      : response.status >= 200 && response.status < 300;
    if (ok) return response;
    const error = new Error(`Request failed with status code ${response.status}`);
    error.config = config;
    error.response = response;
    error.isAxiosError = true;
    throw error;
  };
}

/** A fresh copy of the module — empty token store, no in-flight refresh. */
async function loadApi(handler = () => ({ data: {} })) {
  vi.resetModules();
  const mod = await import("./api");
  const calls = [];
  // `auth` snapshots the header at call time. The 401 retry replays the *same*
  // config object with a new token on it, so reading calls[0].headers after the
  // fact shows the retry's value for both attempts.
  const auth = [];
  mod.default.defaults.adapter = makeAdapter((config) => {
    calls.push(config);
    auth.push(config.headers.Authorization);
    return handler(config);
  });
  return { ...mod, api: mod.default, calls, auth };
}

/** Stub window.location so redirectToLogin can be observed without navigating. */
function stubLocation(pathname = "/dashboard") {
  delete window.location;
  window.location = { pathname, href: "" };
  return window.location;
}

const originalLocation = window.location;

beforeEach(() => {
  global.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.location = originalLocation;
});

/** A /auth/refresh response. */
const refreshOk = (body = { access_token: "fresh-token", role: "owner" }) => ({
  ok: true,
  json: async () => body,
});
const refreshFailed = { ok: false, json: async () => ({}) };

// ── Token store (issue #22: nothing token-shaped in localStorage) ────────────

describe("token store", () => {
  test("keeps the access token and role in memory only", async () => {
    const { tokens } = await loadApi();
    tokens.set({ access_token: "abc", role: "admin" });

    expect(tokens.access).toBe("abc");
    expect(tokens.role).toBe("admin");
    // The whole point of #22: an XSS payload must not find it in storage.
    expect(window.localStorage.getItem("access_token")).toBeNull();
    expect(JSON.stringify(window.localStorage)).not.toContain("abc");
  });

  test("a payload missing a field leaves the existing value alone", async () => {
    // /auth/refresh returns a token and no role. Overwriting the role with
    // undefined would silently demote the user mid-session.
    const { tokens } = await loadApi();
    tokens.set({ access_token: "first", role: "owner" });
    tokens.set({ access_token: "second" });

    expect(tokens.access).toBe("second");
    expect(tokens.role).toBe("owner");
  });

  test("set() tolerates being handed nothing", async () => {
    const { tokens } = await loadApi();
    tokens.set({ access_token: "abc", role: "owner" });
    expect(() => tokens.set(undefined)).not.toThrow();
    expect(tokens.access).toBe("abc");
  });

  test("clear() empties both", async () => {
    const { tokens } = await loadApi();
    tokens.set({ access_token: "abc", role: "owner" });
    tokens.clear();

    expect(tokens.access).toBeNull();
    expect(tokens.role).toBeNull();
  });
});

// ── Request interceptor ──────────────────────────────────────────────────────

describe("request interceptor", () => {
  test("attaches the access token when there is one", async () => {
    const { tokens, getUsage, calls } = await loadApi();
    tokens.set({ access_token: "abc", role: "owner" });
    await getUsage();

    expect(calls[0].headers.Authorization).toBe("Bearer abc");
  });

  test("sends no Authorization header when there is no session", async () => {
    // "Bearer null" is worse than nothing: it turns an anonymous request into a
    // malformed-credential one, and /auth/signup-config is called with no session.
    const { getSignupConfig, calls } = await loadApi();
    await getSignupConfig();

    expect(calls[0].headers.Authorization).toBeUndefined();
  });
});

// ── 401 handling: refresh once, retry once, then give up ─────────────────────

describe("response interceptor", () => {
  test("a 401 refreshes and replays the request with the new token", async () => {
    global.fetch.mockResolvedValue(refreshOk());
    let first = true;
    const { tokens, getUsage, calls, auth } = await loadApi(() => {
      if (first) {
        first = false;
        return { status: 401, data: { detail: "expired" } };
      }
      return { status: 200, data: { plan: "pro" } };
    });
    tokens.set({ access_token: "stale", role: "owner" });

    const res = await getUsage();

    expect(res.data).toEqual({ plan: "pro" });
    expect(calls).toHaveLength(2);
    expect(auth).toEqual(["Bearer stale", "Bearer fresh-token"]);
  });

  test("it retries once and only once", async () => {
    // Without the _retried flag a token the server keeps rejecting would loop
    // forever, hammering /auth/refresh from every open tab.
    global.fetch.mockResolvedValue(refreshOk());
    const location = stubLocation();
    const { tokens, getUsage, calls } = await loadApi(() => ({ status: 401, data: {} }));
    tokens.set({ access_token: "stale", role: "owner" });

    await expect(getUsage()).rejects.toMatchObject({ response: { status: 401 } });

    expect(calls).toHaveLength(2);
    expect(location.href).toBe("/login");
    // And the freshly-minted token is dropped on the way out. The refresh
    // succeeded, so nothing else clears it — but the server has just rejected
    // it, and keeping it means the next page load opens by sending a
    // credential already known to be bad.
    expect(tokens.access).toBeNull();
  });

  test("a refresh that fails redirects to login and rejects", async () => {
    global.fetch.mockResolvedValue(refreshFailed);
    const location = stubLocation();
    const { tokens, getUsage, calls } = await loadApi(() => ({ status: 401, data: {} }));
    tokens.set({ access_token: "stale", role: "owner" });

    await expect(getUsage()).rejects.toMatchObject({ response: { status: 401 } });

    expect(calls).toHaveLength(1); // no replay without a token
    expect(location.href).toBe("/login");
    expect(tokens.access).toBeNull();
  });

  test("already on /login, it does not navigate again", async () => {
    // The guard that stops a redirect loop when the login page's own request 401s.
    global.fetch.mockResolvedValue(refreshFailed);
    const location = stubLocation("/login");
    const { getUsage } = await loadApi(() => ({ status: 401, data: {} }));

    await expect(getUsage()).rejects.toBeDefined();

    expect(location.href).toBe("");
  });

  test("errors that are not 401 pass straight through", async () => {
    const { getUsage, calls } = await loadApi(() => ({ status: 500, data: { detail: "boom" } }));

    await expect(getUsage()).rejects.toMatchObject({
      response: { status: 500, data: { detail: "boom" } },
    });

    expect(global.fetch).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
  });

  test("a 403 is not treated as an expired session", async () => {
    // Email-verification and closed-signup gates both answer 403. Refreshing
    // the token would achieve nothing and bounce the user off the page.
    const { getUsage } = await loadApi(() => ({ status: 403, data: {} }));

    await expect(getUsage()).rejects.toMatchObject({ response: { status: 403 } });
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

// ── Single-flight refresh ────────────────────────────────────────────────────

describe("refresh coordination", () => {
  test("concurrent 401s share one refresh call", async () => {
    // The dashboard fires several requests at once. One refresh per 401 would
    // rotate the refresh cookie repeatedly and log the user out — the exact
    // thing the shared promise exists to prevent.
    let resolveRefresh;
    global.fetch.mockReturnValue(
      new Promise((r) => {
        resolveRefresh = () => r(refreshOk());
      })
    );

    const seen = [];
    const { tokens, getUsage, getBillingStatus, getInvoices } = await loadApi((config) => {
      seen.push(config.url);
      return seen.filter((u) => u === config.url).length === 1
        ? { status: 401, data: {} }
        : { status: 200, data: {} };
    });
    tokens.set({ access_token: "stale", role: "owner" });

    const all = Promise.all([getUsage(), getBillingStatus(), getInvoices()]);
    await Promise.resolve();
    resolveRefresh();
    await all;

    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  test("a later 401 starts a new refresh", async () => {
    // The `finally` that clears refreshPromise. Without it the first refresh's
    // result would be reused forever and the session could never recover.
    global.fetch.mockResolvedValue(refreshOk());
    let failNext = true;
    const { tokens, getUsage } = await loadApi(() => {
      const status = failNext ? 401 : 200;
      failNext = !failNext;
      return { status, data: {} };
    });
    tokens.set({ access_token: "stale", role: "owner" });

    await getUsage();
    await getUsage();

    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  test("the refresh request sends the cookie and no body", async () => {
    global.fetch.mockResolvedValue(refreshOk());
    const { bootstrapSession } = await loadApi();
    await bootstrapSession();

    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe("/api/auth/refresh");
    expect(init.method).toBe("POST");
    // The refresh token is an httpOnly cookie; it is never in a body.
    expect(init.credentials).toBe("include");
    expect(init.body).toBeUndefined();
  });
});

// ── bootstrapSession: the boot gate in App.jsx ───────────────────────────────

describe("bootstrapSession", () => {
  test("recovers a session from the refresh cookie", async () => {
    global.fetch.mockResolvedValue(refreshOk({ access_token: "t", role: "manager" }));
    const { bootstrapSession, tokens } = await loadApi();

    await expect(bootstrapSession()).resolves.toEqual({ token: "t", role: "manager" });
    expect(tokens.access).toBe("t");
  });

  test("returns null when there is no valid cookie", async () => {
    // A first visit is not an error: no session yet is the normal case, and
    // bouncing the user to /login from here would break the signup page.
    global.fetch.mockResolvedValue(refreshFailed);
    const location = stubLocation();
    const { bootstrapSession, tokens } = await loadApi();

    await expect(bootstrapSession()).resolves.toBeNull();
    expect(tokens.access).toBeNull();
    expect(location.href).toBe("");
  });

  test("returns null when the network is down", async () => {
    global.fetch.mockRejectedValue(new Error("Network Error"));
    const { bootstrapSession } = await loadApi();

    await expect(bootstrapSession()).resolves.toBeNull();
  });

  test("a non-OK refresh cannot install a token from its own body", async () => {
    // The status check is what makes this safe, not the parse: an error page or
    // a proxy response that happens to carry an `access_token` field would
    // otherwise be adopted as a session.
    global.fetch.mockResolvedValue({
      ok: false,
      json: async () => ({ access_token: "from-an-error-page", role: "owner" }),
    });
    const { bootstrapSession, tokens } = await loadApi();

    await expect(bootstrapSession()).resolves.toBeNull();
    expect(tokens.access).toBeNull();
    expect(tokens.role).toBeNull();
  });
});

// ── Upload ───────────────────────────────────────────────────────────────────

describe("uploadFile", () => {
  const file = new File(["a,b\n1,2"], "data.csv", { type: "text/csv" });

  test("posts the file as multipart form data", async () => {
    const { uploadFile, calls } = await loadApi();
    await uploadFile(file);

    expect(calls[0].url).toBe("/upload/");
    expect(calls[0].method).toBe("post");
    expect(calls[0].data.get("file")).toBe(file);
  });

  test("reports progress as a whole percentage", async () => {
    const onProgress = vi.fn();
    const { uploadFile, calls } = await loadApi();
    await uploadFile(file, onProgress);

    calls[0].onUploadProgress({ loaded: 512, total: 2048 });
    expect(onProgress).toHaveBeenCalledWith(25);

    // Rounded, not truncated — a bare ratio would render "33.33333%".
    calls[0].onUploadProgress({ loaded: 1, total: 3 });
    expect(onProgress).toHaveBeenLastCalledWith(33);
  });

  test("a progress event with no total is ignored", async () => {
    // The browser omits `total` when the body length is unknown; dividing by it
    // would report Infinity as the percentage.
    const onProgress = vi.fn();
    const { uploadFile, calls } = await loadApi();
    await uploadFile(file, onProgress);

    calls[0].onUploadProgress({ loaded: 512 });
    expect(onProgress).not.toHaveBeenCalled();
  });

  test("progress events are harmless with no callback", async () => {
    const { uploadFile, calls } = await loadApi();
    await uploadFile(file);

    expect(() => calls[0].onUploadProgress({ loaded: 1, total: 2 })).not.toThrow();
  });
});

// ── The SSE stream ───────────────────────────────────────────────────────────

/** A fetch Response whose body yields the given chunks in order. */
function streamResponse(chunks, { status = 200 } = {}) {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: status >= 200 && status < 300,
    status,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
      }),
    },
    json: async () => ({}),
  };
}

const sse = (obj) => `data: ${JSON.stringify(obj)}\n`;

describe("askQuestionStream", () => {
  const handlers = () => ({
    onStage: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
    onToken: vi.fn(),
  });

  test("dispatches each event kind to its own callback", async () => {
    const h = handlers();
    global.fetch.mockResolvedValue(
      streamResponse([
        sse({ stage: "thinking", message: "Reading schema" }),
        sse({ stage: "token", token: "SELECT" }),
        sse({ stage: "done", result: { rows: [] } }),
      ])
    );
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "how many?", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onStage).toHaveBeenCalledWith("thinking", "Reading schema");
    expect(h.onToken).toHaveBeenCalledWith("SELECT");
    expect(h.onDone).toHaveBeenCalledWith({ rows: [] });
    expect(h.onError).not.toHaveBeenCalled();
  });

  test("an error event reaches onError, not onStage", async () => {
    const h = handlers();
    global.fetch.mockResolvedValue(
      streamResponse([sse({ stage: "error", message: "Query failed" })])
    );
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onError).toHaveBeenCalledWith("Query failed");
    expect(h.onStage).not.toHaveBeenCalled();
  });

  test("an event split across two chunks survives the boundary", async () => {
    // The reason the parser buffers at all. TCP does not respect line breaks,
    // and a `done` event lost to a chunk boundary hangs the UI on "thinking"
    // with the answer already delivered.
    const h = handlers();
    const event = sse({ stage: "done", result: { rows: [{ n: 1 }] } });
    global.fetch.mockResolvedValue(
      streamResponse([event.slice(0, 12), event.slice(12)])
    );
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onDone).toHaveBeenCalledWith({ rows: [{ n: 1 }] });
  });

  test("malformed and non-data lines are skipped without killing the stream", async () => {
    const h = handlers();
    global.fetch.mockResolvedValue(
      streamResponse([
        ": keep-alive comment\n",
        "data: {not json\n",
        "event: ping\n",
        sse({ stage: "done", result: { ok: true } }),
      ])
    );
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onDone).toHaveBeenCalledWith({ ok: true });
    expect(h.onError).not.toHaveBeenCalled();
  });

  test("a non-data line is not dispatched even when its tail parses as JSON", async () => {
    // This is what the `data: ` prefix check is for, and the try/catch alone
    // does not cover it. `event: {...}` is a legal SSE line; slicing six
    // characters off it yields valid JSON, so without the prefix check a
    // keep-alive or a comment could fire `done` and end the query early.
    const h = handlers();
    global.fetch.mockResolvedValue(
      streamResponse([
        'event: {"stage":"done","result":"spurious"}\n',
        sse({ stage: "done", result: "real" }),
      ])
    );
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onDone).toHaveBeenCalledTimes(1);
    expect(h.onDone).toHaveBeenCalledWith("real");
  });

  test("token events are optional", async () => {
    // ChatWindow does not always pass onToken; a bare call must not throw
    // halfway through a stream that is otherwise fine.
    const h = handlers();
    global.fetch.mockResolvedValue(
      streamResponse([sse({ stage: "token", token: "x" }), sse({ stage: "done", result: 1 })])
    );
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError);

    expect(h.onDone).toHaveBeenCalledWith(1);
  });

  test("sends the session and question with the current token", async () => {
    const h = handlers();
    global.fetch.mockResolvedValue(streamResponse([]));
    const { askQuestionStream, tokens } = await loadApi();
    tokens.set({ access_token: "abc", role: "owner" });

    await askQuestionStream("s1", "how many?", h.onStage, h.onDone, h.onError, h.onToken);

    const [url, init] = global.fetch.mock.calls[0];
    expect(url).toBe("/api/query/stream");
    expect(init.headers.Authorization).toBe("Bearer abc");
    expect(JSON.parse(init.body)).toEqual({ session_id: "s1", question: "how many?" });
  });

  test("a 401 refreshes and replays the stream", async () => {
    const h = handlers();
    global.fetch
      .mockResolvedValueOnce({ status: 401, ok: false, json: async () => ({}) })
      .mockResolvedValueOnce(refreshOk())
      .mockResolvedValueOnce(streamResponse([sse({ stage: "done", result: "ok" })]));
    const { askQuestionStream, tokens } = await loadApi();
    tokens.set({ access_token: "stale", role: "owner" });

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onDone).toHaveBeenCalledWith("ok");
    expect(global.fetch.mock.calls[2][1].headers.Authorization).toBe("Bearer fresh-token");
  });

  test("a 401 with no usable refresh gives up quietly", async () => {
    // No onError: the user is about to be redirected by the caller's own
    // bootstrap, and an error toast on the way out is noise.
    const h = handlers();
    global.fetch
      .mockResolvedValueOnce({ status: 401, ok: false, json: async () => ({}) })
      .mockResolvedValueOnce(refreshFailed);
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onError).not.toHaveBeenCalled();
    expect(h.onDone).not.toHaveBeenCalled();
  });

  test("a 401 that survives the refresh sends the user to login", async () => {
    const h = handlers();
    const location = stubLocation();
    global.fetch
      .mockResolvedValueOnce({ status: 401, ok: false, json: async () => ({}) })
      .mockResolvedValueOnce(refreshOk())
      .mockResolvedValueOnce({ status: 401, ok: false, json: async () => ({}) });
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(location.href).toBe("/login");
    expect(h.onError).not.toHaveBeenCalled();
  });

  test("an unreachable server says so in words the user can act on", async () => {
    const h = handlers();
    global.fetch.mockRejectedValue(new TypeError("Failed to fetch"));
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onError).toHaveBeenCalledWith("Cannot reach the server. Is the backend running?");
  });

  test("a non-OK response surfaces the API's own detail", async () => {
    const h = handlers();
    global.fetch.mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ detail: "Monthly query quota exhausted" }),
    });
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onError).toHaveBeenCalledWith("Monthly query quota exhausted");
  });

  test("a non-OK response with an unreadable body still says something", async () => {
    const h = handlers();
    global.fetch.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("not json");
      },
    });
    const { askQuestionStream } = await loadApi();

    await askQuestionStream("s1", "q", h.onStage, h.onDone, h.onError, h.onToken);

    expect(h.onError).toHaveBeenCalledWith("Request failed");
  });
});

// ── Endpoint wrappers ────────────────────────────────────────────────────────
// Thin, but the URL and payload shapes are a contract with the backend and
// nothing else asserts them. The defaults are the interesting part.

describe("endpoints", () => {
  test.each([
    ["login", (m) => m.login("u", "p"), "post", "/auth/login", { username: "u", password: "p" }],
    ["askQuestion", (m) => m.askQuestion("s1", "q?"), "post", "/query/",
      { session_id: "s1", question: "q?" }],
    ["getSignupConfig", (m) => m.getSignupConfig(), "get", "/auth/signup-config", undefined],
    ["register", (m) => m.register("Org", "u", "e@x.com", "pw", "cap"), "post", "/auth/register",
      { org_name: "Org", username: "u", email: "e@x.com", password: "pw", captcha_token: "cap" }],
    ["listUsers", (m) => m.listUsers(), "get", "/users/", undefined],
    ["setUserActive", (m) => m.setUserActive("bob", false), "patch", "/users/bob/status",
      { is_active: false }],
    ["getUsage", (m) => m.getUsage(), "get", "/usage/", undefined],
    ["changePlan", (m) => m.changePlan("pro"), "put", "/usage/plan", { plan: "pro" }],
    ["getBillingStatus", (m) => m.getBillingStatus(), "get", "/billing/", undefined],
    ["startCheckout", (m) => m.startCheckout("pro"), "post", "/billing/checkout", { plan: "pro" }],
    ["openBillingPortal", (m) => m.openBillingPortal(), "post", "/billing/portal", undefined],
    ["getInvoices", (m) => m.getInvoices(), "get", "/billing/invoices", undefined],
    ["exportMyData", (m) => m.exportMyData(), "get", "/me/export", undefined],
    ["deleteMyAccount", (m) => m.deleteMyAccount(), "delete", "/me", undefined],
    ["deleteMyOrganization", (m) => m.deleteMyOrganization(), "delete", "/org", undefined],
  ])("%s", async (_name, call, method, url, data) => {
    const mod = await loadApi();
    await call(mod);

    expect(mod.calls[0].method).toBe(method);
    expect(mod.calls[0].url).toBe(url);
    if (data === undefined) expect(mod.calls[0].data).toBeUndefined();
    else expect(JSON.parse(mod.calls[0].data)).toEqual(data);
  });

  test("createUser defaults new accounts to the least privileged role", async () => {
    // A default of "admin" would hand every invited user the console.
    const { createUser, calls } = await loadApi();
    await createUser("bob", "b@x.com", "pw");

    expect(JSON.parse(calls[0].data).role).toBe("member");
  });

  test("createUser honours an explicit role", async () => {
    const { createUser, calls } = await loadApi();
    await createUser("bob", "b@x.com", "pw", "admin");

    expect(JSON.parse(calls[0].data).role).toBe("admin");
  });

  test("getAuditLogs pages from the start by default", async () => {
    const { getAuditLogs, calls } = await loadApi();
    await getAuditLogs();

    expect(calls[0].url).toBe("/audit/logs?limit=50&offset=0");
  });

  test("getAuditLogs passes an explicit page through", async () => {
    const { getAuditLogs, calls } = await loadApi();
    await getAuditLogs(25, 100);

    expect(calls[0].url).toBe("/audit/logs?limit=25&offset=100");
  });

  test("exportPdf asks for a blob, not parsed JSON", async () => {
    // Without responseType the PDF arrives as a mangled string and the
    // downloaded file is corrupt.
    const { exportPdf, calls } = await loadApi();
    await exportPdf("s1");

    expect(calls[0].url).toBe("/export/pdf/s1");
    expect(calls[0].responseType).toBe("blob");
  });

  test("logout never rejects", async () => {
    // It is called on the way out of the app, often with an already-invalid
    // token. An unhandled rejection there would surface as a console error the
    // user can do nothing about.
    const { logout } = await loadApi(() => ({ status: 500, data: {} }));

    await expect(logout()).resolves.toBeUndefined();
  });
});
