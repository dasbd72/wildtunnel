from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16


@dataclass(frozen=True)
class Admin:
    password_hash: str
    must_change_password: bool
    email: str | None = None
    reset_token_hash: str | None = None
    reset_token_expires_at: float | None = None


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            password_hash TEXT NOT NULL,
            must_change_password INTEGER NOT NULL,
            email TEXT,
            reset_token_hash TEXT,
            reset_token_expires_at REAL
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
        "SELECT password_hash, must_change_password, email,"
        " reset_token_hash, reset_token_expires_at FROM admin WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return Admin(
        password_hash=row[0],
        must_change_password=bool(row[1]),
        email=row[2],
        reset_token_hash=row[3],
        reset_token_expires_at=row[4],
    )


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


def set_email(conn: sqlite3.Connection, email: str) -> None:
    conn.execute("UPDATE admin SET email = ? WHERE id = 1", (email,))
    conn.commit()


def _hash_token(token: str) -> str:
    # Reset tokens are high-entropy random values, not user-chosen secrets,
    # so a plain fast hash (unlike the slow PBKDF2 used for passwords) is
    # enough to avoid storing the plaintext token at rest.
    return hashlib.sha256(token.encode()).hexdigest()


def create_password_reset_token(
    conn: sqlite3.Connection, token: str, *, ttl_seconds: float
) -> None:
    """Store a hash of the given reset token, replacing any prior one.

    `ttl_seconds` may be negative in tests to produce an already-expired
    token without needing to sleep.
    """
    expires_at = time.time() + ttl_seconds
    conn.execute(
        "UPDATE admin SET reset_token_hash = ?, reset_token_expires_at = ?"
        " WHERE id = 1",
        (_hash_token(token), expires_at),
    )
    conn.commit()


def reset_password_with_token(
    conn: sqlite3.Connection, token: str, new_password: str
) -> bool:
    """Validate a reset token and, if valid, set the new password.

    Returns False (without changing anything) when there is no pending
    token, the token doesn't match, or it has expired. The stored token is
    always a single use: a successful reset clears it, so replaying the
    same token afterwards returns False too.
    """
    admin = get_admin(conn)
    if admin is None or admin.reset_token_hash is None:
        return False
    if not hmac.compare_digest(_hash_token(token), admin.reset_token_hash):
        return False
    if (
        admin.reset_token_expires_at is None
        or time.time() > admin.reset_token_expires_at
    ):
        return False

    conn.execute(
        "UPDATE admin SET password_hash = ?, must_change_password = 0,"
        " reset_token_hash = NULL, reset_token_expires_at = NULL WHERE id = 1",
        (_hash_password(new_password),),
    )
    conn.commit()
    return True
