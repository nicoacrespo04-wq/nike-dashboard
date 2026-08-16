"""Retail Media Opportunity Engine.

Responde la pregunta que un dashboard de scraping no puede responder:
*¿conviene invertir en visibilidad (retail media) o en descuento?*

Score 0..100 con los 7 pesos de ``retail_media.weights`` y una recomendación
accionable elegida con los umbrales de ``retail_media.thresholds``:

  1. INVEST_IN_RETAIL_MEDIA               stock alto + precio competitivo + competidor con momentum
  2. EVALUATE_PRICE_ACTION_BEFORE_MEDIA   stock alto pero precio muy por encima del competidor
  3. DO_NOT_INCREASE_MEDIA                stock bajo + demanda alta
  4. CAPTURE_COMPETITOR_STOCKOUT          competidor en quiebre + Nike con stock y precio competitivo
  5. PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN  todo lo anterior + producto relevante y bajo share of shelf

Todo resultado es explicable: los drivers persistidos incluyen los valores que
dispararon la decisión (stock Nike, relevancia competitiva, momentum del
competidor, gap de precio %, share of shelf) más la confianza por cobertura.
"""

from __future__ import annotations

from typing import Any

from app.config import DB_PATH, section, weights
from app.db import get_conn
from app.services import scoring
from app.services.opportunities import IntelContext, price_comparison, build_context
from app.services.common import CompositeScore, Factor, clamp, combine, to_json

INVEST_IN_RETAIL_MEDIA = "INVEST_IN_RETAIL_MEDIA"
EVALUATE_PRICE_ACTION_BEFORE_MEDIA = "EVALUATE_PRICE_ACTION_BEFORE_MEDIA"
DO_NOT_INCREASE_MEDIA = "DO_NOT_INCREASE_MEDIA"
CAPTURE_COMPETITOR_STOCKOUT = "CAPTURE_COMPETITOR_STOCKOUT"
PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN = "PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN"


def _thresholds() -> dict[str, float]:
    raw = section("retail_media", "thresholds", default={}) or {}
    return {k: float(v) for k, v in raw.items()}


def _fmt(value: float | None, digits: int = 0) -> str:
    return "s/d" if value is None else f"{value:.{digits}f}"


# ── señales ─────────────────────────────────────────────────


def build_signals(nike_product: dict, competitor_product: dict | None,
                  retailer: dict | None, ctx: IntelContext) -> dict[str, Any]:
    """Todas las señales crudas del triplete (Nike, competidor, retailer)."""
    nike_id = int(nike_product["id"])
    comp_id = int(competitor_product["id"]) if competitor_product else None
    rid = int(retailer["id"]) if retailer else None

    nike_stock = ctx.availability(nike_id, rid)
    if nike_stock is None:
        nike_stock = ctx.availability(nike_id)
    comp_stock = None
    if comp_id is not None:
        comp_stock = ctx.availability(comp_id, rid)
        if comp_stock is None:
            comp_stock = ctx.availability(comp_id)

    price_gap = None
    price_detail: dict[str, Any] = {}
    if comp_id is not None:
        comparison = price_comparison(ctx, nike_id, comp_id)
        price_gap = comparison["gap_pct"]          # >0 => Nike más caro que el competidor
        price_detail = {"basis": comparison["basis"]}
        if rid is not None:
            for row in comparison["per_retailer"]:
                if row["retailer_id"] == rid:
                    price_gap = row["gap_pct"]
                    price_detail = {"basis": f"precios en {row['retailer']}",
                                    "nike_price": row["nike_price"],
                                    "competitor_price": row["competitor_price"]}
                    break

    match_score = ctx.match_score(nike_id, comp_id) if comp_id is not None else None
    comp_momentum = ctx.momentum(comp_id)["value"] if comp_id is not None else None
    nike_momentum = ctx.momentum(nike_id)["value"]
    shelf_share = ctx.shelf_share(nike_id)

    importance_inputs = ctx.importance_inputs(
        nike_id, competitor_id=comp_id,
        retailer_ids=[rid] if rid is not None else None,
        price_gap_pct=price_gap,
    )
    importance = scoring.business_importance(importance_inputs, ctx)

    demand_candidates = [m for m in (comp_momentum, nike_momentum) if m is not None]
    return {
        "nike_product_id": nike_id,
        "competitor_product_id": comp_id,
        "retailer_id": rid,
        "country_code": nike_product.get("country_code") or (retailer or {}).get("country_code"),
        "nike_stock_pct": nike_stock,
        "competitor_stock_pct": comp_stock,
        "price_gap_pct": price_gap,
        "price_detail": price_detail,
        "match_score": match_score,
        "competitor_momentum": comp_momentum,
        "nike_momentum": nike_momentum,
        "demand_signal": max(demand_candidates) if demand_candidates else None,
        "nike_shelf_share": shelf_share,
        "business_importance": importance.score,
        "business_importance_confidence": importance.confidence,
        "nike_discount_pct": ctx.avg_discount(nike_id),
    }


def _price_competitiveness(gap_pct: float | None, th: dict[str, float]) -> float | None:
    """1 = Nike ya competitivo en precio; 0 = desventaja de precio plena."""
    if gap_pct is None:
        return None
    competitive = th.get("price_competitive_pct", 0.0)
    disadvantage = th.get("price_disadvantage_pct", competitive)
    if gap_pct <= competitive:
        return 1.0
    if disadvantage <= competitive or gap_pct >= disadvantage:
        return 0.0
    return clamp((disadvantage - gap_pct) / (disadvantage - competitive))


def score_from_signals(signals: dict[str, Any]) -> CompositeScore:
    """Combina los 7 factores de ``retail_media.weights``."""
    w = weights("retail_media", "weights")
    th = _thresholds()

    stock = signals.get("nike_stock_pct")
    comp_stock = signals.get("competitor_stock_pct")
    match_score = signals.get("match_score")
    shelf_share = signals.get("nike_shelf_share")
    importance = signals.get("business_importance")

    raw: dict[str, float | None] = {
        "nike_stock_health": clamp(stock / 100.0) if stock is not None else None,
        "price_competitiveness": _price_competitiveness(signals.get("price_gap_pct"), th),
        "competitive_relevance": clamp(match_score / 100.0) if match_score is not None else None,
        "business_importance": clamp(importance / 100.0) if importance is not None else None,
        "competitor_momentum": signals.get("competitor_momentum"),
        "shelf_gap": clamp(1.0 - clamp(shelf_share)) if shelf_share is not None else None,
        "competitor_stock_gap": clamp(1.0 - clamp(comp_stock / 100.0)) if comp_stock is not None else None,
    }
    detail: dict[str, dict] = {
        "nike_stock_health": {"nike_stock_pct": stock},
        "price_competitiveness": {"price_gap_pct": signals.get("price_gap_pct"),
                                  **(signals.get("price_detail") or {})},
        "competitive_relevance": {"match_score": match_score},
        "business_importance": {"score": importance},
        "competitor_momentum": {"momentum": signals.get("competitor_momentum")},
        "shelf_gap": {"nike_shelf_share": shelf_share},
        "competitor_stock_gap": {"competitor_stock_pct": comp_stock},
    }

    factors = [Factor(name, raw.get(name), float(weight), detail.get(name, {}))
               for name, weight in w.items()]
    return combine(factors, section("retail_media", "confidence_thresholds"))


# ── decisión ────────────────────────────────────────────────


def decide(signals: dict[str, Any]) -> tuple[str, str]:
    """Los 5 casos del brief. Devuelve (recomendación, razón en español)."""
    th = _thresholds()
    stock = signals.get("nike_stock_pct")
    comp_stock = signals.get("competitor_stock_pct")
    gap = signals.get("price_gap_pct")
    momentum = signals.get("competitor_momentum")
    demand = signals.get("demand_signal")
    shelf_share = signals.get("nike_shelf_share")
    importance = signals.get("business_importance")

    high_stock = th.get("nike_stock_high_pct", 100.0)
    low_stock = th.get("nike_stock_low_pct", 0.0)
    competitive_pct = th.get("price_competitive_pct", 0.0)
    disadvantage_pct = th.get("price_disadvantage_pct", competitive_pct)
    stockout_pct = th.get("competitor_stockout_pct", 0.0)
    high_momentum = th.get("high_momentum", 1.0)
    # "Share of shelf bajo" reutiliza el umbral declarado en config para
    # participación insuficiente de Nike en un segmento.
    low_shelf = float(section("opportunities", "assortment_white_space", "max_nike_share",
                              default=0.0) or 0.0)
    # "Producto relevante" = importancia de negocio por encima de LOW.
    relevant_min = float(section("business_importance", "severity_thresholds", "medium",
                                 default=0.0) or 0.0)

    price_ok = gap is not None and gap <= competitive_pct
    price_bad = gap is not None and gap >= disadvantage_pct
    stock_high = stock is not None and stock >= high_stock
    stock_low = stock is not None and stock <= low_stock
    hot = momentum is not None and momentum >= high_momentum
    demand_high = demand is not None and demand >= high_momentum
    comp_out = comp_stock is not None and comp_stock <= stockout_pct
    shelf_low = shelf_share is not None and shelf_share <= low_shelf
    relevant = importance is not None and importance >= relevant_min

    # 3. Stock bajo + demanda alta: nunca generar demanda sobre inventario insuficiente.
    if stock_low and demand_high:
        return DO_NOT_INCREASE_MEDIA, (
            f"El stock Nike está en {_fmt(stock)}% (por debajo del piso de "
            f"{_fmt(low_stock)}%) con demanda alta ({demand:.2f}): invertir en visibilidad "
            f"generaría tráfico sobre inventario insuficiente. Primero reponer."
        )

    # 4. Competidor en quiebre + Nike con stock y precio competitivo.
    if comp_out and stock is not None and stock > low_stock and price_ok:
        return CAPTURE_COMPETITOR_STOCKOUT, (
            f"El competidor cayó a {_fmt(comp_stock)}% de disponibilidad (umbral de quiebre "
            f"{_fmt(stockout_pct)}%) mientras Nike sostiene {_fmt(stock)}% con un gap de precio "
            f"de {_fmt(gap, 1)}%: media en el retailer captura demanda insatisfecha sin "
            f"resignar precio."
        )

    # 2. Stock alto pero precio muy por encima del competidor.
    if stock_high and price_bad:
        return EVALUATE_PRICE_ACTION_BEFORE_MEDIA, (
            f"Nike está {_fmt(gap, 1)}% por encima del competidor (umbral de desventaja "
            f"{_fmt(disadvantage_pct, 1)}%) con {_fmt(stock)}% de stock: la visibilidad no "
            f"corrige una brecha de precio. Evaluar acción de precio antes de invertir en media."
        )

    # 5. Caso completo: retail media en lugar de un markdown adicional.
    if stock_high and price_ok and relevant and hot and shelf_low:
        return PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN, (
            f"Nike tiene {_fmt(stock)}% de stock, ya es competitivo en precio "
            f"({_fmt(gap, 1)}% de gap) y el producto es relevante "
            f"(importancia {_fmt(importance, 1)}), pero el competidor acelera "
            f"({momentum:.2f}) y el share of shelf Nike es de sólo "
            f"{shelf_share * 100:.0f}%. En vez de financiar un markdown adicional con el "
            f"retailer, reasignar parte de esa inversión a visibilidad/retail media: el "
            f"problema es exposición, no precio."
        )

    # 1. Stock alto + precio competitivo + competidor con momentum.
    if stock_high and price_ok and hot:
        return INVEST_IN_RETAIL_MEDIA, (
            f"Nike tiene {_fmt(stock)}% de stock y un gap de precio de {_fmt(gap, 1)}% "
            f"(dentro de la banda competitiva de {_fmt(competitive_pct, 1)}%) mientras el "
            f"competidor acelera ({momentum:.2f}): bajar precio es innecesario, Nike ya es "
            f"competitivo en precio. La palanca es visibilidad."
        )

    # Cierre por defecto, siempre dentro de las 5 decisiones del contrato.
    if stock_low:
        return DO_NOT_INCREASE_MEDIA, (
            f"Con {_fmt(stock)}% de stock Nike no hay inventario para sostener una inversión "
            f"en visibilidad; priorizar reposición."
        )
    if price_bad:
        return EVALUATE_PRICE_ACTION_BEFORE_MEDIA, (
            f"El gap de precio de {_fmt(gap, 1)}% supera el umbral de desventaja: revisar "
            f"precio antes de invertir en media."
        )
    momentum_txt = (f"momentum del competidor {momentum:.2f}" if momentum is not None
                    else "sin señal de momentum del competidor")
    return INVEST_IN_RETAIL_MEDIA, (
        f"Stock Nike {_fmt(stock)}%, gap de precio {_fmt(gap, 1)}% y {momentum_txt}: sin una "
        f"desventaja de precio que corregir, la visibilidad rinde más que un descuento adicional."
    )


def drivers_from(signals: dict[str, Any], score: CompositeScore, rationale: str) -> list[dict]:
    """Explicabilidad persistida: valores disparadores + contribución de factores.

    Formato de PERSISTENCIA (un sobre con el contexto del caso). La API lo
    publica en la forma canónica `drivers` + `signals` — ver
    `app.api.serializers.canonical_drivers`. Cada factor viaja con su peso y su
    detalle (por ejemplo, sobre qué precios se calculó el gap) para que esa
    traducción no pierda nada.
    """
    return [{
        "rationale": rationale,
        "nike_stock_pct": signals.get("nike_stock_pct"),
        "competitor_stock_pct": signals.get("competitor_stock_pct"),
        "price_gap_pct": signals.get("price_gap_pct"),
        "competitive_relevance": signals.get("match_score"),
        "competitor_momentum": signals.get("competitor_momentum"),
        "demand_signal": signals.get("demand_signal"),
        "nike_shelf_share": signals.get("nike_shelf_share"),
        "business_importance": signals.get("business_importance"),
        "nike_discount_pct": signals.get("nike_discount_pct"),
        "coverage": round(score.coverage, 4),
        "factors": [
            {"name": f["factor"], "value": f["raw_score"], "contribution": f["contribution"],
             "weight": f["weight"], "available": f["available"], "detail": f["detail"]}
            for f in score.factors
        ],
    }]


# ── API pública ─────────────────────────────────────────────


def score_retail_media(nike_product: dict, competitor_product: dict | None,
                       retailer: dict | None, ctx: IntelContext) -> tuple[CompositeScore, str]:
    """Score 0..100 + recomendación para un triplete (Nike, competidor, retailer)."""
    signals = build_signals(nike_product, competitor_product, retailer, ctx)
    score = score_from_signals(signals)
    recommendation, rationale = decide(signals)
    signals["recommendation"] = recommendation
    signals["rationale"] = rationale
    return score, recommendation


def run_retail_media(db_path: Any = DB_PATH) -> dict[str, int]:
    """Evalúa cada match competitivo en cada retailer y persiste. Idempotente."""
    ctx = build_context(db_path)
    min_score = _thresholds().get("min_score_to_report", 0.0)

    evaluated = 0
    rows: list[tuple] = []
    for nike_id, matches in ctx.matches.items():
        nike_product = ctx.product(nike_id)
        if not nike_product:
            continue
        for match in matches:
            comp_id = int(match["competitor_product_id"])
            competitor = ctx.product(comp_id)
            if not competitor:
                continue
            shared = sorted(ctx.retailers_of(nike_id) & ctx.retailers_of(comp_id))
            targets: list[int | None] = shared or sorted(ctx.retailers_of(nike_id)) or [None]
            for rid in targets:
                retailer = ctx.retailers.get(rid) if rid is not None else None
                signals = build_signals(nike_product, competitor, retailer, ctx)
                score = score_from_signals(signals)
                evaluated += 1
                if score.score < min_score:
                    continue
                recommendation, rationale = decide(signals)
                rows.append((
                    nike_id, comp_id, rid, signals.get("country_code"),
                    round(score.score, 2), recommendation, score.confidence,
                    to_json(drivers_from(signals, score, rationale)),
                ))

    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM retail_media_opportunities")
        conn.executemany(
            "INSERT INTO retail_media_opportunities (nike_product_id, competitor_product_id, "
            "retailer_id, country_code, score, recommendation, confidence, drivers) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )

    counts: dict[str, int] = {"evaluated": evaluated, "retail_media_opportunities": len(rows)}
    for row in rows:
        counts[row[5]] = counts.get(row[5], 0) + 1
    return counts
