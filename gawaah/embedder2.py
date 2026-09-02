"""NAZAR-2 — the second embedder: SqueezeNet features, nuisance-whitened.

WHY THE HANDCRAFTED ONE WAS RETIRED FROM THE LIVE PATH. `embedder.py` ranks
almost perfectly and scores timidly: the same PONDS jar, taught in daylight and
shown in warm evening light, scored 0.7421 against a 0.92 gate while the best
WRONG product scored 0.34 — verdict I DO NOT KNOW, margin +0.40. Measured on a
56-image bench (six real products, real captured frames, deterministic
lighting/rotation/blur variants), its worst same-product cosine sits BELOW its
best different-product cosine (gap −0.2071). Overlapping distributions have no
correct threshold; no gate, and no margin rule over the same scores, can fix
that. Five independent candidates were built and benched on identical pixels;
this one was the only survivor of adversarial verification:

    worst_gap   −0.2071 → +0.1901     (same_min 0.6281 vs cross_max 0.4379)
    p5_gap       0.1805 → +0.3957
    the failing jar   0.7421 → 0.8199 (margin over best wrong: +0.4867)
    19.7 ms per embed · 512-d · byte-deterministic

ON INVARIANT 3, PLAINLY. "No model weights in the browser" continues to govern
the PAGE, which still ships nothing and calls no third-party inference. This
module puts 4.96 MB of Apache-2.0 SqueezeNet weights on the SERVER, beside the
YOLO proposer that already lives there. The claim the product retires is
"recognition is handcrafted maths end to end"; the claim it keeps is that
nothing about a shop leaves the machine and nothing runs on the customer's
device.

THE PIPELINE, each step measured before it stayed:
  1. BAND-LIMIT to 96 px on the long side. Stored references are 96 px and live
     crops arrive at ~300; without this the two sit on different points of the
     network's frequency response and the same jar reads ~0.10 lower.
  2. Gray-world white balance — the warm/cool illuminant shift, cancelled
     before the network sees it. This alone was most of the original failure.
  3. Resize 224 (the graph is shape-locked), Gaussian pre-blur σ2.5 so sharp
     and soft captures of one packet meet in the middle.
  4. Forward to the fire9 concat, global-average-pool → 512-d, signed sqrt.
  5. WHITENING v = W(f − μ), constants from `models/squeeze_whiten.npz`,
     fitted by `tools/fit_whiten.py` on a seeded SYNTHETIC corpus — no bench or
     shop pixel read. Directions that lighting/rotation/blur move are divided
     down; directions only identity moves keep their length. Without this,
     post-ReLU CNN features share a common direction that floors every
     cross-product cosine at ~0.87.
  6. TTA over rotations {0, ±20, ±40}°, averaged, renormalised.

THE VERIFIED LIMIT, stated not hidden: an untaught same-brand sibling variant
(identical print, recoloured band) scores ~0.83 and will be named as its taught
sibling — no gate between the genuine floor (0.63) and 0.83 exists. The shipped
baseline failed the identical case HARDER (0.970, with a ranking inversion).
The mitigations are the product's own: teach the sibling and the pair goes
permanently amber via the margin rule; a size difference is caught by the
footprint gate.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

NAME = "squeezenet1.1 fire9-gap-ssr, WCCN-whitened, rot-TTA"
EMBED_DIM = 512

_MODELS = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = Path(os.environ.get("GAWAAH_EMBED2_ONNX",
                                 str(_MODELS / "squeezenet1.1-7.onnx")))
WHITEN_PATH = Path(os.environ.get("GAWAAH_EMBED2_WHITEN",
                                  str(_MODELS / "squeeze_whiten.npz")))

#: The fire9 concat — 512 channels at 13×13. NOT the network output.
_LAYER = "onnx_node!squeezenet0_concat7"

_SIDE = 224          # locked by the ONNX graph; any other side raises
_LOWRES = 96         # band-limit, px on the crop's long side
_PREBLUR = 2.5       # gaussian sigma at 224
_TTA_ROT = (0.0, 20.0, -20.0, 40.0, -40.0)

_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


class Embedder2Error(RuntimeError):
    """The model or its whitening constants could not be loaded."""


#: Loaded on first use, not at import. Half the test suite imports modules that
#: transitively reach this file and never embeds anything; paying a model load
#: for that would slow every test and every cold start that only wanted money.
_STATE: dict[str, Any] = {"net": None, "mu": None, "W": None}


def _load() -> None:
    if _STATE["net"] is not None:
        return
    if not MODEL_PATH.is_file():
        raise Embedder2Error(
            f"no model at {MODEL_PATH} — recognition by appearance needs "
            f"models/squeezenet1.1-7.onnx (4.96 MB, Apache-2.0, onnx/models "
            f"zoo). Codes still price; appearance cannot.")
    if not WHITEN_PATH.is_file():
        raise Embedder2Error(
            f"no whitening constants at {WHITEN_PATH} — regenerate them "
            f"deterministically with tools/fit_whiten.py.")
    # ENGINE_CLASSIC IS LOAD-BEARING. OpenCV 5's default engine silently
    # ignores forward-to-intermediate-layer and returns the 1000-d classifier
    # logits under the SAME layer name — verified, not theoretical. A test
    # asserts the forward yields 512 channels so "modernising" this flag fails
    # loudly instead of quietly replacing the descriptor.
    _STATE["net"] = cv2.dnn.readNetFromONNX(str(MODEL_PATH),
                                            cv2.dnn.ENGINE_CLASSIC)
    wh = np.load(str(WHITEN_PATH))
    _STATE["mu"], _STATE["W"] = wh["mu"], wh["W"]


def reset_cache() -> None:
    """Forget the loaded model. For tests that move files about."""
    _STATE["net"] = None
    _STATE["mu"] = None
    _STATE["W"] = None


def _grayworld(bgr: np.ndarray) -> np.ndarray:
    f = bgr.astype(np.float32)
    means = f.reshape(-1, 3).mean(axis=0)
    g = float(means.mean())
    return np.clip(f * (g / np.maximum(means, 1e-6))[None, None, :], 0.0, 255.0)


def _rot(img: np.ndarray, deg: float) -> np.ndarray:
    if deg == 0.0:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), deg, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _forward(bgr_f32: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(bgr_f32, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (_SIDE, _SIDE), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.GaussianBlur(rgb, (0, 0), _PREBLUR)
    blob = ((rgb / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[None]
    _STATE["net"].setInput(blob)
    fmap = _STATE["net"].forward(_LAYER)[0]            # (512, 13, 13)
    v = fmap.reshape(fmap.shape[0], -1).mean(axis=1)   # GAP
    v = np.sign(v) * np.sqrt(np.abs(v))                # signed sqrt
    v = _STATE["W"] @ (v.astype(np.float64) - _STATE["mu"])
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def embed(bgr: np.ndarray) -> np.ndarray:
    """One crop → one unit-length 512-d vector. Deterministic."""
    _load()
    if bgr is None or getattr(bgr, "size", 0) == 0:
        raise Embedder2Error("an empty image has no appearance")
    if bgr.ndim == 2:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    h, w = bgr.shape[:2]
    m = max(h, w)
    if m > _LOWRES:
        f = _LOWRES / m
        bgr = cv2.resize(bgr, (max(2, round(w * f)), max(2, round(h * f))),
                         interpolation=cv2.INTER_AREA)
    img = _grayworld(bgr)
    acc = None
    for deg in _TTA_ROT:
        v = _forward(_rot(img, deg))
        acc = v if acc is None else acc + v
    n = float(np.linalg.norm(acc))
    return (acc / n if n > 0 else acc).astype(np.float32)


__all__ = ["NAME", "EMBED_DIM", "MODEL_PATH", "WHITEN_PATH",
           "Embedder2Error", "embed", "reset_cache"]
