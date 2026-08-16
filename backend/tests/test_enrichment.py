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
# división (FW / AP / EQ)
# ============================================================

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FOOTWEAR DIVISION", "FW"), ("Footwear", "FW"), ("fw", "FW"),
        ("Calzado", "FW"), ("ZAPATILLAS", "FW"),
        ("APPAREL DIVISION", "AP"), ("Apparel", "AP"), ("ap", "AP"),
        ("Indumentaria", "AP"),
        ("EQUIPMENT DIVISION", "EQ"), ("eq", "EQ"), ("Accesorios", "EQ"),
        ("accessories", "EQ"),
        ("running", None), ("", None), (None, None),
    ],
)
def test_normalize_division_matches_frontend_criterion(raw, expected):
    """Mismo criterio que `web/src/lib/utils.ts::normalizeDivision`, en códigos."""
    assert en.normalize_division(raw) == expected


def test_infer_division_prefers_the_explicit_column():
    assert en.infer_division({"division": "APPAREL", "category": "footwear"}) == "AP"


def test_infer_division_rescues_the_legacy_category():
    """Hasta la unificación, la ingesta guardaba la división dentro de `category`."""
    assert en.infer_division({"category": "footwear", "sport": "running"}) == "FW"
    assert en.infer_division({"category": "apparel"}) == "AP"
    assert en.infer_division({"category": "accessories"}) == "EQ"


def test_infer_division_from_text_evidence():
    assert en.infer_division(
        {"category": "football", "description": "Camiseta de futbol para hincha, tela liviana."}
    ) == "AP"
    assert en.infer_division(
        {"category": "running", "description": PEGASUS_DESC}
    ) == "FW"


def test_infer_division_without_evidence_is_none():
    assert en.infer_division({"product_name": "Producto generico"}) is None


def test_infer_division_is_idempotent():
    """Segunda corrida: `category` ya es la categoría deportiva, `division` manda."""
    once = en.infer_division({"category": "footwear", "sport": "running"})
    assert en.infer_division({"division": once, "category": "running", "sport": "running"}) == once


# ============================================================
# unificación category / sport
# ============================================================

def test_unify_category_drops_the_division_stored_in_category():
    assert en.unify_category({"category": "footwear", "sport": "running"}) == "running"


def test_unify_category_prefers_category_when_it_is_a_real_category():
    assert en.unify_category({"category": "football", "sport": "soccer"}) == "football"


def test_unify_category_falls_back_to_sport_and_then_use_case():
    assert en.unify_category({"category": None, "sport": "basketball"}) == "basketball"
    assert en.unify_category({}, "daily running") == "running"
    assert en.unify_category({}, "gym/training") == "training"
    assert en.unify_category({}) is None                    # sin evidencia no inventa


def test_unify_category_is_idempotent():
    once = en.unify_category({"category": "footwear", "sport": "running"})
    assert en.unify_category({"category": once, "sport": once}) == once


# ============================================================
# infer_price_band  (bandas EN PLATA)
# ============================================================

def test_price_band_keys_are_canonical_labels():
    """La clave del yaml TIENE que ser el label derivado de los montos.

    `matching._price_band_similarity` usa esas claves para medir distancia
    ordinal entre bandas, así que una clave mal escrita degradaría el scoring
    en silencio. Acá falla ruidosamente.
    """
    countries = section("enrichment", "price_bands", default={}) or {}
    assert countries, "weights.yaml debe traer enrichment.price_bands"
    for country in countries:
        bands = en.price_bands(country)
        assert bands, f"{country} sin bandas"
        for band in bands:
            assert band["label"] == band["canonical_label"], (
                f"{country}: la banda '{band['label']}' no coincide con los montos "
                f"{band['min']}..{band['upper_bound']} (esperado '{band['canonical_label']}')"
            )


def test_price_bands_are_declared_in_ascending_order():
    """El ORDEN de las claves es la escala ordinal que lee `matching`."""
    countries = section("enrichment", "price_bands", default={}) or {}
    for country, raw in countries.items():
        declared = list(raw)
        assert declared == [b["label"] for b in en.price_bands(country)]


def test_price_band_label_expresses_money_not_a_tier():
    assert en.price_band_label(90000, 160000, "AR") == "90.000-160.000"
    assert en.price_band_label(260000, 99999999, "AR", open_top=True) == "260.000+"
    assert en.price_band_label(90, 140, "US") == "90-140"


def test_infer_price_band_uses_config_bands():
    for band in en.price_bands("AR"):
        midpoint = (band["min"] + min(band["upper_bound"], band["min"] * 2 + 10000)) / 2
        assert en.infer_price_band(midpoint, "AR") == band["label"]


def test_infer_price_band_boundaries_are_lower_inclusive():
    for band in en.price_bands("AR"):
        assert en.infer_price_band(band["min"], "AR") == band["label"]


def test_infer_price_band_above_top_band():
    top = en.price_bands("AR")[-1]
    assert en.infer_price_band(top["upper_bound"] * 10, "AR") == top["label"]


def test_infer_price_band_other_country():
    us_band = en.band_for_price(120.0, "US")
    assert us_band["min"] == 90 and us_band["upper_bound"] == 140
    assert en.infer_price_band(120.0, "US") == us_band["label"]
    assert en.infer_price_band(120.0, "us") == us_band["label"]


def test_price_band_bounds_are_amounts_and_top_band_is_open():
    low, high = en.infer_price_band_bounds(145000.0, "AR")
    assert (low, high) == (90000.0, 160000.0)
    top_low, top_high = en.infer_price_band_bounds(500000.0, "AR")
    assert top_low == 260000.0 and top_high is None     # banda abierta: sin centinela


def test_price_tier_keeps_the_legacy_qualitative_alias():
    tiers = section("enrichment", "price_band_tiers", "AR")
    assert [b["tier"] for b in en.price_bands("AR")] == list(tiers)
    assert en.infer_price_tier(145000.0, "AR") == "mid"


def test_price_band_count_comes_from_config(monkeypatch):
    """Cambiar la cantidad de bandas NO requiere tocar código."""
    from app import config as app_config

    original = app_config.get_config()
    patched = {**original, "enrichment": {**original["enrichment"],
                                          "price_bands": {"AR": {"0-50.000": [0, 50000],
                                                                 "50.000+": [50000, 99999999]}},
                                          "price_band_tiers": {"AR": ["low", "high"]}}}
    monkeypatch.setattr(app_config, "get_config", lambda: patched)
    assert len(en.price_bands("AR")) == 2
    assert en.infer_price_band(10000.0, "AR") == "0-50.000"
    assert en.infer_price_band(900000.0, "AR") == "50.000+"
    assert en.infer_price_tier(900000.0, "AR") == "high"


def test_infer_price_band_missing_data():
    assert en.infer_price_band(None, "AR") is None
    assert en.infer_price_band(100000.0, "BR") is None      # país sin bandas configuradas
    assert en.infer_price_band(100000.0, "") is None
    assert en.infer_price_band_bounds(None, "AR") == (None, None)
    assert en.infer_price_tier(None, "AR") is None


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
        "normalized_product_name", "division", "category", "sport", "use_case",
        "price_band", "price_band_min", "price_band_max", "price_tier",
        "lifecycle_stage", "enrichment_version",
    }
    # `category` y `sport` son la misma dimensión: siempre salen sincronizados.
    assert result["fields"]["category"] == result["fields"]["sport"]
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
    assert (fields["price_band_min"], fields["price_band_max"]) == \
        en.infer_price_band_bounds(145000.0, "AR")
    assert fields["price_tier"] == en.infer_price_tier(145000.0, "AR")
    assert fields["division"] == "FW"
    assert fields["category"] == fields["sport"] == "running"
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
    assert result["fields"]["price_band_min"] is None
    assert result["fields"]["price_band_max"] is None
    assert result["fields"]["price_tier"] is None
    assert result["fields"]["division"] is None
    assert result["fields"]["category"] is None
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

    assert counts["division_set"] == 3               # las tres tienen evidencia de calzado

    assert rows[1]["normalized_product_name"] == "nike air zoom pegasus 41"
    assert rows[1]["use_case"] == "daily running"
    assert rows[1]["price_band"] == en.infer_price_band(145000.0, "AR")
    assert (rows[1]["price_band_min"], rows[1]["price_band_max"]) == \
        en.infer_price_band_bounds(145000.0, "AR")
    assert rows[1]["price_tier"] == en.infer_price_tier(145000.0, "AR")
    assert rows[1]["lifecycle_stage"] == "launch"
    assert rows[1]["enrichment_version"] == section("enrichment", "version")

    # División poblada y category/sport sincronizados en TODAS las filas.
    for row in rows.values():
        assert row["division"] == "FW"
        assert row["category"] == row["sport"]
    assert rows[1]["category"] == "running"
    assert rows[3]["category"] == "football"

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


def test_run_enrichment_migrates_the_legacy_taxonomy(tmp_path):
    """`category` traía la división: se rescata en `division` y se reescribe."""
    path = tmp_path / "legacy.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1)")
        conn.executemany(
            """INSERT INTO products
               (id, brand_id, country_code, product_name, category, sport, msrp, description)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (1, 1, "AR", "Nike Pegasus 41", "footwear", "running", 145000.0, PEGASUS_DESC),
                (2, 1, "AR", "Nike Camiseta Argentina", "apparel", "football", 95000.0,
                 "Camiseta de futbol de la seleccion, tela liviana y transpirable."),
            ],
        )

    counts = en.run_enrichment(path)
    assert counts["category_unified"] == 2

    with get_conn(path) as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM products").fetchall()}

    assert (rows[1]["division"], rows[1]["category"], rows[1]["sport"]) == ("FW", "running", "running")
    assert (rows[2]["division"], rows[2]["category"], rows[2]["sport"]) == ("AP", "football", "football")

    # Segunda corrida: ya no queda nada por unificar y el estado no cambia.
    again = en.run_enrichment(path)
    assert again["category_unified"] == 0
    with get_conn(path) as conn:
        assert {r["id"]: dict(r)["category"] for r in
                conn.execute("SELECT id, category FROM products")} == {1: "running", 2: "football"}


def _snapshot(path) -> tuple[list[dict], list[dict]]:
    with get_conn(path) as conn:
        products = [dict(r) for r in conn.execute(
            "SELECT id, normalized_product_name, division, category, sport, use_case,"
            " price_band, price_band_min, price_band_max, price_tier, lifecycle_stage,"
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
