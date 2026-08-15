"""Tests del dataset demo y de app.seed.

Verifican tres cosas:
  1. Conteos mínimos por tabla (el dataset no se degradó).
  2. Integridad referencial (ninguna FK huérfana).
  3. Que existan los 5 escenarios de negocio que disparan las reglas del motor.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.seed import LOAD_ORDER, seed

#: Última fecha de snapshot del dataset (fija, no dinámica).
LAST_SNAPSHOT = "2026-08-15"

MIN_COUNTS = {
    "brands": 6,
    "countries": 2,
    "retailers": 8,
    "products": 40,
    "price_observations": 300,
    "stock_observations": 300,
    "reviews": 100,
    "editorial_mentions": 30,
    "social_mention_aggregates": 80,
}


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Base sembrada una sola vez para todo el módulo."""
    db_path = tmp_path_factory.mktemp("seed") / "intelligence.db"
    counts = seed(db_path, drop=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn, counts, db_path
    conn.close()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


# ── 1. Conteos ──────────────────────────────────────────────


def test_carga_todas_las_tablas(seeded):
    _, counts, _ = seeded
    assert set(counts) == set(LOAD_ORDER)
    assert all(n > 0 for n in counts.values()), counts


@pytest.mark.parametrize("table,minimum", sorted(MIN_COUNTS.items()))
def test_conteos_minimos(seeded, table, minimum):
    _, counts, _ = seeded
    assert counts[table] >= minimum, f"{table}={counts[table]} < {minimum}"


def test_cantidad_de_productos_en_rango(seeded):
    _, counts, _ = seeded
    assert 40 <= counts["products"] <= 50


def test_seed_es_idempotente(seeded):
    _, counts, db_path = seeded
    assert seed(db_path, drop=False) == counts
    assert seed(db_path, drop=True) == counts


def test_marca_foco_unica(seeded):
    conn, _, _ = seeded
    assert scalar(conn, "SELECT COUNT(*) FROM brands WHERE is_focus = 1") == 1
    assert scalar(conn, "SELECT name FROM brands WHERE is_focus = 1") == "Nike"


def test_paises_y_retailers(seeded):
    conn, _, _ = seeded
    assert scalar(conn, "SELECT currency FROM countries WHERE code = 'AR'") == "ARS"
    assert scalar(conn, "SELECT currency FROM countries WHERE code = 'US'") == "USD"
    # Retailers AR con importancia diferenciada y al menos un D2C.
    ar = scalar(conn, "SELECT COUNT(*) FROM retailers WHERE country_code = 'AR'")
    distintas = scalar(conn,
                       "SELECT COUNT(DISTINCT importance) FROM retailers WHERE country_code = 'AR'")
    assert ar >= 8 and distintas >= 6
    assert scalar(conn, "SELECT COUNT(*) FROM retailers WHERE channel = 'D2C'") >= 1
    assert scalar(conn, "SELECT COUNT(*) FROM retailers WHERE country_code = 'US'") >= 1
    assert scalar(conn, "SELECT MIN(importance) >= 0 AND MAX(importance) <= 1 FROM retailers") == 1


def test_tres_snapshots_temporales(seeded):
    conn, _, _ = seeded
    fechas = [r[0] for r in conn.execute(
        "SELECT DISTINCT observed_at FROM price_observations ORDER BY observed_at")]
    assert fechas == ["2026-06-15", "2026-07-15", "2026-08-15"]
    assert scalar(conn, "SELECT COUNT(DISTINCT observed_at) FROM stock_observations") == 3


def test_huecos_deliberados_para_degradacion(seeded):
    """~15% de los productos deben tener algún campo vacío."""
    conn, counts, _ = seeded
    con_huecos = scalar(conn, """
        SELECT COUNT(*) FROM products
        WHERE msrp IS NULL OR launch_date IS NULL OR description IS NULL""")
    ratio = con_huecos / counts["products"]
    assert 0.08 <= ratio <= 0.25, f"{con_huecos}/{counts['products']}"


def test_precios_y_descuentos_consistentes(seeded):
    conn, _, _ = seeded
    assert scalar(conn, """
        SELECT COUNT(*) FROM price_observations
        WHERE current_price > full_price OR discount_pct < 0 OR discount_pct > 45""") == 0
    # discount_pct coherente con full/current (tolerancia por redondeo a $100).
    assert scalar(conn, """
        SELECT COUNT(*) FROM price_observations
        WHERE ABS(discount_pct - (1 - current_price / full_price) * 100.0) > 0.5""") == 0


def test_stock_consistente(seeded):
    conn, _, _ = seeded
    assert scalar(conn, """
        SELECT COUNT(*) FROM stock_observations
        WHERE availability_pct < 0 OR availability_pct > 100
           OR sizes_available > sizes_total
           OR in_stock NOT IN (0, 1)""") == 0


def test_reviews_agregadas_e_individuales(seeded):
    conn, _, _ = seeded
    assert scalar(conn, "SELECT COUNT(*) FROM reviews WHERE review_count IS NOT NULL") >= 60
    assert scalar(conn, "SELECT COUNT(*) FROM reviews WHERE review_text IS NOT NULL") >= 40
    assert scalar(conn, "SELECT COUNT(*) FROM reviews WHERE rating < 0 OR rating > 5") == 0


def test_editorial_tipos_y_listas(seeded):
    conn, _, _ = seeded
    tipos = {r[0] for r in conn.execute("SELECT DISTINCT mention_type FROM editorial_mentions")}
    assert tipos == {"versus", "alternative", "same_list", "ranking", "review"}
    assert scalar(conn,
                  "SELECT COUNT(DISTINCT list_key) FROM editorial_mentions "
                  "WHERE list_key IS NOT NULL") >= 3
    # Pares fuertes esperados por el brief.
    for a, b in [(1, 16), (1, 22), (2, 17), (11, 29)]:
        assert scalar(conn, """
            SELECT COUNT(*) FROM editorial_mentions
            WHERE mention_type IN ('versus', 'alternative')
              AND ((product_a_id = ? AND product_b_id = ?)
                OR (product_a_id = ? AND product_b_id = ?))""", (a, b, b, a)) >= 1


def test_social_siempre_agregado_y_con_dos_ventanas(seeded):
    conn, _, _ = seeded
    ventanas = [tuple(r) for r in conn.execute(
        "SELECT DISTINCT period_start, period_end FROM social_mention_aggregates "
        "ORDER BY period_start")]
    assert ventanas == [("2026-06-16", "2026-07-15"), ("2026-07-16", "2026-08-15")]
    # Nunca individuos: toda fila tiene marca o producto y un volumen agregado.
    assert scalar(conn, """
        SELECT COUNT(*) FROM social_mention_aggregates
        WHERE (brand_id IS NULL AND product_id IS NULL)
           OR mention_count <= 0
           OR sentiment_score < -1 OR sentiment_score > 1""") == 0
    # sample_evidence es un JSON array no vacío.
    assert scalar(conn, """
        SELECT COUNT(*) FROM social_mention_aggregates
        WHERE sample_evidence IS NULL OR sample_evidence NOT LIKE '[%]'""") == 0
    # Co-menciones producto <-> co_product para social_competition_score.
    assert scalar(conn, """
        SELECT COUNT(*) FROM social_mention_aggregates
        WHERE product_id IS NOT NULL AND co_product_id IS NOT NULL
          AND comention_count > 0""") >= 20


def test_social_narrativa_ar(seeded):
    """Nike lidera performance; Adidas gana lifestyle; precio es el driver negativo."""
    conn, _, _ = seeded
    nike_perf = scalar(conn, """
        SELECT SUM(mention_count) FROM social_mention_aggregates
        WHERE brand_id = 1 AND product_id IS NULL AND topic = 'performance'""")
    otras_perf = scalar(conn, """
        SELECT MAX(v) FROM (SELECT SUM(mention_count) v FROM social_mention_aggregates
        WHERE brand_id != 1 AND product_id IS NULL AND topic = 'performance'
        GROUP BY brand_id)""")
    assert nike_perf > otras_perf

    nike_moda = scalar(conn, """
        SELECT SUM(mention_count) FROM social_mention_aggregates
        WHERE brand_id = 1 AND product_id IS NULL
          AND topic IN ('fashion', 'fashionable', 'streetwear')""")
    adidas_moda = scalar(conn, """
        SELECT SUM(mention_count) FROM social_mention_aggregates
        WHERE brand_id = 2 AND product_id IS NULL
          AND topic IN ('fashion', 'fashionable', 'streetwear')""")
    assert adidas_moda > nike_moda

    peor_topic = scalar(conn, """
        SELECT topic FROM social_mention_aggregates
        WHERE brand_id = 1 AND product_id IS NULL AND sentiment_score < 0
        ORDER BY mention_count DESC LIMIT 1""")
    assert peor_topic in ("expensive", "overpriced")

    # Al menos una marca con momentum fuertemente acelerado entre ventanas.
    acel = scalar(conn, """
        SELECT MAX(v2 * 1.0 / v1) FROM (
          SELECT brand_id,
                 SUM(CASE WHEN period_end = '2026-07-15' THEN mention_count ELSE 0 END) v1,
                 SUM(CASE WHEN period_end = '2026-08-15' THEN mention_count ELSE 0 END) v2
          FROM social_mention_aggregates WHERE product_id IS NULL GROUP BY brand_id)
        WHERE v1 > 0""")
    assert acel >= 1.15


# ── 2. Integridad referencial ───────────────────────────────


ORPHAN_QUERIES = {
    "products.brand_id": """
        SELECT COUNT(*) FROM products p LEFT JOIN brands b ON b.id = p.brand_id
        WHERE b.id IS NULL""",
    "products.country_code": """
        SELECT COUNT(*) FROM products p LEFT JOIN countries c ON c.code = p.country_code
        WHERE p.country_code IS NOT NULL AND c.code IS NULL""",
    "retailers.country_code": """
        SELECT COUNT(*) FROM retailers r LEFT JOIN countries c ON c.code = r.country_code
        WHERE c.code IS NULL""",
    "price_observations.product_id": """
        SELECT COUNT(*) FROM price_observations o LEFT JOIN products p ON p.id = o.product_id
        WHERE p.id IS NULL""",
    "price_observations.retailer_id": """
        SELECT COUNT(*) FROM price_observations o LEFT JOIN retailers r ON r.id = o.retailer_id
        WHERE r.id IS NULL""",
    "stock_observations.product_id": """
        SELECT COUNT(*) FROM stock_observations o LEFT JOIN products p ON p.id = o.product_id
        WHERE p.id IS NULL""",
    "stock_observations.retailer_id": """
        SELECT COUNT(*) FROM stock_observations o LEFT JOIN retailers r ON r.id = o.retailer_id
        WHERE r.id IS NULL""",
    "reviews.product_id": """
        SELECT COUNT(*) FROM reviews x LEFT JOIN products p ON p.id = x.product_id
        WHERE p.id IS NULL""",
    "reviews.retailer_id": """
        SELECT COUNT(*) FROM reviews x LEFT JOIN retailers r ON r.id = x.retailer_id
        WHERE x.retailer_id IS NOT NULL AND r.id IS NULL""",
    "editorial_mentions.product_a_id": """
        SELECT COUNT(*) FROM editorial_mentions e LEFT JOIN products p ON p.id = e.product_a_id
        WHERE e.product_a_id IS NOT NULL AND p.id IS NULL""",
    "editorial_mentions.product_b_id": """
        SELECT COUNT(*) FROM editorial_mentions e LEFT JOIN products p ON p.id = e.product_b_id
        WHERE e.product_b_id IS NOT NULL AND p.id IS NULL""",
    "editorial_mentions.country_code": """
        SELECT COUNT(*) FROM editorial_mentions e LEFT JOIN countries c ON c.code = e.country_code
        WHERE e.country_code IS NOT NULL AND c.code IS NULL""",
    "social.brand_id": """
        SELECT COUNT(*) FROM social_mention_aggregates s LEFT JOIN brands b ON b.id = s.brand_id
        WHERE s.brand_id IS NOT NULL AND b.id IS NULL""",
    "social.product_id": """
        SELECT COUNT(*) FROM social_mention_aggregates s LEFT JOIN products p ON p.id = s.product_id
        WHERE s.product_id IS NOT NULL AND p.id IS NULL""",
    "social.co_product_id": """
        SELECT COUNT(*) FROM social_mention_aggregates s
        LEFT JOIN products p ON p.id = s.co_product_id
        WHERE s.co_product_id IS NOT NULL AND p.id IS NULL""",
    "social.country_code": """
        SELECT COUNT(*) FROM social_mention_aggregates s
        LEFT JOIN countries c ON c.code = s.country_code
        WHERE s.country_code IS NOT NULL AND c.code IS NULL""",
}


@pytest.mark.parametrize("nombre,sql", sorted(ORPHAN_QUERIES.items()))
def test_sin_huerfanos(seeded, nombre, sql):
    conn, _, _ = seeded
    assert scalar(conn, sql) == 0, f"FK huérfana en {nombre}"


def test_foreign_key_check_de_sqlite(seeded):
    conn, _, _ = seeded
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# ── 3. Escenarios de negocio ────────────────────────────────


SQL_ESCENARIO_A = """
SELECT COUNT(*) FROM stock_observations sc
JOIN products pc ON pc.id = sc.product_id
JOIN brands   bc ON bc.id = pc.brand_id AND bc.is_focus = 0
JOIN stock_observations sn
     ON sn.retailer_id = sc.retailer_id AND sn.observed_at = sc.observed_at
JOIN products pn ON pn.id = sn.product_id AND pn.use_case = pc.use_case
JOIN brands   bn ON bn.id = pn.brand_id AND bn.is_focus = 1
WHERE sc.observed_at = ?
  AND sc.availability_pct < 40.0
  AND sn.availability_pct > 80.0
"""

SQL_ESCENARIO_B = """
WITH shelf AS (
  SELECT o.retailer_id,
         COUNT(DISTINCT CASE WHEN b.is_focus = 1 THEN p.id END) * 1.0
           / COUNT(DISTINCT p.id) AS nike_share
  FROM price_observations o
  JOIN products p ON p.id = o.product_id
  JOIN brands   b ON b.id = p.brand_id
  GROUP BY o.retailer_id)
SELECT COUNT(*) FROM shelf
JOIN price_observations opn ON opn.retailer_id = shelf.retailer_id AND opn.observed_at = ?
JOIN products pn ON pn.id = opn.product_id
JOIN brands   bn ON bn.id = pn.brand_id AND bn.is_focus = 1
JOIN stock_observations sn ON sn.product_id = pn.id
     AND sn.retailer_id = shelf.retailer_id AND sn.observed_at = opn.observed_at
JOIN price_observations opc ON opc.retailer_id = shelf.retailer_id
     AND opc.observed_at = opn.observed_at
JOIN products pc ON pc.id = opc.product_id AND pc.use_case = pn.use_case
JOIN brands   bc ON bc.id = pc.brand_id AND bc.is_focus = 0
WHERE shelf.nike_share < 0.25
  AND sn.availability_pct > 70.0
  AND ABS(opn.current_price - opc.current_price) * 100.0 / opc.current_price <= 5.0
"""

SQL_ESCENARIO_C = """
SELECT COUNT(*) FROM stock_observations s
JOIN products p ON p.id = s.product_id
JOIN brands   b ON b.id = p.brand_id AND b.is_focus = 1
JOIN social_mention_aggregates sm ON sm.product_id = p.id
     AND sm.co_product_id IS NULL AND sm.intent = 'want_to_buy'
     AND sm.period_end = ?
WHERE s.observed_at = ?
  AND s.availability_pct < 35.0
  AND sm.mention_count >= 150
"""

SQL_ESCENARIO_D = """
SELECT COUNT(*) FROM price_observations opn
JOIN products pn ON pn.id = opn.product_id
JOIN brands   bn ON bn.id = pn.brand_id AND bn.is_focus = 1
JOIN price_observations opc ON opc.retailer_id = opn.retailer_id
     AND opc.observed_at = opn.observed_at
JOIN products pc ON pc.id = opc.product_id
     AND pc.use_case = pn.use_case AND pc.subcategory = pn.subcategory
JOIN brands   bc ON bc.id = pc.brand_id AND bc.is_focus = 0
WHERE opn.observed_at = ?
  AND opn.discount_pct - opc.discount_pct >= 8.0
  AND (opn.current_price - opc.current_price) * 100.0 / opc.current_price <= 3.0
"""

SQL_ESCENARIO_E = """
SELECT COUNT(*) FROM (
  SELECT p.use_case, p.subcategory,
         SUM(CASE WHEN b.is_focus = 1 THEN 1 ELSE 0 END) nike_skus,
         SUM(CASE WHEN b.is_focus = 0 THEN 1 ELSE 0 END) comp_skus
  FROM products p JOIN brands b ON b.id = p.brand_id
  GROUP BY p.use_case, p.subcategory
  HAVING comp_skus >= 6
     AND (nike_skus = 0 OR comp_skus * 1.0 / nike_skus >= 2.0))
"""


def test_escenario_a_quiebre_de_stock_del_competidor(seeded):
    """(a) Competidor con availability < 40% donde Nike está > 80%."""
    conn, _, _ = seeded
    assert scalar(conn, SQL_ESCENARIO_A, (LAST_SNAPSHOT,)) > 0


def test_escenario_b_retail_media(seeded):
    """(b) Nike con stock alto y precio competitivo pero bajo share of shelf."""
    conn, _, _ = seeded
    assert scalar(conn, SQL_ESCENARIO_B, (LAST_SNAPSHOT,)) > 0


def test_escenario_c_no_aumentar_media(seeded):
    """(c) Nike con stock bajo y demanda social alta."""
    conn, _, _ = seeded
    assert scalar(conn, SQL_ESCENARIO_C, (LAST_SNAPSHOT, LAST_SNAPSHOT)) > 0


def test_escenario_d_sobre_descuento(seeded):
    """(d) Nike descuenta >= 8 pp más que un comparable pese a estar competitivo."""
    conn, _, _ = seeded
    assert scalar(conn, SQL_ESCENARIO_D, (LAST_SNAPSHOT,)) > 0


def test_escenario_e_assortment_gap(seeded):
    """(e) Segmento con >= 6 SKUs competidores y ratio >= 2x sobre Nike."""
    conn, _, _ = seeded
    assert scalar(conn, SQL_ESCENARIO_E) > 0
    # El segmento del brief: daily running de alta amortiguación.
    fila = conn.execute("""
        SELECT SUM(CASE WHEN b.is_focus = 1 THEN 1 ELSE 0 END) nike,
               SUM(CASE WHEN b.is_focus = 0 THEN 1 ELSE 0 END) comp
        FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE p.use_case = 'daily running' AND p.subcategory = 'high cushioning'""").fetchone()
    assert fila["comp"] >= 6 and fila["comp"] >= 2 * fila["nike"]


#: Share of shelf de Nike por retailer y captura. Réplica en SQL de la
#: definición de app.services.shelf: presencia = visto en precio o en stock.
SQL_SHELF_SERIES = """
WITH presence AS (
  SELECT observed_at, retailer_id, product_id FROM price_observations
  UNION
  SELECT observed_at, retailer_id, product_id FROM stock_observations
), shelf AS (
  SELECT pr.retailer_id, pr.observed_at,
         COUNT(DISTINCT CASE WHEN b.is_focus = 1 THEN pr.product_id END) * 100.0
           / COUNT(DISTINCT pr.product_id) AS share_pct
  FROM presence pr
  JOIN products p ON p.id = pr.product_id
  JOIN brands   b ON b.id = p.brand_id
  GROUP BY pr.retailer_id, pr.observed_at)
SELECT cur.retailer_id,
       ROUND(prev.share_pct, 3) share_prev,
       ROUND(cur.share_pct, 3)  share_last,
       ROUND(cur.share_pct - prev.share_pct, 3) delta_pp
FROM shelf cur
JOIN shelf prev ON prev.retailer_id = cur.retailer_id AND prev.observed_at = '2026-07-15'
WHERE cur.observed_at = '2026-08-15'
"""


def test_escenario_f_share_of_shelf_risk(seeded):
    """(f) Nike pierde > 4 pp de share of shelf entre las dos últimas capturas."""
    conn, _, _ = seeded
    filas = conn.execute(SQL_SHELF_SERIES + " AND cur.share_pct - prev.share_pct < -4.0"
                         " ORDER BY delta_pp").fetchall()
    assert len(filas) >= 2, f"sólo {len(filas)} retailer(s) pierden share of shelf"
    assert all(r["share_prev"] > r["share_last"] for r in filas)


def test_share_of_shelf_tiene_retailers_estables(seeded):
    """El contraste importa: no todos los retailers se mueven."""
    conn, _, _ = seeded
    estables = conn.execute(
        SQL_SHELF_SERIES + " AND ABS(cur.share_pct - prev.share_pct) < 0.001").fetchall()
    assert len(estables) >= 2


def test_surtido_varia_entre_capturas(seeded):
    """Altas y bajas de góndola: el set observado no es idéntico en las 3 fechas."""
    conn, _, _ = seeded
    altas = scalar(conn, """
        SELECT COUNT(*) FROM (
          SELECT retailer_id, product_id FROM price_observations WHERE observed_at = '2026-08-15'
          EXCEPT
          SELECT retailer_id, product_id FROM price_observations WHERE observed_at = '2026-07-15')""")
    bajas = scalar(conn, """
        SELECT COUNT(*) FROM (
          SELECT retailer_id, product_id FROM price_observations WHERE observed_at = '2026-07-15'
          EXCEPT
          SELECT retailer_id, product_id FROM price_observations WHERE observed_at = '2026-08-15')""")
    assert altas >= 5 and bajas >= 2
    # Precio y stock observan exactamente los mismos pares en cada fecha.
    assert scalar(conn, """
        SELECT COUNT(*) FROM (
          SELECT observed_at, retailer_id, product_id FROM price_observations
          EXCEPT
          SELECT observed_at, retailer_id, product_id FROM stock_observations)""") == 0


def test_reviews_agregadas_solo_de_fichas_vigentes(seeded):
    """Ningún agregado de reviews apunta a un par delistado o recién listado."""
    conn, _, _ = seeded
    assert scalar(conn, """
        SELECT COUNT(*) FROM reviews r
        WHERE r.review_count IS NOT NULL AND r.retailer_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM price_observations o
                          WHERE o.product_id = r.product_id AND o.retailer_id = r.retailer_id
                            AND o.observed_at = '2026-08-15')""") == 0
    assert scalar(conn, """
        SELECT COUNT(*) FROM reviews r
        WHERE r.review_count IS NOT NULL AND r.retailer_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM price_observations o
                          WHERE o.product_id = r.product_id AND o.retailer_id = r.retailer_id
                            AND o.observed_at = '2026-06-15')""") == 0


def test_reviews_individuales_coherentes_con_gondola(seeded):
    """Una review individual con retailer existe sólo si el par se observó esa fecha o antes."""
    conn, _, _ = seeded
    assert scalar(conn, """
        SELECT COUNT(*) FROM reviews r
        WHERE r.review_text IS NOT NULL AND r.retailer_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM price_observations o
                          WHERE o.product_id = r.product_id AND o.retailer_id = r.retailer_id
                            AND o.observed_at <= r.observed_at)""") == 0


def test_escenario_extra_distribution_gap(seeded):
    """Competidor presente en >= 2 retailers AR donde el comparable Nike no está."""
    conn, _, _ = seeded
    assert scalar(conn, """
        SELECT COUNT(*) FROM (
          SELECT pn.id nike_id, pc.id comp_id, COUNT(DISTINCT oc.retailer_id) gap
          FROM price_observations oc
          JOIN products pc ON pc.id = oc.product_id
          JOIN brands   bc ON bc.id = pc.brand_id AND bc.is_focus = 0
          JOIN retailers rt ON rt.id = oc.retailer_id AND rt.country_code = 'AR'
          JOIN products pn ON pn.use_case = pc.use_case
          JOIN brands   bn ON bn.id = pn.brand_id AND bn.is_focus = 1
          WHERE oc.retailer_id NOT IN
                (SELECT retailer_id FROM price_observations WHERE product_id = pn.id)
          GROUP BY pn.id, pc.id
          HAVING gap >= 2)""") > 0
