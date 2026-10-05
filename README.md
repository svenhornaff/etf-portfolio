# etf-portfolio

One command turns the latest finanzen.net ZERO CSV export into one
self-contained HTML report. No server, no database, no Docker, and no
network calls except optional price fetching. See
`docs/dev/portfolio-report-concept.md` for the full design.

## Setup

```bash
uv sync
```

Drop the latest `ZERO-kontoumsaetze-DD.MM.YYYY.csv` export into `data/`
(gitignored — the repo holds code only, never financial data).

## Run

```bash
uv run report.py            # latest CSV in data/ -> out/report-YYYY-MM-DD.html
uv run report.py --open     # same, then open in the browser
uv run report.py --offline  # no network; cached prices only (n/a if none cached)
uv run report.py --no-prices # phase-1 only, skip valuation entirely
```

Exit codes: `0` ok, `1` fatal/header mismatch, `2` report generated with
data-quality findings.

## Configure

`config.yaml` maps each ISIN you hold to a price ticker (Yahoo Finance
`.DE` style) so phase-2 valuation (value, XIRR, TWR, drawdown) can run.
ISINs without a ticker — including fully sold-out historical positions —
simply render as `n/a`; TWR/drawdown start from the first day price
coverage is complete for everything held at that point, and the report
says so.

## Tests

```bash
uv run pytest
```

## Layout

```
etf_portfolio/
  report.py                  # CLI entry (argparse, main())
  __main__.py                # enables `python -m etf_portfolio`
  load.py                    # CSV parsing + validation
  classify.py                # regex rules -> typed events
  ledger.py                  # FIFO positions, cash, realized gains
  prices.py                  # Yahoo Finance fetch + JSON cache, offline-safe
  kpi.py                     # contributions, value, XIRR, TWR, drawdown
  render.py                  # Jinja2 -> one HTML file
templates/report.html.j2     # single-page report, vanilla-JS charts (no CDN)
config.yaml                  # tickers, benchmark, goal defaults, overrides
data/ cache/ out/            # gitignored
```

Flat layout, not `src/etf_portfolio/`: this is an application, never
published or pip-installed by anyone else, so the packaging-guide case for
`src/`-layout (isolating tests from an accidentally-importable source tree)
doesn't apply. See `[tool.uv.build-backend] module-root = ""` in
`pyproject.toml`, which is what tells `uv_build` to use a flat layout.
