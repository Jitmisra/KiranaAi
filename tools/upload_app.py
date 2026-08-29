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
import struct
import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


# --------------------------------------------------------------- endpoints

def _body_image(raw: bytes, content_type: str) -> bytes:
    """Accept either a raw image body or a one-part multipart/form-data body.

    Raw bytes are what the page sends (a File is a Blob, fetch takes it whole)
    and what `curl --data-binary` sends. multipart is what `curl -F` sends, and
    python-multipart is not installed here, so the one part is unwrapped by hand
    rather than adding a dependency for a demo tool.
    """
    ctype = (content_type or "").lower()
    if "multipart/form-data" not in ctype or "boundary=" not in ctype:
        return raw
    boundary = ctype.split("boundary=", 1)[1].split(";")[0].strip().strip('"')
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


PAGE = """<!doctype html><meta charset=utf-8>
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
</style>
<h1>GAWAAH — drop an image in</h1>
<div class=lead>Runs the real <code>PlaneEngine</code> and <code>PlacementDetector</code>.
No camera, no printed mat, no phone needed. Uploaded bytes are measured and dropped —
they are never stored and never sent back; only the rectified 840&times;1188 metric
buffer leaves the process.</div>
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
