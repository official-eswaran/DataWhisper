import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
import Signup from "./Signup";
import { register } from "../../services/api";

// Signup is the first thing a new user touches and the only place an
// organization gets created. Its failure handling was the part with no cover:
// every non-2xx used to collapse into "Please check your details and try
// again", which never said *what* was wrong — most often the password rule.

vi.mock("../../services/api", () => ({ register: vi.fn() }));
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

const toast = (await import("react-hot-toast")).default;

const renderSignup = (onLogin = vi.fn()) => {
  render(
    <MemoryRouter>
      <Signup onLogin={onLogin} />
    </MemoryRouter>
  );
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
});

// ── The password rule the form advertises ────────────────────────────────────

test("the password hint matches what the backend enforces", () => {
  renderSignup();
  const field = screen.getByPlaceholderText(/^password/i);
  // validate_password_strength requires 10+ chars with a letter and a digit.
  // The form used to say "min 8 chars", so a valid-looking password 422'd.
  expect(field.getAttribute("placeholder")).toMatch(/10\+/);
  expect(field).toHaveAttribute("minLength", "10");
});

test("the password field describes its rule to screen readers", () => {
  renderSignup();
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

  const onLogin = renderSignup();
  const values = await fillAndSubmit();

  expect(register).toHaveBeenCalledWith(
    values.org, values.username, values.email, values.password
  );
  // Registration returns a session, so there is no second login step.
  expect(onLogin).toHaveBeenCalledWith(session);
  expect(toast.success).toHaveBeenCalled();
});

test("the submit button is disabled while the request is in flight", async () => {
  let resolve;
  register.mockReturnValue(new Promise((r) => { resolve = r; }));

  renderSignup();
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

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Password must contain a digit")
  );
});

test("a 422 with no usable detail still says something", async () => {
  register.mockRejectedValue(httpError(422, { detail: "Invalid request." }));

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Please check your details and try again")
  );
});

test("a 422 with an empty errors array falls back rather than showing undefined", async () => {
  register.mockRejectedValue(httpError(422, { detail: "Invalid request.", errors: [] }));

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Please check your details and try again")
  );
});

test("a 409 names the actual conflict", async () => {
  register.mockRejectedValue(httpError(409, { detail: "Username or email already exists" }));

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("That username or email is already taken")
  );
});

test("a 403 explains that signup is closed, not that the details are wrong", async () => {
  // SIGNUPS_OPEN=false is a deployment choice; telling the user to check their
  // details would send them round a loop that cannot succeed.
  register.mockRejectedValue(httpError(403, { detail: "Public signup is closed" }));

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Public signup is closed on this deployment")
  );
});

test("a 429 tells the user to wait rather than to fix their input", async () => {
  register.mockRejectedValue(httpError(429, { detail: "Too many requests" }));

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      "Too many signups from this network. Please try again later."
    )
  );
});

test("a network failure with no response is still reported", async () => {
  register.mockRejectedValue(new Error("Network Error"));

  renderSignup();
  await fillAndSubmit();

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("Could not create your account")
  );
});

test("a failed signup does not log the user in", async () => {
  register.mockRejectedValue(httpError(409, {}));

  const onLogin = renderSignup();
  await fillAndSubmit();

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onLogin).not.toHaveBeenCalled();
});
