from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3

from .editorial_db import connect, initialize
from .normalization import clean_text, search_key

SPE_URL = "https://www.spestatistica.pt/pt/glossario"
SPE_LICENSE = "CC BY-NC-ND 3.0"
SPE_EXPECTED_RECORDS = 3565


@dataclass(frozen=True)
class SpeTerm:
    english: str
    portuguese: str
    note: str | None = None


@dataclass(frozen=True)
class SpeImportResult:
    source_records: int
    portuguese_terms: int
    imported: int
    unmatched: int
    source_sha256: str


class _GlossaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cells: list[list[str]] = []
        self.note: str | None = None
        self.terms: list[SpeTerm] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "glossario_entries":
            self.in_table = True
            self.table_depth = 1
            return
        if not self.in_table:
            return
        if tag == "table":
            self.table_depth += 1
        elif tag == "tr":
            self.in_row = True
            self.cells = []
            self.note = None
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self.cells.append([])
        elif tag == "button" and "show-description" in (
            attributes.get("class") or ""
        ):
            self.note = _plain_html(attributes.get("data-desc") or "") or None

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "td":
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            values = [clean_text("".join(parts)) for parts in self.cells]
            if len(values) >= 2 and values[0] and values[1]:
                self.terms.append(SpeTerm(values[0], values[1], self.note))
        elif tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False

    def handle_data(self, data):
        if self.in_table and self.in_row and self.in_cell and self.cells:
            self.cells[-1].append(data)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def parse_spe_glossary(source: str | bytes) -> list[SpeTerm]:
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    parser = _GlossaryParser()
    parser.feed(text)
    parser.close()
    return parser.terms


def portuguese_lemmas(term: SpeTerm) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    depth = 0
    for character in term.portuguese:
        if character in "([{":
            depth += 1
        elif character in ")]}" and depth:
            depth -= 1
        if character == ";" and depth == 0:
            if value := clean_text("".join(current)):
                values.append(value)
            current = []
        else:
            current.append(character)
    if value := clean_text("".join(current)):
        values.append(value)
    return list(dict.fromkeys(values))


def import_spe(source: str | Path, db_path: str | Path) -> SpeImportResult:
    path = Path(source)
    source_bytes = path.read_bytes()
    terms = parse_spe_glossary(source_bytes)
    if not terms:
        raise ValueError("O ficheiro não contém registos reconhecíveis da SPE.")
    initialize(db_path)
    connection = connect(db_path)
    try:
        run = connection.execute(
            "SELECT id FROM import_runs WHERE is_active=1 LIMIT 1"
        ).fetchone()
        if run is None:
            raise ValueError("É necessária uma importação XML ativa.")
        run_id = int(run["id"])
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        connection.execute(
            """
            INSERT INTO external_sources(
                code, name, source_url, license, source_sha256
            ) VALUES ('SPE', ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,
                source_url=excluded.source_url,
                license=excluded.license,
                source_sha256=excluded.source_sha256,
                imported_at=CURRENT_TIMESTAMP
            """,
            (
                "Sociedade Portuguesa de Estatística",
                SPE_URL,
                SPE_LICENSE,
                source_sha,
            ),
        )
        connection.execute("DELETE FROM enrichments WHERE source_code='SPE'")
        connection.execute(
            "DELETE FROM unmatched_enrichments WHERE source_code='SPE'"
        )
        imported = unmatched = candidates = 0
        for source_ordinal, term in enumerate(terms, 1):
            for lemma in portuguese_lemmas(term):
                candidates += 1
                entry, reason, candidate_ids = _match_entry(
                    connection, run_id, lemma
                )
                payload = {
                    "source": "SPE",
                    "source_url": SPE_URL,
                    "license": SPE_LICENSE,
                    "portuguese": term.portuguese,
                    "target_lemma": lemma,
                    "note": term.note,
                    "source_equivalent": term.english,
                }
                if entry is None:
                    connection.execute(
                        """
                        INSERT INTO unmatched_enrichments(
                            source_code, source_ordinal, target_lemma,
                            target_lemma_normalized, reason,
                            candidate_entry_ids, raw_json
                        ) VALUES ('SPE', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_ordinal,
                            lemma,
                            search_key(lemma),
                            reason,
                            ",".join(map(str, candidate_ids)),
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                    unmatched += 1
                else:
                    definition = (
                        clean_text(term.note)
                        or "Termo de Estatística registado pela Sociedade Portuguesa de Estatística."
                    )
                    connection.execute(
                        """
                        INSERT INTO enrichments(
                            entry_id, source_code, domain, definition,
                            source_value, raw_json, approval_status
                        ) VALUES (?, 'SPE', 'Stat.', ?, ?, ?, 'imported')
                        """,
                        (
                            entry["id"],
                            definition,
                            term.portuguese,
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                    imported += 1
        connection.commit()
        return SpeImportResult(
            len(terms), candidates, imported, unmatched, source_sha
        )
    except (ValueError, sqlite3.Error):
        connection.rollback()
        raise
    finally:
        connection.close()


def _match_entry(connection, run_id: int, lemma: str):
    key = search_key(lemma)
    for resource in ("dictionary", "vocabulary"):
        rows = connection.execute(
            """
            SELECT id, public_id, lemma
              FROM entries
             WHERE import_run_id=? AND resource=? AND lemma_normalized=?
             ORDER BY source_ordinal
            """,
            (run_id, resource, key),
        ).fetchall()
        if len(rows) == 1:
            return rows[0], f"exact_{resource}", [int(rows[0]["id"])]
        if len(rows) > 1:
            return None, f"ambiguous_{resource}", [
                int(row["id"]) for row in rows
            ]
    return None, "no_exact_acl_entry", []


def _plain_html(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    return clean_text("".join(parser.parts))

