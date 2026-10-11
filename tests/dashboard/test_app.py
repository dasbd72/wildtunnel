import sqlite3

from fastapi.testclient import TestClient

from wildtunnel.dashboard import db
from wildtunnel.dashboard.app import create_app
from wildtunnel.dashboard.stats import ProxyStats, StatsSource


class _FakeStatsSource:
    def __init__(self, stats: list[ProxyStats]) -> None:
        self._stats = stats

    def current_stats(self) -> list[ProxyStats]:
        return self._stats


def _client_with_admin(
    password: str = "bootstrap-pw",
    must_change_password: bool = True,
    stats_source: StatsSource | None = None,
) -> TestClient:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    db.init_schema(conn)
    db.create_admin(conn, password=password, must_change_password=must_change_password)
    app = create_app(
        conn,
        session_secret="test-secret",
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


def test_tampered_session_cookie_is_rejected() -> None:
    client = _client_with_admin(password="correct-horse", must_change_password=False)
    client.post("/login", data={"password": "correct-horse"})

    client.cookies.set("session", "tampered-value")
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 401
