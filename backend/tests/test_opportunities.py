"""Tests del motor de oportunidades.

Todas las filas se insertan A MANO (sin `app/seed.py` ni `app/services/matching.py`):
el fixture está armado a propósito para disparar las 12 reglas.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import section
from app.db import get_conn, init_db, query
from app.services import opportunities, scoring
from app.services.common import from_json

TODAY = date.today()
RECENT_LAUNCH = (TODAY - timedelta(days=30)).isoformat()
OLD_LAUNCH = (TODAY - timedelta(days=900)).isoformat()
OBS_DATE = TODAY.isoformat()

FAMILIES = section("opportunities", "families")


# ── fixture ─────────────────────────────────────────────────


def _base_rows(conn):
    conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
    conn.executemany(
        "INSERT INTO brands (id, name, is_focus) VALUES (?,?,?)",
        [(1, "Nike", 1), (2, "Adidas", 0), (3, "New Balance", 0), (4, "Puma", 0)],
    )
    conn.executemany(
        "INSERT INTO retailers (id, name, country_code, channel, importance) VALUES (?,?,?,?,?)",
        [
            (1, "Dexter", "AR", "B2B", 0.80),
            (2, "Solo Deportes", "AR", "B2B", 0.70),
            (3, "Mercado Libre", "AR", "MARKETPLACE", 0.90),
            (4, "Falabella", "AR", "B2B", 0.60),
        ],
    )


def _products(conn):
    rows = [
        # Nike
        (1, 1, "Nike Pegasus 41", "Pegasus", "running", "daily running", "mature", 200000, OLD_LAUNCH),
        (2, 1, "Nike Vomero 17", "Vomero", "running", "daily running", "mature", 150000, OLD_LAUNCH),
        (3, 1, "Nike Invincible 3", "Invincible", "running", "daily running", "growth", 180000, OLD_LAUNCH),
        (4, 1, "Nike Air Max 270", "Air Max", "lifestyle", "lifestyle", "mature", 170000, OLD_LAUNCH),
        (5, 1, "Nike Air Force 1", "Air Force 1", "lifestyle", "lifestyle", "mature", 120000, OLD_LAUNCH),
        # Competencia: daily running (8 SKUs vs 3 de Nike)
        (10, 2, "Adidas Ultraboost 5", None, "running", "daily running", "launch", 160000, RECENT_LAUNCH),
        (11, 2, "Adidas Adizero SL", None, "running", "daily running", "mature", 148000, OLD_LAUNCH),
        (12, 3, "New Balance 1080 v13", None, "running", "daily running", "mature", 178000, OLD_LAUNCH),
        (13, 3, "New Balance Rebel v4", None, "running", "daily running", "mature", 140000, OLD_LAUNCH),
        (14, 4, "Puma Deviate Nitro 3", None, "running", "daily running", "mature", 165000, OLD_LAUNCH),
        (15, 4, "Puma Velocity Nitro 3", None, "running", "daily running", "mature", 135000, OLD_LAUNCH),
        (16, 2, "Adidas Supernova Rise", None, "running", "daily running", "mature", 140000, OLD_LAUNCH),
        (17, 3, "New Balance FuelCell", None, "running", "daily running", "mature", 155000, OLD_LAUNCH),
        # Competencia: lifestyle
        (20, 2, "Adidas Samba OG", None, "lifestyle", "lifestyle", "mature", 168000, OLD_LAUNCH),
        (21, 2, "Adidas Gazelle", None, "lifestyle", "lifestyle", "mature", 140000, OLD_LAUNCH),
        # Competencia: trail running (Nike sin presencia => white space)
        (30, 4, "Puma Voyage Nitro", None, "running", "trail running", "mature", 150000, OLD_LAUNCH),
        (31, 3, "New Balance Hierro", None, "running", "trail running", "mature", 160000, OLD_LAUNCH),
        (32, 2, "Adidas Terrex Agravic", None, "running", "trail running", "mature", 170000, OLD_LAUNCH),
    ]
    conn.executemany(
        "INSERT INTO products (id, brand_id, product_name, franchise, category, use_case, "
        "lifecycle_stage, msrp, launch_date, country_code) VALUES (?,?,?,?,?,?,?,?,?,'AR')",
        rows,
    )


def _prices(conn):
    # (product, retailer, full_price, current_price, discount_pct)
    rows = [
        # P1 Pegasus: caro y con poco descuento
        (1, 1, 210000, 200000, 5.0), (1, 2, 210000, 200000, 5.0),
        # C10 Ultraboost: 20% más barato que Pegasus, en 4 retailers, en markdown
        (10, 1, 213000, 160000, 25.0), (10, 2, 213000, 160000, 25.0),
        (10, 3, 213000, 160000, 25.0), (10, 4, 213000, 160000, 25.0),
        # C16 Supernova: segundo competidor en markdown profundo
        (16, 1, 200000, 140000, 30.0),
        # P2 Vomero: sobre-descontado pese a estar a la par del competidor
        (2, 1, 214000, 150000, 30.0), (2, 2, 214000, 150000, 30.0),
        (11, 1, 164000, 148000, 10.0), (11, 2, 164000, 148000, 10.0),
        # P3 Invincible: descuenta sin presión competitiva real
        (3, 1, 225000, 180000, 20.0), (3, 2, 225000, 180000, 20.0),
        (12, 1, 202000, 178000, 12.0), (12, 2, 202000, 178000, 12.0),
        # P4 Air Max vs Samba (quiebre del competidor)
        (4, 1, 170000, 170000, 0.0),
        (20, 1, 168000, 168000, 0.0),
        # P5 Air Force 1 muy por debajo de un competidor equivalente
        (5, 1, 120000, 120000, 0.0),
        (21, 1, 140000, 140000, 0.0),
    ]
    conn.executemany(
        "INSERT INTO price_observations (product_id, retailer_id, observed_at, full_price, "
        "current_price, discount_pct, currency) VALUES (?,?,?,?,?,?,'ARS')",
        [(p, r, OBS_DATE, f, c, d) for p, r, f, c, d in rows],
    )


def _stock(conn):
    rows = [
        (1, 1, 85.0), (1, 2, 80.0),
        (10, 1, 75.0), (10, 2, 75.0), (10, 3, 75.0), (10, 4, 75.0),
        (2, 1, 82.0), (11, 1, 79.0),
        (3, 1, 88.0), (12, 1, 80.0),
        (4, 1, 90.0), (20, 1, 30.0),   # competidor en quiebre
        (5, 1, 70.0), (21, 1, 70.0),
    ]
    conn.executemany(
        "INSERT INTO stock_observations (product_id, retailer_id, observed_at, in_stock, "
        "availability_pct, sizes_available, sizes_total) VALUES (?,?,?,?,?,?,?)",
        [(p, r, OBS_DATE, 1 if a > 0 else 0, a, int(a / 10), 10) for p, r, a in rows],
    )


def _signals_and_buzz(conn):
    conn.executemany(
        "INSERT INTO reviews (product_id, retailer_id, source, rating, review_count, observed_at) "
        "VALUES (?,?,?,?,?,?)",
        [(1, 1, "retailer", 4.6, 120, OBS_DATE), (10, 1, "retailer", 4.4, 80, OBS_DATE)],
    )
    # Caída de share of shelf de Nike Pegasus
    conn.execute(
        "INSERT INTO market_signals (signal_type, entity_type, entity_id, country_code, value, "
        "delta, acceleration, period_start, period_end) "
        "VALUES ('share_of_shelf','product','1','AR', 22.0, -6.0, -0.2, '2026-06-01','2026-06-30')"
    )
    social = [
        # Ultraboost acelera fuerte entre períodos
        (10, "2026-06-01", "2026-06-30", 100, 20),
        (10, "2026-07-01", "2026-07-31", 400, 60),
        # Demanda concentrada en trail running, donde Nike no tiene SKUs
        (30, "2026-07-01", "2026-07-31", 800, 40),
        (31, "2026-07-01", "2026-07-31", 800, 40),
        (32, "2026-07-01", "2026-07-31", 800, 40),
    ]
    conn.executemany(
        "INSERT INTO social_mention_aggregates (product_id, period_start, period_end, "
        "country_code, source_type, mention_count, comention_count, sentiment_score, topic) "
        "VALUES (?,?,?,'AR','forum',?,?,0.3,'running')",
        social,
    )
    conn.execute(
        "INSERT INTO editorial_mentions (source_name, url, title, published_at, mention_type, "
        "product_a_id, product_b_id, country_code) VALUES ('Runner AR','http://x','Pegasus vs "
        "Ultraboost', ?, 'versus', 1, 10, 'AR')",
        (OBS_DATE,),
    )


def _matches(conn):
    rows = [
        (1, 10, 88.0), (1, 16, 62.0),
        (2, 11, 75.0),
        (3, 12, 68.0),
        (4, 20, 80.0),
        (5, 21, 85.0),
    ]
    conn.executemany(
        "INSERT INTO competitive_matches (nike_product_id, competitor_product_id, match_score, "
        "confidence, coverage) VALUES (?,?,?,'HIGH',0.85)",
        rows,
    )


def _build_db(tmp_path, *, with_matches: bool = True, name: str = "opps.db"):
    path = tmp_path / name
    init_db(path, drop=True)
    with get_conn(path) as conn:
        _base_rows(conn)
        _products(conn)
        _prices(conn)
        _stock(conn)
        _signals_and_buzz(conn)
        if with_matches:
            _matches(conn)
    return path


@pytest.fixture()
def db(tmp_path):
    return _build_db(tmp_path)


# ── reglas ──────────────────────────────────────────────────


def _by_type(path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in query("SELECT * FROM opportunities", path=path):
        out.setdefault(row["opportunity_type"], []).append(row)
    return out


def test_dispara_las_doce_reglas(db):
    counts = opportunities.run_opportunities(db)
    disparadas = {t for t in opportunities.OPPORTUNITY_TYPES if counts[t] > 0}
    assert len(disparadas) >= 6
    assert disparadas == set(opportunities.OPPORTUNITY_TYPES), (
        f"no dispararon: {set(opportunities.OPPORTUNITY_TYPES) - disparadas}"
    )
    assert counts["opportunities"] == sum(counts[t] for t in opportunities.OPPORTUNITY_TYPES)


def test_price_competitiveness_risk_cuantifica_gap_y_retailers(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["price_competitiveness_risk"][0]
    cfg = section("opportunities", "price_competitiveness_risk")

    assert row["nike_product_id"] == 1 and row["competitor_product_id"] == 10
    assert "20%" in row["description"]          # (200000-160000)/200000
    assert "2 retailers" in row["description"]
    assert cfg["min_retailers"] == 2
    assert row["family"] == FAMILIES["price_competitiveness_risk"]


def test_over_discounting_risk_detecta_descuento_sin_necesidad(db):
    opportunities.run_opportunities(db)
    rows = {r["nike_product_id"]: r for r in _by_type(db)["over_discounting_risk"]}
    assert 2 in rows                                    # Vomero: 30% vs 10%
    assert "20.0pp" in rows[2]["description"]
    assert "disponibilidad similar" in rows[2]["description"]


def test_full_price_opportunity(db):
    opportunities.run_opportunities(db)
    ids = {r["nike_product_id"] for r in _by_type(db)["full_price_opportunity"]}
    assert {2, 3} <= ids


def test_assortment_gap_cuenta_skus(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["assortment_gap"][0]
    assert "8 SKUs de competidores" in row["description"]
    assert "3 de Nike" in row["description"]
    assert "daily running" in row["description"]


def test_distribution_gap_lista_retailers_faltantes(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["distribution_gap"][0]
    assert row["nike_product_id"] == 1
    assert "2 retailers" in row["description"]
    assert "Mercado Libre" in row["description"] and "Falabella" in row["description"]
    # el retailer sugerido es el más importante de los faltantes
    assert row["retailer_id"] == 3


def test_share_of_shelf_risk_usa_market_signals(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["share_of_shelf_risk"][0]
    assert "6.0pp" in row["description"]
    assert row["nike_product_id"] == 1


def test_competitor_momentum(db):
    opportunities.run_opportunities(db)
    rows = {r["competitor_product_id"] for r in _by_type(db)["competitor_momentum"]}
    assert 10 in rows                                   # 100 -> 400 menciones


def test_competitor_stockout_opportunity(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["competitor_stockout_opportunity"][0]
    assert row["nike_product_id"] == 4 and row["competitor_product_id"] == 20
    assert "30%" in row["description"] and "90%" in row["description"]


def test_assortment_white_space(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["assortment_white_space"][0]
    assert "trail running" in row["description"]
    assert "0%" in row["description"]                   # Nike sin SKUs en el segmento


def test_premiumization_opportunity(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["premiumization_opportunity"][0]
    assert row["nike_product_id"] == 5 and row["competitor_product_id"] == 21
    assert "16.7%" in row["description"]


def test_promotional_pressure(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["promotional_pressure"][0]
    assert row["nike_product_id"] == 1
    assert "2 competidores" in row["description"]
    assert "27.5%" in row["description"]


def test_product_launch_threat(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["product_launch_threat"][0]
    assert row["competitor_product_id"] == 10
    assert "30 días" in row["description"]
    assert "4 retailers" in row["description"]


# ── contrato de salida ──────────────────────────────────────


def test_toda_oportunidad_tiene_familia_severidad_importancia_y_drivers(db):
    opportunities.run_opportunities(db)
    rows = query("SELECT * FROM opportunities", path=db)
    assert rows
    for row in rows:
        assert row["family"] == FAMILIES[row["opportunity_type"]]
        assert row["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        assert 0.0 <= row["business_importance"] <= 100.0
        assert row["confidence"] in {"LOW", "MEDIUM", "HIGH"}
        drivers = from_json(row["drivers"], [])
        assert drivers and all({"name", "value", "contribution"} <= set(d) for d in drivers)
        assert any(char.isdigit() for char in row["description"])


def test_cada_oportunidad_tiene_su_recomendacion(db):
    counts = opportunities.run_opportunities(db)
    recs = query(
        "SELECT r.*, o.opportunity_type FROM recommendations r "
        "JOIN opportunities o ON o.id = r.opportunity_id",
        path=db,
    )
    assert len(recs) == counts["opportunities"] == counts["recommendations"]
    for rec in recs:
        assert rec["action"] == opportunities.ACTIONS[rec["opportunity_type"]]
        assert rec["rationale"] and len(rec["rationale"]) > 20
        assert rec["score"] is not None and rec["confidence"] in {"LOW", "MEDIUM", "HIGH"}


def test_el_gate_se_refleja_en_los_drivers(db):
    opportunities.run_opportunities(db)
    row = _by_type(db)["price_competitiveness_risk"][0]
    relevance = next(d for d in from_json(row["drivers"], [])
                     if d["name"] == "competitive_relevance")
    # El gate se DERIVA de la relevancia observada con la fórmula de config, no
    # es un número fijo: `relevance_gate` es una rampa que satura en 1.0 a
    # partir de `business_importance.gate_full_relevance` (antes era el propio
    # valor de la relevancia, y eso le ponía techo a toda la escala).
    assert relevance["detail"]["gate"] == pytest.approx(
        scoring.relevance_gate(relevance["value"]))


def test_es_idempotente(db):
    primero = opportunities.run_opportunities(db)
    segundo = opportunities.run_opportunities(db)
    assert primero == segundo
    assert len(query("SELECT * FROM opportunities", path=db)) == primero["opportunities"]
    assert len(query("SELECT * FROM recommendations", path=db)) == primero["recommendations"]


# ── degradación elegante ────────────────────────────────────


def test_db_vacia_no_rompe(tmp_path):
    path = tmp_path / "vacia.db"
    init_db(path, drop=True)
    counts = opportunities.run_opportunities(path)
    assert counts["opportunities"] == 0
    assert all(counts[t] == 0 for t in opportunities.OPPORTUNITY_TYPES)


def test_sin_competitive_matches_las_reglas_de_catalogo_siguen_funcionando(tmp_path):
    """`competitive_matches` la puebla otro módulo: si está vacía no se rompe nada."""
    path = _build_db(tmp_path, with_matches=False, name="sin_matches.db")
    counts = opportunities.run_opportunities(path)

    dependientes = {"price_competitiveness_risk", "full_price_opportunity", "distribution_gap",
                    "competitor_stockout_opportunity", "premiumization_opportunity"}
    assert all(counts[t] == 0 for t in dependientes)
    # las que no dependen de matches siguen produciendo decisiones
    assert counts["assortment_gap"] > 0
    assert counts["assortment_white_space"] > 0
    assert counts["competitor_momentum"] > 0
    assert counts["share_of_shelf_risk"] > 0
    assert counts["product_launch_threat"] > 0
    assert counts["opportunities"] > 0


def test_solo_catalogo_sin_observaciones(tmp_path):
    path = tmp_path / "catalogo.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        _base_rows(conn)
        _products(conn)
    counts = opportunities.run_opportunities(path)
    assert counts["opportunities"] >= 0
    assert counts["price_competitiveness_risk"] == 0
