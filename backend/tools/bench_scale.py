#!/usr/bin/env python
"""Mide el motor END TO END a escala real y encuentra el cuello de botella.

Qué corre
---------
    ingest -> enrichment -> matching -> shelf_signals -> brand_intelligence
           -> opportunities -> retail_media

y reporta segundos y conteos por etapa. Con ``--blocking`` corre además un
PROTOTIPO de *candidate generation* para el matching (bloqueo por país +
categoría + segmento) y compara tiempo y resultado contra el barrido completo.
El prototipo vive acá y NO toca `app/services/matching.py`, que es de otro
dueño: la idea es tener el número que respalde el cambio, no hacerlo por
sorpresa.

Uso
---
    # 1. generar y cargar el fixture sintético
    python tools/generate_scale_fixture.py --dsn "$SCALE_DSN"

    # 2. medir
    python tools/bench_scale.py --dsn "$SCALE_DSN" --db /tmp/scale.db --blocking

    # sólo re-medir el matching sobre una base ya construida
    python tools/bench_scale.py --db /tmp/scale.db --stage matching --blocking
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH, section  # noqa: E402

# Etapas del pipeline, en orden. `ingest` se agrega adelante si hay `--dsn`.
STAGES: tuple[tuple[str, str, str], ...] = (
    ("enrichment",         "app.services.enrichment",         "run_enrichment"),
    ("matching",           "app.services.matching",           "run_matching"),
    ("shelf_signals",      "app.services.shelf",              "run_shelf_signals"),
    ("brand_intelligence", "app.services.brand_intelligence", "run_brand_intelligence"),
    ("opportunities",      "app.services.opportunities",      "run_opportunities"),
    ("retail_media",       "app.services.retail_media",       "run_retail_media"),
)


class Timer:
    """Cronómetro con reporte acumulado."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def run(self, name: str, func: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        status, result, error = "ok", None, None
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 - una etapa rota no frena la medición
            status, error = "error", f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        self.rows.append({"stage": name, "seconds": round(elapsed, 3),
                          "status": status, "counts": result, "error": error})
        marker = "OK " if status == "ok" else "ERR"
        print(f"  [{marker}] {name:<20} {elapsed:>9.2f}s  {_short(result) if result else (error or '')}",
              flush=True)
        return result

    @property
    def total(self) -> float:
        return sum(r["seconds"] for r in self.rows)

    def report(self) -> None:
        print(f"\n  {'etapa':<20} {'segundos':>10} {'% del total':>12}")
        print(f"  {'-' * 20} {'-' * 10:>10} {'-' * 12:>12}")
        for r in sorted(self.rows, key=lambda x: -x["seconds"]):
            share = 100.0 * r["seconds"] / self.total if self.total else 0.0
            print(f"  {r['stage']:<20} {r['seconds']:>10.2f} {share:>11.1f}%")
        print(f"  {'TOTAL':<20} {self.total:>10.2f} {100.0:>11.1f}%")


def _short(value: Any, limit: int = 110) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ============================================================
# Prototipo de candidate generation (bloqueo) para el matching
# ============================================================

#: Campos "de uso" del producto. Son los que el scoring semántico pondera más
#: fuerte (use_case 0.30, sport 0.10, subcategory 0.05 de `field_weights`).
_USE_FIELDS: tuple[str, ...] = ("use_case", "sport", "subcategory")


def _norm(value: Any) -> str | None:
    text = str(value).strip().lower() if value not in (None, "") else ""
    return text or None


def _use_keys(product: dict) -> set[tuple[str, str]]:
    """Claves de uso del producto (las que definen su bloque)."""
    return {(field, _norm(product.get(field))) for field in _USE_FIELDS
            if _norm(product.get(field))}


def is_candidate(nike: dict, comp: dict) -> bool:
    """¿Vale la pena puntuar este par? (candidate generation)

    Dos condiciones, las dos derivadas de lo que el propio scoring ya considera
    decisivo — no son heurísticas nuevas:

    1. **Misma `category`** (footwear / apparel / accessories).
       `matching._score_semantic` ya aplica ``hard_mismatch_penalty`` cuando no
       coinciden: una mochila contra una zapatilla no va a ser nunca un match
       persistible. Si a alguno le falta la categoría no se filtra (no se
       descarta por falta de dato).
    2. **Comparten al menos un campo de uso** (``use_case`` | ``sport`` |
       ``subcategory``). Se pide UNO, no todos, para que un producto con
       ``use_case`` en NULL siga bloqueando por ``sport``.

    Y se descartan además los pares con **conflicto duro de género**
    (`matching._gender_conflict`): men vs. women, que el scoring ya penaliza.
    """
    from app.services import matching

    cat_a, cat_b = _norm(nike.get("category")), _norm(comp.get("category"))
    if cat_a and cat_b and cat_a != cat_b:
        return False
    if not (_use_keys(nike) & _use_keys(comp)):
        return False
    return not matching._gender_conflict(nike.get("gender"), comp.get("gender"))


def candidates_for(nike: dict, index: dict[tuple, list[dict]]) -> list[dict]:
    """Competidores a puntuar para un producto Nike, vía índice invertido.

    El índice es por ``(país, campo_de_uso, valor)``: recorrerlo cuesta O(tamaño
    del bloque), no O(catálogo). El filtro fino (categoría, género, marca) se
    aplica después sobre ese conjunto ya reducido — sigue siendo aritmética
    barata comparada con `compute_match`.
    """
    seen: dict[int, dict] = {}
    country = nike.get("country_code")
    for field, value in _use_keys(nike):
        for comp in index.get((country, field, value), ()):
            if comp["id"] in seen or comp["brand_id"] == nike["brand_id"]:
                continue
            if is_candidate(nike, comp):
                seen[comp["id"]] = comp
    return list(seen.values())


def build_candidate_index(ctx: Any) -> dict[tuple, list[dict]]:
    index: dict[tuple, list[dict]] = defaultdict(list)
    for product in ctx.products.values():
        if product.get("brand_is_focus"):
            continue
        country = product.get("country_code")
        for field, value in _use_keys(product):
            index[(country, field, value)].append(product)
    return index


def run_matching_blocked(db_path: Path | str) -> dict[str, Any]:
    """`run_matching` con candidate generation. NO persiste: sólo mide y devuelve
    los pares que sobrevivirían (para comparar el conjunto, no sólo el conteo)."""
    from app.services import matching

    cfg = section("competitive_match", default={}) or {}
    min_score = float(cfg.get("min_score_to_persist", 0.0))
    top_n = int(cfg.get("top_n_per_product", 0)) or None

    ctx = matching.build_context(db_path)
    nike_products = [p for p in ctx.products.values() if p.get("brand_is_focus")]
    competitors = [p for p in ctx.products.values() if not p.get("brand_is_focus")]
    index = build_candidate_index(ctx)

    pairs_evaluated = 0
    pairs: set[tuple[int, int]] = set()
    for nike in nike_products:
        scored: list[tuple[float, int]] = []
        for comp in candidates_for(nike, index):
            if comp.get("country_code") != nike.get("country_code"):
                continue
            pairs_evaluated += 1
            result = matching.compute_match(nike, comp, ctx)
            adjusted = matching.evidence_adjusted(result.score, result.coverage)
            if adjusted >= min_score:
                scored.append((adjusted, comp["id"]))
        scored.sort(reverse=True)
        for _, comp_id in (scored[:top_n] if top_n else scored):
            pairs.add((nike["id"], comp_id))

    full_grid = len(nike_products) * len(competitors)
    return {
        "nike_products": len(nike_products),
        "competitor_products": len(competitors),
        "pairs_evaluated": pairs_evaluated,
        "pairs_full_grid": full_grid,
        "pairs_skipped": full_grid - pairs_evaluated,
        "matches": len(pairs),
        "_pairs": pairs,
    }


def compare_matching(db_path: Path | str) -> dict[str, Any]:
    """Barrido completo vs. bloqueo: tiempo, pares evaluados y recall real."""
    from app.db import query
    from app.services import matching

    started = time.perf_counter()
    full = matching.run_matching(db_path=db_path)
    full_seconds = time.perf_counter() - started
    full_pairs = {(r["nike_product_id"], r["competitor_product_id"])
                  for r in query("SELECT nike_product_id, competitor_product_id "
                                 "FROM competitive_matches", path=db_path)}

    started = time.perf_counter()
    blocked = run_matching_blocked(db_path)
    blocked_seconds = time.perf_counter() - started
    blocked_pairs = blocked.pop("_pairs")

    # Cuántos de los pares persistidos por el barrido completo llegaron siquiera
    # a evaluarse con el bloqueo (recall de candidatos: el número que importa —
    # un par que no se evalúa no puede ganar).
    ctx = matching.build_context(db_path)
    reachable = sum(1 for a, b in full_pairs
                    if a in ctx.products and b in ctx.products
                    and is_candidate(ctx.products[a], ctx.products[b]))

    return {
        "full": {**full, "seconds": round(full_seconds, 3)},
        "blocked": {**blocked, "seconds": round(blocked_seconds, 3)},
        "speedup": round(full_seconds / blocked_seconds, 2) if blocked_seconds else None,
        "pair_reduction_pct": round(
            100.0 * (1 - blocked["pairs_evaluated"] / max(blocked["pairs_full_grid"], 1)), 2),
        "candidate_recall_pct": round(100.0 * reachable / len(full_pairs), 2) if full_pairs else None,
        "matches_recall_pct": round(100.0 * len(full_pairs & blocked_pairs) / len(full_pairs), 2)
        if full_pairs else None,
        "matches_lost": len(full_pairs - blocked_pairs),
        "matches_added": len(blocked_pairs - full_pairs),
    }


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/bench_scale.py",
        description="Corre el motor end to end a escala y reporta tiempos por etapa",
    )
    parser.add_argument("--dsn", help="Postgres con el `pricing_data` sintético (etapa ingest)")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite destino")
    parser.add_argument("--country", default="AR")
    parser.add_argument("--stage", action="append",
                        help="Correr sólo estas etapas (repetible)")
    parser.add_argument("--blocking", action="store_true",
                        help="Comparar el matching completo contra el prototipo con bloqueo")
    parser.add_argument("--json", help="Escribir el reporte a este archivo")
    return parser


def main(argv: list[str] | None = None) -> int:
    import importlib

    args = build_parser().parse_args(argv)
    only = set(args.stage or [])
    timer = Timer()

    print(f"\n=== Benchmark a escala — base {args.db} ===\n")

    if args.dsn and (not only or "ingest" in only):
        from app.ingest import ingest_from_postgres

        timer.run("ingest", lambda: ingest_from_postgres(
            args.dsn, args.db, country=args.country, drop=True))

    for name, module_name, func_name in STAGES:
        if only and name not in only:
            continue
        func = getattr(importlib.import_module(module_name), func_name)
        timer.run(name, lambda f=func: f(db_path=args.db))

    timer.report()

    comparison = None
    if args.blocking:
        print("\n=== matching: barrido completo vs. candidate generation ===\n")
        comparison = compare_matching(args.db)
        full, blocked = comparison["full"], comparison["blocked"]
        print(f"  completo : {full['seconds']:>8.2f}s  "
              f"{full['pairs_evaluated']:>9,} pares  {full['matches']:>7,} matches")
        print(f"  bloqueado: {blocked['seconds']:>8.2f}s  "
              f"{blocked['pairs_evaluated']:>9,} pares  {blocked['matches']:>7,} matches")
        print(f"  speedup  : {comparison['speedup']}x   "
              f"pares evitados: {comparison['pair_reduction_pct']}%")
        print(f"  recall   : candidatos {comparison['candidate_recall_pct']}%  "
              f"matches persistidos {comparison['matches_recall_pct']}%  "
              f"(perdidos {comparison['matches_lost']}, nuevos {comparison['matches_added']})")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"stages": timer.rows, "total_seconds": round(timer.total, 3),
                        "matching_comparison": comparison},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\nReporte JSON: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
