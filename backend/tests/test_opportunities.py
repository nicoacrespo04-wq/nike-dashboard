"""Tests del motor de oportunidades.

Todas las filas se insertan A MANO (sin `app/seed.py` ni `app/services/matching.py`):
el fixture está armado a propósito para disparar las 12 reglas.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from app.config import get_config, section
from app.db import get_conn, init_db, query
from app.services import opportunities, scoring, triage
from app.services.common import from_json

TODAY = date.today()
RECENT_LAUNCH = (TODAY - timedelta(days=30)).isoformat()
OLD_LAUNCH = (TODAY - timedelta(days=900)).isoformat()
OBS_DATE = TODAY.isoformat()

# Las reglas de stylecolor (`full_price_opportunity`, `clearance_needed`) miden
# ROTACIÓN, y eso necesita una SERIE, no una foto. El fixture observa tres veces
# con 30 días de separación; la última observación es exactamente la de antes,
# así que todas las reglas que leen "el último dato" ven lo mismo que siempre.
OBS_DATES = [(TODAY - timedelta(days=60)).isoformat(),
             (TODAY - timedelta(days=30)).isoformat(),
             OBS_DATE]

#: Descuento en las DOS primeras observaciones (la tercera es la del row).
#: Default: descuento plano (sin presión de markdown).
DISCOUNT_HISTORY: dict[int, tuple[float, float]] = {
    2: (18.0, 24.0),      # Vomero: markdown que se profundiza +6pp/mes
    3: (19.0, 19.5),      # Invincible: markdown estable
}

#: Disponibilidad en las DOS primeras observaciones.
#: Default: `actual + 6` y `actual + 3` => drena 3pp/mes (el stylecolor rota).
AVAILABILITY_HISTORY: dict[int, tuple[float, float]] = {
    2: (80.0, 81.0),      # Vomero: curva de talles estancada (no rota)
    3: (96.0, 92.0),      # Invincible: drena 4pp/mes (rota)
}

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
        "lifecycle_stage, msrp, launch_date, style_code, country_code) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'AR')",
        # El `style_code` es la identidad del STYLECOLOR: la unidad sobre la que
        # deciden `full_price_opportunity` y `clearance_needed`.
        [(*row, f"SC{row[0]:03d}-{100 + row[0]}") for row in rows],
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
        [
            (p, r, when, f, c, disc)
            for p, r, f, c, d in rows
            for when, disc in zip(OBS_DATES, (*DISCOUNT_HISTORY.get(p, (d, d)), d))
        ],
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
        [
            (p, r, when, 1 if av > 0 else 0, av, int(av / 10), 10)
            for p, r, a in rows
            for when, av in zip(OBS_DATES,
                                (*AVAILABILITY_HISTORY.get(p, (a + 6.0, a + 3.0)), a))
        ],
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
    disparadas = {t for t in opportunities.ALL_OPPORTUNITY_TYPES if counts[t] > 0}
    assert len(disparadas) >= 6
    assert disparadas == set(opportunities.ALL_OPPORTUNITY_TYPES), (
        f"no dispararon: {set(opportunities.ALL_OPPORTUNITY_TYPES) - disparadas}"
    )
    assert counts["opportunities"] == sum(counts[t] for t in opportunities.ALL_OPPORTUNITY_TYPES)


def test_los_doce_tipos_historicos_siguen_declarados():
    """`app.calibration` declara umbral y señal por cada uno: no se toca la tupla."""
    assert len(opportunities.OPPORTUNITY_TYPES) == 12
    assert set(opportunities.EXTRA_OPPORTUNITY_TYPES).isdisjoint(opportunities.OPPORTUNITY_TYPES)
    assert set(opportunities.RULES) == set(opportunities.ALL_OPPORTUNITY_TYPES)
    assert set(opportunities.ACTIONS) == set(opportunities.ALL_OPPORTUNITY_TYPES)


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
    """Volver a full price se decide POR STYLECOLOR, según su rotación.

    Regla de negocio: no se vuelve a precio lleno todos los stylecolors de una
    silueta o franquicia, porque cada colorway rota distinto. El producto 3
    (Invincible) drena 4pp/mes: rota, puede volver a full price. El producto 2
    (Vomero) tiene la curva de talles estancada: acumula WOH y hay que
    liquidarlo, aunque su franquicia esté competitiva en precio.
    """
    opportunities.run_opportunities(db)
    by_type = _by_type(db)

    full_price = {r["nike_product_id"] for r in by_type["full_price_opportunity"]}
    assert 3 in full_price
    assert 2 not in full_price, "un stylecolor que no rota no vuelve a full price"

    clearance = {r["nike_product_id"] for r in by_type.get("clearance_needed", [])}
    assert 2 in clearance, "el stylecolor estancado tiene que salir como liquidación"


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


# ── diversidad: agrupación por producto Nike ────────────────
#
# El problema que resuelven estos tests: el Opportunity Center se ordena por
# `business_importance` y un producto con muchas oportunidades legítimas se
# quedaba con toda la primera pantalla (en la base demo, 9 de las 10 primeras
# filas eran la misma zapatilla). La respuesta es AGRUPAR, no descartar, así que
# lo que hay que fijar es: no se pierde ninguna fila, no cambia la identidad de
# ninguna (o el triaje y el historial se cortan), y la primera pantalla deja de
# ser un solo SKU.


@contextmanager
def diversity(**overrides):
    """Sobrescribe ``opportunities.diversity`` de config dentro del bloque."""
    config = get_config()["opportunities"]
    previous = config.get("diversity")
    config["diversity"] = overrides
    try:
        yield
    finally:
        if previous is None:
            config.pop("diversity", None)
        else:
            config["diversity"] = previous


def _group_detail(row: dict) -> dict | None:
    """`detail` del driver de grupo de una fila persistida, si lo tiene."""
    for driver in from_json(row["drivers"], []):
        if driver["name"] == "opportunity_group":
            return driver["detail"]
    return None


def _entity_keys(path) -> set[str]:
    return {triage.entity_key(row) for row in query("SELECT * FROM opportunities", path=path)}


def _raw_draft_counts(path) -> dict[str, int]:
    """Drafts CRUDOS por regla, sin pasar por la persistencia."""
    ctx = opportunities.build_context(path)
    return {t: len(opportunities.RULES[t](ctx)) for t in opportunities.ALL_OPPORTUNITY_TYPES}


def test_agrupar_no_pierde_ninguna_oportunidad(db):
    """Cada draft que produce una regla termina siendo una fila propia.

    El pedido explícito del negocio es AGRUPAR sobre DESCARTAR: una oportunidad
    agrupada tiene que seguir existiendo. También es lo que audita
    `app.calibration` (drafts producidos vs filas persistidas por regla).
    """
    counts = opportunities.run_opportunities(db)
    crudos = _raw_draft_counts(db)

    for rule, n in crudos.items():
        assert counts[rule] == n, f"{rule}: {n} drafts -> {counts[rule]} filas persistidas"
    assert counts["opportunities"] == sum(crudos.values())


def test_agrupar_no_cambia_la_identidad_de_ninguna_oportunidad(db):
    """`entity_key` idéntica con y sin agrupación: el triaje no se entera.

    Si la agrupación tocara la identidad (fusionando filas, cambiando el
    competidor o el retailer de la fila que sobrevive), el estado que el equipo
    ya cargó — descartada, pospuesta, asignada — quedaría huérfano en la próxima
    corrida.
    """
    with diversity(enabled=False):
        opportunities.run_opportunities(db)
        sin_agrupar = _entity_keys(db)

    opportunities.run_opportunities(db)
    agrupado = _entity_keys(db)

    assert agrupado == sin_agrupar
    filas = query("SELECT * FROM opportunities", path=db)
    assert len(agrupado) == len(filas), "dos filas con la misma identidad rompen el triaje"


def test_el_triaje_sigue_apuntando_a_la_misma_oportunidad_tras_recalcular(db):
    """Descartar una variante agrupada → recalcular → sigue descartada."""
    opportunities.run_opportunities(db)
    variantes = [row for row in query("SELECT * FROM opportunities", path=db)
                 if (_group_detail(row) or {}).get("role") == "variant"]
    assert variantes, "el fixture tiene que producir al menos una variante agrupada"

    objetivo = variantes[0]
    key = triage.entity_key(objetivo)
    triage.set_state(key, "dismissed", db, updated_by="nico")

    opportunities.run_opportunities(db)

    iguales = [row for row in query("SELECT * FROM opportunities", path=db)
               if triage.entity_key(row) == key]
    assert len(iguales) == 1
    assert triage.get_state(key, db)["state"] == "dismissed"


def test_la_cabecera_del_grupo_lleva_todas_sus_variantes_y_son_filas_reales(db):
    """La tarjeta del grupo contiene la lista completa, y cada variante existe."""
    opportunities.run_opportunities(db)
    filas = query("SELECT * FROM opportunities", path=db)
    por_clave = {triage.entity_key(row): row for row in filas}

    grupos: dict[str, list[dict]] = {}
    for row in filas:
        detail = _group_detail(row)
        if detail:
            grupos.setdefault(detail["group_key"], []).append(row)

    assert grupos, "el fixture tiene que producir grupos"
    for group_key, miembros in grupos.items():
        cabeceras = [r for r in miembros if _group_detail(r)["role"] == "head"]
        assert len(cabeceras) == 1, f"{group_key}: {len(cabeceras)} cabeceras"
        detail = _group_detail(cabeceras[0])
        assert detail["size"] == len(miembros)
        assert len(detail["members"]) == len(miembros)
        # cada variante listada en la tarjeta es una fila accesible de la base
        for member in detail["members"]:
            assert member["entity_key"] in por_clave
            assert member["opportunity_type"] and member["title"] and member["action"]
        # y cada variante sabe volver a su cabecera
        for row in miembros:
            assert _group_detail(row)["head_entity_key"] == detail["entity_key"]


def test_la_cabecera_conserva_su_importancia_y_las_variantes_bajan_con_piso(db):
    """La más grave de cada producto compite de igual a igual; el resto baja."""
    with diversity(repeat_decay=0.75, min_factor=0.35):
        opportunities.run_opportunities(db)
        for row in query("SELECT * FROM opportunities", path=db):
            detail = _group_detail(row)
            if detail is None:
                continue
            base, mostrado = detail["base_importance"], row["business_importance"]
            if detail["role"] == "head":
                assert detail["rank_factor"] == 1.0
                assert mostrado == pytest.approx(base, abs=0.01)
            else:
                assert detail["rank"] >= 1
                assert 0.35 <= detail["rank_factor"] < 1.0
                assert mostrado == pytest.approx(base * detail["rank_factor"], abs=0.01)
                # nunca desaparece: conserva el piso de su importancia real
                assert mostrado >= base * 0.35 - 0.01


def test_la_severidad_no_baja_por_la_posicion_en_el_ranking(db):
    """`severity` sale de la importancia REAL, no del score atenuado.

    Una oportunidad no se vuelve menos grave porque otra del mismo producto la
    superó en la lista: si eso pasara, el filtro por severidad dejaría de
    encontrar la variante crítica que quedó decimoquinta.
    """
    opportunities.run_opportunities(db)
    atenuadas = 0
    for row in query("SELECT * FROM opportunities", path=db):
        detail = _group_detail(row)
        base = detail["base_importance"] if detail else row["business_importance"]
        assert row["severity"] == scoring.severity(base)
        if detail and detail["rank_factor"] < 1.0:
            atenuadas += 1
            assert row["business_importance"] < base
    assert atenuadas, "el fixture tiene que atenuar alguna variante"


def test_agrupar_descongestiona_la_primera_pantalla(db):
    """La métrica que motivó el cambio: cuántos productos distintos se ven."""
    with diversity(enabled=False):
        opportunities.run_opportunities(db)
        antes = opportunities.concentration(db)

    opportunities.run_opportunities(db)
    despues = opportunities.concentration(db)

    # ninguna oportunidad se pierde por el camino
    assert despues["total"] == antes["total"]
    assert despues["by_product"] == antes["by_product"]
    # pero la primera pantalla deja de ser el mismo SKU repetido
    assert despues["distinct_in_screen"] > antes["distinct_in_screen"]
    assert despues["max_repeat_in_screen"] < antes["max_repeat_in_screen"]


def test_la_diversidad_se_puede_apagar_sin_tocar_codigo(db):
    """`enabled: false` devuelve el ranking a la importancia pura."""
    with diversity(enabled=False):
        opportunities.run_opportunities(db)
        filas = query("SELECT * FROM opportunities", path=db)
        assert filas
        assert all(_group_detail(row) is None for row in filas)
        assert all(row["severity"] == scoring.severity(row["business_importance"])
                   for row in filas)


def test_rank_penalty_false_agrupa_pero_no_toca_el_score(db):
    """Cuando la UI agrupe nativo, se apaga la atenuación y quedan las tarjetas."""
    with diversity(rank_penalty=False):
        opportunities.run_opportunities(db)
        filas = query("SELECT * FROM opportunities", path=db)
        agrupadas = [row for row in filas if _group_detail(row)]
        assert agrupadas
        for row in agrupadas:
            detail = _group_detail(row)
            assert detail["rank_factor"] == 1.0
            assert row["business_importance"] == pytest.approx(detail["base_importance"],
                                                               abs=0.01)
        assert any(_group_detail(row)["size"] > 1 for row in agrupadas)


def test_el_driver_de_grupo_respeta_el_contrato_canonico_de_driver(db):
    """`value` en 0..1 y `contribution` 0: la API publica los drivers tal cual.

    El contrato lo fija `tests/test_api_drivers.py`, pero ese test se saltea si
    no hay base del pipeline; acá queda amarrado con el fixture propio. Poner el
    tamaño del grupo en `value` (11) rompía la escala publicada.
    """
    opportunities.run_opportunities(db)
    vistos = 0
    for row in query("SELECT * FROM opportunities", path=db):
        drivers = from_json(row["drivers"], [])
        assert sum(d["contribution"] for d in drivers) == pytest.approx(100.0, abs=0.5)
        for driver in drivers:
            assert 0.0 <= driver["value"] <= 1.0, driver
        detail = _group_detail(row)
        if detail is None:
            continue
        vistos += 1
        grupo = next(d for d in drivers if d["name"] == "opportunity_group")
        assert grupo["value"] == pytest.approx(detail["rank_factor"])
        assert grupo["contribution"] == 0.0
        assert isinstance(detail["size"], int) and detail["size"] >= 1
    assert vistos, "el fixture tiene que producir drivers de grupo"


def test_la_clave_de_grupo_es_estable_y_no_colisiona_con_la_de_una_oportunidad():
    """Identidad NUEVA: convive con `entity_key`, no la reemplaza."""
    from app.services import history

    key = opportunities.group_key_of("nike_product", 1)
    assert key == opportunities.group_key_of("nike_product", 1)          # determinística
    assert key == history.entity_key(opportunities.GROUP_KIND,
                                     group_axis="nike_product", entity_id=1)
    assert key != opportunities.group_key_of("nike_product", 2)
    assert key != opportunities.group_key_of("retailer", 1)              # otro eje
    # y jamás puede confundirse con la clave de una oportunidad de ese producto
    assert key != triage.entity_key({"opportunity_type": "promotional_pressure",
                                     "nike_product_id": 1, "competitor_product_id": None,
                                     "retailer_id": None, "country_code": "AR"})


def test_dos_drafts_con_la_misma_identidad_se_fusionan_en_vez_de_duplicarla():
    """Duplicar una `entity_key` haría que el triaje se aplique a una fila sí y a otra no.

    No dispara con los datos de hoy (el motor produce claves todas distintas);
    está para que un cambio futuro en una regla no rompa en silencio la
    supervivencia del triaje.
    """
    def _draft(description: str) -> opportunities.OpportunityDraft:
        return opportunities.OpportunityDraft(
            opportunity_type="promotional_pressure", nike_product_id=1,
            competitor_product_id=10, retailer_id=None, title="t",
            description=description, drivers=[], importance_inputs={},
            action="PREPARE_PROMO_RESPONSE", rationale="r", country_code="AR",
        )

    def _fila(descripcion: str, revenue: float) -> opportunities.RankedOpportunity:
        draft = _draft(descripcion)
        return opportunities.RankedOpportunity(
            draft=draft,
            importance=scoring.business_importance({"revenue_proxy": revenue}),
            drivers=[],
            entity_key=opportunities.entity_key_of(draft),
        )

    # gana la más importante, sin importar en qué orden llegan
    for orden in ((0.9, 0.1), (0.1, 0.9)):
        quedan, fusionadas = opportunities._merge_duplicates(
            [_fila("la primera", orden[0]), _fila("la segunda", orden[1])])
        assert fusionadas == 1
        assert len(quedan) == 1
        ganadora = "la primera" if orden[0] > orden[1] else "la segunda"
        perdedora = "la segunda" if orden[0] > orden[1] else "la primera"
        assert quedan[0].draft.description == f"{ganadora} Además: {perdedora}"


def test_las_oportunidades_sin_producto_nike_no_se_agrupan_por_producto(db):
    """Segmento o retailer: se agrupan por su propio eje, o quedan sueltas."""
    opportunities.run_opportunities(db)
    for row in query("SELECT * FROM opportunities WHERE nike_product_id IS NULL", path=db):
        detail = _group_detail(row)
        assert detail is None or detail["group_axis"] == "retailer"
