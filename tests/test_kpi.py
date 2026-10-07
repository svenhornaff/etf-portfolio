"""KPI reference tests.

docs/dev/portfolio-report-concept.md §6 "Reference tests".
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from etf_portfolio.classify import Event
from etf_portfolio.kpi import (
    beta_and_correlation,
    best_worst_month,
    daily_value_series,
    downside_deviation_annualized,
    drawdown_durations,
    max_drawdown,
    monthly_returns,
    portfolio_value,
    sharpe_ratio,
    sortino_ratio,
    twr_series,
    unusual_jumps,
    xirr,
)
from etf_portfolio.ledger import build_ledger
from etf_portfolio.load import Row
from etf_portfolio.prices import PriceSeries


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


def test_valuation_is_none_when_a_held_isin_is_unpriced():
    """docs/dev/refactor-concept.md §3: a partial sum must never masquerade as Gesamt."""
    positions = {"AA": Decimal("10"), "BB": Decimal("5")}
    priced_only = {"AA": PriceSeries(closes={date(2025, 1, 1): Decimal("100")}, source={date(2025, 1, 1): "market"})}
    v = portfolio_value(positions, Decimal("0"), priced_only, date(2025, 1, 1))
    assert v.value is None
    assert v.missing == ["BB"]
    assert v.reason is not None and "BB" in v.reason


def test_valuation_flags_implied_price_as_estimated():
    positions = {"AA": Decimal("10")}
    series = {"AA": PriceSeries(closes={date(2025, 1, 1): Decimal("12")}, source={date(2025, 1, 1): "implied"})}
    v = portfolio_value(positions, Decimal("0"), series, date(2025, 1, 1))
    assert v.value == Decimal("120")
    assert v.estimated == ["AA"]


def test_monthly_returns_twelve_percent_months_from_compounding_index():
    # a daily index that compounds +1%/month for 3 months, sampled at month ends
    idx = [
        (date(2025, 1, 31), 1.00),
        (date(2025, 2, 28), 1.01),
        (date(2025, 3, 31), 1.0201),
    ]
    months = monthly_returns(idx)
    assert len(months) == 3
    assert abs(months[1]["ret"] - 0.01) < 1e-9
    assert abs(months[2]["ret"] - 0.01) < 1e-9


def test_best_worst_month_prefers_complete_months():
    months = [
        {"month": "2025-01", "ret": 0.5, "partial": True},  # would be "best" but partial
        {"month": "2025-02", "ret": 0.02, "partial": False},
        {"month": "2025-03", "ret": -0.03, "partial": False},
    ]
    best, worst = best_worst_month(months)
    assert best is not None and best["month"] == "2025-02"
    assert worst is not None and worst["month"] == "2025-03"


def test_fifo_partial_sell_cost_has_no_decimal_dust():
    """docs/dev/refactor-concept.md §7: quantize the realized cost to the cent."""
    b1 = make_row(1, date(2025, 1, 1), Decimal("-100.00"))  # 3 units
    s1 = make_row(2, date(2025, 1, 10), Decimal("40.00"))  # sell 1 of 3 units
    events = [
        Event(row=b1, kind="BUY", isin="XX", name="Test", qty=Decimal(3)),
        Event(row=s1, kind="SELL", isin="XX", name="Test", qty=Decimal(1)),
    ]
    ledger = build_ledger(events)
    assert ledger.sells[0].cost == ledger.sells[0].cost.quantize(Decimal("0.01"))


def test_drawdown_durations_tracks_peak_to_now():
    idx = [
        (date(2025, 1, 1), 1.0),
        (date(2025, 1, 2), 1.2),  # new peak
        (date(2025, 1, 3), 1.1),  # -1 day underwater
        (date(2025, 1, 4), 1.0),  # -2 days underwater
        (date(2025, 1, 5), 1.3),  # new peak, back to 0
    ]
    max_dur, current_dur = drawdown_durations(idx)
    assert max_dur == 2
    assert current_dur == 0


def test_sharpe_and_sortino_none_without_inputs():
    assert sharpe_ratio(None, 0.1, 0.02) is None
    assert sharpe_ratio(0.1, None, 0.02) is None
    assert sortino_ratio(0.1, 0.0, 0.02) is None  # zero downside deviation -> undefined, not infinite
    assert sharpe_ratio(0.12, 0.10, 0.02) == pytest.approx(1.0)


def test_downside_deviation_ignores_up_days():
    # 150 flat-ish days with one -2% day should give a small, non-None downside dev
    idx = [(date(2025, 1, 1) + timedelta(days=i), 1.0 + i * 0.0001) for i in range(150)]
    idx[100] = (idx[100][0], idx[99][1] * 0.98)
    dd = downside_deviation_annualized(idx)
    assert dd is not None and dd > 0


def test_beta_and_correlation_perfect_tracker_is_beta_one():
    base = date(2025, 1, 1)
    idx = [(base + timedelta(days=i), 1.0 + i * 0.001) for i in range(80)]
    bench = {base + timedelta(days=i): Decimal(str(100 + i * 0.1)) for i in range(80)}
    beta, corr = beta_and_correlation(idx, bench)
    assert beta is not None and beta == pytest.approx(1.0, abs=0.05)
    assert corr is not None and corr > 0.9


def test_beta_and_correlation_none_below_min_obs():
    base = date(2025, 1, 1)
    idx = [(base + timedelta(days=i), 1.0 + i * 0.001) for i in range(10)]
    bench = {base + timedelta(days=i): Decimal("100") for i in range(10)}
    beta, corr = beta_and_correlation(idx, bench, min_obs=60)
    assert beta is None and corr is None


def test_unusual_jumps_flags_portfolio_only_move():
    idx = [(date(2025, 1, 1), 1.0), (date(2025, 1, 2), 1.10)]  # +10%, no benchmark data
    jumps = unusual_jumps(idx, None, threshold=0.05)
    assert len(jumps) == 1
    assert jumps[0]["date"] == date(2025, 1, 2)
    assert jumps[0]["benchmark_ret"] is None


def test_unusual_jumps_not_flagged_when_benchmark_shares_the_move():
    idx = [(date(2025, 1, 1), 1.0), (date(2025, 1, 2), 1.10)]
    bench = {date(2025, 1, 1): Decimal("100"), date(2025, 1, 2): Decimal("109")}  # also +9%, within threshold
    jumps = unusual_jumps(idx, bench, threshold=0.05)
    assert jumps == []


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
