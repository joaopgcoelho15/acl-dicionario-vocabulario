from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

from . import __version__
from .editorial_db import connect, initialize
from .governance import GovernanceService
from .importer import processing_instructions
from .public_document import build_public_document
from .validation import validate_active_run, validate_canonical_xml


@dataclass(frozen=True)
class PublicationResult:
    release_id: str
    path: Path
    entries: int
    errors: int


def build_release(
    db_path: str | Path,
    releases_root: str | Path,
    *,
    release_id: str | None = None,
    contracts_root: str | Path | None = None,
    images_root: str | Path | None = None,
    prepared_by: str = "editor.demo",
    description: str = "Versão candidata",
    rng_path: str | Path | None = None,
    resume: bool = False,
) -> PublicationResult:
    initialize(db_path)
    GovernanceService(db_path).require_user(
        prepared_by, {"editor", "reviewer", "approver", "administrator"}
    )
    validation = validate_active_run(db_path, rng_path=rng_path)
    if not validation.valid:
        raise RuntimeError(
            f"A candidata não pode ser preparada: {validation.errors} erros impeditivos."
        )
    release_id = release_id or datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", release_id):
        raise ValueError("release_id inválido.")
    release_path = Path(releases_root) / release_id
    if release_path.exists() and (not resume or (release_path / "manifest.json").exists()):
        raise FileExistsError(f"A release {release_id} já existe.")
    release_path.mkdir(parents=True, exist_ok=resume)

    connection = connect(db_path)
    active_run = connection.execute(
        "SELECT * FROM import_runs WHERE is_active=1 LIMIT 1"
    ).fetchone()
    if active_run is None:
        connection.close()
        raise RuntimeError("Não existe uma importação editorial ativa.")

    counts = {"entries": 0, "dictionary": 0, "vocabulary": 0}
    issues: list[dict] = []
    lemma_index = {
        row["lemma_normalized"]: row["lemma"]
        for row in connection.execute(
            """
            SELECT lemma_normalized, MIN(lemma) AS lemma
              FROM entries
             WHERE import_run_id=? AND lemma_normalized<>''
             GROUP BY lemma_normalized
            """,
            (active_run["id"],),
        ).fetchall()
    }
    dictionary_path = release_path / "dictionary.ndjson"
    vocabulary_path = release_path / "vocabulary.ndjson"
    canonical_path = release_path / "canonical.xml"
    with ExitStack() as stack:
        dictionary = stack.enter_context(dictionary_path.open("w", encoding="utf-8"))
        vocabulary = stack.enter_context(vocabulary_path.open("w", encoding="utf-8"))
        canonical = stack.enter_context(canonical_path.open("w", encoding="utf-8"))
        canonical.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        instructions = json.loads(active_run["source_processing_instructions"] or "[]")
        if not instructions and Path(active_run["source_path"]).is_file():
            instructions = processing_instructions(active_run["source_path"])
        for instruction in instructions:
            canonical.write(instruction)
            canonical.write("\n")
        canonical.write("<dic>\n")
        rows = connection.execute(
            """
            SELECT * FROM entries
             WHERE import_run_id=?
             ORDER BY source_ordinal
            """,
            (active_run["id"],),
        )
        for row in rows:
            try:
                document = build_public_document(
                    row, connection, release_id, lemma_index
                )
                _validate_public_document(document)
                target = dictionary if row["resource"] == "dictionary" else vocabulary
                target.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
                target.write("\n")
                canonical.write(row["raw_xml"])
                canonical.write("\n")
                counts["entries"] += 1
                counts[row["resource"]] += 1
            except Exception as exc:
                issues.append(
                    {
                        "severity": "error",
                        "rule": "PUBLIC_PROJECTION",
                        "entry_id": row["public_id"],
                        "message": str(exc),
                    }
                )
        canonical.write("</dic>\n")

    structured_counts = {
        "forms": _active_child_count(connection, "forms", active_run["id"]),
        "senses": _active_child_count(connection, "senses", active_run["id"])
        + int(
            connection.execute(
                """
                SELECT COUNT(*) FROM enrichments
                JOIN entries ON entries.id=enrichments.entry_id
                WHERE entries.import_run_id=?
                  AND enrichments.approval_status IN ('approved','imported')
                  AND EXISTS (
                    SELECT 1 FROM external_sources
                     WHERE external_sources.code=enrichments.source_code
                       AND external_sources.publication_enabled=1
                  )
                """,
                (active_run["id"],),
            ).fetchone()[0]
        ),
        "definitions": int(
            connection.execute(
                """
                SELECT COUNT(*) FROM senses
                JOIN entries ON entries.id=senses.entry_id
                WHERE entries.import_run_id=?
                  AND (
                    NULLIF(TRIM(senses.definition),'') IS NOT NULL
                    OR NULLIF(TRIM(senses.gloss),'') IS NOT NULL
                  )
                """,
                (active_run["id"],),
            ).fetchone()[0]
        )
        + int(
            connection.execute(
                """
                SELECT COUNT(*) FROM enrichments
                JOIN entries ON entries.id=enrichments.entry_id
                WHERE entries.import_run_id=?
                  AND enrichments.approval_status IN ('approved','imported')
                  AND EXISTS (
                    SELECT 1 FROM external_sources
                     WHERE external_sources.code=enrichments.source_code
                       AND external_sources.publication_enabled=1
                  )
                  AND NULLIF(TRIM(enrichments.definition),'') IS NOT NULL
                """,
                (active_run["id"],),
            ).fetchone()[0]
        ),
        "relations": _active_child_count(
            connection, "relations", active_run["id"]
        ),
        "labels": _active_child_count(
            connection, "labels", active_run["id"]
        ),
    }
    counts.update(structured_counts)
    external_sources = [
        {
            **dict(row),
            "source_records": int(
                connection.execute(
                    "SELECT COUNT(*) FROM unmatched_enrichments WHERE source_code=?",
                    (row["code"],),
                ).fetchone()[0]
            )
            + int(
                connection.execute(
                    "SELECT COUNT(*) FROM enrichments WHERE source_code=?",
                    (row["code"],),
                ).fetchone()[0]
            ),
            "imported_entries": int(
                connection.execute(
                    """SELECT COUNT(*) FROM enrichments
                        WHERE source_code=?
                          AND approval_status IN ('approved','imported')""",
                    (row["code"],),
                ).fetchone()[0]
            ),
            "expected_entries": 3565 if row["code"] == "SPE" else None,
            "source_code": row["code"],
            "source_license": row["license"],
        }
        for row in connection.execute(
            "SELECT * FROM external_sources WHERE publication_enabled=1 ORDER BY code"
        ).fetchall()
    ]

    contracts = Path(contracts_root) if contracts_root else _project_root() / "contracts"
    for filename in ("dictionary-settings.json", "vocabulary-settings.json"):
        (release_path / filename).write_bytes((contracts / filename).read_bytes())
    if images_root:
        source_images = Path(images_root)
        if not source_images.is_dir():
            issues.append(
                {
                    "severity": "warning",
                    "rule": "IMAGES_ROOT_MISSING",
                    "message": f"Diretório de imagens inexistente: {source_images}",
                }
            )
        else:
            target_images = release_path / "images"
            if not (resume and target_images.is_dir()):
                shutil.copytree(source_images, target_images)

    canonical_validation = validate_canonical_xml(canonical_path, rng_path=rng_path)
    if not canonical_validation.get("xml_id_unique", True):
        issues.append(
            {
                "severity": "warning",
                "rule": "DUPLICATE_GLOBAL_XML_ID",
                "message": (
                    "O consolidado reutiliza xml:id entre recursos; os "
                    "fragmentos foram preservados sem alteração."
                ),
                "details": canonical_validation["duplicate_xml_ids"],
            }
        )
    if not canonical_validation["well_formed"] or canonical_validation["rng_valid"] is False:
        issues.append(
            {
                "severity": "error",
                "rule": "CANONICAL_XML_INVALID",
                "message": "O XML canónico não passou a validação final.",
                "details": canonical_validation["errors"],
            }
        )
    issue_counts = {}
    for row in connection.execute(
        """
        SELECT severity, rule_code, COUNT(*) AS count FROM validation_issues
         WHERE import_run_id=? GROUP BY severity, rule_code
         ORDER BY severity, rule_code
        """,
        (active_run["id"],),
    ):
        issue_counts.setdefault(row["severity"], {})[row["rule_code"]] = row["count"]
    validation_report = {
        "release_id": release_id,
        "valid": not any(item["severity"] == "error" for item in issues),
        "counts": counts,
        "external_sources": external_sources,
        "quality": {
            "missing_xml_id": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM entries
                    WHERE import_run_id=? AND public_id LIKE 'generated-%'
                    """,
                    (active_run["id"],),
                ).fetchone()[0]
            ),
            "missing_lemma": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM entries
                    WHERE import_run_id=? AND TRIM(lemma)=''
                    """,
                    (active_run["id"],),
                ).fetchone()[0]
            ),
            "missing_grammar": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM entries
                    WHERE import_run_id=?
                      AND NULLIF(TRIM(grammatical_info),'') IS NULL
                    """,
                    (active_run["id"],),
                ).fetchone()[0]
            ),
            "missing_source_status": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM entries
                    WHERE import_run_id=?
                      AND NULLIF(TRIM(editorial_status),'') IS NULL
                    """,
                    (active_run["id"],),
                ).fetchone()[0]
            ),
        },
        "issues": issues,
        "rules": issue_counts,
        "summary": validation.as_dict(),
        "validation_waivers": [
            dict(row)
            for row in connection.execute(
                """
                SELECT e.public_id AS entry_id, vw.rule_code, vw.reason,
                       vw.actor, vw.entry_sha256, vw.created_at
                  FROM validation_waivers vw
                  JOIN entries e ON e.id=vw.entry_id
                 WHERE vw.import_run_id=? AND vw.revoked_at IS NULL
                   AND vw.entry_sha256=e.raw_sha256
                 ORDER BY e.public_id, vw.rule_code
                """,
                (active_run["id"],),
            ).fetchall()
        ],
        "canonical_xml": canonical_validation,
        "fidelity": {
            "source_entries": active_run["entry_count"],
            "exported_entries": counts["entries"],
            "entry_count_equal": active_run["entry_count"] == counts["entries"],
            "unchanged_entry_hashes": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM entries WHERE import_run_id=?
                     AND raw_sha256=imported_raw_sha256
                    """,
                    (active_run["id"],),
                ).fetchone()[0]
            ),
            "edited_entry_hashes": int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM entries WHERE import_run_id=?
                     AND raw_sha256<>imported_raw_sha256
                    """,
                    (active_run["id"],),
                ).fetchone()[0]
            ),
        },
    }
    _write_json(release_path / "validation-report.json", validation_report)
    manifest = {
        "release_id": release_id,
        "state": "validated" if validation_report["valid"] else "prepared",
        "contract_version": "1",
        "generator_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": active_run["source_path"],
            "sha256": active_run["source_sha256"],
            "import_run_id": active_run["id"],
        },
        "counts": counts,
        "external_sources": external_sources,
        "quality": validation_report["quality"],
        "prepared_by": prepared_by,
        "description": description,
        "indexes": {
            "dictionary": f"dictionary__{_safe_index_suffix(release_id)}",
            "vocabulary": f"vocabulary__{_safe_index_suffix(release_id)}",
        },
        "files": _file_inventory(release_path),
    }
    _write_json(release_path / "manifest.json", manifest)
    connection.execute(
        """
        INSERT INTO releases(
            release_id, import_run_id, state, manifest_path,
            description, prepared_by, report_json
        ) VALUES (?, ?, 'candidate', ?, ?, ?, ?)
        """,
        (
            release_id,
            active_run["id"],
            str((release_path / "manifest.json").resolve()),
            description,
            prepared_by,
            json.dumps(validation_report, ensure_ascii=False),
        ),
    )
    connection.execute(
        """
        INSERT INTO audit_events(
            event_type, actor, release_id, resulting_state, comment, details_json
        ) VALUES ('RELEASE_PREPARED', ?, ?, 'candidate', ?, ?)
        """,
        (
            prepared_by,
            release_id,
            description,
            json.dumps(validation.as_dict(), ensure_ascii=False),
        ),
    )
    connection.commit()
    connection.close()
    return PublicationResult(release_id, release_path, counts["entries"], len(issues))


def verify_release(release_path: str | Path) -> dict:
    root = Path(release_path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    failures = []
    for item in manifest["files"]:
        path = root / item["path"]
        if not path.is_file():
            failures.append({"path": item["path"], "reason": "missing"})
            continue
        actual_size = path.stat().st_size
        if actual_size != item["bytes"]:
            failures.append(
                {
                    "path": item["path"],
                    "reason": "size",
                    "expected": item["bytes"],
                    "actual": actual_size,
                }
            )
            continue
        actual = _sha256(path)
        if actual != item["sha256"]:
            failures.append(
                {
                    "path": item["path"],
                    "reason": "checksum",
                    "expected": item["sha256"],
                    "actual": actual,
                }
            )
    return {
        "release_id": manifest["release_id"],
        "valid": not failures,
        "failures": failures,
        "counts": manifest["counts"],
    }


def integrity_failure_message(verification: dict, *, limit: int = 5) -> str:
    """Explica uma falha sem obrigar o operador a consultar logs do servidor."""
    labels = {
        "missing": "ficheiro em falta",
        "size": "tamanho diferente",
        "checksum": "conteúdo/checksum diferente",
    }
    failures = verification.get("failures") or []
    details = []
    for failure in failures[:limit]:
        path = failure.get("path", "(ficheiro desconhecido)")
        reason = labels.get(failure.get("reason"), failure.get("reason", "falha"))
        detail = f"{path}: {reason}"
        if failure.get("reason") == "size":
            detail += (
                f" (esperado {failure.get('expected')} bytes; "
                f"encontrado {failure.get('actual')} bytes)"
            )
        elif failure.get("reason") == "checksum":
            detail += (
                f" (esperado {str(failure.get('expected', ''))[:12]}…; "
                f"encontrado {str(failure.get('actual', ''))[:12]}…)"
            )
        details.append(detail)
    remaining = len(failures) - len(details)
    if remaining > 0:
        details.append(f"e mais {remaining} ficheiros")
    return "; ".join(details) or "o manifesto ou o pacote está incompleto"


def activate_local_release(
    db_path: str | Path,
    releases_root: str | Path,
    release_id: str,
    *,
    verify_integrity: bool = True,
) -> Path:
    root = Path(releases_root)
    release_path = root / release_id
    if verify_integrity:
        verification = verify_release(release_path)
        if not verification["valid"]:
            raise RuntimeError(
                "A release não passou a verificação de checksums: "
                + integrity_failure_message(verification)
            )
    previous = current_release(releases_root)
    connection = connect(db_path)
    row = connection.execute(
        "SELECT state FROM releases WHERE release_id=?", (release_id,)
    ).fetchone()
    if row is None:
        connection.close()
        raise RuntimeError("A release não está registada.")
    if row["state"] not in {"approved", "indexed", "tested", "active", "archived"}:
        connection.close()
        raise RuntimeError("A release ainda não foi aprovada.")
    pointer = root / "ACTIVE_RELEASE"
    temporary = root / ".ACTIVE_RELEASE.tmp"
    temporary.write_text(release_id + "\n", encoding="utf-8")
    temporary.replace(pointer)
    connection.execute(
        "UPDATE releases SET state='archived' WHERE state='active'"
    )
    connection.execute(
        """
        UPDATE releases
           SET state='active', activated_at=CURRENT_TIMESTAMP,
               previous_release_id=?
         WHERE release_id=?
        """,
        (previous, release_id),
    )
    connection.commit()
    connection.close()
    return pointer


def approve_release(
    db_path: str | Path,
    releases_root: str | Path,
    release_id: str,
    *,
    actor: str,
    comment: str = "",
) -> dict:
    GovernanceService(db_path).require_user(
        actor, {"approver", "administrator"}
    )
    verification = verify_release(Path(releases_root) / release_id)
    if not verification["valid"]:
        raise RuntimeError(
            "A release não passou a verificação de integridade: "
            + integrity_failure_message(verification)
        )
    connection = connect(db_path)
    row = connection.execute(
        "SELECT * FROM releases WHERE release_id=?", (release_id,)
    ).fetchone()
    if row is None or row["state"] != "candidate":
        connection.close()
        raise RuntimeError("A release não está no estado de candidata.")
    connection.execute(
        """
        UPDATE releases SET state='approved', approved_by=?,
               approved_at=CURRENT_TIMESTAMP, decision_comment=?
         WHERE release_id=?
        """,
        (actor, comment or None, release_id),
    )
    connection.execute(
        """
        INSERT INTO audit_events(
            event_type, actor, release_id, previous_state,
            resulting_state, comment
        ) VALUES ('RELEASE_APPROVED', ?, ?, 'candidate', 'approved', ?)
        """,
        (actor, release_id, comment or None),
    )
    connection.commit()
    result = dict(
        connection.execute(
            "SELECT * FROM releases WHERE release_id=?", (release_id,)
        ).fetchone()
    )
    connection.close()
    return result


def set_release_state(
    db_path: str | Path,
    release_id: str,
    state: str,
    *,
    actor: str,
    comment: str = "",
) -> None:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT state FROM releases WHERE release_id=?", (release_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("Release inexistente.")
        connection.execute(
            "UPDATE releases SET state=? WHERE release_id=?", (state, release_id)
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                event_type, actor, release_id, previous_state,
                resulting_state, comment
            ) VALUES ('RELEASE_STATE', ?, ?, ?, ?, ?)
            """,
            (actor, release_id, row["state"], state, comment or None),
        )


def release_records(db_path: str | Path) -> list[dict]:
    initialize(db_path)
    with connect(db_path) as connection:
        output = []
        for row in connection.execute(
            "SELECT * FROM releases ORDER BY created_at DESC"
        ):
            item = dict(row)
            report = item.pop("report_json", None)
            item["report"] = json.loads(report) if report else None
            output.append(item)
        return output


def current_release(releases_root: str | Path) -> str | None:
    pointer = Path(releases_root) / "ACTIVE_RELEASE"
    return pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else None


def _validate_public_document(document: dict) -> None:
    required = (
        "id",
        "source_id",
        "resource",
        "lemma",
        "lemma_normalized",
        "forms",
        "senses",
        "publication_version",
        "source_sha256",
    )
    missing = [key for key in required if key not in document]
    if missing:
        raise ValueError(f"Campos públicos em falta: {', '.join(missing)}")
    if document["resource"] not in {"dictionary", "vocabulary"}:
        raise ValueError("Recurso público inválido.")
    if len(document["source_sha256"]) != 64:
        raise ValueError("Checksum de origem inválido.")


def _file_inventory(root: Path) -> list[dict]:
    output = []
    for path in sorted(root.rglob("*")):
        if path.name == "manifest.json" or not path.is_file():
            continue
        output.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _safe_index_suffix(release_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", release_id)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _active_child_count(
    connection: sqlite3.Connection, table: str, import_run_id: int
) -> int:
    return int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM {table}
            JOIN entries ON entries.id={table}.entry_id
            WHERE entries.import_run_id=?
            """,
            (import_run_id,),
        ).fetchone()[0]
    )
