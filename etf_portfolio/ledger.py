"""Replay classified events into positions, cash and realized gains.

Phase 1 of docs/dev/portfolio-report-concept.md §5.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .classify import Event

CENT = Decimal("0.01")


@dataclass
class Lot:
    date: date
    qty: Decimal
    cost: Decimal  # total cost of this lot (positive)


@dataclass
class Position:
    isin: str
    name: str | None
    qty: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal(0)

    @property
    def avg_cost(self) -> Decimal | None:
        if self.qty == 0:
            return None
        return self.cost_basis / self.qty


@dataclass
class BuyRecord:
    """A buy transaction exactly as executed, independent of `lots`.

    `lots` is mutated/consumed by later sells (FIFO), so a later-sold buy can
    end up with qty 0 or vanish from `lots` entirely. Anything that needs the
    original executed buy (e.g. execution-vs-close checks) must use this, not
    `lots` — a real bug: a NVIDIA buy later sold 3 days afterwards was silently
    skipped by an execution-vs-close check that read `lots`.
    """

    date: date
    isin: str
    name: str | None
    qty: Decimal
    cost: Decimal


@dataclass
class SellRecord:
    date: date
    isin: str
    name: str | None
    qty: Decimal
    proceeds: Decimal
    cost: Decimal

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost


@dataclass
class DqIssue:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class LedgerResult:
    positions_now: dict[str, Position] = field(default_factory=dict)
    lots: dict[str, list[Lot]] = field(default_factory=lambda: defaultdict(list))
    realized: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    buys: list[BuyRecord] = field(default_factory=list)
    sells: list[SellRecord] = field(default_factory=list)
    flows: list[tuple[date, Decimal]] = field(default_factory=list)  # DEPOSIT/WITHDRAWAL, booking date
    cash: Decimal = Decimal(0)
    cash_ts: list[tuple[date, Decimal]] = field(default_factory=list)
    income: dict[str, dict[str, Decimal]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Decimal))
    )  # income["YYYY-MM"]["dividend"|"tax"|"tax_vap"|"fee"] = amount
    qty_history: list[tuple[date, str, Decimal]] = field(default_factory=list)  # (date, isin, qty_after)
    names: dict[str, str] = field(default_factory=dict)
    dq: list[DqIssue] = field(default_factory=list)
    vap_checks: list[dict] = field(default_factory=list)


def qty_at(history: list[tuple[date, str, Decimal]], isin: str, as_of: date) -> Decimal:
    """Latest known qty for isin at or before as_of, from qty_history (sorted)."""
    result = Decimal(0)
    for d, i, q in history:
        if i != isin or d > as_of:
            continue
        result = q
    return result


def holding_periods(qty_history: list[tuple[date, str, Decimal]]) -> dict[str, list[tuple[date, date | None]]]:
    """Per ISIN, the [first_held, last_held_or_None] spans implied by qty_history.

    A span's end is None while the position is still open (as of the last
    qty_history entry for that ISIN). Re-buying after fully selling out
    starts a new span — used for price-fetch windows and implied pricing,
    where "held" only means non-zero qty, not necessarily a single run.
    """
    by_isin: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for d, isin, qty_after in sorted(qty_history, key=lambda t: t[0]):
        by_isin[isin].append((d, qty_after))

    out: dict[str, list[tuple[date, date | None]]] = {}
    for isin, events in by_isin.items():
        spans: list[tuple[date, date | None]] = []
        open_start: date | None = None
        prev_qty = Decimal(0)
        for d, qty_after in events:
            if prev_qty == 0 and qty_after != 0:
                open_start = d
            elif prev_qty != 0 and qty_after == 0 and open_start is not None:
                spans.append((open_start, d))
                open_start = None
            prev_qty = qty_after
        if open_start is not None:
            spans.append((open_start, None))
        out[isin] = spans
    return out


def build_ledger(events: list[Event]) -> LedgerResult:
    res = LedgerResult()
    cash_running = Decimal(0)

    ordered = sorted(events, key=lambda e: (e.row.booking, e.row.idx))

    for ev in ordered:
        row = ev.row
        cash_running += row.amount
        month = row.booking.strftime("%Y-%m")

        if ev.isin and ev.name:
            res.names[ev.isin] = ev.name

        if ev.kind == "BUY":
            isin = ev.isin or "UNKNOWN_ISIN"
            qty = ev.qty or Decimal(0)
            cost = -row.amount
            pos = res.positions_now.setdefault(isin, Position(isin=isin, name=ev.name))
            pos.qty += qty
            pos.cost_basis += cost
            if ev.name:
                pos.name = ev.name
            res.lots[isin].append(Lot(date=row.booking, qty=qty, cost=cost))
            res.buys.append(BuyRecord(date=row.booking, isin=isin, name=ev.name, qty=qty, cost=cost))
            res.qty_history.append((row.booking, isin, pos.qty))

        elif ev.kind == "SELL":
            isin = ev.isin or "UNKNOWN_ISIN"
            qty = ev.qty or Decimal(0)
            proceeds = row.amount
            pos = res.positions_now.setdefault(isin, Position(isin=isin, name=ev.name))
            remaining = qty
            consumed_cost = Decimal(0)
            lots = res.lots[isin]
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(lot.qty, remaining)
                if lot.qty > 0:
                    consumed_cost += lot.cost * (take / lot.qty)
                    lot.cost -= lot.cost * (take / lot.qty)
                lot.qty -= take
                remaining -= take
                if lot.qty <= 0:
                    lots.pop(0)
            # Fractional FIFO splitting on partial sells leaves Decimal
            # noise (e.g. 59.999999999998) in the consumed cost; quantize at
            # this realized boundary — a cent is the real-world resolution
            # of a EUR cost, anything finer is rounding artifact, not signal.
            consumed_cost = consumed_cost.quantize(CENT, rounding=ROUND_HALF_UP)
            if remaining > 0:
                res.dq.append(
                    DqIssue(
                        "error",
                        f"{isin}: sold {qty} but only had {qty - remaining} in FIFO lots "
                        f"(row {row.idx}, {row.booking.isoformat()})",
                    )
                )
            pos.qty -= qty
            pos.cost_basis -= consumed_cost
            if pos.qty < 0:
                res.dq.append(
                    DqIssue("error", f"{isin}: position went negative ({pos.qty}) after row {row.idx}")
                )
            if ev.name:
                pos.name = ev.name
            res.realized[isin] += proceeds - consumed_cost
            res.sells.append(SellRecord(date=row.booking, isin=isin, name=ev.name, qty=qty, proceeds=proceeds, cost=consumed_cost))
            res.qty_history.append((row.booking, isin, pos.qty))

        elif ev.kind in ("DEPOSIT", "WITHDRAWAL"):
            res.flows.append((row.booking, row.amount))

        elif ev.kind == "DIVIDEND":
            res.income[month]["dividend"] += row.amount

        elif ev.kind == "TAX":
            res.income[month]["tax"] += row.amount

        elif ev.kind == "TAX_VAP":
            res.income[month]["tax_vap"] += row.amount
            if ev.isin:
                prior_year_end = date(row.booking.year - 1, 12, 31)
                reconstructed = qty_at(res.qty_history, ev.isin, prior_year_end)
                stated = ev.qty or Decimal(0)
                diff = abs(reconstructed - stated)
                res.vap_checks.append(
                    {
                        "isin": ev.isin,
                        "name": ev.name,
                        "date": row.booking,
                        "checkpoint": prior_year_end,
                        "stated_qty": stated,
                        "reconstructed_qty": reconstructed,
                        "diff": diff,
                        "ok": diff <= Decimal("0.001"),
                    }
                )

        elif ev.kind == "FEE_INTEREST":
            res.income[month]["fee"] += row.amount

        if row.status != "gebucht":
            res.dq.append(DqIssue("warning", f"row {row.idx}: non-gebucht status {row.status!r}"))
        if row.storno:
            res.dq.append(DqIssue("warning", f"row {row.idx}: 'Betrag storniert' set ({row.storno}), not auto-handled"))

        res.cash_ts.append((row.booking, cash_running))

    res.cash = cash_running
    # Drop fully-sold-out positions from the "now" view (qty history is kept
    # separately for VAP checks above). FIFO lot splitting on fractional
    # quantities can leave epsilon-scale Decimal rounding noise (e.g. 4E-25)
    # on both qty and cost_basis after the last share is sold — a true zero
    # position is anything below this threshold, not just exact zero.
    dust = Decimal("0.0000001")
    for isin in list(res.positions_now):
        pos = res.positions_now[isin]
        if abs(pos.qty) <= dust:
            del res.positions_now[isin]

    return res
