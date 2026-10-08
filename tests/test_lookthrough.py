"""Tests for etf_portfolio/lookthrough.py's all-or-nothing aggregation.

docs/dev/report-v4-concept.md: added after instruments.yaml got real
factsheet data for 3 of 4 currently-held funds (TER for all 4). Uses
synthetic instrument dicts, not the real instruments.yaml, so these tests
don't break if the sourced data changes.
"""

from decimal import Decimal

from etf_portfolio.lookthrough import lookthrough, overlap_matrix, weighted_ter


def test_weighted_ter_activates_once_every_held_isin_has_ter():
    holdings_values = {"A": Decimal("1000"), "B": Decimal("3000")}
    instruments = {"A": {"ter": 0.0012}, "B": {"ter": 0.0035}}
    weighted, annual_cost, reason = weighted_ter(holdings_values, instruments)
    assert reason is None
    assert weighted is not None
    assert annual_cost is not None
    # (1000*0.0012 + 3000*0.0035) / 4000
    assert abs(float(weighted) - 0.002925) < 1e-9
    assert abs(float(annual_cost) - 11.7) < 1e-9


def test_weighted_ter_is_na_with_named_isin_if_one_holding_lacks_ter():
    holdings_values = {"A": Decimal("1000"), "B": Decimal("3000")}
    instruments = {"A": {"ter": 0.0012}, "B": {}}
    weighted, annual_cost, reason = weighted_ter(holdings_values, instruments)
    assert weighted is None
    assert annual_cost is None
    assert reason is not None
    assert "B" in reason


def test_lookthrough_regions_activates_once_every_held_isin_has_regions():
    weights = {"A": 0.6, "B": 0.4}
    instruments = {
        "A": {"regions": {"US": 0.8, "other": 0.2}},
        "B": {"regions": {"US": 0.5, "EU": 0.5}},
    }
    result = lookthrough(weights, instruments, "regions")
    assert result.available
    # 0.6*0.8 + 0.4*0.5 = 0.68
    assert abs(result.breakdown["US"] - 0.68) < 1e-9
    assert result.coverage == 1.0


def test_lookthrough_names_only_the_isins_missing_the_field():
    """Regression: before real factsheet data existed for 3/4 held funds,
    this reason always named all 4 ISINs. Now that 3 have `regions`, the
    reason must narrow to exactly the one still missing it (Boreas, in
    the real data) — not silently stay broad.
    """
    weights = {"A": 0.3, "B": 0.3, "C": 0.3, "D": 0.1}
    instruments = {
        "A": {"regions": {"US": 1.0}},
        "B": {"regions": {"US": 1.0}},
        "C": {"regions": {"US": 1.0}},
        "D": {},  # only this one is missing it
    }
    result = lookthrough(weights, instruments, "regions")
    assert not result.available
    assert result.reason is not None
    assert "D" in result.reason
    assert "A" not in result.reason
    assert "B" not in result.reason
    assert "C" not in result.reason


def test_overlap_matrix_computes_shared_min_weight():
    weights = {"A": 0.5, "B": 0.5}
    instruments = {
        "A": {"top_holdings": {"NVIDIA": 0.08, "AMD": 0.05}},
        "B": {"top_holdings": {"NVIDIA": 0.03, "TSMC": 0.07}},
    }
    pairs, reason = overlap_matrix(weights, instruments)
    assert reason is None
    assert pairs is not None
    assert len(pairs) == 1
    assert pairs[0]["shared_names"] == ["NVIDIA"]
    assert abs(pairs[0]["overlap"] - 0.03) < 1e-9  # min(0.08, 0.03)


def test_overlap_matrix_is_na_if_any_held_isin_lacks_top_holdings():
    weights = {"A": 0.5, "B": 0.5}
    instruments = {"A": {"top_holdings": {"NVIDIA": 0.08}}, "B": {}}
    pairs, reason = overlap_matrix(weights, instruments)
    assert pairs is None
    assert reason is not None
    assert "B" in reason
