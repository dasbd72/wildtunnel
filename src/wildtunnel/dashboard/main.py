from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI

from wildtunnel.dashboard import db
from wildtunnel.dashboard.app import create_app
from wildtunnel.dashboard.bootstrap import ensure_bootstrapped

_DEFAULT_PORT = 8080


def _env_path(name: str) -> Path:
    return Path(os.environ[name])


def build_app() -> FastAPI:
    """Construct the dashboard FastAPI app from its env-var contract.

    Opens (creating if needed) the sqlite db at DASHBOARD_DB_PATH, runs
    the schema migration and one-time admin bootstrap (writing the
    generated password to DASHBOARD_SECRET_FILE the first time only),
    then builds the app using the session secret persisted at
    DASHBOARD_SESSION_SECRET_FILE. Safe to call more than once: bootstrap
    is a no-op once an admin account exists.
    """
    db_path = _env_path("DASHBOARD_DB_PATH")
    secret_file = _env_path("DASHBOARD_SECRET_FILE")
    session_secret_file = _env_path("DASHBOARD_SESSION_SECRET_FILE")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    db.init_schema(conn)
    ensure_bootstrapped(conn, secret_file)

    session_secret = session_secret_file.read_text().strip()
    return create_app(conn, session_secret=session_secret)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("DASHBOARD_PORT", _DEFAULT_PORT))
    uvicorn.run(build_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
