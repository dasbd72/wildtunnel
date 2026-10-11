import sqlite3
import stat
from pathlib import Path

from wildtunnel.dashboard import db
from wildtunnel.dashboard import reset_password as reset_password_module


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db.init_schema(conn)
    return conn


def test_resetting_invalidates_old_password_and_validates_new_one(
    tmp_path: Path,
) -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    secret_file = tmp_path / "admin-password.txt"

    new_password = reset_password_module.reset_password(conn, secret_file)

    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, new_password) is True
    assert db.verify_password(admin, "correct-horse") is False


def test_resetting_forces_a_password_change_on_next_login(tmp_path: Path) -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    secret_file = tmp_path / "admin-password.txt"

    reset_password_module.reset_password(conn, secret_file)

    admin = db.get_admin(conn)
    assert admin is not None
    assert admin.must_change_password is True


def test_resetting_overwrites_secret_file_with_restrictive_permissions(
    tmp_path: Path,
) -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    secret_file = tmp_path / "admin-password.txt"
    secret_file.write_text("stale-password\n")

    new_password = reset_password_module.reset_password(conn, secret_file)

    assert secret_file.read_text().strip() == new_password
    mode = stat.S_IMODE(secret_file.stat().st_mode)
    assert mode == 0o600


def test_resetting_twice_in_a_row_produces_different_passwords(
    tmp_path: Path,
) -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    secret_file = tmp_path / "admin-password.txt"

    first = reset_password_module.reset_password(conn, secret_file)
    second = reset_password_module.reset_password(conn, secret_file)

    assert first != second
