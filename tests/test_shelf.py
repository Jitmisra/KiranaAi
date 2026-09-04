"""gawaah/shelf.py — count what is facing out on a shelf.

The claims this suite makes checkable, because each is one a demo can fake:

  1. A FACING IS COUNTED, NOT GUESSED. Two red packets and one blue on a
     synthetic shelf come back as exactly {red: 2, blue: 1}, and a colour the
     shop never taught comes back as an UNNAMED region with its crop — never
     as a price, never as the nearest thing.
  2. THE GAP IS A FACT WITH A DIRECTION. The shelf showing more than the stock
     figure is named as the figure being wrong; fewer is named as "cannot be
     told from a photograph"; never counted is named as an absence. The
     figure itself is gawaah/stock.py's own, never re-derived here.
  3. NOTHING HERE IS MONEY AND NOTHING HERE WRITES STOCK. No response carries
     a paise; a shelf read leaves the stock baseline exactly where it was.
  4. EVERY READ IS ON THE MODULE'S OWN CHAIN, verifiable, with no pixels in
     it, and the money service's ledger is never touched.
  5. EVERY REFUSAL HAS A NAME and nothing produces a 500.

The embedder is a STUB that reads a crop's mean colour and returns a one-hot
vector, so recognition here is exact and deterministic and the suite runs
without the model file. The detector, the identifier, the gates, the teaching
path and the stock derivation are all real.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gawaah import manage, shelf, stock  # noqa: E402
from gawaah.ledger import verify  # noqa: E402
from tools import upload_app  # noqa: E402


# ------------------------------------------------------------------ colours
#
# Every product on the synthetic shelf is a flat colour, and the stub embedder
# maps a crop's mean colour to the nearest of these and answers one-hot. Two
# packets of one colour are one product seen twice, which is what a facing is.

PALETTE: dict[str, tuple[int, int, int]] = {          # BGR
    "red": (40, 40, 210),
    "blue": (200, 90, 40),
    "green": (60, 170, 70),
    "yellow": (40, 200, 230),
    "purple": (170, 60, 150),
    "teal": (150, 120, 20),
}
DIM = 8
INDEX = {name: i for i, name in enumerate(PALETTE)}

RED = ("red_pack", "Red packet", 1250)
BLUE = ("blue_pack", "Blue packet", 3990)
BG = (120, 140, 160)


def onehot(colour: str) -> np.ndarray:
    v = np.zeros(DIM, np.float64)
    v[INDEX[colour]] = 1.0
    return v


def stub_embed(crop: np.ndarray) -> np.ndarray:
    """Mean BGR -> nearest palette colour -> one-hot. Exact and deterministic."""
    mean = np.asarray(crop, np.float64).reshape(-1, 3).mean(axis=0)
    best = min(PALETTE, key=lambda k: float(np.linalg.norm(mean - np.array(PALETTE[k]))))
    return onehot(best)


def shelf_frame(packs: list[tuple[str, int, int, int, int]],
                seed: int = 3) -> np.ndarray:
    """A 1280x720 shelf: flat packets on a noisy grey-blue surface."""
    rng = np.random.default_rng(seed)
    h, w = 720, 1280
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = BG
    img = np.clip(img.astype(np.int16) + rng.integers(-8, 8, (h, w, 3)),
                  0, 255).astype(np.uint8)
    for colour, x, y, pw, ph in packs:
        img[y:y + ph, x:x + pw] = PALETTE[colour]
    return img


def png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


TWO_RED_ONE_BLUE = [("red", 100, 200, 220, 300), ("red", 420, 200, 220, 300),
                    ("blue", 760, 200, 220, 300)]


# ----------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Nothing in this suite may see, let alone write, results/.

    Both overrides — the environment AND the till's cached handle — because a
    harness once destroyed the live catalogue by honouring only one of them.
    The embedder is stubbed for every test and restored afterwards.
    """
    data = tmp_path / "data"
    shop = data / "shop"
    shop.mkdir(parents=True)
    monkeypatch.setenv("GAWAAH_DATA_DIR", str(data))
    monkeypatch.setenv("GAWAAH_SHOP_DIR", str(shop))
    monkeypatch.delenv("GAWAAH_SCAN_DIR", raising=False)
    monkeypatch.delenv("GAWAAH_CODES_FILE", raising=False)
    upload_app.set_store_dir(shop)
    upload_app._DEPS["embed"] = stub_embed
    manage._CHAIN_CACHE.clear()
    shelf.forget_held()
    yield
    upload_app._DEPS["embed"] = None
    manage._CHAIN_CACHE.clear()
    shelf.forget_held()


@pytest.fixture
def client() -> TestClient:
    """The router mounted the way the orchestrator will mount it: bare."""
    app = FastAPI()
    app.include_router(shelf.router)
    return TestClient(app)


def teach(sku: tuple[str, str, int], colour: str,
          vector: np.ndarray | None = None) -> None:
    """One appearance-only product, straight into the till's sidecar."""
    sku_id, name, price = sku
    upload_app._ao_put(sku_id, name, price,
                       [onehot(colour) if vector is None else vector], None)


def count(client: TestClient, img: np.ndarray, **fields) -> dict:
    data = {"yolo": "0", **fields}
    r = client.post("/shelf/count",
                    files={"image": ("shelf.png", png(img), "image/png")},
                    data=data)
    return r.json()


def facing(body: dict, sku_id: str) -> dict:
    return next(f for f in body["facings"] if f["sku_id"] == sku_id)


def count_at(sku_id: str, units: int, when: str | None = None) -> None:
    """A stock baseline through manage's own writer, as the Stock screen does."""
    when = when or (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    stock_map, _ = manage.read_opening_stock()
    stock_map[sku_id] = {"units": units, "counted_at": when}
    manage.write_opening_stock(stock_map)


def walk(node, where: str = "$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{where}[{i}]")
    else:
        yield where, node


# ===================================================== 1. counting facings

def test_two_red_and_one_blue_are_two_facings_and_one(client: TestClient) -> None:
    """The whole feature in one test: facings per product, nothing invented."""
    teach(RED, "red")
    teach(BLUE, "blue")
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    assert body["ok"] is True
    assert body["counts"] == {
        "regions_seen": 3, "named": 3, "unnamed": 0, "products": 2,
        "shelf_exceeds_figure": 0,
        # Named BY THE CAMERA, all three: `by_hand` counts the ones a person
        # typed, and a read straight off a photograph has none.
        "by_hand": 0,
        "rejected": 0, "same_packet": 0, "corrections": 0,
        # Both taught products are on this shelf, so nothing is missing.
        "missing": 0, "gone": 0,
    }
    assert facing(body, "red_pack")["facings"] == 2
    assert facing(body, "blue_pack")["facings"] == 1
    assert len(facing(body, "red_pack")["boxes"]) == 2
    # Most facings first, so the product that fills the shelf heads the list.
    assert [f["sku_id"] for f in body["facings"]] == ["red_pack", "blue_pack"]


def test_the_boxes_sit_on_the_packets(client: TestClient) -> None:
    """A facing is a box the shopkeeper can see drawn on his own shelf."""
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 300, 150, 220, 300)]))
    [box] = facing(body, "red_pack")["boxes"]
    x, y, w, h = box
    assert abs(x - 300) <= 12 and abs(y - 150) <= 12
    assert abs(w - 220) <= 24 and abs(h - 300) <= 24


def test_a_colour_the_shop_never_taught_is_an_unnamed_region_with_its_crop(
        client: TestClient) -> None:
    """Invariant 7. Something is there; it is reported, never priced, never
    named as the nearest thing."""
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                      ("purple", 500, 200, 220, 300)]))
    assert body["counts"]["named"] == 1
    assert body["counts"]["unnamed"] == 1
    [u] = body["unnamed"]
    assert u["reason"] == "below_similarity"
    assert u["top1"] == 0.0
    assert u["region"] in (1, 2)
    thumb = base64.b64decode(u["crop_png_b64"])
    img = cv2.imdecode(np.frombuffer(thumb, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and max(img.shape[:2]) <= shelf.CROP_THUMB_PX
    # The crop IS the purple packet, so the shopkeeper sees what was missed.
    assert np.linalg.norm(img.reshape(-1, 3).mean(axis=0)
                          - np.array(PALETTE["purple"])) < 30


def test_regions_are_numbered_left_to_right_and_the_numbers_are_on_the_picture(
        client: TestClient) -> None:
    teach(RED, "red")
    body = count(client, shelf_frame([("purple", 800, 200, 220, 300),
                                      ("red", 100, 200, 220, 300),
                                      ("purple", 450, 200, 220, 300)]))
    order = [(r["region"], r["box"][0]) for r in body["regions"]]
    assert [n for n, _x in order] == [1, 2, 3]
    assert [x for _n, x in order] == sorted(x for _n, x in order)
    assert facing(body, "red_pack")["regions"] == [1]
    assert [u["region"] for u in body["unnamed"]] == [2, 3]


def test_an_empty_shelf_is_a_result_not_a_refusal(client: TestClient) -> None:
    teach(RED, "red")
    body = count(client, shelf_frame([]))
    assert body["ok"] is True
    assert body["empty_shelf"] is True
    assert body["counts"]["regions_seen"] == 0
    assert body["facings"] == [] and body["unnamed"] == []


def test_the_same_frame_read_twice_gives_the_same_facings(client: TestClient) -> None:
    """Determinism. A count that changes between two presses is not a count."""
    teach(RED, "red")
    teach(BLUE, "blue")
    img = shelf_frame(TWO_RED_ONE_BLUE)
    a, b = count(client, img), count(client, img)
    strip = lambda d: {f["sku_id"]: (f["facings"], f["boxes"]) for f in d["facings"]}
    assert strip(a) == strip(b)
    assert a["counts"] == b["counts"]


def test_touching_packets_read_as_one_facing_and_that_is_a_stated_limit(
        client: TestClient) -> None:
    """AN HONEST FAILURE, ASSERTED. Below a finger's width the detector's masks
    fuse, nothing can separate them, and the response says so in words."""
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                      ("red", 320, 200, 220, 300)]))  # gap 0
    assert facing(body, "red_pack")["facings"] == 1
    assert "finger" in body["limits"]["touching_packets"]


def test_packets_a_fingers_width_apart_are_separate_facings(client: TestClient) -> None:
    """The floor, MEASURED on this suite's own flat packets and pinned.

    Swept before this was written: two flat same-colour packets 10, 16 and
    20 px apart come back as ONE region; 24, 30 and 40 px come back as two.
    The detector's own suite measures 20 px on textured product photographs;
    a flat colour has no printing to break its mask and needs a little more.
    Both numbers are a finger's width at 1280 px, which is what the page says.
    """
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                      ("red", 344, 200, 220, 300)]))  # gap 24
    assert facing(body, "red_pack")["facings"] == 2
    body = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                      ("red", 340, 200, 220, 300)]))  # gap 20
    assert facing(body, "red_pack")["facings"] == 1


def test_the_yolo_proposer_can_be_left_out_and_the_answer_is_reported(
        client: TestClient) -> None:
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 300, 150, 220, 300)]))
    assert body["use_yolo"] is False
    assert facing(body, "red_pack")["facings"] == 1


def test_with_the_optional_model_in_play_the_facings_do_not_change(
        client: TestClient) -> None:
    """YOLO adds recall on COCO objects and must never subtract a packet."""
    teach(RED, "red")
    teach(BLUE, "blue")
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")
    assert body["use_yolo"] is True
    assert facing(body, "red_pack")["facings"] == 2
    assert facing(body, "blue_pack")["facings"] == 1


# ================================================= 2. facings beside stock

def test_fewer_visible_than_the_figure_concludes_nothing(client: TestClient) -> None:
    teach(RED, "red")
    count_at("red_pack", 10)
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    st = facing(body, "red_pack")["stock"]
    assert st["on_hand_units"] == 10
    assert st["difference"] == -8
    assert st["shelf_exceeds_figure"] is False
    assert st["verdict"] == "face_below_figure"
    assert "behind the front row" in st["sentence"]
    assert "photograph" in st["sentence"]


def test_more_visible_than_the_figure_means_the_figure_is_wrong(
        client: TestClient) -> None:
    """The ONE decisive direction. The shop cannot hold fewer than it shows."""
    teach(RED, "red")
    count_at("red_pack", 1)
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    st = facing(body, "red_pack")["stock"]
    assert st["difference"] == 1
    assert st["shelf_exceeds_figure"] is True
    assert st["verdict"] == "shelf_exceeds_figure"
    assert "figure is wrong" in st["sentence"]
    assert body["counts"]["shelf_exceeds_figure"] == 1


def test_a_matching_figure_is_consistent_and_says_it_is_not_proof(
        client: TestClient) -> None:
    teach(RED, "red")
    count_at("red_pack", 2)
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    st = facing(body, "red_pack")["stock"]
    assert st["difference"] == 0
    assert st["verdict"] == "face_matches_figure"
    assert "not proof" in st["sentence"]


def test_a_product_never_counted_has_no_figure_and_no_gap(client: TestClient) -> None:
    """An absence, never a zero. A zero would be a claim about a shelf."""
    teach(RED, "red")
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    st = facing(body, "red_pack")["stock"]
    assert st["on_hand_units"] is None
    assert st["difference"] is None
    assert st["basis"] == "never_counted"
    assert st["verdict"] == "never_counted"
    assert "never been counted" in st["sentence"]


def test_the_figure_is_stocks_own_derivation_movements_included(
        client: TestClient) -> None:
    """Not a second on-hand. A delivery booked on the Stock screen moves the
    figure this screen compares against, because it is the same figure."""
    teach(RED, "red")
    count_at("red_pack", 1)
    sapp = FastAPI()
    sapp.include_router(stock.router)
    r = TestClient(sapp).post("/stock/red_pack/in",
                              json={"units": 4, "reason": "delivery"})
    assert r.json()["on_hand_units"] == 5
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    st = facing(body, "red_pack")["stock"]
    assert st["on_hand_units"] == 5
    assert st["difference"] == -3
    assert "4 in" in st["derivation"]


def test_when_the_stock_derivation_is_unavailable_the_count_still_stands(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing figure is a fact to report, not a reason to refuse a count
    and not a licence to compare against a zero nobody derived."""
    teach(RED, "red")

    def _boom():
        raise RuntimeError("stock chain unreadable")

    monkeypatch.setattr(stock, "stock_rows", _boom)
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    assert body["ok"] is True
    assert facing(body, "red_pack")["facings"] == 2
    assert body["stock_figures"]["available"] is False
    assert "stock chain unreadable" in body["stock_figures"]["detail"]
    st = facing(body, "red_pack")["stock"]
    assert st["verdict"] == "no_figure_available"
    assert st["on_hand_units"] is None and st["difference"] is None


def test_a_shelf_read_never_moves_the_stock_baseline(client: TestClient) -> None:
    """A facing count is not a count. The baseline file is byte-identical
    after a read that saw a different number."""
    teach(RED, "red")
    count_at("red_pack", 7)
    before = manage.stock_path().read_bytes()
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    assert facing(body, "red_pack")["facings"] == 2
    assert manage.stock_path().read_bytes() == before
    assert "never written into the stock figure" in body["limits"]["not_a_stock_count"]


# ======================================================== 3. codes and folds

def test_a_code_inside_a_packets_region_names_that_facing_once() -> None:
    """A barcode on the corner of a packet is one packet, not two facings."""
    items = [
        {"box": [100, 100, 300, 400], "how": "appearance", "found_by": "contour",
         "sku_id": "guess", "name": "Guess", "price_paise": 100,
         "reason": "recognised_by_appearance"},
        {"box": [120, 120, 60, 60], "how": "code", "code": "8901234567890",
         "sku_id": "truth", "name": "Truth", "price_paise": 200,
         "reason": "read_a_printed_code"},
    ]
    out = shelf._fold_codes_into_regions(items)
    assert len(out) == 1
    assert out[0]["sku_id"] == "truth"          # a measurement beats an opinion
    assert out[0]["how"] == "code"
    assert out[0]["appearance_said"] == "guess"  # and the disagreement is kept
    assert out[0]["box"] == [100, 100, 300, 400]


def test_a_code_that_is_not_inside_any_region_is_its_own_facing() -> None:
    items = [
        {"box": [100, 100, 300, 400], "how": "appearance", "sku_id": "a",
         "name": "A", "price_paise": 1, "reason": "recognised_by_appearance"},
        {"box": [900, 100, 200, 200], "how": "code", "sku_id": "b", "name": "B",
         "price_paise": 1, "reason": "read_a_printed_code"},
    ]
    out = shelf._fold_codes_into_regions(items)
    assert [i["sku_id"] for i in out] == ["a", "b"]


def test_an_unbound_code_inside_a_region_does_not_erase_the_regions_name() -> None:
    items = [
        {"box": [100, 100, 300, 400], "how": "appearance", "sku_id": "a",
         "name": "A", "price_paise": 1, "reason": "recognised_by_appearance"},
        {"box": [110, 110, 50, 50], "how": "code", "code": "000", "sku_id": None,
         "name": None, "price_paise": None, "reason": "code_not_bound"},
    ]
    out = shelf._fold_codes_into_regions(items)
    assert len(out) == 1 and out[0]["sku_id"] == "a"
    assert out[0]["code"] == "000" and out[0]["code_reason"] == "code_not_bound"


def test_a_real_printed_code_on_a_packet_is_read_and_counted_once(
        client: TestClient) -> None:
    """End to end through zxing: a `gawaah:` sticker in the corner of a packet
    the shop taught by sight. One facing, named by the code."""
    teach(RED, "red")
    img = shelf_frame([("red", 300, 120, 420, 460)])
    enc = cv2.QRCodeEncoder.create()
    q = enc.encode("gawaah:red_pack")
    q = (q * 255).astype(np.uint8) if q.max() <= 1 else q.astype(np.uint8)
    q = cv2.cvtColor(cv2.resize(q, (150, 150), interpolation=cv2.INTER_NEAREST),
                     cv2.COLOR_GRAY2BGR)
    img[130:280, 310:460] = q
    body = count(client, img)
    assert body["counts"]["regions_seen"] == 1
    f = facing(body, "red_pack")
    assert f["facings"] == 1
    assert f["by_code"] == 1


# ================================================== 4. the annotated frame

def test_the_annotated_frame_is_a_png_of_the_shelf_with_the_boxes_on_it(
        client: TestClient) -> None:
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                      ("purple", 500, 200, 220, 300)]))
    raw = base64.b64decode(body["annotated_png_b64"])
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    h, w = img.shape[:2]
    assert w <= shelf.ANNOTATED_MAX_PX
    assert abs((w / h) - (1280 / 720)) < 0.02
    # Something was drawn: the frame is no longer just packets on a surface.
    k = w / 1280
    named_edge = img[int(200 * k):int(500 * k), int(100 * k) - 1]
    assert np.linalg.norm(named_edge.reshape(-1, 3).mean(axis=0)
                          - np.array(BG)) > 20


def test_the_annotated_frame_can_be_left_out(client: TestClient) -> None:
    teach(RED, "red")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]), annotate="0")
    assert body["ok"] is True
    assert body["annotated_png_b64"] is None
    assert facing(body, "red_pack")["facings"] == 1


# ====================================================== 5. the chain and dir

def test_every_read_is_on_the_modules_own_chain_and_the_chain_verifies(
        client: TestClient, tmp_path: Path) -> None:
    teach(RED, "red")
    count_at("red_pack", 3)
    a = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    b = count(client, shelf_frame([]))
    assert a["audited"] is True and b["audited"] is True
    path = shelf.audit_path()
    assert path == tmp_path / "data" / "shop" / "shelf.audit.jsonl"
    ok, n, head, err = verify(path)
    assert ok and err is None and n == 2
    assert head == b["chain_head"]
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert lines[0]["event"] == "shelf.count"
    assert lines[0]["facings"] == [{"sku_id": "red_pack", "facings": 2,
                                    "on_hand_units": 3, "difference": -1}]
    assert lines[1]["regions_seen"] == 0


def test_no_pixels_reach_the_chain(client: TestClient) -> None:
    """Boxes and counts only. An audit log gets pasted into bug reports."""
    teach(RED, "red")
    count(client, shelf_frame([("red", 100, 200, 220, 300),
                               ("purple", 500, 200, 220, 300)]))
    text = shelf.audit_path().read_text()
    assert "png" not in text and "b64" not in text
    assert len(text) < 2000


def test_the_money_services_ledger_is_never_touched(
        client: TestClient, tmp_path: Path) -> None:
    teach(RED, "red")
    count(client, shelf_frame(TWO_RED_ONE_BLUE))
    assert not (tmp_path / "data" / "audit.jsonl").exists()
    repo_ledger = Path(__file__).resolve().parent.parent / "results" / "audit.jsonl"
    if repo_ledger.exists():
        # The suite runs in a temp shop; the live chain must not have grown.
        assert shelf.audit_path() != repo_ledger


def test_earlier_reads_are_listed_newest_first_and_the_limit_holds(
        client: TestClient) -> None:
    teach(RED, "red")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300)]))
    second = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    body = client.get("/shelf/counts").json()
    assert body["ok"] is True
    assert [r["shelf_id"] for r in body["reads"]] == [second["shelf_id"],
                                                      first["shelf_id"]]
    assert body["reads"][0]["facings"][0]["facings"] == 2
    one = client.get("/shelf/counts?limit=1").json()
    assert one["count"] == 1 and one["matched"] == 2
    assert one["reads"][0]["shelf_id"] == second["shelf_id"]


def test_a_bad_limit_is_refused_by_name(client: TestClient) -> None:
    for bad in ("0", "-3", "x", str(shelf.MAX_LIMIT + 1)):
        r = client.get(f"/shelf/counts?limit={bad}")
        assert r.status_code == 400
        assert r.json()["reason"] == shelf.R_BAD_LIMIT


def test_the_shop_directory_override_is_honoured(tmp_path: Path) -> None:
    assert shelf.shop_dir() == tmp_path / "data" / "shop"
    assert str(shelf.audit_path()).startswith(str(tmp_path))


# ================================================================ 6. no money

def test_no_response_carries_a_price(client: TestClient) -> None:
    """Units are counts, never money. Not a paise anywhere in the body."""
    teach(RED, "red")
    teach(BLUE, "blue")
    count_at("red_pack", 4)
    for body in (count(client, shelf_frame(TWO_RED_ONE_BLUE)),
                 client.get("/shelf").json(),
                 client.get("/shelf/counts").json()):
        for where, _v in walk(body):
            low = where.lower()
            assert "paise" not in low and "rupee" not in low and "price" not in low, where
        assert body["settles_money"] is False


def test_the_lint_that_guards_invariant_one_passes_with_this_module() -> None:
    import subprocess

    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "tools" / "lint_no_float.py")],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr


# ================================================================ 7. refusals

def test_nothing_taught_is_refused_by_the_tills_own_name(client: TestClient) -> None:
    r = client.post("/shelf/count",
                    files={"image": ("s.png", png(shelf_frame([])), "image/png")},
                    data={"yolo": "0"})
    assert r.status_code == 400
    assert r.json()["reason"] == "nothing_enrolled_yet"
    assert r.json()["ok"] is False


def test_a_missing_image_and_a_non_image_are_each_refused_by_name(
        client: TestClient) -> None:
    teach(RED, "red")
    r = client.post("/shelf/count", data={"yolo": "0"})
    assert r.status_code == 400
    assert r.json()["reason"] in ("form_field_missing", "form_not_multipart")
    r = client.post("/shelf/count",
                    files={"image": ("s.txt", b"not an image at all", "text/plain")})
    assert r.status_code == 400
    assert r.json()["reason"] == "upload_not_an_image"


def test_a_json_body_with_base64_works_for_scripts(client: TestClient) -> None:
    teach(RED, "red")
    b64 = base64.b64encode(png(shelf_frame([("red", 100, 200, 220, 300)]))).decode()
    r = client.post("/shelf/count", json={"image": b64, "yolo": "0"})
    assert r.status_code == 200
    assert facing(r.json(), "red_pack")["facings"] == 1


def test_a_crash_inside_the_read_is_a_400_with_a_name_never_a_500(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    teach(RED, "red")

    def _boom(*_a, **_k):
        raise RuntimeError("the detector fell over")

    from gawaah import detector as _det
    monkeypatch.setattr(_det, "detect", _boom)
    r = client.post("/shelf/count",
                    files={"image": ("s.png", png(shelf_frame([])), "image/png")})
    assert r.status_code == 400
    assert r.json() == {"ok": False, "reason": shelf.R_INTERNAL,
                        "detail": "RuntimeError: the detector fell over",
                        "settles_money": False}


def test_describe_never_refuses_and_states_every_limit(client: TestClient) -> None:
    body = client.get("/shelf").json()
    assert body["ok"] is True
    assert body["taught"] == {"by_sight": 0, "by_code_only": 0, "total": 0,
                              "problem": None}
    assert body["counts_money"] is False and body["writes_stock"] is False
    assert "front row" in body["limits"]["front_row_only"]
    assert "finger" in body["limits"]["touching_packets"]
    assert body["detector"]["identifies_products"] is False
    assert body["reads_on_chain"] == 0 and body["last_read_at"] is None
    teach(RED, "red")
    upload_app.do_enrol_code_only(b"", "code_only", "Code only", 500,
                                  typed="8901234567891")
    body = client.get("/shelf").json()
    assert body["taught"]["by_sight"] == 1
    assert body["taught"]["by_code_only"] == 1


# ================================================================= 8. teaching

def test_an_unnamed_region_can_be_taught_as_a_new_product_from_the_held_frame(
        client: TestClient) -> None:
    """The loop the screen exists for: the camera did not know it, the
    shopkeeper names it, the next read counts it."""
    teach(RED, "red")
    img = shelf_frame([("red", 100, 200, 220, 300), ("purple", 500, 200, 220, 300)])
    first = count(client, img)
    [u] = first["unnamed"]
    r = client.post(f"/shelf/{first['shelf_id']}/teach", json={
        "region": u["region"], "sku_id": "purple_pack", "name": "Purple packet",
        "price_rupees": "12.50"})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["how"] == "product_taught"
    assert body["stored"]["sku_id"] == "purple_pack"
    assert body["audited"] is True
    assert "price" not in json.dumps(body["stored"])
    second = count(client, img)
    assert second["counts"]["unnamed"] == 0
    assert facing(second, "purple_pack")["facings"] == 1
    # The teaching went through the till's own store: the price it holds is
    # integer paise, parsed from the string, never a float.
    assert upload_app.priced_skus()["purple_pack"]["price_paise"] == 1250


def test_an_unnamed_region_can_be_added_as_a_view_of_a_product_it_resembles(
        client: TestClient) -> None:
    """Another angle. Taught with a vector that only half-resembles the teal
    packet — above the add-view floor, below the recognition bar — so the
    region is unnamed until this view is added, and named after."""
    half = 0.5 * onehot("teal") + np.sqrt(0.75) * onehot("yellow")
    teach(("teal_pack", "Teal packet", 900), "teal", vector=half)
    img = shelf_frame([("teal", 400, 200, 220, 300)])
    first = count(client, img)
    assert first["counts"]["unnamed"] == 1
    [u] = first["unnamed"]
    assert u["top1_sku"] == "teal_pack" and 0.4 < u["top1"] < 0.6
    r = client.post(f"/shelf/{first['shelf_id']}/teach",
                    json={"region": u["region"], "sku_id": "teal_pack"})
    assert r.status_code == 200, r.json()
    assert r.json()["how"] == "view_added"
    assert r.json()["stored"]["views_after"] == 3
    second = count(client, img)
    assert facing(second, "teal_pack")["facings"] == 1


def test_teaching_a_region_as_a_product_it_does_not_resemble_is_refused_by_name(
        client: TestClient) -> None:
    """The till's own floor, passed through verbatim. A bag of rice appended
    to the Parle-G gallery is permanent and silent; this is what stops it."""
    teach(RED, "red")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                       ("purple", 500, 200, 220, 300)]))
    [u] = first["unnamed"]
    r = client.post(f"/shelf/{first['shelf_id']}/teach",
                    json={"region": u["region"], "sku_id": "red_pack"})
    assert r.status_code == 400
    assert r.json()["reason"] == "does_not_look_like_this_product"


def test_a_new_product_needs_a_name_and_a_price_and_a_float_price_is_refused(
        client: TestClient) -> None:
    teach(RED, "red")
    first = count(client, shelf_frame([("purple", 500, 200, 220, 300)]))
    sid, region = first["shelf_id"], first["unnamed"][0]["region"]
    r = client.post(f"/shelf/{sid}/teach", json={"region": region, "sku_id": "p"})
    assert r.status_code == 400
    assert r.json()["reason"] == shelf.R_NEED_NAME_AND_PRICE
    r = client.post(f"/shelf/{sid}/teach",
                    json={"region": region, "sku_id": "p", "name": "P"})
    assert r.status_code == 400
    assert r.json()["reason"] == shelf.R_NEED_NAME_AND_PRICE
    # INVARIANT 1 at the boundary: 12.5 as a JSON number is a float, and a
    # float is not money. Refused by the till's own name, nothing stored.
    r = client.post(f"/shelf/{sid}/teach",
                    json={"region": region, "sku_id": "p", "name": "P",
                          "price_rupees": 12.5})
    assert r.status_code == 400
    assert r.json()["reason"] == "price_not_integer_paise"
    assert "p" not in upload_app.priced_skus()


def test_teaching_refuses_a_region_that_is_not_there_or_already_named(
        client: TestClient) -> None:
    teach(RED, "red")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                       ("purple", 500, 200, 220, 300)]))
    sid = first["shelf_id"]
    named = facing(first, "red_pack")["regions"][0]
    r = client.post(f"/shelf/{sid}/teach", json={"region": named, "sku_id": "x"})
    assert r.status_code == 400 and r.json()["reason"] == shelf.R_REGION_NAMED
    for bad in (0, 9, "2", 1.5, None):
        r = client.post(f"/shelf/{sid}/teach", json={"region": bad, "sku_id": "x"})
        assert r.status_code == 400 and r.json()["reason"] == shelf.R_BAD_REGION


def test_a_shelf_read_that_is_not_held_is_refused_with_a_404(
        client: TestClient) -> None:
    r = client.post("/shelf/shf_000000000000/teach", json={"region": 1, "sku_id": "x"})
    assert r.status_code == 404
    assert r.json()["reason"] == shelf.R_NO_SHELF
    r = client.post("/shelf/shf_000000000000/teach", content=b"not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["reason"] == shelf.R_BAD_BODY


def test_only_the_last_few_reads_are_held_and_they_expire(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A laptop left open must not keep a photograph of the shop all day."""
    teach(RED, "red")
    ids = [count(client, shelf_frame([("purple", 500, 200, 220, 300)]))["shelf_id"]
           for _ in range(shelf.HELD_FRAMES + 2)]
    assert len(shelf._HELD) == shelf.HELD_FRAMES
    r = client.post(f"/shelf/{ids[0]}/teach", json={"region": 1, "sku_id": "x"})
    assert r.status_code == 404 and r.json()["reason"] == shelf.R_NO_SHELF
    later = shelf.time.monotonic() + shelf.HELD_SECONDS + 1
    monkeypatch.setattr(shelf.time, "monotonic", lambda: later)
    r = client.post(f"/shelf/{ids[-1]}/teach", json={"region": 1, "sku_id": "x"})
    assert r.status_code == 404 and r.json()["reason"] == shelf.R_NO_SHELF
    assert len(shelf._HELD) == 0


def test_no_forgery_primitive_lives_in_this_module() -> None:
    src = Path(shelf.__file__).read_text(encoding="utf-8")
    assert "upi:" not in src.lower()
    assert "short_url" not in src
    assert "razorpay" not in src.lower()


# ============================ 6. a photograph of a person is not a shelf face

def _person(x: int, y: int, w: int, h: int, score: float = 0.75):
    from gawaah.detector import Rejection
    return Rejection(x, y, w, h, "person", score)


def _rejects(monkeypatch, *boxes) -> None:
    """Make the optional detector see exactly these not-a-facing regions."""
    import gawaah.detector as det
    monkeypatch.setattr(det, "yolo_rejections", lambda bgr: list(boxes))


def test_a_person_holding_stock_is_refused_not_counted(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE SECOND HALF OF THE BUG, AND THE ONE NO DETECTOR WORK FIXES.

    Pointed at two cartons held up in a bedroom, this screen reported TWELVE
    facings. Some of that was the detector cutting each carton into three, and
    that is fixed. The rest was the room — a face, a torso, a wardrobe edge —
    and a bedroom does not become a shelf by being segmented better.

    A facing is a position in a row. This frame has no rows, so the honest
    output is not a better number, it is no number and the reason why.
    """
    teach(RED, "red")
    _rejects(monkeypatch, _person(40, 30, 700, 640))          # 48% of the frame
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")

    assert body["ok"] is True                  # a condition, never a crash
    assert body["counted"] is False
    assert body["abstained"]["reason"] == "frame_is_not_a_shelf"
    assert body["facings"] == []
    assert body["counts"]["products"] == 0 and body["counts"]["named"] == 0
    # and it says WHICH condition it saw, not just that it gave up
    assert "person" in body["abstained"]["detail"]
    assert body["abstained"]["covers_frame_pct"] > 20
    assert body["note"].endswith(body["abstained"]["detail"])


def test_refusing_to_count_hides_nothing_it_saw(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Withholding the COUNT is not withholding the reading.

    The camera recognised the shopkeeper's own product; saying so costs
    nothing and telling him only "cannot count" would look like blindness.
    """
    teach(RED, "red")
    _rejects(monkeypatch, _person(40, 30, 700, 640))
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")

    named = [u for u in body["unnamed"] if u.get("name_seen") == "Red packet"]
    assert len(named) == 2, "the two red packets it recognised were not reported"
    assert all(u["reason"] == "not_counted_frame_is_not_a_shelf" for u in named)
    assert body["counts"]["regions_seen"] == 3      # still says what it saw
    assert all(u["box"] for u in body["unnamed"])


def test_a_shopkeeper_at_the_edge_of_the_frame_still_counts(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE CASE THIS MUST NOT BREAK.

    A hand or a shoulder at the edge of an otherwise good shelf photo is not a
    reason to refuse a whole aisle. Measured bar is 20% of the frame; this is
    a person over about 6% of it.
    """
    teach(RED, "red")
    teach(BLUE, "blue")
    _rejects(monkeypatch, _person(0, 480, 240, 240))
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")

    assert body["counted"] is True and body["abstained"] is None
    assert facing(body, "red_pack")["facings"] == 2


def test_without_the_optional_model_nothing_is_ever_refused(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal rides on an OPTIONAL file. No weights, no new behaviour."""
    teach(RED, "red")
    _rejects(monkeypatch)                       # the model sees nothing
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")
    assert body["counted"] is True and body["abstained"] is None
    # and with the proposer switched off entirely it is never even consulted
    body2 = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="0")
    assert body2["counted"] is True


def test_a_refused_frame_writes_no_facing_and_no_stock_comparison(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dangerous half: a wrong facing count that looks like a measurement.

    Nothing may be compared against the stock figure from a frame that was not
    a shelf, because the comparison's one decisive direction — the shelf shows
    more than the figure — would then be drawn from a photograph of a person.
    """
    teach(RED, "red")
    count_at("red_pack", 1)
    _rejects(monkeypatch, _person(40, 30, 700, 640))
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")
    assert body["counts"]["shelf_exceeds_figure"] == 0
    assert body["facings"] == []
    line = json.loads((Path(shelf.audit_path())).read_text().strip().split("\n")[-1])
    assert line["abstained"] == "frame_is_not_a_shelf"
    assert line["named"] == 0 and line["facings"] == []


def test_the_limit_is_stated_on_every_response(client: TestClient) -> None:
    """Every limit this module has is on every response, refused or not."""
    teach(RED, "red")
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE))
    assert "no rows" in body["limits"]["not_a_shelf"]
    assert body["counted"] is True


# ================================================ 11. correcting what it said

def test_a_wrong_name_can_be_corrected_and_the_correction_teaches(
        client: TestClient) -> None:
    """THE LOOP THIS SCREEN IS FOR, from the other side.

    A shopkeeper who cannot fix a wrong name has to choose between a count he
    knows is wrong and no count at all. So a correction does two things and
    both are asserted here: this read is recomputed by the SERVER — new
    facings, new totals, a new picture — and the crop is taught to the product
    he named, so the next photograph is read by a counter that has seen it.
    """
    teach(RED, "red")
    teach(BLUE, "blue")
    img = shelf_frame([("red", 100, 200, 220, 300)])
    first = count(client, img)
    region = facing(first, "red_pack")["regions"][0]
    before = len(upload_app._ao_load()["skus"]["blue_pack"]["vectors"])

    r = client.post(f"/shelf/{first['shelf_id']}/correct",
                    json={"region": region, "sku_id": "blue_pack", "force": True})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["was"] == "red_pack" and body["sku_id"] == "blue_pack"

    read = body["read"]
    assert read["counts"]["named"] == 1 and read["counts"]["by_hand"] == 1
    assert facing(read, "blue_pack")["facings"] == 1
    assert facing(read, "blue_pack")["by_hand"] == 1
    # NAMED, BUT NOT BY THE CAMERA, and the row says which.
    assert facing(read, "blue_pack")["by_appearance"] == 0
    assert [f["sku_id"] for f in read["facings"]] == ["blue_pack"]
    # red_pack is now missing from the shelf, which is a thing to be told.
    assert [m["sku_id"] for m in read["missing"]] == ["red_pack"]

    # AND IT TAUGHT. A correction that only relabelled a screen would leave the
    # counter making the same mistake on the next frame.
    after = len(upload_app._ao_load()["skus"]["blue_pack"]["vectors"])
    assert after > before, "the corrected view never reached the catalogue"


def test_a_correction_is_refused_on_a_region_the_counter_did_not_name(
        client: TestClient) -> None:
    """"It saw nothing here" and "it saw the wrong thing" are different facts.

    Collapsing them would let a correction quietly promote an abstention to a
    name, which is the one thing this module may never do.
    """
    teach(RED, "red")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                       ("purple", 500, 200, 220, 300)]))
    [u] = first["unnamed"]
    r = client.post(f"/shelf/{first['shelf_id']}/correct",
                    json={"region": u["region"], "sku_id": "red_pack"})
    assert r.status_code == 400 and r.json()["reason"] == shelf.R_NOT_NAMED
    assert "teach" in r.json()["detail"]


def test_correcting_a_region_to_the_name_it_already_has_changes_nothing(
        client: TestClient) -> None:
    teach(RED, "red")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300)]))
    region = facing(first, "red_pack")["regions"][0]
    r = client.post(f"/shelf/{first['shelf_id']}/correct",
                    json={"region": region, "sku_id": "red_pack"})
    assert r.status_code == 400 and r.json()["reason"] == shelf.R_SAME_NAME


def test_a_false_region_can_be_rejected_and_it_teaches_the_camera_nothing(
        client: TestClient) -> None:
    """A price label is not a packet, and a shopkeeper must be able to say so.

    What a rejection does is stated on the response rather than implied,
    because the obvious guess — that the camera has learned something — is
    wrong, and a button that implied it would be contradicted by the very next
    photograph.
    """
    teach(RED, "red")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300),
                                       ("purple", 500, 200, 220, 300)]))
    [u] = first["unnamed"]
    r = client.post(f"/shelf/{first['shelf_id']}/reject",
                    json={"region": u["region"]})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["teaches_the_camera"] is False
    assert "does not teach the camera" in body["detail"]
    read = body["read"]
    assert read["counts"]["unnamed"] == 0 and read["counts"]["rejected"] == 1
    # STRUCK OUT, NOT DELETED. A box that simply vanished would leave the
    # shopkeeper unable to tell his own rejection from a region never proposed.
    assert [x["region"] for x in read["rejected"]] == [u["region"]]
    assert read["regions"][u["region"] - 1]["state"] == "rejected"
    r = client.post(f"/shelf/{first['shelf_id']}/reject",
                    json={"region": u["region"]})
    assert r.status_code == 400
    assert r.json()["reason"] == shelf.R_ALREADY_REJECTED


def test_teaching_a_region_counts_it_on_this_read_and_says_who_named_it(
        client: TestClient) -> None:
    """The count on screen used to stay one short of what the counter knew.

    The old teach response said only what had been stored, so the page struck
    the region off its own list — a figure the browser had authored. The whole
    reading comes back instead.
    """
    teach(RED, "red")
    img = shelf_frame([("red", 100, 200, 220, 300), ("purple", 500, 200, 220, 300)])
    first = count(client, img)
    [u] = first["unnamed"]
    r = client.post(f"/shelf/{first['shelf_id']}/teach", json={
        "region": u["region"], "sku_id": "purple_pack", "name": "Purple packet",
        "price_rupees": "12.50"})
    read = r.json()["read"]
    assert read["counts"]["unnamed"] == 0
    assert read["counts"]["named"] == 2 and read["counts"]["by_hand"] == 1
    assert facing(read, "purple_pack")["by_hand"] == 1


def test_a_corrected_read_is_listed_with_its_corrections_applied(
        client: TestClient) -> None:
    """A list of figures the shopkeeper has already corrected is a list of
    wrong figures, and the next read's comparison would be drawn against them.

    The chain still holds both lines — a log that hid the counter's mistake
    would not be a log — but the read is replayed, not filtered.
    """
    teach(RED, "red")
    teach(BLUE, "blue")
    first = count(client, shelf_frame([("red", 100, 200, 220, 300)]))
    region = facing(first, "red_pack")["regions"][0]
    client.post(f"/shelf/{first['shelf_id']}/correct",
                json={"region": region, "sku_id": "blue_pack", "force": True})
    rows = client.get("/shelf/counts").json()["reads"]
    assert len(rows) == 1, "a correction must not look like a second read"
    assert rows[0]["corrected"] is True and rows[0]["corrections"] == 1
    assert [f["sku_id"] for f in rows[0]["facings"]] == ["blue_pack"]
    events = [e["event"] for e in shelf.read_events()[0]]
    assert events == ["shelf.count", "shelf.corrected"]


# ================================================ 12. what is NOT on the shelf

def test_a_taught_product_the_frame_does_not_show_is_reported_as_missing(
        client: TestClient) -> None:
    teach(RED, "red")
    teach(BLUE, "blue")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]))
    assert body["counts"]["missing"] == 1
    [m] = body["missing"]
    assert m["sku_id"] == "blue_pack" and m["verdict"] == "never_seen"
    # NOT AN OUT-OF-STOCK CLAIM, and the sentence may not read like one.
    assert "may be on another shelf" in m["sentence"]
    assert "not a list of what has run out" in body["limits"]["missing_is_not_out_of_stock"]


def test_a_code_only_product_is_listed_apart_because_it_could_never_be_seen(
        client: TestClient) -> None:
    """Its absence is evidence of nothing, and saying so is the whole point.

    Left off the list entirely, a shopkeeper would think the counter had
    checked; listed beside the others, he would think it had run out.
    """
    teach(RED, "red")
    upload_app.do_enrol_code_only(b"", "code_only", "Code only", 500,
                                  typed="8901234567891")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]))
    [m] = [x for x in body["missing"] if x["sku_id"] == "code_only"]
    assert m["verdict"] == "cannot_be_seen" and m["taught_by_sight"] is False
    assert "It is not missing from this shelf" in m["sentence"]
    # last, under everything a photograph could actually have found
    assert body["missing"][-1]["sku_id"] == "code_only"


def test_a_product_that_was_here_last_time_and_is_gone_now_leads_the_list(
        client: TestClient) -> None:
    """The strong case: same shelf, same camera, and the packets are gone."""
    teach(RED, "red")
    teach(BLUE, "blue")
    count(client, shelf_frame([("red", 100, 200, 220, 300),
                               ("blue", 500, 200, 220, 300)]), label="Aisle 2")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]),
                 label="Aisle 2")
    assert body["counts"]["gone"] == 1
    assert body["missing"][0]["sku_id"] == "blue_pack"
    assert body["missing"][0]["verdict"] == "was_here"
    assert body["missing"][0]["previous_facings"] == 1
    assert "last read of this shelf" in body["missing"][0]["sentence"]


# =============================================== 13. against the last count

def test_a_read_is_compared_with_the_last_read_of_the_same_shelf(
        client: TestClient) -> None:
    teach(RED, "red")
    count(client, shelf_frame([("red", 100, 200, 220, 300),
                               ("red", 420, 200, 220, 300)]), label="Aisle 2")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]),
                 label="Aisle 2")
    assert body["previous"]["same_shelf"] is True
    assert body["previous"]["label"] == "Aisle 2"
    f = facing(body, "red_pack")
    assert f["previous_facings"] == 2 and f["change"] == -1


def test_a_comparison_across_two_named_shelves_is_never_drawn(
        client: TestClient) -> None:
    """Two aisles are not one shelf, and "2 fewer" across them is a number the
    counter invented. With no earlier read of THIS shelf there is no
    comparison at all, which is the honest answer."""
    teach(RED, "red")
    count(client, shelf_frame([("red", 100, 200, 220, 300),
                               ("red", 420, 200, 220, 300)]), label="Aisle 2")
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]),
                 label="Aisle 7")
    assert body["previous"] is None
    assert facing(body, "red_pack")["previous_facings"] is None
    assert facing(body, "red_pack")["change"] is None
    assert body["counts"]["gone"] == 0


def test_an_unlabelled_read_says_the_comparison_may_be_of_another_shelf(
        client: TestClient) -> None:
    teach(RED, "red")
    count(client, shelf_frame([("red", 100, 200, 220, 300),
                               ("red", 420, 200, 220, 300)]))
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]))
    assert body["previous"] is not None
    assert body["previous"]["same_shelf"] is True   # neither read is labelled
    assert "same shelf" in body["limits"]["comparison_needs_a_label"]
    # A labelled read is never compared against an unlabelled one.
    body = count(client, shelf_frame([("red", 100, 200, 220, 300)]),
                 label="Aisle 2")
    assert body["previous"] is None


def test_the_shelf_names_this_counter_knows_are_offered_back(
        client: TestClient) -> None:
    teach(RED, "red")
    assert client.get("/shelf").json()["labels"] == []
    count(client, shelf_frame([("red", 100, 200, 220, 300)]), label="Aisle 2")
    count(client, shelf_frame([("red", 100, 200, 220, 300)]), label="Cold case")
    assert client.get("/shelf").json()["labels"] == ["Cold case", "Aisle 2"]


def test_nothing_on_a_frame_that_was_not_counted_wears_the_counted_colour(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Green is a facing this counter stands behind. There are none here.

    The reading is still reported — the two red packets it recognised are on
    the response with the name it matched — but every region on a refused frame
    is in the ABSTAINED state, so the picture draws it amber and the list calls
    it amber. A green box on a frame the counter has just refused to count is
    the one thing this screen exists not to print.
    """
    teach(RED, "red")
    _rejects(monkeypatch, _person(40, 30, 700, 640))
    body = count(client, shelf_frame(TWO_RED_ONE_BLUE), yolo="1")

    assert body["counted"] is False
    assert {r["state"] for r in body["regions"]} == {"unnamed"}
    assert all(r["sku_id"] is None and r["name"] is None for r in body["regions"])
    # The name it matched is still there, under a field that cannot be mistaken
    # for the region's own name.
    assert [u["name_seen"] for u in body["unnamed"] if u.get("name_seen")] \
        == ["Red packet", "Red packet"]
