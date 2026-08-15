"""Tests de app.services.enrichment.

La DB temporal se crea con ``init_db()`` y se puebla a mano (no depende de
app/seed.py). Los umbrales esperados se leen de weights.yaml, no se hardcodean.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import section
from app.db import get_conn, init_db
from app.services import enrichment as en

VALID_GROUPS = {"physical", "visual", "performance", "derived"}
VALID_USE_CASES = {
    "daily running", "race day", "trail running", "lifestyle", "basketball",
    "football", "gym/training", "walking", "casual", "tennis", "skateboarding",
}

PEGASUS_DESC = (
    "Zapatilla de running para entrenamiento diario en asfalto. Parte superior de "
    "engineered mesh transpirable, amortiguacion Zoom Air reactiva y suela de goma "
    "duradera con excelente traccion. Liviana y comoda para rodajes de todos los dias."
)
MERCURIAL_DESC = (
    "Botines de futbol con tapones FG para cesped natural. Upper de cuero sintetico "
    "para un toque preciso y sujecion firme en cancha."
)


def _days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# ── DB temporal ─────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "enrichment.db"
    init_db(path, drop=True)
    lifecycle = section("enrichment", "lifecycle", default={})
    with get_conn(path) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1),(2,'Adidas',0)")
        conn.execute("INSERT INTO retailers (id, name, country_code, channel, importance)"
                     " VALUES (1,'Dexter','AR','B2B',0.8)")
        conn.executemany(
            """INSERT INTO products
               (id, brand_id, country_code, product_name, franchise, category, sport,
                use_case, launch_date, msrp, description)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                # 1: Nike running, sin use_case (debe inferirse)
                (1, 1, "AR", "Nike Air Zoom Pegasus 41 Men's Black/White", "Pegasus",
                 "running", "running", None, _days_ago(30), 145000.0, PEGASUS_DESC),
                # 2: competidor con use_case del scraper (debe respetarse)
                (2, 2, "AR", "Adidas Ultraboost 5 Mujer Azul", "Ultraboost",
                 "running", "running", "running", _days_ago(400), 210000.0, PEGASUS_DESC),
                # 3: futbol, sin launch_date, con descuento fuerte (=> clearance)
                (3, 1, "AR", "Nike Mercurial Vapor 16 FG Unisex Volt", "Mercurial",
                 "football", "football", None, None, 260000.0, MERCURIAL_DESC),
            ],
        )
        conn.executemany(
            "INSERT INTO price_observations (product_id, retailer_id, observed_at, full_price,"
            " current_price, discount_pct, currency) VALUES (?,?,?,?,?,?,?)",
            [
                (1, 1, _days_ago(5), 145000.0, 145000.0, 0.0, "ARS"),
                (1, 1, _days_ago(1), 145000.0, 130500.0, 10.0, "ARS"),
                (3, 1, _days_ago(5), 260000.0, 130000.0, None, "ARS"),
                (3, 1, _days_ago(1), 260000.0, 130000.0, 50.0, "ARS"),
            ],
        )
    assert lifecycle, "weights.yaml debe traer enrichment.lifecycle"
    return path


# ============================================================
# normalize_name
# ============================================================

def test_normalize_name_basic():
    assert en.normalize_name("Nike Air Zoom Pegasus 41") == "nike air zoom pegasus 41"


def test_normalize_name_removes_colors_sizes_and_gender():
    assert en.normalize_name("Nike Air Zoom Pegasus 41 Men's Black/White (Talle 42)") == \
        "nike air zoom pegasus 41"
    assert en.normalize_name("Adidas Ultraboost 5 Mujer Azul") == "adidas ultraboost 5"
    assert en.normalize_name("Zapatillas Nike Dunk Low Retro Unisex Negro Talle 9.5") == \
        "nike dunk low retro"


def test_normalize_name_strips_accents_and_punctuation():
    assert en.normalize_name("Nike Air Máx 90 - Edición Especial!") == "nike air max 90 edicion especial"


def test_normalize_name_is_deterministic_and_idempotent():
    raw = "Nike Air Force 1 '07 Women's White/Black"
    once = en.normalize_name(raw)
    assert once == en.normalize_name(raw)
    assert once == en.normalize_name(once)


def test_normalize_name_handles_empty():
    assert en.normalize_name("") == ""
    assert en.normalize_name(None) == ""


# ============================================================
# infer_use_case
# ============================================================

def test_infer_use_case_respects_scraper_value():
    use_case, confidence = en.infer_use_case({"use_case": "trail running", "category": "football"})
    assert (use_case, confidence) == ("trail running", 1.0)


def test_infer_use_case_canonicalizes_scraper_synonyms():
    assert en.infer_use_case({"use_case": "Running"}) == ("daily running", 1.0)
    assert en.infer_use_case({"use_case": "soccer"}) == ("football", 1.0)


@pytest.mark.parametrize(
    ("product", "expected"),
    [
        ({"category": "running", "description": PEGASUS_DESC}, "daily running"),
        ({"category": "football", "description": MERCURIAL_DESC}, "football"),
        ({"product_name": "Nike Vaporfly 3", "description": "Para race day con placa de carbono"},
         "race day"),
        ({"sport": "trail running", "description": "Para senderos de montana"}, "trail running"),
        ({"category": "basketball", "product_name": "Jordan Luka 3"}, "basketball"),
        ({"product_name": "Nike Metcon 9", "description": "Zapatilla de training para crossfit"},
         "gym/training"),
        ({"product_name": "Nike SB Janoski", "description": "Para skateboarding"}, "skateboarding"),
        ({"product_name": "Nike Air Force 1", "description": "Icono del lifestyle y streetwear"},
         "lifestyle"),
    ],
)
def test_infer_use_case_rules(product, expected):
    use_case, confidence = en.infer_use_case(product)
    assert use_case == expected
    assert use_case in VALID_USE_CASES
    assert 0.0 < confidence < 1.0


def test_infer_use_case_without_evidence():
    assert en.infer_use_case({"product_name": "Producto generico"}) == (None, 0.0)


# ============================================================
# infer_price_band
# ============================================================

def test_infer_price_band_uses_config_bands():
    bands = section("enrichment", "price_bands", "AR")
    for name, (low, high) in bands.items():
        midpoint = (float(low) + min(float(high), float(low) * 2 + 10000)) / 2
        assert en.infer_price_band(midpoint, "AR") == name


def test_infer_price_band_boundaries_are_lower_inclusive():
    bands = section("enrichment", "price_bands", "AR")
    for name, (low, _high) in bands.items():
        assert en.infer_price_band(float(low), "AR") == name


def test_infer_price_band_above_top_band():
    bands = section("enrichment", "price_bands", "AR")
    top = max(bands.items(), key=lambda item: float(item[1][0]))
    assert en.infer_price_band(float(top[1][1]) * 10, "AR") == top[0]


def test_infer_price_band_other_country():
    assert en.infer_price_band(120.0, "US") == "mid"
    assert en.infer_price_band(120.0, "us") == "mid"


def test_infer_price_band_missing_data():
    assert en.infer_price_band(None, "AR") is None
    assert en.infer_price_band(100000.0, "BR") is None      # país sin bandas configuradas
    assert en.infer_price_band(100000.0, "") is None


# ============================================================
# infer_lifecycle_stage
# ============================================================

def test_infer_lifecycle_stage_by_launch_date():
    cfg = section("enrichment", "lifecycle")
    assert en.infer_lifecycle_stage({"launch_date": _days_ago(10)}, None) == "launch"
    assert en.infer_lifecycle_stage(
        {"launch_date": _days_ago(int(cfg["launch_max_days"]) + 10)}, None) == "growth"
    assert en.infer_lifecycle_stage(
        {"launch_date": _days_ago(int(cfg["growth_max_days"]) + 10)}, None) == "mature"
    assert en.infer_lifecycle_stage(
        {"launch_date": _days_ago(int(cfg["mature_max_days"]) + 10)}, None) == "decline"
    assert en.infer_lifecycle_stage(
        {"launch_date": _days_ago(int(cfg["decline_max_days"]) + 10)}, None) == "clearance"


def test_sustained_discount_forces_clearance():
    cfg = section("enrichment", "lifecycle")
    threshold = float(cfg["clearance_discount_pct"])
    product = {"launch_date": _days_ago(10)}                # sería 'launch'
    assert en.infer_lifecycle_stage(product, threshold + 5) == "clearance"
    assert en.infer_lifecycle_stage(product, threshold - 5) == "launch"


def test_infer_lifecycle_without_launch_date():
    cfg = section("enrichment", "lifecycle")
    threshold = float(cfg["clearance_discount_pct"])
    assert en.infer_lifecycle_stage({}, None) is None                  # no inventa
    assert en.infer_lifecycle_stage({}, threshold + 1) == "clearance"
    assert en.infer_lifecycle_stage({}, threshold / 2 + 1) == "decline"
    assert en.infer_lifecycle_stage({}, 1.0) is None


# ============================================================
# enrich_product
# ============================================================

def test_enrich_product_contract_shape():
    product = {
        "product_name": "Nike Air Zoom Pegasus 41 Black/White",
        "category": "running", "sport": "running", "franchise": "Pegasus",
        "msrp": 145000.0, "country_code": "AR", "launch_date": _days_ago(30),
        "description": PEGASUS_DESC,
    }
    result = en.enrich_product(product)

    assert set(result.keys()) == {"fields", "attributes"}
    assert set(result["fields"].keys()) == {
        "normalized_product_name", "use_case", "price_band",
        "lifecycle_stage", "enrichment_version",
    }
    for attribute in result["attributes"]:
        assert set(attribute.keys()) == {
            "attr_group", "attr_name", "value_text", "value_num", "confidence", "source",
        }
        assert attribute["attr_group"] in VALID_GROUPS
        assert attribute["source"] == "rules"
        assert 0.0 <= attribute["confidence"] <= 1.0
        assert attribute["value_text"] is not None or attribute["value_num"] is not None


def test_enrich_product_fields():
    product = {
        "product_name": "Nike Air Zoom Pegasus 41 Black/White",
        "category": "running", "msrp": 145000.0, "country_code": "AR",
        "launch_date": _days_ago(30), "description": PEGASUS_DESC,
    }
    fields = en.enrich_product(product)["fields"]
    assert fields["normalized_product_name"] == "nike air zoom pegasus 41"
    assert fields["use_case"] == "daily running"
    assert fields["price_band"] == en.infer_price_band(145000.0, "AR")
    assert fields["lifecycle_stage"] == "launch"
    assert fields["enrichment_version"] == section("enrichment", "version")


def test_enrich_product_derives_expected_attributes():
    product = {
        "product_name": "Nike Air Zoom Pegasus 41 Black/White",
        "category": "running", "description": PEGASUS_DESC,
    }
    attributes = {a["attr_name"]: a for a in en.enrich_product(product)["attributes"]}

    assert attributes["dominant_color"]["value_text"] == "black"
    assert "white" in attributes["secondary_colors"]["value_text"]
    assert attributes["upper_material"]["value_text"] == "engineered mesh"
    assert attributes["material"]["value_text"] == "textile"
    assert attributes["sole_type"]["value_text"] == "rubber"
    assert attributes["cushioning_type"]["value_text"] in {"zoom air", "air max", "zoomx", "react"}
    assert attributes["silhouette"]["value_text"] == "runner"

    for rating in ("cushioning", "comfort", "breathability", "traction", "weight"):
        assert attributes[rating]["value_num"] is not None
        assert 0.0 <= attributes[rating]["value_num"] <= 1.0
        assert attributes[rating]["attr_group"] == "derived"
        assert attributes[rating]["value_text"] is None


def test_enrich_product_without_evidence_emits_nothing():
    result = en.enrich_product({"product_name": "Producto 123"})
    assert result["attributes"] == []
    assert result["fields"]["use_case"] is None
    assert result["fields"]["price_band"] is None
    assert result["fields"]["lifecycle_stage"] is None


def test_enrich_product_uses_context_discount():
    threshold = float(section("enrichment", "lifecycle")["clearance_discount_pct"])
    product = {"product_name": "Nike Pegasus 41", "launch_date": _days_ago(10)}
    result = en.enrich_product(product, {"avg_discount_pct": threshold + 10})
    assert result["fields"]["lifecycle_stage"] == "clearance"


# ============================================================
# run_enrichment
# ============================================================

def test_run_enrichment_counts_and_persistence(db):
    counts = en.run_enrichment(db)
    assert counts["products"] == 3
    assert counts["updated"] == 3
    assert counts["attributes"] > 0
    assert counts["use_case_inferred"] == 2          # productos 1 y 3 (el 2 ya venía)

    with get_conn(db) as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM products").fetchall()}
        attrs = [dict(r) for r in conn.execute("SELECT * FROM product_attributes").fetchall()]

    assert rows[1]["normalized_product_name"] == "nike air zoom pegasus 41"
    assert rows[1]["use_case"] == "daily running"
    assert rows[1]["price_band"] == en.infer_price_band(145000.0, "AR")
    assert rows[1]["lifecycle_stage"] == "launch"
    assert rows[1]["enrichment_version"] == section("enrichment", "version")

    # El use_case del scraper se respeta (canonicalizado o tal cual).
    assert rows[2]["use_case"] == "running"
    assert rows[2]["lifecycle_stage"] == "mature"      # 400 días desde el launch

    # Descuento promedio sostenido (50%) fuerza clearance aunque no haya launch_date.
    assert rows[3]["use_case"] == "football"
    assert rows[3]["lifecycle_stage"] == "clearance"
    assert rows[3]["normalized_product_name"] == "nike mercurial vapor 16 fg"

    assert attrs, "debe persistir atributos"
    assert {a["attr_group"] for a in attrs} <= VALID_GROUPS
    assert all(a["source"] == "rules" for a in attrs)
    assert len(attrs) == counts["attributes"]


def _snapshot(path) -> tuple[list[dict], list[dict]]:
    with get_conn(path) as conn:
        products = [dict(r) for r in conn.execute(
            "SELECT id, normalized_product_name, use_case, price_band, lifecycle_stage,"
            " enrichment_version FROM products ORDER BY id").fetchall()]
        attributes = [dict(r) for r in conn.execute(
            "SELECT product_id, attr_group, attr_name, value_text, value_num, confidence, source"
            " FROM product_attributes ORDER BY product_id, attr_name").fetchall()]
    return products, attributes


def test_run_enrichment_is_idempotent(db):
    first = en.run_enrichment(db)
    snapshot_1 = _snapshot(db)
    second = en.run_enrichment(db)
    snapshot_2 = _snapshot(db)

    assert first["attributes"] == second["attributes"]
    assert snapshot_1 == snapshot_2                  # UPSERT: mismo estado, sin duplicados
    assert len(snapshot_1[1]) == first["attributes"]


def test_run_enrichment_does_not_overwrite_scraper_attributes(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO product_attributes (product_id, attr_group, attr_name, value_text,"
            " confidence, source) VALUES (1,'visual','dominant_color','multicolor',1.0,'scraper')"
        )
    en.run_enrichment(db)
    with get_conn(db) as conn:
        row = dict(conn.execute(
            "SELECT * FROM product_attributes WHERE product_id = 1 AND attr_name = 'dominant_color'"
        ).fetchone())
    assert row["value_text"] == "multicolor"
    assert row["source"] == "scraper"


def test_run_enrichment_on_empty_db(tmp_path):
    path = tmp_path / "empty.db"
    init_db(path, drop=True)
    counts = en.run_enrichment(path)
    assert counts["products"] == 0
    assert counts["attributes"] == 0
