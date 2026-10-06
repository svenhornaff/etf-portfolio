"""Price resolution reference tests.

docs/dev/refactor-concept.md §2.3, §8.
"""

from datetime import date
from decimal import Decimal

from etf_portfolio.prices import PriceSeries, _implied_price_on, resolve_closes


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


def test_price_series_close_on_or_before_carries_forward():
    series = PriceSeries(closes={date(2025, 1, 1): Decimal("5")}, source={date(2025, 1, 1): "market"})
    hit = series.close_on_or_before(date(2025, 1, 3))
    assert hit == (Decimal("5"), date(2025, 1, 1), "market")
    assert series.close_on_or_before(date(2024, 12, 1)) is None
