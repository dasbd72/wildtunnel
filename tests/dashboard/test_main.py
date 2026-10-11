import sqlite3
from pathlib import Path

import pytest

from wildtunnel.dashboard import db
from wildtunnel.dashboard.main import build_app


def test_build_app_bootstraps_admin_and_returns_working_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "dashboard.db"
    secret_file = tmp_path / "admin-password.txt"
    session_secret_file = tmp_path / "session-secret"
    session_secret_file.write_text("test-session-secret\n")
    monkeypatch.setenv("DASHBOARD_DB_PATH", str(db_path))
    monkeypatch.setenv("DASHBOARD_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET_FILE", str(session_secret_file))

    app = build_app()

    assert secret_file.exists()
    conn = sqlite3.connect(str(db_path))
    admin = db.get_admin(conn)
    assert admin is not None
    assert admin.must_change_password is True

    from fastapi.testclient import TestClient

    client = TestClient(app)
    login_page = client.get("/login")
    assert login_page.status_code == 200


def test_build_app_uses_dashboard_initial_admin_password_env_var_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "dashboard.db"
    secret_file = tmp_path / "admin-password.txt"
    session_secret_file = tmp_path / "session-secret"
    session_secret_file.write_text("test-session-secret\n")
    monkeypatch.setenv("DASHBOARD_DB_PATH", str(db_path))
    monkeypatch.setenv("DASHBOARD_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET_FILE", str(session_secret_file))
    monkeypatch.setenv("DASHBOARD_INITIAL_ADMIN_PASSWORD", "deploy-time-password")

    build_app()

    conn = sqlite3.connect(str(db_path))
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, "deploy-time-password") is True


def test_build_app_does_not_rebootstrap_on_second_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "dashboard.db"
    secret_file = tmp_path / "admin-password.txt"
    session_secret_file = tmp_path / "session-secret"
    session_secret_file.write_text("test-session-secret\n")
    monkeypatch.setenv("DASHBOARD_DB_PATH", str(db_path))
    monkeypatch.setenv("DASHBOARD_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET_FILE", str(session_secret_file))

    build_app()
    first_password = secret_file.read_text()
    secret_file.unlink()
    build_app()

    assert not secret_file.exists()
    conn = sqlite3.connect(str(db_path))
    admin = db.get_admin(conn)
    assert admin is not None
    assert db.verify_password(admin, first_password.strip())
