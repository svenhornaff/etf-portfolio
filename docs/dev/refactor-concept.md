# etf-portfolio — Refactor Concept v2

Base: commit `8314fa7` · 2026-10-05 · Scope: correctness fixes, full-period valuation, report redesign. No new runtime dependencies beyond an optional vendored chart lib.

## 0. Problems being solved

| # | Problem | Effect |
|---|---|---|
| P1 | Only 4 of 34 traded ISINs have tickers; `coverage_start_date` starts TWR the day after the last unpriced holding | TWR/Max DD cover 10 days (from 2026-09-23); the cards are meaningless |
| P2 | Price fetch start = earliest *remaining FIFO lot*, not first holding day | Held-since-2024 positions get prices only from 2026-08 |
| P3 | `value_partial` is computed but ignored | Value, gain €, XIRR and "Perf. auf Einlage" render as complete when they aren't |
| P4 | `as_of` = last booking date | Valuation ignores the latest prices; the file name doesn't match the export date |
| P5 | Benchmark line starts 2024, value line 2026-09 | The hero chart compares unlike things |
| P6 | Report is a data dump (53-row sell list, 34-row realized table with ISINs only), monthly charts without axes, mixed DE/EN, no dark mode, KPIs without period | Hard to read; key messages are buried |
| P7 | Missing KPIs: monthly returns, best/worst month, volatility, concentration | Concept §6 phase 2/3 incomplete |

## 1. Dates (P4)

Introduce two dates in `report.py`:

- `booking_as_of = max(row.booking)`: the ledger state and "Daten bis" label.
- `valuation_date = date.today()` (overridable with `--as-of YYYY-MM-DD`): used for prices, value, XIRR terminal flow and the end of the series.

Ledger quantities are unchanged between `booking_as_of` and `valuation_date`. Output: `out/report-<valuation_date>.html`. The header shows both dates plus the latest price date.

## 2. Instrument master and price coverage (P1, P2)

### 2.1 `config.yaml → instruments`

Extend the format to all ISINs ever traded:

```yaml
instruments:
  IE000I8KRLL9: { ticker: SEC0.DE, short: "MSCI Global Semiconductors", class: ETF }
  US67066G1040: { ticker: NVD.DE,  short: "NVIDIA",                     class: Aktie }
  XC000A2YY6Q6: { ticker: null,    short: "Bitcoin (ZERO Krypto)",     class: Krypto }
  # ticker: null  → no market data, use implied prices (§2.3)
```

`class` drives the asset-class donut, replacing the `isin.startswith("XC")` heuristic.

### 2.2 New CLI helper: `uv run etf-portfolio --list-instruments`

Prints every ISIN in the ledger with name, first/last holding date, current qty, configured ticker, cached price range, and a `MISSING` flag. Exit 0. This makes filling `config.yaml` a 10-minute chore.

### 2.3 Price resolution order (new `prices.resolve_closes`)

For each ISIN, over `[first_held, last_held or valuation_date]`:

1. **Market:** the Yahoo close (cache-first, fetch only the missing tail).
2. **Implied:** `|amount| / qty` from the ISIN's own BUY/SELL rows, linearly interpolated between trade dates, flat after the last trade. Only valid inside the holding period. Tag it `source="implied"`.
3. **None:** the position is unpriced on that day.

Returns `PriceSeries(closes: dict[date, Decimal], source: dict[date, Literal["market","implied"]])`.

Implied prices include trading fees. That is acceptable here: it slightly understates intra-holding returns and is flagged in the report.

### 2.4 Fetch window (P2)

`start_by_isin[isin] = first date the ISIN appears in ledger.qty_history`, not the FIFO remaining lots. Sold-out ISINs with a ticker are fetched over `[first_held, last_held + 5d]` only.

### 2.5 Coverage model (replaces `coverage_start_date`)

`kpi.coverage(series_by_isin, qty_history, start, end) -> Coverage`

```python
@dataclass
class Coverage:
    start: date                 # first day every held ISIN has market or implied price
    implied_share: float        # Σ(implied-priced value-days) / Σ(all value-days)
    unpriced_isins: list[str]   # held at some point with neither source
    estimated: bool             # implied_share > 0
```

- If `unpriced_isins` is non-empty → fall back to the current behaviour (start after the last unpriced day) and show a warning.
- If `implied_share > 0` → show TWR/DD with an "≈" prefix and the badge "geschätzt (x % implizite Kurse)".

## 3. Valuation and KPI guards (P3)

New `kpi.Valuation` dataclass returned by `portfolio_value`:

```python
@dataclass
class Valuation:
    value: Decimal | None       # None if any held ISIN is unpriced
    priced_value: Decimal       # sum of what *is* priced (for the warning text)
    missing: list[str]
    price_dates: dict[str, date]
    stale: list[str]            # price older than valuation_date - 3 business days
```

Rules:

- `value is None` → Value, Gain €, XIRR and Performance auf Einlage are all `n/a`, with the reason "Kurs fehlt: <names>".
- `stale` non-empty → show a value with a warning badge and the stale price date.
- Remove `perf_on_deposit` from the hero. Keep it in the stat list, renamed "Gewinn ÷ Einzahlungen".

## 4. KPI set (P7)

All in `kpi.py`. Every KPI returns `Kpi(label, value, period, reason, estimated)`, so the template never formats raw numbers.

| KPI | Definition | Period label |
|---|---|---|
| Depotwert | §3 | Kursdatum |
| Gewinn € | value − net contributions | seit Start |
| XIRR | as now, terminal = value @ valuation_date | seit Start |
| TWR gesamt | chained daily index over coverage | "seit dd.mm.yyyy" |
| TWR p.a. | `index^(365/days) − 1`, only if days ≥ 365 | — |
| TWR YTD | index[valuation]/index[31.12. prior] − 1 | YTD |
| Max Drawdown | on TWR index | coverage period |
| Akt. Drawdown | last / running peak − 1 | — |
| Volatilität p.a. | stdev of daily r on business days × √252, only if ≥ 120 obs | coverage period |
| Bester / schlechtester Monat | from monthly returns | — |
| Monatsrenditen | month-end index / previous month-end − 1; partial months flagged | grid |
| Benchmark TWR | same window as portfolio TWR | same |
| Benchmark-Vermögen | as now, but chart it from `coverage.start` with the portfolio value rebased (see §5) | — |
| Größte Position | max weight | today |
| Top-3 Anteil | Σ top 3 weights | today |
| Anzahl Instrumente | held / ever traded | — |
| Umschlag (Turnover) | Σ sell proceeds (12M) ÷ avg value (12M) | 12M |
| Realisiert | FIFO, as now | seit Start |
| Steuern netto | Σ TAX + TAX_VAP, label "+ = Erstattung" | seit Start |
| Dividenden | as now | seit Start |

Monthly returns use calendar month-ends, carry the last close forward, and are only computed where coverage exists.

## 5. Chart data (P5)

`chart_data.wealth` gets one common x-range: `[coverage.start, valuation_date]`, with a toggle "ab Start (nur Einzahlungen)" to show the contributions line alone from the first deposit.

Two chart modes, toggled in the hero:

- **Vermögen €:** depot value, cumulative contributions, and benchmark wealth (flows from `coverage.start`, seeded with the portfolio value at `coverage.start`).
- **Index (=100):** portfolio TWR vs benchmark TWR, both rebased to 100 at `coverage.start`.

Implied-priced stretches are drawn dashed: emit `estimated: [[start,end],…]` ranges per series.

## 6. Report redesign (P6)

### 6.1 Principles

- German only (labels, months, number format). Code and comments stay English.
- One question per section; the most important at the top.
- Long lists collapsed by default (`<details>`).
- Every number carries period and source; estimated numbers carry "≈".
- Light/dark via the existing CSS variables plus `prefers-color-scheme` and a manual toggle.
- Sign and arrow for gains/losses, never colour alone.
- Print stylesheet: expand all `<details>`, hide toggles and sliders.

### 6.2 Page order

**Kopf**
- Title, "Daten bis {booking_as_of} · Kurse vom {price_date} · Quelle {file}"
- Toggles: Beträge verbergen · Hell/Dunkel

**① Hero (2/3 + 1/3)**
- Left: Depotwert (big), Gewinn € and XIRR beneath it; chart with the €/Index toggle and range tabs 3M/1J/YTD/MAX.
- Right: "Auf einen Blick" stat list (Einzahlungen, Depot, Cash, Realisiert, Unrealisiert, Steuern netto, Dividenden).

**② KPI-Leiste (6 cards)**
- TWR · TWR vs Benchmark (Δ pp) · Max DD · Volatilität · Größte Position · Turnover 12M
- Each card has the value, a period line, and either an "≈" badge or a reason for `n/a`.

**③ Positionen (2/3 + 1/3)**
- Holdings table: short name (ISIN on hover), qty, Ø Kosten, Kurs + Datum, Wert, Gewicht (inline bar), unrealisiert €/%. Sortable by clicking headers.
- Allocation donut: Positionen / Assetklasse tabs.

**④ Monatsrenditen**
- Heatmap of years × months plus a year column; a diverging scale centred on 0; partial months hatched.
- A best/worst month line below it.

**⑤ Cashflows & Steuern (3 small multiples, shared month axis)**
- Einzahlungen/Monat, Käufe vs Verkäufe €/Monat (diverging bars), Steuern & Erträge/Monat.
- Y-axis with 3 ticks, value tooltips. Outliers above 3× the median are capped with a "▲ 10.000 €" label.

**⑥ Trading**
- Trade counters (keep the current 1W/1M/1J/Gesamt table).
- Realisiert je Instrument: horizontal bar chart sorted by €, names not ISINs; the top 5 and bottom 5 visible, the rest in `<details>`.
- Verkaufsliste: inside `<details>`, collapsed, with a filter input.

**⑦ Ziel-Simulator** (keep, restyled)
- Default the start value to the current depot value; add 4/7/10 % preset buttons next to the slider.
- Add a small projection line chart (nominal vs real).

**⑧ Abgleich & Datenqualität** (collapsed if there are no errors)
- Vorabpauschale checkpoints, price coverage per ISIN (market / implied / none, date range), warnings.

**Fuß:** Konventionen (TWR end-of-day flows, FIFO, implied prices incl. fees), parser version, generated-at timestamp.

### 6.3 Charts

Option A (recommended): **vendor ECharts** (`static/echarts.min.js`, inlined at render, ~1 MB). It gives axes, tooltips, dataZoom, heatmap and dark theme for free and removes about 250 lines of custom SVG JavaScript.

Option B: keep the hand-rolled SVG but extract it into `static/charts.js` (also inlined), with shared `axis()`, `tooltip()`, `line()`, `bars()` and `heatmap()` helpers.

Whichever you choose, chart JavaScript lives in `static/`, never inline in the template, and the template only holds containers plus the `report-data` JSON.

### 6.4 Template structure

```
templates/
  report.html.j2          # skeleton, includes + blocks
  _macros.html.j2         # kpi_card(k), money(v), pct(v), badge(...), details(...)
  sections/
    hero.html.j2
    kpis.html.j2
    holdings.html.j2
    monthly.html.j2
    cashflows.html.j2
    trading.html.j2
    goals.html.j2
    quality.html.j2
static/
  report.css              # inlined at render
  charts.js | echarts.min.js
  report.js               # wiring: toggles, tabs, sorting, goal sim
```

`render.py` reads `static/*` and passes `inline_css` and `inline_js` into the context. Output remains a single file.

## 7. Code structure changes

| File | Change |
|---|---|
| `report.py` | Shrink to CLI + orchestration (~80 lines). Move `build_ctx` to `context.py` and `build_trade_buckets` to `kpi.py`. |
| `context.py` (new) | `build_context(...) -> ReportContext` — typed dataclasses, not loose dicts |
| `kpi.py` | Add `Valuation`, `Coverage`, monthly returns, volatility, concentration and turnover; KPIs return `Kpi` objects |
| `prices.py` | Add `resolve_closes` (market → implied), `PriceSeries`, and the stale check; replace `datetime.utcfromtimestamp` (deprecated) with `datetime.fromtimestamp(ts, UTC)` |
| `ledger.py` | Expose `holding_periods: dict[isin, list[(start, end)]]`; quantize FIFO split cost to 0.01 € at the realized boundary to kill the `…99999998` noise |
| `classify.py` | Unchanged |
| `render.py` | Inline static assets; add filters `eur_short` (12,3 T€) and `date_de` |
| `config.yaml` | Add all instruments with `class`; `benchmark.isin` verified |

## 8. Tests to add

- `prices.resolve_closes`: implied interpolation between two trades and flat after the last; market overrides implied; nothing outside the holding period.
- Coverage: one unpriced ISIN → start after its last held day; all implied → `estimated=True`.
- Valuation guard: one missing price → `value is None` and XIRR `n/a`.
- Monthly returns: synthetic index +1 %/month → 12 × 1 %.
- TWR invariance: deposit days produce no return; buy/sell at the close price produces no return.
- Volatility suppressed below 120 observations.
- Snapshot smoke test: render with a fixture CSV and cache; assert no `DE\d{20}` in the HTML, and that the `n/a` reasons are present when the cache is empty.

## 9. Delivery order

- **Step A — correctness:** §1 dates, §2.4 fetch window, §3 guards, tests. Small diff; it makes the current report honest.
- **Step B — coverage:** §2.1–2.3, §2.5, `--list-instruments`, fill `config.yaml`. This turns the TWR into a 2-year figure.
- **Step C — KPIs:** §4 and the §5 chart data.
- **Step D — redesign:** §6 template split, CSS/JavaScript extraction, charts.
- **Step E — cleanup:** §7 module moves, README entry point fix (`uv run etf-portfolio`).

## 10. Acceptance

- With all tickers configured: TWR start ≤ 2024-10-17, `implied_share` shown, and no KPI renders without a period.
- Removing one ticker from the config → value/XIRR `n/a` with the instrument named, and the TWR start moves.
- The 1280 px page fits the hero, KPI strip and holdings in about the first two screens; the sell list is collapsed.
- Dark mode is readable, print expands everything, and the page works offline.
- `grep -E 'DE[0-9]{20}' out/*.html` is empty.
