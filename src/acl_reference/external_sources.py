from __future__ import annotations

import json
from pathlib import Path

from .editorial_db import connect, initialize, transaction
from .governance import GovernanceService


def source_publication_status(db_path: str | Path) -> list[dict]:
    initialize(db_path)
    with connect(db_path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT external_sources.*,
                       (SELECT COUNT(*) FROM enrichments
                         WHERE source_code=external_sources.code) AS matched,
                       (SELECT COUNT(*) FROM unmatched_enrichments
                         WHERE source_code=external_sources.code) AS unmatched
                  FROM external_sources ORDER BY code
                """
            )
        ]


def set_source_publication(
    db_path: str | Path,
    code: str,
    *,
    enabled: bool,
    actor: str,
    comment: str = "",
) -> dict:
    GovernanceService(db_path).require_user(
        actor, {"approver", "administrator"}
    )
    with transaction(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM external_sources WHERE code=?", (code,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Fonte externa inexistente: {code}")
        previous = bool(row["publication_enabled"])
        connection.execute(
            "UPDATE external_sources SET publication_enabled=? WHERE code=?",
            (int(enabled), code),
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                event_type, actor, previous_state, resulting_state,
                comment, details_json
            ) VALUES ('EXTERNAL_SOURCE_PUBLICATION', ?, ?, ?, ?, ?)
            """,
            (
                actor,
                "enabled" if previous else "deferred",
                "enabled" if enabled else "deferred",
                comment or None,
                json.dumps({"source_code": code}, ensure_ascii=False),
            ),
        )
    return next(
        item for item in source_publication_status(db_path) if item["code"] == code
    )
