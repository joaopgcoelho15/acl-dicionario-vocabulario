from __future__ import annotations

import sqlite3
import time

from acl_reference.usage_log import UsageLog


def _wait_for_rows(database, expected: int) -> None:
    for _ in range(100):
        with sqlite3.connect(database) as connection:
            count = connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        if count == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"Esperavam-se {expected} registos; existem {count}.")


def test_healthcheck_is_not_recorded_and_weekly_database_is_used(tmp_path):
    log = UsageLog(tmp_path / "usage-logs")
    log.record(
        client_ip="127.0.0.1", method="GET", raw_path="/health",
        status_code=200, duration_ms=1, user_agent="healthcheck", referrer=None,
    )
    log.record(
        client_ip="192.0.2.10", method="GET", raw_path="/?q=casa",
        status_code=200, duration_ms=2, user_agent="browser", referrer=None,
    )
    databases = list((tmp_path / "usage-logs").glob("usage-*.sqlite"))
    assert len(databases) == 1
    _wait_for_rows(databases[0], 1)
    with sqlite3.connect(databases[0]) as connection:
        assert connection.execute(
            "SELECT path, search_query FROM usage_events"
        ).fetchone() == ("/", "casa")


def test_dashboard_combines_weekly_files_and_hides_old_healthchecks(tmp_path):
    directory = tmp_path / "usage-logs"
    log = UsageLog(directory)
    second = directory / "usage-2025-W01.sqlite"
    log._initialize(second)
    with sqlite3.connect(second) as connection:
        connection.executemany(
            """INSERT INTO usage_events(
                created_at, client_ip, method, path, route_kind, search_query,
                status_code, duration_ms, user_agent, referrer
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                ("2025-01-01T00:00:00+00:00", "127.0.0.1", "GET", "/health", "page", None, 200, 1, "healthcheck", None),
                ("2025-01-01T00:01:00+00:00", "192.0.2.20", "GET", "/", "search", "cavalo", 200, 3, "browser", None),
            ],
        )
    dashboard = log.dashboard(0)
    assert dashboard["overview"]["requests"] == 1
    assert dashboard["overview"]["visitors"] == 1
    assert dashboard["searches"] == [{"value": "cavalo", "count": 1}]
