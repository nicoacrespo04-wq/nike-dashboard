"""Ingesta de `pricing_data` (PostgreSQL/Supabase o CSV) a la base normalizada.

Flujo:

    pricing_data (fila ancha)          modelo normalizado (SQLite)
    ─────────────────────────          ───────────────────────────
    1 fila = 1 comparación       ──▶   2 productos  (competidor + Nike)
      · competidor @ retailer          2 observaciones de precio
      · Nike de referencia             2 observaciones de stock
      · fecha de corrida               + brands / countries / retailers

Deduplicación (el mismo SKU aparece en N retailers y N fechas):
  * `products`            : clave natural ``(marca, país, código|nombre)``.
  * `price_observations`  : clave ``(producto, retailer, fecha)``.
  * `stock_observations`  : idem.
Sin esto se duplicaban productos y se contaban dos veces las observaciones.

Idempotencia:
  * ``drop=True``  recrea la base.
  * ``drop=False`` hace upsert: los productos ya existentes se actualizan sin
    pisar lo que escribió `enrichment`, y las observaciones de las combinaciones
    ``(producto, retailer, fecha)`` que se vuelven a cargar se reemplazan.
    Correr dos veces la misma ingesta deja exactamente los mismos conteos.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from app.config import DB_PATH
from app.db import get_conn, init_db
from app.ingest.mapping import (
    COMPETITOR,
    NIKE,
    canon_key,
    retailer_key,
    clean_text,
    country_meta,
    ingest_config,
    is_mappable_product,
    map_country,
    map_price_observation,
    map_product,
    map_stock_observation,
    norm_lower,
    price_limits,
    retailer_for_side,
    to_iso_date,
)

log = logging.getLogger("app.ingest")

#: Columnas de `pricing_data` que necesita el mapeo (no se traen las ~30 columnas
#: derivadas —gaps, BML, USD— porque el motor las recalcula desde el modelo).
PRICING_COLUMNS: tuple[str, ...] = (
    "id", "fecha_corrida", "scraper", "canal", "marca", "season",
    "style_color", "product_code_competitor", "marketing_name",
    "division", "category", "franchise_scrapper", "gender",
    "productcode_competitor", "product_name_competitor", "category_competitor",
    "division_competitor", "franchise_competitor", "gender_competitor",
    "size_available_competitor", "size_available_nike", "link_pdp_competitor",
    "competitor_full_price", "competitor_final_price", "cuotas_competitor",
    "nike_full_price", "nike_final_price", "cuotas_nike",
    "text_sizes_nike", "text_sizes_competitor", "pdp_nike",
    "precio_sugerido", "silueta",
)

#: Columnas de `products` que escribe la ingesta. Las que faltan
#: (`normalized_product_name`, `use_case`, `price_band`, `lifecycle_stage`,
#: `enrichment_version`) son de `enrichment` y no se tocan.
PRODUCT_COLUMNS: tuple[str, ...] = (
    "brand_id", "country_code", "product_name", "franchise", "model", "version",
    "sku", "style_code", "launch_date", "category", "subcategory", "sport",
    "activity", "gender", "age_segment", "performance_vs_lifestyle",
    "consumer_segment", "msrp", "url", "image_url", "description",
)

#: Prioridad de la fuente de una observación: lo capturado directamente en el
#: retailer (bloque competidor) manda sobre el bloque Nike de referencia.
_SIDE_PRIORITY = {COMPETITOR: 2, NIKE: 1}


# ============================================================
# Acumulador (deduplica en memoria antes de escribir)
# ============================================================

class _Accumulator:
    """Junta y deduplica lo leído de `pricing_data` antes de persistirlo."""

    def __init__(self, country: str | None = "AR") -> None:
        self.country = (country or "").upper() or None
        self.brands: dict[str, int] = {}                      # nombre -> is_focus
        self.countries: set[str] = set()
        self.retailers: dict[str, dict[str, Any]] = {}
        self.products: dict[tuple, dict[str, Any]] = {}
        self.prices: dict[tuple, dict[str, Any]] = {}
        self.stocks: dict[tuple, dict[str, Any]] = {}
        self.size_grid: dict[tuple, set[str]] = {}
        self.stats: dict[str, int] = {
            "rows_read": 0,
            "rows_skipped_country": 0,
            "rows_skipped_no_date": 0,
            "sides_skipped_unidentified": 0,
            "prices_examined": 0,
            "prices_valid": 0,
            "prices_zero_discarded": 0,
            "prices_out_of_range_discarded": 0,
            "prices_not_numeric_discarded": 0,
            "prices_fixed_by_cuotas": 0,
            "price_rows_unusable": 0,
            "price_observations_deduplicated": 0,
            "stock_observations_deduplicated": 0,
        }

    # ── ingesta de una fila ──────────────────────────────────
    def feed(self, row: dict[str, Any]) -> None:
        self.stats["rows_read"] += 1

        country = map_country(row, self.country)
        if self.country and country != self.country:
            self.stats["rows_skipped_country"] += 1
            return

        observed_at = to_iso_date(row.get("fecha_corrida"))
        if observed_at is None:
            self.stats["rows_skipped_no_date"] += 1

        self.countries.add(country)

        for side in (COMPETITOR, NIKE):
            product = map_product(row, side, country=country)
            if not is_mappable_product(product):
                self.stats["sides_skipped_unidentified"] += 1
                continue

            key = product["key"]
            self._merge_product(key, product)
            self.brands.setdefault(product["brand"], 0)
            self.brands[product["brand"]] |= int(product["is_focus"])

            retailer = retailer_for_side(row, side, country)
            self._register_retailer(retailer)

            if observed_at is None:
                continue

            # `product` y `retailer` ya están calculados: se reutilizan para no
            # remapear la misma fila en cada observación.
            self._feed_price(row, side, country, product, retailer, observed_at)
            self._feed_stock(row, side, country, product, retailer, observed_at)

    # ── productos ────────────────────────────────────────────
    def _merge_product(self, key: tuple, incoming: dict[str, Any]) -> None:
        """Fusiona filas del mismo SKU: gana el valor no nulo más reciente."""
        current = self.products.get(key)
        if current is None:
            incoming = dict(incoming)
            incoming["_rows"] = 1
            self.products[key] = incoming
            return

        current["_rows"] += 1
        newer = (incoming.get("observed_at") or "") >= (current.get("observed_at") or "")
        for column, value in incoming.items():
            if column in ("key", "_rows", "side"):
                continue
            if value is None:
                continue
            if newer or current.get(column) is None:
                current[column] = value
        current["is_focus"] = max(int(current.get("is_focus") or 0), int(incoming["is_focus"]))
        if newer:
            current["observed_at"] = incoming.get("observed_at") or current.get("observed_at")

    def _register_retailer(self, retailer: dict[str, Any]) -> None:
        stored = self.retailers.get(retailer["key"])
        if stored is None:
            self.retailers[retailer["key"]] = dict(retailer)
            self.countries.add(retailer["country_code"])
        elif retailer["is_retailer"] and not stored["is_retailer"]:
            stored.update(retailer)

    # ── precios ──────────────────────────────────────────────
    def _count_price_flags(self, flags: dict[str, str], values: Iterable[Any]) -> None:
        counters = {
            "zero": "prices_zero_discarded",
            "out_of_range": "prices_out_of_range_discarded",
            "not_numeric": "prices_not_numeric_discarded",
            "cuotas": "prices_fixed_by_cuotas",
        }
        for reason in flags.values():
            self.stats[counters[reason]] += 1
        for value in values:
            if value is not None:
                self.stats["prices_valid"] += 1

    def _feed_price(self, row: dict[str, Any], side: str, country: str,
                    product: dict[str, Any], retailer: dict[str, Any],
                    observed_at: str) -> None:
        pkey, rkey = product["key"], retailer["key"]
        obs = map_price_observation(row, side, country=country,
                                    product=product, retailer=retailer)
        self.stats["prices_examined"] += 2          # full price + final price
        self._count_price_flags(obs["flags"], (obs["full_price"], obs["current_price"]))

        if not obs["usable"]:
            self.stats["price_rows_unusable"] += 1
            return

        key = (pkey, rkey, observed_at)
        record = {
            "product_key": pkey,
            "retailer_key": rkey,
            "observed_at": observed_at,
            "full_price": obs["full_price"],
            "current_price": obs["current_price"],
            "discount_pct": obs["discount_pct"],
            "currency": obs["currency"],
            "priority": _SIDE_PRIORITY[side],
        }
        stored = self.prices.get(key)
        if stored is None:
            self.prices[key] = record
            return

        self.stats["price_observations_deduplicated"] += 1
        self._merge_observation(stored, record,
                                ("full_price", "current_price", "discount_pct", "currency"))

    # ── stock ────────────────────────────────────────────────
    def _feed_stock(self, row: dict[str, Any], side: str, country: str,
                    product: dict[str, Any], retailer: dict[str, Any],
                    observed_at: str) -> None:
        pkey, rkey = product["key"], retailer["key"]
        obs = map_stock_observation(row, side, country=country,
                                    product=product, retailer=retailer)
        if obs["size_tokens"]:
            self.size_grid.setdefault(pkey, set()).update(obs["size_tokens"])
        if not obs["usable"]:
            return

        key = (pkey, rkey, observed_at)
        record = {
            "product_key": pkey,
            "retailer_key": rkey,
            "observed_at": observed_at,
            "in_stock": obs["in_stock"],
            "sizes_available": obs["sizes_available"],
            "sizes_total": None,
            "availability_pct": None,
            "priority": _SIDE_PRIORITY[side],
        }
        stored = self.stocks.get(key)
        if stored is None:
            self.stocks[key] = record
            return

        self.stats["stock_observations_deduplicated"] += 1
        self._merge_observation(stored, record, ("in_stock", "sizes_available"))

    @staticmethod
    def _merge_observation(stored: dict[str, Any], incoming: dict[str, Any],
                           columns: tuple[str, ...]) -> None:
        """Gana la fuente de mayor prioridad; los huecos se completan igual."""
        if incoming["priority"] > stored["priority"]:
            for column in columns:
                if incoming[column] is not None or stored.get(column) is None:
                    stored[column] = incoming[column]
            stored["priority"] = incoming["priority"]
            return
        for column in columns:
            if stored.get(column) is None and incoming.get(column) is not None:
                stored[column] = incoming[column]

    # ── grid de talles ───────────────────────────────────────
    def apply_size_grid(self) -> int:
        """Deriva `sizes_total`/`availability_pct` sólo si el grid es inferible.

        El grid del producto es la unión de los talles vistos en
        `text_sizes_*`. Si esa columna no vino, no hay denominador y ambos
        campos quedan en NULL (no se inventa un total).
        """
        if not ingest_config().get("derive_availability_pct", True):
            return 0

        derived = 0
        for record in self.stocks.values():
            grid = self.size_grid.get(record["product_key"])
            available = record["sizes_available"]
            if not grid or available is None:
                continue
            total = len(grid)
            if total <= 0 or available > total:
                continue                      # grid incompleto: no se fuerza
            record["sizes_total"] = total
            record["availability_pct"] = round(100.0 * available / total, 2)
            derived += 1
        return derived


# ============================================================
# Persistencia
# ============================================================

def _db_product_key(row: dict[str, Any], brand_name: str) -> tuple[str, str, str]:
    """Clave natural de un producto ya persistido (misma fórmula que el mapeo)."""
    code = canon_key(row.get("sku")) or canon_key(row.get("style_code"))
    if not code:
        code = "name:" + (norm_lower(row.get("product_name")) or "")
    return (brand_name.upper(), str(row.get("country_code") or "").upper(), code)


def _write(acc: _Accumulator, db_path: Path | str, *, drop: bool) -> dict[str, int]:
    """Persiste el acumulador. Devuelve conteos de escritura."""
    counts = {
        "brands": 0, "countries": 0, "retailers": 0, "retailers_non_retail_d2c": 0,
        "products": 0, "products_inserted": 0, "products_updated": 0,
        "products_nike": 0, "products_competitor": 0,
        "price_observations": 0, "price_observations_replaced": 0,
        "stock_observations": 0, "stock_observations_replaced": 0,
        "availability_pct_derived": 0,
    }
    counts["availability_pct_derived"] = acc.apply_size_grid()

    init_db(db_path, drop=drop)

    with get_conn(db_path) as conn:
        # ── países ───────────────────────────────────────────
        for code in sorted(acc.countries):
            meta = country_meta(code)
            conn.execute(
                "INSERT INTO countries (code, name, currency) VALUES (?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET name = excluded.name, "
                "currency = COALESCE(excluded.currency, countries.currency)",
                (meta["code"], meta["name"], meta["currency"] or None),
            )
        counts["countries"] = len(acc.countries)

        # ── marcas ───────────────────────────────────────────
        existing_brands = {str(row["name"]).upper(): str(row["name"])
                           for row in conn.execute("SELECT name FROM brands").fetchall()}
        for name, is_focus in sorted(acc.brands.items()):
            stored = existing_brands.get(name.upper(), name)
            conn.execute(
                "INSERT INTO brands (name, is_focus) VALUES (?,?) "
                "ON CONFLICT(name) DO UPDATE SET is_focus = MAX(brands.is_focus, excluded.is_focus)",
                (stored, int(is_focus)),
            )
        counts["brands"] = len(acc.brands)
        all_brands = {str(row["name"]): int(row["id"])
                      for row in conn.execute("SELECT id, name FROM brands").fetchall()}
        # La clave del mapeo es UPPER; la fila puede estar guardada con otro casing.
        brand_ids = {name.upper(): bid for name, bid in all_brands.items()}

        # ── retailers ────────────────────────────────────────
        for retailer in sorted(acc.retailers.values(), key=lambda r: (r["name"], r["country_code"])):
            conn.execute(
                "INSERT INTO retailers (name, country_code, channel, importance) VALUES (?,?,?,?) "
                "ON CONFLICT(name, country_code) DO UPDATE SET channel = excluded.channel, "
                "importance = excluded.importance",
                (retailer["name"], retailer["country_code"], retailer["channel"],
                 float(retailer["importance"])),
            )
            if not retailer["is_retailer"]:
                counts["retailers_non_retail_d2c"] += 1
        counts["retailers"] = len(acc.retailers)

        retailer_id_by_key = {
            retailer_key(row["name"], row["country_code"]): int(row["id"])
            for row in conn.execute("SELECT id, name, country_code FROM retailers").fetchall()
        }

        # ── productos ────────────────────────────────────────
        brand_name_by_id = {bid: name for name, bid in brand_ids.items()}
        existing: dict[tuple, int] = {}
        for row in conn.execute(
                "SELECT id, brand_id, country_code, sku, style_code, product_name "
                "FROM products").fetchall():
            brand_name = brand_name_by_id.get(int(row["brand_id"]))
            if brand_name is None:
                continue
            existing[_db_product_key(dict(row), brand_name)] = int(row["id"])

        product_ids: dict[tuple, int] = {}
        insert_sql = (f"INSERT INTO products ({', '.join(PRODUCT_COLUMNS)}) "
                      f"VALUES ({', '.join('?' for _ in PRODUCT_COLUMNS)})")
        update_sql = ("UPDATE products SET "
                      + ", ".join(f"{col} = COALESCE(?, {col})" for col in PRODUCT_COLUMNS[2:])
                      + ", updated_at = datetime('now') WHERE id = ?")

        for key, product in sorted(acc.products.items()):
            brand_id = brand_ids[product["brand"].upper()]
            values = [brand_id, product["country_code"]] + [
                product.get(col) for col in PRODUCT_COLUMNS[2:]]
            product_id = existing.get(key)
            if product_id is None:
                cursor = conn.execute(insert_sql, values)
                product_id = int(cursor.lastrowid)
                counts["products_inserted"] += 1
            else:
                conn.execute(update_sql, values[2:] + [product_id])
                counts["products_updated"] += 1
            product_ids[key] = product_id
            if product["is_focus"]:
                counts["products_nike"] += 1
            else:
                counts["products_competitor"] += 1
        counts["products"] = len(product_ids)

        # ── observaciones (reemplazo idempotente por clave) ──
        counts["price_observations"], counts["price_observations_replaced"] = _write_observations(
            conn, "price_observations",
            ("full_price", "current_price", "discount_pct", "currency"),
            acc.prices.values(), product_ids, retailer_id_by_key,
        )
        counts["stock_observations"], counts["stock_observations_replaced"] = _write_observations(
            conn, "stock_observations",
            ("in_stock", "availability_pct", "sizes_available", "sizes_total"),
            acc.stocks.values(), product_ids, retailer_id_by_key,
        )

    return counts


def _write_observations(conn, table: str, columns: tuple[str, ...],
                        records: Iterable[dict[str, Any]],
                        product_ids: dict[tuple, int],
                        retailer_ids: dict[str, int]) -> tuple[int, int]:
    """Borra las claves que se recargan y reinserta. Devuelve `(insertadas, borradas)`."""
    rows: list[tuple] = []
    keys: list[tuple] = []
    for record in records:
        product_id = product_ids.get(record["product_key"])
        retailer_id = retailer_ids.get(record["retailer_key"])
        if product_id is None or retailer_id is None:
            continue
        keys.append((product_id, retailer_id, record["observed_at"]))
        rows.append((product_id, retailer_id, record["observed_at"],
                     *(record.get(col) for col in columns)))

    if not rows:
        return 0, 0

    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _ingest_keys "
                 "(product_id INTEGER, retailer_id INTEGER, observed_at TEXT)")
    conn.execute("DELETE FROM _ingest_keys")
    conn.executemany("INSERT INTO _ingest_keys VALUES (?,?,?)", keys)
    deleted = conn.execute(
        f"DELETE FROM {table} WHERE EXISTS ("
        "  SELECT 1 FROM _ingest_keys k"
        f"  WHERE k.product_id = {table}.product_id"
        f"    AND k.retailer_id = {table}.retailer_id"
        f"    AND k.observed_at = {table}.observed_at)"
    ).rowcount or 0

    all_columns = ("product_id", "retailer_id", "observed_at", *columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(all_columns)}) "
        f"VALUES ({', '.join('?' for _ in all_columns)})",
        rows,
    )
    conn.execute("DELETE FROM _ingest_keys")
    return len(rows), int(deleted)


# ============================================================
# API pública
# ============================================================

def ingest_rows(rows: Iterable[dict[str, Any]], db_path: Path | str = DB_PATH, *,
                country: str | None = "AR", limit: int | None = None,
                drop: bool = True) -> dict[str, int]:
    """Ingesta desde cualquier iterable de filas con columnas de `pricing_data`."""
    started = time.perf_counter()
    acc = _Accumulator(country)
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        acc.feed(row)

    summary = dict(acc.stats)
    summary.update(_write(acc, db_path, drop=drop))
    summary["seconds"] = int(round(time.perf_counter() - started))
    log_summary(summary, country=country)
    return summary


def ingest_from_postgres(dsn: str, db_path: Path | str = DB_PATH, *, country: str = "AR",
                         limit: int | None = None, drop: bool = True,
                         since: str | None = None, until: str | None = None,
                         batch_size: int = 5000) -> dict[str, int]:
    """Lee `pricing_data` de Postgres (Supabase) y la carga normalizada.

    Args:
        dsn: connection string (``postgresql://...``).
        db_path: SQLite destino.
        country: país a cargar (``'AR'``). Filtra por el scraper de origen.
        limit: máximo de filas a leer (muestra: las más recientes).
        drop: ``True`` recrea la base; ``False`` hace upsert idempotente.
        since / until: rango de `fecha_corrida` (ISO), para cargas parciales.
        batch_size: tamaño del cursor server-side.
    """
    rows = _iter_postgres(dsn, country=country, limit=limit, since=since,
                          until=until, batch_size=batch_size)
    return ingest_rows(rows, db_path, country=country, drop=drop)


def ingest_from_csv(csv_path: str | Path, db_path: Path | str = DB_PATH,
                    **kw: Any) -> dict[str, int]:
    """Igual que `ingest_from_postgres` pero desde un CSV con el mismo formato.

    Acepta los encabezados del CSV original (``Competitor_Final_Price``) y los
    nombres de columna de la tabla (``competitor_final_price``).
    """
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [_normalize_csv_row(row) for row in reader]
    return ingest_rows(rows, db_path, **kw)


def _normalize_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    """Encabezado del CSV -> nombre de columna de `pricing_data`."""
    out: dict[str, Any] = {}
    for header, value in row.items():
        if header is None:
            continue
        column = header.strip().lstrip("﻿").strip("_").lower()
        out[column] = clean_text(value)
    return out


# ============================================================
# Lectura de Postgres
# ============================================================

def _connect(dsn: str):
    try:
        import psycopg2  # noqa: PLC0415 - dependencia opcional
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise RuntimeError(
            "psycopg2 no está instalado. Instalalo con `pip install psycopg2-binary` "
            "(es una dependencia opcional: sólo hace falta para leer de Postgres)."
        ) from exc
    sslmode = "require" if "supabase.co" in dsn else "prefer"
    return psycopg2.connect(dsn, sslmode=sslmode)


def build_query(country: str | None = "AR", *, limit: int | None = None,
                since: str | None = None, until: str | None = None) -> tuple[str, list[Any]]:
    """SQL + params para leer `pricing_data`. Prefiltra por país y por fecha."""
    where: list[str] = []
    params: list[Any] = []

    if country:
        foreign = sorted({
            canon_key(key)
            for key, site in ingest_config()["brand_site_scrapers"].items()
            if str(site.get("country_code", "")).upper() != country.upper()
        })
        if foreign:
            canon_sql = "regexp_replace(lower(coalesce({col}, '')), '[^a-z0-9]', '', 'g')"
            where.append(f"{canon_sql.format(col='scraper')} <> ALL(%s)")
            params.append(foreign)
            where.append(f"{canon_sql.format(col='canal')} <> ALL(%s)")
            params.append(foreign)

    if since:
        where.append("fecha_corrida >= %s")
        params.append(since)
    if until:
        where.append("fecha_corrida <= %s")
        params.append(until)

    sql = f"SELECT {', '.join(PRICING_COLUMNS)} FROM pricing_data"
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Con `limit` se toma la muestra más reciente; sin él, orden cronológico
    # (el merge de productos usa la fecha, así que el orden sólo desempata).
    sql += " ORDER BY fecha_corrida DESC NULLS LAST, id DESC" if limit else \
           " ORDER BY fecha_corrida ASC NULLS LAST, id ASC"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    return sql, params


def _iter_postgres(dsn: str, *, country: str | None, limit: int | None,
                   since: str | None, until: str | None,
                   batch_size: int) -> Iterator[dict[str, Any]]:
    """Cursor server-side: no carga la tabla entera en memoria."""
    import psycopg2.extras  # noqa: PLC0415

    sql, params = build_query(country, limit=limit, since=since, until=until)
    connection = _connect(dsn)
    try:
        cursor = connection.cursor(name="ingest_pricing_data",
                                   cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.itersize = batch_size
        cursor.execute(sql, params)
        for row in cursor:
            yield dict(row)
        cursor.close()
    finally:
        connection.close()


# ============================================================
# Reporte
# ============================================================

def log_summary(summary: dict[str, int], *, country: str | None = None) -> None:
    """Resumen de negocio: qué entró, qué se dedupló y qué precio se descartó."""
    minimum, maximum, max_cuotas = price_limits()
    log.info("── Ingesta pricing_data%s ──", f" ({country})" if country else "")
    log.info("  Filas leídas                 : %s", f"{summary.get('rows_read', 0):,}")
    log.info("  Filas de otro país (omitidas): %s", f"{summary.get('rows_skipped_country', 0):,}")
    log.info("  Filas sin fecha de corrida   : %s", f"{summary.get('rows_skipped_no_date', 0):,}")
    log.info("  Lados sin identificar        : %s",
             f"{summary.get('sides_skipped_unidentified', 0):,}")
    log.info("  Marcas / países / retailers  : %s / %s / %s  (D2C no-retail: %s)",
             summary.get("brands", 0), summary.get("countries", 0),
             summary.get("retailers", 0), summary.get("retailers_non_retail_d2c", 0))
    log.info("  Productos únicos             : %s  (nuevos %s, actualizados %s)",
             f"{summary.get('products', 0):,}", f"{summary.get('products_inserted', 0):,}",
             f"{summary.get('products_updated', 0):,}")
    log.info("    · Nike / competencia       : %s / %s",
             f"{summary.get('products_nike', 0):,}", f"{summary.get('products_competitor', 0):,}")
    log.info("  Observaciones de precio      : %s  (deduplicadas %s, reemplazadas %s)",
             f"{summary.get('price_observations', 0):,}",
             f"{summary.get('price_observations_deduplicated', 0):,}",
             f"{summary.get('price_observations_replaced', 0):,}")
    log.info("  Observaciones de stock       : %s  (deduplicadas %s, reemplazadas %s)",
             f"{summary.get('stock_observations', 0):,}",
             f"{summary.get('stock_observations_deduplicated', 0):,}",
             f"{summary.get('stock_observations_replaced', 0):,}")
    log.info("    · availability_pct derivada: %s",
             f"{summary.get('availability_pct_derived', 0):,}")
    log.info("  ── Saneamiento de precios (rango [%s, %s] ARS, cuotas máx %s) ──",
             f"{minimum:,.0f}", f"{maximum:,.0f}", max_cuotas)
    log.info("    Precios examinados         : %s", f"{summary.get('prices_examined', 0):,}")
    log.info("    Válidos                    : %s", f"{summary.get('prices_valid', 0):,}")
    log.info("    <= 0 → descartados         : %s", f"{summary.get('prices_zero_discarded', 0):,}")
    log.info("    Corregidos por cuotas (/N) : %s", f"{summary.get('prices_fixed_by_cuotas', 0):,}")
    log.info("    Fuera de rango → descartados: %s",
             f"{summary.get('prices_out_of_range_discarded', 0):,}")
    log.info("    No numéricos → descartados : %s",
             f"{summary.get('prices_not_numeric_discarded', 0):,}")
    log.info("    Filas sin ningún precio    : %s", f"{summary.get('price_rows_unusable', 0):,}")
