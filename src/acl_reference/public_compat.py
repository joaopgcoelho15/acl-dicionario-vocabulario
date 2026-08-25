from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
from typing import Iterable

from .meili import MeiliClient, MeiliError
from .labels import domain_label, grammar_label, status_label
from .normalization import search_key
from .services import ReleaseService

COLLECTION_TO_INDEX = {
    "DLP": "dictionary",
    "VOCABULARIO": "vocabulary",
}
INDEX_TO_COLLECTION = {value: key for key, value in COLLECTION_TO_INDEX.items()}
SUMMARY_ATTRIBUTES = [
    "id",
    "source_id",
    "resource",
    "lemma",
    "lemma_normalized",
    "homograph_order",
    "grammatical_categories",
    "status",
    "definitions_text",
    "sense_ids",
]


@dataclass
class PublicCompatibilityService:
    client: MeiliClient
    releases: ReleaseService

    def entry_counts(self) -> dict:
        current = self.releases.current(verify=False)
        counts = current.get("manifest", {}).get("counts", {})
        return {
            "entries": counts.get("entries", 0),
            "collections": {
                "DLP": counts.get("dictionary", 0),
                "VOCABULARIO": counts.get("vocabulary", 0),
            },
        }

    def global_facets(self) -> dict:
        return self._contextual_facets("", None, None, None, None)

    def search(
        self,
        *,
        query: str,
        collection: str | None,
        grammar: str | None,
        domain: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> dict:
        filters = self._filters(grammar, domain, status)
        indexes = self._indexes(collection)
        requested = min(offset + limit, 500)
        groups = [
            (
                index,
                self.client.search_index(
                    index,
                    {
                        "q": query,
                        "matchingStrategy": "last",
                        "limit": requested,
                        "offset": 0,
                        "filter": filters or None,
                        "attributesToRetrieve": SUMMARY_ATTRIBUTES,
                    },
                ),
            )
            for index in indexes
        ]
        hits = [
            hit
            for _, group in groups
            for hit in group.get("hits", [])
        ]
        hits.sort(key=lambda item: _search_sort_key(item, query))
        items = [
            self.entry_summary(item)
            for item in hits[offset : offset + limit]
        ]
        total = sum(
            int(group.get("estimatedTotalHits", group.get("totalHits", 0)))
            for _, group in groups
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
            "facets": self._contextual_facets(
                query, collection, grammar, domain, status
            ),
        }

    def catalogue(
        self,
        *,
        collection: str | None,
        grammar: str | None,
        domain: str | None,
        status: str | None,
        letter: str | None,
        limit: int,
        cursor: str | None,
    ) -> dict:
        offsets = _decode_cursor(cursor)
        filters = self._filters(grammar, domain, status)
        start = search_key((letter or "")[:1])
        if start:
            end = chr(ord(start[0]) + 1) if start[0] != "z" else "zzzz"
            filters.extend(
                [
                    f'lemma_normalized >= "{_filter_escape(start)}"',
                    f'lemma_normalized < "{_filter_escape(end)}"',
                ]
            )
        groups = []
        for index in self._indexes(collection):
            group = self.client.search_index(
                index,
                {
                    "q": "",
                    "limit": limit,
                    "offset": offsets.get(index, 0),
                    "filter": filters or None,
                    "sort": ["lemma_normalized:asc"],
                    "attributesToRetrieve": SUMMARY_ATTRIBUTES,
                },
            )
            groups.append((index, group))
        merged = [
            (index, hit)
            for index, group in groups
            for hit in group.get("hits", [])
        ]
        merged.sort(
            key=lambda pair: (
                pair[1].get("lemma_normalized") or "",
                pair[1].get("homograph_order", 9999),
                pair[1].get("source_id") or "",
            )
        )
        selected = merged[:limit]
        for index, _ in selected:
            offsets[index] = offsets.get(index, 0) + 1
        total = sum(
            int(group.get("estimatedTotalHits", group.get("totalHits", 0)))
            for _, group in groups
        )
        has_more = sum(offsets.values()) < total
        return {
            "limit": limit,
            "total": total,
            "items": [self.entry_summary(hit) for _, hit in selected],
            "next_cursor": _encode_cursor(offsets) if has_more else None,
            "has_more": has_more,
            "facets": self._contextual_facets(
                "", collection, grammar, domain, status,
                extra_filters=(
                    [
                        f'lemma_normalized >= "{_filter_escape(start)}"',
                        f'lemma_normalized < "{_filter_escape(end)}"',
                    ]
                    if start else []
                ),
            ),
        }

    def resolve(self, identifier: str) -> dict:
        matches = []
        for index in ("dictionary", "vocabulary"):
            try:
                item = self.client.get_entry(index, identifier)
            except MeiliError:
                item = None
            if item:
                matches.append(self.entry_detail(item))
        if matches:
            return {"kind": "entry", "matches": matches}
        escaped = _filter_escape(identifier)
        for index in ("dictionary", "vocabulary"):
            result = self.client.search_index(
                index,
                {
                    "q": "",
                    "filter": f'sense_ids = "{escaped}"',
                    "limit": 2,
                },
            )
            for hit in result.get("hits", []):
                matches.append(self.entry_detail(hit))
        return {
            "kind": "sense" if matches else "not_found",
            "matches": matches,
        }

    def get_entry(self, identifier: str) -> dict:
        result = self.resolve(identifier)
        if len(result["matches"]) != 1:
            raise MeiliError(
                f"O identificador {identifier!r} não corresponde a uma entrada única."
            )
        return result["matches"][0]

    def debug_entry(self, identifier: str) -> dict:
        entry = self.get_entry(identifier)
        return {
            "xml_id": entry["xml_id"],
            "raw_xml": entry.pop("_source_xml", ""),
            "json": entry,
        }

    def entry_summary(self, item: dict) -> dict:
        definitions = item.get("definitions_text") or ""
        return {
            "xml_id": item.get("source_id"),
            "collection_code": INDEX_TO_COLLECTION.get(
                item.get("resource"), item.get("resource")
            ),
            "source_code": INDEX_TO_COLLECTION.get(
                item.get("resource"), item.get("resource")
            ),
            "lemma": item.get("lemma"),
            "grammatical_info": _first(item.get("grammatical_categories")),
            "grammatical_label": grammar_label(
                _first(item.get("grammatical_categories"))
            ),
            "source_status": item.get("status"),
            "source_status_label": status_label(item.get("status")),
            "workflow_status": "PUBLISHED",
            "summary": definitions[:360],
            "sense_count": len(
                item.get("senses") or item.get("sense_ids") or []
            ),
            "anomaly_flags": "",
        }

    def entry_detail(self, item: dict) -> dict:
        summary = self.entry_summary(item)
        senses = []
        for sense in item.get("senses") or []:
            provenance = sense.get("provenance") or {}
            senses.append(
                {
                    "xml_id": sense.get("id"),
                    "number": sense.get("number"),
                    "depth": sense.get("depth"),
                    "section": sense.get("section"),
                    "definition": sense.get("definition"),
                    "definition_segments": sense.get(
                        "definition_segments"
                    )
                    or [],
                    "labels": [
                        {
                            "type": label.get("type"),
                            "value": label.get("value"),
                            "label": (
                                domain_label(label.get("value"))
                                if label.get("type") in {"dom", "domain"}
                                else label.get("value")
                            ),
                        }
                        for label in sense.get("labels") or []
                    ],
                    "examples": sense.get("examples") or [],
                    "references": [
                        {
                            "type": reference.get("type"),
                            "value": reference.get("target_text"),
                            "target": reference.get("target_id"),
                        }
                        for reference in sense.get("relations") or []
                    ],
                    "notes": sense.get("notes") or [],
                    "images": _images(sense.get("images") or []),
                    "source": {
                        "code": provenance.get("source"),
                        "url": provenance.get("source_url"),
                        "license": provenance.get("license"),
                    }
                    if provenance
                    else None,
                }
            )
        detail = {
            **summary,
            "source_url": None,
            "source_license": None,
            "is_published": 1,
            "raw_sha256": item.get("source_sha256"),
            "lexical": {
                "orthographies": item.get("forms") or [],
                "pronunciations": [],
                "syllabifications": [],
                "etymologies": _split_text(item.get("etymology_text")),
                "notes": [
                    {"type": None, "value": value}
                    for value in _split_text(item.get("notes_text"))
                ],
                "related_forms": [],
                "glosses": item.get("glosses") or [],
                "gloss_items": [
                    {
                        "value": value,
                        "segments": (
                            item.get("gloss_segments") or []
                        )[index]
                        if index < len(item.get("gloss_segments") or [])
                        else [],
                    }
                    for index, value in enumerate(item.get("glosses") or [])
                ],
                "references": [
                    {
                        "type": reference.get("type"),
                        "value": reference.get("target_text"),
                        "target": reference.get("target_id"),
                    }
                    for reference in item.get("relations") or []
                ],
                "images": _images(item.get("images") or []),
                "senses": senses,
                "lexical_links": {
                    "count": sum(
                        1
                        for sense in item.get("senses") or []
                        for segment in sense.get("definition_segments") or []
                        if segment.get("query")
                    )
                    + sum(
                        1
                        for segments in item.get("gloss_segments") or []
                        for segment in segments
                        if segment.get("query")
                    )
                },
            },
            "_source_xml": item.get("source_xml") or "",
        }
        return detail

    def _indexes(self, collection: str | None) -> list[str]:
        if collection:
            index = COLLECTION_TO_INDEX.get(collection.upper())
            if not index:
                raise ValueError("Coleção inválida.")
            return [index]
        return ["dictionary", "vocabulary"]

    def _filters(
        self, grammar: str | None, domain: str | None, status: str | None
    ) -> list[str]:
        output = []
        for attribute, value in (
            ("grammatical_categories", grammar),
            ("domains", domain),
            ("status", status),
        ):
            if value:
                output.append(
                    f'{attribute} = "{_filter_escape(value)}"'
                )
        return output

    def _facets(
        self, query: str, collection: str | None, filters: list[str]
    ) -> dict:
        groups = [
            (
                index,
                self.client.search_index(
                    index,
                    {
                        "q": query,
                        "limit": 0,
                        "filter": filters or None,
                        "facets": [
                            "grammatical_categories",
                            "domains",
                            "status",
                        ],
                    },
                ),
            )
            for index in self._indexes(collection)
        ]
        return self._merge_facets(groups)

    def _contextual_facets(
        self,
        query: str,
        collection: str | None,
        grammar: str | None,
        domain: str | None,
        status: str | None,
        *,
        extra_filters: list[str] | None = None,
    ) -> dict:
        """Calcula cada faceta com todos os filtros exceto ela própria."""
        dimensions = (
            ("grammatical_categories", self._filters(None, domain, status)),
            ("domains", self._filters(grammar, None, status)),
            ("status", self._filters(grammar, domain, None)),
        )
        queries = []
        keys = []
        for attribute, filters in dimensions:
            filters.extend(extra_filters or [])
            for index in self._indexes(collection):
                item = {
                    "indexUid": index,
                    "q": query,
                    "matchingStrategy": "last",
                    "limit": 0,
                    "facets": [attribute],
                }
                if filters:
                    item["filter"] = filters
                queries.append(item)
                keys.append(index)
        collection_filters = self._filters(grammar, domain, status)
        collection_filters.extend(extra_filters or [])
        for index in ("dictionary", "vocabulary"):
            item = {
                "indexUid": index,
                "q": query,
                "matchingStrategy": "last",
                "limit": 0,
            }
            if collection_filters:
                item["filter"] = collection_filters
            queries.append(item)
        response = self.client.request(
            "POST", "/multi-search", {"queries": queries}
        )
        results = response.get("results") or []
        facets = self._merge_facets(list(zip(keys, results[:len(keys)])))
        collection_results = results[len(keys):]
        facets["collections"] = [
            {
                "value": INDEX_TO_COLLECTION[index],
                "label": "Dicionário" if index == "dictionary" else "Vocabulário",
                "count": int(result.get("estimatedTotalHits", result.get("totalHits", 0))),
            }
            for index, result in zip(("dictionary", "vocabulary"), collection_results)
        ]
        return facets

    def _merge_facets(self, groups) -> dict:
        merged = {
            "grammatical_categories": {},
            "domains": {},
            "status": {},
        }
        for _, group in groups:
            for name, values in (group.get("facetDistribution") or {}).items():
                for value, count in values.items():
                    merged[name][value] = merged[name].get(value, 0) + count
        def rows(name, alphabetical=False):
            values = [
                {
                    "value": value,
                    "label": (
                        grammar_label(value)
                        if name == "grammatical_categories"
                        else domain_label(value)
                        if name == "domains"
                        else status_label(value)
                    ),
                    "count": count,
                    "unmapped": (
                        (
                            grammar_label(value)
                            if name == "grammatical_categories"
                            else domain_label(value)
                            if name == "domains"
                            else status_label(value)
                        )
                        == value
                    ),
                }
                for value, count in merged[name].items()
            ]
            values.sort(
                key=(
                    (lambda item: item["value"].casefold())
                    if alphabetical
                    else (lambda item: (-item["count"], item["value"].casefold()))
                )
            )
            return values
        return {
            "collections": [],
            "grammar": rows("grammatical_categories"),
            "domains": rows("domains", alphabetical=True),
            "statuses": rows("status"),
        }


def _search_sort_key(item: dict, query: str):
    lemma = search_key(item.get("lemma"))
    term = search_key(query)
    if lemma == term:
        match = (0, 0)
    elif lemma.startswith(term):
        match = (1, len(lemma) - len(term))
    elif term in lemma:
        match = (2, len(lemma) - len(term))
    else:
        # A ordenação anterior descartava a relevância ortográfica do
        # Meilisearch e podia colocar palavras encontradas numa definição
        # antes de um lema com apenas uma gralha. A distância mantém esses
        # lemas aproximados no topo sem retirar resultados conceptuais.
        distance = _edit_distance(lemma, term)
        allowed = 2 if len(term) >= 9 else 1 if len(term) >= 5 else 0
        match = (3 if distance <= allowed else 4, distance)
    return (
        *match,
        item.get("homograph_order", 9999),
        lemma,
        item.get("source_id") or "",
    )


def _edit_distance(left: str, right: str) -> int:
    """Distância de Levenshtein para ordenar o máximo de 500 candidatos."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row_no, left_char in enumerate(left, 1):
        current = [row_no]
        for column, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _first(values):
    return values[0] if values else None


def _images(items):
    return [
        {
            "url": item.get("path"),
            "caption": item.get("description"),
        }
        for item in items
        if item.get("path")
    ]


def _split_text(value):
    return [value] if value else []


def _filter_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _encode_cursor(offsets: dict[str, int]) -> str:
    raw = json.dumps(offsets, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> dict[str, int]:
    if not cursor:
        return {"dictionary": 0, "vocabulary": 0}
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        return {
            "dictionary": max(0, int(value.get("dictionary", 0))),
            "vocabulary": max(0, int(value.get("vocabulary", 0))),
        }
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Cursor inválido.")
