"""
Ruchita Interiors — application settings.

Values come from the environment (see `.env.example`) with development-friendly
defaults, so a fresh clone runs with no configuration. Nothing here is secret: the
session key is generated per process in development and must be supplied in
production.

Phase 2 adds the authentication settings (§16). Two rules matter:

- `JWT_SECRET_KEY` must be distinct from `SECRET_KEY`, so rotating one does not
  silently invalidate the other. In production both are mandatory.
- `CORS_ORIGINS` replaces the Phase 1 `FRONTEND_URL`. The old name is still read
  as a fallback so an existing `.env` keeps working.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_ROOT / ".env")


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings:
    """Application configuration resolved once at import time."""

    ENVIRONMENT = os.getenv("FLASK_ENV", "development")
    DEBUG = _as_bool("FLASK_DEBUG", ENVIRONMENT == "development")
    TESTING = _as_bool("FLASK_TESTING", False)

    IS_PRODUCTION = ENVIRONMENT == "production"

    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)

    # A separate signing key for access/refresh tokens. They are generated
    # distinctly in `.env` so that rotating the session key cannot invalidate live
    # tokens.
    #
    # Both keys fall back to a per-process random value, which is what makes a
    # fresh clone runnable with no configuration at all. That fallback is a
    # development convenience with a sharp edge: the value is regenerated on every
    # restart, so a production boot that reaches it silently invalidates every
    # session on each deploy and every token on each worker. `_verify_production_secrets`
    # refuses that combination outright rather than letting it run.
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or SECRET_KEY
    JWT_ALGORITHM = "HS256"
    ACCESS_TOKEN_TTL_SECONDS = _as_int("ACCESS_TOKEN_TTL_SECONDS", 15 * 60)
    REFRESH_TOKEN_TTL_SECONDS = _as_int("REFRESH_TOKEN_TTL_SECONDS", 30 * 24 * 60 * 60)

    # The `.env.example` placeholders. Shipping these is a public, known signing
    # key, so production refuses to start on them.
    SECRET_PLACEHOLDERS = frozenset({"change-me", "changeme", "change_me", "please-change-me"})

    # CSRF double-submit (§16): a readable cookie plus a matching header.
    #
    # The cookie name gets a `__Secure-` prefix in production, so the browser
    # refuses to send it over plain HTTP. The access and refresh cookies below
    # already do this; the CSRF cookie had been left at the plain name in
    # every environment, which meant the frontend's `__Secure-` expectation
    # (in `src/api/client.js`) would silently break the double-submit check
    # on every mutation in production. Aligned here.
    CSRF_COOKIE_NAME = "csrf_token" if not IS_PRODUCTION else "__Secure-csrf_token"
    CSRF_HEADER_NAME = "X-CSRF-Token"
    CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    # Cookie names are prefixed so a plain-HTTP development session and a
    # Secure production session cannot overwrite each other in one browser.
    ACCESS_COOKIE_NAME = "ri_access" if not IS_PRODUCTION else "__Secure-ri_access"
    REFRESH_COOKIE_NAME = "ri_refresh" if not IS_PRODUCTION else "__Secure-ri_refresh"

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{(BACKEND_ROOT / 'instance' / 'ruchita_interiors.db').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # §8.5 connection settings. WAL lets the dashboard read while a write is in
    # progress; foreign_keys is off by default in SQLite and must be set per
    # connection, otherwise the RESTRICT rules in §8.3 are silently ignored.
    SQLITE_JOURNAL_MODE = "WAL"
    SQLITE_BUSY_TIMEOUT_MS = 5000
    SQLITE_SYNCHRONOUS = "NORMAL"

    # Same-origin by default, so CORS is not needed for the standard dev setup: the
    # frontend uses a relative API base URL and the Vite dev server proxies /api.
    # Only a frontend served from a different origin requires an entry here, and
    # "localhost" and "127.0.0.1" must be listed separately - the browser treats
    # them as different origins *and* as different sites for SameSite cookies, so an
    # unlisted origin is silently blocked and surfaces as "Cannot reach the server".
    # An explicitly empty `CORS_ORIGINS` means "no cross-origin access at all",
    # which is the correct production posture for a same-origin deployment. It is
    # distinguished from unset via `is not None` rather than truthiness: `or`
    # treats "" as absent and silently substitutes the two localhost dev origins,
    # so the one value that means "lock it down" could not be expressed.
    _cors = os.getenv("CORS_ORIGINS")
    if _cors is None:
        _cors = os.getenv("FRONTEND_URL") or "http://localhost:5173,http://127.0.0.1:5173"
    CORS_ORIGINS = _cors

    # §16 Content-Security-Policy. The app is a same-origin SPA that loads
    # nothing from anywhere else, so this can be strict rather than permissive —
    # and strictness is the point: it is the backstop for the user-supplied text
    # that flows through the whole product (client names, line-item descriptions,
    # notes, terms) and into the printed documents.
    #
    # No 'unsafe-inline' anywhere, which is why the pre-paint theme script in
    # `frontend/index.html` is a file (`public/theme-boot.js`) rather than an
    # inline block: a synchronously-loaded same-origin script still runs before
    # the first paint, so the no-white-flash guarantee is unchanged and the policy
    # needs no hash to stay accurate when the file changes.
    #
    # Overridable as a single string so the policy can be tightened or relaxed
    # without a code change, but the shipped default is the strict one.
    CONTENT_SECURITY_POLICY = os.getenv("CONTENT_SECURITY_POLICY") or "; ".join(
        (
            # Everything loads from this origin: the bundle, the stylesheet, the
            # manifest, the icons, and the authenticated image routes.
            "default-src 'self'",
            # 'unsafe-inline' is required for styles only, and only because the
            # print documents set a handful of computed style properties (a QR
            # size in mm, a page-break rule) at render time. Scripts get no such
            # exemption.
            "style-src 'self' 'unsafe-inline'",
            "script-src 'self'",
            # data: covers the inline SVG data URIs the UI uses for icons; blob:
            # covers object URLs.
            "img-src 'self' data: blob:",
            "font-src 'self'",
            # The service worker is what makes the PWA installable and offline-capable.
            "worker-src 'self'",
            "manifest-src 'self'",
            # The API is same-origin and sends credentials; no other origin may
            # read any of it.
            "connect-src 'self'",
            # No <form> submits anywhere in the app, and no <iframe> is embedded.
            "form-action 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "object-src 'none'",
        )
    )

    # Seeded owner account (FR-A4). Consumed by `flask seed-admin`, never by a
    # request handler, so a password can never be changed through the API
    # without going through PUT /auth/password.
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    ADMIN_NAME = os.getenv("ADMIN_NAME", "Ruchita Interiors")

    # §16 password policy, enforced at seed time and on password change.
    PASSWORD_MIN_LENGTH = 8

    # §16 login rate limit: 5 attempts per 5 minutes per IP + email.
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS = _as_int("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 5)
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = _as_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 5 * 60)

    # Off by default: `X-Forwarded-For` is client-controlled, so honouring it
    # without a known reverse proxy in front lets anyone spoof their IP and walk
    # around the login limit. Turn it on only when a proxy is deployed that sets
    # the header, and ideally narrow TRUSTED_PROXY_COUNT to match.
    TRUST_PROXY_HEADERS = _as_bool("TRUST_PROXY_HEADERS", False)
    TRUSTED_PROXY_COUNT = _as_int("TRUSTED_PROXY_COUNT", 1)

    # §15 Branding: the logo upload lands in `UPLOADS_DIR` (git-ignored), never in
    # the static folder, so a stored file is only ever served through the
    # authenticated API route rather than by the web server directly.
    UPLOADS_DIR = os.getenv("UPLOADS_DIR", str(BACKEND_ROOT / "uploads"))
    LOGO_SUBDIR = "branding"
    # The payment QR (§8.5) gets its own subdirectory so clearing a logo can never
    # unlink a QR, and vice versa.
    PAYMENT_QR_SUBDIR = "payment-qr"

    # §15/§16 upload policy: images only (PNG/JPEG/WEBP - SVG is rejected for XSS),
    # ≤ 2 MB, validated by extension + MIME + magic bytes, stored under a UUID name.
    #
    # The logo and the payment QR share one policy: both are small raster images
    # the owner uploads through the same control, and a second copy of the rules
    # would be free to drift. Slot-specific names below stay for .env compatibility.
    IMAGE_UPLOAD_MAX_SIZE_BYTES = _as_int("IMAGE_UPLOAD_MAX_SIZE_BYTES", 2 * 1024 * 1024)
    IMAGE_UPLOAD_ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})
    IMAGE_UPLOAD_ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

    LOGO_MAX_SIZE_BYTES = _as_int("LOGO_MAX_SIZE_BYTES", IMAGE_UPLOAD_MAX_SIZE_BYTES)
    LOGO_ALLOWED_EXTENSIONS = IMAGE_UPLOAD_ALLOWED_EXTENSIONS
    LOGO_ALLOWED_MIME_TYPES = IMAGE_UPLOAD_ALLOWED_MIME_TYPES

    # Werkzeug's hard ceiling on a request body, enforced by the framework before
    # any handler runs. Without it the upload limit above is only a limit the
    # handler chooses to apply *after* the body has already been buffered — so a
    # large upload is a memory-exhaustion vector, not a rejected file.
    #
    # It is the whole body, not just the file part: multipart framing and the
    # other form fields ride along, hence the headroom over the 2 MB image limit.
    # A JSON API request that legitimately needs more can raise this explicitly.
    MAX_CONTENT_LENGTH = _as_int("MAX_CONTENT_LENGTH", 3 * 1024 * 1024)

    API_PREFIX = "/api/v1"
    APP_VERSION = "1.0.0"
    # Phase 9A (SERVICES_PLAN): the services catalog rides between Phase 9 and
    # Phase 10 so the later sweeps cover it. Bumping it past 9 keeps `/health`
    # reporting the true build state.
    PHASE = 9.1

    # Deployment: where the built frontend lives, so one origin can serve both the
    # API and the SPA.
    #
    # Render builds `frontend/dist` and serves the whole app from this single Web
    # Service, which is why the frontend's relative `VITE_API_BASE_URL` stays
    # same-origin in production exactly as the dev proxy makes it in development -
    # no CORS entry needed, and the SameSite=Lax auth cookies keep working.
    #
    # The check is existence-based (`app/spa.py`): with no build present the app
    # stays API-only, so tests and a bare `flask run` are unaffected. Overridable
    # only for unusual layouts, e.g. a build served from a different stage.
    SPA_DIST_DIR = os.getenv("SPA_DIST_DIR", str(BACKEND_ROOT.parent / "frontend" / "dist"))

    # Deployment: bring the database up to date and create the owner account on
    # first boot. See app/bootstrap.py.
    #
    # Off by default and off in tests, because it is a deployment convenience, not
    # application behaviour: the documented local path stays Alembic plus
    # `flask db upgrade` and `flask seed-admin`.
    #
    # It exists because Render's Free web services have no shell access and no
    # pre-deploy command (both are paid-only), so there is no way to run those two
    # commands against a fresh instance. On a Free instance the filesystem is
    # ephemeral, so every cold start is a first boot and every start needs this.
    AUTO_SEED_ADMIN = _as_bool("AUTO_SEED_ADMIN", False)

    @classmethod
    def verify_production_secrets(cls) -> None:
        """
        Refuse to run in production on an unusable signing key.

        The fallbacks above are a development convenience: a fresh clone boots with
        no configuration. In production the same code path is a silent outage, and
        a security hole at the same time:

        - **Silent outage.** The fallback is `secrets.token_hex(32)`, generated per
          process. A production boot that reaches it signs cookies and tokens with a
          key nobody else has, so every deploy and every extra worker invalidates
          every live session — the user is logged out at random, with nothing in the
          logs to explain it.
        - **Security hole.** `JWT_SECRET_KEY` falls back to `SECRET_KEY`, so one
          leaked session key also forges tokens; and the `.env.example` placeholder
          `change-me` is a *public* signing key, since the template is committed.

        A loud refusal at boot is the only outcome that is safe in both cases: the
        alternative is a process that appears healthy and is not. A no-op outside
        production, so tests and a bare `flask run` are unaffected.
        """
        if not cls.IS_PRODUCTION:
            return

        problems = []
        for name, value in (("SECRET_KEY", cls.SECRET_KEY), ("JWT_SECRET_KEY", cls.JWT_SECRET_KEY)):
            if not os.getenv(name):
                problems.append(
                    f"{name} is not set — it would fall back to a per-process random "
                    f"value, invalidating every session on each restart."
                )
            elif value.strip().lower() in cls.SECRET_PLACEHOLDERS:
                problems.append(
                    f"{name} is still the .env.example placeholder — that is a public "
                    f"signing key."
                )

        if cls.SECRET_KEY == cls.JWT_SECRET_KEY and os.getenv("JWT_SECRET_KEY"):
            problems.append(
                "SECRET_KEY and JWT_SECRET_KEY are identical — rotating one would "
                "invalidate the other's tokens. Generate two values."
            )

        if len(cls.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be at least 32 characters.")

        if problems:
            raise RuntimeError(
                "Refusing to start in production with unsafe secrets:\n  - "
                + "\n  - ".join(problems)
                + "\n\nGenerate two distinct values, e.g.:\n"
                "  python -c \"import secrets; print(secrets.token_hex(32))\""
            )


settings = Settings()
settings.verify_production_secrets()
