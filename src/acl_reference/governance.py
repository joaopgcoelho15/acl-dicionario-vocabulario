from __future__ import annotations

from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

from .editorial_db import connect, initialize, transaction
from .labels import DOMAINS, GRAMMAR, STATUSES
from .normalization import search_key


CATALOGUES = {
    "grammar": GRAMMAR,
    "domain": DOMAINS,
    "editorial_status": STATUSES,
}
TEI = "http://www.tei-c.org/ns/1.0"


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


def refresh_controlled_values(
    db_path: str | Path, touched: dict[str, set[str | None]]
) -> None:
    """Atualiza apenas contagens alteradas por uma edição individual."""
    with transaction(db_path) as connection:
        run = connection.execute(
            "SELECT id FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            return
        for category, values in touched.items():
            if category not in CATALOGUES:
                continue
            for value in {item for item in values if item}:
                if category == "grammar":
                    count = connection.execute(
                        "SELECT COUNT(*) FROM entries WHERE import_run_id=? AND grammatical_info=?",
                        (run["id"], value),
                    ).fetchone()[0]
                elif category == "editorial_status":
                    count = connection.execute(
                        "SELECT COUNT(*) FROM entries WHERE import_run_id=? AND editorial_status=?",
                        (run["id"], value),
                    ).fetchone()[0]
                else:
                    count = connection.execute(
                        """
                        SELECT COUNT(DISTINCT labels.entry_id) FROM labels
                        JOIN entries ON entries.id=labels.entry_id
                        WHERE entries.import_run_id=? AND labels.label_type IN ('domain','dom')
                          AND labels.value=?
                        """,
                        (run["id"], value),
                    ).fetchone()[0]
                label = CATALOGUES[category].get(value)
                connection.execute(
                    """
                    INSERT INTO controlled_values(
                        category,value,display_label,governance_status,usage_count
                    ) VALUES(?,?,?,?,?) ON CONFLICT(category,value) DO UPDATE SET
                        usage_count=excluded.usage_count,
                        display_label=COALESCE(controlled_values.display_label,excluded.display_label)
                    """,
                    (category, value, label, "authorized" if label else "unmapped", count),
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
                     WHERE active=1 ORDER BY CASE role
                       WHEN 'editor' THEN 1 WHEN 'reviewer' THEN 2
                       WHEN 'approver' THEN 3 WHEN 'administrator' THEN 4 END,
                       display_name
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
        self, category: str | None = None, governance_status: str | None = None,
        sort: str = "alphabetical", direction: str = "asc",
    ) -> list[dict]:
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
        if sort not in {"alphabetical", "usage", "custom"}:
            raise ValueError("Ordenação de lista controlada inválida.")
        if direction not in {"asc", "desc"}:
            raise ValueError("Direção de ordenação inválida.")
        order = {
            "alphabetical": "value COLLATE NOCASE",
            "usage": "usage_count",
            "custom": "sort_order",
        }[sort]
        with connect(self.db_path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT * FROM controlled_values{where}
                     ORDER BY category, {order} {direction.upper()}, value COLLATE NOCASE
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
        new_value = str(payload.get("value") or "").strip()
        with transaction(self.db_path) as connection:
            current = connection.execute(
                "SELECT * FROM controlled_values WHERE id=?", (value_id,)
            ).fetchone()
            if current is None:
                raise ValueError("Valor controlado inexistente.")
            new_value = new_value or current["value"]
            duplicate = connection.execute(
                "SELECT id FROM controlled_values WHERE category=? AND value=? AND id<>?",
                (current["category"], new_value, value_id),
            ).fetchone()
            if duplicate:
                raise ValueError(
                    "Já existe um valor igual. Confirme o merge na interface para unir os dois valores."
                )
            if status == "obsolete" and not replacement:
                raise ValueError("Um valor obsoleto deve indicar a substituição proposta.")
            connection.execute(
                """
                UPDATE controlled_values
                   SET value=?, governance_status=?, replacement_value=?, display_label=?,
                       updated_by=?, updated_at=CURRENT_TIMESTAMP
                 WHERE id=?
                """,
                (new_value, status, replacement, label, username, value_id),
            )
            if new_value != current["value"]:
                self._replace_occurrences(
                    connection, current["category"], current["value"], new_value,
                    actor=username,
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
                    new_value,
                    replacement,
                ),
            )
        return next(item for item in self.list_values() if item["id"] == value_id)

    def create_value(self, payload: dict) -> dict:
        actor = str(payload.get("actor") or "").strip()
        self.require_user(actor, {"reviewer", "approver", "administrator"})
        category = str(payload.get("category") or "").strip()
        value = str(payload.get("value") or "").strip()
        if category not in CATALOGUES or not value:
            raise ValueError("Indique uma categoria e um valor válidos.")
        label = str(payload.get("display_label") or "").strip() or None
        with transaction(self.db_path) as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO controlled_values(
                        category,value,display_label,governance_status,updated_by
                    ) VALUES(?,?,?,'authorized',?)
                    """,
                    (category, value, label, actor),
                )
            except Exception as exc:
                raise ValueError("Este valor já existe nesta lista controlada.") from exc
            connection.execute(
                """
                INSERT INTO audit_events(event_type,actor,resulting_state,comment,details_json)
                VALUES('CONTROLLED_VALUE_CREATE',?,'authorized',?,?)
                """,
                (actor, str(payload.get("comment") or "Novo valor controlado"),
                 json.dumps({"category": category, "value": value}, ensure_ascii=False)),
            )
            value_id = int(cursor.lastrowid)
        return next(item for item in self.list_values() if item["id"] == value_id)

    def delete_value(self, value_id: int, *, actor: str, comment: str = "") -> None:
        self.require_user(actor, {"reviewer", "approver", "administrator"})
        with transaction(self.db_path) as connection:
            current = connection.execute(
                "SELECT * FROM controlled_values WHERE id=?", (value_id,)
            ).fetchone()
            if current is None:
                raise ValueError("Valor controlado inexistente.")
            if current["usage_count"]:
                raise ValueError(
                    "Este valor está em uso. Faça merge com outro valor antes de o apagar."
                )
            connection.execute("DELETE FROM controlled_values WHERE id=?", (value_id,))
            connection.execute(
                """
                INSERT INTO audit_events(event_type,actor,previous_state,comment,details_json)
                VALUES('CONTROLLED_VALUE_DELETE',?,?,?,?)
                """,
                (actor, current["governance_status"], comment or "Valor controlado apagado",
                 json.dumps({"category": current["category"], "value": current["value"]}, ensure_ascii=False)),
            )

    def merge_values(
        self, source_id: int, target_id: int, *, actor: str, comment: str = ""
    ) -> dict:
        self.require_user(actor, {"reviewer", "approver", "administrator"})
        if source_id == target_id:
            raise ValueError("A origem e o destino do merge têm de ser diferentes.")
        with transaction(self.db_path) as connection:
            source = connection.execute(
                "SELECT * FROM controlled_values WHERE id=?", (source_id,)
            ).fetchone()
            target = connection.execute(
                "SELECT * FROM controlled_values WHERE id=?", (target_id,)
            ).fetchone()
            if source is None or target is None or source["category"] != target["category"]:
                raise ValueError("Valores de merge inexistentes ou de categorias diferentes.")
            affected = self._replace_occurrences(
                connection, source["category"], source["value"], target["value"],
                actor=actor,
            )
            connection.execute("DELETE FROM controlled_values WHERE id=?", (source_id,))
            connection.execute(
                """
                INSERT INTO audit_events(event_type,actor,previous_state,resulting_state,comment,details_json)
                VALUES('CONTROLLED_VALUE_MERGE',?,?,?, ?,?)
                """,
                (actor, source["value"], target["value"], comment or "Merge de valores controlados",
                 json.dumps({"category": source["category"], "affected_entries": affected}, ensure_ascii=False)),
            )
        return next(item for item in self.list_values() if item["id"] == target_id)

    @staticmethod
    def _replace_occurrences(
        connection, category: str, old: str, new: str, *, actor: str
    ) -> int:
        if old == new:
            return 0
        run = connection.execute(
            "SELECT id FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            return 0
        if category == "grammar":
            rows = connection.execute(
                "SELECT * FROM entries WHERE import_run_id=? AND grammatical_info=?",
                (run["id"], old),
            ).fetchall()
        elif category == "editorial_status":
            rows = connection.execute(
                "SELECT * FROM entries WHERE import_run_id=? AND editorial_status=?",
                (run["id"], old),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT DISTINCT entries.* FROM entries JOIN labels ON labels.entry_id=entries.id
                 WHERE entries.import_run_id=? AND labels.label_type IN ('domain','dom')
                   AND labels.value=?
                """,
                (run["id"], old),
            ).fetchall()
        for row in rows:
            root = ET.fromstring(row["raw_xml"])
            if category == "grammar":
                for node in root.iter():
                    if _local(node.tag) == "gramGrp" and (node.text or "").strip() == old:
                        node.text = new
                connection.execute(
                    "UPDATE entries SET grammatical_info=? WHERE id=?", (new, row["id"])
                )
            elif category == "editorial_status":
                for node in root.iter():
                    for attribute, value in list(node.attrib.items()):
                        if attribute.rsplit("}", 1)[-1] == "status" and value == old:
                            node.set(attribute, new)
                connection.execute(
                    "UPDATE entries SET editorial_status=? WHERE id=?", (new, row["id"])
                )
            else:
                for node in root.iter():
                    if (_local(node.tag) == "usg" and node.get("type") in {"domain", "dom"}
                            and "".join(node.itertext()).strip() == old):
                        node.text = new
                connection.execute(
                    "UPDATE labels SET value=?,value_normalized=? WHERE entry_id=? AND label_type IN ('domain','dom') AND value=?",
                    (new, search_key(new), row["id"], old),
                )
            raw_xml = ET.tostring(root, encoding="unicode", short_empty_elements=True)
            connection.execute(
                """
                UPDATE entries SET raw_xml=?,raw_sha256=?,workflow_status='EDITED',
                       workflow_actor=?,
                       workflow_updated_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (raw_xml, hashlib.sha256(raw_xml.encode()).hexdigest(), actor, row["id"]),
            )
        return len(rows)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
