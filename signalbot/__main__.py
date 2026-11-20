from __future__ import annotations

import argparse
import asyncio
import json
import logging

from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="signalbot")
    ap.add_argument("--config", default="config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="listen to Telegram and trade on MetaTrader 5")
    sub.add_parser("chats", help="list your Telegram chats")
    p = sub.add_parser("export-signals", help="save the group's message history for backtesting")
    p.add_argument("--limit", type=int, default=3000)
    p.add_argument("--out", default="data/signals.jsonl")
    p = sub.add_parser("export-ticks", help="save price history from MetaTrader 5 for backtesting")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--out", default="data/ticks.csv")
    p = sub.add_parser("backtest", help="replay saved signals over saved prices")
    p.add_argument("--signals", default="data/signals.jsonl")
    p.add_argument("--ticks", default="data/ticks.csv")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--server-utc-offset", type=float, default=0.0)
    p.add_argument("--latency", type=float, default=1.0)
    args = ap.parse_args()

    cfg = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("signalbot.log", encoding="utf-8")],
    )

    if args.cmd == "backtest":
        from .backtest import read_signals, read_ticks, run_backtest

        logging.getLogger("signalbot").setLevel(logging.WARNING)
        report = run_backtest(cfg, read_signals(args.signals), read_ticks(args.ticks), args.symbol,
                              server_utc_offset_hours=args.server_utc_offset,
                              latency_seconds=args.latency)
        notes = report.pop("notes")
        print(json.dumps(report, indent=2))
        with open("backtest_notes.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(notes))
        print("Every decision is listed in backtest_notes.txt")
        return

    from . import live

    if args.cmd == "run":
        asyncio.run(live.run(cfg))
    elif args.cmd == "chats":
        asyncio.run(live.list_chats(cfg))
    elif args.cmd == "export-signals":
        asyncio.run(live.export_signals(cfg, args.out, args.limit))
    elif args.cmd == "export-ticks":
        live.export_ticks(cfg, args.symbol, args.days, args.out)


if __name__ == "__main__":
    main()
