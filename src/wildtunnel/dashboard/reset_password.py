from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

from wildtunnel.dashboard import db


def reset_password(conn: sqlite3.Connection, secret_file: Path) -> str:
    """Generate a fresh admin password and force a change on next login.

    Unlike `bootstrap.ensure_bootstrapped`, this has no no-op branch: it
    always generates a new password and overwrites `secret_file`, even if
    called repeatedly - that's the whole point of a reset.
    """
    password = secrets.token_urlsafe(24)
    db.set_password(conn, password, must_change_password=True)
    secret_file.write_text(password + "\n")
    secret_file.chmod(0o600)
    return password


def main() -> None:
    db_path = Path(os.environ["DASHBOARD_DB_PATH"])
    secret_file = Path(os.environ["DASHBOARD_SECRET_FILE"])

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    db.init_schema(conn)
    password = reset_password(conn, secret_file)
    print(f"new admin password: {password}")


if __name__ == "__main__":
    main()
