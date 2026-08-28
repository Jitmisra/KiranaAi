"""S4d acceptance: the session state machine bills correctly or refuses loudly.

Every test in this file runs under a fixture that verifies the hash chain from
genesis at teardown, so "the ledger verifies after every scenario" is not one
test, it is a precondition of all of them.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from gawaah.clock import VirtualClock
from gawaah.ledger import Ledger, verify
from gawaah.money import MoneyError, from_rupees_str
from gawaah.session import (
    DEGRADED_P95_MS,
    GREEN_EVENTS,
    Placement,
    Reason,
    Session,
    State,
    Verdict,
)

# ------------------------------------------------------------------ helpers


@pytest.fixture
def audit(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def s(audit: Path):
    """A session whose ledger is re-verified from genesis when the test ends."""
    ledger = Ledger(audit)
    session = Session(VirtualClock(), ledger)
    yield session
    ok, n, head, err = verify(audit)
    assert ok, f"ledger failed to verify: {err}"
    assert n == ledger.count, f"verify saw {n} lines, writer counted {ledger.count}"
    assert head == ledger.head


def lines(audit: Path) -> list[dict]:
    return list(Ledger(audit).read())


def step(session: Session, audit: Path, fn, *args, **kwargs):
    """Call a handler and assert R7: every transition writes exactly one line.

    The handler declares how many lines it wrote; this asserts the ledger
    agrees, so a silent write or a silent skip fails the test.
    """
    before = len(lines(audit))
    t = fn(*args, **kwargs)
    after = len(lines(audit))
    assert after - before == t.lines_written, (
        f"{fn.__name__}{args} appended {after - before} ledger lines "
        f"but declared {t.lines_written}"
    )
    assert (t.lines_written > 0) == t.applied
    if t.applied:
        assert t.ledger_hash == Ledger(audit).head
        assert lines(audit)[-1]["reason"] == t.reason
    else:
        assert t.ledger_hash is None
    return t


def ready(session: Session) -> None:
    session.on_mat_lock(True)


def place_and_exit(session: Session, item_id: str, price: int | None, name=None):
    session.on_placement(Placement(item_id, name, price))
    return session.on_exit(item_id)


def green(session: Session, amount: int, event_id: str = "evt_1") -> Verdict:
    return Verdict(
        event_id=event_id,
        event="payment_link.paid",
        session_id=session.session_id,
        amount_paise=amount,
        green=True,
        signature_valid=True,
    )


# ------------------------------------------------------------------ setup


def test_starts_in_setup_and_logs_its_own_birth(s, audit):
    assert s.state is State.SETUP
    recs = lines(audit)
    assert len(recs) == 1
    assert recs[0]["module"] == "session"
    assert recs[0]["reason"] == Reason.SESSION_OPENED
    assert recs[0]["to"] == "SETUP"


def test_setup_refuses_every_billing_event(s, audit):
    t = step(s, audit, s.on_placement, Placement("t1", "x", 1000))
    assert t.reason == Reason.MAT_NOT_LOCKED
    assert s.state is State.SETUP
    assert int(s.total_paise) == 0


def test_mat_lock_leaves_setup(s, audit):
    t = step(s, audit, s.on_mat_lock, True)
    assert t.frm is State.SETUP and t.to is State.IDLE
    assert s.mat_locked


# ------------------------------------------------------------------ happy path


def test_happy_path_three_items_done_green(s, audit):
    """A whole sale, end to end, on real rupee strings."""
    ready(s)
    prices = [from_rupees_str("10.00"), from_rupees_str("45.50"), from_rupees_str("159.00")]
    for i, p in enumerate(prices):
        step(s, audit, s.on_placement, Placement(f"t{i}", f"sku{i}", p))
        assert s.state is State.PRICED
        step(s, audit, s.on_exit, f"t{i}")
        assert s.state is State.BASKET_OPEN

    assert int(s.total_paise) == sum(prices) == 21450
    assert s.amber_count == 0
    assert s.money_authorised is False

    t = step(s, audit, s.on_done)
    assert t.to is State.AWAITING_SETTLEMENT
    assert s.intent_amount_paise == 21450
    assert s.money_authorised is False

    t = step(s, audit, s.on_webhook, green(s, 21450))
    assert t.to is State.PAID
    assert s.money_authorised is True
    assert s.authorised_paise == 21450
    assert s.authorised_paise == s.intent_amount_paise

    settled = [r for r in lines(audit) if r["reason"] == Reason.SETTLED]
    assert len(settled) == 1
    assert settled[0]["authorised_paise"] == 21450


def test_total_is_recomputed_from_committed_lines_not_incremented(s, audit):
    ready(s)
    for i, p in enumerate([500, 700, 900]):
        place_and_exit(s, f"t{i}", p)
    expected = sum(li.price_paise for li in s.committed_items if li.price_paise)
    assert int(s.total_paise) == expected
    # mutate a line behind the machine's back: a recomputed total follows it,
    # an ad-hoc running counter would not.
    s.line_items[1].price_paise = 1
    assert int(s.total_paise) == 500 + 1 + 900


# ------------------------------------------------------------------ amber


def test_amber_item_never_reaches_the_total(s, audit):
    ready(s)
    place_and_exit(s, "known", 5000)
    t = step(s, audit, s.on_placement, Placement("mystery", "?", None))
    assert t.to is State.AMBER
    assert t.detail["excluded_from_total"] is True
    step(s, audit, s.on_exit, "mystery")

    assert s.amber_count == 1
    assert len(s.committed_items) == 2
    assert int(s.total_paise) == 5000  # not 5000 + anything

    step(s, audit, s.on_done)
    assert s.intent_amount_paise == 5000

    done = [r for r in lines(audit) if r["reason"] == Reason.INTENT_REQUESTED][0]
    assert done["amber_excluded"] == 1
    assert done["intent_amount_paise"] == 5000

    # no ledger line ever attributed money to the amber line, and every line
    # that classified or committed it said out loud that it was excluded.
    mystery = [r for r in lines(audit) if r.get("item_id") == "mystery"]
    assert len(mystery) == 3  # placement, classify, exit
    for r in mystery:
        assert "price_paise" not in r
    for r in mystery:
        if r["event"] in ("classify", "exit"):
            assert r["excluded_from_total"] is True
            assert r["abstained"] is True


def test_amber_priced_by_a_tap_joins_the_total(s, audit):
    """WARM ENROLL: the shopkeeper confirms a price once, on an item he was selling."""
    ready(s)
    s.on_placement(Placement("t1", None, None))
    s.on_exit("t1")
    assert int(s.total_paise) == 0 and s.amber_count == 1

    t = step(s, audit, s.on_price, "t1", 2500)
    assert t.detail["was_amber"] is True
    assert t.detail["human_override"] is True
    assert int(s.total_paise) == 2500
    assert s.amber_count == 0


def test_an_all_amber_basket_cannot_be_charged(s, audit):
    ready(s)
    place_and_exit(s, "a", None)
    place_and_exit(s, "b", None)
    t = step(s, audit, s.on_done)
    assert t.reason == Reason.ZERO_TOTAL
    assert s.state is State.BASKET_OPEN
    assert s.intent_amount_paise is None


# ------------------------------------------------------------------ revert


def test_revert_decrements_exactly_and_logs_human_override(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    place_and_exit(s, "b", 2550)
    place_and_exit(s, "c", 300)
    assert int(s.total_paise) == 3850

    t = step(s, audit, s.on_revert, "b")
    assert t.detail["human_override"] is True
    assert t.detail["removed_paise"] == 2550
    assert int(s.total_paise) == 3850 - 2550 == 1300
    assert len(s.committed_items) == 2

    rec = [r for r in lines(audit) if r["reason"] == Reason.REVERTED][0]
    assert rec["human_override"] is True
    assert rec["total_paise"] == 1300


def test_reverting_the_last_line_returns_to_idle(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    t = step(s, audit, s.on_revert, "a")
    assert t.to is State.IDLE
    assert int(s.total_paise) == 0


def test_revert_is_refused_once_the_basket_is_locked(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    t = step(s, audit, s.on_revert, "a")
    assert t.reason == Reason.BASKET_LOCKED
    assert int(s.total_paise) == 1000
    assert s.intent_amount_paise == 1000


def test_a_reverted_line_is_dead_and_cannot_be_resurrected(s, audit):
    """If the customer puts it back, the tracker mints a new id, not this one."""
    ready(s)
    place_and_exit(s, "a", 1000)
    place_and_exit(s, "b", 400)
    s.on_revert("a")
    assert int(s.total_paise) == 400

    t = step(s, audit, s.on_exit, "a")
    assert t.reason == Reason.REVERTED_ITEM
    t = step(s, audit, s.on_price, "a", 9999)
    assert t.reason == Reason.REVERTED_ITEM
    assert int(s.total_paise) == 400
    assert len(s.committed_items) == 1

    # a genuine re-add arrives as a fresh placement and bills normally
    place_and_exit(s, "a2", 1000)
    assert int(s.total_paise) == 1400


def test_revert_of_unknown_item_is_refused_not_silent(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    t = step(s, audit, s.on_revert, "ghost")
    assert t.reason == Reason.UNKNOWN_ITEM
    assert int(s.total_paise) == 1000


def test_an_item_on_the_mat_that_never_exits_is_not_billed(s, audit):
    """Only the exit crossing commits a line. Sitting on the mat is not a sale."""
    ready(s)
    place_and_exit(s, "sold", 1000)
    step(s, audit, s.on_placement, Placement("browsing", "picked up", 99900))
    assert int(s.total_paise) == 1000
    assert len(s.line_items) == 2 and len(s.committed_items) == 1
    s.on_done()
    assert s.intent_amount_paise == 1000


def test_the_basket_cannot_be_repriced_behind_a_minted_intent(s, audit):
    """Once DONE names an amount, nothing may change what that amount was for."""
    ready(s)
    place_and_exit(s, "a", 1000)
    place_and_exit(s, "b", None)
    s.on_done()
    assert s.intent_amount_paise == 1000

    for call, args in ((s.on_price, ("b", 5000)), (s.on_price, ("a", 1)),
                       (s.on_exit, ("b",)), (s.on_revert, ("a",))):
        t = step(s, audit, call, *args)
        assert t.reason == Reason.BASKET_LOCKED, (call, args)
    assert int(s.total_paise) == 1000
    assert s.intent_amount_paise == 1000


def test_the_done_line_records_the_total_it_asked_to_be_paid(s, audit):
    ready(s)
    place_and_exit(s, "a", from_rupees_str("99.99"))
    s.on_done()
    rec = lines(audit)[-1]
    assert rec["reason"] == Reason.INTENT_REQUESTED
    assert rec["intent_amount_paise"] == rec["total_paise"] == 9999


def test_an_uncounted_crossing_in_setup_is_refused_not_frozen(s, audit):
    t = step(s, audit, s.on_exit, None)
    assert t.reason == Reason.MAT_NOT_LOCKED
    assert s.state is State.SETUP and s.frozen is False


# ------------------------------------------------------------------ webhook


def test_wrong_amount_webhook_lands_in_amount_mismatch_not_paid(s, audit):
    ready(s)
    place_and_exit(s, "a", 21450)
    s.on_done()
    wrong = Verdict(
        event_id="evt_bad",
        event="payment_link.paid",
        session_id=s.session_id,
        amount_paise=21400,  # 50 paise short
        green=True,          # even though paisa said green
        signature_valid=True,
    )
    t = step(s, audit, s.on_webhook, wrong)
    assert t.to is State.AMOUNT_MISMATCH
    assert s.state is State.AMOUNT_MISMATCH
    assert s.money_authorised is False
    assert s.authorised_paise is None
    assert t.detail["expected_paise"] == 21450
    assert t.detail["webhook_amount_paise"] == 21400


def test_amount_mismatch_is_a_red_hold_that_refuses_more_billing(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    s.on_webhook(
        Verdict("evt_bad", "payment_link.paid", s.session_id, 999, True, True)
    )
    assert s.state is State.AMOUNT_MISMATCH
    for call, args in (
        (s.on_placement, (Placement("b", None, 100),)),
        (s.on_exit, ("a",)),
        (s.on_done, ()),
    ):
        t = step(s, audit, call, *args)
        assert t.reason == Reason.RED_HOLD, call
    assert s.money_authorised is False


def test_invalid_signature_is_discarded_and_never_changes_state(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    forged = Verdict(
        event_id="evt_forged",
        event="payment_link.paid",
        session_id=s.session_id,
        amount_paise=1000,
        green=True,
        signature_valid=False,
    )
    t = step(s, audit, s.on_webhook, forged)
    assert t.changed_state is False
    assert t.reason == Reason.BAD_SIGNATURE
    assert s.state is State.AWAITING_SETTLEMENT
    assert s.money_authorised is False
    assert lines(audit)[-1]["discarded"] is True


def test_webhook_for_another_session_is_discarded(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    t = step(
        s,
        audit,
        s.on_webhook,
        Verdict("evt_x", "payment_link.paid", "some-other-session", 1000, True, True),
    )
    assert t.reason == Reason.FOREIGN_SESSION
    assert s.money_authorised is False


def test_green_webhook_with_no_open_intent_is_refused(s, audit):
    """Never green on a payment we did not ask for."""
    ready(s)
    place_and_exit(s, "a", 1000)
    assert s.intent_amount_paise is None
    t = step(s, audit, s.on_webhook, green(s, 1000))
    assert t.reason == Reason.NO_OPEN_INTENT
    assert s.state is State.BASKET_OPEN
    assert s.money_authorised is False


@pytest.mark.parametrize("event", ["payment.failed", "payment_link.expired", "qr_code.closed"])
def test_events_outside_the_green_set_never_settle(s, audit, event):
    assert event not in GREEN_EVENTS
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    t = step(
        s,
        audit,
        s.on_webhook,
        Verdict(f"evt_{event}", event, s.session_id, 1000, True, True),
    )
    assert t.reason == Reason.NOT_IN_GREEN_SET
    assert t.changed_state is False
    assert s.money_authorised is False


def test_session_cannot_grant_green_that_paisa_withheld(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    t = step(
        s,
        audit,
        s.on_webhook,
        Verdict("evt_ng", "payment_link.paid", s.session_id, 1000, False, True,
                reason="notes.session_id absent"),
    )
    assert t.reason == Reason.PAISA_REFUSED_GREEN
    assert s.state is State.AWAITING_SETTLEMENT
    assert s.money_authorised is False


def test_webhook_amount_must_be_integer_paise(s):
    with pytest.raises(MoneyError):
        Verdict("e", "payment_link.paid", s.session_id, 214.50, True, True)


def test_a_settlement_event_with_no_amount_is_a_mismatch_not_a_settlement(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    t = step(
        s, audit, s.on_webhook,
        Verdict("evt_noamt", "payment_link.paid", s.session_id, None, True, True),
    )
    assert t.to is State.AMOUNT_MISMATCH
    assert s.money_authorised is False


def test_off_by_one_paisa_is_still_a_mismatch(s, audit):
    ready(s)
    place_and_exit(s, "a", from_rupees_str("214.50"))
    s.on_done()
    t = step(s, audit, s.on_webhook, green(s, 21449))
    assert t.to is State.AMOUNT_MISMATCH
    assert t.detail["expected_paise"] == 21450
    assert s.money_authorised is False


def test_money_is_authorised_in_no_state_but_paid(s, audit):
    """R4, walked across the whole lifecycle."""
    seen: list[State] = []

    def check() -> None:
        seen.append(s.state)
        if s.state is not State.PAID:
            assert s.money_authorised is False, s.state
            assert s.authorised_paise is None, s.state

    check()
    ready(s); check()
    s.on_placement(Placement("a", None, None)); check()      # AMBER
    s.on_placement(Placement("b", None, 5000)); check()      # PRICED
    s.on_exit("a"); s.on_exit("b"); check()                  # BASKET_OPEN
    s.on_perf(400); check()                                  # DEGRADED
    s.on_perf(10); check()
    s.on_mat_lock(False); check()                            # MAT_LOST
    s.on_mat_lock(True); check()
    s.on_brain(False); check()                               # BRAIN_LOST
    s.on_brain(True); check()
    s.on_network(False); check()
    s.on_done(); check()                                     # PENDING_OFFLINE
    s.on_network(True); check()                              # AWAITING_SETTLEMENT
    s.on_webhook(green(s, 5000))
    assert s.state is State.PAID
    assert s.money_authorised is True

    assert State.PAID not in seen
    for st in (State.SETUP, State.IDLE, State.AMBER, State.PRICED, State.BASKET_OPEN,
               State.DEGRADED, State.MAT_LOST, State.BRAIN_LOST,
               State.PENDING_OFFLINE, State.AWAITING_SETTLEMENT):
        assert st in seen, st


# ------------------------------------------------------------------ freezes


def test_mat_loss_mid_basket_freezes_the_total(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    place_and_exit(s, "b", 2000)
    assert int(s.total_paise) == 3000

    t = step(s, audit, s.on_mat_lock, False)
    assert t.to is State.MAT_LOST
    assert s.frozen is True
    assert t.detail["frozen_total_paise"] == 3000

    # every billing event is refused while frozen, and the total does not move
    for call, args, reason in (
        (s.on_placement, (Placement("c", None, 9999),), Reason.REFUSED_MAT_LOST),
        (s.on_exit, ("a",), Reason.REFUSED_MAT_LOST),
        (s.on_price, ("a", 7777), Reason.REFUSED_MAT_LOST),
        (s.on_revert, ("a",), Reason.REFUSED_MAT_LOST),
        (s.on_done, (), Reason.REFUSED_MAT_LOST),
    ):
        t = step(s, audit, call, *args)
        assert t.reason == reason
        assert int(s.total_paise) == 3000
        assert s.state is State.MAT_LOST

    t = step(s, audit, s.on_mat_lock, True)
    assert t.to is State.BASKET_OPEN
    assert s.frozen is False
    assert int(s.total_paise) == 3000  # billing resumes exactly where it froze


def test_brain_loss_freezes_the_total(s, audit):
    ready(s)
    place_and_exit(s, "a", 4200)
    t = step(s, audit, s.on_brain, False)
    assert t.to is State.BRAIN_LOST
    assert s.frozen and int(s.total_paise) == 4200
    t = step(s, audit, s.on_placement, Placement("b", None, 100))
    assert t.reason == Reason.REFUSED_BRAIN_LOST
    assert int(s.total_paise) == 4200
    t = step(s, audit, s.on_brain, True)
    assert t.to is State.BASKET_OPEN
    assert not s.frozen


def test_two_freeze_causes_thaw_independently(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_mat_lock(False)
    s.on_brain(False)
    assert s.state is State.MAT_LOST  # mat is the one to show the operator
    t = step(s, audit, s.on_mat_lock, True)
    assert t.to is State.BRAIN_LOST   # still frozen: the brain is still down
    assert s.frozen
    t = step(s, audit, s.on_brain, True)
    assert t.to is State.BASKET_OPEN
    assert not s.frozen


def test_uncounted_crossing_freezes_the_total(s, audit):
    """Abstention 11: goods left the counter and we cannot say which."""
    ready(s)
    place_and_exit(s, "a", 1000)
    t = step(s, audit, s.on_exit, None)
    assert t.to is State.FROZEN_TOTAL
    assert t.reason == Reason.UNCOUNTED_CROSSING
    assert s.frozen and int(s.total_paise) == 1000
    t = step(s, audit, s.on_done)
    assert t.reason == Reason.REFUSED_FROZEN_TOTAL
    t = step(s, audit, s.on_acknowledge)
    assert t.to is State.BASKET_OPEN
    assert t.detail["human_override"] is True
    assert not s.frozen


def test_exit_for_an_item_never_measured_freezes_rather_than_dropping_it(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    t = step(s, audit, s.on_exit, "never_seen")
    assert t.to is State.FROZEN_TOTAL
    assert t.reason == Reason.UNTRACKED_EXIT


def test_mat_loss_after_done_does_not_freeze_and_cannot_unpay_a_sale(s, audit):
    """A settled sale must not be un-settled by the mat coming back.

    Settlement does not need the mat: the amount was locked at DONE. If mat
    loss froze here, the later re-acquire would 'resume' the pre-DONE state
    and walk PAID backwards into AWAITING_SETTLEMENT.
    """
    ready(s)
    place_and_exit(s, "a", 6000)
    s.on_done()
    t = step(s, audit, s.on_mat_lock, False)
    assert t.changed_state is False
    assert t.reason == Reason.SIGNAL_AFTER_BASKET_CLOSED
    assert s.state is State.AWAITING_SETTLEMENT
    assert s.frozen is False

    t = step(s, audit, s.on_webhook, green(s, 6000))
    assert t.to is State.PAID
    assert s.money_authorised is True

    t = step(s, audit, s.on_mat_lock, True)
    assert s.state is State.PAID, "the mat coming back must not un-pay the sale"
    assert s.money_authorised is True
    assert s.authorised_paise == 6000


def test_a_lost_mat_still_blocks_a_new_basket_after_paid(s, audit):
    ready(s)
    place_and_exit(s, "a", 6000)
    s.on_done()
    s.on_mat_lock(False)
    s.on_webhook(green(s, 6000))
    assert s.state is State.PAID
    t = step(s, audit, s.on_placement, Placement("b", None, 100))
    assert t.reason == Reason.REFUSED_MAT_LOST
    assert s.state is State.PAID
    assert len(s.line_items) == 1


def test_mat_loss_during_a_frozen_total_does_not_thaw_the_exception(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_exit(None)
    assert s.state is State.FROZEN_TOTAL
    s.on_mat_lock(False)
    assert s.state is State.MAT_LOST
    t = step(s, audit, s.on_mat_lock, True)
    assert t.to is State.FROZEN_TOTAL, "the uncounted crossing is still unresolved"
    assert s.frozen is True


# ------------------------------------------------------------------ offline


def test_offline_done_goes_pending_and_authorises_nothing(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    step(s, audit, s.on_network, False)
    place_and_exit(s, "b", 500)  # R6: billing continues locally
    assert int(s.total_paise) == 1500

    t = step(s, audit, s.on_done)
    assert t.to is State.PENDING_OFFLINE
    assert t.reason == Reason.OFFLINE_NO_AUTHORISATION
    assert s.money_authorised is False
    assert s.intent_amount_paise == 1500

    t = step(s, audit, s.on_network, True)
    assert t.to is State.AWAITING_SETTLEMENT
    assert s.intent_amount_paise == 1500
    assert s.money_authorised is False

    s.on_webhook(green(s, 1500))
    assert s.state is State.PAID and s.money_authorised is True


def test_network_drop_while_awaiting_settlement_goes_pending(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    assert s.state is State.AWAITING_SETTLEMENT
    t = step(s, audit, s.on_network, False)
    assert t.to is State.PENDING_OFFLINE
    assert s.money_authorised is False


# ------------------------------------------------------------------ degraded


def test_degraded_disables_auto_commit_and_requires_a_tap(s, audit):
    ready(s)
    s.on_placement(Placement("a", None, 1000))
    t = step(s, audit, s.on_perf, DEGRADED_P95_MS + 150)
    assert t.to is State.DEGRADED
    assert s.degraded

    t = step(s, audit, s.on_exit, "a")
    assert t.reason == Reason.DEGRADED_REQUIRES_TAP
    assert int(s.total_paise) == 0

    t = step(s, audit, s.on_exit, "a", tap=True)
    assert t.to is State.BASKET_OPEN
    assert int(s.total_paise) == 1000

    t = step(s, audit, s.on_perf, 20)
    assert s.degraded is False
    s.on_placement(Placement("b", None, 400))
    step(s, audit, s.on_exit, "b")  # auto-commit works again
    assert int(s.total_paise) == 1400


# ------------------------------------------------------------------ idempotency


def test_replaying_every_event_twice_is_idempotent(s, audit):
    """The second copy of any event must change neither state, total nor ledger."""
    calls = [
        (s.on_mat_lock, (True,), {}),
        (s.on_placement, (Placement("a", "sku", 1000),), {}),
        (s.on_exit, ("a",), {}),
        (s.on_placement, (Placement("b", "sku2", None),), {}),
        (s.on_exit, ("b",), {}),
        (s.on_price, ("b", 250), {}),
        (s.on_revert, ("b",), {}),
        (s.on_network, (False,), {}),
        (s.on_network, (True,), {}),
        (s.on_done, (), {}),
    ]
    for fn, args, kw in calls:
        first = step(s, audit, fn, *args, **kw)
        assert first.applied, (fn.__name__, args)
        state_after, total_after, n_after = s.state, int(s.total_paise), len(lines(audit))

        second = step(s, audit, fn, *args, **kw)  # step asserts 0 new lines
        assert second.applied is False, (fn.__name__, args)
        assert s.state is state_after
        assert int(s.total_paise) == total_after
        assert len(lines(audit)) == n_after

    assert s.state is State.AWAITING_SETTLEMENT
    assert s.intent_amount_paise == 1000

    v = green(s, 1000, "evt_only_once")
    t1 = step(s, audit, s.on_webhook, v)
    assert t1.to is State.PAID
    n = len(lines(audit))
    t2 = step(s, audit, s.on_webhook, v)
    assert t2.applied is False
    assert len(lines(audit)) == n
    assert s.authorised_paise == 1000
    assert int(s.total_paise) == 1000


def test_repeated_refusal_in_the_same_state_logs_once(s, audit):
    ready(s)
    s.on_mat_lock(False)
    t1 = step(s, audit, s.on_placement, Placement("a", None, 100))
    assert t1.applied and t1.reason == Reason.REFUSED_MAT_LOST
    t2 = step(s, audit, s.on_placement, Placement("a", None, 100))
    assert t2.applied is False


def test_a_replayed_green_webhook_cannot_double_authorise(s, audit):
    ready(s)
    place_and_exit(s, "a", 7500)
    s.on_done()
    s.on_webhook(green(s, 7500, "evt_1"))
    assert s.authorised_paise == 7500
    # a *different* event id for the same already-settled intent
    t = step(s, audit, s.on_webhook, green(s, 7500, "evt_2"))
    assert t.reason == Reason.ALREADY_SETTLED
    assert t.changed_state is False
    assert s.authorised_paise == 7500


# ------------------------------------------------------------------ new basket


def test_a_new_placement_after_paid_opens_a_fresh_basket(s, audit):
    ready(s)
    place_and_exit(s, "a", 3000)
    s.on_done()
    s.on_webhook(green(s, 3000))
    assert s.money_authorised and int(s.total_paise) == 3000

    t = step(s, audit, s.on_placement, Placement("b", None, 1200))
    assert t.detail["new_basket"] is True
    assert s.money_authorised is False
    assert s.authorised_paise is None
    assert s.intent_amount_paise is None
    assert int(s.total_paise) == 0        # nothing committed in the new basket yet
    assert s.last_settled_paise == 3000   # the old sale is still on record
    s.on_exit("b")
    assert int(s.total_paise) == 1200


# ------------------------------------------------------------------ money hygiene


@pytest.mark.parametrize("bad", [12.5, "100", True, 0.0, 1e-3])
def test_a_float_price_cannot_enter_the_session(s, bad):
    with pytest.raises(MoneyError):
        Placement("t", None, bad)
    ready(s)
    s.on_placement(Placement("t", None, None))
    with pytest.raises(MoneyError):
        s.on_price("t", bad)


def test_price_none_is_amber_on_a_placement_but_not_a_tap(s):
    """None means 'the kernel abstained'. It is never an amount to charge."""
    ready(s)
    t = s.on_placement(Placement("t", None, None))       # legal: AMBER
    assert t.to is State.AMBER
    with pytest.raises(MoneyError):
        s.on_price("t", None)                            # illegal: not money


def test_negative_prices_are_refused(s):
    with pytest.raises(MoneyError):
        Placement("t", None, -1)


def test_every_ledger_line_reports_an_integer_total(s, audit):
    ready(s)
    place_and_exit(s, "a", 1050)
    place_and_exit(s, "b", None)
    s.on_price("b", 995)
    s.on_done()
    for r in lines(audit):
        assert isinstance(r["total_paise"], int)
        assert not isinstance(r["total_paise"], bool)
        assert isinstance(r["reason"], str) and r["reason"]
        assert r["session_id"] == s.session_id


def test_a_fifty_line_basket_bills_exactly(s, audit):
    """Bigger than any real kirana basket, with every third line amber."""
    ready(s)
    expected = 0
    for i in range(50):
        price = None if i % 3 == 0 else from_rupees_str(f"{i + 1}.{i % 100:02d}")
        s.on_placement(Placement(f"i{i}", f"sku{i}", price))
        s.on_exit(f"i{i}")
        if price is not None:
            expected += price
    assert s.amber_count == 17
    assert int(s.total_paise) == expected
    s.on_done()
    assert s.intent_amount_paise == expected
    assert Ledger(audit).count == 1 + 1 + 50 * 3 + 1  # open, lock, 3/item, done


# ------------------------------------------------------------------ boundary


def test_handlers_reject_non_boolean_signals(s):
    for fn in (s.on_mat_lock, s.on_brain, s.on_network):
        with pytest.raises(TypeError):
            fn(1)


def test_placement_and_verdict_accept_mappings_from_other_modules(s, audit):
    """paisa and the kernel hand plain dicts across a process boundary."""
    ready(s)
    t = step(s, audit, s.on_placement, {"item_id": "a", "name": "sku", "price_paise": 1000})
    assert t.to is State.PRICED
    s.on_exit("a")
    s.on_done()
    t = step(
        s, audit, s.on_webhook,
        {
            "event_id": "evt_dict",
            "event": "payment_link.paid",
            "session_id": s.session_id,
            "amount_paise": 1000,
            "green": True,
            "signature_valid": True,
        },
    )
    assert t.to is State.PAID


@pytest.mark.parametrize("junk", [None, 42, "a string", ["a", "b"]])
def test_placement_and_verdict_reject_junk(junk):
    with pytest.raises((TypeError, ValueError)):
        Placement.coerce(junk)
    with pytest.raises((TypeError, ValueError)):
        Verdict.coerce(junk)


def test_identifiers_must_be_non_empty_strings(s):
    with pytest.raises(ValueError):
        Placement("", None, 100)
    with pytest.raises(ValueError):
        Verdict("", "payment_link.paid", s.session_id, 100, True, True)


def test_verdict_flags_must_be_booleans(s):
    with pytest.raises(TypeError):
        Verdict("e", "payment_link.paid", s.session_id, 100, green=1, signature_valid=True)
    with pytest.raises(TypeError):
        Verdict("e", "payment_link.paid", s.session_id, 100, green=True, signature_valid="yes")


def test_done_on_an_empty_basket_is_refused(s, audit):
    ready(s)
    t = step(s, audit, s.on_done)
    assert t.reason == Reason.EMPTY_BASKET
    assert s.state is State.IDLE
    assert s.intent_amount_paise is None


def test_acknowledge_outside_a_frozen_total_does_nothing(s, audit):
    ready(s)
    t = step(s, audit, s.on_acknowledge)
    assert t.applied is False and t.lines_written == 0
    assert s.state is State.IDLE


def test_brain_loss_after_done_blocks_the_next_basket(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    step(s, audit, s.on_brain, False)
    s.on_webhook(green(s, 1000))
    assert s.state is State.PAID
    t = step(s, audit, s.on_placement, Placement("b", None, 100))
    assert t.reason == Reason.REFUSED_BRAIN_LOST


def test_repeated_brain_and_perf_signals_are_duplicates(s, audit):
    ready(s)
    assert step(s, audit, s.on_brain, True).applied is False
    assert step(s, audit, s.on_perf, 10).applied is False       # already healthy
    assert step(s, audit, s.on_perf, 900).applied is True
    assert step(s, audit, s.on_perf, 901).applied is False      # still degraded


def test_degradation_outside_a_billing_state_logs_without_a_state_change(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    s.on_done()
    t = step(s, audit, s.on_perf, 900)
    assert t.changed_state is False
    assert t.reason == Reason.DEGRADED
    assert s.state is State.AWAITING_SETTLEMENT and s.degraded is True
    t = step(s, audit, s.on_perf, 10)
    assert t.changed_state is False and s.degraded is False


def test_snapshot_reports_the_whole_machine(s, audit):
    ready(s)
    place_and_exit(s, "a", 1000)
    place_and_exit(s, "b", None)
    s.on_network(False)
    s.on_brain(True)  # duplicate, no-op
    snap = s.snapshot()
    assert snap == {
        "session_id": s.session_id,
        "state": "BASKET_OPEN",
        "total_paise": 1000,
        "amber_count": 1,
        "committed": 2,
        "frozen": False,
        "degraded": False,
        "online": False,
        "mat_locked": True,
        "brain_up": True,
        "intent_amount_paise": None,
        "authorised_paise": None,
        "money_authorised": False,
    }
    assert s.online is False and s.brain_up is True
    assert len(s.transitions) == Ledger(audit).count
    assert all(t.applied for t in s.transitions)


def test_two_sessions_on_one_ledger_get_distinct_ids(tmp_path):
    ledger = Ledger(tmp_path / "audit.jsonl")
    a = Session(VirtualClock(), ledger)
    b = Session(VirtualClock(), ledger)
    assert a.session_id != b.session_id
    ok, n, head, err = verify(tmp_path / "audit.jsonl")
    assert ok, err
    assert n == 2


# ------------------------------------------------------------------ property


ITEMS = ["i0", "i1", "i2", "i3"]
OPS = st.lists(
    st.tuples(
        st.sampled_from(["place", "exit", "price", "revert"]),
        st.sampled_from(ITEMS),
        st.one_of(st.none(), st.integers(min_value=0, max_value=100000)),
    ),
    max_size=40,
)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops=OPS)
def test_total_always_equals_the_sum_of_priced_committed_lines(tmp_path_factory, ops):
    """R1 + R2 under arbitrary orderings, including nonsense ones."""
    d = tmp_path_factory.mktemp("prop")
    audit = d / "audit.jsonl"
    ledger = Ledger(audit)
    session = Session(VirtualClock(), ledger)
    session.on_mat_lock(True)
    frozen_at: int | None = None
    declared = 0

    for op, item, price in ops:
        if op == "place":
            t = session.on_placement(Placement(item, None, price))
        elif op == "exit":
            t = session.on_exit(item)
        elif op == "price":
            t = session.on_price(item, price if price is not None else 0)
        else:
            t = session.on_revert(item)
        declared += t.lines_written

        live = sum(
            li.price_paise
            for li in session.line_items
            if li.committed and not li.reverted and li.price_paise is not None
        )
        if session.frozen:
            # R5: once frozen the reported total never moves again
            if frozen_at is None:
                frozen_at = int(session.total_paise)
            assert int(session.total_paise) == frozen_at
        else:
            frozen_at = None
            assert int(session.total_paise) == live

        # R1: no amber line ever contributes, in any ordering
        assert int(session.live_total_paise) == sum(
            li.price_paise for li in session.committed_items if not li.amber
        )
        assert session.money_authorised is False

    assert declared == ledger.count - 2, "every ledger line was declared"  # open + mat lock
    ok, n, head, err = verify(audit)
    assert ok, err
    assert n == ledger.count
