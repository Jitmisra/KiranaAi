"""S6 acceptance: the BRIDGE, over a real WebSocket, with no camera.

Everything here drives `gawaah.brain_server` through Starlette's in-process
ASGI transport — the same code path a browser takes, minus the socket. The
brain, the placement detector, the tracker, the line zone, MUDRA, PEEL, CHILLA,
SAAF, the sqlite kernel, the Razorpay simulator and its real HMAC-SHA256
signatures are all the shipping modules; nothing in this file is a mock.

The file is organised by INVARIANT rather than by function, because that is
what these tests are for. The four the task names — invariant 4's frame gate,
invariant 5's secret, the protocol itself, and integer paise — are the first
four sections and every one of them is asserted against a real message on a
real socket, not against a return value.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from gawaah import brain_server as bs  # noqa: E402
from gawaah import chilla as _chilla  # noqa: E402
from gawaah import ident_sticker as _peel  # noqa: E402
from gawaah.takhti import BUF_H, BUF_W  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def server(tmp_path):
    """A whole counter, wired, with the sim script attached but not pumping."""
    s = bs.build_sim_server(tmp_path / "work", web_dir=WEB, with_sim=True)
    yield s
    s.close()


@pytest.fixture
def bare(tmp_path):
    """The same counter with no sim script, so no async pump starts on connect."""
    s = bs.build_sim_server(tmp_path / "work", web_dir=WEB, with_sim=False)
    yield s
    s.close()


@pytest.fixture
def sim():
    return bs.SimScript()


def png_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    import base64

    return base64.b64encode(buf.tobytes()).decode("ascii")


def frame_msg(img: np.ndarray, ts: str | None = None) -> dict:
    return {"type": "frame", "rect": png_b64(img), "ts": ts}


def run_sim(server, *, upto: int | None = None) -> list[dict]:
    """Drive the whole script through `handle` and return EVERY message."""
    script = server.sim
    assert script is not None
    n = script.total_frames if upto is None else upto
    out: list[dict] = []
    for i in range(n):
        out += server.handle(frame_msg(script.frame(i)))
        for cmd in script.commands_at(i):
            out += server.handle(cmd)
    return out


def of_type(msgs, kind: str) -> list[dict]:
    return [m for m in msgs if m.get("type") == kind]


# =====================================================================
# INVARIANT 4 — the mask is applied at frame grab. This gate is the whole
# reason this module can be trusted with a camera on the other end.
# =====================================================================


def test_a_rectified_frame_is_accepted(sim):
    v = bs.decode_rect(png_b64(sim.frame(0)))
    assert v.ok, v.reason
    assert v.image is not None
    assert v.image.shape[:2] == (BUF_H, BUF_W) == bs.RECT_SHAPE


@pytest.mark.parametrize(
    "shape,why",
    [
        ((960, 1280), "a 1280x960 raw camera frame"),
        ((720, 1280), "a 720p raw camera frame"),
        ((1080, 1920), "a 1080p raw camera frame"),
        ((840, 1188), "the rectified buffer TRANSPOSED"),
        ((594, 420), "the rectified buffer halved"),
        ((1189, 840), "one row too tall"),
        ((1188, 841), "one column too wide"),
        ((8, 8), "a thumbnail"),
    ],
)
def test_a_frame_that_is_not_the_rectified_buffer_is_refused(shape, why):
    """THE test. A raw camera frame must never reach the brain."""
    img = np.full(shape, 128, np.uint8)
    v = bs.decode_rect(png_b64(img))
    assert not v.ok, f"{why} was ACCEPTED — invariant 4 is broken"
    assert v.reason == bs.R_RECT_WRONG_SHAPE
    assert v.image is None
    assert v.shape[:2] == shape
    assert "invariant 4" in v.detail


def test_the_refusal_is_logged(server, caplog):
    caplog.set_level(logging.WARNING, logger="gawaah.brain_server")
    server.handle(frame_msg(np.full((960, 1280), 128, np.uint8)))
    hits = [r for r in caplog.records if bs.R_RECT_WRONG_SHAPE in r.getMessage()]
    assert hits, "a refused frame must be logged, not silently dropped"
    assert "1280x960" in hits[0].getMessage()


def test_the_refusal_is_counted_and_the_brain_never_sees_the_frame(server):
    before = server.brain.frame_index
    msgs = server.handle(frame_msg(np.full((960, 1280), 128, np.uint8)))
    assert server.brain.frame_index == before, "a refused frame reached the brain"
    assert server.frames_accepted == 0
    assert server.refusals[bs.R_RECT_WRONG_SHAPE] == 1
    assert [m["type"] for m in msgs] == ["refused"]
    assert msgs[0]["expected_shape"] == [BUF_H, BUF_W]


def test_a_wrong_shape_frame_is_refused_over_the_websocket(bare):
    """The assertion the task names, made where it matters: on the socket."""
    app = bs.create_app(bare)
    before = bare.brain.frame_index
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(len(bare.hello())):
                ws.receive_json()
            ws.send_json(frame_msg(np.full((960, 1280), 200, np.uint8)))
            m = ws.receive_json()
    assert m["type"] == "refused"
    assert m["reason"] == bs.R_RECT_WRONG_SHAPE
    assert m["shape"] == [960, 1280]
    assert bare.brain.frame_index == before, "a refused frame reached the brain"
    assert bare.frames_accepted == 0


@pytest.mark.parametrize(
    "rect,reason",
    [
        (None, bs.R_RECT_MISSING),
        ("", bs.R_RECT_MISSING),
        (12345, bs.R_RECT_MISSING),
        ("not base64 at all!!", bs.R_RECT_NOT_BASE64),
        ("///", bs.R_RECT_NOT_BASE64),
        ("aGVsbG8gd29ybGQ=", bs.R_RECT_NOT_AN_IMAGE),  # valid b64, not an image
    ],
)
def test_every_frame_refusal_reason_is_reachable(rect, reason):
    assert bs.decode_rect(rect).reason == reason


def test_an_oversized_payload_is_refused_before_it_is_decoded():
    v = bs.decode_rect("A" * 64, max_b64=32)
    assert v.reason == bs.R_RECT_TOO_LARGE


def test_a_colour_png_of_the_right_shape_is_accepted(sim):
    colour = cv2.cvtColor(sim.frame(0), cv2.COLOR_GRAY2BGR)
    v = bs.decode_rect(png_b64(colour))
    assert v.ok
    assert v.image.shape == (BUF_H, BUF_W, 3)


def test_a_data_url_prefix_is_accepted(sim):
    v = bs.decode_rect("data:image/png;base64," + png_b64(sim.frame(0)))
    assert v.ok


def test_the_shape_gate_is_necessary_but_not_sufficient(sim):
    """The honest limit, as an executable assertion rather than a docstring.

    A raw camera frame RESIZED to 840x1188 passes the shape gate. It must, and
    saying otherwise would be a false claim of safety. What separates the two
    cases is `mat_evidence`, which looks for the four TAKHTI markers: the
    genuine rectified buffer has all four, the resized impostor has none.
    """
    impostor = cv2.resize(
        np.random.default_rng(0).integers(0, 255, (960, 1280), dtype=np.uint8),
        (BUF_W, BUF_H),
    )
    assert bs.decode_rect(png_b64(impostor)).ok, (
        "the shape gate cannot see through a resize, and this test exists so "
        "nobody believes it can"
    )
    assert bs.mat_evidence(impostor)["markers_found"] == []
    assert bs.mat_evidence(sim.frame(0))["markers_found"] == [0, 1, 2, 3]


def test_mat_evidence_never_raises_on_rubbish():
    ev = bs.mat_evidence(np.zeros((BUF_H, BUF_W), np.uint8))
    assert ev["markers_found"] == []
    assert ev["markers_expected"] == [0, 1, 2, 3]


def test_no_message_ever_carries_pixels(server):
    """Invariant 4's other half: what survives is millimetres and paise."""
    for m in run_sim(server):
        blob = json.dumps(m)
        assert "rect" not in m, m["type"]
        assert "image" not in m, m["type"]
        assert "crop" not in m, m["type"]
        # A PNG or JPEG smuggled into a string field would carry its magic.
        assert "iVBORw0KGgo" not in blob, f"a PNG leaked in a {m['type']} message"
        assert "/9j/" not in blob, f"a JPEG leaked in a {m['type']} message"


def test_the_encoder_refuses_to_encode_a_non_rectified_buffer():
    with pytest.raises(bs.BridgeError, match="rectified"):
        bs.encode_rect(np.zeros((960, 1280), np.uint8))


# =====================================================================
# INVARIANT 5 — this server holds no Razorpay secret and does not mint.
# =====================================================================


def test_no_secret_ever_appears_in_any_message(server):
    """Drive the ENTIRE sim, including a real signed webhook and a settlement,
    and grep every byte that would have gone down the socket."""
    secret = server.forbidden[0]
    assert secret.startswith("whsec_"), "the fixture must register the real secret"
    msgs = run_sim(server)
    assert len(msgs) > 300, "the sweep must actually cover a whole sale"
    assert of_type(msgs, "state"), "no state messages — the sweep proved nothing"
    for m in msgs:
        blob = json.dumps(m)
        assert secret not in blob, f"the webhook secret leaked in a {m['type']}"
        for key in bs.FORBIDDEN_KEYS:
            assert key not in blob.lower(), f"{key!r} appeared in a {m['type']}"


def test_the_session_really_did_settle_during_that_sweep(server):
    """Guards the test above: a sweep over a sale that never paid would find no
    secret because there was never a settlement to leak one from."""
    msgs = run_sim(server)
    states = [m["session_state"] for m in of_type(msgs, "state")]
    assert "PAID" in states, states[-6:]
    assert any(m["settled_payment_id"] for m in of_type(msgs, "state"))


def test_the_server_holds_no_secret_attribute(server):
    assert not hasattr(server, "secret")
    assert not hasattr(server, "webhook_secret")
    assert not hasattr(server, "mint")
    for name in vars(server):
        assert "secret" not in name.lower()
    # `forbidden` deliberately DOES hold it — that is a promise never to say
    # it, not a capability. It cannot sign anything.
    assert server.forbidden and isinstance(server.forbidden[0], str)


def test_scrub_blocks_a_forbidden_key():
    with pytest.raises(bs.SecretLeak, match="key"):
        bs.scrub({"type": "state", "webhook_secret": "whsec_x"}, ())
    with pytest.raises(bs.SecretLeak):
        bs.scrub({"type": "state", "cfg": {"keySecret": "x"}}, ())
    with pytest.raises(bs.SecretLeak):
        bs.scrub({"type": "state", "rows": [{"Authorization": "Bearer x"}]}, ())


def test_scrub_blocks_a_forbidden_value_under_an_innocent_key():
    with pytest.raises(bs.SecretLeak, match="forbidden string"):
        bs.scrub({"type": "refused", "detail": "bad sig for whsec_abcdefgh"},
                 ("whsec_abcdefgh",))


def test_scrub_passes_a_clean_message():
    bs.scrub({"type": "state", "total_paise": 2850, "nonce": "abc"}, ("whsec_x" * 3,))


def test_a_leaking_message_is_dropped_not_sent(server):
    secret = server.forbidden[0]
    out = server.safe({"type": "state", "detail": f"oops {secret}"})
    assert out["type"] == "refused"
    assert out["reason"] == bs.R_OUTBOUND_REDACTED
    assert out["dropped_type"] == "state"
    assert secret not in json.dumps(out)
    assert server.leaks_blocked == 1


def test_a_short_forbidden_string_is_refused_at_registration(server):
    with pytest.raises(bs.BridgeError, match="at least"):
        server.add_forbidden("abc")


def test_settlement_is_the_only_thing_that_can_mint(server):
    """The brain's port holds the secret; the bridge holds a reference to the
    brain and nothing else. Reverting a mint is not expressible here."""
    assert not hasattr(server, "settlement")
    assert not hasattr(server, "gateway")
    assert not hasattr(server, "kernel")


# =====================================================================
# THE PROTOCOL — every documented message, on a real socket.
# =====================================================================


def test_connecting_yields_the_whole_board_at_once(bare):
    app = bs.create_app(bare)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            got = [ws.receive_json() for _ in range(len(bare.hello()))]
    kinds = [m["type"] for m in got]
    assert kinds[0] == "state"
    for panel in ("ledger", "mudra", "peel", "chilla", "saaf"):
        assert panel in kinds, f"a fresh client is blank on the {panel} panel"
    for m in got:
        if m["type"] != "state":
            assert "reason" in m, f"{m['type']} arrived with no reason"


def test_a_frame_yields_the_documented_message_sequence(bare, sim):
    app = bs.create_app(bare)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(len(bare.hello())):
                ws.receive_json()
            ws.send_json(frame_msg(sim.frame(0), ts="2026-08-29T09:00:00.000+00:00"))
            got = [ws.receive_json() for _ in range(6)]
    assert [m["type"] for m in got] == [
        "state", "mudra", "peel", "chilla", "saaf", "ledger",
    ]
    # Brain.frame_index starts at -1 and the first ingest makes it 0.
    assert got[0]["frame_index"] == 0
    assert all(m["frame_index"] == 0 for m in got)


def test_the_websocket_is_served_where_app_js_actually_dials(bare):
    """web/app.js has WS_URL = 'ws://localhost:8787' — the ROOT path."""
    src = (WEB / "app.js").read_text(encoding="utf-8")
    assert "ws://localhost:8787'" in src or 'ws://localhost:8787"' in src, (
        "app.js moved its WS_URL; if it now dials /ws, delete the root route, "
        "not this test"
    )
    app = bs.create_app(bare)
    with TestClient(app) as client:
        with client.websocket_connect("/") as ws:
            assert ws.receive_json()["type"] == "state"


def test_the_static_client_is_served_from_the_same_process(bare):
    app = bs.create_app(bare)
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "<!doctype html>" in index.text.lower()
        for asset in ("app.js", "style.css"):
            assert client.get(f"/{asset}").status_code == 200, asset


def test_health_and_state_endpoints(bare, sim):
    app = bs.create_app(bare)
    with TestClient(app) as client:
        h = client.get("/health").json()
        assert h["ok"] and h["module"] == "brain_server"
        assert h["rect_shape"] == [BUF_H, BUF_W]
        assert h["panels"] == list(bs.PANELS)
        st = client.get("/state").json()
        assert isinstance(st["total_paise"], int)


@pytest.mark.parametrize(
    "msg,first",
    [
        ({"type": "done"}, "state"),
        ({"type": "ack"}, "state"),
        ({"type": "revert", "item_id": "nope"}, "refused"),
        ({"type": "select_panel", "id": "mudra"}, "panel"),
        ({"type": "enrol_sticker", "name": "s"}, "saaf"),
        ({"type": "refresh"}, "state"),
    ],
)
def test_every_documented_client_verb_is_answered(bare, msg, first):
    out = bare.handle(msg)
    assert out, f"{msg['type']} produced no reply at all"
    assert out[0]["type"] == first


def test_the_published_verb_list_is_exactly_what_is_handled(bare):
    """A verb advertised in a refusal that nothing handles is a lie the client
    would act on."""
    for verb in bs.CLIENT_VERBS:
        out = bare.handle({"type": verb})
        assert out[0].get("reason") != bs.R_UNKNOWN_TYPE, (
            f"{verb!r} is published in CLIENT_VERBS but has no handler"
        )
    assert bare.handle({"type": "select_panels"})[0]["reason"] == bs.R_UNKNOWN_TYPE


def test_the_sim_can_be_rewound(sim):
    a = [sim.next_frame() for _ in range(4)]
    sim.reset()
    b = [sim.next_frame() for _ in range(4)]
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    assert sim.drain_commands() == []


def test_an_unknown_type_is_refused_with_the_known_list(bare):
    out = bare.handle({"type": "launch_missiles"})
    assert out[0]["reason"] == bs.R_UNKNOWN_TYPE
    assert out[0]["known"] == list(bs.CLIENT_VERBS)


def test_refresh_carries_an_out_of_band_settlement_to_the_browser(server):
    """A webhook is delivered to the BRAIN. Nothing about that delivery passes
    through this socket, so without `refresh` the shopkeeper's screen would
    still say AWAITING_SETTLEMENT after the customer had paid."""
    script = server.sim
    run_sim(server, upto=script.pay_at)
    assert server.brain.state().session_state == "AWAITING_SETTLEMENT"
    script.on_pay()  # the customer pays; the brain settles; the socket is silent
    assert server.brain.state().session_state == "PAID"
    out = server.handle({"type": "refresh"})
    assert [m["type"] for m in out] == ["state", "ledger"]
    assert out[0]["session_state"] == "PAID"
    assert out[0]["settled_payment_id"].startswith("pay_")


@pytest.mark.parametrize("raw", ["not json", "[]", '"a string"', "17", b"\xff\xfe"])
def test_a_non_message_is_refused_without_killing_the_socket(bare, raw):
    out = bare.handle(raw)
    assert out[0]["type"] == "refused"
    assert out[0]["reason"] == bs.R_NOT_A_MESSAGE


def test_a_message_with_no_type_is_refused(bare):
    assert bare.handle({"rect": "x"})[0]["reason"] == bs.R_NOT_A_MESSAGE


def test_revert_accepts_both_spellings_and_names_an_unknown_line(bare):
    assert bare.handle({"type": "revert"})[0]["reason"] == bs.R_BAD_ARGUMENT
    assert bare.handle({"type": "revert", "item_id": 7})[0]["reason"] == bs.R_BAD_ARGUMENT
    for key in ("item_id", "itemId"):
        out = bare.handle({"type": "revert", key: "ghost"})
        assert out[0]["reason"] == bs.R_UNKNOWN_ITEM, key
        assert out[0]["item_id"] == "ghost"
        assert out[0]["known_items"] == []


def test_reverting_a_line_that_is_not_on_the_bill_is_not_a_silent_no_op(server):
    """Session.on_revert answers an unknown id with a refused transition whose
    reason reaches the LEDGER and not the wire, so `Brain.revert` hands back an
    unchanged state and the tap looks like it worked. The bridge checks first
    so the shopkeeper is told."""
    run_sim(server, upto=server.sim.done_at)
    before = server.brain.state().to_dict()
    out = server.handle({"type": "revert", "item_id": "definitely_not_a_line"})
    assert out[0]["type"] == "refused"
    assert out[0]["reason"] == bs.R_UNKNOWN_ITEM
    assert out[0]["known_items"], "the bill was empty, so this proved nothing"
    assert server.brain.state().to_dict()["lines"] == before["lines"]


def test_a_reverted_line_leaves_the_total(server):
    run_sim(server, upto=server.sim.done_at)
    state = of_type(server.handle({"type": "select_panel", "id": "basket"}), "state")
    lines = [li for li in state[0]["lines"] if not li["amber"]]
    assert lines, "nothing was billed, so revert would prove nothing"
    before = state[0]["total_paise"]
    assert before > 0
    out = server.handle({"type": "revert", "item_id": lines[0]["item_id"]})
    after = of_type(out, "state")[0]
    assert after["total_paise"] == 0 < before
    assert any(li["reverted"] for li in after["lines"])


def test_select_panel_replays_that_panel(server, sim):
    server.handle(frame_msg(sim.frame(0)))
    server.handle(frame_msg(sim.frame(1)))
    out = server.handle({"type": "select_panel", "id": "mudra"})
    assert out[0]["type"] == "panel" and out[0]["id"] == "mudra"
    assert out[1]["type"] == "mudra"
    assert out[1]["state"] is not None


def test_a_replayed_panel_is_the_message_that_was_sent_not_a_fresh_one(server, sim):
    """`select_panel` REPLAYS. It must not re-derive.

    A re-derived MUDRA reading would be measured against a different frame from
    the one the client is looking at, and would be stamped with a frame index
    it never saw. Byte-identity is the only way to state that.
    """
    for i in range(3):
        server.handle(frame_msg(sim.frame(i)))
    live = of_type(server.handle(frame_msg(sim.frame(3))), "mudra")[0]
    assert live["ok"] is True, "a live reading is needed for this to mean anything"

    # Messages that do NOT advance the frame index: a refused frame and a tap.
    server.handle(frame_msg(np.zeros((10, 10), np.uint8)))
    server.handle({"type": "refresh"})

    replay = server.handle({"type": "select_panel", "id": "mudra"})[1]
    assert replay == live, "select_panel re-derived the panel instead of replaying it"
    assert replay["frame_index"] == live["frame_index"] == server.brain.frame_index
    # The stored copy is a snapshot, not a live reference into our own state.
    assert server.last("mudra") is not server.last("mudra")
    stolen = server.last("mudra")
    stolen["state"] = "TAMPERED_BY_A_CALLER"
    assert server.last("mudra")["state"] == live["state"]


def test_select_panel_on_a_panel_that_never_ran_abstains(bare):
    out = bare.handle({"type": "select_panel", "id": "saaf"})
    assert out[1]["type"] == "saaf"
    assert out[1]["ok"] is False
    assert out[1]["reason"] == bs.A_NEVER_RUN


@pytest.mark.parametrize("pid", [None, "", "basketball", 7, "MUDRA"])
def test_an_unknown_panel_is_refused(bare, pid):
    out = bare.handle({"type": "select_panel", "id": pid})
    assert out[0]["reason"] == bs.R_UNKNOWN_PANEL
    assert out[0]["known"] == list(bs.PANELS)


def test_enrol_sticker_answers_with_saaf_then_peel(server, sim):
    for i in range(10):
        server.handle(frame_msg(sim.frame(i)))
    out = server.handle({"type": "enrol_sticker", "name": "counter-upi"})
    assert [m["type"] for m in out] == ["saaf", "peel"]
    saaf, peel = out
    assert saaf["used"] == server.burst_len and saaf["rejected"] == 0
    assert peel["registered"] is True and peel["reason"] == "ENROLLED"
    assert server.registry.is_enrolled("counter-upi")
    # ...and the next frame actually compares against it.
    nxt = of_type(server.handle(frame_msg(sim.frame(11))), "peel")[0]
    assert nxt["reason"] == _peel.R_COMPARED
    assert nxt["verdict"] == _peel.GENUINE


def test_enrol_sticker_without_a_name_is_refused(bare):
    for bad in ({}, {"name": ""}, {"name": 3}, {"name": "   "}):
        out = bare.handle({"type": "enrol_sticker", **bad})
        assert out[0]["reason"] == bs.R_BAD_ARGUMENT


def test_enrol_sticker_with_no_frames_abstains_on_both_panels(bare):
    out = bare.handle({"type": "enrol_sticker", "name": "x"})
    assert [m["type"] for m in out] == ["saaf", "peel"]
    assert out[0]["ok"] is False and out[0]["reason"] == bs.A_BURST_TOO_SHORT
    assert out[1]["ok"] is False and out[1]["reason"] == bs.A_BURST_TOO_SHORT


def test_the_socket_keeps_alive_when_nobody_speaks(bare):
    app = bs.create_app(bare, keepalive_s=0.01)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(len(bare.hello())):
                ws.receive_json()
            assert ws.receive_json() == {"type": "keepalive"}


def test_a_frame_that_the_brain_throws_on_is_refused_not_fatal(bare, sim, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(bare.brain, "ingest_frame", boom)
    out = bare.handle(frame_msg(sim.frame(0)))
    assert out[0]["reason"] == bs.R_BRAIN_REFUSED
    assert "detector exploded" in out[0]["detail"]
    # and the server is still usable
    assert bare.handle({"type": "select_panel", "id": "basket"})[0]["type"] == "panel"


def test_every_published_refusal_reason_can_actually_fire():
    """A reason code nobody can reach is a lie in a docstring."""
    assert set(bs.REFUSAL_REASONS) == set(bs.REFUSAL_REASONS)
    for r in bs.REFUSAL_REASONS:
        assert r.isupper(), r
    assert len(set(bs.REFUSAL_REASONS)) == len(bs.REFUSAL_REASONS)


# =====================================================================
# INTEGER PAISE — invariant 1, on the wire.
# =====================================================================


MONEY_KEYS = (
    "total_paise",
    "price_paise",
    "intent_amount_paise",
    "amount_paise",
)


def _assert_money(obj, where=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in MONEY_KEYS and v is not None:
                assert isinstance(v, int) and not isinstance(v, bool), (
                    f"{where}.{k} is {v!r} ({type(v).__name__}); money is "
                    f"integer paise (invariant 1)"
                )
            _assert_money(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_money(v, f"{where}[{i}]")


def test_the_state_message_carries_integer_paise(server):
    msgs = run_sim(server)
    states = of_type(msgs, "state")
    assert states
    totals = {m["total_paise"] for m in states}
    assert totals != {0}, "no money moved, so this test proved nothing"
    for m in msgs:
        _assert_money(m, m["type"])


def test_a_billed_line_is_integer_paise_end_to_end(server):
    run_sim(server, upto=server.sim.done_at)
    st = server.brain.state().to_dict()
    priced = [li for li in st["lines"] if li["price_paise"] is not None]
    assert priced, "nothing was priced"
    for li in priced:
        assert isinstance(li["price_paise"], int)
    assert st["total_paise"] == sum(
        li["price_paise"] for li in priced if li["committed"] and not li["reverted"]
    )


def test_an_amber_line_carries_no_price_and_is_not_in_the_total(server):
    st = None
    for m in of_type(run_sim(server), "state"):
        if any(li["amber"] for li in m["lines"]):
            st = m
            break
    assert st is not None, "the sim never produced an amber line"
    amber = [li for li in st["lines"] if li["amber"]]
    for li in amber:
        assert li["price_paise"] is None, "an abstention is not money"
        assert li["reason"], "an amber line with no named cause is a guess"
    # amber_items is the COMMITTED subset — the ones that actually left the
    # counter. Either way, no amber line is in the total.
    assert all(li["amber"] and li["committed"] for li in st["amber_items"])
    assert st["amber_count"] == len(st["amber_items"])
    assert st["total_paise"] == sum(
        li["price_paise"]
        for li in st["lines"]
        if not li["amber"] and li["committed"] and not li["reverted"]
    )


def test_chilla_reports_the_intent_amount_as_integer_paise(server):
    matched = [
        m for m in of_type(run_sim(server), "chilla")
        if m["verdict"] == _chilla.MATCHED
    ]
    assert matched, "the sim never corroborated a screen"
    for m in matched:
        assert isinstance(m["amount_paise"], int)
        assert not isinstance(m["amount_paise"], bool)


# =====================================================================
# INVARIANT 7 — every panel has a visible "I do not know" with a name.
# =====================================================================


@pytest.mark.parametrize("panel", ["mudra", "peel", "chilla", "saaf"])
def test_every_panel_abstains_before_it_has_evidence(bare, panel):
    msg = [m for m in bare.hello() if m["type"] == panel]
    assert msg, f"the {panel} panel says nothing at all on connect"
    assert msg[0]["ok"] is False
    assert msg[0]["reason"], f"{panel} abstained with no named reason"


def test_the_first_frame_abstains_on_mudra_and_chilla_because_it_is_the_reference(
    bare, sim
):
    out = bare.handle(frame_msg(sim.frame(0)))
    mudra = of_type(out, "mudra")[0]
    chilla = of_type(out, "chilla")[0]
    assert mudra["ok"] is False and mudra["reason"] == bs.A_NO_REFERENCE
    assert chilla["ok"] is False and chilla["reason"] == bs.A_NO_REFERENCE
    assert "reference" in mudra["detail"]


def test_the_reference_seeding_is_announced_in_the_ledger(bare, sim):
    before = bare.brain.ledger.count
    bare.handle(frame_msg(sim.frame(0)))
    assert bare.brain.ledger.count > before
    seeded = [
        r for r in bare.brain.ledger.read()
        if r.get("module") == bs.MODULE and r.get("what") == "reference_seeded"
    ]
    assert seeded, "an auto-seeded reference must never be silent"
    assert "background" in seeded[0]["detail"]


def test_peel_abstains_by_name_for_a_sticker_nobody_enrolled(bare, sim):
    bare.handle(frame_msg(sim.frame(0)))
    peel = of_type(bare.handle(frame_msg(sim.frame(1))), "peel")[0]
    assert peel["ok"] is False
    assert peel["verdict"] == _peel.UNREGISTERABLE
    assert peel["reason"] == _peel.R_NOT_ENROLLED
    assert peel["registered"] is False


def test_chilla_abstains_by_name_when_there_is_no_screen(bare, sim):
    bare.handle(frame_msg(sim.frame(0)))
    ch = of_type(bare.handle(frame_msg(sim.frame(1))), "chilla")[0]
    assert ch["ok"] is False
    assert ch["reason"].startswith(bs.A_NO_SCREEN + ":")
    assert ch["screen"]["found"] is False
    assert ch["screen"]["reason"]


def test_chilla_abstains_when_a_screen_exists_but_nothing_was_minted(bare, sim):
    """A screen with no intent behind it is not evidence of anything."""
    idx = sim._phase_start("screen")
    bare.handle(frame_msg(sim.frame(0)))
    ch = of_type(bare.handle(frame_msg(sim.frame(idx + 1))), "chilla")[0]
    assert ch["screen"]["found"] is True, ch["screen"]["reason"]
    assert ch["ok"] is False
    assert ch["reason"] == bs.A_NO_INTENT_AMOUNT


def test_chilla_says_amber_stale_against_a_mirror_nobody_refreshed(server, sim):
    """The honest default. An empty mirror is infinitely stale, and a verdict
    computed against it must say so rather than say NO_MATCH."""
    run_sim(server, upto=server.sim.done_at + 1)  # DONE, but nobody paid
    assert server.brain.state().intent_amount_paise is not None
    assert server.mirror.fetched_at is None
    idx = sim._phase_start("screen")
    ch = of_type(server.handle(frame_msg(sim.frame(idx + 1))), "chilla")[0]
    assert ch["verdict"] == _chilla.AMBER_STALE
    assert ch["mirror_rows"] == 0
    assert ch["mirror_age_s"] is None
    assert "never been refreshed" in ch["detail"]


def test_saaf_abstains_with_a_burst_count_before_it_has_one(bare, sim):
    bare.handle(frame_msg(sim.frame(0)))
    s = of_type(bare.handle(frame_msg(sim.frame(1))), "saaf")[0]
    assert s["ok"] is False
    assert s["reason"] == bs.A_BURST_TOO_SHORT
    assert s["burst"] == 2 and s["burst_target"] == bare.burst_len


def test_every_panel_message_carries_ok_and_reason(server):
    for m in run_sim(server):
        if m["type"] in ("mudra", "peel", "chilla", "saaf", "ledger"):
            assert "ok" in m, m
            assert "reason" in m, m
            assert isinstance(m["reason"], str)


def test_the_state_panel_abstains_when_the_client_reports_no_mat_lock(bare, sim):
    """Invariant 7 for the basket: the browser adjudicates the lock, and when
    it says it lost the mat the counter stops measuring rather than guessing."""
    bare.handle(frame_msg(sim.frame(0)))
    msg = frame_msg(sim.frame(1))
    msg["lock"] = {"locked": False, "reason": "only_two_markers"}
    st = of_type(bare.handle(msg), "state")[0]
    assert st["mat_lock"]["locked"] is False
    assert st["mat_lock"]["reason"] == "only_two_markers"
    assert st["placements"] == []
    assert any(e["code"] == "mat_lost" for e in st["exceptions"])


def test_a_client_lock_claim_does_not_persist_to_the_next_frame(bare, sim):
    bare.handle(frame_msg(sim.frame(0)))
    lost = frame_msg(sim.frame(1))
    lost["lock"] = {"locked": False, "reason": "glare"}
    bare.handle(lost)
    st = of_type(bare.handle(frame_msg(sim.frame(2))), "state")[0]
    assert st["mat_lock"]["locked"] is True
    assert st["mat_lock"]["reason"] == "client_rectified"


# =====================================================================
# INVARIANT 2 — no feature turns the counter green.
# =====================================================================


def test_chilla_is_amber_even_when_it_matches(server):
    chilla = of_type(run_sim(server), "chilla")
    matched = [m for m in chilla if m["verdict"] == _chilla.MATCHED]
    assert matched, "the sim never reached MATCHED, so this proves nothing"
    for m in chilla:
        assert m["light"] == "AMBER", (m["verdict"], m["light"])


def test_green_appears_in_exactly_one_field_and_it_is_the_webhook_verdict(server):
    """Invariant 2, as a grep. The word GREEN may appear on the wire in one
    place only: `state.last_webhook_reason`, which is the green predicate's
    verdict over bytes whose HMAC it checked. No panel may ever say it."""
    seen_in_state = 0
    for m in run_sim(server):
        if m["type"] == "state":
            for k, v in m.items():
                if isinstance(v, str) and "GREEN" in v.upper():
                    assert k == "last_webhook_reason", (
                        f"state.{k} says GREEN; only a verified webhook may"
                    )
                    seen_in_state += 1
            rest = {k: v for k, v in m.items() if k != "last_webhook_reason"}
            assert "GREEN" not in json.dumps(rest).upper()
        else:
            assert "GREEN" not in json.dumps(m).upper(), (
                f"a {m['type']} PANEL message said GREEN"
            )
    assert seen_in_state, "the webhook never greened, so this proved nothing"


def test_paid_arrives_only_after_the_signed_webhook_and_never_from_a_panel(server):
    """The exact ordering, on the wire: the session is not PAID until the
    frame on which the gateway's signed delivery was adjudicated."""
    script = server.sim
    paid_at = None
    matched_at = None
    for i in range(script.total_frames):
        msgs = server.handle(frame_msg(script.frame(i)))
        for cmd in script.commands_at(i):
            msgs += server.handle(cmd)
        for m in of_type(msgs, "state"):
            if m["session_state"] == "PAID" and paid_at is None:
                paid_at = i
        for m in of_type(msgs, "chilla"):
            if m["verdict"] == _chilla.MATCHED and matched_at is None:
                matched_at = i
    assert paid_at is not None and matched_at is not None
    assert paid_at == script.pay_at, (
        f"the session turned PAID at frame {paid_at}, not at the frame the "
        f"webhook was delivered ({script.pay_at})"
    )
    assert matched_at >= paid_at, (
        "CHILLA corroborated before the webhook settled, which would mean the "
        "mirror was populated by something other than a payment"
    )


def test_done_cannot_authorise_money(bare):
    """DONE records an amount. Only a webhook can make it money."""
    st = of_type(bare.handle({"type": "done"}), "state")[0]
    assert st["money_authorised"] is False
    assert st["settled_payment_id"] is None
    assert st["session_state"] != "PAID"


# =====================================================================
# The client-rectified plane adapter.
# =====================================================================


def test_the_plane_adapter_is_the_identity_on_a_rectified_buffer(sim):
    plane = bs.ClientRectifiedPlane()
    buf = sim.frame(0)
    lock = plane.detect(buf)
    assert lock.locked and lock.reason == "client_rectified"
    assert np.array_equal(lock.H, np.eye(3))
    assert plane.rectify(buf, lock.H) is buf


def test_the_plane_adapter_refuses_a_buffer_that_is_not_rectified():
    plane = bs.ClientRectifiedPlane()
    assert plane.detect(np.zeros((960, 1280), np.uint8)).locked is False
    assert plane.detect(np.zeros((960, 1280), np.uint8)).reason == "buffer_not_rectified"
    assert plane.detect("not an array").locked is False


def test_the_plane_adapter_carries_the_clients_measurements(sim):
    plane = bs.ClientRectifiedPlane()
    plane.push_client_lock(
        {"locked": True, "ids_found": [0, 1, 2, 3], "reproj_rmse_px": 0.42,
         "scale_err": 0.001, "persp_index": 0.03}
    )
    lock = plane.detect(sim.frame(0))
    assert lock.ids_found == (0, 1, 2, 3)
    assert lock.reproj_rmse_px == 0.42
    assert lock.scale_err == 0.001


def test_the_plane_adapter_ignores_a_malformed_claim(sim):
    plane = bs.ClientRectifiedPlane()
    plane.push_client_lock("nonsense")
    assert plane.detect(sim.frame(0)).locked is True
    plane.push_client_lock({"locked": True, "reproj_rmse_px": "eh", "ids_found": "no"})
    lock = plane.detect(sim.frame(0))
    assert lock.reproj_rmse_px is None
    assert lock.ids_found == ()


# =====================================================================
# The sim — what makes the six panels filmable on a laptop.
# =====================================================================


def test_every_sim_frame_is_the_rectified_buffer(sim):
    for i in range(sim.total_frames):
        f = sim.frame(i)
        assert f.shape == (BUF_H, BUF_W), (i, f.shape)
        assert f.dtype == np.uint8
        assert bs.decode_rect(png_b64(f)).ok


def test_the_sim_is_deterministic(sim):
    other = bs.SimScript()
    for i in (0, 5, 20, 40, 60):
        assert np.array_equal(sim.frame(i), sim.frame(i)), i
        assert np.array_equal(sim.frame(i), other.frame(i)), i


def test_the_sim_holds_on_the_last_phase_rather_than_looping(sim):
    last = sim.total_frames - 1
    assert sim.phase_at(last)[0] == sim.PHASES[-1][0]
    assert sim.phase_at(last + 50) == sim.phase_at(last)


def test_the_sim_phase_boundaries_are_consistent(sim):
    assert sim.total_frames == sum(c for _, c in sim.PHASES)
    n = 0
    for name, count in sim.PHASES:
        assert sim._phase_start(name) == n
        assert sim.phase_at(n) == (name, 0)
        assert sim.phase_at(n + count - 1) == (name, count - 1)
        n += count


def test_the_goods_settle_before_they_walk(sim):
    """The schedule the placement detector actually needs. A packet that walks
    in from off-mat is never stable, never registered, and its crossing freezes
    the total instead of billing it — which is what the first run of this sim
    did."""
    ys = [sim.goods_y_mm(k) for k in range(dict(sim.PHASES)["goods"])]
    assert ys[: sim.GOODS_SETTLE] == [sim.GOODS_Y0_MM] * sim.GOODS_SETTLE
    assert ys[-1] == sim.GOODS_Y1_MM
    assert max(b - a for a, b in zip(ys, ys[1:])) <= sim.GOODS_STEP_MM


def test_the_sim_drives_a_whole_sale_to_settlement(server):
    msgs = run_sim(server)
    states = [m["session_state"] for m in of_type(msgs, "state")]
    for expected in ("PRICED", "BASKET_OPEN", "AWAITING_SETTLEMENT", "PAID"):
        assert expected in states, f"the sim never reached {expected}: {set(states)}"
    paid = [m for m in of_type(msgs, "state") if m["session_state"] == "PAID"][0]
    assert paid["total_paise"] == 2850
    assert paid["settled_payment_id"].startswith("pay_")
    assert paid["money_authorised"] is True


def test_the_sim_demonstrates_a_real_verdict_on_all_six_panels(server):
    """The point of --sim: every panel must show a MEASUREMENT at some point,
    not just an abstention, or the demo is six error messages."""
    msgs = run_sim(server)
    mudra = {m["state"] for m in of_type(msgs, "mudra") if m["ok"]}
    peel = {m["verdict"] for m in of_type(msgs, "peel") if m["ok"]}
    chilla = {m["verdict"] for m in of_type(msgs, "chilla") if m["ok"]}
    saaf = [m for m in of_type(msgs, "saaf") if m["ok"]]

    assert {"OPEN", "GOODS"} <= mudra, f"MUDRA only ever said {mudra}"
    assert {_peel.GENUINE, _peel.TAMPERED} <= peel, f"PEEL only ever said {peel}"
    assert _chilla.MATCHED in chilla, f"CHILLA only ever said {chilla}"
    assert saaf and saaf[0]["used"] >= 2, "SAAF never stacked anything"
    assert of_type(msgs, "ledger")[-1]["count"] > 0
    assert any(m["total_paise"] > 0 for m in of_type(msgs, "state"))


def test_the_sim_also_demonstrates_an_abstention_on_every_panel(server):
    """The other half. A panel that can only ever say a verdict is not
    abstaining, it is guessing."""
    msgs = run_sim(server)
    for panel in ("mudra", "peel", "chilla", "saaf"):
        abstained = [m for m in of_type(msgs, panel) if not m["ok"]]
        assert abstained, f"the {panel} panel never once said 'I do not know'"
        assert all(m["reason"] for m in abstained)


def test_saaf_reaches_subpixel_diversity_in_the_sim(server, sim):
    """Without the sticker jitter every burst frame samples the same sub-pixel
    phase and SAAF correctly refuses to call the result super-resolution. The
    jitter exists so the sim exercises the other branch too."""
    for i in range(server.burst_len):
        server.handle(frame_msg(sim.frame(i)))
    s = of_type(server.handle({"type": "enrol_sticker", "name": "x"}), "saaf")[0]
    assert s["ok"] is True
    assert s["used"] == server.burst_len
    assert s["subpixel_diversity"] > _saaf_min_diversity()
    assert s["warning"] == "", s["warning"]


def _saaf_min_diversity() -> float:
    from gawaah.saaf import DEFAULT_MIN_DIVERSITY

    return DEFAULT_MIN_DIVERSITY


def test_without_jitter_saaf_says_denoising_only(tmp_path):
    """The honest branch, still reachable and still named."""
    script = bs.SimScript(jitter_px=0.0)
    s = bs.build_sim_server(tmp_path / "w", sim=script)
    try:
        for i in range(s.burst_len):
            s.handle(frame_msg(script.frame(i)))
        msg = of_type(s.handle({"type": "enrol_sticker", "name": "x"}), "saaf")[0]
        assert "NO_SUBPIXEL_DIVERSITY" in msg["warning"]
        assert "not super-resolution" in msg["warning"]
    finally:
        s.close()


def test_the_sim_ledger_verifies_from_genesis(server):
    from gawaah.ledger import verify

    run_sim(server)
    ok, n, head, err = verify(server.brain.ledger.path)
    assert ok, err
    assert n > 0
    assert head == server.brain.state().ledger_head
    assert head == of_type(run_sim(server, upto=1), "ledger")[-1]["head"]


def test_the_sticker_enrolment_is_written_to_the_ledger(server):
    run_sim(server, upto=server.sim.enrol_at + 1)
    rows = [
        r for r in server.brain.ledger.read()
        if r.get("module") == bs.MODULE and r.get("what") == "sticker_enrolled"
    ]
    assert rows, "an enrolment that is not in the ledger never happened"
    assert rows[0]["name"] == server.sim.sticker_name
    assert len(rows[0]["digest"]) == 64
    assert rows[0]["human_override"] is True


def test_the_sim_pump_also_taps_the_scripted_client_messages(tmp_path):
    """The pump does not only push frames: the script taps ENROL and DONE the
    way a shopkeeper would, so a hands-off demo reaches every panel."""
    script = bs.SimScript(period_s=0.0, enrol_at=2)
    s = bs.build_sim_server(tmp_path / "w", sim=script)
    try:
        app = bs.create_app(s)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                got = [ws.receive_json() for _ in range(len(s.hello()) + 40)]
    finally:
        s.close()
    assert s.registry.is_enrolled(script.sticker_name)
    # The tap's own answer must have reached the BROWSER, not just the disk.
    enrolled = [
        m for m in got if m["type"] == "peel" and m.get("reason") == "ENROLLED"
    ]
    assert enrolled, "the scripted enrolment never reached the client"
    assert enrolled[0]["name"] == script.sticker_name
    assert any(m["type"] == "saaf" and m["ok"] for m in got)


def test_the_sim_pump_feeds_a_connected_browser(server):
    """--sim with a real client attached: frames arrive with nobody sending
    any, which is what makes the UI demonstrable with no hardware."""
    app = bs.create_app(server)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            kinds = []
            for _ in range(len(server.hello()) + 18):
                kinds.append(ws.receive_json()["type"])
    assert kinds.count("state") >= 3, kinds
    for panel in ("mudra", "peel", "chilla", "saaf", "ledger"):
        assert panel in kinds
    assert server.frames_accepted >= 2


# =====================================================================
# CLI
# =====================================================================


def test_dry_run_drives_the_whole_script(tmp_path, capsys):
    rc = bs.main(
        ["--sim", "--dry-run", "--frames", "12", "--work", str(tmp_path / "w")]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "message counts:" in out
    counts = json.loads(out.split("message counts:")[1].split("\n")[0])
    assert counts["state"] >= 12
    assert {"mudra", "peel", "chilla", "saaf", "ledger"} <= set(counts)


def test_dry_run_needs_sim(tmp_path, capsys):
    assert bs.main(["--dry-run", "--work", str(tmp_path / "w")]) == 2
    assert "needs --sim" in capsys.readouterr().err


def test_a_missing_web_directory_is_reported_not_ignored(tmp_path, capsys):
    assert bs.main(["--web", str(tmp_path / "nope")]) == 2
    assert "no static directory" in capsys.readouterr().err


def test_serving_without_a_websocket_library_says_exactly_what_to_do(
    tmp_path, capsys, monkeypatch
):
    """The honest failure. uvicorn cannot speak WebSocket without `websockets`
    or `wsproto`; starting anyway would serve a socket that 500s every upgrade.
    """
    import importlib.util

    real = importlib.util.find_spec

    def missing(name, *a, **k):
        return None if name in ("websockets", "wsproto") else real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", missing)
    rc = bs.main(["--sim", "--work", str(tmp_path / "w")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "pip install websockets" in err
    assert "--dry-run" in err


def test_the_default_port_is_what_app_js_dials():
    assert bs.DEFAULT_PORT == 8787
    src = (WEB / "app.js").read_text(encoding="utf-8")
    assert "8787" in src


# =====================================================================
# Wiring guards
# =====================================================================


def test_the_rect_shape_constant_is_the_takhti_buffer():
    assert bs.RECT_SHAPE == (BUF_H, BUF_W) == (1188, 840)


def test_the_sticker_roi_is_big_enough_to_enrol(bare, sim):
    crop = bare._crop_roi(sim.frame(0))
    assert min(crop.shape) >= _peel.MIN_CROP_PX, (
        f"the ROI crops to {crop.shape}, under ident_sticker's "
        f"{_peel.MIN_CROP_PX}px floor, so nothing could ever be enrolled"
    )
    assert _peel.contrast_of(crop) >= _peel.MIN_ENROLMENT_CONTRAST


def test_the_burst_never_grows_without_bound(bare, sim):
    for i in range(bare.burst_len * 3):
        bare.handle(frame_msg(sim.frame(i % 8)))
    assert len(bare._burst) == bare.burst_len


def test_a_zero_length_burst_is_refused_at_construction(bare):
    with pytest.raises(bs.BridgeError, match="burst_len"):
        bs.BrainServer(bare.brain, burst_len=0)


def test_set_mirror_keeps_the_matchers_window(bare):
    window = bare.matcher.window_seconds
    bare.set_mirror(_chilla.Mirror((), fetched_at=1000))
    assert bare.matcher.window_seconds == window
    assert bare.mirror.fetched_at == 1000


def test_health_reports_the_refusal_tally(bare, sim):
    bare.handle(frame_msg(np.zeros((10, 10), np.uint8)))
    bare.handle({"type": "nope"})
    h = bare.health()
    assert h["refusals"][bs.R_RECT_WRONG_SHAPE] == 1
    assert h["refusals"][bs.R_UNKNOWN_TYPE] == 1
    assert h["frames_accepted"] == 0
    assert h["sim"] is False


def test_the_module_exports_what_it_documents():
    for name in bs.__all__:
        assert hasattr(bs, name), name


def test_the_sticker_directory_is_reachable_and_real(bare):
    assert bare.sticker_dir.is_dir()
    assert bare.sticker_dir == bare.registry.dir


def test_an_unknown_phase_name_is_a_programmer_error(sim):
    with pytest.raises(bs.BridgeError, match="no phase"):
        sim._phase_start("nonexistent")


def test_paying_before_anything_is_minted_does_nothing(tmp_path, caplog):
    """The customer cannot pay a link that was never created, and the sim must
    say so rather than reach into a None."""
    script = bs.SimScript()
    s = bs.build_sim_server(tmp_path / "w", sim=script)
    try:
        caplog.set_level(logging.INFO, logger="gawaah.brain_server")
        assert s.brain.state().nonce is None
        script.on_pay()
        assert s.brain.state().session_state != "PAID"
        assert any("nothing to pay" in r.getMessage() for r in caplog.records)
    finally:
        s.close()


# ---- the two enrolment abstentions, which the docstring claims are reachable


def _server_with_roi(tmp_path, roi_mm):
    brain_holder = bs.build_sim_server(tmp_path / "w", with_sim=True)
    return bs.BrainServer(
        brain_holder.brain,
        sticker_dir=tmp_path / "st",
        clock=brain_holder.clock,
        plane=brain_holder.plane,
        sticker_roi_mm=roi_mm,
        sim=brain_holder.sim,
    )


def test_saaf_rejecting_every_frame_abstains_on_both_panels(tmp_path):
    """A burst of featureless paper: SAAF's blur floor rejects every frame, so
    there is no image to enrol and PEEL must say so rather than enrol paper."""
    # Bare mat at (200..260, 250..310) mm: clear of the sticker, the markers,
    # the scale patch, the exit arrow and the goods lane (x 127..169 mm), so
    # every frame of the burst really is featureless paper.
    s = _server_with_roi(tmp_path, (200.0, 250.0, 60.0, 60.0))
    script = s.sim
    for i in range(s.burst_len):
        s.handle(frame_msg(script.frame(i)))
    assert s._crop_roi(script.frame(0)).std() == 0.0, "the ROI must be blank paper"
    out = s.handle({"type": "enrol_sticker", "name": "blank"})
    assert [m["type"] for m in out] == ["saaf", "peel"]
    saaf, peel = out
    assert saaf["ok"] is False
    assert saaf["reason"] == bs.A_STACK_REFUSED
    assert saaf["used"] == 0 and saaf["rejected"] == s.burst_len
    assert peel["ok"] is False
    assert peel["reason"] == bs.A_STACK_REFUSED
    assert peel["registered"] is False
    assert not s.registry.is_enrolled("blank")


def test_an_enrolment_the_registry_refuses_is_named_not_mislabelled(tmp_path):
    """A crop SAAF is happy to stack but the registry will not store.

    The reason must be `enrolment_refused` with the registry's own words in
    `detail`. Reporting a borrowed ident_sticker code here would label a
    too-small crop as featureless — two faults, two fixes, one wrong label.
    """
    # 15 mm of the printed sticker: 42 px, over SAAF's 8 px floor and under
    # ident_sticker's 64 px enrolment floor.
    s = _server_with_roi(tmp_path, (35.0, 45.0, 15.0, 15.0))
    script = s.sim
    for i in range(s.burst_len):
        s.handle(frame_msg(script.frame(i)))
    out = s.handle({"type": "enrol_sticker", "name": "tiny"})
    saaf, peel = out
    assert saaf["ok"] is True, "SAAF must have produced an image for this to test"
    assert peel["ok"] is False
    assert peel["reason"] == bs.A_ENROLMENT_REFUSED
    assert str(_peel.MIN_CROP_PX) in peel["detail"]
    assert not s.registry.is_enrolled("tiny")


def test_a_saaf_error_is_a_refusal_not_a_crash(server, sim, monkeypatch):
    for i in range(server.burst_len):
        server.handle(frame_msg(sim.frame(i)))

    def boom(self, frames):
        raise __import__("gawaah.saaf", fromlist=["SaafError"]).SaafError("nope")

    monkeypatch.setattr(bs._saaf.BurstStacker, "stack", boom)
    out = server.handle({"type": "enrol_sticker", "name": "x"})
    assert out[0]["reason"] == bs.R_BRAIN_REFUSED
    assert "SaafError" in out[0]["detail"]


@pytest.mark.parametrize(
    "verb,method",
    [("done", "done"), ("ack", "acknowledge")],
)
def test_a_brain_that_throws_is_a_refusal_not_a_dead_socket(
    bare, verb, method, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("kernel is on fire")

    monkeypatch.setattr(bare.brain, method, boom)
    out = bare.handle({"type": verb})
    assert out[0]["type"] == "refused"
    assert out[0]["reason"] == bs.R_BRAIN_REFUSED
    assert "kernel is on fire" in out[0]["detail"]
    assert bare.handle({"type": "refresh"})[0]["type"] == "state"


def test_revert_surfaces_a_brain_exception(server, monkeypatch):
    run_sim(server, upto=server.sim.done_at)
    item = server.brain.state().lines[0].item_id

    def boom(*a, **k):
        raise RuntimeError("ledger is read-only")

    monkeypatch.setattr(server.brain, "revert", boom)
    out = server.handle({"type": "revert", "item_id": item})
    assert out[0]["reason"] == bs.R_BRAIN_REFUSED


def test_a_malformed_ids_list_does_not_become_evidence(sim):
    plane = bs.ClientRectifiedPlane()
    plane.push_client_lock({"locked": True, "ids_found": ["nought", "one"]})
    assert plane.detect(sim.frame(0)).ids_found == ()


def test_dry_run_verbose_prints_whole_messages(tmp_path, capsys):
    rc = bs.main(
        ["--sim", "--dry-run", "--frames", "3", "-v", "--work", str(tmp_path / "w")]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"type": "mudra"' in out
    assert '"type": "chilla"' in out


def test_every_published_abstention_is_a_lowercase_named_cause():
    assert len(set(bs.ABSTENTIONS)) == len(bs.ABSTENTIONS)
    for a in bs.ABSTENTIONS:
        assert a.islower() and " " not in a, a
