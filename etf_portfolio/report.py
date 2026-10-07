"""CLI entry point: ZERO CSV -> one self-contained HTML report.

docs/dev/portfolio-report-concept.md §9, docs/dev/refactor-concept.md.

    uv run etf-portfolio                    # latest CSV -> out/report-<valuation_date>.html
    uv run etf-portfolio --as-of 2026-01-01 # value as of a specific date (must be >= booking_as_of)
    uv run etf-portfolio --list-instruments # every ISIN ever traded + ticker/coverage status
    uv run etf-portfolio --open             # build, then open in the default browser
    uv run python -m etf_portfolio          # equivalent, if you prefer module form
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from etf_portfolio import kpi, lookthrough
from etf_portfolio.classify import apply_overrides, classify, redact
from etf_portfolio.health import compute_health_checks
from etf_portfolio.ledger import build_ledger, holding_periods
from etf_portfolio.load import (
    HeaderMismatchError,
    find_latest_csv,
    load_rows,
    summarize,
)
from etf_portfolio.narrative import build_summary
from etf_portfolio.prices import load_cache, resolve_closes
from etf_portfolio.render import render_report

# etf_portfolio/report.py -> package dir's parent is the repo root, where
# data/, cache/, out/ and config.yaml live.
ROOT = Path(__file__).resolve().parent.parent

BENCHMARK_KEY = "__benchmark__"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def trade_points_by_isin(events) -> dict[str, list[tuple[date, Decimal]]]:
    """§2.3 implied-price inputs: execution price (|amount| / qty) per trade."""
    out: dict[str, list[tuple[date, Decimal]]] = {}
    for ev in events:
        if ev.kind not in ("BUY", "SELL") or not ev.isin or not ev.qty:
            continue
        price = abs(ev.row.amount) / ev.qty
        out.setdefault(ev.isin, []).append((ev.row.booking, price))
    return out


def build_ctx(
    rows,
    events,
    ledger,
    cfg,
    price_data,
    booking_as_of: date,
    valuation_date: date,
    source_file: Path,
) -> dict:
    series_by_isin, price_warnings = price_data  # dict[isin, PriceSeries]
    have_prices = bool(series_by_isin)

    net_contrib = kpi.net_contributions(ledger)
    invested = kpi.invested_cost_basis(ledger)
    realized = kpi.realized_gain_total(ledger)
    taxes = kpi.taxes_total(ledger)
    dividends = kpi.dividends_total(ledger)

    positions = {isin: pos.qty for isin, pos in ledger.positions_now.items()}
    valuation = kpi.portfolio_value(positions, ledger.cash, series_by_isin, valuation_date)
    value_kpi = valuation.value
    gain_eur = None if value_kpi is None else value_kpi - net_contrib

    global_start = min((d for d, _ in ledger.flows), default=valuation_date)
    invested_series = [
        (d.isoformat(), float(v))
        for d, v in kpi.invested_capital_series(ledger.flows, global_start, valuation_date)
    ]

    coverage = kpi.compute_coverage(ledger.qty_history, series_by_isin, global_start, valuation_date) if have_prices else None

    benchmark_cfg = cfg.get("benchmark", {}) or {}
    bench_series_obj = series_by_isin.get(BENCHMARK_KEY)
    bench_closes = bench_series_obj.closes if bench_series_obj else None

    xirr_value = None
    twr_value = None
    twr_pa_value = None
    twr_ytd_value = None
    mdd_value = None
    akt_dd_value = None
    vol_value = None
    months: list[dict] = []
    best_month = worst_month = None
    benchmark_twr_value = None
    twr_idx: list[tuple[date, float]] = []
    value_series: list[tuple[str, float]] | None = None
    benchmark_series: list[tuple[str, float]] | None = None

    if value_kpi is not None and ledger.flows:
        # XIRR convention: cash the investor pays in is negative to them;
        # the ledger stores deposits as positive, so flip the sign here.
        xflows = [(d, -a) for d, a in ledger.flows] + [(valuation_date, value_kpi)]
        xirr_value = kpi.xirr(xflows)

    if coverage and ledger.flows and coverage.start <= valuation_date:
        series = kpi.daily_value_series(ledger, series_by_isin, coverage.start, valuation_date)
        twr_idx = kpi.twr_series(series)
        if twr_idx:
            twr_value = twr_idx[-1][1] - 1.0
            mdd_value = kpi.max_drawdown(twr_idx)
            akt_dd_value = kpi.current_drawdown(twr_idx)
            vol_value = kpi.volatility_annualized(twr_idx)
            days = (valuation_date - coverage.start).days
            twr_pa_value = kpi.twr_annualized(twr_value, days)
            twr_ytd_value = kpi.twr_ytd(twr_idx, valuation_date)
            months = kpi.monthly_returns(twr_idx)
            best_month, worst_month = kpi.best_worst_month(months)
        value_series = [(d.isoformat(), float(v)) for d, v, _flow in series]

        if bench_closes:
            from etf_portfolio.prices import close_on_or_before

            p0 = close_on_or_before(bench_closes, coverage.start)
            p1 = close_on_or_before(bench_closes, valuation_date)
            if p0 and p1 and p0[0] > 0:
                benchmark_twr_value = float(p1[0] / p0[0]) - 1.0

            # Rebase the benchmark-wealth line to start at the portfolio's
            # own value on coverage.start, so the two lines are comparable
            # from the same starting point (§5); flows already "inside"
            # that seed are excluded to avoid double-counting them.
            seed_value = series[0][1]
            seeded_flows = [(coverage.start, seed_value)] + [
                (d, a) for d, a in ledger.flows if d > coverage.start
            ]
            benchmark_series = [
                (d.isoformat(), float(v))
                for d, v in kpi.benchmark_wealth_series(seeded_flows, bench_closes, coverage.start, valuation_date)
            ]

    # --- docs/dev/report-v3-concept.md §9 risk/performance additions -------
    underwater_series: list[tuple[str, float]] | None = None
    benchmark_underwater_series: list[tuple[str, float]] | None = None
    dd_max_days = dd_current_days = None
    downside_vol_value = sharpe_value = sortino_value = None
    beta_value = corr_value = None
    rolling_1y_value = None
    annual_rets: list[dict] = []
    annual_rets_bench: list[dict] = []
    months_bench: list[dict] = []
    unusual_jumps: list[dict] = []
    risk_free_rate = (cfg.get("risk_free", {}) or {}).get("rate", 0.0)

    if coverage and twr_idx:
        underwater_series = [(d.isoformat(), v) for d, v in kpi.drawdown_series(twr_idx)]
        dd_max_days, dd_current_days = kpi.drawdown_durations(twr_idx)
        downside_vol_value = kpi.downside_deviation_annualized(twr_idx)
        sharpe_value = kpi.sharpe_ratio(twr_pa_value, vol_value, risk_free_rate)
        sortino_value = kpi.sortino_ratio(twr_pa_value, downside_vol_value, risk_free_rate)
        rolling_1y_value = kpi.rolling_return(twr_idx, valuation_date)
        annual_rets = kpi.annual_returns(twr_idx)
        unusual_jumps = kpi.unusual_jumps(twr_idx, bench_closes)
        if bench_closes:
            beta_value, corr_value = kpi.beta_and_correlation(twr_idx, bench_closes)
            # benchmark's own raw price index (no cashflows) over the same
            # window, for the annual-bars/underwater comparisons — distinct
            # from benchmark_series above, which is wealth-with-cashflows.
            from etf_portfolio.prices import close_on_or_before as _cob

            base_hit = _cob(bench_closes, coverage.start)
            if base_hit and base_hit[0]:
                bench_idx = []
                for d, _v in twr_idx:
                    hit = _cob(bench_closes, d)
                    if hit:
                        bench_idx.append((d, float(hit[0] / base_hit[0])))
                if bench_idx:
                    annual_rets_bench = kpi.annual_returns(bench_idx)
                    months_bench = kpi.monthly_returns(bench_idx)
                    benchmark_underwater_series = [(d.isoformat(), v) for d, v in kpi.drawdown_series(bench_idx)]

    holdings = []
    instruments_cfg = cfg.get("instruments", {}) or {}
    for isin, pos in sorted(ledger.positions_now.items(), key=lambda kv: kv[1].name or kv[0]):
        if pos.qty == 0:
            continue
        price_date = valuation.price_dates.get(isin)
        source = valuation.price_sources.get(isin)
        series_obj = series_by_isin.get(isin)
        price = series_obj.closes.get(price_date) if series_obj and price_date else None
        mv = pos.qty * price if price is not None else None
        unrealized = (mv - pos.cost_basis) if mv is not None else None
        holdings.append(
            {
                "isin": isin,
                "name": pos.name or isin,
                "qty": pos.qty,
                "avg_cost": pos.avg_cost,
                "price": price,
                "price_date": price_date,
                "estimated": source == "implied",
                "value": mv,
                "weight": None,  # filled below once total value is known
                "unrealized": unrealized,
                "unrealized_pct": (unrealized / pos.cost_basis) if unrealized is not None and pos.cost_basis else None,
            }
        )
    known_value = sum((h["value"] for h in holdings if h["value"] is not None), Decimal(0))
    for h in holdings:
        if h["value"] is not None and known_value:
            h["weight"] = float(h["value"] / known_value)

    weights = {h["isin"]: h["weight"] for h in holdings if h["weight"] is not None}
    largest = kpi.largest_position(weights)
    top3 = kpi.top_n_share(weights, 3) if weights else None
    ever_traded = len({isin for _, isin, _ in ledger.qty_history})
    turnover = kpi.turnover_12m(ledger.sells, valuation_date, value_kpi or valuation.priced_value or None)

    # --- docs/dev/report-v3-concept.md §6/§9: look-through, health, narrative
    holdings_values = {h["isin"]: h["value"] for h in holdings if h["value"] is not None}
    weighted_ter_pct, weighted_ter_eur, ter_reason = lookthrough.weighted_ter(holdings_values, instruments_cfg)
    satellite_share_value = lookthrough.satellite_share(weights, instruments_cfg)
    region_lt = lookthrough.lookthrough(weights, instruments_cfg, "regions")
    sector_lt = lookthrough.lookthrough(weights, instruments_cfg, "sectors")
    currency_lt = lookthrough.lookthrough(weights, instruments_cfg, "currency")
    overlap_pairs, overlap_reason = lookthrough.overlap_matrix(weights, instruments_cfg)

    unresolved_dq_count = sum(1 for e in events if e.kind == "UNKNOWN") + sum(1 for i in ledger.dq if i.severity == "error")
    health_checks = compute_health_checks(
        largest,
        {h["isin"]: (instruments_cfg.get(h["isin"], {}) or {}).get("short") or h["name"] for h in holdings},
        top3,
        satellite_share_value,
        float(weighted_ter_pct) if weighted_ter_pct is not None else None,
        akt_dd_value,
        coverage.implied_share if coverage else 1.0,
        unresolved_dq_count,
        cfg.get("targets", {}),
    )

    # Waterfall bridge, §3: Einzahlungen -> +realisiert -> +unrealisiert ->
    # +Erträge -> -Steuern -> Depotwert. unrealized is only meaningful if
    # every current holding is priced; otherwise the bridge is n/a, not a
    # guess built on a partial sum.
    unrealized_total = sum((h["unrealized"] for h in holdings if h["unrealized"] is not None), Decimal(0)) if value_kpi is not None else None
    waterfall = None
    if value_kpi is not None and unrealized_total is not None:
        steps = [
            ("Einzahlungen", net_contrib),
            ("Realisiert", realized),
            ("Unrealisiert", unrealized_total),
            ("Erträge", dividends),
            ("Steuern", taxes),
        ]
        running = Decimal(0)
        bars = []
        for label, delta in steps:
            bars.append({"label": label, "start": float(running), "delta": float(delta)})
            running += delta
        residual = value_kpi - running
        if abs(residual) > Decimal("1"):
            bars.append({"label": "Rest (Cash-Timing/Rundung)", "start": float(running), "delta": float(residual)})
            running += residual
        waterfall = {"bars": bars, "total": float(running)}

    savings_by_month: dict[str, Decimal] = {}
    for d, amt in ledger.flows:
        if amt > 0:
            month = d.strftime("%Y-%m")
            savings_by_month[month] = savings_by_month.get(month, Decimal(0)) + amt

    trades_by_month: dict[str, dict] = {}
    for ev in events:
        if ev.kind in ("BUY", "SELL"):
            month = ev.row.booking.strftime("%Y-%m")
            bucket = trades_by_month.setdefault(month, {"buy_count": 0, "sell_count": 0, "buy_amt": Decimal(0), "sell_amt": Decimal(0)})
            if ev.kind == "BUY":
                bucket["buy_count"] += 1
                bucket["buy_amt"] += -ev.row.amount
            else:
                bucket["sell_count"] += 1
                bucket["sell_amt"] += ev.row.amount

    income_by_month = {m: dict(v) for m, v in sorted(ledger.income.items())}

    # §8: price coverage per ISIN, for the data-quality section.
    price_coverage = []
    for isin, series in sorted(series_by_isin.items()):
        if isin == BENCHMARK_KEY:
            continue
        market_days = sum(1 for s in series.source.values() if s == "market")
        implied_days = series.implied_days
        if not series.closes:
            status = "keine Kursdaten"
        elif implied_days == 0:
            status = "vollständig (Markt)"
        elif market_days == 0:
            status = "implizit (aus Kaufpreisen)"
        else:
            status = "gemischt (Markt + implizit)"
        price_coverage.append(
            {
                "isin": isin,
                "name": instruments_cfg.get(isin, {}).get("short") or ledger.names.get(isin, isin),
                "status": status,
                "from": min(series.closes) if series.closes else None,
                "to": max(series.closes) if series.closes else None,
                "market_days": market_days,
                "implied_days": implied_days,
            }
        )

    dq_items = []
    for issue in ledger.dq:
        dq_items.append({"severity": issue.severity, "message": issue.message})
    for ev in events:
        if ev.kind == "UNKNOWN":
            dq_items.append(
                {
                    "severity": "warning",
                    "message": f"row {ev.row.idx} ({ev.row.booking.isoformat()}, {ev.row.amount}): unrecognized — {redact(ev.row.text)}",
                }
            )
    for w in price_warnings:
        dq_items.append({"severity": "info", "message": w})
    if valuation.stale:
        dq_items.append({"severity": "warning", "message": f"Kurs älter als {5} Tage: {', '.join(valuation.stale)}"})

    vap_checks = [
        {**v, "name": v["name"] or v["isin"]}
        for v in sorted(ledger.vap_checks, key=lambda x: x["date"])
    ]

    trade_buckets = build_trade_buckets(events, booking_as_of)

    asset_class_weights: dict[str, float] = {}
    for h in holdings:
        if h["weight"] is None:
            continue
        cls = (instruments_cfg.get(h["isin"], {}) or {}).get("class") or "Nicht zugeordnet"
        asset_class_weights[cls] = asset_class_weights.get(cls, 0.0) + h["weight"]

    perf_on_deposit = float(gain_eur / net_contrib) if gain_eur is not None and net_contrib else None

    chart_data = {
        "savings_by_month": {m: float(v) for m, v in sorted(savings_by_month.items())},
        "trades_by_month": {
            m: {k: (float(v) if isinstance(v, Decimal) else v) for k, v in b.items()}
            for m, b in sorted(trades_by_month.items())
        },
        "income_by_month": {
            m: {k: float(v) for k, v in b.items()} for m, b in income_by_month.items()
        },
        "holdings_weights": {h["isin"]: h["weight"] for h in holdings if h["weight"] is not None},
        "holdings_names": {
            h["isin"]: (instruments_cfg.get(h["isin"], {}) or {}).get("short") or h["name"]
            for h in holdings
        },
        "asset_class_weights": asset_class_weights,
        "monthly_returns": months,
        "realized_by_isin_named": {
            (instruments_cfg.get(isin, {}) or {}).get("short") or ledger.names.get(isin) or isin: float(amt)
            for isin, amt in ledger.realized.items()
            if amt != 0
        },
        "wealth": {
            "invested": invested_series,
            "benchmark": benchmark_series,
            "value": value_series,
            "benchmark_name": benchmark_cfg.get("name"),
        },
        "underwater": underwater_series,
        "benchmark_underwater": benchmark_underwater_series,
        "annual_returns": annual_rets,
        "annual_returns_benchmark": annual_rets_bench,
        "monthly_returns_benchmark": months_bench,
        "start_value": float(value_kpi) if value_kpi is not None else 0.0,
        "target_wealth_10y": (cfg.get("targets", {}) or {}).get("target_wealth_10y"),
        "waterfall": waterfall,
        "region_lt": region_lt.breakdown if region_lt.available else None,
        "sector_lt": sector_lt.breakdown if sector_lt.available else None,
        "currency_lt": currency_lt.breakdown if currency_lt.available else None,
    }

    for jump in unusual_jumps:
        bench_text = f"{jump['benchmark_ret']:+.1%}" if jump["benchmark_ret"] is not None else "n/a"
        dq_items.append(
            {
                "severity": "info",
                "message": (
                    f"{jump['date'].isoformat()}: Depotwert-Tagesrendite {jump['portfolio_ret']:+.1%} "
                    f"(Benchmark {bench_text}) — ungewöhnlicher Ausschlag, nicht notwendigerweise ein Fehler "
                    "(konzentriertes Themen-Depot kann stärker schwanken als der Index)"
                ),
            }
        )

    kpis = {
        "net_contributions": net_contrib,
        "invested": invested,
        "realized": realized,
        "taxes": taxes,
        "dividends": dividends,
        "value": value_kpi,
        "value_reason": valuation.reason,
        "value_estimated": bool(valuation.estimated),
        "gain_eur": gain_eur,
        "xirr": xirr_value,
        "twr": twr_value,
        "twr_pa": twr_pa_value,
        "twr_ytd": twr_ytd_value,
        "twr_start": coverage.start if coverage else None,
        "coverage_estimated": coverage.estimated if coverage else False,
        "coverage_implied_share": coverage.implied_share if coverage else 0.0,
        "coverage_unpriced": coverage.unpriced_isins if coverage else [],
        "max_drawdown": mdd_value,
        "current_drawdown": akt_dd_value,
        "volatility": vol_value,
        "benchmark_twr": benchmark_twr_value,
        "largest_position": largest,
        "top3_share": top3,
        "drawdown_duration_max_days": dd_max_days,
        "drawdown_duration_current_days": dd_current_days,
        "downside_volatility": downside_vol_value,
        "sharpe": sharpe_value,
        "sortino": sortino_value,
        "beta": beta_value,
        "correlation": corr_value,
        "rolling_1y": rolling_1y_value,
        "risk_free_rate": risk_free_rate,
        "weighted_ter": weighted_ter_pct,
        "weighted_ter_eur": weighted_ter_eur,
        "ter_reason": ter_reason,
        "satellite_share": satellite_share_value,
        "instruments_held": len(ledger.positions_now),
        "instruments_ever": ever_traded,
        "turnover_12m": turnover,
        "best_month": best_month,
        "worst_month": worst_month,
    }

    summary_sentences = build_summary(kpis, chart_data["holdings_names"])

    unresolved_for_dot = sum(1 for e in events if e.kind == "UNKNOWN") + sum(1 for i in ledger.dq if i.severity == "error")
    implied_share_for_dot = coverage.implied_share if coverage else 1.0
    if unresolved_for_dot > 0:
        dq_dot_color, dq_dot_title = "red", f"{unresolved_for_dot} ungelöste Buchung(en)"
    elif implied_share_for_dot < 0.20:
        dq_dot_color, dq_dot_title = "green", f"{implied_share_for_dot:.0%} der Kurse geschätzt"
    elif implied_share_for_dot < 0.50:
        dq_dot_color, dq_dot_title = "amber", f"{implied_share_for_dot:.0%} der Kurse geschätzt"
    else:
        dq_dot_color, dq_dot_title = "red", f"{implied_share_for_dot:.0%} der Kurse geschätzt"

    return {
        "booking_as_of": booking_as_of,
        "valuation_date": valuation_date,
        "booking_date_range": summarize(rows),
        "source_file": source_file.name,
        "parser_version": "1.0",
        "dq_dot_color": dq_dot_color,
        "dq_dot_title": dq_dot_title,
        "kpis": kpis,
        "health_checks": health_checks,
        "summary_sentences": summary_sentences,
        "waterfall": waterfall,
        "overlap_pairs": overlap_pairs,
        "overlap_reason": overlap_reason,
        "region_lt": region_lt,
        "sector_lt": sector_lt,
        "currency_lt": currency_lt,
        "holdings": holdings,
        "sells": sorted(ledger.sells, key=lambda s: s.date, reverse=True),
        "realized_by_isin": {isin: amt for isin, amt in ledger.realized.items() if amt != 0},
        "price_coverage": price_coverage,
        "monthly_returns": months,
        "realized_by_isin_named": chart_data["realized_by_isin_named"],
        "dq_items": dq_items,
        "vap_checks": vap_checks,
        "data_json": "",  # filled by render() with escaped JSON
        "chart_data": chart_data,
        "goal": cfg.get("goal", {}) or {},
        "event_count": len(events),
        "unknown_count": sum(1 for e in events if e.kind == "UNKNOWN"),
        "buy_count": sum(1 for e in events if e.kind == "BUY"),
        "sell_count": sum(1 for e in events if e.kind == "SELL"),
        "cash_sum": ledger.cash,
        "withdrawals_sum": sum((amt for _, amt in ledger.flows if amt < 0), Decimal(0)),
        "perf_on_deposit": perf_on_deposit,
        "trade_buckets": trade_buckets,
        "dq_error_count": sum(1 for d in dq_items if d["severity"] == "error"),
        "dq_warning_count": sum(1 for d in dq_items if d["severity"] == "warning"),
    }


def build_trade_buckets(events, as_of: date) -> list[dict]:
    """Buy/sell counts over a few trailing windows, ZERO-dashboard style.

    Each window is its own trailing query (not a mutually-exclusive
    partition) — e.g. "1 Jahr" overlaps "1 Monat".
    """
    windows = [
        ("Heute", 0),
        ("Gestern", 1),
        ("1 Woche", 6),
        ("1 Monat", 29),
        ("1 Jahr", 364),
    ]
    trades = [ev for ev in events if ev.kind in ("BUY", "SELL")]
    out = []
    for label, lookback in windows:
        if label == "Gestern":
            cutoff_lo = cutoff_hi = as_of - timedelta(days=1)
        else:
            cutoff_lo, cutoff_hi = as_of - timedelta(days=lookback), as_of
        buy = sum(1 for ev in trades if cutoff_lo <= ev.row.booking <= cutoff_hi and ev.kind == "BUY")
        sell = sum(1 for ev in trades if cutoff_lo <= ev.row.booking <= cutoff_hi and ev.kind == "SELL")
        out.append({"label": label, "buy": buy, "sell": sell})
    out.append({"label": "Gesamt", "buy": sum(1 for ev in trades if ev.kind == "BUY"), "sell": sum(1 for ev in trades if ev.kind == "SELL")})
    return out


def list_instruments(ledger, events, cfg: dict, cache_path: Path, as_of: date) -> None:
    """`--list-instruments`: every ISIN ever traded, config/coverage at a glance."""
    instruments_cfg = cfg.get("instruments", {}) or {}
    spans = holding_periods(ledger.qty_history)
    cache = load_cache(cache_path)
    isins = sorted({isin for _, isin, _ in ledger.qty_history})

    print(f"{'ISIN':<14} {'name':<36} {'first':<11} {'last':<11} {'qty now':>10}  {'ticker':<10} {'cached':<23} flag")
    for isin in isins:
        name = ledger.names.get(isin, "")[:36]
        isin_spans = spans.get(isin, [])
        first = min((s for s, _ in isin_spans), default=None)
        last_open = any(e is None for _, e in isin_spans)
        last = None if last_open else max((e for _, e in isin_spans if e is not None), default=None)
        qty_now = ledger.positions_now[isin].qty if isin in ledger.positions_now else Decimal(0)
        ticker = (instruments_cfg.get(isin, {}) or {}).get("ticker") or ""
        cached = cache.get(isin, {})
        cached_range = f"{min(cached)}..{max(cached)}" if cached else ""
        flag = "" if ticker or cached else "MISSING"
        print(
            f"{isin:<14} {name:<36} {first.isoformat() if first else '':<11} "
            f"{'offen' if last_open else (last.isoformat() if last else ''):<11} {qty_now:>10} "
            f"{ticker:<10} {cached_range:<23} {flag}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained portfolio HTML report.")
    parser.add_argument("--file", type=Path, help="CSV file to use instead of auto-selecting the latest")
    parser.add_argument("--as-of", type=str, help="valuation date YYYY-MM-DD (default: today; must be >= last booking date)")
    parser.add_argument("--list-instruments", action="store_true", help="list every ISIN ever traded with ticker/coverage status and exit")
    parser.add_argument("--offline", action="store_true", help="never hit the network; cache only")
    parser.add_argument("--no-prices", action="store_true", help="skip phase-2 valuation entirely")
    parser.add_argument("--open", action="store_true", help="open the report in the default browser")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    data_dir = ROOT / "data"
    cache_path = ROOT / "cache" / "prices.json"
    out_dir = ROOT / "out"
    config_path = ROOT / "config.yaml"
    instruments_path = ROOT / "instruments.yaml"

    try:
        csv_path = args.file or find_latest_csv(data_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        rows = load_rows(csv_path)
    except HeaderMismatchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"file: {csv_path.name}  ({summarize(rows)})")

    cfg = load_config(config_path)
    # instruments.yaml is reference data (docs/dev/report-v3-concept.md §6),
    # kept separate from config.yaml's settings but merged in here so the
    # rest of the code can keep reading cfg["instruments"] as before.
    cfg["instruments"] = load_config(instruments_path)
    events = classify(rows)
    events = apply_overrides(events, cfg.get("overrides", []))

    kind_counts: dict[str, int] = {}
    for ev in events:
        kind_counts[ev.kind] = kind_counts.get(ev.kind, 0) + 1
    print("event counts:", kind_counts)

    ledger = build_ledger(events)
    print(f"cash (reconstructed): {ledger.cash}")

    booking_as_of = max((r.booking for r in rows), default=date.today())

    if args.as_of:
        try:
            valuation_date = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"error: --as-of must be YYYY-MM-DD, got {args.as_of!r}", file=sys.stderr)
            return 1
    else:
        valuation_date = date.today()

    if valuation_date < booking_as_of:
        print(
            f"error: --as-of {valuation_date.isoformat()} is before the last booking date "
            f"{booking_as_of.isoformat()} — valuing a ledger before it's complete isn't supported "
            "(would need a truncated-ledger replay, not just an earlier price lookup)",
            file=sys.stderr,
        )
        return 1

    if args.list_instruments:
        list_instruments(ledger, events, cfg, cache_path, valuation_date)
        return 0

    price_data: tuple[dict, list[str]] = ({}, [])
    if not args.no_prices:
        instruments = dict(cfg.get("instruments", {}) or {})
        spans = holding_periods(ledger.qty_history)
        points = trade_points_by_isin(events)

        benchmark_cfg = cfg.get("benchmark", {}) or {}
        if benchmark_cfg.get("ticker") and ledger.flows:
            instruments[BENCHMARK_KEY] = {"ticker": benchmark_cfg["ticker"]}
            spans[BENCHMARK_KEY] = [(min(d for d, _ in ledger.flows), None)]

        series_by_isin, warnings = resolve_closes(instruments, points, spans, valuation_date, cache_path, args.offline)
        price_data = (series_by_isin, warnings)
        for w in warnings:
            print(f"price warning: {w}")

    ctx = build_ctx(rows, events, ledger, cfg, price_data, booking_as_of, valuation_date, csv_path)

    out_path = out_dir / f"report-{valuation_date.isoformat()}.html"
    render_report(ctx, out_path)
    print(f"report: {out_path}")

    has_dq_errors = any(d["severity"] == "error" for d in ctx["dq_items"])

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())

    return 2 if has_dq_errors else 0


if __name__ == "__main__":
    sys.exit(main())
