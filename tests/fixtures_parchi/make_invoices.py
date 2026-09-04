#!/usr/bin/env python3
"""Five printed distributor invoices for the seeded catalogue, drawn with PIL.

Run from the repo root:  .venv/bin/python tests/fixtures_parchi/make_invoices.py

Writes invoice_1.png … invoice_5.jpg beside this file, and `truth.json`: for
each invoice, the exact JSON a perfect reading of it would produce (the shape
`gawaah/parchi.py` asks the model for) and what the pipeline is expected to
make of it. The tests feed `truth.json` through a FAKE transport, so they run
without a model; the bench (`bench.py`) sends the PNGs to the real one and
scores its reading against the same file.

What the five cover:
    #1  Sharma Distributors — the demo bill. Six lines, mixed exact names and
        one abbreviation ("MAGGI 2-MIN NOODLES 70G"), a 5% GST footer. Adds
        up to the paisa.
    #2  Gupta Agencies — staples, seven lines, one of which (BOURNVITA 500G)
        is not in the catalogue: the exception row.
    #3  Sharma Distributors again — DELIBERATELY WRONG BY ONE PAISA on line 2
        (36 × 11.75 is 423.00; the bill prints 423.01). The subtotal and the
        taxes are consistent with the misprinted amount, so exactly one
        thing fails: that line's arithmetic. This is the bill the demo uses
        to show the gate refusing by name.
    #4  Verma Traders — personal care, 18% GST, rotated 1.4° with sensor noise.
    #5  Raj Snacks & Beverages — rates inclusive of GST, no tax line, rotated
        −1.2° with noise.

Every figure is integer paise; the generator divides nothing. Taxes are
floored to the paisa, which is what a distributor's software does.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

W, H = 1240, 1754            # A4 at 150 dpi
PAPER = (250, 249, 245)
INK = (24, 24, 28)
FAINT = (110, 110, 118)
RULE = (150, 150, 158)

FONT_DIRS = ("/System/Library/Fonts/Supplemental", "/Library/Fonts",
             "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/msttcorefonts")


def _font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)  # type: ignore[return-value]


HEAD = lambda s: _font(("Arial Bold.ttf", "DejaVuSans-Bold.ttf"), s)   # noqa: E731
BODY = lambda s: _font(("Arial.ttf", "DejaVuSans.ttf"), s)             # noqa: E731
MONO = lambda s: _font(("Courier New.ttf", "DejaVuSansMono.ttf"), s)   # noqa: E731
MONOB = lambda s: _font(("Courier New Bold.ttf", "DejaVuSansMono-Bold.ttf"), s)  # noqa: E731


def rs(p: int) -> str:
    """Integer paise as the rupee string a bill prints. No float, no division
    that can round: `//` and `%` on an integer."""
    sign = "-" if p < 0 else ""
    p = abs(p)
    return f"{sign}{p // 100}.{p % 100:02d}"


def from_rs(s: str) -> int:
    whole, _, frac = s.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


# ------------------------------------------------------------ the bills --
#
# (printed name, catalogue sku it should match, qty, rate as printed)
# `sku` None means the product is not in the seeded catalogue.

INVOICES: list[dict] = [
    {
        "file": "invoice_1.png",
        "supplier": {"name": "Sharma Distributors", "phone": "98200 44711"},
        "address": "14, Kalbadevi Road, Mumbai 400002 · GSTIN 27AABCS1429B1ZQ",
        "invoice_no": "SD/2026/0917",
        "date": "03/09/2026", "date_iso": "2026-09-03",
        "tax_pct_tenths": 25,     # CGST 2.5% + SGST 2.5%
        "tax_labels": ("CGST 2.5%", "SGST 2.5%"),
        "rotate_deg": 0.0, "noise": False,
        "lines": [
            ("PARLE-G BISCUIT 100G", "parle_g_biscuit", 48, "8.20"),
            ("MAGGI 2-MIN NOODLES 70G", "maggi_noodles_70g", 24, "11.75"),
            ("TATA SALT IODISED 1KG", "tata_salt_1kg", 12, "25.50"),
            ("AMUL BUTTER 100G", "amul_butter_100g", 10, "54.00"),
            ("LIFEBUOY TOTAL SOAP 125G", "lifebuoy_soap_125g", 20, "31.25"),
            ("SURF EXCEL EASY WASH 1KG", "surf_excel_1kg", 6, "118.00"),
        ],
        "hsn": ("1905", "1902", "2501", "0405", "3401", "3402"),
    },
    {
        "file": "invoice_2.png",
        "supplier": {"name": "Gupta Agencies", "phone": "022 2345 8890"},
        "address": "Shop 3, APMC Market, Vashi, Navi Mumbai 400703 · GSTIN 27AAGFG2210C1Z5",
        "invoice_no": "GA-4471",
        "date": "01/09/2026", "date_iso": "2026-09-01",
        "tax_pct_tenths": 25,
        "tax_labels": ("CGST 2.5%", "SGST 2.5%"),
        "rotate_deg": 0.0, "noise": False,
        "lines": [
            ("AASHIRVAAD ATTA 5KG", "aashirvaad_atta_5kg", 8, "238.00"),
            ("BASMATI RICE 5KG", "basmati_rice_5kg", 4, "465.00"),
            ("TOOR DAL 1KG", "toor_dal_1kg", 10, "152.00"),
            ("CHEENI SUGAR 1KG", "cheeni_sugar_1kg", 20, "46.50"),
            ("FORTUNE SUNFLOWER OIL 1L", "fortune_sunflower_1l", 12, "148.00"),
            ("TATA TEA GOLD 250G", "tata_tea_gold_250g", 6, "158.00"),
            ("BOURNVITA 500G", None, 6, "195.00"),
        ],
        "hsn": ("1101", "1006", "0713", "1701", "1512", "0902", "1806"),
    },
    {
        "file": "invoice_3.png",
        "supplier": {"name": "Sharma Distributors", "phone": "98200 44711"},
        "address": "14, Kalbadevi Road, Mumbai 400002 · GSTIN 27AABCS1429B1ZQ",
        "invoice_no": "SD/2026/0931",
        "date": "04/09/2026", "date_iso": "2026-09-04",
        "tax_pct_tenths": 25,
        "tax_labels": ("CGST 2.5%", "SGST 2.5%"),
        "rotate_deg": 0.0, "noise": False,
        "lines": [
            ("PARLE-G BISCUIT 100G", "parle_g_biscuit", 24, "8.20"),
            ("MAGGI 2-MIN NOODLES 70G", "maggi_noodles_70g", 36, "11.75"),
            ("RED LABEL TEA 250G", "red_label_250g", 6, "132.50"),
            ("VIM BAR 200G", "vim_bar_200g", 30, "17.60"),
        ],
        "hsn": ("1905", "1902", "0902", "3401"),
        # THE DELIBERATE ERROR: line 2's printed amount is one paisa over.
        "misprint": {"line": 1, "off_by_paise": 1},
    },
    {
        "file": "invoice_4.jpg",
        "supplier": {"name": "Verma Traders", "phone": "98337 10422"},
        "address": "Gala 7, Sakinaka Industrial Estate, Mumbai 400072 · GSTIN 27ABQPV5521M1ZD",
        "invoice_no": "VT/1188",
        "date": "02-09-2026", "date_iso": "2026-09-02",
        "tax_pct_tenths": 90,     # CGST 9% + SGST 9%
        "tax_labels": ("CGST 9%", "SGST 9%"),
        "rotate_deg": 1.4, "noise": True,
        "lines": [
            ("COLGATE STRONG TEETH 100G", "colgate_strong_100g", 12, "49.00"),
            ("DETTOL ORIGINAL SOAP 125G", "dettol_soap_125g", 24, "38.50"),
            ("CLINIC PLUS SHAMPOO SACHET 5ML X16", "clinic_plus_sachet", 10, "25.60"),
            ("PARACHUTE COCONUT OIL 100ML", "parachute_oil_100ml", 12, "41.00"),
            ("HARPIC 500ML", "harpic_500ml", 6, "74.00"),
            ("VIM DISHWASH BAR 200G", "vim_bar_200g", 24, "17.60"),
        ],
        "hsn": ("3306", "3401", "3305", "1513", "3402", "3401"),
    },
    {
        "file": "invoice_5.jpg",
        "supplier": {"name": "Raj Snacks & Beverages", "phone": "99870 61255"},
        "address": "Plot 22, MIDC Andheri East, Mumbai 400093 · GSTIN 27AAKCR7781E1Z2",
        "invoice_no": "RSB-2209",
        "date": "30/08/2026", "date_iso": "2026-08-30",
        "tax_pct_tenths": 0,      # rates inclusive; no tax line
        "tax_labels": (),
        "rotate_deg": -1.2, "noise": True,
        "lines": [
            ("KURKURE MASALA MUNCH 70G", "kurkure_masala_70g", 30, "16.00"),
            ("HALDIRAM ALOO BHUJIA 200G", "haldirams_bhujia_200g", 12, "44.00"),
            ("FROOTI 150ML", "frooti_150ml", 48, "8.10"),
            ("THUMS UP 750ML", "thums_up_750ml", 24, "32.00"),
            ("DAIRY MILK 50G", "dairy_milk_50g", 30, "36.00"),
            ("GOOD DAY CASHEW 100G", "good_day_cashew_100g", 24, "24.00"),
            ("MARIE GOLD 250G", "marie_gold_250g", 12, "36.50"),
        ],
        "hsn": ("1905", "2106", "2202", "2202", "1806", "1905", "1905"),
    },
]

BUYER = "VERMA KIRANA STORE, Shop 4, Sai Darshan CHS, Ghatkopar (W), Mumbai 400086"


def figures(inv: dict) -> dict:
    """Every printed figure of one bill, in integer paise, plus the answer a
    perfect reading gives and what the pipeline should decide."""
    rows = []
    for i, (name, sku, qty, rate) in enumerate(inv["lines"]):
        rate_p = from_rs(rate)
        amount_p = rate_p * qty
        printed_p = amount_p
        mis = inv.get("misprint")
        if mis and mis["line"] == i:
            printed_p = amount_p + int(mis["off_by_paise"])
        rows.append({"name": name, "sku": sku, "qty": qty, "rate_paise": rate_p,
                     "true_paise": amount_p, "printed_paise": printed_p})
    subtotal_p = sum(r["printed_paise"] for r in rows)
    taxes = []
    for label in inv["tax_labels"]:
        taxes.append({"label": label,
                      "amount_paise": subtotal_p * int(inv["tax_pct_tenths"]) // 1000})
    total_p = subtotal_p + sum(t["amount_paise"] for t in taxes)

    answer = {
        "supplier": dict(inv["supplier"]),
        "invoice_no": inv["invoice_no"],
        "date": inv["date_iso"],
        "lines": [{"name": r["name"], "qty": r["qty"], "rate": rs(r["rate_paise"]),
                   "amount": rs(r["printed_paise"])} for r in rows],
        "subtotal": rs(subtotal_p),
        "taxes": [{"label": t["label"], "amount": rs(t["amount_paise"])} for t in taxes],
        "printed_total": rs(total_p),
    }
    failing = [i for i, r in enumerate(rows) if r["printed_paise"] != r["true_paise"]]
    expect = {
        "gate_ok": not failing,
        "failing_lines": failing,
        "skus": [r["sku"] for r in rows],
        "in_catalogue": sum(1 for r in rows if r["sku"]),
        "not_in_catalogue": sum(1 for r in rows if not r["sku"]),
        "subtotal_paise": subtotal_p,
        "total_paise": total_p,
    }
    return {"rows": rows, "taxes": taxes, "subtotal_paise": subtotal_p,
            "total_paise": total_p, "answer": answer, "expect": expect}


# ------------------------------------------------------------- drawing --

def draw(inv: dict, fig: dict, seed: int) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    x0, x1 = 90, W - 90

    # Header: the seller, big; the address; the GSTIN line.
    d.text((x0, 70), inv["supplier"]["name"].upper(), font=HEAD(44), fill=INK)
    d.text((x0, 128), inv["address"], font=BODY(20), fill=FAINT)
    d.text((x0, 156), f"Phone: {inv['supplier']['phone']}", font=BODY(20), fill=FAINT)
    d.text((x1 - 260, 78), "TAX INVOICE", font=HEAD(28), fill=INK)
    d.line((x0, 200, x1, 200), fill=RULE, width=2)

    # Invoice number, date, buyer.
    d.text((x0, 222), f"Invoice No: {inv['invoice_no']}", font=BODY(22), fill=INK)
    d.text((x0 + 480, 222), f"Date: {inv['date']}", font=BODY(22), fill=INK)
    d.text((x0, 262), "Bill To:", font=BODY(18), fill=FAINT)
    d.text((x0, 288), BUYER, font=BODY(20), fill=INK)
    d.line((x0, 340, x1, 340), fill=RULE, width=2)

    # The table.
    cols = {"sno": x0, "item": x0 + 70, "hsn": x0 + 590, "qty": x0 + 720,
            "rate": x0 + 860, "amount": x1}
    y = 362
    hf = HEAD(19)
    d.text((cols["sno"], y), "S.No", font=hf, fill=INK)
    d.text((cols["item"], y), "Item", font=hf, fill=INK)
    d.text((cols["hsn"], y), "HSN", font=hf, fill=INK)
    d.text((cols["qty"], y), "Qty", font=hf, fill=INK)
    d.text((cols["rate"], y), "Rate", font=hf, fill=INK)
    d.text((cols["amount"], y), "Amount", font=hf, fill=INK, anchor="ra")
    y += 34
    d.line((x0, y, x1, y), fill=RULE, width=1)
    y += 14

    mf = MONO(23)
    for i, r in enumerate(fig["rows"]):
        d.text((cols["sno"], y), f"{i + 1}", font=mf, fill=INK)
        d.text((cols["item"], y), r["name"], font=mf, fill=INK)
        d.text((cols["hsn"], y), inv["hsn"][i], font=mf, fill=INK)
        d.text((cols["qty"] + 40, y), f"{r['qty']}", font=mf, fill=INK, anchor="ra")
        d.text((cols["rate"] + 90, y), rs(r["rate_paise"]), font=mf, fill=INK, anchor="ra")
        d.text((cols["amount"], y), rs(r["printed_paise"]), font=mf, fill=INK, anchor="ra")
        y += 46
    y += 8
    d.line((x0, y, x1, y), fill=RULE, width=1)
    y += 20

    # Footer figures, right-aligned in a column.
    lf = BODY(22)
    mb = MONOB(24)
    labx = cols["rate"] - 120

    def foot(label: str, val: str, bold: bool = False) -> None:
        nonlocal y
        d.text((labx, y), label, font=lf, fill=INK)
        d.text((cols["amount"], y), val, font=mb if bold else mf, fill=INK, anchor="ra")
        y += 40

    foot("Subtotal", rs(fig["subtotal_paise"]))
    for t in fig["taxes"]:
        foot(t["label"], rs(t["amount_paise"]))
    if not fig["taxes"]:
        d.text((x0, y + 6), "All rates inclusive of GST.", font=BODY(19), fill=FAINT)
    d.line((labx, y, x1, y), fill=INK, width=2)
    y += 10
    foot("TOTAL", rs(fig["total_paise"]), bold=True)

    d.text((x0, y + 30), f"Rupees {words(fig['total_paise'])} only.",
           font=BODY(19), fill=FAINT)
    d.text((x0, H - 200), "Goods once sold will not be taken back. E.&O.E.",
           font=BODY(18), fill=FAINT)
    d.text((x1 - 300, H - 200), f"For {inv['supplier']['name']}", font=BODY(20), fill=INK)
    d.text((x1 - 300, H - 130), "Authorised Signatory", font=BODY(18), fill=FAINT)

    # Two of the five are a phone photograph, not a scan: a small rotation and
    # sensor noise, and nothing else. A bill that cannot survive that is a bill
    # the model would fail on at a real counter.
    if inv["rotate_deg"]:
        img = img.rotate(inv["rotate_deg"], resample=Image.BICUBIC, expand=False,
                         fillcolor=(236, 234, 228))
    if inv["noise"]:
        img = _noise(img, seed)
    return img


def _noise(img: Image.Image, seed: int) -> Image.Image:
    """Gaussian sensor noise, sigma ~6 of 255, deterministic per seed."""
    import numpy as np
    rng = np.random.default_rng(seed)
    arr = np.asarray(img).astype(np.int16)
    arr = arr + rng.normal(0, 6, arr.shape).astype(np.int16)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _small(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")
    return _ONES[n // 100] + " hundred" + (f" {_small(n % 100)}" if n % 100 else "")


def words(p: int) -> str:
    """'two thousand nine hundred ninety seven and paise thirty two' — the
    line every Indian bill carries under the total."""
    rupees, paise = p // 100, p % 100
    parts = []
    if rupees >= 100000:
        parts.append(_small(rupees // 100000) + " lakh")
        rupees %= 100000
    if rupees >= 1000:
        parts.append(_small(rupees // 1000) + " thousand")
        rupees %= 1000
    if rupees:
        parts.append(_small(rupees))
    out = " ".join(parts) or "zero"
    if paise:
        out += f" and paise {_small(paise)}"
    return out


def main() -> None:
    random.seed(7)
    truth = {"format": 1, "invoices": []}
    for n, inv in enumerate(INVOICES, 1):
        fig = figures(inv)
        img = draw(inv, fig, seed=n)
        # The two phone-photograph bills are JPEG, as a phone would send them:
        # sensor noise defeats PNG compression and made each one 4 MB.
        if inv["file"].endswith(".jpg"):
            img.save(HERE / inv["file"], "JPEG", quality=88, optimize=True)
        else:
            img.save(HERE / inv["file"], "PNG", optimize=True)
        truth["invoices"].append({
            "n": n, "file": inv["file"],
            "supplier": inv["supplier"],
            "rotate_deg": inv["rotate_deg"], "noise": inv["noise"],
            "misprint": inv.get("misprint"),
            "answer": fig["answer"],
            "expect": fig["expect"],
        })
        print(f"{inv['file']}: {len(inv['lines'])} lines, total {rs(fig['total_paise'])}"
              f"{'  (misprinted, one paisa)' if inv.get('misprint') else ''}")
    (HERE / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    print("truth.json written")


if __name__ == "__main__":
    main()
