"""Render the ctx dict into one self-contained HTML file.

docs/dev/portfolio-report-concept.md §8.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# etf_portfolio/render.py -> package dir's parent is the repo root, where
# templates/ lives (flat layout, see README.md).
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def fmt_eur(amount: Decimal | float | None, show_sign: bool = False) -> str:
    """German-style EUR formatting: 1.234,56 €. Portable, no locale module."""
    if amount is None:
        return "n/a"
    value = float(amount)
    sign = "+" if show_sign and value > 0 else ""
    s = f"{abs(value):,.2f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    out = f"{sign}{'-' if value < 0 else ''}{s} €"
    return out


def fmt_pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{decimals}f} %".replace(".", ",")


def fmt_qty(qty: Decimal | None) -> str:
    if qty is None:
        return "n/a"
    s = f"{qty.normalize():f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {obj!r}")


def to_safe_json(data: dict) -> str:
    """json.dumps with </ escaped so it can't break out of <script>."""
    return json.dumps(data, default=_json_default, ensure_ascii=False).replace("</", "<\\/")


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["eur"] = fmt_eur
    env.filters["pct"] = fmt_pct
    env.filters["qty"] = fmt_qty
    return env


def render_report(ctx: dict, out_path: Path) -> None:
    ctx = dict(ctx)
    ctx["data_json"] = to_safe_json(ctx.get("chart_data", {}))
    ctx.setdefault("goal", {})
    env = build_env()
    template = env.get_template("report.html.j2")
    html = template.render(**ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
