"""Core types: provenance, staged values, gate exceptions.

MOCK/SAMPLE DATA DEMONSTRATION. Money is Decimal throughout and rounded half-up to cents.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .catalog import CODES

CENT = Decimal("0.01")


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(x: Decimal) -> str:
    """Format a Decimal without float noise (2 places for money-sized values)."""
    q = x.quantize(CENT, rounding=ROUND_HALF_UP)
    return f"{q:,.2f}"


@dataclass(frozen=True)
class Provenance:
    """Where a value came from: file, row (header is row 1), column, the original text, and what was done to it."""

    file: str
    row: int
    column: str
    original_text: str
    transform: str = ""

    def ref(self) -> str:
        if self.row == 0:  # refers to the file or config as a whole, not one row
            return f"{self.file}:[{self.column}]"
        return f"{self.file}:row {self.row}:[{self.column}]"


@dataclass
class StagedValue:
    worker_id: str
    field: str  # hours.regular, hours.overtime, pay.regular, pay.overtime, gross, rate@<date>, deduction:<code>, net.prior_register
    value: Decimal
    unit: str  # USD, INR, hours
    derivation: str
    sources: list[Provenance] = dataclasses.field(default_factory=list)
    inputs: list["StagedValue"] = dataclasses.field(default_factory=list)

    def all_sources(self) -> list[Provenance]:
        out = list(self.sources)
        for i in self.inputs:
            for s in i.all_sources():
                if s not in out:
                    out.append(s)
        return out

    def trace_lines(self, indent: int = 0, seen: set[int] | None = None) -> list[str]:
        """Tree of how this value was derived, down to source cells. A node already printed is
        referenced as '(shown above)' instead of repeated."""
        seen = set() if seen is None else seen
        pad = "  " * indent
        v = f"{self.value.normalize():f}" if self.unit == "hours" else fmt(self.value)
        head = f"{pad}{self.worker_id} {self.field} = {v} {self.unit}  <- {self.derivation}"
        if id(self) in seen:
            return [head + "  (shown above)"]
        seen.add(id(self))
        lines = [head]
        for s in self.sources:
            t = f" ({s.transform})" if s.transform else ""
            lines.append(f"{pad}  source {s.ref()} text={s.original_text!r}{t}")
        for i in self.inputs:
            lines.extend(i.trace_lines(indent + 1, seen))
        return lines


@dataclass
class GateException:
    code: str
    worker_id: str | None
    field: str | None
    message: str
    sources: list[Provenance] = dataclasses.field(default_factory=list)
    key: str = ""  # extra discriminator so ids are stable and unique
    resolved_by: str | None = None
    resolution: str | None = None

    @property
    def severity(self) -> str:
        return CODES[self.code].severity

    @property
    def action(self) -> str:
        return CODES[self.code].action

    @property
    def scenario(self) -> str:
        return CODES[self.code].scenario

    @property
    def id(self) -> str:
        raw = "|".join([self.code, self.worker_id or "", self.field or "", self.key])
        return "X" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    @property
    def blocking(self) -> bool:
        return self.severity == "BLOCK" and self.resolved_by is None
