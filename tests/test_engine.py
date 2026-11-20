from datetime import datetime, timedelta, timezone

import pytest

from signalbot.broker import GOLD, SimBroker
from signalbot.config import Config
from signalbot.engine import Engine
from signalbot.models import Side
from signalbot.risk import split_lots
from signalbot.state import State

T0 = datetime(2026, 11, 20, 14, 0, 0)
BUY = "Gold Buy now 3839\nSL 3830\n\nTp 3843\nTp 3846\nTp 3850\nTp 3855\nTp 3865"
SELL = "Gold Sell now 3868 _ 3870\nSL 3885 ( High Risk )\n\nTp 3865\nTp 3861\nTp 3855"


class Rig:
    """Engine + simulated broker sharing one clock."""

    def __init__(self, cfg=None, state=None, broker=None, price=3839.0):
        self.cfg = cfg or Config()
        self.broker = broker or SimBroker()
        self.notes: list[str] = []
        self.engine = Engine(self.cfg, self.broker, state or State(), self.notes.append,
                             clock=lambda: self.broker.time.replace(tzinfo=timezone.utc))
        self.t = T0
        if price is not None:
            self.price(price, 0)

    def price(self, bid, seconds=1, spread=0.0, symbol="XAUUSD"):
        """Advance the clock, feed one quote, run one engine tick."""
        self.t += timedelta(seconds=seconds)
        self.broker.feed(symbol, self.t, bid, bid + spread)
        self.engine.tick()

    def signal(self, text=BUY, msg_id=1, age=1.0, edited=False):
        sent = self.t.replace(tzinfo=timezone.utc) - timedelta(seconds=age)
        self.engine.on_message(msg_id, text, sent, edited=edited)

    @property
    def open(self):
        return sorted(self.broker.open.values(), key=lambda p: p.ticket)

    def noted(self, fragment):
        return any(fragment in n for n in self.notes)


# ---------------------------------------------------------------- sizing
def test_split_lots_caps_total_risk():
    # $9 stop => $9 per 0.01 lot. $40 buys 4 minimum lots.
    assert split_lots(GOLD, Side.BUY, 3839, 3830, 40, 5) == [0.01] * 4
    assert split_lots(GOLD, Side.BUY, 3839, 3830, 100, 5) == [0.03, 0.02, 0.02, 0.02, 0.02]
    assert split_lots(GOLD, Side.SELL, 3870, 3885, 40, 5) == [0.01, 0.01]
    assert split_lots(GOLD, Side.BUY, 3839, 3830, 8, 5) == []  # can't afford one
    assert split_lots(GOLD, Side.BUY, 3839, 3845, 40, 5) == []  # stop on wrong side


# ---------------------------------------------------------------- opening
def test_opens_one_leg_per_target_with_stop_loss_and_capped_risk():
    r = Rig()
    r.signal()
    assert [p.lots for p in r.open] == [0.01] * 4
    assert all(p.sl == 3830 and p.side is Side.BUY for p in r.open)
    r.price(3829.9)  # stop out
    assert not r.open
    assert -40 <= r.broker.balance - 6000 < -36
    assert r.noted("signal closed")


def test_high_risk_signal_uses_half_risk():
    r = Rig(price=3869.0)
    r.signal(SELL)
    # $40 * 0.5 = $20; stop is $16 away => one 0.01 leg
    assert [p.lots for p in r.open] == [0.01]
    assert r.open[0].side is Side.SELL and r.open[0].sl == 3885


def test_duplicate_and_edited_messages_do_not_trade_twice():
    r = Rig()
    r.signal()
    r.signal()
    r.signal(BUY.replace("Tp 3843", "Tp 3843 ✅"), edited=True)
    assert len(r.open) == 4


@pytest.mark.parametrize(
    "text, kwargs, price, reason",
    [
        (BUY, {"age": 60}, 3839.0, "old"),
        (BUY, {}, 3842.0, "moved away"),
        (BUY, {}, 3835.0, "moved away"),
        (BUY.replace("Tp 3843", "Tp 3843 ✅"), {}, 3839.0, "already hit"),
        (BUY.replace("3839", "3839 swing"), {}, 3839.0, "swing"),
    ],
)
def test_signals_that_must_be_skipped(text, kwargs, price, reason):
    r = Rig(price=price)
    r.signal(text, **kwargs)
    assert not r.open
    assert r.noted(reason)


def test_unreadable_signal_alerts_and_does_not_trade():
    r = Rig()
    r.signal("Gold Buy now 3839\nSL 3845\nTp 3843")
    assert not r.open and r.noted("not traded")


def test_corrected_edit_of_a_bad_signal_trades_if_still_fresh():
    r = Rig()
    r.signal("Gold Buy now 3839\nSL 3845\nTp 3843")
    r.signal(BUY, edited=True, age=5)
    assert len(r.open) == 4


def test_targets_already_passed_are_dropped():
    r = Rig(price=3840.4)  # within deviation, but nothing passed yet
    r.signal(BUY.replace("3843", "3840.2"))
    assert len(r.open) == 3  # $40 / $10.40 stop = 3 legs, first target skipped
    r.price(3840.4, 40)
    assert sorted(p.tp for p in r.open) == [3846, 3850, 3855]


def test_too_late_in_the_day():
    r = Rig()
    r.t = T0.replace(hour=22, minute=5)
    r.price(3839.0)
    r.signal()
    assert not r.open and r.noted("too late")


def test_open_signal_and_daily_limits():
    cfg = Config()
    cfg.rules.max_open_signals = 1
    r = Rig(cfg)
    r.signal(msg_id=1)
    r.signal(msg_id=2)
    assert len(r.open) == 4 and r.noted("too many signals")

    cfg = Config()
    cfg.rules.max_signals_per_day = 1
    r = Rig(cfg)
    r.signal(msg_id=1)
    r.price(3829.0)
    r.price(3839.0)
    r.signal(msg_id=2)
    assert not r.open and r.noted("daily signal limit")


def test_rejected_orders():
    r = Rig()
    r.broker.reject_orders = 99
    r.signal()
    assert not r.open and r.noted("rejected")

    r = Rig()
    r.broker.reject_orders = 0
    r.signal()
    assert len(r.open) == 4


def test_partial_fill_keeps_what_opened():
    r = Rig()
    real = r.broker.market_order
    calls = []

    def flaky(*a):
        calls.append(a)
        if len(calls) > 2:
            r.broker.reject_orders = 1
        return real(*a)

    r.broker.market_order = flaky
    r.signal()
    assert len(r.open) == 2 and r.noted("Order 3/4 failed")
    assert all(p.sl == 3830 for p in r.open)


# ---------------------------------------------------------------- 30-second hold
def test_no_target_on_the_broker_until_30_seconds():
    r = Rig()
    r.signal()
    assert all(p.tp == 0 for p in r.open)
    r.price(3840.0, 29)
    assert all(p.tp == 0 for p in r.open)
    r.price(3840.0, 2)
    assert [p.tp for p in r.open] == [3843, 3846, 3850, 3855]


def test_fast_spike_through_target_is_not_closed_before_30_seconds():
    r = Rig()
    r.signal()
    r.price(3844.0, 5)  # past TP1 after 5s
    assert len(r.open) == 4
    r.price(3844.0, 26)  # 31s: TP1 leg is closed at market, in profit
    assert len(r.open) == 3
    first = r.broker.closed[0]
    assert first.profit > 0
    assert (first.close_time - first.open_time).total_seconds() >= 30


def test_no_winning_trade_ever_closes_inside_30_seconds():
    r = Rig()
    r.signal()
    for bid in (3841, 3844, 3847, 3851, 3856, 3852, 3848):
        r.price(bid, 4)
    for bid in (3856, 3866, 3840, 3829):
        r.price(bid, 10)
    assert not r.open
    quick_wins = [t for t in r.broker.closed
                  if t.profit > 0 and (t.close_time - t.open_time).total_seconds() < 30]
    assert quick_wins == []


def test_stop_loss_is_live_from_the_first_second():
    r = Rig()
    r.signal()
    r.price(3829.0, 2)
    assert not r.open and all(t.reason == "sl" for t in r.broker.closed)


def test_hold_time_zero_attaches_targets_immediately():
    cfg = Config()
    cfg.rules.min_hold_seconds = 0
    r = Rig(cfg)
    r.signal()
    assert [p.tp for p in r.open] == [3843, 3846, 3850, 3855]


# ---------------------------------------------------------------- breakeven
def test_stop_moves_to_entry_after_first_target():
    r = Rig()
    r.signal()
    r.price(3840.0, 31)
    r.price(3843.5)  # TP1 hit by the broker
    r.price(3843.5)
    assert len(r.open) == 3
    assert all(p.sl == 3839 for p in r.open) and r.noted("moved to entry")
    assert [p.tp for p in r.open] == [3846, 3850, 3855]
    r.price(3839.0)  # back to entry: remaining legs close flat
    assert not r.open
    assert r.broker.balance - 6000 == pytest.approx(4.0)


def test_breakeven_retries_until_the_broker_accepts():
    r = Rig()
    r.signal()
    r.price(3840.0, 31)
    r.broker.reject_modifies = 4
    r.price(3843.5)
    r.price(3843.5)
    assert any(p.sl == 3830 for p in r.open)
    r.price(3843.5)
    r.price(3843.5)
    assert all(p.sl == 3839 for p in r.open)


# ---------------------------------------------------------------- loss stops
def test_headroom_shrinks_then_blocks_new_trades():
    cfg = Config()
    cfg.rules.max_signals_per_day = 99
    r = Rig(cfg)
    for i in range(3):  # three full losses: about -$36 each
        r.signal(msg_id=i)
        r.price(3829.9)
        r.price(3839.0)
    assert r.broker.balance == pytest.approx(6000 - 3 * 36.4)
    r.signal(msg_id=10)  # $30.80 left before the $140 stop => 3 legs, not 4
    assert len(r.open) == 3
    r.price(3829.9)
    r.price(3839.0)
    r.signal(msg_id=11)  # $3.50 left: not enough for one minimum lot
    assert not r.open and r.noted("too little")
    assert 6000 - r.broker.balance <= 140


def test_daily_stop_closes_everything_and_halts_until_tomorrow():
    cfg = Config()
    cfg.rules.daily_stop = 30
    cfg.risk.risk_per_signal = 28
    r = Rig(cfg)
    r.signal()
    assert len(r.open) == 3
    r.broker.open = {t: p.__class__(**{**p.__dict__, "sl": 3800.0}) for t, p in r.broker.open.items()}
    r.price(3828.5)  # down $31.50 with the stops (artificially) far away
    assert not r.open and r.noted("Daily loss stop")
    r.price(3839.0)
    r.signal(msg_id=2)
    assert not r.open and r.noted("no more trades today")

    r.t = T0 + timedelta(days=1)
    r.price(3839.0)
    r.signal(msg_id=3)
    assert r.open


def test_max_loss_stop_is_permanent(tmp_path):
    cfg = Config()
    cfg.rules.max_loss_stop = 50
    state = State(tmp_path / "state.json")
    r = Rig(cfg, state, SimBroker(balance=5960.0))
    r.signal()  # only $10 of headroom: one leg
    assert len(r.open) == 1
    r.price(3828.0)
    r.price(3839.0)
    assert r.noted("Max loss stop")
    r.t = T0 + timedelta(days=3)
    r.price(3839.0)
    r.signal(msg_id=2)
    assert not r.open
    assert State(tmp_path / "state.json").halted_forever


def test_position_without_stop_blocks_new_risk():
    r = Rig()
    r.signal()
    r.broker.open = {t: p.__class__(**{**p.__dict__, "sl": 0.0}) for t, p in r.broker.open.items()}
    cfg2 = r.cfg
    cfg2.rules.max_open_signals = 5
    r.signal(msg_id=2)
    assert len(r.open) == 4 and r.noted("too little")


# ---------------------------------------------------------------- end of day
def test_end_of_day_closes_everything():
    r = Rig()
    r.signal()
    r.price(3840.0, 31)
    r.t = T0.replace(hour=22, minute=59, second=58)
    r.price(3840.0)
    assert len(r.open) == 4
    r.price(3840.0, 5)
    assert not r.open and r.noted("End of day")
    r.price(3840.0)
    assert not r.engine.state.trades


# ---------------------------------------------------------------- restarts
def test_restart_resumes_open_trade_and_ignores_old_message(tmp_path):
    path = tmp_path / "state.json"
    r = Rig(state=State(path))
    r.signal()
    r.price(3840.0, 10)

    r2 = Rig(state=State(path), broker=r.broker, price=None)
    r2.t = r.t
    r2.signal()  # same message replayed after restart
    assert len(r2.open) == 4
    r2.price(3840.0, 25)
    assert [p.tp for p in r2.open] == [3843, 3846, 3850, 3855]
    assert not any("not in its records" in n for n in r2.notes)


def test_unknown_bot_position_is_reported_once():
    r = Rig()
    r.broker.market_order("XAUUSD", Side.BUY, 0.01, 3830, 0.0, "stray")
    r.price(3839.0)
    r.price(3839.0)
    assert sum("not in its records" in n for n in r.notes) == 1
