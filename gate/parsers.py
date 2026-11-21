"""Deterministic parsers for the odd formats found in the MOCK/SAMPLE exports.

Each parser either returns a value or raises ParseError. None of them guess: an ambiguous
input (for example 13/11/2026 under month-first order, or a bare "7" as a time) is an error,
and the gate turns that error into a VALUE_UNPARSEABLE exception for a human.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

HOURS_Q = Decimal("0.01")

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

CURRENCY_CODES = ("USD", "INR", "PHP", "EUR", "GBP", "CAD")
CURRENCY_SYMBOLS = (("₹", "INR"), ("Rs.", "INR"), ("Rs", "INR"), ("$", "USD"))


class ParseError(ValueError):
    pass


def read_csv(text: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Return headers and (row_number, row) pairs. Row numbers count the header as row 1."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return [], []
    headers = rows[0]
    out = []
    for n, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        r = r + [""] * (len(headers) - len(r))
        out.append((n, dict(zip(headers, r))))
    return headers, out


def parse_date(text: str, order: str = "MDY") -> date:
    s = text.strip()
    if not s:
        raise ParseError("empty date")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            a, b, y = (int(g) for g in m.groups())
            if order == "MDY":
                mo, d = a, b
            elif order == "DMY":
                d, mo = a, b
            else:
                raise ParseError(f"unknown date order {order!r}")
        else:
            m = re.fullmatch(r"(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{4})", s)
            if not m:
                raise ParseError(f"unrecognized date format {text!r}")
            d, y = int(m.group(1)), int(m.group(3))
            mo = MONTHS.get(m.group(2).lower())
            if mo is None:
                raise ParseError(f"unknown month in {text!r}")
    try:
        return date(y, mo, d)
    except ValueError as e:
        raise ParseError(f"invalid date {text!r} (order {order}): {e}") from None


def parse_time(text: str) -> time:
    s = text.strip().upper().replace(".", "")
    if not s:
        raise ParseError("empty time")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?\s*(AM|PM)?", s)
    if m and (m.group(2) is not None or m.group(4) is not None):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(4)
        if ap:
            if not 1 <= h <= 12:
                raise ParseError(f"bad 12-hour time {text!r}")
            h = h % 12 + (12 if ap == "PM" else 0)
    else:
        m = re.fullmatch(r"(\d{2})(\d{2})", s)
        if not m:
            raise ParseError(f"unrecognized time format {text!r}")
        h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ParseError(f"time out of range {text!r}")
    return time(h, mi)


def _hours(x: Decimal) -> Decimal:
    return x.quantize(HOURS_Q, rounding=ROUND_HALF_UP)


def parse_hours(text: str) -> Decimal:
    s = text.strip().lower()
    if not s:
        raise ParseError("empty hours")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)?", s)
    if m:
        return _hours(Decimal(m.group(1)))
    m = re.fullmatch(r"(\d+):(\d{2})", s)
    if m and int(m.group(2)) < 60:
        return _hours(Decimal(m.group(1)) + Decimal(m.group(2)) / 60)
    m = re.fullmatch(r"(\d+)\s*h\s*(\d{1,2})\s*m?", s)
    if m and int(m.group(2)) < 60:
        return _hours(Decimal(m.group(1)) + Decimal(m.group(2)) / 60)
    m = re.fullmatch(r"(\d+)\s*(?:m|min|mins)", s)
    if m:
        return _hours(Decimal(m.group(1)) / 60)
    raise ParseError(f"unrecognized hours format {text!r}")


def parse_break_minutes(text: str) -> int:
    s = text.strip().lower()
    if not s:
        return 0
    m = re.fullmatch(r"(\d+)\s*(?:m|min|mins)?", s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+):(\d{2})", s)
    if m and int(m.group(2)) < 60:
        return int(m.group(1)) * 60 + int(m.group(2))
    raise ParseError(f"unrecognized break format {text!r}")


def _valid_grouping(intpart: str) -> bool:
    if "," not in intpart:
        return intpart.isdigit()
    g = intpart.split(",")
    if not all(x.isdigit() for x in g) or len(g[-1]) != 3:
        return False
    western = 1 <= len(g[0]) <= 3 and all(len(x) == 3 for x in g[1:-1])
    indian = 1 <= len(g[0]) <= 2 and all(len(x) == 2 for x in g[1:-1])
    return western or indian


def parse_amount(text: str) -> tuple[Decimal, str | None]:
    """Parse '$1,234.50', '₹18,00,000', 'INR 9,60,000', '(12.00)'. Returns (value, currency or None)."""
    s = text.strip()
    if not s:
        raise ParseError("empty amount")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    cur = None
    for code in CURRENCY_CODES:
        if s.upper().startswith(code):
            cur, s = code, s[len(code):].strip()
            break
        if s.upper().endswith(code):
            cur, s = code, s[: -len(code)].strip()
            break
    if s.startswith("-"):
        neg, s = not neg, s[1:].strip()
    for sym, code in CURRENCY_SYMBOLS:
        if s.startswith(sym):
            if cur and cur != code:
                raise ParseError(f"conflicting currency markers in {text!r}")
            cur, s = code, s[len(sym):].strip()
            break
    if s.startswith("-"):
        neg, s = not neg, s[1:].strip()
    intpart, _, frac = s.partition(".")
    if not _valid_grouping(intpart) or (frac and not frac.isdigit()):
        raise ParseError(f"unrecognized amount {text!r}")
    try:
        v = Decimal(s.replace(",", ""))
    except InvalidOperation:
        raise ParseError(f"unrecognized amount {text!r}") from None
    return (-v if neg else v), cur
