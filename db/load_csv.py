#!/usr/bin/env python3
"""
load_csv.py — Carga pricing_combinado_*.csv a PostgreSQL (Supabase o local)

Uso:
    python load_csv.py                          # usa path hardcodeado
    python load_csv.py ruta/al/archivo.csv      # path manual
    DATABASE_URL=postgresql://... python load_csv.py

Variables de entorno:
    DATABASE_URL   — connection string de PostgreSQL (requerido)
    CSV_PATH       — path al CSV (opcional, usa default si no se setea)
    BATCH_SIZE     — filas por batch (default: 500)
    TRUNCATE       — si "true", trunca la tabla antes de insertar

    ── Sanitización de precios (ver bloque "SANITIZACIÓN DE PRECIOS") ──
    PRICE_SANITIZE            — "false" para desactivarla (default: true)
    PRICE_MIN_ARS             — piso plausible en ARS (default: 1000)
    PRICE_MAX_ARS             — techo plausible en ARS (default: 2000000)
    PRICE_MAX_CUOTAS          — máx. de cuotas parseables (default: 24)
    PRICE_INVALIDATE_DERIVED  — "false" para NO anular gaps/BML/USD de las
                                filas cuyo precio fue corregido (default: true)
"""
import csv
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 no instalado. Corré: pip install psycopg2-binary")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────
DEFAULT_CSV = str(Path(__file__).parent.parent / "pricing_combinado_20260810_093113.csv")
CSV_PATH    = sys.argv[1] if len(sys.argv) > 1 else os.getenv("CSV_PATH", DEFAULT_CSV)
DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH_SIZE   = int(os.getenv("BATCH_SIZE", "500"))
DO_TRUNCATE  = os.getenv("TRUNCATE", "false").lower() == "true"

# ── Sanitización de precios ───────────────────────────────────
PRICE_SANITIZE           = os.getenv("PRICE_SANITIZE", "true").lower() != "false"
PRICE_MIN_ARS            = float(os.getenv("PRICE_MIN_ARS", "1000"))
PRICE_MAX_ARS            = float(os.getenv("PRICE_MAX_ARS", "2000000"))
PRICE_MAX_CUOTAS         = int(os.getenv("PRICE_MAX_CUOTAS", "24"))
PRICE_INVALIDATE_DERIVED = os.getenv("PRICE_INVALIDATE_DERIVED", "true").lower() != "false"

# Columnas del CSV → columnas de la tabla
COL_MAP = {
    "Fecha_Corrida":              "fecha_corrida",
    "Scraper":                    "scraper",
    "Canal":                      "canal",
    "Marca":                      "marca",
    "Season":                     "season",
    "StyleColor":                 "style_color",
    "Product_Code_Competitor":    "product_code_competitor",
    "Marketing_Name":             "marketing_name",
    "Division":                   "division",
    "Category":                   "category",
    "Franchise_Scrapper":         "franchise_scrapper",
    "Gender":                     "gender",
    "GAMA_BOTIN":                 "gama_botin",
    "PLATO":                      "plato",
    "NDDC":                       "nddc",
    "ProductCode_Competitor":     "productcode_competitor",
    "Product_Name_Competitor":    "product_name_competitor",
    "Category_Competitor":        "category_competitor",
    "Division_Competitor":        "division_competitor",
    "Franchise_Competitor":       "franchise_competitor",
    "Gender_Competitor":          "gender_competitor",
    "Size_Available_Competitor":  "size_available_competitor",
    "Size_Available_Nike":        "size_available_nike",
    "Link_PDP_Competitor":        "link_pdp_competitor",
    "Competitor_Full_Price":      "competitor_full_price",
    "Competitor_Markdown":        "competitor_markdown",
    "Competitor_Final_Price":     "competitor_final_price",
    "Competitor_Shipping":        "competitor_shipping",
    "Competitor_Price_Shipping":  "competitor_price_shipping",
    "Cuotas_Competitor":          "cuotas_competitor",
    "Nike_Full_Price":            "nike_full_price",
    "Nike_Markdown":              "nike_markdown",
    "Nike_Final_Price":           "nike_final_price",
    "Nike_Shipping":              "nike_shipping",
    "Nike_Price_Shipping":        "nike_price_shipping",
    "Cuotas_Nike":                "cuotas_nike",
    "Gap_Final_Price_Pct":        "gap_final_price_pct",
    "Gap_Full_Price_Pct":         "gap_full_price_pct",
    "Gap_Shipping_Pct":           "gap_shipping_pct",
    "BML_Final_Price":            "bml_final_price",
    "BML_Full_Price":             "bml_full_price",
    "BML_with_Shipping":          "bml_with_shipping",
    "BML_Cuotas":                 "bml_cuotas",
    "FX_ARS_USD":                 "fx_ars_usd",
    "Competitor_Price_USD":       "competitor_price_usd",
    "Competitor_Price_USD_IVA":   "competitor_price_usd_iva",
    "Competitor_Price_USD_IVA_BF":"competitor_price_usd_iva_bf",
    "Gap_Full_Price_USD":         "gap_full_price_usd",
    "Gap_Full_Price_USD_IVA":     "gap_full_price_usd_iva",
    "Gap_Full_Price_USD_IVA_BF":  "gap_full_price_usd_iva_bf",
    "Gap_Final_Price_USD":        "gap_final_price_usd",
    "Gap_Markdown_Price_USD":     "gap_markdown_price_usd",
    "BML_vs_NikeAR":              "bml_vs_nikear",
    "BML_vs_NikeAR_IVA":          "bml_vs_nikear_iva",
    "BML_vs_NikeAR_IVA_BF":       "bml_vs_nikear_iva_bf",
    "__source_file__":            "source_file",
    "Text_Sizes_Nike":            "text_sizes_nike",
    "Text_Sizes_Competitor":      "text_sizes_competitor",
    "PDP_Nike":                   "pdp_nike",
    "Rango_Precio":               "rango_precio",
    "Precio_Sugerido":            "precio_sugerido",
    "Price_Index":                "price_index",
    "Silueta":                    "silueta",
}

NUMERIC_COLS = {
    "size_available_competitor", "size_available_nike",
    "competitor_full_price", "competitor_markdown", "competitor_final_price",
    "competitor_shipping", "competitor_price_shipping",
    "nike_full_price", "nike_markdown", "nike_final_price",
    "nike_shipping", "nike_price_shipping",
    "gap_final_price_pct", "gap_full_price_pct", "gap_shipping_pct",
    "fx_ars_usd", "competitor_price_usd", "competitor_price_usd_iva",
    "competitor_price_usd_iva_bf", "gap_full_price_usd",
    "gap_full_price_usd_iva", "gap_full_price_usd_iva_bf",
    "gap_final_price_usd", "gap_markdown_price_usd",
    "precio_sugerido", "price_index",
}


def clean(val: str, col: str):
    """Limpia y tipea un valor del CSV."""
    v = val.strip() if val else ""
    if v in ("", "nan", "NaN", "None", "NULL", "#N/A", "N/A"):
        return None
    if col in NUMERIC_COLS:
        try:
            return float(v)
        except ValueError:
            return None
    if col == "fecha_corrida":
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            return None
    return v or None


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ══════════════════════════════════════════════════════════════
# SANITIZACIÓN DE PRECIOS
# ══════════════════════════════════════════════════════════════
# Los scrapers originales no están en este repo y el CSV llega con dos tipos
# de basura conocidos:
#
#  1) PRECIO INFLADO POR CUOTAS
#     El parser se comió el "N cuotas sin interés" y guardó el TOTAL
#     financiado en vez del precio unitario. Los casos reportados son
#     exactamente 8x el precio real:
#         2.639.992 = 329.999 x 8
#         2.399.992 = 299.999 x 8
#         2.079.992 = 259.999 x 8
#
#  2) PRECIO EN 0
#     `Competitor_Final_Price = 0` NO significa "gratis": es dato ausente.
#     Si entra a la base contamina compliance, violaciones de PVP (aparece
#     como -100%), gaps, promedios, markdown y BML.
#
# CRITERIO (heurística sobre datos sucios — ajustable por env var):
#   · `<= 0`                        → NULL (dato ausente)
#   · dentro de [MIN, MAX]          → se deja como está
#   · fuera de rango + cuotas conocidas y `precio/cuotas` plausible
#                                   → se corrige dividiendo por las cuotas
#   · fuera de rango sin corrección → NULL (preferimos N/D antes que un
#                                     número que el negocio no puede usar)
#
# No adivinamos el divisor cuando las cuotas no vienen declaradas: varios
# divisores podrían "funcionar" y sería inventar dato.
#
# El mismo criterio está replicado en `web/src/lib/price.ts` (capa de
# lectura). Si se cambia acá, cambiarlo allá también.
# ══════════════════════════════════════════════════════════════

# Grupos precio → columna de cuotas que puede explicar el inflado.
PRICE_GROUPS = [
    (("competitor_final_price", "competitor_full_price", "competitor_price_shipping"),
     "cuotas_competitor"),
    (("nike_final_price", "nike_full_price", "nike_price_shipping"),
     "cuotas_nike"),
]

# Columnas derivadas del precio del competidor. Si el precio de origen fue
# corregido o descartado, lo que el CSV traía calculado sobre el valor sucio
# ya no es confiable: se anula para que la UI muestre N/D en vez de un número
# derivado de basura.
COMPETITOR_DERIVED_COLS = (
    "gap_final_price_pct", "gap_full_price_pct", "gap_shipping_pct",
    "price_index",
    "competitor_price_usd", "competitor_price_usd_iva", "competitor_price_usd_iva_bf",
    "gap_full_price_usd", "gap_full_price_usd_iva", "gap_full_price_usd_iva_bf",
    "gap_final_price_usd", "gap_markdown_price_usd",
    "bml_final_price", "bml_full_price", "bml_with_shipping", "bml_cuotas",
    "bml_vs_nikear", "bml_vs_nikear_iva", "bml_vs_nikear_iva_bf",
)

_CUOTAS_RE = re.compile(r"\d+")


def parse_cuotas(raw):
    """'8 cuotas sin interés' / '12x' / 6 → int. None si no se puede inferir."""
    if raw is None:
        return None
    m = _CUOTAS_RE.search(str(raw))
    if not m:
        return None
    cuotas = int(m.group(0))
    if cuotas < 2 or cuotas > PRICE_MAX_CUOTAS:
        return None
    return cuotas


def is_plausible_price(value) -> bool:
    if value is None:
        return False
    return PRICE_MIN_ARS <= value <= PRICE_MAX_ARS


def sanitize_price(value, cuotas):
    """
    Devuelve (precio_saneado, motivo).
    motivo ∈ {None, 'zero', 'cuotas', 'out_of_range'}
    """
    if value is None:
        return None, None
    if value <= 0:
        return None, "zero"
    if is_plausible_price(value):
        return value, None
    if cuotas:
        unit = value / cuotas
        if is_plausible_price(unit):
            return round(unit, 2), "cuotas"
    return None, "out_of_range"


def sanitize_prices(record: dict, stats: dict) -> None:
    """Sanea in-place los precios de una fila ya mapeada a columnas de la tabla."""
    if not PRICE_SANITIZE:
        return

    competitor_touched = False

    for cols, cuotas_col in PRICE_GROUPS:
        cuotas = parse_cuotas(record.get(cuotas_col))
        for col in cols:
            original = record.get(col)
            fixed, reason = sanitize_price(original, cuotas)
            if reason is None:
                continue
            record[col] = fixed
            stats[reason] = stats.get(reason, 0) + 1
            stats.setdefault("by_col", {})
            stats["by_col"][col] = stats["by_col"].get(col, 0) + 1
            if cuotas_col == "cuotas_competitor":
                competitor_touched = True

    if competitor_touched and PRICE_INVALIDATE_DERIVED:
        invalidated = False
        for col in COMPETITOR_DERIVED_COLS:
            if record.get(col) is not None:
                record[col] = None
                invalidated = True
        if invalidated:
            stats["derived_invalidated"] = stats.get("derived_invalidated", 0) + 1


def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL no está configurada.")
        print("Ejemplo: $env:DATABASE_URL='postgresql://postgres:pass@db.xxx.supabase.co:5432/postgres'")
        sys.exit(1)

    if not Path(CSV_PATH).exists():
        print(f"ERROR: CSV no encontrado en {CSV_PATH}")
        sys.exit(1)

    log(f"Conectando a PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require" if "supabase.co" in DATABASE_URL else "prefer")
    conn.autocommit = False
    cur = conn.cursor()

    # Registrar run
    run_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO scrape_runs_nike (id, scraper_name, status) VALUES (%s, %s, 'running')",
        (run_id, "csv_load")
    )
    conn.commit()

    if DO_TRUNCATE:
        log("TRUNCATE pricing_data...")
        cur.execute("TRUNCATE TABLE pricing_data RESTART IDENTITY")
        conn.commit()

    log(f"Leyendo CSV: {CSV_PATH}")
    if PRICE_SANITIZE:
        log(f"Sanitización de precios ACTIVA — rango plausible "
            f"[{PRICE_MIN_ARS:,.0f}, {PRICE_MAX_ARS:,.0f}] ARS, "
            f"cuotas máx {PRICE_MAX_CUOTAS}, "
            f"invalidar derivados: {PRICE_INVALIDATE_DERIVED}")
    else:
        log("Sanitización de precios DESACTIVADA (PRICE_SANITIZE=false)")

    total = inserted = errors = 0
    batch = []
    price_stats: dict = {}
    start = time.time()

    db_cols = list(COL_MAP.values())
    placeholders = ",".join(["%s"] * len(db_cols))
    insert_sql = f"""
        INSERT INTO pricing_data ({",".join(db_cols)})
        VALUES ({placeholders})
    """

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            try:
                record = {db_col: clean(row.get(csv_col, ""), db_col)
                          for csv_col, db_col in COL_MAP.items()}
                sanitize_prices(record, price_stats)
                values = tuple(record[db_col] for db_col in db_cols)
                batch.append(values)
            except Exception as e:
                errors += 1
                if errors <= 5:
                    log(f"  Fila {total} error: {e}")
                continue

            if len(batch) >= BATCH_SIZE:
                try:
                    psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=BATCH_SIZE)
                    conn.commit()
                    inserted += len(batch)
                    batch = []
                    if inserted % 5000 == 0:
                        elapsed = time.time() - start
                        log(f"  {inserted:,} filas insertadas ({elapsed:.1f}s)...")
                except Exception as e:
                    conn.rollback()
                    errors += len(batch)
                    log(f"  ERROR en batch: {e}")
                    batch = []

    # Último batch
    if batch:
        try:
            psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=BATCH_SIZE)
            conn.commit()
            inserted += len(batch)
        except Exception as e:
            conn.rollback()
            errors += len(batch)
            log(f"  ERROR en último batch: {e}")

    elapsed = time.time() - start

    # Actualizar run
    cur.execute(
        "UPDATE scrape_runs_nike SET status=%s, rows_inserted=%s, finished_at=NOW() WHERE id=%s",
        ("success" if errors == 0 else "partial", inserted, run_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    log("=" * 50)
    log(f"DONE en {elapsed:.1f}s")
    log(f"  Total filas CSV : {total:,}")
    log(f"  Insertadas      : {inserted:,}")
    log(f"  Errores         : {errors:,}")

    if PRICE_SANITIZE:
        by_col = price_stats.get("by_col", {})
        log("  ── Sanitización de precios ──")
        log(f"  Precios <= 0 → NULL          : {price_stats.get('zero', 0):,}")
        log(f"  Corregidos por cuotas (/N)   : {price_stats.get('cuotas', 0):,}")
        log(f"  Fuera de rango → NULL        : {price_stats.get('out_of_range', 0):,}")
        log(f"  Filas con derivados anulados : {price_stats.get('derived_invalidated', 0):,}")
        for col, n in sorted(by_col.items(), key=lambda kv: -kv[1]):
            log(f"      · {col}: {n:,}")
    log("=" * 50)


if __name__ == "__main__":
    main()
