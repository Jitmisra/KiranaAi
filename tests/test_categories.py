"""gawaah/categories.py — filing a catalogue that has outgrown one list.

Five claims, in the order they would hurt if they were false:

  1. FILING NEVER TOUCHES STOCK. Deleting a category uncategorises products and
     deletes none of them, and the response says how many it moved. A test below
     snapshots every byte of the shopkeeper's catalogue directory, runs the
     whole API over it, and asserts that nothing outside this module's own two
     files changed.

  2. THE SUGGESTION IS A KEYWORD LIST. It is asserted to be deterministic, to
     publish its own rules, to change nothing on disk, and to leave genuinely
     ambiguous names unmatched rather than guessing at them.

  3. EVERY REFUSAL HAS A NAME. Each named reason in the module has a test here,
     and no input of any shape produces a 500.

  4. ONE LEVEL OF NESTING IS TRUE OF WHAT IS READ AS WELL AS WHAT IS WRITTEN. A
     hand-edited file with a grandchild in it comes back flattened rather than
     crashing a Products screen.

  5. NOTHING HERE MAY SEE `results/`. Both GAWAAH_SHOP_DIR and the till's own
     cached handle are redirected for every test — a harness that honoured only
     one of them once destroyed the live catalogue, and that has no undo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gawaah import categories  # noqa: E402
from gawaah.categories import (  # noqa: E402
    MAX_ASSIGN,
    MAX_CATEGORIES,
    MAX_NAME,
    MAX_TAG,
    MAX_TAGS_PER_SKU,
    R_BAD_ASSIGNMENT,
    R_BAD_BODY,
    R_BAD_SORT,
    R_BAD_TAG,
    R_INTERNAL,
    R_NAME_TAKEN,
    R_NESTING_TOO_DEEP,
    R_NO_CATALOGUE,
    R_NO_CATEGORY,
    R_NO_NAME,
    R_NO_PARENT,
    R_NO_TILL,
    R_NOTHING_TO_CHANGE,
    R_SELF_PARENT,
    R_TOO_LONG,
    R_TOO_MANY,
    R_TOO_MANY_ASSIGNMENTS,
    R_TOO_MANY_TAGS,
    R_UNKNOWN_SKU,
    R_UNWRITABLE,
    Category,
    CategoryRefused,
    load_book,
    save_book,
    suggest_for_name,
)
from gawaah.ledger import Ledger, verify  # noqa: E402
from tools import upload_app  # noqa: E402

# The shop these tests file. The names are chosen to drive the keyword table:
# a bathing bar that the list calls Household, a biscuit, a staple, a dairy
# item, and one product whose name is honestly ambiguous.
SOAP = ("lifebuoy_125g", "Lifebuoy Soap 125g", 3950)
BISCUIT = ("parle_g_200g", "Parle-G Biscuits 200g", 2145)
SALT = ("tata_salt_1kg", "Tata Salt 1kg", 2800)
BUTTER = ("amul_butter_100g", "Amul Butter 100g", 6200)
POWDER = ("ponds_talc_100g", "Ponds Talcum Powder", 9900)
CATALOGUE = (SOAP, BISCUIT, SALT, BUTTER, POWDER)


# ------------------------------------------------------------------ rigging


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A shop that lives and dies with the test. Never `results/`."""
    shop = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAWAAH_CATEGORIES_FILE", raising=False)
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    categories.set_categories_path(None)
    yield
    categories.set_categories_path(None)


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(categories.router)
    return TestClient(app)


@pytest.fixture()
def client() -> TestClient:
    for i, (sku, name, price) in enumerate(CATALOGUE):
        upload_app.do_enrol_code_only(b"", sku, name, price,
                                      typed=f"89012345678{i:02d}")
    return _app()


def _mk(client: TestClient, name: str, **over) -> dict:
    """Create a category and return it, failing loudly if it was refused."""
    body = {"name": name}
    body.update(over)
    r = client.post("/categories", json=body)
    assert r.status_code == 200, r.text
    return r.json()["category"]


def _refused(response, reason: str) -> dict:
    """Assert one named refusal, in the shape every endpoint here answers with."""
    assert response.status_code in (400, 404), response.text
    body = response.json()
    assert body["ok"] is False, body
    assert body["reason"] == reason, body
    assert body["detail"], body
    assert body["settles_money"] is False, body
    return body


def _snapshot(d: Path) -> dict[str, bytes]:
    """Every file in the shop directory EXCEPT this module's own sidecars."""
    out: dict[str, bytes] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name.startswith("categories."):
            continue
        out[str(p.relative_to(d))] = p.read_bytes()
    return out


# ----------------------------------------------------------------- reading


def test_an_unfiled_shop_reads_as_every_product_uncategorised(client):
    r = client.get("/categories")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["categories"] == []
    assert body["products"] == len(CATALOGUE)
    assert body["categorised"] == 0
    assert body["uncategorised"] == len(CATALOGUE)
    assert body["limits"]["max_categories"] == MAX_CATEGORIES
    assert "one level" in body["limits"]["nesting"]


def test_health_names_a_sidecar_beside_the_catalogue_and_its_own_chain(client,
                                                                      tmp_path):
    body = client.get("/categories/health").json()
    assert body["ok"] is True
    assert body["file"] == str(tmp_path / "shop" / "categories.json")
    assert body["audit_file"] == str(tmp_path / "shop" / "categories.audit.jsonl")
    # The money service holds results/audit.jsonl open as sole writer. This
    # module must never be a second writer on it.
    assert not body["audit_file"].endswith(os.path.join("results",
                                                        "audit.jsonl"))
    assert body["owns_catalog_json"] is False
    assert body["catalogue_readable"] is True
    assert body["categories"] == 0


def test_the_product_list_carries_integer_paise_and_a_rendered_rupee(client):
    body = client.get("/categories/products").json()
    assert body["count"] == len(CATALOGUE)
    by_sku = {row["sku_id"]: row for row in body["products"]}
    assert set(by_sku) == {sku for sku, _, _ in CATALOGUE}
    for sku, name, price in CATALOGUE:
        row = by_sku[sku]
        assert row["price_paise"] == price
        assert isinstance(row["price_paise"], int)
        assert not isinstance(row["price_paise"], bool)
        assert row["name"] == name
        assert row["category_id"] is None
        assert row["tags"] == []
    assert by_sku[SOAP[0]]["price_rupees"] == "39.50"
    assert body["paginated"] is False


# ---------------------------------------------------------------- creating


def test_a_created_category_gets_an_id_and_shows_up_in_the_menu(client):
    cat = _mk(client, "Household")
    assert categories.CATEGORY_ID_RE.match(cat["category_id"]), cat
    assert cat["name"] == "Household"
    assert cat["parent_id"] is None
    assert cat["depth"] == 0
    assert cat["products"] == 0

    body = client.get("/categories").json()
    assert body["count"] == 1
    assert body["categories"][0]["category_id"] == cat["category_id"]


def test_a_category_with_no_name_is_refused(client):
    _refused(client.post("/categories", json={}), R_NO_NAME)
    _refused(client.post("/categories", json={"name": "   "}), R_NO_NAME)
    _refused(client.post("/categories", json={"name": 7}), R_NO_NAME)


def test_a_name_past_the_cap_is_refused(client):
    _refused(client.post("/categories", json={"name": "x" * (MAX_NAME + 1)}),
             R_TOO_LONG)


def test_two_categories_cannot_read_the_same_in_a_menu(client):
    _mk(client, "Snacks")
    _refused(client.post("/categories", json={"name": "snacks"}), R_NAME_TAKEN)
    _refused(client.post("/categories", json={"name": "  SNACKS  "}),
             R_NAME_TAKEN)


def test_a_parent_that_does_not_exist_is_refused(client):
    _refused(client.post("/categories",
                         json={"name": "Cleaning", "parent_id": "cat_deadbeef"}),
             R_NO_PARENT)
    _refused(client.post("/categories",
                         json={"name": "Cleaning", "parent_id": 12}),
             R_NO_PARENT)


def test_a_second_level_of_nesting_is_refused_rather_than_flattened(client):
    top = _mk(client, "Household")
    mid = _mk(client, "Cleaning", parent_id=top["category_id"])
    assert mid["depth"] == 1
    _refused(client.post("/categories",
                         json={"name": "Floor", "parent_id": mid["category_id"]}),
             R_NESTING_TOO_DEEP)
    # And the refusal changed nothing: the menu is still two lines.
    assert client.get("/categories").json()["count"] == 2


def test_a_sort_order_is_a_whole_number_in_a_stated_range(client):
    for bad in (1.5, True, "3", -1, 10 ** 9):
        _refused(client.post("/categories",
                             json={"name": f"S{bad!r}", "sort_order": bad}),
                 R_BAD_SORT)


def test_default_sort_orders_leave_room_to_insert_between_them(client):
    first = _mk(client, "Household")
    second = _mk(client, "Snacks")
    assert second["sort_order"] > first["sort_order"]
    middle = _mk(client, "Dairy", sort_order=first["sort_order"] + 1)
    names = [c["name"] for c in client.get("/categories").json()["categories"]]
    assert names == ["Household", "Dairy", "Snacks"]
    assert middle["sort_order"] == first["sort_order"] + 1


def test_the_menu_puts_each_parent_ahead_of_its_own_children(client):
    house = _mk(client, "Household", sort_order=10)
    _mk(client, "Snacks", sort_order=20)
    _mk(client, "Cleaning", parent_id=house["category_id"])
    rows = client.get("/categories").json()["categories"]
    assert [r["name"] for r in rows] == ["Household", "Cleaning", "Snacks"]
    assert [r["depth"] for r in rows] == [0, 1, 0]
    assert rows[0]["children"] == [rows[1]["category_id"]]
    assert rows[1]["parent_name"] == "Household"


def test_the_category_cap_is_a_named_refusal(client):
    save_book([Category(f"cat_{i:08x}", f"Shelf {i}", None, i, "")
               for i in range(MAX_CATEGORIES)], {})
    body = _refused(client.post("/categories", json={"name": "One too many"}),
                    R_TOO_MANY)
    assert str(MAX_CATEGORIES) in body["detail"]


# ----------------------------------------------------------- filing one SKU


def test_filing_a_product_moves_it_out_of_uncategorised(client):
    cat = _mk(client, "Household")
    r = client.put(f"/categories/sku/{SOAP[0]}",
                   json={"category_id": cat["category_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["category_id"] == cat["category_id"]
    assert body["product"]["category_name"] == "Household"
    assert body["was_category_id"] is None
    assert body["audited"] is True

    menu = client.get("/categories").json()
    assert menu["categorised"] == 1
    assert menu["uncategorised"] == len(CATALOGUE) - 1
    assert menu["categories"][0]["products"] == 1


def test_filing_a_product_this_shop_does_not_sell_is_refused(client):
    cat = _mk(client, "Household")
    r = client.put("/categories/sku/not_a_real_sku",
                   json={"category_id": cat["category_id"]})
    assert r.status_code == 404
    _refused(r, R_UNKNOWN_SKU)


def test_filing_into_a_category_that_does_not_exist_is_refused(client):
    r = client.put(f"/categories/sku/{SOAP[0]}",
                   json={"category_id": "cat_00000000"})
    assert r.status_code == 404
    _refused(r, R_NO_CATEGORY)


def test_a_null_category_takes_a_product_back_out(client):
    cat = _mk(client, "Household")
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": cat["category_id"]})
    r = client.put(f"/categories/sku/{SOAP[0]}", json={"category_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["product"]["category_id"] is None
    assert client.get("/categories").json()["uncategorised"] == len(CATALOGUE)


def test_tags_are_lowercased_deduplicated_and_sorted(client):
    r = client.put(f"/categories/sku/{SOAP[0]}",
                   json={"tags": ["Daily", "daily", "  BULK  ", "monsoon"]})
    assert r.status_code == 200, r.text
    assert r.json()["product"]["tags"] == ["bulk", "daily", "monsoon"]
    menu = client.get("/categories").json()
    assert {t["tag"] for t in menu["tags"]} == {"bulk", "daily", "monsoon"}
    assert all(t["products"] == 1 for t in menu["tags"])


def test_a_tag_that_cannot_be_typed_back_is_refused(client):
    for bad in ([5], [""], ["   "], ["-leading"], ["what?"], "notalist"):
        _refused(client.put(f"/categories/sku/{SOAP[0]}", json={"tags": bad}),
                 R_BAD_TAG)


def test_a_tag_past_the_cap_is_refused(client):
    _refused(client.put(f"/categories/sku/{SOAP[0]}",
                        json={"tags": ["t" * (MAX_TAG + 1)]}), R_TOO_LONG)


def test_more_tags_than_the_cap_on_one_product_is_refused(client):
    tags = [f"tag{i}" for i in range(MAX_TAGS_PER_SKU + 1)]
    _refused(client.put(f"/categories/sku/{SOAP[0]}", json={"tags": tags}),
             R_TOO_MANY_TAGS)


def test_a_request_that_changes_no_filing_is_refused(client):
    _refused(client.put(f"/categories/sku/{SOAP[0]}", json={}),
             R_NOTHING_TO_CHANGE)
    client.put(f"/categories/sku/{SOAP[0]}", json={"tags": ["daily"]})
    _refused(client.put(f"/categories/sku/{SOAP[0]}", json={"tags": ["daily"]}),
             R_NOTHING_TO_CHANGE)


def test_tags_belong_to_the_product_and_survive_losing_a_category(client):
    cat = _mk(client, "Household")
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": cat["category_id"], "tags": ["daily"]})
    client.delete(f"/categories/{cat['category_id']}")
    rows = client.get("/categories/products").json()["products"]
    soap = next(r for r in rows if r["sku_id"] == SOAP[0])
    assert soap["category_id"] is None
    assert soap["tags"] == ["daily"]


# ----------------------------------------------------------------- editing


def test_renaming_keeps_the_id_so_the_products_stay_filed(client):
    cat = _mk(client, "Snacks")
    client.put(f"/categories/sku/{BISCUIT[0]}",
               json={"category_id": cat["category_id"]})
    r = client.patch(f"/categories/{cat['category_id']}",
                     json={"name": "Namkeen"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"]["category_id"] == cat["category_id"]
    assert body["category"]["name"] == "Namkeen"
    assert body["was"]["name"] == "Snacks"
    assert body["category"]["products"] == 1


def test_renaming_onto_another_categorys_name_is_refused(client):
    _mk(client, "Snacks")
    other = _mk(client, "Household")
    _refused(client.patch(f"/categories/{other['category_id']}",
                          json={"name": "snacks"}), R_NAME_TAKEN)


def test_a_category_cannot_be_filed_inside_itself(client):
    cat = _mk(client, "Household")
    _refused(client.patch(f"/categories/{cat['category_id']}",
                          json={"parent_id": cat["category_id"]}),
             R_SELF_PARENT)


def test_a_category_with_children_cannot_be_nested_under_another(client):
    house = _mk(client, "Household")
    _mk(client, "Cleaning", parent_id=house["category_id"])
    snacks = _mk(client, "Snacks")
    _refused(client.patch(f"/categories/{house['category_id']}",
                          json={"parent_id": snacks["category_id"]}),
             R_NESTING_TOO_DEEP)


def test_a_child_can_be_moved_back_to_the_top_level(client):
    house = _mk(client, "Household")
    child = _mk(client, "Cleaning", parent_id=house["category_id"])
    r = client.patch(f"/categories/{child['category_id']}",
                     json={"parent_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["category"]["parent_id"] is None
    assert r.json()["category"]["depth"] == 0


def test_editing_a_category_that_does_not_exist_is_a_404(client):
    r = client.patch("/categories/cat_00000000", json={"name": "Anything"})
    assert r.status_code == 404
    _refused(r, R_NO_CATEGORY)


def test_an_edit_that_changes_nothing_is_refused(client):
    cat = _mk(client, "Household")
    _refused(client.patch(f"/categories/{cat['category_id']}", json={}),
             R_NOTHING_TO_CHANGE)
    _refused(client.patch(f"/categories/{cat['category_id']}",
                          json={"name": "Household"}), R_NOTHING_TO_CHANGE)


# ---------------------------------------------------------------- deleting


def test_deleting_a_category_deletes_no_products(client):
    cat = _mk(client, "Household")
    for sku, _, _ in (SOAP, POWDER):
        client.put(f"/categories/sku/{sku}",
                   json={"category_id": cat["category_id"]})
    before = set(upload_app.priced_skus())

    r = client.delete(f"/categories/{cat['category_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uncategorised"] == 2
    assert body["products_deleted"] == 0
    assert body["removed"] == "Household"

    assert set(upload_app.priced_skus()) == before
    listing = client.get("/categories/products").json()
    assert listing["count"] == len(CATALOGUE)
    assert all(row["category_id"] is None for row in listing["products"])
    assert client.get("/categories").json()["uncategorised"] == len(CATALOGUE)


def test_deleting_a_parent_promotes_its_children_and_says_how_many(client):
    house = _mk(client, "Household", sort_order=10)
    cleaning = _mk(client, "Cleaning", parent_id=house["category_id"])
    r = client.delete(f"/categories/{house['category_id']}")
    body = r.json()
    assert body["children_promoted"] == 1
    assert body["promoted"] == [cleaning["category_id"]]

    rows = client.get("/categories").json()["categories"]
    assert [r_["name"] for r_ in rows] == ["Cleaning"]
    assert rows[0]["parent_id"] is None
    assert rows[0]["depth"] == 0


def test_deleting_a_category_that_does_not_exist_is_a_404(client):
    r = client.delete("/categories/cat_00000000")
    assert r.status_code == 404
    _refused(r, R_NO_CATEGORY)


# ------------------------------------------------------------- suggestions


def test_the_suggestion_reads_the_name_and_names_the_keyword_it_matched(client):
    for name in ("Household", "Snacks", "Staples", "Dairy"):
        _mk(client, name)
    body = client.get("/categories/suggest").json()
    proposals = {p["sku_id"]: p for p in body["proposals"]}

    assert proposals[SOAP[0]]["suggested_name"] == "Household"
    assert proposals[SOAP[0]]["matched_keyword"] == "soap"
    assert proposals[BISCUIT[0]]["suggested_name"] == "Snacks"
    assert proposals[BISCUIT[0]]["matched_keyword"] == "biscuits"
    assert proposals[SALT[0]]["suggested_name"] == "Staples"
    assert proposals[BUTTER[0]]["suggested_name"] == "Dairy"
    assert all(p["ready"] for p in body["proposals"])
    assert body["method"] == "keyword list, not inference"


def test_the_suggestion_publishes_the_whole_keyword_table(client):
    body = client.get("/categories/suggest").json()
    table = {rule["category"]: rule["keywords"] for rule in body["rules"]}
    assert list(table) == list(categories.SUGGESTED_NAMES)
    assert "soap" in table["Household"]
    assert "biscuit" in table["Snacks"]
    # An honestly ambiguous word is in NO rule rather than in a guessed one.
    for keywords in table.values():
        assert "powder" not in keywords
        assert "cream" not in keywords


def test_the_suggestion_is_deterministic_and_writes_nothing(client, tmp_path):
    _mk(client, "Household")
    before_menu = client.get("/categories").json()
    snapshot = _snapshot(tmp_path / "shop")
    sidecar = (tmp_path / "shop" / "categories.json").read_bytes()

    first = client.get("/categories/suggest").json()
    second = client.get("/categories/suggest").json()
    assert first == second
    assert first["changed_nothing"] is True
    assert (tmp_path / "shop" / "categories.json").read_bytes() == sidecar
    assert _snapshot(tmp_path / "shop") == snapshot
    assert client.get("/categories").json() == before_menu


def test_the_suggestion_says_which_categories_do_not_exist_yet(client):
    body = client.get("/categories/suggest").json()
    assert "Household" in body["missing_categories"]
    assert "Snacks" in body["missing_categories"]
    soap = next(p for p in body["proposals"] if p["sku_id"] == SOAP[0])
    assert soap["category_id"] is None
    assert soap["ready"] is False


def test_an_ambiguous_name_comes_back_unmatched_rather_than_guessed(client):
    body = client.get("/categories/suggest").json()
    unmatched = {row["sku_id"] for row in body["unmatched"]}
    assert POWDER[0] in unmatched
    assert POWDER[0] not in {p["sku_id"] for p in body["proposals"]}
    assert suggest_for_name("Ponds Talcum Powder") is None


def test_the_suggestion_leaves_a_decision_a_person_already_made_alone(client):
    cat = _mk(client, "Dairy")
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": cat["category_id"]})

    body = client.get("/categories/suggest").json()
    assert body["already_categorised"] == 1
    assert SOAP[0] not in {p["sku_id"] for p in body["proposals"]}

    louder = client.get("/categories/suggest?include_assigned=true").json()
    soap = next(p for p in louder["proposals"] if p["sku_id"] == SOAP[0])
    assert soap["currently"] == cat["category_id"]
    assert soap["suggested_name"] == "Household"


def test_whole_words_only_so_salted_chips_is_not_a_staple():
    # Substring matching would file "Salted Chips" under Staples on "salt" and
    # "Pepsodent" under Stationery on "pen". Both are whole-word misses.
    assert suggest_for_name("Salted Chips 50g") == ("Snacks", "chips")
    assert suggest_for_name("Pepsodent Toothpaste") == ("Personal care",
                                                        "toothpaste")
    assert suggest_for_name("Parachute Hair Oil") == ("Personal care",
                                                      "hair oil")
    assert suggest_for_name("Fortune Sunflower Oil 1L") == ("Staples", "oil")


def test_a_proposal_becomes_a_filing_only_when_a_person_accepts_it(client):
    for name in ("Household", "Snacks", "Staples", "Dairy"):
        _mk(client, name)
    proposals = client.get("/categories/suggest").json()["proposals"]
    assert client.get("/categories").json()["categorised"] == 0

    r = client.post("/categories/assign", json={
        "assign": [{"sku_id": p["sku_id"], "category_id": p["category_id"]}
                   for p in proposals if p["ready"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == len(proposals)
    assert body["unchanged"] == 0
    assert body["audited"] is True

    menu = client.get("/categories").json()
    assert menu["categorised"] == len(proposals)
    assert menu["uncategorised"] == len(CATALOGUE) - len(proposals)


# ----------------------------------------------------------- bulk assigning


def test_one_unknown_sku_in_a_list_files_none_of_it(client):
    cat = _mk(client, "Household")
    r = client.post("/categories/assign", json={"assign": [
        {"sku_id": SOAP[0], "category_id": cat["category_id"]},
        {"sku_id": "ghost_sku", "category_id": cat["category_id"]},
    ]})
    _refused(r, R_UNKNOWN_SKU)
    assert client.get("/categories").json()["categorised"] == 0


def test_an_unknown_category_in_a_list_files_none_of_it(client):
    cat = _mk(client, "Household")
    r = client.post("/categories/assign", json={"assign": [
        {"sku_id": SOAP[0], "category_id": cat["category_id"]},
        {"sku_id": BISCUIT[0], "category_id": "cat_00000000"},
    ]})
    _refused(r, R_NO_CATEGORY)
    assert client.get("/categories").json()["categorised"] == 0


def test_an_assignment_that_is_not_a_list_of_lines_is_refused(client):
    for bad in ({}, {"assign": []}, {"assign": "everything"},
                {"assign": [3]}, {"assign": [["a", "b"]]}):
        _refused(client.post("/categories/assign", json=bad), R_BAD_ASSIGNMENT)


def test_an_assignment_past_the_cap_is_refused(client):
    rows = [{"sku_id": SOAP[0], "category_id": None}
            for _ in range(MAX_ASSIGN + 1)]
    _refused(client.post("/categories/assign", json={"assign": rows}),
             R_TOO_MANY_ASSIGNMENTS)


def test_assigning_null_uncategorises_and_reports_the_count(client):
    cat = _mk(client, "Household")
    client.post("/categories/assign", json={"assign": [
        {"sku_id": SOAP[0], "category_id": cat["category_id"]},
        {"sku_id": BISCUIT[0], "category_id": cat["category_id"]},
    ]})
    r = client.post("/categories/assign", json={"assign": [
        {"sku_id": SOAP[0], "category_id": None},
        {"sku_id": BISCUIT[0], "category_id": cat["category_id"]},
    ]})
    body = r.json()
    assert body["uncategorised"] == 1
    assert body["changed"] == 1
    assert body["unchanged"] == 1
    assert client.get("/categories").json()["categorised"] == 1


# --------------------------------------------------------------- filtering


def test_filtering_by_a_parent_includes_the_categories_inside_it(client):
    house = _mk(client, "Household")
    cleaning = _mk(client, "Cleaning", parent_id=house["category_id"])
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": house["category_id"]})
    client.put(f"/categories/sku/{POWDER[0]}",
               json={"category_id": cleaning["category_id"]})

    body = client.get(f"/categories/products?category={house['category_id']}").json()
    assert {r["sku_id"] for r in body["products"]} == {SOAP[0], POWDER[0]}
    assert body["filter"]["included_children"] == [cleaning["category_id"]]

    inner = client.get(
        f"/categories/products?category={cleaning['category_id']}").json()
    assert {r["sku_id"] for r in inner["products"]} == {POWDER[0]}


def test_filtering_by_tag_and_by_text(client):
    client.put(f"/categories/sku/{SOAP[0]}", json={"tags": ["Daily"]})
    client.put(f"/categories/sku/{BISCUIT[0]}", json={"tags": ["festival"]})

    tagged = client.get("/categories/products?tag=DAILY").json()
    assert [r["sku_id"] for r in tagged["products"]] == [SOAP[0]]
    assert tagged["filter"]["tag"] == "daily"

    searched = client.get("/categories/products?q=parle").json()
    assert [r["sku_id"] for r in searched["products"]] == [BISCUIT[0]]
    assert client.get("/categories/products?q=zzz").json()["count"] == 0


def test_the_uncategorised_filter_shows_only_what_is_not_filed(client):
    cat = _mk(client, "Household")
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": cat["category_id"]})
    body = client.get("/categories/products?category=none").json()
    assert body["count"] == len(CATALOGUE) - 1
    assert SOAP[0] not in {r["sku_id"] for r in body["products"]}


def test_filtering_by_a_category_that_does_not_exist_is_a_404(client):
    r = client.get("/categories/products?category=cat_00000000")
    assert r.status_code == 404
    _refused(r, R_NO_CATEGORY)


# ------------------------------------------------- storage and invariants


def test_the_shopkeepers_catalogue_is_never_rewritten(client, tmp_path):
    """The catalogue belongs to shop_store.py. This module writes beside it."""
    shop = tmp_path / "shop"
    before = _snapshot(shop)
    assert before, "the fixture should have written a catalogue to compare with"

    house = _mk(client, "Household")
    child = _mk(client, "Cleaning", parent_id=house["category_id"])
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": child["category_id"], "tags": ["daily"]})
    client.post("/categories/assign", json={"assign": [
        {"sku_id": BISCUIT[0], "category_id": house["category_id"]}]})
    client.patch(f"/categories/{house['category_id']}", json={"name": "Ghar"})
    client.get("/categories/suggest")
    client.delete(f"/categories/{house['category_id']}")

    assert _snapshot(shop) == before
    assert (shop / "categories.json").exists()


def test_the_audit_chain_is_this_modules_own_and_it_verifies(client, tmp_path):
    cat = _mk(client, "Household")
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": cat["category_id"]})
    client.patch(f"/categories/{cat['category_id']}", json={"name": "Ghar"})
    client.delete(f"/categories/{cat['category_id']}")

    path = categories.audit_path()
    assert path == tmp_path / "shop" / "categories.audit.jsonl"
    ok, lines, head, err = verify(path)
    assert ok, err
    assert lines == 4
    events = [rec["event"] for rec in Ledger(path).read()]
    assert events == ["category.created", "sku.filed", "category.edited",
                      "category.deleted"]
    assert all(rec["module"] == "categories" for rec in Ledger(path).read())


def test_filing_survives_a_restart_because_it_is_on_disk(client, tmp_path):
    cat = _mk(client, "Household")
    client.put(f"/categories/sku/{SOAP[0]}",
               json={"category_id": cat["category_id"], "tags": ["daily"]})

    fresh = _app()  # a new app over the same directory, nothing cached
    rows = fresh.get("/categories/products").json()["products"]
    soap = next(r for r in rows if r["sku_id"] == SOAP[0])
    assert soap["category_id"] == cat["category_id"]
    assert soap["tags"] == ["daily"]

    cats, skus = load_book()
    assert [c.name for c in cats] == ["Household"]
    assert skus[SOAP[0]]["category_id"] == cat["category_id"]


def test_a_sidecar_that_cannot_be_parsed_reads_as_an_unfiled_shop(client,
                                                                  tmp_path):
    (tmp_path / "shop" / "categories.json").write_text("{not json at all",
                                                       encoding="utf-8")
    body = client.get("/categories").json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["uncategorised"] == len(CATALOGUE)
    # And it is repairable by writing over it, not by hand-editing it back.
    assert client.post("/categories", json={"name": "Household"}).status_code == 200


def test_a_hand_edited_grandchild_is_read_as_one_level(client, tmp_path):
    (tmp_path / "shop" / "categories.json").write_text(json.dumps({
        "format": 1,
        "categories": [
            {"category_id": "cat_00000001", "name": "Household",
             "parent_id": None, "sort_order": 10, "created_at": ""},
            {"category_id": "cat_00000002", "name": "Cleaning",
             "parent_id": "cat_00000001", "sort_order": 10, "created_at": ""},
            {"category_id": "cat_00000003", "name": "Floor",
             "parent_id": "cat_00000002", "sort_order": 10, "created_at": ""},
            {"category_id": "cat_00000004", "name": "Orphan",
             "parent_id": "cat_99999999", "sort_order": 10, "created_at": ""},
        ],
        "skus": {},
    }), encoding="utf-8")

    rows = client.get("/categories").json()["categories"]
    assert {r["name"]: r["depth"] for r in rows} == {
        "Household": 0, "Cleaning": 1, "Floor": 0, "Orphan": 0}
    assert all(r["depth"] in (0, 1) for r in rows)


def test_a_sidecar_that_cannot_be_written_is_a_named_refusal(client, tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("not a directory", encoding="utf-8")
    categories.set_categories_path(blocker / "categories.json")
    _refused(client.post("/categories", json={"name": "Household"}),
             R_UNWRITABLE)


def test_a_filing_for_a_product_that_left_the_catalogue_is_kept_and_counted(
        client, tmp_path):
    cat = _mk(client, "Household")
    cats, _ = load_book()
    save_book(cats, {"a_sku_that_is_gone": {"category_id": cat["category_id"],
                                            "tags": ["daily"]}})
    health = client.get("/categories/health").json()
    assert health["orphans"] == 1
    assert health["filed"] == 0
    listing = client.get("/categories/products").json()
    assert "a_sku_that_is_gone" not in {r["sku_id"] for r in listing["products"]}
    assert client.get("/categories").json()["categories"][0]["products"] == 0


def test_an_unreadable_catalogue_is_a_named_refusal(client, monkeypatch):
    def boom():
        raise RuntimeError("the catalogue is on fire")

    monkeypatch.setattr(categories, "_till",
                        lambda: SimpleNamespace(offer_priced_skus=boom))
    _refused(client.get("/categories"), R_NO_CATALOGUE)
    monkeypatch.setattr(categories, "_till", lambda: SimpleNamespace())
    _refused(client.get("/categories/products"), R_NO_CATALOGUE)


def test_a_missing_till_is_a_named_refusal_not_a_crash(client, monkeypatch):
    def no_till():
        raise CategoryRefused(R_NO_TILL, "tools/upload_app.py is not importable")

    monkeypatch.setattr(categories, "_till", no_till)
    for r in (client.get("/categories"),
              client.get("/categories/suggest"),
              client.get("/categories/products"),
              client.put(f"/categories/sku/{SOAP[0]}",
                         json={"category_id": None, "tags": ["daily"]})):
        _refused(r, R_NO_TILL)

    # Making a shelf does NOT need the catalogue, and it still works when the
    # till is not there. That is deliberate: the sidecar's location comes from
    # GAWAAH_SHOP_DIR, and a shopkeeper who can name their shelves before the
    # products load has lost nothing.
    assert client.post("/categories",
                       json={"name": "Household"}).status_code == 200


def test_an_unexpected_failure_is_a_400_with_a_reason_not_a_500(client,
                                                               monkeypatch):
    def boom(*a, **k):
        raise ZeroDivisionError("something nobody planned for")

    monkeypatch.setattr(categories, "load_book", boom)
    r = client.get("/categories")
    assert r.status_code == 400
    body = _refused(r, R_INTERNAL)
    assert "ZeroDivisionError" in body["detail"]


def test_a_body_that_is_not_a_json_object_is_named(client):
    cat = _mk(client, "Household")
    for method, url in (("post", "/categories"),
                        ("post", "/categories/assign"),
                        ("put", f"/categories/sku/{SOAP[0]}"),
                        ("patch", f"/categories/{cat['category_id']}")):
        call = getattr(client, method)
        _refused(call(url, content=b"not json at all",
                      headers={"content-type": "application/json"}), R_BAD_BODY)
        _refused(call(url, json=["a", "list"]), R_BAD_BODY)
        _refused(call(url, json=None), R_BAD_BODY)


def test_no_shape_of_request_reaches_a_500(client):
    cat = _mk(client, "Household")
    junk = [
        {}, {"name": None}, {"name": {"a": 1}}, {"name": ["x"]},
        {"name": "ok", "parent_id": ["x"]}, {"name": "ok", "sort_order": []},
        {"category_id": 5}, {"category_id": {"x": 1}}, {"tags": {"a": 1}},
        {"tags": [None]}, {"assign": {"sku_id": SOAP[0]}},
        {"assign": [{"sku_id": None}]}, {"assign": [{"category_id": None}]},
        {"name": "   "}, {"name": chr(0)}, {"name": "x" * 5000},
        {"sort_order": 10 ** 40}, {"parent_id": "../../etc/passwd"},
    ]
    urls = [("post", "/categories"), ("post", "/categories/assign"),
            ("put", f"/categories/sku/{SOAP[0]}"),
            ("put", "/categories/sku/../../catalog"),
            ("patch", f"/categories/{cat['category_id']}"),
            ("patch", "/categories/..%2F..%2Fcatalog")]
    for method, url in urls:
        for body in junk:
            r = getattr(client, method)(url, json=body)
            assert r.status_code != 500, (method, url, body, r.text)
            if r.status_code not in (200, 404, 405, 422):
                assert r.json()["ok"] is False, (method, url, body, r.text)
    for url in ("/categories", "/categories/health", "/categories/suggest",
                "/categories/products?category=%2E%2E%2F",
                "/categories/products?tag=%00", "/categories/products?q=" + "z" * 900):
        r = client.get(url)
        assert r.status_code != 500, (url, r.text)


def test_this_module_holds_no_float_and_divides_only_paths():
    """Invariant 1, asserted against the file rather than only against a run.

    `tools/lint_no_float.py` scans this package for floats reaching money-named
    identifiers. This goes further for this one file: there is no float literal
    in it, no `float()` and no `round()`, and every `/` in it joins a path
    rather than dividing a number. `total / 100` and `price / 2` both fail the
    last loop; `shop_dir() / "categories.json"` does not.
    """
    import ast

    tree = ast.parse((Path(REPO) / "gawaah" / "categories.py")
                     .read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "float" not in called
    assert "round" not in called

    money_words = ("paise", "price", "amount", "total", "rupee")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        for side in (node.left, node.right):
            assert not (isinstance(side, ast.Constant)
                        and isinstance(side.value, (int, float))), ast.dump(node)
            name = getattr(side, "id", None) or getattr(side, "attr", "")
            assert not any(w in str(name).lower() for w in money_words), \
                ast.dump(node)


def test_the_router_is_mounted_bare_with_absolute_paths():
    """The orchestrator includes this router with no prefix."""
    paths = {(tuple(sorted(r.methods)), r.path)
             for r in categories.router.routes}
    assert paths == {
        (("GET",), "/categories"),
        (("POST",), "/categories"),
        (("GET",), "/categories/suggest"),
        (("GET",), "/categories/products"),
        (("GET",), "/categories/health"),
        (("POST",), "/categories/assign"),
        (("PUT",), "/categories/sku/{sku_id}"),
        (("PATCH",), "/categories/{category_id}"),
        (("DELETE",), "/categories/{category_id}"),
    }
    assert all(p.startswith("/categories") for _, p in paths)
    # The static paths must be declared ahead of the parameterised one, so a
    # GET added to /categories/{category_id} later cannot swallow /suggest.
    order = [r.path for r in categories.router.routes]
    assert order.index("/categories/suggest") < order.index(
        "/categories/{category_id}")


def test_a_tag_is_normalised_the_same_way_everywhere():
    assert categories.clean_tag("  Daily   Use ") == "daily use"
    with pytest.raises(CategoryRefused) as e:
        categories.clean_tag(" ")
    assert e.value.reason == R_BAD_TAG
    with pytest.raises(CategoryRefused) as e:
        categories.clean_tag("x" * (MAX_TAG + 1))
    assert e.value.reason == R_TOO_LONG
