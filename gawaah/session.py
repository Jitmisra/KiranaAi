"""S4d — SAUDA, the session state machine. The counter's whole lifecycle.

This module owns *what state the counter is in* and *what the total is*. It owns
nothing else. In particular it does not mint, does not hold a secret, does not
verify an HMAC and does not construct a payment payload of any kind. It is
handed a `Verdict` that `paisa` already adjudicated, and it is allowed to be
*more* conservative than that verdict but never less.

Load-bearing rules, each with a test:

  R1  AMBER line items are excluded from the total. Always. There is no path
      that adds an unpriced item to money.
  R2  The total is `money.total(...)` recomputed from the committed line items
      on every read. Nothing anywhere increments a running sum.
  R3  Tap-to-revert removes a line and writes ``human_override: True``.
  R4  Money is authorised in exactly one state, PAID, and PAID is reachable
      only through a green settlement `Verdict` whose amount equals the open
      intent to the paisa.
  R5  MAT_LOST and BRAIN_LOST snapshot the total and refuse every billing
      event. Billing never silently continues across a perception outage.
  R6  Offline -> PENDING_OFFLINE. Billing continues locally, nothing is
      authorised.
  R7  Every applied transition appends exactly one ledger line carrying its
      reason code. Duplicates append nothing.

Integer paise everywhere; `tools/lint_no_float.py` AST-checks this file for
float literals, float() casts and true division.

Three handlers beyond the mandated API exist because three of the mandated
states are otherwise unreachable: `on_brain` (BRAIN_LOST), `on_perf`
(DEGRADED) and `on_acknowledge` (the way out of FROZEN_TOTAL). They are named
so that their extension status is obvious.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from .clock import Clock
from .ledger import Ledger, canonical
from .money import MoneyError, Paise
from .money import paise as make_paise
from .money import total as sum_paise

__all__ = [
    "State",
    "Reason",
    "Placement",
    "Verdict",
    "LineItem",
    "Transition",
    "Session",
    "GREEN_EVENTS",
    "DEGRADED_P95_MS",
]


class State(str, Enum):
    SETUP = "SETUP"
    IDLE = "IDLE"
    MEASURING = "MEASURING"
    PRICED = "PRICED"
    AMBER = "AMBER"
    BASKET_OPEN = "BASKET_OPEN"
    AWAITING_SETTLEMENT = "AWAITING_SETTLEMENT"
    PENDING_OFFLINE = "PENDING_OFFLINE"
    PAID = "PAID"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MAT_LOST = "MAT_LOST"
    BRAIN_LOST = "BRAIN_LOST"
    DEGRADED = "DEGRADED"
    FROZEN_TOTAL = "FROZEN_TOTAL"


class Reason:
    """Named reason codes. Every ledger line carries exactly one."""

    SESSION_OPENED = "session_opened"
    MAT_LOCKED = "mat_locked"
    MAT_LOST = "mat_lost"
    MAT_REACQUIRED = "mat_reacquired"
    BRAIN_LOST = "brain_lost"
    BRAIN_REACQUIRED = "brain_reacquired"
    STILL_FROZEN = "still_frozen"
    SIGNAL_AFTER_BASKET_CLOSED = "perception_signal_after_basket_closed"

    PLACEMENT_SEEN = "placement_seen"
    PRICED = "priced_from_gallery"
    UNKNOWN_SKU = "unknown_sku"
    PRICE_TAPPED = "price_tapped"
    COMMITTED = "exit_crossing_committed"
    COMMITTED_AMBER = "exit_crossing_committed_amber_excluded"
    REVERTED = "reverted_by_shopkeeper"

    UNCOUNTED_CROSSING = "uncounted_crossing_no_tracker_id"
    UNTRACKED_EXIT = "uncounted_crossing_unknown_item"
    HUMAN_ACKNOWLEDGED = "human_acknowledged_freeze"

    INTENT_REQUESTED = "intent_requested"
    OFFLINE_NO_AUTHORISATION = "offline_billing_continues_nothing_authorised"
    NETWORK_DOWN = "network_down"
    NETWORK_DOWN_BILLING_CONTINUES = "network_down_billing_continues"
    NETWORK_RESTORED = "network_restored"

    BAD_SIGNATURE = "webhook_signature_invalid_discarded"
    FOREIGN_SESSION = "webhook_session_id_does_not_match_discarded"
    NO_OPEN_INTENT = "webhook_no_open_intent_discarded"
    ALREADY_SETTLED = "webhook_after_settlement_ignored"
    NOT_IN_GREEN_SET = "webhook_event_not_in_green_set"
    PAISA_REFUSED_GREEN = "paisa_refused_green"
    AMOUNT_MISMATCH = "webhook_amount_does_not_match_intent"
    SETTLED = "settled_green"

    DEGRADED = "p95_over_threshold"
    PERF_RECOVERED = "p95_recovered"
    DEGRADED_REQUIRES_TAP = "degraded_auto_commit_disabled_tap_required"

    MAT_NOT_LOCKED = "refused_mat_not_locked"
    REFUSED_MAT_LOST = "refused_mat_lost"
    REFUSED_BRAIN_LOST = "refused_brain_lost"
    REFUSED_FROZEN_TOTAL = "refused_total_frozen"
    BASKET_LOCKED = "refused_basket_locked_after_done"
    RED_HOLD = "refused_red_hold_manual_resolution"
    EMPTY_BASKET = "refused_empty_basket"
    ZERO_TOTAL = "refused_zero_total_all_amber"
    UNKNOWN_ITEM = "refused_unknown_item"
    REVERTED_ITEM = "refused_item_already_reverted"

    DUPLICATE = "duplicate_event_ignored"


#: Razorpay events that assert money landed. Membership is the "event in the
#: green set" leg of the four-part green predicate; it needs no secret, so the
#: session re-checks it rather than trusting a flag.
GREEN_EVENTS = frozenset(
    {"payment_link.paid", "payment.captured", "qr_code.credited"}
)

#: p95 frame time above which auto-commit is disabled (abstention 9).
DEGRADED_P95_MS = 250

_FROZEN: frozenset[State] = frozenset(
    {State.MAT_LOST, State.BRAIN_LOST, State.FROZEN_TOTAL}
)
_FROZEN_REASON = {
    State.MAT_LOST: Reason.REFUSED_MAT_LOST,
    State.BRAIN_LOST: Reason.REFUSED_BRAIN_LOST,
    State.FROZEN_TOTAL: Reason.REFUSED_FROZEN_TOTAL,
}
#: states in which the basket is closed and its total is committed to an intent
_LOCKED: frozenset[State] = frozenset(
    {
        State.AWAITING_SETTLEMENT,
        State.PENDING_OFFLINE,
        State.PAID,
        State.AMOUNT_MISMATCH,
    }
)
#: states from which a perf degradation has a meaningful resume point
_BILLING: frozenset[State] = frozenset(
    {
        State.IDLE,
        State.MEASURING,
        State.PRICED,
        State.AMBER,
        State.BASKET_OPEN,
    }
)


def _as_price(value: Any) -> int:
    """Validate a money input at the boundary. Rejects float, bool, str, None."""
    amount = int(make_paise(value))
    if amount < 0:
        raise MoneyError(f"a price cannot be negative: {amount}")
    return amount


@dataclass(frozen=True)
class Placement:
    """What the perception kernel says it sees, handed to the session as one unit.

    `item_id` is a *tracker* id, unique per physical placement, not a SKU id.
    `price_paise is None` means the kernel abstained; `reason` must then name
    why, and the line will be AMBER and excluded from the total.
    """

    item_id: str
    name: str | None = None
    price_paise: int | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id:
            raise ValueError(f"item_id must be a non-empty str: {self.item_id!r}")
        if self.price_paise is not None:
            object.__setattr__(self, "price_paise", _as_price(self.price_paise))
        if self.price_paise is None and not self.reason:
            object.__setattr__(self, "reason", Reason.UNKNOWN_SKU)

    @classmethod
    def coerce(cls, item: "Placement | Mapping[str, Any]") -> "Placement":
        if isinstance(item, Placement):
            return item
        if isinstance(item, Mapping):
            return cls(**dict(item))
        raise TypeError(f"not a Placement: {item!r}")


@dataclass(frozen=True)
class Verdict:
    """A webhook adjudicated by `paisa`, which alone holds the secret.

    `green` is paisa's answer to the four-part predicate. The session may veto
    it (wrong amount, no open intent, foreign session) but can never grant
    green that paisa withheld.
    """

    event_id: str
    event: str
    session_id: str
    amount_paise: int | None = None
    green: bool = False
    signature_valid: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError(f"event_id must be a non-empty str: {self.event_id!r}")
        if self.amount_paise is not None:
            object.__setattr__(self, "amount_paise", _as_price(self.amount_paise))
        for flag in ("green", "signature_valid"):
            if not isinstance(getattr(self, flag), bool):
                raise TypeError(f"{flag} must be a bool")

    @classmethod
    def coerce(cls, v: "Verdict | Mapping[str, Any]") -> "Verdict":
        if isinstance(v, Verdict):
            return v
        if isinstance(v, Mapping):
            return cls(**dict(v))
        raise TypeError(f"not a Verdict: {v!r}")


@dataclass
class LineItem:
    item_id: str
    name: str | None
    price_paise: int | None
    reason: str
    committed: bool = False
    reverted: bool = False

    @property
    def amber(self) -> bool:
        return self.price_paise is None

    @property
    def counts(self) -> bool:
        """Does this line contribute to the total? Amber never does."""
        return self.committed and not self.reverted and self.price_paise is not None


@dataclass(frozen=True)
class Transition:
    frm: State
    to: State
    reason: str
    applied: bool
    ledger_hash: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    #: how many ledger lines this call appended. One per transition; a
    #: placement is two (MEASURING, then the classification), a duplicate zero.
    lines_written: int = 0

    @property
    def changed_state(self) -> bool:
        return self.frm is not self.to


class Session:
    """The counter's lifecycle. One instance per customer at the counter."""

    def __init__(
        self,
        clock: Clock,
        ledger: Ledger,
        session_id: str | None = None,
    ) -> None:
        self.clock = clock
        self.ledger = ledger

        self._state: State = State.SETUP
        self._items: dict[str, LineItem] = {}
        self._order: list[str] = []

        self._mat_locked = False
        self._brain_up = True
        self._online = True
        self._degraded = False

        self._frozen_total: int | None = None
        self._resume_state: State | None = None
        self._degraded_resume: State | None = None
        # independent freeze causes. The total thaws only when all are clear,
        # so a mat loss during a frozen-total exception cannot silently thaw it.
        self._causes: set[str] = set()

        self._intent_amount: int | None = None
        self._authorised_paise: int | None = None
        self._last_settled_paise: int | None = None

        self._webhooks: dict[str, Transition] = {}
        self._noop_keys: dict[str, Transition] = {}
        self._transitions: list[Transition] = []

        self.session_id = session_id or self._derive_id()
        self._emit(State.SETUP, "session", Reason.SESSION_OPENED, {})

    # ------------------------------------------------------------ identity

    def _derive_id(self) -> str:
        seed = {"opened": self.clock.now_iso(), "head": self.ledger.head}
        return hashlib.sha256(canonical(seed)).hexdigest()[:16]

    # ------------------------------------------------------------ readouts

    @property
    def state(self) -> State:
        return self._state

    @property
    def frozen(self) -> bool:
        return self._frozen_total is not None

    @property
    def mat_locked(self) -> bool:
        return self._mat_locked

    @property
    def brain_up(self) -> bool:
        return self._brain_up

    @property
    def online(self) -> bool:
        return self._online

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _live_total(self) -> int:
        """R2: recomputed from committed line items, every time. Never a counter."""
        return int(
            sum_paise(
                [li.price_paise for li in self._items.values() if li.counts]
            )
        )

    @property
    def total_paise(self) -> Paise:
        """The billable total. Frozen states report the snapshot, not a live sum."""
        if self._frozen_total is not None:
            return Paise(self._frozen_total)
        return Paise(self._live_total())

    @property
    def live_total_paise(self) -> Paise:
        """What the total *would* be if nothing were frozen. Diagnostics only."""
        return Paise(self._live_total())

    @property
    def line_items(self) -> list[LineItem]:
        return [self._items[i] for i in self._order]

    @property
    def committed_items(self) -> list[LineItem]:
        return [
            self._items[i]
            for i in self._order
            if self._items[i].committed and not self._items[i].reverted
        ]

    @property
    def amber_items(self) -> list[LineItem]:
        return [li for li in self.committed_items if li.amber]

    @property
    def amber_count(self) -> int:
        return len(self.amber_items)

    @property
    def intent_amount_paise(self) -> int | None:
        return self._intent_amount

    @property
    def authorised_paise(self) -> int | None:
        return self._authorised_paise

    @property
    def last_settled_paise(self) -> int | None:
        return self._last_settled_paise

    @property
    def money_authorised(self) -> bool:
        """R4. True in PAID and nowhere else, ever."""
        return self._state is State.PAID and self._authorised_paise is not None

    @property
    def transitions(self) -> list[Transition]:
        return list(self._transitions)

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self._state.value,
            "total_paise": int(self.total_paise),
            "amber_count": self.amber_count,
            "committed": len(self.committed_items),
            "frozen": self.frozen,
            "degraded": self._degraded,
            "online": self._online,
            "mat_locked": self._mat_locked,
            "brain_up": self._brain_up,
            "intent_amount_paise": self._intent_amount,
            "authorised_paise": self._authorised_paise,
            "money_authorised": self.money_authorised,
        }

    # ------------------------------------------------------------ plumbing

    def _committed(self) -> list[LineItem]:
        return [li for li in self._items.values() if li.committed and not li.reverted]

    def _emit(
        self,
        to: State,
        event: str,
        reason: str,
        detail: Mapping[str, Any] | None = None,
    ) -> Transition:
        """R7: one applied transition, one ledger line, one reason code."""
        frm = self._state
        if to is not frm:
            self._state = to
            # a refusal is only a duplicate while nothing has moved
            self._noop_keys.clear()
            if frm is State.DEGRADED:
                self._degraded_resume = None
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "event": event,
            "from": frm.value,
            "to": to.value,
            "reason": reason,
            "total_paise": int(self.total_paise),
            "amber_count": self.amber_count,
            "committed": len(self._committed()),
            "frozen": self.frozen,
            "degraded": self._degraded,
            "money_authorised": self.money_authorised,
        }
        payload.update(dict(detail or {}))
        h = self.ledger.append(ts=self.clock.now_iso(), module="session", **payload)
        t = Transition(
            frm=frm,
            to=to,
            reason=reason,
            applied=True,
            ledger_hash=h,
            detail=dict(detail or {}),
            lines_written=1,
        )
        self._transitions.append(t)
        return t

    def _noop(self, reason: str) -> Transition:
        """A duplicate. No state change, no ledger line, no reason to pretend."""
        return Transition(self._state, self._state, reason, False, None, {}, 0)

    def _refuse(
        self, key: str, reason: str, detail: Mapping[str, Any] | None = None
    ) -> Transition:
        """Log the refusal once per (event, state). Repeats are duplicates."""
        seen = self._noop_keys.get(key)
        if seen is not None:
            return seen
        t = self._emit(self._state, "refused", reason, {**dict(detail or {}), "refused": True})
        self._noop_keys[key] = replace(t, applied=False, ledger_hash=None, lines_written=0)
        return t

    def _billing_guard(
        self, key: str, *, allow_paid: bool = False
    ) -> Transition | None:
        if self._state is State.SETUP:
            return self._refuse(key, Reason.MAT_NOT_LOCKED)
        if self._state in _FROZEN:
            return self._refuse(key, _FROZEN_REASON[self._state])
        # after DONE the freeze states are not entered (there is nothing left to
        # bill), so the raw signals are re-checked here before any new billing.
        if not self._mat_locked:
            return self._refuse(key, Reason.REFUSED_MAT_LOST)
        if not self._brain_up:
            return self._refuse(key, Reason.REFUSED_BRAIN_LOST)
        if self._state is State.AMOUNT_MISMATCH:
            return self._refuse(key, Reason.RED_HOLD)
        if self._state is State.PAID and allow_paid:
            return None
        if self._state in _LOCKED:
            return self._refuse(key, Reason.BASKET_LOCKED)
        return None

    def _resume_target(self) -> State:
        resume = self._resume_state
        if resume is None or resume is State.SETUP or resume in _FROZEN:
            resume = State.BASKET_OPEN if self._committed() else State.IDLE
        return resume

    def _snapshot_freeze(self) -> None:
        """R5: capture the total at the instant of the first freeze cause."""
        if self._frozen_total is None:
            self._frozen_total = self._live_total()
            self._resume_state = self._state

    def _freeze_state(self) -> State | None:
        """Which freeze the operator should see. Mat beats brain beats exception."""
        if "mat" in self._causes:
            return State.MAT_LOST
        if "brain" in self._causes:
            return State.BRAIN_LOST
        if "exception" in self._causes:
            return State.FROZEN_TOTAL
        return None

    def _settle_freeze(self, event: str, reason: str) -> Transition:
        target = self._freeze_state()
        if target is not None:
            self._snapshot_freeze()
            if self._state is target:
                return self._emit(target, event, Reason.STILL_FROZEN, {})
            return self._emit(
                target, event, reason, {"frozen_total_paise": self._frozen_total}
            )
        resume = self._resume_target()
        self._frozen_total = None
        self._resume_state = None
        return self._emit(
            resume, event, reason, {"resumed_total_paise": self._live_total()}
        )

    # ------------------------------------------------------------ mat / brain

    def on_mat_lock(self, locked: bool) -> Transition:
        """Four markers seen (True) or lost (False). Loss freezes the total."""
        if not isinstance(locked, bool):
            raise TypeError(f"on_mat_lock takes a bool: {locked!r}")
        if locked == self._mat_locked:
            return self._noop(Reason.DUPLICATE)
        self._mat_locked = locked
        return self._availability(
            "mat_lock", Reason.MAT_LOST if not locked else Reason.MAT_REACQUIRED
        )

    def on_brain(self, up: bool) -> Transition:
        """Extension. The phone's link to the brain. Loss freezes the total."""
        if not isinstance(up, bool):
            raise TypeError(f"on_brain takes a bool: {up!r}")
        if up == self._brain_up:
            return self._noop(Reason.DUPLICATE)
        self._brain_up = up
        return self._availability(
            "brain_link", Reason.BRAIN_LOST if not up else Reason.BRAIN_REACQUIRED
        )

    def _availability(self, event: str, reason: str) -> Transition:
        """R5. Either perception input going down snapshots the total and freezes."""
        if self._mat_locked:
            self._causes.discard("mat")
        else:
            self._causes.add("mat")
        if self._brain_up:
            self._causes.discard("brain")
        else:
            self._causes.add("brain")
        if self._intent_amount is not None:
            # The basket is already closed against an intent, so no billing is
            # in flight and there is nothing to freeze. Crucially, entering a
            # freeze state here would let a later re-acquire "resume" a session
            # that had meanwhile been settled, un-paying a paid sale.
            return self._emit(
                self._state, event, Reason.SIGNAL_AFTER_BASKET_CLOSED,
                {"signal": reason, "mat_locked": self._mat_locked,
                 "brain_up": self._brain_up},
            )
        return self._settle_freeze(event, reason)

    # ------------------------------------------------------------ billing

    def on_placement(self, item: Placement | Mapping[str, Any]) -> Transition:
        """An object landed on the mat, already classified (or abstained on)."""
        p = Placement.coerce(item)
        if p.item_id in self._items:
            return self._noop(Reason.DUPLICATE)
        key = f"place:{p.item_id}"
        refused = self._billing_guard(key, allow_paid=True)
        if refused is not None:
            return refused

        new_basket = False
        if self._state is State.PAID:
            # PRD: PAID -> new placement. The settled basket is closed out.
            self._items.clear()
            self._order.clear()
            self._intent_amount = None
            self._authorised_paise = None
            new_basket = True

        line = LineItem(
            item_id=p.item_id,
            name=p.name,
            price_paise=p.price_paise,
            reason=p.reason or (Reason.PRICED if p.price_paise is not None else Reason.UNKNOWN_SKU),
        )
        self._items[p.item_id] = line
        self._order.append(p.item_id)

        entry = self._state
        detail = {"item_id": p.item_id, "name": p.name, "new_basket": new_basket}
        self._emit(State.MEASURING, "placement", Reason.PLACEMENT_SEEN, detail)
        if line.amber:
            # R1: an abstention is a line on the mat, never a line in the money.
            final = self._emit(
                State.AMBER,
                "classify",
                line.reason,
                {**detail, "excluded_from_total": True, "abstained": True},
            )
        else:
            final = self._emit(
                State.PRICED,
                "classify",
                Reason.PRICED,
                {**detail, "price_paise": line.price_paise},
            )
        # a placement is two transitions and two ledger lines; report both, and
        # report the composite as starting where the caller found the session.
        return replace(final, frm=entry, lines_written=2)

    def on_price(self, item_id: str, paise: int) -> Transition:
        """The shopkeeper tapped a price. Warm enroll: amber becomes billable."""
        amount = _as_price(paise)  # raises MoneyError on float/bool/str/None
        line = self._items.get(item_id)
        if line is None:
            return self._refuse(
                f"price:{item_id}", Reason.UNKNOWN_ITEM, {"item_id": item_id}
            )
        if line.price_paise == amount:
            return self._noop(Reason.DUPLICATE)
        refused = self._billing_guard(f"price:{item_id}:{amount}")
        if refused is not None:
            return refused
        if line.reverted:
            return self._refuse(
                f"price:{item_id}:{amount}", Reason.REVERTED_ITEM, {"item_id": item_id}
            )

        was_amber = line.amber
        line.price_paise = amount
        line.reason = Reason.PRICE_TAPPED
        # pricing an already-committed line only changes the money, not the
        # chrome; pricing one still on the mat resolves it out of AMBER.
        to = self._state if line.committed else State.PRICED
        return self._emit(
            to,
            "price",
            Reason.PRICE_TAPPED,
            {
                "item_id": item_id,
                "price_paise": amount,
                "was_amber": was_amber,
                "human_override": True,
            },
        )

    def on_exit(self, item_id: str | None, tap: bool = False) -> Transition:
        """The item crossed the exit edge. This is what commits a line.

        `item_id=None` is abstention 11: a crossing whose tracker id was lost.
        Goods left the counter and we cannot say which. The total freezes.
        """
        key = f"exit:{item_id}:{tap}"
        refused = self._billing_guard(key)
        if refused is not None:
            return refused
        if item_id is None or item_id == "":
            return self._freeze_total("exit", Reason.UNCOUNTED_CROSSING, {})
        line = self._items.get(item_id)
        if line is None:
            # Something crossed that we never measured. Silently dropping it
            # would be billing that quietly under-counts. Freeze instead.
            return self._freeze_total(
                "exit", Reason.UNTRACKED_EXIT, {"item_id": item_id}
            )
        if line.reverted:
            return self._refuse(key, Reason.REVERTED_ITEM, {"item_id": item_id})
        if line.committed:
            return self._noop(Reason.DUPLICATE)
        if self._degraded and not tap:
            # abstention 9: over the p95 threshold, auto-commit is disabled.
            return self._refuse(
                key, Reason.DEGRADED_REQUIRES_TAP, {"item_id": item_id}
            )

        line.committed = True
        if line.amber:
            return self._emit(
                State.BASKET_OPEN,
                "exit",
                Reason.COMMITTED_AMBER,
                {
                    "item_id": item_id,
                    "excluded_from_total": True,
                    "abstained": True,
                    "tap": tap,
                },
            )
        return self._emit(
            State.BASKET_OPEN,
            "exit",
            Reason.COMMITTED,
            {"item_id": item_id, "price_paise": line.price_paise, "tap": tap},
        )

    def on_revert(self, item_id: str) -> Transition:
        """R3. Tap-to-revert. Removes the line, logs human_override=True."""
        line = self._items.get(item_id)
        if line is None:
            return self._refuse(
                f"revert:{item_id}", Reason.UNKNOWN_ITEM, {"item_id": item_id}
            )
        if line.reverted:
            return self._noop(Reason.DUPLICATE)
        refused = self._billing_guard(f"revert:{item_id}")
        if refused is not None:
            return refused

        removed = line.price_paise if line.price_paise is not None else 0
        was_committed = line.committed
        line.reverted = True
        to = State.BASKET_OPEN if self._committed() else State.IDLE
        return self._emit(
            to,
            "revert",
            Reason.REVERTED,
            {
                "item_id": item_id,
                "removed_paise": removed,
                "was_committed": was_committed,
                "was_amber": line.amber,
                "human_override": True,
            },
        )

    def _freeze_total(
        self, event: str, reason: str, detail: Mapping[str, Any]
    ) -> Transition:
        # reaching here already means the billing guard passed, so the session
        # is not in FROZEN_TOTAL and this is a fresh exception.
        self._snapshot_freeze()
        self._causes.add("exception")
        return self._emit(
            State.FROZEN_TOTAL,
            event,
            reason,
            {**dict(detail), "frozen_total_paise": self._frozen_total, "abstained": True},
        )

    def on_acknowledge(self) -> Transition:
        """Extension. The shopkeeper accepts a frozen-total exception and resumes."""
        if self._state is not State.FROZEN_TOTAL:
            return self._noop(Reason.DUPLICATE)
        self._causes.discard("exception")
        resume = self._resume_target()
        self._frozen_total = None
        self._resume_state = None
        return self._emit(
            resume,
            "acknowledge",
            Reason.HUMAN_ACKNOWLEDGED,
            {"human_override": True, "resumed_total_paise": self._live_total()},
        )

    # ------------------------------------------------------------ settlement

    def on_done(self) -> Transition:
        """DONE tap. Locks the basket and records the amount paisa must mint for."""
        if self._state in (State.AWAITING_SETTLEMENT, State.PENDING_OFFLINE):
            return self._noop(Reason.DUPLICATE)
        refused = self._billing_guard("done")
        if refused is not None:
            return refused
        if not self._committed():
            return self._refuse("done", Reason.EMPTY_BASKET)
        amount = self._live_total()
        if amount <= 0:
            # every committed line abstained; there is nothing to charge for.
            return self._refuse("done", Reason.ZERO_TOTAL, {"amber_count": self.amber_count})

        self._intent_amount = amount
        detail = {
            "intent_amount_paise": amount,
            "amber_excluded": self.amber_count,
            "lines": len(self._committed()),
        }
        if not self._online:
            # R6: billing happened, authorisation cannot.
            return self._emit(
                State.PENDING_OFFLINE, "done", Reason.OFFLINE_NO_AUTHORISATION, detail
            )
        return self._emit(
            State.AWAITING_SETTLEMENT, "done", Reason.INTENT_REQUESTED, detail
        )

    def on_network(self, up: bool) -> Transition:
        if not isinstance(up, bool):
            raise TypeError(f"on_network takes a bool: {up!r}")
        if up == self._online:
            return self._noop(Reason.DUPLICATE)
        self._online = up
        if not up:
            if self._state is State.AWAITING_SETTLEMENT:
                return self._emit(
                    State.PENDING_OFFLINE,
                    "network",
                    Reason.NETWORK_DOWN,
                    {"intent_amount_paise": self._intent_amount},
                )
            return self._emit(
                self._state, "network", Reason.NETWORK_DOWN_BILLING_CONTINUES, {}
            )
        if self._state is State.PENDING_OFFLINE:
            # PENDING_OFFLINE is only reachable through DONE, so an intent
            # always exists here and the queue has exactly one thing to drain.
            return self._emit(
                State.AWAITING_SETTLEMENT,
                "network",
                Reason.NETWORK_RESTORED,
                {"intent_amount_paise": self._intent_amount},
            )
        return self._emit(self._state, "network", Reason.NETWORK_RESTORED, {})

    def on_webhook(self, verdict: Verdict | Mapping[str, Any]) -> Transition:
        """R4. The only door to PAID, and it has four locks.

        paisa verified the HMAC over raw bytes and set `green`. The session
        re-checks the three legs it can check without a secret — event class,
        session id, exact amount — and can only ever refuse harder.
        """
        v = Verdict.coerce(verdict)
        seen = self._webhooks.get(v.event_id)
        if seen is not None:
            return seen

        def finish(t: Transition) -> Transition:
            self._webhooks[v.event_id] = replace(t, applied=False, ledger_hash=None, lines_written=0)
            return t

        base: dict[str, Any] = {
            "event_id": v.event_id,
            "razorpay_event": v.event,
            "webhook_amount_paise": v.amount_paise,
        }

        if not v.signature_valid:
            # abstention 17: discarded, logged, never a state change.
            return finish(
                self._emit(
                    self._state, "webhook", Reason.BAD_SIGNATURE, {**base, "discarded": True}
                )
            )
        if v.session_id != self.session_id:
            return finish(
                self._emit(
                    self._state, "webhook", Reason.FOREIGN_SESSION, {**base, "discarded": True}
                )
            )
        if self._intent_amount is None:
            return finish(
                self._emit(
                    self._state, "webhook", Reason.NO_OPEN_INTENT, {**base, "discarded": True}
                )
            )
        if self._authorised_paise is not None:
            return finish(self._emit(self._state, "webhook", Reason.ALREADY_SETTLED, base))
        if v.event not in GREEN_EVENTS:
            return finish(
                self._emit(self._state, "webhook", Reason.NOT_IN_GREEN_SET, base)
            )
        if v.amount_paise is None or v.amount_paise != self._intent_amount:
            # RED HOLD. Never PAID on a wrong amount, whatever paisa said.
            return finish(
                self._emit(
                    State.AMOUNT_MISMATCH,
                    "webhook",
                    Reason.AMOUNT_MISMATCH,
                    {**base, "expected_paise": self._intent_amount},
                )
            )
        if not v.green:
            return finish(
                self._emit(
                    self._state,
                    "webhook",
                    Reason.PAISA_REFUSED_GREEN,
                    {**base, "paisa_reason": v.reason},
                )
            )

        self._authorised_paise = int(self._intent_amount)
        self._last_settled_paise = self._authorised_paise
        return finish(
            self._emit(
                State.PAID,
                "webhook",
                Reason.SETTLED,
                {**base, "authorised_paise": self._authorised_paise},
            )
        )

    # ------------------------------------------------------------ perf

    def on_perf(self, p95_ms: int, threshold_ms: int = DEGRADED_P95_MS) -> Transition:
        """Extension. Abstention 9: over the p95 budget, auto-commit is disabled."""
        over = int(p95_ms) > int(threshold_ms)
        if over == self._degraded:
            return self._noop(Reason.DUPLICATE)
        self._degraded = over
        detail = {"p95_ms": int(p95_ms), "threshold_ms": int(threshold_ms)}
        if over:
            if self._state in _BILLING:
                self._degraded_resume = self._state
                return self._emit(State.DEGRADED, "perf", Reason.DEGRADED, detail)
            return self._emit(self._state, "perf", Reason.DEGRADED, detail)
        if self._state is State.DEGRADED:
            # DEGRADED is only entered from a billing state, which always
            # records where to resume, and leaving it clears the slot.
            resume = self._degraded_resume or State.IDLE
            self._degraded_resume = None
            return self._emit(resume, "perf", Reason.PERF_RECOVERED, detail)
        return self._emit(self._state, "perf", Reason.PERF_RECOVERED, detail)
