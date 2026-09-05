"""SALAAHKAAR — the advisor a shopkeeper can talk to. Shaped like a call.

The operator's wish was "a person who does a video call with the kirana owner
to ask doubts". This is the honest version of that: a voice on the other end
that remembers the last few things said on THIS call, answers from the shop's
own files, and — when a model is available — reasons about the figures out
loud. It is not a person and the page never pretends it is.

WHAT IT BUILDS ON
=================
Every figure spoken here is produced by `gawaah/assistant.py`'s tool executors
— takings off the hash chain, open orders off the storefront, stock off the
shopkeeper's own count, a price off the catalogue — called through
`assistant.execute`. One tool is added here because MUNSHI has none for it:
`todays_margin`, which calls `gawaah/purchases.py`'s own day-margin derivation
rather than re-deriving it. Nothing about money is computed in this file. There
is no arithmetic on a rupee anywhere below; every number is read from a module
that owns it.

WHAT IS DIFFERENT FROM MUNSHI, STATED PLAINLY
=============================================
MUNSHI sends the model one sentence and never a figure. SALAAHKAAR, when a key
is present, sends the model TWO things more, and says so in every response:

  1. the last few turns of this call — the shopkeeper's sentences and what
     the advisor said back — so "uska daam?" can be resolved; and
  2. the RESULT OF THE ONE TOOL that answered the current question, scrubbed
     (`facts_for_model`): rupee strings, counts, product names. Never the
     catalogue, never a paise integer, never a customer's name, phone or
     address, never a sku id, never the audit chain.

That is the price of reasoning: a model cannot advise on figures it has not
seen. What it is given is listed under `left_the_machine` in the response and
appended, as FIELD NAMES ONLY, to this module's own hash-chained log — so a
shopkeeper can read back every time a figure left the machine and exactly
which fields went, without the values themselves being written to disk twice.

THE MODEL MAY NOT INVENT A NUMBER
=================================
Every digit in the model's advice is checked against the figures it was given
and the shopkeeper's own sentence. A number that came from neither — a sum it
did, a percentage it worked out, a rupee amount it misread — drops the advice
on the floor: the counter's own sentence is spoken instead and the response
says by name why (`model_quoted_a_figure_it_was_not_given`). That is invariant
9 applied to prose: every published number comes from running code.

NO KEY IS A FIRST-CLASS STATE
=============================
With no XAI_API_KEY the advisor still answers every tool question from the
shop's own files — and says, plainly, that it cannot reason about them without
a model. It does not produce advice from nowhere. The key is read from the
environment at call time, never logged, never returned.

THE CONTEXT IS SHORT, IN MEMORY, AND EXPIRES
============================================
A call keeps its last MAX_TURNS exchanges in this process's memory for
SESSION_TTL_S seconds after the last thing said. Nothing said on a call is
written to disk, and a restart forgets every call. There is no endpoint that
lists calls.

A REFUSAL IS A RESULT. Every failure has a name and a 400. Nothing here mints,
bills, moves stock or holds a gateway credential; `settles_money` is false on
every response as a fact about the code — this module has no add-to-bill tool
at all, and a sentence that asks for one is refused by name.

MOUNTING
========
The router carries NO prefix; these paths are already absolute::

    GET  /advisor/health                    which brain, what it keeps, what leaves
    POST /advisor/say                       one sentence in, an answer to speak out
    GET  /advisor/session/{session_id}      read this call's kept turns back
    POST /advisor/session/{session_id}/end  hang up: forget the call

    from gawaah import advisor
    app.include_router(advisor.router)
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import assistant
from . import tts as _tts
from .assistant import AssistantRefused, GrokUnavailable
from .ledger import Ledger
from .money import MoneyError

router = APIRouter()


# --------------------------------------------------------------- refusals --
#
# The body-shape refusals are MUNSHI's own, by name, because the same page rule
# applies: the browser sends a sentence and never a figure. The rest are states
# only this module can reach.

R_BAD_BODY = assistant.R_BAD_BODY
R_NO_TEXT = assistant.R_NO_TEXT
R_TEXT_TOO_LONG = assistant.R_TEXT_TOO_LONG
R_BAD_SOURCE = assistant.R_BAD_SOURCE
R_CLIENT_AUTHORED = assistant.R_CLIENT_AUTHORED
R_NOT_UNDERSTOOD = assistant.R_NOT_UNDERSTOOD
R_UNKNOWN_TOOL = assistant.R_UNKNOWN_TOOL
R_MODEL_PRICED = assistant.R_MODEL_PRICED
R_BAD_TOOL_ARGS = assistant.R_BAD_TOOL_ARGS

R_BAD_SESSION_ID = "session_id_malformed"
R_NO_SESSION = "no_such_call"
R_NOT_A_COUNTER = "this_is_a_call_not_the_till"
R_MODEL_INVENTED_A_FIGURE = "model_quoted_a_figure_it_was_not_given"
R_MARGIN_UNAVAILABLE = "margin_unavailable"
R_INTERNAL = "advisor_internal_error"

#: Reasons the model path can fail without it being the shopkeeper's problem.
#: These fall back and are named in the response; they never refuse the turn.
R_GROK_UNREACHABLE = assistant.R_GROK_UNREACHABLE
R_GROK_HTTP = assistant.R_GROK_HTTP
R_GROK_SHAPE = assistant.R_GROK_SHAPE
R_NO_TOOL_CALL = assistant.R_NO_TOOL_CALL
R_ADVICE_EMPTY = "model_returned_no_advice"

BRAIN_LOCAL = assistant.BRAIN_LOCAL
BRAIN_GROK = assistant.BRAIN_GROK
#: The provider's real name, for anything a person reads. See the note in
#: `assistant.py`: a counter that prints `grok` while calling Google is wrong
#: about the one fact a shopkeeper might check.
brain_name = assistant.brain_name


# ------------------------------------------------------------------ limits --

MAX_TEXT = assistant.MAX_TEXT
#: Exchanges kept per call. Eight is a conversation, not a transcript.
MAX_TURNS = 8
#: A call with nothing said on it for this long is over.
SESSION_TTL_S = 900
#: Calls held at once. Past this the quietest one is forgotten first.
MAX_SESSIONS = 64
#: Rows of any list handed to the model. It is phrasing an answer, not auditing.
MAX_FACT_ROWS = 8
#: Characters of advice accepted from the model. Longer is a lecture, not a call.
MAX_ADVICE = 900

SESSION_ID_RE = re.compile(r"^call_[0-9a-f]{12}$")
SOURCES = assistant.SOURCES


class AdvisorRefused(AssistantRefused):
    """A named refusal. A subclass so one `except` catches both modules'."""


def _refusal(exc: AssistantRefused, status: int = 400,
             **extra: Any) -> JSONResponse:
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


# ------------------------------------------------------------------ tools --
#
# MUNSHI's five QUESTIONS, and one of this module's own. Its one INSTRUCTION,
# add_to_bill, is deliberately not here: this is a call, and a bill line is
# written on the till by a person.

TOOL_ORDERS = assistant.TOOL_ORDERS
TOOL_TAKINGS = assistant.TOOL_TAKINGS
TOOL_FIND = assistant.TOOL_FIND
TOOL_LOW_STOCK = assistant.TOOL_LOW_STOCK
TOOL_PRICE = assistant.TOOL_PRICE
TOOL_MARGIN = "todays_margin"

_MARGIN_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_MARGIN,
        "description": ("What this counter earned today on what it billed: "
                        "revenue off the audit chain, cost from the shop's own "
                        "recorded purchases, and the margin only where a cost "
                        "is known. Products with no recorded cost are reported "
                        "as unknown, never as zero."),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

#: The two tools that put something on a BILL — a line, or the whole bill onto
#: a customer's book — are the till's, not a call's. Both are refused here by
#: name (`this_is_a_call_not_the_till`), which is what sends the sentence back
#: to the till's own Salaahkaar where a proposal waits for a person.
_TILL_ONLY = frozenset({assistant.TOOL_ADD, assistant.TOOL_KHATA_BOOK})

TOOLS: tuple[dict[str, Any], ...] = tuple(
    [dict(t) for t in assistant.TOOLS
     if t["function"]["name"] not in _TILL_ONLY] + [_MARGIN_TOOL])

TOOL_NAMES = tuple(t["function"]["name"] for t in TOOLS)

#: Words that make a sentence a question about the margin. Checked BEFORE the
#: sentence reaches MUNSHI's parser, whose takings words ("hua", "aaj") would
#: otherwise swallow "aaj ka munafa kitna hua".
MARGIN_WORDS = frozenset({
    "margin", "margins", "munafa", "munaafa", "munafe", "profit", "profits",
    "fayda", "faayda", "fayeda", "faida", "nafa", "labh", "laabh", "bachat",
    "earned", "earning", "earnings", "kamaya_kya",
})

#: "that one", said the way it is said. When a call has already named a
#: product, these stand for it. The English "is"/"us" are deliberately absent:
#: "what is the price" is a sentence, and "us" is the shopkeeper.
PRONOUNS = frozenset({
    "iska", "uska", "iski", "uski", "iske", "uske", "isko", "usko", "isi",
    "usi", "yeh", "ye", "woh", "wo", "wahi", "vahi", "same", "it", "its",
    "that", "this",
})

ROUTER_PROMPT = (
    "You are the routing layer of a small Indian kirana shop's advisor, on a "
    "call with the shopkeeper. The earlier turns of this call are shown so "
    "you can resolve 'that one', 'uska', 'the same product'. For the LATEST "
    "sentence choose exactly ONE tool and fill its arguments from the "
    "shopkeeper's own words, usually Hinglish — Hindi in Latin script — or "
    "English.\n"
    "You have not been given this shop's catalogue, prices, costs, orders, "
    "stock or takings, and you must not invent any of them. Pass product "
    "words through exactly as said; the counter resolves them. Never put a "
    "price, a total, a rupee amount or a sku id in any argument.\n"
    "If the latest sentence asks for general advice that no tool answers — "
    "how to run the shop, what to do about a situation — reply in two or "
    "three short sentences without calling a tool, in the language the "
    "shopkeeper used, and put NO number of any kind in the reply, because "
    "you have none."
)

#: What a language toggle on the page adds to the phrasing prompt. The
#: figures are unchanged — they are the tool's, checked afterwards in whatever
#: script they come back in — only the words around them move. `hi-IN` asks
#: for Devanagari because that is what the natural voice reads best and what
#: the shopkeeper's own recogniser returns; Hinglish in Latin script is what
#: the model would otherwise default to, and that reads aloud as English.
LANG_LINES: dict[str, str] = {
    "hi-IN": ("in Hindi, written in Devanagari script, keeping every rupee "
              "amount exactly as given with Latin digits"),
    "en-IN": ("in plain Indian English in Latin script — English even when "
              "the question was asked in Hindi or Hinglish"),
    "bn-IN": ("in Bengali, written in Bengali script, keeping every rupee "
              "amount exactly as given with Latin digits"),
}

#: With no choice made, the old rule: follow the shopkeeper.
_FOLLOW = "in the language the shopkeeper used — Hinglish or English"


def lang_line(lang: Optional[str]) -> str:
    return LANG_LINES.get(lang or "", _FOLLOW)


def advice_system(lang: Optional[str]) -> str:
    """The phrasing prompt with the language clause IN ITS SLOT.

    It was appended as a second sentence at first, and the model followed the
    first one: asked "aaj kitna hua" with English selected, it answered in
    Hinglish, because the base prompt still said "the language the shopkeeper
    used". A toggle that loses to the question is not a toggle. One clause,
    one place.
    """
    return ADVICE_PROMPT.replace("{LANGUAGE}", lang_line(lang))


ADVICE_PROMPT = (
    "You are Salaahkaar, the advisor of a small Indian kirana shop, speaking "
    "to the shopkeeper on a call. The tool result you have just been given "
    "was computed on the shop's own machine from its own records, and it is "
    "the only source of figures you have.\n"
    "Answer in two to four short spoken sentences, {LANGUAGE}. Quote rupee "
    "amounts exactly as they appear in the fields ending in _rupees, written "
    "as 'Rs 14.00'. Never say a field name aloud — say what it means. Do "
    "not compute any figure that is not in the result: no sums, no "
    "differences, no percentages, no averages, no estimates, no rounding. "
    "If a field says something is unknown, uncounted or partial, say so "
    "rather than filling it in. If the result does not answer the question, "
    "say what is missing. End with one concrete next step if the figures "
    "support one. Plain words, no marketing voice, no exclamation marks."
)


# --------------------------------------------------------------- sessions --
#
# IN MEMORY, AND ONLY HERE. A call is a dict entry in this process. Nothing in
# it is written to disk, nothing survives a restart, and there is no route that
# lists calls. The clock is injectable so a test can let a call expire without
# waiting fifteen minutes for it.


@dataclass
class Turn:
    at: str
    you: str
    tool: Optional[str]
    spoken: str
    product: Optional[str]
    reasoned: bool


@dataclass
class Session:
    session_id: str
    started_at: str
    last_mono: int
    turns: deque = field(default_factory=lambda: deque(maxlen=MAX_TURNS))
    turn_count: int = 0

    @property
    def last_product(self) -> Optional[str]:
        """The product most recently resolved on this call, if any."""
        for t in reversed(self.turns):
            if t.product:
                return t.product
        return None


_SESSIONS: dict[str, Session] = {}
_LOCK = threading.Lock()

_DEPS: dict[str, Any] = {"transport": None, "monotonic": None}


def set_monotonic(fn: Optional[Callable[[], int]]) -> None:
    """Replace the seconds counter used for expiry. `None` restores real time."""
    _DEPS["monotonic"] = fn


def _mono_s() -> int:
    fn = _DEPS["monotonic"]
    return int(fn()) if fn else int(time.monotonic())


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _valid_session_id(raw: Any) -> str:
    s = str(raw or "").strip()
    if not SESSION_ID_RE.match(s):
        raise AdvisorRefused(
            R_BAD_SESSION_ID,
            f"{raw!r} is not a call id from this counter. They look like "
            f"'call_' followed by twelve hex characters.")
    return s


def _sweep(now: int) -> None:
    """Forget calls nobody has spoken on for SESSION_TTL_S, then trim to the
    cap. Called under the lock, on every request — there is no timer thread."""
    dead = [k for k, s in _SESSIONS.items() if now - s.last_mono > SESSION_TTL_S]
    for k in dead:
        del _SESSIONS[k]
    if len(_SESSIONS) > MAX_SESSIONS:
        by_quiet = sorted(_SESSIONS.values(), key=lambda s: s.last_mono)
        for s in by_quiet[:len(_SESSIONS) - MAX_SESSIONS]:
            _SESSIONS.pop(s.session_id, None)


def _open_session(raw: Any) -> tuple[Session, dict[str, Any]]:
    """The call this sentence belongs to, and how it was found.

    An id the page sent that is well-formed but gone — expired, or from before
    a restart — starts a NEW call and says so, rather than refusing the
    question. The shopkeeper asked something; the honest answer is "I do not
    remember the earlier part of this call", not "no".
    """
    now = _mono_s()
    with _LOCK:
        _sweep(now)
        if raw is not None and str(raw).strip():
            sid = _valid_session_id(raw)
            found = _SESSIONS.get(sid)
            if found is not None:
                found.last_mono = now
                return found, {"resumed": True, "previous": None}
            previous = "expired_or_unknown"
        else:
            previous = None
        sid = "call_" + secrets.token_hex(6)
        s = Session(session_id=sid, started_at=_now_iso(), last_mono=now)
        _SESSIONS[sid] = s
        return s, {"resumed": False, "previous": previous}


def _get_session(raw: Any) -> Session:
    sid = _valid_session_id(raw)
    with _LOCK:
        _sweep(_mono_s())
        s = _SESSIONS.get(sid)
    if s is None:
        raise AdvisorRefused(
            R_NO_SESSION,
            f"this counter has no call {sid!r} in memory. Calls are kept for "
            f"{SESSION_TTL_S // 60} minutes after the last thing said and "
            f"are never written to disk.")
    return s


def _end_session(raw: Any) -> Session:
    s = _get_session(raw)
    with _LOCK:
        _SESSIONS.pop(s.session_id, None)
    return s


def sessions_live() -> int:
    with _LOCK:
        _sweep(_mono_s())
        return len(_SESSIONS)


def _session_view(s: Session) -> dict[str, Any]:
    return {
        "session_id": s.session_id,
        "started_at": s.started_at,
        "turns": [{"at": t.at, "you": t.you, "tool": t.tool,
                   "spoken": t.spoken, "reasoned": t.reasoned}
                  for t in s.turns],
        "turn_count": s.turn_count,
        "kept": min(len(s.turns), MAX_TURNS),
        "keeps_at_most": MAX_TURNS,
        "expires_after_s": SESSION_TTL_S,
        "on_disk": False,
    }


# ---------------------------------------------------------------- the log --


def audit_path() -> Path:
    """This module's own chain, beside the catalogue — never results/audit.jsonl,
    for the reason assistant.py documents: that file has one writer."""
    return Path(assistant.shop_dir()) / "advisor.audit.jsonl"


def _audit(event: str, **fields: Any) -> Optional[str]:
    """One line per time shop figures LEFT THE MACHINE. Field names, never
    values, and never the shopkeeper's sentence."""
    try:
        return Ledger(audit_path()).append(
            ts=_now_iso(), module="advisor", event=event, **fields)
    except Exception:  # noqa: BLE001 - a failed audit must not lose an answer
        return None


# ------------------------------------------------------ the margin tool --


def _do_margin(args: dict[str, Any]) -> dict[str, Any]:
    """Today's margin, from gawaah/purchases.py's own derivation.

    The endpoint function is called directly and its body read back. That is
    deliberate: the split between covered and uncovered revenue, and the rule
    that an unknown cost is never a zero cost, live in that module and are
    tested there. A second derivation here would be a second truth.
    """
    try:
        from . import purchases  # noqa: WPS433 - late; it may be absent
    except Exception as exc:  # noqa: BLE001
        raise AdvisorRefused(
            R_MARGIN_UNAVAILABLE,
            f"gawaah/purchases.py is not importable ({type(exc).__name__}: "
            f"{exc}), so no margin can be derived. Nothing was estimated.") \
            from None
    try:
        resp = purchases.margin_today_ep(day=None)
        doc = json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AdvisorRefused(
            R_MARGIN_UNAVAILABLE,
            f"today's margin could not be derived ({type(exc).__name__}: "
            f"{exc}). Nothing was estimated.") from None
    if not isinstance(doc, dict) or doc.get("ok") is not True:
        reason = str((doc or {}).get("reason") or R_MARGIN_UNAVAILABLE)
        detail = str((doc or {}).get("detail") or
                     "the purchases module refused to derive a margin.")
        raise AdvisorRefused(reason, detail)

    label = str(doc.get("date") or "today")
    bills = int(doc.get("bills") or 0)
    cov = dict(doc.get("covered") or {})
    unc = dict(doc.get("uncovered") or {})
    partial = bool(doc.get("margin_is_partial"))
    if bills == 0:
        said = (f"Nothing has been billed at this counter today ({label}), so "
                f"there is no margin to report. That is the chain's answer, "
                f"not an estimate.")
    else:
        pct = cov.get("margin_pct_of_price")
        pct_said = f" — {pct}% of the price" if pct is not None else ""
        if int(cov.get("units") or 0) > 0:
            said = (f"{bills} bills today ({label}) took Rs "
                    f"{doc.get('revenue_rupees')}. On the {cov.get('units')} "
                    f"units with a recorded cost the margin is Rs "
                    f"{cov.get('margin_rupees')}{pct_said}.")
        else:
            said = (f"{bills} bills today ({label}) took Rs "
                    f"{doc.get('revenue_rupees')}, but no product sold today "
                    f"has a recorded cost, so the margin is not known.")
        if partial:
            said = (f"{said} {unc.get('units')} units worth Rs "
                    f"{unc.get('revenue_rupees')} have no recorded cost, so "
                    f"their margin is not known — it is not zero. Record a "
                    f"purchase for those products and this figure completes.")
    chain = doc.get("chain") if isinstance(doc.get("chain"), dict) else {}
    if chain and not chain.get("ok", True):
        said = (f"{said} The audit chain is broken at line "
                f"{chain.get('lines_checked')}, so anything after that break "
                f"is missing from this figure.")
    items = [r for r in (doc.get("items") or []) if isinstance(r, dict)]
    return {
        "answer": said,
        "data": {
            "date": label, "bills": bills,
            "revenue_paise": doc.get("revenue_paise"),
            "revenue_rupees": doc.get("revenue_rupees"),
            "covered": cov, "uncovered": unc,
            "margin_is_partial": partial,
            "lines_without_a_price": doc.get("lines_without_a_price"),
            "items": items[:MAX_FACT_ROWS],
            "listed": min(len(items), MAX_FACT_ROWS),
            "chain": chain,
            "derived_from": doc.get("derived_from"),
        },
        "proposal": None,
    }


def execute(tool: str, args: dict[str, Any], *,
            brain: str = BRAIN_LOCAL) -> dict[str, Any]:
    """Run one tool HERE. MUNSHI's, through MUNSHI; the margin, through
    KHAREED. This module computes no figure of its own."""
    if tool == TOOL_MARGIN:
        return _do_margin(args)
    if tool in _TILL_ONLY:
        raise AdvisorRefused(
            R_NOT_A_COUNTER,
            "that would put something on a bill, and this is a call, not the "
            "till. Say it on the Ask screen, where a proposal waits for you to "
            "accept it, or add the line on the till. Nothing was done.")
    if tool not in TOOL_NAMES:
        raise AdvisorRefused(
            R_UNKNOWN_TOOL,
            f"{tool!r} is not a tool this advisor has. It has: "
            f"{', '.join(TOOL_NAMES)}.")
    return assistant.execute(tool, args, brain=brain)


def _product_of(tool: str, result: dict[str, Any]) -> Optional[str]:
    """The product a turn was about, for "uska" on the next one."""
    if tool in (TOOL_PRICE, TOOL_FIND):
        data = result.get("data") or {}
        name = data.get("name") or data.get("sku_id")
        return str(name) if name else None
    return None


# ------------------------------------------------------ the local brain --


def carry_context(text: str, session: Session) -> tuple[str, Optional[str]]:
    """"uska daam" after "Maggi milega kya" is a question about Maggi.

    A pronoun is replaced with the product this call last resolved, and the
    replacement is returned so the response can SHOW the substitution rather
    than silently answering a different question. With no earlier product the
    sentence goes through untouched and MUNSHI's parser refuses it by name.
    """
    product = session.last_product
    if not product:
        return text, None
    words = text.split()
    swapped = False
    out: list[str] = []
    for w in words:
        core = "".join(assistant.normalise(w))
        if core in PRONOUNS:
            out.append(product)
            swapped = True
        else:
            out.append(w)
    if not swapped:
        return text, None
    return " ".join(out), product


def local_route(text: str, session: Session
                ) -> tuple[str, dict[str, Any], Optional[str]]:
    """The deterministic parser, with this call's context and the margin.

    Returns (tool, arguments, carried_product). Raises AssistantRefused when
    it does not understand, and AdvisorRefused by name when the sentence was
    an instruction to bill — this is a call, and a call bills nothing.
    """
    text2, carried = carry_context(text, session)
    words = set(assistant.normalise(text2))
    if words & MARGIN_WORDS:
        return TOOL_MARGIN, {}, carried
    tool, args = assistant.local_route(text2)
    if tool in _TILL_ONLY:
        raise AdvisorRefused(
            R_NOT_A_COUNTER,
            f"{text.strip()!r} would put something on a bill, and this is a "
            f"call, not the till. Say it on the Ask screen, where a proposal "
            f"waits for you to accept it, or add the line on the till. "
            f"Nothing was done.")
    return tool, args, carried


# --------------------------------------------------------------- the model --
#
# THE TRANSPORT IS INJECTED, the same way as MUNSHI's. Absent an injection of
# its own this module uses whatever MUNSHI's is — so a test that has already
# forbidden the network for one has forbidden it for both.

Transport = Callable[[str, dict[str, str], bytes, int], "tuple[int, Any]"]


def set_transport(fn: Optional[Transport]) -> None:
    _DEPS["transport"] = fn


def transport() -> Transport:
    return _DEPS["transport"] or assistant.transport()


def _history_messages(session: Session) -> list[dict[str, Any]]:
    """The kept turns, as the model sees them. Sentences and spoken answers;
    never the data blocks behind them."""
    out: list[dict[str, Any]] = []
    for t in session.turns:
        out.append({"role": "user", "content": t.you})
        out.append({"role": "assistant", "content": t.spoken})
    return out


def router_payload(session: Session, text: str) -> dict[str, Any]:
    """THE FIRST REQUEST, whole. Prompt, the call so far, the sentence, the
    schemas. No catalogue, no price, no order, no stock, no takings."""
    return {
        "model": assistant.model_name(),
        "messages": [{"role": "system", "content": ROUTER_PROMPT},
                     *_history_messages(session),
                     {"role": "user", "content": text}],
        "tools": [dict(t) for t in TOOLS],
        "tool_choice": "auto",
        "temperature": 0,
        "stream": False,
    }


#: Keys that never leave, whatever tool produced them. Identifiers, people,
#: timestamps, and the chain itself.
_DROP_KEYS = frozenset({
    "sku_id", "order_id", "customer", "phone", "address", "at", "counted_at",
    "cost_recorded_on", "first_bill_at", "last_bill_at", "chain",
    "open_states", "units_by_sku", "line_revenue_by_sku", "derived_from",
    "note", "listed", "still_in_catalogue", "skus",
})

#: The orders rows carry the customer's name under `name`. The stock and
#: margin rows carry a PRODUCT under the same key. Only the first is a person.
_DROP_BY_TOOL: dict[str, frozenset[str]] = {
    TOOL_ORDERS: frozenset({"name"}),
    # MILAN. The settlement match carries the gateway's own identifiers —
    # payment ids, settlement ids, a UTR, the nonces this counter minted.
    # None of them helps a model phrase a rupee figure, and each is a key
    # into somebody's bank record. Only the counts and rupee strings leave.
    assistant.TOOL_BANK: frozenset({"entity_id", "payment_id", "settlement_id",
                                    "settlement_utr", "nonce", "rows",
                                    "counter_payment_id", "bill_session_id"}),
}


def _scrub(value: Any, drop: frozenset[str]) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key in drop or key.endswith("_paise"):
                continue
            out[key] = _scrub(v, drop)
        return out
    if isinstance(value, list):
        return [_scrub(v, drop) for v in value[:MAX_FACT_ROWS]]
    return value


def facts_for_model(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    """WHAT THE MODEL IS GIVEN TO PHRASE AN ANSWER, and nothing more.

    The tool's DATA block: rupee STRINGS and counts, product names where the
    tool is about products. Every `*_paise` integer is removed — a model shown
    1400 beside "14.00" will sooner or later say "Rs 1400" — and with it every
    identifier, every person, every timestamp and the chain. The counter's own
    sentence is deliberately NOT sent: it quotes order ids and sku ids by
    design, and the structured block already carries every figure it does. A
    test serialises this and asserts the absences.
    """
    drop = _DROP_KEYS | _DROP_BY_TOOL.get(tool, frozenset())
    data = result.get("data")
    facts: dict[str, Any] = {"tool": tool}
    if isinstance(data, dict):
        facts["result"] = _scrub(data, drop)
        chain = data.get("chain")
        if isinstance(chain, dict) and "ok" in chain:
            facts["audit_chain_ok"] = bool(chain.get("ok"))
            if not chain.get("ok"):
                facts["audit_chain_verified_up_to_line"] = chain.get(
                    "lines_checked")
    return facts


def _field_names(value: Any, prefix: str = "") -> list[str]:
    """Dotted key paths, for the log and the response. Names only."""
    names: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            names.append(path)
            names.extend(_field_names(v, path))
    elif isinstance(value, list) and value:
        names.extend(_field_names(value[0], f"{prefix}[]"))
    return names


def advice_payload(session: Session, text: str, tool: str,
                   args: dict[str, Any], facts: dict[str, Any],
                   lang: Optional[str] = None) -> dict[str, Any]:
    """THE SECOND REQUEST, whole: the call so far, the sentence, the tool the
    model chose, and the scrubbed result. No tool schemas — it is phrasing,
    not routing — and nothing else."""
    # THE RESULT IS HANDED OVER AS TEXT, NOT AS A FORGED TOOL EXCHANGE.
    #
    # This used to replay a `tool_calls` message the model never sent, with a
    # made-up id, followed by a `tool` message. The shape looked native and
    # every provider but one accepted it. Gemini 3 refuses a function call in
    # history that carries no `thought_signature` — a token only the model can
    # mint — with HTTP 400, so every Hindi answer fell back to the counter's
    # English sentence under "grok_refused_the_request". A fabricated call is
    # a fabricated call; the honest shape is to say what ran and what it
    # returned. What leaves the machine is byte-for-byte the same fields.
    handed = (f"The counter ran `{tool}` with {json.dumps(args, ensure_ascii=False)} "
              f"on its own machine. Its result, the only figures you have:\n"
              f"{json.dumps(facts, ensure_ascii=False)}")
    if lang:
        # SAID TWICE, AND LAST. The clause in the system prompt was not enough:
        # a small model asked "aaj kitna hua" with English selected answered
        # in Hinglish, following the question's language over the instruction
        # above it. The last thing a model reads weighs most, so the language
        # is repeated as the final line of the final message.
        handed += f"\n\nReply {lang_line(lang)}."
    return {
        "model": assistant.model_name(),
        "messages": [{"role": "system", "content": advice_system(lang)},
                     *_history_messages(session),
                     {"role": "user", "content": text},
                     {"role": "user", "content": handed}],
        "temperature": 0,
        "stream": False,
    }


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    """One POST to the provider. Returns the message, or raises
    GrokUnavailable with a reason the response will carry."""
    key = assistant.api_key()
    if not key:
        raise GrokUnavailable(
            R_GROK_UNREACHABLE,
            "XAI_API_KEY is not set, so the counter used its own parser.")
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    status, data = transport()(f"{assistant.base_url()}/chat/completions",
                               headers, body, assistant.XAI_TIMEOUT_S)
    if int(status) != 200:
        raise GrokUnavailable(
            R_GROK_HTTP,
            f"the model service answered HTTP {status}.")
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
    if not isinstance(message, dict):
        raise GrokUnavailable(
            R_GROK_SHAPE, "the model service's message was not an object.")
    return message


def _tool_call_of(message: dict[str, Any]
                  ) -> Optional[tuple[str, dict[str, Any]]]:
    """(tool, arguments) if the model chose a tool; None if it wrote prose.
    Raises AssistantRefused when it chose something it may not."""
    calls = message.get("tool_calls")
    if not calls:
        return None
    try:
        fn = calls[0]["function"]
        name = str(fn["name"])
        raw = fn.get("arguments")
    except Exception:  # noqa: BLE001
        raise GrokUnavailable(
            R_GROK_SHAPE,
            "the model's tool call had no function name in it.") from None
    if name not in TOOL_NAMES:
        raise AdvisorRefused(
            R_UNKNOWN_TOOL,
            f"the model asked for a tool called {name!r}, which this advisor "
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
            raise AdvisorRefused(
                R_BAD_TOOL_ARGS,
                f"the model's arguments for {name!r} are not JSON. Nothing "
                f"was done.") from None
    return name, assistant._check_arguments(name, args)


# ------------------------------------------------ numbers the model may say --

_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")


#: ० १ २ … and ০ ১ ২ … are the digits a Hindi or Bengali answer is written
#: in. `\d` matches them, so they were found — and then never equalled the
#: ASCII figure the tool had given, so every Hindi answer with a number in it
#: was "invented" and dropped. Translated first, so ८० IS 80.
_INDIC_DIGITS = assistant._DIGITS


def _norm_figure(s: str) -> str:
    s = s.translate(_INDIC_DIGITS).replace(",", "")
    if "." in s:
        whole, _, frac = s.partition(".")
        whole = whole.lstrip("0") or "0"
        frac = frac.rstrip("0")
        return f"{whole}.{frac}" if frac else whole
    return s.lstrip("0") or "0"


def figures_in(value: Any) -> set[str]:
    """Every number a value carries, normalised — integers as they are and
    every digit run inside a string, so "14.00" allows "14" and "2026-09-02"
    allows "2026", "9" and "2"."""
    out: set[str] = set()
    if isinstance(value, bool):
        return out
    if isinstance(value, int):
        out.add(_norm_figure(str(value)))
    elif isinstance(value, str):
        for m in _FIGURE.findall(value):
            out.add(_norm_figure(m))
            if "." in m:
                for part in m.split("."):
                    out.add(_norm_figure(part))
    elif isinstance(value, dict):
        for v in value.values():
            out |= figures_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out |= figures_in(v)
    return out


def unbacked_figures(advice: str, allowed: set[str]) -> list[str]:
    """Numbers in the advice that came from nowhere the model was shown."""
    foreign: list[str] = []
    for m in _FIGURE.findall(advice):
        if _norm_figure(m) not in allowed and m not in foreign:
            foreign.append(m)
    return foreign


def _general_answer(message: dict[str, Any], text: str) -> str:
    """The model's prose when it chose no tool — accepted only with no figure
    in it, because it has none. A number here is invented by construction."""
    content = message.get("content")
    prose = " ".join(str(content or "").split())
    if not prose:
        raise GrokUnavailable(
            R_NO_TOOL_CALL,
            "the model replied with neither a tool nor a sentence.")
    foreign = unbacked_figures(prose, figures_in(text))
    if foreign:
        raise AdvisorRefused(
            R_MODEL_INVENTED_A_FIGURE,
            f"the model answered without reading any of this shop's files and "
            f"still quoted {', '.join(foreign[:4])}. It was given no figure, "
            f"so that number is invented. Nothing was said.")
    return prose[:MAX_ADVICE]


# -------------------------------------------------------------- one turn --


def _brain_block(brain: str, err: Optional[GrokUnavailable]) -> dict[str, Any]:
    present = bool(assistant.api_key())
    return {
        # `brain` is compared internally as BRAIN_GROK/BRAIN_LOCAL; what a
        # person reads is the provider's real name.
        "brain": brain_name() if brain == BRAIN_GROK else brain,
        "model": assistant.model_name() if brain == BRAIN_GROK else None,
        "key_present": present,
        "grok_error": ({"reason": err.reason, "detail": err.detail}
                       if err else None),
    }


def _no_model_note() -> str:
    return ("No XAI_API_KEY is set in the till's environment, so this counter "
            "reads its own figures and speaks them but cannot reason about "
            "them. Set the key and restart the till for advice.")


#: When the model wrote prose and this counter's parser then tried the
#: sentence as a tool question, THESE refusals mean the parser was guessing —
#: MUNSHI reads any unplaced words as a product name — and the prose stands.
#: An ambiguity is not here: "which of these two" is a real answer about the
#: shop and beats an opinion.
_PROSE_WINS = frozenset({
    R_NOT_UNDERSTOOD, assistant.R_NO_SUCH_PRODUCT,
    assistant.R_NO_PRODUCT_NAMED, assistant.R_EMPTY_CATALOGUE,
})


def _sentences_out(session: Session) -> dict[str, Any]:
    """What a routing request carries: this call's sentences, no figures."""
    return {"sentences": len(session.turns) + 1,
            "answers": len(session.turns), "fields": []}


#: The one deterministic answer a shopkeeper asks for most, in the language
#: they asked it in. The parser's prose was English-only, so a Hindi question
#: that the model failed to route came back as "12 bills today ... Rs 3173.00"
#: — right figures, wrong language, and "Rs" read aloud as something else.
#: Only the hot template is translated here; every other tool's prose still
#: gets its money normalised for speech by `_tts.spoken_money`.
_TAKINGS = {
    "hi": ("आज ({label}) {bills} बिल हुए, कुल {rev}। उसमें से {settled} गेटवे से "
           "सेटल हो चुका है और {await_} अभी बाकी है।",
           "आज ({label}) इस काउंटर पर कोई बिल नहीं बना। यह चेन का जवाब है, अंदाज़ा नहीं।"),
    "bn": ("আজ ({label}) {bills}টি বিল হয়েছে, মোট {rev}। তার মধ্যে {settled} গেটওয়ে "
           "থেকে সেটল হয়েছে আর {await_} এখনও বাকি।",
           "আজ ({label}) এই কাউন্টারে কোনো বিল হয়নি। এটা চেইনের উত্তর, অনুমান নয়।"),
    "ta": ("இன்று ({label}) {bills} பில்கள், மொத்தம் {rev}. அதில் {settled} கேட்வேயில் "
           "செட்டில் ஆகிவிட்டது, {await_} இன்னும் நிலுவையில்.",
           "இன்று ({label}) இந்த கவுண்டரில் பில் எதுவும் இல்லை. இது சங்கிலியின் பதில், ஊகம் அல்ல."),
    "te": ("ఈరోజు ({label}) {bills} బిల్లులు, మొత్తం {rev}. అందులో {settled} గేట్‌వే ద్వారా "
           "సెటిల్ అయింది, {await_} ఇంకా పెండింగ్‌లో ఉంది.",
           "ఈరోజు ({label}) ఈ కౌంటర్‌లో బిల్లు ఏదీ లేదు. ఇది చైన్ సమాధానం, ఊహ కాదు."),
}


def _in_the_askers_language(tool: str, result: dict[str, Any], answer: str,
                            lang: Optional[str]) -> str:
    base = (lang or "en").split("-")[0].lower()
    tpl = _TAKINGS.get(base)
    brief = result.get("data") if isinstance(result.get("data"), dict) else None
    if tool != "todays_takings" or tpl is None or brief is None:
        return answer
    bills = int(brief.get("bills") or 0)
    label = str(brief.get("date") or brief.get("day") or "")
    if not bills:
        return tpl[1].format(label=label)
    r = lambda k: _tts.spoken_money(f"Rs {brief.get(k) or '0.00'}", lang or "hi-IN")
    return tpl[0].format(label=label, bills=bills, rev=r("revenue_rupees"),
                         settled=r("settled_rupees"), await_=r("awaiting_rupees"))


def _grounded(*, tool: str, args: dict[str, Any], result: dict[str, Any],
              brain: str, err: Optional[GrokUnavailable],
              carried: Optional[str], left: Optional[dict[str, Any]],
              advice: Optional[str], cannot: Optional[str],
              lang: Optional[str] = None,
              ) -> dict[str, Any]:
    """A turn whose figures came from a tool run on this machine."""
    answer = _in_the_askers_language(tool, result, str(result.get("answer") or ""), lang)
    return {
        "tool": tool, "arguments": args, "answer": answer,
        # What the VOICE gets: the same sentence with every "Rs 3173.00"
        # turned into digits plus the word for rupees in the asker's
        # language. The page keeps `answer` for the eye; a mouth reads this.
        "advice": advice, "spoken": _tts.spoken_money(advice or answer, lang or "en-IN"),
        "grounded": True, "reasoned": advice is not None,
        "data": result.get("data"),
        "context": {"carried_product": carried},
        "left_the_machine": left,
        "cannot_reason_because": cannot,
        **_brain_block(brain, err),
        "product": _product_of(tool, result),
    }


def answer_turn(session: Session, text: str,
                lang: Optional[str] = None) -> dict[str, Any]:
    """One sentence on one call. Everything that decides what is said.

    Returns the response body minus the session bookkeeping. Raises
    AssistantRefused (or its subclass) for a named refusal.
    """
    if not assistant.api_key():
        tool, args, carried = local_route(text, session)
        result = execute(tool, args, brain=BRAIN_LOCAL)
        return _grounded(tool=tool, args=args, result=result,
                         brain=BRAIN_LOCAL, err=None, carried=carried,
                         left=None, advice=None, cannot=_no_model_note(), lang=lang)

    # ---- the model routes ----------------------------------------------
    try:
        message = _post(router_payload(session, text))
        chosen = _tool_call_of(message)
    except GrokUnavailable as exc:
        tool, args, carried = local_route(text, session)
        result = execute(tool, args, brain=BRAIN_LOCAL)
        return _grounded(tool=tool, args=args, result=result,
                         brain=BRAIN_LOCAL, err=exc, carried=carried,
                         left=_sentences_out(session), advice=None,
                         cannot=exc.detail, lang=lang)

    if chosen is None:
        # Prose. If this counter's own parser can read the sentence AND run
        # it, the parser wins — a figure beats an opinion. If the parser was
        # only guessing, the prose is the answer and is marked as general.
        try:
            tool, args, carried = local_route(text, session)
            result = execute(tool, args, brain=BRAIN_LOCAL)
        except AssistantRefused as exc:
            if exc.reason not in _PROSE_WINS:
                raise
            prose = _general_answer(message, text)
            return {
                "tool": None, "arguments": {}, "answer": prose,
                "advice": None, "spoken": prose, "grounded": False,
                "reasoned": True, "data": None,
                "context": {"carried_product": None},
                "left_the_machine": _sentences_out(session),
                "cannot_reason_because": None,
                **_brain_block(BRAIN_GROK, None),
                "product": None,
            }
        err = GrokUnavailable(
            R_NO_TOOL_CALL,
            "the model replied with prose instead of choosing a tool; this "
            "counter's own parser routed the sentence and no advice was "
            "asked for.")
        return _grounded(tool=tool, args=args, result=result,
                         brain=BRAIN_LOCAL, err=err, carried=None,
                         left=_sentences_out(session), advice=None,
                         cannot=err.detail, lang=lang)

    # ---- this machine answers ------------------------------------------
    tool, args = chosen
    result = execute(tool, args, brain=BRAIN_GROK)

    # ---- the model phrases it ------------------------------------------
    facts = facts_for_model(tool, result)
    fields = _field_names(facts)
    # Recorded BEFORE the request goes: the figures are leaving whether or
    # not the provider answers, and a log that only counts successes is a log
    # that undercounts.
    _audit("advisor.consulted", session_id=session.session_id, tool=tool,
           fields=fields, turns_sent=len(session.turns))
    left = {**_sentences_out(session), "fields": fields}
    advice: Optional[str] = None
    err: Optional[GrokUnavailable] = None
    try:
        message = _post(advice_payload(session, text, tool, args, facts, lang))
        prose = " ".join(str(message.get("content") or "").split())
        if not prose:
            raise GrokUnavailable(
                R_ADVICE_EMPTY,
                "the model returned no sentence to speak, so the counter's "
                "own is spoken instead.")
        allowed = figures_in(facts) | figures_in(text) \
            | figures_in(advice_system(lang))
        foreign = unbacked_figures(prose, allowed)
        if foreign:
            raise GrokUnavailable(
                R_MODEL_INVENTED_A_FIGURE,
                f"the model's advice quoted {', '.join(foreign[:4])}, which "
                f"is in none of the figures it was given. The advice was "
                f"dropped and the counter's own sentence is spoken instead.")
        advice = prose[:MAX_ADVICE]
    except GrokUnavailable as exc:
        err = exc
    return _grounded(tool=tool, args=args, result=result, brain=BRAIN_GROK,
                     err=err, carried=None, left=left, advice=advice,
                     cannot=err.detail if err else None, lang=lang)


# ----------------------------------------------------------------- routes --


def _read_lang(body: dict[str, Any]) -> Optional[str]:
    """`lang`, if the page sent one it may. Absent is fine — the prompt then
    says "the language the shopkeeper used"; unknown is a named refusal."""
    from . import tts as _tts
    raw = body.get("lang")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or raw not in _tts.LANGS:
        raise AdvisorRefused(
            _tts.R_BAD_LANG,
            f"{raw!r} is not a language this counter speaks. It speaks: "
            f"{', '.join(_tts.LANGS)}.")
    return raw


def voice_block() -> dict[str, Any]:
    from . import tts as _tts
    ok, why = _tts.available()
    return {
        "available": ok,
        "model": _tts.model_name() if ok else None,
        "voice": _tts.voice_name("hi-IN") if ok else None,
        "languages": list(_tts.LANGS),
        "why_not": why,
        "sends": ("the one sentence to be spoken — the same words the model "
                  "phrased, never a figure the sentence does not carry"),
    }


def ears_block() -> dict[str, Any]:
    """What this counter can hear with, beside what it can speak with.

    `/advisor/health` already says whether there is a voice; a page deciding
    whether to offer the microphone at all needs the same answer about ears.
    """
    from . import stt as _stt
    ok, why = _stt.available()
    return {
        "available": ok,
        "model": _stt.model_name() if ok else None,
        "languages": list(_stt.LANGS),
        "max_seconds": _stt.MAX_SECONDS,
        "why_not": why,
        "sends": ("the recording, and nothing else — the same departure the "
                  "browser's own recogniser makes, to the provider this shop "
                  "configured rather than one nobody chose"),
    }


_VOICE_KEY = re.compile(r"^[0-9a-f]{64}$")


@router.post("/advisor/speak")
async def advisor_speak_ep(request: Request) -> JSONResponse:
    """Have a sentence voiced. Body: {"text", "lang"}. Answers with WHERE the
    audio is, not the audio.

    The page then plays `/advisor/voice/<key>.wav` — a same-origin URL, which
    the till's CSP allows without being widened. The first design handed the
    bytes back and the page wrapped them in a `blob:` URL; `default-src
    'self'` refused to play it, the tag said "fetched once", and the answer
    was silent. Serving the cached file by name needs no blob, no object URL
    to revoke, and lets the browser cache the sound itself.

    Refused by name — in JSON, never a 500 — when no key is set, the provider
    has no voice, or the text is over the cap. The page falls back to the
    browser's own voice on any refusal, so a refusal here costs a shopkeeper
    nothing but a robotic sentence.
    """
    from . import tts as _tts
    try:
        body = await assistant._json_body(request)
        raw = body.get("text")
        if not isinstance(raw, str) or not raw.strip():
            raise AdvisorRefused(_tts.R_EMPTY, "nothing was given to say.")
        lang = _read_lang(body) or "hi-IN"
    except AssistantRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)

    try:
        v = _tts.synthesise(raw, lang)
    except _tts.TTSRefused as exc:
        return JSONResponse(
            {"ok": False, "reason": exc.reason, "detail": exc.detail,
             "settles_money": False}, status_code=400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)

    # Its length, never its words. Recorded whether or not it was cached,
    # because what the page is told is "a sentence went to be voiced", and a
    # cached one went the first time.
    _audit("advisor.voiced", chars=v.chars, lang=v.lang, cached=v.cached,
           model=v.model)
    key = v.path.stem
    return JSONResponse({
        "ok": True,
        "settles_money": False,
        "url": f"/advisor/voice/{key}.wav",
        "key": key,
        "cached": v.cached,
        "model": v.model,
        "voice": v.voice,
        "lang": v.lang,
        "chars": v.chars,
        "bytes": len(v.wav),
        "note": ("One sentence went to be voiced" + (", the first time it was said"
                 if v.cached else "") + ". Its length is on this module's own "
                 "chain; its words are not."),
    })


@router.post("/advisor/listen")
async def advisor_listen_ep(request: Request) -> JSONResponse:
    """Write down what was said. Body: {"audio_b64", "mime", "lang", "seconds"}.

    THE SECOND PAIR OF EARS. The page listens with the browser's own
    `SpeechRecognition` first, because it needs no key and costs nothing. But
    that API is a cloud call to Google's speech service wearing a browser
    API's clothes, and when the network refuses it the microphone simply stops
    — "The speech service could not be reached" over a till that is otherwise
    working, which is what happened on the machine this was written on.

    So the page may post the recording here instead, and the counter writes it
    down on the same key that already reasons and speaks. Same origin, so the
    till's `default-src 'self'` needs no widening: a browser that cannot reach
    a CDN certainly cannot be handed a third-party speech endpoint.

    Refused by name — in JSON, never a 500 — when no key is set, the provider
    has no speech API, the clip is over the cap, or nothing was heard. Every
    refusal leaves the browser's own recogniser as the fallback, which is the
    same bargain `/advisor/speak` makes with the browser's own voice.
    """
    import base64 as _b64

    from . import stt as _stt
    try:
        body = await assistant._json_body(request)
        raw = body.get("audio_b64")
        if not isinstance(raw, str) or not raw.strip():
            raise AdvisorRefused(_stt.R_EMPTY, "no recording was sent.")
        try:
            audio = _b64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001 - a bad body is a refusal, not a crash
            raise AdvisorRefused(
                _stt.R_EMPTY, "the recording was not readable base64.") from None
        mime = body.get("mime")
        lang = _read_lang(body) or "hi-IN"
        secs = body.get("seconds")
        seconds = float(secs) if isinstance(secs, (int, float)) else None
    except AssistantRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)

    try:
        h = _stt.transcribe(audio, mime, lang, seconds)
    except _stt.STTRefused as exc:
        return JSONResponse(
            {"ok": False, "reason": exc.reason, "detail": exc.detail,
             "settles_money": False}, status_code=400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)

    # ITS LENGTH, NEVER ITS WORDS — the same rule `advisor.voiced` keeps. What
    # the shopkeeper said is handed back to their own page and to nothing else.
    _audit("advisor.heard", chars=len(h.text), lang=h.lang, model=h.model,
           bytes_in=h.bytes_in, seconds=h.seconds)
    return JSONResponse({
        "ok": True,
        "settles_money": False,
        "text": h.text,
        "lang": h.lang,
        "model": h.model,
        "bytes": h.bytes_in,
        "seconds": h.seconds,
        "note": ("The recording went to be written down. Its length is on this "
                 "module's own chain; its words are not, and the catalogue, "
                 "the prices and the bill did not go with it."),
    })


@router.get("/advisor/voice/{name}")
def advisor_voice_ep(name: str) -> Response:
    """One cached sentence, as audio. The name is the sha256 the till gave the
    page a moment ago; anything else is refused before the disk is touched."""
    from . import tts as _tts
    key = name[:-4] if name.endswith(".wav") else name
    if not _VOICE_KEY.match(key):
        return JSONResponse(
            {"ok": False, "reason": "voice_no_such_sentence",
             "detail": "that is not the name of a voiced sentence.",
             "settles_money": False}, status_code=404)
    path = _tts.cache_dir() / f"{key}.wav"
    try:
        wav = path.read_bytes()
    except OSError:
        return JSONResponse(
            {"ok": False, "reason": "voice_no_such_sentence",
             "detail": "that sentence has not been voiced on this till, or its "
                       "file is gone. Ask for it again.",
             "settles_money": False}, status_code=404)
    return Response(
        content=wav, media_type="audio/wav",
        headers={"Cache-Control": "private, max-age=86400",
                 "X-Gawaah-Voice-Chars": str(max(0, (len(wav) - 44)))})


@router.get("/advisor/health")
def advisor_health_ep() -> JSONResponse:
    """Which brain would answer, what a call keeps, and what leaves."""
    try:
        try:
            n_products = len(assistant.catalogue())
            catalogue_problem = None
        except AssistantRefused as exc:
            n_products = 0
            catalogue_problem = {"reason": exc.reason, "detail": exc.detail}
        present = bool(assistant.api_key())
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "brain": brain_name() if present else BRAIN_LOCAL,
            "key_present": present,
            "reasons": present,
            "model": assistant.model_name() if present else None,
            "base_url": assistant.base_url(),
            "tools": list(TOOL_NAMES),
            "products_priced": n_products,
            "catalogue_problem": catalogue_problem,
            "sessions_live": sessions_live(),
            "keeps": {"turns": MAX_TURNS, "for_s": SESSION_TTL_S,
                      "on_disk": False},
            "voice": voice_block(),
        "ears": ears_block(),
            "sends_to_the_model": (
                "with a key: this call's last turns, the sentence, the tool "
                "schemas — and, to phrase the answer, the one tool's result "
                "with every paise integer, identifier, person and timestamp "
                "removed. Without a key: nothing."),
            "cannot_reason_because": None if present else _no_model_note(),
            "note": ("This is a call, not the till. It bills nothing, moves "
                     "no stock and settles no money; it has no add-to-bill "
                     "tool at all."),
        })
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/advisor/say")
async def advisor_say_ep(request: Request) -> JSONResponse:
    """One sentence on a call. Body: {"text", "source", "session_id"?}.

    Omit `session_id` to start a call; send the one that came back to stay on
    it. The answer carries `spoken`, which is what the page says aloud.
    """
    try:
        body = await assistant._json_body(request)
        assistant._refuse_authorship(body)
        text = assistant._read_text(body)
        source = assistant._read_source(body)
        lang = _read_lang(body)
        session, found = _open_session(body.get("session_id"))
    except AssistantRefused as exc:
        return _refusal(exc)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)

    present = bool(assistant.api_key())
    brain: dict[str, Any] = {
        "brain": brain_name() if present else BRAIN_LOCAL,
        "model": assistant.model_name() if present else None,
        "key_present": present, "grok_error": None}
    try:
        turn = answer_turn(session, text, lang)
        product = turn.pop("product", None)
        with _LOCK:
            session.turn_count += 1
            session.turns.append(Turn(
                at=_now_iso(), you=text, tool=turn["tool"],
                spoken=str(turn["spoken"]), product=product,
                reasoned=bool(turn["reasoned"])))
            session.last_mono = _mono_s()
            n_turn = session.turn_count
            kept = len(session.turns)
        return JSONResponse({
            "ok": True,
            "settles_money": False,
            "session_id": session.session_id,
            "turn": n_turn,
            "context_turns": kept - 1,
            "resumed": found["resumed"],
            "previous_call": found["previous"],
            "expires_in_s": SESSION_TTL_S,
            "heard": text,
            "source": source,
            "lang": lang,
            **turn,
            "note": ("Nothing here has been billed, charged or taken off a "
                     "shelf. What was said on this call is kept in memory for "
                     f"{SESSION_TTL_S // 60} minutes and is not written to "
                     "disk."),
        })
    except AssistantRefused as exc:
        return _refusal(exc, session_id=session.session_id, **brain)
    except MoneyError as exc:
        return _refusal(AdvisorRefused(
            assistant.R_NO_CATALOGUE,
            f"a price in this shop's files is not integer paise ({exc}). "
            f"Nothing was said."), session_id=session.session_id, **brain)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.get("/advisor/session/{session_id}")
def advisor_session_ep(session_id: str) -> JSONResponse:
    """This call's kept turns, read back from memory."""
    try:
        s = _get_session(session_id)
        return JSONResponse({"ok": True, "settles_money": False,
                             **_session_view(s)})
    except AssistantRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_SESSION else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)


@router.post("/advisor/session/{session_id}/end")
def advisor_end_ep(session_id: str) -> JSONResponse:
    """Hang up. The call is forgotten; there was nothing on disk to delete."""
    try:
        s = _end_session(session_id)
        return JSONResponse({"ok": True, "settles_money": False,
                             "session_id": s.session_id,
                             "turns_forgotten": s.turn_count,
                             "note": "Nothing about this call was on disk."})
    except AssistantRefused as exc:
        return _refusal(exc, status=404 if exc.reason == R_NO_SESSION else 400)
    except Exception as exc:  # noqa: BLE001 - never a 500
        return _crash(exc)
