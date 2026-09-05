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
import re
from urllib.parse import urlsplit
import struct
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah.identity import (  # noqa: E402
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    Gallery,
    Identifier,
    IdentityError,
)
from gawaah import detector as _det  # noqa: E402
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

#: Refusals that mean "the MAT path did not produce a result" — whether the
#: lock failed or succeeded and something later did not. Every one of them
#: should carry the offer of the weaker no-mat path, because "0 of 4 markers"
#: with nothing after it reads as a dead end. Gating the offer on the presence
#: of a diagnosis object was an accident of plumbing: two refusals raised
#: AFTER a good lock carried none and silently lost the offer.
_MAT_PATH_REFUSALS = frozenset({
    R_NO_ITEM, R_REFERENCE_REQUIRED, "no markers detected",
})

# ------------------------------------------- teaching from an ORDINARY photo
#
# The mat path above is the GOOD one and nothing below changes it. It locks a
# printed plane, so it yields MILLIMETRES, and millimetres are a second,
# independent discriminator: identity filters candidates by footprint BEFORE it
# ranks them by appearance, and the bench shows cross-product cosines of
# 0.80-0.88 that are only ever rejected because the two products are different
# SIZES.
#
# But a shopkeeper who has just downloaded a picture of a toothpaste carton has
# no mat in it, and refusing him with "no markers detected, 0 of 4" makes the
# feature unusable for the case people try first. So there is a second path,
# and it is deliberately advertised as WEAKER: the product is segmented off a
# plain background, embedded, and stored with NO footprint. At the till it is
# judged on appearance alone, at a HIGHER similarity bar, and it is marked
# appearance-only in every response and every catalogue row.
#
# Each of these is a state this path can honestly reach on a real photograph,
# and each says what to physically change. None of them is a guess.
R_MATLESS_FLAT = "matless_low_contrast"
R_MATLESS_NO_REGION = "matless_no_dominant_region"
R_MATLESS_CROPPED = "matless_region_touches_every_border"
R_MATLESS_TINY = "matless_crop_too_small"
R_MATLESS_UNSUPPORTED = "matless_catalog_cannot_hold_it"

#: How a SKU was taught. Stored, reported, and shown per row in the catalogue,
#: because "this one has no size check" is the single most important thing a
#: shopkeeper can know about an entry he is about to trust with a price.
TAUGHT_ON_MAT = "mat_measured"
TAUGHT_FROM_PHOTO = "appearance_only"

#: The gates identity uses. Named here so /health can publish them and so the
#: page can show the number a refusal was measured against. INVARIANT 7 says
#: these are never widened to make a demo look better, so they are read from
#: gawaah.identity rather than retyped.
THETA = DEFAULT_THETA
PHI = DEFAULT_PHI

#: A new view must look at least this much like the views already stored for
#: that product. Measured: the same packet turned 25 degrees still scores 0.65
#: against its taught view, and a DIFFERENT product scores 0.20-0.35 — so 0.45
#: sits in an empty band between "another angle of this" and "something else".
#: Too high and a genuinely new angle is refused, which is the whole feature;
#: too low and a bag of rice gets appended to the Parle-G gallery permanently.
ADD_VIEW_FLOOR = 0.45

#: Every view is compared against on every frame, so a gallery that grows
#: without limit slows the till for every product, not just this one.
MAX_VIEWS_PER_SKU = 12
TAU_MM = DEFAULT_TAU_MM

# The similarity bar for an entry with NO footprint.
#
# INVARIANT 7 says abstain rather than guess, and an appearance-only entry has
# lost a discriminator: nothing rules it out before the cosine is consulted, so
# every taught SKU competes against every probe. The honest response to a
# missing gate is a HIGHER bar for the entries that are missing it, never a
# lower one for everybody -- PHI stays 0.90 for footprint-gated entries and is
# read from gawaah.identity, not retyped, so this file cannot drift from it.
#
# 0.94 is derived from two measurements, not from taste:
#
#   FLOOR. results/RECOGNISE.md, "The worst impostors, named", lists the
#   different-product pairs the footprint gate ALONE rejects -- the column
#   `in footprint gate` is "no" and the cosine is what appearance would have
#   scored anyway. The worst of them is intruder_sachet vs chandrika_bar at
#   0.8797 (32.17 mm apart), then hide_seek vs lifebuoy_red at 0.8486
#   (24.75 mm apart). Those are precisely the pairs that stop being rejected
#   when an entry has no size. At the shipped phi=0.90 the worst of them sits
#   0.020 under the bar. That is the entire remaining margin, and it is thin.
#
#   CEILING. The lowest same-product cosine measured on this path -- one plain
#   photo taught, a DIFFERENT plain photo recognised, over the sample products
#   at three distances, three angles and three surfaces -- is 0.9788
#   (shampoo_sachet, view 0 -> view 2). See
#   test_the_appearance_only_gate_sits_between_two_measured_numbers, which
#   re-measures both ends and fails if this constant leaves the gap.
#
# 0.94 is inside (0.8797, 0.9788): it triples the margin over the worst
# size-only rejection, from 0.020 to 0.060, and it still clears the worst
# true match by 0.039. Measured cost on the sample set: zero abstentions.
# If the sibling library publishes its own swept constant, that one wins --
# one number, one owner.
try:                                                          # pragma: no cover
    from gawaah.identity import PHI_APPEARANCE_ONLY as _LIB_PHI_AO  # noqa: E402
except Exception:
    _LIB_PHI_AO = None
PHI_APPEARANCE_ONLY = 0.94 if _LIB_PHI_AO is None else float(_LIB_PHI_AO)
PHI_APPEARANCE_ONLY_SOURCE = ("gawaah.identity.PHI_APPEARANCE_ONLY"
                              if _LIB_PHI_AO is not None else
                              "tools/upload_app.py (library has none yet)")

# -- the segmenter's numbers, all of them fractions of the frame -------------
#: Segmentation runs at this long side. A phone photo is 2600 px after
#: decode_upload and the object boundary does not get any truer at full size --
#: it only gets slower and noisier from print texture.
MATLESS_WORK_PX = 640
#: Robust dynamic range (p98 - p2 of grey) below which there is nothing in the
#: picture to segment. A blank wall reads about 10; a carton on a table, 150+.
MATLESS_MIN_RANGE = 28
#: A blob smaller than this fraction of the frame is not "the product", it is a
#: crumb, a shadow edge or sensor noise.
MATLESS_MIN_REGION_FRAC = 0.0008
#: ...and a region that IS the biggest thing present still has to be big enough
#: to embed. Below either of these the crop is mostly interpolation.
MATLESS_MIN_CROP_FRAC = 0.02
MATLESS_MIN_CROP_PX = 64
#: The centre-crop fallback, used when the background will not separate at all.
MATLESS_CENTRE_FRAC = 0.62
#: A little air around the object, so the segmenter's boundary error does not
#: shave the packet's own edge off the crop.
MATLESS_PAD_FRAC = 0.04

# MARKER_IDS is (0, 1, 2, 3) == top-left, top-right, bottom-right, bottom-left.
CORNER_NAMES = ("top-left", "top-right", "bottom-right", "bottom-left")
CORNER_OF = dict(zip(MARKER_IDS, CORNER_NAMES))

SAMPLE_RENDER_PX_PER_MM = 4.0
SAMPLE_TILT_FRAC = 0.02          # keeps the synthetic view inside the tilt gate
SAMPLE_NOISE_SIGMA = 4.0

app = FastAPI(title="GAWAAH — upload")


# ===========================================================================
# THE LOCK, AND EXACTLY WHAT IT LEAVES OPEN
#
# `gawaah/auth.py` shipped a complete, tested guard and NOTHING APPLIED IT.
# `GET /auth/status` answered `enforced: true` the moment GAWAAH_REQUIRE_AUTH
# was set, while `GET /shop`, `GET /manage/today` and `GET /orders` all
# answered 200 to a browser that had never signed in. That is worse than an
# unlocked door: it is an unlocked door with a sign on it saying LOCKED.
#
# So `AUTH_GUARD` below goes on every router and every route in this file, and
# the six things that must stay reachable are named here rather than left to an
# environment variable somebody has to remember on each machine.
#
# WHAT STAYS OPEN, AND WHY EACH ONE HAS TO.
#
#   /                  The built page itself. Lock this and there is no screen
#                      left to sign in on — the shopkeeper cannot reach their
#                      own till. `index.html` carries no shop data.
#   /health            Liveness. A monitor, a `make` target and a shell script
#                      have no account and never will.
#   /assets/*          The hashed JS/CSS bundle the page loads. It is a
#                      StaticFiles mount and CANNOT carry a dependency at all,
#                      so this is a statement of fact as much as a policy.
#   /store*            THE SHOP'S FRONT DOOR. A customer scans the QR on the
#                      shutter, browses the catalogue, places an order and
#                      watches it. They have no account and never will. Locking
#                      this locks the shop, not the books.
#   /receipt*          The bill. `gawaah/share.py` composes "Full bill: <url>"
#                      into a WhatsApp message and sends it to the customer,
#                      and `/receipt/<id>/qr` is the code printed on the paper
#                      slip. Every one of those links is already in somebody's
#                      phone; guarding this path turns all of them into 401s.
#                      THE TRADE, SAID OUT LOUD: a receipt URL is a capability
#                      URL — anyone holding one can read that one bill, exactly
#                      as `/store/order/<id>` already works. It is one entry in
#                      the tuple below if that judgement is ever overruled.
#   /qr/link/*         The payment QR the CUSTOMER'S OWN order page draws:
#                      `POST /store/order/<id>/pay` answers with
#                      `qr_url: /qr/link/<session>`, and their phone fetches
#                      it. It sits under /qr/ rather than /store/, so the
#                      prefix has to be named separately. `/qr/<sku_id>` — the
#                      shopkeeper's sticker printer — is NOT open, and the
#                      longer prefix is what keeps them apart.
#
# Everything else is the shopkeeper's: the catalogue, the till, the books,
# stock, customers, expenses, offers, the assistant, GST, the day book, and
# every teach-and-recognise route in this file.
#
# `/auth/*` is handled by auth itself — its five way-back-in routes are open by
# definition (you cannot sign in through a guard that requires a session) and
# `/auth/invite` requires a session whether or not the switch is on.
# `GAWAAH_AUTH_OPEN` still adds to this list for an operator who needs to open
# something nobody here thought of.
#
# WITH THE SWITCH OFF — which is still the default, and still what a demo and
# twenty parallel agents need — every one of these dependencies is inert: it
# records who is signed in on `request.state.shopkeeper` and lets the request
# through. Nothing below changes what is reachable until somebody sets
# GAWAAH_REQUIRE_AUTH.
# ===========================================================================
from gawaah import auth as _auth                        # noqa: E402

#: One guard object, built once and shared. See `auth.depends_open` for why
#: this is not built per router.
AUTH_GUARD = _auth.depends_open(
    paths=("/", "/health"),
    prefixes=("/store", "/receipt", "/qr/link"),
)


# The React build. Present after `make ui`; absent in a fresh checkout, and the
# server must still start then — a missing front end is a front end to rebuild,
# not a server that refuses to boot.
UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"

# Pages whose script is INLINE, and which therefore still need 'unsafe-inline'.
# The React build does not: it ships one external, same-origin module, so the
# policy on the main site is strictly tighter than the one it replaced.
def _csp_for() -> str:
    """The Content-Security-Policy this server actually enforces.

    The page used to display `connect-src 'self'` with the sentence "enforced
    by the browser and not by our good intentions" — while no policy header was
    sent by any channel, which a headless-Chrome probe proved: cross-origin
    fetches resolved and eval ran. A security claim in the product's own trust
    copy was false. The policy now exists, is emitted on every HTML response,
    and the page's readout is templated from THIS constant so the two cannot
    drift apart again.

    `script-src` is 'self' ALONE — no exceptions left anywhere. The old inline
    page had to permit 'unsafe-inline', the single biggest hole a CSP can have,
    and three routes carried that exception for it. Deleting that page deleted
    the exception with it: the built bundle is an external same-origin module
    and needs no such permission, so there is no longer any request path in this
    server that can execute an inline script.

    `style-src` keeps 'unsafe-inline' because React writes style attributes,
    which CSP governs under style-src-attr — that is a presentational hole, not
    a script-execution one.

    There is no longer a `connect-src` entry for anything but this origin. The
    page used to be allowed to reach a second service on :8787 for the four
    capability screens; those screens are gone, so the permission goes with
    them. The list of places this page may talk to is now exactly one, which is
    short enough to read.
    """
    return ("default-src 'self'; script-src 'self'; "
            # blob: is for the teach page's OWN photo preview. Products.tsx mints
            # a blob URL from the chosen file; without this the <img> renders at
            # naturalWidth 0 and fires a securitypolicyviolation, while the copy
            # beside it promises "the preview tells you whether the code is
            # legible before you teach it". The legacy meta policy already
            # granted it; the React-era header dropped it. Grants no network reach.
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "connect-src 'self'; frame-src 'none'")


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@app.middleware("http")
async def _same_origin_only(request: Request, call_next):
    """A page on another site may not write to this counter.

    `POST /enrol` is multipart, which makes it a CORS-*simple* request: any web
    page anywhere could submit one, with no preflight, and never need to read
    the response. It calls publish_price_map(), which rewrites shop.json, which
    live_app re-stats on EVERY price lookup — so a cross-site form with
    `force=1` reprices a real shelf product on a live till, no restart needed.
    The same reachability applies to /reference, /demo/teach and the brain
    forward.

    The rule is deliberately narrow. Requests carrying NEITHER header are
    allowed, so curl, the test client and any scripting keep working — this
    closes the browser-driven hole, which is the one that exists, without
    pretending to be authentication. It is method-keyed rather than
    route-keyed so a new write endpoint is covered the day it is added.
    """
    if request.method not in SAFE_METHODS:
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        host = (request.headers.get("host") or "").lower()
        cross = False
        if site is not None and site not in ("same-origin", "none"):
            cross = True
        elif origin:
            from urllib.parse import urlsplit as _us
            netloc = _us(origin).netloc.lower()
            cross = bool(netloc) and netloc != host
        if cross:
            return JSONResponse(
                {"ok": False, "reason": "cross_origin_refused",
                 "detail": "This counter only accepts writes from its own page. "
                           f"The request declared origin {origin or site!r}."},
                status_code=403)
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    if str(resp.headers.get("content-type", "")).startswith("text/html"):
        resp.headers["Content-Security-Policy"] = _csp_for()
        # The page IS the application — markup, styles and script in one
        # response — so a cached copy is a cached version of the whole program.
        # Without this a browser keeps serving yesterday's build after a
        # deploy, and every symptom looks like a bug that was already fixed.
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


# The bundle's filenames carry a content hash, so a stale asset cannot be served
# under a name the new HTML asks for. Mounted only if it exists — see UI_DIST.
if (UI_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="assets")

# One optional empty-mat reference, supplied by the operator via POST /reference.
# It is the honest reference (see BrainConfig.reference); without it an upload
# falls back to a SYNTHESISED reference and the response says so.
_REFERENCE: dict[str, Any] = {"buffer": None, "at": None}


class UploadRefused(Exception):
    """A named refusal with a reason a human can act on.

    `diagnosis`, when given, is the TRUE lock report at the moment of refusal.
    It used to be attached only by failed locks, which turned its absence into
    an overloaded sentinel: refusals raised AFTER a successful lock (no empty
    reference; nothing measurable on the mat) fell through to a fabricated
    "0 of 4 markers, all corners missing" block — every field the exact
    inverse of the truth, misdirecting the user on a working install.
    """

    def __init__(self, reason: str, detail: str,
                 diagnosis: "Optional[dict]" = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        if diagnosis is not None:
            self.diagnosis = diagnosis


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
    # ABSENT and EMPTY are two different facts and were reported under one
    # name, producing the self-contradicting "no 'image' file part in the
    # form. Parts received: ['image']." The right name already existed and
    # was simply unreachable on this path.
    if part is not None and not part.data:
        raise UploadRefused(
            R_EMPTY_BODY,
            f"the {name!r} file part arrived with zero bytes — the capture "
            f"produced no image")
    if part is None:
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
    """gawaah.embedder2.embed, or a named refusal explaining its absence.

    ON INVARIANT 3, RESTATED FOR WHAT ACTUALLY SHIPS NOW. "No model weights in
    the browser" governs the PAGE, which still ships nothing and calls no
    third-party inference. The SERVER now embeds through 4.96 MB of Apache-2.0
    SqueezeNet weights (gawaah/embedder2.py), beside the YOLO proposer that
    already lives here — because the handcrafted descriptor's same-product and
    different-product cosine distributions overlap under real lighting change
    (measured gap −0.21), and overlapping distributions have no correct gate.
    The retired claim is "recognition is handcrafted maths end to end"; the
    kept claim is that nothing about a shop leaves this machine.
    """
    if _DEPS["embed"] is None:
        try:
            from gawaah.embedder2 import embed  # noqa: WPS433
        except Exception as exc:
            raise UploadRefused(
                R_NO_EMBEDDER,
                f"gawaah.embedder2 is not importable ({type(exc).__name__}: "
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


# ---------------------------------------- where a SKU with NO footprint lives
#
# gawaah/shop_store.py is the catalogue and it should hold every SKU, including
# the ones with no millimetres. It is being taught to (footprint_mm=None is
# becoming legal there). This app therefore ALWAYS tries the real store first
# and only falls back to a sidecar file when the installed store still insists
# on a footprint -- so on a build where the store has learned, there is one
# catalogue and the fallback is never written. Which one was used is reported
# in every response as `storage`, because a shopkeeper's catalogue silently
# living in two places is exactly the kind of thing that should be visible.

AO_SIDECAR = "appearance_only.json"
#: BUMPED 1 -> 2 on 2026-08-29, because the DESCRIPTOR changed, not the schema.
#: Mat-less crops now have their background suppressed before embedding
#: (see BG_FILL), so a vector written by format 1 describes a different picture
#: of the same packet and a cosine between the two means nothing. `_ao_load`
#: already discards a catalogue whose format it does not recognise, so bumping
#: this is what makes stale vectors unreachable rather than quietly wrong.
#:
#: What the bump alone does NOT do is say so, and a shopkeeper whose products
#: vanished between two runs is owed better than an empty list -- hence
#: `ao_superseded`, which reads the old file and reports what has to be taught
#: again. Nothing is deleted: the file stays on disk, so the names and prices
#: are recoverable even though the vectors are not usable.
AO_FORMAT = 2


def ao_path() -> Path:
    return store_dir() / AO_SIDECAR


def _ao_load() -> dict[str, Any]:
    p = ao_path()
    if not p.exists():
        return {"format": AO_FORMAT, "skus": {}}
    try:
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"format": AO_FORMAT, "skus": {}}
    if data.get("format") != AO_FORMAT or not isinstance(data.get("skus"), dict):
        return {"format": AO_FORMAT, "skus": {}}
    return data


def ao_superseded() -> dict[str, Any]:
    """Appearance-only SKUs on disk under an OLDER descriptor, by name.

    `_ao_load` refuses a catalogue it does not recognise and hands back an
    empty one, which is the correct safety behaviour and a terrible user
    experience on its own: the products are simply gone and nothing says why.
    This reads the same file WITHOUT trusting its vectors and reports what was
    lost, so the page can name the products that need teaching again instead of
    showing an empty catalogue and letting the shopkeeper conclude the counter
    forgot on its own.
    """
    p = ao_path()
    out: dict[str, Any] = {"n": 0, "format_found": None, "skus": []}
    if not p.exists():
        return out
    try:
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    found = data.get("format")
    if found == AO_FORMAT or not isinstance(data.get("skus"), dict):
        return out
    out["format_found"] = found
    for sku_id, rec in sorted(data["skus"].items()):
        # name and price are plain data and survive a descriptor change; the
        # VECTORS are what stopped meaning anything, so they are not read.
        out["skus"].append({
            "sku_id": sku_id,
            "name": str((rec or {}).get("name") or sku_id),
            "price_paise": (rec or {}).get("price_paise"),
        })
    out["n"] = len(out["skus"])
    if out["n"]:
        out["why"] = (
            "These products were taught before mat-less crops had their "
            "background suppressed. The stored descriptors describe a "
            "different picture of the same packet, so comparing against them "
            "would be meaningless rather than merely worse. Nothing was "
            "deleted and no price was lost — teach each one again from the "
            "same photograph and it will be recognised far more reliably than "
            "it was before.")
    return out


def _ao_save(data: dict[str, Any]) -> None:
    import json

    p = ao_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")),
                 encoding="utf-8")


def _ao_put(sku_id: str, name: str, price_paise: int, vectors: list[Any],
            thumb: Optional[str]) -> bool:
    """Write one appearance-only SKU to the sidecar. True if it replaced one.

    price_paise arrives as an int and is stored as an int. INVARIANT 1: this
    function never sees a rupee and never sees a float.
    """
    data = _ao_load()
    replaced = sku_id in data["skus"]
    data["skus"][sku_id] = {
        "name": name,
        "price_paise": int(price_paise),
        "vectors": [np.asarray(v, dtype=np.float64).ravel().tolist()
                    for v in vectors],
        "photo": thumb,
        "taught_with": TAUGHT_FROM_PHOTO,
        "footprint_mm": None,
    }
    _ao_save(data)
    return replaced


def _ao_remove(sku_id: str) -> bool:
    data = _ao_load()
    if sku_id not in data["skus"]:
        return False
    del data["skus"][sku_id]
    _ao_save(data)
    return True


@dataclass(frozen=True)
class TaughtSku:
    """One taught product as this app needs to see it — with or without a size.

    footprint_mm is None exactly when the SKU was taught from a photo with no
    mat in it. That None is the whole feature and it is never filled in with a
    guess, a default or a zero.
    """

    sku_id: str
    name: str
    price_paise: Optional[int]
    vectors: np.ndarray
    footprint_mm: Optional[float]
    taught_with: str
    thumb: Optional[str] = None
    storage: str = "shop_store"

    @property
    def appearance_only(self) -> bool:
        return self.footprint_mm is None

    @property
    def n_views(self) -> int:
        return int(self.vectors.shape[0])

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1])


def _footprint_of(rec: Any) -> Optional[float]:
    """The record's footprint, or None if it has none.

    Written to survive both shapes of gawaah/shop_store.py: the one where
    footprint_mm is a mandatory float, and the one where it is Optional. A
    missing attribute and a None are the same answer — no millimetres — and
    neither is ever turned into 0.0.
    """
    fp = getattr(rec, "footprint_mm", None)
    if fp is None:
        return None
    try:
        v = float(fp)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) and v > 0.0 else None


def taught_skus() -> tuple[TaughtSku, ...]:
    """Every product the counter knows, sorted, from both places it can live.

    A sku present in the real store SHADOWS a sidecar entry of the same name:
    if the store has learned to hold footprint-less SKUs between one run and the
    next, the store's copy is the truth and the stale sidecar row is ignored
    rather than competing with it.
    """
    store = load_store()
    out: dict[str, TaughtSku] = {}

    # A 461-dim vector was written by the RETIRED handcrafted embedder and
    # means nothing to the one running now — comparing across the two would
    # produce confident noise, and identity.py's own dim check would refuse a
    # mixed gallery anyway. Refusing by name here, at the vector gateway, keeps
    # every CODE path pricing (codes never touch vectors) while appearance says
    # exactly what would fix it.
    #
    # EXACTLY 461, not "anything that is not 512". The embedder is INJECTED
    # throughout this codebase — that is identity.py's contract — and the test
    # suite runs whole shops on 8- and 24-dim doubles that are internally
    # consistent and perfectly legal. The only dimension that provably means
    # "written by the retired embedder and unreadable now" is the retired
    # embedder's own.
    RETIRED_DIM = 461
    if store.dim == RETIRED_DIM:
        raise UploadRefused(
            "catalog_needs_migration",
            f"this catalogue holds {RETIRED_DIM}-dim vectors from the retired "
            f"embedder. Run ./.venv/bin/python tools/migrate_gallery.py to "
            f"re-embed every product from its stored photograph. Codes still "
            f"price; appearance cannot until then.")

    for sku_id, rec in sorted(_ao_load()["skus"].items()):
        try:
            vecs = np.asarray(rec["vectors"], dtype=np.float64)
            if vecs.ndim != 2 or vecs.shape[0] == 0:
                continue
            if vecs.shape[1] == RETIRED_DIM:
                raise UploadRefused(
                    "catalog_needs_migration",
                    f"{sku_id!r} holds {RETIRED_DIM}-dim vectors from the "
                    f"retired embedder. Run ./.venv/bin/python "
                    f"tools/migrate_gallery.py.")
            out[sku_id] = TaughtSku(
                sku_id=sku_id, name=str(rec.get("name") or sku_id),
                price_paise=int(rec["price_paise"]), vectors=vecs,
                footprint_mm=None, taught_with=TAUGHT_FROM_PHOTO,
                thumb=rec.get("photo"), storage="appearance_only_sidecar")
        except UploadRefused:
            # The stale-dim refusal above. The blanket catcher exists so one
            # CORRUPT row cannot hide the catalogue; a row that is fine but
            # written by the retired embedder is not corruption, it is a state
            # the operator must hear about, so it flies.
            raise
        except Exception:
            continue

    for rec in store.all():
        fp = _footprint_of(rec)
        how = getattr(rec, "taught_with", None) or (
            TAUGHT_ON_MAT if fp is not None else TAUGHT_FROM_PHOTO)
        out[rec.sku_id] = TaughtSku(
            sku_id=rec.sku_id, name=rec.name,
            price_paise=int(rec.price_paise),
            vectors=np.asarray(rec.vectors, dtype=np.float64),
            footprint_mm=fp, taught_with=str(how),
            thumb=_stored_thumb(store, rec.sku_id), storage="shop_store")

    return tuple(out[s] for s in sorted(out))


def _store_can_hold_a_footprint_less_sku(store: Any) -> bool:
    """Ask the installed catalogue whether None is a legal footprint.

    Asked of the module's own validator rather than by writing a probe SKU to
    the shopkeeper's catalogue and deleting it again.
    """
    try:
        from gawaah.shop_store import _require_mm  # noqa: WPS433
    except Exception:
        return False
    try:
        return _require_mm(None) is None
    except Exception:
        return False


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


# ------------------------------------------ the crop, with no mat to help us
#
# Everything here is cv2 primitives — a border-colour model, Otsu, morphology,
# connected components, minAreaRect. INVARIANT 3: no weights, no checkpoint, no
# download. It is a segmenter, not a detector: it finds THE object in a photo
# that has one object on a plain surface, and when it cannot it says so by name
# rather than handing back the middle of the picture and calling it a product.


def _work_scale(shape: tuple[int, ...]) -> float:
    """Factor that takes full-resolution pixels to segmentation pixels."""
    longest = max(int(shape[0]), int(shape[1]))
    return min(1.0, MATLESS_WORK_PX / float(max(1, longest)))


def contrast_range(bgr: np.ndarray) -> float:
    """p98 - p2 of grey. Robust: a single blown highlight or one dead pixel
    moves min/max by 255 and moves this by nothing."""
    g = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if g.size == 0:
        return 0.0
    lo, hi = np.percentile(g.astype(np.float64), (2.0, 98.0))
    return float(hi - lo)


def _tail_mean(values: np.ndarray, frac: float, floor_n: int = 24) -> float:
    """Mean of the top `frac` of `values` — a max that one hot pixel cannot move.

    Plain max() is what a stuck sensor pixel or a JPEG ringing artefact reads,
    and a percentile is blind to a real object that occupies less of the frame
    than the percentile's own tail. Averaging the top slice answers the actual
    question: is there a patch of this picture that is genuinely unlike the
    background, however small.
    """
    v = np.asarray(values).ravel()
    if v.size == 0:
        return 0.0
    n = int(max(floor_n, round(v.size * frac)))
    n = min(n, v.size)
    return float(np.mean(np.partition(v, v.size - n)[v.size - n:]))


def foreground_mask(bgr: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """A binary mask of what is NOT the background, plus the working scale.

    The background is estimated from a band around the frame edge, in Lab, by
    MEDIAN — a product photo has the product in the middle and the surface at
    the edge, and a median survives a corner of the object poking into the band.
    Every pixel then gets its Lab distance from that background colour, and Otsu
    splits the distance map.

    Distance-from-the-border rather than plain Otsu on grey, because Otsu on
    grey needs to be told the polarity: a white carton on a dark table and a
    dark bottle on white paper are opposite problems and the same call cannot
    solve both. Distance has only one polarity — far from the background is the
    object — so one threshold covers both.
    """
    k = _work_scale(bgr.shape)
    if k < 1.0:
        small = cv2.resize(bgr, (max(16, int(round(bgr.shape[1] * k))),
                                 max(16, int(round(bgr.shape[0] * k)))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = bgr.copy()

    lab = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0),
                       cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    band = max(2, int(round(0.04 * max(h, w))))
    border = np.concatenate([
        lab[:band].reshape(-1, 3), lab[-band:].reshape(-1, 3),
        lab[:, :band].reshape(-1, 3), lab[:, -band:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(lab - bg[None, None, :], axis=2)
    span = float(dist.max())
    if span <= 1e-6:
        return np.zeros((h, w), np.uint8), k, dist
    d8 = np.clip(dist * (255.0 / span), 0, 255).astype(np.uint8)
    _, mask = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # Open kills speckle (noise, dust, print grain); close fills the holes a
    # printed logo punches in the middle of a packet. Both are small and fixed:
    # a kernel big enough to bridge two separate objects would be inventing one.
    op = max(3, (int(round(min(h, w) * 0.008)) | 1))
    cl = max(5, (int(round(min(h, w) * 0.020)) | 1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (op, op)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cl, cl)))
    return mask, k, dist


def _touched_borders(label_img: np.ndarray, label: int) -> list[str]:
    sel = (label_img == label)
    out = []
    if sel[0].any():
        out.append("top")
    if sel[-1].any():
        out.append("bottom")
    if sel[:, 0].any():
        out.append("left")
    if sel[:, -1].any():
        out.append("right")
    return out


#: Background pixels inside a mat-less crop are replaced with this before the
#: crop is embedded. MEASURED, not chosen for looks -- see the table in
#: FAILURES.md. Teaching from a catalogue photo (white surround) and then
#: showing the same product to a webcam on a dark counter scored 0.50 and the
#: counter abstained on its own product; the crop is ~27% surround by area and
#: this embedder counts colour, so the surround, not the product, was deciding.
#: Zero is the right fill rather than white or grey because a uniform black
#: region contributes nothing to either the colour histograms or the edge
#: orientations -- it is absent from the descriptor rather than present as a
#: different colour. Across 10 live conditions: unmasked 1/10 named, white
#: 9/10, grey 9/10, BLACK 10/10, with the catalogue set held at 7/7 and
#: false-price at 0/22 in every one.
BG_FILL = (0, 0, 0)


def _oriented_crop_from_rect(bgr: np.ndarray, rect: tuple,
                             mask: Optional[np.ndarray] = None) -> tuple[np.ndarray, float]:
    """Upright colour crop of a rotated rectangle, long edge horizontal.

    Long-edge-horizontal is the same convention oriented_crop_bgr uses on the
    mat (w from long_edge_mm, h from short_edge_mm), so a crop taken here and a
    crop taken there are the same picture of the same packet and their vectors
    are comparable. What is NOT resolved either here or there is the remaining
    180-degree ambiguity — a rectangle has no top — which is why enrolment on
    this path stores the crop AND its 180-degree turn as two views.
    """
    (cx, cy), (rw, rh), ang = rect
    if rw < rh:
        rw, rh = rh, rw
        ang += 90.0
    pad = 1.0 + 2.0 * MATLESS_PAD_FRAC
    w = max(2, int(round(rw * pad)))
    h = max(2, int(round(rh * pad)))
    src = bgr if bgr.ndim == 3 else cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    # Warp STRAIGHT to the crop, never via a frame-sized intermediate.
    #
    # Rotating inside the source rectangle and cropping afterwards loses the
    # product whenever the upright crop is wider than the frame -- exactly the
    # case for a tall packet in a portrait photo, which is what a phone and a
    # catalogue image both produce. Measured on a real 1000x319 product photo:
    # the crop wanted 846 px of the 784 px long edge and got clamped to the
    # 319 px frame width, so 62% of the toothpaste carton was cut away and the
    # descriptor was built from a slice. It matched itself, so nothing looked
    # broken until the same item was shown at a tilt and scored 0.82.
    #
    # Composing the rotation with the translation that lands the rect centre in
    # the middle of a w-by-h output removes the intermediate, so no dimension
    # is ever clamped by the source. The identity case falls out of the same
    # expression -- a zero-degree rotation is just the translation.
    m = cv2.getRotationMatrix2D((float(cx), float(cy)), float(ang), 1.0)
    m[0, 2] += w / 2.0 - float(cx)
    m[1, 2] += h / 2.0 - float(cy)
    crop = cv2.warpAffine(src, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
    if mask is not None:
        # The silhouette rides the SAME affine as the pixels. Warping the two
        # separately with two matrices is how a mask ends up a few pixels out
        # from the thing it is supposed to cover, which shaves a rim of product
        # off one edge and leaves a rim of counter on the other.
        keep = cv2.warpAffine(mask, m, (w, h), flags=cv2.INTER_NEAREST,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        crop = crop.copy()
        crop[keep < 128] = BG_FILL
    return crop, float(ang)


def _centre_crop(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    ch = max(2, int(round(h * MATLESS_CENTRE_FRAC)))
    cw = max(2, int(round(w * MATLESS_CENTRE_FRAC)))
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2
    return bgr[y0:y0 + ch, x0:x0 + cw]


# ===========================================================================
# WHICH REGION IS THE PRODUCT
#
# THE DEFECT THIS REPLACES, measured on a real camera frame: a PONDS jar held
# up in front of the operator's face, pale wall behind, wooden cupboard and a
# dark shirt at the frame edge. The old rule took the LARGEST connected
# component of "far from the border-median colour", and on that frame the
# largest component is hair + face + hand + the jar's rim fused into one blob.
# The jar's own pale LABEL — the only part that identifies the product — landed
# within 51 Lab units of the border median, was scored as background, and was
# cut out. The stored reference came back 61.6% PURE BLACK, IoU 0.276 with the
# jar, and recognition by sight could never match it.
#
# Five independent approaches were built and benched against that frame with
# the three products that already worked as a regression set. This one:
#
#     hard case   IoU 0.276 -> 0.771,  black 61.6% -> 2.6%,  109 -> 146 ms
#     lifebuoy    0.984 -> 0.984          parle_g 0.988 -> 0.988
#     shampoo     0.991 -> 0.991          all three refusals intact
#
# The clean cases are unchanged to three decimals, which is the point: this
# does not trade the products that work for the one that did not.
#
# WHAT ACTUALLY CHANGED. Not the threshold and not the descriptor — the CHOICE.
# A product is not the biggest thing in a photograph; it is the thing that
# fills its own outline. Regions are proposed from four independent sources
# (the shipped border-median components, colour k-means, a counter-surface
# estimate, and gawaah.detector's contour/YOLO proposals) and ranked by how
# solidly each fills its own minimum-area rectangle, with the frame edge as a
# penalty rather than a disqualifier. Measured fill: the jar 0.81, the three
# working packets 0.95-1.00, the hair crescent 0.39.
#
# Background suppression STAYS ON. It was tempting to remove it — the black
# fill costs 0.33 of cosine on top of a wrong region — but measured against a
# CORRECT region across four surfaces it is worth about 0.06 of margin when the
# crop is loose, and the margin is the only quantity the cosine gate reads.
# Inpainting scored better still and was rejected: it fabricates the pixels it
# wins with, and invariant 7 says abstain rather than guess.
# ===========================================================================

#: box measured here and a box measured there have the same precision.
WORK_PX = 640
#: Colour quantisation is the one O(pixels x K x iterations) step here, and it
#: does not need the resolution: it is deciding which COLOUR a region is, not
#: where its edge falls. Half the work scale, a quarter of the pixels.
COLOUR_PX = 320
#: How many colours to quantise into. A range, not a number — the right number
#: of colours is a property of the photograph. Low K keeps a printed packet in
#: one piece; high K separates two objects of similar colour.
KMEANS_K = (3, 4, 5, 6)
#: k-means seeding is randomised, and a crop function that returns a different
#: answer on the same photograph is not a crop function.
RNG_SEED = 20240817
#: Below this fraction of the frame a region is a speck; above it, a surface.
MIN_AREA_FRAC = 0.012
MAX_AREA_FRAC = 0.92
#: Wildly elongated regions are a counter edge, a cable, or a shadow seam.
MAX_ASPECT = 6.0
#: How close to the frame edge still counts as touching it. This is the margin
#: of surface a presented product has round it.
BORDER_MARGIN_FRAC = 0.012
#: What a region that runs off the frame keeps of its score.
OUTSIDE_KEEP = 0.40
#: HOW THE FOUR CUES ARE WEIGHED. All four stay close to linear, deliberately.
#:
#: Size is sub-linear because a region twice the area of another is not twice
#: as likely to be the product. Convexity and rectangle-fill are LINEAR rather
#: than superlinear because rectangle-fill is capped at pi/4 = 0.785 for
#: anything round: squaring it punishes a jar lid or a tin twice for being
#: circular, and measured, a toothpaste tube then beat a jar in the same frame
#: 0.0755 to 0.0651. Edge agreement only shades the result — Canny has a two
#: pixel tolerance and morphology moves a boundary by about that much, so it is
#: evidence, not a verdict.
#:
#: Swept against 38 scenes plus the failing frame: the failing frame scores
#: 0.771 and the three working products stay above 0.98 at EVERY combination
#: tried, which is the reason to believe the choice is not balanced on these
#: numbers.
FRAC_E = 0.75
SOL_E = 1.0
RF_E = 1.0
EDGE_W = 0.50
#: What corroboration by an independent proposer is worth.
SUPPORT_BONUS = 0.25
#: Two top proposals overlapping less than this are different objects, not the
#: same object found twice. Only then is a second opinion worth asking for.
CONTEST_IOU = 0.50
#: ...and only when the loser is within this much of the winner.
CONTEST_RATIO = 0.70
#: How much of the returned crop must be product before it stops being pulled
#: in. See _shrink_to_cover.
CROP_COVER = 0.95
#: Rotated rectangles this close to square have no meaningful long edge, so
#: their angle is noise. See _stabilise_angle.
SQUARE_ASPECT = 1.12


# --------------------------------------------------------------- regions --

class Region:
    """One candidate: a box AND the silhouette inside it, stored locally.

    Local rather than frame-sized because a few dozen frame-sized masks is most
    of the runtime budget on its own.
    """

    __slots__ = ("x", "y", "w", "h", "m", "area", "source", "score", "hull",
                 "hull_area", "solidity", "rect_fill", "support", "edge")

    def __init__(self, x: int, y: int, m: np.ndarray, source: str):
        # TIGHT ROUND THE SILHOUETTE, ALWAYS. A proposer that hands back a box
        # wider than the thing inside it — propose_contours does, because it
        # dilates its Canny edges — would otherwise be scored, and cropped, on
        # a box that is a few pixels too big in every direction.
        bx, by, bw, bh = cv2.boundingRect(m)
        if bw > 0 and bh > 0 and (bw != m.shape[1] or bh != m.shape[0]):
            m = m[by:by + bh, bx:bx + bw]
            x, y = x + bx, y + by
        self.x, self.y = int(x), int(y)
        self.h, self.w = m.shape[:2]
        self.m = m
        self.area = int(cv2.countNonZero(m))
        self.source = source
        self.score = 0.0
        self.support = 0.0
        self.edge = 0.0
        self.hull = m
        self.hull_area = float(self.area)
        self.solidity = 1.0
        self.rect_fill = 0.0

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


def _components(binary: np.ndarray, source: str, frame_area: float,
                out: list, scale: float = 1.0) -> None:
    """Every connected piece of a binary mask, as a Region, filtered by size.

    `scale` maps this mask's coordinates onto the working frame, so proposers
    may run at whatever resolution suits them and still be compared.
    """
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    floor = MIN_AREA_FRAC * 0.6 * frame_area / (scale * scale)
    for i in range(1, n):
        if int(stats[i, cv2.CC_STAT_AREA]) < floor:
            continue
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        if w < 2 or h < 2:
            continue
        loc = np.where(lab[y:y + h, x:x + w] == i, np.uint8(255), np.uint8(0))
        if scale != 1.0:
            loc = cv2.resize(loc, (max(2, int(round(w * scale))),
                                   max(2, int(round(h * scale)))),
                             interpolation=cv2.INTER_NEAREST)
            x, y = int(round(x * scale)), int(round(y * scale))
        out.append(Region(x, y, loc, source))


def _surface_mask(small: np.ndarray) -> np.ndarray:
    """What is NOT the surface this photo was taken on.

    Background as the MODE of a coarse Lab histogram — detector.py's own
    estimator — rather than the median of a border band. Then Otsu on the
    distance map, which has one polarity (far from the surface is the object),
    so a dark bottle on white paper and a white carton on a dark table are the
    same problem and not opposite ones.
    """
    h, w = small.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2LAB)
    bg = _det._background_colour(lab)
    dist = np.linalg.norm(lab.astype(np.float32) - bg.astype(np.float32), axis=2)
    span = float(dist.max())
    if span <= 1e-6:
        return np.zeros((h, w), np.uint8)
    d8 = np.clip(dist * (255.0 / span), 0, 255).astype(np.uint8)
    _t, mask = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # Open kills print grain and sensor noise; close fills the holes a printed
    # logo punches in the middle of a packet. Both small and fixed: a kernel
    # big enough to bridge two objects would be inventing one.
    op = max(3, (int(round(min(h, w) * 0.008)) | 1))
    cl = max(5, (int(round(min(h, w) * 0.020)) | 1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (op, op)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cl, cl)))
    return mask


def _colour_regions(small: np.ndarray, frame_area: float, out: list) -> None:
    """The connected parts of each of a few quantised colours, at several K."""
    h, w = small.shape[:2]
    k = min(1.0, COLOUR_PX / float(max(h, w)))
    tiny = (cv2.resize(small, (max(8, int(round(w * k))), max(8, int(round(h * k)))),
                       interpolation=cv2.INTER_AREA) if k < 1.0 else small)
    th, tw = tiny.shape[:2]
    inv = w / float(tw)
    lab = cv2.cvtColor(cv2.GaussianBlur(tiny, (5, 5), 0), cv2.COLOR_BGR2LAB)
    z = lab.reshape(-1, 3).astype(np.float32)
    # How many colours are there actually? Asking k-means for more clusters
    # than the picture has distinct colours is an error, not a degradation, and
    # a flat test pattern really does have one. Counted on coarse bins because
    # the exact number is never needed, only whether it is bigger than K.
    q = (lab.reshape(-1, 3) // 16).astype(np.int32)
    distinct = int(np.bincount(q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]).astype(bool).sum())
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 8, 1.0)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cv2.setRNGSeed(RNG_SEED)
    for kk in KMEANS_K:
        if kk > distinct:
            continue
        _c, labels, _ctr = cv2.kmeans(z, kk, None, crit, 1, cv2.KMEANS_PP_CENTERS)
        lm = labels.reshape(th, tw)
        for i in range(kk):
            m = np.where(lm == i, np.uint8(255), np.uint8(0))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kern)
            _components(m, f"colour{kk}", frame_area, out, scale=inv)


def _counter_regions(small: np.ndarray, surface: np.ndarray, out: list) -> None:
    """detector.propose_contours, unchanged, given a silhouette to go with it.

    The module hands back boxes. A box cannot be scored by shape, so each box
    is paired with the part of the surface mask that falls inside it — which is
    the silhouette the box was drawn around in the first place.
    """
    h, w = surface.shape[:2]
    try:
        props = _det.propose_contours(small)
    except Exception:
        return
    for p in props:
        x0, y0 = max(0, p.x), max(0, p.y)
        x1, y1 = min(w, p.x + p.w), min(h, p.y + p.h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        sub = surface[y0:y1, x0:x1]
        if cv2.countNonZero(sub) < 16:
            continue
        out.append(Region(x0, y0, sub.copy(), "counter"))


def _yolo_boxes(bgr: np.ndarray, k: float) -> list:
    """Boxes only, in working coordinates. Corroboration, never a choice."""
    try:
        return [(int(p.x * k), int(p.y * k), int(p.w * k), int(p.h * k))
                for p in _det.propose_yolo(bgr)]
    except Exception:
        return []


# ------------------------------------------------------------- the choice --

def _box_iou(a, b) -> float:
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / float(a[2] * a[3] + b[2] * b[3] - i) if i > 0 else 0.0


def _measure(r: Region) -> bool:
    """Fill in the shape cues. False if the region is not measurable."""
    cnts, _ = cv2.findContours(r.m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return False
    hull = cv2.convexHull(np.vstack(cnts))
    hm = np.zeros_like(r.m)
    cv2.drawContours(hm, [hull], -1, 255, cv2.FILLED)
    r.hull = hm
    r.hull_area = float(cv2.countNonZero(hm))
    if r.hull_area < 4.0:
        return False
    r.solidity = min(1.0, r.area / r.hull_area)
    (_, _), (rw, rh), _ = cv2.minAreaRect(hull)
    ra = float(rw) * float(rh)
    r.rect_fill = min(1.0, r.hull_area / ra) if ra > 1 else 0.0
    return True


def _edge_support(r: Region, edges: np.ndarray) -> float:
    """How much of this silhouette's outline sits on image gradient.

    Measured on the region's OWN outline, never on its convex hull. The hull
    bridges every concavity with a straight line drawn across background, where
    there is nothing for it to agree with: a toothpaste tube, which narrows at
    the cap, scored 0.46 on its hull and 0.92 on its own edge, and lost to the
    red half of its own label. Concavity is already paid for once, in solidity;
    charging for it twice is what let a part beat the whole.
    """
    sub = edges[r.y:r.y + r.h, r.x:r.x + r.w]
    outline = cv2.subtract(r.m, cv2.erode(r.m, np.ones((3, 3), np.uint8)))
    n = cv2.countNonZero(outline)
    if n == 0 or sub.shape != outline.shape:
        return 0.0
    return cv2.countNonZero(cv2.bitwise_and(outline, sub)) / float(n)


def _touches_border(r: Region, h: int, w: int) -> bool:
    m = max(1, int(round(BORDER_MARGIN_FRAC * max(h, w))))
    return (r.x <= m or r.y <= m
            or r.x + r.w >= w - m or r.y + r.h >= h - m)


def _rank(regions: list, small: np.ndarray) -> list:
    h, w = small.shape[:2]
    frame_area = float(h * w)
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(grey, (5, 5), 0), 40, 120),
                       np.ones((3, 3), np.uint8))

    kept = []
    for r in regions:
        # CLAMP, DO NOT DROP. A proposer that ran at a different resolution
        # comes back a pixel or two over the edge after being scaled up, and
        # dropping those silently loses exactly the regions that sit against
        # the frame edge — which is the case the border rule below exists to
        # judge, not to hide.
        if r.x < 0 or r.y < 0 or r.x + r.w > w or r.y + r.h > h:
            x0, y0 = max(0, r.x), max(0, r.y)
            x1, y1 = min(w, r.x + r.w), min(h, r.y + r.h)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            r = Region(x0, y0,
                       r.m[y0 - r.y:y1 - r.y, x0 - r.x:x1 - r.x].copy(),
                       r.source)
        if not _measure(r):
            continue
        frac = r.hull_area / frame_area
        if frac < MIN_AREA_FRAC or frac > MAX_AREA_FRAC:
            continue
        if max(r.w, r.h) / float(max(1, min(r.w, r.h))) > MAX_ASPECT:
            continue
        r.edge = _edge_support(r, edges)
        r.score = ((frac ** FRAC_E)
                   * (r.solidity ** SOL_E)
                   * (r.rect_fill ** RF_E)
                   * (1.0 - EDGE_W + EDGE_W * r.edge))
        if _touches_border(r, h, w):
            r.score *= OUTSIDE_KEEP
            r.source += "/edge"
        kept.append(r)
    _sort(kept)
    return kept


def _sort(kept: list) -> None:
    # Ties happen — two proposers finding the same object agree to the pixel.
    # Break them on solidity, then on area, so the answer never depends on the
    # order the proposers happened to run in.
    kept.sort(key=lambda r: (-r.score * (1.0 + SUPPORT_BONUS * r.support),
                             -r.solidity, -r.area))


def _contested(kept: list) -> bool:
    """Are the top two proposals DIFFERENT objects with nearly equal claims?

    Two proposers agreeing on one object is not a contest, however close the
    numbers are — that is the same answer arrived at twice, and a second
    opinion cannot improve it. A contest is two boxes that do not overlap,
    scoring within a hair of each other. That is the only case where the
    optional detector is worth the third of the runtime it costs, so it is the
    only case it is asked.
    """
    if len(kept) < 2:
        return False
    a, b = kept[0], kept[1]
    if b.score <= 0 or a.score <= 0:
        return False
    return (_box_iou(a.box, b.box) < CONTEST_IOU
            and b.score / a.score >= CONTEST_RATIO)


# ------------------------------------------------------------- the output --

def _stabilise_angle(rect: tuple) -> tuple:
    """A square has no long edge, so do not pretend to have found one.

    _oriented_crop_from_rect turns the long edge horizontal so that a crop
    taken here and a crop taken on the mat are the same picture. When the two
    edges are within a few per cent of each other — a jar lid, a square sachet,
    a tin — which one is "long" is decided by noise, and two photographs of the
    same item then produce crops rotated differently from each other for no
    reason. Leaving a near-square upright is the only stable answer.
    """
    (cx, cy), (rw, rh), ang = rect
    lo, sh = max(rw, rh), max(1e-6, min(rw, rh))
    if lo / sh <= SQUARE_ASPECT:
        return ((cx, cy), (lo, lo), 0.0)
    return rect


def _shrink_to_cover(mask: np.ndarray, rect: tuple, scale: float,
                     target: float = CROP_COVER) -> tuple:
    """Pull the crop rectangle in until it is nearly all product.

    The shipped crop is the object's rotated rectangle plus a 4% pad, with
    everything outside the silhouette painted BG_FILL black. For a rectangular
    packet that pad is the only black in the picture, which is why the three
    working products sit at 14-15%. For anything that is NOT a rectangle — a
    jar lid, a bottle, a blister pack — the corners of the rectangle are black
    too, and black is not neutral: BG_FILL exists precisely because the
    descriptor counts what is in the crop, and 61.6% of it being black is the
    defect being fixed here. So the rectangle is shrunk about its own centre
    until the silhouette covers `target` of it.

    On a rectangular packet this is a no-op beyond dropping the pad, which was
    only ever black. On a round lid it trades the outermost ring of the rim,
    which carries no printing, for a crop that is nearly all label — and the
    label is the only part that identifies the product.

    Measured on the working mask, not at full resolution: the answer is a
    coverage ratio and it does not change with resolution, but nine full-frame
    warps do cost more than the rest of this module put together.
    """
    (cx, cy), (rw, rh), ang = rect
    pad = 1.0 + 2.0 * MATLESS_PAD_FRAC
    scx, scy, srw, srh = cx * scale, cy * scale, rw * scale, rh * scale
    best = ((cx, cy), (rw / pad, rh / pad), ang)
    for s in (1.0, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60):
        pw, ph = srw * s, srh * s
        if pw < 6 or ph < 6:
            break
        m = cv2.getRotationMatrix2D((float(scx), float(scy)), float(ang), 1.0)
        m[0, 2] += pw / 2.0 - float(scx)
        m[1, 2] += ph / 2.0 - float(scy)
        patch = cv2.warpAffine(mask, m, (int(round(pw)), int(round(ph))),
                               flags=cv2.INTER_NEAREST,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if patch.size == 0:
            break
        best = ((cx, cy), (rw * s / pad, rh * s / pad), ang)
        if cv2.countNonZero(patch) / float(patch.size) >= target:
            break
    return best


def _centre_fallback(bgr: np.ndarray, ev: dict, n_pool: int):
    """Nothing segmented. Take the middle of the frame rather than refuse — a
    jar on a patterned tablecloth is a real photograph — but ONLY if the middle
    actually has something in it. A fallback that fired on a flat picture would
    be inventing a product."""
    h_full, w_full = bgr.shape[:2]
    cc = _centre_crop(bgr)
    cc_range = contrast_range(cc)
    ev["centre_crop_contrast_range"] = round(cc_range, 1)
    if cc_range < MATLESS_MIN_RANGE:
        raise UploadRefused(
            R_MATLESS_NO_REGION,
            f"Nothing in this photograph is big enough to be the product. "
            f"{n_pool} region(s) separated from the background and none of "
            f"them is the size or the shape of an item, and the middle of the "
            f"picture is blank too — its range is {cc_range:.0f} levels. Put "
            f"ONE item on a plain surface, fill the middle of the frame with "
            f"it, and shoot again.")
    ev.update({"region_source": "centre_crop_fallback",
               "region_rule": "centre_crop_fallback",
               "region_px": [int((w_full - cc.shape[1]) // 2),
                             int((h_full - cc.shape[0]) // 2),
                             int(cc.shape[1]), int(cc.shape[0])],
               "angle_deg": 0.0, "touches_borders": [],
               "crop_px": [int(cc.shape[1]), int(cc.shape[0])]})
    return cc, ev


def plain_crop(bgr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """The product out of an ordinary photo that has NO mat in it.

    Returns (crop, evidence). Raises UploadRefused, by name, when the photo
    cannot honestly yield one — which is still the whole point of the function.
    """
    if bgr is None or getattr(bgr, "ndim", 0) != 3 or bgr.shape[2] != 3:
        raise UploadRefused(R_MATLESS_FLAT, "That is not a colour photograph.")

    h_full, w_full = bgr.shape[:2]
    frame_range = contrast_range(bgr)
    ev: dict[str, Any] = {
        "frame_px": [int(w_full), int(h_full)],
        "frame_contrast_range": round(frame_range, 1),
        "min_contrast_range": MATLESS_MIN_RANGE,
    }
    if frame_range < MATLESS_MIN_RANGE:
        raise UploadRefused(
            R_MATLESS_FLAT,
            f"This photograph has almost nothing in it: its light-to-dark "
            f"range is {frame_range:.0f} levels out of 255, and anything under "
            f"{MATLESS_MIN_RANGE} is a blank surface, not a product. Nothing "
            f"was segmented and nothing was stored. Fill more of the frame "
            f"with the item and make sure it is lit.")

    k = min(1.0, WORK_PX / float(max(h_full, w_full)))
    if k < 1.0:
        small = cv2.resize(bgr, (max(16, int(round(w_full * k))),
                                 max(16, int(round(h_full * k)))),
                           interpolation=cv2.INTER_AREA)
        k = small.shape[1] / float(w_full)
    else:
        small, k = bgr, 1.0
    sh, sw = small.shape[:2]
    frame_area = float(sh * sw)

    pool: list = []
    surface = _surface_mask(small)
    _components(surface, "surface", frame_area, pool)
    _counter_regions(small, surface, pool)
    _colour_regions(small, frame_area, pool)
    try:
        bmask, bk, _bd = foreground_mask(small)
        if bmask.shape[:2] != small.shape[:2]:
            bmask = cv2.resize(bmask, (small.shape[1], small.shape[0]),
                               interpolation=cv2.INTER_NEAREST)
        _components(bmask, "border", frame_area, pool)
    except Exception:
        pass
    ev["proposals"] = len(pool)

    ranked = _rank(pool, small)
    ev["proposals_kept"] = len(ranked)
    if not ranked:
        return _centre_fallback(bgr, ev, len(pool))

    # A SECOND OPINION, ONLY WHEN THE FIRST ONE IS NOT DECISIVE. The optional
    # ONNX proposer costs about a third of this function's runtime and it
    # cannot name anything, so it is not run to confirm an uncontested answer.
    ev["second_opinion"] = _contested(ranked)
    if ev["second_opinion"]:
        boxes = _yolo_boxes(bgr, k)
        ev["second_opinion_boxes"] = len(boxes)
        for r in ranked:
            r.support = max([_box_iou(r.box, b) for b in boxes], default=0.0)
        _sort(ranked)

    best = ranked[0]
    ev["region_source"] = f"proposal:{best.source}"
    ev["region_rule"] = "best_objectness"
    ev["region_score"] = round(best.score, 4)
    ev["region_cues"] = {"area_frac": round(best.hull_area / frame_area, 4),
                         "solidity": round(best.solidity, 3),
                         "rect_fill": round(best.rect_fill, 3),
                         "edge_support": round(best.edge, 3),
                         "yolo_support": round(best.support, 3)}
    ev["region_rank"] = [{"source": r.source, "box": [int(v) for v in r.box],
                          "score": round(r.score, 4)} for r in ranked[:5]]

    inv = 1.0 / max(k, 1e-9)
    ev["region_px"] = [int(round(best.x * inv)), int(round(best.y * inv)),
                       int(round(best.w * inv)), int(round(best.h * inv))]

    touches = []
    if best.y <= 0:
        touches.append("top")
    if best.y + best.h >= sh:
        touches.append("bottom")
    if best.x <= 0:
        touches.append("left")
    if best.x + best.w >= sw:
        touches.append("right")
    ev["touches_borders"] = touches
    if len(touches) == 4:
        raise UploadRefused(
            R_MATLESS_CROPPED,
            f"The object runs off all four edges of this photograph, so I "
            f"cannot see where it ends and I would be describing a piece of "
            f"it. Re-shoot it further back, with a margin of plain surface all "
            f"the way round the item.")

    solo_small = np.zeros((sh, sw), np.uint8)
    solo_small[best.y:best.y + best.h, best.x:best.x + best.w] = best.hull
    ys, xs = np.nonzero(best.hull)
    if len(xs) < 3:
        return _centre_fallback(bgr, ev, len(pool))
    rect_s = cv2.minAreaRect(
        np.column_stack([xs + best.x, ys + best.y]).astype(np.float32))
    rect_s = _stabilise_angle(rect_s)
    rect = ((rect_s[0][0] * inv, rect_s[0][1] * inv),
            (rect_s[1][0] * inv, rect_s[1][1] * inv), rect_s[2])

    long_px, short_px = max(rect[1]), min(rect[1])
    box_frac = (long_px * short_px) / float(max(1, w_full * h_full))
    ev["region_long_px"] = int(round(long_px))
    ev["region_short_px"] = int(round(short_px))
    ev["region_box_frac"] = round(box_frac, 5)
    if short_px < MATLESS_MIN_CROP_PX or box_frac < MATLESS_MIN_CROP_FRAC:
        raise UploadRefused(
            R_MATLESS_TINY,
            f"The biggest thing in this photograph is only "
            f"{long_px:.0f}x{short_px:.0f} px, which is "
            f"{box_frac * 100:.2f}% of the frame. A crop that small is mostly "
            f"interpolation and the descriptor built from it would be noise "
            f"(the floor is {MATLESS_MIN_CROP_PX} px on the short side and "
            f"{MATLESS_MIN_CROP_FRAC * 100:.0f}% of the frame). Move the "
            f"camera closer, or crop the picture to the item.")

    rect = _shrink_to_cover(solo_small, rect, k)
    solo = cv2.resize(solo_small, (w_full, h_full), interpolation=cv2.INTER_NEAREST)
    out, ang = _oriented_crop_from_rect(bgr, rect, mask=solo)
    ev.update({"background": "suppressed",
               "angle_deg": round(ang % 180.0, 1),
               "crop_px": [int(out.shape[1]), int(out.shape[0])]})
    return out, ev


#: How the caller says WHO decided where the product is. Absent means nobody
#: did and `plain_crop` must work it out; `user_drawn` means a person dragged a
#: rectangle around the item before uploading it.
REGION_USER_DRAWN = "user_drawn"


def read_region(form: dict[str, Any]) -> str:
    """`region=user_drawn` says the picture IS the rectangle a human drew.

    The default is the empty string — "nobody has answered this, segment it" —
    because the strong claim has to be made out loud. A page that forgot to
    send the field gets the careful path, never the trusting one.
    """
    raw = str(form_value(form, "region") or "").strip().lower()
    if raw in ("user_drawn", "user-drawn", "hand_drawn", "operator", "drawn"):
        return REGION_USER_DRAWN
    return ""


def hand_drawn_crop(bgr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """A region a HUMAN drew, taken as the segmentation it already is.

    WHY THIS IS A SEPARATE FUNCTION AND NOT A FLAG INSIDE `plain_crop`
    -----------------------------------------------------------------
    `plain_crop` answers two questions at once: WHERE is the product in this
    photograph, and IS THAT ANSWER TRUSTWORTHY. Its four-border refusal
    (`R_MATLESS_CROPPED`) is the second question doing its job — a region that
    runs off every edge might be the product, or a piece of something bigger,
    or the wall, and the segmenter genuinely cannot tell, so it abstains.

    That gate is LOAD-BEARING on the uncropped path and is not touched here.
    FAILURES.md: a stored reference that was 58.5% pure black because the
    background estimate fell on the wrong side of the product. Deleting the
    border check would make that class of failure silent again.

    But an operator who drags a rectangle around a packet has already answered
    the first question, with their eyes, and this build's own UI tells them to
    answer it TIGHTLY: "DRAW A BOX AROUND THE PRODUCT — only the box is checked
    and taught". A tight box means the product fills it, so of course the
    product touches all four borders. Doing exactly what the screen asked is
    what triggered the refusal, and a refusal you earn by complying is a bug in
    the refusal.

    Re-segmenting inside that box is also asking the weaker instrument to
    overrule the stronger one, in the precise condition the weaker one is
    documented to fail: when the product fills the frame the border band IS the
    product, the background estimate equals the product's own colour, and
    FAILURES.md records two of three real product photographs being refused
    outright for it. So on this path the rectangle is the region. Nothing is
    re-segmented and, because the region came from a human rather than from a
    mask, nothing is masked out either — background suppression is a remedy for
    a LOOSE region (measured: with a right region, no-fill beats black fill
    0.702 to 0.533 cosine) and a hand-drawn box is not loose.

    WHAT IS STILL REFUSED — THIS IS NOT "ACCEPT ANYTHING"
    -----------------------------------------------------
    Every refusal that is about the IMAGE rather than about the segmentation
    survives, by name and unchanged:

      * not a colour photograph at all           -> R_MATLESS_FLAT
      * one flat colour, a blank wall, an empty
        frame, a box drawn over nothing          -> R_MATLESS_FLAT
      * too few pixels to build a descriptor on  -> R_MATLESS_TINY

    A person can draw a box around a bare wall, and this still says no and says
    why. What it no longer does is refuse a good box for being a good box.

    Returns (crop, evidence). The evidence records `region_source` and states
    that the border gate was skipped and by whose authority, so a stored SKU can
    always be traced back to which of the two paths produced it.
    """
    if bgr is None or getattr(bgr, "ndim", 0) != 3 or bgr.shape[2] != 3:
        raise UploadRefused(R_MATLESS_FLAT, "That is not a colour photograph.")

    h_full, w_full = bgr.shape[:2]
    frame_range = contrast_range(bgr)
    ev: dict[str, Any] = {
        "frame_px": [int(w_full), int(h_full)],
        "frame_contrast_range": round(frame_range, 1),
        "min_contrast_range": MATLESS_MIN_RANGE,
        "region_source": "operator_rectangle",
        "region_rule": "the box the operator drew IS the region",
        "region_px": [0, 0, int(w_full), int(h_full)],
        "angle_deg": 0.0,
        # Reported honestly rather than suppressed: it IS true that the region
        # meets all four borders. What changed is who is entitled to conclude
        # something from that, and the next line says so in the audit record.
        "touches_borders": ["top", "bottom", "left", "right"],
        "border_gate": (
            "not applied — a person drew this region, so a region that fills "
            "it is the intended answer and not a failed segmentation"),
        "background": "kept",
        "crop_px": [int(w_full), int(h_full)],
    }
    if frame_range < MATLESS_MIN_RANGE:
        raise UploadRefused(
            R_MATLESS_FLAT,
            f"The box you drew has almost nothing in it: its light-to-dark "
            f"range is {frame_range:.0f} levels out of 255, and anything under "
            f"{MATLESS_MIN_RANGE} is a blank surface, not a product. Drawing a "
            f"rectangle says WHERE the item is; it cannot put one there. "
            f"Nothing was stored. Draw the box around the packet itself, and "
            f"make sure it is lit.")

    short_px = min(int(w_full), int(h_full))
    if short_px < MATLESS_MIN_CROP_PX:
        raise UploadRefused(
            R_MATLESS_TINY,
            f"The box you drew is only {w_full}x{h_full} px. A crop that small "
            f"is mostly interpolation and the descriptor built from it would be "
            f"noise (the floor is {MATLESS_MIN_CROP_PX} px on the short side). "
            f"Move the camera closer to the item and draw the box again.")

    return bgr, ev


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


# ----------------------------------------- a photo with NO mat in it at all
#
# What the user actually did: downloaded a picture of a carton on a plain
# background. There is no mat, no marker and no scale, so nothing here can be
# expressed in millimetres — which is exactly the point. These stand-ins are
# RENDERED, never photographed, and everything built from them is stamped
# SIMULATED (INVARIANT 7).


@dataclass(frozen=True)
class PlainView:
    """One ordinary photograph of one product: how big it happens to look, how
    it happens to be turned, where it happens to sit, and on what surface."""

    px_per_mm: float
    rot_deg: float
    centre: tuple[float, float]        # fractions of the frame
    bg: tuple[int, int, int]           # BGR of the plain surface
    seed: int


#: Three genuinely different photographs of the same object. Different distance
#: (so different pixel size), different angle, different position, different
#: surface colour and independent noise. Nothing about a match between two of
#: these can be explained by re-presenting the same pixels.
PLAIN_VIEWS: tuple[PlainView, ...] = (
    PlainView(6.0, 0.0, (0.50, 0.50), (238, 240, 242), 5),
    PlainView(4.6, 17.0, (0.43, 0.56), (246, 247, 249), 9),
    PlainView(7.4, -11.0, (0.57, 0.45), (222, 226, 231), 13),
)


def plain_photo(p: SampleProduct, view: int = 0, *,
                size: tuple[int, int] = (900, 1200),
                noise: float = 3.0) -> np.ndarray:
    """One product on a plain surface. No mat, no markers, no millimetres."""
    v = PLAIN_VIEWS[int(view) % len(PLAIN_VIEWS)]
    w, h = int(size[0]), int(size[1])
    img = np.empty((h, w, 3), np.uint8)
    img[:, :] = v.bg
    # A gentle top-to-bottom fall-off: a perfectly flat background is a
    # laboratory, and a segmenter that only works on one would be a lie.
    ramp = np.linspace(8.0, -8.0, h, dtype=np.float32)[:, None, None]
    img = np.clip(img.astype(np.float32) + ramp, 0, 255).astype(np.uint8)

    _paste_rotated(img, render_product(p, v.px_per_mm),
                   w * v.centre[0], h * v.centre[1], v.rot_deg)

    if noise > 0:
        n = np.random.default_rng(_seed32(v.seed)).normal(0, noise, img.shape)
        img = np.clip(img.astype(np.int16) + n, 0, 255).astype(np.uint8)
    return img


def plain_photo_png(p: SampleProduct, view: int = 0, **kw) -> bytes:
    ok, buf = cv2.imencode(".png", plain_photo(p, view, **kw))
    if not ok:                                            # pragma: no cover
        raise UploadRefused(R_INTERNAL, "could not encode the simulated photo")
    return buf.tobytes()


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
    # No diagnosis carried means NO CLAIM ABOUT THE LOCK — not a claim that it
    # failed. This branch used to synthesise "0 of 4 markers, every corner
    # missing, locked: false" for refusals raised after a SUCCESSFUL lock,
    # inverting every field of the truth. The page already renders cleanly
    # without the block (`if(d.markers_found!=null)`), so honesty costs nothing.
    return JSONResponse({
        "ok": False,
        "locked": False,
        "reason": exc.reason,
        "detail": exc.detail,
        "settles_money": False,
        "items": [],
        "refusals": [],
    }, status_code=status)


@app.get("/health", dependencies=AUTH_GUARD)
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
        "identity_gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                           "phi_appearance_only": PHI_APPEARANCE_ONLY},
        "teaching_modes": {
            MODE_MAT: {
                "footprint": "measured in millimetres off the printed mat",
                "phi": PHI,
                "size_check": "footprint_gated",
                "note": "the good path — a wrong-sized lookalike is refused "
                        "before appearance is consulted",
            },
            MODE_PLAIN: {
                "footprint": None,
                "phi": PHI_APPEARANCE_ONLY,
                "size_check": "none",
                "note": APPEARANCE_ONLY_WARNING,
                "refusals": [R_MATLESS_FLAT, R_MATLESS_NO_REGION,
                             R_MATLESS_CROPPED, R_MATLESS_TINY],
                "phi_source": PHI_APPEARANCE_ONLY_SOURCE,
                "catalog_holds_footprint_less_skus": _catalog_holds_them(),
            },
        },
        "model_weights": "none — invariant 3; the descriptor is classical cv2",
        "store_dir": str(store_dir()),
    })


def _catalog_holds_them() -> Optional[bool]:
    """Whether gawaah/shop_store.py itself can keep a SKU with no footprint.

    False does not mean the mode is unavailable — this app keeps those SKUs in
    a sidecar beside the catalogue instead — but it does mean they are in two
    files rather than one, and that is worth publishing rather than hiding.
    """
    try:
        return _store_can_hold_a_footprint_less_sku(load_store())
    except Exception:
        return None


@app.get("/sample", dependencies=AUTH_GUARD)
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


@app.post("/codes", dependencies=AUTH_GUARD)
async def codes_ep(request: Request) -> JSONResponse:
    """multipart: image -> every printed code in it, with where and how big.

    Its own endpoint because AIMING is a different job from teaching. A
    shopkeeper holding a packet up needs to know NOW whether the barcode is
    readable, not after pressing TEACH and being told nothing was bound. This
    is what the camera preview polls while it is open, so the box lights up the
    moment the code becomes legible and the operator stops moving.

    When nothing decodes it says WHAT WOULD HELP, in the measured numbers: an
    EAN-13 needs about 220 px of frame width (1.95 px per module) and nothing
    under 180 px decoded at all in testing, and past roughly 12 degrees of tilt
    a barcode stops being readable while a QR keeps going.
    """
    try:
        form = await read_form(request)
        bgr, note = decode_upload(form_image(form))
        t0 = time.perf_counter()
        found = decode_all_codes(bgr)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        h, w = bgr.shape[:2]
        rows = []
        for c in found:
            box = c["box"] or [0, 0, 0, 0]
            wide = max(int(box[2]), int(box[3]))
            rows.append({
                "payload": c["payload"], "format": c["format"], "box": c["box"],
                "px_across": wide,
                # WHICH pass read it. "direct" was square-on and big enough;
                # "deskewed" means the packet was at an angle and had to be
                # flattened; "upscaled" means it was small. Worth showing,
                # because it names what the operator could change.
                "read_by": c.get("read_by"),
                "sku_id": resolve_code(c["payload"]),
            })
        out: dict[str, Any] = {
            "ok": True, "settles_money": False,
            "frame_px": [int(w), int(h)],
            "codes": rows, "count": len(rows),
            "elapsed_ms": ms,
        }
        if not rows:
            seen = barcode_like_regions(bgr)
            out["candidates"] = seen
            if seen:
                out["hint"] = (
                    f"A barcode is IN FRAME but not readable yet — keep turning it "
                    f"towards the camera. On a round bottle the bars are squashed "
                    f"as it rolls away: measured on this build, a curved label "
                    f"reads within about 20 degrees of facing you and nothing "
                    f"recovers it past 30. Turn it flat, and closer.")
                return JSONResponse(out)
            out["hint"] = (
                "No printed code was readable in this frame — MOVE CLOSER. "
                "Measured on this build: a barcode reads at ANY angle once it "
                "is about 220 px wide in frame, and square-on down to about "
                "180 px. Under 150 px nothing decodes whatever the angle — "
                "there are not enough pixels left per bar. Fill roughly a "
                "fifth of the frame width with the code itself.")
        return JSONResponse(out)
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "codes": [], "count": 0}, status_code=400)


SCAN_TTL_S = 900


def scans_dir() -> Path:
    return store_dir().parent / "scans"


def _bp(x: Any) -> Optional[int]:
    """A 0..1 similarity as integer basis points, or None. 0.6439 -> 6439.

    The money service may hold no float, so every number that has to be
    COMPARED there crosses the wire as an int.
    """
    try:
        return int(round(float(x) * 10000))
    except (TypeError, ValueError):
        return None


def _witness_lines(raw: bytes, bgr: Any) -> tuple[list[dict[str, Any]],
                                                  list[dict[str, Any]], int,
                                                  list[dict[str, Any]]]:
    """The counter, read for the record: (lines, amber, witnessed_paise, unnamed).

    WHAT COUNTS AS AMBER HERE, AND WHY IT IS NOT EVERYTHING UNNAMED.

    A DECODED CODE that cannot be priced is amber and refuses the mint. That
    rule does not move: a code is a measurement. Something was printed on a
    packet, this counter read it, and it could not put a price on it — so the
    bill is short by exactly one real product, and a bill short by silence
    looks exactly like a complete one.

    A REGION THE PROPOSER FOUND AND THE GALLERY COULD NOT NAME is a different
    animal, and treating it as amber is what made this endpoint unusable. The
    proposer is class-agnostic on purpose; on a real counter it returns the
    shopkeeper's hand, their sleeve, the edge of the mat and whatever is on the
    wall behind. Refusing every charge that has a hand in frame is not caution,
    it is a till that cannot take money. Those regions are recorded as
    `unnamed` — they are in the witness, they are in the audit, and the page
    shows them — but they do not refuse on their own.

    The guarantee that survives is the one that matters: `witnessed_paise` is
    the sum of what this counter could NAME and PRICE, and the charge is
    refused unless it equals the bill. An item the camera failed to name simply
    is not in that sum, so it cannot pay for itself.
    """
    codes_found = decode_all_codes(bgr)
    lines: list[dict[str, Any]] = []
    amber: list[dict[str, Any]] = []
    unnamed: list[dict[str, Any]] = []
    witnessed = 0

    try:
        read = do_counter(raw)
        items = read.get("items") or []
    except UploadRefused as exc:
        # Nothing taught yet: fall back to the codes-only reading this endpoint
        # has always done, rather than refusing a charge outright.
        if exc.reason != R_EMPTY_GALLERY:
            raise
        items = []
        known = priced_skus()
        for i, c in enumerate(codes_found):
            sku = resolve_code(c["payload"])
            rec = known.get(sku) if sku else None
            row = {"id": i, "code": c["payload"], "format": c["format"],
                   "box": c["box"], "read_by": c.get("read_by"),
                   "named_by": "code"}
            if rec is None:
                row.update({"sku_id": None, "name": None, "price_paise": None,
                            "reason": ("code_not_taught" if sku is None
                                       else "code_names_a_missing_product")})
                amber.append(row)
            else:
                row.update({"sku_id": rec["sku_id"], "name": rec["name"],
                            "price_paise": int(rec["price_paise"]),
                            "reason": "code_exact"})
                witnessed += int(rec["price_paise"])
            lines.append(row)
        return lines, amber, witnessed, unnamed

    for i, it in enumerate(items):
        how = str(it.get("how") or "")
        by_code = how == "code" or it.get("code") is not None
        row: dict[str, Any] = {
            "id": i,
            "code": it.get("code") or "",
            "box": it.get("box"),
            "named_by": "code" if by_code else "appearance",
            "reason": it.get("reason"),
        }
        if it.get("price_paise") is not None and it.get("sku_id"):
            row.update({"sku_id": str(it["sku_id"]), "name": it.get("name"),
                        "price_paise": int(it["price_paise"])})
            if not by_code:
                # The EVIDENCE, carried to the money service so it can hold the
                # gate itself rather than take a name on trust.
                #
                # IN BASIS POINTS, AS INTEGERS. paisa is a strict no-float
                # module — `tools/lint_no_float.py` fails the build on a
                # `float()` inside it — and that rule is not worth bending for a
                # similarity score. 0.6439 travels as 6439 and the money service
                # compares two ints, which is also the only comparison that
                # cannot round differently on the two sides of the wire. The
                # decimal forms are kept beside them for the page to print.
                row["top1"] = it.get("top1")
                row["phi_used"] = it.get("phi_used")
                row["top1_bp"] = _bp(it.get("top1"))
                row["phi_bp"] = _bp(it.get("phi_used"))
            witnessed += int(it["price_paise"])
            lines.append(row)
        elif by_code:
            row.update({"sku_id": None, "name": None, "price_paise": None})
            amber.append(row)
            lines.append(row)
        else:
            row.update({"sku_id": None, "name": None, "price_paise": None,
                        "detail": it.get("detail")})
            unnamed.append(row)

    return lines, amber, witnessed, unnamed


@app.post("/counter/entered", dependencies=AUTH_GUARD)
async def counter_entered_ep(request: Request) -> JSONResponse:
    """A bill the SHOPKEEPER entered, witnessed as exactly that.

    THE CAMERA IS NOT ALWAYS THE ANSWER, AND PRETENDING IT IS MAKES A TILL YOU
    CANNOT USE. A shopkeeper says "do Maggi aur ek Parle-G", accepts the lines,
    and has a correct bill in front of them -- and CHARGE stayed dead, because
    the only evidence this counter knew how to mint against was a photograph.
    Loose goods, a product taught by code with the label facing away, anything
    the lens cannot resolve: all uncharegable. That is not caution, it is a
    counter that cannot take money.

    SO THIS RECORDS WHO SAID SO, AND DOES NOT PRETEND OTHERWISE. `kind` is
    `counter_entered`, `read_by` is `shopkeeper`, the id is prefixed `ent`, and
    `evidence` says in words that no camera was involved. A person reading the
    scans directory can tell a photograph from a typed bill at a glance. That
    is the same bargain `gawaah/storefront.py` already struck for an order
    placed on a phone -- see `_write_witness` there, which this deliberately
    mirrors rather than reinvents.

    AND IT BYPASSES NOTHING. There is ONE mint path. This witness is loaded by
    `paisa.load_scan_witness` and re-priced by `paisa.rerun_scan` like any
    other, so every guard still stands: each line is re-resolved through
    paisa's own binding table, re-priced from paisa's OWN book, a line it
    cannot price BLOCKS the mint as `amber_in_basket`, and one paisa of
    disagreement refuses. The browser sends sku ids and counts. It does not
    send prices, and no price it could send would be read.
    """
    import datetime as _dt
    import json
    import secrets

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _refusal(UploadRefused(R_FIELD_MISSING, "expected a JSON body"))
    if not isinstance(body, dict):
        return _refusal(UploadRefused(R_FIELD_MISSING, "expected a JSON object"))

    raw_lines = body.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return _refusal(UploadRefused(
            R_FIELD_MISSING,
            "`lines` must be a non-empty list of {sku_id, qty}. A bill with no "
            "lines is not a bill."))
    if len(raw_lines) > 200:
        return _refusal(UploadRefused(
            R_FIELD_MISSING, "a counter bill is capped at 200 lines."))

    known = priced_skus()
    out: list[dict[str, Any]] = []
    witnessed = 0
    i = 0
    for ln in raw_lines:
        if not isinstance(ln, dict):
            return _refusal(UploadRefused(R_FIELD_MISSING, "each line must be an object"))
        sku = str(ln.get("sku_id") or "").strip()
        try:
            qty = int(ln.get("qty"))
        except (TypeError, ValueError):
            return _refusal(UploadRefused(
                R_FIELD_MISSING, f"line {sku!r} has no whole count on it."))
        if not sku or qty < 1 or qty > 999:
            return _refusal(UploadRefused(
                R_FIELD_MISSING,
                f"line {sku!r} needs a sku and a count between 1 and 999."))
        rec = known.get(sku)
        if rec is None:
            # Named, and refused rather than dropped: a bill quietly one line
            # short looks exactly like a complete one.
            return _refusal(UploadRefused(
                R_NOT_TAUGHT,
                f"{sku!r} is not a product this counter has taught with a "
                f"price, so it cannot be put on a bill. Nothing was written."))
        unit = int(rec["price_paise"])
        for _ in range(qty):
            out.append({
                "id": i, "code": f"{QR_PREFIX}{sku}", "format": "COUNTER",
                "box": None, "read_by": "shopkeeper", "sku_id": sku,
                "name": rec.get("name"), "price_paise": unit,
                "qty_on_the_bill": qty, "reason": "entered_by_the_shopkeeper",
            })
            witnessed += unit
            i += 1

    scan_id = "ent" + secrets.token_hex(9)
    doc = {
        "scan_id": scan_id,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "kind": "counter_entered",
        "source": "till",
        "read_by": "shopkeeper",
        "evidence": ("a bill entered at the counter by the shopkeeper and "
                     "priced by this till from its own catalogue; no camera "
                     "was involved and no frame was decoded"),
        "frame_sha256": None,
        "frame_px": None,
        "codes_found": len(out),
        "distinct_codes": len({ln["code"] for ln in out}),
        "lines": out,
        "witnessed_paise": witnessed,
    }
    d = scans_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{scan_id}.json").write_text(
            json.dumps(doc, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        return _refusal(UploadRefused(
            R_INTERNAL,
            f"this bill's witness could not be written to {d} ({exc}). Nothing "
            f"was minted, because there would have been nothing for the money "
            f"service to re-price."))

    return JSONResponse({
        "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
        "scan_id": scan_id, "kind": "counter_entered",
        "witnessed_paise": witnessed, "witnessed_rupees": rupees_str(witnessed),
        "lines": len(out),
        "note": ("Entered at the counter, not photographed. The money service "
                 "re-prices every line from its own book before it mints."),
    })


@app.post("/scan", dependencies=AUTH_GUARD)
async def scan_ep(request: Request) -> JSONResponse:
    """multipart: image -> a WITNESS this counter wrote down, under an id.

    THE BROWSER IS NEVER AN AUTHOR. It sends pixels and receives an id; it is
    given no field in which to assert a payload, a sku or a price. The witness
    is written here — decoded, resolved through this counter's binding table,
    priced from its own catalogue — and persisted. To charge, the page sends
    that id and a total; the money service loads the same witness BY ID and
    re-derives every rupee from ITS own tables before minting.

    That is why this exists as a separate endpoint from /recognise: recognise
    is a look at the counter, and this is a RECORD that can be charged against.
    """
    import datetime as _dt
    import hashlib
    import json
    import secrets

    try:
        form = await read_form(request)
        raw = form_image(form)
        bgr, note = decode_upload(raw)
        t0 = time.perf_counter()

        # THE WITNESS READS THE COUNTER THE WAY THE COUNTER IS READ.
        #
        # This endpoint decoded printed codes and NOTHING ELSE for its whole
        # life, while the live loop beside it named products by appearance. On
        # a shop where 34 of 36 products carry no printed label — which is what
        # `seed_shop.py` builds and what teaching from a photograph produces —
        # that split meant a product could be recognised, priced and put on the
        # bill, and then be invisible to the photograph taken to charge it:
        # `codes_found: 0`, `witnessed_paise: 0`, `chargeable: false`, and a
        # CHARGE button that never armed. The bug was not in the recogniser
        # (ponds scores 0.81 against a 0.60 gate); it was that the money path
        # never asked it.
        #
        # `do_counter` is the same whole-counter read the Shelf and the counter
        # button use, and it takes codes FIRST — an identifier that was read is
        # a measurement, an appearance match is an opinion, and where they
        # disagree the code wins. So this is the strictly wider reading, not a
        # different one.
        lines, amber, witnessed, unnamed = _witness_lines(raw, bgr)
        elapsed = round((time.perf_counter() - t0) * 1000, 1)

        scan_id = "scn_" + secrets.token_hex(10)
        doc = {
            "scan_id": scan_id,
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),

            "frame_px": [int(bgr.shape[1]), int(bgr.shape[0])],
            "frame_sha256": hashlib.sha256(raw).hexdigest(),
            "codes_found": sum(1 for ln in lines if ln.get("named_by") == "code"),
            "distinct_codes": len({str(ln.get("code") or "") for ln in lines
                                   if ln.get("named_by") == "code" and ln.get("code")}),
            # Regions the proposer found and the gallery could not name. Kept
            # in the witness so the audit shows everything that was on the
            # counter, NOT counted as amber — see `_witness_lines`.
            "unnamed": unnamed,
            # EVERY decoded line, including the ones this till could not price.
            # Filing them under a separate key hid them from the money
            # service's own amber check, and a basket holding one untaught code
            # MINTED for the price of the rest — the silent undercharge this
            # whole design exists to prevent. paisa re-resolves each payload
            # itself; it is not told which are good.
            "lines": lines,
            "witnessed_paise": witnessed,
        }
        d = scans_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{scan_id}.json").write_text(
            json.dumps(doc, sort_keys=True, separators=(",", ":")), encoding="utf-8")

        return JSONResponse({
            "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
            "scan_id": scan_id,
            "codes_found": doc["codes_found"], "distinct_codes": doc["distinct_codes"],
            "items": lines, "amber": amber, "unnamed": unnamed,
            "counts": {# NAMED means priced. Counting every line here made `named` include the
            # amber ones, so /legacy printed "2 line(s), Rs 10.00" for a counter
            # where exactly one line had a price. The sibling do_recognise_basket
            # computes it correctly — one file shipped two meanings.
            "named": len(doc["lines"]) - len(amber), "amber": len(amber)},
            "witnessed_paise": witnessed,
            "witnessed_rupees": rupees_str(witnessed),
            "total_paise": witnessed, "total_rupees": rupees_str(witnessed),
            "elapsed_ms": elapsed,
            "chargeable": bool(doc["lines"]) and not amber,
            "why_not_chargeable": (
                None if (doc["lines"] and not amber) else
                ("nothing on this counter could be priced" if not doc["lines"] else
                 f"{len(amber)} line(s) cannot be priced. They are NOT dropped "
                 f"from the total — the charge is refused until each one is "
                 f"taught or removed, because a bill that is short by silence "
                 f"looks exactly like a complete one.")),
            "input": note,
        })
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "items": [], "amber": []}, status_code=400)


@app.post("/waapsi/scan", dependencies=AUTH_GUARD)
async def waapsi_scan_ep(request: Request) -> JSONResponse:
    """WAAPSI: a return by camera. multipart image of a packet AND the
    customer's receipt QR, held up together -> the SKU(s) on the packet and
    the bill the receipt names.

    THE RECEIPT QR IS GAWAAH'S OWN BOOKMARK, NOT A PAYMENT PAYLOAD. It carries
    this counter's `/receipt/{session_id}/page` address and nothing else —
    `receipts.receipt_url` refused to encode anything else — so reading the
    session id back out of it is reading our own link, not parsing a payment
    payload (invariant 1). `receipt_session_from_payload` proves the shape and
    refuses a UPI string or a gateway host by name; the money service then
    resolves the session against the signed audit chain before a paisa moves,
    which is the real proof that this counter billed it.

    This endpoint mints NOTHING and refunds NOTHING. It reads pixels and
    returns names and a session id; the person presses REFUND on the next
    screen, and only the gateway's signed webhook turns a refund REFUNDED.
    """
    try:
        form = await read_form(request)
        raw = form_image(form)
        bgr, note = decode_upload(raw)
        found = decode_all_codes(bgr)

        known = priced_skus()
        # WHICH decoded codes are OUR receipt bookmark, and which are product
        # codes on the packet. A code is at most one of these: the receipt URL
        # matches a strict path shape, a product code resolves through the
        # binding table. Anything that is neither is reported, never guessed.
        receipt_session: Optional[str] = None
        receipt_payload: Optional[str] = None
        items: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []
        for c in found:
            payload = c.get("payload") or ""
            sid = _receipts.receipt_session_from_payload(payload)
            if sid is not None:
                # The first receipt code wins; a second, different one is
                # reported rather than chosen between — two receipts held up
                # at once is a question for a person.
                if receipt_session is None:
                    receipt_session, receipt_payload = sid, payload
                elif sid != receipt_session:
                    others.append({"code": payload, "reason": "second_receipt_code",
                                   "box": c.get("box")})
                continue
            sku = resolve_code(payload)
            rec = known.get(sku) if sku else None
            row = {"code": payload, "format": c.get("format"),
                   "box": c.get("box"), "read_by": c.get("read_by"),
                   "sku_id": sku, "name": (rec or {}).get("name"),
                   "price_paise": (int(rec["price_paise"]) if rec else None),
                   "price_rupees": (rupees_str(int(rec["price_paise"])) if rec else None),
                   "reason": ("code_exact" if rec is not None else
                              "code_not_taught" if sku is None else
                              "code_names_a_missing_product")}
            items.append(row)

        return JSONResponse({
            "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
            "frame_px": [int(bgr.shape[1]), int(bgr.shape[0])],
            "codes_found": len(found),
            # The bill this return is against, off our own receipt QR. None
            # when no receipt code was in frame — the page then asks for the
            # receipt to be held up too, because a return with no bill is a
            # figure nobody witnessed.
            "receipt_session": receipt_session,
            "receipt_payload": receipt_payload if receipt_session else None,
            # The packet(s) on the counter, priced where taught, amber where
            # not — the same shape /scan returns, so the return page reads one.
            "items": items,
            "other_codes": others,
            "counts": {
                "priced": sum(1 for r in items if r["price_paise"] is not None),
                "amber": sum(1 for r in items if r["price_paise"] is None),
            },
            "input": note,
            "note": (
                "Hold up the packet and the paper receipt's QR in the same "
                "frame. The receipt code is this counter's own bill link; the "
                "money service checks it against the signed audit chain before "
                "any refund is asked for."),
        })
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "items": [], "receipt_session": None}, status_code=400)


@app.post("/analyse", dependencies=AUTH_GUARD)
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


@app.post("/reference", dependencies=AUTH_GUARD)
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


@app.delete("/reference", dependencies=AUTH_GUARD)
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
            "The mat LOCKED — this refusal is not about the markers. Teaching "
            "needs a photograph of the EMPTY mat first: without one the "
            "background is synthesised from the printed design and does not "
            "cancel exactly, so the mat's own printed scale patch and arrow are "
            "indistinguishable from a small product — and the largest blob on "
            "an empty mat is the mat itself. Use SET EMPTY-MAT REFERENCE under "
            "SETUP (or POST the photo to /reference), then teach.",
            diagnosis=lock_info.get("diagnosis"))

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
               f"Place the item on the mat and re-shoot."),
            diagnosis=lock_info.get("diagnosis"))

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
            # The bar this SKU must clear at the till. The plain path names
            # its raised 0.92 here; the strong path omitting its 0.90 made the
            # TAUGHT card print an em-dash exactly where the weaker instrument
            # prints its number — the page's own message, inverted.
            "phi_used": PHI,
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


# ------------------------------- teaching and pricing with NO millimetres
#
# Identity's own ladder is: filter by footprint, rank by cosine, then phi, then
# the top-2 margin. With no mat there is no footprint on either side of the
# comparison, so the first rung is simply not there. Everything below is the
# SAME ladder minus that rung, at a HIGHER phi -- never a lower one -- and it
# says so in every answer it gives.

#: Plain English for a shopkeeper, attached to every appearance-only answer.
APPEARANCE_ONLY_WARNING = (
    "APPEARANCE ONLY. This product was taught from a photograph with no mat in "
    "it, so nothing here knows how BIG it is. There is no size check: it is "
    "matched on looks alone, at a stricter similarity bar "
    f"({PHI_APPEARANCE_ONLY:.2f} instead of {PHI:.2f}) precisely because a "
    "discriminator is missing. It is easier to confuse with a similar-looking "
    "product of a different size — a 100 g packet and the 500 g packet of the "
    "same brand look identical to it. BETTER: put the item on the printed mat "
    "and teach it again. That measures it in millimetres, and a SKU with "
    "millimetres is refused before appearance is even consulted when the size "
    "is wrong."
)

APPEARANCE_ONLY_ALTERNATIVE = (
    "Print the TAKHTI mat, photograph the EMPTY mat once and POST it to "
    "/reference, then photograph the item alone on the mat and teach it again "
    "with the same sku_id. The mat-taught entry replaces this one and gets a "
    "real footprint."
)


def taught_gallery(records: tuple[TaughtSku, ...]) -> Gallery:
    """A gawaah.identity Gallery over every taught SKU, sizes and all.

    footprint_mm=None goes in as None. The library is the one place that knows
    what to do with that — skip the metric filter, judge on appearance, apply
    the higher bar — and this app does not re-implement the ladder. One number,
    one owner: if PHI_APPEARANCE_ONLY moves in gawaah/identity.py, it moves
    here with no edit.
    """
    g = Gallery()
    for r in records:
        g.enroll(r.sku_id, r.vectors, r.footprint_mm)
    return g


def matless_identifier(records: tuple[TaughtSku, ...],
                       embed: Callable[[np.ndarray], Any],
                       *, drop: Optional[str] = None) -> Identifier:
    """The real Identifier over the merged catalogue.

    `drop` removes one sku, for the enrolment guard: an item must never be
    found to collide with its own outgoing entry.
    """
    keep = tuple(r for r in records if r.sku_id != drop)
    return Identifier(taught_gallery(keep), embed,
                      theta=THETA, phi=PHI, tau_mm=TAU_MM)


def _two_orientations(crop: np.ndarray) -> list[np.ndarray]:
    """The crop and its 180-degree turn.

    A rotated rectangle has no top. minAreaRect cannot tell a packet lying
    head-up from the same packet lying head-down, and neither can the mat path
    (placement reports angle_deg in [0, 180)). Rather than pretend, enrolment
    here stores BOTH turns as two views and lets the gallery's best-of scoring
    pick whichever the till photo happens to produce. It costs one embed and it
    is the difference between a round trip that works and one that works half
    the time.
    """
    return [crop, cv2.rotate(crop, cv2.ROTATE_180)]

# TILT AUGMENTATION WAS TRIED HERE AND REMOVED, 2026-08-29.
#
# Four stored tilts (+/-9, +/-18 deg) and a 12% tighter framing were added to
# this path on the theory that an unrectified hand-held photo needs them. On
# the real product photo they moved a 12-degree tilt from 0.8478 to 0.8621 --
# still an abstain, so they bought no recall at all -- while pulling the worst
# impostor from 0.6692 up to 0.7195 and spending a fifth of the safety
# headroom. The tilt was never the disease. _oriented_crop_from_rect was
# truncating the crop against the frame width; with that fixed the two turns
# alone name 15 of 16 views including every tilt out to 90 degrees.
#
# Kept as a comment because the tempting fix and the real one look nothing
# alike here, and the measurement is the only thing that told them apart.


def _appearance_only_block(*, extra: Optional[dict[str, Any]] = None
                           ) -> dict[str, Any]:
    out = {
        "appearance_only": True,
        "taught_with": TAUGHT_FROM_PHOTO,
        "size_check": "none",
        "footprint_mm": None,
        "phi_used": PHI_APPEARANCE_ONLY,
        "phi_footprint_gated": PHI,
        "warning": APPEARANCE_ONLY_WARNING,
        "better": APPEARANCE_ONLY_ALTERNATIVE,
    }
    if extra:
        out.update(extra)
    return out


def do_enrol_plain(raw: bytes, sku_id: str, name: str, price_paise: int,
                   *, force: bool = False, hand_drawn: bool = False
                   ) -> dict[str, Any]:
    """One ORDINARY photo + a name + a price -> one appearance-only SKU.

    No mat, so no millimetres, so no footprint — and the entry is stored with
    footprint_mm=None rather than with a guessed one. A guessed footprint would
    be worse than none at all: identity would filter candidates against a
    number nobody measured, and the filter would be confidently wrong instead
    of honestly absent.

    `hand_drawn` says the upload IS a rectangle a person dragged around the
    product. It changes ONE thing — who decided where the product is — and the
    two functions below are the whole of that difference. See
    `hand_drawn_crop`'s docstring for why it is not a widened `plain_crop`.
    """
    embed = load_embedder()
    store = load_store()

    bgr, note = decode_upload(raw)
    # Both of these raise, by name, when the picture cannot honestly yield a
    # descriptor. Only the segmentation-specific refusals differ.
    crop, region = (hand_drawn_crop(bgr) if hand_drawn else plain_crop(bgr))

    t0 = time.perf_counter()
    views = _two_orientations(crop)
    try:
        vectors = [np.asarray(embed(c), dtype=np.float64).ravel() for c in views]
    except Exception as exc:
        raise UploadRefused(
            R_NO_EMBEDDER,
            f"gawaah.embedder.embed failed on a {crop.shape[1]}x{crop.shape[0]} "
            f"crop: {type(exc).__name__}: {exc}") from None
    embed_ms = round((time.perf_counter() - t0) * 1000, 2)

    known = taught_skus()
    # The SAME guard the mat path runs, asked with no footprint. It is
    # STRICTER that way, not looser: with no millimetres on one side, size
    # cannot let a same-looking pair through, so appearance alone decides.
    try:
        collision = matless_identifier(
            known, embed, drop=sku_id).check_collision(vectors, None)
    except IdentityError as exc:
        raise UploadRefused(R_IDENTITY, f"{exc}") from None
    verdict = collision.to_audit()
    verdict["message"] = collision.message
    if collision.collides and not force:
        raise UploadRefused(
            R_COLLISION,
            f"Refusing to teach {sku_id!r} from a plain photo: it is "
            f"indistinguishable from {collision.sku_id!r} — cosine "
            f"{collision.similarity:.4f}, bar {1.0 - THETA:.2f}, and NO size "
            f"was measured on either side. On the mat these two could still "
            f"have been told apart if they are different sizes; with no mat "
            f"there is nothing left to separate them and both would be "
            f"permanently amber at the till. Photograph a different face of "
            f"the packet, or teach this one on the mat.")

    replaced_existing = any(r.sku_id == sku_id for r in known)
    # Two sizes on purpose: the page gets a readable crop back once, the
    # catalogue keeps a small one for ever. The sidecar is JSON on disk and a
    # 220 px crop per SKU would make a twenty-product shop several megabytes.
    shown = _thumb_png(crop, 220)
    thumb = _thumb_png(crop, 96)

    storage = "shop_store"
    action = "added"
    result_reason = "sku_added"
    photo_action = "none"
    photo_size = 0
    previous_paise = None
    for r in known:
        if r.sku_id == sku_id:
            previous_paise = r.price_paise

    if _store_can_hold_a_footprint_less_sku(store):
        try:
            result = store.add_sku(sku_id, name, int(price_paise), vectors,
                                   None, photo_png=crop)
        except MoneyError:
            raise
        except Exception as exc:
            raise UploadRefused(
                R_MATLESS_UNSUPPORTED,
                f"The catalogue accepted a null footprint from its validator "
                f"but refused the write: {type(exc).__name__}: {exc}. Nothing "
                f"was stored.") from None
        if not result.ok:
            raise UploadRefused(
                R_COLLISION if result.collides_with else result.reason,
                f"{result.message or result.reason}"
                + (f" (colliding with {result.collides_with!r})"
                   if result.collides_with else ""))
        action = result.action
        result_reason = result.reason
        photo_action = result.photo_action
        photo_size = int(result.photo_bytes)
        previous_paise = result.previous_price_paise
        _ao_remove(sku_id)          # never leave two copies of one sku
    else:
        # The installed catalogue still requires millimetres. Rather than
        # invent some, this app keeps the footprint-less entries beside it and
        # SAYS SO in the response, so the split is visible instead of silent.
        if sku_id in store:
            raise UploadRefused(
                R_MATLESS_UNSUPPORTED,
                f"{sku_id!r} already exists in the mat-measured catalogue, "
                f"which in this build cannot hold an entry with no footprint. "
                f"Re-teaching it from a plain photo would leave two entries "
                f"claiming the same sku. Remove it first, or teach it on the "
                f"mat.")
        storage = "appearance_only_sidecar"
        was = _ao_put(sku_id, name, int(price_paise), vectors, thumb)
        action = "replaced" if was else "added"
        result_reason = "sku_replaced" if was else "sku_added"
        photo_action = "stored" if thumb else "none"
        photo_size = len(base64.b64decode(thumb)) if thumb else 0

    return {
        "ok": True,
        "settles_money": False,
        "money_note": MONEY_NOTE,
        "locked": False,
        "mode": "plain_photo",
        "reason": "taught_without_a_mat",
        "reference_source": "not_applicable_no_mat",
        "ids_found": [],
        "measured": {
            "footprint_mm": None,
            "millimetres": "none — there is no mat in this photograph, so "
                           "nothing in it has a known size",
            **region,
        },
        "stored": {
            "sku_id": sku_id,
            "name": name,
            "price_paise": int(price_paise),
            "price_rupees": rupees_str(price_paise),
            "footprint_mm": None,
            "n_views": len(vectors),
            "views_note": "the crop and its 180-degree turn — a rectangle has "
                          "no top, so both are stored",
            "vector_dim": int(vectors[0].shape[0]),
            "replaced_existing": bool(replaced_existing),
            "embed_ms": embed_ms,
            "store_action": action,
            "store_reason": result_reason,
            "photo_action": photo_action,
            "photo_bytes": int(photo_size),
            "previous_price_paise": previous_paise,
            "storage": storage,
            **_appearance_only_block(),
        },
        "collision": verdict,
        "forced": bool(force and collision.collides),
        "crop_png": shown,
        "catalog_size": len(taught_skus()),
        "input": note,
        "source_image_returned": False,
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                  "phi_appearance_only": PHI_APPEARANCE_ONLY},
        **_appearance_only_block(),
    }


#: A GAWAAH product sticker carries this prefix and nothing else. Bare sku ids
#: are accepted too, so a QR generated elsewhere still works, but the prefix is
#: what lets a shopkeeper tell OUR sticker from the dozen other QR codes already
#: printed on a packet -- a UPI code, a warranty link, a marketing campaign.
QR_PREFIX = "gawaah:"
R_QR_UNKNOWN = "qr_names_an_unknown_sku"
#: The same shape shop_store demands of an sku id. A payload without our prefix
#: has to at least look like one before it is treated as naming a product.
_QR_BARE_SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


#: Where a manufacturer's printed code is bound to a taught sku. Kept in a file
#: this app owns rather than in the catalogue record, so the binding works the
#: same whether the sku lives in the real store or the appearance-only sidecar.
CODES_SIDECAR = "product_codes.json"
CODES_FORMAT = 1
R_CODE_UNKNOWN = "code_names_an_unknown_product"


def codes_path() -> Path:
    return store_dir() / CODES_SIDECAR


def _codes_load() -> dict[str, str]:
    """{printed code -> sku id}. Never raises; a broken file is an empty map."""
    p = codes_path()
    if not p.exists():
        return {}
    try:
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt binding file must not kill the till
        return {}
    if data.get("format") != CODES_FORMAT or not isinstance(data.get("codes"), dict):
        return {}
    return {str(k): str(v) for k, v in data["codes"].items() if k and v}


def _codes_save(codes: dict[str, str]) -> None:
    import json

    p = codes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"format": CODES_FORMAT, "codes": codes},
                            sort_keys=True, separators=(",", ":")),
                 encoding="utf-8")


def bind_code(code: str, sku_id: str) -> dict[str, Any]:
    """Bind one printed code to one sku. Returns what changed, by name.

    A code binds to exactly ONE sku. Rebinding is allowed and reported, because
    a shopkeeper who scans the wrong packet while teaching needs to be able to
    fix it -- but it is never silent: a code that quietly changed which product
    it prices is a wrong bill with an audit trail that says nothing happened.
    """
    codes = _codes_load()
    prev = codes.get(code)
    codes[code] = sku_id
    _codes_save(codes)
    return {"code": code, "sku_id": sku_id,
            "action": "rebound" if prev and prev != sku_id else
                      ("unchanged" if prev == sku_id else "bound"),
            "previous_sku": prev}


def unbind_sku(sku_id: str) -> list[str]:
    """Forget every code bound to a sku. Returns the codes dropped."""
    codes = _codes_load()
    gone = [c for c, s in codes.items() if s == sku_id]
    if gone:
        for c in gone:
            codes.pop(c, None)
        _codes_save(codes)
    return gone


def resolve_code(payload: str) -> Optional[str]:
    """The sku a decoded payload names, or None.

    Two routes, in order. A `gawaah:` sticker carries the sku id itself and
    needs no binding. Anything else -- the manufacturer's EAN-13, a Code128, a
    QR the brand printed -- means nothing until a shopkeeper has TAUGHT what it
    is, so it is looked up in the binding table and is otherwise unknown.
    """
    s = (payload or "").strip()
    if s.lower().startswith(QR_PREFIX):
        return s[len(QR_PREFIX):].strip() or None
    return _codes_load().get(s)


def load_zxing():
    """zxing-cpp, or a named refusal. Pure algorithm, no model weights."""
    if _DEPS.get("zxing") is None:
        try:
            import zxingcpp  # noqa: WPS433
        except Exception as exc:  # noqa: BLE001
            raise UploadRefused(
                "barcode_reader_unavailable",
                f"zxing-cpp is not importable ({type(exc).__name__}: {exc}), so "
                f"printed product codes cannot be read. Appearance still works.",
            ) from None
        _DEPS["zxing"] = zxingcpp
    return _DEPS["zxing"]


def decode_all_codes(bgr: np.ndarray) -> list[dict[str, Any]]:
    """EVERY printed code in one picture, with where each one is.

    A shopper puts a whole basket on the counter at once, so reading one code
    per frame would turn a supermarket into a queue of one. zxing scans the
    whole frame and returns every symbol it finds; MEASURED on a 1400x760
    counter holding three EAN-13 packets and one of our own QR stickers, all
    four decode together in about 40 ms.

    What it will NOT do is stated here so nobody has to discover it at a till:
    an EAN-13 needs roughly 220 px of frame width (1.95 px per module) and
    nothing under 180 px decoded at all; past about 12 degrees of tilt, or under
    real motion blur, a barcode stops being readable while a QR keeps going.
    """
    try:
        zx = load_zxing()
    except UploadRefused:
        return []

    out: list[dict[str, Any]] = []

    def _iou(a, b) -> float:
        if not a or not b:
            return 0.0
        ax0, ay0, aw, ah = a
        bx0, by0, bw, bh = b
        x0, y0 = max(ax0, bx0), max(ay0, by0)
        x1, y1 = min(ax0 + aw, bx0 + bw), min(ay0 + ah, by0 + bh)
        inter = max(0, x1 - x0) * max(0, y1 - y0)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    def _box_of(r):
        try:
            q = r.position
            xs = [q.top_left.x, q.top_right.x, q.bottom_right.x, q.bottom_left.x]
            ys = [q.top_left.y, q.top_right.y, q.bottom_right.y, q.bottom_left.y]
            return [int(min(xs)), int(min(ys)),
                    int(max(xs) - min(xs)), int(max(ys) - min(ys))]
        except Exception:  # noqa: BLE001 - position is best-effort
            return None

    def _take(results, how, back=None):
        """Record each new symbol, in ORIGINAL frame coordinates.

        `back` maps a point from whatever image was decoded into the frame the
        caller handed in. Without it a deskewed crop reports its box in the
        CROP's space, IoU against the direct pass is then 0, and the same
        physical packet is recorded twice — measured: a three-item basket
        billed six lines and doubled the total.

        Deduped by POSITION, never by payload: two identical packets carry the
        same barcode, and keying on the text bills one of them silently.
        """
        for r in results:
            text = getattr(r, "text", "") or ""
            if not text:
                continue
            box = _box_of(r)
            if box and back is not None:
                x0, y0 = back(box[0], box[1])
                x1, y1 = back(box[0] + box[2], box[1] + box[3])
                box = [int(min(x0, x1)), int(min(y0, y1)),
                       int(abs(x1 - x0)), int(abs(y1 - y0))]
            # A DECODED SYMBOL HAS TO HAVE PLAUSIBLE GEOMETRY. The deskew
            # pass can hand zxing a degenerate strip and get a checksum-valid
            # EAN-13 back out of noise — observed: payload 0190000000008 in a
            # box 276x2 px. It was harmless there only because nothing was
            # bound to that number; bound, it would have priced a product that
            # is not on the counter. A barcode this program can read is at
            # least ~150 px across (measured floor) and a QR ~70 px, so a
            # symbol claiming less than that, or only a few pixels tall, is a
            # misread and not a packet.
            if box is not None:
                bw, bh = int(box[2]), int(box[3])
                if min(bw, bh) < 5 or max(bw, bh) < 40:
                    continue
            if any(d["payload"] == text and _iou(d["box"], box) > 0.5 for d in out):
                continue
            out.append({"payload": text, "format": str(getattr(r, "format", "")),
                        "box": box, "read_by": how})

    # PASS 1 — the frame as it arrived. Cheapest, and enough whenever the code
    # is square-on and big.
    try:
        _take(zx.read_barcodes(bgr), "direct")
    except Exception:  # noqa: BLE001 - a bad frame must never kill the loop
        return out

    # PASS 2 — LOCATE, DESKEW, UPSCALE. zxing's own try_rotate only covers
    # 90-degree steps, so a packet held at an angle defeats it entirely: at 25
    # and 45 degrees the direct pass reads nothing at any size, and this pass
    # reads it at 220 px and up. ~7 ms.
    _candidates: list = []
    try:
        det = cv2.barcode.BarcodeDetector()
        ok, pts = det.detect(bgr)
        if ok and pts is not None:
            for quad in np.asarray(pts, dtype=np.float32).reshape(-1, 4, 2):
                (cx, cy), (bw, bh), ang = cv2.minAreaRect(quad)
                if bw < bh:
                    bw, bh = bh, bw
                    ang += 90.0
                W = max(24, int(bw * 1.35))
                H = max(16, int(bh * 1.35))
                m = cv2.getRotationMatrix2D((float(cx), float(cy)), float(ang), 1.0)
                m[0, 2] += W / 2.0 - cx
                m[1, 2] += H / 2.0 - cy
                flat = cv2.warpAffine(bgr, m, (W, H), flags=cv2.INTER_CUBIC,
                                      borderMode=cv2.BORDER_REPLICATE)
                if flat.size == 0:
                    continue
                _candidates.append(flat)
                big = cv2.resize(flat, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                inv = cv2.invertAffineTransform(m)

                def _back(px, py, _inv=inv):
                    fx, fy = px / 3.0, py / 3.0          # undo the upscale
                    return (_inv[0, 0] * fx + _inv[0, 1] * fy + _inv[0, 2],
                            _inv[1, 0] * fx + _inv[1, 1] * fy + _inv[1, 2])

                before = len(out)
                _take(zx.read_barcodes(big), "deskewed", back=_back)
                if len(out) == before:
                    # local contrast, for glare and a dark bottle
                    grey = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
                    grey = cv2.createCLAHE(2.5, (8, 8)).apply(grey)
                    _take(zx.read_barcodes(grey), "deskewed_contrast", back=_back)
    except Exception:  # noqa: BLE001 - a locator failure is not a frame failure
        pass

    # PASS 2b — NARROW HORIZONTAL BANDS of each candidate. A 1D symbol is read
    # along a scanline, and on a barcode wrapped round a bottle some bands are
    # much less foreshortened than the whole label. Measured: recovers a 60-deg
    # label rolled 20 deg, which every other pass misses. Past ~30 deg of roll
    # nothing recovers it — the bars are compressed below the decoder's floor
    # and that is geometry, not an algorithm choice.
    if not out and _candidates:
        try:
            for flat in _candidates[:2]:
                grey = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
                h = grey.shape[0]
                for i in range(0, 10, 2):
                    y0, y1 = int(h * i / 10), int(h * (i + 3) / 10)
                    if y1 - y0 < 8:
                        continue
                    band = cv2.resize(grey[y0:y1], None, fx=3, fy=3,
                                      interpolation=cv2.INTER_CUBIC)
                    _take(zx.read_barcodes(band), "band")
                    if out:
                        break
                if out:
                    break
        except Exception:  # noqa: BLE001
            pass

    # PASS 3 — RESAMPLE THE WHOLE FRAME, cheapest rung first.
    #
    # Measured over 33 QR conditions and the barcode set, not chosen by feel:
    #
    #   2x  recovers 5 of 7 hard cases in 24 ms — a 55 px QR that aliases at
    #       native size, and blur out to sigma 25
    #   3x  recovers 3 in 52 ms, and is what a small square-on BARCODE needs
    #       (180 px reads at 3x and nowhere else)
    #   4x  recovers 4 in 91 ms and adds nothing 2x and 3x do not
    #
    # So 2x runs before 3x: it is both cheaper AND strictly better on the QR
    # set, which is the opposite of the "bigger is better" guess this code
    # originally shipped with.
    for factor, label in ((2.0, "upscaled_2x"), (3.0, "upscaled_3x")):
        if out:
            break
        try:
            big = cv2.resize(bgr, None, fx=factor, fy=factor,
                             interpolation=cv2.INTER_CUBIC)
            _take(zx.read_barcodes(big), label,
                  back=lambda px, py, _f=factor: (px / _f, py / _f))
        except Exception:  # noqa: BLE001
            pass

    # PASS 4 — SHARPEN, then 2x. Only blur responds to this, and only blur
    # beyond what the resample rungs recover: an unsharp mask restores the
    # module EDGES a soft lens threw away, where interpolation alone only
    # makes the softness bigger.
    if not out:
        try:
            grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            soft = cv2.GaussianBlur(grey, (0, 0), 3.0)
            sharp = cv2.addWeighted(grey, 1.9, soft, -0.9, 0)
            big = cv2.resize(sharp, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            _take(zx.read_barcodes(big), "sharpened",
                  back=lambda px, py: (px / 2.0, py / 2.0))
        except Exception:  # noqa: BLE001
            pass

    return out


def _barcode_candidate_boxes(bgr: np.ndarray) -> list:
    """Axis-aligned boxes around barcode-SHAPED regions, decoded or not.

    Shares OpenCV's detector with `barcode_like_regions`, which reports the
    count for the aiming hint. This returns the geometry so the curved-label
    rung can work on the label alone instead of sweeping the whole frame.

    Like its sibling this is NOT evidence: a region that was seen but not read
    is not a line on any bill. It only decides WHERE to look harder.
    """
    try:
        ok, pts = cv2.barcode.BarcodeDetector().detect(bgr)
        if not ok or pts is None:
            return []
        boxes = []
        for quad in np.asarray(pts).reshape(-1, 4, 2):
            x0, y0 = quad[:, 0].min(), quad[:, 1].min()
            x1, y1 = quad[:, 0].max(), quad[:, 1].max()
            w, h = int(x1 - x0), int(y1 - y0)
            if w < 24 or h < 8:
                continue
            boxes.append((int(x0), int(y0), w, h))
        return boxes[:4]           # more than four is a texture, not a counter
    except Exception:  # noqa: BLE001
        return []


def barcode_like_regions(bgr: np.ndarray) -> int:
    """How many barcode-SHAPED regions are in this frame, decoded or not.

    Aiming only — never money. "Nothing decoded" and "a barcode is right there
    but too turned to read" are different messages: the first means move on,
    the second means keep turning. Kept out of `decode_all_codes` because that
    function's output becomes a bill, and a region that was seen but not read
    is not a line.
    """
    try:
        ok, pts = cv2.barcode.BarcodeDetector().detect(bgr)
        if not ok or pts is None:
            return 0
        return int(len(np.asarray(pts).reshape(-1, 4, 2)))
    except Exception:  # noqa: BLE001
        return 0


def decode_sku_qr(bgr: np.ndarray) -> tuple[Optional[str], dict[str, Any]]:
    """Any GAWAAH product QR in this picture, and what was seen.

    EXACT where appearance is a judgement. A decoded QR is an identifier, not a
    similarity, so when one is present it settles identity outright and the
    cosine gates are not consulted -- there is nothing for them to add to a
    string that either matched a taught sku or did not.

    Measured before being offered (see FAILURES.md): a QR round-trips down to
    70 px in a 1280x720 frame under blur and noise -- about a 29 mm sticker at
    counter framing. The manufacturer's own EAN-13 barcode does NOT survive the
    same test and is not attempted: a 32 mm barcode on a tube filling 70% of
    the frame gets 0.84 px per module where EAN needs at least 2.
    """
    ev: dict[str, Any] = {"qr_found": False, "qr_payload": None}
    try:
        det = cv2.QRCodeDetector()
        texts: list[str] = []
        try:
            ok, decoded, _pts, _ = det.detectAndDecodeMulti(bgr)
            if ok and decoded:
                texts = [t for t in decoded if t]
        except Exception:                       # noqa: BLE001 - optional API
            texts = []
        if not texts:
            one = det.detectAndDecode(bgr)[0]
            if one:
                texts = [one]
    except Exception:                           # noqa: BLE001 - never kill a frame
        return None, ev
    if not texts:
        return None, ev
    ev["qr_found"] = True
    ev["qr_payload"] = texts[0]
    # A packet already carries other people's QR codes -- a UPI code, a
    # warranty link, a marketing campaign -- and NONE of them name a price
    # here. Our prefix is what tells ours apart. A bare payload is still
    # accepted, because a code generated elsewhere should work, but only if it
    # could actually BE an sku id: without that check the first marketing URL
    # on a wrapper becomes a catalogue lookup, and the failure mode is a
    # refusal that names somebody's tracking link as a missing product.
    for t in texts:
        s = t.strip()
        if s.lower().startswith(QR_PREFIX):
            s = s[len(QR_PREFIX):].strip()
        elif not _QR_BARE_SKU_RE.match(s):
            ev.setdefault("qr_ignored", []).append(t[:80])
            continue
        if s:
            ev["qr_payload"] = t
            return s, ev
    return None, ev


def do_recognise_plain(raw: bytes, *, thumbs: bool = True) -> dict[str, Any]:
    """One ordinary photo at the till: name the one item in it, or abstain.

    A photo with no mat has ONE subject by construction — the segmenter finds
    the dominant region and there is no metric plane to lay several items out
    on. So this returns at most one row, never a basket, and its total is that
    row's price or nothing.
    """
    embed = load_embedder()
    load_store()

    bgr, note = decode_upload(raw)
    crop, region = plain_crop(bgr)

    known = taught_skus()
    if not known:
        raise UploadRefused(
            R_EMPTY_GALLERY,
            "Nothing has been taught yet, so there is nothing to compare this "
            "photograph against. Teach a product first.")

    # A QR is read from the WHOLE uploaded picture, not from the crop: the
    # sticker is often beside the item or on a face of it the segmenter did not
    # keep, and a code that is legible in the frame should not be lost to a
    # tight crop. It is still only the counter rectangle -- nothing outside what
    # the operator chose to upload is examined.
    qr_sku, qr_ev = decode_sku_qr(bgr)
    region = {**region, **qr_ev}
    # How many printed codes were legible in this frame, whatever the mode.
    # The appearance path is a similarity judgement; if an exact identifier was
    # sitting right there, the page should be able to say so.
    codes_seen = len(decode_all_codes(bgr))
    if qr_sku is not None:
        # Everything PRICED, not only what has a descriptor: the same sticker
        # was being priced by basket mode and denounced as "not in the
        # catalogue" by this one, for a code-only product that /shop lists.
        by_sku = {r.sku_id: {"sku_id": r.sku_id, "name": r.name,
                             "price_paise": int(r.price_paise)} for r in known}
        for k2, v2 in offer_priced_skus().items():
            by_sku.setdefault(k2, v2)
        rec = by_sku.get(qr_sku)
        if rec is None:
            # Refuse by name rather than fall through to appearance. A sticker
            # that names something untaught is a question that was answered
            # exactly and wrongly, and quietly guessing from pixels instead
            # would hide that the catalogue is missing an entry.
            raise UploadRefused(
                R_QR_UNKNOWN,
                f"A GAWAAH product code was read and it says {qr_sku!r}, which "
                f"is not in the catalogue. Nothing was priced. Teach "
                f"{qr_sku!r} first, or use a sticker for a product that "
                f"exists. Taught: {sorted(by_sku) or 'nothing yet'}.")
        price = int(rec["price_paise"])
        return {
            "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
            "locked": False, "mode": MODE_PLAIN,
            "reason": "recognised_by_product_code",
            "identified_by": "qr_code",
            "reference_source": "not_applicable_no_mat",
            "ids_found": [],
            "items": [{
                "id": 0, "measured": {"footprint_mm": None, **region},
                "sku_id": rec["sku_id"], "name": rec["name"],
                "price_paise": price, "price_rupees": rupees_str(price),
                "top1": None, "top2": None, "margin": None,
                "top1_sku": None, "top2_sku": None, "n_candidates": len(known),
                "reason": "qr_exact", "size_check": "none",
                "gate": "product_code",
                "phi_used": None,
                "identity": {"sku_id": rec["sku_id"], "mode": "product_code",
                             "payload": qr_ev.get("qr_payload"),
                             "note": "an identifier was read, not a similarity "
                                     "judged; no cosine gate applies"},
            }],
            "named": [], "amber": [],
            "counts": {"placements": 1, "named": 1, "amber": 0},
            "amber_reasons": [],
            "total_paise": price, "total_rupees": rupees_str(price),
            "excluded_paise": 0, "excluded_count": 0,
            "catalog_size": len(set(priced_skus()) | {r.sku_id for r in known}),
            "comparable_by_look": len(known),
            "codes_seen": codes_seen,
            "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                      "phi_appearance_only": PHI_APPEARANCE_ONLY},
            "elapsed_ms": 0.0,
            "crop_png": _thumb_png(crop, 260) if thumbs else None,
            "input": note,
            "appearance_only": False,
            "size_check": "none",
            "footprint_mm": None,
            "warning": (
                "Named from a printed product code, so this line did NOT go "
                "through the appearance gate and has no size check either. The "
                "code is exact about WHICH sku; it says nothing about what is "
                "physically in front of the camera. A sticker on the wrong "
                "packet prices the wrong packet."),
        }

    t0 = time.perf_counter()
    try:
        # long_edge_mm=None, in as many words: this photograph never had a mat
        # in it, so nothing in it has been measured. That is NOT a failed
        # measurement being waved through — a failed mat lock is refused
        # upstream — it is the honest absence of one, and the library answers
        # it by skipping the metric filter and raising the similarity bar.
        res = matless_identifier(known, embed).identify(crop, None)
    except IdentityError as exc:
        raise UploadRefused(R_IDENTITY, f"{exc}") from None
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    by_id = {r.sku_id: r for r in known}
    row: dict[str, Any] = {
        "id": 0,
        "measured": {"footprint_mm": None, **region},
        "sku_id": None, "name": None, "price_paise": None, "price_rupees": None,
        "top1": round(float(res.top1), 4),
        "top2": round(float(res.top2), 4),
        "margin": round(float(res.margin), 4),
        "top1_sku": res.top1_sku, "top2_sku": res.top2_sku,
        "n_candidates": int(res.n_candidates),
        "reason": res.reason,
        "size_check": "none",
        "gate": res.mode,
        "phi_used": res.phi_applied,
        "identity": res.to_audit(),
    }

    total = 0
    if res.sku_id is None:
        row["explain"] = MATLESS_ABSTAIN_EXPLAIN.get(
            res.reason, ABSTAIN_EXPLAIN.get(res.reason,
                                            "Abstained; see reason."))
    else:
        rec = by_id[res.sku_id]
        if rec.price_paise is None:
            row.update({"reason": R_NO_PRICE, "top1_sku": res.top1_sku})
            row["explain"] = (
                f"Recognised as {res.sku_id!r} but the catalog has no price "
                f"for it, so it is amber rather than billed at zero.")
        else:
            row.update({
                "sku_id": rec.sku_id, "name": rec.name,
                "price_paise": int(rec.price_paise),
                "price_rupees": rupees_str(int(rec.price_paise)),
                "enrolled_footprint_mm": rec.footprint_mm,
                "footprint_delta_mm": None,
                "matched_sku_taught_with": rec.taught_with,
                "matched_sku_appearance_only": rec.appearance_only,
            })
            total = int(rec.price_paise)

    named = [row] if row["sku_id"] is not None else []
    amber = [] if named else [row]

    return {
        "ok": True,
        "settles_money": False,
        "money_note": MONEY_NOTE,
        "locked": False,
        "mode": "plain_photo",
        "reason": "recognised_without_a_mat",
        "reference_source": "not_applicable_no_mat",
        "ids_found": [],
        "items": [row],
        "named": named,
        "amber": amber,
        "counts": {"placements": 1, "named": len(named), "amber": len(amber)},
        "amber_reasons": sorted({str(r["reason"]) for r in amber}),
        "total_paise": int(total),
        "total_rupees": rupees_str(total),
        "excluded_paise": 0,
        "excluded_count": len(amber),
        # The same number /shop reports, so the chip on the page cannot flip
        # between two truths as the read mode toggles. The size of the
        # descriptor gallery — what THIS mode can compare against — is its own
        # field, because they are different facts.
        "catalog_size": len(set(priced_skus()) | {r.sku_id for r in known}),
        "comparable_by_look": len(known),
        "codes_seen": codes_seen,
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                  "phi_appearance_only": PHI_APPEARANCE_ONLY},
        "elapsed_ms": elapsed_ms,
        # THE SAME 260 px CROP, ENCODED TWICE, ~50 KB each.
        #
        # Together they were 95% of this response body, sent four times a second
        # to a React page that reads neither — basket mode's equivalent is
        # 1.5 KB. /legacy DOES render both, so they cannot simply be deleted;
        # the caller that does not want them asks not to have them.
        "overlay_png": _thumb_png(crop, 260) if thumbs else None,
        "crop_png": _thumb_png(crop, 260) if thumbs else None,
        "input": note,
        "source_image_returned": False,
        **_appearance_only_block(extra={"footprint_mm": None}),
    }


#: What an abstention MEANS when there was never a size to help.
MATLESS_ABSTAIN_EXPLAIN = {
    "below_similarity":
        "Nothing taught LOOKS enough like this, at the stricter bar an "
        "appearance-only match has to clear. There was no size to help: with "
        "no mat in the photograph nothing here was measured, so every taught "
        "product competed and none of them won. Either it is a new product, or "
        "teach this one on the mat and try again.",
    "below_margin":
        "There is a leader but not by enough, and with no millimetres there is "
        "no second opinion to break the tie. The leader is named above as a "
        "SUGGESTION and is NOT priced.",
    "ambiguous_pair":
        "The top two are tied to within numerical noise and nothing measured "
        "can separate them, because nothing was measured. Both are named "
        "above; a human must pick.",
    R_EMPTY_GALLERY: "Nothing has been taught yet.",
    R_NO_PRICE: "Recognised, but no price is stored for that SKU.",
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
        # Honest bookkeeping, not a feature: on a build whose shop_store still
        # demands millimetres, an appearance-only SKU cannot enter the gallery
        # this pass ranks against, so it CANNOT be found here. Saying which
        # ones beats letting a shopkeeper wonder why the thing he taught
        # yesterday is amber today.
        "not_in_this_pass": [r.sku_id for r in taught_skus()
                             if r.sku_id not in gallery],
        "not_in_this_pass_why": (
            "taught from a plain photo and this build's catalog cannot hold a "
            "footprint-less SKU in the gallery; recognise them with "
            "mode=plain_photo"),
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                  "phi_appearance_only": PHI_APPEARANCE_ONLY},
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


#: How the caller says which teaching path it wants. The DEFAULT is the mat,
#: always, because the mat is the good path: it yields millimetres and a SKU
#: with millimetres is protected by a discriminator that appearance cannot
#: supply. The weaker path is never taken by accident, never as a silent
#: fallback from a failed lock, and never without the caller asking for it in
#: as many words.
MODE_MAT = "mat"
MODE_PLAIN = "plain_photo"
MODE_BASKET = "basket"


def read_mode(form: dict[str, Any]) -> str:
    """`mode=plain_photo` (or the friendlier `no_mat=1`) opts into the weaker
    path. Anything else, including nothing at all, is the mat."""
    raw = str(form_value(form, "mode") or "").strip().lower()
    no_mat = str(form_value(form, "no_mat") or "").strip().lower()
    if raw in ("basket", "codes", "scan", "multi"):
        return MODE_BASKET
    if raw in ("plain", "plain_photo", "no_mat", "matless", "appearance_only"):
        return MODE_PLAIN
    if no_mat in ("1", "true", "yes", "on"):
        return MODE_PLAIN
    return MODE_MAT


# ---------------------------------------------------------------- offers --
#
# WHERE A DISCOUNT HAS TO BE APPLIED, AND WHY IT IS HERE AND NOT IN THE PAGE.
#
# paisa re-prices every basket from ITS OWN book before it mints, and its book
# is wrapped in `offers.OfferPriceBook`. So paisa already charges the
# discounted price. If the till kept showing the marked price, the total the
# page proposes and the total paisa derives would differ, and the mint would be
# refused with `amount_disagreement` — which is invariant 5 working exactly as
# it should, and a till nobody can charge with.
#
# The fix is not to teach the browser about offers. It is to make sure every
# place the SERVER puts a price on a line has already asked the same question
# paisa will ask. Then the page has nothing to know, and the two totals cannot
# drift apart because they are derived from one rule in one process.
#
# `priced_skus()` deliberately stays UNDISCOUNTED: `gawaah/offers.py` reads it
# to learn what a product is marked at, and discounting it there would apply
# every offer twice.


def offer_priced_skus() -> dict[str, dict[str, Any]]:
    """`priced_skus()` with today's active offers applied.

    Every row keeps `marked_paise` beside the charged `price_paise`, because a
    line that is cheaper than the shelf edge should be able to say so — and
    because a shopkeeper checking a bill needs to see that the difference was
    an offer and not a mistake.
    """
    base = priced_skus()
    try:
        from gawaah import offers as _offers
        quotes = _offers.priced_map({k: int(v["price_paise"]) for k, v in base.items()})
    except Exception:
        # An unreadable or half-written offers file must never take the till
        # down or, worse, silently charge a price nobody set. Marked prices are
        # the safe answer: they are what the shelf edge says.
        return base
    out: dict[str, dict[str, Any]] = {}
    for sku, row in base.items():
        q = quotes.get(sku)
        marked = int(row["price_paise"])
        if q is None or q.off_paise <= 0:
            out[sku] = {**row, "marked_paise": marked}
            continue
        out[sku] = {**row, "price_paise": int(q.price_paise),
                    "marked_paise": marked, "off_paise": int(q.off_paise),
                    "offer_id": q.offer_id, "clamped": bool(q.clamped)}
    return out


def priced_skus() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, how} for everything that HAS a price.

    NOT the same set as `taught_skus`, and the difference matters. That function
    returns products with a DESCRIPTOR, because its callers are about to compare
    vectors, and it drops a zero-row gallery on purpose. A product taught from a
    printed code alone has no descriptor by definition — it is a name and a
    price bound to a string of digits — so it is invisible there and would be
    unpriceable at a till that reads its code.
    """
    out: dict[str, dict[str, Any]] = {}
    for sku_id, rec in sorted(_ao_load()["skus"].items()):
        try:
            # `paise()` FIRST, `int()` SECOND, AND THE ORDER IS THE WHOLE GUARD.
            # `int(paise(x))` refuses a float and then narrows the type;
            # `paise(int(x))` truncates 1050.7 to 1050 before paise ever sees
            # it, which is a silently wrong price that passes every check.
            #
            # This is the one door into the price map that had no guard.
            # `appearance_only.json` is a JSON file on disk — anything that can
            # write to the shop directory can put `10.5` in it — and this value
            # goes straight into `publish_price_map`, which is the file paisa
            # re-prices every bill from. `gawaah/manage.py:682` refuses even to
            # RENDER such a row, saying that showing it "would launder it into a
            # number the shopkeeper believes"; this one was laundering it into
            # the bill.
            #
            # A row that fails is SKIPPED, exactly like a row with no price at
            # all: an unpriceable product falls out of the catalogue as amber,
            # which is this program's stated behaviour for a price it cannot
            # derive. It is not rounded into something plausible.
            price = int(paise(rec["price_paise"]))
        except Exception:  # noqa: BLE001 - no price, or not integer paise
            continue
        vecs = rec.get("vectors") or []
        out[sku_id] = {"sku_id": sku_id,
                       "name": str(rec.get("name") or sku_id),
                       "price_paise": price,
                       "how": "appearance" if len(vecs) else "product_code_only"}
    for rec in taught_skus():
        if rec.price_paise is None:
            continue
        out[rec.sku_id] = {"sku_id": rec.sku_id, "name": rec.name,
                           "price_paise": int(rec.price_paise),
                           "how": getattr(rec, "taught_with", "appearance")}
    return out


def publish_price_map() -> Optional[Path]:
    """Every priced sku, written where the money service reads prices.

    Two price files had grown: the store's own sidecar at
    ``<store>/shop.json`` and the file paisa actually loads,
    ``<store>/../shop.json`` (live_app's ``GAWAAH_DATA_DIR / shop.json``).
    Products taught on this site -- appearance-only and code-only ones
    especially -- lived in the first and never reached the second, so at mint
    time they were unpriceable and fell out of the bill as amber. The visible
    symptom was a total that was quietly short, which is the exact failure this
    program calls disqualifying everywhere else.

    This writes the merged map after every catalogue mutation. paisa remains
    the sole authority at mint time: it READS this file server-side and
    re-prices every line from it; nothing a browser sends can alter a price on
    the way through. Best-effort by design -- a publish failure must not turn a
    successful teach into an error -- but never silent: the caller gets None
    and the response says the money service cannot see the product yet.

    THE TARGET IS RESOLVED THE WAY THE READER RESOLVES IT, and that is the whole
    of the second fix here. This used to be `store_dir().parent / "shop.json"`
    unconditionally, which is only the file paisa reads when the shop directory
    happens to sit inside the data directory. Point `GAWAAH_SHOP_DIR` and
    `GAWAAH_DATA_DIR` at different places -- which every scratch test does, and
    which the two-service layout is free to do -- and this wrote a complete,
    correct price map to a path nothing ever opens. `live_app.py` reads
    `GAWAAH_DATA_DIR / shop.json`, so that is what is written when the variable
    is set, and `store_dir().parent` only when it is not, which is the layout
    that made the old line right in the first place.
    """
    import json

    try:
        env = os.environ.get("GAWAAH_DATA_DIR")
        target = (Path(env) if env else store_dir().parent) / "shop.json"
        prices = {sku: int(v["price_paise"]) for sku, v in priced_skus().items()}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(prices, sort_keys=True, indent=1) + "\n",
                          encoding="utf-8")
        return target
    except Exception:  # noqa: BLE001 - publishing is subordinate to teaching
        return None


def _bind_codes_from(raw: bytes, sku_id: str, typed: str) -> dict[str, Any]:
    """Bind whatever printed code identifies this product, and say which.

    A typed number is a STATEMENT by the shopkeeper and is taken as one. A code
    merely visible in the enrolment photograph is weaker evidence -- the packet
    behind it has a barcode too -- so it is only bound when exactly ONE code is
    in shot. Two codes in a teaching photo is an ambiguity, and the honest
    answer to an ambiguity is to bind neither and say so.
    """
    out: dict[str, Any] = {"bound": [], "seen": [], "note": None}
    t = (typed or "").strip()
    if t:
        out["bound"].append(bind_code(t, sku_id))
        out["note"] = f"Bound the code you typed. Showing {t!r} now prices this product."
        return out
    try:
        bgr, _ = decode_upload(raw)
    except Exception:  # noqa: BLE001 - binding is a bonus, never a blocker
        return out
    found = decode_all_codes(bgr)
    out["seen"] = [c["payload"] for c in found]
    if len(found) == 1:
        out["bound"].append(bind_code(found[0]["payload"], sku_id))
        out["note"] = (f"Read {found[0]['payload']!r} off this photograph and bound it. "
                       f"Showing that code now prices this product.")
    elif len(found) > 1:
        out["note"] = (f"{len(found)} printed codes are in this photograph, so none was "
                       f"bound — there is no way to tell which one belongs to this "
                       f"product. Type the number, or re-shoot with one packet in frame.")
    return out


def do_enrol_code_only(raw: bytes, sku_id: str, name: str, price_paise: int,
                       *, typed: str = "") -> dict[str, Any]:
    """Teach a product from its PRINTED CODE alone, with no descriptor at all.

    The fastest way to fill a catalogue, and the weakest thing in this program.
    Nothing is measured and nothing is embedded: the counter learns that a
    string of digits means a name and a price, and NOTHING about what the
    product looks like. Shown a different packet carrying that code -- a
    refill, a multipack, a sticker peeled off and stuck on something else --
    it will price it without hesitation, because it has never seen the product
    and has no way to disagree.

    It is offered because a shopkeeper with four hundred SKUs will not
    photograph four hundred products, and a catalogue that exists is worth more
    than one that was too much work to build. It is not offered quietly.
    """
    load_store()
    code = (typed or "").strip()
    seen: list[str] = []
    if not code:
        bgr, _ = decode_upload(raw)
        found = decode_all_codes(bgr)
        seen = [c["payload"] for c in found]
        if not found:
            raise UploadRefused(
                "no_code_in_frame",
                "No printed code could be read in this photograph and none was "
                "typed, so there is nothing to bind a price to. Fill more of the "
                "frame with the barcode — an EAN-13 needs about 220 px of width "
                "to decode — or type the number under the bars.")
        if len(found) > 1:
            raise UploadRefused(
                "several_codes_in_frame",
                f"{len(found)} printed codes are readable here ({', '.join(seen[:4])}"
                f"{'…' if len(seen) > 4 else ''}), so there is no way to tell which "
                f"one belongs to this product. Re-shoot with one packet in frame, "
                f"or type the number.")
        code = found[0]["payload"]

    replaced = _ao_put(sku_id, name, int(price_paise), [], None)
    binding = bind_code(code, sku_id)
    return {
        "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
        "locked": False, "mode": MODE_BASKET,
        "reason": "taught_from_a_printed_code",
        "reference_source": "not_applicable_no_mat",
        "ids_found": [],
        "measured": {"footprint_mm": None,
                     "millimetres": "none — nothing was measured",
                     "descriptor": "none — nothing was embedded"},
        "stored": {
            "sku_id": sku_id, "name": name,
            "price_paise": int(price_paise), "price_rupees": rupees_str(price_paise),
            "footprint_mm": None, "n_views": 0, "vector_dim": 0,
            "replaced_existing": bool(replaced),
            "store_action": "replaced" if replaced else "added",
            "size_check": "none", "appearance_check": "none",
            "taught_with": "product_code_only",
            "code": code,
        },
        "codes": {"bound": [binding], "seen": seen,
                  "note": f"Showing {code!r} now prices this product."},
        "collision": {"collides": False, "reason": "not_applicable_no_descriptor"},
        "catalog_size": len({r.sku_id for r in taught_skus()}),
        "appearance_only": False,
        "warning": (
            "TAUGHT FROM A CODE ONLY. This counter now knows that "
            f"{code!r} means {name!r} at {rupees_str(price_paise)} — and knows "
            "NOTHING about what the product looks like. It cannot recognise this "
            "product by sight, and it cannot notice that the code is on the wrong "
            "packet. There is no size check and no appearance check on this line, "
            "ever."),
        "better": (
            "Photograph the product as well. Teaching by appearance adds a second, "
            "independent opinion, so a code stuck on the wrong item can be caught "
            "instead of billed."),
    }


def do_recognise_basket(raw: bytes) -> dict[str, Any]:
    """EVERY printed code on the counter at once, priced and totalled.

    This is the supermarket lane rather than the one-item pose: a shopper puts
    a basket down, and every packet showing a readable code is named in the same
    frame. It is a DIFFERENT KIND OF CLAIM from the appearance path and is kept
    visibly separate from it -- a decoded code is an identifier that was READ,
    not a likeness that was JUDGED, so no cosine gate applies and none is
    reported. What a code cannot tell you is what is physically in front of the
    lens: a sticker on the wrong packet prices the wrong packet, and that is
    said on every line it produces.

    A code that names nothing taught is a row, not a silence. It is the single
    most likely thing to happen at a real counter -- the shopkeeper has not
    taught that product yet -- and dropping it would make a short bill look
    like a complete one.
    """
    load_store()
    bgr, note = decode_upload(raw)
    t0 = time.perf_counter()
    found = decode_all_codes(bgr)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    known = offer_priced_skus()
    h_full, w_full = bgr.shape[:2]

    items: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    total = 0
    for i, c in enumerate(found):
        sku_id = resolve_code(c["payload"])
        rec = known.get(sku_id) if sku_id else None
        row = {
            "id": i,
            "code": c["payload"],
            "code_format": c["format"],
            "box": c["box"],
            "measured": {"footprint_mm": None, "frame_px": [int(w_full), int(h_full)],
                         "region_px": c["box"]},
            "gate": "product_code",
            "size_check": "none",
            "phi_used": None,
            "top1": None, "top2": None, "margin": None,
        }
        if rec is None:
            row.update({
                "sku_id": None, "name": None,
                "price_paise": None, "price_rupees": None,
                "reason": ("code_not_taught" if sku_id is None
                           else "code_names_a_missing_product"),
                "explain": (
                    f"A printed code was read and it says {c['payload']!r}. "
                    + ("Nothing has been taught for it, so there is no price to "
                       "apply. Teach this product and bind the code to it."
                       if sku_id is None else
                       f"It names {sku_id!r}, which is not in the catalogue.")),
            })
            unknown.append(row)
        else:
            price = int(rec["price_paise"])
            total += price
            row.update({
                "sku_id": rec["sku_id"], "name": rec["name"],
                "taught_with": rec["how"],
                "price_paise": price, "price_rupees": rupees_str(price),
                "reason": "code_exact",
                "identified_by": ("gawaah_sticker"
                                  if c["payload"].lower().startswith(QR_PREFIX)
                                  else "printed_product_code"),
            })
        items.append(row)

    named = [r for r in items if r["sku_id"] is not None]
    return {
        "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
        "locked": False, "mode": MODE_BASKET,
        "reason": ("read_the_whole_counter" if found else "no_codes_in_frame"),
        "identified_by": "product_codes",
        "reference_source": "not_applicable_no_mat",
        "ids_found": [],
        "items": items, "named": named, "amber": unknown,
        "counts": {"placements": len(items), "named": len(named),
                   "amber": len(unknown)},
        "amber_reasons": sorted({r["reason"] for r in unknown}),
        # Both numbers, because they answer different questions. `codes_found`
        # is physical symbols on the counter — two identical packets are two.
        # `distinct_codes` is how many different products those symbols name.
        # Reporting only the second is how a dropped packet hides.
        "codes_found": len(found),
        "distinct_codes": len({c["payload"] for c in found}),
        "total_paise": total, "total_rupees": rupees_str(total),
        "excluded_paise": 0, "excluded_count": len(unknown),
        "catalog_size": len(known),
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                  "phi_appearance_only": PHI_APPEARANCE_ONLY},
        "elapsed_ms": elapsed_ms,
        "input": note,
        "appearance_only": False,
        "size_check": "none",
        "footprint_mm": None,
        "warning": (
            "Every line here was named from a PRINTED CODE, not from appearance. "
            "A code is exact about WHICH product it names and says nothing about "
            "what is physically in front of the camera: a sticker on the wrong "
            "packet prices the wrong packet. No size check and no similarity "
            "gate applies to any of these lines."),
        "limits": (
            "An EAN-13 needs about 220 px of frame width to decode and nothing "
            "under 180 px decoded at all in testing; past roughly 12 degrees "
            "of tilt, or under motion blur, a barcode stops being readable while "
            "a QR keeps going. A packet whose code is not readable is not "
            "missing from the world, only from this frame."),
    }


def _offer_the_matless_path(res: JSONResponse) -> JSONResponse:
    """Bolt the second path onto a mat refusal, as an offer the page can act on.

    The refusal itself is unchanged and still correct: no mat means no
    millimetres. But "no markers detected, 0 of 4" with nothing after it is
    where a shopkeeper with a downloaded product photo gives up, so every mat
    refusal now also says what else is possible and what it costs.
    """
    import json

    body = json.loads(bytes(res.body).decode())
    body["alternative"] = {
        "mode": MODE_PLAIN,
        "how": "POST the same photo again with mode=plain_photo (the page's "
               "TEACH IT ANYWAY button), or no_mat=1.",
        "what_you_get": "the product segmented off its background and stored "
                        "with NO footprint",
        "what_it_costs": APPEARANCE_ONLY_WARNING,
    }
    return JSONResponse(body, status_code=res.status_code)


@app.post("/enrol", dependencies=AUTH_GUARD)
async def enrol_ep(request: Request) -> JSONResponse:
    """multipart: image + sku_id + name + price_rupees -> one taught product.

    `mode=plain_photo` teaches from a photograph with no mat in it and stores
    the SKU with no footprint. It is opt-in and it is louder about what it
    cannot do than about what it can.

    `region=user_drawn` additionally says the uploaded image IS a rectangle a
    person dragged around the product, so the segmenter is not asked where the
    product is. Opt-in for the same reason: the careful path is the default and
    the trusting one has to be requested in as many words.
    """
    try:
        form = await read_form(request)
        sku_id = _valid_sku(str(form_value(form, "sku_id") or ""))
        name = _valid_name(str(form_value(form, "name") or ""))
        price = price_to_paise(form_value(form, "price_rupees"),
                               form_value(form, "price_paise"))
        force = str(form_value(form, "force") or "").lower() in ("1", "true", "yes")
        img = form_image(form)
        mode = read_mode(form)
        if mode == MODE_BASKET:
            # Teaching BY CODE alone: no descriptor, no photograph to embed.
            # The product is whatever the shopkeeper says this code is worth.
            res = do_enrol_code_only(img, sku_id, name, price,
                                     typed=str(form_value(form, "barcode") or ""))
        elif mode == MODE_PLAIN:
            # `region=user_drawn` is a claim about PROVENANCE, not a permission:
            # it says a person already answered "where is the product", so the
            # segmenter is not asked to answer it again and cannot refuse a
            # correctly-tight box for touching all four borders. Everything that
            # can still be wrong with the image is still refused by name.
            res = do_enrol_plain(img, sku_id, name, price, force=force,
                                 hand_drawn=read_region(form) == REGION_USER_DRAWN)
        else:
            res = do_enrol(img, sku_id, name, price, force=force)

        # A code may ride along with EITHER teaching path. Typed wins over read:
        # a shopkeeper holding the packet and typing its number is stating a
        # fact, while a code merely visible in the frame might belong to the
        # box behind it.
        if mode != MODE_BASKET:
            res["codes"] = _bind_codes_from(img, sku_id,
                                            str(form_value(form, "barcode") or ""))
        published = publish_price_map()
        res["price_map_published"] = str(published) if published else None
        if published is None:
            res["price_map_warning"] = (
                "The merged price map could not be written, so the money "
                "service cannot price this product until it is.")
        res["simulated"] = False
        return JSONResponse(res)
    except UploadRefused as exc:
        out = _refusal(exc)
        if exc.reason in _MAT_PATH_REFUSALS or getattr(exc, "diagnosis", None) is not None:
            return _offer_the_matless_path(out)
        return out
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "ids_found": [], "items": [], "refusals": []},
                            status_code=400)


@app.post("/recognise", dependencies=AUTH_GUARD)
async def recognise_ep(request: Request) -> JSONResponse:
    """multipart: image -> every item, named or amber, and an integer total.

    `mode=plain_photo` reads a photograph with no mat in it: one subject, no
    millimetres, appearance only, at the stricter bar.

    `mode=basket` reads EVERY printed code in the frame at once and prices each
    one -- the supermarket lane rather than the one-item pose.
    """
    try:
        form = await read_form(request)
        mode = read_mode(form)
        if mode == MODE_BASKET:
            res = do_recognise_basket(form_image(form))
        elif mode == MODE_PLAIN:
            # The React till reads neither thumbnail; /legacy renders both.
            want_thumbs = str(form_value(form, "thumbs") or "1").strip() != "0"
            res = do_recognise_plain(form_image(form), thumbs=want_thumbs)
        else:
            res = do_recognise(form_image(form))
        res["simulated"] = False
        return JSONResponse(res)
    except UploadRefused as exc:
        out = _refusal(exc)
        if exc.reason in _MAT_PATH_REFUSALS or getattr(exc, "diagnosis", None) is not None:
            return _offer_the_matless_path(out)
        return out
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "locked": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}",
                             "ids_found": [], "items": [], "refusals": [],
                             "amber": [], "total_paise": 0},
                            status_code=400)


def catalog() -> dict[str, Any]:
    """Every taught product, and — per row — HOW it was taught.

    taught_with is not decoration. A row with footprint_mm=None is judged on
    appearance alone at the till: the tape measure that rejects a same-looking
    packet of the wrong size is simply not there for it. A shopkeeper deciding
    whether to trust a price needs to see which rows are which, so the field is
    beside the price rather than buried in an audit log.
    """
    rows = []
    # A product taught from a printed code alone has NO descriptor, so it is
    # absent from `taught_skus` by design — and a catalogue that hides it would
    # let a shopkeeper enter four hundred SKUs and then be told the shop is
    # empty. They are listed here, marked for what they are.
    seen_ids = {r.sku_id for r in taught_skus()}
    code_only = [v for k, v in priced_skus().items()
                 if k not in seen_ids and v["how"] == "product_code_only"]
    bound_all = _codes_load()
    # THE PICTURE OF A PRODUCT THAT HAS NO DESCRIPTOR. These rows carried
    # `thumb_png: None` unconditionally, which was true only while the sole way
    # to reach this bucket was `do_enrol_code_only` — that path stores no
    # thumbnail because it is handed no photograph. `gawaah/shopadmin.py`'s
    # camera-free add and its `PUT /shop/{sku_id}/photo` both put a real base64
    # thumbnail in the same sidecar row, and a hard-coded None reported every
    # one of them as "no photo" on the shopkeeper's own catalogue screen — the
    # screen whose job is to show which products still need one.
    ao_rows = _ao_load()["skus"]
    for c in code_only:
        codes = sorted(k for k, sk in bound_all.items() if sk == c["sku_id"])
        rows.append({
            "sku_id": c["sku_id"], "name": c["name"],
            "price_paise": int(c["price_paise"]),
            "price_rupees": rupees_str(int(c["price_paise"])),
            "footprint_mm": None, "n_views": 0, "vector_dim": 0,
            "thumb_png": (ao_rows.get(c["sku_id"]) or {}).get("photo"),
            "taught_with": "product_code_only",
            "appearance_only": False,
            "size_check": "none", "appearance_check": "none",
            "phi_used": None,
            "storage": "appearance_only_sidecar",
            # TWO DIFFERENT PRODUCTS WEAR THIS ONE LABEL, and only one of them
            # has a code. `product_code_only` is the bucket for "priced, no
            # descriptor", and a product typed in at the counter with no
            # barcode lands in it too. Telling that shopkeeper it "cannot
            # notice the code on the wrong packet" names a code they never
            # bound; the real limit is that there is no way to reach the
            # product at the till at all except by hand.
            "warning": (
                "Taught from a printed code alone. This counter knows what this "
                "code is worth and NOTHING about what the product looks like, so "
                "it cannot notice the code on the wrong packet."
                if codes else
                "Typed in, never seen and no printed code. This counter knows "
                "this product's name and price and nothing else — it cannot be "
                "recognised by the camera and cannot be scanned, so it reaches "
                "a bill only by hand or through the storefront."),
            "codes": codes,
        })
    for rec in taught_skus():
        price = rec.price_paise
        rows.append({
            "sku_id": rec.sku_id,
            "name": rec.name,
            "price_paise": None if price is None else int(price),
            "price_rupees": None if price is None else rupees_str(int(price)),
            "footprint_mm": (None if rec.footprint_mm is None
                             else round(rec.footprint_mm, 2)),
            "n_views": rec.n_views,
            "vector_dim": rec.dim,
            "thumb_png": rec.thumb,
            "taught_with": rec.taught_with,
            "appearance_only": rec.appearance_only,
            "size_check": "none" if rec.appearance_only else "footprint_gated",
            "phi_used": (PHI_APPEARANCE_ONLY if rec.appearance_only else PHI),
            "storage": rec.storage,
            "warning": APPEARANCE_ONLY_WARNING if rec.appearance_only else None,
            "codes": sorted(c for c, sk in _codes_load().items() if sk == rec.sku_id),
        })
    # OFFERS, APPLIED ONCE, AT THE END.
    #
    # This list is assembled from two stores, so discounting either input alone
    # showed an offer on the code-taught rows and the marked price on every
    # other one. Applying it to the finished rows is the only place there is a
    # single answer — and this list is what the till, the voice bar and the
    # customer's storefront all read, so a price that is wrong here is wrong in
    # three places at once.
    #
    # `marked_paise` rides alongside so a screen can show the shelf-edge price
    # struck through. A cheaper line the shopkeeper cannot explain is worse
    # than no offer at all.
    try:
        from gawaah import offers as _offers
        _quotes = _offers.priced_map({r["sku_id"]: int(r["price_paise"])
                                      for r in rows if r["price_paise"] is not None})
        for r in rows:
            q = _quotes.get(r["sku_id"])
            if q is None or q.off_paise <= 0:
                continue
            r["marked_paise"] = int(q.base_paise)
            r["marked_rupees"] = rupees_str(int(q.base_paise))
            r["off_paise"] = int(q.off_paise)
            r["offer_id"] = q.offer_id
            r["price_paise"] = int(q.price_paise)
            r["price_rupees"] = rupees_str(int(q.price_paise))
    except Exception:
        # An unreadable offers file must never take the catalogue down, and must
        # never invent a price. The marked prices are the safe answer.
        pass

    weak = [r for r in rows if r["appearance_only"]]
    return {
        "ok": True,
        "settles_money": False,
        "money_note": MONEY_NOTE,
        "count": len(rows),
        "skus": rows,
        "store_dir": str(store_dir()),
        "gates": {"theta": THETA, "phi": PHI, "tau_mm": TAU_MM,
                  "phi_appearance_only": PHI_APPEARANCE_ONLY},
        "priced": sum(1 for r in rows if r["price_paise"] is not None),
        # These three partition `count` exactly. The old subtraction counted a
        # code-only row — no footprint, no descriptor, no checks of any kind —
        # as MAT-MEASURED, overstating the strong bucket by exactly code_only.
        "mat_measured": sum(1 for r in rows if r["footprint_mm"] is not None),
        "appearance_only": len(weak),
        "no_size_check": sum(1 for r in rows if r["size_check"] == "none"),
        "code_only": sum(1 for r in rows if r.get("taught_with") == "product_code_only"),
        "appearance_only_skus": [r["sku_id"] for r in weak],
        "appearance_only_warning": (APPEARANCE_ONLY_WARNING if weak else None),
        # Products on disk that this build can no longer compare against. An
        # empty catalogue with no explanation reads as data loss; this names
        # them so the page can offer to teach them again.
        "needs_reteach": ao_superseded(),
    }


# ------------------------------------------------------- the enrolment gate --
# SAAF ON AN ORDINARY CAMERA.
#
# Like ident_sticker, `gawaah/saaf.py` contains ZERO millimetre references and
# never touches the mat: it takes a burst of grayscale frames, scores each on
# glare, guarded blur and absolute defocus, throws away the failures, registers
# the survivors and stacks them. None of that needs the printed sheet.
#
# It also answers a gap this project has been carrying openly: SAAF gated the
# STICKER reference and nothing else, so teaching a product accepted any
# photograph however blurred. This route is the same module on any webcam, and
# the per-frame reports come back with it — the brain's own serialiser dropped
# them, which is why the panel's contact sheet has never had anything to draw.

def _f(v) -> "float | None":
    """A measurement, or None when there was nothing to measure.

    Not 0.0. "We could not measure this" and "this measured zero" are different
    facts, and on a gate that decides what gets learned they must not be
    collapsed into one number.
    """
    return None if v is None else float(v)


@app.post("/saaf/stack", dependencies=AUTH_GUARD)
async def saaf_stack_ep(request: Request) -> JSONResponse:
    """Score a burst of frames and stack the survivors.

    Frames arrive as `image0`, `image1`, ... A burst of one is refused by name
    rather than silently treated as a stack of one, because "we could not
    compare these" and "these agreed" are different answers.
    """
    try:
        from gawaah import saaf as saaf_defaults
        from gawaah.saaf import BurstStacker

        form = await read_form(request)
        parts = form.get("_parts", {})
        keys = sorted((k for k in parts if k.startswith("image")),
                      key=lambda k: int(k[5:] or 0))
        if len(keys) < 2:
            raise UploadRefused(
                R_FIELD_MISSING,
                f"a burst needs at least two frames to compare; got {len(keys)}")
        frames = []
        for k in keys:
            bgr, _n = decode_upload(parts[k].data)
            frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        # Every frame must be the same size — a burst is one region over time.
        h, w = frames[0].shape[:2]
        frames = [f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)) for f in frames]

        # THE RESOLUTION CEILING, OVERRIDABLE FOR THIS MACHINE'S CAMERA.
        #
        # DEFAULT_MAX_BLUR_SCORE = 0.46 is calibrated, not guessed: MTF50 at
        # 0.15 cyc/px, measured across 10 scenes into 0.4573..0.4684. It is a
        # threshold on RESOLUTION, so a camera that genuinely resolves less
        # genuinely fails it, and lowering the shipped default would quietly
        # lower what every shop is allowed to teach.
        #
        # But the number a LAPTOP WEBCAM produces at arm's length sits right on
        # it — a softness of sigma 1.0, which nobody would call a blurry photo,
        # scores 0.486 — so on some machines the gate rejects every frame of
        # every burst and the camera path is simply unusable. That is a worse
        # outcome than a slightly softer taught view, and it is a property of
        # the operator's hardware, not of this shop's standards.
        #
        # So it is settable per machine and NOT changed here. An operator who
        # raises it is choosing to teach from a softer picture, which is a
        # choice they can see the consequence of on the very next scan.
        _ceiling = os.environ.get("GAWAAH_MAX_BLUR_SCORE", "").strip()
        _kw: dict[str, Any] = {"scale": 2}
        if _ceiling:
            try:
                v = float(_ceiling)
            except ValueError:
                raise UploadRefused(
                    R_FIELD_MISSING,
                    f"GAWAAH_MAX_BLUR_SCORE={_ceiling!r} is not a number. It is "
                    f"a blur-score ceiling between 0 and 1; the built-in is "
                    f"{saaf_defaults.DEFAULT_MAX_BLUR_SCORE}.") from None
            if not (0.0 < v <= 1.0):
                raise UploadRefused(
                    R_FIELD_MISSING,
                    f"GAWAAH_MAX_BLUR_SCORE={v} is outside (0, 1]. The built-in "
                    f"is {saaf_defaults.DEFAULT_MAX_BLUR_SCORE}.")
            _kw["max_blur_score"] = v

        res = BurstStacker(**_kw).stack(frames)
        return JSONResponse({
            "ok": res.image is not None,
            "settles_money": False,
            "used": int(res.used or 0), "rejected": int(res.rejected or 0),
            # EVERY ONE OF THESE CAN BE None when the whole burst was rejected.
            # A bare float() on them turned "nothing survived the gate" — the
            # single most important thing this module has to be able to say —
            # into an internal error, which is exactly the failure mode the
            # brain's own serialiser had.
            "mean_shift_px": _f(res.mean_shift_px),
            "subpixel_diversity": _f(res.subpixel_diversity),
            "sharpness_gain": _f(res.sharpness_gain),
            "warning": res.warning or "",
            "reference_index": (None if res.reference_index is None
                                else int(res.reference_index)),
            "burst": len(frames),
            # THE CONTACT SHEET. Per-frame, with the number that decided it —
            # this is what the panel was built to draw and never received.
            "frames": [{
                "index": int(r.index),
                "used": bool(r.used),
                "reason": str(r.reason),
                # `reason` carries the measurement inline ("blur:12.3"), which
                # is right for a human reading one row and useless for grouping
                # — every rejected frame has a distinct string. `code` is the
                # bare reason so a caller can count them.
                "code": str(r.code),
                "vlap": _f(r.vlap),
                "sat_frac": _f(r.sat_frac),
                "blur_score": _f(r.blur_score),
                "shift_px": _f(r.shift_px),
            } for r in (res.reports or [])],
            # THE GATES ACTUALLY APPLIED, read from the module that applies
            # them. These were five hardcoded literals, so they reported 0.46
            # even when the run used a different ceiling — and the page prints
            # this number to the shopkeeper as the bar their frame missed. A
            # figure on screen that does not match the one that judged them is
            # worse than no figure.
            "gates": {
                "blur_var_min": saaf_defaults.DEFAULT_BLUR_VAR_MIN,
                "sat_frac_max": saaf_defaults.DEFAULT_SAT_FRAC_MAX,
                "max_blur_score": _kw.get(
                    "max_blur_score", saaf_defaults.DEFAULT_MAX_BLUR_SCORE),
                "min_shift_px": saaf_defaults.DEFAULT_MIN_SHIFT_PX,
                "min_diversity": saaf_defaults.DEFAULT_MIN_DIVERSITY,
            },
        })
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"}, status_code=400)




# ===========================================================================
# THE WHOLE COUNTER AT ONCE
#
# `/recognise?mode=plain_photo` names ONE item, because a photo with no mat has
# one subject by construction. That is a real limit and it is the wrong limit
# for a counter: a customer puts four things down at once, and asking the
# shopkeeper to photograph them one at a time is asking them to do the work the
# camera was supposed to do.
#
# So this endpoint separates two questions that a single vision model conflates
# and does neither well:
#
#     WHERE is a thing   gawaah/detector.py — class-agnostic regions, no
#                        product knowledge, no weights required
#     WHICH thing is it  the shop's OWN taught vectors, at the same cosine gate
#                        every other path uses
#
# MEASURED, on three real taught products laid on a 1280x720 counter:
#
#     contour proposer   3/3 found, IoU 0.90-0.93, 79 ms
#     COCO YOLOv5n       0/3 found
#
# which is not a defect in YOLO — a bar of Lifebuoy is not one of the eighty
# things it knows, and its best guess for one is "person". It stays wired
# because it does add recall on the COCO objects that genuinely appear at a
# kirana counter (a bottle, a cup, a phone) and costs nothing when the model
# file is absent. It is never asked what a product is.
#
# THE NUMBER THAT MATTERS MOST IS `unnamed`. This endpoint reports how many
# regions it could see and could NOT name. That is the honest version of the
# "the camera saw three items and the bill has two" check: it is not an
# accusation and it is not a guess, it is the counter saying there is something
# here I cannot price. Invariant 7 — abstain rather than guess — means an
# unnamed region must never silently become a price.
# ===========================================================================

def _covers(box: tuple[int, int, int, int], pt: tuple[float, float]) -> bool:
    x, y, w, h = box
    return x <= pt[0] <= x + w and y <= pt[1] <= y + h


def do_counter(raw: bytes, *, use_yolo: bool = True) -> dict[str, Any]:
    """Every product on the counter, priced where it can be and named where not."""
    from gawaah import detector as _det

    embed = load_embedder()
    load_store()
    bgr, _note = decode_upload(raw)
    fh, fw = bgr.shape[:2]

    known = taught_skus()
    priced = offer_priced_skus()
    if not known and not priced:
        raise UploadRefused(
            R_EMPTY_GALLERY,
            "Nothing has been taught yet, so there is nothing to compare this "
            "counter against. Teach a product first.")

    t0 = time.perf_counter()

    # 1. CODES FIRST, ALWAYS. An identifier that was READ is not a similarity
    #    judgement and does not go near the cosine gate. Where a code and an
    #    appearance guess disagree, the code wins, because one of them is a
    #    measurement and the other is an opinion.
    items: list[dict[str, Any]] = []
    code_boxes: list[tuple[int, int, int, int]] = []
    for c in decode_all_codes(bgr):
        payload = str(c.get("payload") or "")
        sku_id = c.get("sku_id") or resolve_code(payload)
        box = c.get("box") or {}
        bx = (int(box.get("x", 0)), int(box.get("y", 0)),
              int(box.get("w", 0)), int(box.get("h", 0)))
        code_boxes.append(bx)
        rec = priced.get(sku_id) if sku_id else None
        if rec is None:
            items.append({
                "box": list(bx), "how": "code", "code": payload,
                "sku_id": sku_id, "name": None, "price_paise": None,
                "reason": "code_not_in_catalogue" if sku_id else "code_not_bound",
                "detail": (f"{payload!r} was read cleanly but is not bound to any "
                           f"product. Teach it, or type the number when teaching."),
            })
            continue
        items.append({
            "box": list(bx), "how": "code", "code": payload,
            "sku_id": rec["sku_id"], "name": rec["name"],
            "price_paise": int(rec["price_paise"]),
            "price_rupees": rupees_str(int(rec["price_paise"])),
            "reason": "read_a_printed_code",
        })

    # 2. WHERE. Regions the code pass did not already account for.
    regions = _det.detect(bgr, use_yolo=use_yolo)
    fresh = [p for p in regions
             if not any(_covers(b, (p.x + p.w / 2.0, p.y + p.h / 2.0))
                        for b in code_boxes)]

    # 3. WHICH. One embed per region, against the shop's own vectors.
    ident = matless_identifier(known, embed) if known else None
    for p in fresh:
        crop = p.crop(bgr)
        if crop.size == 0 or min(crop.shape[:2]) < 24:
            continue
        row: dict[str, Any] = {"box": list(p.box), "how": "appearance",
                               "found_by": p.source}
        if ident is None:
            row.update({"sku_id": None, "name": None, "price_paise": None,
                        "reason": "nothing_taught_by_appearance",
                        "detail": "Every product in this shop was taught by its "
                                  "printed code, so there is no appearance to "
                                  "compare against. Teach one from a photograph."})
            items.append(row)
            continue
        try:
            res = ident.identify(crop, None)
        except Exception as exc:                       # a bad crop is not a 500
            row.update({"sku_id": None, "name": None, "price_paise": None,
                        "reason": "identify_failed",
                        "detail": f"{type(exc).__name__}: {exc}"})
            items.append(row)
            continue
        row.update({
            "top1": round(float(res.top1), 4) if res.top1 is not None else None,
            "top1_sku": res.top1_sku,
            "phi_used": res.phi_applied,
        })
        rec = priced.get(res.sku_id) if res.sku_id else None
        if res.sku_id and rec is not None:
            row.update({"sku_id": rec["sku_id"], "name": rec["name"],
                        "price_paise": int(rec["price_paise"]),
                        "price_rupees": rupees_str(int(rec["price_paise"])),
                        "reason": "recognised_by_appearance"})
        else:
            # ABSTAIN, LOUDLY. This region is real and this counter cannot
            # price it. Saying so is the whole point: a short bill an operator
            # can see beats a confident bill that is wrong.
            row.update({"sku_id": None, "name": None, "price_paise": None,
                        "reason": res.reason or "below_the_bar",
                        "detail": ("Something is here and it does not match "
                                   "anything taught closely enough to price. "
                                   "Show its printed code, or teach this view "
                                   "of it.")})
        items.append(row)

    named = [i for i in items if i.get("price_paise") is not None]
    unnamed = [i for i in items if i.get("price_paise") is None]
    # INTEGER PAISE. Never a float, never a division. Invariant 1.
    total = int(sum(int(i["price_paise"]) for i in named))
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
        "mode": "counter",
        "reason": "read_the_whole_counter",
        "frame_px": [int(fw), int(fh)],
        "items": items,
        "counts": {
            "regions_seen": len(regions) + len(code_boxes),
            "named": len(named),
            # THE HONEST MISMATCH SIGNAL. Not "the shopkeeper is stealing" —
            # "there is something on this counter I cannot price". A person
            # decides what that means.
            "unnamed": len(unnamed),
            "by_code": sum(1 for i in items if i.get("how") == "code"),
            "by_appearance": sum(1 for i in items if i.get("how") == "appearance"
                                 and i.get("price_paise") is not None),
        },
        "total_paise": total, "total_rupees": rupees_str(total),
        "gates": {"theta": THETA, "phi": PHI,
                  "phi_appearance_only": PHI_APPEARANCE_ONLY},
        "detector": _det.describe(),
        "elapsed_ms": elapsed_ms,
    }


@app.post("/counter", dependencies=AUTH_GUARD)
async def counter_ep(request: Request) -> JSONResponse:
    """Read the WHOLE counter: several products in one frame."""
    try:
        form = await read_form(request)
        want_yolo = str(form_value(form, "yolo") or "1").strip() != "0"
        return JSONResponse(do_counter(form_image(form), use_yolo=want_yolo))
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"},
                            status_code=400)


# ---------------------------------------------------------- the storefront --
#
# The customer's side of the same shop: a QR on the shutter, a phone that
# browses the catalogue this counter has taught, and an order that arrives
# here. Mounted as a router rather than written inline because it is a
# genuinely separate audience — everything above this line is read by the
# shopkeeper and everything in that module is read by a customer.
#
# It holds no credentials and constructs no payable string. An order is priced
# by the SERVER from this shop's own catalogue, then handed to paisa exactly as
# the till hands it a counter scan, and paisa re-prices the whole basket from
# its own book before it mints. The only payable string that reaches a phone is
# the opaque short_url the gateway issued.
from gawaah import storefront as _storefront          # noqa: E402
app.include_router(_storefront.router, dependencies=AUTH_GUARD)

# ------------------------------------------------------- the back office --
#
# Billing history, inventory and the configuration readout. Everything here is
# DERIVED — from the hash-chained audit log, the catalogue, and the money
# service's own health — and never from a second store that could disagree with
# them. A management screen that keeps its own copy of the numbers is a screen
# that will eventually show a different total from the till.
from gawaah import manage as _manage                  # noqa: E402
app.include_router(_manage.router, dependencies=AUTH_GUARD)

# ------------------------------------------------------------- the offers --
#
# A discount that only the browser knows about is a number paisa has never
# derived, and the mint dies with `amount_disagreement` the moment somebody
# tries to pay. So offers are applied by WRAPPING the money service's own price
# book (see gawaah/live_app.py) — the discounted price is one paisa works out
# itself, which is why invariant 5 survives this feature rather than being
# quietly bent by it. These routes only ever manage the offer records.
from gawaah import offers as _offers                    # noqa: E402
app.include_router(_offers.router, dependencies=AUTH_GUARD)

# -------------------------------------------------- editing what was taught --
#
# Create, read and delete existed; UPDATE did not. A mistyped price meant
# forgetting the product and photographing it again, losing every taught view
# with it. A price change is a money change, so it goes on the audit chain with
# both the old and the new value — a bill from last week has to stay
# explainable after the price it was rung up at has moved.
from gawaah import shopadmin as _shopadmin              # noqa: E402
app.include_router(_shopadmin.router, dependencies=AUTH_GUARD)

# ------------------------------------------------ the face of the shop --
#
# The shutter code used to encode `<origin>/#/shop` — the same string for
# every counter on earth. This gives the shop a slug of its own in the link,
# a printable code carrying it, a photograph for the storefront header, and
# the one OPEN endpoint (`GET /store/shop`) that says whether a link was
# printed for this shop or another. Mounted with the guard like everything
# else; its two `/store/...` reads are open through the prefix above AND are
# named in `auth.OPEN_PATHS`, the way `/shop/nameplate` is.
from gawaah import shopface as _shopface                # noqa: E402
app.include_router(_shopface.router, dependencies=AUTH_GUARD)

# ------------------------------------------------------- the rest of a shop --
#
# Ten capability modules, each its own file with its own tests and its own
# hash-chained log. They are mounted BARE — every path inside them is absolute,
# and passing a prefix here would produce /manage/manage/history-shaped 404s
# nobody can explain.
#
# ORDER MATTERS FOR EXACTLY ONE OF THESE. `auth` installs an exception handler
# that flattens its guard refusals into this program's own
# {ok, reason, detail} shape; everything else is order-independent.
from gawaah import assistant as _assistant              # noqa: E402
from gawaah import categories as _categories            # noqa: E402
from gawaah import customers as _customers              # noqa: E402
from gawaah import daybook as _daybook                  # noqa: E402
from gawaah import expenses as _expenses                # noqa: E402
from gawaah import purchases as _purchases              # noqa: E402
from gawaah import receipts as _receipts                # noqa: E402
from gawaah import search as _search                    # noqa: E402
from gawaah import advisor as _advisor                  # noqa: E402
from gawaah import expiry as _expiry                    # noqa: E402
from gawaah import gst as _gst                          # noqa: E402
from gawaah import insights as _insights                # noqa: E402
from gawaah import labels as _labels                    # noqa: E402
from gawaah import loyalty as _loyalty                  # noqa: E402
from gawaah import khata as _khata                      # noqa: E402
from gawaah import po as _po                            # noqa: E402
from gawaah import share as _share                      # noqa: E402
from gawaah import shelf as _shelf                      # noqa: E402
from gawaah import stock as _stock                      # noqa: E402
from gawaah import weighed as _weighed                  # noqa: E402

app.include_router(_assistant.router, dependencies=AUTH_GUARD)
app.include_router(_categories.router, dependencies=AUTH_GUARD)
app.include_router(_customers.router, dependencies=AUTH_GUARD)
app.include_router(_daybook.router, dependencies=AUTH_GUARD)
app.include_router(_expenses.router, dependencies=AUTH_GUARD)
app.include_router(_purchases.router, dependencies=AUTH_GUARD)
app.include_router(_receipts.router, dependencies=AUTH_GUARD)
app.include_router(_search.router, dependencies=AUTH_GUARD)
app.include_router(_stock.router, dependencies=AUTH_GUARD)
app.include_router(_advisor.router, dependencies=AUTH_GUARD)
app.include_router(_expiry.router, dependencies=AUTH_GUARD)
app.include_router(_gst.router, dependencies=AUTH_GUARD)
app.include_router(_insights.router, dependencies=AUTH_GUARD)
app.include_router(_labels.router, dependencies=AUTH_GUARD)
app.include_router(_loyalty.router, dependencies=AUTH_GUARD)
# KHATA: the udhaar book. Its own chain under the shop dir; balances derived
# from the money chain; COLLECT forwards to paisa, which mints or refuses.
app.include_router(_khata.router, dependencies=AUTH_GUARD)
# MILAN: the day close matched against Razorpay's settlement report. Reads
# the chain through manage and the report through paisa; its one POST hands
# a nonce to paisa's reconcile route. Holds no key, mints nothing.
from gawaah import milan as _milan                      # noqa: E402
app.include_router(_milan.router, dependencies=AUTH_GUARD)
app.include_router(_po.router, dependencies=AUTH_GUARD)
app.include_router(_share.router, dependencies=AUTH_GUARD)
app.include_router(_shelf.router, dependencies=AUTH_GUARD)
app.include_router(_weighed.router, dependencies=AUTH_GUARD)

# ------------------------------------------------- the photographed bill --
#
# PARCHI reads a wholesaler's invoice off a photograph and books it through
# `purchases.py`'s own writer. It is the one place a vision model is used, it
# uses no Razorpay product, and it is mounted BARE with the guard like every
# other router: a photograph of a bill is the shop's cost book, and a stranger
# must not be able to read a cost off it or file one.
from gawaah import parchi as _parchi                    # noqa: E402
app.include_router(_parchi.router, dependencies=AUTH_GUARD)

# AUTH IS MOUNTED, WIRED, AND STILL OFF BY DEFAULT — all three at once.
#
# WIRED: `AUTH_GUARD` is on all twenty-three routers above and on every route
# in this file. That is the part that was missing, and its absence is why
# `/auth/status` could answer `enforced: true` while `GET /shop` answered 200
# to a stranger.
#
# OFF: enforcement is still behind GAWAAH_REQUIRE_AUTH, which nothing here
# sets. With it unset the guard resolves, records `request.state.shopkeeper`,
# and returns — every screen is exactly as reachable as it was.
#
# `install()` is what makes a guard refusal come out in this program's own flat
# `{ok, reason, detail, settles_money}` shape instead of Starlette's nested
# `{"detail": {...}}`. It is called AFTER the routers on purpose: it looks for
# `/auth/me` before mounting, so calling it twice cannot produce two copies of
# the sign-in routes.
#
# The auth router itself is mounted WITHOUT AUTH_GUARD, and that is the one
# correct exception. Its five sign-in routes are `auth.OPEN_PATHS` — you cannot
# sign in through a guard that requires you to be signed in — and `/auth/invite`
# calls `require_shopkeeper_always` itself, so it needs a session whether or not
# the switch is on. `auth.guard_coverage` knows both facts and does not report
# them as holes.
_auth.install(app)


@app.get("/detector", dependencies=AUTH_GUARD)
def detector_ep() -> JSONResponse:
    """What this counter can honestly say about how it finds things."""
    from gawaah import detector as _det
    return JSONResponse({"ok": True, "settles_money": False, **_det.describe()})


# ===========================================================================
# ANOTHER VIEW OF A PRODUCT ALREADY TAUGHT
#
# THE MEASUREMENT THAT MADE THIS NECESSARY. Appearance recognition holds up
# perfectly against light and against a 180-degree flip, and falls apart on
# rotation. Cosine against the taught view, per angle:
#
#            0 deg    5     10     15     25     180
#   lifebuoy 1.000  0.942  0.829  0.760  0.650  0.991
#   parle_g  1.000  0.874  0.775  0.759  0.715  0.998
#   shampoo  1.000  0.988  0.972  0.949  0.889  0.997
#
# against a gate of 0.92. So a packet turned more than about five to ten
# degrees from the angle it was photographed at STOPS BEING RECOGNISED — and
# on a real counter nobody puts a packet down at the angle it was taught at.
#
# The cause is not the gate and not the descriptor. It is that every product in
# this shop has exactly ONE taught view (plus the 180-degree flip that
# enrolment stores for free), because there has never been a way to add a
# second. Dimming to 60% costs 0.002 of cosine; turning the packet 15 degrees
# costs 0.24. One view is the whole problem.
#
# So: photograph the same product again from another angle and APPEND the
# vector. The gate stays where it is — widening it to accept 0.65 would let
# every packet in the shop match every other one, and the margin column shows
# why that is the wrong trade: at 25 degrees Lifebuoy still leads its runner-up
# 0.650 to 0.225. The answer is more views, not a lower bar.
#
# WHAT THIS MAY NOT DO. It may not change a price, a name, or a footprint —
# those are the things a person decided, and a camera pointed at a packet is
# not a reason to revise them. It only ever adds to what the product looks like.
# ===========================================================================

def do_add_view(raw: bytes, sku_id: str, *, force: bool = False) -> dict[str, Any]:
    """Append another appearance of a product that is already taught."""
    embed = load_embedder()
    store = load_store()

    known = taught_skus()
    rec = next((r for r in known if r.sku_id == sku_id), None)
    if rec is None:
        priced = priced_skus()
        if sku_id in priced:
            # A code-only product. It has a price and a binding and NO
            # appearance at all, which is exactly the state that reads as
            # "recognition is broken" when someone holds it up to the camera.
            # Adding a first view is the fix, and it goes through the ordinary
            # teaching path rather than this one, because a first view has to
            # clear the collision guard from a standing start.
            raise UploadRefused(
                "taught_by_code_only",
                f"{sku_id!r} was taught from its printed code, so nothing about "
                f"what it looks like was ever stored and there is no view to add "
                f"to. Teach it again from a photograph on the PRODUCTS screen — "
                f"its price and its code binding are kept.")
        raise UploadRefused(
            R_UNKNOWN_SKU,
            f"{sku_id!r} is not in the catalogue, so there is nothing to add a "
            f"view to. Taught: {sorted(r.sku_id for r in known) or 'nothing yet'}.")

    bgr, _note = decode_upload(raw)
    crop, region = plain_crop(bgr)              # raises, by name, if unusable

    t0 = time.perf_counter()
    views = _two_orientations(crop)
    try:
        fresh = [np.asarray(embed(c), dtype=np.float64).ravel() for c in views]
    except Exception as exc:
        raise UploadRefused(
            R_NO_EMBEDDER,
            f"gawaah.embedder.embed failed on a {crop.shape[1]}x{crop.shape[0]} "
            f"crop: {type(exc).__name__}: {exc}") from None
    embed_ms = round((time.perf_counter() - t0) * 1000, 2)

    existing = [np.asarray(v, dtype=np.float64).ravel() for v in rec.vectors]

    # IS THIS EVEN THE SAME PRODUCT? Nothing stops someone photographing a bag
    # of rice while the Parle-G card is open, and a wrong vector appended to a
    # gallery is permanent and silent: the product simply starts matching the
    # wrong things and no screen ever says why. So the new view has to look at
    # least a little like the ones already stored.
    best_own = max((float(np.dot(f, e)) for f in fresh for e in existing),
                   default=0.0)
    if best_own < ADD_VIEW_FLOOR and not force:
        raise UploadRefused(
            "does_not_look_like_this_product",
            f"This photograph scores {best_own:.3f} against the views already "
            f"stored for {sku_id!r}, below the {ADD_VIEW_FLOOR:.2f} floor — it "
            f"may be a different product. Another angle of the SAME packet "
            f"normally scores well above that. Nothing was added.")

    # AND IS IT NOW SOMEONE ELSE'S PRODUCT? The same guard enrolment runs,
    # asked of the combined gallery: a new view that happens to look like a
    # DIFFERENT sku makes both of them permanently ambiguous at the till.
    try:
        collision = matless_identifier(
            known, embed, drop=sku_id).check_collision(fresh, None)
    except IdentityError as exc:
        raise UploadRefused(R_IDENTITY, f"{exc}") from None
    if collision.collides and not force:
        raise UploadRefused(
            R_COLLISION,
            f"This view of {sku_id!r} is indistinguishable from "
            f"{collision.sku_id!r} — cosine {collision.similarity:.4f} against a "
            f"bar of {1.0 - THETA:.2f}. Adding it would make BOTH products "
            f"ambiguous at the till, so nothing was added. Photograph a face of "
            f"the packet that the other product does not share.")

    combined = existing + fresh
    if len(combined) > MAX_VIEWS_PER_SKU:
        raise UploadRefused(
            "too_many_views",
            f"{sku_id!r} already holds {len(existing)} views and the limit is "
            f"{MAX_VIEWS_PER_SKU}. Every view is compared against on every "
            f"frame, so an unbounded gallery slows the till for everyone. "
            f"Forget the product and teach it again if its packaging changed.")

    where = "shop_store"
    if rec.storage == "appearance_only_sidecar":
        _ao_put(sku_id, rec.name, int(rec.price_paise), combined, rec.thumb)
        where = "appearance_only_sidecar"
    else:
        # photo_png=None RETAINS the existing photograph on a replace. The card
        # should keep showing the picture the shopkeeper recognises, not
        # whichever angle happened to be added last.
        result = store.add_sku(sku_id, rec.name, int(rec.price_paise),
                               combined, rec.footprint_mm, photo_png=None)
        if not result.ok:
            raise UploadRefused(
                R_COLLISION if result.collides_with else result.reason,
                f"{result.message or result.reason}"
                + (f" (colliding with {result.collides_with!r})"
                   if result.collides_with else ""))

    return {
        "ok": True, "settles_money": False, "money_note": MONEY_NOTE,
        "reason": "view_added",
        "sku_id": sku_id, "name": rec.name,
        "views_before": len(existing), "views_after": len(combined),
        "added": len(fresh),
        "similarity_to_existing": round(best_own, 4),
        "floor": ADD_VIEW_FLOOR,
        "storage": where,
        "measured": {**region, "embed_ms": embed_ms},
        "price_paise": int(rec.price_paise),
        "price_rupees": rupees_str(int(rec.price_paise)),
        "note": ("Price, name and footprint are unchanged — this only adds to "
                 "what the product looks like."),
    }


@app.post("/shop/{sku_id}/view", dependencies=AUTH_GUARD)
async def add_view_ep(sku_id: str, request: Request) -> JSONResponse:
    """Photograph a taught product from another angle and remember that too."""
    try:
        form = await read_form(request)
        forced = str(form_value(form, "force") or "").strip() in ("1", "true", "yes")
        return JSONResponse(do_add_view(form_image(form), sku_id, force=forced))
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"},
                            status_code=400)


@app.get("/shop", dependencies=AUTH_GUARD)
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


def _deactivate_offers_for(sku_id: str) -> int:
    """Switch off every offer scoped to one deleted product. Returns how many.

    THE ASYMMETRY THIS CLOSES. `gawaah/offers.py` REFUSES to create an offer for
    a sku the shop does not price. It had no opinion about a sku that stops
    being priced afterwards — so deleting a product left "10% off
    lifebuoy_soap" sitting in `offers.json` with `active: true`, invisible
    because the product was gone, and armed. The day somebody re-teaches that
    sku id — the same string, a different packet, a different price — the till,
    the storefront and the gateway all start agreeing on a discount nobody
    chose. The discount is applied by wrapping paisa's own price book, so all
    three agree perfectly; there is no disagreement anywhere to notice.

    An offer scoped to EVERY product (`sku_id is None`) is left alone. It was
    never about this product and deleting one packet is not a reason to end a
    shop-wide sale.

    DEACTIVATED, NOT DELETED, and only `active` moves: the record stays, so the
    shopkeeper can see what was switched off and switch it back on if they
    re-teach the product deliberately. Best-effort, like `publish_price_map`
    above — a product a shopkeeper has stopped selling must come out of the
    catalogue whether or not the offers file can be written — but the count is
    returned rather than swallowed, so the response says what happened.
    """
    try:
        from gawaah import offers as _off
    except Exception:  # noqa: BLE001 - a deployment without offers is survivable
        return 0
    try:
        rows = _off.load_offers()
        hit = [o for o in rows if o.sku_id == sku_id and o.active]
        if not hit:
            return 0
        _off.save_offers([
            replace(o, active=False) if (o.sku_id == sku_id and o.active) else o
            for o in rows
        ])
        # ON THE OFFERS CHAIN, NOT THIS FILE'S. A price change made from here is
        # still a price change, and a reader walking `offers.audit.jsonl` to
        # explain why a discount stopped must find the line. `_audit` is
        # private to that module and this is the one place outside it that
        # calls it; the right home is a public `deactivate_for_sku()` in
        # `gawaah/offers.py`, which this change does not own. Wrapped so a
        # rename there cannot turn a successful delete into a 400.
        try:
            for o in hit:
                _off._audit("offer.deactivated_with_sku", offer_id=o.offer_id,
                            sku_id=sku_id, kind=o.kind, value=int(o.value),
                            why="the product was removed from the catalogue")
        except Exception:  # noqa: BLE001 - a failed audit must not block a delete
            pass
        return len(hit)
    except Exception:  # noqa: BLE001 - see the docstring: best-effort
        return 0


@app.delete("/shop/{sku_id}", dependencies=AUTH_GUARD)
def shop_delete_ep(sku_id: str) -> JSONResponse:
    """Remove one product — and everything that could still price it."""
    try:
        store = load_store()
        # `priced_skus`, not `taught_skus`: a product taught from a printed
        # code alone has no descriptor and is invisible to the vector list,
        # which made it UNDELETABLE — visible in the catalogue, 404 on remove.
        known = set(priced_skus()) | {r.sku_id for r in taught_skus()}
        if sku_id not in known:
            raise UploadRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not in the catalog. Nothing was removed. "
                f"Enrolled: {sorted(known) or 'nothing yet'}.")
        # Every place that could still price it, unconditionally: an
        # appearance-only entry left behind would resurrect the price, and an
        # ORPHANED CODE BINDING is worse — a barcode that keeps pricing a
        # product the shopkeeper deleted, with nothing anywhere saying so.
        # WHO IS STILL WAITING FOR THIS. Asked BEFORE the delete, because
        # afterwards there is no price to explain the orders with. Removing a
        # product cannot be blocked by an order -- a shopkeeper who has stopped
        # stocking something has stopped stocking it -- but they must be told,
        # because the money service re-derives every rupee from its own book
        # and an open order holding a sku it cannot find can never be paid.
        stranded: list[dict[str, Any]] = []
        try:
            from gawaah import storefront as _sf
            stranded = _sf.orders_still_wanting(sku_id)
        except Exception:  # noqa: BLE001 - a missing storefront is not fatal here
            stranded = []

        removed = bool(store.remove(sku_id)) if sku_id in store else False
        removed = bool(_ao_remove(sku_id)) or removed
        codes_dropped = unbind_sku(sku_id)
        offers_off = _deactivate_offers_for(sku_id)
        publish_price_map()
        body: dict[str, Any] = {"ok": bool(removed), "reason": "sku_removed",
                                "sku_id": sku_id, "settles_money": False,
                                "codes_unbound": codes_dropped,
                                "offers_deactivated": offers_off,
                                "count": len(priced_skus())}
        if stranded:
            body["stranded_orders"] = stranded
            body["stranded_warning"] = (
                f"{len(stranded)} open order(s) still contain {sku_id!r}. The money "
                f"service prices every line from its own book, so those orders can no "
                f"longer be paid — the customer will be refused with `amber_in_basket` "
                f"naming this product. Teach it again with a price to make them "
                f"payable, or cancel them.")
        return JSONResponse(body)
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


@app.post("/demo/teach", dependencies=AUTH_GUARD)
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


@app.post("/demo/recognise", dependencies=AUTH_GUARD)
@app.get("/demo/recognise", dependencies=AUTH_GUARD)
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


@app.post("/demo/reference", dependencies=AUTH_GUARD)
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


@app.get("/demo/photo", dependencies=AUTH_GUARD)
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


@app.get("/demo/plain_photo", dependencies=AUTH_GUARD)
def demo_plain_photo_ep(sku: str = "parle_g_biscuit", view: int = 0):
    """An ORDINARY product photograph — one item, plain surface, no mat.

    This is the picture the user actually had: something downloaded, with no
    markers anywhere in it. view=0/1/2 are three genuinely different shots of
    the same object (different distance, angle, position and surface colour,
    independent noise), so a round trip taught on one and recognised on another
    cannot pass by re-presenting the same pixels.
    """
    from fastapi.responses import Response
    p = PRODUCTS_BY_ID.get(sku)
    if p is None:
        return JSONResponse({"ok": False, "reason": R_UNKNOWN_SKU,
                             "detail": f"no sample product {sku!r}; have "
                                       f"{sorted(PRODUCTS_BY_ID)}"},
                            status_code=404)
    return Response(plain_photo_png(p, int(view)), media_type="image/png",
                    headers={"X-Gawaah-Simulated": "true",
                             "X-Gawaah-Has-Mat": "false"})


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
 /* the weaker path, offered but never disguised as the strong one */
 .card.alt{background:#1a1508;border-color:#4a3a12}
 .warn{background:#31260f;border-left:3px solid var(--amb);color:var(--amb);
   padding:9px 12px;border-radius:0 6px 6px 0;font-size:12.5px;line-height:1.5}
 .btn{background:var(--card);border:1px solid var(--rule);color:var(--fg);border-radius:7px;
   padding:9px 15px;font:600 12.5px ui-monospace,Menlo,monospace;cursor:pointer}
 .btn:hover{border-color:#5c6470}
 input{background:#0f1216;border:1px solid var(--rule);color:var(--fg);border-radius:7px;
   padding:9px 11px;font:13px ui-monospace,Menlo,monospace}
 input:focus{outline:none;border-color:#5c6470}
 .btn.amber{background:#31260f;border-color:#6a5218;color:var(--amb)}
 .btn.amber:hover{background:#3d2f13}
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

function refusalCard(r, opts){
  const d = r.diagnosis||{}, alt = r.alternative, o = opts||{};
  let h = '<div class=card><div class="head amb">I DO NOT KNOW — '+esc(r.reason)+'</div>';
  h += '<div>'+esc(r.detail||d.headline||'')+'</div>';
  if(d.markers_found!=null)
    h += '<div class=k style="margin-top:8px"><b>markers</b><span>'+d.markers_found+' of '+(d.markers_expected||4)+
         (d.corners_missing&&d.corners_missing.length?' — missing '+esc(d.corners_missing.join(', ')):'')+'</span></div>';
  if(d.fix&&d.fix.length){h+='<ul class=fix>';d.fix.forEach(f=>h+='<li>'+esc(f)+'</li>');h+='</ul>';}
  h += '<div class=sub style="margin-top:10px">Nothing was stored and nothing was priced.</div>';

  // The mat is the strong path, not the only one. When the server says a
  // weaker path exists, OFFER it -- one button, consequence stated first.
  // Not an automatic retry: dropping the size check is a real loss of a
  // safety property, so a human takes that step deliberately.
  if(alt && o.action){
    h += '<div class="card alt" style="margin-top:14px">'
       + '<div class=head>NO MAT IN THIS PHOTO?</div>'
       + '<div class=sub style="margin-top:6px">'+esc(alt.what_you_get||'')+'</div>'
       + '<div class="warn" style="margin-top:8px">'+esc(alt.what_it_costs||'')+'</div>'
       + '<button class="btn amber" id="'+esc(o.action)+'" style="margin-top:10px">'+esc(o.label)+'</button>'
       + '</div>';
  }
  return h+'</div>';
}

async function teach(noMat){
  const f = $('#t_file').files[0];
  if(!f){ $('#teachout').innerHTML='<div class="card bad">Choose a photo first.</div>'; return; }
  const fd = new FormData();
  fd.append('image', f);
  fd.append('sku_id', $('#t_sku').value);
  fd.append('name', $('#t_name').value);
  fd.append('price_rupees', $('#t_price').value);
  if(noMat) fd.append('mode','plain_photo');
  $('#teachout').innerHTML = '<div class=card>'
    + (noMat ? 'no mat — segmenting the product off its background, embedding…'
             : 'locking the mat, measuring, embedding…') + '</div>';
  const r = await post('/enrol', fd);
  if(!r.ok){
    $('#teachout').innerHTML = refusalCard(r, {action:'t_anyway', label:'TEACH IT ANYWAY (no mat)'});
    const b = $('#t_anyway'); if(b) b.addEventListener('click', ()=>teach(true));
    loadShop(); return;
  }
  const m=r.measured, s=r.stored, c=r.collision, appearance = !!r.appearance_only;
  let h = '<div class=card><div class="head '+(appearance?'amb':'ok')+'">TAUGHT'
        + (appearance?' (APPEARANCE ONLY)':'')+' — '+esc(s.sku_id)+'</div>';
  h += '<div class=grid style="margin-top:10px">';
  // Two different things were learned, so two different things get shown.
  // Printing "footprint: null" under a millimetre heading would be a lie of
  // layout: the mat path knows a size, this path provably does not.
  if(appearance){
    h += '<div><div class=k><b>millimetres</b><span class=amb>none — no mat in this photo</span></div>'
       + '<div class=k><b>how it was found</b><span>'+esc(m.region_source||'')+'</span></div>'
       + '<div class=k><b>region of the frame</b><span>'+Math.round((m.largest_region_frac||0)*100)+'%</span></div>'
       + '<div class=k><b>crop the embedder saw</b><span>'+(m.crop_px?m.crop_px.join(' × '):'')+' px</span></div>'
       + '<div class=k><b>size check</b><span class=amb>'+esc(s.size_check||'none')+'</span></div></div>';
  } else {
    h += '<div><div class=k><b>measured long edge</b><span>'+m.long_edge_mm+' mm</span></div>'
       + '<div class=k><b>short edge</b><span>'+m.short_edge_mm+' mm</span></div>'
       + '<div class=k><b>angle on the mat</b><span>'+m.angle_deg+'°</span></div>'
       + '<div class=k><b>candidates on the mat</b><span>'+m.candidates_considered+' (largest taken)</span></div>'
       + '<div class=k><b>reference</b><span class="'+(r.reference_source==='empty_mat_photo_supplied'?'ok':'amb')+'">'+esc(r.reference_source)+'</span></div></div>';
  }
  h += '<div><div class=k><b>stored price</b><span class=ok>'+s.price_paise+' paise = ₹'+esc(s.price_rupees)+'</span></div>'
     + '<div class=k><b>footprint stored</b><span class="'+(appearance?'amb':'')+'">'
       + (appearance?'none':(s.footprint_mm+' mm'))+'</span></div>'
     + '<div class=k><b>bar it must clear</b><span>cosine ≥ '+(s.phi_used!=null?s.phi_used:r.gates.phi)
       + (appearance?' (raised, no size check)':'')+'</span></div>'
     + '<div class=k><b>vector</b><span>'+s.vector_dim+'-d, '+s.embed_ms+' ms</span></div>'
     + '<div class=k><b>action</b><span>'+esc(s.store_action)+(s.replaced_existing?' (replaced)':'')+'</span></div>'
     + '<div class=k><b>collision check</b><span class="'+(c.collides?'bad':'ok')+'">'+(c.collides?esc(c.message):'clear')+'</span></div></div>';
  h += '</div>';
  if(r.crop_png) h += '<div style="margin-top:12px"><div class=sub>the crop the embedder saw</div>'
     + '<img class=thumb style="width:auto;height:120px;margin-top:6px" src="data:image/png;base64,'+r.crop_png+'"></div>';
  if(appearance && r.warning) h += '<div class="warn" style="margin-top:12px">'+esc(r.warning)+'</div>';
  if(appearance && r.better) h += '<div class=sub style="margin-top:8px"><b>To make it stronger:</b> '+esc(r.better)+'</div>';
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
  // An empty catalogue has two very different causes and they must not look
  // the same: nothing was ever taught, or what WAS taught is no longer
  // comparable. The second one names the products and says why.
  const nr = r.needs_reteach || {};
  const reteach = nr.n>0
    ? '<div class="card alt"><div class="head amb">'+nr.n+' product'+(nr.n===1?'':'s')
      + ' must be taught again</div><div class=sub style="margin-top:6px">'+esc(nr.why||'')+'</div>'
      + '<div style="margin-top:10px">'+nr.skus.map(s=>'<div class=k><b>'+esc(s.sku_id)+'</b><span>'
        + esc(s.name)+(s.price_paise!=null?' — ₹'+(s.price_paise/100).toFixed(2):'')+'</span></div>').join('')
      + '</div></div>'
    : '';
  if(!r.count){
    $('#shopout').innerHTML = reteach + '<div class=card><div class=head>Nothing taught yet</div>'
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
       + '<td style="white-space:nowrap">'
       + '<a href="/qr/'+encodeURIComponent(s.sku_id)+'" target="_blank" class=sub '
       + 'style="margin-right:10px">print code</a>'
       + '<button class=x onclick="removeSku(\''+esc(s.sku_id)+'\')">REMOVE</button></td></tr>';
  });
  h += '</table></div>';
  h += '<div class=k style="margin-top:8px"><b>taught</b><span>'+r.count+' sku'+(r.count>1?'s':'')
     + ', '+r.priced+' priced</span></div>';
  h += '<div class=k><b>gates</b><span>φ '+r.gates.phi+' similarity · θ '+r.gates.theta
     + ' margin · τ '+r.gates.tau_mm+' mm footprint</span></div></div>';
  $('#shopout').innerHTML = reteach + h;   // a partial loss must show too
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

let lastRecogniseFile = null;
async function tryIt(noMat){
  const f = $('#r_file').files[0] || lastRecogniseFile;
  if(!f) return;
  lastRecogniseFile = f;              // kept so the no-mat retry has the photo
  $('#recout').innerHTML = '<div class=card>'
    + (noMat ? 'no mat — segmenting one subject, embedding, matching…'
             : 'locking, measuring, embedding, matching…') + '</div>';
  const fd = new FormData(); fd.append('image', f);
  if(noMat) fd.append('mode','plain_photo');
  const r = await post('/recognise', fd);
  if(!r.ok && r.alternative && !noMat){
    $('#recout').innerHTML = refusalCard(r, {action:'r_anyway', label:'READ IT ANYWAY (no mat)'});
    const b = $('#r_anyway'); if(b) b.addEventListener('click', ()=>tryIt(true));
  } else {
    $('#recout').innerHTML = recogniseCard(r);
  }
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



@app.get("/", response_class=HTMLResponse, dependencies=AUTH_GUARD)
def index() -> HTMLResponse:
    """The counter.

    There used to be a 2,500-line copy of this page written as an inline-script
    HTML string right here, served at `/legacy`, `/classic` and `/live` and used
    as a fallback when the React build was missing. It was the last thing in the
    system asking the Content-Security-Policy to allow `unsafe-inline`, and it
    was a second front end that could disagree with the first — which it did:
    three panels were dead for a day because the two copies disagreed about the
    type of a global.

    So it is gone, and with it the inline-script exception. A checkout with no
    build gets an instruction, not a different product.
    """
    built = UI_DIST / "index.html"
    if built.is_file():
        return HTMLResponse(built.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>GAWAAH</title>"
        "<body style=\"font:16px/1.6 system-ui;max-width:34rem;margin:15vh auto;padding:0 1.5rem\">"
        "<h1 style=\"font-size:1.4rem\">The front end has not been built.</h1>"
        "<p>Run <code style=\"background:#eee;padding:.15rem .4rem;border-radius:4px\">make ui</code>"
        " and reload. The API on this port is already running.</p>",
        status_code=503,
        # ASCII ONLY. Header values are latin-1 on the wire, so an em
        # dash here raised UnicodeEncodeError inside Starlette and turned a
        # clear 503 into a 500 with a stack trace — the failure path failing.
        headers={"X-Gawaah-UI": "no build - run `make ui`"},
    )



# ---------------------------------------------------------------- one origin
#
# The site is ONE page, but the system behind it is deliberately three
# processes: this one holds the catalogue, `paisa` holds the keys, and the mat
# counter holds the camera loop. That split is invariant 5 -- paisa is the sole
# secret holder and re-runs the crossing predicate server-side -- and merging
# the processes to make the URL bar tidier would dissolve it.
#
# So the PAGE is unified and the SERVICES are not. These two routes exist only
# so the page can read money state from its own origin instead of asking a
# browser to cross-origin its way to 8788 mid-demo.
#
# ONLY the read-only pair is bridged. `/intent` mints a payable link and
# `/webhook` is the one input that can turn a counter green; proxying either
# would put this server in the money path and make it a second place where the
# decision lives. They are absent from this file on purpose.
PAISA_BASE = os.environ.get("GAWAAH_PAISA_URL", "http://127.0.0.1:8788")
PAISA_TIMEOUT_S = 6


def _paisa_get(path: str) -> tuple[int, dict[str, Any]]:
    """GET one read-only path from paisa. Never raises, never carries a secret."""
    import http.client
    import json as _json
    import urllib.error
    import urllib.request

    url = f"{PAISA_BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=PAISA_TIMEOUT_S) as r:
            return r.status, _json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, _json.loads(e.read().decode())
        except Exception:  # noqa: BLE001 - error bodies are not guaranteed JSON
            return e.code, {"ok": False, "reason": f"paisa returned HTTP {e.code}"}
    except (http.client.InvalidURL, ValueError) as exc:
        # OUR request was malformed — no socket was ever opened, so blaming
        # the service ("not currently running") sent people to debug a healthy
        # money process over a space in a session id.
        return 400, {"ok": False, "reason": "bad_request_path",
                     "detail": f"This request could not be formed into a URL "
                               f"({type(exc).__name__}). The money service was "
                               f"never contacted and nothing can be said about it."}
    except Exception as exc:  # noqa: BLE001 - urllib raises a wide family
        # Named, not blank. A money panel that just goes empty when the service
        # is down looks identical to a money panel with nothing to report.
        return 503, {
            "ok": False,
            "reason": "paisa_unreachable",
            "detail": (
                f"The money service did not answer at {PAISA_BASE} "
                f"({type(exc).__name__}). Nothing here settles money in any "
                f"case — this panel is a read-only view of a service that is "
                f"not currently running."),
        }


@app.get("/api/money/health", dependencies=AUTH_GUARD)
def money_health_ep() -> JSONResponse:
    status, body = _paisa_get("/health")
    return JSONResponse(body, status_code=200 if status == 200 else status)


@app.post("/api/money/mint", dependencies=AUTH_GUARD)
async def money_mint_ep(request: Request) -> JSONResponse:
    """Forward a mint to paisa. This server adds NOTHING to the body.

    Three fields go through unchanged — session_id, amount_paise and the scan
    id — and none of them is evidence. paisa loads the witness by that id and
    re-derives every rupee from its own binding table and its own price book,
    so neither the browser nor this process can author a line. The forward
    exists only so the page can stay on one origin; it is not a step in the
    decision, and it is why the body is copied field by field rather than
    passed along as whatever arrived.
    """
    import json as _json
    import urllib.error
    import urllib.request

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": "bad_request",
                             "detail": "the mint body must be JSON"},
                            status_code=400)
    scan = (body or {}).get("scan") or {}
    forward = {
        "session_id": str((body or {}).get("session_id") or ""),
        "amount_paise": (body or {}).get("amount_paise"),
        "scan": {"scan_id": str(scan.get("scan_id") or "")},
    }
    req = urllib.request.Request(
        f"{PAISA_BASE}/intent", data=_json.dumps(forward).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return JSONResponse(_json.loads(r.read().decode()), status_code=r.status)
    except urllib.error.HTTPError as e:
        try:
            return JSONResponse(_json.loads(e.read().decode()), status_code=e.code)
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"paisa_http_{e.code}"},
                                status_code=e.code)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({
            "ok": False, "reason": "paisa_unreachable",
            "detail": f"The money service did not answer at {PAISA_BASE} "
                      f"({type(exc).__name__}). Nothing was minted."},
            status_code=503)


@app.get("/api/money/session/{session_id}", dependencies=AUTH_GUARD)
def money_session_ep(session_id: str) -> JSONResponse:
    # quote(safe="") or a '#' in the id silently truncates the upstream path
    # and this proxy answers about a DIFFERENT session — measured: a request
    # for 'sess#1' returned session 'sess' with nothing marking the swap.
    from urllib.parse import quote

    status, body = _paisa_get("/session/" + quote(session_id, safe=""))
    return JSONResponse(body, status_code=200 if status == 200 else status)


def _paisa_post_json(path: str, forward: dict[str, Any], *,
                     timeout_s: int = 30) -> JSONResponse:
    """Forward one POST to paisa, adding nothing. Same discipline as the mint
    forward: the body is a dict this process built field by field, never
    whatever arrived, so the browser cannot smuggle a field paisa would read."""
    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{PAISA_BASE}{path}", data=_json.dumps(forward).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return JSONResponse(_json.loads(r.read().decode()), status_code=r.status)
    except urllib.error.HTTPError as e:
        try:
            return JSONResponse(_json.loads(e.read().decode()), status_code=e.code)
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"paisa_http_{e.code}"},
                                status_code=e.code)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({
            "ok": False, "reason": "paisa_unreachable",
            "detail": f"The money service did not answer at {PAISA_BASE} "
                      f"({type(exc).__name__}). Nothing moved."},
            status_code=503)


# ------------------------------------------------------------- WAAPSI: returns
#
# The refund forwards. The browser hands over the bill, the line and the paise
# it believes were charged; paisa re-derives all three from its own tables and
# the signed audit chain before it asks the gateway, and REFUNDED lands only on
# a signed refund.processed at /webhook. None of these routes decides anything;
# they exist so the return page can stay on one origin.

@app.post("/api/money/refund", dependencies=AUTH_GUARD)
async def money_refund_ep(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": "bad_request",
                             "detail": "the refund body must be JSON"}, status_code=400)
    forward = {
        "session_id": str((body or {}).get("session_id") or ""),
        "item_id": str((body or {}).get("item_id") or ""),
        "sku_id": str((body or {}).get("sku_id") or ""),
        "amount_paise": (body or {}).get("amount_paise"),
    }
    return _paisa_post_json("/refund", forward)


@app.get("/api/money/refund/{refund_key}", dependencies=AUTH_GUARD)
def money_refund_view_ep(refund_key: str) -> JSONResponse:
    from urllib.parse import quote

    status, body = _paisa_get("/refund/" + quote(refund_key, safe=""))
    return JSONResponse(body, status_code=200 if status == 200 else status)


@app.get("/api/money/refunds/{session_id}", dependencies=AUTH_GUARD)
def money_refunds_ep(session_id: str) -> JSONResponse:
    from urllib.parse import quote

    status, body = _paisa_get("/refunds/" + quote(session_id, safe=""))
    return JSONResponse(body, status_code=200 if status == 200 else status)


@app.post("/api/money/sim/refund", dependencies=AUTH_GUARD)
async def money_sim_refund_ep(request: Request) -> JSONResponse:
    """Simulator only: the gateway's back office processes a refund and pushes
    the signed callback. paisa refuses this by name on the live gateway."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": "bad_request",
                             "detail": "the body must be JSON"}, status_code=400)
    forward = {
        "refund_key": str((body or {}).get("refund_key") or ""),
        "outcome": str((body or {}).get("outcome") or "processed"),
    }
    return _paisa_post_json("/sim/refund", forward)


#: Hosts a payable link may live on. A `short_url` pointing anywhere else is not
#: encoded into a QR, whatever the money service said -- the one thing a
#: customer's phone will act on without reading is the one thing that gets an
#: allowlist.
def _looks_like_upi(url: str) -> bool:
    """True for anything that could be read as a UPI payment payload.

    Checked on the string with leading control characters and whitespace
    stripped, because "\\tupi://pay?pa=..." is still a UPI payload to every
    scanner that will ever read it off a screen.
    """
    return url.lstrip("\x00-\x20 \t\r\n").lower().lstrip().startswith("upi:")


#: The host the SIMULATOR mints on. `gawaah/rzp_sim.py` moved off the gateway's
#: own domain shape on purpose — a test double that mints `rzp.io/i/<token>` is
#: a forgery primitive sitting in the codebase (invariant 6). `.invalid` is
#: reserved by RFC 2606 and can never resolve, so a link here cannot be paid,
#: cannot be phished with, and cannot be mistaken for a real one by anything
#: that tries to follow it.
#:
#: IT IS ON THE ALLOWLIST, AND THAT IS NOT A HOLE. The list answers "is this
#: shaped like a payable link this counter minted", and in sim mode it is —
#: leaving it off meant a customer who pressed PAY on the storefront got
#: `refused_to_show_this_string` and no way to pay at all, on every counter
#: not wired to a live gateway. What keeps the invariant is not this list: it
#: is that nothing here CONSTRUCTS a payable string, and that only a
#: signature-verified webhook can turn a bill green.
SIM_LINK_HOST = "pay.gawaah-sim.invalid"

#: Hosts a payable link may live on. Real gateway hosts, plus the simulator's
#: unresolvable one. Every consumer reads this — receipts, khata, shopface and
#: the storefront all defer to it rather than keeping a second copy.
LINK_HOSTS = ("rzp.io", "razorpay.com", "rzp.link", SIM_LINK_HOST)
R_REFUSED_QR = "refused_to_encode_this_string"


@app.get("/qr/link/{session_id}", dependencies=AUTH_GUARD)
def payment_qr_ep(session_id: str, px: int = 620):
    """A QR of the payable link Razorpay minted for this session.

    THE PAGE NEVER CHOOSES THE BYTES. A browser hands over a session id; this
    route fetches the session from paisa, takes `short_url` off it, checks the
    host, and encodes THAT. There is no QR encoder in browser source and no code
    path anywhere in this program that builds a `upi://` payload -- constructing
    a payment target locally is a forgery primitive (invariant 6) and there is
    no version of it that is acceptable.

    `short_url` is an opaque token minted by the gateway. Rendering a QR of a
    string somebody else issued is not the same act as composing one.
    """
    from fastapi.responses import Response

    try:
        from urllib.parse import quote

        status, sess = _paisa_get("/session/" + quote(session_id, safe=""))
        if status == 404:
            raise UploadRefused(
                "session_not_found",
                f"The money service has no record of session {session_id!r}. "
                f"Either nothing was ever minted into it, or it was minted "
                f"before the money service last restarted — sessions live in "
                f"memory and do not survive one. Either way there is nothing "
                f"payable to show from here.")
        if status != 200 or not isinstance(sess, dict):
            # NOT session_not_found: a 503 means the service did not answer,
            # which says nothing about whether the session exists. Collapsing
            # every failure into "no session" reported a total outage as a
            # client error.
            raise UploadRefused(
                (sess or {}).get("reason") or "money_service_error",
                (sess or {}).get("detail") or
                f"The money service answered HTTP {status}, so whether this "
                f"session exists could not be determined. Nothing was encoded.")
        url = None
        for key in ("short_url", "payment_link_short_url"):
            if isinstance(sess.get(key), str):
                url = sess[key]
                break
        if url is None:
            for it in (sess.get("intents") or []):
                if isinstance(it, dict) and isinstance(it.get("short_url"), str):
                    url = it["short_url"]
                    break
        if not url:
            raise UploadRefused(
                "no_payable_link_on_this_session",
                "This session carries no short_url, so nothing has been minted "
                "for it yet. Nothing was encoded.")
        # INVARIANT 6. Nothing is encoded that did not come from the gateway.
        #
        # The authority used to be found by hand:
        #     url.split("//",1)[-1].split("/",1)[0]
        # which does not stop at "?", "#" or "\\". So
        # "https://evil.com#.rzp.io" produced a host of "evil.com#.rzp.io",
        # which ends with ".rzp.io", and was ENCODED. urlsplit alone does not
        # close it either: "https://evil.com\\.rzp.io" is one host to RFC 3986
        # and two to WHATWG, so a browser and this parser would disagree about
        # where the payload points.
        #
        # Parse properly, then require the host to be nothing but the characters
        # a hostname may contain. That charset check is what kills the backslash
        # variant, and it is the load-bearing line here.
        #
        # Not browser-reachable today — short_url comes only off the gateway
        # document — but a guard on the forgery invariant has to hold on its own.
        url = url.strip()
        if _looks_like_upi(url):
            raise UploadRefused(
                R_REFUSED_QR,
                "That string is a UPI payload, not a gateway link. This program "
                "does not encode payment targets it did not receive from the "
                "gateway, and will not start now.")
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https"):
            raise UploadRefused(
                R_REFUSED_QR,
                f"A payable link must be http or https, not {parts.scheme!r}. "
                f"Nothing was encoded.")
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise UploadRefused(
                R_REFUSED_QR,
                "That link's host is not a plain hostname, so where it actually "
                "points cannot be agreed on. Nothing was encoded.")
        if not any(host == h or host.endswith("." + h) for h in LINK_HOSTS):
            raise UploadRefused(
                R_REFUSED_QR,
                f"The link points at {host!r}, which is not one of the gateway "
                f"hosts a payable link may live on ({', '.join(LINK_HOSTS)}). "
                f"Nothing was encoded.")

        enc = cv2.QRCodeEncoder.create()
        q = enc.encode(url)
        q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
        side = max(200, min(int(px), 1600))
        q = cv2.resize(q, (side, side), interpolation=cv2.INTER_NEAREST)
        pad = side // 14
        card = np.full((side + 2 * pad, side + 2 * pad), 255, np.uint8)
        card[pad:pad + side, pad:pad + side] = q
        ok, buf = cv2.imencode(".png", cv2.cvtColor(card, cv2.COLOR_GRAY2BGR))
        if not ok:
            raise UploadRefused(R_INTERNAL, "the QR would not encode")
        return Response(buf.tobytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "X-Gawaah-Link-Host": host})
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"},
                            status_code=400)


@app.get("/qr/{sku_id}", dependencies=AUTH_GUARD)
def qr_sticker_ep(sku_id: str, px: int = 700):
    """A printable product sticker for one taught sku.

    Deliberately carries the sku id and NOT the price. A price printed on a
    sticker is a second place for it to live, and the day the shopkeeper
    changes it in the catalogue the two disagree with no way to tell which is
    real. The code names the product; the catalogue prices it; there is one
    source of truth for money and it is not stuck to the packet.
    """
    from fastapi.responses import Response

    try:
        # The union, not `taught_skus` alone: a product taught from a printed
        # code has no descriptor and is invisible to the vector list, which
        # made its sticker link a 400 opening a tab of raw JSON — while the
        # product sat priced in the very table the user clicked from. The
        # sticker only needs a name; a gawaah: sticker resolves straight to
        # the sku id at the till, so it works for a code-only product exactly
        # as well as for a photographed one.
        known = {r.sku_id: r.name for r in taught_skus()}
        for k, v in priced_skus().items():
            known.setdefault(k, v["name"])
        name = known.get(sku_id)
        if name is None:
            raise UploadRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not in the catalog, so there is nothing to "
                f"print a code for. Taught: {sorted(known) or 'nothing yet'}.")
        enc = cv2.QRCodeEncoder.create()
        q = enc.encode(f"{QR_PREFIX}{sku_id}")
        q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
        side = max(140, min(int(px), 2000))
        q = cv2.resize(q, (side, side), interpolation=cv2.INTER_NEAREST)

        pad, foot = side // 10, side // 4
        card = np.full((side + 2 * pad + foot, side + 2 * pad), 255, np.uint8)
        card[pad:pad + side, pad:pad + side] = q
        card = cv2.cvtColor(card, cv2.COLOR_GRAY2BGR)
        base = side / 700.0
        cv2.putText(card, name[:26], (pad, side + pad + int(46 * base)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.78 * base, (20, 20, 20),
                    max(1, int(2 * base)), cv2.LINE_AA)
        cv2.putText(card, f"{QR_PREFIX}{sku_id}",
                    (pad, side + pad + int(86 * base)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52 * base, (120, 120, 120),
                    max(1, int(1 * base)), cv2.LINE_AA)
        cv2.putText(card, "price lives in the catalogue, not on this sticker",
                    (pad, side + pad + int(122 * base)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40 * base, (150, 150, 150),
                    1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", card)
        if not ok:
            raise UploadRefused(R_INTERNAL, "the sticker would not encode")
        return Response(buf.tobytes(), media_type="image/png",
                        headers={"Content-Disposition":
                                 f'inline; filename="{sku_id}_gawaah_qr.png"'})
    except UploadRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return JSONResponse({"ok": False, "reason": R_INTERNAL,
                             "detail": f"{type(exc).__name__}: {exc}"},
                            status_code=400)


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
