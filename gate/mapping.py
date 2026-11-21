"""Column mapping: deterministic synonyms first, then a pluggable "AI-assisted" proposer.

MOCK/SAMPLE DATA DEMONSTRATION. The only proposer shipped here is RecordedMappingAssistant,
which replays a hand-written JSON fixture shaped like an LLM's structured output. No model
is called and no API key is needed, so tests are deterministic. A live model could implement
the same MappingAssistant protocol; its proposals would pass through the same acceptance rules:

- a deterministic synonym match always wins; an AI proposal that disagrees with it is a conflict;
- an AI proposal is accepted only at or above the confidence threshold and only for a target
  that exists in the schema;
- an AI "ignore" is refused for a column that holds money (money is never dropped silently);
- anything else becomes a MAPPING_UNRESOLVED exception for a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .model import GateException, Provenance
from .parsers import ParseError, parse_amount

SCHEMAS: dict[str, dict[str, set[str]]] = {
    "register": {
        "targets": {"worker_ref", "worker_name", "entity_code", "period_start", "period_end", "pay_date",
                    "regular_hours", "overtime_hours", "rate", "gross", "net", "currency"},
        "required": {"worker_ref", "entity_code", "period_start", "period_end", "gross", "net", "currency"},
    },
    "timeclock": {
        "targets": {"badge", "worker_name", "work_date", "time_in", "time_out", "break_minutes", "hours",
                    "entry_source"},
        "required": {"badge", "work_date"},
    },
    "comp": {
        "targets": {"worker_name", "effective_date", "rate_type", "rate", "currency", "reason"},
        "required": {"worker_name", "effective_date", "rate_type", "rate"},
    },
}

SYNONYMS: dict[str, dict[str, str]] = {
    "register": {
        "emp #": "worker_ref", "emp no": "worker_ref", "employee id": "worker_ref",
        "employee name": "worker_name", "co code": "entity_code", "company code": "entity_code",
        "period beg": "period_start", "period begin": "period_start", "period start": "period_start",
        "period end": "period_end", "chk date": "pay_date", "check date": "pay_date", "pay date": "pay_date",
        "reg hrs": "regular_hours", "regular hours": "regular_hours", "ot hrs": "overtime_hours",
        "overtime hours": "overtime_hours", "rate": "rate", "gross pay": "gross", "gross": "gross",
        "net pay": "net", "net": "net", "curr": "currency", "currency": "currency",
    },
    "timeclock": {
        "badge": "badge", "badge #": "badge", "emp name": "worker_name", "employee name": "worker_name",
        "work date": "work_date", "date": "work_date", "in": "time_in", "clock in": "time_in",
        "out": "time_out", "clock out": "time_out", "hrs": "hours", "hours": "hours",
        "break (min)": "break_minutes",
    },
    "comp": {
        "employee": "worker_name", "employee name": "worker_name", "effective": "effective_date",
        "effective date": "effective_date", "rate type": "rate_type", "curr": "currency",
        "currency": "currency", "reason": "reason",
    },
}

IGNORE = "ignore"


def norm_header(h: str) -> str:
    return " ".join(h.strip().lower().split())


@dataclass(frozen=True)
class MappingProposal:
    source_column: str
    target_field: str
    confidence: float
    rationale: str
    origin: str  # "deterministic" | name of the assistant


class MappingAssistant(Protocol):
    name: str

    def propose(self, kind: str, headers: list[str], sample_rows: list[dict[str, str]]) -> list[MappingProposal]:
        ...


class RecordedMappingAssistant:
    """Replays a hand-written fixture. Stands in for an LLM; no model is called."""

    name = "recorded-fixture"

    def __init__(self, fixture: dict):
        self.fixture = fixture

    def propose(self, kind, headers, sample_rows):
        out = []
        for p in self.fixture.get("proposals", {}).get(kind, []):
            if p["source_column"] in headers:
                out.append(MappingProposal(p["source_column"], p["target_field"], float(p["confidence"]),
                                           p.get("rationale", ""), self.name))
        return out


@dataclass
class ColumnMap:
    target: str
    note: str  # human-readable provenance of the mapping decision


def _is_valid_target(kind: str, target: str, deduction_codes: set[str]) -> bool:
    if target == IGNORE or target in SCHEMAS[kind]["targets"]:
        return True
    return kind == "register" and target.startswith("deduction:") and target[len("deduction:"):] in deduction_codes


def _holds_money(header: str, rows: list[tuple[int, dict[str, str]]]) -> bool:
    for _, r in rows:
        try:
            v, _ = parse_amount(r.get(header, ""))
        except ParseError:
            continue
        if v != 0:
            return True
    return False


def resolve_mapping(kind: str, filename: str, headers: list[str], rows: list[tuple[int, dict[str, str]]],
                    assistant: MappingAssistant | None, deduction_codes: set[str],
                    threshold: float = 0.90) -> tuple[dict[str, ColumnMap], list[GateException]]:
    """Map each source header to a target field. Returns (header -> ColumnMap, exceptions)."""
    exceptions: list[GateException] = []
    sample = [r for _, r in rows[:5]]
    ai: dict[str, MappingProposal] = {}
    if assistant is not None:
        for p in assistant.propose(kind, headers, sample):
            # keep the highest-confidence proposal per column
            if p.source_column not in ai or p.confidence > ai[p.source_column].confidence:
                ai[p.source_column] = p
    result: dict[str, ColumnMap] = {}

    def header_prov(h: str) -> list[Provenance]:
        return [Provenance(filename, 1, h, h, "header")]

    for h in headers:
        det = SYNONYMS[kind].get(norm_header(h))
        prop = ai.get(h)
        if det:
            if prop and prop.target_field != det and prop.confidence >= threshold:
                exceptions.append(GateException(
                    "MAPPING_CONFLICT", None, h,
                    f"{filename}: column {h!r} matches the known synonym for {det!r}, but the {prop.origin} "
                    f"proposes {prop.target_field!r} at confidence {prop.confidence:.2f}. Neither is applied.",
                    header_prov(h), key=f"{kind}:{h}"))
                continue
            result[h] = ColumnMap(det, f"column {h!r} -> {det} (deterministic synonym)")
            continue
        if prop is None:
            exceptions.append(GateException(
                "MAPPING_UNRESOLVED", None, h,
                f"{filename}: column {h!r} has no known synonym and no mapping proposal.",
                header_prov(h), key=f"{kind}:{h}"))
            continue
        if not _is_valid_target(kind, prop.target_field, deduction_codes):
            exceptions.append(GateException(
                "MAPPING_UNRESOLVED", None, h,
                f"{filename}: column {h!r} was proposed as {prop.target_field!r}, which is not a field in the "
                f"{kind} target schema or the entities' deduction codes.",
                header_prov(h), key=f"{kind}:{h}"))
            continue
        if prop.confidence < threshold:
            exceptions.append(GateException(
                "MAPPING_UNRESOLVED", None, h,
                f"{filename}: column {h!r} -> {prop.target_field!r} proposed at confidence "
                f"{prop.confidence:.2f}, below the {threshold:.2f} acceptance threshold. "
                f"Rationale given: {prop.rationale!r}",
                header_prov(h), key=f"{kind}:{h}"))
            continue
        if prop.target_field == IGNORE and _holds_money(h, rows):
            exceptions.append(GateException(
                "MAPPING_UNRESOLVED", None, h,
                f"{filename}: column {h!r} was proposed as 'ignore' but holds non-zero amounts; money columns "
                f"are never dropped without a human decision.",
                header_prov(h), key=f"{kind}:{h}"))
            continue
        result[h] = ColumnMap(prop.target_field,
                              f"column {h!r} -> {prop.target_field} ({prop.origin}, confidence {prop.confidence:.2f})")

    # two columns claiming the same target
    by_target: dict[str, list[str]] = {}
    for h, cm in result.items():
        if cm.target != IGNORE:
            by_target.setdefault(cm.target, []).append(h)
    for target, cols in by_target.items():
        if len(cols) > 1:
            for h in cols:
                exceptions.append(GateException(
                    "MAPPING_CONFLICT", None, h,
                    f"{filename}: columns {cols} all map to {target!r}.", header_prov(h), key=f"{kind}:{h}:dup"))
                result.pop(h, None)

    mapped = {cm.target for cm in result.values()}
    for req in sorted(SCHEMAS[kind]["required"] - mapped):
        exceptions.append(GateException(
            "MAPPING_UNRESOLVED", None, req,
            f"{filename}: required field {req!r} is not mapped from any column.",
            [Provenance(filename, 1, "(none)", "", "header")], key=f"{kind}:missing:{req}"))
    return result, exceptions
