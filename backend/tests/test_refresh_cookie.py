"""Refresh token lives in an httpOnly cookie, not the response body (issue #22).

The point of the change is that JS/XSS can never read the refresh token, so the
attributes on the cookie are the security property under test — not an
implementation detail.
"""


def _login(client):
    return client.post("/api/auth/login", json={"username": "ceo", "password": "Admin@2024"})


def test_login_sets_httponly_refresh_cookie(client):
    r = _login(client)
    assert r.status_code == 200
    # Inspect the raw Set-Cookie header — the parsed jar hides the flags.
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "dw_refresh=" in set_cookie
    assert "httponly" in set_cookie
    assert "path=/api/auth" in set_cookie
    assert "samesite=lax" in set_cookie
    client.cookies.clear()


def test_refresh_token_absent_from_body(client):
    body = _login(client).json()
    assert "access_token" in body
    assert "refresh_token" not in body
    client.cookies.clear()


def test_refresh_without_cookie_is_401(client):
    client.cookies.clear()
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401


def test_logout_clears_the_cookie(client):
    login = _login(client)
    token = login.json()["access_token"]
    r = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # A delete is a Set-Cookie that expires it immediately.
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "dw_refresh=" in set_cookie
    assert ("max-age=0" in set_cookie) or ("expires=" in set_cookie)
    client.cookies.clear()


def test_secure_flag_follows_debug(client, monkeypatch):
    """Secure is off in DEBUG (http dev) and on in production (https)."""
    from app.api.routes import auth

    # DEBUG is true under the test settings → no Secure flag (would be dropped
    # over plain http). Confirm the header omits it here...
    assert "secure" not in _login(client).headers.get("set-cookie", "").lower()
    client.cookies.clear()

    # ...and that the flag is wired to DEBUG, not hardcoded off.
    monkeypatch.setattr(auth.settings, "DEBUG", False)
    assert "secure" in _login(client).headers.get("set-cookie", "").lower()
    client.cookies.clear()
