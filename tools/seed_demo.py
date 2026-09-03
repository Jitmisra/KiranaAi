#!/usr/bin/env python3
"""Fill in the shop settings that five screens need before they show anything.

    ./.venv/bin/python tools/seed_demo.py          # say what it WOULD do
    ./.venv/bin/python tools/seed_demo.py --yes    # actually do it
    ./.venv/bin/python tools/seed_demo.py --yes --gst   # and set tax rates too

WHY THIS EXISTS. Five screens are empty not because they are broken but because
nobody has told them anything yet, and each one says so honestly:

    "0 product(s) have a reorder level set and 9 do not -- a product with no
     level can never appear on this list."

That is correct behaviour and it is the right answer for a real shop on day
one. It is a poor answer during a five-minute demonstration, where five blank
screens out of twenty-eight read as five things that do not work.

WHAT IT IS NOT. It does not invent history, sales, customers or money. It sets
CONFIGURATION -- the handful of decisions a shopkeeper makes once when they
start using a till -- and it goes through the ordinary HTTP endpoints, so every
value passes exactly the validation a person typing it would face. Nothing here
touches the catalogue's prices, the audit chain, or any bill.

THESE ARE PLACEHOLDERS, NOT ADVICE. A reorder level is a judgement about how
fast a shop sells a thing and how long a supplier takes. A per-kilo price is a
price. This script picks defensible round numbers so the screens have something
to draw; a real shopkeeper should change every one of them.

GST IS BEHIND ITS OWN FLAG, deliberately. A tax rate is a legal statement about
a product, not a display setting. The rates below are the ordinary published
ones for these goods, and they are still nobody's decision but the person who
files the return. Without `--gst` this script does not touch tax at all, and
the GST screen keeps reporting those lines as exceptions -- which is a perfectly
good thing to demonstrate.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

TILL = "http://127.0.0.1:8790"

G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; D = "\033[2m"; B = "\033[1m"; X = "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = B = X = ""

#: Reorder levels, in whole units. "Tell me when there are this many left."
#: Fast movers get a higher number because the gap between noticing and the
#: delivery arriving is when a shop loses a sale.
REORDER = {
    "parle_g_biscuit": 24, "shampoo_sachet": 30, "lifebuoy_soap": 12,
    "ThumsUp": 12, "maxfresh": 6, "PONDS": 4,
}

#: Sold from the sack, priced per kilo. These are the products a kirana
#: actually weighs; they are added to the catalogue by this script's caller,
#: not here -- only products that already exist can be marked.
WEIGHED = {"parle_g_biscuit": 20000}

#: Batches with a date on them. `stock_in` false: recording that a batch exists
#: and when it expires is not the same claim as receiving new stock, and
#: conflating the two would put units on the shelf that nobody delivered.
BATCHES = [
    ("parle_g_biscuit", 40, "2026-10-15"),
    ("shampoo_sachet", 60, "2027-03-31"),
    ("lifebuoy_soap", 18, "2028-01-31"),
]

#: One point per rupee spent, each point worth ten paise. A one per cent
#: return, in whole numbers, which is what the module requires.
LOYALTY = {"points_per_rupee": 1, "paise_per_point": 10}

#: HSN heading and rate per product. ORDINARY PUBLISHED RATES, not advice.
#: Aerated drinks are deliberately absent: they moved to 40 per cent in
#: September 2025 and this counter records 0/5/12/18/28 only, so Thums Up stays
#: an exception rather than being filed at a rate it is not taxed at. That
#: refusal is worth showing.
GST = {
    "parle_g_biscuit": ("1905", 18),
    "shampoo_sachet": ("3305", 18),
    "lifebuoy_soap": ("3401", 18),
    "maxfresh": ("3306", 18),
    "PONDS": ("3304", 18),
}

DONE: list[str] = []
SKIPPED: list[str] = []


def post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{TILL}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"detail": str(e)}


def catalogue() -> dict[str, str]:
    try:
        with urllib.request.urlopen(f"{TILL}/shop", timeout=15) as r:
            d = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {}
    return {s["sku_id"]: s.get("name") or s["sku_id"]
            for s in (d.get("skus") or d.get("items") or [])}


def do(what: str, path: str, body: dict, apply: bool) -> None:
    if not apply:
        print(f"  {D}would{X}  {what}")
        return
    code, res = post(path, body)
    if code in (200, 201) and res.get("ok") is not False:
        print(f"  {G}set{X}    {what}")
        DONE.append(what)
    else:
        why = res.get("detail") or res.get("reason") or f"HTTP {code}"
        print(f"  {Y}skip{X}   {what}\n         {D}{str(why)[:110]}{X}")
        SKIPPED.append(what)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true", help="actually write. without it, nothing changes")
    ap.add_argument("--gst", action="store_true", help="also set HSN and tax rate per product")
    a = ap.parse_args()

    have = catalogue()
    if not have:
        print(f"  {R}The till is not answering on {TILL}, or the catalogue is empty.{X}")
        print(f"  {D}Start it with: ./.venv/bin/python tools/upload_app.py{X}")
        return 1

    print(f"\n{B}Shop settings{X}  {D}{len(have)} products in the catalogue{X}")
    if not a.yes:
        print(f"  {Y}Dry run.{X} Nothing will be written. Add {B}--yes{X} to apply.\n")
    else:
        print()

    print(f"{B}Reorder levels{X}  {D}what makes the low-stock list and the reorder list work{X}")
    for sku, units in REORDER.items():
        if sku in have:
            do(f"{have[sku][:26]:28} tell me at {units} left", f"/stock/{sku}/reorder", {"units": units}, a.yes)
        else:
            print(f"  {D}absent {sku}{X}")

    print(f"\n{B}Sold by weight{X}  {D}priced per kilo from the sack{X}")
    for sku, paise in WEIGHED.items():
        if sku in have:
            do(f"{have[sku][:26]:28} Rs {paise // 100}.{paise % 100:02d} per kg",
               f"/weighed/{sku}", {"price_per_kg_paise": paise}, a.yes)

    print(f"\n{B}Batches with a date{X}  {D}what the expiry screen watches{X}")
    for sku, units, on in BATCHES:
        if sku in have:
            do(f"{have[sku][:26]:28} {units} units, expires {on}",
               "/expiry/batch", {"sku_id": sku, "units": units, "expires_on": on, "stock_in": False}, a.yes)

    print(f"\n{B}Loyalty{X}  {D}points earned only on bills the gateway settled{X}")
    do(f"{'1 point per rupee, 10 paise a point':28} (a 1% return)",
       "/loyalty/rules", LOYALTY, a.yes)

    if a.gst:
        print(f"\n{B}Tax{X}  {Y}these are ordinary published rates, NOT advice{X}")
        print(f"  {D}Confirm every one with whoever files the return. Aerated drinks are{X}")
        print(f"  {D}left out on purpose: 40 per cent is outside the slabs this counter records.{X}")
        for sku, (hsn, rate) in GST.items():
            if sku in have:
                do(f"{have[sku][:26]:28} HSN {hsn}, {rate}%",
                   f"/gst/products/{sku}", {"hsn": hsn, "rate": rate, "accepted_suggestion": False}, a.yes)
    else:
        print(f"\n{B}Tax{X}  {D}not touched. Add --gst to set HSN and rate per product.{X}")
        print(f"  {D}Without it the GST screen keeps listing those lines as exceptions,{X}")
        print(f"  {D}which is honest and worth demonstrating on its own.{X}")

    if not a.yes:
        print(f"\n  {Y}Nothing was written.{X} Run again with {B}--yes{X}"
              f"{' --gst' if a.gst else ''} to apply.\n")
        return 0

    print(f"\n  {G}{B}{len(DONE)} setting(s) written{X}"
          + (f", {Y}{len(SKIPPED)} refused{X}" if SKIPPED else ""))
    print(f"  {D}Every value above is a placeholder a real shopkeeper should change.{X}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
