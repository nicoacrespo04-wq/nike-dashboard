"""Tests del Competitive Product Matching Engine.

La DB se arma A MANO en cada test (no depende de app/seed.py) y el motor debe
funcionar aunque app/services/embeddings.py todavía no exista.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.config import get_config, reload_config, section, weights
from app.db import get_conn, init_db
from app.services import matching
from app.services.matching import (
    build_context,
    compute_match,
    run_matching,
)

TODAY = date.today()


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# ── helpers de armado de DB ─────────────────────────────────

PRODUCT_COLUMNS = (
    "id", "brand_id", "country_code", "product_name", "franchise", "category",
    "subcategory", "sport", "use_case", "gender", "performance_vs_lifestyle",
    "msrp", "price_band", "description", "launch_date",
)


def _insert(conn, table: str, rows: list[dict]) -> None:
    for row in rows:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


@pytest.fixture()
def db(tmp_path):
    """DB temporal con esquema + referencia (marcas, país, retailers)."""
    path = tmp_path / "matching.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        _insert(conn, "countries", [{"code": "AR", "name": "Argentina", "currency": "ARS"}])
        _insert(conn, "brands", [
            {"id": 1, "name": "Nike", "is_focus": 1},
            {"id": 2, "name": "Adidas", "is_focus": 0},
            {"id": 3, "name": "Asics", "is_focus": 0},
        ])
        _insert(conn, "retailers", [
            {"id": 1, "name": "Dexter", "country_code": "AR", "channel": "B2B", "importance": 0.8},
            {"id": 2, "name": "Solo Deportes", "country_code": "AR", "channel": "B2B", "importance": 0.6},
            {"id": 3, "name": "Mercado Libre", "country_code": "AR", "channel": "MARKETPLACE", "importance": 0.7},
        ])
    return path


def product(pid: int, brand_id: int, name: str, **over) -> dict:
    base = {
        "id": pid,
        "brand_id": brand_id,
        "country_code": "AR",
        "product_name": name,
        "franchise": None,
        "category": "footwear",
        "subcategory": "running shoes",
        "sport": "running",
        "use_case": "daily running",
        "gender": "men",
        "performance_vs_lifestyle": "performance",
        "msrp": 150000.0,
        "price_band": "mid",
        "description": "Zapatilla de running para entrenamiento diario con amortiguacion reactiva.",
        "launch_date": days_ago(200),
    }
    base.update(over)
    return {k: base[k] for k in PRODUCT_COLUMNS}


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch):
    """Fuerza el escenario 'embeddings no instalado' para que los tests sean
    deterministas exista o no el módulo hermano."""
    monkeypatch.setattr(matching, "_embeddings_module", None, raising=False)
    yield
    matching.reset_embeddings_cache()


@contextmanager
def config_override(**overrides):
    """Sobrescribe temporalmente claves de ``competitive_match`` en memoria.

    No toca weights.yaml: muta el dict cacheado y lo restaura al salir.
    """
    cfg = get_config()["competitive_match"]
    cfg.update(overrides)
    try:
        yield
    finally:
        reload_config()


# ── 1. productos idénticos -> score alto ────────────────────


def test_identical_products_score_high(db):
    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41", franchise="Pegasus"),
            product(2, 2, "Adidas Adizero SL", franchise="Adizero"),
        ])
        _insert(conn, "product_attributes", [
            {"product_id": p, "attr_group": "visual", "attr_name": n, "value_text": v}
            for p in (1, 2)
            for n, v in (
                ("silhouette", "low-top running"),
                ("colors", "negro blanco"),
                ("materials", "mesh engineered"),
            )
        ])
        _insert(conn, "price_observations", [
            {"product_id": p, "retailer_id": r, "observed_at": days_ago(3),
             "full_price": 150000.0, "current_price": 150000.0, "discount_pct": 0.0}
            for p in (1, 2) for r in (1, 2)
        ])
        _insert(conn, "editorial_mentions", [
            {"source_name": "Runner's World AR", "title": "Pegasus 41 vs Adizero SL",
             "published_at": days_ago(30), "mention_type": "versus",
             "product_a_id": 1, "product_b_id": 2, "country_code": "AR"},
            {"source_name": "Blog Running", "title": "Mejores daily trainers 2026",
             "published_at": days_ago(60), "mention_type": "same_list",
             "product_a_id": 1, "product_b_id": 2, "list_key": "daily-trainers-2026",
             "country_code": "AR"},
        ])
        _insert(conn, "social_mention_aggregates", [
            {"period_start": days_ago(30), "period_end": days_ago(1), "country_code": "AR",
             "product_id": 1, "co_product_id": 2, "source_type": "forum",
             "mention_count": 120, "comention_count": 40, "sentiment_score": 0.2},
        ])
        _insert(conn, "reviews", [
            {"product_id": p, "retailer_id": 1, "source": "retailer", "rating": 4.6,
             "review_count": 1, "observed_at": days_ago(20),
             "review_text": t}
            for p in (1, 2)
            for t in (
                "Muy comoda, la amortiguacion es excelente.",
                "El calce es perfecto y son livianas.",
                "Buena durabilidad y agarre en asfalto mojado.",
                "Relacion precio calidad correcta.",
            )
        ])

    ctx = build_context(db)
    result = compute_match(ctx.products[1], ctx.products[2], ctx)

    assert result.score >= 75.0
    assert result.confidence == "HIGH"
    assert result.coverage == pytest.approx(1.0)
    assert all(f["available"] for f in result.factors)
    assert sum(f["contribution"] for f in result.factors) == pytest.approx(100.0, abs=0.1)


# ── 2. categorías totalmente distintas -> score bajo ────────


def test_unrelated_products_score_low(db):
    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41", franchise="Pegasus"),
            product(2, 2, "Adidas Camiseta River Home", category="apparel",
                    subcategory="jerseys", sport="football", use_case="fan wear",
                    gender="women", performance_vs_lifestyle="lifestyle",
                    msrp=45000.0, price_band="entry",
                    description="Camiseta de futbol para hincha, tela liviana."),
        ])
        _insert(conn, "product_attributes", [
            {"product_id": 1, "attr_group": "visual", "attr_name": "silhouette", "value_text": "low-top running"},
            {"product_id": 1, "attr_group": "visual", "attr_name": "colors", "value_text": "negro blanco"},
            {"product_id": 1, "attr_group": "visual", "attr_name": "materials", "value_text": "mesh engineered"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "silhouette", "value_text": "jersey regular"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "colors", "value_text": "rojo"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "materials", "value_text": "poliester reciclado"},
        ])
        _insert(conn, "price_observations", [
            {"product_id": 1, "retailer_id": 1, "observed_at": days_ago(3),
             "full_price": 150000.0, "current_price": 150000.0},
            {"product_id": 2, "retailer_id": 3, "observed_at": days_ago(3),
             "full_price": 45000.0, "current_price": 45000.0},
        ])

    ctx = build_context(db)
    result = compute_match(ctx.products[1], ctx.products[2], ctx)

    assert result.score < 20.0
    by_name = {f["factor"]: f for f in result.factors}
    assert by_name["semantic"]["detail"]["hard_mismatch"]["fields"] == ["gender", "category"]
    assert by_name["retailer_overlap"]["raw_score"] == 0.0
    # sin datos editoriales/sociales/reviews el factor no penaliza: se excluye
    assert not by_name["editorial"]["available"]
    assert not by_name["social"]["available"]
    assert not by_name["reviews"]["available"]


# ── 3. par sin precio / editorial / social ──────────────────


def test_missing_signals_are_excluded_and_renormalized(db):
    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Vomero 18", franchise="Vomero", msrp=None, price_band=None),
            product(2, 3, "Asics Gel Nimbus 27", msrp=None, price_band=None),
        ])
        _insert(conn, "product_attributes", [
            {"product_id": p, "attr_group": "visual", "attr_name": n, "value_text": v}
            for p in (1, 2)
            for n, v in (("silhouette", "low-top running"), ("colors", "negro"), ("materials", "mesh"))
        ])
        # retailers vía stock (sin ninguna observación de precio)
        _insert(conn, "stock_observations", [
            {"product_id": p, "retailer_id": r, "observed_at": days_ago(2),
             "in_stock": 1, "availability_pct": 80.0}
            for p in (1, 2) for r in (1, 2)
        ])
        _insert(conn, "reviews", [
            {"product_id": p, "rating": 4.5, "review_count": 1,
             "review_text": "Comoda y con buena amortiguacion.", "observed_at": days_ago(10)}
            for p in (1, 2) for _ in range(3)
        ])

    ctx = build_context(db)
    result = compute_match(ctx.products[1], ctx.products[2], ctx)
    by_name = {f["factor"]: f for f in result.factors}

    for missing in ("price", "editorial", "social"):
        assert by_name[missing]["available"] is False
        assert by_name[missing]["raw_score"] is None
        assert by_name[missing]["contribution"] == 0.0
        assert "reason" in by_name[missing]["detail"]

    for present in ("visual", "semantic", "retailer_overlap", "reviews"):
        assert by_name[present]["available"] is True

    # el score se calcula igual, renormalizando sobre los factores disponibles
    assert result.score > 0.0
    assert sum(f["contribution"] for f in result.factors) == pytest.approx(100.0, abs=0.1)

    w = weights("competitive_match", "weights")
    expected_coverage = (w["visual"] + w["semantic"] + w["retailer_overlap"] + w["reviews"]) / sum(w.values())
    assert result.coverage == pytest.approx(expected_coverage)

    # sin precio no hay penalización: el score sigue siendo alto
    assert result.score >= 70.0


def test_reviews_below_min_are_unavailable(db):
    min_reviews = int(section("competitive_match", "reviews", "min_reviews_for_signal"))
    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41"),
            product(2, 3, "Asics Novablast 5"),
        ])
        _insert(conn, "reviews", [
            {"product_id": 1, "rating": 4.5, "review_text": "Comoda.", "observed_at": days_ago(5)}
            for _ in range(min_reviews - 1)
        ] + [
            {"product_id": 2, "rating": 4.4, "review_text": "Comoda.", "observed_at": days_ago(5)}
            for _ in range(min_reviews + 2)
        ])

    ctx = build_context(db)
    score, detail = matching._score_reviews(ctx.products[1], ctx.products[2], ctx)
    assert score is None
    assert "min_reviews_for_signal" in detail["reason"]


def test_social_respects_min_comentions(db):
    min_com = float(section("competitive_match", "social", "min_comentions"))
    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41"),
            product(2, 3, "Asics Novablast 5"),
        ])
        _insert(conn, "social_mention_aggregates", [
            {"period_start": days_ago(30), "period_end": days_ago(1), "country_code": "AR",
             "product_id": 1, "co_product_id": 2, "source_type": "forum",
             "mention_count": 10, "comention_count": int(min_com) - 1},
        ])

    ctx = build_context(db)
    score, detail = matching._score_social(ctx.products[1], ctx.products[2], ctx)
    assert score is None
    assert "min_comentions" in detail["reason"]
    assert detail["aggregate_only"] is True


# ── 4. persistencia: min_score_to_persist y top_n_per_product ─


def _seed_one_nike_many_competitors(db) -> None:
    with get_conn(db) as conn:
        _insert(conn, "countries", [{"code": "US", "name": "United States", "currency": "USD"}])
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41", franchise="Pegasus"),
            product(2, 2, "Adidas Adizero SL"),
            product(3, 3, "Asics Novablast 5"),
            product(4, 2, "Adidas Ultraboost 5"),
            product(5, 3, "Asics Gel Cumulus 27"),
            # otro país: nunca debe matchear
            product(6, 2, "Adidas Supernova US", country_code="US"),
        ])
        _insert(conn, "price_observations", [
            {"product_id": p, "retailer_id": 1, "observed_at": days_ago(3),
             "full_price": 150000.0, "current_price": 150000.0 - 1000 * p}
            for p in (1, 2, 3, 4, 5)
        ])
        _insert(conn, "editorial_mentions", [
            {"source_name": "Blog", "title": f"Pegasus vs {p}", "published_at": days_ago(20 * i + 10),
             "mention_type": "versus", "product_a_id": 1, "product_b_id": p, "country_code": "AR"}
            for i, p in enumerate((2, 3, 4, 5), start=1)
        ])


def test_run_matching_respects_top_n(db):
    _seed_one_nike_many_competitors(db)
    with config_override(min_score_to_persist=0.0, top_n_per_product=2):
        stats = run_matching(db)

    assert stats["nike_products"] == 1
    assert stats["pairs_evaluated"] == 4  # el producto US queda fuera
    assert stats["matches"] == 2
    assert stats["factors"] == 2 * len(matching.FACTOR_FUNCS)

    with get_conn(db) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM competitive_matches ORDER BY match_score DESC").fetchall()]
        factors = [dict(r) for r in conn.execute(
            "SELECT * FROM competitive_match_factors").fetchall()]

    assert len(rows) == 2
    assert all(r["nike_product_id"] == 1 for r in rows)
    assert rows[0]["match_score"] >= rows[1]["match_score"]
    assert all(r["confidence"] in {"LOW", "MEDIUM", "HIGH"} for r in rows)
    assert all(0.0 <= r["coverage"] <= 1.0 for r in rows)
    # un row por factor y por match, con detail JSON
    assert {f["match_id"] for f in factors} == {r["id"] for r in rows}
    assert all(f["detail"].startswith("{") for f in factors)


def test_run_matching_respects_min_score(db):
    _seed_one_nike_many_competitors(db)
    with config_override(min_score_to_persist=99.9, top_n_per_product=10):
        stats = run_matching(db)
    assert stats["matches"] == 0
    assert stats["factors"] == 0

    with config_override(min_score_to_persist=0.0, top_n_per_product=10):
        stats = run_matching(db)
    assert stats["matches"] == 4


def test_run_matching_is_idempotent(db):
    _seed_one_nike_many_competitors(db)
    with config_override(min_score_to_persist=0.0, top_n_per_product=3):
        first = run_matching(db)
        second = run_matching(db)
    assert first == second
    with get_conn(db) as conn:
        n_matches = conn.execute("SELECT COUNT(*) FROM competitive_matches").fetchone()[0]
        n_factors = conn.execute("SELECT COUNT(*) FROM competitive_match_factors").fetchone()[0]
    assert n_matches == 3
    assert n_factors == 3 * len(matching.FACTOR_FUNCS)


# ── 5. degradación elegante / uso de embeddings ─────────────


def test_works_without_embeddings_module(db):
    # El fallback por atributos necesita evidencia suficiente (ver
    # competitive_match.visual.min_evidence_weight): la silueta sola no alcanza,
    # así que el fixture aporta también color y material.
    with get_conn(db) as conn:
        _insert(conn, "products", [product(1, 1, "Nike Pegasus 41"), product(2, 3, "Asics Novablast 5")])
        _insert(conn, "product_attributes", [
            {"product_id": p, "attr_group": "visual", "attr_name": name, "value_text": value}
            for p in (1, 2)
            for name, value in (("silhouette", "low-top running"),
                                ("dominant_color", "negro"),
                                ("upper_material", "engineered mesh"))
        ])
    ctx = build_context(db)
    result = compute_match(ctx.products[1], ctx.products[2], ctx)
    by_name = {f["factor"]: f for f in result.factors}
    assert by_name["visual"]["detail"]["embedding_method"] == "unavailable"
    assert by_name["semantic"]["detail"]["text_similarity_backend"] == "unavailable"
    assert by_name["visual"]["available"] is True     # fallback por atributos
    assert by_name["semantic"]["available"] is True


def test_visual_needs_minimum_evidence(db):
    """Una sola sub-señal visual no basta para afirmar (di)similitud.

    Sin este piso, dos vocabularios de enrichment que dicen "trainer" y
    "runner" sobre la misma silueta producen un visual=0.0 rotundo que hunde
    el match de dos productos que sí compiten.
    """
    with get_conn(db) as conn:
        _insert(conn, "products", [product(1, 1, "Nike Pegasus 41"), product(2, 3, "Asics Novablast 5")])
        _insert(conn, "product_attributes", [
            {"product_id": 1, "attr_group": "visual", "attr_name": "silhouette", "value_text": "trainer"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "silhouette", "value_text": "runner"},
        ])
    ctx = build_context(db)
    by_name = {f["factor"]: f for f in compute_match(ctx.products[1], ctx.products[2], ctx).factors}
    assert by_name["visual"]["available"] is False
    assert "evidencia visual insuficiente" in by_name["visual"]["detail"]["reason"]


def test_evidence_shrinkage_ranks_documented_rivalry_first():
    """Con menos cobertura, el score ajustado cae hacia el prior.

    Es lo que evita que un par sin evidencia editorial ni social le gane a uno
    con rivalidad documentada sólo por evaluarse con los factores más fáciles.
    """
    from app.services.matching import evidence_adjusted

    poor = evidence_adjusted(77.0, 0.60)    # score alto, poca evidencia
    rich = evidence_adjusted(74.0, 0.85)    # score algo menor, más evidencia
    assert rich > poor
    # Con cobertura total el ajuste es neutro.
    assert evidence_adjusted(80.0, 1.0) == pytest.approx(80.0)


def test_uses_embeddings_when_available(db, monkeypatch):
    stub = SimpleNamespace(
        backend_name=lambda: "tfidf",
        text_similarity=lambda a, b: 0.9,
        image_similarity=lambda a, b: (0.8, "clip-vit-b32"),
    )
    monkeypatch.setattr(matching, "_embeddings_module", stub, raising=False)
    with get_conn(db) as conn:
        _insert(conn, "products", [product(1, 1, "Nike Pegasus 41"), product(2, 3, "Asics Novablast 5")])
    ctx = build_context(db)
    by_name = {f["factor"]: f for f in compute_match(ctx.products[1], ctx.products[2], ctx).factors}
    assert by_name["visual"]["detail"]["embedding_method"] == "clip-vit-b32"
    assert by_name["visual"]["raw_score"] == pytest.approx(0.8)
    assert by_name["semantic"]["detail"]["text_similarity_backend"] == "tfidf"


# ── 6. feature importance del ejemplo del brief ─────────────


@pytest.mark.parametrize("use_embeddings", [False, True], ids=["sin-embeddings", "con-embeddings"])
def test_feature_importance_breakdown(db, monkeypatch, use_embeddings):
    """Pegasus 41 vs Novablast 5: los 7 factores disponibles y contribución legible."""
    if use_embeddings:
        monkeypatch.setattr(matching, "_embeddings_module", SimpleNamespace(
            backend_name=lambda: "tfidf",
            text_similarity=lambda a, b: 0.55,
            image_similarity=lambda a, b: (0.62, "clip-vit-b32"),
        ), raising=False)

    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41", franchise="Pegasus", msrp=159999.0, price_band="mid"),
            product(2, 3, "Asics Novablast 5", franchise="Novablast", msrp=169999.0,
                    price_band="premium", subcategory="neutral running shoes",
                    description="Zapatilla neutra de entrenamiento diario con espuma FF Blast."),
        ])
        _insert(conn, "product_attributes", [
            {"product_id": 1, "attr_group": "visual", "attr_name": "silhouette", "value_text": "low-top running"},
            {"product_id": 1, "attr_group": "visual", "attr_name": "colors", "value_text": "negro blanco"},
            {"product_id": 1, "attr_group": "visual", "attr_name": "materials", "value_text": "mesh engineered"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "silhouette", "value_text": "low-top running"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "colors", "value_text": "negro blanco"},
            {"product_id": 2, "attr_group": "visual", "attr_name": "materials", "value_text": "mesh knit"},
        ])
        _insert(conn, "price_observations", [
            {"product_id": 1, "retailer_id": 1, "observed_at": days_ago(2), "current_price": 149999.0},
            {"product_id": 1, "retailer_id": 2, "observed_at": days_ago(2), "current_price": 155999.0},
            {"product_id": 2, "retailer_id": 1, "observed_at": days_ago(2), "current_price": 165999.0},
            {"product_id": 2, "retailer_id": 2, "observed_at": days_ago(2), "current_price": 169999.0},
            {"product_id": 2, "retailer_id": 3, "observed_at": days_ago(2), "current_price": 169999.0},
        ])
        _insert(conn, "editorial_mentions", [
            {"source_name": f"medio-{i}", "title": "Pegasus 41 vs Novablast 5",
             "published_at": days_ago(12 * i), "mention_type": mt,
             "product_a_id": 1, "product_b_id": 2, "country_code": "AR"}
            for i, mt in enumerate(("versus", "alternative", "versus", "ranking",
                                    "alternative", "versus", "review", "versus",
                                    "alternative", "versus", "ranking", "versus"), start=1)
        ] + [
            {"source_name": "guia", "title": "Mejores daily trainers 2026",
             "published_at": days_ago(45), "mention_type": "same_list",
             "product_a_id": 1, "product_b_id": 2, "list_key": "daily-trainers-2026",
             "country_code": "AR"},
        ])
        _insert(conn, "social_mention_aggregates", [
            {"period_start": days_ago(60), "period_end": days_ago(31), "country_code": "AR",
             "product_id": 1, "co_product_id": 2, "source_type": "forum",
             "mention_count": 300, "comention_count": 22},
            {"period_start": days_ago(30), "period_end": days_ago(1), "country_code": "AR",
             "product_id": 2, "co_product_id": 1, "source_type": "social",
             "mention_count": 420, "comention_count": 31},
        ])
        _insert(conn, "reviews", [
            {"product_id": 1, "rating": 4.6, "review_count": 1, "observed_at": days_ago(15),
             "review_text": t}
            for t in ("Muy comoda para el uso diario.",
                      "La amortiguacion es excelente y el calce justo.",
                      "Livianas, buen agarre en asfalto.",
                      "El precio esta caro pero la calidad acompana.")
        ] + [
            {"product_id": 2, "rating": 4.4, "review_count": 1, "observed_at": days_ago(15),
             "review_text": t}
            for t in ("Comoda y con mucha amortiguacion.",
                      "El calce es angosto, pedir un numero mas.",
                      "Son livianas pero el agarre en mojado es regular.",
                      "Buena durabilidad para el precio.")
        ])

    ctx = build_context(db)
    result = compute_match(ctx.products[1], ctx.products[2], ctx)

    assert all(f["available"] for f in result.factors)
    assert result.confidence == "HIGH"
    assert sum(f["contribution"] for f in result.factors) == pytest.approx(100.0, abs=0.1)
    top = result.drivers[0]
    assert top["factor"] == "semantic"

    print(f"\nPegasus 41 vs Novablast 5 -> score={result.score:.1f} "
          f"coverage={result.coverage:.2f} confidence={result.confidence}")
    for f in result.drivers:
        print(f"  {f['factor']:<18} raw={f['raw_score']:.3f} weight={f['weight']:.2f} "
              f"contribution={f['contribution']:.1f}%")


# ── 7. rendimiento: el atajo textual y los índices no mueven un score ──
#
# Estos tests son la red que protege las optimizaciones de rendimiento (ver
# backend/docs/matching.md § rendimiento). La regla es simple: acelerar no puede
# cambiar ni un decimal. Por eso se compara SIEMPRE contra el camino lento, no
# contra una expectativa escrita a mano.


def _golden_db(db) -> None:
    """Catálogo chico pero con las siete señales prendidas."""
    with get_conn(db) as conn:
        _insert(conn, "products", [
            product(1, 1, "Nike Pegasus 41", franchise="Pegasus", msrp=159999.0,
                    description="Zapatilla de running para entrenamiento diario "
                                "con amortiguacion reactiva."),
            product(2, 2, "Adidas Adizero SL", franchise="Adizero", msrp=149999.0,
                    description="Zapatilla liviana de entrenamiento rapido con "
                                "espuma Lightstrike."),
            product(3, 3, "Asics Novablast 5", franchise="Novablast", msrp=169999.0,
                    price_band="premium",
                    description="Zapatilla neutra de entrenamiento diario con espuma FF Blast."),
            product(4, 2, "Adidas Terrex Agravic", category="trail", sport="trail",
                    use_case="trail running", msrp=189999.0,
                    description="Zapatilla de trail con placa de roca y agarre Continental."),
        ])
        _insert(conn, "price_observations", [
            {"product_id": p, "retailer_id": r, "observed_at": days_ago(3),
             "current_price": 150000.0 - 1000 * p - 500 * r}
            for p in (1, 2, 3, 4) for r in (1, 2)
        ])
        _insert(conn, "reviews", [
            {"product_id": p, "rating": 4.0 + p / 10, "review_count": 3,
             "observed_at": days_ago(10),
             "review_text": "Comoda y liviana, buena amortiguacion y calce justo."}
            for p in (1, 2, 3, 4)
        ])
        _insert(conn, "editorial_mentions", [
            {"source_name": "Blog", "title": "Pegasus vs Adizero",
             "published_at": days_ago(30), "mention_type": "versus",
             "product_a_id": 1, "product_b_id": 2, "country_code": "AR"},
        ])
        _insert(conn, "social_mention_aggregates", [
            {"period_start": days_ago(60), "period_end": days_ago(2), "country_code": "AR",
             "product_id": 1, "co_product_id": 3, "source_type": "forum",
             "mention_count": 100, "comention_count": 12},
        ])


# score crudo, score ajustado, cobertura y confianza de Pegasus 41 contra cada
# competidor, con el fixture `no_embeddings` activo (fallback determinístico:
# no dependen de la versión de sklearn). Son los valores que producía el motor
# ANTES de las optimizaciones de rendimiento — la comparación par a par sobre el
# catálogo entero está en tools/profile_matching.py --compare.
GOLDEN_SCORES = {
    2: (82.7721, 70.8291, 0.75, "HIGH"),
    3: (84.1142, 69.3799, 0.70, "HIGH"),
    4: (61.8964, 51.1378, 0.60, "MEDIUM"),
}


def test_scores_no_cambian_con_las_optimizaciones(db):
    """Regresión numérica: los scores son los de siempre, al cuarto decimal."""
    _golden_db(db)
    ctx = build_context(db)
    for comp_id, (raw, adjusted, coverage, confidence) in GOLDEN_SCORES.items():
        result = compute_match(ctx.products[1], ctx.products[comp_id], ctx)
        assert round(result.score, 4) == raw
        assert round(matching.evidence_adjusted(result.score, result.coverage), 4) == adjusted
        assert round(result.coverage, 4) == coverage
        assert result.confidence == confidence


def test_el_contexto_memoiza_por_producto_sin_cambiar_resultados(db):
    """Los memos por producto devuelven lo mismo que el cálculo directo."""
    _golden_db(db)
    ctx = build_context(db)

    # precio promedio: mismo número que el cálculo ingenuo sobre latest_price
    for pid in ctx.products:
        prices = [float(obs["current_price"])
                  for (p, _r), obs in ctx.latest_price.items()
                  if p == pid and obs.get("current_price") is not None]
        expected = sum(prices) / len(prices) if prices else None
        assert ctx.avg_current_price(pid) == expected

    # señales de reviews: mismo volumen/atributos/rating que las funciones puras
    for pid in ctx.products:
        rows = ctx.reviews.get(pid, [])
        volume, attrs, rating = ctx.review_signals(pid)
        assert volume == matching._review_volume(rows)
        assert set(attrs) == matching._review_attributes(rows)
        assert rating == matching._avg_rating(rows)

    # y el memo no se "gasta": pedirlo dos veces da lo mismo
    assert ctx.review_signals(1) == ctx.review_signals(1)
    assert ctx.attr_tokens(1, matching._COLOR_ATTRS) == ctx.attr_tokens(1, matching._COLOR_ATTRS)


def test_el_atajo_textual_reproduce_a_sklearn():
    """El TF-IDF analítico da lo mismo que ajustar el vectorizador por par.

    Si este test falla, `embeddings` cambió de fórmula y el atajo tiene que
    apagarse solo (lo hace `verify_text_fast_path`).
    """
    embeddings = pytest.importorskip("app.services.embeddings")
    if embeddings.backend_name() != "tfidf":
        pytest.skip("el backend activo no es TF-IDF")

    matching.reset_embeddings_cache()
    engine = matching._text_similarity_engine(embeddings)
    assert engine is not None and engine.available

    textos = [
        "Zapatilla de running para entrenamiento diario con amortiguacion reactiva.",
        "Zapatilla liviana de entrenamiento rapido con espuma Lightstrike.",
        "Zapatilla neutra de entrenamiento diario con espuma FF Blast.",
        "Botines de futbol para cancha de pasto natural con tapones FG.",
        "Remera de entrenamiento con tecnologia de secado rapido.",
        "",
        "zapatilla",
    ]
    comparados = 0
    for i, a in enumerate(textos):
        for b in textos[i + 1:]:
            lento = embeddings.text_similarity(a, b)
            rapido = matching.text_similarity(a, b, embeddings)
            assert (lento is None) == (rapido is None)
            if lento is not None:
                # La diferencia admisible es el redondeo float32 de sklearn:
                # tres órdenes por debajo de los 4 decimales que se persisten.
                assert abs(lento - rapido) <= matching._FAST_TEXT_TOLERANCE
                comparados += 1
    assert comparados >= 10
    matching.reset_embeddings_cache()


def test_el_score_completo_es_igual_con_y_sin_atajo_textual(db, monkeypatch):
    """El par completo (los 7 factores) puntúa igual por los dos caminos."""
    embeddings = pytest.importorskip("app.services.embeddings")
    if embeddings.backend_name() != "tfidf":
        pytest.skip("el backend activo no es TF-IDF")
    _golden_db(db)
    monkeypatch.setattr(matching, "_embeddings_module", embeddings, raising=False)

    matching.reset_embeddings_cache()
    monkeypatch.setattr(matching, "_embeddings_module", embeddings, raising=False)
    ctx = build_context(db)
    rapido = {cid: compute_match(ctx.products[1], ctx.products[cid], ctx).score
              for cid in (2, 3, 4)}

    # Camino lento: el mismo que corría antes de la optimización.
    monkeypatch.setattr(matching, "text_similarity",
                        lambda a, b, module: module.text_similarity(a, b))
    embeddings.reset_cache()
    ctx = build_context(db)
    lento = {cid: compute_match(ctx.products[1], ctx.products[cid], ctx).score
             for cid in (2, 3, 4)}

    for cid in rapido:
        assert round(rapido[cid], 4) == round(lento[cid], 4)
    matching.reset_embeddings_cache()


def test_el_atajo_textual_se_apaga_si_embeddings_cambia(monkeypatch):
    """Un módulo de embeddings que no expone sus internos usa el camino lento."""
    llamadas = []

    def slow(a, b):
        llamadas.append((a, b))
        return 0.42

    stub = SimpleNamespace(backend_name=lambda: "tfidf", text_similarity=slow)
    matching.reset_embeddings_cache()
    assert matching.text_similarity("una cosa", "otra cosa", stub) == 0.42
    assert llamadas == [("una cosa", "otra cosa")]
    assert matching._text_fast_path.available is False
    assert "no expone" in matching._text_fast_path.reason
    matching.reset_embeddings_cache()


# ── 8. candidate generation (prefiltro) ─────────────────────


def test_el_prefiltro_no_pierde_matches_en_un_catalogo_chico(db):
    """Red de seguridad: con pocos competidores no se descarta nada.

    Un catálogo chico llena su cupo de top-N con pares de otra categoría; si el
    prefiltro los sacara, el resultado cambiaría. Por eso, cuando el bloque
    queda por debajo de `min_candidates_per_product`, ese producto Nike vuelve
    al barrido completo.
    """
    _golden_db(db)
    with config_override(min_score_to_persist=0.0, top_n_per_product=10):
        con_filtro = run_matching(db)
        with get_conn(db) as conn:
            filtrados = {(r["nike_product_id"], r["competitor_product_id"], r["match_score"])
                         for r in conn.execute("SELECT * FROM competitive_matches")}

        cfg = get_config()["competitive_match"]
        cfg["candidate_filter"] = {"enabled": False}
        completo = run_matching(db)
        with get_conn(db) as conn:
            todos = {(r["nike_product_id"], r["competitor_product_id"], r["match_score"])
                     for r in conn.execute("SELECT * FROM competitive_matches")}

    assert con_filtro["pairs_skipped_by_filter"] == 0
    assert con_filtro["products_without_filter"] == con_filtro["nike_products"]
    assert filtrados == todos
    assert con_filtro["pairs_evaluated"] == completo["pairs_evaluated"]


def test_el_prefiltro_descarta_categorias_distintas_cuando_el_bloque_alcanza(db):
    """Con bloque suficiente, un par de otra categoría no se puntúa."""
    _golden_db(db)
    ctx = build_context(db)
    nike, trail = ctx.products[1], ctx.products[4]

    with config_override(candidate_filter={"enabled": True, "same_category": True,
                                           "min_candidates_per_product": 1}):
        ctx = build_context(db)
        assert matching.is_candidate(nike, ctx.products[2], ctx) is True     # misma categoría
        assert matching.is_candidate(nike, trail, ctx) is False              # running vs trail
        bloque, descartados = matching.candidates_for(nike, ctx)
        assert descartados == 1
        assert trail["id"] not in {p["id"] for p in bloque}

    with config_override(candidate_filter={"enabled": False}):
        ctx = build_context(db)
        bloque, descartados = matching.candidates_for(nike, ctx)
        assert descartados == 0
        assert trail["id"] in {p["id"] for p in bloque}


def test_el_prefiltro_nunca_descarta_una_rivalidad_documentada(db):
    """Si el mercado ya los compara, la taxonomía no manda.

    Es el caso que el brief pide proteger: dos productos pueden verse distintos
    (otra categoría) y competir igual. Una mención editorial del par, una
    co-mención social o una lista compartida lo rescatan del prefiltro.
    """
    _golden_db(db)
    with get_conn(db) as conn:
        _insert(conn, "editorial_mentions", [
            {"source_name": "Guia", "title": "Pegasus vs Terrex para correr en cerro",
             "published_at": days_ago(20), "mention_type": "alternative",
             "product_a_id": 1, "product_b_id": 4, "country_code": "AR"},
        ])

    with config_override(candidate_filter={"enabled": True, "same_category": True,
                                           "keep_documented_pairs": True,
                                           "min_candidates_per_product": 1}):
        ctx = build_context(db)
        assert matching.is_candidate(ctx.products[1], ctx.products[4], ctx) is True

    with config_override(candidate_filter={"enabled": True, "same_category": True,
                                           "keep_documented_pairs": False,
                                           "min_candidates_per_product": 1}):
        ctx = build_context(db)
        assert matching.is_candidate(ctx.products[1], ctx.products[4], ctx) is False
