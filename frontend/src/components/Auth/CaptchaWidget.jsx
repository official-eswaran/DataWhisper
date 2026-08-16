import React, { useEffect, useRef, useState } from "react";

// Signup captcha widget (issue #21). Renders nothing unless the server says a
// provider is configured — the default deployment, dev and the test suite all
// take that path and are unchanged.
//
// The provider → script map lives *here*, not in the API response. The server
// sends only a provider name and a public site key; a misconfigured or
// compromised API must not be able to name a URL the SPA then loads as script.
// An unrecognised provider renders nothing rather than guessing.
//
// hCaptcha and Turnstile expose the same explicit-render API — `render(el,
// opts)` returning a widget id, plus `reset(id)` — which is why one component
// covers both. A third provider needs a row here and one in core/captcha.py.
export const PROVIDERS = {
  hcaptcha: {
    src: "https://js.hcaptcha.com/1/api.js?render=explicit",
    global: "hcaptcha",
  },
  turnstile: {
    src: "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
    global: "turnstile",
  },
};

// Loading state, kept out of React so a second mount reuses the same <script>
// rather than injecting another. Keyed by src.
const scriptPromises = new Map();

function loadScript(src) {
  if (scriptPromises.has(src)) return scriptPromises.get(src);
  const promise = new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.defer = true;
    el.onload = () => resolve();
    el.onerror = () => {
      // Drop the rejected promise so a remount can retry; a cached rejection
      // would make one blocked request permanent for the tab.
      scriptPromises.delete(src);
      reject(new Error(`Could not load ${src}`));
    };
    document.head.appendChild(el);
  });
  scriptPromises.set(src, promise);
  return promise;
}

/**
 * @param provider    provider name from /auth/signup-config
 * @param siteKey     public site key from the same place
 * @param onToken     called with the solved token, or "" when it is no longer valid
 * @param resetSignal increment to force a fresh challenge (see below)
 */
function CaptchaWidget({ provider, siteKey, onToken, resetSignal = 0 }) {
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);
  const onTokenRef = useRef(onToken);
  const [failed, setFailed] = useState(false);

  // Kept in a ref so a new callback identity on the parent's re-render doesn't
  // tear down and re-render the widget — which would throw away a challenge the
  // user has already solved.
  useEffect(() => {
    onTokenRef.current = onToken;
  }, [onToken]);

  const config = PROVIDERS[provider];

  useEffect(() => {
    if (!config || !siteKey) return undefined;
    let cancelled = false;

    loadScript(config.src)
      .then(() => {
        const api = window[config.global];
        // `cancelled` is what makes StrictMode's mount → cleanup → mount safe:
        // the first run's callback lands after its own cleanup and stops here,
        // so only the surviving run draws a widget.
        if (cancelled || !api || !containerRef.current) return;
        widgetIdRef.current = api.render(containerRef.current, {
          sitekey: siteKey,
          callback: (token) => onTokenRef.current(token),
          // Expiry and error must clear the token, not leave the last one in
          // place: submitting an expired token fails at the server, and the
          // user is shown a captcha error for a challenge they did solve.
          "expired-callback": () => onTokenRef.current(""),
          "error-callback": () => onTokenRef.current(""),
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      // The widget went with the unmounted node, so the id no longer refers to
      // anything. Leaving it set makes the next mount think it already has a
      // challenge — and a reset against a dead id throws inside the provider.
      widgetIdRef.current = null;
    };
  }, [config, siteKey]);

  // A captcha token is single-use. After a rejected signup the parent bumps
  // resetSignal, which asks the provider for a fresh challenge — without this,
  // a user who fixes a duplicate username and resubmits sends the token the
  // server has already consumed, and is told the captcha failed.
  useEffect(() => {
    if (!resetSignal || widgetIdRef.current === null) return;
    window[config.global]?.reset?.(widgetIdRef.current);
    onTokenRef.current("");
  }, [resetSignal, config]);

  if (!config || !siteKey) return null;

  if (failed) {
    // Says what is true — the challenge could not be loaded — rather than
    // leaving an empty box above a submit button that will not work. role=alert
    // because it contradicts what the form otherwise implies (#82's lesson).
    return (
      <div className="captcha-error" role="alert">
        The signup challenge could not be loaded. Check your connection or any
        content blocker, then reload the page.
      </div>
    );
  }

  return <div ref={containerRef} data-testid="captcha-widget" />;
}

export default CaptchaWidget;
