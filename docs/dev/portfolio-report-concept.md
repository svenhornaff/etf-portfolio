# Portfolio Report — Lean Concept

Version 1.0 · 2026-10-05 · Base currency EUR · Build: `uv` + pi, local only

## 1. Goal

One command turns the latest finanzen.net ZERO CSV export into **one self-contained HTML report**. There is no server, no database, no Docker and no network calls except optional price fetching.

```bash
uv run report.py            # latest CSV in data/ → out/report-YYYY-MM-DD.html
uv run report.py --open     # same, then open in browser
```

Design principles:

- **Recompute everything on every run.** The raw CSV is the system of record, and at roughly 300 rows a database adds nothing.
- **Decimal for money and shares.** Floats are allowed only for chart output and XIRR solving.
- **Missing is not zero.** A KPI without the inputs it needs renders as `n/a` with a reason.
- **Unknown rows are never dropped.** They appear in the report's data-quality section.

## 2. Project layout

```
portfolio-report/
├── pyproject.toml
├── report.py                 # CLI entry: orchestrates the pipeline
├── cockpit/
│   ├── load.py               # find latest CSV, read, validate header
│   ├── classify.py           # regex rules → typed events
│   ├── ledger.py             # positions, cash, cost basis (FIFO)
│   ├── prices.py             # price fetch + JSON cache (phase 2)
│   ├── kpi.py                # contributions, value, TWR, XIRR, drawdown
│   ├── goals.py              # accumulation/withdrawal defaults (JS does live calc)
│   └── render.py             # Jinja2 → single HTML
├── templates/
│   └── report.html.j2
├── static/
│   └── echarts.min.js        # vendored, inlined into output
├── config.yaml               # ticker map, benchmark, goal defaults, overrides
├── data/                     # gitignored — ZERO-kontoumsaetze-*.csv
├── cache/                    # gitignored — prices.json
├── out/                      # gitignored — generated reports
└── tests/
    ├── test_parse.py
    └── test_kpi.py
```

`.gitignore` must contain `data/`, `cache/`, `out/`. The repo holds code only, never financial data.

Dependencies: `jinja2`, `pyyaml`, `pytest` (dev). Phase 2 adds one price library, chosen after a coverage check (see §7). No pandas is needed. If you want it for resampling convenience, it is fine but optional.

## 3. Input handling (`load.py`)

### 3.1 Selecting the file

- Glob `data/ZERO-kontoumsaetze-*.csv`.
- Parse the date from the filename (`DD.MM.YYYY`; also accept `DD_MM_YYYY`) and pick the **newest by that date**, falling back to mtime if it doesn't parse.
- `--file PATH` overrides the selection.
- Print the chosen file, its row count and booking date range to stdout.

Exports are cumulative ("since account opening"), so the latest file is enough. **Option for later:** load *all* files and dedupe, in case ZERO caps the export window. Section 3.4 gives the dedupe key, so this is a small switch.

### 3.2 Format (verified on the 2026-10-05 export)

- UTF-8 **with BOM**, so read with `encoding="utf-8-sig"`.
- Delimiter `;`. Header: `Datum;Valuta;Betrag;Betrag storniert;Status;Verwendungszweck;IBAN`
- Dates are `DD.MM.YYYY`. Amounts are German format: `-1.983,30` → `Decimal("-1983.30")`.
- `IBAN` column: `INTERN` for order bookings, empty otherwise.
- Status: all rows `gebucht`. Any other status → warning in data quality.
- `Betrag storniert`: empty everywhere so far. If filled → flag the row as a reversal and do not auto-handle it.

Abort with a clear message if the header doesn't match exactly. This is how you notice a format change.

```python
def de_decimal(s: str) -> Decimal:
    return Decimal(s.strip().replace(".", "").replace(",", "."))
```

### 3.3 Row model

```python
@dataclass(frozen=True)
class Row:
    idx: int            # source row number
    booking: date       # Datum
    value: date         # Valuta
    amount: Decimal     # Betrag (signed, cash view)
    text: str           # Verwendungszweck (raw)
    iban: str
```

### 3.4 Dedupe key (only needed if multiple files are loaded)

`(booking, value, amount, normalized_text, occurrence_n)`, where `occurrence_n` counts identical rows *within one file*. Two identical rows in one file are two real bookings. Across files, take the maximum occurrence count, not the sum.

## 4. Classification (`classify.py`)

**Classify by text prefix first, then parse fields.** Never infer the event type from `STK` or from the amount sign.

Observed patterns in the current export (264 rows):

| Count | Prefix / pattern | Event | External flow? |
|---|---|---|---|
| 84 | `Order Nr <id> ISIN <isin> - Kauf (…)` | `BUY` | no |
| 72 | `Sparplan-Order zu ISIN <isin> - Kauf (…)` | `BUY` (`savings_plan=True`) | no |
| 53 | `Order Nr <id> ISIN <isin> - Verkauf (…)` | `SELL` | no |
| 29 | `Gutschrift: …` | `DEPOSIT` | **yes** |
| 3 | `Lastschrift aktiv: …` (amount > 0) | `DEPOSIT` (direct debit pull) | **yes** |
| 13 | `Steuerausgleich: …` | `TAX` (positive = refund / loss offset) | no |
| 8 | `Vorabpauschale für Fonds: …` | `TAX_VAP` | no |
| 1 | `Coupons/Dividende: …` | `DIVIDEND` | no |
| 1 | `KKT-Abschluss` | `FEE_INTEREST` (account closing) | no |
| — | anything else | `UNKNOWN` → data quality | — |

Notes from the data:

- The order ID is numeric (`259794625`) **or a UUID** (crypto orders). Use the regex `Order Nr (\S+)`.
- **Two description variants exist for orders:**
  - ETF: `(<NAME> ISIN <ISIN> STK <qty>)`, sells end in `STK <qty>    -)`.
  - Crypto/ETN (ISIN prefix `XC…`, e.g. Bitcoin): `(KRY … STK <qty> Kurs EUR <price> Provision DonauCapital EUR <x> Provision Baader EUR <y>)`. This variant carries **an explicit price and fees**; parse them into `price` and `fees`.
- Quantities are fractional with a comma (`4,539`, `0,02112535`). Parse them with `de_decimal`, keeping all digits.
- The `Vorabpauschale` text contains `STK <qty>` = the **holding at the cutoff date**, not a trade. It is useful as a free reconciliation checkpoint per ISIN (see §5.3).
- `Gutschrift`/`Lastschrift` texts contain an IBAN and a name. **Never render the raw text in the report.** Show the event type and amount only, or redact with `re.sub(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", "•••", text)`.

Classification is an ordered list of `(name, compiled_regex, handler)`, where the first match wins. Each handler returns an `Event`:

```python
@dataclass(frozen=True)
class Event:
    row: Row
    kind: str                     # BUY, SELL, DEPOSIT, WITHDRAWAL, TAX, TAX_VAP, DIVIDEND, FEE_INTEREST, UNKNOWN
    isin: str | None = None
    name: str | None = None       # short broker name, e.g. "ISHSIII-CORE MSCI WLD DLA"
    qty: Decimal | None = None    # positive; sign comes from kind
    price: Decimal | None = None  # only when stated (crypto variant)
    fees: Decimal | None = None   # only when stated
    order_id: str | None = None
    savings_plan: bool = False
```

**Withdrawals:** none in the current file. Expect the pattern for a payout to the reference account to be something like `Überweisung …` or `Auszahlung …` with a negative amount. Leave it `UNKNOWN` until seen, then add a rule.

### 4.1 Overrides (`config.yaml`)

Manual fixes, applied after classification and keyed by source fingerprint, never by row index:

```yaml
overrides:
  - match: { booking: "2026-03-31", amount: "-0.03", text_startswith: "KKT-Abschluss" }
    kind: FEE_INTEREST
    note: "Quarterly account closing"
```

## 5. Ledger (`ledger.py`)

### 5.1 Positions

Iterate events in order of `(booking, idx)`:

- `BUY` → `qty[isin] += q`; push FIFO lot `(date, q, cost=-amount)`.
- `SELL` → `qty[isin] -= q`; consume FIFO lots → realized gain = proceeds − consumed cost.
- If any position goes negative, raise a data-quality error and keep going.

**Cost includes fees implicitly**, because the debit amount is all-in. That is fine for performance work. Do not try to split fees out except in the crypto variant, where they are stated.

### 5.2 Cash

`cash = Σ amount` over all rows. The current export sums to **EUR 0.15**. This equals the true cash balance only if the export starts at account opening, which the 1 € opening debit on 2024-10-17 suggests. Show it as "reconstructed cash" and compare it to the app's displayed balance once (as a manual check in `config.yaml`).

### 5.3 Reconciliation checkpoints

- **Automatic:** every `TAX_VAP` row states `STK <qty>` per ISIN at the turn of the year. Compare it with the reconstructed `qty[isin]` at the end of the prior year. Show green/red per ISIN in data quality.
- **Manual (optional):** `config.yaml → reconcile: { date: 2026-10-05, positions: { IE00B4L5Y983: "123.456", … } }` from the depot screen.

### 5.4 Outputs

- `positions_now`: ISIN, name, qty, cost basis (FIFO remaining), avg cost
- `positions_ts`: qty per ISIN per day (for valuation)
- `realized`: per ISIN realized gain
- `flows`: list of `(date, amount)` for DEPOSIT/WITHDRAWAL only, using the **booking date** (Valuta for trades is T+2 and irrelevant for external flows)
- `cash_ts`: cash per day
- `income`: dividends, taxes (VAP and refunds) and fees by month

## 6. KPIs (`kpi.py`)

Phase 1 needs no prices:

| KPI | Definition |
|---|---|
| Net contributions | Σ DEPOSIT − Σ WITHDRAWAL |
| Invested (cost basis) | Σ FIFO remaining cost |
| Realized gain | from FIFO, per ISIN and total |
| Taxes paid/refunded | Σ TAX + TAX_VAP (signed) |
| Dividends | Σ DIVIDEND |
| Savings rate | deposits per month (bar chart) |
| Trade activity | buys/sells per month; sell count is notably high (53), so surface it |

Phase 2 requires prices:

| KPI | Definition / rule |
|---|---|
| Portfolio value | `cash + Σ qty × close`; show the price date per ISIN; if any active ISIN lacks a price → value is shown as "partial" |
| Total gain EUR | `value − net contributions` |
| XIRR | deposits negative, withdrawals positive, terminal value positive on the valuation date. Solve with Newton, falling back to bisection on [−0.99, 10]. If no sign change → `n/a`. Label it "not annualized-meaningful" if the period is under 1 year (not the case here). |
| TWR | Daily: `r_t = (V_t − F_t) / V_{t−1} − 1`, where `F_t` is the external flow on day t (end-of-day convention, documented in the report). Chain `Π(1+r_t) − 1`. |
| Max drawdown | on the TWR index, never on the raw account value |
| Volatility | `stdev(daily r) × √252` — show only with ≥ 120 daily observations |
| Benchmark wealth | simulate buying the benchmark ETF with each external flow on its date at that day's close; value it daily |

Reference tests (in `tests/test_kpi.py`):

- A deposit with no price change → TWR 0, gain 0.
- A known 2-flow XIRR case (e.g. −1000 on day 0, +1100 on day 365 → 10%).
- Buy then sell at the same price → realized 0.
- Parsing `"4,539"`, `"0,02112535"`, `"-1.983,30"`, `"10.000,00"`.
- The current file: 264 rows, Σ amount = `Decimal("0.15")`, 0 UNKNOWN.

## 7. Prices (`prices.py`, phase 2)

- `config.yaml → instruments:` maps ISIN → ticker/listing (EUR, e.g. Xetra), maintained by hand. There are about 8–10 ISINs, so do this manually.
- Fetch daily closes from the first trade date up to today and cache them in `cache/prices.json` (`{isin: {"YYYY-MM-DD": "123.45"}}`). Fetch only the missing tail.
- Only tickers leave the machine, never quantities.
- The provider is **open**. Check coverage for every ISIN in your file (including the `XC…` Bitcoin ETN and sold-out positions such as `IE00B1XNHC34`) before committing. Candidates to test: the `yfinance` package (free, Xetra `.DE` tickers, unofficial API), or a keyed API with EUR listings. Keep the provider behind one function: `get_closes(ticker, start, end) -> dict[date, Decimal]`.
- Weekends and holidays → carry forward the last close (keep its date for display).
- Price fetch failure → use the cache, mark it stale in the report, never crash.
- **No-network mode:** `--offline` uses the cache only.

Accumulating ETFs need no dividend handling. The one distributing fund (`… DLDIS`) has its dividend booked as cash, so use **unadjusted** closes and keep the cash dividend. Do not use adjusted closes, which would double count.

## 8. Report (`render.py` + `templates/report.html.j2`)

### 8.1 Rendering

- Python builds one `ctx` dict: KPIs as preformatted strings plus raw series as lists.
- Series go into the page as JSON: `<script id="data" type="application/json">{{ data_json }}</script>`. Dump it with `json.dumps(..., default=str)` and **escape `</`** (`.replace("</", "<\\/")`) to stay XSS-safe.
- Inline ECharts: `<script>{{ echarts_js | safe }}</script>`, read from `static/echarts.min.js`. No CDN, no remote fonts. The file must work offline.
- Jinja `autoescape=True`.
- Number formatting is done in Python with German locale style (`1.234,56 €`) via a small helper rather than `locale` (portable).
- The output file name is `out/report-<valuation-date>.html`, and the file is **overwritten** for the same date.

### 8.2 Page structure (single scroll, no tabs needed)

- **Header:** the as-of date (last booking date and price date), source file name, parser version, and a "Hide amounts" toggle that applies a CSS class to blur `.amt`.
- **KPI cards:** Value · Net contributions · Gain € · XIRR · TWR · Max DD. Phase 1 shows `n/a — prices not loaded` where applicable.
- **Main chart:** value vs. cumulative contributions vs. benchmark wealth (EUR), with an indexed-TWR toggle and `dataZoom`.
- **Holdings table:** ISIN, name, qty, avg cost, price/date, value, weight, unrealized €/%.
- **Allocation:** bar or treemap by instrument weight.
- **Monthly returns heatmap:** year × month, from the TWR (phase 2).
- **Cashflows:** monthly deposits, buys and sells as bars; cumulative contributions as a line.
- **Taxes & income:** VAP, Steuerausgleich and dividends by month.
- **Realized gains:** per ISIN, with the sell list.
- **Goal simulator** (JavaScript, live, no Python): sliders for monthly saving (default 700), years (10), return (4/7/10% buttons + custom), inflation (2%) and withdrawal years. Start value = current value. Output is FV nominal/real and the monthly withdrawal (annuity), using the formulas below. The label states "Illustrative, constant return, not a forecast."
- **Data quality:** UNKNOWN rows (redacted), reconciliation results (VAP checkpoints), missing/stale prices, negative positions, non-`gebucht` status.

Goal formulas (JS):

```text
i  = (1+r)^(1/12) − 1
FV = V0·(1+i)^n + S·((1+i)^n − 1)/i          (i=0 → V0 + S·n)
W  = FV·i / (1 − (1+i)^(−m))                  (i=0 → FV/m)
real r = (1+r)/(1+π) − 1
```

### 8.3 Styling

Plain CSS in the template: a CSS-variables theme with `prefers-color-scheme` dark/light and a system font stack. Gains and losses are marked by **sign and arrow**, not colour alone. It should work on mobile (stacked cards).

## 9. CLI (`report.py`)

```text
uv run report.py [--file PATH] [--offline] [--open] [--no-prices] [--verbose]
```

Pipeline: `load → classify → apply overrides → ledger → (prices) → kpi → render`. Exit codes: `0` ok, `1` header mismatch or fatal error, `2` report generated **with** data-quality errors (handy if you script it later).

Logging prints counts per event kind, UNKNOWN count, cash sum, file chosen and output path. **Never log descriptions or IBANs.**

## 10. Phasing

- **Phase 1:** load, classify, ledger, cashflow/tax/realized views, data-quality section, HTML. This works without any price source.
- **Phase 2:** price cache, valuation, holdings value, XIRR, TWR, drawdown, benchmark wealth line.
- **Phase 3 (only if wanted):** volatility, monthly heatmap polish, TER per fund from `config.yaml` (weighted TER card), rebalancing distance vs. target weights from `config.yaml`.
- **Optional automation:** a shell alias or a `launchd` agent that runs `uv run report.py --open` when a new CSV lands in `data/`. Downloading the CSV stays manual.

Explicitly out of scope: web server, database, Docker, import UI, policy score, look-through/overlap, Monte Carlo, tax reporting.

## 11. `config.yaml` skeleton

```yaml
base_currency: EUR
benchmark:
  name: "FTSE All-World (proxy ETF)"
  isin: IE00BK5BQT80          # verify; or MSCI World IE00B4L5Y983
  ticker: VWCE.DE             # verify listing
instruments:                  # fill per ISIN found in the CSV
  IE00B4L5Y983: { ticker: EUNL.DE, short: "MSCI World" }   # verify
  # …
goal:
  monthly_saving: 700
  years: 10
  inflation: 0.02
  withdraw_until_age: 85
  birth_year: null            # fill locally
reconcile:
  cash_check: { date: null, balance: null }
  positions: {}
overrides: []
```

Every ticker must be checked against your price source before use. The examples above are placeholders.

## 12. Acceptance checks

- The current CSV produces: 264 rows, 0 UNKNOWN, Σ amount = 0.15, 156 buys, 53 sells.
- No position is negative after replay.
- VAP checkpoints match the reconstructed quantities, or the mismatch is shown.
- The HTML opens offline (Wi-Fi off) with working charts.
- `grep -E 'DE[0-9]{20}' out/*.html` finds nothing.
- `--offline` with an empty cache still renders phase-1 content and shows `n/a` for valuation KPIs.