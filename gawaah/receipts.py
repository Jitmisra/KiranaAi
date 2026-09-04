"""RASEED — the bill a customer can keep.

The counter already knows what it billed: `gawaah/manage.py` rebuilds every
bill from the hash-chained audit log for the shopkeeper's History screen. This
module puts the SAME bill in front of the person who paid for it, in the three
shapes a customer can actually take away:

    GET /receipt/{session_id}        the bill as JSON
    GET /receipt/{session_id}/page   the bill as a printable page
    GET /receipt/{session_id}/qr     a QR of that page's address
    GET /receipt/{session_id}/link   what that QR carries, as text

The customer photographs the QR off the counter screen and keeps the bill on
their own phone. Nothing is emailed, nothing is texted, and no address is asked
for — the shop never learns anything about the customer in order to hand them
their own receipt.

FIVE RULES THIS FILE EXISTS TO KEEP
===================================

1.  ONE DERIVATION OF A BILL, AND IT IS NOT HERE. Every figure below comes out
    of ``manage.read_chain()`` and ``manage.bills_from()`` — the same two
    functions the History screen calls, over the same audit chain. A second
    fold of the ledger written here would be a second answer to "what was
    billed", and the first time the receipt and the history disagreed there
    would be no way to tell which one to believe. What this module adds is
    presentation: packets grouped into lines with a quantity, names looked up,
    and a page a person can read.

2.  A RECEIPT IS A RECORD OF WHAT HAPPENED, NOT A CLAIM THAT MONEY MOVED. A
    bill that no signature-verified webhook settled says NOT PAID, in the
    banner, in the title of the page and in the JSON. Invariant 2 says only the
    gateway's signed callback turns a bill green; this file can report that
    callback and can never stand in for it.

3.  THE QR CARRIES THIS SERVER'S OWN ADDRESS AND NOTHING ELSE. It is built from
    the Host header the request arrived on, then the finished string is put
    back through the same checks ``/qr/link`` runs before it encodes a payable
    link: not a UPI payload, http or https, a host that is nothing but the
    characters a hostname may hold, and — the one this endpoint adds — a host
    that is NOT a payment gateway. A receipt code opens a receipt. There is no
    parameter here a caller could use to make it point anywhere else, and the
    checks are what keeps that true on the day somebody adds one.

4.  NOTHING IS WRITTEN. Not the receipt, not a view count, not an audit line.
    A receipt changes no money and no stock, so there is nothing here that the
    audit chain is for; and the chain in ``results/audit.jsonl`` has a single
    writer holding a lock in another process, which a page a customer can
    refresh must never queue behind. The receipt is recomputed on every read,
    which is also why it cannot drift from the ledger it came from.

5.  INTEGER PAISE. Quantities are integers, a line total is a unit price times
    a count, and the bill total is the one the chain recorded at the moment the
    basket closed. No float, no division, no rounding — and where the lines do
    not add up to the recorded total, BOTH numbers are shown rather than one of
    them being quietly adjusted.

WHAT THIS DOES NOT DO
=====================
It does not price anything. A line's price is the price the chain recorded when
that packet crossed the counter; today's catalogue supplies the product NAME and
never the money. It does not know about tax, because this counter has never
computed any. It cannot produce a receipt for a basket that never closed — that
is not a bill yet — and it cannot produce one for a bill that sits on the far
side of a chain break, because a line whose hash does not recompute is not
evidence of anything.

MOUNTING
========
``router`` carries NO prefix and every path below is absolute::

    from gawaah import receipts
    app.include_router(receipts.router)      # -> /receipt/{session_id}
"""
from __future__ import annotations

import datetime as _dt
import re
from html import escape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import manage
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach. The reason
# names the state; the sentence that says what to do about it goes in `detail`.
# Two of them deliberately reuse strings that already exist elsewhere in this
# program — a screen showing one message per state should not have to learn a
# second name for the same state depending on which route answered.

R_BAD_SESSION_ID = "session_id_malformed"
R_UNKNOWN_SESSION = "session_not_in_the_ledger"      # as gawaah/manage.py names it
R_NOT_A_BILL = "session_never_became_a_bill"
R_NO_HOST = "cannot_tell_this_shops_address"         # as gawaah/storefront.py names it
R_REFUSED_QR = "refused_to_encode_this_string"       # as tools/upload_app.py names it
R_NO_ENCODER = "qr_encoder_unavailable"
R_INTERNAL = "receipt_internal_error"


#: A session id becomes part of a URL, part of a QR and part of an HTML page,
#: so it is checked against a charset BEFORE it is used for any of the three.
#: The ids this counter actually mints are words, digits, underscores and
#: hyphens (`till_mth34cri_s4d6jemx`, `shop_ord_2f1c…`); the extra punctuation
#: allowed here is what a hand-run probe or another service might use. Anything
#: outside it — a space, a slash, a control character, a script tag — is refused
#: by name rather than escaped and carried around.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@#-]{0,127}$")

#: `gawaah/shopadmin.py` writes this file and is its only writer. The name is
#: repeated here rather than imported at module scope so that a receipt can
#: still be printed on a counter whose admin module is not loaded — but
#: `_profile_path()` prefers shopadmin's own answer when it is available, and
#: a test pins the two constants together so a rename cannot silently cost the
#: shop its name on every receipt it prints.
PROFILE_NAME = "shop_profile.json"
PROFILE_FORMAT = 1

#: Hosts a payable link may live on — `tools.upload_app.LINK_HOSTS`, repeated
#: for the same reason as above and preferred from the till when it is loaded.
#: This module uses the list the other way round from everybody else: a receipt
#: QR pointing AT one of these hosts is refused, because a code that opens a
#: bill must never be a code that asks for money.
GATEWAY_HOSTS = ("rzp.io", "razorpay.com", "rzp.link")

#: Addresses that mean "whatever device opens this". A QR carrying one is a
#: perfectly good QR that no phone can follow, which is a silent failure unless
#: something says it out loud — see `/receipt/{id}/link`.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")

#: The chain's own words for why an item was left off a bill, in the words a
#: customer standing at the counter can act on. The raw reason stays in the JSON
#: — a shopkeeper matching this against the History screen needs the exact
#: string — and the sentence is what gets printed. An unrecognised reason falls
#: back to the plain sentence rather than printing snake_case on a receipt.
EXCLUSION_LABELS = {
    "exit_crossing_committed_amber_excluded":
        "the counter could not name this, so it was left off the bill",
}
EXCLUSION_FALLBACK = "the counter could not name this"

#: QR size in pixels, clamped. 200 is below the measured floor for a code read
#: off a screen at arm's length; 1600 is more than any counter display has.
MIN_QR_PX = 200
MAX_QR_PX = 1600
DEFAULT_QR_PX = 620


class ReceiptRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: ReceiptRefused) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none.

    A customer holding a phone at a counter cannot act on a stack trace, but a
    shopkeeper can act on 'FileNotFoundError: results/audit.jsonl', so the
    exception type and message are passed through rather than swallowed.
    """
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ------------------------------------------------------------ small things --


def _rupees(value: int) -> str:
    """Integer paise as a rupee string. `paise()` refuses a float or a bool."""
    return to_rupees_str(int(paise(value)))


def _items(n: int) -> str:
    """'1 item' / '3 items'. A receipt saying '1 item(s)' was written for a
    machine and is being read by somebody who just paid for something."""
    return f"{int(n)} item" if int(n) == 1 else f"{int(n)} items"


def human_time(value: Any) -> Optional[str]:
    """An ISO stamp from the chain as a person reads it, in the counter's own
    timezone. None when it will not parse.

    The chain stamps UTC, and a receipt that told a customer their bill was at
    05:27 when the shop clock said 10:57 would be read as somebody else's bill.
    The conversion is to whatever timezone this machine is in, which is the
    shop's — the same call `manage._local_day_bounds` makes for the day brief,
    and the same stated limit applies: a counter physically moved across
    timezones will print the new one.

    Built field by field rather than with a `%-I` format, which is not portable
    off glibc and BSD, and returns None rather than a guess when the stamp is
    not a stamp: a receipt saying nothing about when beats one saying the wrong
    thing.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    local = parsed.astimezone()
    hour12 = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    zone = local.strftime("%Z")
    stamp = (f"{local.day} {local.strftime('%B %Y')}, "
             f"{hour12}:{local.minute:02d} {ampm}")
    return f"{stamp} {zone}" if zone else stamp


def _int_or_none(value: Any) -> Optional[int]:
    """An integer, or None. A bool is not a number here — see money.paise."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _till_if_loaded() -> Any:
    """The till module IF SOMETHING ELSE ALREADY IMPORTED IT. Never imports it.

    `gawaah/storefront.py` documents at length why importing the till under the
    wrong one of its two names loads a second copy of the file with its own
    catalogue directory. This module needs nothing from the till it cannot
    supply itself, so it takes the cheaper half of that lesson: look in
    sys.modules, use what is there, and never trigger the import. A receipt does
    not need the vision stack to be resident in order to print.
    """
    import sys

    for name in ("upload_app", "tools.upload_app"):
        mod = sys.modules.get(name)
        if mod is not None:
            return mod
    return None


def _gateway_hosts() -> tuple[str, ...]:
    up = _till_if_loaded()
    hosts = getattr(up, "LINK_HOSTS", None) if up is not None else None
    if isinstance(hosts, (list, tuple)) and hosts:
        return tuple(str(h) for h in hosts)
    return GATEWAY_HOSTS


def _looks_like_upi(url: str) -> bool:
    """True for anything that could be read as a UPI payment payload.

    Copied from `tools.upload_app._looks_like_upi`, including the reason for the
    stripping: "\\tupi://pay?pa=..." is still a UPI payload to every scanner
    that will ever read it off a screen. The till's own function is used when
    the till is loaded, so the two cannot drift while they share a process; this
    body is what answers when it is not.
    """
    up = _till_if_loaded()
    fn = getattr(up, "_looks_like_upi", None) if up is not None else None
    if callable(fn):
        return bool(fn(url))
    return url.lstrip("\x00-\x20 \t\r\n").lower().lstrip().startswith("upi:")


def _valid_session_id(session_id: str) -> str:
    """Checked against a strict charset before it reaches a URL or a page."""
    s = (session_id or "").strip()
    if not SESSION_ID_RE.match(s):
        raise ReceiptRefused(
            R_BAD_SESSION_ID,
            f"{session_id!r} is not a session id from this counter. They are "
            f"letters, digits, dots, dashes and underscores, up to 128 "
            f"characters — this counter writes them like "
            f"'till_mth34cri_s4d6jemx'. Nothing was looked up.")
    return s


# ------------------------------------------------------------- the shop --


def shop_dir() -> Path:
    """Where the shop's own files live — `manage.store_dir()`, not a third one.

    manage.py already resolves GAWAAH_SHOP_DIR and documents why it does so
    without importing the till. This module is built on manage; borrowing its
    answer keeps one directory in play, which is the whole point of the rule
    that a harness must never be able to reach `results/`.
    """
    return manage.store_dir()


def _profile_path() -> Path:
    """The shop profile file, found the way its own writer finds it.

    `shopadmin.profile_path()` resolves through the till, so it is only asked
    when the till is ALREADY loaded — which it is in the process that serves
    this page, and is not in a test that has no reason to pay for the vision
    stack. Both answers name the same file under the same `GAWAAH_SHOP_DIR`;
    asking the writer first is what keeps them the same file on the day one of
    them moves.
    """
    if _till_if_loaded() is not None:
        try:
            from . import shopadmin  # noqa: WPS433 - deliberately late

            return Path(shopadmin.profile_path())
        except Exception:  # noqa: BLE001 - no admin module is not a missing shop
            pass
    return shop_dir() / PROFILE_NAME


def shop_profile() -> dict[str, Any]:
    """The shop's name, address and phone, or a stated absence.

    A shop that has not been named is NOT an error and must not read as one: a
    counter set up an hour ago has a catalogue and no signboard yet. What would
    be wrong is inventing a name, so the receipt says the shop has not been
    named and prints the bill anyway — the numbers on it are true either way.

    A corrupt or unrecognised file reads the same as an absent one, and says
    which, because "your shop name vanished" should never be silent.
    """
    path = _profile_path()
    out: dict[str, Any] = {
        "configured": False,
        "name": None,
        "address": None,
        "phone": None,
        "path": str(path),
        "problem": None,
    }
    if not path.exists():
        return out
    try:
        import json

        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a hand-edit is not an outage
        out["problem"] = (
            f"{path.name} could not be read ({type(exc).__name__}: {exc}), so "
            f"this receipt carries no shop name.")
        return out
    if not isinstance(doc, dict) or doc.get("format") != PROFILE_FORMAT:
        out["problem"] = (
            f"{path.name} is not a format {PROFILE_FORMAT} shop profile, so it "
            f"was not read. The shop name is missing rather than guessed at.")
        return out
    name = doc.get("name")
    out["configured"] = isinstance(name, str) and bool(name.strip())
    out["name"] = name.strip() if isinstance(name, str) and name.strip() else None
    for key in ("address", "phone"):
        value = doc.get(key)
        out[key] = value.strip() if isinstance(value, str) and value.strip() else None
    return out


def _catalogue_names() -> tuple[dict[str, str], Optional[str]]:
    """{sku_id -> name} for everything currently taught, and why not if not.

    THE NAME ONLY. Not the price: the price on this receipt is the price the
    chain recorded when the packet crossed the counter, and today's catalogue
    price is a different number about a different moment. A receipt that
    re-priced last week's bill at this week's prices would be a forgery with
    good intentions.

    An unreadable catalogue costs the receipt its product names and nothing
    else, so it is reported and stepped over rather than raised.
    """
    try:
        cat = manage.catalogue()
        items = cat.get("items") or {}
        return ({str(sku): str(rec.get("name") or sku)
                 for sku, rec in items.items()}, None)
    except Exception as exc:  # noqa: BLE001 - names are not the bill
        return {}, (
            f"the catalogue could not be read ({type(exc).__name__}: {exc}), so "
            f"these lines carry product ids instead of names. The money is "
            f"unaffected: it comes from the audit chain, not the catalogue.")


# ------------------------------------------------------------- the lines --


def group_lines(bill: dict[str, Any],
                names: dict[str, str]) -> list[dict[str, Any]]:
    """One row per product and price, with a count. Integer arithmetic only.

    The chain records one `exit` per PACKET — three Parle-G on the counter are
    three lines, `parle_g#0`, `parle_g#1`, `parle_g#2` — because that is what
    the counter saw. A customer reading a receipt wants "Parle-G  x3", so the
    packets are folded here.

    Grouped by (sku, unit price) and NOT by sku alone. If a product's price
    changed between two packets in one basket — which the chain can record and
    this module must not hide — the two prices stay two rows rather than being
    averaged into a number nobody was charged.

    A counted packet whose chain record carries no integer price keeps a null
    price and is counted separately. Filling that in with a zero would turn a
    gap in the record into a free packet.
    """
    order: list[tuple[str, Optional[int]]] = []
    groups: dict[tuple[str, Optional[int]], dict[str, Any]] = {}

    for item in bill.get("line_items") or []:
        sku_id = str(item.get("sku_id") or "")
        unit = _int_or_none(item.get("price_paise"))
        key = (sku_id, unit)
        row = groups.get(key)
        if row is None:
            row = {"sku_id": sku_id, "unit_paise": unit, "qty": 0,
                   "item_ids": []}
            groups[key] = row
            order.append(key)
        row["qty"] = int(row["qty"]) + 1
        item_id = item.get("item_id")
        if isinstance(item_id, str):
            row["item_ids"].append(item_id)

    lines: list[dict[str, Any]] = []
    for key in order:
        row = groups[key]
        sku_id = str(row["sku_id"])
        unit = row["unit_paise"]
        qty = int(row["qty"])
        name = names.get(sku_id)
        line_total = None if unit is None else int(paise(unit)) * qty
        lines.append({
            "sku_id": sku_id,
            "name": name or sku_id,
            "named_from_catalogue": name is not None,
            "qty": qty,
            "unit_paise": unit,
            "unit_rupees": None if unit is None else _rupees(unit),
            "line_paise": line_total,
            "line_rupees": None if line_total is None else _rupees(line_total),
            "priced": unit is not None,
            "item_ids": list(row["item_ids"]),
        })
    return lines


def _excluded_lines(bill: dict[str, Any],
                    names: dict[str, str]) -> list[dict[str, Any]]:
    """The items the counter saw and could not name. Never priced.

    Invariant 7 on paper. These packets crossed the counter and were left OFF
    the total because the counter would not guess what they were, and the
    customer is entitled to know that happened — a short bill they can see beats
    a confident bill that is wrong. There is no price here and there must not
    be: the counter did not have one.
    """
    out: list[dict[str, Any]] = []
    for item in bill.get("excluded") or []:
        sku_id = str(item.get("sku_id") or "")
        name = names.get(sku_id)
        reason = item.get("reason")
        out.append({
            "sku_id": sku_id,
            "name": name or sku_id,
            "named_from_catalogue": name is not None,
            "item_id": item.get("item_id"),
            "reason": reason,
            "why": EXCLUSION_LABELS.get(str(reason), EXCLUSION_FALLBACK),
            "at": item.get("at"),
            "charged": False,
        })
    return out


# -------------------------------------------------------- the money state --


def settlement(bill: dict[str, Any]) -> dict[str, Any]:
    """Did money actually arrive, and who says so.

    INVARIANT 2 IS THE WHOLE OF THIS FUNCTION. A bill turns green on a
    signature-verified webhook and on nothing else, so there are three states
    here and not two:

      unpaid   — no settlement line in the chain at all.
      webhook  — `session/webhook` with reason `settled_green`. The gateway's
                 own signed callback reached this counter. This is the only
                 state a receipt may call PAID without qualification.
      counter  — `kernel/intent.settled` with no webhook line beside it.
                 manage.py accepts this as a labelled fallback and so does this
                 module, LABELLED: it is the counter's own record of a
                 settlement, written downstream of a webhook that this chain
                 does not itself carry. Printing it as an unqualified 'paid'
                 would let a bill be green on this counter's word.
    """
    settled = bool(bill.get("settled"))
    by = bill.get("settled_by")
    by = str(by) if isinstance(by, str) else None
    verified = settled and by == "webhook"
    payment_id = bill.get("payment_id")
    payment_id = payment_id if isinstance(payment_id, str) and payment_id else None

    when = human_time(bill.get("settled_at")) or bill.get("settled_at")

    if not settled:
        state = "unpaid"
        headline = "NOT PAID"
        detail = ("This is a record of what the counter billed. Nothing here "
                  "says money moved. A bill turns paid on the payment "
                  "gateway's own signed callback, and no such callback has "
                  "reached this counter for this bill.")
    elif verified:
        state = "paid"
        headline = "PAID"
        detail = ("The payment gateway's signed callback reached this counter "
                  f"at {when} and this bill was marked paid then.")
    else:
        state = "recorded_paid_by_the_counter"
        headline = "PAID — recorded by the counter"
        detail = (f"This counter recorded settlement at {when}. The gateway's "
                  f"own signed callback is not in the audit chain beside it, "
                  f"so this line is the counter's record and not the "
                  f"gateway's confirmation. Check the payment against the "
                  f"gateway dashboard before treating it as final.")

    return {
        "settled": settled,
        "settled_by": by,
        "settled_at": bill.get("settled_at"),
        "settled_at_human": human_time(bill.get("settled_at")),
        "settled_by_verified_webhook": verified,
        "payment_state": state,
        "payment_headline": headline,
        "payment_detail": detail,
        "payment_id": payment_id,
        "payment_link_id": (bill.get("payment_link_id")
                            if isinstance(bill.get("payment_link_id"), str)
                            else None),
        "link_minted": bool(bill.get("minted")),
        "webhooks_seen": len(bill.get("webhooks") or []),
        "counter_state": bill.get("state"),
    }


# ---------------------------------------------------------- the whole bill --


def build_receipt(session_id: str) -> dict[str, Any]:
    """One bill, in the shape both the JSON and the printed page are made of.

    Derived, never stored: `manage.read_chain()` re-verifies the chain from
    genesis and `manage.bills_from()` folds it, exactly as the History screen
    does. Everything this function adds is presentation.
    """
    wanted = _valid_session_id(session_id)
    records, chain = manage.read_chain()
    bills = manage.bills_from(records)
    bill = bills.get(wanted)
    if bill is None:
        raise ReceiptRefused(
            R_UNKNOWN_SESSION,
            f"{wanted!r} does not appear in the audit chain at "
            f"{chain['path']}. Either nothing was ever billed under that id, or "
            f"it lies on the far side of a break in the chain — this chain has "
            f"{chain['lines_verified']} verified lines"
            f"{'' if chain['ok'] else ' and does not verify past that'}.",
            status=404)
    if not bill.get("closed"):
        raise ReceiptRefused(
            R_NOT_A_BILL,
            f"session {wanted!r} is in the chain but never closed, so there is "
            f"no total and no bill to print. A basket becomes a bill when the "
            f"counter writes its 'done' line; this one has "
            f"{len(bill.get('line_items') or [])} item(s) on the counter and no "
            f"such line.")

    names, catalogue_problem = _catalogue_names()
    lines = group_lines(bill, names)
    excluded = _excluded_lines(bill, names)

    # `_int_or_none` and not `int(...)`: int() of a float is exactly the silent
    # coercion invariant 1 exists to forbid, and a total that is not an integer
    # number of paise must reach `paise()` as itself so it is refused there.
    recorded = _int_or_none(bill.get("total_paise"))
    total_paise = int(paise(bill.get("total_paise") if recorded is None
                            else recorded))
    # Recomputed rather than trusted, the same way manage.py's detail endpoint
    # recomputes it, so a receipt can SHOW the counter disagreeing with itself
    # if it ever does. Sum of integers; unpriced packets are left out of it and
    # counted separately below, because adding a zero for them would make the
    # two numbers agree by inventing a free packet.
    lines_sum_paise = 0
    unpriced = 0
    for line in lines:
        if line["line_paise"] is None:
            unpriced += int(line["qty"])
        else:
            lines_sum_paise += int(paise(line["line_paise"]))
    total_agrees = lines_sum_paise == total_paise and unpriced == 0

    money = settlement(bill)

    # WAAPSI. What has gone BACK, off the kernel's own refund lines. A refund
    # is counted as refunded only on its `refund.processed` line — the one
    # the kernel writes on a signed callback — and a refund merely asked for
    # is reported separately, as asked for. Neither is netted into the total:
    # the bill stays the bill, and the figure beside it says what came back.
    refund_rows = list((refunds_from(records).get(wanted) or {}).values())
    refund_rows.sort(key=lambda r: (str(r.get("created_at") or ""), r["refund_key"]))
    refunded_paise = sum(int(paise(r["amount_paise"])) for r in refund_rows
                         if r["refunded"] and r["amount_paise"] is not None)
    refund_requested_paise = sum(
        int(paise(r["amount_paise"])) for r in refund_rows
        if r["committed"] and not r["refunded"] and r["amount_paise"] is not None)
    refunded_item_ids = {r["item_id"] for r in refund_rows if r["committed"]}
    for line in lines:
        line["refunded_qty"] = sum(
            1 for r in refund_rows if r["refunded"] and r["item_id"] in line["item_ids"])
        line["refund_committed_qty"] = sum(
            1 for r in refund_rows if r["committed"] and r["item_id"] in line["item_ids"])
        line["returnable_item_ids"] = [
            i for i in line["item_ids"] if i not in refunded_item_ids]

    notes: list[str] = []
    if refunded_paise:
        notes.append(
            f"{_rupees(refunded_paise)} of {_rupees(total_paise)} was refunded "
            f"through the payment gateway, on its own signed callback. The "
            f"total above is what was billed; it is not adjusted.")
    if refund_requested_paise:
        notes.append(
            f"A refund of {_rupees(refund_requested_paise)} has been asked for "
            f"and the gateway has not yet confirmed it.")
    if not total_agrees:
        notes.append(
            f"The lines on this receipt come to {lines_sum_paise} paise and the "
            f"counter recorded a total of {total_paise} paise. Both numbers are "
            f"printed as they are; neither has been adjusted to match the "
            f"other.")
    if unpriced:
        notes.append(
            f"{_items(unpriced)} counted into this bill carry no price on the "
            f"audit chain. They are shown without one rather than at zero.")
    if excluded:
        notes.append(
            f"{_items(len(excluded))} crossed the counter and could not be "
            f"identified, so they were left off the total and were not "
            f"charged.")
    if not chain["ok"]:
        notes.append(
            f"The audit chain this bill was rebuilt from does not verify past "
            f"line {chain['lines_verified']} ({chain['error']}). This bill is "
            f"inside the verified part; anything after that point is not shown "
            f"anywhere on this counter.")
    if catalogue_problem:
        notes.append(catalogue_problem)

    return {
        "ok": True,
        "settles_money": False,
        "session_id": wanted,
        "shop": shop_profile(),
        "at": bill.get("at"),
        "at_human": human_time(bill.get("at")),
        "opened_at": bill.get("opened_at"),
        "lines": lines,
        "line_count": len(lines),
        "item_count": sum(int(line["qty"]) for line in lines),
        "excluded": excluded,
        "excluded_count": len(excluded),
        "unpriced_items": unpriced,
        "total_paise": total_paise,
        "total_rupees": _rupees(total_paise),
        "lines_sum_paise": lines_sum_paise,
        "lines_sum_rupees": _rupees(lines_sum_paise),
        "total_agrees": total_agrees,
        **money,
        "refunds": refund_rows,
        "refund_count": len(refund_rows),
        "refunded_paise": refunded_paise,
        "refunded_rupees": _rupees(refunded_paise),
        "refund_requested_paise": refund_requested_paise,
        "refund_requested_rupees": _rupees(refund_requested_paise),
        "net_paise": total_paise - refunded_paise,
        "net_rupees": _rupees(total_paise - refunded_paise),
        "chain": chain,
        "notes": notes,
        "derived_from": ("Rebuilt from this counter's hash-chained audit log "
                         "every time it is opened. Nothing about this receipt "
                         "is stored, and no price on it comes from today's "
                         "catalogue."),
    }


# ------------------------------------------------------------ the address --


def _own_origin(request: Request) -> str:
    """This server's address as the browser reached it.

    Taken from the Host header rather than a configured value, for the reason
    `gawaah/storefront.py` sets out at length: the shopkeeper opens the till at
    `http://192.168.1.7:8790`, so that is the address a phone can reach, and
    `127.0.0.1` — which is what a configured default would say — is the one
    address guaranteed not to work from another device.

    The Host header is client-controlled, so it is charset-checked and refused
    if it is anything but a plain host and an optional port. A STATED LIMIT: an
    IPv6 literal (`[::1]:8790`) is refused by this check, so a counter reached
    only over IPv6 cannot print a receipt code. Nothing here guesses at one.
    """
    host = (request.headers.get("host") or "").strip().lower()
    if not host or not re.fullmatch(r"[a-z0-9.\-]+(:[0-9]{1,5})?", host):
        raise ReceiptRefused(
            R_NO_HOST,
            f"this counter cannot tell what address it was reached on "
            f"({host!r}), so it will not print a code pointing at a guess.")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme
             or "http").strip().lower()
    if proto not in ("http", "https"):
        proto = "http"
    return f"{proto}://{host}"


def page_path(session_id: str) -> str:
    """The path of the printable page, with the id percent-encoded.

    Encoded rather than interpolated raw: a session id may contain '#', which
    unencoded would turn everything after it into a URL fragment and hand the
    phone a receipt for a different bill.
    """
    return f"/receipt/{quote(session_id, safe='')}/page"


def receipt_url(request: Request, session_id: str) -> str:
    """The address of one receipt page, checked as hard as a payable link.

    The string is BUILT from this server's own origin and then put back through
    the checks `/qr/link` runs before it encodes anything: not a UPI payload,
    http or https, a host of nothing but hostname characters, and a host that
    does not belong to a payment gateway. None of these can fire on the code as
    written — there is no parameter here that reaches the URL. They are here
    because the day somebody adds one, the guard has to be already in place;
    that is the same argument `storefront.store_qr_ep` makes for the same two
    checks, and it has since been the reason nothing in this program encodes a
    string it did not build.
    """
    origin = _own_origin(request)
    url = f"{origin}{page_path(session_id)}"

    if _looks_like_upi(url):
        raise ReceiptRefused(
            R_REFUSED_QR,
            "that string is a UPI payload. This code opens a receipt; it does "
            "not carry money. Nothing was encoded.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ReceiptRefused(
            R_REFUSED_QR,
            f"a receipt address must be http or https, not {parts.scheme!r}. "
            f"Nothing was encoded.")
    host = (parts.hostname or "").lower()
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ReceiptRefused(
            R_REFUSED_QR,
            "that address's host is not a plain hostname, so where it actually "
            "points cannot be agreed on. Nothing was encoded.")
    hosts = _gateway_hosts()
    if any(host == h or host.endswith("." + h) for h in hosts):
        raise ReceiptRefused(
            R_REFUSED_QR,
            f"this code would point at {host!r}, which is a payment gateway "
            f"host ({', '.join(hosts)}). A receipt code opens a bill and never "
            f"asks for money. Nothing was encoded.")
    if parts.netloc.lower() != urlsplit(origin).netloc.lower():
        raise ReceiptRefused(
            R_REFUSED_QR,
            f"this code would point at {parts.netloc!r} and this counter was "
            f"reached at {urlsplit(origin).netloc!r}. A receipt code carries "
            f"this counter's own address and nothing else. Nothing was encoded.")
    return url


def _is_loopback(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in LOOPBACK_HOSTS


# ------------------------------------------------- WAAPSI: the code, read back
#
# A customer bringing a packet back holds up two things: the packet and the
# receipt they photographed off this counter. The receipt QR carries THIS
# server's own `/receipt/{session_id}/page` address and nothing else — that
# is rule 3 above, enforced by `receipt_url` — so reading the session id back
# out of it is reading this program's own bookmark, not parsing a payment
# payload. Nothing below can be handed a `upi://` string or a gateway host
# and answer with a session: those are refused first, by the same tests the
# encoder ran, and the id is then checked against the chain by the caller.

_RECEIPT_PATH_RE = re.compile(r"^/receipt/([^/]+)(?:/page)?/?$")


def receipt_session_from_payload(payload: Any) -> Optional[str]:
    """The session id a decoded QR names, IF it is one of this counter's own
    receipt codes; None for anything else.

    "One of this counter's own" is decided by SHAPE, not by host: the receipt
    code was printed with whatever address the shopkeeper's browser used to
    reach the till (a LAN IP, a hostname), and the till cannot enumerate the
    addresses it answers on. So the test is: http or https, a plain hostname
    that is NOT a payment gateway's, and the exact path this module prints —
    and then the caller resolves the id against the audit chain, which is
    the real proof that this counter billed it. A code that is not a URL, a
    UPI payload, a gateway link, or a URL to some other path is None here and
    is treated as whatever it was before this function existed.
    """
    if not isinstance(payload, str):
        return None
    s = payload.strip()
    if not s or _looks_like_upi(s):
        return None
    try:
        parts = urlsplit(s)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    if any(host == h or host.endswith("." + h) for h in _gateway_hosts()):
        return None
    m = _RECEIPT_PATH_RE.match(parts.path or "")
    if not m:
        return None
    from urllib.parse import unquote

    session_id = unquote(m.group(1))
    if not SESSION_ID_RE.match(session_id):
        return None
    return session_id


def refunds_from(records: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """session_id -> {refund_key -> the refund's LAST state}, off the chain.

    Folded from the kernel's own `refund.*` lines — the one writer of
    results/audit.jsonl — the same way `manage.bills_from` folds bills, so a
    receipt, the returns screen and the loyalty ledger all read one story.
    The last line about a refund wins; `processed_at` is stamped by the
    `refund.processed` line and by nothing else, because only a signed
    callback writes that line.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for rec in records:
        if rec.get("module") != "kernel":
            continue
        event = rec.get("event")
        if not isinstance(event, str) or not event.startswith("refund."):
            continue
        key = rec.get("refund_key")
        sid = rec.get("session_id")
        if not isinstance(key, str) or not key or not isinstance(sid, str) or not sid:
            continue
        by_key = out.setdefault(sid, {})
        rf = by_key.get(key)
        if rf is None:
            rf = {
                "refund_key": key,
                "session_id": sid,
                "item_id": rec.get("item_id"),
                "sku_id": rec.get("sku_id"),
                "amount_paise": _int_or_none(rec.get("amount_paise")),
                "payment_id": rec.get("payment_id"),
                "gateway_refund_id": None,
                "state": None,
                "needs_human": False,
                "reason": None,
                "created_at": rec.get("ts"),
                "requested_at": None,
                "processed_at": None,
                "failed_at": None,
                "last_at": rec.get("ts"),
                "last_event": None,
            }
            by_key[key] = rf
        state = rec.get("to_state")
        if isinstance(state, str) and state:
            rf["state"] = state
        gw = rec.get("gateway_refund_id")
        if isinstance(gw, str) and gw:
            rf["gateway_refund_id"] = gw
        rf["needs_human"] = bool(rec.get("needs_human"))
        rf["reason"] = rec.get("reason")
        rf["last_at"] = rec.get("ts")
        rf["last_event"] = event
        if event == "refund.requested":
            rf["requested_at"] = rec.get("ts")
        elif event == "refund.processed":
            rf["processed_at"] = rec.get("ts")
        elif event == "refund.failed":
            rf["failed_at"] = rec.get("ts")
    for by_key in out.values():
        for rf in by_key.values():
            amt = rf["amount_paise"]
            rf["amount_rupees"] = None if amt is None else _rupees(amt)
            rf["refunded"] = rf["state"] == "PROCESSED"
            rf["committed"] = rf["state"] in ("NEW", "CALLING", "REQUESTED",
                                              "INDETERMINATE", "PROCESSED")
    return out


# --------------------------------------------------------- the printed page --

#: Self-contained by requirement, not by preference: the Content-Security-Policy
#: this server sends is `default-src 'self'; script-src 'self'`, so an external
#: stylesheet, a web font or a CDN script would be blocked by the browser and
#: the page would silently lose its layout. There is no <script> of any kind
#: here — the page needs none, and `script-src 'self'` forbids an inline one
#: anyway. `style-src` permits 'unsafe-inline', which is what makes the one
#: <style> block below legal.
_PAGE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 16px;
  background: #f4f2ee; color: #17150f;
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
}
.sheet {
  max-width: 420px; margin: 0 auto; background: #fff;
  border: 1px solid #d9d3c7; border-radius: 4px; padding: 22px 22px 18px;
}
h1 { margin: 0 0 2px; font-size: 19px; letter-spacing: -0.01em; }
.muted { color: #6a6357; }
.small { font-size: 12px; }
.rule { border: 0; border-top: 1px solid #e2ddd2; margin: 14px 0; }
.rule.strong { border-top: 2px solid #17150f; margin: 10px 0 8px; }
.banner {
  margin: 14px 0; padding: 10px 12px; border-radius: 3px;
  border: 1px solid; font-weight: 600;
}
.banner .why { display: block; margin-top: 4px; font-weight: 400; font-size: 12px; }
.banner.unpaid { border-color: #a8341f; background: #fbeeea; color: #7d2415; }
.banner.paid { border-color: #1d6b3f; background: #ecf6ef; color: #14532b; }
.banner.qualified { border-color: #8a6a12; background: #fbf4e3; color: #6b520c; }
.banner.chain { border-color: #8a6a12; background: #fbf4e3; color: #6b520c;
                font-weight: 400; }
table { width: 100%; border-collapse: collapse; }
td { padding: 4px 0; vertical-align: top; }
td.qty { width: 34px; color: #6a6357; font-variant-numeric: tabular-nums; }
td.amt { text-align: right; white-space: nowrap;
         font-variant-numeric: tabular-nums; }
tr.excluded td { color: #7d2415; }
.total { display: flex; justify-content: space-between; align-items: baseline;
         font-size: 21px; font-weight: 700; }
.total .paise { font-size: 11px; font-weight: 400; color: #6a6357; }
.qr { text-align: center; margin: 16px 0 6px; }
.qr img { width: 180px; height: 180px; image-rendering: pixelated; }
.notes { margin: 12px 0 0; padding-left: 18px; }
.notes li { margin: 5px 0; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: 12px; word-break: break-all; }
@media print {
  body { background: #fff; padding: 0; }
  .sheet { border: 0; max-width: none; }
  .noprint { display: none; }
}
"""


def _money_cell(rupees: Optional[str]) -> str:
    return "&mdash;" if rupees is None else f"&#8377;{escape(rupees)}"


def render_page(rec: dict[str, Any], *, qr_path: Optional[str],
                qr_problem: Optional[str], reachable: Optional[bool]) -> str:
    """The receipt as one self-contained HTML document.

    EVERYTHING FROM DISK IS ESCAPED. A product name, a shop name and a chain
    error message all come out of files a person can edit, and one of them
    containing '<' must render as text on this page rather than as markup.
    """
    shop = rec["shop"]
    # An unnamed shop is stated, and stated in the muted voice: rendering the
    # sentence in the same weight as a signboard makes it read as the name.
    named = bool(shop.get("name"))
    name = shop.get("name") or "This shop has not been named yet"
    state = rec["payment_state"]
    banner_class = {"paid": "paid",
                    "recorded_paid_by_the_counter": "qualified"}.get(
                        state, "unpaid")

    rows: list[str] = []
    for line in rec["lines"]:
        label = escape(str(line["name"]))
        if not line["named_from_catalogue"]:
            label += ' <span class="muted small">(no longer in the catalogue)</span>'
        unit_note = ""
        if line["qty"] > 1 and line["unit_rupees"] is not None:
            unit_note = (f'<div class="muted small">{line["qty"]} &times; '
                         f'&#8377;{escape(line["unit_rupees"])}</div>')
        rows.append(
            f'<tr><td class="qty">{line["qty"]}</td>'
            f'<td>{label}{unit_note}</td>'
            f'<td class="amt">{_money_cell(line["line_rupees"])}</td></tr>')
    if not rows:
        rows.append('<tr><td colspan="3" class="muted">This bill has no priced '
                    'lines on the audit chain.</td></tr>')

    excluded_rows = ""
    if rec["excluded"]:
        cells = "".join(
            f'<tr class="excluded"><td class="qty">1</td>'
            f'<td>{escape(str(item["name"]))}'
            f'<div class="muted small">{escape(str(item["why"]))}</div></td>'
            f'<td class="amt">not charged</td></tr>'
            for item in rec["excluded"])
        excluded_rows = (
            '<hr class="rule">'
            '<p class="small muted">Seen on the counter and left off this bill '
            'because the counter would not guess what they were:</p>'
            f'<table>{cells}</table>')

    qr_block = ""
    if qr_path:
        note = ("Photograph this code to keep the bill on your own phone."
                if reachable else
                "This code carries a loopback address, which points at "
                "whatever device opens it. A phone scanning it will try to "
                "reach itself. Open this counter at its address on the shop's "
                "network to print a code a phone can follow.")
        qr_block = (
            f'<div class="qr"><img src="{escape(qr_path, quote=True)}" '
            f'alt="A QR code carrying the address of this receipt"></div>'
            f'<p class="small muted" style="text-align:center">{escape(note)}</p>')
    elif qr_problem:
        qr_block = f'<p class="small muted">{escape(qr_problem)}</p>'

    chain_banner = ""
    if not rec["chain"]["ok"]:
        chain_banner = (
            '<div class="banner chain">The audit log this bill was rebuilt from '
            'does not verify: '
            f'{escape(str(rec["chain"]["error"]))}. This bill is inside the part '
            'that does verify.</div>')

    notes = ""
    if rec["notes"]:
        items = "".join(f"<li>{escape(str(n))}</li>" for n in rec["notes"])
        notes = f'<ul class="notes small muted">{items}</ul>'

    shop_lines = []
    if shop.get("address"):
        shop_lines.append(escape(str(shop["address"])).replace("\n", "<br>"))
    if shop.get("phone"):
        shop_lines.append("Phone " + escape(str(shop["phone"])))
    shop_block = ('<p class="small muted">' + "<br>".join(shop_lines) + "</p>"
                  if shop_lines else "")

    settled_line = ""
    if rec["payment_id"]:
        settled_line += (f'<p class="small muted">Gateway payment id '
                         f'<code>{escape(str(rec["payment_id"]))}</code></p>')

    # WAAPSI. Money that went back, in the same neutral ink as the rest of
    # the sheet: a refund is not a payment and does not get the banner's
    # green. The total is left as billed; the line under it says what came
    # back and on whose word.
    refund_block = ""
    if rec.get("refunded_paise"):
        refund_block = (
            '<div class="total" style="font-weight:400">'
            '<span>Refunded via the gateway</span>'
            f'<span>&#8377;{escape(rec["refunded_rupees"])} of '
            f'&#8377;{escape(rec["total_rupees"])}</span></div>')
    if rec.get("refund_requested_paise"):
        refund_block += (
            f'<p class="small muted">A refund of &#8377;'
            f'{escape(rec["refund_requested_rupees"])} has been asked for and '
            f'is not yet confirmed by the gateway.</p>')

    title = (f"Receipt {rec['session_id']} — "
             f"{'paid' if state == 'paid' else 'not paid'}")

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{_PAGE_CSS}</style>
</head><body>
<main class="sheet">
  <h1{'' if named else ' class="muted"'}>{escape(str(name))}</h1>
  {shop_block}
  <hr class="rule">
  <p class="small muted">Bill <code>{escape(rec['session_id'])}</code><br>
     {escape(str(rec['at_human'] or rec['at'] or 'time not recorded'))}</p>

  <div class="banner {banner_class}">{escape(rec['payment_headline'])}
    <span class="why">{escape(rec['payment_detail'])}</span></div>
  {settled_line}
  {chain_banner}

  <table>{''.join(rows)}</table>
  <hr class="rule strong">
  <div class="total"><span>Total</span>
    <span>&#8377;{escape(rec['total_rupees'])}
      <span class="paise">{rec['total_paise']} paise</span></span></div>
  {refund_block}
  {excluded_rows}
  {qr_block}
  <hr class="rule">
  {notes}
  <p class="small muted">{escape(rec['derived_from'])}</p>
</main>
</body></html>
"""


# ----------------------------------------------------------------- routes --


@router.get("/receipt/{session_id}")
def receipt_json_ep(session_id: str) -> JSONResponse:
    """One bill as JSON, rebuilt from the audit chain.

    Integer paise in `total_paise`; the rupee string beside it is for printing
    and is derived from the integer, never the other way round.
    """
    try:
        return JSONResponse(build_receipt(session_id))
    except ReceiptRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(ReceiptRefused(
            R_INTERNAL,
            f"a figure on this bill is not integer paise ({exc}). The receipt "
            f"is not printed rather than printed with a number that cannot be "
            f"exact."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/receipt/{session_id}/page")
def receipt_page_ep(session_id: str, request: Request):
    """The same bill as a printable page, self-contained down to the last byte.

    A REFUSAL IS STILL JSON. The page and the JSON route refuse identically, so
    a screen that opens this in a tab shows the same reason string it would have
    got from the API rather than an HTML error nobody parsed.

    A bad Host header costs the page its QR and nothing else: the address of
    this server is needed to draw a code, and not to print a bill.
    """
    try:
        rec = build_receipt(session_id)
        qr_path: Optional[str] = None
        qr_problem: Optional[str] = None
        reachable: Optional[bool] = None
        try:
            url = receipt_url(request, rec["session_id"])
            reachable = not _is_loopback(url)
            qr_path = f"/receipt/{quote(rec['session_id'], safe='')}/qr"
        except ReceiptRefused as exc:
            qr_problem = (f"No code is shown on this bill: {exc.detail} The "
                          f"figures above are unaffected.")
        html = render_page(rec, qr_path=qr_path, qr_problem=qr_problem,
                           reachable=reachable)
        return Response(html, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    except ReceiptRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/receipt/{session_id}/qr")
def receipt_qr_ep(session_id: str, request: Request,
                  px: int = DEFAULT_QR_PX):
    """A QR carrying the address of THIS receipt page on THIS server.

    The bill is resolved first and the code is refused if there is none: a QR
    that opens a 404 is worse than no QR, because the customer only finds out
    after they have walked out of the shop.
    """
    try:
        rec = build_receipt(session_id)
        url = receipt_url(request, rec["session_id"])

        try:
            import cv2
            import numpy as np
        except Exception as exc:  # noqa: BLE001 - a missing library is a state
            raise ReceiptRefused(
                R_NO_ENCODER,
                f"this counter cannot draw a QR ({type(exc).__name__}: {exc}). "
                f"The receipt itself is at {page_path(rec['session_id'])} and "
                f"is unaffected.") from None

        enc = cv2.QRCodeEncoder.create()
        q = enc.encode(url)
        q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
        side = max(MIN_QR_PX, min(int(px), MAX_QR_PX))
        q = cv2.resize(q, (side, side), interpolation=cv2.INTER_NEAREST)
        # A quiet zone. Without it a scanner cannot find the finder patterns
        # against whatever is behind the screen.
        pad = side // 14
        card = np.full((side + 2 * pad, side + 2 * pad), 255, np.uint8)
        card[pad:pad + side, pad:pad + side] = q
        ok, buf = cv2.imencode(".png", cv2.cvtColor(card, cv2.COLOR_GRAY2BGR))
        if not ok:
            raise ReceiptRefused(R_NO_ENCODER, "the code would not encode.")
        return Response(buf.tobytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "X-Gawaah-Receipt-Url": url,
                                 "Content-Disposition":
                                     'inline; filename="gawaah_receipt_qr.png"'})
    except ReceiptRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/receipt/{session_id}/link")
def receipt_link_ep(session_id: str, request: Request) -> JSONResponse:
    """The address the receipt code carries, as text.

    Separate from the image for the same reason `/store/link` is: a QR reading
    `http://127.0.0.1:8790` is a perfectly good QR that no phone on earth can
    open, and that failure is silent unless something says it out loud.
    """
    try:
        rec = build_receipt(session_id)
        url = receipt_url(request, rec["session_id"])
        loopback = _is_loopback(url)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "session_id": rec["session_id"],
            "url": url,
            "qr_url": f"/receipt/{quote(rec['session_id'], safe='')}/qr",
            "json_url": f"/receipt/{quote(rec['session_id'], safe='')}",
            "reachable_from_a_phone": not loopback,
            "settled": rec["settled"],
            "settled_by_verified_webhook": rec["settled_by_verified_webhook"],
            "total_paise": rec["total_paise"],
            "note": (
                "This address is the loopback interface, which points at "
                "whatever device opens it. A phone scanning this code will try "
                "to reach itself and fail. Open this counter at its address on "
                "the shop's network and print the code from there."
                if loopback else
                "A phone on the same network can open this address."),
        })
    except ReceiptRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "ReceiptRefused",
    "build_receipt",
    "group_lines",
    "page_path",
    "receipt_session_from_payload",
    "receipt_url",
    "refunds_from",
    "render_page",
    "router",
    "settlement",
    "shop_dir",
    "shop_profile",
]
