"""Tests for the --sim BEAT MACHINE in gawaah/brain_server.py.

These live in their own file rather than in tests/test_brain_server.py because
that file is shared and another agent may be editing it. Everything here
exercises `SimDriver`, `load_sim_source`, `SimSourceAdapter`, the `/sim/*`
transport and the `{"type": "sim"}` client verb — all of which are new.

The thing being proved is one sentence: a person with no camera, no printed
mat and no phone can open the page and watch every one of the six panels reach
a real, measured, non-abstaining state — while the abstentions stay reachable
and nothing simulated can ever turn the counter green.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gawaah import brain_server as bs  # noqa: E402
from gawaah.takhti import BUF_H, BUF_W  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def simserver(tmp_path):
    """A wired counter with the sim attached and its driver installed."""
    s = bs.build_sim_server(tmp_path / "work", web_dir=WEB, with_sim=True, period_s=0.0)
    yield s
    s.close()


@pytest.fixture
def bare(tmp_path):
    """The same counter with no sim at all. The real-camera shape."""
    s = bs.build_sim_server(tmp_path / "work", web_dir=WEB, with_sim=False)
    yield s
    s.close()


def drive(server, n: int) -> list[dict]:
    """Push `n` scripted frames straight through the driver, synchronously."""
    driver = server.sim_driver
    assert driver is not None
    out: list[dict] = []
    for _ in range(n):
        out += driver.emit_once()
    return out


def of_type(msgs, kind: str) -> list[dict]:
    return [m for m in msgs if m.get("type") == kind]


# =====================================================================
# THE WIRING ITSELF — the driver exists, is attached, and pushes frames.
# =====================================================================


def test_a_sim_server_gets_a_driver_and_a_bare_one_does_not(simserver, bare):
    assert simserver.sim_driver is not None
    assert simserver.sim_driver.mode == bs.SIM_STOPPED
    assert bare.sim_driver is None
    assert bare.sim_status()["reason"] == bs.R_SIM_NOT_ENABLED


def test_one_frame_feeds_all_six_panels(simserver):
    msgs = drive(simserver, 1)
    kinds = [m["type"] for m in msgs]
    assert kinds == ["state", "mudra", "peel", "chilla", "saaf", "ledger"], kinds


def test_the_ledger_panel_is_actually_sent_a_head(simserver):
    """LEDGER used to be the one panel with nothing to render."""
    led = of_type(drive(simserver, 3), "ledger")[-1]
    assert led["head"] and len(led["head"]) == 64
    assert led["count"] > 0
    assert led["verified"] is True, led["verified_reason"]
    assert led["chain_lines"] >= led["count"]


def test_the_ledger_verification_is_recomputed_not_remembered(simserver):
    """`verified` re-reads and re-hashes the file. Corrupt it and it says so."""
    drive(simserver, 3)
    path = simserver.brain.ledger.path
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["what"] = "tampered_after_the_fact"
    lines[1] = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Force the cache to re-read by appending a real line through the writer.
    simserver.brain.ledger.append(
        ts=simserver.clock.now_iso(), module="test", what="poke"
    )
    led = of_type(drive(simserver, 1), "ledger")[-1]
    assert led["verified"] is False
    assert "hash mismatch" in led["verified_reason"] or "chain break" in led["verified_reason"]


# =====================================================================
# INVARIANT 7 — every abstention is still reachable, and still shown.
# =====================================================================


ABSTAIN_AT_BEAT_ZERO = {
    "mudra": bs.A_NO_REFERENCE,
    "chilla": bs.A_NO_REFERENCE,
    "saaf": bs.A_BURST_TOO_SHORT,
}


def test_beat_zero_still_abstains_on_every_panel_that_has_nothing_to_say(simserver):
    """Adding a path to the working state must not delete the honest one."""
    first = {m["type"]: m for m in drive(simserver, 1)}
    for panel, reason in ABSTAIN_AT_BEAT_ZERO.items():
        assert first[panel]["ok"] is False, panel
        assert first[panel]["reason"] == reason, (panel, first[panel]["reason"])
    # PEEL abstains for its own reason: nobody has enrolled a sticker.
    assert first["peel"]["ok"] is False
    assert first["peel"]["reason"] == "NOT_ENROLLED"
    assert first["peel"]["verdict"] == "UNREGISTERABLE"


def test_an_abstaining_panel_sends_nulls_and_not_zeros(simserver):
    first = {m["type"]: m for m in drive(simserver, 1)}
    mu = first["mudra"]
    assert mu["solidity"] is None and mu["area_mm2"] is None and mu["state"] is None
    ch = first["chilla"]
    assert ch["amount_paise"] is None and ch["candidates"] == []


def test_chilla_abstains_with_no_intent_even_once_it_can_see_a_screen(simserver):
    """The named abstention `no_intent_amount` is reachable in a live run."""
    msgs = drive(simserver, simserver.sim.total_frames)
    reasons = {m.get("reason") for m in of_type(msgs, "chilla")}
    assert bs.A_NO_INTENT_AMOUNT in reasons
    assert any(str(r).startswith(bs.A_NO_SCREEN) for r in reasons)
    assert bs.A_NO_REFERENCE in reasons


# =====================================================================
# THE WHOLE STORY — every panel reaches a real, measured verdict.
# =====================================================================


@pytest.fixture(scope="module")
def whole_run(tmp_path_factory):
    """Drive the entire script once and keep every message."""
    s = bs.build_sim_server(
        tmp_path_factory.mktemp("whole"), web_dir=WEB, with_sim=True, period_s=0.0
    )
    try:
        msgs = drive(s, s.sim.total_frames)
        yield s, msgs
    finally:
        s.close()


def test_mudra_reads_a_real_hand(whole_run):
    _s, msgs = whole_run
    states = {m.get("state") for m in of_type(msgs, "mudra") if m.get("ok")}
    assert "OPEN" in states, states
    hands = [m for m in of_type(msgs, "mudra") if m.get("state") == "OPEN"]
    assert hands[0]["defects"] >= 1
    assert 4000.0 <= hands[0]["area_mm2"] <= 22000.0


def test_peel_reaches_both_genuine_and_tampered(whole_run):
    _s, msgs = whole_run
    verdicts = [m.get("verdict") for m in of_type(msgs, "peel")]
    assert "GENUINE" in verdicts
    assert "TAMPERED" in verdicts
    tampered = [m for m in of_type(msgs, "peel") if m.get("verdict") == "TAMPERED"][0]
    assert tampered["ignited_fraction"] > 0.03


def test_saaf_actually_stacks_a_burst(whole_run):
    _s, msgs = whole_run
    stacked = [m for m in of_type(msgs, "saaf") if m.get("ok")]
    assert stacked, "SAAF never ran"
    assert stacked[0]["used"] >= 2
    assert stacked[0]["reason"] == "stacked"


def test_chilla_matches_and_is_still_amber(whole_run):
    """INVARIANT 2 on screen: corroboration is not settlement."""
    _s, msgs = whole_run
    matched = [m for m in of_type(msgs, "chilla") if m.get("verdict") == "MATCHED"]
    assert matched, "CHILLA never corroborated anything"
    for m in of_type(msgs, "chilla"):
        assert m["light"] == "AMBER", m
    assert matched[0]["amount_paise"] == 2850
    assert isinstance(matched[0]["amount_paise"], int)


def test_the_basket_bills_in_integer_paise_and_settles(whole_run):
    _s, msgs = whole_run
    paid = [m for m in of_type(msgs, "state") if m.get("session_state") == "PAID"]
    assert paid, "the scripted sale never settled"
    assert paid[0]["total_paise"] == 2850
    for m in of_type(msgs, "state"):
        assert isinstance(m["total_paise"], int)
        assert not isinstance(m["total_paise"], bool)
        for line in m["lines"]:
            # None is the honest price of a line nobody could identify. What
            # must never appear is a float.
            assert line["price_paise"] is None or (
                isinstance(line["price_paise"], int)
                and not isinstance(line["price_paise"], bool)
            ), line


def test_the_ledger_head_moves_through_the_run(whole_run):
    _s, msgs = whole_run
    heads = [m["head"] for m in of_type(msgs, "ledger")]
    assert len(set(heads)) > 3, "the audit head never moved"


def test_every_beat_of_the_script_is_seen(whole_run):
    _s, msgs = whole_run
    beats = [m["beat"] for m in msgs]
    assert {"settle", "goods", "screen", "hand", "tamper"} <= set(beats), set(beats)
    # and in order, each one announced before the next begins
    order = [b for i, b in enumerate(beats) if i == 0 or beats[i - 1] != b]
    assert order[:5] == ["settle", "goods", "screen", "hand", "tamper"], order


# =====================================================================
# LABELLING — anything simulated says so, everywhere, always.
# =====================================================================


def test_every_message_the_sim_pushes_is_labelled_simulated(whole_run):
    _s, msgs = whole_run
    unlabelled = [m for m in msgs if m.get("simulated") is not True]
    assert unlabelled == [], unlabelled[:3]


def test_every_message_carries_the_beat_it_was_measured_on(whole_run):
    _s, msgs = whole_run
    for m in msgs:
        assert m["beat"] in bs.SIM_BEATS, m["beat"]
        assert m["beat_label"], m
        assert isinstance(m["sim_frame"], int)


def test_a_replayed_panel_keeps_the_beat_it_was_measured_on(simserver):
    """`select_panel` must not relabel an old reading with the CURRENT beat.

    A MUDRA reading taken during `goods` and replayed while the sim is sitting
    on `tamper` must still say `goods`. Relabelling it would attribute a
    measurement to a frame it was not taken from — the same lie `frame_index`
    exists to prevent, one field along.
    """
    drive(simserver, 20)  # into `goods`
    early = simserver.last("mudra")
    assert early["beat"] == "goods"

    # Move the beat WITHOUT pushing a frame, which is what a replay races.
    d = simserver.sim_driver
    d.beat = d._beat_for(d.source.total_frames - 1)
    simserver.set_sim_tag(d._tag())
    assert d.beat["beat"] == "tamper"

    replay = simserver.handle({"type": "select_panel", "id": "mudra"})[-1]
    assert replay["simulated"] is True
    assert replay["beat"] == "goods", replay["beat"]
    assert replay["sim_frame"] == early["sim_frame"] == 19
    assert replay["frame_index"] == early["frame_index"]


def test_a_camera_only_server_labels_nothing_as_simulated(bare):
    """--sim is additive. With no sim, no message claims to be simulated."""
    script = bs.SimScript()
    out = bare.handle(
        {"type": "frame", "rect": bs.encode_rect(script.frame(0)), "ts": None}
    )
    out += bare.hello()
    assert out
    for m in out:
        assert "simulated" not in m, m
        assert "beat" not in m, m


# =====================================================================
# INVARIANT 2 — a simulated FRAME can never turn the counter green.
# =====================================================================


def test_no_frame_of_the_script_authorises_money(whole_run):
    """Money is authorised exactly once, and only after the webhook tap."""
    _s, msgs = whole_run
    authorised = [m for m in of_type(msgs, "state") if m.get("money_authorised")]
    assert authorised, "the scripted sale never settled at all"
    assert authorised[0]["settled_payment_id"], "PAID with no payment id"
    assert authorised[0]["session_state"] == "PAID"


class _BrainThatGoesGreenOnAFrame:
    """A brain that authorises money purely by being handed a frame.

    This must never exist. The test is here to prove that if it ever DID —
    a regression in session.py, a mis-wired settlement port — the sim stops
    and says so instead of painting a green counter nobody paid for.
    """

    def __init__(self, inner):
        self._inner = inner
        self._calls = 0

    def __getattr__(self, k):
        return getattr(self._inner, k)

    def state(self):
        st = self._inner.state()
        self._calls += 1
        if self._calls >= 2:
            return dataclasses.replace(st, money_authorised=True)
        return st


def test_a_simulated_frame_that_authorised_money_faults_the_sim(simserver):
    simserver.brain = _BrainThatGoesGreenOnAFrame(simserver.brain)
    out = simserver.sim_driver.emit_once()
    refusals = of_type(out, "refused")
    assert refusals, [m["type"] for m in out]
    assert refusals[0]["reason"] == bs.R_SIM_GREEN_REFUSED
    assert simserver.sim_driver.mode == bs.SIM_FAULTED
    # and it STAYS stopped: a faulted sim does not quietly carry on.
    before = simserver.sim_driver.frames_emitted
    simserver.sim_driver.command("start")
    assert simserver.sim_driver.mode == bs.SIM_FAULTED
    assert simserver.sim_driver.frames_emitted == before


def test_no_simulated_message_ever_carries_the_settlement_secret(whole_run):
    s, msgs = whole_run
    assert s.forbidden, "the sim rig registered no forbidden string"
    blob = json.dumps(msgs)
    for secret in s.forbidden:
        assert secret not in blob
    assert s.leaks_blocked == 0


# =====================================================================
# THE TRANSPORT — pause on a beat, step one frame, read the numbers.
# =====================================================================


def test_the_sim_verb_is_published_and_handled(simserver):
    assert "sim" in bs.CLIENT_VERBS
    out = simserver.handle({"type": "sim", "action": "status"})
    assert out[0]["type"] == "sim"
    assert out[0]["mode"] in bs.SIM_MODES


def test_the_sim_verb_is_refused_by_name_on_a_camera_server(bare):
    out = bare.handle({"type": "sim", "action": "start"})
    assert out[0]["type"] == "refused"
    assert out[0]["reason"] == bs.R_SIM_NOT_ENABLED
    assert out[0]["actions"] == list(bs.SIM_ACTIONS)


def test_an_unknown_sim_action_is_refused_and_lists_the_known_ones(simserver):
    out = simserver.handle({"type": "sim", "action": "rewind_reality"})
    assert out[0]["reason"] == bs.R_BAD_ARGUMENT
    assert out[0]["actions"] == list(bs.SIM_ACTIONS)


def test_step_queues_exactly_one_frame_and_pause_holds(simserver):
    d = simserver.sim_driver
    d.command("step")
    assert d.mode == bs.SIM_PAUSED
    assert d.pending_steps == 1
    d.pending_steps -= 1
    d.emit_once()
    assert d.index == 1
    d.command("pause")
    assert d.pending_steps == 0


def test_the_script_does_not_advance_before_anybody_is_watching(simserver):
    """The old pump burned the story before the browser had loaded."""
    d = simserver.sim_driver
    assert d.mode == bs.SIM_STOPPED
    assert d.index == 0
    sub = d.subscribe()
    assert d.mode == bs.SIM_RUNNING
    # A fresh subscriber is not pushed a transport message it never asked for.
    assert sub.wants_sim is False
    assert sub.queue.qsize() == 0
    d.send_status_to(sub)
    assert sub.wants_sim is True
    assert sub.queue.get_nowait()["type"] == "sim"


def test_the_http_transport_drives_the_beat_machine(tmp_path):
    script = bs.SimScript(period_s=0.0, enrol_at=None)
    s = bs.build_sim_server(tmp_path / "w", sim=script, period_s=0.0, autostart=False)
    try:
        app = bs.create_app(s)
        with TestClient(app) as client:
            assert client.get("/sim").json()["mode"] == bs.SIM_PAUSED
            first = client.post("/sim/step").json()
            assert first["index"] == 1, first
            assert first["beat"] == "settle"
            tenth = client.post("/sim/step?n=9").json()
            assert tenth["index"] == 10
            assert client.post("/sim/start").json()["mode"] == bs.SIM_RUNNING
            assert client.post("/sim/stop").json()["mode"] == bs.SIM_PAUSED
            reset = client.post("/sim/reset?fresh=false").json()
            assert reset["index"] == 0
    finally:
        s.close()


def test_the_http_transport_says_so_when_there_is_no_sim(bare):
    app = bs.create_app(bare)
    with TestClient(app) as client:
        r = client.post("/sim/start")
        assert r.status_code == 409
        assert r.json()["reason"] == bs.R_SIM_NOT_ENABLED


def test_a_fresh_reset_gives_a_genuinely_new_counter(tmp_path):
    """A replay must not re-settle the first run's sale."""
    script = bs.SimScript(period_s=0.0)
    s = bs.build_sim_server(tmp_path / "w", sim=script, period_s=0.0)
    try:
        drive(s, s.sim.total_frames)
        old = s.brain.state()
        assert old.settled_payment_id is not None
        old_ledger = s.brain.ledger.path
        s.sim_driver.command("reset", fresh=True)
        new = s.brain.state()
        assert new.session_id != old.session_id
        assert new.settled_payment_id is None
        assert new.total_paise == 0
        assert s.brain.ledger.path != old_ledger
        # everything measured against the old run's pixels is dropped
        assert s.gesture is None
        assert s.last("mudra") is None
        assert s._peel_name is None
        # and the first run's audit trail is untouched
        from gawaah.ledger import verify

        ok, n, _head, err = verify(old_ledger)
        assert ok, err
        assert n > 0
    finally:
        s.close()


# =====================================================================
# ONE PUMP — the bug that made the demo unwatchable.
# =====================================================================


def test_two_browsers_see_the_same_frames_and_do_not_double_advance(tmp_path):
    """N sockets used to mean N pumps sharing one script at N times speed."""
    script = bs.SimScript(period_s=0.0, enrol_at=None)
    s = bs.build_sim_server(tmp_path / "w", sim=script, period_s=0.0)
    try:
        app = bs.create_app(s)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as a:
                with client.websocket_connect("/ws") as b:
                    seen = {"a": [], "b": []}
                    hello_n = len(s.hello())
                    for name, sock in (("a", a), ("b", b)):
                        for _ in range(hello_n):
                            sock.receive_json()  # the opening burst
                        for _ in range(40):
                            m = sock.receive_json()
                            if m["type"] == "state":
                                seen[name].append(m["sim_frame"])
                    # Both clients were shown the same frames of the same script.
                    common = set(seen["a"]) & set(seen["b"])
                    assert len(common) >= 3, seen
                    # And no frame index was produced twice.
                    for name in ("a", "b"):
                        assert len(seen[name]) == len(set(seen[name])), seen[name]
    finally:
        s.close()


def test_a_connected_browser_is_fed_without_sending_anything(tmp_path):
    script = bs.SimScript(period_s=0.0, enrol_at=2)
    s = bs.build_sim_server(tmp_path / "w", sim=script, period_s=0.0)
    try:
        app = bs.create_app(s)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                got = [ws.receive_json() for _ in range(len(s.hello()) + 40)]
    finally:
        s.close()
    kinds = {m["type"] for m in got}
    assert {"state", "mudra", "peel", "chilla", "saaf", "ledger"} <= kinds
    # ...and it was NOT sent a transport message it does not know how to read.
    assert "sim" not in kinds, "a passive client was pushed the transport stream"
    pushed = [m for m in got if m.get("simulated") is True]
    assert len(pushed) >= 30
    assert any(m["type"] == "peel" and m.get("reason") == "ENROLLED" for m in got)
    # It still knows the board is synthetic and which beat it is on.
    assert all(m["beat"] for m in got)


def test_a_beat_change_announces_itself_before_the_beat_s_first_frame(simserver):
    """The transport bar must name the beat BEFORE the numbers for it arrive."""
    settle = dict(bs.SimScript.PHASES)["settle"]
    msgs = drive(simserver, settle + 1)
    status = [m for m in msgs if m["type"] == "sim"]
    assert status, "no beat change was announced"
    assert status[0]["beat"] == "goods"
    # ...and it came before that beat's state message, not after.
    i = msgs.index(status[0])
    assert msgs[i + 1]["type"] == "state"
    assert msgs[i + 1]["beat"] == "goods"
    assert all(m["beat"] == "settle" for m in msgs[:i])


def test_a_mode_change_reaches_a_browser_that_did_not_cause_it(tmp_path):
    """Pause typed in one tab must move the transport bar in the other."""
    script = bs.SimScript(period_s=0.0, enrol_at=None)
    s = bs.build_sim_server(tmp_path / "w", sim=script, period_s=0.0)
    try:
        app = bs.create_app(s)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                for _ in range(len(s.hello())):
                    ws.receive_json()
                # Opt in, which is what a client with a transport bar does.
                ws.send_json({"type": "sim", "action": "status"})
                assert client.post("/sim/stop").json()["mode"] == bs.SIM_PAUSED
                paused = None
                for _ in range(400):
                    m = ws.receive_json()
                    if m["type"] == "sim" and m["mode"] == bs.SIM_PAUSED:
                        paused = m
                        break
    finally:
        s.close()
    assert paused is not None, "the pause never reached the socket"
    assert paused["simulated"] is True


def test_the_client_can_drive_the_sim_over_the_socket(tmp_path):
    script = bs.SimScript(period_s=0.0, enrol_at=None)
    s = bs.build_sim_server(tmp_path / "w", sim=script, period_s=0.0, autostart=False)
    try:
        app = bs.create_app(s)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                for _ in range(len(s.hello())):
                    ws.receive_json()
                ws.send_json({"type": "sim", "action": "step", "n": 2})
                seen: list[dict] = []
                for _ in range(20):
                    seen.append(ws.receive_json())
                    if len([m for m in seen if m["type"] == "state"]) >= 2:
                        break
    finally:
        s.close()
    assert s.sim_driver.frames_emitted == 2
    assert [m["sim_frame"] for m in seen if m["type"] == "state"] == [0, 1]
    # Speaking the verb turned the transport stream on for this connection.
    assert any(m["type"] == "sim" for m in seen)


# =====================================================================
# gawaah.sim_source — the other agent's module, if it ever lands.
# =====================================================================


def test_load_sim_source_always_returns_something_that_works():
    """`gawaah.sim_source` is another agent's file. It may be absent, it may be
    half-written, and it may be perfect. All three must produce a running demo,
    and the answer must SAY which one happened rather than pretending."""
    source, why = bs.load_sim_source()
    assert why, "the loader must explain which source it chose"
    assert source.total_frames > 0
    f0 = source.frame(0)
    assert f0.shape[:2] == (BUF_H, BUF_W)
    if isinstance(source, bs.SimScript):
        assert "SimScript" in why
    else:
        assert isinstance(source, bs.SimSourceAdapter)
        assert source.prove()[0]


def test_a_server_built_on_the_chosen_source_feeds_every_panel(tmp_path):
    """Whichever source won, the six panels get fed and are labelled."""
    s = bs.build_sim_server(
        tmp_path / "w", with_sim=True, period_s=0.0, prefer_sim_source=True
    )
    try:
        msgs = drive(s, 6)
        kinds = {m["type"] for m in msgs}
        assert {"state", "mudra", "peel", "chilla", "saaf", "ledger"} <= kinds
        assert all(m["simulated"] is True for m in msgs)
        assert all(m["beat"] for m in msgs)
        # And PEEL is cropping where the source actually painted its sticker.
        assert tuple(s.roi_mm) == tuple(
            float(v) for v in getattr(s.sim, "roi_mm", bs.DEFAULT_STICKER_ROI_MM)
        )
    finally:
        s.close()


class _GoodSource:
    period_s = 0.0
    total_frames = 3

    def __init__(self, **_kw):
        self._s = bs.SimScript()

    def frame(self, i):
        return self._s.frame(i)

    def beat_at(self, i):
        return {"name": "settle", "index": i, "of": 3}

    def commands_at(self, i):
        return []


class _WrongShapeSource:
    period_s = 0.0
    total_frames = 3

    def frame(self, i):
        return np.zeros((480, 640), np.uint8)


def test_the_adapter_accepts_a_well_formed_source():
    a = bs.SimSourceAdapter(_GoodSource())
    ok, why = a.prove()
    assert ok, why
    assert a.total_frames == 3
    assert a.frame(0).shape[:2] == (BUF_H, BUF_W)
    assert a.beat_at(1)["name"] == "settle"
    assert a.commands_at(0) == []


def test_the_adapter_refuses_a_source_that_is_not_the_rectified_crop():
    """A source that could hand the brain a raw camera frame is not used."""
    ok, why = bs.SimSourceAdapter(_WrongShapeSource()).prove()
    assert ok is False
    assert "rectified" in why


def test_a_driver_runs_on_an_adapted_foreign_source(tmp_path):
    s = bs.build_sim_server(tmp_path / "w", with_sim=True, period_s=0.0)
    try:
        s.sim_driver.source = bs.SimSourceAdapter(_GoodSource())
        s.sim_driver._reset(fresh=False)
        msgs = s.sim_driver.emit_once()
        assert [m["type"] for m in msgs][0] == "state"
        assert all(m["simulated"] is True for m in msgs)
        assert msgs[0]["beat"] == "settle"
    finally:
        s.close()


# =====================================================================
# The real-camera path is unchanged.
# =====================================================================


def _png(img: np.ndarray) -> str:
    import base64

    import cv2

    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def test_a_raw_camera_frame_is_still_refused_on_a_sim_server(simserver):
    """INVARIANT 4's gate does not soften because a sim is attached."""
    out = simserver.handle(
        {"type": "frame", "rect": _png(np.zeros((960, 1280), np.uint8))}
    )
    assert out[0]["reason"] == bs.R_RECT_WRONG_SHAPE
    # ...and the refusal is labelled too, because it happened on a sim board.
    assert out[0]["simulated"] is True


def test_a_real_client_frame_still_drives_the_panels_on_a_bare_server(bare):
    script = bs.SimScript()
    out = []
    for i in range(12):
        out += bare.handle(
            {"type": "frame", "rect": bs.encode_rect(script.frame(i)), "ts": None}
        )
    kinds = {m["type"] for m in out}
    assert {"state", "mudra", "peel", "chilla", "saaf", "ledger"} <= kinds
    assert bare.frames_accepted == 12
