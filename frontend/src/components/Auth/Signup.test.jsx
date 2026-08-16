import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
import Signup from "./Signup";
import { getSignupConfig, register } from "../../services/api";

// Signup is the first thing a new user touches and the only place an
// organization gets created. Its failure handling was the part with no cover:
// every non-2xx used to collapse into "Please check your details and try
// again", which never said *what* was wrong — most often the password rule.

vi.mock("../../services/api", () => ({ register: vi.fn(), getSignupConfig: vi.fn() }));
// The widget is a wrapper around a third-party script; its own behaviour is
// covered in CaptchaWidget.test.jsx. Here it stands in as a button that hands
// back a token, which is all Signup knows about it.
vi.mock("./CaptchaWidget", () => ({
  default: ({ onToken, resetSignal }) => (
    <button type="button" data-reset={resetSignal} onClick={() => onToken("solved-token")}>
      solve captcha
    </button>
  ),
}));
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const toast = (await import("react-hot-toast")).default;

/** Render and flush the signup-config fetch the form issues on mount.
 *
 * Awaiting it here rather than per-test keeps assertions running against the
 * form the user actually sees, and keeps the suite free of act() warnings that
 * would otherwise appear in every test that renders. */
const renderSignup = async (onLogin = vi.fn()) => {
  render(
    <MemoryRouter>
      <Signup onLogin={onLogin} />
    </MemoryRouter>
  );
  await act(async () => {});
  return onLogin;
};

/** Same, for a deployment whose captcha is configured. */
const renderConfigured = async (onLogin = vi.fn()) => {
  await renderSignup(onLogin);
  await waitFor(() => screen.getByRole("button", { name: /solve captcha/i }));
  return onLogin;
};

async function fillAndSubmit(overrides = {}) {
  const values = {
    org: "Acme",
    username: "acme_owner",
    email: "owner@example.com",
    password: "Str0ngPassw0rd",
    ...overrides,
  };
  await userEvent.type(screen.getByPlaceholderText(/organization name/i), values.org);
  await userEvent.type(screen.getByPlaceholderText(/^username$/i), values.username);
  await userEvent.type(screen.getByPlaceholderText(/^email$/i), values.email);
  await userEvent.type(screen.getByPlaceholderText(/^password/i), values.password);
  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /create workspace/i }));
  });
  return values;
}

/** An axios-shaped rejection, which is what the component actually inspects. */
const httpError = (status, data) => ({ response: { status, data } });

beforeEach(() => {
  vi.clearAllMocks();
  // The default deployment has no captcha configured, which is what every test
  // below assumes unless it says otherwise.
  getSignupConfig.mockResolvedValue({ data: { signups_open: true, captcha: null } });
});

// ── The password rule the form advertises ────────────────────────────────────

test("the password hint matches what the backend enforces", async () => {
  await renderSignup();
  const field = screen.getByPlaceholderText(/^password/i);
  // validate_password_strength requires 10+ chars with a letter and a digit.
  // The form used to say "min 8 chars", so a valid-looking password 422'd.
  expect(field.getAttribute("placeholder")).toMatch(/10\+/);
  expect(field).toHaveAttribute("minLength", "10");
});

test("the password field describes its rule to screen readers", async () => {
  await renderSignup();
  const field = screen.getByPlaceholderText(/^password/i);
  const describedBy = field.getAttribute("aria-describedby");
  expect(describedBy).toBeTruthy();
  expect(document.getElementById(describedBy)).toHaveTextContent(
    /at least 10 characters.*letter.*number/i
  );
});

// ── Success ──────────────────────────────────────────────────────────────────

test("a successful signup logs the user straight in", async () => {
  const session = { access_token: "tok", role: "owner", expires_in: 3600 };
  register.mockResolvedValue({ data: session });

  const onLogin = await renderSignup();
  const values = await fillAndSubmit();

  expect(register).toHaveBeenCalledWith(
    values.org, values.username, values.email, values.password, ""
  );
  // Registration returns a session, so there is no second login step.
  expect(onLogin).toHaveBeenCalledWith(session);
  expect(toast.success).toHaveBeenCalled();
});

test("the submit button is disabled while the request is in flight", async () => {
  let resolve;
  register.mockReturnValue(new Promise((r) => { resolve = r; }));

  await renderSignup();
  await userEvent.type(screen.getByPlaceholderText(/organization name/i), "Acme");
  await userEvent.type(screen.getByPlaceholderText(/^username$/i), "owner");
  await userEvent.type(screen.getByPlaceholderText(/^email$/i), "o@example.com");
  await userEvent.type(screen.getByPlaceholderText(/^password/i), "Str0ngPassw0rd");

  const button = screen.getByRole("button", { name: /create workspace/i });
  await act(async () => {
    await userEvent.click(button);
  });
  expect(button).toBeDisabled();

  await act(async () => {
    resolve({ data: { access_token: "t", role: "owner" } });
  });
  await waitFor(() => expect(button).not.toBeDisabled());
});

// ── Failures: each one tells the user something they can act on ──────────────

test("a 422 surfaces the API's own explanation", async () => {
  // This is the case the generic message used to swallow. The backend says
  // exactly which field failed and why; showing that is the whole point.
  register.mockRejectedValue(
    httpError(422, {
      detail: "Invalid request.",
      errors: [{ loc: ["body", "password"], msg: "Password must contain a digit", type: "value_error" }],
    })
  );

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Password must contain a digit")
  );
});

test("a 422 with no usable detail still says something", async () => {
  register.mockRejectedValue(httpError(422, { detail: "Invalid request." }));

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Please check your details and try again")
  );
});

test("a 422 with an empty errors array falls back rather than showing undefined", async () => {
  register.mockRejectedValue(httpError(422, { detail: "Invalid request.", errors: [] }));

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Please check your details and try again")
  );
});

test("a 409 names the actual conflict", async () => {
  register.mockRejectedValue(httpError(409, { detail: "Username or email already exists" }));

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("That username or email is already taken")
  );
});

test("a 403 explains that signup is closed, not that the details are wrong", async () => {
  // SIGNUPS_OPEN=false is a deployment choice; telling the user to check their
  // details would send them round a loop that cannot succeed.
  register.mockRejectedValue(httpError(403, { detail: "Public signup is closed" }));

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Public signup is closed on this deployment")
  );
});

test("a 429 tells the user to wait rather than to fix their input", async () => {
  register.mockRejectedValue(httpError(429, { detail: "Too many requests" }));

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      "Too many signups from this network. Please try again later."
    )
  );
});

test("a network failure with no response is still reported", async () => {
  register.mockRejectedValue(new Error("Network Error"));

  await renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Could not create your account")
  );
});

test("a failed signup does not log the user in", async () => {
  register.mockRejectedValue(httpError(409, {}));

  const onLogin = await renderSignup();
  await fillAndSubmit();

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onLogin).not.toHaveBeenCalled();
});

// ── Captcha (issue #21) ──────────────────────────────────────────────────────
// The server is what enforces this; the form's job is to obtain a token and to
// not let the user discover the requirement by being rejected.

const withCaptcha = () =>
  getSignupConfig.mockResolvedValue({
    data: { signups_open: true, captcha: { provider: "hcaptcha", site_key: "site-key" } },
  });

test("no captcha is rendered when the server has none configured", async () => {
  await renderSignup();
  await waitFor(() => expect(getSignupConfig).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: /solve captcha/i })).toBeNull();
  expect(screen.getByRole("button", { name: /create workspace/i })).not.toBeDisabled();
});

test("a configured captcha blocks submit until it is solved", async () => {
  withCaptcha();
  await renderConfigured();

  const submit = screen.getByRole("button", { name: /create workspace/i });
  expect(submit).toBeDisabled();
  // Disabled with no reason given is where people abandon a form.
  expect(screen.getByText(/complete the challenge above/i)).toBeInTheDocument();

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /solve captcha/i }));
  });
  expect(submit).not.toBeDisabled();
});

test("the disabled submit button names the control blocking it", async () => {
  withCaptcha();
  await renderConfigured();

  const submit = screen.getByRole("button", { name: /create workspace/i });
  const describedBy = submit.getAttribute("aria-describedby");
  expect(describedBy).toBeTruthy();
  expect(document.getElementById(describedBy)).toHaveTextContent(/complete the challenge/i);
});

test("the solved token is sent with the registration", async () => {
  withCaptcha();
  register.mockResolvedValue({ data: { access_token: "t", role: "owner" } });
  await renderConfigured();

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /solve captcha/i }));
  });
  const values = await fillAndSubmit();

  expect(register).toHaveBeenCalledWith(
    values.org, values.username, values.email, values.password, "solved-token"
  );
});

test("a 400 says the captcha failed rather than blaming the details", async () => {
  withCaptcha();
  register.mockRejectedValue(httpError(400, { detail: "Captcha verification failed." }));
  await renderConfigured();

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /solve captcha/i }));
  });
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      "Captcha verification failed — please try the challenge again"
    )
  );
});

test("a 503 tells the user to wait, not to fix anything", async () => {
  // The provider was unreachable and the server failed closed. Nothing the
  // user can correct, so "check your details" would send them round a loop.
  withCaptcha();
  register.mockRejectedValue(httpError(503, { detail: "Could not verify the captcha" }));
  await renderConfigured();

  await act(async () => {
    await userEvent.click(screen.getByRole("button", { name: /solve captcha/i }));
  });
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      "Signup is temporarily unavailable. Please try again in a moment."
    )
  );
});

test("a failed signup asks the widget for a fresh challenge", async () => {
  // Captcha tokens are single-use. Without the reset, a user who fixes a
  // duplicate username and resubmits sends a spent token and is told the
  // captcha failed — for a challenge they solved correctly.
  withCaptcha();
  register.mockRejectedValue(httpError(409, {}));
  await renderConfigured();

  const widget = screen.getByRole("button", { name: /solve captcha/i });
  expect(widget).toHaveAttribute("data-reset", "0");

  await act(async () => {
    await userEvent.click(widget);
  });
  await fillAndSubmit();

  await waitFor(() => expect(widget).toHaveAttribute("data-reset", "1"));
});

test("a successful signup does not reset the widget", async () => {
  // The counter only moves on failure; bumping it on success would re-render a
  // challenge on a form that is already gone.
  withCaptcha();
  register.mockResolvedValue({ data: { access_token: "t", role: "owner" } });
  await renderConfigured();

  const widget = screen.getByRole("button", { name: /solve captcha/i });
  await act(async () => {
    await userEvent.click(widget);
  });
  await fillAndSubmit();

  expect(widget).toHaveAttribute("data-reset", "0");
});

test("a failed config fetch leaves the form usable", async () => {
  // The GET is a convenience; the server still refuses a tokenless signup on a
  // captcha-enabled deployment. Blocking signup on a transient GET would turn
  // a degraded experience into an outage.
  getSignupConfig.mockRejectedValue(new Error("Network Error"));
  register.mockResolvedValue({ data: { access_token: "t", role: "owner" } });

  await renderSignup();
  await waitFor(() => expect(getSignupConfig).toHaveBeenCalled());

  expect(screen.getByRole("button", { name: /create workspace/i })).not.toBeDisabled();
  await fillAndSubmit();
  expect(register).toHaveBeenCalled();
});
