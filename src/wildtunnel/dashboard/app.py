from __future__ import annotations

import sqlite3

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from wildtunnel.dashboard import db
from wildtunnel.dashboard.db import Admin

_INVALID_CREDENTIALS = HTTPException(status_code=401, detail="invalid credentials")
_NOT_AUTHENTICATED = HTTPException(status_code=401, detail="not authenticated")
_PASSWORD_FORM = HTMLResponse("<form method='post'><input name='password'></form>")


def create_app(conn: sqlite3.Connection, *, session_secret: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=session_secret)
    app.state.conn = conn

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
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> RedirectResponse:
        db.set_password(request.app.state.conn, password, must_change_password=False)
        return RedirectResponse(url="/", status_code=303)

    @app.get("/", response_model=None)
    async def root(
        admin: Admin = Depends(current_admin),  # noqa: B008
    ) -> HTMLResponse | RedirectResponse:
        if admin.must_change_password:
            return RedirectResponse(url="/change-password", status_code=303)
        return HTMLResponse("<p>dashboard</p>")

    return app
