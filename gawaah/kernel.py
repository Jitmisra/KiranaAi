"""S4a — THE EXACTLY-ONCE KERNEL.

A debit for (session_id, cycle, amount_paise) executes ONCE OR NEVER.

The ordering below is the entire module. Everything else is bookkeeping:

    1. create_intent(...)  -> write-ahead row + nonce COMMITTED, connection CLOSED
    2. mark_calling(nonce) -> state CALLING COMMITTED, connection CLOSED
    3. ---- only now is the gateway called, with no DB connection held ----
    4. mark_settled / mark_indeterminate / mark_failed

Why that ordering buys exactly-once:

  * A row in NEW proves the gateway was never called: the call is unreachable
    until CALLING is durable. NEW rows are therefore safe to drive forward.
  * A row in CALLING that outlives the process is the indeterminate case: the
    money may or may not have moved. recover() converts those to INDETERMINATE.
  * INDETERMINATE is never retried blind. It goes to RETRIEVE and asks the
    gateway what happened to that nonce. reconcile() has no code path that can
    charge anything -- it only looks up -- so reconciliation cannot double-charge
    even if it runs a hundred times.
  * The idempotency key is a hash of (session_id, cycle, amount_paise) under a
    UNIQUE index, so N concurrent callers produce exactly one row and one nonce.
    A legitimate second charge needs a new cycle; that is what cycle is for.

A transaction is NEVER held across a network call. The connection is opened,
used, committed and closed inside each mark_* method; `open_connections` is a
live counter so tests can prove the connection really is released (see
tests/test_kernel.py::test_no_db_connection_is_held_across_the_gateway_call).

INVARIANT 1: this file is on the money path. No float, no float(), no "/".
INVARIANT 7: unknown gateway answers abstain (back to INDETERMINATE or parked
for a human with needs_human=1). They never guess a settlement.
INVARIANT 6: there is no payload construction anywhere in this module. It moves
state machines and reads the gateway; it cannot mint a request.

Two operational properties that are as load-bearing as the safety ones:

  * THE ABSTENTION LOOP IS BOUNDED. Abstaining forever is not safety, it is a
    stuck till: an INDETERMINATE intent would be re-polled by every sweep until
    the end of time, writing two audit lines a turn and never reaching a person.
    `max_retrieve_attempts` caps the machine's budget; when it runs out the row
    moves to ESCALATED, which no sweep will ever touch again. Escalation is a
    hand-off, not a decision: it settles nothing, fails nothing, and — because
    `resolve_escalated` takes no gateway argument — it cannot charge anything.

  * TWO PROCESSES MAY SHARE ONE LEDGER FILE. `Ledger` caches the chain head in
    memory, which is correct for a single writer and silently wrong for two:
    the second process appends a line whose prev_hash is whatever the head was
    when IT opened the file, and `ledger.verify` reports a chain break on the
    very next line. A threading.RLock cannot see another process. So every
    append this module makes is taken under an `flock` on a sidecar lock file,
    and the true head is re-read from disk under that lock first. Measured: 4
    processes x 12 intents (144 lines) breaks the chain at line 2 without this
    and verifies clean with it — see
    tests/test_kernel.py::test_two_processes_sharing_one_ledger_keep_the_chain_intact
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

try:                                    # POSIX only; see ledger_lock_is_cross_process
    import fcntl as _fcntl
except ImportError:                     # pragma: no cover - Windows
    _fcntl = None                       # type: ignore[assignment]

from .clock import Clock
from .ledger import GENESIS, Ledger, canonical
from .money import MoneyError, paise

MODULE = "kernel"

# ---------------------------------------------------------------- states

NEW = "NEW"
CALLING = "CALLING"
SETTLED = "SETTLED"
INDETERMINATE = "INDETERMINATE"
RETRIEVE = "RETRIEVE"
FAILED = "FAILED"
#: The machine has spent its whole retrieve budget and still does not know
#: whether money moved. Terminal for every automatic code path in this module;
#: only `resolve_escalated` (a human) or a late authoritative webhook moves it.
ESCALATED = "ESCALATED"
#: KHATA. The bill closed onto a customer's book instead of onto a payment
#: link: the debit for (session, cycle, amount) executed NEVER, by decision,
#: and that answer is final for this intent. Reachable ONLY from NEW, which is
#: the write-ahead proof that no gateway was ever called for it — a bill that
#: already has a link (CALLING) or money (SETTLED) cannot be booked, because
#: that would be two ways of getting paid for one basket. The money is
#: collected later by a different flow with its own tables (`collections`,
#: `captures`), never by settling this row: `mark_settled` on a BOOKED intent
#: is an IllegalTransition, so a partial capture can never turn a booked bill
#: PAID.
BOOKED = "BOOKED"

ALL_STATES = frozenset(
    {NEW, CALLING, SETTLED, INDETERMINATE, RETRIEVE, FAILED, ESCALATED, BOOKED}
)
#: States in which the MONEY question is answered. ESCALATED is deliberately
#: NOT here: it is terminal for the sweeper but the money is still unknown, and
#: calling it terminal would let a caller mistake a stuck row for a decision.
#: BOOKED is here: the question "did this debit execute" is answered — never —
#: and the outstanding rupees are a question for the book, not for this row.
TERMINAL = frozenset({SETTLED, FAILED, BOOKED})
#: States no automatic sweep may drive forward.
MACHINE_TERMINAL = frozenset({SETTLED, FAILED, ESCALATED, BOOKED})

#: How many times the machine may ask the gateway "what happened to this nonce?"
#: before handing the row to a person. Each attempt is one read-only lookup and
#: two audit lines, so an uncapped loop is both unbounded work and unbounded
#: log. Eight is enough to ride out a multi-minute gateway outage on a one
#: minute sweep and small enough that a genuinely stuck intent reaches a human
#: within the same shift.
DEFAULT_MAX_RETRIEVE_ATTEMPTS = 8

# NEW -> CALLING -> (SETTLED | INDETERMINATE | FAILED)
# INDETERMINATE -> RETRIEVE -> (SETTLED | FAILED | back to INDETERMINATE)
# INDETERMINATE -> SETTLED is allowed for a late authoritative webhook.
# (INDETERMINATE | RETRIEVE) -> ESCALATED when the retrieve budget is spent.
# ESCALATED -> (SETTLED | FAILED) only through a human, or a late authoritative
# webhook; needs_human stays raised either way so a person still signs it off.
# FAILED -> SETTLED is allowed but always raises needs_human: it means an
# earlier conclusion of "no money moved" was wrong, and a person must look.
# NEW -> BOOKED only. BOOKED -> nothing: not SETTLED (a webhook for a booked
# bill's session names no open intent, and a capture against the book is a
# different row in a different table), not CALLING (a booked bill is never
# minted; collecting it mints a COLLECTION link instead).
LEGAL: dict[str, frozenset[str]] = {
    NEW: frozenset({CALLING, BOOKED}),
    CALLING: frozenset({SETTLED, INDETERMINATE, FAILED}),
    INDETERMINATE: frozenset({RETRIEVE, SETTLED, ESCALATED}),
    RETRIEVE: frozenset({SETTLED, FAILED, INDETERMINATE, ESCALATED}),
    SETTLED: frozenset(),
    FAILED: frozenset({SETTLED}),
    ESCALATED: frozenset({SETTLED, FAILED}),
    BOOKED: frozenset(),
}

# ---------------------------------------------------------------- collections
#
# KHATA's money flow. A COLLECTION is one Payment Link minted for a book's
# whole outstanding balance with accept_partial on; a CAPTURE is one signed
# webhook crediting some paise against it. The two tables are deliberately NOT
# the intents table: an intent is exactly-once per (session, cycle, amount) and
# settles whole or not at all, while a collection settles in pieces, each piece
# keyed on the event id inside the signed envelope. Bolting partials onto
# `mark_settled` would have meant teaching the four-condition green predicate
# to accept a smaller number than it asked for, which is the one thing it must
# never learn.

COL_NEW = "NEW"
COL_CALLING = "CALLING"
#: The link exists and is payable. A second COLLECT while one is here is
#: refused by name (`collection_link_already_open`).
COL_OPEN = "OPEN"
COL_PAID = "PAID"
COL_EXPIRED = "EXPIRED"
COL_CANCELLED = "CANCELLED"
COL_FAILED = "FAILED"
#: The gateway call did not complete. The link may or may not exist, so a new
#: one is not minted over it — a capture arriving for it proves it exists and
#: moves it to OPEN; a person closes it otherwise.
COL_INDETERMINATE = "INDETERMINATE"

COL_STATES = frozenset({COL_NEW, COL_CALLING, COL_OPEN, COL_PAID, COL_EXPIRED,
                        COL_CANCELLED, COL_FAILED, COL_INDETERMINATE})
#: A book has at most ONE collection in any of these at a time.
COL_LIVE = frozenset({COL_NEW, COL_CALLING, COL_OPEN, COL_INDETERMINATE})
#: States a signed capture may credit against. EXPIRED is included: money
#: that landed just before the link expired and whose webhook arrived after is
#: still money that landed. PAID is not: a link the gateway has already closed
#: as paid cannot receive more.
COL_CREDITABLE = frozenset({COL_OPEN, COL_CALLING, COL_INDETERMINATE, COL_EXPIRED})
COL_LEGAL: dict[str, frozenset[str]] = {
    COL_NEW: frozenset({COL_CALLING}),
    COL_CALLING: frozenset({COL_OPEN, COL_INDETERMINATE, COL_FAILED}),
    COL_OPEN: frozenset({COL_PAID, COL_EXPIRED, COL_CANCELLED}),
    COL_INDETERMINATE: frozenset({COL_OPEN, COL_FAILED, COL_CANCELLED, COL_EXPIRED}),
    COL_EXPIRED: frozenset({COL_PAID}),
    COL_PAID: frozenset(),
    COL_CANCELLED: frozenset(),
    COL_FAILED: frozenset(),
}

CAP_CREDITED = "CREDITED"
#: Recorded under its event id so a retry cannot re-park it, but NOT counted:
#: the amount did not reconcile against the book and a person must look.
CAP_PARKED = "PARKED"

# ---------------------------------------------------------------- refunds
#
# WAAPSI's money flow. A REFUND is one line of a SETTLED bill going back to
# the customer through the gateway's Refunds API, and it is its OWN state
# machine in its OWN table, keyed to the original payment id and the line.
#
# It is deliberately NOT a negative intent and NOT a move on the intents
# table. The intent stays SETTLED for ever: the debit for (session, cycle,
# amount) executed ONCE, that answer was final, and a refund does not un-
# execute it — it is a second, separate movement of money in the other
# direction, with its own write-ahead, its own gateway call, its own signed
# callback. Bolting it onto `mark_settled` (a partial settlement, a negative
# amount) would have meant teaching the exactly-once predicate that a
# settled amount can change, which is the one thing it must never learn.
#
# The same discipline as an intent, in the other direction:
#   create_refund        -> NEW committed, connection closed. Proves the
#                           gateway was never asked.
#   mark_refund_calling  -> CALLING committed, connection closed.
#   ---- the gateway is asked to refund, with no DB connection held ----
#   mark_refund_requested / mark_refund_indeterminate / mark_refund_failed
#   record_refund_event  -> the signed `refund.processed` arrives. ONLY THIS
#                           moves a refund to PROCESSED; the HTTP answer to
#                           the refund call never does, however confident.
#
# A refund is REQUESTED in neutral ink until the gateway's own signed
# callback says it went through. Test-mode refunds take minutes; that state
# has to look finished on its own, so `requested_ts` is kept for the screen
# to show an age against.

RF_NEW = "NEW"
RF_CALLING = "CALLING"
#: The gateway accepted the request and gave it an id. Money has NOT moved
#: back yet as far as this counter knows; only a signed refund.processed
#: says that.
RF_REQUESTED = "REQUESTED"
RF_PROCESSED = "PROCESSED"
RF_FAILED = "FAILED"
#: The refund call did not complete. The gateway may or may not have taken
#: it. It is never retried blind — a second call could refund twice — and a
#: late signed webhook for it proves it happened and moves it on.
RF_INDETERMINATE = "INDETERMINATE"

RF_STATES = frozenset({RF_NEW, RF_CALLING, RF_REQUESTED, RF_PROCESSED,
                       RF_FAILED, RF_INDETERMINATE})
#: Refunds whose paise are COMMITTED against the bill. Everything but FAILED:
#: a refund that is merely asked for already counts, so two lines cannot
#: together refund more than the bill settled, whichever order the callbacks
#: land in.
RF_COMMITTED = frozenset({RF_NEW, RF_CALLING, RF_REQUESTED, RF_INDETERMINATE,
                          RF_PROCESSED})
#: States a signed refund.processed may move to PROCESSED. CALLING is here
#: on purpose: the webhook can beat the HTTP response to the refund call.
RF_PROCESSABLE = frozenset({RF_CALLING, RF_REQUESTED, RF_INDETERMINATE})
RF_LEGAL: dict[str, frozenset[str]] = {
    RF_NEW: frozenset({RF_CALLING}),
    RF_CALLING: frozenset({RF_REQUESTED, RF_INDETERMINATE, RF_FAILED, RF_PROCESSED}),
    RF_REQUESTED: frozenset({RF_PROCESSED, RF_FAILED}),
    RF_INDETERMINATE: frozenset({RF_REQUESTED, RF_PROCESSED, RF_FAILED}),
    RF_PROCESSED: frozenset(),
    RF_FAILED: frozenset(),
}

#: What one signed refund event did. APPLIED moved the refund; PARKED was
#: recorded under its event id and moved nothing because it did not
#: reconcile; ACKNOWLEDGED was a `refund.created` or a repeat that changed
#: no state.
RFE_APPLIED = "APPLIED"
RFE_PARKED = "PARKED"
RFE_ACKNOWLEDGED = "ACKNOWLEDGED"

# Gateway status vocabulary. Anything outside these three sets is UNKNOWN and
# is abstained on, never guessed.
GW_SETTLED = frozenset({"captured", "settled", "paid", "succeeded", "success"})
GW_FAILED = frozenset({"failed", "cancelled", "canceled", "declined", "expired", "voided"})
GW_PENDING = frozenset({"created", "authorized", "pending", "processing", "in_progress"})


class KernelError(RuntimeError):
    """The kernel refuses to proceed."""


class IllegalTransition(KernelError):
    """A state move that would break exactly-once. Always loud, never silent."""


class UnknownIntent(KernelError):
    """No row for that nonce."""


class UnknownCollection(KernelError):
    """No collection row for that id."""


class CollectionOpen(KernelError):
    """A book already has a live collection; a second link is refused by name."""

    def __init__(self, existing: "Collection") -> None:
        super().__init__(
            f"book {existing.book_id} already has collection "
            f"{existing.collection_id} in state {existing.state}"
        )
        self.existing = existing


class UnknownRefund(KernelError):
    """No refund row for that key."""


class RefundRefused(KernelError):
    """A refund this kernel will not write. `code` is a closed name paisa
    carries out verbatim: bill_not_settled, refund_exceeds_bill."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.extra = dict(extra)


# ---------------------------------------------------------------- records

@dataclass(frozen=True)
class Intent:
    nonce: str
    state: str
    session_id: str
    cycle: int
    amount_paise: int
    idem_key: str
    payment_id: str | None
    attempts: int
    retrieve_attempts: int
    needs_human: bool
    reason: str | None
    created_ts: str
    updated_ts: str
    #: KHATA: which book this bill was closed onto. Set only by `mark_booked`.
    book_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        """The money question is answered. ESCALATED is NOT terminal here."""
        return self.state in TERMINAL

    @property
    def is_escalated(self) -> bool:
        return self.state == ESCALATED

    @property
    def machine_done(self) -> bool:
        """No automatic code path will ever move this row again."""
        return self.state in MACHINE_TERMINAL


@dataclass(frozen=True)
class GatewayResult:
    """What a lookup of one nonce returned. `status` is the gateway's word."""

    found: bool
    payment_id: str | None = None
    amount_paise: int | None = None
    status: str = "unknown"

    @staticmethod
    def from_any(obj: Any) -> "GatewayResult":
        if isinstance(obj, GatewayResult):
            return obj
        if obj is None:
            return GatewayResult(found=False, status="not_found")
        if isinstance(obj, Mapping):
            status = str(obj.get("status", "unknown"))
            if "found" in obj:
                found = bool(obj["found"])
            else:
                found = status != "not_found"
            amt = obj.get("amount_paise", obj.get("amount"))
            if amt is not None:
                # paise() rejects float/bool/str: a gateway amount that is not
                # an exact integer never reaches a comparison.
                amt = int(paise(amt))
            pid = obj.get("payment_id", obj.get("id"))
            return GatewayResult(
                found=found,
                payment_id=None if pid is None else str(pid),
                amount_paise=amt,
                status=status,
            )
        raise KernelError(f"gateway_lookup_fn returned {type(obj).__name__}, "
                          "expected GatewayResult, Mapping or None")


GatewayLookup = Callable[[str], Any]


@dataclass(frozen=True)
class Collection:
    """One Payment Link minted for a book's outstanding balance."""

    collection_id: str
    book_id: str
    state: str
    amount_paise: int
    captured_paise: int
    payment_link_id: str | None
    short_url: str | None
    expire_by: int | None
    needs_human: bool
    reason: str | None
    created_ts: str
    updated_ts: str

    @property
    def is_live(self) -> bool:
        return self.state in COL_LIVE


@dataclass(frozen=True)
class Capture:
    """One signed webhook's worth of paise against a collection.

    `event_id` is the exactly-once key: the id inside the SIGNED envelope, or
    the sha256 of the signed bytes when the envelope carries none — never a
    header. `replayed` is True when this call found the row already there and
    did nothing.
    """

    event_id: str
    collection_id: str
    book_id: str
    state: str
    amount_paise: int
    payment_id: str | None
    link_amount_paid: int | None
    event: str | None
    reason: str | None
    created_ts: str
    replayed: bool = False
    #: The book's outstanding paise AFTER this capture was applied (or not).
    outstanding_paise: int = 0

    @property
    def credited(self) -> bool:
        return self.state == CAP_CREDITED


@dataclass(frozen=True)
class Refund:
    """One line of one settled bill going back through the gateway.

    `refund_key` is this counter's own id for the refund and travels to the
    gateway in `notes`, so the signed callback can name the row it is about.
    `gateway_refund_id` is the gateway's (`rfnd_…`), known only once the
    refund call answered. `replayed` is True when `create_refund` found the
    row already there and wrote nothing — pressing REFUND twice.
    """

    refund_key: str
    state: str
    nonce: str
    session_id: str
    cycle: int
    payment_id: str
    item_id: str
    sku_id: str
    amount_paise: int
    attempt: int
    gateway_refund_id: str | None
    receipt: str | None
    needs_human: bool
    reason: str | None
    created_ts: str
    updated_ts: str
    requested_ts: str | None
    processed_ts: str | None
    processed_event_id: str | None
    replayed: bool = False

    @property
    def is_committed(self) -> bool:
        return self.state in RF_COMMITTED

    @property
    def processed(self) -> bool:
        return self.state == RF_PROCESSED


@dataclass(frozen=True)
class RefundEvent:
    """One signed refund webhook, keyed on the event id inside the envelope.

    UNIQUE on `event_id`, like a capture: a redelivery finds its own row and
    does nothing. `state` says what the delivery did (APPLIED / PARKED /
    ACKNOWLEDGED); `replayed` is True when this call found the row already
    there.
    """

    event_id: str
    refund_key: str
    event: str | None
    state: str
    amount_paise: int | None
    gateway_refund_id: str | None
    reason: str | None
    created_ts: str
    replayed: bool = False


# ---------------------------------------------------------------- schema

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    nonce             TEXT    NOT NULL,
    idem_key          TEXT    NOT NULL,
    session_id        TEXT    NOT NULL,
    cycle             INTEGER NOT NULL,
    amount_paise      INTEGER NOT NULL,
    state             TEXT    NOT NULL,
    payment_id        TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    retrieve_attempts INTEGER NOT NULL DEFAULT 0,
    needs_human       INTEGER NOT NULL DEFAULT 0,
    reason            TEXT,
    created_ts        TEXT    NOT NULL,
    updated_ts        TEXT    NOT NULL,
    book_id           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intents_nonce    ON intents(nonce);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intents_idem_key ON intents(idem_key);
CREATE INDEX        IF NOT EXISTS ix_intents_state    ON intents(state);
CREATE TABLE IF NOT EXISTS collections (
    collection_id     TEXT    NOT NULL,
    book_id           TEXT    NOT NULL,
    state             TEXT    NOT NULL,
    amount_paise      INTEGER NOT NULL,
    captured_paise    INTEGER NOT NULL DEFAULT 0,
    payment_link_id   TEXT,
    short_url         TEXT,
    expire_by         INTEGER,
    needs_human       INTEGER NOT NULL DEFAULT 0,
    reason            TEXT,
    created_ts        TEXT    NOT NULL,
    updated_ts        TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_collections_id   ON collections(collection_id);
CREATE INDEX        IF NOT EXISTS ix_collections_book ON collections(book_id);
CREATE TABLE IF NOT EXISTS captures (
    event_id          TEXT    NOT NULL,
    collection_id     TEXT    NOT NULL,
    book_id           TEXT    NOT NULL,
    state             TEXT    NOT NULL,
    amount_paise      INTEGER NOT NULL,
    payment_id        TEXT,
    link_amount_paid  INTEGER,
    event             TEXT,
    reason            TEXT,
    created_ts        TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_captures_event      ON captures(event_id);
CREATE INDEX        IF NOT EXISTS ix_captures_collection ON captures(collection_id);
CREATE INDEX        IF NOT EXISTS ix_captures_book       ON captures(book_id);
CREATE TABLE IF NOT EXISTS refunds (
    refund_key         TEXT    NOT NULL,
    idem_key           TEXT    NOT NULL,
    nonce              TEXT    NOT NULL,
    session_id         TEXT    NOT NULL,
    cycle              INTEGER NOT NULL,
    payment_id         TEXT    NOT NULL,
    item_id            TEXT    NOT NULL,
    sku_id             TEXT    NOT NULL,
    amount_paise       INTEGER NOT NULL,
    attempt            INTEGER NOT NULL DEFAULT 0,
    state              TEXT    NOT NULL,
    gateway_refund_id  TEXT,
    receipt            TEXT,
    needs_human        INTEGER NOT NULL DEFAULT 0,
    reason             TEXT,
    created_ts         TEXT    NOT NULL,
    updated_ts         TEXT    NOT NULL,
    requested_ts       TEXT,
    processed_ts       TEXT,
    processed_event_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_refunds_key     ON refunds(refund_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_refunds_idem    ON refunds(idem_key);
CREATE INDEX        IF NOT EXISTS ix_refunds_nonce   ON refunds(nonce);
CREATE INDEX        IF NOT EXISTS ix_refunds_session ON refunds(session_id);
CREATE INDEX        IF NOT EXISTS ix_refunds_gateway ON refunds(gateway_refund_id);
CREATE TABLE IF NOT EXISTS refund_events (
    event_id           TEXT    NOT NULL,
    refund_key         TEXT    NOT NULL,
    event              TEXT,
    state              TEXT    NOT NULL,
    amount_paise       INTEGER,
    gateway_refund_id  TEXT,
    reason             TEXT,
    created_ts         TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_refund_events_id  ON refund_events(event_id);
CREATE INDEX        IF NOT EXISTS ix_refund_events_key ON refund_events(refund_key);
"""

_COLS = ("nonce, idem_key, session_id, cycle, amount_paise, state, payment_id, "
         "attempts, retrieve_attempts, needs_human, reason, created_ts, updated_ts, "
         "book_id")

_COL_COLS = ("collection_id, book_id, state, amount_paise, captured_paise, "
             "payment_link_id, short_url, expire_by, needs_human, reason, "
             "created_ts, updated_ts")

_CAP_COLS = ("event_id, collection_id, book_id, state, amount_paise, payment_id, "
             "link_amount_paid, event, reason, created_ts")

_RF_COLS = ("refund_key, idem_key, nonce, session_id, cycle, payment_id, item_id, "
            "sku_id, amount_paise, attempt, state, gateway_refund_id, receipt, "
            "needs_human, reason, created_ts, updated_ts, requested_ts, "
            "processed_ts, processed_event_id")

_RFE_COLS = ("event_id, refund_key, event, state, amount_paise, gateway_refund_id, "
             "reason, created_ts")


def new_refund_key() -> str:
    """A refund's id on this counter; travels to the gateway in `notes`."""
    return "rf_" + secrets.token_hex(12)


def refund_idem_key(payment_id: str, item_id: str, attempt: int) -> str:
    """The exactly-once key for a refund: one line of one payment, per attempt.

    `attempt` counts the FAILED refunds already recorded for the same line, so
    a refund the gateway definitely refused can be asked for again while a
    refund that is anywhere else — asked for, unknown, processed — finds its
    own row and is refused by name.
    """
    return hashlib.sha256(canonical({
        "payment_id": payment_id,
        "item_id": item_id,
        "attempt": int(attempt),
    })).hexdigest()


def new_collection_id() -> str:
    """A collection's id, also its gateway `reference_id`. 96 bits from the OS."""
    return "col_" + secrets.token_hex(12)


def idem_key(session_id: str, cycle: int, amount_paise: int) -> str:
    """The exactly-once key. Canonical JSON in, sha256 out, stable across hosts."""
    return hashlib.sha256(canonical({
        "session_id": session_id,
        "cycle": cycle,
        "amount_paise": amount_paise,
    })).hexdigest()


def new_nonce() -> str:
    """One-shot gateway idempotency token. 128 bits from the OS CSPRNG."""
    return "gwn_" + secrets.token_hex(16)


#: Read size for the tail scan that recovers another process's chain head.
_LEDGER_SCAN_CHUNK = 1 << 16


def _hash_of_line(raw: bytes) -> str:
    """The `hash` field of one ledger line, or genesis if the line is unusable.

    A line we cannot read is not a chain we may extend, so this abstains to
    GENESIS rather than guessing; `ledger.verify` will then say so loudly on the
    very next check instead of the corruption spreading silently.
    """
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return GENESIS
    h = rec.get("hash") if isinstance(rec, dict) else None
    return h if isinstance(h, str) and h else GENESIS


def _row_to_intent(row: sqlite3.Row) -> Intent:
    return Intent(
        nonce=row["nonce"],
        state=row["state"],
        session_id=row["session_id"],
        cycle=int(row["cycle"]),
        amount_paise=int(row["amount_paise"]),
        idem_key=row["idem_key"],
        payment_id=row["payment_id"],
        attempts=int(row["attempts"]),
        retrieve_attempts=int(row["retrieve_attempts"]),
        needs_human=bool(row["needs_human"]),
        reason=row["reason"],
        created_ts=row["created_ts"],
        updated_ts=row["updated_ts"],
        book_id=row["book_id"],
    )


def _row_to_collection(row: sqlite3.Row) -> Collection:
    return Collection(
        collection_id=row["collection_id"],
        book_id=row["book_id"],
        state=row["state"],
        amount_paise=int(row["amount_paise"]),
        captured_paise=int(row["captured_paise"]),
        payment_link_id=row["payment_link_id"],
        short_url=row["short_url"],
        expire_by=None if row["expire_by"] is None else int(row["expire_by"]),
        needs_human=bool(row["needs_human"]),
        reason=row["reason"],
        created_ts=row["created_ts"],
        updated_ts=row["updated_ts"],
    )


def _row_to_capture(row: sqlite3.Row, *, replayed: bool = False,
                    outstanding_paise: int = 0) -> Capture:
    return Capture(
        event_id=row["event_id"],
        collection_id=row["collection_id"],
        book_id=row["book_id"],
        state=row["state"],
        amount_paise=int(row["amount_paise"]),
        payment_id=row["payment_id"],
        link_amount_paid=(None if row["link_amount_paid"] is None
                          else int(row["link_amount_paid"])),
        event=row["event"],
        reason=row["reason"],
        created_ts=row["created_ts"],
        replayed=replayed,
        outstanding_paise=int(outstanding_paise),
    )


def _row_to_refund(row: sqlite3.Row, *, replayed: bool = False) -> Refund:
    return Refund(
        refund_key=row["refund_key"],
        state=row["state"],
        nonce=row["nonce"],
        session_id=row["session_id"],
        cycle=int(row["cycle"]),
        payment_id=row["payment_id"],
        item_id=row["item_id"],
        sku_id=row["sku_id"],
        amount_paise=int(row["amount_paise"]),
        attempt=int(row["attempt"]),
        gateway_refund_id=row["gateway_refund_id"],
        receipt=row["receipt"],
        needs_human=bool(row["needs_human"]),
        reason=row["reason"],
        created_ts=row["created_ts"],
        updated_ts=row["updated_ts"],
        requested_ts=row["requested_ts"],
        processed_ts=row["processed_ts"],
        processed_event_id=row["processed_event_id"],
        replayed=replayed,
    )


def _row_to_refund_event(row: sqlite3.Row, *, replayed: bool = False) -> RefundEvent:
    return RefundEvent(
        event_id=row["event_id"],
        refund_key=row["refund_key"],
        event=row["event"],
        state=row["state"],
        amount_paise=(None if row["amount_paise"] is None
                      else int(row["amount_paise"])),
        gateway_refund_id=row["gateway_refund_id"],
        reason=row["reason"],
        created_ts=row["created_ts"],
        replayed=replayed,
    )


def _book_key_ok(book_id: Any) -> bool:
    """A book id as khata mints them: `bk_` and hex. Checked before it is
    written into a row or an audit line, because it is echoed everywhere."""
    if not isinstance(book_id, str) or not book_id.startswith("bk_"):
        return False
    tail = book_id[3:]
    return 8 <= len(tail) <= 64 and all(c in "0123456789abcdef" for c in tail)


# ---------------------------------------------------------------- kernel

class Kernel:
    """Durable exactly-once intent store over stdlib sqlite3.

    db_path must be a real file: the whole point is that the row survives the
    process. ":memory:" is rejected rather than silently losing money state.
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        clock: Clock,
        ledger: Ledger,
        *,
        busy_timeout_ms: int = 15000,
        synchronous: str = "FULL",
        max_retrieve_attempts: int = DEFAULT_MAX_RETRIEVE_ATTEMPTS,
    ) -> None:
        p = os.fspath(db_path)
        if p in (":memory:", "") or p.startswith("file::memory:"):
            raise KernelError(
                "Kernel needs a durable file path. An in-memory DB cannot "
                "survive the crash it exists to survive."
            )
        self.db_path = p
        self.clock = clock
        self.ledger = ledger
        self._busy_timeout_ms = int(busy_timeout_ms)
        if synchronous.upper() not in ("FULL", "NORMAL", "EXTRA"):
            raise KernelError(f"bad synchronous pragma: {synchronous!r}")
        self._synchronous = synchronous.upper()
        if isinstance(max_retrieve_attempts, bool) or not isinstance(
            max_retrieve_attempts, int
        ):
            raise KernelError(
                f"max_retrieve_attempts must be an int, got "
                f"{max_retrieve_attempts!r}. An unbounded retrieve loop is a "
                "stuck till that never reaches a person."
            )
        if max_retrieve_attempts < 1:
            raise KernelError(
                f"max_retrieve_attempts must be >= 1, got {max_retrieve_attempts}. "
                "Zero would escalate every indeterminate intent without ever "
                "asking the gateway what happened."
            )
        self._max_retrieve = int(max_retrieve_attempts)
        # One re-entrant lock guards BOTH the clock advance and the ledger
        # append, so an audit line and its timestamp are atomic together.
        # Ledger keeps an in-memory head; concurrent appends would race it.
        self._audit_lock = threading.RLock()
        self._conn_lock = threading.Lock()
        self._open_conns = 0

        parent = os.path.dirname(os.path.abspath(p))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_ledger_lock()
        with self._conn() as con:
            con.executescript(_SCHEMA)
            # A kernel.db written before KHATA has an intents table with no
            # book_id column, and CREATE TABLE IF NOT EXISTS leaves it that way.
            # Add the column rather than fail: every existing row is a bill
            # that was never booked, which is exactly what NULL says.
            have = {r["name"] for r in con.execute("PRAGMA table_info(intents)")}
            if "book_id" not in have:
                con.execute("ALTER TABLE intents ADD COLUMN book_id TEXT")

    # ------------------------------------------------------------ plumbing

    @property
    def max_retrieve_attempts(self) -> int:
        """The machine's budget for "what happened to this nonce?" lookups."""
        return self._max_retrieve

    @property
    def open_connections(self) -> int:
        """Live count of DB connections this Kernel holds. Must be 0 while any
        network call is in flight; tests assert exactly that."""
        with self._conn_lock:
            return self._open_conns

    # -------------------------------------------------- cross-process ledger

    def _init_ledger_lock(self) -> None:
        """Open the sidecar lock file and snapshot where the ledger file ends.

        The lock is advisory and per open-file-description, so it serialises
        appends between PROCESSES, which is exactly the case `threading.RLock`
        cannot see. It is taken and released around each append rather than held
        for the Kernel's lifetime: a lock held at open would make a second
        writer fail rather than wait, and a till that refuses to audit because
        another process has the file is a worse failure than a queued append.
        """
        self._ledger_path = os.fspath(self.ledger.path)
        self._ledger_lock_path = self._ledger_path + ".lock"
        parent = os.path.dirname(os.path.abspath(self._ledger_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock_fd: int | None = None
        if _fcntl is not None:
            self._lock_fd = os.open(
                self._ledger_lock_path, os.O_CREAT | os.O_RDWR, 0o600
            )
        else:                                   # pragma: no cover - Windows
            # Still create the file so the path is observable, but say plainly
            # that this build has thread-level protection only.
            with open(self._ledger_lock_path, "a", encoding="utf-8"):
                pass
        # Where our knowledge of the file ends. Anything past this offset was
        # written by somebody else and must be read before we append.
        self._ledger_size = self._file_size(self._ledger_path)
        self._ledger_head = self.ledger.head
        self._ledger_count = self.ledger.count
        # Ledger is a plain dataclass with a cached head. If that ever stops
        # being true, fail here rather than silently writing a broken chain.
        self._head_syncable = hasattr(self.ledger, "_head") and hasattr(
            self.ledger, "_count"
        )

    @property
    def ledger_lock_path(self) -> str:
        """The sidecar file whose flock serialises appends across processes."""
        return self._ledger_lock_path

    @property
    def ledger_lock_is_cross_process(self) -> bool:
        """True when appends are safe between processes, not just threads.

        False on a platform without `fcntl`, or against a ledger whose head is
        not re-readable. Reported rather than assumed, so a deployment can tell
        what it actually has instead of trusting a docstring.
        """
        return self._lock_fd is not None and self._head_syncable

    @staticmethod
    def _file_size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    @contextmanager
    def _ledger_file_lock(self) -> Iterator[None]:
        if self._lock_fd is None:               # pragma: no cover - Windows
            yield
            return
        _fcntl.flock(self._lock_fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(self._lock_fd, _fcntl.LOCK_UN)

    def _sync_ledger_head(self) -> None:
        """Re-read the true chain head from disk. MUST run under the file lock.

        The ledger is append-only, so anything we have not seen is a contiguous
        tail: we scan only the bytes past our last known offset instead of the
        whole file, which keeps a shared ledger O(new bytes) per append rather
        than O(file). A file that SHRANK was replaced under us, so that case
        rescans from genesis rather than trusting a stale offset.
        """
        if not self._head_syncable:             # pragma: no cover - duck-typed
            return
        size = self._file_size(self._ledger_path)
        if size == self._ledger_size:
            return                              # nobody else has written
        if size < self._ledger_size:
            start, head, count = 0, GENESIS, 0
        else:
            start = self._ledger_size
            head, count = self._ledger_head, self._ledger_count

        last = b""
        try:
            f = open(self._ledger_path, "rb")
        except FileNotFoundError:               # pragma: no cover - raced unlink
            head, count, size = GENESIS, 0, 0
        else:
            with f:
                f.seek(start)
                carry = b""
                while True:
                    chunk = f.read(_LEDGER_SCAN_CHUNK)
                    if not chunk:
                        break
                    carry += chunk
                    parts = carry.split(b"\n")
                    carry = parts.pop()
                    for raw in parts:
                        if raw.strip():
                            last, count = raw, count + 1
                if carry.strip():
                    last, count = carry, count + 1
        if last:
            head = _hash_of_line(last)

        self._ledger_size, self._ledger_head, self._ledger_count = size, head, count
        # Push the truth back into the shared Ledger object. Private attributes
        # on purpose: Ledger has no public setter, and inventing one would mean
        # editing a module this file does not own.
        self.ledger._head = head                # noqa: SLF001
        self.ledger._count = count              # noqa: SLF001

    def audit_append(self, module: str, **fields: Any) -> str:
        """Append one ledger line safely against other PROCESSES, not just threads.

        Public because `paisa` shares this ledger file and must go through the
        same lock; a service that audits around the kernel would reintroduce
        exactly the chain break this method exists to prevent.
        """
        with self._audit_lock, self._ledger_file_lock():
            self._sync_ledger_head()
            h = self.ledger.append(
                ts=self.clock.now_iso(), module=module, **fields
            )
            self._ledger_size = self._file_size(self._ledger_path)
            self._ledger_head = h
            self._ledger_count = self.ledger.count
            return h

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        with self._conn_lock:
            self._open_conns += 1
        try:
            con.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            # `PRAGMA journal_mode=WAL` is NOT covered by busy_timeout. Changing
            # the journal mode needs a brief exclusive lock, and SQLite returns
            # SQLITE_BUSY for it IMMEDIATELY when another connection is attached
            # instead of waiting the way ordinary statements do. Running it on
            # every connect therefore turns "another process is using the
            # database" into "database is locked", which is exactly the case
            # this kernel exists to survive: eight processes racing for one
            # nonce is the exactly-once test, not an unusual load.
            #
            # WAL is a persistent property of the FILE, so it only has to be set
            # by whoever finds the database in some other mode. Reading first
            # makes the common path a no-op and leaves the write to the one
            # connection that actually needs it.
            if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                try:
                    con.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    # Somebody else is mid-flight with the same idea. Their
                    # write is as good as ours; what must not happen is this
                    # connection dying over a mode it is about to be given.
                    mode = str(con.execute("PRAGMA journal_mode").fetchone()[0])
                    if mode.lower() not in ("wal", "delete", "truncate",
                                            "persist", "memory", "off"):
                        raise
            con.execute(f"PRAGMA synchronous={self._synchronous}")
            yield con
        finally:
            con.close()
            with self._conn_lock:
                self._open_conns -= 1

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE so two writers serialise at the start, not halfway."""
        with self._conn() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def _now(self) -> str:
        with self._audit_lock:
            return self.clock.now_iso()

    def _audit(self, event: str, it: Intent, *, from_state: str | None,
               **extra: Any) -> str:
        """Append one audit line. Called AFTER the DB commit, so the ledger can
        never claim a transition that did not persist."""
        return self.audit_append(
            MODULE,
            event=event,
            nonce=it.nonce,
            session_id=it.session_id,
            cycle=it.cycle,
            amount_paise=it.amount_paise,
            from_state=from_state,
            to_state=it.state,
            payment_id=it.payment_id,
            reason=it.reason,
            needs_human=it.needs_human,
            **extra,
        )

    def close(self) -> None:
        """Release the ledger lock fd. Idempotent; the DB holds no open handle."""
        fd, self._lock_fd = getattr(self, "_lock_fd", None), None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:                     # pragma: no cover
                pass

    def __del__(self) -> None:                  # pragma: no cover - GC timing
        self.close()

    # ------------------------------------------------------------ reads

    def get(self, nonce: str) -> Intent:
        with self._conn() as con:
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
        if row is None:
            raise UnknownIntent(f"no intent for nonce {nonce!r}")
        return _row_to_intent(row)

    def find(self, session_id: str, amount_paise: int, cycle: int = 0) -> Intent | None:
        key = idem_key(session_id, cycle, int(paise(amount_paise)))
        with self._conn() as con:
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE idem_key=?", (key,)
            ).fetchone()
        return None if row is None else _row_to_intent(row)

    def all_intents(self) -> list[Intent]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents ORDER BY created_ts, nonce"
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def count(self) -> int:
        with self._conn() as con:
            return int(con.execute("SELECT COUNT(*) FROM intents").fetchone()[0])

    def intents_needing_retrieve(self) -> list[Intent]:
        """Everything whose outcome is unknown and still machine-resolvable.

        Includes RETRIEVE rows: a crash mid-reconcile leaves one, and re-running
        a read-only lookup is free. Excludes needs_human rows -- those are
        parked deliberately and must not be swept into an automatic decision.
        """
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents "
                "WHERE state IN (?,?) AND needs_human=0 "
                "ORDER BY created_ts, nonce",
                (INDETERMINATE, RETRIEVE),
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def intents_needing_human(self) -> list[Intent]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents WHERE needs_human=1 "
                "ORDER BY created_ts, nonce"
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def escalated_intents(self) -> list[Intent]:
        """Rows the machine gave up on. The operator's queue, and nothing else."""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents WHERE state=? ORDER BY created_ts, nonce",
                (ESCALATED,),
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    # ------------------------------------------------------------ step 1

    def create_intent(self, session_id: str, amount_paise: int,
                      cycle: int = 0) -> Intent:
        """Write-ahead. Idempotent per (session_id, cycle, amount_paise).

        Returns the SAME nonce for a repeated key, whatever state that row is
        in -- including FAILED. Re-charging after a failure is a new cycle, by
        construction, so a caller cannot accidentally mint a second debit for a
        key that already has one.
        """
        if not isinstance(session_id, str) or not session_id.strip():
            raise KernelError("session_id must be a non-empty string")
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            raise KernelError(f"cycle must be an int, got {cycle!r}")
        if cycle < 0:
            raise KernelError(f"cycle must be >= 0, got {cycle}")
        amt = int(paise(amount_paise))  # rejects float/bool/str at the door
        if amt <= 0:
            raise MoneyError(f"a debit must be a positive amount, got {amt} paise")

        key = idem_key(session_id, cycle, amt)
        ts = self._now()
        candidate = new_nonce()

        with self._tx() as con:
            con.execute(
                "INSERT OR IGNORE INTO intents "
                "(nonce, idem_key, session_id, cycle, amount_paise, state, "
                " payment_id, attempts, retrieve_attempts, needs_human, reason, "
                " created_ts, updated_ts) "
                "VALUES (?,?,?,?,?,?,NULL,0,0,0,NULL,?,?)",
                (candidate, key, session_id, cycle, amt, NEW, ts, ts),
            )
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE idem_key=?", (key,)
            ).fetchone()
        # connection closed here, before anything else happens
        it = _row_to_intent(row)
        if it.nonce == candidate:
            self._audit("intent.created", it, from_state=None)
        return it

    # ------------------------------------------------------------ step 2..4

    def _transition(self, nonce: str, to_state: str, *, event: str,
                    payment_id: str | None = None, reason: str | None = None,
                    bump_attempts: bool = False,
                    bump_retrieve: bool = False,
                    needs_human: bool | None = None,
                    audit_extra: Mapping[str, Any] | None = None) -> Intent:
        with self._tx() as con:
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
            if row is None:
                raise UnknownIntent(f"no intent for nonce {nonce!r}")
            cur = _row_to_intent(row)
            if to_state not in LEGAL[cur.state]:
                raise IllegalTransition(
                    f"{cur.state} -> {to_state} is not a legal move for "
                    f"{nonce}. Exactly-once forbids it."
                )
            if payment_id is not None and cur.payment_id not in (None, payment_id):
                raise IllegalTransition(
                    f"{nonce} already carries payment {cur.payment_id!r}; "
                    f"refusing to overwrite with {payment_id!r} -- that would "
                    "mean two debits for one intent."
                )
            flag = cur.needs_human if needs_human is None else needs_human
            # FAILED -> SETTLED means an earlier "no money moved" was wrong.
            if cur.state == FAILED and to_state == SETTLED:
                flag = True
                reason = "settled_after_failed"
            ts = self._now()
            con.execute(
                "UPDATE intents SET state=?, payment_id=COALESCE(?, payment_id), "
                "reason=?, needs_human=?, updated_ts=?, "
                "attempts=attempts+?, retrieve_attempts=retrieve_attempts+? "
                "WHERE nonce=?",
                (to_state, payment_id, reason, 1 if flag else 0, ts,
                 1 if bump_attempts else 0, 1 if bump_retrieve else 0, nonce),
            )
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
        # connection closed here
        it = _row_to_intent(row)
        self._audit(event, it, from_state=cur.state,
                    **(dict(audit_extra) if audit_extra else {}))
        return it

    def mark_calling(self, nonce: str) -> Intent:
        """NEW -> CALLING. Commit this, close the connection, THEN call out.

        Anything not in NEW raises: a blind retry of an indeterminate charge is
        structurally impossible, which is the point.
        """
        return self._transition(nonce, CALLING, event="intent.calling",
                                reason=None, bump_attempts=True)

    def mark_settled(self, nonce: str, payment_id: str) -> Intent:
        if not isinstance(payment_id, str) or not payment_id.strip():
            raise KernelError("payment_id must be a non-empty string")
        cur = self.get(nonce)
        if cur.state == SETTLED:
            if cur.payment_id != payment_id:
                raise IllegalTransition(
                    f"{nonce} is already SETTLED as {cur.payment_id!r}; a second "
                    f"payment {payment_id!r} for the same intent is a double charge."
                )
            return cur  # idempotent replay of the same webhook: no new audit line
        return self._transition(nonce, SETTLED, event="intent.settled",
                                payment_id=payment_id, reason=None)

    def mark_indeterminate(self, nonce: str, reason: str = "timeout") -> Intent:
        """The call may or may not have moved money. Park it for RETRIEVE."""
        return self._transition(nonce, INDETERMINATE, event="intent.indeterminate",
                                reason=str(reason))

    def mark_failed(self, nonce: str, reason: str = "declined") -> Intent:
        """Only for a DEFINITE negative from the gateway. Never for a timeout."""
        return self._transition(nonce, FAILED, event="intent.failed",
                                reason=str(reason))

    def mark_booked(self, nonce: str, book_id: str) -> Intent:
        """NEW -> BOOKED. The bill closes onto a customer's book; no gateway.

        Legal from NEW only. NEW is the write-ahead proof that no link was ever
        minted for this basket, so booking it cannot leave a payable link
        behind; anything else raises IllegalTransition, and a CALLING or
        SETTLED bill therefore cannot ALSO go on the book.

        Idempotent for the same book: a BOOKED row asked to be booked onto the
        same book again is returned as it is, with no second audit line. Asked
        for a DIFFERENT book it raises — moving a debt between customers is a
        human's job with a human's name on it, not a retry.
        """
        if not _book_key_ok(book_id):
            raise KernelError(f"book_id {book_id!r} is not a khata book id")
        cur = self.get(nonce)
        if cur.state == BOOKED:
            if cur.book_id != book_id:
                raise IllegalTransition(
                    f"{nonce} is already on book {cur.book_id!r}; refusing to "
                    f"move it to {book_id!r}. A debt is not re-homed by a retry."
                )
            return cur
        with self._tx() as con:
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
            if row is None:
                raise UnknownIntent(f"no intent for nonce {nonce!r}")
            cur = _row_to_intent(row)
            if BOOKED not in LEGAL[cur.state]:
                raise IllegalTransition(
                    f"{cur.state} -> {BOOKED} is not a legal move for {nonce}: "
                    "only a bill the gateway was never asked about can go on "
                    "the book."
                )
            ts = self._now()
            con.execute(
                "UPDATE intents SET state=?, book_id=?, reason=?, updated_ts=? "
                "WHERE nonce=?",
                (BOOKED, book_id, f"booked:{book_id}", ts, nonce),
            )
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
        it = _row_to_intent(row)
        self._audit("intent.booked", it, from_state=cur.state,
                    book_id=book_id, booked=True, minted=False)
        return it

    def booked_intents(self, book_id: str) -> list[Intent]:
        """Every bill closed onto one book, oldest first."""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents WHERE state=? AND book_id=? "
                "ORDER BY created_ts, nonce",
                (BOOKED, book_id),
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    # ------------------------------------------------------------ recovery

    def recover(self) -> list[Intent]:
        """Startup sweep. Call once, before driving any new intent.

        A CALLING row at startup means the process died between committing
        CALLING and learning the answer. That is exactly the indeterminate case.
        NEW rows are deliberately left alone: the write-ahead ordering proves
        the gateway was never reached, so they are safe to drive forward.
        """
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents WHERE state=? ORDER BY created_ts, nonce",
                (CALLING,),
            ).fetchall()
        out: list[Intent] = []
        for r in rows:
            out.append(self.mark_indeterminate(
                r["nonce"], reason="crash_between_commit_and_result"))
        return out

    def reconcile(self, nonce: str, gateway_lookup_fn: GatewayLookup) -> Intent:
        """INDETERMINATE -> RETRIEVE -> ask the gateway -> settle or abstain.

        There is no charge path in here. The only thing this method can do to
        the outside world is a read-only lookup of one nonce, so running it
        twice, or a hundred times, cannot move money twice.
        """
        cur = self.get(nonce)
        if cur.machine_done:
            # SETTLED/FAILED are decided; ESCALATED belongs to a person now.
            # Either way the gateway is not called: `gateway_lookup_fn` is never
            # reached, which is what makes repeated sweeps free.
            return cur
        if cur.state in (NEW, CALLING):
            raise IllegalTransition(
                f"{nonce} is {cur.state}: mark_indeterminate() it first. "
                "Reconciling a live call would race the call itself."
            )
        if cur.retrieve_attempts >= self._max_retrieve:
            # The budget is spent. Abstaining once more would just re-queue the
            # same question forever; hand it to a human instead. Note where this
            # sits: BEFORE the lookup, so escalation costs nothing and, more
            # importantly, cannot be reached from a code path that charges.
            return self._escalate(
                nonce,
                f"retrieve_budget_exhausted:{cur.retrieve_attempts}"
                f"/{self._max_retrieve}",
            )
        if cur.state == INDETERMINATE:
            cur = self._transition(nonce, RETRIEVE, event="intent.retrieve",
                                   reason="reconcile", bump_retrieve=True)
        # ---- connection is closed; the network call happens outside any tx ----
        try:
            raw = gateway_lookup_fn(nonce)
        except Exception as exc:  # the gateway is still unreachable
            return self._transition(
                nonce, INDETERMINATE, event="intent.indeterminate",
                reason=f"lookup_failed:{type(exc).__name__}")
        try:
            res = GatewayResult.from_any(raw)
        except (KernelError, MoneyError) as exc:
            return self._park(nonce, f"bad_lookup_response:{exc}")

        if not res.found or res.status == "not_found":
            # The gateway has no record of this nonce. The write-ahead row
            # exists, so we know we tried; the gateway knows it never landed.
            return self._transition(nonce, FAILED, event="intent.failed",
                                    reason="gateway_never_saw_nonce")

        status = res.status.lower()
        if status in GW_PENDING:
            return self._transition(nonce, INDETERMINATE,
                                    event="intent.indeterminate",
                                    reason=f"gateway_pending:{status}")
        if status in GW_FAILED:
            return self._transition(nonce, FAILED, event="intent.failed",
                                    reason=f"gateway_status:{status}")
        if status not in GW_SETTLED:
            # INVARIANT 7: unknown is not a guess. Abstain and sweep again.
            return self._transition(nonce, INDETERMINATE,
                                    event="intent.indeterminate",
                                    reason=f"unknown_status:{status}")

        # Gateway says settled. Verify it is OUR money before believing it.
        if res.amount_paise is None:
            return self._park(nonce, "settled_without_amount")
        if res.amount_paise != cur.amount_paise:
            return self._park(
                nonce,
                f"amount_mismatch:gateway={res.amount_paise}:intent={cur.amount_paise}")
        if not res.payment_id:
            return self._park(nonce, "settled_without_payment_id")
        return self._transition(nonce, SETTLED, event="intent.settled",
                                payment_id=res.payment_id,
                                reason=f"reconciled:{status}")

    def _park(self, nonce: str, reason: str) -> Intent:
        """Stop. Do not settle, do not fail, do not retry. Flag a human.

        Used when the gateway's answer is self-inconsistent (a capture for the
        wrong amount, a capture with no id). Guessing either way here is how
        people get charged twice.
        """
        cur = self.get(nonce)
        # Parking is a flag, not a move: the row stays in whatever unknown state
        # it is already in, so nothing downstream can mistake it for a decision.
        with self._tx() as con:
            ts = self._now()
            con.execute(
                "UPDATE intents SET needs_human=1, reason=?, updated_ts=? "
                "WHERE nonce=?", (reason, ts, nonce))
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
        it = _row_to_intent(row)
        self._audit("intent.parked", it, from_state=cur.state)
        return it

    def _escalate(self, nonce: str, reason: str) -> Intent:
        """The machine is out of budget. Stop, flag a human, charge nothing.

        This is the terminal case of INVARIANT 7: after N honest attempts the
        gateway still will not say what happened, and the only remaining moves
        are "guess" or "ask a person". It parks in ESCALATED, which no sweep
        reads, so the abstention loop is bounded by construction rather than by
        an operator remembering to look.
        """
        spent = self.get(nonce).retrieve_attempts
        return self._transition(
            nonce, ESCALATED, event="intent.escalated", reason=reason,
            needs_human=True, audit_extra={
                "retrieve_attempts": spent,
                "max_retrieve_attempts": self._max_retrieve,
            },
        )

    def resolve_escalated(
        self,
        nonce: str,
        outcome: str,
        *,
        operator: str,
        payment_id: str | None = None,
        note: str = "",
    ) -> Intent:
        """A human closes an ESCALATED row. Takes no gateway: it cannot charge.

        `outcome` is SETTLED (a person found the payment on the gateway's own
        dashboard and is copying its id in) or FAILED (a person confirmed no
        money moved). needs_human stays raised afterwards so the row keeps its
        "a person decided this" mark in every later report.
        """
        if not isinstance(operator, str) or not operator.strip():
            raise KernelError(
                "resolve_escalated needs a non-empty operator: an unattributed "
                "manual settlement is indistinguishable from a bug."
            )
        if outcome not in (SETTLED, FAILED):
            raise KernelError(
                f"a human may resolve an escalated intent to {SETTLED} or "
                f"{FAILED}, not {outcome!r}."
            )
        if outcome == SETTLED and not (isinstance(payment_id, str) and payment_id.strip()):
            raise KernelError(
                "resolving to SETTLED needs the gateway's payment_id. Without a "
                "reference this is a guess, and INVARIANT 7 forbids guessing."
            )
        cur = self.get(nonce)
        if cur.state != ESCALATED:
            raise IllegalTransition(
                f"{nonce} is {cur.state}, not {ESCALATED}; resolve_escalated is "
                "only for rows the machine has given up on."
            )
        reason = f"human_resolved:{operator.strip()}"
        if note:
            reason = f"{reason}:{note}"
        return self._transition(
            nonce, outcome, event="intent.resolved",
            payment_id=payment_id if outcome == SETTLED else None,
            reason=reason, needs_human=True,
            audit_extra={"operator": operator.strip(), "note": note},
        )

    def sweep(self, gateway_lookup_fn: GatewayLookup) -> list[Intent]:
        """Reconcile every machine-resolvable unknown. Safe to run on a timer.

        Bounded work: `intents_needing_retrieve` already excludes ESCALATED and
        parked rows, and `reconcile` escalates anything out of budget, so the
        set this iterates over strictly shrinks even against a gateway that
        never answers.
        """
        return [self.reconcile(it.nonce, gateway_lookup_fn)
                for it in self.intents_needing_retrieve()]

    # ------------------------------------------------------------ collections
    #
    # The same discipline as intents, in a second state machine:
    #   create_collection -> NEW committed, connection closed
    #   mark_collection_calling -> CALLING committed, connection closed
    #   ---- the gateway mints the link, with no DB connection held ----
    #   mark_collection_open / mark_collection_indeterminate / mark_collection_failed
    # and then captures arrive one signed webhook at a time.

    def _audit_collection(self, event: str, col: Collection, *,
                          from_state: str | None, **extra: Any) -> str:
        return self.audit_append(
            MODULE,
            event=event,
            collection_id=col.collection_id,
            book_id=col.book_id,
            amount_paise=col.amount_paise,
            captured_paise=col.captured_paise,
            from_state=from_state,
            to_state=col.state,
            payment_link_id=col.payment_link_id,
            short_url=col.short_url,
            expire_by=col.expire_by,
            reason=col.reason,
            needs_human=col.needs_human,
            **extra,
        )

    def get_collection(self, collection_id: str) -> Collection:
        with self._conn() as con:
            row = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE collection_id=?",
                (collection_id,),
            ).fetchone()
        if row is None:
            raise UnknownCollection(f"no collection {collection_id!r}")
        return _row_to_collection(row)

    def collections_for(self, book_id: str) -> list[Collection]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE book_id=? "
                "ORDER BY created_ts, collection_id", (book_id,),
            ).fetchall()
        return [_row_to_collection(r) for r in rows]

    def all_collections(self) -> list[Collection]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COL_COLS} FROM collections "
                "ORDER BY created_ts, collection_id"
            ).fetchall()
        return [_row_to_collection(r) for r in rows]

    def live_collection_for(self, book_id: str) -> Collection | None:
        """The one collection a book may have in flight, or None."""
        with self._conn() as con:
            row = self._live_collection_row(con, book_id)
        return None if row is None else _row_to_collection(row)

    @staticmethod
    def _live_collection_row(con: sqlite3.Connection, book_id: str):
        marks = ",".join("?" for _ in COL_LIVE)
        return con.execute(
            f"SELECT {_COL_COLS} FROM collections WHERE book_id=? "
            f"AND state IN ({marks}) ORDER BY created_ts DESC LIMIT 1",
            (book_id, *sorted(COL_LIVE)),
        ).fetchone()

    @staticmethod
    def _booked_paise(con: sqlite3.Connection, book_id: str) -> int:
        row = con.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) FROM intents "
            "WHERE state=? AND book_id=?", (BOOKED, book_id),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _credited_paise(con: sqlite3.Connection, book_id: str) -> int:
        row = con.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) FROM captures "
            "WHERE state=? AND book_id=?", (CAP_CREDITED, book_id),
        ).fetchone()
        return int(row[0])

    def outstanding_paise(self, book_id: str) -> int:
        """sum(booked bills) - sum(credited captures). Integers, from the rows."""
        with self._conn() as con:
            return self._booked_paise(con, book_id) - self._credited_paise(con, book_id)

    def create_collection(self, book_id: str, amount_paise: int) -> Collection:
        """Write-ahead for one collection link. ONE live collection per book.

        The "already open" check and the insert happen in one BEGIN IMMEDIATE
        transaction, so two COLLECT presses racing each other produce one row
        and one CollectionOpen, never two links for one balance.

        `amount_paise` must equal the book's outstanding as THESE tables see
        it; a caller's figure that disagrees is refused here rather than
        minted, because the link's amount is what the customer will be asked
        to pay.
        """
        if not _book_key_ok(book_id):
            raise KernelError(f"book_id {book_id!r} is not a khata book id")
        amt = int(paise(amount_paise))
        if amt <= 0:
            raise MoneyError(f"a collection must be for a positive amount, got {amt}")
        ts = self._now()
        cid = new_collection_id()
        with self._tx() as con:
            live = self._live_collection_row(con, book_id)
            if live is not None:
                raise CollectionOpen(_row_to_collection(live))
            due = self._booked_paise(con, book_id) - self._credited_paise(con, book_id)
            if due != amt:
                raise KernelError(
                    f"book {book_id} has {due} paise outstanding by this "
                    f"kernel's own rows; refusing to mint a link for {amt}."
                )
            con.execute(
                "INSERT INTO collections (collection_id, book_id, state, "
                "amount_paise, captured_paise, payment_link_id, short_url, "
                "expire_by, needs_human, reason, created_ts, updated_ts) "
                "VALUES (?,?,?,?,0,NULL,NULL,NULL,0,NULL,?,?)",
                (cid, book_id, COL_NEW, amt, ts, ts),
            )
            row = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE collection_id=?", (cid,)
            ).fetchone()
        col = _row_to_collection(row)
        self._audit_collection("collection.created", col, from_state=None)
        return col

    def _collection_transition(
        self, collection_id: str, to_state: str, *, event: str,
        reason: str | None = None, payment_link_id: str | None = None,
        short_url: str | None = None, expire_by: int | None = None,
        needs_human: bool | None = None, audit_extra: Mapping[str, Any] | None = None,
    ) -> Collection:
        with self._tx() as con:
            row = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE collection_id=?",
                (collection_id,),
            ).fetchone()
            if row is None:
                raise UnknownCollection(f"no collection {collection_id!r}")
            cur = _row_to_collection(row)
            if to_state not in COL_LEGAL[cur.state]:
                raise IllegalTransition(
                    f"collection {collection_id}: {cur.state} -> {to_state} is "
                    "not a legal move."
                )
            flag = cur.needs_human if needs_human is None else needs_human
            ts = self._now()
            con.execute(
                "UPDATE collections SET state=?, reason=?, needs_human=?, "
                "payment_link_id=COALESCE(?, payment_link_id), "
                "short_url=COALESCE(?, short_url), "
                "expire_by=COALESCE(?, expire_by), updated_ts=? "
                "WHERE collection_id=?",
                (to_state, reason, 1 if flag else 0, payment_link_id, short_url,
                 None if expire_by is None else int(expire_by), ts, collection_id),
            )
            row = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE collection_id=?",
                (collection_id,),
            ).fetchone()
        col = _row_to_collection(row)
        self._audit_collection(event, col, from_state=cur.state,
                               **(dict(audit_extra) if audit_extra else {}))
        return col

    def mark_collection_calling(self, collection_id: str) -> Collection:
        """NEW -> CALLING. Commit, close, THEN ask the gateway for the link."""
        return self._collection_transition(
            collection_id, COL_CALLING, event="collection.calling")

    def mark_collection_open(self, collection_id: str, *, payment_link_id: str,
                             short_url: str | None, expire_by: int | None) -> Collection:
        """CALLING -> OPEN: the gateway minted the link. It is payable now."""
        if not isinstance(payment_link_id, str) or not payment_link_id.strip():
            raise KernelError("payment_link_id must be a non-empty string")
        return self._collection_transition(
            collection_id, COL_OPEN, event="collection.open",
            payment_link_id=payment_link_id, short_url=short_url,
            expire_by=expire_by, reason=None)

    def mark_collection_indeterminate(self, collection_id: str,
                                      reason: str = "timeout") -> Collection:
        """The mint may or may not have happened. Nothing is retried blind."""
        return self._collection_transition(
            collection_id, COL_INDETERMINATE, event="collection.indeterminate",
            reason=str(reason), needs_human=True)

    def close_collection(self, collection_id: str, outcome: str,
                         reason: str) -> Collection:
        """OPEN/INDETERMINATE -> EXPIRED | CANCELLED | FAILED. Money-neutral.

        Used for a signed `payment_link.expired`/`.cancelled`, for a link whose
        `expire_by` has passed, and for a person closing an indeterminate one.
        It credits nothing and un-credits nothing.
        """
        if outcome not in (COL_EXPIRED, COL_CANCELLED, COL_FAILED):
            raise KernelError(
                f"a collection closes to EXPIRED, CANCELLED or FAILED, not {outcome!r}")
        return self._collection_transition(
            collection_id, outcome, event="collection.closed", reason=str(reason))

    def captures_for(self, collection_id: str) -> list[Capture]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_CAP_COLS} FROM captures WHERE collection_id=? "
                "ORDER BY created_ts, event_id", (collection_id,),
            ).fetchall()
        return [_row_to_capture(r) for r in rows]

    def captures_for_book(self, book_id: str) -> list[Capture]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_CAP_COLS} FROM captures WHERE book_id=? "
                "ORDER BY created_ts, event_id", (book_id,),
            ).fetchall()
        return [_row_to_capture(r) for r in rows]

    def record_capture(
        self, *, event_id: str, collection_id: str, amount_paise: int,
        payment_id: str | None, link_amount_paid: int | None,
        event: str | None, final: bool,
    ) -> Capture:
        """One signed webhook's paise against one collection. EXACTLY ONCE.

        The row is INSERT OR IGNORE'd under a UNIQUE index on `event_id` inside
        a BEGIN IMMEDIATE transaction. A replay — the same signed envelope
        delivered again — finds its own row and returns it with
        `replayed=True`, crediting nothing and writing no audit line. That is
        the whole of replay safety and it does not depend on any in-memory
        "seen" set surviving a restart.

        WHAT IS CREDITED is `amount_paise` — the amount the SIGNED body says
        this payment carried — and never a figure the caller worked out.

        WHAT IS NOT CREDITED (parked, needs_human, still recorded under its
        event id so a retry cannot re-park it):
          * a capture that would take the book's credited total past its
            booked total. A customer cannot have paid more than they owe;
            either a booking is missing or the money is somebody else's, and
            netting it away would hide exactly the row a person must see.
          * a capture against a collection that cannot receive one (PAID,
            CANCELLED, FAILED).
        A `final` capture (payment_link.paid) closes the collection PAID. If
        the collection's credited total then differs from the amount it was
        minted for — a partial's webhook never arrived — the row is flagged
        needs_human rather than the difference being invented.
        """
        if not isinstance(event_id, str) or not event_id:
            raise KernelError("event_id must be a non-empty string: it is the exactly-once key")
        amt = int(paise(amount_paise))
        if amt <= 0:
            raise MoneyError(f"a capture must be a positive amount, got {amt}")
        paid = None if link_amount_paid is None else int(paise(link_amount_paid))
        ts = self._now()
        with self._tx() as con:
            existing = con.execute(
                f"SELECT {_CAP_COLS} FROM captures WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is not None:
                book_id = existing["book_id"]
                due = self._booked_paise(con, book_id) - self._credited_paise(con, book_id)
                return _row_to_capture(existing, replayed=True, outstanding_paise=due)
            row = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE collection_id=?",
                (collection_id,),
            ).fetchone()
            if row is None:
                raise UnknownCollection(f"no collection {collection_id!r}")
            col = _row_to_collection(row)
            book_id = col.book_id
            due_before = self._booked_paise(con, book_id) - self._credited_paise(con, book_id)

            state = CAP_CREDITED
            reason: str | None = None
            if col.state not in COL_CREDITABLE:
                state, reason = CAP_PARKED, f"collection_not_creditable:{col.state}"
            elif amt > due_before:
                state, reason = CAP_PARKED, f"over_capture:{amt}>outstanding:{due_before}"
            con.execute(
                "INSERT OR IGNORE INTO captures (event_id, collection_id, book_id, "
                "state, amount_paise, payment_id, link_amount_paid, event, reason, "
                "created_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (event_id, collection_id, book_id, state, amt, payment_id, paid,
                 event, reason, ts),
            )
            col_from = col.state
            col_reason = col.reason
            col_state = col.state
            col_human = col.needs_human
            if state == CAP_CREDITED:
                captured = int(col.captured_paise) + amt
                if col_state in (COL_CALLING, COL_INDETERMINATE):
                    # The webhook proves the link exists: the mint we could
                    # not confirm did happen.
                    col_state = COL_OPEN
                    col_reason = "opened_by_capture"
                if final:
                    if col_state in (COL_OPEN, COL_EXPIRED):
                        col_state = COL_PAID
                    if captured != col.amount_paise:
                        col_human = True
                        col_reason = (f"paid_but_captures_disagree:captured={captured}"
                                      f":asked={col.amount_paise}")
            else:
                captured = int(col.captured_paise)
                col_human = True
                col_reason = f"capture_parked:{reason}"
            con.execute(
                "UPDATE collections SET captured_paise=?, state=?, reason=?, "
                "needs_human=?, updated_ts=? WHERE collection_id=?",
                (captured, col_state, col_reason, 1 if col_human else 0, ts,
                 collection_id),
            )
            cap_row = con.execute(
                f"SELECT {_CAP_COLS} FROM captures WHERE event_id=?", (event_id,)
            ).fetchone()
            col_row = con.execute(
                f"SELECT {_COL_COLS} FROM collections WHERE collection_id=?",
                (collection_id,),
            ).fetchone()
            due_after = self._booked_paise(con, book_id) - self._credited_paise(con, book_id)
        # connection closed; audit AFTER the commit, as everywhere else here
        cap = _row_to_capture(cap_row, outstanding_paise=due_after)
        new_col = _row_to_collection(col_row)
        self.audit_append(
            MODULE,
            event="capture.credited" if cap.credited else "capture.parked",
            event_id=cap.event_id,
            collection_id=cap.collection_id,
            book_id=cap.book_id,
            amount_paise=cap.amount_paise,
            payment_id=cap.payment_id,
            link_amount_paid=cap.link_amount_paid,
            razorpay_event=cap.event,
            reason=cap.reason,
            final=bool(final),
            captured_paise=new_col.captured_paise,
            outstanding_before_paise=due_before,
            outstanding_paise=due_after,
            needs_human=not cap.credited,
        )
        if new_col.state != col_from or new_col.needs_human != col.needs_human:
            self._audit_collection(
                "collection.paid" if new_col.state == COL_PAID else "collection.updated",
                new_col, from_state=col_from, event_id=cap.event_id)
        return cap

    def book_view(self, book_id: str) -> dict[str, Any]:
        """Everything these tables know about one book. All integers."""
        booked = self.booked_intents(book_id)
        cols = self.collections_for(book_id)
        caps = self.captures_for_book(book_id)
        booked_paise = sum(int(b.amount_paise) for b in booked)
        credited = sum(int(c.amount_paise) for c in caps if c.credited)
        parked = sum(int(c.amount_paise) for c in caps if not c.credited)
        live = next((c for c in cols if c.is_live), None)
        return {
            "book_id": book_id,
            "booked": booked,
            "collections": cols,
            "captures": caps,
            "booked_paise": booked_paise,
            "captured_paise": credited,
            "parked_paise": parked,
            "outstanding_paise": booked_paise - credited,
            "live_collection": live,
        }

    def parked_captures(self) -> list[Capture]:
        """Every capture a person still has to look at."""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_CAP_COLS} FROM captures WHERE state=? "
                "ORDER BY created_ts, event_id", (CAP_PARKED,),
            ).fetchall()
        return [_row_to_capture(r) for r in rows]

    # ------------------------------------------------------------ refunds
    #
    # WAAPSI. See the constants block above for why this is a separate
    # machine. Nothing in this section touches the intents table: a refund
    # READS the settled intent it hangs off and never writes it.

    def _audit_refund(self, event: str, rf: Refund, *, from_state: str | None,
                      **extra: Any) -> str:
        return self.audit_append(
            MODULE,
            event=event,
            refund_key=rf.refund_key,
            nonce=rf.nonce,
            session_id=rf.session_id,
            cycle=rf.cycle,
            payment_id=rf.payment_id,
            item_id=rf.item_id,
            sku_id=rf.sku_id,
            amount_paise=rf.amount_paise,
            attempt=rf.attempt,
            from_state=from_state,
            to_state=rf.state,
            gateway_refund_id=rf.gateway_refund_id,
            reason=rf.reason,
            needs_human=rf.needs_human,
            **extra,
        )

    def settled_intent_for(self, session_id: str) -> Intent | None:
        """The SETTLED intent with a payment id for a session, or None.

        The only intent a refund may hang off. A NEW/CALLING/BOOKED/FAILED
        row is not money that arrived, and a SETTLED row without a payment id
        is a settlement this counter cannot name to the gateway.
        """
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_COLS} FROM intents WHERE session_id=? AND state=? "
                "ORDER BY cycle DESC, created_ts DESC", (session_id, SETTLED),
            ).fetchall()
        for r in rows:
            it = _row_to_intent(r)
            if it.payment_id:
                return it
        return None

    def get_refund(self, refund_key: str) -> Refund:
        with self._conn() as con:
            row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?", (refund_key,)
            ).fetchone()
        if row is None:
            raise UnknownRefund(f"no refund {refund_key!r}")
        return _row_to_refund(row)

    def refund_by_gateway_id(self, gateway_refund_id: str) -> Refund | None:
        if not isinstance(gateway_refund_id, str) or not gateway_refund_id:
            return None
        with self._conn() as con:
            row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE gateway_refund_id=? "
                "ORDER BY created_ts LIMIT 1", (gateway_refund_id,),
            ).fetchone()
        return None if row is None else _row_to_refund(row)

    def refunds_for_session(self, session_id: str) -> list[Refund]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE session_id=? "
                "ORDER BY created_ts, refund_key", (session_id,),
            ).fetchall()
        return [_row_to_refund(r) for r in rows]

    def refunds_for_nonce(self, nonce: str) -> list[Refund]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE nonce=? "
                "ORDER BY created_ts, refund_key", (nonce,),
            ).fetchall()
        return [_row_to_refund(r) for r in rows]

    def all_refunds(self) -> list[Refund]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_RF_COLS} FROM refunds ORDER BY created_ts, refund_key"
            ).fetchall()
        return [_row_to_refund(r) for r in rows]

    def parked_refunds(self) -> list[Refund]:
        """Every refund a person still has to look at."""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE needs_human=1 "
                "ORDER BY created_ts, refund_key"
            ).fetchall()
        return [_row_to_refund(r) for r in rows]

    def refund_events_for(self, refund_key: str) -> list[RefundEvent]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {_RFE_COLS} FROM refund_events WHERE refund_key=? "
                "ORDER BY created_ts, event_id", (refund_key,),
            ).fetchall()
        return [_row_to_refund_event(r) for r in rows]

    @staticmethod
    def _refund_sum(con: sqlite3.Connection, nonce: str,
                    states: frozenset[str]) -> int:
        marks = ",".join("?" for _ in states)
        row = con.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) FROM refunds "
            f"WHERE nonce=? AND state IN ({marks})", (nonce, *sorted(states)),
        ).fetchone()
        return int(row[0])

    def refunded_paise(self, nonce: str) -> int:
        """Paise a signed refund.processed has sent back on this intent."""
        with self._conn() as con:
            return self._refund_sum(con, nonce, frozenset({RF_PROCESSED}))

    def committed_refund_paise(self, nonce: str) -> int:
        """Paise asked for or sent back — everything but FAILED."""
        with self._conn() as con:
            return self._refund_sum(con, nonce, RF_COMMITTED)

    def create_refund(self, nonce: str, *, item_id: str, sku_id: str,
                      amount_paise: int) -> Refund:
        """Write-ahead for one refund. EXACTLY ONCE per (payment, line, attempt).

        The read of the settled intent, the sum of what is already committed
        against it, and the insert happen in ONE BEGIN IMMEDIATE transaction,
        so two REFUND presses racing each other produce one row, and two
        lines racing each other cannot together pass the bill's own amount.

        Refused BY NAME rather than guessed at:
          * `bill_not_settled`     — no SETTLED intent with a payment id. A
                                     refund of money that never arrived is
                                     not a refund, it is a gift.
          * `refund_exceeds_bill`  — this line, plus every refund already
                                     committed on this payment, would exceed
                                     what settled. Note this counts REQUESTED
                                     and INDETERMINATE refunds, not just
                                     processed ones: money that has been
                                     asked for is spoken for.
        A repeat of the same (payment, line) finds its own row and returns it
        with `replayed=True`; paisa names that `already_refunded`. Only a
        FAILED refund frees the line for another attempt.
        """
        if not isinstance(item_id, str) or not item_id.strip():
            raise KernelError("item_id must be a non-empty string: it names the line")
        if not isinstance(sku_id, str) or not sku_id.strip():
            raise KernelError("sku_id must be a non-empty string")
        amt = int(paise(amount_paise))
        if amt <= 0:
            raise MoneyError(f"a refund must be a positive amount, got {amt} paise")
        ts = self._now()
        key = new_refund_key()
        with self._tx() as con:
            row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (nonce,)
            ).fetchone()
            if row is None:
                raise UnknownIntent(f"no intent for nonce {nonce!r}")
            it = _row_to_intent(row)
            if it.state != SETTLED or not it.payment_id:
                raise RefundRefused(
                    "bill_not_settled",
                    f"bill {it.session_id} is {it.state}"
                    + ("" if it.payment_id else " with no payment id")
                    + ": no signed webhook ever settled it, so there is no "
                    "money to send back.",
                    session_id=it.session_id, nonce=it.nonce, state=it.state)
            if amt > it.amount_paise:
                raise RefundRefused(
                    "refund_exceeds_bill",
                    f"a refund of {amt} paise on a bill that settled for "
                    f"{it.amount_paise} paise.",
                    session_id=it.session_id, nonce=it.nonce,
                    bill_amount_paise=it.amount_paise)
            failed = int(con.execute(
                "SELECT COUNT(*) FROM refunds WHERE payment_id=? AND item_id=? "
                "AND state=?", (it.payment_id, item_id, RF_FAILED),
            ).fetchone()[0])
            idem = refund_idem_key(it.payment_id, item_id, failed)
            existing = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE idem_key=?", (idem,)
            ).fetchone()
            if existing is not None:
                return _row_to_refund(existing, replayed=True)
            committed = self._refund_sum(con, nonce, RF_COMMITTED)
            if committed + amt > it.amount_paise:
                raise RefundRefused(
                    "refund_exceeds_bill",
                    f"{committed} paise are already asked for or sent back on "
                    f"this bill; another {amt} would pass the {it.amount_paise} "
                    "that settled. Nothing is asked for.",
                    session_id=it.session_id, nonce=it.nonce,
                    bill_amount_paise=it.amount_paise,
                    committed_paise=committed)
            con.execute(
                "INSERT INTO refunds (refund_key, idem_key, nonce, session_id, "
                "cycle, payment_id, item_id, sku_id, amount_paise, attempt, "
                "state, gateway_refund_id, receipt, needs_human, reason, "
                "created_ts, updated_ts, requested_ts, processed_ts, "
                "processed_event_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,0,NULL,?,?,NULL,NULL,NULL)",
                (key, idem, it.nonce, it.session_id, it.cycle, it.payment_id,
                 item_id.strip(), sku_id.strip(), amt, failed, RF_NEW, ts, ts),
            )
            row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?", (key,)
            ).fetchone()
        rf = _row_to_refund(row)
        self._audit_refund("refund.created", rf, from_state=None,
                           bill_amount_paise=it.amount_paise)
        return rf

    def _refund_transition(
        self, refund_key: str, to_state: str, *, event: str,
        reason: str | None = None, gateway_refund_id: str | None = None,
        receipt: str | None = None, needs_human: bool | None = None,
        stamp_requested: bool = False, audit_extra: Mapping[str, Any] | None = None,
    ) -> Refund:
        with self._tx() as con:
            row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?", (refund_key,)
            ).fetchone()
            if row is None:
                raise UnknownRefund(f"no refund {refund_key!r}")
            cur = _row_to_refund(row)
            if to_state not in RF_LEGAL[cur.state]:
                raise IllegalTransition(
                    f"refund {refund_key}: {cur.state} -> {to_state} is not a "
                    "legal move.")
            if (gateway_refund_id is not None and cur.gateway_refund_id
                    not in (None, gateway_refund_id)):
                raise IllegalTransition(
                    f"refund {refund_key} already carries gateway refund "
                    f"{cur.gateway_refund_id!r}; refusing to overwrite with "
                    f"{gateway_refund_id!r} -- that would mean two refunds for "
                    "one line.")
            flag = cur.needs_human if needs_human is None else needs_human
            ts = self._now()
            con.execute(
                "UPDATE refunds SET state=?, reason=?, needs_human=?, "
                "gateway_refund_id=COALESCE(?, gateway_refund_id), "
                "receipt=COALESCE(?, receipt), "
                "requested_ts=CASE WHEN ? THEN COALESCE(requested_ts, ?) "
                "ELSE requested_ts END, updated_ts=? WHERE refund_key=?",
                (to_state, reason, 1 if flag else 0, gateway_refund_id, receipt,
                 1 if stamp_requested else 0, ts, ts, refund_key),
            )
            row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?", (refund_key,)
            ).fetchone()
        rf = _row_to_refund(row)
        self._audit_refund(event, rf, from_state=cur.state,
                           **(dict(audit_extra) if audit_extra else {}))
        return rf

    def mark_refund_calling(self, refund_key: str) -> Refund:
        """NEW -> CALLING. Commit, close, THEN ask the gateway to refund."""
        return self._refund_transition(refund_key, RF_CALLING,
                                       event="refund.calling")

    def mark_refund_requested(self, refund_key: str, *, gateway_refund_id: str,
                              receipt: str | None = None) -> Refund:
        """CALLING/INDETERMINATE -> REQUESTED: the gateway took the request.

        Money has not been shown to move. If the signed refund.processed
        already beat this call (the row is PROCESSED), the gateway id is
        recorded if it was missing and the row is returned as it is: a
        processed refund does not go back to "requested" because the HTTP
        answer arrived late.
        """
        if not isinstance(gateway_refund_id, str) or not gateway_refund_id.strip():
            raise KernelError("gateway_refund_id must be a non-empty string")
        cur = self.get_refund(refund_key)
        if cur.state == RF_PROCESSED:
            if cur.gateway_refund_id not in (None, gateway_refund_id):
                raise IllegalTransition(
                    f"refund {refund_key} was processed as "
                    f"{cur.gateway_refund_id!r}; the gateway now answers "
                    f"{gateway_refund_id!r}. Two ids for one refund.")
            if cur.gateway_refund_id is None:
                with self._tx() as con:
                    con.execute(
                        "UPDATE refunds SET gateway_refund_id=?, "
                        "receipt=COALESCE(?, receipt), updated_ts=? "
                        "WHERE refund_key=?",
                        (gateway_refund_id, receipt, self._now(), refund_key))
                cur = self.get_refund(refund_key)
                self._audit_refund("refund.requested_after_processed", cur,
                                   from_state=RF_PROCESSED)
            return cur
        return self._refund_transition(
            refund_key, RF_REQUESTED, event="refund.requested",
            gateway_refund_id=gateway_refund_id, receipt=receipt,
            reason=None, needs_human=False, stamp_requested=True)

    def mark_refund_indeterminate(self, refund_key: str,
                                  reason: str = "timeout", *,
                                  gateway_refund_id: str | None = None) -> Refund:
        """The refund call may or may not have reached the gateway. Parked
        for a person; NEVER retried blind, because the retry could refund
        twice. A late signed webhook for it moves it on by itself.

        `gateway_refund_id` is for the case where the gateway DID answer but
        answered wrongly (a different amount): the id is kept so the signed
        callback can still find this row, and the row is still parked.
        """
        return self._refund_transition(
            refund_key, RF_INDETERMINATE, event="refund.indeterminate",
            reason=str(reason), needs_human=True,
            gateway_refund_id=gateway_refund_id)

    def mark_refund_failed(self, refund_key: str, reason: str = "declined") -> Refund:
        """Only for a DEFINITE negative: the gateway's own answer, or its
        signed refund.failed. Never for a timeout."""
        return self._refund_transition(
            refund_key, RF_FAILED, event="refund.failed", reason=str(reason))

    def record_refund_event(
        self, *, event_id: str, event: str, refund_key: str,
        amount_paise: int | None, gateway_refund_id: str | None,
        status: str | None = None,
    ) -> tuple[RefundEvent, Refund]:
        """One signed refund webhook against one refund. EXACTLY ONCE.

        `event_id` is the id inside the SIGNED envelope (or the sha256 of the
        signed bytes), never a header. INSERT OR IGNORE under a UNIQUE index
        inside BEGIN IMMEDIATE: a redelivery finds its own row, changes
        nothing and writes no audit line.

        WHAT MOVES THE REFUND is a `refund.processed` whose signed amount
        EQUALS the paise this counter asked for, on a refund that is
        CALLING, REQUESTED or INDETERMINATE. Anything else is recorded and
        either ACKNOWLEDGED (created, a repeat) or PARKED (the amount
        disagrees, the gateway id disagrees, the refund is in no state to be
        processed) — parked means needs_human is raised on the refund and
        nothing is netted, rounded or corrected.

        A signed `refund.failed` moves a live refund to FAILED, which frees
        the line for another attempt.
        """
        if not isinstance(event_id, str) or not event_id:
            raise KernelError("event_id must be a non-empty string: it is the exactly-once key")
        amt = None if amount_paise is None else int(paise(amount_paise))
        ts = self._now()
        with self._tx() as con:
            existing = con.execute(
                f"SELECT {_RFE_COLS} FROM refund_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing is not None:
                ev = _row_to_refund_event(existing, replayed=True)
                row = con.execute(
                    f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?",
                    (ev.refund_key,),
                ).fetchone()
                if row is None:
                    raise UnknownRefund(f"no refund {ev.refund_key!r}")
                return ev, _row_to_refund(row)
            row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?", (refund_key,)
            ).fetchone()
            if row is None:
                raise UnknownRefund(f"no refund {refund_key!r}")
            cur = _row_to_refund(row)

            ev_state = RFE_ACKNOWLEDGED
            reason: str | None = None
            to_state = cur.state
            human = cur.needs_human
            rf_reason = cur.reason
            new_gw = cur.gateway_refund_id
            processed_ts = cur.processed_ts
            processed_ev = cur.processed_event_id

            if event == "refund.processed":
                if amt is None:
                    ev_state, reason = RFE_PARKED, "amount_missing"
                elif amt != cur.amount_paise:
                    ev_state = RFE_PARKED
                    reason = f"amount_disagrees:signed={amt}:requested={cur.amount_paise}"
                elif (cur.gateway_refund_id and gateway_refund_id
                      and cur.gateway_refund_id != gateway_refund_id):
                    ev_state = RFE_PARKED
                    reason = (f"refund_id_disagrees:signed={gateway_refund_id}"
                              f":requested={cur.gateway_refund_id}")
                elif cur.state == RF_PROCESSED:
                    ev_state, reason = RFE_ACKNOWLEDGED, "already_processed"
                elif cur.state not in RF_PROCESSABLE:
                    ev_state, reason = RFE_PARKED, f"refund_not_processable:{cur.state}"
                else:
                    ev_state = RFE_APPLIED
                    to_state = RF_PROCESSED
                    rf_reason = f"gateway:{event}"
                    processed_ts = ts
                    processed_ev = event_id
                    if new_gw is None and gateway_refund_id:
                        new_gw = gateway_refund_id
            elif event == "refund.failed":
                if cur.state in (RF_CALLING, RF_REQUESTED, RF_INDETERMINATE):
                    ev_state = RFE_APPLIED
                    to_state = RF_FAILED
                    rf_reason = f"gateway:{event}"
                    if new_gw is None and gateway_refund_id:
                        new_gw = gateway_refund_id
                else:
                    ev_state, reason = RFE_ACKNOWLEDGED, f"refund_is_{cur.state}"
            else:
                ev_state, reason = RFE_ACKNOWLEDGED, str(event or "unnamed_event")
                if new_gw is None and gateway_refund_id:
                    new_gw = gateway_refund_id

            if ev_state == RFE_PARKED:
                human = True
                rf_reason = f"event_parked:{reason}"

            con.execute(
                "INSERT OR IGNORE INTO refund_events (event_id, refund_key, event, "
                "state, amount_paise, gateway_refund_id, reason, created_ts) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (event_id, refund_key, event, ev_state, amt, gateway_refund_id,
                 reason, ts),
            )
            con.execute(
                "UPDATE refunds SET state=?, reason=?, needs_human=?, "
                "gateway_refund_id=?, processed_ts=?, processed_event_id=?, "
                "updated_ts=? WHERE refund_key=?",
                (to_state, rf_reason, 1 if human else 0, new_gw, processed_ts,
                 processed_ev, ts, refund_key),
            )
            ev_row = con.execute(
                f"SELECT {_RFE_COLS} FROM refund_events WHERE event_id=?", (event_id,)
            ).fetchone()
            rf_row = con.execute(
                f"SELECT {_RF_COLS} FROM refunds WHERE refund_key=?", (refund_key,)
            ).fetchone()
            it_row = con.execute(
                f"SELECT {_COLS} FROM intents WHERE nonce=?", (cur.nonce,)
            ).fetchone()
            bill_amount = int(it_row["amount_paise"]) if it_row is not None else None
            refunded_after = self._refund_sum(con, cur.nonce, frozenset({RF_PROCESSED}))
        # connection closed; audit AFTER the commit, as everywhere else here
        ev = _row_to_refund_event(ev_row)
        rf = _row_to_refund(rf_row)
        audit_event = {
            RFE_APPLIED: "refund.processed" if rf.state == RF_PROCESSED else "refund.failed",
            RFE_PARKED: "refund.parked",
            RFE_ACKNOWLEDGED: "refund.acknowledged",
        }[ev.state]
        self._audit_refund(
            audit_event, rf, from_state=cur.state,
            event_id=ev.event_id, razorpay_event=event, event_state=ev.state,
            event_reason=ev.reason, signed_amount_paise=ev.amount_paise,
            signed_status=status, bill_amount_paise=bill_amount,
            refunded_total_paise=refunded_after,
        )
        return ev, rf


__all__ = [
    "Kernel", "Intent", "Collection", "Capture", "GatewayResult",
    "KernelError", "IllegalTransition", "UnknownIntent", "UnknownCollection",
    "CollectionOpen", "idem_key", "new_nonce", "new_collection_id",
    "NEW", "CALLING", "SETTLED", "INDETERMINATE", "RETRIEVE", "FAILED",
    "ESCALATED", "BOOKED", "DEFAULT_MAX_RETRIEVE_ATTEMPTS",
    "ALL_STATES", "TERMINAL", "MACHINE_TERMINAL", "LEGAL",
    "COL_NEW", "COL_CALLING", "COL_OPEN", "COL_PAID", "COL_EXPIRED",
    "COL_CANCELLED", "COL_FAILED", "COL_INDETERMINATE", "COL_LIVE",
    "COL_CREDITABLE", "COL_LEGAL", "CAP_CREDITED", "CAP_PARKED",
    "Refund", "RefundEvent", "UnknownRefund", "RefundRefused",
    "new_refund_key", "refund_idem_key",
    "RF_NEW", "RF_CALLING", "RF_REQUESTED", "RF_PROCESSED", "RF_FAILED",
    "RF_INDETERMINATE", "RF_STATES", "RF_COMMITTED", "RF_PROCESSABLE", "RF_LEGAL",
    "RFE_APPLIED", "RFE_PARKED", "RFE_ACKNOWLEDGED",
]
