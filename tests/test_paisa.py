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
from gawaah import paisa
from gawaah.money import MoneyError
from gawaah.paisa import (
    PII_DROPPED_KEY,
    PII_FIELDS,
    REFUSAL_CODES,
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
    strip_pii,
)
from gawaah.rzp_sim import (
    SHORT_URL_PREFIX,
    SIM_CONTACT,
    SIM_EMAIL,
    SIM_VPA,
    RazorpaySim,
    serialize_body,
    sign_body,
)
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
    assert minted["short_url"].startswith(SHORT_URL_PREFIX)
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


# =====================================================================
# 8. GAP — INVARIANT 1 AT THE PRICE-BOOK BOUNDARY
#
# Every other float gate in this system is pinned: the wire model is StrictInt,
# `money.paise` rejects floats, the AST lint bans them from the file. The price
# book was the one door with no test behind it. Deleting the `paise()` call in
# `DictPriceBook.__init__` turns a `214.507` rupee-ish price into `int(214.507)
# == 214` paise — a silent 99% discount that nothing in the suite noticed.
# These tests are that missing lock.
# =====================================================================


@pytest.mark.parametrize(
    "bad", [214.507, 214.5, 0.0, "21450", "214.50", True, False, None, [21450]]
)
def test_the_price_book_refuses_a_non_integer_price_at_the_door(bad):
    """INVARIANT 1: a price that is not integer paise never enters the book."""
    with pytest.raises(MoneyError):
        DictPriceBook({SKU_RICE: bad})
    book = DictPriceBook({SKU_RICE: PRICES[SKU_RICE]})
    with pytest.raises(MoneyError):
        book.set_price(SKU_RICE, bad)
    # and the rejection left the book exactly as it was
    assert book.price_paise(SKU_RICE) == PRICES[SKU_RICE]


def test_a_truncating_price_book_would_be_caught_not_silently_billed():
    """The specific corruption the `paise()` call prevents, named out loud.

    214.507 is what a rupees->paise conversion looks like when someone did the
    arithmetic in floats. `int()` of it is 214 paise — two rupees fourteen for a
    two-hundred-fourteen rupee bag of rice. The book must refuse, not round.
    """
    with pytest.raises(MoneyError) as ei:
        DictPriceBook({SKU_RICE: 214.507})
    assert "float is not money" in str(ei.value)
    assert int(214.507) == 214  # what we would have billed instead


def test_a_price_book_that_hands_back_a_float_is_refused_not_truncated(rig):
    """The Protocol is injectable, so the boundary must hold for any impl.

    `PriceBook` is a Protocol: a store could plug in a book backed by a CSV, a
    spreadsheet export, or an API that returns JSON numbers. If such a book
    hands `rerun_geometry` a float, the total must be refused with a named
    code — not truncated into a discount, and not raised as a 500 that tells an
    operator nothing.
    """

    class FloatyBook:
        def price_paise(self, item_id):
            return {SKU_RICE: 21450.0, SKU_DAL: 9900}.get(item_id)

    v = rerun_geometry(IntentRequest(**two_item_body()), FloatyBook())
    assert v.agrees is False
    assert v.reason == "bad_price_book"
    assert v.server_total_paise == 0
    assert SKU_RICE in v.detail

    rig.svc.price_book = FloatyBook()
    r = rig.client.post("/intent", json=two_item_body("sess-floaty"))
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "bad_price_book"
    _assert_nothing_minted(rig)
    assert "bad_price_book" in REFUSAL_CODES


def test_a_float_price_never_reaches_the_ledger_or_a_total(rig):
    """End to end: no float-derived number is ever written as money."""

    class FloatyBook:
        def price_paise(self, item_id):
            return 21450.0 if item_id == SKU_RICE else PRICES.get(item_id)

    rig.svc.price_book = FloatyBook()
    rig.client.post("/intent", json=two_item_body("sess-floatled"))
    for rec in rig.ledger.read():
        for key, value in rec.items():
            if any(k in key for k in ("paise", "amount", "price", "total")):
                assert not isinstance(value, float), (key, value)
    assert rig.kernel.count() == 0


# =====================================================================
# 9. GAP — CUSTOMER PII
#
# `rzp_sim` puts a real-shaped vpa, email, contact, rrn and card on every
# payment it emits, and documents that paisa drops them on receipt (PRD 9).
# Nothing asserted it. These tests do, from both ends: the webhook path (a
# signed body full of PII that must be fully processed and fully forgotten) and
# the gateway path (a client whose response carries a customer object, which
# must be scrubbed before paisa stores it anywhere).
# =====================================================================


CARD_PII = {
    "id": "card_LEAKCANARY_cardid",
    "last4": "4242",
    "network": "Visa",
    "name": "LEAKCANARY Cardholder Name",
    "issuer": "HDFC",
    "international": False,
    "type": "credit",
}
PII_STRINGS = (
    SIM_VPA,
    SIM_EMAIL,
    SIM_CONTACT,
    CARD_PII["id"],
    CARD_PII["last4"],
    CARD_PII["name"],
)


def pii_laden_delivery(rig, minted):
    """A genuine signed delivery, re-signed with a card object bolted on.

    The sim already carries vpa/email/contact/rrn; a `card` block only appears
    on a real card payment, so it is injected here and the body is re-signed by
    the SIMULATOR's own signer. Nothing in this helper constructs a payment
    request — it dresses a webhook the sim already produced (INVARIANT 6).
    """
    d = pay(rig, minted)[0]
    obj = json.loads(d.body.decode("utf-8"))
    entity = obj["payload"]["payment"]["entity"]
    assert entity["vpa"] == SIM_VPA and entity["email"] == SIM_EMAIL
    assert entity["contact"] == SIM_CONTACT
    entity["card"] = dict(CARD_PII)
    entity["card_id"] = CARD_PII["id"]
    body = serialize_body(obj)
    signature = sign_body(body, WEBHOOK_SECRET)
    for s in PII_STRINGS:
        assert s in body.decode("utf-8"), s
    return body, signature


def test_no_customer_pii_from_a_webhook_is_persisted_logged_or_returned(rig):
    """A green webhook stuffed with PII pays the session and leaves no trace."""
    minted = rig.client.post("/intent", json=two_item_body("sess-pii")).json()
    body, signature = pii_laden_delivery(rig, minted)

    r = rig.client.post(
        "/webhook",
        content=body,
        headers={"Content-Type": "application/json",
                 "X-Razorpay-Signature": signature},
    )
    assert r.status_code == 200, r.text
    # the PII-bearing body really was processed all the way to money
    assert r.json()["green"] is True
    assert r.json()["settled_nonce"] == minted["nonce"]
    assert rig.kernel.get(minted["nonce"]).state == SETTLED

    surfaces = {
        "webhook response": r.text,
        "session view": rig.client.get("/session/sess-pii").text,
        "health": rig.client.get("/health").text,
        "intent replay": rig.client.post(
            "/intent", json=two_item_body("sess-pii")
        ).text,
        "service repr": repr(rig.svc),
        "stored link": json.dumps(rig.svc.stored_link(minted["nonce"])),
        "ledger file": open(rig.ledger_path, encoding="utf-8").read(),
        "kernel rows": json.dumps([vars(i) for i in rig.kernel.all_intents()]),
    }
    for name, blob in surfaces.items():
        for needle in PII_STRINGS:
            assert needle not in blob, f"{needle!r} leaked into {name}"
        # secrets, on the same sweep and for the same reason
        assert WEBHOOK_SECRET not in blob, f"webhook secret leaked into {name}"
        assert KEY_SECRET not in blob, f"key secret leaked into {name}"
    # nor the raw body, which is the only thing that could reconstruct them
    assert "acquirer_data" not in surfaces["ledger file"]
    assert '"vpa"' not in surfaces["ledger file"]

    ok, _, _, err = verify(rig.ledger_path)
    assert ok, err


def test_a_gateway_that_hands_back_customer_pii_has_it_stripped_before_storage(rig):
    """paisa keeps the minted link. It must not keep the customer with it.

    The simulator returns a lean link entity, but the real Razorpay
    `payment_links` response carries a `customer` block, and a fetched payment
    carries vpa/email/contact/card. paisa stores whatever the gateway returned,
    so the scrub has to happen at that boundary, not at the response boundary.
    """

    class ChattyGateway:
        def create_payment_link(self, amount_paise, notes, **kw):
            return {
                "id": "plink_chatty",
                "short_url": "https://rzp.io/i/chatty",
                "reference_id": notes["nonce"],
                "amount": int(amount_paise),
                "status": "created",
                "notes": dict(notes),
                "customer": {
                    "name": CARD_PII["name"],
                    "email": SIM_EMAIL,
                    "contact": SIM_CONTACT,
                },
                "vpa": SIM_VPA,
                "card": dict(CARD_PII),
                "card_id": CARD_PII["id"],
                "acquirer_data": {"rrn": "LEAKCANARY_rrn_123456"},
                "payments": [
                    {"payment_id": "pay_1", "email": SIM_EMAIL,
                     "contact": SIM_CONTACT, "amount": int(amount_paise)}
                ],
            }

    rig.svc.gateway = ChattyGateway()
    r = rig.client.post("/intent", json=two_item_body("sess-chatty"))
    assert r.status_code == 200, r.text
    nonce = r.json()["nonce"]

    stored = rig.svc.stored_link(nonce)
    blobs = {
        "stored link": json.dumps(stored),
        "intent response": r.text,
        "session view": rig.client.get("/session/sess-chatty").text,
        "ledger": open(rig.ledger_path, encoding="utf-8").read(),
    }
    for name, blob in blobs.items():
        for needle in (SIM_EMAIL, SIM_CONTACT, SIM_VPA, CARD_PII["name"],
                       CARD_PII["last4"], "LEAKCANARY_rrn_123456"):
            assert needle not in blob, f"{needle!r} survived into {name}"

    # and the fields paisa actually needs are untouched
    assert stored["id"] == "plink_chatty"
    assert stored["short_url"] == "https://rzp.io/i/chatty"
    assert stored["amount"] == PRICES[SKU_RICE] + PRICES[SKU_DAL]
    assert r.json()["short_url"] == "https://rzp.io/i/chatty"


def test_strip_pii_is_recursive_and_keeps_the_money(rig):
    """Unit-level: nested, listed and deeply buried PII all go."""
    doc = {
        "id": "plink_1",
        "amount": 21450,
        "email": SIM_EMAIL,
        "notes": {"session_id": "s1", "contact": SIM_CONTACT},
        "payments": [
            {"payment_id": "pay_1", "vpa": SIM_VPA,
             "card": dict(CARD_PII), "amount": 21450},
            {"payment_id": "pay_2", "customer": {"email": SIM_EMAIL}},
        ],
        "acquirer_data": {"rrn": "999", "upi_transaction_id": "u1"},
    }
    clean = strip_pii(doc)
    blob = json.dumps(clean)
    for needle in (SIM_EMAIL, SIM_CONTACT, SIM_VPA, CARD_PII["last4"],
                   CARD_PII["name"], "999", "u1"):
        assert needle not in blob, needle
    assert clean["id"] == "plink_1"
    assert clean["amount"] == 21450
    assert clean["notes"]["session_id"] == "s1"
    assert clean["payments"][0]["payment_id"] == "pay_1"
    assert clean["payments"][0]["amount"] == 21450
    assert doc["email"] == SIM_EMAIL, "strip_pii mutated its argument"
    assert set(PII_FIELDS) >= {"vpa", "email", "contact", "card", "card_id",
                               "customer", "acquirer_data", "rrn"}
    # the scrub leaves a receipt: field NAMES, never values
    assert set(clean[PII_DROPPED_KEY]) == {
        "email", "contact", "vpa", "card", "customer", "acquirer_data"
    }
    assert strip_pii({"id": "x"}) == {"id": "x"}   # no marker when nothing went


def test_an_escalated_intent_becomes_visible_to_a_human_on_health(rig):
    """The other half of GAP 1: escalation only works if someone can see it.

    An indeterminate intent used to be swept forever with nothing counting it.
    Now the kernel gives up after a bounded number of lookups and `/health`
    reports the queue, so the till can be watched without reading the ledger.
    """

    class Exploding:
        def create_payment_link(self, **kw):
            raise RuntimeError("upstream said no")

    rig.svc.gateway = Exploding()
    assert rig.client.post("/intent", json=two_item_body("sess-esc")).status_code == 502
    nonce = rig.kernel.all_intents()[0].nonce
    assert rig.kernel.get(nonce).state == "INDETERMINATE"

    h = rig.client.get("/health").json()
    assert h["intents_escalated"] == 0 and h["intents_needing_human"] == 0

    charges = []

    def never_answers(n):
        return {"found": True, "status": "pending", "payment_id": "p",
                "amount_paise": PRICES[SKU_RICE] + PRICES[SKU_DAL]}

    for _ in range(50):
        rig.kernel.sweep(never_answers)

    assert rig.kernel.get(nonce).state == "ESCALATED"
    h2 = rig.client.get("/health").json()
    assert h2["intents_escalated"] == 1
    assert h2["intents_needing_human"] == 1
    assert charges == []
    # and the session is still unpaid: escalation settles nothing
    view = rig.client.get("/session/sess-esc").json()
    assert view["paid"] is False
    assert view["intents"][0]["needs_human"] is True
    ok, _, _, err = verify(rig.ledger_path)
    assert ok, err


# --------------------------------------------------------------------------
# THE CODE PATH REACHES THE MONEY.
#
# Until this existed, `IntentRequest.geometry` was REQUIRED, so a basket of
# barcodes could not become a payable link at all — no page in this program had
# ever posted a mint. A scan witness is now a second kind of evidence, and it
# is subject to the same rule as the first: paisa re-derives every rupee from
# ITS OWN tables before the kernel is touched. The client sends an id and an
# amount and is given no field in which to assert a payload, a sku or a price.
# --------------------------------------------------------------------------

def _write_witness(tmp_path, lines, *, scan_id="scn_abcdef012345", age_s=0,
                   omit_timestamp=False):
    """Write a witness the way the COUNTER writes one.

    This helper used to inject an `age_s` field and no timestamp at all. paisa
    read `age_s`, so the staleness test passed — against a document shape the
    counter never produces. The real witness carries `at` and nothing else, and
    the only writer of `age_s` set it to a literal 0, which meant the staleness
    gate had never fired in production and the test could not have noticed.

    `age_s` here now moves the REAL timestamp backwards, so a test asking for a
    16-minute-old scan gets a document that is genuinely 16 minutes old.
    """
    import json as _json
    import datetime as _dt

    scans = tmp_path / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=age_s)
    doc = {
        "scan_id": scan_id,
        "codes_found": len(lines), "distinct_codes": len({l["code"] for l in lines}),
        "lines": lines,
    }
    if not omit_timestamp:
        doc["at"] = at.isoformat()
    (scans / f"{scan_id}.json").write_text(_json.dumps(doc), encoding="utf-8")
    return scan_id


def _write_bindings(tmp_path, mapping):
    import json as _json

    shop = tmp_path / "shop"
    shop.mkdir(parents=True, exist_ok=True)
    (shop / "product_codes.json").write_text(
        _json.dumps({"format": 1, "codes": mapping}), encoding="utf-8")


def _scan_req(scan_id, amount, session="s1"):
    return paisa.IntentRequest(
        session_id=session, amount_paise=amount,
        scan=paisa.ScanRef(scan_id=scan_id))


def test_a_scan_witness_is_repriced_from_the_servers_own_book(tmp_path) -> None:
    """The witness names a sku; the price book decides what it costs."""
    _write_bindings(tmp_path, {"111": "parle", "222": "soap"})
    sid = _write_witness(tmp_path, [
        {"code": "111", "sku_id": "parle"},
        {"code": "222", "sku_id": "soap"},
    ])
    book = paisa.DictPriceBook({"parle": 1000, "soap": 3500})
    v = paisa.rerun_scan(_scan_req(sid, 4500), book, data_dir=str(tmp_path))
    assert v.agrees is True, v.detail
    assert v.server_total_paise == 4500
    assert v.witnessed_paise == 4500
    assert set(v.priced_items) == {"parle", "soap"}


def test_a_witness_that_names_the_wrong_sku_is_refused_not_believed(tmp_path) -> None:
    """The till's claim is COMPARED against this counter's table, never trusted."""
    _write_bindings(tmp_path, {"111": "soap"})           # the table says soap
    sid = _write_witness(tmp_path, [{"code": "111", "sku_id": "parle"}])  # till said parle
    book = paisa.DictPriceBook({"parle": 1000, "soap": 3500})
    v = paisa.rerun_scan(_scan_req(sid, 1000), book, data_dir=str(tmp_path))
    assert v.agrees is False
    assert v.reason == "code_names_a_different_product"


def test_an_unpriceable_line_blocks_the_mint_and_is_never_dropped(tmp_path) -> None:
    """A bill that is short by silence looks exactly like a complete one.

    This is the single most valuable refusal in the code path: the amber line
    must not quietly fall out of the total leaving a smaller, plausible bill.
    """
    _write_bindings(tmp_path, {"111": "parle"})          # 222 is bound to nothing
    sid = _write_witness(tmp_path, [
        {"code": "111", "sku_id": "parle"},
        {"code": "222", "sku_id": None},
    ])
    book = paisa.DictPriceBook({"parle": 1000})
    v = paisa.rerun_scan(_scan_req(sid, 1000), book, data_dir=str(tmp_path))
    assert v.agrees is False
    assert v.reason == "amber_in_basket"
    assert "222" in v.detail


def test_a_one_paisa_disagreement_refuses(tmp_path) -> None:
    _write_bindings(tmp_path, {"111": "parle"})
    sid = _write_witness(tmp_path, [{"code": "111", "sku_id": "parle"}])
    book = paisa.DictPriceBook({"parle": 1000})
    v = paisa.rerun_scan(_scan_req(sid, 1001), book, data_dir=str(tmp_path))
    assert v.agrees is False
    assert v.reason == "scan_total_disagreement"
    assert v.server_total_paise == 1000


def test_a_missing_witness_is_refused_by_name(tmp_path) -> None:
    _write_bindings(tmp_path, {})
    book = paisa.DictPriceBook({})
    v = paisa.rerun_scan(_scan_req("scn_notarealscanid1", 1000), book,
                         data_dir=str(tmp_path))
    assert v.agrees is False and v.reason == "scan_not_found"


def test_a_stale_witness_is_refused(tmp_path) -> None:
    """A basket seen twenty minutes ago is not the basket on the counter."""
    _write_bindings(tmp_path, {"111": "parle"})
    sid = _write_witness(tmp_path, [{"code": "111", "sku_id": "parle"}], age_s=99999)
    book = paisa.DictPriceBook({"parle": 1000})
    v = paisa.rerun_scan(_scan_req(sid, 1000), book, data_dir=str(tmp_path))
    assert v.agrees is False and v.reason == "stale_witness"


def test_a_scan_id_cannot_walk_out_of_the_scan_directory(tmp_path) -> None:
    """The id is a filename component and is checked before it is joined."""
    assert paisa.load_scan_witness("../../etc/passwd", str(tmp_path)) is None
    assert paisa.load_scan_witness("..", str(tmp_path)) is None
    assert paisa.load_scan_witness("a/b", str(tmp_path)) is None
    assert paisa.load_scan_witness("", str(tmp_path)) is None


def test_an_intent_with_no_evidence_is_refused_at_the_mint(tmp_path) -> None:
    """Neither means nothing was witnessed; both means two stories, one basket.

    The model ALLOWS both fields to be absent — the refusal lives in
    create_intent, before the kernel is touched, so that it can be audited and
    named rather than raised as a validation error nobody records.
    """
    req = paisa.IntentRequest(session_id="s", amount_paise=100)
    assert req.geometry is None and req.scan is None


def test_a_witness_with_no_timestamp_is_stale_not_fresh(tmp_path):
    """FAIL CLOSED. An age that cannot be established is not an age of zero.

    The old code read a field the counter wrote as a literal 0, so a witness of
    any age minted. A document whose age is unknowable must be refused, because
    "we could not tell" and "it is fresh" are different answers and only one of
    them is safe on a money path.
    """
    _write_bindings(tmp_path, {"111": "parle"})
    sid = _write_witness(tmp_path, [{"code": "111", "sku_id": "parle"}],
                         omit_timestamp=True)
    v = paisa.rerun_scan(_scan_req(sid, 1000),
                         paisa.DictPriceBook({"parle": 1000}), data_dir=str(tmp_path))
    assert v.agrees is False
    assert v.reason == "stale_witness"


def test_the_staleness_window_is_enforced_at_its_stated_edge(tmp_path):
    """A scan just inside the window mints; one just outside does not.

    Pinning both sides matters here because the gate spent its whole life
    unreachable while a test asserted it worked.
    """
    _write_bindings(tmp_path, {"111": "parle"})
    book = paisa.DictPriceBook({"parle": 1000})

    inside = _write_witness(tmp_path, [{"code": "111", "sku_id": "parle"}],
                            scan_id="scn_inside_window", age_s=880)
    v = paisa.rerun_scan(_scan_req(inside, 1000), book, data_dir=str(tmp_path))
    assert v.reason != "stale_witness", "a 880s-old scan was called stale inside a 900s window"

    outside = _write_witness(tmp_path, [{"code": "111", "sku_id": "parle"}],
                             scan_id="scn_outside_window", age_s=920)
    v = paisa.rerun_scan(_scan_req(outside, 1000), book, data_dir=str(tmp_path))
    assert v.agrees is False
    assert v.reason == "stale_witness"


# ---------------------------------------------------------------------------
# WEBHOOK LIVENESS — can anything reach this counter at all?
#
# A pay screen that knows only "not green yet" shows the identical spinner for
# a customer who has not paid and for a tunnel that has been dead since
# Saturday. Both happened: cloudflared's quick tunnel was revoked and looped on
# "Unauthorized: Tunnel not found" for hours, so a payment that HAD settled at
# the gateway left the till spinning "AWAITING_SETTLEMENT — 78s".
#
# So the service records when it last heard from the gateway AT ALL. This is a
# reachability fact and never an authorisation: a forged POST proves the path is
# open exactly as well as a genuine one, and neither can turn anything green.
# ---------------------------------------------------------------------------


def test_a_counter_that_has_never_heard_a_webhook_says_so(rig):
    h = rig.svc.health()
    assert h["webhooks_seen"] == 0
    assert h["last_webhook_at"] is None
    assert h["last_green_webhook_at"] is None


def test_a_webhook_rejected_for_a_bad_signature_still_proves_reachability(rig):
    # THE WHOLE POINT. The question this answers is "can anything get here",
    # not "was that payment real" — so a refusal must still move the counter,
    # or a dead tunnel and a hostile POST would look identical from the outside.
    status, _body = rig.svc.handle_webhook(
        b'{"event":"payment_link.paid"}', "not-a-valid-signature"
    )
    assert status != 200
    h = rig.svc.health()
    assert h["webhooks_seen"] == 1
    assert h["last_webhook_at"] is not None
    # ...and it authorised nothing.
    assert h["last_green_webhook_at"] is None


def test_reachability_is_never_mistaken_for_settlement(rig):
    for _ in range(5):
        rig.svc.handle_webhook(b"{}", "garbage")
    h = rig.svc.health()
    assert h["webhooks_seen"] == 5
    assert h["last_green_webhook_at"] is None
    assert h["intents_by_state"].get("SETTLED", 0) == 0


def test_the_session_view_carries_the_liveness_fact_the_pay_screen_polls(rig):
    # The pay screen polls /session/{id}, not /health. A liveness fact that only
    # exists on /health cannot be shown where the person is actually looking —
    # and "where the person is looking" is the entire fix.
    body = two_item_body()
    assert rig.client.post("/intent", json=body).status_code == 200

    view = rig.client.get("/session/sess-happy").json()
    assert view["paid"] is False
    assert view["webhooks_seen"] == 0
    assert view["last_webhook_at"] is None

    # A callback arrives and is REFUSED. The session must not move — and the
    # liveness fact must, because the tunnel is demonstrably up.
    rig.client.post(
        "/webhook",
        content=b'{"event":"payment_link.paid"}',
        headers={"X-Razorpay-Signature": "wrong"},
    )
    after = rig.client.get("/session/sess-happy").json()
    assert after["paid"] is False, "a refused webhook must never settle a session"
    assert after["webhooks_seen"] == 1
    assert after["last_webhook_at"] is not None


# ===========================================================================
# A BILL THE COUNTER RECOGNISED BY LOOKING
#
# 34 of the 36 products in a seeded shop carry no printed label. For the whole
# life of `rerun_scan` such a line had no payload to re-resolve, missed the
# binding table and fell into `amber` — so every appearance-only bill refused
# with `amber_in_basket` and the till could not take money for anything it had
# recognised by camera. These hold the branch that fixed it, including the two
# ways it must still refuse.
# ===========================================================================

def _appearance_witness(tmp_path, *, sku="ponds", paise=30000,
                        top1_bp=6439, phi_bp=6000):
    import datetime as _dt
    scans = tmp_path / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    doc = {
        "scan_id": "scn_look0000000000000001",
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "frame_px": [419, 315],
        "codes_found": 0,
        "lines": [{"id": 0, "code": "", "named_by": "appearance",
                   "sku_id": sku, "name": sku, "price_paise": paise,
                   "top1_bp": top1_bp, "phi_bp": phi_bp,
                   "reason": "recognised_by_appearance"}],
        "witnessed_paise": paise,
    }
    (scans / f"{doc['scan_id']}.json").write_text(json.dumps(doc))
    return doc


def test_a_product_named_by_appearance_is_repriced_and_agrees(tmp_path, monkeypatch):
    doc = _appearance_witness(tmp_path)
    monkeypatch.setenv("GAWAAH_SCAN_DIR", str(tmp_path / "scans"))
    book = paisa.DictPriceBook({"ponds": 30000})
    req = paisa.IntentRequest(session_id="s_look_1", amount_paise=30000,
                              scan=paisa.ScanRef(scan_id=doc["scan_id"]))
    v = paisa.rerun_scan(req, book, data_dir=str(tmp_path))
    assert v.agrees, (v.reason, v.detail)
    # The PRICE is paisa's own, not the till's figure.
    assert v.server_total_paise == 30000
    assert v.server_lines == ("ponds",)
    assert v.amber_items == ()


def test_the_price_comes_from_paisa_not_from_the_witness(tmp_path, monkeypatch):
    """A till that recorded the wrong price does not get to keep it."""
    doc = _appearance_witness(tmp_path, paise=999999)
    monkeypatch.setenv("GAWAAH_SCAN_DIR", str(tmp_path / "scans"))
    book = paisa.DictPriceBook({"ponds": 30000})
    req = paisa.IntentRequest(session_id="s_look_2", amount_paise=999999,
                              scan=paisa.ScanRef(scan_id=doc["scan_id"]))
    v = paisa.rerun_scan(req, book, data_dir=str(tmp_path))
    # paisa re-priced it at its own 30000, so the declared 999999 cannot agree.
    assert v.server_total_paise == 30000


def test_appearance_below_its_own_gate_is_refused(tmp_path, monkeypatch):
    """The counter must show its working, and the working must clear the bar."""
    doc = _appearance_witness(tmp_path, top1_bp=5900, phi_bp=6000)
    monkeypatch.setenv("GAWAAH_SCAN_DIR", str(tmp_path / "scans"))
    book = paisa.DictPriceBook({"ponds": 30000})
    req = paisa.IntentRequest(session_id="s_look_3", amount_paise=30000,
                              scan=paisa.ScanRef(scan_id=doc["scan_id"]))
    v = paisa.rerun_scan(req, book, data_dir=str(tmp_path))
    assert not v.agrees
    assert v.reason == "appearance_evidence_missing"


def test_appearance_with_no_evidence_at_all_is_refused(tmp_path, monkeypatch):
    """A line that carries no similarity is not minted on trust."""
    doc = _appearance_witness(tmp_path, top1_bp=None, phi_bp=None)
    monkeypatch.setenv("GAWAAH_SCAN_DIR", str(tmp_path / "scans"))
    book = paisa.DictPriceBook({"ponds": 30000})
    req = paisa.IntentRequest(session_id="s_look_4", amount_paise=30000,
                              scan=paisa.ScanRef(scan_id=doc["scan_id"]))
    v = paisa.rerun_scan(req, book, data_dir=str(tmp_path))
    assert not v.agrees
    assert v.reason == "appearance_evidence_missing"


def test_an_appearance_sku_paisa_cannot_price_is_still_amber(tmp_path, monkeypatch):
    """The short-by-silence rule survives the new path."""
    doc = _appearance_witness(tmp_path, sku="never_taught_here")
    monkeypatch.setenv("GAWAAH_SCAN_DIR", str(tmp_path / "scans"))
    book = paisa.DictPriceBook({"ponds": 30000})
    req = paisa.IntentRequest(session_id="s_look_5", amount_paise=30000,
                              scan=paisa.ScanRef(scan_id=doc["scan_id"]))
    v = paisa.rerun_scan(req, book, data_dir=str(tmp_path))
    assert not v.agrees
    assert v.reason == "amber_in_basket"
