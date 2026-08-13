#!/usr/bin/env python3
"""
load_shelf_csv.py — Carga retail_media_search.csv a PostgreSQL (Supabase)

Uso:
    python load_shelf_csv.py
    DATABASE_URL=postgresql://... python load_shelf_csv.py
"""
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 no instalado. Corré: pip install psycopg2-binary")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────
CSV_PATH = str(Path(__file__).parent / "retail_media_search.csv")
DATABASE_URL = os.getenv("DATABASE_URL", "")
BATCH_SIZE = 500

COL_MAP = {
    "Fecha_Corrida": "fecha_corrida",
    "Scraper": "scraper",
    "Canal": "canal",
    "Marca": "marca",
    "Season": "season",
    "Search_Term": "search_term",
    "Nike_Visibility": "nike_visibility",
    "Type": "type",
    "Category": "category",
    "Level _1": "level_1",
    "Level_2": "level_2",
    "Level_3": "level_3",
    "Division": "division",
}

def clean(val: str, col: str):
    """Limpia y tipea un valor del CSV."""
    v = val.strip() if val else ""
    if v in ("", "nan", "NaN", "None", "NULL", "#N/A", "N/A"):
        return None
    if col == "nike_visibility":
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

def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL no está configurada.")
        sys.exit(1)

    if not Path(CSV_PATH).exists():
        print(f"ERROR: CSV no encontrado en {CSV_PATH}")
        sys.exit(1)

    log(f"Conectando a PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require" if "supabase.co" in DATABASE_URL else "prefer")
    conn.autocommit = False
    cur = conn.cursor()

    log("TRUNCATE retail_media_search...")
    cur.execute("TRUNCATE TABLE retail_media_search RESTART IDENTITY")
    conn.commit()

    log(f"Leyendo CSV: {CSV_PATH}")
    total = inserted = errors = 0
    batch = []

    db_cols = list(COL_MAP.values())
    placeholders = ",".join(["%s"] * len(db_cols))
    insert_sql = f"""
        INSERT INTO retail_media_search ({",".join(db_cols)})
        VALUES ({placeholders})
    """

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            try:
                values = tuple(clean(row.get(csv_col, ""), db_col) for csv_col, db_col in COL_MAP.items())
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
                    if inserted % 1000 == 0:
                        log(f"  {inserted:,} filas insertadas...")
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

    cur.close()
    conn.close()

    log("=" * 50)
    log(f"DONE")
    log(f"  Total filas CSV : {total:,}")
    log(f"  Insertadas      : {inserted:,}")
    log(f"  Errores         : {errors:,}")
    log("=" * 50)

if __name__ == "__main__":
    main()
