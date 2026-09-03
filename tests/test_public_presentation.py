import unittest

from acl_reference.public_compat import PublicCompatibilityService
from acl_reference.public_document import parse_public_xml


SOURCE_XML = """<entry xmlns="http://www.tei-c.org/ns/1.0"
  xmlns:xml="http://www.w3.org/XML/1998/namespace" xml:id="DLP-casa_1-teste">
  <form><orth>casa</orth><syll>ca.sa</syll><pron>ˈkazɐ</pron></form>
  <gramGrp>n. f.</gramGrp>
  <sense n="1" ana="#level_very_easy" xml:id="s1">
    <def>habitação</def>
    <xr type="synonymy"><ref type="entry">residência</ref></xr>
    <cit type="example"><quote>Exemplo principal.</quote><bibl>
      <author>Autora</author><title>Obra</title><citedRange>12</citedRange>
    </bibl></cit>
    <re><form><orth>casa de campo</orth></form><sense xml:id="s2">
      <usg type="domain">Arquitetura</usg><def>residência rural</def>
      <cit type="example"><quote type="combinacao_frequente">bela casa de campo</quote></cit>
    </sense></re>
  </sense>
</entry>"""

APAGAO_XML = """<entry xmlns="http://www.tei-c.org/ns/1.0" xml:id="DLP-apagao_1">
  <form><orth>apagão</orth><pron>ɐpɐˈgɐ̃w̃</pron><syll>a.pa.gão</syll></form>
  <gramGrp>n. m.</gramGrp>
  <sense n="1"><def>interrupção no fornecimento de energia</def>
    <cit type="example"><quote>Ocorreu um apagão.</quote><bibl><title>SIC Notícias</title><date>28/04/2025</date></bibl></cit>
  </sense>
  <sense n="2"><usg type="socioCultural">Fig.</usg><def>perda momentânea de memória</def>
    <cit type="example"><quote type="example">Tive um apagão.</quote></cit>
  </sense>
  <etym>De <ref type="entry">apagar</ref> + sufixo <ref type="entry">-ão</ref></etym>
  <note type="plural">Plural: apagões <pron>ɐpɐˈgõj̃ʃ</pron></note>
</entry>"""


class PublicPresentationTests(unittest.TestCase):
    def test_parser_preserves_lexicographic_scope_and_example_source(self):
        parsed = parse_public_xml(SOURCE_XML)
        self.assertEqual(parsed["syllabifications"], ["ca.sa"])
        self.assertEqual(parsed["pronunciations"], ["ˈkazɐ"])
        self.assertEqual(len(parsed["senses"]), 2)

        main, related = parsed["senses"]
        self.assertEqual(main["synonyms"], [{"value": "residência", "target": None}])
        self.assertEqual(main["labels"], [])
        self.assertEqual(len(main["examples"]), 1)
        self.assertEqual(main["examples"][0]["source"], "(Autora, Obra, p. 12)")
        self.assertEqual(main["readability"], "#level_very_easy")

        self.assertEqual(related["section"], "casa de campo")
        self.assertEqual(related["labels"][0]["value"], "Arquitetura")
        self.assertEqual(related["examples"][0]["type"], "combinacao_frequente")

    def test_entry_detail_enriches_an_existing_release_from_source_xml(self):
        service = PublicCompatibilityService(client=None, releases=None)
        detail = service.entry_detail(
            {
                "source_id": "DLP-casa_1-teste",
                "resource": "dictionary",
                "lemma": "casa",
                "grammatical_categories": ["n. f."],
                "status": "edited",
                "definitions_text": "habitação",
                "senses": [{"id": "s1", "definition_segments": []}],
                "source_xml": SOURCE_XML,
            }
        )
        lexical = detail["lexical"]
        self.assertEqual(lexical["syllabifications"], ["ca.sa"])
        self.assertEqual(lexical["pronunciations"], ["ˈkazɐ"])
        self.assertEqual(lexical["senses"][0]["synonyms"][0]["value"], "residência")
        self.assertEqual(lexical["senses"][1]["section"], "casa de campo")

    def test_parser_keeps_etymology_and_plural_out_of_cross_references(self):
        parsed = parse_public_xml(APAGAO_XML)
        self.assertEqual(parsed["pronunciations"], ["ɐpɐˈgɐ̃w̃"])
        self.assertEqual(parsed["relations"], [])
        self.assertEqual(parsed["all_relations"], [])
        self.assertEqual(
            parsed["etymology_items"][0]["segments"],
            [
                {"text": "De "}, {"text": "apagar", "query": "apagar"},
                {"text": " + sufixo "}, {"text": "-ão", "query": "-ão"},
            ],
        )
        self.assertEqual(
            parsed["notes"],
            [{"type": "plural", "value": "Plural: apagões", "pronunciations": ["ɐpɐˈgõj̃ʃ"]}],
        )
        self.assertEqual(parsed["senses"][0]["examples"][0]["source"], "(SIC Notícias, 28/04/2025)")
        self.assertTrue(parsed["senses"][0]["examples"][0]["has_bibliography"])
        self.assertFalse(parsed["senses"][1]["examples"][0]["has_bibliography"])

    def test_entry_detail_expands_figurado_and_preserves_structured_notes(self):
        service = PublicCompatibilityService(client=None, releases=None)
        detail = service.entry_detail(
            {
                "source_id": "DLP-apagao_1",
                "resource": "dictionary",
                "lemma": "apagão",
                "grammatical_categories": ["n. m."],
                "status": "reviewed",
                "definitions_text": "interrupção perda de memória",
                "senses": [],
                "source_xml": APAGAO_XML,
            }
        )
        lexical = detail["lexical"]
        self.assertEqual(
            lexical["senses"][1]["labels"][0]["label"],
            "Fig. (Figurado)",
        )
        self.assertEqual(lexical["references"], [])
        self.assertEqual(
            lexical["notes"][0]["pronunciations"], ["ɐpɐˈgõj̃ʃ"]
        )


if __name__ == "__main__":
    unittest.main()
