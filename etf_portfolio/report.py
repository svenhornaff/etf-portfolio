"""CLI entry point: ZERO CSV -> one self-contained HTML report.

docs/dev/portfolio-report-concept.md §9.

    uv run etf-portfolio              # latest CSV in data/ -> out/report-YYYY-MM-DD.html
    uv run etf-portfolio --open       # same, then open in browser
    uv run python -m etf_portfolio    # equivalent, if you prefer module form
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from etf_portfolio import kpi
from etf_portfolio.classify import apply_overrides, classify, redact
from etf_portfolio.ledger import build_ledger
from etf_portfolio.load import (
    HeaderMismatchError,
    find_latest_csv,
    load_rows,
    summarize,
)
from etf_portfolio.prices import fetch_all
from etf_portfolio.render import render_report

# etf_portfolio/report.py -> package dir's parent is the repo root, where
# data/, cache/, out/ and config.yaml live.
ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_ctx(rows, events, ledger, cfg, price_data, as_of: date, source_file: Path) -> dict:
    closes_by_isin, price_warnings = price_data

    net_contrib = kpi.net_contributions(ledger)
    invested = kpi.invested_cost_basis(ledger)
    realized = kpi.realized_gain_total(ledger)
    taxes = kpi.taxes_total(ledger)
    dividends = kpi.dividends_total(ledger)

    positions = {isin: pos.qty for isin, pos in ledger.positions_now.items()}
    value, partial, missing, price_dates = kpi.portfolio_value(positions, ledger.cash, closes_by_isin, as_of)
    have_any_prices = bool(closes_by_isin) and any(closes_by_isin.values())

    value_kpi = None if not have_any_prices else value
    gain_eur = None if value_kpi is None else value_kpi - net_contrib

    global_start = min((d for d, _ in ledger.flows), default=as_of)

    invested_series = [
        (d.isoformat(), float(v)) for d, v in kpi.invested_capital_series(ledger.flows, global_start, as_of)
    ]

    benchmark_cfg = cfg.get("benchmark", {}) or {}
    benchmark_series: list[tuple[str, float]] | None = None
    bench_closes = closes_by_isin.get("__benchmark__")
    if benchmark_cfg.get("ticker") and ledger.flows and bench_closes:
        benchmark_series = [
            (d.isoformat(), float(v))
            for d, v in kpi.benchmark_wealth_series(ledger.flows, bench_closes, global_start, as_of)
        ]

    xirr_value = None
    twr_value = None
    mdd_value = None
    twr_start = None
    value_series: list[tuple[str, float]] | None = None
    if have_any_prices and ledger.flows:
        # XIRR convention: cash the investor pays in is negative to them;
        # the ledger stores deposits as positive (cash arriving in the
        # account), so flip the sign here. Terminal value is positive.
        xflows = [(d, -a) for d, a in ledger.flows] + [(as_of, value)]
        xirr_value = kpi.xirr(xflows)

        twr_start = kpi.coverage_start_date(ledger, closes_by_isin, global_start, as_of)
        series = kpi.daily_value_series(ledger, closes_by_isin, twr_start, as_of)
        twr_idx = kpi.twr_series(series)
        if twr_idx:
            twr_value = twr_idx[-1][1] - 1.0
            mdd_value = kpi.max_drawdown(twr_idx)
        value_series = [(d.isoformat(), float(v)) for d, v, _flow in series]

    holdings = []
    for isin, pos in sorted(ledger.positions_now.items(), key=lambda kv: kv[1].name or kv[0]):
        if pos.qty == 0:
            continue
        price_date = price_dates.get(isin)
        price = None
        if price_date and closes_by_isin.get(isin):
            price = closes_by_isin[isin].get(price_date)
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

    vap_checks = [
        {**v, "name": v["name"] or v["isin"]}
        for v in sorted(ledger.vap_checks, key=lambda x: x["date"])
    ]

    trade_buckets = build_trade_buckets(events, as_of)

    asset_class_weights: dict[str, float] = {}
    for h in holdings:
        if h["weight"] is None:
            continue
        cls = "Krypto" if h["isin"].startswith("XC") else "ETF"
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
            h["isin"]: ((cfg.get("instruments", {}) or {}).get(h["isin"], {}) or {}).get("short") or h["name"]
            for h in holdings
        },
        "asset_class_weights": asset_class_weights,
        "wealth": {
            "invested": invested_series,
            "benchmark": benchmark_series,
            "value": value_series,
            "benchmark_name": benchmark_cfg.get("name"),
        },
    }

    return {
        "as_of": as_of,
        "booking_date_range": summarize(rows),
        "source_file": source_file.name,
        "parser_version": "1.0",
        "kpis": {
            "net_contributions": net_contrib,
            "invested": invested,
            "realized": realized,
            "taxes": taxes,
            "dividends": dividends,
            "value": value_kpi,
            "value_partial": partial,
            "gain_eur": gain_eur,
            "xirr": xirr_value,
            "twr": twr_value,
            "twr_start": twr_start,
            "max_drawdown": mdd_value,
        },
        "holdings": holdings,
        "sells": sorted(ledger.sells, key=lambda s: s.date, reverse=True),
        "realized_by_isin": {isin: amt for isin, amt in ledger.realized.items() if amt != 0},
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained portfolio HTML report.")
    parser.add_argument("--file", type=Path, help="CSV file to use instead of auto-selecting the latest")
    parser.add_argument("--offline", action="store_true", help="never hit the network; cache only")
    parser.add_argument("--no-prices", action="store_true", help="skip phase-2 valuation entirely")
    parser.add_argument("--open", action="store_true", help="open the report in the default browser")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    data_dir = ROOT / "data"
    cache_path = ROOT / "cache" / "prices.json"
    out_dir = ROOT / "out"
    config_path = ROOT / "config.yaml"

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
    events = classify(rows)
    events = apply_overrides(events, cfg.get("overrides", []))

    kind_counts: dict[str, int] = {}
    for ev in events:
        kind_counts[ev.kind] = kind_counts.get(ev.kind, 0) + 1
    print("event counts:", kind_counts)

    ledger = build_ledger(events)
    print(f"cash (reconstructed): {ledger.cash}")

    as_of = max((r.booking for r in rows), default=date.today())

    price_data: tuple[dict, list[str]] = ({}, [])
    if not args.no_prices:
        instruments = cfg.get("instruments", {}) or {}
        active_isins = {isin for isin, pos in ledger.positions_now.items() if pos.qty != 0}
        start_by_isin = {}
        for isin in active_isins:
            lots_dates = [lot.date for lot in ledger.lots.get(isin, [])]
            start_by_isin[isin] = min(lots_dates) if lots_dates else as_of - timedelta(days=365)

        benchmark_cfg = cfg.get("benchmark", {})
        if benchmark_cfg.get("ticker") and ledger.flows:
            instruments = dict(instruments)
            instruments["__benchmark__"] = {"ticker": benchmark_cfg["ticker"]}
            start_by_isin["__benchmark__"] = min(d for d, _ in ledger.flows)

        closes_by_isin, warnings = fetch_all(instruments, start_by_isin, as_of, cache_path, args.offline)
        price_data = (closes_by_isin, warnings)
        for w in warnings:
            print(f"price warning: {w}")

    ctx = build_ctx(rows, events, ledger, cfg, price_data, as_of, csv_path)

    out_path = out_dir / f"report-{as_of.isoformat()}.html"
    render_report(ctx, out_path)
    print(f"report: {out_path}")

    has_dq_errors = any(d["severity"] == "error" for d in ctx["dq_items"])

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())

    return 2 if has_dq_errors else 0


if __name__ == "__main__":
    sys.exit(main())
