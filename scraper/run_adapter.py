#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_adapter.py — CLI para correr a mano los adapters de `scraper/adapters/`.

Sirve para dos cosas:
  · probar un adapter mientras se lo desarrolla, sin tocar el workflow;
  · generar un CSV con el mismo formato que consume `db/load_csv.py`, para
    validar que un adapter migrado produce lo mismo que su script legacy.

Ejemplos:
    python scraper/run_adapter.py --list
    python scraper/run_adapter.py nike_ar --limit 20 --csv /tmp/nike.csv
    python scraper/run_adapter.py adidas --csv adidas.csv
    DATABASE_URL=postgresql://... python scraper/run_adapter.py adidas --to-db

Salida:
    exit 0 si el scraper terminó OK y produjo al menos un producto.
    exit 1 si falló, si el retailer todavía no tiene adapter, o si no
    devolvió nada (no queremos "éxitos" vacíos).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.adapters import RETAILERS, get_adapter  # noqa: E402

# ScrapedProduct → columnas que espera db/load_csv.py (COL_MAP).
# El loader usa row.get(col, "") así que un CSV parcial es válido.
CSV_COLUMNS: list[tuple[str, str]] = [
    ("Fecha_Corrida",             "_fecha"),
    ("Scraper",                   "scraper"),
    ("Canal",                     "canal"),
    ("Marca",                     "marca"),
    ("Season",                    "season"),
    ("StyleColor",                "style_color"),
    ("Product_Code_Competitor",   "product_code_competitor"),
    ("Marketing_Name",            "marketing_name"),
    ("Division",                  "division"),
    ("Category",                  "category"),
    ("Franchise_Scrapper",        "franchise_scrapper"),
    ("Gender",                    "gender"),
    ("ProductCode_Competitor",    "product_code_competitor"),
    ("Product_Name_Competitor",   "product_name_competitor"),
    ("Category_Competitor",       "category_competitor"),
    ("Division_Competitor",       "division_competitor"),
    ("Franchise_Competitor",      "franchise_competitor"),
    ("Gender_Competitor",         "gender_competitor"),
    ("Size_Available_Competitor", "size_available_competitor"),
    ("Text_Sizes_Competitor",     "text_sizes_competitor"),
    ("Link_PDP_Competitor",       "link_pdp_competitor"),
    ("Competitor_Full_Price",     "competitor_full_price"),
    ("Competitor_Markdown",       "competitor_markdown"),
    ("Competitor_Final_Price",    "competitor_final_price"),
    ("Competitor_Shipping",       "competitor_shipping"),
    ("Cuotas_Competitor",         "cuotas_competitor"),
    ("Nike_Full_Price",           "nike_full_price"),
    ("Nike_Final_Price",          "nike_final_price"),
    ("Gap_Final_Price_Pct",       "gap_final_price_pct"),
    ("BML_Final_Price",           "bml_final_price"),
    ("Precio_Sugerido",           "precio_sugerido"),
    ("Silueta",                   "silueta"),
]


def write_csv(products, dest: Path, scraper_name: str) -> int:
    hoy = date.today().isoformat()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([c for c, _ in CSV_COLUMNS] + ["__source_file__"])
        for p in products:
            row = []
            for _, attr in CSV_COLUMNS:
                value = hoy if attr == "_fecha" else getattr(p, attr, None)
                row.append("" if value is None else value)
            row.append(f"{scraper_name} (run_adapter.py)")
            writer.writerow(row)
    return len(products)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("retailer", nargs="?", help="Nombre del retailer (ver --list)")
    ap.add_argument("--list", action="store_true", help="Lista los retailers y su estado de migración")
    ap.add_argument("--limit", type=int, default=0, help="Corta la salida a N productos (para probar)")
    ap.add_argument("--csv", default=None, help="Escribe un CSV compatible con db/load_csv.py")
    ap.add_argument("--to-db", action="store_true", help="Inserta directo en pricing_data (necesita DATABASE_URL)")
    args = ap.parse_args()

    if args.list or not args.retailer:
        print(f"{'RETAILER':<14} {'ADAPTER':<10} {'SCRIPT LEGACY':<28} CANAL")
        for name, spec in RETAILERS.items():
            estado = "sí" if spec.implemented else "PENDIENTE"
            print(f"{name:<14} {estado:<10} {spec.legacy_script:<28} {spec.canal}")
        print("\nLos 'PENDIENTE' siguen corriendo con el script legacy del repo "
              "privado Nike_Scrapper_Final (ver docs/scrapers.md).")
        return 0 if args.list else 1

    try:
        adapter = get_adapter(args.retailer)
    except (KeyError, NotImplementedError) as e:
        print(f"ERROR: {e}")
        return 1

    result = adapter.run()
    products = result.products[: args.limit] if args.limit else result.products

    print(f"\n{result.scraper}: success={result.success} "
          f"productos={len(result.products)} duración={result.duration_s:.1f}s")
    if result.error:
        print(f"Error: {result.error}")

    if not result.success:
        return 1
    if not products:
        print("ERROR: el scraper terminó bien pero no devolvió productos.")
        return 1

    if args.csv:
        n = write_csv(products, Path(args.csv), result.scraper)
        print(f"CSV escrito: {args.csv} ({n:,} filas)")
        print(f"Para cargarlo:  DATABASE_URL=... python db/load_csv.py {args.csv}")

    if args.to_db:
        inserted = adapter.save_to_db(products)
        print(f"Insertados en pricing_data: {inserted:,}")
        if inserted == 0:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
