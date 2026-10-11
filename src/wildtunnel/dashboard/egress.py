from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from wildtunnel.dashboard.stats import ProxyStats, StatsSource

_DAY_FORMAT = "%Y-%m-%d"
_WEEK_WINDOW_DAYS = 7
_MONTH_WINDOW_DAYS = 30


@dataclass(frozen=True)
class EgressTotals:
    tunnel_name: str
    day_bytes: int
    week_bytes: int
    month_bytes: int


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egress_last_seen (
            tunnel_name TEXT PRIMARY KEY,
            bytes_out INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egress_daily (
            tunnel_name TEXT NOT NULL,
            day TEXT NOT NULL,
            bytes_out INTEGER NOT NULL,
            PRIMARY KEY (tunnel_name, day)
        )
        """
    )
    conn.commit()


def _day_str(moment: datetime) -> str:
    return moment.strftime(_DAY_FORMAT)


def record_sample(
    conn: sqlite3.Connection,
    stats: list[ProxyStats],
    *,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(UTC)
    today = _day_str(moment)

    for proxy in stats:
        row = conn.execute(
            "SELECT bytes_out FROM egress_last_seen WHERE tunnel_name = ?",
            (proxy.name,),
        ).fetchone()

        if row is None:
            delta = 0
        else:
            previous = row[0]
            delta = (
                proxy.bytes_out - previous
                if proxy.bytes_out >= previous
                else proxy.bytes_out
            )

        conn.execute(
            """
            INSERT INTO egress_last_seen (tunnel_name, bytes_out) VALUES (?, ?)
            ON CONFLICT(tunnel_name) DO UPDATE SET bytes_out = excluded.bytes_out
            """,
            (proxy.name, proxy.bytes_out),
        )

        if delta:
            conn.execute(
                """
                INSERT INTO egress_daily (tunnel_name, day, bytes_out) VALUES (?, ?, ?)
                ON CONFLICT(tunnel_name, day) DO UPDATE SET
                    bytes_out = egress_daily.bytes_out + excluded.bytes_out
                """,
                (proxy.name, today, delta),
            )

    conn.commit()


def totals_by_tunnel(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> list[EgressTotals]:
    moment = now or datetime.now(UTC)
    today = _day_str(moment)
    week_start = _day_str(moment - timedelta(days=_WEEK_WINDOW_DAYS - 1))
    month_start = _day_str(moment - timedelta(days=_MONTH_WINDOW_DAYS - 1))

    tunnel_names = [
        row[0]
        for row in conn.execute(
            """
            SELECT tunnel_name FROM egress_daily
            UNION
            SELECT tunnel_name FROM egress_last_seen
            ORDER BY tunnel_name
            """
        )
    ]

    totals = []
    for name in tunnel_names:
        day_bytes = conn.execute(
            "SELECT COALESCE(SUM(bytes_out), 0) FROM egress_daily"
            " WHERE tunnel_name = ? AND day = ?",
            (name, today),
        ).fetchone()[0]
        week_bytes = conn.execute(
            "SELECT COALESCE(SUM(bytes_out), 0) FROM egress_daily"
            " WHERE tunnel_name = ? AND day >= ?",
            (name, week_start),
        ).fetchone()[0]
        month_bytes = conn.execute(
            "SELECT COALESCE(SUM(bytes_out), 0) FROM egress_daily"
            " WHERE tunnel_name = ? AND day >= ?",
            (name, month_start),
        ).fetchone()[0]
        totals.append(EgressTotals(name, day_bytes, week_bytes, month_bytes))

    return totals


async def run_periodically(
    stats_source: StatsSource,
    conn: sqlite3.Connection,
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Poll `stats_source` on a fixed cadence, forever, recording each
    snapshot into the rollup tables via `record_sample`.

    Runs until cancelled (the caller is expected to manage the task's
    lifetime, e.g. cancel it on app shutdown). A single failed poll or
    database hiccup is swallowed rather than killing the loop, since a
    transient error shouldn't require a process restart to recover from -
    the next cadence tick just tries again.
    """
    while True:
        try:
            record_sample(conn, stats_source.current_stats())
        except Exception:  # noqa: BLE001, S110 - one bad poll shouldn't end the loop
            pass
        await sleep(interval_seconds)
