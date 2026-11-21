"""Pay-change explanations against the diff, and the labeled eval set (MOCK/SAMPLE data)."""

import io
import unittest
from contextlib import redirect_stdout
from decimal import Decimal

from gate.__main__ import main
from gate.dataset import load_dataset
from gate.evals import run_evals, summarize
from gate.explain import check_explanation, deterministic_explanation, pay_diff
from gate.pipeline import build

from .helpers import CLEAN, EVALS


class TestMockExplanations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = build(load_dataset(CLEAN))

    def test_mock_diff_for_hourly_worker(self):
        d = {r.component: r for r in pay_diff(self.res, "W002")}
        self.assertEqual(d["gross"].delta, Decimal("177.00"))
        self.assertEqual(d["overtime_pay"].current, Decimal("180.00"))
        self.assertEqual(d["rate"].note, "effective 2026-11-09")
        self.assertEqual(d["regular_hours"].delta, Decimal("-2"))

    def test_mock_template_explanation_is_faithful(self):
        for wid in ("W002", "W003", "W001", "W101"):
            rows = pay_diff(self.res, wid)
            self.assertTrue(check_explanation(deterministic_explanation(rows), rows).ok, wid)

    def test_mock_wrong_number_is_caught(self):
        rows = pay_diff(self.res, "W003")
        f = check_explanation("Your gross pay fell from $2,240.00 to $2,212.00, a drop of $29.00.", rows)
        self.assertFalse(f.ok)
        self.assertEqual(f.unsupported_numbers, ["29.00 (bound to gross)"])

    def test_mock_reviewer_counterexample_wrong_direction(self):
        rows = pay_diff(self.res, "W003")  # gross fell by 28.00
        f = check_explanation("Your gross pay rose by $28.00. Your regular hours went from 80 to 76. "
                              "You worked 2 overtime hours.", rows)
        self.assertFalse(f.ok)
        self.assertTrue(f.wrong_direction)

    def test_mock_reviewer_counterexample_number_on_wrong_component(self):
        rows = pay_diff(self.res, "W003")  # 28 is in the diff, but not as an overtime-hours value
        f = check_explanation("Your gross pay fell from $2,240.00 to $2,212.00. Your regular hours went from 80 to 76. "
                              "Your overtime hours went from 0 to 28.", rows)
        self.assertFalse(f.ok)
        self.assertTrue(f.wrong_from_to)
        f2 = check_explanation("Your gross pay fell by $28.00. Your regular hours fell by 4. "
                               "You worked 28 overtime hours.", rows)
        self.assertIn("28 (bound to overtime_hours)", f2.unsupported_numbers)

    def test_mock_reversed_from_to_is_caught(self):
        rows = pay_diff(self.res, "W003")
        f = check_explanation("Your gross pay went from $2,212.00 to $2,240.00. Your regular hours went from 80 to 76. "
                              "Your overtime hours went from 0 to 2.", rows)
        self.assertEqual(len(f.wrong_from_to), 1)

    def test_mock_stayed_for_a_changed_value_is_caught(self):
        rows = pay_diff(self.res, "W002")  # rate changed 30.00 -> 31.50
        f = check_explanation("Your gross pay rose by $177.00. Your hourly rate stayed at $30.00. "
                              "Your regular hours went from 80 to 78. Your overtime hours went from 0 to 4.", rows)
        self.assertTrue(any("stayed" in w for w in f.wrong_direction))

    def test_mock_invented_reason_is_caught(self):
        rows = pay_diff(self.res, "W001")  # salaried, no change
        f = check_explanation("Your gross pay did not change, but a bonus was added.", rows)
        self.assertFalse(f.ok)
        self.assertTrue(f.out_of_diff_claims)

    def test_mock_sentence_with_no_diff_component_is_caught(self):
        rows = pay_diff(self.res, "W001")
        f = check_explanation("Your gross pay did not change. Payroll had a busy month.", rows)
        self.assertEqual(f.unsupported_sentences, ["Payroll had a busy month."])

    def test_mock_omitting_a_change_is_caught(self):
        rows = pay_diff(self.res, "W002")
        f = check_explanation("Your gross pay rose from $2,400.00 to $2,577.00.", rows)
        self.assertEqual(sorted(f.uncovered_components), ["overtime_hours", "rate", "regular_hours"])

    def test_mock_dates_are_not_treated_as_amounts(self):
        rows = pay_diff(self.res, "W002")
        text = deterministic_explanation(rows)
        self.assertIn("2026-11-09", text)
        self.assertEqual(check_explanation(text, rows).unsupported_numbers, [])

    def test_mock_cli_explain(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["explain", str(CLEAN), "W003"])
        self.assertIn("faithfulness check: PASS", buf.getvalue())


class TestMockEvalSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.expl = run_evals(EVALS)
        cls.summary = summarize(cls.cases, cls.expl)

    def test_mock_eval_false_accepts_must_be_zero(self):
        fa = [c.id for c in self.cases if c.outcome == "FALSE_ACCEPT"]
        self.assertEqual(fa, [])
        self.assertEqual(self.summary["false_accepts"], 0)

    def test_mock_eval_set_size_and_known_false_reject(self):
        self.assertGreaterEqual(self.summary["defect_cases"], 20)
        self.assertGreaterEqual(self.summary["clean_cases"], 10)
        fr = [c.id for c in self.cases if c.outcome == "FALSE_REJECT"]
        self.assertEqual(fr, ["C08-field-worker-really-9to5"])  # documented trade-off, not hidden

    def test_mock_every_scenario_has_a_defect_case(self):
        from gate.catalog import CODES
        planted = {c.scenario for c in self.cases if c.label == "defect"}
        for code in CODES.values():
            if code.severity == "BLOCK" and code.code != "REGISTER_ROW_MISSING":
                self.assertIn(code.scenario, planted, code.code)

    def test_mock_explanation_checks_all_correct_including_the_unfaithful_one(self):
        self.assertEqual(self.summary["explanation_checks_correct"], self.summary["explanation_checks"])
        for eid in ("E3-W003-model-unfaithful", "E6-W003-wrong-direction", "E7-W003-number-wrong-component"):
            self.assertFalse(next(e for e in self.expl if e.id == eid).faithful, eid)
        self.assertGreaterEqual(sum(1 for e in self.expl if e.expect_faithful), 2)

    def test_mock_eval_passes_overall(self):
        self.assertTrue(self.summary["passed"])


if __name__ == "__main__":
    unittest.main()
