"""Ingesta INCREMENTAL de `pricing_data`: cargar sólo el delta semanal.

Por qué existe
--------------
La carga full (``drop=True``) recrea la base en cada corrida. Con capturas
semanales eso tiene dos costos que no se pueden pagar en operación:

1. **Se pierde el histórico.** Las tendencias, el momentum y las señales de
   `share_of_shelf` se calculan sobre la serie de `price_observations` y
   `stock_observations`. Si cada lunes se borra todo y se recarga la última
   captura, el motor no tiene serie: tiene una foto.
2. **Se re-lee la tabla entera.** ~73.000 filas hoy, y crece una captura por
   semana. Leer todo para escribir el 15% nuevo es tiempo y transferencia
   tirados.

Este módulo resuelve las dos: deduce desde qué fecha hay que leer mirando lo
que YA está cargado y escribe las observaciones en modo **append-only**.

Contrato
--------
``last_ingested_at(db_path, country=None) -> date | None``
    Fecha de la observación más reciente ya cargada (``None`` si la base está
    vacía o no existe). Es el "dónde me quedé" de la carga incremental.

``ingest_incremental(dsn, db_path, since=None, country='AR') -> dict[str, int]``
    Lee de Postgres sólo ``fecha_corrida >= since`` y lo carga sin pisar nada:

    * **Productos**: UPSERT por identidad estable (``marca, país, style_code|sku``,
      ver `mapping.product_key`). Un SKU que ya existe se actualiza campo a campo
      con ``COALESCE`` — nunca se duplica ni se le borra lo que escribió
      `enrichment`.
    * **Observaciones**: append-only. La clave ``(producto, retailer, fecha)``
      que ya está en la base NO se toca; se cuenta como salteada. El histórico
      es inmutable.

    Con ``since=None`` la fecha se deduce de ``last_ingested_at``, y se relee
    **desde ese día inclusive** (``>=``, no ``>``): una captura puede haber
    quedado a medias — el scraper corre por retailer y puede fallar uno — y
    releer el último día es barato porque lo ya cargado se saltea. Esa es
    justamente la garantía que da el modo append-only.

Idempotencia
------------
Correr dos veces el mismo delta deja la base byte a byte igual: la segunda
corrida reporta ``observations_inserted = 0`` y todas salteadas.

CLI
---
    python -m app.ingest --dsn "$DATABASE_URL" --incremental
    python -m app.ingest --dsn "$DATABASE_URL" --incremental --since 2026-08-01
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import DB_PATH
from app.db import get_conn
from app.ingest.pricing_data import APPEND, ingest_from_postgres

log = logging.getLogger("app.ingest")

#: Tablas de observaciones cuya fecha define "hasta dónde llegó la ingesta".
_OBSERVATION_TABLES = ("price_observations", "stock_observations")

#: Motivos posibles de la fecha `since` resuelta (para el reporte).
SINCE_EXPLICIT = "argumento --since"
SINCE_DEDUCED = "deducido de lo ya cargado"
SINCE_EMPTY = "base vacía: se carga todo el histórico disponible"


# ============================================================
# Dónde me quedé
# ============================================================

def _as_date(value: Any) -> date | None:
    """``date`` / ``datetime`` / ``'YYYY-MM-DD'`` -> ``date``. ``None`` si no se puede."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def last_ingested_at(db_path: Path | str = DB_PATH, *,
                     country: str | None = None) -> date | None:
    """Fecha de la observación más reciente ya cargada. ``None`` si no hay ninguna.

    Args:
        db_path: base SQLite del motor.
        country: si se pasa, sólo mira las observaciones capturadas en retailers
            de ese país (la carga es país por país; el `since` de AR no tiene
            por qué ser el de CO).

    Mira `price_observations` **y** `stock_observations` porque una captura
    puede traer stock sin precio utilizable (o al revés): quedarse sólo con una
    de las dos haría releer de más o —peor— saltear un día real.
    """
    path = Path(db_path)
    if not path.exists():
        return None

    code = (country or "").strip().upper() or None
    best: date | None = None
    try:
        with get_conn(path) as conn:
            for table in _OBSERVATION_TABLES:
                sql = f"SELECT MAX(o.observed_at) AS last FROM {table} o"
                params: tuple = ()
                if code:
                    sql += (" JOIN retailers r ON r.id = o.retailer_id "
                            " WHERE UPPER(r.country_code) = ?")
                    params = (code,)
                row = conn.execute(sql, params).fetchone()
                found = _as_date(row["last"] if row else None)
                if found is not None and (best is None or found > best):
                    best = found
    except sqlite3.Error as exc:          # base a medio crear / esquema viejo
        log.debug("last_ingested_at: %s: %s", type(exc).__name__, exc)
        return None
    return best


def resolve_since(db_path: Path | str = DB_PATH, *, since: date | str | None = None,
                  country: str | None = "AR") -> tuple[date | None, str]:
    """Devuelve ``(fecha_desde, motivo)`` para una carga incremental.

    Con ``since`` explícito manda el argumento. Sin él se deduce de
    `last_ingested_at` (inclusive: se relee ese día). Si la base está vacía
    devuelve ``(None, ...)``: no hay delta que calcular, se carga todo.
    """
    explicit = _as_date(since)
    if since is not None and explicit is None:
        raise ValueError(f"--since no es una fecha ISO válida: {since!r}")
    if explicit is not None:
        return explicit, SINCE_EXPLICIT

    last = last_ingested_at(db_path, country=country)
    if last is None:
        return None, SINCE_EMPTY
    return last, SINCE_DEDUCED


# ============================================================
# Carga incremental
# ============================================================

def ingest_incremental(dsn: str, db_path: Path | str = DB_PATH, *,
                       since: date | None = None,
                       country: str = "AR") -> dict[str, int]:
    """Carga sólo el delta de `pricing_data` sin perder el histórico.

    Args:
        dsn: connection string de Postgres (``postgresql://...``).
        db_path: SQLite destino (se conserva; nunca se recrea).
        since: `fecha_corrida` mínima a leer. ``None`` la deduce de lo ya
            cargado (ver `resolve_since`).
        country: país a cargar.

    Returns:
        El resumen de `ingest_rows` más las claves del delta:

        * ``products_inserted`` / ``products_updated`` — productos nuevos vs.
          upserts sobre identidad ya conocida.
        * ``price_observations`` / ``stock_observations`` — filas NUEVAS
          insertadas (append-only).
        * ``price_observations_skipped`` / ``stock_observations_skipped`` — ya
          estaban: se saltean, el histórico no se pisa.
        * ``observations_inserted`` / ``observations_skipped`` — los totales.
        * ``incremental`` = 1 y ``since_deduced`` = 1 si la fecha se dedujo.
    """
    resolved, reason = resolve_since(db_path, since=since, country=country)
    log.info("── Ingesta incremental (%s) ── desde %s (%s)",
             country, resolved.isoformat() if resolved else "el principio", reason)

    summary = ingest_from_postgres(
        dsn, db_path, country=country, drop=False,
        since=resolved.isoformat() if resolved else None,
        observations=APPEND,
    )

    summary["incremental"] = 1
    summary["since_deduced"] = int(reason == SINCE_DEDUCED)
    summary["observations_inserted"] = (summary.get("price_observations", 0)
                                        + summary.get("stock_observations", 0))
    summary["observations_skipped"] = (summary.get("price_observations_skipped", 0)
                                       + summary.get("stock_observations_skipped", 0))

    log.info("  Delta: %s productos nuevos, %s actualizados; "
             "%s observaciones nuevas, %s ya existentes (salteadas)",
             f"{summary.get('products_inserted', 0):,}",
             f"{summary.get('products_updated', 0):,}",
             f"{summary['observations_inserted']:,}",
             f"{summary['observations_skipped']:,}")
    return summary
