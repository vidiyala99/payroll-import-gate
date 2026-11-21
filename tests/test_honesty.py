"""Honesty guards for this MOCK/SAMPLE data demonstration: labels, public-source citations, no network code."""

import json
import re
import unittest

from gate.catalog import CODES, SCENARIOS

from .helpers import ROOT


class TestMockHonesty(unittest.TestCase):
    def test_mock_every_code_cites_a_public_scenario(self):
        for c in CODES.values():
            s = SCENARIOS[c.scenario]
            self.assertTrue(s.url.startswith("https://"), c.code)
            self.assertIn(s.label, {"reported", "vendor-stated", "posting text", "public API schema"})
            self.assertTrue(s.modeled_on.strip())

    def test_mock_reddit_and_review_sources_are_labeled_reported(self):
        for s in SCENARIOS.values():
            if "reddit.com" in s.url or "employerrecords.com" in s.url:
                self.assertEqual(s.label, "reported", s.id)

    def test_mock_readme_states_mock_data_at_top_and_lists_every_scenario(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("MOCK/SAMPLE DATA", readme[:600])
        for s in SCENARIOS.values():
            self.assertIn(s.url, readme, s.id)
        self.assertIn("What is NOT claimed", readme)

    def test_mock_every_python_file_header_says_mock_or_sample(self):
        for p in list((ROOT / "gate").glob("*.py")) + list((ROOT / "tests").glob("test_*.py")):
            head = p.read_text(encoding="utf-8")[:900]
            self.assertTrue("MOCK/SAMPLE" in head or "MOCK" in head, p.name)

    def test_mock_every_sample_file_is_labeled_or_listed_in_a_labeled_manifest(self):
        for d in ("acme-switch", "acme-switch-clean"):
            m = json.loads((ROOT / "samples" / d / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("MOCK/SAMPLE DATA", m["_mock"])
        self.assertIn("MOCK/SAMPLE", json.loads((ROOT / "samples/evals/cases.json").read_text(encoding="utf-8"))["_mock"])

    def test_mock_no_network_or_subprocess_code(self):
        pat = re.compile(r"^\s*(import|from)\s+(socket|urllib|http|requests|httpx|aiohttp|subprocess|smtplib)\b", re.M)
        for p in (ROOT / "gate").glob("*.py"):
            self.assertIsNone(pat.search(p.read_text(encoding="utf-8")), p.name)


if __name__ == "__main__":
    unittest.main()
