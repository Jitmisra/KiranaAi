"""GRAAHAK — the people who buy, derived from the orders they already placed.

An order arrives with a name, a phone number and an address, lands in
``store_dir()/orders/``, and is never looked at again as a PERSON. The same
woman ordering on Monday and on Friday is two strangers to this counter: the
shopkeeper knows her, the till does not, and "she takes the small Lifebuoy and
she is on the second floor" lives in one head and nowhere else. When that head
is at a wedding and the nephew is minding the shop, it lives nowhere at all.

This module is that memory. It is A VIEW, NOT A STORE.

WHAT IT IS
==========
Every customer here is DERIVED, on each request, from the order files the
storefront already wrote. There is no customers table, no signup, no profile
anybody fills in, and nothing on disk that only this module knows. Nothing here
opens a file for writing — the test suite asserts that against this file's own
source, and asserts that the shop directory is byte-identical after every route
has been called.

That is a deliberate trade, and here is what it costs. The derivation reads
every order file on every request, so a shop with fifty thousand orders will
feel it: at a few hundred orders it is milliseconds, and it grows in a straight
line from there. The alternative — a customers file with a life of its own —
buys speed and pays with the one failure this program will not have, which is
two records disagreeing and no way to tell which is lying. Re-deriving is always
correct. When a shop is genuinely too big for this, the answer is a cache keyed
on the orders directory's own state, not a second place a customer can exist.

KEYED ON THE PHONE NUMBER, AND WHY THAT IS THE HONEST KEY
=========================================================
A name is not an identity: two Rekhas is the normal case in one lane. An address
is not one either — a family orders to one flat under three names. The phone is
what the shopkeeper actually dials, so it is the key, normalised as
``normalise_phone`` documents.

The stated limit: ONE HOUSEHOLD, ONE PHONE. A husband ordering on his wife's
number is her, here, and there is nothing in an order file that could say
otherwise. And if a number is recycled by the operator to somebody new, the new
owner inherits the old one's history until the shopkeeper notices the name
changed. The name on the newest order is therefore the name shown, and
``names_seen`` in the detail view lists every name that number has ever used —
so the shopkeeper can see the change rather than have it merged out of sight.

PRIVACY IS A FEATURE HERE
=========================
This is the shopkeeper's own record of their own customers, derived from what
those customers typed into this shop's own storefront. It is computed on this
machine, from files on this machine, and IT LEAVES THE MACHINE NOWHERE: there is
no export route, no third party, no analytics call, no identifier sent anywhere.
Nothing in this module makes a network request of any kind.

Inside that, one rule is enforced by the shapes below rather than by care:

    A SUMMARY NEVER CARRIES AN ADDRESS. ``/customers``, ``/customers/lookup``
    and ``/customers/regulars`` return a row with a count of addresses and no
    address in it. Only ``/customers/{phone}``, which is one named person a
    shopkeeper asked for by number, returns where they live.

The reason is the difference between looking someone up and holding a list. A
screen that renders every customer's home address is one screenshot away from
being a leak, and nobody at a counter ever needed forty addresses at once.

WHAT THIS DOES NOT DO
=====================
It does not authenticate. These routes are the shopkeeper's side of the glass
and carry no login, exactly like the ``/orders`` list they are derived from — so
a till exposed to the public internet through a tunnel exposes these too. That
is a property of the server they mount into, not something this module can fix,
and it is written here so nobody has to discover it.

It does not decide anything about money. No route below mints, refunds,
discounts or marks anything paid; ``settles_money`` is False on every response
and that is a fact about the code rather than a promise. It writes no audit line
because it changes no money and no stock — there is nothing to witness in a read
that leaves every byte where it found it.

MOUNTING
========
An ``APIRouter`` with NO prefix; the paths below are already absolute::

    from gawaah import customers
    app.include_router(customers.router)

    GET /customers                the list, no addresses in it
    GET /customers/regulars       who comes back, by spend and by count
    GET /customers/lookup         a number said at the counter, whole or partial
    GET /customers/{phone}        one person in full: addresses, orders

``/customers/regulars`` and ``/customers/lookup`` are declared BEFORE
``/customers/{phone}`` on purpose. FastAPI matches in declaration order, and the
other way round a request for the regulars list would be read as a customer
whose phone number is the word "regulars".
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .money import MoneyError, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach, and each is
# written so a shopkeeper can act on it. The reason names the STATE; the
# sentence that says what to do goes in `detail`.

R_NO_ORDERS_SOURCE = "orders_location_unknown"
R_NO_TILL = "till_module_unavailable"
R_NO_ORDERS = "orders_directory_unreadable"
R_NO_PHONE = "phone_missing"
R_BAD_PHONE = "phone_not_a_number"
R_SHORT_PHONE = "phone_too_short_to_search"
R_NO_CUSTOMER = "no_customer_with_this_number"
R_BAD_LIMIT = "limit_not_a_positive_integer"
R_BAD_SORT = "unknown_ordering"
R_TOO_LONG = "search_text_too_long"
R_INTERNAL = "customers_internal_error"


# ------------------------------------------------------------------ caps --
#
# What each one costs when it is wrong: a genuine query is refused and the
# shopkeeper has to narrow it. That is a nuisance. An unbounded response
# assembled from every order on disk is a page that never paints.

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
REGULARS_LIMIT = 10
MAX_SEARCH = 40

#: A number with fewer digits than this is not a number anybody can dial, and it
#: is the same floor `gawaah/storefront.py` applies when it takes the order.
MIN_PHONE_DIGITS = 7

#: A partial search at the counter. Four digits is what people actually say when
#: they say "the last four" — below that, half the shop matches and the answer
#: is noise rather than a lookup.
MIN_SEARCH_DIGITS = 4

#: One order is a visit, not a habit. Ranking by frequency includes only
#: customers who have come back at least this many times; the response says so
#: rather than leaving a shopkeeper to wonder why a name is missing.
MIN_REGULAR_ORDERS = 2

SORTS = ("recent", "spend", "orders", "name")
RANKINGS = ("spend", "frequency")

#: Orders that were cancelled are counted, and their money is not. See
#: `_absorb` for what that costs.
CANCELLED = "cancelled"


class CustomersRefused(Exception):
    """A named refusal with a sentence a shopkeeper can act on."""

    def __init__(self, reason: str, detail: str, status: int = 400) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status


def _refusal(exc: CustomersRefused) -> JSONResponse:
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


# ------------------------------------------------------- where orders live --


def _storefront() -> Any:
    """The module that OWNS the orders, imported late.

    Not a second answer to "where do orders live". `gawaah/storefront.py`
    writes them and publishes `orders_dir()`, which is
    `upload_app.store_dir()/orders` — so honouring `GAWAAH_SHOP_DIR` here is
    the same act as honouring it there, and the day somebody moves the orders
    this module follows them instead of reading an empty directory and
    reporting, with total confidence, that the shop has no customers.

    Imported inside the function because the till mounts both routers: at
    module scope this would be an import cycle through `tools/upload_app.py`.
    """
    try:
        from . import storefront  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001 - a missing module is a named answer
        raise CustomersRefused(
            R_NO_ORDERS_SOURCE,
            f"gawaah/storefront.py is not importable ({type(exc).__name__}: "
            f"{exc}), and it is the module that knows where orders are kept. "
            f"No customer list can be derived without it.") from None
    return storefront


def orders_dir() -> Path:
    """The directory the storefront writes orders into. Never guessed here."""
    sf = _storefront()
    try:
        return Path(sf.orders_dir())
    except CustomersRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - includes StorefrontRefused
        reason = str(getattr(exc, "reason", "") or R_NO_TILL)
        detail = str(getattr(exc, "detail", "") or
                     f"the orders directory could not be located "
                     f"({type(exc).__name__}: {exc}).")
        raise CustomersRefused(reason, detail) from None


def _read_orders() -> list[dict[str, Any]]:
    """Every order on disk, newest first.

    A directory that does not exist is a shop that has taken no orders yet, and
    that is a RESULT — an empty list — not a refusal. A path that exists and is
    not a directory is different: something is wrong with the shop directory
    itself, and saying "no customers" would be a lie with a plausible face.

    One unreadable or half-written file is skipped rather than fatal, for the
    same reason `storefront._all_orders` skips it: the other four hundred orders
    are still true, and losing the whole screen because one file is mid-rename
    would be a worse answer than a slightly short one.
    """
    d = orders_dir()
    if not d.exists():
        return []
    if not d.is_dir():
        raise CustomersRefused(
            R_NO_ORDERS,
            f"{d} exists but is not a directory, so the orders cannot be read. "
            f"Nothing was changed.")
    try:
        paths = sorted(d.glob("ord_*.json"))
    except OSError as exc:
        raise CustomersRefused(
            R_NO_ORDERS,
            f"the orders directory at {d} could not be listed "
            f"({type(exc).__name__}: {exc}).") from None

    out: list[dict[str, Any]] = []
    for p in paths:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - one bad file must not hide the rest
            continue
        if isinstance(doc, dict) and doc.get("order_id"):
            out.append(doc)
    # `at` is an ISO-8601 UTC string, so lexical order IS chronological order.
    # The id breaks ties so two orders placed in the same microsecond come out
    # in a stable order rather than a filesystem-dependent one.
    out.sort(key=lambda o: (str(o.get("at") or ""), str(o.get("order_id"))),
             reverse=True)
    return out


# ------------------------------------------------------------- the number --


def normalise_phone(raw: Any) -> str:
    """The digits that identify one subscriber, or "" if there are none.

    Three spellings of one number reach this shop — `9876543210`,
    `+91 98765 43210`, `098765 43210` — and they are one customer. So:
    everything that is not a digit is dropped, one leading zero is dropped, and
    a leading `91` is dropped when what remains is a ten-digit Indian mobile.

    THE STATED LIMIT: ONLY INDIA'S COUNTRY CODE IS FOLDED. A `+44` number keys
    separately from its local form, because folding country codes in general
    means guessing a national numbering plan from a string, and a wrong guess
    merges two customers who have never met. Getting a number wrong in the other
    direction — one person listed twice — is visible to the shopkeeper and
    fixable by the customer saying their number the same way next time.
    """
    if not isinstance(raw, str):
        return ""
    digits = re.sub(r"\D", "", raw)
    while digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


# ---------------------------------------------------------- reading orders --


def _int(value: Any) -> Optional[int]:
    """A whole number, or None.

    bool first: True is an int in Python, and a quantity of True is not a thing
    anybody meant.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _money(value: Any) -> Optional[int]:
    """Integer paise, validated at the boundary, or None. Never a float.

    `money.paise()` refuses a float, a bool and anything non-integral. If an
    order on disk ever held 21.45 instead of 2145, this is where it becomes an
    UNPRICED order that the response counts out loud, rather than where a rupee
    quietly becomes approximate.
    """
    n = _int(value)
    if n is None or n < 0:
        return None
    try:
        return int(paise(n))
    except MoneyError:
        return None


def _rupees(p: Optional[int]) -> Optional[str]:
    return None if p is None else to_rupees_str(int(paise(p)))


def _text(value: Any, cap: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:cap]


def _parse_ts(s: Any) -> Optional[_dt.datetime]:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return _dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_between(first: Any, last: Any) -> Optional[int]:
    """Whole days from the first order to the last, or None if either is unclear.

    Whole days, not an average and not a rate: `.days` on a timedelta is an
    integer, and nothing here divides anything.
    """
    a, b = _parse_ts(first), _parse_ts(last)
    if a is None or b is None:
        return None
    try:
        return int((b - a).days)
    except TypeError:
        # One timestamp carried a timezone and the other did not. Subtracting
        # them raises rather than lying by an unknown number of hours.
        return None


def _order_lines(doc: dict[str, Any]) -> list[dict[str, Any]]:
    raw = doc.get("lines")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for ln in raw:
        if not isinstance(ln, dict):
            continue
        unit = _money(ln.get("unit_paise"))
        line = _money(ln.get("line_paise"))
        qty = _int(ln.get("qty"))
        out.append({
            "sku_id": _text(ln.get("sku_id"), cap=120),
            "name": _text(ln.get("name"), cap=120),
            "qty": qty,
            "unit_paise": unit,
            "unit_rupees": _rupees(unit),
            "line_paise": line,
            "line_rupees": _rupees(line),
        })
    return out


def _order_total(doc: dict[str, Any], lines: list[dict[str, Any]]
                 ) -> Optional[int]:
    """What this order came to, in integer paise, or None if it cannot be read.

    The order's own `total_paise` first, because that is the number the shop
    quoted and the customer agreed to. Only if that field is missing or is not
    integer paise are the lines added up instead, and if the lines cannot be
    added either the order is UNPRICED: counted as a visit, excluded from every
    rupee, and reported as excluded. Abstaining beats a total that is confident
    and wrong — the same rule the counter applies to a packet it cannot name.
    """
    stated = _money(doc.get("total_paise"))
    if stated is not None:
        return stated
    summed = 0
    seen = False
    for ln in lines:
        if ln["line_paise"] is None:
            return None
        summed += int(ln["line_paise"])
        seen = True
    if not seen:
        return None
    try:
        return int(paise(summed))
    except MoneyError:
        return None


def _order_view(doc: dict[str, Any]) -> dict[str, Any]:
    """One order as a customer's history shows it.

    A projection, not the order document: the address and the phone are already
    on the customer record that owns this list, and the payment block carries a
    gateway link that has no business being repeated on a history screen.
    """
    lines = _order_lines(doc)
    total = _order_total(doc, lines)
    pay = doc.get("payment") if isinstance(doc.get("payment"), dict) else {}
    return {
        "order_id": _text(doc.get("order_id"), cap=64),
        "at": _text(doc.get("at"), cap=64),
        "status": _text(doc.get("status"), cap=32),
        "total_paise": total,
        "total_rupees": _rupees(total),
        "priced": total is not None,
        "paid": bool(pay.get("paid")),
        "line_count": len(lines),
        "lines": lines,
    }


# ------------------------------------------------------------ deriving them --


def _blank(phone: str) -> dict[str, Any]:
    return {
        "phone": phone,
        "phone_as_given": "",
        "name": "",
        "names_seen": [],
        "order_count": 0,
        "cancelled_count": 0,
        "kept_count": 0,
        "paid_count": 0,
        "unpriced_count": 0,
        "total_paise": 0,
        "paid_paise": 0,
        "cancelled_paise": 0,
        "first_order_at": "",
        "last_order_at": "",
        "last_status": "",
        "_addresses": {},
        "orders": [],
    }


def _absorb(rec: dict[str, Any], doc: dict[str, Any],
            customer: dict[str, Any], raw_phone: str) -> None:
    """Fold one order into one customer. Called newest order first.

    WHAT COUNTS AS SPENT, stated once here because every rupee on every screen
    below comes from it:

      `total_paise` is the sum of the orders that were NOT cancelled. It is what
      this customer ASKED the shop for. It is not what settled — a customer can
      order and never pay, and calling that "spent" would flatter the number.

      `paid_paise` is the subset the gateway confirmed. That is money that
      actually arrived.

      `cancelled_paise` is kept separately and added to neither, so a customer
      who orders and cancels every time is visible as exactly that rather than
      as a big spender or as nothing at all.
    """
    view = _order_view(doc)
    rec["orders"].append(view)
    rec["order_count"] += 1

    status = view["status"]
    total = view["total_paise"]
    if total is None:
        rec["unpriced_count"] += 1

    if status == CANCELLED:
        rec["cancelled_count"] += 1
        if total is not None:
            rec["cancelled_paise"] += int(total)
    else:
        rec["kept_count"] += 1
        if total is not None:
            rec["total_paise"] += int(total)
    if view["paid"]:
        rec["paid_count"] += 1
        if total is not None:
            rec["paid_paise"] += int(total)

    # The newest order wins the name and the spelling of the number, because a
    # customer who corrects either has corrected it. Orders arrive newest first,
    # so the first one to set these is the newest one.
    name = _text(customer.get("name"), cap=80)
    if name and not rec["name"]:
        rec["name"] = name
    if name and name not in rec["names_seen"]:
        rec["names_seen"].append(name)
    given = _text(raw_phone, cap=24)
    if given and not rec["phone_as_given"]:
        rec["phone_as_given"] = given

    at = view["at"]
    if at:
        if not rec["last_order_at"] or at > rec["last_order_at"]:
            rec["last_order_at"] = at
            rec["last_status"] = status
        if not rec["first_order_at"] or at < rec["first_order_at"]:
            rec["first_order_at"] = at

    address = _text(customer.get("address"), cap=400)
    if address:
        seen = rec["_addresses"].get(address)
        if seen is None:
            rec["_addresses"][address] = {
                "address": address, "orders": 1,
                "first_seen": at, "last_seen": at,
            }
        else:
            seen["orders"] += 1
            if at:
                if not seen["last_seen"] or at > seen["last_seen"]:
                    seen["last_seen"] = at
                if not seen["first_seen"] or at < seen["first_seen"]:
                    seen["first_seen"] = at


def build() -> dict[str, Any]:
    """Every customer this shop has, derived fresh from the orders on disk.

    Returns the records and the two facts a reader needs to trust them: how many
    orders were read, and how many of those could not be attributed to anybody.
    An order with no dialable phone number is NOT silently dropped — it is
    counted and reported, because the difference between "you have no customers"
    and "your orders have no phone numbers in them" is the whole answer.
    """
    docs = _read_orders()
    by_phone: dict[str, dict[str, Any]] = {}
    orphans = 0
    for doc in docs:
        customer = doc.get("customer")
        if not isinstance(customer, dict):
            orphans += 1
            continue
        raw_phone = customer.get("phone")
        raw_phone = raw_phone if isinstance(raw_phone, str) else ""
        key = normalise_phone(raw_phone)
        if len(key) < MIN_PHONE_DIGITS:
            orphans += 1
            continue
        rec = by_phone.get(key)
        if rec is None:
            rec = _blank(key)
            by_phone[key] = rec
        _absorb(rec, doc, customer, raw_phone)
    return {
        "customers": by_phone,
        "orders_read": len(docs),
        "orders_without_a_phone": orphans,
    }


# -------------------------------------------------------------- the shapes --


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    """One customer as a LIST shows them. NO ADDRESS APPEARS HERE.

    Not "the address is omitted from the template" — it is not in the dict, so
    no page, no console log and no copied-out response can carry it. What the
    list gets is a COUNT of addresses, which is the useful part anyway: two
    addresses on one number is a customer who moved, or an office and a home,
    and that is worth seeing before you dial.
    """
    return {
        "phone": rec["phone"],
        "name": rec["name"],
        "order_count": rec["order_count"],
        "kept_count": rec["kept_count"],
        "cancelled_count": rec["cancelled_count"],
        "paid_count": rec["paid_count"],
        "unpriced_count": rec["unpriced_count"],
        "total_paise": rec["total_paise"],
        "total_rupees": to_rupees_str(int(paise(rec["total_paise"]))),
        "paid_paise": rec["paid_paise"],
        "paid_rupees": to_rupees_str(int(paise(rec["paid_paise"]))),
        "cancelled_paise": rec["cancelled_paise"],
        "cancelled_rupees": to_rupees_str(int(paise(rec["cancelled_paise"]))),
        "first_order_at": rec["first_order_at"],
        "last_order_at": rec["last_order_at"],
        "last_status": rec["last_status"],
        "days_known": _days_between(rec["first_order_at"],
                                    rec["last_order_at"]),
        "address_count": len(rec["_addresses"]),
        "names_seen_count": len(rec["names_seen"]),
    }


def detail(rec: dict[str, Any]) -> dict[str, Any]:
    """One customer in full: the addresses and the orders, newest first.

    This is the endpoint a shopkeeper reaches by typing ONE number, and it is
    the only shape in this module that carries where somebody lives. Addresses
    are ordered most recently used first, so the top one is where to send today
    and the older ones are still there for the delivery that says "the new
    place, not the old one".
    """
    out = dict(summary(rec))
    addresses = sorted(rec["_addresses"].values(),
                       key=lambda a: (str(a["last_seen"]), a["address"]),
                       reverse=True)
    out["addresses"] = addresses
    out["names_seen"] = list(rec["names_seen"])
    out["phone_as_given"] = rec["phone_as_given"]
    # `_absorb` was fed newest-first, so this list is already newest-first. It
    # is re-sorted anyway rather than relying on that from two files away.
    out["orders"] = sorted(rec["orders"],
                           key=lambda o: (str(o["at"]), str(o["order_id"])),
                           reverse=True)
    return out


# -------------------------------------------------------- reading the query --


def _require_limit(raw: Any, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        want = int(str(raw))
    except (TypeError, ValueError):
        raise CustomersRefused(
            R_BAD_LIMIT,
            f"limit={raw!r} is not a whole number. Leave it out for "
            f"{default}.") from None
    if want < 1:
        raise CustomersRefused(
            R_BAD_LIMIT,
            f"limit={want} asks for nobody at all; the smallest useful limit "
            f"is 1.")
    if want > MAX_LIMIT:
        raise CustomersRefused(
            R_BAD_LIMIT,
            f"limit={want} is over the ceiling of {MAX_LIMIT}. Search for a "
            f"name or a number instead of asking for everybody.")
    return want


def _require_sort(raw: Any) -> str:
    if raw is None or raw == "":
        return "recent"
    want = str(raw).strip().lower()
    if want not in SORTS:
        raise CustomersRefused(
            R_BAD_SORT,
            f"{raw!r} is not an order this list can be put in. It knows: "
            f"{', '.join(SORTS)}.")
    return want


def _require_ranking(raw: Any) -> Optional[str]:
    """Which regulars list was asked for, or None for both."""
    if raw is None or raw == "":
        return None
    want = str(raw).strip().lower()
    if want == "orders":
        # A shopkeeper's page may honestly call this "orders"; it is the same
        # ranking, and refusing a synonym would be pedantry rather than safety.
        want = "frequency"
    if want not in RANKINGS:
        raise CustomersRefused(
            R_BAD_SORT,
            f"{raw!r} is not a way of being a regular that this shop counts. "
            f"It knows: {', '.join(RANKINGS)}. Leave it out for both.")
    return want


def _require_search(raw: Any) -> str:
    if raw is None:
        return ""
    s = " ".join(str(raw).split())
    if len(s) > MAX_SEARCH:
        raise CustomersRefused(
            R_TOO_LONG,
            f"that search is {len(s)} characters and the cap is {MAX_SEARCH}. "
            f"A name or a phone number is shorter than that.")
    return s


def _require_phone(raw: Any, *, whole: bool) -> str:
    """A number to look somebody up by, in digits.

    `whole=True` is the detail route, where a partial number would be a guess at
    which customer was meant. `whole=False` is the counter, where "the last four
    are 4210" is a normal thing for a person to say.
    """
    if raw is None or not str(raw).strip():
        raise CustomersRefused(
            R_NO_PHONE,
            "no phone number was given. This lookup takes the number the "
            "customer says at the counter.")
    digits = normalise_phone(str(raw))
    if not digits:
        raise CustomersRefused(
            R_BAD_PHONE,
            f"{str(raw)!r} has no digits in it, so it is not a phone number. "
            f"Search the list by name instead.")
    floor = MIN_PHONE_DIGITS if whole else MIN_SEARCH_DIGITS
    if len(digits) < floor:
        raise CustomersRefused(
            R_SHORT_PHONE,
            f"{str(raw)!r} has {len(digits)} digits in it and this lookup "
            f"needs at least {floor}. Fewer than that matches half the shop.")
    return digits


def _sorted(rows: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    """Deterministic in every order, ties broken by the phone number.

    Two customers with identical spend must not swap places between two loads of
    the same screen — a list that reshuffles under a shopkeeper's finger reads
    as a bug in the shop, not in the sort.
    """
    if how == "spend":
        return sorted(rows, key=lambda r: (-int(r["total_paise"]),
                                           -int(r["order_count"]),
                                           r["phone"]))
    if how == "orders":
        return sorted(rows, key=lambda r: (-int(r["order_count"]),
                                           -int(r["total_paise"]),
                                           r["phone"]))
    if how == "name":
        return sorted(rows, key=lambda r: ((r["name"] or "").casefold(),
                                           r["phone"]))
    return sorted(rows, key=lambda r: (r["last_order_at"], r["phone"]),
                  reverse=True)


def _matches(rec_summary: dict[str, Any], needle: str) -> bool:
    """Name or number, either way round, case-insensitively."""
    if not needle:
        return True
    hay_name = (rec_summary["name"] or "").casefold()
    if needle.casefold() in hay_name:
        return True
    digits = re.sub(r"\D", "", needle)
    return bool(digits) and digits in rec_summary["phone"]


NO_ADDRESS_NOTE = (
    "This list carries no addresses. Open one customer by their number to see "
    "where they live.")

DERIVED_NOTE = (
    "Every figure here is derived from this shop's own order files each time it "
    "is asked for. Nothing about a customer is stored separately and nothing "
    "leaves this machine.")


# ----------------------------------------------------------------- routes --


@router.get("/customers")
def customers_ep(q: str | None = None, limit: str | None = None,
                 sort: str | None = None) -> JSONResponse:
    """Everyone who has ordered, most recent first. No addresses in this list.

    `q` matches a name or any run of digits in the number, so a shopkeeper can
    type "rekha" or "4210". `sort` is one of recent, spend, orders, name.

    `total_paise` is what each customer has ASKED this shop for, cancelled
    orders excluded; `paid_paise` is the part the gateway confirmed arrived.
    They are different numbers on purpose and both are shown, because an order
    placed is not money received.
    """
    try:
        needle = _require_search(q)
        want = _require_limit(limit, DEFAULT_LIMIT)
        how = _require_sort(sort)

        derived = build()
        rows = [summary(rec) for rec in derived["customers"].values()]
        matched = [r for r in rows if _matches(r, needle)]
        ordered = _sorted(matched, how)
        shown = ordered[:want]

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "customers": shown,
            "count": len(shown),
            "matched": len(matched),
            "total_customers": len(rows),
            "orders_read": derived["orders_read"],
            "orders_without_a_phone": derived["orders_without_a_phone"],
            "limit": want,
            "sort": how,
            "sorts": list(SORTS),
            "q": needle,
            "note": NO_ADDRESS_NOTE + " " + DERIVED_NOTE,
        })
    except CustomersRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/customers/regulars")
def regulars_ep(by: str | None = None, limit: str | None = None
                ) -> JSONResponse:
    """Who comes back — by what they have spent, and by how often they come.

    Two lists, because they answer two questions and the same shop rarely gives
    the same name to both. The wholesale-sized monthly order is at the top of
    `by_spend`; the woman who takes two things every second evening is at the
    top of `by_frequency`, and she is the one whose habit the shop is built on.

    `by_frequency` counts ORDERS KEPT — cancelled ones do not make somebody a
    regular — and includes only customers with at least MIN_REGULAR_ORDERS of
    them. One visit is not a habit, and the response says the floor out loud so
    a missing name is explainable rather than mysterious.

    THE STATED LIMIT: frequency here is a COUNT, not a rate. Five orders in a
    week and five orders across a year rank the same. `days_known` on every row
    is what separates them, and a shopkeeper reading the two columns together
    gets the truth; a single blended score would hide it.
    """
    try:
        which = _require_ranking(by)
        want = _require_limit(limit, REGULARS_LIMIT)

        derived = build()
        rows = [summary(rec) for rec in derived["customers"].values()]

        out: dict[str, Any] = {
            "ok": True,
            "settles_money": False,
            "by": which,
            "rankings": list(RANKINGS),
            "limit": want,
            "min_orders_for_frequency": MIN_REGULAR_ORDERS,
            "total_customers": len(rows),
            "orders_read": derived["orders_read"],
            "note": NO_ADDRESS_NOTE + " " + DERIVED_NOTE,
        }
        if which in (None, "spend"):
            spenders = [r for r in rows if int(r["total_paise"]) > 0]
            out["by_spend"] = _sorted(spenders, "spend")[:want]
        if which in (None, "frequency"):
            often = [r for r in rows
                     if int(r["kept_count"]) >= MIN_REGULAR_ORDERS]
            out["by_frequency"] = _sorted(often, "orders")[:want]
        return JSONResponse(out)
    except CustomersRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/customers/lookup")
def lookup_ep(phone: str | None = None, limit: str | None = None
              ) -> JSONResponse:
    """A number said at the counter. Whole, or the last few digits.

    The till's own question: somebody is standing there, they say their number,
    and the shopkeeper wants to know whether this is a customer and what they
    usually take. An exact match on the whole number comes back as `exact`; a
    partial comes back as a short list to read out and confirm.

    FINDING NOBODY IS AN ANSWER, NOT A REFUSAL. A number that matches nothing
    returns `ok` with an empty list and `matched_on: none`, because a new
    customer at the counter is the most ordinary event in a shop and the till
    should show a blank card rather than an error. Asking for a specific
    customer who does not exist — `GET /customers/{phone}` — is the case that
    404s.

    No addresses here either: this is a summary, and the counter needs a name
    and a history, not a doorstep.
    """
    try:
        digits = _require_phone(phone, whole=False)
        want = _require_limit(limit, REGULARS_LIMIT)

        derived = build()
        records = derived["customers"]
        exact = records.get(digits)

        rows = [summary(rec) for key, rec in records.items()
                if digits in key]
        rows = _sorted(rows, "recent")[:want]

        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "asked_for": digits,
            "matched_on": ("exact" if exact is not None
                           else "part_of_the_number" if rows else "none"),
            "customer": summary(exact) if exact is not None else None,
            "matches": rows,
            "count": len(rows),
            "detail_url": (f"/customers/{digits}" if exact is not None
                           else None),
            "note": (NO_ADDRESS_NOTE if rows else
                     "No customer in this shop's orders has that number. If "
                     "they order, they will appear here without anybody "
                     "adding them."),
        })
    except CustomersRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/customers/{phone}")
def customer_ep(phone: str) -> JSONResponse:
    """One customer in full: their addresses and every order, newest first.

    The whole number, not a fragment — a partial here would answer a question
    about one person with a guess about several. THIS IS THE ONLY SHAPE IN THIS
    MODULE THAT CARRIES AN ADDRESS, and it is reached by naming the number of
    the person whose address it is.
    """
    try:
        digits = _require_phone(phone, whole=True)
        derived = build()
        rec = derived["customers"].get(digits)
        if rec is None:
            raise CustomersRefused(
                R_NO_CUSTOMER,
                f"no order in this shop was placed on {digits}. A customer "
                f"appears here when they order, and nobody can be added by "
                f"hand.",
                status=404)
        body = detail(rec)
        body.update({
            "ok": True,
            "settles_money": False,
            "note": DERIVED_NOTE,
        })
        return JSONResponse(body)
    except CustomersRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
