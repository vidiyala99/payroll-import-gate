"""Mapping acceptance rules, using a hand-written fixture in place of an LLM (MOCK/SAMPLE data)."""

import unittest

from gate.mapping import MappingProposal, RecordedMappingAssistant, resolve_mapping

DED = {"fed_income_tax", "tds"}
HEADERS = ["Emp #", "Co Code", "Period Beg", "Period End", "Gross Pay", "Net Pay", "Curr"]


def rows(extra=None):
    r = {h: "x" for h in HEADERS}
    r.update(extra or {})
    return [(2, r)]


def fixture(*props):
    return RecordedMappingAssistant({"proposals": {"register": [
        {"source_column": c, "target_field": t, "confidence": conf, "rationale": "fixture"} for c, t, conf in props]}})


class TestMockMapping(unittest.TestCase):
    def codes(self, exc):
        return sorted(e.code for e in exc)

    def test_mock_synonyms_map_without_any_assistant(self):
        m, exc = resolve_mapping("register", "r.csv", HEADERS, rows(), None, DED)
        self.assertEqual(exc, [])
        self.assertEqual(m["Gross Pay"].target, "gross")
        self.assertIn("deterministic", m["Gross Pay"].note)

    def test_mock_ai_proposal_accepted_at_threshold_and_noted_in_provenance(self):
        m, exc = resolve_mapping("register", "r.csv", HEADERS + ["FIT W/H"], rows({"FIT W/H": "1.00"}),
                                 fixture(("FIT W/H", "deduction:fed_income_tax", 0.90)), DED)
        self.assertEqual(exc, [])
        self.assertEqual(m["FIT W/H"].target, "deduction:fed_income_tax")
        self.assertIn("confidence 0.90", m["FIT W/H"].note)

    def test_mock_low_confidence_blocks(self):
        _, exc = resolve_mapping("register", "r.csv", HEADERS + ["Misc"], rows(),
                                 fixture(("Misc", "deduction:tds", 0.89)), DED)
        self.assertEqual(self.codes(exc), ["MAPPING_UNRESOLVED"])

    def test_mock_unknown_target_blocks_even_at_high_confidence(self):
        _, exc = resolve_mapping("register", "r.csv", HEADERS + ["Misc"], rows(),
                                 fixture(("Misc", "deduction:union_dues", 0.99)), DED)
        self.assertEqual(self.codes(exc), ["MAPPING_UNRESOLVED"])
        self.assertIn("not a field", exc[0].message)

    def test_mock_ai_cannot_override_a_synonym(self):
        _, exc = resolve_mapping("register", "r.csv", HEADERS, rows(), fixture(("Gross Pay", "net", 0.99)), DED)
        self.assertIn("MAPPING_CONFLICT", self.codes(exc))

    def test_mock_ignore_refused_for_money_column(self):
        _, exc = resolve_mapping("register", "r.csv", HEADERS + ["Bonus"], rows({"Bonus": "$100.00"}),
                                 fixture(("Bonus", "ignore", 0.99)), DED)
        self.assertEqual(self.codes(exc), ["MAPPING_UNRESOLVED"])

    def test_mock_ignore_allowed_for_text_column(self):
        m, exc = resolve_mapping("register", "r.csv", HEADERS + ["Dept"], rows({"Dept": "Field Ops"}),
                                 fixture(("Dept", "ignore", 0.95)), DED)
        self.assertEqual(exc, [])
        self.assertEqual(m["Dept"].target, "ignore")

    def test_mock_column_with_no_proposal_blocks(self):
        _, exc = resolve_mapping("register", "r.csv", HEADERS + ["???"], rows(), None, DED)
        self.assertEqual(self.codes(exc), ["MAPPING_UNRESOLVED"])

    def test_mock_two_columns_same_target_conflict(self):
        _, exc = resolve_mapping("register", "r.csv", HEADERS + ["Gross"], rows(), None, DED)
        self.assertEqual(self.codes(exc).count("MAPPING_CONFLICT"), 2)

    def test_mock_missing_required_field_blocks(self):
        _, exc = resolve_mapping("register", "r.csv", [h for h in HEADERS if h != "Net Pay"], rows(), None, DED)
        self.assertEqual(self.codes(exc), ["MAPPING_UNRESOLVED"])
        self.assertEqual(exc[0].field, "net")

    def test_mock_assistant_protocol_accepts_any_object_with_propose(self):
        class Live:
            name = "stub"

            def propose(self, kind, headers, sample_rows):
                return [MappingProposal("TDS", "deduction:tds", 0.97, "stub", self.name)]

        m, exc = resolve_mapping("register", "r.csv", HEADERS + ["TDS"], rows(), Live(), DED)
        self.assertEqual(exc, [])
        self.assertIn("stub", m["TDS"].note)


if __name__ == "__main__":
    unittest.main()
