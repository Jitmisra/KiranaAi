"""Tests for S4e — gawaah.paisa, the money service.

The three properties these tests exist to pin, in order of how much money they
are worth:

  1. A compromised phone cannot move a rupee. Every claim it makes — which items
     crossed the exit line, what they cost, what the total is, even the
     homography that defines the plane — is recomputed server-side, and a
     disagreement is a 409 with nothing minted (INVARIANT 5).
  2. GREEN is the four-part predicate over the raw bytes and nothing else, and
     replaying a delivery cannot pay twice (INVARIANT 2).
  3. The secrets stay in the process. They are not in a response, not in the
     ledger, not in a repr, not in a traceback (INVARIANT 5).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

from gawaah.clock import VirtualClock
from gawaah.kernel import CALLING, NEW, SETTLED, Kernel
from gawaah.ledger import Ledger, verify
from gawaah.paisa import (
    DictPriceBook,
    IntentRequest,
    PaisaConfig,
    PaisaConfigError,
    PaisaService,
    build_service,
    check_homography,
    create_app,
    expected_marker_points,
    replay_crossings,
    rerun_geometry,
)
from gawaah.rzp_sim import RazorpaySim
from gawaah.takhti import PlaneEngine, marker_centres_mm, mm_to_buffer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Distinctive so a substring search for them is meaningful.
WEBHOOK_SECRET = "whsec_LEAKCANARY_webhook_c0ffee_do_not_print"
KEY_SECRET = "rzpsecret_LEAKCANARY_key_deadbeef_do_not_print"

SKU_RICE = "trk-rice-01"
SKU_DAL = "trk-dal-02"
SKU_SOAP = "trk-soap-03"
PRICES = {SKU_RICE: 21450, SKU_DAL: 9900, SKU_SOAP: 4500}

# The exit line sits at y = MAT_H_MM - 18 = 402 mm with a 1 mm dead band, so
# y < 401 is definitely inside and y > 403 is definitely past it. With
# min_crossing_frames=3 the history window is 4, hence 4 inside frames then 3
# outside frames is the shortest path that commits exactly one crossing.
IN_YS = (390.0, 392.0, 394.0, 396.0)
OUT_YS = (406.0, 409.0, 412.0)


def out_path(x_mm: float = 100.0) -> list[list[float]]:
    """A centroid track that crosses the exit line and commits."""
    return [[x_mm, y] for y in IN_YS] + [[x_mm, y] for y in OUT_YS]


def in_path(x_mm: float = 150.0) -> list[list[float]]:
    """A centroid track that never leaves the shopkeeper's side."""
    return [[x_mm, y] for y in IN_YS] * 2


def stalled_path(x_mm: float = 200.0) -> list[list[float]]:
    """Crosses the line but the track dies before the debounce commits it."""
    return [[x_mm, y] for y in IN_YS] + [[x_mm, 406.0]]


# --------------------------------------------------------------- geometry


def identity_h() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def identity_corners() -> list[list[float]]:
    """Under H = I the frame IS the buffer, so the printed centres pass exactly."""
    pts, _ = expected_marker_points()
    assert pts is not None
    return [[float(x), float(y)] for x, y in pts]


def geometry(
    crossings: list[dict],
    *,
    h: list[list[float]] | None = None,
    corners: list[list[float]] | None = None,
    untracked: list[list[list[float]]] | None = None,
) -> dict:
    return {
        "H": h if h is not None else identity_h(),
        "corners": corners if corners is not None else identity_corners(),
        "crossings": crossings,
        "untracked": untracked or [],
        "min_crossing_frames": 3,
    }


def crossing(item_id: str, track_id: int, path: list[list[float]], committed: bool,
             **kw) -> dict:
    return {
        "item_id": item_id,
        "track_id": track_id,
        "path_mm": path,
        "committed": committed,
        **kw,
    }


def two_item_body(session_id: str = "sess-happy") -> dict:
    """Rice and dal both cross; soap stays on the mat. Total = 214.50 + 99.00."""
    return {
        "session_id": session_id,
        "amount_paise": PRICES[SKU_RICE] + PRICES[SKU_DAL],
        "geometry": geometry(
            [
                crossing(SKU_RICE, 1, out_path(80.0), True, name="rice 5kg"),
                crossing(SKU_DAL, 2, out_path(160.0), True, name="dal 1kg"),
                crossing(SKU_SOAP, 3, in_path(240.0), False, name="soap"),
            ]
        ),
    }


# --------------------------------------------------------------- fixtures


@pytest.fixture
def rig(tmp_path):
    """A whole counter: clock, ledger, kernel, simulator, service, HTTP client."""
    clock = VirtualClock()
    ledger_path = os.path.join(str(tmp_path), "audit.jsonl")
    ledger = Ledger(ledger_path)
    kernel = Kernel(os.path.join(str(tmp_path), "kernel.db"), clock, ledger)
    cfg = PaisaConfig(
        mode="sim",
        key_id="rzp_test_LEAKCANARY",
        key_secret=KEY_SECRET,
        webhook_secret=WEBHOOK_SECRET,
        seed=7,
    )
    sim = RazorpaySim(webhook_secret=cfg.effective_webhook_secret, clock=clock, seed=7)
    svc = PaisaService(
        clock=clock,
        ledger=ledger,
        kernel=kernel,
        gateway=sim,
        config=cfg,
        price_book=DictPriceBook(PRICES),
    )
    client = TestClient(create_app(svc))

    class Rig:
        pass

    r = Rig()
    r.clock, r.ledger, r.ledger_path = clock, ledger, ledger_path
    r.kernel, r.sim, r.svc, r.client, r.cfg = kernel, sim, svc, client, cfg
    return r


def post_delivery(rig, delivery, *, body: bytes | None = None,
                  signature: str | None = None):
    headers = dict(delivery.headers)
    if signature is not None:
        headers["X-Razorpay-Signature"] = signature
    return rig.client.post(
        "/webhook",
        content=body if body is not None else delivery.body,
        headers=headers,
    )


def pay(rig, short_url_response):
    """Customer pays the minted link; returns the deliveries Razorpay would send."""
    return rig.sim.pay_link(short_url_response["payment_link_id"]).deliveries


# =====================================================================
# 1. health, config, and the lint that guards INVARIANT 1
# =====================================================================


def test_health_reports_booleans_not_secrets(rig):
    h = rig.client.get("/health").json()
    assert h["ok"] is True
    assert h["mode"] == "sim"
    assert h["key_secret_configured"] is True
    assert h["webhook_secret_configured"] is True
    assert h["intents"] == 0 and h["sessions"] == 0
    assert h["price_book_entries"] == 3
    # the values themselves are nowhere in the document
    blob = json.dumps(h)
    assert WEBHOOK_SECRET not in blob and KEY_SECRET not in blob


def test_no_float_lint_covers_paisa():
    """INVARIANT 1: paisa is on the money path, so it must survive the AST lint."""
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "lint_no_float.py")],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Assert the INTENT (paisa is linted), not the message text. The previous
    # form pinned the literal string "5 money-path modules clean" and broke the
    # moment webhook.py was added to the strict list -- a brittle test that
    # punished widening the lint's coverage, which is backwards.
    from tools.lint_no_float import MONEY_PATH
    assert "gawaah/paisa.py" in MONEY_PATH
    assert "no-float lint: PASS" in proc.stdout
    assert os.path.exists(os.path.join(REPO, "gawaah", "paisa.py"))


def test_live_mode_refuses_to_start_without_secrets():
    with pytest.raises(PaisaConfigError) as ei:
        PaisaConfig.from_env({"RZP_MODE": "live"})
    msg = str(ei.value)
    assert "RAZORPAY_KEY_SECRET" in msg and "RAZORPAY_WEBHOOK_SECRET" in msg

    with pytest.raises(PaisaConfigError):
        PaisaConfig.from_env(
            {"RZP_MODE": "live", "RAZORPAY_KEY_SECRET": KEY_SECRET}
        )

    cfg = PaisaConfig.from_env(
        {
            "RZP_MODE": "live",
            "RAZORPAY_KEY_SECRET": KEY_SECRET,
            "RAZORPAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
        }
    )
    assert cfg.mode == "live"
    assert cfg.effective_webhook_secret == WEBHOOK_SECRET
    # and it still refuses to say what they are
    assert KEY_SECRET not in repr(cfg) and WEBHOOK_SECRET not in repr(cfg)
    assert KEY_SECRET not in str(cfg) and WEBHOOK_SECRET not in str(cfg)


def test_sim_mode_never_verifies_against_an_empty_secret():
    """An empty secret makes every signature forgeable; sim gets a placeholder."""
    cfg = PaisaConfig.from_env({"RZP_MODE": "sim"})
    assert cfg.webhook_secret_configured is False
    assert cfg.effective_webhook_secret != ""


# =====================================================================
# 2. the server-side re-run, as a pure function
# =====================================================================


def test_replay_crossings_counts_only_a_committed_crossing():
    r = replay_crossings(
        [
            IntentRequest(
                session_id="s",
                amount_paise=1,
                geometry=geometry(
                    [
                        crossing("a", 1, out_path(80.0), True),
                        crossing("b", 2, in_path(160.0), False),
                    ]
                ),
            ).geometry.crossings[i]
            for i in (0, 1)
        ]
    )
    assert r.committed == (1,)
    assert r.frames == 8  # the longer of the two tracks
    assert r.uncounted == 0


def test_replay_flags_a_track_that_died_mid_crossing():
    req = IntentRequest(
        session_id="s",
        amount_paise=1,
        geometry=geometry([crossing("a", 1, stalled_path(), True)]),
    )
    r = replay_crossings(req.geometry.crossings)
    assert r.committed == ()
    assert r.never_counted == 1
    assert "never_counted" in r.exceptions[0] or "detected_but_never_counted" in r.exceptions[0]


def test_homography_check_accepts_a_real_detection_and_rejects_a_nudge():
    """The corner check runs against a homography a real detector produced."""
    from tests.test_plane import synth_frame

    frame, _ = synth_frame(px_per_mm=4.0, tilt=(3, 2), size=(960, 1280), seed=1)
    lock = PlaneEngine().detect(frame)
    assert lock.locked, lock.reason

    inv = np.linalg.inv(lock.H)
    corners = []
    for bx, by in mm_to_buffer(marker_centres_mm()):
        v = inv @ np.array([bx, by, 1.0])
        corners.append([float(v[0] / v[2]), float(v[1] / v[2])])
    h = [[float(x) for x in row] for row in lock.H.tolist()]

    ok, detail, slack, note = check_homography(h, corners)
    assert ok, detail
    assert slack is not None and slack >= 0
    assert "marker centres" in note

    nudged = [list(c) for c in corners]
    nudged[0][0] += 40.0
    ok2, detail2, slack2, _ = check_homography(h, nudged)
    assert ok2 is False
    assert slack2 is not None and slack2 < 0
    assert "printed marker centres" in detail2


def test_homography_check_bounds_its_own_claim():
    """HONEST LIMIT, pinned so nobody later mistakes this check for more.

    `cv2.findHomography` on exactly four correspondences fits those four points
    EXACTLY — there is no redundancy left, so the reprojection residual of a
    genuine detection is float noise (measured: 2.37e-05 px worst case over the
    ten synthetic detections below) regardless of tilt or sensor noise. The
    residual therefore measures nothing about the detection.

    What follows is that the corner check can only prove the submitted H and the
    submitted corners are mutually consistent with the PRINTED mat. It cannot
    prove the corners were ever observed: a phone that fabricates a plausible
    plane and then reports corners consistent with it passes. Nothing
    server-side can do better while INVARIANT 4 keeps the pixels on the phone.
    The money is protected by the crossing re-run and the price book, not by
    this check, and this test exists to keep that honest.
    """
    from tests.test_plane import synth_frame

    residuals = []
    for tilt in [(0, 0), (2, 1), (3, 2), (5, 0), (6, 4)]:
        for noise in [0.0, 8.0]:
            frame, _ = synth_frame(
                px_per_mm=4.0, tilt=tilt, size=(960, 1280), noise=noise, seed=3
            )
            lock = PlaneEngine().detect(frame)
            assert lock.locked, (tilt, noise, lock.reason)
            residuals.append(lock.reproj_rmse_px)
    # 4 points, 8 unknowns: the fit is exact, so this is float noise, not error
    assert max(residuals) < 1e-4, residuals

    # a fabricated but SELF-CONSISTENT plane passes, and that is not a bug
    fake_h = [[2.0, 0.0, 5.0], [0.0, 2.0, -3.0], [0.0, 0.0, 1.0]]
    pts, _ = expected_marker_points()
    fake_corners = [[(x - 5.0) / 2.0, (y + 3.0) / 2.0] for x, y in pts]
    ok, detail, slack, _ = check_homography(fake_h, fake_corners)
    assert ok is True, detail
    assert slack is not None and slack >= 0


def test_homography_check_rejects_a_singular_matrix():
    ok, detail, _, _ = check_homography(
        [[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [0.0, 0.0, 1.0]], identity_corners()
    )
    assert ok is False and "singular" in detail


def test_rerun_geometry_agrees_with_an_honest_phone():
    req = IntentRequest(**two_item_body())
    v = rerun_geometry(req, DictPriceBook(PRICES))
    assert v.agrees is True
    assert set(v.server_committed) == {SKU_RICE, SKU_DAL}
    assert v.server_total_paise == PRICES[SKU_RICE] + PRICES[SKU_DAL]
    assert v.amber_items == ()
    assert v.uncounted == 0


def test_rerun_geometry_marks_an_unpriceable_item_amber_not_a_guess():
    """INVARIANT 7: unknown is amber and excluded, never a made-up price."""
    body = two_item_body()
    v = rerun_geometry(
        IntentRequest(**body), DictPriceBook({SKU_RICE: PRICES[SKU_RICE]})
    )
    # dal crossed but cannot be priced -> amber, excluded, so the total no
    # longer matches what the phone asked for
    assert v.agrees is False
    assert v.reason == "amount_disagreement"
    assert v.amber_items == (SKU_DAL,)
    assert v.server_total_paise == PRICES[SKU_RICE]


# =====================================================================
# 3. the happy path
# =====================================================================


def test_happy_path_intent_pay_webhook_session_paid(rig):
    body = two_item_body()
    expected = PRICES[SKU_RICE] + PRICES[SKU_DAL]

    r = rig.client.post("/intent", json=body)
    assert r.status_code == 200, r.text
    minted = r.json()
    assert minted["amount_paise"] == expected
    assert minted["amount_rupees"] == "313.50"
    assert minted["short_url"].startswith("https://rzp.io/i/")
    assert minted["state"] == CALLING
    assert minted["session_state"] == "AWAITING_SETTLEMENT"
    assert minted["geometry"]["agrees"] is True
    assert sorted(minted["priced_items"]) == sorted([SKU_RICE, SKU_DAL])

    mid = rig.client.get("/session/sess-happy").json()
    assert mid["state"] == "AWAITING_SETTLEMENT"
    assert mid["total_paise"] == expected
    assert mid["paid"] is False
    assert mid["money_authorised"] is False

    deliveries = pay(rig, minted)
    assert len(deliveries) == 1 and deliveries[0].event == "payment_link.paid"

    w = post_delivery(rig, deliveries[0])
    assert w.status_code == 200, w.text
    wb = w.json()
    assert wb["green"] is True
    assert wb["reason"] == "green"
    assert wb["amount_paise"] == expected == wb["expected_paise"]
    assert wb["session_state"] == "PAID"
    assert wb["settled_nonce"] == minted["nonce"]
    assert wb["payment_id"].startswith("pay_")

    view = rig.client.get("/session/sess-happy").json()
    assert view["state"] == "PAID"
    assert view["paid"] is True
    assert view["money_authorised"] is True
    assert view["total_paise"] == expected
    assert view["total_rupees"] == "313.50"
    assert view["authorised_paise"] == expected
    assert view["intents"][0]["state"] == SETTLED
    assert view["intents"][0]["payment_id"] == wb["payment_id"]
    # soap never crossed, so it is not a line at all
    assert {li["item_id"] for li in view["line_items"]} == {SKU_RICE, SKU_DAL}

    ok, n, head, err = verify(rig.ledger_path)
    assert ok, err
    assert n == rig.ledger.count > 0


def test_repeat_intent_is_idempotent_and_mints_one_link(rig):
    body = two_item_body()
    a = rig.client.post("/intent", json=body).json()
    b = rig.client.post("/intent", json=body).json()
    assert a["nonce"] == b["nonce"]
    assert a["short_url"] == b["short_url"]
    assert a["replayed"] is False and b["replayed"] is True
    assert rig.kernel.count() == 1
    assert rig.sim.fetch_payment_links()["count"] == 1


def test_an_amber_item_crosses_into_the_session_but_never_into_the_total(rig):
    """INVARIANT 7 end to end: the dal is billed, the unpriceable rice is not."""
    rig.svc.price_book = DictPriceBook({SKU_DAL: PRICES[SKU_DAL]})
    body = two_item_body("sess-amber")
    body["amount_paise"] = PRICES[SKU_DAL]  # the amber line is excluded

    r = rig.client.post("/intent", json=body)
    assert r.status_code == 200, r.text
    minted = r.json()
    assert minted["amber_items"] == [SKU_RICE]
    assert minted["priced_items"] == [SKU_DAL]
    assert minted["amount_paise"] == PRICES[SKU_DAL]

    view = rig.client.get("/session/sess-amber").json()
    assert view["total_paise"] == PRICES[SKU_DAL]
    assert view["amber_count"] == 1
    assert view["committed"] == 2  # both lines exist; only one is money
    lines = {li["item_id"]: li for li in view["line_items"]}
    assert lines[SKU_RICE]["amber"] is True
    assert lines[SKU_RICE]["price_paise"] is None
    assert lines[SKU_RICE]["counts"] is False
    assert lines[SKU_DAL]["counts"] is True

    d = pay(rig, minted)[0]
    assert post_delivery(rig, d).json()["green"] is True
    paid = rig.client.get("/session/sess-amber").json()
    assert paid["state"] == "PAID"
    assert paid["authorised_paise"] == PRICES[SKU_DAL]


def test_unknown_session_is_a_404(rig):
    assert rig.client.get("/session/never-existed").status_code == 404


# =====================================================================
# 4. INVARIANT 5 — a lying phone moves nothing
# =====================================================================


def _assert_nothing_minted(rig):
    assert rig.kernel.count() == 0, "an intent row was written"
    assert rig.sim.fetch_payment_links()["count"] == 0, "a payment link was minted"
    assert rig.sim.deliveries == ()


def test_geometry_disagreement_claimed_crossing_returns_409_and_mints_nothing(rig):
    """The phone says the dal crossed. The server re-runs the track: it did not."""
    body = {
        "session_id": "sess-liar",
        "amount_paise": PRICES[SKU_RICE] + PRICES[SKU_DAL],
        "geometry": geometry(
            [
                crossing(SKU_RICE, 1, out_path(80.0), True),
                crossing(SKU_DAL, 2, in_path(160.0), True),  # <- the lie
            ]
        ),
    }
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409, r.text
    d = r.json()
    assert d["error"] == "crossing_set_mismatch"
    assert d["minted"] is False
    assert d["geometry"]["server_committed"] == [SKU_RICE]
    assert sorted(d["geometry"]["client_committed"]) == sorted([SKU_RICE, SKU_DAL])
    assert SKU_DAL in d["detail"]
    _assert_nothing_minted(rig)
    assert rig.svc.has_session("sess-liar") is False

    # and the refusal is in the audit log, with a reason code
    refusals = [
        rec
        for rec in rig.ledger.read()
        if rec.get("module") == "paisa" and rec.get("event") == "intent.refused"
    ]
    assert len(refusals) == 1
    assert refusals[0]["reason"] == "crossing_set_mismatch"
    assert refusals[0]["minted"] is False


def test_geometry_disagreement_undeclared_crossing_also_refuses(rig):
    """Hiding a crossing is the same lie in the other direction."""
    body = {
        "session_id": "sess-hider",
        "amount_paise": PRICES[SKU_RICE],
        "geometry": geometry(
            [
                crossing(SKU_RICE, 1, out_path(80.0), True),
                crossing(SKU_DAL, 2, out_path(160.0), False),  # crossed, undeclared
            ]
        ),
    }
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409
    d = r.json()
    assert d["error"] == "crossing_set_mismatch"
    assert d["geometry"]["server_committed"] == [SKU_RICE, SKU_DAL]
    _assert_nothing_minted(rig)


def test_amount_disagreement_refuses_even_when_the_crossings_agree(rig):
    body = two_item_body("sess-cheap")
    body["amount_paise"] = 100  # one rupee for three hundred rupees of goods
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409
    assert r.json()["error"] == "amount_disagreement"
    assert r.json()["geometry"]["server_total_paise"] == (
        PRICES[SKU_RICE] + PRICES[SKU_DAL]
    )
    _assert_nothing_minted(rig)


def test_price_disagreement_refuses_a_phone_that_writes_to_the_price_book(rig):
    body = {
        "session_id": "sess-discount",
        "amount_paise": 100 + PRICES[SKU_DAL],
        "geometry": geometry(
            [
                crossing(SKU_RICE, 1, out_path(80.0), True, price_paise=100),
                crossing(SKU_DAL, 2, out_path(160.0), True),
            ]
        ),
    }
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409
    d = r.json()
    assert d["error"] == "price_disagreement"
    assert str(PRICES[SKU_RICE]) in d["detail"]
    _assert_nothing_minted(rig)


def test_uncounted_crossing_refuses_rather_than_billing_an_incomplete_total(rig):
    """A centroid past the line with no tracker id is a sale nobody can name."""
    body = {
        "session_id": "sess-untracked",
        "amount_paise": PRICES[SKU_RICE],
        "geometry": geometry(
            [crossing(SKU_RICE, 1, out_path(80.0), True)],
            untracked=[[], [], [], [], [[250.0, 410.0]], [], []],
        ),
    }
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409
    d = r.json()
    assert d["error"] == "uncounted_crossing"
    assert d["geometry"]["uncounted"] == 1
    _assert_nothing_minted(rig)


def test_tampered_homography_refuses(rig):
    body = two_item_body("sess-badplane")
    body["geometry"]["corners"][0] = [
        body["geometry"]["corners"][0][0] + 60.0,
        body["geometry"]["corners"][0][1],
    ]
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409
    assert r.json()["error"] == "homography_rejected"
    assert r.json()["geometry"]["homography_ok"] is False
    _assert_nothing_minted(rig)


def test_zero_total_when_everything_is_amber(rig):
    rig.svc.price_book = DictPriceBook({})
    body = two_item_body("sess-allamber")
    r = rig.client.post("/intent", json=body)
    assert r.status_code == 409
    assert r.json()["error"] == "zero_total"
    _assert_nothing_minted(rig)


def test_float_amount_is_a_422_and_never_becomes_money(rig):
    """INVARIANT 1 at the wire boundary: 21450.0 is not 21450 paise."""
    for bad in (21450.0, "21450", None, True):
        body = two_item_body("sess-float")
        body["amount_paise"] = bad
        r = rig.client.post("/intent", json=body)
        assert r.status_code == 422, f"{bad!r} was accepted"
    _assert_nothing_minted(rig)


def test_unknown_geometry_field_is_refused(rig):
    body = two_item_body("sess-extra")
    body["geometry"]["confidence"] = 0.99
    assert rig.client.post("/intent", json=body).status_code == 422
    _assert_nothing_minted(rig)


# =====================================================================
# 5. INVARIANT 2 — the webhook
# =====================================================================


def test_tampered_webhook_body_is_rejected_and_nothing_is_settled(rig):
    minted = rig.client.post("/intent", json=two_item_body("sess-tamper")).json()
    d = pay(rig, minted)[0]

    # flip the amount inside the signed document; the signature no longer covers it
    tampered = d.body.replace(b'"amount":31350', b'"amount":00001')
    assert tampered != d.body

    r = post_delivery(rig, d, body=tampered)
    assert r.status_code == 400
    rb = r.json()
    assert rb["green"] is False
    assert rb["reason"] == "bad_signature"
    assert rb["signature_valid"] is False
    assert rb["session_id"] is None  # nothing was parsed out of it

    view = rig.client.get("/session/sess-tamper").json()
    assert view["state"] == "AWAITING_SETTLEMENT"
    assert view["paid"] is False
    assert view["intents"][0]["state"] == CALLING

    # the honest body still pays, so the rejection was of the tampering only
    assert post_delivery(rig, d).json()["green"] is True
    assert rig.client.get("/session/sess-tamper").json()["state"] == "PAID"


def test_forged_signature_is_rejected(rig):
    minted = rig.client.post("/intent", json=two_item_body("sess-forge")).json()
    d = pay(rig, minted)[0]
    r = post_delivery(rig, d, signature="0" * 64)
    assert r.status_code == 400 and r.json()["reason"] == "bad_signature"
    assert rig.client.get("/session/sess-forge").json()["paid"] is False


def test_missing_signature_header_is_rejected(rig):
    minted = rig.client.post("/intent", json=two_item_body("sess-nosig")).json()
    d = pay(rig, minted)[0]
    r = rig.client.post("/webhook", content=d.body)
    assert r.status_code == 400 and r.json()["reason"] == "bad_signature"


def test_signature_is_checked_before_the_body_is_parsed(rig):
    """A body that would crash a parser must die at the signature gate.

    If anything parsed above `verify_signature`, undecodable bytes would raise
    before the HMAC ever ran and the reason code would not be `bad_signature`.
    """
    r = rig.client.post(
        "/webhook",
        content=b"\xff\xfe not json at all {{{",
        headers={"X-Razorpay-Signature": "deadbeef"},
    )
    assert r.status_code == 400
    assert r.json()["reason"] == "bad_signature"
    assert r.json()["session_id"] is None


def test_webhook_for_a_session_this_counter_never_minted_is_not_green(rig):
    """A validly-signed delivery is still not money without an open intent."""
    other = RazorpaySim(webhook_secret=WEBHOOK_SECRET, clock=rig.clock, seed=99)
    link = other.create_payment_link(
        amount_paise=50000, notes={"session_id": "sess-elsewhere"}
    )
    d = other.pay_link(link["id"]).deliveries[0]
    r = post_delivery(rig, d)
    assert r.status_code == 200
    assert r.json()["green"] is False
    assert r.json()["reason"] == "unknown_session"
    assert rig.kernel.count() == 0


def test_wrong_amount_webhook_never_pays_and_holds_red(rig):
    minted = rig.client.post("/intent", json=two_item_body("sess-wrong")).json()
    rig.sim.set_mode("wrong_amount", wrong_amount_delta_paise=100)
    d = pay(rig, minted)[0]
    r = post_delivery(rig, d)
    assert r.status_code == 200
    rb = r.json()
    assert rb["green"] is False
    assert rb["reason"] == "amount_mismatch"
    assert rb["severity"] == "RED"
    assert rb["amount_paise"] == rb["expected_paise"] + 100

    view = rig.client.get("/session/sess-wrong").json()
    assert view["state"] == "AMOUNT_MISMATCH"
    assert view["paid"] is False
    assert view["money_authorised"] is False
    assert view["intents"][0]["state"] == CALLING  # never settled


def test_replaying_a_webhook_twice_is_idempotent(rig):
    expected = PRICES[SKU_RICE] + PRICES[SKU_DAL]
    minted = rig.client.post("/intent", json=two_item_body("sess-replay")).json()
    d = pay(rig, minted)[0]

    first = post_delivery(rig, d).json()
    assert first["green"] is True and first["session_state"] == "PAID"
    after_first = rig.client.get("/session/sess-replay").json()
    lines_after_first = rig.ledger.count

    second = post_delivery(rig, d).json()
    assert second["green"] is False
    assert second["reason"] == "replay"
    assert second["settled_nonce"] is None

    after_second = rig.client.get("/session/sess-replay").json()
    assert after_second["state"] == "PAID"
    assert after_second["total_paise"] == expected == after_first["total_paise"]
    assert after_second["authorised_paise"] == after_first["authorised_paise"]
    assert after_second["intents"] == after_first["intents"]  # same payment_id
    assert rig.kernel.get(minted["nonce"]).state == SETTLED

    # a replay writes audit lines but never a second settlement
    settled = [
        rec for rec in rig.ledger.read() if rec.get("event") == "intent.settled"
    ]
    assert len(settled) == 1
    assert rig.ledger.count > lines_after_first  # the replay was logged, not silent

    third = post_delivery(rig, d).json()
    assert third["reason"] == "replay"
    assert rig.client.get("/session/sess-replay").json()["state"] == "PAID"

    ok, _, _, err = verify(rig.ledger_path)
    assert ok, err


def test_a_second_distinct_event_cannot_settle_the_same_intent_twice(rig):
    """payment.captured then payment_link.paid: one settles, one is inert."""
    minted = rig.client.post("/intent", json=two_item_body("sess-two")).json()
    deliveries = rig.sim.pay_link(
        minted["payment_link_id"], emit_captured=True
    ).deliveries
    assert [d.event for d in deliveries] == ["payment.captured", "payment_link.paid"]

    a = post_delivery(rig, deliveries[0]).json()
    assert a["green"] is True and a["session_state"] == "PAID"

    b = post_delivery(rig, deliveries[1]).json()
    assert b["green"] is False
    # the intent is SETTLED, so the adapter no longer offers it as OPEN
    assert b["reason"] == "unknown_session"
    assert b["settled_nonce"] is None

    view = rig.client.get("/session/sess-two").json()
    assert view["state"] == "PAID"
    assert view["authorised_paise"] == PRICES[SKU_RICE] + PRICES[SKU_DAL]
    assert view["intents"][0]["payment_id"] == a["payment_id"]
    settled = [r for r in rig.ledger.read() if r.get("event") == "intent.settled"]
    assert len(settled) == 1


# =====================================================================
# 6. secrets
# =====================================================================


def test_secrets_never_appear_in_any_response_or_in_the_ledger(rig):
    """Drive every endpoint, then grep everything this process emitted."""
    seen: list[str] = [repr(rig.svc), repr(rig.cfg), str(rig.cfg), repr(rig.sim)]

    seen.append(rig.client.get("/health").text)

    minted = rig.client.post("/intent", json=two_item_body("sess-secret"))
    seen.append(minted.text)
    link = minted.json()

    seen.append(rig.client.post("/intent", json=two_item_body("sess-secret")).text)

    bad = two_item_body("sess-secret-2")
    bad["geometry"]["crossings"][1]["path_mm"] = in_path(160.0)
    seen.append(rig.client.post("/intent", json=bad).text)

    d = pay(rig, link)[0]
    seen.append(post_delivery(rig, d, signature="ff" * 32).text)
    seen.append(post_delivery(rig, d).text)
    seen.append(post_delivery(rig, d).text)
    seen.append(rig.client.get("/session/sess-secret").text)
    seen.append(rig.client.get("/session/nope").text)
    seen.append(rig.client.get("/health").text)

    # every ledger line ever written by this counter
    with open(rig.ledger_path, encoding="utf-8") as f:
        seen.append(f.read())

    for secret in (WEBHOOK_SECRET, KEY_SECRET):
        for blob in seen:
            assert secret not in blob, f"secret leaked into: {blob[:400]}"

    # the ledger is not empty and it really did record the money
    assert any(
        json.loads(line).get("event") == "intent.settled"
        for line in open(rig.ledger_path, encoding="utf-8")
        if line.strip()
    )


def test_a_gateway_traceback_cannot_leak_the_secret(rig):
    """An exploding gateway parks the intent; the error text names no secret."""

    class Exploding:
        def create_payment_link(self, **kw):
            raise RuntimeError("upstream said no")

    rig.svc.gateway = Exploding()
    r = rig.client.post("/intent", json=two_item_body("sess-boom"))
    assert r.status_code == 502
    assert r.json()["error"] == "gateway_error"
    assert WEBHOOK_SECRET not in r.text and KEY_SECRET not in r.text
    # INVARIANT: an indeterminate call is parked, never blind-retried
    nonce = rig.kernel.all_intents()[0].nonce
    assert rig.kernel.get(nonce).state == "INDETERMINATE"


# =====================================================================
# 7. assembly
# =====================================================================


def test_build_service_from_a_directory_round_trips(tmp_path):
    """The wiring `create_app` uses by default, driven end to end."""
    data = os.path.join(str(tmp_path), "counter")
    svc = build_service(
        data_dir=data,
        clock=VirtualClock(),
        config=PaisaConfig(
            mode="sim", key_secret=KEY_SECRET, webhook_secret=WEBHOOK_SECRET, seed=3
        ),
        price_book=DictPriceBook(PRICES),
    )
    client = TestClient(create_app(svc))
    minted = client.post("/intent", json=two_item_body("sess-built")).json()
    d = svc.gateway.pay_link(minted["payment_link_id"]).deliveries[0]
    r = client.post("/webhook", content=d.body, headers=dict(d.headers))
    assert r.json()["green"] is True
    assert client.get("/session/sess-built").json()["state"] == "PAID"

    ok, n, _, err = verify(os.path.join(data, "audit.jsonl"))
    assert ok, err
    assert n > 0
    assert os.path.exists(os.path.join(data, "kernel.db"))
