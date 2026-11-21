"""Pay-change explanations, checked against a deterministic pay-run diff.

MOCK/SAMPLE DATA DEMONSTRATION. The diff is computed first (prior register period vs the
staged current period). An explanation, whether written from a template or by a model, is
then checked sentence by sentence: every sentence must name a component that is in the diff;
every number is bound to the nearest named component and must be that component's prior,
current or change value; direction words (rose/fell/stayed) must match that component's sign;
"from X to Y" must match exactly; components the diff does not contain (tax, bonus, ...) may
not be named; and every component that changed must be mentioned. It is a lexical checker that
binds by proximity, so unusual phrasing can be misbound (see README, limits). The "model" explanations in samples/ are hand-written fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .model import Provenance, fmt, money
from .pipeline import GateResult

ZERO = Decimal("0")

KEYWORDS = {
    "gross": ("gross",),
    "overtime_hours": ("overtime hour", "hours of overtime"),
    "overtime_pay": ("overtime pay",),
    "regular_hours": ("regular hour",),
    "regular_pay": ("regular pay",),
    "rate": ("rate", "raise"),
}
# components this diff never contains; an explanation that names them is making something up
OUT_OF_DIFF = ("tax", "withholding", "bonus", "benefit", "deduction", "reimbursement", "garnish")
MUST_COVER = ("gross", "regular_hours", "overtime_hours", "rate")


@dataclass
class DiffRow:
    component: str
    prior: Decimal
    current: Decimal
    unit: str
    note: str = ""
    sources: list[Provenance] = field(default_factory=list)

    @property
    def delta(self) -> Decimal:
        return self.current - self.prior


def pay_diff(result: GateResult, worker_id: str) -> list[DiffRow]:
    w = result.workers[worker_id]
    prev = result.register_row(worker_id, "previous")
    cur = result.staged[worker_id]
    if prev is None or "gross" not in cur:
        raise ValueError(f"{worker_id}: need a prior-period register row and a staged gross")
    unit = cur["gross"].unit
    pv = {k: v for k, (v, _) in prev.values.items()}
    pp = {k: p for k, (_, p) in prev.values.items()}
    rows = [DiffRow("gross", pv["gross"], cur["gross"].value, unit, sources=[pp["gross"]])]
    if w.pay_type == "hourly":
        p_rate = pv.get("rate", ZERO)
        p_reg_h = pv.get("regular_hours", ZERO)
        p_ot_h = pv.get("overtime_hours", ZERO)
        p_reg_pay = money(p_reg_h * p_rate)
        rates = sorted((x for x in cur["pay.regular"].inputs + cur["pay.overtime"].inputs), key=lambda s: s.field)
        rate_vals = []
        for seg in rates:
            for i in seg.inputs:
                if i.field.startswith("rate@") and i not in rate_vals:
                    rate_vals.append(i)
        rate_vals.sort(key=lambda r: r.field)
        c_rate = rate_vals[-1].value if rate_vals else p_rate
        note = ""
        if len(rate_vals) > 1 or (rate_vals and rate_vals[-1].value != p_rate):
            note = f"effective {rate_vals[-1].field[len('rate@'):]}"
        rows += [
            DiffRow("regular_hours", p_reg_h, cur["hours.regular"].value, "hours", sources=[pp.get("regular_hours")]),
            DiffRow("overtime_hours", p_ot_h, cur["hours.overtime"].value, "hours", sources=[pp.get("overtime_hours")]),
            DiffRow("rate", p_rate, c_rate, f"{unit}/hour", note, sources=[pp.get("rate")]),
            DiffRow("regular_pay", p_reg_pay, cur["pay.regular"].value, unit),
            DiffRow("overtime_pay", pv["gross"] - p_reg_pay, cur["pay.overtime"].value, unit,
                    "prior = register gross - regular hours x rate"),
        ]
    return rows


def _n(x: Decimal) -> str:
    return f"{x.normalize():f}"


def deterministic_explanation(rows: list[DiffRow]) -> str:
    """Template explanation built only from the diff. Faithful by construction."""
    by = {r.component: r for r in rows}
    g = by["gross"]
    if g.delta:
        word = "rose" if g.delta > 0 else "fell"
        out = [f"Your gross pay {word} from {fmt(g.prior)} to {fmt(g.current)} {g.unit}, "
               f"a change of {fmt(abs(g.delta))}."]
    else:
        out = [f"Your gross pay did not change: {fmt(g.current)} {g.unit} in both periods."]
    if "regular_hours" in by and by["regular_hours"].delta:
        r = by["regular_hours"]
        out.append(f"Your regular hours went from {_n(r.prior)} to {_n(r.current)}.")
    if "overtime_hours" in by and by["overtime_hours"].delta:
        r, p = by["overtime_hours"], by["overtime_pay"]
        out.append(f"Your overtime hours went from {_n(r.prior)} to {_n(r.current)}, "
                   f"so overtime pay went from {fmt(p.prior)} to {fmt(p.current)}.")
    if "rate" in by and by["rate"].delta:
        r = by["rate"]
        out.append(f"Your hourly rate went from {fmt(r.prior)} to {fmt(r.current)} {r.note}.".replace(" .", "."))
    return " ".join(out)


@dataclass
class Faithfulness:
    ok: bool
    unsupported_sentences: list[str]
    unsupported_numbers: list[str]  # not a prior/current/change value of the component the number is bound to
    out_of_diff_claims: list[str]
    uncovered_components: list[str]
    wrong_direction: list[str] = field(default_factory=list)  # "rose" for a fall, "stayed" for a change, ...
    wrong_from_to: list[str] = field(default_factory=list)  # "from X to Y" differs from that component's prior/current


_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUM = re.compile(r"(?<![\w.])\d+(?:,\d+)*(?:\.\d+)?")
_FROM_TO = re.compile(r"\bfrom\s+[$₹]?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s+(?:[a-z]{3}\s+)?to\s+[$₹]?\s*(\d+(?:,\d+)*(?:\.\d+)?)")
DIRECTION = {
    "up": ("rose", "rise", "risen", "up", "increase", "increased", "increasing", "higher", "added", "adding", "more",
           "raised", "gained", "grew"),
    "down": ("fell", "fall", "fallen", "down", "drop", "dropped", "decrease", "decreased", "lower", "lowered",
             "lowering", "less", "reduced", "reducing", "cut", "shrank"),
    "same": ("did not change", "didn't change", "unchanged", "stayed", "no change", "the same"),
}


def _dec(m: str) -> Decimal:
    return money(Decimal(m.replace(",", "")))


def _mentions(low: str, present: set[str]) -> list[tuple[int, int, str]]:
    out = []
    for c, kws in KEYWORDS.items():
        if c in present:
            for k in kws:
                out += [(m.start(), m.end(), c) for m in re.finditer(r"\b" + re.escape(k), low)]
    return sorted(out)


def _nearest(span: tuple[int, int], mentions: list[tuple[int, int, str]], preceding: bool = False) -> str | None:
    """Component whose mention is closest to span, by characters between them."""
    best, best_d = None, None
    for ms, me, c in mentions:
        if preceding and ms > span[0]:
            continue
        d = span[0] - me if me <= span[0] else (ms - span[1] if ms >= span[1] else 0)
        if best_d is None or d < best_d:
            best, best_d = c, d
    return best


def check_explanation(text: str, rows: list[DiffRow]) -> Faithfulness:
    """Each number is bound to the component named nearest to it in the same sentence and must be that
    component's prior, current or absolute change. Each direction word is bound the same way and must match the
    sign of that component's change. 'from X to Y' must equal that component's prior and current exactly."""
    by = {r.component: r for r in rows}
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    unsupported, bad_nums, extra, wrong_dir, wrong_ft = [], [], [], [], []
    mentioned: set[str] = set()
    for s in sentences:
        low = _DATE.sub(lambda m: " " * len(m.group()), s.lower())
        ments = _mentions(low, set(by))
        mentioned |= {c for _, _, c in ments}
        for word in OUT_OF_DIFF:
            if re.search(r"\b" + word, low):
                extra.append(f"{word!r} in: {s}")
        if not ments:
            unsupported.append(s)
            continue
        in_pairs: list[tuple[int, int]] = []
        for m in _FROM_TO.finditer(low):
            # "X went from A to B": bound to the component named before it in the sentence
            in_pairs.append(m.span())
            c = _nearest(m.span(), ments, preceding=True) or _nearest(m.span(), ments)
            r = by[c]
            if (_dec(m.group(1)), _dec(m.group(2))) != (money(r.prior), money(r.current)):
                wrong_ft.append(f"{c}: said from {m.group(1)} to {m.group(2)}, diff is {fmt(r.prior)} -> {fmt(r.current)}")
        for m in _NUM.finditer(low):
            if any(a <= m.start() < b for a, b in in_pairs):
                continue  # checked as a pair above
            c = _nearest(m.span(), ments)
            r = by[c]
            if _dec(m.group()) not in {money(r.prior), money(r.current), money(abs(r.delta))}:
                bad_nums.append(f"{m.group()} (bound to {c})")
        for kind, words in DIRECTION.items():
            for w in words:
                for m in re.finditer(r"\b" + re.escape(w) + r"\b", low):
                    c = _nearest(m.span(), ments)
                    d = by[c].delta
                    if (kind == "up" and d <= 0) or (kind == "down" and d >= 0) or (kind == "same" and d != 0):
                        wrong_dir.append(f"{w!r} bound to {c}, but its change is {fmt(d)}")
    uncovered = [r.component for r in rows if r.component in MUST_COVER and r.delta != 0 and r.component not in mentioned]
    ok = not (unsupported or bad_nums or extra or uncovered or wrong_dir or wrong_ft)
    return Faithfulness(ok, unsupported, bad_nums, extra, uncovered, wrong_dir, wrong_ft)
