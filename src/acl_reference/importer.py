from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import lzma
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET

from .editorial_db import connect, initialize
from .normalization import clean_text, search_key
from .xml_stream import iter_entry_xml
from .governance import synchronize_controlled_values

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass(frozen=True)
class ImportResult:
    run_id: int
    imported: int
    errors: int


def import_xml(
    source: str | Path,
    db_path: str | Path,
    *,
    limit: int | None = None,
    batch_size: int = 500,
) -> ImportResult:
    path = Path(source).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if limit is not None and limit < 1:
        raise ValueError("O limite deve ser positivo.")
    initialize(db_path)
    source_sha = _file_sha256(path)
    connection = connect(db_path)
    cursor = connection.execute(
        """
        INSERT INTO import_runs(
            source_path, source_sha256, source_processing_instructions
        ) VALUES (?, ?, ?)
        """,
        (str(path), source_sha, json.dumps(processing_instructions(path))),
    )
    run_id = int(cursor.lastrowid)
    connection.commit()
    imported = errors = 0
    try:
        for ordinal, fragment in enumerate(iter_entry_xml(path), 1):
            if limit is not None and ordinal > limit:
                break
            try:
                _insert_entry(connection, run_id, ordinal, fragment)
                imported += 1
            except (ET.ParseError, ValueError, sqlite3.Error) as exc:
                errors += 1
                connection.execute(
                    """
                    INSERT INTO validation_issues(
                        import_run_id, severity, rule_code, message
                    ) VALUES (?, 'error', 'IMPORT_ENTRY', ?)
                    """,
                    (run_id, f"Entrada {ordinal}: {exc}"),
                )
            if ordinal % batch_size == 0:
                connection.commit()
        connection.execute("UPDATE import_runs SET is_active = 0")
        connection.execute(
            """
            UPDATE import_runs
               SET finished_at=CURRENT_TIMESTAMP, entry_count=?, error_count=?,
                   status='completed', is_active=1
             WHERE id=?
            """,
            (imported, errors, run_id),
        )
        connection.commit()
        connection.close()
        synchronize_controlled_values(db_path)
        connection = None
        return ImportResult(run_id, imported, errors)
    except Exception:
        connection.execute(
            """
            UPDATE import_runs
               SET finished_at=CURRENT_TIMESTAMP, entry_count=?, error_count=?,
                   status='failed'
             WHERE id=?
            """,
            (imported, errors, run_id),
        )
        connection.commit()
        raise
    finally:
        if connection is not None:
            connection.close()


def _insert_entry(
    connection: sqlite3.Connection, run_id: int, ordinal: int, fragment: str
) -> None:
    root = ET.fromstring(fragment)
    if _local(root.tag) != "entry":
        raise ValueError("Fragmento sem elemento entry.")
    public_id = root.get(XML_ID) or f"generated-{ordinal}"
    resource = "vocabulary" if root.get("volp") == "only" else "dictionary"
    orths = [node for node in root.iter() if _local(node.tag) == "orth"]
    lemma = _text(orths[0]) if orths else ""
    gram = next((_text(node) for node in root if _local(node.tag) == "gramGrp"), "")
    status = _editorial_status(root)
    raw_sha = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
    entry_cursor = connection.execute(
        """
        INSERT INTO entries(
            import_run_id, source_ordinal, public_id, resource, lemma,
            lemma_normalized, grammatical_info, editorial_status, raw_xml,
            raw_sha256, imported_raw_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            ordinal,
            public_id,
            resource,
            lemma,
            search_key(lemma),
            gram or None,
            status,
            fragment,
            raw_sha,
            raw_sha,
        ),
    )
    entry_id = int(entry_cursor.lastrowid)
    for position, orth in enumerate(orths, 1):
        value = _text(orth)
        if value:
            connection.execute(
                """
                INSERT INTO forms(
                    entry_id, position, value, value_normalized, language, kind
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    position,
                    value,
                    search_key(value),
                    orth.get(XML_LANG),
                    None,
                ),
            )
    _insert_senses(connection, entry_id, root, None, ())


def _insert_senses(
    connection: sqlite3.Connection,
    entry_id: int,
    parent: ET.Element,
    parent_id: int | None,
    prefix: tuple[int, ...],
) -> None:
    children = _child_senses(parent)
    for index, sense in enumerate(children, 1):
        path = (*prefix, index)
        definition = next(
            (_text(node) for node in sense if _local(node.tag) == "def"), ""
        )
        gloss = next(
            (_text(node) for node in sense if _local(node.tag) == "gloss"), ""
        )
        cursor = connection.execute(
            """
            INSERT INTO senses(
                entry_id, parent_id, public_id, position_path, number_label,
                definition, gloss, raw_xml
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                parent_id,
                sense.get(XML_ID),
                ".".join(map(str, path)),
                sense.get("n"),
                definition or None,
                gloss or None,
                ET.tostring(sense, encoding="unicode"),
            ),
        )
        sense_id = int(cursor.lastrowid)
        for node in sense.iter():
            local = _local(node.tag)
            if local == "ref" and _text(node):
                connection.execute(
                    """
                    INSERT INTO relations(
                        entry_id, sense_id, relation_type, target_text, target_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        sense_id,
                        node.get("type"),
                        _text(node),
                        node.get("target"),
                    ),
                )
            elif local == "usg" and _text(node):
                connection.execute(
                    """
                    INSERT INTO labels(
                        entry_id, sense_id, label_type, value, value_normalized
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        sense_id,
                        node.get("type") or "usage",
                        _text(node),
                        search_key(_text(node)),
                    ),
                )
        _insert_senses(connection, entry_id, sense, sense_id, path)


def _child_senses(parent: ET.Element) -> list[ET.Element]:
    """Devolve sentidos cujo antepassado sense mais próximo é ``parent``."""
    output: list[ET.Element] = []
    for child in parent:
        if _local(child.tag) == "sense":
            output.append(child)
        else:
            output.extend(_child_senses(child))
    return output


def _editorial_status(root: ET.Element) -> str | None:
    for node in root.iter():
        if _local(node.tag) == "meta" and node.get("status"):
            return node.get("status")
    return None


def _text(node: ET.Element | None) -> str:
    return clean_text("".join(node.itertext())) if node is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def processing_instructions(path: str | Path) -> list[str]:
    """Preserva instruções de modelo encontradas fora das entradas."""
    path = Path(path)
    opener = lzma.open if path.suffix == ".xz" else open
    pattern = re.compile(r"<\?xml-model\s+.*?\?>", re.DOTALL)
    found: list[str] = []
    tail = ""
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        while chunk := handle.read(1024 * 1024):
            block = tail + chunk
            for match in pattern.findall(block):
                compact = " ".join(match.split())
                if compact not in found:
                    found.append(compact)
            tail = block[-4096:]
    return found
