"""Health-check traffic lights against `config.yaml: targets`.

docs/dev/report-v3-concept.md §3. Shows deviation from *your own*
configured targets — never a buy/sell recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HealthCheck:
    label: str
    status: str  # "green" | "amber" | "red" | "na"
    value_text: str
    detail: str = ""


def _position_check(largest: tuple[str, float] | None, names: dict[str, str], max_position: float) -> HealthCheck:
    if not largest:
        return HealthCheck("Größte Position", "na", "n/a", "keine offenen Positionen")
    isin, weight = largest
    name = names.get(isin, isin)
    if weight <= max_position:
        status = "green"
    elif weight <= max_position + 0.05:
        status = "amber"
    else:
        status = "red"
    return HealthCheck("Größte Position", status, f"{weight * 100:.1f} %", f"{name} · Ziel ≤ {max_position * 100:.0f} %")


def _top3_check(top3: float | None) -> HealthCheck:
    if top3 is None:
        return HealthCheck("Top-3", "na", "n/a")
    status = "green" if top3 <= 0.60 else "amber" if top3 <= 0.80 else "red"
    return HealthCheck("Top-3", status, f"{top3 * 100:.1f} %", "Ziel ≤ 60 %")


def _satellite_check(satellite_share: float | None, targets: dict) -> HealthCheck:
    allocation = (targets or {}).get("allocation") or {}
    target_satellite = allocation.get("satellite")
    band = (targets or {}).get("band_pp", 5) / 100.0
    if satellite_share is None or target_satellite is None:
        return HealthCheck("Satellite-Anteil", "na", "n/a", "`role` fehlt in instruments.yaml für die gehaltenen ISINs")
    delta = satellite_share - target_satellite
    status = "green" if abs(delta) <= band else "amber" if abs(delta) <= 2 * band else "red"
    sign = "+" if delta >= 0 else ""
    return HealthCheck("Satellite-Anteil", status, f"{satellite_share * 100:.1f} %", f"{sign}{delta * 100:.1f} pp vs. Ziel {target_satellite * 100:.0f} %")


def _ter_check(weighted_ter: float | None, max_ter: float) -> HealthCheck:
    if weighted_ter is None:
        return HealthCheck("Gewichtete TER", "na", "n/a — Stammdaten fehlen", "`ter` fehlt in instruments.yaml")
    status = "green" if weighted_ter <= 0.0025 else "amber" if weighted_ter <= max_ter * 1.6 else "red"
    return HealthCheck("Gewichtete TER", status, f"{weighted_ter * 100:.2f} %", f"Ziel ≤ {max_ter * 100:.2f} %")


def _drawdown_check(current_drawdown: float | None) -> HealthCheck:
    if current_drawdown is None:
        return HealthCheck("Drawdown aktuell", "na", "n/a")
    status = "green" if current_drawdown > -0.10 else "amber" if current_drawdown > -0.20 else "red"
    return HealthCheck("Drawdown aktuell", status, f"{current_drawdown * 100:.1f} %")


def _data_quality_check(implied_share: float, unresolved: int) -> HealthCheck:
    if unresolved > 0:
        status = "red"
    elif implied_share < 0.20:
        status = "green"
    elif implied_share < 0.50:
        status = "amber"
    else:
        status = "red"
    return HealthCheck(
        "Datenqualität",
        status,
        f"{implied_share * 100:.0f} % geschätzt",
        f"{unresolved} ungelöst" if unresolved else "0 ungelöst",
    )


def compute_health_checks(
    largest_position: tuple[str, float] | None,
    holdings_names: dict[str, str],
    top3_share: float | None,
    satellite_share: float | None,
    weighted_ter: float | None,
    current_drawdown: float | None,
    implied_share: float,
    unresolved_count: int,
    targets: dict,
) -> list[HealthCheck]:
    targets = targets or {}
    return [
        _position_check(largest_position, holdings_names, targets.get("max_position", 0.35)),
        _top3_check(top3_share),
        _satellite_check(satellite_share, targets),
        _ter_check(weighted_ter, targets.get("max_ter", 0.0025)),
        _drawdown_check(current_drawdown),
        _data_quality_check(implied_share, unresolved_count),
    ]
