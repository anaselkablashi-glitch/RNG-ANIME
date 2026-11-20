import pytest

from signalbot.models import Side
from signalbot.parser import SignalError, parse_signal

S1 = """Gold Buy now 3745 _ 3743
SL 3730 ( Scalping ) Small Lot


Tp 3748 ✅️
Tp 3751 ✅️
Tp 3755 ✅️
Tp 3760 ✅️
Tp 3770 ✅️"""

S2 = """Gold Buy now 3818
SL 3805

Tp 3823 ✅️
Tp 3826 ✅️
Tp 3830 ✅️
Tp 3835
Tp 3840"""

S3 = """Gold Sell now 3868 _ 3870
SL 3885 ( High Risk )

Tp 3865 ✅️
Tp 3861 ✅️
Tp 3855
Tp 3850
Tp 3832"""

S4 = """Gold Buy now 3839
SL 3830

Tp 3843 ✅️
Tp 3846 ✅️
Tp 3850 ✅️
Tp 3855 ✅️
Tp 3865 ✅️"""

S5 = """Gold Buy now 3875 _ 76
SL 3862 ( High Risk )


Tp 3880 ✅️
Tp 3885 ✅️
Tp 3890
Tp 3895
Tp 3925 ( Swing )"""


def test_sample_1_range_and_tags():
    s = parse_signal(S1)
    assert (s.symbol, s.side) == ("XAUUSD", Side.BUY)
    assert (s.entry_low, s.entry_high, s.sl) == (3743, 3745, 3730)
    assert s.tps == (3748, 3751, 3755, 3760, 3770)
    assert s.tags == {"scalping", "small lot"}
    assert s.tps_hit == 5


def test_sample_2_single_entry():
    s = parse_signal(S2)
    assert (s.entry_low, s.entry_high, s.sl) == (3818, 3818, 3805)
    assert s.tps == (3823, 3826, 3830, 3835, 3840)
    assert s.tags == frozenset()
    assert s.tps_hit == 3


def test_sample_3_sell():
    s = parse_signal(S3)
    assert s.side is Side.SELL
    assert (s.entry_low, s.entry_high, s.sl) == (3868, 3870, 3885)
    assert s.tps == (3865, 3861, 3855, 3850, 3832)
    assert s.tags == {"high risk"}


def test_sample_4():
    s = parse_signal(S4)
    assert (s.entry_low, s.sl, s.tps[0], s.tps[-1]) == (3839, 3830, 3843, 3865)


def test_sample_5_shorthand_entry_and_swing_target_dropped():
    s = parse_signal(S5)
    assert (s.entry_low, s.entry_high) == (3875, 3876)
    assert s.tps == (3880, 3885, 3890, 3895)
    assert s.dropped_tps == (3925,)
    assert "swing" not in s.tags  # only that one target was a swing


def test_fresh_message_has_no_ticks():
    assert parse_signal(S1.replace("✅️", "")).tps_hit == 0


def test_swing_on_header_tags_whole_signal():
    s = parse_signal("Gold Buy now 3839 ( Swing )\nSL 3830\nTp 3843\nTp 3900")
    assert "swing" in s.tags


@pytest.mark.parametrize("name", ["Nasdaq", "NAS100", "US100", "ustec"])
def test_nasdaq_aliases(name):
    s = parse_signal(f"{name} Sell now 24850 _ 60\nSL 24920\nTp 24820\nTp 24780")
    assert s.symbol == "NAS100"
    assert (s.entry_low, s.entry_high) == (24850, 24860)


@pytest.mark.parametrize("line", ["TP1 3823", "TP1: 3823", "Tp 1 : 3823", "TP: 3823", "tp 3823.5"])
def test_tp_line_variants(line):
    s = parse_signal(f"Gold Buy now 3818\nSL 3805\n{line}")
    assert s.tps[0] in (3823, 3823.5)


@pytest.mark.parametrize(
    "text",
    [
        "Good morning traders",
        "Tp 3748 hit ✅ 30 pips",
        "Gold looking bullish today, wait for my signal",
        "Close gold now",
    ],
)
def test_chat_is_not_a_signal(text):
    assert parse_signal(text) is None


@pytest.mark.parametrize(
    "text, why",
    [
        ("Gold Buy now 3818\nSL 3825\nTp 3823", "wrong side"),
        ("Gold Sell now 3868\nSL 3860\nTp 3865", "wrong side"),
        ("Gold Buy now 3818\nTp 3823", "no stop loss"),
        ("Gold Buy now 3818\nSL 3805", "no take profit"),
        ("Gold Buy now 3818\nSL 3805\nTp 3810", "wrong side"),
        ("Gold Buy now 3818\nSL 3805\nTp 3826\nTp 3823", "out of order"),
        ("Gold Buy now 3818\nSL 380\nTp 3823", "too far"),
        ("Gold Buy now 3818\nSL 3805\nTp 38230", "too far"),
        ("Gold Buy now 3818 _ 3618\nSL 3605\nTp 3823", "too wide"),
        ("Gold Buy now 3818\nSL 3805\nSL 3800\nTp 3823", "more than one"),
        ("Gold Buy limit 3818\nSL 3805\nTp 3823", "not supported"),
        ("Buy Gold @ 3818\nSL 3805\nTp 3823", "not recognised"),
        ("Gold Buy now 3818\nSL 3805\nTp 5 pips", "wrong side"),
    ],
)
def test_untrustworthy_signals_are_rejected(text, why):
    with pytest.raises(SignalError, match=why):
        parse_signal(text)
