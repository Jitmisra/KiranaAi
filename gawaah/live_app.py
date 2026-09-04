"""ASGI entrypoint that injects the live Razorpay gateway and the shop's prices.

paisa refuses to construct a live gateway itself (invariant 5 -- the gateway is
a Protocol so the money service is testable with no credentials). This module is
the one place that binds the two together, so `grep -l rzp_live gawaah/` names
every file that can reach the real API.

Prices come from a JSON file so that enrolling an SKU is a shopkeeper action on
disk, never a code change:

    results/shop.json   {"parle_g_200g": 21437, ...}   values are INTEGER PAISE

An item with no entry is AMBER and is excluded from the total. paisa will refuse
to mint rather than guess a price -- that refusal is invariant 7 and it is the
product's whole thesis, so this loader must never invent a default.

Offers are applied HERE, by wrapping that book, and there is no other place they
could be. paisa re-prices every basket from its own book and refuses to mint a
total it did not derive; a discount applied in the browser or in the till is a
number paisa has never seen, and the mint dies with `amount_disagreement` at the
counter. Wrapping the book means the discounted price is one paisa DERIVED
itself, so invariant 5 is not weakened -- it is simply told the truth. See
`gawaah/offers.py`.

    RZP_MODE=live ./.venv/bin/uvicorn --factory gawaah.live_app:app --port 8788
"""
from __future__ import annotations

import json
import os
import pathlib

from .clock import RealClock
from .offers import OfferPriceBook
from .paisa import DictPriceBook, build_service, create_app
from .rzp_live import live_factory

DATA_DIR = os.environ.get("GAWAAH_DATA_DIR", "results")
SHOP_FILE = pathlib.Path(DATA_DIR) / "shop.json"


def load_prices() -> DictPriceBook:
    """Read the enrolled SKUs. Missing file means an empty shop, not a crash.

    Values must be integer paise. A float here would be a money bug, so
    DictPriceBook validates through money.paise() and raises rather than
    silently truncating -- see
    tests/test_paisa.py::test_a_truncating_price_book_would_be_caught_not_silently_billed
    """
    if not SHOP_FILE.exists():
        return DictPriceBook({})
    return DictPriceBook(json.loads(SHOP_FILE.read_text()))


class FileBackedPriceBook:
    """`shop.json`, re-read when it changes on disk.

    The counter teaches products all day; the money service starts once. A book
    loaded at boot means every SKU taught after boot is unpriceable until
    someone remembers to restart paisa -- and an unpriceable item is excluded
    from the total, so the visible symptom is a bill that is quietly short.

    Reload is by mtime, checked per lookup. A price lookup happens a handful of
    times per mint, so a stat() per call costs nothing that matters; caching the
    stat on a timer would save microseconds and buy a window in which a taught
    product is still invisible. Every reload passes through DictPriceBook, so
    the float-rejection invariant holds for the reloaded file exactly as it did
    for the boot one -- and a reload that FAILS keeps the previous book rather
    than replacing real prices with an empty shop, because a temporarily
    half-written file must not turn a working till into one that refuses
    everything.
    """

    def __init__(self) -> None:
        self._book = load_prices()
        self._mtime = self._stat()

    @staticmethod
    def _stat() -> float:
        try:
            return SHOP_FILE.stat().st_mtime
        except OSError:
            return -1.0

    def _fresh(self) -> DictPriceBook:
        m = self._stat()
        if m != self._mtime:
            try:
                self._book = load_prices()
                self._mtime = m
            except Exception:  # noqa: BLE001 - keep the last good book
                pass
        return self._book

    def price_paise(self, item_id: str):
        return self._fresh().price_paise(item_id)

    def __len__(self) -> int:
        return len(self._fresh())


def app():
    return create_app(
        build_service(
            data_dir=DATA_DIR,
            clock=RealClock(),
            # The shop's marked prices, with the shop's active offers on top.
            # OfferPriceBook re-reads offers.json by mtime for the same reason
            # FileBackedPriceBook re-reads shop.json: an offer created after
            # boot that the money service cannot see is a discount the till
            # shows and paisa refuses.
            price_book=OfferPriceBook(FileBackedPriceBook()),
            live_factory=live_factory,
        )
    )
