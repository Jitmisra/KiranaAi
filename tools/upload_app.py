"""Drop an image in, see what GAWAAH actually measures.

    ./.venv/bin/python tools/upload_app.py             # -> http://127.0.0.1:8790
    ./.venv/bin/python tools/upload_app.py --port 9000

No camera, no printed mat, no phone. Upload a photograph (or press SAMPLE) and
this runs the REAL pipeline on it -- gawaah.takhti.PlaneEngine for the mat lock
and gawaah.placement.PlacementDetector for the objects -- then draws what it
found and reports every measurement in millimetres.

It is deliberately the same code the counter runs. If it refuses here, it would
refuse there, for the same named reason. Three things this tool is careful
about, because they are the difference between a demo and evidence:

  TRUTH.   The SAMPLE builds a scene whose object sizes are KNOWN, so the page
           prints measured-vs-truth error per item. A number you can check beats
           a number you can admire.

  REASONS. A failed mat lock is the common real failure, and the message is the
           product. Every refusal keeps its named reason and adds how many of
           the four markers were seen, which corners are missing, and what to
           physically change. Nothing is guessed to paper over a refusal.

  LABELS.  The sample is synthetic and says so on screen, in the JSON, and on
           every image it draws (INVARIANT 7). No result from this tool is money;
           it cannot mark anything GREEN and never talks to the settlement path.

INVARIANT 4 is honoured for uploads: the uploaded bytes are decoded, measured
and dropped. They are never stored and never echoed back. Only the rectified
840x1188 metric buffer leaves this process.
"""
from __future__ import annotations

import argparse
import base64
import os
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah.identity import (  # noqa: E402
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    Gallery,
    Identifier,
    IdentityError,
)
from gawaah.money import MoneyError, from_rupees_str, paise  # noqa: E402
from gawaah.placement import (  # noqa: E402
    MIN_AREA_MM2,
    REASON_BORDER,
    REASON_MERGED,
    PlacementDetector,
    PlacementError,
)
from gawaah.takhti import (  # noqa: E402
    BUF_H,
    BUF_W,
    MARKER_IDS,
    MARKER_MM,
    MAT_H_MM,
    MAT_W_MM,
    MAX_PERSP_INDEX,
    MAX_SCALE_ERR,
    PX_PER_MM_X,
    PX_PER_MM_Y,
    PlaneEngine,
    render_takhti,
)

DEFAULT_PORT = 8790

# A phone photo is 4000 px wide and the metric buffer is 840. Anything above
# this adds latency and no millimetres, so the long side is capped and the fact
# is reported rather than hidden.
MAX_SIDE_PX = 2600
MAX_UPLOAD_BYTES = 48 * 1024 * 1024

# Named refusals. Every one of these is a state this tool can honestly reach,
# and each is rendered on screen with its own name. None of them is a guess.
R_EMPTY_BODY = "upload_empty_body"
R_TOO_LARGE = "upload_too_large"
R_NOT_AN_IMAGE = "upload_not_an_image"
R_UNSUPPORTED = "upload_unsupported_format"
R_DEGENERATE = "upload_degenerate_image"
R_NOT_RECTIFIED = "placement_buffer_mismatch"
R_INTERNAL = "upload_internal_error"

# Enrolment / recognition refusals. Same rule: every one is a state this tool
# can honestly reach, and each is named so the page can say what to DO about it.
R_NO_EMBEDDER = "embedder_unavailable"
R_NO_STORE = "shop_store_unavailable"
R_FIELD_MISSING = "form_field_missing"
R_BAD_MULTIPART = "form_not_multipart"
R_BAD_SKU = "sku_id_invalid"
R_BAD_NAME = "name_invalid"
R_BAD_PRICE = "price_not_integer_paise"
R_NO_ITEM = "nothing_on_the_mat"
R_COLLISION = "enrol_collision"
R_EMPTY_GALLERY = "nothing_enrolled_yet"
R_UNKNOWN_SKU = "sku_not_enrolled"
R_IDENTITY = "identity_refused"
R_NO_PRICE = "sku_matched_but_no_price"
R_REFERENCE_REQUIRED = "empty_mat_reference_required"

#: The gates identity uses. Named here so /health can publish them and so the
#: page can show the number a refusal was measured against. INVARIANT 7 says
#: these are never widened to make a demo look better, so they are read from
#: gawaah.identity rather than retyped.
THETA = DEFAULT_THETA
PHI = DEFAULT_PHI
TAU_MM = DEFAULT_TAU_MM

# MARKER_IDS is (0, 1, 2, 3) == top-left, top-right, bottom-right, bottom-left.
CORNER_NAMES = ("top-left", "top-right", "bottom-right", "bottom-left")
CORNER_OF = dict(zip(MARKER_IDS, CORNER_NAMES))

SAMPLE_RENDER_PX_PER_MM = 4.0
SAMPLE_TILT_FRAC = 0.02          # keeps the synthetic view inside the tilt gate
SAMPLE_NOISE_SIGMA = 4.0

app = FastAPI(title="GAWAAH — upload")

# One optional empty-mat reference, supplied by the operator via POST /reference.
# It is the honest reference (see BrainConfig.reference); without it an upload
# falls back to a SYNTHESISED reference and the response says so.
_REFERENCE: dict[str, Any] = {"buffer": None, "at": None}


class UploadRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ------------------------------------------------------------------- EXIF

def exif_orientation(raw: bytes) -> Optional[int]:
    """The EXIF Orientation tag (0x0112) of a JPEG, or None if there isn't one.

    Parsed here rather than trusted to the decoder: OpenCV's IMREAD_COLOR does
    apply orientation on this build, but that is a build-dependent behaviour and
    a phone photo landing sideways would silently mis-measure every millimetre.
    We decode with IMREAD_IGNORE_ORIENTATION and rotate deliberately, so the
    behaviour is the same everywhere and is reported in the response.
    """
    if len(raw) < 4 or raw[0] != 0xFF or raw[1] != 0xD8:
        return None                                   # not a JPEG: no EXIF
    i = 2
    n = len(raw)
    while i + 4 <= n:
        if raw[i] != 0xFF:
            return None
        marker = raw[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xDA, 0xD9):                    # start of scan / end
            return None
        seg_len = struct.unpack(">H", raw[i + 2:i + 4])[0]
        if seg_len < 2:
            return None
        body = raw[i + 4:i + 2 + seg_len]
        if marker == 0xE1 and body[:6] == b"Exif\x00\x00":
            return _orientation_from_tiff(body[6:])
        i += 2 + seg_len
    return None


def _orientation_from_tiff(tiff: bytes) -> Optional[int]:
    if len(tiff) < 8:
        return None
    if tiff[:2] == b"II":
        end = "<"
    elif tiff[:2] == b"MM":
        end = ">"
    else:
        return None
    magic, ifd_off = struct.unpack(end + "HI", tiff[2:8])
    if magic != 42 or ifd_off + 2 > len(tiff):
        return None
    count = struct.unpack(end + "H", tiff[ifd_off:ifd_off + 2])[0]
    base = ifd_off + 2
    for k in range(count):
        e = base + k * 12
        if e + 12 > len(tiff):
            return None
        tag, typ, _cnt = struct.unpack(end + "HHI", tiff[e:e + 8])
        if tag == 0x0112 and typ == 3:
            value = struct.unpack(end + "H", tiff[e + 8:e + 10])[0]
            return value if 1 <= value <= 8 else None
    return None


def apply_orientation(img: np.ndarray, orientation: Optional[int]) -> np.ndarray:
    """Undo the phone's EXIF rotation so the mat is the right way up.

    The mat is not square (297 x 420 mm), so an unrotated portrait photo would
    not merely look wrong -- the marker layout would not match and the lock
    would fail with a reason that blamed the user for the decoder's omission.
    """
    if orientation in (None, 1):
        return img
    if orientation == 2:
        return cv2.flip(img, 1)
    if orientation == 3:
        return cv2.rotate(img, cv2.ROTATE_180)
    if orientation == 4:
        return cv2.flip(img, 0)
    if orientation == 5:
        return cv2.rotate(cv2.flip(img, 0), cv2.ROTATE_90_CLOCKWISE)
    if orientation == 6:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.rotate(cv2.flip(img, 1), cv2.ROTATE_90_CLOCKWISE)
    if orientation == 8:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


# ------------------------------------------------------------------ decode

_HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1",
                b"avif", b"avis")


def _sniff_unsupported(raw: bytes) -> Optional[str]:
    """Name the format when we can, so the refusal is actionable."""
    if len(raw) >= 12 and raw[4:8] == b"ftyp" and raw[8:12] in _HEIF_BRANDS:
        return ("This looks like an Apple HEIC/HEIF photo, which this build of "
                "OpenCV cannot decode. On iPhone: Settings > Camera > Formats > "
                "Most Compatible, or share the photo as JPEG.")
    if raw[:4] == b"%PDF":
        return "This is a PDF, not a photograph. Export a page as PNG or JPEG."
    if raw[:4] in (b"\x1aE\xdf\xa3",) or raw[4:8] == b"ftypmp4" or raw[4:8] == b"ftypisom":
        return "This is a video, not a photograph. Send one still frame."
    return None


def decode_upload(raw: bytes) -> tuple[np.ndarray, dict[str, Any]]:
    """Bytes -> a BGR frame, upright, bounded in size. Refuses by name."""
    if not raw:
        raise UploadRefused(
            R_EMPTY_BODY,
            "The request body was empty. Send the image bytes as the POST body, "
            "e.g. curl --data-binary @photo.jpg http://127.0.0.1:8790/analyse")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadRefused(
            R_TOO_LARGE,
            f"{len(raw) / 1e6:.1f} MB exceeds the {MAX_UPLOAD_BYTES / 1e6:.0f} MB "
            f"limit. A normal phone JPEG is 2-6 MB.")

    named = _sniff_unsupported(raw)
    if named is not None:
        raise UploadRefused(R_UNSUPPORTED, named)

    orientation = exif_orientation(raw)
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8),
                       cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if arr is None:
        raise UploadRefused(
            R_NOT_AN_IMAGE,
            "These bytes are not an image this build can decode. Supported: "
            "JPEG, PNG, BMP, TIFF, WebP.")
    if arr.ndim != 3 or arr.shape[0] < 16 or arr.shape[1] < 16:
        raise UploadRefused(
            R_DEGENERATE,
            f"Decoded to {arr.shape[1]}x{arr.shape[0]}, which is far too small "
            f"to contain a 297x420 mm mat.")

    note: dict[str, Any] = {
        "exif_orientation": orientation,
        "rotated_by_exif": bool(orientation not in (None, 1)),
        "decoded_px": [int(arr.shape[1]), int(arr.shape[0])],
        "downscaled": False,
    }
    arr = apply_orientation(arr, orientation)
    note["upright_px"] = [int(arr.shape[1]), int(arr.shape[0])]

    long_side = max(arr.shape[:2])
    if long_side > MAX_SIDE_PX:
        k = MAX_SIDE_PX / float(long_side)
        arr = cv2.resize(arr, (max(1, int(round(arr.shape[1] * k))),
                               max(1, int(round(arr.shape[0] * k)))),
                         interpolation=cv2.INTER_AREA)
        note["downscaled"] = True
        note["working_px"] = [int(arr.shape[1]), int(arr.shape[0])]
    else:
        note["working_px"] = note["upright_px"]
    return arr, note


# ------------------------------------------------------------------ sample

# Objects of KNOWN millimetre size, placed at KNOWN millimetre positions on the
# mat. (name, width_mm, height_mm, (x_mm, y_mm) of the top-left corner.)
SAMPLE_TRUTH: tuple[tuple[str, float, float, tuple[float, float]], ...] = (
    ("biscuit packet", 60.0, 95.0, (60.0, 70.0)),
    ("soap bar", 45.0, 70.0, (150.0, 70.0)),
    ("sachet", 38.0, 38.0, (95.0, 200.0)),
)


def truth_rows() -> list[dict[str, Any]]:
    rows = []
    for name, w_mm, h_mm, (x_mm, y_mm) in SAMPLE_TRUTH:
        rows.append({
            "name": name,
            "long_edge_mm": round(max(w_mm, h_mm), 2),
            "short_edge_mm": round(min(w_mm, h_mm), 2),
            "area_mm2": round(w_mm * h_mm, 1),
            "centre_mm": [round(x_mm + w_mm / 2, 2), round(y_mm + h_mm / 2, 2)],
        })
    return rows


def _warp_like_a_camera(mat: np.ndarray, tilt: float = SAMPLE_TILT_FRAC) -> np.ndarray:
    h, w = mat.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    d = w * tilt
    dst = np.float32([[d, d * 0.6], [w - d * 0.4, 0], [w, h - d * 0.5], [d * 0.3, h]])
    return cv2.warpPerspective(mat, cv2.getPerspectiveTransform(src, dst), (w, h),
                               borderValue=(235, 235, 235))


def _hide_marker(img: np.ndarray, marker_id: int,
                 px_per_mm: float = SAMPLE_RENDER_PX_PER_MM) -> np.ndarray:
    """Cover one printed corner square, the way a hand or a packet does."""
    from gawaah.takhti import marker_centres_mm
    centres = marker_centres_mm()
    idx = list(MARKER_IDS).index(marker_id)
    cx, cy = centres[idx]
    out = img.copy()
    cv2.circle(out, (int(round(cx * px_per_mm)), int(round(cy * px_per_mm))),
               int(round(MARKER_MM * px_per_mm * 0.9)), (62, 54, 48), -1)
    return out


def _stamp_simulated(img: np.ndarray) -> np.ndarray:
    """INVARIANT 7: anything simulated is VISIBLY labelled as simulated."""
    out = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    out = out.copy()
    h, w = out.shape[:2]
    scale = max(0.6, w / 900.0)
    text = "SIMULATED - NOT A PHOTOGRAPH"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x, y = max(8, (w - tw) // 2), h - max(14, int(th * 0.8))
    cv2.rectangle(out, (x - 10, y - th - 10), (x + tw + 10, y + 12), (18, 18, 22), -1)
    cv2.putText(out, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (79, 169, 224), 2, cv2.LINE_AA)
    return out


def _seed32(seed: int) -> int:
    """Any int the caller types, mapped into a seed numpy will accept.

    ?seed=-5 is a reasonable thing for a person to try and numpy rejects
    negative seeds, so it is folded rather than turned into a refusal about
    something the user cannot see and did not do wrong.
    """
    return int(seed) & 0xFFFFFFFF


def sample_scene(seed: int = 7, *, tilt: float = SAMPLE_TILT_FRAC,
                 hide: Optional[int] = None) -> tuple[np.ndarray, np.ndarray]:
    """A synthetic 'photo' pair: the real mat empty, and the real mat with
    objects of KNOWN mm size on it, both seen from the same slightly tilted
    camera. Because the sizes are known, the measurement can be CHECKED.

    The empty frame is returned too because it is the honest reference -- it is
    exactly what BrainConfig.reference wants, and generating it here costs
    nothing while a real upload has to make do with a synthesised one.
    """
    px = SAMPLE_RENDER_PX_PER_MM
    base = cv2.cvtColor(render_takhti(px), cv2.COLOR_GRAY2BGR)
    loaded = base.copy()
    for name, w_mm, h_mm, (x_mm, y_mm) in SAMPLE_TRUTH:
        x0, y0 = int(round(x_mm * px)), int(round(y_mm * px))
        x1, y1 = int(round((x_mm + w_mm) * px)), int(round((y_mm + h_mm) * px))
        cv2.rectangle(loaded, (x0, y0), (x1, y1), (40, 45, 60), -1)
        cv2.putText(loaded, name.split()[0], (x0 + 8, y0 + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (215, 215, 215), 1, cv2.LINE_AA)

    if hide is not None:
        loaded = _hide_marker(loaded, int(hide))

    def shoot(img: np.ndarray, noise_seed: int) -> np.ndarray:
        out = _warp_like_a_camera(img, tilt)
        noise = np.random.default_rng(_seed32(noise_seed)).normal(
            0, SAMPLE_NOISE_SIGMA, out.shape)
        return np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # Different noise seeds: the empty frame is a SEPARATE exposure, not a copy
    # with the objects erased. Sharing the noise would make the reference
    # unrealistically perfect and hide sensor noise the real detector must eat.
    return shoot(loaded, seed), shoot(base, seed + 1)


# -------------------------------------------------------------- references

_CLEAN_BUFFER: Optional[np.ndarray] = None


def clean_mat_buffer() -> np.ndarray:
    """The printed mat design, at exactly the metric buffer's own scale."""
    global _CLEAN_BUFFER
    if _CLEAN_BUFFER is None:
        _CLEAN_BUFFER = cv2.resize(render_takhti(SAMPLE_RENDER_PX_PER_MM),
                                   (BUF_W, BUF_H), interpolation=cv2.INTER_AREA)
    return _CLEAN_BUFFER


def synthesised_reference(H: np.ndarray, frame_shape: tuple[int, ...]) -> np.ndarray:
    """The empty-mat reference we do not have, built from the design we do.

    A naive `resize(render_takhti(), BUF)` is NOT good enough and the failure is
    instructive: the printed markers are hard black-on-white edges, so a
    sub-pixel misalignment against the rectified photo is a 200-grey-level
    difference -- far above DIFF_THRESH -- and the four corner markers get
    reported as merged objects sitting on the mat.

    So the design is pushed OUT through inv(H) into this photo's own frame
    geometry and pulled back through H. It then carries the same resampling blur
    the photo carries, and the printed ink cancels instead of ringing.
    """
    Hi = np.linalg.inv(H)
    h, w = int(frame_shape[0]), int(frame_shape[1])
    frame_like = cv2.warpPerspective(clean_mat_buffer(), Hi, (w, h), borderValue=235)
    return cv2.warpPerspective(frame_like, H, (BUF_W, BUF_H))


# ----------------------------------------------------------------- lock UX

def diagnose_lock(lock: Any) -> dict[str, Any]:
    """Why the mat did not lock, in the terms of the thing the user must move.

    This is the most common real failure and the message IS the product, so it
    reports how many of the four markers were seen, names the missing corners,
    and says what to physically change. It never guesses a lock.
    """
    found = tuple(int(i) for i in lock.ids_found)
    expected = set(MARKER_IDS)
    seen = sorted(expected & set(found))
    missing = sorted(expected - set(found))
    d: dict[str, Any] = {
        "markers_expected": len(MARKER_IDS),
        "markers_found": len(seen),
        "ids_found": list(found),
        "ids_missing": missing,
        "corners_found": [CORNER_OF[i] for i in seen],
        "corners_missing": [CORNER_OF[i] for i in missing],
    }
    if lock.locked:
        d["headline"] = "Mat locked on all four markers."
        d["fix"] = []
        return d

    if len(seen) == 0:
        d["headline"] = ("No TAKHTI markers were found at all — 0 of 4.")
        d["fix"] = [
            "Check this is a photo of the printed TAKHTI mat.",
            "Fill the frame with the mat: each printed corner square is 30 mm "
            "and needs roughly 40 px or more across in the photo.",
            "Hold still — motion blur destroys the marker's black/white edges.",
            "Even, indirect light. A hard reflection across a corner square "
            "erases it as surely as covering it up.",
        ]
        return d

    if missing:
        d["headline"] = (
            f"Only {len(seen)} of 4 markers were found. "
            f"Missing the {', '.join(CORNER_OF[i] for i in missing)} "
            f"corner{'s' if len(missing) > 1 else ''}.")
        d["fix"] = [
            "Get the WHOLE mat in frame — all four printed corner squares, "
            "none cropped by the edge of the photo.",
            "Move your hand, phone or a product off the missing corner: a "
            "covered marker is a missing marker.",
            "Kill glare on that corner; step back half a metre and re-shoot.",
        ]
        return d

    # All four seen, so the refusal is a quality gate, not a visibility problem.
    reason = str(lock.reason)
    if reason.startswith("scale error"):
        d["headline"] = (
            f"All 4 markers found, but after rectifying, the printed "
            f"{MARKER_MM:.0f} mm marker squares measure "
            f"{lock.scale_err * 100:.2f}% off — the gate is "
            f"{MAX_SCALE_ERR * 100:.1f}%.")
        d["fix"] = [
            "The mat must be FLAT. A curl or a fold bends the plane and the "
            "millimetres stop being millimetres.",
            "Smooth it onto a hard surface, weight the corners, re-shoot.",
            "Check the mat was printed at 100% scale, not 'fit to page'.",
        ]
    elif reason.startswith("perspective index"):
        d["headline"] = (
            f"All 4 markers found, but the camera is too oblique: perspective "
            f"index {lock.persp_index:.4f}, gate {MAX_PERSP_INDEX}. That is "
            f"roughly {PlaneEngine.persp_to_deg(lock.persp_index):.0f} degrees "
            f"of tilt (approximate — the index, not the angle, is what is "
            f"measured and gated).")
        d["fix"] = [
            "Shoot from more directly overhead, looking straight down.",
            "Raise the phone rather than leaning it: distance flattens "
            "perspective, tilting does not.",
        ]
    else:
        d["headline"] = f"Mat lock refused: {reason}"
        d["fix"] = ["Re-shoot the whole mat, flat and square on."]
    return d


# ---------------------------------------------------------------- analysis

def _png_b64(img: np.ndarray) -> Optional[str]:
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf.tobytes()).decode() if ok else None


def _draw_items(rect: np.ndarray, placements: list[Any]) -> np.ndarray:
    vis = rect.copy() if rect.ndim == 3 else cv2.cvtColor(rect, cv2.COLOR_GRAY2BGR)
    for p in placements:
        cx = float(p.centre_mm[0]) * PX_PER_MM_X
        cy = float(p.centre_mm[1]) * PX_PER_MM_Y
        if p.measurable and p.long_edge_mm is not None:
            w = float(p.long_edge_mm) * PX_PER_MM_X
            h = float(p.short_edge_mm) * PX_PER_MM_Y
            colour = (90, 220, 120) if p.stable else (70, 170, 240)
            box = cv2.boxPoints(((cx, cy), (w, h), float(p.angle_deg))).astype(np.int32)
            cv2.drawContours(vis, [box], 0, colour, 3)
            label = f"{p.long_edge_mm:.1f} x {p.short_edge_mm:.1f} mm"
            top = max(22, int(cy - h / 2) - 10)
            cv2.putText(vis, label, (max(4, int(cx - w / 2)), top),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.62, colour, 2, cv2.LINE_AA)
        else:
            # A refusal is drawn too, in its own colour, with its own reason.
            colour = (100, 121, 224)
            r = 26
            cv2.rectangle(vis, (int(cx - r), int(cy - r)), (int(cx + r), int(cy + r)),
                          colour, 2)
            cv2.putText(vis, str(p.reason), (max(4, int(cx - r)), max(20, int(cy - r) - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2, cv2.LINE_AA)
    return vis


def analyse(bgr: np.ndarray, *, reference: Optional[np.ndarray] = None,
            settle_frames: int = 6) -> dict[str, Any]:
    """Run the REAL pipeline. Every refusal keeps its own named reason."""
    t0 = time.perf_counter()
    eng = PlaneEngine()
    lock = eng.detect(bgr)

    out: dict[str, Any] = {
        "ok": True,
        "locked": bool(lock.locked),
        "reason": str(lock.reason),
        "ids_found": [int(i) for i in lock.ids_found],
        "scale_err_pct": None if lock.scale_err is None else round(lock.scale_err * 100, 4),
        "persp_index": None if lock.persp_index is None else round(lock.persp_index, 5),
        "reproj_rmse_px": None if lock.reproj_rmse_px is None else round(lock.reproj_rmse_px, 5),
        "gates": {
            "max_scale_err_pct": round(MAX_SCALE_ERR * 100, 3),
            "max_persp_index": MAX_PERSP_INDEX,
            "min_area_mm2": MIN_AREA_MM2,
        },
        "diagnosis": diagnose_lock(lock),
        "items": [],
        "refusals": [],
        "reference_source": None,
        "buffer_png": None,
        "overlay_png": None,
        "elapsed_ms": None,
    }
    if not lock.locked:
        out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    rect = eng.rectify(bgr, lock.H)

    if reference is not None:
        ref = reference
        out["reference_source"] = "empty_mat_photo_supplied"
        out["reference_note"] = ("Measured against the empty-mat frame you "
                                 "supplied. This is the honest reference.")
    else:
        ref = synthesised_reference(lock.H, bgr.shape)
        out["reference_source"] = "synthesised_from_printed_design"
        out["reference_note"] = (
            "No empty-mat photo was supplied, so the background was SYNTHESISED "
            "from the printed TAKHTI design under this photo's own homography. "
            "Real ink, shadows and paper texture are not in it, so small "
            "artefacts near the printed marks are possible. Upload an empty-mat "
            "photo to POST /reference for the honest comparison.")

    try:
        det = PlacementDetector(ref)
        placements: list[Any] = []
        for _ in range(max(1, settle_frames)):
            placements = det.update(rect)
    except PlacementError as exc:
        out["ok"] = False
        out["locked"] = False
        out["reason"] = R_NOT_RECTIFIED
        out["detail"] = str(exc)
        out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    for p in placements:
        row = {
            "id": int(p.id),
            "measurable": bool(p.measurable),
            "reason": str(p.reason),
            "centre_mm": [round(float(p.centre_mm[0]), 2),
                          round(float(p.centre_mm[1]), 2)],
            "stable": bool(p.stable),
            "frames_seen": int(p.frames_seen),
            "long_edge_mm": None if p.long_edge_mm is None else round(float(p.long_edge_mm), 2),
            "short_edge_mm": None if p.short_edge_mm is None else round(float(p.short_edge_mm), 2),
            "area_mm2": None if p.area_mm2 is None else round(float(p.area_mm2), 1),
            "angle_deg": None if p.angle_deg is None else round(float(p.angle_deg), 1),
            "fill_ratio": None if p.fill_ratio is None else round(float(p.fill_ratio), 3),
            "components": p.components,
        }
        if p.measurable:
            out["items"].append(row)
        else:
            row["explain"] = (
                "Touches the buffer edge, so its true edges are cropped and its "
                "size is unknown — put the whole item on the mat."
                if p.reason == REASON_BORDER else
                "Two or more items are touching, so one contour covers both and "
                "neither size is trustworthy — separate them."
                if p.reason == REASON_MERGED else
                "Refused; see reason.")
            out["refusals"].append(row)

    out["buffer_png"] = _png_b64(cv2.resize(rect, (BUF_W // 2, BUF_H // 2),
                                            interpolation=cv2.INTER_AREA))
    out["overlay_png"] = _png_b64(
        cv2.resize(_draw_items(rect, placements), (BUF_W // 2, BUF_H // 2),
                   interpolation=cv2.INTER_AREA))
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def compare_to_truth(items: list[dict[str, Any]],
                     truth: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-item measured-vs-truth error, matched by nearest centre.

    Matching is by centre because that is the one quantity that cannot be
    confused between three items 90 mm apart; matching by size would let a bad
    measurement pick whichever truth row flattered it most.
    """
    rows: list[dict[str, Any]] = []
    unmatched = list(items)
    worst_mm = 0.0
    for t in truth:
        best, best_d = None, None
        for it in unmatched:
            dx = it["centre_mm"][0] - t["centre_mm"][0]
            dy = it["centre_mm"][1] - t["centre_mm"][1]
            d = (dx * dx + dy * dy) ** 0.5
            if best_d is None or d < best_d:
                best, best_d = it, d
        row: dict[str, Any] = {
            "name": t["name"],
            "truth_long_mm": t["long_edge_mm"],
            "truth_short_mm": t["short_edge_mm"],
            "truth_centre_mm": t["centre_mm"],
        }
        if best is None:
            row.update({"matched": False, "note": "no measured item left to match"})
            rows.append(row)
            continue
        unmatched.remove(best)
        e_long = abs(best["long_edge_mm"] - t["long_edge_mm"])
        e_short = abs(best["short_edge_mm"] - t["short_edge_mm"])
        row.update({
            "matched": True,
            "item_id": best["id"],
            "measured_long_mm": best["long_edge_mm"],
            "measured_short_mm": best["short_edge_mm"],
            "measured_centre_mm": best["centre_mm"],
            "err_long_mm": round(e_long, 2),
            "err_short_mm": round(e_short, 2),
            "err_centre_mm": round(best_d, 2),
            "stable": best["stable"],
        })
        rows.append(row)
        worst_mm = max(worst_mm, e_long, e_short)

    matched = [r for r in rows if r.get("matched")]
    errs = [r["err_long_mm"] for r in matched] + [r["err_short_mm"] for r in matched]
    return {
        "rows": rows,
        "extra_items": [it["id"] for it in unmatched],
        "matched_count": len(matched),
        "truth_count": len(truth),
        "worst_edge_err_mm": round(worst_mm, 2),
        "mean_edge_err_mm": round(sum(errs) / len(errs), 3) if errs else None,
        "worst_centre_err_mm": round(
            max((r["err_centre_mm"] for r in matched), default=0.0), 2),
    }


def run_sample(seed: int = 7, *, synthetic_reference: bool = False,
               tilt: float = SAMPLE_TILT_FRAC,
               hide: Optional[int] = None) -> dict[str, Any]:
    """The SAMPLE: a scene of known size, measured, then scored against truth.

    `tilt` and `hide` exist so the tool can DEMONSTRATE its own refusals. The
    no-lock message is the product and a user cannot conveniently reproduce a
    failure on demand, so the failure is offered as a scene. Nothing about the
    refusal is faked: the over-tilted scene really is over-tilted and the real
    PlaneEngine really refuses it, for its own reason.
    """
    loaded, empty = sample_scene(seed, tilt=tilt, hide=hide)
    ref = None
    if not synthetic_reference:
        eng = PlaneEngine()
        elock = eng.detect(empty)
        if elock.locked:
            ref = eng.rectify(empty, elock.H)
    res = analyse(loaded, reference=ref)
    res["simulated"] = True
    res["simulated_note"] = (
        "SIMULATED. This scene was rendered, not photographed. The pipeline "
        "measuring it is the real one; the light hitting it is not. No result "
        "here is money and nothing here can mark a session GREEN.")
    res["seed"] = seed
    res["truth"] = truth_rows()
    # Truth is only scoreable against a lock. Without one there are no
    # millimetres to score, so no accuracy block is published rather than one
    # full of zeros that could be mistaken for agreement.
    if res["locked"]:
        res["accuracy"] = compare_to_truth(res["items"], res["truth"])
    res["input_png"] = _png_b64(_stamp_simulated(
        cv2.resize(loaded, (loaded.shape[1] // 3, loaded.shape[0] // 3),
                   interpolation=cv2.INTER_AREA)))
    if res.get("overlay_png"):
        buf = cv2.imdecode(np.frombuffer(base64.b64decode(res["overlay_png"]),
                                         np.uint8), cv2.IMREAD_COLOR)
        res["overlay_png"] = _png_b64(_stamp_simulated(buf))
    return res


# ============================================================== ENROLMENT ===
#
# From here down is the PHOTO -> PRODUCT path: a shopkeeper photographs an item,
# types a name and a price, and the counter can price that item from then on.
#
# Three rules shape all of it and none of them bends for a demo:
#   INVARIANT 1  money is integer paise; a rupee never becomes a float.
#   INVARIANT 3  no model weights anywhere; the embedder is classical cv2.
#   INVARIANT 7  abstain rather than guess; an item identity cannot place is
#                AMBER with its named reason and is EXCLUDED from the total.
# Nothing on this path settles money. Recognition proposes a price; only a
# signature-verified webhook can ever turn a session GREEN (INVARIANT 2).


# ------------------------------------------------------------- multipart

@dataclass(frozen=True)
class Part:
    """One decoded part of a multipart/form-data body."""

    name: str
    filename: Optional[str]
    content_type: str
    data: bytes

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace").strip()


def _header_param(header: str, key: str) -> Optional[str]:
    """Pull `key="value"` (or bare `key=value`) out of one header line."""
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if chunk.lower().startswith(key.lower() + "="):
            return chunk[len(key) + 1:].strip().strip('"')
    return None


def parse_multipart(raw: bytes, content_type: str) -> dict[str, Part]:
    """multipart/form-data -> {field name: Part}.

    python-multipart is NOT installed in this venv, so fastapi's Form/File would
    raise at import time. Rather than add a dependency for a demo tool, the body
    is unwrapped here. This is the multi-FIELD sibling of _body_image, which
    only ever needed the first part; /enrol needs image + sku_id + name + price
    together, so it needs the names.

    Later parts with a duplicate name win, which matches how a browser replays a
    re-submitted form field.
    """
    raw_ctype = content_type or ""
    ctype = raw_ctype.lower()
    if "multipart/form-data" not in ctype or "boundary=" not in ctype:
        raise UploadRefused(
            R_BAD_MULTIPART,
            "Expected multipart/form-data with a boundary. The page sends a "
            "FormData; from a shell use curl -F image=@photo.jpg -F sku_id=... ")
    # The boundary is taken from the ORIGINAL header, not the lower-cased copy.
    # Boundaries are case-sensitive and real ones carry upper-case letters
    # (curl's are base64-ish, browsers' contain a mixed-case nonce), so lowering
    # the whole header makes the separator never match, every part is dropped
    # and the request looks like a form somebody forgot to fill in.
    cut = ctype.index("boundary=") + len("boundary=")
    boundary = raw_ctype[cut:].split(";")[0].strip().strip('"')
    sep = b"--" + boundary.encode()

    out: dict[str, Part] = {}
    for chunk in raw.split(sep):
        if chunk in (b"", b"--", b"--\r\n", b"\r\n"):
            continue
        if chunk.startswith(b"--"):          # the closing boundary and epilogue
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        head_end = chunk.find(b"\r\n\r\n")
        if head_end == -1:
            continue
        head = chunk[:head_end].decode("utf-8", "replace")
        body = chunk[head_end + 4:]
        if body.endswith(b"\r\n"):
            body = body[:-2]

        name = None
        filename = None
        part_ctype = "application/octet-stream"
        for line in head.split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition:"):
                name = _header_param(line, "name")
                filename = _header_param(line, "filename")
            elif low.startswith("content-type:"):
                part_ctype = line.split(":", 1)[1].strip()
        if name:
            out[name] = Part(name, filename, part_ctype, body)
    return out


async def read_form(request: Request) -> dict[str, Any]:
    """Accept a multipart form OR a JSON body, and say which arrived.

    JSON exists so the endpoints are scriptable with curl and so a genuinely
    float price can reach the money boundary and be REFUSED there. A multipart
    field is always a string, so multipart alone could never prove that
    float-is-not-money holds at the API.
    """
    raw = await request.body()
    # The ORIGINAL header is kept for parse_multipart, which needs the boundary
    # with its case intact; only a lower-cased COPY is used for the type checks.
    # Passing the lower-cased header down was a real bug that no test here could
    # catch: httpx builds its boundary out of lower-case hex, so lower-casing it
    # is a no-op and TestClient passed happily, while `curl -F` (mixed-case
    # boundary) silently delivered a form with every field missing.
    raw_ctype = request.headers.get("content-type") or ""
    ctype = raw_ctype.lower()
    if "application/json" in ctype:
        import json
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise UploadRefused(R_BAD_MULTIPART, f"body is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise UploadRefused(R_BAD_MULTIPART, "JSON body must be an object")
        return {"_kind": "json", **data}
    if "application/x-www-form-urlencoded" in ctype:
        # What an HTTP client sends for a form with no file in it. It cannot
        # carry an image, so /enrol will still refuse for a MISSING IMAGE rather
        # than for the encoding -- which is the refusal the caller can act on.
        from urllib.parse import parse_qsl
        return {"_kind": "json",
                **dict(parse_qsl(raw.decode("utf-8", "replace")))}
    return {"_kind": "multipart", "_parts": parse_multipart(raw, raw_ctype)}


def form_value(form: dict[str, Any], name: str) -> Any:
    """One field, as whatever type it genuinely arrived as.

    Multipart gives a str. JSON gives whatever the caller wrote — including a
    float, which is the point: it must survive as a float all the way to the
    money boundary so that boundary can refuse it.
    """
    if form.get("_kind") == "json":
        return form.get(name)
    part = form.get("_parts", {}).get(name)
    return None if part is None else part.text


def form_image(form: dict[str, Any], name: str = "image") -> bytes:
    """The image bytes of a form, from a multipart file part or base64 JSON."""
    if form.get("_kind") == "json":
        b64 = form.get(name) or form.get(name + "_b64")
        if not b64:
            raise UploadRefused(
                R_FIELD_MISSING,
                f"no {name!r} in the JSON body. Send it as base64, or use "
                f"multipart/form-data with a file part.")
        try:
            return base64.b64decode(str(b64), validate=True)
        except Exception as exc:
            raise UploadRefused(R_NOT_AN_IMAGE, f"{name!r} is not valid base64: {exc}")
    part = form.get("_parts", {}).get(name)
    if part is None or not part.data:
        have = sorted(form.get("_parts", {}))
        raise UploadRefused(
            R_FIELD_MISSING,
            f"no {name!r} file part in the form. Parts received: "
            f"{have if have else 'none'}.")
    return part.data


# ------------------------------------------------------------------ money

# The rupee->paise boundary. This is the ONE place a price enters this service,
# and it is deliberately narrow: a str is parsed digit by digit and a float is
# refused outright. 214.507 is refused, never rounded -- rounding a price is
# how a shop loses half a paisa a thousand times and never finds out.
def price_to_paise(rupees: Any = None, paise_value: Any = None) -> int:
    """-> integer paise, or UploadRefused(R_BAD_PRICE) naming what was wrong."""
    try:
        if paise_value not in (None, ""):
            if isinstance(paise_value, bool):
                raise MoneyError(f"bool is not money: {paise_value!r}")
            if isinstance(paise_value, float):
                raise MoneyError(
                    f"float is not money: {paise_value!r}. Paise are whole.")
            if isinstance(paise_value, str):
                s = paise_value.strip()
                if not s.isdigit():
                    raise MoneyError(
                        f"price_paise must be whole digits, got {paise_value!r}")
                v: int = int(s)
            elif isinstance(paise_value, int):
                v = paise_value
            else:
                raise MoneyError(
                    f"price_paise must be an integer, got "
                    f"{type(paise_value).__name__}")
            total = int(paise(v))
        elif rupees not in (None, ""):
            if isinstance(rupees, bool):
                raise MoneyError(f"bool is not money: {rupees!r}")
            if isinstance(rupees, float):
                raise MoneyError(
                    f"float is not money: {rupees!r}. A rupee is not a float — "
                    f"send it as a string, e.g. \"214.50\".")
            if isinstance(rupees, int):
                total = int(paise(rupees)) * 100
            elif isinstance(rupees, str):
                total = int(from_rupees_str(rupees))
            else:
                raise MoneyError(
                    f"price_rupees must be a decimal string, got "
                    f"{type(rupees).__name__}")
        else:
            raise UploadRefused(
                R_FIELD_MISSING,
                "no price. Send price_rupees (e.g. \"35.00\") or price_paise "
                "(e.g. 3500).")
    except MoneyError as exc:
        raise UploadRefused(R_BAD_PRICE, str(exc)) from None
    if total <= 0:
        raise UploadRefused(
            R_BAD_PRICE,
            f"{total} paise is not a price. A zero or negative price at a till "
            f"is a typo, and billing it would be worse than refusing it.")
    return total


def rupees_str(p: int) -> str:
    """Integer paise -> a rupee string, without ever touching a float."""
    p = int(p)
    return f"{p // 100}.{p % 100:02d}"


# ------------------------------------------------- the injected embedder

_DEPS: dict[str, Any] = {"embed": None, "store": None, "store_dir": None}


def store_dir() -> Path:
    """Where the shopkeeper's catalog lives. Overridable for tests."""
    if _DEPS["store_dir"] is None:
        _DEPS["store_dir"] = Path(
            os.environ.get(
                "GAWAAH_SHOP_DIR",
                str(Path(__file__).resolve().parent.parent / "results" / "shop"),
            )
        )
    return Path(_DEPS["store_dir"])


def set_store_dir(path: Any) -> None:
    """Point the catalog at another directory and drop the cached store."""
    _DEPS["store_dir"] = Path(path)
    _DEPS["store"] = None


def load_embedder() -> Callable[[np.ndarray], Any]:
    """gawaah.embedder.embed, or a named refusal explaining its absence.

    INVARIANT 3: this resolves a CLASSICAL descriptor built from cv2 primitives.
    Nothing here downloads a checkpoint and nothing here ships weights. If the
    module is missing the endpoint says so by name rather than falling back to
    something that would quietly identify items by a worse rule.
    """
    if _DEPS["embed"] is None:
        try:
            from gawaah.embedder import embed  # noqa: WPS433
        except Exception as exc:
            raise UploadRefused(
                R_NO_EMBEDDER,
                f"gawaah.embedder is not importable ({type(exc).__name__}: "
                f"{exc}). Recognition needs a descriptor and this service will "
                f"not invent one.") from None
        _DEPS["embed"] = embed
    return _DEPS["embed"]


def load_store() -> Any:
    """gawaah.shop_store.ShopStore over store_dir(), or a named refusal."""
    if _DEPS["store"] is None:
        try:
            from gawaah.shop_store import ShopStore  # noqa: WPS433
        except Exception as exc:
            raise UploadRefused(
                R_NO_STORE,
                f"gawaah.shop_store is not importable ({type(exc).__name__}: "
                f"{exc}). There is nowhere to keep the catalog.") from None
        d = store_dir()
        d.mkdir(parents=True, exist_ok=True)
        _DEPS["store"] = ShopStore(d)
    return _DEPS["store"]


def deps_status() -> dict[str, Any]:
    """Whether the two injected pieces are present, without raising."""
    out: dict[str, Any] = {}
    for key, fn in (("embedder", load_embedder), ("shop_store", load_store)):
        try:
            fn()
            out[key] = {"available": True, "reason": None}
        except UploadRefused as exc:
            out[key] = {"available": False, "reason": exc.reason,
                        "detail": exc.detail}
    return out


def _stored_thumb(store: Any, sku_id: str) -> Optional[str]:
    """The enrolment photo the store kept, as base64 PNG, or None.

    The catalog on disk holds a PATH and a byte count, never the pixels, so the
    picture is read back here only when the page asks for it.
    """
    try:
        data = store.photo_bytes(sku_id)
    except Exception:
        return None
    return None if not data else base64.b64encode(data).decode()


# --------------------------------------------------------------- the crop

def oriented_crop_bgr(rect: np.ndarray, placement: Any) -> np.ndarray:
    """The oriented, upright COLOUR crop of one placement.

    Geometry is Brain._crop's, deliberately: the embedder must see at the till
    exactly what it saw at enrolment, and an axis-aligned crop of an item lying
    at 30 degrees is mostly mat. The one difference is that colour SURVIVES
    here. Brain._crop greys the buffer, and grey would throw away the hue and
    saturation channels the classical descriptor leans on hardest -- a red
    packet and a green packet of the same size and print are the same picture in
    grey, and telling those apart is most of the job.
    """
    cx = float(placement.centre_mm[0]) * PX_PER_MM_X
    cy = float(placement.centre_mm[1]) * PX_PER_MM_Y
    w = max(2, int(round(float(placement.long_edge_mm or 0.0) * PX_PER_MM_X)))
    h = max(2, int(round(float(placement.short_edge_mm or 0.0) * PX_PER_MM_Y)))
    angle = float(placement.angle_deg or 0.0)

    src = rect if rect.ndim == 3 else cv2.cvtColor(rect, cv2.COLOR_GRAY2BGR)
    if abs(angle) < 1e-6 or abs(angle - 180.0) < 1e-6:
        rot = src
    else:
        m = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
        rot = cv2.warpAffine(src, m, (BUF_W, BUF_H), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    return cv2.getRectSubPix(rot, (min(w, BUF_W), min(h, BUF_H)), (cx, cy))


# ------------------------------------------------------- synthetic products
#
# There is no camera and no printed mat here, so the round trip -- teach it,
# then show it -- has to be demonstrable from a mouse alone. These are the
# stand-in products. They are RENDERED, never photographed, and every image and
# every response built from them is stamped SIMULATED (INVARIANT 7).
#
# The set is chosen to make the demonstration honest rather than flattering:
#   - three products a shopkeeper would plausibly stock, at three different
#     footprints, so the metric tiebreak has something real to do;
#   - a HARD PAIR: 'jeera_biscuit' is the same size and the same two colours as
#     'parle_g_biscuit' and differs only in LAYOUT. A global colour histogram
#     cannot separate those two at all. Whether the descriptor does is measured
#     in the tests and reported, not assumed;
#   - an INTRUDER, 'chai_masala_box', which is never enrolled and is the same
#     size as parle_g_biscuit, so it survives the footprint filter and has to be
#     refused on appearance. An intruder of an unusual size would be refused by
#     the tape measure alone and would prove nothing about recognition.

@dataclass(frozen=True)
class SampleProduct:
    sku_id: str
    name: str
    w_mm: float
    h_mm: float
    price_rupees: str
    body: tuple[int, int, int]      # BGR
    accent: tuple[int, int, int]    # BGR
    layout: str                     # cap_top | cap_bottom | band_diag | dot

    @property
    def long_edge_mm(self) -> float:
        return max(self.w_mm, self.h_mm)


SAMPLE_PRODUCTS: tuple[SampleProduct, ...] = (
    SampleProduct("parle_g_biscuit", "Parle-G biscuit 100g", 60.0, 95.0,
                  "10.00", (60, 190, 235), (110, 60, 35), "cap_top"),
    SampleProduct("lifebuoy_soap", "Lifebuoy soap 125g", 45.0, 70.0,
                  "35.00", (55, 55, 200), (240, 240, 240), "band_diag"),
    SampleProduct("shampoo_sachet", "Clinic shampoo sachet", 38.0, 38.0,
                  "3.00", (85, 160, 65), (245, 245, 245), "dot"),
)

#: THE 180-DEGREE TWIN. Same size and same palette as parle_g_biscuit, and its
#: cap is at the BOTTOM instead of the top -- which is the one difference this
#: pipeline provably cannot see. Placement reports angle_deg in [0, 180), so a
#: packet laid head-up and the same packet laid head-down produce the SAME
#: measured angle and therefore crops that differ by a 180 degree turn. A
#: descriptor that separated these two would be WORSE, not better: it would
#: report a different identity for one product depending on which way round the
#: shopkeeper happened to put it down. Measured: this pair scores 0.9986, and
#: parle_g against its own 180-degree rotation scores 0.9980 -- the same number,
#: because it is the same observation. The collision guard refuses this
#: enrolment, which is the correct and only safe answer.
HARD_PAIR_PRODUCT = SampleProduct(
    "jeera_biscuit", "Jeera biscuit 100g (180-degree twin)", 60.0, 95.0,
    "12.00", (60, 190, 235), (110, 60, 35), "cap_bottom")

#: THE LAYOUT TWIN. Also the same size and the same two colours as
#: parle_g_biscuit, but its layout is NOT a rotation of it. This is the pair
#: that shows what the descriptor can genuinely do: measured 0.4643, below the
#: 0.55 similarity gate, so the two are separable and both enrol cleanly. A
#: global colour histogram would score these two at essentially 1.0.
LAYOUT_TWIN_PRODUCT = SampleProduct(
    "glucose_biscuit", "Glucose biscuit 100g (layout twin)", 60.0, 95.0,
    "14.00", (60, 190, 235), (110, 60, 35), "dot")

#: Never enrolled by the demo. Same footprint as parle_g_biscuit on purpose.
INTRUDER_PRODUCT = SampleProduct(
    "chai_masala_box", "Chai masala box (never taught)", 60.0, 95.0,
    "0.00", (150, 60, 130), (40, 170, 240), "dot")

PRODUCTS_BY_ID = {p.sku_id: p for p in
                  SAMPLE_PRODUCTS + (HARD_PAIR_PRODUCT, LAYOUT_TWIN_PRODUCT,
                                     INTRUDER_PRODUCT)}


def render_product(p: SampleProduct, px_per_mm: float) -> np.ndarray:
    """One product as a flat BGR patch of its true millimetre size.

    Features are deliberately CHUNKY. The scene is rendered at 4 px/mm, warped
    by a camera, noised, then rectified back to 2.83 px/mm -- fine print would
    not survive that round trip, and a descriptor tuned on detail that the
    pipeline destroys would look excellent here and fail on a real shelf.
    """
    w = max(4, int(round(p.w_mm * px_per_mm)))
    h = max(4, int(round(p.h_mm * px_per_mm)))
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = p.body

    if p.layout == "cap_top":
        img[: int(h * 0.28), :] = p.accent
    elif p.layout == "cap_bottom":
        img[int(h * 0.72):, :] = p.accent
    elif p.layout == "band_diag":
        cv2.line(img, (0, h), (w, 0), p.accent, max(3, int(min(w, h) * 0.22)),
                 cv2.LINE_AA)
    elif p.layout == "dot":
        cv2.circle(img, (w // 2, h // 2), max(3, int(min(w, h) * 0.30)),
                   p.accent, -1, cv2.LINE_AA)

    # A dark rim: real packets have an edge, and it gives the segmenter a clean
    # boundary so the measured millimetres are the packet's, not a soft halo's.
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), (35, 35, 40), max(1, int(px_per_mm)))
    return img


def _paste_rotated(scene: np.ndarray, patch: np.ndarray,
                   cx_px: float, cy_px: float, rot_deg: float) -> None:
    """Paste `patch` into `scene` centred at (cx, cy), rotated, in place."""
    h, w = patch.shape[:2]
    side = int(np.ceil(np.hypot(w, h))) + 4
    canvas = np.zeros((side, side, 3), np.uint8)
    mask = np.zeros((side, side), np.uint8)
    y0, x0 = (side - h) // 2, (side - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = patch
    mask[y0:y0 + h, x0:x0 + w] = 255

    m = cv2.getRotationMatrix2D((side / 2.0, side / 2.0), rot_deg, 1.0)
    canvas = cv2.warpAffine(canvas, m, (side, side), flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(mask, m, (side, side), flags=cv2.INTER_NEAREST)

    tx = int(round(cx_px - side / 2.0))
    ty = int(round(cy_px - side / 2.0))
    sx0, sy0 = max(0, tx), max(0, ty)
    sx1 = min(scene.shape[1], tx + side)
    sy1 = min(scene.shape[0], ty + side)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    sub = canvas[sy0 - ty:sy1 - ty, sx0 - tx:sx1 - tx]
    sub_m = mask[sy0 - ty:sy1 - ty, sx0 - tx:sx1 - tx].astype(bool)
    region = scene[sy0:sy1, sx0:sx1]
    region[sub_m] = sub[sub_m]


#: (product, centre_x_mm, centre_y_mm, rotation_deg)
Pose = tuple[SampleProduct, float, float, float]


def product_scene(poses: list[Pose], seed: int = 11,
                  *, tilt: float = SAMPLE_TILT_FRAC
                  ) -> tuple[np.ndarray, np.ndarray]:
    """A synthetic 'photograph' of the real mat with products on it.

    Returns (loaded, empty) shot from the same tilted camera with SEPARATE
    noise, for the same reason sample_scene() does: sharing the noise would give
    the detector a reference more perfect than any real empty-mat photo.
    """
    px = SAMPLE_RENDER_PX_PER_MM
    base = cv2.cvtColor(render_takhti(px), cv2.COLOR_GRAY2BGR)
    loaded = base.copy()
    for p, cx_mm, cy_mm, rot in poses:
        _paste_rotated(loaded, render_product(p, px), cx_mm * px, cy_mm * px, rot)

    def shoot(img: np.ndarray, noise_seed: int) -> np.ndarray:
        out = _warp_like_a_camera(img, tilt)
        noise = np.random.default_rng(_seed32(noise_seed)).normal(
            0, SAMPLE_NOISE_SIGMA, out.shape)
        return np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    return shoot(loaded, seed), shoot(base, seed + 1)


def scene_png(poses: list[Pose], seed: int = 11) -> bytes:
    """A simulated photo as PNG bytes, ready to POST at /enrol or /recognise."""
    loaded, _ = product_scene(poses, seed)
    ok, buf = cv2.imencode(".png", loaded)
    if not ok:
        raise UploadRefused(R_INTERNAL, "could not encode the simulated scene")
    return buf.tobytes()


def scene_png_and_reference(poses: list[Pose], seed: int = 11
                            ) -> tuple[bytes, Optional[np.ndarray]]:
    """The simulated photo AND the rectified empty-mat buffer that goes with it.

    A real shopkeeper shoots the empty mat once and POSTs it to /reference; the
    demo has that frame for free, so it uses it. This is not the demo being
    kinder to itself than reality — it is the demo doing the thing the tool
    already asks a real user to do, and reporting reference_source so which one
    was used is never in doubt.
    """
    loaded, empty = product_scene(poses, seed)
    ok, buf = cv2.imencode(".png", loaded)
    if not ok:
        raise UploadRefused(R_INTERNAL, "could not encode the simulated scene")
    eng = PlaneEngine()
    elock = eng.detect(empty)
    ref = eng.rectify(empty, elock.H) if elock.locked else None
    return buf.tobytes(), ref


def enrol_pose(p: SampleProduct, seed: int = 11) -> list[Pose]:
    """One product alone, mid-mat, square on — an enrolment photograph."""
    return [(p, MAT_W_MM / 2.0, MAT_H_MM / 2.0, 0.0)]


# ------------------------------------------------------- measure the scene

def _rectify_and_place(bgr: np.ndarray, *, settle_frames: int = 6,
                       reference: Optional[np.ndarray] = None
                       ) -> tuple[np.ndarray, list[Any], dict[str, Any]]:
    """Lock -> rectify -> placements. Raises UploadRefused with the diagnosis.

    The refusal carries diagnose_lock()'s full answer, so a caller that could
    not lock is told how many of the four markers were seen and what to
    physically change — never just 'failed'.
    """
    eng = PlaneEngine()
    lock = eng.detect(bgr)
    if not lock.locked:
        exc = UploadRefused(str(lock.reason),
                            diagnose_lock(lock).get("headline", str(lock.reason)))
        exc.diagnosis = diagnose_lock(lock)               # type: ignore[attr-defined]
        exc.lock = lock                                   # type: ignore[attr-defined]
        raise exc

    rect = eng.rectify(bgr, lock.H)
    ref = reference if reference is not None else _REFERENCE["buffer"]
    ref_source = "empty_mat_photo_supplied"
    if ref is None:
        # Without a real empty-mat photo the printed design is synthesised, and
        # it does NOT cancel perfectly: the 20 mm scale patch and the exit arrow
        # leave residue that segments as small blobs. They are reported, and
        # they abstain honestly rather than being filtered away by size --
        # silently dropping small blobs would also drop a genuine sachet.
        ref = synthesised_reference(lock.H, bgr.shape)
        ref_source = "synthesised_from_printed_design"

    det = PlacementDetector(ref)
    placements: list[Any] = []
    for _ in range(max(1, settle_frames)):
        placements = det.update(rect)
    return rect, placements, {
        "reference_source": ref_source,
        "locked": True,
        "reason": str(lock.reason),
        "ids_found": [int(i) for i in lock.ids_found],
        "diagnosis": diagnose_lock(lock),
        "scale_err_pct": None if lock.scale_err is None else round(lock.scale_err * 100, 4),
        "persp_index": None if lock.persp_index is None else round(lock.persp_index, 5),
    }


def _measured_row(p: Any) -> dict[str, Any]:
    return {
        "id": int(p.id),
        "long_edge_mm": None if p.long_edge_mm is None else round(float(p.long_edge_mm), 2),
        "short_edge_mm": None if p.short_edge_mm is None else round(float(p.short_edge_mm), 2),
        "area_mm2": None if p.area_mm2 is None else round(float(p.area_mm2), 1),
        "angle_deg": None if p.angle_deg is None else round(float(p.angle_deg), 1),
        "centre_mm": [round(float(p.centre_mm[0]), 2), round(float(p.centre_mm[1]), 2)],
        "stable": bool(p.stable),
    }


def _thumb_png(crop: np.ndarray, long_side: int = 96) -> Optional[str]:
    """A small base64 PNG of the enrolled crop, so the catalog can show what was
    actually taught. Capped deliberately: the catalog is JSON on disk and a full
    crop per SKU would make it megabytes for no extra evidence."""
    h, w = crop.shape[:2]
    if max(h, w) > long_side:
        k = long_side / float(max(h, w))
        crop = cv2.resize(crop, (max(1, int(w * k)), max(1, int(h * k))),
                          interpolation=cv2.INTER_AREA)
    return _png_b64(crop)


# --------------------------------------------------------------- endpoints

def _body_image(raw: bytes, content_type: str) -> bytes:
    """Accept either a raw image body or a one-part multipart/form-data body.

    Raw bytes are what the page sends (a File is a Blob, fetch takes it whole)
    and what `curl --data-binary` sends. multipart is what `curl -F` sends, and
    python-multipart is not installed here, so the one part is unwrapped by hand
    rather than adding a dependency for a demo tool.
    """
    raw_ctype = content_type or ""
    ctype = raw_ctype.lower()
    if "multipart/form-data" not in ctype or "boundary=" not in ctype:
        return raw
    # Case-sensitive: see parse_multipart. Lower-casing the boundary here made
    # `curl -F` uploads silently arrive empty.
    cut = ctype.index("boundary=") + len("boundary=")
    boundary = raw_ctype[cut:].split(";")[0].strip().strip('"')
    sep = b"--" + boundary.encode()
    parts = [p for p in raw.split(sep) if p not in (b"", b"--", b"--\r\n", b"\r\n")]
    for part in parts:
        head_end = part.find(b"\r\n\r\n")
        if head_end == -1:
            continue
        body = part[head_end + 4:]
        if body.endswith(b"\r\n"):
            body = body[:-2]
        if body:
            return body
    return raw


def _refusal(exc: UploadRefused, status: int = 400) -> JSONResponse:
    # A refusal raised after a failed mat lock carries the real diagnosis --
    # which corners were missing and what to change. Passing it through is the
    # difference between "no lock" and an instruction the user can act on.
    carried = getattr(exc, "diagnosis", None)
    if carried is not None:
        return JSONResponse({
            "ok": False, "locked": False, "reason": exc.reason,
            "detail": exc.detail, "settles_money": False,
            "ids_found": carried.get("ids_found", []),
            "items": [], "refusals": [], "amber": [],
            "total_paise": 0, "total_rupees": "0.00",
            "diagnosis": carried,
        }, status_code=status)
    return JSONResponse({
        "ok": False,
        "locked": False,
        "reason": exc.reason,
        "detail": exc.detail,
        "ids_found": [],
        "items": [],
        "refusals": [],
        "diagnosis": {
            "markers_expected": len(MARKER_IDS),
            "markers_found": 0,
            "ids_found": [],
            "ids_missing": list(MARKER_IDS),
            "corners_found": [],
            "corners_missing": list(CORNER_NAMES),
            "headline": exc.detail,
            "fix": [],
        },
    }, status_code=status)


@app.get("/health")
def health() -> JSONResponse:
    """Liveness plus the numbers that decide every answer this service gives."""
    return JSONResponse({
        "ok": True,
        "service": "gawaah-upload",
        "buffer_px": [BUF_W, BUF_H],
        "mat_mm": [MAT_W_MM, MAT_H_MM],
        "px_per_mm": [round(PX_PER_MM_X, 6), round(PX_PER_MM_Y, 6)],
        "marker_ids": list(MARKER_IDS),
        "marker_mm": MARKER_MM,
        "gates": {
            "max_scale_err_pct": round(MAX_SCALE_ERR * 100, 3),
            "max_persp_index": MAX_PERSP_INDEX,
            "min_area_mm2": MIN_AREA_MM2,
        },
        "limits": {"max_upload_bytes": MAX_UPLOAD_BYTES, "max_side_px": MAX_SIDE_PX},
        "reference_loaded": _REFERENCE["buffer"] is not None,
        "reference_at": _REFERENCE["at"],
        "opencv": cv2.__version__,
        "money": "none — this service cannot price, bill or mark anything GREEN",
        # The two injected pieces the enrol/recognise path needs. Reported
        # rather than assumed, so a page that cannot teach says WHY instead of
        # rendering an empty catalog that looks like an empty shop.
        "dependencies": deps_status(),
        "identity_gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM},
        "model_weights": "none — invariant 3; the descriptor is classical cv2",
        "store_dir": str(store_dir()),
    })


@app.get("/sample")
def sample_ep(seed: int = 7, reference: str = "empty_photo",
              fail: str = "") -> JSONResponse:
    """?fail=tilt shoots the same scene too obliquely; ?fail=marker covers one
    printed corner. Both are refused by the real engine for its own reason."""
    tilt = SAMPLE_TILT_FRAC
    hide: Optional[int] = None
    if fail == "tilt":
        tilt = 0.045          # measured above the 0.04 perspective-index gate
    elif fail == "marker":
        hide = 1              # the top-right corner
    try:
        return JSONResponse(run_sample(
            int(seed), synthetic_reference=(reference == "synthetic"),
            tilt=tilt, hide=hide))
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "ids_found": [], "items": [], "refusals": []},
                            status_code=400)


@app.post("/analyse")
async def analyse_ep(request: Request) -> JSONResponse:
    try:
        raw = _body_image(await request.body(),
                          request.headers.get("content-type", ""))
        bgr, note = decode_upload(raw)
        res = analyse(bgr, reference=_REFERENCE["buffer"])
        res["input"] = note
        res["simulated"] = False
        # INVARIANT 4: the photo is not echoed back and is not stored. Only the
        # rectified 840x1188 metric buffer leaves this process.
        res["source_image_returned"] = False
        return JSONResponse(res)
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "ids_found": [], "items": [], "refusals": []},
                            status_code=400)


@app.post("/reference")
async def reference_ep(request: Request) -> JSONResponse:
    """Give the tool a photo of the EMPTY mat. That is the honest reference."""
    try:
        raw = _body_image(await request.body(),
                          request.headers.get("content-type", ""))
        bgr, note = decode_upload(raw)
        eng = PlaneEngine()
        lock = eng.detect(bgr)
        if not lock.locked:
            return JSONResponse({
                "ok": False, "locked": False, "reason": str(lock.reason),
                "detail": "The reference photo must itself lock the mat.",
                "ids_found": [int(i) for i in lock.ids_found],
                "diagnosis": diagnose_lock(lock), "items": [], "refusals": [],
            }, status_code=400)
        _REFERENCE["buffer"] = eng.rectify(bgr, lock.H)
        _REFERENCE["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return JSONResponse({"ok": True, "locked": True,
                             "reason": "reference_accepted",
                             "reference_at": _REFERENCE["at"], "input": note})
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"},
                            status_code=400)


@app.delete("/reference")
def clear_reference_ep() -> JSONResponse:
    _REFERENCE["buffer"] = None
    _REFERENCE["at"] = None
    return JSONResponse({"ok": True, "reason": "reference_cleared"})


# ------------------------------------------------------- enrol / recognise

MONEY_NOTE = ("Nothing on this page settles money. Recognition PROPOSES a "
              "price; only a signature-verified Razorpay webhook can mark a "
              "session GREEN.")


def _valid_sku(sku_id: str) -> str:
    s = (sku_id or "").strip()
    if not s:
        raise UploadRefused(R_BAD_SKU, "sku_id is required and was empty.")
    if len(s) > 64:
        raise UploadRefused(R_BAD_SKU, f"sku_id is {len(s)} characters; cap is 64.")
    ok = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    bad = sorted(set(s) - ok)
    if bad:
        raise UploadRefused(
            R_BAD_SKU,
            f"sku_id may only contain letters, digits, '_', '-' and '.'; "
            f"found {''.join(bad)!r}. It becomes a filename and a ledger key.")
    return s


def _valid_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        raise UploadRefused(
            R_BAD_NAME,
            "name is required. The shopkeeper reads the name, not the sku_id.")
    if len(s) > 120:
        raise UploadRefused(R_BAD_NAME, f"name is {len(s)} characters; cap is 120.")
    return s


def do_enrol(raw: bytes, sku_id: str, name: str, price_paise: int,
             *, force: bool = False,
             reference: Optional[np.ndarray] = None) -> dict[str, Any]:
    """One photo + a name + a price -> one SKU the counter can price.

    The order is deliberate: the mat locks first, the item is MEASURED first,
    and only then is it embedded. Identity is never attempted without a metric
    footprint, and an enrolment with no millimetres would poison every later
    identification -- the footprint filter would let it compete against
    everything, because its declared size would be a guess.
    """
    embed = load_embedder()
    store = load_store()

    bgr, note = decode_upload(raw)
    rect, placements, lock_info = _rectify_and_place(bgr, reference=reference)

    # TEACHING REQUIRES AN HONEST BACKGROUND, and this is not fussiness.
    #
    # Without a real empty-mat photo the background is SYNTHESISED from the
    # printed design, and it does not cancel perfectly: the mat's own 20 mm
    # scale patch and its exit arrow survive as small blobs. Enrolment takes the
    # LARGEST blob, so on a mat with nothing on it the largest blob is the mat's
    # own printing -- and it was measured here doing exactly that, teaching the
    # scale patch as a 21.0 mm product and then confidently billing it at the
    # price the operator typed.
    #
    # A mis-taught SKU is permanent and produces a confident WRONG PRICE for
    # ever after, which is the one outcome this whole system exists to prevent.
    # Recognition is allowed to run without a reference because its mistakes are
    # transient and abstain safely; enrolment is not, because its mistakes do
    # not. One extra photograph, once, is a cheap price for that.
    if lock_info["reference_source"] != "empty_mat_photo_supplied":
        raise UploadRefused(
            R_REFERENCE_REQUIRED,
            "Teaching needs a photograph of the EMPTY mat first. Without one "
            "the background is synthesised from the printed design and does not "
            "cancel exactly, so the mat's own printed scale patch and arrow are "
            "indistinguishable from a small product — and the largest blob on "
            "an empty mat is the mat itself. Shoot the mat with nothing on it, "
            "POST it to /reference (the SET EMPTY-MAT REFERENCE button), then "
            "teach.")

    usable = [p for p in placements
              if p.measurable and p.long_edge_mm and p.area_mm2]
    refused = [{"id": int(p.id), "reason": str(p.reason),
                "centre_mm": [round(float(p.centre_mm[0]), 2),
                              round(float(p.centre_mm[1]), 2)]}
               for p in placements if not p.measurable]
    if not usable:
        raise UploadRefused(
            R_NO_ITEM,
            "The mat locked, but nothing measurable is on it. "
            + (f"{len(refused)} blob(s) were found and refused "
               f"({', '.join(sorted({r['reason'] for r in refused}))}) — put the "
               f"WHOLE item well inside the mat, not touching the edge, and not "
               f"touching another item."
               if refused else
               f"No blob above {MIN_AREA_MM2} mm² differed from the mat at all. "
               f"Place the item on the mat and re-shoot."))

    # The LARGEST measurable placement is the subject. An enrolment photo has
    # one item on the mat; if a stray shadow or a fingertip also segmented, the
    # product is the big one. Every candidate is reported either way, so the
    # choice is visible rather than silent.
    largest = max(usable, key=lambda p: float(p.area_mm2))
    crop = oriented_crop_bgr(rect, largest)
    footprint_mm = float(largest.long_edge_mm)

    t0 = time.perf_counter()
    try:
        vector = np.asarray(embed(crop), dtype=np.float64).ravel()
    except Exception as exc:
        raise UploadRefused(
            R_NO_EMBEDDER,
            f"gawaah.embedder.embed failed on a "
            f"{crop.shape[1]}x{crop.shape[0]} crop: "
            f"{type(exc).__name__}: {exc}") from None
    embed_ms = round((time.perf_counter() - t0) * 1000, 2)

    # The collision verdict is computed HERE, with identify()'s own thresholds,
    # so it is reported whatever gawaah/shop_store.py chooses to do about it.
    # A pair inside both the appearance margin and the footprint tolerance is
    # permanently amber, and saying so now -- while the shopkeeper still has the
    # item in his hand -- is free. Saying it at the till is a wrong price.
    try:
        gallery = store.to_gallery()
        probe = gallery
        if sku_id in gallery:
            probe = Gallery.from_dict(gallery.to_dict())
            probe.remove(sku_id)
        ident = Identifier(probe, embed, theta=THETA, phi=PHI, tau_mm=TAU_MM)
        collision = ident.check_collision([vector], footprint_mm)
    except IdentityError as exc:
        raise UploadRefused(R_IDENTITY, f"{exc}") from None

    replaced = sku_id in gallery
    verdict = collision.to_audit()
    verdict["message"] = collision.message

    if collision.collides and not force:
        raise UploadRefused(
            R_COLLISION,
            f"Refusing to enrol {sku_id!r}: it is indistinguishable from "
            f"{collision.sku_id!r} — cosine {collision.similarity:.4f} (bar "
            f"{1.0 - THETA:.2f}) and footprint delta "
            f"{collision.footprint_delta_mm:.2f} mm (tolerance {TAU_MM} mm). "
            f"Identify would be permanently amber between these two. Take a "
            f"disambiguating photo — a different face of the packet — or give "
            f"them genuinely different sizes.")

    # The crop goes in as an ndarray: ShopStore downscales and re-encodes it
    # itself, so encoding here just to have it decoded again would be waste.
    result = store.add_sku(sku_id, name, int(price_paise), [vector],
                           footprint_mm, photo_png=crop)
    if not result.ok:
        # The store runs its own collision guard with the same thresholds. If it
        # refuses after we cleared the item, the two disagree and that is a bug
        # worth surfacing loudly, not papering over.
        raise UploadRefused(
            R_COLLISION if result.collides_with else result.reason,
            f"{result.message or result.reason}"
            + (f" (colliding with {result.collides_with!r})"
               if result.collides_with else ""))

    return {
        "ok": True,
        "settles_money": False,
        "money_note": MONEY_NOTE,
        "locked": True,
        **{k: lock_info[k] for k in
           ("reason", "ids_found", "reference_source", "scale_err_pct",
            "persp_index", "diagnosis")},
        "measured": {
            **_measured_row(largest),
            "footprint_mm": round(footprint_mm, 2),
            "candidates_considered": len(usable),
            "other_candidates": [_measured_row(p) for p in usable
                                 if p.id != largest.id],
            "refused_blobs": refused,
        },
        "stored": {
            "sku_id": sku_id,
            "name": name,
            "price_paise": int(price_paise),
            "price_rupees": rupees_str(price_paise),
            "footprint_mm": round(footprint_mm, 2),
            "n_views": 1,
            "vector_dim": int(vector.shape[0]),
            "replaced_existing": bool(replaced),
            "embed_ms": embed_ms,
            "store_action": result.action,
            "store_reason": result.reason,
            "photo_action": result.photo_action,
            "photo_bytes": int(result.photo_bytes),
            "previous_price_paise": result.previous_price_paise,
        },
        "collision": verdict,
        "forced": bool(force and collision.collides),
        "crop_png": _thumb_png(crop, 220),
        "catalog_size": len(store),
        "input": note,
        "source_image_returned": False,
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM},
    }


def _draw_recognition(rect: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    """Green box + price for a named item, amber box + reason for an abstention.

    Amber is drawn as prominently as green on purpose. An abstention is a
    correct outcome, not an error to be tucked away, and the shopkeeper needs to
    see the one line he must tap as clearly as the ones he need not.
    """
    vis = rect.copy() if rect.ndim == 3 else cv2.cvtColor(rect, cv2.COLOR_GRAY2BGR)
    for r in rows:
        m = r.get("measured") or {}
        if m.get("long_edge_mm") is None:
            continue
        cx = float(m["centre_mm"][0]) * PX_PER_MM_X
        cy = float(m["centre_mm"][1]) * PX_PER_MM_Y
        w = float(m["long_edge_mm"]) * PX_PER_MM_X
        h = float(m["short_edge_mm"]) * PX_PER_MM_Y
        named = r.get("sku_id") is not None and r.get("price_paise") is not None
        colour = (120, 220, 130) if named else (70, 175, 235)
        box = cv2.boxPoints(((cx, cy), (w, h),
                             float(m.get("angle_deg") or 0.0))).astype(np.int32)
        cv2.drawContours(vis, [box], 0, colour, 3)
        label = (f"{r['sku_id']}  Rs {r['price_rupees']}" if named
                 else f"AMBER {r.get('reason')}")
        cv2.putText(vis, label, (max(4, int(cx - w / 2)),
                                 max(22, int(cy - h / 2) - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, colour, 2, cv2.LINE_AA)
    return vis


def do_recognise(raw: bytes, *,
                 reference: Optional[np.ndarray] = None) -> dict[str, Any]:
    """Every item on the mat, named or honestly refused, and a total.

    The total is the sum of the items that were NAMED. Amber items are listed,
    with their reason and their millimetres, and are not in it. That exclusion
    is the whole product: a counter that guesses a price is worse than one that
    asks for a tap.
    """
    embed = load_embedder()
    store = load_store()

    bgr, note = decode_upload(raw)
    gallery = store.to_gallery()
    rect, placements, lock_info = _rectify_and_place(bgr, reference=reference)

    ident = Identifier(gallery, embed, theta=THETA, phi=PHI, tau_mm=TAU_MM)

    rows: list[dict[str, Any]] = []
    total = 0
    t0 = time.perf_counter()

    for p in placements:
        base: dict[str, Any] = {
            "id": int(p.id),
            "measured": _measured_row(p),
            "sku_id": None, "name": None,
            "price_paise": None, "price_rupees": None,
            "top1": None, "top2": None, "margin": None,
            "top1_sku": None, "top2_sku": None, "n_candidates": 0,
        }

        # Not measurable -> not identifiable. A cropped or merged blob has no
        # trustworthy long edge, and identity is never attempted without one.
        if not p.measurable or p.long_edge_mm is None:
            base["reason"] = str(p.reason)
            base["explain"] = (
                "Touches the buffer edge, so its true size is unknown — put the "
                "whole item on the mat."
                if p.reason == REASON_BORDER else
                "Two or more items are touching, so one contour covers both — "
                "separate them."
                if p.reason == REASON_MERGED else
                "Not measurable; see reason.")
            rows.append(base)
            continue

        if len(gallery) == 0:
            base["reason"] = R_EMPTY_GALLERY
            base["explain"] = ("Nothing has been taught yet, so there is nothing "
                               "to compare against. Enrol a product first.")
            rows.append(base)
            continue

        crop = oriented_crop_bgr(rect, p)
        try:
            res = ident.identify(crop, float(p.long_edge_mm))
        except IdentityError as exc:
            base["reason"] = R_IDENTITY
            base["explain"] = str(exc)
            rows.append(base)
            continue

        base.update({
            "reason": res.reason,
            "top1": round(float(res.top1), 4),
            "top2": round(float(res.top2), 4),
            "margin": round(float(res.margin), 4),
            "top1_sku": res.top1_sku,
            "top2_sku": res.top2_sku,
            "n_candidates": int(res.n_candidates),
        })

        if res.sku_id is None:
            base["explain"] = ABSTAIN_EXPLAIN.get(
                res.reason, "Abstained; see reason.")
            rows.append(base)
            continue

        # Named. A price is still not guaranteed, and a named SKU with no price
        # must go AMBER rather than bill zero.
        try:
            price = store.price_paise(res.sku_id)
        except Exception:
            price = None
        if price is None:
            base.update({"sku_id": None, "reason": R_NO_PRICE,
                         "top1_sku": res.top1_sku})
            base["explain"] = (
                f"Recognised as {res.sku_id!r} but the catalog has no price for "
                f"it, so it is amber rather than billed at zero.")
            rows.append(base)
            continue

        rec = store.get(res.sku_id)
        base.update({
            "sku_id": res.sku_id,
            "name": getattr(rec, "name", res.sku_id) if rec else res.sku_id,
            "price_paise": int(price),
            "price_rupees": rupees_str(int(price)),
            "enrolled_footprint_mm": (
                round(float(rec.footprint_mm), 2) if rec else None),
            "footprint_delta_mm": (
                round(abs(float(rec.footprint_mm) - float(p.long_edge_mm)), 2)
                if rec else None),
        })
        total += int(price)
        rows.append(base)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    named = [r for r in rows if r["sku_id"] is not None]
    amber = [r for r in rows if r["sku_id"] is None]

    return {
        "ok": True,
        "settles_money": False,
        "money_note": MONEY_NOTE,
        "locked": True,
        **{k: lock_info[k] for k in
           ("reason", "ids_found", "reference_source", "scale_err_pct",
            "persp_index", "diagnosis")},
        "items": rows,
        "named": named,
        "amber": amber,
        "counts": {"placements": len(rows), "named": len(named),
                   "amber": len(amber)},
        "amber_reasons": sorted({str(r["reason"]) for r in amber}),
        # INVARIANT 7, in one number: the total is what was NAMED. Amber items
        # are excluded, listed above, and must be resolved by a human tap.
        "total_paise": int(total),
        "total_rupees": rupees_str(total),
        "excluded_paise": 0,
        "excluded_count": len(amber),
        "catalog_size": len(gallery),
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM},
        "elapsed_ms": elapsed_ms,
        "overlay_png": _png_b64(cv2.resize(
            _draw_recognition(rect, rows), (BUF_W // 2, BUF_H // 2),
            interpolation=cv2.INTER_AREA)),
        "input": note,
        "source_image_returned": False,
    }


#: What each abstention MEANS, in the terms of what the shopkeeper does next.
ABSTAIN_EXPLAIN = {
    "no_candidate_in_footprint":
        "Nothing taught is this SIZE. The tape measure ruled every SKU out "
        "before appearance was even consulted — this is probably a new product.",
    "below_similarity":
        "Something taught is the right size, but nothing LOOKS like this. "
        "Probably a new product: teach it.",
    "ambiguous_pair":
        "The top two are tied to within numerical noise, so which one is "
        "'first' is an artefact of sort order and carries no information. "
        "Both are named above; a human must pick.",
    "below_margin":
        "There is a leader, but not by enough to be safe. The leader is named "
        "above as a SUGGESTION, never as a fact.",
    R_EMPTY_GALLERY: "Nothing has been taught yet.",
    R_NO_PRICE: "Recognised, but no price is stored for that SKU.",
}


@app.post("/enrol")
async def enrol_ep(request: Request) -> JSONResponse:
    """multipart: image + sku_id + name + price_rupees -> one taught product."""
    try:
        form = await read_form(request)
        sku_id = _valid_sku(str(form_value(form, "sku_id") or ""))
        name = _valid_name(str(form_value(form, "name") or ""))
        price = price_to_paise(form_value(form, "price_rupees"),
                               form_value(form, "price_paise"))
        force = str(form_value(form, "force") or "").lower() in ("1", "true", "yes")
        res = do_enrol(form_image(form), sku_id, name, price, force=force)
        res["simulated"] = False
        return JSONResponse(res)
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "ids_found": [], "items": [], "refusals": []},
                            status_code=400)


@app.post("/recognise")
async def recognise_ep(request: Request) -> JSONResponse:
    """multipart: image -> every item, named or amber, and an integer total."""
    try:
        form = await read_form(request)
        res = do_recognise(form_image(form))
        res["simulated"] = False
        return JSONResponse(res)
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "ids_found": [], "items": [], "refusals": [],
                             "amber": [], "total_paise": 0},
                            status_code=400)


def catalog() -> dict[str, Any]:
    store = load_store()
    gallery = store.to_gallery()
    prices = store.price_map()
    rows = []
    for sku_id in gallery.skus():
        # to_gallery() and price_map() are two projections of one dict, so they
        # cannot disagree about which SKUs exist. Reading the price through the
        # map rather than the record keeps that guarantee load-bearing here: if
        # they ever DID diverge, this page would show it as a missing price
        # rather than quietly preferring one source.
        price = prices.get(sku_id)
        entry = gallery.get(sku_id)
        rows.append({
            "sku_id": sku_id,
            "name": getattr(store.get(sku_id), "name", sku_id),
            "price_paise": None if price is None else int(price),
            "price_rupees": None if price is None else rupees_str(int(price)),
            "footprint_mm": round(float(gallery.footprint(sku_id)), 2),
            "n_views": int(entry.n_views),
            "vector_dim": int(entry.dim),
            "thumb_png": _stored_thumb(store, sku_id),
        })
    return {
        "ok": True,
        "settles_money": False,
        "money_note": MONEY_NOTE,
        "count": len(rows),
        "skus": rows,
        "store_dir": str(store_dir()),
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM},
        "priced": sum(1 for r in rows if r["price_paise"] is not None),
    }


@app.get("/shop")
def shop_ep() -> JSONResponse:
    """The taught catalog: names, integer paise, footprints, thumbnails."""
    try:
        return JSONResponse(catalog())
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "count": 0, "skus": []}, status_code=400)


@app.delete("/shop/{sku_id}")
def shop_delete_ep(sku_id: str) -> JSONResponse:
    try:
        store = load_store()
        if sku_id not in store:
            raise UploadRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not in the catalog. Nothing was removed. "
                f"Enrolled: {list(store.skus()) or 'nothing yet'}.")
        removed = store.remove(sku_id)
        return JSONResponse({"ok": bool(removed), "reason": "sku_removed",
                             "sku_id": sku_id, "settles_money": False,
                             "count": len(store)})
    except UploadRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_UNKNOWN_SKU else 400)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"},
                            status_code=400)


# ------------------------------------------------------------ the demo path
#
# Everything below runs the SAME do_enrol/do_recognise as a real upload. Only
# the photograph is synthetic, and it is stamped as such on the image, in the
# JSON, and on the page. Without these a visitor with no mat and no camera could
# not perform the round trip at all, and the round trip is the whole argument.

SIM_NOTE = ("SIMULATED. These scenes were rendered, not photographed. The mat "
            "lock, the millimetres, the descriptor, the thresholds and the "
            "total are all the real ones. " + MONEY_NOTE)


@app.post("/demo/teach")
async def demo_teach_ep(request: Request) -> JSONResponse:
    """Teach the sample products from simulated photos, one real enrol each."""
    try:
        form = await read_form(request) if await request.body() else {"_kind": "json"}
    except UploadRefused:
        form = {"_kind": "json"}
    # ?hard_pair=1 additionally offers the two twins, which are the interesting
    # cases: the LAYOUT twin enrols cleanly (0.4643, separable) and the
    # 180-DEGREE twin is REFUSED by the collision guard (0.9986, provably not
    # separable). Both outcomes are correct and the demo shows both.
    hard = str(form_value(form, "hard_pair") or "").lower() in ("1", "true", "yes")
    products = list(SAMPLE_PRODUCTS) + (
        [LAYOUT_TWIN_PRODUCT, HARD_PAIR_PRODUCT] if hard else [])

    taught: list[dict[str, Any]] = []
    for p in products:
        png, ref = scene_png_and_reference(enrol_pose(p))
        try:
            r = do_enrol(png, p.sku_id, p.name,
                         price_to_paise(p.price_rupees), force=False,
                         reference=ref)
            taught.append({
                "sku_id": p.sku_id, "ok": True,
                "truth_long_mm": round(p.long_edge_mm, 2),
                "measured_long_mm": r["measured"]["long_edge_mm"],
                "err_long_mm": round(abs(r["measured"]["long_edge_mm"]
                                         - p.long_edge_mm), 2),
                "price_paise": r["stored"]["price_paise"],
                "collision": r["collision"],
                "crop_png": r["crop_png"],
            })
        except UploadRefused as exc:
            taught.append({"sku_id": p.sku_id, "ok": False,
                           "reason": exc.reason, "detail": exc.detail})
    return JSONResponse({
        "ok": any(t["ok"] for t in taught),
        "simulated": True, "simulated_note": SIM_NOTE,
        "settles_money": False, "money_note": MONEY_NOTE,
        "taught": taught,
        "catalog": catalog(),
    })


#: A DIFFERENT scene from the enrolment photos: every item is somewhere else on
#: the mat and turned to a different angle, and an untaught intruder is present.
#: Recognising the enrolment photo back would prove only that a hash works.
DEMO_SCENE: tuple[tuple[str, float, float, float], ...] = (
    ("parle_g_biscuit", 85.0, 105.0, 24.0),
    ("lifebuoy_soap", 205.0, 118.0, -31.0),
    ("shampoo_sachet", 96.0, 268.0, 47.0),
    ("chai_masala_box", 208.0, 300.0, -12.0),
)


@app.post("/demo/recognise")
@app.get("/demo/recognise")
def demo_recognise_ep(intruder: str = "1", seed: int = 23) -> JSONResponse:
    """Recognise a simulated scene the counter has never seen before."""
    try:
        poses: list[Pose] = [
            (PRODUCTS_BY_ID[sku], x, y, r) for sku, x, y, r in DEMO_SCENE
            if sku != INTRUDER_PRODUCT.sku_id
            or str(intruder).lower() in ("1", "true", "yes")
        ]
        png, ref = scene_png_and_reference(poses, seed=int(seed))
        res = do_recognise(png, reference=ref)
        res["simulated"] = True
        res["simulated_note"] = SIM_NOTE
        res["scene_truth"] = [
            {"sku_id": p.sku_id, "name": p.name,
             "long_edge_mm": round(p.long_edge_mm, 2),
             "centre_mm": [x, y], "rotation_deg": r,
             "taught": p.sku_id != INTRUDER_PRODUCT.sku_id}
            for p, x, y, r in poses
        ]
        res["scoring"] = score_against_truth(res)
        if res.get("overlay_png"):
            buf = cv2.imdecode(np.frombuffer(
                base64.b64decode(res["overlay_png"]), np.uint8), cv2.IMREAD_COLOR)
            res["overlay_png"] = _png_b64(_stamp_simulated(buf))
        return JSONResponse(res)
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "items": [], "amber": [], "total_paise": 0},
                            status_code=400)


def score_against_truth(res: dict[str, Any]) -> dict[str, Any]:
    """Mark the demo's own homework, and publish the marks.

    The simulated scene knows what is really on the mat, so the demo can check
    itself — and the only version of that worth shipping is the one that reports
    its own failures as loudly as its successes.

    The verdict that matters is MIS-NAMED: an item that was never taught, given
    a name and a price anyway. That is a confident wrong price, the one outcome
    this whole system exists to prevent, and it is strictly worse than an
    abstention. An untaught item that comes back amber is a CORRECT answer and
    is counted as one.

    Items are matched to truth by centre, in millimetres — the one quantity that
    cannot be confused between items 90 mm apart. Matching by identity would let
    the scorer grade itself on its own answer.
    """
    truth = res.get("scene_truth") or []
    rows: list[dict[str, Any]] = []
    unmatched = [i for i in res.get("items", [])
                 if (i.get("measured") or {}).get("long_edge_mm") is not None]
    for t in truth:
        best, best_d = None, None
        for it in unmatched:
            c = it["measured"]["centre_mm"]
            d = ((c[0] - t["centre_mm"][0]) ** 2
                 + (c[1] - t["centre_mm"][1]) ** 2) ** 0.5
            if best_d is None or d < best_d:
                best, best_d = it, d
        if best is None:
            rows.append({"truth_sku": t["sku_id"], "verdict": "not_detected",
                         "taught": t["taught"]})
            continue
        unmatched.remove(best)
        got = best.get("sku_id")
        if t["taught"]:
            verdict = "correct" if got == t["sku_id"] else (
                "mis_named" if got else "abstained_on_a_taught_item")
        else:
            verdict = "correctly_abstained" if got is None else "MIS_NAMED"
        rows.append({
            "truth_sku": t["sku_id"], "taught": t["taught"], "got_sku": got,
            "verdict": verdict, "top1": best.get("top1"),
            "reason": best.get("reason"),
            "price_paise": best.get("price_paise"),
            "centre_err_mm": round(best_d, 2),
        })

    mis = [r for r in rows if r.get("verdict") == "MIS_NAMED"]
    return {
        "rows": rows,
        "correct": sum(1 for r in rows if r.get("verdict") == "correct"),
        "correctly_abstained": sum(
            1 for r in rows if r.get("verdict") == "correctly_abstained"),
        "mis_named_untaught": len(mis),
        "mis_priced_paise": sum(int(r.get("price_paise") or 0) for r in mis),
        "honest": not mis,
        "headline": (
            "Every item was either named correctly or honestly refused."
            if not mis else
            f"{len(mis)} item(s) that were NEVER TAUGHT were named and priced "
            f"anyway, adding {sum(int(r.get('price_paise') or 0) for r in mis)} "
            f"paise that should not be in the total. This is a confident wrong "
            f"price. The similarity gate phi={PHI} did not hold: "
            + "; ".join(f"{r['truth_sku']} scored {r['top1']} as "
                        f"{r['got_sku']}" for r in mis)
            + ". The gate is NOT widened or narrowed to hide this — it belongs "
              "to gawaah/identity.py and the descriptor to gawaah/embedder.py."),
    }


@app.post("/demo/reference")
def demo_reference_ep() -> JSONResponse:
    """Install the SIMULATED empty mat as the reference.

    The real button takes a photograph of the operator's own empty mat. This is
    the same act for someone who has neither mat nor camera, and it is the step
    /enrol now insists on, so the mouse-only round trip still works end to end.
    """
    try:
        _, empty = product_scene([], seed=11)
        eng = PlaneEngine()
        lock = eng.detect(empty)
        if not lock.locked:
            raise UploadRefused(str(lock.reason),
                                "the simulated empty mat did not lock")
        _REFERENCE["buffer"] = eng.rectify(empty, lock.H)
        _REFERENCE["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return JSONResponse({"ok": True, "reason": "reference_accepted",
                             "simulated": True, "simulated_note": SIM_NOTE,
                             "reference_at": _REFERENCE["at"],
                             "settles_money": False})
    except UploadRefused as exc:
        return _refusal(exc)


@app.get("/demo/photo")
def demo_photo_ep(sku: str = "parle_g_biscuit", seed: int = 11):
    """A simulated enrolment photograph, so the file-upload path can be tried
    with a real file by someone who has no mat and no camera."""
    from fastapi.responses import Response
    p = PRODUCTS_BY_ID.get(sku)
    if p is None:
        return JSONResponse({"ok": False, "reason": R_UNKNOWN_SKU,
                             "detail": f"no sample product {sku!r}; have "
                                       f"{sorted(PRODUCTS_BY_ID)}"},
                            status_code=404)
    return Response(scene_png(enrol_pose(p), seed=int(seed)),
                    media_type="image/png",
                    headers={"X-Gawaah-Simulated": "true"})


# A RAW string: the page carries JS regexes (\d, \.) and escaped quotes (\') that
# must reach the browser verbatim. Without the r-prefix Python eats the
# backslashes, \' collapses to ' and the catalog's REMOVE button emits broken
# JavaScript -- a failure that is invisible in Python tests and fatal in a
# browser, which is exactly why tests/test_upload_enrol.py asserts on it.
PAGE = r"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>GAWAAH — upload an image</title>
<style>
 :root{--bg:#0f1115;--fg:#e8e4dc;--dim:#8b8781;--ok:#5fbf87;--amb:#e0a94f;
       --bad:#e07964;--sim:#7aa4e8;--card:#171a20;--rule:#262a32}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:15px/1.55 -apple-system,system-ui,sans-serif;padding:20px;
      max-width:1100px;margin:0 auto}
 h1{font-size:20px;margin:0 0 2px}
 .sub{color:var(--dim);font:12px ui-monospace,Menlo,monospace}
 .lead{color:var(--dim);margin:4px 0 18px;font-size:13px}
 .row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
 button,label.f{background:var(--fg);color:#000;border:0;border-radius:8px;
   padding:11px 18px;font-size:15px;font-weight:650;cursor:pointer;line-height:1.2}
 label.f{background:#2a2f38;color:var(--fg)}
 label.g{background:transparent;color:var(--fg);border:1px solid #3a4049;font-weight:600}
 button.s{background:transparent;color:var(--amb);border:1px solid var(--rule);
   font-size:12.5px;font-weight:600;padding:7px 12px}
 input[type=file]{display:none}
 .card{background:var(--card);border:1px solid var(--rule);border-radius:10px;
   padding:14px;margin-bottom:14px}
 .k{display:flex;justify-content:space-between;gap:12px;padding:6px 0;
    border-bottom:1px solid var(--rule);font:12.5px ui-monospace,Menlo,monospace}
 .k:last-child{border:0}
 .k b{color:var(--dim);font-weight:600;white-space:nowrap}
 .k span{text-align:right;word-break:break-word}
 .ok{color:var(--ok)}.amb{color:var(--amb)}.bad{color:var(--bad)}.sim{color:var(--sim)}
 img{max-width:100%;border-radius:8px;display:block;border:1px solid var(--rule)}
 table{width:100%;border-collapse:collapse;font:12.5px ui-monospace,Menlo,monospace}
 th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--rule)}
 th{color:var(--dim);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase}
 td.n{text-align:right}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
 .banner{border-left:3px solid var(--sim);background:#141a25;padding:10px 14px;
   border-radius:6px;margin-bottom:14px;font-size:13px}
 .head{font-size:15px;font-weight:650;margin:2px 0 8px}
 ul.fix{margin:6px 0 0;padding-left:20px;font-size:13px;color:var(--fg)}
 ul.fix li{margin:4px 0}
 .tag{display:inline-block;font:10.5px ui-monospace,Menlo,monospace;
   padding:2px 7px;border-radius:99px;border:1px solid var(--rule);color:var(--dim)}
 .scroll{overflow-x:auto}
 h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);
    margin:0 0 10px;font-weight:650}
 .step{border:1px solid var(--rule);border-radius:12px;padding:16px;margin-bottom:16px;
   background:var(--card)}
 .step>h3{margin:0 0 4px;font-size:16px;font-weight:680}
 .step>.why{color:var(--dim);font-size:13px;margin:0 0 14px}
 .money{border-left:3px solid var(--ok);background:#121a16}
 input[type=text]{background:#0c0e12;border:1px solid #333a44;color:var(--fg);
   border-radius:8px;padding:10px 12px;font:13px ui-monospace,Menlo,monospace;min-width:0}
 input[type=text]:focus{outline:2px solid #3d6ea8}
 .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;
   margin-bottom:12px}
 .fields label{display:flex;flex-direction:column;gap:5px;font:11px ui-monospace,Menlo,monospace;
   color:var(--dim);letter-spacing:.05em;text-transform:uppercase}
 .pill{display:inline-block;padding:3px 9px;border-radius:99px;font:11px ui-monospace,Menlo,monospace;
   font-weight:700;letter-spacing:.04em}
 .pill.ok{background:#16301f;color:var(--ok);border:1px solid #23503330}
 .pill.amb{background:#31260f;color:var(--amb)}
 .pill.bad{background:#331a16;color:var(--bad)}
 .total{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
   border-top:2px solid var(--rule);margin-top:12px;padding-top:12px}
 .total b{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}
 .total span{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
 .thumb{width:52px;height:52px;object-fit:contain;background:#0b0d11;border-radius:6px;
   border:1px solid var(--rule)}
 .x{background:transparent;border:1px solid var(--rule);color:var(--bad);border-radius:6px;
   padding:5px 10px;font-size:11px;font-weight:700;cursor:pointer}
 .muted{color:var(--dim);font-size:12.5px}
 hr{border:0;border-top:1px solid var(--rule);margin:30px 0 22px}
</style>
<h1>GAWAAH — teach it a product, then let it price it</h1>
<div class=lead>Photograph an item on the mat, give it a name and a price in rupees, and from
then on this page recognises that item and prices it. The descriptor is classical OpenCV —
colour, layout, edges and shape. <b>No model weights, anywhere, ever</b> (invariant 3).</div>

<div class="banner money"><b>NOTHING HERE SETTLES MONEY.</b> Recognition only ever
<i>proposes</i> a price. Only a signature-verified Razorpay webhook can mark a session GREEN.
An item this page cannot name with confidence is shown AMBER with its reason and is
<b>excluded from the total</b> — never priced by a guess.</div>

<div id=deps></div>

<div class=step>
  <h3>1 &nbsp;Teach a product</h3>
  <p class=why>One photo of the item alone on the mat. The mat locks, the item is measured in
  millimetres, the largest placement is cropped and embedded, and the vector is stored against
  your name and price. Price is converted to integer paise at this boundary and refused if it
  is not exact — 214.507 is rejected, never rounded.</p>
  <div class=fields>
    <label>sku id<input type=text id=t_sku placeholder="parle_g_100g"></label>
    <label>name<input type=text id=t_name placeholder="Parle-G biscuit 100g"></label>
    <label>price in rupees<input type=text id=t_price placeholder="10.00" oninput="pricePreview()"></label>
  </div>
  <div id=refstate></div>
  <div class=row>
    <label class=g>SET EMPTY-MAT REFERENCE<input type=file accept="image/*" onchange="setRef(this)"></label>
    <label class=f>CHOOSE A PHOTO<input type=file id=t_file accept="image/*" onchange="teachPick()"></label>
    <button onclick="teach()">TEACH THIS PRODUCT</button>
    <span class=sub id=t_hint>no file chosen</span>
  </div>
  <div class=row>
    <span class=sub>no mat or camera? use a simulated photo:</span>
    <button class=s onclick="demoTeach(false)">TEACH 3 SAMPLE PRODUCTS</button>
    <button class=s onclick="demoTeach(true)">…AND BOTH HARD TWINS</button>
    <button class=s onclick="grabDemoPhoto()">PUT A SAMPLE PHOTO IN THE FORM</button>
  </div>
  <div id=teachout></div>
</div>

<div class=step>
  <h3>2 &nbsp;The catalog</h3>
  <p class=why>Every product taught so far, with the price in rupees AND in the integer paise
  actually stored, the footprint measured off the mat, and the crop the embedder saw.</p>
  <div class=row><button class=s onclick="loadShop()">REFRESH</button></div>
  <div id=shopout></div>
</div>

<div class=step>
  <h3>3 &nbsp;Try it — a second photo</h3>
  <p class=why>Upload a <i>different</i> photo, with the items somewhere else on the mat and
  turned to another angle. Every item is measured, embedded and matched against the catalog.
  Named items are priced and totalled; anything the gallery cannot place is AMBER with its
  named reason and is left out of the total.</p>
  <div class=row>
    <label class=f>CHOOSE A PHOTO<input type=file id=r_file accept="image/*" onchange="tryIt()"></label>
    <button class=s onclick="demoRecognise(true)">RECOGNISE A SIMULATED SCENE</button>
    <button class=s onclick="demoRecognise(false)">…WITHOUT THE UNTAUGHT INTRUDER</button>
  </div>
  <div id=recout></div>
</div>

<hr>
<h1>GAWAAH — drop an image in</h1>
<div class=lead>The measurement tool on its own. Runs the real <code>PlaneEngine</code> and
<code>PlacementDetector</code>. No camera, no printed mat, no phone needed. Uploaded bytes are
measured and dropped — they are never stored and never sent back; only the rectified
840&times;1188 metric buffer leaves the process.</div>
<div class=row>
  <button onclick="runSample('')">TRY A SAMPLE</button>
  <label class=f>UPLOAD A PHOTO<input type=file id=f accept="image/*" onchange="send(this,'/analyse')"></label>
  <label class=g>SET EMPTY-MAT REFERENCE<input type=file accept="image/*" onchange="send(this,'/reference')"></label>
</div>
<div class=row>
  <span class=sub>see a refusal:</span>
  <button class=s onclick="runSample('&fail=tilt')">CAMERA TOO OBLIQUE</button>
  <button class=s onclick="runSample('&fail=marker')">A CORNER COVERED</button>
  <button class=s onclick="runSample('&reference=synthetic')">NO EMPTY-MAT REFERENCE</button>
</div>
<div id=out></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const mm=v=>v==null?'—':Number(v).toFixed(2)+' mm';

function lockCard(r){
  const d=r.diagnosis||{};
  const cls=r.locked?'ok':'amb';
  let h=`<div class=card>`;
  h+=`<div class=k><b>mat lock</b><span class=${cls}>${r.locked?'LOCKED':'NO LOCK'}</span></div>`;
  h+=`<div class=k><b>reason</b><span>${esc(r.reason)}</span></div>`;
  h+=`<div class=k><b>markers</b><span>${d.markers_found==null?'—':d.markers_found} of ${d.markers_expected==null?4:d.markers_expected}`
    +`${d.ids_found&&d.ids_found.length?' — ids '+esc(d.ids_found.join(', ')):''}</span></div>`;
  if(d.corners_missing&&d.corners_missing.length)
    h+=`<div class=k><b>corners missing</b><span class=amb>${esc(d.corners_missing.join(', '))}</span></div>`;
  if(r.scale_err_pct!=null)h+=`<div class=k><b>scale error</b><span>${r.scale_err_pct}% (gate ${r.gates?r.gates.max_scale_err_pct:'—'}%)</span></div>`;
  if(r.persp_index!=null)h+=`<div class=k><b>perspective index</b><span>${r.persp_index} (gate ${r.gates?r.gates.max_persp_index:'—'})</span></div>`;
  if(r.reproj_rmse_px!=null)h+=`<div class=k><b>reprojection rmse</b><span>${r.reproj_rmse_px} px</span></div>`;
  if(r.elapsed_ms!=null)h+=`<div class=k><b>elapsed</b><span>${r.elapsed_ms} ms</span></div>`;
  h+=`</div>`;
  return h;
}

function noLockCard(r){
  const d=r.diagnosis||{};
  let h=`<div class=card><div class="head amb">I DO NOT KNOW — no mat lock</div>`;
  h+=`<div>${esc(d.headline||r.detail||r.reason)}</div>`;
  if(d.fix&&d.fix.length){h+=`<ul class=fix>`;d.fix.forEach(f=>h+=`<li>${esc(f)}</li>`);h+=`</ul>`}
  h+=`<div class=sub style="margin-top:10px">Without a lock nothing can be measured in millimetres,
       so nothing is measured. No size is guessed and no line could be billed.</div></div>`;
  return h;
}

function itemsCard(r){
  let h='';
  if(r.items&&r.items.length){
    h+=`<div class=card><h2>measured — ${r.items.length} item${r.items.length>1?'s':''}</h2><div class=scroll><table>
      <tr><th>#</th><th>long</th><th>short</th><th>area</th><th>centre</th><th>angle</th><th>state</th></tr>`;
    r.items.forEach(it=>{h+=`<tr><td>${it.id}</td><td class=n>${mm(it.long_edge_mm)}</td>
      <td class=n>${mm(it.short_edge_mm)}</td><td class=n>${it.area_mm2} mm²</td>
      <td class=n>${it.centre_mm[0]}, ${it.centre_mm[1]}</td><td class=n>${it.angle_deg}°</td>
      <td class=${it.stable?'ok':'amb'}>${it.stable?'stable':'settling'}</td></tr>`});
    h+=`</table></div></div>`;
  } else if(r.locked){
    h+=`<div class=card><div class="head">Mat locked — nothing on it</div>
        <div class=sub>The plane is good and the reference matched it. No blob above
        ${r.gates?r.gates.min_area_mm2:100} mm² was found, so there is nothing to measure.</div></div>`;
  }
  if(r.refusals&&r.refusals.length){
    h+=`<div class=card><h2 class=bad>refused — ${r.refusals.length}</h2><div class=scroll><table>
      <tr><th>#</th><th>reason</th><th>centre</th><th>what to do</th></tr>`;
    r.refusals.forEach(it=>{h+=`<tr><td>${it.id}</td><td class=bad>${esc(it.reason)}</td>
      <td class=n>${it.centre_mm[0]}, ${it.centre_mm[1]}</td><td>${esc(it.explain)}</td></tr>`});
    h+=`</table></div></div>`;
  }
  return h;
}

function truthCard(r){
  const a=r.accuracy;if(!a)return'';
  let h=`<div class=card><h2>measured vs truth</h2><div class=scroll><table>
    <tr><th>item</th><th>truth long</th><th>measured</th><th>err</th>
    <th>truth short</th><th>measured</th><th>err</th><th>centre err</th></tr>`;
  a.rows.forEach(t=>{
    if(!t.matched){h+=`<tr><td>${esc(t.name)}</td><td class=n>${mm(t.truth_long_mm)}</td>
      <td colspan=6 class=bad>not matched — ${esc(t.note||'')}</td></tr>`;return}
    const c=e=>e<=1?'ok':(e<=2?'amb':'bad');
    h+=`<tr><td>${esc(t.name)}</td>
      <td class=n>${mm(t.truth_long_mm)}</td><td class=n>${mm(t.measured_long_mm)}</td>
      <td class="n ${c(t.err_long_mm)}">${t.err_long_mm.toFixed(2)}</td>
      <td class=n>${mm(t.truth_short_mm)}</td><td class=n>${mm(t.measured_short_mm)}</td>
      <td class="n ${c(t.err_short_mm)}">${t.err_short_mm.toFixed(2)}</td>
      <td class="n ${c(t.err_centre_mm)}">${t.err_centre_mm.toFixed(2)}</td></tr>`});
  h+=`</table></div>`;
  h+=`<div class=k style="margin-top:8px"><b>matched</b><span>${a.matched_count} of ${a.truth_count}</span></div>`;
  h+=`<div class=k><b>worst edge error</b><span class=${a.worst_edge_err_mm<=2?'ok':'bad'}>${a.worst_edge_err_mm} mm</span></div>`;
  h+=`<div class=k><b>mean edge error</b><span>${a.mean_edge_err_mm} mm</span></div>`;
  h+=`<div class=k><b>worst centre error</b><span class=${a.worst_centre_err_mm<=2?'ok':'bad'}>${a.worst_centre_err_mm} mm</span></div>`;
  if(a.extra_items.length)h+=`<div class=k><b>unmatched extras</b><span class=amb>${esc(a.extra_items.join(', '))}</span></div>`;
  h+=`</div>`;
  return h;
}

function render(r){
  let h='';
  if(r.simulated)h+=`<div class="banner sim"><b>SIMULATED</b> — this scene was rendered, not
    photographed. The pipeline measuring it is the real one. No result here is money and
    nothing here can mark a session GREEN.</div>`;
  h+=lockCard(r);
  if(r.reference_source)h+=`<div class=card><div class=k><b>reference</b>
    <span class="${r.reference_source==='empty_mat_photo_supplied'?'ok':'amb'}">${esc(r.reference_source)}</span></div>
    <div class=sub style="margin-top:8px">${esc(r.reference_note)}</div></div>`;
  if(!r.locked||r.ok===false)h+=noLockCard(r);else{h+=itemsCard(r);h+=truthCard(r)}
  if(r.input)h+=`<div class=card><h2>what arrived</h2>
    <div class=k><b>decoded</b><span>${r.input.decoded_px.join(' × ')} px</span></div>
    <div class=k><b>exif orientation</b><span>${r.input.exif_orientation==null?'none':r.input.exif_orientation}
      ${r.input.rotated_by_exif?'<span class="tag ok">rotated upright</span>':''}</span></div>
    <div class=k><b>working size</b><span>${r.input.working_px.join(' × ')} px
      ${r.input.downscaled?'<span class=tag>downscaled</span>':''}</span></div>
    <div class=k><b>bytes kept</b><span class=ok>none — invariant 4</span></div></div>`;
  h+=`<div class=grid>`;
  if(r.input_png)h+=`<div class=card><h2>input (simulated)</h2><img src="data:image/png;base64,${r.input_png}"></div>`;
  if(r.overlay_png)h+=`<div class=card><h2>rectified 840×1188 + measured</h2><img src="data:image/png;base64,${r.overlay_png}"></div>`;
  h+=`</div>`;
  $('#out').innerHTML=h;
}

async function post(url,body){
  const res=await fetch(url,{method:'POST',body});
  try{return await res.json()}
  catch(e){return{ok:false,locked:false,reason:'bad_response',detail:'HTTP '+res.status,diagnosis:{}}}
}
async function runSample(extra){
  $('#out').innerHTML='<div class=card>running the real pipeline on a simulated scene…</div>';
  try{render(await (await fetch('/sample?seed=7'+(extra||''))).json())}
  catch(e){$('#out').innerHTML='<div class="card bad">'+esc(e)+'</div>'}
}
async function send(el,url){
  if(!el.files||!el.files[0])return;
  $('#out').innerHTML='<div class=card>analysing…</div>';
  const r=await post(url,el.files[0]);
  el.value='';
  if(url==='/reference'&&r.ok){
    $('#out').innerHTML='<div class="card ok">Empty-mat reference accepted at '+esc(r.reference_at)
      +'. Uploads will now be measured against it. Press TRY A SAMPLE or upload a loaded mat.</div>';
    return;
  }
  render(r);
}
/* ==================================================== teach / catalog / try
   The round trip. Every number rendered below came off the wire from the real
   pipeline; nothing here recomputes a measurement or a price in the browser.  */

const paiseFmt = p => (p==null ? '—' : '₹' + (Math.trunc(p/100)) + '.' + String(p%100).padStart(2,'0'));

// Rupees -> integer paise, by STRING, never through a float. This mirrors
// gawaah.money.from_rupees_str and exists only to preview what will be stored:
// the server re-parses and is the authority, so a disagreement is a bug, not a
// rounding difference. 214.507 fails the regex and is refused, never rounded.
function toPaise(s){
  s = String(s==null?'':s).trim();
  if(!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const [w, f=''] = s.split('.');
  return parseInt(w||'0',10)*100 + parseInt((f+'00').slice(0,2),10);
}

function pricePreview(){
  const raw = $('#t_price').value, p = toPaise(raw), el = $('#t_hint');
  if(!raw){ el.className='sub'; el.textContent = fileName||'no file chosen'; return; }
  if(p===null){ el.className='sub bad'; el.textContent = '"'+raw+'" is not an exact rupee amount — it will be REFUSED, not rounded'; return; }
  if(p===0){ el.className='sub bad'; el.textContent = '0 paise is not a price'; return; }
  el.className='sub ok'; el.textContent = 'stores as '+p+' paise ('+paiseFmt(p)+')';
}

let fileName = '';
function teachPick(){
  const f=$('#t_file').files[0];
  fileName = f ? f.name : '';
  if(f && !$('#t_sku').value) $('#t_sku').value = f.name.replace(/\.[^.]+$/,'').replace(/[^A-Za-z0-9_.-]+/g,'_').slice(0,64);
  pricePreview();
}

// Whether an honest empty-mat background exists. Teaching REQUIRES one: without
// it the mat's own printed scale patch is the largest blob on an empty mat and
// would be taught as a product, then confidently priced.
async function refState(){
  let h='';
  try{
    const d = await (await fetch('/health')).json();
    h = d.reference_loaded
      ? '<div class=k><b>empty-mat reference</b><span class=ok>set at '+esc(d.reference_at)+' — teaching is enabled</span></div>'
      : '<div class="card amb" style="margin-bottom:12px"><b>No empty-mat reference yet.</b> '
        + 'Teaching is refused until there is one: without a photo of the bare mat the background is '
        + 'synthesised from the printed design, it does not cancel exactly, and the mat&rsquo;s own scale '
        + 'patch is the largest blob on an empty mat — it would be taught as a product and then priced. '
        + '<button class=s style="margin-top:8px" onclick="demoRef()">USE A SIMULATED EMPTY MAT</button></div>';
  }catch(e){ h=''; }
  $('#refstate').innerHTML = h;
}

async function setRef(el){
  if(!el.files||!el.files[0]) return;
  const r = await post('/reference', el.files[0]);
  el.value='';
  $('#teachout').innerHTML = r.ok
    ? '<div class="card ok">Empty-mat reference accepted at '+esc(r.reference_at)+'. You can teach now.</div>'
    : refusalCard(r);
  refState();
}

async function demoRef(){
  const r = await post('/demo/reference', new FormData());
  $('#teachout').innerHTML = r.ok
    ? '<div class="card sim">A SIMULATED empty mat is now the reference. Teaching is enabled.</div>'
    : refusalCard(r);
  refState();
}

async function grabDemoPhoto(){
  // Fetch a simulated enrolment photo and load it into the real file input, so
  // the ordinary upload path can be exercised by someone with no mat at all.
  // The matching simulated empty mat goes in as the reference too, because that
  // is the step /enrol insists on and the point is a round trip that works.
  await demoRef();
  const sku = 'parle_g_biscuit';
  const r = await fetch('/demo/photo?sku='+sku);
  const b = await r.blob();
  const dt = new DataTransfer();
  dt.items.add(new File([b], sku+'_SIMULATED.png', {type:'image/png'}));
  $('#t_file').files = dt.files;
  $('#t_sku').value = sku; $('#t_name').value = 'Parle-G biscuit 100g';
  $('#t_price').value = '10.00';
  fileName = sku+'_SIMULATED.png';
  pricePreview();
  $('#teachout').innerHTML = '<div class="card sim">A SIMULATED photo is now in the form. '
    + 'Press TEACH THIS PRODUCT to run the real enrol path on it.</div>';
}

function refusalCard(r){
  const d = r.diagnosis||{};
  let h = '<div class=card><div class="head amb">I DO NOT KNOW — '+esc(r.reason)+'</div>';
  h += '<div>'+esc(r.detail||d.headline||'')+'</div>';
  if(d.markers_found!=null)
    h += '<div class=k style="margin-top:8px"><b>markers</b><span>'+d.markers_found+' of '+(d.markers_expected||4)+
         (d.corners_missing&&d.corners_missing.length?' — missing '+esc(d.corners_missing.join(', ')):'')+'</span></div>';
  if(d.fix&&d.fix.length){h+='<ul class=fix>';d.fix.forEach(f=>h+='<li>'+esc(f)+'</li>');h+='</ul>';}
  h += '<div class=sub style="margin-top:10px">Nothing was stored and nothing was priced.</div></div>';
  return h;
}

async function teach(){
  const f = $('#t_file').files[0];
  if(!f){ $('#teachout').innerHTML='<div class="card bad">Choose a photo first.</div>'; return; }
  const fd = new FormData();
  fd.append('image', f);
  fd.append('sku_id', $('#t_sku').value);
  fd.append('name', $('#t_name').value);
  fd.append('price_rupees', $('#t_price').value);
  $('#teachout').innerHTML = '<div class=card>locking the mat, measuring, embedding…</div>';
  const r = await post('/enrol', fd);
  if(!r.ok){ $('#teachout').innerHTML = refusalCard(r); loadShop(); return; }
  const m=r.measured, s=r.stored, c=r.collision;
  let h = '<div class=card><div class="head ok">TAUGHT — '+esc(s.sku_id)+'</div>';
  h += '<div class=grid style="margin-top:10px">';
  h += '<div><div class=k><b>measured long edge</b><span>'+m.long_edge_mm+' mm</span></div>'
     + '<div class=k><b>short edge</b><span>'+m.short_edge_mm+' mm</span></div>'
     + '<div class=k><b>angle on the mat</b><span>'+m.angle_deg+'°</span></div>'
     + '<div class=k><b>candidates on the mat</b><span>'+m.candidates_considered+' (largest taken)</span></div>'
     + '<div class=k><b>reference</b><span class="'+(r.reference_source==='empty_mat_photo_supplied'?'ok':'amb')+'">'+esc(r.reference_source)+'</span></div></div>';
  h += '<div><div class=k><b>stored price</b><span class=ok>'+s.price_paise+' paise = ₹'+esc(s.price_rupees)+'</span></div>'
     + '<div class=k><b>footprint stored</b><span>'+s.footprint_mm+' mm</span></div>'
     + '<div class=k><b>vector</b><span>'+s.vector_dim+'-d, '+s.embed_ms+' ms</span></div>'
     + '<div class=k><b>action</b><span>'+esc(s.store_action)+(s.replaced_existing?' (replaced)':'')+'</span></div>'
     + '<div class=k><b>collision check</b><span class="'+(c.collides?'bad':'ok')+'">'+(c.collides?esc(c.message):'clear')+'</span></div></div>';
  h += '</div>';
  if(r.crop_png) h += '<div style="margin-top:12px"><div class=sub>the crop the embedder saw</div>'
     + '<img class=thumb style="width:auto;height:120px;margin-top:6px" src="data:image/png;base64,'+r.crop_png+'"></div>';
  h += '<div class=sub style="margin-top:10px">'+esc(r.money_note)+'</div></div>';
  $('#teachout').innerHTML = h;
  loadShop();
}

async function demoTeach(hard){
  $('#teachout').innerHTML = '<div class=card>teaching from SIMULATED photos — running the real enrol path on each…</div>';
  const fd = new FormData(); if(hard) fd.append('hard_pair','1');
  const r = await post('/demo/teach', fd);
  let h = '<div class="banner sim"><b>SIMULATED</b> — these scenes were rendered, not photographed. '
        + 'The mat lock, the millimetres, the descriptor and the thresholds are the real ones.</div>';
  h += '<div class=card><div class=scroll><table><tr><th>sku</th><th>result</th><th>truth</th>'
     + '<th>measured</th><th>err</th><th>price</th></tr>';
  (r.taught||[]).forEach(t=>{
    if(t.ok){
      h += '<tr><td>'+esc(t.sku_id)+'</td><td><span class="pill ok">TAUGHT</span></td>'
         + '<td class=n>'+t.truth_long_mm+' mm</td><td class=n>'+t.measured_long_mm+' mm</td>'
         + '<td class="n '+(t.err_long_mm<=1?'ok':'amb')+'">'+t.err_long_mm.toFixed(2)+'</td>'
         + '<td class=n>'+paiseFmt(t.price_paise)+'</td></tr>';
    } else {
      h += '<tr><td>'+esc(t.sku_id)+'</td><td><span class="pill bad">REFUSED</span></td>'
         + '<td colspan=4 class=bad>'+esc(t.reason)+' — '+esc(t.detail)+'</td></tr>';
    }
  });
  h += '</table></div></div>';
  $('#teachout').innerHTML = h;
  loadShop();
}

async function loadShop(){
  let r;
  try{ r = await (await fetch('/shop')).json(); }
  catch(e){ $('#shopout').innerHTML='<div class="card bad">catalog unreachable: '+esc(e)+'</div>'; return; }
  if(!r.ok){ $('#shopout').innerHTML = refusalCard(r); return; }
  if(!r.count){
    $('#shopout').innerHTML = '<div class=card><div class=head>Nothing taught yet</div>'
      + '<div class=sub>The catalog is empty, so recognition has nothing to compare against and '
      + 'every item would be amber. Teach a product above.</div></div>';
    return;
  }
  let h = '<div class=card><div class=scroll><table><tr><th></th><th>sku</th><th>name</th>'
        + '<th>price</th><th>paise</th><th>footprint</th><th>views</th><th></th></tr>';
  r.skus.forEach(s=>{
    h += '<tr><td>'+(s.thumb_png?'<img class=thumb src="data:image/png;base64,'+s.thumb_png+'">':'—')+'</td>'
       + '<td>'+esc(s.sku_id)+'</td><td>'+esc(s.name)+'</td>'
       + '<td class=n>'+(s.price_rupees?'₹'+esc(s.price_rupees):'<span class=bad>none</span>')+'</td>'
       + '<td class=n>'+(s.price_paise==null?'<span class=bad>—</span>':s.price_paise)+'</td>'
       + '<td class=n>'+s.footprint_mm+' mm</td><td class=n>'+s.n_views+'</td>'
       + '<td><button class=x onclick="removeSku(\''+esc(s.sku_id)+'\')">REMOVE</button></td></tr>';
  });
  h += '</table></div>';
  h += '<div class=k style="margin-top:8px"><b>taught</b><span>'+r.count+' sku'+(r.count>1?'s':'')
     + ', '+r.priced+' priced</span></div>';
  h += '<div class=k><b>gates</b><span>φ '+r.gates.phi+' similarity · θ '+r.gates.theta
     + ' margin · τ '+r.gates.tau_mm+' mm footprint</span></div></div>';
  $('#shopout').innerHTML = h;
}

async function removeSku(sku){
  await fetch('/shop/'+encodeURIComponent(sku), {method:'DELETE'});
  loadShop();
}

function recogniseCard(r){
  let h='';
  if(r.simulated) h += '<div class="banner sim"><b>SIMULATED</b> — rendered, not photographed. '
    + 'The lock, the millimetres, the descriptor, the thresholds and the total are all real.</div>';
  if(!r.ok) return h + refusalCard(r);
  h += '<div class=card><div class=scroll><table><tr><th>#</th><th>verdict</th><th>sku</th>'
     + '<th>measured</th><th>top1</th><th>top2</th><th>margin</th><th>price</th></tr>';
  (r.items||[]).forEach(it=>{
    const named = it.sku_id!=null;
    h += '<tr><td>'+it.id+'</td>'
       + '<td><span class="pill '+(named?'ok':'amb')+'">'+(named?'NAMED':'AMBER')+'</span></td>'
       + '<td>'+esc(named?it.sku_id:(it.top1_sku?it.top1_sku+' ?':'—'))+'</td>'
       + '<td class=n>'+(it.measured.long_edge_mm==null?'—':it.measured.long_edge_mm+' mm')+'</td>'
       + '<td class=n>'+(it.top1==null?'—':it.top1)+'</td>'
       + '<td class=n>'+(it.top2==null?'—':it.top2)+'</td>'
       + '<td class=n>'+(it.margin==null?'—':it.margin)+'</td>'
       + '<td class="n '+(named?'ok':'amb')+'">'+(named?paiseFmt(it.price_paise):'excluded')+'</td></tr>';
    if(!named) h += '<tr><td></td><td colspan=7 class=amb style="padding-top:0">'
       + '<b>'+esc(it.reason)+'</b> — '+esc(it.explain||'')+'</td></tr>';
  });
  h += '</table></div>';
  h += '<div class=total><b>total — named items only</b><span class=ok>'+paiseFmt(r.total_paise)+'</span></div>';
  h += '<div class=k><b>integer paise</b><span>'+r.total_paise+'</span></div>';
  h += '<div class=k><b>excluded</b><span class="'+(r.excluded_count?'amb':'ok')+'">'+r.excluded_count
     + ' amber item'+(r.excluded_count===1?'':'s')
     + (r.amber_reasons&&r.amber_reasons.length?' — '+esc(r.amber_reasons.join(', ')):'')+'</span></div>';
  h += '<div class=k><b>catalog</b><span>'+r.catalog_size+' taught</span></div>';
  h += '<div class=k><b>elapsed</b><span>'+r.elapsed_ms+' ms</span></div>';
  h += '<div class=sub style="margin-top:10px">'+esc(r.money_note||'')+'</div></div>';
  if(r.scoring){
    // The demo marks its own homework and shows the marks, especially the bad
    // ones. A MIS-NAMED untaught item is a confident wrong price and is the
    // single worst thing this system can do, so it is stated first and in red.
    const sc=r.scoring;
    h += '<div class="card" style="border-color:'+(sc.honest?'#2a4a35':'#5a2a24')+'">'
       + '<div class="head '+(sc.honest?'ok':'bad')+'">'
       + (sc.honest?'HONEST — no untaught item was priced':'WRONG PRICE — an untaught item was named')
       + '</div><div>'+esc(sc.headline)+'</div>';
    h += '<div class=k style="margin-top:8px"><b>named correctly</b><span class=ok>'+sc.correct+'</span></div>';
    h += '<div class=k><b>correctly abstained</b><span class=ok>'+sc.correctly_abstained+'</span></div>';
    h += '<div class=k><b>mis-named untaught</b><span class="'+(sc.mis_named_untaught?'bad':'ok')+'">'
       + sc.mis_named_untaught+'</span></div>';
    if(sc.mis_priced_paise) h += '<div class=k><b>paise that should not be billed</b><span class=bad>'
       + sc.mis_priced_paise+'</span></div>';
    h += '<div class=scroll style="margin-top:10px"><table><tr><th>truth</th><th>taught?</th>'
       + '<th>got</th><th>top1</th><th>verdict</th></tr>';
    sc.rows.forEach(x=>{
      const bad = x.verdict==='MIS_NAMED'||x.verdict==='mis_named';
      h += '<tr><td>'+esc(x.truth_sku)+'</td><td class="'+(x.taught?'ok':'amb')+'">'+(x.taught?'yes':'no')+'</td>'
         + '<td>'+esc(x.got_sku==null?'— (abstained)':x.got_sku)+'</td>'
         + '<td class=n>'+(x.top1==null?'—':x.top1)+'</td>'
         + '<td class="'+(bad?'bad':'ok')+'">'+esc(x.verdict)+'</td></tr>';
    });
    h += '</table></div></div>';
  }
  if(r.scene_truth){
    h += '<div class=card><h2>what was actually on the mat (simulated scene)</h2><div class=scroll><table>'
       + '<tr><th>sku</th><th>taught?</th><th>true long edge</th><th>placed at</th><th>turned</th></tr>';
    r.scene_truth.forEach(t=>{
      h += '<tr><td>'+esc(t.sku_id)+'</td><td class="'+(t.taught?'ok':'amb')+'">'+(t.taught?'yes':'NO — must be amber')+'</td>'
         + '<td class=n>'+t.long_edge_mm+' mm</td><td class=n>'+t.centre_mm[0]+', '+t.centre_mm[1]+' mm</td>'
         + '<td class=n>'+t.rotation_deg+'°</td></tr>';
    });
    h += '</table></div></div>';
  }
  if(r.overlay_png) h += '<div class=card><h2>rectified 840×1188 — green named, amber abstained</h2>'
     + '<img src="data:image/png;base64,'+r.overlay_png+'"></div>';
  return h;
}

async function tryIt(){
  const f = $('#r_file').files[0];
  if(!f) return;
  $('#recout').innerHTML = '<div class=card>locking, measuring, embedding, matching…</div>';
  const fd = new FormData(); fd.append('image', f);
  $('#recout').innerHTML = recogniseCard(await post('/recognise', fd));
  $('#r_file').value='';
}

async function demoRecognise(intruder){
  $('#recout').innerHTML = '<div class=card>recognising a SIMULATED scene the counter has not seen before…</div>';
  const r = await post('/demo/recognise?intruder='+(intruder?'1':'0'), new FormData());
  $('#recout').innerHTML = recogniseCard(r);
}

async function loadDeps(){
  let h;
  try{
    const d = await (await fetch('/health')).json();
    const bad = Object.entries(d.dependencies||{}).filter(([k,v])=>!v.available);
    if(!bad.length) return $('#deps').innerHTML='';
    h = '<div class="card bad"><div class=head>This page cannot teach or recognise yet</div>'
      + bad.map(([k,v])=>'<div class=k><b>'+esc(k)+'</b><span class=bad>'+esc(v.reason)+' — '+esc(v.detail||'')+'</span></div>').join('')
      + '<div class=sub style="margin-top:8px">Measurement below still works. Nothing is guessed in the meantime.</div></div>';
  }catch(e){
    h = '<div class="card bad">/health is unreachable: '+esc(e)+'</div>';
  }
  $('#deps').innerHTML = h;
}
loadDeps(); loadShop(); refState();

// /?demo performs the ENTIRE round trip on load -- set the reference, teach the
// sample products, then recognise a scene none of them was taught from. It is a
// link you can send someone who has no camera, no mat and no phone, and it is
// how the browser end of this page is tested.
async function autoDemo(){
  await demoRef();
  await demoTeach(/[?&]demo=hard/.test(location.search));
  await demoRecognise(true);
  document.body.setAttribute('data-demo-complete','1');
}
if(/[?&]demo/.test(location.search)) autoDemo();

// /?auto lands straight on the measured sample -- a link you can send someone
// who has no camera, no mat and no phone, and it shows them the real pipeline.
// /?auto=tilt and /?auto=marker land on the corresponding real refusal.
(function(){
  var m=/[?&]auto(?:=([a-z]*))?/.exec(location.search);
  if(!m)return;
  var k=m[1]||'';
  runSample(k==='tilt'?'&fail=tilt':k==='marker'?'&fail=marker':
            k==='synthetic'?'&reference=synthetic':'');
})();
</script>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


def port_in_use(host: str, port: int) -> bool:
    """True if something already owns this port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("" if host == "0.0.0.0" else host, port))
        except OSError:
            return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GAWAAH upload/measure demo")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the sample once, print the truth table, exit")
    args = ap.parse_args(argv)

    if args.selfcheck:
        r = run_sample()
        print(f"locked={r['locked']} reason={r['reason']!r} "
              f"markers={r['diagnosis']['markers_found']}/4 "
              f"items={len(r['items'])} refused={len(r['refusals'])}")
        for row in r["accuracy"]["rows"]:
            if row.get("matched"):
                print(f"  {row['name']:<16} "
                      f"long {row['measured_long_mm']:7.2f} vs {row['truth_long_mm']:6.2f} "
                      f"(err {row['err_long_mm']:.2f})  "
                      f"short {row['measured_short_mm']:7.2f} vs {row['truth_short_mm']:6.2f} "
                      f"(err {row['err_short_mm']:.2f})  "
                      f"centre err {row['err_centre_mm']:.2f} mm")
            else:
                print(f"  {row['name']:<16} NOT MATCHED")
        print(f"worst edge error {r['accuracy']['worst_edge_err_mm']} mm, "
              f"mean {r['accuracy']['mean_edge_err_mm']} mm")
        return 0 if r["locked"] and r["accuracy"]["worst_edge_err_mm"] <= 2.0 else 1

    # Pre-flight the bind. uvicorn logs a bind failure and returns normally,
    # which looks indistinguishable from a clean start -- and if something else
    # already owns the port you then measure ITS 404s and blame this tool.
    busy = port_in_use(args.host, args.port)
    if busy:
        print(f"REFUSING TO START: {args.host}:{args.port} is already in use "
              f"by another process.\n"
              f"Something else would answer your requests and you would be "
              f"reading its output, not this tool's.\n"
              f"Pick another port:  --port {args.port + 1}", file=sys.stderr)
        return 1

    import uvicorn
    print(f"GAWAAH upload -> http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
