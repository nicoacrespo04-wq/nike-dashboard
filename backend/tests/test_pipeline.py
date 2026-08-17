"""Tests del orquestador del pipeline.

El caso que motivó este archivo es real y costó una corrida entera: con
`--keep` sobre una base cargada por `app.ingest`, la etapa `seed` llamaba a
`seed(drop=True)` y **borraba el archivo SQLite completo**, reemplazando 46.715
productos reales por los 45 del dataset de demostración. El pipeline seguía
en verde y todo el análisis posterior —matches, oportunidades, calibración—
salía de datos de mentira sin que nada lo advirtiera.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import pipeline
from app.db import get_conn, init_db, query


def _fake_ingested(db: Path, n: int = 3) -> None:
    """Base con datos 'reales' (no los del demo)."""
    init_db(db, drop=True)
    with get_conn(db) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1)")
        conn.executemany(
            "INSERT INTO products (id, brand_id, product_name, country_code) VALUES (?,1,?,'AR')",
            [(i, f"SKU INGERIDO {i}") for i in range(1, n + 1)],
        )


def test_keep_no_borra_los_datos_ingeridos(tmp_path):
    """`--keep` significa "no toques los datos que hay"."""
    db = tmp_path / "ingested.db"
    _fake_ingested(db)

    report = pipeline.run_all(db, reset=False)

    assert report["seed"]["status"] == "skipped"
    assert "--keep" in report["seed"]["reason"]

    nombres = [r["product_name"] for r in query("SELECT product_name FROM products", path=db)]
    assert nombres == ["SKU INGERIDO 1", "SKU INGERIDO 2", "SKU INGERIDO 3"]


def test_stage_seed_explicito_sigue_cargando_el_demo(tmp_path):
    """Pedir la etapa a mano sí carga el dataset de demostración."""
    db = tmp_path / "forzado.db"
    _fake_ingested(db)

    report = pipeline.run_all(db, reset=False, stages=["seed"])

    assert report["seed"]["status"] == "ok"
    total = query("SELECT COUNT(*) AS n FROM products", path=db)[0]["n"]
    assert total > 3, "con --stage seed explícito se espera el catálogo demo"


def test_run_completo_sigue_sembrando_el_demo(tmp_path):
    """Sin `--keep`, el flujo de demostración no cambia."""
    db = tmp_path / "demo.db"
    report = pipeline.run_all(db, reset=True)

    assert report["seed"]["status"] == "ok"
    assert report["seed"]["counts"]["products"] > 0
    assert report["matching"]["status"] == "ok"


def test_keep_recalcula_sobre_lo_que_hay(tmp_path):
    """Con `--keep` las etapas de cálculo corren igual, sobre los datos vivos."""
    db = tmp_path / "recalc.db"
    _fake_ingested(db)

    report = pipeline.run_all(db, reset=False)

    for stage in ("enrichment", "matching", "opportunities"):
        assert report[stage]["status"] == "ok", report[stage]
