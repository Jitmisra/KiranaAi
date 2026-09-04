"""SHARE — putting a bill, an order or a reorder list on WhatsApp.

A kirana runs on WhatsApp. The receipt a customer keeps, the "your order is on
the way" message, the list a shopkeeper sends his wholesaler at nine in the
morning — all of it already happens there, typed by hand, with the numbers
copied off a screen and occasionally copied wrong.

THERE IS NO WHATSAPP BUSINESS API HERE and this module does not pretend to
have one. It cannot send a message. What it does is COMPOSE one — from figures
the other modules already derived — and hand back a `https://wa.me/<phone>?
text=<message>` deep link. Tapping that opens WhatsApp on any phone with the
message already typed and the right contact already chosen; the person still
presses send. That is the whole mechanism, and saying so plainly matters more
than the feature does: a shopkeeper who believes this counter sent a receipt
that it did not send will find out from an angry customer.

Because nothing is sent, NOTHING IS LOGGED. This module appends no ledger line
and writes no file. A line saying "receipt shared" would be a claim about an
act this server cannot observe — the shopkeeper may close WhatsApp without
pressing send — and inventing it would break the one rule the product is built
on. Every response says so in its own words.

Five rules this file exists to keep, in the order they would hurt if broken:

  1. NO PAYMENT PAYLOAD, EVER. `wa.me` is the only external host this program
     links to, and the message that rides on it is checked before it goes:
     no `upi:` payload, no gateway hostname, and no URL other than the receipt
     page this module itself derived. This is not decorative. A storefront
     order carries a customer NAME typed on the open internet, and that name
     is printed into the message a shopkeeper is about to forward — "Rekha" is
     a name and so is `upi://pay?pa=thief@bank&am=500`. `_check_message` is
     the line that stops the second one, and it is tested.

  2. INTEGER PAISE. Every amount here is read as an int and rendered by
     `gawaah/money.py`. No float, no division, no rounding. Where a figure is
     absent on the record it is printed as absent, never as zero.

  3. NOTHING IS RE-DERIVED. The bill comes from `gawaah/receipts.py`, which
     rebuilds it from the hash chain. The order comes from
     `gawaah/storefront.py`, which owns the order document. The low-stock list
     comes from `gawaah/stock.py`'s own endpoint, called as a function so the
     two lists cannot drift. This module adds sentences and nothing else.

  4. A REFUSAL IS A RESULT. Every failure has a name and the name is in the
     body, with a 400. Nothing here raises a 500.

  5. THE BROWSER IS NEVER AN AUTHOR. The page sends a session id, an order id
     and a phone number. It cannot send a line, a price, a total or a message.
     Everything printed below is composed here from what the server already
     knew.

The router carries NO prefix: the paths are already absolute. Mount it with
`app.include_router(share.router)`.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import receipts
from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, named for the
# STATE and not for the fix. The sentence saying what to do about it goes in
# `detail`. Three of them deliberately reuse strings that already exist
# elsewhere in this program: a screen showing one message per state should not
# have to learn a second name for the same state depending on which route
# answered it.

R_BAD_BODY = "share_body_not_json"
R_MODULE_UNAVAILABLE = "a_module_this_needs_is_unavailable"

R_PHONE_MISSING = "whatsapp_phone_missing"
R_PHONE_NOT_TEXT = "whatsapp_phone_not_text"
R_PHONE_NOT_A_NUMBER = "whatsapp_phone_not_a_number"
R_PHONE_TOO_SHORT = "whatsapp_phone_too_short"
R_PHONE_TOO_LONG = "whatsapp_phone_too_long"
R_PHONE_NOT_INDIA = "whatsapp_phone_country_code_not_india"
R_PHONE_NOT_A_MOBILE = "whatsapp_phone_not_an_indian_mobile"

R_REFUSED_MESSAGE = "refused_to_share_this_message"
R_REFUSED_LINK = "refused_to_share_this_link"
R_TOO_LONG = "message_too_long_to_send"

R_NOTHING_IS_LOW = "nothing_is_low_on_stock"
R_BAD_SUPPLIER_ID = "supplier_id_malformed"      # as gawaah/purchases.py names it
R_NO_SUPPLIER = "no_such_supplier"               # as gawaah/purchases.py names it

R_INTERNAL = "share_internal_error"


#: The one external host this product links to. Checked the way
#: `tools/upload_app.py`'s `/qr/link` checks a payable link, and for the same
#: reason: the thing a person taps without reading is the thing that gets an
#: allowlist. `api.whatsapp.com` is deliberately NOT here — one host is enough,
#: and every host on this list is a host somebody has to keep thinking about.
SHARE_HOSTS = ("wa.me",)

#: A deep link is a URL, and a URL that is too long is silently truncated by
#: whatever hands it to the phone — Android's intent parser, an SMS field, a
#: chat client that shortened it. A truncated bill is a wrong bill, so the
#: message is capped and the cap is a refusal rather than a trim. 1600
#: characters of message is roughly 4-5 KB once percent-encoded, which every
#: platform measured carries intact.
MAX_MESSAGE_CHARS = 1600

#: How many product lines go INTO the message. Past this the message says how
#: many were left out and leans on the receipt page, which carries all of them.
#: A forty-line bill pasted into WhatsApp is not read by anybody.
MAX_LINES_IN_A_MESSAGE = 20

#: A phone number a person typed. Longer than this is not a mis-typed number,
#: it is a paste of something else.
MAX_PHONE_CHARS = 32

#: Punctuation people put in phone numbers. Stripped before the digits are
#: read; anything left that is not an ASCII digit is refused by name.
_PHONE_PUNCT = re.compile(r"[\s\-(). ]")

#: ASCII digits ONLY, and `str.isdigit()` will not do: '٩' and '९' are digits
#: to Python and are not digits to E.164. A number pasted from an Indian
#: keyboard in Devanagari numerals must be refused by name rather than turned
#: into a `wa.me` link that opens a chat with nobody.
_ASCII_DIGITS = re.compile(r"[0-9]+")

#: `scheme://…` anywhere in a composed message. Used to find URLs that arrived
#: through a name typed by a customer, not to parse them.
_SCHEME_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s<>\"']+")

#: An Indian mobile number begins with one of these. A number beginning 0-5 is
#: a landline, a special service, or a typo, and WhatsApp has no account on it.
MOBILE_FIRST_DIGITS = "6789"


class ShareRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: ShareRefused) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False},
        status_code=exc.status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# ------------------------------------------------------- the other modules --


def _module(name: str) -> Any:
    """A sibling module, imported LATE, with a named refusal if it will not.

    Late for two reasons. `gawaah/storefront.py` and `gawaah/purchases.py`
    reach the till, and the till mounts this router — importing either at
    module scope is a cycle. And a counter is a live system under active
    change: if a sibling module fails to import, that must cost the shopkeeper
    the share button and nothing else, rather than taking the whole till down
    at start-up because one screen could not be built.
    """
    from importlib import import_module

    try:
        return import_module(f".{name}", __package__)
    except Exception as exc:  # noqa: BLE001 - a missing module is a named answer
        raise ShareRefused(
            R_MODULE_UNAVAILABLE,
            f"gawaah/{name}.py is not importable ({type(exc).__name__}: "
            f"{exc}), and this message is composed from figures that module "
            f"owns. Nothing was composed — the numbers are not guessed at "
            f"here.") from None


async def _json_body(request: Request) -> dict[str, Any]:
    """The body, or a named refusal. An empty body is an empty object.

    A POST with no body at all is how a page asks 'use the number you already
    have on file', which is a real and useful request — so it is not an error.
    """
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise ShareRefused(
            R_BAD_BODY,
            'the body of this request is not JSON. It should look like '
            '{"phone": "9876543210"}.') from None
    if not isinstance(body, dict):
        raise ShareRefused(
            R_BAD_BODY,
            f"the body of this request is a {type(body).__name__}; it must be "
            f'a JSON object like {{"phone": "9876543210"}}.')
    return body


# ----------------------------------------------------------- the phone --


def to_e164(raw: Any, *, whose: str = "this number") -> str:
    """An Indian mobile number as E.164 (`+919876543210`), or a refusal.

    Four shapes are accepted because all four are what people actually have in
    their contacts: a bare ten digits, `+91` and ten, `91` and ten, and a
    trunk-prefixed `0` and ten. Everything else is refused BY NAME rather than
    coerced, because the failure mode of a coerced number is a WhatsApp chat
    opened with a stranger, or with nobody, and the shopkeeper finds out when
    the customer says they never got the bill.

    A STATED LIMIT, and it is a real one: `08023456789` is eleven digits
    beginning with a zero. Read as a trunk-prefixed mobile that is
    `+918023456789`; read as a landline it is Bangalore's 080 followed by an
    eight-digit local number, and NOTHING IN THE DIGITS DISTINGUISHES THE TWO.
    This function reads it as a mobile. Being wrong that way costs a "this
    number is not on WhatsApp" on the shopkeeper's own screen, which he can
    see and fix; refusing it outright would block a large number of perfectly
    good mobiles that people store with the leading zero.

    Country code: only +91. This counter composes messages for a shop in
    India, and a number in another country is refused with its own name rather
    than silently prefixed with 91 — which would produce a plausible-looking
    Indian number belonging to someone else entirely.
    """
    if raw is None:
        raise ShareRefused(
            R_PHONE_MISSING,
            f"no phone number was given for {whose}, so there is nobody to "
            f"open WhatsApp with. Nothing was composed.")
    if not isinstance(raw, str):
        raise ShareRefused(
            R_PHONE_NOT_TEXT,
            f"a phone number must be text, not {type(raw).__name__}. A number "
            f"sent as a JSON number loses its leading zero before it ever "
            f"arrives here.")
    s = raw.strip()
    if not s:
        raise ShareRefused(
            R_PHONE_MISSING,
            f"the phone number for {whose} is empty. Nothing was composed.")
    if len(s) > MAX_PHONE_CHARS:
        raise ShareRefused(
            R_PHONE_TOO_LONG,
            f"that is {len(s)} characters long and a phone number is not. The "
            f"cap here is {MAX_PHONE_CHARS}. Nothing was composed.")

    s = _PHONE_PUNCT.sub("", s)
    plus = s.startswith("+")
    if plus:
        s = s[1:]
    if not _ASCII_DIGITS.fullmatch(s):
        junk = "".join(sorted({c for c in s if not c.isascii() or not c.isdigit()}))
        raise ShareRefused(
            R_PHONE_NOT_A_NUMBER,
            f"{raw!r} is not a phone number this counter can dial. After "
            f"spaces, brackets and dashes were removed it still contains "
            f"{junk!r}. Only the digits 0 to 9 and a leading + are a number.")

    if plus:
        if not s.startswith("91"):
            raise ShareRefused(
                R_PHONE_NOT_INDIA,
                f"{raw!r} carries a country code that is not India's. This "
                f"counter composes messages for Indian mobiles (+91) and will "
                f"not put a 91 in front of a number that already belongs "
                f"somewhere else.")
        rest = s[2:]
    elif len(s) == 12 and s.startswith("91"):
        rest = s[2:]
    elif len(s) == 11 and s.startswith("0"):
        rest = s[1:]
    else:
        rest = s

    if len(rest) < 10:
        raise ShareRefused(
            R_PHONE_TOO_SHORT,
            f"{raw!r} comes to {len(rest)} digits. An Indian mobile is ten. "
            f"Nothing was composed.")
    if len(rest) > 10:
        raise ShareRefused(
            R_PHONE_TOO_LONG,
            f"{raw!r} comes to {len(rest)} digits. An Indian mobile is ten. "
            f"Nothing was composed.")
    if rest[0] not in MOBILE_FIRST_DIGITS:
        raise ShareRefused(
            R_PHONE_NOT_A_MOBILE,
            f"{raw!r} starts with {rest[0]!r}. An Indian mobile starts with "
            f"6, 7, 8 or 9 — a number starting with anything else is a "
            f"landline or a service number, and WhatsApp has no account on "
            f"it.")
    return "+91" + rest


def display_phone(e164: str) -> str:
    """`+919876543210` as `+91 98765 43210`, for a screen to show back.

    Shown so the shopkeeper can check the number he typed was read the way he
    meant it — the whole failure this function guards against is a message
    that opens on the wrong contact.
    """
    rest = e164[3:]
    return f"+91 {rest[:5]} {rest[5:]}"


# ---------------------------------------------------------- the message --


def _rupees(value: int) -> str:
    """Integer paise as `₹21.45`. `paise()` refuses a float or a bool."""
    return "₹" + to_rupees_str(int(paise(value)))


def _items(n: int) -> str:
    """'1 item' / '3 items'. A message reading '1 item(s)' was written for a
    machine and is being read by somebody who just paid for something."""
    return f"{int(n)} item" if int(n) == 1 else f"{int(n)} items"


def _units(n: int) -> str:
    return f"{int(n)} packet" if int(n) == 1 else f"{int(n)} packets"


def _fold(rows: list[str], what: str, where: str) -> list[str]:
    """At most `MAX_LINES_IN_A_MESSAGE` rows, and a sentence if some were cut.

    The sentence is NOT optional, and `where` — the place the rest can be read
    — is not optional either. A message that quietly showed twenty of forty
    lines would be a short bill nobody could see was short, which is the exact
    failure this program refuses everywhere else.
    """
    if len(rows) <= MAX_LINES_IN_A_MESSAGE:
        return rows
    kept = rows[:MAX_LINES_IN_A_MESSAGE]
    left = len(rows) - MAX_LINES_IN_A_MESSAGE
    kept.append(f"and {left} more {what} — {where}")
    return kept


def _check_message(text: str, allowed_urls: tuple[str, ...]) -> None:
    """Refuse to hand over a message carrying anything payable. INVARIANT 6.

    THIS IS THE LOAD-BEARING FUNCTION IN THE FILE. Three of the things printed
    into these messages — a customer's name, a supplier's name, a product name
    — come out of a file a person can write, and one of those people is
    whoever scanned the shutter QR from the open internet. A storefront order
    placed under the name `upi://pay?pa=thief@upi&am=500` produces a message
    that a shopkeeper forwards to a customer with a live payment request in
    it, in the shop's own voice. Nothing else in this program would catch it,
    because nothing else in this program prints a customer's name into a
    string that a phone will act on.

    So: no `upi:` token anywhere, no URL that this module did not itself put
    there, and no gateway hostname even without a scheme — WhatsApp linkifies
    a bare `rzp.io/l/abc` perfectly well.

    `_looks_like_upi` is checked per whitespace-token and not on the whole
    string, because the till's own version only looks at the START of what it
    is given: `_looks_like_upi("Rekha upi://pay")` is False and the payload is
    still in the message.
    """
    for token in text.split():
        if receipts._looks_like_upi(token):
            raise ShareRefused(
                R_REFUSED_MESSAGE,
                f"this message contains {token[:40]!r}, which reads as a UPI "
                f"payment payload. This shop sends bills and order notes over "
                f"WhatsApp and never a payment target it composed. Nothing "
                f"was composed.")

    for match in _SCHEME_URL.finditer(text):
        url = match.group(0)
        if url not in allowed_urls:
            raise ShareRefused(
                R_REFUSED_MESSAGE,
                f"this message contains the address {url[:60]!r}, which this "
                f"counter did not put there. A shared message carries this "
                f"shop's own receipt page and nothing else. Nothing was "
                f"composed.")

    low = text.lower()
    for host in receipts._gateway_hosts():
        if host.lower() in low:
            raise ShareRefused(
                R_REFUSED_MESSAGE,
                f"this message mentions {host!r}, a payment gateway host. A "
                f"message from this shop never carries a payment link, "
                f"whether or not it was typed as one. Nothing was composed.")

    if len(text) > MAX_MESSAGE_CHARS:
        raise ShareRefused(
            R_TOO_LONG,
            f"this message is {len(text)} characters and a deep link carries "
            f"{MAX_MESSAGE_CHARS} before it starts being truncated by "
            f"whatever hands it to the phone. A truncated bill is a wrong "
            f"bill, so nothing was composed. Send the receipt page address "
            f"instead.")


def wa_url(e164: str, message: str, allowed_urls: tuple[str, ...] = ()) -> str:
    """The `wa.me` deep link, checked as hard as a payable link is.

    `wa.me` wants the number as bare digits with no `+`. The message is
    percent-encoded whole — `safe=''` so that a `&`, a `#` or a `/` inside a
    product name cannot end the query string early and lop the rest of the
    bill off.

    The finished URL then goes back through the SAME checks
    `tools/upload_app.py`'s `/qr/link` runs before it encodes a QR: not a UPI
    payload, http or https, a host made of nothing but hostname characters,
    and a host on the allowlist. None of them can fire on the code as written
    — every piece of this string is built here. They are here because the day
    somebody makes the host configurable is the day the guard has to already
    be in place, which is the argument `storefront.store_qr_ep` makes for the
    same two checks and is why nothing in this program encodes a string it did
    not build.
    """
    _check_message(message, allowed_urls)
    url = f"https://wa.me/{e164.lstrip('+')}?text={quote(message, safe='')}"

    if receipts._looks_like_upi(url):
        raise ShareRefused(
            R_REFUSED_LINK,
            "that string reads as a UPI payload. This link opens a chat; it "
            "does not carry money. Nothing was composed.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ShareRefused(
            R_REFUSED_LINK,
            f"a shareable link must be http or https, not {parts.scheme!r}. "
            f"Nothing was composed.")
    host = (parts.hostname or "").lower()
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ShareRefused(
            R_REFUSED_LINK,
            "that link's host is not a plain hostname, so where it actually "
            "points cannot be agreed on. Nothing was composed.")
    if not any(host == h or host.endswith("." + h) for h in SHARE_HOSTS):
        raise ShareRefused(
            R_REFUSED_LINK,
            f"that link points at {host!r}. The only outside address this "
            f"shop links to is {', '.join(SHARE_HOSTS)}. Nothing was "
            f"composed.")
    return url


#: What every response says about what just happened, because what just
#: happened is easy to misread: a link was composed, and no message was sent.
SENT_NOTHING = (
    "Nothing has been sent. This is a link that opens WhatsApp with the "
    "message already typed — the person holding the phone still presses send. "
    "This counter cannot see whether they did, so it does not record that "
    "they did.")


# -------------------------------------------------------------- a receipt --


#: `receipts.settlement()` publishes three payment states and no more. Each is
#: given one plain sentence here. A state this map does not know falls through
#: to the module's own headline rather than to silence — a receipt that said
#: nothing about payment would be read as "paid".
_PAID_SENTENCE = {
    "paid": "Paid. The payment gateway's own confirmation reached this "
            "counter.",
    "recorded_paid_by_the_counter":
        "Paid — recorded by this counter. The gateway's own confirmation is "
        "not on the record beside it, so please check with the shop if this "
        "matters.",
    "unpaid": "Not paid. This is a record of what was billed.",
}


def compose_receipt(rec: dict[str, Any], url: Optional[str]
                    ) -> tuple[str, tuple[str, ...]]:
    """The message for one bill, and the URLs it deliberately contains.

    `rec` is `receipts.build_receipt()` verbatim — every figure below was
    derived there from the hash chain and none is computed here. `url` is
    `receipts.receipt_url()`, or None when this counter is only reachable on
    loopback and the address would open on the customer's own phone.
    """
    shop = rec.get("shop") or {}
    name = shop.get("name")
    head = name if isinstance(name, str) and name else "Your bill"

    out: list[str] = [head]
    when = rec.get("at_human")
    if isinstance(when, str) and when:
        out.append(when)
    out.append("")

    rows: list[str] = []
    for line in rec.get("lines") or []:
        label = str(line.get("name") or line.get("sku_id") or "item")
        qty = int(line.get("qty") or 0)
        cell = line.get("line_paise")
        # An unpriced packet keeps its absence. A zero here would be this
        # counter inventing a free packet on a customer's receipt.
        money = ("no price on the record" if cell is None
                 else _rupees(int(cell)))
        rows.append(f"{label} x{qty} — {money}")
    out.extend(_fold(rows, "items", "the full bill is on the page below"))

    out.append("")
    out.append(f"Total: {_rupees(int(rec.get('total_paise') or 0))}")
    out.append(_PAID_SENTENCE.get(str(rec.get("payment_state")),
                                  str(rec.get("payment_headline") or "")))

    # The three ways a bill can be less than it looks. Each is on the receipt
    # page in full; each gets one sentence here, because a customer who reads
    # only the message must not be the last to know.
    excluded = int(rec.get("excluded_count") or 0)
    if excluded:
        out.append(
            f"{_items(excluded)} could not be identified at the counter and "
            f"{'was' if excluded == 1 else 'were'} left off this bill. "
            f"{'It was' if excluded == 1 else 'They were'} not charged for.")
    unpriced = int(rec.get("unpriced_items") or 0)
    if unpriced:
        out.append(
            f"{_items(unpriced)} on this bill "
            f"{'carries' if unpriced == 1 else 'carry'} no price on the "
            f"record and {'is' if unpriced == 1 else 'are'} shown without "
            f"one.")
    if not rec.get("total_agrees"):
        out.append(
            "The lines above and the recorded total do not agree. Both "
            "figures are on the full bill, neither adjusted to match the "
            "other.")

    urls: tuple[str, ...] = ()
    if url:
        out.append("")
        out.append(f"Full bill: {url}")
        urls = (url,)
    return "\n".join(out).strip(), urls


def _receipt_material(request: Request, session_id: str
                      ) -> tuple[dict[str, Any], str, tuple[str, ...],
                                 Optional[str]]:
    """The bill, the message, its URLs, and why the address was left out.

    A bad Host header, or a counter reached only on loopback, costs the
    message its link and NOTHING ELSE. The figures on a bill do not depend on
    knowing this server's address, and refusing the whole message because a QR
    could not be drawn would be the tail wagging the dog.
    """
    rec = receipts.build_receipt(session_id)
    url: Optional[str] = None
    problem: Optional[str] = None
    try:
        candidate = receipts.receipt_url(request, rec["session_id"])
        if receipts._is_loopback(candidate):
            problem = (
                f"The address this counter was reached on ({candidate}) is "
                f"the loopback interface, which points at whatever device "
                f"opens it. A customer tapping it would reach their own "
                f"phone, so it is not in the message. Open this counter at "
                f"its address on the shop's network and share again.")
        else:
            url = candidate
    except receipts.ReceiptRefused as exc:
        problem = (f"No receipt address is in this message: {exc.detail} The "
                   f"figures are unaffected.")
    message, urls = compose_receipt(rec, url)
    return rec, message, urls, problem


# ----------------------------------------------------------- an order --


#: The storefront's five statuses, each in one sentence a customer can read.
#: Keyed off `storefront.STATUSES`; a status this map does not know is printed
#: as itself rather than dropped, so a new state added upstream shows up as
#: something to fix instead of as a message that says nothing.
_STATUS_SENTENCE = {
    "new": "The shop has your order.",
    "preparing": "The shop is putting your order together.",
    "out_for_delivery": "Your order is on the way.",
    "delivered": "Your order was delivered.",
    "cancelled": "This order was cancelled.",
}


def compose_order(doc: dict[str, Any], shop: dict[str, Any]) -> str:
    """The message for one storefront order. No payment link — see below.

    THE GATEWAY LINK ON THIS ORDER IS NOT PUT IN THE MESSAGE, and that is a
    decision rather than an omission. `doc['payment']['short_url']` is a real
    payable link the gateway minted, and forwarding a payment link through a
    chat app is precisely the shape of every UPI fraud a kirana customer has
    been warned about — a message they cannot tell from a fake one. The
    customer pays on the shop's own page, which they reached by scanning the
    shutter code themselves. `_check_message` enforces this independently: a
    gateway host anywhere in the text is refused.
    """
    name = shop.get("name")
    out: list[str] = [name if isinstance(name, str) and name else "Your order"]

    customer = doc.get("customer") or {}
    who = customer.get("name")
    if isinstance(who, str) and who:
        out.append(f"For {who}")
    out.append(f"Order {doc.get('order_id')}")
    out.append("")

    rows: list[str] = []
    for line in doc.get("lines") or []:
        label = str(line.get("name") or line.get("sku_id") or "item")
        qty = int(line.get("qty") or 0)
        rows.append(f"{label} x{qty} — {_rupees(int(line.get('line_paise') or 0))}")
    out.extend(_fold(rows, "items", "ask the shop for the rest"))

    out.append("")
    out.append(f"Total: {_rupees(int(doc.get('total_paise') or 0))}")

    status = str(doc.get("status") or "")
    out.append(_STATUS_SENTENCE.get(status, f"Status: {status}"))

    pay = doc.get("payment") or {}
    if pay.get("paid"):
        out.append("This order is paid.")
    else:
        out.append(
            "Nothing has been charged for this order yet. Pay on the shop "
            "page you ordered from, or at the door.")
    return "\n".join(out).strip()


def _order_doc(order_id: str) -> dict[str, Any]:
    """One order, read by the module that owns it.

    `storefront._read_order` is private and is used anyway, deliberately. It
    is the one reader of an order document: it validates the id against a
    charset BEFORE the id is joined to a path, it resolves the directory
    through the till's `store_dir()` so `GAWAAH_SHOP_DIR` is honoured, and it
    names its own refusals. A second JSON read here would be a second answer
    to "where does an order live", and the day the two disagreed a shopkeeper
    would be looking at an order that the Orders screen says does not exist.
    A test pins the attribute so a rename upstream fails loudly here.
    """
    sf = _module("storefront")
    try:
        return sf._read_order(order_id)
    except sf.StorefrontRefused as exc:
        # The storefront's own words and its own status: 404 for an order that
        # is not there, 400 for an id that is not an id.
        raise ShareRefused(
            exc.reason, exc.detail,
            status=404 if exc.reason == sf.R_NO_ORDER else 400) from None


# ------------------------------------------------------- a reorder list --


def compose_reorder(low: dict[str, Any], shop: dict[str, Any],
                    supplier: Optional[dict[str, Any]]) -> str:
    """The message a shopkeeper sends his wholesaler. Packets, never money.

    `gawaah/stock.py` holds no money and neither does this. A valuation of
    what is on the shelf would be an arithmetic claim about a cost this
    counter does not know, and a purchase order quoting a price the wholesaler
    never agreed is worse than one that quotes none.

    THERE IS NO ORDER QUANTITY ON THIS MESSAGE, and its absence is the point.
    Printing one would take a lead time and a case size — how long the
    wholesaler takes, whether Parle-G comes in twelves or in forty-eights —
    and this counter is told neither. `level - on hand` looks like an answer
    and is not one: at exactly the level it comes to nought, which is not what
    anybody means by "reorder", and a shopkeeper who noticed that once would
    stop trusting every other figure on the message.

    So each line carries what IS derived — what is on the shelf, the level the
    shopkeeper himself set, the shortfall between them, and the days of cover
    where there is enough billing history to divide — and the quantity is left
    to the person who knows the case size. That is one word to type per line
    against a message he would otherwise have written from scratch.

    Two other lists ride along and are deliberately not folded into the first:

      - a product with a level that has never been counted. What is on the
        shelf is unknown, so whether it is low cannot be said. It is named.
      - a product whose derived figure is below zero. Stock left without being
        recorded, so the shortfall is not the shortfall. It is named as
        needing a recount.
    """
    name = shop.get("name")
    out: list[str] = [name if isinstance(name, str) and name else "Order"]
    if supplier and supplier.get("name"):
        out.append(f"For {supplier['name']}")
    out.append("Running low:")
    out.append("")

    rows: list[str] = []
    for row in low.get("orderable") or []:
        level = int(row.get("reorder_level"))
        hand = int(row.get("on_hand_units"))
        label = str(row.get("name") or row.get("sku_id") or "item")
        short = level - hand
        gap = "at the level" if short <= 0 else f"short by {short}"
        days = row.get("days_of_cover")
        # `days_of_cover` is absent whenever stock.py could not honestly
        # divide, and it says why in `cover.why`. An absence prints as an
        # absence: "0 days left" and "we cannot tell" are different sentences
        # and only one of them is true.
        cover = ("" if not isinstance(days, int) or isinstance(days, bool)
                 else f", about {days} day{'' if days == 1 else 's'} left")
        rows.append(f"{label} — {_units(hand)} on the shelf, level {level} "
                    f"({gap}{cover})")
    out.extend(_fold(rows, "products",
                     "the Inventory screen at the shop has the rest"))

    out.append("")
    out.append("These are the shelf figures against the levels set at this "
               "shop. How many to send is not on this message: this counter "
               "does not know your case sizes or how long you take, so it "
               "does not guess. Please write the quantity against each line.")

    unknown = low.get("unknown") or []
    if unknown:
        names = ", ".join(str(r.get("name") or r.get("sku_id"))
                          for r in unknown[:6])
        more = "" if len(unknown) <= 6 else f" and {len(unknown) - 6} more"
        out.append(
            f"Not on this list because the shelf has never been counted, so "
            f"how many are there cannot be said: {names}{more}.")

    recount = low.get("needs_recount") or []
    if recount:
        names = ", ".join(str(r.get("name") or r.get("sku_id"))
                          for r in recount[:6])
        more = "" if len(recount) <= 6 else f" and {len(recount) - 6} more"
        out.append(
            f"Left off because the counter's figure for them is below zero "
            f"and needs a recount first: {names}{more}.")
    return "\n".join(out).strip()


def _low_stock() -> dict[str, Any]:
    """The low-stock report, from `stock.py`'s OWN endpoint, called directly.

    Not a re-derivation and not a second filter. `/stock/low` already decides
    what counts as low, what cannot be said, and what needs a recount, and it
    sorts by the worst shortfall. Calling the route function and reading its
    JSON means the message and the Inventory screen cannot disagree about
    which products are short — which they would, the first time somebody
    changed the sort or the threshold in one place.

    Its refusal is passed through verbatim, reason and detail, because the
    reasons it can give ("the movement chain does not verify") are things a
    shopkeeper has to go and look at, not things this module should paraphrase.
    """
    stock = _module("stock")
    resp = stock.stock_low_ep()
    body = json.loads(bytes(resp.body).decode("utf-8"))
    if resp.status_code != 200 or not body.get("ok"):
        raise ShareRefused(
            str(body.get("reason") or "stock_low_unavailable"),
            str(body.get("detail")
                or "the low-stock list could not be derived, so no reorder "
                   "message was composed."),
            status=resp.status_code if resp.status_code >= 400 else 400)
    # A product can be BOTH at-or-under its level and below zero: stock.py
    # reports it on both lists, correctly, because both facts are true. It
    # must not appear as a line on a purchase order, because "short by 12" off
    # a shelf figure of -7 is a number nobody can act on. It is named in the
    # recount sentence instead. Skipping it here is not a re-derivation — the
    # flag is stock.py's own, read off its own row.
    recount = {str(r.get("sku_id")) for r in (body.get("needs_recount") or [])}
    body["orderable"] = [r for r in (body.get("low") or [])
                         if str(r.get("sku_id")) not in recount]

    if not body["orderable"]:
        held = len(body.get("low") or []) - len(body["orderable"])
        raise ShareRefused(
            R_NOTHING_IS_LOW,
            f"There is nothing to put on a purchase order. "
            f"{body.get('skus_with_a_level') or 0} product(s) have a reorder "
            f"level set and {body.get('skus_without_a_level') or 0} do not — "
            f"a product with no level can never appear on this list"
            + (f", and {held} that are under their level have a shelf figure "
               f"below zero and need a recount before anything is ordered "
               f"against them." if held else "."))
    return body


def _supplier(supplier_id: Any) -> Optional[dict[str, Any]]:
    """One supplier from `gawaah/purchases.py`, or None when none was asked for.

    Read through purchases' own loader and its own id check, so a supplier id
    that is not one is refused here the way it is refused there, under the
    same name.
    """
    if supplier_id is None or supplier_id == "":
        return None
    pur = _module("purchases")
    rows = pur._load_suppliers()
    try:
        sid = pur._valid_supplier_id(supplier_id)
    except pur.PurchaseRefused as exc:
        raise ShareRefused(exc.reason, exc.detail) from None
    rec = rows.get(sid)
    if not isinstance(rec, dict):
        raise ShareRefused(
            R_NO_SUPPLIER,
            f"this shop has no supplier {sid}. Nothing was composed.",
            status=404)
    return pur._supplier_view(sid, rec)


# ---------------------------------------------------------------- shaping --


def _shop() -> dict[str, Any]:
    """The shop's own name and address, from receipts' reader. Never invented.

    A shop that has not been named is not an error: the message goes out with
    "Your bill" at the top instead of a signboard the counter made up.
    """
    try:
        return receipts.shop_profile()
    except Exception:  # noqa: BLE001 - an unnamed shop still sends messages
        return {"configured": False, "name": None}


def _composed(kind: str, message: str, urls: tuple[str, ...],
              *, extra: Optional[dict[str, Any]] = None,
              link_problem: Optional[str] = None) -> dict[str, Any]:
    """The preview half of every response: the text and what is in it.

    The message is checked HERE too, and not only inside `wa_url`. A preview
    is what the shopkeeper reads before deciding to send, and handing him a
    UPI payload to look at — even without a link attached — would be handing
    him something to copy by hand.
    """
    _check_message(message, urls)
    out: dict[str, Any] = {
        "ok": True,
        "settles_money": False,
        "kind": kind,
        "message": message,
        "message_chars": len(message),
        "message_cap": MAX_MESSAGE_CHARS,
        "link_included": bool(urls),
        "carries_a_payment_link": False,
        "note": SENT_NOTHING,
    }
    if link_problem:
        out["link_problem"] = link_problem
    if extra:
        out.update(extra)
    return out


def _addressed(base: dict[str, Any], e164: str, message: str,
               urls: tuple[str, ...]) -> dict[str, Any]:
    """The preview, plus the number and the deep link that opens the chat."""
    return {
        **base,
        "to": e164,
        "to_display": display_phone(e164),
        "wa_url": wa_url(e164, message, urls),
        "wa_host": SHARE_HOSTS[0],
    }


def _phone_from(body: dict[str, Any], fallback: Any, *, whose: str,
                fallback_from: str) -> tuple[str, str]:
    """The number to open, and where it came from.

    A phone the page sent wins over the one on file, because a customer
    standing at the counter saying "send it to this number instead" is the
    normal case. Where it came from is reported, so the shopkeeper's screen
    can show whether it used the number he typed or the one on the order.
    """
    if body.get("phone") not in (None, ""):
        return to_e164(body.get("phone"), whose=whose), "the number you typed"
    if fallback in (None, ""):
        raise ShareRefused(
            R_PHONE_MISSING,
            f"no phone number was given for {whose} and there is none on "
            f"{fallback_from}. Nothing was composed.")
    return to_e164(fallback, whose=whose), fallback_from


# ----------------------------------------------------------------- routes --


@router.get("/share/limits")
def share_limits_ep() -> JSONResponse:
    """What this can do and — more usefully — what it cannot.

    Published as an endpoint rather than left in a docstring because the page
    shows it to the shopkeeper. Somebody who thinks this counter sends
    WhatsApp messages by itself will stop checking that they went, and the
    first he hears of it will be a customer who never got a bill.
    """
    return JSONResponse({
        "ok": True,
        "settles_money": False,
        "sends_messages": False,
        "host": SHARE_HOSTS[0],
        "how": ("This counter composes the text and hands back a wa.me deep "
                "link. Tapping it opens WhatsApp with the message typed and "
                "the contact chosen. A person presses send."),
        "why_not_the_api": (
            "The WhatsApp Business API needs an approved business account, "
            "message templates cleared in advance, and a paid provider. This "
            "shop has none of those, so this counter uses the deep link every "
            "phone already supports rather than pretending otherwise."),
        "carries_a_payment_link": False,
        "payment_links_note": (
            "No message from here carries a payment link or a UPI payload, "
            "including on an order that already has one. A payable string "
            "forwarded through a chat app is one a customer cannot tell from "
            "a fake. Customers pay on the shop's own page."),
        "records_what_was_sent": False,
        "records_note": (
            "Nothing is written down when a message is composed. This counter "
            "cannot see whether the shopkeeper pressed send, and a log saying "
            "he did would be a record of something nobody observed."),
        "numbers": {
            "accepts": "Indian mobiles: 9876543210, +919876543210, "
                       "919876543210, 09876543210.",
            "refuses": "Any other country code, a landline, and anything "
                       "that is not ten digits beginning 6, 7, 8 or 9.",
            "stated_limit": (
                "An eleven-digit number beginning with 0 is read as a mobile "
                "with a trunk prefix. A landline written the same way cannot "
                "be told apart from it by its digits, and WhatsApp will say "
                "the number has no account rather than this counter guessing."),
        },
        "message_cap_chars": MAX_MESSAGE_CHARS,
        "lines_in_a_message": MAX_LINES_IN_A_MESSAGE,
    })


@router.get("/share/receipt/{session_id}")
def share_receipt_preview_ep(session_id: str, request: Request) -> JSONResponse:
    """The receipt message this counter would send, with no number attached.

    A GET on purpose, and a phone number on purpose absent: a query string is
    written to every access log between here and the browser, and a customer's
    number does not belong in one. The number goes in the POST body below.
    """
    try:
        rec, message, urls, problem = _receipt_material(request, session_id)
        return JSONResponse(_composed(
            "receipt", message, urls,
            link_problem=problem,
            extra={
                "session_id": rec["session_id"],
                "total_paise": rec["total_paise"],
                "total_rupees": rec["total_rupees"],
                "payment_state": rec["payment_state"],
                "payment_headline": rec["payment_headline"],
                "settled_by_verified_webhook":
                    rec["settled_by_verified_webhook"],
                "excluded_count": rec["excluded_count"],
                "receipt_url": urls[0] if urls else None,
            }))
    except ShareRefused as exc:
        return _refusal(exc)
    except receipts.ReceiptRefused as exc:
        return _refusal(ShareRefused(exc.reason, exc.detail, status=exc.status))
    except MoneyError as exc:
        return _refusal(ShareRefused(
            R_INTERNAL,
            f"a figure on this bill is not integer paise ({exc}). No message "
            f"was composed rather than one carrying a number that cannot be "
            f"exact."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/share/receipt/{session_id}")
async def share_receipt_ep(session_id: str, request: Request) -> JSONResponse:
    """The same message, plus the link that opens WhatsApp on one number.

    Body: {"phone": "9876543210"}.
    """
    try:
        body = await _json_body(request)
        e164 = to_e164(body.get("phone"), whose="the customer")
        rec, message, urls, problem = _receipt_material(request, session_id)
        base = _composed(
            "receipt", message, urls,
            link_problem=problem,
            extra={
                "session_id": rec["session_id"],
                "total_paise": rec["total_paise"],
                "total_rupees": rec["total_rupees"],
                "payment_state": rec["payment_state"],
                "payment_headline": rec["payment_headline"],
                "settled_by_verified_webhook":
                    rec["settled_by_verified_webhook"],
                "excluded_count": rec["excluded_count"],
                "receipt_url": urls[0] if urls else None,
            })
        return JSONResponse(_addressed(base, e164, message, urls))
    except ShareRefused as exc:
        return _refusal(exc)
    except receipts.ReceiptRefused as exc:
        return _refusal(ShareRefused(exc.reason, exc.detail, status=exc.status))
    except MoneyError as exc:
        return _refusal(ShareRefused(
            R_INTERNAL,
            f"a figure on this bill is not integer paise ({exc}). No message "
            f"was composed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/share/order/{order_id}")
def share_order_preview_ep(order_id: str) -> JSONResponse:
    """The order message, and the number the customer left when they ordered.

    `phone_on_file` is shown to the SHOPKEEPER, who already has the whole
    order in front of him on the Orders screen including the delivery address.
    It is not on any route a customer reaches.
    """
    try:
        doc = _order_doc(order_id)
        message = compose_order(doc, _shop())
        customer = doc.get("customer") or {}
        return JSONResponse(_composed(
            "order", message, (),
            extra={
                "order_id": doc.get("order_id"),
                "status": doc.get("status"),
                "total_paise": int(doc.get("total_paise") or 0),
                "total_rupees": to_rupees_str(
                    int(paise(doc.get("total_paise") or 0))),
                "phone_on_file": customer.get("phone"),
                "paid": bool((doc.get("payment") or {}).get("paid")),
            }))
    except ShareRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(ShareRefused(
            R_INTERNAL,
            f"a figure on this order is not integer paise ({exc}). No message "
            f"was composed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/share/order/{order_id}")
async def share_order_ep(order_id: str, request: Request) -> JSONResponse:
    """The order message addressed to a number.

    Body: {"phone": "9876543210"} — or no body at all, which means "the number
    the customer left on the order". The phone the page sends wins, because a
    customer who rings from a second number is a normal thing.
    """
    try:
        body = await _json_body(request)
        doc = _order_doc(order_id)
        customer = doc.get("customer") or {}
        e164, source = _phone_from(
            body, customer.get("phone"), whose="the customer",
            fallback_from="the number on this order")
        message = compose_order(doc, _shop())
        base = _composed(
            "order", message, (),
            extra={
                "order_id": doc.get("order_id"),
                "status": doc.get("status"),
                "total_paise": int(doc.get("total_paise") or 0),
                "total_rupees": to_rupees_str(
                    int(paise(doc.get("total_paise") or 0))),
                "paid": bool((doc.get("payment") or {}).get("paid")),
                "phone_from": source,
            })
        return JSONResponse(_addressed(base, e164, message, ()))
    except ShareRefused as exc:
        return _refusal(exc)
    except MoneyError as exc:
        return _refusal(ShareRefused(
            R_INTERNAL,
            f"a figure on this order is not integer paise ({exc}). No message "
            f"was composed."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/share/reorder")
def share_reorder_preview_ep(supplier_id: str | None = None) -> JSONResponse:
    """The purchase order this counter would send, from the low-stock list.

    `?supplier_id=` only addresses it — it does not filter the list. This
    counter does not record which supplier a product comes from, so filtering
    by one would be a claim it cannot support; the shopkeeper edits the message
    in WhatsApp, which is where he was going to edit it anyway.
    """
    try:
        low = _low_stock()
        sup = _supplier(supplier_id)
        message = compose_reorder(low, _shop(), sup)
        return JSONResponse(_composed(
            "reorder", message, (),
            extra={
                "low_count": len(low.get("orderable") or []),
                "at_or_under_level_count": len(low.get("low") or []),
                "unknown_count": len(low.get("unknown") or []),
                "needs_recount_count": len(low.get("needs_recount") or []),
                "supplier": sup,
                "phone_on_file": (sup or {}).get("phone"),
                "filtered_by_supplier": False,
                "chain": low.get("chain"),
            }))
    except ShareRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/share/reorder")
async def share_reorder_ep(request: Request) -> JSONResponse:
    """The purchase order addressed to a wholesaler.

    Body: {"supplier_id": "sup_…"} uses that supplier's number on file;
    {"phone": "9876543210"} overrides it; both together address a recorded
    supplier on a different number.
    """
    try:
        body = await _json_body(request)
        low = _low_stock()
        sup = _supplier(body.get("supplier_id"))
        e164, source = _phone_from(
            body, (sup or {}).get("phone"), whose="the supplier",
            fallback_from=("this supplier's record" if sup
                           else "file — no supplier was named either"))
        message = compose_reorder(low, _shop(), sup)
        base = _composed(
            "reorder", message, (),
            extra={
                "low_count": len(low.get("orderable") or []),
                "at_or_under_level_count": len(low.get("low") or []),
                "unknown_count": len(low.get("unknown") or []),
                "needs_recount_count": len(low.get("needs_recount") or []),
                "supplier": sup,
                "phone_from": source,
                "filtered_by_supplier": False,
                "chain": low.get("chain"),
            })
        return JSONResponse(_addressed(base, e164, message, ()))
    except ShareRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


__all__ = [
    "MAX_LINES_IN_A_MESSAGE",
    "MAX_MESSAGE_CHARS",
    "SHARE_HOSTS",
    "ShareRefused",
    "compose_order",
    "compose_receipt",
    "compose_reorder",
    "display_phone",
    "router",
    "to_e164",
    "wa_url",
]
