"""Retail Media Opportunity Engine.

Responde la pregunta que un dashboard de scraping no puede responder:
*¿conviene invertir en visibilidad (retail media) o en descuento?*

La unidad de decisión es el **cuadro** = (producto Nike × retailer): la plata de
visibilidad se compra por producto y por góndola, no contra un rival puntual.
Cada cuadro lleva adentro los N competidores más relevantes de esa góndola con
sus señales propias (stock, precio, gap, momentum), y tanto el score como la
recomendación se calculan sobre el CONJUNTO.

Score 0..100 con los 7 pesos de ``retail_media.weights`` y una recomendación
accionable elegida con los umbrales de ``retail_media.thresholds``:

  1. INVEST_IN_RETAIL_MEDIA               stock alto + precio competitivo + set con momentum
  2. EVALUATE_PRICE_ACTION_BEFORE_MEDIA   stock alto pero precio muy por encima del set
  3. DO_NOT_INCREASE_MEDIA                stock bajo + demanda alta
  4. CAPTURE_COMPETITOR_STOCKOUT          set en quiebre + Nike con stock y precio competitivo
  5. PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN  todo lo anterior + producto relevante y bajo share of shelf

Cómo se combinan varios competidores (``build_group_signals``)
--------------------------------------------------------------
Cada competidor pesa por su **relevancia** (``match_score / 100``): un rival 85%
comparable manda más que uno 45% comparable. Sobre esa base, el criterio no es
estadístico sino de negocio — depende de si la señal es un *riesgo asimétrico*
(alcanza con que UN comparable lo dispare) o una *magnitud de conjunto*:

* **Peor caso entre los comparables decisivos** para ``price_gap_pct`` y
  ``competitor_momentum``. "Decisivo" = por encima del piso de "comparable
  suficiente para actuar" (``opportunities.premiumization_opportunity.min_match_score``,
  el mismo que usa el gate de business importance). Al consumidor le alcanza
  con ver **una** alternativa comparable más barata para que la visibilidad
  pagada trabaje en contra, y con **un** comparable acelerando para que la
  categoría esté en movimiento: promediar escondería justo el caso que decide.
  Además, promediar haría que la lectura dependa de cuántos rivales entren en
  el cuadro — agregar un quinto comparable flojo no puede "enfriar" la góndola.
  Si ningún competidor llega al piso, se cae a promedio ponderado por
  relevancia: cuando todo el set es flojo, ninguno manda solo.

* **Promedio ponderado por relevancia** para ``competitor_stock_pct``, que sí
  es una magnitud de conjunto: mide cuánta demanda del set queda sin atender, y
  es lo que puntúa el factor ``competitor_stock_gap`` (tamaño de la
  oportunidad). El GATILLO de la ventana de captura, en cambio, vuelve a ser
  asimétrico y viaja aparte en ``competitor_stock_min_pct``: la demanda que
  libera un quiebre es la de ESE modelo y queda a tiro aunque el resto de la
  góndola tenga stock. Score y decisión responden preguntas distintas —
  "cuánto vale" y "qué palanca" — y las dos se publican.

* **Líder (máximo)** para ``competitive_relevance``: "cuán real es la
  competencia" lo define el mejor comparable. Promediar castigaría haber
  documentado una cola larga de rivales flojos — el mismo incentivo perverso
  que ``competitive_match.evidence_shrinkage`` corrige del otro lado.

El competidor líder es además el que se persiste en ``competitor_product_id``,
para que todo consumidor viejo del contrato siga viendo un rival coherente (el
más relevante) en vez de uno arbitrario.

Todo resultado es explicable: los drivers persistidos incluyen los valores que
dispararon la decisión (stock Nike, relevancia competitiva, momentum del set,
gap de precio %, share of shelf) más la confianza por cobertura y la lista
completa de competidores del cuadro con sus señales individuales.
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

#: Cuántos competidores entran en un cuadro. Los N más relevantes: más allá de
#: eso la tarjeta deja de ser leíble y la cola de rivales flojos no mueve la
#: decisión (pesa por relevancia). Configurable en `retail_media.max_competitors`.
DEFAULT_MAX_COMPETITORS = 4

#: Peso de relevancia cuando un match no trae `match_score` (no debería pasar:
#: los competidores salen de `competitive_matches`). Neutro, ni manda ni se
#: anula.
NEUTRAL_RELEVANCE = 0.5


def _thresholds() -> dict[str, float]:
    raw = section("retail_media", "thresholds", default={}) or {}
    return {k: float(v) for k, v in raw.items()}


def _max_competitors() -> int:
    raw = section("retail_media", "max_competitors", default=DEFAULT_MAX_COMPETITORS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_COMPETITORS


def _decisive_relevance() -> float:
    """Match score (0..100) desde el cual un competidor decide el precio.

    Reutiliza el piso que el motor ya declara como "comparable suficiente para
    actuar" (``premiumization_opportunity.min_match_score``), el mismo que
    ``business_importance.gate_full_relevance`` expresa en 0..1. No se inventa
    un umbral nuevo para lo que el sistema ya definió una vez.
    """
    value = section("opportunities", "premiumization_opportunity", "min_match_score",
                    default=0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float | None, digits: int = 0) -> str:
    return "s/d" if value is None else f"{value:.{digits}f}"


# ── combinación de varios competidores ──────────────────────


def _weighted_mean(pairs: list[tuple[float | None, float]]) -> float | None:
    """Promedio ponderado de ``[(valor, peso)]``. Sin pares con dato => None."""
    usable = [(v, w) for v, w in pairs if v is not None and w > 0]
    if not usable:
        return None
    total = sum(w for _, w in usable)
    if total <= 0:
        return None
    return sum(v * w for v, w in usable) / total


def _worst_case(blocks: list[dict[str, Any]], key: str,
                decisive_min: float) -> tuple[float | None, dict[str, Any] | None, str]:
    """Señal de riesgo asimétrico del set: el máximo entre comparables decisivos.

    Devuelve ``(valor, competidor de referencia, cómo se combinó)``. Si ningún
    competidor supera ``decisive_min`` de match score, cae a promedio ponderado
    por relevancia — cuando todo el set es flojo, ninguno decide solo.
    """
    with_data = [b for b in blocks if b.get(key) is not None]
    if not with_data:
        return None, None, "sin datos"

    reference = max(with_data, key=lambda b: float(b[key]))
    decisive = [b for b in with_data
                if b["match_score"] is None or float(b["match_score"]) >= decisive_min]
    if decisive:
        reference = max(decisive, key=lambda b: float(b[key]))
        return (float(reference[key]), reference,
                f"peor caso entre {len(decisive)} comparable(s) decisivo(s)")

    value = _weighted_mean([(float(b[key]), b["relevance"]) for b in with_data])
    return value, reference, ("promedio ponderado por relevancia (ningún comparable llega a "
                              f"{decisive_min:.0f} de match score)")


def _decisive_blocks(blocks: list[dict[str, Any]], key: str,
                     decisive_min: float) -> list[dict[str, Any]]:
    """Competidores con dato en ``key``, priorizando los comparables decisivos."""
    with_data = [b for b in blocks if b.get(key) is not None]
    decisive = [b for b in with_data
                if b["match_score"] is None or float(b["match_score"]) >= decisive_min]
    return decisive or with_data


# ── señales ─────────────────────────────────────────────────


def build_competitor_signals(nike_product: dict, competitor_product: dict,
                             retailer: dict | None, ctx: IntelContext) -> dict[str, Any]:
    """Señales propias de UN competidor dentro del cuadro (Nike × retailer).

    Es el bloque que la UI muestra por rival: stock, precio, gap y momentum,
    más la relevancia que le da su peso en la decisión conjunta.
    """
    nike_id = int(nike_product["id"])
    comp_id = int(competitor_product["id"])
    rid = int(retailer["id"]) if retailer else None

    stock = ctx.availability(comp_id, rid) if rid is not None else None
    present_at_retailer = stock is not None
    if stock is None:
        stock = ctx.availability(comp_id)

    comparison = price_comparison(ctx, nike_id, comp_id)
    gap = comparison["gap_pct"]                     # >0 => Nike más caro
    basis = str(comparison["basis"])
    nike_price = comparison.get("nike_price")
    competitor_price = comparison.get("competitor_price")
    if rid is not None:
        for row in comparison["per_retailer"]:
            if row["retailer_id"] == rid:
                gap = row["gap_pct"]
                basis = f"precios en {row['retailer']}"
                nike_price = row["nike_price"]
                competitor_price = row["competitor_price"]
                break

    match_score = ctx.match_score(nike_id, comp_id)
    relevance = (match_score / 100.0) if match_score is not None else NEUTRAL_RELEVANCE

    return {
        "competitor_product_id": comp_id,
        "competitor_name": ctx.full_name(comp_id),
        "brand": ctx.brand_name(comp_id),
        "match_score": match_score,
        "relevance": clamp(relevance),
        "stock_pct": stock,
        "present_at_retailer": present_at_retailer,
        "price_gap_pct": gap,
        "price_basis": basis,
        "nike_price": nike_price,
        "competitor_price": competitor_price,
        "momentum": ctx.momentum(comp_id)["value"],
    }


def build_group_signals(nike_product: dict, competitor_products: list[dict],
                        retailer: dict | None, ctx: IntelContext) -> dict[str, Any]:
    """Todas las señales del cuadro (Nike × retailer) con su set de competidores.

    Las señales del conjunto se combinan según la política documentada arriba:
    promedio ponderado por relevancia para momentum y stock del set, peor caso
    para el precio, líder para la relevancia competitiva.
    """
    nike_id = int(nike_product["id"])
    rid = int(retailer["id"]) if retailer else None

    blocks = [build_competitor_signals(nike_product, comp, retailer, ctx)
              for comp in competitor_products if comp]
    blocks.sort(key=lambda b: (b["relevance"], -int(b["competitor_product_id"])), reverse=True)
    blocks = blocks[:_max_competitors()]

    total_relevance = sum(b["relevance"] for b in blocks)
    for block in blocks:
        block["relevance_weight"] = round(
            (block["relevance"] / total_relevance) if total_relevance > 0
            else (1.0 / len(blocks) if blocks else 0.0), 4)
        block["is_leader"] = False
        block["is_price_reference"] = False
        block["is_momentum_reference"] = False
        block["is_stockout_reference"] = False
    if blocks:
        blocks[0]["is_leader"] = True

    leader = blocks[0] if blocks else None
    decisive_min = _decisive_relevance()

    # — precio: riesgo asimétrico => peor caso entre comparables decisivos —
    price_gap, price_reference, price_mode = _worst_case(blocks, "price_gap_pct", decisive_min)
    price_detail: dict[str, Any] = {}
    if price_reference is not None:
        price_reference["is_price_reference"] = True
        price_detail = {
            "basis": price_reference["price_basis"],
            "combination": price_mode,
            "reference_competitor": price_reference["competitor_name"],
            "reference_competitor_id": price_reference["competitor_product_id"],
            "competitors": len(blocks),
        }
        if price_reference.get("nike_price") is not None:
            price_detail["nike_price"] = price_reference["nike_price"]
        if price_reference.get("competitor_price") is not None:
            price_detail["competitor_price"] = price_reference["competitor_price"]

    # — momentum: riesgo asimétrico => mayor amenaza entre comparables decisivos —
    comp_momentum, momentum_reference, momentum_mode = _worst_case(blocks, "momentum",
                                                                   decisive_min)
    if momentum_reference is not None:
        momentum_reference["is_momentum_reference"] = True

    # — stock del set: magnitud de conjunto => promedio ponderado por relevancia.
    #   Mide cuánta demanda del set queda sin atender, y es lo que puntúa el
    #   factor `competitor_stock_gap`.
    comp_stock = _weighted_mean([(b["stock_pct"], b["relevance"]) for b in blocks])
    #   La VENTANA de captura, en cambio, es un disparo asimétrico: la demanda
    #   que libera un quiebre es la de ESE modelo, y se libera aunque el resto
    #   de la góndola tenga stock. Por eso el gatillo mira al comparable
    #   decisivo peor abastecido.
    stock_pool = _decisive_blocks(blocks, "stock_pct", decisive_min)
    stockout_reference = (min(stock_pool, key=lambda b: float(b["stock_pct"]))
                          if stock_pool else None)
    comp_stock_min = (float(stockout_reference["stock_pct"])
                      if stockout_reference is not None else None)
    if stockout_reference is not None:
        stockout_reference["is_stockout_reference"] = True

    # — relevancia competitiva: la del líder —
    match_score = leader["match_score"] if leader else None

    nike_stock = ctx.availability(nike_id, rid)
    if nike_stock is None:
        nike_stock = ctx.availability(nike_id)
    nike_momentum = ctx.momentum(nike_id)["value"]
    shelf_share = ctx.shelf_share(nike_id)

    importance_inputs = ctx.importance_inputs(
        nike_id,
        competitor_id=leader["competitor_product_id"] if leader else None,
        retailer_ids=[rid] if rid is not None else None,
        price_gap_pct=price_gap,
    )
    importance = scoring.business_importance(importance_inputs, ctx)

    stockout_pct = _thresholds().get("competitor_stockout_pct", 0.0)
    in_stockout = sum(1 for b in blocks
                      if b["stock_pct"] is not None and b["stock_pct"] <= stockout_pct)

    demand_candidates = [m for m in (comp_momentum, nike_momentum) if m is not None]
    return {
        "nike_product_id": nike_id,
        "competitor_product_id": leader["competitor_product_id"] if leader else None,
        "retailer_id": rid,
        "country_code": nike_product.get("country_code") or (retailer or {}).get("country_code"),
        "competitors": blocks,
        "competitor_count": len(blocks),
        "competitors_in_stockout": in_stockout,
        "nike_stock_pct": nike_stock,
        "competitor_stock_pct": comp_stock,
        "competitor_stock_min_pct": comp_stock_min,
        "price_gap_pct": price_gap,
        "price_detail": price_detail,
        "match_score": match_score,
        "competitor_momentum": comp_momentum,
        "momentum_detail": ({"combination": momentum_mode,
                             "reference_competitor": momentum_reference["competitor_name"],
                             "reference_competitor_id":
                                 momentum_reference["competitor_product_id"]}
                            if momentum_reference is not None else {}),
        "nike_momentum": nike_momentum,
        "demand_signal": max(demand_candidates) if demand_candidates else None,
        "nike_shelf_share": shelf_share,
        "business_importance": importance.score,
        "business_importance_confidence": importance.confidence,
        "nike_discount_pct": ctx.avg_discount(nike_id),
    }


def build_signals(nike_product: dict, competitor_product: dict | None,
                  retailer: dict | None, ctx: IntelContext) -> dict[str, Any]:
    """Señales del cuadro con UN solo competidor.

    Se conserva porque es la puerta que usan la calibración y los tests por
    caso; con un único rival, promedio ponderado, peor caso y líder coinciden,
    así que es exactamente ``build_group_signals`` con una lista de uno.
    """
    return build_group_signals(nike_product, [competitor_product] if competitor_product else [],
                               retailer, ctx)


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
    """Combina los 7 factores de ``retail_media.weights``.

    Opera sobre las señales YA agregadas del cuadro, así que el score y la
    recomendación miran exactamente los mismos números: nunca puede pasar que
    el score describa a un competidor y la acción a otro.
    """
    w = weights("retail_media", "weights")
    th = _thresholds()

    stock = signals.get("nike_stock_pct")
    comp_stock = signals.get("competitor_stock_pct")
    match_score = signals.get("match_score")
    shelf_share = signals.get("nike_shelf_share")
    importance = signals.get("business_importance")
    count = int(signals.get("competitor_count") or 0)

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
        "competitive_relevance": {"match_score": match_score,
                                  "combination": "competidor líder del cuadro",
                                  "competitors": count},
        "business_importance": {"score": importance},
        "competitor_momentum": {"momentum": signals.get("competitor_momentum"),
                                **(signals.get("momentum_detail") or {}),
                                "competitors": count},
        "shelf_gap": {"nike_shelf_share": shelf_share},
        "competitor_stock_gap": {"competitor_stock_pct": comp_stock,
                                 "combination": "promedio ponderado por relevancia",
                                 "worst_competitor_stock_pct":
                                     signals.get("competitor_stock_min_pct"),
                                 "in_stockout": signals.get("competitors_in_stockout"),
                                 "competitors": count},
    }

    factors = [Factor(name, raw.get(name), float(weight), detail.get(name, {}))
               for name, weight in w.items()]
    return combine(factors, section("retail_media", "confidence_thresholds"))


# ── decisión ────────────────────────────────────────────────


def _set_phrase(signals: dict[str, Any]) -> str:
    """Cómo se nombra al set competidor en el racional."""
    count = int(signals.get("competitor_count") or 0)
    if count == 0:
        return "sin competidor identificado"
    if count == 1:
        blocks = signals.get("competitors") or []
        name = blocks[0].get("competitor_name") if blocks else None
        return f"frente a {name}" if name else "frente a 1 competidor"
    return f"frente a los {count} competidores relevantes del cuadro"


def _reference_phrase(signals: dict[str, Any], flag: str, prefix: str) -> str:
    """Quién define una señal de peor caso (sólo si hay más de un rival)."""
    blocks = signals.get("competitors") or []
    if len(blocks) < 2:
        return ""
    reference = next((b for b in blocks if b.get(flag)), None)
    if not reference:
        return ""
    return f"{prefix}{reference.get('competitor_name')}"


def _price_reference_phrase(signals: dict[str, Any]) -> str:
    return _reference_phrase(signals, "is_price_reference", ", peor caso contra ")


def _momentum_reference_phrase(signals: dict[str, Any]) -> str:
    return _reference_phrase(signals, "is_momentum_reference", ", traccionado por ")


def _stockout_reference_phrase(signals: dict[str, Any]) -> str:
    return _reference_phrase(signals, "is_stockout_reference", " — ")


def decide(signals: dict[str, Any]) -> tuple[str, str]:
    """Los 5 casos del brief sobre el CUADRO. Devuelve (recomendación, razón)."""
    th = _thresholds()
    stock = signals.get("nike_stock_pct")
    comp_stock = signals.get("competitor_stock_pct")
    gap = signals.get("price_gap_pct")
    momentum = signals.get("competitor_momentum")
    demand = signals.get("demand_signal")
    shelf_share = signals.get("nike_shelf_share")
    importance = signals.get("business_importance")
    set_txt = _set_phrase(signals)

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
    # La ventana de quiebre la abre un comparable decisivo sin stock, no el
    # promedio de la góndola: la demanda que libera ese modelo queda a tiro
    # aunque el resto del set esté abastecido.
    comp_stock_min = signals.get("competitor_stock_min_pct")
    comp_out = comp_stock_min is not None and comp_stock_min <= stockout_pct
    shelf_low = shelf_share is not None and shelf_share <= low_shelf
    relevant = importance is not None and importance >= relevant_min

    # 3. Stock bajo + demanda alta: nunca generar demanda sobre inventario insuficiente.
    if stock_low and demand_high:
        return DO_NOT_INCREASE_MEDIA, (
            f"El stock Nike está en {_fmt(stock)}% (por debajo del piso de "
            f"{_fmt(low_stock)}%) con demanda alta ({demand:.2f}) {set_txt}: invertir en "
            f"visibilidad generaría tráfico sobre inventario insuficiente. Primero reponer."
        )

    # 4. Set competidor en quiebre + Nike con stock y precio competitivo.
    if comp_out and stock is not None and stock > low_stock and price_ok:
        return CAPTURE_COMPETITOR_STOCKOUT, (
            f"Un comparable decisivo cayó a {_fmt(comp_stock_min)}% de disponibilidad"
            f"{_stockout_reference_phrase(signals)} (umbral de quiebre {_fmt(stockout_pct)}%; "
            f"{signals.get('competitors_in_stockout')} de {signals.get('competitor_count')} "
            f"del cuadro en quiebre, set en {_fmt(comp_stock)}%) mientras Nike sostiene "
            f"{_fmt(stock)}% con un gap de precio de {_fmt(gap, 1)}%: media en el retailer "
            f"captura demanda insatisfecha sin resignar precio."
        )

    # 2. Stock alto pero precio muy por encima del set comparable.
    if stock_high and price_bad:
        return EVALUATE_PRICE_ACTION_BEFORE_MEDIA, (
            f"Nike está {_fmt(gap, 1)}% por encima del comparable más caro de sostener "
            f"(umbral de desventaja {_fmt(disadvantage_pct, 1)}%{_price_reference_phrase(signals)}) "
            f"con {_fmt(stock)}% de stock: la visibilidad no corrige una brecha de precio. "
            f"Evaluar acción de precio antes de invertir en media."
        )

    # 5. Caso completo: retail media en lugar de un markdown adicional.
    if stock_high and price_ok and relevant and hot and shelf_low:
        return PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN, (
            f"Nike tiene {_fmt(stock)}% de stock, ya es competitivo en precio "
            f"({_fmt(gap, 1)}% de gap {set_txt}) y el producto es relevante "
            f"(importancia {_fmt(importance, 1)}), pero el set competidor acelera "
            f"({momentum:.2f}{_momentum_reference_phrase(signals)}) y el share of shelf Nike es de sólo "
            f"{shelf_share * 100:.0f}%. En vez de financiar un markdown adicional con el "
            f"retailer, reasignar parte de esa inversión a visibilidad/retail media: el "
            f"problema es exposición, no precio."
        )

    # 1. Stock alto + precio competitivo + set con momentum.
    if stock_high and price_ok and hot:
        return INVEST_IN_RETAIL_MEDIA, (
            f"Nike tiene {_fmt(stock)}% de stock y un gap de precio de {_fmt(gap, 1)}% "
            f"{set_txt} (dentro de la banda competitiva de {_fmt(competitive_pct, 1)}%) "
            f"mientras el set acelera ({momentum:.2f}{_momentum_reference_phrase(signals)}): "
            f"bajar precio es innecesario, Nike ya es competitivo en precio. La palanca es "
            f"visibilidad."
        )

    # Cierre por defecto, siempre dentro de las 5 decisiones del contrato.
    if stock_low:
        return DO_NOT_INCREASE_MEDIA, (
            f"Con {_fmt(stock)}% de stock Nike no hay inventario para sostener una inversión "
            f"en visibilidad; priorizar reposición."
        )
    if price_bad:
        return EVALUATE_PRICE_ACTION_BEFORE_MEDIA, (
            f"El gap de precio de {_fmt(gap, 1)}%{_price_reference_phrase(signals)} supera el "
            f"umbral de desventaja: revisar precio antes de invertir en media."
        )
    momentum_txt = (f"momentum del set competidor {momentum:.2f}" if momentum is not None
                    else "sin señal de momentum del set competidor")
    return INVEST_IN_RETAIL_MEDIA, (
        f"Stock Nike {_fmt(stock)}%, gap de precio {_fmt(gap, 1)}% {set_txt} y {momentum_txt}: "
        f"sin una desventaja de precio que corregir, la visibilidad rinde más que un descuento "
        f"adicional."
    )


def public_competitors(signals: dict[str, Any]) -> list[dict[str, Any]]:
    """Bloques por competidor, tal como se persisten y se publican.

    Es el contrato nuevo del cuadro: la UI recorre esta lista para dibujar
    varios modelos competidores adentro de una sola tarjeta.
    """
    out: list[dict[str, Any]] = []
    for block in signals.get("competitors") or []:
        out.append({
            "competitor_product_id": block.get("competitor_product_id"),
            "competitor_name": block.get("competitor_name"),
            "brand": block.get("brand"),
            "match_score": block.get("match_score"),
            "relevance_weight": block.get("relevance_weight"),
            "stock_pct": block.get("stock_pct"),
            "price_gap_pct": block.get("price_gap_pct"),
            "nike_price": block.get("nike_price"),
            "competitor_price": block.get("competitor_price"),
            "price_basis": block.get("price_basis"),
            "momentum": block.get("momentum"),
            "present_at_retailer": bool(block.get("present_at_retailer")),
            "is_leader": bool(block.get("is_leader")),
            "is_price_reference": bool(block.get("is_price_reference")),
            "is_momentum_reference": bool(block.get("is_momentum_reference")),
            "is_stockout_reference": bool(block.get("is_stockout_reference")),
        })
    return out


def drivers_from(signals: dict[str, Any], score: CompositeScore, rationale: str) -> list[dict]:
    """Explicabilidad persistida: valores disparadores + contribución de factores.

    Formato de PERSISTENCIA (un sobre con el contexto del caso). La API lo
    publica en la forma canónica `drivers` + `signals` — ver
    `app.api.serializers.canonical_drivers`. Cada factor viaja con su peso y su
    detalle (por ejemplo, sobre qué precios se calculó el gap) para que esa
    traducción no pierda nada.

    `competitors` es la lista del cuadro: viaja como estructura anidada, así
    que el normalizador canónico la ignora (no es una métrica escalar) y el
    router de retail media la publica aparte, ya resuelta contra el catálogo.
    """
    return [{
        "rationale": rationale,
        "nike_stock_pct": signals.get("nike_stock_pct"),
        "competitor_stock_pct": signals.get("competitor_stock_pct"),
        "competitor_stock_min_pct": signals.get("competitor_stock_min_pct"),
        "price_gap_pct": signals.get("price_gap_pct"),
        "competitive_relevance": signals.get("match_score"),
        "competitor_momentum": signals.get("competitor_momentum"),
        "demand_signal": signals.get("demand_signal"),
        "nike_shelf_share": signals.get("nike_shelf_share"),
        "business_importance": signals.get("business_importance"),
        "nike_discount_pct": signals.get("nike_discount_pct"),
        "competitor_count": signals.get("competitor_count"),
        "competitors_in_stockout": signals.get("competitors_in_stockout"),
        "coverage": round(score.coverage, 4),
        "competitors": public_competitors(signals),
        "factors": [
            {"name": f["factor"], "value": f["raw_score"], "contribution": f["contribution"],
             "weight": f["weight"], "available": f["available"], "detail": f["detail"]}
            for f in score.factors
        ],
    }]


# ── API pública ─────────────────────────────────────────────


def score_retail_media(nike_product: dict, competitor_product: dict | None,
                       retailer: dict | None, ctx: IntelContext) -> tuple[CompositeScore, str]:
    """Score 0..100 + recomendación para un cuadro con UN competidor."""
    return score_retail_media_group(
        nike_product, [competitor_product] if competitor_product else [], retailer, ctx)


def score_retail_media_group(nike_product: dict, competitor_products: list[dict],
                             retailer: dict | None,
                             ctx: IntelContext) -> tuple[CompositeScore, str]:
    """Score 0..100 + recomendación para el cuadro (Nike × retailer) completo."""
    signals = build_group_signals(nike_product, competitor_products, retailer, ctx)
    score = score_from_signals(signals)
    recommendation, rationale = decide(signals)
    signals["recommendation"] = recommendation
    signals["rationale"] = rationale
    return score, recommendation


def _competitor_set(ctx: IntelContext, nike_id: int, ranked_ids: list[int],
                    rid: int | None) -> list[int]:
    """Competidores del cuadro: los que comparten esa góndola con Nike.

    Si en ese retailer no se observó a ninguno, se cae al set completo de
    matches (compiten en la categoría igual; sus señales salen del promedio de
    canales, que es lo que `IntelContext.availability` ya hace).
    """
    if rid is None:
        return ranked_ids
    present = [cid for cid in ranked_ids if rid in ctx.retailers_of(cid)]
    return present or ranked_ids


def run_retail_media(db_path: Any = DB_PATH) -> dict[str, int]:
    """Evalúa cada cuadro (producto Nike × retailer) y persiste. Idempotente.

    Un registro por góndola, con los N competidores más relevantes adentro: la
    decisión de invertir en visibilidad se toma mirando el conjunto, y de paso
    el volumen deja de multiplicarse por la cantidad de rivales.
    """
    ctx = build_context(db_path)
    min_score = _thresholds().get("min_score_to_report", 0.0)

    evaluated = 0
    rows: list[tuple] = []
    for nike_id, matches in ctx.matches.items():
        nike_product = ctx.product(nike_id)
        if not nike_product:
            continue
        ranked = sorted(
            (m for m in matches if ctx.product(int(m["competitor_product_id"]))),
            key=lambda m: float(m.get("match_score") or 0.0),
            reverse=True,
        )
        ranked_ids = [int(m["competitor_product_id"]) for m in ranked]
        if not ranked_ids:
            continue

        targets: list[int | None] = sorted(ctx.retailers_of(nike_id)) or [None]
        for rid in targets:
            retailer = ctx.retailers.get(rid) if rid is not None else None
            competitors = [ctx.product(cid)
                           for cid in _competitor_set(ctx, nike_id, ranked_ids, rid)]
            signals = build_group_signals(nike_product, competitors, retailer, ctx)
            score = score_from_signals(signals)
            evaluated += 1
            if score.score < min_score:
                continue
            recommendation, rationale = decide(signals)
            rows.append((
                nike_id, signals.get("competitor_product_id"), rid, signals.get("country_code"),
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
