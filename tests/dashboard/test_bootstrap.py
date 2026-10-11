import sqlite3
import stat
from pathlib import Path

from wildtunnel.dashboard import bootstrap, db


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db.init_schema(conn)
    return conn


def test_bootstraps_admin_with_random_password_when_none_exists(tmp_path: Path) -> None:
    conn = _connect()
    secret_file = tmp_path / "initial-password.txt"

    password = bootstrap.ensure_bootstrapped(conn, secret_file)

    assert password is not None
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, password) is True
    assert admin.must_change_password is True


def test_writes_plaintext_password_to_secret_file_with_restrictive_permissions(
    tmp_path: Path,
) -> None:
    conn = _connect()
    secret_file = tmp_path / "initial-password.txt"

    password = bootstrap.ensure_bootstrapped(conn, secret_file)

    assert secret_file.read_text().strip() == password
    mode = stat.S_IMODE(secret_file.stat().st_mode)
    assert mode == 0o600


def test_does_not_regenerate_password_when_admin_already_exists(tmp_path: Path) -> None:
    conn = _connect()
    db.create_admin(conn, password="already-set", must_change_password=False)
    secret_file = tmp_path / "initial-password.txt"

    result = bootstrap.ensure_bootstrapped(conn, secret_file)

    assert result is None
    assert not secret_file.exists()
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, "already-set") is True
