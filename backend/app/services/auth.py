"""
Ruchita Interiors — access/refresh token issuing and cookie handling.

§16 fixes the shape: a 15-minute access token and a 30-day sliding refresh token,
both in httpOnly cookies, the refresh cookie path-scoped to `/api/v1/auth` so it
is not attached to ordinary business requests.

PyJWT is used directly rather than flask-jwt-extended because this design is
cookie-first: the browser never sends an `Authorization` header, and the CSRF
double-submit in `csrf.py` has to own the request. A Bearer-oriented library
would add a second, conflicting CSRF mechanism.

Token revocation: both tokens embed the user's `token_version`. Calling
`User.revoke_sessions()` bumps that counter, so every previously issued refresh
token fails verification. This is what makes logout and password change take
effect immediately, which stateless JWTs otherwise cannot do.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from flask import current_app, g, request

from app.config.settings import settings
from app.utils.errors import ApiError, unauthenticated

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def _config(key: str):
    """
    Read configuration from the current app, falling back to the module default.

    Everything goes through `current_app.config` so that per-app overrides in
    tests take effect, rather than reading the import-time settings singleton.
    """
    try:
        return current_app.config[key]
    except RuntimeError:
        return getattr(settings, key)


def _secret() -> str:
    return _config("JWT_SECRET_KEY") or _config("SECRET_KEY")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_tokens(user) -> tuple[str, str]:
    """Return `(access_token, refresh_token)` for a user."""
    issued_at = _now()
    base_claims = {
        "sub": str(user.id),
        "email": user.email,
        "tv": user.token_version or 0,
        "iat": issued_at,
    }

    access_ttl = _config("ACCESS_TOKEN_TTL_SECONDS")
    access_token = jwt.encode(
        {
            **base_claims,
            "type": ACCESS_TOKEN_TYPE,
            "exp": issued_at + timedelta(seconds=access_ttl),
        },
        _secret(),
        algorithm=_config("JWT_ALGORITHM"),
    )

    refresh_ttl = _config("REFRESH_TOKEN_TTL_SECONDS")
    refresh_token = jwt.encode(
        {
            **base_claims,
            "type": REFRESH_TOKEN_TYPE,
            "exp": issued_at + timedelta(seconds=refresh_ttl),
        },
        _secret(),
        algorithm=_config("JWT_ALGORITHM"),
    )

    return access_token, refresh_token


def decode_token(token: str, expected_type: str) -> dict:
    """
    Verify a token and return its claims, or raise `ApiError` (401).

    Every failure - bad signature, expiry, wrong type, revoked - returns the same
    generic 401 so the client cannot distinguish them, and none of them reveal
    whether a token was ever valid.
    """
    generic = unauthenticated("Your session has expired. Please sign in again.")

    try:
        claims = jwt.decode(
            token,
            _secret(),
            algorithms=[_config("JWT_ALGORITHM")],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise generic
    except jwt.InvalidTokenError:
        raise generic

    if claims.get("type") != expected_type:
        raise generic

    return claims


# --------------------------------------------------------------- cookies


def _cookie_kwargs(max_age: int) -> dict:
    """
    Shared cookie flags.

    `SameSite=None` with `Secure=True` is required for cross-site cookie delivery
    (e.g., Netlify frontend -> PythonAnywhere backend). `Secure` requires HTTPS.
    """
    is_production = bool(_config("IS_PRODUCTION"))
    return {
        "httponly": True,
        "samesite": "None",
        "secure": True,
        "max_age": max_age,
        "path": "/",
    }


def set_auth_cookies(response, user) -> None:
    """Attach the access and refresh cookies to a response."""
    access_token, refresh_token = issue_tokens(user)

    access_ttl = _config("ACCESS_TOKEN_TTL_SECONDS")
    refresh_ttl = _config("REFRESH_TOKEN_TTL_SECONDS")

    response.set_cookie(
        _config("ACCESS_COOKIE_NAME"),
        access_token,
        **_cookie_kwargs(access_ttl),
    )
    # Path-scoped to the auth blueprint (§16): the refresh token is never sent
    # with ordinary business requests, which limits exposure if one leaks.
    response.set_cookie(
        _config("REFRESH_COOKIE_NAME"),
        refresh_token,
        **{**_cookie_kwargs(refresh_ttl), "path": f"{_config('API_PREFIX')}/auth"},
    )


def clear_auth_cookies(response) -> None:
    """Remove both auth cookies. Must use the same path as when they were set."""
    response.delete_cookie(
        _config("ACCESS_COOKIE_NAME"),
        path="/",
        samesite="None",
        secure=True,
    )
    response.delete_cookie(
        _config("REFRESH_COOKIE_NAME"),
        path=f"{_config('API_PREFIX')}/auth",
        samesite="None",
        secure=True,
    )


# -------------------------------------------------------- request-scoped


def load_current_user():
    """
    Resolve the caller from the access cookie, or None.

    Populates `g.current_user` once per request. A missing, invalid or revoked
    cookie is not an error here - the endpoint decides whether auth was required.
    """
    if "current_user" in g:
        return g.current_user

    token = request.cookies.get(_config("ACCESS_COOKIE_NAME"))
    user = None

    if token:
        try:
            claims = decode_token(token, ACCESS_TOKEN_TYPE)
        except ApiError:
            claims = None

        if claims:
            from app.extensions.database import db
            from app.models import User

            user = db.session.get(User, int(claims["sub"]))
            # Revoked (token_version moved) or deactivated: treat as signed out.
            if user and (not user.is_active or (user.token_version or 0) != claims.get("tv", -1)):
                user = None

    g.current_user = user
    return user


def load_user_from_refresh_token():
    """
    Resolve the user behind a valid refresh cookie, or None.

    Separate from `load_current_user` because the two tokens answer different
    questions: this one is the "does the long-lived credential still belong to a
    live account" check that logout and password change need, where the access
    token may already be gone.

    Returns None rather than raising for every failure mode, so callers such as
    logout stay idempotent and reveal nothing about the token (§25).
    """
    token = request.cookies.get(_config("REFRESH_COOKIE_NAME"))
    if not token:
        return None

    try:
        claims = decode_token(token, REFRESH_TOKEN_TYPE)
    except ApiError:
        return None

    from app.extensions.database import db
    from app.models import User

    user = db.session.get(User, int(claims["sub"]))
    if user is None or not user.is_active or (user.token_version or 0) != claims.get("tv", -1):
        return None
    return user
