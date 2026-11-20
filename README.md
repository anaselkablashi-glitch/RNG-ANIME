# Signal bot: Telegram signals to MetaTrader 5

Reads trade signals from a Telegram group the moment they are posted and places
them on MetaTrader 5 with the stop loss and targets attached.

> **Check your account's rules before running this on it.** Many prop firms ban
> trading from signal groups or Telegram-driven automation. FundedNext does:
> see its [EA policy](https://help.fundednext.com/en/articles/8020763-is-ea-allowed-in-fundednext).
> The bot only starts on a demo account until you turn `require_demo` off.

## What it does with a signal

```
Gold Buy now 3875 _ 76
SL 3862 ( High Risk )

Tp 3880
Tp 3885
Tp 3890
Tp 3895
Tp 3925 ( Swing )
```

1. Reads the symbol, direction, entry zone, stop loss and targets. A target
   marked "Swing" is dropped. A message it cannot read with certainty is not
   traded, and you get a Telegram alert instead.
2. Skips the signal if it is more than 20 seconds old, if price has already
   left the entry zone, or if targets are already ticked off.
3. Sizes the trade so that hitting the stop loss costs at most
   `risk_per_signal` dollars ("High Risk" and "Small Lot" signals get half),
   and never more than what is left before the daily or overall loss stop.
4. Opens one position per target, each with the stop loss in the same order.
5. Attaches each target only after the position is 30 seconds old, so no
   profit is ever booked inside 30 seconds. The stop loss is live from the
   first second.
6. Moves the stop loss to entry after the first target is hit.
7. Closes everything at `close_all_at`, so nothing is held overnight.
8. Closes everything and stops for the day at `daily_stop`, and stops for good
   at `max_loss_stop`.

Every action and every skipped signal is sent to your Telegram "Saved Messages"
and written to `signalbot.log`.

## Setup (Windows PC)

1. Install [Python 3.11 or newer](https://www.python.org/downloads/) and tick
   "Add Python to PATH".
2. Install MetaTrader 5, log in to a **demo** account, and switch on the
   **Algo Trading** button. Leave MT5 running.
3. Get a Telegram `api_id` and `api_hash` at <https://my.telegram.org> under
   "API development tools".
4. Double-click `setup.bat`. It installs what the bot needs, runs the
   self-tests and opens `config.toml` in Notepad. Fill in your MT5 account
   number and the Telegram keys, check the symbol names match your MT5 Market
   Watch, then save.
5. Double-click `chats.bat`. The first time it asks for your phone number and
   a login code. Copy the group's name into `chat` in `config.toml`.
6. Double-click `start.bat` to run the bot.

The PC must stay on, awake and online, with MT5 open, for the bot to trade.

## Backtest

```
python -m signalbot export-signals            # the group's history -> data/signals.jsonl
python -m signalbot export-ticks --days 30    # gold prices from MT5 -> data/ticks.csv
python -m signalbot backtest --server-utc-offset 2
```

`export-ticks` prints the offset to pass to `backtest`. The backtest replays
every past signal through the same code that trades live and reports profit,
the worst day, the largest drawdown and how often the loss stops were hit.
`backtest_notes.txt` lists every decision.

## Tests

```
python -m pytest
```

The tests run the real decision code against a simulated broker. The live
Telegram and MetaTrader 5 connections can only be proven on the Windows PC,
on a demo account.

## Files

| File | Purpose |
|---|---|
| `signalbot/parser.py` | Message text to signal |
| `signalbot/engine.py` | What to trade, and managing open trades |
| `signalbot/risk.py` | Lot sizing |
| `signalbot/broker.py` | Simulated broker for tests and backtests |
| `signalbot/mt5_broker.py` | MetaTrader 5 connection |
| `signalbot/live.py` | Telegram connection and the live loop |
| `signalbot/backtest.py` | Replay of past signals |
