import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest

from wildtunnel.dashboard import egress
from wildtunnel.dashboard.stats import ProxyStats


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    egress.init_schema(conn)
    return conn


def test_first_sample_of_a_tunnel_establishes_baseline_without_recording_egress() -> (
    None
):
    conn = _connect()

    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 1000)])

    totals = egress.totals_by_tunnel(conn)
    assert totals == [egress.EgressTotals("home-web", 0, 0, 0)]


def test_counter_increase_accumulates_delta_into_todays_rollup() -> None:
    conn = _connect()
    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 1000)])

    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 1500)])
    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 1800)])

    totals = egress.totals_by_tunnel(conn)
    assert totals == [egress.EgressTotals("home-web", 800, 800, 800)]


def test_counter_decrease_is_treated_as_a_new_baseline_not_a_negative_delta() -> None:
    # A counter going backwards looks identical whether it's caused by an
    # frps restart or by frp's own UTC-midnight "today" counter reset -
    # both are handled the same way: the lower reading is itself the
    # delta since whatever reset it, not `current - previous`.
    conn = _connect()
    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 1000)])
    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 1800)])  # +800

    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 50)])  # reset, +50
    egress.record_sample(conn, [ProxyStats("home-web", 0, 0, 30)])  # reset again, +30

    totals = egress.totals_by_tunnel(conn)
    assert totals == [egress.EgressTotals("home-web", 880, 880, 880)]


def test_totals_by_tunnel_sums_day_week_month_windows_across_multiple_days() -> None:
    conn = _connect()
    now = datetime(2026, 10, 11, tzinfo=UTC)
    rows = [
        # home-web: today, 3 days ago (within week+month), 20 days ago
        # (outside week, within month), and 40 days ago (outside both).
        ("home-web", "2026-10-11", 100),
        ("home-web", "2026-10-08", 50),
        ("home-web", "2026-09-21", 20),
        ("home-web", "2026-09-01", 10),
        # home-ssh: only an old sample, outside the month window.
        ("home-ssh", "2026-08-01", 5),
    ]
    conn.executemany(
        "INSERT INTO egress_daily (tunnel_name, day, bytes_out) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()

    totals = egress.totals_by_tunnel(conn, now=now)

    assert totals == [
        egress.EgressTotals("home-ssh", day_bytes=0, week_bytes=0, month_bytes=0),
        egress.EgressTotals("home-web", day_bytes=100, week_bytes=150, month_bytes=170),
    ]


def test_multiple_tunnels_in_one_sample_are_tracked_independently() -> None:
    conn = _connect()
    egress.record_sample(
        conn,
        [
            ProxyStats("home-web", 0, 0, 1000),
            ProxyStats("home-ssh", 0, 0, 200),
        ],
    )

    egress.record_sample(
        conn,
        [
            ProxyStats("home-web", 0, 0, 1300),  # +300
            ProxyStats("home-ssh", 0, 0, 220),  # +20
        ],
    )

    totals = egress.totals_by_tunnel(conn)
    assert totals == [
        egress.EgressTotals("home-ssh", 20, 20, 20),
        egress.EgressTotals("home-web", 300, 300, 300),
    ]


class _FakeStatsSource:
    def __init__(self, stats: list[ProxyStats]) -> None:
        self._stats = stats

    def current_stats(self) -> list[ProxyStats]:
        return self._stats


def test_run_periodically_samples_each_tick_until_cancelled() -> None:
    conn = _connect()
    stats_source = _FakeStatsSource([ProxyStats("home-web", 0, 0, 100)])
    sleep_calls: list[float] = []

    class _Stop(Exception):
        pass

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise _Stop

    async def run() -> None:
        await egress.run_periodically(
            stats_source, conn, interval_seconds=300, sleep=fake_sleep
        )

    with pytest.raises(_Stop):
        asyncio.run(run())

    assert sleep_calls == [300, 300, 300]
    # First tick establishes the baseline (delta=0); the fake StatsSource
    # returns the same unchanging reading on every tick after that, so no
    # further egress should have accumulated.
    totals = egress.totals_by_tunnel(conn)
    assert totals == [egress.EgressTotals("home-web", 0, 0, 0)]
