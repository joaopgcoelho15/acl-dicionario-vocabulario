from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from .editorial_db import connect, initialize, transaction
from .validation import validate_canonical_xml
from .workflow import WORKFLOW_XML_STATUS


TEI = "http://www.tei-c.org/ns/1.0"
DACL = "http://dacl.zbr.pt/annotations"
ET.register_namespace("", TEI)
ET.register_namespace("dacl", DACL)


def persistence_status(db_path: str | Path) -> dict:
    initialize(db_path)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM dataset_persistence WHERE id=1"
        ).fetchone()
        return dict(row)


def mark_dataset_synchronized(
    db_path: str | Path,
    *,
    source_path: str | Path | None = None,
    source_sha256: str | None = None,
) -> None:
    """Marca uma importação inicial bem-sucedida como referência canónica."""
    initialize(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with transaction(db_path) as connection:
        state = connection.execute(
            "SELECT working_revision FROM dataset_persistence WHERE id=1"
        ).fetchone()
        connection.execute(
            """
            UPDATE dataset_persistence
               SET saved_revision=?, has_unsaved_changes=0,
                   last_saved_at=?, last_saved_path=COALESCE(?,last_saved_path),
                   last_saved_sha256=COALESCE(?,last_saved_sha256),
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=1
            """,
            (
                state["working_revision"],
                now,
                str(source_path) if source_path else None,
                source_sha256,
            ),
        )


def save_canonical_xml(
    db_path: str | Path,
    exports_root: str | Path,
    *,
    actor: str,
    rng_path: str | Path | None = None,
    audit: bool = True,
) -> dict:
    """Guarda a versão de trabalho completa em TEI/XML e gera o log associado."""
    initialize(db_path)
    root_dir = Path(exports_root)
    root_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    stamp = created.astimezone().strftime("%Y%m%d_%H%M%S")
    xml_name = f"DLP_Vocabulario_{stamp}.xml"
    log_name = f"DLP_Vocabulario_{stamp}.log.json"
    xml_path = root_dir / xml_name
    log_path = root_dir / log_name

    with connect(db_path) as connection:
        run = connection.execute(
            "SELECT * FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            raise RuntimeError("Não existe um conjunto de dados ativo para guardar.")
        state = dict(connection.execute(
            "SELECT * FROM dataset_persistence WHERE id=1"
        ).fetchone())
        rows = connection.execute(
            """
            SELECT * FROM entries
             WHERE import_run_id=? AND workflow_status<>'REMOVED'
             ORDER BY source_ordinal
            """,
            (run["id"],),
        ).fetchall()
        removed = connection.execute(
            "SELECT COUNT(*) FROM entries WHERE import_run_id=? AND workflow_status='REMOVED'",
            (run["id"],),
        ).fetchone()[0]
        states = {
            row["workflow_status"]: row["count"]
            for row in connection.execute(
                """
                SELECT workflow_status,COUNT(*) AS count FROM entries
                 WHERE import_run_id=? GROUP BY workflow_status
                """,
                (run["id"],),
            )
        }
        events = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM audit_events WHERE id>? ORDER BY id",
                (state["last_saved_event_id"],),
            )
        ]
        instructions = json.loads(run["source_processing_instructions"] or "[]")

    xml_fd, xml_temporary_name = tempfile.mkstemp(
        prefix="acl-save-", suffix=".xml", dir=root_dir
    )
    log_fd, log_temporary_name = tempfile.mkstemp(
        prefix="acl-save-", suffix=".json", dir=root_dir
    )
    os.close(xml_fd)
    os.close(log_fd)
    temporary_xml = Path(xml_temporary_name)
    temporary_log = Path(log_temporary_name)
    try:
        digest = hashlib.sha256()
        with temporary_xml.open("w", encoding="utf-8", newline="\n") as output:
            def write(value: str) -> None:
                output.write(value)
                digest.update(value.encode("utf-8"))

            write('<?xml version="1.0" encoding="UTF-8"?>\n')
            for instruction in instructions:
                write(str(instruction) + "\n")
            write("<dic>\n")
            for row in rows:
                write(_entry_xml_with_workflow(row) + "\n")
            write("</dic>\n")

        validation = validate_canonical_xml(temporary_xml, rng_path=rng_path)
        if not validation.get("well_formed", False):
            raise RuntimeError("O TEI/XML gerado não está bem formado.")
        if not validation.get("xml_id_unique", True):
            raise RuntimeError("O TEI/XML gerado contém identificadores duplicados.")

        log_document = {
            "generated_at": created.isoformat(),
            "generated_by": actor,
            "source_import": {
                "path": run["source_path"],
                "sha256": run["source_sha256"],
                "imported_at": run["finished_at"],
            },
            "working_revision": state["working_revision"],
            "previous_saved_revision": state["saved_revision"],
            "entries_exported": len(rows),
            "entries_removed": int(removed),
            "states": states,
            "xml_sha256": digest.hexdigest(),
            "validation": validation,
            "audit_events_since_previous_save": events,
        }
        temporary_log.write_text(
            json.dumps(log_document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_xml.replace(xml_path)
        temporary_log.replace(log_path)

        with transaction(db_path) as connection:
            if audit:
                cursor = connection.execute(
                    """
                    INSERT INTO audit_events(
                        event_type,actor,resulting_state,comment,details_json
                    ) VALUES('CANONICAL_XML_SAVED',?,'synchronized',?,?)
                    """,
                    (
                        actor,
                        f"TEI/XML guardado em {xml_name}",
                        json.dumps(
                            {"xml": xml_name, "log": log_name, "sha256": digest.hexdigest()},
                            ensure_ascii=False,
                        ),
                    ),
                )
                last_saved_event_id = int(cursor.lastrowid)
            else:
                last_saved_event_id = int(connection.execute(
                    "SELECT COALESCE(MAX(id),0) FROM audit_events"
                ).fetchone()[0])
            connection.execute(
                """
                UPDATE dataset_persistence
                   SET saved_revision=working_revision,has_unsaved_changes=0,
                       last_saved_at=?,last_saved_path=?,last_saved_sha256=?,
                       last_saved_event_id=?,updated_at=CURRENT_TIMESTAMP
                 WHERE id=1
                """,
                (
                    created.isoformat(),
                    str(xml_path),
                    digest.hexdigest(),
                    last_saved_event_id,
                ),
            )
        return {
            "xml_name": xml_name,
            "log_name": log_name,
            "xml_path": str(xml_path),
            "log_path": str(log_path),
            "entries": len(rows),
            "removed": int(removed),
            "sha256": digest.hexdigest(),
            "validation": validation,
        }
    finally:
        temporary_xml.unlink(missing_ok=True)
        temporary_log.unlink(missing_ok=True)


def _entry_xml_with_workflow(row) -> str:
    root = ET.fromstring(row["raw_xml"])
    meta = next((node for node in root if node.tag == f"{{{DACL}}}meta"), None)
    if meta is None:
        meta = ET.Element(f"{{{DACL}}}meta")
        root.insert(0, meta)
    status = row["workflow_status"]
    meta.set("status", WORKFLOW_XML_STATUS.get(status, status.lower()))
    meta.set("origin", row["workflow_origin"] or "imported")
    meta.set("transitionedAt", row["workflow_updated_at"] or row["updated_at"])
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)
