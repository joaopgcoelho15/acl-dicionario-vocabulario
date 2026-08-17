from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import queue
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse


class UsageLog:
    """Non-blocking local usage log with a single background SQLite writer."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: queue.Queue[dict] = queue.Queue(maxsize=10000)
        self._initialize()
        threading.Thread(
            target=self._writer, name="acl-usage-log", daemon=True
        ).start()

    def record(
        self,
        *,
        client_ip: str,
        method: str,
        raw_path: str,
        status_code: int,
        duration_ms: float,
        user_agent: str | None,
        referrer: str | None,
    ) -> None:
        parsed = urlparse(raw_path)
        params = parse_qs(parsed.query)
        search_query = (
            params.get("q", params.get("query", [""]))[0].strip() or None
        )
        event = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "client_ip": client_ip,
            "method": method,
            "path": parsed.path,
            "route_kind": _route_kind(parsed.path, search_query),
            "search_query": search_query,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "user_agent": user_agent,
            "referrer": referrer,
        }
        try:
            self.events.put_nowait(event)
        except queue.Full:
            pass

    def dashboard(self, days: int) -> dict:
        where = ""
        params: list[object] = []
        if days > 0:
            where = "WHERE created_at >= ?"
            params.append(
                (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            )
        with self._connect() as connection:
            overview = dict(
                connection.execute(
                    f"""
                    SELECT COUNT(*) AS requests,
                           COUNT(DISTINCT client_ip) AS visitors,
                           SUM(CASE WHEN route_kind='page' THEN 1 ELSE 0 END)
                               AS page_views,
                           SUM(CASE WHEN search_query IS NOT NULL THEN 1 ELSE 0 END)
                               AS searches,
                           ROUND(AVG(
                               CASE WHEN path LIKE '/api/%' THEN duration_ms END
                           ), 1) AS average_duration_ms
                      FROM usage_events {where}
                    """,
                    params,
                ).fetchone()
            )
            return {
                "privacy": (
                    "Registo técnico local para investigação de utilização; "
                    "inclui IP, rota, pesquisa, agente e duração."
                ),
                "overview": overview,
                "daily": self._rows(
                    connection,
                    f"""
                    SELECT substr(created_at,1,10) AS date, COUNT(*) AS requests
                      FROM usage_events {where}
                     GROUP BY date ORDER BY date
                    """,
                    params,
                ),
                "searches": self._top(
                    connection, "search_query", where, params,
                    extra="AND search_query IS NOT NULL" if where else "WHERE search_query IS NOT NULL",
                ),
                "agents": self._top_expression(
                    connection,
                    """
                    CASE
                      WHEN user_agent LIKE '%bot%' THEN 'Robô'
                      WHEN user_agent LIKE '%Mobile%' THEN 'Dispositivo móvel'
                      ELSE 'Navegador'
                    END
                    """,
                    where,
                    params,
                ),
                "routes": self._top(connection, "path", where, params),
                "status_codes": self._top(
                    connection, "status_code", where, params
                ),
                "referrers": self._top(
                    connection,
                    "COALESCE(NULLIF(referrer,''),'Acesso direto')",
                    where,
                    params,
                ),
                "ips": self._top(connection, "client_ip", where, params),
                "hours": self._top(
                    connection, "substr(created_at,12,2)", where, params
                ),
                "recent": self._rows(
                    connection,
                    f"""
                    SELECT created_at, client_ip, route_kind, path,
                           search_query, status_code, duration_ms
                      FROM usage_events {where}
                     ORDER BY id DESC LIMIT 100
                    """,
                    params,
                ),
            }

    def _writer(self) -> None:
        connection = self._connect()
        while True:
            first = self.events.get()
            batch = [first]
            while len(batch) < 250:
                try:
                    batch.append(self.events.get_nowait())
                except queue.Empty:
                    break
            connection.executemany(
                """
                INSERT INTO usage_events(
                    created_at, client_ip, method, path, route_kind,
                    search_query, status_code, duration_ms, user_agent, referrer
                ) VALUES (
                    :created_at, :client_ip, :method, :path, :route_kind,
                    :search_query, :status_code, :duration_ms, :user_agent,
                    :referrer
                )
                """,
                batch,
            )
            connection.commit()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    client_ip TEXT,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    route_kind TEXT NOT NULL,
                    search_query TEXT,
                    status_code INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    user_agent TEXT,
                    referrer TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_usage_created
                    ON usage_events(created_at);
                CREATE INDEX IF NOT EXISTS idx_usage_search
                    ON usage_events(search_query);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @staticmethod
    def _rows(connection, sql: str, params: list[object]) -> list[dict]:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def _top(
        self,
        connection,
        expression: str,
        where: str,
        params: list[object],
        *,
        extra: str = "",
    ) -> list[dict]:
        return self._top_expression(
            connection, expression, where, params, extra=extra
        )

    def _top_expression(
        self,
        connection,
        expression: str,
        where: str,
        params: list[object],
        *,
        extra: str = "",
    ) -> list[dict]:
        sql = f"""
            SELECT {expression} AS value, COUNT(*) AS count
              FROM usage_events {where} {extra}
             GROUP BY value ORDER BY count DESC, value LIMIT 20
        """
        return self._rows(connection, sql, params)


def _route_kind(path: str, search_query: str | None) -> str:
    if search_query:
        return "search"
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/assets/") or path.startswith("/entry-images/"):
        return "asset"
    return "page"
