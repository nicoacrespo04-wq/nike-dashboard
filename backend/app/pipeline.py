"""Orquestador del pipeline de inteligencia.

Orden de ejecución (cada etapa depende de las anteriores):

    init_db -> seed -> enrichment -> matching -> brand_intelligence
            -> opportunities -> retail_media -> snapshot

Cada etapa se importa de forma perezosa y tolerante: si un módulo todavía no
existe o falla, se registra el error y el pipeline continúa. Así una pieza rota
nunca deja la demo sin datos.

Historial
---------
El pipeline recalcula todo desde cero, así que por sí solo no puede responder
"¿esto viene subiendo?". Alrededor de las etapas hay tres ganchos de
`app.services.history` (ver ese módulo para el contrato de `entity_key`):

  1. antes del reset, `capture()` guarda el historial y el triaje; después de
     `init_db(drop=True)`, `restore()` los devuelve a la base nueva;
  2. `start_run()` abre la corrida en `pipeline_runs`;
  3. al final, `snapshot()` copia el estado calculado al historial y
     `finish_run()` cierra la corrida con el reporte.

Los ganchos son de sólo lectura sobre las tablas de scoring y están envueltos
en try/except: si el historial falla, el pipeline sigue produciendo datos.

Uso:
    python -m app.pipeline            # reconstruye todo desde cero
    python -m app.pipeline --keep     # sin resetear la DB
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path
from typing import Any, Callable

from app.config import DB_PATH
from app.db import init_db

# (etapa, módulo, función)
STAGES: list[tuple[str, str, str]] = [
    ("seed",               "app.seed",                          "seed"),
    ("enrichment",         "app.services.enrichment",           "run_enrichment"),
    ("matching",           "app.services.matching",             "run_matching"),
    ("shelf_signals",      "app.services.shelf",                "run_shelf_signals"),
    ("brand_intelligence", "app.services.brand_intelligence",   "run_brand_intelligence"),
    ("opportunities",      "app.services.opportunities",        "run_opportunities"),
    ("retail_media",       "app.services.retail_media",         "run_retail_media"),
]


def _history() -> Any | None:
    """`app.services.history`, o ``None`` si no está disponible.

    Mismo criterio de tolerancia que las etapas: el historial es valor agregado,
    nunca un motivo para que el pipeline no produzca datos.
    """
    try:
        return importlib.import_module("app.services.history")
    except ImportError:
        return None


def _resolve(module_name: str, func_name: str) -> Callable[..., Any] | None:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, func_name, None)


def run_all(db_path: Path | str = DB_PATH, *, reset: bool = True,
            stages: list[str] | None = None, source: str = "demo",
            history: bool = True) -> dict[str, dict[str, Any]]:
    """Ejecuta el pipeline completo y devuelve un reporte por etapa."""
    report: dict[str, dict[str, Any]] = {}
    hist = _history() if history else None
    run_id: int | None = None

    if reset:
        started = time.perf_counter()
        # El reset borra el archivo entero: el historial y el triaje se rescatan
        # antes y se reinsertan con sus ids, para que las FKs sigan válidas.
        carried: dict[str, Any] = {}
        if hist is not None:
            try:
                carried = hist.capture(db_path)
            except Exception as exc:  # noqa: BLE001 - nunca frenar por el historial
                report["history_carry"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        init_db(db_path, drop=True)
        if hist is not None and carried:
            try:
                restored = hist.restore(carried, db_path)
                report["history_carry"] = {"status": "ok", "counts": restored}
            except Exception as exc:  # noqa: BLE001
                report["history_carry"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        report["init_db"] = {"status": "ok", "seconds": round(time.perf_counter() - started, 3)}

    guard: dict[str, Any] = {}
    if hist is not None:
        try:
            run_id = hist.start_run(db_path, source=source)
            # Red de seguridad: cualquier etapa puede hacer `init_db(drop=True)`
            # (`seed` lo hace sin `reset`) y llevarse el historial y el triaje.
            # Se re-insertan después de las etapas, antes del snapshot.
            guard = hist.capture(db_path)
        except Exception as exc:  # noqa: BLE001
            report["history_run"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    for name, module_name, func_name in STAGES:
        if stages and name not in stages:
            continue

        func = _resolve(module_name, func_name)
        if func is None:
            report[name] = {"status": "skipped", "reason": f"{module_name}.{func_name} no disponible"}
            continue

        started = time.perf_counter()
        try:
            # `seed` ya crea el esquema; si reseteamos acá, no lo dupliques.
            # Ojo: sin `reset`, `seed` usa su `drop=True` y BORRA EL ARCHIVO
            # entero — por eso el historial se protege con `guard` más abajo.
            counts = func(db_path=db_path, drop=False) if name == "seed" and reset else func(db_path=db_path)
            report[name] = {
                "status": "ok",
                "counts": counts,
                "seconds": round(time.perf_counter() - started, 3),
            }
        except TypeError:
            # Firma sin kwargs opcionales: reintentar con el mínimo.
            try:
                counts = func(db_path)
                report[name] = {"status": "ok", "counts": counts,
                                "seconds": round(time.perf_counter() - started, 3)}
            except Exception as exc:  # noqa: BLE001 - una etapa rota no frena el pipeline
                report[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            report[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    if hist is not None and guard:
        try:
            hist.restore(guard, db_path)
        except Exception as exc:  # noqa: BLE001
            report["history_carry"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    # El snapshot corre al final y sólo LEE lo que quedó persistido: ningún
    # servicio de scoring se entera de que existe el historial.
    if hist is not None and run_id is not None:
        started = time.perf_counter()
        try:
            counts = hist.snapshot(run_id, db_path)
            report["snapshot"] = {"status": "ok", "counts": counts,
                                  "seconds": round(time.perf_counter() - started, 3)}
        except Exception as exc:  # noqa: BLE001
            report["snapshot"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        try:
            hist.finish_run(run_id, db_path,
                            status=hist.run_status_from_report(report), counts=report)
        except Exception as exc:  # noqa: BLE001
            report["history_run"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline de Competitive Intelligence")
    parser.add_argument("--keep", action="store_true", help="No resetear la base")
    parser.add_argument("--db", default=str(DB_PATH), help="Ruta del archivo SQLite")
    parser.add_argument("--stage", action="append", help="Ejecutar sólo estas etapas")
    parser.add_argument("--source", default="demo", help="Origen de los datos: demo | ingest")
    parser.add_argument("--no-history", action="store_true",
                        help="No registrar la corrida ni tomar snapshot")
    args = parser.parse_args(argv)

    report = run_all(args.db, reset=not args.keep, stages=args.stage,
                     source=args.source, history=not args.no_history)

    failed = 0
    for stage, info in report.items():
        status = info.get("status")
        if status == "ok":
            detail = info.get("counts")
            print(f"  ✓ {stage:<20} {info.get('seconds', 0):>6.2f}s  {detail if detail else ''}")
        elif status == "skipped":
            print(f"  · {stage:<20} omitida — {info.get('reason')}")
        else:
            failed += 1
            print(f"  ✗ {stage:<20} {info.get('error')}")

    print(f"\nBase: {args.db}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
