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


def test_admin_has_no_email_by_default() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=True)

    admin = db.get_admin(conn)
    assert admin is not None
    assert admin.email is None


def test_set_email_binds_recovery_address() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=True)

    db.set_email(conn, "admin@example.com")

    admin = db.get_admin(conn)
    assert admin is not None
    assert admin.email == "admin@example.com"


def test_create_password_reset_token_round_trips_through_reset() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    db.set_email(conn, "admin@example.com")

    db.create_password_reset_token(conn, "reset-token", ttl_seconds=3600)
    ok = db.reset_password_with_token(conn, "reset-token", "new-password")

    assert ok is True
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, "new-password") is True
    assert db.verify_password(admin, "correct-horse") is False


def test_reset_password_rejects_wrong_token() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    db.set_email(conn, "admin@example.com")
    db.create_password_reset_token(conn, "reset-token", ttl_seconds=3600)

    ok = db.reset_password_with_token(conn, "wrong-token", "new-password")

    assert ok is False
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, "correct-horse") is True


def test_reset_password_rejects_expired_token() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    db.set_email(conn, "admin@example.com")
    db.create_password_reset_token(conn, "reset-token", ttl_seconds=-1)

    ok = db.reset_password_with_token(conn, "reset-token", "new-password")

    assert ok is False
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, "correct-horse") is True


def test_reset_password_rejects_already_used_token() -> None:
    conn = _connect()
    db.create_admin(conn, password="correct-horse", must_change_password=False)
    db.set_email(conn, "admin@example.com")
    db.create_password_reset_token(conn, "reset-token", ttl_seconds=3600)
    first = db.reset_password_with_token(conn, "reset-token", "new-password")
    assert first is True

    second = db.reset_password_with_token(conn, "reset-token", "another-password")

    assert second is False
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, "new-password") is True
