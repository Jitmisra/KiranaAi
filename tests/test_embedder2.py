"""The second embedder: the contract, the traps, and the measured frontier.

Two files of tests were specified when the integration was judged; this is the
first — the mechanical contract. `test_embedder2_separation.py` holds the
frontier the gates stand on.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gawaah import embedder2  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures_embed"

#: Pinned artifact digests. A silently swapped weight file changes every
#: stored vector's meaning without changing a line of code — the one class of
#: drift a reviewer cannot see in a diff.
MODEL_SHA = "1eeff551a67ae8d565ca33b572fc4b66e3ef357b0eb2863bb9ff47a918cc4088"
WHITEN_SHA = "c55c08d2a98194c73c360c0648ce6f2850988994c6ab12fb0999ef5964aac44f"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_weight_files_are_the_verified_ones():
    assert _sha(embedder2.MODEL_PATH) == MODEL_SHA
    assert _sha(embedder2.WHITEN_PATH) == WHITEN_SHA


def test_dim_unit_norm_and_finite():
    v = embedder2.embed(cv2.imread(str(FIX / "ref_ponds.png")))
    assert v.shape == (embedder2.EMBED_DIM,) == (512,)
    assert np.isfinite(v).all()
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_grey_input_is_a_first_class_case():
    img = cv2.imread(str(FIX / "ref_ponds.png"), cv2.IMREAD_GRAYSCALE)
    v = embedder2.embed(img)
    assert v.shape == (512,)
    assert np.isfinite(v).all()


def test_byte_deterministic_across_processes():
    """Same image → the same bytes, in a FRESH interpreter. A vector that
    drifts between runs makes every stored gallery slowly wrong."""
    code = (
        "import sys, cv2, hashlib;"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r});"
        "from gawaah import embedder2;"
        f"v = embedder2.embed(cv2.imread({str(FIX / 'ref_ponds.png')!r}));"
        "print(hashlib.sha256(v.tobytes()).hexdigest())"
    )
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, check=True).stdout.strip()
            for _ in range(2)}
    here = hashlib.sha256(
        embedder2.embed(cv2.imread(str(FIX / "ref_ponds.png"))).tobytes()
    ).hexdigest()
    runs.add(here)
    assert len(runs) == 1, f"vectors drifted: {runs}"


def test_the_engine_classic_trap_fails_loudly():
    """OpenCV 5's default dnn engine silently ignores forward-to-intermediate
    and returns the 1000-d classifier logits UNDER THE SAME LAYER NAME. This
    pins that the fire9 forward really yields 512 channels, so 'modernising'
    the engine flag breaks a test instead of quietly replacing the descriptor.
    """
    embedder2._load()
    img = np.full((224, 224, 3), 128, np.float32)
    out = embedder2._forward(img)
    assert out.shape[0] == 512, (
        f"the fire9 forward returned {out.shape[0]} values — if this is 1000, "
        f"the dnn engine ignored the intermediate layer")


def test_an_empty_image_is_refused_by_name():
    with pytest.raises(embedder2.Embedder2Error):
        embedder2.embed(np.zeros((0, 0, 3), np.uint8))


def test_a_missing_model_says_what_would_fix_it(monkeypatch, tmp_path):
    monkeypatch.setattr(embedder2, "MODEL_PATH", tmp_path / "gone.onnx")
    embedder2.reset_cache()
    try:
        with pytest.raises(embedder2.Embedder2Error) as e:
            embedder2.embed(np.full((64, 64, 3), 128, np.uint8))
        assert "models/squeezenet1.1-7.onnx" in str(e.value)
        assert "Codes still price" in str(e.value)
    finally:
        embedder2.reset_cache()


def test_it_keeps_up_with_the_scan_loop():
    """~20 ms measured; the budget is 60 next to a ~35 ms YOLO pass inside a
    240 ms poll. Soft bound so a slow CI machine does not flake it."""
    img = cv2.imread(str(FIX / "live_ponds_warm.png"))
    big = cv2.resize(img, (500, 500))
    embedder2.embed(big)                                   # warm the model
    t0 = time.perf_counter()
    embedder2.embed(big)
    assert (time.perf_counter() - t0) * 1000 < 120.0
