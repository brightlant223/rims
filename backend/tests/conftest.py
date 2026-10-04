"""
Shared fixtures.

Every test gets an isolated app backed by its own temporary SQLite file, so no test
can touch the developer's database and tests cannot leak state into one another.

`app` and `client` are the base pair. Auth tests that need a seeded owner account
build their own app (see `test_auth.py`) because the seeding is specific to that
suite.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions.database import db
from app.services.rate_limit import reset_rate_limit


@pytest.fixture()
def app(tmp_path):
    """
    A fresh app on a throwaway database file.

    A file rather than `:memory:` because Phase 2 enables `PRAGMA foreign_keys=ON`
    and WAL (§8.5); an in-memory database silently ignores the journal mode, and a
    file exercises the same connection settings as production.
    """
    db_path = tmp_path / "ruchita_test.db"
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path.as_posix()}",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            # The developer's local backend/.env may set FLASK_ENV=production,
            # which would make the settings module choose the production CSRF
            # cookie name ("__Secure-csrf_token"). Test fixtures and their
            # helpers throughout the suite hardcode the dev name ("csrf_token"),
            # so pin it here to keep tests deterministic regardless of the
            # developer's local environment. The production cookie name itself
            # is exercised in tests/test_config.py.
            "CSRF_COOKIE_NAME": "csrf_token",
        }
    )

    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _isolate_default_database(tmp_path, monkeypatch):
    """
    Send the *default* database somewhere harmless, for every test.

    The `app` fixture passes an explicit URI, but a test is free to build its own
    app, and `create_app()` reads `SQLALCHEMY_DATABASE_URI` off the settings
    singleton - which defaults to `backend/instance/ruchita_interiors.db`. A test
    that omits the override therefore opens the developer's real database, and any
    `db.create_all()` / `db.drop_all()` in it rewrites or empties that file.

    Not hypothetical: `test_allowed_actions.py` built its app without the override
    and called `drop_all()`, so an ordinary `npm test` dropped every table in the
    development database - clients, invoices, quotations and payments - while
    leaving `alembic_version` claiming a revision whose schema no longer existed.
    The module docstring above promises that no test can do this; redirecting the
    default is what makes the promise true rather than merely intended.

    Patched on the settings object rather than the environment because
    `app.config.settings` resolves `DATABASE_URL` once at import, long before any
    fixture runs.
    """
    from app.config.settings import settings

    db_path = tmp_path / "default_isolated.db"
    monkeypatch.setattr(
        settings, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{db_path.as_posix()}", raising=False
    )
    yield


@pytest.fixture(autouse=True)
def _pin_dev_csrf_cookie_name(request, monkeypatch):
    """
    Force every test app to use the dev CSRF cookie name ("csrf_token").

    The production cookie name is "__Secure-csrf_token" (settings.py chooses it
    when `IS_PRODUCTION` is True). A developer's local `backend/.env` often has
    `FLASK_ENV=production` set for local production-mode testing, which makes
    `create_app()` read the production name. Most test files hardcode the dev
    name in their own cookie helpers (`_get_csrf`, `_csrf`, ...), so without this
    pin every such test would fail with a 403 "Security check failed" on the
    first mutation.

    `tests/test_config.py` explicitly overrides `CSRF_COOKIE_NAME` on the apps
    it builds, and it is the only suite that exercises the production cookie
    name, so this pin does not paper over that coverage.

    Monkey-patched on `app.config.settings` (the singleton `create_app` reads
    from at import time) rather than in the app config dict, because that
    reaches every `create_app(...)` call the test suite makes — the conftest
    `app` fixture, plus every per-file helper that builds its own app in
    `test_auth.py`, `test_bootstrap.py`, `test_config.py`, `test_health.py`,
    and `test_spa.py`.
    """
    from app.config.settings import settings

    if request.node.fspath and "test_config" in str(request.node.fspath):
        # Let test_config build its apps with the config values it wants —
        # it exercises the production CSRF name explicitly.
        yield
        return

    monkeypatch.setattr(settings, "CSRF_COOKIE_NAME", "csrf_token", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_login_rate_limit():
    """
    The rate limiter is process-wide (§16 in-memory, single process), so a test that
    exhausts the budget would otherwise block unrelated tests that run afterwards.
    """
    reset_rate_limit()
    yield
    reset_rate_limit()


def _csrf_token(client) -> str:
    """Read the CSRF cookie the test client carries after a request sets it."""
    return next(
        (c.value for k, c in getattr(client, "_cookies", {}).items() if isinstance(k, tuple) and k[2] == "csrf_token"),
        "",
    )


def _csrf(client) -> dict:
    """Drop a fresh CSRF header onto the cookie the `client` fixture holds."""
    client.get("/api/v1/auth/csrf")
    return {"X-CSRF-Token": _csrf_token(client)}


@pytest.fixture()
def authed_client(client):
    """A test client carrying a valid access cookie (login is Phase 2 behaviour)."""
    from app.models import User

    with client.application.app_context():
        user = User(email="owner@test.local", name="Owner", role="owner", is_active=True)
        user.set_password("password-123456")
        db.session.add(user)
        db.session.commit()

    # GET /auth/csrf sets the csrf cookie; read it *after* so it matches the cookie
    # the browser will send on the login POST (double-submit, §16).
    client.get("/api/v1/auth/csrf")
    token = _csrf_token(client) or client.get("/api/v1/auth/csrf").get_json().get("csrf_token", "")
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@test.local", "password": "password-123456"},
        headers={"X-CSRF-Token": token},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    return client
