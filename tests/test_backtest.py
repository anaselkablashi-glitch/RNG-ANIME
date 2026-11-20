from datetime import datetime, timedelta, timezone

from signalbot.backtest import run_backtest
from signalbot.config import Config

BUY = "Gold Buy now 3839\nSL 3830\n\nTp 3843 ✅\nTp 3846 ✅\nTp 3850\nTp 3855\nTp 3865"


def ticks(start, prices, step=5):
    for i, bid in enumerate(prices):
        yield start + timedelta(seconds=i * step), bid, bid + 0.2


def test_backtest_replays_signals_through_the_live_engine():
    server_open = datetime(2026, 11, 20, 14, 0, 0)  # server is UTC+2
    sent_utc = datetime(2026, 11, 20, 12, 0, 20, tzinfo=timezone.utc)
    path = [3838.8] * 8 + [3840, 3841, 3842, 3843.2, 3844, 3846.1, 3845, 3842, 3839.5, 3838]
    report = run_backtest(Config(), [(sent_utc, 7, BUY)], ticks(server_open, path), "XAUUSD",
                          server_utc_offset_hours=2)
    assert report["signals_traded"] == 1
    assert report["positions_closed"] == 4
    assert report["wins"] == 2  # TP1 and TP2, then the rest stop out at entry
    assert report["net_profit"] > 0
    assert report["quick_profit_share"] == 0.0
    assert report["largest_overall_drawdown"] < 40


def test_backtest_skips_signal_when_market_has_no_fresh_price():
    start = datetime(2026, 11, 20, 14, 0, 0)
    sent = datetime(2026, 11, 20, 13, 0, 0, tzinfo=timezone.utc)  # an hour before any tick
    report = run_backtest(Config(), [(sent, 7, BUY)], ticks(start, [3839.0] * 5), "XAUUSD")
    assert report["signals_traded"] == 0 and report["signals_skipped"] == 1
