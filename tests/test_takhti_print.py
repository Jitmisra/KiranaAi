"""S-print acceptance: the PRINTED TAKHTI is metrically correct.

Everything the system measures is measured against a piece of paper.  A test
that only checks the generator's own arithmetic would be circular, so these
tests go the long way round: render the artwork to pixels, hand those pixels to
the REAL `gawaah.takhti.PlaneEngine`, and check what it sees against
`marker_centres_mm()`.  The PDF is checked the same way — its content stream is
parsed back out and re-rasterised, so the file the builder actually prints is
the file that gets detected, not a proxy for it.

Numbers reported in the summary at the bottom are produced by this run.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gawaah.sellevent import LineZone                                # noqa: E402
from gawaah.takhti import (                                          # noqa: E402
    MARKER_IDS, MARKER_MM, MAT_H_MM, MAT_W_MM, SCALE_PATCH_MM,
    PlaneEngine, marker_centres_mm,
)
from tools.make_takhti import (                                      # noqa: E402
    A4_SAFE_MARGIN_MM, A4_SCALE, DIAG_MM, MM_PER_INCH, PRINT_DPI, PRINT_TOL_MM,
    PT_PER_MM, SPAN_X_MM, SPAN_Y_MM, EXIT_Y_MM, Marker, Page, Rect, Text, Zone,
    build_a3_page, build_a4_page, build_pdf, build_verify_page, emit,
    expected_measurements, main, page_facts, png_get_dpi, png_set_dpi,
    render_page, text_width_em, write_png,
)

PPM = PRINT_DPI / MM_PER_INCH        # 11.811 px per mm at 300 DPI

# Every measurement below is asserted against this, and it is the number the
# verification sheet prints.  0.5 mm on the 366 mm span is 0.137 % of print
# scale -- roughly ten times tighter than a domestic printer's repeatability,
# which is the point: the artwork must not be what consumes the budget.
TOL_MM = PRINT_TOL_MM

# The raster's own floor.  A 300 DPI pixel is 84.7 um, and a rasterised edge
# can only land on a pixel boundary, so no feature in the PNG can be truer than
# about one pixel.  Tests of PRINTED FEATURE SIZE use this rather than a number
# picked to make them pass; the PDF has no such floor and is checked exactly.
RASTER_TOL_MM = 1.0 / PPM

_MEASURED: dict[str, float] = {}      # collected for the end-of-run summary


# --------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def a3_page() -> Page:
    return build_a3_page()


@pytest.fixture(scope="module")
def a4_page() -> Page:
    return build_a4_page()


@pytest.fixture(scope="module")
def a3_img(a3_page) -> np.ndarray:
    return render_page(a3_page, PRINT_DPI)


@pytest.fixture(scope="module")
def a4_img(a4_page) -> np.ndarray:
    return render_page(a4_page, PRINT_DPI)


@pytest.fixture(scope="module")
def engine() -> PlaneEngine:
    return PlaneEngine()


def _centres_px(eng: PlaneEngine, img: np.ndarray) -> dict[int, np.ndarray]:
    """Detected marker centres, frame pixels.  Fails loudly if any is missing."""
    corners, ids, _ = eng._det.detectMarkers(img)
    assert ids is not None, "no ArUco markers at all in the rendered sheet"
    flat = [int(i) for i in ids.flatten()]
    assert len(flat) == len(set(flat)), f"duplicate marker ids detected: {flat}"
    by = {i: c.reshape(4, 2) for i, c in zip(flat, corners)}
    missing = [i for i in MARKER_IDS if i not in by]
    assert not missing, f"missing markers {missing}, found {sorted(by)}"
    return by


# =================================================== ACCEPTANCE: the A3 sheet

def test_ACCEPTANCE_a3_png_locks_with_all_four_markers(a3_img, engine):
    """The printed sheet, rendered at 300 DPI, must satisfy the real mat lock."""
    lock = engine.detect(a3_img)
    assert lock.locked, lock.reason
    assert lock.ids_found == (0, 1, 2, 3)
    assert lock.reproj_rmse_px is not None and lock.reproj_rmse_px < 1.0
    _MEASURED["a3_scale_err_pct"] = lock.scale_err * 100.0
    _MEASURED["a3_reproj_rmse_px"] = lock.reproj_rmse_px


def test_ACCEPTANCE_a3_marker_spans_within_half_mm(a3_img, engine):
    """The whole artefact in one assertion: the distance between the printed
    marker centres, measured in pixels by the detector and converted with
    nothing but 300 DPI, must be the distance `marker_centres_mm()` promises."""
    by = _centres_px(engine, a3_img)
    ctr = {i: by[i].mean(axis=0) for i in MARKER_IDS}
    got = {
        "tl_tr": float(np.linalg.norm(ctr[1] - ctr[0])) / PPM,
        "tl_bl": float(np.linalg.norm(ctr[3] - ctr[0])) / PPM,
        "tl_br": float(np.linalg.norm(ctr[2] - ctr[0])) / PPM,
    }
    want = {"tl_tr": SPAN_X_MM, "tl_bl": SPAN_Y_MM, "tl_br": DIAG_MM}
    worst = 0.0
    for k in want:
        err = abs(got[k] - want[k])
        worst = max(worst, err)
        assert err < TOL_MM, (
            f"A3 {k}: printed {got[k]:.4f} mm, expected {want[k]:.4f} mm, "
            f"error {err:.4f} mm > {TOL_MM} mm"
        )
    _MEASURED["a3_worst_span_err_mm"] = worst


def test_a3_png_is_3508x4961_at_300_dpi(a3_img):
    assert a3_img.shape == (4961, 3508)
    # sanity: those pixel counts really are A3 at 300 DPI
    assert abs(3508 / PPM - MAT_W_MM) < 0.05
    assert abs(4961 / PPM - MAT_H_MM) < 0.05


def test_a3_marker_squares_measure_30mm(a3_img, engine):
    """MARKER_MM is 30.  At 300 DPI that is 354.33 px, which is not an integer,
    so the raster can only get within a fraction of a pixel.  Pin how close."""
    by = _centres_px(engine, a3_img)
    sides = []
    for i in MARKER_IDS:
        q = by[i]
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
            sides.append(float(np.linalg.norm(q[a] - q[b])) / PPM)
    worst = max(abs(s - MARKER_MM) for s in sides)
    assert worst < 0.10, f"marker side error {worst:.4f} mm; sides={sides}"
    _MEASURED["a3_worst_marker_side_err_mm"] = worst


# =================================================== ACCEPTANCE: the A4 sheet

def test_ACCEPTANCE_a4_png_locks_with_all_four_markers(a4_img, engine):
    lock = engine.detect(a4_img)
    assert lock.locked, lock.reason
    assert lock.ids_found == (0, 1, 2, 3)
    _MEASURED["a4_scale_err_pct"] = lock.scale_err * 100.0
    _MEASURED["a4_reproj_rmse_px"] = lock.reproj_rmse_px


def test_ACCEPTANCE_a4_marker_spans_are_two_thirds_within_half_mm(a4_img, engine):
    by = _centres_px(engine, a4_img)
    ctr = {i: by[i].mean(axis=0) for i in MARKER_IDS}
    got = {
        "tl_tr": float(np.linalg.norm(ctr[1] - ctr[0])) / PPM,
        "tl_bl": float(np.linalg.norm(ctr[3] - ctr[0])) / PPM,
        "tl_br": float(np.linalg.norm(ctr[2] - ctr[0])) / PPM,
    }
    want = {"tl_tr": SPAN_X_MM * A4_SCALE, "tl_bl": SPAN_Y_MM * A4_SCALE,
            "tl_br": DIAG_MM * A4_SCALE}
    worst = 0.0
    for k in want:
        err = abs(got[k] - want[k])
        worst = max(worst, err)
        assert err < TOL_MM, (
            f"A4 {k}: printed {got[k]:.4f} mm, expected {want[k]:.4f} mm, "
            f"error {err:.4f} mm"
        )
    _MEASURED["a4_worst_span_err_mm"] = worst


def test_a4_is_exactly_two_thirds_and_respects_the_printer_safe_margin(a4_page):
    f = page_facts(a4_page)
    assert A4_SCALE == pytest.approx(2.0 / 3.0, abs=0)
    assert f["marker_side_mm"] == pytest.approx(20.0, abs=1e-12)
    assert f["span_tl_tr_mm"] == pytest.approx(162.0, abs=1e-9)
    assert f["span_tl_bl_mm"] == pytest.approx(244.0, abs=1e-9)
    # the mat must sit inside the non-borderless printable area
    assert f["left_edge_to_marker_mm"] >= A4_SAFE_MARGIN_MM
    assert f["top_edge_to_marker_mm"] >= A4_SAFE_MARGIN_MM
    mat_w = MAT_W_MM * A4_SCALE
    mat_h = MAT_H_MM * A4_SCALE
    assert (210.0 - mat_w) / 2 == pytest.approx(6.0, abs=1e-9)
    assert (297.0 - mat_h) / 2 == pytest.approx(8.5, abs=1e-9)


def test_HONEST_LIMIT_a4_over_reports_size_by_one_and_a_half(a3_img, a4_img, engine):
    """The A4 fallback's one dangerous property, as an executable fact.

    The 20 mm scale patch is printed TRUE SIZE on both sheets, so it is a real
    physical object of known size sitting on each mat.  Rectify both and
    measure it in the buffer's millimetres:

        A3 -> 20 mm.  A4 -> 30 mm, because the A4 mat is 2/3 the size and the
        buffer has no way to know that.

    Anything consuming the A4 sheet must multiply by A4_SCALE.  If this test
    ever starts reporting 20 mm for A4, the buffer became scale-aware and the
    caveat printed on the sheet can be removed.
    """
    from gawaah.takhti import PX_PER_MM_X, PX_PER_MM_Y

    def patch_mm_in_buffer(img):
        lock = engine.detect(img)
        assert lock.locked, lock.reason
        rect = engine.rectify(img, lock.H)
        # the patch is the largest solid black blob in the buffer's upper half
        band = rect[: rect.shape[0] // 2]
        _, bw = cv2.threshold(band, 128, 255, cv2.THRESH_BINARY_INV)
        n, _lbl, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        best, best_fill = None, 0.0
        for k in range(1, n):
            x, y, w, h, area = stats[k]
            if w < 20 or h < 20:
                continue
            fill = area / float(w * h)
            if fill > 0.9 and abs(w / float(h) - 1.0) < 0.15 and area > best_fill:
                best, best_fill = (w, h), area
        assert best is not None, "scale patch not found in the rectified buffer"
        return best[0] / PX_PER_MM_X, best[1] / PX_PER_MM_Y

    a3_w, a3_h = patch_mm_in_buffer(a3_img)
    a4_w, a4_h = patch_mm_in_buffer(a4_img)

    assert a3_w == pytest.approx(SCALE_PATCH_MM, abs=0.4), a3_w
    assert a3_h == pytest.approx(SCALE_PATCH_MM, abs=0.4), a3_h
    assert a4_w == pytest.approx(SCALE_PATCH_MM / A4_SCALE, abs=0.6), a4_w
    assert a4_w * A4_SCALE == pytest.approx(SCALE_PATCH_MM, abs=0.4)
    _MEASURED["a3_patch_in_buffer_mm"] = a3_w
    _MEASURED["a4_patch_in_buffer_mm"] = a4_w


# ======================================= the sheet as a human-verifiable object

def _measure_black_box(img: np.ndarray, x_mm, y_mm, w_mm, h_mm,
                       pad_mm: float = 8.0) -> tuple[float, float]:
    """Measure the largest solid black rectangle near a known spot, in mm."""
    x0 = max(0, int((x_mm - pad_mm) * PPM))
    y0 = max(0, int((y_mm - pad_mm) * PPM))
    x1 = min(img.shape[1], int((x_mm + w_mm + pad_mm) * PPM))
    y1 = min(img.shape[0], int((y_mm + h_mm + pad_mm) * PPM))
    crop = img[y0:y1, x0:x1]
    _, bw = cv2.threshold(crop, 128, 255, cv2.THRESH_BINARY_INV)
    n, _l, stats, _c = cv2.connectedComponentsWithStats(bw, 8)
    best, best_area = None, 0
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if area > best_area:
            best, best_area = (w, h), area
    assert best is not None, "no ink found where a black box was expected"
    return best[0] / PPM, best[1] / PPM


@pytest.mark.parametrize("which", ["A3", "A4"])
def test_scale_patch_is_true_20mm_on_BOTH_sheets(which, a3_img, a4_img,
                                                 a3_page, a4_page):
    """The patch is a HUMAN instrument, so it is 20.00 mm on both sheets.

    An earlier draft scaled the whole A4 layout, which shrank this patch to
    13.33 mm while its own caption still read '20.00 mm'.  A caption that lies
    to the one person checking the print is worse than no caption.
    """
    page, img = (a3_page, a3_img) if which == "A3" else (a4_page, a4_img)
    pr = next(r for r in page.items if isinstance(r, Rect) and r.tag == "scale_patch")
    w, h = _measure_black_box(img, pr.x, pr.y, pr.w, pr.h)
    assert w == pytest.approx(SCALE_PATCH_MM, abs=RASTER_TOL_MM), \
        f"{which} patch width {w}"
    assert h == pytest.approx(SCALE_PATCH_MM, abs=RASTER_TOL_MM), \
        f"{which} patch height {h}"
    _MEASURED[f"{which.lower()}_patch_err_mm"] = max(abs(w - SCALE_PATCH_MM),
                                                     abs(h - SCALE_PATCH_MM))


@pytest.mark.parametrize("which", ["A3", "A4"])
def test_ruler_ticks_land_on_true_millimetres(which, a3_img, a4_img,
                                              a3_page, a4_page):
    """Read the printed ruler back off the pixels: the 10 mm ticks must be
    10.00 mm apart and the strip must be as long as its own caption claims."""
    page, img = (a3_page, a3_img) if which == "A3" else (a4_page, a4_img)
    hr = next(r for r in page.items if isinstance(r, Rect) and r.tag == "hruler")
    # sample a row where ONLY the 5.5 mm decade ticks reach
    row = int(round((hr.y - 4.8) * PPM))
    strip = img[row, int((hr.x - 5) * PPM): int((hr.x + hr.w + 5) * PPM)] < 128
    runs, i = [], 0
    while i < len(strip):
        if strip[i]:
            j = i
            while j < len(strip) and strip[j]:
                j += 1
            runs.append((i + j) / 2.0)      # pixel k covers [k, k+1)
            i = j
        else:
            i += 1
    n_expected = int(round(hr.w / 10.0)) + 1
    assert len(runs) == n_expected, (
        f"{which}: found {len(runs)} decade ticks, expected {n_expected}")
    span_mm = (runs[-1] - runs[0]) / PPM
    assert span_mm == pytest.approx(hr.w, abs=RASTER_TOL_MM), (
        f"{which} ruler measures {span_mm:.4f} mm, printed caption says {hr.w}")
    gaps = np.diff(runs) / PPM
    # a tick centre can be half a pixel out, so a gap between two of them can
    # be a whole pixel out; anything larger is a real placement bug
    assert gaps.max() == pytest.approx(10.0, abs=RASTER_TOL_MM), gaps
    assert gaps.min() == pytest.approx(10.0, abs=RASTER_TOL_MM), gaps
    _MEASURED[f"{which.lower()}_ruler_span_err_mm"] = abs(span_mm - hr.w)
    _MEASURED[f"{which.lower()}_ruler_worst_gap_err_mm"] = float(
        np.abs(gaps - 10.0).max())


@pytest.mark.parametrize("which", ["A3", "A4"])
def test_marker_quiet_zones_are_clean(which, a3_img, a4_img, a3_page, a4_page):
    """ArUco needs one clear marker CELL of white around each marker.

    This is the test that stops a future label, ruler or footer from being
    nudged into the one place on the sheet where ink breaks detection.
    """
    page, img = (a3_page, a3_img) if which == "A3" else (a4_page, a4_img)
    for mk in [i for i in page.items if isinstance(i, Marker)]:
        cell = mk.side_mm / 6.0                # DICT_4X4_50: 6 cells across
        half = mk.side_mm / 2.0
        ox0 = int(round((mk.cx - half - cell) * PPM))
        oy0 = int(round((mk.cy - half - cell) * PPM))
        ox1 = int(round((mk.cx + half + cell) * PPM))
        oy1 = int(round((mk.cy + half + cell) * PPM))
        assert ox0 >= 0 and oy0 >= 0 and ox1 <= img.shape[1] and oy1 <= img.shape[0], (
            f"{which} marker {mk.mid}: quiet zone runs off the sheet")
        outer = img[oy0:oy1, ox0:ox1].copy()
        ix0 = int(round((mk.cx - half) * PPM)) - ox0
        iy0 = int(round((mk.cy - half) * PPM)) - oy0
        ix1 = int(round((mk.cx + half) * PPM)) - ox0
        iy1 = int(round((mk.cy + half) * PPM)) - oy0
        outer[iy0:iy1, ix0:ix1] = 255          # blank the marker itself
        dark = int((outer < 200).sum())
        assert dark == 0, (
            f"{which} marker {mk.mid}: {dark} dark px inside its quiet zone")


@pytest.mark.parametrize("which", ["A3", "A4"])
def test_placement_zone_is_pure_white(which, a3_img, a4_img, a3_page, a4_page):
    """Invariant of the layout: the item-placement area carries no ink, so the
    segmenter never has to tell a printed tick apart from a packet of biscuits."""
    page, img = (a3_page, a3_img) if which == "A3" else (a4_page, a4_img)
    z = next(i for i in page.items if isinstance(i, Zone) and i.tag == "placement")
    assert z.w > 100 and z.h > 100, f"{which} placement zone too small: {z}"
    crop = img[int(round(z.y * PPM)):int(round((z.y + z.h) * PPM)),
               int(round(z.x * PPM)):int(round((z.x + z.w) * PPM))]
    assert crop.size > 0
    assert int(crop.min()) == 255, (
        f"{which}: {(crop < 255).sum()} non-white px inside the placement zone")
    _MEASURED[f"{which.lower()}_placement_zone_mm2"] = z.w * z.h


# ============================================ ties to the rest of the system

def test_printed_exit_line_is_the_line_sellevent_actually_uses(a3_page, a4_page):
    """The sheet's EXIT rule and LineZone's crossing predicate must be the same
    line.  If someone re-tunes `mat_exit_line`'s inset, this fails."""
    zone = LineZone.mat_exit_line()
    code_y = zone.p1[1]
    assert zone.p1[1] == zone.p2[1], "the exit line stopped being horizontal"
    assert code_y == pytest.approx(EXIT_Y_MM, abs=1e-9)
    assert page_facts(a3_page)["exit_line_from_top_mm"] == pytest.approx(
        code_y, abs=1e-9)
    # on A4 the mat is inset, so the sheet-edge distance is NOT code_y * 2/3
    a4_from_top = page_facts(a4_page)["exit_line_from_top_mm"]
    assert a4_from_top == pytest.approx(8.5 + code_y * A4_SCALE, abs=1e-9)
    assert a4_from_top != pytest.approx(code_y * A4_SCALE, abs=1e-6)


@pytest.mark.parametrize("which", ["A3", "A4"])
def test_marker_ids_are_in_the_right_corners(which, a3_img, a4_img, engine):
    """ids are TL,TR,BR,BL in that order.  Get this wrong and the mat is
    mirrored: the exit edge ends up on the shopkeeper's side and every sale is
    counted backwards, with no other symptom."""
    img = a3_img if which == "A3" else a4_img
    by = {i: q.mean(axis=0) for i, q in _centres_px(engine, img).items()}
    xs = {i: by[i][0] for i in MARKER_IDS}
    ys = {i: by[i][1] for i in MARKER_IDS}
    assert xs[0] < xs[1] and xs[3] < xs[2], f"{which}: left/right swapped"
    assert ys[0] < ys[3] and ys[1] < ys[2], f"{which}: top/bottom swapped"
    assert abs(ys[0] - ys[1]) < 2 and abs(ys[2] - ys[3]) < 2, f"{which}: not level"
    assert abs(xs[0] - xs[3]) < 2 and abs(xs[1] - xs[2]) < 2, f"{which}: not plumb"


@pytest.mark.parametrize("which", ["A3", "A4"])
def test_printed_exit_line_lands_on_its_stated_pixel_row(which, a3_img, a4_img,
                                                         a3_page, a4_page):
    """Measure the exit line off the pixels, not off the display list: find the
    longest horizontal ink run in the bottom band and check where it is."""
    page, img = (a3_page, a3_img) if which == "A3" else (a4_page, a4_img)
    want = page_facts(page)["exit_line_from_top_mm"]
    er = next(r for r in page.items if isinstance(r, Rect) and r.tag == "exit_line")
    band = img[int((want - 6) * PPM):int((want + 6) * PPM),
               int((er.x + 20) * PPM):int((er.x + er.w - 20) * PPM)]
    dark_per_row = (band < 128).sum(axis=1)
    assert dark_per_row.max() == band.shape[1], "the exit line is broken"
    rows = np.flatnonzero(dark_per_row == band.shape[1])
    # pixel k covers [k, k+1), so a run of rows r0..r1 is centred at (r0+r1+1)/2
    centre_mm = (int((want - 6) * PPM) + (rows[0] + rows[-1] + 1) / 2.0) / PPM
    assert centre_mm == pytest.approx(want, abs=RASTER_TOL_MM), (
        f"{which}: exit line printed at {centre_mm:.4f} mm, stated {want:.4f} mm")
    thick_mm = len(rows) / PPM
    assert thick_mm == pytest.approx(er.h, abs=RASTER_TOL_MM)
    _MEASURED[f"{which.lower()}_exit_line_err_mm"] = abs(centre_mm - want)


def test_marker_positions_come_from_takhti_not_from_a_copy(a3_page):
    got = np.array(page_facts(a3_page)["marker_centres_mm"])
    assert np.allclose(got, marker_centres_mm(), atol=0.0, rtol=0.0)


def test_verification_sheet_quotes_the_real_pages(a3_page, a4_page):
    """Every number on the print-verification sheet must be measured off the
    art, not restated.  This pins the fix for a bug that shipped in the first
    draft: the A4 column was computed as `A3 * 2/3`, which is right for lengths
    and silently wrong for anything referenced to the sheet edge."""
    exp = expected_measurements()
    assert exp["A3"] == page_facts(a3_page)
    assert exp["A4"] == page_facts(a4_page)

    a3, a4 = exp["A3"], exp["A4"]
    # lengths do scale...
    for k in ("marker_side_mm", "span_tl_tr_mm", "span_tl_bl_mm", "diag_tl_br_mm"):
        assert a4[k] == pytest.approx(a3[k] * A4_SCALE, rel=1e-12), k
    # ...sheet-referenced offsets do not, and true-size furniture does not
    assert a4["left_edge_to_marker_mm"] == pytest.approx(14.0, abs=1e-9)
    assert a4["top_edge_to_marker_mm"] == pytest.approx(16.5, abs=1e-9)
    assert a4["scale_patch_mm"] == a3["scale_patch_mm"] == SCALE_PATCH_MM
    for k in ("left_edge_to_marker_mm", "top_edge_to_marker_mm",
              "scale_patch_mm", "exit_line_from_top_mm"):
        assert a4[k] != pytest.approx(a3[k] * A4_SCALE, abs=1e-6), (
            f"{k} is being scaled naively again")


def test_verification_sheet_prints_every_expected_value(a3_page, a4_page):
    """Cross-check the rendered sheet's own text against the expectation table:
    for each row, both columns must appear on the page as printed strings."""
    ver = build_verify_page()
    printed = {t.text for t in ver.items if isinstance(t, Text)}
    exp = expected_measurements()
    for key in ("marker_side_mm", "span_tl_tr_mm", "span_tl_bl_mm",
                "diag_tl_br_mm", "scale_patch_mm", "ruler_strip_mm",
                "vruler_strip_mm", "left_edge_to_marker_mm",
                "top_edge_to_marker_mm", "exit_line_from_top_mm"):
        for col in ("A3", "A4"):
            s = f"{exp[col][key]:.2f}"
            assert s in printed, f"{col} {key} = {s} is not printed on the sheet"


def test_tolerance_is_stated_and_tight(a3_page):
    exp = expected_measurements()
    assert exp["tolerance_mm"] == TOL_MM
    pct = exp["tolerance_pct_of_long_span"]
    assert pct == pytest.approx(100.0 * TOL_MM / SPAN_Y_MM, rel=1e-12)
    assert pct < 0.2, f"print-scale tolerance loosened to {pct:.3f}%"


def test_all_printed_text_is_ascii(a3_page, a4_page):
    """cv2.putText draws Hershey (ASCII) and the PDF uses WinAnsi; a character
    outside plain ASCII would render as one thing on screen and another on
    paper.  So the artwork is restricted to ASCII by construction."""
    for page in (a3_page, a4_page, build_verify_page()):
        for t in [i for i in page.items if isinstance(i, Text)]:
            assert t.text.isascii(), f"{page.name}: non-ascii {t.text!r}"


def test_text_stays_on_the_sheet(a3_page, a4_page):
    """Anchor arithmetic, checked with the real Helvetica advance widths."""
    for page in (a3_page, a4_page, build_verify_page()):
        for t in [i for i in page.items if isinstance(i, Text)]:
            em = t.size_mm / 0.718
            w = text_width_em(t.text, t.bold) * em
            x = t.x - (w / 2 if t.anchor[0] == "c" else w if t.anchor[0] == "r" else 0)
            assert -0.01 <= x and x + w <= page.w_mm + 0.01, (
                f"{page.name}: {t.text!r} spans {x:.1f}..{x + w:.1f} mm "
                f"on a {page.w_mm} mm sheet")
            top = t.y if t.anchor[1] == "t" else (
                t.y - t.size_mm / 2 if t.anchor[1] == "m" else t.y - t.size_mm)
            assert -0.01 <= top and top + t.size_mm <= page.h_mm + 0.01, (
                f"{page.name}: {t.text!r} is off the top/bottom edge")


def test_helvetica_width_table_is_not_scrambled():
    assert text_width_em("", False) == 0.0
    # Helvetica digits are tabular: every digit is 556/1000
    assert len({text_width_em(d, False) for d in "0123456789"}) == 1
    assert text_width_em("0", False) == pytest.approx(0.556)
    assert text_width_em("W", False) > text_width_em("i", False)
    assert text_width_em("W", True) >= text_width_em("W", False)
    assert text_width_em("mm", False) == pytest.approx(2 * 0.833)


# ============================================================ the PNG on disk

def test_png_declares_its_true_physical_size(tmp_path, a3_img):
    """Without a pHYs chunk a print dialog has to guess the paper size, and
    'Actual size' becomes whatever the guess was."""
    p = tmp_path / "a3.png"
    raw = write_png(p, a3_img, PRINT_DPI)
    assert png_get_dpi(raw) == (PRINT_DPI, PRINT_DPI)
    assert png_get_dpi(p.read_bytes()) == (PRINT_DPI, PRINT_DPI)
    back = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    assert back.shape == a3_img.shape
    assert np.array_equal(back, a3_img), "PNG round trip is not lossless"


def test_png_dpi_chunk_is_replaced_not_duplicated(a3_img):
    ok, buf = cv2.imencode(".png", a3_img)
    assert ok
    once = png_set_dpi(buf.tobytes(), 300)
    twice = png_set_dpi(once, 600)
    assert once.count(b"pHYs") == 1
    assert twice.count(b"pHYs") == 1
    assert png_get_dpi(twice) == (600, 600)


def test_png_set_dpi_rejects_non_png():
    with pytest.raises(ValueError):
        png_set_dpi(b"not a png at all", 300)


# ================================================================ the PDF

def _pdf_tokens(s: bytes):
    """Tokenise a PDF content stream.  Real tokeniser, not a line splitter:
    the tests must not silently depend on how the writer happens to wrap."""
    i, n = 0, len(s)
    while i < n:
        c = s[i:i + 1]
        if c in b" \t\r\n":
            i += 1
        elif c == b"%":
            j = s.find(b"\n", i)
            j = n if j < 0 else j
            yield ("comment", s[i:j].decode("latin-1"))
            i = j
        elif c == b"(":
            depth, j = 1, i + 1
            while j < n and depth:
                ch = s[j:j + 1]
                if ch == b"\\":
                    j += 2
                    continue
                depth += (ch == b"(") - (ch == b")")
                j += 1
            yield ("str", s[i + 1:j - 1].decode("latin-1"))
            i = j
        elif c == b"/":
            j = i + 1
            while j < n and s[j:j + 1] not in b" \t\r\n/[]<>(%":
                j += 1
            yield ("name", s[i:j].decode("latin-1"))
            i = j
        else:
            j = i
            while j < n and s[j:j + 1] not in b" \t\r\n/[]<>(%":
                j += 1
            tok = s[i:j].decode("latin-1")
            i = max(j, i + 1)
            try:
                yield ("num", float(tok))
            except ValueError:
                yield ("op", tok)


class PdfPageOps:
    """Interprets the subset of PDF this generator emits, back into mm."""

    def __init__(self, stream: bytes, page_h_mm: float) -> None:
        self.rects: list[tuple[float, float, float, float]] = []
        self.polys: list[list[tuple[float, float]]] = []
        self.markers: list[dict] = []
        self.zones: list[dict] = []
        self.texts: list[str] = []
        self.ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        self._h = page_h_mm
        self._open_marker: dict | None = None
        self._run(stream)

    def _to_mm(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.ctm
        xp = a * x + c * y + e
        yp = b * x + d * y + f
        return xp / PT_PER_MM, self._h - yp / PT_PER_MM

    def _run(self, stream: bytes) -> None:
        st: list = []
        pend: list[tuple[float, float]] = []
        cur_rect = None
        for kind, val in _pdf_tokens(stream):
            if kind in ("num", "name", "str"):
                st.append(val)
                continue
            if kind == "comment":
                if val.startswith("% ENDMARKER"):
                    self._open_marker = None
                m = re.match(r"% MARKER id=(\S+) cx=(\S+) cy=(\S+) side=(\S+)", val)
                if m:
                    self._open_marker = {"id": int(m.group(1)),
                                         "cx": float(m.group(2)),
                                         "cy": float(m.group(3)),
                                         "side": float(m.group(4)),
                                         "rects": []}
                    self.markers.append(self._open_marker)
                m = re.match(r"% ZONE (\S+) x=(\S+) y=(\S+) w=(\S+) h=(\S+)", val)
                if m:
                    self.zones.append({"tag": m.group(1),
                                       "x": float(m.group(2)),
                                       "y": float(m.group(3)),
                                       "w": float(m.group(4)),
                                       "h": float(m.group(5))})
                continue
            op = val
            if op == "cm":
                self.ctm = tuple(float(v) for v in st[-6:])
            elif op == "re":
                x, y, w, h = (float(v) for v in st[-4:])
                # y-down CTM: the rect's mm-space top edge is at min of the corners
                (x0, ya), (x1, yb) = self._to_mm(x, y), self._to_mm(x + w, y + h)
                cur_rect = (min(x0, x1), min(ya, yb), abs(x1 - x0), abs(yb - ya))
            elif op in ("f", "F", "f*") and cur_rect is not None:
                self.rects.append(cur_rect)
                if self._open_marker is not None:
                    self._open_marker["rects"].append(cur_rect)
                cur_rect = None
            elif op == "S" and cur_rect is not None:
                cur_rect = None
            elif op == "m":
                pend = [self._to_mm(float(st[-2]), float(st[-1]))]
            elif op == "l":
                pend.append(self._to_mm(float(st[-2]), float(st[-1])))
            elif op in ("f", "f*", "h") and pend:
                if op != "h":
                    self.polys.append(pend)
                    pend = []
            elif op == "Tj" and st:
                self.texts.append(str(st[-1]))
            st = []
        return None


def _content_streams(pdf: bytes) -> list[bytes]:
    out = []
    for m in re.finditer(rb"stream\n(.*?)endstream", pdf, re.S):
        out.append(m.group(1))
    return out


def _mediaboxes(pdf: bytes) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in
            re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)]


@pytest.fixture(scope="module")
def a3_pdf(a3_page) -> bytes:
    return build_pdf([a3_page])


def test_pdf_structure_is_valid(a3_pdf):
    """A hand-written PDF is only worth having if it is actually well formed:
    every xref offset must land on its object header."""
    assert a3_pdf.startswith(b"%PDF-1.4")
    assert a3_pdf.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\s+(\d+)", a3_pdf).group(1))
    assert a3_pdf[start:start + 4] == b"xref"
    size = int(re.search(rb"/Size (\d+)", a3_pdf).group(1))
    body = a3_pdf[start:].split(b"trailer")[0].splitlines()
    entries = [ln for ln in body[2:] if ln.strip().endswith((b"n", b"f"))]
    assert len(entries) == size, f"xref has {len(entries)} entries, /Size {size}"
    for i, ln in enumerate(entries[1:], start=1):
        off = int(ln.split()[0])
        assert a3_pdf[off:off + len(f"{i} 0 obj")] == f"{i} 0 obj".encode(), (
            f"xref entry {i} points at {a3_pdf[off:off + 20]!r}")
    assert b"/Root 1 0 R" in a3_pdf
    assert b"/BaseFont /Helvetica" in a3_pdf


def test_pdf_mediabox_is_exactly_the_paper(a3_page, a4_page):
    (w, h), = _mediaboxes(build_pdf([a3_page]))
    assert w == pytest.approx(MAT_W_MM * PT_PER_MM, abs=0.01)   # 841.89 pt
    assert h == pytest.approx(MAT_H_MM * PT_PER_MM, abs=0.01)   # 1190.55 pt
    (w4, h4), = _mediaboxes(build_pdf([a4_page]))
    assert w4 == pytest.approx(210.0 * PT_PER_MM, abs=0.01)     # 595.28 pt
    assert h4 == pytest.approx(297.0 * PT_PER_MM, abs=0.01)     # 841.89 pt


def test_pack_pdf_has_three_pages_of_two_sizes(a3_page, a4_page):
    pdf = build_pdf([a3_page, a4_page, build_verify_page()])
    boxes = _mediaboxes(pdf)
    assert len(boxes) == 3
    assert b"/Count 3" in pdf
    assert boxes[0] != boxes[1] and boxes[1] == boxes[2]
    assert len(_content_streams(pdf)) == 3


def test_pdf_vector_markers_are_EXACT(a3_page, a3_pdf):
    """No quantisation at all in the vector path: the printed marker squares
    are 30.000000 mm centred on marker_centres_mm() to machine precision.

    This is why the PDF, not the PNG, is the thing to print.
    """
    ops = PdfPageOps(_content_streams(a3_pdf)[0], MAT_H_MM)
    assert len(ops.markers) == 4
    want = {int(i): c for i, c in zip(MARKER_IDS, marker_centres_mm())}
    for mk in ops.markers:
        rs = mk["rects"]
        assert rs, f"marker {mk['id']} emitted no ink"
        x0 = min(r[0] for r in rs)
        y0 = min(r[1] for r in rs)
        x1 = max(r[0] + r[2] for r in rs)
        y1 = max(r[1] + r[3] for r in rs)
        assert (x1 - x0) == pytest.approx(MARKER_MM, abs=1e-6)
        assert (y1 - y0) == pytest.approx(MARKER_MM, abs=1e-6)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        assert cx == pytest.approx(want[mk["id"]][0], abs=1e-6)
        assert cy == pytest.approx(want[mk["id"]][1], abs=1e-6)


def test_pdf_geometry_matches_the_display_list(a3_page, a3_pdf):
    """Both backends read the same display list; prove the PDF did not drift."""
    ops = PdfPageOps(_content_streams(a3_pdf)[0], MAT_H_MM)
    emitted = {(round(x, 4), round(y, 4), round(w, 4), round(h, 4))
               for x, y, w, h in ops.rects}
    for r in [i for i in a3_page.items if isinstance(i, Rect) and i.stroke_mm == 0]:
        key = (round(r.x, 4), round(r.y, 4), round(r.w, 4), round(r.h, 4))
        assert key in emitted, f"rect {key} in the layout is missing from the PDF"
    assert len(ops.zones) == 1 and ops.zones[0]["tag"] == "placement"
    assert "EXIT ->" in ops.texts


def _rasterise_pdf_page(ops: PdfPageOps, w_mm: float, h_mm: float,
                        dpi: int = PRINT_DPI) -> np.ndarray:
    """Re-rasterise the parsed vector page.  Text is skipped on purpose: no
    measurement is ever taken off a letterform, and glyph outlines are not in
    the content stream anyway."""
    ppm = dpi / MM_PER_INCH
    img = np.full((int(round(h_mm * ppm)), int(round(w_mm * ppm))), 255, np.uint8)
    for x, y, w, h in ops.rects:
        x0, y0 = int(round(x * ppm)), int(round(y * ppm))
        x1, y1 = int(round((x + w) * ppm)), int(round((y + h) * ppm))
        img[max(0, y0):max(y1, y0 + 1), max(0, x0):max(x1, x0 + 1)] = 0
    for p in ops.polys:
        cv2.fillPoly(img, [np.array([[int(round(a * ppm)), int(round(b * ppm))]
                                     for a, b in p], np.int32)], 0)
    return img


def test_ACCEPTANCE_the_pdf_that_gets_printed_detects_and_measures(a3_pdf, engine):
    """End to end on the PRINT artefact: parse the PDF's own content stream,
    re-rasterise it at 300 DPI, and hand that to the real PlaneEngine."""
    ops = PdfPageOps(_content_streams(a3_pdf)[0], MAT_H_MM)
    img = _rasterise_pdf_page(ops, MAT_W_MM, MAT_H_MM)
    lock = engine.detect(img)
    assert lock.locked, lock.reason
    assert lock.ids_found == (0, 1, 2, 3)

    by = _centres_px(engine, img)
    ctr = {i: by[i].mean(axis=0) for i in MARKER_IDS}
    worst = 0.0
    for k, a, b in (("tl_tr", 0, 1), ("tl_bl", 0, 3), ("tl_br", 0, 2)):
        want = {"tl_tr": SPAN_X_MM, "tl_bl": SPAN_Y_MM, "tl_br": DIAG_MM}[k]
        got = float(np.linalg.norm(ctr[b] - ctr[a])) / PPM
        worst = max(worst, abs(got - want))
        assert abs(got - want) < TOL_MM, f"PDF {k}: {got:.4f} vs {want:.4f} mm"
    _MEASURED["pdf_worst_span_err_mm"] = worst
    _MEASURED["pdf_scale_err_pct"] = lock.scale_err * 100.0


def test_pdf_a4_also_detects(a4_page, engine):
    ops = PdfPageOps(_content_streams(build_pdf([a4_page]))[0], 297.0)
    img = _rasterise_pdf_page(ops, 210.0, 297.0)
    lock = engine.detect(img)
    assert lock.locked, lock.reason
    assert lock.ids_found == (0, 1, 2, 3)


# ============================================================== the CLI + emit

@pytest.fixture(scope="module")
def emitted(tmp_path_factory) -> tuple[Path, dict]:
    d = tmp_path_factory.mktemp("takhti_out")
    return d, emit(d, PRINT_DPI)


def test_emit_writes_every_artefact(emitted):
    d, man = emitted
    for name in ("takhti_a3.png", "takhti_a4.png", "takhti_verify.png",
                 "takhti_a3.pdf", "takhti_a4.pdf", "takhti_verify.pdf",
                 "takhti_pack.pdf", "takhti_manifest.json"):
        assert (d / name).exists() and (d / name).stat().st_size > 0, name
    assert man["png_px"]["a3"] == [3508, 4961]
    assert man["png_px"]["a4"] == [2480, 3508]
    for blob in man["self_check"].values():
        assert blob["locked"], blob["reason"]
        assert blob["worst_abs_error_mm"] < TOL_MM
    loaded = json.loads((d / "takhti_manifest.json").read_text())
    assert loaded["expected"]["A3"]["span_tl_tr_mm"] == SPAN_X_MM


def test_emit_is_byte_deterministic(tmp_path):
    """No clock, no randomness: two runs must be identical, so a builder can
    prove the sheet in their hand is the sheet the repo describes."""
    a, b = tmp_path / "a", tmp_path / "b"
    ma, mb = emit(a, PRINT_DPI, self_check=False), emit(b, PRINT_DPI,
                                                        self_check=False)
    assert ma["files"] == mb["files"]
    for name in ma["files"]:
        h1 = hashlib.sha256((a / name).read_bytes()).hexdigest()
        h2 = hashlib.sha256((b / name).read_bytes()).hexdigest()
        assert h1 == h2 == ma["files"][name]["sha256"], name


def test_cli_main_runs(tmp_path, capsys):
    assert main(["--out", str(tmp_path / "cli")]) == 0
    out = capsys.readouterr().out
    assert "LOCKED" in out and "3508x4961" in out
    assert (tmp_path / "cli" / "takhti_pack.pdf").exists()


def test_page_facts_refuses_a_page_it_cannot_measure():
    bad = Page("bad", 10.0, 10.0, ())
    with pytest.raises(ValueError, match="missing markers"):
        page_facts(bad)


# ================================================================== summary

def test_zzz_report_measured_numbers(capsys):
    """Not an assertion: prints the numbers this run actually measured, so the
    report cannot contain a figure that was not produced by running code."""
    assert _MEASURED, "no measurements were collected"
    with capsys.disabled():
        print("\n  --- TAKHTI print metrology, measured this run ---")
        for k in sorted(_MEASURED):
            print(f"    {k:34s} {_MEASURED[k]:.5f}")


# ---------------------------------------------------------------------------
# Gates added after the D-day audit. The verifier found that scale_err was
# MEASURED three times and ASSERTED zero times, and that MAX_SCALE_ERR appeared
# nowhere in this file -- so a print that drifted past the 1.5% mat-lock gate
# would have sailed through CI and only failed on a real counter. Also adds the
# missing A4 marker-side check (A3 had one, A4 did not).
# ---------------------------------------------------------------------------

import pytest as _pytest
from gawaah.takhti import MAX_SCALE_ERR as _MAX_SCALE_ERR, MARKER_MM as _MARKER_MM
from gawaah.takhti import PlaneEngine as _PlaneEngine, PX_PER_MM as _PX_PER_MM
import numpy as _np
import cv2 as _cv2


def _lock_of(img):
    lock = _PlaneEngine().detect(img)
    assert lock.locked, f"generated artwork must lock: {lock.reason}"
    return lock


@_pytest.mark.parametrize("key", ["a3", "a4", "pdf"])
def test_GATE_generated_artwork_is_inside_the_mat_lock_scale_budget(key):
    """The gate that was measured but never enforced.

    MAX_SCALE_ERR is what PlaneEngine.detect() refuses on. If the generated
    artwork exceeds it, the printed mat cannot lock, and every millimetre
    downstream is wrong. Assert it rather than merely reporting it.
    """
    k = f"{key}_scale_err_pct"
    if k not in _MEASURED:
        _pytest.skip(f"{k} not measured in this run")
    frac = _MEASURED[k] / 100.0
    assert frac <= _MAX_SCALE_ERR, (
        f"{key}: scale error {frac:.4%} exceeds the mat-lock gate "
        f"{_MAX_SCALE_ERR:.2%} -- this artwork would refuse to lock"
    )


def test_GATE_a4_marker_squares_measure_30mm(a4_img):
    """A3 had this assertion; A4 did not. An A4 fallback printed at the wrong
    scale would have been silently accepted."""
    img = a4_img
    lock = _lock_of(img)
    det = _PlaneEngine()._det
    gray = img if img.ndim == 2 else _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
    corners, ids, _ = det.detectMarkers(gray)
    assert ids is not None and len(ids) == 4

    worst = 0.0
    for c in corners:
        q = _cv2.perspectiveTransform(
            c.reshape(-1, 1, 2).astype(_np.float64), lock.H).reshape(4, 2)
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
            side_mm = float(_np.linalg.norm(q[a] - q[b])) / _PX_PER_MM
            worst = max(worst, abs(side_mm - _MARKER_MM))
    _MEASURED["a4_worst_marker_side_err_mm"] = worst
    assert worst < 0.5, f"A4 marker side off by {worst:.3f}mm (budget 0.5mm)"
