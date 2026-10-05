"""Load and validate the finanzen.net ZERO CSV export.

Phase 1 of docs/dev/portfolio-report-concept.md §3.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

EXPECTED_HEADER = ["Datum", "Valuta", "Betrag", "Betrag storniert", "Status", "Verwendungszweck", "IBAN"]

FILENAME_DATE_RE = re.compile(r"ZERO-kontoumsaetze-(\d{2})[._](\d{2})[._](\d{4})\.csv$")


class HeaderMismatchError(RuntimeError):
    """Raised when the CSV header does not match the known format exactly."""


@dataclass(frozen=True)
class Row:
    idx: int  # source row number (1-based, header excluded)
    booking: date  # Datum
    value: date  # Valuta
    amount: Decimal  # Betrag (signed, cash view)
    text: str  # Verwendungszweck (raw)
    iban: str
    status: str = "gebucht"
    storno: str = ""


def de_decimal(s: str) -> Decimal:
    """Parse a German-formatted decimal string, e.g. "-1.983,30" -> Decimal("-1983.30")."""
    s = s.strip()
    if not s:
        raise InvalidOperation(f"empty decimal string: {s!r}")
    return Decimal(s.replace(".", "").replace(",", "."))


def de_date(s: str) -> date:
    """Parse a DD.MM.YYYY date string."""
    return datetime.strptime(s.strip(), "%d.%m.%Y").date()


def find_latest_csv(data_dir: Path) -> Path:
    """Pick the newest CSV in data_dir by the date encoded in its filename.

    Falls back to mtime if the filename date cannot be parsed.
    """
    candidates = sorted(data_dir.glob("ZERO-kontoumsaetze-*.csv"))
    if not candidates:
        raise FileNotFoundError(f"no ZERO-kontoumsaetze-*.csv found in {data_dir}")

    def sort_key(p: Path) -> tuple[date, float]:
        m = FILENAME_DATE_RE.search(p.name)
        if m:
            dd, mm, yyyy = m.groups()
            try:
                return (date(int(yyyy), int(mm), int(dd)), p.stat().st_mtime)
            except ValueError:
                pass
        return (date.min, p.stat().st_mtime)

    return max(candidates, key=sort_key)


def load_rows(path: Path) -> list[Row]:
    """Read and validate the CSV, returning the parsed rows in file order."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        try:
            header = next(reader)
        except StopIteration as e:
            raise HeaderMismatchError(f"{path}: file is empty") from e
        if header != EXPECTED_HEADER:
            raise HeaderMismatchError(
                f"{path}: header mismatch.\n  expected: {EXPECTED_HEADER}\n  got:      {header}"
            )

        rows: list[Row] = []
        for i, fields in enumerate(reader, start=1):
            if not fields or all(f == "" for f in fields):
                continue
            datum, valuta, betrag, storniert, status, text, iban = (fields + [""] * 7)[:7]
            rows.append(
                Row(
                    idx=i,
                    booking=de_date(datum),
                    value=de_date(valuta),
                    amount=de_decimal(betrag),
                    text=text,
                    iban=iban.strip(),
                    status=status.strip(),
                    storno=storniert.strip(),
                )
            )
    return rows


def summarize(rows: list[Row]) -> str:
    if not rows:
        return "0 rows"
    dmin = min(r.booking for r in rows)
    dmax = max(r.booking for r in rows)
    return f"{len(rows)} rows, booking {dmin.isoformat()} .. {dmax.isoformat()}"
