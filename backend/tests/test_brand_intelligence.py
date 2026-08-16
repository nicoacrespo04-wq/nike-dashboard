"""Tests del módulo Consumer & Brand Intelligence (Argentina).

Las filas se insertan A MANO (no dependemos de ``app/seed.py``). El "hoy" del
análisis se deriva del máximo ``period_end`` de los datos, así que las fechas
del fixture fijan las ventanas de forma determinista:

    hoy      = 2025-06-30
    actual   = (2025-05-31, 2025-06-30]
    anterior = (2025-05-01, 2025-05-31]
    previo   = (2025-04-01, 2025-05-01]
"""

from __future__ import annotations

import json

import pytest

from app.config import section
from app.db import get_conn, init_db, query
from app.services.brand_intelligence import (
    build_context,
    brand_momentum,
    classify_text,
    conversation_momentum,
    detect_trends,
    generate_insights,
    run_brand_intelligence,
    social_competition_signals,
)

TODAY = "2025-06-30"
CURRENT_END = "2025-06-30"
PREVIOUS_END = "2025-05-31"
PRIOR_END = "2025-04-30"

MEDIUM_MIN = int(section("brand_intelligence", "confidence", "medium_min_volume"))
HIGH_MIN = int(section("brand_intelligence", "confidence", "high_min_volume"))
# Piso de EMISIÓN (anti-invención). Es una clave propia y no el corte de
# confianza MEDIUM: son dos decisiones distintas ("¿existe el insight?" vs
# "¿con cuánta precisión se afirma?") y acoplarlas obligaba a elegir entre una
# confianza que no discrimina o un tablero al que le faltan insights reales.
EMIT_MIN = int(section("brand_intelligence", "confidence", "min_volume_to_emit"))
# Base mínima en la ventana anterior para que una variación relativa se publique.
MIN_BASE = float(section("brand_intelligence", "momentum", "min_base_volume"))


# ── helpers ─────────────────────────────────────────────────


def _exec(db, sql, rows):
    with get_conn(db) as conn:
        conn.executemany(sql, rows)


def _reference_data(db):
    """Marcas, país, retailer y productos comunes a todos los fixtures."""
    _exec(db, "INSERT INTO countries (code, name, currency) VALUES (?,?,?)",
          [("AR", "Argentina", "ARS")])
    _exec(db, "INSERT INTO brands (id, name, is_focus) VALUES (?,?,?)",
          [(1, "Nike", 1), (2, "Adidas", 0)])
    _exec(db, "INSERT INTO retailers (id, name, country_code, channel, importance) "
              "VALUES (?,?,?,?,?)",
          [(1, "Dexter", "AR", "B2B", 0.8)])
    _exec(db, "INSERT INTO products (id, brand_id, country_code, product_name, franchise, "
              "category, sport, use_case) VALUES (?,?,?,?,?,?,?,?)",
          [(1, 1, "AR", "Nike Pegasus 41", "Pegasus", "footwear", "running", "daily running"),
           (2, 2, "AR", "Adidas Adizero SL", "Adizero", "footwear", "running", "daily running"),
           (3, 2, "AR", "Adidas Ultraboost 5", "Ultraboost", "footwear", "running",
            "daily running"),
           (4, 1, "AR", "Nike Air Force 1", "Air Force 1", "footwear", "lifestyle", "lifestyle")])


_SOCIAL_SQL = (
    "INSERT INTO social_mention_aggregates "
    "(period_start, period_end, country_code, brand_id, product_id, co_product_id, "
    " source_type, mention_count, comention_count, sentiment_score, intent, topic, "
    " sample_evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _social(period_end, brand_id, product_id, topic, mentions, sentiment,
            *, co_product_id=None, comentions=0, intent=None, evidence=(),
            source_type="forum"):
    starts = {CURRENT_END: "2025-06-01", PREVIOUS_END: "2025-05-01", PRIOR_END: "2025-04-01"}
    return (starts[period_end], period_end, "AR", brand_id, product_id, co_product_id,
            source_type, mentions, comentions, sentiment, intent, topic,
            json.dumps(list(evidence), ensure_ascii=False))


@pytest.fixture()
def rich_db(tmp_path):
    """DB con conversación social, reviews y menciones editoriales de AR."""
    db = tmp_path / "rich.db"
    init_db(db, drop=True)
    _reference_data(db)

    _exec(db, _SOCIAL_SQL, [
        # ── conversación por tópico ──
        _social(CURRENT_END, 1, 1, "performance", 120, 0.45, intent="comparing",
                evidence=["Las Pegasus 41 vuelan en los 10k",
                          "Para entrenar todos los días no fallan"]),
        _social(PREVIOUS_END, 1, 1, "performance", 100, 0.50,
                evidence=["Las Pegasus responden bien en fondo largo"]),
        _social(PRIOR_END, 1, 1, "performance", 95, 0.50,
                evidence=["Buenas para entrenar"]),

        _social(CURRENT_END, 2, 2, "performance", 30, 0.40,
                evidence=["Las Adizero rinden para series"]),
        _social(PREVIOUS_END, 2, 2, "performance", 25, 0.40,
                evidence=["Adizero para series"]),

        _social(CURRENT_END, 1, 1, "overpriced", 90, -0.60,
                evidence=["Carísimas para lo que son",
                          "No justifica el precio que tienen acá"]),
        _social(PREVIOUS_END, 1, 1, "overpriced", 50, -0.30,
                evidence=["Están caras pero se bancan"]),
        _social(PRIOR_END, 1, 1, "overpriced", 45, -0.25,
                evidence=["Precio alto"]),

        _social(CURRENT_END, 2, 2, "fashionable", 140, 0.55, source_type="social",
                evidence=["Las Adidas combinan con todo",
                          "Re cancheras las nuevas siluetas"]),
        _social(PREVIOUS_END, 2, 2, "fashionable", 60, 0.50, source_type="social",
                evidence=["Cancheras las Adidas"]),
        _social(PRIOR_END, 2, 2, "fashionable", 55, 0.50, source_type="social",
                evidence=["Adidas moda"]),
        _social(CURRENT_END, 1, 4, "fashionable", 40, 0.30, source_type="social",
                evidence=["Las AF1 nunca fallan para el día a día"]),
        _social(PREVIOUS_END, 1, 4, "fashionable", 35, 0.30, source_type="social",
                evidence=["AF1 siempre"]),

        # ── señal por debajo del volumen mínimo (no debe generar insight) ──
        _social(CURRENT_END, 1, 4, "sneaker_culture", 5, 0.90, source_type="community",
                evidence=["Salió un retro nuevo"]),
        _social(PREVIOUS_END, 1, 4, "sneaker_culture", 1, 0.90, source_type="community",
                evidence=["Retro"]),

        # ── co-menciones producto ↔ co_producto ──
        _social(CURRENT_END, 1, 1, "comparing", 60, 0.10, co_product_id=2, comentions=60,
                intent="comparing",
                evidence=["Pegasus 41 vs Adizero SL, ¿cuál conviene?"]),
        _social(PREVIOUS_END, 1, 1, "comparing", 20, 0.10, co_product_id=2, comentions=20,
                intent="comparing", evidence=["Pegasus o Adizero"]),
        _social(PRIOR_END, 1, 1, "comparing", 10, 0.10, co_product_id=2, comentions=10,
                intent="comparing", evidence=["Pegasus o Adizero"]),
        _social(CURRENT_END, 1, 1, "comparing", 25, 0.05, co_product_id=3, comentions=25,
                intent="comparing", evidence=["Pegasus o Ultraboost para fondo largo"]),
        _social(PREVIOUS_END, 1, 1, "comparing", 24, 0.05, co_product_id=3, comentions=24,
                intent="comparing", evidence=["Pegasus o Ultraboost"]),
        _social(PRIOR_END, 1, 1, "comparing", 20, 0.05, co_product_id=3, comentions=20,
                intent="comparing", evidence=["Pegasus o Ultraboost"]),
    ])

    _exec(db, "INSERT INTO reviews (product_id, retailer_id, source, rating, review_count, "
              "review_text, observed_at) VALUES (?,?,?,?,?,?,?)", [
        (1, 1, "dexter", 5.0, 60,
         "El diseño es hermoso, las más lindas que tuve", "2025-06-05"),
        (1, 1, "dexter", 2.0, 35,
         "Buen diseño pero no hay talles y es difícil de conseguir el colorway",
         "2025-06-12"),
        (1, 1, "dexter", 4.5, 40,
         "Comodísimas y con muy buena amortiguación para correr", "2025-06-10"),
        (1, 1, "dexter", 2.5, 20,
         "No había talles, difícil de conseguir", "2025-05-20"),
        (1, 1, "dexter", 5.0, 45,
         "El diseño es hermoso y muy cómodas", "2025-05-10"),
    ])

    _exec(db, "INSERT INTO editorial_mentions (source_name, url, title, published_at, "
              "mention_type, product_a_id, product_b_id, excerpt, country_code) "
              "VALUES (?,?,?,?,?,?,?,?,?)", [
        ("Infobae", "https://ejemplo.ar/guia", "Guía de zapatillas para correr 2025",
         "2025-06-20", "versus", 1, 2,
         "Comparamos Pegasus 41 contra Adizero SL para running de fondo", "AR"),
        ("Infobae", "https://ejemplo.ar/guia-mayo", "Las mejores para running",
         "2025-05-20", "versus", 1, 2,
         "Pegasus y Adizero siguen liderando el running en Argentina", "AR"),
    ])
    return db


@pytest.fixture()
def two_window_db(tmp_path):
    """DB mínima para verificar aritmética de tendencia y dirección."""
    db = tmp_path / "windows.db"
    init_db(db, drop=True)
    _reference_data(db)
    _exec(db, _SOCIAL_SQL, [
        _social(CURRENT_END, 1, 1, "performance", 150, 0.2, evidence=["crece"]),
        _social(PREVIOUS_END, 1, 1, "performance", 100, 0.2, evidence=["base"]),
        _social(PRIOR_END, 1, 1, "performance", 100, 0.2, evidence=["base previa"]),
        _social(CURRENT_END, 2, 2, "performance", 50, 0.2, evidence=["cae"]),
        _social(PREVIOUS_END, 2, 2, "performance", 100, 0.2, evidence=["base rival"]),
        _social(PRIOR_END, 2, 2, "performance", 100, 0.2, evidence=["base rival previa"]),
    ])
    return db


# ── tablas vacías ───────────────────────────────────────────


def test_empty_tables_do_not_break(tmp_path):
    db = tmp_path / "empty.db"
    init_db(db, drop=True)

    ctx = build_context(db)
    assert ctx.today is None
    assert ctx.has_data is False
    assert generate_insights(ctx) == []
    assert detect_trends(ctx) == []
    assert social_competition_signals(ctx) == []
    assert brand_momentum(1, ctx)["volume"] == 0
    assert conversation_momentum("performance", ctx)["score"] == 0.0

    counts = run_brand_intelligence(db)
    assert counts == {"market_signals": 0, "brand_insights": 0, "trends": 0,
                      "social_competition_pairs": 0}
    assert query("SELECT * FROM brand_insights", path=db) == []


def test_only_reference_data_does_not_break(tmp_path):
    """Marcas y productos cargados pero sin ninguna observación."""
    db = tmp_path / "refs.db"
    init_db(db, drop=True)
    _reference_data(db)
    counts = run_brand_intelligence(db)
    assert counts["brand_insights"] == 0
    assert counts["market_signals"] == 0


# ── ventanas, tendencia y dirección ─────────────────────────


def test_today_derives_from_max_period_end(rich_db):
    ctx = build_context(rich_db)
    assert ctx.today.isoformat() == TODAY
    assert ctx.period_end == TODAY
    assert ctx.period_start == "2025-05-31"


def test_trend_and_direction_between_windows(two_window_db):
    ctx = build_context(two_window_db)

    up = brand_momentum(1, ctx)
    assert up["volume"] == 150 and up["volume_previous"] == 100
    assert up["trend"] == pytest.approx(50.0)          # (150-100)/100
    assert up["direction"] == "up"
    assert up["acceleration"] == pytest.approx(0.5)    # 0.5 (actual) - 0.0 (anterior)
    assert up["spike"] is True                         # |0.5| >= spike_threshold

    down = brand_momentum(2, ctx)
    assert down["trend"] == pytest.approx(-50.0)
    assert down["direction"] == "down"
    assert down["acceleration"] == pytest.approx(-0.5)
    assert down["score"] < up["score"]

    topic = conversation_momentum("performance", ctx)
    assert topic["volume"] == 200 and topic["volume_previous"] == 200
    assert topic["direction"] == "flat"
    assert topic["trend"] == pytest.approx(0.0)


def test_momentum_score_is_bounded_and_explainable(two_window_db):
    ctx = build_context(two_window_db)
    result = brand_momentum(1, ctx)
    assert 0.0 <= result["score"] <= 100.0
    names = {f["factor"] for f in result["factors"]}
    assert names == {"volume", "trend", "acceleration"}
    assert sum(f["contribution"] for f in result["factors"]) == pytest.approx(100.0, abs=0.1)


def test_momentum_degrades_without_third_window(tmp_path):
    """Sin ventana previa a la anterior, la aceleración se marca no disponible."""
    db = tmp_path / "two.db"
    init_db(db, drop=True)
    _reference_data(db)
    _exec(db, _SOCIAL_SQL, [
        _social(CURRENT_END, 1, 1, "performance", 120, 0.2, evidence=["a"]),
        _social(PREVIOUS_END, 1, 1, "performance", 60, 0.2, evidence=["b"]),
    ])
    result = brand_momentum(1, build_context(db))
    assert result["acceleration"] is None
    accel = next(f for f in result["factors"] if f["factor"] == "acceleration")
    assert accel["available"] is False
    assert result["score"] > 0            # se renormaliza, no penaliza


# ── regla inviolable: sin volumen o sin evidencia, no se emite ──


def test_low_volume_signal_is_not_emitted(rich_db):
    """`sneaker_culture` tiene 5 menciones (< medium_min_volume) => no hay insight."""
    ctx = build_context(rich_db)
    insights = generate_insights(ctx)
    assert insights, "el fixture debe producir insights"
    assert all(i["topic"] != "sneaker_culture" for i in insights)
    assert all(i["signal_volume"] >= EMIT_MIN for i in insights)


def test_every_insight_has_evidence_and_coherent_confidence(rich_db):
    ctx = build_context(rich_db)
    insights = generate_insights(ctx)
    assert insights

    # Todos los textos públicos que existen en la DB (social + reviews + editorial).
    corpus = set()
    for row in query("SELECT sample_evidence FROM social_mention_aggregates", path=rich_db):
        corpus.update(json.loads(row["sample_evidence"] or "[]"))
    for row in query("SELECT review_text FROM reviews", path=rich_db):
        corpus.add((row["review_text"] or "").strip())
    for row in query("SELECT excerpt FROM editorial_mentions", path=rich_db):
        corpus.add((row["excerpt"] or "").strip())

    for insight in insights:
        evidence = insight["evidence"]
        assert evidence["examples"], f"insight sin evidencia: {insight['insight_text']}"
        assert 1 <= len(evidence["examples"]) <= 3
        assert evidence["sources"]
        for example in evidence["examples"]:
            # Ninguna cita es inventada: sale textual de la base.
            assert example["text"] in corpus
        assert insight["direction"] in ("up", "down", "flat")
        assert insight["insight_text"].strip()
        expected = ("HIGH" if insight["signal_volume"] >= HIGH_MIN
                    else "MEDIUM" if insight["signal_volume"] >= MEDIUM_MIN else "LOW")
        assert insight["confidence"] == expected


def test_insight_without_evidence_is_dropped(tmp_path):
    """Mismo volumen alto, pero `sample_evidence` vacío => no se emite nada."""
    db = tmp_path / "no_evidence.db"
    init_db(db, drop=True)
    _reference_data(db)
    _exec(db, _SOCIAL_SQL, [
        _social(CURRENT_END, 1, 1, "overpriced", 200, -0.8, evidence=[]),
        _social(PREVIOUS_END, 1, 1, "overpriced", 50, -0.2, evidence=[]),
        _social(PRIOR_END, 1, 1, "overpriced", 40, -0.2, evidence=[]),
    ])
    ctx = build_context(db)
    assert ctx.has_data is True
    assert generate_insights(ctx) == []


def test_insight_templates_are_parameterized_with_real_metrics(rich_db):
    ctx = build_context(rich_db)
    by_type: dict[str, list] = {}
    for insight in generate_insights(ctx):
        by_type.setdefault(insight["insight_type"], []).append(insight)

    # El precio como driver negativo: sale del tópico con sentimiento negativo.
    negative = next(i for i in by_type["negative_driver"] if i["topic"] == "overpriced")
    assert negative["sentiment"] < 0
    assert negative["signal_volume"] == 90        # menciones reales del período
    assert negative["trend"] == pytest.approx(80.0)   # (90-50)/50
    assert negative["direction"] == "up"
    assert all(i["sentiment"] < 0 for i in by_type["negative_driver"])

    # Liderazgo de tópico vs competidor que gana otra conversación.
    leadership = by_type["brand_topic_leadership"][0]
    assert "Nike" in leadership["insight_text"] and "Adidas" in leadership["insight_text"]
    assert leadership["evidence"]["metrics"]["focus_topic"] == "performance"
    assert leadership["evidence"]["metrics"]["rival_topic"] == "fashionable"

    # Elogio vs fricción: la fricción sale de la dimensión availability.
    praise = by_type["praise_vs_friction"][0]
    assert praise["dimension"] == "availability"
    assert praise["evidence"]["metrics"]["praised_topic"] == "design"

    # Desplazamiento de comparación (Pegasus contra Adizero antes que Ultraboost).
    shift = by_type["comparison_shift"][0]
    metrics = shift["evidence"]["metrics"]
    assert metrics["rising_product_id"] == 2 and metrics["reference_product_id"] == 3
    assert metrics["rising_trend_ratio"] > metrics["reference_trend_ratio"]


# ── clasificación léxica de reviews ─────────────────────────


@pytest.mark.parametrize("text, expected", [
    ("Comodísimas y con muy buena amortiguación", {("product_perception", "comfort"),
                                                   ("product_perception", "technology")}),
    ("Carísimas para lo que son", {("price_perception", "expensive"),
                                   ("price_perception", "overpriced")}),
    ("No había talles, difícil de conseguir", {("availability", "sizing_availability"),
                                               ("availability", "difficult_to_find")}),
    ("Las volvería a comprar sin dudarlo", {("consumer_intent", "repurchase")}),
    ("", set()),
    (None, set()),
])
def test_review_lexicon_classification(text, expected):
    taxonomy = section("brand_intelligence", "taxonomy")
    hits = set(classify_text(text, taxonomy))
    assert expected <= hits
    # Sólo devuelve pares declarados en la taxonomía de weights.yaml.
    for dimension, topic in hits:
        assert topic in taxonomy[dimension]


def test_reviews_feed_the_taxonomy(rich_db):
    ctx = build_context(rich_db)
    from_reviews = {(r["dimension"], r["topic"]) for r in ctx.records
                    if r["source"] == "review"}
    assert ("product_perception", "design") in from_reviews
    assert ("availability", "difficult_to_find") in from_reviews


# ── tendencias ──────────────────────────────────────────────


def test_detect_trends_finds_acceleration_and_competitor(rich_db):
    trends = detect_trends(build_context(rich_db))
    types = {t["trend_type"] for t in trends}
    assert "spike" in types or "acceleration" in types
    assert "emerging_competitor" in types
    for trend in trends:
        assert trend["period_end"] == TODAY
        assert trend["volume"] >= EMIT_MIN


def test_detect_trends_flags_emerging_topic(tmp_path):
    db = tmp_path / "emerging.db"
    init_db(db, drop=True)
    _reference_data(db)
    _exec(db, _SOCIAL_SQL, [
        _social(CURRENT_END, 1, 1, "performance", 100, 0.3, evidence=["base"]),
        _social(PREVIOUS_END, 1, 1, "performance", 100, 0.3, evidence=["base"]),
        # tópico que sólo aparece en el período actual
        _social(CURRENT_END, 1, 1, "difficult_to_find", 40, -0.4,
                evidence=["No se consigue en ningún lado"]),
    ])
    ctx = build_context(db)
    emerging = [t for t in detect_trends(ctx) if t["trend_type"] == "emerging_topic"]
    assert [t["entity_id"] for t in emerging] == ["difficult_to_find"]
    assert emerging[0]["volume_previous"] == 0

    types = {i["insight_type"] for i in generate_insights(ctx)}
    assert "emerging_topic" in types


# ── puente Brand ↔ Product Intelligence ─────────────────────


def test_social_competition_signals(rich_db):
    pairs = social_competition_signals(build_context(rich_db))
    assert pairs
    by_key = {(p["product_id"], p["co_product_id"]): p for p in pairs}
    assert set(by_key) == {(1, 2), (1, 3)}

    top = by_key[(1, 2)]
    # `intensity` sigue siendo el share contra el par MÁS co-mencionado (máximo):
    # es un peso 0..1 que consume el matching engine, no una lectura de momentum.
    assert top["intensity"] == 1.0                      # par más co-mencionado
    # La ventana anterior tiene 20 co-menciones, por debajo de `min_base_volume`:
    # (60-20)/20 = +200% sería ruido amplificado, así que la variación no se
    # publica. La dirección sí, que es un hecho observado y no un cociente.
    assert 20 < MIN_BASE
    assert top["trend"] is None
    assert top["direction"] == "up"
    assert top["brand_name"] == "Nike" and top["co_brand_name"] == "Adidas"
    assert top["evidence"]["examples"]

    other = by_key[(1, 3)]
    assert 0.0 <= other["intensity"] <= 1.0
    assert other["intensity"] < top["intensity"]
    # Es sólo un dato de entrada para el matching engine: no toca competitive_matches.
    assert query("SELECT * FROM competitive_matches", path=rich_db) == []


# ── persistencia ────────────────────────────────────────────


def test_run_persists_signals_and_insights(rich_db):
    counts = run_brand_intelligence(rich_db)
    assert counts["brand_insights"] > 0
    assert counts["market_signals"] > 0
    assert counts["social_competition_pairs"] == 2

    signals = query("SELECT * FROM market_signals", path=rich_db)
    assert {s["signal_type"] for s in signals} == {"social_momentum", "editorial_momentum",
                                                   "review_momentum"}
    assert {s["entity_type"] for s in signals} == {"brand", "product", "franchise"}
    for signal in signals:
        assert signal["country_code"] == "AR"
        assert signal["period_end"] == TODAY
        assert 0.0 <= signal["value"] <= 100.0

    rows = query("SELECT * FROM brand_insights", path=rich_db)
    assert len(rows) == counts["brand_insights"]
    for row in rows:
        assert row["country_code"] == "AR"
        assert row["signal_volume"] >= EMIT_MIN
        assert row["confidence"] in ("LOW", "MEDIUM", "HIGH")
        evidence = json.loads(row["evidence"])
        assert evidence["examples"] and evidence["sources"]
        assert row["period_start"] and row["period_end"]


def test_run_is_idempotent(rich_db):
    first = run_brand_intelligence(rich_db)
    second = run_brand_intelligence(rich_db)
    assert first == second
    assert len(query("SELECT * FROM brand_insights", path=rich_db)) == first["brand_insights"]
    assert len(query("SELECT * FROM market_signals", path=rich_db)) == first["market_signals"]


def test_other_market_signal_types_are_preserved(rich_db):
    """El módulo sólo borra sus propias señales (share_of_shelf es de otro servicio)."""
    _exec(rich_db, "INSERT INTO market_signals (signal_type, entity_type, entity_id, "
                   "country_code, value) VALUES (?,?,?,?,?)",
          [("share_of_shelf", "brand", "1", "AR", 42.0)])
    run_brand_intelligence(rich_db)
    kept = query("SELECT * FROM market_signals WHERE signal_type = 'share_of_shelf'",
                 path=rich_db)
    assert len(kept) == 1
