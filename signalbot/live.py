"""Live wiring: Telegram in, MetaTrader 5 out. Runs on the Windows PC."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, events

from .config import Config
from .engine import Engine
from .state import State

log = logging.getLogger("signalbot")
SESSION = "signalbot"  # Telegram login is stored in signalbot.session


def _client(cfg: Config) -> TelegramClient:
    if not cfg.telegram.api_id or not cfg.telegram.api_hash:
        raise SystemExit("Fill in [telegram] api_id and api_hash in config.toml (from my.telegram.org).")
    return TelegramClient(SESSION, cfg.telegram.api_id, cfg.telegram.api_hash)


async def _find_chat(client: TelegramClient, wanted: str):
    target = wanted.strip().lstrip("@").lower()
    async for dialog in client.iter_dialogs():
        username = (getattr(dialog.entity, "username", None) or "").lower()
        if target in (str(dialog.id), dialog.name.strip().lower(), username):
            return dialog.entity
    raise SystemExit(f"Chat '{wanted}' not found. Run `python -m signalbot chats` to list yours.")


async def list_chats(cfg: Config) -> None:
    async with _client(cfg) as client:
        async for dialog in client.iter_dialogs():
            print(f"{dialog.id:>16}  {dialog.name}")


async def export_signals(cfg: Config, out: str, limit: int) -> None:
    async with _client(cfg) as client:
        chat = await _find_chat(client, cfg.telegram.chat)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(out, "w", encoding="utf-8") as fh:
            async for msg in client.iter_messages(chat, limit=limit):
                if msg.raw_text:
                    fh.write(json.dumps({"id": msg.id, "date": msg.date.isoformat(),
                                         "text": msg.raw_text}, ensure_ascii=False) + "\n")
                    count += 1
        print(f"Wrote {count} messages to {out}")


def export_ticks(cfg: Config, symbol: str, days: int, out: str) -> None:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"Cannot reach MetaTrader 5: {mt5.last_error()}")
    name = cfg.symbols[symbol].broker_symbol
    mt5.symbol_select(name, True)
    tick = mt5.symbol_info_tick(name)
    offset = round((tick.time - time.time()) / 3600)
    print(f"Server time is UTC{offset:+d} right now (pass --server-utc-offset {offset} to backtest).")
    end = datetime.now(timezone.utc) + timedelta(days=1)
    day = end - timedelta(days=days + 1)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with open(out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time_msc", "bid", "ask"])
        while day < end:
            ticks = mt5.copy_ticks_range(name, day, day + timedelta(days=1), mt5.COPY_TICKS_INFO)
            for t in ticks if ticks is not None else []:
                writer.writerow([int(t["time_msc"]), float(t["bid"]), float(t["ask"])])
                rows += 1
            day += timedelta(days=1)
    print(f"Wrote {rows} ticks to {out}")


async def run(cfg: Config) -> None:
    from .mt5_broker import MT5Broker

    broker = MT5Broker(cfg)
    client = _client(cfg)
    await client.start()
    chat = await _find_chat(client, cfg.telegram.chat)
    loop = asyncio.get_running_loop()
    pending: set[asyncio.Task] = set()

    def notify(text: str) -> None:
        task = loop.create_task(client.send_message("me", text))
        pending.add(task)
        task.add_done_callback(pending.discard)

    engine = Engine(cfg, broker, State("state.json"), notify)

    def handle(message, edited: bool) -> None:
        try:
            engine.on_message(message.id, message.raw_text or "", message.date, edited)
        except Exception as exc:  # never let one bad message stop the bot
            log.exception("message %s failed", message.id)
            notify(f"❌ Error handling message {message.id}: {exc}")

    @client.on(events.NewMessage(chats=chat))
    async def _new(event):
        handle(event.message, False)

    @client.on(events.MessageEdited(chats=chat))
    async def _edited(event):
        handle(event.message, True)

    async def manage() -> None:
        last_alert = 0.0
        while True:
            try:
                engine.tick()
            except Exception as exc:
                log.exception("tick failed")
                if time.monotonic() - last_alert > 60:
                    last_alert = time.monotonic()
                    notify(f"❌ Bot error while managing trades: {exc}")
            await asyncio.sleep(0.5)

    acct = broker.account()
    notify(f"🟢 Signal bot started on account {cfg.account.login}. "
           f"Balance ${acct.balance:.2f}, listening to '{cfg.telegram.chat}'.")
    await asyncio.gather(client.run_until_disconnected(), manage())
