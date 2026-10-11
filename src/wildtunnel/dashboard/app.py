from __future__ import annotations

import asyncio
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from wildtunnel.dashboard import db, egress
from wildtunnel.dashboard.db import Admin
from wildtunnel.dashboard.mailer import Mailer
from wildtunnel.dashboard.stats import FrpsStatsSource, StatsSource

_INVALID_CREDENTIALS = HTTPException(status_code=401, detail="invalid credentials")
_NOT_AUTHENTICATED = HTTPException(status_code=401, detail="not authenticated")
_INVALID_OR_EXPIRED_TOKEN = HTTPException(
    status_code=400, detail="invalid or expired token"
)
_PASSWORD_FORM = HTMLResponse("<form method='post'><input name='password'></form>")
_EMAIL_FORM = HTMLResponse("<form method='post'><input name='email'></form>")
_FORGOT_PASSWORD_FORM = HTMLResponse("<form method='post'><input name='email'></form>")
_RESET_PASSWORD_FORM = HTMLResponse(
    "<form method='post'><input name='token'><input name='password'></form>"
)
_RESET_TOKEN_SENT = HTMLResponse(
    "<p>If that email is registered, a reset link was sent.</p>"
)

_DEFAULT_RESET_TOKEN_TTL_SECONDS = 3600.0

# frps's webServer stats port, as bound by server-setup.sh.
_DEFAULT_STATS_BASE_URL = "http://127.0.0.1:7500"


def create_app(
    conn: sqlite3.Connection,
    *,
    session_secret: str,
    mailer: Mailer | None = None,
    reset_token_ttl_seconds: float = _DEFAULT_RESET_TOKEN_TTL_SECONDS,
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
    app.state.mailer = mailer
    app.state.stats_source = stats_source or FrpsStatsSource(
        base_url=_DEFAULT_STATS_BASE_URL, username="", password=""
    )
    app.state.egress_sampler_task = None

    async def current_admin(request: Request) -> Admin:
        if not request.session.get("authenticated"):
            raise _NOT_AUTHENTICATED
        admin = db.get_admin(request.app.state.conn)
        if admin is None:
            raise _NOT_AUTHENTICATED
        return admin

    @app.get("/login")
    async def login_form() -> HTMLResponse:
        return _PASSWORD_FORM

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
        return _PASSWORD_FORM

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

    @app.get("/account/email")
    async def email_form(
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> HTMLResponse:
        return _EMAIL_FORM

    @app.post("/account/email")
    async def bind_email(
        request: Request,
        email: str = Form(...),
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> RedirectResponse:
        db.set_email(request.app.state.conn, email)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/forgot-password")
    async def forgot_password_form() -> HTMLResponse:
        return _FORGOT_PASSWORD_FORM

    @app.post("/forgot-password")
    async def forgot_password(request: Request, email: str = Form(...)) -> HTMLResponse:
        admin = db.get_admin(request.app.state.conn)
        # Always the same response whether or not the email matched a
        # bound address, so this endpoint can't be used to probe which
        # addresses are registered.
        if admin is not None and admin.email is not None and admin.email == email:
            token = secrets.token_urlsafe(32)
            db.create_password_reset_token(
                request.app.state.conn, token, ttl_seconds=reset_token_ttl_seconds
            )
            if mailer is not None:
                mailer.send(
                    to=admin.email,
                    subject="wildtunnel password reset",
                    body=f"Use this code to reset your password: {token}",
                )
        return _RESET_TOKEN_SENT

    @app.get("/reset-password")
    async def reset_password_form() -> HTMLResponse:
        return _RESET_PASSWORD_FORM

    @app.post("/reset-password")
    async def reset_password(
        request: Request, token: str = Form(...), password: str = Form(...)
    ) -> RedirectResponse:
        ok = db.reset_password_with_token(request.app.state.conn, token, password)
        if not ok:
            raise _INVALID_OR_EXPIRED_TOKEN
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/", response_model=None)
    async def root(
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> HTMLResponse | RedirectResponse:
        if admin.must_change_password:
            return RedirectResponse(url="/change-password", status_code=303)
        return HTMLResponse("<p>dashboard</p>")

    @app.get("/stats", response_model=None)
    async def stats(
        request: Request,
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> HTMLResponse | RedirectResponse:
        if admin.must_change_password:
            return RedirectResponse(url="/change-password", status_code=303)
        rows = "".join(
            f"<tr><td>{proxy.name}</td><td>{proxy.connections}</td>"
            f"<td>{proxy.bytes_in}</td><td>{proxy.bytes_out}</td></tr>"
            for proxy in request.app.state.stats_source.current_stats()
        )
        table = (
            "<table><thead><tr><th>tunnel</th><th>connections</th>"
            f"<th>bytes in</th><th>bytes out</th></tr></thead><tbody>{rows}</tbody></table>"
        )
        return HTMLResponse(table)

    @app.get("/egress", response_model=None)
    async def egress_page(
        request: Request,
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> HTMLResponse | RedirectResponse:
        if admin.must_change_password:
            return RedirectResponse(url="/change-password", status_code=303)
        rows = "".join(
            f"<tr><td>{total.tunnel_name}</td><td>{total.day_bytes}</td>"
            f"<td>{total.week_bytes}</td><td>{total.month_bytes}</td></tr>"
            for total in egress.totals_by_tunnel(request.app.state.conn)
        )
        table = (
            "<table><thead><tr><th>tunnel</th><th>day</th>"
            f"<th>week</th><th>month</th></tr></thead><tbody>{rows}</tbody></table>"
        )
        return HTMLResponse(table)

    return app
