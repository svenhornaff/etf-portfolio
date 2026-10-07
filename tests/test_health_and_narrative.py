"""Regression tests for the two 'gating' bugs flagged in a report review:

- health._ter_check must use config's `targets.max_ter`, not a hardcoded value.
- narrative.build_summary must only compare against the benchmark once market
  coverage is at least 80% (implied_share < 0.20), not the old, much looser 0.80.
"""

from decimal import Decimal

from etf_portfolio.health import _ter_check
from etf_portfolio.narrative import build_summary


def test_ter_check_uses_configured_target_not_hardcoded_value():
    # A fund cheaper than a *strict* configured target should still be green.
    strict = _ter_check(weighted_ter=0.0010, max_ter=0.0010)
    assert strict.status == "green"
    # The same 0.0010 fund against a looser target is still green (it's well under).
    loose = _ter_check(weighted_ter=0.0010, max_ter=0.01)
    assert loose.status == "green"
    # Breaching a strict target by a little is amber, not silently green.
    amber = _ter_check(weighted_ter=0.0015, max_ter=0.0010)
    assert amber.status == "amber"
    # Breaching it by a lot is red.
    red = _ter_check(weighted_ter=0.01, max_ter=0.0010)
    assert red.status == "red"
    assert "0,10" in red.value_text.replace(".", ",") or "1.00" in red.value_text


def test_ter_check_na_without_data():
    result = _ter_check(weighted_ter=None, max_ter=0.0025)
    assert result.status == "na"


def test_benchmark_sentence_requires_80pct_market_coverage():
    base = {
        "value": Decimal("10000"),
        "net_contributions": Decimal("9000"),
        "gain_eur": Decimal("1000"),
        "xirr": 0.10,
        "twr": 0.10,
        "benchmark_twr": 0.08,
    }
    # Below 80% market coverage (implied_share >= 0.20): no benchmark comparison sentence.
    high_implied = build_summary({**base, "coverage_implied_share": 0.71}, {})
    assert not any("Vergleichsindex" in s for s in high_implied)

    # At/above 80% market coverage (implied_share < 0.20): the comparison appears.
    low_implied = build_summary({**base, "coverage_implied_share": 0.14}, {})
    assert any("Vergleichsindex" in s for s in low_implied)


def test_benchmark_sentence_boundary_is_020_not_080():
    base = {
        "value": Decimal("10000"),
        "net_contributions": Decimal("9000"),
        "gain_eur": Decimal("1000"),
        "xirr": 0.10,
        "twr": 0.10,
        "benchmark_twr": 0.08,
    }
    # 0.50 implied share used to pass the old (< 0.80) gate; it must not anymore.
    sentences = build_summary({**base, "coverage_implied_share": 0.50}, {})
    assert not any("Vergleichsindex" in s for s in sentences)
