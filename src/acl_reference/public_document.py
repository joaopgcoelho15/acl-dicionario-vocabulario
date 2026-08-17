from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET

from .normalization import clean_text, search_key

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def build_public_document(
    row: sqlite3.Row,
    connection: sqlite3.Connection,
    release_id: str,
    lemma_index: dict[str, str] | None = None,
) -> dict:
    root = ET.fromstring(row["raw_xml"])
    forms = []
    for node in root.iter():
        if _local(node.tag) == "orth" and (value := _text(node)):
            forms.append(
                {
                    "value": value,
                    "normalized": search_key(value),
                    "language": node.get(XML_LANG),
                }
            )

    senses: list[dict] = []
    _walk_senses(root, senses, (), None)
    entry_glosses = [
        _text_without_senses(node)
        for node in root
        if _local(node.tag) == "gloss" and _text_without_senses(node)
    ]
    enrichments = connection.execute(
        """
        SELECT enrichments.*, external_sources.name AS source_name,
               external_sources.source_url, external_sources.license
          FROM enrichments
          JOIN external_sources
            ON external_sources.code = enrichments.source_code
         WHERE enrichments.entry_id = ?
           AND enrichments.approval_status IN ('approved', 'imported')
           AND external_sources.publication_enabled = 1
         ORDER BY enrichments.id
        """,
        (row["id"],),
    ).fetchall()
    for enrichment in enrichments:
        senses.append(
            {
                "id": f"{row['public_id']}-ext-{enrichment['id']}",
                "number": None,
                "position": str(len(senses) + 1),
                "depth": 1,
                "definition": enrichment["definition"],
                "definition_kind": "external-enrichment",
                "labels": [
                    {
                        "type": "domain",
                        "value": enrichment["domain"],
                    }
                ]
                if enrichment["domain"]
                else [],
                "examples": [],
                "relations": [],
                "notes": [],
                "provenance": {
                    "source": enrichment["source_code"],
                    "source_name": enrichment["source_name"],
                    "source_url": enrichment["source_url"],
                    "license": enrichment["license"],
                    "source_value": enrichment["source_value"],
                },
            }
        )

    relations = _all_relations(root)
    images = _images(root)
    etymologies = [
        _text(node)
        for node in root.iter()
        if _local(node.tag) == "etym" and _text(node)
    ]
    notes = [
        _text(node)
        for node in root.iter()
        if _local(node.tag) == "note" and _text(node)
    ]
    domains = _unique(
        label["value"]
        for sense in senses
        for label in sense.get("labels", [])
        if label.get("type") in {"dom", "domain"} and label.get("value")
    )
    definitions = [
        sense["definition"] for sense in senses if sense.get("definition")
    ]
    if lemma_index:
        for sense in senses:
            sense["definition_segments"] = _lexical_segments(
                sense.get("definition"),
                lemma_index,
                current=search_key(row["lemma"]),
            )
    examples = [
        example["quote"]
        for sense in senses
        for example in sense.get("examples", [])
        if example.get("quote")
    ]
    grammar = [row["grammatical_info"]] if row["grammatical_info"] else []
    return {
        "id": _index_id(row["resource"], row["public_id"]),
        "source_id": row["public_id"],
        "homograph_order": _homograph_order(row["public_id"]),
        "resource": row["resource"],
        "lemma": row["lemma"],
        "lemma_normalized": row["lemma_normalized"],
        "forms": _deduplicate_dicts(forms),
        "variants": _unique(
            form["value"] for form in forms[1:] if form.get("value")
        ),
        "grammatical_categories": grammar,
        "domains": domains,
        "status": row["editorial_status"],
        "definitions_text": " ".join(definitions),
        "glosses": _unique(entry_glosses),
        "gloss_segments": [
            _lexical_segments(
                value, lemma_index or {}, current=search_key(row["lemma"])
            )
            for value in _unique(entry_glosses)
        ],
        "glosses_text": " ".join(entry_glosses),
        "examples_text": " ".join(examples),
        "etymology_text": " ".join(etymologies),
        "notes_text": " ".join(notes),
        "senses": senses,
        "sense_ids": [
            sense["id"] for sense in senses if sense.get("id")
        ],
        "relations": relations,
        "images": images,
        "provenance": [
            {
                "source": "ACL_XML",
                "source_entry_id": row["public_id"],
                "source_sha256": row["raw_sha256"],
            },
            *[
                {
                    "source": item["source_code"],
                    "source_name": item["source_name"],
                    "source_url": item["source_url"],
                    "license": item["license"],
                }
                for item in enrichments
            ],
        ],
        "publication_version": release_id,
        "source_sha256": row["raw_sha256"],
        "source_xml": row["raw_xml"],
    }


def _walk_senses(
    parent: ET.Element,
    output: list[dict],
    prefix: tuple[int, ...],
    section: str | None,
) -> None:
    direct_senses = _child_senses(parent)
    for index, sense in enumerate(direct_senses, 1):
        path = (*prefix, index)
        definition_node = next(
            (node for node in sense if _local(node.tag) == "def"), None
        )
        kind = "def"
        if definition_node is None or not _text(definition_node):
            definition_node = next(
                (node for node in sense if _local(node.tag) == "gloss"), None
            )
            kind = "gloss"
        labels = [
            {"type": node.get("type") or "usage", "value": _text(node)}
            for node in sense.iter()
            if _local(node.tag) == "usg" and _text(node)
        ]
        examples = []
        for citation in sense.iter():
            if _local(citation.tag) != "cit":
                continue
            quote = next(
                (node for node in citation if _local(node.tag) == "quote"), None
            )
            if quote is not None and _text(quote):
                examples.append({"quote": _text(quote), "source": None})
        output.append(
            {
                "id": sense.get(XML_ID),
                "number": sense.get("n"),
                "position": ".".join(map(str, path)),
                "depth": len(path),
                "section": section,
                "definition": _text(definition_node) or None,
                "definition_kind": kind if definition_node is not None else None,
                "labels": _deduplicate_dicts(labels),
                "examples": _deduplicate_dicts(examples),
                "relations": _relations(sense),
                "notes": [
                    {"type": node.get("type"), "value": _text(node)}
                    for node in sense.iter()
                    if _local(node.tag) == "note" and _text(node)
                ],
            }
        )
        _walk_senses(sense, output, path, section)

    for related in [child for child in parent if _local(child.tag) == "re"]:
        related_orth = next(
            (node for node in related.iter() if _local(node.tag) == "orth"),
            None,
        )
        _walk_senses(related, output, prefix, _text(related_orth) or "Forma relacionada")


def _child_senses(parent: ET.Element) -> list[ET.Element]:
    output: list[ET.Element] = []
    for child in parent:
        if _local(child.tag) == "sense":
            output.append(child)
        elif _local(child.tag) == "re":
            continue
        else:
            output.extend(_child_senses(child))
    return output


def _relations(parent: ET.Element) -> list[dict]:
    return _deduplicate_dicts(
        [
            {
                "type": node.get("type"),
                "target_text": _text(node),
                "target_id": node.get("target"),
            }
            for node in parent.iter()
            if _local(node.tag) == "ref" and _text(node)
        ]
    )


def _all_relations(root: ET.Element) -> list[dict]:
    return _relations(root)


def _images(root: ET.Element) -> list[dict]:
    output = []
    for node in root.iter():
        if _local(node.tag) not in {"graphic", "media"}:
            continue
        value = node.get("url") or node.get("target")
        if value:
            output.append(
                {
                    "path": value,
                    "mime_type": node.get("mimeType"),
                    "description": node.get("n"),
                }
            )
    return _deduplicate_dicts(output)


def _text(node: ET.Element | None) -> str:
    return clean_text("".join(node.itertext())) if node is not None else ""


def _text_without_senses(node: ET.Element) -> str:
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node:
        if _local(child.tag) == "sense":
            if child.tail:
                parts.append(child.tail)
            continue
        parts.append(_text(child))
        if child.tail:
            parts.append(child.tail)
    return clean_text(" ".join(parts))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _lexical_segments(
    value: str | None,
    lemma_index: dict[str, str],
    *,
    current: str,
) -> list[dict]:
    if not value or not lemma_index:
        return []
    segments: list[dict] = []
    last = 0
    for match in re.finditer(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", value, re.UNICODE):
        if match.start() > last:
            segments.append({"text": value[last : match.start()]})
        text = match.group(0)
        normalized = search_key(text)
        query = lemma_index.get(normalized)
        segment = {"text": text}
        if query and normalized != current and len(normalized) >= 3:
            segment["query"] = query
        segments.append(segment)
        last = match.end()
    if last < len(value):
        segments.append({"text": value[last:]})
    return segments if any(item.get("query") for item in segments) else []


def _unique(values) -> list:
    result = []
    seen = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _deduplicate_dicts(items: list[dict]) -> list[dict]:
    return _unique(items)


def _index_id(resource: str, source_id: str) -> str:
    """Cria uma chave Meilisearch estável sem substituir o ID institucional."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", search_key(source_id)).strip("_")
    stem = stem[:180] or "entry"
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]
    prefix = "dic" if resource == "dictionary" else "voc"
    return f"{prefix}_{stem}_{digest}"


def _homograph_order(source_id: str) -> int:
    match = re.search(r"_(\d+)(?:-|$)", source_id)
    return int(match.group(1)) if match else 9999
