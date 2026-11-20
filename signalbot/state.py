"""What the bot must remember across restarts, kept in one JSON file."""
from __future__ import annotations

import json
import os
from pathlib import Path


class State:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.seen: dict[str, str] = {}  # message id -> server date it was handled
        self.trades: dict[str, dict] = {}  # message id -> open trade
        self.day: str = ""  # current server date
        self.signals_today: int = 0
        self.halted_day: str = ""  # server date trading was stopped for
        self.halted_forever: str = ""  # reason, once the max-loss stop is hit
        self.eod_day: str = ""  # server date the end-of-day close ran
        self.orphans: list[int] = []  # unknown bot positions already reported
        if self.path and self.path.exists():
            self.__dict__.update(json.loads(self.path.read_text()))
            self.path = Path(path)

    def save(self) -> None:
        if not self.path:
            return
        data = {k: v for k, v in self.__dict__.items() if k != "path"}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self.path)
