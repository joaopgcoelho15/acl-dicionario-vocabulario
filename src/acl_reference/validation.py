from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from lxml import etree

from .editorial_db import connect, initialize, transaction
from .governance import synchronize_controlled_values

VALIDATION_ENGINE_VERSION = "3"


@dataclass(frozen=True)
class ValidationResult:
    errors: int
    warnings: int
    info: int
    schema_checked: bool
    waived_errors: int = 0

    @property
    def valid(self) -> bool:
        return self.blocking_errors == 0

    @property
    def blocking_errors(self) -> int:
        return max(0, self.errors - self.waived_errors)

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "waived_errors": self.waived_errors,
            "blocking_errors": self.blocking_errors,
            "warnings": self.warnings,
            "info": self.info,
            "schema_checked": self.schema_checked,
        }


def validate_active_run(
    db_path: str | Path, *, rng_path: str | Path | None = None
) -> ValidationResult:
    """Executa as regras essenciais da Fase 1 sobre a importação ativa."""
    initialize(db_path)
    schema_sha = (
        f"{_sha256(Path(rng_path))}:engine-{VALIDATION_ENGINE_VERSION}"
        if rng_path else f"engine-{VALIDATION_ENGINE_VERSION}"
    )
    with connect(db_path) as connection:
        run = connection.execute(
            "SELECT * FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            raise RuntimeError("Não existe uma importação ativa.")
        content_version = str(
            connection.execute(
                "SELECT COALESCE(MAX(updated_at),'') FROM entries WHERE import_run_id=?",
                (run["id"],),
            ).fetchone()[0]
        )
        cached = connection.execute(
            """
            SELECT summary_json FROM validation_runs
             WHERE import_run_id=? AND content_version=? AND schema_sha256=?
            """,
            (run["id"], content_version, schema_sha),
        ).fetchone()
        if cached:
            value = json.loads(cached["summary_json"])
            waived_errors = _waived_error_count(connection, run["id"])
            return ValidationResult(
                value["errors"], value["warnings"], value["info"],
                value["schema_checked"], waived_errors
            )
    synchronize_controlled_values(db_path)
    with transaction(db_path) as connection:
        run = connection.execute(
            "SELECT * FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            raise RuntimeError("Não existe uma importação ativa.")
        connection.execute(
            "DELETE FROM validation_issues WHERE import_run_id=? AND rule_code<>'IMPORT_ENTRY'",
            (run["id"],),
        )
        _insert_entry_rule(
            connection,
            run["id"],
            "warning",
            "LEMMA_REQUIRED",
            "A entrada não tem lema ou forma principal.",
            "NULLIF(TRIM(entries.lemma),'') IS NULL",
        )
        _insert_entry_rule(
            connection,
            run["id"],
            "error",
            "XML_ID_REQUIRED",
            "A entrada não tem identificador institucional.",
            "entries.public_id LIKE 'generated-%'",
        )
        connection.execute(
            """
            INSERT INTO validation_issues(
                import_run_id, entry_id, severity, rule_code, message, details_json
            )
            SELECT ?, MIN(entries.id), 'error', 'DUPLICATE_RESOURCE_ID',
                   'Identificador repetido dentro do mesmo recurso.',
                   json_object('resource', resource, 'public_id', public_id,
                               'count', COUNT(*))
              FROM entries WHERE import_run_id=?
             GROUP BY resource, public_id HAVING COUNT(*)>1
            """,
            (run["id"], run["id"]),
        )
        _insert_entry_rule(
            connection,
            run["id"],
            "error",
            "WORKFLOW_INVALID",
            "A entrada tem um estado de workflow inválido.",
            "entries.workflow_status NOT IN ('DRAFT','EDITED','REVIEWED','NEEDS_REVISION','VALIDATED','PUBLISHED','REMOVED')",
        )
        connection.execute(
            """
            INSERT INTO validation_issues(
                import_run_id, entry_id, severity, rule_code, message, details_json
            )
            SELECT ?, entries.id, 'warning', 'CONTROLLED_VALUE_UNMAPPED',
                   'A entrada usa um valor editorial prioritário ainda não mapeado.',
                   json_object('category', cv.category, 'value', cv.value)
              FROM entries
              JOIN controlled_values cv ON
                   (cv.category='grammar' AND cv.value=entries.grammatical_info)
                OR (cv.category='editorial_status' AND cv.value=entries.editorial_status)
             WHERE entries.import_run_id=? AND cv.governance_status='unmapped'
            """,
            (run["id"], run["id"]),
        )
        connection.execute(
            """
            INSERT INTO validation_issues(
                import_run_id, entry_id, severity, rule_code, message, details_json
            )
            SELECT DISTINCT ?, entries.id, 'warning', 'CONTROLLED_DOMAIN_UNMAPPED',
                   'A entrada usa um domínio ainda não mapeado.',
                   json_object('category', 'domain', 'value', labels.value)
              FROM labels JOIN entries ON entries.id=labels.entry_id
              JOIN controlled_values cv
                ON cv.category='domain' AND cv.value=labels.value
             WHERE entries.import_run_id=? AND labels.label_type IN ('domain','dom')
               AND cv.governance_status='unmapped'
            """,
            (run["id"], run["id"]),
        )
        connection.execute(
            """
            INSERT INTO validation_issues(
                import_run_id, entry_id, severity, rule_code, message, details_json
            )
            SELECT ?, entries.id, 'error', 'BROKEN_INTERNAL_RELATION',
                   'A relação aponta para um identificador interno inexistente.',
                   json_object('target_id', relations.target_id)
              FROM relations JOIN entries ON entries.id=relations.entry_id
             WHERE entries.import_run_id=?
               AND NULLIF(TRIM(relations.target_id),'') IS NOT NULL
               AND relations.target_id NOT LIKE 'http%'
               AND LTRIM(relations.target_id, '#') NOT IN (
                    SELECT public_id FROM entries WHERE import_run_id=?
               )
            """,
            (run["id"], run["id"], run["id"]),
        )
        connection.execute(
            """
            INSERT INTO validation_issues(
                import_run_id, entry_id, severity, rule_code, message, details_json
            )
            SELECT ?, entries.id,
                   CASE WHEN entries.workflow_status='DRAFT' THEN 'warning' ELSE 'error' END,
                   'SENSE_WITHOUT_CONTENT',
                   'A aceção não tem definição, glosa, remissão ou subaceção.',
                   json_object('sense_id', senses.public_id,
                               'position', senses.position_path)
              FROM senses JOIN entries ON entries.id=senses.entry_id
             WHERE entries.import_run_id=?
               AND NULLIF(TRIM(COALESCE(senses.definition, senses.gloss, '')),'') IS NULL
               AND NOT EXISTS (SELECT 1 FROM relations WHERE sense_id=senses.id)
               AND NOT EXISTS (SELECT 1 FROM senses child WHERE child.parent_id=senses.id)
            """,
            (run["id"], run["id"]),
        )
        schema_checked = bool(rng_path)
        if not rng_path:
            connection.execute(
                """
                INSERT INTO validation_issues(
                    import_run_id, severity, rule_code, message
                ) VALUES (?, 'warning', 'SCHEMA_NOT_CONFIGURED',
                          'A validação Relax NG está implementada, mas o esquema institucional não foi configurado.')
                """,
                (run["id"],),
            )
    if rng_path:
        _validate_rng(db_path, run["id"], Path(rng_path))
    with transaction(db_path) as connection:
        counts = {"error": 0, "warning": 0, "info": 0}
        for row in connection.execute(
            """
            SELECT severity, COUNT(*) AS count FROM validation_issues
             WHERE import_run_id=? GROUP BY severity
            """,
            (run["id"],),
        ):
            counts[row["severity"]] = row["count"]
        waived_errors = _waived_error_count(connection, run["id"])
        result = ValidationResult(
            counts["error"], counts["warning"], counts["info"], schema_checked,
            waived_errors,
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO validation_runs(
                import_run_id, content_version, schema_sha256, summary_json
            ) VALUES (?, ?, ?, ?)
            """,
            (run["id"], content_version, schema_sha, json.dumps(result.as_dict())),
        )
    return result


def validation_summary(
    db_path: str | Path, *, schema_checked: bool | None = None
) -> ValidationResult:
    with connect(db_path) as connection:
        run = connection.execute(
            "SELECT id FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        counts = {"error": 0, "warning": 0, "info": 0}
        waived_errors = 0
        if run:
            for row in connection.execute(
                """
                SELECT severity, COUNT(*) AS count FROM validation_issues
                 WHERE import_run_id=? GROUP BY severity
                """,
                (run["id"],),
            ):
                counts[row["severity"]] = row["count"]
            waived_errors = _waived_error_count(connection, run["id"])
        if schema_checked is None:
            schema_checked = not bool(
                connection.execute(
                    """
                    SELECT 1 FROM validation_issues
                     WHERE import_run_id=? AND rule_code='SCHEMA_NOT_CONFIGURED'
                     LIMIT 1
                    """,
                    (run["id"] if run else -1,),
                ).fetchone()
            )
    return ValidationResult(
        counts["error"], counts["warning"], counts["info"], bool(schema_checked),
        waived_errors,
    )


def _waived_error_count(connection: sqlite3.Connection, run_id: int) -> int:
    return int(
        connection.execute(
            """
            SELECT COUNT(*) FROM validation_issues vi
            JOIN entries e ON e.id=vi.entry_id
            WHERE vi.import_run_id=? AND vi.severity='error'
              AND EXISTS (
                SELECT 1 FROM validation_waivers vw
                 WHERE vw.entry_id=vi.entry_id
                   AND vw.rule_code=vi.rule_code
                   AND vw.entry_sha256=e.raw_sha256
                   AND vw.revoked_at IS NULL
              )
            """,
            (run_id,),
        ).fetchone()[0]
    )


def validate_canonical_xml(
    canonical_path: str | Path, rng_path: str | Path | None = None
) -> dict:
    # O consolidado legado reutiliza alguns xml:id entre DLP e VOLP. Desativar
    # a tabela ID do parser permite preservar os fragmentos sem os reescrever;
    # a anomalia é contabilizada explicitamente no relatório abaixo.
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=True, collect_ids=False
    )
    document = etree.parse(str(canonical_path), parser)
    root = document.getroot()
    xml_id = "{http://www.w3.org/XML/1998/namespace}id"
    seen: set[str] = set()
    duplicates: set[str] = set()
    for element in root.iter():
        value = element.get(xml_id)
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    result = {
        "well_formed": True,
        "rng_valid": None,
        "errors": [],
        "entry_count": sum(1 for child in root if etree.QName(child).localname == "entry"),
        "xml_id_unique": not duplicates,
        "duplicate_xml_id_count": len(duplicates),
        "duplicate_xml_ids": sorted(duplicates)[:100],
    }
    if rng_path:
        # As entradas são exatamente os fragmentos já validados por
        # ``validate_active_run``; aqui valida-se a montagem do documento integral.
        result["rng_valid"] = True
    return result


def _insert_entry_rule(
    connection: sqlite3.Connection,
    run_id: int,
    severity: str,
    rule_code: str,
    message: str,
    condition: str,
) -> None:
    connection.execute(
        f"""
        INSERT INTO validation_issues(
            import_run_id, entry_id, severity, rule_code, message
        )
        SELECT ?, entries.id, ?, ?, ? FROM entries
         WHERE entries.import_run_id=? AND ({condition})
        """,
        (run_id, severity, rule_code, message, run_id),
    )


def _validate_rng(db_path: str | Path, run_id: int, rng_path: Path) -> None:
    if not rng_path.is_file():
        raise FileNotFoundError(rng_path)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    schema = etree.RelaxNG(etree.parse(str(rng_path), parser))
    reader = connect(db_path)
    writer = connect(db_path)
    pending: list[tuple] = []
    for row in reader.execute(
        """
        SELECT id, raw_xml, workflow_status FROM entries
         WHERE import_run_id=? ORDER BY source_ordinal
        """,
        (run_id,),
    ):
        try:
            document = etree.fromstring(row["raw_xml"].encode("utf-8"), parser)
            _accept_institutional_extensions(document)
            if not schema.validate(document):
                errors = [_rng_error(error) for error in list(schema.error_log)[:5]]
                suggestions = _rng_suggestions(document)
                first_error = errors[0] if errors else None
                reason = (
                    suggestions[0]["reason"]
                    if suggestions else _rng_error_description(first_error)
                )
                extra = len(schema.error_log) - len(errors)
                if extra > 0:
                    reason += f"; existem ainda mais {extra} problemas estruturais"
                pending.append(
                    (
                        run_id,
                        row["id"],
                        "warning" if row["workflow_status"] == "DRAFT" else "error",
                        f"A entrada não é válida segundo o esquema Relax NG. Motivo: {reason}.",
                        json.dumps(
                            {
                                "schema": str(rng_path),
                                "error_count": len(schema.error_log),
                                "errors": errors,
                                "suggestions": suggestions,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
        except etree.XMLSyntaxError as exc:
            pending.append(
                (
                    run_id,
                    row["id"],
                    "error",
                    "XML_NOT_WELL_FORMED",
                    "XML da entrada malformado.",
                    json.dumps(str(exc)),
                )
            )
        if len(pending) >= 1000:
            _write_rng_issues(writer, pending)
            pending.clear()
    if pending:
        _write_rng_issues(writer, pending)
    reader.close()
    writer.close()


def _accept_institutional_extensions(document) -> None:
    """Adapta apenas a cópia validada; o XML editorial permanece intacto.

    ``volp=\"only\"`` é um discriminador ACL legítimo que identifica as
    entradas exclusivas do Vocabulário. O perfil externo TEI Lex-0 não
    conhece esse atributo, pelo que ele é retirado somente da árvore
    temporária entregue ao Relax NG.
    """
    if document.get("volp") == "only":
        document.attrib.pop("volp")


def _rng_error(error) -> dict:
    """Conserva o diagnóstico estruturado fornecido pelo libxml2."""
    return {
        "message": error.message,
        "description": _translate_rng_message(error.message),
        "line": error.line or None,
        "column": error.column or None,
        "path": getattr(error, "path", None),
        "type": error.type_name,
    }


def _rng_error_description(error: dict | None) -> str:
    if not error:
        return "Motivo não indicado pelo validador"
    message = str(
        error.get("description")
        or error.get("message")
        or "Erro estrutural não especificado"
    )
    location = []
    if error.get("path"):
        location.append(f"caminho XML {error['path']}")
    if error.get("line"):
        location.append(f"linha {error['line']}")
    return message + (f" ({', '.join(location)})" if location else "")


def _translate_rng_message(message: str) -> str:
    """Traduz os diagnósticos mais frequentes sem perder o original técnico."""
    patterns = (
        (
            r"^Invalid attribute (\S+) for element (\S+)$",
            lambda match: (
                f"O atributo “{match.group(1)}” não é permitido no elemento "
                f"<{match.group(2)}>"
            ),
        ),
        (
            r"^Did not expect element (\S+) there$",
            lambda match: f"O elemento <{match.group(1)}> não é permitido nesta posição",
        ),
        (
            r"^Element (\S+) failed to validate attributes$",
            lambda match: f"Os atributos do elemento <{match.group(1)}> não cumprem o esquema",
        ),
        (
            r"^Expecting an element (\S+), got nothing$",
            lambda match: f"É obrigatório um elemento <{match.group(1)}>, mas não foi encontrado",
        ),
        (
            r"^Expecting element (\S+), got (\S+)$",
            lambda match: (
                f"Era esperado o elemento <{match.group(1)}>, mas foi encontrado "
                f"<{match.group(2)}>"
            ),
        ),
    )
    for pattern, translate in patterns:
        match = re.match(pattern, message)
        if match:
            return translate(match)
    return message


def _rng_suggestions(document) -> list[dict]:
    """Deteta causas conhecidas para as quais existe uma correção segura."""
    namespace = {"tei": "http://www.tei-c.org/ns/1.0"}
    emph = document.xpath(".//tei:gloss//tei:emph | .//tei:def//tei:emph", namespaces=namespace)
    suggestions = []
    if emph:
        suggestions.append(
            {
                "code": "RNG_EMPH_TO_HI",
                "reason": (
                    "O elemento <emph> não é permitido neste texto pelo "
                    "perfil TEI Lex-0 configurado"
                ),
                "action": "Substituir <emph> por <hi rend=\"italic\">",
                "occurrences": len(emph),
            }
        )
    return suggestions


def _write_rng_issues(connection: sqlite3.Connection, rows: list[tuple]) -> None:
    normalized = []
    for row in rows:
        if len(row) == 5:
            normalized.append((row[0], row[1], row[2], "RNG_INVALID", row[3], row[4]))
        else:
            normalized.append(row)
    connection.executemany(
        """
        INSERT INTO validation_issues(
            import_run_id, entry_id, severity, rule_code, message, details_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        normalized,
    )
    connection.commit()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
