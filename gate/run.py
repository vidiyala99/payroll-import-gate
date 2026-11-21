"""Approval and posting: AI prepares, a human posts.

MOCK/SAMPLE DATA DEMONSTRATION. "Posting" writes rows to a local SQLite table and nothing
else; there is no network code in this package. A run can be posted only after a named human
approves it, and approval is refused while any BLOCK exception is unresolved.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .pipeline import GateResult


class GateBlocked(RuntimeError):
    pass


class NotApproved(RuntimeError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (ts TEXT, run TEXT, actor TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS exceptions (run TEXT, id TEXT, code TEXT, severity TEXT, worker TEXT, field TEXT,
    message TEXT, sources TEXT, resolved_by TEXT, resolution TEXT);
CREATE TABLE IF NOT EXISTS staged_values (run TEXT, worker TEXT, field TEXT, value TEXT, unit TEXT,
    derivation TEXT, sources TEXT);
CREATE TABLE IF NOT EXISTS posted_lines (run TEXT, worker TEXT, field TEXT, value TEXT, unit TEXT,
    approved_by TEXT, posted_at TEXT);
"""


class Store:
    """Local SQLite store (in memory by default). Holds the audit trail and the 'posted' lines."""

    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def log(self, run: str, actor: str, event: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit_log VALUES (?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), run, actor, event, detail))
        self.db.commit()

    def save_result(self, run: str, result: GateResult) -> None:
        for e in result.exceptions:
            self.db.execute("INSERT INTO exceptions VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (run, e.id, e.code, e.severity, e.worker_id, e.field, e.message,
                             json.dumps([s.ref() for s in e.sources]), e.resolved_by, e.resolution))
        for wid, vals in result.staged.items():
            for f, sv in vals.items():
                self.db.execute("INSERT INTO staged_values VALUES (?,?,?,?,?,?,?)",
                                (run, wid, f, str(sv.value), sv.unit, sv.derivation,
                                 json.dumps([s.ref() for s in sv.all_sources()])))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def posted(self, run: str) -> list[tuple]:
        return self.db.execute("SELECT worker, field, value, unit, approved_by FROM posted_lines WHERE run=? "
                               "ORDER BY worker, field", (run,)).fetchall()


class PayRun:
    POSTABLE = ("hours.regular", "hours.overtime", "pay.regular", "pay.overtime", "gross")

    def __init__(self, run_id: str, result: GateResult, store: Store):
        self.run_id, self.result, self.store = run_id, result, store
        self.approved_by: str | None = None
        self.posted = False
        self.skipped: list[str] = []
        store.save_result(run_id, result)
        store.log(run_id, "gate", "staged", f"status={result.status} blocks={len(result.blocks)}")

    @property
    def status(self) -> str:
        if self.posted:
            return "POSTED"
        if self.approved_by:
            return "APPROVED"
        return self.result.status

    def approve(self, approver: str) -> None:
        if not approver or not approver.strip():
            raise ValueError("approval needs a named human approver")
        if self.result.blocks:
            self.store.log(self.run_id, approver, "approve_refused", f"{len(self.result.blocks)} unresolved blocks")
            raise GateBlocked(f"{len(self.result.blocks)} unresolved BLOCK exceptions: "
                              + ", ".join(sorted({e.code for e in self.result.blocks})))
        self.approved_by = approver.strip()
        self.store.log(self.run_id, self.approved_by, "approved")

    def post(self) -> int:
        if self.result.blocks:
            raise GateBlocked("run has unresolved BLOCK exceptions")
        if not self.approved_by:
            raise NotApproved("run must be approved by a named human before posting")
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        self.skipped: list[str] = []
        for wid, vals in sorted(self.result.staged.items()):
            if wid in self.result.excluded:
                continue
            if "gross" not in vals:
                # e.g. a human accepted HOURS_MISSING or RATE_MISSING: there is no gross to pay, so nothing is posted
                self.skipped.append(wid)
                self.store.log(self.run_id, self.approved_by, "skipped_no_gross", wid)
                continue
            for f in self.POSTABLE:
                if f in vals:
                    sv = vals[f]
                    self.store.db.execute("INSERT INTO posted_lines VALUES (?,?,?,?,?,?,?)",
                                          (self.run_id, wid, f, str(sv.value), sv.unit, self.approved_by, now))
                    n += 1
        self.store.db.commit()
        self.posted = True
        self.store.log(self.run_id, self.approved_by, "posted",
                       f"{n} lines to local SQLite; skipped (no gross): {self.skipped}")
        return n
