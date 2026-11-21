"""Labeled eval set: planted defects and clean cases, plus explanation-faithfulness checks.

MOCK/SAMPLE DATA DEMONSTRATION. Each case is a set of patches applied to the clean sample
dataset (samples/acme-switch-clean). Labels were written by hand when the defect was planted.

- false accept: a defect case where the gate did not raise a BLOCK with an expected code
  (the bad value would have reached a pay run). Must be 0 to pass.
- false reject: a clean case the gate blocked. Reported, not hidden.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .dataset import apply_patches, load_dataset
from .explain import check_explanation, deterministic_explanation, pay_diff
from .pipeline import build


@dataclass
class CaseResult:
    id: str
    label: str
    scenario: str
    expected: list[str]
    blocked_codes: list[str]
    outcome: str  # caught | FALSE_ACCEPT | pass | FALSE_REJECT


@dataclass
class ExplainResult:
    id: str
    worker: str
    expect_faithful: bool
    faithful: bool
    detail: str

    @property
    def correct(self) -> bool:
        return self.expect_faithful == self.faithful


def run_evals(evals_dir: str | Path) -> tuple[list[CaseResult], list[ExplainResult]]:
    evals_dir = Path(evals_dir)
    spec = json.loads((evals_dir / "cases.json").read_text(encoding="utf-8"))
    base = load_dataset((evals_dir / spec["base"]).resolve())
    out: list[CaseResult] = []
    for c in spec["cases"]:
        ds = apply_patches(base, c.get("patches", []), c["id"])
        res = build(ds)
        codes = sorted({e.code for e in res.blocks})
        if c["label"] == "defect":
            hit = any(x in codes for x in c["expect_codes"])
            outcome = "caught" if hit else "FALSE_ACCEPT"
        else:
            outcome = "FALSE_REJECT" if codes else "pass"
        out.append(CaseResult(c["id"], c["label"], c.get("scenario", ""), c.get("expect_codes", []), codes, outcome))

    expl: list[ExplainResult] = []
    res = build(base)
    fixtures = json.loads(base.files[base.manifest["extra_files"]["explanations"]])
    for e in spec["explanations"]:
        rows = pay_diff(res, e["worker"])
        text = deterministic_explanation(rows) if e["fixture"] == "template" else fixtures["explanations"][e["fixture"]]["text"]
        f = check_explanation(text, rows)
        detail = "; ".join(x for x in [
            f"unsupported sentences {f.unsupported_sentences}" if f.unsupported_sentences else "",
            f"numbers not in diff {f.unsupported_numbers}" if f.unsupported_numbers else "",
            f"out-of-diff claims {f.out_of_diff_claims}" if f.out_of_diff_claims else "",
            f"changed but not mentioned {f.uncovered_components}" if f.uncovered_components else "",
            f"wrong direction {f.wrong_direction}" if f.wrong_direction else "",
            f"wrong from/to {f.wrong_from_to}" if f.wrong_from_to else "",
        ] if x) or "every sentence and number maps to the diff"
        expl.append(ExplainResult(e["id"], e["worker"], e["expect_faithful"], f.ok, detail))
    return out, expl


def summarize(cases: list[CaseResult], expl: list[ExplainResult]) -> dict:
    defects = [c for c in cases if c.label == "defect"]
    clean = [c for c in cases if c.label == "clean"]
    fa = [c for c in defects if c.outcome == "FALSE_ACCEPT"]
    fr = [c for c in clean if c.outcome == "FALSE_REJECT"]
    return {
        "defect_cases": len(defects),
        "clean_cases": len(clean),
        "false_accepts": len(fa),
        "false_rejects": len(fr),
        "false_accept_rate": len(fa) / len(defects) if defects else 0.0,
        "false_reject_rate": len(fr) / len(clean) if clean else 0.0,
        "explanation_checks": len(expl),
        "explanation_checks_correct": sum(1 for e in expl if e.correct),
        "passed": not fa and all(e.correct for e in expl),
    }


def format_report(cases: list[CaseResult], expl: list[ExplainResult]) -> str:
    s = summarize(cases, expl)
    lines = ["EVAL REPORT (demonstration on MOCK/SAMPLE data written for this repo)", ""]
    lines.append(f"{'case':<38}{'label':<8}{'outcome':<14}expected -> blocked codes")
    for c in cases:
        lines.append(f"{c.id:<38}{c.label:<8}{c.outcome:<14}{','.join(c.expected) or '-'} -> {','.join(c.blocked_codes) or '(none)'}")
    lines += ["", "Pay-change explanation checks (diff-grounded):"]
    for e in expl:
        verdict = "faithful" if e.faithful else "UNFAITHFUL"
        mark = "ok" if e.correct else "WRONG"
        lines.append(f"  {e.id:<34}{e.worker:<6} expected {'faithful' if e.expect_faithful else 'unfaithful':<11}"
                     f"got {verdict:<11}[{mark}] {e.detail}")
    lines += ["",
              f"defect cases: {s['defect_cases']}  false accepts: {s['false_accepts']}  "
              f"false-accept rate: {s['false_accept_rate']:.1%}",
              f"clean cases:  {s['clean_cases']}  false rejects: {s['false_rejects']}  "
              f"false-reject rate: {s['false_reject_rate']:.1%}",
              f"explanation checks correct: {s['explanation_checks_correct']}/{s['explanation_checks']}",
              f"RESULT: {'PASS' if s['passed'] else 'FAIL'} (pass = 0 false accepts and every explanation check correct)"]
    return "\n".join(lines)
