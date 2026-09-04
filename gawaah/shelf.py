"""TAAK — count what is FACING OUT on a shelf, with the camera.

Point the camera at a shelf and press once. The counter finds every region
that looks like a product, names each one against the shop's own taught
catalogue, and reports FACINGS per product — how many of that product are
visible in the front row — plus the regions it could see and could not name.

WHAT A FACING IS, AND WHAT IT IS NOT. A facing is one product-shaped region
that the camera can see. It is not a unit of stock. A shelf three packets deep
shows one facing and holds three; a shelf with two rows shows the front row
and nothing behind it. So this module counts the SHELF FACE and says so on
every response, and it never writes a facing count into the stock figure as
though somebody had counted the shelf. The comparison it does make is a fact,
not a correction:

    facings visible now   vs   what gawaah/stock.py derives is on hand

and the one direction in which that fact is decisive is stated by name: if the
front row alone shows MORE than the stock figure, the figure is wrong, because
there cannot be fewer packets in the shop than are visible on the shelf. In the
other direction — fewer visible than the figure — nothing can be concluded from
a photograph, and this module says that rather than hinting at shrinkage.

WHAT IS REUSED, AND WHAT IS ASSEMBLED HERE. The till answers "what is on
this counter" in `tools/upload_app.do_counter` out of four pieces: printed
codes read first (a measurement), `gawaah/detector.py` for WHERE, the shop's
own taught vectors through `matless_identifier` for WHICH — at the same cosine
gates every other path uses — and the catalogue for names. This module calls
EXACTLY THOSE PIECES, by name, from the till module, and re-implements none of
them: the gates, the detector and the identifier are the till's, so a packet
named one way at the till is named the same way on the shelf.

What it does NOT do is call `do_counter` itself, for two reasons that are
both about a printed code on a packet. First, that function reads a code's
box as a dict and `decode_all_codes` hands it a list, so the whole read falls
over the moment any code is in view (measured: a `gawaah:` sticker on one
packet, AttributeError, nothing counted) — a till bug, reported, not this
module's to fix. Second, a barcode in the corner of a packet is a code box
INSIDE a region box, and on a shelf those two must be one facing, not two.
So the assembly lives here, with the fold rule beside the code that needs it.

WHAT THIS MODULE MAY NOT DO. It carries no price and no total — a facing count
is a count of packets and money has no business in it. It cannot write a stock
count. It cannot mark anything paid. It proposes; a person reads the gap.

STATED LIMITS, on the response and on the page rather than in a footnote:
  - front row only — facings, not units;
  - packets closer together than about a finger's width (~20 px at 1280 wide)
    read as ONE region, so a tightly packed row under-counts; the detector's
    own test asserts that failure;
  - a product taught from its printed code alone has no appearance stored, so
    it can be counted here only if its code happens to face the camera;
  - a region it could not name is reported with its crop, so the shopkeeper
    can teach it from this very frame — the frame is held in memory for a few
    minutes for exactly that, and for nothing else.

A REFUSAL IS A RESULT. Every failure below answers `{ok: false, reason,
detail}` with a 400 (404 for a shelf read that is no longer held). Nothing here
raises a 500. The router carries NO prefix; the paths are absolute. Mount with
`app.include_router(shelf.router)`.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import GENESIS, Ledger, verify

router = APIRouter()

MODULE = "shelf"

# ---------------------------------------------------------------- refusals --

R_NO_TILL = "till_module_unavailable"
R_BAD_BODY = "shelf_body_not_json"
R_NO_SHELF = "no_such_shelf_read"
R_BAD_REGION = "region_not_on_this_shelf"
R_REGION_NAMED = "region_already_named"
R_NEED_NAME_AND_PRICE = "new_product_needs_a_name_and_a_price"
R_BAD_LIMIT = "limit_not_a_positive_integer"
R_INTERNAL = "shelf_internal_error"
#: A correction is for a region the counter NAMED. Sending one for a region it
#: abstained on would quietly turn an abstention into a name without teaching
#: anything, which is the one thing this module may never do.
R_NOT_NAMED = "region_was_not_named"
R_ALREADY_REJECTED = "region_already_rejected"
R_SAME_NAME = "correction_names_the_same_product"

# ------------------------------------------------------------------ limits --

#: How many shelf reads are held in memory so an unnamed region can be taught
#: from the frame it was seen in. Eight is a few minutes of a shopkeeper
#: walking down an aisle; the frames are JPEG bytes, not decoded arrays, so
#: this is a few megabytes at most.
HELD_FRAMES = 8
#: After this long a held frame is dropped. Long enough to fill in a name and
#: a price on a phone; short enough that a laptop left open does not keep a
#: photograph of the shop in memory all day.
HELD_SECONDS = 15 * 60

#: The annotated frame handed back to the page, longest side. A 1280 px PNG
#: with boxes on it is 1-2 MB and the page only needs to show it in a card.
ANNOTATED_MAX_PX = 960
#: The crop of an unnamed region, longest side. It is shown as a thumbnail and
#: it is what the shopkeeper looks at to decide what the camera missed.
CROP_THUMB_PX = 160
#: When a region is TAUGHT from a held frame, the crop is padded by this
#: fraction of its own size on every side. `plain_crop` segments the product
#: from its surroundings and needs some surroundings to segment against; a box
#: cut exactly on the packet's edge leaves it nothing.
TEACH_PAD_FRAC = 0.2

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

#: How much of a region has to lie inside ANOTHER region before the two are one
#: packet seen twice rather than two facings.
#:
#: WHY THERE IS SUCH A RULE AT ALL. A facing is a POSITION IN A ROW, and two
#: positions in a row do not overlap. The detector's own suppression is written
#: for a counter, where one object can genuinely sit partly in front of
#: another, so it only suppresses a box 80% inside a bigger one; on a shelf the
#: leftover is the packet's lower half plus its shadow plus the price label
#: under it, reported as a second, amber, unnamed region sitting on a packet
#: the shopkeeper has just been told the name of.
#:
#: MEASURED, as intersection over the SMALLER box, on every scene in the bench:
#:
#:     pair                                                     inside
#:     jar's lower half + its shelf label, over the jar           0.75
#:     jar's lower half + its shelf label, over the jar           0.73
#:     ---------------------------------------------------------- 0.60 (here)
#:     any two boxes that are really two different products       0.00
#:
#: The floor of the second population is 0.00 and not merely small: across two
#: tiers, a mixed row, three counter scenes and two cartons on a wall, no pair
#: of boxes that were really two products overlapped at all. That is structural
#: — packets stand side by side on a shelf — so the bar is set with margin
#: under the fragments rather than tight above the products.
#:
#: NOTHING IS DROPPED BY THIS RULE. The region stays in `unnamed`, keeps its
#: crop and its Teach button, and gains `same_packet_as` so the page can group
#: it under the facing it sits on and say why. An abstention that vanished
#: would be worse than a noisy one.
SAME_PACKET_INSIDE = 0.60

SHELF_AUDIT_FILENAME = "shelf.audit.jsonl"

EV_COUNT = "shelf.count"
EV_TAUGHT = "shelf.taught"
#: The counter named a region and the shopkeeper says it named it wrong. The
#: crop is taught to the RIGHT product, so the correction is not a note on a
#: screen — the next read is made by a counter that has seen this view.
EV_CORRECTED = "shelf.corrected"
#: A region that is not a product at all: a price label, a shelf bracket, the
#: shopkeeper's own hand. This corrects THIS read and is recorded; it teaches
#: the camera nothing, and says so rather than implying it does.
EV_REJECTED = "shelf.rejected"

#: Box colours for the annotated frame, in BGR because that is what cv2 draws
#: in. Green and amber are recognition state — named and not named — which is
#: the one meaning those two colours are allowed to carry on this product.
_BGR_NAMED = (79, 138, 14)
_BGR_UNNAMED = (0, 99, 154)
_BGR_INK = (43, 4, 2)
_BGR_WHITE = (255, 255, 255)
#: A region the SHOPKEEPER named, by teaching it or by correcting the counter.
#: Neither green nor amber, because it is neither: the camera did not recognise
#: this packet, a person said what it was. Nor the brand blue, which is the
#: machine's own colour everywhere else in this product and a hand-placed name
#: is the one thing on this frame the machine did not decide. So it is INK —
#: the same near-black the box outlines are drawn in, at full strength.
_BGR_BY_HAND = (51, 20, 15)
#: A region struck out as not a product at all. Drawn, not deleted: a box that
#: silently disappears leaves the shopkeeper unable to tell a rejection he made
#: from a region the counter never proposed.
_BGR_REJECTED = (150, 150, 150)

#: The sentence every response carries. It is the module's thesis and it is
#: repeated rather than linked to, because a number without its limit beside
#: it is exactly the confident figure this program exists not to print.
LIMIT_FRONT_ROW = (
    "Facings are what is visible in the front row. This counts the shelf "
    "face, not the stock: a packet behind another packet is not seen, and a "
    "shelf three deep shows one facing and holds three.")
LIMIT_TOUCHING = (
    "Packets closer together than about a finger's width read as one region, "
    "so a tightly packed row under-counts. Leave a little space between "
    "packets or count that row by hand.")
LIMIT_CODE_ONLY = (
    "A product taught from its printed code alone has no appearance stored. "
    "It is counted here only when its code faces the camera.")
LIMIT_NOT_A_COUNT = (
    "A facing count is never written into the stock figure. Counting the "
    "shelf is the shopkeeper's own act, on the Stock screen, and this does "
    "not do it on his behalf.")
LIMIT_NOT_A_SHELF = (
    "A facing is a position in a row on a shelf. A photograph of somebody "
    "holding stock has no rows and no front, so there is nothing to count "
    "and this says so instead of printing a number.")
#: WHAT "MISSING" MEANS, AND — MORE IMPORTANTLY — WHAT IT DOES NOT.
#:
#: An empty facing is the thing a shopkeeper most needs to see, and it is also
#: the easiest thing on this screen to lie about. A photograph of ONE shelf
#: cannot tell you a product is out of stock: it can be on the next shelf, in
#: the back, or behind the packet in front of it. So this list says exactly one
#: thing — the shop has taught this product and this frame does not show it —
#: and every sentence on it is written so it cannot be read as anything more.
LIMIT_MISSING = (
    "Not seen in this frame is not the same as not in the shop. A product can "
    "be on another shelf, in the back, or behind the packet in front of it. "
    "This list is what the camera did not see here, which is where to look "
    "first — it is not a list of what has run out.")
#: The comparison with the previous read, and the reason it carries a label.
#:
#: Two reads are only comparable if they are of the SAME SHELF, and a
#: photograph does not know which shelf it is of. Comparing this frame against
#: whatever was photographed last — possibly a different aisle — and printing
#: "2 fewer" would be a number invented by the counter. So the comparison is
#: made against the last read carrying the same label, the label is the
#: shopkeeper's own word, and when there is none the response says the
#: comparison is against the last read whatever it was of.
LIMIT_COMPARISON = (
    "Two shelf reads are only comparable if they are of the same shelf, and a "
    "photograph does not know which shelf it is of. Name the shelf before "
    "counting it and the comparison is made against the last read of that "
    "name; leave it blank and it is made against the last read, whatever it "
    "was of.")
#: What a rejection does, said plainly, because the obvious guess is wrong.
LIMIT_REJECTION = (
    "Rejecting a region corrects this read and is written to the chain. It "
    "does not teach the camera: there is no way to teach this counter that "
    "something is NOT a product, and pretending otherwise would be a promise "
    "the next photograph breaks.")

#: How much of the frame a person, an animal or a piece of furniture has to
#: cover before this stops counting facings altogether.
#:
#: WHY THIS EXISTS AT ALL. Pointed at two white cartons held up by somebody in a
#: bedroom, this screen reported TWELVE facings: the detector's fragmentation
#: bug accounted for some of it and is fixed in `gawaah/detector.py`, but the
#: rest was the room — a face, a torso, a wardrobe edge, a bag on the floor —
#: and no amount of detector work makes a bedroom into a shelf. Measured after
#: the detector fix, that frame still yields ten regions for two cartons.
#:
#: The number a shopkeeper can act on is therefore no number. A wrong facing
#: count is worse than an admitted one because it looks like a measurement.
#:
#: MEASURED, as the union of confidently-detected not-a-facing boxes over the
#: frame area, on the frames used to fix this:
#:
#:     two people holding two cartons        34%   refuses
#:     zidane.jpg (two people, no shelf)     47%   refuses
#:     bus.jpg (three people at a bus)       23%   refuses
#:     ---------------------------------------------- 20% (here)
#:     every counter and shelf scene in the detector tests    0%   counts
#:
#: A shopkeeper's hand or shoulder at the edge of an otherwise good shelf photo
#: is well under this and still counts, which is the case this must not break.
NOT_A_SHELF_FRAME_FRAC = 0.20


class ShelfRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: ShelfRefused) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status)


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400)


# ------------------------------------------------------------- the till --
#
# Imported late and looked up in sys.modules first, for the reasons
# storefront.py records: the till mounts this router (a cycle at module
# scope), the till is expensive to import, and importing it under the OTHER
# of its two names loads a second copy with its own catalogue handle.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
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
        raise ShelfRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). The shelf is read with the till's own detector and "
            f"recogniser and will not keep a second one.") from None
    return upload_app


def shop_dir() -> Path:
    """The till's answer to where the shop lives — never a second one."""
    return Path(_till().store_dir())


def audit_path() -> Path:
    """This module's own hash chain, beside the catalogue.

    NOT `results/audit.jsonl`: the money service holds that file open in
    another process and keeps the chain head in memory, so a second writer
    would break `make verify-ledger` on the one log that must be beyond
    argument. Same rule as storefront.py, offers.py and stock.py.
    """
    return shop_dir() / SHELF_AUDIT_FILENAME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


_WRITE_LOCK = threading.Lock()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """One line on the chain. Best effort, never silent.

    A shelf read is a reading, not a store: the count is in the response
    whether or not the line was written, and the response says `audited:
    false` when it was not. No pixels ever reach this file — boxes and counts
    only. An audit log is the file most likely to be pasted into a bug report.
    """
    try:
        with _WRITE_LOCK:
            return Ledger(audit_path()).append(
                ts=_now_iso(), module=MODULE, event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a reading
        return None


def read_events() -> tuple[tuple[dict, ...], dict]:
    """Every verified line of this module's chain, and the chain's state.

    Truncated at the first broken link, as stock.py does: a line whose hash
    does not recompute is not evidence of anything.
    """
    path = audit_path()
    ok, verified, head, error = verify(path)
    records: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break
            if not isinstance(rec, dict):
                break
            records.append(rec)
    if not ok:
        records = records[:verified]
    return tuple(records), {
        "ok": bool(ok),
        "exists": path.exists(),
        "lines_verified": int(verified),
        "lines_readable": len(records),
        "head": head if path.exists() else GENESIS,
        "error": error,
        "path": str(path),
    }


# ------------------------------------------------------------ held frames --
#
# A shelf read is stateless except for this: the frame is kept for a few
# minutes so that an unnamed region can be TAUGHT from the picture it was seen
# in, rather than asking the shopkeeper to go and photograph the packet again.
# Nothing else reads these bytes. They are never written to disk.

_HELD: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_HELD_LOCK = threading.Lock()


def _hold(shelf_id: str, raw: bytes, regions: list[dict[str, Any]],
          items: list[dict[str, Any]], *, label: Optional[str],
          use_yolo: bool) -> None:
    """Keep the frame AND the reading, so a correction can be applied to it.

    The items are held as well as the pixels because a correction has to
    produce a whole new reading — new facings, new gaps against the stock
    figure, a new picture with the boxes redrawn — and re-running the detector
    and the recogniser on the same frame to get back the same regions would be
    half a second of work to reproduce an answer this process already has.
    Nothing here is written to disk.
    """
    with _HELD_LOCK:
        _HELD[shelf_id] = {"raw": raw, "regions": regions, "items": items,
                           "at": time.monotonic(), "taught": {},
                           "overrides": {}, "label": label,
                           "use_yolo": bool(use_yolo)}
        _HELD.move_to_end(shelf_id)
        while len(_HELD) > HELD_FRAMES:
            _HELD.popitem(last=False)


def _lean(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The region list without its crops, for the held record.

    The crops are cut fresh from the held frame on every assembly, so keeping a
    second base64 copy of each of them per held read would put a few megabytes
    of duplicate pixels in memory for nothing.
    """
    return [{k: v for k, v in r.items() if k != "crop_png_b64"} for r in regions]


def _expire(now: Optional[float] = None) -> None:
    now = time.monotonic() if now is None else now
    with _HELD_LOCK:
        for key in [k for k, v in _HELD.items()
                    if now - v["at"] > HELD_SECONDS]:
            _HELD.pop(key, None)


def _held(shelf_id: str) -> dict[str, Any]:
    _expire()
    with _HELD_LOCK:
        rec = _HELD.get(shelf_id)
    if rec is None:
        raise ShelfRefused(
            R_NO_SHELF,
            f"shelf read {shelf_id!r} is not held any more. A frame is kept "
            f"for {HELD_SECONDS // 60} minutes, and only the last "
            f"{HELD_FRAMES} reads are kept. Read the shelf again and teach "
            f"from the new picture.",
            status=404)
    return rec


def forget_held() -> None:
    """Drop every held frame. For tests, and for a shopkeeper who asks."""
    with _HELD_LOCK:
        _HELD.clear()


# ------------------------------------------------------------- geometry --


def _centre_inside(inner: list[int], outer: list[int]) -> bool:
    """Is the centre of `inner` inside `outer`? Boxes are [x, y, w, h]."""
    cx = inner[0] + inner[2] // 2
    cy = inner[1] + inner[3] // 2
    return (outer[0] <= cx <= outer[0] + outer[2]
            and outer[1] <= cy <= outer[1] + outer[3])


def _inside_frac(inner: list[int], outer: list[int]) -> float:
    """How much of `inner` lies inside `outer`, as a fraction of `inner`."""
    ix = max(0, min(inner[0] + inner[2], outer[0] + outer[2]) - max(inner[0], outer[0]))
    iy = max(0, min(inner[1] + inner[3], outer[1] + outer[3]) - max(inner[1], outer[1]))
    return (ix * iy) / float(max(1, inner[2] * inner[3]))


def _area(box: list[int]) -> int:
    return int(box[2]) * int(box[3])


def _int_box(box: Any) -> list[int]:
    """[x, y, w, h] as ints, from a list, a tuple or an {x, y, w, h} dict.

    `decode_all_codes` hands back a list and the till's own `/counter` read it
    as a dict; accepting both here is what keeps a code in view from being a
    crash instead of a name.
    """
    if isinstance(box, dict):
        return [int(box.get("x", 0)), int(box.get("y", 0)),
                int(box.get("w", 0)), int(box.get("h", 0))]
    b = list(box or [0, 0, 0, 0])
    return [int(b[0]), int(b[1]), int(b[2]), int(b[3])]


# ------------------------------------------------------------ the reading --


def _same_packet(code_box: list[int], region_box: list[int]) -> bool:
    """Is this code on this region? Either centre inside the other.

    A barcode in the corner of a packet is a small box inside a large one; a
    QR sticker that fills a packet can come back as a code box slightly
    LARGER than the contour's region. Both are one packet.
    """
    return (_centre_inside(code_box, region_box)
            or _centre_inside(region_box, code_box))


def _fold_codes_into_regions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One facing per packet, even when the packet also shows a code.

    A code whose box shares a packet with a region names that region — a code
    is a measurement and the cosine is an opinion, so the code's sku wins
    where both exist, and the disagreement is kept on the row as
    `appearance_said` — and the code item itself is folded away. A code on no
    region at all (a sticker on a bare shelf edge) stays as its own facing.
    """
    regions = [i for i in items if i.get("how") == "appearance"]
    codes = [i for i in items if i.get("how") == "code"]
    out: list[dict[str, Any]] = list(regions)
    for c in codes:
        cbox = _int_box(c.get("box"))
        host = next((r for r in regions
                     if _same_packet(cbox, _int_box(r.get("box")))), None)
        if host is None:
            out.append(c)
            continue
        host["code"] = c.get("code")
        if c.get("sku_id") and c.get("name"):
            if host.get("sku_id") and host["sku_id"] != c["sku_id"]:
                host["appearance_said"] = host["sku_id"]
            host["sku_id"] = c["sku_id"]
            host["name"] = c["name"]
            host["how"] = "code"
            host["reason"] = "read_a_printed_code_on_this_facing"
            host.pop("detail", None)
        else:
            host["code_reason"] = c.get("reason")
    return out


def _read_items(up: Any, bgr: Any, *, use_yolo: bool) -> list[dict[str, Any]]:
    """Every packet-shaped thing in the frame, named where the till would name it.

    THE TILL'S OWN PIECES, IN THE TILL'S OWN ORDER:
      1. `decode_all_codes` + `resolve_code`   a code that was READ names its
                                               packet; no cosine is consulted
      2. `detector.detect`                     WHERE — class-agnostic regions
      3. `matless_identifier(...).identify`    WHICH — the shop's own vectors,
                                               judged with no footprint, at
                                               the higher appearance-only bar
    Names come from `taught_skus` (sight) and `priced_skus` (code-only). No
    price is read for any purpose.
    """
    from . import detector as _det  # noqa: WPS433

    embed = up.load_embedder()
    up.load_store()
    known = up.taught_skus()
    priced = up.priced_skus()
    if not known and not priced:
        raise up.UploadRefused(
            up.R_EMPTY_GALLERY,
            "Nothing has been taught yet, so there is nothing to compare this "
            "shelf against. Teach a product first.")
    names: dict[str, str] = {k: str(v.get("name") or k) for k, v in priced.items()}
    names.update({r.sku_id: r.name for r in known})

    items: list[dict[str, Any]] = []
    for c in up.decode_all_codes(bgr):
        payload = str(c.get("payload") or "")
        sku_id = c.get("sku_id") or up.resolve_code(payload)
        row: dict[str, Any] = {"box": _int_box(c.get("box")), "how": "code",
                               "code": payload, "found_by": "code"}
        if sku_id and sku_id in names:
            row.update({"sku_id": sku_id, "name": names[sku_id],
                        "reason": "read_a_printed_code"})
        else:
            row.update({"sku_id": None, "name": None,
                        "reason": "code_not_in_catalogue" if sku_id
                        else "code_not_bound",
                        "detail": (f"{payload!r} was read cleanly but is not "
                                   f"bound to any product. Teach it, or type "
                                   f"the number when teaching.")})
        items.append(row)

    ident = up.matless_identifier(known, embed) if known else None
    for p in _det.detect(bgr, use_yolo=use_yolo):
        crop = p.crop(bgr)
        if crop.size == 0 or min(crop.shape[:2]) < 24:
            continue
        row = {"box": _int_box(p.box), "how": "appearance", "found_by": p.source}
        if ident is None:
            row.update({"sku_id": None, "name": None,
                        "reason": "nothing_taught_by_appearance",
                        "detail": ("Every product in this shop was taught by "
                                   "its printed code, so there is no "
                                   "appearance to compare against. Teach one "
                                   "from a photograph.")})
            items.append(row)
            continue
        try:
            res = ident.identify(crop, None)
        except Exception as exc:  # noqa: BLE001 - a bad crop is not a 500
            row.update({"sku_id": None, "name": None,
                        "reason": "identify_failed",
                        "detail": f"{type(exc).__name__}: {exc}"})
            items.append(row)
            continue
        row.update({
            "top1": round(float(res.top1), 4) if res.top1 is not None else None,
            "top1_sku": res.top1_sku,
            "phi_used": res.phi_applied,
        })
        if res.sku_id and res.sku_id in names:
            row.update({"sku_id": res.sku_id, "name": names[res.sku_id],
                        "reason": "recognised_by_appearance"})
        else:
            # ABSTAIN, LOUDLY. Invariant 7: an unnamed region must never
            # quietly become a facing of the nearest thing.
            row.update({"sku_id": None, "name": None,
                        "reason": res.reason or "below_the_bar",
                        "detail": ("Something is here and it does not match "
                                   "anything taught closely enough to name. "
                                   "Show its printed code, or teach this view "
                                   "of it.")})
        items.append(row)
    return _fold_codes_into_regions(items)


def _not_a_shelf(bgr: Any, *, use_yolo: bool) -> Optional[dict[str, Any]]:
    """Is this a photograph of a shelf at all, or of somebody holding stock?

    Returns the condition when the frame is disqualified, `None` when it is
    countable. The judgement is made from the OPTIONAL model, so a checkout
    without the weights counts exactly as it did before — this can only ever
    withhold a number, never invent one.

    WHY NOT JUST DROP THE PERSON'S REGIONS AND COUNT THE REST. Measured, and it
    does not work: on the bench frame the person's box is (139, 189, 566, 528)
    and carton B at (518, 384, 182, 220) is entirely INSIDE it, because the
    person is holding the carton. Any rule strong enough to delete the face and
    the torso — both of which are parts of the person, not the whole of it —
    also deletes the product being held up to the camera, and a missing line is
    the worst failure this counter has. So the frame is refused whole.
    """
    if not use_yolo:
        return None
    import cv2
    import numpy as np

    from . import detector as _det  # noqa: WPS433

    seen = _det.yolo_rejections(bgr)
    if not seen:
        return None
    h, w = bgr.shape[:2]
    area = float(max(1, h * w))
    # Union, not sum: two overlapping boxes are not twice the frame.
    cover = np.zeros((h, w), np.uint8)
    for r in seen:
        cv2.rectangle(cover, (r.x, r.y), (r.x + r.w, r.y + r.h), 255, -1)
    frac = float((cover > 0).sum()) / area
    if frac < NOT_A_SHELF_FRAME_FRAC:
        return None
    labels = sorted({r.label for r in seen})
    lead = labels[0] if len(labels) == 1 else ", ".join(labels)
    return {
        "reason": "frame_is_not_a_shelf",
        "detail": (
            f"A {lead} covers {round(frac * 100)}% of this frame, so this is a "
            f"photograph of somebody holding stock rather than of a shelf "
            f"face. Product-shaped things are visible and are listed below, "
            f"but how many facings are on a shelf cannot be read off it. "
            f"Point the camera along the shelf, with nobody in the frame."),
        "covers_frame_pct": round(frac * 100, 1),
        "saw": [r.to_json() for r in seen],
    }


def _named(item: dict[str, Any]) -> bool:
    return bool(item.get("sku_id")) and bool(item.get("name"))


# ----------------------------------------------------- what was here before --


def reads_on_chain() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every shelf read, oldest first, WITH ITS CORRECTIONS ALREADY APPLIED.

    A correction is its own line on the chain — the log has to show that the
    counter said one thing and a person said another, or it is not a log — but
    a LIST of reads that showed the uncorrected figures would be a list of
    numbers the shopkeeper has already told this counter are wrong, and the
    comparison drawn against the last read would be drawn against them.

    So the chain is replayed rather than filtered: a count line opens a read,
    and every correction line carrying the same `shelf_id` overwrites its
    totals with the ones recomputed at the time. `corrected` says it happened.
    """
    events, chain = read_events()
    order: list[str] = []
    reads: dict[str, dict[str, Any]] = {}
    for e in events:
        ev = e.get("event")
        sid = str(e.get("shelf_id") or "")
        if ev == EV_COUNT:
            if sid and sid not in reads:
                order.append(sid)
            reads[sid] = {
                "at": e.get("ts"),
                "shelf_id": sid,
                "label": e.get("label"),
                "frame_px": e.get("frame_px"),
                "regions_seen": e.get("regions_seen"),
                "named": e.get("named"),
                "unnamed": e.get("unnamed"),
                "products": e.get("products"),
                "facings": e.get("facings") or [],
                "counted": e.get("abstained") is None,
                "corrected": False,
                "corrections": 0,
                "hash": e.get("hash"),
            }
        elif ev in (EV_CORRECTED, EV_REJECTED, EV_TAUGHT):
            row = reads.get(sid)
            if row is None:
                continue
            row["corrected"] = True
            row["corrections"] = int(row["corrections"]) + 1
            row["hash"] = e.get("hash")
            # A teach line written before this field existed carries no totals;
            # the read then keeps the figures it was counted with rather than
            # being blanked by a line that never held them.
            if isinstance(e.get("named"), int):
                row.update({
                    "regions_seen": e.get("regions_seen"),
                    "named": e.get("named"),
                    "unnamed": e.get("unnamed"),
                    "products": e.get("products"),
                    "facings": e.get("facings") or [],
                })
    return [reads[s] for s in order if s in reads], chain


def _previous_read(label: Optional[str], skip: Optional[str] = None
                   ) -> Optional[dict[str, Any]]:
    """The read this one is comparable with, or None.

    Comparable means SAME SHELF, and the only thing that carries which shelf a
    photograph is of is the label the shopkeeper typed. With a label, the
    comparison is against the last read of that name and is a real comparison.
    Without one it is against the last read whatever it was of, and the row
    says so — `same_shelf: false` — so nothing downstream can print a
    difference as though two aisles were one.
    """
    try:
        rows, _chain = reads_on_chain()
    except Exception:  # noqa: BLE001 - an unreadable chain is not a count
        return None
    want = (label or "").strip().casefold()
    for row in reversed(rows):
        if skip and row.get("shelf_id") == skip:
            continue
        if not row.get("counted"):
            continue
        if want:
            if str(row.get("label") or "").strip().casefold() != want:
                continue
            return {**row, "same_shelf": True}
        return {**row, "same_shelf": not row.get("label")}
    return None


def _previous_facings(previous: Optional[dict[str, Any]]) -> dict[str, int]:
    if not previous:
        return {}
    out: dict[str, int] = {}
    for f in previous.get("facings") or []:
        sku = f.get("sku_id")
        if sku:
            out[str(sku)] = int(f.get("facings") or 0)
    return out


# ------------------------------------------------- what is NOT on the shelf --


def _missing(up: Any, seen: dict[str, Any], by_sku: dict[str, dict[str, Any]],
             figures_ok: bool, previous: Optional[dict[str, Any]]
             ) -> list[dict[str, Any]]:
    """Taught products this frame does not show, worst gap first.

    AN EMPTY FACING IS THE THING A SHOPKEEPER MOST NEEDS TO SEE, and it is also
    the easiest thing on this screen to lie about. So each row states only what
    is true of it, and the ordering is by how much evidence there is that the
    facing really is empty rather than the product being somewhere else:

      1. `was_here`   it had facings on the last read of this same shelf and
                      has none now. This is the strong one: the same camera,
                      the same shelf, and the packets are gone.
      2. `never_seen` the shop taught it by sight and this frame does not show
                      it. It may simply live on another shelf.
      3. `cannot_be_seen` taught from a printed code alone, so it has no
                      appearance stored and could only ever be counted here
                      with its code facing the camera. Its absence is evidence
                      of nothing at all, and it is listed apart for that
                      reason rather than left out — left out, a shopkeeper
                      would think the counter had checked.

    The stock figure rides along where stock.py has one, never as a verdict:
    "the shelf shows none and the figure says 12" is a place to look, not a
    finding, because the twelve can be in the back.
    """
    names: dict[str, str] = {}
    by_sight: set[str] = set()
    try:
        for r in up.taught_skus():
            names[r.sku_id] = r.name
            by_sight.add(r.sku_id)
    except Exception:  # noqa: BLE001 - a catalogue that will not read is not a gap
        return []
    try:
        for k, v in up.priced_skus().items():
            names.setdefault(k, str(v.get("name") or k))
    except Exception:  # noqa: BLE001
        pass

    before = _previous_facings(previous)
    same_shelf = bool(previous and previous.get("same_shelf"))
    out: list[dict[str, Any]] = []
    for sku_id, name in names.items():
        if sku_id in seen:
            continue
        sighted = sku_id in by_sight
        was = int(before.get(sku_id, 0))
        row = by_sku.get(sku_id) if figures_ok else None
        on_hand = row.get("on_hand_units") if isinstance(row, dict) else None
        if isinstance(on_hand, bool) or not isinstance(on_hand, int):
            on_hand = None
        if not sighted:
            verdict, rank = "cannot_be_seen", 3
            sentence = (
                f"{name} was taught from its printed code alone, so this "
                f"counter has never seen what it looks like. It is not "
                f"missing from this shelf — it could not have been counted on "
                f"it either way, unless its code faced the camera.")
        elif was > 0 and same_shelf:
            verdict, rank = "was_here", 0
            sentence = (
                f"{name} had {was} facing{'s' if was != 1 else ''} on the last "
                f"read of this shelf and none now. Same shelf, same camera: "
                f"this is the one to look at first.")
        elif was > 0:
            verdict, rank = "was_here_elsewhere", 1
            sentence = (
                f"{name} had {was} facing{'s' if was != 1 else ''} on the last "
                f"read and none on this one — but that read was not labelled, "
                f"so it may have been of a different shelf.")
        else:
            verdict, rank = "never_seen", 2
            sentence = (
                f"{name} is taught and this frame does not show it. It may be "
                f"on another shelf or behind the front row; a photograph of "
                f"one shelf cannot tell you which.")
        if on_hand is not None and sighted:
            sentence += (
                f" The stock figure says {on_hand} on hand." if on_hand > 0
                else " The stock figure also says none on hand.")
        out.append({
            "sku_id": sku_id, "name": name,
            "taught_by_sight": sighted,
            "previous_facings": was if previous else None,
            "on_hand_units": on_hand,
            "verdict": verdict,
            "sentence": sentence,
            "_rank": rank,
        })
    out.sort(key=lambda r: (r["_rank"], -(r["previous_facings"] or 0),
                            r["name"].lower()))
    for r in out:
        r.pop("_rank", None)
    return out


def _stock_figures() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """{sku -> stock row} from gawaah/stock.py, and whether that worked.

    The figure is stock.py's own derivation, taken as it stands. A shelf
    module that computed its own on-hand would be a third answer to a
    question two screens already agree on. When the derivation is not
    available the comparison is ABSENT and the response says why, rather than
    comparing against a zero nobody derived.
    """
    try:
        from . import stock as _stock  # noqa: WPS433 - late, like the till

        rows = _stock.stock_rows()
    except Exception as exc:  # noqa: BLE001 - a missing figure is a fact
        reason = getattr(exc, "reason", None) or type(exc).__name__
        detail = getattr(exc, "detail", None) or str(exc)
        return {}, {"available": False, "reason": str(reason),
                    "detail": str(detail), "source": "gawaah/stock.py"}
    by_sku = {str(r.get("sku_id")): r for r in rows.get("items") or []
              if r.get("sku_id")}
    return by_sku, {"available": True, "reason": None, "detail": None,
                    "source": "gawaah/stock.py stock_rows()",
                    "chain": rows.get("chain")}


def _gap(sku_id: str, name: str, facings: int,
         row: Optional[dict[str, Any]], figures_ok: bool) -> dict[str, Any]:
    """The facings beside the stock figure, and what — if anything — follows.

    Every branch says in words what it can and cannot conclude. The ONLY
    decisive direction is the shelf showing more than the figure: the shop
    cannot hold fewer than are visible, so the figure is wrong. Fewer visible
    than the figure is the normal state of a shelf with depth and concludes
    nothing.
    """
    base: dict[str, Any] = {
        "on_hand_units": None, "basis": None, "counted_at": None,
        "derivation": None, "difference": None,
        "shelf_exceeds_figure": False,
    }
    if not figures_ok:
        return {**base, "verdict": "no_figure_available", "sentence": (
            f"{facings} facing{'s' if facings != 1 else ''} of {name} are "
            f"visible. The stock figure could not be read, so there is "
            f"nothing to set that beside.")}
    if row is None:
        return {**base, "verdict": "not_in_stock_rows", "sentence": (
            f"{facings} facing{'s' if facings != 1 else ''} of {name} are "
            f"visible. {sku_id!r} has no row on the Stock screen, so there "
            f"is no figure to compare.")}
    on_hand = row.get("on_hand_units")
    base.update({
        "basis": row.get("basis"),
        "counted_at": row.get("counted_at"),
        "derivation": row.get("derivation"),
    })
    if isinstance(on_hand, bool) or not isinstance(on_hand, int):
        return {**base, "verdict": "never_counted", "sentence": (
            f"{facings} facing{'s' if facings != 1 else ''} of {name} are "
            f"visible. This product has never been counted, so there is no "
            f"stock figure to compare against — and a facing count is not a "
            f"stock count.")}
    diff = facings - int(on_hand)
    base.update({"on_hand_units": int(on_hand), "difference": diff})
    if diff > 0:
        return {**base, "verdict": "shelf_exceeds_figure",
                "shelf_exceeds_figure": True, "sentence": (
            f"The front row alone shows {facings} of {name}; the stock "
            f"figure says {on_hand}. The shop cannot hold fewer than are "
            f"visible, so the figure is wrong by at least {diff}. Count this "
            f"shelf on the Stock screen.")}
    if diff == 0:
        return {**base, "verdict": "face_matches_figure", "sentence": (
            f"{facings} of {name} are visible and the stock figure is also "
            f"{on_hand}. That is consistent; it is not proof, because a "
            f"packet behind the front row is not seen.")}
    return {**base, "verdict": "face_below_figure", "sentence": (
        f"{facings} of {name} are visible; the stock figure says {on_hand}. "
        f"{-diff} may be behind the front row or elsewhere in the shop. "
        f"Nothing about that can be told from a photograph.")}


#: The four states a box on the annotated frame can be in, and the colour and
#: line each wears. Green and amber are recognition state on this product —
#: named by the camera, not named by the camera — and nothing else here is
#: allowed to wear them: a box the SHOPKEEPER named is ink, and a box he struck
#: out is grey and hollow.
_BOX_STYLE = {
    "named": (_BGR_NAMED, False),
    "by_hand": (_BGR_BY_HAND, False),
    "unnamed": (_BGR_UNNAMED, True),
    "rejected": (_BGR_REJECTED, True),
}


def _draw_annotated(bgr: Any, drawn: list[dict[str, Any]]) -> Optional[str]:
    """The frame with every box on it, as a base64 PNG no wider than needed.

    EVERY BOX CARRIES ITS REGION NUMBER, including the named ones. The list
    beside the picture is addressed by region number and the picture used to
    number only the boxes it could not name, so "region 4" in the Facings list
    pointed at nothing a shopkeeper could find. A row and a box that cannot be
    matched by eye are two answers, not one.
    """
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    k = min(1.0, ANNOTATED_MAX_PX / float(max(h, w)))
    canvas = bgr.copy()
    if k < 1.0:
        canvas = cv2.resize(canvas, (max(1, int(round(w * k))),
                                     max(1, int(round(h * k)))),
                            interpolation=cv2.INTER_AREA)
    lw = max(2, int(round(canvas.shape[1] / 400)))
    scale = max(0.45, canvas.shape[1] / 1600.0)

    def _box(b: list[int], colour: tuple[int, int, int], label: str,
             dashed: bool) -> None:
        x, y, bw, bh = (int(round(v * k)) for v in b)
        if dashed:
            step = max(8, lw * 5)
            for sx in range(x, x + bw, step * 2):
                cv2.line(canvas, (sx, y), (min(x + bw, sx + step), y), colour, lw)
                cv2.line(canvas, (sx, y + bh), (min(x + bw, sx + step), y + bh),
                         colour, lw)
            for sy in range(y, y + bh, step * 2):
                cv2.line(canvas, (x, sy), (x, min(y + bh, sy + step)), colour, lw)
                cv2.line(canvas, (x + bw, sy), (x + bw, min(y + bh, sy + step)),
                         colour, lw)
        else:
            cv2.rectangle(canvas, (x, y), (x + bw, y + bh), _BGR_INK, lw + 2)
            cv2.rectangle(canvas, (x, y), (x + bw, y + bh), colour, lw)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX,
                                         scale, max(1, lw - 1))
        pad = max(3, int(round(4 * scale)))
        ly = y - th - base - pad * 2
        if ly < 0:
            ly = y + pad
        cv2.rectangle(canvas, (x, ly), (x + tw + pad * 2, ly + th + base + pad * 2),
                      colour, cv2.FILLED)
        cv2.putText(canvas, label, (x + pad, ly + th + pad), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, _BGR_WHITE, max(1, lw - 1), cv2.LINE_AA)

    # Rejected first, so a struck-out box never covers a live one.
    for d in sorted(drawn, key=lambda d: d["state"] != "rejected"):
        colour, dashed = _BOX_STYLE.get(str(d["state"]), _BOX_STYLE["unnamed"])
        _box(_int_box(d["box"]), colour, str(d["label"]), dashed=dashed)

    ok, buf = cv2.imencode(".png", np.ascontiguousarray(canvas))
    return base64.b64encode(buf.tobytes()).decode() if ok else None


def _thumb(bgr: Any, box: list[int], side: int) -> Optional[str]:
    import cv2

    x, y, w, h = box
    crop = bgr[max(0, y):max(0, y) + max(1, h), max(0, x):max(0, x) + max(1, w)]
    if crop.size == 0:
        return None
    ch, cw = crop.shape[:2]
    k = min(1.0, side / float(max(ch, cw)))
    if k < 1.0:
        crop = cv2.resize(crop, (max(1, int(round(cw * k))),
                                 max(1, int(round(ch * k)))),
                          interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", crop)
    return base64.b64encode(buf.tobytes()).decode() if ok else None


def _label(raw: Any) -> Optional[str]:
    """What shelf this is, in the shopkeeper's own word. Optional, and short.

    It exists for ONE reason: two reads are comparable only if they are of the
    same shelf, and nothing in a photograph says which shelf it is of.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())[:60].strip()
    return text or None


def _rows_for(items: list[dict[str, Any]],
              overrides: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per region, with what the shopkeeper said laid over what the
    camera said — and the camera's own answer kept beside it, never replaced.

    `was` and `was_reason` survive a correction on purpose. A screen that
    showed only the corrected name would hide the counter's mistake from the
    person best placed to notice it happening twice.
    """
    rows: list[dict[str, Any]] = []
    for idx, it in enumerate(items, 1):
        ov = overrides.get(idx) or {}
        box = _int_box(it.get("box"))
        row: dict[str, Any] = {
            "region": idx,
            "box": box,
            "found_by": it.get("found_by") or ("code" if it.get("how") == "code"
                                               else None),
            "top1": it.get("top1"),
            "top1_sku": it.get("top1_sku"),
            "code": it.get("code"),
            "camera_sku_id": it.get("sku_id") if _named(it) else None,
            "camera_name": it.get("name") if _named(it) else None,
        }
        if ov.get("rejected"):
            row.update({
                "state": "rejected", "sku_id": None, "name": None,
                "how": "rejected_by_the_shopkeeper",
                "reason": "not_a_product",
                "detail": (f"You marked region {idx} as not a product. It is "
                           f"not counted and not listed as something to teach. "
                           + LIMIT_REJECTION),
                "was": it.get("sku_id") if _named(it) else None,
            })
            rows.append(row)
            continue
        if ov.get("sku_id"):
            row.update({
                "state": "by_hand",
                "sku_id": ov["sku_id"], "name": ov.get("name") or ov["sku_id"],
                "how": "named_by_hand",
                "reason": ("corrected_by_the_shopkeeper" if _named(it)
                           else "taught_by_the_shopkeeper"),
                "detail": None,
                "was": it.get("sku_id") if _named(it) else None,
                "was_reason": it.get("reason") if _named(it) else None,
            })
            rows.append(row)
            continue
        if _named(it):
            row.update({
                "state": "named",
                "sku_id": it["sku_id"], "name": it["name"],
                "how": it.get("how") or "appearance",
                "reason": it.get("reason") or "recognised_by_appearance",
                "detail": None,
                "appearance_said": it.get("appearance_said"),
            })
        else:
            row.update({
                "state": "unnamed", "sku_id": None, "name": None,
                "how": it.get("how") or "appearance",
                "reason": it.get("reason") or "below_the_bar",
                "detail": it.get("detail"),
            })
        rows.append(row)
    return rows


def _mark_same_packet(rows: list[dict[str, Any]]) -> int:
    """Flag every unnamed region that is really part of a named one.

    See SAME_PACKET_INSIDE. The row is not removed, not renamed and not
    counted as a facing — it gains `same_packet_as` and nothing else, so the
    page can group it under the packet it sits on and the shopkeeper can still
    disagree and teach it.
    """
    solid = [r for r in rows if r["state"] in ("named", "by_hand")]
    n = 0
    for r in rows:
        if r["state"] != "unnamed":
            continue
        best: Optional[tuple[float, dict[str, Any]]] = None
        for s in solid:
            if _area(s["box"]) < _area(r["box"]):
                continue
            frac = _inside_frac(r["box"], s["box"])
            if frac >= SAME_PACKET_INSIDE and (best is None or frac > best[0]):
                best = (frac, s)
        if best is None:
            continue
        frac, s = best
        r["same_packet_as"] = {
            "region": s["region"], "sku_id": s["sku_id"], "name": s["name"],
            "inside": round(frac, 2),
            "detail": (f"{int(round(frac * 100))}% of this region is inside "
                       f"region {s['region']}, which is {s['name']}. Two "
                       f"facings are two positions in a row and do not "
                       f"overlap, so this is almost certainly the same packet "
                       f"seen twice — its lower half, its shadow, or the "
                       f"price label under it."),
        }
        n += 1
    return n


def _assemble(up: Any, bgr: Any, items: list[dict[str, Any]], *,
              shelf_id: str, label: Optional[str], use_yolo: bool,
              annotate: bool, overrides: dict[int, dict[str, Any]],
              previous: Optional[dict[str, Any]], at: str,
              elapsed_ms: float, corrections: int) -> dict[str, Any]:
    """The whole answer, from the regions and whatever the shopkeeper has said.

    Called once for the photograph and again after every correction, so a
    corrected read is the SAME shape as a fresh one — the page renders one
    thing, and no figure on it is ever computed in the browser.
    """
    fh, fw = bgr.shape[:2]
    rows = _rows_for(items, overrides)
    # EVERY REGION CARRIES ITS CROP, not only the ones that were not named.
    # The crop is what a shopkeeper looks at to decide whether the name on a
    # region is right, and it was sent only for regions the counter had already
    # given up on — so the one dialog where he is being asked "is this really
    # ponds?" had nothing in it to look at. A dozen 160 px crops is a few
    # hundred kilobytes on the shop's own LAN, which is where this is served
    # from; being unable to check a name is not worth saving them.
    for r in rows:
        r["crop_png_b64"] = _thumb(bgr, r["box"], CROP_THUMB_PX)
    abstained = _not_a_shelf(bgr, use_yolo=use_yolo)

    by_sku, figures = _stock_figures()
    figures_ok = bool(figures.get("available"))
    before = _previous_facings(previous)

    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    unnamed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    same_packet = 0

    if abstained is None:
        same_packet = _mark_same_packet(rows)
        for r in rows:
            if r["state"] == "rejected":
                rejected.append(r)
                continue
            if r["state"] == "unnamed":
                unnamed.append(r)
                continue
            g = groups.setdefault(r["sku_id"], {
                "sku_id": r["sku_id"], "name": r["name"], "facings": 0,
                "boxes": [], "regions": [], "by_code": 0, "by_appearance": 0,
                "by_hand": 0, "appearance_said": [],
            })
            g["facings"] += 1
            g["boxes"].append(r["box"])
            g["regions"].append(r["region"])
            if r["state"] == "by_hand":
                g["by_hand"] += 1
            elif r["how"] == "code":
                g["by_code"] += 1
            else:
                g["by_appearance"] += 1
            if r.get("appearance_said"):
                g["appearance_said"].append(r["appearance_said"])
    else:
        # NOTHING IS HIDDEN, ONLY THE COUNT IS WITHHELD. Every region the camera
        # found, named or not, is still listed — with the name it matched, so a
        # shopkeeper can see the camera recognised his stock — but no region is
        # promoted to a FACING and no region is compared against the stock
        # figure, because a facing is a position in a row and this frame has no
        # rows in it.
        #
        # AND NOTHING ON A FRAME THAT WAS NOT COUNTED MAY WEAR GREEN. Green is
        # a facing this counter stands behind; on a refused frame there are no
        # facings, so a region it recognised is moved back to the abstained
        # state — amber on the picture, amber in the list — and the name it
        # matched rides along as `name_seen` rather than as the region's name.
        # Drawing the recognised ones green here was this screen printing a
        # settled-looking box on a frame it had just refused to count.
        for r in rows:
            if r["state"] == "rejected":
                rejected.append(r)
                continue
            if r["state"] == "unnamed":
                unnamed.append(r)
                continue
            r.update({
                "state": "unnamed",
                "reason": "not_counted_frame_is_not_a_shelf",
                "detail": (f"This looks like {r['name']}, but it is not "
                           f"counted as a facing: " + LIMIT_NOT_A_SHELF),
                "name_seen": r["name"],
                "sku_id_seen": r["sku_id"],
                "sku_id": None,
                "name": None,
            })
            unnamed.append(r)
        unnamed.sort(key=lambda u: u["region"])

    facings: list[dict[str, Any]] = []
    for sku_id, g in groups.items():
        gap = _gap(sku_id, g["name"], g["facings"], by_sku.get(sku_id),
                   figures_ok)
        was = before.get(sku_id)
        facings.append({
            **g, "stock": gap,
            "previous_facings": was if previous else None,
            "change": (g["facings"] - was) if isinstance(was, int) else None,
            "new_here": bool(previous) and was is None,
        })
    facings.sort(key=lambda f: (-f["facings"], f["name"].lower()))

    missing = (_missing(up, groups, by_sku, figures_ok, previous)
               if abstained is None else [])
    gone = [m for m in missing if m["verdict"].startswith("was_here")]

    # Built AFTER the loop above, so an abstained frame draws the amber it was
    # just moved to rather than the green it was recognised as.
    drawn = [{"region": r["region"], "box": r["box"], "state": r["state"],
              "label": (f"{r['region']} {r['name']}" if r["state"] in ("named", "by_hand")
                        else f"{r['region']} not a product" if r["state"] == "rejected"
                        else f"? {r['region']}")}
             for r in rows]

    from . import detector as _det  # noqa: WPS433

    n_named = sum(f["facings"] for f in facings)
    by_hand = sum(f["by_hand"] for f in facings)
    exceeds = [f["sku_id"] for f in facings if f["stock"]["shelf_exceeds_figure"]]
    return {
        "ok": True,
        "settles_money": False,
        "mode": "shelf",
        "shelf_id": shelf_id,
        "label": label,
        "at": at,
        "frame_px": [int(fw), int(fh)],
        "counts": {
            "regions_seen": len(rows),
            "named": n_named,
            # SPLIT OUT, NEVER FOLDED IN. A facing the camera recognised and a
            # facing a person typed are both facings and are not the same kind
            # of evidence, so the tile that says how many were named says how
            # many of them the camera did not.
            "by_hand": by_hand,
            "unnamed": len(unnamed),
            "rejected": len(rejected),
            "same_packet": same_packet,
            "products": len(facings),
            "missing": len(missing),
            "gone": len(gone),
            "corrections": int(corrections),
            "shelf_exceeds_figure": len(exceeds),
        },
        "facings": facings,
        "unnamed": unnamed,
        "rejected": rejected,
        "missing": missing,
        "regions": [{"region": r["region"], "box": r["box"],
                     "state": r["state"], "sku_id": r["sku_id"],
                     "name": r["name"],
                     "crop_png_b64": r["crop_png_b64"]} for r in rows],
        "previous": previous and {
            "shelf_id": previous.get("shelf_id"), "at": previous.get("at"),
            "label": previous.get("label"), "same_shelf": previous.get("same_shelf"),
            "named": previous.get("named"), "products": previous.get("products"),
            "regions_seen": previous.get("regions_seen"),
            "facings": previous.get("facings") or [],
        },
        "stock_figures": figures,
        "annotated_png_b64": _draw_annotated(bgr, drawn) if annotate else None,
        "empty_shelf": len(rows) == 0,
        # A COUNT, OR AN ADMISSION — never a number with a caveat attached.
        # `counted` is false exactly when `abstained` is set, so a caller that
        # reads only one of the two cannot get a wrong answer from it.
        "counted": abstained is None,
        "abstained": abstained,
        "held_for_seconds": HELD_SECONDS,
        "limits": {
            "front_row_only": LIMIT_FRONT_ROW,
            "touching_packets": LIMIT_TOUCHING,
            "code_only_products": LIMIT_CODE_ONLY,
            "not_a_stock_count": LIMIT_NOT_A_COUNT,
            "not_a_shelf": LIMIT_NOT_A_SHELF,
            "missing_is_not_out_of_stock": LIMIT_MISSING,
            "comparison_needs_a_label": LIMIT_COMPARISON,
            "rejection_teaches_nothing": LIMIT_REJECTION,
        },
        "gates": {"theta": up.THETA, "phi": up.PHI,
                  "phi_appearance_only": up.PHI_APPEARANCE_ONLY},
        "detector": _det.describe(),
        "use_yolo": bool(use_yolo),
        "elapsed_ms": elapsed_ms,
        "note": (
            "Nothing here settles money and nothing here changes the stock "
            "figure. " + (LIMIT_FRONT_ROW if abstained is None
                          else abstained["detail"])),
    }


def _chain_fields(body: dict[str, Any]) -> dict[str, Any]:
    """The totals a chain line carries. Boxes and counts only — never pixels."""
    c = body["counts"]
    return {
        "regions_seen": c["regions_seen"],
        "named": c["named"],
        "by_hand": c["by_hand"],
        "unnamed": c["unnamed"],
        "rejected": c["rejected"],
        "products": c["products"],
        "missing": c["missing"],
        "facings": [{"sku_id": f["sku_id"], "facings": f["facings"],
                     "on_hand_units": f["stock"]["on_hand_units"],
                     "difference": f["stock"]["difference"]}
                    for f in body["facings"]],
    }


def read_shelf(raw: bytes, *, use_yolo: bool = True, annotate: bool = True,
               label: Any = None) -> dict[str, Any]:
    """One photograph of a shelf -> facings per product, and what was not named.

    Raises the till's own `UploadRefused` for anything the till would refuse
    (an empty gallery, bytes that are not an image) so the reason on the page
    is the reason the till would have given.
    """
    up = _till()
    t0 = time.perf_counter()
    bgr, _note = up.decode_upload(raw)

    items = _read_items(up, bgr, use_yolo=use_yolo)
    # The order regions are listed in is the order a shopkeeper reads a shelf:
    # left to right, then the next row down. Numbered that way, "? 3" on the
    # picture is the third box along.
    items.sort(key=lambda i: (_int_box(i.get("box"))[1] // 120,
                              _int_box(i.get("box"))[0]))

    shelf_id = "shf_" + secrets.token_hex(6)
    name = _label(label)
    previous = _previous_read(name)
    body = _assemble(
        up, bgr, items, shelf_id=shelf_id, label=name, use_yolo=use_yolo,
        annotate=annotate, overrides={}, previous=previous, at=_now_iso(),
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 2), corrections=0)
    _hold(shelf_id, raw, _lean(body["regions"]), items, label=name,
          use_yolo=use_yolo)

    head = _audit(
        EV_COUNT, shelf_id=shelf_id, label=name,
        frame_px=body["frame_px"],
        unnamed_boxes=[u["box"] for u in body["unnamed"]],
        use_yolo=bool(use_yolo),
        abstained=None if body["abstained"] is None else body["abstained"]["reason"],
        elapsed_ms=body["elapsed_ms"], **_chain_fields(body))
    body["audited"] = head is not None
    body["chain_head"] = head
    return body


# ---------------------------------------------------------------- teaching --


def _crop_png_for_teaching(raw: bytes, box: list[int]) -> bytes:
    """The region, padded, as PNG bytes for the till's own teaching path."""
    import cv2

    up = _till()
    bgr, _note = up.decode_upload(raw)
    h, w = bgr.shape[:2]
    x, y, bw, bh = box
    px = int(round(bw * TEACH_PAD_FRAC))
    py = int(round(bh * TEACH_PAD_FRAC))
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(w, x + bw + px), min(h, y + bh + py)
    crop = bgr[y0:y1, x0:x1]
    ok, buf = cv2.imencode(".png", crop)
    if not ok or crop.size == 0:
        raise ShelfRefused(
            R_BAD_REGION,
            "that region could not be cut out of the held frame. Nothing was "
            "taught.")
    return buf.tobytes()


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise ShelfRefused(
            R_BAD_BODY,
            'the body of this request is not JSON. It should look like '
            '{"region": 3, "sku_id": "maggi_70g", "name": "Maggi 70g", '
            '"price_rupees": "14.00"}.') from None
    if not isinstance(body, dict):
        raise ShelfRefused(
            R_BAD_BODY,
            f"the body of this request is a {type(body).__name__}; it must be "
            f"a JSON object.")
    return body


def _region_of(shelf_id: str, held: dict[str, Any],
               body: dict[str, Any]) -> dict[str, Any]:
    """The held record for the region this request names, or a named refusal."""
    region = body.get("region")
    if isinstance(region, bool) or not isinstance(region, int):
        raise ShelfRefused(
            R_BAD_REGION,
            f"region={region!r} is not a region number. The numbers are the "
            f"ones drawn on the picture, starting at 1.")
    rec = next((r for r in held["regions"] if r["region"] == region), None)
    if rec is None:
        have = [r["region"] for r in held["regions"]]
        raise ShelfRefused(
            R_BAD_REGION,
            f"region {region} is not on shelf read {shelf_id}. It has "
            f"{len(have)} region{'s' if len(have) != 1 else ''}"
            f"{': ' + ', '.join(str(n) for n in have) if have else ''}.")
    return rec


def _teach_the_crop(up: Any, held: dict[str, Any], rec: dict[str, Any],
                    body: dict[str, Any]) -> tuple[dict[str, Any], str, dict, Any, str]:
    """Put this region's pixels into the catalogue. The shared half of both
    teaching and correcting.

    Two paths, decided by whether the sku already has an appearance:

      - it does     -> `do_add_view`: another angle of a product the shop
                       already knows, through the same floor and collision
                       guard the PRODUCTS screen uses;
      - it does not -> `do_enrol_plain`: a new product, which needs a name and
                       a price, through the same collision guard.

    The browser names the product and the region. The pixels come from the
    frame this process holds, the vectors are derived here, and the price
    crosses the money boundary through the till's own `price_to_paise`, which
    refuses a float by name.
    """
    sku_id = up._valid_sku(str(body.get("sku_id") or ""))
    force = bool(body.get("force"))
    png = _crop_png_for_teaching(held["raw"], rec["box"])

    known = {r.sku_id: r.name for r in up.taught_skus()}
    if sku_id in known:
        res = up.do_add_view(png, sku_id, force=force)
        return res, "view_added", {
            "sku_id": res.get("sku_id"), "name": res.get("name"),
            "views_before": res.get("views_before"),
            "views_after": res.get("views_after"),
            "similarity_to_existing": res.get("similarity_to_existing"),
            "storage": res.get("storage"),
        }, None, str(res.get("name") or known[sku_id] or sku_id)

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ShelfRefused(
            R_NEED_NAME_AND_PRICE,
            f"{sku_id!r} is not a product this shop has taught, so this "
            f"would be a NEW product and it needs a name and a price. "
            f"Send name and price_rupees, or pick a product that already "
            f"exists to add this view to it.")
    if body.get("price_rupees") in (None, "") and \
            body.get("price_paise") in (None, ""):
        raise ShelfRefused(
            R_NEED_NAME_AND_PRICE,
            f"{sku_id!r} would be a new product and no price was sent. "
            f"Send price_rupees as a string, e.g. \"14.00\".")
    name = up._valid_name(name)
    price_paise = up.price_to_paise(rupees=body.get("price_rupees"),
                                    paise_value=body.get("price_paise"))
    res = up.do_enrol_plain(png, sku_id, name, price_paise, force=force)
    st = res.get("stored") or {}
    return res, "product_taught", {
        "sku_id": st.get("sku_id"), "name": st.get("name"),
        "n_views": st.get("n_views"),
        "replaced_existing": st.get("replaced_existing"),
        "storage": st.get("storage"),
    }, res.get("crop_png"), str(st.get("name") or name)


def _reassemble(shelf_id: str, held: dict[str, Any], *,
                annotate: bool = True) -> dict[str, Any]:
    """The read again, with everything the shopkeeper has said applied.

    THE BROWSER IS STILL NOT AN AUTHOR. A correction does not patch a row on a
    screen: it goes to the server, the server re-derives the facings, the gaps
    against the stock figure, what is missing and the picture with the boxes on
    it, and hands the whole reading back. Every figure the shopkeeper then
    reads was computed here, exactly as the first one was.
    """
    up = _till()
    t0 = time.perf_counter()
    bgr, _note = up.decode_upload(held["raw"])
    body = _assemble(
        up, bgr, held["items"], shelf_id=shelf_id, label=held.get("label"),
        use_yolo=bool(held.get("use_yolo", True)), annotate=annotate,
        overrides=held["overrides"],
        previous=_previous_read(held.get("label"), skip=shelf_id),
        at=_now_iso(),
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
        corrections=len(held["overrides"]))
    with _HELD_LOCK:
        held["regions"] = _lean(body["regions"])
    return body


def teach_from_shelf(shelf_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Teach one UNNAMED region of a held frame as a product.

    Correcting a region the counter DID name is a different act with a
    different chain line — see `correct_region` — because "it saw nothing here"
    and "it saw the wrong thing here" are different facts about this counter
    and collapsing them into one would lose the second.
    """
    up = _till()
    held = _held(shelf_id)
    rec = _region_of(shelf_id, held, body)
    region = int(rec["region"])
    already = rec.get("sku_id") or held["taught"].get(region)
    if already:
        raise ShelfRefused(
            R_REGION_NAMED,
            f"region {region} was already named {already!r}. Correct it "
            f"instead — POST to /shelf/{shelf_id}/correct with the same body "
            f"— which teaches this view to the product you name and records "
            f"that the counter had it wrong.")

    res, how, stored, crop_png, name = _teach_the_crop(up, held, rec, body)
    sku_id = str(stored.get("sku_id") or body.get("sku_id"))
    with _HELD_LOCK:
        held["taught"][region] = sku_id
        held["overrides"][region] = {"sku_id": sku_id, "name": name}
    read = _reassemble(shelf_id, held)
    head = _audit(EV_TAUGHT, shelf_id=shelf_id, region=region, sku_id=sku_id,
                  how=how, box=rec["box"], forced=bool(res.get("forced")),
                  **_chain_fields(read))
    read["audited"] = head is not None
    read["chain_head"] = head
    return {
        "ok": True,
        "settles_money": False,
        "shelf_id": shelf_id,
        "region": region,
        "sku_id": sku_id,
        "how": how,
        "stored": stored,
        "crop_png_b64": crop_png,
        "collision": res.get("collision"),
        "forced": bool(res.get("forced")),
        "audited": head is not None,
        "chain_head": head,
        # THE WHOLE READING COMES BACK, NOT A ROW. The old response said only
        # what had been stored, so the page struck the region off its own list
        # and the facing count on screen stayed one short of what the counter
        # now knew — a figure the browser had authored by subtraction.
        "read": read,
        "detail": (
            f"Region {region} is now a view of {sku_id!r} and is counted as a "
            f"facing of it — named by you, not by the camera, and marked that "
            f"way. Nothing about the stock figure changed."),
    }


def correct_region(shelf_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """The counter named a region and it named it wrong.

    THIS TEACHES. The crop goes into the catalogue as a view of the product the
    shopkeeper names, through exactly the path the Products screen uses, so the
    correction is not a note on a screen — the next photograph is read by a
    counter that has seen this packet from this angle. What it does NOT do is
    un-teach the wrong product: there is no way to tell this recogniser that a
    view is not something, and inventing one would be a promise the next read
    breaks.
    """
    up = _till()
    held = _held(shelf_id)
    rec = _region_of(shelf_id, held, body)
    region = int(rec["region"])
    if (held["overrides"].get(region) or {}).get("rejected"):
        raise ShelfRefused(
            R_ALREADY_REJECTED,
            f"region {region} was marked as not a product. Read the shelf "
            f"again to name it.")
    was = rec.get("sku_id")
    if not was:
        raise ShelfRefused(
            R_NOT_NAMED,
            f"region {region} was not named by the counter, so there is "
            f"nothing to correct. Teach it instead — POST to "
            f"/shelf/{shelf_id}/teach with the same body.")
    wanted = str(body.get("sku_id") or "").strip()
    if wanted and wanted == was:
        raise ShelfRefused(
            R_SAME_NAME,
            f"region {region} is already named {was!r}, so this correction "
            f"would change nothing. To add another view of it, use the "
            f"Products screen.")

    res, how, stored, crop_png, name = _teach_the_crop(up, held, rec, body)
    sku_id = str(stored.get("sku_id") or wanted)
    with _HELD_LOCK:
        held["taught"][region] = sku_id
        held["overrides"][region] = {"sku_id": sku_id, "name": name}
    read = _reassemble(shelf_id, held)
    head = _audit(EV_CORRECTED, shelf_id=shelf_id, region=region,
                  was=was, sku_id=sku_id, how=how, box=rec["box"],
                  forced=bool(res.get("forced")), **_chain_fields(read))
    read["audited"] = head is not None
    read["chain_head"] = head
    return {
        "ok": True,
        "settles_money": False,
        "shelf_id": shelf_id,
        "region": region,
        "was": was,
        "sku_id": sku_id,
        "how": how,
        "stored": stored,
        "crop_png_b64": crop_png,
        "collision": res.get("collision"),
        "forced": bool(res.get("forced")),
        "audited": head is not None,
        "chain_head": head,
        "read": read,
        "detail": (
            f"Region {region} said {was!r} and now says {sku_id!r}. This view "
            f"has been taught to {sku_id!r}, so the next read is made by a "
            f"counter that has seen it. The old name was not un-taught — "
            f"nothing here can do that — so if it keeps happening, teach "
            f"{sku_id!r} another view or two on the Products screen."),
    }


def reject_region(shelf_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """This region is not a product: a price label, a bracket, a hand.

    WHAT THIS DOES, EXACTLY, because the obvious guess is wrong. It removes the
    region from THIS read — the facings, the count and the picture — and writes
    a line on the chain saying a person struck it out. It does not teach the
    camera anything: nothing in this counter can learn that something is NOT a
    product from one example, and a button that implied it could would be
    contradicted by the very next photograph.
    """
    held = _held(shelf_id)
    rec = _region_of(shelf_id, held, body)
    region = int(rec["region"])
    struck = bool((held["overrides"].get(region) or {}).get("rejected"))
    # PUTTING IT BACK IS AS EASY AS STRIKING IT OUT, on purpose. This is a
    # control a shopkeeper presses on a phone with one thumb while holding the
    # shelf steady with the other, and an irreversible one gets pressed by
    # nobody. The undo is its own line on the chain, so the log shows both.
    undo = bool(body.get("undo"))
    if undo and not struck:
        raise ShelfRefused(
            R_BAD_REGION,
            f"region {region} is not struck out, so there is nothing to undo.")
    if struck and not undo:
        raise ShelfRefused(
            R_ALREADY_REJECTED,
            f"region {region} is already marked as not a product.")
    was = rec.get("sku_id")
    with _HELD_LOCK:
        if undo:
            # Back to whatever it was before the strike — which may be a name
            # the shopkeeper had already typed, not the camera's answer.
            restore = (held["overrides"].get(region) or {}).get("restore")
            if restore:
                held["overrides"][region] = restore
                held["taught"][region] = restore.get("sku_id")
            else:
                held["overrides"].pop(region, None)
        else:
            prev = held["overrides"].get(region)
            held["overrides"][region] = {"rejected": True, "restore": prev}
            held["taught"].pop(region, None)
    read = _reassemble(shelf_id, held)
    head = _audit(EV_REJECTED, shelf_id=shelf_id, region=region, was=was,
                  undone=undo, box=rec["box"], **_chain_fields(read))
    read["audited"] = head is not None
    read["chain_head"] = head
    return {
        "ok": True,
        "settles_money": False,
        "shelf_id": shelf_id,
        "region": region,
        "was": was,
        "undone": undo,
        "audited": head is not None,
        "chain_head": head,
        "read": read,
        "teaches_the_camera": False,
        "detail": (
            f"Region {region} is back in this read, counted as it was before."
            if undo else
            f"Region {region} is struck out of this read. " + LIMIT_REJECTION),
    }


# ------------------------------------------------------------------ input --


def _limit(raw: Any) -> int:
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise ShelfRefused(
            R_BAD_LIMIT,
            f"limit={raw!r} is not a whole number. Leave it out for "
            f"{DEFAULT_LIMIT}.") from None
    if want < 1:
        raise ShelfRefused(
            R_BAD_LIMIT, f"limit={want} asks for nothing; the smallest is 1.")
    if want > MAX_LIMIT:
        raise ShelfRefused(
            R_BAD_LIMIT,
            f"limit={want} is over the ceiling of {MAX_LIMIT}. Read "
            f"{SHELF_AUDIT_FILENAME} directly for more.")
    return want


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _known_labels(rows: list[dict[str, Any]], cap: int = 12) -> list[str]:
    """Every shelf name this counter has been given, most recently used first.

    Takes the replayed reads rather than fetching them: `/shelf` already has
    them, and parsing the chain a second time to collect a dozen strings
    doubled the cost of opening this page for nothing. MEASURED at 35 ms per
    parse on a 2000-line chain — about three months of a shop reading twenty
    shelves a day — against 400-600 ms for the read itself.
    """
    seen: list[str] = []
    for row in reversed(rows):
        name = _label(row.get("label"))
        if name and name not in seen:
            seen.append(name)
        if len(seen) >= cap:
            break
    return seen


def _taught_summary(up: Any) -> dict[str, Any]:
    """How many products can be counted by sight, and how many only by code."""
    try:
        by_sight = {r.sku_id for r in up.taught_skus()}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"by_sight": None, "by_code_only": None, "total": None,
                "problem": f"{type(exc).__name__}: {exc}"}
    try:
        priced = set(up.priced_skus())
    except Exception as exc:  # noqa: BLE001
        return {"by_sight": len(by_sight), "by_code_only": None,
                "total": None, "problem": f"{type(exc).__name__}: {exc}"}
    return {"by_sight": len(by_sight),
            "by_code_only": len(priced - by_sight),
            "total": len(priced | by_sight), "problem": None}


# ----------------------------------------------------------------- routes --


@router.get("/shelf")
def shelf_ep() -> JSONResponse:
    """What this counter can honestly say about counting a shelf.

    Never a refusal: an empty catalogue is a state to describe, not an error.
    The page opens on this, so it carries what the page needs to explain
    itself before a single photograph is taken.
    """
    try:
        up = _till()
        from . import detector as _det  # noqa: WPS433

        # ONE PARSE OF THE CHAIN, not two. The reads are replayed with their
        # corrections applied — the same list `/shelf/counts` serves — and the
        # shelf names are collected from it rather than by reading the file
        # again.
        reads, chain = reads_on_chain()
        last = reads[-1] if reads else None
        _by_sku, figures = _stock_figures()
        figures.pop("chain", None)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "module": MODULE,
            "taught": _taught_summary(up),
            "detector": _det.describe(),
            "gates": {"theta": up.THETA, "phi": up.PHI,
                      "phi_appearance_only": up.PHI_APPEARANCE_ONLY},
            "stock_figures": figures,
            "limits": {
                "front_row_only": LIMIT_FRONT_ROW,
                "touching_packets": LIMIT_TOUCHING,
                "code_only_products": LIMIT_CODE_ONLY,
                "not_a_stock_count": LIMIT_NOT_A_COUNT,
                "not_a_shelf": LIMIT_NOT_A_SHELF,
                "missing_is_not_out_of_stock": LIMIT_MISSING,
                "comparison_needs_a_label": LIMIT_COMPARISON,
                "rejection_teaches_nothing": LIMIT_REJECTION,
            },
            "reads_on_chain": len(reads),
            "last_read_at": None if last is None else last.get("at"),
            # The shelf names this counter has been given, newest first, so the
            # page can offer them instead of asking a shopkeeper to spell
            # "Aisle 2 top" the same way twice.
            "labels": _known_labels(reads),
            "held_reads": len(_HELD),
            "held_for_seconds": HELD_SECONDS,
            "chain": chain,
            "store_dir": str(shop_dir()),
            "counts_money": False,
            "writes_stock": False,
        })
    except ShelfRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/shelf/counts")
def shelf_counts_ep(limit: str | None = None) -> JSONResponse:
    """Earlier shelf reads, newest first, with their corrections applied."""
    try:
        want = _limit(limit)
        rows, chain = reads_on_chain()
        # frame_px is not replayed by `reads_on_chain` — it belongs to the
        # photograph, not to the count, so it is read off the count line here.
        events, _c = read_events()
        px = {str(e.get("shelf_id")): e.get("frame_px")
              for e in events if e.get("event") == EV_COUNT}
        reads = [{**r, "frame_px": px.get(str(r["shelf_id"]))} for r in rows]
        reads.reverse()
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": min(want, len(reads)),
            "matched": len(reads),
            "limit": want,
            "reads": reads[:want],
            "chain": chain,
        })
    except ShelfRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/shelf/count")
async def shelf_count_ep(request: Request) -> JSONResponse:
    """Count the facings on one photograph of a shelf.

    Multipart with an `image` file part, or JSON with `image` as base64 — the
    till's own `read_form` accepts both. `yolo=0` runs the contour proposer
    alone; `annotate=0` leaves the drawn frame out of the response.
    """
    up = None
    try:
        up = _till()
        form = await up.read_form(request)
        want_yolo = _flag(up.form_value(form, "yolo"), True)
        want_png = _flag(up.form_value(form, "annotate"), True)
        return JSONResponse(read_shelf(up.form_image(form), use_yolo=want_yolo,
                                       annotate=want_png,
                                       label=up.form_value(form, "label")))
    except ShelfRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        if up is not None and isinstance(exc, getattr(up, "UploadRefused", ())):
            return _refusal(ShelfRefused(exc.reason, exc.detail))
        return _crash(exc)


@router.post("/shelf/{shelf_id}/teach")
async def shelf_teach_ep(shelf_id: str, request: Request) -> JSONResponse:
    """Teach one unnamed region of a held shelf read as a product.

    Body: {"region": 3, "sku_id": "maggi_70g"} to add a view to a product the
    shop already knows; add "name" and "price_rupees" (a string) for a new
    one. "force": true skips the collision guard, exactly as PRODUCTS does.
    """
    up = None
    try:
        up = _till()
        body = await _json_body(request)
        return JSONResponse(teach_from_shelf(shelf_id, body))
    except ShelfRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        if up is not None and isinstance(exc, getattr(up, "UploadRefused", ())):
            return _refusal(ShelfRefused(exc.reason, exc.detail))
        return _crash(exc)


@router.post("/shelf/{shelf_id}/correct")
async def shelf_correct_ep(shelf_id: str, request: Request) -> JSONResponse:
    """The counter named a region wrong. Name it right, and teach it.

    Body: the same shape as /teach — {"region": 3, "sku_id": "maggi_70g"}, plus
    "name" and "price_rupees" if the right answer is a product the shop has
    never taught. The whole corrected reading comes back under "read".
    """
    up = None
    try:
        up = _till()
        body = await _json_body(request)
        return JSONResponse(correct_region(shelf_id, body))
    except ShelfRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        if up is not None and isinstance(exc, getattr(up, "UploadRefused", ())):
            return _refusal(ShelfRefused(exc.reason, exc.detail))
        return _crash(exc)


@router.post("/shelf/{shelf_id}/reject")
async def shelf_reject_ep(shelf_id: str, request: Request) -> JSONResponse:
    """This region is not a product at all. Body: {"region": 3}.

    Corrects this read and writes a line on the chain. It teaches the camera
    nothing, and the response says so under "teaches_the_camera".
    """
    try:
        body = await _json_body(request)
        return JSONResponse(reject_region(shelf_id, body))
    except ShelfRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "ShelfRefused", "audit_path", "correct_region", "forget_held",
    "read_events", "reads_on_chain", "read_shelf", "reject_region",
    "router", "shop_dir", "teach_from_shelf",
    "LIMIT_FRONT_ROW", "LIMIT_TOUCHING", "LIMIT_MISSING", "LIMIT_COMPARISON",
    "LIMIT_REJECTION", "HELD_FRAMES", "HELD_SECONDS", "SAME_PACKET_INSIDE",
]
