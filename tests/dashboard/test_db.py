import sqlite3

from wildtunnel.dashboard import db


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db.init_schema(conn)
    return conn


def test_no_admin_initially() -> None:
    conn = _connect()

    assert db.get_admin(conn) is None


def test_create_admin_stores_hash_not_plaintext() -> None:
    conn = _connect()

    db.create_admin(conn, password="correct-horse", must_change_password=True)

    admin = db.get_admin(conn)
    assert admin is not None
    assert "correct-horse" not in admin.password_hash


def test_verify_password_accepts_correct_password() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=True)
    admin = db.get_admin(conn)
    assert admin is not None

    assert db.verify_password(admin, "correct-horse") is True


def test_verify_password_rejects_wrong_password() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=True)
    admin = db.get_admin(conn)
    assert admin is not None

    assert db.verify_password(admin, "wrong-password") is False


def test_created_admin_must_change_password_flag_is_honored() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=True)

    admin = db.get_admin(conn)
    assert admin is not None
    assert admin.must_change_password is True


def test_set_password_updates_hash_and_clears_must_change_flag() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=True)

    db.set_password(conn, "battery-staple", must_change_password=False)

    admin = db.get_admin(conn)
    assert admin is not None
    assert admin.must_change_password is False
    assert db.verify_password(admin, "battery-staple") is True
    assert db.verify_password(admin, "correct-horse") is False
