import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { FiBriefcase, FiDatabase, FiLock, FiMail, FiUser } from "react-icons/fi";
import { getSignupConfig, register } from "../../services/api";
import CaptchaWidget from "./CaptchaWidget";
import "./Login.css";

function Signup({ onLogin }) {
  const [form, setForm] = useState({ org: "", username: "", email: "", password: "" });
  const [loading, setLoading] = useState(false);
  // null until the server answers, and null forever on a deployment with no
  // provider — which is the default and every existing install (issue #21).
  const [captcha, setCaptcha] = useState(null);
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaNonce, setCaptchaNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSignupConfig()
      .then((res) => {
        // No optional chaining on `res.data`: a response without a body is not
        // a case worth a branch nothing covers — it throws, the catch below
        // takes it, and the form is left usable, which is the same outcome.
        if (!cancelled) setCaptcha(res.data.captcha ?? null);
      })
      // A failed config fetch leaves the form usable without a challenge. The
      // server is the thing that enforces this, and it will reject a tokenless
      // signup with a message saying so — showing no widget and letting the
      // attempt fail is better than blocking signup on a transient GET.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await register(
        form.org,
        form.username,
        form.email,
        form.password,
        captchaToken
      );
      onLogin(res.data); // register returns tokens — log straight in
      toast.success("Organization created — welcome to DataWhisper!");
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) toast.error("That username or email is already taken");
      else if (status === 422) {
        // The API says exactly which field failed and why (e.g. "Password must
        // contain a digit"). Showing that beats a generic "check your details",
        // which left the user guessing — most often about the password rule.
        const first = err?.response?.data?.errors?.[0]?.msg;
        toast.error(first || "Please check your details and try again");
      } else if (status === 403) {
        toast.error("Public signup is closed on this deployment");
      } else if (status === 429) {
        toast.error("Too many signups from this network. Please try again later.");
      } else if (status === 400) {
        toast.error("Captcha verification failed — please try the challenge again");
      } else if (status === 503) {
        toast.error("Signup is temporarily unavailable. Please try again in a moment.");
      } else toast.error("Could not create your account");

      // Every failure path, not just the captcha ones. The token was spent by
      // the attempt — the server consumes it whether the rest of the request
      // succeeded or not — so a resubmit with the same token is refused for a
      // challenge the user genuinely solved.
      if (captcha) setCaptchaNonce((n) => n + 1);
    } finally {
      setLoading(false);
    }
  };

  // A configured captcha must be solved before the button does anything. The
  // server refuses a tokenless signup regardless; this is so the user finds out
  // before filling the form in, not after.
  const awaitingCaptcha = Boolean(captcha) && !captchaToken;

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-logo">
          <FiDatabase size={40} aria-hidden="true" />
          <h1>Create your workspace</h1>
          <p>Start querying your data in plain English</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form" aria-label="Create account">
          <div className="input-group">
            <FiBriefcase className="input-icon" aria-hidden="true" />
            <label htmlFor="org" className="sr-only">Organization name</label>
            <input
              id="org"
              name="organization"
              type="text"
              placeholder="Organization name"
              autoComplete="organization"
              value={form.org}
              onChange={update("org")}
              required
            />
          </div>

          <div className="input-group">
            <FiUser className="input-icon" aria-hidden="true" />
            <label htmlFor="username" className="sr-only">Username</label>
            <input
              id="username"
              name="username"
              type="text"
              placeholder="Username"
              autoComplete="username"
              value={form.username}
              onChange={update("username")}
              required
            />
          </div>

          <div className="input-group">
            <FiMail className="input-icon" aria-hidden="true" />
            <label htmlFor="email" className="sr-only">Email</label>
            <input
              id="email"
              name="email"
              type="email"
              placeholder="Email"
              autoComplete="email"
              value={form.email}
              onChange={update("email")}
              required
            />
          </div>

          <div className="input-group">
            <FiLock className="input-icon" aria-hidden="true" />
            <label htmlFor="password" className="sr-only">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              placeholder="Password (10+ chars, with a letter and a number)"
              autoComplete="new-password"
              value={form.password}
              onChange={update("password")}
              minLength={10}
              required
              aria-describedby="password-help"
            />
            {/* Must mirror validate_password_strength in core/security.py — it
                said "min 8" while the backend enforced 10, so a valid-looking
                password 422'd with no indication of why. */}
            <span id="password-help" className="sr-only">
              Password must be at least 10 characters and contain a letter and a number
            </span>
          </div>

          {captcha && (
            <CaptchaWidget
              provider={captcha.provider}
              siteKey={captcha.site_key}
              onToken={setCaptchaToken}
              resetSignal={captchaNonce}
            />
          )}

          <button
            type="submit"
            className="login-btn"
            disabled={loading || awaitingCaptcha}
            aria-busy={loading}
            aria-describedby={awaitingCaptcha ? "captcha-help" : undefined}
          >
            {loading ? "Creating…" : "Create workspace"}
          </button>
          {awaitingCaptcha && (
            // A disabled button with no explanation is the reason people give
            // up on a form. Screen readers get it via aria-describedby above.
            <span id="captcha-help" className="login-hint">
              Complete the challenge above to create your workspace
            </span>
          )}
        </form>

        <div className="login-footer">
          <span>Already have an account? </span>
          <Link to="/login">Sign in</Link>
        </div>
      </div>
    </div>
  );
}

export default Signup;
