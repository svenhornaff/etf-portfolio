"""Parsing + classification reference tests.

docs/dev/portfolio-report-concept.md §6 "Reference tests".
"""

from decimal import Decimal
from pathlib import Path

import pytest

from etf_portfolio.classify import classify, redact
from etf_portfolio.load import (
    HeaderMismatchError,
    de_decimal,
    find_latest_csv,
    load_rows,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4,539", Decimal("4.539")),
        ("0,02112535", Decimal("0.02112535")),
        ("-1.983,30", Decimal("-1983.30")),
        ("10.000,00", Decimal("10000.00")),
    ],
)
def test_de_decimal(raw, expected):
    assert de_decimal(raw) == expected


def test_redact_strips_iban():
    text = "Gutschrift: Sven Hornaff DE76380601860704960018 GENODED1BRS ETF Saving Plan"
    redacted = redact(text)
    assert "DE76380601860704960018" not in redacted
    assert "•••" in redacted


def _latest_csv() -> Path:
    try:
        return find_latest_csv(DATA_DIR)
    except FileNotFoundError:
        pytest.skip("no sample CSV in data/ (gitignored, expected locally)")


def test_current_file_acceptance_numbers():
    """docs/dev/portfolio-report-concept.md §12 acceptance checks."""
    path = _latest_csv()
    rows = load_rows(path)
    assert len(rows) == 264

    events = classify(rows)
    unknown = [e for e in events if e.kind == "UNKNOWN"]
    assert len(unknown) == 0

    total = sum(r.amount for r in rows)
    assert total == Decimal("0.15")

    buys = sum(1 for e in events if e.kind == "BUY")
    sells = sum(1 for e in events if e.kind == "SELL")
    assert buys == 156
    assert sells == 53


def test_header_mismatch_raises(tmp_path):
    bad = tmp_path / "ZERO-kontoumsaetze-01.01.2026.csv"
    bad.write_text("Wrong;Header;Here\n", encoding="utf-8-sig")
    with pytest.raises(HeaderMismatchError):
        load_rows(bad)


def test_find_latest_csv_picks_newest_by_filename_date(tmp_path):
    header = "Datum;Valuta;Betrag;Betrag storniert;Status;Verwendungszweck;IBAN\n"
    older = tmp_path / "ZERO-kontoumsaetze-01.01.2025.csv"
    newer = tmp_path / "ZERO-kontoumsaetze-05.10.2026.csv"
    older.write_text(header, encoding="utf-8-sig")
    newer.write_text(header, encoding="utf-8-sig")
    assert find_latest_csv(tmp_path) == newer
