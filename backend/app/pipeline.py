"""Orquestador del pipeline de inteligencia.

Orden de ejecución (cada etapa depende de las anteriores):

    init_db -> seed -> enrichment -> matching -> brand_intelligence
            -> opportunities -> retail_media

Cada etapa se importa de forma perezosa y tolerante: si un módulo todavía no
existe o falla, se registra el error y el pipeline continúa. Así una pieza rota
nunca deja la demo sin datos.

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
    ("brand_intelligence", "app.services.brand_intelligence",   "run_brand_intelligence"),
    ("opportunities",      "app.services.opportunities",        "run_opportunities"),
    ("retail_media",       "app.services.retail_media",         "run_retail_media"),
]


def _resolve(module_name: str, func_name: str) -> Callable[..., Any] | None:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, func_name, None)


def run_all(db_path: Path | str = DB_PATH, *, reset: bool = True,
            stages: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Ejecuta el pipeline completo y devuelve un reporte por etapa."""
    report: dict[str, dict[str, Any]] = {}

    if reset:
        started = time.perf_counter()
        init_db(db_path, drop=True)
        report["init_db"] = {"status": "ok", "seconds": round(time.perf_counter() - started, 3)}

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

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline de Competitive Intelligence")
    parser.add_argument("--keep", action="store_true", help="No resetear la base")
    parser.add_argument("--db", default=str(DB_PATH), help="Ruta del archivo SQLite")
    parser.add_argument("--stage", action="append", help="Ejecutar sólo estas etapas")
    args = parser.parse_args(argv)

    report = run_all(args.db, reset=not args.keep, stages=args.stage)

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
