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
from datetime import date, datetime, timedelta
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
        d = datetime.utcfromtimestamp(ts).date()
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
) -> tuple[dict[str, dict[date, Decimal]], list[str]]:
    """Return {isin: {date: close}} and a list of warning strings.

    Uses the cache when offline or when a fetch fails. Never raises.
    """
    cache = load_cache(cache_path)
    closes_by_isin: dict[str, dict[date, Decimal]] = {}
    warnings: list[str] = []
    dirty = False

    for isin, start in start_by_isin.items():
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
            fetched = get_closes(ticker, start, as_of)
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
