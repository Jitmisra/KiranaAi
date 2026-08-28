"""S1 acceptance: money is exact, the ledger chain is tamper-evident."""
from __future__ import annotations

import json

import pytest
from hypothesis import given, strategies as st

from gawaah.clock import VirtualClock
from gawaah.ledger import GENESIS, Ledger, verify
from gawaah.money import MoneyError, add, from_rupees_str, paise, to_rupees_str


# ---------------------------------------------------------------- money

@pytest.mark.parametrize("bad", [214.50, 0.1, -3.0, True, False, "5", None, 1 + 2j])
def test_money_rejects_non_integers(bad):
    with pytest.raises(MoneyError):
        paise(bad)


@pytest.mark.parametrize(
    "s,expected",
    [("214.50", 21450), ("0.01", 1), ("0", 0), ("1", 100),
     ("999999.99", 99999999), ("-5.25", -525), ("7.1", 710)],
)
def test_rupee_string_parsing(s, expected):
    assert from_rupees_str(s) == expected


def test_sub_paisa_precision_is_rejected():
    with pytest.raises(MoneyError):
        from_rupees_str("1.234")


def test_the_classic_float_bug_cannot_happen():
    """0.1 + 0.2 != 0.3 in floats. In paise it is exact."""
    assert 0.1 + 0.2 != 0.3                      # the bug we are avoiding
    a, b = from_rupees_str("0.10"), from_rupees_str("0.20")
    assert add(a, b) == from_rupees_str("0.30")  # exact


@given(st.integers(min_value=-10**12, max_value=10**12))
def test_rupee_string_roundtrip(n):
    assert from_rupees_str(to_rupees_str(paise(n))) == n


@given(st.lists(st.integers(min_value=0, max_value=10**9), max_size=50))
def test_addition_is_associative_and_exact(xs):
    assert add(*[paise(x) for x in xs]) == sum(xs)


# ---------------------------------------------------------------- ledger

def test_empty_ledger_verifies(tmp_path):
    ok, n, head, err = verify(tmp_path / "nope.jsonl")
    assert ok and n == 0 and head == GENESIS and err is None


def test_chain_verifies_and_head_advances(tmp_path):
    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    assert led.head == GENESIS
    heads = [led.append(ts=clk.now_iso(), module="test", i=i, amount_paise=i * 100)
             for i in range(25)]
    assert len(set(heads)) == 25          # every hash distinct
    assert led.head == heads[-1]
    ok, n, head, err = verify(p)
    assert ok, err
    assert n == 25 and head == heads[-1]


def test_reopening_recovers_head_and_count(tmp_path):
    p = tmp_path / "audit.jsonl"
    clk = Ledger(p), VirtualClock()
    led, clk = clk
    for i in range(5):
        led.append(ts=clk.now_iso(), module="m", i=i)
    head, count = led.head, led.count
    again = Ledger(p)
    assert again.head == head and again.count == count


def test_tampering_with_a_value_is_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    for i in range(6):
        led.append(ts=clk.now_iso(), module="m", amount_paise=i * 1000)
    lines = p.read_text().splitlines()
    rec = json.loads(lines[3])
    rec["amount_paise"] = 999999            # the fraud
    lines[3] = json.dumps(rec, sort_keys=True)
    p.write_text("\n".join(lines) + "\n")
    ok, n, _, err = verify(p)
    assert not ok and "line 4" in err


def test_deleting_a_line_is_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    for i in range(6):
        led.append(ts=clk.now_iso(), module="m", i=i)
    lines = p.read_text().splitlines()
    del lines[2]
    p.write_text("\n".join(lines) + "\n")
    ok, _, _, err = verify(p)
    assert not ok and "chain break" in err


def test_reordering_lines_is_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    led, clk = Ledger(p), VirtualClock()
    for i in range(6):
        led.append(ts=clk.now_iso(), module="m", i=i)
    lines = p.read_text().splitlines()
    lines[2], lines[4] = lines[4], lines[2]
    p.write_text("\n".join(lines) + "\n")
    ok, _, _, err = verify(p)
    assert not ok


def test_replay_is_byte_identical(tmp_path):
    """Same inputs + same VirtualClock -> identical file. This is what makes
    `make bench` reproducible."""
    def run(path):
        led, clk = Ledger(path), VirtualClock()
        for i in range(10):
            led.append(ts=clk.now_iso(), module="m", i=i, amount_paise=i * 7)
        return path.read_bytes()

    assert run(tmp_path / "a.jsonl") == run(tmp_path / "b.jsonl")
