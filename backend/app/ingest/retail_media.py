"""Lectura de `retail_media_search` (share of shelf por término de búsqueda).

`retail_media_search` mide la **visibilidad** de Nike/Adidas/Puma en el buscador
de cada retailer: `nike_visibility` es 0..1 según la posición relativa del
primer resultado de esa marca para ese término (1 = mejor posición posible).

Este módulo **lee y agrega**, pero NO escribe en `market_signals`: esa tabla es
del módulo de señales (`app.services.shelf` / `brand_intelligence`). Acá sólo se
dejan listas las funciones para que ese módulo las consuma cuando corresponda.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from app.ingest.mapping import (
    NIKE_BRAND,
    clean_text,
    map_brand,
    map_retailer,
    to_iso_date,
    to_number,
)

#: Columnas de `retail_media_search` que se usan.
SHELF_COLUMNS: tuple[str, ...] = (
    "fecha_corrida", "scraper", "canal", "marca", "season", "search_term",
    "nike_visibility", "type", "category", "level_1", "level_2", "level_3", "division",
)

SIGNAL_TYPE = "share_of_shelf"


def map_shelf_row(row: dict[str, Any], *, default_country: str = "AR") -> dict[str, Any]:
    """Fila de `retail_media_search` -> registro normalizado (función pura)."""
    retailer = map_retailer(row, default_country)
    return {
        "observed_at": to_iso_date(row.get("fecha_corrida")),
        "retailer_key": retailer["key"],
        "retailer_name": retailer["name"],
        "country_code": retailer["country_code"],
        "brand": map_brand(row),
        "search_term": clean_text(row.get("search_term")),
        "visibility": to_number(row.get("nike_visibility")),
        "category": clean_text(row.get("category")),
        "division": clean_text(row.get("division")),
        "season": clean_text(row.get("season")),
    }


def shelf_visibility_signals(rows: Iterable[dict[str, Any]], *,
                             default_country: str = "AR") -> list[dict[str, Any]]:
    """Agrega visibilidad a share of shelf por `(retailer, fecha)`.

    Devuelve filas con la forma de `market_signals` (``signal_type``,
    ``entity_type``, ``entity_id``, ``country_code``, ``value``, ``period_*``)
    **sin persistirlas**. `value` es el share de visibilidad de Nike:
    visibilidad Nike / visibilidad total de las marcas medidas, en %.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        record = map_shelf_row(raw, default_country=default_country)
        if record["observed_at"] is None or record["visibility"] is None:
            continue
        key = (record["retailer_key"], record["observed_at"])
        bucket = buckets.setdefault(key, {
            "retailer_key": record["retailer_key"],
            "retailer_name": record["retailer_name"],
            "country_code": record["country_code"],
            "observed_at": record["observed_at"],
            "nike_visibility": 0.0,
            "total_visibility": 0.0,
            "search_terms": set(),
            "brands": set(),
        })
        bucket["total_visibility"] += float(record["visibility"])
        if record["brand"] == NIKE_BRAND:
            bucket["nike_visibility"] += float(record["visibility"])
        bucket["brands"].add(record["brand"])
        if record["search_term"]:
            bucket["search_terms"].add(record["search_term"])

    signals: list[dict[str, Any]] = []
    for bucket in sorted(buckets.values(), key=lambda b: (b["retailer_key"], b["observed_at"])):
        total = bucket["total_visibility"]
        signals.append({
            "signal_type": SIGNAL_TYPE,
            "entity_type": "retailer",
            "entity_id": bucket["retailer_name"],
            "country_code": bucket["country_code"],
            "value": round(100.0 * bucket["nike_visibility"] / total, 3) if total > 0 else None,
            "period_start": bucket["observed_at"],
            "period_end": bucket["observed_at"],
            "search_terms": len(bucket["search_terms"]),
            "brands": sorted(bucket["brands"]),
        })
    return signals


def _shelf_column(header: str | None) -> str:
    """``'Level _1'`` -> ``'level_1'``; ``'Fecha_Corrida'`` -> ``'fecha_corrida'``."""
    return (header or "").strip().lstrip("﻿").lower().replace(" ", "")


def read_shelf_csv(csv_path: str | Path) -> list[dict[str, Any]]:
    """Lee el CSV de `retail_media_search` (encabezados originales o de tabla)."""
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        return [{_shelf_column(header): value for header, value in row.items()}
                for row in csv.DictReader(handle)]


def fetch_shelf_rows(dsn: str, *, country: str = "AR", limit: int | None = None,
                     since: str | None = None, until: str | None = None) -> Iterator[dict[str, Any]]:
    """Lee `retail_media_search` de Postgres. No escribe nada."""
    from app.ingest.pricing_data import _connect  # noqa: PLC0415 - dependencia opcional

    import psycopg2.extras  # noqa: PLC0415

    where: list[str] = []
    params: list[Any] = []
    if since:
        where.append("fecha_corrida >= %s")
        params.append(since)
    if until:
        where.append("fecha_corrida <= %s")
        params.append(until)

    sql = f"SELECT {', '.join(SHELF_COLUMNS)} FROM retail_media_search"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fecha_corrida ASC NULLS LAST, id ASC"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))

    connection = _connect(dsn)
    try:
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(sql, params)
        for row in cursor.fetchall():
            yield dict(row)
        cursor.close()
    finally:
        connection.close()
