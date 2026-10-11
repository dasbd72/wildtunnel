import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from wildtunnel.dashboard import db, egress
from wildtunnel.dashboard.app import create_app
from wildtunnel.dashboard.mailer import Mailer
from wildtunnel.dashboard.stats import ProxyStats, StatsSource


class FakeMailer(Mailer):
    """Test double that records what would have been sent — no real SMTP."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


class _FakeStatsSource:
    def __init__(self, stats: list[ProxyStats]) -> None:
        self._stats = stats

    def current_stats(self) -> list[ProxyStats]:
        return self._stats


def _client_with_admin(
    password: str = "bootstrap-pw",
    must_change_password: bool = True,
    mailer: Mailer | None = None,
    reset_token_ttl_seconds: float = 3600,
    stats_source: StatsSource | None = None,
) -> TestClient:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    db.init_schema(conn)
    db.create_admin(conn, password=password, must_change_password=must_change_password)
    app = create_app(
        conn,
        session_secret="test-secret",
        mailer=mailer,
        reset_token_ttl_seconds=reset_token_ttl_seconds,
        stats_source=stats_source or _FakeStatsSource([]),
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


def test_login_page_has_a_mobile_viewport_meta_tag() -> None:
    client = _client_with_admin()

    response = client.get("/login")

    assert response.status_code == 200
    assert "<meta name='viewport' content='width=device-width, initial-scale=1'>" in (
        response.text
    )


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


def test_stats_rejects_unauthenticated_request() -> None:
    client = _client_with_admin()

    response = client.get("/stats", follow_redirects=False)

    assert response.status_code == 401


def test_stats_shows_per_tunnel_connections_and_bandwidth_when_authenticated() -> None:
    stats_source = _FakeStatsSource(
        [ProxyStats(name="home-ssh", connections=3, bytes_in=1024, bytes_out=2048)]
    )
    client = _client_with_admin(
        password="correct-horse",
        must_change_password=False,
        stats_source=stats_source,
    )
    client.post("/login", data={"password": "correct-horse"})

    response = client.get("/stats")

    assert response.status_code == 200
    assert "home-ssh" in response.text
    assert "3" in response.text
    assert "1024" in response.text
    assert "2048" in response.text


def test_egress_rejects_unauthenticated_request() -> None:
    client = _client_with_admin()

    response = client.get("/egress", follow_redirects=False)

    assert response.status_code == 401


def test_egress_shows_day_week_month_totals_per_tunnel_when_authenticated() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})
    conn = client.app.state.conn  # type: ignore[attr-defined]
    egress.init_schema(conn)
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    within_week = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    outside_week_within_month = (now - timedelta(days=20)).strftime("%Y-%m-%d")
    outside_month = (now - timedelta(days=45)).strftime("%Y-%m-%d")
    conn.executemany(
        "INSERT INTO egress_daily (tunnel_name, day, bytes_out) VALUES (?, ?, ?)",
        [
            ("home-ssh", today, 100),
            ("home-ssh", within_week, 50),
            ("home-ssh", outside_week_within_month, 10),
            ("home-ssh", outside_month, 999),
        ],
    )
    conn.commit()

    response = client.get("/egress")

    assert response.status_code == 200
    assert "home-ssh" in response.text
    assert "100" in response.text
    assert "150" in response.text
    assert "160" in response.text
    assert "999" not in response.text


def test_egress_sampler_task_is_scheduled_when_interval_configured() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    db.init_schema(conn)
    db.create_admin(conn, password="bootstrap-pw", must_change_password=True)
    app = create_app(
        conn,
        session_secret="test-secret",
        stats_source=_FakeStatsSource([]),
        egress_sample_interval_seconds=300,
    )

    with TestClient(app):
        assert app.state.egress_sampler_task is not None
        assert not app.state.egress_sampler_task.done()

    assert app.state.egress_sampler_task.cancelled()


def test_no_egress_sampler_task_when_interval_not_configured() -> None:
    client = _client_with_admin()

    with TestClient(client.app):
        assert client.app.state.egress_sampler_task is None  # type: ignore[attr-defined]


def test_tampered_session_cookie_is_rejected() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    client.cookies.set("session", "tampered-value")
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 401


def test_change_password_page_includes_current_password_field_when_not_forced() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    response = client.get("/change-password")

    assert response.status_code == 200
    assert "name='current_password'" in response.text


def test_change_password_page_omits_current_password_field_when_forced() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=True)
    client.post("/login", data={"password": "correct-horse"})

    response = client.get("/change-password")

    assert response.status_code == 200
    assert "name='current_password'" not in response.text


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


def test_account_email_get_and_post_are_gated_behind_forced_password_change() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=True)
    client.post("/login", data={"password": "correct-horse"})

    get_response = client.get("/account/email", follow_redirects=False)
    assert get_response.status_code in (302, 303)
    assert get_response.headers["location"] == "/change-password"

    post_response = client.post(
        "/account/email", data={"email": "admin@example.com"}, follow_redirects=False
    )
    assert post_response.status_code in (302, 303)
    assert post_response.headers["location"] == "/change-password"


def test_account_email_is_reachable_once_password_has_been_changed() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=True)
    client.post("/login", data={"password": "correct-horse"})
    client.post("/change-password", data={"password": "new-password"})

    response = client.post(
        "/account/email", data={"email": "admin@example.com"}, follow_redirects=False
    )

    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/"


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


def test_root_renders_links_to_other_pages_once_unlocked() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    response = client.get("/")

    assert response.status_code == 200
    assert "/stats" in response.text
    assert "/egress" in response.text
    assert "/account/email" in response.text
    assert "/logout" in response.text


def test_logout_clears_session_so_subsequent_requests_are_unauthenticated() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code in (302, 303)
    assert logout.headers["location"] == "/login"

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 401


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
