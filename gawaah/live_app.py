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

    RZP_MODE=live ./.venv/bin/uvicorn --factory gawaah.live_app:app --port 8788
"""
from __future__ import annotations

import json
import os
import pathlib

from .clock import RealClock
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


def app():
    return create_app(
        build_service(
            data_dir=DATA_DIR,
            clock=RealClock(),
            price_book=load_prices(),
            live_factory=live_factory,
        )
    )
