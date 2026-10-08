# ETF Portfolio Report v4 — Concept

Base: `develop` @ `c2397cf` and `report-2026-10-07.html` · 2026-10-07 · Language: **English throughout**, glossary at the end

---

## 0. Decision: finish v3 first, or go straight to v4?

> **Status 2026-10-08 (verified against `develop` @ `3ac06b3`): Step 0 is done; v4 Steps 1–7 haven't started.**
> 41 tests pass (42 with the patch below).
> - **VUAA start date: fixed.** A one-way implied→market switch at the ticker's first quote; `twr_start` is back to 2024-10-16 and implied share is 15 %. The logic is correct and regression-tested (leading edge and mid-span gap).
> - **08.06.2026 jump: explanation corrected, and the ledger supports it.** The 2,102-share sell of the semis ETF executed at **€16.02**, while the 0.701-share fractional sell *on the same day* got **€17.30**. The buy-backs on the next two days paid €18.53 and €18.10. So the whole-share order really did execute far below the close; it isn't a pricing bug. To confirm the execution time, check the contract note (Wertpapierabrechnung).
> - **Bug in the new `execution_vs_close()`: patched (see `fix-execution-vs-close.patch`).** It iterated `ledger.lots`, which only holds *still-open* FIFO lots, so any buy that was later sold was never checked. The commit message's "NVIDIA buy within 1 %" could not have come from this function, because the NVIDIA lot was closed on 11.06. The patch iterates trade events instead, ranks divergences by € impact, and adds a regression test.
> - **New finding for v4 (§6.1, §7 ⑥): round trips.** Selling a fund and buying the same fund back within 30 days happened 6 times, costing **≈ €3,330 in net re-entry cost**. €3,400 of it comes from the June 2026 semis episode alone; the 3-day NVIDIA trade (bought 08.06., sold 11.06.) lost another €1,008. This is the most concrete "cost of trading" figure the report can show. **Not built this session — see §12.**
>
> **Status 2026-10-08, later the same day: §4's fund factsheet data is now sourced and live in `instruments.yaml`** (not built: Steps 1/3/4/5/6/7, policy.py, observations.py, trading.py, models.py, fundcard.py, English rewrite — see §12). Per user decision: fund data first, English later; `role` (core/satellite) and `policy.py` explicitly skipped this session (investment-policy decisions, not factsheet lookups). Verified live, not re-typed from §4's own research: `IE00BF4RFH31` (iShares MSCI World Small Cap) TER 0.35 %, AUM $8,824,872,815, 3,583 holdings, launched 27 Mar 2018 — all confirmed directly from iShares' own product page, dated 07 Oct 2026. `FR001400YYJ0`'s feeder structure (≥85 % into Amundi MSCI World UCITS ETF `IE000BI8OT95`) is now **confirmed** via finanzen.net's own fund page, not just flagged as unconfirmed per §4.1. `IE000N7LUK04` (Boreas)'s regions/sectors/top_holdings are confirmed **not independently obtainable with real weights** — the issuer's own site is JS-rendered, and third-party sources (trackinsight) list holding *names* (IonQ, Rigetti, D-Wave, IBM, Baidu, Intel, Alphabet, Microsoft, Tencent) but no verified weights, so nothing was fabricated; TER/AUM/launch/domicile/replication for it are sourced instead (justETF + Lunate's own 2-pager + a PRIIPs KID). Effect: weighted TER now computes for real (0.27 % p.a., ≈€107.85/year, amber vs. the 0.25 % target) instead of showing n/a; region/sector/currency look-through and the overlap matrix still show n/a overall (all-or-nothing across the 4 held ISINs), but the reason now names only Boreas, not all 4 — confirmed via a new regression test (`tests/test_lookthrough.py`) proving the reason narrows correctly. 48/48 tests pass.

**Close the two v3 correctness items first. Skip the v3 readability list and fold it into v4.**

| v3 remainder | Do it now? | Why |
|---|---|---|
| VUAA.DE history starts 30.12.2024, so TWR now starts on 30.12.2024 instead of 16.10.2024 | **Yes** | Every performance number and chart in v4 depends on it. Your current report already shows "Performance line since 2024-12-30". |
| 08.06.2026 −6.6 % jump ("rebalancing") | **Yes** | Swapping one holding for another can't move TWR. Either the closes don't match the execution prices or the dates are wrong. Check it before any chart is polished. |
| §12a readability punch list (German strings, ISO dates, n/a cards, label clipping) | **No** | v4 rewrites every label in English and replaces the n/a cards with real look-through data. Polishing them now is wasted work. |
| Step D (model portfolios), Step E (market context) | Merge into v4 | They become v4 sections §5.7 and §5.8. |

Two fixes are needed before any v4 work:

- **VUAA start date.** Try an EUR listing with longer history (`VUAA.MI`, `VUAA.AS`), checking currency, exchange and first date. Otherwise allow exactly one source switch per holding period: implied prices *before* the first market quote, market prices after it.
- **08.06.2026 jump.** Compare the closes in `cache/prices.json` for SEC0.DE, NVD.DE and IUSN.DE on 2026-06-08 with the prices actually paid and received: ≈ 16.01 €, ≈ 180.98 € and ≈ 8.815 €. If they are close, it's a date or listing bug. If they're far apart, label it "execution price differs from close by X %".

---

## 1. Investment policy encoded in the report

You stated the policy: **ETFs only for now, maybe single stocks later, no other structured products.** Make it explicit in config so the report checks against it instead of guessing:

```yaml
policy:
  allowed_classes: [ETF, Stock]        # Stock allowed, currently unused
  forbidden_classes: [ETP, ETN, ETC, Certificate, Crypto, Leveraged, Inverse]
  max_single_stock_weight: 0.05        # for later
  max_fund_weight: 0.35
  min_fund_aum_eur: 100_000_000        # closure-risk guard
  domicile_preferred: [IE]
  replication_preferred: [physical]
```

What the report does with it:

- A **Policy compliance** health light. Today it's green: all four holdings are UCITS ETFs.
- Historical positions outside the policy are listed once in the appendix as "outside current policy (historical)": the Bitcoin product (XC000A2YY6Q6), the Bitwise ETP (DE000A3G3ZL3) and NVIDIA (a single stock, allowed under the future rule).
- `instruments.yaml → class` becomes a controlled vocabulary (`ETF | Stock | ETP | ETN | ETC | Crypto`) and is validated at load.

---

## 2. Language and format

- All UI text, narrative, KPI labels, notes and data-quality messages are in **English**.
- Number format: English (`€40,825.18`, `19.6 %`, `+5.6 pp`), with dates as `7 Oct 2026` in prose and `2026-10-07` in tables. Set it once in `render.py` with `locale: en-GB` in config, so switching later is a single change.
- Keep German *source* terms where they are legal or tax terms, explained in the glossary: Vorabpauschale, Teilfreistellung, Steuerausgleich.
- **No developer text** in the body: no file names, no `§` references, no raw ISIN lists. Those go to the appendix's *Data quality* page.

---

## 3. Better data: where to get it

Four kinds of data are needed. The table lists sources by recommendation; everything marked "free" was checked on 2026-10-07.

| Data | Recommended source | Access | Notes |
|---|---|---|---|
| **Daily prices** | Yahoo Finance chart API (already used) | free, unofficial, no key | Fine for one person. Keep the cache. Fallback if it breaks: EODHD or FMP end-of-day (paid), behind the same `get_closes()` function. |
| **ISIN → ticker / listing** | **OpenFIGI** mapping API | free; 25 requests/min without key, 25 per 6 s with key | Returns ticker, exchange code, security type for an ISIN. Use it to auto-propose tickers, then check currency and exchange with a Yahoo `meta` call as you did by hand. |
| **Fund master data** (TER, AUM, launch, replication, domicile, distribution) | Issuer factsheet PDF + KID (PRIIPs) | free, monthly | Authoritative. justETF, extraETF and Finanzfluss are good for cross-checking but have no API (scraping is fragile and against their terms). |
| **Holdings** (security, weight, country, sector) | **Issuer holdings files**: iShares product page "Detailed holdings" CSV; Amundi product page Excel | free, daily or monthly | iShares exposes a CSV download per fund (`…/<productId>/<slug>/1506575576011.ajax?fileType=csv&fileName=<TICKER>_holdings&dataType=fund`; pattern known since 2018, verify per fund). Amundi publishes an Excel file. Boreas/Lunate publishes holdings only on its own site, which blocks automated access. |
| **Index reference** (MSCI World top 10, sector/country) | MSCI index factsheets (monthly PDF) | free | Useful for the benchmark and as a cross-check for feeder funds. |
| **FX and risk-free rate** | ECB Data Portal API (EUR/USD, €STR) | free, no key | Replaces the fixed 2 % in `risk_free`; enables "USD exposure in €" figures. |
| All-in-one (optional) | EODHD fundamentals, Financial Modeling Prep | paid | Both advertise ETF holdings and sector/country weights. Check **UCITS coverage for your exact ISINs** on a trial before paying. |

### Recommended data architecture (still no database)

```
reference/
  instruments.yaml                 # master data: ticker, class, role, ter, aum, launch, domicile, replication, index, source, as_of
  holdings/
    IE000I8KRLL9/2026-08-31.csv    # normalized: name, isin, ticker, weight, country, sector, currency
    FR001400YYJ0/2026-08-31.csv
    IE00BF4RFH31/2026-08-31.csv
    IE000N7LUK04/2026-08-31.csv    # manual until a source is found
cache/prices.json  cache/fx.json  cache/estr.json
```

New CLI commands:

- `etf-portfolio refresh-holdings`: downloads iShares CSVs and parses Amundi Excel into the normalized CSV, saved under the as-of date. If a download fails, it keeps the last file and flags it as stale.
- `etf-portfolio resolve-tickers`: OpenFIGI first, then a Yahoo meta check (currency, exchange, first date), and **prints proposals**. Writing to `instruments.yaml` stays a manual step.
- `etf-portfolio refresh-master`: prints which `instruments.yaml` fields are older than 35 days.

**Join key for overlap:** ISIN where the file provides it, else ticker plus exchange, else a normalized name ("NVIDIA CORP" → "NVIDIA"). Report the match rate per fund in data quality.

---

## 4. Research findings: your four ETFs

Data as of the dates shown, taken from issuer factsheets and fund portals. Traffic lights use **your 16-KPI ETF standard**; n/a means the data isn't available yet.

### 4.1 finanzen.net MSCI World UCITS ETF — FR001400YYJ0 · 41.5 % of portfolio

> **Verified 2026-10-08**: the feeder structure below is confirmed, not just flagged, via finanzen.net's own fund page — see `instruments.yaml`'s `feeder_of: IE000BI8OT95` entry.

| KPI | Value | Rating |
|---|---|---|
| Issuer / structure | Amundi; French FCP. **Confirmed 2026-10-08** (was "check the prospectus"): this is a feeder, investing ≥85 % into **Amundi MSCI World UCITS ETF (IE000BI8OT95)**, confirmed via finanzen.net's own fund page. | 🟡 |
| Index | MSCI World NR (≈1,300 large and mid caps, 23 developed markets) | 🟢 |
| TER | 0.12 % (extraETF) vs 0.14 % (Finanzfluss). Take the KID's "ongoing costs" as the truth, including any underlying-fund cost. | 🟢 |
| AUM | **≈ €45–47 M** | 🔴 under €100 M |
| Launch | 3 Sep 2025 (≈ 13 months) | 🟡 short track record |
| Replication | Physical | 🟢 |
| Domicile | **France**, outside your Irish-only standard. Check withholding-tax efficiency on US dividends against an Irish equivalent. | 🟡 |
| Distribution | Accumulating | 🟢 |
| Look-through | US 72.8 %, Japan 5.9 %, UK 3.3 %, Canada 3.3 %, CH 2.5 %; semiconductors 14.9 % as the largest industry | — |

Your largest position is a small (<€50 M), one-year-old, broker-branded fund. Its *exposure* is plain MSCI World. Its *vehicle* risk (closure or merger, which forces a taxable sale in Germany; spreads; tracking) is higher than a multi-billion MSCI World ETF. The report should show this in the fund card, not hide it in the TER.

### 4.2 iShares MSCI Global Semiconductors UCITS ETF — IE000I8KRLL9 · 24.8 %

| KPI | Value | Rating |
|---|---|---|
| Index | MSCI ACWI IMI Semiconductors & Semiconductor Equipment ESG Screened Capped (developed and emerging) | — |
| TER | 0.35 % | 🟡 |
| AUM | ≈ USD 5.9 bn | 🟢 |
| Launch | 5 Aug 2021 | 🟢 |
| Holdings | 258 | — |
| Top-10 concentration | ≈ 60 % | 🔴 |
| Top 10 (31 Aug 2026) | Micron 9.56 · AMD 8.05 · TSMC 7.88 · NVIDIA 7.06 · Broadcom 6.43 · ASML 5.15 · Lam Research 4.64 · Applied Materials 4.51 · SK Hynix 4.47 · Intel 3.47 | — |
| Countries | US 64.1 % · Taiwan 14.8 % · Japan 7.4 % · Korea 5.5 % · Netherlands 4.9 % | — |
| Calendar years (USD) | 2022 −34.8 % · 2023 +64.1 % · 2024 +13.8 % · 2025 +53.5 % | 🔴 volatility |
| Replication / domicile / distribution | Physical · IE · Acc | 🟢 |

### 4.3 iShares MSCI World Small Cap UCITS ETF — IE00BF4RFH31 · 24.6 %

> **Verified 2026-10-08, live from iShares' own product page** (not re-typed from this table): TER 0.35 %, AUM USD 8,824,872,815, launch 27 Mar 2018, 3,583 holdings, domicile Ireland, physical/optimised-sampling, dated 07 Oct 2026.

| KPI | Value | Rating |
|---|---|---|
| TER | 0.35 % | 🟡 |
| AUM | **USD 8,824,872,815** (07 Oct 2026, confirmed live) | 🟢 |
| Launch | 27 Mar 2018 | 🟢 |
| Holdings | **3,583** (07 Oct 2026, confirmed live) | 🟢 |
| Top-10 concentration | ≈ 4.4 %; largest is SanDisk at 1.94 %, which is unusually large for a small-cap index | 🟢 |
| Sectors | Industrials 19.3 · Financials 14.2 · IT 13.7 · Health Care 11.3 · Consumer Discretionary 10.1 | — |
| Calendar years | 2019 +25.7 · 2020 +15.8 · 2021 +15.8 · 2022 −18.6 · 2023 +16.0 · 2024 +7.9 · 2025 +19.8 | 🟡 |
| Replication | Optimised sampling (physical) | 🟢 |
| Domicile / distribution | IE · Acc | 🟢 |

### 4.4 Boreas Solactive Quantum Computing UCITS ETF — IE000N7LUK04 · 9.1 %

> **Verified 2026-10-08**: TER/AUM/launch/domicile/replication confirmed (justETF + Lunate's own `quantm-2pager.pdf` + a fundinfo PRIIPs KID). Holdings data confirmed **not obtainable with real weights**, not just "not tried": the issuer's own site (etfs.lunate.com) is JS-rendered; third-party sources (trackinsight) list holding *names* (IonQ, Rigetti Computing, D-Wave Quantum, IBM, Baidu, Intel, Alphabet, Microsoft, Tencent) but no verified weights — a name list isn't enough to build a `top_holdings` dict without guessing numbers, so `instruments.yaml` leaves it unset rather than fabricating one.

| KPI | Value | Rating |
|---|---|---|
| Issuer | Lunate Capital (Abu Dhabi); first launched on the Abu Dhabi exchange in 2025, with the UCITS share class following | 🟡 |
| Index | Solactive Developed Quantum Computing: 25 US/European-listed companies, rank-weighted by Solactive's NLP relevance score (ARTIS) | 🟡 |
| TER | 0.49 % | 🟡 |
| AUM | **≈ €4 M** | 🔴 very high closure risk |
| Launch | 23 Feb 2026 | 🔴 |
| Max drawdown since launch | −35.9 % | 🔴 |
| Holdings data | **Confirmed not obtainable automatically** (issuer site is JS-rendered; third-party sources give names, not weights) — manual monthly entry needed if ever done | 🔴 data |
| Replication / domicile / distribution | Physical · IE · Acc | 🟢 |

---

## 5. Overlap and look-through: what you actually own

These estimates combine your weights from the 2026-10-07 report with the funds' top-10 data. Weights for names outside a fund's top 10 (e.g. AMD and ASML in MSCI World) are approximated at about 0.5 %. v4 computes all of this from the full holdings files.

### 5.1 Pairwise overlap (Σ min weight, holdings-weighted)

| | World | Semis | Small Cap | Quantum |
|---|---|---|---|---|
| **World** | — | **≈ 12–15 %** | ≈ 0 % (by construction) | n/a (holdings unknown) |
| **Semis** | | — | < 1 % | n/a |
| **Small Cap** | | | — | n/a |

**World + Small Cap don't overlap and complement each other:** together they approximate MSCI World IMI. Your mix is about 63/37 large to small; the market itself is about 85/15.

### 5.2 Look-through exposures (whole portfolio)

| Exposure | Estimate | Comment |
|---|---|---|
| **Semiconductor industry** | **≈ 31 %** | 24.5 % via the semis ETF, 6.2 % via MSCI World, a little via small caps, plus an unknown amount via quantum |
| NVIDIA | ≈ 4.0 % | 2.3 % via World + 1.8 % via Semis |
| Micron | ≈ 2.8 % (+0.5 % SanDisk) | Memory cycle concentration |
| Broadcom | ≈ 2.3 % | |
| AMD | ≈ 2.2 % | |
| TSMC | ≈ 2.0 % | Only via Semis (Taiwan isn't in MSCI World) |
| United States | ≈ 69 % | |
| Emerging markets | ≈ 5 % | Only Taiwan, Korea and China via Semis; **no broad EM allocation** |
| USD-denominated assets | ≈ 70 % | Currency risk vs. a EUR base; all four funds are unhedged |

The headline v4 should state: **"About one euro in three is in semiconductors."** Combined with the July 2026 −17.8 % month and the semis outflows reported on 5 Oct 2026, this is the single most decision-relevant fact in the report.

### 5.3 Visuals for the "Portfolio X-ray" section

- **Top-15 underlying companies.** Horizontal bars, each one stacked by the fund it comes from. This is the clearest overlap visual available.
- **Fund × fund overlap heatmap**, using the pairwise numbers above.
- **Sector, country and currency.** 100 % bars for the portfolio versus the benchmark.
- **Theme exposure gauges.** Semiconductors, AI and quantum against configurable caps from your policy.

---

## 6. "ETF or market recommendations": what the report may and may not say

A static report generated from your own data should show **observations against your own rules**, plus **neutral reference information**. It shouldn't produce buy/sell calls: those would be rules nobody can audit, and they would read as personal financial advice. I'm not a financial advisor. What follows is the design for the section, not advice on what to do.

### 6.1 "Points to consider" (rule-based, generated by `observations.py`)

Each observation has a trigger rule, the measured value, your threshold and a link to the evidence section. Examples using today's data:

| Trigger | Today | Observation text (generated) |
|---|---|---|
| Fund AUM < `min_fund_aum_eur` | World €46 M, Quantum €4 M | "2 funds (50.6 % of the portfolio) are below your €100 M minimum fund size. Small funds carry higher closure risk; a closure forces a sale." |
| Theme exposure > cap | Semis ≈ 31 % | "Look-through semiconductor exposure is 31 % (cap: 20 %)." |
| Largest fund > `max_fund_weight` | 41.5 % | "Largest fund is 41.5 % (target ≤ 35 %)." |
| Turnover 12M > 100 % | 565 % | "Turnover was 565 %. Each switch can realise taxable gains and adds spread costs." |
| Round-trip re-entry cost > €0 (sell → rebuy same ISIN ≤ 30 days) | 6 round trips, ≈ €3,330 | "You sold and re-bought the same fund 6 times within 30 days; buying back cost ≈ €3,330 more than the sale proceeds." |
| Execution vs. close > 3 % on trades > €1,000 | 1 trade (−12.1 %, semis sell 08.06.2026) | "1 large order executed 12.1 % below that day's close." |
| No EM allocation | ≈ 5 % via semis only | "Emerging markets are ≈ 5 % via sector funds only; the global market weight is roughly 10 %." |
| Domicile ∉ preferred | FR | "1 fund is domiciled outside Ireland." |
| Goal gap | vs. 2037 target | "At 7 % p.a. and €700/month, the projected 2037 value is X vs. target Y." |

Set `targets.target_wealth_10y` to your actual 2037 goal. Config has 150 k, but your plan is about €279 k by 2037.

### 6.2 Reference portfolios (neutral, deterministic)

Simulate on your **exact cashflows**, as already planned in v3 Step D:

- **Global all-cap single ETF.** The MSCI ACWI IMI or FTSE All-World type, which you held as your core earlier.
- **World + Small Cap 85/15**: the same building blocks as today, at market weight.
- **Your target mix**, from `targets.allocation`.

Show return, volatility, max drawdown and end wealth in one table, plus a risk/return scatter. Leave conclusions to the reader.

### 6.3 Market context (dated, sourced, separate)

As in v3 §5.3: a monthly briefing file `context/YYYY-MM.md` written with pi/Claude using web search, rendered verbatim with its date and sources. Example facts found today:

- European ETFs week 38 (14–18 Sep 2026): equity +€3.55 bn; world equity ETFs +€1.46 bn; Europe-equity ETFs −€405 M.
- 5 Oct 2026: US semiconductor ETFs SMH and SOXX saw combined outflows of $1.26 bn in one day, described as a sector reversal.

---

## 7. Report blueprint v4

One scrolling page, about 6 screens plus a collapsed appendix. Navigation: Summary · Performance · X-ray · Funds · Risk · Costs · Considerations · Market · Goals · Appendix.

**① Summary**
- 4 tiles: Portfolio value · Gain € · Return p.a. (XIRR) · vs. benchmark.
- 4–5 generated sentences in English, for example: "Your portfolio is worth €40,825, of which €7,970 is gain on €32,855 contributed. Since inception you earned 19.6 % p.a. … About one euro in three is invested in semiconductors."
- Health strip with 7 lights: largest fund, top-3, semis cap, weighted TER, current drawdown, policy compliance, data quality.

**② Performance**
- Hero chart: € / Index / Drawdown modes; gain band; benchmark with identical cashflows; deposit ticks; drawdown bracket; line-end labels.
- Monthly heatmap (portfolio vs. benchmark), annual bars, KPI row.

**③ Portfolio X-ray** (new, §5)
- Top-15 companies stacked by fund · overlap heatmap · sector/country/currency vs. benchmark · theme gauges.

**④ Fund profiles** (new, §4)
- One card per ETF: name, issuer, index, role (core/satellite), weight, your P/L.
- **16-KPI traffic-light grid** in your standard (Risk · Return · Risk-adjusted · Tracking · Cost · Scale), with as-of date and source per KPI.
- A sparkline of the fund's price over your holding period.

**⑤ Risk**
- Volatility, max/current drawdown, duration, Sharpe, Sortino, beta/correlation; underwater chart; risk/return scatter including the reference portfolios.

**⑥ Costs & activity**
- Weighted TER (€ per year) · explicit fees · tax drag (Vorabpauschale, Steuerausgleich) · turnover · trades per month.
- **Trading cost analysis** (new): round trips (sell → rebuy of the same ISIN within 30 days, with re-entry cost in €), execution vs. close for every trade over €1,000, and holding-period distribution (share of positions held < 30 days). The result is one honest number: "what active trading cost you vs. holding".

**⑦ Points to consider** (§6.1)

**⑧ Market context** (§6.3)

**⑨ Goals 2037**
- Fan chart 4/7/10 % with a nominal/real toggle · target line · required savings and required return · withdrawal phase.

**⑩ Appendix** (collapsed)
- Positions · transactions · realized gains · reconciliation · price coverage · data quality · methodology · **glossary**.

### Design direction

- Editorial style, like an annual report: generous white space, one accent colour, green and red only for gains and losses, always with a sign.
- Light/dark toggle in the header; print layout (A4, summary on page 1).
- ECharts throughout; tabular figures; English locale formatting.

---

## 8. Glossary (rendered at the end of the report)

Generated from `reference/glossary.yaml` so terms stay consistent. Each KPI label in the report links to its entry via an `ⓘ` tooltip.

| Term | Definition |
|---|---|
| **Accumulating (Acc)** | Fund reinvests dividends instead of paying them out. |
| **AUM** | Assets under management: the fund's total size. Small funds face higher closure risk. |
| **Benchmark** | Reference index used for comparison; here a global equity ETF that receives the same cashflows as you. |
| **Beta** | Sensitivity of the portfolio to benchmark moves; 1.38 means about 1.4 % per 1 % benchmark move. |
| **Cash-flow-matched benchmark** | Simulation investing your exact deposits on the same dates into the benchmark. |
| **Core / satellite** | Broad, low-cost base holding(s) plus smaller thematic or factor positions. |
| **Correlation** | How closely two return series move together (−1 to +1). |
| **Domicile** | Country where the fund is legally based; it affects withholding tax on dividends. |
| **Drawdown** | Fall from the previous peak; *max drawdown* is the largest such fall. |
| **Drawdown duration** | Days from a peak until it is regained (or until today if not yet regained). |
| **Emerging markets (EM)** | Less developed equity markets, e.g. China, Taiwan, India, Korea. |
| **ETF** | Exchange-traded fund; under UCITS it's a regulated, diversified fund. |
| **ETP / ETN / ETC** | Exchange-traded products that are not UCITS funds (notes or certificates); outside your policy. |
| **Excess return** | Portfolio return minus benchmark return, in percentage points (pp). |
| **Feeder / wrapper fund** | Fund that invests (almost) entirely in another fund. |
| **Implied price** | Price estimated from your own trade amounts when no market price is available; marked ≈. |
| **Look-through** | Analysing the companies, sectors and countries *inside* your funds. |
| **Optimised sampling** | Replication holding a representative subset of index constituents. |
| **Overlap** | Share of holdings two funds have in common, weighted by the smaller weight per company. |
| **Percentage point (pp)** | Absolute difference between two percentages. |
| **Physical replication** | Fund holds the index securities directly (vs. synthetic/swap-based). |
| **Realized / unrealized gain** | Gain locked in by selling, vs. gain on positions still held. |
| **Rolling 1-year return** | Return over the most recent 365 days. |
| **Round trip** | Selling a fund and buying the same fund back within a short window (here 30 days); the *re-entry cost* is the price difference times the shares re-bought. |
| **Execution vs. close** | Difference between the price you actually traded at and that day's closing price used for valuation. |
| **Sharpe ratio** | Excess return over the risk-free rate per unit of volatility. |
| **Sortino ratio** | Like Sharpe, but counts only downside volatility. |
| **Steuerausgleich** | German broker tax adjustment, e.g. refunds when losses offset earlier gains. |
| **Teilfreistellung** | German partial tax exemption for equity funds (30 % of gains tax-free for private investors). |
| **TER** | Total expense ratio: annual running cost, already deducted inside the fund price. |
| **Top-10 concentration** | Weight of a fund's ten largest holdings. |
| **Tracking difference** | Fund return minus index return over a period; the true cost of tracking. |
| **Turnover** | Value sold over 12 months divided by average portfolio value. |
| **TWR** | Time-weighted return: performance independent of when and how much you deposited. |
| **UCITS** | EU framework for regulated, diversified retail funds. |
| **Vorabpauschale** | German advance lump-sum tax on accumulating funds, charged each January. |
| **Volatility** | Annualised standard deviation of daily returns; a measure of fluctuation. |
| **XIRR** | Money-weighted annual return on your actual cashflows. |

---

## 9. Code changes

| Module | Change | Status |
|---|---|---|
| `reference/` (new dir) | `instruments.yaml` (moved), `holdings/<ISIN>/<date>.csv`, `glossary.yaml` | ❌ Not built — `instruments.yaml` stays at repo root; no `holdings/` CSVs, no `glossary.yaml` (see §12) |
| `sources/` (new pkg) | `openfigi.py`, `ishares.py` (CSV), `amundi.py` (Excel via `openpyxl`), `ecb.py` (FX, €STR). Each has a cache and a `--offline` fallback. | ❌ Not built — skipped per user decision, see §12 |
| `lookthrough.py` | Works from the holdings files: company/sector/country/currency aggregation, pairwise overlap (Σ min), theme exposure, match-rate reporting | ⚠️ Partially active — module itself unchanged (already built in v3); now has real data for weighted TER (all 4 held ISINs) and regions/sectors/top_holdings for 3 of 4 (Boreas missing, confirmed not fabricable). No holdings-file match-rate reporting (needs §9's skipped `sources/`) |
| `policy.py` (new) | Class validation, compliance check, historical out-of-policy list | ❌ Not built — skipped per user decision (investment-policy statement, not a lookup), see §12 |
| `observations.py` (new) | Rule → generated "Points to consider" sentence (§6.1) | ❌ Not built, see §12 |
| `trading.py` (new) | Round trips (sell → rebuy ≤ 30 days, re-entry cost), execution vs. close across *all* trades over €1,000 (reuses the patched `kpi.execution_vs_close`), holding-period distribution | ❌ Not built, see §12 |
| `models.py` (new) | Reference portfolios on identical cashflows (§6.2) | ❌ Not built, see §12 |
| `fundcard.py` (new) | Your 16-KPI grid per fund with traffic lights, as-of date and source per KPI | ❌ Not built, see §12 |
| `narrative.py` | Rewritten in English; adds the semis sentence and the goal sentence | ❌ Not built — stays German this session, see §12 |
| `render.py` | `locale: en-GB` formatting; glossary tooltips | ❌ Not built, see §12 |
| `templates/` | Split into section partials; all strings English | ❌ Not built — stays one file, stays German, see §12 |
| `instruments.yaml` | Factsheet fields (`ter`, `aum_usd`/`aum_eur`, `launch`, `domicile`, `replication`, `index`, `regions`, `sectors`, `top_holdings`, `as_of`, `source`) | ✅ **Done 2026-10-08** for the 4 currently-held funds (TER for all 4; regions/sectors/top_holdings for 3 of 4, Boreas confirmed not obtainable) |
| `tests/test_lookthrough.py` (new) | Regression coverage for `weighted_ter`/`lookthrough`/`overlap_matrix` all-or-nothing behavior | ✅ **Done 2026-10-08**, 6 tests |
| new deps | `openpyxl` (Amundi Excel). Everything else stays stdlib. | ❌ Not added — no Excel parsing built this session |

---

## 10. Delivery order

- **Step 0: v3 correctness.** ✅ Done in `3ac06b3` + `4377904` (execution_vs_close buy-side fix).
- **Step 1: English and glossary.** ❌ Not started — user chose "fund data first, English later" 2026-10-08; see §12.
- **Step 2: reference data.** ⚠️ **Partially done 2026-10-08**: TER/AUM/launch/domicile/replication/index/regions/sectors/top_holdings sourced for the 4 held funds (Boreas missing regions/sectors/top_holdings, confirmed not fabricable) — see §4, §9. `policy:` config and the compliance check **not done**, skipped per user decision (investment policy, not factsheet data).
- **Step 3: holdings and X-ray.** ❌ Not started — needs the `sources/` fetchers from Step 2, which were skipped; see §12.
- **Step 4: fund profiles.** ❌ Not started.
- **Step 5: considerations and reference portfolios.** ❌ Not started.
- **Step 6: market context.** ❌ Not started.
- **Step 7: design polish.** ❌ Not started.

## 11. Acceptance

- ~~No German UI string remains outside glossary terms; every KPI label has a glossary tooltip.~~ **Not met** — Step 1 (English) not done this session.
- ~~X-ray holdings match rate is at least 95 % for the iShares and Amundi funds, and every look-through figure shows its holdings as-of date.~~ **Not met** — Step 3 (X-ray, holdings fetchers) not done this session.
- ~~Semiconductor look-through exposure is computed from the holdings files and agrees with §5.2 within ±3 pp.~~ **Not met** — no holdings files (no `sources/ishares.py`); `instruments.yaml`'s `top_holdings` for the semis fund is from the issuer's own fact sheet directly, not a separate holdings-file cross-check.
- **Partially met**: each `instruments.yaml` entry filled this session shows a `source` and `as_of` for its factsheet fields; missing data (Boreas regions/sectors/top_holdings) shows as genuinely absent, never a guess — confirmed by `tests/test_lookthrough.py`'s narrowing-reason test. Not yet surfaced per-KPI in the UI (no `fundcard.py`, Step 4).
- ~~"Points to consider" contains only rule-triggered sentences, each with its threshold shown. No buy/sell wording.~~ **Not met** — `observations.py` (Step 5) not built this session.
- Performance starts on 16 Oct 2024, or the report states clearly why it doesn't. ✅ **Met** — fixed in the v3 follow-up session (`isin-ticker-resolution-concept.md` §1.7); `twr_start` is 2024-10-16, confirmed still true after this session's changes (48/48 tests pass).

## 12. Leftovers (for the next pass)

In priority order, based on what actually unlocks the most once you've seen what §2's partial completion did (weighted TER went from n/a to a real, amber-rated 0.27 % p.a.):

1. **Boreas's regions/sectors/top_holdings.** The one piece of §2 that's genuinely missing, not skipped by choice — the issuer's site is JS-rendered and third-party sources only had names, no weights. If you can get a screenshot or copy-paste of Boreas's own factsheet/KID holdings table (even a PDF), that's the one input needed to light up region/sector look-through and the overlap matrix for the whole portfolio — right now both still show n/a, named to exactly this one fund.
2. **English rewrite (Step 1).** Deferred by explicit user choice this session ("fund data first, English later"), not forgotten. Large, mechanical: every string in `templates/report.html.j2`, `narrative.py`, `health.py`, plus a locale/number/date-format switch in `render.py`, plus `glossary.yaml` + tooltips. Worth doing as one dedicated pass rather than mixed into other work, since it touches nearly every file and has no logic risk if done carefully (pure string/format substitution).
3. **`instruments.yaml`'s `currency` field.** Not sourced this session for any fund (would need either a real per-fund currency breakdown or a defensible country→currency derivation from the `regions` data already in place) — currency look-through stays n/a for all 4 funds until this exists. Smaller chore than the rest of §2, could piggyback on item 1 if Boreas's data also arrives.
4. **Trading-cost analysis (`trading.py`, §6/§7⑥).** The v4 doc's own §0 status banner already found the headline number by hand (6 round trips, ≈€3,330 net re-entry cost; the 3-day NVIDIA round trip alone cost ≈€1,008) — turning that hand calculation into code (reusing the now-correct `kpi.execution_vs_close` and `ledger.buys`/`ledger.sells`) is a contained, high-value addition with no external-data dependency.
5. **`policy.py` + `role` (core/satellite).** Explicitly skipped this session per your own choice — needs you to state actual rules (allowed/forbidden classes, max weights, domicile preference) and which of the 4 funds you consider core vs. satellite, not a lookup. The allocation health check keeps showing n/a until this exists.
6. **`observations.py` (§6.1 "Points to consider").** Needs `policy.py` (item 5) and `trading.py` (item 4) as inputs for several of its rules (AUM guard, round-trip cost, policy domicile) — sequence after those, not before.
7. **`models.py` (reference portfolios, §6.2) + risk/return scatter.** No blockers, moderate effort, carried over unchanged from the v3 leftovers list (same item, still not built).
8. **Fund profiles / 16-KPI cards (`fundcard.py`, Step 4) + X-ray section (Step 3, needs the skipped `sources/` fetchers).** The largest remaining chunks; Step 3 specifically needs either live scraping (iShares CSV, Amundi Excel — both skipped this session on purpose, fragile/ToS risk) or continued manual factsheet transcription like this session's §2 work, extended to holdings-level granularity.
9. **Market context (Step 6)** and **design polish (Step 7: dark mode, print, mobile)** — unchanged from the v3 leftovers list, lowest priority, report is fully usable without them.
10. **v3 §12a readability punch list** (German-only this session, so most of it still applies as-is: dev-text leakage, placeholder n/a cards — now narrower but not gone, asset-class donut at 100 % ETF, donut legend pagination at 4 items, contribution-bar label clipping, ISO vs. German date/number formats, goal fan-chart legend overlap, no manual dark-mode toggle) — explicitly still deferred, now doubly so since Step 1's English rewrite will touch most of the same strings.
