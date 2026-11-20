"""Decides what to trade and manages the trades. Talks only to the Broker
interface, so the same code runs live, in tests and in backtests."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .broker import Broker
from .config import Config, parse_hhmm
from .models import Position, Side, Signal
from .parser import SignalError, parse_signal
from .risk import pnl_at_stop, split_lots
from .state import State

log = logging.getLogger("signalbot")
REDUCED_RISK_TAGS = {"high risk", "small lot"}


class Engine:
    def __init__(
        self,
        cfg: Config,
        broker: Broker,
        state: State | None = None,
        notify: Callable[[str], None] = lambda text: None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.cfg = cfg
        self.broker = broker
        self.state = state or State()
        self._notify = notify
        self.clock = clock  # real time (UTC), for signal age and hold timers
        self.no_new_after = parse_hhmm(cfg.session.no_new_trades_after)
        self.close_all_at = parse_hhmm(cfg.session.close_all_at)
        self._close_failed: set[int] = set()  # tickets already reported

    def notify(self, text: str) -> None:
        log.info(text)
        self._notify(text)

    # ------------------------------------------------------------------ signals
    def on_message(self, msg_id: int, text: str, sent_at: datetime, edited: bool = False) -> None:
        """Handle a new or edited group message. `sent_at` is always the time
        the message was first posted, never the edit time."""
        key = str(msg_id)
        if key in self.state.seen:
            return
        try:
            signal = parse_signal(text)
        except SignalError as exc:
            self.notify(f"⚠️ Message {msg_id} not traded: {exc}.\n{text[:300]}")
            return
        if signal is None:
            return
        self._roll_day()
        self.state.seen[key] = self.state.day
        reason = self._open(signal, key, sent_at)
        if reason:
            message = f"⏭️ {signal.symbol} {signal.side.value} skipped: {reason}"
            if edited:  # routine: old messages get edited as targets are ticked off
                log.info(message)
            else:
                self.notify(message)
        self.state.save()

    def _open(self, sig: Signal, key: str, sent_at: datetime) -> str | None:
        """Open the trade. Returns the reason when it is skipped."""
        cfg, st, broker = self.cfg, self.state, self.broker
        if st.halted_forever:
            return f"trading is stopped ({st.halted_forever})"
        if st.halted_day == st.day:
            return "daily loss stop was hit, no more trades today"
        if "swing" in sig.tags:
            return "swing signal"
        if sig.tps_hit:
            return "targets were already hit when the bot saw it"
        age = (self.clock() - sent_at).total_seconds()
        if age > cfg.session.max_signal_age_seconds:
            return f"signal is {age:.0f}s old"
        sym = cfg.symbols.get(sig.symbol)
        if sym is None:
            return "symbol is not configured"
        if broker.now().time() >= self.no_new_after:
            return "too late in the day"
        if st.signals_today >= cfg.rules.max_signals_per_day:
            return "daily signal limit reached"
        if len(st.trades) >= cfg.rules.max_open_signals:
            return "too many signals already open"

        quote = broker.quote(sig.symbol)
        if quote is None:
            return "no price from the broker"
        spec = broker.spec(sig.symbol)
        buy = sig.side is Side.BUY
        price, exit_price = (quote.ask, quote.bid) if buy else (quote.bid, quote.ask)
        dev = sym.max_entry_deviation
        if not sig.entry_low - dev <= price <= sig.entry_high + dev:
            return f"price {price} has moved away from the entry {sig.entry_low}-{sig.entry_high}"
        sign = 1 if buy else -1
        gap = spec.min_stop_distance
        if sign * (exit_price - sig.sl) <= gap:
            return f"price {price} is at or beyond the stop loss"
        tps = [tp for tp in sig.tps[: cfg.risk.max_tps] if sign * (tp - price) > gap]
        if not tps:
            return "price is already past every target"

        risk = cfg.risk.risk_per_signal
        if sig.tags & REDUCED_RISK_TAGS:
            risk *= cfg.risk.reduced_risk_factor
        risk = min(risk, self._headroom())
        lots = split_lots(spec, sig.side, price, sig.sl, risk, len(tps))
        if not lots:
            return f"${max(risk, 0):.0f} of risk is too little for the smallest lot at this stop"

        legs = []
        hold = cfg.rules.min_hold_seconds > 0
        for i, (lot, tp) in enumerate(zip(lots, tps), 1):
            comment = f"sig{key}-tp{i}"
            res = broker.market_order(sig.symbol, sig.side, lot, sig.sl, 0.0 if hold else tp, comment)
            if not res.ok:
                self.notify(f"❌ Order {i}/{len(lots)} failed: {res.error}")
                break
            legs.append({"ticket": res.ticket, "tp": tp, "lots": lot, "price": res.price,
                         "opened": self.clock().isoformat(), "tp_set": not hold})
        if not legs:
            return "the broker rejected the order"

        # Never leave a position without its stop loss.
        open_now = {p.ticket: p for p in broker.positions()}
        for leg in list(legs):
            pos = open_now.get(leg["ticket"])
            if pos is not None and not pos.sl and not broker.modify(pos.ticket, sig.sl, pos.tp).ok:
                broker.close(pos.ticket)
                legs.remove(leg)
                self.notify(f"❌ Closed position {pos.ticket}: its stop loss could not be set")
        if not legs:
            return "stop loss could not be set"

        st.trades[key] = {"symbol": sig.symbol, "side": sig.side.value, "sl": sig.sl,
                          "be_done": False, "opened_legs": len(legs), "legs": legs}
        st.signals_today += 1
        total = sum(leg["lots"] for leg in legs)
        targets = ", ".join(f"{leg['tp']:g}" for leg in legs)
        self.notify(f"✅ {sig.symbol} {sig.side.value.upper()} {total:g} lots @ {legs[0]['price']:g}, "
                    f"SL {sig.sl:g}, TP {targets}")
        return None

    def _headroom(self) -> float:
        """Dollars a new trade may risk before a loss stop would be reached,
        assuming every open position is stopped out."""
        acct = self.broker.account()
        worst_equity = acct.equity
        for pos in self.broker.positions():
            at_stop = pnl_at_stop(self.broker.spec(pos.symbol), pos)
            worst_equity += min(pos.profit, at_stop) - pos.profit
        day_start = acct.balance - self.broker.realized_today()
        return min(
            self.cfg.rules.daily_stop - (day_start - worst_equity),
            self.cfg.rules.max_loss_stop - (self.cfg.account.initial_balance - worst_equity),
        )

    # ------------------------------------------------------------------ managing
    def tick(self) -> None:
        """Run a few times a second: enforce the loss stops, the end-of-day
        close, the minimum hold time and breakeven."""
        self._roll_day()
        st, broker, rules = self.state, self.broker, self.cfg.rules
        before = repr(st.__dict__)
        positions = {p.ticket: p for p in broker.positions()}
        acct = broker.account()

        total_loss = self.cfg.account.initial_balance - acct.equity
        day_loss = acct.balance - broker.realized_today() - acct.equity
        if total_loss >= rules.max_loss_stop and not st.halted_forever:
            st.halted_forever = f"account is down ${total_loss:.0f} overall"
            self.notify(f"🛑 Max loss stop hit: {st.halted_forever}. The bot will not trade again "
                        "until you clear halted_forever in the state file.")
        elif day_loss >= rules.daily_stop and st.halted_day != st.day and not st.halted_forever:
            st.halted_day = st.day
            self.notify(f"🛑 Daily loss stop hit (down ${day_loss:.0f} today). No more trades today.")

        halted = bool(st.halted_forever) or st.halted_day == st.day
        end_of_day = broker.now().time() >= self.close_all_at
        if (halted or end_of_day) and positions:
            self._close_all(positions)
            if end_of_day and not halted and st.eod_day != st.day:
                st.eod_day = st.day
                self.notify("🌙 End of day: all positions closed.")
        else:
            for key in list(st.trades):
                self._manage(key, st.trades[key], positions)
            known = {leg["ticket"] for t in st.trades.values() for leg in t["legs"]}
            for ticket in positions.keys() - known - set(st.orphans):
                st.orphans.append(ticket)
                self.notify(f"⚠️ Position {ticket} carries the bot's tag but is not in its records. "
                            "It has no managed target; check it by hand.")

        if repr(st.__dict__) != before:
            st.save()

    def _manage(self, key: str, trade: dict, positions: dict[int, Position]) -> None:
        legs = trade["legs"]
        open_legs = [leg for leg in legs if leg["ticket"] in positions]
        if not open_legs:
            del self.state.trades[key]
            acct = self.broker.account()
            self.notify(f"🏁 {trade['symbol']} signal closed. Today: "
                        f"${self.broker.realized_today():+.2f}, balance ${acct.balance:.2f}")
            return
        trade["legs"] = open_legs
        buy = trade["side"] == Side.BUY.value
        sign = 1 if buy else -1
        quote = self.broker.quote(trade["symbol"])
        if quote is None:
            return
        out = quote.bid if buy else quote.ask
        gap = self.broker.spec(trade["symbol"]).min_stop_distance
        now = self.clock()

        # Targets are attached only once a leg is old enough, so no profit is
        # ever booked inside the minimum hold time.
        for leg in open_legs:
            if leg["tp_set"]:
                continue
            age = (now - datetime.fromisoformat(leg["opened"])).total_seconds()
            if age < self.cfg.rules.min_hold_seconds:
                continue
            pos = positions[leg["ticket"]]
            if sign * (out - leg["tp"]) >= 0:
                self.broker.close(pos.ticket)
            elif sign * (leg["tp"] - out) > gap and self.broker.modify(pos.ticket, pos.sl, leg["tp"]).ok:
                leg["tp_set"] = True

        # Breakeven: legs close nearest-target first, so fewer legs than we
        # opened while others are still running means targets were hit.
        n = self.cfg.risk.breakeven_after_tp
        if n and not trade["be_done"] and trade["opened_legs"] - len(open_legs) >= n:
            entry = max(leg["price"] for leg in open_legs) if buy else min(leg["price"] for leg in open_legs)
            if sign * (out - entry) > gap:
                results = [
                    self.broker.modify(leg["ticket"], entry, positions[leg["ticket"]].tp
                                       or (leg["tp"] if leg["tp_set"] else 0.0))
                    for leg in open_legs
                ]
                if all(r.ok for r in results):
                    trade["be_done"] = True
                    trade["sl"] = entry
                    self.notify(f"🔒 {trade['symbol']}: stop loss moved to entry {entry:g}")

    def _close_all(self, positions: dict[int, Position]) -> None:
        for ticket in positions:
            res = self.broker.close(ticket)
            if not res.ok and ticket not in self._close_failed:
                self._close_failed.add(ticket)
                self.notify(f"❌ Could not close position {ticket}: {res.error}")

    def _roll_day(self) -> None:
        today = self.broker.now().date().isoformat()
        st = self.state
        if st.day != today:
            st.day = today
            st.signals_today = 0
            st.seen = {k: d for k, d in st.seen.items() if d >= _days_before(today, 7)}
            st.save()


def _days_before(day: str, n: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()
