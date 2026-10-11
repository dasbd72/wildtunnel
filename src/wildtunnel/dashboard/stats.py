from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

# frp's webServer stats API exposes per-proxy-type endpoints rather than
# one combined list; these are the proxy types wildtunnel tunnels can use.
_PROXY_TYPES = ("tcp", "udp", "http", "https", "stcp", "xtcp")

_REQUEST_TIMEOUT_SECS = 3


@dataclass(frozen=True)
class ProxyStats:
    """Current snapshot of one frp proxy (tunnel), as reported by frps.

    ``bytes_in``/``bytes_out`` are frp's "today" traffic counters (reset
    daily by frps itself), not a running historical total - good enough
    for a live/current-snapshot view. Accumulating history across resets
    is a separate, later concern (the egress rollup ticket).
    """

    name: str
    connections: int
    bytes_in: int
    bytes_out: int


class StatsSource(Protocol):
    def current_stats(self) -> list[ProxyStats]: ...


class FrpsStatsSource:
    """Polls frps's built-in webServer stats API over HTTP.

    frps must be configured with a ``webServer`` block (see
    ``server-setup.sh``) exposing ``/api/proxy/<type>`` endpoints guarded
    by HTTP basic auth. This is meant to be polled locally (127.0.0.1) -
    it is not internet-facing.
    """

    def __init__(self, *, base_url: str, username: str, password: str) -> None:
        self._base_url = base_url.rstrip("/")
        credentials = f"{username}:{password}".encode()
        self._auth_header = "Basic " + base64.b64encode(credentials).decode()

    def current_stats(self) -> list[ProxyStats]:
        stats: list[ProxyStats] = []
        for proxy_type in _PROXY_TYPES:
            stats.extend(self._fetch_proxy_type(proxy_type))
        return stats

    def _fetch_proxy_type(self, proxy_type: str) -> list[ProxyStats]:
        request = urllib.request.Request(
            f"{self._base_url}/api/proxy/{proxy_type}",
            headers={"Authorization": self._auth_header},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=_REQUEST_TIMEOUT_SECS
            ) as response:
                body = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return []

        return [
            ProxyStats(
                name=proxy["name"],
                connections=proxy.get("cur_conns", 0),
                bytes_in=proxy.get("today_traffic_in", 0),
                bytes_out=proxy.get("today_traffic_out", 0),
            )
            for proxy in body.get("proxies", [])
        ]
