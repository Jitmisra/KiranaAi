"""Tests for gawaah/sim_source.py — the synthetic counter session.

WHAT THESE TESTS ARE FOR
------------------------
``sim_source`` exists so that every capability panel can be seen working with
no camera, no printed mat and no second phone. That claim is only worth
anything if the pixels it emits are good enough for the REAL modules, so almost
nothing here checks the sim against itself. Each test runs the actual module —
``PlaneEngine``, ``PlacementDetector``, ``Brain``, ``OccluderGesture``,
``StickerRegistry``, ``ScreenFinder``, ``BurstStacker`` — over the sim's frames
and checks what that module concluded.

The two exceptions are determinism (a property of the source alone) and the
labelling/no-green tests, which are about what the source is NOT allowed to
contain.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from gawaah import chilla as _chilla
from gawaah import ident_sticker as _peel
from gawaah.brain import Brain, BrainConfig, LocalSettlement
from gawaah.clock import VirtualClock
from gawaah.identity import Gallery, Identifier
from gawaah.ledger import Ledger, verify
from gawaah.mudra import OccluderGesture
from gawaah.placement import PlacementDetector
from gawaah.sellevent import CentroidTracker, LineZone
from gawaah.sim_source import (
    KNOWN_SKUS,
    SELL_LINE_Y_MM,
    UNKNOWN_SKU,
    SimBeat,
    SimError,
    SimNote,
    SimSource,
)
from gawaah.takhti import BUF_H, BUF_W, PlaneEngine

SEED = 20260829


# ============================================================ fixtures/helpers


@pytest.fixture(scope="module")
def src() -> SimSource:
    """One grey-buffer source, shared. ``frame(i)`` is pure, so sharing is
    safe and saves re-rendering the mat for every test."""
    return SimSource(seed=SEED, colour=False)


@pytest.fixture(scope="module")
def frames(src: SimSource) -> list[np.ndarray]:
    return [src.frame(i) for i in range(src.total_frames)]


def _embed(crop: np.ndarray) -> np.ndarray:
    """An 8x8 mean-subtracted thumbnail.

    Deliberately NOT a neural net: invariant 3 says zero model weights, and
    identity.py takes the embedder as an injection precisely so a test can hand
    it 64 honest floats. This is the same embedder ``brain_server`` wires into
    the demo, so what these tests measure is what the demo will measure.
    """
    small = cv2.resize(crop, (8, 8), interpolation=cv2.INTER_AREA)
    v = small.astype(np.float64).ravel()
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return np.ones(64, np.float64) / 8.0 if n == 0.0 else v / n


def _settled_placements(ref: np.ndarray, frame: np.ndarray, n: int = 6):
    """Run the detector over one still frame until its stability gate settles."""
    det = PlacementDetector(ref)
    found = ()
    for _ in range(n):
        found = det.update(frame)
    return list(found)


def _build_brain(source: SimSource, work: Path):
    """The whole counter, wired the way brain_server wires it, but driven by
    the sim: real ledger, real kernel, real Razorpay simulator with real
    HMAC-SHA256 signatures, real green predicate, real identifier.

    Returns (brain, ledger). Nothing here can settle: no webhook is ever
    delivered by any test that uses this, which is the point of
    ``test_no_frame_of_the_sim_can_produce_green``.
    """
    from gawaah import kernel as _kernel
    from gawaah.brain_server import ClientRectifiedPlane
    from gawaah.rzp_sim import RazorpaySim

    clock = VirtualClock("2026-08-29T09:00:00.000+00:00", step_ms=100)
    ledger = Ledger(work / "kaala_dabba.jsonl")
    kern = _kernel.Kernel(str(work / "kernel.db"), clock, ledger)
    secret = "whsec_gawaah_sim_source_test"
    gateway = RazorpaySim(secret, clock, seed=0, ledger=ledger)
    settlement = LocalSettlement(kern, gateway, clock, ledger, secret)

    ref = source.reference_frame()
    gallery = Gallery()
    # Dogfood the one call a consumer is meant to make. If this is broken then
    # so is every wiring of the sim, and every money test below would be
    # measuring a hand-rolled gallery instead of the shipped one.
    prices = source.enrol_gallery(gallery, _embed, Brain._crop)
    assert prices == source.prices()

    brain = Brain(BrainConfig(
        clock=clock,
        ledger=ledger,
        settlement=settlement,
        plane=ClientRectifiedPlane(),
        tracker=CentroidTracker(max_dist_mm=25.0, max_missing_frames=3),
        line=LineZone.mat_exit_line(80.0, min_crossing_frames=3),
        identifier=Identifier(gallery, _embed),
        prices=prices,
        detector=PlacementDetector(ref, clock=clock),
        reference=ref,
    ))
    return brain, ledger


def _run_session(source: SimSource, work: Path):
    """Drive the whole script through the brain, honouring the sim's taps."""
    brain, ledger = _build_brain(source, work)
    states = []
    for frame, ts, note in source.frames():
        st = brain.ingest_frame(frame, ts)
        for cmd in note.commands:
            if cmd["type"] == "done":
                st = brain.done()
        states.append((note, st))
    return brain, ledger, states


# ============================================================ 1. the buffer


def test_every_frame_is_exactly_the_840x1188_rectified_buffer(src, frames):
    """INVARIANT 4's shape, on every single frame of the script.

    Not "the first one" and not "a sample": the gate in brain_server's
    ``decode_rect`` refuses anything that is not this shape, so ONE off-size
    frame is a beat that silently never reaches the brain.
    """
    assert src.total_frames > 100
    for i, f in enumerate(frames):
        assert f.shape == (BUF_H, BUF_W), (i, f.shape)
        assert f.dtype == np.uint8
    assert (BUF_W, BUF_H) == (840, 1188)


def test_colour_frames_are_bgr_of_the_same_shape_and_grey_to_the_same_pixels():
    """The BGR frame a client would send greys back to the buffer that was
    composited — channel-identical, not tinted.

    This is what lets the 3 mm claim mean anything: cvtColor's weights are
    0.114/0.587/0.299, so a tinted wrapper would measure a DIFFERENT luminance
    than the one painted and every millimetre downstream would be measuring the
    tint instead of the object.
    """
    grey = SimSource(seed=SEED, colour=False)
    col = SimSource(seed=SEED, colour=True)
    for i in (0, 30, 90, col.total_frames - 1):
        c = col.frame(i)
        assert c.shape == (BUF_H, BUF_W, 3)
        assert np.array_equal(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), grey.frame(i))


# ============================================================ 2. the mat lock


def test_the_mat_locks_on_a_rendered_frame(src):
    """CORE's abstention ends here: a REAL PlaneEngine re-detects the four
    ArUco markers in the rendered mat and locks on it.

    Note what is NOT stubbed. ``render_takhti`` draws the same markers that go
    on the print, they are resampled to the rectified buffer, dimmed to paper
    white under a lamp, and then found again by the same detector a camera
    frame would go through.
    """
    lock = PlaneEngine().detect(src.frame(0))
    assert lock.locked, lock.reason
    assert lock.reason == "locked"
    assert set(lock.ids_found) == {0, 1, 2, 3}
    assert lock.scale_err < 0.015
    assert lock.persp_index < 0.040
    assert lock.reproj_rmse_px < 0.01


def test_the_mat_locks_on_every_frame_of_the_script(src, frames):
    """Every beat, not just the empty one.

    This is a regression test with a story. The first draft of the script
    parked four items abreast past the sell line; the outer two clipped the
    bottom corner markers, and the plane engine then refused to lock with a
    13.3 % scale error for the last 71 frames of the session. The SCRIPT was
    changed — the customer bags the three before the unknown goes across — and
    the gate was left exactly as it was.
    """
    engine = PlaneEngine()
    failures = [(i, engine.detect(f).reason)
                for i, f in enumerate(frames)
                if not engine.detect(f).locked]
    assert failures == []


def test_the_mat_lock_abstention_is_still_reachable(src):
    """INVARIANT 7. Adding a path to the working state must not delete the
    honest one: hide one marker and the plane engine still refuses."""
    frame = src.frame(0).copy()
    frame[0:200, 0:200] = 200          # paint out the top-left marker
    lock = PlaneEngine().detect(frame)
    assert not lock.locked
    assert "missing markers" in lock.reason
    assert 0 not in lock.ids_found


# ======================================================== 3. the millimetres


@pytest.mark.parametrize("spec", KNOWN_SKUS, ids=lambda s: s.sku_id)
def test_a_known_object_measures_within_3mm_of_what_was_composited(src, spec):
    """The whole claim of the file, one SKU at a time.

    The object is composited in MILLIMETRES onto the metric plane, and the real
    placement detector — absdiff, blur, threshold, open, close, contours,
    minAreaRect, 50 %-amplitude refit, stability gate — measures it back in
    millimetres. Tolerance is the task's 3 mm; the measured residuals are
    printed in the assertion message so a regression shows how far it drifted,
    not merely that it did.
    """
    ref = src.reference_frame()
    goods = src.beat("goods")
    frame = src.frame(goods.start)
    found = [p for p in _settled_placements(ref, frame) if p.measurable]
    assert len(found) == len(KNOWN_SKUS)

    near = min(found, key=lambda p: abs(p.centre_mm[0] - spec.x_mm))
    assert abs(near.centre_mm[0] - spec.x_mm) < 3.0, near.centre_mm
    d_long = abs(near.long_edge_mm - spec.long_mm)
    d_short = abs(near.short_edge_mm - spec.short_mm)
    assert d_long < 3.0, f"{spec.sku_id} long: {near.long_edge_mm} vs {spec.long_mm}"
    assert d_short < 3.0, f"{spec.sku_id} short: {near.short_edge_mm} vs {spec.short_mm}"
    # Measured residuals at the time of writing were all under 0.5 mm. Assert
    # the sim is not merely inside the ask but comfortably inside it, so that a
    # future change which quietly costs 2 mm fails here instead of passing.
    assert d_long < 1.0 and d_short < 1.0, (d_long, d_short)


def test_the_three_goods_never_merge_into_one_contour(src):
    """One contour is one price, so two packets read as one blob is a MONEY bug
    in the undercharging direction. The lanes are spaced for that."""
    ref = src.reference_frame()
    goods = src.beat("goods")
    det = PlacementDetector(ref)
    for i in range(goods.start, goods.stop):
        placements = det.update(src.frame(i))
        assert len(placements) == 3, (i, len(placements))
        for p in placements:
            assert p.reason == "OK", (i, p.reason)


def test_the_empty_mat_yields_no_placements_at_all(src):
    """The abstention that CORE starts on. An empty mat is empty."""
    ref = src.reference_frame()
    det = PlacementDetector(ref)
    for i in range(src.beat("settle").start, src.beat("settle").stop):
        assert det.update(src.frame(i)) == []


# ==================================================== 4. the money exclusion


def test_the_unknown_item_is_ambered_and_excluded_from_the_total(tmp_path, src):
    """INVARIANT 7 with a rupee sign on it.

    Three known SKUs cross the line and are priced. A fourth, whose 90 mm
    footprint nothing in the gallery is within 4 mm of, crosses too. It is
    admitted as an AMBER line with ``sku_id=None`` and ``price_paise=None`` —
    not a price of zero, which would be silent — and the total does not move.
    """
    brain, ledger, states = _run_session(src, tmp_path)
    st = brain.state()

    expected = src.expected_total_paise()
    assert expected == sum(s.price_paise for s in KNOWN_SKUS)
    assert st.total_paise == expected
    assert isinstance(st.total_paise, int)

    priced = {li.sku_id: li.price_paise for li in st.lines if li.sku_id}
    assert priced == src.prices()
    assert UNKNOWN_SKU.sku_id not in priced

    assert st.amber_count == 1
    (amber,) = st.amber_items
    assert amber.sku_id is None
    assert amber.price_paise is None
    assert amber.reason == "no_candidate_in_footprint"

    # It crossed. Being unnameable did not make it invisible.
    assert st.net_crossings == len(KNOWN_SKUS) + 1

    # And the total was still climbing before the unknown arrived: a total that
    # was right by accident (e.g. always zero) would pass everything above.
    totals = [s.total_paise for _n, s in states]
    assert totals[0] == 0
    assert max(totals) == expected
    unknown_beat = src.beat("unknown")
    during = {s.total_paise for n, s in states if n.beat in ("unknown", "lift")}
    assert during == {expected}, during
    assert unknown_beat.start > src.beat("goods").start


def test_prices_are_integer_paise_and_the_unknown_has_no_price(src):
    """INVARIANT 1. Money is an int, and an unidentified item has no price at
    all rather than a zero one."""
    prices = src.prices()
    assert set(prices) == {s.sku_id for s in KNOWN_SKUS}
    for v in prices.values():
        assert isinstance(v, int) and not isinstance(v, bool)
    assert UNKNOWN_SKU.price_paise is None
    assert isinstance(src.expected_total_paise(), int)


def test_the_ledger_is_written_and_verifies(tmp_path, src):
    """LEDGER's abstention ends here: there IS an audit head, it moved, and the
    hash chain over the whole session verifies."""
    brain, ledger, _states = _run_session(src, tmp_path)
    st = brain.state()
    assert st.ledger_lines > 20
    assert st.ledger_head != "0" * 64
    ok, count, head, bad = verify(ledger.path)
    assert ok, bad
    assert count == st.ledger_lines
    assert head == st.ledger_head


# ================================================= 5. invariant 2 — no green


def test_no_frame_of_the_sim_can_produce_green(tmp_path, src):
    """A picture is not a payment.

    The whole script runs against the REAL LocalSettlement, the REAL Razorpay
    simulator and the REAL green predicate. DONE is tapped, an intent is
    minted — and the session ends in AWAITING_SETTLEMENT with
    ``money_authorised`` False, because no signature-verified webhook was ever
    delivered and nothing in the frame stream can deliver one.
    """
    brain, _ledger, states = _run_session(src, tmp_path)
    st = brain.state()
    assert st.money_authorised is False
    assert st.settled_payment_id is None
    for _note, s in states:
        assert s.money_authorised is False
        assert s.session_state != "PAID"


def test_the_sim_has_no_money_surface_at_all(src):
    """Invariant 2 as a SHAPE, not as a promise.

    ``SimSource`` cannot mint, sign, pay or settle because it has no method,
    attribute or scripted command that does any of those things. Checked by
    inspection so that a later edit which adds an ``on_pay`` hook — the obvious
    convenience — fails here and has to be argued for.
    """
    banned = ("pay", "mint", "settle", "webhook", "secret", "sign", "hmac",
              "green", "authoris", "authoriz", "gateway", "razorpay")
    for attr in dir(src):
        if attr.startswith("__"):
            continue
        low = attr.lower()
        assert not any(b in low for b in banned), attr

    allowed_commands = {"select_panel", "enrol_sticker", "done"}
    for i in range(src.total_frames):
        for cmd in src.commands_at(i):
            assert cmd["type"] in allowed_commands, cmd
            assert cmd.get("simulated") is True, cmd

    import gawaah.sim_source as mod
    text = Path(mod.__file__).read_text()
    for forbidden in ("import hmac", "import hashlib", "requests", "urllib"):
        assert forbidden not in text, forbidden


# ================================================== 6. invariant 7 — labels


def test_every_beat_and_every_note_is_labelled_simulated(src):
    for beat in src.script():
        assert beat.simulated is True
        assert beat.to_dict()["simulated"] is True
    for i in range(src.total_frames):
        note = src.note_at(i)
        assert note.simulated is True
        assert note.to_dict()["simulated"] is True
        assert note["simulated"] is True
        assert note.label.startswith("SIMULATED")
    assert all(line.startswith("SIMULATED") for line in src.describe().splitlines())


def test_simulated_cannot_be_switched_off():
    """The label is not a dial. A consumer that could construct an unbadged
    note could show a simulated reading as a real one."""
    with pytest.raises(SimError):
        SimBeat("x", 1, "core", "t", "e", simulated=False)
    with pytest.raises(SimError):
        SimNote(0, "x", 0, "core", "t", "e", "l", simulated=False)


def test_every_panel_id_has_a_beat_that_drives_it(src):
    """The task in one assertion: every panel that was sitting on an abstention
    has a scripted way out of it."""
    assert {b.panel for b in src.script()} == SimSource.PANEL_IDS
    assert SimSource.PANEL_IDS == {"core", "mudra", "peel", "chilla", "saaf",
                                   "ledger"}


def test_a_beat_aimed_at_an_unknown_panel_is_refused():
    class Typo(SimSource):
        BEAT_PLAN = (("only", 1, "muddra", "t", "e"),)

    with pytest.raises(SimError, match="muddra"):
        Typo(seed=SEED)


def test_a_plan_that_leaves_a_panel_dark_is_refused():
    class Partial(SimSource):
        BEAT_PLAN = (("only", 1, "core", "t", "e"),)

    with pytest.raises(SimError, match="no beat drives"):
        Partial(seed=SEED)


# ==================================================== 7. determinism by seed


def test_the_same_seed_gives_byte_identical_frames():
    a = SimSource(seed=4242, colour=False)
    b = SimSource(seed=4242, colour=False)
    assert a.total_frames == b.total_frames
    for i in range(a.total_frames):
        fa, fb = a.frame(i), b.frame(i)
        assert fa.tobytes() == fb.tobytes(), i


def test_frame_is_pure_and_does_not_depend_on_call_order():
    """``frame(i)`` may be asked for out of order — a UI scrubbing the script
    does exactly that — and must not consume a shared random stream."""
    a = SimSource(seed=99, colour=False)
    forward = [a.frame(i) for i in range(a.total_frames)]
    b = SimSource(seed=99, colour=False)
    for i in reversed(range(b.total_frames)):
        assert np.array_equal(b.frame(i), forward[i]), i


def test_a_different_seed_gives_different_frames():
    a = SimSource(seed=1, colour=False)
    b = SimSource(seed=2, colour=False)
    differing = sum(
        0 if np.array_equal(a.frame(i), b.frame(i)) else 1
        for i in range(a.total_frames)
    )
    assert differing == a.total_frames


def test_the_frames_generator_and_the_pure_accessor_agree(src):
    seen = 0
    stream = SimSource(seed=SEED, colour=False)
    for i, (frame, ts, note) in enumerate(stream.frames()):
        assert np.array_equal(frame, src.frame(i))
        assert note.frame_index == i
        assert isinstance(ts, str) and ts.endswith("+00:00")
        seen += 1
    assert seen == src.total_frames


# ================================================================== 8. MUDRA


def test_mudra_commits_open_then_fist_then_ambiguous(src):
    """The real occluder engine, over the real silhouettes, with its real
    4-frame dwell filter. Each beat is asserted to COMMIT, not merely to
    produce the right instantaneous guess."""
    eng = OccluderGesture(src.reference_frame())
    committed: dict[str, str] = {}
    evidence: dict[str, object] = {}
    for name, want in (("palm", "OPEN"), ("fist", "FIST"),
                       ("unsure", "AMBIGUOUS")):
        beat = src.beat(name)
        for i in range(beat.start, beat.stop):
            state = eng.update(src.frame(i))
        committed[name] = state.state
        evidence[name] = (round(state.solidity, 3), state.defects,
                          round(state.compactness, 3), round(state.area_mm2))
        assert state.state == want, (name, state.state, state.reason, evidence[name])

    assert committed == {"palm": "OPEN", "fist": "FIST", "unsure": "AMBIGUOUS"}
    # The three shapes must sit in three DIFFERENT corners of mudra's decision
    # space, not merely produce three labels: solidity is the axis it decides on.
    sol = {k: v[0] for k, v in evidence.items()}
    assert sol["fist"] < 0.80 <= sol["palm"] <= 0.95
    assert sol["unsure"] > sol["palm"]
    assert evidence["palm"][1] >= 3 and evidence["unsure"][1] < 3


def test_mudras_hand_sized_gate_is_really_being_satisfied(src):
    """MUDRA refuses to call anything a hand unless it MEASURES like one, 4000
    to 22000 mm2. If the sim's silhouettes drifted out of that window the
    verdicts above would come back AMBIGUOUS for a completely different reason,
    so the areas are asserted directly."""
    eng = OccluderGesture(src.reference_frame())
    for name in ("palm", "fist", "unsure"):
        beat = src.beat(name)
        state = eng.update(src.frame(beat.start))
        assert 4000.0 <= state.area_mm2 <= 22000.0, (name, state.area_mm2)


def test_mudra_abstains_on_the_empty_mat(src):
    """The abstention MUDRA starts on stays reachable: nothing on the plane is
    NONE, not a guess."""
    eng = OccluderGesture(src.reference_frame())
    for i in range(src.beat("settle").start, src.beat("settle").stop):
        state = eng.update(src.frame(i))
        assert state.state == "NONE"
        assert state.decided is False


# =================================================================== 9. PEEL


def test_peel_abstains_then_reads_genuine_then_tampered(tmp_path, src):
    """The whole PEEL arc on real pixels: nothing enrolled, then enrolled and
    GENUINE against a later frame, then TAMPERED once one sixteenth of the
    sticker's modules are replaced."""
    reg = _peel.StickerRegistry(tmp_path / "stickers")
    name = src.sticker_name

    # 1. the abstention it starts on
    before = reg.compare(name, src.sticker_crop(src.frame(0)))
    assert before.verdict == _peel.UNREGISTERABLE
    assert before.reason == _peel.R_NOT_ENROLLED
    assert before.abstained

    # 2. enrol from the frame the script enrols on
    enrol = src.beat("enrol")
    record = reg.enrol(name, src.sticker_crop(src.frame(enrol.start)))
    assert reg.is_enrolled(name)
    assert record.contrast >= _peel.MIN_ENROLMENT_CONTRAST

    # 3. GENUINE on a LATER frame, not the enrolment frame itself
    later = src.beat("goods")
    good = reg.compare(name, src.sticker_crop(src.frame(later.stop - 1)))
    assert good.verdict == _peel.GENUINE, good.evidence()
    assert good.reason == _peel.R_COMPARED
    assert good.ecc_ok
    assert good.ignited_fraction < _peel.TAMPER_GATE

    # 4. TAMPERED once the modules are substituted
    tamper = src.beat("tamper")
    bad = reg.compare(name, src.sticker_crop(src.frame(tamper.stop - 1)))
    assert bad.verdict == _peel.TAMPERED, bad.evidence()
    assert bad.reason == _peel.R_COMPARED
    assert bad.ignited_fraction > _peel.TAMPER_GATE, bad.ignited_fraction


def test_the_tamper_patch_is_a_substitution_not_a_blank(src):
    """Why the tamper INVERTS modules instead of blanking or re-randomising.

    ``ident_sticker._blind_mask`` writes off regions where structure was
    DESTROYED, because that is glare or a thumb rather than a substitution — so
    a flat patch ignites nothing. And a freshly randomised patch agrees with
    the original on about half its cells by chance, which lands a 6.25 %-area
    change under the 3 % gate. Both failure modes are checked here against the
    real comparator so the choice is evidence rather than a comment.
    """
    tamper = src.beat("tamper")
    clean = src.sticker_crop(src.frame(0))
    substituted = src.sticker_crop(src.frame(tamper.start))
    assert not np.array_equal(clean, substituted)

    changed = substituted != clean
    frac = float(changed.mean())
    assert 0.02 < frac < 0.15, frac        # about one sixteenth of the sticker

    # Every changed pixel moved to the OTHER ink level, not to a flat fill.
    values = np.unique(substituted[changed])
    assert values.size >= 2, values

    with tempfile.TemporaryDirectory() as d:
        reg = _peel.StickerRegistry(d)
        reg.enrol("s", clean)
        blanked = clean.copy()
        y0 = int(clean.shape[0] * 5 / 16)
        x0 = int(clean.shape[1] * 5 / 16)
        h = int(clean.shape[0] * 4 / 16)
        w = int(clean.shape[1] * 4 / 16)
        blanked[y0:y0 + h, x0:x0 + w] = int(clean.mean())
        flat = reg.compare("s", blanked)
        real = reg.compare("s", substituted)
        assert real.ignited_fraction > flat.ignited_fraction, (
            real.ignited_fraction, flat.ignited_fraction)
        assert real.verdict == _peel.TAMPERED


# ================================================================= 10. CHILLA


def test_chilla_finds_the_phone_and_measures_it(src):
    """CHILLA's abstention ends here, and the geometry it reports is checked
    against what was actually composited."""
    finder = _chilla.ScreenFinder()
    finder.set_reference(src.reference_frame())
    beat = src.beat("screen")
    det = finder.detect(src.frame(beat.start))
    assert det.found, det.reason
    assert det.reason == "screen_found"
    assert det.in_placement_box
    assert det.n_candidates == 1

    cx, cy, w_mm, h_mm = src.screen_rect_mm()
    r = det.rect_mm
    assert abs(r.cx_mm - cx) < 3.0
    assert abs(r.cy_mm - cy) < 3.0
    assert abs(r.w_mm - w_mm) < 3.0, (r.w_mm, w_mm)
    assert abs(r.h_mm - h_mm) < 3.0, (r.h_mm, h_mm)
    assert det.rectangularity >= _chilla.MIN_RECTANGULARITY
    assert det.delta_luma >= _chilla.MIN_BRIGHTNESS_DELTA


def test_chilla_can_actually_run_its_reflective_versus_emissive_test(src):
    """The sim lights the mat with a counter LAMP for a reason.

    CHILLA's discrimination between an emissive panel and a diffuse reflector
    is a correlation between the patch and the illumination gradient underneath
    it. On perfectly flat paper there is no gradient, and CHILLA honestly
    reports ``coupling_measurable: False`` — it cannot tell a phone from a
    piece of white card. With the lamp field the test runs, and the emissive
    rectangle scores near zero coupling.
    """
    finder = _chilla.ScreenFinder()
    finder.set_reference(src.reference_frame())
    det = finder.detect(src.frame(src.beat("screen").start))
    assert det.coupling_measurable is True
    assert det.ref_contrast >= _chilla.MIN_COUPLING_CONTRAST
    assert det.illum_coupling is not None
    assert det.illum_coupling < _chilla.MAX_ILLUM_COUPLING, det.illum_coupling


def test_chilla_abstains_when_there_is_no_screen(src):
    finder = _chilla.ScreenFinder()
    finder.set_reference(src.reference_frame())
    for i in range(src.beat("settle").start, src.beat("settle").stop):
        det = finder.detect(src.frame(i))
        assert det.found is False
        assert det.reason == "no_bright_region"
        assert det.rect_mm is None


def test_chillas_corroboration_is_amber_and_never_green(tmp_path, src):
    """A screen that matches the mirror is still AMBER.

    Every verdict CHILLA can reach maps to AMBER by construction. The counter
    goes PAID because a signature-verified webhook said so; CHILLA's MATCHED
    had nothing to do with it and is not allowed to imply that it did.
    """
    finder = _chilla.ScreenFinder()
    finder.set_reference(src.reference_frame())
    assert finder.detect(src.frame(src.beat("screen").start)).found

    at = 1_800_000_000
    mirror = _chilla.Mirror(
        (_chilla.MirrorRow("pay_sim_0001", src.expected_total_paise(), at),),
        fetched_at=at,
    )
    matcher = _chilla.LedgerMatcher(mirror, window_seconds=180)
    result = matcher.match(src.expected_total_paise(), at + 4, mirror_age_s=4.0)
    assert result.verdict == _chilla.MATCHED
    assert result.light == "AMBER"
    assert result.is_amber
    assert all(v == "AMBER" for v in _chilla.LIGHT_FOR_VERDICT.values())

    # And a total the mirror never saw does not match.
    missing = matcher.match(src.expected_total_paise() + 100, at + 4,
                            mirror_age_s=4.0)
    assert missing.verdict == _chilla.NO_MATCH
    assert missing.light == "AMBER"


# =================================================================== 11. SAAF


def test_saaf_stacks_the_burst_rejects_the_blurred_frame_and_measures_a_gain(src):
    """SAAF's abstention ends here, with numbers it measured rather than
    numbers the sim supplied.

    The burst is ten grabs of the sticker with real sub-pixel shake and one
    deliberately defocused frame. The real ``BurstStacker`` registers them by
    ECC, throws the defocused one out BY NAME, and reports a sharpness gain it
    computed from the stacked result against the sharpest single frame.
    """
    result = src.stack_burst()
    assert result.used + result.rejected == len(src.saaf_burst_crops())
    assert result.used >= 8
    assert result.rejected == 1

    blurred_local = SimSource.BLURRED_BURST_INDEX
    rejected = [r for r in result.reports if r.code not in ("ok", "reference")]
    assert [r.index for r in rejected] == [blurred_local]
    assert rejected[0].code == "blur"

    assert math.isfinite(result.sharpness_gain)
    assert result.sharpness_gain > 1.0, result.sharpness_gain
    assert result.image is not None
    # The jitter is what earns the right to say anything beyond "denoised".
    assert result.warning == "", result.warning
    assert result.subpixel_diversity >= 0.10, result.subpixel_diversity
    assert result.mean_shift_px >= 0.15, result.mean_shift_px


def test_the_burst_is_marked_in_the_notes_and_the_ruined_frame_is_named(src):
    beat = src.beat("burst")
    members = [i for i in range(src.total_frames) if src.note_at(i).burst_member]
    assert members == list(range(beat.start, beat.stop))
    blurred = [i for i in members if src.note_at(i).burst_blurred]
    assert blurred == [beat.start + SimSource.BLURRED_BURST_INDEX]


def test_saaf_would_warn_honestly_if_the_camera_never_moved(src):
    """The abstention behind SAAF's headline claim, still reachable.

    Feed it the SAME crop ten times — a rigidly clamped camera over a static
    sticker — and it says NO_SUBPIXEL_DIVERSITY: "this result is denoising
    only, not super-resolution". The sim jitters the ROI so the other path can
    be seen too, but it must never be able to hide this one.
    """
    from gawaah.saaf import BurstStacker, W_NO_DIVERSITY

    still = src.sticker_crop(src.frame(0))
    result = BurstStacker().stack([still.copy() for _ in range(10)])
    assert result.warning.startswith(W_NO_DIVERSITY), result.warning
    assert result.degraded


# ==================================================== 12. the script itself


def test_the_script_beats_tile_the_frame_range_without_gaps(src):
    beats = src.script()
    assert beats[0].start == 0
    for prev, nxt in zip(beats, beats[1:]):
        assert nxt.start == prev.stop
    assert beats[-1].stop == src.total_frames
    for i in range(src.total_frames):
        cur = src.beat_at(i)
        assert cur.beat.start <= i < cur.beat.stop
        assert cur.index == i - cur.beat.start
        # The cursor is also a Mapping, which is how brain_server reads it.
        assert cur["name"] == cur.beat.name
        assert cur["index"] == cur.index
        assert cur["of"] == cur.beat.frames
        assert cur["simulated"] is True


def test_past_the_end_the_script_holds_rather_than_looping(src):
    """A loop would replay a settled sale with nothing reseeded, and a replayed
    sale is the one thing a counter must never show."""
    last = src.total_frames - 1
    cur = src.beat_at(last + 50)
    assert cur.beat is src.script()[-1]
    assert cur.index == cur.beat.frames - 1
    src.reset()
    for _ in range(src.total_frames + 5):
        frame, _ts, note = src.next_frame()
        assert frame.shape == (BUF_H, BUF_W)
    assert np.array_equal(frame, src.frame(last))


def test_the_goods_really_cross_the_sell_line(src):
    """The beat is not decoration: the packets start on the shopkeeper's side
    of y=340 mm and finish on the customer's."""
    goods = src.beat("goods")
    ref = src.reference_frame()
    det = PlacementDetector(ref)
    first = det.update(src.frame(goods.start))
    for i in range(goods.start + 1, goods.stop):
        last = det.update(src.frame(i))
    assert all(p.centre_mm[1] < SELL_LINE_Y_MM for p in first)
    assert all(p.centre_mm[1] > SELL_LINE_Y_MM for p in last)


def test_the_beat_list_is_json_ready(src):
    import json
    payload = json.dumps({"script": src.script_dicts(),
                          "note": src.note_at(0).to_dict()})
    back = json.loads(payload)
    assert len(back["script"]) == len(src.script())
    assert back["note"]["simulated"] is True
    assert all(b["simulated"] is True for b in back["script"])


def test_bad_input_is_refused_by_name(src):
    with pytest.raises(SimError):
        src.beat("no-such-beat")
    with pytest.raises(SimError):
        src.beat_at(-1)
    with pytest.raises(SimError):
        src.sku("NOT-A-SKU")
    with pytest.raises(SimError):
        src.sticker_crop(np.zeros((10, 10), np.uint8))


# ================================================ 13. the consumer seam


def test_the_focus_taps_only_name_panels_both_sides_agree_on(src):
    """A tap the server refuses is a refusal message on the shopkeeper's
    screen that nobody caused.

    ``web/app.js`` calls the basket panel 'core'; ``brain_server`` calls it
    'basket'. Until those two lists are reconciled — a change to two files this
    module does not own — the sim sends no focus tap for that panel and every
    beat still names its panel in the beat metadata.
    """
    from gawaah.brain_server import PANELS as SERVER_PANELS
    from gawaah.sim_source import FOCUSABLE_PANELS

    for i in range(src.total_frames):
        for cmd in src.commands_at(i):
            if cmd["type"] == "select_panel":
                assert cmd["id"] in SERVER_PANELS, cmd
                assert cmd["id"] in FOCUSABLE_PANELS, cmd

    # And the disagreement is real, not imagined — if it ever goes away this
    # test should be revisited rather than quietly kept.
    assert FOCUSABLE_PANELS < SimSource.PANEL_IDS
    assert SimSource.PANEL_IDS - FOCUSABLE_PANELS == {"core"}
    assert "core" not in SERVER_PANELS


def test_the_beat_cursor_answers_the_keys_brain_server_reads(src):
    """The exact keys ``brain_server``'s driver and its dry-run printer read
    off ``beat_at(i)``. A KeyError here is a blank ticker in the demo."""
    from collections.abc import Mapping

    cur = src.beat_at(30)
    assert isinstance(cur, Mapping)
    for key in ("name", "label", "detail", "index", "of"):
        assert key in cur, key
    assert cur["name"] == cur.beat.name
    assert cur["of"] == cur.beat.frames
    assert str(cur.beat) == cur.beat.name


def test_the_paste_goods_hook_brain_server_probes_for_exists(src):
    """``build_sim_server`` decides whether it can build a gallery by probing
    the frame source for ``_paste_goods``. It exists, it paints a known SKU,
    and the placement detector measures that SKU back."""
    paste = getattr(src, "_paste_goods", None)
    assert callable(paste)
    buf = src.reference_frame().copy()
    paste(buf, 150.0)
    found = [p for p in _settled_placements(src.reference_frame(), buf)
             if p.measurable]
    assert len(found) == 1
    spec = src.sku("CHAI-250")
    assert abs(found[0].long_edge_mm - spec.long_mm) < 3.0
    assert abs(found[0].centre_mm[1] - 150.0) < 3.0


def test_enrol_gallery_refuses_a_half_built_gallery(src):
    """A gallery missing one SKU prices two thirds of a basket and ambers the
    rest, which looks like a detection failure and is a wiring failure. It
    raises instead."""
    class Refusing:
        def enroll(self, *a, **k):
            raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        src.enrol_gallery(Refusing(), _embed, Brain._crop)


def test_a_marker_clipping_sticker_roi_is_refused_by_name():
    """The ROI brain_server would try first paints over marker 0.

    Accepting it would cost the mat lock, and a session whose CORE panel never
    lights because of a 12 x 2 mm sliver of ink is the most expensive silent
    failure in the file. It is refused with the marker named, so the caller
    falls back instead of shipping a dead demo.
    """
    from gawaah.brain_server import DEFAULT_STICKER_ROI_MM

    with pytest.raises(SimError, match="marker 0"):
        SimSource(sticker_roi_mm=DEFAULT_STICKER_ROI_MM)
    with pytest.raises(SimError, match="off the"):
        SimSource(sticker_roi_mm=(280.0, 400.0, 40.0, 40.0))


def test_brain_server_picks_this_source_up_and_proves_the_buffer():
    """The seam, end to end: ``load_sim_source()`` imports this module,
    constructs it, and PROVES frame 0 is the rectified 840x1188 crop before it
    will hand it to the brain."""
    from gawaah.brain_server import load_sim_source

    source, why = load_sim_source()
    assert type(source).__name__ == "SimSourceAdapter", (type(source), why)
    assert "sim_source.SimSource" in why, why
    assert source.total_frames > 100
    assert source.frame(0).shape[:2] == (BUF_H, BUF_W)
