from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import xml.etree.ElementTree as ET

from .editorial_db import connect, initialize, transaction
from .governance import GovernanceService, refresh_controlled_values
from .normalization import search_key
from .public_document import build_public_document
from .labels import domain_label, grammar_label, status_label
from .persistence import persistence_status, save_canonical_xml

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
TEI = "http://www.tei-c.org/ns/1.0"
DACL = "http://dacl.zbr.pt/annotations"
WORKFLOW_STATES = {
    "DRAFT", "EDITED", "REVIEWED", "NEEDS_REVISION",
    "VALIDATED", "PUBLISHED", "REMOVED",
}
ROLE_TRANSITIONS = {
    "editor": {
        "DRAFT": {"EDITED", "REMOVED"},
        "NEEDS_REVISION": {"EDITED"},
        "REMOVED": {"DRAFT"},
    },
    "reviewer": {
        "DRAFT": {"EDITED", "REVIEWED", "REMOVED"},
        "EDITED": {"NEEDS_REVISION", "REVIEWED", "REMOVED"},
        "NEEDS_REVISION": {"EDITED", "REVIEWED"},
        "REMOVED": {"DRAFT"},
    },
    "approver": {
        "DRAFT": {"EDITED", "REVIEWED", "VALIDATED", "REMOVED"},
        "EDITED": {"NEEDS_REVISION", "REVIEWED", "VALIDATED", "REMOVED"},
        "REVIEWED": {"NEEDS_REVISION", "VALIDATED", "REMOVED"},
        "NEEDS_REVISION": {"EDITED", "REVIEWED", "VALIDATED", "REMOVED"},
        "VALIDATED": {"NEEDS_REVISION", "REMOVED"},
        "PUBLISHED": {"NEEDS_REVISION", "REMOVED"},
        "REMOVED": {"DRAFT"},
    },
}


class EditorialError(RuntimeError):
    status = 400


class EntryNotFound(EditorialError):
    status = 404


class EditConflict(EditorialError):
    status = 409


class InvalidWorkflow(EditorialError):
    status = 422


class EditorialService:
    def __init__(self, db_path: str | Path, exports_root: str | Path | None = None):
        self.db_path = Path(db_path)
        self.exports_root = Path(exports_root or (self.db_path.parent / "exports"))
        initialize(self.db_path)
        self.governance = GovernanceService(self.db_path)
        self._facet_index = None
        self._facet_index_lock = threading.Lock()

    def overview(self) -> dict:
        with connect(self.db_path) as connection:
            run = connection.execute(
                "SELECT * FROM import_runs WHERE is_active=1 LIMIT 1"
            ).fetchone()
            releases = connection.execute(
                "SELECT * FROM releases ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            issues = connection.execute(
                """
                SELECT severity, COUNT(*) AS count
                  FROM validation_issues
                 GROUP BY severity
                """
            ).fetchall()
            workflow = connection.execute(
                """
                SELECT workflow_status AS value, COUNT(*) AS count
                  FROM entries
                 WHERE import_run_id=?
                 GROUP BY workflow_status
                 ORDER BY workflow_status
                """,
                (run["id"] if run else -1,),
            ).fetchall()
            run_id = run["id"] if run else -1
            filter_counts = {
                "resource": [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT resource AS value, COUNT(*) AS count
                          FROM entries WHERE import_run_id=?
                         GROUP BY resource ORDER BY resource
                        """,
                        (run_id,),
                    )
                ],
                "workflow": [dict(row) for row in workflow],
                "editorial_statuses": [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT editorial_status AS value, COUNT(*) AS count
                          FROM entries
                         WHERE import_run_id=?
                           AND NULLIF(TRIM(editorial_status),'') IS NOT NULL
                         GROUP BY editorial_status
                         ORDER BY editorial_status COLLATE NOCASE
                        """,
                        (run_id,),
                    )
                ],
                "grammar": [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT grammatical_info AS value, COUNT(*) AS count
                          FROM entries
                         WHERE import_run_id=?
                           AND NULLIF(TRIM(grammatical_info),'') IS NOT NULL
                         GROUP BY grammatical_info
                         ORDER BY grammatical_info COLLATE NOCASE
                        """,
                        (run_id,),
                    )
                ],
                "domains": [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT labels.value AS value,
                               COUNT(DISTINCT entries.id) AS count
                          FROM labels
                          JOIN entries ON entries.id=labels.entry_id
                         WHERE entries.import_run_id=?
                           AND labels.label_type IN ('domain','dom')
                         GROUP BY labels.value
                         ORDER BY labels.value COLLATE NOCASE
                        """,
                        (run_id,),
                    )
                ],
                "severity": [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT vi.severity AS value,
                               COUNT(DISTINCT vi.entry_id) AS count
                          FROM validation_issues vi
                          JOIN entries ON entries.id=vi.entry_id
                         WHERE entries.import_run_id=?
                         GROUP BY vi.severity ORDER BY vi.severity
                        """,
                        (run_id,),
                    )
                ],
            }
            enrichment = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM enrichments e
                    JOIN external_sources s ON s.code=e.source_code
                   WHERE s.publication_enabled=1
                     AND e.approval_status IN ('approved','imported')) AS matched,
                  (SELECT COUNT(*) FROM unmatched_enrichments u
                    JOIN external_sources s ON s.code=u.source_code
                   WHERE s.publication_enabled=1) AS unmatched,
                  (SELECT COUNT(*) FROM enrichments e
                    JOIN external_sources s ON s.code=e.source_code
                   WHERE s.publication_enabled=0) AS deferred
                """
            ).fetchone()
            filter_options = {
                key: [item["value"] for item in filter_counts[key]]
                for key in ("editorial_statuses", "grammar", "domains")
            }
            controlled_options = {
                category: [dict(item) for item in connection.execute(
                    """
                    SELECT value,display_label FROM controlled_values
                     WHERE category=? AND governance_status<>'obsolete'
                     ORDER BY value COLLATE NOCASE
                    """,
                    (category,),
                )]
                for category in ("grammar", "domain", "editorial_status")
            }
        return {
            "mode": "editable",
            "active_import": dict(run) if run else None,
            "releases": [dict(row) for row in releases],
            "validation_issues": [dict(row) for row in issues],
            "workflow": [dict(row) for row in workflow],
            "external_enrichments": dict(enrichment),
            "users": self.governance.users(),
            "filter_options": filter_options,
            "filter_counts": filter_counts,
            "controlled_options": controlled_options,
            "persistence": persistence_status(self.db_path),
        }

    def warm_entry_facets(self) -> None:
        """Prepara em segundo plano o índice de interseções dos filtros."""
        with connect(self.db_path) as connection:
            run = self._active_run(connection)
            if run is not None:
                self._cached_entry_facets(
                    connection,
                    run,
                    resource=None,
                    workflow=None,
                    editorial_status=None,
                    grammar=None,
                    domain=None,
                    severity=None,
                )

    def save_canonical(self, *, actor: str, rng_path: str | Path | None = None) -> dict:
        self.governance.require_user(
            actor, {"editor", "reviewer", "approver", "administrator"}
        )
        return save_canonical_xml(
            self.db_path, self.exports_root, actor=actor, rng_path=rng_path
        )

    def audit_report(self) -> dict:
        with connect(self.db_path) as connection:
            run = self._active_run(connection)
            run_id = run["id"] if run else -1
            states = [dict(row) for row in connection.execute(
                "SELECT workflow_status AS value,COUNT(*) AS count FROM entries WHERE import_run_id=? GROUP BY workflow_status ORDER BY workflow_status",
                (run_id,),
            )]
            resources = [dict(row) for row in connection.execute(
                "SELECT resource AS value,COUNT(*) AS count FROM entries WHERE import_run_id=? GROUP BY resource ORDER BY resource",
                (run_id,),
            )]
            issues = [dict(row) for row in connection.execute(
                "SELECT severity AS value,COUNT(*) AS count FROM validation_issues WHERE import_run_id=? GROUP BY severity ORDER BY severity",
                (run_id,),
            )]
            events = [dict(row) for row in connection.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT 200"
            )]
            releases = [dict(row) for row in connection.execute(
                "SELECT release_id,state,created_at,activated_at,description,prepared_by,approved_by FROM releases ORDER BY created_at DESC LIMIT 50"
            )]
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "active_import": dict(run) if run else None,
            "persistence": persistence_status(self.db_path),
            "states": states,
            "resources": resources,
            "validation_issues": issues,
            "recent_events": events,
            "releases": releases,
        }

    def list_entries(
        self,
        term: str,
        limit: int = 50,
        offset: int = 0,
        *,
        resource: str | None = None,
        workflow_status: str | None = None,
        editorial_status: str | None = None,
        grammar: str | None = None,
        domain: str | None = None,
        severity: str | None = None,
    ) -> dict:
        normalized = search_key(term)
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        with connect(self.db_path) as connection:
            run = self._active_run(connection)
            if run is None:
                return {"total": 0, "items": [], "facets": {}}

            workflow_value = workflow_status.upper() if workflow_status else None
            if resource and resource not in {"dictionary", "vocabulary"}:
                raise EditorialError("Filtro resource inválido.")
            if workflow_value and workflow_value not in WORKFLOW_STATES:
                raise EditorialError("Filtro workflow_status inválido.")
            if severity and severity not in {"error", "warning", "info"}:
                raise EditorialError("Severidade inválida.")

            def where_without(skipped: str | None = None):
                params: list[object] = [run["id"]]
                conditions: list[str] = []
                if normalized:
                    conditions.append(
                        "entries.lemma_normalized >= ? AND entries.lemma_normalized < ?"
                    )
                    params.extend((normalized, normalized + "\uffff"))
                for name, column, value in (
                    ("resource", "resource", resource),
                    ("workflow", "workflow_status", workflow_value),
                    ("editorial_status", "editorial_status", editorial_status),
                    ("grammar", "grammatical_info", grammar),
                ):
                    if value and skipped != name:
                        conditions.append(f"entries.{column}=?")
                        params.append(value)
                if domain and skipped != "domain":
                    conditions.append(
                        "entries.id IN (SELECT labels.entry_id FROM labels "
                        "WHERE labels.label_type IN ('domain','dom') AND labels.value=?)"
                    )
                    params.append(domain)
                if severity and skipped != "severity":
                    conditions.append(
                        "entries.id IN (SELECT vi.entry_id FROM validation_issues vi "
                        "WHERE vi.severity=?)"
                    )
                    params.append(severity)
                condition = " AND " + " AND ".join(conditions) if conditions else ""
                return condition, params

            condition, params = where_without()
            active_filters = any(
                (resource, workflow_value, editorial_status, grammar, domain, severity)
            )
            cached_facets = None
            if active_filters and not normalized:
                cached_facets, total = self._cached_entry_facets(
                    connection,
                    run,
                    resource=resource,
                    workflow=workflow_value,
                    editorial_status=editorial_status,
                    grammar=grammar,
                    domain=domain,
                    severity=severity,
                )
            else:
                total = connection.execute(
                    f"SELECT COUNT(*) FROM entries WHERE import_run_id=? {condition}",
                    params,
                ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT id, public_id, resource, lemma, grammatical_info,
                       editorial_status, workflow_status, workflow_origin,
                       workflow_actor, raw_sha256,
                       updated_at,
                       (SELECT COUNT(*) FROM validation_issues vi
                         WHERE vi.entry_id=entries.id AND vi.severity='error'
                           AND NOT EXISTS (
                             SELECT 1 FROM validation_waivers vw
                              WHERE vw.entry_id=vi.entry_id
                                AND vw.rule_code=vi.rule_code
                                AND vw.entry_sha256=entries.raw_sha256
                                AND vw.revoked_at IS NULL
                           )) AS error_count,
                       (SELECT COUNT(*) FROM validation_issues vi
                         WHERE vi.entry_id=entries.id AND vi.severity='error'
                           AND EXISTS (
                             SELECT 1 FROM validation_waivers vw
                              WHERE vw.entry_id=vi.entry_id
                                AND vw.rule_code=vi.rule_code
                                AND vw.entry_sha256=entries.raw_sha256
                                AND vw.revoked_at IS NULL
                           )) AS waived_error_count,
                       (SELECT COUNT(*) FROM validation_issues vi
                         WHERE vi.entry_id=entries.id AND vi.severity='warning') AS warning_count
                  FROM entries
                 WHERE import_run_id=? {condition}
                 ORDER BY lemma_normalized, source_ordinal
                 LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

            if cached_facets is not None:
                facets = cached_facets
            else:
                facets = {}
                for name, column in (
                    ("resource", "resource"),
                    ("workflow", "workflow_status"),
                    ("editorial_statuses", "editorial_status"),
                    ("grammar", "grammatical_info"),
                ):
                    skipped = "editorial_status" if name == "editorial_statuses" else name
                    facet_condition, facet_params = where_without(skipped)
                    facets[name] = [
                        dict(row)
                        for row in connection.execute(
                            f"""
                            SELECT entries.{column} AS value, COUNT(*) AS count
                              FROM entries
                             WHERE import_run_id=? {facet_condition}
                               AND NULLIF(TRIM(entries.{column}),'') IS NOT NULL
                             GROUP BY entries.{column}
                             ORDER BY entries.{column} COLLATE NOCASE
                            """,
                            facet_params,
                        )
                    ]

                domain_condition, domain_params = where_without("domain")
                facets["domains"] = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT labels.value AS value,
                               COUNT(DISTINCT entries.id) AS count
                          FROM entries
                          JOIN labels ON labels.entry_id=entries.id
                         WHERE entries.import_run_id=? {domain_condition}
                           AND labels.label_type IN ('domain','dom')
                         GROUP BY labels.value
                         ORDER BY labels.value COLLATE NOCASE
                        """,
                        domain_params,
                    )
                ]
                severity_condition, severity_params = where_without("severity")
                facets["severity"] = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT vi.severity AS value,
                               COUNT(DISTINCT entries.id) AS count
                          FROM entries
                          JOIN validation_issues vi ON vi.entry_id=entries.id
                         WHERE entries.import_run_id=? {severity_condition}
                         GROUP BY vi.severity
                         ORDER BY vi.severity
                        """,
                        severity_params,
                    )
                ]
        return {
            "total": total,
            "items": [dict(row) for row in rows],
            "facets": facets,
        }

    def _cached_entry_facets(self, connection, run, **selected) -> dict:
        signature_row = connection.execute(
            """
            SELECT
              COALESCE((SELECT MAX(id) FROM audit_events),0),
              COALESCE((SELECT MAX(id) FROM validation_issues),0),
              (SELECT COUNT(*) FROM validation_issues),
              (SELECT COUNT(*) FROM entries WHERE import_run_id=?)
            """,
            (run["id"],),
        ).fetchone()
        signature = (run["id"], *tuple(signature_row))
        with self._facet_index_lock:
            if not self._facet_index or self._facet_index["signature"] != signature:
                dimensions = {
                    name: {}
                    for name in (
                        "resource", "workflow", "editorial_status",
                        "grammar", "domain", "severity",
                    )
                }

                def add(name, value, entry_id):
                    if value:
                        dimensions[name].setdefault(value, set()).add(entry_id)

                universe = set()
                for row in connection.execute(
                    """
                    SELECT id,resource,workflow_status,editorial_status,
                           grammatical_info
                      FROM entries WHERE import_run_id=?
                    """,
                    (run["id"],),
                ):
                    entry_id = row["id"]
                    universe.add(entry_id)
                    add("resource", row["resource"], entry_id)
                    add("workflow", row["workflow_status"], entry_id)
                    add("editorial_status", row["editorial_status"], entry_id)
                    add("grammar", row["grammatical_info"], entry_id)
                for row in connection.execute(
                    """
                    SELECT labels.entry_id,labels.value
                      FROM labels JOIN entries ON entries.id=labels.entry_id
                     WHERE entries.import_run_id=?
                       AND labels.label_type IN ('domain','dom')
                    """,
                    (run["id"],),
                ):
                    add("domain", row["value"], row["entry_id"])
                for row in connection.execute(
                    """
                    SELECT vi.entry_id,vi.severity
                      FROM validation_issues vi
                      JOIN entries ON entries.id=vi.entry_id
                     WHERE entries.import_run_id=?
                    """,
                    (run["id"],),
                ):
                    add("severity", row["severity"], row["entry_id"])
                self._facet_index = {
                    "signature": signature,
                    "universe": universe,
                    "dimensions": dimensions,
                }

            index = self._facet_index
            output = {}
            response_names = {
                "resource": "resource",
                "workflow": "workflow",
                "editorial_status": "editorial_statuses",
                "grammar": "grammar",
                "domain": "domains",
                "severity": "severity",
            }
            for dimension, values in index["dimensions"].items():
                matching = index["universe"]
                for other, selected_value in selected.items():
                    if other != dimension and selected_value:
                        matching = matching & index["dimensions"][other].get(
                            selected_value, set()
                        )
                        if not matching:
                            break
                output[response_names[dimension]] = [
                    {"value": value, "count": len(matching & entry_ids)}
                    for value, entry_ids in values.items()
                    if matching & entry_ids
                ]
            matching_all = index["universe"]
            for dimension, selected_value in selected.items():
                if selected_value:
                    matching_all = matching_all & index["dimensions"][dimension].get(
                        selected_value, set()
                    )
                    if not matching_all:
                        break
            return output, len(matching_all)

    def get_entry(self, public_id: str) -> dict:
        with connect(self.db_path) as connection:
            row = self._entry_row(connection, public_id)
            value = dict(row)
            entry_id = row["id"]
            for key, table, order in (
                ("forms", "forms", "position, id"),
                ("senses", "senses", "position_path, id"),
                ("relations", "relations", "id"),
                ("labels", "labels", "id"),
                ("revisions", "revisions", "revision_no DESC"),
            ):
                value[key] = [
                    dict(item)
                    for item in connection.execute(
                        f"SELECT * FROM {table} WHERE entry_id=? ORDER BY {order}",
                        (entry_id,),
                    ).fetchall()
                ]
            value["enrichments"] = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT enrichments.* FROM enrichments
                      JOIN external_sources
                        ON external_sources.code=enrichments.source_code
                     WHERE enrichments.entry_id=?
                       AND enrichments.approval_status IN ('approved','imported')
                       AND external_sources.publication_enabled=1
                     ORDER BY enrichments.id
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            value["validation_issues"] = [
                self._issue_value(connection, entry_id, row["raw_sha256"], item)
                for item in connection.execute(
                    """
                    SELECT severity, rule_code, message, details_json
                      FROM validation_issues
                     WHERE entry_id=?
                     ORDER BY severity, id
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            value["audit_events"] = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT event_type, actor, previous_state, resulting_state,
                           comment, details_json, created_at
                      FROM audit_events WHERE entry_id=? ORDER BY id DESC
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            value["selected_for_publication"] = bool(
                connection.execute(
                    "SELECT 1 FROM publication_selections WHERE entry_id=?", (entry_id,)
                ).fetchone()
            )
            document = build_public_document(row, connection, "editorial-preview")
            value["public_view"] = _public_preview(document)
            value["public_view"]["workflow_status"] = row["workflow_status"]
        return value

    def publication_entries(self, limit: int = 200, offset: int = 0) -> dict:
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        with connect(self.db_path) as connection:
            run = self._active_run(connection)
            if run is None:
                return {"total": 0, "selected": 0, "items": []}
            ready_condition = """
                e.import_run_id=? AND (
                    e.workflow_status='VALIDATED' OR (
                        e.workflow_status='REMOVED' AND EXISTS (
                            SELECT 1 FROM published_entry_snapshots pes WHERE pes.entry_id=e.id
                        )
                    )
                )
            """
            total = connection.execute(
                f"SELECT COUNT(*) FROM entries e WHERE {ready_condition}",
                (run["id"],),
            ).fetchone()[0]
            selected = connection.execute(
                """
                SELECT COUNT(*) FROM publication_selections ps
                JOIN entries e ON e.id=ps.entry_id
                WHERE e.import_run_id=? AND e.workflow_status IN ('VALIDATED','REMOVED')
                """,
                (run["id"],),
            ).fetchone()[0]
            rows = connection.execute(
                """
                SELECT e.id,e.public_id,e.resource,e.lemma,e.grammatical_info,
                       e.editorial_status,e.workflow_status,e.updated_at,
                       CASE WHEN ps.entry_id IS NULL THEN 0 ELSE 1 END AS selected
                FROM entries e LEFT JOIN publication_selections ps ON ps.entry_id=e.id
                WHERE {ready_condition}
                ORDER BY e.lemma_normalized,e.source_ordinal LIMIT ? OFFSET ?
                """.format(ready_condition=ready_condition),
                (run["id"], limit, offset),
            ).fetchall()
        return {"total": total, "selected": selected, "items": [dict(row) for row in rows]}

    def select_for_publication(
        self, public_ids: list[str], *, actor: str, selected: bool
    ) -> dict:
        self.governance.require_user(actor, {"approver", "administrator"})
        clean_ids = [str(value) for value in public_ids if str(value).strip()]
        if not clean_ids:
            raise EditorialError("Selecione pelo menos uma entrada.")
        placeholders = ",".join("?" for _ in clean_ids)
        with transaction(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT id,public_id,workflow_status FROM entries WHERE public_id IN ({placeholders})",
                clean_ids,
            ).fetchall()
            if len(rows) != len(set(clean_ids)):
                raise EditorialError("Uma ou mais entradas selecionadas não existem.")
            if selected and any(row["workflow_status"] not in {"VALIDATED", "REMOVED"} for row in rows):
                raise InvalidWorkflow(
                    "Só podem entrar em publicação entradas validadas ou remoções pendentes."
                )
            for row in rows:
                if selected:
                    connection.execute(
                        """
                        INSERT INTO publication_selections(entry_id,selected_by)
                        VALUES(?,?) ON CONFLICT(entry_id) DO UPDATE SET
                        selected_by=excluded.selected_by,selected_at=CURRENT_TIMESTAMP
                        """,
                        (row["id"], actor),
                    )
                else:
                    connection.execute(
                        "DELETE FROM publication_selections WHERE entry_id=?", (row["id"],)
                    )
                connection.execute(
                    """
                    INSERT INTO audit_events(event_type,actor,entry_id,resulting_state,comment)
                    VALUES('PUBLICATION_SELECTION',?,?,?,?)
                    """,
                    (actor, row["id"], "selected" if selected else "pending",
                     "Entrada selecionada para publicação" if selected else "Entrada retirada da publicação"),
                )
        return self.publication_entries()

    @staticmethod
    def _issue_value(connection, entry_id: int, raw_sha256: str, row) -> dict:
        value = dict(row)
        waiver = connection.execute(
            """
            SELECT actor, reason, created_at FROM validation_waivers
             WHERE entry_id=? AND rule_code=? AND entry_sha256=?
               AND revoked_at IS NULL ORDER BY id DESC LIMIT 1
            """,
            (entry_id, row["rule_code"], raw_sha256),
        ).fetchone()
        value["waiver"] = dict(waiver) if waiver else None
        return value

    def waive_issue(
        self, public_id: str, rule_code: str, *, actor: str, reason: str
    ) -> dict:
        self.governance.require_user(actor, {"reviewer", "approver", "administrator"})
        reason = reason.strip()[:1000]
        if len(reason) < 10:
            raise EditorialError("Indique uma justificação com pelo menos 10 caracteres.")
        with transaction(self.db_path) as connection:
            row = self._entry_row(connection, public_id)
            issue = connection.execute(
                """
                SELECT id FROM validation_issues
                 WHERE entry_id=? AND rule_code=? AND severity='error' LIMIT 1
                """,
                (row["id"], rule_code),
            ).fetchone()
            if issue is None:
                raise EditorialError("Este problema não é um erro bloqueante ativo.")
            connection.execute(
                """
                INSERT INTO validation_waivers(
                    import_run_id, entry_id, rule_code, entry_sha256, reason, actor
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id, rule_code, entry_sha256) DO UPDATE SET
                    reason=excluded.reason, actor=excluded.actor,
                    created_at=CURRENT_TIMESTAMP, revoked_at=NULL
                """,
                (row["import_run_id"], row["id"], rule_code, row["raw_sha256"], reason, actor),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_type, actor, entry_id, resulting_state, comment, details_json
                ) VALUES ('VALIDATION_ERROR_WAIVED', ?, ?, ?, ?, ?)
                """,
                (
                    actor, row["id"], row["workflow_status"], reason,
                    json.dumps({"rule_code": rule_code, "entry_sha256": row["raw_sha256"]}),
                ),
            )
        return self.get_entry(public_id)

    def apply_issue_fix(
        self, public_id: str, rule_code: str, fix_code: str, *, actor: str, comment: str
    ) -> dict:
        self.governance.require_user(actor, {"editor", "reviewer", "approver", "administrator"})
        if rule_code != "RNG_INVALID" or fix_code != "RNG_EMPH_TO_HI":
            raise EditorialError("Esta correção automática não está disponível.")
        with transaction(self.db_path) as connection:
            row = self._entry_row(connection, public_id)
            root = ET.fromstring(row["raw_xml"])
            changed = 0
            for node in root.iter():
                if _local(node.tag) == "emph":
                    node.tag = f"{{{TEI}}}hi"
                    node.set("rend", "italic")
                    changed += 1
            if not changed:
                raise EditorialError("A entrada não contém elementos <emph> para corrigir.")
            snapshot = self._snapshot(connection, row)
            revision_no = int(connection.execute(
                "SELECT COALESCE(MAX(revision_no),0)+1 FROM revisions WHERE entry_id=?",
                (row["id"],),
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO revisions(entry_id,revision_no,actor,comment,snapshot_json) VALUES(?,?,?,?,?)",
                (row["id"], revision_no, actor, comment or "Normalização TEI: emph para hi", json.dumps(snapshot, ensure_ascii=False)),
            )
            raw_xml = _serialize(root)
            raw_sha = hashlib.sha256(raw_xml.encode("utf-8")).hexdigest()
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            connection.execute(
                "UPDATE entries SET raw_xml=?,raw_sha256=?,workflow_status='EDITED',workflow_actor=?,workflow_updated_at=?,updated_at=? WHERE id=?",
                (raw_xml, raw_sha, actor, now, now, row["id"]),
            )
            connection.execute("DELETE FROM validation_issues WHERE entry_id=?", (row["id"],))
            connection.execute("DELETE FROM validation_runs WHERE import_run_id=?", (row["import_run_id"],))
            connection.execute(
                """INSERT INTO audit_events(event_type,actor,entry_id,previous_state,resulting_state,comment,details_json)
                   VALUES('VALIDATION_AUTO_FIX',?,?,?,'EDITED',?,?)""",
                (actor, row["id"], row["workflow_status"], comment or "Normalização TEI", json.dumps({"rule_code": rule_code, "fix_code": fix_code, "occurrences": changed})),
            )
        return self.get_entry(public_id)

    def update_entry(self, public_id: str, payload: dict) -> dict:
        actor = str(payload.get("actor") or "").strip()[:120]
        self.governance.require_user(
            actor, {"editor", "reviewer", "approver", "administrator"}
        )
        comment = str(payload.get("comment") or "Edição editorial").strip()[:500]
        with transaction(self.db_path) as connection:
            row = self._entry_row(connection, public_id)
            if row["workflow_status"] == "REMOVED":
                raise InvalidWorkflow(
                    "Recupere a entrada apagada antes de a voltar a editar."
                )
            if row["workflow_status"] == "PUBLISHED":
                raise InvalidWorkflow(
                    "Na área Publicação, devolva primeiro a entrada para revisão."
                )
            expected = payload.get("expected_updated_at")
            if expected and expected != row["updated_at"]:
                raise EditConflict(
                    "A entrada foi alterada depois de ser aberta. Recarregue-a antes de gravar."
                )
            snapshot = self._snapshot(connection, row)
            old_domains = {
                item["value"] for item in snapshot.get("labels", [])
                if item.get("label_type") in {"domain", "dom"}
            }
            revision_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM revisions WHERE entry_id=?",
                    (row["id"],),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO revisions(entry_id, revision_no, actor, comment, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    revision_no,
                    actor,
                    comment,
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            lemma = str(payload.get("lemma", row["lemma"])).strip()
            grammar = _nullable(payload.get("grammatical_info", row["grammatical_info"]))
            source_status = _nullable(
                payload.get("editorial_status", row["editorial_status"])
            )
            if not lemma:
                raise EditorialError("O lema não pode ficar vazio.")
            root = ET.fromstring(row["raw_xml"])
            orth = next((node for node in root.iter() if _local(node.tag) == "orth"), None)
            if orth is None:
                raise EditorialError("O XML da entrada não contém <orth>.")
            orth.text = lemma
            gram_node = next(
                (node for node in root.iter() if _local(node.tag) == "gramGrp"), None
            )
            if gram_node is None and grammar:
                gram_node = ET.Element(f"{{{TEI}}}gramGrp")
                root.insert(1, gram_node)
            if gram_node is not None:
                gram_node.text = grammar or ""
            self._set_source_status(root, source_status)
            self._update_forms(connection, row["id"], root, payload.get("forms"))
            self._update_senses(connection, row["id"], root, payload.get("senses", []))
            self._update_labels(connection, row["id"], root, payload.get("labels"))
            self._update_relations(
                connection, row["id"], root, payload.get("relations")
            )
            raw_xml = _serialize(root)
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            raw_sha = hashlib.sha256(raw_xml.encode("utf-8")).hexdigest()
            connection.execute(
                """
                UPDATE entries
                   SET lemma=?, lemma_normalized=?, grammatical_info=?,
                       editorial_status=?, workflow_status='EDITED', workflow_actor=?,
                       workflow_updated_at=?, raw_xml=?, raw_sha256=?, updated_at=?
                 WHERE id=?
                """,
                (
                    lemma,
                    search_key(lemma),
                    grammar,
                    source_status,
                    actor,
                    now,
                    raw_xml,
                    raw_sha,
                    now,
                    row["id"],
                ),
            )
            connection.execute(
                """
                UPDATE forms
                   SET value=?, value_normalized=?
                 WHERE id=(
                    SELECT id FROM forms WHERE entry_id=?
                    ORDER BY position, id LIMIT 1
                 )
                """,
                (lemma, search_key(lemma), row["id"]),
            )
            self._validate(connection, row["id"], row["import_run_id"], root, lemma)
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_type, actor, entry_id, previous_state,
                    resulting_state, comment, details_json
                ) VALUES ('ENTRY_EDIT', ?, ?, ?, 'EDITED', ?, ?)
                """,
                (
                    actor,
                    row["id"],
                    row["workflow_status"],
                    comment,
                    json.dumps({"revision_no": revision_no}, ensure_ascii=False),
                ),
            )
            connection.execute(
                "DELETE FROM publication_selections WHERE entry_id=?", (row["id"],)
            )
            new_domains = {
                item["value"] for item in connection.execute(
                    "SELECT value FROM labels WHERE entry_id=? AND label_type IN ('domain','dom')",
                    (row["id"],),
                )
            }
        refresh_controlled_values(
            self.db_path,
            {
                "grammar": {row["grammatical_info"], grammar},
                "editorial_status": {row["editorial_status"], source_status},
                "domain": old_domains | new_domains,
            },
        )
        return self.get_entry(public_id)

    def set_workflow(
        self, public_id: str, target: str, *, actor: str, comment: str = "",
        confirmed: bool = False,
    ) -> dict:
        target = target.upper()
        user = self.governance.require_user(actor)
        role = "approver" if user["role"] == "administrator" else user["role"]
        with transaction(self.db_path) as connection:
            row = self._entry_row(connection, public_id)
            current = row["workflow_status"]
            if target not in ROLE_TRANSITIONS.get(role, {}).get(current, set()):
                raise InvalidWorkflow(
                    f"O papel {user['role']} não pode executar a transição {current} → {target}."
                )
            if target == "REMOVED" and not confirmed:
                raise InvalidWorkflow(
                    "A remoção exige confirmação explícita do utilizador."
                )
            error_count = connection.execute(
                """
                SELECT COUNT(*) FROM validation_issues vi
                JOIN entries e ON e.id=vi.entry_id
                 WHERE vi.entry_id=? AND vi.severity='error'
                   AND NOT EXISTS (
                     SELECT 1 FROM validation_waivers vw
                      WHERE vw.entry_id=vi.entry_id
                        AND vw.rule_code=vi.rule_code
                        AND vw.entry_sha256=e.raw_sha256
                        AND vw.revoked_at IS NULL
                   )
                """,
                (row["id"],),
            ).fetchone()[0]
            if target in {"REVIEWED", "VALIDATED"} and error_count:
                raise InvalidWorkflow(
                    "A entrada tem erros de validação que impedem esta transição."
                )
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            connection.execute(
                """
                UPDATE entries SET workflow_status=?,
                       workflow_origin=CASE WHEN ?='DRAFT' THEN 'recovered' ELSE workflow_origin END,
                       workflow_actor=?,workflow_updated_at=?,updated_at=? WHERE id=?
                """,
                (target, target, actor, now, now, row["id"]),
            )
            if target == "REMOVED" and current == "PUBLISHED":
                connection.execute(
                    """
                    INSERT INTO publication_selections(entry_id,selected_by)
                    VALUES(?,?) ON CONFLICT(entry_id) DO UPDATE SET
                    selected_by=excluded.selected_by,selected_at=CURRENT_TIMESTAMP
                    """,
                    (row["id"], actor),
                )
            elif target != "VALIDATED":
                connection.execute(
                    "DELETE FROM publication_selections WHERE entry_id=?", (row["id"],)
                )
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_type, actor, entry_id, previous_state,
                    resulting_state, comment
                ) VALUES ('WORKFLOW', ?, ?, ?, ?, ?)
                """,
                (actor, row["id"], current, target, comment or None),
            )
        return self.get_entry(public_id)

    def restore_revision(
        self, public_id: str, revision_no: int, *, actor: str, comment: str = ""
    ) -> dict:
        self.governance.require_user(
            actor, {"editor", "reviewer", "approver", "administrator"}
        )
        with transaction(self.db_path) as connection:
            row = self._entry_row(connection, public_id)
            revision = connection.execute(
                """
                SELECT * FROM revisions WHERE entry_id=? AND revision_no=?
                """,
                (row["id"], revision_no),
            ).fetchone()
            if revision is None:
                raise EditorialError("Revisão inexistente.")
            snapshot = json.loads(revision["snapshot_json"])
            previous = self._snapshot(connection, row)
            next_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision_no),0)+1 FROM revisions WHERE entry_id=?",
                    (row["id"],),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO revisions(entry_id, revision_no, actor, comment, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    next_no,
                    actor,
                    comment or f"Antes de repor a revisão {revision_no}",
                    json.dumps(previous, ensure_ascii=False),
                ),
            )
            restored = snapshot["entry"]
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            connection.execute(
                """
                UPDATE entries SET lemma=?, lemma_normalized=?, grammatical_info=?,
                       editorial_status=?, workflow_status='EDITED', workflow_actor=?,
                       workflow_updated_at=?, raw_xml=?, raw_sha256=?, updated_at=? WHERE id=?
                """,
                (
                    restored["lemma"],
                    restored["lemma_normalized"],
                    restored["grammatical_info"],
                    restored["editorial_status"],
                    actor,
                    now,
                    restored["raw_xml"],
                    restored["raw_sha256"],
                    now,
                    row["id"],
                ),
            )
            for table in ("forms", "senses", "relations", "labels"):
                connection.execute(f"DELETE FROM {table} WHERE entry_id=?", (row["id"],))
            self._restore_children(connection, row["id"], snapshot)
            connection.execute(
                """
                INSERT INTO audit_events(
                    event_type, actor, entry_id, previous_state,
                    resulting_state, comment, details_json
                ) VALUES ('REVISION_RESTORE', ?, ?, ?, 'EDITED', ?, ?)
                """,
                (
                    actor,
                    row["id"],
                    row["workflow_status"],
                    comment or f"Reposição da revisão {revision_no}",
                    json.dumps({"restored_revision": revision_no}),
                ),
            )
        return self.get_entry(public_id)

    def mark_published(
        self, release_id: str | None = None, *, actor: str = "system.publish"
    ) -> None:
        with transaction(self.db_path) as connection:
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            if release_id:
                rows = connection.execute(
                    """
                    SELECT e.* FROM entries e JOIN release_entries re ON re.entry_id=e.id
                     WHERE re.release_id=?
                    """,
                    (release_id,),
                ).fetchall()
                for row in rows:
                    if row["workflow_status"] == "REMOVED":
                        connection.execute(
                            "DELETE FROM published_entry_snapshots WHERE entry_id=?",
                            (row["id"],),
                        )
                        continue
                    document = build_public_document(row, connection, release_id)
                    connection.execute(
                        """
                        INSERT INTO published_entry_snapshots(
                            entry_id,raw_xml,document_json,release_id
                        ) VALUES(?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET
                            raw_xml=excluded.raw_xml,document_json=excluded.document_json,
                            release_id=excluded.release_id,updated_at=CURRENT_TIMESTAMP
                        """,
                        (row["id"], row["raw_xml"],
                         json.dumps(document, ensure_ascii=False), release_id),
                    )
                connection.execute(
                    """
                    UPDATE entries SET workflow_status='PUBLISHED',
                           workflow_actor=?,workflow_updated_at=?,updated_at=?
                     WHERE workflow_status='VALIDATED'
                       AND id IN (SELECT entry_id FROM release_entries WHERE release_id=?)
                    """,
                    (actor, now, now, release_id),
                )
                connection.execute(
                    "DELETE FROM publication_selections WHERE entry_id IN (SELECT entry_id FROM release_entries WHERE release_id=?)",
                    (release_id,),
                )
            else:
                connection.execute(
                    "UPDATE entries SET workflow_status='PUBLISHED',workflow_actor=?,workflow_updated_at=?,updated_at=? WHERE workflow_status='VALIDATED'",
                    (actor, now, now),
                )

    def can_publish(self, *, require_selection: bool = False) -> tuple[bool, str | None]:
        with connect(self.db_path) as connection:
            selected = connection.execute(
                "SELECT COUNT(*) FROM publication_selections"
            ).fetchone()[0]
            errors = connection.execute(
                """
                SELECT COUNT(*) FROM validation_issues vi
                LEFT JOIN entries e ON e.id=vi.entry_id
                JOIN publication_selections ps ON ps.entry_id=vi.entry_id
                WHERE vi.severity='error' AND e.workflow_status<>'REMOVED' AND NOT EXISTS (
                    SELECT 1 FROM validation_waivers vw
                     WHERE vw.entry_id=vi.entry_id
                       AND vw.rule_code=vi.rule_code
                       AND vw.entry_sha256=e.raw_sha256
                       AND vw.revoked_at IS NULL
                )
                """
            ).fetchone()[0]
            invalid = connection.execute(
                """
                SELECT COUNT(*) FROM publication_selections ps JOIN entries e ON e.id=ps.entry_id
                 WHERE e.workflow_status NOT IN ('VALIDATED','REMOVED')
                """
            ).fetchone()[0]
            if not require_selection:
                errors = connection.execute(
                    """
                    SELECT COUNT(*) FROM validation_issues vi LEFT JOIN entries e ON e.id=vi.entry_id
                    WHERE vi.severity='error' AND NOT EXISTS (
                        SELECT 1 FROM validation_waivers vw WHERE vw.entry_id=vi.entry_id
                          AND vw.rule_code=vi.rule_code AND vw.entry_sha256=e.raw_sha256
                          AND vw.revoked_at IS NULL
                    )
                    """
                ).fetchone()[0]
                invalid = connection.execute(
                    "SELECT COUNT(*) FROM entries WHERE workflow_status IN ('EDITED','REVIEWED','NEEDS_REVISION')"
                ).fetchone()[0]
        if require_selection and not selected:
            return False, "Selecione pelo menos uma entrada validada ou remoção pendente."
        if errors:
            return False, f"Existem {errors} erros de validação."
        if invalid:
            return False, (
                "A seleção contém entradas que já não estão validadas."
                if require_selection else
                f"Existem {invalid} entradas em edição ou revisão; valide-as antes de publicar."
            )
        return True, None

    def _update_senses(
        self,
        connection: sqlite3.Connection,
        entry_id: int,
        root: ET.Element,
        updates: list[dict],
    ) -> None:
        xml_senses = {
            node.get(XML_ID): node
            for node in root.iter()
            if _local(node.tag) == "sense" and node.get(XML_ID)
        }
        for item in updates:
            try:
                sense_id = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            sense = connection.execute(
                "SELECT * FROM senses WHERE id=? AND entry_id=?",
                (sense_id, entry_id),
            ).fetchone()
            if sense is None:
                continue
            definition = str(item.get("definition") or "").strip()
            node = xml_senses.get(sense["public_id"])
            if node is None:
                continue
            target = next(
                (
                    child
                    for child in node
                    if _local(child.tag) in {"def", "gloss"}
                ),
                None,
            )
            if target is None:
                target = ET.Element(f"{{{TEI}}}def")
                node.insert(0, target)
            target.text = definition
            kind = _local(target.tag)
            connection.execute(
                """
                UPDATE senses SET definition=?, gloss=?, raw_xml=?
                 WHERE id=?
                """,
                (
                    definition if kind == "def" else None,
                    definition if kind == "gloss" else None,
                    ET.tostring(node, encoding="unicode"),
                    sense_id,
                ),
            )

    def _update_forms(self, connection, entry_id, root, updates) -> None:
        if updates is None:
            return
        nodes = [node for node in root.iter() if _local(node.tag) == "orth"]
        rows = connection.execute(
            "SELECT * FROM forms WHERE entry_id=? ORDER BY position, id",
            (entry_id,),
        ).fetchall()
        by_id = {int(item.get("id", -1)): str(item.get("value") or "").strip()
                 for item in updates}
        for node, row in zip(nodes, rows):
            if row["id"] not in by_id:
                continue
            value = by_id[row["id"]]
            if not value:
                raise EditorialError("Uma forma existente não pode ficar vazia.")
            node.text = value
            connection.execute(
                "UPDATE forms SET value=?, value_normalized=? WHERE id=?",
                (value, search_key(value), row["id"]),
            )

    def _update_labels(self, connection, entry_id, root, updates) -> None:
        if updates is None:
            return
        nodes = [node for node in root.iter() if _local(node.tag) == "usg" and _text(node)]
        rows = connection.execute(
            "SELECT * FROM labels WHERE entry_id=? ORDER BY id", (entry_id,)
        ).fetchall()
        by_id = {int(item.get("id", -1)): str(item.get("value") or "").strip()
                 for item in updates}
        for node, row in zip(nodes, rows):
            if row["id"] not in by_id:
                continue
            value = by_id[row["id"]]
            if not value:
                raise EditorialError("Uma marca existente não pode ficar vazia.")
            node.text = value
            connection.execute(
                "UPDATE labels SET value=?, value_normalized=? WHERE id=?",
                (value, search_key(value), row["id"]),
            )

    def _update_relations(self, connection, entry_id, root, updates) -> None:
        if updates is None:
            return
        nodes = [node for node in root.iter() if _local(node.tag) == "ref" and _text(node)]
        rows = connection.execute(
            "SELECT * FROM relations WHERE entry_id=? ORDER BY id", (entry_id,)
        ).fetchall()
        by_id = {int(item.get("id", -1)): item for item in updates}
        for node, row in zip(nodes, rows):
            item = by_id.get(row["id"])
            if not item:
                continue
            text = str(item.get("target_text") or "").strip()
            target = str(item.get("target_id") or "").strip() or None
            if not text:
                raise EditorialError("O texto de uma remissão não pode ficar vazio.")
            node.text = text
            if target:
                node.set("target", target)
            else:
                node.attrib.pop("target", None)
            connection.execute(
                "UPDATE relations SET target_text=?, target_id=? WHERE id=?",
                (text, target, row["id"]),
            )

    def _set_source_status(self, root: ET.Element, value: str | None) -> None:
        node = next((n for n in root.iter() if _local(n.tag) == "meta"), None)
        if node is None and value:
            node = ET.Element(f"{{{DACL}}}meta")
            root.insert(0, node)
        if node is not None:
            if value:
                node.set("status", value)
            else:
                node.attrib.pop("status", None)

    def _validate(
        self,
        connection: sqlite3.Connection,
        entry_id: int,
        import_run_id: int,
        root: ET.Element,
        lemma: str,
    ) -> None:
        connection.execute(
            "DELETE FROM validation_issues WHERE entry_id=?", (entry_id,)
        )
        checks = []
        if not root.get(XML_ID):
            checks.append(("error", "XML_ID_REQUIRED", "A entrada não tem xml:id."))
        if not lemma:
            checks.append(("error", "LEMMA_REQUIRED", "A entrada não tem lema."))
        if not any(_local(node.tag) == "orth" for node in root.iter()):
            checks.append(("error", "ORTH_REQUIRED", "A entrada não contém <orth>."))
        if len({node.get(XML_ID) for node in root.iter() if node.get(XML_ID)}) != len(
            [node for node in root.iter() if node.get(XML_ID)]
        ):
            checks.append(("error", "DUPLICATE_LOCAL_ID", "Existem xml:id repetidos na entrada."))
        for severity, rule, message in checks:
            connection.execute(
                """
                INSERT INTO validation_issues(
                    import_run_id, entry_id, severity, rule_code, message
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (import_run_id, entry_id, severity, rule, message),
            )

    def _snapshot(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        return {
            "entry": dict(row),
            "forms": [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM forms WHERE entry_id=? ORDER BY position, id",
                    (row["id"],),
                )
            ],
            "senses": [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM senses WHERE entry_id=? ORDER BY position_path, id",
                    (row["id"],),
                )
            ],
            "relations": [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM relations WHERE entry_id=? ORDER BY id", (row["id"],)
                )
            ],
            "labels": [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM labels WHERE entry_id=? ORDER BY id", (row["id"],)
                )
            ],
        }

    @staticmethod
    def _restore_children(connection, entry_id: int, snapshot: dict) -> None:
        id_maps: dict[str, dict[int, int]] = {"senses": {}}
        for item in snapshot.get("forms", []):
            connection.execute(
                """
                INSERT INTO forms(entry_id, position, value, value_normalized, language, kind)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (entry_id, item["position"], item["value"], item["value_normalized"],
                 item.get("language"), item.get("kind")),
            )
        pending = list(snapshot.get("senses", []))
        while pending:
            progressed = False
            for item in pending[:]:
                old_parent = item.get("parent_id")
                if old_parent and old_parent not in id_maps["senses"]:
                    continue
                cursor = connection.execute(
                    """
                    INSERT INTO senses(entry_id, parent_id, public_id, position_path,
                        number_label, definition, gloss, raw_xml)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (entry_id, id_maps["senses"].get(old_parent), item.get("public_id"),
                     item["position_path"], item.get("number_label"),
                     item.get("definition"), item.get("gloss"), item["raw_xml"]),
                )
                id_maps["senses"][item["id"]] = int(cursor.lastrowid)
                pending.remove(item)
                progressed = True
            if not progressed:
                raise EditorialError("Não foi possível reconstruir a hierarquia de aceções.")
        for item in snapshot.get("relations", []):
            connection.execute(
                """
                INSERT INTO relations(entry_id, sense_id, relation_type, target_text, target_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry_id, id_maps["senses"].get(item.get("sense_id")),
                 item.get("relation_type"), item["target_text"], item.get("target_id")),
            )
        for item in snapshot.get("labels", []):
            connection.execute(
                """
                INSERT INTO labels(entry_id, sense_id, label_type, value, value_normalized)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry_id, id_maps["senses"].get(item.get("sense_id")), item["label_type"],
                 item["value"], item["value_normalized"]),
            )

    @staticmethod
    def _active_run(connection: sqlite3.Connection):
        return connection.execute(
            "SELECT * FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()

    @staticmethod
    def _entry_row(connection: sqlite3.Connection, public_id: str) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT entries.* FROM entries
            JOIN import_runs ON import_runs.id=entries.import_run_id
            WHERE import_runs.is_active=1 AND entries.public_id=?
            ORDER BY entries.source_ordinal LIMIT 1
            """,
            (public_id,),
        ).fetchone()
        if row is None:
            raise EntryNotFound("Entrada editorial não encontrada.")
        return row


def _nullable(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _public_preview(document: dict) -> dict:
    """Adapta a projeção canónica ao mesmo contrato visual da interface pública."""
    grammar = (document.get("grammatical_categories") or [None])[0]
    senses = []
    for sense in document.get("senses") or []:
        provenance = sense.get("provenance") or {}
        senses.append(
            {
                "xml_id": sense.get("id"),
                "number": sense.get("number"),
                "depth": sense.get("depth"),
                "section": sense.get("section"),
                "definition": sense.get("definition"),
                "definition_segments": sense.get("definition_segments") or [],
                "labels": [
                    {
                        "type": label.get("type"),
                        "value": label.get("value"),
                        "label": domain_label(label.get("value"))
                        if label.get("type") in {"dom", "domain"}
                        else label.get("value"),
                    }
                    for label in sense.get("labels") or []
                ],
                "examples": sense.get("examples") or [],
                "references": [
                    {
                        "type": relation.get("type"),
                        "value": relation.get("target_text"),
                        "target": relation.get("target_id"),
                    }
                    for relation in sense.get("relations") or []
                ],
                "notes": sense.get("notes") or [],
                "images": sense.get("images") or [],
                "source": {
                    "code": provenance.get("source"),
                    "url": provenance.get("source_url"),
                    "license": provenance.get("license"),
                }
                if provenance
                else None,
            }
        )
    return {
        "xml_id": document.get("source_id"),
        "lemma": document.get("lemma"),
        "grammatical_info": grammar,
        "grammatical_label": grammar_label(grammar),
        "source_status": document.get("status"),
        "source_status_label": status_label(document.get("status")),
        "lexical": {
            "orthographies": document.get("forms") or [],
            "gloss_items": [
                {"value": value, "segments": []}
                for value in document.get("glosses") or []
            ],
            "senses": senses,
            "etymologies": [document["etymology_text"]]
            if document.get("etymology_text")
            else [],
            "notes": [],
            "references": [
                {"value": relation.get("target_text"), "target": relation.get("target_id")}
                for relation in document.get("relations") or []
            ],
            "images": document.get("images") or [],
        },
    }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _serialize(root: ET.Element) -> str:
    ET.register_namespace("", TEI)
    ET.register_namespace("dacl", DACL)
    return ET.tostring(root, encoding="unicode")
