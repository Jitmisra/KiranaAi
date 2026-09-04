"""Where are the things on this counter?

SEPARATE THE TWO QUESTIONS. "Where is an object" and "which product is it" are
different problems and they want different machinery. Conflating them is why a
generic vision model disappoints on a kirana counter: asked to do both, it does
the first adequately and the second not at all.

    1. WHERE   this module. Class-agnostic regions. No product knowledge.
    2. WHICH   gawaah/recogniser.py, against the shop's OWN taught vectors.

MEASURED, BEFORE ANY OF THIS WAS WRITTEN. A COCO-trained YOLOv5n scores its
best guess on the three products this shop has photographed as:

    lifebuoy_soap    max objectness 0.233   best class "person"
    parle_g_biscuit  max objectness 0.126   best class "person"
    shampoo_sachet   max objectness 0.286   best class "cell phone"

Nothing above the usual 0.25 gate on two of three, and a class column that is
noise. That is not a defect in YOLO — a bar of Lifebuoy is not one of the
eighty things it was trained on, and no amount of tuning adds a class that is
not in the weights. So this module uses YOLO for exactly what it is good for
here, which is proposing that SOMETHING occupies a region, and ignores its
class head entirely.

And because objectness alone is unreliable on packets, YOLO is not the only
proposer. A counter is a broadly uniform surface with colourful things on it,
which is a segmentation problem that classical CV solves well and that needs no
weights at all. The contour proposer is the workhorse; YOLO adds recall on the
objects it does know (bottles, cups, phones) and is optional — this module
works with the model file absent, which is why `MODEL_PATH` missing is a
condition, not an error.

WHAT THIS MODULE MAY NOT DO. It may not name a product, it may not price
anything, and a region it proposes is not a claim that a product is there. It
hands boxes to a caller that has its own gate. Nothing here settles money.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

MODULE = "detector"

#: Where the optional ONNX detector lives. Absent is a supported state.
MODEL_PATH = Path(
    os.environ.get(
        "GAWAAH_YOLO_ONNX",
        str(Path(__file__).resolve().parent.parent / "models" / "yolov5n.onnx"),
    )
)

#: YOLO's letterbox input. 640 is what the exported graph expects.
YOLO_SIDE = 640

#: Objectness floor for a CLASS-AGNOSTIC proposal.
#:
#: Deliberately below the 0.25 a classifier would use. This is not a detection
#: threshold — a proposal is only a suggestion that something is here, and the
#: cosine gate downstream is what actually decides. Measured above: real
#: packets sit at 0.13-0.29, so a 0.25 floor discards most of them and a 0.10
#: floor keeps them at the cost of some empty boxes. An empty box costs one
#: embed and is then rejected by the recogniser; a missing box costs an item
#: off the customer's bill. Those are not symmetric, so this errs low.
YOLO_OBJECTNESS_FLOOR = 0.10

#: A region smaller than this fraction of the frame is noise, not a packet.
MIN_AREA_FRAC = 0.004
#: Larger than this and it is the counter, a hand, or the whole scene.
MAX_AREA_FRAC = 0.45
#: Wildly elongated regions are edges of the counter, cable, or a shadow seam.
MAX_ASPECT = 6.0
#: Two boxes overlapping by more than this are the same object seen twice.
NMS_IOU = 0.40
#: Never hand back more than this many. A counter with more than a dozen
#: distinct items on it is a scene this product does not claim to bill.
MAX_PROPOSALS = 12

#: The COCO classes that CANNOT be a thing for sale on a shelf, by index.
#:
#: THIS IS THE OPPOSITE QUESTION TO THE ONE FAILURES.md REJECTED, and the
#: distinction is the whole reason it is allowed here. Asked "which product is
#: this", a COCO model is useless on a kirana counter, because a bar of
#: Lifebuoy is not one of the eighty things in the weights — measured, best
#: class "person" at 0.233. Asked "is this a person", it is answering about a
#: class that IS in the weights, and it is good at it. Measured on real
#: photographs rather than on a drawing, because a drawn face proves nothing:
#:
#:     zidane.jpg   person 0.787, person 0.493
#:     bus.jpg      person 0.813, person 0.804, person 0.615, bus 0.550
#:
#: So the class head is read for one purpose only — to say that a region is
#: something a shop does not sell — and never to say what a product is.
#:
#: What is NOT on this list matters as much as what is, and the exclusions are
#: measured, not guessed. A bottle, a cup, a bowl, a book, a clock and a
#: toothbrush are all COCO classes AND all things a kirana actually sells, so
#: none of them may veto anything.
#:
#: NEITHER MAY ANY BOX-SHAPED CLASS, WHICH COST A REAL PRODUCT WHEN TRIED. The
#: first version of this list included the appliances, on the reasoning that a
#: shop does not sell a fridge. Measured on the bench frame, a white medicine
#: carton scored:
#:
#:     laptop 0.423, over carton B, IoU 0.84 with the real packet
#:
#: — so `laptop` in this dict DELETES a product the shopkeeper is holding up to
#: the camera, and a missing line is the worst failure this counter has. A
#: printed carton IS a white box, so every white-box class is a class this
#: model will sometimes give it: tv, laptop, microwave, oven, toaster,
#: refrigerator and sink are all off the list for that one reason.
#:
#: What is left is the shapes a packet never has: living things, and furniture
#: too big and too soft to be stock. Cropped out and shown alone, the same two
#: cartons score NOTHING over 0.10 on any class, which is the FAILURES.md
#: finding intact — they are only ever mislabelled in context.
_NOT_A_FACING: dict[int, str] = {
    0: "person",
    14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow",
    20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe",
    13: "bench", 56: "chair", 57: "couch", 58: "potted plant", 59: "bed",
    60: "dining table", 61: "toilet",
}

#: The bar a REJECTION has to clear. Deliberately far above
#: `YOLO_OBJECTNESS_FLOOR`, and for the opposite reason: a proposal is only a
#: suggestion and errs low because a missing box costs a line off the bill, but
#: a veto DELETES a region and can therefore cost the same line. So a veto is
#: only allowed when the model is as sure as an ordinary detector would demand.
YOLO_REJECT_CONF = 0.25

#: How much of a proposal a rejection has to account for before it may veto it.
#: This is IoU, not containment, and that is deliberate: a person HOLDING a
#: packet has a box that contains the packet completely, so a containment rule
#: would delete the very product the shopkeeper was showing the camera. IoU only
#: fires when the proposal essentially IS the rejected object.
REJECT_IOU = 0.45


@dataclass(frozen=True)
class Proposal:
    """One region that MIGHT hold a product. Never a claim that it does."""

    x: int
    y: int
    w: int
    h: int
    #: "yolo" or "contour" — kept so a bench can attribute recall to a proposer.
    source: str
    #: Comparable only WITHIN a source. YOLO objectness and contour saliency are
    #: different scales and are never compared to each other.
    score: float

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    @property
    def area(self) -> int:
        return int(self.w) * int(self.h)

    def crop(self, bgr: "np.ndarray") -> "np.ndarray":
        return bgr[self.y:self.y + self.h, self.x:self.x + self.w]

    def to_json(self) -> dict[str, Any]:
        return {"box": [self.x, self.y, self.w, self.h],
                "source": self.source, "score": round(float(self.score), 4)}


class DetectorUnavailable(RuntimeError):
    """The optional ONNX model could not be loaded. Never fatal."""


# ---------------------------------------------------------------- the model --

_NET: dict[str, Any] = {"net": None, "tried": False, "why": ""}


def yolo_available() -> bool:
    """True if the optional model is loadable. Cheap after the first call."""
    return _load_net() is not None


def yolo_status() -> dict[str, Any]:
    """What a page may honestly say about the optional detector."""
    net = _load_net()
    return {
        "loaded": net is not None,
        "path": str(MODEL_PATH),
        "present": MODEL_PATH.is_file(),
        "why_not": _NET["why"] if net is None else "",
        # STATED, NOT IMPLIED. Anyone reading a page that says "YOLO" will
        # assume it is naming products. It is not, and the class head is never
        # even read.
        "used_for": "class-agnostic region proposals only; the class head is ignored",
    }


def _load_net():
    if _NET["tried"]:
        return _NET["net"]
    _NET["tried"] = True
    if not MODEL_PATH.is_file():
        _NET["why"] = f"no model file at {MODEL_PATH}"
        return None
    try:
        net = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
    except Exception as exc:                       # a bad file is not a crash
        _NET["why"] = f"{type(exc).__name__}: {exc}"
        return None
    _NET["net"] = net
    return net


def reset_model_cache() -> None:
    """Forget the load attempt. For tests that move the file about."""
    _NET["net"] = None
    _NET["tried"] = False
    _NET["why"] = ""


# ------------------------------------------------------------- the proposers --

def _letterbox(bgr: "np.ndarray", side: int) -> tuple["np.ndarray", float, int, int]:
    """Resize keeping aspect, pad to square. Returns the mapping back.

    Squashing to square instead would distort every box by the frame's aspect
    ratio — on a 1280x720 counter that is 1.78x horizontally, so a box drawn
    round a packet would be wrong by most of a packet.
    """
    h, w = bgr.shape[:2]
    scale = side / max(h, w)
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    small = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.full((side, side, 3), 114, np.uint8)   # YOLO's own pad grey
    top, left = (side - nh) // 2, (side - nw) // 2
    out[top:top + nh, left:left + nw] = small
    return out, scale, left, top


def propose_yolo(bgr: "np.ndarray") -> list[Proposal]:
    """Class-agnostic boxes from the optional ONNX model. [] when absent."""
    net = _load_net()
    if net is None:
        return []
    h, w = bgr.shape[:2]
    if h < 8 or w < 8:
        return []
    square, scale, padx, pady = _letterbox(bgr, YOLO_SIDE)
    blob = cv2.dnn.blobFromImage(square, 1 / 255.0, (YOLO_SIDE, YOLO_SIDE),
                                 swapRB=True, crop=False)
    net.setInput(blob)
    try:
        raw = net.forward()
    except Exception:                              # never take the till down
        return []
    pred = np.asarray(raw)
    if pred.ndim == 3:
        pred = pred[0]
    if pred.ndim != 2 or pred.shape[1] < 5:
        return []

    obj = pred[:, 4].astype(np.float64)
    keep = obj >= YOLO_OBJECTNESS_FLOOR
    if not keep.any():
        return []
    rows, scores = pred[keep], obj[keep]

    out: list[Proposal] = []
    for row, s in zip(rows, scores):
        cx, cy, bw, bh = (float(v) for v in row[:4])
        # Undo the letterbox: subtract the pad, then divide by the scale.
        x = (cx - bw / 2.0 - padx) / scale
        y = (cy - bh / 2.0 - pady) / scale
        bw, bh = bw / scale, bh / scale
        x, y = max(0.0, x), max(0.0, y)
        bw, bh = min(bw, w - x), min(bh, h - y)
        if bw < 2 or bh < 2:
            continue
        # THE SAME FILTERS THE CONTOUR PROPOSER APPLIES. They lived only in
        # propose_contours, so every YOLO box skipped them: a 6 px speck on an
        # otherwise empty counter came back as a 14x17 proposal, and an empty
        # counter is supposed to propose nothing at all. A filter that only one
        # of two proposers obeys is not a filter.
        frac = (bw * bh) / float(w * h)
        if frac < MIN_AREA_FRAC or frac > MAX_AREA_FRAC:
            continue
        if max(bw, bh) / max(1.0, min(bw, bh)) > MAX_ASPECT:
            continue
        out.append(Proposal(int(x), int(y), int(bw), int(bh), "yolo", float(s)))
    return out


@dataclass(frozen=True)
class Rejection:
    """A region the model is confident holds something a shop does not sell."""

    x: int
    y: int
    w: int
    h: int
    #: The COCO class name. Reported so a page can say WHICH condition it saw.
    label: str
    score: float

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    @property
    def area(self) -> int:
        return int(self.w) * int(self.h)

    def to_json(self) -> dict[str, Any]:
        return {"box": [self.x, self.y, self.w, self.h], "label": self.label,
                "score": round(float(self.score), 4)}


def yolo_rejections(bgr: "np.ndarray") -> list[Rejection]:
    """Regions that are confidently a person, an animal or room furniture.

    [] when the model is absent, which is why this can never be load-bearing:
    a checkout that never downloaded the weights behaves exactly as it did
    before. ABSENCE IS NEVER EVIDENCE — nothing is rejected for failing to be
    recognised, only for being positively recognised as something a shop does
    not sell.
    """
    net = _load_net()
    if net is None:
        return []
    h, w = bgr.shape[:2]
    if h < 8 or w < 8:
        return []
    square, scale, padx, pady = _letterbox(bgr, YOLO_SIDE)
    blob = cv2.dnn.blobFromImage(square, 1 / 255.0, (YOLO_SIDE, YOLO_SIDE),
                                 swapRB=True, crop=False)
    net.setInput(blob)
    try:
        raw = net.forward()
    except Exception:                              # never take the till down
        return []
    pred = np.asarray(raw)
    if pred.ndim == 3:
        pred = pred[0]
    if pred.ndim != 2 or pred.shape[1] < 6:
        return []

    obj = pred[:, 4].astype(np.float64)
    cls = pred[:, 5:].astype(np.float64)
    idx = cls.argmax(axis=1)
    conf = obj * cls.max(axis=1)

    out: list[Rejection] = []
    for row, c, k in zip(pred, conf, idx):
        if c < YOLO_REJECT_CONF:
            continue
        label = _NOT_A_FACING.get(int(k))
        if label is None:
            continue
        cx, cy, bw, bh = (float(v) for v in row[:4])
        x = (cx - bw / 2.0 - padx) / scale
        y = (cy - bh / 2.0 - pady) / scale
        bw, bh = bw / scale, bh / scale
        x, y = max(0.0, x), max(0.0, y)
        bw, bh = min(bw, w - x), min(bh, h - y)
        if bw < 2 or bh < 2:
            continue
        out.append(Rejection(int(x), int(y), int(bw), int(bh), label, float(c)))

    # Collapse the duplicates the raw grid emits, keeping the surest of each.
    out.sort(key=lambda r: r.score, reverse=True)
    kept: list[Rejection] = []
    for r in out:
        if any(_box_iou(r.box, k.box) > 0.45 for k in kept if k.label == r.label):
            continue
        kept.append(r)
    return kept


def _background_colour(lab: "np.ndarray") -> "np.ndarray":
    """The counter surface, as one Lab colour.

    Taken as the MODE of a coarse histogram rather than the mean. A mean is
    dragged towards whatever is on the counter, so a counter with four bright
    packets on it reports a background that is not the colour of any pixel and
    the subtraction then fires everywhere including on the counter itself.
    """
    q = (lab.reshape(-1, 3) // 16).astype(np.int32)     # 16^3 coarse bins
    key = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    counts = np.bincount(key)
    top = int(counts.argmax())
    centre = np.array([top // 256, (top // 16) % 16, top % 16], np.float64)
    return centre * 16.0 + 8.0


def propose_contours(bgr: "np.ndarray") -> list[Proposal]:
    """Regions that differ from the counter surface. No weights, no model.

    THIS IS THE WORKHORSE, not the fallback. A kirana packet is not a COCO
    class and never will be, but it is reliably a colourful thing on a broadly
    uniform surface — and that is a problem classical segmentation was solving
    decades before object detectors existed.
    """
    h, w = bgr.shape[:2]
    if h < 32 or w < 32:
        return []
    frame_area = float(h * w)

    small_w = 640 if w > 640 else w
    scale = small_w / float(w)
    small = cv2.resize(bgr, (small_w, max(1, int(round(h * scale)))),
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else bgr

    lab = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2LAB)
    bg = _background_colour(lab)
    dist = np.linalg.norm(lab.astype(np.float64) - bg, axis=2)

    # Otsu on the DISTANCE image, not on the frame. Thresholding luminance
    # directly loses a dark packet on a dark counter and a pale one on a pale
    # counter; distance-from-background has no such blind spot.
    d8 = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _t, mask = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Edges rescue a packet whose colour happens to match the counter: it still
    # has printing on it and a boundary, and neither survives being the same
    # colour as the surface.
    edges = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 60, 160)
    # ONCE, not twice. Dilating the edges twice thickens the boundary of every
    # object by ~4 px on each side, and two objects 20 px apart then meet in the
    # middle and become one region — measured: 3 of 3 products found at a 20 px
    # gap with one dilation, 0 of 3 with two. The second pass was buying a
    # little robustness on faint edges and paying for it with whole missing
    # lines on the bill.
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    mask = cv2.bitwise_or(mask, edges)

    # 7x7 ONCE, AND THE GAP LIMIT THAT COMES WITH IT.
    #
    # Two packets close together are one blob, and then one of them is never
    # offered to the recogniser and is simply MISSING from the bill — the worst
    # failure this counter has, because unlike an amber line nobody sees it.
    #
    # Swept kernel 3/5/7/9 against gap, three products in a row (1280 px wide):
    #
    #     gap        60   30   20   10    5    0
    #     3x3       3/3  3/3  3/3  0/3  0/3  0/3
    #     5x5       3/3  3/3  3/3  0/3  0/3  0/3
    #     7x7       3/3  3/3  3/3  0/3  0/3  0/3
    #     9x9       3/3  3/3  0/3  0/3  0/3  0/3
    #
    # So 9x9 costs a real 20 px case and buys nothing, and BELOW 20 px no
    # kernel helps — the floor is not the close at all, it is the dilated Canny
    # edges of two objects meeting. That is a limit of the method, not a
    # setting, and it is stated on the page rather than tuned at: leave about a
    # finger's width between packets. The distance-transform split below
    # rescues the cases where a neck survives; where the mask genuinely fuses,
    # nothing in the mask can separate them and this does not pretend otherwise.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    inv = 1.0 / scale if scale < 1.0 else 1.0

    # Collect the raw parts FIRST, in small-image coordinates, because the
    # grouping pass below has to read the pixels between them. Filtering here
    # would throw away the single line of small print that is the evidence its
    # neighbours are all one printed face.
    parts: list[tuple[int, int, int, int, float]] = []
    for c in cnts:
        parts.extend(_split_blob(mask, c))

    out: list[Proposal] = []
    for x, y, bw, bh, fill in _regroup_printing(parts, dist,
                                                lab.astype(np.float64), mask):
        X, Y = int(x * inv), int(y * inv)
        W, H = int(bw * inv), int(bh * inv)
        if W < 2 or H < 2:
            continue
        frac = (W * H) / frame_area
        if frac < MIN_AREA_FRAC or frac > MAX_AREA_FRAC:
            continue
        long_side, short_side = max(W, H), max(1, min(W, H))
        if long_side / short_side > MAX_ASPECT:
            continue
        if fill < 0.35:
            continue
        out.append(Proposal(X, Y, W, H, "contour", fill))
    return out


# ------------------------------------------- printing is not several objects --

#: How much further from the background the material BETWEEN two fragments has
#: to sit before they are judged to be printing on one packet rather than two
#: packets with counter between them. Expressed as a multiple of the frame's own
#: background level, so it carries across cameras, counters and exposures
#: instead of being a brightness that only holds for one shop.
#:
#: MEASURED, on the scene this was written for (two white medicine cartons held
#: up in a bedroom) and on the counter scenes that already worked:
#:
#:     pair                                            gap/background
#:     carton A  small print  <-> blue band                 1.37
#:     carton A  blue band    <-> brand lockup              1.37
#:     carton A  small print  <-> brand lockup              1.44
#:     ---------------------------------------------------------- 1.25 (here)
#:     carton A  <-> the wardrobe behind it            0.91, 0.96, 1.14
#:     carton A  <-> the person holding it                   1.11
#:     carton A  <-> carton B                                1.06
#:     two products on a counter, 20 px apart           1.05, 1.06
#:     two products on a counter, 40 px apart           1.01, 1.01
#:
#: The two populations do not overlap and the reason they do not is structural,
#: not lucky: the stuff between two fragments of one carton IS carton, and the
#: stuff between two packets on a shelf IS the shelf — which is the very colour
#: the background was measured as, so that ratio is 1.0 by construction.
FRAGMENT_GAP_RATIO = 1.25
#: Two fragments of one printed face are also LINED UP — the band runs the width
#: of the carton the lockup sits on. Requiring most of that width to be shared
#: stops a corner clipping a diagonal neighbour.
#:
#: MEASURED AGAINST THE LARGER FRAGMENT. The share used to be taken over the
#: SHORTER of the two boxes, and a fraction over the shorter box is 1.00 for
#: ANY box that spans the frame — which is exactly what a shelf edge, a price
#: rail or a counter lip is. So this gate, on its own, said a rail was lined up
#: with every packet standing on it:
#:
#:     scene A, 4 real products on a shelf with a price strip
#:     -> parts (0,668,1280,52) rail, (320,428,302,224) soap,
#:        (932,446,176,190) sachet
#:     rail <-> soap    over-smaller 1.00   over-larger 0.24
#:     rail <-> sachet  over-smaller 1.00   over-larger 0.14
#:
#: THIS GATE ALONE NEVER MERGED THEM, AND THE CHANGE FIXES NO OBSERVED MISS.
#: Measured on that exact scene: under BOTH measurements `_regroup_printing`
#: returns the three parts ungrouped. Alignment is only the first of three
#: gates, and the gap-is-background test below already rejects both rail/packet
#: pairs — the shelf between a packet and the rail under it IS the modal
#: colour. Reverting this line leaves all 29 detector and 64 shelf tests
#: passing and changes no proposal on 60 randomised shelf scenes.
#:
#: It is kept because that later gate rejects those pairs with almost no room:
#: the gap measured 7.87 against a bar of 8.00, a 1.6% margin that one darker
#: shelf or one brighter rail would close. Two independent reasons to keep a
#: rail off a packet is the point, not a repaired miss. Share over the LARGER
#: extent, which is what sets the bar below:
#:
#:     pair                                             share/larger
#:     carton A  small print  <-> blue band                  0.91
#:     carton B  small print  <-> blue band                  0.91
#:     carton A  blue band    <-> brand lockup               0.77
#:     carton B  blue band    <-> brand lockup               0.77
#:     ------------------------------------------------------ 0.60 (here)
#:     shelf rail             <-> lifebuoy soap              0.24
#:     shelf rail             <-> shampoo sachet             0.14
#:
#: The populations separate for a structural reason, not a lucky one: two
#: fragments of one printed face are both pieces of THAT FACE, so each spans
#: most of it; a rail spans the frame and a packet spans a fraction of it.
FRAGMENT_ALIGN = 0.60

#: How near, in Lab, the material between two fragments has to be to the blank
#: surface each of them is printed on before they count as printing on ONE face.
#:
#: MEASURED, as max(|gap - surround_a|, |gap - surround_b|):
#:
#:     two fragments of one carton              0.00, 0.00, 1.00, 2.00
#:     ---------------------------------------------------------- 10.0 (here)
#:     one carton's print vs the next carton's           26.02
#:
#: This is the test that the global ratio above cannot make, and vice versa. A
#: lit wall's distance from its own modal colour drifts with the light, so the
#: wall BETWEEN two separate cartons measured 1.31x background and passed the
#: ratio test on its own; it fails this one at 26. A counter passes this one
#: trivially — the gap and the surround are both counter — and fails the ratio
#: test at 1.0. Only a surface that is both not-background AND the same surface
#: the fragments sit on is a packet with printing on it.
FRAGMENT_SURFACE_TOL = 10.0


def _background_level(dist: "np.ndarray") -> float:
    """How far the background sits from its own modal colour: the noise floor.

    The lower half of the distance image is background by construction (the
    modal colour is the commonest one), so its median is what "no object here"
    measures on THIS frame — the number every gap below is compared against.
    """
    lo = dist[dist <= np.percentile(dist, 50)]
    return float(np.median(lo)) if lo.size else 0.0


def _gap_between(a: tuple, b: tuple) -> tuple[Optional[tuple], int, float, int]:
    """The rectangle strictly between two boxes, its axis, and how aligned.

    Returns (rect, separation, alignment, axis) where axis is 0 for a vertical
    stack and 1 for side by side. rect is None when the boxes overlap or only
    meet at a corner — there is then no material between them to read.

    THE AXIS IS RETURNED, NOT INFERRED. It was briefly derived by comparing the
    gap's height to the separation, which silently picks the wrong axis whenever
    those two numbers happen to be equal, and then compares a horizontal gap
    against the boxes' heights.

    THE ALIGNMENT IS A SHARE OF THE LARGER BOX, NOT THE SMALLER. Over the
    smaller box it is 1.00 for any box that spans the frame, so a shelf edge
    counted as lined up with every packet standing on it. That never cost a
    proposal on its own — the gap-is-background gate rejects those pairs
    anyway, and reverting this measurement changes no test and no scene. See
    FRAGMENT_ALIGN for what was actually measured and why it is still kept.
    """
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    ox0, ox1 = max(a[0], b[0]), min(ax2, bx2)      # shared span across x
    oy0, oy1 = max(a[1], b[1]), min(ay2, by2)      # shared span across y
    if ox1 > ox0 and oy1 <= oy0:                   # stacked, one above the other
        y0, y1 = min(ay2, by2), max(a[1], b[1])
        align = (ox1 - ox0) / float(max(1, a[2], b[2]))
        return (ox0, y0, ox1 - ox0, y1 - y0), y1 - y0, align, 0
    if oy1 > oy0 and ox1 <= ox0:                   # side by side
        x0, x1 = min(ax2, bx2), max(a[0], b[0])
        align = (oy1 - oy0) / float(max(1, a[3], b[3]))
        return (x0, oy0, x1 - x0, oy1 - oy0), x1 - x0, align, 1
    return None, 0, 0.0, -1


def _surface_around(lab: "np.ndarray", mask: "np.ndarray", box: tuple,
                    pad: int = 6) -> Optional["np.ndarray"]:
    """The colour of the blank surface a fragment is printed on, if any.

    A band just outside the fragment, with everything the mask calls ink
    excluded — so for a word on a carton this is the carton, and for a packet
    on a counter it is the counter.
    """
    x, y, w, h = box
    H, W = lab.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    sel = np.zeros((H, W), bool)
    sel[y0:y1, x0:x1] = True
    sel[y:y + h, x:x + w] = False
    sel &= (mask == 0)
    if int(sel.sum()) < 12:
        return None
    return np.median(lab[sel], axis=0)


def _regroup_printing(parts: Sequence[tuple], dist: "np.ndarray",
                      lab: "np.ndarray", mask: "np.ndarray"
                      ) -> list[tuple[int, int, int, int, float]]:
    """Fragments of ONE printed packet, put back together.

    THE BUG THIS EXISTS FOR, AND WHY IT IS NOT "MERGE HARDER". A white medicine
    carton photographed against a pale wall was returned as THREE objects: the
    brand lockup, the saturated colour band, and the paragraph of small print.
    The obvious reading is that the merge logic failed, and it is wrong — the
    merge logic never had anything to work with, because THE PARENT BOX WAS
    NEVER PROPOSED. Measured on the failing frame: the modal background is the
    lit wall, the carton's white body sits 15 from it on the normalised distance
    scale, and Otsu — a two-class split driven by the strongest contrast in the
    frame, which is the dark person against that bright wall at 195 — puts the
    cut at 98. So the carton's own body is classified as background and only its
    ink survives. Three islands of ink, no parent, nothing to suppress.
    `_swallows_several` and the containment pre-pass in `merge` are innocent;
    both regressions recorded in FAILURES.md came from over-eager merging and
    neither is repeated here.

    Nor is the fix to lower the threshold. Swept: the window that admits the
    carton without admitting the wall is 18.3-19.7 out of a 0-189 range, it is
    set by this one frame's exposure, and it buys nothing on any scene that
    already worked. A cut that narrow is a coincidence, not a setting.

    So the question is asked of the PIXELS BETWEEN the fragments instead, where
    there is a real margin: material that is measurably further from the
    background than the background is from itself is a surface — a carton — and
    the fragments on it are one thing. Two packets standing apart on a shelf
    have SHELF between them, which is the background colour by construction, so
    they are never grouped at any gap. The 20 px separation this module
    promises is therefore untouched, and it is asserted in the tests.
    """
    boxes = [tuple(int(v) for v in p[:4]) for p in parts]
    fills = [float(p[4]) for p in parts]
    n = len(boxes)
    if n < 2:
        return [tuple(b) + (f,) for b, f in zip(boxes, fills)]

    base = _background_level(dist)
    if base <= 0.0:
        return [tuple(b) + (f,) for b, f in zip(boxes, fills)]
    bar = base * FRAGMENT_GAP_RATIO

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            rect, sep, align, axis = _gap_between(a, b)
            if rect is None or rect[2] < 1 or rect[3] < 1:
                continue
            if align < FRAGMENT_ALIGN:
                continue
            # Fragments of one face are CLOSE relative to their own size, along
            # the axis that actually separates them. This is what stops a chain
            # running away across the room: the person and the wardrobe are
            # 410 px apart and 168 px wide, so they are never even considered,
            # whatever the wall between them measures.
            across = min(a[3], b[3]) if axis == 0 else min(a[2], b[2])
            if sep > max(4, across):
                continue
            gx, gy, gw, gh = rect
            patch = dist[gy:gy + gh, gx:gx + gw]
            if patch.size == 0:
                continue
            # TEST ONE, GLOBAL: is the stuff between them background?
            # Two packets on a shelf have shelf between them, and the shelf IS
            # the modal colour, so this ratio is 1.0 for them by construction.
            if float(np.median(patch)) < bar:
                continue
            # TEST TWO, LOCAL: is it the SAME SURFACE these fragments sit on?
            # Test one alone is fooled by a lit wall, whose distance from its own
            # modal colour drifts with the light — measured, the wall between two
            # separate cartons read 1.31x and would have merged them into one
            # box. Test two alone is fooled by a counter, where the gap and the
            # surround are both counter and so match perfectly. Each covers the
            # other's blind spot, and only both together mean "one printed face".
            lab_gap = np.median(lab[gy:gy + gh, gx:gx + gw].reshape(-1, 3), axis=0)
            sa = _surface_around(lab, mask, a)
            sb = _surface_around(lab, mask, b)
            if sa is None or sb is None:
                continue
            if (float(np.linalg.norm(lab_gap - sa)) > FRAGMENT_SURFACE_TOL
                    or float(np.linalg.norm(lab_gap - sb)) > FRAGMENT_SURFACE_TOL):
                continue
            parent[find(i)] = find(j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out: list[tuple[int, int, int, int, float]] = []
    for members in groups.values():
        if len(members) == 1:
            i = members[0]
            out.append(tuple(boxes[i]) + (fills[i],))
            continue
        xs = [boxes[i][0] for i in members]
        ys = [boxes[i][1] for i in members]
        x2 = max(boxes[i][0] + boxes[i][2] for i in members)
        y2 = max(boxes[i][1] + boxes[i][3] for i in members)
        x, y = min(xs), min(ys)
        w, h = x2 - x, y2 - y
        # The fill of a REASSEMBLED packet is not the sum of its ink. It is how
        # much of the box holds material that is not background — which is the
        # same measurement that justified grouping it, asked of the whole box.
        patch = dist[y:y + h, x:x + w]
        fill = float((patch >= bar).mean()) if patch.size else 0.0
        out.append((x, y, w, h, fill))
    return out


def _split_blob(mask: "np.ndarray", contour: "np.ndarray"
                ) -> list[tuple[int, int, int, int, float]]:
    """One blob in, one or more boxes out. Returns (x, y, w, h, fill).

    TWO PACKETS SITTING NEXT TO EACH OTHER ARE ONE BLOB. Measured: a shampoo
    sachet and a second object with a 30 px gap between them came back as a
    single 426 px box, so one of the two was never offered to the recogniser
    and would simply have been missing from the bill. A missing line is the
    worst failure this counter has — worse than an amber one, because nobody
    sees it.

    The gap was bridged by the morphological close, and weakening the close is
    not the fix: it is there because a printed packet has dark text on light
    card and fragments into a dozen pieces without it. Closing and then
    SPLITTING is the right order — fill the holes inside one object, then
    separate objects from each other.

    The split is a distance transform: every pixel scored by how far it is from
    the nearest background pixel, so the centre of each packet is a peak and
    the neck between two touching packets is a valley. Thresholding at 45% of
    the LOCAL maximum (local, because two packets in one blob may be different
    sizes and a global threshold erases the smaller one) leaves one seed per
    object, and a watershed grows those seeds back out to the real boundaries.
    """
    x, y, w, h = cv2.boundingRect(contour)
    area = float(cv2.contourArea(contour))
    whole = (x, y, w, h, area / float(max(1, w * h)))
    if w < 24 or h < 24:
        return [whole]

    local = np.zeros((h, w), np.uint8)
    cv2.drawContours(local, [contour], -1, 255, cv2.FILLED, offset=(-x, -y))

    dist = cv2.distanceTransform(local, cv2.DIST_L2, 5)
    peak = float(dist.max())
    if peak < 6.0:                       # too thin to hold two of anything
        return [whole]
    _t, seeds = cv2.threshold(dist, 0.45 * peak, 255, cv2.THRESH_BINARY)
    seeds = seeds.astype(np.uint8)
    n, labels = cv2.connectedComponents(seeds)
    if n <= 2:                           # background + one seed = one object
        return [whole]

    markers = labels.astype(np.int32) + 1
    markers[local == 0] = 1              # everything outside the blob is background
    cv2.watershed(cv2.cvtColor(local, cv2.COLOR_GRAY2BGR), markers)

    parts: list[tuple[int, int, int, int, float]] = []
    for lab in range(2, n + 1):
        piece = (markers == lab).astype(np.uint8)
        px = int(piece.sum())
        if px < 64:
            continue
        cs, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs:
            continue
        big = max(cs, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(big)
        parts.append((x + bx, y + by, bw, bh,
                      float(cv2.contourArea(big)) / float(max(1, bw * bh))))
    # A split that produced nothing usable is not a split. Fall back to the
    # whole blob rather than losing the object entirely.
    return parts or [whole]


# ------------------------------------------------------------------- merging --

def _box_iou(a: Sequence[int], b: Sequence[int]) -> float:
    """IoU of two [x, y, w, h] boxes."""
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    return inter / float(a[2] * a[3] + b[2] * b[3] - inter)


def _iou(a: Proposal, b: Proposal) -> float:
    return _box_iou(a.box, b.box)


def _contains(big: Proposal, small: Proposal, *, frac: float = 0.80) -> bool:
    """Is `small` mostly inside `big`? Intersection over the SMALLER box."""
    ix = max(0, min(big.x + big.w, small.x + small.w) - max(big.x, small.x))
    iy = max(0, min(big.y + big.h, small.y + small.h) - max(big.y, small.y))
    inter = ix * iy
    return inter > 0 and (inter / float(max(1, small.area))) >= frac


def _swallows_several(p: Proposal, others: Sequence[Proposal]) -> bool:
    """True if `p` is one box drawn around SEVERAL separate objects."""
    inside = [o for o in others if o is not p and _contains(p, o)]
    for i, a in enumerate(inside):
        for b in inside[i + 1:]:
            if _iou(a, b) < 0.10:        # two things that are not each other
                return True
    return False


def merge(proposals: Sequence[Proposal], *, iou: float = NMS_IOU,
          limit: int = MAX_PROPOSALS) -> list[Proposal]:
    """Non-maximum suppression across BOTH proposers.

    RANKED BY AREA DESCENDING, WHICH WAS A BUG, AND THE FIX IS THE FIRST PASS.
    Preferring the larger box is right within one object — the whole packet
    beats a corner of its label. It is exactly wrong across objects: measured,
    a single sloppy YOLO box drawn around all three products on the counter
    suppressed the three precise contour boxes underneath it and the frame went
    from 3 items found to 0. Ranking the other way just inverts the failure: a
    fragment of printing would then suppress the packet it sits on.

    Neither ordering can fix it, because the question is not which box is
    bigger. It is whether a box is a proposal for ONE thing. So a box that
    mostly contains two proposals that are not each other is a merge of several
    objects, and it is dropped before ranking begins — after which largest-first
    is the right rule again, among boxes that each describe one object.

    Scores are never compared across proposers: YOLO objectness and contour
    fill are different scales, and ranking by them would silently prefer
    whichever happened to have the more generous numeric range.
    """
    candidates = [p for p in proposals if not _swallows_several(p, proposals)]
    # Every proposal swallowed something — a counter photographed so close that
    # one object fills it. Keep the originals rather than return nothing.
    if not candidates:
        candidates = list(proposals)

    ordered = sorted(candidates, key=lambda p: p.area, reverse=True)
    kept: list[Proposal] = []
    for p in ordered:
        # IoU ALONE IS NOT ENOUGH, and the gap it leaves bills twice. A
        # fragment of a packet's label — 120x120 inside a 200x200 box — scores
        # IoU 0.36 against the box that contains it, under the 0.40 threshold,
        # so both survived and the same packet produced two lines. Containment
        # is the right question for a box that sits INSIDE another: it is the
        # same object seen twice, at any overlap ratio.
        if any(_iou(p, q) > iou or _contains(q, p) for q in kept):
            continue
        kept.append(p)
        if len(kept) >= limit:
            break
    return kept


def detect(bgr: "np.ndarray", *, use_yolo: bool = True) -> list[Proposal]:
    """Every region on this counter that might hold a product.

    Both proposers always run when the model is available: they fail on
    different things. YOLO misses packets it was never trained on; the contour
    proposer misses an object the same colour as the counter that is also out
    of focus. Running one and falling back to the other only when it returns
    nothing would hide exactly the cases where they disagree.
    """
    props = list(propose_contours(bgr))
    if use_yolo:
        props.extend(propose_yolo(bgr))
        vetoes = yolo_rejections(bgr)
        if vetoes:
            props = [p for p in props
                     if not any(_box_iou(p.box, v.box) >= REJECT_IOU for v in vetoes)]
    return merge(props)


def describe() -> dict[str, Any]:
    """What this counter can honestly say about how it finds things."""
    return {
        "module": MODULE,
        "proposers": ["contour", "yolo"] if yolo_available() else ["contour"],
        "yolo": yolo_status(),
        "gates": {
            "yolo_objectness_floor": YOLO_OBJECTNESS_FLOOR,
            "min_area_frac": MIN_AREA_FRAC,
            "max_area_frac": MAX_AREA_FRAC,
            "max_aspect": MAX_ASPECT,
            "nms_iou": NMS_IOU,
            "max_proposals": MAX_PROPOSALS,
            "yolo_reject_conf": YOLO_REJECT_CONF,
            "reject_iou": REJECT_IOU,
            "fragment_gap_ratio": FRAGMENT_GAP_RATIO,
            "fragment_surface_tol": FRAGMENT_SURFACE_TOL,
        },
        # STATED, because "we use the class head" and "we use it to name your
        # products" are one word apart and only one of them is true.
        "rejects": {
            "classes": sorted(set(_NOT_A_FACING.values())),
            "used_for": ("vetoing a region that is confidently something a shop "
                         "does not sell; never for naming what a product is"),
            "note": ("A class a packet can be mistaken for may not veto one, so "
                     "no box-shaped class is on this list — a printed carton "
                     "scores as a laptop, measured."),
        },
        "settles_money": False,
        "identifies_products": False,
        "note": ("Proposes regions only. Which product a region holds is decided "
                 "by the shop's own taught vectors, not here."),
    }


__all__ = [
    "Proposal", "DetectorUnavailable", "detect", "describe", "merge",
    "propose_yolo", "propose_contours", "yolo_available", "yolo_status",
    "reset_model_cache", "MODEL_PATH",
]
