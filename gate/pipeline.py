"""Extraction, staging and the validation gate.

MOCK/SAMPLE DATA DEMONSTRATION. Inputs are hand-written sample exports for a fictional
two-entity company (US + India). This module does not compute taxes and makes no statutory
claim; country "rules" in entities.json are sample configuration for the demo.

Flow: map columns -> parse cells (every value keeps its file/row/column/original text) ->
stage hours, rates, gross and carried deductions per worker -> run blocking checks ->
GateResult with an exception queue. Nothing is posted here; see run.py for approval and posting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from .dataset import Dataset
from .mapping import IGNORE, SCHEMAS, ColumnMap, RecordedMappingAssistant, resolve_mapping
from .model import GateException, Provenance, StagedValue, fmt, money
from .parsers import (ParseError, parse_amount, parse_break_minutes, parse_date, parse_hours,
                      parse_time, read_csv)

ZERO = Decimal("0")


# ---------------------------------------------------------------- inputs

@dataclass
class Worker:
    worker_id: str
    name: str
    entity: str
    pay_type: str  # hourly | salaried
    schedule_type: str  # fixed | variable
    badge: str
    prior_emp_no: str
    expected_hours_min: Decimal | None
    status: str


@dataclass
class Rule:
    effective_from: date
    currency: str
    periods_per_year: int
    ot_weekly_threshold_hours: Decimal | None
    ot_multiplier: Decimal
    deduction_codes: list[str]


@dataclass
class Entity:
    id: str
    name: str
    country: str
    aliases: list[str]
    rules: list[Rule]

    def rule_at(self, d: date) -> Rule | None:
        eligible = [r for r in self.rules if r.effective_from <= d]
        return max(eligible, key=lambda r: r.effective_from) if eligible else None


@dataclass
class Shift:
    worker_id: str
    work_date: date
    start: datetime | None
    end: datetime | None
    hours: Decimal
    entry_source: str
    value: StagedValue


@dataclass
class CompRow:
    worker_id: str
    effective: date
    rate: Decimal
    rate_type: str
    currency: str
    value: StagedValue


@dataclass
class RegisterRow:
    row: int
    worker_id: str | None
    entity: str | None
    entity_text: str
    period: tuple[date, date]
    values: dict[str, tuple[Decimal, Provenance]]  # gross, net, rate, regular_hours, overtime_hours
    deductions: dict[str, tuple[Decimal, Provenance]]
    currency: str
    currency_prov: Provenance
    entity_prov: Provenance


@dataclass
class GateResult:
    dataset: str
    workers: dict[str, Worker]
    entities: dict[str, Entity]
    periods: dict[str, dict[str, tuple[date, date]]]
    staged: dict[str, dict[str, StagedValue]]
    exceptions: list[GateException]
    register: list[RegisterRow]
    mappings: dict[str, dict[str, ColumnMap]]
    excluded: set[str] = field(default_factory=set)

    @property
    def blocks(self) -> list[GateException]:
        return [e for e in self.exceptions if e.blocking]

    @property
    def status(self) -> str:
        return "BLOCKED" if self.blocks else "READY_FOR_APPROVAL"

    def register_row(self, worker_id: str, which: str) -> RegisterRow | None:
        w = self.workers[worker_id]
        per = self.periods[w.entity][which]
        for r in self.register:
            if r.worker_id == worker_id and r.period == per:
                return r
        return None


def _norm_name(s: str) -> str:
    s = s.strip()
    if "," in s:
        last, first = s.split(",", 1)
        s = f"{first} {last}"
    return " ".join(s.lower().split())


def _cells(cmap: dict[str, ColumnMap]) -> dict[str, str]:
    return {cm.target: h for h, cm in cmap.items() if cm.target != IGNORE}


def _rows_if_mapped(kind: str, inv: dict[str, str], rows: list) -> list:
    """Skip a file's rows when a required field is unmapped (already raised as MAPPING_UNRESOLVED)."""
    return rows if SCHEMAS[kind]["required"] <= set(inv) else []


class _Builder:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.exc: list[GateException] = []
        self.order = ds.manifest.get("date_order", "MDY")

    def add(self, code, worker, fld, msg, sources, key=""):
        self.exc.append(GateException(code, worker, fld, msg, list(sources), key=key))

    # ---- static config
    def load_roster(self) -> dict[str, Worker]:
        _, rows = read_csv(self.ds.text("roster"))
        out = {}
        for _, r in rows:
            exp = r.get("expected_hours_min", "").strip()
            out[r["worker_id"]] = Worker(r["worker_id"], r["name"], r["entity"], r["pay_type"], r["schedule_type"],
                                         r.get("badge", "").strip(), r.get("prior_emp_no", "").strip(),
                                         Decimal(exp) if exp else None, r.get("status", "active"))
        return out

    def load_entities(self) -> dict[str, Entity]:
        doc = self.ds.json("entities")
        out = {}
        for eid, e in doc["entities"].items():
            rules = [Rule(date.fromisoformat(r["effective_from"]), r["currency"], int(r["periods_per_year"]),
                          Decimal(r["ot_weekly_threshold_hours"]) if r.get("ot_weekly_threshold_hours") else None,
                          Decimal(r.get("ot_multiplier", "1")), list(r.get("deduction_codes", [])))
                     for r in e["rules"]]
            out[eid] = Entity(eid, e["name"], e["country"], [a.upper() for a in e.get("aliases", [])], rules)
        return out

    # ---- generic cell access
    def _get(self, fname, rownum, row, inv, cmap, target):
        h = inv.get(target)
        if h is None:  # column not mapped: no text, but a provenance that says so (never None)
            return None, Provenance(fname, rownum, f"(unmapped {target})", "", "column not mapped")
        text = row.get(h, "")
        return text, Provenance(fname, rownum, h, text, cmap[h].note)

    def _parse(self, fn, text, prov, worker, fld, key):
        try:
            return fn(text)
        except ParseError as e:
            self.add("VALUE_UNPARSEABLE", worker, fld, f"{prov.ref()}: {e}", [prov], key=key)
            return None


def build(ds: Dataset, threshold: float = 0.90) -> GateResult:
    b = _Builder(ds)
    workers = b.load_roster()
    entities = b.load_entities()
    periods = {eid: {k: (date.fromisoformat(v[0]), date.fromisoformat(v[1])) for k, v in p.items()}
               for eid, p in ds.manifest["pay_periods"].items()}
    alias = {a: e.id for e in entities.values() for a in [e.id.upper(), *e.aliases]}
    deduction_codes = {c for e in entities.values() for r in e.rules for c in r.deduction_codes}
    assistant = RecordedMappingAssistant(ds.json("ai_mapping")) if "ai_mapping" in ds.manifest["files"] else None
    by_badge = {w.badge: w for w in workers.values() if w.badge}
    by_emp = {w.prior_emp_no: w for w in workers.values() if w.prior_emp_no}
    by_name = {_norm_name(w.name): w for w in workers.values()}

    mappings: dict[str, dict[str, ColumnMap]] = {}
    parsed: dict[str, tuple[str, list]] = {}
    for kind in ("register", "timeclock", "comp"):
        fname = ds.filename(kind)
        headers, rows = read_csv(ds.text(kind))
        cmap, exc = resolve_mapping(kind, fname, headers, rows, assistant, deduction_codes, threshold)
        b.exc.extend(exc)
        mappings[kind] = cmap
        parsed[kind] = (fname, rows)

    # ---------------------------------------------------- register
    register: list[RegisterRow] = []
    fname, rows = parsed["register"]
    cmap = mappings["register"]
    inv = _cells(cmap)
    for n, r in _rows_if_mapped("register", inv, rows):
        ref_text, ref_p = b._get(fname, n, r, inv, cmap, "worker_ref")
        w = by_emp.get(ref_text.strip())
        if w is None:
            b.add("WORKER_UNMATCHED", None, "worker_ref",
                  f"{ref_p.ref()}: prior-provider employee {ref_text!r} is not in the roster.", [ref_p], key=f"reg:{n}")
        wid = w.worker_id if w else None
        ok = True
        ent_text, ent_p = b._get(fname, n, r, inv, cmap, "entity_code")
        ent = alias.get((ent_text or "").strip().upper())
        if ent is None:
            b.add("ENTITY_MISMATCH", wid, "entity_code",
                  f"{ent_p.ref()}: company code {ent_text!r} matches no configured legal entity.", [ent_p], key=f"reg:{n}")
            ok = False
        ps_t, ps_p = b._get(fname, n, r, inv, cmap, "period_start")
        pe_t, pe_p = b._get(fname, n, r, inv, cmap, "period_end")
        ps = b._parse(lambda t: parse_date(t, b.order), ps_t or "", ps_p, wid, "period_start", f"reg:{n}:ps")
        pe = b._parse(lambda t: parse_date(t, b.order), pe_t or "", pe_p, wid, "period_end", f"reg:{n}:pe")
        cur_t, cur_p = b._get(fname, n, r, inv, cmap, "currency")
        vals: dict[str, tuple[Decimal, Provenance]] = {}
        for tgt, required in (("gross", True), ("net", True), ("rate", False), ("regular_hours", False),
                              ("overtime_hours", False)):
            t, p = b._get(fname, n, r, inv, cmap, tgt)
            if t is None or (not t.strip() and not required):
                continue
            fn = parse_hours if tgt.endswith("hours") else (lambda x: parse_amount(x)[0])
            v = b._parse(fn, t, p, wid, tgt, f"reg:{n}:{tgt}")
            if v is None:
                ok = False
            else:
                if v < 0:
                    b.add("NEGATIVE_VALUE", wid, tgt, f"{p.ref()}: {tgt} is negative ({v}).", [p], key=f"reg:{n}:{tgt}")
                vals[tgt] = (v, p)
        deds: dict[str, tuple[Decimal, Provenance]] = {}
        for h, cm in cmap.items():
            if cm.target.startswith("deduction:") and r.get(h, "").strip():
                p = Provenance(fname, n, h, r[h], cm.note)
                v = b._parse(lambda x: parse_amount(x)[0], r[h], p, wid, cm.target, f"reg:{n}:{h}")
                if v is None:
                    ok = False
                else:
                    if v < 0:
                        b.add("NEGATIVE_VALUE", wid, cm.target, f"{p.ref()}: deduction is negative ({v}).", [p],
                              key=f"reg:{n}:{h}")
                    deds[cm.target[len("deduction:"):]] = (v, p)
        if ps is None or pe is None or not ok:
            continue
        rr = RegisterRow(n, wid, ent, ent_text or "", (ps, pe), vals, deds, (cur_t or "").strip().upper(),
                         cur_p, ent_p)
        register.append(rr)
        if "gross" in vals and "net" in vals:
            total_d = sum((v for v, _ in deds.values()), ZERO)
            if money(vals["gross"][0] - total_d) != money(vals["net"][0]):
                b.add("REGISTER_INTERNAL_MISMATCH", wid, "net",
                      f"{fname} row {n}: gross {fmt(vals['gross'][0])} - deductions {fmt(total_d)} = "
                      f"{fmt(vals['gross'][0] - total_d)}, but the register's net is {fmt(vals['net'][0])}.",
                      [vals["gross"][1], vals["net"][1], *[p for _, p in deds.values()]], key=f"reg:{n}")

    # ---------------------------------------------------- comp sheet
    comps: dict[str, list[CompRow]] = {}
    fname, rows = parsed["comp"]
    cmap = mappings["comp"]
    inv = _cells(cmap)
    for n, r in _rows_if_mapped("comp", inv, rows):
        nm_t, nm_p = b._get(fname, n, r, inv, cmap, "worker_name")
        w = by_name.get(_norm_name(nm_t))
        if w is None:
            b.add("WORKER_UNMATCHED", None, "worker_name",
                  f"{nm_p.ref()}: comp row for {nm_t!r} matches no roster name.", [nm_p], key=f"comp:{n}")
            continue
        eff_t, eff_p = b._get(fname, n, r, inv, cmap, "effective_date")
        eff = b._parse(lambda t: parse_date(t, b.order), eff_t or "", eff_p, w.worker_id, "effective_date",
                       f"comp:{n}:eff")
        rate_t, rate_p = b._get(fname, n, r, inv, cmap, "rate")
        amt = b._parse(parse_amount, rate_t or "", rate_p, w.worker_id, "rate", f"comp:{n}:rate")
        rt_t, rt_p = b._get(fname, n, r, inv, cmap, "rate_type")
        rate_type = (rt_t or "").strip().lower()
        if rate_type not in ("hourly", "annual"):
            b.add("VALUE_UNPARSEABLE", w.worker_id, "rate_type", f"{rt_p.ref()}: rate type {rt_t!r} is not hourly/annual.",
                  [rt_p], key=f"comp:{n}:rt")
            continue
        cur_t, cur_p = b._get(fname, n, r, inv, cmap, "currency")
        if eff is None or amt is None:
            continue
        rate, sym_cur = amt
        if rate <= 0:
            b.add("NEGATIVE_VALUE", w.worker_id, "rate", f"{rate_p.ref()}: rate must be positive ({rate}).", [rate_p],
                  key=f"comp:{n}:rate")
            continue
        col_cur = (cur_t or "").strip().upper() or None
        if col_cur and sym_cur and col_cur != sym_cur:
            b.add("VALUE_UNPARSEABLE", w.worker_id, "currency",
                  f"{rate_p.ref()}: amount says {sym_cur} but the currency column says {col_cur}.",
                  [rate_p, cur_p], key=f"comp:{n}:cur")
            continue
        currency = col_cur or sym_cur
        if currency is None:
            b.add("VALUE_UNPARSEABLE", w.worker_id, "currency", f"{rate_p.ref()}: no currency given.", [rate_p],
                  key=f"comp:{n}:cur")
            continue
        srcs = [rate_p, eff_p, rt_p] + ([cur_p] if cur_p else [])
        sv = StagedValue(w.worker_id, f"rate@{eff.isoformat()}", rate, f"{currency}/{'hour' if rate_type == 'hourly' else 'year'}",
                         f"comp sheet {rate_type} rate effective {eff.isoformat()}", srcs)
        comps.setdefault(w.worker_id, []).append(CompRow(w.worker_id, eff, rate, rate_type, currency, sv))

    conflicted: set[str] = set()
    unusable: dict[str, set[date]] = {}  # effective dates whose rate must not be used (conflict or foreign currency)
    for wid, lst in comps.items():
        ent = entities.get(workers[wid].entity)
        if ent is None:
            continue  # raised as RULE_MISSING below
        seen: dict[date, CompRow] = {}
        for c in sorted(lst, key=lambda c: c.effective):
            prev = seen.get(c.effective)
            if prev and (prev.rate, prev.rate_type, prev.currency) != (c.rate, c.rate_type, c.currency):
                conflicted.add(wid)
                unusable.setdefault(wid, set()).add(c.effective)
                b.add("COMP_EFFECTIVE_CONFLICT", wid, "rate",
                      f"{wid}: two comp rows take effect on {c.effective.isoformat()} with different values "
                      f"({fmt(prev.rate)} {prev.currency} {prev.rate_type} vs {fmt(c.rate)} {c.currency} {c.rate_type}).",
                      prev.value.sources[:1] + c.value.sources[:1], key=c.effective.isoformat())
            seen.setdefault(c.effective, c)
            rule = ent.rule_at(c.effective) or min(ent.rules, key=lambda r: r.effective_from)
            if c.currency != rule.currency:
                b.add("FX_BASIS_MISSING", wid, "rate",
                      f"{wid}: comp row effective {c.effective.isoformat()} is in {c.currency}, but entity "
                      f"{ent.id} pays in {rule.currency}. No conversion rate or date is given, so the gate will "
                      f"not convert it.", c.value.sources[:1] + c.value.sources[3:4], key=c.effective.isoformat())
                conflicted.add(wid)
                unusable.setdefault(wid, set()).add(c.effective)

    def rate_at(wid: str, d: date) -> CompRow | None:
        eligible = [c for c in comps.get(wid, []) if c.effective <= d]
        return max(eligible, key=lambda c: c.effective) if eligible else None

    def rate_blocked(wid: str, c: CompRow | None) -> bool:
        """True when the effective comp row is already blocked (conflict / FX); no second exception is raised."""
        return c is not None and c.effective in unusable.get(wid, set())

    # ---------------------------------------------------- time clock
    shifts: dict[str, list[Shift]] = {}
    fname, rows = parsed["timeclock"]
    cmap = mappings["timeclock"]
    inv = _cells(cmap)
    for n, r in _rows_if_mapped("timeclock", inv, rows):
        bd_t, bd_p = b._get(fname, n, r, inv, cmap, "badge")
        w = by_badge.get(bd_t.strip())
        if w is None:
            b.add("WORKER_UNMATCHED", None, "badge",
                  f"{bd_p.ref()}: badge {bd_t!r} matches no roster worker; its hours cannot be attributed.",
                  [bd_p], key=f"tc:{n}")
            continue
        wid = w.worker_id
        d_t, d_p = b._get(fname, n, r, inv, cmap, "work_date")
        wd = b._parse(lambda t: parse_date(t, b.order), d_t, d_p, wid, "work_date", f"tc:{n}:date")
        in_t, in_p = b._get(fname, n, r, inv, cmap, "time_in")
        out_t, out_p = b._get(fname, n, r, inv, cmap, "time_out")
        br_t, br_p = b._get(fname, n, r, inv, cmap, "break_minutes")
        hr_t, hr_p = b._get(fname, n, r, inv, cmap, "hours")
        src_t, _ = b._get(fname, n, r, inv, cmap, "entry_source")
        if wd is None:
            continue
        start = end = None
        computed = None
        srcs = [d_p]
        if (in_t or "").strip() or (out_t or "").strip():
            t_in = b._parse(parse_time, in_t or "", in_p, wid, "time_in", f"tc:{n}:in")
            t_out = b._parse(parse_time, out_t or "", out_p, wid, "time_out", f"tc:{n}:out")
            brk = b._parse(parse_break_minutes, br_t or "", br_p, wid, "break_minutes", f"tc:{n}:brk") if br_p else 0
            if t_in is None or t_out is None or brk is None:
                continue
            start = datetime.combine(wd, t_in)
            end = datetime.combine(wd, t_out)
            if end <= start:
                end += timedelta(days=1)  # overnight shift
            computed = (Decimal((end - start).seconds) / 3600 - Decimal(brk) / 60).quantize(Decimal("0.01"))
            srcs += [in_p, out_p] + ([br_p] if br_p and (br_t or "").strip() else [])
        stated = None
        if (hr_t or "").strip():
            stated = b._parse(parse_hours, hr_t, hr_p, wid, "hours", f"tc:{n}:hrs")
            if stated is None:
                continue
            srcs.append(hr_p)
        if computed is None and stated is None:
            b.add("VALUE_UNPARSEABLE", wid, "hours", f"{fname} row {n}: no in/out times and no hours.", [d_p],
                  key=f"tc:{n}:none")
            continue
        if computed is not None and stated is not None and abs(computed - stated) > Decimal("0.01"):
            b.add("TIMECLOCK_HOURS_INCONSISTENT", wid, "hours",
                  f"{fname} row {n}: in/out/break give {computed} h but the hours column says {stated} h.",
                  srcs, key=f"tc:{n}")
            continue
        hours = computed if computed is not None else stated
        if hours < 0:
            b.add("NEGATIVE_VALUE", wid, "hours", f"{fname} row {n}: hours come out negative ({hours}); "
                  f"the break is longer than the shift.", srcs, key=f"tc:{n}")
            continue
        deriv = "out - in - break" if computed is not None else "hours column"
        sv = StagedValue(wid, f"shift@{wd.isoformat()}", hours, "hours", deriv, srcs)
        shifts.setdefault(wid, []).append(Shift(wid, wd, start, end, hours, (src_t or "").strip().lower(), sv))

    # ---------------------------------------------------- rules changing inside a period
    for eid, ent in entities.items():
        if eid not in periods:
            continue
        s, e = periods[eid]["current"]
        for rule in ent.rules:
            if s < rule.effective_from <= e:
                b.add("RULE_CHANGE_IN_PERIOD", None, eid,
                      f"{eid}: a rule version takes effect {rule.effective_from.isoformat()}, inside the pay period "
                      f"{s.isoformat()}..{e.isoformat()}.",
                      [Provenance(ds.filename("entities"), 0, f"entities.{eid}.rules", rule.effective_from.isoformat(),
                                  "rule effective_from")], key=rule.effective_from.isoformat())

    # ---------------------------------------------------- every entity in use needs a period and a rule at its start
    unconfigured: set[str] = set()
    for eid in sorted({w.entity for w in workers.values() if w.status == "active"}):
        ent = entities.get(eid)
        cfg = Provenance(ds.filename("entities"), 0, f"entities.{eid}", eid, "entity configuration")
        if ent is None or eid not in periods:
            b.add("RULE_MISSING", None, eid, f"{eid}: the roster uses this entity, but it has no entity configuration "
                  f"or no pay period in the manifest.", [cfg], key="config")
            unconfigured.add(eid)
            continue
        s0 = periods[eid]["current"][0]
        if ent.rule_at(s0) is None:
            b.add("RULE_MISSING", None, eid, f"{eid}: no rule version is effective on the period start {s0.isoformat()} "
                  f"(earliest is {min(r.effective_from for r in ent.rules).isoformat()}).", [cfg], key=s0.isoformat())
            unconfigured.add(eid)

    # ---------------------------------------------------- stage per worker
    staged: dict[str, dict[str, StagedValue]] = {}
    for wid, w in workers.items():
        if w.status != "active" or w.entity in unconfigured:
            continue
        ent = entities[w.entity]
        start, end = periods[w.entity]["current"]
        rule = ent.rule_at(start)
        unit = rule.currency
        sv: dict[str, StagedValue] = {}
        staged[wid] = sv
        gross_ok = wid not in conflicted
        if w.pay_type == "hourly":
            mine = sorted(shifts.get(wid, []), key=lambda s: (s.work_date, s.start or datetime.min))
            in_period = []
            for s in mine:
                if start <= s.work_date <= end:
                    in_period.append(s)
                else:
                    b.add("SHIFT_OUTSIDE_PERIOD", wid, s.value.field,
                          f"{wid}: shift on {s.work_date.isoformat()} is outside {start.isoformat()}..{end.isoformat()}.",
                          s.value.sources[:1], key=s.work_date.isoformat())
            for a, c in zip(in_period, in_period[1:]):
                if a.start and c.start and c.start < a.end:
                    b.add("DUPLICATE_SHIFT", wid, "hours",
                          f"{wid}: shift starting {c.start:%Y-%m-%d %H:%M} overlaps the shift "
                          f"{a.start:%H:%M}-{a.end:%H:%M} on {a.work_date.isoformat()}.",
                          a.value.sources[:1] + c.value.sources[:1], key=c.start.isoformat())
                    gross_ok = False
                elif a.work_date == c.work_date and (a.start is None or c.start is None):
                    b.add("DUPLICATE_SHIFT", wid, "hours",
                          f"{wid}: two entries on {a.work_date.isoformat()} and at least one has hours only, so overlap "
                          f"cannot be checked. Confirm both are real shifts.",
                          a.value.sources[:1] + c.value.sources[:1], key=f"{a.work_date.isoformat()}:hours-only")
                    gross_ok = False
            for s in in_period:
                if s.hours > 14:
                    b.add("SHIFT_LONG", wid, s.value.field, f"{wid}: {s.hours} h shift on {s.work_date.isoformat()}.",
                          s.value.sources, key=s.work_date.isoformat())
            total = sum((s.hours for s in in_period), ZERO)
            if not in_period:
                expect = (f"roster expects at least {w.expected_hours_min} h" if w.expected_hours_min
                          else "roster gives no expected minimum")
                b.add("HOURS_MISSING", wid, "hours",
                      f"{wid} ({w.name}) is an active hourly worker with no time entries in "
                      f"{start.isoformat()}..{end.isoformat()} ({expect}). "
                      f"Paying zero would leave them unpaid.",
                      [Provenance(ds.filename("timeclock"), 0, "Badge", w.badge, "no rows for this badge in period")],
                      key=start.isoformat())
                gross_ok = False
            elif w.expected_hours_min and ZERO < total < w.expected_hours_min:
                b.add("HOURS_LOW", wid, "hours", f"{wid}: {total} h is below the usual minimum {w.expected_hours_min} h.",
                      [], key=start.isoformat())
            if w.schedule_type == "variable" and in_period:
                flagged = [s for s in in_period if s.entry_source in ("schedule", "pattern", "default", "template")]
                exact = [s for s in in_period if s.start and s.start.time().strftime("%H:%M") == "09:00"
                         and s.end and s.end.strftime("%H:%M") == "17:00"]
                if flagged or (len(in_period) >= 4 and len(exact) == len(in_period)):
                    why = (f"{len(flagged)} entries are marked as coming from a schedule/pattern" if flagged else
                           f"all {len(in_period)} entries are exactly 09:00-17:00")
                    b.add("DEFAULT_SCHEDULE_SUSPECTED", wid, "hours",
                          f"{wid} ({w.name}) has a variable schedule, but {why}. These look like template hours, "
                          f"not worked hours.",
                          [x for s in (flagged or in_period)[:3] for x in s.value.sources[:3]], key=start.isoformat())
                    gross_ok = False
            # regular / overtime split per ISO week, chronological; OT rate uses the rate effective on that shift's date
            reg_inputs, ot_inputs = [], []
            reg_h = ot_h = ZERO
            segs: dict[tuple[str, str], list] = {}
            week_hours: dict[tuple[int, int], Decimal] = {}
            threshold = rule.ot_weekly_threshold_hours
            rate_missing = False
            for s in in_period:
                c = rate_at(wid, s.work_date)
                if rate_blocked(wid, c):
                    rate_missing = True  # already raised as COMP_EFFECTIVE_CONFLICT / FX_BASIS_MISSING
                    continue
                if c is None or c.rate_type != "hourly":
                    if not rate_missing:
                        b.add("RATE_MISSING", wid, "rate",
                              f"{wid}: no hourly rate is effective on {s.work_date.isoformat()}.",
                              s.value.sources[:1], key=s.work_date.isoformat())
                    rate_missing = True
                    continue
                wk = s.work_date.isocalendar()[:2]
                before = week_hours.get(wk, ZERO)
                after = before + s.hours
                week_hours[wk] = after
                if threshold is None or after <= threshold:
                    r_part, o_part = s.hours, ZERO
                else:
                    o_part = min(s.hours, after - threshold)
                    r_part = s.hours - o_part
                reg_h += r_part
                ot_h += o_part
                if r_part:
                    segs.setdefault(("regular", c.effective.isoformat()), [c, ZERO, []])
                    segs[("regular", c.effective.isoformat())][1] += r_part
                    segs[("regular", c.effective.isoformat())][2].append(s.value)
                if o_part:
                    segs.setdefault(("overtime", c.effective.isoformat()), [c, ZERO, []])
                    segs[("overtime", c.effective.isoformat())][1] += o_part
                    segs[("overtime", c.effective.isoformat())][2].append(s.value)
            shift_vals = [s.value for s in in_period]
            none_src = [] if shift_vals else [Provenance(ds.filename("timeclock"), 0, "Badge", w.badge,
                                                         "no rows for this badge in the period")]
            sv["hours.regular"] = StagedValue(wid, "hours.regular", reg_h, "hours",
                                              f"sum of shift hours up to {threshold} h per ISO week", none_src, shift_vals)
            sv["hours.overtime"] = StagedValue(wid, "hours.overtime", ot_h, "hours",
                                               f"shift hours beyond {threshold} h per ISO week", none_src, shift_vals)
            for (kind, _), (c, h, vals) in sorted(segs.items()):
                mult = rule.ot_multiplier if kind == "overtime" else Decimal("1")
                amt = money(h * c.rate * mult)
                seg = StagedValue(wid, f"pay.{kind}@{fmt(c.rate)}", amt, unit,
                                  f"{h} h x {fmt(c.rate)}" + (f" x {mult}" if kind == "overtime" else ""),
                                  [], [c.value, *vals])
                (reg_inputs if kind == "regular" else ot_inputs).append(seg)
            pay_r = sum((x.value for x in reg_inputs), ZERO)
            pay_o = sum((x.value for x in ot_inputs), ZERO)
            sv["pay.regular"] = StagedValue(wid, "pay.regular", pay_r, unit, "sum of regular segments", [],
                                            reg_inputs or [sv["hours.regular"]])
            sv["pay.overtime"] = StagedValue(wid, "pay.overtime", pay_o, unit,
                                             "sum of overtime segments" if ot_inputs else "no overtime hours", [],
                                             ot_inputs or [sv["hours.overtime"]])
            if gross_ok and not rate_missing:
                sv["gross"] = StagedValue(wid, "gross", pay_r + pay_o, unit, "pay.regular + pay.overtime", [],
                                          [sv["hours.regular"], sv["hours.overtime"], sv["pay.regular"], sv["pay.overtime"]])
        else:  # salaried: annual / periods_per_year, split by calendar days if comp changes inside the period
            days = (end - start).days + 1
            points = sorted({start, *[c.effective for c in comps.get(wid, []) if start < c.effective <= end]})
            parts = []
            missing = False
            for i, p in enumerate(points):
                seg_end = points[i + 1] - timedelta(days=1) if i + 1 < len(points) else end
                c = rate_at(wid, p)
                if rate_blocked(wid, c):
                    missing = True  # already raised as COMP_EFFECTIVE_CONFLICT / FX_BASIS_MISSING
                    break
                if c is None or c.rate_type != "annual":
                    b.add("RATE_MISSING", wid, "rate", f"{wid}: no annual salary is effective on {p.isoformat()}.",
                          [], key=p.isoformat())
                    missing = True
                    break
                seg_days = (seg_end - p).days + 1
                amt = money(c.rate / rule.periods_per_year * seg_days / days)
                deriv = f"{fmt(c.rate)} / {rule.periods_per_year}" + ("" if seg_days == days else f" x {seg_days}/{days} days")
                parts.append(StagedValue(wid, f"pay.salary@{p.isoformat()}", amt, unit, deriv, [], [c.value]))
            if not missing:
                sv["pay.regular"] = StagedValue(wid, "pay.regular", sum((x.value for x in parts), ZERO), unit,
                                                "sum of salary segments", [], parts)
                if gross_ok:
                    sv["gross"] = StagedValue(wid, "gross", sv["pay.regular"].value, unit, "pay.regular", [], [sv["pay.regular"]])

    # ---------------------------------------------------- carry deductions and reconcile to the prior register
    result = GateResult(ds.name, workers, entities, periods, staged, b.exc, register, mappings)
    for wid, sv in staged.items():
        w = workers[wid]
        ent = entities[w.entity]
        rule = ent.rule_at(periods[w.entity]["current"][0])
        row = result.register_row(wid, "current")
        if row is None:
            b.add("REGISTER_ROW_MISSING", wid, "gross",
                  f"{wid}: the prior register has no row for the parallel-run period "
                  f"{periods[w.entity]['current'][0].isoformat()}..{periods[w.entity]['current'][1].isoformat()}.",
                  [], key="current")
            continue
        if row.entity != w.entity:
            b.add("ENTITY_MISMATCH", wid, "entity_code",
                  f"{wid}: the roster puts this worker in {w.entity} ({ent.country}), but the prior register books "
                  f"them to {row.entity_text!r} ({row.entity}).", [row.entity_prov], key=f"reg:{row.row}")
        if row.currency != rule.currency:
            b.add("CURRENCY_MISMATCH", wid, "currency",
                  f"{wid}: entity {w.entity} pays in {rule.currency}, but the prior register row is in {row.currency!r}.",
                  [row.currency_prov], key=f"reg:{row.row}")
        for code, (v, p) in row.deductions.items():
            sv[f"deduction:{code}"] = StagedValue(wid, f"deduction:{code}", v, unit,
                                                  "carried from prior register (parallel run)", [p])
        if "net" in row.values:
            v, p = row.values["net"]
            sv["net.prior_register"] = StagedValue(wid, "net.prior_register", v, unit, "prior register net", [p])
        if "gross" in sv and "gross" in row.values:
            rg, rp = row.values["gross"]
            diff = sv["gross"].value - rg
            if abs(diff) > Decimal(ds.manifest.get("reconcile_tolerance", "0.00")):
                parts = [f"staged {fmt(sv['gross'].value)} vs prior register {fmt(rg)} {unit} (difference {fmt(diff)})"]
                if w.pay_type == "hourly":
                    rh = row.values.get("regular_hours", (None, None))[0]
                    oh = row.values.get("overtime_hours", (None, None))[0]
                    parts.append(f"hours: staged {sv['hours.regular'].value} regular + {sv['hours.overtime'].value} OT, "
                                 f"register {rh} regular + {oh} OT")
                b.add("REGISTER_RECONCILE_MISMATCH", wid, "gross", f"{wid} ({w.name}): " + "; ".join(parts) + ".",
                      [rp, *sv["gross"].all_sources()[:4]], key=f"reg:{row.row}")
    return result


def apply_resolutions(result: GateResult, resolutions: list[dict]) -> list[str]:
    """Record human decisions. 'accept' clears one exception; 'exclude_worker' drops the worker from this run
    and clears that worker's exceptions. Returns ids that matched nothing."""
    by_id = {e.id: e for e in result.exceptions}
    unknown = []
    for r in resolutions:
        e = by_id.get(r["exception_id"])
        reviewer = (r.get("reviewer") or "").strip()
        if e is None or not reviewer:
            unknown.append(r["exception_id"])
            continue
        if r["decision"] == "accept":
            e.resolved_by, e.resolution = reviewer, f"accepted: {r.get('note', '')}"
        elif r["decision"] == "exclude_worker" and e.worker_id:
            result.excluded.add(e.worker_id)
            for x in result.exceptions:
                if x.worker_id == e.worker_id and x.resolved_by is None:
                    x.resolved_by, x.resolution = reviewer, f"worker excluded from run: {r.get('note', '')}"
        else:
            unknown.append(r["exception_id"])
    return unknown
