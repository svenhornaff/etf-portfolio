"""Price resolution reference tests.

docs/dev/refactor-concept.md §2.3, §8.
"""

from datetime import date
from decimal import Decimal

from etf_portfolio.prices import (
    PriceSeries,
    _implied_price_on,
    load_cache,
    merge_into_cache,
    resolve_closes,
    save_cache,
)


def test_implied_price_interpolates_between_trades():
    points = [(date(2025, 1, 1), Decimal("10")), (date(2025, 1, 11), Decimal("20"))]
    # halfway between the two trades -> halfway between the two prices
    assert _implied_price_on(points, date(2025, 1, 6)) == Decimal("15")


def test_implied_price_flat_after_last_trade():
    points = [(date(2025, 1, 1), Decimal("10")), (date(2025, 1, 11), Decimal("20"))]
    assert _implied_price_on(points, date(2025, 6, 1)) == Decimal("20")


def test_implied_price_none_before_first_trade():
    points = [(date(2025, 1, 1), Decimal("10"))]
    assert _implied_price_on([], date(2025, 1, 1)) is None
    assert _implied_price_on(points, date(2024, 12, 31)) is None


def test_resolve_closes_market_overrides_implied(tmp_path):
    """A configured ticker's real close wins over the implied price on the same day."""
    instruments = {"XX": {"ticker": "DOES.NOT.EXIST"}}  # forces no network, offline path below
    trade_points = {"XX": [(date(2025, 1, 1), Decimal("10")), (date(2025, 1, 5), Decimal("14"))]}
    spans: dict[str, list[tuple[date, date | None]]] = {"XX": [(date(2025, 1, 1), date(2025, 1, 5))]}
    # offline with no cache -> falls through to implied only; this exercises
    # the no-market-data path end to end without hitting the network.
    cache_path = tmp_path / "prices.json"
    series_by_isin, warnings = resolve_closes(instruments, trade_points, spans, date(2025, 1, 5), cache_path, offline=True)
    series = series_by_isin["XX"]
    assert series.source[date(2025, 1, 1)] == "implied"
    assert series.closes[date(2025, 1, 1)] == Decimal("10")
    assert series.closes[date(2025, 1, 5)] == Decimal("14")
    assert any("implied prices" in w for w in warnings)


def test_resolve_closes_no_ticker_no_trades_outside_span(tmp_path):
    """Days outside the holding span are never priced, even with trade points."""
    instruments: dict = {}
    trade_points = {"XX": [(date(2025, 1, 1), Decimal("10"))]}
    spans: dict[str, list[tuple[date, date | None]]] = {"XX": [(date(2025, 1, 1), date(2025, 1, 1))]}
    cache_path = tmp_path / "prices.json"
    series_by_isin, _ = resolve_closes(instruments, trade_points, spans, date(2025, 1, 10), cache_path, offline=True)
    series = series_by_isin["XX"]
    assert date(2025, 1, 2) not in series.closes
    assert series.closes[date(2025, 1, 1)] == Decimal("10")


def test_resolve_closes_leading_gap_before_first_market_quote_uses_implied(tmp_path):
    """docs/dev/isin-ticker-resolution-concept.md: a real case (VUAA.DE's Yahoo
    history starting well after the ISIN was first held) means a single,
    one-way implied-to-market switch at the ticker's own first quote is
    allowed — days strictly before it are implied, not left unpriced.
    """
    cache_path = tmp_path / "prices.json"
    cache = load_cache(cache_path)
    # market data exists only for day 5 of a day1..day10 span
    merge_into_cache(cache, "YY", {date(2025, 1, 5): Decimal("50")})
    save_cache(cache_path, cache)

    instruments = {"YY": {"ticker": "ANY.DE"}}
    trade_points = {"YY": [(date(2025, 1, 1), Decimal("10"))]}
    spans: dict[str, list[tuple[date, date | None]]] = {"YY": [(date(2025, 1, 1), date(2025, 1, 10))]}

    series_by_isin, warnings = resolve_closes(instruments, trade_points, spans, date(2025, 1, 10), cache_path, offline=True)
    series = series_by_isin["YY"]

    # days 1-4, before the ticker's first ever market quote, are implied
    for d in range(1, 5):
        assert series.source[date(2025, 1, d)] == "implied"
        assert series.closes[date(2025, 1, d)] == Decimal("10")
    # day 5 onward is market, and stays market even where carry-forward is
    # doing the work (days 6-10 have no quote of their own) — no reverting
    # back to implied once the market series has started
    for d in range(5, 11):
        assert series.source[date(2025, 1, d)] == "market"
        assert series.closes[date(2025, 1, d)] == Decimal("50")
    assert any("market data starts 2025-01-05" in w for w in warnings)


def test_resolve_closes_never_mixes_sources_mid_span_after_market_starts(tmp_path):
    """Once the market series has started, a day it genuinely has no quote
    for (beyond the carry-forward window) stays unpriced — it is NOT
    patched with an implied price. Only the leading edge gets that
    exception, never the middle or end of a span.
    """
    cache_path = tmp_path / "prices.json"
    cache = load_cache(cache_path)
    # market data only for day 1 and day 30 — day 15 is a genuine mid-span
    # gap far beyond the 10-day carry-forward window
    merge_into_cache(cache, "YY", {date(2025, 1, 1): Decimal("10"), date(2025, 1, 30): Decimal("99")})
    save_cache(cache_path, cache)

    instruments = {"YY": {"ticker": "ANY.DE"}}
    trade_points = {"YY": [(date(2025, 1, 1), Decimal("10")), (date(2025, 1, 30), Decimal("99"))]}
    spans: dict[str, list[tuple[date, date | None]]] = {"YY": [(date(2025, 1, 1), date(2025, 1, 30))]}

    series_by_isin, _ = resolve_closes(instruments, trade_points, spans, date(2025, 1, 30), cache_path, offline=True)
    series = series_by_isin["YY"]

    # day 15 is well past the 10-day carry-forward window from day 1 and
    # before day 30 — must stay unpriced, NOT be implied-patched
    assert date(2025, 1, 15) not in series.closes
    assert "implied" not in series.source.values()


def test_resolve_closes_different_spans_can_pick_different_sources(tmp_path):
    """A re-buy after fully selling out is a new span and may independently use implied or market."""
    cache_path = tmp_path / "prices.json"
    cache = load_cache(cache_path)
    merge_into_cache(cache, "ZZ", {date(2025, 6, 1): Decimal("99")})
    save_cache(cache_path, cache)

    instruments = {"ZZ": {"ticker": "ANY.DE"}}
    trade_points = {"ZZ": [(date(2025, 1, 1), Decimal("10")), (date(2025, 6, 1), Decimal("95"))]}
    spans: dict[str, list[tuple[date, date | None]]] = {
        "ZZ": [(date(2025, 1, 1), date(2025, 1, 2)), (date(2025, 6, 1), date(2025, 6, 2))]
    }
    series_by_isin, warnings = resolve_closes(instruments, trade_points, spans, date(2025, 6, 2), cache_path, offline=True)
    series = series_by_isin["ZZ"]
    assert series.source[date(2025, 1, 1)] == "implied"  # first span: no market data at all
    assert series.source[date(2025, 6, 1)] == "market"  # second span: has market data
    assert any("different holding periods use different price sources" in w for w in warnings)


def test_price_series_close_on_or_before_carries_forward():
    series = PriceSeries(closes={date(2025, 1, 1): Decimal("5")}, source={date(2025, 1, 1): "market"})
    hit = series.close_on_or_before(date(2025, 1, 3))
    assert hit == (Decimal("5"), date(2025, 1, 1), "market")
    assert series.close_on_or_before(date(2024, 12, 1)) is None
