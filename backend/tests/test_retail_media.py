"""Tests del Retail Media Opportunity Engine.

Cada uno de los 5 casos del brief se arma con filas insertadas a mano
(sin `app/seed.py` ni `app/services/matching.py`).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import section
from app.db import get_conn, init_db, query
from app.services import retail_media
from app.services.common import from_json
from app.services.opportunities import build_context

TH = {k: float(v) for k, v in section("retail_media", "thresholds").items()}
OBS = date.today().isoformat()

NIKE_ID, COMP_ID, RETAILER_ID = 1, 10, 1


# ── fixture parametrizable ──────────────────────────────────


def _scenario(tmp_path, name, *, nike_price, comp_price, nike_stock, comp_stock,
              match_score=85.0, comp_mentions=(60, 340), extra_competitors=1,
              extra_nike=1, nike_discount=0.0):
    """DB mínima con un producto Nike, un competidor y un retailer."""
    path = tmp_path / f"{name}.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.executemany("INSERT INTO brands (id, name, is_focus) VALUES (?,?,?)",
                         [(1, "Nike", 1), (2, "Adidas", 0)])
        conn.execute(
            "INSERT INTO retailers (id, name, country_code, channel, importance) "
            "VALUES (1,'Dexter','AR','B2B',0.9)"
        )
        products = [
            (NIKE_ID, 1, "Nike Pegasus 41", "Pegasus", "daily running", "mature", nike_price),
            (COMP_ID, 2, "Adidas Ultraboost 5", None, "daily running", "mature", comp_price),
        ]
        # SKUs extra del mismo segmento: definen el share of shelf de Nike.
        products += [(100 + i, 1, f"Nike Extra {i}", "Pegasus", "daily running", "mature", nike_price)
                     for i in range(extra_nike)]
        products += [(200 + i, 2, f"Adidas Extra {i}", None, "daily running", "mature", comp_price)
                     for i in range(extra_competitors)]
        conn.executemany(
            "INSERT INTO products (id, brand_id, product_name, franchise, use_case, "
            "lifecycle_stage, msrp, category, country_code) "
            "VALUES (?,?,?,?,?,?,?,'running','AR')",
            products,
        )
        conn.executemany(
            "INSERT INTO price_observations (product_id, retailer_id, observed_at, full_price, "
            "current_price, discount_pct, currency) VALUES (?,?,?,?,?,?,'ARS')",
            [
                (NIKE_ID, RETAILER_ID, OBS, nike_price, nike_price, nike_discount),
                (COMP_ID, RETAILER_ID, OBS, comp_price, comp_price, 0.0),
            ],
        )
        conn.executemany(
            "INSERT INTO stock_observations (product_id, retailer_id, observed_at, in_stock, "
            "availability_pct, sizes_available, sizes_total) VALUES (?,?,?,?,?,?,?)",
            [
                (NIKE_ID, RETAILER_ID, OBS, 1, nike_stock, int(nike_stock / 10), 10),
                (COMP_ID, RETAILER_ID, OBS, 1 if comp_stock > 0 else 0, comp_stock,
                 int(comp_stock / 10), 10),
            ],
        )
        conn.execute(
            "INSERT INTO competitive_matches (nike_product_id, competitor_product_id, "
            "match_score, confidence, coverage) VALUES (?,?,?,'HIGH',0.9)",
            (NIKE_ID, COMP_ID, match_score),
        )
        conn.execute(
            "INSERT INTO reviews (product_id, retailer_id, source, rating, review_count, "
            "observed_at) VALUES (?,1,'retailer',4.6,150,?)", (NIKE_ID, OBS),
        )
        previous, current = comp_mentions
        conn.executemany(
            "INSERT INTO social_mention_aggregates (product_id, period_start, period_end, "
            "country_code, source_type, mention_count, comention_count, sentiment_score) "
            "VALUES (?,?,?,'AR','forum',?,10,0.4)",
            [
                (COMP_ID, "2026-06-01", "2026-06-30", previous),
                (COMP_ID, "2026-07-01", "2026-07-31", current),
            ],
        )
    return path


def _evaluate(path):
    ctx = build_context(path)
    return retail_media.score_retail_media(
        ctx.product(NIKE_ID), ctx.product(COMP_ID), ctx.retailers[RETAILER_ID], ctx
    )


# ── los 5 casos del brief ───────────────────────────────────


def test_caso_1_invest_in_retail_media(tmp_path):
    """Stock alto + precio competitivo + competidor con momentum."""
    path = _scenario(tmp_path, "caso1", nike_price=100000, comp_price=98000,
                     nike_stock=90.0, comp_stock=80.0, extra_nike=2, extra_competitors=1)
    score, recommendation = _evaluate(path)

    assert recommendation == retail_media.INVEST_IN_RETAIL_MEDIA
    assert 0.0 <= score.score <= 100.0
    signals = retail_media.build_signals(*_triple(path))
    assert signals["nike_stock_pct"] >= TH["nike_stock_high_pct"]
    assert signals["price_gap_pct"] <= TH["price_competitive_pct"]
    assert signals["competitor_momentum"] >= TH["high_momentum"]
    # razón explícita: no hace falta bajar precio
    _, rationale = retail_media.decide(signals)
    assert "ya es competitivo en precio" in rationale


def test_caso_2_evaluate_price_action_before_media(tmp_path):
    """Stock alto pero precio muy por encima del competidor."""
    path = _scenario(tmp_path, "caso2", nike_price=130000, comp_price=100000,
                     nike_stock=90.0, comp_stock=80.0)
    score, recommendation = _evaluate(path)
    signals = retail_media.build_signals(*_triple(path))

    assert recommendation == retail_media.EVALUATE_PRICE_ACTION_BEFORE_MEDIA
    assert signals["price_gap_pct"] >= TH["price_disadvantage_pct"]
    assert "antes de invertir en media" in retail_media.decide(signals)[1]


def test_caso_3_do_not_increase_media(tmp_path):
    """Stock bajo + demanda alta: no generar demanda sobre inventario insuficiente."""
    path = _scenario(tmp_path, "caso3", nike_price=100000, comp_price=99000,
                     nike_stock=20.0, comp_stock=80.0)
    _, recommendation = _evaluate(path)
    signals = retail_media.build_signals(*_triple(path))

    assert recommendation == retail_media.DO_NOT_INCREASE_MEDIA
    assert signals["nike_stock_pct"] <= TH["nike_stock_low_pct"]
    assert signals["demand_signal"] >= TH["high_momentum"]
    assert "inventario insuficiente" in retail_media.decide(signals)[1]


def test_caso_4_capture_competitor_stockout(tmp_path):
    """Competidor en quiebre + Nike con stock y precio competitivo."""
    path = _scenario(tmp_path, "caso4", nike_price=100000, comp_price=99000,
                     nike_stock=88.0, comp_stock=25.0)
    _, recommendation = _evaluate(path)
    signals = retail_media.build_signals(*_triple(path))

    assert recommendation == retail_media.CAPTURE_COMPETITOR_STOCKOUT
    assert signals["competitor_stock_pct"] <= TH["competitor_stockout_pct"]
    assert signals["nike_stock_pct"] > TH["nike_stock_low_pct"]
    assert signals["price_gap_pct"] <= TH["price_competitive_pct"]


def test_caso_5_prioritize_retail_media_over_markdown(tmp_path):
    """Stock alto, precio competitivo, producto relevante, competidor con
    momentum y share of shelf Nike bajo => media en lugar de markdown."""
    path = _scenario(tmp_path, "caso5", nike_price=100000, comp_price=99500,
                     nike_stock=92.0, comp_stock=85.0, extra_nike=0, extra_competitors=4)
    _, recommendation = _evaluate(path)
    signals = retail_media.build_signals(*_triple(path))

    assert recommendation == retail_media.PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN
    max_share = float(section("opportunities", "assortment_white_space", "max_nike_share"))
    assert signals["nike_shelf_share"] <= max_share
    assert signals["business_importance"] >= float(
        section("business_importance", "severity_thresholds", "medium")
    )
    rationale = retail_media.decide(signals)[1]
    assert "markdown adicional" in rationale and "retail media" in rationale


def _triple(path):
    ctx = build_context(path)
    return ctx.product(NIKE_ID), ctx.product(COMP_ID), ctx.retailers[RETAILER_ID], ctx


# ── score y explicabilidad ──────────────────────────────────


def test_usa_los_7_pesos_de_config(tmp_path):
    path = _scenario(tmp_path, "pesos", nike_price=100000, comp_price=98000,
                     nike_stock=90.0, comp_stock=80.0)
    score, _ = _evaluate(path)
    w = section("retail_media", "weights")

    assert len(w) == 7
    assert {f["factor"] for f in score.factors} == set(w)
    for row in score.factors:
        assert row["weight"] == pytest.approx(float(w[row["factor"]]), abs=1e-4)
        assert row["available"], f"{row['factor']} sin datos en el escenario completo"
        assert 0.0 <= row["raw_score"] <= 1.0
    assert sum(f["contribution"] for f in score.factors) == pytest.approx(100.0, abs=0.1)


def test_price_competitiveness_interpola_entre_umbrales():
    th = TH
    assert retail_media._price_competitiveness(0.0, th) == 1.0
    assert retail_media._price_competitiveness(-20.0, th) == 1.0
    assert retail_media._price_competitiveness(th["price_competitive_pct"], th) == 1.0
    assert retail_media._price_competitiveness(th["price_disadvantage_pct"], th) == 0.0
    medio = (th["price_competitive_pct"] + th["price_disadvantage_pct"]) / 2
    assert retail_media._price_competitiveness(medio, th) == pytest.approx(0.5)
    assert retail_media._price_competitiveness(None, th) is None


def test_persistencia_con_drivers_explicables(tmp_path):
    path = _scenario(tmp_path, "persistencia", nike_price=100000, comp_price=98000,
                     nike_stock=90.0, comp_stock=80.0)
    counts = retail_media.run_retail_media(path)
    rows = query("SELECT * FROM retail_media_opportunities", path=path)

    assert counts["retail_media_opportunities"] == len(rows) >= 1
    row = rows[0]
    assert row["score"] >= TH["min_score_to_report"]
    assert row["recommendation"] in {
        retail_media.INVEST_IN_RETAIL_MEDIA,
        retail_media.EVALUATE_PRICE_ACTION_BEFORE_MEDIA,
        retail_media.DO_NOT_INCREASE_MEDIA,
        retail_media.CAPTURE_COMPETITOR_STOCKOUT,
        retail_media.PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN,
    }
    assert row["confidence"] in {"LOW", "MEDIUM", "HIGH"}
    drivers = from_json(row["drivers"], [])[0]
    for key in ("nike_stock_pct", "competitive_relevance", "competitor_momentum",
                "price_gap_pct", "nike_shelf_share", "rationale"):
        assert key in drivers
    assert drivers["nike_stock_pct"] == 90.0
    assert drivers["competitive_relevance"] == 85.0
    assert row["country_code"] == "AR"


def test_respeta_min_score_to_report(tmp_path):
    """Un caso flojo (sin momentum, sin stock, precio caro) no llega al umbral."""
    path = _scenario(tmp_path, "flojo", nike_price=200000, comp_price=100000,
                     nike_stock=5.0, comp_stock=100.0, match_score=10.0,
                     comp_mentions=(400, 1), extra_nike=8, extra_competitors=0)
    ctx = build_context(path)
    score, _ = retail_media.score_retail_media(
        ctx.product(NIKE_ID), ctx.product(COMP_ID), ctx.retailers[RETAILER_ID], ctx)
    assert score.score < TH["min_score_to_report"]

    counts = retail_media.run_retail_media(path)
    assert counts["evaluated"] == 1
    assert counts["retail_media_opportunities"] == 0
    assert query("SELECT * FROM retail_media_opportunities", path=path) == []


def test_es_idempotente(tmp_path):
    path = _scenario(tmp_path, "idempotente", nike_price=100000, comp_price=98000,
                     nike_stock=90.0, comp_stock=80.0)
    primero = retail_media.run_retail_media(path)
    segundo = retail_media.run_retail_media(path)
    assert primero == segundo
    assert len(query("SELECT * FROM retail_media_opportunities", path=path)) == \
        primero["retail_media_opportunities"]


# ── degradación elegante ────────────────────────────────────


def test_db_vacia_no_rompe(tmp_path):
    path = tmp_path / "vacia.db"
    init_db(path, drop=True)
    counts = retail_media.run_retail_media(path)
    assert counts == {"evaluated": 0, "retail_media_opportunities": 0}


def test_sin_competitive_matches_no_produce_filas(tmp_path):
    path = _scenario(tmp_path, "sin_matches", nike_price=100000, comp_price=98000,
                     nike_stock=90.0, comp_stock=80.0)
    with get_conn(path) as conn:
        conn.execute("DELETE FROM competitive_matches")
    counts = retail_media.run_retail_media(path)
    assert counts["retail_media_opportunities"] == 0


def test_sin_observaciones_los_factores_se_marcan_no_disponibles(tmp_path):
    path = _scenario(tmp_path, "sin_obs", nike_price=100000, comp_price=98000,
                     nike_stock=90.0, comp_stock=80.0)
    with get_conn(path) as conn:
        conn.execute("DELETE FROM price_observations")
        conn.execute("DELETE FROM stock_observations")
        conn.execute("DELETE FROM social_mention_aggregates")

    ctx = build_context(path)
    score, recommendation = retail_media.score_retail_media(
        ctx.product(NIKE_ID), ctx.product(COMP_ID), ctx.retailers[RETAILER_ID], ctx)

    faltantes = {f["factor"] for f in score.factors if not f["available"]}
    assert {"nike_stock_health", "competitor_momentum", "competitor_stock_gap"} <= faltantes
    assert score.coverage < 1.0
    assert recommendation in {retail_media.INVEST_IN_RETAIL_MEDIA,
                              retail_media.EVALUATE_PRICE_ACTION_BEFORE_MEDIA,
                              retail_media.DO_NOT_INCREASE_MEDIA}
