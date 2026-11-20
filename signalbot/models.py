from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Signal:
    symbol: str  # canonical name: "XAUUSD", "NAS100"
    side: Side
    entry_low: float
    entry_high: float
    sl: float
    tps: tuple[float, ...]  # nearest first, swing targets already removed
    tags: frozenset[str] = frozenset()
    dropped_tps: tuple[float, ...] = ()  # targets marked "swing"
    tps_hit: int = 0  # targets already ticked off in the message


@dataclass(frozen=True)
class SymbolSpec:
    tick_size: float
    tick_value: float  # account-currency value of one tick for 1.00 lot
    volume_min: float
    volume_step: float
    volume_max: float
    digits: int = 2
    min_stop_distance: float = 0.0  # broker's minimum SL/TP distance, in price


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    time: datetime  # broker server time, naive


@dataclass(frozen=True)
class Account:
    balance: float
    equity: float


@dataclass(frozen=True)
class Position:
    ticket: int
    symbol: str  # canonical name
    side: Side
    lots: float
    open_price: float
    sl: float  # 0.0 = none
    tp: float  # 0.0 = none
    profit: float  # floating P&L in account currency


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    ticket: int = 0
    price: float = 0.0
    error: str = ""
