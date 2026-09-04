"""gawaah/labels.py — the sticker sheet and the shelf talker.

A sheet of stickers is the one output of this program that leaves the machine
on paper and cannot be corrected by a redeploy, so these tests hold the claims
a printed sheet makes:

  1. THE GEOMETRY IS THE SHEET'S. Every grid fits a 210 x 297 mm page, no two
     labels overlap, and a label is placed at the millimetre the layout table
     says — checked on the rendered HTML, not on the table alone.
  2. THE PRICE IS THE CATALOGUE'S. The sticker prints the marked price and the
     talker prints the charged one, both read from the shop's own catalogue as
     integer paise. A price the browser asserts is compared and refused. A
     float in the catalogue is refused, not rounded.
  3. THE CODE NAMES THE PRODUCT. The symbol on paper is rasterised and DECODED
     back by OpenCV, and it reads `gawaah:<sku_id>` and nothing else. The
     module's source carries no UPI string and no gateway host.
  4. EVERY REFUSAL HAS A NAME, and no input of any shape produces a 500.
  5. EVERY PRINT IS WITNESSED on the module's own chain, in the shop directory,
     and `results/` is never touched.

The catalogue is redirected two ways for every test — the environment and the
till's cached handle — because a harness that honoured only one of them once
destroyed a live shop.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import labels, offers  # noqa: E402
from gawaah.labels import (  # noqa: E402
    LAYOUT_BY_ID,
    LAYOUTS,
    MAX_COPIES,
    MAX_LABELS,
    MAX_LINES,
    MAX_TALKER_COPIES,
    PAGE_H_MM,
    PAGE_W_MM,
    QUIET_MODULES,
    R_BAD_BODY,
    R_BAD_COPIES,
    R_BAD_ITEMS,
    R_BAD_SIZE,
    R_BAD_SKIP,
    R_EMPTY_CATALOGUE,
    R_INTERNAL,
    R_NO_CATALOGUE,
    R_NO_ITEMS,
    R_NO_LAYOUT,
    R_PRICE_DISAGREES,
    R_TOO_MANY_COPIES,
    R_TOO_MANY_LABELS,
    R_TOO_MANY_LINES,
    R_UNKNOWN_LAYOUT,
    R_UNKNOWN_SKU,
    TALKER_BY_ID,
    TALKER_SIZES,
)
from gawaah.ledger import verify  # noqa: E402
from tools import upload_app  # noqa: E402

# Prices with paise in them, so a bug that rounds shows in the second decimal.
BISCUIT = ("parle_g_200g", "Parle-G 200g", 2145)
# A name with markup in it: the sheet must escape it, not render it.
PICKLE = ("pickle_jar", "Mango pickle 500g <home-made> & sour", 12000)
ATTA = ("atta_loose_1kg", "Atta, loose, 1 kg", 4250)


# ------------------------------------------------------------------ rigging


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A shop that lives and dies with the test. Never `results/`."""
    shop = tmp_path / "shop"
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(tmp_path))
    for k in ("GAWAAH_OFFERS_FILE", "GAWAAH_SCAN_DIR", "GAWAAH_CODES_FILE"):
        monkeypatch.delenv(k, raising=False)
    upload_app.set_store_dir(shop)
    offers.set_offers_path(None)
    yield
    offers.set_offers_path(None)


@pytest.fixture()
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(labels.router)
    return TestClient(app)


@pytest.fixture()
def shop(client: TestClient) -> TestClient:
    """One product taught from a code, two from appearance with no code."""
    upload_app.do_enrol_code_only(b"", *BISCUIT, typed="8901234567890")
    store = upload_app.load_store()
    # Orthogonal vectors: the collision guard is real and refuses look-alikes.
    r1 = store.add_sku(PICKLE[0], PICKLE[1], PICKLE[2],
                       vectors=[[1.0, 0.0, 0.0, 0.0] * 4], footprint_mm=None)
    r2 = store.add_sku(ATTA[0], ATTA[1], ATTA[2],
                       vectors=[[0.0, 1.0, 0.0, 0.0] * 4], footprint_mm=None)
    assert r1.ok and r2.ok, (r1, r2)
    return client


@pytest.fixture()
def offer_on_pickle(shop: TestClient) -> TestClient:
    """₹5 off the pickle, live, so marked and charged differ by 500 paise."""
    offers.save_offers([offers.Offer(
        offer_id="off_000000000001", sku_id=PICKLE[0], kind=offers.KIND_FLAT,
        value=500, active=True, created_at="2026-01-01T00:00:00+00:00",
        label="Diwali")])
    return shop


def _plan(client: TestClient, **body) -> dict:
    r = client.post("/labels/plan", json=body)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["ok"] is True and doc["settles_money"] is False
    return doc


def _refused(r, reason: str, status: int = 400) -> dict:
    assert r.status_code == status, r.text
    doc = r.json()
    assert doc["ok"] is False
    assert doc["settles_money"] is False
    assert doc["reason"] == reason, doc
    assert isinstance(doc["detail"], str) and doc["detail"]
    return doc


def _positions(html: str) -> list[tuple[float, float]]:
    """Every label's (left, top) in mm, in document order."""
    return [(float(x), float(y)) for x, y in
            re.findall(r'class="lab" style="left:([\d.]+)mm;top:([\d.]+)mm"', html)]


def _items(*pairs) -> list[dict]:
    return [{"sku_id": s, "copies": n} for s, n in pairs]


# ---------------------------------------------------------------- geometry


def test_every_layout_fits_on_a4_with_nothing_off_the_page() -> None:
    for lay in LAYOUTS:
        assert lay.right_mm >= 0, lay.layout_id
        assert lay.bottom_mm >= 0, lay.layout_id
        assert lay.left_mm + lay.label_w_mm <= PAGE_W_MM
        assert lay.top_mm + lay.label_h_mm <= PAGE_H_MM
        assert lay.qr_mm > 0


def test_no_layout_lets_neighbouring_labels_overlap() -> None:
    for lay in LAYOUTS:
        assert lay.gap_x_mm >= 0, lay.layout_id
        assert lay.gap_y_mm >= 0, lay.layout_id


def test_sticker_grids_are_centred_on_the_sheet() -> None:
    """The Avery geometry is symmetric to a tenth of a millimetre. If a margin
    were typed wrong the sheet would be lopsided and this is what would say so."""
    for lay in LAYOUTS:
        if lay.cut_lines:
            continue
        assert abs(lay.right_mm - lay.left_mm) < 0.15, (lay.layout_id, lay.right_mm)
        assert abs(lay.bottom_mm - lay.top_mm) < 0.15, (lay.layout_id, lay.bottom_mm)


def test_layout_ids_are_unique() -> None:
    ids = [lay.layout_id for lay in LAYOUTS]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(LAYOUT_BY_ID)
    assert set(t.size_id for t in TALKER_SIZES) == set(TALKER_BY_ID)


def test_layouts_endpoint_states_every_grid_in_millimetres(client: TestClient) -> None:
    r = client.get("/labels/layouts")
    assert r.status_code == 200
    doc = r.json()
    assert doc["ok"] and doc["settles_money"] is False
    assert doc["count"] == len(LAYOUTS)
    for row in doc["layouts"]:
        for key in ("label_w_mm", "label_h_mm", "left_mm", "top_mm",
                    "pitch_x_mm", "pitch_y_mm", "right_mm", "bottom_mm", "qr_mm"):
            assert isinstance(row[key], (int, float)), (row["layout_id"], key)
        assert row["per_page"] == row["cols"] * row["rows"]
        assert "210 x 297" in row["page"]
    assert doc["limits"]["max_labels"] == MAX_LABELS
    assert [t["size_id"] for t in doc["talker_sizes"]] == ["a6", "a5", "a4"]


# ---------------------------------------------------------------- products


def test_products_lists_every_priced_product_with_the_marked_price(shop: TestClient) -> None:
    r = shop.get("/labels/products")
    assert r.status_code == 200
    doc = r.json()
    rows = {i["sku_id"]: i for i in doc["items"]}
    assert set(rows) == {BISCUIT[0], PICKLE[0], ATTA[0]}
    assert rows[BISCUIT[0]]["price_paise"] == 2145
    assert rows[BISCUIT[0]]["price_rupees"] == "21.45"
    assert rows[ATTA[0]]["price_rupees"] == "42.50"
    assert rows[PICKLE[0]]["qr_text"] == "gawaah:pickle_jar"
    assert rows[PICKLE[0]]["qr_png_url"] == "/qr/pickle_jar"
    assert doc["price_on_label"] == "marked"


def test_products_marks_which_already_carry_a_printed_code(shop: TestClient) -> None:
    rows = {i["sku_id"]: i for i in shop.get("/labels/products").json()["items"]}
    assert rows[BISCUIT[0]]["has_printed_code"] is True
    assert rows[BISCUIT[0]]["taught_with"] == "product_code_only"
    assert rows[PICKLE[0]]["has_printed_code"] is False
    assert rows[ATTA[0]]["has_printed_code"] is False
    assert shop.get("/labels/products").json()["without_printed_code"] == 2


def test_products_keeps_the_marked_price_when_an_offer_is_on(offer_on_pickle: TestClient) -> None:
    doc = offer_on_pickle.get("/labels/products").json()
    rows = {i["sku_id"]: i for i in doc["items"]}
    p = rows[PICKLE[0]]
    assert p["offer_today"] is True
    assert p["price_paise"] == 12000            # what the sticker prints
    assert p["charged_paise"] == 11500          # what the till charges today
    assert rows[BISCUIT[0]]["offer_today"] is False
    assert doc["offers_today"] == 1


def test_products_on_an_untaught_shop_is_empty_not_a_refusal(client: TestClient) -> None:
    r = client.get("/labels/products")
    assert r.status_code == 200
    assert r.json()["count"] == 0 and r.json()["items"] == []


# -------------------------------------------------------------------- plan


def test_plan_counts_labels_sheets_and_blank_cells(shop: TestClient) -> None:
    doc = _plan(shop, layout="a4_65", skip=3,
                items=_items((BISCUIT[0], 40), (PICKLE[0], 30)))
    assert doc["labels"] == 70
    assert doc["cells_per_page"] == 65
    assert doc["skipped"] == 3
    assert doc["pages"] == 2                    # 3 + 70 = 73 cells
    assert doc["blank_on_last_page"] == 130 - 73
    assert doc["price_on_label"] == "marked"
    assert doc["sheet_url"] == (
        "/labels/sheet?layout=a4_65&items=parle_g_200g:40,pickle_jar:30&skip=3")


def test_plan_with_an_exact_fit_leaves_no_blank_cells(shop: TestClient) -> None:
    doc = _plan(shop, layout="a4_8", items=_items((ATTA[0], 16)))
    assert doc["pages"] == 2 and doc["blank_on_last_page"] == 0


def test_plan_merges_a_repeated_sku_and_says_so(shop: TestClient) -> None:
    doc = _plan(shop, layout="a4_40",
                items=_items((PICKLE[0], 2), (ATTA[0], 1), (PICKLE[0], 5)))
    assert [(ln["sku_id"], ln["copies"]) for ln in doc["lines"]] == \
        [(PICKLE[0], 7), (ATTA[0], 1)]
    assert doc["labels"] == 8


def test_plan_refuses_a_layout_it_does_not_know(shop: TestClient) -> None:
    doc = _refused(shop.post("/labels/plan", json={
        "layout": "a4_1000", "items": _items((ATTA[0], 1))}), R_UNKNOWN_LAYOUT)
    for lay in LAYOUTS:
        assert lay.layout_id in doc["detail"]


def test_plan_refuses_a_missing_layout(shop: TestClient) -> None:
    _refused(shop.post("/labels/plan", json={"items": _items((ATTA[0], 1))}),
             R_NO_LAYOUT)


def test_plan_refuses_a_sku_the_shop_has_not_priced(shop: TestClient) -> None:
    doc = _refused(shop.post("/labels/plan", json={
        "layout": "a4_65", "items": _items(("ghee_tin", 1))}), R_UNKNOWN_SKU)
    assert "ghee_tin" in doc["detail"]


@pytest.mark.parametrize("bad", [0, -3, 2.5, True, "x", "", [1], {"n": 1}])
def test_plan_refuses_every_shape_of_bad_copy_count(shop: TestClient, bad) -> None:
    _refused(shop.post("/labels/plan", json={
        "layout": "a4_65", "items": [{"sku_id": ATTA[0], "copies": bad}]}),
        R_BAD_COPIES)


def test_plan_refuses_more_copies_than_one_run(shop: TestClient) -> None:
    _refused(shop.post("/labels/plan", json={
        "layout": "a4_65", "items": _items((ATTA[0], MAX_COPIES + 1))}),
        R_TOO_MANY_COPIES)
    # The merged total is checked too, not just each line.
    _refused(shop.post("/labels/plan", json={
        "layout": "a4_65",
        "items": _items((ATTA[0], MAX_COPIES), (ATTA[0], 1))}),
        R_TOO_MANY_COPIES)


def test_plan_refuses_more_labels_than_twenty_sheets(shop: TestClient) -> None:
    # Three products at the per-line cap is 1500 labels, past 1300.
    _refused(shop.post("/labels/plan", json={
        "layout": "a4_65",
        "items": _items((ATTA[0], MAX_COPIES), (PICKLE[0], MAX_COPIES),
                        (BISCUIT[0], MAX_COPIES))}),
        R_TOO_MANY_LABELS)


def test_resolve_refuses_more_products_than_one_run() -> None:
    known = {f"sku_{i:03d}": {"sku_id": f"sku_{i:03d}", "name": f"P{i}",
                              "price_paise": 100, "how": "appearance_only"}
             for i in range(MAX_LINES + 1)}
    with pytest.raises(labels.LabelsRefused) as ei:
        labels._resolve([{"sku_id": s, "copies": 1} for s in known], known)
    assert ei.value.reason == R_TOO_MANY_LINES


@pytest.mark.parametrize("bad", [65, -1, "abc", 2.5, True])
def test_plan_refuses_a_start_cell_off_the_sheet(shop: TestClient, bad) -> None:
    _refused(shop.post("/labels/plan", json={
        "layout": "a4_65", "items": _items((ATTA[0], 1)), "skip": bad}),
        R_BAD_SKIP)


def test_plan_checks_an_asserted_price_and_refuses_disagreement(shop: TestClient) -> None:
    doc = _refused(shop.post("/labels/plan", json={
        "layout": "a4_65",
        "items": [{"sku_id": ATTA[0], "copies": 1, "price_paise": 4200}]}),
        R_PRICE_DISAGREES)
    assert "4250" in doc["detail"]


def test_plan_accepts_an_asserted_price_that_agrees_and_still_uses_its_own(
        offer_on_pickle: TestClient) -> None:
    # The marked price agrees; the charged price today is 11500 and would not.
    doc = _plan(offer_on_pickle, layout="a4_65",
                items=[{"sku_id": PICKLE[0], "copies": 1, "price_paise": 12000}])
    assert doc["lines"][0]["price_paise"] == 12000
    _refused(offer_on_pickle.post("/labels/plan", json={
        "layout": "a4_65",
        "items": [{"sku_id": PICKLE[0], "copies": 1, "price_paise": 11500}]}),
        R_PRICE_DISAGREES)


def test_plan_refuses_a_body_that_is_not_an_object(shop: TestClient) -> None:
    _refused(shop.post("/labels/plan", json=[1, 2]), R_BAD_BODY)
    _refused(shop.post("/labels/plan", content=b"not json",
                       headers={"Content-Type": "application/json"}), R_BAD_BODY)
    _refused(shop.post("/labels/plan", json={"layout": "a4_65", "items": "x"}),
             R_BAD_ITEMS)
    _refused(shop.post("/labels/plan", json={"layout": "a4_65", "items": [7]}),
             R_BAD_ITEMS)


def test_plan_refuses_an_empty_run(shop: TestClient) -> None:
    _refused(shop.post("/labels/plan", json={"layout": "a4_65", "items": []}),
             R_NO_ITEMS)
    _refused(shop.post("/labels/plan", json={"layout": "a4_65"}), R_NO_ITEMS)


def test_plan_reports_module_size_from_the_grid(shop: TestClient) -> None:
    doc = _plan(shop, layout="a4_65", items=_items((PICKLE[0], 1)))
    ln = doc["lines"][0]
    n = ln["qr_modules"]
    assert n == len(labels._qr_matrix("gawaah:pickle_jar"))
    lay = LAYOUT_BY_ID["a4_65"]
    assert ln["module_mm"] == round(lay.qr_mm / (n + 2 * QUIET_MODULES), 3)


def test_plan_on_an_untaught_shop_refuses_by_name(client: TestClient) -> None:
    _refused(client.post("/labels/plan", json={
        "layout": "a4_65", "items": _items((ATTA[0], 1))}), R_EMPTY_CATALOGUE)


# ------------------------------------------------------------------- sheet


def test_sheet_is_self_contained(shop: TestClient) -> None:
    doc = _plan(shop, layout="a4_65", items=_items((BISCUIT[0], 40), (PICKLE[0], 30)))
    r = shop.get(doc["sheet_url"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    low = html.lower()
    assert "<script" not in low
    assert "<link" not in low
    assert "<img" not in low
    assert not re.search(r"""(src|href)=["'](https?:)?//""", low)
    assert "@page{size:210.00mm 297.00mm;margin:0}" in html
    assert html.count("<symbol ") == 2              # one per distinct product
    assert html.count("<use ") == 70                 # one per label
    assert html.count('class="page"') == 2
    assert "70 labels on 2 sheets of A4" in html


def test_sheet_places_labels_at_the_grids_millimetres(shop: TestClient) -> None:
    lay = LAYOUT_BY_ID["a4_65"]
    html = shop.get("/labels/sheet?layout=a4_65&items=atta_loose_1kg:7").text
    pos = _positions(html)
    assert len(pos) == 7
    assert pos[0] == (4.65, 10.7)
    assert pos[1] == (round(4.65 + 40.64, 2), 10.7)
    assert pos[4] == (round(4.65 + 4 * 40.64, 2), 10.7)
    assert pos[5] == (4.65, round(10.7 + 21.2, 2))   # wraps to the second row
    assert pos[5][1] - pos[0][1] == pytest.approx(lay.pitch_y_mm, abs=0.01)


def test_sheet_starts_at_the_skipped_cell(shop: TestClient) -> None:
    html = shop.get("/labels/sheet?layout=a4_40&items=atta_loose_1kg:2&skip=39").text
    pos = _positions(html)
    assert len(pos) == 2
    # Cell 39 is the last on a 4 x 10 sheet; the next label opens a new page.
    assert pos[0] == (round(9.75 + 3 * 48.25, 2), round(21.5 + 9 * 25.4, 2))
    assert pos[1] == (9.75, 21.5)
    assert html.count('class="page"') == 2


def test_sheet_prints_the_marked_price_not_the_offer(offer_on_pickle: TestClient) -> None:
    doc = _plan(offer_on_pickle, layout="a4_24", items=_items((PICKLE[0], 3)))
    assert doc["offers_today"] == [PICKLE[0]]
    html = offer_on_pickle.get(doc["sheet_url"]).text
    assert len(re.findall(r'<div class="p"[^>]*>₹120\.00</div>', html)) == 3
    assert "115.00" not in html
    assert "Today's offers are not on a sticker" in html


def test_sheet_escapes_a_product_name(shop: TestClient) -> None:
    html = shop.get("/labels/sheet?layout=a4_21&items=pickle_jar:1").text
    assert "<home-made>" not in html
    assert "&lt;home-made&gt; &amp; sour" in html


def test_sheet_draws_cut_lines_only_on_plain_paper(shop: TestClient) -> None:
    plain = shop.get("/labels/sheet?layout=a4_cut_40&items=atta_loose_1kg:1").text
    sticker = shop.get("/labels/sheet?layout=a4_65&items=atta_loose_1kg:1").text
    assert "dashed" in plain
    assert "dashed" not in sticker


@pytest.mark.parametrize("query, reason", [
    ("layout=a4_65", R_NO_ITEMS),
    ("layout=a4_65&items=", R_NO_ITEMS),
    ("layout=a4_65&items=bad%20sku!:1", R_BAD_ITEMS),
    ("layout=a4_65&items=../etc:1", R_BAD_ITEMS),
    ("layout=a4_65&items=atta_loose_1kg:x", R_BAD_COPIES),
    ("layout=a4_65&items=atta_loose_1kg:0", R_BAD_COPIES),
    ("layout=a4_65&items=ghee_tin:1", R_UNKNOWN_SKU),
    ("layout=nope&items=atta_loose_1kg:1", R_UNKNOWN_LAYOUT),
    ("items=atta_loose_1kg:1", R_NO_LAYOUT),
    ("layout=a4_65&items=atta_loose_1kg:1&skip=65", R_BAD_SKIP),
    ("layout=a4_65&items=atta_loose_1kg:1&skip=abc", R_BAD_SKIP),
])
def test_sheet_refuses_by_name_on_get(shop: TestClient, query: str, reason: str) -> None:
    r = shop.get(f"/labels/sheet?{query}")
    _refused(r, reason)
    assert r.headers["content-type"].startswith("application/json")


def test_parse_items_query_tolerates_whitespace_and_empty_tokens() -> None:
    got = labels._parse_items_query("  parle_g_200g : 2 , , pickle_jar ,atta_loose_1kg:12,")
    assert got == [{"sku_id": "parle_g_200g", "copies": "2"},
                   {"sku_id": "pickle_jar"},
                   {"sku_id": "atta_loose_1kg", "copies": "12"}]


def test_sheet_is_witnessed_on_its_own_chain(shop: TestClient, tmp_path: Path) -> None:
    r = shop.get("/labels/sheet?layout=a4_65&items=pickle_jar:3,atta_loose_1kg:2&skip=1")
    assert r.status_code == 200
    assert r.headers["x-gawaah-witnessed"] == "true"
    chain = tmp_path / "shop" / "labels.audit.jsonl"
    assert chain.exists()
    assert labels.audit_path() == chain
    ok, n, head, err = verify(chain)
    assert ok and n == 1 and err is None
    rec = json.loads(chain.read_text().splitlines()[0])
    assert rec["module"] == "labels" and rec["event"] == "labels.sheet"
    assert rec["layout"] == "a4_65" and rec["labels"] == 5 and rec["pages"] == 1
    assert rec["skip"] == 1 and rec["minted"] is False
    assert rec["lines"] == [
        {"sku_id": "pickle_jar", "copies": 3, "unit_paise": 12000},
        {"sku_id": "atta_loose_1kg", "copies": 2, "unit_paise": 4250}]
    # The line's hash is on the sheet, so paper can be matched to the run.
    assert f"witness {head[:12]}" in r.text
    # A second print extends the chain rather than replacing it.
    shop.get("/labels/sheet?layout=a4_8&items=atta_loose_1kg:1")
    ok, n, _, _ = verify(chain)
    assert ok and n == 2


def test_nothing_touches_results(shop: TestClient, tmp_path: Path) -> None:
    live = REPO / "results" / "audit.jsonl"
    before = live.stat() if live.exists() else None
    shop.get("/labels/sheet?layout=a4_65&items=atta_loose_1kg:5")
    shop.get("/labels/talker/atta_loose_1kg")
    after = live.stat() if live.exists() else None
    assert (before is None) == (after is None)
    if before is not None and after is not None:
        assert (before.st_size, before.st_mtime) == (after.st_size, after.st_mtime)
    assert str(labels.audit_path()).startswith(str(tmp_path))
    assert not (REPO / "results" / "shop" / "labels.audit.jsonl").exists() or \
        labels.audit_path() != REPO / "results" / "shop" / "labels.audit.jsonl"


# -------------------------------------------------------------------- code


def test_the_code_on_paper_decodes_to_the_product_id_and_nothing_else() -> None:
    """Rasterise the very matrix the SVG is drawn from and read it back."""
    import cv2
    import numpy as np

    m = labels._qr_matrix("gawaah:pickle_jar")
    n = len(m)
    scale = 8
    side = (n + 2 * QUIET_MODULES) * scale
    img = np.full((side, side), 255, np.uint8)
    for y, row in enumerate(m):
        for x, dark in enumerate(row):
            if dark:
                y0 = (y + QUIET_MODULES) * scale
                x0 = (x + QUIET_MODULES) * scale
                img[y0:y0 + scale, x0:x0 + scale] = 0
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert text == "gawaah:pickle_jar"


def test_symbol_path_covers_every_dark_module_and_nothing_outside() -> None:
    m = labels._qr_matrix("gawaah:atta_loose_1kg")
    n = len(m)
    svg = labels._qr_symbol("q-atta_loose_1kg", m)
    runs = re.findall(r"M(\d+) (\d+)h(\d+)v1h-(\d+)z", svg)
    assert runs, svg
    drawn = 0
    for x, y, w, w2 in runs:
        assert w == w2
        x, y, w = int(x), int(y), int(w)
        assert QUIET_MODULES <= x and x + w <= n + QUIET_MODULES
        assert QUIET_MODULES <= y < n + QUIET_MODULES
        drawn += w
    assert drawn == sum(1 for row in m for v in row if v)
    assert f'viewBox="0 0 {n + 8} {n + 8}"' in svg
    assert 'shape-rendering="crispEdges"' in svg


def test_no_forgery_primitive_in_the_source() -> None:
    src = (REPO / "gawaah" / "labels.py").read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]          # past the module docstring
    for needle in ("upi:", "pa=", "rzp.io", "razorpay.com", "short_url",
                   "payment_link"):
        assert needle not in body.lower(), needle


# ------------------------------------------------------------------ talker


@pytest.mark.parametrize("size, page, per_page", [
    ("a6", "297.00mm 210.00mm", 4),
    ("a5", "210.00mm 297.00mm", 2),
    ("a4", "297.00mm 210.00mm", 1),
])
def test_talker_sizes_are_cut_from_an_a4_sheet(shop: TestClient, size: str,
                                               page: str, per_page: int) -> None:
    r = shop.get(f"/labels/talker/atta_loose_1kg?size={size}")
    assert r.status_code == 200, r.text
    html = r.text
    assert f"@page{{size:{page};margin:0}}" in html
    assert html.count('class="tk"') == per_page          # fills one sheet by default
    assert html.count('class="page"') == 1
    assert "<script" not in html.lower()
    assert "dashed" in html                               # the cut line
    assert len(re.findall(r'<div class="fig"[^>]*>₹42\.50</div>', html)) == per_page


def test_talker_shows_the_charged_price_with_the_marked_struck_through(
        offer_on_pickle: TestClient) -> None:
    html = offer_on_pickle.get("/labels/talker/pickle_jar?size=a4").text
    assert '<div class="was">₹120.00</div>' in html
    assert re.search(r'<div class="fig"[^>]*>₹115\.00</div>', html)
    assert "offer price, printed" in html


def test_talker_without_an_offer_shows_one_price(shop: TestClient) -> None:
    html = shop.get("/labels/talker/pickle_jar?size=a4").text
    assert 'class="was"' not in html
    assert re.search(r'<div class="fig"[^>]*>₹120\.00</div>', html)
    assert "No offer is on today" in html


def test_talker_copies_spill_onto_a_second_sheet(shop: TestClient) -> None:
    html = shop.get("/labels/talker/atta_loose_1kg?size=a6&copies=5").text
    assert html.count('class="tk"') == 5
    assert html.count('class="page"') == 2


@pytest.mark.parametrize("query, reason", [
    ("copies=0", R_BAD_COPIES),
    (f"copies={MAX_TALKER_COPIES + 1}", R_BAD_COPIES),
    ("copies=abc", R_BAD_COPIES),
    ("size=a3", R_BAD_SIZE),
])
def test_talker_refuses_bad_copies_and_sizes(shop: TestClient, query: str, reason: str) -> None:
    _refused(shop.get(f"/labels/talker/atta_loose_1kg?{query}"), reason)


def test_talker_unknown_sku_is_404_and_a_bad_id_is_400(shop: TestClient) -> None:
    _refused(shop.get("/labels/talker/ghee_tin"), R_UNKNOWN_SKU, status=404)
    # A bad character inside one path segment reaches the route; a %2F would
    # be decoded to a slash by the framework and never arrive here.
    _refused(shop.get("/labels/talker/bad%20sku!"), R_BAD_ITEMS)


def test_talker_is_witnessed(offer_on_pickle: TestClient, tmp_path: Path) -> None:
    r = offer_on_pickle.get("/labels/talker/pickle_jar?size=a5&copies=2")
    assert r.status_code == 200 and r.headers["x-gawaah-witnessed"] == "true"
    chain = tmp_path / "shop" / "labels.audit.jsonl"
    ok, n, _, _ = verify(chain)
    assert ok and n == 1
    rec = json.loads(chain.read_text().splitlines()[0])
    assert rec["event"] == "labels.talker"
    assert rec["sku_id"] == "pickle_jar" and rec["size"] == "a5" and rec["copies"] == 2
    assert rec["charged_paise"] == 11500 and rec["marked_paise"] == 12000
    assert rec["offer_today"] is True and rec["minted"] is False


# ------------------------------------------------------------------ health


def test_health_verifies_the_chain(shop: TestClient, tmp_path: Path) -> None:
    doc = shop.get("/labels/health").json()
    assert doc["ok"] and doc["exists"] is False and doc["lines"] == 0
    assert doc["chain_ok"] is True and doc["qr_encoder"] is True
    assert doc["audit_file"] == str(tmp_path / "shop" / "labels.audit.jsonl")
    shop.get("/labels/sheet?layout=a4_65&items=atta_loose_1kg:1")
    doc = shop.get("/labels/health").json()
    assert doc["exists"] is True and doc["lines"] == 1 and doc["chain_ok"] is True
    assert doc["layouts"] == len(LAYOUTS) and doc["qr_prefix"] == "gawaah:"


# ------------------------------------------------------------------- money


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def test_every_paise_in_every_response_is_an_int(offer_on_pickle: TestClient) -> None:
    docs = [
        offer_on_pickle.get("/labels/products").json(),
        _plan(offer_on_pickle, layout="a4_14",
              items=_items((PICKLE[0], 2), (BISCUIT[0], 1))),
        offer_on_pickle.get("/labels/layouts").json(),
    ]
    seen = 0
    for doc in docs:
        for path, v in _walk(doc):
            if "paise" in path:
                seen += 1
                assert isinstance(v, int) and not isinstance(v, bool), (path, v)
    assert seen >= 8


def test_a_float_price_in_the_catalogue_is_refused_not_rounded(
        shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(labels, "catalogue", lambda: {
        "x": {"sku_id": "x", "name": "X", "price_paise": 21.45,
              "how": "appearance_only"}})
    _refused(shop.post("/labels/plan", json={
        "layout": "a4_65", "items": _items(("x", 1))}), R_NO_CATALOGUE)
    _refused(shop.get("/labels/sheet?layout=a4_65&items=x:1"), R_NO_CATALOGUE)
    _refused(shop.get("/labels/talker/x"), R_NO_CATALOGUE)
    _refused(shop.get("/labels/products"), R_NO_CATALOGUE)


def test_a_crash_is_a_400_with_a_name(shop: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("the renderer fell over")

    monkeypatch.setattr(labels, "_render_sheet", boom)
    monkeypatch.setattr(labels, "_render_talker", boom)
    doc = _refused(shop.get("/labels/sheet?layout=a4_65&items=atta_loose_1kg:1"),
                   R_INTERNAL)
    assert "the renderer fell over" in doc["detail"]
    _refused(shop.get("/labels/talker/atta_loose_1kg"), R_INTERNAL)
