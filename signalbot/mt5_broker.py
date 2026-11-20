"""MetaTrader 5 adapter. Windows only: the MT5 terminal must be running,
logged in, and have the "Algo Trading" button switched on."""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import MetaTrader5 as mt5

from .config import Config
from .models import Account, OrderResult, Position, Quote, Side, SymbolSpec

MAGIC = 20261120  # tags every order so the bot only ever touches its own positions
_RETRY = {10004, 10020, 10021}  # requote, price changed, no quotes: nothing was filled


class MT5Broker:
    def __init__(self, cfg: Config):
        if not mt5.initialize():
            raise RuntimeError(f"cannot reach the MetaTrader 5 terminal: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MetaTrader 5 is not logged in to an account")
        if info.login != cfg.account.login:
            raise RuntimeError(
                f"MT5 is logged in to account {info.login}, but config.toml allows "
                f"only {cfg.account.login}. Refusing to trade."
            )
        if cfg.account.require_demo and info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("this is not a demo account and require_demo is on")
        if info.margin_mode != mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
            raise RuntimeError("the account must be a hedging account (one position per target)")
        if not mt5.terminal_info().trade_allowed:
            raise RuntimeError("switch on the 'Algo Trading' button in MetaTrader 5")
        self.cfg = cfg
        self.names = {canon: s.broker_symbol for canon, s in cfg.symbols.items()}
        self.canon = {name: canon for canon, name in self.names.items()}
        for name in self.names.values():
            if not mt5.symbol_select(name, True):
                raise RuntimeError(f"symbol '{name}' does not exist at this broker; fix config.toml")

    # --- reading ---------------------------------------------------------------
    def now(self) -> datetime:
        ticks = [mt5.symbol_info_tick(name) for name in self.names.values()]
        latest = max((t.time for t in ticks if t is not None), default=0)
        if not latest:
            raise RuntimeError("no prices from MetaTrader 5")
        return datetime.fromtimestamp(latest, timezone.utc).replace(tzinfo=None)

    def quote(self, symbol: str) -> Quote | None:
        tick = mt5.symbol_info_tick(self.names[symbol])
        if tick is None or not tick.bid or not tick.ask:
            return None
        return Quote(tick.bid, tick.ask, datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None))

    def spec(self, symbol: str) -> SymbolSpec:
        i = mt5.symbol_info(self.names[symbol])
        return SymbolSpec(
            tick_size=i.trade_tick_size,
            tick_value=i.trade_tick_value_loss or i.trade_tick_value,
            volume_min=i.volume_min,
            volume_step=i.volume_step,
            volume_max=i.volume_max,
            digits=i.digits,
            min_stop_distance=i.trade_stops_level * i.point,
        )

    def account(self) -> Account:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"cannot read the account: {mt5.last_error()}")
        return Account(info.balance, info.equity)

    def realized_today(self) -> float:
        midnight = calendar.timegm(self.now().date().timetuple())
        deals = mt5.history_deals_get(midnight, midnight + 2 * 86400)
        if deals is None:
            raise RuntimeError(f"cannot read today's deals: {mt5.last_error()}")
        trades = (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
        return sum(d.profit + d.commission + d.swap + d.fee for d in deals if d.type in trades)

    def positions(self) -> list[Position]:
        raw = mt5.positions_get()
        if raw is None:
            raise RuntimeError(f"cannot read positions: {mt5.last_error()}")
        return [
            Position(
                ticket=p.ticket,
                symbol=self.canon[p.symbol],
                side=Side.BUY if p.type == mt5.POSITION_TYPE_BUY else Side.SELL,
                lots=p.volume,
                open_price=p.price_open,
                sl=p.sl,
                tp=p.tp,
                profit=p.profit + p.swap,
            )
            for p in raw
            if p.magic == MAGIC and p.symbol in self.canon
        ]

    # --- trading ---------------------------------------------------------------
    def _filling(self, info) -> int:
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _deal(self, name: str, side: Side, lots: float, slippage_factor: int = 1, **extra) -> OrderResult:
        info = mt5.symbol_info(name)
        slippage = self.cfg.symbols[self.canon[name]].max_slippage
        result = OrderResult(False, error="not sent")
        for _ in range(2):
            tick = mt5.symbol_info_tick(name)
            if tick is None:
                return OrderResult(False, error="no price")
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": name,
                "volume": lots,
                "type": mt5.ORDER_TYPE_BUY if side is Side.BUY else mt5.ORDER_TYPE_SELL,
                "price": tick.ask if side is Side.BUY else tick.bid,
                "deviation": int(round(slippage / info.point)) * slippage_factor,
                "magic": MAGIC,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
                **extra,
            }
            res = mt5.order_send(request)
            if res is None:
                return OrderResult(False, error=f"no answer from MT5: {mt5.last_error()}")
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                return OrderResult(True, res.order, res.price)
            result = OrderResult(False, error=f"{res.retcode} {res.comment}")
            if res.retcode not in _RETRY:
                break
        return result

    def market_order(self, symbol, side, lots, sl, tp, comment) -> OrderResult:
        name = self.names[symbol]
        digits = mt5.symbol_info(name).digits
        return self._deal(name, side, lots, sl=round(sl, digits), tp=round(tp, digits), comment=comment[:31])

    def modify(self, ticket, sl, tp) -> OrderResult:
        found = mt5.positions_get(ticket=ticket)
        if not found:
            return OrderResult(False, error="position not found")
        digits = mt5.symbol_info(found[0].symbol).digits
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": found[0].symbol,
            "sl": round(sl, digits),
            "tp": round(tp, digits),
        })
        if res is None:
            return OrderResult(False, error=f"no answer from MT5: {mt5.last_error()}")
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, error=f"{res.retcode} {res.comment}")
        return OrderResult(True, ticket)

    def close(self, ticket) -> OrderResult:
        found = mt5.positions_get(ticket=ticket)
        if not found:
            return OrderResult(False, error="position not found")
        pos = found[0]
        opposite = Side.SELL if pos.type == mt5.POSITION_TYPE_BUY else Side.BUY
        # Getting out matters more than the last few cents: allow 10x the slippage.
        return self._deal(pos.symbol, opposite, pos.volume, slippage_factor=10,
                          position=ticket, comment="signalbot close")
