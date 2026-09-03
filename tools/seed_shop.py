#!/usr/bin/env python3
"""Fill an empty counter with a believable neighbourhood kirana.

    ./.venv/bin/python tools/seed_shop.py --dry-run    # say what it would do
    ./.venv/bin/python tools/seed_shop.py              # do it
    ./.venv/bin/python tools/seed_shop.py --force      # seed again over itself

WHY THIS EXISTS. An empty shop demonstrates badly and a shop holding four
products called "derma" and "manmatter" demonstrates worse: every screen is
technically correct and none of them is legible. This seeds thirty real Indian
kirana products with real pack sizes, prices in the right 2026 ballpark, the
Devanagari a shopkeeper would actually say, a picture each, a category each, a
shelf each, stock levels with two products deliberately short, two dated
batches and three storefront orders.

IT GOES THROUGH THE COUNTER'S OWN HTTP API, not the files. Every product is
added through the same `/enrol` or `POST /shop` a shopkeeper's screen posts to,
every price passes `gawaah/money.py`, every category goes through
`gawaah/categories.py`'s own validation, and the price map the money service
reads is republished by the server on each add. A shop seeded this way is
indistinguishable from one somebody typed in by hand, which is the only kind
worth demonstrating.

WHAT IT WILL NOT DO, AND THIS IS THE LOAD-BEARING PART
------------------------------------------------------
  * It never marks anything PAID, SETTLED or green. Green belongs to
    `gawaah/webhook.py` on a signature-verified webhook and to nothing else, so
    the three storefront orders it places are ordinary unpaid orders — amber,
    awaiting settlement, exactly as a real order is between the customer
    pressing ORDER and the gateway saying anything.
  * It never mints, builds, parses or reassembles a payment link or a UPI
    string. It does not call the pay route at all.
  * It never appends to `results/audit.jsonl`. That file has one writer
    (`gawaah/kernel.py`) and a seeded bill would be a sale that never happened
    sitting in the day's takings. So it seeds no BILLS — see NO FABRICATED
    HISTORY below.

NO FABRICATED HISTORY, SPELLED OUT. A bill on this counter is the fold of a
session's own chain — placements, exits, a close, an intent — written by the
kernel as goods crossed a line. There is no honest way to manufacture one from
outside, and a "past bill" written straight into the chain would be money the
shop owes itself and a figure on the Today screen that no packet ever earned.
So Books and Today show what this counter actually did. What this script CAN
seed honestly it does: orders that are genuinely unpaid, stock that was
genuinely counted, batches whose dates are genuinely near.

THE PICTURES ARE DRAWN, NOT PHOTOGRAPHED. `tools/packshot.py` renders a packet
silhouette in the brand's colour family with the name, the Devanagari and the
pack size on it. No brand photography is downloaded and no logo is reproduced —
the page's CSP (`default-src 'self'`) forbids an external image at runtime in
any case. Every tile carries "generated image · not a photograph" under the
pack and the PNG carries the same statement in a tEXt chunk.

RE-RUNNABLE. A marker file in the shop's own directory records what this script
put there. A second run without `--force` refuses rather than doubling the
shop; with `--force` it skips what is already right and fills in what is not.
Both `GAWAAH_SHOP_DIR` and `GAWAAH_DATA_DIR` are respected, because the shop
directory is read back from the server's own `GET /shop`, so the seeder writes
wherever the till is actually writing.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import http.cookiejar
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.packshot import (Pack, font_report, pack_shot, png_bytes,  # noqa: E402
                            save_png)

TILL = os.environ.get("GAWAAH_TILL", "http://127.0.0.1:8790")

G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"
D = "\033[2m"; B = "\033[1m"; C = "\033[36m"; X = "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = B = C = X = ""


# =========================================================== THE SHOPKEEPER ==
#
# A plausible name and a plausible number. THE PASSWORD IS NOT HERE: it is
# generated per run by `secrets` and printed once at the end. A committed
# password is a committed password whatever the file says about it, and a
# password in a URL is in the shell history, the server log and the browser's
# address bar at the same time.

SHOPKEEPER_NAME = "Ramesh Verma"
SHOPKEEPER_PHONE = "9820114477"

SHOP_PROFILE = {
    "name": "Verma Kirana Store",
    "address": ("Shop 4, Ganesh Nagar Market, Near Hanuman Mandir,\n"
                "Andheri East, Mumbai 400069"),
    "phone": "9820114477",
    "hours": {"open": "07:00", "close": "22:00",
              "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
}


# ================================================================ THE SHELVES ==
#
# WHY THESE ARE TAGS. `gawaah/shelf.py` is a camera that counts what is FACING
# OUT on a shelf; a shelf there is a LABEL on a photograph, and there is no
# planogram, no rack table and no location column anywhere in this repo —
# `gawaah/shop_store.py` stores a name, a price, vectors and a footprint, and
# nothing else. So "where does this packet live" is recorded the one way the
# catalogue can record it: as a tag through `gawaah/categories.py`, whose tag
# vocabulary is free text under 24 characters.
#
# THE STRINGS ARE THE SAME ON BOTH SIDES. The label on a shelf READ is the same
# string as the tag on the products that live there, so "Rack 1 — Staples" on
# the Shelf screen and `rack 1 staples` on a product are the same shelf and can
# be joined by eye. They are not joined by code, because nothing in the shelf
# module is looking for a tag and this script may not teach it to.

SHELVES: dict[str, str] = {
    # tag on the product         label on a shelf read
    "rack 1 staples":            "Rack 1 — Staples",
    "rack 2 snacks":             "Rack 2 — Biscuits & Namkeen",
    "rack 3 tea coffee":         "Rack 3 — Tea, Coffee & Masala",
    "rack 4 personal care":      "Rack 4 — Personal care",
    "rack 5 home care":          "Rack 5 — Soap & Detergent",
    "cold rack":                 "Cold rack — Dairy & Cold drinks",
    "front counter":             "Front counter — Impulse",
}


# ============================================================== THE CATALOGUE ==
#
# PRICES ARE MRP-ISH FOR 2026 AND THEY ARE NOT ADVICE. They were sanity-checked
# against ordinary Indian retail listings in September 2026 and they are round
# numbers a shopkeeper would recognise, not a price list. Every one is written
# here in RUPEES as a string and sent as a string: the server parses it through
# `gawaah/money.py` into integer paise, which is the only arithmetic this
# program does with money. Nothing in this file multiplies, divides or floats a
# price — INVARIANT 3 is kept by not doing money arithmetic at all.
#
# THE NAME CARRIES THE DEVANAGARI. `gawaah/search.py` searches the product NAME
# and romanises a Devanagari query to reach a latin name; putting the Hindi in
# the name itself means the counter's hi-IN microphone finds the packet on the
# first pass rather than on the damped romanised one. The cap is 96 characters
# (gawaah/shop_store.NAME_MAX_CHARS) and every name below is well under it.


@dataclass(frozen=True)
class Item:
    sku_id: str
    latin: str                 # what is printed on the shelf edge
    hindi: str                 # what the shopkeeper says
    price_rupees: str          # sent as text; the server makes the paise
    category: str              # a gawaah/categories.py RULES name
    shelf: str                 # a key of SHELVES
    tags: tuple[str, ...] = ()
    pack: Pack = field(default_factory=lambda: Pack("?"))
    #: What the shelf holds after the opening count. `None` means "do not count
    #: this one", which is a real state — a shopkeeper does not count the whole
    #: shop on day one.
    on_hand: Optional[int] = None
    reorder: Optional[int] = None

    @property
    def name(self) -> str:
        return f"{self.latin} ({self.hindi})"


def _p(brand, variant, size, hindi, shape, primary, accent,
       band="middle", tall=1.0, motif="none") -> Pack:
    return Pack(brand=brand, variant=variant, size_text=size, devanagari=hindi,
                shape=shape, primary=primary, accent=accent, band=band,
                tall=tall, motif=motif)


# Colour families, straight from the packets a kirana actually stacks. These are
# ordinary hex values, not assets: nothing here is a logo and nothing is traced.
RED = (198, 36, 42); DEEP_RED = (166, 27, 34); YELLOW = (243, 195, 45)
BLUE = (24, 86, 170); DEEP_BLUE = (18, 52, 116); GREEN = (34, 122, 62)
LIME = (126, 176, 52); ORANGE = (236, 132, 34); WHITE = (250, 249, 246)
CREAM = (238, 214, 156); BROWN = (109, 66, 38); PINK = (214, 68, 122)
TEAL = (28, 136, 140); PURPLE = (104, 58, 132); BLACK = (32, 30, 30)
GOLD = (206, 160, 48)

CATALOGUE: tuple[Item, ...] = (
    # ---------------------------------------------------------- staples --
    Item("aashirvaad_atta_5kg", "Aashirvaad Whole Wheat Atta 5 kg",
         "आशीर्वाद आटा", "285", "Staples", "rack 1 staples",
         ("bulk", "daily need"),
         _p("AASHIRVAAD", "Whole Wheat Atta", "5 kg", "आशीर्वाद आटा",
            "pouch", RED, YELLOW, "middle", 1.25, "stripes"), 14, 6),
    Item("sona_masoori_rice_5kg", "Sona Masoori Rice 5 kg",
         "सोना मसूरी चावल", "420", "Staples", "rack 1 staples",
         ("bulk", "loose alternative"),
         _p("SONA MASOORI", "Steam Rice", "5 kg", "सोना मसूरी चावल",
            "pouch", (206, 178, 118), GREEN, "middle", 1.25, "skyline"), 9, 4),
    Item("tata_salt_1kg", "Tata Salt Iodised 1 kg", "टाटा नमक",
         "30", "Staples", "rack 1 staples", ("daily need", "mrp fixed"),
         _p("TATA SALT", "Iodised Namak", "1 kg", "टाटा नमक",
            "pouch", BLUE, WHITE, "middle", 1.15, "halo"), 36, 12),
    Item("moong_dal_dhuli_1kg", "Moong Dal Dhuli 1 kg", "मूंग दाल",
         "148", "Staples", "rack 1 staples", ("daily need",),
         _p("MOONG DAL", "Dhuli", "1 kg", "मूंग दाल",
            "pouch", (218, 190, 62), GREEN, "middle", 1.1, "checker"), 11, 5),
    Item("cheeni_sugar_1kg", "Cheeni Sulphurless Sugar 1 kg", "चीनी",
         "58", "Staples", "rack 1 staples", ("daily need", "bulk"),
         _p("CHEENI", "Sulphurless Sugar", "1 kg", "चीनी",
            "pouch", (244, 240, 232), (196, 132, 44), "middle", 1.1, "waves"), 22, 8),
    Item("fortune_sunflower_1l", "Fortune Sunflower Oil 1 L",
         "फॉर्च्यून सूरजमुखी तेल", "175", "Staples", "rack 1 staples",
         ("cooking oil", "daily need"),
         _p("FORTUNE", "Sunflower Oil", "1 L", "फॉर्च्यून तेल",
            "bottle", (245, 186, 30), RED, "middle", 1.55, "burst"), 16, 6),
    Item("amul_ghee_500ml", "Amul Pure Ghee 500 ml", "अमूल घी",
         "355", "Dairy", "rack 1 staples", ("cooking oil", "festival"),
         _p("AMUL GHEE", "Pure Ghee", "500 ml", "अमूल घी",
            "jar", (240, 196, 60), RED, "middle", 1.0, "corner"), 6, 3),

    # --------------------------------------------- biscuits and namkeen --
    Item("good_day_cashew_100g", "Britannia Good Day Cashew 100 g",
         "गुड डे काजू", "30", "Snacks", "rack 2 snacks", ("fast moving",),
         _p("GOOD DAY", "Cashew Cookies", "100 g", "गुड डे काजू",
            "packet", (240, 178, 40), DEEP_RED, "middle", 1.0, "halo"), 30, 12),
    Item("marie_gold_250g", "Britannia Marie Gold 250 g", "मैरी गोल्ड",
         "45", "Snacks", "rack 2 snacks", ("chai time",),
         _p("MARIE GOLD", "Light Tea Biscuits", "250 g", "मैरी गोल्ड",
            "packet", (232, 186, 74), DEEP_RED, "top", 1.1, "columns"), 18, 8),
    Item("kurkure_masala_70g", "Kurkure Masala Munch 70 g", "कुरकुरे",
         "20", "Snacks", "rack 2 snacks", ("fast moving", "kids"),
         _p("KURKURE", "Masala Munch", "70 g", "कुरकुरे",
            "packet", GREEN, RED, "middle", 1.35, "stripes"), 40, 18),
    Item("haldirams_bhujia_200g", "Haldiram's Aloo Bhujia 200 g",
         "हल्दीराम आलू भुजिया", "55", "Snacks", "rack 2 snacks",
         ("namkeen",),
         _p("HALDIRAM'S", "Aloo Bhujia", "200 g", "आलू भुजिया",
            "packet", ORANGE, DEEP_RED, "middle", 1.15, "waves"), 14, 6),
    Item("dairy_milk_50g", "Cadbury Dairy Milk 50 g", "डेयरी मिल्क",
         "45", "Snacks", "front counter", ("kids", "impulse"),
         _p("DAIRY MILK", "Milk Chocolate", "50 g", "डेयरी मिल्क",
            "bar", PURPLE, (198, 168, 90), "middle", 0.55, "panel"), 25, 10),

    # -------------------------------------------------------- ready to eat --
    Item("maggi_noodles_70g", "Maggi 2-Minute Noodles 70 g", "मैगी नूडल्स",
         "14", "Ready to eat", "front counter", ("fast moving", "kids"),
         _p("MAGGI", "2-Minute Noodles", "70 g", "मैगी नूडल्स",
            "packet", (240, 190, 36), RED, "bottom", 1.0, "skyline"), 48, 24),

    # ----------------------------------------------------------- beverages --
    Item("red_label_250g", "Brooke Bond Red Label Tea 250 g", "रेड लेबल चाय",
         "155", "Beverages", "rack 3 tea coffee", ("daily need", "chai time"),
         _p("RED LABEL", "Strong Chai Patti", "250 g", "रेड लेबल चाय",
            "carton", DEEP_RED, YELLOW, "bottom", 1.3, "checker"), 12, 6),
    Item("tata_tea_gold_250g", "Tata Tea Gold 250 g", "टाटा टी गोल्ड",
         "185", "Beverages", "rack 3 tea coffee", ("chai time",),
         _p("TATA TEA", "Gold", "250 g", "टाटा टी गोल्ड",
            "carton", (188, 40, 44), GOLD, "middle", 1.3, "burst"), 7, 4),
    Item("bru_instant_50g", "Bru Instant Coffee 50 g", "ब्रू कॉफ़ी",
         "175", "Beverages", "rack 3 tea coffee", (),
         _p("BRU", "Instant Coffee", "50 g", "ब्रू कॉफ़ी",
            "jar", (146, 62, 30), (228, 168, 44), "middle", 1.05, "stripes"), 5, 3),
    Item("thums_up_750ml", "Thums Up 750 ml", "थम्स अप",
         "40", "Beverages", "cold rack", ("cold", "fast moving"),
         _p("THUMS UP", "Strong Cola", "750 ml", "थम्स अप",
            "bottle", (26, 26, 34), RED, "middle", 1.9, "waves"), 20, 12),
    Item("frooti_150ml", "Frooti Mango Drink 150 ml", "फ्रूटी",
         "10", "Beverages", "cold rack", ("cold", "kids", "impulse"),
         _p("FROOTI", "Mango Drink", "150 ml", "फ्रूटी",
            "tetra", (240, 166, 26), GREEN, "middle", 1.5, "halo"), 36, 18),

    # --------------------------------------------------------------- dairy --
    Item("amul_butter_100g", "Amul Butter 100 g", "अमूल मक्खन",
         "62", "Dairy", "cold rack", ("cold", "daily need"),
         _p("AMUL", "Butter", "100 g", "अमूल मक्खन",
            "carton", (226, 32, 44), YELLOW, "bottom", 0.75, "checker"), 9, 6),
    Item("amul_taaza_500ml", "Amul Taaza Toned Milk 500 ml", "अमूल ताज़ा दूध",
         "29", "Dairy", "cold rack", ("cold", "daily need"),
         _p("AMUL TAAZA", "Toned Milk", "500 ml", "अमूल ताज़ा दूध",
            "pouch", (36, 122, 196), WHITE, "middle", 0.95, "panel"), 0, 20),

    # -------------------------------------------------------------- spices --
    Item("everest_garam_masala_100g", "Everest Garam Masala 100 g",
         "एवरेस्ट गरम मसाला", "85", "Spices", "front counter", (),
         _p("EVEREST", "Garam Masala", "100 g", "गरम मसाला",
            "packet", DEEP_RED, YELLOW, "top", 1.2, "skyline"), 10, 5),
    Item("mdh_haldi_100g", "MDH Haldi Powder 100 g", "एमडीएच हल्दी",
         "42", "Spices", "front counter", ("daily need",),
         _p("MDH", "Haldi Powder", "100 g", "हल्दी पाउडर",
            "packet", (232, 152, 32), DEEP_RED, "top", 1.2, "panel"), 13, 6),

    # ------------------------------------------------------- personal care --
    Item("colgate_strong_100g", "Colgate Strong Teeth 100 g", "कोलगेट",
         "62", "Personal care", "rack 4 personal care", ("daily need",),
         _p("COLGATE", "Strong Teeth", "100 g", "कोलगेट",
            "tube", (198, 26, 36), WHITE, "middle", 1.5, "burst"), 15, 6),
    Item("clinic_plus_sachet", "Clinic Plus Shampoo Sachet 5 ml x 16",
         "क्लिनिक प्लस शैम्पू", "32", "Personal care", "rack 4 personal care",
         ("sachet", "hanging strip"),
         _p("CLINIC PLUS", "Shampoo Sachet", "5 ml x 16", "क्लिनिक प्लस",
            "sachet", PINK, WHITE, "none", 1.5, "none"), 22, 10),
    Item("parachute_oil_100ml", "Parachute Coconut Hair Oil 100 ml",
         "पैराशूट नारियल तेल", "52", "Personal care", "rack 4 personal care",
         (),
         _p("PARACHUTE", "Coconut Hair Oil", "100 ml", "नारियल तेल",
            "bottle", (34, 116, 178), WHITE, "middle", 1.7, "columns"), 12, 5),
    Item("lifebuoy_soap_125g", "Lifebuoy Total Soap 125 g", "लाइफबॉय साबुन",
         "38", "Personal care", "rack 4 personal care", ("daily need",),
         _p("LIFEBUOY", "Total 10 Soap", "125 g", "लाइफबॉय साबुन",
            "bar", (192, 32, 40), YELLOW, "middle", 0.62, "burst"), 26, 12),

    # ----------------------------------------------------------- household --
    Item("surf_excel_1kg", "Surf Excel Easy Wash 1 kg", "सर्फ़ एक्सेल",
         "140", "Household", "rack 5 home care", ("bulk", "daily need"),
         _p("SURF EXCEL", "Easy Wash", "1 kg", "सर्फ़ एक्सेल",
            "pouch", (24, 78, 168), LIME, "middle", 1.05, "corner"), 8, 4),
    Item("vim_bar_200g", "Vim Dishwash Bar 200 g", "विम बार",
         "22", "Household", "rack 5 home care", ("fast moving", "daily need"),
         _p("VIM", "Dishwash Bar", "200 g", "विम बार",
            "bar", (46, 138, 62), YELLOW, "middle", 0.6, "checker"), 34, 15),
    Item("dettol_soap_125g", "Dettol Original Soap 125 g", "डेटॉल साबुन",
         "48", "Household", "rack 5 home care", (),
         _p("DETTOL", "Original Soap", "125 g", "डेटॉल साबुन",
            "bar", (22, 108, 92), RED, "middle", 0.62, "stripes"), 2, 8),
    Item("harpic_500ml", "Harpic Toilet Cleaner 500 ml", "हार्पिक",
         "92", "Household", "rack 5 home care", (),
         _p("HARPIC", "Toilet Cleaner", "500 ml", "हार्पिक",
            "bottle", (36, 46, 132), (222, 62, 46), "bottom", 1.9, "panel"), 6, 3),
)


# ================================================================ THE BATCHES ==
#
# `stock_in` is FALSE on every one of these, and gawaah/expiry.py explains why:
# recording that a batch exists and when it expires is not the same claim as
# receiving new stock, and booking it in here as well would put units on the
# shelf that nobody delivered. The counts above are the delivery; these are the
# dates.
#
# Offsets in days from today, so a shop seeded in March is still short-dated in
# June and the Expiry screen has something true to show on any day.

BATCHES: tuple[tuple[str, int, int, str], ...] = (
    # sku, units, days from today, note
    ("amul_taaza_500ml", 24, -2, "yesterday's crate, pulled off the cold rack"),
    ("amul_butter_100g", 9, 6, "short-dated, sell first"),
    ("bru_instant_50g", 5, 41, "next month"),
    ("maggi_noodles_70g", 48, 214, "carton from the March delivery"),
)


# ================================================================= THE ORDERS ==
#
# Three real storefront orders through `POST /store/order`, exactly as a
# customer's phone places them. NONE of them is paid, none of them is minted,
# and this script never calls the pay route: an order between ORDER and the
# gateway saying anything is amber, and that is what these are.

ORDERS: tuple[dict[str, Any], ...] = (
    {"name": "Sunita Joshi", "phone": "9867012233",
     "address": "B-104, Shreeji Apartments, Ganesh Nagar, Andheri East 400069",
     "items": [("aashirvaad_atta_5kg", 1), ("tata_salt_1kg", 2),
               ("fortune_sunflower_1l", 1), ("red_label_250g", 1)],
     "advance_to": "out_for_delivery"},
    {"name": "Imran Shaikh", "phone": "9930445566",
     "address": "Room 7, Chawl No 3, Marol Village Road, Andheri East 400059",
     "items": [("maggi_noodles_70g", 6), ("kurkure_masala_70g", 3),
               ("frooti_150ml", 4)],
     "advance_to": "preparing"},
    {"name": "Lata Nair", "phone": "9820778899",
     "address": "Flat 12, Sai Krupa CHS, Chakala, Andheri East 400099",
     "items": [("amul_butter_100g", 2), ("good_day_cashew_100g", 2),
               ("colgate_strong_100g", 1), ("surf_excel_1kg", 1)],
     "advance_to": None},
)


#: Written into the shop's own directory so a second run can see the first.
MARKER = "seeded_by_seed_shop.json"
MARKER_FORMAT = 1


# ==================================================================== plumbing ==


class Till:
    """The counter over HTTP, with a cookie jar and no opinions.

    TWO OPENERS, ON PURPOSE. The shopkeeper's jar carries the session cookie
    that `POST /auth/signup` sets. `POST /store/order` REFUSES a request
    carrying that cookie — gawaah/storefront.py calls it a preview of what a
    customer sees and not a customer's order, and it is right — so the orders
    go out through `anon`, which has no jar at all.
    """

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.shop = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.anon = urllib.request.build_opener()

    def _do(self, opener, req: urllib.request.Request, timeout: int
            ) -> tuple[int, dict[str, Any]]:
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:  # noqa: BLE001 - a non-JSON body is still a status
                return e.code, {}
        except (urllib.error.URLError, OSError) as e:
            return 0, {"detail": f"{type(e).__name__}: {e}"}
        except json.JSONDecodeError as e:
            return 0, {"detail": f"the counter answered something that is not "
                                 f"JSON: {e}"}

    def get(self, path: str, timeout: int = 30) -> tuple[int, dict[str, Any]]:
        return self._do(self.shop,
                        urllib.request.Request(self.base + path), timeout)

    def json(self, method: str, path: str, body: dict[str, Any], *,
             anon: bool = False, timeout: int = 60
             ) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method=method)
        return self._do(self.anon if anon else self.shop, req, timeout)

    def multipart(self, path: str, fields: dict[str, str],
                  image: Optional[bytes] = None, *, timeout: int = 180
                  ) -> tuple[int, dict[str, Any]]:
        """A multipart POST, hand-rolled because this repo has no requests.

        The boundary is random per call rather than fixed: a fixed boundary
        that happens to appear inside a PNG's bytes truncates the upload, and
        the failure looks like a corrupt image rather than like a bug here.
        """
        boundary = "----gawaahseed" + secrets.token_hex(12)
        sep = f"--{boundary}\r\n".encode()
        out = bytearray()
        for k, v in fields.items():
            out += sep
            out += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
            out += str(v).encode("utf-8") + b"\r\n"
        if image is not None:
            out += sep
            out += (b'Content-Disposition: form-data; name="image"; '
                    b'filename="packshot.png"\r\n'
                    b'Content-Type: image/png\r\n\r\n')
            out += image + b"\r\n"
        out += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            self.base + path, data=bytes(out),
            headers={"Content-Type":
                     f"multipart/form-data; boundary={boundary}"},
            method="POST")
        return self._do(self.shop, req, timeout)


def why(res: dict[str, Any], code: int) -> str:
    """The counter's own sentence about a refusal, or the status if it had none."""
    if not isinstance(res, dict):
        return f"HTTP {code}"
    for key in ("detail", "message", "reason"):
        v = res.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return f"HTTP {code}"


def ok(code: int, res: dict[str, Any]) -> bool:
    return code in (200, 201) and res.get("ok") is not False


#: Refusals that mean THE SHOP IS ALREADY LIKE THAT, not that a write failed.
#: On a second run every filing comes back `nothing_to_change` from
#: gawaah/categories.py — the counter refusing to write a row identical to the
#: one it holds — and counting thirty of those as refusals reported a clean
#: re-run as "30 refused", which is the summary line telling the reader to go
#: and look for a problem that does not exist.
ALREADY_REASONS = frozenset({
    "nothing_to_change",                 # gawaah/categories.py, shopadmin.py
    "order_already_in_that_status",      # gawaah/storefront.py
})


class Report:
    """What happened, counted, so the end of the run can be one honest line."""

    def __init__(self, apply: bool) -> None:
        self.apply = apply
        self.done: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []

    def step(self, what: str, call, *, already: Optional[str] = None) -> Any:
        """Run one write, print one line, remember which bucket it fell in.

        `already` marks a step this shop is already in the state of. It is
        counted apart from a failure because "the salt is already on the shelf"
        and "the salt was refused" are different runs and a summary that adds
        them together is a summary nobody can act on.
        """
        if already is not None:
            print(f"  {C}have{X}   {what}\n         {D}{already}{X}")
            self.skipped.append((what, already))
            return None
        if not self.apply:
            print(f"  {D}would{X}  {what}")
            return None
        code, res = call()
        if ok(code, res):
            print(f"  {G}ok{X}     {what}")
            self.done.append(what)
            return res
        if str(res.get("reason") or "") in ALREADY_REASONS:
            print(f"  {C}have{X}   {what}")
            self.skipped.append((what, why(res, code)))
            return None
        print(f"  {Y}skip{X}   {what}\n         {D}{why(res, code)[:150]}{X}")
        self.failed.append((what, why(res, code)))
        return None


def head(title: str, sub: str = "") -> None:
    print(f"\n{B}{title}{X}" + (f"  {D}{sub}{X}" if sub else ""))


# ====================================================================== steps ==


def read_shop(till: Till) -> tuple[dict[str, dict[str, Any]], Optional[Path]]:
    """Every product the counter already holds, and where it keeps them."""
    code, res = till.get("/shop")
    if not ok(code, res):
        return {}, None
    rows = {s["sku_id"]: s for s in (res.get("skus") or [])}
    where = res.get("store_dir")
    return rows, (Path(where) if isinstance(where, str) and where else None)


def marker_path(store_dir: Optional[Path]) -> Optional[Path]:
    return None if store_dir is None else store_dir / MARKER


def step_account(till: Till, rep: Report) -> Optional[str]:
    """Open the counter's first account. Returns the password, once.

    The first account on a counter is free and every one after it needs an
    invitation — `gawaah/auth.py` says so and this script does not argue with
    it. A counter that already has an account is not an error here: the
    shopkeeper already exists and the seed goes on.
    """
    head("Shopkeeper", "the counter's own account, through POST /auth/signup")
    code, status = till.get("/auth/status")
    if ok(code, status) and status.get("accounts", 0):
        print(f"  {C}have{X}   this counter already has an account — "
              f"not creating a second one")
        print(f"         {D}A second account needs an invite from somebody "
              f"signed in (POST /auth/invite).{X}")
        return None

    # Generated here, printed once at the end, written to no file. `token_hex`
    # rather than a wordlist because a demo password that looks memorable is a
    # demo password that gets reused somewhere that matters.
    password = "kirana-" + secrets.token_hex(5)
    if not rep.apply:
        print(f"  {D}would{X}  open an account for {SHOPKEEPER_NAME} "
              f"({SHOPKEEPER_PHONE}) with a freshly generated password")
        return None
    res = rep.step(
        f"{SHOPKEEPER_NAME:22} {SHOPKEEPER_PHONE}  (owner)",
        lambda: till.json("POST", "/auth/signup", {
            "name": SHOPKEEPER_NAME, "phone": SHOPKEEPER_PHONE,
            "password": password}))
    return password if res is not None else None


def step_profile(till: Till, rep: Report) -> None:
    head("Shop", "name, address, phone and hours — PUT /shop/profile")
    code, cur = till.get("/shop/profile")
    if ok(code, cur) and cur.get("configured"):
        prof = cur.get("profile") or {}
        if prof.get("name") == SHOP_PROFILE["name"]:
            rep.step(f"{SHOP_PROFILE['name']}", lambda: (0, {}),
                     already="already set to this shop")
            return
        print(f"  {Y}note{X}   this counter is already called "
              f"{prof.get('name')!r}; overwriting it with "
              f"{SHOP_PROFILE['name']!r}")
    rep.step(f"{SHOP_PROFILE['name']:22} 07:00–22:00, every day",
             lambda: till.json("PUT", "/shop/profile", SHOP_PROFILE))


def step_categories(till: Till, rep: Report) -> dict[str, str]:
    """Make the categories this catalogue needs; return name -> category_id.

    The NAMES come from `gawaah/categories.RULES` — the counter's own keyword
    table — so a product filed here lands where that module would have proposed
    it, and a shopkeeper pressing "suggest" later sees agreement instead of a
    second opinion.
    """
    head("Categories", "gawaah/categories.py's own vocabulary — POST /categories")
    want = []
    for it in CATALOGUE:
        if it.category not in want:
            want.append(it.category)

    code, res = till.get("/categories")
    have: dict[str, str] = {}
    if ok(code, res):
        for c in (res.get("categories") or []):
            n = c.get("name")
            if isinstance(n, str):
                have[n] = c.get("category_id")

    for name in want:
        if name in have:
            rep.step(f"{name}", lambda: (0, {}), already="already a category here")
            continue
        res = rep.step(f"{name}",
                       lambda n=name: till.json("POST", "/categories", {"name": n}))
        if res:
            have[name] = ((res.get("category") or {}).get("category_id"))
        elif not rep.apply:
            have[name] = f"<new {name}>"
    return have


def step_products(till: Till, rep: Report, existing: dict[str, Any],
                  photo_dir: Optional[Path], *, photos: bool
                  ) -> dict[str, str]:
    """Add every product, with its picture. Returns sku -> how it was taught.

    TWO DOORS, AND WHICH ONE IS TRIED FIRST MATTERS.

      `/enrol` with `mode=plain_photo` embeds the tile into a descriptor and
      stores the product in the REAL catalogue (`gawaah/shop_store.py`). That
      is the door that puts a picture on the customer's storefront —
      `gawaah/storefront.py` looks for pictures through `taught_skus()`, which
      drops rows with no descriptor — and it is the only one that leaves a
      product the camera can name.

      `POST /shop` types the product in: a name, a price and a thumbnail in the
      appearance-only sidecar. The shopkeeper's own screens show the picture;
      the storefront cannot yet. `gawaah/shopadmin.py` documents that gap and
      says it is not that module's to fix.

    So enrol is tried first and the typed door is the fallback, and every row
    of the summary says which one this product came through. `region=user_drawn`
    is honest here for a reason that is not a shortcut: the tile IS the
    rectangle — this script drew it — so nothing is asking the segmenter to
    guess where the product is.
    """
    head("Products", f"{len(CATALOGUE)} packets, with a drawn pack shot each")
    if photos:
        f = font_report()
        print(f"  {D}fonts: latin {Path(f['latin']).name if f['latin'] else 'NONE'}"
              f" · devanagari "
              f"{Path(f['devanagari']).name if f['devanagari'] else 'NONE'}"
              f" · shaping {'yes' if f['shaping'] else 'NO'}{X}")
        if not f["devanagari_drawn"]:
            print(f"  {Y}note{X}   no shaped Devanagari on this machine, so the "
                  f"tiles carry the latin name only")

    how: dict[str, str] = {}
    for it in CATALOGUE:
        label = f"{it.latin[:34]:36} Rs {it.price_rupees}"
        if it.sku_id in existing:
            how[it.sku_id] = "already_here"
            rep.step(label, lambda: (0, {}),
                     already=f"{it.sku_id} is already in this shop at "
                             f"{existing[it.sku_id].get('price_rupees')}")
            continue
        if not rep.apply:
            print(f"  {D}would{X}  {label}   {D}{it.shelf} · {it.category}{X}")
            how[it.sku_id] = "would_add"
            continue

        img = None
        if photos:
            tile = pack_shot(it.pack)
            img = png_bytes(tile)
            if photo_dir is not None:
                try:
                    save_png(tile, photo_dir / f"{it.sku_id}.png")
                except OSError as exc:
                    print(f"  {Y}note{X}   could not keep a copy of "
                          f"{it.sku_id}.png ({exc})")

        res = None
        if img is not None:
            code, res_e = till.multipart("/enrol", {
                "sku_id": it.sku_id, "name": it.name,
                "price_rupees": it.price_rupees,
                "mode": "plain_photo", "region": "user_drawn"}, img)
            if ok(code, res_e):
                print(f"  {G}ok{X}     {label}   {D}taught from the tile{X}")
                rep.done.append(label)
                how[it.sku_id] = "enrolled"
                continue
            res = (code, res_e)

        body: dict[str, Any] = {"sku_id": it.sku_id, "name": it.name,
                                "price_rupees": it.price_rupees}
        if img is not None:
            body["photo_b64"] = base64.b64encode(img).decode("ascii")
        code, res_t = till.json("POST", "/shop", body)
        if ok(code, res_t):
            note = "typed in" if res is None else f"typed in — {why(*res[::-1])[:70]}"
            print(f"  {G}ok{X}     {label}   {D}{note}{X}")
            rep.done.append(label)
            how[it.sku_id] = "typed"
        else:
            print(f"  {Y}skip{X}   {label}\n         {D}{why(res_t, code)[:150]}{X}")
            rep.failed.append((label, why(res_t, code)))
            how[it.sku_id] = "refused"
    return how


def step_filing(till: Till, rep: Report, cats: dict[str, str],
                existing_before: dict[str, Any]) -> None:
    """Category and tags on every seeded product — PUT /categories/sku/{id}.

    The shelf is one of the tags. See the SHELVES block for why it has to be:
    there is no location field in this catalogue to put it in.
    """
    head("Filing", "a category, a shelf and tags on each — PUT /categories/sku/{id}")
    for it in CATALOGUE:
        cid = cats.get(it.category)
        tags = [it.shelf, *it.tags]
        label = f"{it.latin[:30]:32} {it.category:14} {it.shelf}"
        if cid is None:
            rep.failed.append((label, f"no category id for {it.category!r}"))
            print(f"  {Y}skip{X}   {label}\n         {D}the category "
                  f"{it.category!r} was not made{X}")
            continue
        rep.step(label, lambda i=it, c=cid, t=tags: till.json(
            "PUT", f"/categories/sku/{i.sku_id}",
            {"category_id": c, "tags": t}))

    # WHAT THE SHOP ALREADY HELD. Filed by the counter's OWN suggestion rather
    # than by a guess in this file: `GET /categories/suggest` reads the product
    # name against `RULES` and proposes, and only a proposal it actually made
    # is accepted. A product it cannot place stays uncategorised, which is the
    # honest state and the one the Categories screen is built to show.
    strangers = [s for s in existing_before if not any(
        i.sku_id == s for i in CATALOGUE)]
    if not strangers:
        return
    print(f"  {D}and the {len(strangers)} product(s) that were here before "
          f"this run, by the counter's own suggestion:{X}")
    code, res = till.get("/categories/suggest")
    # `proposals` rows carry `suggested_name`, the `matched_keyword` that got
    # there, and the `category_id` the counter would file it under — so the id
    # comes from the counter's own answer rather than from this script looking
    # a name up in a dict it built. A row with `ready: false` names a category
    # that does not exist yet and is left alone.
    #
    # `unmatched` IS READ SEPARATELY, and the difference matters on a re-run.
    # This endpoint only proposes for products that are NOT already filed, so
    # after one pass a product is absent from `proposals` for two completely
    # different reasons: no keyword matched it, or it is already where it
    # belongs. Reporting both as "no keyword matched" is a lie the second time
    # the script is run.
    proposals: dict[str, tuple[str, str, str]] = {}
    unmatched: set[str] = set()
    if ok(code, res):
        for row in (res.get("proposals") or []):
            sku, name = row.get("sku_id"), row.get("suggested_name")
            cid, word = row.get("category_id"), row.get("matched_keyword")
            if sku in strangers and row.get("ready") and isinstance(cid, str):
                proposals[sku] = (str(name), cid, str(word))
        unmatched = {u.get("sku_id") for u in (res.get("unmatched") or [])}
    for sku in strangers:
        name = existing_before[sku].get("name") or sku
        prop = proposals.get(sku)
        if prop is None:
            said = ("no keyword in the counter's own table matched its name"
                    if sku in unmatched else
                    "not proposed — it is already filed, or the category it "
                    "would need does not exist here")
            print(f"  {D}—      {name[:36]:38} {said}{X}")
            continue
        cat_name, cid, word = prop
        rep.step(f"{name[:28]:30} {cat_name:14} (matched {word!r})",
                 lambda s=sku, c=cid: till.json(
                     "PUT", f"/categories/sku/{s}",
                     {"category_id": c, "tags": ["was here before the seed"]}))


def step_stock(till: Till, rep: Report) -> None:
    """An opening count and a reorder level per product.

    A COUNT, NOT A DELIVERY. `POST /stock/{sku}/count` resets the baseline and
    records what the shelf actually held; `POST /stock/{sku}/in` claims a crate
    arrived. Seeding a shop is the first of those — nobody delivered anything —
    and using the delivery door would put a movement on the chain describing a
    van that never came.

    TWO PRODUCTS ARE DELIBERATELY SHORT. Dettol is under its reorder level and
    Amul Taaza is at zero, so the Low stock list and the Out of stock list have
    something true in them. A demo where every list is empty proves nothing
    about the lists.
    """
    head("Stock", "an opening count and a reorder level — POST /stock/{sku}/count")

    # Both of these SET rather than create, so a re-run cannot double a figure.
    # They are still skipped when the shop already holds the number, because
    # every count is a line on the stock chain: eight identical re-counts of
    # thirty products is 240 lines of a shopkeeper counting a shelf and finding
    # exactly what he found last time, and a log nobody can read is a log
    # nobody will read.
    code, book = till.get("/stock")
    counted: dict[str, Optional[int]] = {}
    levels: dict[str, Optional[int]] = {}
    if ok(code, book):
        for row in (book.get("items") or []):
            sku = str(row.get("sku_id"))
            counted[sku] = row.get("counted_units")
            levels[sku] = row.get("reorder_level")

    for it in CATALOGUE:
        if it.on_hand is None:
            continue
        flag = ""
        if it.on_hand == 0:
            flag = f"  {Y}out of stock{X}"
        elif it.reorder is not None and it.on_hand <= it.reorder:
            flag = f"  {Y}at/under reorder level{X}"
        label = f"{it.latin[:30]:32} counted {it.on_hand:>3}{flag}"
        if counted.get(it.sku_id) == it.on_hand:
            rep.step(label, lambda: (0, {}),
                     already="the last count on this shelf was this number")
            continue
        rep.step(label,
                 lambda i=it: till.json("POST", f"/stock/{i.sku_id}/count",
                                        {"units": i.on_hand}))
    print()
    for it in CATALOGUE:
        if it.reorder is None:
            continue
        label = f"{it.latin[:30]:32} tell me at {it.reorder:>3} left"
        if levels.get(it.sku_id) == it.reorder:
            rep.step(label, lambda: (0, {}), already="already set to this level")
            continue
        rep.step(label,
                 lambda i=it: till.json("POST", f"/stock/{i.sku_id}/reorder",
                                        {"units": i.reorder}))


def step_batches(till: Till, rep: Report) -> None:
    """Book the dated batches — and BOOK EACH ONE ONCE.

    THE SECOND WRITE THAT CREATES RATHER THAN SETS, and it cost something
    before it was caught: `POST /expiry/batch` mints a fresh `batch_id` on
    every call, so eight `--force` runs during development put eight identical
    crates of Amul Taaza on the Expiry screen. Nothing is wrong with the
    endpoint — a shopkeeper who books the same crate twice HAS booked it twice
    and the chain is right to hold both lines — the bug was here, in a seeder
    re-running a create as though it were a set.

    A batch has no natural key, so one is made from the three fields that
    identify the crate: which product, the date printed on it, and how many
    were in it. A batch matching all three is this seed's batch.
    """
    head("Dated batches", "one already expired, one short-dated — POST /expiry/batch")
    today = _dt.date.today()

    code, book = till.get("/expiry/batches")
    have: set[tuple[str, str, int]] = set()
    if ok(code, book):
        for row in (book.get("batches") or []):
            try:
                have.add((str(row.get("sku_id")), str(row.get("expires_on")),
                          int(row.get("units"))))
            except (TypeError, ValueError):      # a row this build cannot read
                continue

    for sku, units, offset, note in BATCHES:
        on = (today + _dt.timedelta(days=offset)).isoformat()
        when = ("EXPIRED" if offset < 0 else
                f"{offset} day{'' if offset == 1 else 's'} left")
        label = f"{sku[:30]:32} {units:>3} units, {on}  ({when})"
        if (sku, on, units) in have:
            rep.step(label, lambda: (0, {}),
                     already="this crate is already booked — booking it again "
                             "is a second crate, and there is only one")
            continue
        rep.step(label,
                 lambda s=sku, u=units, o=on, n=note: till.json(
                     "POST", "/expiry/batch",
                     {"sku_id": s, "units": u, "expires_on": o,
                      "note": n, "stock_in": False}))


def step_orders(till: Till, rep: Report) -> None:
    """Three storefront orders, none of them paid.

    NOTHING HERE IS GREEN AND NOTHING HERE MINTS. An order is a request to the
    shop; it becomes money only when the gateway sends a signed webhook and
    `gawaah/webhook.py` adjudicates it. These are placed and then walked along
    the ordinary status ladder — new, preparing, out for delivery — which is
    about a rider, not about a rupee.
    """
    head("Storefront orders", "placed as a customer's phone places them — "
                              "unpaid, amber")
    print(f"  {D}Not one of these is marked paid and none mints a payment "
          f"link. Green is the gateway's word.{X}")

    # THE ONE STEP THAT IS NOT IDEMPOTENT AT THE SERVER. Every other write here
    # is a PUT, a replace or a levelling count, so a second run lands on the
    # same state; `POST /store/order` mints a new order id every time and a
    # re-run would quietly triple the order book. There is no "find my order"
    # route, so the check is done here: an order already standing from this
    # customer's number is this seed's order, and it is left alone.
    code, book = till.get("/orders")
    placed: set[str] = set()
    if ok(code, book):
        for row in (book.get("orders") or []):
            phone = ((row.get("customer") or {}).get("phone") or "")
            if phone:
                placed.add(str(phone))

    for spec in ORDERS:
        if spec["phone"] in placed:
            rep.step(f"{spec['name'][:18]:20} already ordered", lambda: (0, {}),
                     already=f"an order from {spec['phone']} is already in the "
                             f"book; a second would be a second order, not the "
                             f"same one")
            continue
        items = [{"sku_id": s, "qty": q} for s, q in spec["items"]]
        label = (f"{spec['name'][:18]:20} {len(items)} line(s)"
                 f" -> {spec['advance_to'] or 'new'}")
        res = rep.step(label, lambda sp=spec, it=items: till.json(
            "POST", "/store/order",
            {"items": it, "name": sp["name"], "phone": sp["phone"],
             "address": sp["address"]}, anon=True))
        if not res:
            continue
        order_id = res.get("order_id")
        want = spec["advance_to"]
        if not order_id or not want:
            continue
        # The ladder is walked one rung at a time because gawaah/storefront.py
        # refuses a jump: new -> out_for_delivery is not a legal move and being
        # refused for it is correct behaviour, not something to work around.
        ladder = ["preparing", "out_for_delivery", "delivered"]
        for rung in ladder[:ladder.index(want) + 1]:
            rep.step(f"  {order_id} -> {rung}",
                     lambda o=order_id, s=rung: till.json(
                         "POST", f"/orders/{o}/status", {"status": s}))


def step_shelf_reads(till: Till, rep: Report, *, photos: bool) -> None:
    """Photograph each shelf and count what is facing out — POST /shelf/count.

    THIS IS THE REAL SHELF MODEL AND IT IS A CAMERA. `gawaah/shelf.py` holds no
    planogram; a shelf there is a LABEL on a photograph plus a count of the
    facings the detector found and the shop's own vectors named. So the only
    honest way to put anything on the Shelf screen is to take a picture of a
    shelf and press count, which is what this does.

    OFF BY DEFAULT, AND HERE IS THE MEASUREMENT THAT DECIDED THAT. A frame
    composed out of these drawn tiles is not a photograph of a shelf, and the
    counter is right to say so. Measured over all seven shelves on this
    catalogue, thirty packets put out: the detector proposed 37 regions — more
    than there are packets, because the accent band splits a pack into two
    contours — and the identifier named FOUR of them. Every other region came
    back honestly unnamed, top1 usually the right product at a cosine in the
    0.4s against a 0.60 bar.

    The reason is one framing mismatch, and it is measurable a second way: an
    attempt to teach the same product a second view from a shelf-style crop
    scored 0.07 to 0.44 against its own stored view and `POST /shop/{id}/view`
    refused six of seven as too different to be the same packet. So this is not
    a gate that needs loosening; it is a photograph that is not of a shelf.

    Left as a flag because a screen full of "0 named" demonstrates abstention
    well and counting badly. Nothing here forces a name onto a region the
    counter would not name by itself.
    """
    head("Shelf reads", "a photograph per shelf, counted — POST /shelf/count")
    if not photos:
        print(f"  {D}skipped: --no-photos{X}")
        return
    print(f"  {Y}These frames are composed from the drawn tiles, not "
          f"photographed.{X}")
    print(f"  {D}Measured on this catalogue: 37 regions proposed for 30 "
          f"packets (the accent{X}")
    print(f"  {D}band splits a pack in two) and 4 of them named. The counter "
          f"abstains on the{X}")
    print(f"  {D}rest, correctly — the frame is the problem, not the gate.{X}")
    by_shelf: dict[str, list[Item]] = {}
    for it in CATALOGUE:
        by_shelf.setdefault(it.shelf, []).append(it)
    for tag, label in SHELVES.items():
        items = by_shelf.get(tag) or []
        if not items:
            continue
        if not rep.apply:
            print(f"  {D}would{X}  {label:34} {len(items)} packet(s) put out")
            continue
        try:
            img = png_bytes(shelf_photo(items))
        except Exception as exc:  # noqa: BLE001 - a drawing failure is not fatal
            print(f"  {Y}skip{X}   {label}\n         {D}could not draw the "
                  f"shelf ({type(exc).__name__}: {exc}){X}")
            rep.failed.append((label, f"{type(exc).__name__}: {exc}"))
            continue
        code, res = till.multipart("/shelf/count",
                                   {"label": label, "annotate": "0"}, img)
        if ok(code, res):
            regions = res.get("regions") or []
            named = sum(1 for r in regions if r.get("state") == "named")
            print(f"  {G}ok{X}     {label:34} {named} named of "
                  f"{len(regions)} region(s) {D}({len(items)} put out){X}")
            rep.done.append(f"shelf read: {label}")
        else:
            print(f"  {Y}skip{X}   {label}\n         {D}{why(res, code)[:150]}{X}")
            rep.failed.append((label, why(res, code)))


#: The composed frame, in pixels. Two rows rather than one long shelf, and
#: MEASURED rather than chosen: seven packets in a single row make a 2400x537
#: frame in which each packet is 1.4% of the area, and the detector proposed
#: ONE region out of seven. The same seven over two rows of a 1700x1500 frame —
#: each packet nearer 4% — proposed ten. `gawaah/detector.MIN_AREA_FRAC` is
#: 0.004, so neither frame is refused by the gate; the wide one simply gives
#: the contour proposer almost nothing to work with.
SHELF_FRAME = (1700, 1500)
SHELF_PACK_PX = 560


def shelf_photo(items: list[Item]):
    """The products of one shelf, stood out where the camera can see them.

    Drawn rather than photographed, like everything else here, and composed
    from each product's own CUT-OUT rather than from whole tiles: pasting the
    tiles side by side puts each packet inside its own pale square and the
    detector finds the squares — measured, two regions out of seven.

    THE GAPS ARE WIDE ON PURPOSE. gawaah/shelf.py's own stated limit is that
    packets closer together than about a finger's width read as ONE region and
    a tightly packed row under-counts. A seeded shelf that trips that by
    accident teaches the wrong lesson about the product; a shopkeeper who wants
    to see that failure can push the packets together and photograph it.
    """
    from PIL import Image, ImageDraw, ImageFilter  # local: --no-photos skips this

    w, h = SHELF_FRAME
    rows = 1 if len(items) <= 3 else 2
    board = Image.new("RGB", (w, h), (238, 234, 226))
    per = (len(items) + rows - 1) // max(1, rows)
    band = h // max(1, rows)

    for r in range(rows):
        chunk = items[r * per:(r + 1) * per]
        if not chunk:
            continue
        cuts = []
        for it in chunk:
            cut = pack_shot(it.pack, 520, cut_out=True)
            bbox = cut.getbbox()
            if bbox is None:                     # pragma: no cover - a blank pack
                continue
            cut = cut.crop(bbox)
            k = SHELF_PACK_PX / float(cut.height)
            cuts.append(cut.resize((max(1, int(cut.width * k)), SHELF_PACK_PX),
                                   Image.LANCZOS))
        if not cuts:
            continue
        gap = (w - sum(c.width for c in cuts)) // (len(cuts) + 1)
        x = gap
        for c in cuts:
            y = r * band + (band - c.height) // 2
            # A contact shadow. Without one every packet looks pasted on, and
            # the detector's edge cue has nothing at the foot to work with.
            sh = Image.new("L", (c.width + 70, c.height + 70), 0)
            ImageDraw.Draw(sh).rounded_rectangle(
                (30, 30, c.width + 50, c.height + 50), 40, fill=90)
            sh = sh.filter(ImageFilter.GaussianBlur(16))
            board.paste(Image.new("RGB", sh.size, (120, 114, 104)),
                        (x - 25, y - 15), sh)
            board.paste(c, (x, y), c)
            x += c.width + gap
    return board


# ======================================================================= main ==


def preflight(till: Till) -> bool:
    code, res = till.get("/health")
    if code == 0:
        print(f"  {R}The till is not answering on {till.base}.{X}")
        print(f"  {D}Start it with: ./.venv/bin/python tools/upload_app.py{X}")
        return False
    if not ok(code, res):
        print(f"  {R}{till.base}/health answered {code}: {why(res, code)}{X}")
        return False
    deps = res.get("dependencies") or {}
    for name in ("embedder", "shop_store"):
        if not (deps.get(name) or {}).get("available", True):
            print(f"  {Y}note{X}   the counter's {name} is unavailable "
                  f"({(deps.get(name) or {}).get('reason')}) — products will "
                  f"be typed in rather than taught")
    return True


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Seed a believable Indian kirana onto this counter.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what it would do and write nothing")
    ap.add_argument("--force", action="store_true",
                    help="seed again even though this counter is already seeded")
    ap.add_argument("--till", default=TILL, help=f"counter base URL ({TILL})")
    ap.add_argument("--no-photos", action="store_true",
                    help="skip the drawn pack shots and the shelf reads")
    ap.add_argument("--photo-dir", default=None,
                    help="where to keep a copy of each tile "
                         "(default: <shop dir>/seed_packshots)")
    ap.add_argument("--skip-orders", action="store_true",
                    help="do not place the three storefront orders")
    ap.add_argument("--shelf-reads", action="store_true",
                    help="also photograph each shelf and press count. Off by "
                         "default: a frame composed from drawn tiles is not a "
                         "photograph of a shelf and the counter abstains on "
                         "most regions — see step_shelf_reads. NOT idempotent, "
                         "and cannot be: every run is another photograph and "
                         "another read on the chain, which is what a camera "
                         "does")
    a = ap.parse_args(argv)

    till = Till(a.till)
    print(f"\n{B}GAWAAH — seed a kirana{X}  {D}{till.base}{X}")
    if not preflight(till):
        return 1

    existing, store_dir = read_shop(till)
    mark = marker_path(store_dir)
    print(f"  {D}shop directory (the server's own answer): {store_dir}{X}")
    print(f"  {D}{len(existing)} product(s) already here{X}")

    if mark is not None and mark.exists() and not a.force and not a.dry_run:
        try:
            was = json.loads(mark.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            was = {}
        print(f"\n  {R}This counter is already seeded.{X} "
              f"{D}{mark}{X}")
        print(f"  {D}seeded at {was.get('at')} — {len(was.get('skus') or [])} "
              f"product(s){X}")
        print(f"  Run again with {B}--force{X} to fill in whatever is missing, "
              f"or {B}--dry-run{X} to see what that would be.")
        print(f"  {D}Nothing was written.{X}\n")
        return 2

    rep = Report(apply=not a.dry_run)
    if a.dry_run:
        print(f"\n  {Y}Dry run.{X} Nothing will be written.")

    photo_dir = None
    if not a.no_photos:
        photo_dir = (Path(a.photo_dir) if a.photo_dir
                     else (store_dir / "seed_packshots" if store_dir else None))

    password = step_account(till, rep)
    step_profile(till, rep)
    cats = step_categories(till, rep)
    how = step_products(till, rep, existing, photo_dir, photos=not a.no_photos)
    step_filing(till, rep, cats, existing)
    step_stock(till, rep)
    step_batches(till, rep)
    if not a.skip_orders:
        step_orders(till, rep)
    if a.shelf_reads:
        step_shelf_reads(till, rep, photos=not a.no_photos)

    # ------------------------------------------------------------ the marker --
    if rep.apply and mark is not None:
        try:
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(json.dumps({
                "format": MARKER_FORMAT,
                "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "by": "tools/seed_shop.py",
                "skus": [i.sku_id for i in CATALOGUE],
                "taught": how,
                "shelves": SHELVES,
                "photos": "drawn by tools/packshot.py — not photographs",
                "money": ("nothing here settled, minted or was marked paid; "
                          "no bill was written"),
            }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"\n  {Y}note{X}   could not write {mark} ({exc}); a second "
                  f"run will not know this one happened")

    # ----------------------------------------------------------- the summary --
    print(f"\n{B}Summary{X}")
    if not rep.apply:
        print(f"  {Y}Nothing was written.{X} Run without {B}--dry-run{X} to apply.\n")
        return 0
    print(f"  {G}{len(rep.done)} written{X}"
          + (f", {C}{len(rep.skipped)} already right{X}" if rep.skipped else "")
          + (f", {Y}{len(rep.failed)} refused{X}" if rep.failed else ""))
    if photo_dir is not None:
        print(f"  {D}pack shots kept in {photo_dir}{X}")

    print(f"\n{B}What this did NOT do{X}")
    print(f"  {D}No bill was written. A bill on this counter is the fold of a{X}")
    print(f"  {D}session's own chain and there is no honest way to make one from{X}")
    print(f"  {D}outside; a seeded bill is money the shop owes itself. The three{X}")
    print(f"  {D}orders above are UNPAID — no link was minted, nothing is green.{X}")
    print(f"  {D}The pack shots are DRAWN, not photographed, and say so on the{X}")
    print(f"  {D}tile and in the PNG metadata.{X}")

    if password:
        print(f"\n{B}Sign in{X}  {D}printed once, here, and stored nowhere{X}")
        print(f"  phone     {B}{SHOPKEEPER_PHONE}{X}")
        print(f"  password  {B}{password}{X}")
        print(f"  {D}Written to no file and not in any URL. Copy it now — this{X}")
        print(f"  {D}script cannot print it again, and the counter keeps only a{X}")
        print(f"  {D}scrypt hash of it.{X}")
    print()
    return 0 if not rep.failed else 3


if __name__ == "__main__":
    sys.exit(main())
