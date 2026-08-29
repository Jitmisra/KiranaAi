"""THE TRUE END-TO-END SUITE.

Twelve scenarios, each one driving the seventeen real modules through a whole
customer at the counter — from ArUco markers on a rendered A3 sheet to an
HMAC-signed webhook and an integer number of paise.

WHAT MAKES THIS SUITE DIFFERENT FROM THE UNIT SUITES
====================================================
Every other test file in this repo exercises one module against a harness built
for that module. This one exercises the JOINTS: the places where placement's
millimetres become identity's footprint tiebreak, where sellevent's crossing
becomes session's committed line, where session's total becomes kernel's
idempotency key, where the gateway's raw bytes become a green verdict.

It does NOT import ``gawaah.brain``. The wiring lives in
``tools/e2e_scenarios.py`` and is written independently, so a brain that stops
calling ``session.on_exit`` — or calls it twice — fails these tests instead of
redefining them. That independence is the entire point; see the module
docstring there.

NOTHING HERE IS MOCKED
----------------------
No ``unittest.mock`` import, no fake homography, no pre-canned centroid, no
stubbed HMAC. The camera is synthetic (frames are rendered and projected) and
the gateway is ``RazorpaySim``, but both put real bytes into real production
code and everything downstream is the shipping implementation.

EVERY SCENARIO VERIFIES BOTH HASH CHAINS FROM GENESIS
-----------------------------------------------------
``ledger.verify`` is deliberately standalone — it does not import ``Ledger`` —
so a bug in the writer cannot mask itself in the verifier. Both the counter's
chain and the money daemon's chain are recomputed at the end of every scenario;
``test_<name>_ledger_verifies`` is the assertion, and it is repeated per
scenario rather than factored into one loop so that a failure names the
scenario that broke it.

WHAT THIS SUITE FOUND, AND WHAT IT DELIBERATELY DOES NOT ASSERT
---------------------------------------------------------------
`scenario_webhook_storm` races forty concurrent deliveries of one signed body.
It pins the money — one payment, one settled intent, one audit line, every
time — and only RECORDS `green_verdicts`, which is measured anywhere from 1 to
40 across runs because `GreenPredicate`'s replay gate is a check-then-add on a
plain set. Pinning that number would be pinning a race outcome; the defect is
reported to the owner of `gawaah/webhook.py` instead. Same for the handful of
`IllegalTransition`s that `Kernel.mark_settled` raises under contention: they
are fail-closed (an exception is not a debit), so they are counted, not
asserted away.

SPEED
-----
The scenarios are module-scoped fixtures: each one runs ONCE and every test
about it reads that one result. The happy path renders and processes 160 camera
frames (~2.6 s); the rest reuse a cached empty-mat reference and gallery and
cost ~0.1-0.5 s each. The whole file is ~6 s.
"""
from __future__ import annotations

import ast
import json

import numpy as np
import pytest

from gawaah.identity import DEFAULT_PHI, DEFAULT_TAU_MM
from gawaah.kernel import (
    CALLING, ESCALATED, FAILED, INDETERMINATE, SETTLED, idem_key,
)
from gawaah.ledger import verify as ledger_verify
from gawaah.money import to_rupees_str
from gawaah.money import total as sum_paise
from gawaah.placement import STABLE_FRAMES
from gawaah.sellevent import MAT_H_MM as SELL_MAT_H_MM
from gawaah.sellevent import MAT_W_MM as SELL_MAT_W_MM
from gawaah.session import GREEN_EVENTS, Reason, State
from gawaah.takhti import MAT_H_MM, MAT_W_MM
from gawaah.webhook import verify_signature
from tools import e2e_scenarios as e2e
from tools.e2e_scenarios import (
    CATALOGUE, UNENROLLED, CrossingEvidence, block_embed, build_rig,
    replay_crossing, scenario_amber_excluded, scenario_concurrency,
    scenario_crash, scenario_crash_before_gateway, scenario_happy_path,
    scenario_mat_lost, scenario_offline, scenario_replay, scenario_revert,
    scenario_tampered_webhook, scenario_webhook_storm, scenario_wrong_amount,
)

# The three enrolled goods, and the exact paise the counter must arrive at.
BASKET = ("PARLE_G", "MAGGI", "SURF")
BASKET_PAISE = int(sum_paise([CATALOGUE[s].price_paise for s in BASKET]))   # 5950


# ------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def happy():
    return scenario_happy_path()


@pytest.fixture(scope="module")
def amber():
    return scenario_amber_excluded()


@pytest.fixture(scope="module")
def revert():
    return scenario_revert()


@pytest.fixture(scope="module")
def wrong_amount():
    return scenario_wrong_amount()


@pytest.fixture(scope="module")
def tampered():
    return scenario_tampered_webhook()


@pytest.fixture(scope="module")
def replayed():
    return scenario_replay()


@pytest.fixture(scope="module")
def offline():
    return scenario_offline()


@pytest.fixture(scope="module")
def crash():
    return scenario_crash(webhook_arrives=True)


@pytest.fixture(scope="module")
def crash_webhook_lost():
    return scenario_crash(webhook_arrives=False)


@pytest.fixture(scope="module")
def crash_before_gateway():
    return scenario_crash_before_gateway()


@pytest.fixture(scope="module")
def mat_lost():
    return scenario_mat_lost()


@pytest.fixture(scope="module")
def concurrency():
    return scenario_concurrency()


@pytest.fixture(scope="module")
def webhook_storm():
    return scenario_webhook_storm()


# ==========================================================================
# 0. THE HARNESS ITSELF — a driver that lies makes every test below a lie
# ==========================================================================

def _imports(*parts: str) -> set[str]:
    """Every module name a source file actually imports, via its AST.

    Deliberately not a text search. A grep for ``import cv2`` matches the
    sentence in `sellevent`'s docstring that EXPLAINS why it must not import
    cv2, so a grep-based version of these tests fails on the very comment that
    documents the property. Parsing the file asks the real question.
    """
    tree = ast.parse((e2e.ROOT.joinpath(*parts)).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


DRIVER = ("tools", "e2e_scenarios.py")


def test_the_suite_does_not_import_the_brain():
    """The independence claim, enforced rather than promised.

    If this suite drove the system through the production orchestrator it
    would be testing that orchestrator against itself, and a brain that
    stopped calling `session.on_exit` would pass by redefining the expectation.
    """
    for names in (_imports(*DRIVER), _imports("tests", "test_end_to_end.py")):
        assert not any(n == "brain" or n.startswith("gawaah.brain")
                       or n.endswith(".brain") for n in names), sorted(names)


def test_nothing_in_the_driver_is_mocked():
    """No mock library anywhere in the driver. The modules are the real ones."""
    names = _imports(*DRIVER)
    for forbidden in ("unittest.mock", "mock", "pytest", "responses"):
        assert not any(n == forbidden or n.startswith(forbidden + ".")
                       for n in names), f"driver imports {forbidden}"
    source = (e2e.ROOT.joinpath(*DRIVER)).read_text()
    for forbidden in ("MagicMock", "monkeypatch", "patch("):
        assert forbidden not in source, f"driver uses {forbidden}"


def test_driver_composes_every_module_in_the_chain():
    """takhti -> placement -> identity -> sellevent -> session -> kernel
    -> rzp_sim -> webhook, each imported directly, none via a wrapper."""
    names = _imports(*DRIVER)
    for module in ("takhti", "placement", "identity", "sellevent", "session",
                   "kernel", "rzp_sim", "webhook", "ledger", "money", "clock"):
        assert f"gawaah.{module}" in names, module


def test_the_synthetic_camera_produces_a_real_mat_lock():
    """The harness's mat must lock through the SHIPPING plane engine, at a
    tilt, with sensor noise on — otherwise every millimetre below is fiction."""
    rig = build_rig()
    try:
        frame = e2e.camera_frame(e2e.mat_sheet(), seed=3)
        lock = rig.engine.detect(frame)
        assert lock.locked, lock.reason
        assert lock.ids_found == (0, 1, 2, 3)
        assert lock.reproj_rmse_px < 1.0
        assert lock.scale_err < 0.015
        rect = rig.engine.rectify(frame, lock.H)
        assert rect.shape[:2] == (1188, 840)
    finally:
        rig.destroy()


def test_the_embedder_ships_no_weights():
    """INVARIANT 3's spirit: the injected embedder is arithmetic, not a model.

    Two crops of the same artwork must agree and two crops of different artwork
    must not — otherwise `identify` would be deciding on noise and the amber
    scenario would be passing by accident.
    """
    names = _imports(*DRIVER)
    for forbidden in ("torch", "tensorflow", "onnx", "onnxruntime", "tflite",
                      "sklearn", "keras", "jax", "transformers"):
        assert not any(n == forbidden or n.startswith(forbidden + ".")
                       for n in names), f"embedder path imports {forbidden}"
    src = (e2e.ROOT.joinpath(*DRIVER)).read_text()
    for loader in ("np.load(", "np.loadtxt(", "pickle.load("):
        assert loader not in src, f"embedder path loads {loader}"

    a = np.zeros((40, 70), np.uint8)
    a[:20, :] = 200
    b = a.copy()
    c = np.zeros((40, 70), np.uint8)
    c[:, :35] = 200
    va, vb, vc = block_embed(a), block_embed(b), block_embed(c)
    assert float(va @ vb) == pytest.approx(1.0, abs=1e-9)
    assert abs(float(va @ vc)) < 0.2
    assert float(np.linalg.norm(va)) == pytest.approx(1.0, abs=1e-9)


def test_unenrolled_good_is_the_same_size_as_an_enrolled_one():
    """The amber test would be worthless if the unknown item were merely an
    odd size: the footprint tiebreak alone would reject it and the cosine would
    never be consulted. It is deliberately PARLE_G's exact footprint."""
    assert UNENROLLED.long_mm == CATALOGUE["PARLE_G"].long_mm
    assert UNENROLLED.short_mm == CATALOGUE["PARLE_G"].short_mm
    assert UNENROLLED.sku_id not in CATALOGUE


def test_the_two_mat_definitions_still_agree():
    """sellevent duplicates the mat's size so paisa can re-run the crossing
    without OpenCV. If the two ever drift, the server-side replay silently
    starts judging a different line from the one the counter drew."""
    assert (SELL_MAT_W_MM, SELL_MAT_H_MM) == (MAT_W_MM, MAT_H_MM)


# ==========================================================================
# 1. HAPPY PATH
# ==========================================================================

def test_happy_path_reaches_paid(happy):
    assert happy.final_state == State.PAID.value
    assert happy.money_authorised is True


def test_happy_path_total_is_exact_to_the_paisa(happy):
    assert happy.total_paise == BASKET_PAISE == 5950
    assert happy.total_paise == happy.expected_paise
    assert happy.notes["rupees"] == "59.50"
    assert to_rupees_str(happy.total_paise) == "59.50"


def test_happy_path_authorised_exactly_the_intent(happy):
    """R4: PAID is reachable only through a settlement equal to the paisa."""
    assert happy.authorised_paise == BASKET_PAISE
    assert happy.mints[0].amount_paise == BASKET_PAISE
    assert happy.adjudications[0].amount_paise == BASKET_PAISE
    assert happy.adjudications[0].expected_paise == BASKET_PAISE


def test_happy_path_identified_all_three_from_real_crops(happy):
    """Every good was NAMED, not guessed at, and the margin cleared theta."""
    assert len(happy.observations) == 3
    for obs in happy.observations:
        assert obs.sku_id == obs.label, obs
        assert obs.identity_reason == "match"
        assert obs.price_paise == CATALOGUE[obs.label].price_paise
        assert obs.top1 >= DEFAULT_PHI
        assert obs.stable is True
        assert obs.placement_reason == "OK"


def test_happy_path_measured_millimetres_are_real_millimetres(happy):
    """The long edge measured through the whole optical chain must match the
    printed size. This is the number the footprint tiebreak keys on, so an
    error here is an error in identity, and then in money."""
    for obs in happy.observations:
        truth = CATALOGUE[obs.label].long_mm
        assert obs.long_edge_mm == pytest.approx(truth, abs=0.5), (
            f"{obs.label}: measured {obs.long_edge_mm:.3f}mm, printed {truth}mm")


def test_happy_path_crossings_were_imaged_not_asserted(happy):
    """The carry-out was decided from rendered frames, not from numbers this
    suite invented. 160 frames went through ArUco + homography + segmentation."""
    assert happy.frames_rendered >= 150
    assert happy.notes["imaged_frames"] == happy.frames_rendered
    assert [c.committed for c in happy.crossings] == [True, True, True]
    assert [c.net_count for c in happy.crossings] == [1, 2, 3]


def test_happy_path_had_no_amber_and_no_uncounted_crossing(happy):
    assert happy.amber_item_ids == ()
    for crossing in happy.crossings:
        assert crossing.amber is False
        assert crossing.exceptions == ()


def test_happy_path_running_total_grew_by_each_item(happy):
    """R2: the total is recomputed from committed lines, never incremented."""
    running = [c.total_paise for c in happy.crossings]
    assert running == [1000, 2400, 5950]
    assert running[-1] == BASKET_PAISE


def test_happy_path_minted_exactly_one_intent_and_one_charge(happy):
    assert len(happy.intents) == 1
    assert happy.intents[0]["state"] == SETTLED
    assert happy.intents[0]["amount_paise"] == BASKET_PAISE
    assert happy.settled_intents == 1
    assert happy.gateway_links == 1
    assert happy.gateway_payments == 1


def test_happy_path_idempotency_key_is_the_documented_one(happy):
    """The exactly-once key must be a hash of (session, cycle, amount) — not of
    anything a retry could vary."""
    intent = happy.intents[0]
    assert idem_key(intent["session_id"], intent["cycle"],
                    intent["amount_paise"]) is not None
    rows = [r for r in happy.ledger_lines if r.get("event") == "intent.created"]
    assert len(rows) == 1


def test_happy_path_webhook_was_green_for_the_right_reasons(happy):
    adj = happy.adjudications[0]
    assert adj.green is True
    assert adj.reason == "green"
    assert adj.severity == "GREEN"
    assert adj.signature_valid is True
    assert adj.session_reason == Reason.SETTLED
    assert adj.kernel_state == SETTLED


def test_happy_path_gateway_link_is_a_string_we_only_read(happy):
    """INVARIANT 6: the counter renders a QR from a URL the GATEWAY minted. It
    never constructs a UPI payload, and there is no code here that could."""
    assert happy.notes["short_url"].startswith("https://rzp.io/i/")
    # The URL was never assembled here; it came back off the link entity.
    assert happy.mints[0].short_url == happy.notes["short_url"]
    src = (e2e.ROOT.joinpath(*DRIVER)).read_text()
    for forbidden in ("upi://", "upi:", "&am=", "&pa=", "&tr=", "NPCI"):
        assert forbidden not in src, f"driver contains a payload primitive: {forbidden}"


def test_happy_path_ledger_verifies(happy):
    assert happy.ledger_ok is True
    assert happy.counter_ledger["ok"] is True
    assert happy.counter_ledger["error"] is None
    assert happy.counter_ledger["lines"] > 0
    assert happy.kernel_ledger["ok"] is True
    assert happy.kernel_ledger["error"] is None
    assert happy.kernel_ledger["lines"] > 0


def test_happy_path_ledger_tells_the_whole_story(happy):
    """R7: one applied transition, one line, one reason code."""
    reasons = happy.ledger_reasons("session")
    assert reasons.count(Reason.COMMITTED) == 3
    assert reasons.count(Reason.PRICED) == 3
    assert reasons.count(Reason.INTENT_REQUESTED) == 1
    assert reasons.count(Reason.SETTLED) == 1
    assert Reason.UNCOUNTED_CROSSING not in reasons
    assert Reason.AMOUNT_MISMATCH not in reasons


def test_happy_path_ledger_never_holds_a_secret(happy):
    """The audit trail must be publishable. Nothing in it may reconstruct a
    signature, and nothing may replay a delivery."""
    blob = json.dumps(list(happy.ledger_lines))
    assert e2e.WEBHOOK_SECRET not in blob
    assert "X-Razorpay-Signature" not in blob
    for line in happy.ledger_lines:
        assert "secret" not in line
        assert "signature" not in line


# ==========================================================================
# INVARIANT 5 — paisa re-runs the crossing predicate server-side
# ==========================================================================

def test_paisa_refuses_to_mint_against_a_count_it_cannot_reproduce(happy):
    """The counter claimed four crossings over a track that contains three.

    paisa rebuilt a CentroidTracker and a LineZone from the raw millimetre
    track and counted for itself. This is the load-bearing half of INVARIANT 5:
    the phone's number is evidence, not authority.
    """
    assert happy.notes["forged_mint_refused"] is True
    assert "counter_claimed=4" in happy.notes["forged_mint_reason"]
    assert "paisa_counted=3" in happy.notes["forged_mint_reason"]
    # ... and no second intent was created for the doctored claim.
    assert len(happy.intents) == 1


def test_server_side_replay_agreed_with_the_camera(happy):
    """The honest number, re-derived without OpenCV, matched the imaged one."""
    assert happy.mints[0].replay_net == 3
    assert happy.mints[0].replay_amber is False


def test_the_server_side_replay_imports_no_opencv():
    """INVARIANT 5 is only meaningful if the money service can run on a box
    that has never seen a camera. `sellevent` is the module that has to be
    clean, and this asserts it at the source."""
    names = _imports("gawaah", "sellevent.py")
    assert not any(n == "cv2" or n.startswith("cv2.") for n in names), sorted(names)
    assert not any(n.startswith("gawaah.takhti") for n in names), (
        "takhti imports cv2 at module scope; a lazy import is the same "
        "dependency, deferred")
    # And it really is importable with cv2 absent from the picture.
    import importlib
    assert importlib.import_module("gawaah.sellevent") is not None


def test_replay_crossing_is_deterministic_and_independent():
    """Two runs over the same track give the same answer, and the answer does
    not depend on any state the counter holds."""
    frames = tuple(
        ((148.0, y),) for y in
        [310.0, 340.0, 370.0, 395.0, 405.0, 407.0, 409.0, 411.0, 413.0]
    ) + ((), (), (), (), ())
    evidence = CrossingEvidence(frames=frames, claimed_net=1)
    first = replay_crossing(evidence)
    second = replay_crossing(evidence)
    assert first == second
    assert first[0] == 1          # net_count
    assert first[1] is False      # not amber


def test_a_crossing_with_no_stable_id_is_amber_not_a_silent_sale():
    """Abstention 11 through the money door: two centroids that tie for one
    track cannot be named, so the crossing cannot be counted — and paisa must
    refuse to mint rather than bill the shopkeeper's guess."""
    # Two blobs arriving on top of each other, then crossing together.
    frames = []
    for y in [300.0, 340.0, 380.0, 400.0, 406.0, 409.0, 412.0, 415.0]:
        frames.append(((148.0, y), (148.2, y)))
    frames.extend([(), (), (), (), ()])
    evidence = CrossingEvidence(frames=tuple(frames), claimed_net=2)
    net, amber, exceptions = replay_crossing(evidence)
    assert amber is True
    assert exceptions

    rig = build_rig()
    try:
        mint = rig.money.open_intent(rig.session.session_id, 5950, evidence)
        assert mint.minted is False
        assert "amber" in mint.reason
        assert rig.kernel.count() == 0, "an amber basket must mint nothing"
    finally:
        rig.destroy()


# ==========================================================================
# 2. AMBER — the unknown item is excluded from the total
# ==========================================================================

def test_amber_unknown_item_is_not_priced(amber):
    obs = next(o for o in amber.observations if o.label == "SOAP")
    assert obs.sku_id is None
    assert obs.price_paise is None
    assert obs.identity_reason == "below_similarity"
    assert obs.top1 < DEFAULT_PHI


def test_amber_abstention_was_forced_by_appearance_not_by_size(amber):
    """The un-enrolled good measures the SAME long edge as PARLE_G, so the
    footprint tiebreak shortlisted it and only the cosine could refuse."""
    assert amber.notes["footprint_delta_vs_parle_mm"] < DEFAULT_TAU_MM
    assert amber.notes["footprint_delta_vs_parle_mm"] == pytest.approx(0.0, abs=0.1)
    assert amber.notes["unknown_n_candidates"] >= 1
    assert amber.notes["unknown_top1"] < 0.05


def test_amber_item_crossed_and_was_committed_as_a_line(amber):
    """R1's precondition: the item IS on the bill as a line. It is excluded
    from the money, not from the record — a silently dropped item is the bug."""
    unknown = amber.notes["unknown_item_id"]
    assert unknown in amber.committed_item_ids
    assert unknown in amber.amber_item_ids
    assert len(amber.committed_item_ids) == 3


def test_amber_total_is_correct_WITHOUT_the_unknown_item(amber):
    """The whole scenario, in one line: 3550 + 1000 and nothing else."""
    expected = int(sum_paise([CATALOGUE["SURF"].price_paise,
                              CATALOGUE["PARLE_G"].price_paise]))
    assert amber.total_paise == expected == 4550
    assert amber.total_paise == amber.expected_paise
    assert amber.notes["rupees"] == "45.50"


def test_amber_still_settles_for_the_reduced_amount(amber):
    assert amber.final_state == State.PAID.value
    assert amber.authorised_paise == 4550
    assert amber.intents[0]["amount_paise"] == 4550
    assert amber.settled_intents == 1


def test_amber_ledger_records_the_exclusion_explicitly(amber):
    """An abstention that is not in the audit trail is indistinguishable from
    a bug that lost an item."""
    excluded = amber.lines_where(module="session",
                                reason=Reason.COMMITTED_AMBER)
    assert len(excluded) == 1
    assert excluded[0]["excluded_from_total"] is True
    assert excluded[0]["abstained"] is True
    assert excluded[0]["item_id"] == amber.notes["unknown_item_id"]
    assert amber.ledger_reasons("session").count(Reason.COMMITTED) == 2


def test_amber_ledger_verifies(amber):
    assert amber.ledger_ok is True
    assert amber.counter_ledger["error"] is None
    assert amber.kernel_ledger["error"] is None


# ==========================================================================
# 3. REVERT
# ==========================================================================

def test_revert_decrements_the_total_exactly(revert):
    assert revert.notes["total_before_revert"] == BASKET_PAISE
    assert revert.notes["decrement"] == CATALOGUE["MAGGI"].price_paise == 1400
    assert revert.notes["total_after_revert"] == BASKET_PAISE - 1400
    assert revert.total_paise == revert.expected_paise == 4550


def test_revert_logs_human_override(revert):
    """R3. A reversal is a person overruling the machine, and the ledger has
    to say so or the shopkeeper cannot be defended later."""
    lines = revert.lines_where(module="session", reason=Reason.REVERTED)
    assert len(lines) == 1
    line = lines[0]
    assert line["human_override"] is True
    assert line["item_id"] == revert.notes["reverted_item_id"]
    assert line["removed_paise"] == 1400
    assert line["was_committed"] is True
    assert line["was_amber"] is False
    assert revert.notes["revert_detail"]["human_override"] is True


def test_revert_item_leaves_the_committed_set(revert):
    assert revert.notes["reverted_item_id"] not in revert.committed_item_ids
    assert len(revert.committed_item_ids) == 2


def test_revert_settles_for_the_decremented_amount(revert):
    assert revert.final_state == State.PAID.value
    assert revert.authorised_paise == 4550
    assert revert.intents[0]["amount_paise"] == 4550
    assert revert.notes["rupees"] == "45.50"


def test_revert_ledger_verifies(revert):
    assert revert.ledger_ok is True
    assert revert.counter_ledger["error"] is None
    assert revert.kernel_ledger["error"] is None


# ==========================================================================
# 4. WRONG AMOUNT
# ==========================================================================

def test_wrong_amount_never_reaches_paid(wrong_amount):
    assert wrong_amount.final_state == State.AMOUNT_MISMATCH.value
    assert wrong_amount.money_authorised is False
    assert wrong_amount.authorised_paise is None


def test_wrong_amount_is_off_by_exactly_one_paisa(wrong_amount):
    """One paisa. Not one rupee, not a rounding band — the smallest wrong
    number there is, which is the one a float would have swallowed."""
    assert wrong_amount.notes["intent_paise"] == BASKET_PAISE
    assert wrong_amount.notes["webhook_paise"] == BASKET_PAISE + 1
    assert wrong_amount.notes["delta_paise"] == 1


def test_wrong_amount_was_otherwise_a_perfect_webhook(wrong_amount):
    """Valid HMAC, green event, right session. Only the amount gate can catch
    it, so this test is what proves the amount gate exists."""
    adj = wrong_amount.adjudications[-1]
    assert adj.signature_valid is True
    assert adj.green is False
    assert adj.reason == "amount_mismatch"
    assert adj.severity == "RED"


def test_wrong_amount_session_holds_red_rather_than_guessing(wrong_amount):
    assert wrong_amount.notes["session_reason"] == Reason.AMOUNT_MISMATCH
    lines = wrong_amount.lines_where(module="session",
                                     reason=Reason.AMOUNT_MISMATCH)
    assert len(lines) == 1
    assert lines[0]["expected_paise"] == BASKET_PAISE
    assert lines[0]["webhook_amount_paise"] == BASKET_PAISE + 1


def test_wrong_amount_leaves_the_intent_unsettled(wrong_amount):
    """Nothing is marked settled on a number we did not ask for."""
    assert wrong_amount.settled_intents == 0
    assert wrong_amount.intents[0]["state"] == CALLING
    assert wrong_amount.intents[0]["payment_id"] is None


def test_wrong_amount_ledger_verifies(wrong_amount):
    assert wrong_amount.ledger_ok is True
    assert wrong_amount.counter_ledger["error"] is None
    assert wrong_amount.kernel_ledger["error"] is None


# ==========================================================================
# 5. TAMPERED WEBHOOK
# ==========================================================================

def test_tampered_webhook_never_reaches_paid(tampered):
    assert tampered.final_state == State.AWAITING_SETTLEMENT.value
    assert tampered.money_authorised is False
    assert tampered.authorised_paise is None
    assert tampered.settled_intents == 0


def test_tampered_webhook_differs_by_exactly_one_byte(tampered):
    assert tampered.notes["bytes_differ"] == 1
    assert tampered.notes["same_length"] is True
    assert tampered.notes["original_sha256"] != tampered.notes["tampered_sha256"]


def test_tampered_body_is_still_valid_json_and_still_claims_money(tampered):
    """The tamper is not a corruption the parser would have caught. It is a
    well-formed document asking for a different number of rupees, which is
    precisely why the HMAC has to run BEFORE the parser."""
    assert tampered.notes["still_valid_json"] is True
    assert tampered.notes["tampered_claim_paise"] != tampered.notes["honest_claim_paise"]
    assert tampered.notes["honest_claim_paise"] == BASKET_PAISE


def test_tampered_webhook_is_rejected_on_the_signature(tampered):
    adj = tampered.adjudications[-1]
    assert adj.green is False
    assert adj.reason == "bad_signature"
    assert adj.signature_valid is False
    assert tampered.notes["session_reason"] == Reason.BAD_SIGNATURE


def test_tampered_webhook_is_discarded_and_logged(tampered):
    lines = tampered.lines_where(module="session", reason=Reason.BAD_SIGNATURE)
    assert len(lines) == 1
    assert lines[0]["discarded"] is True
    assert lines[0]["to"] == State.AWAITING_SETTLEMENT.value


def test_the_untampered_body_would_have_verified(tampered):
    """Control: the rejection is about the tamper, not about a broken harness.

    Without this, a driver that always produced an invalid signature would
    make the test above pass for entirely the wrong reason.
    """
    rig = build_rig()
    try:
        counter = e2e.Counter(rig)
        counter.acquire_mat()
        counter.arrive({"PARLE_G": ("PARLE_G", e2e.LANE_X, e2e.ROW_Y[2])})
        counter.carry_out("PARLE_G")
        _, mint = counter.done()
        rig.sim.set_sink(None)
        paid = counter.pay(mint.link_id)
        delivery = paid.deliveries[0]
        assert verify_signature(delivery.body, delivery.signature,
                                e2e.WEBHOOK_SECRET) is True
        bad, _ = e2e._flip_amount_byte(delivery.body)
        assert verify_signature(bad, delivery.signature,
                                e2e.WEBHOOK_SECRET) is False
    finally:
        rig.destroy()


def test_tampered_webhook_ledger_verifies(tampered):
    assert tampered.ledger_ok is True
    assert tampered.counter_ledger["error"] is None
    assert tampered.kernel_ledger["error"] is None


# ==========================================================================
# 6. REPLAY
# ==========================================================================

def test_replay_delivered_the_identical_bytes_twice(replayed):
    """A true replay, not a lookalike: same event id, same body, same
    signature. Anything weaker would be caught by an accident."""
    assert replayed.notes["deliveries"] == 2
    assert replayed.notes["identical_bytes"] is True
    assert replayed.notes["identical_event_ids"] is True


def test_replay_greens_once_and_refuses_the_second(replayed):
    assert replayed.notes["verdicts"] == [(True, "green"), (False, "replay")]
    assert len(replayed.adjudications) == 2
    assert replayed.adjudications[0].green is True
    assert replayed.adjudications[1].green is False
    assert replayed.adjudications[1].reason == "replay"


def test_replay_settles_exactly_once_everywhere(replayed):
    """Three independent defences, all of which must agree: the predicate's
    replay store, the session's per-event memo, the kernel's idempotent
    mark_settled."""
    assert replayed.notes["session_settled_lines"] == 1
    assert replayed.notes["kernel_settled_lines"] == 1
    assert replayed.settled_intents == 1
    assert len(replayed.intents) == 1
    assert replayed.gateway_payments == 1


def test_replay_state_and_total_are_unchanged_by_the_second_delivery(replayed):
    assert replayed.final_state == State.PAID.value
    assert replayed.total_paise == replayed.expected_paise == BASKET_PAISE
    assert replayed.authorised_paise == BASKET_PAISE


def test_replay_ledger_verifies(replayed):
    assert replayed.ledger_ok is True
    assert replayed.counter_ledger["error"] is None
    assert replayed.kernel_ledger["error"] is None


# ==========================================================================
# 7. OFFLINE
# ==========================================================================

def test_offline_done_goes_to_pending_offline(offline):
    assert offline.notes["state_after_done"] == State.PENDING_OFFLINE.value


def test_offline_billing_continued_while_the_line_was_down(offline):
    """R6's first half. A good was measured, identified and committed with no
    network anywhere in the path, and the total grew."""
    assert offline.notes["billing_continued"] is True
    assert offline.notes["total_before_outage"] == CATALOGUE["MAGGI"].price_paise
    assert offline.notes["total_while_offline"] == BASKET_PAISE


def test_offline_authorised_nothing(offline):
    """R6's second half, and the one that matters: billing is not charging."""
    assert offline.notes["authorised_while_offline"] is False
    assert offline.notes["minted_while_offline"] is False
    assert offline.notes["intents_while_offline"] == 0


def test_offline_ledger_says_so_in_its_own_words(offline):
    lines = offline.lines_where(module="session",
                                reason=Reason.OFFLINE_NO_AUTHORISATION)
    assert len(lines) == 1
    assert lines[0]["to"] == State.PENDING_OFFLINE.value
    assert lines[0]["intent_amount_paise"] == BASKET_PAISE
    assert lines[0]["money_authorised"] is False


def test_offline_queue_drains_on_reconnect(offline):
    assert offline.notes["reconnect_reason"] == Reason.NETWORK_RESTORED
    assert offline.notes["drained_nonce"] is not None
    assert len(offline.intents) == 1
    assert offline.intents[0]["state"] == SETTLED


def test_offline_ends_paid_for_the_full_amount(offline):
    assert offline.final_state == State.PAID.value
    assert offline.total_paise == offline.expected_paise == BASKET_PAISE
    assert offline.authorised_paise == BASKET_PAISE
    assert offline.gateway_payments == 1


def test_offline_ledger_verifies(offline):
    assert offline.ledger_ok is True
    assert offline.counter_ledger["error"] is None
    assert offline.kernel_ledger["error"] is None


# ==========================================================================
# 8. CRASH
# ==========================================================================

def test_crash_died_in_the_indeterminate_window(crash):
    """The row was CALLING when the process died. That is exactly the state
    that means 'the money may or may not have moved', and recover() must call
    it that rather than retrying blind."""
    assert crash.notes["state_at_crash"] == CALLING
    assert crash.notes["recovered_rows"] == 1
    assert crash.notes["state_after_recover"] == "INDETERMINATE"


def test_crash_recovers_to_exactly_one_charge(crash):
    assert len(crash.intents) == 1
    assert crash.settled_intents == 1
    assert crash.gateway_links == 1
    assert crash.gateway_payments == 1
    assert crash.notes["kernel_settled_audit_lines"] == 1


def test_crash_settlement_names_the_gateways_own_payment(crash):
    assert crash.notes["payment_id"] == crash.notes["gateway_payment_id"]
    assert crash.intents[0]["payment_id"] == crash.notes["gateway_payment_id"]


def test_crash_reconcile_after_a_webhook_settlement_is_a_no_op(crash):
    """reconcile() has no charge path at all, and on a machine-done row it does
    not even reach the gateway. Running it after the webhook settled must
    change nothing."""
    assert crash.notes["settled_by"] == "webhook"
    assert crash.notes["state_after_reconcile"] == SETTLED
    assert crash.intents[0]["retrieve_attempts"] == 0


def test_crash_a_retry_of_the_whole_mint_buys_no_second_charge(crash):
    """The idempotency key already has a row, and that row is past NEW, so the
    gateway is never reached a second time."""
    assert crash.notes["retry_minted"] is False
    assert "only_one_gateway_call_per_intent" in crash.notes["retry_reason"]
    assert crash.gateway_payments == 1
    assert len(crash.intents) == 1


def test_crash_ends_paid(crash):
    assert crash.final_state == State.PAID.value
    assert crash.authorised_paise == BASKET_PAISE


def test_crash_ledger_verifies(crash):
    """Both chains, and the kernel's one was written by TWO different Kernel
    objects over the same file — the restart is the point."""
    assert crash.ledger_ok is True
    assert crash.counter_ledger["error"] is None
    assert crash.kernel_ledger["error"] is None
    assert crash.kernel_ledger["lines"] >= 4


# -- the harder crash: the webhook is lost for good -------------------------

def test_crash_with_lost_webhook_still_settles_exactly_once(crash_webhook_lost):
    assert crash_webhook_lost.notes["settled_by"] == "reconcile"
    assert crash_webhook_lost.settled_intents == 1
    assert len(crash_webhook_lost.intents) == 1
    assert crash_webhook_lost.gateway_payments == 1
    assert crash_webhook_lost.notes["kernel_settled_audit_lines"] == 1


def test_crash_with_lost_webhook_does_not_claim_paid(crash_webhook_lost):
    """The honest, uncomfortable outcome, asserted rather than hidden.

    The kernel knows the money moved because the gateway said so on a lookup.
    The SESSION does not go PAID, because green has exactly one door and a
    poll is not it (INVARIANT 2). A counter that turned green on a poll would
    be a counter that can be turned green by anything that can answer an HTTP
    request.
    """
    assert crash_webhook_lost.final_state == State.AWAITING_SETTLEMENT.value
    assert crash_webhook_lost.money_authorised is False
    assert crash_webhook_lost.authorised_paise is None


def test_crash_with_lost_webhook_ledger_verifies(crash_webhook_lost):
    assert crash_webhook_lost.ledger_ok is True
    assert crash_webhook_lost.counter_ledger["error"] is None
    assert crash_webhook_lost.kernel_ledger["error"] is None


# -- the other half of the window: the gateway was never reached ------------

def test_crash_before_gateway_died_after_the_write_ahead(crash_before_gateway):
    """The ordering that makes the survivor's question answerable at all.

    The row was durably CALLING before a single byte went to the gateway. If
    the call had come first, a process that died mid-flight would leave a
    charge with no local record of it — which is the failure mode write-ahead
    exists to prevent.
    """
    c = crash_before_gateway
    assert c.notes["died"] is True
    assert c.notes["state_at_crash"] == CALLING


def test_crash_before_gateway_really_never_reached_the_gateway(crash_before_gateway):
    """The load-bearing measurement: ZERO payment links existed at the moment
    of death, and still zero after reconcile decided the row's fate."""
    c = crash_before_gateway
    assert c.notes["links_at_crash"] == 0
    assert c.notes["links_after_reconcile"] == 0


def test_crash_before_gateway_recovers_to_indeterminate_then_failed(crash_before_gateway):
    """This is the branch `scenario_crash` cannot reach.

    recover() must say INDETERMINATE — the survivor genuinely does not know —
    and only the read-only lookup may downgrade that to FAILED, on the
    gateway's own word that it never saw the nonce. Nothing is guessed.
    """
    c = crash_before_gateway
    assert c.notes["recovered_rows"] == 1
    assert c.notes["state_after_recover"] == INDETERMINATE
    assert c.notes["state_after_reconcile"] == FAILED
    assert c.notes["reconcile_reason"] == "gateway_never_saw_nonce"


def test_crash_before_gateway_moved_no_money_on_the_dead_intent(crash_before_gateway):
    c = crash_before_gateway
    dead = [i for i in c.intents if i["nonce"] == c.notes["dead_nonce"]]
    assert len(dead) == 1
    assert dead[0]["state"] == FAILED
    assert dead[0]["payment_id"] is None
    assert dead[0]["needs_human"] is False


def test_crash_before_gateway_refuses_a_retry_on_the_same_cycle(crash_before_gateway):
    """A decided row is decided. Re-minting the same (session, cycle, amount)
    must not resurrect it, or FAILED would be a suggestion rather than a fact."""
    c = crash_before_gateway
    assert c.notes["same_cycle_retry_minted"] is False
    assert c.notes["same_cycle_retry_reason"] == (
        f"intent_already_{FAILED}:only_one_gateway_call_per_intent")


def test_crash_before_gateway_retry_on_a_new_cycle_buys_exactly_one_charge(
        crash_before_gateway):
    """R8's real shape. 'Exactly one charge' is a statement about the WHOLE
    story: one intent that never reached the gateway, one that did, and one
    payment between them."""
    c = crash_before_gateway
    assert c.notes["retry_minted"] is True
    assert c.notes["nonces_differ"] is True
    assert len(c.intents) == 2
    assert sorted(i["state"] for i in c.intents) == [FAILED, SETTLED]
    assert sorted(i["cycle"] for i in c.intents) == [0, 1]
    assert c.settled_intents == 1
    assert c.gateway_links == 1
    assert c.gateway_payments == 1


def test_crash_before_gateway_retry_billed_the_unchanged_basket(crash_before_gateway):
    """The basket did not change, so the amount must not have. Only the
    idempotency cycle moved."""
    c = crash_before_gateway
    assert {i["amount_paise"] for i in c.intents} == {BASKET_PAISE}
    assert c.final_state == State.PAID.value
    assert c.authorised_paise == BASKET_PAISE
    assert c.notes["rupees"] == "59.50"


def test_crash_before_gateway_ledger_verifies(crash_before_gateway):
    c = crash_before_gateway
    assert c.ledger_ok is True
    assert c.counter_ledger["error"] is None
    assert c.kernel_ledger["error"] is None


# ==========================================================================
# 9. MAT LOST
# ==========================================================================

def test_mat_lost_was_detected_by_the_plane_engine(mat_lost):
    """Not declared by the harness: two ArUco markers were covered on the
    rendered sheet and the shipping detector refused to lock."""
    assert mat_lost.notes["markers_found"] == [2, 3]
    assert "missing markers" in mat_lost.notes["lock_reason"]
    assert mat_lost.notes["transition_reason"] == Reason.MAT_LOST


def test_mat_lost_freezes_the_total(mat_lost):
    assert mat_lost.final_state == State.MAT_LOST.value
    assert mat_lost.notes["frozen_total_paise"] == mat_lost.expected_paise
    assert mat_lost.total_paise == mat_lost.notes["frozen_total_paise"]


def test_mat_lost_stops_all_further_billing(mat_lost):
    """R5. Every billing door — exit, placement, revert, price, done — is
    tried while the mat is gone, and every one of them refuses."""
    assert mat_lost.notes["every_billing_door_refused"] is True
    for door, reason in mat_lost.notes["refusals"].items():
        assert reason == Reason.REFUSED_MAT_LOST, door
    assert mat_lost.notes["total_after_attempts"] == mat_lost.notes["frozen_total_paise"]


def test_mat_lost_does_not_admit_a_new_line(mat_lost):
    """A placement offered while the mat is gone must not even become a line:
    a line that exists is a line that could be billed after the thaw."""
    assert mat_lost.notes["ghost_line_created"] is False
    assert len(mat_lost.committed_item_ids) == 2


def test_mat_lost_authorises_nothing(mat_lost):
    assert mat_lost.money_authorised is False
    assert mat_lost.settled_intents == 0
    assert mat_lost.intents == ()
    assert mat_lost.gateway_links == 0


def test_mat_lost_ledger_verifies(mat_lost):
    assert mat_lost.ledger_ok is True
    assert mat_lost.counter_ledger["error"] is None
    assert mat_lost.kernel_ledger["error"] is None


# ==========================================================================
# 10. CONCURRENCY
# ==========================================================================

def test_concurrency_raced_fifty_threads(concurrency):
    assert concurrency.notes["threads"] == 50


def test_concurrency_produced_exactly_one_intent(concurrency):
    assert len(concurrency.intents) == 1
    assert concurrency.notes["intent_created_lines"] == 1
    assert concurrency.notes["intent_calling_lines"] == 1
    assert concurrency.notes["intent_requested_lines"] == 1


def test_concurrency_produced_exactly_one_charge(concurrency):
    assert concurrency.notes["mint_winners"] == 1
    assert concurrency.notes["distinct_link_ids"] == 1
    assert concurrency.gateway_links == 1
    assert concurrency.gateway_payments == 1
    assert concurrency.settled_intents == 1


def test_concurrency_the_forty_nine_losers_refused_for_the_right_reason(concurrency):
    """They did not fail, and they did not silently succeed: they were told
    the intent was already past NEW, which is the only state in which a
    gateway call is reachable."""
    assert concurrency.notes["refusal_reasons"] == [
        f"intent_already_{CALLING}:only_one_gateway_call_per_intent"]
    assert concurrency.notes["done_applied"] == 1


def test_concurrency_settled_for_the_right_amount(concurrency):
    assert concurrency.final_state == State.PAID.value
    assert concurrency.total_paise == concurrency.expected_paise == BASKET_PAISE
    assert concurrency.authorised_paise == BASKET_PAISE
    assert concurrency.intents[0]["amount_paise"] == BASKET_PAISE


def test_concurrency_ledger_verifies(concurrency):
    """The chain survives fifty threads appending through the same Ledger."""
    assert concurrency.ledger_ok is True
    assert concurrency.counter_ledger["error"] is None
    assert concurrency.kernel_ledger["error"] is None


# ==========================================================================
# 10b. WEBHOOK STORM — exactly-once under real contention
# ==========================================================================

def test_webhook_storm_raced_the_whole_paisa_hot_path(webhook_storm):
    """`scenario_replay` delivers the same body twice in sequence. This one
    delivers it forty times at once, which is what a FastAPI worker actually
    sees when a gateway retries a delivery that is still in flight."""
    assert webhook_storm.notes["threads"] == 40


def test_webhook_storm_charges_exactly_once(webhook_storm):
    """The invariant that matters, under the hardest conditions in the suite.

    Forty unguarded trips through adjudicate -> confirm collapse to one
    settlement, because `Kernel._transition` is a guarded move on a row that
    can leave CALLING exactly once.
    """
    ws = webhook_storm
    assert ws.gateway_payments == 1
    assert ws.gateway_links == 1
    assert ws.settled_intents == 1
    assert len(ws.intents) == 1
    assert ws.notes["kernel_settled_lines"] == 1
    assert ws.notes["settled_exactly_once"] is True


def test_webhook_storm_writes_one_settlement_line_not_forty(webhook_storm):
    """The audit chain must record the settlement once. A chain that grew a
    line per retry would make 'how many times were we paid' unanswerable."""
    assert webhook_storm.notes["session_settled_lines"] == 1


def test_webhook_storm_settles_for_the_right_amount(webhook_storm):
    ws = webhook_storm
    assert ws.final_state == State.PAID.value
    assert ws.total_paise == ws.expected_paise == BASKET_PAISE
    assert ws.authorised_paise == BASKET_PAISE
    assert ws.intents[0]["amount_paise"] == BASKET_PAISE


def test_webhook_storm_every_confirm_either_settled_or_refused(webhook_storm):
    """No thread got a third outcome. `mark_settled` is idempotent in sequence
    (36 threads got SETTLED back) and fail-closed under the race (4 raised
    rather than writing) — and an exception is not a debit, which is why the
    payment count above is still one.

    Both numbers are recorded rather than pinned: they are a scheduling
    artefact and asserting an exact split would be asserting a race outcome.
    What is pinned is that they account for every thread and that neither route
    moved money twice.
    """
    ws = webhook_storm
    assert ws.notes["confirm_calls"] + ws.notes["confirm_errors"] == \
        ws.notes["green_verdicts"]
    assert ws.notes["confirm_states"] == [SETTLED]
    assert ws.notes["confirm_errors"] == 0 or \
        ws.notes["confirm_error_types"] == ["IllegalTransition"]


def test_webhook_storm_ledger_verifies(webhook_storm):
    assert webhook_storm.ledger_ok is True
    assert webhook_storm.counter_ledger["error"] is None
    assert webhook_storm.kernel_ledger["error"] is None


# ==========================================================================
# THE CARRY MODEL — the shortcut nine scenarios lean on, measured
# ==========================================================================

def test_the_modelled_carry_track_matches_the_imaged_one():
    """Only `happy_path` images every frame of every carry; it costs ~2.6 s and
    the other scenarios model the track instead. That shortcut is only
    legitimate if the model AGREES with the camera, so this measures it.

    If `_visible_centroid` ever drifts from what a clipped contour actually
    publishes, nine scenarios quietly start testing a different geometry from
    the one the happy path proves — and this test is what fails first.
    """
    def carry(imaged):
        rig = build_rig()
        try:
            counter = e2e.Counter(rig)
            counter.acquire_mat()
            counter.arrive({"PARLE_G": ("PARLE_G", e2e.LANE_X, e2e.ROW_Y[2])})
            before = len(counter.evidence().frames)
            report = counter.carry_out("PARLE_G", imaged=imaged)
            ys = [f[0][1] for f in counter.evidence().frames[before:] if f]
            return report, ys
        finally:
            rig.destroy()

    imaged, imaged_ys = carry(True)
    modelled, modelled_ys = carry(False)

    # The verdicts are what the scenarios actually consume.
    assert imaged.committed is modelled.committed is True
    assert imaged.net_count == modelled.net_count == 1
    assert imaged.exceptions == modelled.exceptions == ()
    assert imaged.frames == modelled.frames

    # And the geometry underneath them agrees to well under a millimetre.
    assert imaged_ys and modelled_ys
    n = min(len(imaged_ys), len(modelled_ys))
    worst = max(abs(a - b) for a, b in zip(imaged_ys[:n], modelled_ys[:n]))
    assert worst < 1.0, f"model drifted {worst:.3f}mm from the camera"

    # The imaged blob stops being published slightly sooner, because a contour
    # running off the buffer eventually falls under the minimum area. The model
    # is allowed to outlive it, but not to see fewer frames than the camera did.
    assert len(modelled_ys) >= len(imaged_ys)


# ==========================================================================
# CROSS-SCENARIO INVARIANTS
# ==========================================================================

ALL_FIXTURES = ("happy", "amber", "revert", "wrong_amount", "tampered",
                "replayed", "offline", "crash", "crash_webhook_lost",
                "crash_before_gateway", "mat_lost", "concurrency",
                "webhook_storm")


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_scenario_verifies_both_hash_chains(name, request):
    """The suite-wide requirement, restated per scenario so a failure names it.

    `ledger.verify` recomputes from genesis with code that never imports
    `Ledger`, so a bug in the writer cannot mask itself here.
    """
    result = request.getfixturevalue(name)
    assert result.ledger_ok is True, (
        f"{name}: counter={result.counter_ledger} kernel={result.kernel_ledger}")
    assert result.counter_ledger["ok"] is True
    assert result.counter_ledger["error"] is None
    assert result.kernel_ledger["ok"] is True
    assert result.kernel_ledger["error"] is None


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_scenario_ends_on_its_expected_paise(name, request):
    result = request.getfixturevalue(name)
    assert result.total_paise == result.expected_paise, name
    assert isinstance(result.total_paise, int)
    assert not isinstance(result.total_paise, bool)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_money_is_authorised_only_in_paid(name, request):
    """R4, across every scenario at once. There is no other state in which
    `authorised_paise` may be set."""
    result = request.getfixturevalue(name)
    if result.final_state == State.PAID.value:
        assert result.money_authorised is True
        assert result.authorised_paise == result.expected_paise
    else:
        assert result.money_authorised is False
        assert result.authorised_paise is None


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_scenario_ever_double_charges(name, request):
    """One settled intent at most, and never more payments than links."""
    result = request.getfixturevalue(name)
    assert result.settled_intents <= 1, name
    assert result.gateway_payments <= 1, name
    assert result.gateway_links <= 1, name
    nonces = [i["nonce"] for i in result.intents]
    assert len(nonces) == len(set(nonces))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_scenario_leaves_an_intent_needing_a_human(name, request):
    """`needs_human` is the kernel's flag for 'the gateway contradicted itself'.
    None of these ten stories should raise it; if one starts to, the
    reconciliation logic has begun guessing."""
    result = request.getfixturevalue(name)
    for intent in result.intents:
        assert intent["needs_human"] is False, (name, intent)
        assert intent["state"] != ESCALATED, (name, intent)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_scenario_leaks_the_webhook_secret(name, request):
    """The secret lives in exactly one object and appears in no ledger, no
    verdict and no scenario result."""
    result = request.getfixturevalue(name)
    blob = json.dumps(list(result.ledger_lines)) + json.dumps(result.as_dict(),
                                                              default=str)
    assert e2e.WEBHOOK_SECRET not in blob, name


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_green_verdict_names_a_green_event(name, request):
    """INVARIANT 2's second leg. Nothing may go green on an event outside the
    set, whatever the signature said."""
    result = request.getfixturevalue(name)
    for adj in result.adjudications:
        if adj.green:
            assert adj.signature_valid is True, name
            assert adj.amount_paise == adj.expected_paise, name
            assert adj.session_reason in (Reason.SETTLED,
                                          Reason.ALREADY_SETTLED), name


def test_the_green_event_sets_agree_across_modules():
    """session re-checks the event class without a secret, so its idea of the
    green set must be a subset of what paisa will ever hand it."""
    from gawaah.webhook import GREEN_EVENTS as WEBHOOK_GREEN
    assert WEBHOOK_GREEN <= GREEN_EVENTS


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_scenario_ever_bills_an_amber_line(name, request):
    """R1, globally: the total is always the sum of the PRICED committed
    lines, and never includes a line the machine abstained on."""
    result = request.getfixturevalue(name)
    for item_id in result.amber_item_ids:
        assert item_id in result.committed_item_ids, name
    priced = len(result.committed_item_ids) - len(result.amber_item_ids)
    if result.final_state == State.PAID.value:
        assert priced >= 1, name


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_scenario_rendered_real_camera_frames(name, request):
    """No scenario short-circuits perception. The cheapest still puts seven
    exposures through ArUco detection, homography and segmentation."""
    result = request.getfixturevalue(name)
    assert result.frames_rendered >= 7, result.name


def test_placement_stability_gate_was_actually_exercised(happy):
    """The goods were watched settling: REST_FRAMES exceeds STABLE_FRAMES, so
    `stable` is a measurement rather than a default."""
    assert e2e.REST_FRAMES > STABLE_FRAMES
    assert all(o.stable for o in happy.observations)


# ==========================================================================
# THE LEDGER IS THE PRODUCT — a few properties of the chain itself
# ==========================================================================

def test_a_single_flipped_byte_breaks_the_chain_detectably():
    """The audit trail is only worth anything if tampering with it is loud.

    This is the control for every `ledger_ok is True` above: it proves the
    verifier can actually fail.
    """
    rig = build_rig()
    try:
        counter = e2e.Counter(rig)
        counter.acquire_mat()
        counter.arrive({"PARLE_G": ("PARLE_G", e2e.LANE_X, e2e.ROW_Y[2])})
        counter.carry_out("PARLE_G")
        path = rig.ledger.path
        ok, n, _, err = ledger_verify(path)
        assert ok is True and err is None and n > 3

        lines = path.read_text(encoding="utf-8").splitlines()
        victim = json.loads(lines[2])
        victim["total_paise"] = int(victim.get("total_paise", 0)) + 1
        lines[2] = json.dumps(victim, sort_keys=True, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, checked, _, err = ledger_verify(path)
        assert ok is False
        assert err is not None
        assert "line 3" in err
        assert checked == 2, "the break must be reported where it happened"
    finally:
        rig.destroy()


def test_ledger_lines_carry_a_reason_code_each(happy):
    """R7. A state change with no named reason is a state change nobody can
    audit."""
    session_lines = [r for r in happy.ledger_lines if r.get("module") == "session"]
    assert session_lines
    for line in session_lines:
        assert line.get("reason"), line
        assert line.get("from") is not None
        assert line.get("to") is not None
        assert isinstance(line.get("total_paise"), int)


def test_the_kernel_chain_survives_a_restart(crash):
    """Two Kernel objects wrote the same file across a simulated process death
    and the chain still recomputes from genesis."""
    assert crash.kernel_ledger["ok"] is True
    assert crash.kernel_ledger["lines"] >= 4
    assert crash.kernel_ledger["head"] != "0" * 64
