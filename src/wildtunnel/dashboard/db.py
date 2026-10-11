from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


@dataclass(frozen=True)
class Admin:
    password_hash: str
    must_change_password: bool


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            password_hash TEXT NOT NULL,
            must_change_password INTEGER NOT NULL
        )
        """
    )
    conn.commit()


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"{_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(admin: Admin, password: str) -> bool:
    iterations_str, salt, expected_hex = admin.password_hash.split("$")
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations_str)
    )
    return hmac.compare_digest(digest.hex(), expected_hex)


def get_admin(conn: sqlite3.Connection) -> Admin | None:
    row = conn.execute(
        "SELECT password_hash, must_change_password FROM admin WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return Admin(password_hash=row[0], must_change_password=bool(row[1]))


def create_admin(
    conn: sqlite3.Connection, *, password: str, must_change_password: bool
) -> None:
    conn.execute(
        "INSERT INTO admin (id, password_hash, must_change_password) VALUES (1, ?, ?)",
        (_hash_password(password), int(must_change_password)),
    )
    conn.commit()


def set_password(
    conn: sqlite3.Connection, password: str, *, must_change_password: bool
) -> None:
    conn.execute(
        "UPDATE admin SET password_hash = ?, must_change_password = ? WHERE id = 1",
        (_hash_password(password), int(must_change_password)),
    )
    conn.commit()
