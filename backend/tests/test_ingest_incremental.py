"""Tests de la ingesta INCREMENTAL (`app.ingest.incremental`).

Lo que tiene que ser cierto para que la carga semanal sirva en operación:

  1. **`last_ingested_at`** sabe hasta dónde llegó la carga (por país), y no
     explota con una base inexistente o vacía.
  2. **Deducción de `since`**: sin argumento, el delta arranca en la última
     fecha ya cargada — inclusive, porque una captura puede haber quedado a
     medias.
  3. **Append-only**: las observaciones ya cargadas NO se pisan. El histórico
     de precios y stock es lo que alimenta tendencias y momentum: si se
     reemplaza, se pierde.
  4. **UPSERT de productos**: el mismo SKU en el delta actualiza la fila que ya
     existe, no crea una nueva.
  5. **Idempotencia**: correr dos veces el mismo delta deja la base idéntica y
     reporta 0 filas nuevas.

No hace falta Postgres: `ingest_incremental` se prueba inyectando un lector
falso que filtra las filas por `fecha_corrida` igual que lo hace el `WHERE` del
cursor server-side.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from app.ingest import mapping as M
from app.ingest.incremental import (
    SINCE_DEDUCED,
    SINCE_EMPTY,
    SINCE_EXPLICIT,
    ingest_incremental,
    last_ingested_at,
    resolve_since,
)
from app.ingest.pricing_data import ingest_rows

# ============================================================
# Fixtures: capturas semanales del mismo par en varios retailers
# ============================================================

SEMANAS = ["2026-07-27", "2026-08-03", "2026-08-10"]
RETAILERS = ["Dexter", "StockCenter", "Solo Deportes"]

BASE_ROW = {
    "fecha_corrida": "2026-07-27",
    "scraper": "Dexter_AR",
    "canal": "Dexter",
    "marca": "Adidas",
    "style_color": "FQ8080-100",
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
    "competitor_full_price": 329999,
    "competitor_final_price": 296999,
    "cuotas_competitor": "6 cuotas sin interés",
    "nike_full_price": 319999,
    "nike_final_price": 319999,
    "cuotas_nike": "3 cuotas sin interés",
    "text_sizes_nike": "38 | 39 | 40 | 41 | 42 | 43 | 44 | 45",
    "text_sizes_competitor": "39 | 40 | 41 | 42 | 43 | 44",
    "precio_sugerido": 319999,
    "silueta": "RUNNING",
}


def row(**overrides):
    return {**BASE_ROW, **overrides}


def captura(fecha: str, *, precio: float = 296999) -> list[dict]:
    """Una captura semanal: el mismo par visto en cada retailer."""
    return [
        row(fecha_corrida=fecha, canal=retailer,
            scraper=f"{retailer.replace(' ', '')}_AR",
            competitor_final_price=precio)
        for retailer in RETAILERS
    ]


def historico() -> list[dict]:
    filas: list[dict] = []
    for i, fecha in enumerate(SEMANAS):
        filas.extend(captura(fecha, precio=296999 - 1000 * i))
    return filas


@pytest.fixture(autouse=True)
def _clean_config():
    M.reset_config_cache()
    yield
    M.reset_config_cache()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def snapshot(db_path):
    """Estado completo de lo que escribe la ingesta (para comparar corridas)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {
            "products": [dict(r) for r in conn.execute(
                "SELECT brand_id, country_code, product_name, sku, style_code, msrp "
                "FROM products ORDER BY product_name, sku")],
            "prices": [dict(r) for r in conn.execute(
                "SELECT product_id, retailer_id, observed_at, full_price, current_price "
                "FROM price_observations ORDER BY product_id, retailer_id, observed_at")],
            "stocks": [dict(r) for r in conn.execute(
                "SELECT product_id, retailer_id, observed_at, in_stock, sizes_available "
                "FROM stock_observations ORDER BY product_id, retailer_id, observed_at")],
            "retailers": [dict(r) for r in conn.execute(
                "SELECT name, country_code FROM retailers ORDER BY name, country_code")],
        }
    finally:
        conn.close()


class FakePostgres:
    """Reemplaza la lectura de Postgres: filtra en memoria como el WHERE del cursor."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.calls: list[dict] = []

    def __call__(self, dsn, db_path, *, country="AR", drop=True, since=None,
                 until=None, observations="replace", **kw):
        from app.ingest.pricing_data import ingest_rows as _ingest_rows

        self.calls.append({"dsn": dsn, "since": since, "country": country,
                           "drop": drop, "observations": observations})
        filas = [r for r in self.rows
                 if (since is None or str(r["fecha_corrida"]) >= since)
                 and (until is None or str(r["fecha_corrida"]) <= until)]
        return _ingest_rows(filas, db_path, country=country, drop=drop,
                            observations=observations)


@pytest.fixture
def fake_pg(monkeypatch):
    def _install(rows):
        fake = FakePostgres(rows)
        monkeypatch.setattr("app.ingest.incremental.ingest_from_postgres", fake)
        return fake
    return _install


# ============================================================
# 1. last_ingested_at
# ============================================================

def test_base_inexistente_no_tiene_fecha(tmp_path):
    assert last_ingested_at(tmp_path / "no-existe.db") is None


def test_base_vacia_no_tiene_fecha(tmp_path):
    from app.db import init_db

    db_path = tmp_path / "vacia.db"
    init_db(db_path, drop=True)
    assert last_ingested_at(db_path) is None
    assert last_ingested_at(db_path, country="AR") is None


def test_last_ingested_at_es_la_observacion_mas_reciente(tmp_path):
    db_path = tmp_path / "hist.db"
    ingest_rows(historico(), db_path, country="AR", drop=True)
    assert last_ingested_at(db_path) == date.fromisoformat(SEMANAS[-1])
    assert last_ingested_at(db_path, country="AR") == date.fromisoformat(SEMANAS[-1])


def test_last_ingested_at_filtra_por_pais(tmp_path):
    db_path = tmp_path / "paises.db"
    ingest_rows(historico(), db_path, country="AR", drop=True)
    # Nada cargado de CO: el `since` de CO no es el de AR.
    assert last_ingested_at(db_path, country="CO") is None
    assert last_ingested_at(db_path, country="ar") == date.fromisoformat(SEMANAS[-1])


def test_last_ingested_at_ve_stock_aunque_no_haya_precio(tmp_path):
    """Una captura con stock pero sin precio utilizable igual mueve la frontera."""
    db_path = tmp_path / "solo-stock.db"
    ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)
    sin_precio = [row(fecha_corrida=SEMANAS[1], canal=r,
                      scraper=f"{r.replace(' ', '')}_AR",
                      competitor_full_price=0, competitor_final_price=0,
                      nike_full_price=0, nike_final_price=0, precio_sugerido=0)
                  for r in RETAILERS]
    ingest_rows(sin_precio, db_path, country="AR", drop=False)

    conn = sqlite3.connect(db_path)
    assert scalar(conn, "SELECT MAX(observed_at) FROM price_observations") == SEMANAS[0]
    conn.close()
    assert last_ingested_at(db_path) == date.fromisoformat(SEMANAS[1])


# ============================================================
# 2. Deducción de `since`
# ============================================================

def test_since_explicito_manda(tmp_path):
    db_path = tmp_path / "hist.db"
    ingest_rows(historico(), db_path, country="AR", drop=True)
    desde, motivo = resolve_since(db_path, since="2026-01-01", country="AR")
    assert desde == date(2026, 1, 1) and motivo == SINCE_EXPLICIT


def test_since_se_deduce_de_lo_ya_cargado(tmp_path):
    db_path = tmp_path / "hist.db"
    ingest_rows(historico()[: len(RETAILERS) * 2], db_path, country="AR", drop=True)
    desde, motivo = resolve_since(db_path, country="AR")
    # Inclusive: la última captura puede haber quedado a medias.
    assert desde == date.fromisoformat(SEMANAS[1])
    assert motivo == SINCE_DEDUCED


def test_sin_base_el_since_es_none(tmp_path):
    desde, motivo = resolve_since(tmp_path / "nueva.db", country="AR")
    assert desde is None and motivo == SINCE_EMPTY


def test_since_invalido_es_un_error(tmp_path):
    with pytest.raises(ValueError):
        resolve_since(tmp_path / "x.db", since="la semana pasada")


def test_ingest_incremental_no_relee_todo(tmp_path, fake_pg):
    """La deducción tiene que traducirse en un WHERE, no en releer la tabla."""
    db_path = tmp_path / "delta.db"
    fake = fake_pg(historico())

    ingest_rows(historico()[: len(RETAILERS) * 2], db_path, country="AR", drop=True)
    ingest_incremental("postgresql://fake", db_path, country="AR")

    assert fake.calls[-1]["since"] == SEMANAS[1]    # no None: no se releyó todo
    assert fake.calls[-1]["drop"] is False
    assert fake.calls[-1]["observations"] == "append"


# ============================================================
# 3. Append-only: el histórico no se pisa
# ============================================================

def test_el_delta_no_duplica_observaciones(tmp_path, fake_pg):
    db_path = tmp_path / "append.db"
    fake_pg(historico())

    ingest_rows(historico()[: len(RETAILERS) * 2], db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)
    antes = scalar(conn, "SELECT COUNT(*) FROM price_observations")
    conn.close()

    summary = ingest_incremental("postgresql://fake", db_path, country="AR")

    conn = sqlite3.connect(db_path)
    duplicadas = scalar(conn, """
        SELECT COUNT(*) FROM (SELECT product_id, retailer_id, observed_at, COUNT(*) c
                              FROM price_observations GROUP BY 1,2,3 HAVING c > 1)""")
    total = scalar(conn, "SELECT COUNT(*) FROM price_observations")
    conn.close()

    assert duplicadas == 0
    assert total == antes + summary["price_observations"]
    # La captura releída (SEMANAS[1]) se saltea entera; sólo entra SEMANAS[2].
    assert summary["price_observations_skipped"] > 0
    assert summary["observations_skipped"] > 0
    assert summary["price_observations_replaced"] == 0


def test_el_historico_de_precios_no_se_pisa(tmp_path, fake_pg):
    """Si la fuente cambia un precio viejo, el append-only conserva el original."""
    db_path = tmp_path / "hist-inmutable.db"
    ingest_rows(captura(SEMANAS[0], precio=296999), db_path, country="AR", drop=True)

    conn = sqlite3.connect(db_path)
    original = scalar(conn, "SELECT current_price FROM price_observations "
                            "WHERE observed_at = ? AND current_price IS NOT NULL", (SEMANAS[0],))
    conn.close()

    # La misma fecha vuelve con otro precio + una captura nueva.
    fake_pg(captura(SEMANAS[0], precio=111111) + captura(SEMANAS[1], precio=250000))
    ingest_incremental("postgresql://fake", db_path, country="AR")

    conn = sqlite3.connect(db_path)
    ahora = scalar(conn, "SELECT current_price FROM price_observations "
                         "WHERE observed_at = ? AND current_price IS NOT NULL", (SEMANAS[0],))
    nuevas = scalar(conn, "SELECT COUNT(*) FROM price_observations WHERE observed_at = ?",
                    (SEMANAS[1],))
    conn.close()

    assert ahora == original            # el histórico es inmutable
    assert nuevas > 0                   # la captura nueva sí entró


def test_el_stock_tambien_es_append_only(tmp_path, fake_pg):
    db_path = tmp_path / "stock.db"
    ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)
    antes = scalar(conn, "SELECT COUNT(*) FROM stock_observations")
    conn.close()

    fake_pg(captura(SEMANAS[0]) + captura(SEMANAS[1]))
    summary = ingest_incremental("postgresql://fake", db_path, country="AR")

    conn = sqlite3.connect(db_path)
    duplicadas = scalar(conn, """
        SELECT COUNT(*) FROM (SELECT product_id, retailer_id, observed_at, COUNT(*) c
                              FROM stock_observations GROUP BY 1,2,3 HAVING c > 1)""")
    total = scalar(conn, "SELECT COUNT(*) FROM stock_observations")
    conn.close()

    assert duplicadas == 0
    assert summary["stock_observations_skipped"] == antes
    assert total == antes + summary["stock_observations"]


def test_modo_append_directo_sobre_ingest_rows(tmp_path):
    """El modo se puede usar sin Postgres: misma garantía."""
    db_path = tmp_path / "modo.db"
    primera = ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)
    segunda = ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=False,
                          observations="append")

    assert segunda["price_observations"] == 0
    assert segunda["price_observations_skipped"] == primera["price_observations"]
    assert segunda["price_observations_replaced"] == 0


def test_modo_de_observaciones_invalido(tmp_path):
    with pytest.raises(ValueError):
        ingest_rows(captura(SEMANAS[0]), tmp_path / "x.db", country="AR",
                    drop=True, observations="merge")


# ============================================================
# 4. UPSERT de productos
# ============================================================

def test_el_delta_no_duplica_productos(tmp_path, fake_pg):
    db_path = tmp_path / "upsert.db"
    ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)
    conn = sqlite3.connect(db_path)
    antes = scalar(conn, "SELECT COUNT(*) FROM products")
    conn.close()

    fake_pg(captura(SEMANAS[1]))
    summary = ingest_incremental("postgresql://fake", db_path, country="AR")

    conn = sqlite3.connect(db_path)
    assert scalar(conn, "SELECT COUNT(*) FROM products") == antes
    assert scalar(conn, "SELECT COUNT(*) FROM products WHERE sku = 'IE2960'") == 1
    conn.close()
    assert summary["products_inserted"] == 0
    assert summary["products_updated"] == antes


def test_un_sku_nuevo_en_el_delta_se_inserta(tmp_path, fake_pg):
    db_path = tmp_path / "nuevo-sku.db"
    ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)

    nuevo = [row(fecha_corrida=SEMANAS[1], canal="Dexter", scraper="Dexter_AR",
                 productcode_competitor="HQ3820", marca="Puma",
                 product_name_competitor="Puma Velocity Nitro 3")]
    fake_pg(captura(SEMANAS[1]) + nuevo)
    summary = ingest_incremental("postgresql://fake", db_path, country="AR")

    conn = sqlite3.connect(db_path)
    assert scalar(conn, "SELECT COUNT(*) FROM products WHERE sku = 'HQ3820'") == 1
    conn.close()
    assert summary["products_inserted"] == 1


def test_el_delta_no_pisa_lo_que_escribio_enrichment(tmp_path, fake_pg):
    from app.services.enrichment import run_enrichment

    db_path = tmp_path / "enrich.db"
    ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)
    run_enrichment(db_path=db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    antes = [dict(r) for r in conn.execute(
        "SELECT id, normalized_product_name, use_case, price_band, enrichment_version "
        "FROM products ORDER BY id")]
    conn.close()
    assert any(r["enrichment_version"] for r in antes)

    fake_pg(captura(SEMANAS[1]))
    ingest_incremental("postgresql://fake", db_path, country="AR")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    despues = [dict(r) for r in conn.execute(
        "SELECT id, normalized_product_name, use_case, price_band, enrichment_version "
        "FROM products ORDER BY id")]
    conn.close()
    assert despues == antes


# ============================================================
# 5. Idempotencia
# ============================================================

def test_correr_dos_veces_el_mismo_delta_no_cambia_nada(tmp_path, fake_pg):
    db_path = tmp_path / "idem.db"
    ingest_rows(historico()[: len(RETAILERS) * 2], db_path, country="AR", drop=True)

    fake_pg(historico())
    primera = ingest_incremental("postgresql://fake", db_path, country="AR")
    estado_1 = snapshot(db_path)
    segunda = ingest_incremental("postgresql://fake", db_path, country="AR")
    estado_2 = snapshot(db_path)

    assert estado_1 == estado_2
    assert primera["observations_inserted"] > 0
    # La segunda corrida lee de nuevo la última captura (el `since` ya avanzó) y
    # no escribe ni una fila: todo lo que lee ya estaba.
    assert segunda["observations_inserted"] == 0
    assert segunda["observations_skipped"] > 0
    assert segunda["products_inserted"] == 0


def test_cargar_semana_a_semana_da_lo_mismo_que_full(tmp_path, fake_pg):
    """El resultado del incremental es el mismo que el de una carga full."""
    incremental_db = tmp_path / "incremental.db"
    fake = fake_pg(historico())
    for semana in SEMANAS:
        fake.rows = [f for f in historico() if f["fecha_corrida"] <= semana]
        ingest_incremental("postgresql://fake", incremental_db, country="AR")

    full_db = tmp_path / "full.db"
    ingest_rows(historico(), full_db, country="AR", drop=True)

    assert snapshot(incremental_db) == snapshot(full_db)


def test_el_reporte_distingue_nuevas_de_salteadas(tmp_path, fake_pg):
    db_path = tmp_path / "reporte.db"
    ingest_rows(captura(SEMANAS[0]), db_path, country="AR", drop=True)

    fake_pg(captura(SEMANAS[0]) + captura(SEMANAS[1]))
    summary = ingest_incremental("postgresql://fake", db_path, country="AR")

    assert summary["incremental"] == 1
    assert summary["since_deduced"] == 1
    for key in ("products_inserted", "products_updated", "observations_inserted",
                "observations_skipped", "price_observations_skipped",
                "stock_observations_skipped"):
        assert key in summary and isinstance(summary[key], int)
    assert summary["observations_inserted"] > 0
    assert summary["observations_skipped"] > 0
