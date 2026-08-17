from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading


_INITIALIZED_PATHS: set[Path] = set()
_INITIALIZE_LOCK = threading.Lock()

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    entry_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    source_processing_instructions TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    source_ordinal INTEGER NOT NULL,
    public_id TEXT NOT NULL,
    resource TEXT NOT NULL CHECK (resource IN ('dictionary', 'vocabulary')),
    lemma TEXT NOT NULL,
    lemma_normalized TEXT NOT NULL,
    grammatical_info TEXT,
    editorial_status TEXT,
    workflow_status TEXT NOT NULL DEFAULT 'IMPORTED',
    raw_xml TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    imported_raw_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(import_run_id, source_ordinal)
);

CREATE INDEX IF NOT EXISTS idx_entries_run ON entries(import_run_id);
CREATE INDEX IF NOT EXISTS idx_entries_public_id ON entries(public_id);
CREATE INDEX IF NOT EXISTS idx_entries_lemma ON entries(lemma_normalized);
CREATE INDEX IF NOT EXISTS idx_entries_run_lemma
    ON entries(import_run_id, lemma_normalized, source_ordinal);
CREATE INDEX IF NOT EXISTS idx_entries_resource ON entries(resource);
CREATE INDEX IF NOT EXISTS idx_entries_match
    ON entries(import_run_id, resource, lemma_normalized, source_ordinal);

CREATE TABLE IF NOT EXISTS forms (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    value TEXT NOT NULL,
    value_normalized TEXT NOT NULL,
    language TEXT,
    kind TEXT
);

CREATE TABLE IF NOT EXISTS senses (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES senses(id) ON DELETE CASCADE,
    public_id TEXT,
    position_path TEXT NOT NULL,
    number_label TEXT,
    definition TEXT,
    gloss TEXT,
    raw_xml TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    sense_id INTEGER REFERENCES senses(id) ON DELETE CASCADE,
    relation_type TEXT,
    target_text TEXT NOT NULL,
    target_id TEXT
);

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    sense_id INTEGER REFERENCES senses(id) ON DELETE CASCADE,
    label_type TEXT NOT NULL,
    value TEXT NOT NULL,
    value_normalized TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_sources (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_url TEXT,
    license TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_sha256 TEXT,
    publication_enabled INTEGER NOT NULL DEFAULT 1 CHECK (publication_enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS enrichments (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    source_code TEXT NOT NULL REFERENCES external_sources(code),
    domain TEXT,
    definition TEXT NOT NULL,
    source_value TEXT,
    raw_json TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS unmatched_enrichments (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL REFERENCES external_sources(code),
    source_ordinal INTEGER NOT NULL,
    target_lemma TEXT NOT NULL,
    target_lemma_normalized TEXT NOT NULL,
    reason TEXT NOT NULL,
    candidate_entry_ids TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    actor TEXT,
    comment TEXT,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entry_id, revision_no)
);

CREATE TABLE IF NOT EXISTS validation_issues (
    id INTEGER PRIMARY KEY,
    import_run_id INTEGER REFERENCES import_runs(id) ON DELETE CASCADE,
    entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    rule_code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS validation_waivers (
    id INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    entry_sha256 TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT,
    UNIQUE(entry_id, rule_code, entry_sha256)
);

CREATE TABLE IF NOT EXISTS releases (
    release_id TEXT PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    state TEXT NOT NULL,
    manifest_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    description TEXT,
    prepared_by TEXT,
    approved_by TEXT,
    approved_at TEXT,
    previous_release_id TEXT,
    report_json TEXT,
    decision_comment TEXT
);

CREATE TABLE IF NOT EXISTS editorial_users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('editor', 'reviewer', 'approver', 'administrator')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS controlled_values (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN ('grammar', 'domain', 'editorial_status')),
    value TEXT NOT NULL,
    display_label TEXT,
    governance_status TEXT NOT NULL DEFAULT 'unmapped'
        CHECK (governance_status IN ('authorized', 'obsolete', 'unmapped')),
    replacement_value TEXT,
    usage_count INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(category, value)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    entry_id INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    release_id TEXT,
    previous_state TEXT,
    resulting_state TEXT,
    comment TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS validation_runs (
    id INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    content_version TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(import_run_id, content_version, schema_sha256)
);

CREATE INDEX IF NOT EXISTS idx_controlled_values_category
    ON controlled_values(category, governance_status, value);
CREATE INDEX IF NOT EXISTS idx_audit_events_entry
    ON audit_events(entry_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_release
    ON audit_events(release_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forms_entry_position
    ON forms(entry_id, position, id);
CREATE INDEX IF NOT EXISTS idx_senses_entry_position
    ON senses(entry_id, position_path, id);
CREATE INDEX IF NOT EXISTS idx_relations_entry
    ON relations(entry_id, id);
CREATE INDEX IF NOT EXISTS idx_labels_entry
    ON labels(entry_id, id);
CREATE INDEX IF NOT EXISTS idx_labels_filter
    ON labels(label_type, value, entry_id);
CREATE INDEX IF NOT EXISTS idx_enrichments_entry
    ON enrichments(entry_id, approval_status, id);
CREATE INDEX IF NOT EXISTS idx_enrichments_source
    ON enrichments(source_code, approval_status);
CREATE INDEX IF NOT EXISTS idx_revisions_entry
    ON revisions(entry_id, revision_no DESC);
CREATE INDEX IF NOT EXISTS idx_validation_entry_severity
    ON validation_issues(entry_id, severity);
CREATE INDEX IF NOT EXISTS idx_validation_run_severity
    ON validation_issues(import_run_id, severity);
CREATE INDEX IF NOT EXISTS idx_validation_waivers_lookup
    ON validation_waivers(entry_id, rule_code, entry_sha256, revoked_at);
CREATE INDEX IF NOT EXISTS idx_entries_run_status
    ON entries(import_run_id, editorial_status);
CREATE INDEX IF NOT EXISTS idx_entries_run_grammar
    ON entries(import_run_id, grammatical_info);
CREATE INDEX IF NOT EXISTS idx_entries_run_resource
    ON entries(import_run_id, resource, lemma_normalized, source_ordinal);
"""


class ClosingConnection(sqlite3.Connection):
    """Ligação SQLite que também fecha ao terminar um bloco ``with``.

    O context manager nativo de ``sqlite3.Connection`` apenas faz
    commit/rollback; não fecha o descritor. Como os serviços fazem muitas
    leituras curtas, centralizamos aqui o fecho para impedir acumulação de
    ligações e bloqueios durante importações e publicações longas.
    """

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(path: str | Path) -> None:
    resolved = Path(path).resolve()
    if resolved in _INITIALIZED_PATHS:
        return
    with _INITIALIZE_LOCK:
        if resolved in _INITIALIZED_PATHS:
            return
        _initialize_once(path)
        _INITIALIZED_PATHS.add(resolved)


def _initialize_once(path: str | Path) -> None:
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(entries)").fetchall()
        }
        if "workflow_status" not in columns:
            connection.execute(
                """
                ALTER TABLE entries
                ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'IMPORTED'
                """
            )
        if "imported_raw_sha256" not in columns:
            connection.execute("ALTER TABLE entries ADD COLUMN imported_raw_sha256 TEXT")
        connection.execute(
            "UPDATE entries SET imported_raw_sha256=raw_sha256 WHERE imported_raw_sha256 IS NULL"
        )
        import_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(import_runs)").fetchall()
        }
        if "source_processing_instructions" not in import_columns:
            connection.execute(
                "ALTER TABLE import_runs ADD COLUMN source_processing_instructions TEXT NOT NULL DEFAULT '[]'"
            )
        source_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(external_sources)").fetchall()
        }
        if "publication_enabled" not in source_columns:
            connection.execute(
                "ALTER TABLE external_sources ADD COLUMN publication_enabled INTEGER NOT NULL DEFAULT 1"
            )
        release_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(releases)").fetchall()
        }
        for name, definition in (
            ("description", "TEXT"),
            ("prepared_by", "TEXT"),
            ("approved_by", "TEXT"),
            ("approved_at", "TEXT"),
            ("previous_release_id", "TEXT"),
            ("report_json", "TEXT"),
            ("decision_comment", "TEXT"),
        ):
            if name not in release_columns:
                connection.execute(f"ALTER TABLE releases ADD COLUMN {name} {definition}")
        connection.executemany(
            """
            INSERT INTO editorial_users(username, display_name, role)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO NOTHING
            """,
            (
                ("editor.demo", "Editor de demonstração", "editor"),
                ("revisor.demo", "Revisor de demonstração", "reviewer"),
                ("aprovador.demo", "Aprovador de demonstração", "approver"),
            ),
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entries_workflow
                ON entries(import_run_id, workflow_status)
            """
        )


@contextmanager
def transaction(path: str | Path):
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
