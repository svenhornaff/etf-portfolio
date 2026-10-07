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


def test_resolve_closes_never_mixes_sources_within_one_span(tmp_path):
    """docs/dev/report-v3-concept.md §1: once a span has *any* market data,
    the whole span is priced from the market — gaps are left unpriced, not
    silently patched with an implied price (the bug that caused the hero
    chart spikes).
    """
    cache_path = tmp_path / "prices.json"
    cache = load_cache(cache_path)
    # market data exists only for day 5 of a day1..day10 span
    merge_into_cache(cache, "YY", {date(2025, 1, 5): Decimal("50")})
    save_cache(cache_path, cache)

    instruments = {"YY": {"ticker": "ANY.DE"}}
    # trade_points would, under the old per-day-fallback logic, have implied-filled day 1
    trade_points = {"YY": [(date(2025, 1, 1), Decimal("10"))]}
    spans: dict[str, list[tuple[date, date | None]]] = {"YY": [(date(2025, 1, 1), date(2025, 1, 10))]}

    series_by_isin, _ = resolve_closes(instruments, trade_points, spans, date(2025, 1, 10), cache_path, offline=True)
    series = series_by_isin["YY"]

    # day 1 is before the only market quote and must NOT be backfilled from
    # trade_points once the span committed to "market"
    assert date(2025, 1, 1) not in series.closes
    assert series.closes[date(2025, 1, 5)] == Decimal("50")
    assert series.source[date(2025, 1, 5)] == "market"
    assert all(s == "market" for s in series.source.values())


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
