"""Broker interface, plus a simulated broker for tests and backtests."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from .models import Account, OrderResult, Position, Quote, Side, SymbolSpec
from .risk import pnl


class Broker(Protocol):
    def now(self) -> datetime: ...  # broker server time, naive
    def quote(self, symbol: str) -> Quote | None: ...
    def spec(self, symbol: str) -> SymbolSpec: ...
    def account(self) -> Account: ...
    def realized_today(self) -> float: ...  # closed P&L since server midnight
    def positions(self) -> list[Position]: ...  # the bot's own positions only
    def market_order(
        self, symbol: str, side: Side, lots: float, sl: float, tp: float, comment: str
    ) -> OrderResult: ...
    def modify(self, ticket: int, sl: float, tp: float) -> OrderResult: ...
    def close(self, ticket: int) -> OrderResult: ...


GOLD = SymbolSpec(tick_size=0.01, tick_value=1.0, volume_min=0.01, volume_step=0.01, volume_max=50.0)
NAS100 = SymbolSpec(tick_size=0.01, tick_value=0.01, volume_min=0.1, volume_step=0.1, volume_max=100.0)


@dataclass(frozen=True)
class ClosedTrade:
    ticket: int
    symbol: str
    side: Side
    lots: float
    open_time: datetime
    close_time: datetime
    open_price: float
    close_price: float
    profit: float
    reason: str  # "sl", "tp" or "close"
    comment: str


class SimBroker:
    """Fills market orders at the current quote and triggers SL/TP on each new
    quote. A stop loss fills at the quote that crossed it, so gaps cost extra,
    as they do live."""

    def __init__(self, specs: dict[str, SymbolSpec] | None = None, balance: float = 6000.0):
        self.specs = specs or {"XAUUSD": GOLD, "NAS100": NAS100}
        self.balance = balance
        self.quotes: dict[str, Quote] = {}
        self.open: dict[int, Position] = {}
        self.meta: dict[int, tuple[datetime, str]] = {}  # ticket -> (open time, comment)
        self.closed: list[ClosedTrade] = []
        self.time = datetime(2000, 1, 1)
        self.next_ticket = 1000
        self.reject_orders = 0  # fail this many upcoming market orders
        self.reject_modifies = 0
        self.slippage = 0.0  # adverse fill slippage on market orders, in price

    # --- driving the simulation -------------------------------------------
    def feed(self, symbol: str, when: datetime, bid: float, ask: float | None = None) -> None:
        self.time = when
        self.quotes[symbol] = Quote(bid, bid if ask is None else ask, when)
        for pos in [p for p in self.open.values() if p.symbol == symbol]:
            out = bid if pos.side is Side.BUY else self.quotes[symbol].ask
            sign = 1 if pos.side is Side.BUY else -1
            if pos.sl and sign * (out - pos.sl) <= 0:
                self._close(pos, out, "sl")
            elif pos.tp and sign * (out - pos.tp) >= 0:
                self._close(pos, pos.tp, "tp")

    def _close(self, pos: Position, price: float, reason: str) -> None:
        profit = pnl(self.specs[pos.symbol], pos.side, pos.lots, pos.open_price, price)
        opened, comment = self.meta.pop(pos.ticket)
        del self.open[pos.ticket]
        self.balance += profit
        self.closed.append(
            ClosedTrade(pos.ticket, pos.symbol, pos.side, pos.lots, opened, self.time,
                        pos.open_price, price, profit, reason, comment)
        )

    def _mark(self, pos: Position) -> Position:
        q = self.quotes[pos.symbol]
        out = q.bid if pos.side is Side.BUY else q.ask
        return replace(pos, profit=pnl(self.specs[pos.symbol], pos.side, pos.lots, pos.open_price, out))

    # --- Broker interface ---------------------------------------------------
    def now(self) -> datetime:
        return self.time

    def quote(self, symbol: str) -> Quote | None:
        return self.quotes.get(symbol)

    def spec(self, symbol: str) -> SymbolSpec:
        return self.specs[symbol]

    def account(self) -> Account:
        floating = sum(self._mark(p).profit for p in self.open.values())
        return Account(self.balance, self.balance + floating)

    def realized_today(self) -> float:
        today = self.time.date()
        return sum(t.profit for t in self.closed if t.close_time.date() == today)

    def positions(self) -> list[Position]:
        return [self._mark(p) for p in self.open.values()]

    def market_order(self, symbol, side, lots, sl, tp, comment) -> OrderResult:
        if self.reject_orders > 0:
            self.reject_orders -= 1
            return OrderResult(False, error="simulated rejection")
        q = self.quotes[symbol]
        price = q.ask + self.slippage if side is Side.BUY else q.bid - self.slippage
        ticket = self.next_ticket
        self.next_ticket += 1
        self.open[ticket] = Position(ticket, symbol, side, lots, price, sl, tp, 0.0)
        self.meta[ticket] = (self.time, comment)
        return OrderResult(True, ticket, price)

    def modify(self, ticket, sl, tp) -> OrderResult:
        if self.reject_modifies > 0:
            self.reject_modifies -= 1
            return OrderResult(False, error="simulated rejection")
        if ticket not in self.open:
            return OrderResult(False, error="position not found")
        self.open[ticket] = replace(self.open[ticket], sl=sl, tp=tp)
        return OrderResult(True, ticket)

    def close(self, ticket) -> OrderResult:
        pos = self.open.get(ticket)
        if pos is None:
            return OrderResult(False, error="position not found")
        q = self.quotes[pos.symbol]
        price = q.bid if pos.side is Side.BUY else q.ask
        self._close(pos, price, "close")
        return OrderResult(True, ticket, price)
