"""Turn a Telegram message into a Signal.

parse_signal() returns:
  * a Signal        - the message is a signal and every level is consistent
  * None            - the message is not a signal (chat, results, etc.)
and raises SignalError when the message looks like a signal but cannot be
trusted. The bot never trades a message that raises.
"""
from __future__ import annotations

import re

from .models import Side, Signal

ALIASES = {
    "gold": "XAUUSD",
    "xauusd": "XAUUSD",
    "xau": "XAUUSD",
    "nasdaq": "NAS100",
    "nas100": "NAS100",
    "nas": "NAS100",
    "us100": "NAS100",
    "ustec": "NAS100",
    "ndx100": "NAS100",
}

_WS = r"[^\S\n]"  # whitespace that is not a newline
_NUM = r"\d+(?:\.\d+)?"

_HEADER = re.compile(
    rf"\b(?P<sym>{'|'.join(sorted(ALIASES, key=len, reverse=True))})\b{_WS}*"
    rf"(?P<side>buy|sell)\b(?:{_WS}+(?P<kind>now|limit|stop))?{_WS}*@?{_WS}*"
    rf"(?P<p1>{_NUM})(?:{_WS}*[_\-–—/]{_WS}*(?P<p2>{_NUM}))?",
    re.I,
)
_SL = re.compile(rf"\b(?:sl|stop{_WS}*loss)\b{_WS}*[:=@]?{_WS}*(?P<p>{_NUM})", re.I)
_TP = re.compile(
    rf"^{_WS}*tp{_WS}*(?:\d{_WS}*(?:[:=\-]{_WS}*|{_WS}+))?[:=@]?{_WS}*"
    rf"(?P<p>{_NUM})(?P<rest>[^\n]*)$",
    re.I | re.M,
)
_TAGS = {
    "scalping": re.compile(r"scalp", re.I),
    "high risk": re.compile(r"high\s*risk", re.I),
    "small lot": re.compile(r"small\s*lot", re.I),
    "swing": re.compile(r"swing", re.I),
}
_LOOKS_LIKE_SIGNAL = re.compile(r"\b(buy|sell)\b.*\b(sl|stop\s*loss)\b.*\btp", re.I | re.S)

MAX_ENTRY_RANGE = 0.005  # entry zone wider than 0.5% of price = typo
MAX_SL_DISTANCE = 0.03
MAX_TP_DISTANCE = 0.10


class SignalError(ValueError):
    """The message looks like a signal but is not safe to trade."""


def _expand(p1: str, p2: str) -> float:
    """'3875', '76' -> 3876.0 (shorthand for the second entry price)."""
    if "." not in p1 and "." not in p2 and len(p2) < len(p1):
        return float(p1[: -len(p2)] + p2)
    return float(p2)


def parse_signal(text: str) -> Signal | None:
    head = _HEADER.search(text)
    if head is None:
        if _LOOKS_LIKE_SIGNAL.search(text):
            raise SignalError("looks like a signal but the format is not recognised")
        return None

    kind = (head["kind"] or "now").lower()
    if kind != "now":
        raise SignalError(f"'{kind}' orders are not supported, only market ('now') signals")

    side = Side(head["side"].lower())
    p1 = float(head["p1"])
    p2 = _expand(head["p1"], head["p2"]) if head["p2"] else p1
    entry_low, entry_high = min(p1, p2), max(p1, p2)
    if entry_high - entry_low > entry_high * MAX_ENTRY_RANGE:
        raise SignalError(f"entry zone {entry_low}-{entry_high} is too wide")

    stops = {float(m["p"]) for m in _SL.finditer(text)}
    if len(stops) != 1:
        raise SignalError("no stop loss found" if not stops else "more than one stop loss found")
    sl = stops.pop()

    tps: list[float] = []
    dropped: list[float] = []
    hit = 0
    for m in _TP.finditer(text):
        price = float(m["p"])
        if _TAGS["swing"].search(m["rest"]):
            dropped.append(price)
            continue
        tps.append(price)
        hit += "✅" in m["rest"]
    if not tps:
        raise SignalError("no take profit found")

    # Tags come from everything except the TP lines, so a "( Swing )" on one
    # target drops only that target, while "swing" on the header skips the signal.
    body = _TP.sub("", text)
    tags = frozenset(name for name, rx in _TAGS.items() if rx.search(body))

    sign = 1 if side is Side.BUY else -1
    entry_far = entry_high if side is Side.BUY else entry_low  # edge nearest the TPs
    entry_near_sl = entry_low if side is Side.BUY else entry_high
    if sign * (entry_near_sl - sl) <= 0:
        raise SignalError(f"stop loss {sl} is on the wrong side of the entry")
    if abs(entry_near_sl - sl) > entry_high * MAX_SL_DISTANCE:
        raise SignalError(f"stop loss {sl} is too far from the entry")
    last = entry_far
    for tp in tps:
        if sign * (tp - last) <= 0:
            raise SignalError(f"take profit {tp} is out of order or on the wrong side")
        if abs(tp - entry_far) > entry_high * MAX_TP_DISTANCE:
            raise SignalError(f"take profit {tp} is too far from the entry")
        last = tp

    return Signal(
        symbol=ALIASES[head["sym"].lower()],
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        sl=sl,
        tps=tuple(tps),
        tags=tags,
        dropped_tps=tuple(dropped),
        tps_hit=hit,
    )
