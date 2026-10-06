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
uv run etf-portfolio                      # latest CSV -> out/report-<valuation_date>.html
uv run python -m etf_portfolio            # equivalent, module form
uv run etf-portfolio --open               # same, then open in the browser
uv run etf-portfolio --offline            # no network; cached prices only
uv run etf-portfolio --no-prices          # skip valuation entirely
uv run etf-portfolio --as-of 2026-01-01   # value as of a specific date (>= last booking date)
uv run etf-portfolio --list-instruments   # every ISIN ever traded + ticker/coverage status
```

Exit codes: `0` ok, `1` fatal/bad input, `2` report generated with
data-quality findings.

`booking_as_of` (the ledger's last transaction date) and `valuation_date`
(default: today, or `--as-of`) are tracked separately and both shown in
the report header — quantities are fixed by the ledger, prices move with
`valuation_date`.

## Configure

`config.yaml` can map any ISIN to a ticker (Yahoo Finance `.DE` style)
plus a display name and asset class. You do **not** need a ticker for
every ISIN you've ever traded: an ISIN without one gets an "implied"
price derived from its own buy/sell amounts (linearly interpolated
between trades, flat after the last one), clearly marked `≈` wherever it
feeds a KPI or chart. `uv run etf-portfolio --list-instruments` lists
every ISIN ever traded with its current ticker/coverage status, to make
filling in real tickers for currently-held positions a quick pass. A
held ISIN with *no* price at all (neither market nor implied) makes
the portfolio value `n/a`, with the ISIN named — never a silent partial
sum.

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
