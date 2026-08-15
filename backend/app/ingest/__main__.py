"""CLI de ingesta.

    python -m app.ingest --dsn "$DATABASE_URL" --country AR [--limit N]
    python -m app.ingest --csv pricing_combinado.csv --country AR --keep
    python -m app.ingest --dsn "$DATABASE_URL" --since 2026-08-01 --until 2026-08-10

Con ``--keep`` la carga es incremental e idempotente (upsert por clave natural),
así se puede ingerir país por país o fecha por fecha sin perder lo ya cargado.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from app.config import DB_PATH
from app.ingest.pricing_data import ingest_from_csv, ingest_from_postgres

RESUMEN_ORDEN = (
    "rows_read", "rows_skipped_country", "rows_skipped_no_date",
    "sides_skipped_unidentified",
    "brands", "countries", "retailers", "retailers_non_retail_d2c",
    "products", "products_inserted", "products_updated",
    "products_nike", "products_competitor",
    "price_observations", "price_observations_deduplicated",
    "price_observations_replaced",
    "stock_observations", "stock_observations_deduplicated",
    "stock_observations_replaced", "availability_pct_derived",
    "prices_examined", "prices_valid", "prices_zero_discarded",
    "prices_fixed_by_cuotas", "prices_out_of_range_discarded",
    "prices_not_numeric_discarded", "price_rows_unusable", "seconds",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Ingesta de pricing_data (Postgres/Supabase o CSV) al modelo normalizado",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dsn", default=os.getenv("DATABASE_URL"),
                        help="Connection string de Postgres (default: $DATABASE_URL)")
    source.add_argument("--csv", help="Ruta a un CSV con el formato de pricing_data")
    parser.add_argument("--country", default="AR", help="País a cargar (default: AR)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Máximo de filas a leer (muestra más reciente)")
    parser.add_argument("--since", help="fecha_corrida >= YYYY-MM-DD")
    parser.add_argument("--until", help="fecha_corrida <= YYYY-MM-DD")
    parser.add_argument("--db", default=str(DB_PATH), help="Ruta del SQLite destino")
    parser.add_argument("--keep", action="store_true",
                        help="No recrear la base: carga incremental idempotente")
    parser.add_argument("--log-level", default="INFO",
                        help="DEBUG | INFO | WARNING (default: INFO)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO),
                        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    if not args.csv and not args.dsn:
        print("ERROR: falta --dsn (o $DATABASE_URL) o --csv", file=sys.stderr)
        return 2

    if args.csv:
        summary = ingest_from_csv(args.csv, args.db, country=args.country,
                                  limit=args.limit, drop=not args.keep)
    else:
        summary = ingest_from_postgres(args.dsn, args.db, country=args.country,
                                       limit=args.limit, drop=not args.keep,
                                       since=args.since, until=args.until)

    print()
    for key in RESUMEN_ORDEN:
        if key in summary:
            print(f"  {key:<34} {summary[key]:>12,}")
    for key in sorted(set(summary) - set(RESUMEN_ORDEN)):
        print(f"  {key:<34} {summary[key]:>12,}")
    print(f"\nBase: {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
