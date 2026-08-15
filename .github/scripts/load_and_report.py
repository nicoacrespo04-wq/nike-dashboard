#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
load_and_report.py — Puente verificado entre un scraper y `db/load_csv.py`.

Por qué existe
──────────────
El workflow viejo hacía:

    python ../nike-dashboard/db/load_csv.py Nike_AR_General_*.csv

Eso tenía tres problemas: la ruta relativa no existe con `actions/checkout`,
el glob lo expandía el shell (si no matcheaba nada le pasaba el patrón literal
al loader) y el paso corría aunque el scraper no hubiera producido NADA — así
que "cargar la nada" quedaba en verde.

Este script, en cambio:

  1. Resuelve los globs en Python (case-insensitive) y sólo acepta archivos
     CREADOS EN ESTA CORRIDA (`--min-mtime`), para no recargar CSVs viejos
     que vengan commiteados en el repo de scrapers.
  2. Verifica que cada CSV exista y tenga al menos una fila de datos.
  3. Corre `db/load_csv.py` por archivo, con la salida en vivo.
  4. Parsea el resumen del loader (filas insertadas, errores y contadores de
     sanitización de precios) y escribe un JSON de métricas que después
     consume el job `notify` para mandar el email de resumen.
  5. Sale con código != 0 ante cualquier problema — nada de fallar en silencio.

Uso:
    python .github/scripts/load_and_report.py \
        --name nike_ar --label "Nike AR" \
        --scrape-outcome success \
        --metrics-dir run-metrics \
        --min-mtime "$SCRAPE_START" \
        'Nike_Scrapper_Final/Nike_AR_General_*.csv'

Los patrones posicionales son una CADENA DE FALLBACK: se usa el primero que
matchee al menos un archivo fresco.

Variables de entorno relevantes:
    ALLOW_STALE_CSV=true   ignora `--min-mtime` (útil para recargas manuales)
    DATABASE_URL, TRUNCATE, BATCH_SIZE, PRICE_*  → las consume db/load_csv.py

Códigos de salida:
    0  cargó al menos un CSV sin errores del loader
    1  no hay CSV / CSV vacío / el loader falló / el scraper había fallado
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Parseo del resumen que imprime db/load_csv.py ─────────────────────────
#   Total filas CSV : 1,234
#   Insertadas      : 1,200
#   Errores         : 0
#   Precios <= 0 → NULL          : 12
#   Corregidos por cuotas (/N)   : 3
#   Fuera de rango → NULL        : 1
#   Filas con derivados anulados : 4
LOADER_PATTERNS = {
    "rows_csv":            r"Total filas CSV\s*:\s*([\d.,]+)",
    "rows_inserted":       r"Insertadas\s*:\s*([\d.,]+)",
    "loader_errors":       r"Errores\s*:\s*([\d.,]+)",
    "price_zero":          r"Precios <= 0[^:]*:\s*([\d.,]+)",
    "price_cuotas":        r"Corregidos por cuotas[^:]*:\s*([\d.,]+)",
    "price_out_of_range":  r"Fuera de rango[^:]*:\s*([\d.,]+)",
    "derived_invalidated": r"Filas con derivados anulados[^:]*:\s*([\d.,]+)",
}

METRIC_KEYS = list(LOADER_PATTERNS.keys())


def log(msg: str) -> None:
    print(msg, flush=True)


def gh_error(msg: str) -> None:
    print(f"::error::{msg}", flush=True)


def gh_warn(msg: str) -> None:
    print(f"::warning::{msg}", flush=True)


def expand_pattern(pattern: str) -> list[Path]:
    """
    Expande un glob de forma case-insensitive (los CSV de los retailers vienen
    con mayúsculas inconsistentes: `soloDeportes_...` vs `solodeportes_...`).
    Sólo soporta el comodín en el nombre de archivo, no en el directorio.
    """
    p = Path(pattern)
    directory = p.parent if str(p.parent) else Path(".")
    name_pat = p.name.lower()
    if not directory.is_dir():
        return []
    return sorted(
        entry for entry in directory.iterdir()
        if entry.is_file() and fnmatch.fnmatch(entry.name.lower(), name_pat)
    )


def count_data_rows(path: Path) -> int:
    """Cantidad de filas con contenido, sin contar el header."""
    rows = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # header
        except StopIteration:
            return 0
        for row in reader:
            if any((cell or "").strip() for cell in row):
                rows += 1
    return rows


def parse_loader_output(text: str) -> dict:
    out: dict[str, int] = {}
    for key, pat in LOADER_PATTERNS.items():
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1).replace(",", "").replace(".", ""))
    return out


def run_loader(loader: Path, csv_path: Path) -> tuple[int, str]:
    """Corre db/load_csv.py mostrando la salida en vivo y devolviéndola."""
    cmd = [sys.executable, str(loader), str(csv_path)]
    log(f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        print(line.rstrip(), flush=True)
    proc.wait()
    return proc.returncode, "".join(chunks)


def write_metrics(metrics: dict, metrics_dir: Path, name: str) -> Path:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    dest = metrics_dir / f"{name}.json"
    dest.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Métricas escritas en {dest}")
    return dest


def append_step_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:  # nunca romper el job por no poder escribir el resumen
        gh_warn(f"No se pudo escribir GITHUB_STEP_SUMMARY: {e}")


def finish(metrics: dict, args, code: int) -> int:
    metrics["status"] = metrics.get("status", "unknown")
    write_metrics(metrics, Path(args.metrics_dir), args.name)

    icon = {"success": "✅", "partial": "⚠️"}.get(metrics["status"], "❌")
    append_step_summary([
        f"### {icon} {metrics['label']} — {metrics['status']}",
        "",
        f"- Archivos cargados: `{', '.join(metrics['files']) or '(ninguno)'}`",
        f"- Filas en CSV: {metrics.get('rows_csv', 0):,}",
        f"- Filas insertadas: {metrics.get('rows_inserted', 0):,}",
        f"- Precios descartados (<=0 / fuera de rango): "
        f"{metrics.get('price_zero', 0):,} / {metrics.get('price_out_of_range', 0):,}",
        f"- Precios corregidos por cuotas: {metrics.get('price_cuotas', 0):,}",
        f"- Detalle: {metrics.get('error') or 'sin errores'}",
    ])
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("patterns", nargs="+", help="Globs de CSV, en orden de preferencia")
    ap.add_argument("--name", required=True, help="ID corto del scraper (nombre del archivo de métricas)")
    ap.add_argument("--label", default=None, help="Nombre humano para el resumen")
    ap.add_argument("--job-id", default=None,
                    help="ID del job del workflow que produjo estas métricas (para el resumen final)")
    ap.add_argument("--loader", default=None, help="Path a db/load_csv.py")
    ap.add_argument("--metrics-dir", default="run-metrics")
    ap.add_argument("--copy-to", default=None,
                    help="Copia los CSV encontrados a esta carpeta, para subirlos como artifact "
                         "(evita depender de globs case-sensitive en upload-artifact)")
    ap.add_argument("--scrape-outcome", default="success",
                    help="Resultado del paso que corrió el scraper (success/failure/...)")
    ap.add_argument("--min-mtime", default="0",
                    help="Epoch: sólo se aceptan CSV modificados después de este instante")
    args = ap.parse_args()

    label = args.label or args.name
    workspace = Path(os.getenv("GITHUB_WORKSPACE", ".")).resolve()
    loader = Path(args.loader) if args.loader else workspace / "db" / "load_csv.py"

    try:
        min_mtime = float(args.min_mtime or 0)
    except ValueError:
        min_mtime = 0.0
    if os.getenv("ALLOW_STALE_CSV", "false").lower() == "true":
        log("ALLOW_STALE_CSV=true — se aceptan CSVs preexistentes.")
        min_mtime = 0.0

    metrics: dict = {
        "scraper": args.name,
        "label": label,
        "job_id": args.job_id or args.name,
        "status": "unknown",
        "scrape_outcome": args.scrape_outcome,
        "files": [],
        "error": None,
        "started_at": time.time(),
    }
    for key in METRIC_KEYS:
        metrics[key] = 0

    # ── 0. ¿El scraper terminó bien? ─────────────────────────────────────
    if args.scrape_outcome not in ("success", ""):
        gh_error(f"{label}: el scraper terminó con estado '{args.scrape_outcome}'. "
                 f"No se carga nada a la base.")
        metrics["status"] = "scraper_failed"
        metrics["error"] = f"El scraper terminó con estado '{args.scrape_outcome}'"
        return finish(metrics, args, 1)

    if not loader.is_file():
        gh_error(f"No se encontró el loader en {loader}. "
                 f"¿El checkout de este repo quedó en la raíz del workspace?")
        metrics["status"] = "loader_missing"
        metrics["error"] = f"Loader no encontrado en {loader}"
        return finish(metrics, args, 1)

    # ── 1. Buscar los CSV (cadena de fallback de patrones) ───────────────
    matched: list[Path] = []
    used_pattern = None
    stale: list[Path] = []

    for pattern in args.patterns:
        found = expand_pattern(pattern)
        fresh = [f for f in found if f.stat().st_mtime >= min_mtime]
        stale.extend(f for f in found if f not in fresh)
        log(f"Patrón '{pattern}': {len(found)} archivo(s), {len(fresh)} de esta corrida.")
        if fresh:
            matched, used_pattern = fresh, pattern
            break

    if not matched:
        detail = f"Ningún CSV nuevo matcheó {args.patterns}."
        if stale:
            detail += (f" Sí existen {len(stale)} CSV viejos "
                       f"({', '.join(p.name for p in stale[:5])}) — si querés cargarlos, "
                       f"corré el workflow manualmente con allow_stale_csv=true.")
        gh_error(f"{label}: {detail}")
        # Listado de ayuda para arreglar el patrón.
        search_dir = Path(args.patterns[0]).parent
        if search_dir.is_dir():
            csvs = sorted(p.name for p in search_dir.glob("*.csv"))
            log(f"CSVs presentes en {search_dir}: {csvs if csvs else '(ninguno)'}")
        metrics["status"] = "no_data"
        metrics["error"] = detail
        return finish(metrics, args, 1)

    log(f"{label}: {len(matched)} archivo(s) via patrón '{used_pattern}'.")

    # Copia para el artifact: se suben los archivos que REALMENTE encontramos,
    # incluidos los que después se rechacen por vacíos (sirven para debuggear).
    if args.copy_to:
        dest_dir = Path(args.copy_to)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in matched:
            try:
                shutil.copy2(f, dest_dir / f.name)
            except OSError as e:
                gh_warn(f"No se pudo copiar {f} a {dest_dir}: {e}")

    # ── 2. Verificar que tengan filas ────────────────────────────────────
    loadable: list[Path] = []
    for f in matched:
        try:
            rows = count_data_rows(f)
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            gh_error(f"{label}: no se pudo leer {f}: {e}")
            metrics["status"] = "unreadable"
            metrics["error"] = f"CSV ilegible: {f} ({e})"
            return finish(metrics, args, 1)
        if rows == 0:
            gh_warn(f"{label}: {f.name} no tiene filas de datos — se descarta.")
            continue
        log(f"  {f.name}: {rows:,} filas de datos")
        loadable.append(f)

    if not loadable:
        detail = (f"Los {len(matched)} CSV encontrados están vacíos "
                  f"(sólo header). El scraper no produjo datos.")
        gh_error(f"{label}: {detail}")
        metrics["status"] = "empty"
        metrics["error"] = detail
        return finish(metrics, args, 1)

    # ── 3. Cargar ────────────────────────────────────────────────────────
    failures: list[str] = []
    for f in loadable:
        code, output = run_loader(loader, f)
        stats = parse_loader_output(output)
        for key in METRIC_KEYS:
            metrics[key] += stats.get(key, 0)
        metrics["files"].append(f.name)
        if code != 0:
            failures.append(f"{f.name} (exit {code})")
            gh_error(f"{label}: db/load_csv.py falló con {f.name} (exit {code}).")

    metrics["duration_s"] = round(time.time() - metrics["started_at"], 1)

    if failures:
        metrics["status"] = "load_failed"
        metrics["error"] = "load_csv.py falló en: " + ", ".join(failures)
        return finish(metrics, args, 1)

    if metrics["rows_inserted"] == 0:
        detail = "El loader no insertó ninguna fila."
        gh_error(f"{label}: {detail}")
        metrics["status"] = "no_rows_inserted"
        metrics["error"] = detail
        return finish(metrics, args, 1)

    if metrics["loader_errors"] > 0:
        metrics["status"] = "partial"
        metrics["error"] = f"{metrics['loader_errors']} filas rechazadas por el loader"
        gh_warn(f"{label}: {metrics['error']}")
        return finish(metrics, args, 0)

    metrics["status"] = "success"
    log(f"{label}: OK — {metrics['rows_inserted']:,} filas insertadas.")
    return finish(metrics, args, 0)


if __name__ == "__main__":
    sys.exit(main())
