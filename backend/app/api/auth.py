"""
Ruchita Interiors — authentication endpoints (§9.2).

    POST /api/v1/auth/login     {email, password}  -> sets cookies
    POST /api/v1/auth/logout                        -> clears cookies
    POST /api/v1/auth/refresh                       -> new pair, sliding window
    GET  /api/v1/auth/me                            -> current user
    PUT  /api/v1/auth/password   {current, new}     -> revokes other sessions

Every response uses the §9.1 envelope. `login` and `health` are the only public
endpoints; everything else in the API requires a valid access cookie (§16).

User enumeration is the constraint that shapes this module. A wrong email and a
wrong password produce the identical 401 with the identical message and take
comparable time, so neither reveals whether an account exists (§25).
"""

from __future__ import annotations

from flask import Blueprint, g, request

from app.extensions.database import db
from app.models import User, verify_dummy
from app.schemas.auth import ChangePasswordSchema, LoginSchema, load_or_raise
from app.services.auth import (
    REFRESH_TOKEN_TYPE,
    clear_auth_cookies,
    decode_token,
    issue_tokens,
    load_current_user,
    load_user_from_refresh_token,
    set_auth_cookies,
)
from app.services.csrf import csrf_protect, generate_csrf_token, set_csrf_cookie
from app.services.rate_limit import (
    client_ip,
    enforce_login_rate_limit,
    get_limiter,
)
from app.utils.errors import ApiError, forbidden, unauthenticated, success
from app.utils.guards import OWNER_ROLE

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Deliberately identical for every failure mode, and short enough not to imply a
# lookup was slow.
INVALID_CREDENTIALS = "Email or password is incorrect."


@auth_bp.get("/csrf")
@csrf_protect
def csrf_token():
    """
    Issue the readable CSRF cookie.

    Login is itself a non-GET call, so it needs a CSRF token - but the client has
    no token yet on a cold visit. This endpoint is the bootstrap. It is a GET, so
    the cross-origin preflight problem does not apply and no CSRF check is needed
    for it.

    Returns the token in the response body for cross-origin clients that cannot
    read the cookie via document.cookie.
    """
    token = generate_csrf_token()
    response = success({"csrf_token": token})
    set_csrf_cookie(response, token=token)
    return response


@auth_bp.post("/login")
@csrf_protect
def login():
    """Verify credentials and set the access + refresh cookies."""
    payload = load_or_raise(LoginSchema(), request.get_json(silent=True))
    email = payload["email"].strip().lower()
    password = payload["password"]

    # Checked before the database lookup so a blocked identity costs the same as a
    # wrong password, and so the counter is keyed on the attempted identity.
    enforce_login_rate_limit(email)

    user = db.session.scalar(db.select(User).where(User.email == email))

    # Run the hash check even when the user does not exist. Skipping it would make
    # "no such account" measurably faster than "wrong password", which enumerates
    # accounts through response timing. `and` would short-circuit on a null user, so
    # the branch is written the long way: always hash, then decide.
    password_matches = user.check_password(password) if user is not None else verify_dummy(password)

    if user is None or not password_matches or not user.is_active:
        get_limiter().record_failure(client_ip(), email)
        raise unauthenticated(INVALID_CREDENTIALS)

    get_limiter().record_success(client_ip(), email)

    response = success({"user": user.to_dict()})
    set_auth_cookies(response, user)
    # A fresh token pair, so rotate the CSRF cookie too (§16).
    token = set_csrf_cookie(response)
    # Update the response data to include the new CSRF token
    response.get_json()["data"]["csrf_token"] = token
    return response


@auth_bp.post("/logout")
@csrf_protect
def logout():
    """
    Clear the session server-side.

    Bumping `token_version` revokes any refresh token already captured by a client,
    so logout is not merely "delete the cookie and hope". Idempotent: signing out
    twice is not an error.

    The user is resolved from the *refresh* token when the access token is missing
    or expired. Using the access token alone would leave a captured refresh cookie
    alive for a user who signs out after their 15-minute access token lapsed - the
    exact case a sign-out button is meant to cover.
    """
    user = load_current_user()
    if user is None:
        user = load_user_from_refresh_token()

    if user is not None:
        user.revoke_sessions()
        db.session.commit()

    response = success({"logged_out": True})
    clear_auth_cookies(response)
    token = set_csrf_cookie(response)
    response.get_json()["data"]["csrf_token"] = token
    return response


@auth_bp.post("/refresh")
@csrf_protect
def refresh():
    """
    Exchange a valid refresh token for a new pair (sliding 30-day window).

    The refresh cookie is path-scoped to `/api/v1/auth`, so it is only ever
    presented to this blueprint.
    """
    from app.config.settings import settings

    token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not token:
        raise unauthenticated("Your session has expired. Please sign in again.")

    claims = decode_token(token, REFRESH_TOKEN_TYPE)
    user = db.session.get(User, int(claims["sub"]))

    # A revoked user is signed out; a null user means the row was deleted.
    if user is None or not user.is_active or (user.token_version or 0) != claims.get("tv", -1):
        raise unauthenticated("Your session has expired. Please sign in again.")

    response = success({"user": user.to_dict()})
    set_auth_cookies(response, user)
    token = set_csrf_cookie(response)
    response.get_json()["data"]["csrf_token"] = token
    return response


@auth_bp.get("/me")
@csrf_protect
def me():
    """Return the signed-in user. 401 when there is no valid session."""
    user = load_current_user()
    if user is None:
        raise unauthenticated()
    return success({"user": user.to_dict()})


@auth_bp.put("/password")
@csrf_protect
def change_password():
    """
    Change the owner's password.

    Owner-only, and the one route here that says so explicitly rather than through
    a decorator: it does not use `login_required` (it resolves the user itself so
    it can distinguish "no session" from "wrong current password"), so the role
    check is inline. Without it, any account that appeared in the users table could
    take over the owner's credentials — `revoke_sessions()` kicks the real owner
    out and leaves the caller holding the only working session.

    Verifies the current password first, then bumps `token_version`, which
    invalidates every other session - including this one, so the client must
    re-authenticate afterwards (§25).
    """
    user = load_current_user()
    if user is None:
        raise unauthenticated()
    if (user.role or "").strip().lower() != OWNER_ROLE:
        raise forbidden("Only the owner can change the account password.")

    payload = load_or_raise(ChangePasswordSchema(), request.get_json(silent=True))

    if not user.check_password(payload["current_password"]):
        raise forbidden("Your current password is incorrect.")

    user.set_password(payload["new_password"])
    user.revoke_sessions()
    db.session.commit()

    response = success({"password_changed": True, "reauthenticate": True})
    clear_auth_cookies(response)
    token = set_csrf_cookie(response)
    response.get_json()["data"]["csrf_token"] = token
    return response
