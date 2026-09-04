"""PARCHI (पर्ची) — photograph the wholesaler's bill, and the margin becomes known.

The one number a kirana runs on is margin, and until a cost price is on file
the books say "no product sold today has a recorded cost". The cost prices
exist — on a printed distributor invoice in the shopkeeper's hand — and typing
forty lines of it into a form is the step nobody takes. So this module takes
the photograph instead.

THIS IS THE ONE PLACE A LANGUAGE MODEL GENUINELY EARNS ITS SEAT. A messy
printed document into structured lines is a job the deterministic parser in
`assistant.py` cannot do, and the vision model does it well. Everything around
that one call is deterministic and refuses by name, exactly like the rest of
this product:

    THE VISION CALL   the photograph goes to the provider behind the key the
                      advisor already uses (`assistant.api_key()`), and the
                      answer is a strict JSON schema of {name, qty, rate,
                      amount} lines plus the printed total, supplier and
                      date. The model is never shown this shop's catalogue,
                      a price, or a sku id, and a test reads the request
                      bytes back to prove it.

    THE MATCH, LOCALLY  each printed name is matched against the catalogue on
                      THIS machine, through `search.py`'s typo-tolerant name
                      match. An exact hit is PROPOSED; a fuzzy one asks
                      "confirm?"; nothing usable is an EXCEPTION row that
                      offers the Products screen as a link and nothing more.

    THE ARITHMETIC GATE  every figure is parsed digit by digit into integer
                      paise through `money.from_rupees_str` — never a float
                      from OCR. For every line qty x rate must equal the
                      printed amount, and the lines must add to the printed
                      subtotal, and subtotal plus the printed taxes must
                      equal the printed total. ONE PAISA OFF REFUSES, and the
                      refusal names the line. A figure that does not
                      reconcile is parked and named, never netted or rounded.

    THE BOOKING       a PERSON accepts the lines that survived, and they are
                      recorded through `purchases.record_purchase` — the same
                      writer, the same refusals, the same chain as a typed
                      invoice. This module writes no purchase file of its own.

WHAT LEAVES THE MACHINE. The photograph, and therefore the supplier's name and
the cost prices printed on it. Nothing else: no catalogue, no selling price, no
customer, no takings. The response says so under `left_the_machine` in the
same shape the advisor uses, and the fact is logged on this module's own
hash chain (`<shop>/parchi.audit.jsonl`), never on `results/audit.jsonl`,
which has one writer and it is not this.

NO RAZORPAY PRODUCT IS USED HERE. Nothing is paid, minted, or settled;
`settles_money` is false on every response as a fact about the code. This is
the cost side of the counter, and the gateway has nothing to say about it.

NO KEY, NO GUESS. With no provider key the status endpoint says so, the parse
endpoint refuses by name, and the typed invoice form on the Purchases screen
— which already exists — is the way in.

MOUNTING. The router carries NO prefix; the paths below are absolute::

    from gawaah import parchi
    app.include_router(parchi.router, dependencies=AUTH_GUARD)
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import assistant, purchases, search
from .ledger import Ledger
from .money import MoneyError, from_rupees_str, paise, to_rupees_str

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# Named for the STATE, not the fix; the sentence that says what to do is in
# `detail`. Every one is reachable and every one has a test that fires it.

R_NO_KEY = "parchi_no_model_key"
R_NO_PHOTOGRAPH = "parchi_no_photograph"
R_NOT_AN_IMAGE = "parchi_not_an_image"
R_PHOTOGRAPH_TOO_LARGE = "parchi_photograph_too_large"
R_MODEL_UNREACHABLE = "parchi_model_unreachable"
R_MODEL_HTTP = "parchi_model_http"
R_MODEL_UNREADABLE = "parchi_model_answer_unreadable"
R_NO_LINES = "parchi_no_lines_read"
R_TOO_MANY_LINES = "parchi_too_many_lines"
R_ARITHMETIC = "parchi_arithmetic_refused"
R_BAD_ID = "parchi_id_malformed"
R_NO_PARCHI = "no_such_parchi"
R_ALREADY_BOOKED = "parchi_already_booked"
R_BAD_BODY = "parchi_body_not_json"
R_NO_ACCEPTED_LINES = "parchi_no_lines_accepted"
R_LINE_NOT_BOOKABLE = "parchi_line_not_bookable"
R_SUPPLIER_UNRESOLVED = "parchi_supplier_unresolved"
R_INTERNAL = "parchi_internal_error"

#: Per-line arithmetic states. `LINE_UNREADABLE` is a figure that could not be
#: read as digits at all — "illegible", a float, a stray word — and it fails
#: the gate exactly as a wrong figure does, because a line that cannot be
#: checked is a line that cannot be booked.
LINE_OK = "ok"
LINE_FAILS = "fails"
LINE_UNREADABLE = "unreadable"

#: Per-line match states.
MATCH_PROPOSED = "proposed"
MATCH_CONFIRM = "confirm"
MATCH_NONE = "none"


class ParchiRefused(Exception):
    def __init__(self, reason: str, detail: str, status: int = 400,
                 **extra: Any) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.status = status
        self.extra = extra


def _refusal(exc: ParchiRefused) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "reason": exc.reason, "detail": exc.detail,
         "settles_money": False, **exc.extra},
        status_code=exc.status)


def _crash(exc: BaseException) -> JSONResponse:
    """The last resort. A 500 teaches the reader nothing, so there are none."""
    return JSONResponse(
        {"ok": False, "reason": R_INTERNAL,
         "detail": f"{type(exc).__name__}: {exc}", "settles_money": False},
        status_code=400)


# ----------------------------------------------------------------- limits --

#: A phone photograph of an A4 bill is two to five megabytes. Twelve is a
#: generous ceiling; past it the request is a mistake, and the provider bills
#: by the byte.
MAX_PHOTOGRAPH_BYTES = 12 * 1024 * 1024

#: The same cap `purchases.py` keeps on one invoice, because that is where the
#: lines are going.
MAX_LINES = purchases.MAX_LINES

#: How many catalogue candidates a "confirm?" row offers. Three is what fits
#: in a select a shopkeeper will actually read.
MAX_CANDIDATES = 3

TIMEOUT_S = 60
PARCHI_FORMAT = 1
PARCHI_ID_RE = re.compile(r"^prc_[0-9a-f]{12}$")
PARCHI_SUBDIR = "parchi"
AUDIT_SIDECAR = "parchi.audit.jsonl"

#: `search.py`'s scores, read as: PROPOSED when every printed word is exactly
#: a word of the catalogue name (S_TOKEN_EXACT is the mean when all are), or
#: the printed name IS the catalogue name or its start. Below that but at or
#: above CONFIRM_AT, something was inferred — a word matched as a prefix
#: ("2-MIN" for "2-Minute"), within the typo budget, or the printed name is
#: only part of the catalogue name ("RED LABEL TEA 250G" inside "Brooke Bond
#: Red Label Tea 250 g") — and a person confirms it. Measured on the bench:
#: 24 of 30 printed lines score 700 or above, 5 land in the confirm band,
#: and the one product not in the catalogue scores nothing.
PROPOSE_AT = search.S_TOKEN_EXACT        # 700
CONFIRM_AT = search.S_FUZZY_2            # 200
#: A runner-up this close to the winner makes the winner a guess, not a match:
#: "Amul Butter 100 g" against a catalogue holding two Amul butters.
AMBIGUITY_GAP = 60

#: Words distributors print that name PACKAGING, not the product. "MAGGI 70G
#: PKT" must reach "Maggi 70 g"; `search.py` requires every query word to land
#: somewhere, so these are dropped from the query before it is asked.
_PACKAGING_WORDS = frozenset({
    "pkt", "pkts", "pc", "pcs", "nos", "no", "ctn", "case", "box", "bx",
    "mrp", "each", "ea", "unit", "units", "pack", "pk", "dz", "dzn",
})


# ------------------------------------------------------------- provider --

#: What the model is told. THE ENTIRE INSTRUCTION, in one constant, so the
#: claim "the catalogue is not in it" is checkable by reading it. Figures are
#: asked for as STRINGS because a JSON number is a float by the time anything
#: parses it, and 8.20 x 48 is not 393.60 once that has happened.
PROMPT = (
    "You are reading ONE photograph of a wholesaler's or distributor's "
    "invoice (a purchase bill) for a small Indian grocery shop. Transcribe "
    "it into the JSON schema you were given.\n"
    "- Copy every figure DIGIT BY DIGIT exactly as printed, as a string of "
    "digits with at most two decimal places (write 393.60 as \"393.60\", "
    "8 as \"8\"). Never compute, round, correct or reconcile anything; if a "
    "line's arithmetic looks wrong, copy what is printed anyway.\n"
    "- One entry in `lines` per item row, in printed order: `name` exactly as "
    "printed (abbreviations and all), `qty` as a whole number, `rate` the "
    "per-unit price, `amount` the line's printed amount.\n"
    "- `subtotal` is the printed sum before tax, or \"\" if none is printed. "
    "`taxes` holds every tax or charge line (CGST, SGST, IGST, cess, "
    "freight, round-off) with its printed label and amount; write a "
    "negative round-off as \"-0.30\". `printed_total` is the final total "
    "printed on the bill.\n"
    "- `supplier.name` is the seller's business name from the header; "
    "`supplier.phone` its phone number if printed, else \"\". `invoice_no` "
    "and `date` as printed, `date` as YYYY-MM-DD when you can read it "
    "unambiguously, else \"\".\n"
    "- If any figure is illegible, write \"illegible\" in that field rather "
    "than guessing.\n"
    "You have not been given this shop's catalogue and must not invent "
    "product names, prices or totals."
)


def _schema(strict: bool) -> dict[str, Any]:
    """The answer's shape. `strict` adds what OpenAI-shaped providers need for
    structured output (every field required, no extras); the Google native
    endpoint rejects `additionalProperties`, so it gets the plain one."""
    def obj(props: dict[str, Any]) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "object", "properties": props,
                             "required": list(props)}
        if strict:
            d["additionalProperties"] = False
        return d

    s = {"type": "string"}
    line = obj({"name": s, "qty": {"type": "integer"}, "rate": s, "amount": s})
    tax = obj({"label": s, "amount": s})
    return obj({
        "supplier": obj({"name": s, "phone": s}),
        "invoice_no": s,
        "date": s,
        "lines": {"type": "array", "items": line},
        "subtotal": s,
        "taxes": {"type": "array", "items": tax},
        "printed_total": s,
    })


def model_name() -> str:
    """`GAWAAH_PARCHI_MODEL`, else the advisor's own model. The one this
    counter ships with (gemini-3.1-flash-lite) reads images."""
    return (os.environ.get("GAWAAH_PARCHI_MODEL") or "").strip() or assistant.model_name()


def _host() -> str:
    return assistant.base_url().split("//", 1)[-1].split("/", 1)[0].lower()


def provider() -> str:
    """`google` for the native generateContent API, `openai` for anything that
    speaks chat-completions with image parts (xAI, OpenAI, a proxy)."""
    return "google" if "googleapis.com" in _host() else "openai"


def endpoint() -> str:
    """Where the photograph goes.

    Google: the chat client talks to `/v1beta/openai`, the OpenAI-shaped
    facade, whose structured-output support is the thinner of the two. The
    native endpoint is on the same origin with the facade segment dropped —
    exactly how `tts.py` reaches audio — and it honours `responseSchema`,
    which is what makes the answer's shape a contract rather than a hope.
    """
    base = assistant.base_url()
    if provider() == "google":
        if base.endswith("/openai"):
            base = base[: -len("/openai")]
        return f"{base}/models/{model_name()}:generateContent"
    return f"{base}/chat/completions"


def available() -> tuple[bool, Optional[str]]:
    """(can this counter read a bill, why not). Asked fresh every time, like
    the key, so an operator who exports one after the till started does not
    restart it."""
    if not assistant.api_key():
        return False, ("no model key is set, so a photograph cannot be read. "
                       "The typed invoice form still works, and nothing "
                       "leaves this machine.")
    return True, None


def _headers() -> dict[str, str]:
    if provider() == "google":
        return {"Content-Type": "application/json",
                "x-goog-api-key": assistant.api_key()}
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {assistant.api_key()}"}


def payload_for(image: bytes, mime: str) -> dict[str, Any]:
    """THE ENTIRE REQUEST BODY, in one function so the claim is checkable.

    The photograph, the instruction, the schema. There is no place in this
    dictionary for a catalogue, a price, a sku id or a supplier list, and a
    test serialises it and asserts none of the shop's own strings are in it.
    """
    b64 = base64.b64encode(image).decode("ascii")
    if provider() == "google":
        return {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": PROMPT},
            ]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": _schema(strict=False),
            },
        }
    return {
        "model": model_name(),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": "Transcribe this invoice."},
            ]},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "parchi", "strict": True,
                            "schema": _schema(strict=True)},
        },
        "stream": False,
    }


# ------------------------------------------------------------ transport --
#
# INJECTED, like `tts.set_transport`. The suite runs against a fake and asserts
# on the bytes; nothing in the tests reaches a provider.

Transport = Callable[[str, dict[str, str], bytes, int], "tuple[int, Any]"]
_DEPS: dict[str, Any] = {"transport": None}


def set_transport(fn: Optional[Transport]) -> None:
    _DEPS["transport"] = fn


def transport() -> Transport:
    return _DEPS["transport"] or _urllib_post


def _urllib_post(url: str, headers: dict[str, str], body: bytes, timeout: int
                 ) -> "tuple[int, Any]":
    """One POST. The request object holds the key and is never stringified."""
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
        raise ParchiRefused(
            R_MODEL_UNREACHABLE,
            f"the model service did not answer ({type(exc).__name__}). "
            f"Nothing was read; the typed invoice form still works.") from None


def _text_of(data: Any) -> str:
    """The model's text out of either envelope, or a named refusal."""
    try:
        if provider() == "google":
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(str(p.get("text") or "") for p in parts
                           if isinstance(p, dict))
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            return "".join(str(p.get("text") or "") for p in content
                           if isinstance(p, dict))
        return str(content or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ParchiRefused(
            R_MODEL_UNREADABLE,
            "the model service answered without any text in it. Nothing was "
            "read.") from None


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def read_invoice(image: bytes, mime: str) -> dict[str, Any]:
    """The photograph to the model, the model's JSON back. Raises ParchiRefused.

    Only the transport and the envelope live here; nothing is judged. What
    comes back is whatever the model claims the bill says, and every claim in
    it is checked downstream against arithmetic and the catalogue.
    """
    ok, why = available()
    if not ok:
        raise ParchiRefused(R_NO_KEY, why or "no model key is set.")
    body = json.dumps(payload_for(image, mime)).encode("utf-8")
    status, data = transport()(endpoint(), _headers(), body, TIMEOUT_S)
    if int(status) != 200:
        msg = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or "")[:200]
        raise ParchiRefused(
            R_MODEL_HTTP,
            f"the model service answered HTTP {status}"
            + (f": {msg}" if msg else "") + ". Nothing was read.")
    text = _text_of(data)
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        doc = json.loads(text)
    except ValueError:
        raise ParchiRefused(
            R_MODEL_UNREADABLE,
            "the model replied with prose instead of the JSON it was asked "
            "for, so there is nothing to check. Nothing was read.") from None
    if not isinstance(doc, dict):
        raise ParchiRefused(
            R_MODEL_UNREADABLE,
            f"the model's answer is a {type(doc).__name__}, not the object it "
            f"was asked for. Nothing was read.")
    return doc


# --------------------------------------------------------------- figures --

_CURRENCY = re.compile(r"(?i)^(?:rs\.?|inr|₹|rupees?)\s*")
_TRAIL = re.compile(r"\s*/-?$")


def figure_paise(raw: Any) -> tuple[Optional[int], Optional[str]]:
    """A printed figure as integer paise, digit by digit — or None and why.

    Accepts what a bill prints: "393.60", "₹ 8.20", "Rs.1,245.00", "8",
    "1245/-", a leading minus for a round-off, and a JSON integer. REFUSES a
    float: a float has already been rounded by whoever produced it, and this
    gate exists to catch one paisa. It refuses "illegible", which is the model
    saying it could not read the figure, and that is the right thing for it
    to have said.
    """
    if isinstance(raw, bool):
        return None, "a true/false is not a figure"
    if isinstance(raw, float):
        return None, ("the figure arrived as a floating-point number, which "
                      "has already been rounded; a bill's figures are read as "
                      "digits")
    if isinstance(raw, int):
        try:
            return int(paise(raw * 100)), None
        except MoneyError as exc:
            return None, str(exc)
    if not isinstance(raw, str):
        return None, f"a {type(raw).__name__} is not a figure"
    s = raw.strip()
    if not s:
        return None, "nothing was printed here"
    if s.lower() == "illegible":
        return None, "the model could not read this figure"
    s = _CURRENCY.sub("", s)
    s = _TRAIL.sub("", s)
    s = s.replace(",", "").replace(" ", "")
    try:
        return int(from_rupees_str(s)), None
    except MoneyError:
        return None, f"{raw!r} is not a figure of rupees and paise"


def _whole(raw: Any) -> Optional[int]:
    """A quantity as a whole number, or None. '48' and 48 both count; 48.0
    does not, because a count of packets is never a float."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdecimal():
        return int(raw.strip())
    return None


_DATE_SHAPES = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$"), (3, 2, 1)),
)


def _date_of(raw: Any) -> Optional[str]:
    """YYYY-MM-DD if the printed date reads as one unambiguously (ISO, or the
    Indian DD/MM/YYYY), on or before today. Anything else is None and the
    booking defaults to today — a bill dated in the future or in a shape that
    could be either of two days is not a date this counter will guess at."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    for shape, (y, m, d) in _DATE_SHAPES:
        hit = shape.match(s)
        if not hit:
            continue
        label = f"{int(hit.group(y)):04d}-{int(hit.group(m)):02d}-{int(hit.group(d)):02d}"
        try:
            _dt.date.fromisoformat(label)
        except ValueError:
            return None
        return label if label <= purchases._today_label() else None
    return None


# --------------------------------------------------------------- matching --

_UNIT_SPLIT = re.compile(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)")


def _split_units(text: str) -> str:
    """'100G' -> '100 G', 'x12' -> 'x 12'. Distributors print the unit glued to
    the number and the catalogue prints it apart; `search.py` scores whole
    words, so the two spellings have to become one before they are compared."""
    return " ".join(_UNIT_SPLIT.sub(" ", text).split())


def _query_of(name: str) -> str:
    """The printed name as the words worth asking the catalogue about.

    Packaging is dropped: a bare 'x', the count that follows it ('x 48'), a
    packaging word, and the count before one ('48 NOS'). 'FROOTI 150ML x 48
    NOS' becomes 'frooti 150 ml', which is the product; the 48 is how many
    came in the case, and the quantity column already says so.
    """
    toks = search._tokens(_split_units(name))
    out: list[str] = []
    prev_x = False
    for w in toks:
        if w == "x":
            prev_x = True
            continue
        if w in _PACKAGING_WORDS:
            if out and out[-1].isdecimal():
                out.pop()
            prev_x = False
            continue
        if prev_x and w.isdecimal():
            prev_x = False
            continue
        prev_x = False
        out.append(w)
    return " ".join(out)


def _pool() -> list[search.Candidate]:
    """Every product this shop sells, findable by BOTH spellings of its name.

    `search.load_products()` is read-only use of the search module: its
    candidates carry the catalogue name as one scored field, and a second
    field with the unit split off ('70 g' for '70g') is appended here so an
    invoice's '70G' and a catalogue's '70 g' can find each other. The model
    never sees this list; it exists on this machine only.
    """
    cands, state = search.load_products()
    if not state.available:
        raise ParchiRefused(
            state.reason or search.R_CATALOGUE_UNREADABLE,
            state.detail or "the catalogue could not be read.")
    for c in cands:
        name = str(c.doc.get("name") or "")
        alt = _split_units(name)
        if search._norm(alt) != search._norm(name):
            c.fields.append(("name", alt, search.W_NAME, True))
    return cands


def match_name(name: str, pool: list[search.Candidate]) -> dict[str, Any]:
    """One printed name against the catalogue: proposed, confirm?, or none.

    Deterministic, and never the model's opinion. `candidates` carries up to
    MAX_CANDIDATES hits so a "confirm?" row can offer the runners-up, and the
    `why` on each is `search.py`'s own sentence about how it matched.
    """
    q = _query_of(name)
    hits = search.rank(pool, q) if q else []
    products = [h for h in hits if h.get("type") == search.KIND_PRODUCT]
    cands = [{
        "sku_id": h["sku_id"], "name": h["name"], "score": int(h["score"]),
        "why": h["why"], "sell_paise": h.get("price_paise"),
    } for h in products[:MAX_CANDIDATES]]
    if not cands:
        return {"status": MATCH_NONE, "sku_id": None, "sku_name": None,
                "score": 0, "why": "no product this shop sells has every word "
                                    "of that name in it", "candidates": [],
                "query": q}
    top = cands[0]
    runner = cands[1]["score"] if len(cands) > 1 else 0
    if top["score"] >= PROPOSE_AT and top["score"] - runner > AMBIGUITY_GAP:
        status = MATCH_PROPOSED
    elif top["score"] >= CONFIRM_AT:
        status = MATCH_CONFIRM
    else:
        status = MATCH_NONE
    return {"status": status, "sku_id": top["sku_id"] if status != MATCH_NONE else None,
            "sku_name": top["name"] if status != MATCH_NONE else None,
            "score": top["score"], "why": top["why"], "candidates": cands,
            "query": q}


# ------------------------------------------------------------------ gate --


def _line(i: int, raw: Any) -> dict[str, Any]:
    """One printed line, its figures read as paise, its arithmetic checked."""
    if not isinstance(raw, dict):
        raw = {}
    name = " ".join(str(raw.get("name") or "").split())
    qty = _whole(raw.get("qty"))
    rate, rate_why = figure_paise(raw.get("rate"))
    amount, amount_why = figure_paise(raw.get("amount"))

    out: dict[str, Any] = {
        "i": i, "name": name or f"line {i + 1}",
        "qty": qty,
        "rate": raw.get("rate"), "rate_paise": rate,
        "rate_rupees": None if rate is None else to_rupees_str(paise(rate)),
        "amount": raw.get("amount"), "amount_paise": amount,
        "amount_rupees": None if amount is None else to_rupees_str(paise(amount)),
        "computed_paise": None, "computed_rupees": None,
    }
    problems = []
    if qty is None or qty <= 0:
        problems.append(f"the quantity {raw.get('qty')!r} is not a whole "
                        f"number of units")
    if rate is None:
        problems.append(f"the rate could not be read: {rate_why}")
    if amount is None:
        problems.append(f"the amount could not be read: {amount_why}")
    if problems:
        out["arithmetic"] = LINE_UNREADABLE
        out["arithmetic_detail"] = "; ".join(problems)
        return out

    assert qty is not None and rate is not None and amount is not None
    computed = int(paise(rate * qty))
    out["computed_paise"] = computed
    out["computed_rupees"] = to_rupees_str(paise(computed))
    if computed == amount:
        out["arithmetic"] = LINE_OK
        out["arithmetic_detail"] = None
    else:
        off = amount - computed
        out["arithmetic"] = LINE_FAILS
        out["arithmetic_detail"] = (
            f"{qty} × ₹{to_rupees_str(paise(rate))} is "
            f"₹{to_rupees_str(paise(computed))}; the bill prints "
            f"₹{to_rupees_str(paise(amount))} — "
            f"{_paise_words(off)} {'over' if off > 0 else 'under'}.")
    return out


def _paise_words(off: int) -> str:
    """'one paisa', '3 paise', '₹1.50' — the size of a disagreement, said so a
    shopkeeper can find it on the paper."""
    n = abs(int(off))
    if n == 1:
        return "one paisa"
    if n < 100:
        return f"{n} paise"
    return f"₹{to_rupees_str(paise(n))}"


def gate(lines: list[dict[str, Any]], subtotal_raw: Any, taxes_raw: Any,
         total_raw: Any) -> dict[str, Any]:
    """The arithmetic gate. Integer paise, and a refusal that names its line.

    Three equalities, all of them exact:
        every line        qty × rate == printed amount
        the lines         Σ amount   == printed subtotal (when one is printed)
        the bill          subtotal + Σ taxes == printed total
    A tax, a freight charge or a round-off is a printed figure like any other:
    it is added as printed, and if what is printed does not add up the bill
    is refused rather than a figure being invented to make it. NOTHING here
    nets, rounds or corrects.
    """
    failing = [ln["i"] for ln in lines if ln["arithmetic"] != LINE_OK]
    reasons: list[str] = []
    for ln in lines:
        if ln["arithmetic"] == LINE_FAILS:
            reasons.append(f"line {ln['i'] + 1} ({ln['name']}): {ln['arithmetic_detail']}")
        elif ln["arithmetic"] == LINE_UNREADABLE:
            reasons.append(f"line {ln['i'] + 1} ({ln['name']}) could not be "
                           f"checked: {ln['arithmetic_detail']}")

    taxes = []
    tax_sum = 0
    for t in (taxes_raw if isinstance(taxes_raw, list) else []):
        if not isinstance(t, dict):
            continue
        label = " ".join(str(t.get("label") or "tax").split())
        amt, why = figure_paise(t.get("amount"))
        taxes.append({"label": label, "amount": t.get("amount"),
                      "amount_paise": amt,
                      "amount_rupees": None if amt is None else to_rupees_str(paise(amt))})
        if amt is None:
            reasons.append(f"the {label} line could not be read: {why}")
        else:
            tax_sum += amt

    sum_lines = 0
    for ln in lines:
        if ln["amount_paise"] is not None:
            sum_lines += int(ln["amount_paise"])

    subtotal, sub_why = (None, None)
    subtotal_printed = isinstance(subtotal_raw, str) and subtotal_raw.strip() != ""
    if subtotal_printed:
        subtotal, sub_why = figure_paise(subtotal_raw)
        if subtotal is None:
            reasons.append(f"the subtotal could not be read: {sub_why}")
        elif subtotal != sum_lines:
            reasons.append(
                f"the lines add to ₹{to_rupees_str(paise(sum_lines))} and the "
                f"bill prints a subtotal of ₹{to_rupees_str(paise(subtotal))} "
                f"— {_paise_words(subtotal - sum_lines)} apart.")
    base = subtotal if subtotal is not None else sum_lines

    total, tot_why = figure_paise(total_raw)
    expected = base + tax_sum
    if total is None:
        reasons.append(f"the printed total could not be read: {tot_why}")
    elif not reasons and total != expected:
        reasons.append(
            f"the lines{' and taxes' if taxes else ''} come to "
            f"₹{to_rupees_str(paise(expected))} and the bill prints a total of "
            f"₹{to_rupees_str(paise(total))} — "
            f"{_paise_words(total - expected)} apart.")

    ok = not reasons
    return {
        "ok": ok,
        "reason": None if ok else R_ARITHMETIC,
        "detail": (None if ok else
                   "Refused: this bill does not add up, and nothing on it "
                   "will be booked until it does. " + " ".join(reasons)),
        "failing_lines": failing,
        "lines_checked": len(lines),
        "sum_of_lines_paise": sum_lines,
        "subtotal_printed": subtotal_printed,
        "subtotal": subtotal_raw if subtotal_printed else "",
        "subtotal_paise": subtotal,
        "taxes": taxes,
        "tax_paise": tax_sum,
        "expected_total_paise": expected,
        "printed_total": total_raw,
        "printed_total_paise": total,
        "rule": ("every line qty × rate must equal its printed amount; the "
                 "lines must add to the printed subtotal; subtotal plus the "
                 "printed taxes must equal the printed total. One paisa off "
                 "refuses. Nothing is rounded, netted or corrected."),
    }


# ------------------------------------------------------------ where things live --


def shop_dir() -> Path:
    """The till's own answer, never a second one — see `purchases.shop_dir`."""
    return purchases.shop_dir()


def parchi_dir() -> Path:
    return shop_dir() / PARCHI_SUBDIR


def audit_path() -> Path:
    """This module's own hash-chained log. NOT `results/audit.jsonl`, for the
    reason `purchases.audit_path` gives: that file has one writer in another
    process, and a second appender corrupts the money chain."""
    return shop_dir() / AUDIT_SIDECAR


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, **fields: Any) -> Optional[str]:
    """One chained line. What is logged is what LEFT and what was DECIDED —
    the photograph's digest and size, the verdict, the line count — never
    the supplier's phone and never the photograph itself."""
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="parchi", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose a parse
        return None


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _valid_id(parchi_id: Any) -> str:
    """Checked against a strict charset BEFORE it is joined to a path."""
    s = (parchi_id or "").strip() if isinstance(parchi_id, str) else ""
    if not PARCHI_ID_RE.match(s):
        raise ParchiRefused(
            R_BAD_ID,
            f"{parchi_id!r} is not a parchi id from this shop. They look like "
            f"'prc_' followed by twelve hex characters.")
    return s


def _read(parchi_id: str) -> dict[str, Any]:
    p = parchi_dir() / f"{_valid_id(parchi_id)}.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ParchiRefused(
            R_NO_PARCHI, f"this shop has no parchi {parchi_id!r}.", status=404) from None
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a crash
        raise ParchiRefused(
            R_NO_PARCHI,
            f"parchi {parchi_id!r} is on disk but could not be read "
            f"({type(exc).__name__}: {exc}).", status=404) from None
    if not isinstance(doc, dict):
        raise ParchiRefused(R_NO_PARCHI, f"parchi {parchi_id!r} is not a parchi document.",
                            status=404)
    return doc


# ---------------------------------------------------------------- images --

_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)


def _image_kind(raw: bytes) -> tuple[str, str]:
    """(mime, extension) from the bytes themselves, never from a filename."""
    for magic, mime, ext in _MAGIC:
        if raw.startswith(magic):
            return mime, ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ParchiRefused(
        R_NOT_AN_IMAGE,
        f"the {len(raw)} bytes sent are not a JPEG, PNG or WebP photograph. "
        f"Nothing left this machine.")


def _check_image(raw: bytes) -> tuple[str, str]:
    if not raw:
        raise ParchiRefused(
            R_NO_PHOTOGRAPH,
            "no photograph arrived. Send the bill as the 'image' file part.")
    if len(raw) > MAX_PHOTOGRAPH_BYTES:
        raise ParchiRefused(
            R_PHOTOGRAPH_TOO_LARGE,
            f"the photograph is {len(raw)} bytes and the cap is "
            f"{MAX_PHOTOGRAPH_BYTES}. A phone photograph of a bill is a few "
            f"megabytes; nothing left this machine.")
    return _image_kind(raw)


# ----------------------------------------------------------------- parse --


def _left_the_machine(raw: bytes, mime: str) -> dict[str, Any]:
    """The disclosure, in the advisor's shape: what left, by name."""
    return {
        "photograph": {"bytes": len(raw), "mime": mime,
                       "sha256": hashlib.sha256(raw).hexdigest()},
        "fields": ["the photograph",
                   "the supplier's name printed on it",
                   "the cost prices printed on it"],
        "not_sent": ["the catalogue", "selling prices", "customers",
                     "takings", "any earlier bill"],
        "to": {"provider": provider(), "model": model_name(),
               "host": _host()},
        "note": ("this photograph, the supplier's name and the cost prices "
                 "printed on it left the machine. The catalogue did not: "
                 "every match below was made here."),
    }


def parse_image(raw: bytes) -> dict[str, Any]:
    """The whole pipeline for one photograph, minus the person.

    Read (the model) -> figures (digit by digit) -> match (locally) -> gate
    (integer paise) -> a document on disk that a later ACCEPT books from. The
    document is written whether or not the gate passed, because a refusal is
    a result and a shopkeeper will want to see which line failed.
    """
    mime, ext = _check_image(raw)
    # The catalogue is read BEFORE the photograph leaves, so a shop with an
    # unreadable catalogue is refused without paying the provider for a bill
    # nothing could be matched against.
    pool = _pool()
    left = _left_the_machine(raw, mime)
    answer = read_invoice(raw, mime)

    raw_lines = answer.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ParchiRefused(
            R_NO_LINES,
            "the model read no item lines on this bill. Nothing was booked; "
            "try a straighter, better-lit photograph, or type the invoice.")
    if len(raw_lines) > MAX_LINES:
        raise ParchiRefused(
            R_TOO_MANY_LINES,
            f"the model read {len(raw_lines)} lines and the cap on one "
            f"invoice is {MAX_LINES}. Photograph it in parts.")

    lines = [_line(i, r) for i, r in enumerate(raw_lines)]
    for ln in lines:
        ln["match"] = match_name(ln["name"], pool)
        ln["status"] = _status_of(ln)
    verdict = gate(lines, answer.get("subtotal"), answer.get("taxes"),
                   answer.get("printed_total"))

    sup_raw = answer.get("supplier") if isinstance(answer.get("supplier"), dict) else {}
    sup_name = " ".join(str(sup_raw.get("name") or "").split())[:purchases.MAX_NAME]
    sup_phone = " ".join(str(sup_raw.get("phone") or "").split())[:purchases.MAX_PHONE]
    on_file = purchases.find_supplier(sup_name) if sup_name else None

    invoice_no = " ".join(str(answer.get("invoice_no") or "").split())[:purchases.MAX_INVOICE]
    if invoice_no and not purchases.INVOICE_RE.match(invoice_no):
        invoice_no = ""

    now = _now_iso()
    parchi_id = "prc_" + secrets.token_hex(6)
    doc: dict[str, Any] = {
        "format": PARCHI_FORMAT,
        "parchi_id": parchi_id,
        "at": now,
        "image": {**left["photograph"], "file": f"{parchi_id}.{ext}"},
        "model": model_name(),
        "provider": provider(),
        "supplier": {"name": sup_name, "phone": sup_phone,
                     "on_file": on_file},
        "invoice_no": invoice_no or None,
        "date": _date_of(answer.get("date")),
        "date_printed": str(answer.get("date") or ""),
        "lines": lines,
        "gate": verdict,
        "counts": _counts(lines),
        "left_the_machine": left,
        "booked": None,
        "uses_razorpay": False,
        "add_product_route": "#/products",
    }
    d = parchi_dir()
    d.mkdir(parents=True, exist_ok=True)
    # The photograph is kept beside the parse: it is the shopkeeper's own
    # record of the bill, and a cost traced back to a parchi id must be able
    # to show the paper it was read from.
    (d / doc["image"]["file"]).write_bytes(raw)
    _write_json(d / f"{parchi_id}.json", doc)
    head = _audit("parchi.parsed", parchi_id=parchi_id,
                  image_sha256=left["photograph"]["sha256"],
                  image_bytes=len(raw), model=model_name(),
                  provider=provider(), lines=len(lines),
                  gate_ok=verdict["ok"], failing_lines=verdict["failing_lines"],
                  left_the_machine=left["fields"], minted=False)
    doc["audited"] = head is not None
    return doc


def _status_of(ln: dict[str, Any]) -> str:
    """The one word a row wears. Arithmetic outranks matching: a line whose
    figures do not add up is not bookable whatever it matched."""
    if ln["arithmetic"] == LINE_FAILS:
        return "arithmetic_fails"
    if ln["arithmetic"] == LINE_UNREADABLE:
        return "unreadable"
    m = ln["match"]["status"]
    if m == MATCH_PROPOSED:
        return "proposed"
    if m == MATCH_CONFIRM:
        return "confirm"
    return "no_match"


def _counts(lines: list[dict[str, Any]]) -> dict[str, int]:
    out = {"lines": len(lines), "proposed": 0, "confirm": 0, "no_match": 0,
           "arithmetic_fails": 0, "unreadable": 0}
    for ln in lines:
        out[ln["status"]] = out.get(ln["status"], 0) + 1
    return out


# ------------------------------------------------------------------ book --


def _json_or_refuse(resp: JSONResponse) -> dict[str, Any]:
    return json.loads(bytes(resp.body).decode("utf-8"))


def _day_margin(day: Optional[str]) -> Optional[dict[str, Any]]:
    """Today's margin as the Purchases screen shows it, or None if that
    screen would refuse. Read through `purchases.py`'s own endpoint so the
    before/after figures this module quotes are that screen's figures."""
    try:
        body = _json_or_refuse(purchases.margin_today_ep(day))
    except Exception:  # noqa: BLE001 - the chain may be unreadable
        return None
    if not body.get("ok"):
        return None
    cov = body.get("covered") or {}
    unc = body.get("uncovered") or {}
    return {
        "date": body.get("date"),
        "revenue_paise": body.get("revenue_paise"),
        "margin_is_partial": bool(body.get("margin_is_partial")),
        "covered_skus": cov.get("skus"),
        "covered_revenue_paise": cov.get("revenue_paise"),
        "margin_paise": cov.get("margin_paise"),
        "margin_pct_of_price": cov.get("margin_pct_of_price"),
        "uncovered_skus": list(unc.get("skus") or []),
        "uncovered_revenue_paise": unc.get("revenue_paise"),
    }


def book(parchi_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """A person accepted these lines. File them through purchases.py.

    Body: {supplier_id?, new_supplier?: {name, phone}, lines: [{i, sku_id}],
    date?, invoice_no?}. Everything money is taken from the STORED parse,
    never from this body: a client cannot send a cost. The gate is run again
    on the stored figures, so a refusal cannot be got round by a client that
    skipped the screen.
    """
    doc = _read(parchi_id)
    if doc.get("booked"):
        raise ParchiRefused(
            R_ALREADY_BOOKED,
            f"parchi {parchi_id} was booked as "
            f"{(doc['booked'] or {}).get('purchase_id')} on "
            f"{(doc['booked'] or {}).get('at')}. Booking it twice would "
            f"double this shop's costs and halve its margin.")

    lines = doc.get("lines") or []
    # THE GATE IS RUN AGAIN, on the stored lines and the stored printed
    # figures, so a client that skipped the screen cannot book a bill the
    # screen refused. The stored `gate` block carries the printed subtotal,
    # taxes and total exactly as the parse read them, which is what the gate
    # needs; a document whose gate block is missing is refused, not trusted.
    stored = doc.get("gate") if isinstance(doc.get("gate"), dict) else {}
    verdict = gate(lines, stored.get("subtotal"),
                   [{"label": t.get("label"), "amount": t.get("amount")}
                    for t in (stored.get("taxes") or [])],
                   stored.get("printed_total"))
    if not verdict.get("ok"):
        raise ParchiRefused(
            R_ARITHMETIC, str(verdict.get("detail") or "this bill does not add up."),
            failing_lines=list(verdict.get("failing_lines") or []))

    raw_accept = body.get("lines")
    if not isinstance(raw_accept, list) or not raw_accept:
        raise ParchiRefused(
            R_NO_ACCEPTED_LINES,
            "no lines were accepted, so there is nothing to book. Tick the "
            "lines that matched a product and press ACCEPT.")
    by_i = {int(ln["i"]): ln for ln in lines}
    out_lines = []
    booked_from = []
    seen: set[int] = set()
    for raw in raw_accept:
        if not isinstance(raw, dict):
            raise ParchiRefused(R_BAD_BODY, "every accepted line is {i, sku_id}.")
        i = raw.get("i")
        if isinstance(i, bool) or not isinstance(i, int) or i not in by_i:
            raise ParchiRefused(
                R_LINE_NOT_BOOKABLE,
                f"line {i!r} is not on this parchi, which has "
                f"{len(lines)} line{'s' if len(lines) != 1 else ''}.")
        if i in seen:
            raise ParchiRefused(
                R_LINE_NOT_BOOKABLE, f"line {i + 1} was accepted twice.")
        seen.add(i)
        ln = by_i[i]
        if ln.get("arithmetic") != LINE_OK:
            raise ParchiRefused(
                R_LINE_NOT_BOOKABLE,
                f"line {i + 1} ({ln.get('name')}) did not pass the arithmetic "
                f"gate and cannot be booked: {ln.get('arithmetic_detail')}")
        sku = raw.get("sku_id")
        if not isinstance(sku, str) or not sku.strip():
            raise ParchiRefused(
                R_LINE_NOT_BOOKABLE,
                f"line {i + 1} ({ln.get('name')}) was accepted with no "
                f"product chosen for it. Choose one, or leave the line out.")
        sku = sku.strip()
        m = ln.get("match") or {}
        offered = {c.get("sku_id") for c in (m.get("candidates") or [])}
        out_lines.append({"sku_id": sku, "units": int(ln["qty"]),
                          "cost_paise": int(paise(ln["rate_paise"])),
                          "line_paise": int(paise(ln["amount_paise"]))})
        booked_from.append({"i": i, "name": ln.get("name"), "sku_id": sku,
                            "chosen_by": ("machine" if sku == m.get("sku_id")
                                          and m.get("status") == MATCH_PROPOSED
                                          else "person"),
                            "was_offered": sku in offered})

    # The supplier: on file already, or added now through purchases.py's own
    # writer, with the phone a person confirmed. Never guessed.
    sid = body.get("supplier_id")
    new_sup = body.get("new_supplier")
    supplier_added = None
    if isinstance(sid, str) and sid.strip():
        sid = sid.strip()
    elif isinstance(new_sup, dict):
        try:
            added = purchases.add_supplier({
                "name": str(new_sup.get("name") or ""),
                "phone": str(new_sup.get("phone") or ""),
                "notes": f"added from a photographed bill ({parchi_id})",
            })
        except purchases.PurchaseRefused as exc:
            raise ParchiRefused(exc.reason, exc.detail, status=exc.status) from None
        supplier_added = added["supplier"]
        sid = str(supplier_added["supplier_id"])
    else:
        raise ParchiRefused(
            R_SUPPLIER_UNRESOLVED,
            "say which supplier this bill is from: a supplier_id already on "
            "the list, or new_supplier {name, phone} to add one. The name "
            "read off the bill is offered, never assumed.")

    before = purchases.cost_coverage()
    day_label = body.get("date") if isinstance(body.get("date"), str) else doc.get("date")
    today_before = _day_margin(None)

    pbody: dict[str, Any] = {"supplier_id": sid, "lines": out_lines}
    if day_label:
        pbody["date"] = day_label
    inv = body.get("invoice_no") if isinstance(body.get("invoice_no"), str) else doc.get("invoice_no")
    if inv:
        pbody["invoice_no"] = inv
    try:
        rec = purchases.record_purchase(
            pbody, source={"parchi_id": parchi_id,
                           "image_sha256": doc["image"]["sha256"]})
    except purchases.PurchaseRefused as exc:
        raise ParchiRefused(exc.reason, exc.detail, status=exc.status) from None

    after = purchases.cost_coverage()
    today_after = _day_margin(None)
    purchase = rec["purchase"]
    doc["booked"] = {"purchase_id": purchase["purchase_id"], "at": _now_iso(),
                     "lines": booked_from, "supplier_id": sid,
                     "left_out": sorted(set(by_i) - seen)}
    _write_json(parchi_dir() / f"{parchi_id}.json", doc)
    head = _audit("parchi.booked", parchi_id=parchi_id,
                  purchase_id=purchase["purchase_id"], supplier_id=sid,
                  lines_booked=len(out_lines), lines_left_out=len(by_i) - len(seen),
                  total_paise=int(purchase["total_paise"]), minted=False)
    return {
        "parchi_id": parchi_id,
        "purchase": purchase,
        "supplier_added": supplier_added,
        "booked": doc["booked"],
        "audited": head is not None and rec["audited"],
        "cost_known": {"before": before["with_a_cost"],
                       "after": after["with_a_cost"],
                       "of": after["count"]},
        "today": {"before": today_before, "after": today_after},
        "note": ("Booked through the same writer as a typed invoice. Nothing "
                 "was paid, and nothing here can pay a supplier."),
    }


# ---------------------------------------------------------------- routes --


@router.get("/parchi/status")
def status_ep() -> JSONResponse:
    """Can this counter read a bill, and what leaves when it does."""
    ok, why = available()
    return JSONResponse({
        "ok": True, "settles_money": False, "uses_razorpay": False,
        "available": ok, "reason": None if ok else R_NO_KEY, "detail": why,
        "provider": provider() if ok else None,
        "model": model_name() if ok else None,
        "what_leaves": ["the photograph", "the supplier's name printed on it",
                        "the cost prices printed on it"],
        "what_stays": ["the catalogue", "selling prices", "customers",
                       "takings"],
        "gate": ("every line qty × rate must equal its printed amount, and "
                 "the lines plus printed taxes must equal the printed total, "
                 "to the paisa"),
        "typed_form": "the RECORD A PURCHASE form works with or without a key",
    })


@router.post("/parchi/parse")
async def parse_ep(request: Request) -> JSONResponse:
    """multipart: image -> the parsed bill, matched and gated, on disk."""
    try:
        till = purchases._till()
        try:
            form = await till.read_form(request)
            raw = till.form_image(form)
        except ParchiRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - the till names its refusals
            reason = getattr(exc, "reason", None)
            detail = getattr(exc, "detail", None) or str(exc)
            raise ParchiRefused(
                R_NO_PHOTOGRAPH if reason else R_BAD_BODY,
                f"no photograph could be read from this request "
                f"({reason or type(exc).__name__}: {detail}). Send the bill "
                f"as the 'image' file part of a multipart form.") from None
        doc = parse_image(raw)
        return JSONResponse({"ok": True, "settles_money": False, **doc})
    except ParchiRefused as exc:
        return _refusal(exc)
    except purchases.PurchaseRefused as exc:
        return _refusal(ParchiRefused(exc.reason, exc.detail, status=exc.status))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/parchi/{parchi_id}")
def one_ep(parchi_id: str) -> JSONResponse:
    try:
        doc = _read(parchi_id)
        return JSONResponse({"ok": True, "settles_money": False, **doc})
    except ParchiRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/parchi/{parchi_id}/book")
async def book_ep(parchi_id: str, request: Request) -> JSONResponse:
    try:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise ParchiRefused(R_BAD_BODY, "this request's body is not JSON.") from None
        if not isinstance(body, dict):
            raise ParchiRefused(
                R_BAD_BODY,
                f"this request's body is a {type(body).__name__}; it must be "
                f"a JSON object.")
        out = book(parchi_id, body)
        return JSONResponse({"ok": True, "settles_money": False, **out})
    except ParchiRefused as exc:
        return _refusal(exc)
    except purchases.PurchaseRefused as exc:
        return _refusal(ParchiRefused(exc.reason, exc.detail, status=exc.status))
    except MoneyError as exc:
        return _refusal(ParchiRefused(
            R_LINE_NOT_BOOKABLE,
            f"a figure on this parchi is not integer paise ({exc}). Nothing "
            f"was booked."))
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
