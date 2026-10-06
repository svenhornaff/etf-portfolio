"""KPIs: phase 1 (no prices needed) and phase 2 (needs prices).

docs/dev/portfolio-report-concept.md §6.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .ledger import LedgerResult
from .prices import PriceSeries, close_on_or_before


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


@dataclass
class Valuation:
    """docs/dev/refactor-concept.md §3: the valuation guard.

    `value` is None — not a partial sum — if any currently-held ISIN has
    no resolved price at all. A held ISIN priced only via an implied
    series still counts as priced, but shows up in `estimated`.
    """

    value: Decimal | None
    priced_value: Decimal  # sum of what *is* priced, for the "missing" warning text
    missing: list[str] = field(default_factory=list)
    estimated: list[str] = field(default_factory=list)  # held isins priced via implied series today
    price_dates: dict[str, date] = field(default_factory=dict)
    price_sources: dict[str, str] = field(default_factory=dict)  # isin -> "market" | "implied"
    stale: list[str] = field(default_factory=list)  # price older than as_of - 3 business days
    reason: str | None = None


def portfolio_value(
    positions: dict[str, Decimal],
    cash: Decimal,
    closes_by_isin: dict[str, dict[date, Decimal]] | dict[str, PriceSeries],
    as_of: date,
    stale_after_days: int = 5,  # ~3 business days, calendar approximation
) -> Valuation:
    priced_value = cash
    missing: list[str] = []
    estimated: list[str] = []
    stale: list[str] = []
    price_dates: dict[str, date] = {}
    price_sources: dict[str, str] = {}
    for isin, qty in positions.items():
        if qty == 0:
            continue
        series = closes_by_isin.get(isin)
        if isinstance(series, PriceSeries):
            hit = series.close_on_or_before(as_of)
        else:
            plain_hit = close_on_or_before(series, as_of) if series else None
            hit = (plain_hit[0], plain_hit[1], "market") if plain_hit else None
        if hit is None:
            missing.append(isin)
            continue
        price, price_date, source = hit
        priced_value += qty * price
        price_dates[isin] = price_date
        price_sources[isin] = source
        if source == "implied":
            estimated.append(isin)
        if (as_of - price_date).days > stale_after_days:
            stale.append(isin)
    if missing:
        names = ", ".join(sorted(missing))
        return Valuation(
            value=None,
            priced_value=priced_value,
            missing=missing,
            estimated=estimated,
            price_dates=price_dates,
            price_sources=price_sources,
            stale=stale,
            reason=f"Kurs fehlt: {names}",
        )
    return Valuation(
        value=priced_value,
        priced_value=priced_value,
        missing=missing,
        estimated=estimated,
        price_dates=price_dates,
        price_sources=price_sources,
        stale=stale,
    )


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


@dataclass
class Coverage:
    """docs/dev/refactor-concept.md §2.5: replaces the old coverage_start_date.

    `start` is the first day every then-held ISIN has *some* resolved
    price (market or implied). `implied_share` is the fraction of
    held-isin-days in [start, end] priced only via the implied series —
    shown in the report as "≈ geschätzt (x % implizite Kurse)".
    """

    start: date
    implied_share: float
    unpriced_isins: list[str] = field(default_factory=list)

    @property
    def estimated(self) -> bool:
        return self.implied_share > 0.0


def compute_coverage(
    qty_history: list[tuple[date, str, Decimal]],
    series_by_isin: dict[str, PriceSeries],
    global_start: date,
    end: date,
) -> Coverage:
    qty_events = sorted(qty_history, key=lambda t: t[0])

    def walk_held(from_day: date, to_day: date):
        qty_by_isin: dict[str, Decimal] = {}
        qi = 0
        day = from_day
        while day <= to_day:
            while qi < len(qty_events) and qty_events[qi][0] <= day:
                _, isin, qty_after = qty_events[qi]
                qty_by_isin[isin] = qty_after
                qi += 1
            yield day, {isin: q for isin, q in qty_by_isin.items() if q != 0}
            day += timedelta(days=1)

    last_uncovered: date | None = None
    unpriced: set[str] = set()
    for day, held in walk_held(global_start, end):
        for isin in held:
            series = series_by_isin.get(isin)
            if not (series and series.close_on_or_before(day)):
                last_uncovered = day
                unpriced.add(isin)
    start = (last_uncovered + timedelta(days=1)) if last_uncovered else global_start

    total_isin_days = 0
    implied_isin_days = 0
    if start <= end:
        for day, held in walk_held(start, end):
            for isin in held:
                series = series_by_isin.get(isin)
                hit = series.close_on_or_before(day) if series else None
                if hit is None:
                    continue  # already reported via unpriced above
                total_isin_days += 1
                if hit[2] == "implied":
                    implied_isin_days += 1
    implied_share = (implied_isin_days / total_isin_days) if total_isin_days else 0.0

    return Coverage(start=start, implied_share=implied_share, unpriced_isins=sorted(unpriced))


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
            series = closes_by_isin.get(isin)
            if isinstance(series, PriceSeries):
                hit = series.close_on_or_before(day)
                price = hit[0] if hit else None
            else:
                hit = close_on_or_before(series, day) if series else None
                price = hit[0] if hit else None
            if price is not None:
                value += qty * price
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


# --- Phase 3: concept §4 KPI set -------------------------------------------


def daily_returns(index_series: list[tuple[date, float]]) -> list[float]:
    """Simple day-over-day returns on the TWR index, for volatility."""
    out = []
    for (_, a), (_, b) in zip(index_series, index_series[1:], strict=False):
        if a:
            out.append(b / a - 1.0)
    return out


def volatility_annualized(index_series: list[tuple[date, float]], min_obs: int = 120) -> float | None:
    """Annualized stdev of daily TWR returns × √252. None below `min_obs`."""
    rets = daily_returns(index_series)
    if len(rets) < min_obs:
        return None
    return statistics.pstdev(rets) * (252 ** 0.5)


def twr_annualized(twr_total: float, days: int) -> float | None:
    """TWR p.a., only meaningful once the coverage period is ≥ 1 year."""
    if days < 365:
        return None
    return (1.0 + twr_total) ** (365.0 / days) - 1.0


def twr_index_on(index_series: list[tuple[date, float]], d: date) -> float | None:
    """Last index value at or before d, or None if d is before coverage starts."""
    hit = None
    for day, v in index_series:
        if day > d:
            break
        hit = v
    return hit


def twr_ytd(index_series: list[tuple[date, float]], as_of: date) -> float | None:
    """Return since the last calendar year-end (or coverage start if later)."""
    if not index_series:
        return None
    year_end = date(as_of.year - 1, 12, 31)
    base = twr_index_on(index_series, year_end)
    if base is None:
        base = index_series[0][1]  # coverage starts mid-year: YTD = since coverage start
    latest = index_series[-1][1]
    if base == 0:
        return None
    return latest / base - 1.0


def current_drawdown(index_series: list[tuple[date, float]]) -> float | None:
    """Latest index value vs. its running peak — how far below the high-water mark now."""
    if not index_series:
        return None
    peak = index_series[0][1]
    for _, v in index_series:
        peak = max(peak, v)
    latest = index_series[-1][1]
    if peak == 0:
        return None
    return latest / peak - 1.0


def monthly_returns(index_series: list[tuple[date, float]]) -> list[dict]:
    """Calendar-month returns on the TWR index: [{month, ret, partial}].

    `partial` marks the first month if coverage doesn't start on the 1st
    (and the last, if it doesn't run through month-end) — the return is
    real but not comparable to a full month.
    """
    if not index_series:
        return []
    by_month: dict[str, list[tuple[date, float]]] = {}
    for d, v in index_series:
        by_month.setdefault(d.strftime("%Y-%m"), []).append((d, v))
    months = sorted(by_month)
    out = []
    prev_last_value = index_series[0][1]
    for i, m in enumerate(months):
        points = by_month[m]
        last_value = points[-1][1]
        first_day_of_month = date(points[0][0].year, points[0][0].month, 1)
        partial = (i == 0 and points[0][0] != first_day_of_month) or i == len(months) - 1 and points[-1][0].day < 28
        ret = (last_value / prev_last_value - 1.0) if prev_last_value else 0.0
        out.append({"month": m, "ret": ret, "partial": partial})
        prev_last_value = last_value
    return out


def best_worst_month(months: list[dict]) -> tuple[dict | None, dict | None]:
    complete = [m for m in months if not m["partial"]]
    pool = complete or months
    if not pool:
        return None, None
    return max(pool, key=lambda m: m["ret"]), min(pool, key=lambda m: m["ret"])


def largest_position(weights: dict[str, float]) -> tuple[str, float] | None:
    if not weights:
        return None
    isin = max(weights, key=lambda k: weights[k])
    return isin, weights[isin]


def top_n_share(weights: dict[str, float], n: int = 3) -> float:
    return sum(sorted(weights.values(), reverse=True)[:n])


def turnover_12m(sells: list, as_of: date, avg_value: Decimal | None) -> float | None:
    """Σ sell proceeds in the trailing 12 months ÷ average portfolio value.

    None if there's no current value to divide by — a ratio against an
    unknown denominator isn't a number, it's a guess.
    """
    if not avg_value:
        return None
    cutoff = as_of - timedelta(days=365)
    proceeds = sum((s.proceeds for s in sells if s.date > cutoff), Decimal(0))
    return float(proceeds / avg_value)
