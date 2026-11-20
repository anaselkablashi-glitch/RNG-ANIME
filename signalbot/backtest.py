"""Replay past signals over recorded ticks with the same Engine that trades live."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .broker import SimBroker
from .config import Config
from .engine import Engine
from .models import SymbolSpec


def read_signals(path: str | Path) -> list[tuple[datetime, int, str]]:
    """JSON lines written by `signalbot export-signals`: {"id", "date", "text"}."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.append((datetime.fromisoformat(row["date"]), row["id"], row["text"]))
    return sorted(out)


def read_ticks(path: str | Path) -> Iterator[tuple[datetime, float, float]]:
    """CSV written by `signalbot export-ticks`: time_msc,bid,ask (server time)."""
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            when = datetime.fromtimestamp(int(row["time_msc"]) / 1000, timezone.utc).replace(tzinfo=None)
            yield when, float(row["bid"]), float(row["ask"])


def run_backtest(
    cfg: Config,
    signals: list[tuple[datetime, int, str]],
    ticks: Iterable[tuple[datetime, float, float]],
    symbol: str,
    spec: SymbolSpec | None = None,
    server_utc_offset_hours: float = 0.0,
    latency_seconds: float = 1.0,
) -> dict:
    """`signals` carry UTC times (aware or naive); `ticks` carry server time.
    Each signal is acted on at the first tick after it arrived plus latency."""
    broker = SimBroker(balance=cfg.account.initial_balance)
    if spec:
        broker.specs[symbol] = spec
    notes: list[str] = []
    engine = Engine(cfg, broker, notify=notes.append,
                    clock=lambda: broker.time.replace(tzinfo=timezone.utc))
    shift = timedelta(hours=server_utc_offset_hours)
    queue = [(when.replace(tzinfo=None) + shift, msg_id, text.replace("✅", ""))
             for when, msg_id, text in sorted(signals)]
    lag = timedelta(seconds=latency_seconds)
    i = 0
    lowest_equity = cfg.account.initial_balance
    for when, bid, ask in ticks:
        broker.feed(symbol, when, bid, ask)
        while i < len(queue) and queue[i][0] + lag <= when:
            sent, msg_id, text = queue[i]
            engine.on_message(msg_id, text, sent.replace(tzinfo=timezone.utc))
            i += 1
        if broker.open or engine.state.trades:
            engine.tick()
            lowest_equity = min(lowest_equity, broker.account().equity)
    return summarize(cfg, broker, notes, lowest_equity)


def summarize(cfg: Config, broker: SimBroker, notes: list[str], lowest_equity: float) -> dict:
    closed = broker.closed
    by_day: dict[str, float] = defaultdict(float)
    for t in closed:
        by_day[t.close_time.date().isoformat()] += t.profit
    gross_profit = sum(t.profit for t in closed if t.profit > 0)
    hold = cfg.rules.min_hold_seconds or 30
    quick = sum(t.profit for t in closed
                if t.profit > 0 and (t.close_time - t.open_time).total_seconds() < hold)
    return {
        "signals_traded": sum(n.startswith("✅") for n in notes),
        "signals_skipped": sum("skipped:" in n for n in notes),
        "signals_unreadable": sum("not traded" in n for n in notes),
        "positions_closed": len(closed),
        "wins": sum(t.profit > 0 for t in closed),
        "losses": sum(t.profit < 0 for t in closed),
        "net_profit": round(sum(t.profit for t in closed), 2),
        "final_balance": round(broker.balance, 2),
        "worst_day": round(min(by_day.values(), default=0.0), 2),
        "best_day": round(max(by_day.values(), default=0.0), 2),
        "largest_overall_drawdown": round(cfg.account.initial_balance - lowest_equity, 2),
        "daily_stop_days": sum("Daily loss stop" in n for n in notes),
        "max_loss_stop_hit": any("Max loss stop" in n for n in notes),
        "quick_profit_share": round(quick / gross_profit, 3) if gross_profit else 0.0,
        "profit_by_day": {day: round(v, 2) for day, v in sorted(by_day.items())},
        "notes": notes,
    }
