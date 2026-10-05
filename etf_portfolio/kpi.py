"""KPIs: phase 1 (no prices needed) and phase 2 (needs prices).

docs/dev/portfolio-report-concept.md §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .ledger import LedgerResult
from .prices import close_on_or_before


@dataclass
class Kpi:
    label: str
    value: str | None  # preformatted, or None if n/a
    reason: str | None = None  # why it's n/a


# --- Phase 1 --------------------------------------------------------------


def net_contributions(ledger: LedgerResult) -> Decimal:
    return sum((amt for _, amt in ledger.flows), Decimal(0))


def invested_cost_basis(ledger: LedgerResult) -> Decimal:
    return sum((p.cost_basis for p in ledger.positions_now.values()), Decimal(0))


def realized_gain_total(ledger: LedgerResult) -> Decimal:
    return sum(ledger.realized.values(), Decimal(0))


def taxes_total(ledger: LedgerResult) -> Decimal:
    total = Decimal(0)
    for month in ledger.income.values():
        total += month.get("tax", Decimal(0)) + month.get("tax_vap", Decimal(0))
    return total


def dividends_total(ledger: LedgerResult) -> Decimal:
    return sum((month.get("dividend", Decimal(0)) for month in ledger.income.values()), Decimal(0))


def invested_capital_series(
    flows: list[tuple[date, Decimal]], start: date, end: date
) -> list[tuple[date, Decimal]]:
    """Cumulative net external cash in, day by day — no prices needed.

    This is the "what I actually put in" line for the value-vs-invested
    chart; it never goes n/a, unlike the priced portfolio value.
    """
    by_day: dict[date, Decimal] = {}
    for d, amt in flows:
        by_day[d] = by_day.get(d, Decimal(0)) + amt
    out: list[tuple[date, Decimal]] = []
    running = Decimal(0)
    day = start
    while day <= end:
        running += by_day.get(day, Decimal(0))
        out.append((day, running))
        day += timedelta(days=1)
    return out


# --- Phase 2 ---------------------------------------------------------------


def portfolio_value(
    positions: dict[str, Decimal],
    cash: Decimal,
    closes_by_isin: dict[str, dict[date, Decimal]],
    as_of: date,
) -> tuple[Decimal, bool, list[str], dict[str, date]]:
    """value, partial_flag, missing_isins, price_date_by_isin."""
    value = cash
    missing: list[str] = []
    price_dates: dict[str, date] = {}
    for isin, qty in positions.items():
        if qty == 0:
            continue
        closes = closes_by_isin.get(isin)
        hit = close_on_or_before(closes, as_of) if closes else None
        if hit is None:
            missing.append(isin)
            continue
        price, price_date = hit
        value += qty * price
        price_dates[isin] = price_date
    return value, bool(missing), missing, price_dates


def xirr(flows: list[tuple[date, Decimal]], guess: float = 0.1) -> float | None:
    """Solve for the annualized rate given signed (date, amount) cashflows.

    Newton's method with a bisection fallback on [-0.99, 10]. Returns None
    if there's no sign change (can't bracket a root).
    """
    if len(flows) < 2:
        return None
    amounts = [float(a) for _, a in flows]
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None

    t0 = flows[0][0]
    days = [(d - t0).days / 365.0 for d, _ in flows]

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for a, t in zip(amounts, days, strict=True))

    def dnpv(rate: float) -> float:
        return sum(-t * a / (1.0 + rate) ** (t + 1) for a, t in zip(amounts, days, strict=True))

    rate = guess
    for _ in range(100):
        try:
            f = npv(rate)
            df = dnpv(rate)
        except (OverflowError, ZeroDivisionError):
            break
        if df == 0:
            break
        new_rate = rate - f / df
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = new_rate
        if rate <= -0.999999:
            rate = -0.999999

    lo, hi = -0.99, 10.0
    try:
        f_lo, f_hi = npv(lo), npv(hi)
    except OverflowError:
        return None
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def coverage_start_date(
    ledger: LedgerResult,
    closes_by_isin: dict[str, dict[date, Decimal]],
    global_start: date,
    end: date,
) -> date:
    """First day from which every *then-held* ISIN has a usable price.

    Historical, now-fully-sold-out positions (e.g. an ETF bought and sold
    in 2024, with no ticker configured) would otherwise silently drop out
    of the valuation on their buy day and reappear on their sell day,
    producing bogus swings. Rather than require a ticker for every ISIN
    ever traded, TWR/drawdown are computed only from the day after the
    last day any held-but-unpriced ISIN existed — documented in the
    report as "missing is not zero", not silently approximated.
    """
    qty_by_isin: dict[str, Decimal] = {}
    qty_events = sorted(ledger.qty_history, key=lambda t: t[0])
    qi = 0
    last_uncovered: date | None = None
    day = global_start
    while day <= end:
        while qi < len(qty_events) and qty_events[qi][0] <= day:
            _, isin, qty_after = qty_events[qi]
            qty_by_isin[isin] = qty_after
            qi += 1
        for isin, qty in qty_by_isin.items():
            if qty == 0:
                continue
            closes = closes_by_isin.get(isin)
            if not (closes and close_on_or_before(closes, day)):
                last_uncovered = day
                break
        day += timedelta(days=1)
    return (last_uncovered + timedelta(days=1)) if last_uncovered else global_start


def daily_value_series(
    ledger: LedgerResult,
    closes_by_isin: dict[str, dict[date, Decimal]],
    start: date,
    end: date,
) -> list[tuple[date, Decimal, Decimal]]:
    """[(day, portfolio_value, external_flow_that_day)] for each day in [start, end].

    Uses end-of-day convention: a flow on day t is included in day t's
    value and excluded from day t's return numerator per the TWR formula
    below (documented in the report). Callers should pick `start` via
    coverage_start_date() so every held ISIN in range has a price.
    """
    qty_by_isin: dict[str, Decimal] = {}
    qty_events = sorted(ledger.qty_history, key=lambda t: t[0])
    flow_by_day: dict[date, Decimal] = {}
    for d, amt in ledger.flows:
        flow_by_day[d] = flow_by_day.get(d, Decimal(0)) + amt
    cash_by_day = dict(ledger.cash_ts)

    out: list[tuple[date, Decimal, Decimal]] = []
    qi = 0
    cash = Decimal(0)
    last_cash = Decimal(0)
    day = start
    while day <= end:
        while qi < len(qty_events) and qty_events[qi][0] <= day:
            _, isin, qty_after = qty_events[qi]
            qty_by_isin[isin] = qty_after
            qi += 1
        if day in cash_by_day:
            last_cash = cash_by_day[day]
        cash = last_cash

        value = cash
        for isin, qty in qty_by_isin.items():
            if qty == 0:
                continue
            closes = closes_by_isin.get(isin)
            hit = close_on_or_before(closes, day) if closes else None
            if hit:
                value += qty * hit[0]
        out.append((day, value, flow_by_day.get(day, Decimal(0))))
        day += timedelta(days=1)
    return out


def twr_series(series: list[tuple[date, Decimal, Decimal]]) -> list[tuple[date, float]]:
    """Chained TWR index starting at 1.0. r_t = (V_t - F_t) / V_{t-1} - 1."""
    out: list[tuple[date, float]] = []
    index = 1.0
    prev_value: Decimal | None = None
    for d, value, flow in series:
        if prev_value is None or prev_value == 0:
            out.append((d, index))
            prev_value = value
            continue
        r = float((value - flow) / prev_value) - 1.0
        index *= 1.0 + r
        out.append((d, index))
        prev_value = value
    return out


def max_drawdown(index_series: list[tuple[date, float]]) -> float | None:
    if not index_series:
        return None
    peak = index_series[0][1]
    mdd = 0.0
    for _, v in index_series:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def benchmark_wealth_series(
    flows: list[tuple[date, Decimal]],
    benchmark_closes: dict[date, Decimal],
    start: date,
    end: date,
) -> list[tuple[date, Decimal]]:
    """Simulate buying the benchmark with every external flow on its date."""
    units = Decimal(0)
    flow_by_day: dict[date, Decimal] = {}
    for d, amt in flows:
        flow_by_day[d] = flow_by_day.get(d, Decimal(0)) + amt

    out: list[tuple[date, Decimal]] = []
    day = start
    while day <= end:
        if day in flow_by_day:
            hit = close_on_or_before(benchmark_closes, day)
            if hit:
                price, _ = hit
                if price > 0:
                    units += flow_by_day[day] / price
        hit = close_on_or_before(benchmark_closes, day)
        value = units * hit[0] if hit else Decimal(0)
        out.append((day, value))
        day += timedelta(days=1)
    return out
