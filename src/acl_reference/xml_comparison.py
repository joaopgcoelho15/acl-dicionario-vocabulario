from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .editorial_db import connect, initialize
from .normalization import clean_text
from .workflow import workflow_from_xml_status
from .xml_stream import iter_entry_xml


XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_IGNORED_META_ATTRIBUTES = {"status", "origin", "transitionedAt"}
_PREVIEW_LIMIT = 1000


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_xml_with_database(
    source: str | Path,
    db_path: str | Path,
    output_dir: str | Path,
    *,
    source_label: str | None = None,
) -> dict:
    """Compara um XML sem alterar a base e produz um CSV integral das diferenças."""
    path = Path(source).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    initialize(db_path)
    source_sha = file_sha256(path)
    current: dict[str, dict] = {}
    with connect(db_path) as connection:
        run = connection.execute(
            "SELECT id,source_sha256 FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            raise ValueError("Não existe um conjunto de dados ativo para comparar.")
        for row in connection.execute(
            """
            SELECT public_id,resource,raw_xml,workflow_status
              FROM entries WHERE import_run_id=?
            """,
            (run["id"],),
        ):
            current[row["public_id"]] = _snapshot(
                row["raw_xml"], workflow_override=row["workflow_status"]
            )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_name = f"comparacao-xml-{stamp}-{source_sha[:8]}.csv"
    csv_path = output / csv_name
    preview: list[dict] = []
    summary = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    seen: set[str] = set()
    incoming = 0

    try:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_handle:
            writer = csv.writer(csv_handle, delimiter=";")
            writer.writerow(
                [
                    "Tipo",
                    "Identificador",
                    "Lema na base",
                    "Lema no XML",
                    "Recurso",
                    "Campos alterados",
                ]
            )
            for ordinal, fragment in enumerate(iter_entry_xml(path), 1):
                incoming += 1
                root = ET.fromstring(fragment)
                public_id = root.get(XML_ID) or f"generated-{ordinal}"
                if public_id in seen:
                    raise ValueError(f"O XML repete o identificador {public_id}.")
                seen.add(public_id)
                uploaded = _snapshot(fragment)
                existing = current.pop(public_id, None)
                if existing is None:
                    difference = _difference("added", public_id, None, uploaded)
                    summary["added"] += 1
                    _write_difference(writer, difference)
                    if len(preview) < _PREVIEW_LIMIT:
                        preview.append(difference)
                elif existing["signature"] == uploaded["signature"]:
                    summary["unchanged"] += 1
                else:
                    difference = _difference("changed", public_id, existing, uploaded)
                    summary["changed"] += 1
                    _write_difference(writer, difference)
                    if len(preview) < _PREVIEW_LIMIT:
                        preview.append(difference)

            for public_id, existing in current.items():
                difference = _difference("removed", public_id, existing, None)
                summary["removed"] += 1
                _write_difference(writer, difference)
                if len(preview) < _PREVIEW_LIMIT:
                    preview.append(difference)
    except Exception:
        csv_path.unlink(missing_ok=True)
        raise

    different = summary["added"] + summary["removed"] + summary["changed"]
    return {
        "source_name": source_label or path.name,
        "source_sha256": source_sha,
        "current_source_sha256": run["source_sha256"],
        "current_entries": sum(summary.values()) - summary["added"],
        "xml_entries": incoming,
        "summary": summary,
        "different": different,
        "preview": preview,
        "preview_limit": _PREVIEW_LIMIT,
        "preview_truncated": different > len(preview),
        "csv_name": csv_name,
        "compared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _snapshot(fragment: str, workflow_override: str | None = None) -> dict:
    root = ET.fromstring(fragment)
    resource = "vocabulary" if root.get("volp") == "only" else "dictionary"
    status = next(
        (
            node.get("status")
            for node in root.iter()
            if _local(node.tag) == "meta" and node.get("status")
        ),
        None,
    )
    workflow = workflow_override or workflow_from_xml_status(status)
    orths = [_text(node) for node in root.iter() if _local(node.tag) == "orth"]
    grammar = next(
        (_text(node) for node in root if _local(node.tag) == "gramGrp"), ""
    )
    senses = [
        (_local(node.tag), _text(node))
        for node in root.iter()
        if _local(node.tag) in {"def", "gloss"}
    ]
    labels = [
        (node.get("type") or "", _text(node))
        for node in root.iter()
        if _local(node.tag) == "usg"
    ]
    relations = [
        (node.get("type") or "", node.get("target") or "", _text(node))
        for node in root.iter()
        if _local(node.tag) == "ref"
    ]
    xml_hash = _digest(_element_value(root))
    values = {
        "lemma": orths[0] if orths else "",
        "resource": resource,
        "grammar": grammar,
        "workflow": workflow,
        "forms_hash": _digest(orths),
        "senses_hash": _digest(senses),
        "labels_hash": _digest(labels),
        "relations_hash": _digest(relations),
        "xml_hash": xml_hash,
    }
    values["signature"] = _digest(values)
    return values


def _element_value(node: ET.Element):
    attributes = tuple(
        sorted(
            (key, " ".join(value.split()))
            for key, value in node.attrib.items()
            if not (_local(node.tag) == "meta" and _local(key) in _IGNORED_META_ATTRIBUTES)
        )
    )
    children = tuple(
        value
        for child in node
        if (value := _element_value(child)) is not None
    )
    text = " ".join((node.text or "").split())
    tail = " ".join((node.tail or "").split())
    if _local(node.tag) == "meta" and not attributes and not children and not text:
        return None
    return (node.tag, attributes, text, children, tail)


def _difference(kind: str, public_id: str, current: dict | None, uploaded: dict | None) -> dict:
    fields: list[str] = []
    if current and uploaded:
        comparisons = (
            ("lemma", "lema"),
            ("resource", "recurso"),
            ("grammar", "classe gramatical"),
            ("workflow", "estado editorial"),
            ("forms_hash", "formas e variantes"),
            ("senses_hash", "aceções e definições"),
            ("labels_hash", "marcas e domínios"),
            ("relations_hash", "relações e remissões"),
        )
        fields.extend(label for key, label in comparisons if current[key] != uploaded[key])
        if current["xml_hash"] != uploaded["xml_hash"]:
            fields.append("estrutura/conteúdo XML")
    return {
        "type": kind,
        "public_id": public_id,
        "current_lemma": current["lemma"] if current else "",
        "xml_lemma": uploaded["lemma"] if uploaded else "",
        "resource": (uploaded or current or {}).get("resource", ""),
        "fields": fields,
    }


def _write_difference(writer, difference: dict) -> None:
    labels = {"added": "Adicionada", "removed": "Removida", "changed": "Alterada"}
    writer.writerow(
        [
            labels[difference["type"]],
            difference["public_id"],
            difference["current_lemma"],
            difference["xml_lemma"],
            "Dicionário" if difference["resource"] == "dictionary" else "Vocabulário",
            ", ".join(difference["fields"]),
        ]
    )


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(node: ET.Element) -> str:
    return clean_text("".join(node.itertext()))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
