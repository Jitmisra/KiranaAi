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

ALL_STATES = frozenset(
    {NEW, CALLING, SETTLED, INDETERMINATE, RETRIEVE, FAILED, ESCALATED}
)
#: States in which the MONEY question is answered. ESCALATED is deliberately
#: NOT here: it is terminal for the sweeper but the money is still unknown, and
#: calling it terminal would let a caller mistake a stuck row for a decision.
TERMINAL = frozenset({SETTLED, FAILED})
#: States no automatic sweep may drive forward.
MACHINE_TERMINAL = frozenset({SETTLED, FAILED, ESCALATED})

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
LEGAL: dict[str, frozenset[str]] = {
    NEW: frozenset({CALLING}),
    CALLING: frozenset({SETTLED, INDETERMINATE, FAILED}),
    INDETERMINATE: frozenset({RETRIEVE, SETTLED, ESCALATED}),
    RETRIEVE: frozenset({SETTLED, FAILED, INDETERMINATE, ESCALATED}),
    SETTLED: frozenset(),
    FAILED: frozenset({SETTLED}),
    ESCALATED: frozenset({SETTLED, FAILED}),
}

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
    updated_ts        TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intents_nonce    ON intents(nonce);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intents_idem_key ON intents(idem_key);
CREATE INDEX        IF NOT EXISTS ix_intents_state    ON intents(state);
"""

_COLS = ("nonce, idem_key, session_id, cycle, amount_paise, state, payment_id, "
         "attempts, retrieve_attempts, needs_human, reason, created_ts, updated_ts")


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
    )


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
            con.execute("PRAGMA journal_mode=WAL")
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


__all__ = [
    "Kernel", "Intent", "GatewayResult", "KernelError", "IllegalTransition",
    "UnknownIntent", "idem_key", "new_nonce",
    "NEW", "CALLING", "SETTLED", "INDETERMINATE", "RETRIEVE", "FAILED",
    "ESCALATED", "DEFAULT_MAX_RETRIEVE_ATTEMPTS",
    "ALL_STATES", "TERMINAL", "MACHINE_TERMINAL", "LEGAL",
]
