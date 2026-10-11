from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from wildtunnel.dashboard import db, egress
from wildtunnel.dashboard.db import Admin
from wildtunnel.dashboard.stats import (
    DEFAULT_STATS_BASE_URL,
    FrpsStatsSource,
    StatsSource,
)

_INVALID_CREDENTIALS = HTTPException(status_code=401, detail="invalid credentials")


class _MustChangePassword(Exception):
    """Raised by current_admin_with_password_set to force /change-password."""


class _NotAuthenticated(Exception):
    """Raised by current_admin to send an unauthenticated request to /login."""


_STYLE = """
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1a1d23; --muted: #5b6472;
  --accent: #2563eb; --border: #e3e6ea;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b0d12; --panel: #151822; --text: #e6e8eb; --muted: #9aa3af;
    --accent: #6ea8fe; --border: #262b36;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text); line-height: 1.5;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif;
}
.container { max-width: 640px; margin: 0 auto; padding: 32px 20px; }
h1 { font-size: 1.25rem; font-weight: 600; margin: 0 0 20px; }
p { color: var(--muted); }
nav { display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }
nav a, nav button {
  color: var(--muted); text-decoration: none; font-size: 0.9rem; background: none;
  border: none; padding: 0; cursor: pointer; font-family: inherit;
}
nav a:hover, nav button:hover { color: var(--accent); }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 24px;
}
form { display: flex; flex-direction: column; gap: 14px; }
label { display: flex; flex-direction: column; gap: 6px; font-size: 0.85rem; color: var(--muted); }
input {
  padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg); color: var(--text); font-size: 0.95rem; font-family: inherit;
}
input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
button[type='submit'] {
  padding: 10px 16px; border-radius: 8px; border: none; background: var(--accent);
  color: #fff; font-size: 0.95rem; font-weight: 500; cursor: pointer;
  align-self: flex-start;
}
button[type='submit']:hover { opacity: 0.9; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 500; }
th:not(:first-child), td:not(:first-child) { text-align: right; }
"""

_NAV = (
    "<nav>"
    "<a href='/'>Dashboard</a>"
    "<a href='/stats'>Stats</a>"
    "<a href='/egress'>Egress</a>"
    "<form method='post' action='/logout'><button type='submit'>Log out</button></form>"
    "</nav>"
)

_FIELD_META = {
    "password": ("Password", "password"),
    "current_password": ("Current password", "password"),
}


def _page(body: str) -> HTMLResponse:
    """Wrap a fragment in a styled document with a mobile viewport.

    Without the viewport meta tag, mobile browsers render the bare
    fragment at desktop width and zoom out, making every page too small
    to read or tap.
    """
    return HTMLResponse(
        "<!doctype html><html><head>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{_STYLE}</style>"
        f"</head><body><div class='container'>{body}</div></body></html>"
    )


def _form(*field_names: str, submit_label: str = "Submit") -> str:
    """Build a labeled POST form fragment, one field per name, in order."""
    fields = "".join(
        f"<label>{_FIELD_META[name][0]}"
        f"<input type='{_FIELD_META[name][1]}' name='{name}' required></label>"
        for name in field_names
    )
    return f"<form method='post'>{fields}<button type='submit'>{submit_label}</button></form>"


def _card(body: str) -> str:
    return f"<div class='card'>{body}</div>"


def _auth_page(title: str, body: str) -> HTMLResponse:
    """A pre-/semi-authenticated page (login, password reset): no nav."""
    return _page(f"<h1>{title}</h1>{_card(body)}")


def _dashboard_page(title: str, body: str) -> HTMLResponse:
    """An authenticated dashboard page: nav bar plus a heading and card."""
    return _page(f"{_NAV}<h1>{title}</h1>{_card(body)}")


def create_app(
    conn: sqlite3.Connection,
    *,
    session_secret: str,
    stats_source: StatsSource | None = None,
    egress_sample_interval_seconds: float | None = None,
) -> FastAPI:
    egress.init_schema(conn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The periodic egress sampler only runs when a cadence is given
        # (production wiring, via main.py). Tests exercising routes via
        # TestClient without a `with` block never trigger lifespan events
        # at all, so this has no effect on them either way; tests that do
        # want the sampler running use `with TestClient(app) as client`.
        task = None
        if egress_sample_interval_seconds is not None:
            task = asyncio.create_task(
                egress.run_periodically(
                    app.state.stats_source,
                    app.state.conn,
                    egress_sample_interval_seconds,
                )
            )
        app.state.egress_sampler_task = task
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=session_secret)
    app.state.conn = conn
    app.state.stats_source = stats_source or FrpsStatsSource(
        base_url=DEFAULT_STATS_BASE_URL, username="", password=""
    )
    app.state.egress_sampler_task = None

    async def current_admin(request: Request) -> Admin:
        if not request.session.get("authenticated"):
            raise _NotAuthenticated
        admin = db.get_admin(request.app.state.conn)
        if admin is None:
            raise _NotAuthenticated
        return admin

    async def current_admin_with_password_set(
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> Admin:
        # Everywhere except /change-password (the escape valve) and
        # /login must stay unreachable until the forced first-login
        # password change is done - issue #8's "no other route is
        # reachable" acceptance criterion.
        if admin.must_change_password:
            raise _MustChangePassword
        return admin

    @app.exception_handler(_MustChangePassword)
    async def _redirect_to_change_password(
        request: Request, exc: _MustChangePassword
    ) -> RedirectResponse:
        return RedirectResponse(url="/change-password", status_code=303)

    @app.exception_handler(_NotAuthenticated)
    async def _redirect_to_login(
        request: Request, exc: _NotAuthenticated
    ) -> RedirectResponse:
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/login")
    async def login_form() -> HTMLResponse:
        return _auth_page("Log in", _form("password", submit_label="Log in"))

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)) -> RedirectResponse:
        admin = db.get_admin(request.app.state.conn)
        if admin is None or not db.verify_password(admin, password):
            raise _INVALID_CREDENTIALS
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)

    @app.get("/change-password")
    async def change_password_form(
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> HTMLResponse:
        if admin.must_change_password:
            return _auth_page(
                "Set a new password", _form("password", submit_label="Save")
            )
        return _auth_page(
            "Change your password",
            _form("current_password", "password", submit_label="Save"),
        )

    @app.post("/change-password")
    async def change_password(
        request: Request,
        password: str = Form(...),
        current_password: str | None = Form(None),
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> RedirectResponse:
        # The forced first-login change (must_change_password) doesn't
        # require the bootstrap password back, since the admin just read
        # it from the secret file. A voluntary change later must prove
        # knowledge of the current password first.
        if not admin.must_change_password and (
            current_password is None or not db.verify_password(admin, current_password)
        ):
            raise _INVALID_CREDENTIALS
        db.set_password(request.app.state.conn, password, must_change_password=False)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/")
    async def root(
        admin: Admin = Depends(current_admin_with_password_set),  # noqa: B008
    ) -> HTMLResponse:
        return _dashboard_page("Dashboard", "<p>Manage your wildtunnel deployment.</p>")

    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/stats")
    async def stats(
        request: Request,
        admin: Admin = Depends(current_admin_with_password_set),  # noqa: B008
    ) -> HTMLResponse:
        rows = "".join(
            f"<tr><td>{proxy.name}</td><td>{proxy.connections}</td>"
            f"<td>{proxy.bytes_in}</td><td>{proxy.bytes_out}</td></tr>"
            for proxy in request.app.state.stats_source.current_stats()
        )
        table = (
            "<table><thead><tr><th>tunnel</th><th>connections</th>"
            f"<th>bytes in</th><th>bytes out</th></tr></thead><tbody>{rows}</tbody></table>"
        )
        return _dashboard_page("Live stats", table)

    @app.get("/egress")
    async def egress_page(
        request: Request,
        admin: Admin = Depends(current_admin_with_password_set),  # noqa: B008
    ) -> HTMLResponse:
        rows = "".join(
            f"<tr><td>{total.tunnel_name}</td><td>{total.day_bytes}</td>"
            f"<td>{total.week_bytes}</td><td>{total.month_bytes}</td></tr>"
            for total in egress.totals_by_tunnel(request.app.state.conn)
        )
        table = (
            "<table><thead><tr><th>tunnel</th><th>day</th>"
            f"<th>week</th><th>month</th></tr></thead><tbody>{rows}</tbody></table>"
        )
        return _dashboard_page("Egress history", table)

    return app
