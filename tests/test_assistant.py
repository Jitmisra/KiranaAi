"""gawaah/assistant.py — the shopkeeper's assistant, and what it does not send.

Seven claims, each of which a demo can fake and each of which is asserted here
against running code:

  1. THE SHOP'S DATA NEVER LEAVES THE MACHINE. The request body that would go to
     xAI is built by one function, and the tests below serialise it and assert
     that no sku id, no product name, no price, no order and no customer name is
     anywhere in the bytes. The model is sent one sentence and the tool schemas.
     It gets back a tool NAME; the tool runs here.

  2. NO KEY IS A FIRST-CLASS STATE. Every test in this file runs with
     XAI_API_KEY deleted from the environment, and the assistant answers anyway
     on its own parser. The Grok path is exercised through an INJECTED
     transport; nothing here has ever opened a socket to api.x.ai, and a
     transport that would is installed by default so that an accidental attempt
     fails the test rather than making a call.

  3. THE ASSISTANT PROPOSES; IT DOES NOT BILL, MOVE STOCK OR SPEND. The
     strongest thing it writes is a proposal file with `accepted: false`. For
     the two write-shaped tools the test goes further: it takes the proposal's
     own `accept_by` body, posts it to the REAL endpoint, and asserts both that
     the shelf did not move before that and that it moved after — a piece of
     paper that fails on presentation is worse than no paper.

  4. EVERY REFUSAL HAS A NAME. Each named refusal in the module has a test, and
     no input of any shape produces a 500.

  5. EVERY MODULE IT REACHES IS OPTIONAL. Eleven ways to be half-installed, and
     each of them is asserted to answer with a sentence naming the missing file
     rather than a figure derived from nothing.

  6. THREE LANGUAGES REACH THE SAME ARITHMETIC. The same order in Hinglish,
     Hindi, Bengali, English, Bengali script digits and Devanagari digits is
     asserted to produce the identical sku ids, counts and total — not merely
     to be "understood".

  7. A SENTENCE IS PROPOSED WHOLE OR NOT AT ALL. Two good lines and one that
     resolves to nothing is a refusal naming which line failed, never two
     lines. That was the reason the old blanket refusal existed and it is the
     property that had to survive its removal.
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

from gawaah import assistant  # noqa: E402
from gawaah import storefront  # noqa: E402
from gawaah.assistant import (  # noqa: E402
    BRAIN_GROK,
    BRAIN_LOCAL,
    MAX_QTY,
    MAX_TEXT,
    R_AMBIGUOUS,
    R_BAD_BODY,
    R_BAD_PROPOSAL_ID,
    R_BAD_QTY,
    R_BAD_SOURCE,
    R_BAD_THRESHOLD,
    R_BAD_TOOL_ARGS,
    R_CLIENT_AUTHORED,
    R_EMPTY_CATALOGUE,
    R_GROK_HTTP,
    R_GROK_UNREACHABLE,
    R_INTERNAL,
    R_MODEL_PRICED,
    R_NO_CATALOGUE,
    R_NO_PRODUCT_NAMED,
    R_NO_PROPOSAL,
    R_NO_SUCH_PRODUCT,
    R_NO_TEXT,
    R_NO_TILL,
    R_NO_TOOL_CALL,
    R_NOT_UNDERSTOOD,
    R_ORDERS_UNAVAILABLE,
    R_QTY_TOO_LARGE,
    R_SEVERAL_PRODUCTS,
    R_STOCK_UNAVAILABLE,
    R_TAKINGS_UNAVAILABLE,
    R_TEXT_TOO_LONG,
    R_UNKNOWN_TOOL,
    TOOL_ADD,
    TOOL_FIND,
    TOOL_LOW_STOCK,
    TOOL_ORDERS,
    TOOL_PRICE,
    TOOL_TAKINGS,
    TOOL_NAMES,
    AssistantRefused,
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

    THE CATALOGUE IS REDIRECTED THREE WAYS ON PURPOSE. `set_store_dir` moves the
    till's cached handle, `GAWAAH_SHOP_DIR` covers anything that re-reads the
    environment, and `GAWAAH_DATA_DIR` moves the audit chain — otherwise a
    takings test would read the live one in results/. A harness that honoured
    only one of these once destroyed a real catalogue, and that has no undo.

    XAI_API_KEY IS DELETED. Every test here must pass on a machine that has one
    exported, and the local brain is the default answer.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    monkeypatch.delenv("XAI_MODEL", raising=False)
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    # Restored afterwards: `set_store_dir` mutates a module global that outlives
    # this test, and leaving the till pointed at a deleted temp directory is how
    # one file's fixture becomes another file's mystery failure.
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"890123456789{i}")

    assistant.set_transport(_forbidden_transport)
    app = FastAPI()
    app.include_router(assistant.router)
    client = TestClient(app)
    try:
        yield client
    finally:
        assistant.set_transport(None)
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


@pytest.fixture()
def empty_shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A counter installed this morning: nothing taught, nothing billed."""
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    assistant.set_transport(_forbidden_transport)
    app = FastAPI()
    app.include_router(assistant.router)
    client = TestClient(app)
    try:
        yield client
    finally:
        assistant.set_transport(None)
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


def ask(client: TestClient, text: str, **over):
    body = {"text": text}
    body.update(over)
    return client.post("/assistant/ask", json=body)


def refusal(resp, reason: str) -> dict:
    """Every refusal in this program has the same shape. Assert all of it."""
    assert resp.status_code in (400, 404), resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == reason, body
    assert isinstance(body["detail"], str) and body["detail"].strip()
    assert body["settles_money"] is False
    return body


# ------------------------------------------------------------------------
# 1. THE MODEL IS A ROUTER. What goes out is the sentence and nothing else.
# ------------------------------------------------------------------------


class Fake:
    """A transport that records what would have been sent and answers to order."""

    def __init__(self, answer, status: int = 200):
        self.answer = answer
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "body": body.decode("utf-8"), "timeout": timeout})
        if callable(self.answer):
            return self.answer()
        return self.status, self.answer

    @property
    def sent(self) -> dict:
        return json.loads(self.calls[-1]["body"])


def tool_call(name: str, args) -> dict:
    return {"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": name,
                      "arguments": args if isinstance(args, str)
                      else json.dumps(args)}}]}}]}


def test_payload_carries_the_sentence_and_the_tools_and_nothing_else(shop):
    """The whole request body is two messages and six schemas."""
    body = assistant.payload_for("do Maggi add karo")
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "do Maggi add karo"
    assert len(body["tools"]) == len(TOOL_NAMES)
    assert {t["function"]["name"] for t in body["tools"]} == set(TOOL_NAMES)
    assert set(body) == {"model", "messages", "tools", "tool_choice",
                         "temperature", "stream"}


def test_no_product_price_order_or_customer_is_in_the_outgoing_payload(shop):
    """THE PRIVACY PROPERTY, asserted on the bytes that would leave the machine."""
    storefront._write_order({
        "order_id": "ord_00000000000a", "at": "2026-09-01T09:00:00+00:00",
        "status": "new", "customer": {"name": "Rekha", "phone": "9876543210",
                                      "address": "12 MG Road, near the tank"},
        "lines": [], "total_paise": 9999, "payment": {}})
    raw = json.dumps(assistant.payload_for("do Maggi add karo")).lower()
    for secret in ("parle", "lifebuoy", "amul", "2145", "3950", "2750",
                   "maggi_noodles_70g", "rekha", "9876543210", "mg road",
                   "ord_00000000000a", "9999"):
        assert secret.lower() not in raw, f"{secret!r} leaked into the payload"
    # The one thing that DOES go out is what the shopkeeper said.
    assert "maggi" in raw


def test_the_request_actually_sent_carries_no_shop_data(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    fake = Fake(tool_call(TOOL_ADD, {"product": "Maggi", "qty": 2}))
    assistant.set_transport(fake)
    r = ask(shop, "do Maggi add karo")
    assert r.status_code == 200, r.text
    sent = fake.calls[-1]["body"].lower()
    for secret in ("parle", "lifebuoy", "2145", "1400", "maggi_noodles_70g"):
        assert secret not in sent
    assert fake.sent["messages"][1]["content"] == "do Maggi add karo"


def test_grok_answers_and_the_price_still_comes_from_the_catalogue(shop,
                                                                   monkeypatch):
    """The model named a product; this machine put the number on it."""
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, {"product": "Maggi",
                                                      "qty": 2})))
    body = ask(shop, "do Maggi add karo").json()
    assert body["brain"] == BRAIN_GROK
    assert body["model"] == assistant.XAI_MODEL
    line = body["proposal"]["lines"][0]
    assert line["sku_id"] == MAGGI[0]
    assert line["unit_paise"] == MAGGI[2]
    assert line["line_paise"] == MAGGI[2] * 2


def test_the_key_is_sent_as_a_bearer_header_and_never_returned(shop,
                                                              monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    fake = Fake(tool_call(TOOL_TAKINGS, {}))
    assistant.set_transport(fake)
    r = ask(shop, "aaj kitna hua")
    assert fake.calls[-1]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in r.text
    assert FAKE_KEY not in shop.get("/assistant/health").text
    assert FAKE_KEY not in shop.get("/assistant/tools").text


def test_health_reports_only_whether_a_key_is_present(shop, monkeypatch):
    off = shop.get("/assistant/health").json()
    assert off["key_present"] is False and off["brain"] == BRAIN_LOCAL
    assert off["model"] is None
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    on = shop.get("/assistant/health").json()
    assert on["key_present"] is True and on["brain"] == BRAIN_GROK
    assert "key" not in json.dumps(on).replace("key_present", "")


def test_tools_endpoint_publishes_exactly_what_is_sent(shop):
    body = shop.get("/assistant/tools").json()
    assert body["system_prompt"] == assistant.SYSTEM_PROMPT
    assert body["tools"] == assistant.payload_for("x")["tools"]
    assert body["settles_money"] is False


def test_base_url_and_model_are_configurable(shop, monkeypatch):
    monkeypatch.setenv("XAI_BASE_URL", "https://example.invalid/v9/")
    monkeypatch.setenv("XAI_MODEL", "grok-test-only")
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    fake = Fake(tool_call(TOOL_TAKINGS, {}))
    assistant.set_transport(fake)
    ask(shop, "aaj kitna hua")
    assert fake.calls[-1]["url"] == "https://example.invalid/v9/chat/completions"
    assert fake.sent["model"] == "grok-test-only"


# ------------------------------------------------------------------------
# 2. NO KEY IS A FIRST-CLASS STATE, and a dead network is not an outage.
# ------------------------------------------------------------------------


def test_with_no_key_the_local_brain_answers_and_says_so(shop):
    body = ask(shop, "do Maggi add karo").json()
    assert body["ok"] is True
    assert body["brain"] == BRAIN_LOCAL
    assert body["key_present"] is False
    assert body["grok_error"] is None
    assert body["proposal"]["lines"][0]["sku_id"] == MAGGI[0]


def test_an_http_error_from_the_provider_falls_back_and_names_it(shop,
                                                                 monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake({"error": "over quota"}, status=429))
    body = ask(shop, "do Maggi add karo").json()
    assert body["ok"] is True
    assert body["brain"] == BRAIN_LOCAL
    assert body["grok_error"]["reason"] == R_GROK_HTTP
    assert body["proposal"]["lines"][0]["qty"] == 2


def test_an_unreachable_provider_falls_back_and_names_it(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)

    def dead(url, headers, body, timeout):
        raise assistant.GrokUnavailable(R_GROK_UNREACHABLE, "no route to host")

    assistant.set_transport(dead)
    body = ask(shop, "teen Lifebuoy add karo").json()
    assert body["brain"] == BRAIN_LOCAL
    assert body["grok_error"]["reason"] == R_GROK_UNREACHABLE
    assert body["proposal"]["total_paise"] == SOAP[2] * 3


def test_prose_instead_of_a_tool_call_falls_back(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(
        {"choices": [{"message": {"role": "assistant", "content": "Sure!"}}]}))
    body = ask(shop, "do Maggi add karo").json()
    assert body["brain"] == BRAIN_LOCAL
    assert body["grok_error"]["reason"] == R_NO_TOOL_CALL


@pytest.mark.parametrize("answer", [
    {"not": "a completion"},           # no choices at all
    {"choices": []},                   # choices, but empty
    {"choices": [{"message": {}}]},    # a message with no tool call
    ["a list, not an object"],         # not even a JSON object
])
def test_a_nonsense_shape_from_the_provider_falls_back_and_names_it(
        shop, monkeypatch, answer):
    """Four shapes the provider could return, and none of them is an outage.

    The reason is asserted by NAME so the fallback is diagnosable: "the
    assistant got worse this afternoon" needs an answer on the screen.
    """
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(answer))
    body = ask(shop, "do Maggi add karo").json()
    assert body["ok"] is True and body["brain"] == BRAIN_LOCAL
    assert body["grok_error"]["reason"] in (assistant.R_GROK_SHAPE,
                                            R_NO_TOOL_CALL)
    # And the local parser still got the order right.
    assert body["proposal"]["lines"][0]["qty"] == 2


# ------------------------------------------------------------------------
# 3. A model that breaks the contract is REFUSED, never papered over.
# ------------------------------------------------------------------------


def test_a_model_that_names_a_price_is_refused_by_name(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_ADD, {"product": "Maggi", "qty": 1, "price_paise": 1})))
    body = refusal(ask(shop, "ek Maggi one rupee me add karo"), R_MODEL_PRICED)
    assert body["brain"] == BRAIN_GROK
    assert "price_paise" in body["detail"]


@pytest.mark.parametrize("key", ["total_paise", "amount", "unit_price",
                                 "rupees", "discount"])
def test_every_money_shaped_argument_from_the_model_is_refused(shop,
                                                               monkeypatch, key):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD,
                                           {"product": "Maggi", key: 5})))
    refusal(ask(shop, "ek Maggi"), R_MODEL_PRICED)


def test_a_tool_the_counter_does_not_have_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call("delete_the_catalogue", {})))
    body = refusal(ask(shop, "sab kuch mita do"), R_UNKNOWN_TOOL)
    assert "delete_the_catalogue" in body["detail"]


def test_execute_refuses_an_unknown_tool_directly(shop):
    with pytest.raises(AssistantRefused) as exc:
        assistant.execute("drop_tables", {})
    assert exc.value.reason == R_UNKNOWN_TOOL


def test_arguments_that_are_not_json_are_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, "{product: 'Maggi'")))
    refusal(ask(shop, "ek Maggi"), R_BAD_TOOL_ARGS)


def test_arguments_that_are_not_an_object_are_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, "[1, 2, 3]")))
    refusal(ask(shop, "ek Maggi"), R_BAD_TOOL_ARGS)


def test_a_fractional_quantity_from_the_model_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, {"product": "Maggi",
                                                      "qty": 2.5})))
    body = refusal(ask(shop, "dhai Maggi"), R_BAD_QTY)
    assert "whole number" in body["detail"]


# ------------------------------------------------------------------------
# 4. The local brain: Hinglish in, one tool out, and a refusal when it cannot.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said,tool", [
    ("do kilo doodh add karo", TOOL_ADD),
    ("do Maggi", TOOL_ADD),
    ("Maggi daal do", TOOL_ADD),
    ("paanch packet Maggi bill me daal do", TOOL_ADD),
    ("kitne online orders pending hain?", TOOL_ORDERS),
    ("aaj ki bikri kitni hui", TOOL_TAKINGS),
    ("aaj ka total kitna hua", TOOL_TAKINGS),
    ("Maggi ka daam kya hai", TOOL_PRICE),
    ("Lifebuoy ka rate batao", TOOL_PRICE),
    ("kya kya khatam ho raha hai", TOOL_LOW_STOCK),
    ("kitna stock bacha hai", TOOL_LOW_STOCK),
    ("Lifebuoy hai kya", TOOL_FIND),
])
def test_the_local_parser_routes_these_sentences(shop, said, tool):
    assert assistant.local_route(said)[0] == tool
    body = ask(shop, said).json()
    assert body["tool"] == tool, body


def test_the_count_is_only_read_before_the_product(shop):
    """"Maggi daal do" is one Maggi, not two. "do" there is a verb."""
    assert assistant.local_route("Maggi daal do")[1].get("qty") is None
    assert assistant.local_route("do Maggi")[1]["qty"] == 2
    body = ask(shop, "Maggi daal do").json()
    assert body["proposal"]["lines"][0]["qty"] == 1


def test_daal_is_still_a_food_when_it_is_not_followed_by_a_verb(shop):
    tool, args = assistant.local_route("ek kilo daal do")
    assert tool == TOOL_ADD and args["product"] == "daal"


def test_a_weight_unit_is_carried_through_as_a_stated_caution(shop):
    body = ask(shop, "do kilo doodh add karo").json()
    prop = body["proposal"]
    assert prop["lines"][0]["sku_id"] == MILK[0]
    assert prop["lines"][0]["qty"] == 2
    assert "kilo" in prop["caution"] and "packets" in prop["caution"]
    assert prop["total_paise"] == MILK[2] * 2


def test_a_dozen_is_multiplied_and_the_multiplication_is_said_out_loud(shop):
    body = ask(shop, "ek dozen Maggi add karo").json()
    prop = body["proposal"]
    assert prop["lines"][0]["qty"] == 12
    assert "12 packets" in prop["caution"]
    assert prop["total_paise"] == MAGGI[2] * 12


def test_half_a_kilo_is_refused_rather_than_rounded(shop):
    body = refusal(ask(shop, "aadha kilo doodh add karo"), R_BAD_QTY)
    assert "whole packets" in body["detail"]


def test_two_products_in_one_sentence_become_two_lines_not_one(shop):
    """The old refusal is gone; what replaced it must not lose a line.

    This is the change the refusal was protecting against, so it is asserted on
    the arithmetic and not on the prose: two lines, both skus, and a total that
    is the sum of both rather than the first one.
    """
    body = ask(shop, "do Maggi aur ek Lifebuoy add karo").json()
    assert body["ok"] is True and body["tool"] == TOOL_ADD
    prop = body["proposal"]
    assert prop["kind"] == "bill"
    assert [ln["sku_id"] for ln in prop["lines"]] == [MAGGI[0], SOAP[0]]
    assert [ln["qty"] for ln in prop["lines"]] == [2, 1]
    assert prop["total_paise"] == MAGGI[2] * 2 + SOAP[2]
    assert prop["accepted"] is False


def test_a_sentence_the_parser_cannot_read_is_refused_by_name(shop):
    body = refusal(ask(shop, "please karo ji"), R_NOT_UNDERSTOOD)
    assert "XAI_API_KEY" in body["detail"]


def test_a_price_question_with_no_product_is_refused(shop):
    refusal(ask(shop, "daam kya hai"), R_NO_PRODUCT_NAMED)


def test_a_product_this_shop_does_not_sell_is_refused_with_what_it_does(shop):
    body = refusal(ask(shop, "do Bournvita add karo"), R_NO_SUCH_PRODUCT)
    assert "Maggi Noodles 70g" in body["detail"]


def test_an_ambiguous_name_is_refused_with_both_candidates(shop):
    upload_app.do_enrol_code_only(b"", "maggi_atta_noodles_75g",
                                  "Maggi Atta Noodles 75g", 1600,
                                  typed="8901234567899")
    body = refusal(ask(shop, "do Maggi add karo"), R_AMBIGUOUS)
    assert "maggi_noodles_70g" in body["detail"]
    assert "maggi_atta_noodles_75g" in body["detail"]


def test_a_hinglish_word_reaches_an_english_catalogue(shop):
    body = ask(shop, "doodh ka daam kya hai").json()
    assert body["data"]["sku_id"] == MILK[0]
    assert body["data"]["price_paise"] == MILK[2]


def test_a_quantity_past_the_cap_is_refused(shop):
    body = refusal(ask(shop, f"{MAX_QTY + 1} Maggi add karo"), R_QTY_TOO_LARGE)
    assert str(MAX_QTY) in body["detail"]


def test_nothing_taught_yet_is_its_own_refusal(empty_shop):
    refusal(ask(empty_shop, "do Maggi add karo"), R_EMPTY_CATALOGUE)


# ------------------------------------------------------------------------
# 5. A proposal is written down. A bill is not.
# ------------------------------------------------------------------------


def test_a_proposal_is_stored_unaccepted_and_can_be_read_back(shop):
    body = ask(shop, "teen Parle-G add karo").json()
    prop = body["proposal"]
    assert prop["accepted"] is False
    assert prop["total_paise"] == BISCUIT[2] * 3
    again = shop.get(f"/assistant/proposal/{prop['proposal_id']}")
    assert again.status_code == 200
    assert again.json()["proposal"]["total_paise"] == BISCUIT[2] * 3
    assert again.json()["settles_money"] is False


def test_a_proposal_lands_on_its_own_hash_chain_under_the_shop_dir(shop):
    body = ask(shop, "do Maggi add karo").json()
    assert body["proposal"]["audited"] is True
    path = assistant.audit_path()
    assert path.parent == assistant.shop_dir()
    assert path.name != "audit.jsonl"
    ok, n, _head, err = verify(path)
    assert ok and n == 1, err
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["event"] == "assistant.proposed"
    assert line["total_paise"] == MAGGI[2] * 2
    assert line["minted"] is False
    # The shopkeeper's own words are NOT in the chain.
    assert "maggi add karo" not in json.dumps(line).lower()


def test_the_assistant_never_writes_to_the_money_chain(shop, tmp_path):
    money_chain = Path(str(tmp_path / "data")) / "audit.jsonl"
    ask(shop, "do Maggi add karo")
    ask(shop, "ek Lifebuoy add karo")
    assert not money_chain.exists()


def test_a_malformed_proposal_id_is_refused_before_it_touches_a_path(shop):
    body = refusal(shop.get("/assistant/proposal/prop_zzzzzzzzzzzz"),
                   R_BAD_PROPOSAL_ID)
    assert "prop_" in body["detail"]


@pytest.mark.parametrize("nasty", ["../../catalog", "..", "/etc/passwd",
                                   "prop_../../x", "appearance_only"])
def test_a_traversal_never_reaches_the_filesystem(shop, nasty):
    """The shape check runs BEFORE the id is joined to a path."""
    with pytest.raises(AssistantRefused) as exc:
        assistant.read_proposal(nasty)
    assert exc.value.reason == R_BAD_PROPOSAL_ID


def test_an_unknown_proposal_is_a_404_with_a_name(shop):
    r = shop.get("/assistant/proposal/prop_0123456789ab")
    assert r.status_code == 404
    refusal(r, R_NO_PROPOSAL)


def test_every_money_field_in_a_proposal_is_an_integer(shop):
    prop = ask(shop, "teen Lifebuoy add karo").json()["proposal"]
    for key in ("total_paise",):
        assert isinstance(prop[key], int) and not isinstance(prop[key], bool)
    for line in prop["lines"]:
        for key in ("unit_paise", "line_paise", "qty"):
            assert isinstance(line[key], int)
    assert prop["total_rupees"] == "118.50"


def test_the_response_says_it_settles_no_money(shop):
    for r in (shop.get("/assistant/health"), shop.get("/assistant/tools"),
              ask(shop, "do Maggi add karo"), ask(shop, "aaj kitna hua")):
        assert r.json()["settles_money"] is False


# ------------------------------------------------------------------------
# 6. The browser is never an author.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["price_paise", "total_paise", "sku_id",
                                 "lines", "items", "amount_paise",
                                 "proposal_id"])
def test_a_body_that_tries_to_author_the_bill_is_refused(shop, key):
    body = refusal(ask(shop, "do Maggi add karo", **{key: 1}),
                   R_CLIENT_AUTHORED)
    assert key in body["detail"]


def test_a_body_that_is_not_json_is_refused(shop):
    r = shop.post("/assistant/ask", content=b"not json",
                  headers={"Content-Type": "application/json"})
    refusal(r, R_BAD_BODY)


def test_a_body_that_is_not_an_object_is_refused(shop):
    refusal(shop.post("/assistant/ask", json=["do Maggi"]), R_BAD_BODY)


def test_text_that_is_not_a_string_is_refused(shop):
    refusal(shop.post("/assistant/ask", json={"text": 12}), R_BAD_BODY)


def test_no_text_at_all_is_refused(shop):
    refusal(shop.post("/assistant/ask", json={}), R_NO_TEXT)


def test_an_empty_sentence_is_refused(shop):
    refusal(ask(shop, "   "), R_NO_TEXT)


def test_a_sentence_past_the_cap_is_refused(shop):
    body = refusal(ask(shop, "a" * (MAX_TEXT + 1)), R_TEXT_TOO_LONG)
    assert str(MAX_TEXT) in body["detail"]


def test_a_source_that_is_neither_voice_nor_text_is_refused(shop):
    refusal(ask(shop, "do Maggi", source="whatsapp"), R_BAD_SOURCE)


def test_voice_and_text_land_on_exactly_the_same_answer(shop):
    typed = ask(shop, "do Maggi add karo", source="text").json()
    spoken = ask(shop, "do Maggi add karo", source="voice").json()
    assert typed["tool"] == spoken["tool"]
    assert typed["proposal"]["total_paise"] == spoken["proposal"]["total_paise"]
    assert spoken["source"] == "voice"
    # Two proposals, because each is a separate thing a person may accept.
    assert typed["proposal"]["proposal_id"] != spoken["proposal"]["proposal_id"]


# ------------------------------------------------------------------------
# 7. Questions answered from this counter's own files.
# ------------------------------------------------------------------------


def _order(order_id: str, status: str, total_paise: int) -> dict:
    return {"format": 1, "order_id": order_id,
            "at": f"2026-09-01T0{len(order_id) % 9}:00:00+00:00",
            "status": status, "status_changed_at": "2026-09-01T09:00:00+00:00",
            "customer": {"name": "Rekha", "phone": "9876543210",
                         "address": "12 MG Road, near the water tank"},
            "lines": [{"sku_id": MAGGI[0], "qty": 1}],
            "total_paise": total_paise,
            "payment": {"session_id": f"shop_{order_id}", "paid": False}}


def test_pending_orders_counts_only_the_open_ones(shop):
    storefront._write_order(_order("ord_0000000000a1", "new", 1400))
    storefront._write_order(_order("ord_0000000000a2", "preparing", 2145))
    storefront._write_order(_order("ord_0000000000a3", "delivered", 3950))
    storefront._write_order(_order("ord_0000000000a4", "cancelled", 100))
    data = ask(shop, "kitne online orders pending hain?").json()["data"]
    assert data["pending"] == 2
    assert data["total_paise"] == 1400 + 2145
    assert data["counts"] == {"new": 1, "preparing": 1}


def test_no_pending_orders_says_so_rather_than_showing_a_zero_row(shop):
    body = ask(shop, "kitne orders pending hain").json()
    assert body["data"]["pending"] == 0
    assert "No online orders are open" in body["answer"]


def test_orders_that_cannot_be_read_are_a_named_refusal(shop, monkeypatch):
    def boom():
        raise OSError("the orders directory is gone")

    monkeypatch.setattr(storefront, "_all_orders", boom)
    refusal(ask(shop, "kitne orders pending hain"), R_ORDERS_UNAVAILABLE)


def _chain_one_sale(tmp_data: Path, sku: str, price_paise: int) -> None:
    """One closed, settled bill on this counter's own audit chain."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    led = Ledger(tmp_data / "audit.jsonl")
    led.append(ts=now, module="session", event="exit", session_id="s1",
               item_id=f"{sku}#0", price_paise=price_paise,
               reason="exit_crossing_committed")
    led.append(ts=now, module="session", event="done", session_id="s1",
               total_paise=price_paise, lines=1, reason="intent_requested")


def test_todays_takings_are_counted_off_the_chain(shop, tmp_path):
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    body = ask(shop, "aaj ki bikri kitni hui").json()
    assert body["data"]["bills"] == 1
    assert body["data"]["revenue_paise"] == MAGGI[2]
    assert body["data"]["settled_paise"] == 0
    assert "14.00" in body["answer"]


def test_an_empty_chain_says_nothing_was_billed_rather_than_zero_rupees(shop):
    body = ask(shop, "aaj kitna kamaya").json()
    assert body["data"]["bills"] == 0
    assert "Nothing has been billed" in body["answer"]


def test_takings_never_ask_the_money_service_anything(shop, tmp_path,
                                                      monkeypatch):
    """A question about a day must not wait on a network."""
    from gawaah import manage

    def forbidden(path):
        raise AssertionError("takings reached for the money service")

    monkeypatch.setattr(manage, "paisa_get", forbidden)
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    assert ask(shop, "aaj ki bikri").status_code == 200


def test_takings_are_a_named_refusal_when_the_derivation_is_missing(shop,
                                                                    monkeypatch):
    from gawaah import manage

    monkeypatch.delattr(manage, "_brief_for")
    body = refusal(ask(shop, "aaj kitna hua"), R_TAKINGS_UNAVAILABLE)
    assert "_brief_for" in body["detail"]


def test_low_stock_uses_the_shopkeepers_own_count_and_says_what_it_cannot_see(
        shop, tmp_path):
    from gawaah import manage

    manage.write_opening_stock({MAGGI[0]: {"units": 3,
                                           "counted_at": "2026-01-01T00:00:00+00:00"}})
    _chain_one_sale(tmp_path / "data", MAGGI[0], MAGGI[2])
    body = ask(shop, "kya khatam ho raha hai").json()
    data = body["data"]
    assert data["threshold_units"] == 3
    assert data["low"][0]["sku_id"] == MAGGI[0]
    assert data["low"][0]["remaining_units"] == 2
    assert data["uncounted"] == len(CATALOGUE) - 1
    assert "never been counted" in body["answer"]
    assert "no stock sensor" in body["answer"]


def test_nothing_counted_is_not_reported_as_nothing_left(shop):
    body = ask(shop, "kitna stock bacha hai").json()
    assert body["data"]["low"] == []
    assert body["data"]["uncounted"] == len(CATALOGUE)


def test_a_stock_threshold_that_is_not_a_whole_number_is_refused(shop,
                                                                 monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_LOW_STOCK, {"units": "a few"})))
    refusal(ask(shop, "kya kam hai"), R_BAD_THRESHOLD)


def test_an_absurd_stock_threshold_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_LOW_STOCK, {"units": 10 ** 9})))
    refusal(ask(shop, "kya kam hai"), R_BAD_THRESHOLD)


def test_stock_that_cannot_be_read_is_a_named_refusal(shop, monkeypatch):
    from gawaah import manage

    def boom():
        raise OSError("the counts file is unreadable")

    monkeypatch.setattr(manage, "_inventory_rows", boom)
    refusal(ask(shop, "kya khatam ho raha hai"), R_STOCK_UNAVAILABLE)


def test_the_price_answer_shows_an_offer_against_the_shelf_price(shop,
                                                                 monkeypatch):
    """The number quoted is the number paisa will charge, not the shelf edge."""
    def discounted():
        rows = dict(upload_app.priced_skus())
        rows[MAGGI[0]] = {**rows[MAGGI[0]], "price_paise": 1200,
                          "marked_paise": MAGGI[2], "off_paise": 200}
        return rows

    monkeypatch.setattr(upload_app, "offer_priced_skus", discounted)
    body = ask(shop, "Maggi ka daam kya hai").json()
    assert body["data"]["price_paise"] == 1200
    assert body["data"]["marked_paise"] == MAGGI[2]
    assert "offer price" in body["answer"]


def test_find_product_reports_how_it_was_taught(shop):
    body = ask(shop, "Lifebuoy hai kya").json()
    assert body["data"]["sku_id"] == SOAP[0]
    assert body["data"]["taught_with"] == "product_code_only"


# ------------------------------------------------------------------------
# 8. Nothing raises a 500, and there is no forgery primitive anywhere.
# ------------------------------------------------------------------------


def test_a_missing_till_is_a_named_refusal_not_a_crash(shop, monkeypatch):
    def no_till():
        raise AssistantRefused(R_NO_TILL, "tools/upload_app.py is not here")

    monkeypatch.setattr(assistant, "_till", no_till)
    refusal(ask(shop, "Maggi ka daam kya hai"), R_NO_TILL)
    assert shop.get("/assistant/health").json()["catalogue_problem"]["reason"] \
        == R_NO_TILL


def test_an_unreadable_catalogue_is_a_named_refusal(shop, monkeypatch):
    def boom():
        raise OSError("appearance_only.json is a directory")

    monkeypatch.setattr(upload_app, "offer_priced_skus", boom)
    refusal(ask(shop, "Maggi ka daam kya hai"), R_NO_CATALOGUE)


def test_an_unexpected_crash_becomes_a_400_and_never_a_500(shop, monkeypatch):
    def boom(tool, args, brain=BRAIN_LOCAL):
        raise ZeroDivisionError("something nobody predicted")

    monkeypatch.setattr(assistant, "execute", boom)
    r = ask(shop, "do Maggi add karo")
    assert r.status_code == 400
    refusal(r, R_INTERNAL)


@pytest.mark.parametrize("body", [
    {"text": "\x00\x01\x02"},
    {"text": "../../etc/passwd"},
    {"text": "<script>alert(1)</script>"},
    {"text": "'; DROP TABLE skus; --"},
    {"text": "do " * 120},
    {"text": "😀😀😀"},
    {"text": None},
    {"text": {"a": 1}},
    {"say": "do Maggi add karo"},
])
def test_no_input_of_any_shape_produces_a_500(shop, body):
    r = shop.post("/assistant/ask", json=body)
    assert r.status_code in (200, 400), r.text
    assert r.json()["settles_money"] is False


def test_the_module_contains_no_payment_primitive():
    """INVARIANT 6, asserted against this file's own source."""
    src = Path(assistant.__file__).read_text(encoding="utf-8")
    for forbidden in ("upi:", "pa=", "razorpay", "short_url", "payment_link",
                      "vpa"):
        assert forbidden not in src.lower(), f"{forbidden!r} is in assistant.py"


def test_the_only_money_argument_in_the_whole_tool_list_is_a_stated_expense():
    """No route, no tool and no argument can supply a PRICE.

    Asserted structurally over every schema, so a tool added later cannot
    quietly open a second door. Exactly one argument in the list is
    money-shaped: the rupee figure on `propose_expense`, which is a number the
    shopkeeper said out loud about money he already spent — not a price this
    counter charges anybody. Everything else resolves to a sku here and is
    priced from the catalogue here.
    """
    assert assistant.money_shaped_arguments() == {
        assistant.TOOL_PROPOSE_EXPENSE: frozenset({"amount_rupees"})}


def test_that_one_money_argument_is_text_and_is_never_a_float(shop):
    """It arrives as a STRING and is parsed by money.from_rupees_str.

    A schema that said "number" would put 120.50 through JSON as a float before
    this module ever saw it, and 0.1 + 0.2 != 0.3 is the reason invariant 1
    exists at all.
    """
    schema = next(t for t in assistant.TOOLS
                  if t["function"]["name"] == assistant.TOOL_PROPOSE_EXPENSE)
    field = schema["function"]["parameters"]["properties"]["amount_rupees"]
    assert field["type"] == "string"
    body = ask(shop, "chai ka kharcha 120.50 rupaye likho").json()
    assert body["proposal"]["expense"]["amount_paise"] == 12050
    assert body["proposal"]["expense"]["amount_rupees"] == "120.50"


def test_every_declared_tool_has_something_that_runs_it():
    """A tool the model can name and this counter cannot run is a dead end that
    only shows up in front of a shopkeeper."""
    assert set(assistant._EXECUTORS) == set(TOOL_NAMES)
    assert len(TOOL_NAMES) == len(set(TOOL_NAMES)) == len(assistant.TOOLS)


# ========================================================================
# 9. THE WIDER SHOP. Every module that landed is reachable through a tool,
#    every one of them is optional, and a missing one is a sentence.
# ========================================================================
#
# The tests below do NOT re-derive any figure. Where a number is asserted it is
# asserted against what the owning module's own endpoint returns, because the
# claim being made is "the assistant says what the screen says" — and a test
# that recomputed the number would pass even if the two had drifted apart.

from gawaah import categories as _categories  # noqa: E402
from gawaah import expenses as _expenses  # noqa: E402
from gawaah import stock as _stock  # noqa: E402
from gawaah import weighed as _weighed  # noqa: E402
from gawaah.assistant import (  # noqa: E402
    KIND_BILL,
    KIND_EXPENSE,
    KIND_MOVEMENT,
    MAX_LINES,
    R_AMOUNT_TOO_LARGE,
    R_BAD_AMOUNT,
    R_BAD_DAYS,
    R_BAD_DIRECTION,
    R_BAD_EXPENSE_CATEGORY,
    R_BAD_MOVEMENT_REASON,
    R_BAD_PHONE,
    R_CATEGORIES_UNAVAILABLE,
    R_CUSTOMERS_UNAVAILABLE,
    R_DAYBOOK_UNAVAILABLE,
    R_EXPENSES_UNAVAILABLE,
    R_EXPIRY_UNAVAILABLE,
    R_GST_UNAVAILABLE,
    R_LOYALTY_UNAVAILABLE,
    R_NO_AMOUNT,
    R_NO_CATEGORY_NAMED,
    R_NO_PHONE,
    R_NO_SUCH_CATEGORY,
    R_NO_WEIGHT,
    R_NOT_WEIGHED,
    R_NOTE_TOO_LONG,
    R_OFFERS_UNAVAILABLE,
    R_PURCHASES_UNAVAILABLE,
    R_SPOKEN_PRICE,
    R_STOCK_MODULE,
    R_TOO_MANY_LINES,
    R_WEIGHED_UNAVAILABLE,
    TOOL_CASH_POSITION,
    TOOL_CATEGORIES,
    TOOL_CUSTOMER,
    TOOL_DAY_CLOSE,
    TOOL_EXPENSES_TODAY,
    TOOL_EXPIRED,
    TOOL_EXPIRING,
    TOOL_GST_OF,
    TOOL_IN_CATEGORY,
    TOOL_LOYALTY,
    TOOL_LOYALTY_RULES,
    TOOL_MARGIN_OF,
    TOOL_MARGIN_TODAY,
    TOOL_OFFERS,
    TOOL_PROPOSE_EXPENSE,
    TOOL_PROPOSE_MOVEMENT,
    TOOL_REGULARS,
    TOOL_REORDER_LIST,
    TOOL_STOCK_MOVEMENTS,
    TOOL_STOCK_ON_HAND,
    TOOL_SUPPLIERS,
    TOOL_WEIGHED,
)

#: Loose rice, priced by the kilo. Not a round number per kilo on purpose: a
#: bug that divides shows up in the second decimal place or not at all.
RICE = ("basmati_rice_1kg", "Basmati Rice 1kg", 9900)
RICE_PER_KG = 9900


@pytest.fixture()
def big_shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The `shop` fixture plus loose rice, and every other router mounted.

    THE OTHER ROUTERS ARE MOUNTED ON PURPOSE. A proposal is only worth anything
    if the module that has to carry it out would accept it, and the only way to
    assert that is to hand the proposal's own `accept_by` body to the real
    endpoint and watch it succeed.
    """
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(tmp_path / "shop"))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path / "data"))
    for var in ("XAI_API_KEY", "XAI_BASE_URL", "XAI_MODEL",
                "GAWAAH_WEIGHED_FILE", "GAWAAH_CATEGORIES_FILE"):
        monkeypatch.delenv(var, raising=False)
    was = upload_app._DEPS.get("store_dir")
    upload_app.set_store_dir(tmp_path / "shop")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    for i, (sku, name, price) in enumerate(CATALOGUE + (RICE,)):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345678{i}0")
    _weighed.save_weighed({RICE[0]: _weighed.WeighedSku(
        sku_id=RICE[0], price_per_kg_paise=RICE_PER_KG,
        since="2026-09-01T00:00:00+00:00")})

    assistant.set_transport(_forbidden_transport)
    app = FastAPI()
    app.include_router(assistant.router)
    app.include_router(_stock.router)
    app.include_router(_expenses.router)
    client = TestClient(app)
    try:
        yield client
    finally:
        assistant.set_transport(None)
        _weighed.set_weighed_path(None)
        _categories.set_categories_path(None)
        upload_app._DEPS["store_dir"] = was
        upload_app._DEPS["store"] = None


def gone(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make one gawaah module unimportable, the way a rename would.

    `None` in sys.modules is what Python itself uses to mark a failed import,
    so `import gawaah.stock` raises exactly as it would if the file were not
    there — rather than a mock that only resembles absence.
    """
    monkeypatch.setitem(sys.modules, f"gawaah.{name}", None)


# ------------------------------------------------------------------------
# 9a. The tool list IS the capability, and every module in it is optional.
# ------------------------------------------------------------------------


def test_every_module_that_landed_is_reachable_through_a_tool():
    """The claim "the assistant reaches everything" as a set comparison."""
    assert set(assistant._MODULES) == {
        "stock", "expenses", "purchases", "customers", "categories",
        "daybook", "offers", "gst", "expiry", "loyalty", "weighed", "khata",
        "milan"}
    assert len(TOOL_NAMES) >= 25, TOOL_NAMES


def test_health_says_which_modules_are_actually_there(big_shop):
    body = big_shop.get("/assistant/health").json()
    assert body["modules_reachable"] == len(assistant._MODULES)
    for alias, block in body["modules"].items():
        assert block["there"] is True, alias
        assert block["refusal"] is None
        assert block["owns"] and block["file"].startswith("gawaah/")


def test_health_names_a_module_that_has_gone_rather_than_hiding_it(
        big_shop, monkeypatch):
    gone(monkeypatch, "expiry")
    body = big_shop.get("/assistant/health").json()
    assert body["modules"]["expiry"]["there"] is False
    assert body["modules"]["expiry"]["refusal"]["reason"] == R_EXPIRY_UNAVAILABLE
    assert body["modules_reachable"] == len(assistant._MODULES) - 1


@pytest.mark.parametrize("said,module,reason", [
    ("Maggi ka stock kitna hai", "stock", R_STOCK_MODULE),
    ("aaj ka kharcha kitna hua", "expenses", R_EXPENSES_UNAVAILABLE),
    ("Maggi pe kitna munafa hai", "purchases", R_PURCHASES_UNAVAILABLE),
    ("regular customers kaun hai", "customers", R_CUSTOMERS_UNAVAILABLE),
    ("categories dikhao", "categories", R_CATEGORIES_UNAVAILABLE),
    ("aaj ka hisab dikhao", "daybook", R_DAYBOOK_UNAVAILABLE),
    ("kya offer chal raha hai", "offers", R_OFFERS_UNAVAILABLE),
    ("Maggi pe gst kitna hai", "gst", R_GST_UNAVAILABLE),
    ("kya expire ho raha hai", "expiry", R_EXPIRY_UNAVAILABLE),
    ("loyalty rules batao", "loyalty", R_LOYALTY_UNAVAILABLE),
])
def test_a_module_that_has_moved_is_a_sentence_and_never_a_wrong_number(
        big_shop, monkeypatch, said, module, reason):
    """ELEVEN WAYS TO BE HALF-INSTALLED, and none of them invents a figure."""
    gone(monkeypatch, module)
    body = refusal(ask(big_shop, said), reason)
    assert f"gawaah/{module}.py" in body["detail"]
    for lie in ("0", "zero", "nothing to worry"):
        assert not body["detail"].lower().startswith(lie)


def test_a_missing_weighed_module_means_nothing_is_sold_loose_not_an_error(
        big_shop, monkeypatch):
    """Absent is the state every shop starts in, so it is not a failure.

    But a sentence that ASKS for a weight still gets a named refusal, because
    "aadha kilo chawal" cannot be answered in packets and silence would be a
    guess.
    """
    gone(monkeypatch, "weighed")
    body = ask(big_shop, "do Maggi add karo").json()
    assert body["ok"] is True and body["proposal"]["lines"][0]["qty"] == 2
    refusal(ask(big_shop, "aadha kilo chawal"), R_BAD_QTY)


def test_with_the_weighed_module_gone_nothing_is_loose_and_it_says_so(
        big_shop, monkeypatch):
    """The honest answer is "nothing here is sold by weight", not an error.

    A shop with no weighed file and a shop with no weighed module are in the
    same state as far as a shopkeeper is concerned, and that state has a
    sentence: mark it by the kilo, or say how many packets.
    """
    gone(monkeypatch, "weighed")
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_WEIGHED, {"product": "rice", "unit": "kilo"})
    assert caught.value.reason == R_NOT_WEIGHED
    assert "packets" in caught.value.detail


def test_a_weighed_module_that_has_been_refactored_is_named_not_worked_around(
        big_shop, monkeypatch):
    """Present but missing the function this file reaches for. That is the
    failure that otherwise produces a wrong number instead of an error."""
    monkeypatch.delattr(_weighed, "grams_for")
    body = refusal(ask(big_shop, "aadha kilo chawal"), R_WEIGHED_UNAVAILABLE)
    assert "grams_for" in body["detail"]
    assert "gawaah/weighed.py" in body["detail"]


# ------------------------------------------------------------------------
# 9b. Three languages, mixed, in Latin script and in Bengali digits.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said", [
    "do Maggi aur ek Lifebuoy add karo",        # Hinglish
    "dui ta Maggi ar ekta Lifebuoy dao",        # Bengali
    "2 Maggi and 1 Lifebuoy",                   # English
    "dui Maggi aur ek Lifebuoy",                # mixed in one breath
    "২ Maggi ar ১ Lifebuoy",                    # Bengali script digits
    "२ Maggi aur १ Lifebuoy",                   # Devanagari digits
])
def test_the_same_order_in_any_language_reaches_the_same_two_lines(shop, said):
    """THE HEADLINE CLAIM, asserted six ways on the same arithmetic.

    Not "the parser understood something" — the identical sku ids, the
    identical counts and the identical total, whichever language it arrived in.
    """
    body = ask(shop, said).json()
    assert body["ok"] is True, body
    prop = body["proposal"]
    assert [(ln["sku_id"], ln["qty"]) for ln in prop["lines"]] == [
        (MAGGI[0], 2), (SOAP[0], 1)]
    assert prop["total_paise"] == MAGGI[2] * 2 + SOAP[2]


@pytest.mark.parametrize("said,qty", [
    ("ek Maggi", 1), ("ekta Maggi", 1),
    ("dui Maggi", 2), ("duto Maggi", 2), ("do Maggi", 2),
    ("tin Maggi", 3), ("tinte Maggi", 3), ("teen Maggi", 3),
    ("char Maggi", 4), ("panch Maggi", 5),
    ("chhoy Maggi", 6), ("choy Maggi", 6), ("che Maggi", 6),
    ("sat Maggi", 7), ("at Maggi", 8), ("aat Maggi", 8),
    ("noy Maggi", 9), ("nau Maggi", 9),
    ("dosh Maggi", 10), ("das Maggi", 10),
])
def test_bengali_and_hindi_numerals_reach_the_same_count(shop, said, qty):
    assert assistant.local_route(said)[1]["qty"] == qty
    assert ask(shop, said).json()["proposal"]["lines"][0]["qty"] == qty


@pytest.mark.parametrize("digits,qty", [("৫", 5), ("५", 5), ("5", 5),
                                        ("১২", 12), ("१२", 12)])
def test_script_digits_are_transliterated_and_never_dropped(shop, digits, qty):
    """A dropped digit is the silent kind of wrong. "২৫০ gram" tokenising to
    ["gram"] would price one gram of rice and say nothing about it."""
    assert assistant.normalise(f"{digits} Maggi") == [str(qty), "maggi"]
    assert ask(shop, f"{digits} Maggi").json()["proposal"]["lines"][0]["qty"] \
        == qty


@pytest.mark.parametrize("said", ["dudh", "doodh", "milk", "dugdha"])
def test_a_bengali_or_hindi_word_reaches_an_english_catalogue(shop, said):
    tool, args = assistant.local_route(f"do {said} add karo")
    assert tool == TOOL_ADD
    body = ask(shop, f"do {said} add karo").json()
    assert body["proposal"]["lines"][0]["sku_id"] == MILK[0]


@pytest.mark.parametrize("said,tool", [
    ("aajke koto bikri hoyeche", TOOL_TAKINGS),
    ("Maggi r stock koto ache", TOOL_STOCK_ON_HAND),
    ("Maggi r dam koto", TOOL_PRICE),
    ("khoroch koto holo", TOOL_EXPENSES_TODAY),
    ("kon kon jinis sesh hoye jacche", TOOL_LOW_STOCK),
])
def test_a_bengali_question_reaches_the_same_tool_as_the_hindi_one(shop, said,
                                                                   tool):
    assert assistant.local_route(said)[0] == tool


def test_a_bengali_counting_particle_does_not_break_the_product_name(shop):
    """"Maggi ta" and "duto Maggi" have to find Maggi. A particle left on the
    phrase would make it match nothing and refuse a sentence somebody said."""
    assert assistant.local_route("duto Maggi ta dao")[1]["product"] == "maggi"
    assert ask(shop, "duto Maggi ta dao").json()[
        "proposal"]["lines"][0]["sku_id"] == MAGGI[0]


def test_the_bare_bengali_o_is_not_a_conjunction_and_that_is_stated(shop):
    """A one-letter conjunction would eat the tail of "Nestle-O". The cost is
    that "dudh o chini" is one unmatched phrase, refused by name — which is
    what this asserts, so the limit cannot be discovered on a bill."""
    assert "o" not in assistant.CONJUNCTIONS
    assert assistant.normalise("Nestle-O") == ["nestle", "o"]
    refusal(ask(shop, "do dudh o chini add karo"), R_NO_SUCH_PRODUCT)


# ------------------------------------------------------------------------
# 9c. Multi-line proposals: all of it, or none of it.
# ------------------------------------------------------------------------


def test_a_line_that_cannot_be_resolved_refuses_the_whole_proposal(shop):
    """THE RULE THE OLD REFUSAL EXISTED TO PROTECT. Two good lines and one bad
    one is not two lines — it is a refusal that says which one failed."""
    body = refusal(ask(shop, "do Maggi aur ek Bournvita aur teen Parle-G "
                             "add karo"), R_NO_SUCH_PRODUCT)
    assert "bournvita" in body["detail"].lower()
    assert "line 2 of 3" in body["detail"]
    assert "NONE of it" in body["detail"]


def test_the_same_product_twice_becomes_one_line_and_says_so(shop):
    body = ask(shop, "do Maggi aur teen Maggi add karo").json()
    prop = body["proposal"]
    assert len(prop["lines"]) == 1
    assert prop["lines"][0]["qty"] == 5
    assert prop["total_paise"] == MAGGI[2] * 5
    assert "more than once" in prop["caution"]


def test_merging_two_counts_past_the_cap_is_refused_not_clamped(shop):
    refusal(ask(shop, "pachas Maggi aur pachas Maggi add karo"),
            R_QTY_TOO_LARGE)


def test_more_lines_than_the_counter_proposes_is_refused_by_name(shop):
    said = " aur ".join(["ek Maggi"] * (MAX_LINES + 1))
    body = refusal(ask(shop, said), R_TOO_MANY_LINES)
    assert str(MAX_LINES) in body["detail"]


def test_the_model_may_send_items_as_a_list_of_bare_strings(shop, monkeypatch):
    """It is what models actually emit, and refusing it would lose a sentence
    the shopkeeper said perfectly well."""
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD,
                                           {"items": ["Maggi", "Lifebuoy"]})))
    prop = ask(shop, "Maggi aur Lifebuoy").json()["proposal"]
    assert [ln["sku_id"] for ln in prop["lines"]] == [MAGGI[0], SOAP[0]]
    assert [ln["qty"] for ln in prop["lines"]] == [1, 1]


def test_items_that_are_not_a_list_are_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, {"items": "Maggi"})))
    refusal(ask(shop, "Maggi"), R_BAD_TOOL_ARGS)


def test_an_item_that_names_nothing_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, {"items": [7]})))
    refusal(ask(shop, "Maggi"), R_BAD_TOOL_ARGS)


def test_add_with_no_product_at_all_is_refused(shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(TOOL_ADD, {})))
    refusal(ask(shop, "add karo"), R_NO_PRODUCT_NAMED)


def test_a_question_about_two_products_is_still_refused_by_its_old_name(shop):
    """The refusal was narrowed, not deleted: a price is about one product."""
    body = refusal(ask(shop, "Maggi aur Lifebuoy ka daam kya hai"),
                   R_SEVERAL_PRODUCTS)
    assert "maggi" in body["detail"].lower()


# ------------------------------------------------------------------------
# 9d. Proposals of a stock movement and an expense: paper, never the act.
# ------------------------------------------------------------------------


def test_a_stock_movement_is_written_down_and_the_shelf_does_not_move(big_shop):
    """THE WHOLE CLAIM about write-shaped tools, asserted against stock.py.

    The proposal is written; `/stock/{sku}` is then asked what is on the shelf
    and answers exactly what it answered before, because nothing was carried
    out.
    """
    before = big_shop.get(f"/stock/{MAGGI[0]}").json()
    body = ask(big_shop, "ek carton Maggi aaya").json()
    prop = body["proposal"]
    assert body["tool"] == TOOL_PROPOSE_MOVEMENT
    assert prop["kind"] == KIND_MOVEMENT and prop["accepted"] is False
    assert prop["movement"] == {
        "sku_id": MAGGI[0], "name": MAGGI[1], "direction": "in", "units": 1,
        "reason": "delivery", "reason_label": "a delivery arrived", "note": ""}
    after = big_shop.get(f"/stock/{MAGGI[0]}").json()
    assert after["on_hand_units"] == before["on_hand_units"]
    assert after["units_in_since_count"] == before["units_in_since_count"]


def test_the_movement_proposal_is_a_body_stock_py_actually_accepts(big_shop):
    """A piece of paper that fails on presentation is worse than no paper.

    The proposal's own `accept_by` block is posted to the real endpoint, and
    the shelf moves by exactly the units the proposal named.
    """
    prop = ask(big_shop, "do Lifebuoy toot gaye").json()["proposal"]
    accept = prop["accept_by"]
    assert accept["path"] == f"/stock/{SOAP[0]}/out"
    posted = big_shop.request(accept["method"], accept["path"],
                              json=accept["body"])
    assert posted.status_code == 200, posted.text
    assert big_shop.get(f"/stock/{SOAP[0]}").json()[
        "units_out_since_count"] == 2


def test_a_carton_is_never_multiplied_by_a_number_nobody_supplied(big_shop):
    """Twelve to a carton for one product and forty-eight for the next is what
    a wholesaler decides. The count stays 1 and the response says why."""
    body = ask(big_shop, "ek carton Maggi aaya").json()
    assert body["proposal"]["movement"]["units"] == 1
    assert "never been told how many packets" in body["proposal"]["caution"]
    assert "carton" in body["answer"]


def test_a_dozen_is_multiplied_because_a_dozen_is_twelve_everywhere(shop):
    body = ask(shop, "ek dozen Maggi add karo").json()
    assert body["proposal"]["lines"][0]["qty"] == 12


def test_a_movement_with_no_reason_is_refused_rather_than_filed_under_one(
        big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_MOVEMENT,
        {"product": "Maggi", "direction": "out", "reason": "just because",
         "qty": 2})))
    body = refusal(ask(big_shop, "do Maggi nikal do"), R_BAD_MOVEMENT_REASON)
    assert "breakage" in body["detail"] and "expiry" in body["detail"]


def test_a_sentence_that_says_stock_moved_but_not_why_is_refused(big_shop):
    """"do Maggi hatao" is a movement with no reason on it. It is refused,
    not filed under the likeliest one and not quietly turned into a sale."""
    body = refusal(ask(big_shop, "do Maggi hatao"), R_BAD_MOVEMENT_REASON)
    assert "it broke" in body["detail"] and "it expired" in body["detail"]


def test_a_movement_direction_that_is_neither_in_nor_out_is_refused(
        big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_MOVEMENT,
        {"product": "Maggi", "direction": "sideways", "reason": "delivery"})))
    refusal(ask(big_shop, "Maggi aaya"), R_BAD_DIRECTION)


def test_a_movement_note_longer_than_the_counter_keeps_is_refused(
        big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_MOVEMENT,
        {"product": "Maggi", "direction": "in", "reason": "delivery",
         "note": "x" * 500})))
    refusal(ask(big_shop, "Maggi aaya"), R_NOTE_TOO_LONG)


def test_a_movement_naming_two_products_is_refused_by_name(big_shop):
    refusal(ask(big_shop, "do Maggi aur ek Lifebuoy toot gaye"),
            R_SEVERAL_PRODUCTS)


def test_an_expense_is_written_down_and_the_day_book_does_not_move(big_shop):
    before = big_shop.get("/expenses/day").json()
    body = ask(big_shop, "chai ka sau rupaye kharcha likho").json()
    prop = body["proposal"]
    assert body["tool"] == TOOL_PROPOSE_EXPENSE
    assert prop["kind"] == KIND_EXPENSE and prop["accepted"] is False
    assert prop["expense"]["amount_paise"] == 10000
    assert prop["expense"]["category"] == "tea"
    after = big_shop.get("/expenses/day").json()
    assert after["total_paise"] == before["total_paise"] == 0
    assert after["count"] == before["count"] == 0


def test_the_expense_proposal_is_a_body_expenses_py_actually_accepts(big_shop):
    prop = ask(big_shop, "bijli ka kharcha 1250.75 rupaye likho"
               ).json()["proposal"]
    accept = prop["accept_by"]
    posted = big_shop.request(accept["method"], accept["path"],
                              json=accept["body"])
    assert posted.status_code == 200, posted.text
    assert posted.json()["expense"]["amount_paise"] == 125075
    assert big_shop.get("/expenses/day").json()["total_paise"] == 125075
    # And the assistant now reads back what the day book actually holds.
    assert "1250.75" in ask(big_shop, "aaj ka kharcha kitna hua").json()[
        "answer"]


@pytest.mark.parametrize("said,category", [
    ("kiraya ka kharcha 5000 rupaye likho", "rent"),
    ("bijli ka kharcha 800 rupaye likho", "electricity"),
    ("tankhwah ka kharcha 3000 rupaye likho", "wages"),
    ("chai ka kharcha 50 rupaye likho", "tea"),
    ("auto ka kharcha 120 rupaye likho", "transport"),
    ("thaila ka kharcha 90 rupaye likho", "supplies"),
    ("repair ka kharcha 400 rupaye likho", "repairs"),
    ("kuch aur ka kharcha 10 rupaye likho", "other"),
])
def test_an_expense_is_filed_under_a_category_expenses_py_owns(big_shop, said,
                                                               category):
    prop = ask(big_shop, said).json()["proposal"]
    assert prop["expense"]["category"] == category
    assert category in _expenses.CATEGORIES


def test_an_expense_category_the_counter_does_not_record_is_refused(
        big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_EXPENSE, {"amount_rupees": "100",
                               "category": "bribes"})))
    body = refusal(ask(big_shop, "sau rupaye ka kharcha likho"),
                   R_BAD_EXPENSE_CATEGORY)
    assert "rent" in body["detail"]


def test_an_expense_with_no_amount_is_refused(big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_EXPENSE, {"category": "tea"})))
    refusal(ask(big_shop, "chai ka kharcha likho"), R_NO_AMOUNT)


@pytest.mark.parametrize("amount", ["1.234", "abc", "-5", "12,50"])
def test_an_amount_that_is_not_rupees_is_refused_never_coerced(
        big_shop, monkeypatch, amount):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_EXPENSE, {"amount_rupees": amount, "category": "tea"})))
    refusal(ask(big_shop, "chai ka kharcha likho"), R_BAD_AMOUNT)


def test_an_amount_sent_as_a_number_is_refused_because_it_may_be_a_float(
        big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_EXPENSE, {"amount_rupees": 120.5, "category": "tea"})))
    body = refusal(ask(big_shop, "chai ka kharcha likho"), R_BAD_AMOUNT)
    assert "float" in body["detail"]


def test_an_amount_past_expenses_pys_own_cap_is_refused(big_shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    too_much = str(_expenses.MAX_EXPENSE_PAISE // 100 + 1)
    assistant.set_transport(Fake(tool_call(
        TOOL_PROPOSE_EXPENSE, {"amount_rupees": too_much,
                               "category": "rent"})))
    refusal(ask(big_shop, "kiraya ka kharcha likho"), R_AMOUNT_TOO_LARGE)


def test_a_price_said_out_loud_never_becomes_a_bill_line(shop):
    """INVARIANT 3, in the one place a sentence could smuggle a number in."""
    body = refusal(ask(shop, "Maggi 12 rupaye ka add karo"), R_SPOKEN_PRICE)
    assert "catalogue's price" in body["detail"]
    assert "kharcha" in body["detail"]


# ------------------------------------------------------------------------
# 9e. Loose goods: a weight is priced by weighed.py, or refused.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said,grams", [
    ("aadha kilo chawal", 500),
    ("ordhek kilo chal", 500),
    ("pav kilo rice", 250),
    ("poya kilo rice", 250),
    ("dedh kilo rice", 1500),
    ("sava kilo rice", 1250),
    ("ek kilo rice", 1000),
    ("250 gram rice", 250),
    ("২৫০ gram chal", 250),
])
def test_a_weight_in_any_of_the_three_languages_is_priced_by_weighed_py(
        big_shop, said, grams):
    """The paise are weighed.py's own floor-divide, asserted against it here
    rather than recomputed — a second implementation of a rounding rule is a
    second answer."""
    body = ask(big_shop, said).json()
    assert body["ok"] is True, body
    line = body["proposal"]["lines"][0]
    assert line["by"] == "weighed"
    assert line["grams"] == grams
    assert line["line_paise"] == _weighed.line_paise(RICE_PER_KG, grams)
    assert body["proposal"]["total_paise"] == line["line_paise"]


def test_the_dropped_fraction_of_a_paisa_goes_to_the_customer_and_is_shown(
        big_shop):
    """333 g at Rs 99.00 a kilo is 3296.7 paise. The customer pays 3296."""
    line = ask(big_shop, "333 gram rice").json()["proposal"]["lines"][0]
    assert line["line_paise"] == 3296
    assert line["dropped_thousandths_of_a_paisa"] == 700
    assert isinstance(line["line_paise"], int)


def test_a_bengali_fraction_word_is_mapped_onto_weighed_pys_own_vocabulary():
    """This file must never teach weighed.py a word behind its back."""
    for bengali, hindi in assistant._FRACTION_ALIASES.items():
        assert hindi in _weighed.FRACTION_GRAMS, bengali
    for word in assistant.FRACTION_WORDS:
        canonical = assistant._canonical_fraction(word)
        assert canonical in _weighed.FRACTION_GRAMS, word


def test_a_fraction_of_a_product_sold_in_packets_is_still_refused(big_shop):
    """Milk is not marked loose, so "aadha kilo doodh" cannot be halved."""
    body = refusal(ask(big_shop, "aadha kilo doodh add karo"), R_BAD_QTY)
    assert "not sold by weight" in body["detail"]
    assert "whole packets" in body["detail"]


def test_asking_the_weighed_price_of_a_packet_product_is_refused_by_name(
        big_shop):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_WEIGHED, {"product": "Maggi", "unit": "kilo"})
    assert caught.value.reason == R_NOT_WEIGHED
    assert "packets" in caught.value.detail


def test_asking_for_a_weight_without_saying_one_is_refused(big_shop):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_WEIGHED, {"product": "rice"})
    assert caught.value.reason == R_NO_WEIGHT


def test_a_kilogram_figure_that_is_not_text_is_refused(big_shop):
    """A float weight is how 2.5 kg becomes 2499 g in somebody's browser."""
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_WEIGHED, {"product": "rice", "kg": 2.5})
    assert caught.value.reason == R_BAD_AMOUNT
    assert "float" in caught.value.detail


def test_a_litre_is_not_turned_into_a_weight(big_shop):
    """weighed.py refuses it and its reason is carried out whole, because the
    module that knows why is the one that gets to name it."""
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_WEIGHED, {"product": "rice", "unit": "litre",
                                         "qty": 2})
    assert "weighed.py" in caught.value.detail


def test_a_weighed_line_and_a_packet_line_ride_on_one_proposal(big_shop):
    """The sentence a kirana actually says, and the total has to be right."""
    body = ask(big_shop, "ek Maggi aur aadha kilo chawal add karo").json()
    lines = body["proposal"]["lines"]
    assert [ln["by"] for ln in lines] == ["packet", "weighed"]
    assert body["proposal"]["total_paise"] == (
        MAGGI[2] + _weighed.line_paise(RICE_PER_KG, 500))


# ------------------------------------------------------------------------
# 9f. Customers, loyalty and categories: one thing at a time, named.
# ------------------------------------------------------------------------


def test_a_customer_question_with_no_number_lists_the_regulars_instead(shop):
    assert assistant.local_route("regular customers kaun hai")[0] == TOOL_REGULARS


def test_a_customer_lookup_with_no_phone_is_refused(shop):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_CUSTOMER, {})
    assert caught.value.reason == R_NO_PHONE


@pytest.mark.parametrize("phone", ["98765", "12", "abc"])
def test_a_phone_number_that_is_too_short_to_dial_is_refused(shop, phone):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_LOYALTY, {"phone": phone})
    assert caught.value.reason == R_BAD_PHONE


def test_a_phone_number_in_a_sentence_reaches_the_customer_tool(shop):
    tool, args = assistant.local_route("9876543210 ka customer batao")
    assert tool == TOOL_CUSTOMER and args["phone"] == "9876543210"


def test_a_phone_number_in_bengali_digits_is_read_the_same_way(shop):
    tool, args = assistant.local_route("৯৮৭৬৫৪৩২১০ ke points kitne hain")
    assert tool == TOOL_LOYALTY and args["phone"] == "9876543210"


def test_a_customer_the_shop_has_never_seen_says_so_rather_than_showing_zero(
        shop):
    answer = assistant.execute(TOOL_CUSTOMER,
                               {"phone": "9000000001"})["answer"]
    assert "Nobody with that number" in answer
    assert "no phone number on it" in answer


def test_a_customer_who_ordered_is_reported_from_the_storefronts_own_record(
        shop):
    storefront._write_order({
        "order_id": "ord_0000000000b1", "at": "2026-09-01T09:00:00+00:00",
        "status": "delivered",
        "customer": {"name": "Rekha", "phone": "9876543210",
                     "address": "12 MG Road"},
        "lines": [], "total_paise": 12345, "payment": {"paid": True}})
    out = assistant.execute(TOOL_CUSTOMER, {"phone": "9876543210"})
    assert "Rekha" in out["answer"]
    assert "123.45" in out["answer"]


def test_a_category_that_this_shop_does_not_have_is_refused_with_what_it_does(
        big_shop):
    _categories.save_book(
        [_categories.Category("cat_00000001", "Snacks", None, 0,
                              "2026-09-01T00:00:00+00:00")],
        {MAGGI[0]: {"category_id": "cat_00000001", "tags": []}})
    body = refusal(ask(big_shop, "Household category me kya hai"),
                   R_NO_SUCH_CATEGORY)
    assert "Snacks" in body["detail"]


def test_a_category_that_exists_lists_what_is_filed_under_it(big_shop):
    _categories.save_book(
        [_categories.Category("cat_00000001", "Snacks", None, 0,
                              "2026-09-01T00:00:00+00:00")],
        {MAGGI[0]: {"category_id": "cat_00000001", "tags": []}})
    body = ask(big_shop, "Snacks category me kya hai").json()
    assert body["tool"] == TOOL_IN_CATEGORY
    assert MAGGI[1] in body["answer"]
    assert [p["sku_id"] for p in body["data"]["products"]] == [MAGGI[0]]


def test_naming_no_category_at_all_is_refused(big_shop):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_IN_CATEGORY, {"category": "  "})
    assert caught.value.reason == R_NO_CATEGORY_NAMED


def test_a_days_window_that_is_not_a_whole_number_is_refused(big_shop):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_EXPIRING, {"days": 2.5})
    assert caught.value.reason == R_BAD_DAYS


def test_an_absurd_days_window_is_refused(big_shop):
    with pytest.raises(AssistantRefused) as caught:
        assistant.execute(TOOL_EXPIRING, {"days": 100000})
    assert caught.value.reason == R_BAD_DAYS


def test_a_gst_question_with_no_product_is_refused(shop):
    refusal(ask(shop, "gst kitna hai"), R_NO_PRODUCT_NAMED)


# ------------------------------------------------------------------------
# 9g. The routing table, and the invariants that must survive the new tools.
# ------------------------------------------------------------------------


@pytest.mark.parametrize("said,tool", [
    ("Maggi ka stock kitna hai", TOOL_STOCK_ON_HAND),
    ("stock movements dikhao", TOOL_STOCK_MOVEMENTS),
    ("reorder list batao", TOOL_REORDER_LIST),
    ("ek carton Maggi aaya", TOOL_PROPOSE_MOVEMENT),
    ("do Lifebuoy toot gaye", TOOL_PROPOSE_MOVEMENT),
    ("aaj ka kharcha kitna hua", TOOL_EXPENSES_TODAY),
    ("chai ka sau rupaye kharcha likho", TOOL_PROPOSE_EXPENSE),
    ("golla mein kitna cash hai", TOOL_CASH_POSITION),
    ("Maggi pe kitna munafa hai", TOOL_MARGIN_OF),
    ("aaj ka munafa kitna hua", TOOL_MARGIN_TODAY),
    ("supplier kaun kaun hai", TOOL_SUPPLIERS),
    ("9876543210 ka customer batao", TOOL_CUSTOMER),
    ("regular customers kaun hai", TOOL_REGULARS),
    ("categories dikhao", TOOL_CATEGORIES),
    ("Snacks category me kya hai", TOOL_IN_CATEGORY),
    ("aaj ka hisab dikhao", TOOL_DAY_CLOSE),
    ("kya offer chal raha hai", TOOL_OFFERS),
    ("Maggi pe gst kitna hai", TOOL_GST_OF),
    ("kya expire ho raha hai", TOOL_EXPIRING),
    ("expired kya hai", TOOL_EXPIRED),
    ("9876543210 ke points kitne hain", TOOL_LOYALTY),
    ("loyalty rules batao", TOOL_LOYALTY_RULES),
    ("Sharma ji ke khate mein likh do", "book_on_khata"),
    ("Sharma ji ka kitna baaki hai", "khata_balance"),
])
def test_the_local_parser_reaches_every_new_tool_without_a_model(shop, said,
                                                                 tool):
    """WITH NO KEY SET. A shop on a dropped connection reaches all of this."""
    assert assistant.local_route(said)[0] == tool


def test_every_tool_in_the_list_is_reachable_from_some_sentence(shop):
    """A tool nothing routes to is a tool that only exists in a screenshot.

    `find_product`, `price_of` and `add_to_bill` are covered by the older
    parametrised routing test; this asserts the rest of the list is not
    stranded behind a model nobody has a key for.
    """
    reachable = set()
    for said in ("do Maggi add karo", "Maggi ka daam kya hai",
                 "Lifebuoy hai kya", "kitne orders pending hain",
                 "aaj ki bikri kitni hui", "kitna stock bacha hai",
                 "Maggi ka stock kitna hai", "stock movements dikhao",
                 "reorder list batao", "ek carton Maggi aaya",
                 "aaj ka kharcha kitna hua", "chai ka sau rupaye kharcha likho",
                 "golla mein kitna cash hai", "Maggi pe kitna munafa hai",
                 "aaj ka munafa kitna hua", "supplier kaun kaun hai",
                 "9876543210 ka customer batao", "regular customers kaun hai",
                 "categories dikhao", "Snacks category me kya hai",
                 "aaj ka hisab dikhao", "kya offer chal raha hai",
                 "Maggi pe gst kitna hai", "kya expire ho raha hai",
                 "expired kya hai", "9876543210 ke points kitne hain",
                 "loyalty rules batao", "aadha kilo chawal",
                 "Sharma ji ke khate mein likh do",
                 "Sharma ji ka kitna baaki hai",
                 "kal bank mein kitna aaya"):
        try:
            reachable.add(assistant.local_route(said)[0])
        except AssistantRefused:
            pass
    # weighed_price is reached through add_to_bill when the product is loose,
    # which is asserted on the arithmetic in 9e; everything else is here.
    assert set(TOOL_NAMES) - reachable == {TOOL_WEIGHED}


def test_none_of_the_new_tool_schemas_carries_a_word_from_this_shop(shop):
    """THE PRIVACY PROPERTY, re-asserted now that there are far more schemas.

    Twenty-eight tool descriptions is twenty-eight more places a catalogue
    could have leaked into the payload. Asserted on the serialised bytes.
    """
    storefront._write_order({
        "order_id": "ord_0000000000c1", "at": "2026-09-01T09:00:00+00:00",
        "status": "new", "customer": {"name": "Rekha", "phone": "9876543210",
                                      "address": "12 MG Road"},
        "lines": [], "total_paise": 9999, "payment": {}})
    raw = json.dumps(assistant.payload_for("Maggi ka stock kitna hai")).lower()
    for secret in ("parle", "lifebuoy", "amul", "2145", "3950", "2750",
                   "maggi_noodles_70g", "rekha", "9876543210", "mg road",
                   "ord_0000000000c1", "9999"):
        assert secret.lower() not in raw, f"{secret!r} leaked into the payload"


def test_a_money_shaped_argument_is_still_refused_on_every_other_tool(
        shop, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", FAKE_KEY)
    for tool, key in ((TOOL_ADD, "price_paise"),
                      (TOOL_STOCK_ON_HAND, "cost"),
                      (TOOL_PROPOSE_MOVEMENT, "total"),
                      (TOOL_PROPOSE_EXPENSE, "price_paise")):
        assistant.set_transport(Fake(tool_call(tool, {"product": "Maggi",
                                                      key: 100})))
        refusal(ask(shop, "do Maggi add karo"), R_MODEL_PRICED)


def test_a_stock_movement_proposal_lands_on_the_assistants_own_chain(big_shop):
    ask(big_shop, "ek carton Maggi aaya")
    path = assistant.audit_path()
    ok, lines, head, err = verify(path)
    assert ok and err is None and lines >= 1
    last = list(Ledger(path).read())[-1]
    assert last["module"] == "assistant"
    assert last["event"] == "assistant.proposed"
    assert last["kind"] == KIND_MOVEMENT
    assert last["accepted"] is False and last["minted"] is False


def test_an_expense_proposal_lands_on_the_assistants_own_chain(big_shop):
    ask(big_shop, "chai ka sau rupaye kharcha likho")
    ok, lines, head, err = verify(assistant.audit_path())
    assert ok and err is None
    last = list(Ledger(assistant.audit_path()).read())[-1]
    assert last["kind"] == KIND_EXPENSE
    assert last["amount_paise"] == 10000
    # THE SENTENCE ITSELF IS NOT IN THE CHAIN. An audit log is the file most
    # likely to end up in a bug report.
    assert "chai ka sau" not in json.dumps(last)


def test_no_proposal_of_any_kind_touches_the_money_chain(big_shop, tmp_path):
    for said in ("do Maggi add karo", "ek carton Maggi aaya",
                 "chai ka sau rupaye kharcha likho", "aadha kilo chawal"):
        ask(big_shop, said)
    assert not (tmp_path / "data" / "audit.jsonl").exists()
    assert "assistant" in assistant.audit_path().name


def test_every_proposal_kind_says_it_settles_no_money(big_shop):
    for said in ("do Maggi aur ek Lifebuoy add karo", "ek carton Maggi aaya",
                 "chai ka sau rupaye kharcha likho"):
        body = ask(big_shop, said).json()
        assert body["settles_money"] is False
        assert body["proposal"]["accepted"] is False
        assert "accept" in body["answer"].lower()


def test_every_money_field_in_every_proposal_kind_is_an_integer(big_shop):
    for said in ("do Maggi aur aadha kilo chawal add karo",
                 "chai ka sau rupaye kharcha likho"):
        prop = ask(big_shop, said).json()["proposal"]
        for key, value in prop.items():
            if key.endswith("_paise"):
                assert isinstance(value, int) and not isinstance(value, bool)
        for line in prop.get("lines") or []:
            for key, value in line.items():
                if key.endswith("_paise"):
                    assert isinstance(value, int), (key, value)


@pytest.mark.parametrize("said", [
    "do Maggi aur ek Lifebuoy add karo", "ek carton Maggi aaya",
    "chai ka sau rupaye kharcha likho", "aadha kilo chawal",
    "9876543210 ke points kitne hain", "Snacks category me kya hai",
    "kya expire ho raha hai", "Maggi pe gst kitna hai",
    "aaj ka hisab dikhao", "golla mein kitna cash hai",
    "reorder list batao", "supplier kaun kaun hai",
    "৯৮৭৬৫৪৩২১০", "dui ta ar tin ta", "ar ar ar", "kharcha", "koto",
    "" * 3 + "     ", "hataye do", "0 Maggi add karo",
])
def test_no_sentence_of_any_shape_produces_a_500(big_shop, said):
    """The whole widened surface, fuzzed for the one thing that must not
    happen. A 500 teaches a shopkeeper nothing."""
    r = ask(big_shop, said)
    assert r.status_code in (200, 400), r.text
    assert r.json()["settles_money"] is False


def test_the_module_still_contains_no_payment_primitive_after_all_of_this():
    """INVARIANT 6, re-asserted against the much larger file."""
    src = Path(assistant.__file__).read_text(encoding="utf-8")
    for forbidden in ("upi:", "pa=", "razorpay", "short_url", "payment_link",
                      "vpa"):
        assert forbidden not in src.lower(), f"{forbidden!r} is in assistant.py"


def test_no_alias_maps_to_a_phrase_the_matcher_cannot_use():
    """A two-word alias value becomes one token with a space in it and matches
    nothing. It would fail silently, which is why it is asserted here."""
    for said, means in assistant.ALIASES.items():
        assert " " not in means, (said, means)
        assert assistant.normalise(said) == [said], said
        assert assistant.normalise(means) == [means], means


def test_no_word_list_contains_a_token_normalise_could_never_produce():
    """An entry with an underscore or a capital in it is dead weight that reads
    like coverage. `normalise` emits lowercase alphanumerics and nothing else.
    """
    lists = {
        "NUMBER_WORDS": set(assistant.NUMBER_WORDS),
        "FRACTION_WORDS": set(assistant.FRACTION_WORDS),
        "UNIT_WORDS": set(assistant.UNIT_WORDS),
        "CONJUNCTIONS": set(assistant.CONJUNCTIONS),
        "ADD_VERBS": set(assistant.ADD_VERBS),
        "PACK_UNITS": set(assistant.PACK_UNITS),
        "MOVEMENT_WORDS": set(assistant.MOVEMENT_WORDS),
        "RUPEE_WORDS": set(assistant.RUPEE_WORDS),
        "STOP_WORDS": set(assistant.STOP_WORDS),
        "QUESTION_WORDS": set(assistant.QUESTION_WORDS),
        "ALIASES (values)": set(assistant.ALIASES.values()),
        "ALIASES (keys)": set(assistant.ALIASES),
    }
    for name, words in lists.items():
        for word in words:
            assert assistant.normalise(word) == [word], (name, word)


def test_every_proposal_of_every_kind_is_safe_for_a_bill_shaped_reader(
        big_shop):
    """A reader written before movements and expenses existed must degrade.

    Every page that has ever read a proposal reads `lines` — the browser does
    `proposal.lines.length` — and a missing field there throws and takes the
    screen with it. So every kind carries the field, and the two that are not
    bills carry it empty, which renders the sentence and offers nothing to
    accept. That is right: they are accepted on a different screen.
    """
    for said, kind in (("do Maggi aur ek Lifebuoy add karo", KIND_BILL),
                       ("ek carton Maggi aaya", KIND_MOVEMENT),
                       ("chai ka sau rupaye kharcha likho", KIND_EXPENSE)):
        prop = ask(big_shop, said).json()["proposal"]
        assert prop["kind"] == kind
        assert isinstance(prop["lines"], list)
        assert isinstance(prop["caution"], (str, type(None)))
        assert prop["format"] == assistant.PROPOSAL_FORMAT
        assert prop["accepted"] is False
        assert isinstance(prop["proposal_id"], str)
        assert (len(prop["lines"]) > 0) is (kind == KIND_BILL)


def test_a_movement_proposal_carries_no_rupee_total_because_it_moves_none(
        big_shop):
    """A zero there would read as "worth nothing". Absence is the honest shape.
    """
    prop = ask(big_shop, "ek carton Maggi aaya").json()["proposal"]
    assert "total_paise" not in prop
    assert not any(k.endswith("_paise") for k in prop["movement"])


def test_a_weighed_line_and_a_packet_line_have_the_same_shape(big_shop):
    """One reader, one shape. A page written before loose goods existed must
    not meet an undefined field halfway through drawing a row.

    `qty` is 1 and `unit_paise` equals `line_paise` on a weighed line because
    that is what one weighing IS — weighed.py's own rule that a second scoop is
    a second line, not an arithmetic convenience invented here.
    """
    lines = ask(big_shop, "ek Maggi aur aadha kilo chawal add karo"
                ).json()["proposal"]["lines"]
    packet, weighed = lines
    common = {"sku_id", "name", "qty", "unit_paise", "unit_rupees",
              "line_paise", "line_rupees", "taught_with", "by"}
    assert common <= set(packet) and common <= set(weighed)
    assert weighed["qty"] == 1
    assert weighed["unit_paise"] == weighed["line_paise"]
    assert weighed["taught_with"] == packet["taught_with"]
    for line in lines:
        for key in ("unit_paise", "line_paise", "qty"):
            assert isinstance(line[key], int) and not isinstance(line[key], bool)


# ===========================================================================
# SAID AS TWO WORDS, WRITTEN AS ONE
#
# A shopkeeper says "ponds cream". The Products screen holds `pondscream`,
# because that is what somebody typed into the sku box. Every match pass
# compared word against word, so two tokens could never meet one glued token
# and the phrase was refused as a product this shop does not sell — inside a
# refusal that then listed the shop's products.
# ===========================================================================

def _shop():
    return {
        "pondscream": {"name": "pondscream", "price_paise": 30000},
        "parle_g_biscuit": {"name": "Parle-G biscuit 100g", "price_paise": 1000},
        "DermaCoRoller": {"name": "DermaCoRoller", "price_paise": 39900},
        "maggi_noodles_70g": {"name": "Maggi 2-Minute Noodles 70 g", "price_paise": 1400},
    }


@pytest.mark.parametrize("said,expected", [
    ("ponds cream", "pondscream"),          # two words, one sku
    ("pondscream", "pondscream"),           # unchanged
    ("derma co roller", "DermaCoRoller"),   # three words, one sku
    ("dermaco", "DermaCoRoller"),           # prefix, unchanged
    ("parlegbiscuit", "parle_g_biscuit"),   # one breath, three sku words
    ("parle g biscuit", "parle_g_biscuit"), # unchanged
    ("maggi", "maggi_noodles_70g"),         # unchanged
])
def test_gluing_finds_the_product(said, expected):
    assert assistant.resolve_product(said, _shop()) == expected


def test_gluing_never_invents_a_product():
    """It widens what can be FOUND, never what may be assumed."""
    with pytest.raises(assistant.AssistantRefused) as e:
        assistant.resolve_product("pepsi cola", _shop())
    assert e.value.reason == assistant.R_NO_SUCH_PRODUCT


def test_gluing_is_last_and_cannot_overrule_a_real_word_match():
    """A phrase that matches by whole word keeps that answer."""
    shop = dict(_shop())
    shop["ponds"] = {"name": "ponds", "price_paise": 30000}
    assert assistant.resolve_product("ponds", shop) == "ponds"


def test_a_two_letter_glue_is_not_enough_to_match():
    """Short glue is noise; the pass needs four characters to fire."""
    with pytest.raises(assistant.AssistantRefused):
        assistant.resolve_product("a b", _shop())
