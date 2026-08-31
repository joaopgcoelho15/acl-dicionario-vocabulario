from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from acl_reference.importer import import_xml
from acl_reference.meili import MeiliClient
from acl_reference.editorial_service import EditorialService
from acl_reference.external_sources import set_source_publication
from acl_reference.governance import GovernanceService
from acl_reference.publication import (
    activate_local_release,
    approve_release,
    build_release,
    current_release,
    integrity_failure_message,
    verify_release,
)
from acl_reference.publication_jobs import PublicationJobManager
from acl_reference.public_compat import PublicCompatibilityService, _search_sort_key
from acl_reference.validation import validate_active_run
from acl_reference.persistence import persistence_status
from acl_reference.workflow import workflow_from_xml_status
from acl_reference.xml_comparison import compare_xml_with_database, file_sha256
from acl_reference.repository_backup import (
    RepositoryBackupService,
    restore_repository_snapshot,
)


ROOT = Path(__file__).resolve().parent


class ReferenceArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "editorial.sqlite"
        self.releases = self.root / "releases"
        self.source = ROOT / "fixtures" / "sample.xml"

    def tearDown(self):
        self.temp.cleanup()

    def test_editorial_import_preserves_source_and_structure(self):
        result = import_xml(self.source, self.db)
        self.assertEqual(result.imported, 2)
        self.assertEqual(result.errors, 0)
        with sqlite3.connect(self.db) as connection:
            entry_count = connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            sense_count = connection.execute("SELECT COUNT(*) FROM senses").fetchone()[0]
            raw_xml, raw_sha = connection.execute(
                "SELECT raw_xml, raw_sha256 FROM entries ORDER BY source_ordinal LIMIT 1"
            ).fetchone()
        self.assertEqual(entry_count, 2)
        self.assertEqual(sense_count, 3)
        self.assertEqual(
            hashlib.sha256(raw_xml.encode("utf-8")).hexdigest(), raw_sha
        )

    def test_working_data_dirty_indicator_and_explicit_xml_save(self):
        import_xml(self.source, self.db)
        self.assertFalse(persistence_status(self.db)["has_unsaved_changes"])
        service = EditorialService(self.db, self.root / "exports")
        entry = service.get_entry("DLP-cavalo_1-teste")
        service.update_entry(
            entry["public_id"],
            {
                "actor": "editor.demo",
                "expected_updated_at": entry["updated_at"],
                "lemma": "cavalo guardado",
                "senses": [],
                "comment": "Testar salvaguarda canónica",
            },
        )
        self.assertTrue(persistence_status(self.db)["has_unsaved_changes"])
        result = service.save_canonical(actor="editor.demo")
        self.assertFalse(persistence_status(self.db)["has_unsaved_changes"])
        self.assertTrue(Path(result["xml_path"]).is_file())
        self.assertTrue(Path(result["log_path"]).is_file())
        exported = Path(result["xml_path"]).read_text(encoding="utf-8")
        self.assertIn("cavalo guardado", exported)
        self.assertIn('status="edited"', exported)
        self.assertIn('origin="imported"', exported)

    def test_xml_status_initializes_the_editorial_workflow(self):
        import_xml(self.source, self.db)
        with sqlite3.connect(self.db) as connection:
            states = dict(connection.execute(
                "SELECT public_id,workflow_status FROM entries"
            ).fetchall())
        self.assertEqual(states["DLP-cavalo_1-teste"], "EDITED")
        self.assertEqual(states["VOLP-exemplo_1-teste"], "DRAFT")
        self.assertEqual(workflow_from_xml_status("revisada"), "REVIEWED")
        self.assertEqual(workflow_from_xml_status("validated"), "VALIDATED")

    def test_xml_comparison_is_non_destructive_and_exports_csv(self):
        original = import_xml(self.source, self.db)
        report = compare_xml_with_database(
            self.source, self.db, self.root / "exports"
        )
        self.assertEqual(report["summary"], {
            "added": 0, "removed": 0, "changed": 0, "unchanged": 2
        })
        self.assertEqual(report["current_entries"], 2)
        self.assertTrue((self.root / "exports" / report["csv_name"]).is_file())
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT id FROM import_runs WHERE is_active=1"
                ).fetchone()[0],
                original.run_id,
            )

        changed = self.root / "changed.xml"
        changed.write_text(
            self.source.read_text(encoding="utf-8").replace(
                "mamífero doméstico", "mamífero doméstico alterado"
            ),
            encoding="utf-8",
        )
        report = compare_xml_with_database(changed, self.db, self.root / "exports")
        self.assertEqual(report["summary"]["changed"], 1)
        self.assertIn("aceções e definições", report["preview"][0]["fields"])

    def test_approved_xml_replace_overwrites_only_lexical_data(self):
        import_xml(self.source, self.db)
        replacement = self.root / "replacement.xml"
        replacement.write_text(
            '''<dic><entry xmlns="http://www.tei-c.org/ns/1.0" xmlns:dacl="http://dacl.zbr.pt/annotations" xml:id="DLP-substituida" xml:lang="pt"><dacl:meta status="validated"/><form><orth>substituída</orth></form></entry></dic>''',
            encoding="utf-8",
        )
        service = EditorialService(self.db, self.root / "exports")
        report = service.compare_xml(
            replacement, actor="aprovador.demo", source_label="replacement.xml"
        )
        self.assertEqual(report["summary"]["added"], 1)
        self.assertEqual(report["summary"]["removed"], 2)
        result = service.replace_from_xml(
            replacement,
            actor="aprovador.demo",
            expected_sha256=file_sha256(replacement),
            source_label="replacement.xml",
        )
        self.assertEqual(result.imported, 1)
        with sqlite3.connect(self.db) as connection:
            row = connection.execute(
                "SELECT public_id,workflow_status FROM entries "
                "WHERE import_run_id=(SELECT id FROM import_runs WHERE is_active=1)"
            ).fetchone()
            users = connection.execute("SELECT COUNT(*) FROM editorial_users").fetchone()[0]
        self.assertEqual(row, ("DLP-substituida", "VALIDATED"))
        self.assertEqual(users, 3)

    def test_failed_initial_reimport_preserves_the_active_dataset(self):
        first = import_xml(self.source, self.db)
        invalid = self.root / "duplicate.xml"
        invalid.write_text(
            '''<dic><entry xmlns="http://www.tei-c.org/ns/1.0" xml:id="duplicada"><form><orth>uma</orth></form></entry><entry xmlns="http://www.tei-c.org/ns/1.0" xml:id="duplicada"><form><orth>duas</orth></form></entry></dic>''',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "dados existentes foram preservados"):
            import_xml(invalid, self.db)
        with sqlite3.connect(self.db) as connection:
            active_id, count = connection.execute(
                "SELECT id,entry_count FROM import_runs WHERE is_active=1"
            ).fetchone()
            failed = connection.execute(
                "SELECT status,is_active FROM import_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual((active_id, count), (first.run_id, 2))
        self.assertEqual(failed, ("failed", 0))

    def test_publication_package_is_complete_and_verifiable(self):
        import_xml(self.source, self.db)
        result = build_release(
            self.db, self.releases, release_id="test-001"
        )
        self.assertEqual(result.entries, 2)
        manifest = json.loads(
            (result.path / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["state"], "validated")
        self.assertEqual(manifest["counts"]["dictionary"], 1)
        self.assertEqual(manifest["counts"]["vocabulary"], 1)
        self.assertIn("quality", manifest)
        self.assertIn("external_sources", manifest)
        self.assertTrue(verify_release(result.path)["valid"])
        ET.parse(result.path / "canonical.xml")
        dictionary = json.loads(
            (result.path / "dictionary.ndjson").read_text(encoding="utf-8")
        )
        self.assertEqual(dictionary["lemma"], "cavalo")
        self.assertEqual(dictionary["source_id"], "DLP-cavalo_1-teste")
        self.assertRegex(dictionary["id"], r"^[A-Za-z0-9_-]+$")
        self.assertEqual(dictionary["senses"][1]["definition_kind"], "gloss")
        self.assertIn("Zool.", dictionary["domains"])
        self.assertEqual(dictionary["publication_version"], "test-001")
        vocabulary = json.loads(
            (result.path / "vocabulary.ndjson").read_text(encoding="utf-8")
        )
        self.assertEqual(vocabulary["glosses"], ["texto introdutório:"])
        self.assertEqual(
            vocabulary["senses"][0]["definition"],
            "sentido incluído dentro de gloss",
        )

    def test_repository_backup_commits_and_restores_complete_state(self):
        import_xml(self.source, self.db)
        result = build_release(self.db, self.releases, release_id="backup-001")
        approve_release(
            self.db,
            self.releases,
            result.release_id,
            actor="aprovador.demo",
        )
        activate_local_release(self.db, self.releases, result.release_id)
        runtime_env = self.root / "runtime.env"
        runtime_env.write_text("EDITORIAL_PASSWORD=ACL\n", encoding="utf-8")
        repository = self.root / "data-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, stdout=subprocess.DEVNULL)
        (repository / "README.md").write_text("dados\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Teste", "-c", "user.email=teste@example.test", "commit", "-m", "Inicial"],
            cwd=repository,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        service = RepositoryBackupService(
            db_path=self.db,
            releases_root=self.releases,
            repository_path=repository,
            runtime_env=runtime_env,
            require_lfs=False,
            push=False,
        )
        synced = service.sync(actor="aprovador.demo")
        self.assertEqual(synced["state"], "succeeded")
        self.assertTrue((repository / "current" / "editorial.sqlite.xz").is_file())
        manifest = json.loads((repository / "current" / "manifest.json").read_text())
        self.assertEqual(manifest["active_release"], "backup-001")
        self.assertEqual((repository / "current" / "runtime.env").read_text(), "EDITORIAL_PASSWORD=ACL\n")

        restored_db = self.root / "restored" / "editorial.sqlite"
        restored_releases = self.root / "restored-releases"
        restored_env = self.root / "restored.env"
        restored = restore_repository_snapshot(
            repository,
            db_path=restored_db,
            releases_root=restored_releases,
            env_target=restored_env,
        )
        self.assertEqual(restored["active_release"], "backup-001")
        self.assertTrue((restored_releases / "backup-001" / "manifest.json").is_file())
        self.assertEqual(restored_env.read_text(), "EDITORIAL_PASSWORD=ACL\n")
        with sqlite3.connect(restored_db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 2)

        unchanged = service.sync_if_changed(actor="sistema.backup")
        self.assertTrue(unchanged["skipped"])
        self.assertEqual(unchanged["commit"], synced["commit"])

    def test_integrity_failure_identifies_the_changed_file(self):
        import_xml(self.source, self.db)
        result = build_release(self.db, self.releases, release_id="test-damaged")
        dictionary = result.path / "dictionary.ndjson"
        dictionary.write_text(
            dictionary.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        verification = verify_release(result.path)
        self.assertFalse(verification["valid"])
        self.assertEqual(verification["failures"][0]["path"], "dictionary.ndjson")
        self.assertIn("dictionary.ndjson: tamanho diferente", integrity_failure_message(verification))

    def test_rng_issue_explains_element_and_xml_location(self):
        import_xml(self.source, self.db)
        result = validate_active_run(
            self.db, rng_path=Path("contracts/schemas/academia.rng")
        )
        self.assertGreater(result.warnings, 0)
        with sqlite3.connect(self.db) as connection:
            message, details_json = connection.execute(
                "SELECT message, details_json FROM validation_issues "
                "WHERE rule_code='RNG_INVALID' LIMIT 1"
            ).fetchone()
        details = json.loads(details_json)
        self.assertIn("Motivo:", message)
        self.assertTrue(details["errors"][0]["description"])
        self.assertTrue(details["errors"][0]["path"])

    def test_volp_only_is_accepted_without_changing_source_xml(self):
        source = self.root / "volp.xml"
        source.write_text(
            '''<dic><entry xmlns="http://www.tei-c.org/ns/1.0" xml:id="VOLP-teste" xml:lang="pt" volp="only"><form><orth>teste</orth></form><gramGrp>n. m.</gramGrp></entry></dic>''',
            encoding="utf-8",
        )
        import_xml(source, self.db)
        result = validate_active_run(
            self.db, rng_path=Path("contracts/schemas/academia.rng")
        )
        self.assertEqual(result.warnings, 0)
        with sqlite3.connect(self.db) as connection:
            raw_xml = connection.execute("SELECT raw_xml FROM entries").fetchone()[0]
        self.assertIn('volp="only"', raw_xml)

    def test_error_waiver_is_audited_and_only_applies_to_current_xml(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        with sqlite3.connect(self.db) as connection:
            entry_id, run_id = connection.execute(
                "SELECT id, import_run_id FROM entries ORDER BY id LIMIT 1"
            ).fetchone()
            connection.execute(
                "INSERT INTO validation_issues(import_run_id,entry_id,severity,rule_code,message) VALUES(?,?,'error','TEST_ERROR','Erro de teste')",
                (run_id, entry_id),
            )
            connection.execute("UPDATE entries SET workflow_status='VALIDATED'")
        self.assertFalse(service.can_publish()[0])
        item = service.get_entry("DLP-cavalo_1-teste")
        service.waive_issue(
            item["public_id"], "TEST_ERROR", actor="revisor.demo",
            reason="Exceção editorial justificada para teste",
        )
        self.assertTrue(service.can_publish()[0])
        changed = service.update_entry(
            item["public_id"],
            {
                "expected_updated_at": item["updated_at"], "actor": "editor.demo",
                "comment": "altera o conteúdo", "lemma": "cavalo alterado", "senses": [],
            },
        )
        self.assertFalse(any(issue.get("waiver") for issue in changed["validation_issues"]))

    def test_search_explicitly_keeps_typo_matching(self):
        client = MeiliClient("http://invalid", "test")
        captured = {}
        client.request = lambda method, path, body=None: captured.setdefault("body", body) or {}
        client.search("galinas", resource="dictionary")
        self.assertEqual(captured["body"]["queries"][0]["matchingStrategy"], "last")

    def test_typo_lemma_is_ranked_before_definition_only_matches(self):
        hits = [
            {"lemma": "deitar", "source_id": "DLP-deitar"},
            {"lemma": "dormir", "source_id": "DLP-dormir"},
            {"lemma": "Galina", "source_id": "DLP-galina"},
            {"lemma": "galinha", "source_id": "DLP-galinha"},
        ]
        hits.sort(key=lambda item: _search_sort_key(item, "galinas"))
        self.assertEqual(hits[0]["lemma"], "Galina")

    def test_assisted_rng_fix_replaces_emph_and_creates_revision(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        public_id = "DLP-cavalo_1-teste"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "UPDATE entries SET raw_xml=replace(raw_xml, '<def>mamífero doméstico</def>', '<def>mamífero <emph>doméstico</emph></def>') WHERE public_id=?",
                (public_id,),
            )
            raw = connection.execute(
                "SELECT raw_xml FROM entries WHERE public_id=?", (public_id,)
            ).fetchone()[0]
            connection.execute(
                "UPDATE entries SET raw_sha256=? WHERE public_id=?",
                (hashlib.sha256(raw.encode()).hexdigest(), public_id),
            )
        fixed = service.apply_issue_fix(
            public_id, "RNG_INVALID", "RNG_EMPH_TO_HI",
            actor="editor.demo", comment="Corrigir marca de destaque",
        )
        self.assertIn('<hi rend="italic">doméstico</hi>', fixed["raw_xml"])
        self.assertNotIn("<emph>", fixed["raw_xml"])
        self.assertEqual(fixed["workflow_status"], "EDITED")
        self.assertEqual(len(fixed["revisions"]), 1)

    def test_local_activation_is_atomic_and_reversible(self):
        import_xml(self.source, self.db)
        build_release(self.db, self.releases, release_id="test-001")
        build_release(self.db, self.releases, release_id="test-002")
        approve_release(
            self.db, self.releases, "test-001", actor="aprovador.demo"
        )
        approve_release(
            self.db, self.releases, "test-002", actor="aprovador.demo"
        )
        activate_local_release(self.db, self.releases, "test-001")
        self.assertEqual(current_release(self.releases), "test-001")
        activate_local_release(self.db, self.releases, "test-002")
        self.assertEqual(current_release(self.releases), "test-002")
        activate_local_release(self.db, self.releases, "test-001")
        self.assertEqual(current_release(self.releases), "test-001")

    def test_editorial_edit_creates_revision_and_updates_xml(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        original = service.get_entry("DLP-cavalo_1-teste")
        changed = service.update_entry(
            "DLP-cavalo_1-teste",
            {
                "expected_updated_at": original["updated_at"],
                "actor": "editor.demo",
                "comment": "Correção controlada",
                "lemma": "cavalo",
                "grammatical_info": "nome masculino",
                "editorial_status": "revisto",
                "senses": [
                    {
                        "id": original["senses"][0]["id"],
                        "definition": "mamífero doméstico de teste",
                    }
                ],
            },
        )
        self.assertEqual(changed["workflow_status"], "EDITED")
        self.assertEqual(changed["grammatical_info"], "nome masculino")
        self.assertEqual(len(changed["revisions"]), 1)
        self.assertIn("mamífero doméstico de teste", changed["raw_xml"])
        reviewed = service.set_workflow(
            "DLP-cavalo_1-teste", "REVIEWED", actor="revisor.demo"
        )
        self.assertEqual(reviewed["workflow_status"], "REVIEWED")
        validated = service.set_workflow(
            "DLP-cavalo_1-teste", "VALIDATED", actor="aprovador.demo"
        )
        self.assertEqual(validated["workflow_status"], "VALIDATED")

    def test_roles_removal_and_recovery_follow_the_simplified_workflow(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        public_id = "DLP-cavalo_1-teste"
        with self.assertRaisesRegex(Exception, "não pode aplicar"):
            service.set_workflow(public_id, "VALIDATED", actor="editor.demo")
        reviewed = service.set_workflow(public_id, "REVIEWED", actor="revisor.demo")
        self.assertEqual(reviewed["workflow_status"], "REVIEWED")
        self.assertEqual(reviewed["workflow_actor"], "revisor.demo")
        validated = service.set_workflow(public_id, "VALIDATED", actor="aprovador.demo")
        self.assertEqual(validated["workflow_status"], "VALIDATED")
        with self.assertRaisesRegex(Exception, "confirmação"):
            service.set_workflow(public_id, "REMOVED", actor="aprovador.demo")
        removed = service.set_workflow(
            public_id, "REMOVED", actor="aprovador.demo", confirmed=True
        )
        self.assertEqual(removed["workflow_status"], "REMOVED")
        recovered = service.set_workflow(public_id, "DRAFT", actor="editor.demo")
        self.assertEqual(recovered["workflow_origin"], "recovered")
        self.assertEqual(recovered["workflow_actor"], "editor.demo")

        # As permissões dependem do estado pretendido e do perfil, não de um
        # percurso rígido. Uma entrada publicada pode, por exemplo, regressar
        # diretamente à edição; a publicação continua a ser uma operação real.
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "UPDATE entries SET workflow_status='PUBLISHED' WHERE public_id=?",
                (public_id,),
            )
        edited = service.set_workflow(public_id, "EDITED", actor="editor.demo")
        self.assertEqual(edited["workflow_status"], "EDITED")
        with self.assertRaisesRegex(Exception, "não pode aplicar"):
            service.set_workflow(public_id, "PUBLISHED", actor="aprovador.demo")

    def test_unsaved_edits_block_xml_replacement(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        entry = service.get_entry("DLP-cavalo_1-teste")
        service.update_entry(
            entry["public_id"],
            {
                "actor": "editor.demo",
                "expected_updated_at": entry["updated_at"],
                "lemma": "cavalo alterado",
                "senses": [],
            },
        )
        with self.assertRaisesRegex(Exception, "alterações mais recentes"):
            service.replace_from_xml(
                self.source,
                actor="aprovador.demo",
                expected_sha256=file_sha256(self.source),
            )

    def test_readonly_access_key_can_be_rotated_and_disabled(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        first = "a" * 32
        second = "b" * 32
        status = service.set_readonly_access_key(
            first, actor="aprovador.demo"
        )
        self.assertTrue(status["enabled"])
        self.assertEqual(status["hint"], "aaaa…aaaa")
        self.assertTrue(service.readonly_key_is_valid(first))
        first_session = service.readonly_session_token(first)
        self.assertTrue(service.readonly_session_is_valid(first_session))
        with sqlite3.connect(self.db) as connection:
            stored = " ".join(
                row[0] for row in connection.execute(
                    "SELECT setting_value FROM app_settings"
                )
            )
        self.assertNotIn(first, stored)

        service.set_readonly_access_key(second, actor="aprovador.demo")
        self.assertFalse(service.readonly_key_is_valid(first))
        self.assertFalse(service.readonly_session_is_valid(first_session))
        self.assertTrue(service.readonly_key_is_valid(second))

        disabled = service.set_readonly_access_key(
            "", actor="aprovador.demo"
        )
        self.assertFalse(disabled["enabled"])
        self.assertFalse(
            service.readonly_session_is_valid(
                service.readonly_session_token(second)
            )
        )

    def test_audit_identifies_entry_and_publication_selection_action(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        public_id = "DLP-cavalo_1-teste"
        service.set_workflow(public_id, "VALIDATED", actor="aprovador.demo")
        service.select_for_publication(
            [public_id], actor="aprovador.demo", selected=True
        )
        service.select_for_publication(
            [public_id], actor="aprovador.demo", selected=False
        )
        events = [
            event for event in service.audit_report()["recent_events"]
            if event["event_type"].startswith("PUBLICATION_SELECTION")
        ]
        self.assertEqual(events[0]["event_type"], "PUBLICATION_SELECTION_REMOVE")
        self.assertEqual(events[1]["event_type"], "PUBLICATION_SELECTION_ADD")
        self.assertIsNone(events[0]["previous_state"])
        self.assertIsNone(events[0]["resulting_state"])
        self.assertIsNone(events[0]["comment"])
        self.assertEqual(events[0]["subject_type"], "entry")
        self.assertEqual(events[0]["subject_id"], public_id)
        self.assertEqual(events[0]["subject_label"], "cavalo")

    def test_published_removal_becomes_a_pending_public_operation(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE entries SET workflow_status='VALIDATED'")
        ids = ["DLP-cavalo_1-teste", "VOLP-exemplo_1-teste"]
        service.select_for_publication(ids, actor="aprovador.demo", selected=True)
        first = build_release(
            self.db, self.releases, release_id="published-base", selection_mode=True
        )
        service.mark_published(first.release_id)
        published = service.get_entry(ids[0])
        with self.assertRaisesRegex(Exception, "devolva primeiro"):
            service.update_entry(
                ids[0],
                {"actor": "editor.demo", "expected_updated_at": published["updated_at"],
                 "lemma": "não permitido", "senses": []},
            )
        removed = service.set_workflow(
            ids[0], "REMOVED", actor="aprovador.demo", confirmed=True
        )
        self.assertTrue(removed["selected_for_publication"])
        ready = service.publication_entries()
        self.assertEqual(ready["total"], 1)
        self.assertEqual(ready["items"][0]["workflow_status"], "REMOVED")
        second = build_release(
            self.db, self.releases, release_id="published-removal", selection_mode=True
        )
        self.assertNotIn(
            "DLP-cavalo_1-teste",
            (second.path / "canonical.xml").read_text(encoding="utf-8"),
        )
        service.mark_published(second.release_id)
        final = service.get_entry(ids[0])
        self.assertEqual(final["workflow_status"], "REMOVED")
        self.assertEqual(service.publication_entries()["total"], 0)

    def test_editorial_filters_controlled_lists_and_validation(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        overview = service.overview()
        resource_counts = {
            item["value"]: item["count"]
            for item in overview["filter_counts"]["resource"]
        }
        workflow_counts = {
            item["value"]: item["count"]
            for item in overview["filter_counts"]["workflow"]
        }
        domain_counts = {
            item["value"]: item["count"]
            for item in overview["filter_counts"]["domains"]
        }
        self.assertEqual(resource_counts, {"dictionary": 1, "vocabulary": 1})
        self.assertEqual(workflow_counts["DRAFT"], 1)
        self.assertEqual(workflow_counts["EDITED"], 1)
        self.assertEqual(domain_counts["Zool."], 1)
        dictionary = service.list_entries("", resource="dictionary")
        self.assertEqual(dictionary["total"], 1)
        self.assertEqual(
            {item["value"]: item["count"] for item in dictionary["facets"]["resource"]},
            {"dictionary": 1, "vocabulary": 1},
        )
        self.assertEqual(dictionary["facets"]["grammar"][0]["count"], 1)
        self.assertEqual(dictionary["facets"]["domains"][0]["value"], "Zool.")
        edited = service.list_entries("", editorial_status="edited")
        self.assertEqual(edited["total"], 1)
        result = validate_active_run(self.db)
        self.assertTrue(result.valid)
        with sqlite3.connect(self.db) as connection:
            run_id = connection.execute(
                "SELECT id FROM import_runs WHERE is_active=1"
            ).fetchone()[0]
            entry_ids = [
                row[0] for row in connection.execute(
                    "SELECT id FROM entries WHERE import_run_id=? ORDER BY id", (run_id,)
                )
            ]
            connection.executemany(
                """
                INSERT INTO validation_issues(
                    import_run_id,entry_id,severity,rule_code,message
                ) VALUES(?,?,'warning','TEST_WARNING','Aviso de teste')
                """,
                [(run_id, entry_id) for entry_id in entry_ids],
            )
        audit = service.audit_report()
        self.assertTrue(audit["validation_rules"])
        self.assertGreater(audit["validation_entries"], 0)
        rule = audit["validation_rules"][0]
        self.assertGreaterEqual(rule["occurrences"], rule["entries"])
        filtered = service.list_entries("", issue_rule=rule["rule_code"])
        self.assertEqual(filtered["total"], rule["entries"])
        self.assertTrue(
            any(
                item["value"] == rule["rule_code"]
                for item in filtered["facets"]["problems"]
            )
        )
        governance = GovernanceService(self.db)
        grammar = governance.list_values("grammar")
        self.assertTrue(any(item["value"] == "n. m." for item in grammar))

    def test_public_facets_exclude_their_own_filter(self):
        class FakeClient:
            def __init__(self):
                self.queries = []

            def request(self, method, path, payload):
                self.queries = payload["queries"]
                results = []
                for query in self.queries:
                    facets = query.get("facets") or []
                    if facets:
                        results.append({"facetDistribution": {facets[0]: {"valor": 2}}})
                    else:
                        results.append({"estimatedTotalHits": 3})
                return {"results": results}

        client = FakeClient()
        service = PublicCompatibilityService(client=client, releases=None)
        facets = service._contextual_facets(
            "cavalo", None, "n. m.", "Zool.", "edited"
        )
        grammar_query = client.queries[0]
        domain_query = client.queries[2]
        status_query = client.queries[4]
        self.assertNotIn("grammatical_categories", " ".join(grammar_query["filter"]))
        self.assertIn("domains", " ".join(grammar_query["filter"]))
        self.assertNotIn("domains", " ".join(domain_query["filter"]))
        self.assertNotIn('status =', " ".join(status_query["filter"]))
        self.assertEqual(facets["grammar"][0]["count"], 4)
        self.assertEqual(
            {item["value"]: item["count"] for item in facets["collections"]},
            {"DLP": 3, "VOCABULARIO": 3},
        )

    def test_revision_can_be_restored_with_audit_trail(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        original = service.get_entry("DLP-cavalo_1-teste")
        changed = service.update_entry(
            original["public_id"],
            {
                "expected_updated_at": original["updated_at"],
                "actor": "editor.demo",
                "comment": "alteração temporária",
                "lemma": "cavalo-teste",
                "senses": [],
            },
        )
        self.assertEqual(changed["lemma"], "cavalo-teste")
        restored = service.restore_revision(
            original["public_id"], 1, actor="editor.demo"
        )
        self.assertEqual(restored["lemma"], "cavalo")
        self.assertTrue(
            any(event["event_type"] == "REVISION_RESTORE" for event in restored["audit_events"])
        )

    def test_publication_and_rollback_rebuild_the_public_indexes(self):
        import_xml(self.source, self.db)
        for release_id in ("test-001", "test-002"):
            build_release(self.db, self.releases, release_id=release_id)
            approve_release(
                self.db,
                self.releases,
                release_id,
                actor="aprovador.demo",
            )
        manager = PublicationJobManager(
            db_path=self.db,
            releases_root=self.releases,
            images_root=None,
            meili_url="http://invalid",
            meili_key="test",
        )

        class FakeMeili:
            release_id = None
            counts = {}
            indexes = {}

            def build_release_indexes(fake, path):
                manifest = json.loads(
                    (Path(path) / "manifest.json").read_text(encoding="utf-8")
                )
                fake.release_id = manifest["release_id"]
                fake.counts = manifest["counts"]
                fake.indexes = manifest["indexes"]
                return {"indexes": manifest["indexes"]}

            def activate_release_indexes(fake, path):
                manifest = json.loads(
                    (Path(path) / "manifest.json").read_text(encoding="utf-8")
                )
                fake.release_id = manifest["release_id"]

            def request(fake, method, path):
                index = path.split("/")[2]
                resource = next(
                    key for key, value in fake.indexes.items() if value == index
                )
                return {"numberOfDocuments": fake.counts[resource]}

            def search_index(fake, index, body):
                return {"hits": [{}]}

        manager.client = FakeMeili()
        manager._publish("test-001", "aprovador.demo", False, "")
        manager._publish("test-002", "aprovador.demo", False, "")
        self.assertEqual(current_release(self.releases), "test-002")
        manager._publish("test-001", "aprovador.demo", True, "anomalia")
        self.assertEqual(current_release(self.releases), "test-001")
        self.assertEqual(manager.client.release_id, "test-001")

    def test_single_publication_action_builds_checks_and_activates(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE entries SET workflow_status='VALIDATED'")
        service.select_for_publication(
            ["DLP-cavalo_1-teste"], actor="aprovador.demo", selected=True
        )
        manager = PublicationJobManager(
            db_path=self.db, releases_root=self.releases, images_root=None,
            meili_url="http://invalid", meili_key="test",
        )

        class FakeMeili:
            def build_release_indexes(fake, path):
                fake.manifest = json.loads(
                    (Path(path) / "manifest.json").read_text(encoding="utf-8")
                )
                return {"indexes": fake.manifest["indexes"]}

            def request(fake, method, path):
                index = path.split("/")[2]
                resource = next(
                    key for key, value in fake.manifest["indexes"].items()
                    if value == index
                )
                return {"numberOfDocuments": fake.manifest["counts"][resource]}

            def search_index(fake, index, body):
                return {"hits": [{}]}

            def activate_release_indexes(fake, path):
                fake.activated = True

        manager.client = FakeMeili()
        manager._prepare_and_publish(
            "single-action", "aprovador.demo", "Teste de publicação simples"
        )
        self.assertEqual(current_release(self.releases), "single-action")
        self.assertEqual(manager.status()["state"], "succeeded")
        self.assertEqual(
            service.get_entry("DLP-cavalo_1-teste")["workflow_status"], "PUBLISHED"
        )
        with sqlite3.connect(self.db) as connection:
            connection.row_factory = sqlite3.Row
            events = connection.execute(
                "SELECT event_type,details_json FROM audit_events WHERE release_id=?",
                ("single-action",),
            ).fetchall()
        self.assertEqual([event["event_type"] for event in events], ["BULK_PUBLICATION"])
        details = json.loads(events[0]["details_json"])
        self.assertEqual(details["entries_total"], 1)
        self.assertEqual(details["published"], 1)
        self.assertEqual(details["removed"], 0)
        self.assertEqual(details["entries"][0]["public_id"], "DLP-cavalo_1-teste")
        self.assertTrue(details["canonical_xml"]["xml"].endswith(".xml"))
        canonical_path = self.root / "exports" / details["canonical_xml"]["xml"]
        self.assertTrue(canonical_path.is_file())
        self.assertIn('status="published"', canonical_path.read_text(encoding="utf-8"))
        self.assertFalse(persistence_status(self.db)["has_unsaved_changes"])

    def test_failed_smoke_test_does_not_switch_public_indexes(self):
        import_xml(self.source, self.db)
        build_release(self.db, self.releases, release_id="test-failed")
        approve_release(
            self.db,
            self.releases,
            "test-failed",
            actor="aprovador.demo",
        )
        manager = PublicationJobManager(
            db_path=self.db,
            releases_root=self.releases,
            images_root=None,
            meili_url="http://invalid",
            meili_key="test",
        )

        class FailedSmokeMeili:
            activated = False

            def build_release_indexes(fake, path):
                manifest = json.loads(
                    (Path(path) / "manifest.json").read_text(encoding="utf-8")
                )
                fake.indexes = manifest["indexes"]
                return {"indexes": manifest["indexes"]}

            def request(fake, method, path):
                return {"numberOfDocuments": 0}

            def search_index(fake, index, body):
                return {"hits": []}

            def activate_release_indexes(fake, path):
                fake.activated = True

        manager.client = FailedSmokeMeili()
        manager._publish("test-failed", "aprovador.demo", False, "")
        self.assertFalse(manager.client.activated)
        self.assertIsNone(current_release(self.releases))
        with sqlite3.connect(self.db) as connection:
            state = connection.execute(
                "SELECT state FROM releases WHERE release_id='test-failed'"
            ).fetchone()[0]
        self.assertEqual(state, "approved")

    def test_deferred_external_source_is_preserved_but_not_published(self):
        import_xml(self.source, self.db)
        with sqlite3.connect(self.db) as connection:
            entry_id = connection.execute(
                "SELECT id FROM entries WHERE public_id='DLP-cavalo_1-teste'"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO external_sources(
                    code, name, source_url, license, publication_enabled
                ) VALUES ('SPE', 'Sociedade Portuguesa de Estatística',
                          'https://example.test', 'teste', 1)
                """
            )
            connection.execute(
                """
                INSERT INTO enrichments(
                    entry_id, source_code, domain, definition,
                    source_value, raw_json, approval_status
                ) VALUES (?, 'SPE', 'Stat.', 'definição externa',
                          'accuracy', '{}', 'imported')
                """,
                (entry_id,),
            )
        set_source_publication(
            self.db,
            "SPE",
            enabled=False,
            actor="aprovador.demo",
            comment="adiada",
        )
        result = build_release(
            self.db, self.releases, release_id="without-spe"
        )
        document = json.loads(
            (result.path / "dictionary.ndjson").read_text(encoding="utf-8")
        )
        self.assertNotIn("Stat.", document["domains"])
        self.assertFalse(
            any(item.get("source") == "SPE" for item in document["provenance"])
        )
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM enrichments WHERE source_code='SPE'"
                ).fetchone()[0],
                1,
            )

    def test_controlled_values_are_ordered_and_merge_updates_the_corpus(self):
        import_xml(self.source, self.db)
        governance = GovernanceService(self.db)
        self.assertEqual(
            [user["role"] for user in governance.users()],
            ["editor", "reviewer", "approver"],
        )
        target = governance.create_value(
            {
                "actor": "revisor.demo",
                "category": "domain",
                "value": "Zoologia",
                "display_label": "Zoologia",
            }
        )
        source = next(
            value for value in governance.list_values("domain")
            if value["value"] == "Zool."
        )
        governance.merge_values(
            source["id"], target["id"], actor="revisor.demo", comment="Normalizar"
        )
        with sqlite3.connect(self.db) as connection:
            label = connection.execute(
                "SELECT value FROM labels WHERE label_type='dom'"
            ).fetchone()[0]
            raw_xml, workflow = connection.execute(
                "SELECT raw_xml,workflow_status FROM entries WHERE public_id='DLP-cavalo_1-teste'"
            ).fetchone()
        self.assertEqual(label, "Zoologia")
        self.assertIn(">Zoologia<", raw_xml)
        self.assertEqual(workflow, "EDITED")

    def test_selective_release_keeps_unselected_edits_out_of_public_data(self):
        import_xml(self.source, self.db)
        service = EditorialService(self.db)
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE entries SET workflow_status='PUBLISHED'")
        service.set_workflow(
            "DLP-cavalo_1-teste", "NEEDS_REVISION", actor="aprovador.demo"
        )
        service.set_workflow(
            "VOLP-exemplo_1-teste", "NEEDS_REVISION", actor="aprovador.demo"
        )
        cavalo = service.get_entry("DLP-cavalo_1-teste")
        service.update_entry(
            cavalo["public_id"],
            {"actor": "editor.demo", "expected_updated_at": cavalo["updated_at"],
             "lemma": "cavalo revisto", "senses": [], "comment": "revisão"},
        )
        exemplo = service.get_entry("VOLP-exemplo_1-teste")
        service.update_entry(
            exemplo["public_id"],
            {"actor": "editor.demo", "expected_updated_at": exemplo["updated_at"],
             "lemma": "exemplo revisto", "senses": [], "comment": "revisão"},
        )
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE entries SET workflow_status='VALIDATED'")
            connection.execute(
                "UPDATE entries SET lemma='',lemma_normalized='' WHERE public_id='VOLP-exemplo_1-teste'"
            )
        service.select_for_publication(
            ["DLP-cavalo_1-teste"], actor="aprovador.demo", selected=True
        )
        result = build_release(
            self.db, self.releases, release_id="selective-001", selection_mode=True
        )
        dictionary = json.loads(
            (result.path / "dictionary.ndjson").read_text(encoding="utf-8")
        )
        vocabulary = json.loads(
            (result.path / "vocabulary.ndjson").read_text(encoding="utf-8")
        )
        self.assertEqual(dictionary["lemma"], "cavalo revisto")
        self.assertEqual(vocabulary["lemma"], "exemplo")
        with sqlite3.connect(self.db) as connection:
            selected = connection.execute(
                "SELECT COUNT(*) FROM release_entries WHERE release_id='selective-001'"
            ).fetchone()[0]
        self.assertEqual(selected, 1)


if __name__ == "__main__":
    unittest.main()
