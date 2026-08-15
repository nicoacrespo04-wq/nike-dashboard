"""Tests de la ingesta de datos reales (`app.ingest`).

Cubren las cuatro cosas que se rompieron alguna vez en producción:

  1. **Mapeo puro** — sin Postgres: marca, retailer, país, taxonomía y las dos
     caras (competidor / Nike) de cada fila ancha.
  2. **Deduplicación** — el mismo SKU en N retailers y N fechas es UN producto
     con N observaciones; nunca dos productos ni observaciones duplicadas.
  3. **Saneamiento de precios** — mismo criterio que `web/src/lib/price.ts` y
     `db/load_csv.py`: `<= 0` es dato ausente, el precio inflado por cuotas se
     corrige dividiendo, y sin cuotas declaradas se descarta.
  4. **Idempotencia** — dos corridas dejan la misma base, y la re-ingesta no
     pisa lo que escribió `enrichment`.
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

from app.ingest import mapping as M
from app.ingest.pricing_data import ingest_from_csv, ingest_rows
from app.ingest.retail_media import map_shelf_row, shelf_visibility_signals

# ============================================================
# Fixtures de datos (una fila ancha de `pricing_data`)
# ============================================================

BASE_ROW = {
    "fecha_corrida": "2026-08-10",
    "scraper": "Dexter_AR",
    "canal": "Dexter",
    "marca": "Adidas",
    "season": "FA26",
    "style_color": "FQ8080-100",
    "product_code_competitor": "FQ8080",
    "marketing_name": "Nike Pegasus 41",
    "division": "FOOTWEAR DIVISION",
    "category": "RUNNING",
    "franchise_scrapper": "Pegasus",
    "gender": "MENS",
    "productcode_competitor": "IE2960",
    "product_name_competitor": "adidas Ultraboost Light",
    "category_competitor": "RUNNING",
    "division_competitor": "FOOTWEAR DIVISION",
    "franchise_competitor": "Ultraboost",
    "gender_competitor": "MENS",
    "size_available_competitor": 6,
    "size_available_nike": 8,
    "link_pdp_competitor": "https://dexter.com.ar/p/IE2960",
    "competitor_full_price": 329999,
    "competitor_final_price": 296999.1,
    "cuotas_competitor": "6 cuotas sin interés",
    "nike_full_price": 319999,
    "nike_final_price": 319999,
    "cuotas_nike": "3 cuotas sin interés",
    "text_sizes_nike": "38 | 39 | 40 | 41 | 42 | 43 | 44 | 45",
    "text_sizes_competitor": "39 | 40 | 41 | 42 | 43 | 44",
    "pdp_nike": "https://www.nike.com.ar/p/FQ8080-100",
    "precio_sugerido": 319999,
    "silueta": "RUNNING",
}


def row(**overrides):
    return {**BASE_ROW, **overrides}


@pytest.fixture(autouse=True)
def _clean_config():
    """Cada test arranca con la config de ingesta por defecto."""
    M.reset_config_cache()
    yield
    M.reset_config_cache()


# ============================================================
# 1. Mapeo puro — marca
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    ("Nike", "NIKE"), ("NIKE", "NIKE"), ("nike", "NIKE"), ("  nike  ", "NIKE"),
    ("Puma", "PUMA"), ("PUMA", "PUMA"), ("puma", "PUMA"),
    ("Adidas", "ADIDAS"), ("adidas", "ADIDAS"), ("ADIDAS", "ADIDAS"),
    ("adidas originals", "ADIDAS"), ("New Balance", "NEW BALANCE"),
])
def test_marca_se_normaliza_a_upper(raw, expected):
    assert M.map_brand(row(marca=raw)) == expected


def test_el_lado_nike_siempre_es_nike():
    assert M.map_brand(row(marca="Puma"), M.NIKE) == "NIKE"


def test_marca_ausente_se_deduce_del_sitio_de_marca():
    assert M.map_brand(row(marca=None, scraper="ADIDAS_7", canal="Adidas AR")) == "ADIDAS"
    assert M.map_brand(row(marca=None, scraper="OtroRaro", canal="Otro")) == "UNKNOWN"


# ============================================================
# 2. Mapeo puro — retailer y país
# ============================================================

def test_retailer_sale_del_canal_y_es_configurable():
    retailer = M.map_retailer(row(canal="StockCenter"))
    assert retailer["name"] == "Stock Center"          # nombre canónico
    assert retailer["is_retailer"] is True
    assert retailer["channel"] == "B2B"
    assert retailer["importance"] == pytest.approx(0.8)


def test_retailers_altos_y_bajos_segun_importancia():
    alta = {M.map_retailer(row(canal=c))["importance"] for c in ("Dexter", "StockCenter")}
    baja = {M.map_retailer(row(canal=c))["importance"] for c in ("Grid", "Sporting")}
    assert min(alta) > max(baja)


def test_el_sufijo_de_pais_no_crea_un_retailer_nuevo():
    con_sufijo = M.map_retailer(row(canal=None, scraper="Dexter_AR"))
    sin_sufijo = M.map_retailer(row(canal="Dexter", scraper="Dexter"))
    assert con_sufijo["name"] == sin_sufijo["name"] == "Dexter"
    assert con_sufijo["key"] == sin_sufijo["key"]
    assert con_sufijo["importance"] == pytest.approx(0.85)


def test_marketplace_se_marca_como_tal():
    assert M.map_retailer(row(canal="Moov"))["channel"] == "MARKETPLACE"


@pytest.mark.parametrize("scraper", [
    "nike_ar_general", "nike_co_general", "nike_us_general", "URU", "USA",
    "ADIDAS_7", "Puma_AR",
])
def test_scrapers_que_no_son_retailers_quedan_fuera_de_la_lista(scraper):
    retailer = M.map_retailer(row(scraper=scraper, canal=None))
    assert retailer["is_retailer"] is False            # no es un retailer
    assert retailer["channel"] == "D2C"                # pero sí catálogo D2C


def test_pais_sale_del_scraper():
    assert M.map_country(row(scraper="URU", canal="Nike UY")) == "UY"
    assert M.map_country(row(scraper="nike_co_general", canal=None)) == "CO"
    assert M.map_country(row(scraper="Dexter_AR", canal="Dexter")) == "AR"
    assert M.map_country(row(scraper="Moov_CL", canal="Moov"), "AR") == "CL"
    assert M.map_country(row(scraper="Moov", canal="Moov"), "AR") == "AR"


def test_el_precio_nike_se_imputa_al_d2c_del_pais():
    competidor = M.retailer_for_side(row(), M.COMPETITOR, "AR")
    nike = M.retailer_for_side(row(), M.NIKE, "AR")
    assert competidor["name"] == "Dexter"
    assert nike["name"] == "nike.com.ar" and nike["channel"] == "D2C"


# ============================================================
# 3. Mapeo puro — producto y taxonomía
# ============================================================

def test_una_fila_produce_dos_productos_distintos():
    competidor = M.map_product(row(), M.COMPETITOR)
    nike = M.map_product(row(), M.NIKE)

    assert competidor["brand"] == "ADIDAS" and competidor["is_focus"] == 0
    assert competidor["product_name"] == "adidas Ultraboost Light"
    assert competidor["sku"] == "IE2960"
    assert competidor["franchise"] == "Ultraboost"
    assert competidor["url"] == "https://dexter.com.ar/p/IE2960"

    assert nike["brand"] == "NIKE" and nike["is_focus"] == 1
    assert nike["product_name"] == "Nike Pegasus 41"
    assert nike["sku"] == "FQ8080-100" and nike["style_code"] == "FQ8080-100"
    assert nike["franchise"] == "Pegasus"
    assert nike["url"] == "https://www.nike.com.ar/p/FQ8080-100"
    assert competidor["key"] != nike["key"]


def test_taxonomia_se_normaliza():
    producto = M.map_product(row(division_competitor="FOOTWEAR DIVISION",
                                 category_competitor="FOOTBALL/SOCCER",
                                 gender_competitor="WOMENS",
                                 silueta="BOTIN"))
    assert producto["category"] == "footwear"
    assert producto["sport"] == "football"
    assert producto["gender"] == "women"
    assert producto["subcategory"] == "botin"
    assert producto["age_segment"] is None


def test_kids_marca_age_segment():
    producto = M.map_product(row(gender_competitor="KIDS"))
    assert producto["gender"] == "kids" and producto["age_segment"] == "kids"


def test_lo_que_completa_enrichment_queda_en_null():
    producto = M.map_product(row())
    for campo in ("normalized_product_name", "use_case", "price_band",
                  "lifecycle_stage", "enrichment_version"):
        assert producto.get(campo) is None


def test_producto_sin_nombre_ni_codigo_no_es_mapeable():
    vacio = M.map_product(row(product_name_competitor=None,
                              productcode_competitor=None,
                              product_code_competitor=None))
    assert M.is_mappable_product(vacio) is False
    # Con código pero sin nombre sí es identificable (el código hace de nombre).
    con_codigo = M.map_product(row(product_name_competitor=None))
    assert M.is_mappable_product(con_codigo) is True
    assert con_codigo["product_name"] == "IE2960"


# ============================================================
# 4. Precios sucios
# ============================================================

@pytest.mark.parametrize("valor", [0, 0.0, "0", -1, -329999])
def test_precio_menor_o_igual_a_cero_es_dato_ausente(valor):
    precio, motivo = M.sanitize_price(valor, "6 cuotas sin interés")
    assert precio is None and motivo == "zero"


@pytest.mark.parametrize("total,cuotas,unitario", [
    (2_639_992, "8 cuotas sin interés", 329_999.0),
    (2_399_992, "8 cuotas sin interés", 299_999.0),
    (2_079_992, "8 cuotas sin interés", 259_999.0),
    (2_639_992, 8, 329_999.0),
])
def test_precio_inflado_por_cuotas_se_corrige_dividiendo(total, cuotas, unitario):
    precio, motivo = M.sanitize_price(total, cuotas)
    assert precio == pytest.approx(unitario) and motivo == "cuotas"


def test_sin_cuotas_declaradas_el_precio_inflado_se_descarta():
    precio, motivo = M.sanitize_price(2_639_992, None)
    assert precio is None and motivo == "out_of_range"


def test_no_se_adivina_el_divisor():
    # 12 cuotas no devuelve el valor al rango => se descarta, no se prueba otro divisor.
    precio, motivo = M.sanitize_price(50_000_000, "12 cuotas sin interés")
    assert precio is None and motivo == "out_of_range"


def test_precio_plausible_pasa_intacto():
    assert M.sanitize_price(329_999, "8 cuotas sin interés") == (329_999.0, None)


def test_precio_ausente_no_es_un_error():
    assert M.sanitize_price(None, "8 cuotas") == (None, None)
    assert M.sanitize_price("", None) == (None, None)
    assert M.sanitize_price("N/D", None) == (None, None)


@pytest.mark.parametrize("raw,esperado", [
    ("8 cuotas sin interés", 8), ("12x sin interés", 12), ("3 CUOTAS", 3),
    ("6", 6), (None, None), ("1 cuota", None), ("48 cuotas", None), ("sin cuotas", None),
])
def test_parseo_de_cuotas(raw, esperado):
    assert M.parse_cuotas(raw) == esperado


def test_descuento_solo_con_precios_saneados():
    assert M.discount_pct(100_000, 70_000) == pytest.approx(30.0)
    assert M.discount_pct(100_000, None) is None
    assert M.discount_pct(None, 70_000) is None
    assert M.discount_pct(100_000, 150_000) is None      # inconsistente => N/D


def test_la_observacion_de_precio_reporta_el_motivo_del_descarte():
    obs = M.map_price_observation(row(competitor_final_price=0,
                                      competitor_full_price=2_639_992,
                                      cuotas_competitor="8 cuotas sin interés"))
    assert obs["flags"] == {"competitor_final_price": "zero",
                            "competitor_full_price": "cuotas"}
    assert obs["full_price"] == pytest.approx(329_999.0)
    assert obs["current_price"] is None
    assert obs["usable"] is True


def test_fila_sin_ningun_precio_utilizable():
    obs = M.map_price_observation(row(competitor_final_price=0, competitor_full_price=0))
    assert obs["usable"] is False


# ============================================================
# 5. Stock
# ============================================================

def test_stock_mapea_talles_disponibles():
    obs = M.map_stock_observation(row(), M.COMPETITOR)
    assert obs["sizes_available"] == 6 and obs["in_stock"] == 1
    assert obs["sizes_total"] is None and obs["availability_pct"] is None
    assert obs["size_tokens"] == ["39", "40", "41", "42", "43", "44"]


def test_sin_talles_no_hay_observacion_de_stock():
    obs = M.map_stock_observation(row(size_available_competitor=None))
    assert obs["usable"] is False


def test_quiebre_de_stock():
    obs = M.map_stock_observation(row(size_available_competitor=0))
    assert obs["in_stock"] == 0 and obs["sizes_available"] == 0


# ============================================================
# 6. Deduplicación (el corazón del bug de doble conteo)
# ============================================================

RETAILERS = ["Dexter", "StockCenter", "Solo Deportes", "Moov"]
FECHAS = ["2026-07-27", "2026-08-03", "2026-08-10"]


def dataset_multi_retailer():
    """El mismo par (Nike, competidor) visto en 4 retailers y 3 fechas."""
    filas = []
    for fecha in FECHAS:
        for retailer in RETAILERS:
            filas.append(row(fecha_corrida=fecha, canal=retailer,
                             scraper=f"{retailer.replace(' ', '')}_AR",
                             marca=["Adidas", "ADIDAS", "adidas"][len(filas) % 3]))
    return filas


@pytest.fixture
def db_multi(tmp_path):
    db_path = tmp_path / "ingest.db"
    summary = ingest_rows(dataset_multi_retailer(), db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn, summary, db_path
    conn.close()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def test_el_mismo_sku_en_n_retailers_es_un_solo_producto(db_multi):
    conn, summary, _ = db_multi
    assert summary["rows_read"] == len(RETAILERS) * len(FECHAS)
    assert scalar(conn, "SELECT COUNT(*) FROM products") == 2          # Nike + competidor
    assert summary["products_nike"] == 1 and summary["products_competitor"] == 1
    assert scalar(conn, "SELECT COUNT(*) FROM brands") == 2            # NIKE + ADIDAS
    # El casing mixto de marca no crea marcas nuevas.
    assert scalar(conn, "SELECT COUNT(*) FROM brands WHERE name='ADIDAS'") == 1


def test_una_observacion_por_producto_retailer_y_fecha(db_multi):
    conn, _, _ = db_multi
    duplicadas = scalar(conn, """
        SELECT COUNT(*) FROM (SELECT product_id, retailer_id, observed_at, COUNT(*) c
                              FROM price_observations GROUP BY 1,2,3 HAVING c > 1)""")
    assert duplicadas == 0
    # 4 retailers x 3 fechas para el competidor + 3 fechas del D2C de Nike.
    assert scalar(conn, "SELECT COUNT(*) FROM price_observations") == 15


def test_el_precio_nike_no_se_replica_en_cada_retailer(db_multi):
    conn, _, _ = db_multi
    filas = conn.execute("""
        SELECT r.name, COUNT(*) n FROM price_observations po
        JOIN products p ON p.id = po.product_id
        JOIN brands b ON b.id = p.brand_id AND b.is_focus = 1
        JOIN retailers r ON r.id = po.retailer_id GROUP BY 1""").fetchall()
    assert [(f["name"], f["n"]) for f in filas] == [("nike.com.ar", len(FECHAS))]


def test_los_retailers_se_deduplican_por_nombre_canonico(db_multi):
    conn, _, _ = db_multi
    nombres = {f["name"] for f in conn.execute("SELECT name FROM retailers").fetchall()}
    assert nombres == {"Dexter", "Stock Center", "Solo Deportes", "Moov", "nike.com.ar"}


def test_el_producto_conserva_el_valor_no_nulo_mas_reciente(tmp_path):
    filas = [
        row(fecha_corrida="2026-07-27", franchise_competitor=None,
            competitor_full_price=250_000),
        row(fecha_corrida="2026-08-10", franchise_competitor="Ultraboost",
            competitor_full_price=310_000),
    ]
    db_path = tmp_path / "merge.db"
    ingest_rows(filas, db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    producto = conn.execute(
        "SELECT franchise, msrp FROM products p JOIN brands b ON b.id = p.brand_id "
        "WHERE b.name = 'ADIDAS'").fetchone()
    assert producto["franchise"] == "Ultraboost"        # se completó el hueco
    assert producto["msrp"] == pytest.approx(310_000)   # gana el más reciente
    conn.close()


def test_los_precios_sucios_no_llegan_a_la_base(tmp_path):
    filas = [
        row(fecha_corrida="2026-08-10", canal="Dexter", competitor_final_price=0,
            competitor_full_price=0),
        row(fecha_corrida="2026-08-10", canal="Moov",
            competitor_final_price=2_639_992, competitor_full_price=2_639_992,
            cuotas_competitor="8 cuotas sin interés"),
        row(fecha_corrida="2026-08-10", canal="Grid",
            competitor_final_price=2_639_992, competitor_full_price=2_639_992,
            cuotas_competitor=None),
    ]
    db_path = tmp_path / "sucios.db"
    summary = ingest_rows(filas, db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)

    assert summary["prices_zero_discarded"] == 2          # full + final de la fila 1
    assert summary["prices_fixed_by_cuotas"] == 2         # full + final de la fila 2
    assert summary["prices_out_of_range_discarded"] == 2  # fila 3, sin cuotas
    assert scalar(conn, "SELECT COUNT(*) FROM price_observations "
                        "WHERE current_price <= 0 OR full_price <= 0") == 0
    assert scalar(conn, "SELECT COUNT(*) FROM price_observations "
                        "WHERE current_price > 2000000") == 0
    # De las tres filas sólo la corregida por cuotas deja precio del competidor.
    precios = [r[0] for r in conn.execute(
        "SELECT current_price FROM price_observations po JOIN products p ON p.id = po.product_id "
        "JOIN brands b ON b.id = p.brand_id WHERE b.name = 'ADIDAS'").fetchall()]
    assert sorted(x for x in precios if x is not None) == [pytest.approx(329_999.0)]
    conn.close()


def test_availability_pct_solo_si_el_grid_es_inferible(tmp_path):
    con_grid = row(size_available_competitor=6,
                   text_sizes_competitor="39 | 40 | 41 | 42 | 43 | 44 | 45 | 46")
    sin_grid = row(canal="Moov", scraper="Moov_AR", productcode_competitor="XX999",
                   product_name_competitor="Sin grid", size_available_competitor=6,
                   text_sizes_competitor=None)
    db_path = tmp_path / "stock.db"
    ingest_rows([con_grid, sin_grid], db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    filas = {r["sku"]: r for r in conn.execute(
        "SELECT p.sku, s.sizes_available, s.sizes_total, s.availability_pct "
        "FROM stock_observations s JOIN products p ON p.id = s.product_id").fetchall()}
    assert filas["IE2960"]["sizes_total"] == 8
    assert filas["IE2960"]["availability_pct"] == pytest.approx(75.0)
    assert filas["XX999"]["sizes_total"] is None
    assert filas["XX999"]["availability_pct"] is None    # no se inventa denominador
    conn.close()


def test_las_filas_de_otro_pais_se_descartan(tmp_path):
    filas = dataset_multi_retailer() + [
        row(scraper="URU", canal="Nike UY", marca="NIKE"),
        row(scraper="USA", canal="Nike US", marca="nike"),
    ]
    db_path = tmp_path / "pais.db"
    summary = ingest_rows(filas, db_path, country="AR", drop=True)
    assert summary["rows_skipped_country"] == 2
    conn = sqlite3.connect(db_path)
    assert scalar(conn, "SELECT COUNT(*) FROM countries WHERE code <> 'AR'") == 0
    conn.close()


def test_la_ingesta_no_escribe_en_tablas_de_otros_modulos(db_multi):
    conn, _, _ = db_multi
    for tabla in ("market_signals", "brand_insights", "opportunities",
                  "competitive_matches", "product_attributes", "reviews",
                  "editorial_mentions", "social_mention_aggregates"):
        assert scalar(conn, f"SELECT COUNT(*) FROM {tabla}") == 0, tabla


# ============================================================
# 7. Idempotencia
# ============================================================

def snapshot(db_path):
    conn = sqlite3.connect(db_path)
    try:
        estado = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("brands", "countries", "retailers", "products",
                            "price_observations", "stock_observations")}
        estado["suma_precios"] = conn.execute(
            "SELECT ROUND(SUM(COALESCE(current_price, 0)), 2) FROM price_observations"
        ).fetchone()[0]
        return estado
    finally:
        conn.close()


def test_dos_corridas_dejan_la_misma_base(tmp_path):
    db_path = tmp_path / "idem.db"
    filas = dataset_multi_retailer()

    primera = ingest_rows(filas, db_path, country="AR", drop=True)
    estado_1 = snapshot(db_path)
    segunda = ingest_rows(filas, db_path, country="AR", drop=False)
    estado_2 = snapshot(db_path)

    assert estado_1 == estado_2
    assert primera["products_inserted"] == 2 and primera["products_updated"] == 0
    assert segunda["products_inserted"] == 0 and segunda["products_updated"] == 2
    assert segunda["price_observations_replaced"] == segunda["price_observations"]


def test_la_carga_por_fecha_es_incremental(tmp_path):
    db_path = tmp_path / "incremental.db"
    filas = dataset_multi_retailer()

    total = 0
    for fecha in FECHAS:
        parcial = [f for f in filas if f["fecha_corrida"] == fecha]
        summary = ingest_rows(parcial, db_path, country="AR", drop=False)
        total += summary["price_observations"]

    completo = snapshot(db_path)
    assert completo["price_observations"] == total
    # Cargar por fecha o de una sola vez da exactamente lo mismo.
    otra = tmp_path / "completo.db"
    ingest_rows(filas, otra, country="AR", drop=True)
    assert snapshot(otra) == completo


def test_la_reingesta_no_pisa_lo_que_escribio_enrichment(tmp_path):
    from app.services.enrichment import run_enrichment

    db_path = tmp_path / "enrich.db"
    filas = dataset_multi_retailer()
    ingest_rows(filas, db_path, country="AR", drop=True)
    run_enrichment(db_path=db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    antes = [dict(r) for r in conn.execute(
        "SELECT id, normalized_product_name, use_case, price_band, enrichment_version "
        "FROM products ORDER BY id")]
    conn.close()
    assert any(r["enrichment_version"] for r in antes)

    ingest_rows(filas, db_path, country="AR", drop=False)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    despues = [dict(r) for r in conn.execute(
        "SELECT id, normalized_product_name, use_case, price_band, enrichment_version "
        "FROM products ORDER BY id")]
    conn.close()
    assert despues == antes


def test_convive_con_el_dataset_demo(tmp_path):
    """Ingerir sobre una base ya sembrada no duplica marcas ni retailers."""
    from app.seed import seed

    db_path = tmp_path / "mixta.db"
    seed(db_path, drop=True)
    conn = sqlite3.connect(db_path)
    demo_productos = scalar(conn, "SELECT COUNT(*) FROM products")
    demo_marcas = scalar(conn, "SELECT COUNT(*) FROM brands")
    demo_retailers = scalar(conn, "SELECT COUNT(*) FROM retailers")
    conn.close()

    summary = ingest_rows(dataset_multi_retailer(), db_path, country="AR", drop=False)

    conn = sqlite3.connect(db_path)
    # 'Adidas' (demo) y 'ADIDAS' (ingesta) son la misma marca, no dos filas.
    assert scalar(conn, "SELECT COUNT(*) FROM brands") == demo_marcas
    assert scalar(conn, "SELECT COUNT(*) FROM brands WHERE is_focus = 1") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM retailers") == demo_retailers
    assert scalar(conn, "SELECT COUNT(*) FROM products") == demo_productos + 2
    assert summary["products_inserted"] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_el_mismo_nombre_en_dos_paises_son_retailers_distintos():
    assert M.retailer_key("nike.com", "US") != M.retailer_key("nike.com", "AR")
    assert M.retailer_key("Stock Center", "AR") == M.retailer_key("stockcenter", "ar")


def test_el_motor_corre_sobre_datos_ingeridos(tmp_path):
    """Enrichment + matching sobre la base ingerida (sin dataset demo)."""
    from app.services.enrichment import run_enrichment
    from app.services.matching import run_matching

    db_path = tmp_path / "motor.db"
    filas = []
    for i, competidor in enumerate([("IE2960", "adidas Ultraboost Light", "Adidas"),
                                    ("37913901", "Puma Velocity Nitro 3", "Puma"),
                                    ("M1080K13", "New Balance 1080v13", "New Balance")]):
        for fecha in FECHAS:
            for retailer in RETAILERS:
                filas.append(row(fecha_corrida=fecha, canal=retailer,
                                 scraper=f"{retailer.replace(' ', '')}_AR",
                                 productcode_competitor=competidor[0],
                                 product_name_competitor=competidor[1],
                                 marca=competidor[2],
                                 competitor_final_price=250_000 + 10_000 * i))

    ingest_rows(filas, db_path, country="AR", drop=True)
    enriquecidos = run_enrichment(db_path=db_path)
    matches = run_matching(db_path=db_path)

    assert enriquecidos["products"] == 4          # 1 Nike + 3 competidores
    assert enriquecidos["updated"] == 4
    assert matches["nike_products"] == 1
    assert matches["matches"] > 0


# ============================================================
# 8. Ingesta desde CSV (encabezados del CSV original)
# ============================================================

CSV_HEADERS = {
    "fecha_corrida": "Fecha_Corrida", "scraper": "Scraper", "canal": "Canal",
    "marca": "Marca", "style_color": "StyleColor",
    "productcode_competitor": "ProductCode_Competitor",
    "product_name_competitor": "Product_Name_Competitor",
    "marketing_name": "Marketing_Name", "franchise_scrapper": "Franchise_Scrapper",
    "franchise_competitor": "Franchise_Competitor",
    "competitor_full_price": "Competitor_Full_Price",
    "competitor_final_price": "Competitor_Final_Price",
    "cuotas_competitor": "Cuotas_Competitor",
    "nike_final_price": "Nike_Final_Price", "cuotas_nike": "Cuotas_Nike",
    "size_available_competitor": "Size_Available_Competitor",
    "size_available_nike": "Size_Available_Nike",
    "division_competitor": "Division_Competitor",
    "category_competitor": "Category_Competitor",
    "gender_competitor": "Gender_Competitor", "silueta": "Silueta",
}


def test_ingesta_desde_csv_con_encabezados_originales(tmp_path):
    csv_path = tmp_path / "pricing.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_HEADERS.values()))
        writer.writeheader()
        for fila in dataset_multi_retailer():
            writer.writerow({header: fila.get(col) for col, header in CSV_HEADERS.items()})

    db_path = tmp_path / "csv.db"
    summary = ingest_from_csv(csv_path, db_path, country="AR", drop=True)

    assert summary["rows_read"] == len(RETAILERS) * len(FECHAS)
    assert summary["products"] == 2
    conn = sqlite3.connect(db_path)
    assert scalar(conn, "SELECT COUNT(*) FROM price_observations") > 0
    assert scalar(conn, "SELECT COUNT(*) FROM products WHERE sku = 'IE2960'") == 1
    conn.close()


def test_limit_corta_la_lectura(tmp_path):
    db_path = tmp_path / "limit.db"
    summary = ingest_rows(dataset_multi_retailer(), db_path, country="AR",
                          limit=3, drop=True)
    assert summary["rows_read"] == 3


# ============================================================
# 9. retail_media_search (share of shelf) — NO escribe market_signals
# ============================================================

SHELF_ROW = {
    "fecha_corrida": "2026-08-09", "scraper": "retail_media_v8_search",
    "canal": "Moov", "marca": "Nike", "season": "FA26",
    "search_term": "botines de futbol", "nike_visibility": "0.5",
    "type": "search", "category": "FOOTBALL/SOCCER", "division": "FOOTWEAR DIVISION",
}


def test_mapeo_de_una_fila_de_shelf():
    record = map_shelf_row(SHELF_ROW)
    assert record["retailer_name"] == "Moov"
    assert record["brand"] == "NIKE"
    assert record["visibility"] == pytest.approx(0.5)
    assert record["observed_at"] == "2026-08-09"
    assert record["country_code"] == "AR"


def test_share_of_shelf_se_calcula_sobre_la_visibilidad():
    filas = [
        {**SHELF_ROW, "marca": "Nike", "nike_visibility": "0.6"},
        {**SHELF_ROW, "marca": "Adidas", "nike_visibility": "0.3"},
        {**SHELF_ROW, "marca": "Puma", "nike_visibility": "0.1"},
    ]
    señales = shelf_visibility_signals(filas)
    assert len(señales) == 1
    señal = señales[0]
    assert señal["signal_type"] == "share_of_shelf"
    assert señal["entity_type"] == "retailer" and señal["entity_id"] == "Moov"
    assert señal["value"] == pytest.approx(60.0)


def test_las_señales_de_shelf_no_se_persisten(tmp_path):
    """La ingesta deja la función lista, pero `market_signals` es de otro módulo."""
    db_path = tmp_path / "shelf.db"
    ingest_rows(dataset_multi_retailer(), db_path, country="AR", drop=True)
    shelf_visibility_signals([SHELF_ROW])
    conn = sqlite3.connect(db_path)
    assert scalar(conn, "SELECT COUNT(*) FROM market_signals") == 0
    conn.close()
