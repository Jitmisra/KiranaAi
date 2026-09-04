"""LABELS — a price and a code for the packets that carry neither.

A kirana sells loose things: a pack of atta weighed out on the counter, a jar
of home-made pickle, a bundle of agarbatti tied with thread. None of those has
a barcode, so the camera cannot read one, and the counter falls back to
appearance — which is slower and, by design, abstains when it is not sure. The
fix that every shop already knows is a sticker. This module prints the sheet.

The counter already draws one product code at `GET /qr/{sku_id}`: a PNG of the
string `gawaah:<sku_id>`, which the till resolves straight to the product. This
module lays that same code out MANY AT A TIME on the sticker sheets a stationer
actually sells — A4 grids of 65, 40, 24, 21, 14 or 8 — with the product's name
and its price beside each one, as one self-contained HTML page a browser prints
at 100 %. And it prints a SHELF TALKER: one product, one sheet, the price set
large enough to read from the other side of the counter.

WHAT IS ON THE STICKER, AND WHAT IS NOT
=======================================
The code names the product and nothing else. It is `gawaah:<sku_id>`, the same
string the single sticker carries, and it holds no price. The price printed
BESIDE it is the shop's MARKED price — the shelf-edge number, not today's offer
— because a sticker outlives an offer by months and a sticker that promises a
discount the shop stopped running is a lie stuck to a packet. The till prices
every scan from the catalogue at the moment of the sale, so a wrong sticker
cannot make a wrong bill; it can only make a customer ask. The sheet's footer
carries the date it was printed so the two can be told apart.

The shelf talker is the other way round on purpose. It is paper on a shelf
edge, replaced when the price changes, and it is exactly what a shop uses to
announce an offer — so it shows what the till will CHARGE today, with the
marked price struck through beside it when an offer is on, and the date large.

EVERY DIMENSION IS A STATED MILLIMETRE
======================================
The grids below are the published geometry of the sheets they name. Nothing is
inferred from a printer, and the page does not scale: the labels are placed
with absolute millimetre positions on a 210 × 297 mm page with zero margin, and
the screen-only bar at the top says to print at 100 % with margins set to
none, because a browser that "fits to page" shifts every label by a few
millimetres a row until the last row is on the gap. A layout is refused by name
if it is not in the table; there is no free-form grid, because a grid a
shopkeeper typed and got wrong wastes a sheet with nothing saying why.

Because none of that is visible on a screen, the sheet DRAWS ITS WHOLE GRID:
all 65 cells outlined, the empty ones numbered so a part-used sheet can be
started at cell 37. Those outlines are screen-only — a sticker sheet carries
nothing but its labels — except on `?grid=1`, the alignment proof, which puts
the cell edges on PLAIN paper to be checked against the sticker sheet before
any stock is spent.

FOUR RULES, IN THE ORDER THEY WOULD HURT
========================================
  1. INTEGER PAISE. Every price on paper is read from the catalogue as an int
     and rendered through `money.to_rupees_str`. No float, no division.
  2. THE BROWSER IS NEVER AN AUTHOR. The page sends sku ids, a copy count, a
     layout id and a cell to start at. It cannot name a product or a price. An
     asserted price is compared to the catalogue and REFUSED on disagreement,
     never used and never quietly ignored.
  3. NO FORGERY PRIMITIVES. The only string encoded here is `gawaah:<sku_id>`,
     a product identifier. Nothing here builds a UPI payload or a payment link,
     and a caller cannot change what the code says.
  4. A REFUSAL IS A RESULT. Every failure has a name in `reason` and a sentence
     a shopkeeper can act on in `detail`, with a 400. Nothing here raises a 500.

EVERY PRINT IS WITNESSED
========================
Each sheet and each talker appends one line to this module's OWN hash chain,
`<shop>/labels.audit.jsonl`, recording which products were printed at which
price and when. Not `results/audit.jsonl`: that chain is held open by the money
service in another process, and a second writer would break its head. The
line's hash is printed on the sheet's screen-only bar, so a sticker found on a
shelf next year can be matched to the print run that made it.

MOUNTING
========
The router carries NO prefix; these paths are already absolute::

    GET  /labels/layouts               the grids, in millimetres
    GET  /labels/products              what this shop can print a label for
    POST /labels/plan                  how many labels, how many sheets, no paper
    GET  /labels/sheet                 the print-ready page
                                       (?layout&items&skip&grid) — `grid=1` is
                                       the alignment proof, for plain paper
    GET  /labels/talker/{sku_id}       one product, price large (?size&copies)
    GET  /labels/health                where the witness chain lives

    from gawaah import labels
    app.include_router(labels.router)
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .ledger import Ledger, verify
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach. The reason
# names the state; the sentence that says what to change lives in `detail`.

R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_EMPTY_CATALOGUE = "nothing_priced_yet"
R_BAD_BODY = "labels_body_not_json"
R_NO_LAYOUT = "layout_missing"
R_UNKNOWN_LAYOUT = "layout_not_supported"
R_NO_ITEMS = "nothing_to_print"
R_BAD_ITEMS = "items_malformed"
R_UNKNOWN_SKU = "sku_not_in_this_shop"
R_BAD_COPIES = "copies_not_a_whole_number"
R_TOO_MANY_COPIES = "copies_beyond_one_run"
R_TOO_MANY_LINES = "too_many_products_in_one_run"
R_TOO_MANY_LABELS = "too_many_labels_for_one_run"
R_BAD_SKIP = "skip_not_a_cell_on_this_sheet"
R_PRICE_DISAGREES = "client_price_disagrees"
R_BAD_SIZE = "talker_size_not_supported"
R_NO_ENCODER = "qr_encoder_unavailable"
R_QR_FAILED = "code_would_not_encode"
R_BAD_GRID = "grid_flag_not_a_yes_or_no"
R_INTERNAL = "labels_internal_error"

#: Caps. A run is a stack of sheets a shop prints at a counter, not a print
#: shop's job. What it costs when these are wrong: a shop with more than 200
#: loose products prints twice. What it costs the other way: a request that
#: asks for a hundred thousand labels builds a hundred thousand SVGs in memory
#: on the machine that is also running the till.
MAX_COPIES = 500
MAX_LINES = 200
MAX_LABELS = 1300          # twenty sheets of the densest grid
MAX_TALKER_COPIES = 40

PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

#: The same charset `gawaah/shop_store.py` enforces on a sku id, restated here
#: because an id arrives in a query string and becomes an SVG `id` attribute
#: and a filename. Anything outside it is refused before it is looked up.
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Where the code text comes from. Read from the till when it is loaded so a
#: sheet printed here decodes at the till that mounts it; this is the fallback
#: for a module imported on its own.
QR_PREFIX_DEFAULT = "gawaah:"

#: The QR spec's quiet zone is four modules of white on every side. Kept
#: INSIDE the SVG so that a label with a dark border, or a talker with a
#: hairline cut mark, cannot eat it.
QUIET_MODULES = 4

#: The largest code square a label gets. A 45 mm code is 1.4 mm a module,
#: which is more than any counter camera needs; past it the millimetres are
#: better spent on the price.
QR_CAP_MM = 45.0

#: How wide one character of a bold sans figure is, in ems, for fitting a
#: price into its column. Digits in the system faces this page falls back to
#: run 0.55-0.60 em; the rupee sign about 0.6. Stated here because it is the
#: one number in the fit that is an assumption about a font rather than a
#: millimetre of the sheet.
FIGURE_EM_PER_CHAR = 0.58
PT_TO_MM = 0.3528

#: THE SHEET IS DRAWN WHOLE, AND ONLY ON SCREEN.
#:
#: Three labels on a 65-up sheet used to render as three boxes in the top-left
#: corner of an otherwise blank white rectangle, with nothing to say the other
#: 62 cells were anywhere in particular. The geometry was right — the cells
#: measure 4.65 / 45.29 / 85.93 mm from the left edge on a printed proof — but
#: a shopkeeper cannot measure a screen, and a blank rectangle is not evidence.
#: So every cell of the grid is now outlined, filled or not.
#:
#: The outlines are SCREEN ONLY. A line printed on Avery stock is a grey box on
#: every sticker, and a sticker sheet must carry nothing but the labels. The
#: one exception is `?grid=1`, the ALIGNMENT PROOF: the same page with the cell
#: edges printing, meant for a sheet of PLAIN paper held up against the sticker
#: sheet to check the printer before any stock is spent. Which of the two was
#: printed is recorded on the labels chain, because a proof spends no stickers.
#:
#: Neutral greys on purpose: green, amber and red are the counter's settled,
#: abstained and refused, and a sheet of stationery wears none of them.
GRID_INK = "#dde3ea"        # the cell edge
GRID_NUM = "#9aa5b1"        # the cell's number, for counting to a start cell
GRID_USED = "#eef1f5"       # a cell a part-used sheet has already lost
GRID_PROOF_INK = "#555"     # the same edge when it is meant to reach paper


class LabelsRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: LabelsRefused, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=status)


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400)


# ---------------------------------------------------------------- layouts --


@dataclass(frozen=True)
class Layout:
    """One sticker sheet, as the stationer cuts it.

    Positions are the sheet's own: `left_mm`/`top_mm` are the margins to the
    first label, `pitch_*_mm` the distance from one label's edge to the next
    label's same edge (label plus gap). Everything else is derived, and the
    derivations are checked against the page in `tests/test_labels.py`.
    """

    layout_id: str
    name: str
    label_w_mm: float
    label_h_mm: float
    cols: int
    rows: int
    left_mm: float
    top_mm: float
    pitch_x_mm: float
    pitch_y_mm: float
    #: The sheets this grid matches. A name a shopkeeper can read off a packet.
    compatible: str
    #: Plain paper: draw the cell edges so the sheet can be cut with scissors.
    cut_lines: bool
    #: Type sizes, in points, chosen per grid so a name fits on two lines and a
    #: price fills the height. Stated rather than derived so a sheet printed
    #: today and one printed next month set the same type.
    name_pt: float
    figure_pt: float
    code_pt: float
    pad_mm: float

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    @property
    def gap_x_mm(self) -> float:
        return self.pitch_x_mm - self.label_w_mm

    @property
    def gap_y_mm(self) -> float:
        return self.pitch_y_mm - self.label_h_mm

    @property
    def right_mm(self) -> float:
        return PAGE_W_MM - (self.left_mm + (self.cols - 1) * self.pitch_x_mm
                            + self.label_w_mm)

    @property
    def bottom_mm(self) -> float:
        return PAGE_H_MM - (self.top_mm + (self.rows - 1) * self.pitch_y_mm
                            + self.label_h_mm)

    @property
    def qr_mm(self) -> float:
        """The side of the code square: the label's height less the padding,
        capped at `QR_CAP_MM`. On the 8-up grid an uncapped square left the
        price 27 mm to live in and it printed as "₹120." — measured, not
        guessed, on the first proof sheet."""
        return min(self.label_h_mm - 2 * self.pad_mm, QR_CAP_MM)

    @property
    def text_mm(self) -> float:
        """What is left for the name and the price beside the code."""
        return self.label_w_mm - 2 * self.pad_mm - self.qr_mm - self.pad_mm

    def cell_xy(self, cell: int) -> tuple[float, float]:
        r, c = divmod(cell, self.cols)
        return (self.left_mm + c * self.pitch_x_mm,
                self.top_mm + r * self.pitch_y_mm)

    def to_json(self) -> dict[str, Any]:
        return {
            "layout_id": self.layout_id,
            "name": self.name,
            "label_w_mm": self.label_w_mm,
            "label_h_mm": self.label_h_mm,
            "cols": self.cols,
            "rows": self.rows,
            "per_page": self.per_page,
            "left_mm": self.left_mm,
            "top_mm": self.top_mm,
            "right_mm": round(self.right_mm, 2),
            "bottom_mm": round(self.bottom_mm, 2),
            "pitch_x_mm": self.pitch_x_mm,
            "pitch_y_mm": self.pitch_y_mm,
            "gap_x_mm": round(self.gap_x_mm, 2),
            "gap_y_mm": round(self.gap_y_mm, 2),
            "qr_mm": round(self.qr_mm, 2),
            "text_mm": round(self.text_mm, 2),
            "compatible": self.compatible,
            "cut_lines": self.cut_lines,
            "page": "A4 portrait, 210 x 297 mm, zero margin",
        }


#: The grids, densest first. The first six are the geometry printed on the
#: back of the packet for the Avery-numbered sheets and the unbranded A4
#: sheets sold as the same count; the last is plain paper for a shop with no
#: sticker sheets at all, cut with scissors and stuck with tape.
LAYOUTS: tuple[Layout, ...] = (
    Layout("a4_65", "65 per sheet", 38.1, 21.2, 5, 13, 4.65, 10.7, 40.64, 21.2,
           "Avery L7651 / J8651 and unbranded 65-up A4 sheets",
           False, 6.5, 11.0, 4.6, 1.4),
    Layout("a4_40", "40 per sheet", 45.7, 25.4, 4, 10, 9.75, 21.5, 48.25, 25.4,
           "Avery L7654 / J8654 and unbranded 40-up A4 sheets",
           False, 7.5, 13.0, 5.0, 1.6),
    Layout("a4_24", "24 per sheet", 63.5, 33.9, 3, 8, 7.2, 12.9, 66.0, 33.9,
           "Avery L7159 / J8159 and unbranded 24-up A4 sheets",
           False, 9.0, 17.0, 5.5, 2.2),
    Layout("a4_21", "21 per sheet", 63.5, 38.1, 3, 7, 7.2, 15.1, 66.0, 38.1,
           "Avery L7160 / J8160 and unbranded 21-up A4 sheets",
           False, 10.0, 19.0, 6.0, 2.4),
    Layout("a4_14", "14 per sheet", 99.1, 38.1, 2, 7, 4.65, 15.1, 101.6, 38.1,
           "Avery L7163 / J8163 and unbranded 14-up A4 sheets",
           False, 11.0, 21.0, 6.5, 2.6),
    Layout("a4_8", "8 per sheet", 99.1, 67.7, 2, 4, 4.65, 13.1, 101.6, 67.7,
           "Avery L7165 / J8165 and unbranded 8-up A4 sheets",
           False, 14.0, 32.0, 8.0, 4.0),
    Layout("a4_cut_40", "40 on plain paper", 50.0, 27.0, 4, 10, 5.0, 13.5, 50.0,
           27.0, "plain A4 paper, cut along the printed lines",
           True, 8.0, 14.0, 5.2, 1.8),
)

LAYOUT_BY_ID: dict[str, Layout] = {lay.layout_id: lay for lay in LAYOUTS}


@dataclass(frozen=True)
class TalkerSize:
    """A shelf talker and the A4 sheet it is cut from."""

    size_id: str
    name: str
    w_mm: float
    h_mm: float
    page_w_mm: float
    page_h_mm: float
    cols: int
    rows: int
    name_pt: float
    figure_pt: float
    was_pt: float
    code_mm: float
    pad_mm: float

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    @property
    def landscape(self) -> bool:
        return self.page_w_mm > self.page_h_mm

    def to_json(self) -> dict[str, Any]:
        return {
            "size_id": self.size_id, "name": self.name,
            "w_mm": self.w_mm, "h_mm": self.h_mm,
            "page": (f"A4 {'landscape' if self.landscape else 'portrait'}, "
                     f"{self.page_w_mm:g} x {self.page_h_mm:g} mm"),
            "per_page": self.per_page, "cols": self.cols, "rows": self.rows,
            "code_mm": self.code_mm,
        }


#: Every talker is cut from an A4 sheet so the shop's one printer prints it.
#: A6 fits four to a landscape sheet, A5 two to a portrait sheet, A4 is the
#: sheet. The cut lines are drawn.
TALKER_SIZES: tuple[TalkerSize, ...] = (
    TalkerSize("a6", "A6 — four to a sheet", 148.0, 105.0, 297.0, 210.0, 2, 2,
               20.0, 62.0, 16.0, 26.0, 8.0),
    TalkerSize("a5", "A5 — two to a sheet", 210.0, 148.0, 210.0, 297.0, 1, 2,
               28.0, 92.0, 22.0, 36.0, 10.0),
    TalkerSize("a4", "A4 — the whole sheet", 297.0, 210.0, 297.0, 210.0, 1, 1,
               40.0, 140.0, 32.0, 50.0, 14.0),
)

TALKER_BY_ID: dict[str, TalkerSize] = {t.size_id: t for t in TALKER_SIZES}


# ------------------------------------------------------------- the till --
#
# This module reads the shopkeeper's catalogue rather than keeping one, so it
# needs the till module. Imported LATE, inside functions: the till mounts this
# router, so a module-scope import would be a cycle, and the till is expensive.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
    """The already-loaded till module, or a named refusal.

    sys.modules FIRST, under both names the till is registered as, and no
    fresh import while either is present. A second copy of the till has its
    own `_DEPS` cache and its own idea of where the shop is, and a test that
    redirected one copy would leave this module printing another shop's
    prices. See the same note in `gawaah/storefront.py`.
    """
    import sys

    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from tools import upload_app  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001 - a missing till is a named answer
        raise LabelsRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). Labels are printed from the till's own catalogue and "
            f"this module keeps no second copy of the prices.") from None
    return upload_app


def shop_dir() -> Path:
    """Where the catalogue lives — the till's own answer, honouring GAWAAH_SHOP_DIR."""
    return Path(_till().store_dir())


def audit_path() -> Path:
    """This module's own hash chain. Never `results/audit.jsonl` — see the header."""
    return shop_dir() / "labels.audit.jsonl"


def qr_prefix() -> str:
    try:
        return str(getattr(_till(), "QR_PREFIX", QR_PREFIX_DEFAULT))
    except LabelsRefused:
        return QR_PREFIX_DEFAULT


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _printed_on(now: _dt.datetime) -> str:
    """The date as it goes on paper: the machine's own local clock, zone named."""
    local = now.astimezone()
    return local.strftime("%d %b %Y, %H:%M %Z")


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one line to the labels chain. Returns the head, or None on failure.

    Best effort, never silent: a caller that gets None prints "not witnessed"
    on the sheet rather than a hash that does not exist.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="labels", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a print
        return None


# ------------------------------------------------------------- catalogue --


def catalogue() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, marked_paise, off_paise?, how}.

    `offer_priced_skus()`, the offer-aware map, so BOTH numbers are here: the
    marked price the sticker prints and the charged price the talker prints.
    """
    up = _till()
    try:
        return dict(up.offer_priced_skus())
    except LabelsRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store may be unreadable
        reason = getattr(exc, "reason", None) or R_NO_CATALOGUE
        detail = getattr(exc, "detail", None) or (
            f"the catalogue could not be read ({type(exc).__name__}: {exc})")
        raise LabelsRefused(str(reason), str(detail)) from None


def _marked_paise(rec: dict[str, Any]) -> int:
    """The shelf-edge price, as an int, or a MoneyError.

    `marked_paise` is present whenever the offer layer ran; `price_paise` is
    the same number when it did not. Both are validated through `paise()`,
    which refuses a float and a bool.
    """
    marked = rec.get("marked_paise")
    if isinstance(marked, bool) or not isinstance(marked, int):
        marked = rec["price_paise"]
    # `paise()` BEFORE `int()`, never after: int(21.45) is 21 and the float
    # would be gone before anything could refuse it.
    return int(paise(marked))


def _charged_paise(rec: dict[str, Any]) -> int:
    return int(paise(rec["price_paise"]))


def _offer_on(rec: dict[str, Any]) -> bool:
    off = rec.get("off_paise")
    return (not isinstance(off, bool) and isinstance(off, int) and off > 0
            and _charged_paise(rec) < _marked_paise(rec))


def _product_row(sku_id: str, rec: dict[str, Any]) -> dict[str, Any]:
    marked = _marked_paise(rec)
    charged = _charged_paise(rec)
    how = str(rec.get("how") or "unknown")
    return {
        "sku_id": sku_id,
        "name": str(rec.get("name") or sku_id),
        "price_paise": marked,
        "price_rupees": to_rupees_str(paise(marked)),
        "charged_paise": charged,
        "charged_rupees": to_rupees_str(paise(charged)),
        "offer_today": _offer_on(rec),
        "taught_with": how,
        # A product taught from a printed code already carries one. A sticker
        # on it is harmless — `gawaah:` resolves the same way — but it is not
        # the reason this screen exists, so the page can leave it unticked.
        "has_printed_code": how == "product_code_only",
        "qr_text": f"{qr_prefix()}{sku_id}",
        "qr_png_url": f"/qr/{sku_id}",
    }


# ------------------------------------------------------------- the code --


def _qr_matrix(text: str) -> list[list[bool]]:
    """The code as rows of dark/light, quiet zone stripped.

    `cv2.QRCodeEncoder` is the same encoder `GET /qr/{sku_id}` uses, so the
    sheet and the single sticker carry byte-identical symbols. It returns the
    modules with a two-module white border baked in; that border is trimmed
    here and the spec's four-module quiet zone is put back in the SVG, so the
    zone is the same whatever the label's padding.
    """
    try:
        import cv2
        import numpy as np
    except Exception as exc:  # noqa: BLE001 - a missing encoder is a named answer
        raise LabelsRefused(
            R_NO_ENCODER,
            f"the QR encoder (OpenCV) is not importable ({type(exc).__name__}). "
            f"Nothing was printed.") from None
    try:
        q = cv2.QRCodeEncoder.create().encode(text)
        arr = np.asarray(q)
    except Exception as exc:  # noqa: BLE001
        raise LabelsRefused(
            R_QR_FAILED,
            f"{text!r} would not encode as a QR ({type(exc).__name__}: {exc}).") \
            from None
    if arr.ndim != 2 or arr.size == 0:
        raise LabelsRefused(R_QR_FAILED, f"{text!r} produced no symbol.")
    dark = arr < 128
    rows = np.flatnonzero(dark.any(axis=1))
    cols = np.flatnonzero(dark.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        raise LabelsRefused(R_QR_FAILED, f"{text!r} produced a blank symbol.")
    core = dark[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    if core.shape[0] != core.shape[1]:
        raise LabelsRefused(
            R_QR_FAILED,
            f"{text!r} produced a {core.shape[0]}x{core.shape[1]} symbol, "
            f"which is not square.")
    return [[bool(v) for v in row] for row in core.tolist()]


def _qr_symbol(sym_id: str, matrix: list[list[bool]]) -> str:
    """One `<symbol>` holding the code, referenced once per label with `<use>`.

    Dark modules become one path of unit squares, run-length merged along each
    row so a 29-module code is a few hundred bytes rather than a few thousand.
    `shape-rendering="crispEdges"` keeps the printer from anti-aliasing module
    edges into grey, which is what makes a small code fail to decode.
    """
    n = len(matrix)
    side = n + 2 * QUIET_MODULES
    parts: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < n:
            if not row[x]:
                x += 1
                continue
            x0 = x
            while x < n and row[x]:
                x += 1
            parts.append(f"M{x0 + QUIET_MODULES} {y + QUIET_MODULES}h{x - x0}v1h-{x - x0}z")
    return (f'<symbol id="{_html.escape(sym_id)}" viewBox="0 0 {side} {side}">'
            f'<rect width="{side}" height="{side}" fill="#fff"/>'
            f'<path fill="#000" shape-rendering="crispEdges" d="{"".join(parts)}"/>'
            f'</symbol>')


def _sym_id(sku_id: str) -> str:
    return f"q-{sku_id}"


# ------------------------------------------------------------- the lines --


def _layout(layout_id: Any) -> Layout:
    if not isinstance(layout_id, str) or not layout_id.strip():
        raise LabelsRefused(
            R_NO_LAYOUT,
            f"no layout was named. This counter prints: "
            f"{', '.join(lay.layout_id for lay in LAYOUTS)}.")
    lay = LAYOUT_BY_ID.get(layout_id.strip())
    if lay is None:
        raise LabelsRefused(
            R_UNKNOWN_LAYOUT,
            f"{layout_id!r} is not a sheet this counter prints. It prints: "
            f"{', '.join(lay.layout_id for lay in LAYOUTS)}. A grid it does "
            f"not know would put every label on the gap.")
    return lay


def _talker_size(size_id: Any) -> TalkerSize:
    s = (size_id if isinstance(size_id, str) else "").strip() or "a6"
    t = TALKER_BY_ID.get(s)
    if t is None:
        raise LabelsRefused(
            R_BAD_SIZE,
            f"{size_id!r} is not a talker size. This counter prints: "
            f"{', '.join(t.size_id for t in TALKER_SIZES)}.")
    return t


def _whole(value: Any, *, what: str, reason: str) -> int:
    """A count from JSON or a query string. bool is refused before int."""
    if isinstance(value, bool):
        raise LabelsRefused(reason, f"{what} is {value!r}, which is not a count.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise LabelsRefused(
        reason, f"{what} is {value!r}. A count is a whole number.")


def _copies(value: Any, sku_id: str) -> int:
    n = _whole(value, what=f"the copy count for {sku_id!r}", reason=R_BAD_COPIES)
    if n <= 0:
        raise LabelsRefused(
            R_BAD_COPIES,
            f"the copy count for {sku_id!r} is {n}. To leave a product off the "
            f"sheet, leave it out of the list.")
    if n > MAX_COPIES:
        raise LabelsRefused(
            R_TOO_MANY_COPIES,
            f"{n} copies of {sku_id!r} is past the {MAX_COPIES} this counter "
            f"prints in one run. Print it twice.")
    return n


def _skip(value: Any, lay: Layout) -> int:
    n = _whole(value if value not in (None, "") else 0,
               what="the cell to start at", reason=R_BAD_SKIP)
    if n < 0 or n >= lay.per_page:
        raise LabelsRefused(
            R_BAD_SKIP,
            f"start-at cell {n} is not on a {lay.name} sheet, which has cells "
            f"0 to {lay.per_page - 1}. A used sheet's first empty cell is what "
            f"goes here.")
    return n


#: What counts as yes and no in `?grid=`. Stated rather than truth-tested,
#: because `bool("0")` is True and a shopkeeper who typed `grid=0` meant no.
_YES = frozenset({"1", "yes", "true", "on"})
_NO = frozenset({"", "0", "no", "false", "off"})


def _flag(value: Any, *, what: str) -> bool:
    """A yes/no from a query string, or a named refusal.

    Anything unrecognised is REFUSED rather than read as no. A `grid=ture`
    silently treated as no prints the sheet the shopkeeper did not ask for on
    the paper they were trying not to waste, and says nothing about why.
    """
    if isinstance(value, bool):
        return value
    s = ("" if value is None else str(value)).strip().lower()
    if s in _YES:
        return True
    if s in _NO:
        return False
    raise LabelsRefused(
        R_BAD_GRID,
        f"{what} is {value!r}. It is a yes or a no: "
        f"{', '.join(sorted(_YES))} or {', '.join(sorted(x for x in _NO if x))}.")


def _parse_items_query(items: str) -> list[dict[str, Any]]:
    """`items=parle_g:3,pickle_jar,atta_1kg:12` -> [{sku_id, copies}].

    Colon and comma are outside the sku charset, so the split is unambiguous
    and a malformed token is refused by name rather than guessed at.
    """
    s = (items or "").strip()
    if not s:
        raise LabelsRefused(
            R_NO_ITEMS,
            "no products were named, so there is nothing to print. Use "
            "items=<sku_id>:<copies>,<sku_id>:<copies>.")
    out: list[dict[str, Any]] = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        sku, _, n = tok.partition(":")
        entry: dict[str, Any] = {"sku_id": sku.strip()}
        if n.strip():
            entry["copies"] = n.strip()
        out.append(entry)
    if not out:
        raise LabelsRefused(R_NO_ITEMS, "no products were named.")
    return out


def _resolve(items: Any, known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The run, re-priced from the catalogue. The caller named products and counts.

    Repeats of one sku are merged and the merged count is echoed back, so
    nothing is combined out of sight. An asserted `price_paise` is CHECKED
    against the marked price and refused on disagreement — never used.
    """
    if items is None or (isinstance(items, list) and not items):
        raise LabelsRefused(
            R_NO_ITEMS, "no products were chosen, so there is nothing to print.")
    if not isinstance(items, list):
        raise LabelsRefused(
            R_BAD_ITEMS,
            f"'items' must be a list of {{sku_id, copies}}, not "
            f"{type(items).__name__}.")
    if not known:
        raise LabelsRefused(
            R_EMPTY_CATALOGUE,
            "nothing in this shop has a price yet, so there is nothing to put "
            "on a label. Teach a product first.")

    merged: dict[str, int] = {}
    order: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise LabelsRefused(
                R_BAD_ITEMS,
                f"every line must be an object with a sku_id and a copy count; "
                f"found {type(raw).__name__}.")
        sku_id = raw.get("sku_id")
        if not isinstance(sku_id, str) or not sku_id.strip():
            raise LabelsRefused(R_BAD_ITEMS, "a line arrived with no sku_id.")
        sku_id = sku_id.strip()
        if not SKU_RE.match(sku_id):
            raise LabelsRefused(
                R_BAD_ITEMS,
                f"{sku_id!r} is not the shape of a sku id in this shop "
                f"({SKU_RE.pattern}).")
        rec = known.get(sku_id)
        if rec is None:
            raise LabelsRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not something this shop has priced, so it has "
                f"no label. Nothing was printed. Priced: "
                f"{', '.join(sorted(known)[:6])}"
                f"{'…' if len(known) > 6 else ''}.")
        copies = _copies(raw.get("copies", 1), sku_id)

        claimed = raw.get("price_paise")
        if claimed is not None:
            if isinstance(claimed, bool) or not isinstance(claimed, int) \
                    or claimed != _marked_paise(rec):
                raise LabelsRefused(
                    R_PRICE_DISAGREES,
                    f"this request says {sku_id!r} is {claimed!r} paise; the "
                    f"shop's catalogue says {_marked_paise(rec)}. The sticker "
                    f"prints the catalogue's number, and nothing was printed "
                    f"because the two disagree.")

        if sku_id not in merged:
            order.append(sku_id)
        merged[sku_id] = merged.get(sku_id, 0) + copies

    if len(order) > MAX_LINES:
        raise LabelsRefused(
            R_TOO_MANY_LINES,
            f"this run names {len(order)} different products and the cap is "
            f"{MAX_LINES}. Print it in two runs.")

    lines: list[dict[str, Any]] = []
    total_labels = 0
    for sku_id in order:
        copies = merged[sku_id]
        if copies > MAX_COPIES:
            raise LabelsRefused(
                R_TOO_MANY_COPIES,
                f"the repeated lines for {sku_id!r} add up to {copies}, past "
                f"the {MAX_COPIES} this counter prints in one run.")
        rec = known[sku_id]
        unit_paise = _marked_paise(rec)
        lines.append({
            "sku_id": sku_id,
            "name": str(rec.get("name") or sku_id),
            "copies": copies,
            "price_paise": unit_paise,
            "price_rupees": to_rupees_str(paise(unit_paise)),
            "offer_today": _offer_on(rec),
            "charged_today_paise": _charged_paise(rec),
            "qr_text": f"{qr_prefix()}{sku_id}",
        })
        total_labels += copies
    if total_labels > MAX_LABELS:
        raise LabelsRefused(
            R_TOO_MANY_LABELS,
            f"this run is {total_labels} labels and the cap is {MAX_LABELS} — "
            f"twenty sheets of the densest grid. Print it in two runs.")
    return lines


def _count(lines: list[dict[str, Any]], lay: Layout, skip: int) -> dict[str, int]:
    """Labels, sheets and blank cells. Integer arithmetic; nothing estimated."""
    labels = 0
    for ln in lines:
        labels += int(ln["copies"])
    cells = skip + labels
    pages = -(-cells // lay.per_page) if cells else 0
    blank = pages * lay.per_page - cells
    return {"labels": labels, "pages": pages, "skipped": skip,
            "blank_on_last_page": blank, "cells_per_page": lay.per_page}


def _sheet_url(lay: Layout, lines: list[dict[str, Any]], skip: int) -> str:
    items = ",".join(f"{ln['sku_id']}:{ln['copies']}" for ln in lines)
    return f"/labels/sheet?layout={lay.layout_id}&items={items}&skip={skip}"


def _plan(lay: Layout, lines: list[dict[str, Any]], skip: int) -> dict[str, Any]:
    matrices = {ln["sku_id"]: _qr_matrix(ln["qr_text"]) for ln in lines}
    out_lines = []
    for ln in lines:
        n = len(matrices[ln["sku_id"]])
        out_lines.append({
            **ln,
            "qr_modules": n,
            # How big one module prints on this grid, quiet zone included.
            "module_mm": round(lay.qr_mm / (n + 2 * QUIET_MODULES), 3),
            "figure_pt": _fit_pt(f"₹{ln['price_rupees']}", lay.figure_pt,
                                 lay.text_mm),
        })
    counts = _count(lines, lay, skip)
    return {
        "ok": True,
        "settles_money": False,
        "layout": lay.to_json(),
        "lines": out_lines,
        **counts,
        "price_on_label": "marked",
        "offers_today": [ln["sku_id"] for ln in lines if ln["offer_today"]],
        "sheet_url": _sheet_url(lay, lines, skip),
        "note": ("The sticker prints the marked price, not today's offer, "
                 "because a sticker outlives an offer. The till prices every "
                 "scan from the catalogue, so the sticker can never set the "
                 "bill."),
    }


# ------------------------------------------------------------- rendering --


def _fit_pt(text: str, max_pt: float, width_mm: float) -> float:
    """The largest type size, up to `max_pt`, at which `text` fits `width_mm`.

    Deterministic and stated: `len(text) * FIGURE_EM_PER_CHAR * pt * PT_TO_MM`
    must not exceed the column. Rounded down to a half point, never below 5,
    which is the smallest a price is worth printing at all.
    """
    n = max(1, len(text))
    fit = width_mm / (n * FIGURE_EM_PER_CHAR * PT_TO_MM)
    pt = min(max_pt, fit)
    pt = int(pt * 2) / 2
    return max(5.0, pt)


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _mm(v: float) -> str:
    return f"{v:.2f}mm"


def _screen_bar(title: str, facts: list[str], witness: Optional[str],
                extra: str = "") -> str:
    """The instructions above the sheet. Hidden on paper.

    `extra` is trusted HTML the caller has already escaped — one link, in
    practice. It is a parameter rather than a third paragraph baked in here
    because the talker has no alignment proof to offer and should not carry a
    line about one.
    """
    li = "".join(f"<li>{_html.escape(f, quote=False)}</li>" for f in facts)
    w = (f"witness {witness[:12]}" if witness
         else "not witnessed — the labels chain could not be written")
    return (
        '<div class="bar">'
        f'<h1>{_html.escape(title, quote=False)}</h1>'
        f'<ul>{li}</ul>'
        '<p><b>Print at 100 %, on A4, with margins set to none.</b> '
        'Press Ctrl+P (⌘P on a Mac). If the print dialog offers '
        '"fit to page", turn it off: the labels are placed in millimetres and '
        'scaling moves every row onto the gap.</p>'
        f'{extra}'
        f'<p class="w">{_html.escape(w, quote=False)}</p>'
        '</div>')


_PRINT_RESET = (
    "html,body{margin:0;padding:0;background:#fff;color:#000;"
    "font-family:system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',"
    "Arial,sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}"
    "*{box-sizing:border-box}"
    ".bar{max-width:760px;margin:0 auto;padding:18px 20px 8px;color:#222;"
    "font-size:14px;line-height:1.5}"
    ".bar h1{font-size:18px;margin:0 0 6px}.bar ul{margin:0 0 8px;padding-left:18px}"
    ".bar p{margin:0 0 6px}.bar .w{font-family:ui-monospace,Menlo,monospace;"
    "font-size:12px;color:#555}"
    ".bar a{color:#2B84EA}"
    ".page{position:relative;overflow:hidden;background:#fff;margin:12px auto;"
    "box-shadow:0 2px 12px rgba(0,0,0,.18);page-break-after:always;"
    "break-after:page}"
    ".page:last-child{page-break-after:auto;break-after:auto}"
    "@media print{.bar{display:none}.page{margin:0;box-shadow:none}}"
)


def _grid_layer(lay: Layout, page_index: int, skip: int,
                filled: set[int]) -> str:
    """Every cell of one sheet, outlined, so the grid is visible before it prints.

    One div per cell, at the same absolute millimetres the labels use — the
    same `cell_xy` call, not a second copy of the arithmetic, so an outline
    that did not sit exactly under its label would be a bug in one place
    rather than a disagreement between two.

    The cells that already have a label are outlined too. That is the point:
    the complaint this answers was that three labels in a corner give no
    evidence they are on the sheet's grid at all.
    """
    out: list[str] = []
    for cell in range(lay.per_page):
        x, y = lay.cell_xy(cell)
        # `skip` only ever eats cells at the start of the FIRST sheet; every
        # later sheet begins at its own cell 0.
        used = page_index == 0 and cell < skip
        klass = "cell used" if used else "cell"
        # A number only where it can be read — a filled cell already carries a
        # name, a price and a code, and a fourth thing in the corner of a
        # 38 x 21 mm box is noise. The empty ones are the ones being counted.
        num = "" if cell in filled else f"<span>{cell}</span>"
        out.append(f'<div class="{klass}" style="left:{_mm(x)};top:{_mm(y)}">'
                   f'{num}</div>')
    return "".join(out)


def _render_sheet(lay: Layout, lines: list[dict[str, Any]], skip: int,
                  now: _dt.datetime, witness: Optional[str],
                  proof: bool = False) -> str:
    """The print-ready page. One `<symbol>` per product, one `<use>` per label.

    Every label is placed at an absolute millimetre position on a zero-margin
    A4 page. Nothing scales and nothing flows: a name that is too long is cut
    at two lines, and a price is never shrunk, because a price that is hard to
    read is worse than a name that is.

    Under it sits the whole grid, every cell outlined — on screen, so the
    shopkeeper sees the sheet and can count what is left; not on paper, where
    a sticker must carry nothing but its own label. `proof=True` is the one
    page where the outlines print, and it is meant for plain paper.
    """
    matrices = {ln["sku_id"]: _qr_matrix(ln["qr_text"]) for ln in lines}
    counts = _count(lines, lay, skip)
    esc = _html.escape

    defs = "".join(_qr_symbol(_sym_id(s), m) for s, m in matrices.items())
    cut = ("outline:0.15mm dashed #888;" if lay.cut_lines else "")
    # The grid layer. `outline` rather than `border`, so a 0.2 mm line cannot
    # move a cell by 0.2 mm; `outline-offset` pulls it inside the cell so two
    # neighbours on a zero-gap grid (a4_65 has gap_y 0) do not draw a 0.4 mm
    # double line down the row.
    grid_css = (
        f".cell{{position:absolute;width:{_mm(lay.label_w_mm)};"
        f"height:{_mm(lay.label_h_mm)};border-radius:0.8mm;"
        f"outline:0.2mm solid {GRID_INK};outline-offset:-0.1mm}}"
        + f".cell.used{{background:{GRID_USED}}}"
        + f".cell span{{position:absolute;right:0.8mm;bottom:0.5mm;"
          f"font-size:4.2pt;line-height:1;color:{GRID_NUM};"
          f"font-variant-numeric:tabular-nums}}"
        # Screen-only by default: see GRID_INK's note. The proof keeps the
        # edges and drops the numbers, which are for counting, not aligning.
        #
        # `.cell.used` IS NAMED IN BOTH PRINT RULES, and that is not tidiness:
        # `.cell.used` is two classes and outranks a bare `.cell`, so a print
        # rule that only said `.cell{background:none}` left the grey fill on
        # every skipped cell. Measured on a PDF of a skip=37 run — thirty-seven
        # grey boxes printed onto thirty-seven stickers. Neither page fills a
        # cell on paper now; the proof is edges only, which is all alignment
        # needs and a great deal less toner.
        + (f"@media print{{.cell{{outline-color:{GRID_PROOF_INK}}}"
           f".cell.used{{background:none}}.cell span{{display:none}}}}"
           if proof else
           "@media print{.cell,.cell.used{outline:none;background:none}"
           ".cell span{display:none}}")
    )
    style = (
        f"@page{{size:{_mm(PAGE_W_MM)} {_mm(PAGE_H_MM)};margin:0}}"
        + _PRINT_RESET
        + grid_css
        + f".page{{width:{_mm(PAGE_W_MM)};height:{_mm(PAGE_H_MM)}}}"
        + f".lab{{position:absolute;width:{_mm(lay.label_w_mm)};"
          f"height:{_mm(lay.label_h_mm)};padding:{_mm(lay.pad_mm)};"
          f"display:flex;align-items:center;gap:{_mm(lay.pad_mm)};"
          f"overflow:hidden;{cut}}}"
        + f".lab svg{{width:{_mm(lay.qr_mm)};height:{_mm(lay.qr_mm)};flex:none;"
          f"display:block}}"
        + ".t{min-width:0;flex:1;display:flex;flex-direction:column;"
          "justify-content:center;gap:0.4mm}"
        + f".n{{font-size:{lay.name_pt:g}pt;font-weight:600;line-height:1.15;"
          f"overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;"
          f"-webkit-box-orient:vertical;overflow-wrap:anywhere}}"
        + f".p{{font-size:{lay.figure_pt:g}pt;font-weight:800;line-height:1.05;"
          f"letter-spacing:-0.01em;font-variant-numeric:tabular-nums;"
          f"white-space:nowrap}}"
        + f".k{{font-size:{lay.code_pt:g}pt;color:#555;"
          f"font-family:ui-monospace,Menlo,Consolas,monospace;white-space:nowrap;"
          f"overflow:hidden;text-overflow:ellipsis}}"
    )

    per = lay.per_page
    pages: list[list[str]] = [[] for _ in range(counts["pages"])]
    #: Which cells of each sheet a label lands on, so the grid underneath knows
    #: which ones are still free to number.
    filled: list[set[int]] = [set() for _ in range(counts["pages"])]
    idx = skip
    for ln in lines:
        sym = _sym_id(ln["sku_id"])
        figure = f"₹{ln['price_rupees']}"
        fig_pt = _fit_pt(figure, lay.figure_pt, lay.text_mm)
        cell_html = (
            f'<svg aria-hidden="true"><use href="#{esc(sym)}"/></svg>'
            f'<div class="t"><div class="n">{esc(_clip(ln["name"], 64))}</div>'
            f'<div class="p" style="font-size:{fig_pt:g}pt">{esc(figure)}</div>'
            f'<div class="k">{esc(ln["sku_id"])}</div></div>')
        for _ in range(int(ln["copies"])):
            p, cell = divmod(idx, per)
            x, y = lay.cell_xy(cell)
            pages[p].append(
                f'<div class="lab" style="left:{_mm(x)};top:{_mm(y)}">'
                f'{cell_html}</div>')
            filled[p].add(cell)
            idx += 1

    # The grid goes in FIRST on each page so the labels paint over it, and it
    # is built per page: `skip` greys out cells only on the first sheet.
    body = "".join(
        f'<div class="page">{_grid_layer(lay, i, skip, filled[i])}'
        f'{"".join(cells)}</div>'
        for i, cells in enumerate(pages))
    title = f"Labels — {lay.name}" + (" — alignment proof" if proof else "")
    facts = [
        f"{counts['labels']} label{'s' if counts['labels'] != 1 else ''} on "
        f"{counts['pages']} sheet{'s' if counts['pages'] != 1 else ''} of A4"
        + (f", starting at cell {skip}" if skip else "")
        + (f", {counts['blank_on_last_page']} cell"
           f"{'s' if counts['blank_on_last_page'] != 1 else ''} left blank on "
           f"the last sheet" if counts["blank_on_last_page"] else ""),
        f"{lay.label_w_mm:g} × {lay.label_h_mm:g} mm, {lay.cols} across × "
        f"{lay.rows} down — {lay.compatible}",
    ]
    if proof:
        facts.append(
            f"ALIGNMENT PROOF: the {lay.per_page} cell edges WILL print. Put "
            f"PLAIN paper in the printer, not your sticker sheet, then hold "
            f"the two up to the light together — every box should sit on a "
            f"sticker. If they drift, the printer is scaling and \"fit to "
            f"page\" is still on.")
    else:
        facts.append(
            f"All {lay.per_page} cells are outlined on this screen so you can "
            f"see the sheet and count what is left. They do not print: on "
            f"paper this is the labels and nothing else.")
    facts += [
        "The price printed is the marked price. Today's offers are not on a "
        "sticker; the till applies them at the sale.",
        f"Printed {_printed_on(now)}.",
    ]
    # A relative link to this same run, rendered the other way. Built from the
    # server's own `_sheet_url`, so it names the products it already priced;
    # sku ids cannot carry a character that needs escaping here (SKU_RE), and
    # it is escaped anyway.
    here = _sheet_url(lay, lines, skip)
    extra = (
        f'<p><a href="{esc(here, quote=True)}">← Back to the sheet that '
        f'prints only the labels</a></p>' if proof else
        f'<p><a href="{esc(here + "&grid=1", quote=True)}">Print an alignment '
        f'proof on plain paper first →</a> — the same run with the cell edges '
        f'printing, so a misaligned printer costs a sheet of plain paper '
        f'rather than a sheet of stickers.</p>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{esc(title)}</title><style>{style}</style></head><body>"
        f"{_screen_bar(title, facts, witness, extra)}"
        f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
        f"<defs>{defs}</defs></svg>"
        f"{body}</body></html>")


def _render_talker(t: TalkerSize, sku_id: str, rec: dict[str, Any], copies: int,
                   now: _dt.datetime, witness: Optional[str]) -> str:
    """One product, the price large. Shows what the till charges TODAY."""
    esc = _html.escape
    charged = _charged_paise(rec)
    marked = _marked_paise(rec)
    offer = _offer_on(rec)
    name = str(rec.get("name") or sku_id)
    text = f"{qr_prefix()}{sku_id}"
    sym = _sym_id(sku_id)
    defs = _qr_symbol(sym, _qr_matrix(text))

    style = (
        f"@page{{size:{_mm(t.page_w_mm)} {_mm(t.page_h_mm)};margin:0}}"
        + _PRINT_RESET
        + f".page{{width:{_mm(t.page_w_mm)};height:{_mm(t.page_h_mm)}}}"
        + f".tk{{position:absolute;width:{_mm(t.w_mm)};height:{_mm(t.h_mm)};"
          f"padding:{_mm(t.pad_mm)};display:flex;flex-direction:column;"
          f"justify-content:space-between;overflow:hidden;"
          f"outline:0.15mm dashed #888}}"
        + f".nm{{font-size:{t.name_pt:g}pt;font-weight:700;line-height:1.1;"
          f"letter-spacing:-0.01em;overflow:hidden;display:-webkit-box;"
          f"-webkit-line-clamp:2;-webkit-box-orient:vertical;"
          f"overflow-wrap:anywhere}}"
        + ".mid{display:flex;align-items:flex-end;justify-content:space-between;"
          f"gap:{_mm(t.pad_mm)}}}"
        + f".fig{{font-size:{t.figure_pt:g}pt;font-weight:800;line-height:.95;"
          f"letter-spacing:-0.03em;font-variant-numeric:tabular-nums;"
          f"white-space:nowrap}}"
        + f".was{{font-size:{t.was_pt:g}pt;font-weight:600;color:#444;"
          f"text-decoration:line-through;margin-bottom:{_mm(t.pad_mm)}}}"
        + f".tk svg{{width:{_mm(t.code_mm)};height:{_mm(t.code_mm)};flex:none;"
          f"display:block}}"
        + f".ft{{display:flex;justify-content:space-between;align-items:baseline;"
          f"font-size:{max(t.was_pt - 6, 8):g}pt;color:#444;gap:{_mm(t.pad_mm)}}}"
        + ".ft .k{font-family:ui-monospace,Menlo,Consolas,monospace}"
    )

    figure = f"₹{to_rupees_str(paise(charged))}"
    room_mm = t.w_mm - 3 * t.pad_mm - t.code_mm
    fig_pt = _fit_pt(figure, t.figure_pt, room_mm)
    talker = (
        f'<div class="nm">{esc(_clip(name, 80))}</div>'
        '<div class="mid"><div>'
        + (f'<div class="was">₹{esc(to_rupees_str(paise(marked)))}</div>'
           if offer else "")
        + f'<div class="fig" style="font-size:{fig_pt:g}pt">{esc(figure)}</div>'
        f'</div><svg aria-hidden="true"><use href="#{esc(sym)}"/></svg></div>'
        f'<div class="ft"><span class="k">{esc(sku_id)}</span>'
        f'<span>{"offer price, " if offer else ""}printed {esc(_printed_on(now))}'
        f'</span></div>')

    per = t.per_page
    n_pages = -(-copies // per)
    pages: list[str] = []
    for p in range(n_pages):
        cells = []
        for cell in range(per):
            if p * per + cell >= copies:
                break
            r, c = divmod(cell, t.cols)
            cells.append(
                f'<div class="tk" style="left:{_mm(c * t.w_mm)};'
                f'top:{_mm(r * t.h_mm)}">{talker}</div>')
        pages.append(f'<div class="page">{"".join(cells)}</div>')

    title = f"Shelf talker — {name}"
    facts = [
        f"{copies} talker{'s' if copies != 1 else ''} of {t.w_mm:g} × {t.h_mm:g} "
        f"mm on {n_pages} sheet{'s' if n_pages != 1 else ''} of A4 "
        f"{'landscape' if t.landscape else 'portrait'}; cut along the dashed line",
        (f"An offer is on today: the talker shows ₹{to_rupees_str(paise(charged))} "
         f"with the marked ₹{to_rupees_str(paise(marked))} struck through. When "
         f"the offer ends, print it again."
         if offer else
         f"No offer is on today. The price shown is the marked price, "
         f"₹{to_rupees_str(paise(charged))}."),
        f"Printed {_printed_on(now)}.",
    ]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{esc(title)}</title><style>{style}</style></head><body>"
        f"{_screen_bar(title, facts, witness)}"
        f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
        f"<defs>{defs}</defs></svg>"
        f"{''.join(pages)}</body></html>")


def _html_response(doc: str, filename: str, witness: Optional[str]) -> HTMLResponse:
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="{filename}"',
        "X-Gawaah-Witnessed": "true" if witness else "false",
    }
    return HTMLResponse(doc, headers=headers)


# ----------------------------------------------------------------- routes --


@router.get("/labels/layouts")
def labels_layouts_ep() -> JSONResponse:
    """The sheets this counter prints, every one in millimetres."""
    try:
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(LAYOUTS),
            "layouts": [lay.to_json() for lay in LAYOUTS],
            "talker_sizes": [t.to_json() for t in TALKER_SIZES],
            "limits": {"max_copies": MAX_COPIES, "max_lines": MAX_LINES,
                       "max_labels": MAX_LABELS,
                       "max_talker_copies": MAX_TALKER_COPIES},
            "quiet_zone_modules": QUIET_MODULES,
            "note": ("Print at 100 % on A4 with margins set to none. These are "
                     "the sheets' published dimensions; if your sheet measures "
                     "differently, it is a different sheet."),
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/labels/products")
def labels_products_ep() -> JSONResponse:
    """What can go on a label: every priced product, with the price a sticker gets."""
    try:
        known = catalogue()
        items = [_product_row(s, known[s]) for s in sorted(known)]
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(items),
            "without_printed_code": sum(1 for i in items if not i["has_printed_code"]),
            "offers_today": sum(1 for i in items if i["offer_today"]),
            "items": items,
            "price_on_label": "marked",
            "qr_prefix": qr_prefix(),
        })
    except LabelsRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LabelsRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc})."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise LabelsRefused(R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise LabelsRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


@router.post("/labels/plan")
async def labels_plan_ep(request: Request) -> JSONResponse:
    """How many labels and sheets a run would be, with no paper spent.

    Body: {layout, items: [{sku_id, copies}], skip}. Nothing is written and
    nothing is witnessed; the sheet itself does that when it is rendered.
    """
    try:
        body = await _json_body(request)
        lay = _layout(body.get("layout"))
        lines = _resolve(body.get("items"), catalogue())
        skip = _skip(body.get("skip"), lay)
        return JSONResponse(_plan(lay, lines, skip))
    except LabelsRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LabelsRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}). "
            f"Nothing was printed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/labels/sheet")
def labels_sheet_ep(layout: str = "", items: str = "", skip: str = "0",
                    grid: str = ""):
    """The print-ready page. Open it in a tab, print at 100 %.

    Every product on it was priced by this server from the catalogue a moment
    ago, and the run is witnessed on the labels chain before the page is sent.

    `grid=1` renders the ALIGNMENT PROOF: the same run with the sheet's cell
    edges reaching paper, for plain paper. It is witnessed as its own thing —
    a proof spends no stickers, and a chain that could not tell the two apart
    would count a shop's stock of sheets wrong.
    """
    try:
        lay = _layout(layout)
        lines = _resolve(_parse_items_query(items), catalogue())
        start = _skip(skip, lay)
        proof = _flag(grid, what="'grid', the alignment-proof flag")
        now = _now()
        counts = _count(lines, lay, start)
        head = _audit(
            "labels.sheet",
            layout=lay.layout_id,
            labels=counts["labels"],
            pages=counts["pages"],
            skip=start,
            lines=[{"sku_id": ln["sku_id"], "copies": ln["copies"],
                    "unit_paise": ln["price_paise"]} for ln in lines],
            price_on_label="marked",
            alignment_proof=proof,
            minted=False,
        )
        doc = _render_sheet(lay, lines, start, now, head, proof)
        name = "proof" if proof else "labels"
        return _html_response(doc, f"gawaah_{name}_{lay.layout_id}.html", head)
    except LabelsRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(LabelsRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}). "
            f"Nothing was printed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/labels/talker/{sku_id}")
def labels_talker_ep(sku_id: str, size: str = "a6", copies: str = ""):
    """One product, the price large, cut from an A4 sheet.

    Shows what the till will charge today, offers applied, with the marked
    price struck through when they differ — the opposite choice from the
    sticker, for the reason the module header gives.
    """
    try:
        if not SKU_RE.match(sku_id or ""):
            raise LabelsRefused(
                R_BAD_ITEMS,
                f"{sku_id!r} is not the shape of a sku id in this shop.")
        t = _talker_size(size)
        known = catalogue()
        rec = known.get(sku_id)
        if rec is None:
            raise LabelsRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not something this shop has priced, so there is "
                f"no price to print large.")
        n = t.per_page if not (copies or "").strip() else _whole(
            copies, what="the talker count", reason=R_BAD_COPIES)
        if n <= 0 or n > MAX_TALKER_COPIES:
            raise LabelsRefused(
                R_BAD_COPIES,
                f"{n} talkers is not a run this counter prints; 1 to "
                f"{MAX_TALKER_COPIES}.")
        now = _now()
        head = _audit(
            "labels.talker",
            sku_id=sku_id,
            size=t.size_id,
            copies=n,
            charged_paise=_charged_paise(rec),
            marked_paise=_marked_paise(rec),
            offer_today=_offer_on(rec),
            minted=False,
        )
        doc = _render_talker(t, sku_id, rec, n, now, head)
        return _html_response(doc, f"gawaah_talker_{sku_id}_{t.size_id}.html", head)
    except LabelsRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_UNKNOWN_SKU else 400)
    except MoneyError as exc:
        return _refusal(LabelsRefused(
            R_NO_CATALOGUE,
            f"this product's price is not integer paise ({exc}). Nothing was "
            f"printed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/labels/health")
def labels_health_ep() -> JSONResponse:
    """Where the witness chain lives and whether it still verifies."""
    try:
        p = audit_path()
        ok, n, head, err = verify(p)
        try:
            import cv2  # noqa: F401
            encoder = True
        except Exception:  # noqa: BLE001
            encoder = False
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "module": "labels",
            "layouts": len(LAYOUTS),
            "talker_sizes": len(TALKER_SIZES),
            "audit_file": str(p),
            "exists": p.exists(),
            "lines": n,
            "chain_ok": ok,
            "chain_error": err,
            "head": head,
            "shop_dir": str(shop_dir()),
            "qr_encoder": encoder,
            "qr_prefix": qr_prefix(),
        })
    except LabelsRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
