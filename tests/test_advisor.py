"""gawaah/advisor.py — the call, and what it does and does not send.

Five claims, each asserted against running code with NO key and NO network:

  1. IT ANSWERS FROM THE SHOP'S OWN FILES, through MUNSHI's executors and
     KHAREED's margin — never a figure of its own. With no key it says plainly
     that it cannot reason, and still answers.

  2. THE CONTEXT IS SHORT, IN MEMORY, AND EXPIRES. Last MAX_TURNS turns, gone
     after SESSION_TTL_S, never on disk. A test walks the temp directory for
     the sentence and does not find it.

  3. WHAT LEAVES IS BOUNDED AND NAMED. The router request carries no shop
     data. The phrasing request carries the one tool's result with every
     paise integer, sku id, customer name, phone and address removed — the
     bytes are read back and asserted — and the field names go to the
     advisor's own hash chain.

  4. THE MODEL MAY NOT INVENT A NUMBER. A figure in its advice that is not in
     what it was shown drops the advice by name; a prose answer with a digit
     in it is refused.

  5. EVERY REFUSAL HAS A NAME, and no input of any shape produces a 500.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import advisor  # noqa: E402
from gawaah import assistant  # noqa: E402
from gawaah import purchases  # noqa: E402
from gawaah import storefront  # noqa: E402
from gawaah.advisor import (  # noqa: E402
    BRAIN_GROK,
    BRAIN_LOCAL,
    MAX_SESSIONS,
    MAX_TEXT,
    MAX_TURNS,
    R_ADVICE_EMPTY,
    R_BAD_BODY,
    R_BAD_SESSION_ID,
    R_BAD_SOURCE,
    R_CLIENT_AUTHORED,
    R_GROK_HTTP,
    R_GROK_UNREACHABLE,
    R_INTERNAL,
    R_MARGIN_UNAVAILABLE,
    R_MODEL_INVENTED_A_FIGURE,
    R_MODEL_PRICED,
    R_NO_SESSION,
    R_NO_TEXT,
    R_NO_TOOL_CALL,
    R_NOT_A_COUNTER,
    R_TEXT_TOO_LONG,
    R_UNKNOWN_TOOL,
    SESSION_ID_RE,
    SESSION_TTL_S,
    TOOL_FIND,
    TOOL_LOW_STOCK,
    TOOL_MARGIN,
    TOOL_NAMES,
    TOOL_ORDERS,
    TOOL_PRICE,
    TOOL_TAKINGS,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from tools import upload_app  # noqa: E402

# Deliberately not round numbers: a bug that divides or rounds shows up in the
# second decimal place or not at all.
MAGGI = ("maggi_noodles_70g", "Maggi Noodles 70g", 1400)
BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
SOAP = ("lifebuoy_125g", "Lifebuoy 125g", 3950)
MILK = ("amul_milk_500ml", "Amul Milk 500ml", 2750)
CATALOGUE = (MAGGI, BISCUIT, SOAP, MILK)

#: A key that is not a key. Used only to prove it never reaches a response.
FAKE_KEY = "xai-this-string-must-never-appear-in-a-response"


def _forbidden_transport(url, headers, body, timeout):
    raise AssertionError(
        f"a test tried to reach {url} for real. The provider is always faked.")


@pytest.fixture()
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A shop that lives and dies with the test.

    The catalogue is redirected three ways, as every other suite here does:
    `set_store_dir` for the till's cached handle, GAWAAH_SHOP_DIR for anything
    that re-reads the environment, GAWAAH_DATA_DIR for the audit chain. The
    key is DELETED, the network is FORBIDDEN on both modules, and every call
    kept in memory is forgotten before the next test.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    monkeypatch.delenv("XAI_MODEL", raising=False)
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")

    assistant.set_transport(_forbidden_transport)
    advisor.set_transport(None)
    advisor.set_monotonic(None)
    advisor._SESSIONS.clear()
    app = FastAPI()
    app.include_router(advisor.router)
    app.include_router(purchases.router)
    client = TestClient(app)
    try:
        yield client
    finally:
        assistant.set_transport(None)
        advisor.set_transport(None)
        advisor.set_monotonic(None)
        advisor._SESSIONS.clear()
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


def say(client: TestClient, text: str, **over):
    body = {"text": text}
    body.update(over)
    return client.post("/advisor/say", json=body)


def refusal(resp, reason: str) -> dict:
    """Every refusal in this program has the same shape. Assert all of it."""
    assert resp.status_code in (400, 404), resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == reason, body
    assert isinstance(body["detail"], str) and body["detail"].strip()
    assert body["settles_money"] is False
    return body


class Fake:
    """A transport that answers a SEQUENCE — the routing call, then the
    phrasing call — and records every byte that would have gone out."""

    def __init__(self, *answers, status: int = 200):
        self.answers = list(answers)
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "body": body.decode("utf-8"), "timeout": timeout})
        nxt = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(nxt):
            return nxt()
        if isinstance(nxt, tuple):
            return nxt
        return self.status, nxt

    def sent(self, i: int = -1) -> dict:
        return json.loads(self.calls[i]["body"])


def tool_call(name: str, args) -> dict:
    return {"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": name,
                      "arguments": args if isinstance(args, str)
                      else json.dumps(args)}}]}}]}


def prose(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _chain_one_sale(tmp_data: Path, sku: str, price_paise: int) -> None:
    """One closed bill on this counter's own audit chain."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    led = Ledger(tmp_data / "audit.jsonl")
    led.append(ts=now, module="session", event="exit", session_id="s1",
               item_id=f"{sku}#0", price_paise=price_paise,
               reason="exit_crossing_committed")
    led.append(ts=now, module="session", event="done", session_id="s1",
               total_paise=price_paise, lines=1, reason="intent_requested")


def _record_cost(client: TestClient, sku: str, cost_paise: int) -> None:
    r = client.post("/purchases/suppliers",
                    json={"name": "Sharma Traders", "phone": "9876543210"})
    assert r.status_code == 200, r.text
    sid = r.json()["supplier"]["supplier_id"]
    r = client.post("/purchases", json={
        "supplier_id": sid,
        "lines": [{"sku_id": sku, "units": 10, "cost_paise": cost_paise}]})
    assert r.status_code == 200, r.text


def _order(order_id: str, status: str, total_paise: int) -> dict:
    return {"format": 1, "order_id": order_id,
            "at": "2026-09-01T09:00:00+00:00",
            "status": status, "status_changed_at": "2026-09-01T09:00:00+00:00",
            "customer": {"name": "Rekha", "phone": "9876543210",
                         "address": "12 MG Road, near the water tank"},
            "lines": [{"sku_id": MAGGI[0], "qty": 1}],
            "total_paise": total_paise,
            "payment": {"session_id": f"shop_{order_id}", "paid": False}}


# ------------------------------------------------------------------------
# 1. With no key it answers from the shop's files, and says it cannot reason.
# ------------------------------------------------------------------------


def test_health_says_which_brain_and_that_nothing_is_on_disk(shop, monkeypatch):
    off = shop.get("/advisor/health").json()
    assert off["ok"] is True and off["settles_money"] is False
    assert off["brain"] == BRAIN_LOCAL and off["reasons"] is False
    assert off["key_present"] is False and off["model"] is None
    assert off["keeps"] == {"turns": MAX_TURNS, "for_s": SESSION_TTL_S,
                            "on_disk": False}
    assert "XAI_API_KEY" in off["cannot_reason_because"]
    assert set(off["tools"]) == set(TOOL_NAMES)
    assert assistant.TOOL_ADD not in off["tools"]
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    on = shop.get("/advisor/health").json()
    assert on["brain"] == BRAIN_GROK and on["reasons"] is True
    assert on["cannot_reason_because"] is None
    assert FAKE_KEY not in json.dumps(on)


def test_with_no_key_the_figures_are_spoken_and_reasoning_is_declined(shop):
    body = say(shop, "aaj ki bikri kitni hui").json()
    assert body["ok"] is True
    assert body["tool"] == TOOL_TAKINGS
    assert body["brain"] == BRAIN_LOCAL and body["key_present"] is False
    assert body["reasoned"] is False and body["advice"] is None
    assert body["grounded"] is True
    assert body["spoken"] == body["answer"]
    assert "Nothing has been billed" in body["spoken"]
    assert "XAI_API_KEY" in body["cannot_reason_because"]
    assert body["left_the_machine"] is None
    assert SESSION_ID_RE.match(body["session_id"])
    assert body["turn"] == 1 and body["context_turns"] == 0
    assert body["resumed"] is False and body["previous_call"] is None


@pytest.mark.parametrize("said,tool", [
    ("aaj ki bikri kitni hui", TOOL_TAKINGS),
    ("kitne online orders pending hain", TOOL_ORDERS),
    ("kya khatam ho raha hai", TOOL_LOW_STOCK),
    ("Maggi ka daam kya hai", TOOL_PRICE),
    ("Lifebuoy hai kya", TOOL_FIND),
    ("aaj ka munafa kitna hua", TOOL_MARGIN),
    ("what is my margin today", TOOL_MARGIN),
    ("kitna profit hua aaj", TOOL_MARGIN),
])
def test_the_local_parser_routes_these_questions(shop, said, tool):
    body = say(shop, said).json()
    assert body["ok"] is True, body
    assert body["tool"] == tool


def test_a_price_answer_comes_from_the_catalogue_not_the_advisor(shop):
    body = say(shop, "Maggi ka daam kya hai").json()
    assert body["data"]["price_paise"] == MAGGI[2]
    assert isinstance(body["data"]["price_paise"], int)
    assert "14.00" in body["spoken"]


def test_an_instruction_to_bill_is_refused_because_this_is_a_call(shop):
    body = refusal(say(shop, "do Maggi bill me daal do"), R_NOT_A_COUNTER)
    assert "till" in body["detail"]
    # Nothing was proposed, let alone billed: MUNSHI's proposal dir is untouched.
    assert not assistant.proposals_dir().exists()


def test_voice_and_text_land_on_the_same_answer(shop):
    typed = say(shop, "Maggi ka daam kya hai", source="text").json()
    spoken = say(shop, "Maggi ka daam kya hai", source="voice").json()
    assert typed["spoken"] == spoken["spoken"]
    assert spoken["source"] == "voice"


# ------------------------------------------------------------------------
# 2. The margin, through KHAREED's own derivation.
# ------------------------------------------------------------------------


def test_the_margin_is_kharreds_figure_and_the_unknown_part_is_named(
        shop, tmp_path):
    _record_cost(shop, BISCUIT[0], 1400)
    _chain_one_sale(tmp_path / "data", BISCUIT[0], BISCUIT[2])
    body = say(shop, "aaj ka munafa kitna hua").json()
    assert body["ok"] is True and body["tool"] == TOOL_MARGIN
    d = body["data"]
    assert d["bills"] == 1
    assert d["covered"]["margin_paise"] == BISCUIT[2] - 1400
    assert d["covered"]["margin_rupees"] == "7.45"
    assert d["margin_is_partial"] is False
    assert "7.45" in body["spoken"]


def test_a_sale_with_no_recorded_cost_is_a_partial_margin_not_a_zero(
        shop, tmp_path):
    _chain_one_sale(tmp_path / "data", SOAP[0], SOAP[2])
    body = say(shop, "kitna profit hua").json()
    d = body["data"]
    assert d["margin_is_partial"] is True
    assert d["uncovered"]["units"] == 1
    assert d["uncovered"]["revenue_paise"] == SOAP[2]
    assert "not known" in body["spoken"] and "not zero" in body["spoken"]


def test_nothing_billed_means_no_margin_rather_than_a_zero(shop):
    body = say(shop, "margin kya hai aaj").json()
    assert body["data"]["bills"] == 0
    assert "Nothing has been billed" in body["spoken"]


def test_a_margin_that_cannot_be_derived_is_a_named_refusal(shop, monkeypatch):
    def boom(day=None):
        raise OSError("the purchases directory is gone")

    monkeypatch.setattr(purchases, "margin_today_ep", boom)
    refusal(say(shop, "aaj ka munafa"), R_MARGIN_UNAVAILABLE)


# ------------------------------------------------------------------------
# 3. The context: short, in memory, expiring, and used out loud.
# ------------------------------------------------------------------------


def test_a_call_keeps_its_turns_and_reads_them_back(shop):
    first = say(shop, "Maggi ka daam kya hai").json()
    sid = first["session_id"]
    second = say(shop, "aaj ki bikri kitni hui", session_id=sid).json()
    assert second["session_id"] == sid
    assert second["turn"] == 2 and second["context_turns"] == 1
    assert second["resumed"] is True
    view = shop.get(f"/advisor/session/{sid}").json()
    assert view["ok"] is True and view["on_disk"] is False
    assert [t["tool"] for t in view["turns"]] == [TOOL_PRICE, TOOL_TAKINGS]
    assert view["turns"][0]["you"] == "Maggi ka daam kya hai"
    assert view["turns"][0]["spoken"] == first["spoken"]


def test_uska_means_the_product_this_call_last_named(shop):
    first = say(shop, "Lifebuoy milega kya").json()
    assert first["tool"] == TOOL_FIND
    second = say(shop, "uska daam kya hai", session_id=first["session_id"]).json()
    assert second["ok"] is True, second
    assert second["tool"] == TOOL_PRICE
    assert second["data"]["sku_id"] == SOAP[0]
    assert second["context"]["carried_product"] == SOAP[1]
    assert "39.50" in second["spoken"]


def test_uska_with_nothing_named_yet_is_refused_not_guessed(shop):
    body = refusal(say(shop, "uska daam kya hai"),
                   assistant.R_NO_SUCH_PRODUCT)
    assert "uska" in body["detail"]


def test_the_context_is_capped_at_max_turns(shop):
    sid = say(shop, "Maggi ka daam kya hai").json()["session_id"]
    last = None
    for _ in range(MAX_TURNS + 3):
        last = say(shop, "aaj ki bikri kitni hui", session_id=sid).json()
    assert last["turn"] == MAX_TURNS + 4
    assert last["context_turns"] == MAX_TURNS - 1
    view = shop.get(f"/advisor/session/{sid}").json()
    assert len(view["turns"]) == MAX_TURNS
    assert view["turn_count"] == MAX_TURNS + 4


def test_a_quiet_call_expires_and_a_new_one_says_so(shop):
    clock = {"t": 1000}
    advisor.set_monotonic(lambda: clock["t"])
    sid = say(shop, "Maggi ka daam kya hai").json()["session_id"]
    clock["t"] += SESSION_TTL_S + 1
    body = say(shop, "aaj ki bikri", session_id=sid).json()
    assert body["ok"] is True
    assert body["session_id"] != sid
    assert body["resumed"] is False
    assert body["previous_call"] == "expired_or_unknown"
    assert body["context_turns"] == 0
    r = shop.get(f"/advisor/session/{sid}")
    assert r.status_code == 404
    refusal(r, R_NO_SESSION)


def test_hanging_up_forgets_the_call(shop):
    sid = say(shop, "Maggi ka daam kya hai").json()["session_id"]
    say(shop, "aaj ki bikri", session_id=sid)
    r = shop.post(f"/advisor/session/{sid}/end")
    assert r.status_code == 200
    assert r.json()["turns_forgotten"] == 2
    assert shop.get(f"/advisor/session/{sid}").status_code == 404
    assert shop.post(f"/advisor/session/{sid}/end").status_code == 404


def test_the_number_of_live_calls_is_capped(shop):
    clock = {"t": 1000}
    advisor.set_monotonic(lambda: clock["t"])
    for _ in range(MAX_SESSIONS + 5):
        clock["t"] += 1
        assert say(shop, "Maggi ka daam kya hai").status_code == 200
    assert shop.get("/advisor/health").json()["sessions_live"] == MAX_SESSIONS


@pytest.mark.parametrize("bad", ["call_zzzzzzzzzzzz", "../../catalog",
                                 "prop_0123456789ab", "call_", "x",
                                 "call_0123456789ab/../x"])
def test_a_malformed_call_id_is_refused_before_anything_is_looked_up(shop, bad):
    refusal(say(shop, "aaj ki bikri", session_id=bad), R_BAD_SESSION_ID)
    with pytest.raises(assistant.AssistantRefused) as exc:
        advisor._get_session(bad)
    assert exc.value.reason == R_BAD_SESSION_ID
    # Through the URL only for ids the HTTP client will not normalise away
    # first: "../.." never reaches a handler, so it proves nothing here.
    if "/" not in bad:
        refusal(shop.get(f"/advisor/session/{bad}"), R_BAD_SESSION_ID)
        refusal(shop.post(f"/advisor/session/{bad}/end"), R_BAD_SESSION_ID)


def test_nothing_said_on_a_call_is_written_to_disk(shop, tmp_path):
    marker = "zxq-unique-marker-sentence"
    sid = say(shop, f"{marker} ka daam kya hai").json()["session_id"]
    say(shop, "aaj ki bikri kitni hui", session_id=sid)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert marker not in p.read_text(encoding="utf-8",
                                             errors="ignore"), p


# ------------------------------------------------------------------------
# 4. With a key: the model routes, this machine answers, the model phrases —
#    and what leaves is bounded, scrubbed and named.
# ------------------------------------------------------------------------


def test_grok_routes_then_phrases_and_the_history_goes_with_it(shop, tmp_path,
                                                                monkeypatch):
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    # The first turn is taken with no key, so the call already has history
    # when the model is switched on for the second.
    sid = say(shop, "Maggi ka daam kya hai").json()["session_id"]
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)

    fake = Fake(tool_call(TOOL_TAKINGS, {}),
                prose("Aaj 1 bill hua, Rs 14.00 ka. Poora Rs 14.00 abhi "
                      "gateway se settle hona baaki hai."))
    advisor.set_transport(fake)
    body = say(shop, "aur aaj kitna hua", session_id=sid).json()
    assert body["ok"] is True, body
    assert body["brain"] == BRAIN_GROK and body["reasoned"] is True
    assert body["tool"] == TOOL_TAKINGS
    assert body["advice"].startswith("Aaj 1 bill hua")
    assert body["spoken"] == body["advice"]
    assert body["data"]["revenue_paise"] == MAGGI[2]
    assert body["grok_error"] is None
    assert len(fake.calls) == 2

    routing = fake.sent(0)
    roles = [m["role"] for m in routing["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert routing["messages"][1]["content"] == "Maggi ka daam kya hai"
    assert routing["messages"][3]["content"] == "aur aaj kitna hua"
    assert {t["function"]["name"] for t in routing["tools"]} == set(TOOL_NAMES)

    phrasing = fake.sent(1)
    assert "tools" not in phrasing
    # The result is handed over as TEXT, in a second user message — not as a
    # replayed `tool_calls` exchange the model never sent. Gemini 3 refused
    # that forged call (no `thought_signature`) with HTTP 400, and every Hindi
    # answer silently fell back to the counter's English sentence.
    assert [m["role"] for m in phrasing["messages"]][-2:] == ["user", "user"]
    handed = phrasing["messages"][-1]["content"]
    assert TOOL_TAKINGS in handed and "revenue_rupees" in handed
    assert not any(m.get("tool_calls") for m in phrasing["messages"])
    assert body["left_the_machine"]["sentences"] == 2
    assert "result.revenue_rupees" in body["left_the_machine"]["fields"]


def test_the_routing_request_carries_no_shop_data(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    storefront._write_order(_order("ord_00000000000a", "new", 9999))
    fake = Fake(tool_call(TOOL_ORDERS, {}), prose("Ek order khula hai."))
    advisor.set_transport(fake)
    say(shop, "kitne orders pending hain")
    raw = fake.calls[0]["body"].lower()
    for secret in ("parle", "lifebuoy", "amul", "2145", "3950", "2750",
                   "maggi_noodles_70g", "rekha", "9876543210", "mg road",
                   "ord_00000000000a", "9999"):
        assert secret not in raw, f"{secret!r} leaked into the routing request"


def test_the_phrasing_request_carries_the_result_scrubbed(shop, monkeypatch):
    """THE PRIVACY PROPERTY OF THE SECOND CALL, on the bytes."""
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    storefront._write_order(_order("ord_00000000000a", "new", 9999))
    fake = Fake(tool_call(TOOL_ORDERS, {}),
                prose("Ek order khula hai, Rs 99.99 ka."))
    advisor.set_transport(fake)
    body = say(shop, "kitne orders pending hain").json()
    assert body["reasoned"] is True, body
    raw = fake.calls[1]["body"].lower()
    for secret in ("rekha", "9876543210", "mg road", "water tank",
                   "ord_00000000000a", "_paise", "sku_id", "maggi_noodles",
                   "\"9999\"", "session_id"):
        assert secret not in raw, f"{secret!r} leaked into the phrasing request"
    assert "99.99" in raw
    assert "total_rupees" in raw
    assert FAKE_KEY not in raw


def test_facts_for_model_strips_paise_people_and_ids():
    result = {"answer": "1 open", "data": {
        "pending": 1, "total_paise": 9999, "total_rupees": "99.99",
        "orders": [{"order_id": "ord_x", "name": "Rekha", "status": "new",
                    "total_paise": 9999, "total_rupees": "99.99",
                    "lines": 1, "paid": False}],
        "chain": {"ok": True, "lines_checked": 3, "error": None}}}
    facts = advisor.facts_for_model(TOOL_ORDERS, result)
    flat = json.dumps(facts)
    assert "9999" not in flat and "_paise" not in flat
    assert "Rekha" not in flat and "ord_x" not in flat
    assert facts["result"]["orders"][0] == {"status": "new",
                                            "total_rupees": "99.99",
                                            "lines": 1, "paid": False}
    assert facts["audit_chain_ok"] is True
    assert "chain" not in facts["result"]


def test_a_product_name_may_leave_but_a_customers_may_not():
    stock = {"answer": "low", "data": {"low": [
        {"sku_id": "maggi_noodles_70g", "name": "Maggi Noodles 70g",
         "remaining_units": 2, "counted_at": "2026-01-01"}]}}
    assert advisor.facts_for_model(TOOL_LOW_STOCK, stock)["result"]["low"][0] \
        == {"name": "Maggi Noodles 70g", "remaining_units": 2}
    orders = {"answer": "1", "data": {"orders": [{"name": "Rekha"}]}}
    assert advisor.facts_for_model(TOOL_ORDERS, orders)["result"]["orders"] \
        == [{}]


def test_every_consultation_lands_on_the_advisors_own_chain_as_names_only(
        shop, tmp_path, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    advisor.set_transport(Fake(tool_call(TOOL_TAKINGS, {}),
                               prose("Rs 14.00 aaj, 1 bill.")))
    body = say(shop, "aaj kitna hua").json()
    assert body["reasoned"] is True
    path = advisor.audit_path()
    assert path.parent == assistant.shop_dir()
    assert path.name == "advisor.audit.jsonl"
    ok, n, _head, err = verify(path)
    assert ok and n == 1, err
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["event"] == "advisor.consulted"
    assert line["tool"] == TOOL_TAKINGS
    assert "result.revenue_rupees" in line["fields"]
    assert "14.00" not in json.dumps(line)
    assert "aaj kitna hua" not in json.dumps(line)


def test_the_advisor_never_writes_to_the_money_chain(shop, tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(tool_call(TOOL_PRICE, {"product": "Maggi"}),
                               prose("Maggi Rs 14.00 ka hai.")))
    say(shop, "Maggi ka daam")
    assert not (tmp_path / "data" / "audit.jsonl").exists()


def test_the_key_goes_out_as_a_bearer_header_and_never_comes_back(shop,
                                                                 monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    fake = Fake(tool_call(TOOL_PRICE, {"product": "Maggi"}),
                prose("Maggi Rs 14.00 ka hai."))
    advisor.set_transport(fake)
    r = say(shop, "Maggi ka daam")
    sid = r.json()["session_id"]
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    for resp in (r, shop.get("/advisor/health"),
                 shop.get(f"/advisor/session/{sid}")):
        assert FAKE_KEY not in resp.text


# ------------------------------------------------------------------------
# 5. The model may not invent a number.
# ------------------------------------------------------------------------


def test_advice_quoting_a_figure_it_was_not_given_is_dropped_by_name(
        shop, tmp_path, monkeypatch):
    """"Rs 1400" is the paise integer misread as rupees — the exact mistake
    scrubbing the paise is there to make impossible to pass silently."""
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    advisor.set_transport(Fake(tool_call(TOOL_TAKINGS, {}),
                               prose("Aaj Rs 1400 ki bikri hui, badhiya.")))
    body = say(shop, "aaj kitna hua").json()
    assert body["ok"] is True
    assert body["reasoned"] is False and body["advice"] is None
    assert body["spoken"] == body["answer"]
    assert "14.00" in body["spoken"]
    assert body["grok_error"]["reason"] == R_MODEL_INVENTED_A_FIGURE
    assert "1400" in body["grok_error"]["detail"]
    assert body["cannot_reason_because"] == body["grok_error"]["detail"]


def test_advice_may_quote_the_figures_it_was_given_and_the_date(
        shop, tmp_path, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    label = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    y, m, d = label.split("-")
    advisor.set_transport(Fake(
        tool_call(TOOL_TAKINGS, {}),
        prose(f"On {int(d)}/{int(m)}/{y}, 1 bill for Rs 14.00, and Rs 14.00 "
              f"of it is awaiting the gateway; Rs 0.00 is settled.")))
    body = say(shop, "aaj kitna hua").json()
    assert body["reasoned"] is True, body["grok_error"]
    assert body["advice"].startswith("On ")


def test_a_percentage_the_model_worked_out_itself_is_refused(shop, tmp_path,
                                                             monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    advisor.set_transport(Fake(tool_call(TOOL_TAKINGS, {}),
                               prose("Rs 14.00 aaj; 100% abhi baaki hai.")))
    body = say(shop, "aaj kitna hua").json()
    assert body["reasoned"] is False
    assert body["grok_error"]["reason"] == R_MODEL_INVENTED_A_FIGURE
    assert "100" in body["grok_error"]["detail"]


def test_a_figure_the_shopkeeper_said_himself_is_allowed_back(shop, tmp_path,
                                                              monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    advisor.set_transport(Fake(tool_call(TOOL_TAKINGS, {}),
                               prose("Nahi, 3 din ka nahi — aaj ka: 1 bill, "
                                     "Rs 14.00.")))
    body = say(shop, "pichle 3 din me kitna hua").json()
    assert body["reasoned"] is True, body["grok_error"]


def test_general_advice_with_no_figure_is_allowed_and_marked_ungrounded(
        shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(prose("Shaam ko dukaan ke bahar ek board "
                                     "lagao, aur regular customers ko naam "
                                     "se bulao.")))
    body = say(shop, "bheed kaise badhau dukaan me").json()
    assert body["ok"] is True, body
    assert body["tool"] is None and body["grounded"] is False
    assert body["reasoned"] is True and body["brain"] == BRAIN_GROK
    assert body["spoken"].startswith("Shaam ko")
    assert body["data"] is None
    assert body["left_the_machine"]["fields"] == []


def test_general_prose_with_a_number_in_it_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(prose("Har hafte 20% off rakho.")))
    body = refusal(say(shop, "bheed kaise badhau dukaan me"),
                   R_MODEL_INVENTED_A_FIGURE)
    assert "20" in body["detail"]
    assert body["brain"] == BRAIN_GROK


def test_prose_for_a_sentence_the_parser_understands_runs_the_tool(
        shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(prose("Sure, let me check that.")))
    body = say(shop, "Maggi ka daam kya hai").json()
    assert body["ok"] is True
    assert body["tool"] == TOOL_PRICE and body["brain"] == BRAIN_LOCAL
    assert body["grok_error"]["reason"] == R_NO_TOOL_CALL
    assert body["reasoned"] is False
    assert body["data"]["price_paise"] == MAGGI[2]


# ------------------------------------------------------------------------
# 6. A dead or misbehaving model is named, never papered over.
# ------------------------------------------------------------------------


def test_an_unreachable_provider_falls_back_to_the_parser_and_says_so(
        shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)

    def dead(url, headers, body, timeout):
        raise assistant.GrokUnavailable(R_GROK_UNREACHABLE, "no route to host")

    advisor.set_transport(dead)
    body = say(shop, "Maggi ka daam kya hai").json()
    assert body["ok"] is True
    assert body["brain"] == BRAIN_LOCAL and body["key_present"] is True
    assert body["grok_error"]["reason"] == R_GROK_UNREACHABLE
    assert body["reasoned"] is False
    assert body["data"]["price_paise"] == MAGGI[2]


def test_a_phrasing_call_that_fails_still_delivers_the_figures(shop,
                                                               monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(tool_call(TOOL_PRICE, {"product": "Maggi"}),
                               (429, {"error": "over quota"})))
    body = say(shop, "Maggi ka daam").json()
    assert body["ok"] is True and body["brain"] == BRAIN_GROK
    assert body["reasoned"] is False and body["advice"] is None
    assert body["grok_error"]["reason"] == R_GROK_HTTP
    assert "14.00" in body["spoken"]
    assert body["cannot_reason_because"]


def test_an_empty_phrasing_answer_speaks_the_counters_own_sentence(shop,
                                                                   monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(tool_call(TOOL_PRICE, {"product": "Maggi"}),
                               prose("")))
    body = say(shop, "Maggi ka daam").json()
    assert body["reasoned"] is False
    assert body["grok_error"]["reason"] == R_ADVICE_EMPTY
    assert body["spoken"] == body["answer"]


def test_a_model_that_reaches_for_add_to_bill_is_refused_by_name(shop,
                                                                monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(tool_call(assistant.TOOL_ADD,
                                         {"product": "Maggi", "qty": 2})))
    body = refusal(say(shop, "do Maggi daal do"), R_UNKNOWN_TOOL)
    assert assistant.TOOL_ADD in body["detail"]


def test_a_model_that_names_a_price_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    advisor.set_transport(Fake(tool_call(TOOL_PRICE, {"product": "Maggi",
                                                      "price_paise": 1})))
    refusal(say(shop, "Maggi ek rupaye ka hai na"), R_MODEL_PRICED)


# ------------------------------------------------------------------------
# 7. The browser is never an author, and nothing is a 500.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["price_paise", "total_paise", "sku_id",
                                 "lines", "amount_paise"])
def test_a_body_that_tries_to_author_a_figure_is_refused(shop, key):
    body = refusal(say(shop, "aaj kitna hua", **{key: 1}), R_CLIENT_AUTHORED)
    assert key in body["detail"]


def test_body_shape_refusals_are_munshis_own(shop):
    r = shop.post("/advisor/say", content=b"not json",
                  headers={"Content-Type": "application/json"})
    refusal(r, R_BAD_BODY)
    refusal(shop.post("/advisor/say", json={}), R_NO_TEXT)
    refusal(say(shop, "   "), R_NO_TEXT)
    refusal(say(shop, "a" * (MAX_TEXT + 1)), R_TEXT_TOO_LONG)
    refusal(say(shop, "aaj kitna hua", source="whatsapp"), R_BAD_SOURCE)


def test_an_unexpected_crash_becomes_a_400_and_never_a_500(shop, monkeypatch):
    def boom(tool, args, brain=BRAIN_LOCAL):
        raise ZeroDivisionError("something nobody predicted")

    monkeypatch.setattr(advisor, "execute", boom)
    r = say(shop, "aaj kitna hua")
    assert r.status_code == 400
    refusal(r, R_INTERNAL)


@pytest.mark.parametrize("body", [
    {"text": "\x00\x01\x02"},
    {"text": "../../etc/passwd"},
    {"text": "<script>alert(1)</script>"},
    {"text": "do " * 120},
    {"text": "😀😀😀"},
    {"text": None},
    {"text": {"a": 1}},
    {"text": "aaj kitna hua", "session_id": {"a": 1}},
    {"text": "aaj kitna hua", "session_id": 7},
    {"say": "Maggi ka daam"},
])
def test_no_input_of_any_shape_produces_a_500(shop, body):
    r = shop.post("/advisor/say", json=body)
    assert r.status_code in (200, 400), r.text
    assert r.json()["settles_money"] is False


def test_the_module_contains_no_payment_primitive_and_no_arithmetic_on_money():
    """Invariants 1 and 6, asserted against this file's own source."""
    src = Path(advisor.__file__).read_text(encoding="utf-8")
    low = src.lower()
    for forbidden in ("upi:", "pa=", "razorpay", "short_url", "payment_link",
                      "vpa", "to_rupees_str", "from_rupees_str"):
        assert forbidden not in low, f"{forbidden!r} is in advisor.py"
    assert "add_to_bill" not in json.dumps(advisor.TOOLS)
