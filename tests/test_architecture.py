from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
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
from acl_reference.public_compat import _search_sort_key
from acl_reference.validation import validate_active_run


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
        self.assertEqual(fixed["workflow_status"], "EDITING")
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
        self.assertEqual(changed["workflow_status"], "EDITING")
        self.assertEqual(changed["grammatical_info"], "nome masculino")
        self.assertEqual(len(changed["revisions"]), 1)
        self.assertIn("mamífero doméstico de teste", changed["raw_xml"])
        reviewed = service.set_workflow(
            "DLP-cavalo_1-teste", "REVIEW", actor="editor.demo"
        )
        self.assertEqual(reviewed["workflow_status"], "REVIEW")
        validated = service.set_workflow(
            "DLP-cavalo_1-teste", "VALIDATED", actor="revisor.demo"
        )
        self.assertEqual(validated["workflow_status"], "VALIDATED")

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
        self.assertEqual(workflow_counts["IMPORTED"], 2)
        self.assertEqual(domain_counts["Zool."], 1)
        dictionary = service.list_entries("", resource="dictionary")
        self.assertEqual(dictionary["total"], 1)
        edited = service.list_entries("", editorial_status="edited")
        self.assertEqual(edited["total"], 1)
        result = validate_active_run(self.db)
        self.assertTrue(result.valid)
        governance = GovernanceService(self.db)
        grammar = governance.list_values("grammar")
        self.assertTrue(any(item["value"] == "n. m." for item in grammar))

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


if __name__ == "__main__":
    unittest.main()
