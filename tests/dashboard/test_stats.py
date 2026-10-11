import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from wildtunnel.dashboard.stats import FrpsStatsSource, ProxyStats

# frp's webServer stats API returns one JSON body per proxy type
# (/api/proxy/tcp, /api/proxy/udp, ...), each shaped like:
#   {"proxies": [{"name": ..., "cur_conns": ..., "today_traffic_in": ...,
#                 "today_traffic_out": ...}, ...]}
_TCP_BODY = {
    "proxies": [
        {
            "name": "home-ssh",
            "cur_conns": 2,
            "today_traffic_in": 1024,
            "today_traffic_out": 2048,
        },
        {
            "name": "home-web",
            "cur_conns": 0,
            "today_traffic_in": 512,
            "today_traffic_out": 256,
        },
    ]
}
_EMPTY_BODY: dict[str, list[object]] = {"proxies": []}


class _FakeFrpsStatsServer:
    """A throwaway HTTP server standing in for frps's real webServer API.

    Only used by the narrow test below that exercises
    FrpsStatsSource's HTTP + JSON parsing; the dashboard route tests use
    a fake StatsSource double instead and never touch HTTP at all.
    """

    def __init__(self, *, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._httpd = HTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        expected_auth = self.username, self.password

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                import base64

                header = self.headers.get("Authorization", "")
                ok = False
                if header.startswith("Basic "):
                    decoded = base64.b64decode(header[len("Basic ") :]).decode()
                    user, _, pw = decoded.partition(":")
                    ok = (user, pw) == expected_auth
                if not ok:
                    self.send_response(401)
                    self.end_headers()
                    return
                body = _TCP_BODY if self.path == "/api/proxy/tcp" else _EMPTY_BODY
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        return Handler

    def __enter__(self) -> str:
        self._thread.start()
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host!s}:{port}"

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def test_current_stats_parses_proxies_from_frps_webserver_api() -> None:
    with _FakeFrpsStatsServer(username="admin", password="secret") as base_url:
        source = FrpsStatsSource(base_url=base_url, username="admin", password="secret")

        stats = source.current_stats()

    assert (
        ProxyStats(name="home-ssh", connections=2, bytes_in=1024, bytes_out=2048)
        in stats
    )
    assert (
        ProxyStats(name="home-web", connections=0, bytes_in=512, bytes_out=256) in stats
    )


def test_current_stats_rejects_wrong_credentials() -> None:
    with _FakeFrpsStatsServer(username="admin", password="secret") as base_url:
        source = FrpsStatsSource(base_url=base_url, username="admin", password="wrong")

        stats = source.current_stats()

    assert stats == []
