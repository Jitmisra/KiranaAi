"""Money. Integer paise only.

INVARIANT 1: a float anywhere in the money path fails the build.
Enforced three ways:
  - Paise is a NewType over int and every constructor rejects float
  - tools/lint_no_float.py greps this package's money path for float literals/casts
  - test_money.py asserts the rejection behaviour
"""
from __future__ import annotations

from typing import NewType

Paise = NewType("Paise", int)


class MoneyError(ValueError):
    """Raised when a value cannot be trusted to represent money exactly."""


def paise(value: int) -> Paise:
    """Construct Paise. Rejects float, bool, and anything non-integral.

    bool is rejected explicitly: bool is a subclass of int in Python, and
    paise(True) == 1 paisa is never what anyone meant.
    """
    if isinstance(value, bool):
        raise MoneyError(f"bool is not money: {value!r}")
    if isinstance(value, float):
        raise MoneyError(
            f"float is not money: {value!r}. "
            "Money is integer paise. 0.1 + 0.2 != 0.3 and a rupee is not a float."
        )
    if not isinstance(value, int):
        raise MoneyError(f"not an integer: {value!r} ({type(value).__name__})")
    return Paise(value)


def from_rupees_str(s: str) -> Paise:
    """Parse a decimal rupee STRING to Paise without ever touching a float.

    '214.50' -> 21450.  Accepts 0, 1 or 2 decimal places.
    Deliberately takes a str, never a float: float('214.50') is already lossy.
    """
    s = s.strip()
    if not s:
        raise MoneyError("empty rupee string")
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if "." in s:
        whole, _, frac = s.partition(".")
    else:
        whole, frac = s, ""
    if not whole.isdigit() and whole != "":
        raise MoneyError(f"bad rupee string: {s!r}")
    if frac and not frac.isdigit():
        raise MoneyError(f"bad rupee string: {s!r}")
    if len(frac) > 2:
        raise MoneyError(f"sub-paisa precision is not money: {s!r}")
    frac = (frac + "00")[:2]
    total = int(whole or "0") * 100 + int(frac)
    return Paise(-total if neg else total)


def to_rupees_str(p: Paise) -> str:
    """Render Paise as a rupee string. Never returns a float."""
    p = int(p)
    sign = "-" if p < 0 else ""
    p = abs(p)
    return f"{sign}{p // 100}.{p % 100:02d}"


def add(*values: Paise) -> Paise:
    total = 0
    for v in values:
        total += int(paise(v))
    return Paise(total)


def total(values) -> Paise:
    t = 0
    for v in values:
        t += int(paise(v))
    return Paise(t)
