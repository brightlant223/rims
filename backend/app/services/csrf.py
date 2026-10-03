"""
Ruchita Interiors — CSRF double-submit protection.

§16 requires a readable `csrf_token` cookie plus a matching `X-CSRF-Token`
header on every non-GET API call.

Why double-submit, given the cookies are already SameSite=Lax: SameSite stops the
browser attaching cookies to a cross-site *POST*, which is most of the risk, but it
is a browser-side mitigation that a subdomain or a mis-set cookie policy can
defeat. The double-submit check is a server-side check on a value the attacker
cannot know, because a cross-origin page cannot read the cookie even if it is
submitted.

The token is intentionally NOT httpOnly - the frontend has to read it to put it in
the header. That is what makes it "double": the cookie proves the request came
from this browser, the header proves the request was made by code that could read
that cookie rather than by a blind form post.
"""

from __future__ import annotations

import hmac
import secrets
from functools import wraps

from flask import current_app, request

from app.utils.errors import forbidden


def _config(key: str):
    try:
        return current_app.config[key]
    except RuntimeError:  # pragma: no cover - only outside an app context
        from app.config.settings import settings

        return getattr(settings, key)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response, token: str | None = None) -> None:
    """
    Issue the readable CSRF cookie.

    Not httpOnly. `SameSite=None` with `Secure=True` is required for cross-site
    cookie delivery (e.g., Netlify frontend -> PythonAnywhere backend).
    """
    token = token or generate_csrf_token()
    response.set_cookie(
        _config("CSRF_COOKIE_NAME"),
        token,
        httponly=False,
        samesite="None",
        secure=True,
        max_age=_config("REFRESH_TOKEN_TTL_SECONDS"),
        path="/",
    )
    return token


def ensure_csrf_cookie(response):
    """Add a CSRF cookie if the client does not already have a usable one."""
    existing = request.cookies.get(_config("CSRF_COOKIE_NAME"))
    if not existing:
        set_csrf_cookie(response)
    return response


def verify_csrf() -> None:
    """
    Raise 403 unless the header matches the cookie.

    The comparison is constant-time so the endpoint cannot be used as an oracle to
    guess a token one character at a time.
    """
    cookie_name = _config("CSRF_COOKIE_NAME")
    header_name = _config("CSRF_HEADER_NAME")

    cookie_token = request.cookies.get(cookie_name)
    header_token = request.headers.get(header_name)

    if not cookie_token or not header_token:
        raise forbidden("Security check failed. Please reload the page and try again.")

    if not hmac.compare_digest(cookie_token, header_token):
        raise forbidden("Security check failed. Please reload the page and try again.")


def csrf_protect(view):
    """
    Apply the double-submit check to non-GET requests.

    GET/HEAD/OPTIONS are exempt because they must not mutate anything, and the
    browser is allowed to issue them cross-origin. A cross-site form or fetch
    cannot set a custom header without passing CORS preflight first, so requiring
    the header is what blocks the forged request.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if request.method in _config("CSRF_PROTECTED_METHODS"):
            verify_csrf()
        return view(*args, **kwargs)

    return wrapper
