from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from wildtunnel.dashboard import db


def ensure_bootstrapped(conn: sqlite3.Connection, secret_file: Path) -> str | None:
    """Create the admin account with a random password, once.

    Returns the generated plaintext password the first time an admin is
    created, or None if an admin account already exists (so the caller
    knows whether a secret file was written).
    """
    if db.get_admin(conn) is not None:
        return None

    password = secrets.token_urlsafe(24)
    db.create_admin(conn, password=password, must_change_password=True)
    secret_file.write_text(password + "\n")
    secret_file.chmod(0o600)
    return password
