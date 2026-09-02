"""The frontier the gates stand on, as executable numbers.

DEFAULT_PHI=0.55 and PHI_APPEARANCE_ONLY=0.60 are not taste — they sit inside a
measured gap: on the committed fixtures the weakest same-product pair scores
well above them and the strongest different-product pair well below. If either
side of that gap moves — a new OpenCV resamples differently, a weight file
changes, someone edits the preprocessing — these numbers move first, and the
gates stop being justified. That is exactly when this file should go red.

The fixtures are the REAL data the decision was made on: the six stored
references and the two captured frames of the jar whose failure started all of
this (taught in daylight, shown in warm evening light, cosine 0.7421 against
the retired embedder's 0.92 gate).
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah import embedder2  # noqa: E402
from gawaah.identity import DEFAULT_PHI, PHI_APPEARANCE_ONLY  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures_embed"

REFS = ["ref_ponds", "ref_lifebuoy_soap", "ref_parle_g_biscuit",
        "ref_shampoo_sachet", "ref_10C", "ref_maxfresh"]


@pytest.fixture(scope="module")
def vecs() -> dict[str, np.ndarray]:
    out = {}
    for name in REFS + ["live_ponds_warm", "live_ponds_day"]:
        img = cv2.imread(str(FIX / f"{name}.png"))
        assert img is not None, f"fixture {name} missing"
        out[name] = np.asarray(embedder2.embed(img), dtype=np.float64)
    return out


def cos(a, b) -> float:
    return float(np.dot(a, b))


def test_the_once_failing_frame_clears_the_stricter_gate(vecs):
    """The case that started it: 0.7421 vs 0.92 under the retired embedder."""
    c = cos(vecs["live_ponds_warm"], vecs["ref_ponds"])
    assert c >= 0.80, f"warm-evening jar fell to {c:.4f}"
    assert c > PHI_APPEARANCE_ONLY + 0.15


def test_the_daylight_frame_clears_it_too(vecs):
    c = cos(vecs["live_ponds_day"], vecs["ref_ponds"])
    assert c > PHI_APPEARANCE_ONLY, f"daylight jar at {c:.4f}"


def test_no_wrong_product_comes_near_either_gate(vecs):
    """The other side of the frontier. cross_max measured 0.4379; the assert
    leaves room for numerical drift without letting it reach the 0.55 gate."""
    worst, pair = -1.0, None
    for a, b in itertools.combinations(REFS, 2):
        c = cos(vecs[a], vecs[b])
        if c > worst:
            worst, pair = c, (a, b)
    assert worst < 0.50, f"cross-product cosine {worst:.4f} between {pair}"
    assert worst < DEFAULT_PHI


def test_the_live_frames_reject_every_wrong_product(vecs):
    for probe in ("live_ponds_warm", "live_ponds_day"):
        for ref in REFS:
            if ref == "ref_ponds":
                continue
            c = cos(vecs[probe], vecs[ref])
            assert c < 0.50, f"{probe} vs {ref} at {c:.4f}"


def test_open_set_a_probe_with_no_true_product_matches_nothing(vecs):
    """Remove ponds from the gallery: the jar must clear NO gate against what
    remains. This is where margin-style rules die, and where an absolute gate
    on a separated embedding survives — the property the whole design bought.
    """
    best = max(cos(vecs["live_ponds_warm"], vecs[r])
               for r in REFS if r != "ref_ponds")
    assert best < DEFAULT_PHI, f"open-set impostor at {best:.4f}"
    assert best < PHI_APPEARANCE_ONLY


def test_the_gates_sit_inside_the_measured_gap():
    """The relationship itself, so nobody narrows it by editing one constant."""
    assert DEFAULT_PHI == 0.55
    assert PHI_APPEARANCE_ONLY == 0.60
    assert DEFAULT_PHI < PHI_APPEARANCE_ONLY
