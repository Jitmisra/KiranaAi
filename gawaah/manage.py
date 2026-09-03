"""PRABANDH — the three screens the shopkeeper needs once the counter works.

    GET  /manage/history               what was billed, newest first
    GET  /manage/history/{session_id}  one bill in full, exclusions included
    GET  /manage/inventory             what is taught, what has sold
    POST /manage/inventory/{sku}/stock the one number a shopkeeper may enter
    GET  /manage/settings              what this counter is configured to do

MOUNTING
========
This is an ``APIRouter``, not an app. The ``/manage`` prefix is already ON the
router, so::

    from gawaah import manage
    app.include_router(manage.router)          # -> /manage/history

Do NOT pass ``prefix="/manage"`` again; that yields ``/manage/manage/history``.

WHERE EVERY NUMBER COMES FROM
=============================
Nothing here has a store of its own. There is no bills table, no sales counter,
no stock ledger — a second store would be a second truth, and the first time it
disagreed with the audit chain there would be no way to tell which one was
lying. Every figure on these three screens is recomputed from two places:

    results/audit.jsonl     the hash-chained log. Bills, line items, amber
                            exclusions, mints and webhooks all live here.
    results/shop/*.json     the catalogue: names, integer paise, footprints,
                            code bindings.

and one thing the shopkeeper types, which is kept apart from both and labelled
as his word rather than the counter's (see OPENING STOCK below).

THE CHAIN IS THE BILL BOOK, AND IT IS VERIFIED BEFORE IT IS READ
================================================================
``ledger.verify()`` re-walks the chain from genesis on every read. If it breaks
at line N, this module serves the bills derived from lines 1..N-1 and says so,
loudly, in a ``chain`` block on every response.

That is a judgement call and here is what it costs when it is wrong: serving the
verified prefix means a shopkeeper whose chain was truncated this morning can
still see last week's bills, which is what he actually needs. The risk is that
he reads the amber banner as decoration and trusts a book that somebody has
edited. The mitigation is that the banner names the line and the error, and the
figure is never quietly adjusted — a bill after the break is ABSENT, not
approximated. Refusing the whole request instead would be safer and useless: he
would see nothing, learn nothing, and go back to the paper book.

A REFUSAL IS A RESULT
=====================
Every path returns ``{"ok": false, "reason": ..., "detail": ...}`` with a 400
(404 for an id that does not exist). Nothing here raises a 500. A management
screen that crashes on the one file the shopkeeper needs to inspect is worse
than no screen: he cannot even find out what is wrong.

THIS FILE NEVER SETTLES MONEY
=============================
It holds no gateway, mints nothing, and reads the money service over the same
read-only paths the till uses. ``settles_money`` is False on every response and
that is a fact about the code, not a promise.

WHY IT DOES NOT IMPORT tools.upload_app
=======================================
The orchestrator mounts this router INTO ``tools/upload_app.py``. Importing that
module from here at import time would be a cycle, so the two small things this
file needs from it — a read-only GET to the money service, and the shape of the
catalogue sidecars — are reimplemented here rather than shared. The duplication
is about forty lines and it is the cheaper of the two mistakes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .identity import (
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    MODE_APPEARANCE_ONLY,
    PHI_APPEARANCE_ONLY,
)
from .ledger import GENESIS, verify
from .money import to_rupees_str
from .takhti import (
    BUF_H,
    BUF_W,
    MARGIN_MM,
    MARKER_IDS,
    MARKER_MM,
    MAT_H_MM,
    MAT_W_MM,
    MAX_PERSP_INDEX,
    MAX_SCALE_ERR,
    marker_centres_mm,
)

router = APIRouter(prefix="/manage", tags=["manage"])

# ------------------------------------------------------------------ refusals
#
# Lowercase snake_case naming the STATE, matching tools/upload_app.py. The
# sentence that says what to change goes in `detail`, never in the reason.

R_INTERNAL = "manage_internal_error"
R_BAD_LIMIT = "limit_not_a_positive_integer"
R_BAD_SINCE = "since_not_a_timestamp"
R_UNKNOWN_SESSION = "session_not_in_the_ledger"
R_UNKNOWN_SKU = "sku_not_in_the_catalogue"
R_BAD_BODY = "body_not_json_object"
R_STOCK_MISSING = "stock_units_missing"
R_STOCK_NOT_INTEGER = "stock_units_not_an_integer"
R_STOCK_NEGATIVE = "stock_units_negative"
R_STOCK_TOO_LARGE = "stock_units_implausible"
R_STOCK_NOT_WRITTEN = "opening_stock_not_written"

# ------------------------------------------------------------------- limits

#: Default page size for /manage/history. A kirana counter does perhaps 200
#: bills a day, so 50 is roughly the last two hours — enough to answer "did
#: that last one go through?" without shipping the year.
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000

#: A shopkeeper counting a shelf types tens, not millions. The cap exists so a
#: fat-fingered paste cannot put a number on the page that no human entered
#: meaning to. It is refused by name, not clamped: clamping would store a
#: figure the shopkeeper never typed and show it back to him as his own.
MAX_OPENING_STOCK_UNITS = 1_000_000

#: How long the inbound webhook path may be silent before the settings page
#: calls it silent rather than live.
#:
#: WHAT IT COSTS WHEN IT IS WRONG, both ways. Too low and a genuinely quiet
#: Tuesday afternoon paints the panel amber; the shopkeeper learns to ignore
#: the panel, which is strictly worse than not having it. Too high and a
#: revoked tunnel goes unnoticed through a whole trading day — that is the
#: failure that cost a real payment 78 seconds of silence, except for hours.
#: Fifteen minutes is chosen because a counter that has taken nothing in
#: fifteen minutes during trading hours is itself worth a look.
#:
#: The one case that is NOT a threshold judgement is `never`: if no webhook has
#: EVER arrived, the path has never worked and no payment can ever turn a bill
#: green. That is reported red and it is not a guess.
WEBHOOK_SILENT_AFTER_S = 900

#: The money service, read-only. Same env var the till uses.
PAISA_BASE = os.environ.get("GAWAAH_PAISA_URL", "http://127.0.0.1:8788")
#: Shorter than the till's 6 s. This is a page load, not a mint: a settings
#: screen that hangs for six seconds because the money process is down teaches
#: the shopkeeper that the whole site is broken.
PAISA_TIMEOUT_S = 4

#: How a SKU came to be in the catalogue, in the shopkeeper's words. The keys
#: are the strings already written on disk, so a UI never has to infer them.
TAUGHT_ON_MAT = "mat_measured"
TAUGHT_BY_CODE = "product_code_only"
TAUGHT_LABELS = {
    TAUGHT_ON_MAT: "on the printed mat",
    MODE_APPEARANCE_ONLY: "from a photograph",
    TAUGHT_BY_CODE: "by its printed code",
}

STOCK_SIDECAR = "opening_stock.json"
STOCK_FORMAT = 1

# The ledger events each figure is derived from, named once so a reader does
# not have to grep the file to learn which line in the log means "sold".
EV_DONE = "done"                                  # basket closed, bill exists
EV_EXIT = "exit"                                  # one item left the counter
EV_MINTED = "intent.minted"                       # a payment link was issued
EV_SETTLED = "intent.settled"                     # the kernel saw settlement
EV_WEBHOOK = "webhook"                            # session's view of a webhook
EV_REFUSED_PAISA = "intent.refused"               # money refused to mint
EV_REFUSED_SESSION = "refused"                    # the counter refused
REASON_COMMITTED = "exit_crossing_committed"
REASON_AMBER = "exit_crossing_committed_amber_excluded"
REASON_GREEN = "settled_green"


class ManageRefused(Exception):
    """A named refusal with a sentence the shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: ManageRefused) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "reason": exc.reason,
            "detail": exc.detail,
            "settles_money": False,
        },
        status_code=exc.status,
    )


def _crash(exc: Exception) -> JSONResponse:
    """Never a 500. The exception TYPE is named; the message is passed through
    because on a management screen the message is usually the whole diagnosis
    ('No such file or directory: results/audit.jsonl')."""
    return JSONResponse(
        {
            "ok": False,
            "reason": R_INTERNAL,
            "detail": f"{type(exc).__name__}: {exc}",
            "settles_money": False,
        },
        status_code=400,
    )


# --------------------------------------------------------------- where things are
#
# Resolved per call, never memoised at import. A test that sets GAWAAH_SHOP_DIR
# in a fixture must be able to change it between tests, and a module-level
# constant captured at import time silently ignores that — which is how a test
# harness once wrote over the live catalogue in results/.

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Where the hash-chained audit log lives. GAWAAH_DATA_DIR, as paisa and
    live_app read it, so all three processes agree on one chain."""
    return Path(os.environ.get("GAWAAH_DATA_DIR", str(_repo_root() / "results")))


def store_dir() -> Path:
    """The shopkeeper's catalogue. GAWAAH_SHOP_DIR, as the till reads it."""
    override = os.environ.get("GAWAAH_SHOP_DIR")
    if override:
        return Path(override)
    return _repo_root() / "results" / "shop"


def ledger_path() -> Path:
    return data_dir() / "audit.jsonl"


def stock_path() -> Path:
    return store_dir() / STOCK_SIDECAR


# ------------------------------------------------------------------ the chain

#: (path, mtime_ns, size) -> (records, chain_block). The chain is append-only,
#: so mtime+size is a sound cache key: no edit that preserves both can have
#: happened without also breaking verify(), which is recomputed with the entry.
#: Without this, a settings page polling every few seconds would re-verify the
#: whole log inside the SAME process that serves the camera, and the counter
#: would stutter in step with the management screen nobody is looking at.
_CHAIN_CACHE: dict[tuple[str, int, int], tuple[tuple[dict, ...], dict]] = {}


def read_chain() -> tuple[tuple[dict, ...], dict]:
    """Every verified record, plus the state of the chain that carried them.

    Returns ``(records, chain)``. ``records`` stops at the first broken link:
    lines after a break are not returned at all, because a line whose hash does
    not recompute is not evidence of anything.

    An ABSENT log is not an error. A counter installed this morning has no
    bills, and that is a different thing from a counter whose log was deleted —
    which is why ``exists`` is reported separately from ``ok``.
    """
    path = ledger_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (), {
            "ok": True,
            "exists": False,
            "lines_verified": 0,
            "lines_readable": 0,
            "head": GENESIS,
            "error": None,
            "path": str(path),
        }
    cached = _CHAIN_CACHE.get(key)
    if cached is not None:
        return cached

    ok, verified, head, error = verify(path)

    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            break
        if not isinstance(rec, dict):
            break
        records.append(rec)
    # Truncate to what verify() actually stood behind. Parsing and verifying are
    # two different bars and only the second one counts.
    if not ok:
        records = records[:verified]

    chain = {
        "ok": bool(ok),
        "exists": True,
        "lines_verified": int(verified),
        "lines_readable": len(records),
        "head": head,
        "error": error,
        "path": str(path),
    }
    result = (tuple(records), chain)
    # One entry is enough: the key changes on every append, and an unbounded
    # dict keyed by mtime is a slow leak in a process that runs for days.
    _CHAIN_CACHE.clear()
    _CHAIN_CACHE[key] = result
    return result


def sku_of(item_id: str) -> str:
    """The SKU behind a line id.

    paisa writes one line per PLACED PACKET as ``f"{sku}#{i}"`` (paisa.py:1297)
    so that two Parle-G on the same counter are two lines and not one. A sku id
    can never contain '#' — shop_store.SKU_RE forbids it — so splitting on the
    first one is unambiguous rather than a guess.
    """
    return item_id.split("#", 1)[0] if "#" in item_id else item_id


def _parse_ts(value: Any) -> Optional[datetime]:
    """An ISO-8601 stamp as the ledger writes them, or None.

    The one repair: a '+' in a URL query string decodes to a SPACE, so a
    shopkeeper who copies '2026-08-29T05:28:00.078+00:00' out of the ledger and
    pastes it into ?since= sends '...078 00:00' and gets a refusal for a
    timestamp he read off this very product. A space sitting exactly where the
    UTC offset belongs has no other meaning, so it is repaired rather than
    refused. Anything else still refuses: guessing at a half-typed date would
    silently show him the wrong day's takings.
    """
    if not isinstance(value, str) or not value:
        return None
    for candidate in (value, _plus_restored(value)):
        if candidate is None:
            continue
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _plus_restored(value: str) -> Optional[str]:
    """'…T05:28:00 00:00' -> '…T05:28:00+00:00', or None if that is not it."""
    head, sep, tail = value.rpartition(" ")
    if not sep or not head:
        return None
    digits = tail.replace(":", "")
    if len(digits) in (2, 4) and digits.isdigit():
        return f"{head}+{tail}"
    return None


# ------------------------------------------------------------------- the bills

def _blank_bill(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "at": None,
        "opened_at": None,
        "total_paise": 0,
        "total_rupees": "0.00",
        "line_items": [],
        "excluded": [],
        "counted_lines": 0,
        "excluded_lines": 0,
        "closed": False,
        "minted": False,
        "settled": False,
        "settled_at": None,
        "settled_by": None,
        "state": None,
        "payment_link_id": None,
        "payment_id": None,
        "refusals": [],
        "webhooks": [],
        "events": [],
    }


def bills_from(records: Iterable[dict]) -> dict[str, dict[str, Any]]:
    """Fold the chain into one entry per session, in the order they appear.

    Every figure below names the event it came from. Nothing is summed twice and
    nothing is inferred from a figure that is itself inferred.
    """
    bills: dict[str, dict[str, Any]] = {}
    for rec in records:
        session_id = rec.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            continue
        bill = bills.get(session_id)
        if bill is None:
            bill = _blank_bill(session_id)
            bills[session_id] = bill

        module = rec.get("module")
        event = rec.get("event")
        reason = rec.get("reason")
        ts = rec.get("ts")

        if bill["opened_at"] is None:
            bill["opened_at"] = ts

        # The counter's own state machine is the authority on what state a
        # session is in; the last transition it wrote wins.
        if module == "session" and isinstance(rec.get("to"), str):
            bill["state"] = rec["to"]

        bill["events"].append(
            {
                "ts": ts,
                "module": module,
                "event": event,
                "reason": reason,
                "from": rec.get("from") or rec.get("from_state"),
                "to": rec.get("to") or rec.get("to_state"),
                "item_id": rec.get("item_id"),
            }
        )

        # LINE ITEMS: session/exit. An exit is the moment a packet left the
        # counter and was committed to the basket — the placement before it is
        # only a candidate, and pricing off placements double-counts a packet
        # the customer picked back up.
        if module == "session" and event == EV_EXIT:
            item_id = rec.get("item_id")
            if isinstance(item_id, str) and item_id:
                excluded = bool(rec.get("excluded_from_total")) or reason == REASON_AMBER
                line = {
                    "item_id": item_id,
                    "sku_id": sku_of(item_id),
                    "reason": reason,
                    "abstained": bool(rec.get("abstained")),
                    "at": ts,
                }
                if excluded:
                    # No price, and that is the point. An amber item is one the
                    # counter could not identify, so there is no price to show
                    # and inventing one — a zero, a dash, the last price seen —
                    # would turn an honest abstention into a silent guess.
                    line["price_paise"] = None
                    line["price_rupees"] = None
                    line["counted"] = False
                    bill["excluded"].append(line)
                else:
                    price = rec.get("price_paise")
                    line["price_paise"] = int(price) if isinstance(price, int) else None
                    line["price_rupees"] = (
                        to_rupees_str(line["price_paise"])
                        if isinstance(line["price_paise"], int)
                        else None
                    )
                    line["counted"] = True
                    bill["line_items"].append(line)

        # THE BILL ITSELF: session/done, reason 'intent_requested'. That is the
        # moment the basket closed and a total existed to charge. A session with
        # no `done` was never billed and must not appear in a bill book.
        if module == "session" and event == EV_DONE:
            bill["closed"] = True
            bill["at"] = ts
            total = rec.get("total_paise")
            if isinstance(total, int):
                bill["total_paise"] = int(total)
                bill["total_rupees"] = to_rupees_str(int(total))
            bill["counted_lines"] = int(rec.get("lines") or 0)
            amber = rec.get("amber_excluded")
            bill["excluded_lines"] = int(amber) if isinstance(amber, int) else 0

        # THE LINK: paisa/intent.minted.
        if module == "paisa" and event == EV_MINTED:
            bill["minted"] = True
            link = rec.get("payment_link_id")
            if isinstance(link, str):
                bill["payment_link_id"] = link

        # SETTLEMENT. INVARIANT 2: only a signature-verified webhook turns a
        # bill green, so session/webhook with reason 'settled_green' is the
        # authority. kernel/intent.settled is recorded downstream of the same
        # webhook and is accepted only as a fallback, labelled as such, so a
        # reader can tell which line was believed.
        if module == "session" and event == EV_WEBHOOK:
            bill["webhooks"].append(
                {
                    "ts": ts,
                    "reason": reason,
                    "razorpay_event": rec.get("razorpay_event"),
                    "event_id": rec.get("event_id"),
                    "amount_paise": rec.get("webhook_amount_paise"),
                    "to": rec.get("to"),
                }
            )
            if reason == REASON_GREEN or rec.get("to") == "PAID":
                bill["settled"] = True
                bill["settled_at"] = ts
                bill["settled_by"] = "webhook"
        if module == "kernel" and event == EV_SETTLED:
            payment_id = rec.get("payment_id")
            if isinstance(payment_id, str):
                bill["payment_id"] = payment_id
            if not bill["settled"]:
                bill["settled"] = True
                bill["settled_at"] = ts
                bill["settled_by"] = "kernel"

        # REFUSALS, kept with the bill. A bill that was refused is the one the
        # shopkeeper most wants to look at, and a history that drops it leaves
        # him staring at a gap.
        if (module == "paisa" and event == EV_REFUSED_PAISA) or (
            module == "session" and event == EV_REFUSED_SESSION
        ):
            bill["refusals"].append(
                {
                    "ts": ts,
                    "module": module,
                    "reason": reason,
                    "requested_paise": rec.get("requested_paise"),
                    "server_total_paise": rec.get("server_total_paise"),
                    "session_total_paise": rec.get("session_total_paise"),
                }
            )
    return bills


def _summary(bill: dict[str, Any]) -> dict[str, Any]:
    """The row a history LIST shows. The full timeline stays behind the detail
    endpoint — a hundred bills each carrying forty events is a megabyte of JSON
    to render twelve rows."""
    return {
        "session_id": bill["session_id"],
        "at": bill["at"],
        "total_paise": bill["total_paise"],
        "total_rupees": bill["total_rupees"],
        "lines": len(bill["line_items"]),
        "excluded_lines": len(bill["excluded"]),
        "items": [
            {
                "sku_id": line["sku_id"],
                "item_id": line["item_id"],
                "price_paise": line["price_paise"],
                "price_rupees": line["price_rupees"],
            }
            for line in bill["line_items"]
        ],
        "excluded": [
            {"sku_id": line["sku_id"], "item_id": line["item_id"], "reason": line["reason"]}
            for line in bill["excluded"]
        ],
        "settled": bill["settled"],
        "settled_at": bill["settled_at"],
        "settled_by": bill["settled_by"],
        "state": bill["state"],
        "minted": bill["minted"],
        "payment_link_id": bill["payment_link_id"],
        "payment_id": bill["payment_id"],
        "refused": len(bill["refusals"]) > 0,
    }


# --------------------------------------------------------------- the catalogue
#
# Read as JSON, not through ShopStore. A ShopStore load VALIDATES — which is
# exactly right when something is about to be taught, and exactly wrong here: a
# management screen exists so the shopkeeper can look at a catalogue that has
# gone wrong, and one that raises on a hand-edited file shows him nothing at the
# one moment he needs to see it. Nothing in this module writes a SKU, so none of
# the write-side invariants are being skipped.


def _load_json(path: Path) -> tuple[Any, Optional[str]]:
    """Parse a sidecar, or name why not. Never raises."""
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 - a hand-edit is not an outage
        return None, f"{type(exc).__name__}: {exc}"


def catalogue() -> dict[str, Any]:
    """Every product this counter can price, and how it learnt each one.

    Three files, three ways of teaching, one merged view:

        catalog.json           taught on the mat  — has millimetres
        appearance_only.json   taught from a photo (vectors) OR from a printed
                               code alone (no vectors — a name and a price
                               bound to a string of digits)
        product_codes.json     which codes point at which SKU

    catalog.json wins on a clash, exactly as taught_skus() has the store shadow
    the sidecar: a product re-taught on the mat is the stronger record.
    """
    sdir = store_dir()
    problems: list[dict[str, str]] = []
    items: dict[str, dict[str, Any]] = {}

    codes_raw, codes_err = _load_json(sdir / "product_codes.json")
    if codes_err:
        problems.append({"file": "product_codes.json", "detail": codes_err})
    codes_by_sku: dict[str, list[str]] = {}
    if isinstance(codes_raw, dict) and isinstance(codes_raw.get("codes"), dict):
        for code, sku in codes_raw["codes"].items():
            if isinstance(sku, str):
                codes_by_sku.setdefault(sku, []).append(str(code))
    for bound in codes_by_sku.values():
        bound.sort()

    ao_raw, ao_err = _load_json(sdir / "appearance_only.json")
    if ao_err:
        problems.append({"file": "appearance_only.json", "detail": ao_err})
    if isinstance(ao_raw, dict) and isinstance(ao_raw.get("skus"), dict):
        for sku, rec in ao_raw["skus"].items():
            if not isinstance(rec, dict):
                continue
            vectors = rec.get("vectors") or []
            taught = MODE_APPEARANCE_ONLY if len(vectors) else TAUGHT_BY_CODE
            items[str(sku)] = {
                "sku_id": str(sku),
                "name": str(rec.get("name") or sku),
                "price_paise": rec.get("price_paise"),
                "footprint_mm": rec.get("footprint_mm"),
                "taught_by": taught,
                "n_views": len(vectors),
            }

    cat_raw, cat_err = _load_json(sdir / "catalog.json")
    if cat_err:
        problems.append({"file": "catalog.json", "detail": cat_err})
    gates_on_disk = None
    if isinstance(cat_raw, dict):
        if isinstance(cat_raw.get("gates"), dict):
            gates_on_disk = cat_raw["gates"]
        if isinstance(cat_raw.get("skus"), dict):
            for sku, rec in cat_raw["skus"].items():
                if not isinstance(rec, dict):
                    continue
                vectors = rec.get("vectors") or []
                items[str(sku)] = {
                    "sku_id": str(sku),
                    "name": str(rec.get("name") or sku),
                    "price_paise": rec.get("price_paise"),
                    "footprint_mm": rec.get("footprint_mm"),
                    "taught_by": str(rec.get("taught_by") or TAUGHT_ON_MAT),
                    "n_views": len(vectors),
                }

    for sku, row in items.items():
        row["codes"] = codes_by_sku.get(sku, [])
        row["taught_label"] = TAUGHT_LABELS.get(row["taught_by"], row["taught_by"])
        # A price that is not an integer number of paise is not a price. It is
        # reported as absent and named in `problems` rather than rendered: a
        # float here would be invariant 1 broken on disk, and showing it would
        # launder it into a number the shopkeeper believes.
        if not isinstance(row["price_paise"], int) or isinstance(row["price_paise"], bool):
            if row["price_paise"] is not None:
                problems.append(
                    {"file": "catalogue", "detail": f"{sku}: price is not integer paise"}
                )
            row["price_paise"] = None
            row["price_rupees"] = None
        else:
            row["price_rupees"] = to_rupees_str(row["price_paise"])

    # A code bound to a SKU nobody taught still prices nothing. Naming it is the
    # difference between a catalogue that looks complete and one that is.
    orphan_codes = sorted(sku for sku in codes_by_sku if sku not in items)

    return {
        "items": items,
        "gates_on_disk": gates_on_disk,
        "problems": problems,
        "orphan_code_bindings": orphan_codes,
        "dir": str(sdir),
    }


# ------------------------------------------------------------- opening stock
#
# THE DECISION, STATED. This system has never recorded a stock count: no
# delivery note, no shelf audit, nothing. So there is no honest way to compute
# what is on the shelf, and a "remaining" column derived from sales alone would
# be a plausible-looking invention — the exact thing this product refuses to do.
#
# What IS available is the shopkeeper's own word, so that is what is stored, and
# only that. He counts a shelf, types the number, and the counter records BOTH
# the number and the moment. Remaining is then computed over the window that
# arithmetic is actually valid for:
#
#     remaining = units he counted  -  packets billed SINCE he counted
#
# Not "since the beginning of time", which would subtract a year of sales from
# this morning's count and print a large negative number with total confidence.
#
# WHAT IT COSTS WHEN IT IS WRONG: this figure is blind to anything that leaves
# the shop without passing the counter — breakage, a packet handed to a
# relative, a bill taken in cash off-counter — and blind to deliveries after the
# count. It will drift, always downward-biased. That is why the page prints the
# count time beside every remaining figure and calls it "unless something left
# without passing the counter". A SKU with no count shows "not counted", never a
# zero: a zero is a claim.


def read_opening_stock() -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    raw, err = _load_json(stock_path())
    if raw is None or not isinstance(raw, dict):
        return {}, err
    if raw.get("format") != STOCK_FORMAT or not isinstance(raw.get("stock"), dict):
        # An unrecognised format is discarded rather than guessed at, but the
        # caller is told, so "your counts vanished" is never silent.
        return {}, f"opening_stock.json is format {raw.get('format')!r}, not {STOCK_FORMAT}"
    out: dict[str, dict[str, Any]] = {}
    for sku, rec in raw["stock"].items():
        if not isinstance(rec, dict):
            continue
        units = rec.get("units")
        if not isinstance(units, int) or isinstance(units, bool) or units < 0:
            continue
        out[str(sku)] = {"units": units, "counted_at": rec.get("counted_at")}
    return out, err


def write_opening_stock(stock: dict[str, dict[str, Any]]) -> None:
    """Atomic replace. A half-written counts file read by the next request would
    look like the shopkeeper's counts had been deleted."""
    path = stock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": STOCK_FORMAT,
        "note": (
            "Counted by the shopkeeper, not by the counter. This system has no "
            "stock sensor; these are his numbers and the moment he gave them."
        ),
        "stock": {sku: dict(rec) for sku, rec in sorted(stock.items())},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ the money

def paisa_get(path: str) -> tuple[int, dict[str, Any]]:
    """One read-only GET to the money service. Never raises, never carries a
    secret. Reimplemented rather than imported — see the module docstring on why
    this file cannot reach into tools.upload_app."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{PAISA_BASE}{path}", timeout=PAISA_TIMEOUT_S) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 - error bodies are not guaranteed JSON
            return exc.code, {"ok": False, "reason": f"paisa returned HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 - urllib raises a wide family
        return 503, {
            "ok": False,
            "reason": "paisa_unreachable",
            "detail": (
                f"The money service did not answer at {PAISA_BASE} "
                f"({type(exc).__name__}). Nothing on this page settles money in "
                f"any case; what is missing is a READ of a service that is not "
                f"running."
            ),
        }


def key_id_prefix(key_id: Any) -> Optional[str]:
    """'rzp_live_AbCdEf1234' -> 'rzp_live'.

    The key ID is a PUBLIC identifier — Razorpay puts it in the checkout page —
    so showing which gateway and which mode this counter is pointed at is not a
    disclosure. The random tail is dropped anyway: it identifies the account and
    it tells a shopkeeper nothing he can act on. The key SECRET and the webhook
    secret are never read by this module at all; they are reported as booleans
    by the money service and passed straight through as booleans.
    """
    if not isinstance(key_id, str) or not key_id:
        return None
    parts = key_id.split("_")
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return parts[0]


def webhook_liveness(health: dict[str, Any], reachable: bool) -> dict[str, Any]:
    """Is anything still able to reach this counter?

    A counter whose webhook path has quietly died looks IDENTICAL to one where
    nobody has paid yet: both show a link, both spin, both never turn green.
    This is the block that tells them apart, and the distinction is not a
    threshold — it is whether a webhook has ever arrived at all.
    """
    if not reachable:
        return {
            "status": "unknown",
            "headline": "The money service did not answer, so nothing can be "
                        "said about the webhook path.",
            "webhooks_seen": None,
            "last_webhook_at": None,
            "last_green_webhook_at": None,
            "silent_for_seconds": None,
            "silent_after_seconds": WEBHOOK_SILENT_AFTER_S,
        }

    seen = health.get("webhooks_seen")
    seen = seen if isinstance(seen, int) and not isinstance(seen, bool) else 0
    last = health.get("last_webhook_at")
    last_green = health.get("last_green_webhook_at")

    silent_for = None
    parsed = _parse_ts(last)
    if parsed is not None:
        silent_for = int((datetime.now(timezone.utc) - parsed).total_seconds())

    if seen <= 0 or parsed is None:
        # NOT "ever". `webhooks_seen` is a PROCESS-LIFETIME counter — paisa
        # holds it as a plain attribute and it resets to zero on every restart
        # (see the note beside `_webhooks_seen` in paisa.py). Saying "ever" is
        # a claim about all of history made from a number that only knows
        # about this process, and the same server said `bills_settled: 1` on a
        # verified chain in the same breath. That is the tunnel incident in
        # FAILURES.md running BACKWARDS: a false alarm sending a shopkeeper to
        # rebuild infrastructure that works, and in front of a judge it is the
        # product calling itself broken.
        #
        # The chain is the record of what has ever happened. This field is the
        # record of what has happened since the counter started, and it may
        # only say the second thing.
        return {
            # THE TAG STAYS "never" and the SENTENCE changes. Two screens and a
            # TypeScript union consume this value; renaming it would drop both
            # through to their else branch and say nothing at all, which is a
            # worse failure than a badly-named tag. The tag is a misnomer —
            # this state is "quiet since this process started" — and the
            # headline below is now the thing that has to be true, because the
            # headline is what a shopkeeper reads.
            "status": "never",
            "headline": (
                "No webhook has reached this counter since it started. If it "
                "has been running a while, check that the tunnel is up and "
                "that the Razorpay dashboard points at its current address — "
                "a bill cannot turn green without one."
            ),
            "webhooks_seen": seen,
            "last_webhook_at": last,
            "last_green_webhook_at": last_green,
            "silent_for_seconds": silent_for,
            "silent_after_seconds": WEBHOOK_SILENT_AFTER_S,
        }

    if silent_for is not None and silent_for > WEBHOOK_SILENT_AFTER_S:
        status = "silent"
        headline = (
            f"The last webhook reached this counter {_ago(silent_for)} ago. "
            f"The path worked once, so this is either a quiet shop or a tunnel "
            f"that has been revoked since. Charge one rupee to yourself to find "
            f"out which."
        )
    else:
        status = "live"
        headline = f"A webhook reached this counter {_ago(silent_for or 0)} ago."

    return {
        "status": status,
        "headline": headline,
        "webhooks_seen": seen,
        "last_webhook_at": last,
        "last_green_webhook_at": last_green,
        "silent_for_seconds": silent_for,
        "silent_after_seconds": WEBHOOK_SILENT_AFTER_S,
    }


def _ago(seconds: int) -> str:
    """Plain English, integer arithmetic only."""
    seconds = int(seconds)
    if seconds < 0:
        # A clock that disagrees with the money service is worth saying out
        # loud rather than rendering as '0 seconds'.
        return f"{-seconds} seconds in the future (the clocks disagree)"
    if seconds < 120:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours"
    return f"{hours // 24} days"


# ------------------------------------------------------------------- history

@router.get("/history")
def history_ep(limit: str | None = None, since: str | None = None) -> JSONResponse:
    """Completed bills, newest first.

    A bill is a session that reached ``session/done`` — the moment the basket
    closed and there was a total to charge. Sessions that never closed (a probe,
    a customer who walked away, a mat that never locked) are not bills and are
    not listed; they remain readable one at a time through the detail endpoint.
    """
    try:
        want = _require_limit(limit)
        after = _require_since(since)

        records, chain = read_chain()
        bills = bills_from(records)

        rows: list[dict[str, Any]] = []
        undated = 0
        for bill in bills.values():
            if not bill["closed"]:
                continue
            if after is not None:
                at = _parse_ts(bill["at"])
                if at is None:
                    # Keep it. Dropping a real bill because its timestamp will
                    # not parse is the omission this endpoint exists to avoid;
                    # it is counted instead, and the count is reported.
                    undated += 1
                elif at < after:
                    continue
            rows.append(_summary(bill))

        # The chain is append-only, so its order IS chronological and reversing
        # it is newest-first by construction — no sort on a parsed timestamp,
        # which would reorder bills that share a millisecond.
        rows.reverse()
        total_matching = len(rows)

        return JSONResponse(
            {
                "ok": True,
                "settles_money": False,
                "bills": rows[:want],
                "count": min(want, total_matching),
                "matched": total_matching,
                "limit": want,
                "since": since,
                "unparsed_timestamps": undated,
                "sessions_in_ledger": len(bills),
                "chain": chain,
            }
        )
    except ManageRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/history/{session_id}")
def history_one_ep(session_id: str) -> JSONResponse:
    """One bill in full, INCLUDING the items that were excluded from the total.

    Excluding an item the counter could not identify is invariant 7 working —
    a short bill an operator can see beats a confident bill that is wrong. A
    history that showed only the priced lines would hide the one thing the
    shopkeeper has to check by hand, so the exclusions are a first-class list
    here and not a footnote.
    """
    try:
        records, chain = read_chain()
        bills = bills_from(records)
        bill = bills.get(session_id)
        if bill is None:
            raise ManageRefused(
                R_UNKNOWN_SESSION,
                f"{session_id!r} does not appear anywhere in the audit chain at "
                f"{chain['path']}. Either it was never opened on this counter, "
                f"or it is on the far side of a chain break.",
                status=404,
            )
        out = dict(bill)
        out["ok"] = True
        out["settles_money"] = False
        out["chain"] = chain
        # Total-of-lines, recomputed here rather than trusted, so the page can
        # show the counter disagreeing with itself if it ever does. Integer
        # paise throughout: this is a sum of ints, never an average.
        out["lines_sum_paise"] = sum(
            line["price_paise"] for line in bill["line_items"]
            if isinstance(line["price_paise"], int)
        )
        out["total_agrees"] = out["lines_sum_paise"] == bill["total_paise"]
        return JSONResponse(out)
    except ManageRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


def _require_limit(raw: Any) -> int:
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise ManageRefused(
            R_BAD_LIMIT,
            f"limit={raw!r} is not a whole number. Leave it out for "
            f"{DEFAULT_LIMIT}.",
        ) from None
    if want < 1:
        raise ManageRefused(
            R_BAD_LIMIT, f"limit={want} asks for no bills at all; the smallest useful limit is 1."
        )
    if want > MAX_LIMIT:
        raise ManageRefused(
            R_BAD_LIMIT,
            f"limit={want} is over the ceiling of {MAX_LIMIT}. Ask in pages, or "
            f"read the ledger directly.",
        )
    return want


def _require_since(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    parsed = _parse_ts(raw)
    if parsed is None:
        raise ManageRefused(
            R_BAD_SINCE,
            f"since={raw!r} is not an ISO-8601 timestamp. The ledger writes "
            f"them like 2026-08-29T05:28:00.078+00:00.",
        )
    return parsed


# ----------------------------------------------------------------- inventory

def _inventory_rows() -> dict[str, Any]:
    records, chain = read_chain()
    cat = catalogue()
    stock, stock_err = read_opening_stock()

    # SALES, COUNTED FROM THE CHAIN. Two different numbers, because they answer
    # two different questions and collapsing them would be a lie either way:
    #
    #   billed   — committed into a basket that CLOSED (session/exit inside a
    #              session that reached session/done). The packet left the
    #              shelf. This is the number a shopkeeper means by "sold".
    #   settled  — the same, in a session a signature-verified webhook turned
    #              PAID. Invariant 2: this is the only money that is certainly
    #              real, and on this install it is a much smaller number.
    #
    # Reporting only `settled` would show a shop that has sold almost nothing.
    # Reporting only `billed` would count baskets nobody paid for. Both are
    # shown, labelled, and never added together.
    billed: dict[str, int] = {}
    settled: dict[str, int] = {}
    last_billed: dict[str, str] = {}
    last_settled: dict[str, str] = {}
    excluded_seen: dict[str, int] = {}

    bills = bills_from(records)
    for bill in bills.values():
        for line in bill["excluded"]:
            excluded_seen[line["sku_id"]] = excluded_seen.get(line["sku_id"], 0) + 1
        if not bill["closed"]:
            continue
        for line in bill["line_items"]:
            sku = line["sku_id"]
            billed[sku] = billed.get(sku, 0) + 1
            at = line["at"] or bill["at"]
            if isinstance(at, str):
                last_billed[sku] = at
            if bill["settled"]:
                settled[sku] = settled.get(sku, 0) + 1
                if isinstance(at, str):
                    last_settled[sku] = at

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for sku, row in sorted(cat["items"].items()):
        counted = stock.get(sku)
        since_count = None
        remaining = None
        if counted is not None:
            counted_at = _parse_ts(counted.get("counted_at"))
            # Only sales AFTER the count are subtracted from it. Subtracting a
            # year of history from this morning's shelf count would print a
            # large negative number with complete confidence.
            since_count = 0
            for bill in bills.values():
                if not bill["closed"]:
                    continue
                for line in bill["line_items"]:
                    if line["sku_id"] != sku:
                        continue
                    at = _parse_ts(line["at"] or bill["at"])
                    if counted_at is None or (at is not None and at >= counted_at):
                        since_count += 1
            remaining = counted["units"] - since_count
        rows.append(
            {
                **row,
                "billed_count": billed.get(sku, 0),
                "last_billed_at": last_billed.get(sku),
                "settled_count": settled.get(sku, 0),
                "last_settled_at": last_settled.get(sku),
                "amber_count": excluded_seen.get(sku, 0),
                "opening_stock_units": None if counted is None else counted["units"],
                "opening_stock_counted_at": None if counted is None else counted.get("counted_at"),
                "billed_since_count": since_count,
                "remaining_units": remaining,
                "in_catalogue": True,
            }
        )

    # SKUs the chain has sold that the catalogue no longer holds. They were
    # renamed, deleted, or belonged to a demo. Hiding them would make the sales
    # column silently not add up to the bills on the history page, and a
    # shopkeeper chasing that difference has nowhere to look.
    gone: list[dict[str, Any]] = []
    for sku in sorted(set(billed) | set(excluded_seen)):
        if sku in cat["items"]:
            continue
        gone.append(
            {
                "sku_id": sku,
                "name": None,
                "price_paise": None,
                "price_rupees": None,
                "taught_by": None,
                "taught_label": "no longer in the catalogue",
                "billed_count": billed.get(sku, 0),
                "last_billed_at": last_billed.get(sku),
                "settled_count": settled.get(sku, 0),
                "last_settled_at": last_settled.get(sku),
                "amber_count": excluded_seen.get(sku, 0),
                "in_catalogue": False,
            }
        )

    return {
        "items": rows,
        "sold_but_not_in_catalogue": gone,
        "chain": chain,
        "catalogue_problems": cat["problems"],
        "orphan_code_bindings": cat["orphan_code_bindings"],
        "stock_problem": stock_err,
        "store_dir": cat["dir"],
        "now": now_iso,
    }


@router.get("/inventory")
def inventory_ep() -> JSONResponse:
    """What is taught, what it costs, and how much of it has gone out.

    STOCK: this counter has no stock sensor and nobody has ever entered a stock
    count, so there is no stock level to show. What there is instead is an
    OPENING COUNT the shopkeeper can set per SKU; `remaining_units` is that
    count minus what has been billed since he set it, and it is null — never
    zero — for anything he has not counted.
    """
    try:
        payload = _inventory_rows()
        rows = payload["items"]
        return JSONResponse(
            {
                "ok": True,
                "settles_money": False,
                "count": len(rows),
                "stock_tracking": "opening_count",
                "stock_note": (
                    "This counter has no stock sensor. A remaining figure is "
                    "your own count minus what the counter has billed since you "
                    "made it, and it cannot see anything that left the shop "
                    "without passing the counter."
                ),
                "counted_skus": sum(
                    1 for r in rows if r.get("opening_stock_units") is not None
                ),
                **payload,
            }
        )
    except ManageRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/inventory/{sku_id}/stock")
async def inventory_stock_ep(sku_id: str, request: Request) -> JSONResponse:
    """Record the shopkeeper's own count of one product. Integer units.

    This is the ONLY write on these three screens and it settles no money. It
    is stored next to the catalogue, not appended to the audit chain: the chain
    has a single writer holding an flock (gawaah/kernel.py) and a second,
    unsynchronised appender is how a hash chain gets broken. The count is his
    statement, not the counter's observation, so keeping the two apart is also
    the honest filing.
    """
    try:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - a bad body is a refusal, not a crash
            raise ManageRefused(
                R_BAD_BODY, 'the body must be JSON, like {"units": 40}.'
            ) from None
        if not isinstance(body, dict):
            raise ManageRefused(
                R_BAD_BODY, f'the body must be a JSON object, like {{"units": 40}} — got {type(body).__name__}.'
            )
        if "units" not in body:
            raise ManageRefused(
                R_STOCK_MISSING, 'no "units" in the body. Send {"units": 40}.'
            )
        units = body["units"]
        if isinstance(units, bool) or not isinstance(units, int):
            raise ManageRefused(
                R_STOCK_NOT_INTEGER,
                f"units={units!r} is not a whole number of packets. Half a "
                f"packet is not a thing a shelf holds.",
            )
        if units < 0:
            raise ManageRefused(
                R_STOCK_NEGATIVE,
                f"units={units} is negative. Zero is a valid count and means "
                f"the shelf is empty.",
            )
        if units > MAX_OPENING_STOCK_UNITS:
            raise ManageRefused(
                R_STOCK_TOO_LARGE,
                f"units={units} is over {MAX_OPENING_STOCK_UNITS}. Nothing is "
                f"stored: a number nobody typed on purpose must not appear on "
                f"the page as though he had.",
            )

        cat = catalogue()
        if sku_id not in cat["items"]:
            raise ManageRefused(
                R_UNKNOWN_SKU,
                f"{sku_id!r} is not in the catalogue at {cat['dir']}. Teach the "
                f"product first; a count against a product that does not exist "
                f"prices nothing.",
                status=404,
            )

        stock, _ = read_opening_stock()
        counted_at = datetime.now(timezone.utc).isoformat()
        stock[sku_id] = {"units": units, "counted_at": counted_at}
        try:
            write_opening_stock(stock)
        except OSError as exc:
            raise ManageRefused(
                R_STOCK_NOT_WRITTEN,
                f"the count could not be written to {stock_path()} "
                f"({type(exc).__name__}: {exc}). Nothing was recorded, so the "
                f"page is not about to show you a number that is not on disk.",
            ) from None

        return JSONResponse(
            {
                "ok": True,
                "settles_money": False,
                "sku_id": sku_id,
                "units": units,
                "counted_at": counted_at,
                "reason": "opening_count_recorded",
                "detail": (
                    f"Counted by you at {counted_at}. From here the page "
                    f"subtracts what the counter bills; it cannot see anything "
                    f"that leaves the shop another way."
                ),
            }
        )
    except ManageRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


# ------------------------------------------------------------------ settings

@router.get("/settings")
def settings_ep() -> JSONResponse:
    """A read-only readout of what this counter is configured to do.

    Read-only in the strong sense: there is no POST beside it, because every
    number here is a decision that was made somewhere it can be reviewed — a
    constant in gawaah/identity.py, the gates written into the catalogue, the
    environment the money service booted with. A settings page that could widen
    phi from a browser would make invariant 7 a suggestion.

    NO SECRET, NO PREFIX OF ONE, NO LENGTH. The key secret and the webhook
    secret appear here only as booleans, exactly as the money service reports
    them; this module never reads either value and has no code path that could.
    """
    try:
        records, chain = read_chain()
        cat = catalogue()

        status, health = paisa_get("/health")
        reachable = status == 200 and bool(health.get("ok"))

        by_taught: dict[str, int] = {}
        codes_bound = 0
        for row in cat["items"].values():
            by_taught[row["taught_by"]] = by_taught.get(row["taught_by"], 0) + 1
            codes_bound += len(row["codes"])

        gates = cat["gates_on_disk"]
        # The catalogue records the gates it was BUILT under, and shop_store
        # refuses to reopen it under different ones. Those are therefore the
        # gates in force, and the library defaults are only what a counter with
        # no catalogue yet would use. Saying which is which matters: they are
        # equal today and a reader who assumed that would be wrong tomorrow.
        recognition = {
            "phi": (gates or {}).get("phi", DEFAULT_PHI),
            "theta": (gates or {}).get("theta", DEFAULT_THETA),
            "tau_mm": (gates or {}).get("tau_mm", DEFAULT_TAU_MM),
            "phi_appearance_only": (gates or {}).get(
                "phi_appearance_only", PHI_APPEARANCE_ONLY
            ),
            "source": "catalogue" if gates else "library defaults (nothing taught yet)",
            "library_defaults": {
                "phi": DEFAULT_PHI,
                "theta": DEFAULT_THETA,
                "tau_mm": DEFAULT_TAU_MM,
                "phi_appearance_only": PHI_APPEARANCE_ONLY,
            },
        }

        mat = {
            "sheet": "A3",
            "width_mm": MAT_W_MM,
            "height_mm": MAT_H_MM,
            "markers": len(MARKER_IDS),
            "marker_ids": list(MARKER_IDS),
            "marker_mm": MARKER_MM,
            "margin_mm": MARGIN_MM,
            "marker_centres_mm": [[round(x, 2), round(y, 2)] for x, y in marker_centres_mm()],
            "rectified_buffer_px": [BUF_W, BUF_H],
            "max_scale_error": MAX_SCALE_ERR,
            "max_persp_index": MAX_PERSP_INDEX,
            "max_tilt_deg": 8,
        }

        money = {
            "reachable": reachable,
            "base_url": PAISA_BASE,
            "status": status,
            "mode": health.get("mode") if reachable else None,
            "key_id_prefix": key_id_prefix(health.get("key_id")) if reachable else None,
            # Booleans only, straight through. Never a value, a prefix, a length.
            "key_secret_configured": bool(health.get("key_secret_configured"))
            if reachable
            else None,
            "webhook_secret_configured": bool(health.get("webhook_secret_configured"))
            if reachable
            else None,
            "sessions": health.get("sessions") if reachable else None,
            "intents": health.get("intents") if reachable else None,
            "intents_needing_human": health.get("intents_needing_human") if reachable else None,
            # THE HISTOGRAM, forwarded. Settings suppressed its amber verdict
            # because `needs_human` was 0 while 269 intents sat INDETERMINATE
            # — "the gateway was called and the outcome is unknown, so money
            # may have moved", which is that verdict's own copy, wired to a
            # different column. paisa.py records why the histogram exists: "a
            # health readout that only counts escalations is a tautology, not
            # a measurement." It was dropped here, so the screen could not see
            # it even if it wanted to.
            "intents_by_state": health.get("intents_by_state") if reachable else None,
            "price_book_entries": health.get("price_book_entries") if reachable else None,
            "detail": None if reachable else health.get("detail") or health.get("reason"),
        }

        bills = bills_from(records)
        closed = sum(1 for b in bills.values() if b["closed"])
        settled = sum(1 for b in bills.values() if b["settled"])

        return JSONResponse(
            {
                "ok": True,
                "settles_money": False,
                "recognition": recognition,
                "mat": mat,
                "catalogue": {
                    "count": len(cat["items"]),
                    "by_taught": by_taught,
                    "codes_bound": codes_bound,
                    "orphan_code_bindings": cat["orphan_code_bindings"],
                    "problems": cat["problems"],
                    "dir": cat["dir"],
                    "gates_from_disk": gates is not None,
                },
                "money": money,
                "webhook": webhook_liveness(health, reachable),
                "ledger": {
                    "path": chain["path"],
                    "exists": chain["exists"],
                    "head": chain["head"],
                    "lines": chain["lines_verified"],
                    "chain_ok": chain["ok"],
                    "error": chain["error"],
                    "sessions": len(bills),
                    "bills_closed": closed,
                    "bills_settled": settled,
                },
            }
        )
    except ManageRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "ManageRefused",
    "bills_from",
    "catalogue",
    "data_dir",
    "key_id_prefix",
    "paisa_get",
    "read_chain",
    "read_opening_stock",
    "router",
    "sku_of",
    "store_dir",
    "webhook_liveness",
    "write_opening_stock",
]


# ------------------------------------------------------------- the day brief
#
# "Aaj kitna hua?" is the question a shopkeeper actually asks at the end of a
# shift, and until now this product held every number needed to answer it and
# never answered. Everything below is DERIVED from the same chain the history
# screen walks — no second store, no running totals, nothing estimated. Where a
# figure is a comparison, both sides of it are computed the same way from the
# same records, because a delta between two differently-derived numbers is a
# random number with a percent sign.


def _local_day_bounds(day: Optional[str]) -> tuple[datetime, datetime, str]:
    """Midnight-to-midnight in the COUNTER'S OWN timezone.

    The chain stamps UTC. A shopkeeper's day does not start at 05:30 — asking
    "what did I sell today" and being answered with a UTC window quietly moves
    last evening's sales into tomorrow. What it costs when this is wrong: a
    counter physically moved across timezones mid-shift splits that day oddly.
    That is a case this product does not claim.
    """
    tz = datetime.now().astimezone().tzinfo
    if day:
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise ManageRefused(
                "day_malformed",
                f"{day!r} is not a calendar day. Write it as YYYY-MM-DD, for "
                f"example 2026-09-01.")
        start = d.replace(tzinfo=tz)
    else:
        now = datetime.now(tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end, start.strftime("%Y-%m-%d")


def _brief_for(bills: dict[str, dict[str, Any]],
               start: datetime, end: datetime) -> dict[str, Any]:
    """One day's numbers, from bills already derived off the chain."""
    day_bills: list[dict[str, Any]] = []
    for b in bills.values():
        if not b["closed"]:
            continue
        at = _parse_ts(b["at"])
        if at is None or not (start <= at < end):
            continue
        day_bills.append(b)

    revenue = sum(int(b["total_paise"] or 0) for b in day_bills)
    settled = [b for b in day_bills if b["settled"]]
    settled_paise = sum(int(b["total_paise"] or 0) for b in settled)
    units: dict[str, int] = {}
    line_revenue: dict[str, int] = {}
    excluded = 0
    for b in day_bills:
        excluded += len(b["excluded"])
        for line in b["line_items"]:
            sku = line["sku_id"]
            units[sku] = units.get(sku, 0) + 1
            if line["price_paise"] is not None:
                line_revenue[sku] = line_revenue.get(sku, 0) + int(line["price_paise"])

    n = len(day_bills)
    return {
        "bills": n,
        "revenue_paise": revenue,
        "revenue_rupees": to_rupees_str(revenue),
        # Integer division, floor, and SAID to be the floor. An average is a
        # description, not money anyone is charged, but it still may not invent
        # a fraction of a paisa.
        "average_paise": (revenue // n) if n else 0,
        "average_rupees": to_rupees_str(revenue // n) if n else "0.00",
        "settled_count": len(settled),
        "settled_paise": settled_paise,
        "settled_rupees": to_rupees_str(settled_paise),
        "awaiting_count": n - len(settled),
        "awaiting_paise": revenue - settled_paise,
        "awaiting_rupees": to_rupees_str(revenue - settled_paise),
        "excluded_lines": excluded,
        "first_bill_at": day_bills[0]["at"] if day_bills else None,
        "last_bill_at": day_bills[-1]["at"] if day_bills else None,
        "units_by_sku": units,
        "line_revenue_by_sku": line_revenue,
    }


@router.get("/today")
def today_ep(day: str | None = None) -> JSONResponse:
    """The day brief: what happened at this counter today, from the chain.

    `?day=YYYY-MM-DD` reads any past day the same way — the screen's "yesterday"
    comparison is this endpoint asking about two windows, not a cached delta.
    """
    try:
        start, end, label = _local_day_bounds(day)
        records, chain = read_chain()
        bills = bills_from(records)

        today = _brief_for(bills, start, end)
        yesterday = _brief_for(bills, start - timedelta(days=1), start)

        cat = catalogue()
        names = {sku: rec.get("name") or sku for sku, rec in cat["items"].items()}
        top = sorted(today["units_by_sku"].items(),
                     key=lambda kv: (-kv[1], kv[0]))[:5]
        top_sellers = [{
            "sku_id": sku,
            "name": names.get(sku, sku),
            "units": n_units,
            "revenue_paise": today["line_revenue_by_sku"].get(sku, 0),
            "revenue_rupees": to_rupees_str(today["line_revenue_by_sku"].get(sku, 0)),
            "still_in_catalogue": sku in names,
        } for sku, n_units in top]

        status, health = paisa_get("/health")
        webhook = webhook_liveness(health if status == 200 else {}, status == 200)

        for scratch in ("units_by_sku", "line_revenue_by_sku"):
            today.pop(scratch, None)
            yesterday.pop(scratch, None)

        return JSONResponse({
            "ok": True, "settles_money": False,
            "date": label,
            "today": today,
            "yesterday": yesterday,
            "top_sellers": top_sellers,
            "webhook": webhook,
            "chain": chain,
            "derived_from": ("every figure is counted from the hash-chained "
                             "audit log for this calendar day, in this "
                             "counter's own timezone; nothing is cached and "
                             "nothing is estimated"),
        })
    except ManageRefused as exc:
        return _refusal(exc)
    except Exception as exc:                                  # never a 500
        return _crash(exc)
