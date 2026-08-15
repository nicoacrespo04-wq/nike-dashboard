"""Ingesta de datos reales: `pricing_data` (Postgres/Supabase) -> modelo normalizado.

Uso programático:

    from app.ingest import ingest_from_postgres, ingest_from_csv

    ingest_from_postgres(dsn, country="AR", limit=None, drop=True)
    ingest_from_csv("pricing_combinado.csv", country="AR", drop=False)

Uso por CLI:

    python -m app.ingest --dsn "$DATABASE_URL" --country AR [--limit N]
    python -m app.ingest --csv datos.csv --country AR --keep

`app.ingest.mapping` contiene el mapeo campo por campo como funciones puras
(testeable sin Postgres); `app.ingest.pricing_data` la carga y la deduplicación;
`app.ingest.retail_media` la lectura de `retail_media_search` (share of shelf).
"""

from app.ingest.mapping import (  # noqa: F401
    COMPETITOR,
    NIKE,
    NIKE_BRAND,
    ingest_config,
    map_brand,
    map_price_observation,
    map_product,
    map_retailer,
    map_stock_observation,
    product_key,
    sanitize_price,
)
from app.ingest.pricing_data import (  # noqa: F401
    ingest_from_csv,
    ingest_from_postgres,
    ingest_rows,
)

__all__ = [
    "COMPETITOR",
    "NIKE",
    "NIKE_BRAND",
    "ingest_config",
    "ingest_from_csv",
    "ingest_from_postgres",
    "ingest_rows",
    "map_brand",
    "map_price_observation",
    "map_product",
    "map_retailer",
    "map_stock_observation",
    "product_key",
    "sanitize_price",
]
