"""CLI de la capa de adquisición.

    python -m app.collectors --list                       # ficha legal de cada fuente
    python -m app.collectors                              # corre las fuentes habilitadas
    python -m app.collectors --source editorial_fixture   # una fuente puntual
    python -m app.collectors --since 2026-01-01 --dry-run # simulacro, sin escribir

Es idempotente: correrlo dos veces no duplica menciones (la persistencia
deduplica por clave natural). Nunca devuelve error por falta de red: si una
fuente no está disponible, informa 0 filas y sigue.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.collectors import register_builtin_collectors, registered, run_collectors
from app.collectors.base import catalog
from app.config import DB_PATH
from app.services.common import parse_date


def _print_catalog() -> None:
    print("Fuentes registradas (estado legal y operativo)\n")
    for row in catalog():
        estado = "PROHIBIDA" if row["access"] == "prohibited" else (
            "habilitada" if row["enabled"] else "deshabilitada")
        print(f"  {row['name']}")
        print(f"     fuente      : {row['source_name']} ({row['homepage']})")
        print(f"     acceso      : {row['access']} · {estado} · tabla {row['table']}")
        print(f"     términos    : {row['terms']}")
        print(f"     rate limit  : {row['rate_limit_seconds']}s · robots.txt: "
              f"{'sí' if row['respects_robots_txt'] else 'n/a'}")
        if row["requires"]:
            print(f"     credenciales: {', '.join(row['requires'])}")
        if row["official_api"]:
            print(f"     API oficial : {row['official_api']}")
        if row["notes"]:
            print(f"     notas       : {row['notes']}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.collectors",
        description="Adquisición de menciones editoriales y señales sociales agregadas.")
    parser.add_argument("--source", action="append", dest="sources",
                        help="Correr sólo esta fuente (repetible)")
    parser.add_argument("--since", help="Ignorar observaciones anteriores a YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="No escribe: informa cuántas filas nuevas se insertarían")
    parser.add_argument("--db", default=str(DB_PATH), help="Ruta del archivo SQLite")
    parser.add_argument("--list", action="store_true", dest="show_catalog",
                        help="Mostrar las fuentes con su estado legal y salir")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log detallado")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    register_builtin_collectors(args.db)

    if args.show_catalog:
        _print_catalog()
        return 0

    since = parse_date(args.since) if args.since else None
    if args.since and since is None:
        parser.error(f"--since inválido: {args.since!r} (formato YYYY-MM-DD)")

    report = run_collectors(args.db, names=args.sources, since=since, dry_run=args.dry_run)

    if not report:
        print("No hay colectores habilitados. Ver `--list` y "
              "`collectors.sources.<nombre>.enabled` en config/weights.yaml.")
        return 0

    modo = "simulacro (no se escribió nada)" if args.dry_run else "escritura"
    print(f"Adquisición — modo {modo} · base {args.db}")
    total = 0
    for name, inserted in sorted(report.items()):
        collector = registered().get(name)
        stats = getattr(collector, "stats", {}) or {}
        detalle = " ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        print(f"  {name:<28} {inserted:>5} filas nuevas   {detalle}")
        total += inserted
    print(f"\n  TOTAL {total} filas nuevas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
