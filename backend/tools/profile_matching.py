#!/usr/bin/env python
"""Perfilador del Competitive Product Matching Engine.

Por qué
-------
`run_matching` es O(productos_nike × productos_competidores). Con 45 productos
demo son 450 pares; con el catálogo real (~1.000 productos) son cientos de miles.
Antes de optimizar hace falta saber DÓNDE se va el tiempo — por fase y por
factor — y hace falta poder demostrar que optimizar no movió ningún score.

Qué mide
--------
1. **Fases**: ``build_context`` (SQL + índices en memoria) vs. ``scoring``
   (los 7 factores por par) vs. ``persistencia`` (DELETE + INSERT + JSON).
2. **Factores**: segundos y µs/par de cada uno de los siete, instrumentando
   ``matching.FACTOR_FUNCS`` (el desglose se mide en una pasada aparte para no
   contaminar el número limpio de scoring).
3. **Candidate generation**: cuántos pares descarta el prefiltro y qué pares
   persistibles se perderían (recall exacto contra el barrido completo).
4. **Regresión numérica**: vuelca el score de CADA par evaluado a JSON
   (``--dump-scores``) y lo compara contra un volcado previo (``--compare``).
   Es la prueba de que una optimización no cambió resultados.

Uso
---
    # perfil sobre la base demo
    python tools/profile_matching.py

    # perfil + volcado de scores (línea base ANTES de optimizar)
    python tools/profile_matching.py --dump-scores /tmp/before.json

    # después de optimizar: mismo comando y comparación exacta
    python tools/profile_matching.py --dump-scores /tmp/after.json \
        --compare /tmp/before.json

    # a escala: catálogo sintético de ~1.000 productos en una DB temporal
    python tools/profile_matching.py --scale 1000 --scale-db /tmp/scale.db \
        --sample-nike 20

    # top de funciones (cProfile)
    python tools/profile_matching.py --cprofile
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DB_PATH, section  # noqa: E402

SCRATCH_CSV = "pricing_scale_{products}p_{rows}r_{seed}.csv"


# ============================================================
# utilidades
# ============================================================

def _fmt_seconds(value: float) -> str:
    if value >= 60:
        return f"{value / 60:.1f} min"
    if value >= 1:
        return f"{value:.2f} s"
    return f"{value * 1000:.1f} ms"


class Phase:
    """Cronómetro de bloque."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.seconds = 0.0

    def __enter__(self) -> "Phase":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.seconds = time.perf_counter() - self._t0


def reset_caches() -> None:
    """Deja el proceso como recién arrancado.

    OJO: sin esto el perfil MIENTE. `app.services.embeddings` cachea en memoria
    la similitud de cada par de textos, así que una segunda pasada sobre los
    mismos pares no vuelve a calcular TF-IDF y mide 20x más rápido de lo que
    cuesta una corrida real (que ve cada par una sola vez).
    """
    try:
        from app.services import embeddings

        embeddings.reset_cache()
    except ImportError:  # el módulo es opcional
        pass


def warmup() -> None:
    """Paga UNA vez el costo de importar sklearn/numpy y de resolver el backend.

    Ese import (~1 s) es constante por proceso y no escala con los pares: si se
    lo cobra a la primera pasada, el perfil atribuye a `semantic` un tiempo que
    no depende del tamaño del catálogo.
    """
    try:
        from app.services import embeddings

        embeddings.text_similarity("calentando el backend de texto",
                                   "calentando el backend de texto uno")
    except ImportError:
        pass
    reset_caches()


def _pairs_of(matching: Any, ctx: Any, nike_products: list[dict]) -> list[tuple[dict, dict]]:
    """Los mismos pares que recorre ``run_matching`` (misma marca / mismo país)."""
    pairs: list[tuple[dict, dict]] = []
    for nike in nike_products:
        for comp in ctx.products.values():
            if comp["brand_id"] == nike["brand_id"]:
                continue
            if comp.get("country_code") != nike.get("country_code"):
                continue
            pairs.append((nike, comp))
    return pairs


# ============================================================
# medición
# ============================================================

def profile_db(db_path: Path | str, *, sample_nike: int | None = None,
               repeat: int = 1, dump_scores: str | None = None,
               with_factors: bool = True) -> dict[str, Any]:
    """Perfil completo sobre una base ya construida."""
    from app.services import matching

    report: dict[str, Any] = {"db": str(db_path)}
    warmup()

    # Cada pasada arranca EN FRÍO (ver `reset_caches`) y reconstruye el
    # contexto: así el número mide lo que cuesta una corrida real, no una
    # segunda vuelta sobre caches ya calientes.
    build_seconds = float("inf")
    scoring_seconds = float("inf")
    scores: dict[str, Any] = {}
    ctx = None
    for _ in range(max(1, repeat)):
        reset_caches()
        with Phase("build_context") as ph_build:
            ctx = matching.build_context(db_path)

        all_nike = [p for p in ctx.products.values() if p.get("brand_is_focus")]
        nike_products = all_nike[:sample_nike] if sample_nike else all_nike
        pairs = _pairs_of(matching, ctx, nike_products)

        with Phase("scoring") as ph_score:
            pass_scores = {}
            for nike, comp in pairs:
                result = matching.compute_match(nike, comp, ctx)
                pass_scores[f"{nike['id']}-{comp['id']}"] = {
                    "raw": round(result.score, 6),
                    "adjusted": round(matching.evidence_adjusted(result.score, result.coverage), 6),
                    "coverage": round(result.coverage, 6),
                    "confidence": result.confidence,
                    "factors": {f["factor"]: f["raw_score"] for f in result.factors},
                }
        build_seconds = min(build_seconds, ph_build.seconds)
        scoring_seconds = min(scoring_seconds, ph_score.seconds)
        scores = pass_scores

    all_nike = [p for p in ctx.products.values() if p.get("brand_is_focus")]
    competitors = [p for p in ctx.products.values() if not p.get("brand_is_focus")]
    nike_products = all_nike[:sample_nike] if sample_nike else all_nike
    sampled = len(nike_products) < len(all_nike)
    pairs = _pairs_of(matching, ctx, nike_products)
    full_grid_pairs = len(all_nike) * len(competitors)

    # -- desglose por factor (pasada instrumentada aparte, también en frío) ---
    factor_seconds: dict[str, float] = {}
    if with_factors:
        reset_caches()
        ctx = matching.build_context(db_path)
        pairs = _pairs_of(matching, ctx, [p for p in ctx.products.values()
                                          if p.get("brand_is_focus")][:sample_nike]
                          if sample_nike else
                          [p for p in ctx.products.values() if p.get("brand_is_focus")])
        totals = {name: 0.0 for name in matching.FACTOR_FUNCS}
        originals = dict(matching.FACTOR_FUNCS)
        perf = time.perf_counter
        for nike, comp in pairs:
            for name, func in originals.items():
                t0 = perf()
                func(nike, comp, ctx)
                totals[name] += perf() - t0
        factor_seconds = totals

    # -- run_matching completo (para aislar persistencia) --------------------
    stats = None
    total_seconds = None
    if not sampled:
        reset_caches()
        with Phase("run_matching") as ph_total:
            stats = matching.run_matching(db_path)
        total_seconds = ph_total.seconds

    persist_seconds = None
    if total_seconds is not None:
        persist_seconds = max(0.0, total_seconds - build_seconds - scoring_seconds)

    n_pairs = len(pairs)
    report.update({
        "nike_products": len(all_nike),
        "nike_products_profiled": len(nike_products),
        "competitor_products": len(competitors),
        "pairs_profiled": n_pairs,
        "pairs_full_grid": full_grid_pairs,
        "sampled": sampled,
        "phases": {
            "build_context": round(build_seconds, 4),
            "scoring": round(scoring_seconds, 4),
            "persist": round(persist_seconds, 4) if persist_seconds is not None else None,
            "total_run_matching": round(total_seconds, 4) if total_seconds is not None else None,
        },
        "per_pair_ms": round(1000.0 * scoring_seconds / n_pairs, 4) if n_pairs else None,
        "factors": {
            name: {
                "seconds": round(seconds, 4),
                "per_pair_us": round(1e6 * seconds / n_pairs, 2) if n_pairs else None,
                "pct_of_scoring": round(100.0 * seconds / sum(factor_seconds.values()), 1)
                if factor_seconds and sum(factor_seconds.values()) else None,
            }
            for name, seconds in sorted(factor_seconds.items(), key=lambda kv: -kv[1])
        },
        "run_matching_counts": stats,
    })

    if dump_scores:
        Path(dump_scores).write_text(
            json.dumps({"db": str(db_path), "pairs": len(scores), "scores": scores},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
        report["dumped_scores"] = dump_scores

    report["candidate_filter"] = candidate_filter_report(
        db_path, ctx=ctx, nike_products=nike_products, scores=scores)
    # Proyección con el costo por par medido acá y la tasa de descarte medida.
    skipped_pct = (report["candidate_filter"] or {}).get("skipped_pct") or 0.0
    report["projection"] = _projection(scoring_seconds / n_pairs if n_pairs else 0.0,
                                       ctx, skipped_pct / 100.0)
    return report


def _projection(seconds_per_pair: float, ctx: Any, skip_rate: float = 0.0) -> dict[str, Any]:
    """Proyección de tiempo para catálogos de 1.000 y 5.000 productos.

    Se asume la MISMA proporción Nike/competencia que la base perfilada. Se
    proyectan las dos: barrido completo y barrido con la tasa de descarte del
    prefiltro medida sobre esta base.
    """
    products = list(ctx.products.values())
    n_nike = sum(1 for p in products if p.get("brand_is_focus")) or 1
    share = n_nike / max(len(products), 1) or 0.4

    out: dict[str, Any] = {"seconds_per_pair": round(seconds_per_pair, 8),
                           "nike_share": round(share, 3),
                           "filter_skip_rate": round(skip_rate, 4)}
    for size in (1000, 5000):
        nike = max(1, round(size * share))
        pairs = nike * (size - nike)
        full = pairs * seconds_per_pair
        blocked = pairs * (1.0 - skip_rate) * seconds_per_pair
        out[str(size)] = {
            "pairs": pairs,
            "seconds": round(full, 1),
            "pretty": _fmt_seconds(full),
            "pairs_with_filter": int(pairs * (1.0 - skip_rate)),
            "seconds_with_filter": round(blocked, 1),
            "pretty_with_filter": _fmt_seconds(blocked),
        }
    return out


# ============================================================
# candidate generation
# ============================================================

def candidate_filter_report(db_path: Path | str, *, ctx: Any = None,
                            nike_products: list[dict] | None = None,
                            scores: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cuántos pares descarta el prefiltro y a qué costo de recall.

    Se miden DOS cosas distintas, porque "no perder un match" tiene dos lecturas:

    * **pares persistibles**: los que superan ``min_score_to_persist``. Es la
      cota de arriba (después el top-N recorta).
    * **matches persistidos**: los que realmente quedan en
      ``competitive_matches`` después del top-N. Es lo que ve el producto, y el
      número que no puede bajar.

    El recall se calcula contra el barrido COMPLETO usando los mismos scores, así
    que la comparación es exacta y no depende de volver a puntuar.
    """
    from app.services import matching

    if not hasattr(matching, "candidates_for"):
        return {"available": False,
                "reason": "matching.candidates_for no existe (prefiltro no implementado)"}

    ctx = ctx or matching.build_context(db_path)
    nike_products = nike_products or [p for p in ctx.products.values() if p.get("brand_is_focus")]
    cfg = section("competitive_match", default={}) or {}
    min_score = float(cfg.get("min_score_to_persist", 0.0))
    top_n = int(cfg.get("top_n_per_product", 0)) or None

    if scores is None:
        scores = {}
        for nike, comp in _pairs_of(matching, ctx, nike_products):
            result = matching.compute_match(nike, comp, ctx)
            scores[f"{nike['id']}-{comp['id']}"] = {
                "adjusted": matching.evidence_adjusted(result.score, result.coverage)}

    def _persisted(pairs_by_nike: dict[int, list[int]]) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for nike_id, comp_ids in pairs_by_nike.items():
            ranked = [(float(scores[f"{nike_id}-{cid}"]["adjusted"]), cid) for cid in comp_ids
                      if f"{nike_id}-{cid}" in scores]
            ranked = [row for row in ranked if row[0] >= min_score]
            ranked.sort(key=lambda row: row[0], reverse=True)
            for _score, cid in (ranked[:top_n] if top_n else ranked):
                out.add((nike_id, cid))
        return out

    full_by_nike: dict[int, list[int]] = {}
    blocked_by_nike: dict[int, list[int]] = {}
    skipped_pairs = 0
    unfiltered_products = 0
    filter_seconds = 0.0
    for nike in nike_products:
        full = [comp["id"] for comp in ctx.products.values()
                if comp["brand_id"] != nike["brand_id"]
                and comp.get("country_code") == nike.get("country_code")]
        t1 = time.perf_counter()
        block, skipped = matching.candidates_for(nike, ctx)
        filter_seconds += time.perf_counter() - t1
        full_by_nike[nike["id"]] = full
        blocked_by_nike[nike["id"]] = [comp["id"] for comp in block]
        skipped_pairs += skipped
        unfiltered_products += 1 if skipped == 0 else 0

    evaluated = sum(len(v) for v in full_by_nike.values())
    kept = sum(len(v) for v in blocked_by_nike.values())
    persistable = sum(1 for v in scores.values() if float(v["adjusted"]) >= min_score)
    persistable_kept = sum(
        1 for nike_id, comp_ids in blocked_by_nike.items() for cid in comp_ids
        if f"{nike_id}-{cid}" in scores
        and float(scores[f"{nike_id}-{cid}"]["adjusted"]) >= min_score)

    full_persisted = _persisted(full_by_nike)
    blocked_persisted = _persisted(blocked_by_nike)
    lost = sorted(full_persisted - blocked_persisted)
    names = ctx.products
    return {
        "available": True,
        "config": matching.candidate_filter_config(),
        "enabled_in_config": bool(matching.candidate_filter_config().get("enabled", True)),
        "pairs_full_grid": evaluated,
        "pairs_kept": kept,
        "pairs_skipped": skipped_pairs,
        "skipped_pct": round(100.0 * skipped_pairs / evaluated, 2) if evaluated else None,
        "products_without_filter": unfiltered_products,
        "persistable_pairs": persistable,
        "persistable_lost": persistable - persistable_kept,
        "persisted_matches": len(full_persisted),
        "persisted_lost": len(lost),
        "persisted_added": len(blocked_persisted - full_persisted),
        "recall_pct": round(100.0 * len(blocked_persisted & full_persisted) / len(full_persisted), 2)
        if full_persisted else None,
        "lost_examples": [
            {"nike": a, "competitor": b,
             "nike_name": names[a].get("product_name") if a in names else None,
             "competitor_name": names[b].get("product_name") if b in names else None,
             "score": round(float(scores[f"{a}-{b}"]["adjusted"]), 2)}
            for a, b in lost[:10]
        ],
        "filter_seconds": round(filter_seconds, 4),
        "filter_per_pair_us": round(1e6 * filter_seconds / evaluated, 2) if evaluated else None,
    }


# ============================================================
# comparación de scores (regresión numérica)
# ============================================================

def compare_scores(before_path: str | Path, after: dict[str, Any],
                   tolerance: float = 0.0) -> dict[str, Any]:
    """Compara dos volcados de scores par a par."""
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))["scores"]
    keys_before, keys_after = set(before), set(after)

    diffs: list[dict[str, Any]] = []
    max_delta = 0.0
    persisted_diffs = 0
    for key in keys_before & keys_after:
        a, b = before[key], after[key]
        for field in ("raw", "adjusted", "coverage"):
            delta = abs(float(a[field]) - float(b[field]))
            max_delta = max(max_delta, delta)
            # `competitive_matches` guarda 4 decimales: ésa es la precisión en la
            # que "el score no cambió" tiene que ser exacto.
            if round(float(a[field]), 4) != round(float(b[field]), 4):
                persisted_diffs += 1
            if delta > tolerance:
                diffs.append({"pair": key, "field": field,
                              "before": a[field], "after": b[field],
                              "delta": round(delta, 8)})
        if a["confidence"] != b["confidence"]:
            diffs.append({"pair": key, "field": "confidence",
                          "before": a["confidence"], "after": b["confidence"]})
    return {
        "pairs_before": len(keys_before),
        "pairs_after": len(keys_after),
        "missing_after": sorted(keys_before - keys_after)[:10],
        "new_after": sorted(keys_after - keys_before)[:10],
        "max_abs_delta": max_delta,
        "differences": diffs[:20],
        "n_differences": len(diffs),
        "persisted_differences": persisted_diffs,
        "identical_persisted": persisted_diffs == 0 and keys_before == keys_after,
        "identical": not diffs and keys_before == keys_after,
    }


# ============================================================
# catálogo sintético a escala
# ============================================================

def build_scale_db(products: int, db_path: Path | str, *, rows: int = 70_000,
                   seed: int = 20260816, csv_path: Path | str | None = None) -> dict[str, Any]:
    """Genera un catálogo sintético de ~N productos y lo deja listo para medir.

    Reusa `tools/generate_scale_fixture.py` (CSV con la misma suciedad del dato
    real) + `app.ingest.ingest_from_csv` + `enrichment`: la base resultante pasa
    por el mismo camino que el dato de producción.
    """
    from app.ingest import ingest_from_csv
    from app.services.enrichment import run_enrichment

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_scale_fixture as fixture  # type: ignore

    # El nombre lleva el tamaño: reusar el CSV de otra escala fue un pie en el
    # que ya se tropezó (pedís 200 productos y medís sobre 1.000).
    csv_path = Path(csv_path or (Path(db_path).parent / SCRATCH_CSV.format(
        products=products, rows=rows, seed=seed)))
    if not csv_path.exists():
        written = fixture.write_csv(
            csv_path,
            fixture.generate_rows(rows=rows, products=products, retailers=10,
                                  dates=5, seed=seed),
        )
    else:
        written = -1

    ingest = ingest_from_csv(csv_path, db_path, country="AR", drop=True)
    signals = add_synthetic_signals(db_path, seed=seed)
    enrich = run_enrichment(db_path=db_path)
    return {"csv": str(csv_path), "csv_rows": written,
            "products": ingest.get("products"), "nike": ingest.get("products_nike"),
            "competitors": ingest.get("products_competitor"),
            "attributes": enrich.get("attributes"), **signals}


# Vocabulario para las descripciones sintéticas. No es marketing: es material
# para que TF-IDF tenga algo que morder (el `pricing_data` real no trae
# descripción, y sin ella el factor semántico mide la mitad de lo que cuesta).
_DESC_OPENERS = ("zapatilla", "calzado", "modelo", "silueta")
_DESC_MID = ("con amortiguacion reactiva", "de pisada neutra", "con placa de carbono",
             "con mediasuela de espuma", "con upper de malla tecnica",
             "pensada para entrenamiento diario", "para ritmos rapidos en asfalto")
_DESC_TAIL = ("liviana y transpirable", "estable y duradera", "comoda para uso diario",
              "con agarre en superficies mixtas", "de perfil bajo y calce ajustado")
_REVIEW_TEXTS = ("Muy comoda y liviana, la amortiguacion es excelente.",
                 "El calce es ajustado, conviene un numero mas.",
                 "Buena durabilidad y agarre, aunque el precio es caro.",
                 "Transpirable y estable, ideal para entrenamiento diario.",
                 "El diseno es lindo pero la calidad de terminaciones es regular.")


def add_synthetic_signals(db_path: Path | str, *, seed: int = 20260816) -> dict[str, int]:
    """Agrega descripciones, reviews, menciones editoriales y sociales.

    El `pricing_data` sintético sólo trae precio y stock: sin estas señales el
    perfil a escala apagaría cuatro de los siete factores y mediría un motor que
    no es el que corre en producción.
    """
    import random

    from app.db import get_conn

    rng = random.Random(seed)
    with get_conn(db_path) as conn:
        products = [dict(r) for r in conn.execute(
            "SELECT id, product_name, use_case, brand_id FROM products ORDER BY id")]

        conn.executemany(
            "UPDATE products SET description = ? WHERE id = ?",
            [(f"{rng.choice(_DESC_OPENERS)} {p['product_name']} "
              f"{rng.choice(_DESC_MID)}, {rng.choice(_DESC_TAIL)}. "
              f"{p.get('use_case') or ''}".strip(), p["id"])
             for p in products],
        )

        reviews = [
            (p["id"], round(rng.uniform(3.0, 5.0), 1), rng.choice(_REVIEW_TEXTS),
             rng.randint(1, 40))
            for p in products for _ in range(rng.randint(0, 6))
        ]
        conn.executemany(
            "INSERT INTO reviews (product_id, source, rating, review_text, review_count) "
            "VALUES (?, 'synthetic', ?, ?, ?)", reviews)

        ids = [p["id"] for p in products]
        editorial = []
        for i in range(2_000):
            a, b = rng.choice(ids), rng.choice(ids)
            if a == b:
                continue
            editorial.append((f"Medio {i % 20}", f"{a} vs {b}",
                              rng.choice(("versus", "alternative", "ranking", "review")),
                              a, b, f"lista-{i % 120}", "AR"))
        conn.executemany(
            "INSERT INTO editorial_mentions "
            "(source_name, title, mention_type, product_a_id, product_b_id, list_key, country_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", editorial)

        social = []
        for _ in range(2_000):
            a, b = rng.choice(ids), rng.choice(ids)
            if a == b:
                continue
            social.append(("2026-06-01", "2026-07-01", "AR", a, b, "forum",
                           rng.randint(5, 200), rng.randint(1, 30)))
        conn.executemany(
            "INSERT INTO social_mention_aggregates "
            "(period_start, period_end, country_code, product_id, co_product_id, "
            " source_type, mention_count, comention_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            social)

    return {"descriptions": len(products), "reviews": len(reviews),
            "editorial_mentions": len(editorial), "social_aggregates": len(social)}


# ============================================================
# salida
# ============================================================

def print_report(report: dict[str, Any]) -> None:
    ph = report["phases"]
    print(f"\n=== Perfil de matching — {report['db']} ===\n")
    print(f"  catálogo : {report['nike_products']} Nike · "
          f"{report['competitor_products']} competidores")
    print(f"  pares    : {report['pairs_profiled']:,} perfilados "
          f"(grilla completa {report['pairs_full_grid']:,})"
          + ("  [MUESTREADO]" if report["sampled"] else ""))
    print(f"\n  {'fase':<22} {'segundos':>10} {'%':>7}")
    print(f"  {'-' * 22} {'-' * 10} {'-' * 7}")
    base = ph["total_run_matching"] or (ph["build_context"] + ph["scoring"])
    for name in ("build_context", "scoring", "persist", "total_run_matching"):
        value = ph.get(name)
        if value is None:
            continue
        share = 100.0 * value / base if base else 0.0
        print(f"  {name:<22} {value:>10.3f} {share:>6.1f}%")
    if report.get("per_pair_ms"):
        print(f"\n  scoring por par: {report['per_pair_ms']:.3f} ms")

    if report.get("factors"):
        print(f"\n  {'factor':<20} {'segundos':>10} {'µs/par':>10} {'% scoring':>11}")
        print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 11}")
        for name, info in report["factors"].items():
            print(f"  {name:<20} {info['seconds']:>10.3f} {info['per_pair_us']:>10.1f} "
                  f"{(info['pct_of_scoring'] or 0):>10.1f}%")

    cf = report.get("candidate_filter") or {}
    if cf.get("available"):
        print(f"\n  prefiltro (candidate generation, enabled={cf['enabled_in_config']}):")
        print(f"    descarta {cf['pairs_skipped']:,} / {cf['pairs_full_grid']:,} pares "
              f"({cf['skipped_pct']}%)  ·  {cf['filter_per_pair_us']} µs/par de grilla")
        print(f"    productos Nike que igual barrieron todo (red de seguridad): "
              f"{cf['products_without_filter']}")
        print(f"    pares persistibles (>= min_score): {cf['persistable_pairs']:,}  "
              f"perdidos: {cf['persistable_lost']}")
        print(f"    matches persistidos (top-N): {cf['persisted_matches']:,}  "
              f"perdidos: {cf['persisted_lost']}  nuevos: {cf['persisted_added']}  "
              f"recall: {cf['recall_pct']}%")
        for row in cf.get("lost_examples", []):
            print(f"      ! perdido {row['nike_name']} vs {row['competitor_name']} "
                  f"(score {row['score']})")
    elif cf:
        print(f"\n  prefiltro: {cf.get('reason')}")

    proj = report.get("projection") or {}
    if proj:
        print(f"\n  proyección (costo medido: {proj['seconds_per_pair'] * 1e6:.1f} µs/par, "
              f"{proj['nike_share']:.0%} del catálogo es Nike, "
              f"prefiltro descarta {proj['filter_skip_rate']:.0%}):")
        print(f"    {'catálogo':>10} {'pares':>12} {'barrido completo':>18} "
              f"{'pares c/prefiltro':>18} {'con prefiltro':>15}")
        for size in ("1000", "5000"):
            info = proj.get(size)
            if info:
                print(f"    {int(size):>10,} {info['pairs']:>12,} {info['pretty']:>18} "
                      f"{info['pairs_with_filter']:>18,} {info['pretty_with_filter']:>15}")

    if report.get("comparison"):
        cmp_ = report["comparison"]
        status = ("IDÉNTICOS" if cmp_["identical_persisted"]
                  else f"{cmp_['persisted_differences']} DIFERENCIAS")
        print(f"\n  regresión numérica: {status} a 4 decimales (lo que se persiste) — "
              f"max |Δ| = {cmp_['max_abs_delta']:.2e} sobre {cmp_['pairs_after']} pares"
              f" · fuera de tolerancia: {cmp_['n_differences']}")
        for row in cmp_["differences"][:5]:
            print(f"      ! {row}")


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/profile_matching.py",
        description="Perfila run_matching por fase y por factor; compara scores",
    )
    parser.add_argument("--db", default=str(DB_PATH), help="Base a perfilar")
    parser.add_argument("--scale", type=int, metavar="N",
                        help="Construir un catálogo sintético de N productos y perfilar ahí")
    parser.add_argument("--scale-db", help="Ruta de la base sintética (default: <db>.scale.db)")
    parser.add_argument("--scale-rows", type=int, default=70_000,
                        help="Filas del pricing_data sintético (default 70000)")
    parser.add_argument("--sample-nike", type=int, metavar="N",
                        help="Perfilar sólo los primeros N productos Nike (para escalas grandes)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Repetir la fase de scoring y quedarse con el mejor tiempo")
    parser.add_argument("--no-factors", action="store_true",
                        help="Saltear el desglose por factor (pasada extra)")
    parser.add_argument("--dump-scores", help="Volcar el score de cada par a este JSON")
    parser.add_argument("--compare", help="Comparar los scores contra un volcado previo")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="Tolerancia absoluta al comparar (default 0: exacto)")
    parser.add_argument("--cprofile", action="store_true",
                        help="Imprimir el top de funciones acumuladas (cProfile)")
    parser.add_argument("--json", help="Escribir el reporte completo a este JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db_path = args.db
    scale_info = None
    if args.scale:
        db_path = args.scale_db or f"{args.db}.scale.db"
        print(f"\n  construyendo catálogo sintético de {args.scale} productos en {db_path} ...",
              flush=True)
        scale_info = build_scale_db(args.scale, db_path, rows=args.scale_rows)
        print(f"  listo: {scale_info['products']} productos "
              f"({scale_info['nike']} Nike / {scale_info['competitors']} competencia)")

    run: Callable[[], dict[str, Any]] = lambda: profile_db(  # noqa: E731
        db_path, sample_nike=args.sample_nike, repeat=args.repeat,
        dump_scores=args.dump_scores, with_factors=not args.no_factors,
    )

    if args.cprofile:
        profiler = cProfile.Profile()
        profiler.enable()
        report = run()
        profiler.disable()
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(25)
        report["cprofile"] = stream.getvalue()
    else:
        report = run()

    if scale_info:
        report["scale_fixture"] = scale_info

    if args.compare and args.dump_scores:
        after = json.loads(Path(args.dump_scores).read_text(encoding="utf-8"))["scores"]
        report["comparison"] = compare_scores(args.compare, after, tolerance=args.tolerance)

    print_report(report)
    if report.get("cprofile"):
        print("\n=== cProfile (tottime) ===\n")
        print(report["cprofile"])

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                   encoding="utf-8")
        print(f"\n  reporte JSON: {args.json}")

    comparison = report.get("comparison")
    return 1 if comparison and not comparison["identical_persisted"] else 0


if __name__ == "__main__":
    sys.exit(main())
