"""Region/sector/currency look-through, overlap, and weighted TER.

docs/dev/report-v3-concept.md §6, §9. All of this depends on per-fund
factsheet metadata (`instruments.yaml`: ter, regions, sectors, currency,
top_holdings) that this codebase cannot fabricate. Every function here is
all-or-nothing: if even one currently-held ISIN is missing the field in
question, the aggregate is `None` with a named reason — never a partial
or zero-filled number that looks real but isn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class LookthroughResult:
    available: bool
    breakdown: dict[str, float] = field(default_factory=dict)  # category -> weight
    reason: str | None = None
    coverage: float = 0.0  # share of portfolio value covered by isins that HAVE this field


def _missing_isins(holdings_weights: dict[str, float], instruments: dict[str, dict], field_name: str) -> list[str]:
    return [isin for isin in holdings_weights if not (instruments.get(isin) or {}).get(field_name)]


def lookthrough(holdings_weights: dict[str, float], instruments: dict[str, dict], field_name: str) -> LookthroughResult:
    """Weight-average a per-instrument breakdown field (regions/sectors/currency) across holdings."""
    if not holdings_weights:
        return LookthroughResult(available=False, reason="keine offenen Positionen")
    missing = _missing_isins(holdings_weights, instruments, field_name)
    if missing:
        return LookthroughResult(
            available=False,
            reason=f"Stammdaten fehlen in instruments.yaml ({field_name}) für: {', '.join(sorted(missing))}",
        )
    out: dict[str, float] = {}
    for isin, weight in holdings_weights.items():
        breakdown = instruments[isin][field_name]
        for category, share in breakdown.items():
            out[category] = out.get(category, 0.0) + weight * share
    return LookthroughResult(available=True, breakdown=out, coverage=1.0)


def weighted_ter(holdings_values: dict[str, Decimal], instruments: dict[str, dict]) -> tuple[Decimal | None, Decimal | None, str | None]:
    """(weighted_ter_pct, annual_cost_eur, reason). None/None/reason if any holding lacks `ter`."""
    if not holdings_values:
        return None, None, "keine offenen Positionen"
    missing = [isin for isin in holdings_values if (instruments.get(isin) or {}).get("ter") is None]
    if missing:
        return None, None, f"TER fehlt in instruments.yaml für: {', '.join(sorted(missing))}"
    total_value = sum(holdings_values.values(), Decimal(0))
    if not total_value:
        return None, None, "Depotwert ist 0"
    annual_cost = sum((v * Decimal(str(instruments[isin]["ter"])) for isin, v in holdings_values.items()), Decimal(0))
    return (annual_cost / total_value), annual_cost, None


def satellite_share(holdings_weights: dict[str, float], instruments: dict[str, dict]) -> float | None:
    """Share of current holdings weight tagged role: satellite. None if `role` is unset anywhere."""
    if not holdings_weights:
        return None
    missing = [isin for isin in holdings_weights if not (instruments.get(isin) or {}).get("role")]
    if missing:
        return None
    return sum(w for isin, w in holdings_weights.items() if instruments[isin]["role"] == "satellite")


def overlap_matrix(holdings_weights: dict[str, float], instruments: dict[str, dict]) -> tuple[list[dict] | None, str | None]:
    """Pairwise top-holdings overlap (Jaccard-ish on weight) between held instruments.

    None if any holding is missing `top_holdings` in instruments.yaml.
    """
    isins = list(holdings_weights)
    if len(isins) < 2:
        return None, "weniger als 2 Positionen"
    missing = [isin for isin in isins if not (instruments.get(isin) or {}).get("top_holdings")]
    if missing:
        return None, f"Top-Holdings fehlen in instruments.yaml für: {', '.join(sorted(missing))}"
    pairs = []
    for i, a in enumerate(isins):
        for b in isins[i + 1 :]:
            holdings_a = instruments[a]["top_holdings"]
            holdings_b = instruments[b]["top_holdings"]
            shared = set(holdings_a) & set(holdings_b)
            overlap_weight = sum(min(holdings_a[k], holdings_b[k]) for k in shared)
            pairs.append({"a": a, "b": b, "overlap": overlap_weight, "shared_names": sorted(shared)})
    return pairs, None
