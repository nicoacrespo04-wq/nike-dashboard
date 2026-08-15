"""Carga del dataset demo desde ``backend/data/sample/*.csv`` a SQLite.

Reglas:
  * El orden de carga respeta las dependencias de claves foráneas.
  * Las columnas del CSV deben existir en la tabla destino (se validan contra
    ``PRAGMA table_info``); no hace falta que estén todas ni en el mismo orden.
  * Un campo vacío en el CSV se inserta como ``NULL`` — así el dataset puede
    dejar huecos deliberados (msrp, launch_date, description) y ejercitar la
    degradación elegante del scoring.
  * Idempotente: con ``drop=True`` recrea la base; con ``drop=False`` limpia las
    tablas del dataset antes de insertar.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, DB_PATH
from app.db import get_conn, init_db

SAMPLE_DIR = Path(DATA_DIR) / "sample"

#: Tablas del dataset demo en orden de carga (padres antes que hijos).
LOAD_ORDER: list[str] = [
    "brands",
    "countries",
    "retailers",
    "products",
    "price_observations",
    "stock_observations",
    "reviews",
    "editorial_mentions",
    "social_mention_aggregates",
]


def _table_types(conn, table: str) -> dict[str, str]:
    """Mapa columna -> tipo declarado, leído del esquema real."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"]: (row["type"] or "").upper() for row in rows}


def _coerce(value: str | None, declared_type: str) -> Any:
    """Convierte un campo del CSV al tipo de la columna. Vacío => ``None``."""
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    try:
        if "INT" in declared_type:
            return int(float(text))
        if "REAL" in declared_type or "FLOA" in declared_type or "DOUB" in declared_type:
            return float(text)
    except ValueError:
        # Valor no numérico en columna numérica: se deja tal cual y SQLite decide.
        return text
    return text


def _load_table(conn, table: str, csv_path: Path) -> int:
    """Inserta el CSV en la tabla y devuelve la cantidad de filas insertadas."""
    types = _table_types(conn, table)
    if not types:
        raise ValueError(f"La tabla '{table}' no existe en el esquema")

    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return 0
        header = [h.strip().lstrip("﻿") for h in header]

        unknown = [col for col in header if col not in types]
        if unknown:
            raise ValueError(
                f"{csv_path.name}: columnas inexistentes en '{table}': {unknown}"
            )

        col_types = [types[col] for col in header]
        placeholders = ", ".join("?" for _ in header)
        sql = (f"INSERT OR REPLACE INTO {table} ({', '.join(header)}) "
               f"VALUES ({placeholders})")

        inserted = 0
        batch: list[tuple[Any, ...]] = []
        for line_no, row in enumerate(reader, start=2):
            if not row or all(cell.strip() == "" for cell in row):
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"{csv_path.name}:{line_no}: {len(row)} campos, "
                    f"se esperaban {len(header)}"
                )
            batch.append(tuple(_coerce(cell, ctype) for cell, ctype in zip(row, col_types)))
            inserted += 1
        if batch:
            conn.executemany(sql, batch)
    return inserted


def seed(db_path=DB_PATH, *, drop: bool = True) -> dict[str, int]:
    """Crea el esquema y carga los CSV demo. Devuelve conteos por tabla.

    Args:
        db_path: ruta al archivo SQLite.
        drop: si es ``True`` recrea la base desde cero; si es ``False`` vacía
            sólo las tablas del dataset y vuelve a cargarlas.
    """
    init_db(db_path, drop=drop)

    counts: dict[str, int] = {}
    with get_conn(db_path) as conn:
        if not drop:
            # Limpieza en orden inverso para no violar claves foráneas.
            for table in reversed(LOAD_ORDER):
                conn.execute(f"DELETE FROM {table}")

        for table in LOAD_ORDER:
            csv_path = SAMPLE_DIR / f"{table}.csv"
            if not csv_path.exists():
                raise FileNotFoundError(f"Falta el CSV del dataset demo: {csv_path}")
            _load_table(conn, table, csv_path)

        for table in LOAD_ORDER:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        # Falla ruidosamente si algún FK quedó huérfano.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(f"Integridad referencial rota: {len(violations)} filas huérfanas")

    return counts


if __name__ == "__main__":  # pragma: no cover
    for name, total in seed().items():
        print(f"{name:32s} {total}")
