from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import queue
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse


class UsageLog:
    """Registo local não bloqueante, repartido por semana ISO."""

    def __init__(self, path: str | Path):
        configured_path = Path(path)
        # Compatibilidade com configurações antigas que indicavam um ficheiro.
        if configured_path.suffix in {".sqlite", ".db"}:
            self.directory = configured_path.parent / f"{configured_path.stem}-logs"
            self.legacy_path = configured_path if configured_path.is_file() else None
        else:
            self.directory = configured_path
            self.legacy_path = None
        self.directory.mkdir(parents=True, exist_ok=True)
        if configured_path.suffix not in {".sqlite", ".db"}:
            self._copy_legacy_database(configured_path.parent / "usage.sqlite")
        self.events: queue.Queue[dict] = queue.Queue(maxsize=10000)
        self._initialize(self._weekly_path())
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
        # Os healthchecks dos contentores não correspondem a utilização humana.
        if parsed.path == "/health" or parsed.path.endswith("/health"):
            return
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
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            if days > 0 else None
        )
        counters = {
            name: Counter() for name in (
                "daily", "searches", "agents", "routes", "status_codes",
                "referrers", "ips", "hours",
            )
        }
        visitors: set[str] = set()
        overview = {"requests": 0, "page_views": 0, "searches": 0}
        api_duration = 0.0
        api_requests = 0
        recent: list[dict] = []

        for database in self._database_paths():
            with self._connect(database) as connection:
                where, params = _usage_filter(cutoff)
                row = connection.execute(
                    f"""
                    SELECT COUNT(*) AS requests,
                           SUM(CASE WHEN route_kind='page' THEN 1 ELSE 0 END) AS page_views,
                           SUM(CASE WHEN search_query IS NOT NULL THEN 1 ELSE 0 END) AS searches,
                           COALESCE(SUM(CASE WHEN path LIKE '/api/%' THEN duration_ms ELSE 0 END),0) AS api_duration,
                           SUM(CASE WHEN path LIKE '/api/%' THEN 1 ELSE 0 END) AS api_requests
                      FROM usage_events {where}
                    """, params,
                ).fetchone()
                for key in ("requests", "page_views", "searches"):
                    overview[key] += int(row[key] or 0)
                api_duration += float(row["api_duration"] or 0)
                api_requests += int(row["api_requests"] or 0)
                visitors.update(
                    str(item[0]) for item in connection.execute(
                        f"SELECT DISTINCT client_ip FROM usage_events {where} AND client_ip IS NOT NULL",
                        params,
                    )
                )
                expressions = {
                    "daily": "substr(created_at,1,10)",
                    "searches": "search_query",
                    "agents": "CASE WHEN user_agent LIKE '%bot%' THEN 'Robô' WHEN user_agent LIKE '%Mobile%' THEN 'Dispositivo móvel' ELSE 'Navegador' END",
                    "routes": "path",
                    "status_codes": "status_code",
                    "referrers": "COALESCE(NULLIF(referrer,''),'Acesso direto')",
                    "ips": "client_ip",
                    "hours": "substr(created_at,12,2)",
                }
                for name, expression in expressions.items():
                    extra = " AND search_query IS NOT NULL" if name == "searches" else ""
                    for value, count in connection.execute(
                        f"SELECT {expression}, COUNT(*) FROM usage_events {where}{extra} GROUP BY 1",
                        params,
                    ):
                        counters[name][value] += int(count)
                recent.extend(
                    dict(item) for item in connection.execute(
                        f"""SELECT created_at, client_ip, route_kind, path,
                                   search_query, status_code, duration_ms
                              FROM usage_events {where}
                             ORDER BY id DESC LIMIT 100""", params,
                    )
                )

        overview["visitors"] = len(visitors)
        overview["average_duration_ms"] = (
            round(api_duration / api_requests, 1) if api_requests else None
        )
        recent.sort(key=lambda item: item["created_at"], reverse=True)
        return {
            "privacy": (
                "Registo técnico local para investigação de utilização; "
                "inclui IP, rota, pesquisa, agente e duração."
            ),
            "overview": overview,
            "daily": _counter_rows(counters["daily"], alphabetical=True),
            "searches": _counter_rows(counters["searches"]),
            "agents": _counter_rows(counters["agents"]),
            "routes": _counter_rows(counters["routes"]),
            "status_codes": _counter_rows(counters["status_codes"]),
            "referrers": _counter_rows(counters["referrers"]),
            "ips": _counter_rows(counters["ips"]),
            "hours": _counter_rows(counters["hours"]),
            "recent": recent[:100],
        }

    def _writer(self) -> None:
        active_path = self._weekly_path()
        connection = self._connect(active_path)
        while True:
            first = self.events.get()
            batch = [first]
            while len(batch) < 250:
                try:
                    batch.append(self.events.get_nowait())
                except queue.Empty:
                    break
            target = self._weekly_path()
            if target != active_path:
                connection.close()
                self._initialize(target)
                connection = self._connect(target)
                active_path = target
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
                """, batch,
            )
            connection.commit()

    def _weekly_path(self) -> Path:
        year, week, _ = datetime.now(timezone.utc).isocalendar()
        return self.directory / f"usage-{year}-W{week:02d}.sqlite"

    def _database_paths(self) -> list[Path]:
        paths = sorted(self.directory.glob("usage-*.sqlite"))
        if self.legacy_path and self.legacy_path not in paths:
            paths.insert(0, self.legacy_path)
        return paths

    def _copy_legacy_database(self, source: Path) -> None:
        """Copia com consistência o log único antigo na primeira execução."""
        target = self.directory / "usage-legacy.sqlite"
        if not source.is_file() or target.exists():
            return
        with sqlite3.connect(source) as old, sqlite3.connect(target) as new:
            old.backup(new)

    def _initialize(self, path: Path) -> None:
        with self._connect(path) as connection:
            connection.executescript(_SCHEMA)

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection


_SCHEMA = """
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
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_search ON usage_events(search_query);
"""


def _usage_filter(cutoff: str | None) -> tuple[str, list[object]]:
    # Exclui também os healthchecks históricos sem eliminar os dados originais.
    where = "WHERE path <> '/health' AND path NOT LIKE '%/health'"
    return (where + " AND created_at >= ?", [cutoff]) if cutoff else (where, [])


def _counter_rows(counter: Counter, *, alphabetical: bool = False) -> list[dict]:
    items = sorted(counter.items()) if alphabetical else counter.most_common(20)
    return [{"value": value, "count": count} for value, count in items[:20]]


def _route_kind(path: str, search_query: str | None) -> str:
    if search_query:
        return "search"
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/assets/") or path.startswith("/entry-images/"):
        return "asset"
    return "page"
