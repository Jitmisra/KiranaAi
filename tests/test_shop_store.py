"""Acceptance for DUKAAN — the shopkeeper's catalog on disk.

Three things are being proved here, and only three:

  1. A SHOP SURVIVES A RESTART. Name, price, vectors, footprint and photo all
     come back, and the vectors come back BIT-IDENTICAL — because a vector that
     round-trips to within 1e-16 is a vector whose cosine moved, and a cosine
     that moved near theta is a match that flipped.

  2. A PRICE CANNOT ENTER WRONG. This is the door money comes through. Float,
     bool, negative, zero, rupee string, sub-paisa rupee string and a float
     sitting in the JSON file are each refused BY NAME, and a refusal leaves
     nothing behind on disk.

  3. THE TWO PROJECTIONS CANNOT DISAGREE. to_gallery() and price_book() are
     asserted equal after every mutation, including under a randomised op
     sequence, because "the till knows a price for everything it can recognise"
     is the property that makes the whole counter safe.

Plus the collision guard, which is the enrolment-time half of invariant 7: an
item that identify() could never separate is refused while the shopkeeper is
still holding it.

Every vector here is synthetic. No model is loaded, nothing is downloaded, and
the store is asked to embed exactly once — in a test that asserts it refuses.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from gawaah.identity import (
    DEFAULT_PHI,
    DEFAULT_TAU_MM,
    DEFAULT_THETA,
    MODE_APPEARANCE_ONLY,
    PHI_APPEARANCE_ONLY,
    Gallery,
    Identifier,
    IdentityError,
    REASON_MATCH,
)
from gawaah.money import MoneyError
from gawaah.shop_store import (
    ACTION_ADDED,
    ACTION_REFUSED,
    ACTION_REPLACED,
    CATALOG_FORMAT,
    PHOTO_CAP_BYTES,
    PHOTO_EDGE_PX,
    PHOTO_INPUT_CAP_BYTES,
    REASON_COLLISION,
    ShopError,
    ShopStore,
    SkuRecord,
    _digest,
    price_from_rupees,
)

DIM = 24
REPO = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------- fixtures

def vec(seed: int, dim: int = DIM) -> np.ndarray:
    """A reproducible, non-degenerate vector. Never a unit vector on purpose:
    the store must not quietly assume normalisation it was not given."""
    return np.random.default_rng(seed).normal(size=dim) * 3.0 + 0.25


def basis(i: int, dim: int = DIM) -> np.ndarray:
    """An axis vector. Two of these are exactly orthogonal, so their cosine is
    0.0 and no threshold argument is doing hidden work."""
    v = np.zeros(dim)
    v[i] = 1.0
    return v


@pytest.fixture
def store(tmp_path) -> ShopStore:
    return ShopStore(tmp_path / "shop")


def files_under(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# ===================================================== 1. survives a restart

def test_a_shop_survives_a_restart(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    v = [vec(1), vec(2)]
    r = s.add_sku("parle_g_200g", "Parle-G  200g", 2000, v, 118.375)
    assert r.ok and r.action == ACTION_ADDED

    again = ShopStore(d)
    rec = again.get("parle_g_200g")
    assert rec is not None
    assert rec.name == "Parle-G 200g"          # whitespace normalised, not lost
    assert rec.price_paise == 2000
    assert isinstance(rec.price_paise, int)
    assert rec.footprint_mm == 118.375
    assert rec.n_views == 2 and rec.dim == DIM
    assert again.skus() == ("parle_g_200g",)


def test_vectors_round_trip_bit_exact_through_json(tmp_path):
    """Not 'close'. IDENTICAL.

    json.dumps writes a float via repr(), which is the shortest string that
    round-trips, so the bytes are exact. If that ever stops being true the
    cosines the till compares against theta are not the cosines that were
    enrolled, and this test is the alarm.
    """
    d = tmp_path / "shop"
    s = ShopStore(d)
    original = np.vstack([vec(11), vec(12), vec(13)])
    s.add_sku("x", "X", 1500, original, 90.0)
    back = ShopStore(d).get("x").vectors
    assert back.dtype == np.float64
    assert np.array_equal(back, original)
    assert back.tobytes() == np.ascontiguousarray(original).tobytes()


def test_stored_vectors_are_read_only(store):
    """A caller that mutated the block in place would change what the running
    till recognises while the file on disk said something else."""
    store.add_sku("x", "X", 1500, [vec(3)], 90.0)
    rec = store.get("x")
    with pytest.raises(ValueError):
        rec.vectors[0, 0] = 99.0


def test_the_photo_survives_a_restart_and_is_readable(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    img = np.zeros((840, 600, 3), np.uint8)
    img[100:500, 100:400] = (30, 90, 200)
    r = s.add_sku("x", "X", 1500, [vec(3)], 90.0, photo_png=img)
    assert r.photo_action == "stored" and r.photo_bytes > 0

    again = ShopStore(d)
    png = again.photo_bytes("x")
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) == again.get("x").photo_bytes


def test_a_missing_catalog_is_an_empty_shop_not_an_error(tmp_path):
    s = ShopStore(tmp_path / "never-used")
    assert len(s) == 0 and s.skus() == () and s.dim is None
    assert s.price_paise("anything") is None
    # and it wrote nothing, so opening a shop is not a mutation
    assert files_under(tmp_path / "never-used") == set()


# ============================ 1b. the two ways to teach, and the weaker one
# A shopkeeper can teach from a plain product photo with no TAKHTI in it. That
# SKU is stored with NO footprint, judged on appearance alone, and labelled as
# weaker everywhere it appears. What is proved here is that the DISTINCTION
# survives — a missing footprint must never come back as 0.0, and a real one
# must never come back as missing.

def test_a_product_can_be_taught_from_a_plain_photo_with_no_mat(tmp_path):
    """The feature: an ordinary downloaded product photo is teachable."""
    s = ShopStore(tmp_path / "shop")
    r = s.add_sku("colgate_carton", "Colgate 100g", 5500, [vec(1)], None)
    assert r.ok and r.action == ACTION_ADDED
    assert r.taught_by == MODE_APPEARANCE_ONLY
    assert r.is_appearance_only
    # and it SAYS so, unprompted, in words a shopkeeper can act on
    assert "APPEARANCE-ONLY" in r.message
    assert "TAKHTI" in r.message

    rec = s.get("colgate_carton")
    assert rec.footprint_mm is None
    assert rec.is_appearance_only and rec.taught_by == MODE_APPEARANCE_ONLY
    assert s.appearance_only_skus() == ("colgate_carton",)


def test_a_mat_measured_product_says_it_was_measured(tmp_path):
    s = ShopStore(tmp_path / "shop")
    r = s.add_sku("parle_g_200g", "Parle-G", 2000, [vec(1)], 118.375)
    assert r.taught_by == "mat_measured" and not r.is_appearance_only
    assert "MAT-MEASURED at 118.4 mm" in r.message
    assert s.get("parle_g_200g").taught_by == "mat_measured"
    assert s.appearance_only_skus() == ()


def test_a_missing_footprint_survives_a_restart_as_missing_not_as_zero(tmp_path):
    """THE ROUND-TRIP THAT MATTERS.

    ``None`` and ``0.0`` are not two spellings of the same thing. A reload that
    turned a missing footprint into 0.0 would hand the metric tiebreak a size
    nobody ever measured and silently gate a sale on it; a reload that turned a
    real 118.375 into None would strip a mat-taught product of the very
    discriminator it was enrolled with. Both directions are asserted, on the
    same file, after a genuine reopen.
    """
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("photo_taught", "Photo", 2000, [vec(1)], None)
    s.add_sku("mat_taught", "Mat", 2500, [vec(2)], 118.375)

    # on disk: an explicit null, and the word for it beside it
    raw = json.loads((d / "catalog.json").read_text())["skus"]
    assert raw["photo_taught"]["footprint_mm"] is None
    assert raw["photo_taught"]["taught_by"] == MODE_APPEARANCE_ONLY
    assert raw["mat_taught"]["footprint_mm"] == 118.375
    assert raw["mat_taught"]["taught_by"] == "mat_measured"

    again = ShopStore(d)
    photo, mat = again.get("photo_taught"), again.get("mat_taught")
    assert photo.footprint_mm is None
    # not merely falsy: a 0.0 would satisfy `not fp` and would then be COMPARED
    # against by the metric tiebreak. The type is the assertion.
    assert not isinstance(photo.footprint_mm, (int, float))
    assert mat.footprint_mm == 118.375 and isinstance(mat.footprint_mm, float)
    assert again.appearance_only_skus() == ("photo_taught",)
    assert again.taught_by("photo_taught") == MODE_APPEARANCE_ONLY
    assert again.taught_by("mat_taught") == "mat_measured"
    assert again.taught_by("never_heard_of_it") is None


def test_to_gallery_passes_the_absence_of_a_footprint_straight_through(tmp_path):
    """The projection must not invent millimetres either. If it did, the till
    would size-gate an appearance-only SKU on a fabricated measurement and the
    catalog and the gallery would disagree about what was taught."""
    s = ShopStore(tmp_path / "shop")
    s.add_sku("photo_taught", "Photo", 2000, [vec(1)], None)
    s.add_sku("mat_taught", "Mat", 2500, [vec(2)], 118.375)

    g = s.to_gallery()
    assert g.footprint("photo_taught") is None
    assert g.footprint("mat_taught") == 118.375
    assert g.appearance_only_skus() == ("photo_taught",)
    # and the gallery agrees with the store about which half is weak
    assert g.appearance_only_skus() == s.appearance_only_skus()


def test_an_appearance_only_sku_is_actually_identifiable_end_to_end(tmp_path):
    """Teaching from a photo is worth nothing if the till cannot then recognise
    it. Taught with no mat, identified with no mat, priced from the catalog."""
    s = ShopStore(tmp_path / "shop")
    v = basis(0)
    s.add_sku("photo_taught", "Photo", 5500, [v], None)

    ident = Identifier(s.to_gallery(), lambda crop: v.copy())
    r = ident.identify(np.zeros((4, 4), np.uint8), None)
    assert r.sku_id == "photo_taught"
    assert r.reason == REASON_MATCH
    assert r.mode == MODE_APPEARANCE_ONLY
    assert r.phi_applied == PHI_APPEARANCE_ONLY   # the higher bar, not phi
    assert s.price_paise(r.sku_id) == 5500


def test_a_hand_edited_taught_by_is_refused_rather_than_quietly_corrected(tmp_path):
    """``taught_by`` is derived on write, so on read it can only ever reveal a
    hand-edit. A record that disagrees with itself about whether it was
    size-checked cannot be trusted to say so on screen, so it is refused instead
    of being repaired to whichever field we happened to trust."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [vec(1)], None)

    data = json.loads(s.catalog_path.read_text())
    data.pop("sha256")
    data["skus"]["a"]["taught_by"] = "mat_measured"     # a lie about the evidence
    data["sha256"] = _digest(data)
    s.catalog_path.write_text(json.dumps(data))

    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "taught_by" in str(e.value)


def test_a_catalog_with_weak_skus_records_the_bar_they_were_admitted_under(
    tmp_path,
):
    """An appearance-only SKU has no size check, so the similarity bar is the
    only thing protecting it. Reopening under a different one is refused for
    the same reason theta and tau_mm are."""
    d = tmp_path / "shop"
    ShopStore(d).add_sku("a", "A", 2000, [vec(1)], None)
    assert json.loads((d / "catalog.json").read_text())["gates"][
        "phi_appearance_only"] == PHI_APPEARANCE_ONLY

    with pytest.raises(ShopError) as e:
        ShopStore(d, phi_appearance_only=0.95)
    assert "phi_appearance_only" in str(e.value)
    assert ShopStore(d).skus() == ("a",)          # the right gate still opens it


def test_a_weak_sku_must_not_be_the_easier_one_to_match(tmp_path):
    """Invariant 7 at the store's door: you may not buy the mode with fewer
    discriminators a discount."""
    with pytest.raises(ShopError) as e:
        ShopStore(tmp_path / "shop", phi=0.90, phi_appearance_only=0.80)
    assert "must not be the easier one to match" in str(e.value)


def test_a_photo_taught_sku_is_guarded_more_strictly_not_less(tmp_path):
    """Size cannot rescue a pair when one side has no size, so the guard tightens
    for the weak mode. Two bottles 60 mm apart are fine when both are measured
    and refused the moment one of them is taught from a photo."""
    s = ShopStore(tmp_path / "shop")
    s.add_sku("bottle_500", "Bottle 500ml", 2000, [vec(1)], 118.4)
    assert s.add_sku("bottle_1l", "Bottle 1L", 3500, [vec(1)], 178.4).ok

    r = s.add_sku("bottle_1l_photo", "Bottle 1L", 3500, [vec(1)], None)
    assert r.ok is False and r.reason == REASON_COLLISION
    assert r.footprint_delta_mm is None           # size did not exist, was not 0
    assert "no size check can ever tell them apart" in r.message
    assert "bottle_1l_photo" not in s


def test_a_missing_footprint_key_is_not_read_as_taught_from_a_photo(tmp_path):
    """A truncated or foreign file must not be silently downgraded into the weak
    mode: that would strip a mat-taught SKU of its size check with nobody told.
    An explicit null is a claim, an absent key is not."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [vec(1)], 118.4)

    data = json.loads(s.catalog_path.read_text())
    data.pop("sha256")
    del data["skus"]["a"]["footprint_mm"]
    data["sha256"] = _digest(data)
    s.catalog_path.write_text(json.dumps(data))

    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "no 'footprint_mm' key" in str(e.value)


def test_a_mixed_shop_keeps_both_kinds_straight_across_a_restart(tmp_path):
    """The realistic shop: some products taught properly on the mat, some
    photographed in a hurry. The counter has to keep saying which is which."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    for i in range(6):
        s.add_sku(f"sku_{i}", f"Item {i}", 1000 + i, [basis(i)],
                  None if i % 2 else 100.0 + i)

    again = ShopStore(d)
    assert again.appearance_only_skus() == ("sku_1", "sku_3", "sku_5")
    for i in range(6):
        rec = again.get(f"sku_{i}")
        expected_weak = bool(i % 2)
        assert rec.is_appearance_only is expected_weak
        assert (rec.footprint_mm is None) is expected_weak
        assert rec.taught_by == (MODE_APPEARANCE_ONLY if expected_weak
                                 else "mat_measured")
    # the audit line carries it too, so a ledger can answer this later
    assert again.to_gallery().appearance_only_skus() == \
        again.appearance_only_skus()


def test_a_weak_sku_can_be_re_taught_on_the_mat_and_stops_being_weak(tmp_path):
    """The repair path the label exists to enable. ``taught_by`` is what lets a
    shopkeeper find the weak ones; re-teaching with a measurement is what
    actually fixes them, and the record has to follow."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("colgate", "Colgate", 5500, [vec(1)], None)
    assert s.appearance_only_skus() == ("colgate",)

    r = s.add_sku("colgate", "Colgate", 5500, [vec(1)], 96.5)
    assert r.ok and r.action == ACTION_REPLACED
    assert r.taught_by == "mat_measured"
    assert s.appearance_only_skus() == ()
    assert ShopStore(d).get("colgate").footprint_mm == 96.5
    # and it works in the other direction too, without leaving a stale 0.0
    s.add_sku("colgate", "Colgate", 5500, [vec(1)], None)
    assert ShopStore(d).get("colgate").footprint_mm is None


def test_the_result_audit_line_names_how_it_was_taught(tmp_path):
    s = ShopStore(tmp_path / "shop")
    weak = s.add_sku("p", "P", 2000, [vec(1)], None).to_audit()
    assert weak["taught_by"] == MODE_APPEARANCE_ONLY
    assert json.loads(json.dumps(weak)) == weak

    strong = s.add_sku("m", "M", 2000, [basis(3)], 118.4).to_audit()
    assert strong["taught_by"] == "mat_measured"


# ================================================ 2. a price cannot enter wrong

@pytest.mark.parametrize(
    "bad, why",
    [
        (20.0, "float"),
        (2000.5, "float"),
        (True, "bool"),
        (False, "bool"),
        ("2000", "str"),
        ("214.50", "rupee string"),
        (None, "None"),
        (np.int64(2000), "numpy int is not a python int"),
    ],
)
def test_a_price_that_is_not_integer_paise_is_refused(store, bad, why):
    with pytest.raises(MoneyError):
        store.add_sku("x", "X", bad, [vec(1)], 90.0)
    assert len(store) == 0, why


def test_a_float_price_is_refused_even_when_it_is_a_whole_number(store):
    """2000.0 == 2000 is True and that is exactly the trap. int(214.507) is 214
    paise: a 99% discount that no downstream check would ever notice, because by
    then the number IS an int."""
    with pytest.raises(MoneyError) as e:
        store.add_sku("x", "X", 2000.0, [vec(1)], 90.0)
    assert "float is not money" in str(e.value)


def test_a_negative_price_is_refused(store):
    with pytest.raises(MoneyError) as e:
        store.add_sku("x", "X", -2000, [vec(1)], 90.0)
    assert "negative" in str(e.value)


def test_a_zero_price_is_refused_because_an_empty_field_parses_as_zero(store):
    with pytest.raises(MoneyError) as e:
        store.add_sku("x", "X", 0, [vec(1)], 90.0)
    assert "0 paise is not a price" in str(e.value)


def test_a_rupee_string_reaches_paise_only_through_the_named_helper():
    assert price_from_rupees("214.50") == 21450
    assert price_from_rupees("20") == 2000
    assert price_from_rupees("0.05") == 5


def test_a_rupee_string_with_sub_paisa_precision_is_refused():
    with pytest.raises(MoneyError) as e:
        price_from_rupees("214.507")
    assert "sub-paisa" in str(e.value)


@pytest.mark.parametrize("bad", ["-5.00", "0", "0.00", "abc", "", 2000, 20.0, True])
def test_the_rupee_helper_refuses_everything_that_is_not_a_clean_rupee_string(bad):
    with pytest.raises(MoneyError):
        price_from_rupees(bad)


def test_a_refused_price_leaves_nothing_on_disk(tmp_path):
    """The money gate runs BEFORE anything is computed or written, so a bad
    price cannot leave a photo, a half-catalog or a temp file behind."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    img = np.zeros((400, 300, 3), np.uint8)
    with pytest.raises(MoneyError):
        s.add_sku("x", "X", 20.0, [vec(1)], 90.0, photo_png=img)
    assert files_under(d) == set()
    assert len(s) == 0


def test_a_float_price_sitting_in_the_catalog_file_is_refused_as_a_money_error(
    tmp_path,
):
    """The last line of defence. Someone hand-edits 2000 to 2000.0, or a tool
    round-trips the file through a JSON library that emits floats. json.loads
    hands us a float and money.paise() refuses it — the diagnosis is MoneyError,
    not 'bad file', because it IS a money bug."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("x", "X", 2000, [vec(1)], 90.0)

    data = json.loads(s.catalog_path.read_text())
    data.pop("sha256")
    data["skus"]["x"]["price_paise"] = 2000.0
    data["sha256"] = _digest(data)          # digest recomputed: money must fire
    s.catalog_path.write_text(json.dumps(data))

    with pytest.raises(MoneyError) as e:
        ShopStore(d)
    assert "float is not money" in str(e.value)


# ============================================ 3. replace, never silently double

def test_adding_an_existing_sku_replaces_it_and_says_so(store):
    a = store.add_sku("x", "X", 2000, [vec(1)], 90.0)
    b = store.add_sku("x", "X better", 2500, [vec(1), vec(2)], 90.0)

    assert a.action == ACTION_ADDED and a.replaced is False
    assert b.action == ACTION_REPLACED and b.replaced is True
    assert b.previous_price_paise == 2000
    assert "replaced" in b.message and "2000 -> 2500" in b.message

    assert len(store) == 1
    assert store.price_paise("x") == 2500
    assert store.get("x").n_views == 2
    assert store.get("x").name == "X better"


def test_a_replace_never_doubles_the_gallery_or_the_price_book(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    for i in range(5):
        s.add_sku("x", "X", 2000 + i, [vec(1)], 90.0)
    assert len(s) == 1
    assert s.to_gallery().skus() == ("x",)
    assert s.price_map() == {"x": 2004}
    assert ShopStore(d).price_map() == {"x": 2004}


def test_a_price_correction_retains_the_photo_and_remove_deletes_it(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    img = np.zeros((400, 300, 3), np.uint8)
    img[50:200, 50:200] = 200
    first = s.add_sku("x", "X", 2000, [vec(1)], 90.0, photo_png=img)
    assert first.photo_action == "stored"

    again = s.add_sku("x", "X", 2500, [vec(1)], 90.0)   # no photo supplied
    assert again.photo_action == "retained"
    assert s.photo_bytes("x") is not None
    assert s.photo_path("x").exists()

    assert s.remove("x") is True
    assert not s.photo_path("x").exists()
    assert s.photo_bytes("x") is None


def test_remove_is_false_for_a_sku_that_was_never_there(store):
    assert store.remove("ghost") is False


def test_clear_photo_keeps_the_product_and_its_identity(store):
    img = np.zeros((200, 200, 3), np.uint8)
    store.add_sku("x", "X", 2000, [vec(1)], 90.0, photo_png=img)
    before = store.get("x").vectors.copy()
    assert store.clear_photo("x") is True
    assert store.get("x") is not None
    assert store.get("x").photo is None
    assert np.array_equal(store.get("x").vectors, before)
    assert store.clear_photo("x") is False


# ================================================= 4. the collision guard

def test_a_collision_is_refused_by_name(store):
    store.add_sku("parle_g_200g", "Parle-G 200g", 2000, [vec(1)], 118.4)
    r = store.add_sku("parle_g_clone", "Parle-G clone", 2500, [vec(1)], 118.5)

    assert r.ok is False and bool(r) is False
    assert r.action == ACTION_REFUSED and r.reason == REASON_COLLISION
    assert r.collides_with == "parle_g_200g"
    assert r.colliding == ("parle_g_200g",)
    assert r.similarity >= 1.0 - DEFAULT_THETA
    assert r.footprint_delta_mm <= DEFAULT_TAU_MM
    assert "parle_g_200g" in r.message
    assert len(store) == 1


def test_a_collision_refusal_writes_nothing(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [vec(1)], 118.4)
    before = files_under(d)
    img = np.zeros((300, 300, 3), np.uint8)
    r = s.add_sku("b", "B", 2500, [vec(1)], 118.5, photo_png=img)
    assert r.ok is False
    assert files_under(d) == before            # not even the photo
    assert not (d / "photos" / "b.png").exists()
    assert ShopStore(d).skus() == ("a",)


def test_a_far_apart_footprint_escapes_the_guard_because_the_mat_can_separate_them(
    store,
):
    """Identical appearance, 60 mm apart on the mat — a 500 ml and a 1 L bottle
    of the same brand. The metric tiebreak runs first and separates them, so
    refusing this pair would be refusing something identify() handles fine."""
    store.add_sku("bottle_500", "Bottle 500ml", 2000, [vec(1)], 118.4)
    r = store.add_sku("bottle_1l", "Bottle 1L", 3500, [vec(1)], 178.4)
    assert r.ok is True
    assert store.to_gallery().skus() == ("bottle_1l", "bottle_500")


def test_a_different_appearance_escapes_the_guard(store):
    store.add_sku("a", "A", 2000, [basis(0)], 118.4)
    r = store.add_sku("b", "B", 2500, [basis(1)], 118.5)
    assert r.ok is True and r.collides_with is None


def test_an_item_never_collides_with_its_own_outgoing_entry(store):
    """Re-enrolling the same product from a better photo obviously matches the
    entry already on file. If that counted as a collision, no correction would
    ever be possible."""
    store.add_sku("x", "X", 2000, [vec(1)], 118.4)
    r = store.add_sku("x", "X", 2000, [vec(1)], 118.4)
    assert r.ok is True and r.action == ACTION_REPLACED


def test_the_guard_uses_the_same_gates_the_till_will_use(tmp_path):
    """The refusal bound is 1 - theta and tau_mm, read straight off the same
    constants Identifier defaults to. Not a second, friendlier set of numbers."""
    s = ShopStore(tmp_path / "shop")
    assert (s.theta, s.phi, s.tau_mm) == (DEFAULT_THETA, DEFAULT_PHI, DEFAULT_TAU_MM)
    gates = json.loads(
        (tmp_path / "shop" / "catalog.json").read_text()
    ) if (tmp_path / "shop" / "catalog.json").exists() else None
    assert gates is None                      # nothing written until a mutation
    s.add_sku("a", "A", 2000, [vec(1)], 118.4)
    on_disk = json.loads(s.catalog_path.read_text())["gates"]
    # UPDATED: the gates block gained a fourth key when the appearance-only mode
    # landed. It is asserted here by exact equality, not by subset, because a
    # gate that stops being persisted stops being checked on reopen, and this
    # test is the only thing that would notice.
    assert on_disk == {"theta": DEFAULT_THETA, "phi": DEFAULT_PHI,
                       "tau_mm": DEFAULT_TAU_MM,
                       "phi_appearance_only": PHI_APPEARANCE_ONLY}
    assert s.phi_appearance_only == PHI_APPEARANCE_ONLY


def test_a_catalog_admitted_under_different_gates_refuses_to_open(tmp_path):
    d = tmp_path / "shop"
    ShopStore(d).add_sku("a", "A", 2000, [vec(1)], 118.4)
    with pytest.raises(ShopError) as e:
        ShopStore(d, theta=0.30)
    assert "admitted under theta=0.1" in str(e.value)
    assert ShopStore(d).skus() == ("a",)      # the right gates still open it


# ======================================== 5. the projections cannot disagree

def test_to_gallery_and_price_book_agree_on_which_skus_exist(store):
    store.add_sku("a", "A", 2000, [basis(0)], 100.0)
    store.add_sku("b", "B", 2500, [basis(1)], 140.0)
    store.add_sku("c", "C", 3000, [basis(2)], 180.0)
    store.remove("b")

    g = store.to_gallery()
    assert g.skus() == store.skus() == ("a", "c")
    assert set(store.price_map()) == set(g.skus())
    book = store.price_book()
    for sku in g.skus():
        assert book.price_paise(sku) == store.price_paise(sku)
    assert len(book) == len(g)


def test_the_store_is_itself_a_paisa_price_book(store):
    """price_paise(item_id) -> Optional[int] IS the paisa.PriceBook protocol, so
    a ShopStore can be handed straight to build_service."""
    import inspect

    from gawaah.paisa import PriceBook, book_price_paise

    store.add_sku("a", "A", 2000, [basis(0)], 100.0)
    # PriceBook is a plain (non-runtime_checkable) Protocol, so conformance is
    # structural: same method, same parameter name, and it survives the
    # book_price_paise() guard that re-checks every third-party book's answer.
    assert inspect.signature(ShopStore.price_paise).parameters.keys() == (
        inspect.signature(PriceBook.price_paise).parameters.keys()
    )
    assert book_price_paise(store, "a") == 2000
    assert book_price_paise(store, "unknown") is None


def test_an_unknown_sku_prices_none_not_zero(store):
    assert store.price_paise("nope") is None
    assert store.price_book().price_paise("nope") is None


def test_the_gallery_the_store_builds_actually_identifies(store):
    """The point of persisting vectors: a restarted counter still recognises.

    The embedder is a lookup from crop bytes to the vector that was enrolled —
    the same contract a real embedder has (same pixels in, same vector out),
    minus the weights.
    """
    crops = {i: np.full((4, 4), i, np.uint8) for i in range(3)}
    vecs = {i: basis(i) for i in range(3)}
    table = {crops[i].tobytes(): vecs[i] for i in range(3)}

    store.add_sku("a", "A", 2000, [vecs[0]], 100.0)
    store.add_sku("b", "B", 2500, [vecs[1]], 101.0)
    store.add_sku("c", "C", 3000, [vecs[2]], 102.0)

    reopened = ShopStore(store.dir)
    ident = Identifier(reopened.to_gallery(), lambda crop: table[crop.tobytes()])

    got = ident.identify(crops[1], 101.0)
    assert got.sku_id == "b" and got.reason == REASON_MATCH
    assert got.n_candidates == 3
    assert reopened.price_paise(got.sku_id) == 2500

    # something never taught: abstain, and no price is invented
    table[b"unknown"] = np.full(DIM, 0.001)
    stranger = np.frombuffer(b"unknown", np.uint8)
    out = ident.identify(stranger, 101.0)
    assert out.sku_id is None and out.is_amber
    assert reopened.price_paise(out.top1_sku) is not None  # only a suggestion
    assert store.price_paise(None) is None


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    ops=st.lists(
        st.tuples(
            st.sampled_from(["add", "remove"]),
            st.sampled_from(["a", "b", "c", "d"]),
            st.integers(min_value=1, max_value=999_999),
        ),
        max_size=12,
    )
)
def test_the_two_projections_agree_after_any_sequence_of_edits(ops):
    idx = {"a": 0, "b": 1, "c": 2, "d": 3}
    with tempfile.TemporaryDirectory() as tmp:
        s = ShopStore(Path(tmp) / "shop")
        for op, sku, price in ops:
            if op == "add":
                s.add_sku(sku, sku.upper(), price, [basis(idx[sku])],
                          100.0 + 20.0 * idx[sku])
            else:
                s.remove(sku)
            g = s.to_gallery()
            assert g.skus() == s.skus()
            assert set(s.price_map()) == set(g.skus())
            assert len(s.price_book()) == len(g)
        if len(s):
            assert ShopStore(Path(tmp) / "shop").price_map() == s.price_map()


# ================================================ 6. a corrupt file fails loudly

def test_a_truncated_catalog_fails_loudly_rather_than_returning_an_empty_shop(
    tmp_path,
):
    """The failure mode this replaces: a corrupt catalog silently loads as {},
    every item goes amber, and nobody can see why. Loud beats empty."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [vec(1)], 118.4)
    raw = s.catalog_path.read_bytes()
    s.catalog_path.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "not valid JSON" in str(e.value)


def test_a_hand_edited_price_fails_the_digest(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [vec(1)], 118.4)
    text = s.catalog_path.read_text().replace('"price_paise":2000',
                                              '"price_paise":200')
    s.catalog_path.write_text(text)
    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "sha256 mismatch" in str(e.value)


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d.update(format=99), "unsupported catalog format"),
        (lambda d: d.pop("gates"), "missing gates"),
        (lambda d: d.update(skus=[]), "'skus' must be an object"),
        (lambda d: d["skus"]["a"].pop("vectors"), "no vectors supplied"),
        (lambda d: d["skus"]["a"].update(vectors=[]), "no vectors supplied"),
        (lambda d: d["skus"]["a"].update(name=""), "name is empty"),
        (lambda d: d["skus"]["a"].update(footprint_mm=0), "positive real"),
        (lambda d: d["skus"]["a"].update(footprint_mm="118"), "measured long edge"),
        (lambda d: d["skus"]["a"].update(vectors=[[1.0, float("nan")]]),
         "NaN or inf"),
        (lambda d: d["skus"]["a"].update(photo=7), "'photo' must be a string"),
        (lambda d: d.update(dim=999), "declares dim 999"),
    ],
)
def test_a_malformed_catalog_is_named_not_swallowed(tmp_path, mutate, needle):
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [[1.0, 2.0]], 118.4)

    data = json.loads(s.catalog_path.read_text())
    data.pop("sha256")
    mutate(data)
    data["sha256"] = _digest(data)     # digest is honest; the CONTENT is wrong
    s.catalog_path.write_text(json.dumps(data))

    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert needle in str(e.value)


def test_a_missing_digest_is_refused(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [vec(1)], 118.4)
    data = json.loads(s.catalog_path.read_text())
    data.pop("sha256")
    s.catalog_path.write_text(json.dumps(data))
    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "missing sha256" in str(e.value)


def test_the_catalog_is_canonical_bytes(tmp_path):
    """Same shop, same bytes — diffable in git, hashable into the ledger."""
    a, b = tmp_path / "a", tmp_path / "b"
    for d, order in ((a, ("x", "y")), (b, ("y", "x"))):
        s = ShopStore(d)
        prices = {"x": 2000, "y": 2500}
        edges = {"x": 100.0, "y": 140.0}
        seeds = {"x": 0, "y": 1}
        for sku in order:
            s.add_sku(sku, sku.upper(), prices[sku], [basis(seeds[sku])],
                      edges[sku])
    assert (a / "catalog.json").read_bytes() == (b / "catalog.json").read_bytes()


# ================================ 7. the derived sidecar live_app.py already reads

def test_the_sidecar_is_exactly_the_shape_live_app_loads(tmp_path):
    from gawaah.paisa import DictPriceBook

    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("parle_g_200g", "Parle-G", 2000, [basis(0)], 100.0)
    s.add_sku("maggi_70g", "Maggi", 1400, [basis(1)], 140.0)

    raw = json.loads((d / "shop.json").read_text())
    assert raw == {"parle_g_200g": 2000, "maggi_70g": 1400}
    assert raw == s.price_map()
    book = DictPriceBook(raw)                       # what live_app.py does
    assert book.price_paise("maggi_70g") == 1400


def test_a_hand_edited_sidecar_is_caught_rather_than_silently_winning(tmp_path):
    """live_app prices a sale from shop.json; the gallery comes from
    catalog.json. A price typed into shop.json alone would charge a number no
    enrolment ever authorised. There is no winner to pick, so we raise."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [basis(0)], 100.0)
    (d / "shop.json").write_text(json.dumps({"a": 9900}))
    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "disagrees with" in str(e.value) and "['a']" in str(e.value)


def test_a_float_in_the_sidecar_is_caught_even_when_it_equals_the_right_number(
    tmp_path,
):
    """2000 != 2000.0 is False, so a value comparison alone would wave this
    through. A float price is a money bug even when it is the right money."""
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [basis(0)], 100.0)
    (d / "shop.json").write_text(json.dumps({"a": 2000.0}))
    with pytest.raises(ShopError) as e:
        ShopStore(d)
    assert "disagrees with" in str(e.value)


def test_a_legacy_shop_json_is_not_clobbered_by_the_first_enrolment(tmp_path):
    """``results/shop.json`` already exists in this repo with four real prices
    and no catalog beside it. Pointing a fresh store at that directory and
    adding one SKU would rewrite the file with one price and delete the other
    four — prices no catalog can give back, because they were never enrolled.
    So the first save refuses and says what to do."""
    d = tmp_path / "results"
    d.mkdir()
    legacy = {"parle_g_200g": 2000, "lifebuoy_soap": 3500,
              "tata_salt_1kg": 2800, "maggi_70g": 1400}
    (d / "shop.json").write_text(json.dumps(legacy, indent=2))

    s = ShopStore(d)
    assert len(s) == 0                      # a legacy map is not a catalog
    with pytest.raises(ShopError) as e:
        s.add_sku("a", "A", 2000, [basis(0)], 100.0)
    assert "legacy price file" in str(e.value) and "4 price(s)" in str(e.value)
    assert json.loads((d / "shop.json").read_text()) == legacy
    # the refusal fired BEFORE memory changed: a store whose memory held a SKU
    # its disk had never seen would be the exact disagreement this file prevents
    assert len(s) == 0 and s.to_gallery().skus() == () and s.price_map() == {}
    assert not s.catalog_path.exists()

    # opting out of the sidecar is the escape hatch, and it keeps the file
    ok = ShopStore(d, write_sidecar=False)
    assert ok.add_sku("a", "A", 2000, [basis(0)], 100.0).ok
    assert json.loads((d / "shop.json").read_text()) == legacy
    assert ok.catalog_path.exists()


def test_sync_repairs_a_stale_sidecar(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    s.add_sku("a", "A", 2000, [basis(0)], 100.0)
    (d / "shop.json").write_text(json.dumps({"a": 9900, "ghost": 1}))
    s.sync()
    assert json.loads((d / "shop.json").read_text()) == {"a": 2000}
    assert ShopStore(d).price_map() == {"a": 2000}


# ============================================== 8. sku ids, names, vectors

@pytest.mark.parametrize(
    "bad",
    ["", "../../etc/passwd", "a/b", "a b", "-lead", ".dot", "x" * 65,
     "sku\x00", "café"],
)
def test_an_unusable_sku_id_is_refused(store, bad):
    with pytest.raises(ShopError):
        store.add_sku(bad, "X", 2000, [vec(1)], 90.0)
    assert len(store) == 0


def test_a_path_traversal_sku_id_cannot_write_outside_the_store(tmp_path):
    s = ShopStore(tmp_path / "shop")
    with pytest.raises(ShopError):
        s.photo_path("../../pwned")
    assert not (tmp_path.parent / "pwned.png").exists()


@pytest.mark.parametrize("bad", ["", "   ", None, 7, True])
def test_an_unreadable_name_is_refused(store, bad):
    with pytest.raises(ShopError):
        store.add_sku("x", bad, 2000, [vec(1)], 90.0)


def test_a_sku_with_no_vectors_is_refused(store):
    """A priced SKU with no enrolled view would sit in price_book() and not in
    to_gallery(). That is exactly the disagreement this store exists to make
    impossible, so it is refused at the door instead."""
    for empty in ([], (), None, np.zeros((0, DIM))):
        with pytest.raises(IdentityError):
            store.add_sku("x", "X", 2000, empty, 90.0)
    assert len(store) == 0


def test_a_zero_vector_is_refused(store):
    with pytest.raises(IdentityError) as e:
        store.add_sku("x", "X", 2000, [np.zeros(DIM)], 90.0)
    assert "no direction" in str(e.value)


def test_mixed_dimensions_are_refused(store):
    with pytest.raises(IdentityError):
        store.add_sku("x", "X", 2000, [vec(1, 8), vec(2, 9)], 90.0)


def test_a_second_embedder_cannot_be_mixed_into_one_shop(store):
    store.add_sku("a", "A", 2000, [vec(1, 24)], 100.0)
    with pytest.raises(IdentityError) as e:
        store.add_sku("b", "B", 2500, [vec(2, 32)], 140.0)
    assert "cannot hold two embedders" in str(e.value)
    assert store.skus() == ("a",)


@pytest.mark.parametrize("bad", [0, -1, "118", True, float("nan"),
                                 float("inf")])
def test_a_footprint_that_is_not_a_measurement_is_refused(store, bad):
    """UPDATED: ``None`` used to be in this list and is now legal.

    It was the only value here that meant "there was no mat", and lumping it in
    with 0 and NaN is what made a plain product photo unteachable. Everything
    else stays refused, and the distinction is the point: absent is a mode,
    invalid is a bug. See the test immediately below for the other half — a 0 is
    still not a synonym for a missing measurement.
    """
    with pytest.raises(ShopError):
        store.add_sku("x", "X", 2000, [vec(1)], bad)
    assert len(store) == 0


def test_absent_and_invalid_footprints_are_different_things(store):
    """The distinction the parametrisation above now depends on.

    A 0 is not "no measurement", it is a claim that the packet is zero
    millimetres long, and the metric tiebreak would happily compare against it.
    None is the absence of the claim. If these two ever collapse into each
    other, a caller whose measurement silently failed gets an appearance-only
    SKU and is never told.
    """
    ok = store.add_sku("photo_taught", "Photo", 2000, [vec(1)], None)
    assert ok.ok and store.get("photo_taught").footprint_mm is None

    with pytest.raises(ShopError) as e:
        store.add_sku("zero_taught", "Zero", 2000, [vec(2)], 0.0)
    assert "positive real" in str(e.value)
    assert store.skus() == ("photo_taught",)


# ==================================================== 9. photos, with numbers

def test_the_enrolment_photo_is_downscaled_to_the_stated_cap(store):
    import cv2

    big = np.random.default_rng(5).integers(
        0, 256, size=(1200, 800, 3), dtype=np.uint8
    )
    r = store.add_sku("x", "X", 2000, [vec(1)], 90.0, photo_png=big)
    png = store.photo_bytes("x")
    assert len(png) <= PHOTO_CAP_BYTES
    dec = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert max(dec.shape[:2]) <= PHOTO_EDGE_PX
    assert r.photo_bytes == len(png)


def test_an_incompressible_square_photo_falls_down_the_ladder_to_fit(store):
    """Uniform noise is the worst case a PNG can be handed. A 256x256 noise
    square does not fit in 128 KiB, so the ladder drops it to 192 px rather than
    storing something unbounded."""
    import cv2

    noise = np.random.default_rng(1).integers(
        0, 256, size=(1024, 1024, 3), dtype=np.uint8
    )
    store.add_sku("x", "X", 2000, [vec(1)], 90.0, photo_png=noise)
    png = store.photo_bytes("x")
    assert len(png) <= PHOTO_CAP_BYTES
    dec = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert max(dec.shape[:2]) < PHOTO_EDGE_PX


def test_encoded_png_bytes_and_a_raw_crop_are_both_accepted(store):
    import cv2

    img = np.zeros((300, 200, 3), np.uint8)
    img[50:250, 50:150] = (10, 200, 40)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    a = store.add_sku("a", "A", 2000, [basis(0)], 100.0, photo_png=buf.tobytes())
    b = store.add_sku("b", "B", 2500, [basis(1)], 140.0, photo_png=img)
    assert a.ok and b.ok
    assert store.photo_bytes("a") == store.photo_bytes("b")


def test_a_photo_over_the_input_cap_is_refused_before_decode(store):
    bomb = b"\x89PNG\r\n\x1a\n" + b"\x00" * (PHOTO_INPUT_CAP_BYTES + 1)
    with pytest.raises(ShopError) as e:
        store.add_sku("x", "X", 2000, [vec(1)], 90.0, photo_png=bomb)
    assert "before decode" in str(e.value)
    assert len(store) == 0


@pytest.mark.parametrize(
    "bad, needle",
    [
        (b"not an image at all", "did not decode"),
        ("a string", "must be encoded bytes or an ndarray"),
        (np.zeros((10, 10), np.float32), "must be uint8"),
        (np.zeros((0, 10), np.uint8), "unusable shape"),
    ],
)
def test_an_unusable_photo_is_refused_by_name(store, bad, needle):
    with pytest.raises(ShopError) as e:
        store.add_sku("x", "X", 2000, [vec(1)], 90.0, photo_png=bad)
    assert needle in str(e.value)


# ===================================================== 10. cost, and honesty

def test_size_cost_of_json_vectors_is_measured_and_stated(tmp_path, capsys):
    """24 SKUs, 4 views each, 256 dimensions = 24576 float64 values.

    The module docstring claims ~0.5 MB for this shape. This test measures it so
    the claim cannot rot, and prints the number so it can be quoted.
    """
    d = tmp_path / "shop"
    s = ShopStore(d)
    rng = np.random.default_rng(99)
    n_skus, n_views, dim = 24, 4, 256
    for i in range(n_skus):
        block = rng.normal(size=(n_views, dim))
        s.add_sku(f"sku_{i:02d}", f"SKU {i}", 1000 + i * 25, block,
                  60.0 + i * 10.0)

    n_bytes = s.catalog_path.stat().st_size
    values = n_skus * n_views * dim
    raw = values * 8
    with capsys.disabled():
        print(
            f"\n  catalog.json: {n_bytes} bytes for {values} float64 values"
            f" ({n_bytes / values:.1f} B/value, {n_bytes / raw:.2f}x raw"
            f" IEEE-754, {n_bytes / n_skus:.0f} B/sku)"
        )
    assert len(s) == n_skus
    assert n_bytes < 1_000_000                    # the stated ~0.5 MB ceiling
    assert n_bytes > raw                          # text is never cheaper
    assert ShopStore(d).price_map() == s.price_map()


def test_the_store_never_embeds(store):
    """Identifier needs a callable. The store passes one that raises, because
    the store is on the enrolment path only — it guards, it never identifies."""
    from gawaah.shop_store import _never_embeds

    with pytest.raises(ShopError) as e:
        _never_embeds(np.zeros((4, 4), np.uint8))
    assert "holds no embedder" in str(e.value)


def test_no_model_weight_is_loaded_or_downloaded():
    """Invariant 3, checked at the source level: this module names no
    checkpoint, no model hub and no network client."""
    src = (REPO / "gawaah" / "shop_store.py").read_text()
    for banned in ("torch", "onnx", "clip", "urllib", "requests", "httpx",
                   "http.client", "socket", "download", ".pt\"", ".bin\""):
        assert banned not in src.lower().replace("clipped", ""), banned


def test_the_no_float_lint_stays_green_for_this_module():
    """INVARIANT 1: run the real lint, and require it says nothing about us."""
    out = subprocess.run(
        [sys.executable, "tools/lint_no_float.py"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert "shop_store" not in out.stdout, out.stdout


def test_no_temp_file_is_left_behind(tmp_path):
    d = tmp_path / "shop"
    s = ShopStore(d)
    img = np.zeros((200, 200, 3), np.uint8)
    s.add_sku("a", "A", 2000, [basis(0)], 100.0, photo_png=img)
    s.add_sku("b", "B", 2500, [basis(1)], 140.0)
    s.remove("b")
    assert not [p for p in d.rglob("*") if ".tmp" in p.name]
    assert files_under(d) == {"catalog.json", "shop.json", "photos/a.png"}


def test_a_record_reports_its_own_shape(store):
    store.add_sku("a", "A", 2000, [basis(0), basis(1)], 100.0)
    rec = store.get("a")
    assert isinstance(rec, SkuRecord)
    assert (rec.n_views, rec.dim) == (2, DIM)
    assert rec.to_json()["price_paise"] == 2000
    assert store.all() == (rec,)
    assert "a" in store and "z" not in store
    assert "ShopStore(" in repr(store)


def test_the_catalog_format_is_declared_and_checked(store):
    store.add_sku("a", "A", 2000, [basis(0)], 100.0)
    assert json.loads(store.catalog_path.read_text())["format"] == CATALOG_FORMAT


def test_a_result_carries_what_the_ledger_needs(store):
    store.add_sku("a", "A", 2000, [vec(1)], 118.4)
    r = store.add_sku("b", "B", 2500, [vec(1)], 118.5)
    audit = r.to_audit()
    assert json.loads(json.dumps(audit)) == audit      # plain JSON, no numpy
    assert audit["collides_with"] == "a" and audit["ok"] is False
