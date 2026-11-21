"""Staging, provenance and the gate on the MOCK/SAMPLE datasets."""

import unittest
from decimal import Decimal

from gate.dataset import apply_patches, load_dataset
from gate.pipeline import build

from .helpers import CLEAN, DEMO


class TestMockCleanDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(CLEAN)
        cls.res = build(cls.ds)

    def test_mock_clean_dataset_is_ready_for_approval(self):
        self.assertEqual(self.res.status, "READY_FOR_APPROVAL", [e.message for e in self.res.exceptions])
        self.assertEqual(self.res.exceptions, [])

    def test_mock_staged_gross_matches_prior_register_for_every_worker(self):
        expected = {"W001": "4000.00", "W002": "2577.00", "W003": "2212.00", "W004": "2000.00",
                    "W101": "150000.00", "W102": "80000.00"}
        for wid, g in expected.items():
            self.assertEqual(self.res.staged[wid]["gross"].value, Decimal(g), wid)

    def test_mock_overtime_split_per_week_and_rate_change_mid_period(self):
        s = self.res.staged["W002"]
        self.assertEqual(s["hours.regular"].value, Decimal("78"))
        self.assertEqual(s["hours.overtime"].value, Decimal("4"))
        self.assertEqual(s["pay.overtime"].value, Decimal("180.00"))  # 4 h x 30.00 x 1.5 (week 1, old rate)
        segs = {x.field: x.value for x in s["pay.regular"].inputs}
        self.assertEqual(segs, {"pay.regular@30.00": Decimal("1200.00"), "pay.regular@31.50": Decimal("1197.00")})

    def test_mock_overnight_shift_counts_on_start_date(self):
        shifts = [i for i in self.res.staged["W003"]["hours.regular"].inputs if i.field == "shift@2026-11-12"]
        self.assertEqual(shifts[0].value, Decimal("8.5"))

    def test_mock_every_staged_value_traces_to_source_cells(self):
        for wid, vals in self.res.staged.items():
            for f, sv in vals.items():
                srcs = sv.all_sources()
                self.assertTrue(srcs, f"{wid} {f} has no provenance")
                for p in srcs:
                    self.assertTrue(p.file and p.column, f"{wid} {f}: {p}")
                    self.assertGreaterEqual(p.row, 1)

    def test_mock_trace_reaches_original_text_of_rate_and_hours(self):
        texts = {p.original_text for p in self.res.staged["W002"]["gross"].all_sources()}
        self.assertIn("$31.50", texts)  # comp sheet cell
        self.assertIn("8h30m", texts)  # time clock cell in its original odd format
        lines = "\n".join(self.res.staged["W002"]["gross"].trace_lines())
        self.assertIn("comp_changes.csv:row 4:[New Rate]", lines)
        self.assertIn("(shown above)", lines)

    def test_mock_deductions_are_carried_with_register_provenance(self):
        d = self.res.staged["W101"]["deduction:tds"]
        self.assertEqual(d.value, Decimal("12500.00"))
        self.assertEqual(d.sources[0].column, "TDS")
        self.assertEqual(d.unit, "INR")

    def test_mock_salaried_proration_by_days(self):
        ds = apply_patches(self.ds, [
            {"op": "append", "file": "comp_changes.csv", "row": {"Employee": "Rao, Arjun", "Effective": "11/16/2026",
                                                                 "Rate Type": "Annual", "New Rate": "₹10,80,000",
                                                                 "Curr": "INR", "Reason": "x"}}], "prorate")
        res = build(ds)
        self.assertEqual(res.staged["W102"]["gross"].value, Decimal("85000.00"))
        self.assertEqual([e.code for e in res.blocks], ["REGISTER_RECONCILE_MISMATCH"])  # register still says 80,000


class TestMockRobustness(unittest.TestCase):
    """Malformed MOCK inputs must produce exceptions, never a crash or an unblocked zero."""

    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(CLEAN)

    def codes(self, patches):
        return {e.code for e in build(apply_patches(self.ds, patches, "t")).blocks}

    def test_mock_dropping_any_required_column_blocks_without_crashing(self):
        for fn, col in [("prior_register.csv", "Co Code"), ("prior_register.csv", "Period Beg"),
                        ("prior_register.csv", "Emp #"), ("prior_register.csv", "Curr"),
                        ("comp_changes.csv", "Rate Type"), ("comp_changes.csv", "Effective"),
                        ("comp_changes.csv", "Employee"), ("timeclock_export.csv", "Work Date"),
                        ("timeclock_export.csv", "Badge")]:
            with self.subTest(col=col):
                self.assertIn("MAPPING_UNRESOLVED", self.codes([{"op": "drop_column", "file": fn, "column": col}]))

    def test_mock_dropping_optional_columns_does_not_crash(self):
        for fn, col in [("timeclock_export.csv", "In"), ("timeclock_export.csv", "Brk"),
                        ("timeclock_export.csv", "Hrs"), ("comp_changes.csv", "Curr"), ("prior_register.csv", "Rate")]:
            with self.subTest(col=col):
                build(apply_patches(self.ds, [{"op": "drop_column", "file": fn, "column": col}], "t"))

    def test_mock_hourly_worker_without_shifts_or_expected_minimum_is_blocked(self):
        codes = self.codes([{"op": "set", "file": "roster.csv", "where": {"worker_id": "W004"},
                             "set": {"expected_hours_min": ""}},
                            {"op": "delete", "file": "timeclock_export.csv", "where": {"Badge": "2004"}}])
        self.assertIn("HOURS_MISSING", codes)

    def test_mock_rule_starting_after_period_start_blocks_not_crashes(self):
        codes = self.codes([{"op": "json_set", "file": "entities.json",
                             "path": ["entities", "IN-01", "rules", 0, "effective_from"], "value": "2026-11-10"}])
        self.assertIn("RULE_MISSING", codes)

    def test_mock_negative_values_block(self):
        self.assertIn("NEGATIVE_VALUE", self.codes([{"op": "set", "file": "prior_register.csv",
                                                     "where": {"Emp #": "A-0001", "Period Beg": "11/02/2026"},
                                                     "set": {"FIT W/H": "(520.00)"}}]))

    def test_mock_hours_only_entry_on_same_day_is_flagged(self):
        codes = self.codes([{"op": "append", "file": "timeclock_export.csv",
                             "row": {"Badge": "2004", "Emp Name": "KIM J", "Work Date": "11/02/2026", "Hrs": "8"}}])
        self.assertIn("DUPLICATE_SHIFT", codes)


class TestMockDemoDataset(unittest.TestCase):
    """The 'as delivered' demo set has problems planted on purpose."""

    @classmethod
    def setUpClass(cls):
        cls.res = build(load_dataset(DEMO))

    def test_mock_demo_is_blocked_with_the_planted_problems(self):
        codes = {e.code for e in self.res.blocks}
        self.assertEqual(self.res.status, "BLOCKED")
        self.assertEqual(codes, {"COMP_EFFECTIVE_CONFLICT", "DEFAULT_SCHEDULE_SUSPECTED", "ENTITY_MISMATCH",
                                 "FX_BASIS_MISSING", "HOURS_MISSING", "MAPPING_UNRESOLVED",
                                 "REGISTER_INTERNAL_MISMATCH", "WORKER_UNMATCHED"})

    def test_mock_no_gross_is_staged_for_blocked_workers(self):
        for wid in ("W003", "W004", "W005", "W102"):
            self.assertNotIn("gross", self.res.staged[wid], wid)

    def test_mock_foreign_currency_rate_is_never_used(self):
        self.assertNotIn("pay.regular", self.res.staged["W102"])

    def test_mock_every_exception_has_source_or_config_reference_and_an_action(self):
        for e in self.res.exceptions:
            self.assertTrue(e.sources, e.code)
            self.assertTrue(e.action)
            self.assertTrue(e.id.startswith("X"))

    def test_mock_exception_ids_are_stable_across_runs(self):
        again = build(load_dataset(DEMO))
        self.assertEqual(sorted(e.id for e in self.res.exceptions), sorted(e.id for e in again.exceptions))


if __name__ == "__main__":
    unittest.main()
