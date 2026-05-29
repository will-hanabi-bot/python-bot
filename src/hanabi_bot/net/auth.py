"""HTTP login to hanab.live → session cookie.

Port of scala-bot/src/scala_bot/bot.scala lines 121-126 + old-python-bot/main.py auth flow.
"""

from __future__ import annotations

import httpx


class AuthError(RuntimeError):
    pass


def login(login_url: str, username: str, password: str, *, timeout: float = 30.0) -> str:
    """POST credentials, return the raw Set-Cookie header value.

    `version=bot` is sent so the server accepts the request (bypasses the version check).
    """
    print(f'Authenticating to "{login_url}" with username = "{username}".')
    resp = httpx.post(
        login_url,
        data={"username": username, "password": password, "version": "bot"},
        timeout=timeout,
        follow_redirects=False,
    )
    if resp.status_code != 200:
        raise AuthError(f"Authentication failed: HTTP {resp.status_code} {resp.text!r}")
    cookie = resp.headers.get("set-cookie")
    if not cookie:
        raise AuthError(f"No Set-Cookie header in login response: {dict(resp.headers)!r}")
    return cookie
