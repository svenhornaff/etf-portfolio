"""KPI reference tests.

docs/dev/portfolio-report-concept.md §6 "Reference tests".
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from etf_portfolio.classify import Event
from etf_portfolio.kpi import daily_value_series, max_drawdown, twr_series, xirr
from etf_portfolio.ledger import build_ledger
from etf_portfolio.load import Row


def make_row(idx: int, booking: date, amount: Decimal, text: str = "") -> Row:
    return Row(idx=idx, booking=booking, value=booking, amount=amount, text=text, iban="")


def test_buy_then_sell_at_same_price_realizes_zero():
    buy_row = make_row(1, date(2025, 1, 1), Decimal("-1000.00"))
    sell_row = make_row(2, date(2025, 1, 10), Decimal("1000.00"))
    events = [
        Event(row=buy_row, kind="BUY", isin="XX", name="Test", qty=Decimal(10)),
        Event(row=sell_row, kind="SELL", isin="XX", name="Test", qty=Decimal(10)),
    ]
    ledger = build_ledger(events)
    assert ledger.realized["XX"] == Decimal(0)
    assert "XX" not in ledger.positions_now


def test_deposit_with_no_price_change_has_zero_twr_and_gain():
    dep_row = make_row(1, date(2025, 1, 1), Decimal("1000.00"))
    events = [Event(row=dep_row, kind="DEPOSIT")]
    ledger = build_ledger(events)
    series = daily_value_series(ledger, {}, date(2025, 1, 1), date(2025, 1, 5))
    idx = twr_series(series)
    assert idx[-1][1] == 1.0  # no gain, no loss
    assert max_drawdown(idx) == 0.0


def test_xirr_known_two_flow_case():
    flows = [(date(2025, 1, 1), Decimal("-1000")), (date(2026, 1, 1), Decimal("1100"))]
    rate = xirr(flows)
    assert rate is not None
    assert abs(rate - 0.10) < 0.005  # ~365 days ~ 1 year -> ~10%


def test_xirr_no_sign_change_is_none():
    flows = [(date(2025, 1, 1), Decimal("1000")), (date(2026, 1, 1), Decimal("1100"))]
    assert xirr(flows) is None


def test_negative_position_flags_data_quality_error():
    sell_row = make_row(1, date(2025, 1, 1), Decimal("100.00"))
    events = [Event(row=sell_row, kind="SELL", isin="XX", name="Test", qty=Decimal(10))]
    ledger = build_ledger(events)
    assert any(d.severity == "error" for d in ledger.dq)


def test_fifo_realized_gain_two_lots():
    b1 = make_row(1, date(2025, 1, 1), Decimal("-100.00"))  # 10 units @ 10
    b2 = make_row(2, date(2025, 1, 5), Decimal("-120.00"))  # 10 units @ 12
    s1 = make_row(3, date(2025, 1, 10), Decimal("150.00"))  # sell 15 units @ 10
    events = [
        Event(row=b1, kind="BUY", isin="XX", name="Test", qty=Decimal(10)),
        Event(row=b2, kind="BUY", isin="XX", name="Test", qty=Decimal(10)),
        Event(row=s1, kind="SELL", isin="XX", name="Test", qty=Decimal(15)),
    ]
    ledger = build_ledger(events)
    # FIFO consumes all 10 from lot1 (cost 100) + 5 from lot2 (cost 60) = 160 cost
    # proceeds 150 -> realized -10
    assert ledger.realized["XX"] == Decimal("-10.00")
    assert ledger.positions_now["XX"].qty == Decimal("5")


def test_vap_checkpoint_matches_reconstructed_qty():
    buy_row = make_row(1, date(2024, 6, 1), Decimal("-100.00"))
    vap_row = make_row(2, date(2025, 1, 15), Decimal("-1.00"), "Vorabpauschale für Fonds: ...")
    events = [
        Event(row=buy_row, kind="BUY", isin="XX", name="Test", qty=Decimal(10)),
        replace(Event(row=vap_row, kind="TAX_VAP", isin="XX", name="Test", qty=Decimal(10))),
    ]
    ledger = build_ledger(events)
    assert len(ledger.vap_checks) == 1
    assert ledger.vap_checks[0]["ok"] is True
