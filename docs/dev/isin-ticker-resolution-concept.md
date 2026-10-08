# ISIN → Ticker Resolution — Concept

Base: commit `41fa372` · 2026-10-07 · Scope: close the ticker-coverage gap flagged in
`docs/dev/report-v3-concept.md` §12 item 1 ("Source real tickers for the historical ISINs — the
only item that materially changes risk-KPI trustworthiness"). No new runtime dependency, no
database, no server.

> **Update 2026-10-07: the fast path is done, measured, and it worked.** Six known tickers for
> the four long-held, biggest-balance historical funds plus the one real-equity position were
> confirmed live (§1.1) and added to `instruments.yaml`. Measured effect: **implied price share
> dropped from 71% to 14%** on the real ledger — under the 20% target, without building the
> automated resolver in §2 at all. Three related correctness bugs this unblocked
> (benchmark-sentence gate, risk-KPI dampening, TER health check) are fixed in §1.3. The automated
> `isin_resolver.py`/`--resolve-tickers` design in §2 is kept as the right tool for the *next* batch
> of ISINs (anything beyond hand-picked, high-confidence, well-known funds), not retracted — see
> §1.6 for when it's actually worth building.

> **Update 2026-10-07 (later the same day): a review caught two real bugs in how this was first
> explained, both now fixed in code — see §1.7.** `VUAA.DE`'s short Yahoo history (from
> 2024-12-30, not the ISIN's real 2024-10-16 start) was silently pushing `twr_start` 2.5 months
> late, cutting the opening contribution out of every TWR-based figure while XIRR kept the true
> start date — fixed via a one-way implied→market switch inside `resolve_closes()`. The remaining
> "unusual jump" on 2026-06-08 was also mis-explained as "a rebalancing trade": a pure
> reallocation can't move a close-to-close series by itself. The real cause, found by comparing
> ledger trade prices to that day's close, is a −12.1% execution-vs-close divergence on one sell
> leg, now surfaced explicitly via `kpi.execution_vs_close()`. Implied share: 14% → 15% (still
> under target).

> **Update 2026-10-08: the §1.7 fix for the 06-08 jump had its own bug — see §1.8.** The new
> `execution_vs_close()` checked buys via `ledger.lots`, which later sells mutate/consume; a buy
> later fully sold (NVIDIA, sold 3 days after it was bought) was silently never checked, despite
> the §1.7 commit message claiming it was "within 1%". That specific number happened to be right
> (confirmed by hand, separately, at the time) but wasn't actually produced by the function it was
> attributed to. Fixed by adding `ledger.BuyRecord`, an unmutated record of each executed buy,
> symmetric to the existing `SellRecord`. The report's headline message is unchanged (SEC0's main
> sell at −12.1% was, and remains, the worst leg) — this was a test-coverage gap that could have
> produced a wrong headline on a different day's data, not a wrong headline on this one.

## 0. Framing: this is not a database integration

The instinct that "this needs a database" is understandable — 30 of 34 ever-traded ISINs have no
ticker, and manually hand-sourcing 30 tickers one by one on a finance site *feels* like a data
problem big enough to need real infrastructure. It isn't, and here's the proof, done live against
this repo's own data while writing this doc:

```
$ curl -s -A "Mozilla/5.0" "https://query1.finance.yahoo.com/v1/finance/search?q=IE00B4L5Y983"
→ IWDA.L  "iShares Core MSCI World UCITS ETF USD (Acc)"  LSE  ETF
```

Yahoo Finance — the exact same, already-integrated data source `prices.py` uses for daily
closes — has its own ISIN search endpoint. Tested against **all 25 resolvable historical ISINs**
in this portfolio's ledger (the crypto product and a few short-held, since-discontinued listings
aside), **24 resolved to a clean, named ETF/equity match on the first try**, and the one resolved
ticker spot-checked (`IWDA.L`) fetches real daily closes through the **existing, unmodified**
`get_closes()` function with zero code changes. The "database" this needs is a ~1 KB JSON cache
file next to `cache/prices.json`, not a schema, not a server, not a new provider account.

What this concept *does* need to get right, because it's a real risk: **currency and share-class
mismatches**. `IE00B4L5Y983` (iShares Core MSCI World) trades on the LSE in USD (`IWDA.L`) *and*
in several EUR-denominated venues the search endpoint doesn't always surface first. Silently
accepting the first match can quietly feed a USD price series into a EUR portfolio and corrupt
every KPI downstream worse than the implied-price fallback it replaces. §2 below is built around
catching exactly that before anything is written to `instruments.yaml`.

## 1. What actually happened (2026-10-07)

### 1.1 Six tickers, hand-picked, each verified live before trusting it

Rather than building the automated resolver first, the highest-value move was simpler: the four
longest-held, biggest-balance historical funds (Oct 2024 → 2025/26) plus the one real-equity
position account for most of the estimated value-days. Six known tickers were proposed, and each
was **confirmed independently, live, before being added** — not trusted on recall alone:

| ISIN | Ticker | Verified via | Result |
|---|---|---|---|
| `IE00B4L5Y983` | `EUNL.DE` | chart `meta` | EUR, GER (Xetra), ETF — confirmed; history from 2021 |
| `IE00BFMXXD54` | `VUAA.DE` | chart `meta` | EUR, GER, ETF — confirmed; **history only from 2024-12-30** (see §1.2) |
| `IE00B3WJKG14` | `QDVE.DE` | chart `meta` | EUR, GER, ETF — confirmed; history from 2021 |
| `IE00BM67HK77` | `XDWH.DE` | chart `meta` | EUR, GER, ETF — confirmed; history from 2021 |
| `IE00B5BMR087` | `SXR8.DE` | chart `meta` | EUR, GER, ETF — confirmed; history from 2021 |
| `US67066G1040` | `NVD.DE` | chart `meta` | EUR, GER, **EQUITY** — confirmed; real US stock, EUR listing picked on purpose over the USD `NVDA` Yahoo's own search surfaces first (§2.4 still applies) |

Verification method, for each: `curl .../v8/finance/chart/<ticker>?range=5d` and read
`meta.currency` / `meta.exchangeName` / `meta.instrumentType` — exactly the `probe_currency()`
concept from §2.1, just run by hand against six specific tickers instead of an automated sweep
over thirty unknowns. All six came back correct on the first try; nothing here was wrong.

### 1.2 One caveat found during verification: `VUAA.DE`'s history starts 2024-12-30 — **corrected in §1.7, this framing was wrong**

*(Original text, kept for context on what was first assumed — see §1.7 for what a review found
and the actual fix.)*

`IE00BFMXXD54` was first bought 2024-10-16, but Yahoo's `VUAA.DE` history only goes back to
2024-12-30. ~~Per §1's one-source-per-span rule (`report-v3-concept.md` §1): since this span now
has real market data somewhere inside it, the whole span commits to "market" — the ~2.5 months
before the ticker's own history begins are **unpriced**, not silently patched with an implied
price. This is the system working as designed, not a new bug.~~ **Wrong**: being unpriced for 2.5
months at the very start of the ledger's history means `compute_coverage` can't find a price for
every then-held ISIN that far back, which pushes `coverage.start`/`twr_start` forward to
2024-12-30 — cutting out the opening 10.000€ investment from every TWR-based figure (TWR, TWR
p.a., drawdown, volatility, the monthly heatmap, the benchmark comparison) while XIRR kept running
from the true start. Fixed in §1.7.

### 1.3 Measured effect, and the three bugs it unblocked

```
Before: 71% of prices estimated   →   After: 14% of prices estimated
```

Under the 20% target from `report-v3-concept.md` §1, with zero resolver code written. This
exposed three correctness bugs that only mattered once the number was close to the threshold
(at 71%, they were moot — everything was unreliable anyway; at 14%, getting the *gate* right
matters), all fixed the same session:

1. **`narrative.py`'s benchmark-comparison sentence gated on `implied_share < 0.80`**, not `< 0.20`
   as `report-v3-concept.md` §1 specifies ("mention the benchmark only once market coverage is at
   least 80%"). At 71% implied, the old gate let a benchmark-delta sentence through anyway —
   looked authoritative, wasn't. Fixed to `< 0.20`; regression-tested
   (`tests/test_health_and_narrative.py`) against the exact 0.50 value that used to pass the old
   gate and must not pass the new one.
2. **Risk KPIs (Sharpe, Sortino, Vola, Max/Akt. Drawdown, Beta/Korrelation) weren't visually
   dampened when `coverage_implied_share >= 0.20`** — flagged as open in `report-v3-concept.md`'s
   own acceptance notes. Fixed: the Risiko KPI row now gets a `.estimated-risk` class (reduced
   opacity + desaturation) plus an explicit ⚠ banner whenever the gate is tripped, instead of
   looking identically confident regardless of data quality. At today's 14% it renders normally;
   the mechanism exists for whenever a future CSV re-introduces untickered holdings.
3. **`health.py`'s `_ter_check` hardcoded `0.0025`** as the green threshold instead of reading
   `targets.max_ter` from `config.yaml`, and used an unexplained `×1.6` for amber. Fixed to use
   `max_ter` directly for green and `max_ter × 2` for amber — your configured target now actually
   drives the check; regression-tested.

(A fourth, smaller bug from the same review — the contribution chart's card title claiming
"realisiert + unrealisiert" when the underlying data is realized-only — was also fixed, title now
reads "Realisiertes Ergebnis je Instrument" with an explicit note about what's excluded and why.)

### 1.4 Rechecking the "unusual jump" list with real prices in — **mechanism corrected in §1.7**

*(Original text, kept for context — the conclusion "real move, not a bug" turned out to be right,
but the reasoning why was wrong; see §1.7.)*

The explicit ask after adding tickers was: recheck Datenqualität's unusual one-day-jump list,
since "any jump left after this is either a real move or a bug, and I couldn't check that without
real prices." Before this change there were 6 flagged jumps; **after, there is 1**, on
**2026-06-08**. Traced directly against the ledger (`qty_history`, not guessed):

```
2026-06-08: IE000I8KRLL9 (semiconductor ETF) fully sold, qty 2102.701 → 0
            US67066G1040 (NVIDIA) bought same day, qty 0 → 150
            IE00BF4RFH31 (small-cap ETF) bought same day, qty 0 → 1111
```

~~This is a real, large, same-day portfolio rebalancing (full exit of one position funding two new
ones) — not a pricing artifact. **Verdict: real move, not a bug.** The remaining ~−10% single-day
swing is consistent with a big reallocation day... rather than anything resolve_closes() or the
ledger gets wrong.~~ **Wrong mechanism**: a pure internal reallocation (sell A, buy B, same day)
cannot by itself move a close-to-close value series — both legs valued at the same day's close
conserve total value exactly. A jump on a day that looks like a clean rebalance means one leg's
*actual execution price* diverged from the close used to value it. See §1.7 for the real cause,
found by comparing ledger trade prices to that day's cached close, not by guessing.

### 1.5 Current state (measured 2026-10-07)

`uv run etf-portfolio --list-instruments` on the real ledger:

| | Count |
|---|---|
| ISINs ever traded | 34 |
| Have a ticker in `instruments.yaml` now | 10 (4 current holdings + 6 from §1.1) |
| Missing a ticker | 24 |
| Of the 24, resolved via Yahoo search on the first try (tested live, see §0) | ~18 |
| Of the 24, resolved but with a currency/listing caveat to check | ~4–5 (multi-listing ISINs — iShares/Vanguard/Xtrackers products routinely list on 3–6 exchanges) |
| Not resolvable via ISIN search at all | 1 (`XC000A2YY6Q6`, a broker-internal crypto product code, not a real ISIN — stays on implied pricing, by design, forever) |
| **Measured implied price share** | **14%** (was 71% before §1.1) — already under the 20% target |

So the realistic ceiling here is **~33 of 34 ISINs tickered**, not 34 — and that's fine; the
implied-price fallback in `prices.resolve_closes()` exists precisely for the irreducible case, and
the number that matters (implied *share*, weighted by holding-period length and position size) is
already well past the point where the remaining ISINs move it much.

### 1.6 Is the automated resolver in §2 still worth building?

At 14% implied share, **no, not urgently.** The six hand-verified tickers closed the gap that
mattered (big balances, long holding periods) with ~15 minutes of manual `curl` verification —
faster than designing, building, and testing a resolver module would have been. The remaining 24
ISINs are, per `--list-instruments`, mostly short-held (weeks, not months) and small — their
individual contribution to `coverage_implied_share` is already small and shrinking as a share of
an increasingly well-tickered ledger. Build §2 if: (a) a future CSV import adds several new
long-held positions without obvious tickers, or (b) you want the remaining ~24 resolved for
completeness regardless of their KPI impact. Otherwise this concept's automated design stays as a
ready-to-build reference, not a todo.

### 1.7 A review caught two real bugs in §1.2/§1.4, both fixed (2026-10-07, same day)

A review of the shipped commit, re-run against `compute_coverage` and the actual trade ledger
instead of taking the earlier framing at face value, found that both §1.2 and §1.4 above were
wrong in exactly the way flagged — and both are now fixed in code, not just reworded.

**Bug A — `VUAA.DE`'s 2.5-month gap silently moved `twr_start` to 2024-12-30.** Walked it through
`compute_coverage`: a day with *zero* price entry for a then-held ISIN (not "implied", not
"market", nothing) counts as uncovered, and `coverage.start` is set to the day *after* the last
such day. Since the VUAA.DE span committed fully to "market" the moment any market data existed
in it, the 2024-10-16–2024-12-29 stretch had no entry at all. Result: TWR, TWR p.a., max
drawdown, volatility, the monthly heatmap, and the benchmark comparison all silently started
2024-12-30 — cutting out the opening 10.000€ contribution — while XIRR kept running from the true
start, 2024-10-16. The "Seit Start erzielten Sie X % p.a. (XIRR) und liegen Y pp vor dem
Vergleichsindex" sentence was therefore combining two different start dates without saying so.

*Fix* (chose the preferred option offered: allow one switch at the start, not a longer EUR
listing — `VUAA.MI`/Milan wasn't checked, this was simpler and sufficient): inside
`prices.resolve_closes()`, once a span commits to "market", days strictly *before* that ticker's
own first-ever market quote now use the implied price instead of being left unpriced. This is a
single, one-way boundary (implied → market, never back) — not the per-day mixing §1's rule exists
to prevent, and not reintroduced: a day *after* the market series has started that genuinely has
no quote (a real gap, not a before-listing gap) still stays unpriced, verified by a dedicated
regression test (`test_resolve_closes_never_mixes_sources_mid_span_after_market_starts`).
Kursabdeckung now shows this explicitly: a warning naming the ISIN and the date its market data
starts. **Measured result: `twr_start` is back to 2024-10-16 (the true start); implied share rose
slightly to 15% (was 14%, since those leading gap-days now count honestly as implied) — still
well under the 20% target.**

**Bug B — the 2026-06-08 jump explanation ("rebalancing") was mechanistically wrong.** A pure
internal reallocation (sell A, buy B, same day) cannot move a close-to-close value series by
itself — both legs valued at the same day's close conserve total value exactly; conservation of
value, not a guess. A jump on a day that looks like a clean swap means one leg's *actual execution
price* diverged from the close used to value it. Checked directly, trade price vs. that day's
cached close, computed from the ledger's own `Lot`/`SellRecord` data (not estimated from the CSV
by hand): the `IE000I8KRLL9` (iShares MSCI Global Semiconductors) sale executed at ≈16,02€/share
against a closing price of 18,22€/share that day — a **−12,1% divergence**. The NVIDIA and
small-cap-ETF buys that same day were both within 1% of their respective closes, i.e. fine.
Order numbers (the sale's order number is markedly lower than the two buys') are consistent with
the sale executing earlier in the day than the buys, on an ETF that had already dropped ~8% over
the prior week — consistent with real intraday volatility on a concentrated, volatile sector ETF,
not proof of a booking-date bug; not conclusively decided either way without independent intraday
OHLC data, which isn't available.

*Fix*: added `kpi.execution_vs_close(day, ledger, series_by_isin, threshold=0.03)`, which compares
every BUY/SELL booked that day against that day's close using the ledger's own trade records.
When a jump coincides with a ≥3% divergence, the Datenqualität message now names the instrument,
the side (Kauf/Verkauf), and the exact percentage instead of guessing "rebalancing":

```text
2026-06-08: Depotwert-Tagesrendite -10.4% (Benchmark -0.5%) — Ausführungskurs (Verkauf iShares
MSCI Global Semiconductors) weicht -12.1% vom Schlusskurs ab, nicht ein reiner Umschichtungs-Effekt
```

**Smaller fix, same session**: the "vs. Benchmark" KPI tile (`templates/report.html.j2`, the hero
row, not the narrative sentence) had no `coverage_implied_share < 0.20` guard at all — only the
summary sentence did. Harmless at 14–15%, but it would have silently shown a confident number
again if coverage regressed. Added the same gate, with a distinct "Marktabdeckung < 80 %" reason
when the gate trips vs. "keine Kursabdeckung" when there's no benchmark series at all.

6 of the new/rewritten tests live in `tests/test_prices.py` (2) plus the existing
`tests/test_health_and_narrative.py`; 40/40 tests pass.

### 1.8 Bug B's fix (§1.7) was itself incomplete: BUY-side checks read `lots`, which later sells mutate

`execution_vs_close()` as shipped in §1.7 iterated `ledger.lots` for the BUY side. `lots` is the
FIFO structure that later *sells* consume in place — a fully-sold lot ends up with `qty == 0` or
is removed from the list entirely. The NVIDIA buy on 2026-06-08 (`US67066G1040`) was itself fully
sold on 2026-06-11, three days later; by the time any later code looked at `ledger.lots['US67066G1040']`
it was `[]`. **The §1.7 commit message's claim "the NVIDIA ... buy was within 1% of close" was true,
but did not come from running `execution_vs_close()` — it came from an earlier hand calculation
(180,98 vs. close 179,86) done while manually tracing the CSV, which happened to agree with what
the function would have said if it had checked NVIDIA at all. It hadn't.**

*Fix*: added `ledger.BuyRecord` (`etf_portfolio/ledger.py`), symmetric to the existing `SellRecord`
— the original executed buy transaction (date, isin, qty, cost), appended once at BUY time and
never mutated afterwards, unlike `lots`. `execution_vs_close()` now reads `ledger.buys` for the
buy side. Reran the function (not by hand) against the real ledger for 2026-06-08 with the
threshold dropped to 0 to see every leg, not just the one that clears 3%:

```text
IE000I8KRLL9  sell  -12.1%   (the main 2.102-share order; still the worst leg, report unaffected)
IE000I8KRLL9  sell   -5.0%   (the same-day Bruchstücke fractional sell, a second leg not mentioned before)
US67066G1040  buy    +0.6%   (NVIDIA — now actually checked, confirms the §1.7 number was right by luck)
IE00BF4RFH31  buy    -0.3%   (small-cap ETF — also now actually checked)
```

The report's chosen message (worst leg, SEC0's main sell at −12.1%) is unchanged — this was a
test-coverage gap, not a wrong headline number — but the fractional Bruchstücke leg at −5.0% was
never being checked either, and would have been missed if it had been the worst leg on some other
day. Added `tests/test_kpi.py::test_execution_vs_close_still_flags_a_buy_that_is_fully_sold_soon_after`,
which reproduces this exact pattern (buy, then full sell 3 days later) and fails against the old
`lots`-based implementation. 42/42 tests pass.

**Process note**: this is the second review in a row to catch a claim in a commit message that
wasn't actually backed by the code it described (§1.2/§1.4 asserted conclusions the code didn't
yet support; this one asserted a number the new function hadn't actually computed). Worth
treating "did the function get run against this specific case, with this specific output shown"
as a harder requirement before writing a result into a commit message, not just "is the code
plausible."

## 2. Design

### 2.1 New module: `etf_portfolio/isin_resolver.py`

Mirrors the shape of `prices.py` deliberately — same user agent, same cache-file pattern, same
"never crash the report, always have a path to `None` with a reason" discipline.

```python
@dataclass
class TickerCandidate:
    ticker: str
    exchange: str          # "LSE", "GER", "STU", ...
    quote_type: str        # "ETF", "EQUITY", "MUTUALFUND", ...
    name: str
    currency: str | None   # filled in by a follow-up chart-meta probe, not the search call itself
    is_base_currency: bool | None  # None until currency is known


def search_isin(isin: str, timeout: float = 8.0) -> list[TickerCandidate]:
    """Query Yahoo's /v1/finance/search?q=<ISIN>. Returns raw candidates, unranked,
    currency unknown — this call alone is NOT enough to pick a winner."""

def probe_currency(ticker: str, timeout: float = 8.0) -> str | None:
    """One cheap chart-API call (range=5d) read only for meta.currency. Reuses the
    exact request shape get_closes() already makes; added because get_closes()
    today discards meta and only returns the closes dict."""

def resolve_candidates(isin: str, base_currency: str) -> list[TickerCandidate]:
    """search_isin() + probe_currency() for each candidate, then sort:
    1. currency == base_currency first
    2. quote_type == "ETF"/"EQUITY" over "MUTUALFUND" (the Stuttgart/Tradegate-style
       listings showed up tagged MUTUALFUND for several real UCITS ETFs in testing —
       a quirk of Yahoo's own classification, not a signal to trust blindly either way)
    3. stable order otherwise (Yahoo's own relevance score)
    Does not pick a winner. Resolution is a human decision (§2.3); this just ranks."""
```

`get_closes()` itself is **not changed** — `probe_currency()` is a separate, smaller request
(`range=5d` instead of the full history) so resolving 30 ISINs doesn't imply fetching 30 full
price histories just to check currency.

### 2.2 Cache: `cache/isin_tickers.json`

Same spirit as `cache/prices.json` (gitignored, offline-safe, append-only merge):

```json
{
  "IE00B4L5Y983": {
    "resolved_at": "2026-10-06",
    "candidates": [
      {"ticker": "IWDA.L", "exchange": "LSE", "quote_type": "ETF", "currency": "USD", "name": "..."},
      {"ticker": "SWDA.MI", "exchange": "MIL", "quote_type": "ETF", "currency": "EUR", "name": "..."}
    ]
  },
  "XC000A2YY6Q6": { "resolved_at": "2026-10-06", "candidates": [], "note": "not a real ISIN, crypto product code" }
}
```

Caching the *candidate list*, not a single chosen ticker, matters: it means re-running resolution
doesn't re-hit Yahoo for ISINs already looked up, but the human-review step (§2.3) still happens
against fresh-enough data, and a `--refresh-isin-cache` flag can force re-querying for ISINs where
the first pass found nothing (listings do get added after a fund launches).

### 2.3 CLI: `uv run etf-portfolio --resolve-tickers`

**Never writes `instruments.yaml` automatically.** This is the one place this concept deliberately
adds friction on purpose: an auto-write that gets a currency or share class wrong is strictly worse
than today's honest "≈ implied", because it looks authoritative. The command:

1. Finds every ISIN in the ledger with no `ticker` in `instruments.yaml`.
2. Runs `resolve_candidates()` for each (cached; `--offline` uses cache only, consistent with every
   other command in this CLI).
3. Prints a reviewable table, ranked best-candidate-first, **flagging currency mismatches loudly**:

```
ISIN           best candidate        currency        name                                    flag
IE00B4L5Y983   SWDA.MI (MIL)         EUR             iShares Core MSCI World UCITS ETF        
IE00BFMXXD54   VUSA.DE (GER)         EUR             Vanguard S&P 500 UCITS ETF                
US67066G1040   NVDA (NMS)            USD             NVIDIA Corporation                        ⚠ non-EUR, no EUR listing exists (it's a US stock)
DE000A3G3ZL3   DA21.DE (GER)         EUR             Bitwise Europe GmbH ... O                  ⚠ low-confidence name match, verify by hand
XC000A2YY6Q6   —                     —               —                                          no candidates (not a real ISIN)

29 resolved, 1 flagged low-confidence, 1 flagged non-EUR (expected — it's a US stock), 1 unresolvable.
Copy accepted rows into instruments.yaml by hand, or re-run with --apply to write them
(still requires --apply-confirm for any row flagged above).
```

4. `--apply` writes the ranked top candidate into `instruments.yaml` for every **unflagged** row
   only — flagged rows (currency mismatch, low-confidence name match, zero candidates) are always
   left for a human, never auto-applied, regardless of flags.
5. Every write is additive and attributed: `instruments.yaml` gets a comment on each
   auto-populated line (`# auto-resolved 2026-10-06, verify before trusting`), so a future `git diff`
   or a future you can tell "I picked this" apart from "a script picked this and I never looked".

### 2.4 `US67066G1040` (NVIDIA) is not a bug

One ISIN in the ledger is a plain US equity, not a fund. It will only ever resolve to a USD
ticker (`NVDA`) because that's the actual, correct instrument — there is no EUR listing to prefer.
The resolver must not try to "fix" this; it should surface it as an *expected* non-EUR flag (§2.3),
distinct from a *should-have-been-EUR-but-wasn't* flag, so the review table doesn't train you to
ignore warnings.

### 2.5 What this does *not* attempt

- **No fallback to a second provider** (OpenFIGI, financialmodelingprep, etc.) if Yahoo's search
  returns nothing. One source, consistent with the rest of this codebase; an ISIN that doesn't
  resolve stays on implied pricing, same as today, not a hard failure.
- **No automatic currency conversion.** If, for some ISIN, only a non-base-currency listing exists
  and you choose to use it anyway (NVIDIA above, or a fund with no EUR share class), the price
  series is in that currency as-is — `report-v3-concept.md`'s existing "≈"-style honesty badges are
  the right place to flag "priced in USD, not converted", not a new FX-conversion feature. Revisit
  only if it turns out to matter for a real position (today: only the one US stock, immaterial).
- **No change to `prices.resolve_closes()`'s one-source-per-span rule** (`docs/dev/report-v3-concept.md`
  §1). Resolution just gets *more* ISINs a real ticker; what happens once a ticker exists is already
  correct.

## 3. Expected effect on the report — original projection, now superseded by §1

*(Written before the hand-picked fast path in §1 was tried. Kept verbatim for context on what
was predicted vs. what actually happened — see §1.3/§1.5 for the real, measured numbers.)*

Before (then-current `instruments.yaml`, 4/34 tickered): 71% implied price-days, health-check
Datenqualität dot red, risk KPIs shown with `≈` and a disclosure sentence.

After (resolving even the ~29 straightforward cases, leaving the 1 crypto + maybe 1–2 genuinely
ambiguous ones on implied): projected implied share well under the report-v3-concept.md §1 target
of 20% — most of the 30 missing ISINs were only held for a few weeks each (see the `first`/`last`
columns in `--list-instruments`), so even a handful of remaining implied spans won't dominate the
`coverage_implied_share` weighted by holding-period length the way they do today at 4/34.

**Actual result (§1.3): 14% implied, via 6 tickers, not 29.** The projection's *direction* was
right; its *mechanism* (resolve nearly everything) was more than was needed in practice.

## 4. Delivery order for §2 (only if you decide to build it — see §1.6)

Not current work. Kept as a ready-to-execute plan for if/when the remaining ~24 untickered ISINs
become worth resolving automatically (new long-held positions, or wanting full completeness):

- **Step 1**: `isin_resolver.py` (`search_isin`, `probe_currency`, `resolve_candidates`) + cache
  file + ~6 unit tests (mock the two HTTP calls, no live network in tests — same convention as
  `tests/test_prices.py`).
- **Step 2**: `--resolve-tickers` CLI command (read-only table, no `--apply` yet). Lets you review
  real candidates against the real ledger before any write path exists at all.
- **Step 3**: `--apply`/`--apply-confirm` write path, with the attribution comment.
- **Step 4**: run it for real, review the table, decide the ambiguous rows by hand, re-run
  `uv run etf-portfolio` and confirm the implied share and health-check dot both improve further.

## 5. Acceptance (for §2, if built)

- `uv run etf-portfolio --resolve-tickers --offline` works from cache, makes no network calls.
- No row is ever auto-applied with a currency mismatch or zero candidates.
- Every auto-applied `instruments.yaml` line is comment-tagged with the date it was resolved.
- Re-running resolution for an ISIN already in the cache does not re-hit Yahoo unless
  `--refresh-isin-cache` is passed.
- After `--apply` + manual review of the flagged rows, `coverage_implied_share` in the real report
  drops further from today's already-under-target 14% (§1.5).

## 6. What was delivered this session, concretely

- [x] 6 tickers verified live and added to `instruments.yaml` (§1.1), with the `VUAA.DE`
  short-history caveat documented inline (§1.2).
- [x] Measured implied share 71% → 14% → 15% after the §1.7 coverage fix (§1.3/§1.5), real
  build, not a projection; still well under the 20% target.
- [x] `narrative.py` benchmark-sentence gate fixed `0.80 → 0.20` (§1.3.1).
- [x] Risiko KPI row visually dampened (`.estimated-risk`) + ⚠ banner when
  `coverage_implied_share >= 0.20` (§1.3.2) — closes the open item from
  `report-v3-concept.md`'s own acceptance notes.
- [x] `health.py`'s `_ter_check` now reads `targets.max_ter` instead of a hardcoded `0.0025`
  (§1.3.3).
- [x] Contribution chart title corrected to say "Realisiertes…", not "realisiert + unrealisiert"
  (§1.3, parenthetical).
- [x] Unusual-jump list rechecked with real prices: 6 → 1 remaining (§1.4) — **the
  "rebalancing" explanation was mechanistically wrong; corrected in §1.7** to the real cause
  (a -12.1% execution-vs-close divergence on the sell leg), found and fixed with
  `kpi.execution_vs_close()`, not just re-explained.
- [x] **§1.7, found by a review and fixed the same day**: `compute_coverage`'s `twr_start` was
  silently pushed from 2024-10-16 to 2024-12-30 by `VUAA.DE`'s short Yahoo history — fixed via a
  single one-way implied→market switch at the ticker's first quote in `resolve_closes()`, with a
  regression test proving mid-span gaps still don't get the same treatment.
- [x] `templates/report.html.j2`'s "vs. Benchmark" KPI tile gated on `coverage_implied_share <
  0.20`, matching the summary sentence's gate (§1.7) — it had no gate at all before.
- [x] 8 new/rewritten regression tests total (2 in `tests/test_prices.py`, 6 in
  `tests/test_health_and_narrative.py`); all 40 tests pass.
- [ ] `isin_resolver.py` / `--resolve-tickers` (§2) — **not built**, judged not worth it yet (§1.6).
- [ ] The larger "Readability" punch list from the same review (dev-text leaking into the report,
  placeholder-card collapse, asset-class donut, contribution-bar label width, date/number format
  consistency, goal-chart legend overlap, hero-chart annotations, dark-mode toggle) — **out of
  scope for this doc**, tracked in `docs/dev/report-v3-concept.md` §12 instead, since it's a report
  UI/design backlog, not a ticker-resolution concern.
