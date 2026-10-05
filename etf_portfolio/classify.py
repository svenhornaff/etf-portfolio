"""Classify raw CSV rows into typed Events.

Phase 1 of docs/dev/portfolio-report-concept.md §4.

Rule: classify by text prefix first, then parse fields. Never infer the
event kind from "STK" or from the amount sign.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from etf_portfolio.load import Row, de_decimal

IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}")


def redact(text: str) -> str:
    """Strip IBAN-like tokens from free text before it is ever rendered."""
    return IBAN_RE.sub("•••", text)


@dataclass(frozen=True)
class Event:
    row: Row
    kind: str  # BUY, SELL, DEPOSIT, WITHDRAWAL, TAX, TAX_VAP, DIVIDEND, FEE_INTEREST, UNKNOWN
    isin: str | None = None
    name: str | None = None
    qty: Decimal | None = None  # positive; sign comes from kind
    price: Decimal | None = None  # only when stated (crypto variant)
    fees: Decimal | None = None  # only when stated
    order_id: str | None = None
    savings_plan: bool = False
    fractional: bool = False


ORDER_RE = re.compile(r"^Order Nr (\S+) ISIN (\S+) - (Kauf|Verkauf)\s*\((?P<body>.*)\)\s*$")
SPARPLAN_RE = re.compile(r"^Sparplan-Order zu ISIN (\S+) - (Kauf|Verkauf)\s*\((?P<body>.*)\)\s*$")
BRUCH_RE = re.compile(r"^Bruchstücke-Order zu ISIN (\S+) - (Kauf|Verkauf)\s*\((?P<body>.*)\)\s*$")

CRYPTO_BODY_RE = re.compile(
    r"^KRY\s+\S+\s+(?P<name>.+?)\s+STK\s+(?P<qty>[\d.,]+)\s+Kurs\s+EUR\s+(?P<price>[\d.,]+)"
    r"\s+Provision\s+DonauCapital\s+EUR\s+(?P<fee1>[\d.,]+)\s+Provision\s+Baader\s+EUR\s+(?P<fee2>[\d.,]+)\s*$"
)
ETF_BODY_RE = re.compile(r"^(?P<name>.+?)\s+ISIN\s+\S+\s+STK\s+(?P<qty>[\d.,]+)\s*-?\s*$")

GUTSCHRIFT_RE = re.compile(r"^Gutschrift:")
LASTSCHRIFT_RE = re.compile(r"^Lastschrift aktiv:")
STEUERAUSGLEICH_RE = re.compile(r"^Steuerausgleich:")
VAP_RE = re.compile(r"^Vorabpauschale für Fonds:\s*(?P<name>.+?)\s+ISIN\s+(?P<isin>\S+)\s+STK\s+(?P<qty>[\d.,]+)")
DIVIDEND_RE = re.compile(r"^Coupons/Dividende:\s*(?P<name>.+?)\s+ISIN\s+(?P<isin>\S+)\s+STK")
KKT_RE = re.compile(r"^KKT-Abschluss")


def _parse_order_body(body: str) -> tuple[str, Decimal, Decimal | None, Decimal | None]:
    """Parse the parenthetical order body -> (name, qty, price, fees)."""
    m = CRYPTO_BODY_RE.match(body)
    if m:
        qty = de_decimal(m["qty"])
        price = de_decimal(m["price"])
        fees = de_decimal(m["fee1"]) + de_decimal(m["fee2"])
        return m["name"].strip(), qty, price, fees
    m = ETF_BODY_RE.match(body)
    if m:
        return m["name"].strip(), de_decimal(m["qty"]), None, None
    raise ValueError(f"unparseable order body: {body!r}")


def classify_row(row: Row) -> Event:
    text = row.text

    m = ORDER_RE.match(text)
    if m:
        order_id, isin, action = m.group(1), m.group(2), m.group(3)
        name, qty, price, fees = _parse_order_body(m["body"])
        kind = "BUY" if action == "Kauf" else "SELL"
        return Event(row=row, kind=kind, isin=isin, name=name, qty=qty, price=price, fees=fees, order_id=order_id)

    m = SPARPLAN_RE.match(text)
    if m:
        isin, action = m.group(1), m.group(2)
        name, qty, price, fees = _parse_order_body(m["body"])
        kind = "BUY" if action == "Kauf" else "SELL"
        return Event(row=row, kind=kind, isin=isin, name=name, qty=qty, price=price, fees=fees, savings_plan=True)

    m = BRUCH_RE.match(text)
    if m:
        isin, action = m.group(1), m.group(2)
        name, qty, price, fees = _parse_order_body(m["body"])
        kind = "BUY" if action == "Kauf" else "SELL"
        return Event(row=row, kind=kind, isin=isin, name=name, qty=qty, price=price, fees=fees, fractional=True)

    m = VAP_RE.match(text)
    if m:
        return Event(row=row, kind="TAX_VAP", isin=m["isin"], name=m["name"].strip(), qty=de_decimal(m["qty"]))

    m = DIVIDEND_RE.match(text)
    if m:
        return Event(row=row, kind="DIVIDEND", isin=m["isin"], name=m["name"].strip())

    if GUTSCHRIFT_RE.match(text):
        return Event(row=row, kind="DEPOSIT")

    if LASTSCHRIFT_RE.match(text):
        return Event(row=row, kind="DEPOSIT" if row.amount > 0 else "UNKNOWN")

    if STEUERAUSGLEICH_RE.match(text):
        return Event(row=row, kind="TAX")

    if KKT_RE.match(text):
        return Event(row=row, kind="FEE_INTEREST")

    return Event(row=row, kind="UNKNOWN")


def classify(rows: list[Row]) -> list[Event]:
    return [classify_row(r) for r in rows]


# --- Overrides (config.yaml -> overrides:) -----------------------------------


def _matches_override(ev: Event, match: dict) -> bool:
    row = ev.row
    if "booking" in match and row.booking.isoformat() != match["booking"]:
        return False
    if "amount" in match and row.amount != de_decimal(str(match["amount"])):
        return False
    if "text_startswith" in match:
        return row.text.startswith(match["text_startswith"])
    return True


def apply_overrides(events: list[Event], overrides: list[dict]) -> list[Event]:
    """Apply manual fixes keyed by source fingerprint, never by row index."""
    if not overrides:
        return events
    out = []
    for ev in events:
        new_ev = ev
        for ov in overrides:
            if _matches_override(ev, ov.get("match", {})):
                new_ev = replace(new_ev, kind=ov["kind"])
        out.append(new_ev)
    return out
