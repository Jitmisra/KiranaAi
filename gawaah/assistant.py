"""MUNSHI — the counter's assistant. The model routes; the shop answers.

A shopkeeper says or types one sentence — "do kilo doodh add karo", "kitne
online orders pending hain?", "dui ta sabun ar ek Maggi" — and gets back either
an ANSWER about his own shop or a PROPOSAL he can accept. Voice and typing land
in exactly the same place and are treated identically.

THE MODEL IS A ROUTER, NOT A DATA SINK
======================================
This is the whole design and it is the difference between an assistant and a
leak. The model is sent TWO things and nothing else:

    1. the shopkeeper's own sentence, as he said it
    2. the schemas of the tools below — names, argument shapes, no data

It is NOT sent the catalogue, the prices, the orders, the takings, the customer
names, the stock counts, or any part of the audit chain. It cannot be: none of
that is in the request body, and `payload_for()` is the single place the body is
built so that claim is checkable in one function and is asserted by a test that
reads the outgoing bytes.

What comes back is a tool NAME and ARGUMENTS taken from the sentence — usually
just the words the shopkeeper used for a product and a count. The tool is then
EXECUTED HERE, on this machine, against this counter's own files. So the model
learns that somebody said "doodh"; it never learns that this shop stocks Amul
Taaza at 2750 paise, how many are left, or who ordered one.

Two consequences worth stating plainly, because they are limits and not
features:

  - The model never sees a price, so it can never quote one. Every rupee in
    every answer below was read from the shop's own catalogue after the routing
    decision was made.
  - The model DOES see the sentence, which is the shopkeeper's own words. If he
    dictates a customer's name into it, that name goes to xAI. Nothing here can
    prevent that, and pretending otherwise would be the same lie in the other
    direction.

EVERY TOOL BELONGS TO A MODULE THAT ALREADY OWNS THE ANSWER
==========================================================
The assistant does not know what stock is, what a margin is or when a day ends.
Each tool is a thin call into the module that decides those things — stock.py,
expenses.py, purchases.py, customers.py, categories.py, daybook.py, offers.py,
gst.py, expiry.py, loyalty.py, weighed.py — usually into the very function that
draws the corresponding screen, so a figure said out loud here and a figure read
off the screen cannot drift apart. Nothing is re-derived and no second copy of a
rule lives in this file.

Every one of those modules is PRESENCE-CHECKED before it is used, and a module
that has moved is a named refusal with a sentence rather than a wrong number.
When the owning module refuses, ITS reason and ITS sentence come back — the
module that knows what went wrong is the one that gets to name it.

THREE LANGUAGES, AND MIXTURES OF THEM
=====================================
The local parser reads Hinglish, Hindi and Bengali, in Latin script and in
Bengali and Devanagari digits, mixed freely inside one sentence. "dui ta sabun
ar ek Maggi", "do sabun aur ek Maggi" and "2 soap and 1 Maggi" reach the same
tool with the same arguments. What it costs when this is wrong is stated where
each word list is defined: a stopword list that eats a product is worse than one
that leaves a stray verb in.

A SENTENCE WITH SEVERAL PRODUCTS IS PROPOSED IN FULL
====================================================
It used to be refused. Billing half a sentence silently is the failure this
program treats as disqualifying, and refusing was the safe way to avoid it —
but proposing ALL of it and letting a person accept is safe too, and is better.
So a multi-product sentence becomes a multi-line PROPOSAL. If any one line
cannot be resolved the WHOLE proposal is refused by name: a proposal that is
quietly one line short is exactly the failure the refusal existed to prevent.

`more_than_one_product_in_one_sentence` is kept and is still reachable, with a
narrower meaning: a QUESTION is about one product, so "Maggi aur Lifebuoy ka
daam kya hai" is refused by that name rather than answered about one of them.

NO KEY IS A FIRST-CLASS STATE
=============================
`XAI_API_KEY` absent is not an error and not a degraded mode with a warning
banner. The counter falls back to a deterministic local parser that handles the
common shapes — counts and products, and the questions the tools cover — and
every response says which brain answered (`brain: "local"` or `"grok"`). A shop
on a dropped connection keeps working; it just understands fewer ways of
phrasing things. The whole test suite runs with no key set.

The key is read from the environment at call time, never hardcoded, never
logged, and never echoed into a response body — including error details, which
are built from the transport's status and exception type, not from its headers.

WHAT THIS MODULE CANNOT DO
==========================
It never writes a bill, never touches stock, never records an expense, never
mints anything and holds no gateway credential. `settles_money` is False on
every response and that is a fact about the code: there is no payment client
here, no UPI string, no URL template. The strongest action it can take is to
WRITE DOWN A PROPOSAL — resolved sku ids, whole-number quantities and prices
read from the shop's own catalogue — and hand it back for a person to accept.
Accepting is a separate act on a separate screen.

That is why the write-shaped tools are called `propose_*` and why there is no
tool that books a delivery or files an expense. A proposal of a stock movement
is a piece of paper; `POST /stock/{sku}/in` is the shelf. The assistant writes
the paper and nothing else, and it validates the paper against the owning
module's own vocabulary so that a proposal a person accepts cannot then be
refused by the module that has to carry it out.

A REFUSAL IS A RESULT
=====================
Every failure has a name in `reason` and a sentence a shopkeeper can act on in
`detail`, with a 400. Nothing here raises a 500.

MOUNTING
========
The router carries NO prefix; these paths are already absolute::

    GET  /assistant/health              which brain would answer, and on what
    GET  /assistant/tools               exactly what is sent to the model
    POST /assistant/ask                 one sentence in, an answer or proposal
    GET  /assistant/proposal/{id}       read a proposal back

    from gawaah import assistant
    app.include_router(assistant.router)     # -> /assistant/ask
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .ledger import Ledger
from .money import MoneyError, from_rupees_str, paise, to_rupees_str
# THE TYPO BUDGET AND THE TRANSLITERATOR ARE search.py's, IMPORTED AND NOT
# COPIED. `gawaah/search.py` already decides how far off a word may be before
# the search box will offer a product for it, and `resolve_product` below has to
# answer the same question about a word that was spoken instead of typed. Two
# copies of that rule would be two counters with different opinions about what
# "close" means, and the one that drifted would be the one nobody was reading.
# `_max_edits` is private to that module and is imported under its own name so
# that stays visible. The dependency runs one way only: search.py imports
# nothing from here.
from .search import _max_edits, edit_distance, romanise

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Every one of these is a state this module can actually reach. The reason names
# the state; the sentence that says what to change lives in `detail`.

R_BAD_BODY = "ask_body_not_json"
R_NO_TEXT = "nothing_was_said"
R_TEXT_TOO_LONG = "sentence_too_long"
R_BAD_SOURCE = "source_not_voice_or_text"
R_CLIENT_AUTHORED = "client_tried_to_author_the_bill"
R_NOT_UNDERSTOOD = "sentence_not_understood"
R_NO_PRODUCT_NAMED = "no_product_named_in_the_sentence"
R_NO_SUCH_PRODUCT = "no_such_product_in_this_shop"
R_AMBIGUOUS = "several_products_match"
R_BAD_QTY = "quantity_not_a_whole_number"
R_QTY_TOO_LARGE = "quantity_beyond_this_counter"
R_SEVERAL_PRODUCTS = "more_than_one_product_in_one_sentence"
R_BAD_THRESHOLD = "stock_threshold_not_a_whole_number"
R_UNKNOWN_TOOL = "model_named_a_tool_that_does_not_exist"
R_MODEL_PRICED = "model_tried_to_set_a_price"
R_BAD_TOOL_ARGS = "model_arguments_not_json"
R_NO_TILL = "till_module_unavailable"
R_NO_CATALOGUE = "catalogue_unavailable"
R_EMPTY_CATALOGUE = "nothing_taught_yet"
R_ORDERS_UNAVAILABLE = "orders_unavailable"
R_TAKINGS_UNAVAILABLE = "takings_unavailable"
R_STOCK_UNAVAILABLE = "stock_unavailable"
R_BAD_PROPOSAL_ID = "proposal_id_malformed"
R_NO_PROPOSAL = "no_such_proposal"
R_INTERNAL = "assistant_internal_error"

#: One name per module the assistant reaches into. A module that has moved,
#: been renamed or fails to import is THIS refusal and never a wrong number:
#: "the stock module is not here" is an answer a shopkeeper can act on, and
#: "0 left" would not be.
R_STOCK_MODULE = "stock_module_unavailable"
R_EXPENSES_UNAVAILABLE = "expenses_unavailable"
R_PURCHASES_UNAVAILABLE = "purchases_unavailable"
R_CUSTOMERS_UNAVAILABLE = "customers_unavailable"
R_CATEGORIES_UNAVAILABLE = "categories_unavailable"
R_DAYBOOK_UNAVAILABLE = "daybook_unavailable"
R_OFFERS_UNAVAILABLE = "offers_unavailable"
R_GST_UNAVAILABLE = "gst_unavailable"
R_EXPIRY_UNAVAILABLE = "expiry_unavailable"
R_LOYALTY_UNAVAILABLE = "loyalty_unavailable"
R_WEIGHED_UNAVAILABLE = "weighed_unavailable"
R_KHATA_UNAVAILABLE = "khata_unavailable"
R_MILAN_UNAVAILABLE = "milan_unavailable"
R_NO_CUSTOMER_NAMED = "no_customer_named_in_the_sentence"
R_NO_HOUSEHOLD = "no_such_household_in_the_book"
R_SEVERAL_HOUSEHOLDS = "several_households_match"

#: Arguments the wider tool set can be given wrong.
R_TOO_MANY_LINES = "more_lines_than_this_counter_proposes"
R_NO_PHONE = "no_phone_number_in_the_sentence"
R_BAD_PHONE = "phone_not_a_number_this_counter_can_dial"
R_NO_CATEGORY_NAMED = "no_category_named_in_the_sentence"
R_NO_SUCH_CATEGORY = "no_such_category_in_this_shop"
R_BAD_DIRECTION = "movement_direction_not_in_or_out"
R_BAD_MOVEMENT_REASON = "movement_reason_not_one_this_counter_records"
R_BAD_EXPENSE_CATEGORY = "expense_category_not_one_this_counter_records"
R_NO_AMOUNT = "no_amount_in_the_sentence"
R_BAD_AMOUNT = "amount_not_a_rupee_figure"
R_AMOUNT_TOO_LARGE = "amount_beyond_this_counter"
R_SPOKEN_PRICE = "price_said_out_loud_is_not_a_price_here"
R_NO_WEIGHT = "no_weight_in_the_sentence"
R_NOT_WEIGHED = "product_is_not_sold_by_weight"
R_BAD_DAYS = "days_not_a_whole_number"
R_NOTE_TOO_LONG = "note_longer_than_this_counter_records"

#: Reasons the GROK path can fail without it being the shopkeeper's problem.
#: These fall back to the local parser rather than refusing, and the fallback is
#: named in the response — see `_route`.
R_GROK_UNREACHABLE = "grok_unreachable"
R_GROK_HTTP = "grok_refused_the_request"
R_GROK_SHAPE = "grok_answer_not_a_tool_call"
R_NO_TOOL_CALL = "model_answered_without_a_tool"


# ---------------------------------------------------------------- provider --

DEFAULT_BASE_URL = "https://api.x.ai/v1"

#: The model this counter routes through. Overridable with XAI_MODEL for an
#: operator who has been given a different one; the constant is what ships.
XAI_MODEL = "grok-4.20-0309-non-reasoning"
#: WHY THIS ONE. The model here is a ROUTER: it reads one sentence and picks a
#: tool. It never sees the catalogue, never sees a paise integer, and never
#: produces a number — every figure in an answer comes from the tool that owns
#: it. Reasoning tokens buy nothing for that job, so the non-reasoning variant
#: is chosen deliberately: same tool calling, same structured outputs, lower
#: cost and lower latency at a counter where somebody is waiting.
#:
#: The previous default was `grok-4-fast`, which is not a model this account
#: can reach -- `GET /v1/models` lists twelve and that is not among them. With
#: no key set nobody noticed, because the deterministic parser answered
#: everything. The day a key arrived it would have failed on the first
#: question, which is the worst possible time to find out.

XAI_TIMEOUT_S = 20

BRAIN_LOCAL = "local"
#: The word for "a model answered this", as opposed to `local` — the counter's
#: own parser. It NAMES THE PROVIDER, because this field is printed on screen
#: under "which brain answers" and a counter that says `grok` while calling
#: Google is lying about the one thing a shopkeeper might want to check. It did,
#: for one run, the moment the provider was switched: the label was a constant.
#:
#: `BRAIN_GROK` stays as the INTERNAL marker that a model (rather than the
#: local parser) answered — it is compared in both modules — while everything
#: written into a response body uses `brain_name()`.
def brain_name() -> str:
    host = base_url().split("//", 1)[-1].split("/", 1)[0].lower()
    if "x.ai" in host:
        return "grok"
    if "googleapis.com" in host:
        return "gemini"
    if "openai.com" in host:
        return "openai"
    # An operator pointing this at something else gets the host, not a guess.
    return host or "model"


BRAIN_GROK = "grok"


#: A provider that speaks the OpenAI chat-completions shape. There are two on
#: this counter and the code is identical for both, because the ONLY thing this
#: module asks a model to do is read one sentence and name a tool -- a job every
#: provider exposes the same way. Swapping is three environment variables and no
#: code, which is the point: nothing about the shop's behaviour lives in a
#: vendor.
#:
#:   xAI     GAWAAH_LLM_BASE_URL=https://api.x.ai/v1
#:           GAWAAH_LLM_KEY=<xai key>          GAWAAH_LLM_MODEL=grok-4.20-0309-non-reasoning
#:   Google  GAWAAH_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
#:           GAWAAH_LLM_KEY=<AI Studio key>    GAWAAH_LLM_MODEL=gemini-3.1-flash-lite
#:
#: The XAI_* spellings are still read, first, so an operator who set them up
#: before this was generalised does not have to change anything.
#: The provider-neutral name is tried FIRST in each pair. An operator who sets
#: `GAWAAH_LLM_MODEL` has chosen a model on purpose; a leftover `XAI_MODEL` from
#: a provider they have since moved off should not silently outrank it and send
#: a Grok model id to Google. (It did, for one run.)
_BASE_URL_VARS = ("GAWAAH_LLM_BASE_URL", "XAI_BASE_URL")
_MODEL_VARS = ("GAWAAH_LLM_MODEL", "XAI_MODEL")
#: Keys, in the order they are tried. `GAWAAH_LLM_KEY` is first because an
#: operator who sets the provider-neutral name meant it.
_KEY_VARS = ("GAWAAH_LLM_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")

#: WHICH KEY BELONGS TO WHICH HOST. A machine can hold credentials for both
#: providers at once -- this one does -- and sending xAI's key to Google's host
#: is a 401 that reads like a broken key rather than a mismatched one. So the
#: host decides which name to prefer, and the general list above is only the
#: fallback when the host is one this table does not know.
_KEYS_BY_HOST: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("api.x.ai", ("XAI_API_KEY", "GAWAAH_LLM_KEY")),
    ("googleapis.com", ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GAWAAH_LLM_KEY")),
)


def _first_env(names: tuple[str, ...]) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def base_url() -> str:
    """Where the chat-completions API lives. Not a secret; the key is."""
    return (_first_env(_BASE_URL_VARS) or DEFAULT_BASE_URL).rstrip("/")


def model_name() -> str:
    return _first_env(_MODEL_VARS) or XAI_MODEL


def api_key() -> str:
    """The key, or an empty string. NEVER returned in a response body.

    Read fresh on every call rather than captured at import: a test that sets
    or clears it between cases must be able to change the answer, and an
    operator who exports it after the till started should not have to restart.
    """
    host = base_url().split("//", 1)[-1].split("/", 1)[0].lower()
    for suffix, names in _KEYS_BY_HOST:
        if host == suffix or host.endswith("." + suffix) or host.endswith(suffix):
            found = _first_env(names)
            if found:
                return found
            break
    return _first_env(_KEY_VARS)


# ------------------------------------------------------------------ tools --
#
# THE TOOL LIST IS THE ASSISTANT'S CAPABILITY. Every operation the shop's other
# modules expose as a READ is here, and the two write-shaped operations that are
# safe as PAPER — a stock movement and an expense — are here as `propose_*`.
# Nothing on this list bills, books, files or charges anything.
#
# NOT ONE OF THESE TOOLS TAKES A PRICE FROM THE MODEL. The one money-shaped
# argument in the whole list is `amount_rupees` on `propose_expense`, and it is
# there because the amount of an expense is a fact the shopkeeper SAID, not a
# number this counter derives — see `_check_arguments`, which refuses a
# money-shaped argument on every tool whose own schema does not declare one.

TOOL_ADD = "add_to_bill"
TOOL_ORDERS = "list_pending_orders"
TOOL_TAKINGS = "todays_takings"
TOOL_FIND = "find_product"
TOOL_LOW_STOCK = "low_stock"
TOOL_PRICE = "price_of"

TOOL_STOCK_ON_HAND = "stock_on_hand"
TOOL_STOCK_MOVEMENTS = "stock_movements"
TOOL_REORDER_LIST = "reorder_list"
TOOL_PROPOSE_MOVEMENT = "propose_stock_movement"

TOOL_EXPENSES_TODAY = "expenses_today"
TOOL_CASH_POSITION = "cash_position"
TOOL_PROPOSE_EXPENSE = "propose_expense"

TOOL_MARGIN_OF = "margin_of"
TOOL_MARGIN_TODAY = "margin_today"
TOOL_SUPPLIERS = "list_suppliers"

TOOL_CUSTOMER = "customer_lookup"
TOOL_REGULARS = "regular_customers"

TOOL_CATEGORIES = "list_categories"
TOOL_IN_CATEGORY = "products_in_category"

TOOL_DAY_CLOSE = "day_close_preview"
TOOL_OFFERS = "list_offers"
TOOL_GST_OF = "gst_of"
TOOL_EXPIRING = "expiring_soon"
TOOL_EXPIRED = "expired_stock"
TOOL_LOYALTY = "loyalty_balance"
TOOL_LOYALTY_RULES = "loyalty_rules"
TOOL_WEIGHED = "weighed_price"
#: KHATA. `book_on_khata` PROPOSES putting the bill on the counter onto a
#: customer's book — a person presses ON THE BOOK on the till; nothing here
#: books anything. `khata_balance` answers "Sharma ji ka kitna baaki hai"
#: from the money chain through gawaah/khata.py.
TOOL_KHATA_BOOK = "book_on_khata"
TOOL_KHATA_BALANCE = "khata_balance"
#: MILAN. `bank_settlement` answers "kal bank mein kitna aaya" from the
#: gateway's own settlement report matched against the chain, through
#: gawaah/milan.py. It reads; the settle button on the Close screen is a
#: person's press, and no sentence here can press it.
TOOL_BANK = "bank_settlement"

TOOL_NAMES = (
    TOOL_ADD, TOOL_ORDERS, TOOL_TAKINGS, TOOL_FIND, TOOL_LOW_STOCK, TOOL_PRICE,
    TOOL_STOCK_ON_HAND, TOOL_STOCK_MOVEMENTS, TOOL_REORDER_LIST,
    TOOL_PROPOSE_MOVEMENT,
    TOOL_EXPENSES_TODAY, TOOL_CASH_POSITION, TOOL_PROPOSE_EXPENSE,
    TOOL_MARGIN_OF, TOOL_MARGIN_TODAY, TOOL_SUPPLIERS,
    TOOL_CUSTOMER, TOOL_REGULARS,
    TOOL_CATEGORIES, TOOL_IN_CATEGORY,
    TOOL_DAY_CLOSE, TOOL_OFFERS, TOOL_GST_OF, TOOL_EXPIRING, TOOL_EXPIRED,
    TOOL_LOYALTY, TOOL_LOYALTY_RULES, TOOL_WEIGHED,
    TOOL_KHATA_BOOK, TOOL_KHATA_BALANCE,
    TOOL_BANK,
)


def _fn(name: str, description: str, properties: dict[str, Any],
        required: list[str]) -> dict[str, Any]:
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object",
                                        "properties": properties,
                                        "required": required}}}


_PRODUCT_ARG = {
    "type": "string",
    "description": ("the product as the shopkeeper said it, in his own words "
                    "and script — do not translate it, do not correct the "
                    "spelling and do not add a brand he did not say"),
}

_QTY_ARG = {
    "type": "integer",
    "description": ("how many packets, a whole number. Default 1 if the "
                    "shopkeeper did not say a number."),
}

_UNIT_ARG = {
    "type": "string",
    "description": ("the unit he said, if any — kilo, gram, litre, packet, "
                    "dozen. Leave it out if he said none."),
}

_FRACTION_ARG = {
    "type": "string",
    "description": ("the half-or-quarter word he said, if any — aadha, pav, "
                    "dedh, sava, paune, dhai. Pass it through as he said it; "
                    "do not turn it into a number."),
}

_PHONE_ARG = {
    "type": "string",
    "description": ("the phone number as he said it, digits only or with "
                    "spaces — the counter normalises it. Do not invent one."),
}

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {"product": _PRODUCT_ARG, "qty": _QTY_ARG,
                   "unit": _UNIT_ARG, "fraction": _FRACTION_ARG},
    "required": ["product"],
}

_CUSTOMER_ARG = {
    "type": "string",
    "description": ("the customer as the shopkeeper said them — a name such "
                    "as 'Sharma ji' or a phone number — in his own words and "
                    "script. Do not invent one and do not resolve it; the "
                    "counter looks it up in its own book."),
}

TOOLS: tuple[dict[str, Any], ...] = (
    # ---------------------------------------------------------- the bill --
    _fn(TOOL_ADD,
        "Propose adding one or several products to the bill on the counter. "
        "Use 'items' when the sentence names more than one product, so that "
        "the whole sentence is proposed and none of it is dropped. This does "
        "not bill anything; a person accepts it afterwards.",
        {"product": _PRODUCT_ARG, "qty": _QTY_ARG, "unit": _UNIT_ARG,
         "fraction": _FRACTION_ARG,
         "items": {"type": "array", "items": _ITEM_SCHEMA,
                   "description": ("one entry per product the sentence names, "
                                   "in the order he said them. Use this "
                                   "INSTEAD of 'product' when there is more "
                                   "than one.")}},
        []),
    _fn(TOOL_PRICE,
        "What ONE product costs at this counter today, including any offer "
        "running on it. Not for a weight — use weighed_price for that.",
        {"product": _PRODUCT_ARG}, ["product"]),
    _fn(TOOL_FIND,
        "Look ONE product up in the shop's catalogue: whether it is taught at "
        "all, what it is called and how it was taught.",
        {"product": _PRODUCT_ARG}, ["product"]),
    _fn(TOOL_WEIGHED,
        "Price a WEIGHT of a product that is sold loose by the kilo — rice, "
        "dal, sugar, atta. Use this whenever the sentence carries a weight or "
        "a half or a quarter: 'aadha kilo chini', '250 gram chal'. It prices "
        "the weight and bills nothing.",
        {"product": _PRODUCT_ARG, "qty": _QTY_ARG, "unit": _UNIT_ARG,
         "fraction": _FRACTION_ARG,
         "kg": {"type": "string",
                "description": ("the weight in kilograms as text, such as "
                                "\"2.5\", if he gave a decimal one. Text, not "
                                "a number: this counter never puts a weight "
                                "through a float.")}},
        ["product"]),

    # ---------------------------------------------------------- the day --
    _fn(TOOL_TAKINGS,
        "What this counter has billed and settled today, counted off the "
        "hash-chained audit log.",
        {}, []),
    _fn(TOOL_DAY_CLOSE,
        "The day-close preview: what today's book would say if the day were "
        "closed now — bills, takings, what settled and the top sellers. It "
        "closes nothing.",
        {}, []),
    _fn(TOOL_ORDERS,
        "List the online orders from the shop's own storefront that are still "
        "open — placed, being prepared, or out for delivery.",
        {}, []),

    # -------------------------------------------------------- the shelf --
    _fn(TOOL_LOW_STOCK,
        "Which counted products are down to their last few units, judged "
        "against a number of units you pass in. For the shopkeeper's OWN "
        "reorder levels use reorder_list instead.",
        {"units": {"type": "integer",
                   "description": ("treat a product as low at or below this "
                                   "many units left. Default 3.")}},
        []),
    _fn(TOOL_REORDER_LIST,
        "Everything at or under the reorder level the shopkeeper set for it, "
        "worst first, with days of cover where there is enough history.",
        {}, []),
    _fn(TOOL_STOCK_ON_HAND,
        "How many of ONE product are on the shelf: the last count, what has "
        "been billed and moved since it, and what that leaves.",
        {"product": _PRODUCT_ARG}, ["product"]),
    _fn(TOOL_STOCK_MOVEMENTS,
        "The log of stock that arrived or left without being billed — "
        "deliveries, breakage, expiry, returns. Newest first. Name a product "
        "to narrow it to one.",
        {"product": _PRODUCT_ARG}, []),
    _fn(TOOL_PROPOSE_MOVEMENT,
        "Write down a stock movement for a person to accept — 'ek carton "
        "Maggi aaya', 'do Pepsi toot gaye'. It moves nothing: the shelf "
        "changes only when somebody accepts it on the stock screen.",
        {"product": _PRODUCT_ARG, "qty": _QTY_ARG,
         "direction": {"type": "string", "enum": ["in", "out"],
                       "description": ("'in' if stock arrived, 'out' if it "
                                       "left without being sold.")},
         "reason": {"type": "string",
                    "description": ("why, in one word from the counter's own "
                                    "list: delivery, customer_return, found, "
                                    "correction for 'in'; breakage, expiry, "
                                    "personal_use, theft, returned_to_supplier, "
                                    "sample, correction for 'out'.")},
         "note": {"type": "string",
                  "description": "anything else he said about it, in his words."}},
        ["product", "direction", "reason"]),
    _fn(TOOL_EXPIRING,
        "Batches going off soon, soonest first, and what they are worth at "
        "the marked price.",
        {"days": {"type": "integer",
                  "description": "how many days ahead to look. Default 7."}},
        []),
    _fn(TOOL_EXPIRED,
        "Batches already past their date with units still on them — the ones "
        "most likely to be sold to somebody by mistake.",
        {}, []),

    # --------------------------------------------------- money going out --
    _fn(TOOL_EXPENSES_TODAY,
        "What the shop has paid out today, totalled and grouped by kind — "
        "rent, wages, tea, transport, repairs.",
        {}, []),
    _fn(TOOL_CASH_POSITION,
        "The cash drawer today: what was counted in, what came in, what went "
        "out, and what should be in it now.",
        {}, []),
    _fn(TOOL_PROPOSE_EXPENSE,
        "Write down an expense for a person to accept — 'chai ka sau rupaye "
        "likho'. It records nothing: the day book changes only when somebody "
        "accepts it on the expenses screen.",
        {"amount_rupees": {
            "type": "string",
            "description": ("the rupee figure the shopkeeper SAID, as text — "
                            "\"100\" or \"120.50\". Text, not a number, "
                            "because a rupee is never a float here. Only ever "
                            "the figure he said out loud; never one you "
                            "worked out.")},
         "category": {"type": "string",
                      "description": ("one word from the counter's own list: "
                                      "rent, electricity, wages, tea, "
                                      "transport, supplies, repairs, stock, "
                                      "other.")},
         "note": {"type": "string",
                  "description": "what it was for, in his own words."}},
        ["amount_rupees", "category"]),
    _fn(TOOL_MARGIN_OF,
        "What ONE product earns per packet: what it sells for here against "
        "what the last recorded purchase cost.",
        {"product": _PRODUCT_ARG}, ["product"]),
    _fn(TOOL_MARGIN_TODAY,
        "What today's trading earned: the margin on what was actually billed, "
        "split into the lines whose cost is known and the lines whose is not.",
        {}, []),
    _fn(TOOL_SUPPLIERS,
        "The suppliers this shop buys from, with their phone numbers.",
        {}, []),

    # ------------------------------------------------------ the counter --
    _fn(TOOL_CUSTOMER,
        "Look ONE customer up by phone number: what they have ordered, what "
        "they have spent and where to send it.",
        {"phone": _PHONE_ARG}, ["phone"]),
    _fn(TOOL_REGULARS,
        "The customers who come back most, ranked by what they have spent.",
        {}, []),
    _fn(TOOL_LOYALTY,
        "One customer's loyalty points and what they are worth, by phone "
        "number.",
        {"phone": _PHONE_ARG}, ["phone"]),
    _fn(TOOL_LOYALTY_RULES,
        "The loyalty scheme itself: points earned per rupee, what a point is "
        "worth, and whether the scheme is switched on at all.",
        {}, []),
    _fn(TOOL_OFFERS,
        "The discounts running at this counter right now and what they apply "
        "to.",
        {}, []),
    _fn(TOOL_CATEGORIES,
        "The shelf categories this shop files its products under.",
        {}, []),
    _fn(TOOL_IN_CATEGORY,
        "The products filed under ONE category, by the category's name.",
        {"category": {"type": "string",
                      "description": ("the category as the shopkeeper said "
                                      "it, in his own words.")}},
        ["category"]),
    _fn(TOOL_GST_OF,
        "The GST rate and HSN heading recorded for ONE product, and the tax "
        "inside its marked price.",
        {"product": _PRODUCT_ARG}, ["product"]),

    # ---------------------------------------------------------- the book --
    _fn(TOOL_KHATA_BOOK,
        "Propose putting the bill on the counter onto ONE customer's udhaar "
        "book (khata) — 'Sharma ji ke khate mein likh do'. It books nothing: "
        "a person presses ON THE BOOK on the till. Never for a stock movement "
        "or an expense.",
        {"customer": _CUSTOMER_ARG}, ["customer"]),
    _fn(TOOL_KHATA_BALANCE,
        "How much ONE customer still owes on their udhaar book (khata), what "
        "was last collected, and whether a collection link is open — 'Sharma "
        "ji ka kitna baaki hai'.",
        {"customer": _CUSTOMER_ARG}, ["customer"]),

    # ---------------------------------------------------------- the bank --
    _fn(TOOL_BANK,
        "How much the gateway paid into the bank for one settlement day, net "
        "of its fees, how many bills matched, and every bill or payment that "
        "did not match, each by name — 'kal bank mein kitna aaya'. UPI "
        "settles T+1, so 'kal' (yesterday) is the usual day. It reads the "
        "gateway's own settlement report; it settles nothing.",
        {"day": {"type": "string",
                 "description": ("'yesterday', 'today', or a date as "
                                 "YYYY-MM-DD. Leave it out for yesterday.")}},
        []),
)

SYSTEM_PROMPT = (
    "You are the routing layer of a small Indian kirana shop's counter. The "
    "shopkeeper speaks or types one sentence, in Hinglish, Hindi, Bengali or "
    "English — often mixed, usually written in Latin script. Choose exactly "
    "ONE tool and fill its arguments from that sentence alone.\n"
    "You have not been given this shop's catalogue, prices, orders, stock, "
    "customers or takings, and you must not invent any of them. Pass the "
    "product words through exactly as the shopkeeper said them, in his own "
    "script and spelling; the counter resolves them against its own "
    "catalogue.\n"
    "Never put a price, a total, a sku id or any figure of your own in an "
    "argument. The one rupee amount you may pass is amount_rupees on "
    "propose_expense, and only when the shopkeeper said that number himself. "
    "Quantities are whole numbers of packets; a weight belongs in "
    "weighed_price.\n"
    "If the sentence names more than one product to add, call add_to_bill "
    "ONCE with every product in 'items'. Do not drop any of them and do not "
    "call the tool twice.\n"
    "If the sentence asks for none of these things, call no tool."
)

#: An argument key containing any of these is the model reaching for a number it
#: was never given. It is refused rather than dropped — see `_check_arguments`.
_MONEY_ARG_WORDS = ("paise", "price", "amount", "total", "rupee", "rs", "cost",
                    "mrp", "discount", "money")

#: The same rule applied to the REQUEST body. The page sends a sentence; it does
#: not send lines, skus or rupees. Invariant: the browser is never an author.
_CLIENT_AUTHOR_KEYS = ("paise", "price", "amount", "total", "rupee", "sku",
                       "lines", "items", "line_items", "bill", "proposal")


# ------------------------------------------------------------------ limits --

MAX_TEXT = 400
MAX_QTY = 99
DEFAULT_LOW_STOCK_UNITS = 3
MAX_LOW_STOCK_UNITS = 999
MAX_PENDING_LISTED = 20
MAX_MATCHES_LISTED = 8

#: How many products one sentence may propose. A shopkeeper reading a proposal
#: back has to be able to check every line of it against what he just said, and
#: past about a dozen he stops. Refused by name rather than truncated: a
#: proposal silently one line short is the exact failure this file exists to
#: avoid, and truncating would reintroduce it under a friendlier name.
MAX_LINES = 12

#: A note rides along on a proposal in the shopkeeper's own words. The cap is
#: stock.py's and expenses.py's own, asked of them at validation time; this is
#: the fallback for when neither module is present to be asked.
MAX_NOTE = 200

DEFAULT_EXPIRY_DAYS = 7
MAX_EXPIRY_DAYS = 365
MAX_LISTED = 12

#: Format 2 adds `kind` and allows more than one line on a bill proposal.
#: Format 1 documents are still readable — they are bill proposals with one
#: line — and `read_proposal` does not reject them, because a proposal written
#: this morning has to still open this afternoon.
PROPOSAL_FORMAT = 2
PROPOSAL_ID_RE = re.compile(r"^prop_[0-9a-f]{12}$")

#: What a proposal is a proposal OF. Each one is accepted on a different screen
#: by a different module, and none of them is accepted here.
KIND_BILL = "bill"
KIND_MOVEMENT = "stock_movement"
KIND_EXPENSE = "expense"

SOURCES = ("text", "voice")


class AssistantRefused(Exception):
    """A named refusal with a reason a human can act on."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class GrokUnavailable(Exception):
    """The provider did not answer usefully. Falls back; never a refusal.

    Deliberately NOT an AssistantRefused. A shop whose internet dropped should
    keep taking sentences on the local parser, and the response says the model
    was not reached rather than pretending it was.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _refusal(exc: AssistantRefused, status: int = 400,
             **extra: Any) -> JSONResponse:
    """The shape every other endpoint in this program answers a refusal with."""
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False, **extra},
        status_code=status,
    )


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400,
    )


# -------------------------------------------------------------- the till --
#
# Imported LATE, inside functions, and found in sys.modules FIRST — the same
# rule and the same reason as gawaah/storefront.py. `make serve` runs
# `uvicorn upload_app:app --app-dir tools`, which registers the module under the
# bare name `upload_app`; the test suite does `from tools import upload_app` and
# registers it as `tools.upload_app`. Importing the other spelling loads a
# SECOND copy of the file with its own catalogue handle, and an assistant
# answering questions about a different shop from the till it is mounted in
# would say nothing about it.

from gawaah import till_ref as _till_ref

#: One definition, in `gawaah/till_ref.py`. This was sixteen copies of a tuple
#: that was missing `__main__`, and every one of them was wrong the same way.
_TILL_NAMES = _till_ref.TILL_NAMES


def _till() -> Any:
    """The already-loaded till module, or a named refusal."""
    import sys

    for name in _TILL_NAMES:
        mod = sys.modules.get(name)
        if mod is not None and _till_ref.is_the_till(mod):
            return mod
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from tools import upload_app  # noqa: WPS433 - deliberately late
    except Exception as exc:  # noqa: BLE001 - a missing till is a named answer
        raise AssistantRefused(
            R_NO_TILL,
            f"tools/upload_app.py is not importable ({type(exc).__name__}: "
            f"{exc}). The assistant reads the shopkeeper's catalogue through "
            f"it and will not keep a second copy of the prices.") from None
    return upload_app


# ------------------------------------------------------- the other modules --
#
# ONE WAY IN, AND A NAME FOR EVERY WAY IT CAN FAIL. Each tool below reaches a
# module that already owns its answer. This is the only place those modules are
# imported, so "what does the assistant depend on" is one dictionary and not a
# grep, and every dependency is optional in the same way: absent is a sentence.

#: alias -> (module file, the refusal when it is not there, what it owns)
_MODULES: dict[str, tuple[str, str, str]] = {
    "stock": ("stock", R_STOCK_MODULE,
              "stock movements, shelf counts and reorder levels"),
    "expenses": ("expenses", R_EXPENSES_UNAVAILABLE,
                 "money paid out and the cash drawer"),
    "purchases": ("purchases", R_PURCHASES_UNAVAILABLE,
                  "suppliers, what stock cost and what it earns"),
    "customers": ("customers", R_CUSTOMERS_UNAVAILABLE,
                  "who has ordered from the storefront"),
    "categories": ("categories", R_CATEGORIES_UNAVAILABLE,
                   "which shelf a product is filed under"),
    "daybook": ("daybook", R_DAYBOOK_UNAVAILABLE,
                "closing the day and freezing its figures"),
    "offers": ("offers", R_OFFERS_UNAVAILABLE, "the discounts running today"),
    "gst": ("gst", R_GST_UNAVAILABLE, "HSN headings and tax rates"),
    "expiry": ("expiry", R_EXPIRY_UNAVAILABLE, "batches and their dates"),
    "loyalty": ("loyalty", R_LOYALTY_UNAVAILABLE, "points and what they buy"),
    "weighed": ("weighed", R_WEIGHED_UNAVAILABLE,
                "products sold loose by the kilo"),
    "khata": ("khata", R_KHATA_UNAVAILABLE,
              "the udhaar book: who owes what, collected by the gateway"),
    "milan": ("milan", R_MILAN_UNAVAILABLE,
              "the settlement match: what the gateway paid into the bank"),
}


def _module(alias: str) -> Any:
    """The gawaah module that owns this question, or a named refusal.

    Imported LATE for the same reason the till is: several of these pull in
    vision or filesystem work at import time, and a counter that cannot answer
    a question about expiry should still be able to answer one about a price.
    """
    import importlib

    name, reason, owns = _MODULES[alias]
    try:
        return importlib.import_module(f".{name}", __package__)
    except Exception as exc:  # noqa: BLE001 - a missing module is an answer
        raise AssistantRefused(
            reason,
            f"gawaah/{name}.py is not importable ({type(exc).__name__}: "
            f"{exc}). It owns {owns}, and this counter will not work that out "
            f"a second way. Nothing was estimated.") from None


def _needs(mod: Any, alias: str, *attrs: str) -> Any:
    """The module, once it is confirmed to still have what is about to be used.

    A module that imports but has been refactored out from under this file is
    the failure that produces a wrong answer rather than an error, so the
    attribute is checked by name before it is reached for.
    """
    name, reason, owns = _MODULES[alias]
    for attr in attrs:
        if not hasattr(mod, attr):
            raise AssistantRefused(
                reason,
                f"gawaah/{name}.py has no {attr!r}, so this counter cannot "
                f"derive the answer the same way the screen derives it. "
                f"Nothing was estimated.")
    return mod


def _decode(resp: Any) -> dict[str, Any]:
    """A module's own JSON answer as a dict, whatever shape it came back in."""
    if isinstance(resp, dict):
        return resp
    body = getattr(resp, "body", None)
    if body is None:
        raise AssistantRefused(
            R_INTERNAL,
            f"a module answered with a {type(resp).__name__}, which is not a "
            f"response this counter can read.")
    return json.loads(bytes(body).decode("utf-8"))


def _ask(alias: str, fn_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Ask a module the question ITS OWN SCREEN asks, and take its answer whole.

    Calling the endpoint function rather than reimplementing what is behind it
    is the point: the figure said out loud here is the identical object the
    screen renders, produced by the identical code path. When it refuses, ITS
    reason and ITS sentence are carried out to the shopkeeper — the module that
    knows what went wrong is the one that gets to name it, and a refusal
    renamed in transit is a refusal nobody can look up.
    """
    name, reason, owns = _MODULES[alias]
    mod = _needs(_module(alias), alias, fn_name)
    try:
        payload = _decode(getattr(mod, fn_name)(*args, **kwargs))
    except AssistantRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - a module that threw is an answer
        raise AssistantRefused(
            reason,
            f"gawaah/{name}.py could not answer ({type(exc).__name__}: "
            f"{exc}). Nothing was estimated in its place.") from None
    if not payload.get("ok"):
        raise AssistantRefused(
            str(payload.get("reason") or reason),
            f"{payload.get('detail') or 'that module refused the question.'} "
            f"(refused by gawaah/{name}.py, which owns {owns})")
    return payload


def shop_dir() -> Path:
    """Where the catalogue lives — the till's own answer, never a second one.

    This is what honours `GAWAAH_SHOP_DIR`. Deriving the path here would be a
    second answer to one question, and the day a test moved the catalogue and
    the assistant stayed behind is the day a harness writes over a live shop.
    """
    return Path(_till().store_dir())


def proposals_dir() -> Path:
    """Proposals live NEXT TO the catalogue they were priced from."""
    return shop_dir() / "assistant"


def audit_path() -> Path:
    """This module's own hash-chained log.

    DELIBERATELY NOT `results/audit.jsonl`. That file is held open by the money
    service in a DIFFERENT PROCESS, which keeps the chain head in memory. A
    second process appending between two of its writes gives it a stale head and
    every line paisa writes afterwards fails `gawaah.ledger.verify` — the money
    audit trail, the one thing here that must be beyond argument, would be the
    casualty. So this gets its own chain, in the shop directory, written by the
    one process that owns it and verifiable by exactly the same `verify()`.

    What it costs when this is wrong: there are more chains to walk than one,
    and a reader who checks only the money log will not see the proposals. That
    is a documentation problem. The alternative was a corrupted money ledger.
    """
    return shop_dir() / "assistant.audit.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """Append one auditable line. Returns the new head, or None if it failed.

    A proposal changes no money and no stock — nothing here can. It is chained
    anyway because it is the step BEFORE money: when a bill is queried later,
    "the assistant proposed these three lines at 19:04 and a person accepted
    them" is the only record that the sentence and the bill were the same thing.

    THE SHOPKEEPER'S SENTENCE IS NOT WRITTEN HERE, only its length and the tool
    it routed to. An audit log is the file most likely to end up in a bug report
    and dictated speech is the field most likely to contain a person's name.
    """
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="assistant", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose an answer
        return None


# -------------------------------------------------------------- catalogue --


def catalogue() -> dict[str, dict[str, Any]]:
    """{sku_id -> name, price_paise, how} for everything this shop can sell.

    `offer_priced_skus()`, the OFFER-AWARE one, for the reason storefront.py
    documents: paisa re-prices every basket through its own book and that book
    applies today's offers, so an assistant quoting the shelf-edge price would
    propose a line the money service will not mint. The number here is the
    number that will be charged; `marked_paise` rides alongside so an answer can
    say the difference was an offer and not a mistake.
    """
    up = _till()
    try:
        return dict(up.offer_priced_skus())
    except AssistantRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - the store may be unreadable
        reason = getattr(exc, "reason", None) or R_NO_CATALOGUE
        detail = getattr(exc, "detail", None) or (
            f"the catalogue could not be read ({type(exc).__name__}: {exc})")
        raise AssistantRefused(reason, detail) from None


# ---------------------------------- reading a Hinglish, Hindi or Bengali line --
#
# THE LOCAL BRAIN. This is what answers when there is no key, and what answers
# when the key is there but the network is not. It is deliberately a WORD LIST
# and an ordering, not a language model: counts and products in three
# languages, and one keyword set per question the tools cover. Every tool but
# `weighed_price` is reachable from it with no key set at all, which is what a
# test asserts — a capability that only exists behind somebody's API key is not
# a capability a kirana has.
#
# It does not guess. Where the list runs out it refuses by name, and says that
# a key would have sent the sentence to the model instead.

#: THREE SCRIPTS SURVIVE TOKENISATION: Latin, Devanagari and Bengali. Each run
#: is its own token, so "2 किलो दूध add karo" comes out as five tokens and not
#: as the two ASCII ones that used to be all this saw.
#:
#: Each block is cut back to its LETTERS. The digits are gone by the time this
#: runs (see `_DIGITS` below), and the danda U+0964 and double danda U+0965 are
#: full stops in both scripts — leaving them in would glue "हुई।" into a token
#: that matches nothing. U+0970/U+0971 (Devanagari abbreviation and spacing
#: signs) and the Bengali currency and fraction signs above U+09F1 go for the
#: same reason: they are punctuation, and the docstring's promise is that
#: punctuation is dropped.
_DEVANAGARI = "ऀ-ॣॲ-ॿ"   # letters, marks; no danda, no digits
_BENGALI = "ঀ-ৣৰৱ"       # letters, marks; no digits, no ৳
_WORD = re.compile(f"[a-z0-9]+|[{_DEVANAGARI}]+|[{_BENGALI}]+")

#: BENGALI AND DEVANAGARI DIGITS ARE TRANSLITERATED, NOT DROPPED.
#: Without this pass "২৫০ gram" would tokenise as ["gram"] and be read as one
#: gram — a wrong answer produced silently, which is worse than any refusal.
_DIGIT_ZEROS = ("০", "०")     # Bengali ০, Devanagari ०
_DIGIT_MAP = {chr(ord(zero) + n): str(n)
              for zero in _DIGIT_ZEROS for n in range(10)}
_DIGITS = str.maketrans(_DIGIT_MAP)

#: ZWNJ and ZWJ are invisible and are how a phone keyboard writes some
#: conjuncts. A token with one buried in it looks identical on screen and
#: matches nothing in the table below, which is the worst kind of miss.
_ZERO_WIDTH = str.maketrans({"\u200c": None, "\u200d": None})

#: THE BROWSER'S SPEECH RECOGNITION RETURNS NATIVE SCRIPT. The counter's mic
#: runs at hi-IN (see DEFAULT_LANG in ui/src/lib/voice.ts) and a shopkeeper who
#: speaks Hindi gets Devanagari back, not Hinglish — so for as long as this file
#: matched Latin only, the assistant this product advertises in three languages
#: refused two of them and said "nothing was said". Typing worked; speaking did
#: not.
#:
#: THIS IS A SPELLING TABLE, NOT A SECOND PARSER. Every value is a token the
#: tables further down ALREADY know — "bottle" is the single exception, and it
#: is a word no table holds in Latin either, so it is carried into the product
#: phrase exactly as a typed "bottle" is. A spoken sentence therefore takes the
#: same path through `local_route` as the typed Hinglish one. There is no
#: per-language branch and no language detection: one table, one parser, and a
#: counter that hears all three languages in one breath still reads them.
#:
#: A NATIVE WORD THAT IS NOT HERE PASSES THROUGH UNCHANGED. It is then treated
#: exactly as an unknown Latin word is — carried into the product phrase and
#: refused by name (R_NO_SUCH_PRODUCT), or dropped as a stray word. That
#: fallback is the point and it must not become a guess: a sentence made of
#: words this table does not have gets a refusal that echoes back what was
#: heard, never the nearest product on the shelf.
#:
#: WHAT IS DELIBERATELY MISSING: the vocabulary here is what a shopkeeper SAYS
#: at a counter — question words, counts, days, the movement verbs, units and
#: the highest-traffic nouns. Brand names are not here and cannot be: "पार्ले जी"
#: is a catalogue lookup, not a translation, and a table that guessed at it
#: would be the parser deciding which product was meant.
SCRIPT_ALIASES: dict[str, str] = {
    # ---- what a question is made of -------------------------------------
    "कितना": "kitna", "कितनी": "kitni", "कितने": "kitne",
    "क्या": "kya", "कौन": "kaun", "कौनसा": "kaunsa", "कौन-सा": "kaunsa",
    "कहाँ": "kothay", "कहां": "kothay", "क्यों": "keno",
    "कैसा": "kemon", "कैसी": "kemon", "कैसे": "kemon",
    "है": "hai", "हैं": "hain", "हुआ": "hua", "हुई": "hui", "हुए": "hue",
    "हो": "ho", "होता": "hota", "होती": "hoti",
    "गया": "gaya", "गए": "gaye", "गये": "gaye", "गयी": "gayi", "गई": "gayi",
    "रहा": "raha", "रही": "rahi", "रहे": "rahe",
    "बताओ": "batao", "बताइए": "bataiye", "दिखाओ": "dikhao",
    "सब": "sab", "सारे": "sare", "कुछ": "kichu",
    "सामान": "saman", "माल": "maal", "चीज": "cheez", "चीज़": "cheez",
    # "कब" and "कैसा" have no Latin spelling in the tables; the Bengali one for
    # the same meaning does, and the tables are one shared vocabulary, so the
    # question word lands where a question word belongs either way.
    "कब": "kobe",
    "কত": "koto", "কতটা": "kotota", "কতগুলো": "kotogulo",
    "কি": "kya", "কী": "kya", "কোন": "kon", "কোনটা": "konta",
    "কবে": "kobe", "কেমন": "kemon", "কোথায়": "kothay", "কেন": "keno",
    "আছে": "ache", "হয়েছে": "hoyeche", "হয়ে": "hoye", "হচ্ছে": "hocche",
    "হলো": "holo", "যাচ্ছে": "jacche", "ছিল": "chilo",
    "এটা": "eta", "ওটা": "ota", "আমাদের": "amader",
    "বলো": "batao", "দেখাও": "dikhao",
    "সব": "sob", "সবগুলো": "sobgulo", "কিছু": "kichu", "জিনিস": "jinis",

    # ---- counts. 13, 14 and 16-19 are absent from NUMBER_WORDS in Latin too,
    # ---- and this layer does not invent what the parser does not know.
    "एक": "ek", "दो": "do", "तीन": "teen", "चार": "char",
    "पाँच": "panch", "पांच": "panch", "छह": "chhah", "छे": "che",
    "सात": "saat", "आठ": "aath", "नौ": "nau", "दस": "das",
    "ग्यारह": "gyarah", "बारह": "barah", "पंद्रह": "pandrah",
    "पन्द्रह": "pandrah", "बीस": "bees", "पच्चीस": "pachees", "तीस": "tees",
    "चालीस": "chalis", "पचास": "pachas", "सौ": "sau",
    "এক": "ek", "একটা": "ekta", "একটি": "ekti",
    "দুই": "dui", "দুটো": "duto", "দুটি": "duti",
    "তিন": "tin", "তিনটে": "tinte", "চার": "char", "পাঁচ": "panch",
    "ছয়": "chhoy", "সাত": "sat", "আট": "aat", "নয়": "noy", "দশ": "dosh",
    "এগারো": "egaro", "বারো": "baro", "পনেরো": "ponero",
    "বিশ": "bis", "কুড়ি": "kuri", "পঁচিশ": "pachees", "ত্রিশ": "tirish",
    "চল্লিশ": "chollish", "পঞ্চাশ": "ponchash", "একশো": "sho",
    "একশ": "sho", "শো": "sho",

    # ---- halves and quarters
    "आधा": "aadha", "आधी": "aadhi", "डेढ़": "dedh", "ढाई": "dhai",
    "सवा": "sava", "पौन": "paune", "पौने": "paune", "पाव": "pav",
    "অর্ধেক": "ordhek", "আধা": "aadha", "পোয়া": "poya", "সিকি": "sikey",
    "দেড়": "dedh", "আড়াই": "dhai",

    # ---- the day. "parso" is not a Latin token the tables know, so the
    # ---- day-before-yesterday is left out rather than mapped onto "kal".
    "आज": "aaj", "कल": "kal", "अभी": "abhi",
    "আজ": "aaj", "আজকে": "ajke", "কাল": "kal", "এখন": "ekhon",

    # ---- putting it on the bill
    "जोड़ो": "jodo", "लिखो": "likho", "डालो": "dalo", "लगाओ": "lagao",
    "चढ़ाओ": "chadhao", "करो": "karo", "कर": "kar", "दे": "de",
    "दीजिए": "dijiye", "चाहिए": "chahiye", "दुकान": "dokan",
    # The honorific. "ji" is already a stopword in Latin; without this line the
    # Devanagari one was carried into the product phrase, and "पारले जी" — how
    # Parle-G is actually SAID — romanised to "parale ji" and matched nothing.
    "जी": "ji",
    "दाल": "dal", "और": "aur", "में": "mein", "का": "ka", "की": "ki",
    "के": "ke", "को": "ko", "कुल": "total",
    "দাও": "dao", "দিন": "din", "দিয়ে": "diye", "নাও": "nao",
    "নিয়ে": "niye", "আনো": "ano", "করুন": "korun", "করে": "kore",
    "লিখো": "likho", "আর": "ar", "এবং": "ebong", "দোকান": "dokan",
    "থেকে": "theke", "একটু": "ektu", "মোট": "total",

    # ---- taking it off the shelf, and why
    "हटाओ": "hatao", "हटा": "hata", "निकालो": "nikalo", "निकाल": "nikal",
    "घटाओ": "ghatao",
    "आया": "aaya", "आयी": "aayi", "आई": "aayi", "आए": "aye",
    "पहुँचा": "pahucha", "पहुंचा": "pahucha",
    "वापस": "wapas", "वापसी": "wapsi", "लौटा": "lauta", "लौटाया": "lautaya",
    "टूटा": "toota", "टूट": "toot", "फेंका": "pheka",
    "चोरी": "chori", "पुराना": "purana", "बासी": "basi", "खराब": "kharab",
    "घर": "ghar", "मुफ्त": "muft", "नमूना": "namuna",
    "खत्म": "khatam", "ख़त्म": "khatam", "खतम": "khatam",
    "बचा": "bacha", "बचे": "bache", "बची": "bachi", "कम": "kam",
    "बिक्री": "bikri", "स्टॉक": "stock",
    "সরাও": "sorao", "কমাও": "komao",
    "এসেছে": "eseche", "এলো": "elo",
    "ফেরত": "ferot", "ভেঙে": "bhenge", "ভেঙেছে": "bhengeche",
    "নষ্ট": "nosto", "মেয়াদ": "meyad", "বাসি": "baashi", "চুরি": "churi",
    "ঘরে": "ghore", "নিজে": "nije",
    "শেষ": "sesh", "ফুরিয়ে": "furiye", "কম": "kom",
    "বিক্রি": "bikri", "বিক্রয়": "bikroy", "স্টক": "stock",

    # ---- how much of it. "bottle" is not in UNIT_WORDS in Latin either; it is
    # ---- carried through to the product phrase, which is where a shelf full of
    # ---- "Coke 500ml bottle" wants it.
    "किलो": "kilo", "ग्राम": "gram", "लीटर": "litre", "पैकेट": "packet",
    "दर्जन": "darjan", "पीस": "piece", "बोतल": "bottle", "डिब्बा": "box",
    "पेटी": "peti", "बोरा": "bora", "कार्टन": "carton",
    "কিলো": "kilo", "কেজি": "kg", "গ্রাম": "gram", "লিটার": "litre",
    "প্যাকেট": "packet", "ডজন": "dozen", "পিস": "piece",
    "বোতল": "bottle", "বাক্স": "box", "পেটি": "peti",

    # ---- what is on the shelf
    "दूध": "doodh", "चावल": "chawal", "आटा": "atta", "मैदा": "maida",
    "सूजी": "suji", "चीनी": "chini", "शक्कर": "shakkar", "नमक": "namak",
    "गेहूँ": "gehu", "गेहूं": "gehu", "मूंग": "moong", "चना": "chana",
    "अंडा": "anda", "अंडे": "ande", "दही": "dahi", "मक्खन": "makkhan",
    "घी": "ghi", "आलू": "aloo", "प्याज": "pyaz", "प्याज़": "pyaz",
    "टमाटर": "tamatar", "सब्जी": "sabji", "सब्ज़ी": "sabji",
    "नारियल": "nariyal", "पानी": "pani", "चाय": "chai", "तेल": "tel",
    "मसाला": "masala", "हल्दी": "haldi", "मिर्च": "mirch", "जीरा": "jeera",
    "धनिया": "dhaniya", "सरसों": "sarson", "तिल": "til",
    "साबुन": "sabun", "अगरबत्ती": "agarbatti", "मोमबत्ती": "mombatti",
    "माचिस": "machis", "झाड़ू": "jhadu", "बाल्टी": "balti",
    "बिस्कुट": "biskut", "काजू": "kaju", "बादाम": "badam",
    "किशमिश": "kishmish", "रोटी": "roti", "सिगरेट": "sigret",
    "দুধ": "dudh", "চাল": "chal", "আটা": "atta", "ময়দা": "moida",
    "সুজি": "suji", "চিনি": "chini", "নুন": "nun", "লবণ": "lobon",
    "গম": "gom", "ডাল": "dal", "মুগ": "moong", "ডিম": "dim", "দই": "doi",
    "মাখন": "makhon", "ঘি": "ghi", "আলু": "alu", "পেঁয়াজ": "peyaj",
    "টমেটো": "tamatar", "সবজি": "sobji", "নারকেল": "narial",
    "জল": "jol", "চা": "cha", "তেল": "tel", "মশলা": "moshla",
    "হলুদ": "holud", "মরিচ": "morich", "জিরে": "jira", "ধনে": "dhone",
    "সরষে": "shorshe", "সাবান": "saban", "ধূপ": "dhoop",
    "মোমবাতি": "mombati", "দেশলাই": "deshlai", "ঝাড়ু": "jharu",
    "বালতি": "balti", "বিস্কুট": "biskut", "কাজু": "kaju",
    "বাদাম": "badam", "কিশমিশ": "kishmish", "রুটি": "roti",

    # ---- the questions that are not about a product
    "दाम": "daam", "कीमत": "kimat", "भाव": "bhav",
    "खर्च": "kharch", "खर्चा": "kharcha", "नकद": "nakad", "गल्ला": "galla",
    "मुनाफा": "munafa", "फायदा": "fayda", "ग्राहक": "grahak",
    "हिसाब": "hisab", "ऑर्डर": "order", "छूट": "chhoot",
    "रुपये": "rupaye", "रुपया": "rupaya", "कमाई": "kamai",
    "ढूंढो": "dhundo", "खोजो": "khojo",
    "দাম": "daam", "মূল্য": "mullo", "দর": "dor", "খরচ": "khoroch",
    "নগদ": "nagad", "লাভ": "lav", "খদ্দের": "khoddar", "হিসাব": "hishab",
    "ছাড়": "chhoot", "টাকা": "taka", "খুঁজে": "khunje", "খোঁজ": "khoj",
}

#: The nukta letters (ज़, ड़, ঢ়, য় …) have two encodings that look identical, and
#: which one arrives depends on the keyboard. NFC picks one — it DECOMPOSES
#: them, because they are Unicode composition exclusions — so both spellings of
#: "হয়েছে" find the same entry. Applied to the keys here and to the sentence in
#: `normalise`, so the two can never drift apart.
SCRIPT_ALIASES = {unicodedata.normalize("NFC", k): v
                  for k, v in SCRIPT_ALIASES.items()}


def normalise(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, with Bengali and Devanagari digits first
    turned into the ASCII digits they are and native-script WORDS turned into
    the Latin token the tables are written in. Punctuation is dropped.

    PURE ASCII IN, IDENTICAL TOKENS OUT. Every key in `SCRIPT_ALIASES` is
    non-ASCII and the two added character classes cannot match an ASCII byte, so
    nothing a shopkeeper types in Hinglish reads any differently than it did
    before this layer existed. There is a test for exactly that.
    """
    prepared = (unicodedata.normalize("NFC", text or "")
                .translate(_ZERO_WIDTH).translate(_DIGITS).lower())
    return [SCRIPT_ALIASES.get(t, t) for t in _WORD.findall(prepared)]


#: HINDI, BENGALI AND ENGLISH IN ONE TABLE, because a counter hears all three in
#: one sentence and a per-language parser would have to guess which sentence it
#: was in before it could read the first word.
#:
#: "at" is Bengali for eight and is also an English preposition. It is here
#: anyway, and what contains it is the position rule in `_split`: a count is
#: only read BEFORE the product, so "at Maggi" is eight Maggi and "Maggi at the
#: counter" leaves "at" in the tail where it is dropped as a stray word. What it
#: costs when this is wrong: a sentence starting with "at" that meant the
#: preposition reads as eight of something, and the proposal says "8" on the
#: screen where a person sees it before accepting.
NUMBER_WORDS: dict[str, int] = {
    # 1
    "ek": 1, "eik": 1, "ekta": 1, "ekti": 1, "one": 1,
    # 2
    "do": 2, "dui": 2, "duto": 2, "duita": 2, "duti": 2, "two": 2,
    # 3
    "teen": 3, "tin": 3, "tinte": 3, "tinti": 3, "three": 3,
    # 4
    "char": 4, "chaar": 4, "charte": 4, "charti": 4, "four": 4,
    # 5
    "panch": 5, "paanch": 5, "pach": 5, "panchta": 5, "five": 5,
    # 6
    "che": 6, "chhe": 6, "chah": 6, "chhah": 6, "chhoy": 6, "choy": 6,
    "chhay": 6, "six": 6,
    # 7
    "sat": 7, "saat": 7, "shat": 7, "seven": 7,
    # 8
    "ath": 8, "aath": 8, "aat": 8, "at": 8, "eight": 8,
    # 9
    "nau": 9, "noy": 9, "nou": 9, "nine": 9,
    # 10
    "das": 10, "dus": 10, "dosh": 10, "doshta": 10, "ten": 10,
    "gyarah": 11, "egaro": 11, "eleven": 11,
    "barah": 12, "baro": 12, "twelve": 12,
    "pandrah": 15, "ponero": 15, "fifteen": 15,
    "bees": 20, "bis": 20, "kuri": 20, "twenty": 20,
    "pachees": 25, "twentyfive": 25,
    "tees": 30, "tirish": 30, "thirty": 30,
    "chalis": 40, "chollish": 40, "forty": 40,
    "pachas": 50, "ponchash": 50, "fifty": 50,
    "sau": 100, "sho": 100, "hundred": 100,
}

#: Halves and quarters. A kirana genuinely sells "sava kilo" of loose rice, and
#: this counter can now price it — but only for a product somebody has marked as
#: sold by weight, through gawaah/weighed.py. For everything else a packet is
#: still a packet and the fraction is refused rather than rounded in either
#: direction.
#:
#: The spellings here are the ones weighed.py knows, plus the Bengali ones it
#: does not; `_canonical_fraction` maps the second group onto the first so that
#: this file never teaches weighed.py a new word behind its back. Keeping one
#: vocabulary in two modules is how "aadha" prices 500 g on one screen and is
#: refused on another.
FRACTION_WORDS = frozenset({
    "aadha", "adha", "aadhi", "adhi", "half", "dedh", "derh",
    "dhai", "dhaai", "sava", "savva", "sawa", "paune", "pauna", "pav",
    "quarter", "pao",
    # Bengali, mapped below onto the spellings above.
    "ordhek", "adhek", "poya", "sikey",
})

#: Bengali fraction word -> the spelling gawaah/weighed.py stores grams against.
_FRACTION_ALIASES: dict[str, str] = {
    "ordhek": "aadha", "adhek": "aadha",
    "poya": "pav", "sikey": "pav",
}


def _canonical_fraction(word: Optional[str]) -> Optional[str]:
    """The fraction as weighed.py spells it, so one vocabulary serves both."""
    if not word:
        return None
    w = str(word).strip().lower()
    return _FRACTION_ALIASES.get(w, w)

#: Units a shopkeeper says out loud. The weight and volume ones cannot be billed
#: as themselves — see WEIGHT_UNITS.
#: The single letters "g" and "l" are DELIBERATELY absent. Products are called
#: things like Parle-G and Amul-L; a unit list that eats a one-letter token eats
#: half the brand names in an Indian kirana, and "ek g namak" is not a sentence
#: anybody says.
#: A carton, a peti, a bora. THESE ARE NOT A NUMBER. Twelve to a carton for one
#: product and forty-eight for the next is what a wholesaler decides, and this
#: counter has never been told. So a packing word is carried through and SAID
#: BACK — "this was read as 1, change it if that is wrong" — rather than
#: multiplied by a figure nobody supplied. A dozen IS twelve everywhere, which
#: is why that one is multiplied and this set is not.
PACK_UNITS = frozenset({
    "carton", "cartons", "peti", "petis", "box", "boxes", "case", "cases",
    "bora", "bori", "boro", "katta", "bundle", "bundles", "crate", "crates",
})

UNIT_WORDS = frozenset({
    "kilo", "kilos", "kg", "kgs", "kilogram", "kilograms",
    "gram", "grams", "gm", "gms",
    "litre", "litres", "liter", "liters", "ltr", "ml",
    "packet", "packets", "pack", "packs", "pouch", "pouches",
    "piece", "pieces", "pcs", "pc", "adad", "nag",
    "dozen", "dozens", "darjan",
}) | PACK_UNITS

WEIGHT_UNITS = frozenset({
    "kilo", "kilos", "kg", "kgs", "gram", "grams", "gm", "gms",
    "litre", "litres", "liter", "liters", "ltr", "ml",
})

DOZEN_UNITS = frozenset({"dozen", "dozens", "darjan"})

#: Words that carry no product. Stripped from around the product phrase.
#:
#: "daal" and "dal" are NOT here even though "daal do" means "put it in": daal
#: is also a food this shop very plausibly sells, and a stopword list that eats
#: the product is worse than one that leaves a stray verb in. The trailing "do"
#: of "daal do" is stripped instead, and a leading numeral is only read before
#: the product (see `_split`), so "Maggi daal do" is one Maggi and not two.
STOP_WORDS = frozenset({
    "add", "adda", "karo", "kardo", "kar", "kro", "do", "de", "dedo",
    "dena", "dijiye", "dijie", "chahiye", "jodo", "jod", "likho", "likh",
    "lagao", "lagado", "chadha", "chadhao", "bill", "bil", "bille", "me",
    "mein", "main", "mai", "ko", "ka", "ki", "ke", "aur", "and",
    "please", "plz", "pls", "zara", "thoda", "bhai", "ji", "yaar", "to", "the",
    "a", "an", "of", "put", "in", "into", "on", "it", "bhaiya",
    "counter", "par", "pe", "wala", "wali", "vala", "vali",
    # Time words. "aaj do Maggi add karo" must not have "aaj" swallow the
    # position where the count is read; the takings question still sees the
    # word, because that check works on the whole sentence.
    "aaj", "kal", "abhi", "today", "now", "aj", "aajke", "ajke", "ekhon",
    # BENGALI. "dao", "din" and "diye" are the same shape as Hindi "de" and are
    # filed the same way: they are how a sentence ENDS, not what it is about.
    # "ta", "ti", "khana" and "gulo" are the counting particles a Bengali
    # speaker attaches to a noun — "sabun ta", "duto sabun" — and leaving them
    # on the product phrase would make "sabunta" fail to match "Lifebuoy".
    "dao", "dio", "din", "diye", "diyo", "nao", "niye", "ano", "ene",
    "korun", "kore", "kori", "korbe", "ta", "ti", "tak", "khana", "khani",
    "gulo", "guli", "gulor", "amake", "amar", "tomar", "dokan",
    "dokane", "theke", "ektu",
})

#: A FORM OF ADDRESS IS NOT PART OF A PRODUCT'S NAME. These are the politeness
#: words out of STOP_WORDS, and they are the only stopwords `resolve_product`
#: strips on its own. The local parser already drops them from a phrase, but
#: the MODEL hands a phrase over as said — "parle ji", which is how Parle-G is
#: pronounced at every counter in the north — and nothing in "Parle-G biscuit"
#: is within an edit of "ji". Dropping "the" or "of" there too was considered
#: and rejected: a catalogue name can legitimately contain those.
HONORIFICS = frozenset({"ji", "bhai", "bhaiya", "please", "plz", "pls", "yaar"})

#: Several products in one breath. This is now the ORDINARY case: the sentence
#: is split here and every part of it is proposed, because proposing all of it
#: and letting a person accept is safer than refusing and far safer than
#: silently billing the first half.
#:
#: The bare Bengali "o" — "dudh o chini" — is DELIBERATELY ABSENT. `normalise`
#: splits "Nestle-O" into ["nestle", "o"], so a one-letter conjunction would eat
#: the tail of a brand name; UNIT_WORDS leaves out "g" and "l" for exactly the
#: same reason. What it costs: a shopkeeper who joins two products with "o"
#: instead of "ar" gets one product phrase reading "dudh o chini", which matches
#: nothing and is refused by name rather than half-billed.
CONJUNCTIONS = frozenset({
    "aur", "and", "plus", "bhi", "or",
    "ar", "aar", "ebong", "aro", "sathe", "saathe",
})

#: "daal do" is the verb "put it in"; "daal" on its own is a food this shop very
#: plausibly sells. The bigram is what tells them apart, and it is only applied
#: when stripping it still leaves a product to add — so "ek kilo daal do" stays
#: an order for dal.
_VERB_BIGRAM_HEADS = frozenset({"daal", "dal", "daala", "dala"})
_VERB_BIGRAM_TAILS = frozenset({"do", "de", "dena", "dijiye", "dijie", "dedo"})

#: A very short bridge from what a shopkeeper says to what an English catalogue
#: is likely to call the same thing. It is a LIST, not a translator, and it is
#: only consulted when the words themselves matched nothing — so a shop whose
#: catalogue is already in Hinglish or Bengali is unaffected by it.
#:
#: EVERY VALUE IS ONE WORD. `resolve_product` widens token by token and then
#: matches tokens, so a two-word value like "gram flour" would become a single
#: token with a space in it and match nothing at all. Where the honest English
#: name is two words, the entry is simply left out rather than shipped broken.
#:
#: "pav" is NOT here as bread, though a Mumbai shop sells it under that name:
#: "pav" already means a quarter in FRACTION_WORDS, and one token cannot be both
#: a weight and a loaf. The fraction wins because "pav kilo chini" is the
#: sentence more likely to reach a counter.
ALIASES: dict[str, str] = {
    # --- staples
    "doodh": "milk", "dudh": "milk", "dood": "milk", "dugdha": "milk",
    "chawal": "rice", "chaval": "rice", "chaawal": "rice",
    "chal": "rice", "chaal": "rice",
    "chini": "sugar", "cheeni": "sugar", "shakkar": "sugar", "chinni": "sugar",
    "namak": "salt", "nun": "salt", "noon": "salt", "lobon": "salt",
    "atta": "flour", "aata": "flour", "maida": "flour", "ata": "flour",
    "moida": "flour", "moyda": "flour",
    "gehu": "wheat", "gehun": "wheat", "gehoon": "wheat", "gom": "wheat",
    "suji": "semolina", "sooji": "semolina",
    "dal": "lentil", "daal": "lentil", "dail": "lentil", "masoor": "lentil",
    "arhar": "lentil", "toor": "lentil", "tur": "lentil",
    "moong": "mung", "chana": "chickpea", "chane": "chickpea",
    # --- fresh
    "anda": "egg", "ande": "egg", "dim": "egg", "deem": "egg",
    "dahi": "curd", "doi": "curd", "makkhan": "butter", "makhan": "butter",
    "makhon": "butter", "ghi": "ghee",
    "aloo": "potato", "alu": "potato",
    "pyaz": "onion", "pyaaz": "onion", "peyaj": "onion", "piyaj": "onion",
    "tamatar": "tomato",
    "sabji": "vegetable", "sobji": "vegetable", "nariyal": "coconut",
    "narial": "coconut",
    # --- drink
    "pani": "water", "paani": "water", "jol": "water", "jal": "water",
    "chai": "tea", "chaay": "tea", "patti": "tea", "cha": "tea",
    "chaa": "tea",
    # --- spice
    "masala": "spice", "masla": "spice", "moshla": "spice",
    "haldi": "turmeric", "holud": "turmeric",
    "mirch": "chilli", "mirchi": "chilli", "morich": "chilli",
    "jeera": "cumin", "jira": "cumin",
    "dhaniya": "coriander", "dhone": "coriander",
    "sarso": "mustard", "sarson": "mustard", "shorshe": "mustard",
    "til": "sesame",
    # --- oil, and the things beside it on the same shelf
    "tel": "oil",
    "sabun": "soap", "saabun": "soap", "saban": "soap",
    "surf": "detergent", "manjan": "toothpaste",
    "agarbatti": "incense", "dhoop": "incense",
    "mombatti": "candle", "mombati": "candle",
    "machis": "matches", "maachis": "matches", "deshlai": "matches",
    "jhadu": "broom", "jhaadu": "broom", "jharu": "broom",
    "balti": "bucket", "baltee": "bucket",
    # --- packets
    "biskut": "biscuit", "biscut": "biscuit", "biskit": "biscuit",
    "kaju": "cashew", "badam": "almond", "kishmish": "raisin",
    "roti": "bread", "sigret": "cigarette", "sigaret": "cigarette",
}

#: Question shapes, in the order they are tried. Order is the whole design:
#: "aaj Maggi ka daam kya hai" must reach the price question and not the
#: takings one, so the more specific keyword sets are checked first.
ORDER_WORDS = frozenset({"order", "orders", "ordar", "ordars", "delivery",
                         "deliveries", "parcel", "parcels"})
STOCK_WORDS = frozenset({"stock", "stok", "khatam", "khatm", "khatham",
                         "bacha", "bache", "bachi", "restock", "low", "kam",
                         "bharna", "inventory", "shelf",
                         # Bengali: finished, running out, fewer
                         "sesh", "furiye", "kome", "kom"})
PRICE_WORDS = frozenset({"price", "prices", "daam", "dam", "damm", "rate",
                         "bhav", "bhaav", "kimat", "keemat", "kimmat", "mrp",
                         "cost", "costs",
                         # Bengali: price, rate
                         "mullo", "mulyo", "dor"})
TAKINGS_WORDS = frozenset({"takings", "bikri", "bikree", "bikroy", "sale",
                           "sales", "kamai", "kamaai", "kamaya", "collection",
                           "galla", "gulla", "revenue", "turnover", "hua",
                           "hui", "total", "aaj", "today", "aajke", "ajke",
                           "rojgar", "amdani", "aamdani"})
FIND_WORDS = frozenset({"find", "search", "dhundo", "dhundho", "dhoondo",
                        "khojo", "khoj", "milega", "milta", "milti", "hai",
                        "catalogue", "catalog", "list", "kaunsa", "konsa",
                        # Bengali: is there, look for
                        "ache", "achhe", "khujo", "khuje", "khunje"})

# ---------------------------------------------------- the wider questions --
#
# One keyword set per tool that the LOCAL parser can reach without the model.
# They are checked in the order `local_route` lists them, narrowest first: a set
# whose words are ordinary — "kitna", "aaj" — placed early would swallow every
# other question in the language.

EXPENSE_WORDS = frozenset({"kharch", "kharcha", "kharche", "expense",
                           "expenses", "spent", "spend", "kharoch", "khoroch",
                           "byay", "vyay", "outgoing"})
CASH_WORDS = frozenset({"cash", "golla", "drawer", "nakad",
                        "nagad", "tijori", "till", "khazana"})
MARGIN_WORDS = frozenset({"margin", "munafa", "munaafa", "profit", "labh",
                          "fayda", "faida", "bachat", "lav"})
SUPPLIER_WORDS = frozenset({"supplier", "suppliers", "wholesaler", "distributor",
                            "sapplier", "mahajan", "arhat", "dealer",
                            "dealers"})
CUSTOMER_WORDS = frozenset({"customer", "customers", "grahak", "graahak",
                            "kreta", "khoddar", "buyer", "regular",
                            "regulars"})
CATEGORY_WORDS = frozenset({"category", "categories", "shreni", "vibhag",
                            "section", "sections", "bhag"})
#: MILAN. The bank and the gateway's settlement, in the words a shopkeeper
#: uses for them. Checked AFTER the expense words — "bank se kharcha" is money
#: paid out, not money that came in — and before the cash drawer, which "bank"
#: is not. "aaya" on its own is a delivery word and stays one; it is the
#: bank word beside it that makes the sentence about settlement.
BANK_WORDS = frozenset({"bank", "baink", "bainc", "settlement", "settlements",
                        "settle", "settled", "recon", "milan", "milaan",
                        "utr", "payout", "gateway", "account"})
_BANK_NATIVE = frozenset({"बैंक", "ब्यांक", "ব্যাংক", "ব্যাংকে", "ব্যাঙ্ক", "ব্যাঙ্কে",
                          "মিলান", "मिलान", "সেটলমেন্ট", "banke"})
#: Which day. "kal" is yesterday for a question about money that arrived;
#: "parso" the day before; "aaj" today. Anything else is yesterday, because
#: UPI settles T+1 and today's report is empty until tomorrow.
_BANK_DAY_WORDS: dict[str, str] = {
    "kal": "yesterday", "kaal": "yesterday", "yesterday": "yesterday",
    "कल": "yesterday", "gotokal": "yesterday", "gatokal": "yesterday",
    "গতকাল": "yesterday", "কাল": "yesterday", "kalke": "yesterday",
    "aaj": "today", "today": "today", "आज": "today", "aajke": "today",
    "ajke": "today", "আজ": "today", "আজকে": "today",
    "parso": "day_before", "parson": "day_before", "परसों": "day_before",
    "porshu": "day_before", "পরশু": "day_before",
}
DAY_CLOSE_WORDS = frozenset({"close", "closing", "bondho", "hisab",
                             "hisaab", "hishab", "daybook", "khata",
                             "khaata"})
OFFER_WORDS = frozenset({"offer", "offers", "discount", "discounts", "chhoot",
                         "chhut", "chut", "scheme"})
GST_WORDS = frozenset({"gst", "tax", "hsn", "vat", "slab", "slabs"})
EXPIRY_WORDS = frozenset({"expiry", "expire", "expires", "expired",
                          "expiring", "purana", "puraana", "meyad", "meyaad",
                          "baashi", "basi"})
LOYALTY_WORDS = frozenset({"loyalty", "points", "point", "reward", "rewards",
                           "ank"})
#: KHATA. The oblique "khate" ("khate mein") and the words for a debt. The
#: bare "khata"/"khaata" is deliberately NOT here: "aaj ka hisab-khata" is the
#: day book, which DAY_CLOSE_WORDS already owns.
KHATA_WORDS = frozenset({"khate", "khaate", "khatey", "khaatey", "udhaar",
                         "udhar", "udhaari", "udhari", "baaki", "baki",
                         "bakaya", "baqaya", "bakaaya", "dhaar", "dhar",
                         "baki", "credit"})
#: Said with a khata word, these make it a WRITE — "likh do", "chadha do".
#: Without one, a khata sentence is a question about the balance.
KHATA_WRITE_WORDS = frozenset({"likh", "likho", "likhdo", "likhna", "likhiye",
                               "lekho", "lekh", "chadha", "chadhao", "chadhado",
                               "chadh", "daal", "daalo", "dalo", "daaldo",
                               "likhe", "write", "put"})
#: Devanagari and Bengali spellings that `normalise` leaves in their script.
_KHATA_NATIVE = frozenset({"खाते", "खाता", "उधार", "बाकी", "बकाया", "ধার", "বাকি",
                           "খাতা", "খাতায়"})
_KHATA_WRITE_NATIVE = frozenset({"लिख", "लिखो", "लिखदो", "चढ़ा", "चढ़ाओ", "डाल",
                                 "डालो", "লেখো", "লিখে", "লিখো"})
#: Particles around a name or a number in a khata sentence. Taken out so
#: "Sharma ji ka kitna baaki hai" looks the customer up as "Sharma ji".
_KHATA_PARTICLES = frozenset({"ka", "ke", "ki", "ko", "mein", "me", "main",
                              "par", "pe", "pai", "hai", "hain", "h", "kitna",
                              "kitni", "kitne", "kya", "koto", "kato", "kot",
                              "do", "de", "dijiye", "dena", "dedo", "diya",
                              "abhi", "aaj", "total", "bill", "batao", "bata",
                              "bolo", "dikhao", "dikha", "ye", "yeh", "is",
                              "us", "iska", "uska", "the", "of", "on", "for",
                              "how", "much", "does", "owe", "owes", "still",
                              "left", "in", "into", "to", "it", "this",
                              "please", "zara", "जी", "का", "के", "की", "में",
                              "है", "कितना", "दो", "এর", "কত", "আছে", "দাও"})
#: A stock movement's REASON is read off the words that name it, never guessed.
#: stock.py keeps a closed vocabulary so that "how much went to breakage this
#: month" is answerable, and a reason this counter invented would be a line in
#: that total nobody said. A sentence that says something moved but not why is
#: refused by name, with the list, rather than filed under a likely one.
#:
#: (direction, stock.py's own reason id, the words that mean it)
_MOVEMENT_REASONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("in", "customer_return", frozenset({
        "wapas", "wapsi", "waapas", "return", "returned", "lauta", "lautaya",
        "ferot", "ferat"})),
    ("in", "delivery", frozenset({
        "aaya", "aya", "aayi", "ayi", "aye", "pohcha", "pahucha", "pohuche",
        "eseche", "esheche", "elo", "arrived", "received", "delivered"})),
    ("out", "expiry", frozenset({
        "expired", "expire", "meyad", "meyaad", "baashi", "basi", "nosto",
        "kharab", "purana", "puraana"})),
    ("out", "theft", frozenset({
        "chori", "churaya", "chura", "churi", "theft", "stolen", "gayeb"})),
    ("out", "personal_use", frozenset({
        "ghar", "ghore", "khud", "nije", "personal", "apne"})),
    ("out", "returned_to_supplier", frozenset({
        "supplier", "mahajan", "distributor", "wholesaler"})),
    ("out", "sample", frozenset({
        "sample", "muft", "free", "namuna"})),
    ("out", "breakage", frozenset({
        "toot", "toota", "tut", "tuta", "toote", "phek", "pheka",
        "fek", "phenk", "bhenge", "bhengeche", "broke", "broken", "wasted",
        "damaged"})),
)

MOVEMENT_IN_WORDS = frozenset().union(
    *[w for d, _r, w in _MOVEMENT_REASONS if d == "in"])
MOVEMENT_OUT_WORDS = frozenset().union(
    *[w for d, _r, w in _MOVEMENT_REASONS if d == "out"])

#: Words that say stock moved and do NOT say why — "do Maggi hatao". They are
#: here so that the sentence is recognised as a movement and then refused for
#: the missing reason, instead of falling through to the bill and proposing two
#: Maggi for sale. A movement with no reason is the one that would quietly ruin
#: "how much went to breakage this month".
MOVEMENT_BARE_WORDS = frozenset({
    "hatao", "hataye", "hata", "hatado", "nikal", "nikalo", "nikala",
    "ghatao", "minus", "komao", "soriye", "sorao", "kamkaro",
})

MOVEMENT_WORDS = MOVEMENT_IN_WORDS | MOVEMENT_OUT_WORDS | MOVEMENT_BARE_WORDS

REORDER_WORDS = frozenset({"reorder", "restock", "mangao", "mangana",
                           "mangwao", "level", "levels"})
MOVEMENT_LOG_WORDS = frozenset({"movement", "movements", "log", "history",
                                "itihas"})

#: Words that only ever mean "put this on the bill". "bill" is NOT one of them:
#: "aaj ka bill total kitna hua" is a question about the day, and a verb list
#: that eats it turns a question into an order for a product called "total".
ADD_VERBS = frozenset({"add", "jodo", "jod", "likho", "likh", "lagao",
                       "chadhao", "daalo", "dalo", "daaldo", "daldo"})


def _number(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def _split(tokens: list[str]) -> dict[str, Any]:
    """A leading count and unit, then the product phrase, then trailing verbs.

    THE COUNT IS ONLY READ BEFORE THE PRODUCT. Hinglish puts the number first —
    "do Maggi" — and a number after the noun is far more likely to be part of the
    packet size or the tail of a verb. That single rule is what keeps
    "Maggi daal do" at one packet instead of two.
    """
    qty: Optional[int] = None
    unit: Optional[str] = None
    fraction: Optional[str] = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in FRACTION_WORDS:
            fraction = t
            i += 1
            continue
        n = _number(t)
        if n is not None:
            if qty is None:
                qty = n
            i += 1
            continue
        if t in UNIT_WORDS:
            if unit is None:
                unit = t
            i += 1
            continue
        if t in STOP_WORDS or t in ADD_VERBS:
            i += 1
            continue
        break
    rest = tokens[i:]
    # The tail is where "daalo", "karo", "packet" and "please" collect. Bare
    # numerals go too — a product named with a lone digit would be lost here,
    # which is the price of not reading "do" at the end as a count.
    product = [t for t in rest
               if t not in STOP_WORDS and t not in UNIT_WORDS
               and t not in FRACTION_WORDS and t not in ADD_VERBS
               and t not in CONJUNCTIONS and _number(t) is None]
    return {"qty": qty, "unit": unit, "fraction": fraction,
            "product": " ".join(product), "tail": rest}


def _lift_verb_bigram(tokens: list[str]) -> Optional[list[str]]:
    """Tokens with a "daal do"-shaped verb's head removed, or None if there is
    none. The tail ("do", "de") is already a stopword and needs no help."""
    for i in range(len(tokens) - 1):
        if tokens[i] in _VERB_BIGRAM_HEADS and tokens[i + 1] in _VERB_BIGRAM_TAILS:
            return tokens[:i] + tokens[i + 1:]
    return None


def _segments(tokens: list[str]) -> list[list[str]]:
    """The sentence cut at its conjunctions. One segment is the normal case.

    "do Maggi aur ek Lifebuoy" -> [["do","maggi"], ["ek","lifebuoy"]].

    This is what replaced the old refusal. Each segment is then read by `_split`
    exactly as a whole sentence used to be, so a two-product sentence gets the
    same count-before-the-product rule, the same units and the same fractions
    that a one-product sentence has always got — rather than a second, weaker
    parser written for the multi case.
    """
    out: list[list[str]] = []
    current: list[str] = []
    for t in tokens:
        if t in CONJUNCTIONS:
            if current:
                out.append(current)
            current = []
            continue
        current.append(t)
    if current:
        out.append(current)
    return out


def _parse_products(tokens: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """Every product the sentence names, in order, and whether a verb said ADD.

    A segment that names nothing — "add karo" on its own, or the tail of a
    sentence that was all verbs — contributes nothing and is not an error. Two
    segments that both name something are two lines.
    """
    found: list[dict[str, Any]] = []
    add_verb = bool(set(tokens) & ADD_VERBS)
    for seg in _segments(tokens) or [[]]:
        parts = _split(seg)
        lifted = _lift_verb_bigram(seg)
        if lifted is not None:
            alt = _split(lifted)
            if alt["product"]:
                # It WAS "daal do", the verb, and not dal the food.
                parts = alt
                add_verb = True
        if parts["product"]:
            found.append(parts)
    return found, add_verb


def _phrases(parsed: list[dict[str, Any]]) -> str:
    return ", ".join(repr(p["product"]) for p in parsed)


#: The furniture of a question in all three languages. Whatever is left after
#: these and the question's own keywords are removed is what the question is
#: ABOUT — a product name, a category, a phone number.
QUESTION_WORDS = frozenset({
    # Hindi / Hinglish
    "kitne", "kitna", "kitni", "kya", "kyaa", "hai", "hain", "he", "h",
    "batao", "bata", "bataiye", "dikhao", "dikha", "dikhaiye", "kaun",
    "kaunse", "konse", "sab", "saare", "sare", "online", "pending",
    "abhi", "kal", "hua", "hui", "hue", "raha", "rahi", "rahe", "gaya",
    "gaye", "gayi", "ho", "hota", "hoti",
    # English
    "much", "how", "many", "what", "which", "who", "whose", "is", "are",
    "was", "were", "for", "me", "my", "shop", "counter", "today", "left",
    "show", "tell", "give", "list", "any", "all", "the", "of", "on", "at",
    # Bengali
    "koto", "kota", "kotota", "kotogulo", "kothay", "kobe", "kemon", "keno",
    "kon", "konta", "konti", "kongulo", "hoyeche", "hoeche", "hocche",
    "hoye", "jacche", "holo", "chilo", "amader", "eta", "ota", "sob",
    "sobgulo", "kichu", "r",
    # The word for "thing" in all three. A shopkeeper asking "which THINGS are
    # finishing" has named no product, and leaving the word on the phrase makes
    # the counter look one up called "jinis" and refuse a fair question.
    "jinis", "jinish", "cheez", "cheeze", "cheezein", "saman", "samaan",
    "maal", "item", "items", "product", "products", "stuff", "thing",
    "things",
})


def _strip_question(tokens: list[str], words: frozenset[str]) -> str:
    """The product phrase left once the question's own words are taken out."""
    drop = (words | STOP_WORDS | UNIT_WORDS | ADD_VERBS | CONJUNCTIONS
            | QUESTION_WORDS)
    return " ".join(t for t in tokens
                    if t not in drop and _number(t) is None)


#: A phone number said out loud or typed. Ten digits is the Indian subscriber
#: number; a leading 0 or 91 is normalised away by customers.py, which owns the
#: rule, so this only has to find the digits in the sentence.
_PHONE_RUN = re.compile(r"\d[\d\s-]{7,}\d")


def _phone_in(text: str) -> Optional[str]:
    """The phone number in a sentence, or None. Not validated here — that is
    customers.py's rule and asking it twice is how the two answers diverge."""
    m = _PHONE_RUN.search((text or "").translate(_DIGITS))
    if m is None:
        return None
    digits = "".join(ch for ch in m.group(0) if ch.isdigit())
    return digits or None


#: A rupee figure written in digits, with at most two decimal places. Read off
#: the RAW sentence and kept as TEXT: `normalise` would split "120.50" into two
#: tokens, and `float("120.50")` is already lossy before anything is stored.
_RUPEE_FIGURE = re.compile(r"\d+(?:\.\d{1,2})?")

RUPEE_WORDS = frozenset({"rupaye", "rupaya", "rupay", "rupee", "rupees",
                         "rupiya", "rupya", "rs", "inr", "taka", "tk",
                         "takar"})


def _rupees_in(text: str, tokens: list[str]) -> Optional[str]:
    """The rupee figure the shopkeeper said, as a STRING, or None.

    Digits first, then the number WORDS — "sau rupaye" is a hundred rupees and
    a parser that only reads digits would refuse a sentence a person said
    perfectly clearly.
    """
    m = _RUPEE_FIGURE.search((text or "").translate(_DIGITS))
    if m is not None:
        return m.group(0)
    for t in tokens:
        n = NUMBER_WORDS.get(t)
        if n is not None:
            return str(n)
    return None


def _movement_reason(words: set[str]) -> Optional[tuple[str, str]]:
    """(direction, reason) if the sentence names one, else None."""
    for direction, reason, vocabulary in _MOVEMENT_REASONS:
        if words & vocabulary:
            return direction, reason
    return None


def local_route(text: str) -> tuple[str, dict[str, Any]]:
    """The deterministic parser: a sentence in, a tool name and arguments out.

    Raises AssistantRefused when it does not understand. It is NOT allowed to
    guess: a shopkeeper who gets a wrong line on a bill because a parser picked
    the likeliest of two products is worse off than one who is told to say it
    again.

    ORDER IS THE WHOLE DESIGN, and it runs narrowest first. A check whose words
    are ordinary — "kitna", "aaj", "total" — placed early would swallow every
    other question in three languages, so those go last. The two shapes that
    need a PRODUCT AND A COUNT, a stock movement and a bill line, are matched on
    that shape rather than on a keyword, which is why they can sit near the top
    without eating anything.
    """
    tokens = normalise(text)
    if not tokens:
        raise AssistantRefused(
            R_NO_TEXT, "nothing was said, so there is nothing to do.")
    words = set(tokens)
    parsed, add_verb = _parse_products(tokens)

    # A MOVEMENT IS A PRODUCT, A COUNT AND A WORD SAYING WHICH WAY. All three
    # are required. Without the count "kaunsa maal purana ho raha hai" would
    # read as one packet of something going out, which is a claim nobody made.
    if parsed and parsed[0]["qty"] is not None and (words & MOVEMENT_WORDS):
        if len(parsed) > 1:
            raise AssistantRefused(
                R_SEVERAL_PRODUCTS,
                f"that names more than one product — {_phrases(parsed)} — and a "
                f"stock movement is written against one product at a time. Say "
                f"them one after the other. Nothing was written down.")
        named = _movement_reason(words)
        if named is None:
            raise AssistantRefused(
                R_BAD_MOVEMENT_REASON,
                "that says stock moved but not why, and this counter does not "
                "file a movement under a reason nobody gave. Say what happened "
                "— it arrived, a customer returned it, it broke, it expired, "
                "it was taken for the house, it went back to the supplier.")
        direction, reason = named
        # The words that said WHY come out of the product phrase: "ek carton
        # Maggi aaya" is a movement of Maggi, not of "carton maggi aaya".
        product = _strip_question(tokens, MOVEMENT_WORDS)
        if not product:
            raise AssistantRefused(
                R_NO_PRODUCT_NAMED,
                "that says stock moved but does not name what moved. Say the "
                "product. Nothing was written down.")
        args: dict[str, Any] = {"product": product, "direction": direction,
                                "reason": reason,
                                "qty": int(parsed[0]["qty"])}
        if parsed[0]["unit"] in PACK_UNITS:
            args["unit"] = str(parsed[0]["unit"])
        return TOOL_PROPOSE_MOVEMENT, args

    # --- THE BOOK. Before the add verbs and before the expense words, because
    # "Sharma ji ke khate mein likh do" carries "likh do" and would otherwise
    # be read as a bill line for a product called "sharma ji ke khate". A
    # khata word with a write verb proposes ON THE BOOK; without one it asks
    # the balance. The customer is what is left of the sentence once the
    # khata words and the particles around a name are taken out — a phone
    # number in the sentence wins over a name, because a number is one
    # household and a name can be two.
    if (words & KHATA_WORDS) or (words & _KHATA_NATIVE):
        phone = _phone_in(text)
        if phone:
            customer = phone
        else:
            drop = (KHATA_WORDS | _KHATA_NATIVE | KHATA_WRITE_WORDS
                    | _KHATA_WRITE_NATIVE | _KHATA_PARTICLES)
            customer = " ".join(t for t in tokens if t not in drop)
        if not customer:
            raise AssistantRefused(
                R_NO_CUSTOMER_NAMED,
                "that is about the udhaar book but it does not say whose. Say "
                "the customer's name or number — 'Sharma ji ka kitna baaki "
                "hai', 'Sharma ji ke khate mein likh do'. Nothing was done.")
        if (words & KHATA_WRITE_WORDS) or (words & _KHATA_WRITE_NATIVE):
            return TOOL_KHATA_BOOK, {"customer": customer}
        return TOOL_KHATA_BALANCE, {"customer": customer}

    # --- money going out. Checked before the add verbs, because "chai ka
    # kharcha likho" carries "likho" and names a product, and it is money the
    # shop paid out rather than a packet of tea going onto somebody's bill.
    if words & EXPENSE_WORDS:
        amount = _rupees_in(text, tokens)
        if amount is not None:
            return TOOL_PROPOSE_EXPENSE, {"amount_rupees": amount,
                                          "category": _expense_category(words),
                                          "note": " ".join(tokens)}
        return TOOL_EXPENSES_TODAY, {}
    # MILAN. "kal bank mein kitna aaya" — what the gateway paid into the bank.
    # After the expense check (see BANK_WORDS) and before the drawer.
    if (words & BANK_WORDS) or (words & _BANK_NATIVE):
        day = "yesterday"
        for w in tokens:
            if w in _BANK_DAY_WORDS:
                day = _BANK_DAY_WORDS[w]
                break
        return TOOL_BANK, {"day": day}
    if words & CASH_WORDS:
        return TOOL_CASH_POSITION, {}

    # Below the expense check on purpose: "delivery ka kharcha" is money the
    # shop paid out, not an order somebody placed, and ORDER_WORDS would
    # otherwise take it because "delivery" is in both vocabularies.
    if words & ORDER_WORDS:
        return TOOL_ORDERS, {}

    # --- the named questions, each about one thing
    if words & LOYALTY_WORDS:
        phone = _phone_in(text)
        if phone:
            return TOOL_LOYALTY, {"phone": phone}
        return TOOL_LOYALTY_RULES, {}
    if words & GST_WORDS:
        product = _strip_question(tokens, GST_WORDS)
        if not product:
            raise AssistantRefused(
                R_NO_PRODUCT_NAMED,
                "that is a question about tax but it does not name a product. "
                "A rate is recorded per product — say which one.")
        return TOOL_GST_OF, {"product": product}
    if words & EXPIRY_WORDS:
        # "expired" is the past tense and means the packets already gone off;
        # everything else in that set is the question about what is coming.
        if "expired" in words:
            return TOOL_EXPIRED, {}
        return TOOL_EXPIRING, {}
    if words & OFFER_WORDS:
        return TOOL_OFFERS, {}
    if words & MARGIN_WORDS:
        product = _strip_question(tokens, MARGIN_WORDS)
        if product:
            return TOOL_MARGIN_OF, {"product": product}
        return TOOL_MARGIN_TODAY, {}
    if words & SUPPLIER_WORDS:
        return TOOL_SUPPLIERS, {}
    if words & CUSTOMER_WORDS:
        phone = _phone_in(text)
        if phone:
            return TOOL_CUSTOMER, {"phone": phone}
        return TOOL_REGULARS, {}
    if words & CATEGORY_WORDS:
        category = _strip_question(tokens, CATEGORY_WORDS)
        if category:
            return TOOL_IN_CATEGORY, {"category": category}
        return TOOL_CATEGORIES, {}
    if words & DAY_CLOSE_WORDS:
        return TOOL_DAY_CLOSE, {}

    # --- the shelf. The movement log and the reorder list are asked FIRST,
    # because both of their words also appear in a general stock question and
    # the more specific reading is the one somebody meant by saying them.
    _shelf = STOCK_WORDS | MOVEMENT_LOG_WORDS | REORDER_WORDS
    if words & _shelf:
        product = _strip_question(tokens, _shelf)
        if words & MOVEMENT_LOG_WORDS:
            return TOOL_STOCK_MOVEMENTS, (
                {"product": product} if product else {})
        if words & REORDER_WORDS:
            return TOOL_REORDER_LIST, {}
        if product:
            return TOOL_STOCK_ON_HAND, {"product": product}
        return TOOL_LOW_STOCK, {}

    # --- a price. A question is about ONE product, so two of them is the
    # refusal that used to cover the add case as well.
    if words & PRICE_WORDS:
        if len(parsed) > 1:
            raise AssistantRefused(
                R_SEVERAL_PRODUCTS,
                f"that asks the price of more than one product — "
                f"{_phrases(parsed)}. This counter answers a price question "
                f"about one product at a time, so that the answer cannot be "
                f"read against the wrong name. Ask them one after the other.")
        product = _strip_question(tokens, PRICE_WORDS)
        if not product:
            raise AssistantRefused(
                R_NO_PRODUCT_NAMED,
                "that is a question about a price but it does not name a "
                "product. Say the product too — 'Maggi ka daam kya hai'.")
        return TOOL_PRICE, {"product": product}

    # A RUPEE FIGURE NEVER BECOMES A BILL LINE. "Maggi 12 rupaye ka add karo"
    # is a shopkeeper telling the counter a price, and this counter charges the
    # catalogue's price or it charges nothing — the money service re-prices
    # every basket from its own tables and would refuse the mint anyway. Said
    # out loud here rather than silently ignored, because a line that quietly
    # came out at a different number is the bill nobody checks.
    if (words & RUPEE_WORDS) and parsed:
        raise AssistantRefused(
            R_SPOKEN_PRICE,
            f"that names a product and a rupee figure. A price said out loud "
            f"is not what this counter charges — the catalogue's price is, and "
            f"the money service re-prices the whole bill from its own tables "
            f"before it mints anything. Say just the count ('do Maggi'), change "
            f"the price on the Products screen, or say 'kharcha' if you meant "
            f"money the shop paid out. Nothing was proposed.")

    # --- the bill. A sentence that names products is proposed IN FULL.
    if add_verb and parsed:
        return TOOL_ADD, _add_args(parsed)
    if words & TAKINGS_WORDS:
        return TOOL_TAKINGS, {}
    # A COUNT, A UNIT OR A FRACTION IN FRONT OF A PRODUCT IS AN ORDER FOR IT.
    # "aadha kilo chawal" carries no number at all and is plainly not a
    # question about whether rice is taught; the unit and the fraction are only
    # ever set when they came BEFORE the product, which is what makes this safe
    # for a bare "Maggi packet" — that one still reads as a question.
    if parsed and any(p["qty"] is not None or p["unit"] or p["fraction"]
                      for p in parsed):
        return TOOL_ADD, _add_args(parsed)
    if words & FIND_WORDS:
        if len(parsed) > 1:
            raise AssistantRefused(
                R_SEVERAL_PRODUCTS,
                f"that asks about more than one product — {_phrases(parsed)}. "
                f"This counter looks one product up at a time. Ask them one "
                f"after the other, or say 'add' to put them all on a bill.")
        product = _strip_question(tokens, FIND_WORDS)
        if product:
            return TOOL_FIND, {"product": product}
    if len(parsed) == 1:
        # A bare product name is a question about that product, not an order
        # for one. Adding it would be the parser deciding to spend money.
        return TOOL_FIND, {"product": parsed[0]["product"]}
    if len(parsed) > 1:
        # Two or more bare product names is not a question about one product,
        # and there is no tool that looks several up at once. Proposing them
        # costs nothing — a proposal is paper — and it is what he meant.
        return TOOL_ADD, _add_args(parsed)

    raise AssistantRefused(
        R_NOT_UNDERSTOOD,
        f"this counter's own parser did not understand {text.strip()!r}. It "
        f"knows counts and products ('do Maggi aur ek sabun'), and questions "
        f"about orders, takings, stock, expenses, cash, margins, suppliers, "
        f"customers, offers, categories, tax, expiry, loyalty and a price. "
        f"With XAI_API_KEY set it would send the sentence to the model "
        f"instead.")


#: Which of expenses.py's own categories a sentence is talking about. Only the
#: words that name one count; anything else is "other", which is what
#: expenses.py itself tells a shopkeeper to use when none of them fits.
_EXPENSE_CATEGORY_WORDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("rent", frozenset({"rent", "kiraya", "kiraaya", "bhara", "bhada"})),
    ("electricity", frozenset({"bijli", "electricity", "current", "bidyut",
                               "meter"})),
    ("wages", frozenset({"salary", "wages", "tankhwah", "tankha", "mazdoori",
                         "maina", "beton"})),
    ("tea", frozenset({"chai", "tea", "cha", "nashta", "khana", "snack",
                       "tiffin"})),
    ("transport", frozenset({"transport", "auto", "tempo", "gaadi", "gadi",
                             "petrol", "diesel", "rickshaw"})),
    ("supplies", frozenset({"thaila", "thela", "polythene", "packing", "bag",
                            "bags", "carry"})),
    ("repairs", frozenset({"repair", "repairs", "marammat", "mistri",
                           "sarai", "fix"})),
    ("stock", frozenset({"stock", "maal", "saman", "samaan", "goods"})),
)


def _expense_category(words: set[str]) -> str:
    for category, vocabulary in _EXPENSE_CATEGORY_WORDS:
        if words & vocabulary:
            return category
    return "other"


def _add_args(parsed: list[dict[str, Any]]) -> dict[str, Any]:
    """The arguments for `add_to_bill`: one product, or a list of them.

    A single product keeps the flat shape it has always had, so a page or a
    test that reads `arguments["product"]` still works and the common case
    stays the simple one on the screen.
    """
    items = [_one_item(p) for p in parsed]
    if len(items) == 1:
        return items[0]
    return {"items": items}


def _one_item(parts: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {"product": parts["product"]}
    if parts["qty"] is not None:
        item["qty"] = int(parts["qty"])
    if parts["unit"]:
        item["unit"] = str(parts["unit"])
    if parts["fraction"]:
        item["fraction"] = str(parts["fraction"])
    return item


# ------------------------------------------------------- resolving a product --


def _norm_name(s: str) -> list[str]:
    return normalise(s)


#: How near a romanised word has to be to a catalogue word before this counter
#: will merely NAME it in a refusal. Wider than the budget it RESOLVES on, and
#: it never picks anything — see `_suggestions` for why the two are different
#: numbers and what would be wrong with one.
SUGGEST_EDITS = 2
#: Below this length a suggestion is not offered at all. Two edits on a
#: three-letter word reaches most of a catalogue, and a suggestion that could be
#: anything is not a suggestion.
SUGGEST_MIN_LEN = 4
MAX_SUGGESTED = 2


def resolve_product(phrase: str, known: dict[str, dict[str, Any]]) -> str:
    """The one sku those words mean, or a named refusal. Never a best guess.

    Seven passes, narrowest first, and each one runs only when everything above
    it matched nothing — so a shop whose catalogue is already in Hinglish is
    never dragged through a translation it did not need, and a sentence typed in
    latin never reaches the romanised passes at all.

        1-4  the words as said, through `_match`: sku, name, whole word, prefix.
        5    the same four, with `ALIASES` widening "doodh" to "milk".
        6    THE SAME FOUR AGAIN, on the native-script words spelt out in latin.
             This is what finds `ponds` when the recogniser returned `पॉन्ड्स`.
        7    a bounded edit-distance pass on the FIRST of those latin
             spellings, under search.py's own typo budget — the one a typed
             query already gets. This is what finds `derma` for `डर्मा`, which
             spells out as "darma".

    WHY 6 AND 7 ARE NOT A SECOND PARSER. They do not widen what a sentence can
    MEAN; they only change how one word is SPELT before it is compared with a
    catalogue this file cannot know the contents of. `SCRIPT_ALIASES` cannot do
    this job: it is a fixed table of what a shopkeeper says, and the name of a
    product is whatever the shop typed into the Products screen last week.

    AND WHAT THEY STILL REFUSE. `धर्म` is not a misspelling of `derma` — it is a
    different Hindi word the speech service decided it heard, and it romanises
    to "dharm"/"dharma", which is two edits from "derma" and outside the budget
    pass 7 resolves on. So it is REFUSED, and the refusal names "derma" as a
    thing the shopkeeper may have meant. See `_suggestions`.
    """
    q = normalise(phrase)
    if not q:
        raise AssistantRefused(
            R_NO_PRODUCT_NAMED,
            "no product was named, so there is nothing to look up.")
    # Pass 0: "parle ji" is Parle-G with a form of address on it. Dropped only
    # when a product word survives — a phrase that IS an honorific is left
    # whole and refused by name below, not emptied into a different refusal.
    # A refusal still echoes the words AS SAID (`said`), so a typed Latin
    # sentence reads back exactly as it did before this pass existed.
    said = list(q)
    bare = [t for t in q if t not in HONORIFICS]
    if bare and bare != q:
        q = bare
    if not known:
        raise AssistantRefused(
            R_EMPTY_CATALOGUE,
            "this counter has not been taught any product with a price yet, so "
            "there is nothing to match against. Teach one on the Products "
            "screen first.")

    hits = _match(q, known)
    if not hits:
        widened = [ALIASES.get(t, t) for t in q]
        if widened != q:
            hits = _match(widened, known)
    spellings: list[list[str]] = []
    if not hits:
        # Only now, and never for a phrase that already found its product: the
        # romaniser is not consulted on the path a working sentence takes.
        spellings = _romanised(q)
        for spelt in spellings:
            hits = _match(spelt, known)
            if hits:
                break
        if not hits and spellings:
            hits = _near(spellings[0], known, bonus=0)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        shown = ", ".join(f"{known[s].get('name') or s} ({s})"
                          for s in hits[:MAX_MATCHES_LISTED])
        raise AssistantRefused(
            R_AMBIGUOUS,
            f"{' '.join(said)!r} matches {len(hits)} products in this shop — "
            f"{shown}. Say which one; nothing was added.")
    on_sale = ", ".join(sorted(str(v.get("name") or k)
                               for k, v in known.items())[:6])
    raise AssistantRefused(
        R_NO_SUCH_PRODUCT,
        f"this shop has nothing called {' '.join(said)!r}."
        f"{_suggestions(said, spellings, known)} It sells: {on_sale}"
        f"{'…' if len(known) > 6 else ''}. Teach the product first, or say it "
        f"the way it is written in the catalogue.")


def _romanised(q: list[str]) -> list[list[str]]:
    """The token list respelt in latin, once per spelling `romanise` offers.

    EMPTY FOR A PURE-LATIN PHRASE, and that is the whole safety argument: no
    token of "do maggi add karo" has a Devanagari or Bengali letter in it, so
    `romanise` returns nothing for every one of them, this returns [], and the
    two passes it feeds never execute. A typed sentence takes exactly the path
    it took before any of this was written.

    Two lists come back at most — every word with its word-final unwritten vowel
    dropped, then every word with it kept — rather than a combination per word.
    A product phrase is one or two words at a counter, both spellings are
    ordinary transliterations of the same letters, and enumerating 2^n of them
    would be this function deciding which mixture was meant.
    """
    variants = [romanise(t) for t in q]
    if not any(variants):
        return []
    out: list[list[str]] = []
    for which in (0, -1):
        spelt = [v[which] if v else t for t, v in zip(q, variants)]
        if spelt != q and spelt not in out:
            out.append(spelt)
    return out


def _near(q: list[str], known: dict[str, dict[str, Any]], *, bonus: int
          ) -> list[str]:
    """Skus every one of these words lands on, within an edit budget.

    `edit_distance` and `_max_edits` are search.py's, imported and not copied:
    the tolerance a spoken word gets here has to be the same tolerance a typed
    one gets in the search box, or this counter has two different opinions about
    what "close" means. EVERY word must land, exactly as in `_match` — a phrase
    where one word matched and the other did not has not been understood.
    """
    budget = {t: _max_edits(t) + bonus for t in q}
    hits: list[str] = []
    for sku, rec in known.items():
        haystack = (set(_norm_name(str(rec.get("name") or "")))
                    | set(normalise(sku)))
        if all(any(edit_distance(t, h, budget[t]) <= budget[t]
                   for h in haystack) for t in q):
            hits.append(sku)
    return sorted(hits)


def _suggestions(q: list[str], spellings: list[list[str]],
                 known: dict[str, dict[str, Any]]) -> str:
    """The sentence a refusal adds when a native word came CLOSE to a product.

    THE DECISION THIS ENCODES, because it is the whole point of the function.
    The recogniser hears "derma" and returns `धर्म` — dharma, a real Hindi word
    that sounds like it. That is not a misspelling this counter can correct: it
    is a DIFFERENT WORD, and the only thing linking it to the soap on the shelf
    is that the two sound alike across two scripts. There were two honest
    options and one dishonest one:

      - Resolve it silently. REJECTED, and it is the failure this whole module
        exists to prevent: the counter would put a product on the bill that the
        shopkeeper never named, and the only evidence would be a line he has to
        notice. Money moved on a guess is the one thing that must not happen.
      - Refuse, and say only what was heard. Honest, and what shipped before —
        but it leaves a shopkeeper who said the right word staring at a refusal
        with no idea that the product is one syllable away.
      - REFUSE, AND NAME WHAT IT MIGHT HAVE BEEN. Chosen. Nothing is resolved,
        nothing is proposed, the refusal keeps its name and still echoes the
        word as it was heard. The counter adds one sentence: I heard this, I
        read it as this in latin, did you mean that one? A product a PERSON then
        names is not the parser guessing — the confirmation is the whole
        difference, and it is a person's to give.

    The budget is `SUGGEST_EDITS` and it is deliberately wider than the one
    `resolve_product` resolves on. That is safe precisely BECAUSE it decides
    nothing: a loose suggestion costs a wasted glance, and a loose match costs a
    bill. Two numbers, because they are two different risks.
    """
    if not spellings:
        return ""
    spelt = spellings[0]
    if all(len(t) < SUGGEST_MIN_LEN for t in spelt):
        return ""
    near: list[str] = []
    for candidate in spellings:
        for sku in _near(candidate, known, bonus=SUGGEST_EDITS):
            if sku not in near:
                near.append(sku)
    if not near:
        return ""
    named = ", ".join(f"{known[s].get('name') or s} ({s})"
                      for s in near[:MAX_SUGGESTED])
    return (f" I heard {' '.join(q)!r}, which is {' '.join(spelt)!r} in latin "
            f"letters — did you mean {named}? Nothing was chosen: say or type "
            f"that name and this counter will look it up.")


def _match(q: list[str], known: dict[str, dict[str, Any]]) -> list[str]:
    """Candidate skus for these tokens. Empty, one, or several — never sorted
    by a confidence this module cannot measure."""
    joined = " ".join(q)

    exact_sku = [s for s in known if " ".join(normalise(s)) == joined]
    if exact_sku:
        return sorted(exact_sku)

    exact_name = [s for s, r in known.items()
                  if " ".join(_norm_name(str(r.get("name") or ""))) == joined]
    if exact_name:
        return sorted(exact_name)

    # Every word said appears as a whole word in the name or the sku id.
    whole: list[str] = []
    for s, r in known.items():
        haystack = set(_norm_name(str(r.get("name") or ""))) | set(normalise(s))
        if all(t in haystack for t in q):
            whole.append(s)
    if whole:
        return sorted(whole)

    # Every word said is the START of a word in the name or the sku id. This is
    # what turns "maggi" into "Maggi Noodles 70g" and "parle" into "Parle-G".
    prefix: list[str] = []
    for s, r in known.items():
        haystack = _norm_name(str(r.get("name") or "")) + normalise(s)
        if all(any(h.startswith(t) for h in haystack) for t in q):
            prefix.append(s)
    return sorted(prefix)


def _whole_number(value: Any, *, what: str, reason: str) -> int:
    """An integer, or a named refusal. A string of digits is accepted because a
    model routinely emits "2" where the schema said integer; a float never is."""
    if isinstance(value, bool):
        raise AssistantRefused(
            reason, f"{what} came through as {value!r}, which is not a count.")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, str):
        word = _number(value.strip().lower())
        if word is not None:
            return int(word)
    raise AssistantRefused(
        reason,
        f"{what} came through as {value!r}. It has to be a whole number — "
        f"this counter bills whole packets and cannot hand over part of one.")


# ------------------------------------------------------- executing a tool --


def _priced(rec: dict[str, Any]) -> int:
    """The integer paise this counter will charge for one of these.

    `paise()` rejects a float, a bool and anything non-integral. If a catalogue
    on disk ever held 21.45 instead of 2145, this is where the answer stops
    rather than where a rupee becomes approximate.
    """
    return int(paise(rec["price_paise"]))


def _line_for(sku_id: str, rec: dict[str, Any], qty: int) -> dict[str, Any]:
    unit_paise = _priced(rec)
    line_paise = unit_paise * qty
    line: dict[str, Any] = {
        "sku_id": sku_id,
        "name": str(rec.get("name") or sku_id),
        "qty": int(qty),
        "unit_paise": unit_paise,
        "unit_rupees": to_rupees_str(paise(unit_paise)),
        "line_paise": line_paise,
        "line_rupees": to_rupees_str(paise(line_paise)),
        "taught_with": str(rec.get("how") or "unknown"),
    }
    off = rec.get("off_paise")
    marked = rec.get("marked_paise")
    if not isinstance(off, bool) and isinstance(off, int) and off > 0 \
            and not isinstance(marked, bool) and isinstance(marked, int):
        marked_paise = int(paise(marked))
        if marked_paise > unit_paise:
            line["marked_paise"] = marked_paise
            line["marked_rupees"] = to_rupees_str(paise(marked_paise))
            line["off_paise"] = int(paise(off))
    return line


def _valid_proposal_id(proposal_id: str) -> str:
    """Checked against a strict charset BEFORE it is joined to a path.

    The id becomes a filename. This is what stops a request for `../../catalog`
    reading the shopkeeper's price list.
    """
    s = (proposal_id or "").strip()
    if not PROPOSAL_ID_RE.match(s):
        raise AssistantRefused(
            R_BAD_PROPOSAL_ID,
            f"{proposal_id!r} is not a proposal id from this counter. They look "
            f"like 'prop_' followed by twelve hex characters.")
    return s


def _write_proposal(doc: dict[str, Any]) -> None:
    """Write via a temp file and rename, so a reader never sees half of one."""
    d = proposals_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{doc['proposal_id']}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def read_proposal(proposal_id: str) -> dict[str, Any]:
    p = proposals_dir() / f"{_valid_proposal_id(proposal_id)}.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AssistantRefused(
            R_NO_PROPOSAL,
            f"this counter has no proposal {proposal_id!r}. Nothing was "
            f"changed.") from None
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        raise AssistantRefused(
            R_NO_PROPOSAL,
            f"proposal {proposal_id!r} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}).") from None
    if not isinstance(doc, dict):
        raise AssistantRefused(
            R_NO_PROPOSAL, f"proposal {proposal_id!r} is not a proposal.")
    return doc


def _requested_items(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines the sentence asked for. One, or several. Never zero.

    Both shapes are accepted because both are said: `product` for the ordinary
    one-product sentence, and `items` for "do Maggi aur ek sabun". A model that
    sends a bare list of strings — ["Maggi", "sabun"] — is understood too,
    because that is what models actually emit and refusing it would lose a
    sentence a person said perfectly well.
    """
    raw = args.get("items")
    out: list[dict[str, Any]] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            if isinstance(entry, str):
                out.append({"product": entry})
            elif isinstance(entry, dict):
                out.append(dict(entry))
            else:
                raise AssistantRefused(
                    R_BAD_TOOL_ARGS,
                    f"one entry in 'items' is a {type(entry).__name__}, which "
                    f"names no product. Nothing was proposed.")
    elif raw is not None:
        raise AssistantRefused(
            R_BAD_TOOL_ARGS,
            f"'items' came through as a {type(raw).__name__}; it has to be a "
            f"list of products. Nothing was proposed.")

    if args.get("product"):
        # A flat product ALONGSIDE items is the model saying the same sentence
        # twice. Kept in order and de-duplicated below rather than dropped.
        out.insert(0, {k: args.get(k) for k in
                       ("product", "qty", "unit", "fraction")
                       if args.get(k) is not None})
    if not out:
        raise AssistantRefused(
            R_NO_PRODUCT_NAMED,
            "no product was named, so there is nothing to put on a bill.")
    if len(out) > MAX_LINES:
        raise AssistantRefused(
            R_TOO_MANY_LINES,
            f"that names {len(out)} products and this counter proposes at most "
            f"{MAX_LINES} in one go — past that nobody checks the list before "
            f"accepting it. Say them in two goes. Nothing was proposed.")
    return out


def _sold_by_weight() -> dict[str, int]:
    """{sku_id -> price per kilo in paise} for the loose goods, or {} if the
    weighed module is not here. An ABSENT module means nothing is sold by
    weight, which is the state every shop starts in — not an error."""
    try:
        mod = _module("weighed")
    except AssistantRefused:
        return {}
    if not hasattr(mod, "load_weighed"):
        return {}
    try:
        return {sku: int(row.price_per_kg_paise)
                for sku, row in mod.load_weighed().items()}
    except Exception:  # noqa: BLE001 - an unreadable file is an empty shelf
        return {}


def _weighed_line(sku_id: str, name: str, qty: Optional[int],
                  unit: Optional[str], fraction: Optional[str],
                  kg: Optional[str] = None,
                  taught_with: str = "unknown") -> dict[str, Any]:
    """One priced weight, from gawaah/weighed.py's own arithmetic.

    Every paisa here is `weighed.line_paise` — integer multiply, integer floor
    divide, the remainder to the customer. This file does not do the sum and
    must not: a second implementation of a rounding rule is a second answer.

    THE RESULT IS SHAPED LIKE A PACKET LINE. It carries `qty`, `unit_paise`,
    `unit_rupees` and `taught_with` as well as the weight fields, so that every
    line on every bill proposal can be read the same way and a page written
    before loose goods existed does not meet an undefined field mid-render.
    `qty` is 1 and `unit_paise` equals `line_paise` because that is what a
    weighed line IS: one weighing, at one price, and a second scoop of the same
    rice is a second line — weighed.py's rule, not one invented here.
    """
    mod = _needs(_module("weighed"), "weighed",
                 "grams_for", "grams_from_kg_str", "_line_from")
    try:
        grams = (mod.grams_from_kg_str(kg) if kg
                 else mod.grams_for(qty, unit, _canonical_fraction(fraction)))
        line = dict(mod._line_from(sku_id, int(grams)))
    except Exception as exc:  # noqa: BLE001 - weighed.py names its own refusals
        reason = getattr(exc, "reason", None)
        detail = getattr(exc, "detail", None)
        if reason is None:
            raise AssistantRefused(
                R_WEIGHED_UNAVAILABLE,
                f"the weight could not be priced ({type(exc).__name__}: "
                f"{exc}). Nothing was proposed.") from None
        raise AssistantRefused(
            str(reason),
            f"{detail} (refused by gawaah/weighed.py, which prices "
            f"everything sold loose)") from None
    line["name"] = name
    line["by"] = "weighed"
    line["qty"] = 1
    line["unit_paise"] = int(paise(line["line_paise"]))
    line["unit_rupees"] = line["line_rupees"]
    line["taught_with"] = taught_with
    return line


def _packet_line(sku_id: str, rec: dict[str, Any], item: dict[str, Any],
                 cautions: list[str]) -> dict[str, Any]:
    """One line of packets, with any reading the shopkeeper should check."""
    name = str(rec.get("name") or sku_id)
    qty = 1
    if item.get("qty") is not None:
        qty = _whole_number(item.get("qty"), what="the quantity",
                            reason=R_BAD_QTY)
    unit = str(item.get("unit") or "").strip().lower()
    fraction = _canonical_fraction(item.get("fraction"))

    if fraction:
        raise AssistantRefused(
            R_BAD_QTY,
            f"{fraction!r} is half or a quarter of something, and {name} is "
            f"not sold by weight at this counter, so it bills whole packets. "
            f"Say how many packets, or mark {sku_id} as sold by the kilo on "
            f"the weighed screen.")
    if unit in DOZEN_UNITS:
        qty = qty * 12
        cautions.append(f"You said {unit}, so {name} was read as {qty} "
                        f"packets. Change the count before you accept it if "
                        f"that is wrong.")
    elif unit in PACK_UNITS:
        cautions.append(f"You said {unit}, and this counter has never been "
                        f"told how many packets are in a {unit} of {name}, so "
                        f"this is {qty} and not {qty} {unit} worth. Put the "
                        f"packet count in before you accept it.")
    elif unit in WEIGHT_UNITS:
        cautions.append(f"You said {unit}. This counter bills packets, not "
                        f"weight, so this is {qty} of {name} and not {qty} "
                        f"{unit}. Change the count before you accept it if "
                        f"that is wrong.")

    if qty <= 0:
        raise AssistantRefused(
            R_BAD_QTY,
            f"a quantity of {qty} is not something to add. To take a line off "
            f"a bill, take it off on the till.")
    if qty > MAX_QTY:
        raise AssistantRefused(
            R_QTY_TOO_LARGE,
            f"{qty} of {name} is past the {MAX_QTY} this counter proposes in "
            f"one go. Put it on the bill in smaller lots, or use the "
            f"storefront for a bulk order.")
    line = _line_for(sku_id, rec, qty)
    line["by"] = "packet"
    return line


def _do_add(args: dict[str, Any], brain: str) -> dict[str, Any]:
    """Resolve, price and WRITE DOWN a proposal of one or more lines.

    IT BILLS NOTHING. The quantities are the shopkeeper's; every rupee is the
    catalogue's or, for a loose weight, gawaah/weighed.py's. Neither the page
    nor the model supplies a price at any point.

    IF ANY ONE LINE CANNOT BE RESOLVED THE WHOLE PROPOSAL IS REFUSED, by that
    line's own name and saying which phrase failed. Proposing the three lines
    that worked and dropping the fourth is the exact failure the old blanket
    refusal existed to prevent, and it would be harder to notice than the
    refusal was.
    """
    known = catalogue()
    weighed_prices = _sold_by_weight()
    requested = _requested_items(args)

    cautions: list[str] = []
    lines: list[dict[str, Any]] = []
    for position, item in enumerate(requested, 1):
        phrase = str(item.get("product") or "")
        try:
            sku_id = resolve_product(phrase, known)
        except AssistantRefused as exc:
            if len(requested) == 1:
                raise
            raise AssistantRefused(
                exc.reason,
                f"{exc.detail} That was line {position} of {len(requested)} in "
                f"what you said, so NONE of it was proposed — a bill that is "
                f"quietly one line short is worse than one that was refused.",
            ) from None
        rec = known[sku_id]
        name = str(rec.get("name") or sku_id)
        wants_weight = bool(item.get("fraction")) or bool(item.get("kg")) or (
            str(item.get("unit") or "").strip().lower() in WEIGHT_UNITS)
        if wants_weight and sku_id in weighed_prices:
            lines.append(_weighed_line(
                sku_id, name,
                None if item.get("qty") is None else _whole_number(
                    item.get("qty"), what="the count", reason=R_BAD_QTY),
                item.get("unit"), item.get("fraction"), item.get("kg"),
                taught_with=str(rec.get("how") or "unknown")))
        else:
            lines.append(_packet_line(sku_id, rec, item, cautions))

    lines = _merge_packet_lines(lines, cautions)
    total_paise = 0
    for line in lines:
        total_paise += int(paise(line["line_paise"]))
    total_paise = int(paise(total_paise))

    caution = " ".join(cautions) if cautions else None
    proposal_id = "prop_" + secrets.token_hex(6)
    doc = {
        "format": PROPOSAL_FORMAT,
        "kind": KIND_BILL,
        "proposal_id": proposal_id,
        "at": _now_iso(),
        "brain": brain,
        "accepted": False,
        "lines": lines,
        "total_paise": total_paise,
        "total_rupees": to_rupees_str(paise(total_paise)),
        "caution": caution,
        "note": ("Nothing has been billed. This is what the counter thinks was "
                 "asked for; a person has to accept it on the till before it "
                 "becomes a line on a bill."),
    }
    _write_proposal(doc)
    head = _audit("assistant.proposed", proposal_id=proposal_id, brain=brain,
                  tool=TOOL_ADD, kind=KIND_BILL, total_paise=total_paise,
                  lines=[{"sku_id": ln["sku_id"], "qty": int(ln["qty"]),
                          "line_paise": int(ln["line_paise"])} for ln in lines],
                  accepted=False, minted=False)
    doc["audited"] = head is not None

    if len(lines) == 1:
        one = lines[0]
        if one.get("by") == "weighed":
            said = (f"{one['weight']} of {one['name']} at Rs "
                    f"{one['price_per_kg_rupees']} a kilo comes to Rs "
                    f"{one['line_rupees']}.")
        else:
            said = (f"{one['qty']} x {one['name']} at Rs {one['unit_rupees']} "
                    f"each comes to Rs {one['line_rupees']}.")
    else:
        listed = "; ".join(
            (f"{ln['weight']} {ln['name']} Rs {ln['line_rupees']}"
             if ln.get("by") == "weighed" else
             f"{ln['qty']} x {ln['name']} Rs {ln['line_rupees']}")
            for ln in lines)
        said = (f"{len(lines)} lines — {listed} — come to Rs "
                f"{doc['total_rupees']}.")
    if caution:
        said = f"{said} {caution}"
    return {"answer": f"{said} Nothing is on the bill yet — accept it to add "
                      f"it.",
            "proposal": doc,
            "data": {"lines": len(lines),
                     "sku_ids": [ln["sku_id"] for ln in lines],
                     # Kept for the one-line case, which is most of them, so a
                     # caller that read this before a sentence could carry two
                     # products still reads the same field.
                     "sku_id": lines[0]["sku_id"] if len(lines) == 1 else None,
                     "qty": int(lines[0]["qty"]) if len(lines) == 1 else None,
                     "total_paise": total_paise}}


def _merge_packet_lines(lines: list[dict[str, Any]],
                        cautions: list[str]) -> list[dict[str, Any]]:
    """One line per product, with the counts added up. SAID OUT LOUD when it
    happens.

    "do Maggi aur teen Maggi" is five Maggi. Two lines of the same sku on one
    proposal is a list nobody reads carefully, and reading it carefully is the
    only thing standing between a proposal and a bill. Weighed lines are never
    merged: weighed.py says a second scoop of the same rice is a second line,
    and it owns that rule.
    """
    merged: list[dict[str, Any]] = []
    by_sku: dict[str, dict[str, Any]] = {}
    for line in lines:
        if line.get("by") == "weighed":
            merged.append(line)
            continue
        seen = by_sku.get(line["sku_id"])
        if seen is None:
            by_sku[line["sku_id"]] = line
            merged.append(line)
            continue
        total_qty = int(seen["qty"]) + int(line["qty"])
        if total_qty > MAX_QTY:
            raise AssistantRefused(
                R_QTY_TOO_LARGE,
                f"you named {seen['name']} more than once and the counts come "
                f"to {total_qty}, past the {MAX_QTY} this counter proposes in "
                f"one go. Nothing was proposed.")
        seen["qty"] = total_qty
        line_paise = int(paise(int(seen["unit_paise"]) * total_qty))
        seen["line_paise"] = line_paise
        seen["line_rupees"] = to_rupees_str(paise(line_paise))
        cautions.append(f"You said {seen['name']} more than once, so it is one "
                        f"line of {total_qty} rather than two lines.")
    return merged


def _do_price(args: dict[str, Any]) -> dict[str, Any]:
    known = catalogue()
    sku_id = resolve_product(str(args.get("product") or ""), known)
    rec = known[sku_id]
    unit_paise = _priced(rec)
    name = str(rec.get("name") or sku_id)
    said = f"{name} is Rs {to_rupees_str(paise(unit_paise))}."
    off = rec.get("off_paise")
    marked = rec.get("marked_paise")
    data: dict[str, Any] = {
        "sku_id": sku_id, "name": name, "price_paise": unit_paise,
        "price_rupees": to_rupees_str(paise(unit_paise)),
        "taught_with": str(rec.get("how") or "unknown"),
    }
    if not isinstance(off, bool) and isinstance(off, int) and off > 0 \
            and not isinstance(marked, bool) and isinstance(marked, int) \
            and int(marked) > unit_paise:
        data["marked_paise"] = int(paise(marked))
        data["marked_rupees"] = to_rupees_str(int(paise(marked)))
        data["off_paise"] = int(paise(off))
        said = (f"{said} That is an offer price — the shelf edge says Rs "
                f"{to_rupees_str(int(paise(marked)))}.")
    return {"answer": said, "data": data, "proposal": None}


def _do_find(args: dict[str, Any]) -> dict[str, Any]:
    known = catalogue()
    sku_id = resolve_product(str(args.get("product") or ""), known)
    rec = known[sku_id]
    unit_paise = _priced(rec)
    name = str(rec.get("name") or sku_id)
    how = str(rec.get("how") or "unknown")
    return {
        "answer": (f"Yes — {name} ({sku_id}) at Rs "
                   f"{to_rupees_str(paise(unit_paise))}, taught by {how}."),
        "data": {"sku_id": sku_id, "name": name, "price_paise": unit_paise,
                 "price_rupees": to_rupees_str(paise(unit_paise)),
                 "taught_with": how},
        "proposal": None,
    }


#: Which storefront statuses are still the shopkeeper's problem. Read off
#: storefront itself when it is there, so one module decides what "open" means.
_OPEN_FALLBACK = ("new", "preparing", "out_for_delivery")


def _do_orders(args: dict[str, Any]) -> dict[str, Any]:
    """The shop's own storefront orders that have not finished.

    Read through gawaah/storefront.py rather than by globbing the orders
    directory here: the storefront writes those files and decides what a status
    means, and a second reader with its own idea of "open" is a second truth.
    """
    try:
        from . import storefront  # noqa: WPS433 - late, and it may be absent
    except Exception as exc:  # noqa: BLE001 - a named answer, not a crash
        raise AssistantRefused(
            R_ORDERS_UNAVAILABLE,
            f"the storefront module is not importable ({type(exc).__name__}: "
            f"{exc}), so this counter cannot say what is pending. The orders "
            f"screen reads the same file.") from None
    try:
        rows = list(storefront._all_orders())
        # A status with somewhere left to go is a status the shopkeeper still
        # has work in. Asked of storefront's own transition table rather than
        # listed again here, so adding a state there does not silently drop it
        # out of this answer.
        moves = getattr(storefront, "NEXT_STATUS", None)
        if isinstance(moves, dict) and moves:
            open_states = tuple(s for s, nxt in moves.items() if nxt)
        else:
            open_states = _OPEN_FALLBACK
    except Exception as exc:  # noqa: BLE001
        raise AssistantRefused(
            R_ORDERS_UNAVAILABLE,
            f"the orders could not be read ({type(exc).__name__}: {exc}). "
            f"Nothing was changed.") from None

    pending = [d for d in rows if str(d.get("status") or "") in open_states]
    counts: dict[str, int] = {}
    total_paise = 0
    for d in pending:
        s = str(d.get("status") or "")
        counts[s] = counts.get(s, 0) + 1
        total_paise += int(paise(d.get("total_paise") or 0))

    listed = [{
        "order_id": d.get("order_id"),
        "at": d.get("at"),
        "status": d.get("status"),
        "total_paise": int(d.get("total_paise") or 0),
        "total_rupees": to_rupees_str(int(paise(d.get("total_paise") or 0))),
        "lines": len(d.get("lines") or []),
        # The shopkeeper's own screen shows the address; a chat answer does not
        # need it, and a name is enough to know which order is being discussed.
        "name": (d.get("customer") or {}).get("name"),
        "paid": bool((d.get("payment") or {}).get("paid")),
    } for d in pending[:MAX_PENDING_LISTED]]

    if not pending:
        said = ("No online orders are open. Everything placed has been "
                "delivered or cancelled.")
    else:
        parts = ", ".join(f"{n} {s.replace('_', ' ')}"
                          for s, n in sorted(counts.items()))
        said = (f"{len(pending)} online orders are still open ({parts}), worth "
                f"Rs {to_rupees_str(paise(total_paise))} altogether. The oldest "
                f"is {pending[-1].get('order_id')}.")
    return {"answer": said,
            "data": {"pending": len(pending), "counts": counts,
                     "total_paise": total_paise,
                     "total_rupees": to_rupees_str(paise(total_paise)),
                     "orders": listed,
                     "listed": len(listed), "open_states": list(open_states)},
            "proposal": None}


def _do_takings(args: dict[str, Any]) -> dict[str, Any]:
    """Today's numbers, recomputed from the hash-chained audit log.

    Derived through gawaah/manage.py, which is the module that decides what a
    bill is and when a day starts, so the answer here and the Today screen
    cannot drift apart. Offline on purpose: it reads the chain and the
    catalogue, and does not ask the money service anything, so a question about
    takings never waits on a network.
    """
    try:
        from . import manage  # noqa: WPS433 - late; it pulls in the vision deps
    except Exception as exc:  # noqa: BLE001
        raise AssistantRefused(
            R_TAKINGS_UNAVAILABLE,
            f"gawaah/manage.py is not importable ({type(exc).__name__}: "
            f"{exc}), and this counter will not add up a day's takings a "
            f"second way.") from None
    for needed in ("read_chain", "bills_from", "_brief_for",
                   "_local_day_bounds"):
        if not hasattr(manage, needed):
            raise AssistantRefused(
                R_TAKINGS_UNAVAILABLE,
                f"gawaah/manage.py has no {needed!r}, so the day's figures "
                f"cannot be derived the same way the Today screen derives "
                f"them. Nothing was estimated.")
    try:
        start, end, label = manage._local_day_bounds(None)
        records, chain = manage.read_chain()
        brief = manage._brief_for(manage.bills_from(records), start, end)
    except Exception as exc:  # noqa: BLE001
        raise AssistantRefused(
            R_TAKINGS_UNAVAILABLE,
            f"the audit chain could not be read ({type(exc).__name__}: {exc}). "
            f"No figure was invented in its place.") from None

    for scratch in ("units_by_sku", "line_revenue_by_sku"):
        brief.pop(scratch, None)
    bills = int(brief.get("bills") or 0)
    if not bills:
        said = (f"Nothing has been billed at this counter today ({label}). "
                f"That is the chain's answer, not a guess.")
    else:
        said = (f"{bills} bills today ({label}) come to Rs "
                f"{brief.get('revenue_rupees')}. Rs "
                f"{brief.get('settled_rupees')} of that is settled by the "
                f"gateway and Rs {brief.get('awaiting_rupees')} is still "
                f"waiting.")
    if not chain.get("ok", True):
        said = (f"{said} The audit chain is broken at line "
                f"{chain.get('lines_checked')}, so anything after that break is "
                f"missing from this figure.")
    return {"answer": said,
            "data": {"date": label, "chain": chain, **brief},
            "proposal": None}


def _do_low_stock(args: dict[str, Any]) -> dict[str, Any]:
    """What is running out — of the products somebody has actually counted.

    THIS COUNTER HAS NO STOCK SENSOR. A remaining figure is the shopkeeper's own
    count minus what has been billed since he made it, and anything he has never
    counted is reported as uncounted rather than as zero. A zero is a claim.
    """
    units = DEFAULT_LOW_STOCK_UNITS
    if args.get("units") is not None:
        units = _whole_number(args.get("units"), what="the stock threshold",
                              reason=R_BAD_THRESHOLD)
    if units < 0 or units > MAX_LOW_STOCK_UNITS:
        raise AssistantRefused(
            R_BAD_THRESHOLD,
            f"{units} is outside the 0 to {MAX_LOW_STOCK_UNITS} this counter "
            f"treats as a shelf count.")
    try:
        from . import manage  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        raise AssistantRefused(
            R_STOCK_UNAVAILABLE,
            f"gawaah/manage.py is not importable ({type(exc).__name__}: "
            f"{exc}), so the counts and what has sold against them cannot be "
            f"read.") from None
    if not hasattr(manage, "_inventory_rows"):
        raise AssistantRefused(
            R_STOCK_UNAVAILABLE,
            "gawaah/manage.py has no '_inventory_rows', so remaining units "
            "cannot be derived the same way the Inventory screen derives them.")
    try:
        rows = list(manage._inventory_rows()["items"])
    except Exception as exc:  # noqa: BLE001
        raise AssistantRefused(
            R_STOCK_UNAVAILABLE,
            f"the stock counts could not be read ({type(exc).__name__}: "
            f"{exc}). Nothing was estimated.") from None

    low: list[dict[str, Any]] = []
    uncounted = 0
    for r in rows:
        remaining = r.get("remaining_units")
        if remaining is None:
            uncounted += 1
            continue
        if int(remaining) <= units:
            low.append({"sku_id": r.get("sku_id"), "name": r.get("name"),
                        "remaining_units": int(remaining),
                        "counted_at": r.get("opening_stock_counted_at"),
                        "billed_since_count": r.get("billed_since_count")})
    low.sort(key=lambda d: (int(d["remaining_units"]), str(d["sku_id"])))

    if low:
        listed = ", ".join(f"{d['name'] or d['sku_id']} ({d['remaining_units']} "
                           f"left)" for d in low[:MAX_MATCHES_LISTED])
        said = f"{len(low)} products are at or below {units}: {listed}."
    else:
        said = f"Nothing you have counted is down to {units} or fewer."
    if uncounted:
        said = (f"{said} {uncounted} products have never been counted, so this "
                f"counter cannot say anything about them — it has no stock "
                f"sensor.")
    return {"answer": said,
            "data": {"threshold_units": units, "low": low,
                     "low_count": len(low), "uncounted": uncounted,
                     "counted": len(rows) - uncounted},
            "proposal": None}


# --------------------------------------------- the rest of the shop's tools --
#
# Each of these is a call into the module that owns the answer, and a sentence
# built from what came back. THE ARITHMETIC IS NEVER REPEATED HERE: where a
# figure appears in an answer it is the same integer the screen renders, read
# out of the same payload.


def _sku_for(args: dict[str, Any], *, what: str) -> tuple[str, dict[str, Any]]:
    """The one sku those words mean, and its catalogue record."""
    known = catalogue()
    sku_id = resolve_product(str(args.get("product") or ""), known)
    return sku_id, known[sku_id]


def _do_stock_on_hand(args: dict[str, Any]) -> dict[str, Any]:
    """What is on the shelf for one product — stock.py's own subtraction."""
    sku_id, rec = _sku_for(args, what="a stock question")
    # stock_one_ep spreads the row at the top level of its response, so the
    # payload IS the row. Read as it is served rather than reshaped here.
    row = _ask("stock", "stock_one_ep", sku_id)
    payload = row
    name = str(row.get("name") or rec.get("name") or sku_id)
    on_hand = row.get("on_hand_units")
    if on_hand is None:
        said = (f"{name} has never been counted at this counter, so there is "
                f"no figure for it. A zero here would be a claim; this is an "
                f"absence. Count the shelf on the stock screen and the number "
                f"starts from there.")
    else:
        said = (f"{name}: {int(on_hand)} on the shelf. "
                f"{row.get('derivation') or ''}").strip()
        level = row.get("reorder_level")
        if level is not None and row.get("at_or_under_reorder_level"):
            said = f"{said} That is at or under the {int(level)} you set for it."
    return {"answer": said, "data": payload, "proposal": None}


def _do_stock_movements(args: dict[str, Any]) -> dict[str, Any]:
    """Stock that arrived or left without being billed."""
    sku_id = None
    if args.get("product"):
        sku_id, _rec = _sku_for(args, what="a movement question")
    payload = _ask("stock", "stock_movements_ep", sku=sku_id, limit=None)
    rows = list(payload.get("movements") or [])
    if not rows:
        said = ("Nothing has been written into the movement log"
                + (f" for {sku_id}" if sku_id else "")
                + ". Sales are not in this log — they are on the bill chain.")
    else:
        listed = ", ".join(
            f"{r.get('units')} {r.get('sku_id')} ({r.get('reason')})"
            for r in rows[:MAX_LISTED])
        said = (f"{len(rows)} movements, newest first: {listed}. A sale is not "
                f"here — this log is only what moved without being billed.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_reorder_list(args: dict[str, Any]) -> dict[str, Any]:
    """What is at or under the level the shopkeeper set for it."""
    payload = _ask("stock", "stock_low_ep")
    low = list(payload.get("low") or [])
    unknown = list(payload.get("unknown") or [])
    negative = list(payload.get("needs_recount") or [])
    if low:
        listed = ", ".join(
            f"{r.get('name') or r.get('sku_id')} "
            f"({r.get('on_hand_units')} left, level {r.get('reorder_level')})"
            for r in low[:MAX_LISTED])
        said = f"{len(low)} products are at or under their level: {listed}."
    else:
        said = "Nothing is at or under the reorder level you set for it."
    if unknown:
        said = (f"{said} {len(unknown)} have a level set but have never been "
                f"counted, so whether they are low cannot be said.")
    if negative:
        said = (f"{said} {len(negative)} come out below zero, which means "
                f"stock left without being recorded — those shelves need "
                f"re-counting before the figure means anything.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_propose_movement(args: dict[str, Any], brain: str) -> dict[str, Any]:
    """Write a stock movement down for a person to accept. It moves nothing.

    Validated against stock.py's OWN vocabulary and OWN cap, so a proposal
    somebody accepts cannot then be refused by the module that has to carry it
    out — a piece of paper that fails on presentation is worse than no paper.
    """
    mod = _needs(_module("stock"), "stock", "IN_REASONS", "OUT_REASONS",
                 "MAX_MOVEMENT_UNITS")
    direction = str(args.get("direction") or "").strip().lower()
    if direction not in ("in", "out"):
        raise AssistantRefused(
            R_BAD_DIRECTION,
            f"{args.get('direction')!r} is neither 'in' nor 'out', so this "
            f"counter cannot tell whether stock arrived or left. Nothing was "
            f"written down.")
    book = dict(mod.IN_REASONS if direction == "in" else mod.OUT_REASONS)
    reason = str(args.get("reason") or "").strip().lower()
    if reason not in book:
        raise AssistantRefused(
            R_BAD_MOVEMENT_REASON,
            f"{args.get('reason')!r} is not a reason this counter records for "
            f"stock going {direction}. It records: {', '.join(sorted(book))}. "
            f"The vocabulary is closed so that 'how much went to breakage this "
            f"month' has an answer. Nothing was written down.")

    sku_id, rec = _sku_for(args, what="a stock movement")
    units = _whole_number(args.get("qty", 1), what="the number of units",
                          reason=R_BAD_QTY)
    if units <= 0:
        raise AssistantRefused(
            R_BAD_QTY,
            f"a movement of {units} units is not a movement. Say how many "
            f"actually arrived or left.")
    cap = int(getattr(mod, "MAX_MOVEMENT_UNITS", 100_000))
    if units > cap:
        raise AssistantRefused(
            R_QTY_TOO_LARGE,
            f"{units} units is past the {cap} gawaah/stock.py accepts in one "
            f"movement, so a proposal for it could never be carried out. "
            f"Nothing was written down.")
    note = str(args.get("note") or "").strip()
    if len(note) > MAX_NOTE:
        raise AssistantRefused(
            R_NOTE_TOO_LONG,
            f"that note is {len(note)} characters and the counter keeps "
            f"{MAX_NOTE}. Shorten it. Nothing was written down.")

    name = str(rec.get("name") or sku_id)
    pack = str(args.get("unit") or "").strip().lower()
    caution = None
    if pack in PACK_UNITS:
        caution = (f"You said {pack}, and this counter has never been told how "
                   f"many packets are in a {pack} of {name}, so this is "
                   f"written down as {units}. Put the packet count in before "
                   f"you accept it.")
    proposal_id = "prop_" + secrets.token_hex(6)
    doc = {
        "format": PROPOSAL_FORMAT,
        "kind": KIND_MOVEMENT,
        "proposal_id": proposal_id,
        "at": _now_iso(),
        "brain": brain,
        "accepted": False,
        "caution": caution,
        # EMPTY, AND PRESENT ON PURPOSE. Every reader of a proposal was written
        # against the bill shape, which has always had `lines`. A movement has
        # no lines and no money in it at all — but a reader that does
        # `proposal.lines.length` would throw on a missing field and take the
        # whole screen with it. An empty list degrades: it renders the sentence
        # and offers nothing to accept, which is exactly right for a proposal
        # that is accepted on a different screen. There is deliberately no
        # `total_paise` here either: a movement moves packets, not rupees, and
        # a zero would read as "worth nothing".
        "lines": [],
        "movement": {"sku_id": sku_id, "name": name, "direction": direction,
                     "units": units, "reason": reason,
                     "reason_label": str(book[reason]), "note": note},
        # The endpoint a person's ACCEPT has to reach. Named rather than
        # called: this module does not move stock, and writing the path down
        # is not the same as walking it.
        "accept_by": {"method": "POST", "path": f"/stock/{sku_id}/{direction}",
                      "body": {"units": units, "reason": reason,
                               "note": note or None}},
        "note": ("Nothing has moved. The shelf changes when somebody accepts "
                 "this on the stock screen, and not before."),
    }
    _write_proposal(doc)
    head = _audit("assistant.proposed", proposal_id=proposal_id, brain=brain,
                  tool=TOOL_PROPOSE_MOVEMENT, kind=KIND_MOVEMENT,
                  sku_id=sku_id, direction=direction, units=units,
                  reason=reason, accepted=False, minted=False)
    doc["audited"] = head is not None
    arrow = "arrived" if direction == "in" else "left the shelf"
    said = (f"{units} of {name} {arrow} — {book[reason]}. This is written "
            f"down, not done: accept it on the stock screen and the shelf "
            f"figure moves then.")
    if caution:
        said = f"{said} {caution}"
    return {
        "answer": said,
        "proposal": doc,
        "data": {"sku_id": sku_id, "units": units, "direction": direction,
                 "reason": reason},
    }


def _do_expenses_today(args: dict[str, Any]) -> dict[str, Any]:
    """What the shop paid out today, grouped the way expenses.py groups it."""
    # expenses_day_ep spreads its own `_totals` block at the top level.
    payload = _ask("expenses", "expenses_day_ep", day=None)
    totals = payload
    count = int(totals.get("count") or 0)
    if not count:
        said = (f"Nothing has been written into the day book for "
                f"{payload.get('day')}. That is what is recorded, not what was "
                f"spent — a payment nobody entered is not here.")
    else:
        buckets = ", ".join(
            f"{b.get('label') or b.get('category')} Rs {b.get('rupees')}"
            for b in (totals.get("by_category") or [])[:MAX_LISTED])
        said = (f"{count} entries for {payload.get('day')} come to Rs "
                f"{totals.get('total_rupees')} — {buckets}. Rs "
                f"{totals.get('cash_rupees')} of that left the drawer.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_cash_position(args: dict[str, Any]) -> dict[str, Any]:
    """The drawer, reconciled by expenses.py rather than added up again here."""
    payload = _ask("expenses", "cash_position_ep", day=None)
    expected = payload.get("expected_closing_rupees")
    if expected is None:
        said = (f"The drawer for {payload.get('day')} cannot be worked out "
                f"yet — no opening count has been recorded, and this counter "
                f"will not guess what was in it at the start of the day.")
    else:
        said = (f"The drawer for {payload.get('day')} should hold Rs "
                f"{expected}. That is the opening count plus what was taken in "
                f"cash, less what was paid out of it. Anything settled by the "
                f"gateway never touched the drawer.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_propose_expense(args: dict[str, Any], brain: str) -> dict[str, Any]:
    """Write an expense down for a person to accept. It records nothing.

    THE AMOUNT IS THE ONE FIGURE THAT COMES IN FROM OUTSIDE, and it comes in as
    TEXT and is parsed by `money.from_rupees_str`, which never touches a float.
    It is allowed here, and nowhere else, because an expense amount is a fact
    the shopkeeper stated out loud — not a price this counter derives. A price
    is still never accepted from anybody: see the `price_said_out_loud` refusal
    in `local_route`.
    """
    mod = _needs(_module("expenses"), "expenses", "CATEGORIES",
                 "CATEGORY_LABELS", "MAX_EXPENSE_PAISE")
    category = str(args.get("category") or "").strip().lower()
    if category not in tuple(mod.CATEGORIES):
        raise AssistantRefused(
            R_BAD_EXPENSE_CATEGORY,
            f"{args.get('category')!r} is not a kind of expense this counter "
            f"records. It records: {', '.join(mod.CATEGORIES)}. Use 'other' "
            f"with a note if none of them fits. Nothing was written down.")

    raw = args.get("amount_rupees")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise AssistantRefused(
            R_NO_AMOUNT,
            "no rupee figure was said, and an expense of nothing is not an "
            "expense. Say how much. Nothing was written down.")
    if not isinstance(raw, str):
        raise AssistantRefused(
            R_BAD_AMOUNT,
            f"the amount came through as a {type(raw).__name__}. It has to be "
            f"text such as \"120.50\", because a rupee is never a float here. "
            f"Nothing was written down.")
    try:
        amount_paise = int(from_rupees_str(raw))
    except MoneyError as exc:
        raise AssistantRefused(
            R_BAD_AMOUNT,
            f"{raw!r} could not be read as rupees ({exc}). Two decimal places "
            f"at most; a shop does not deal in half paise. Nothing was written "
            f"down.") from None
    if amount_paise <= 0:
        raise AssistantRefused(
            R_BAD_AMOUNT,
            f"an expense of Rs {to_rupees_str(paise(amount_paise))} is not "
            f"something to record. Nothing was written down.")
    cap = int(getattr(mod, "MAX_EXPENSE_PAISE", 10_000_000))
    if amount_paise > cap:
        raise AssistantRefused(
            R_AMOUNT_TOO_LARGE,
            f"Rs {to_rupees_str(paise(amount_paise))} is past the Rs "
            f"{to_rupees_str(paise(cap))} gawaah/expenses.py takes in one "
            f"entry, so a proposal for it could never be carried out. Check "
            f"whether paise were said where rupees were meant. Nothing was "
            f"written down.")

    note = str(args.get("note") or "").strip()[:MAX_NOTE]
    label = dict(mod.CATEGORY_LABELS).get(category, category)
    proposal_id = "prop_" + secrets.token_hex(6)
    doc = {
        "format": PROPOSAL_FORMAT,
        "kind": KIND_EXPENSE,
        "proposal_id": proposal_id,
        "at": _now_iso(),
        "brain": brain,
        "accepted": False,
        "caution": None,
        # Empty and present for the reason the movement proposal states: a
        # reader written for a bill must degrade rather than throw.
        "lines": [],
        "expense": {"amount_paise": amount_paise,
                    "amount_rupees": to_rupees_str(paise(amount_paise)),
                    "category": category, "category_label": str(label),
                    "note": note, "paid_with": "cash"},
        "total_paise": amount_paise,
        "total_rupees": to_rupees_str(paise(amount_paise)),
        "accept_by": {"method": "POST", "path": "/expenses",
                      "body": {"amount_paise": amount_paise,
                               "category": category, "note": note,
                               "paid_with": "cash"}},
        "note": ("Nothing has been recorded and no money has moved. The day "
                 "book changes when somebody accepts this on the expenses "
                 "screen. It is written as paid in cash, which is the only "
                 "kind that moves the drawer — change it there if it went off "
                 "the bank instead."),
    }
    _write_proposal(doc)
    head = _audit("assistant.proposed", proposal_id=proposal_id, brain=brain,
                  tool=TOOL_PROPOSE_EXPENSE, kind=KIND_EXPENSE,
                  amount_paise=amount_paise, category=category,
                  note_len=len(note), accepted=False, minted=False)
    doc["audited"] = head is not None
    return {
        "answer": (f"Rs {doc['total_rupees']} under {label}. This is written "
                   f"down, not recorded: accept it on the expenses screen and "
                   f"the day book moves then. It is marked paid in cash."),
        "proposal": doc,
        "data": {"amount_paise": amount_paise, "category": category},
    }


def _do_margin_of(args: dict[str, Any]) -> dict[str, Any]:
    """What one product earns, from purchases.py's own comparison."""
    sku_id, rec = _sku_for(args, what="a margin question")
    # sku_ep spreads the margin row at the top level of its response.
    row = _ask("purchases", "sku_ep", sku_id)
    name = str(row.get("name") or rec.get("name") or sku_id)
    if not row.get("cost_known"):
        said = (f"{name} sells for Rs {row.get('sell_rupees')}, but no "
                f"purchase has ever been recorded for it, so what it earns is "
                f"not known. It is not the whole selling price — that would be "
                f"counting the cost as zero.")
    else:
        said = (f"{name}: sells at Rs {row.get('sell_rupees')}, last cost Rs "
                f"{row.get('cost_rupees')}, so it earns Rs "
                f"{row.get('margin_rupees')} a packet — "
                f"{row.get('margin_pct_of_price')} per cent of the price, "
                f"{row.get('markup_pct_of_cost')} per cent on the cost. That "
                f"cost was recorded on {row.get('cost_recorded_on')}.")
        if row.get("below_cost"):
            said = f"{said} It is selling BELOW what it cost."
    return {"answer": said, "data": row, "proposal": None}


def _do_margin_today(args: dict[str, Any]) -> dict[str, Any]:
    """What today's trading earned, counted off the bill chain by purchases.py."""
    payload = _ask("purchases", "margin_today_ep", day=None)
    covered = payload.get("covered") or {}
    uncovered = payload.get("uncovered") or {}
    without_a_cost = len(list(uncovered.get("skus") or []))
    if not payload.get("bills"):
        said = (f"Nothing was billed on {payload.get('date')}, so there is no "
                f"margin to work out.")
        return {"answer": said, "data": payload, "proposal": None}
    said = (f"On {payload.get('date')}, across {payload.get('bills')} bills: "
            f"the {covered.get('units')} units whose cost is known sold for Rs "
            f"{covered.get('revenue_rupees')} and earned Rs "
            f"{covered.get('margin_rupees')}.")
    if without_a_cost:
        said = (f"{said} {without_a_cost} products worth Rs "
                f"{uncovered.get('revenue_rupees')} have no recorded cost, so "
                f"they are left out rather than counted as all profit.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_suppliers(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("purchases", "suppliers_ep")
    rows = list(payload.get("suppliers") or [])
    if not rows:
        said = ("No suppliers have been recorded yet. Add one on the purchases "
                "screen and what stock costs starts being answerable.")
    else:
        listed = ", ".join(
            f"{r.get('name')}{' ' + str(r.get('phone')) if r.get('phone') else ''}"
            for r in rows[:MAX_LISTED])
        said = f"{len(rows)} suppliers: {listed}."
    return {"answer": said, "data": payload, "proposal": None}


def _read_phone(args: dict[str, Any]) -> str:
    """The digits of a phone number, checked for shape before it is used.

    Whether those digits are a number this shop knows is customers.py's
    question, not this one's; asking it twice is how two answers diverge.
    """
    raw = args.get("phone")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise AssistantRefused(
            R_NO_PHONE,
            "no phone number was said, and this counter will not guess which "
            "customer was meant.")
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) < 10:
        raise AssistantRefused(
            R_BAD_PHONE,
            f"{raw!r} has {len(digits)} digits in it. An Indian subscriber "
            f"number is ten. Say the whole number.")
    return digits


def _do_customer(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("customers", "lookup_ep", phone=_read_phone(args),
                   limit=None)
    matches = list(payload.get("matches") or [])
    exact = payload.get("customer")
    if exact is not None:
        matches = [exact] + [m for m in matches
                             if m.get("phone") != exact.get("phone")]
    if not matches:
        said = ("Nobody with that number has ordered from this shop's "
                "storefront. Orders taken at the counter are not in this list "
                "— a counter bill has no phone number on it.")
    else:
        first = matches[0]
        said = (f"{first.get('name') or 'That number'}: "
                f"{first.get('order_count')} orders worth Rs "
                f"{first.get('total_rupees')}, last one "
                f"{first.get('last_order_at')} ({first.get('last_status')}).")
        if len(matches) > 1:
            said = f"{said} {len(matches) - 1} other numbers also matched."
    return {"answer": said, "data": payload, "proposal": None}


def _do_regulars(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("customers", "regulars_ep", by=None, limit=None)
    rows = list(payload.get("by_spend") or payload.get("by_frequency") or [])
    if not rows:
        said = ("Nobody has ordered from the storefront yet, so there are no "
                "regulars to rank. Counter bills carry no phone number and are "
                "not in this.")
    else:
        listed = ", ".join(
            f"{r.get('name') or r.get('phone')} "
            f"({r.get('order_count')} orders, Rs {r.get('total_rupees')})"
            for r in rows[:MAX_LISTED])
        said = f"Your regulars, most spent first: {listed}."
    return {"answer": said, "data": payload, "proposal": None}


def _do_loyalty(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("loyalty", "balance_ep", _read_phone(args))
    if not payload.get("known"):
        said = ("That number has no points on it. Points are earned only on "
                "bills the gateway settled — a link that was sent but not paid "
                "earns nothing.")
    else:
        said = (f"{payload.get('balance_points')} points, worth Rs "
                f"{payload.get('balance_value_rupees')}. Earned "
                f"{payload.get('earned_points')} on "
                f"{payload.get('bills_settled')} settled bills, redeemed "
                f"{payload.get('redeemed_points')}.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_loyalty_rules(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("loyalty", "rules_ep")
    rules = payload.get("rules") or {}
    if not rules.get("on"):
        said = ("The loyalty scheme is switched off — zero points per rupee — "
                "so nothing is earning points at the moment.")
    else:
        said = (f"{rules.get('points_per_rupee')} points per rupee, and a "
                f"point is worth {rules.get('paise_per_point')} paise. Points "
                f"are earned only on bills the gateway settled.")
    return {"answer": said, "data": payload, "proposal": None}


# ------------------------------------------------------------- the book --
#
# KHATA. Both tools reach gawaah/khata.py through `_ask`, so the household the
# assistant names is the household the screen would show, found by the same
# lookup. A name that matches two households is refused with both, never
# picked between: a debt written on the wrong Sharma is the worst line this
# counter could produce, and it would look exactly like a right one.

KIND_KHATA_BOOK = "khata_book"

#: Said around a name, never part of one.
_HONORIFICS = frozenset({"ji", "jee", "sahab", "saheb", "sahib", "bhai", "bhaiya",
                         "didi", "aunty", "uncle", "babu", "da", "dada", "boudi"})


def _read_customer(args: dict[str, Any]) -> str:
    raw = args.get("customer")
    said = " ".join(str(raw or "").split())
    if not said:
        raise AssistantRefused(
            R_NO_CUSTOMER_NAMED,
            "no customer was named, and this counter will not guess whose book "
            "was meant. Say the name or the number. Nothing was done.")
    return said[:80]


def _khata_matches(said: str) -> list[dict[str, Any]]:
    payload = _ask("khata", "lookup_ep", q=said)
    return list(payload.get("matches") or [])


def _household_line(h: dict[str, Any]) -> str:
    name = str(h.get("name") or "unnamed")
    masked = str(h.get("phone_masked") or "")
    return f"{name} ({masked})" if masked else name


def _one_household(said: str, *, for_booking: bool) -> Optional[dict[str, Any]]:
    """Exactly one household, or None when the book has nobody by that name
    and a booking may still open one. Two or more is a refusal by name."""
    matches = _khata_matches(said)
    if len(matches) > 1:
        listed = "; ".join(_household_line(h) for h in matches[:MAX_LISTED])
        raise AssistantRefused(
            R_SEVERAL_HOUSEHOLDS,
            f"{said!r} matches more than one household in the book — {listed}. "
            f"Say the phone number, or more of the name. Nothing was done.")
    if not matches:
        if for_booking:
            return None
        raise AssistantRefused(
            R_NO_HOUSEHOLD,
            f"nobody called {said!r} has a book at this counter. A household "
            f"appears in the book when a bill is first put on it at the till.")
    return matches[0]


def _do_khata_balance(args: dict[str, Any]) -> dict[str, Any]:
    said = _read_customer(args)
    h = _one_household(said, for_booking=False)
    assert h is not None
    name = str(h.get("name") or "That household")
    due = str(h.get("outstanding_rupees") or "0.00")
    bills = int(h.get("bills") or 0)
    oldest = h.get("oldest_days")
    last = h.get("last_capture") or None
    live = h.get("live_collection") or None
    parts = [f"{name}: Rs {due} still on the book"]
    if bills:
        parts[0] += f" across {bills} bill{'s' if bills != 1 else ''}"
    if oldest is not None:
        parts[0] += f", the oldest {int(oldest)} day{'s' if int(oldest) != 1 else ''} old"
    parts[0] += "."
    if last:
        when = str(last.get("at") or "")[:10]
        parts.append(f"Last collected Rs {last.get('amount_rupees')} on {when}, "
                     f"through the gateway's link.")
    else:
        parts.append("Nothing has been collected through a link yet.")
    if live:
        parts.append(f"A collection link is open for Rs {live.get('amount_rupees')}"
                     f" (Rs {live.get('captured_rupees')} paid on it so far); "
                     f"the gateway is sending the reminders.")
    if int(h.get("parked_paise") or 0) > 0:
        parts.append(f"Rs {h.get('parked_rupees')} arrived that did not reconcile "
                     f"and is parked for you to look at — not counted.")
    return {"answer": " ".join(parts), "data": h, "proposal": None}


def _do_khata_book(args: dict[str, Any], brain: str) -> dict[str, Any]:
    """Propose ON THE BOOK for the bill on the counter. It books nothing.

    The proposal names the household when the book knows one and carries
    the name alone when it does not — the till asks for the number when a
    person accepts, and the money service re-derives the bill from the
    witness before anything is booked. There is no `accept_by`: the only
    place a booking happens is the till's own ON THE BOOK, with the bill
    that is actually on the counter.
    """
    said = _read_customer(args)
    h = _one_household(said, for_booking=True)
    proposal_id = "prop_" + secrets.token_hex(6)
    # A name the book does not know yet is carried as a NAME, not as the
    # tokens the parser left: "sharma ji" becomes "Sharma", because that is
    # what the till will write on the book when a person accepts.
    spoken_name = " ".join(w.capitalize() for w in said.split()
                           if w.lower() not in _HONORIFICS) or said
    customer = {
        "book_id": h.get("book_id") if h else None,
        "name": str(h.get("name")) if h else spoken_name,
        "phone": str(h.get("phone")) if h else None,
        "phone_masked": str(h.get("phone_masked")) if h else None,
        "known": h is not None,
        "outstanding_paise": int(h.get("outstanding_paise") or 0) if h else 0,
        "outstanding_rupees": str(h.get("outstanding_rupees") or "0.00") if h else "0.00",
        "said": said,
    }
    doc = {
        "format": PROPOSAL_FORMAT,
        "kind": KIND_KHATA_BOOK,
        "proposal_id": proposal_id,
        "at": _now_iso(),
        "brain": brain,
        "accepted": False,
        "caution": None,
        "lines": [],
        "customer": customer,
        "total_paise": 0,
        "total_rupees": "0.00",
        "accept_by": None,
        "note": ("Nothing has been booked. The bill on the counter goes on this "
                 "household's book only when a person presses ON THE BOOK on "
                 "the till, and the money service re-derives the bill from the "
                 "counter's own witness before it agrees."),
    }
    _write_proposal(doc)
    head = _audit("assistant.proposed", proposal_id=proposal_id, brain=brain,
                  tool=TOOL_KHATA_BOOK, kind=KIND_KHATA_BOOK,
                  book_id=customer["book_id"], known=customer["known"],
                  accepted=False, minted=False)
    doc["audited"] = head is not None
    if h:
        answer = (f"On the book for {customer['name']} ({customer['phone_masked']}), "
                  f"who has Rs {customer['outstanding_rupees']} outstanding already. "
                  f"Press ON THE BOOK on the till to close the bill onto it; no "
                  f"colour, no money moves.")
    else:
        answer = (f"On the book for {said!r} — there is no book by that name yet, "
                  f"so the till will ask for the number when you accept. Nothing "
                  f"is booked until you press ON THE BOOK.")
    return {"answer": answer, "proposal": doc, "data": customer}


# ------------------------------------------------------------- the bank --
#
# MILAN. One question, answered by the module whose screen shows the same
# table: what reached the bank on that day, net of what, and what did not
# match. The sentence names every exception class with a count, because "Rs
# 3,112.39 came in" with a found payment left unsaid is the kind of answer a
# shopkeeper would later call a lie.


def _bank_day(args: dict[str, Any]) -> Optional[str]:
    """'yesterday' | 'today' | 'day_before' | YYYY-MM-DD -> the day to ask
    for, or None for the module's own default (yesterday)."""
    raw = str(args.get("day") or "").strip().lower()
    if not raw or raw == "yesterday":
        return None
    today = _dt.date.today()
    if raw == "today":
        return today.isoformat()
    if raw == "day_before":
        return (today - _dt.timedelta(days=2)).isoformat()
    try:
        return _dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        raise AssistantRefused(
            R_BAD_TOOL_ARGS,
            f"{raw!r} is not a day this counter can ask the gateway about. Say "
            f"kal, aaj, parso, or a date as YYYY-MM-DD.") from None


def _do_bank_settlement(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("milan", "match_ep", day=_bank_day(args))
    matched = payload.get("matched") or {}
    exceptions = payload.get("exceptions") or {}
    said = str(payload.get("value_line") or "")
    if not said:
        said = (f"{payload.get('day')}: Rs {matched.get('net_rupees')} reached the "
                f"bank, {matched.get('count')} bills matched.")
    if payload.get("simulated"):
        said = f"{said} (The gateway is the simulator; its rows say so.)"
    warning = payload.get("chain_warning")
    if warning:
        said = f"{said} {warning}"
    data = {
        "day": payload.get("day"),
        "settlement_cycle": payload.get("settlement_cycle"),
        "simulated": payload.get("simulated"),
        "matched": {k: v for k, v in matched.items() if k != "rows"},
        "exceptions": {name: {"count": b.get("count"), "rupees": b.get("rupees")}
                       for name, b in exceptions.items() if isinstance(b, dict)},
        "exception_count": payload.get("exception_count"),
        "value_line": payload.get("value_line"),
        "chain": payload.get("chain"),
        "derived_from": payload.get("derived_from"),
    }
    return {"answer": said, "data": data, "proposal": None}


def _do_offers(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("offers", "offers_ep")
    rows = [o for o in (payload.get("offers") or []) if o.get("active")]
    if not rows:
        said = ("No offers are running, so everything is at its marked price.")
    else:
        listed = ", ".join(
            f"{o.get('label') or o.get('offer_id')} on "
            f"{o.get('sku_id') or 'everything'}" for o in rows[:MAX_LISTED])
        said = (f"{len(rows)} offers are running: {listed}. Every price this "
                f"counter quotes already has them applied.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_categories(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("categories", "categories_ep")
    rows = list(payload.get("categories") or [])
    if not rows:
        said = ("Nothing has been filed under a category yet. Every product is "
                "still findable by name.")
    else:
        listed = ", ".join(str(c.get("name")) for c in rows[:MAX_LISTED])
        said = f"{len(rows)} categories: {listed}."
    return {"answer": said, "data": payload, "proposal": None}


def _do_in_category(args: dict[str, Any]) -> dict[str, Any]:
    """The products under one category, found by the name he said.

    The category is resolved to an id HERE, against categories.py's own book,
    because its products endpoint takes an id and a name typed at a counter is
    not one.
    """
    said_name = " ".join(str(args.get("category") or "").split())
    if not said_name:
        raise AssistantRefused(
            R_NO_CATEGORY_NAMED,
            "no category was named, so there is nothing to list.")
    book = _ask("categories", "categories_ep")
    rows = list(book.get("categories") or [])
    wanted = normalise(said_name)
    hits = [c for c in rows if normalise(str(c.get("name") or "")) == wanted]
    if not hits:
        hits = [c for c in rows
                if all(any(h.startswith(t)
                           for h in normalise(str(c.get("name") or "")))
                       for t in wanted)]
    if not hits:
        names = ", ".join(str(c.get("name")) for c in rows[:MAX_LISTED])
        raise AssistantRefused(
            R_NO_SUCH_CATEGORY,
            f"this shop has no category called {said_name!r}. It has: "
            f"{names or 'none yet'}.")
    if len(hits) > 1:
        names = ", ".join(str(c.get("name")) for c in hits[:MAX_LISTED])
        raise AssistantRefused(
            R_NO_SUCH_CATEGORY,
            f"{said_name!r} matches {len(hits)} categories — {names}. Say "
            f"which one.")
    category_id = str(hits[0].get("category_id"))
    payload = _ask("categories", "categories_products_ep",
                   category=category_id, tag="", q="")
    products = list(payload.get("products") or [])
    if not products:
        said = (f"Nothing is filed under {hits[0].get('name')} yet.")
    else:
        listed = ", ".join(str(p.get("name")) for p in products[:MAX_LISTED])
        said = (f"{len(products)} products under {hits[0].get('name')}: "
                f"{listed}.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_day_close(args: dict[str, Any]) -> dict[str, Any]:
    """What the day book would freeze if the day were closed now."""
    payload = _ask("daybook", "preview_ep", day=None)
    brief = payload.get("derived") or {}
    bills = int(brief.get("bills") or 0)
    if not bills:
        said = (f"Nothing has been billed on {payload.get('day')}, so there is "
                f"nothing to close. That is the chain's answer, not a guess.")
    else:
        said = (f"{payload.get('day')}: {bills} bills, Rs "
                f"{brief.get('revenue_rupees')} taken, Rs "
                f"{brief.get('settled_rupees')} settled by the gateway and Rs "
                f"{brief.get('awaiting_rupees')} still waiting. Nothing has "
                f"been closed — this is only what closing would record.")
    warning = payload.get("chain_warning") or payload.get("warning")
    if warning:
        said = f"{said} {warning}"
    return {"answer": said, "data": payload, "proposal": None}


def _do_gst_of(args: dict[str, Any]) -> dict[str, Any]:
    """The rate recorded against one product, and the tax inside its price."""
    sku_id, rec = _sku_for(args, what="a tax question")
    payload = _ask("gst", "gst_product_ep", sku_id)
    row = payload.get("product") or {}
    name = str(row.get("name") or rec.get("name") or sku_id)
    if not row.get("set"):
        said = (f"No GST rate has been recorded for {name}, so this counter "
                f"cannot say what tax is inside its price.")
        proposed = row.get("suggestion")
        if proposed:
            said = (f"{said} The table proposes HSN {proposed.get('hsn')} at "
                    f"{proposed.get('rate')} per cent because "
                    f"{proposed.get('why')} — a person has to accept that on "
                    f"the GST screen; nothing here sets it.")
    else:
        split = row.get("at_marked_price") or {}
        said = (f"{name} is HSN {row.get('hsn')} at {row.get('rate')} per "
                f"cent. In its Rs {row.get('price_rupees')} price that is Rs "
                f"{split.get('tax_rupees')} of tax on Rs "
                f"{split.get('taxable_rupees')}, split half CGST half SGST.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_expiring(args: dict[str, Any]) -> dict[str, Any]:
    days = DEFAULT_EXPIRY_DAYS
    if args.get("days") is not None:
        days = _whole_number(args.get("days"), what="the number of days",
                             reason=R_BAD_DAYS)
    if days < 0 or days > MAX_EXPIRY_DAYS:
        raise AssistantRefused(
            R_BAD_DAYS,
            f"{days} is outside the 0 to {MAX_EXPIRY_DAYS} days this counter "
            f"looks ahead.")
    payload = _ask("expiry", "expiry_soon_ep", days=str(days))
    rows = list(payload.get("batches") or [])
    if not rows:
        said = (f"Nothing you have booked in goes off within {days} days. "
                f"Only batches somebody entered are in this — a packet with no "
                f"batch is invisible to it.")
    else:
        listed = ", ".join(
            f"{r.get('name') or r.get('sku_id')} "
            f"({r.get('units')} by {r.get('expires_on')})"
            for r in rows[:MAX_LISTED])
        said = (f"{len(rows)} batches go off within {days} days: {listed}. At "
                f"their marked price that is Rs "
                f"{payload.get('value_at_risk_rupees')} — a description of "
                f"what they would fetch, not a loss the books record.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_expired(args: dict[str, Any]) -> dict[str, Any]:
    payload = _ask("expiry", "expiry_expired_ep")
    rows = list(payload.get("batches") or [])
    if not rows:
        said = ("Nothing you have booked in is past its date with units still "
                "on it.")
    else:
        listed = ", ".join(
            f"{r.get('name') or r.get('sku_id')} "
            f"({r.get('units')} since {r.get('expires_on')})"
            for r in rows[:MAX_LISTED])
        said = (f"{len(rows)} batches are past their date with units still on "
                f"them: {listed}. Take them off the shelf and write the "
                f"movement down so the count stops including them.")
    return {"answer": said, "data": payload, "proposal": None}


def _do_weighed(args: dict[str, Any]) -> dict[str, Any]:
    """Price a weight of something sold loose. It bills nothing and writes
    nothing — weighed.py owns writing a weighed line down."""
    sku_id, rec = _sku_for(args, what="a weight")
    name = str(rec.get("name") or sku_id)
    if sku_id not in _sold_by_weight():
        raise AssistantRefused(
            R_NOT_WEIGHED,
            f"{name} is not sold by weight at this counter — it is billed in "
            f"packets. Mark it with a price per kilo on the weighed screen "
            f"first, or say how many packets.")
    kg = args.get("kg")
    if kg is not None and not isinstance(kg, str):
        raise AssistantRefused(
            R_BAD_AMOUNT,
            f"the weight in kilograms came through as a {type(kg).__name__}. "
            f"It has to be text such as \"2.5\", because a weight never goes "
            f"through a float here.")
    qty = None
    if args.get("qty") is not None:
        qty = _whole_number(args.get("qty"), what="the count",
                            reason=R_BAD_QTY)
    if kg is None and not args.get("unit") and not args.get("fraction"):
        raise AssistantRefused(
            R_NO_WEIGHT,
            f"no weight was said. {name} is sold by the kilo, so say how much "
            f"— 'aadha kilo', '250 gram', or the weight off the scale.")
    line = _weighed_line(sku_id, name, qty, args.get("unit"),
                         args.get("fraction"),
                         kg if isinstance(kg, str) else None,
                         taught_with=str(rec.get("how") or "unknown"))
    return {
        "answer": (f"{line['weight']} of {name} at Rs "
                   f"{line['price_per_kg_rupees']} a kilo is Rs "
                   f"{line['line_rupees']}. {line['arithmetic']} — the "
                   f"remainder goes to the customer, never the other way. "
                   f"Nothing is on a bill."),
        "data": line,
        "proposal": None,
    }


#: tool name -> the function that runs it. A DICTIONARY rather than a chain of
#: ifs so that "does every declared tool have an implementation" is one set
#: comparison, which a test makes.
_EXECUTORS: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {
    TOOL_ADD: _do_add,
    TOOL_PRICE: lambda a, _b: _do_price(a),
    TOOL_FIND: lambda a, _b: _do_find(a),
    TOOL_ORDERS: lambda a, _b: _do_orders(a),
    TOOL_TAKINGS: lambda a, _b: _do_takings(a),
    TOOL_LOW_STOCK: lambda a, _b: _do_low_stock(a),
    TOOL_STOCK_ON_HAND: lambda a, _b: _do_stock_on_hand(a),
    TOOL_STOCK_MOVEMENTS: lambda a, _b: _do_stock_movements(a),
    TOOL_REORDER_LIST: lambda a, _b: _do_reorder_list(a),
    TOOL_PROPOSE_MOVEMENT: _do_propose_movement,
    TOOL_EXPENSES_TODAY: lambda a, _b: _do_expenses_today(a),
    TOOL_CASH_POSITION: lambda a, _b: _do_cash_position(a),
    TOOL_PROPOSE_EXPENSE: _do_propose_expense,
    TOOL_MARGIN_OF: lambda a, _b: _do_margin_of(a),
    TOOL_MARGIN_TODAY: lambda a, _b: _do_margin_today(a),
    TOOL_SUPPLIERS: lambda a, _b: _do_suppliers(a),
    TOOL_CUSTOMER: lambda a, _b: _do_customer(a),
    TOOL_REGULARS: lambda a, _b: _do_regulars(a),
    TOOL_CATEGORIES: lambda a, _b: _do_categories(a),
    TOOL_IN_CATEGORY: lambda a, _b: _do_in_category(a),
    TOOL_DAY_CLOSE: lambda a, _b: _do_day_close(a),
    TOOL_OFFERS: lambda a, _b: _do_offers(a),
    TOOL_GST_OF: lambda a, _b: _do_gst_of(a),
    TOOL_EXPIRING: lambda a, _b: _do_expiring(a),
    TOOL_EXPIRED: lambda a, _b: _do_expired(a),
    TOOL_LOYALTY: lambda a, _b: _do_loyalty(a),
    TOOL_LOYALTY_RULES: lambda a, _b: _do_loyalty_rules(a),
    TOOL_WEIGHED: lambda a, _b: _do_weighed(a),
    TOOL_KHATA_BOOK: _do_khata_book,
    TOOL_KHATA_BALANCE: lambda a, _b: _do_khata_balance(a),
    TOOL_BANK: lambda a, _b: _do_bank_settlement(a),
}


def execute(tool: str, args: dict[str, Any], *,
            brain: str = BRAIN_LOCAL) -> dict[str, Any]:
    """Run one tool HERE, against this counter's own files.

    This is the half of the design the model has no part in. By the time
    control reaches this function the only thing that came from outside the
    machine is a tool name and a few words of the shopkeeper's own sentence.
    """
    run = _EXECUTORS.get(tool)
    if run is not None:
        return run(args, brain)
    raise AssistantRefused(
        R_UNKNOWN_TOOL,
        f"{tool!r} is not a tool this counter has. It has: "
        f"{', '.join(TOOL_NAMES)}.")


# --------------------------------------------------------------- the model --
#
# THE TRANSPORT IS INJECTED. A test supplies a fake and asserts on the exact
# bytes that would have gone out; nothing in the test suite ever reaches the
# real API, and nothing in this file can be made to by an input.

Transport = Callable[[str, dict[str, str], bytes, int], "tuple[int, Any]"]

_DEPS: dict[str, Any] = {"transport": None}


def set_transport(fn: Optional[Transport]) -> None:
    """Replace the HTTP transport. `None` restores the real one."""
    _DEPS["transport"] = fn


def transport() -> Transport:
    return _DEPS["transport"] or _urllib_transport


def _urllib_transport(url: str, headers: dict[str, str], body: bytes,
                      timeout: int) -> "tuple[int, Any]":
    """One POST. Raises nothing that carries a header, and so cannot leak a key.

    The exception TYPE and the HTTP status are what come back out of here. The
    request object holds the Authorization header and is never stringified into
    a message.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 - error bodies are not always JSON
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001 - urllib raises a wide family
        raise GrokUnavailable(
            R_GROK_UNREACHABLE,
            f"{base_url()} did not answer ({type(exc).__name__}). The counter "
            f"answered on its own parser instead.") from None


def payload_for(text: str) -> dict[str, Any]:
    """THE ENTIRE REQUEST BODY, in one function so the claim is checkable.

    Two messages and the tool schemas. There is no place in this dictionary for
    a catalogue, a price, an order, a stock count or a customer, and a test
    reads the serialised bytes back and asserts none of the shop's own strings
    are in them.
    """
    return {
        "model": model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "tools": [dict(t) for t in TOOLS],
        "tool_choice": "auto",
        "temperature": 0,
        "stream": False,
    }


def declared_arguments(tool: str) -> frozenset[str]:
    """The argument names this tool's own schema declares. Empty if unknown."""
    for t in TOOLS:
        fn = t["function"]
        if fn["name"] == tool:
            return frozenset(fn["parameters"]["properties"])
    return frozenset()


def money_shaped_arguments() -> dict[str, frozenset[str]]:
    """{tool -> the money-shaped arguments it declares}. Should be tiny.

    Published as a function so a test can assert the whole of it rather than
    trusting a comment: the list of places a rupee figure may enter this module
    from outside is a security property, and it should be readable in one call.
    """
    out: dict[str, frozenset[str]] = {}
    for t in TOOLS:
        fn = t["function"]
        money = frozenset(
            name for name in fn["parameters"]["properties"]
            if any(w in name.lower() for w in _MONEY_ARG_WORDS))
        if money:
            out[fn["name"]] = money
    return out


def _check_arguments(tool: str, args: Any) -> dict[str, Any]:
    """The model's arguments, or a refusal. A money-shaped key is never dropped.

    A MONEY-SHAPED ARGUMENT IS REFUSED UNLESS THIS TOOL'S OWN SCHEMA DECLARES
    ONE. That is the whole rule, and it is stricter than the word list it
    replaced: the model cannot invent an argument, so it cannot invent a place
    to put a number. Exactly one tool declares one — `propose_expense`, whose
    amount is a figure the shopkeeper said out loud rather than a price this
    counter derives — and even there the value arrives as text and is parsed by
    `money.from_rupees_str`, which never touches a float.

    Quietly ignoring an invented price would hide the one failure mode that
    matters here: a model that has started making numbers up. It is refused by
    name so the operator sees it happen.
    """
    if not isinstance(args, dict):
        raise AssistantRefused(
            R_BAD_TOOL_ARGS,
            f"the model's arguments for {tool!r} are a "
            f"{type(args).__name__}, not an object. Nothing was done.")
    allowed = declared_arguments(tool)
    for key in args:
        k = str(key).lower()
        if any(w in k for w in _MONEY_ARG_WORDS) and str(key) not in allowed:
            raise AssistantRefused(
                R_MODEL_PRICED,
                f"the model returned {key!r} in the arguments for {tool!r}, "
                f"which declares no such argument. It is never given this "
                f"shop's prices, so any number it puts there is invented. "
                f"Nothing was priced and nothing was proposed.")
    return dict(args)


def grok_route(text: str) -> tuple[str, dict[str, Any]]:
    """Ask the model which tool this sentence is. Returns (tool, arguments).

    Raises GrokUnavailable when the provider did not answer usefully — the
    caller falls back to the local parser. Raises AssistantRefused when the
    model DID answer and broke the contract, because that is not a network
    problem and hiding it behind a fallback would make it invisible.
    """
    key = api_key()
    if not key:
        raise GrokUnavailable(
            R_GROK_UNREACHABLE,
            "XAI_API_KEY is not set, so the counter used its own parser.")
    body = json.dumps(payload_for(text)).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    status, data = transport()(f"{base_url()}/chat/completions", headers, body,
                               XAI_TIMEOUT_S)
    if int(status) != 200:
        raise GrokUnavailable(
            R_GROK_HTTP,
            f"the model service answered HTTP {status}. The counter answered "
            f"on its own parser instead.")
    if not isinstance(data, dict):
        raise GrokUnavailable(
            R_GROK_SHAPE,
            f"the model service answered with a {type(data).__name__}, not a "
            f"chat completion.")
    try:
        message = data["choices"][0]["message"]
    except Exception:  # noqa: BLE001 - a shape we do not recognise
        raise GrokUnavailable(
            R_GROK_SHAPE,
            "the model service's answer had no choices in it.") from None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not calls:
        raise GrokUnavailable(
            R_NO_TOOL_CALL,
            "the model replied with prose instead of choosing a tool, so there "
            "was nothing to run.")
    try:
        fn = calls[0]["function"]
        name = str(fn["name"])
        raw = fn.get("arguments")
    except Exception:  # noqa: BLE001
        raise GrokUnavailable(
            R_GROK_SHAPE,
            "the model's tool call had no function name in it.") from None
    if name not in TOOL_NAMES:
        raise AssistantRefused(
            R_UNKNOWN_TOOL,
            f"the model asked for a tool called {name!r}, which this counter "
            f"does not have. It has: {', '.join(TOOL_NAMES)}. Nothing was "
            f"done.")
    if raw is None or raw == "":
        args: Any = {}
    elif isinstance(raw, dict):
        args = raw
    else:
        try:
            args = json.loads(raw)
        except Exception:  # noqa: BLE001
            raise AssistantRefused(
                R_BAD_TOOL_ARGS,
                f"the model's arguments for {name!r} are not JSON. Nothing was "
                f"done.") from None
    return name, _check_arguments(name, args)


def _route(text: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Which tool, from whichever brain could answer. Returns the brain block.

    The fallback is NEVER silent: `brain` names who actually answered and
    `grok_error` says why the model did not, so "the assistant got worse this
    afternoon" has an answer on the screen instead of in a log nobody reads.
    """
    if not api_key():
        tool, args = local_route(text)
        return tool, args, {"brain": BRAIN_LOCAL, "model": None,
                            "key_present": False, "grok_error": None}
    try:
        tool, args = grok_route(text)
        return tool, args, {"brain": brain_name(), "model": model_name(),
                            "key_present": True, "grok_error": None}
    except GrokUnavailable as exc:
        tool, args = local_route(text)
        return tool, args, {
            "brain": BRAIN_LOCAL, "model": None, "key_present": True,
            "grok_error": {"reason": exc.reason, "detail": exc.detail}}


# ----------------------------------------------------------- reading a body --


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a named refusal
        raise AssistantRefused(
            R_BAD_BODY, "this request's body is not JSON.") from None
    if not isinstance(body, dict):
        raise AssistantRefused(
            R_BAD_BODY,
            f"this request's body is a {type(body).__name__}; it must be a "
            f"JSON object.")
    return body


def _read_text(body: dict[str, Any]) -> str:
    raw = body.get("text")
    if raw is None:
        raw = body.get("say")
    if raw is None:
        raise AssistantRefused(
            R_NO_TEXT,
            "no 'text' was sent. This endpoint takes one sentence, typed or "
            "dictated.")
    if not isinstance(raw, str):
        raise AssistantRefused(
            R_BAD_BODY,
            f"'text' must be a string, not a {type(raw).__name__}.")
    s = " ".join(raw.split())
    if not s:
        raise AssistantRefused(
            R_NO_TEXT, "nothing was said, so there is nothing to do.")
    if len(s) > MAX_TEXT:
        raise AssistantRefused(
            R_TEXT_TOO_LONG,
            f"that is {len(s)} characters and the cap is {MAX_TEXT}. This "
            f"takes one sentence at a time — and the shorter it is, the less "
            f"of it leaves the machine.")
    return s


def _read_source(body: dict[str, Any]) -> str:
    raw = body.get("source")
    if raw is None:
        return "text"
    if not isinstance(raw, str) or raw.strip().lower() not in SOURCES:
        raise AssistantRefused(
            R_BAD_SOURCE,
            f"{raw!r} is not where a sentence can come from. It is "
            f"{' or '.join(SOURCES)} — and both are treated identically, "
            f"because voice moves no money here either.")
    return raw.strip().lower()


def _refuse_authorship(body: dict[str, Any]) -> None:
    """INVARIANT: the browser is never an author.

    The page sends a sentence. It does not send lines, sku ids or rupees, and
    if it tries the request is refused rather than partly honoured — a page that
    can smuggle a price past the assistant is a page that can put a number on a
    bill the shop never agreed to.
    """
    for key in body:
        k = str(key).lower()
        if k in ("text", "say", "source"):
            continue
        if any(w in k for w in _CLIENT_AUTHOR_KEYS):
            raise AssistantRefused(
                R_CLIENT_AUTHORED,
                f"this request carries {key!r}. The page sends what was said "
                f"and nothing else: the counter resolves the products and the "
                f"counter puts the prices on. Nothing was done.")


# ----------------------------------------------------------------- routes --


@router.get("/assistant/health")
def assistant_health_ep() -> JSONResponse:
    """Which brain would answer right now, and what this thing can reach.

    `key_present` is a boolean and there is no route anywhere in this module
    that returns the key itself.
    """
    try:
        try:
            n_products = len(catalogue())
            catalogue_problem = None
        except AssistantRefused as exc:
            n_products = 0
            catalogue_problem = {"reason": exc.reason, "detail": exc.detail}
        present = bool(api_key())
        # WHICH MODULES ARE ACTUALLY REACHABLE, asked rather than assumed. A
        # tool whose module has moved answers with a sentence instead of a
        # number, and this is where an operator finds out before a shopkeeper
        # does.
        modules: dict[str, Any] = {}
        for alias, (name, reason, owns) in sorted(_MODULES.items()):
            try:
                _module(alias)
                modules[alias] = {"file": f"gawaah/{name}.py", "there": True,
                                  "owns": owns, "refusal": None}
            except AssistantRefused as exc:
                modules[alias] = {"file": f"gawaah/{name}.py", "there": False,
                                  "owns": owns,
                                  "refusal": {"reason": exc.reason,
                                              "detail": exc.detail}}
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "brain": brain_name() if present else BRAIN_LOCAL,
            "key_present": present,
            "model": model_name() if present else None,
            "base_url": base_url(),
            "tools": list(TOOL_NAMES),
            "tool_count": len(TOOL_NAMES),
            "proposal_kinds": [KIND_BILL, KIND_MOVEMENT, KIND_EXPENSE],
            "modules": modules,
            "modules_reachable": sum(1 for m in modules.values() if m["there"]),
            "languages": ["Hinglish", "Hindi", "Bengali", "English"],
            "scripts": ["Latin", "Bengali digits", "Devanagari digits"],
            "products_priced": n_products,
            "catalogue_problem": catalogue_problem,
            "sources": list(SOURCES),
            "money_shaped_arguments": {
                tool: sorted(names)
                for tool, names in money_shaped_arguments().items()},
            "sends_to_the_model": ("the sentence and the tool schemas, and "
                                   "nothing else — no catalogue, no prices, no "
                                   "orders, no takings, no stock, no "
                                   "customers"),
            "note": ("With no XAI_API_KEY this counter answers on its own "
                     "parser, which understands counts and products in three "
                     "languages and the questions the tools above cover. "
                     "Nothing here bills anything, moves any stock, records "
                     "any expense or settles any money."),
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/assistant/tools")
def assistant_tools_ep() -> JSONResponse:
    """Exactly what is sent to the model, so the privacy claim is inspectable.

    The schemas below and the system prompt are the WHOLE of the context the
    provider ever receives, beside one sentence a person typed or said.
    """
    try:
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "count": len(TOOLS),
            "tools": [dict(t) for t in TOOLS],
            "system_prompt": SYSTEM_PROMPT,
            "model": model_name(),
            "note": ("Nothing about this shop is in this payload. The product "
                     "words in a tool call are the shopkeeper's own; the sku "
                     "they mean, and its price, are resolved on this machine "
                     "afterwards."),
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/assistant/ask")
async def assistant_ask_ep(request: Request) -> JSONResponse:
    """One sentence in. An answer about this shop, or a proposal to accept.

    Body: {"text": "do Maggi add karo", "source": "voice"|"text"}. Voice and
    text are the same path and the same rules: nothing on either of them can
    write a bill, move stock or reach a payment gateway.
    """
    try:
        body = await _json_body(request)
        _refuse_authorship(body)
        text = _read_text(body)
        source = _read_source(body)
    except AssistantRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)

    # The brain block is set up BEFORE routing so a refusal still says who was
    # asked. If the model answered and broke the contract, the response must not
    # read as though the local parser had refused.
    _present = bool(api_key())
    brain: dict[str, Any] = {
        "brain": brain_name() if _present else BRAIN_LOCAL,
        "model": model_name() if _present else None,
        "key_present": _present, "grok_error": None}
    try:
        tool, args, brain = _route(text)
        result = execute(tool, args, brain=str(brain["brain"]))
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "heard": text,
            "source": source,
            "tool": tool,
            "arguments": args,
            "answer": result["answer"],
            "proposal": result.get("proposal"),
            "data": result.get("data"),
            **brain,
            "note": ("Nothing here has been billed, charged or taken off a "
                     "shelf. A proposal becomes a bill line only when a person "
                     "accepts it on the till."),
        })
    except AssistantRefused as exc:
        return _refusal(exc, **brain)
    except MoneyError as exc:
        return _refusal(AssistantRefused(
            R_NO_CATALOGUE,
            f"a price in this shop's catalogue is not integer paise ({exc}). "
            f"Nothing was proposed."), **brain)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/assistant/proposal/{proposal_id}")
def assistant_proposal_ep(proposal_id: str) -> JSONResponse:
    """Read a proposal back. It is still not a bill and still settles nothing."""
    try:
        doc = read_proposal(proposal_id)
        return JSONResponse({"ok": True, "settles_money": False,
                             "proposal": doc})
    except AssistantRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_PROPOSAL else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
