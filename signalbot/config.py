from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path


@dataclass
class AccountCfg:
    login: int = 0  # MT5 account number the bot may trade; 0 = refuse to start
    require_demo: bool = True
    initial_balance: float = 6000.0


@dataclass
class RulesCfg:
    daily_stop: float = 140.0  # bot stops for the day here (firm limit: 180)
    max_loss_stop: float = 320.0  # bot stops for good here (firm limit: 360)
    max_signals_per_day: int = 6
    max_open_signals: int = 2
    min_hold_seconds: int = 30  # no profit is taken on a trade younger than this


@dataclass
class RiskCfg:
    risk_per_signal: float = 40.0  # most a single signal may lose, in dollars
    reduced_risk_factor: float = 0.5  # applied to "High Risk" / "Small Lot" signals
    max_tps: int = 5  # use at most this many targets, nearest first
    breakeven_after_tp: int = 1  # move SL to entry once this many targets hit; 0 = off


@dataclass
class SessionCfg:
    no_new_trades_after: str = "22:00"  # broker server time
    close_all_at: str = "23:00"  # broker server time
    max_signal_age_seconds: int = 20


@dataclass
class SymbolCfg:
    broker_symbol: str
    max_entry_deviation: float  # skip if price is further than this from the entry zone
    max_slippage: float = 0.5  # in price units, passed to the broker


@dataclass
class TelegramCfg:
    api_id: int = 0
    api_hash: str = ""
    chat: str = ""  # group title, @username or numeric id


@dataclass
class Config:
    account: AccountCfg = field(default_factory=AccountCfg)
    rules: RulesCfg = field(default_factory=RulesCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    session: SessionCfg = field(default_factory=SessionCfg)
    telegram: TelegramCfg = field(default_factory=TelegramCfg)
    symbols: dict[str, SymbolCfg] = field(
        default_factory=lambda: {
            "XAUUSD": SymbolCfg("XAUUSD", max_entry_deviation=1.5, max_slippage=0.5),
            "NAS100": SymbolCfg("NAS100", max_entry_deviation=8.0, max_slippage=3.0),
        }
    )


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _fill(obj, data: dict, section: str):
    known = {f.name for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown setting [{section}] {key}")
        setattr(obj, key, value)
    return obj


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    cfg = Config()
    for section in ("account", "rules", "risk", "session", "telegram"):
        _fill(getattr(cfg, section), raw.pop(section, {}), section)
    for name, data in raw.pop("symbols", {}).items():
        cfg.symbols[name] = SymbolCfg(**data)
    if raw:
        raise ValueError(f"unknown config sections: {', '.join(raw)}")
    parse_hhmm(cfg.session.no_new_trades_after)
    parse_hhmm(cfg.session.close_all_at)
    return cfg
