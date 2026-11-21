"""CLI. MOCK/SAMPLE DATA DEMONSTRATION; nothing here talks to any network or real system.

  python -m gate run samples/acme-switch [--trace W002:gross] [--resolutions file.json]   (exit 3 if blocked)
  python -m gate post samples/acme-switch-clean --approver "Name" [--db out.sqlite]
  python -m gate explain samples/acme-switch-clean W002
  python -m gate eval samples/evals
"""

from __future__ import annotations

import argparse
import json
import sys

from . import catalog
from .dataset import load_dataset
from .evals import format_report, run_evals, summarize
from .explain import check_explanation, deterministic_explanation, pay_diff
from .model import fmt
from .pipeline import apply_resolutions, build
from .run import GateBlocked, NotApproved, PayRun, Store

BANNER = "payroll-import-gate | DEMONSTRATION ON MOCK/SAMPLE DATA (fictional company, hand-written files)"


def _queue(result) -> str:
    lines = [f"Status: {result.status}   blocks: {len(result.blocks)}   "
             f"warnings: {sum(1 for e in result.exceptions if e.severity == 'WARN')}", "", "EXCEPTION QUEUE"]
    order = {"BLOCK": 0, "WARN": 1}
    for e in sorted(result.exceptions, key=lambda e: (e.resolved_by is not None, order[e.severity], e.code, e.worker_id or "")):
        state = f"resolved by {e.resolved_by}" if e.resolved_by else e.severity
        sc = catalog.SCENARIOS[e.scenario]
        lines.append(f"[{state}] {e.id} {e.code}  worker={e.worker_id or '-'}  field={e.field or '-'}")
        lines.append(f"    what: {e.message}")
        lines.append(f"    do:   {e.action}")
        for s in e.sources[:4]:
            lines.append(f"    src:  {s.ref()} text={s.original_text!r}")
        lines.append(f"    modeled on ({sc.label}, {sc.date}): {sc.url}")
    if not result.exceptions:
        lines.append("(empty)")
    return "\n".join(lines)


def _trace(result, spec: str) -> str:
    wid, _, fld = spec.partition(":")
    sv = result.staged.get(wid, {}).get(fld or "gross")
    if sv is None:
        avail = ", ".join(sorted(result.staged.get(wid, {}))) or "(none: worker unknown or not staged)"
        return f"TRACE {spec}: not staged. Staged fields for {wid}: {avail}"
    return "\n".join([f"TRACE {spec}", *sv.trace_lines()])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gate", description=BANNER)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="stage a dataset and print the exception queue")
    r.add_argument("dataset")
    r.add_argument("--trace", action="append", default=[], help="WORKER:FIELD, e.g. W002:gross")
    r.add_argument("--resolutions", help="JSON file of human decisions")
    p = sub.add_parser("post", help="approve and post to a local SQLite table (refused while blocked)")
    p.add_argument("dataset")
    p.add_argument("--approver", required=True)
    p.add_argument("--resolutions")
    p.add_argument("--db", default=":memory:")
    x = sub.add_parser("explain", help="pay-run diff and a checked explanation for one worker")
    x.add_argument("dataset")
    x.add_argument("worker")
    e = sub.add_parser("eval", help="run the labeled eval set")
    e.add_argument("evals_dir")
    a = ap.parse_args(argv)

    print(BANNER)
    if a.cmd == "eval":
        cases, expl = run_evals(a.evals_dir)
        print(format_report(cases, expl))
        return 0 if summarize(cases, expl)["passed"] else 1

    ds = load_dataset(a.dataset)
    result = build(ds)
    if getattr(a, "resolutions", None):
        with open(a.resolutions, encoding="utf-8") as f:
            unknown = apply_resolutions(result, json.load(f)["resolutions"])
        if unknown:
            print(f"warning: resolutions that matched nothing or lacked a reviewer: {unknown}")
    if a.cmd == "run":
        print(f"dataset: {a.dataset}")
        print(_queue(result))
        for t in a.trace:
            print()
            print(_trace(result, t))
        return 3 if result.blocks else 0
    if a.cmd == "post":
        store = Store(a.db)
        try:
            run = PayRun(ds.name, result, store)
            try:
                run.approve(a.approver)
                n = run.post()
            except (GateBlocked, NotApproved) as err:
                print(f"REFUSED: {err}")
                print(_queue(result))
                return 2
            print(f"POSTED {n} lines to local SQLite ({a.db}); approved by {run.approved_by}")
            if run.skipped:
                print(f"not posted (no gross staged): {', '.join(run.skipped)}")
            return 0
        finally:
            store.close()
    if a.cmd == "explain":
        rows = pay_diff(result, a.worker)
        print(f"PAY-RUN DIFF {a.worker}: prior register period vs staged current period")
        for d in rows:
            print(f"  {d.component:<15}{fmt(d.prior):>14} -> {fmt(d.current):>14}  change {fmt(d.delta):>12} {d.unit} {d.note}")
        text = deterministic_explanation(rows)
        f = check_explanation(text, rows)
        print(f"\nEXPLANATION (template from the diff): {text}")
        print(f"faithfulness check: {'PASS' if f.ok else 'FAIL'}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
