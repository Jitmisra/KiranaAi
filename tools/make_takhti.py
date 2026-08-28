#!/usr/bin/env python3
"""THE PHYSICAL ARTEFACT — emit the printable TAKHTI at true scale.

This is the one thing the builder has to make with a printer, so it is the one
place where a rounding error becomes a physical, permanent, un-debuggable
millimetre. Everything downstream — the metric plane, the footprint tiebreak,
the exit-line crossing predicate, every rupee — is measured against this sheet.

WHAT IS EMITTED
    takhti_a3.png       3508 x 4961 px, 300 DPI, pHYs chunk written so a print
                        dialog reads the true physical size off the file
    takhti_a3.pdf       the same sheet as VECTOR art, MediaBox exactly A3
    takhti_a4.png/.pdf  the A4 fallback at exactly 2/3 scale
    takhti_verify.png/.pdf  the print-verification sheet: what a ruler must read
    takhti_pack.pdf     all three pages in one file
    takhti_manifest.json  every number on those sheets, plus a self-check that
                        actually ran PlaneEngine().detect() on the rendered PNG

ONE GEOMETRY, TWO BACKENDS
    The layout is built once as a display list in MILLIMETRES (`Page.items`).
    The raster backend rasterises it; the PDF backend emits the same numbers as
    vector operators.  So the PNG that the tests detect on and the PDF that the
    builder prints are the same geometry by construction, not by coincidence.
    Only the glyph shapes differ (Hershey vs Helvetica) — text is furniture,
    never metrology.

WHY 2/3 FOR A4
    A4 is 210 x 297 mm.  Leaving a 6 mm safe margin for a non-borderless
    printer gives 198 x 285 mm of printable area, and
        min(198/297, 285/420) = min(0.66667, 0.67857) = 2/3
    exactly.  Taking the round 2/3 rather than the ragged 0.678 buys: markers
    that are exactly 20.000 mm, marker spans that are exactly 162.0 x 244.0 mm,
    and an exact rational scale factor with no decimal rounding anywhere.

    LOUD CAVEAT, also printed on the sheet and re-stated in A4_SCALE's docstring:
    a physical object on the A4 mat rectifies into the SAME 840x1188 buffer, so
    the buffer reports its size in A3-mat millimetres, which are 1/(2/3) = 1.5x
    too large.  Any consumer of the A4 sheet MUST multiply measured mm by
    A4_SCALE.  Nothing in this file can enforce that; it is stated here, printed
    on the sheet, recorded in the manifest, and pinned by a test.

No new dependencies: cv2 + numpy for raster, stdlib zlib/struct for the PNG
pHYs chunk, and a hand-written PDF writer for vector output (PIL is not
installed in this venv — checked, not assumed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gawaah.takhti import (  # noqa: E402
    ARUCO_DICT,
    MARGIN_MM,
    MARKER_IDS,
    MARKER_MM,
    MAT_H_MM,
    MAT_W_MM,
    SCALE_PATCH_MM,
    marker_centres_mm,
)

# --------------------------------------------------------------------- units
MM_PER_INCH = 25.4
PRINT_DPI = 300
PT_PER_MM = 72.0 / MM_PER_INCH          # PDF user space is 1/72 inch
CAP_HEIGHT_EM = 0.718                   # Helvetica & Helvetica-Bold cap height

# --------------------------------------------------------------- sheet sizes
A3_W_MM, A3_H_MM = MAT_W_MM, MAT_H_MM   # the TAKHTI *is* an A3 sheet
A4_W_MM, A4_H_MM = 210.0, 297.0
A4_SAFE_MARGIN_MM = 6.0                 # non-borderless printer safe area

#: Exactly 2/3.  See the module docstring: measurements taken on an A4 mat are
#: reported by the rectified buffer in A3-mat mm and must be multiplied by this
#: to become physical mm.  The A4 sheet is a fallback, not a peer.
A4_SCALE = 2.0 / 3.0

# ------------------------------------------------- derived, never hand-typed
_CENTRES = marker_centres_mm()
SPAN_X_MM = float(np.linalg.norm(_CENTRES[1] - _CENTRES[0]))   # TL -> TR
SPAN_Y_MM = float(np.linalg.norm(_CENTRES[3] - _CENTRES[0]))   # TL -> BL
DIAG_MM = float(np.linalg.norm(_CENTRES[2] - _CENTRES[0]))     # TL -> BR

MAX_RULER_LEN_MM = 200.0                # trimmed to what fits between markers
MAX_VRULER_LEN_MM = 80.0

EXIT_INSET_MM = 18.0                    # == LineZone.mat_exit_line default
EXIT_Y_MM = MAT_H_MM - EXIT_INSET_MM    # 402.0

QUIET_MM = 6.0                          # ink kept this far off every marker (mat mm)

PRINT_TOL_MM = 0.5                      # ruler tolerance on the long span

# Cap heights, PHYSICAL millimetres on every sheet.
CAP_TITLE, CAP_SUB, CAP_LABEL, CAP_NOTE, CAP_SMALL = 7.0, 3.4, 3.0, 2.8, 2.6


# ===========================================================================
# display list — millimetres, origin top-left, y increasing downward
# ===========================================================================

@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle.  stroke_mm == 0 means filled.

    `tag` names a feature the verification sheet quotes a number for, so that
    number can be MEASURED off the art instead of restated by hand.
    """
    x: float
    y: float
    w: float
    h: float
    stroke_mm: float = 0.0
    tag: str = ""


@dataclass(frozen=True)
class Poly:
    """Filled polygon."""
    pts: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Text:
    """`size_mm` is CAP HEIGHT, not em size — a ruler can check a cap height.

    anchor is two characters: horizontal in 'lcr', vertical in 'tmb' where
    't' = top of capitals, 'm' = middle of capitals, 'b' = baseline.
    """
    x: float
    y: float
    text: str
    size_mm: float = 3.0
    bold: bool = False
    anchor: str = "lt"


@dataclass(frozen=True)
class Marker:
    """An ArUco marker, `side_mm` square, centred on (cx, cy)."""
    mid: int
    cx: float
    cy: float
    side_mm: float


@dataclass(frozen=True)
class Zone:
    """A NON-PRINTING annotation: the rectangle that must stay white.

    The placement segmenter treats any non-white blob inside the mat as a
    candidate object, so the layout owes it a guaranteed-clean area.  Making
    that a primitive rather than a comment means the guarantee is something a
    test can read off the page and enforce, instead of a promise in prose.
    """
    x: float
    y: float
    w: float
    h: float
    tag: str = "placement"


#: Everything the two backends can draw.  Deliberately tiny: every printed
#: feature that gets MEASURED is an axis-aligned rectangle, because a rectangle
#: rasterises and vectorises to the same edges in both backends without either
#: one's line-drawing conventions getting a vote.
Item = Rect | Poly | Text | Marker | Zone


@dataclass(frozen=True)
class Page:
    name: str
    w_mm: float
    h_mm: float
    items: tuple[Item, ...]


# ===========================================================================
# layout
# ===========================================================================

def _fmt(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}"


def _ruler(x0: float, y_base: float, length_mm: float,
           horizontal: bool = True, tag: str = "") -> list[Item]:
    """A printed ruler tick strip: 1 mm ticks, taller at 5, tallest+labelled at 10.

    Ticks are RECTANGLES, not strokes, so the raster and the PDF put ink in
    exactly the same place instead of relying on two different line-rasterising
    conventions agreeing about a half-pixel.
    """
    items: list[Item] = []
    n = int(round(length_mm))
    if horizontal:
        items.append(Rect(x0, y_base, length_mm, 0.4, tag=tag))
    else:
        items.append(Rect(x0, y_base, 0.4, length_mm, tag=tag))
    for i in range(n + 1):
        if i % 10 == 0:
            ln, w = 5.5, 0.5
        elif i % 5 == 0:
            ln, w = 3.5, 0.35
        else:
            ln, w = 2.0, 0.25
        if horizontal:
            x = x0 + i
            items.append(Rect(x - w / 2, y_base - ln, w, ln))
            if i % 10 == 0:
                items.append(Text(x, y_base + 1.6, str(i), 2.4, False, "ct"))
        else:
            y = y_base + i
            items.append(Rect(x0, y - w / 2, ln, w))
            if i % 10 == 0:
                items.append(Text(x0 + ln + 1.2, y, str(i), 2.2, False, "lm"))
    return items


def _corner_ticks(x0: float, y0: float, x1: float, y1: float,
                  arm: float = 9.0, w: float = 0.5) -> list[Item]:
    """Four L-shaped marks bracketing the item-placement area.

    Deliberately only at the corners, and deliberately drawn just OUTSIDE the
    rectangle rather than centred on its edge: ink inside that area would be a
    false blob for the placement segmenter, and a mark straddling the boundary
    would put ink in the very area the layout promises to keep white.
    """
    out: list[Item] = []
    for cx, sx in ((x0, 1), (x1, -1)):
        for cy, sy in ((y0, 1), (y1, -1)):
            hx = cx - w if sx > 0 else cx - arm
            hy = cy - w if sy > 0 else cy
            out.append(Rect(hx, hy, arm + w, w))
            vx = cx - w if sx > 0 else cx
            vy = cy - w if sy > 0 else cy - arm
            out.append(Rect(vx, vy, w, arm + w))
    return out


def _chevron_down(cx: float, cy: float, r: float = 4.0, t: float = 1.8) -> Poly:
    """A downward chevron — the OUT direction of the exit line."""
    return Poly((
        (cx - r, cy - r * 0.55),
        (cx, cy + r * 0.45),
        (cx + r, cy - r * 0.55),
        (cx + r, cy - r * 0.55 + t),
        (cx, cy + r * 0.45 + t),
        (cx - r, cy - r * 0.55 + t),
    ))


def _snap10(v: float, lo: float, hi: float) -> float:
    """Largest whole 10 mm that still fits, so a ruler always ends on a label."""
    return float(max(lo, min(hi, int(v // 10) * 10)))


def _mat_items(s: float, dx: float, dy: float, sheet_w: float, sheet_h: float,
               variant: str) -> list[Item]:
    """The mat, laid out directly in FINAL SHEET millimetres.

    TWO SIZE REGIMES, and mixing them up is the trap this function exists to
    avoid:

      * MAT GEOMETRY scales with `s` — marker centres, marker side, the exit
        line, the quiet zones.  It has to, because PlaneEngine fits a
        homography from the four centres to a fixed buffer, so the A4 sheet is
        only usable if it is geometrically SIMILAR to the A3 one.

      * HUMAN VERIFICATION FURNITURE does not scale — the ruler strip, the
        20 mm patch, every cap height.  An earlier draft scaled the whole sheet
        and produced an A4 mat whose ruler was captioned "200.0 mm" while
        physically measuring 133.3 mm, and a "20.00 mm SCALE PATCH" that was
        13.33 mm.  A ruler that is not true size is not a ruler, it is a trap
        laid for the one person trying to check the print.

    Vertical placement is therefore a top-down cursor rather than fixed
    constants: the bands are narrower on A4 but the type inside them is not.
    """
    cx = sheet_w / 2.0
    ms = MARKER_MM * s                       # marker side
    qz = QUIET_MM * s                        # keep ink this far off a marker
    m_top = dy + MARGIN_MM * s               # top markers' outer edge
    m_left = dx + MARGIN_MM * s
    m_right = dx + (MAT_W_MM - MARGIN_MM) * s
    m_bot_top = dy + (MAT_H_MM - MARGIN_MM - MARKER_MM) * s
    exit_y = dy + EXIT_Y_MM * s
    zone_x0 = m_left + ms + qz
    zone_x1 = m_right - ms - qz

    if variant == "A3":
        sheet_txt = f"A3  {_fmt(MAT_W_MM)} x {_fmt(MAT_H_MM)} mm"
        note = "full scale 1:1"
    else:
        sheet_txt = f"A4 FALLBACK  {_fmt(A4_W_MM)} x {_fmt(A4_H_MM)} mm"
        note = ("mat geometry 2/3 - ruler and patch below are TRUE SIZE")

    it: list[Item] = []

    # --- markers, straight from gawaah.takhti.marker_centres_mm() ------------
    for mid, (mx, my) in zip(MARKER_IDS, _CENTRES):
        it.append(Marker(int(mid), dx + float(mx) * s, dy + float(my) * s, ms))

    # --- masthead, stacked downward from the top marker row -----------------
    y = m_top + 1.0

    def stack(txt: str, cap: float, bold: bool = False, gap: float = 1.8) -> None:
        nonlocal y
        it.append(Text(cx, y, txt, cap, bold, "ct"))
        y += cap + gap

    stack("GAWAAH  TAKHTI", CAP_TITLE, True, 2.6)
    stack(sheet_txt, CAP_SUB)
    stack("PRINT AT 100% - DO NOT SCALE", CAP_SUB, True)
    stack(note, CAP_NOTE)
    masthead_end = y

    # --- marker id labels, outside the quiet zone ---------------------------
    id_y = m_top + ms + qz
    it.append(Text(m_left, id_y, "ID 0 (TL)", CAP_LABEL, False, "lt"))
    it.append(Text(m_right, id_y, "ID 1 (TR)", CAP_LABEL, False, "rt"))
    id_bot_y = m_bot_top - qz
    it.append(Text(m_left, id_bot_y, "ID 3 (BL)", CAP_LABEL, False, "lb"))
    it.append(Text(m_right, id_bot_y, "ID 2 (BR)", CAP_LABEL, False, "rb"))

    # --- 20 mm scale-verification patch, TRUE SIZE on every sheet -----------
    y = max(masthead_end, id_y + CAP_LABEL) + 4.0
    stack(f"{_fmt(SCALE_PATCH_MM, 2)} mm SCALE PATCH", CAP_LABEL, True, 2.2)
    it.append(Rect(cx - SCALE_PATCH_MM / 2.0, y, SCALE_PATCH_MM, SCALE_PATCH_MM,
                   tag="scale_patch"))
    y += SCALE_PATCH_MM + 2.5
    stack(f"every side must measure {_fmt(SCALE_PATCH_MM, 2)} mm", CAP_SMALL, False, 6.0)

    # --- horizontal ruler, TRUE SIZE ----------------------------------------
    hr_label_y = y
    ruler_len = _snap10(zone_x1 - zone_x0, 50.0, MAX_RULER_LEN_MM)
    stack(f"{_fmt(ruler_len)} mm RULER - check it with any ruler", CAP_NOTE, False, 7.0)
    base_y = y                               # ticks rise from here
    it += _ruler(cx - ruler_len / 2.0, base_y, ruler_len, True, "hruler")
    y = base_y + 1.6 + 2.4 + 3.0
    stack("PLACE ITEMS INSIDE THE MARKED AREA", CAP_LABEL, True, 3.0)
    zone_y0 = y

    # --- vertical ruler: printers scale x and y independently, check both ---
    vr_x = zone_x0 + 3.0
    vr_y0 = m_top + 2.0
    vruler_len = _snap10(hr_label_y - 4.0 - vr_y0, 30.0, MAX_VRULER_LEN_MM)
    it += _ruler(vr_x, vr_y0, vruler_len, False, "vruler")
    it.append(Text(vr_x, vr_y0 - 1.6, f"{_fmt(vruler_len)} mm", CAP_SMALL, False, "lb"))

    # --- bottom band, stacked UPWARD from the exit line ----------------------
    yb = exit_y - 4.5
    for txt, cap, bold in (
        (f"EXIT LINE  {_fmt(exit_y)} mm from the top edge of the sheet",
         CAP_LABEL, False),
        ("SELL EVENT: an item crossing this line AWAY FROM YOU is a sale",
         CAP_NOTE, False),
        (f"marker centre span {_fmt(SPAN_X_MM * s)} x {_fmt(SPAN_Y_MM * s)} mm"
         f"  -  check it against the verification sheet", CAP_SMALL, False),
        ("PRINT AT 100% - NO 'FIT TO PAGE' - NO MARGIN ADJUSTMENT",
         CAP_LABEL, True),
    ):
        it.append(Text(cx, yb, txt, cap, bold, "cb"))
        yb -= cap + 2.2
    zone_y1 = min(yb, id_bot_y - CAP_LABEL - 2.0) - 2.0

    it.append(Rect(zone_x0, exit_y - 0.4, zone_x1 - zone_x0, 0.8, tag="exit_line"))
    it.append(Text(cx, exit_y + 7.0, "EXIT ->", 5.5, True, "cm"))
    chev_dx = (zone_x1 - zone_x0) * 0.28
    it.append(_chevron_down(cx - chev_dx, exit_y + 5.5))
    it.append(_chevron_down(cx + chev_dx, exit_y + 5.5))

    # --- the clean item-placement area, marked only at its corners ----------
    if zone_y1 - zone_y0 < 60.0:             # pragma: no cover - layout guard
        raise ValueError(f"{variant}: placement zone collapsed to "
                         f"{zone_y1 - zone_y0:.1f} mm")
    it += _corner_ticks(zone_x0, zone_y0, zone_x1, zone_y1, arm=9.0 * s)
    it.append(Zone(zone_x0, zone_y0, zone_x1 - zone_x0, zone_y1 - zone_y0))
    return it


def build_a3_page() -> Page:
    """The TAKHTI itself: the mat is the A3 sheet, 1:1, no offset."""
    return Page("takhti_a3", A3_W_MM, A3_H_MM,
                tuple(_mat_items(1.0, 0.0, 0.0, A3_W_MM, A3_H_MM, "A3")))


def build_a4_page() -> Page:
    """The fallback: the same mat geometry at exactly 2/3, centred on A4.

    The 2/3 leaves (210 - 198)/2 = 6.0 mm left and right and
    (297 - 280)/2 = 8.5 mm top and bottom of printer safe margin.
    """
    w, h = MAT_W_MM * A4_SCALE, MAT_H_MM * A4_SCALE
    dx, dy = (A4_W_MM - w) / 2.0, (A4_H_MM - h) / 2.0
    if min(dx, dy) < A4_SAFE_MARGIN_MM - 1e-9:      # pragma: no cover
        raise ValueError(f"A4 scale {A4_SCALE} breaches the safe margin")
    return Page("takhti_a4", A4_W_MM, A4_H_MM,
                tuple(_mat_items(A4_SCALE, dx, dy, A4_W_MM, A4_H_MM, "A4")))


def page_facts(page: Page) -> dict:
    """MEASURE a built page.  Every number the builder is told to expect comes
    from here, so the verification sheet cannot drift away from the art.

    This is not decoration.  The first version of this file computed the A4
    column as `A3_value * 2/3`, which is right for lengths and WRONG for
    anything referenced to the sheet edge: the 2/3 mat is centred on A4, so it
    carries a 6.0 mm horizontal and 8.5 mm vertical offset that the naive
    formula silently dropped.  Reading the offsets back off the placed
    primitives makes that class of mistake unrepresentable.
    """
    mk = {m.mid: m for m in page.items if isinstance(m, Marker)}
    missing = [i for i in MARKER_IDS if i not in mk]
    if missing:
        raise ValueError(f"page {page.name} is missing markers {missing}")
    tagged: dict[str, Rect] = {r.tag: r for r in page.items
                               if isinstance(r, Rect) and r.tag}
    zones: dict[str, Zone] = {z.tag: z for z in page.items if isinstance(z, Zone)}
    c = {i: np.array([mk[i].cx, mk[i].cy]) for i in MARKER_IDS}
    side = mk[0].side_mm
    if any(abs(mk[i].side_mm - side) > 1e-9 for i in MARKER_IDS):
        raise ValueError(f"page {page.name} has unequal markers")
    return {
        "sheet_w_mm": page.w_mm,
        "sheet_h_mm": page.h_mm,
        "px_w": px(page.w_mm),
        "px_h": px(page.h_mm),
        "marker_side_mm": side,
        "span_tl_tr_mm": float(np.linalg.norm(c[1] - c[0])),
        "span_tl_bl_mm": float(np.linalg.norm(c[3] - c[0])),
        "diag_tl_br_mm": float(np.linalg.norm(c[2] - c[0])),
        "scale_patch_mm": tagged["scale_patch"].w,
        "ruler_strip_mm": tagged["hruler"].w,
        "vruler_strip_mm": tagged["vruler"].h,
        "left_edge_to_marker_mm": mk[0].cx - side / 2.0,
        "top_edge_to_marker_mm": mk[0].cy - side / 2.0,
        "exit_line_from_top_mm": tagged["exit_line"].y + tagged["exit_line"].h / 2.0,
        "placement_zone_mm": [zones["placement"].x, zones["placement"].y,
                              zones["placement"].w, zones["placement"].h],
        "marker_centres_mm": [[float(c[i][0]), float(c[i][1])] for i in MARKER_IDS],
    }


def expected_measurements() -> dict:
    """The full expectation table, measured off the real A3 and A4 pages."""
    return {
        "dpi": PRINT_DPI,
        "a4_scale": A4_SCALE,
        "a4_scale_exact": "2/3",
        "tolerance_mm": PRINT_TOL_MM,
        "tolerance_pct_of_long_span": 100.0 * PRINT_TOL_MM / SPAN_Y_MM,
        "A3": page_facts(build_a3_page()),
        "A4": page_facts(build_a4_page()),
    }


def build_verify_page() -> Page:
    """The print-verification sheet.

    Its whole job is to let a builder with a plastic ruler decide, before any
    tape comes out, whether the print is at true scale.  It therefore carries
    its own 100 mm calibration line: if that line is not 100 mm, nothing else
    on this sheet can be trusted either.
    """
    exp = expected_measurements()
    a3, a4 = exp["A3"], exp["A4"]
    it: list[Item] = []
    cx = A4_W_MM / 2.0
    x_lab, x_a3, x_a4, x_got = 16.0, 116.0, 148.0, 194.0

    it.append(Text(cx, 16.0, "TAKHTI PRINT VERIFICATION", 6.0, True, "ct"))
    it.append(Text(cx, 25.0,
                   "measure the printed sheet BEFORE taping it down", 3.0, False, "ct"))

    # self-calibrating 100 mm line
    it.append(Text(cx, 38.0, "STEP 1 - this line must be exactly 100.0 mm",
                   3.0, True, "ct"))
    y = 50.0
    it.append(Rect(cx - 50.0, y - 0.4, 100.0, 0.8, tag="cal_line"))
    for e in (cx - 50.0, cx + 50.0):
        it.append(Rect(e - 0.4, y - 4.0, 0.8, 8.0))
    it.append(Text(cx - 50.0, y - 5.5, "0", 2.6, False, "cb"))
    it.append(Text(cx + 50.0, y - 5.5, "100", 2.6, False, "cb"))
    it += _ruler(cx - 50.0, y + 15.0, 100.0, horizontal=True, tag="cal_ruler")

    # the table
    it.append(Text(x_lab, 82.0, "STEP 2 - check the printed TAKHTI", 3.4, True, "lt"))
    hdr = 90.0
    it.append(Text(x_lab, hdr, "MEASUREMENT", 2.8, True, "lt"))
    it.append(Text(x_a3, hdr, "A3", 2.8, True, "rt"))
    it.append(Text(x_a4, hdr, "A4 (2/3)", 2.8, True, "rt"))
    it.append(Text(x_got, hdr, "YOU MEASURED", 2.8, True, "rt"))
    it.append(Rect(x_lab, hdr + 4.2, x_got - x_lab, 0.4))

    rows = [
        ("marker square, each side", "marker_side_mm"),
        ("ID0 -> ID1 centre span (across)", "span_tl_tr_mm"),
        ("ID0 -> ID3 centre span (down)", "span_tl_bl_mm"),
        ("ID0 -> ID2 centre span (diagonal)", "diag_tl_br_mm"),
        ("black scale patch, each side", "scale_patch_mm"),
        ("horizontal ruler strip, end to end", "ruler_strip_mm"),
        ("vertical ruler strip, end to end", "vruler_strip_mm"),
        ("sheet LEFT edge to ID0 left edge", "left_edge_to_marker_mm"),
        ("sheet TOP edge to ID0 top edge", "top_edge_to_marker_mm"),
        ("sheet TOP edge to the EXIT line", "exit_line_from_top_mm"),
    ]
    yy = hdr + 10.0
    for label, key in rows:
        it.append(Text(x_lab, yy, label, 2.8, False, "lt"))
        it.append(Text(x_a3, yy, _fmt(a3[key], 2), 2.8, False, "rt"))
        it.append(Text(x_a4, yy, _fmt(a4[key], 2), 2.8, False, "rt"))
        it.append(Rect(x_got - 26.0, yy + 3.4, 26.0, 0.3))
        yy += 7.4
    it.append(Text(x_lab, yy - 2.0, "all values in millimetres", 2.4, False, "lt"))

    # tolerance + verdict
    yy += 6.0
    tol_pct = exp["tolerance_pct_of_long_span"]
    it.append(Text(x_lab, yy, "STEP 3 - the verdict", 3.4, True, "lt"))
    yy += 8.0
    lines = [
        f"PASS  if every measurement is within +/- {_fmt(PRINT_TOL_MM, 1)} mm "
        f"of the printed value.",
        f"      On the {_fmt(a3['span_tl_bl_mm'])} mm span that is a print-scale error "
        f"of {tol_pct:.3f} %.",
        "FAIL  if any value is out. Do NOT tape it down, do NOT calibrate around it.",
        "      Reprint with scaling set to 100% / 'Actual size' / 'None'. A driver",
        "      set to 'Fit to page' or 'Shrink oversized pages' is the usual cause.",
        "",
        "If your sheet disagrees with a whole column by one constant ratio you printed",
        f"the other variant: {1.0 / A4_SCALE:.3f} means A4 art read against the A3 "
        f"column, {A4_SCALE:.3f} the reverse.",
        "",
        "A4 CAVEAT: the A4 mat rectifies into the SAME buffer as the A3 mat, so every",
        f"size comes back {1.0 / A4_SCALE:.1f}x too large unless the system is told the "
        f"sheet is A4.",
        "Use A3 unless you have no A3 printer.",
    ]
    for ln in lines:
        if ln:
            it.append(Text(x_lab, yy, ln, 2.8, False, "lt"))
        yy += 5.0

    yy += 2.0
    it.append(Rect(x_lab, yy, x_got - x_lab, 22.0, stroke_mm=0.4))
    it.append(Text(x_lab + 3.0, yy + 5.0, "sheet printed on (printer / date):",
                   2.6, False, "lt"))
    it.append(Rect(x_lab + 3.0, yy + 12.0, 100.0, 0.3))
    it.append(Text(x_lab + 3.0, yy + 15.0, "verified by:", 2.6, False, "lt"))
    it.append(Rect(x_lab + 30.0, yy + 18.5, 60.0, 0.3))

    it.append(Text(cx, A4_H_MM - 12.0,
                   f"generated at {PRINT_DPI} DPI - A3 art is "
                   f"{a3['px_w']} x {a3['px_h']} px, A4 art is "
                   f"{a4['px_w']} x {a4['px_h']} px",
                   2.4, False, "cb"))
    return Page("takhti_verify", A4_W_MM, A4_H_MM, tuple(it))


# ===========================================================================
# raster backend
# ===========================================================================

def px(mm: float, dpi: int = PRINT_DPI) -> int:
    return int(round(mm * dpi / MM_PER_INCH))


ARUCO_CELLS = 6          # DICT_4X4_50: 4 data cells + a 1-cell black border
SNAP_TOL_PX = 0.5        # snap to whole cells only when it costs less than this


def _aruco_side_px(side_mm: float, ppm: float) -> int:
    """Marker side in whole pixels, snapped to whole ArUco cells ONLY when free.

    Two errors compete and they are not equally expensive:

      * side not a multiple of 6 -> OpenCV nearest-neighbour resizes the 6x6
        grid, so one or two cells come out a pixel wider than the rest.  This
        is a *cosmetic* asymmetry inside the marker; the outer boundary, which
        is what corner refinement and the scale check actually measure, stays
        exactly `side` px.
      * side snapped to a multiple of 6 -> the outer boundary itself moves, and
        the marker really is the wrong physical size.  That one lands straight
        in MatLock.scale_err.

    So snap only when the nearest multiple of 6 is within half a pixel of the
    true size.  At 300 DPI a 30 mm marker is 354.33 px and 354 = 6 x 59, so the
    A3 sheet gets perfectly square cells for 0.03 mm.  A 20 mm marker on the A4
    sheet is 236.22 px, whose nearest multiple of 6 is 234 -- a 0.19 mm shrink
    that alone would report a 0.94 % scale error, so it is refused and 236 px
    is used instead.
    """
    raw = side_mm * ppm
    exact = max(ARUCO_CELLS, int(round(raw)))
    snapped = max(1, int(round(raw / ARUCO_CELLS))) * ARUCO_CELLS
    return snapped if abs(snapped - raw) <= SNAP_TOL_PX else exact


def render_page(page: Page, dpi: int = PRINT_DPI) -> np.ndarray:
    """Rasterise a display list to an 8-bit grayscale image."""
    ppm = dpi / MM_PER_INCH
    w, h = px(page.w_mm, dpi), px(page.h_mm, dpi)
    img = np.full((h, w), 255, np.uint8)
    adict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)

    def rect_px(x, y, rw, rh):
        """Round the ORIGIN and the SIZE separately, not the two edges.

        Rounding both edges independently lets them round opposite ways, so a
        20.000 mm patch rasterises as 237 px (20.066 mm) instead of 236 px
        (19.981 mm) -- an error of a whole pixel in the one feature whose whole
        job is to be measured with a ruler.  Rounding the size makes every
        printed length wrong by at most half a pixel, and pays for it with at
        most half a pixel of position error, which nothing measures.
        """
        x0, y0 = int(round(x * ppm)), int(round(y * ppm))
        return (x0, y0,
                x0 + max(1, int(round(rw * ppm))),
                y0 + max(1, int(round(rh * ppm))))

    for it in page.items:
        if isinstance(it, Rect):
            if it.stroke_mm > 0:
                x0, y0, x1, y1 = rect_px(it.x, it.y, it.w, it.h)
                t = max(1, int(round(it.stroke_mm * ppm)))
                cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), 0, t)
            else:
                x0, y0, x1, y1 = rect_px(it.x, it.y, it.w, it.h)
                img[max(0, y0):y1, max(0, x0):x1] = 0
        elif isinstance(it, Poly):
            pts = np.array([[int(round(a * ppm)), int(round(b * ppm))]
                            for a, b in it.pts], np.int32)
            cv2.fillPoly(img, [pts], 0, cv2.LINE_AA)
        elif isinstance(it, Text):
            _draw_text(img, it, ppm)
        elif isinstance(it, Zone):
            continue                                 # annotation, never ink
        elif isinstance(it, Marker):
            side = _aruco_side_px(it.side_mm, ppm)
            m = cv2.aruco.generateImageMarker(adict, it.mid, side)
            # round the top-left corner, not the centre, so the realised centre
            # tracks the true centre for odd and even side lengths alike
            x0 = int(round(it.cx * ppm - side / 2.0))
            y0 = int(round(it.cy * ppm - side / 2.0))
            if x0 < 0 or y0 < 0 or x0 + side > w or y0 + side > h:
                raise ValueError(f"marker {it.mid} falls off the sheet")
            img[y0:y0 + side, x0:x0 + side] = m
        else:                                        # pragma: no cover
            raise TypeError(f"unknown item {it!r}")
    return img


def _draw_text(img: np.ndarray, t: Text, ppm: float) -> None:
    """Hershey text sized by CAP HEIGHT so it matches the PDF's cap height.

    Glyph shapes differ from the PDF's Helvetica; that is deliberate and
    harmless — no measurement is ever taken off a letterform.
    """
    font = cv2.FONT_HERSHEY_DUPLEX if t.bold else cv2.FONT_HERSHEY_SIMPLEX
    cap_px = t.size_mm * ppm
    thick = max(1, int(round(cap_px / 9.0)))
    (_, h1), _ = cv2.getTextSize("H", font, 1.0, thick)
    if h1 <= 0:                                      # pragma: no cover
        return
    scale = cap_px / h1
    (tw, _th), _ = cv2.getTextSize(t.text, font, scale, thick)

    ax, ay = t.anchor[0], t.anchor[1]
    x = t.x * ppm
    if ax == "c":
        x -= tw / 2.0
    elif ax == "r":
        x -= tw
    y = t.y * ppm
    if ay == "t":
        y += cap_px
    elif ay == "m":
        y += cap_px / 2.0
    cv2.putText(img, t.text, (int(round(x)), int(round(y))), font, scale, 0,
                thick, cv2.LINE_AA)


# ===========================================================================
# PNG carrying a true physical-size chunk
# ===========================================================================

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_set_dpi(raw: bytes, dpi: int) -> bytes:
    """Insert (or replace) the pHYs chunk so the file states its physical size.

    Without this a 3508 x 4961 PNG is just a big picture and every print dialog
    guesses.  With it the dialog reads 297 x 420 mm and 'Actual size' is
    actually actual size.
    """
    if not raw.startswith(_PNG_SIG):
        raise ValueError("not a PNG")
    ppm_int = int(round(dpi / MM_PER_INCH * 1000.0))     # pixels per metre
    phys = _png_chunk(b"pHYs", struct.pack(">IIB", ppm_int, ppm_int, 1))

    out = bytearray(_PNG_SIG)
    i = len(_PNG_SIG)
    inserted = False
    while i < len(raw):
        ln = struct.unpack(">I", raw[i:i + 4])[0]
        tag = raw[i + 4:i + 8]
        chunk = raw[i:i + 12 + ln]
        i += 12 + ln
        if tag == b"pHYs":
            continue                                    # drop any existing one
        out += chunk
        if tag == b"IHDR" and not inserted:
            out += phys
            inserted = True
    if not inserted:                                    # pragma: no cover
        raise ValueError("PNG had no IHDR")
    return bytes(out)


def png_get_dpi(raw: bytes) -> tuple[int, int] | None:
    """Read the pHYs chunk back as (x_dpi, y_dpi), or None if absent."""
    if not raw.startswith(_PNG_SIG):
        raise ValueError("not a PNG")
    i = len(_PNG_SIG)
    while i < len(raw):
        ln = struct.unpack(">I", raw[i:i + 4])[0]
        tag = raw[i + 4:i + 8]
        if tag == b"pHYs":
            xp, yp, unit = struct.unpack(">IIB", raw[i + 8:i + 8 + 9])
            if unit != 1:
                return None
            return (int(round(xp * MM_PER_INCH / 1000.0)),
                    int(round(yp * MM_PER_INCH / 1000.0)))
        i += 12 + ln
    return None


def write_png(path: Path, img: np.ndarray, dpi: int = PRINT_DPI) -> bytes:
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:                                          # pragma: no cover
        raise RuntimeError("PNG encode failed")
    raw = png_set_dpi(buf.tobytes(), dpi)
    path.write_bytes(raw)
    return raw


# ===========================================================================
# PDF backend — hand written, no dependency, MediaBox is the truth
# ===========================================================================

# Adobe standard widths, 1/1000 em, for chr(32)..chr(126).
_W_HELV = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    278, 278, 584, 584, 584, 556, 1015,
    667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    278, 278, 278, 469, 556, 333,
    556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
    334, 260, 334, 584,
)
_W_HELVB = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    333, 333, 584, 584, 584, 611, 975,
    722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    333, 278, 333, 584, 556, 333,
    556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
    389, 280, 389, 584,
)
assert len(_W_HELV) == len(_W_HELVB) == 95


def text_width_em(s: str, bold: bool) -> float:
    """String advance width in em units, for anchoring PDF text."""
    tbl = _W_HELVB if bold else _W_HELV
    tot = 0
    for ch in s:
        o = ord(ch)
        tot += tbl[o - 32] if 32 <= o <= 126 else 500
    return tot / 1000.0


def _n(v: float) -> str:
    """Coordinate, 4 dp.  In millimetres that is 0.1 um: below the resolution
    of any printer, any ruler, and the 300 DPI raster's own 84 um pixel."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _nm(v: float) -> str:
    """Transformation-matrix entry, 9 dp.

    The CTM multiplies EVERY coordinate on the page, so truncating it is not a
    rounding error, it is a scale error.  At 4 dp the mm-to-point factor
    2.834645669 becomes 2.8346, which is 1.6e-5 relative -- enough to turn a
    30.000 mm marker into 29.9995 mm and to bias the whole sheet.  This is the
    one place in the file where extra digits are not noise.
    """
    return f"{v:.9f}".rstrip("0").rstrip(".")


def _pdf_esc(s: str) -> bytes:
    out = s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("ascii", "replace")


def _marker_cells(mid: int) -> np.ndarray:
    """The 6x6 cell grid (True == black) of an ArUco marker.

    Rendering the dictionary at exactly one pixel per cell is the cheapest
    honest way to get the bit pattern; it also means the PDF's marker is
    definitionally the same marker OpenCV will look for.
    """
    adict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    m = cv2.aruco.generateImageMarker(adict, mid, 6)
    return m < 128


def page_content(page: Page) -> bytes:
    """Content stream, in millimetres with y down (the CTM does the flip).

    Left UNCOMPRESSED on purpose: this is a build artefact that a human or a
    test should be able to grep. The whole stream is a few tens of kilobytes.
    """
    k = PT_PER_MM
    hpt = page.h_mm * k
    out: list[bytes] = [
        f"% GAWAAH TAKHTI page {page.name} {_n(page.w_mm)}x{_n(page.h_mm)}mm".encode(),
        b"0 g 0 G",
        f"{_nm(k)} 0 0 {_nm(-k)} 0 {_nm(hpt)} cm".encode(),   # mm, y-down
    ]
    for it in page.items:
        if isinstance(it, Rect):
            if it.stroke_mm > 0:
                out.append(f"{_n(it.stroke_mm)} w {_n(it.x)} {_n(it.y)} "
                           f"{_n(it.w)} {_n(it.h)} re S".encode())
            else:
                out.append(f"{_n(it.x)} {_n(it.y)} {_n(it.w)} {_n(it.h)} "
                           f"re f".encode())
        elif isinstance(it, Poly):
            p = it.pts
            seg = [f"{_n(p[0][0])} {_n(p[0][1])} m"]
            seg += [f"{_n(a)} {_n(b)} l" for a, b in p[1:]]
            out.append((" ".join(seg) + " h f").encode())
        elif isinstance(it, Text):
            fs = it.size_mm / CAP_HEIGHT_EM              # em size, mm units
            x, y = it.x, it.y
            wmm = text_width_em(it.text, it.bold) * fs
            if it.anchor[0] == "c":
                x -= wmm / 2.0
            elif it.anchor[0] == "r":
                x -= wmm
            if it.anchor[1] == "t":
                y += it.size_mm
            elif it.anchor[1] == "m":
                y += it.size_mm / 2.0
            font = b"/F2" if it.bold else b"/F1"
            out.append(b"BT " + font + f" {_n(fs)} Tf 1 0 0 -1 {_n(x)} {_n(y)} "
                       f"Tm (".encode() + _pdf_esc(it.text) + b") Tj ET")
        elif isinstance(it, Zone):
            out.append(f"% ZONE {it.tag} x={_n(it.x)} y={_n(it.y)} "
                       f"w={_n(it.w)} h={_n(it.h)}".encode())
        elif isinstance(it, Marker):
            out.append(f"% MARKER id={it.mid} cx={_n(it.cx)} cy={_n(it.cy)} "
                       f"side={_n(it.side_mm)}".encode())
            cell = it.side_mm / 6.0
            x0 = it.cx - it.side_mm / 2.0
            y0 = it.cy - it.side_mm / 2.0
            bits = _marker_cells(it.mid)
            for r in range(6):
                # merge horizontal runs of black cells into one rect: fewer
                # operators, and no hairline seam between abutting fills
                c = 0
                while c < 6:
                    if not bits[r, c]:
                        c += 1
                        continue
                    c2 = c
                    while c2 < 6 and bits[r, c2]:
                        c2 += 1
                    out.append(
                        f"{_n(x0 + c * cell)} {_n(y0 + r * cell)} "
                        f"{_n((c2 - c) * cell)} {_n(cell)} re f".encode())
                    c = c2
            out.append(b"% ENDMARKER")
        else:                                            # pragma: no cover
            raise TypeError(f"unknown item {it!r}")
    return b"\n".join(out) + b"\n"


def build_pdf(pages: list[Page]) -> bytes:
    """A minimal, valid, byte-deterministic PDF 1.4 with a real xref table."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    n_pages = len(pages)
    # fixed numbering: 1 catalog, 2 page-tree, 3/4 fonts, then page+content pairs
    catalog_no, tree_no, f1_no, f2_no = 1, 2, 3, 4
    page_nos = [5 + 2 * i for i in range(n_pages)]
    cont_nos = [6 + 2 * i for i in range(n_pages)]

    add(f"<< /Type /Catalog /Pages {tree_no} 0 R >>".encode())
    kids = " ".join(f"{p} 0 R" for p in page_nos)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>")
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
        b"/Encoding /WinAnsiEncoding >>")

    for pg, pno, cno in zip(pages, page_nos, cont_nos):
        wpt, hpt = pg.w_mm * PT_PER_MM, pg.h_mm * PT_PER_MM
        add(f"<< /Type /Page /Parent {tree_no} 0 R "
            f"/MediaBox [0 0 {_n(wpt)} {_n(hpt)}] "
            f"/Resources << /Font << /F1 {f1_no} 0 R /F2 {f2_no} 0 R >> >> "
            f"/Contents {cno} 0 R >>".encode())
        body = page_content(pg)
        add(f"<< /Length {len(body)} >>\nstream\n".encode() + body + b"endstream")
        assert len(objs) == cno

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog_no} 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(buf)


def write_pdf(path: Path, pages: list[Page]) -> bytes:
    raw = build_pdf(pages)
    path.write_bytes(raw)
    return raw


# ===========================================================================
# self-check + emit
# ===========================================================================

def detect_self_check(img: np.ndarray, scale: float, dpi: int = PRINT_DPI) -> dict:
    """Run the REAL PlaneEngine on the rendered art and measure what it sees.

    Nothing here is asserted — the numbers are recorded so the manifest states
    measured error rather than intended error.  The tests do the asserting.
    """
    from gawaah.takhti import PlaneEngine

    eng = PlaneEngine()
    lock = eng.detect(img)
    ppm = dpi / MM_PER_INCH
    res: dict = {
        "locked": bool(lock.locked),
        "reason": lock.reason,
        "ids_found": list(lock.ids_found),
        "scale_err": None if lock.scale_err is None else float(lock.scale_err),
        "reproj_rmse_px": (None if lock.reproj_rmse_px is None
                           else float(lock.reproj_rmse_px)),
    }
    corners, ids, _ = eng._det.detectMarkers(img)
    if ids is None:
        return res
    by = {int(i): c.reshape(4, 2).mean(axis=0) for i, c in zip(ids.flatten(), corners)}
    if not set(MARKER_IDS).issubset(by):
        return res
    meas = {
        "span_tl_tr_mm": float(np.linalg.norm(by[1] - by[0])) / ppm,
        "span_tl_bl_mm": float(np.linalg.norm(by[3] - by[0])) / ppm,
        "diag_tl_br_mm": float(np.linalg.norm(by[2] - by[0])) / ppm,
    }
    want = {"span_tl_tr_mm": SPAN_X_MM * scale,
            "span_tl_bl_mm": SPAN_Y_MM * scale,
            "diag_tl_br_mm": DIAG_MM * scale}
    res["measured_mm"] = meas
    res["expected_mm"] = want
    res["error_mm"] = {k: meas[k] - want[k] for k in want}
    res["worst_abs_error_mm"] = max(abs(v) for v in res["error_mm"].values())
    return res


def emit(outdir: Path, dpi: int = PRINT_DPI, self_check: bool = True) -> dict:
    """Write every artefact and return the manifest.  Deterministic: no clock,
    no timestamp, no randomness — the same source produces the same bytes."""
    outdir.mkdir(parents=True, exist_ok=True)
    a3, a4, ver = build_a3_page(), build_a4_page(), build_verify_page()

    files: dict[str, dict] = {}

    def record(name: str, raw: bytes) -> None:
        files[name] = {"bytes": len(raw),
                       "sha256": hashlib.sha256(raw).hexdigest()}

    img_a3 = render_page(a3, dpi)
    img_a4 = render_page(a4, dpi)
    img_ver = render_page(ver, dpi)
    record("takhti_a3.png", write_png(outdir / "takhti_a3.png", img_a3, dpi))
    record("takhti_a4.png", write_png(outdir / "takhti_a4.png", img_a4, dpi))
    record("takhti_verify.png", write_png(outdir / "takhti_verify.png", img_ver, dpi))
    record("takhti_a3.pdf", write_pdf(outdir / "takhti_a3.pdf", [a3]))
    record("takhti_a4.pdf", write_pdf(outdir / "takhti_a4.pdf", [a4]))
    record("takhti_verify.pdf", write_pdf(outdir / "takhti_verify.pdf", [ver]))
    record("takhti_pack.pdf", write_pdf(outdir / "takhti_pack.pdf", [a3, a4, ver]))

    manifest = {
        "artefact": "GAWAAH TAKHTI",
        "deterministic": True,
        "expected": expected_measurements(),
        "png_px": {"a3": list(img_a3.shape[::-1]), "a4": list(img_a4.shape[::-1]),
                   "verify": list(img_ver.shape[::-1])},
        "files": files,
    }
    if self_check:
        manifest["self_check"] = {
            "a3": detect_self_check(img_a3, 1.0, dpi),
            "a4": detect_self_check(img_a4, A4_SCALE, dpi),
        }
    raw = json.dumps(manifest, indent=2, sort_keys=True).encode()
    (outdir / "takhti_manifest.json").write_bytes(raw)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="emit the printable TAKHTI")
    ap.add_argument("--out", type=Path, default=_ROOT / "build" / "takhti")
    ap.add_argument("--dpi", type=int, default=PRINT_DPI)
    ap.add_argument("--no-self-check", action="store_true")
    a = ap.parse_args(argv)

    man = emit(a.out, a.dpi, self_check=not a.no_self_check)
    exp = man["expected"]
    print(f"TAKHTI -> {a.out}")
    print(f"  A3 {exp['A3']['px_w']}x{exp['A3']['px_h']} px @ {a.dpi} DPI  "
          f"({_fmt(A3_W_MM)} x {_fmt(A3_H_MM)} mm)")
    print(f"  A4 {exp['A4']['px_w']}x{exp['A4']['px_h']} px @ {a.dpi} DPI  "
          f"({_fmt(A4_W_MM)} x {_fmt(A4_H_MM)} mm, scale 2/3)")
    print(f"  marker centre spans: A3 {_fmt(SPAN_X_MM, 2)} x {_fmt(SPAN_Y_MM, 2)} mm"
          f" | A4 {_fmt(SPAN_X_MM * A4_SCALE, 2)} x {_fmt(SPAN_Y_MM * A4_SCALE, 2)} mm")
    for name, blob in man.get("self_check", {}).items():
        if blob.get("locked"):
            print(f"  self-check {name}: LOCKED ids={blob['ids_found']} "
                  f"worst span error {blob['worst_abs_error_mm'] * 1000:.1f} um "
                  f"({blob['worst_abs_error_mm']:.4f} mm), "
                  f"scale_err {blob['scale_err']:.4%}")
        else:
            print(f"  self-check {name}: NOT LOCKED - {blob['reason']}")
    for n, meta in sorted(man["files"].items()):
        print(f"  {n:22s} {meta['bytes']:>9,d} B  {meta['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
