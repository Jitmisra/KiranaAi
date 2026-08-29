"""tools/upload_app.py — the drop-an-image-in path.

The point of this tool is that a person with no camera, no printed mat and no
phone can still see the real pipeline run and CHECK it. So these tests check the
two things that makes true:

  1. The sample measures objects of known size to within a stated tolerance.
     If that drifts, the tool is showing numbers nobody should trust.
  2. Everything it cannot measure is refused BY NAME, and no input of any shape
     produces a 500. A crash is the one answer that teaches the user nothing.
"""
from __future__ import annotations

import base64
import os
import random
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gawaah.takhti import (  # noqa: E402
    BUF_H,
    BUF_W,
    MARKER_IDS,
    PlaneEngine,
    render_takhti,
)
from tools import upload_app  # noqa: E402
from tools.upload_app import (  # noqa: E402
    R_DEGENERATE,
    R_EMPTY_BODY,
    R_NOT_AN_IMAGE,
    R_TOO_LARGE,
    R_UNSUPPORTED,
    SAMPLE_TRUTH,
    analyse,
    app,
    apply_orientation,
    compare_to_truth,
    diagnose_lock,
    exif_orientation,
    run_sample,
    sample_scene,
    synthesised_reference,
)

# The sample is synthetic and noiseless apart from a 4-grey-level sensor model,
# so this is a real bar, not a rubber one: measured error runs about 0.5 mm and
# the gate is 2 mm. A regression that doubled the error would still fail it.
TOL_MM = 2.0


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_leftover_reference():
    """Each test starts with no operator-supplied reference."""
    upload_app._REFERENCE["buffer"] = None
    upload_app._REFERENCE["at"] = None
    yield
    upload_app._REFERENCE["buffer"] = None
    upload_app._REFERENCE["at"] = None


# ---------------------------------------------------------------- fixtures

def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _mat_photo() -> np.ndarray:
    return cv2.cvtColor(render_takhti(4.0), cv2.COLOR_GRAY2BGR)


def _exif_app1(orientation: int, big_endian: bool = False) -> bytes:
    end = ">" if big_endian else "<"
    tiff = (b"MM" if big_endian else b"II") + struct.pack(end + "HI", 42, 8)
    tiff += struct.pack(end + "H", 1)
    tiff += struct.pack(end + "HHI", 0x0112, 3, 1)
    tiff += struct.pack(end + "HH", orientation, 0)
    tiff += struct.pack(end + "I", 0)
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _jpeg_with_orientation(img: np.ndarray, orientation: int,
                           big_endian: bool = False) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    raw = buf.tobytes()
    return raw[:2] + _exif_app1(orientation, big_endian) + raw[2:]


# ----------------------------------------------------- 1. the sample is true

def test_sample_locks_the_mat(client: TestClient) -> None:
    r = client.get("/sample")
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is True
    assert body["reason"] == "locked"
    assert body["diagnosis"]["markers_found"] == 4
    assert sorted(body["ids_found"]) == sorted(MARKER_IDS)


def test_sample_measures_every_object_within_tolerance(client: TestClient) -> None:
    body = client.get("/sample").json()
    acc = body["accuracy"]
    assert acc["matched_count"] == len(SAMPLE_TRUTH) == acc["truth_count"]
    for row in acc["rows"]:
        assert row["matched"], f"{row['name']} was not measured at all"
        assert row["err_long_mm"] <= TOL_MM, row
        assert row["err_short_mm"] <= TOL_MM, row
        assert row["err_centre_mm"] <= TOL_MM, row
    assert acc["worst_edge_err_mm"] <= TOL_MM


def test_sample_finds_exactly_the_objects_that_are_there(client: TestClient) -> None:
    body = client.get("/sample").json()
    assert len(body["items"]) == len(SAMPLE_TRUTH)
    assert body["refusals"] == []
    assert body["accuracy"]["extra_items"] == []


def test_sample_measurements_are_millimetres_not_pixels(client: TestClient) -> None:
    """A pixel/mm mix-up is the failure this whole tool exists to make visible.

    PX_PER_MM is ~2.83, so a buffer-pixel answer would be ~2.8x the truth and
    would sail past a loose 'roughly right' check. Pin the magnitude.
    """
    body = client.get("/sample").json()
    longs = sorted(it["long_edge_mm"] for it in body["items"])
    assert longs == pytest.approx(sorted(max(w, h) for _, w, h, _ in SAMPLE_TRUTH),
                                  abs=TOL_MM)


def test_sample_is_labelled_simulated(client: TestClient) -> None:
    """INVARIANT 7: anything simulated is visibly labelled as simulated."""
    body = client.get("/sample").json()
    assert body["simulated"] is True
    assert "SIMULATED" in body["simulated_note"]
    # ...and burned into the pixels, not only into the JSON.
    for key in ("input_png", "overlay_png"):
        img = cv2.imdecode(np.frombuffer(base64.b64decode(body[key]), np.uint8),
                           cv2.IMREAD_COLOR)
        assert img is not None
        assert not np.array_equal(img, np.zeros_like(img))
    page = client.get("/").text
    assert "SIMULATED" in page


def test_sample_is_deterministic_and_seed_changes_nothing_material(
        client: TestClient) -> None:
    a = client.get("/sample?seed=7").json()
    b = client.get("/sample?seed=7").json()
    assert a["accuracy"]["rows"] == b["accuracy"]["rows"]
    c = client.get("/sample?seed=99").json()
    assert c["locked"] is True
    assert c["accuracy"]["worst_edge_err_mm"] <= TOL_MM


@pytest.mark.parametrize("seed", [0, -5, 1, 2 ** 33])
def test_sample_survives_any_seed(client: TestClient, seed: int) -> None:
    r = client.get(f"/sample?seed={seed}")
    assert r.status_code == 200
    assert r.json()["locked"] is True


def test_sample_with_synthesised_reference_still_measures_the_truth(
        client: TestClient) -> None:
    """The path a real single-photo upload takes, which has no empty frame."""
    body = client.get("/sample?reference=synthetic").json()
    assert body["locked"] is True
    assert body["reference_source"] == "synthesised_from_printed_design"
    acc = body["accuracy"]
    assert acc["matched_count"] == len(SAMPLE_TRUTH)
    assert acc["worst_edge_err_mm"] <= TOL_MM


def test_synthesised_reference_is_honest_about_being_synthesised(
        client: TestClient) -> None:
    body = client.get("/sample?reference=synthetic").json()
    assert "SYNTHESISED" in body["reference_note"]
    body2 = client.get("/sample").json()
    assert body2["reference_source"] == "empty_mat_photo_supplied"


def test_naive_resized_reference_would_have_lied() -> None:
    """Why synthesised_reference does the inv(H)/H round trip.

    A plain resize of the printed design misaligns the marker edges by a
    sub-pixel amount, and a black/white edge misaligned by a sub-pixel is a
    200-grey-level difference -- so the mat's own printed corners get reported
    as objects lying on it. This test is the receipt for that choice: if someone
    'simplifies' synthesised_reference back to a resize, it fails.
    """
    loaded, _empty = sample_scene(7)
    eng = PlaneEngine()
    lock = eng.detect(loaded)
    assert lock.locked
    rect = eng.rectify(loaded, lock.H)

    naive = cv2.resize(render_takhti(4.0), (BUF_W, BUF_H),
                       interpolation=cv2.INTER_AREA)
    good = synthesised_reference(lock.H, loaded.shape)

    def count(ref):
        from gawaah.placement import PlacementDetector
        det = PlacementDetector(ref)
        for _ in range(6):
            ps = det.update(rect)
        return len(ps)

    assert count(naive) > count(good), (
        "the round-trip reference must produce fewer phantom blobs than a "
        "naive resize; if it no longer does, the round trip is dead weight")
    assert count(good) <= len(SAMPLE_TRUTH) + 1


# ------------------------------------------------- 2. refusals keep a name

def test_blank_image_refuses_with_a_named_reason(client: TestClient) -> None:
    blank = _png(np.full((900, 700, 3), 255, np.uint8))
    r = client.post("/analyse", content=blank)
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is False
    assert body["reason"] == "no markers detected"
    assert body["items"] == []
    assert body["diagnosis"]["markers_found"] == 0
    assert body["diagnosis"]["fix"], "a refusal with no way out is not a message"


@pytest.mark.parametrize("fill", [0, 128, 255])
def test_featureless_image_of_any_brightness_refuses(client: TestClient,
                                                     fill: int) -> None:
    img = _png(np.full((600, 480, 3), fill, np.uint8))
    body = client.post("/analyse", content=img).json()
    assert body["locked"] is False
    assert body["diagnosis"]["markers_found"] == 0
    assert body["items"] == []


def test_non_image_body_is_refused(client: TestClient) -> None:
    r = client.post("/analyse", content=b"this is prose, not a photograph.\n" * 40)
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == R_NOT_AN_IMAGE
    assert body["detail"]


def test_empty_body_is_refused_by_its_own_name(client: TestClient) -> None:
    r = client.post("/analyse", content=b"")
    assert r.status_code == 400
    assert r.json()["reason"] == R_EMPTY_BODY


def test_heic_is_refused_by_name_and_told_how_to_fix_it(client: TestClient) -> None:
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 256
    body = client.post("/analyse", content=heic).json()
    assert body["reason"] == R_UNSUPPORTED
    assert "HEIC" in body["detail"]


def test_oversize_body_is_refused_before_decoding(client: TestClient) -> None:
    r = client.post("/analyse",
                    content=b"\x00" * (upload_app.MAX_UPLOAD_BYTES + 1024))
    assert r.status_code == 400
    assert r.json()["reason"] == R_TOO_LARGE


def test_tiny_image_is_refused_as_degenerate(client: TestClient) -> None:
    body = client.post("/analyse", content=_png(np.zeros((4, 4, 3), np.uint8))).json()
    assert body["reason"] == R_DEGENERATE


def test_missing_markers_are_named_by_corner(client: TestClient) -> None:
    """The most common real failure, and the message is the product."""
    covered = _mat_photo()
    cv2.circle(covered, (int(270 * 4), int(27 * 4)), 90, (60, 50, 45), -1)
    body = client.post("/analyse", content=_png(covered)).json()
    assert body["locked"] is False
    d = body["diagnosis"]
    assert d["markers_found"] == 3
    assert d["ids_missing"] == [1]
    assert d["corners_missing"] == ["top-right"]
    assert "top-right" in d["headline"]
    assert "3 of 4" in d["headline"]
    assert any("corner" in f for f in d["fix"])


def test_cropped_mat_reports_how_many_markers_were_found(client: TestClient) -> None:
    cropped = _mat_photo()[: int(4 * 420 * 0.6), :]
    body = client.post("/analyse", content=_png(cropped)).json()
    assert body["locked"] is False
    d = body["diagnosis"]
    assert d["markers_found"] == 2
    assert set(d["ids_missing"]) == {2, 3}
    assert set(d["corners_missing"]) == {"bottom-right", "bottom-left"}


def test_no_lock_never_reports_items(client: TestClient) -> None:
    """Abstain rather than guess: without a plane there are no millimetres."""
    for img in (np.full((900, 700, 3), 255, np.uint8),
                _mat_photo()[: int(4 * 420 * 0.6), :]):
        body = client.post("/analyse", content=_png(img)).json()
        assert body["locked"] is False
        assert body["items"] == []
        assert body.get("overlay_png") is None


def test_too_oblique_a_view_is_refused_with_the_tilt_named() -> None:
    """The lock's quality gates, not just its visibility gate."""
    mat = _mat_photo()
    h, w = mat.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    d = w * 0.045                       # measured above the 0.04 index gate
    dst = np.float32([[d, d * 0.6], [w - d * 0.4, 0], [w, h - d * 0.5], [d * 0.3, h]])
    skewed = cv2.warpPerspective(mat, cv2.getPerspectiveTransform(src, dst), (w, h),
                                 borderValue=(235, 235, 235))
    res = analyse(skewed)
    assert res["locked"] is False
    assert res["reason"].startswith("perspective index")
    diag = res["diagnosis"]
    assert diag["markers_found"] == 4            # all four SEEN, still refused
    assert diag["corners_missing"] == []
    assert "oblique" in diag["headline"]
    assert diag["fix"]


def test_sample_can_demonstrate_the_tilt_refusal(client: TestClient) -> None:
    """?fail=tilt must be a REAL refusal from the real engine, not a canned
    string: all four markers are seen and the gate still says no."""
    body = client.get("/sample?fail=tilt").json()
    assert body["locked"] is False
    assert body["reason"].startswith("perspective index")
    assert body["persp_index"] > body["gates"]["max_persp_index"]
    d = body["diagnosis"]
    assert d["markers_found"] == 4 and d["corners_missing"] == []
    assert "oblique" in d["headline"] and d["fix"]
    assert body["items"] == []


def test_sample_can_demonstrate_the_covered_corner_refusal(
        client: TestClient) -> None:
    body = client.get("/sample?fail=marker").json()
    assert body["locked"] is False
    d = body["diagnosis"]
    assert d["markers_found"] == 3
    assert d["corners_missing"] == ["top-right"]
    assert body["items"] == []


@pytest.mark.parametrize("fail", ["tilt", "marker"])
def test_a_demonstrated_failure_publishes_no_accuracy(client: TestClient,
                                                      fail: str) -> None:
    """No lock means no millimetres, so there is nothing to score against
    truth. Publishing a zeroed accuracy block would read as agreement."""
    body = client.get(f"/sample?fail={fail}").json()
    assert "accuracy" not in body
    assert body.get("overlay_png") is None


def test_unknown_fail_mode_is_ignored_not_obeyed(client: TestClient) -> None:
    body = client.get("/sample?fail=bogus").json()
    assert body["locked"] is True


def test_diagnose_lock_covers_a_locked_result() -> None:
    loaded, _ = sample_scene(7)
    lock = PlaneEngine().detect(loaded)
    d = diagnose_lock(lock)
    assert d["markers_found"] == 4
    assert d["corners_missing"] == []
    assert d["fix"] == []


# --------------------------------------------------- 3. never a 500, ever

_HOSTILE = [
    b"",
    b"\x00",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"\xff\xd8" + b"\xff\xe1\x00\x08Exif\x00\x00" + b"\xff\xd9",
    b"\xff\xd8" + b"\xff\xe1\xff\xffExif\x00\x00II" + b"\x00" * 20,
    b"\xff\xd8" + b"\xff\xe1\x00\x10Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08" + b"\xff\xd9",
    b"\xff\xd8" + b"\xff\xe1\x00\x0cExif\x00\x00II\x2a\x00",
    "नमस्ते".encode() * 200,
    b"%PDF-1.7\n" + b"\x00" * 100,
    b"\x00\x00\x00\x18ftypmif1" + b"\x00" * 64,
    b"--x\r\nContent-Disposition: form-data\r\n\r\nzz\r\n--x--",
]


@pytest.mark.parametrize("path", ["/analyse", "/reference"])
@pytest.mark.parametrize("body", _HOSTILE, ids=range(len(_HOSTILE)))
def test_hostile_bodies_never_500(client: TestClient, path: str, body: bytes) -> None:
    r = client.post(path, content=body)
    assert r.status_code < 500, r.text
    assert "reason" in r.json()


def test_truncated_and_corrupted_images_never_500(client: TestClient) -> None:
    raw = _png(_mat_photo())
    rng = random.Random(4)
    cases = [raw[:10], raw[: len(raw) // 2], raw[:100] + os.urandom(400),
             raw[:-1], bytes(reversed(raw))[:5000]]
    for _ in range(8):
        cases.append(os.urandom(rng.choice([9, 512, 20000])))
    for body in cases:
        r = client.post("/analyse", content=body)
        assert r.status_code < 500, r.text
        assert "reason" in r.json()


def test_malformed_multipart_never_500(client: TestClient) -> None:
    r = client.post("/analyse", content=b"nonsense",
                    headers={"content-type": "multipart/form-data; boundary=zzz"})
    assert r.status_code < 500
    assert "reason" in r.json()


@pytest.mark.parametrize("url", ["/", "/health", "/sample",
                                 "/sample?reference=synthetic",
                                 "/sample?reference=bogus",
                                 "/sample?fail=tilt", "/sample?fail=marker",
                                 "/sample?fail=bogus",
                                 "/sample?seed=0&fail=tilt&reference=synthetic"])
def test_get_endpoints_never_500(client: TestClient, url: str) -> None:
    assert client.get(url).status_code < 500


def test_placement_error_is_reported_not_raised() -> None:
    """A buffer of the wrong size must become a named reason, not a traceback."""
    loaded, _ = sample_scene(7)
    res = analyse(loaded, reference=np.zeros((100, 100), np.uint8))
    assert res["ok"] is False
    assert res["reason"] == upload_app.R_NOT_RECTIFIED
    assert "840x1188" in res["detail"] or "840" in res["detail"]


# ------------------------------------------------------------- 4. the phone

@pytest.mark.parametrize("orientation", [1, 2, 3, 4, 5, 6, 7, 8])
def test_every_exif_orientation_is_read(orientation: int) -> None:
    raw = _jpeg_with_orientation(np.zeros((40, 20, 3), np.uint8), orientation)
    assert exif_orientation(raw) == orientation


def test_exif_orientation_reads_big_endian_too() -> None:
    raw = _jpeg_with_orientation(np.zeros((40, 20, 3), np.uint8), 6,
                                 big_endian=True)
    assert exif_orientation(raw) == 6


def test_exif_orientation_is_none_when_absent() -> None:
    ok, buf = cv2.imencode(".png", np.zeros((10, 10, 3), np.uint8))
    assert exif_orientation(buf.tobytes()) is None
    ok, buf = cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))
    assert exif_orientation(buf.tobytes()) is None
    assert exif_orientation(b"") is None
    assert exif_orientation(b"\xff\xd8") is None


@pytest.mark.parametrize("orientation", [1, 2, 3, 4, 5, 6, 7, 8])
def test_apply_orientation_returns_the_right_shape(orientation: int) -> None:
    img = np.zeros((40, 20, 3), np.uint8)
    out = apply_orientation(img, orientation)
    expect = (20, 40) if orientation in (5, 6, 7, 8) else (40, 20)
    assert out.shape[:2] == expect


def test_apply_orientation_6_and_8_are_inverses() -> None:
    img = np.arange(40 * 20 * 3, dtype=np.uint8).reshape(40, 20, 3)
    assert np.array_equal(apply_orientation(apply_orientation(img, 6), 8), img)


def test_a_sideways_phone_photo_is_rotated_upright_and_still_locks(
        client: TestClient) -> None:
    """A portrait phone photo of a landscape-held mat: pixels are stored
    rotated and the truth is only in the EXIF tag."""
    sideways = cv2.rotate(_mat_photo(), cv2.ROTATE_90_COUNTERCLOCKWISE)
    body = client.post("/analyse",
                       content=_jpeg_with_orientation(sideways, 6)).json()
    assert body["input"]["exif_orientation"] == 6
    assert body["input"]["rotated_by_exif"] is True
    # stored 1680x1188, must be worked on as 1188x1680
    assert body["input"]["decoded_px"] == [1680, 1188]
    assert body["input"]["upright_px"] == [1188, 1680]
    assert body["locked"] is True


def test_an_untagged_photo_is_not_rotated(client: TestClient) -> None:
    ok, buf = cv2.imencode(".jpg", _mat_photo(), [cv2.IMWRITE_JPEG_QUALITY, 95])
    body = client.post("/analyse", content=buf.tobytes()).json()
    assert body["input"]["exif_orientation"] is None
    assert body["input"]["rotated_by_exif"] is False
    assert body["input"]["decoded_px"] == body["input"]["upright_px"]


def test_a_huge_photo_is_downscaled_and_says_so(client: TestClient) -> None:
    big = cv2.resize(_mat_photo(), (3400, 4800), interpolation=cv2.INTER_LINEAR)
    body = client.post("/analyse", content=_png(big)).json()
    assert body["input"]["downscaled"] is True
    assert max(body["input"]["working_px"]) == upload_app.MAX_SIDE_PX
    assert body["locked"] is True, "downscaling must not cost the lock"


def test_multipart_upload_is_accepted(client: TestClient) -> None:
    """python-multipart is not installed, so the one part is unwrapped by hand.
    `curl -F file=@photo.jpg` is what a person naturally types."""
    boundary = "----gawaahtest"
    part = _png(_mat_photo())
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"m.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
    body += part + f"\r\n--{boundary}--\r\n".encode()
    r = client.post("/analyse", content=body,
                    headers={"content-type":
                             f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 200
    assert r.json()["locked"] is True


# ------------------------------------------------------ 5. the empty-mat ref

def test_reference_round_trip(client: TestClient) -> None:
    mat = _png(_mat_photo())
    assert client.get("/health").json()["reference_loaded"] is False
    r = client.post("/reference", content=mat)
    assert r.status_code == 200 and r.json()["reason"] == "reference_accepted"
    assert client.get("/health").json()["reference_loaded"] is True

    body = client.post("/analyse", content=mat).json()
    assert body["reference_source"] == "empty_mat_photo_supplied"
    assert body["items"] == [], "an empty mat against itself has nothing on it"

    assert client.delete("/reference").json()["ok"] is True
    assert client.get("/health").json()["reference_loaded"] is False


def test_reference_must_itself_lock(client: TestClient) -> None:
    r = client.post("/reference", content=_png(np.full((800, 600, 3), 255, np.uint8)))
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["diagnosis"]["markers_found"] == 0


def test_supplied_reference_finds_objects_placed_on_it(client: TestClient) -> None:
    loaded, empty = sample_scene(7)
    ok, buf = cv2.imencode(".png", empty)
    assert client.post("/reference", content=buf.tobytes()).status_code == 200
    ok, buf = cv2.imencode(".png", loaded)
    body = client.post("/analyse", content=buf.tobytes()).json()
    assert body["reference_source"] == "empty_mat_photo_supplied"
    assert len(body["items"]) == len(SAMPLE_TRUTH)
    acc = compare_to_truth(body["items"], upload_app.truth_rows())
    assert acc["worst_edge_err_mm"] <= TOL_MM


# ------------------------------------------------------------- 6. contract

def test_health_reports_the_numbers_that_decide_the_answers(
        client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    h = r.json()
    assert h["ok"] is True
    assert h["buffer_px"] == [BUF_W, BUF_H]
    assert h["marker_ids"] == list(MARKER_IDS)
    assert h["gates"]["max_persp_index"] > 0
    assert "money" in h and "GREEN" in h["money"]


@pytest.fixture
def spy_uvicorn(monkeypatch):
    """Capture what main() would serve, without binding anything.

    port_in_use is stubbed False on purpose: whether some OTHER process happens
    to hold 8790 while the suite runs must not decide whether this test passes.
    The busy-port guard has its own test, with its own real socket.
    """
    import uvicorn
    seen: dict = {}
    monkeypatch.setattr(upload_app, "port_in_use", lambda host, port: False)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw) or None)
    return seen


def test_port_flag_actually_reaches_the_server(spy_uvicorn) -> None:
    """--port must serve that port, not merely parse without complaint."""
    assert upload_app.main(["--port", "9111", "--host", "0.0.0.0"]) == 0
    assert spy_uvicorn["port"] == 9111
    assert spy_uvicorn["host"] == "0.0.0.0"


def test_port_defaults_to_8790(spy_uvicorn) -> None:
    assert upload_app.main([]) == 0
    assert spy_uvicorn["port"] == upload_app.DEFAULT_PORT == 8790
    assert spy_uvicorn["host"] == "127.0.0.1"


def test_selfcheck_passes_and_returns_zero(capsys) -> None:
    """`--selfcheck` is the no-browser proof that the tool measures truth."""
    rc = upload_app.main(["--selfcheck"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "locked=True" in out
    assert "worst edge error" in out
    for name, _w, _h, _xy in SAMPLE_TRUTH:
        assert name in out


def test_unknown_flag_is_rejected_not_ignored() -> None:
    with pytest.raises(SystemExit):
        upload_app.main(["--nonsense"])


def test_refuses_to_start_on_a_port_someone_else_owns(monkeypatch, capsys) -> None:
    """uvicorn logs a bind failure and returns normally, which looks exactly
    like a clean start -- and then you measure the other process's 404s and
    blame this tool. Caught for real during development on port 8791."""
    import socket
    import uvicorn
    ran: list = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: ran.append(kw))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        taken = squatter.getsockname()[1]
        assert upload_app.port_in_use("127.0.0.1", taken) is True
        rc = upload_app.main(["--port", str(taken)])

    assert rc == 1, "a busy port must be a non-zero exit, not a silent no-op"
    assert ran == [], "uvicorn must not be started on a port we cannot own"
    err = capsys.readouterr().err
    assert "already in use" in err
    assert "--port" in err, "the message must say how to fix it"


def test_a_free_port_is_not_reported_busy() -> None:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert upload_app.port_in_use("127.0.0.1", free) is False


def test_uploaded_bytes_are_never_echoed_back(client: TestClient) -> None:
    """INVARIANT 4: only the rectified 840x1188 crop leaves the process."""
    mat = _png(_mat_photo())
    body = client.post("/analyse", content=mat).json()
    assert body["source_image_returned"] is False
    assert "input_png" not in body
    for key in ("buffer_png", "overlay_png"):
        if body.get(key):
            img = cv2.imdecode(np.frombuffer(base64.b64decode(body[key]), np.uint8),
                               cv2.IMREAD_COLOR)
            assert img.shape[1] == BUF_W // 2 and img.shape[0] == BUF_H // 2


def _walk(node, keys: list[str], values: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            keys.append(str(k).lower())
            _walk(v, keys, values)
    elif isinstance(node, list):
        for v in node:
            _walk(v, keys, values)
    elif isinstance(node, str):
        values.append(node)


def test_this_tool_cannot_produce_money_or_green(client: TestClient) -> None:
    """INVARIANT 2: no simulated or panel result may ever produce green.

    Checked structurally rather than by substring, because the honest
    DISCLAIMER legitimately contains the word GREEN ("nothing here can mark a
    session GREEN") and a substring test would either fail on the disclaimer or
    be silenced into uselessness. What must not exist is a money FIELD or a
    settlement VERDICT -- so walk the keys, and require that no value is a bare
    settlement state.
    """
    bodies = [client.get("/sample").json(),
              client.post("/analyse", content=_png(_mat_photo())).json(),
              client.get("/sample?reference=synthetic").json()]
    banned_keys = ("paise", "amount", "price", "mrp", "rupee", "balance",
                   "payment", "razorpay", "intent", "settle", "verdict",
                   "webhook", "session_id", "signature")
    for b in bodies:
        keys: list[str] = []
        values: list[str] = []
        _walk(b, keys, values)
        for k in keys:
            assert not any(bad in k for bad in banned_keys), \
                f"money/settlement key {k!r} leaked into an upload result"
        # No value is a settlement state. The disclaimer is a sentence, not a
        # state, so it is allowed to name GREEN while refusing to be one.
        for v in values:
            assert v.strip().upper() not in ("GREEN", "PAID", "SETTLED"), v
        assert "paid" not in keys and "green" not in keys


def test_compare_to_truth_handles_a_missing_measurement() -> None:
    truth = upload_app.truth_rows()
    acc = compare_to_truth([], truth)
    assert acc["matched_count"] == 0
    assert all(r["matched"] is False for r in acc["rows"])
    assert acc["mean_edge_err_mm"] is None


def test_compare_to_truth_reports_extras() -> None:
    truth = upload_app.truth_rows()
    items = [{"id": 9, "centre_mm": [999.0, 999.0], "long_edge_mm": 1.0,
              "short_edge_mm": 1.0, "stable": True}]
    acc = compare_to_truth(items, truth[:1])
    assert acc["matched_count"] == 1
    assert acc["extra_items"] == []
    acc2 = compare_to_truth(items * 1 + [
        {"id": 10, "centre_mm": [1.0, 1.0], "long_edge_mm": 1.0,
         "short_edge_mm": 1.0, "stable": True}], truth[:1])
    assert acc2["extra_items"]


def test_run_sample_is_importable_without_a_server() -> None:
    """The measurement path has no HTTP in it, so it can be scripted."""
    res = run_sample(7)
    assert res["locked"] is True
    assert res["accuracy"]["worst_edge_err_mm"] <= TOL_MM
    assert res["simulated"] is True


def test_page_is_served_and_mentions_the_refusals(client: TestClient) -> None:
    page = client.get("/").text
    assert "<title>" in page
    for token in ("I DO NOT KNOW", "TRY A SAMPLE", "SIMULATED",
                  "measured vs truth", "invariant 4",
                  "CAMERA TOO OBLIQUE", "A CORNER COVERED",
                  "NO EMPTY-MAT REFERENCE"):
        assert token in page, token


def test_hide_marker_actually_covers_the_named_corner() -> None:
    """The demo failure must be caused by a covered marker, not by a flag."""
    clean, _ = sample_scene(7)
    hidden, _ = sample_scene(7, hide=1)
    assert not np.array_equal(clean, hidden)
    assert PlaneEngine().detect(clean).locked
    lock = PlaneEngine().detect(hidden)
    assert lock.locked is False
    assert 1 not in lock.ids_found
