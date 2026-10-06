"""Daily close prices: fetch, cache, offline fallback.

Phase 2 of docs/dev/portfolio-report-concept.md §7.

The provider sits behind a single function, get_closes(), so swapping it
later is a one-function change. Only tickers leave the machine, never
quantities or amounts.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
USER_AGENT = "Mozilla/5.0 (compatible; etf-portfolio-report/1.0)"


class PriceFetchError(RuntimeError):
    pass


def get_closes(ticker: str, start: date, end: date, timeout: float = 8.0) -> dict[date, Decimal]:
    """Fetch daily unadjusted closes for ticker in [start, end] from Yahoo Finance.

    Raises PriceFetchError on any network/parse failure. Callers must fall
    back to cache and never crash the report on a fetch failure.
    """
    period1 = int(datetime(start.year, start.month, start.day).timestamp())
    period2 = int(datetime(end.year, end.month, end.day).timestamp()) + 86400
    url = f"{YAHOO_CHART_URL.format(ticker=ticker)}?period1={period1}&period2={period2}&interval=1d"
    if not url.startswith("https://query1.finance.yahoo.com/"):
        raise PriceFetchError(f"refusing to fetch non-Yahoo URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PriceFetchError(f"{ticker}: fetch failed: {e}") from e

    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as e:
        raise PriceFetchError(f"{ticker}: unexpected response shape: {e}") from e

    out: dict[date, Decimal] = {}
    for ts, close in zip(timestamps, closes, strict=True):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, UTC).date()
        out[d] = Decimal(str(round(close, 6)))
    return out


def load_cache(cache_path: Path) -> dict[str, dict[str, str]]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache_path: Path, cache: dict[str, dict[str, str]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")


def cache_to_decimal(cache: dict[str, dict[str, str]], isin: str) -> dict[date, Decimal]:
    raw = cache.get(isin, {})
    out = {}
    for d_str, p_str in raw.items():
        try:
            out[date.fromisoformat(d_str)] = Decimal(p_str)
        except (ValueError, ArithmeticError) as e:
            # Corrupt cache entry — skip it, the price will simply be refetched.
            print(f"prices: skipping bad cache entry {isin}/{d_str}: {e}")
            continue
    return out


def merge_into_cache(cache: dict[str, dict[str, str]], isin: str, closes: dict[date, Decimal]) -> None:
    bucket = cache.setdefault(isin, {})
    for d, p in closes.items():
        bucket[d.isoformat()] = str(p)


def fetch_all(
    instruments: dict[str, dict],
    start_by_isin: dict[str, date],
    as_of: date,
    cache_path: Path,
    offline: bool,
    end_by_isin: dict[str, date] | None = None,
) -> tuple[dict[str, dict[date, Decimal]], list[str]]:
    """Return {isin: {date: close}} and a list of warning strings.

    Uses the cache when offline or when a fetch fails. Never raises.
    `end_by_isin` lets callers fetch a shorter window per ISIN (e.g. a
    sold-out position doesn't need quotes past its sell date); isins
    absent from it fetch through `as_of` as before.
    """
    cache = load_cache(cache_path)
    closes_by_isin: dict[str, dict[date, Decimal]] = {}
    warnings: list[str] = []
    dirty = False
    end_by_isin = end_by_isin or {}

    for isin, start in start_by_isin.items():
        end = end_by_isin.get(isin, as_of)
        inst = instruments.get(isin)
        cached = cache_to_decimal(cache, isin)
        if not inst or "ticker" not in inst:
            if cached:
                closes_by_isin[isin] = cached
                warnings.append(f"{isin}: no ticker configured in config.yaml, using cached prices only")
            else:
                warnings.append(f"{isin}: no ticker configured in config.yaml — no price available")
            continue

        ticker = inst["ticker"]
        if offline:
            if cached:
                closes_by_isin[isin] = cached
                warnings.append(f"{isin}: --offline, using cached prices (last: {max(cached)})")
            else:
                warnings.append(f"{isin}: --offline and no cache — n/a")
            continue

        try:
            fetched = get_closes(ticker, start, end)
        except PriceFetchError as e:
            if cached:
                closes_by_isin[isin] = cached
                warnings.append(f"{isin}: {e}; using stale cache (last: {max(cached)})")
            else:
                warnings.append(f"{isin}: {e}; no cache available — n/a")
            continue

        merged = dict(cached)
        merged.update(fetched)
        closes_by_isin[isin] = merged
        merge_into_cache(cache, isin, fetched)
        dirty = True

    if dirty:
        save_cache(cache_path, cache)

    return closes_by_isin, warnings


def close_on_or_before(closes: dict[date, Decimal], d: date, max_lookback: int = 10) -> tuple[Decimal, date] | None:
    """Carry forward the last known close for weekends/holidays."""
    for back in range(max_lookback + 1):
        day = d - timedelta(days=back)
        if day in closes:
            return closes[day], day
    return None


@dataclass
class PriceSeries:
    """Resolved daily price for one ISIN, with provenance per day.

    docs/dev/refactor-concept.md §2.3: market close wins when it exists;
    otherwise an "implied" price derived from the ISIN's own trade amounts
    fills the gap inside its holding period. Never silently better than
    the facts: days outside both have no entry at all.
    """

    closes: dict[date, Decimal] = field(default_factory=dict)
    source: dict[date, str] = field(default_factory=dict)  # "market" | "implied"

    def close_on_or_before(self, d: date, max_lookback: int = 10) -> tuple[Decimal, date, str] | None:
        for back in range(max_lookback + 1):
            day = d - timedelta(days=back)
            if day in self.closes:
                return self.closes[day], day, self.source.get(day, "market")
        return None

    @property
    def implied_days(self) -> int:
        return sum(1 for s in self.source.values() if s == "implied")


def _implied_price_on(points: list[tuple[date, Decimal]], day: date) -> Decimal | None:
    """Linear interpolation between trade execution prices, flat after the last.

    `points` must be sorted by date. Returns None before the first trade
    (there's nothing to imply from yet).
    """
    if not points:
        return None
    if day <= points[0][0]:
        return points[0][1] if day == points[0][0] else None
    if day >= points[-1][0]:
        return points[-1][1]
    for (d0, p0), (d1, p1) in zip(points, points[1:], strict=False):
        if d0 <= day <= d1:
            if d1 == d0:
                return p1
            span = (d1 - d0).days
            frac = Decimal((day - d0).days) / Decimal(span)
            return p0 + (p1 - p0) * frac
    return None


def resolve_closes(
    instruments: dict[str, dict],
    trade_points_by_isin: dict[str, list[tuple[date, Decimal]]],
    holding_spans_by_isin: dict[str, list[tuple[date, date | None]]],
    as_of: date,
    cache_path: Path,
    offline: bool,
) -> tuple[dict[str, PriceSeries], list[str]]:
    """Resolve a full daily PriceSeries for every ISIN that was ever held.

    Market data (Yahoo, cache-first) is fetched for every ISIN that has a
    configured ticker, over its own holding window only (§2.4). ISINs
    without a ticker — or days a ticker's data doesn't cover — fall back to
    §2.3's implied price from the ISIN's own trade amounts, inside its
    holding period only. Days with neither are left unpriced; callers must
    treat that as "missing", never as zero.
    """
    start_by_isin: dict[str, date] = {}
    end_by_isin: dict[str, date] = {}
    for isin, spans in holding_spans_by_isin.items():
        if not spans:
            continue
        start_by_isin[isin] = min(s for s, _ in spans)
        last_end = max((e or as_of) for _, e in spans)
        # a few days of slack past a sell-out so close_on_or_before carry-
        # forward has something to land on right at the boundary
        end_by_isin[isin] = min(as_of, last_end + timedelta(days=5))

    market_closes, fetch_warnings = fetch_all(instruments, start_by_isin, as_of, cache_path, offline, end_by_isin=end_by_isin)

    series_by_isin: dict[str, PriceSeries] = {}
    warnings: list[str] = list(fetch_warnings)
    for isin, spans in holding_spans_by_isin.items():
        series = PriceSeries()
        market = market_closes.get(isin, {})
        points = sorted(trade_points_by_isin.get(isin, []))
        has_ticker = bool((instruments.get(isin) or {}).get("ticker"))
        implied_used = False
        for span_start, span_end in spans:
            day = span_start
            last_day = span_end or as_of
            while day <= last_day:
                if day in market:
                    series.closes[day] = market[day]
                    series.source[day] = "market"
                else:
                    implied = _implied_price_on(points, day)
                    if implied is not None:
                        series.closes[day] = implied
                        series.source[day] = "implied"
                        implied_used = True
                day += timedelta(days=1)
        series_by_isin[isin] = series
        if implied_used and not has_ticker:
            warnings.append(f"{isin}: no ticker configured — using implied prices from trade amounts (≈)")
        elif implied_used:
            warnings.append(f"{isin}: market data incomplete — gaps filled with implied prices (≈)")

    return series_by_isin, warnings
