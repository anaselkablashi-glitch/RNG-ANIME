"""Position sizing. Pure arithmetic, no broker calls."""
from __future__ import annotations

import math

from .models import Position, Side, SymbolSpec


def pnl(spec: SymbolSpec, side: Side, lots: float, open_price: float, close_price: float) -> float:
    move = close_price - open_price if side is Side.BUY else open_price - close_price
    return move / spec.tick_size * spec.tick_value * lots


def pnl_at_stop(spec: SymbolSpec, pos: Position) -> float:
    """P&L of a position if its stop loss is hit (unlimited loss if it has none)."""
    if not pos.sl:
        return -math.inf
    return pnl(spec, pos.side, pos.lots, pos.open_price, pos.sl)


def split_lots(
    spec: SymbolSpec, side: Side, price: float, sl: float, risk: float, max_legs: int
) -> list[float]:
    """Split the lot size that loses at most `risk` at the stop into up to
    `max_legs` positions (one per target). Returns [] when even one minimum
    lot would lose more than `risk`."""
    loss_per_lot = -pnl(spec, side, 1.0, price, sl)
    if loss_per_lot <= 0 or risk <= 0 or max_legs <= 0:
        return []
    step = spec.volume_step
    steps = math.floor(risk / loss_per_lot / step + 1e-9)
    min_steps = max(1, round(spec.volume_min / step))
    legs = min(max_legs, steps // min_steps)
    if legs == 0:
        return []
    max_steps = math.floor(spec.volume_max / step + 1e-9)
    base, extra = divmod(steps, legs)
    decimals = max(0, -math.floor(math.log10(step) + 1e-9))
    return [round(min(base + (i < extra), max_steps) * step, decimals) for i in range(legs)]
