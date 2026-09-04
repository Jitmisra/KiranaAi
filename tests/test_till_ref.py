"""One answer to "which module is the till", checked by shape.

Sixteen routers each carried `_TILL_NAMES = ("upload_app", "tools.upload_app")`.
`python tools/upload_app.py` registers the module as `__main__`, which is in
neither spelling, so every router missed, imported a second copy of the till,
and read a different shop from the one serving requests.

It was not an error. The storefront advertised six products from its copy while
the money service could price the three the real one held, and a customer who
built that basket was refused at PAY with `amber_in_basket` on a line the shop
was openly selling. These tests exist so the constant cannot drift back.
"""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

from gawaah import till_ref

GAWAAH = pathlib.Path(__file__).resolve().parent.parent / "gawaah"

#: Modules that reach for the till. Discovered, not listed, so a new router
#: that copies the old idiom is caught the day it is written.
USERS = sorted(p for p in GAWAAH.glob("*.py")
               if p.name != "till_ref.py" and "_TILL_NAMES" in p.read_text())


def test_there_are_routers_to_check() -> None:
    """A discovery test that finds nothing is a test that proves nothing."""
    assert len(USERS) >= 10, [p.name for p in USERS]


@pytest.mark.parametrize("path", USERS, ids=lambda p: p.name)
def test_no_router_keeps_its_own_copy_of_the_name_list(path: pathlib.Path) -> None:
    """The tuple lives in one file. A local copy is the bug, restated."""
    src = path.read_text()
    assert '_TILL_NAMES = ("upload_app"' not in src, (
        f"{path.name} has its own copy of the till name list. Use "
        f"`till_ref.TILL_NAMES` — a second copy is a second thing to be wrong.")
    assert "till_ref" in src, f"{path.name} defines _TILL_NAMES without the shared source"


def test_main_is_a_name_the_till_can_have() -> None:
    """`python tools/upload_app.py` is a supported way to start it."""
    assert "__main__" in till_ref.TILL_NAMES


def test_the_till_is_identified_by_shape_not_by_name() -> None:
    """`__main__` is whatever process started — pytest, here."""
    assert not till_ref.is_the_till(sys.modules["__main__"])
    assert not till_ref.is_the_till(sys)
    assert not till_ref.is_the_till(None)


def test_a_module_called_main_that_IS_the_till_is_accepted() -> None:
    fake = types.ModuleType("__main__")
    for mark in till_ref.TILL_MARKS:
        setattr(fake, mark, lambda: None)
    assert till_ref.is_the_till(fake)
    assert till_ref.find_loaded_till({"__main__": fake}) is fake


def test_a_near_miss_is_refused() -> None:
    """Two of the three marks is not the till. All three, together, is."""
    for drop in till_ref.TILL_MARKS:
        nearly = types.ModuleType("upload_app")
        for mark in till_ref.TILL_MARKS:
            if mark != drop:
                setattr(nearly, mark, lambda: None)
        assert not till_ref.is_the_till(nearly), f"accepted a module missing {drop}"


def test_finding_nothing_imports_nothing() -> None:
    """The lookup never has a side effect; callers decide what to do next."""
    before = set(sys.modules)
    assert till_ref.find_loaded_till({}) is None
    assert set(sys.modules) == before


def test_the_real_till_answers_to_the_marks() -> None:
    """Whatever the marks are, the actual till must carry them."""
    from tools import upload_app
    assert till_ref.is_the_till(upload_app)
