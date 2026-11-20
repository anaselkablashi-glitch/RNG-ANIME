"""Exercises the MT5 adapter against a stand-in for the MetaTrader5 module.
This checks our logic and the requests we build, not the real terminal."""
import sys
import types
from types import SimpleNamespace as NS

import pytest

from signalbot.config import Config
from signalbot.models import Side


@pytest.fixture
def mt5(monkeypatch):
    m = types.ModuleType("MetaTrader5")
    m.TRADE_ACTION_DEAL, m.TRADE_ACTION_SLTP = 1, 6
    m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 0, 1
    m.POSITION_TYPE_BUY, m.POSITION_TYPE_SELL = 0, 1
    m.DEAL_TYPE_BUY, m.DEAL_TYPE_SELL = 0, 1
    m.ORDER_TIME_GTC = 0
    m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN = 0, 1, 2
    m.TRADE_RETCODE_DONE = 10009
    m.ACCOUNT_TRADE_MODE_DEMO = 0
    m.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2
    m.acct = NS(login=111, trade_mode=0, margin_mode=2, balance=6000.0, equity=5990.0)
    m.sent, m.replies = [], []
    m.pos = [NS(ticket=5, symbol="XAUUSD", type=0, volume=0.02, price_open=3839.0, sl=3830.0,
                tp=0.0, profit=-3.0, swap=-0.5, magic=20261120),
             NS(ticket=6, symbol="XAUUSD", type=0, volume=1.0, price_open=3839.0, sl=0.0,
                tp=0.0, profit=0.0, swap=0.0, magic=0)]
    m.initialize = lambda: True
    m.last_error = lambda: (0, "ok")
    m.account_info = lambda: m.acct
    m.terminal_info = lambda: NS(trade_allowed=True)
    m.symbol_select = lambda name, on: name in ("XAUUSD", "NAS100")
    m.symbol_info_tick = lambda name: NS(time=1795183200, bid=3839.0, ask=3839.2)
    m.symbol_info = lambda name: NS(trade_tick_size=0.01, trade_tick_value=1.0,
                                    trade_tick_value_loss=1.0, volume_min=0.01, volume_step=0.01,
                                    volume_max=50.0, digits=2, point=0.01, trade_stops_level=10,
                                    filling_mode=2)
    m.positions_get = lambda ticket=None: [p for p in m.pos if ticket in (None, p.ticket)]
    m.history_deals_get = lambda a, b: [NS(type=0, profit=-20.0, commission=-1.0, swap=0.0, fee=0.0),
                                        NS(type=2, profit=6000.0, commission=0.0, swap=0.0, fee=0.0)]

    def order_send(request):
        m.sent.append(request)
        code = m.replies.pop(0) if m.replies else 10009
        return NS(retcode=code, comment="x", order=77, price=request.get("price", 0.0))

    m.order_send = order_send
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    sys.modules.pop("signalbot.mt5_broker", None)
    return m


def make(mt5, login=111):
    from signalbot.mt5_broker import MT5Broker

    cfg = Config()
    cfg.account.login = login
    return MT5Broker(cfg)


def test_refuses_wrong_account_live_account_and_netting(mt5):
    with pytest.raises(RuntimeError, match="Refusing"):
        make(mt5, login=222)
    with pytest.raises(RuntimeError, match="Refusing"):
        make(mt5, login=0)
    mt5.acct.trade_mode = 2
    with pytest.raises(RuntimeError, match="not a demo"):
        make(mt5)
    mt5.acct.trade_mode, mt5.acct.margin_mode = 0, 0
    with pytest.raises(RuntimeError, match="hedging"):
        make(mt5)


def test_reads_account_positions_and_todays_result(mt5):
    b = make(mt5)
    assert (b.account().balance, b.account().equity) == (6000.0, 5990.0)
    assert b.realized_today() == -21.0  # the deposit is not counted
    (pos,) = b.positions()  # only the bot's own position
    assert (pos.ticket, pos.side, pos.lots, pos.sl, pos.profit) == (5, Side.BUY, 0.02, 3830.0, -3.5)
    spec = b.spec("XAUUSD")
    assert (spec.tick_value, spec.volume_min, spec.min_stop_distance) == (1.0, 0.01, 0.1)
    assert b.now().isoformat() == "2026-11-20T14:00:00"


def test_market_order_carries_stop_loss_in_the_same_request(mt5):
    b = make(mt5)
    res = b.market_order("XAUUSD", Side.BUY, 0.01, 3830.0, 0.0, "sig1-tp1")
    assert res.ok and res.ticket == 77
    (req,) = mt5.sent
    assert req["action"] == 1 and req["type"] == 0 and req["price"] == 3839.2
    assert (req["sl"], req["tp"], req["volume"], req["magic"]) == (3830.0, 0.0, 0.01, 20261120)
    assert req["deviation"] == 50 and req["type_filling"] == mt5.ORDER_FILLING_IOC


def test_requote_is_retried_once_but_a_rejection_is_not(mt5):
    b = make(mt5)
    mt5.replies = [10004]
    assert b.market_order("XAUUSD", Side.SELL, 0.01, 3850.0, 0.0, "c").ok
    assert len(mt5.sent) == 2 and mt5.sent[1]["price"] == 3839.0
    mt5.sent.clear()
    mt5.replies = [10019]  # no money
    res = b.market_order("XAUUSD", Side.SELL, 0.01, 3850.0, 0.0, "c")
    assert not res.ok and "10019" in res.error and len(mt5.sent) == 1


def test_modify_and_close(mt5):
    b = make(mt5)
    assert b.modify(5, 3839.0, 3846.0).ok
    assert mt5.sent[-1] == {"action": 6, "position": 5, "symbol": "XAUUSD", "sl": 3839.0, "tp": 3846.0}
    assert b.close(5).ok
    req = mt5.sent[-1]
    assert (req["position"], req["type"], req["volume"], req["price"]) == (5, 1, 0.02, 3839.0)
    assert not b.close(999).ok and not b.modify(999, 1, 1).ok
