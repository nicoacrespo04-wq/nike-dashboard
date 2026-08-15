#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_run_summary.py — El job `notify` del workflow, de verdad.

Antes el job "Notificación Final" sólo hacía `echo` de tres variables: nadie
se enteraba de nada salvo que entrara a mirar los logs.

Ahora:
  · junta los JSON de métricas que dejó `load_and_report.py` en cada job
    (bajados como artifacts),
  · los cruza con el resultado real de cada job (`needs.<job>.result`),
  · imprime el resumen y lo escribe en el Job Summary de GitHub,
  · manda un email con `scraper/alerts/email_alert.py` (SMTP),
  · sale con código != 0 si algo falló, para que la corrida quede en ROJO.

El envío de email NUNCA tira abajo el workflow: si SMTP falla, queda un
`::warning::` y el resumen igual está en los logs y en el Job Summary.

Uso:
    python .github/scripts/send_run_summary.py \
        --metrics-dir run-metrics \
        --job "nike-ar=success" --job "competencia=failure" \
        --job "retailers-ar=success" --job "preflight=success" \
        --run-url "https://github.com/owner/repo/actions/runs/123"

Flags útiles para probar sin mandar mail:
    --no-email
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

STATUS_OK = {"success", "partial"}

# Etiquetas legibles para los estados que produce load_and_report.py
STATUS_LABEL = {
    "success":           "OK",
    "partial":           "Cargado con filas rechazadas",
    "no_data":           "El scraper no dejó ningún CSV nuevo",
    "empty":             "CSV sin filas de datos",
    "scraper_failed":    "El scraper falló",
    "load_failed":       "db/load_csv.py falló",
    "no_rows_inserted":  "0 filas insertadas",
    "loader_missing":    "No se encontró db/load_csv.py",
    "unreadable":        "CSV ilegible",
    "unknown":           "Estado desconocido",
}


def load_email_module(repo_root: Path):
    """
    Importa scraper/alerts/email_alert.py por path (el repo no tiene
    __init__.py en scraper/, así que no dependemos del import por paquete).
    """
    target = repo_root / "scraper" / "alerts" / "email_alert.py"
    if not target.is_file():
        return None
    spec = importlib.util.spec_from_file_location("email_alert", target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_metrics(metrics_dir: Path) -> list[dict]:
    if not metrics_dir.is_dir():
        return []
    out = []
    for path in sorted(metrics_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"::warning::No se pudo leer {path}: {e}")
            continue
        if isinstance(data, dict) and data.get("scraper"):
            out.append(data)
    return sorted(out, key=lambda d: d.get("scraper", ""))


def parse_jobs(raw: list[str]) -> dict[str, str]:
    jobs = {}
    for item in raw:
        job_id, _, result = item.partition("=")
        jobs[job_id.strip()] = (result.strip() or "unknown")
    return jobs


def build_rows(metrics: list[dict], jobs: dict[str, str]) -> list[dict]:
    """Una fila por scraper + una fila sintética por job caído sin métricas."""
    rows = []
    for m in metrics:
        status = m.get("status", "unknown")
        rows.append({
            "scraper":    m.get("label") or m.get("scraper", "?"),
            "success":    status in STATUS_OK,
            "error":      m.get("error") or STATUS_LABEL.get(status, status),
            "duration_s": m.get("duration_s", 0) or 0,
            "rows":       m.get("rows_inserted", 0) or 0,
            "status":     status,
        })

    covered = {m.get("job_id") for m in metrics}
    reasons = {
        "skipped":   "El job no corrió (una dependencia falló, típicamente el preflight de secrets)",
        "cancelled": "El job fue cancelado (timeout de job o cancelación manual)",
        "failure":   "El job falló antes de llegar a cargar datos (checkout del repo de scrapers, "
                     "instalación de dependencias o el propio scraper)",
    }
    for job_id, result in jobs.items():
        if job_id in covered or result == "success":
            continue
        rows.append({
            "scraper":    f"job: {job_id}",
            "success":    False,
            "error":      reasons.get(result, f"El job terminó en '{result}' sin producir métricas"),
            "duration_s": 0,
            "rows":       0,
            "status":     result,
        })
    return rows


def totals_from(metrics: list[dict]) -> dict:
    keys = ("rows_csv", "rows_inserted", "loader_errors", "price_zero",
            "price_cuotas", "price_out_of_range", "derived_invalidated")
    return {k: sum(int(m.get(k, 0) or 0) for m in metrics) for k in keys}


def render_text(rows: list[dict], jobs: dict[str, str], totals: dict, run_url: str) -> str:
    lines = ["", "=" * 68, "RESUMEN DE LA CORRIDA SEMANAL DE SCRAPERS", "=" * 68]
    lines.append("")
    lines.append("Jobs:")
    for job_id, result in jobs.items():
        lines.append(f"  {'OK ' if result == 'success' else 'X  '} {job_id}: {result}")
    lines.append("")
    lines.append("Scrapers:")
    if not rows:
        lines.append("  (sin métricas — no corrió ningún scraper)")
    for r in rows:
        mark = "OK " if r["success"] else "X  "
        lines.append(f"  {mark} {r['scraper']:<24} {r['rows']:>8,} filas   {r['error']}")
    lines.append("")
    lines.append("Datos cargados:")
    lines.append(f"  Filas en CSV                  : {totals['rows_csv']:,}")
    lines.append(f"  Filas insertadas              : {totals['rows_inserted']:,}")
    lines.append(f"  Filas rechazadas por el loader: {totals['loader_errors']:,}")
    lines.append("")
    lines.append("Saneamiento de precios (db/load_csv.py):")
    lines.append(f"  Precios <= 0 descartados      : {totals['price_zero']:,}")
    lines.append(f"  Precios fuera de rango        : {totals['price_out_of_range']:,}")
    lines.append(f"  Precios corregidos por cuotas : {totals['price_cuotas']:,}")
    lines.append(f"  Filas con derivados anulados  : {totals['derived_invalidated']:,}")
    if run_url:
        lines.append("")
        lines.append(f"Corrida: {run_url}")
    lines.append("=" * 68)
    return "\n".join(lines)


def render_markdown(rows: list[dict], jobs: dict[str, str], totals: dict, run_url: str) -> str:
    md = ["## Resumen de la corrida semanal", "", "| Job | Resultado |", "|---|---|"]
    for job_id, result in jobs.items():
        icon = "✅" if result == "success" else ("⏭️" if result == "skipped" else "❌")
        md.append(f"| `{job_id}` | {icon} {result} |")
    md += ["", "| Scraper | Estado | Filas cargadas | Detalle |", "|---|---|---:|---|"]
    for r in rows:
        icon = "✅" if r["success"] else "❌"
        md.append(f"| {r['scraper']} | {icon} | {r['rows']:,} | {r['error']} |")
    md += [
        "",
        "### Datos cargados",
        f"- Filas en CSV: **{totals['rows_csv']:,}**",
        f"- Filas insertadas: **{totals['rows_inserted']:,}**",
        f"- Filas rechazadas por el loader: **{totals['loader_errors']:,}**",
        "",
        "### Saneamiento de precios (`db/load_csv.py`)",
        f"- Precios `<= 0` descartados: **{totals['price_zero']:,}**",
        f"- Precios fuera de rango descartados: **{totals['price_out_of_range']:,}**",
        f"- Precios corregidos dividiendo por cuotas: **{totals['price_cuotas']:,}**",
        f"- Filas con métricas derivadas anuladas: **{totals['derived_invalidated']:,}**",
    ]
    if run_url:
        md += ["", f"[Ver la corrida completa]({run_url})"]
    return "\n".join(md)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics-dir", default="run-metrics")
    ap.add_argument("--job", action="append", default=[], metavar="ID=RESULT")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()

    repo_root = Path(os.getenv("GITHUB_WORKSPACE", ".")).resolve()
    metrics = read_metrics(Path(args.metrics_dir))
    jobs = parse_jobs(args.job)
    rows = build_rows(metrics, jobs)
    totals = totals_from(metrics)

    print(render_text(rows, jobs, totals, args.run_url))

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(render_markdown(rows, jobs, totals, args.run_url) + "\n")
        except OSError as e:
            print(f"::warning::No se pudo escribir el Job Summary: {e}")

    # ── Email ────────────────────────────────────────────────────────────
    # Nunca puede tirar abajo el workflow: si SMTP falla, warning y seguimos.
    if not args.no_email:
        try:
            mod = load_email_module(repo_root)
            if mod is None:
                print("::warning::No se encontró scraper/alerts/email_alert.py — sin email.")
            elif not (os.getenv("SMTP_USER") and os.getenv("SMTP_PASS")):
                print("::warning::SMTP_USER/SMTP_PASS no configurados — "
                      "el resumen no se envía por email (está en el Job Summary).")
            else:
                sent = mod.send_run_summary(rows, totals=totals, run_url=args.run_url)
                if sent:
                    print(f"Email de resumen enviado a {getattr(mod, 'ALERT_EMAIL', '?')}")
                else:
                    print("::warning::El envío del email de resumen falló "
                          "(ver logs arriba). El workflow sigue.")
        except Exception as e:  # noqa: BLE001 — notificar nunca debe romper el job
            print(f"::warning::Error inesperado enviando el email de resumen: {e}")

    # ── Veredicto ────────────────────────────────────────────────────────
    failed_jobs = [j for j, r in jobs.items() if r != "success"]
    failed_rows = [r["scraper"] for r in rows if not r["success"]]

    if failed_jobs or failed_rows:
        if failed_jobs:
            print(f"::error::Jobs con problemas: {', '.join(failed_jobs)}")
        if failed_rows:
            print(f"::error::Scrapers con problemas: {', '.join(failed_rows)}")
        return 1

    if not rows:
        print("::error::No se cargó ningún dato en esta corrida.")
        return 1

    print("Todo OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
