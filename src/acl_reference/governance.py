from __future__ import annotations

from pathlib import Path

from .editorial_db import connect, initialize, transaction
from .labels import DOMAINS, GRAMMAR, STATUSES


CATALOGUES = {
    "grammar": GRAMMAR,
    "domain": DOMAINS,
    "editorial_status": STATUSES,
}


def synchronize_controlled_values(db_path: str | Path) -> None:
    """Materializa os valores usados no corpus e a sua classificação inicial."""
    initialize(db_path)
    with transaction(db_path) as connection:
        run = connection.execute(
            "SELECT id FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            return
        sources = {
            "grammar": (
                """
                SELECT grammatical_info AS value, COUNT(*) AS usage_count
                  FROM entries WHERE import_run_id=?
                   AND NULLIF(TRIM(grammatical_info),'') IS NOT NULL
                 GROUP BY grammatical_info
                """,
                (run["id"],),
            ),
            "domain": (
                """
                SELECT labels.value, COUNT(*) AS usage_count FROM labels
                  JOIN entries ON entries.id=labels.entry_id
                 WHERE entries.import_run_id=? AND labels.label_type IN ('domain','dom')
                 GROUP BY labels.value
                """,
                (run["id"],),
            ),
            "editorial_status": (
                """
                SELECT editorial_status AS value, COUNT(*) AS usage_count
                  FROM entries WHERE import_run_id=?
                   AND NULLIF(TRIM(editorial_status),'') IS NOT NULL
                 GROUP BY editorial_status
                """,
                (run["id"],),
            ),
        }
        for category, (query, params) in sources.items():
            catalogue = CATALOGUES[category]
            for row in connection.execute(query, params):
                value = row["value"]
                label = catalogue.get(value)
                default_status = "authorized" if label else "unmapped"
                connection.execute(
                    """
                    INSERT INTO controlled_values(
                        category, value, display_label, governance_status, usage_count
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(category, value) DO UPDATE SET
                        usage_count=excluded.usage_count,
                        display_label=COALESCE(controlled_values.display_label,
                                               excluded.display_label)
                    """,
                    (category, value, label, default_status, row["usage_count"]),
                )


class GovernanceService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        initialize(self.db_path)

    def users(self) -> list[dict]:
        with connect(self.db_path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT username, display_name, role FROM editorial_users
                     WHERE active=1 ORDER BY role, display_name
                    """
                )
            ]

    def require_user(self, username: str, roles: set[str] | None = None) -> dict:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT username, display_name, role FROM editorial_users
                 WHERE username=? AND active=1
                """,
                (username,),
            ).fetchone()
        if row is None:
            raise ValueError("Utilizador editorial desconhecido ou inativo.")
        value = dict(row)
        if roles and value["role"] not in roles:
            raise PermissionError(
                "O utilizador selecionado não tem permissão para esta operação."
            )
        return value

    def list_values(
        self, category: str | None = None, governance_status: str | None = None
    ) -> list[dict]:
        synchronize_controlled_values(self.db_path)
        clauses, params = [], []
        if category:
            if category not in CATALOGUES:
                raise ValueError("Categoria de lista controlada inválida.")
            clauses.append("category=?")
            params.append(category)
        if governance_status:
            if governance_status not in {"authorized", "obsolete", "unmapped"}:
                raise ValueError("Estado de governação inválido.")
            clauses.append("governance_status=?")
            params.append(governance_status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with connect(self.db_path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT * FROM controlled_values{where}
                     ORDER BY category, value COLLATE NOCASE
                    """,
                    params,
                )
            ]

    def update_value(self, value_id: int, payload: dict) -> dict:
        username = str(payload.get("actor") or "").strip()
        self.require_user(username, {"reviewer", "approver", "administrator"})
        status = str(payload.get("governance_status") or "").strip()
        if status not in {"authorized", "obsolete", "unmapped"}:
            raise ValueError("Estado de governação inválido.")
        replacement = str(payload.get("replacement_value") or "").strip() or None
        label = str(payload.get("display_label") or "").strip() or None
        with transaction(self.db_path) as connection:
            current = connection.execute(
                "SELECT * FROM controlled_values WHERE id=?", (value_id,)
            ).fetchone()
            if current is None:
                raise ValueError("Valor controlado inexistente.")
            if status == "obsolete" and not replacement:
                raise ValueError("Um valor obsoleto deve indicar a substituição proposta.")
            connection.execute(
                """
                UPDATE controlled_values
                   SET governance_status=?, replacement_value=?, display_label=?,
                       updated_by=?, updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
                """,
                (status, replacement, label, username, value_id),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_type, actor, previous_state, resulting_state,
                    comment, details_json
                ) VALUES ('CONTROLLED_VALUE', ?, ?, ?, ?, json_object(
                    'category', ?, 'value', ?, 'replacement', ?
                ))
                """,
                (
                    username,
                    current["governance_status"],
                    status,
                    str(payload.get("comment") or "Atualização de lista controlada"),
                    current["category"],
                    current["value"],
                    replacement,
                ),
            )
        return next(item for item in self.list_values() if item["id"] == value_id)
