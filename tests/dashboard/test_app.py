import sqlite3

from fastapi.testclient import TestClient

from wildtunnel.dashboard import db
from wildtunnel.dashboard.app import create_app
from wildtunnel.dashboard.mailer import Mailer


class FakeMailer(Mailer):
    """Test double that records what would have been sent — no real SMTP."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


def _client_with_admin(
    password: str = "bootstrap-pw",
    must_change_password: bool = True,
    mailer: Mailer | None = None,
    reset_token_ttl_seconds: float = 3600,
) -> TestClient:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    db.init_schema(conn)
    db.create_admin(conn, password=password, must_change_password=must_change_password)
    app = create_app(
        conn,
        session_secret="test-secret",
        mailer=mailer,
        reset_token_ttl_seconds=reset_token_ttl_seconds,
    )
    return TestClient(app)


def test_login_before_any_admin_exists_fails_with_same_generic_response() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    db.init_schema(conn)
    app = create_app(conn, session_secret="test-secret")
    client = TestClient(app)

    response = client.post("/login", data={"password": "anything"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}


def test_root_rejects_unauthenticated_request() -> None:
    client = _client_with_admin()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 401


def test_login_with_wrong_password_fails_generically() -> None:
    client = _client_with_admin(password="correct-horse")

    response = client.post("/login", data={"password": "wrong"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}


def test_login_with_correct_bootstrap_password_forces_password_change() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=True)

    login = client.post(
        "/login", data={"password": "correct-horse"}, follow_redirects=False
    )
    assert login.status_code in (302, 303)

    root = client.get("/", follow_redirects=False)
    assert root.status_code in (302, 303)
    assert root.headers["location"] == "/change-password"


def test_change_password_clears_forced_flag_and_unlocks_root() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=True)
    client.post("/login", data={"password": "correct-horse"})

    changed = client.post(
        "/change-password", data={"password": "new-password"}, follow_redirects=False
    )
    assert changed.status_code in (302, 303)

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 200


def test_old_password_stops_working_after_change() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=True)
    client.post("/login", data={"password": "correct-horse"})
    client.post("/change-password", data={"password": "new-password"})

    fresh_client = TestClient(client.app)
    response = fresh_client.post("/login", data={"password": "correct-horse"})

    assert response.status_code == 401


def test_session_cookie_persists_login_across_requests() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)

    client.post("/login", data={"password": "correct-horse"})
    first = client.get("/")
    second = client.get("/")

    assert first.status_code == 200
    assert second.status_code == 200


def test_missing_session_cookie_is_rejected_on_a_fresh_client() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    fresh_client = TestClient(client.app)
    response = fresh_client.get("/", follow_redirects=False)

    assert response.status_code == 401


def test_tampered_session_cookie_is_rejected() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    client.cookies.set("session", "tampered-value")
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 401


def test_full_change_password_requires_current_password_when_not_forced() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    response = client.post(
        "/change-password",
        data={"current_password": "wrong", "password": "new-password"},
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_full_change_password_succeeds_with_correct_current_password() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    response = client.post(
        "/change-password",
        data={"current_password": "correct-horse", "password": "new-password"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    fresh_client = TestClient(client.app)
    old_login = fresh_client.post("/login", data={"password": "correct-horse"})
    assert old_login.status_code == 401
    new_login = fresh_client.post(
        "/login", data={"password": "new-password"}, follow_redirects=False
    )
    assert new_login.status_code in (302, 303)


def test_bind_recovery_email_requires_authentication() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)

    response = client.post(
        "/account/email", data={"email": "admin@example.com"}, follow_redirects=False
    )

    assert response.status_code == 401


def test_logged_in_admin_can_bind_recovery_email() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    response = client.post(
        "/account/email", data={"email": "admin@example.com"}, follow_redirects=False
    )

    assert response.status_code in (302, 303)


def test_forgot_password_sends_reset_token_to_bound_email() -> None:
    mailer = FakeMailer()
    client = _client_with_admin(
        password="correct-horse", must_change_password=False, mailer=mailer
    )
    client.post("/login", data={"password": "correct-horse"})
    client.post("/account/email", data={"email": "admin@example.com"})

    fresh_client = TestClient(client.app)
    response = fresh_client.post(
        "/forgot-password", data={"email": "admin@example.com"}
    )

    assert response.status_code == 200
    assert len(mailer.sent) == 1
    to, _subject, body = mailer.sent[0]
    assert to == "admin@example.com"
    assert body  # contains the reset token/link


def test_forgot_password_with_unbound_email_sends_nothing_but_still_succeeds() -> None:
    mailer = FakeMailer()
    client = _client_with_admin(
        password="correct-horse", must_change_password=False, mailer=mailer
    )

    response = client.post("/forgot-password", data={"email": "nobody@example.com"})

    assert response.status_code == 200
    assert mailer.sent == []


def test_reset_password_with_valid_token_sets_new_password() -> None:
    mailer = FakeMailer()
    client = _client_with_admin(
        password="correct-horse", must_change_password=False, mailer=mailer
    )
    client.post("/login", data={"password": "correct-horse"})
    client.post("/account/email", data={"email": "admin@example.com"})
    client.post("/forgot-password", data={"email": "admin@example.com"})
    _to, _subject, body = mailer.sent[0]
    token = body.strip().rsplit(" ", 1)[-1]

    response = client.post(
        "/reset-password",
        data={"token": token, "password": "reset-password-123"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    fresh_client = TestClient(client.app)
    old_login = fresh_client.post("/login", data={"password": "correct-horse"})
    assert old_login.status_code == 401
    new_login = fresh_client.post(
        "/login", data={"password": "reset-password-123"}, follow_redirects=False
    )
    assert new_login.status_code in (302, 303)


def test_reset_password_rejects_already_used_token() -> None:
    mailer = FakeMailer()
    client = _client_with_admin(
        password="correct-horse", must_change_password=False, mailer=mailer
    )
    client.post("/login", data={"password": "correct-horse"})
    client.post("/account/email", data={"email": "admin@example.com"})
    client.post("/forgot-password", data={"email": "admin@example.com"})
    _to, _subject, body = mailer.sent[0]
    token = body.strip().rsplit(" ", 1)[-1]
    client.post("/reset-password", data={"token": token, "password": "first-reset"})

    response = client.post(
        "/reset-password", data={"token": token, "password": "second-reset"}
    )

    assert response.status_code == 400


def test_reset_password_rejects_expired_token() -> None:
    mailer = FakeMailer()
    client = _client_with_admin(
        password="correct-horse",
        must_change_password=False,
        mailer=mailer,
        reset_token_ttl_seconds=-1,
    )
    client.post("/login", data={"password": "correct-horse"})
    client.post("/account/email", data={"email": "admin@example.com"})
    client.post("/forgot-password", data={"email": "admin@example.com"})
    _to, _subject, body = mailer.sent[0]
    token = body.strip().rsplit(" ", 1)[-1]

    response = client.post(
        "/reset-password", data={"token": token, "password": "new-password"}
    )

    assert response.status_code == 400
