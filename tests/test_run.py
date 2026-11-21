"""Approval and posting on MOCK/SAMPLE data: AI prepares, a named human posts, never while blocked."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from gate.__main__ import main
from gate.dataset import load_dataset
from gate.pipeline import apply_resolutions, build
from gate.run import GateBlocked, NotApproved, PayRun, Store

from .helpers import CLEAN, DEMO


class TestMockPosting(unittest.TestCase):
    def test_mock_blocked_run_cannot_be_approved_or_posted(self):
        store = Store()
        run = PayRun("demo", build(load_dataset(DEMO)), store)
        with self.assertRaises(GateBlocked):
            run.approve("A. Reviewer")
        with self.assertRaises(GateBlocked):
            run.post()
        self.assertEqual(store.posted("demo"), [])
        events = [r[0] for r in store.db.execute("SELECT event FROM audit_log")]
        self.assertEqual(events, ["staged", "approve_refused"])

    def test_mock_clean_run_needs_named_approval_before_posting(self):
        store = Store()
        run = PayRun("clean", build(load_dataset(CLEAN)), store)
        with self.assertRaises(NotApproved):
            run.post()
        with self.assertRaises(ValueError):
            run.approve("   ")
        run.approve("A. Reviewer")
        n = run.post()
        self.assertEqual(run.status, "POSTED")
        self.assertEqual(n, len(store.posted("clean")))
        self.assertTrue(all(r[4] == "A. Reviewer" for r in store.posted("clean")))
        gross = {r[0]: r[2] for r in store.posted("clean") if r[1] == "gross"}
        self.assertEqual(gross["W002"], "2577.00")

    def test_mock_staged_values_and_provenance_are_persisted(self):
        store = Store()
        PayRun("clean", build(load_dataset(CLEAN)), store)
        row = store.db.execute("SELECT value, sources FROM staged_values WHERE worker='W002' AND field='gross'").fetchone()
        self.assertEqual(row[0], "2577.00")
        self.assertIn("comp_changes.csv:row 4:[New Rate]", json.loads(row[1]))

    def test_mock_resolutions_need_a_reviewer_and_exclusion_removes_the_worker(self):
        res = build(load_dataset(DEMO))
        hours = next(e for e in res.exceptions if e.code == "HOURS_MISSING")
        unknown = apply_resolutions(res, [{"exception_id": hours.id, "decision": "accept", "reviewer": ""}])
        self.assertEqual(unknown, [hours.id])
        self.assertTrue(hours.blocking)
        apply_resolutions(res, [{"exception_id": hours.id, "decision": "exclude_worker", "reviewer": "A. Reviewer",
                                 "note": "pay off-cycle once hours arrive"}])
        self.assertFalse(hours.blocking)
        self.assertIn("W005", res.excluded)

    def test_mock_all_blocks_resolved_then_post_skips_excluded_workers(self):
        res = build(load_dataset(DEMO))
        decisions = []
        for e in res.blocks:
            if e.worker_id:
                decisions.append({"exception_id": e.id, "decision": "exclude_worker", "reviewer": "A. Reviewer"})
            else:
                decisions.append({"exception_id": e.id, "decision": "accept", "reviewer": "A. Reviewer", "note": "checked"})
        apply_resolutions(res, decisions)
        self.assertEqual(res.status, "READY_FOR_APPROVAL")
        store = Store()
        run = PayRun("demo", res, store)
        run.approve("A. Reviewer")
        run.post()
        posted_workers = {r[0] for r in store.posted("demo")}
        self.assertEqual(posted_workers & res.excluded, set())
        self.assertIn("W002", posted_workers)


class TestMockPostingWithoutGross(unittest.TestCase):
    def test_mock_accepted_hours_missing_does_not_post_a_worker_without_gross(self):
        res = build(load_dataset(DEMO))
        apply_resolutions(res, [{"exception_id": e.id, "decision": "accept", "reviewer": "A. Reviewer",
                                 "note": "accepting everything to test the posting guard"} for e in res.blocks])
        self.assertEqual(res.status, "READY_FOR_APPROVAL")
        self.assertNotIn("gross", res.staged["W005"])
        store = Store()
        run = PayRun("demo", res, store)
        run.approve("A. Reviewer")
        run.post()
        posted_workers = {r[0] for r in store.posted("demo")}
        for wid in ("W003", "W004", "W005", "W102"):  # no gross staged for these
            self.assertNotIn(wid, posted_workers)
            self.assertIn(wid, run.skipped)
        self.assertIn("W002", posted_workers)
        events = [r[0] for r in store.db.execute("SELECT event FROM audit_log")]
        self.assertIn("skipped_no_gross", events)


class TestMockCli(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(args))
        return code, buf.getvalue()

    def test_mock_cli_run_prints_banner_queue_and_trace(self):
        code, out = self.run_cli("run", str(DEMO), "--trace", "W002:gross")
        self.assertEqual(code, 3)  # blocked run exits non-zero
        self.assertIn("MOCK/SAMPLE DATA", out)
        self.assertIn("Status: BLOCKED", out)
        self.assertIn("HOURS_MISSING", out)
        self.assertIn("modeled on (reported, 2026-07-31)", out)
        self.assertIn("TRACE W002:gross", out)

    def test_mock_cli_post_refuses_blocked_and_posts_clean(self):
        code, out = self.run_cli("post", str(DEMO), "--approver", "A. Reviewer")
        self.assertEqual(code, 2)
        self.assertIn("REFUSED", out)
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "out.sqlite")
            code, out = self.run_cli("post", str(CLEAN), "--approver", "A. Reviewer", "--db", db)
            self.assertEqual(code, 0)
            self.assertIn("POSTED", out)

    def test_mock_cli_resolutions_file(self):
        res = build(load_dataset(DEMO))
        decisions = [{"exception_id": e.id, "decision": "exclude_worker" if e.worker_id else "accept",
                      "reviewer": "A. Reviewer"} for e in res.blocks]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "res.json"
            p.write_text(json.dumps({"resolutions": decisions}), encoding="utf-8")
            code, out = self.run_cli("run", str(DEMO), "--resolutions", str(p))
        self.assertIn("Status: READY_FOR_APPROVAL", out)
        self.assertIn("resolved by A. Reviewer", out)

    def test_mock_cli_run_clean_exits_zero(self):
        code, out = self.run_cli("run", str(CLEAN))
        self.assertEqual(code, 0)
        self.assertIn("READY_FOR_APPROVAL", out)

    def test_mock_cli_trace_unknown_field_is_explained(self):
        _, out = self.run_cli("run", str(DEMO), "--trace", "W005:gross")
        self.assertIn("not staged", out)


if __name__ == "__main__":
    unittest.main()
