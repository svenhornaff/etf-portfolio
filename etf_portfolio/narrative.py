"""Executive-summary prose: templates + conditions, no LLM at runtime.

docs/dev/report-v3-concept.md §3 Layer 1. Each sentence only fires if its
condition holds and every number it needs is actually available — a
sentence that would require fabricating or guessing a number is simply
skipped, not filled with a placeholder.
"""

from __future__ import annotations

from decimal import Decimal


def _fmt_eur(v: Decimal) -> str:
    s = f"{float(v):,.0f}".replace(",", ".")
    return f"{s} €"


def _fmt_pct(v: float, decimals: int = 1) -> str:
    s = f"{v * 100:.{decimals}f}".replace(".", ",")
    return f"{s} %"


def build_summary(k: dict, names: dict[str, str]) -> list[str]:
    """Returns the executive-summary sentences that apply, in a fixed order."""
    out: list[str] = []

    if k.get("value") is not None and k.get("net_contributions") is not None:
        gain = k.get("gain_eur")
        sentence = f"Ihr Depot steht bei {_fmt_eur(k['value'])}"
        if gain is not None:
            sentence += f", davon {_fmt_eur(gain)} Gewinn auf {_fmt_eur(k['net_contributions'])} Einzahlungen"
        sentence += "."
        out.append(sentence)

    if k.get("xirr") is not None:
        sentence = f"Seit Start erzielten Sie {_fmt_pct(k['xirr'])} p.a. (XIRR)"
        if k.get("twr") is not None and k.get("benchmark_twr") is not None and k.get("coverage_implied_share", 1.0) < 0.80:
            delta_pp = (k["twr"] - k["benchmark_twr"]) * 100
            verb = "vor" if delta_pp >= 0 else "hinter"
            delta_text = f"{abs(delta_pp):.1f}".replace(".", ",")
            sentence += f" und liegen {delta_text} pp {verb} dem Vergleichsindex bei identischen Einzahlungen"
        sentence += "."
        out.append(sentence)
    elif k.get("twr") is not None:
        out.append(f"Seit Start erzielten Sie {_fmt_pct(k['twr'])} Gesamtrendite (TWR).")

    top3 = k.get("top3_share")
    largest = k.get("largest_position")
    if top3 is not None and top3 > 0.60:
        parts = f"drei Positionen machen {_fmt_pct(top3, 0)} aus"
        if largest:
            isin, weight = largest
            parts = f"{names.get(isin, isin)} allein macht {_fmt_pct(weight, 0)} aus, drei Positionen zusammen {_fmt_pct(top3, 0)}"
        out.append(f"Das Depot ist stark konzentriert: {parts}.")

    turnover = k.get("turnover_12m")
    if turnover is not None and turnover > 1.0:
        out.append(f"Der Umschlag lag im letzten Jahr bei {_fmt_pct(turnover, 0)} — das ist eher ein Handelskonto als ein Sparplan.")

    if k.get("coverage_implied_share", 0.0) >= 0.50:
        out.append(
            f"Hinweis: {_fmt_pct(k['coverage_implied_share'], 0)} der Kurse in diesem Bericht sind geschätzt "
            "(abgeleitet aus Kaufpreisen, nicht Marktkurse) — Risikokennzahlen sind entsprechend unsicher."
        )

    return out
