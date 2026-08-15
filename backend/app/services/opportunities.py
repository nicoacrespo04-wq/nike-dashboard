"""Motor de oportunidades: convierte observaciones en DECISIONES comerciales.

Una función ``_rule_<tipo>(ctx) -> list[OpportunityDraft]`` por cada uno de los
12 tipos declarados en ``opportunities`` (weights.yaml). TODOS los umbrales
salen de config: acá no hay ni un número de negocio hardcodeado.

Cada oportunidad lleva:
  * ``family``            (mapa ``opportunities.families``)
  * ``business_importance`` + ``severity`` (``app.services.scoring``)
  * ``confidence``        (cobertura de los factores disponibles)
  * ``drivers``           JSON [{name, value, contribution}]
  * una ``recommendation`` accionable (action + rationale)

Degradación elegante: ninguna tabla se asume poblada. En particular
``competitive_matches`` la escribe otro módulo; si está vacía, las reglas que
dependen de matches simplemente no disparan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.config import DB_PATH, section
from app.db import get_conn, query
from app.services import scoring
from app.services.common import clamp, parse_date, recency_weight, to_json

# Los 12 tipos, en el orden en que se ejecutan.
OPPORTUNITY_TYPES: tuple[str, ...] = (
    "price_competitiveness_risk",
    "over_discounting_risk",
    "full_price_opportunity",
    "assortment_gap",
    "distribution_gap",
    "share_of_shelf_risk",
    "competitor_momentum",
    "competitor_stockout_opportunity",
    "assortment_white_space",
    "premiumization_opportunity",
    "promotional_pressure",
    "product_launch_threat",
)

# Acción sugerida por tipo (texto de UI en español, códigos en inglés).
ACTIONS: dict[str, str] = {
    "price_competitiveness_risk": "REVIEW_PRICE_POSITIONING",
    "over_discounting_risk": "REDUCE_DISCOUNT_DEPTH",
    "full_price_opportunity": "RETURN_TO_FULL_PRICE",
    "assortment_gap": "EXPAND_ASSORTMENT",
    "distribution_gap": "EXPAND_DISTRIBUTION",
    "share_of_shelf_risk": "DEFEND_SHARE_OF_SHELF",
    "competitor_momentum": "COUNTER_COMPETITOR_MOMENTUM",
    "competitor_stockout_opportunity": "CAPTURE_COMPETITOR_STOCKOUT",
    "assortment_white_space": "LAUNCH_INTO_WHITE_SPACE",
    "premiumization_opportunity": "EVALUATE_PRICE_INCREASE",
    "promotional_pressure": "PREPARE_PROMO_RESPONSE",
    "product_launch_threat": "PREPARE_LAUNCH_DEFENSE",
}


# ── draft ───────────────────────────────────────────────────


@dataclass
class OpportunityDraft:
    """Oportunidad candidata, antes de calcular importancia y persistir."""

    opportunity_type: str
    nike_product_id: int | None
    competitor_product_id: int | None
    retailer_id: int | None
    title: str
    description: str
    drivers: list[dict]          # [{name, value, contribution}]
    importance_inputs: dict      # entrada de scoring.business_importance()
    action: str
    rationale: str
    country_code: str | None = None

    @property
    def family(self) -> str | None:
        families = section("opportunities", "families", default={}) or {}
        return families.get(self.opportunity_type)


# ── contexto ────────────────────────────────────────────────


def _rule_cfg(rule: str) -> dict[str, Any]:
    return section("opportunities", rule, default={}) or {}


def _fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "s/d"
    return f"{value:.{digits}f}"


@dataclass
class IntelContext:
    """Snapshot en memoria de todo lo que las reglas necesitan leer."""

    db_path: Any
    today: date
    products: dict[int, dict] = field(default_factory=dict)
    retailers: dict[int, dict] = field(default_factory=dict)
    nike_ids: list[int] = field(default_factory=list)
    competitor_ids: list[int] = field(default_factory=list)
    prices: dict[int, dict[int, dict]] = field(default_factory=dict)   # product -> retailer -> obs
    stock: dict[int, dict[int, dict]] = field(default_factory=dict)
    reviews: dict[int, dict] = field(default_factory=dict)
    matches: dict[int, list[dict]] = field(default_factory=dict)       # nike -> [match]
    pair_scores: dict[tuple[int, int], float] = field(default_factory=dict)
    signals: list[dict] = field(default_factory=list)
    social: dict[int, list[dict]] = field(default_factory=dict)        # product -> periodos
    editorial: dict[int, float] = field(default_factory=dict)
    maxima: dict[str, float] = field(default_factory=dict)

    # — catálogo —

    def product(self, pid: int | None) -> dict:
        return self.products.get(pid) or {}

    def name(self, pid: int | None) -> str:
        return str(self.product(pid).get("product_name") or f"producto #{pid}")

    def brand_name(self, pid: int | None) -> str:
        return str(self.product(pid).get("brand_name") or "competidor")

    def full_name(self, pid: int | None) -> str:
        """Marca + producto, sin repetir la marca si ya está en el nombre."""
        brand, name = self.brand_name(pid), self.name(pid)
        return name if name.lower().startswith(brand.lower()) else f"{brand} {name}"

    def retailer_name(self, rid: int | None) -> str:
        r = self.retailers.get(rid) or {}
        return str(r.get("name") or f"retailer #{rid}")

    def retailer_importance(self, rid: int | None) -> float:
        """Peso estratégico del canal (0..1). Sin dato => neutro."""
        r = self.retailers.get(rid) or {}
        value = r.get("importance")
        return float(value) if value is not None else 0.5

    def segment_of(self, pid: int | None) -> str:
        p = self.product(pid)
        for key in ("use_case", "subcategory", "category"):
            value = p.get(key)
            if value:
                return str(value)
        return "sin segmento"

    def products_in_segment(self, segment: str, *, nike: bool) -> list[int]:
        pool = self.nike_ids if nike else self.competitor_ids
        return [pid for pid in pool if self.segment_of(pid) == segment]

    def segments(self) -> list[str]:
        return sorted({self.segment_of(pid) for pid in self.products})

    # — precios —

    def price_at(self, pid: int, rid: int) -> float | None:
        obs = (self.prices.get(pid) or {}).get(rid)
        if not obs:
            return None
        value = obs.get("current_price")
        return float(value) if value is not None else None

    def avg_price(self, pid: int) -> float | None:
        values = [
            float(o["current_price"])
            for o in (self.prices.get(pid) or {}).values()
            if o.get("current_price") is not None
        ]
        if values:
            return sum(values) / len(values)
        msrp = self.product(pid).get("msrp")
        return float(msrp) if msrp is not None else None

    def avg_discount(self, pid: int) -> float | None:
        values = [
            float(o["discount_pct"])
            for o in (self.prices.get(pid) or {}).values()
            if o.get("discount_pct") is not None
        ]
        return sum(values) / len(values) if values else None

    def retailers_of(self, pid: int) -> set[int]:
        return set(self.prices.get(pid) or {}) | set(self.stock.get(pid) or {})

    # — stock —

    def availability_at(self, pid: int, rid: int) -> float | None:
        obs = (self.stock.get(pid) or {}).get(rid)
        if not obs:
            return None
        pct = obs.get("availability_pct")
        if pct is not None:
            return float(pct)
        if obs.get("in_stock") is not None:
            return 100.0 if int(obs["in_stock"]) else 0.0
        return None

    def availability(self, pid: int, rid: int | None = None) -> float | None:
        if rid is not None:
            return self.availability_at(pid, rid)
        values = [
            v for v in (self.availability_at(pid, r) for r in (self.stock.get(pid) or {}))
            if v is not None
        ]
        return sum(values) / len(values) if values else None

    # — señales —

    def review_count(self, pid: int) -> float | None:
        row = self.reviews.get(pid)
        return float(row["count"]) if row else None

    def momentum(self, pid: int) -> dict[str, Any]:
        """Momentum 0..1 + aceleración relativa del producto.

        Prioriza ``market_signals`` (lo escribe brand_intelligence); si no hay,
        lo deriva de ``social_mention_aggregates`` comparando el último período
        contra el anterior. Si no hay nada devuelve valores None.
        """
        signal = self.signal_for("product", pid, ("social_momentum", "editorial_momentum",
                                                  "review_momentum"))
        if signal is not None:
            value = signal.get("value")
            if value is not None:
                value = float(value)
                # Los signals pueden venir en 0..1 o en 0..100; se normaliza a 0..1.
                value = clamp(value / 100.0 if value > 1.0 else value)
            accel = signal.get("acceleration")
            if accel is None:
                accel = signal.get("delta")
            return {"value": value,
                    "acceleration": float(accel) if accel is not None else None,
                    "source": "market_signals", "signal_type": signal.get("signal_type")}

        periods = self.social.get(pid) or []
        if not periods:
            return {"value": None, "acceleration": None, "source": "sin datos"}
        current = float(periods[-1].get("mentions") or 0.0)
        previous = float(periods[-2].get("mentions") or 0.0) if len(periods) > 1 else 0.0
        total = current + previous
        value = clamp(current / total) if total > 0 else None
        accel = (current - previous) / previous if previous > 0 else (1.0 if current > 0 else None)
        return {"value": value, "acceleration": accel, "source": "social_mention_aggregates",
                "current_mentions": current, "previous_mentions": previous}

    def social_volume(self, pid: int) -> float:
        return float(sum(p.get("mentions") or 0 for p in (self.social.get(pid) or [])))

    def signal_for(self, entity_type: str, entity_id: Any,
                   signal_types: tuple[str, ...] | None = None) -> dict | None:
        for row in self.signals:
            if row.get("entity_type") != entity_type:
                continue
            if str(row.get("entity_id")) != str(entity_id):
                continue
            if signal_types and row.get("signal_type") not in signal_types:
                continue
            return row
        return None

    # — competencia —

    def matched(self, nike_id: int) -> list[dict]:
        return self.matches.get(nike_id) or []

    def best_match(self, nike_id: int) -> dict | None:
        rows = self.matched(nike_id)
        return rows[0] if rows else None

    def match_score(self, nike_id: int, comp_id: int) -> float | None:
        return self.pair_scores.get((nike_id, comp_id))

    def shelf_share(self, pid: int) -> float | None:
        """Share of shelf de Nike en el segmento del producto (SKUs)."""
        segment = self.segment_of(pid)
        nike = len(self.products_in_segment(segment, nike=True))
        comp = len(self.products_in_segment(segment, nike=False))
        total = nike + comp
        return (nike / total) if total else None

    def revenue_proxy_raw(self, pid: int) -> float | None:
        """SKUs × cobertura × precio (proxy de facturación, sin normalizar)."""
        price = self.avg_price(pid)
        if price is None:
            return None
        coverage = max(len(self.retailers_of(pid)), 0)
        availability = self.availability(pid)
        factor = (availability / 100.0) if availability is not None else 1.0
        return float(price) * max(coverage, 1) * max(factor, 0.0)

    # — entrada estándar para business_importance —

    def importance_inputs(self, nike_id: int | None, *, competitor_id: int | None = None,
                          retailer_ids: list[int] | None = None, **extra: Any) -> dict:
        product = self.product(nike_id)
        rids = list(retailer_ids or (self.retailers_of(nike_id) if nike_id else []))
        importances = [
            float(self.retailers[r]["importance"])
            for r in rids
            if r in self.retailers and self.retailers[r].get("importance") is not None
        ]
        data: dict[str, Any] = {
            "franchise": product.get("franchise"),
            "lifecycle_stage": product.get("lifecycle_stage"),
            "retailer_importances": importances,
            "retailers_present": len(self.retailers_of(nike_id)) if nike_id else 0,
            "retailers_total": max(len(self.retailers), 1),
        }
        if nike_id:
            data["revenue_proxy_raw"] = self.revenue_proxy_raw(nike_id)
            data["review_volume_raw"] = self.review_count(nike_id)
            data["editorial_momentum_raw"] = self.editorial.get(nike_id)
            data["social_momentum"] = self.momentum(nike_id)["value"]
            data["nike_shelf_share"] = self.shelf_share(nike_id)
            data["promo_intensity_pct"] = self.avg_discount(nike_id)
        if competitor_id is not None and nike_id is not None:
            data["match_score"] = self.match_score(nike_id, competitor_id)
        data.update(extra)
        return data


def build_context(db_path: Any = DB_PATH) -> IntelContext:
    """Precarga catálogo, observaciones y señales. Tolera tablas vacías."""
    ctx = IntelContext(db_path=db_path, today=date.today())

    for row in query(
        "SELECT p.*, b.name AS brand_name, b.is_focus AS is_focus "
        "FROM products p JOIN brands b ON b.id = p.brand_id",
        path=db_path,
    ):
        ctx.products[int(row["id"])] = row
        if int(row.get("is_focus") or 0):
            ctx.nike_ids.append(int(row["id"]))
        else:
            ctx.competitor_ids.append(int(row["id"]))

    for row in query("SELECT * FROM retailers", path=db_path):
        ctx.retailers[int(row["id"])] = row

    # Última observación por (producto, retailer).
    for row in query("SELECT * FROM price_observations ORDER BY observed_at, id", path=db_path):
        ctx.prices.setdefault(int(row["product_id"]), {})[int(row["retailer_id"])] = row
    for row in query("SELECT * FROM stock_observations ORDER BY observed_at, id", path=db_path):
        ctx.stock.setdefault(int(row["product_id"]), {})[int(row["retailer_id"])] = row

    for row in query(
        "SELECT product_id, SUM(COALESCE(review_count, 1)) AS cnt, AVG(rating) AS rating "
        "FROM reviews GROUP BY product_id",
        path=db_path,
    ):
        ctx.reviews[int(row["product_id"])] = {
            "count": float(row["cnt"] or 0.0),
            "rating": float(row["rating"]) if row["rating"] is not None else None,
        }

    for row in query(
        "SELECT * FROM competitive_matches ORDER BY nike_product_id, match_score DESC",
        path=db_path,
    ):
        nike_id, comp_id = int(row["nike_product_id"]), int(row["competitor_product_id"])
        ctx.matches.setdefault(nike_id, []).append(row)
        ctx.pair_scores[(nike_id, comp_id)] = float(row["match_score"])

    ctx.signals = query("SELECT * FROM market_signals ORDER BY period_start, id", path=db_path)

    for row in query(
        "SELECT product_id, period_start, SUM(COALESCE(mention_count,0)) AS mentions, "
        "SUM(COALESCE(comention_count,0)) AS comentions, AVG(sentiment_score) AS sentiment "
        "FROM social_mention_aggregates WHERE product_id IS NOT NULL "
        "GROUP BY product_id, period_start ORDER BY period_start",
        path=db_path,
    ):
        ctx.social.setdefault(int(row["product_id"]), []).append(row)

    # Vida media editorial: si config no la declara, cada mención pesa 1 (sin decaimiento).
    half_life = section("competitive_match", "editorial", "recency_half_life_days")
    for row in query("SELECT * FROM editorial_mentions", path=db_path):
        weight = (recency_weight(row.get("published_at"), float(half_life), reference=ctx.today)
                  if half_life else 1.0)
        for key in ("product_a_id", "product_b_id"):
            pid = row.get(key)
            if pid is not None:
                ctx.editorial[int(pid)] = ctx.editorial.get(int(pid), 0.0) + weight

    # Máximos del corpus: normalización relativa, sin constantes mágicas.
    revenue = [v for v in (ctx.revenue_proxy_raw(p) for p in ctx.products) if v is not None]
    reviews = [r["count"] for r in ctx.reviews.values()]
    ctx.maxima = {
        "revenue_proxy": max(revenue) if revenue else 0.0,
        "review_volume": max(reviews) if reviews else 0.0,
        "editorial_momentum": max(ctx.editorial.values()) if ctx.editorial else 0.0,
        "social_momentum": max((ctx.social_volume(p) for p in ctx.products), default=0.0),
    }
    return ctx


# ── utilidades de comparación ───────────────────────────────


def price_comparison(ctx: IntelContext, nike_id: int, comp_id: int) -> dict:
    """Compara precios Nike vs competidor.

    ``gap_pct`` positivo => el competidor es MÁS BARATO que Nike.
    """
    shared = sorted(ctx.retailers_of(nike_id) & ctx.retailers_of(comp_id))
    rows: list[dict] = []
    for rid in shared:
        nike_price, comp_price = ctx.price_at(nike_id, rid), ctx.price_at(comp_id, rid)
        if nike_price and comp_price and nike_price > 0:
            rows.append({
                "retailer_id": rid,
                "retailer": ctx.retailer_name(rid),
                "nike_price": nike_price,
                "competitor_price": comp_price,
                "gap_pct": (nike_price - comp_price) / nike_price * 100.0,
            })
    if rows:
        gap = sum(r["gap_pct"] for r in rows) / len(rows)
        return {"per_retailer": rows, "gap_pct": gap, "basis": "retailers_compartidos"}

    nike_price, comp_price = ctx.avg_price(nike_id), ctx.avg_price(comp_id)
    if nike_price and comp_price and nike_price > 0:
        return {
            "per_retailer": [],
            "gap_pct": (nike_price - comp_price) / nike_price * 100.0,
            "basis": "precio promedio",
            "nike_price": nike_price,
            "competitor_price": comp_price,
        }
    return {"per_retailer": [], "gap_pct": None, "basis": "sin datos"}


def _competitor_pool(ctx: IntelContext, nike_id: int) -> list[int]:
    """Competidores comparables: matches si existen, si no el mismo segmento."""
    matched = [int(m["competitor_product_id"]) for m in ctx.matched(nike_id)]
    if matched:
        return matched
    return ctx.products_in_segment(ctx.segment_of(nike_id), nike=False)


# ── REGLAS (12) ─────────────────────────────────────────────


def _rule_price_competitiveness_risk(ctx: IntelContext) -> list[OpportunityDraft]:
    """El competidor principal vende sistemáticamente por debajo de Nike."""
    cfg = _rule_cfg("price_competitiveness_risk")
    min_gap = float(cfg.get("min_competitor_cheaper_pct", 0.0))
    min_retailers = int(cfg.get("min_retailers", 1))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        match = ctx.best_match(nike_id)
        if not match:
            continue
        comp_id = int(match["competitor_product_id"])
        cmp_ = price_comparison(ctx, nike_id, comp_id)
        cheaper = [r for r in cmp_["per_retailer"] if r["gap_pct"] >= min_gap]
        if len(cheaper) < min_retailers:
            continue
        gap = sum(r["gap_pct"] for r in cheaper) / len(cheaper)
        rids = [r["retailer_id"] for r in cheaper]
        drafts.append(OpportunityDraft(
            opportunity_type="price_competitiveness_risk",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=rids[0],
            title=f"Riesgo de competitividad de precio en {ctx.name(nike_id)}",
            description=(
                f"El competidor principal ({ctx.full_name(comp_id)}) "
                f"vende {_fmt(gap)}% por debajo de Nike en {len(cheaper)} retailers "
                f"estratégicos ({', '.join(r['retailer'] for r in cheaper)})."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id, retailer_ids=rids, price_gap_pct=gap,
            ),
            action=ACTIONS["price_competitiveness_risk"],
            rationale=(
                f"El gap de {_fmt(gap)}% en {len(cheaper)} retailers supera el umbral de "
                f"{_fmt(min_gap, 1)}%. Revisar posicionamiento de precio o argumentar valor "
                f"antes de perder conversión."
            ),
            country_code=ctx.product(nike_id).get("country_code"),
        ))
    return drafts


def _rule_over_discounting_risk(ctx: IntelContext) -> list[OpportunityDraft]:
    """Nike descuenta más que comparables pese a estar ya competitivo en precio."""
    cfg = _rule_cfg("over_discounting_risk")
    min_gap_pp = float(cfg.get("min_discount_gap_pp", 0.0))
    max_disadvantage = float(cfg.get("max_price_disadvantage_pct", 0.0))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        nike_disc = ctx.avg_discount(nike_id)
        if nike_disc is None:
            continue
        pool = _competitor_pool(ctx, nike_id)
        comp_discounts = [(c, ctx.avg_discount(c)) for c in pool]
        comp_discounts = [(c, d) for c, d in comp_discounts if d is not None]
        if not comp_discounts:
            continue
        comp_avg = sum(d for _, d in comp_discounts) / len(comp_discounts)
        gap_pp = nike_disc - comp_avg
        if gap_pp < min_gap_pp:
            continue

        reference = comp_discounts[0][0]
        price_gap = price_comparison(ctx, nike_id, reference)["gap_pct"]
        # "Desventaja de precio" = cuánto más caro está Nike (gap positivo).
        if price_gap is None or price_gap > max_disadvantage:
            continue

        nike_avail = ctx.availability(nike_id)
        comp_avail = ctx.availability(reference)
        drafts.append(OpportunityDraft(
            opportunity_type="over_discounting_risk",
            nike_product_id=nike_id,
            competitor_product_id=reference,
            retailer_id=None,
            title=f"Sobre-descuento en {ctx.name(nike_id)}",
            description=(
                f"La profundidad de descuento de Nike es {_fmt(gap_pp, 1)}pp mayor que la de "
                f"competidores comparables ({_fmt(nike_disc, 1)}% vs {_fmt(comp_avg, 1)}%) "
                f"pese a una disponibilidad similar (Nike {_fmt(nike_avail)}% vs "
                f"{_fmt(comp_avail)}%) y a estar ya competitivo en precio "
                f"({_fmt(price_gap, 1)}% de gap)."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=reference, price_gap_pct=price_gap,
                promo_intensity_pct=nike_disc,
            ),
            action=ACTIONS["over_discounting_risk"],
            rationale=(
                f"Se está financiando margen sin necesidad competitiva: bajar el descuento "
                f"{_fmt(gap_pp, 1)}pp recupera margen sin perder competitividad de precio."
            ),
            country_code=ctx.product(nike_id).get("country_code"),
        ))
    return drafts


def _rule_full_price_opportunity(ctx: IntelContext) -> list[OpportunityDraft]:
    """Nike descuenta sin que el competidor esté más barato: volver a full price."""
    cfg = _rule_cfg("full_price_opportunity")
    max_cheaper = float(cfg.get("max_competitor_cheaper_pct", 0.0))
    min_discount = float(cfg.get("min_nike_discount_pct", 0.0))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        nike_disc = ctx.avg_discount(nike_id)
        if nike_disc is None or nike_disc < min_discount:
            continue
        match = ctx.best_match(nike_id)
        if not match:
            continue
        comp_id = int(match["competitor_product_id"])
        gap = price_comparison(ctx, nike_id, comp_id)["gap_pct"]
        if gap is None or gap > max_cheaper:
            continue
        drafts.append(OpportunityDraft(
            opportunity_type="full_price_opportunity",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=None,
            title=f"Oportunidad de full price en {ctx.name(nike_id)}",
            description=(
                f"Nike sostiene {_fmt(nike_disc, 1)}% de descuento mientras el competidor "
                f"comparable ({ctx.name(comp_id)}) sólo está {_fmt(max(gap, 0.0), 1)}% por "
                f"debajo: el descuento no responde a presión competitiva real."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id, price_gap_pct=gap,
                promo_intensity_pct=nike_disc,
            ),
            action=ACTIONS["full_price_opportunity"],
            rationale=(
                f"Migrar a venta full price recupera hasta {_fmt(nike_disc, 1)}pp de margen: "
                f"el gap competitivo ({_fmt(gap, 1)}%) está por debajo del umbral de "
                f"{_fmt(max_cheaper, 1)}%."
            ),
            country_code=ctx.product(nike_id).get("country_code"),
        ))
    return drafts


def _rule_assortment_gap(ctx: IntelContext) -> list[OpportunityDraft]:
    """Los competidores tienen muchos más SKUs que Nike en un segmento."""
    cfg = _rule_cfg("assortment_gap")
    min_ratio = float(cfg.get("min_sku_ratio", 1.0))
    min_comp_skus = int(cfg.get("min_competitor_skus", 0))
    drafts: list[OpportunityDraft] = []

    for segment in ctx.segments():
        nike_skus = ctx.products_in_segment(segment, nike=True)
        comp_skus = ctx.products_in_segment(segment, nike=False)
        if len(comp_skus) < min_comp_skus:
            continue
        ratio = len(comp_skus) / max(len(nike_skus), 1)
        if ratio < min_ratio:
            continue
        representative = _representative(ctx, nike_skus)
        drafts.append(OpportunityDraft(
            opportunity_type="assortment_gap",
            nike_product_id=representative,
            competitor_product_id=None,
            retailer_id=None,
            title=f"Gap de surtido en {segment}",
            description=(
                f"El segmento '{segment}' muestra {len(comp_skus)} SKUs de competidores "
                f"vs {len(nike_skus)} de Nike (ratio {ratio:.1f}x)."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                representative, competitor_id=_top_competitor(ctx, representative),
                nike_shelf_share=(len(nike_skus) / (len(nike_skus) + len(comp_skus))),
            ),
            action=ACTIONS["assortment_gap"],
            rationale=(
                f"Con {len(comp_skus)} SKUs competidores contra {len(nike_skus)} de Nike, el "
                f"segmento está sub-cubierto: ampliar surtido o profundizar talles/colores."
            ),
            country_code=ctx.product(representative).get("country_code"),
        ))
    return drafts


def _rule_distribution_gap(ctx: IntelContext) -> list[OpportunityDraft]:
    """El competidor está en retailers donde el equivalente Nike no llega."""
    cfg = _rule_cfg("distribution_gap")
    min_gap = int(cfg.get("min_retailer_coverage_gap", 1))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        match = ctx.best_match(nike_id)
        if not match:
            continue
        comp_id = int(match["competitor_product_id"])
        missing = sorted(ctx.retailers_of(comp_id) - ctx.retailers_of(nike_id))
        if len(missing) < min_gap:
            continue
        missing.sort(key=lambda r: float((ctx.retailers.get(r) or {}).get("importance") or 0.0),
                     reverse=True)
        names = ", ".join(ctx.retailer_name(r) for r in missing)
        drafts.append(OpportunityDraft(
            opportunity_type="distribution_gap",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=missing[0],
            title=f"Gap de distribución en {ctx.name(nike_id)}",
            description=(
                f"{ctx.full_name(comp_id)} está presente en "
                f"{len(missing)} retailers donde el equivalente Nike no tiene presencia "
                f"({names}); Nike cubre {len(ctx.retailers_of(nike_id))} de "
                f"{len(ctx.retailers)} retailers relevados."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id, retailer_ids=missing,
            ),
            action=ACTIONS["distribution_gap"],
            rationale=(
                f"Abrir o reactivar {names} cierra {len(missing)} puntos de contacto donde hoy "
                f"la demanda se captura sin oferta Nike."
            ),
            country_code=ctx.product(nike_id).get("country_code"),
        ))
    return drafts


def _rule_share_of_shelf_risk(ctx: IntelContext) -> list[OpportunityDraft]:
    """Caída del share of shelf de Nike reportada en market_signals."""
    cfg = _rule_cfg("share_of_shelf_risk")
    min_drop = float(cfg.get("min_shelf_drop_pp", 0.0))
    drafts: list[OpportunityDraft] = []

    for signal in ctx.signals:
        if signal.get("signal_type") != "share_of_shelf":
            continue
        delta = signal.get("delta")
        if delta is None or -float(delta) < min_drop:
            continue
        drop = -float(delta)
        entity_type = str(signal.get("entity_type") or "")
        entity_id = signal.get("entity_id")
        nike_id = None
        retailer_id = None
        if entity_type == "product":
            try:
                candidate = int(entity_id)
            except (TypeError, ValueError):
                candidate = None
            if candidate in ctx.products:
                nike_id = candidate
        elif entity_type == "retailer":
            try:
                retailer_id = int(entity_id)
            except (TypeError, ValueError):
                retailer_id = None

        label = (ctx.name(nike_id) if nike_id else
                 ctx.retailer_name(retailer_id) if retailer_id else f"{entity_type} {entity_id}")
        value = signal.get("value")
        drafts.append(OpportunityDraft(
            opportunity_type="share_of_shelf_risk",
            nike_product_id=nike_id,
            competitor_product_id=None,
            retailer_id=retailer_id,
            title=f"Caída de share of shelf en {label}",
            description=(
                f"El share of shelf de Nike en {label} cayó {_fmt(drop, 1)}pp respecto del "
                f"período anterior, quedando en {_fmt(value, 1)}%."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id,
                competitor_id=_top_competitor(ctx, nike_id) if nike_id else None,
                retailer_ids=[retailer_id] if retailer_id else None,
                share_of_shelf=clamp(drop / 100.0),
            ),
            action=ACTIONS["share_of_shelf_risk"],
            rationale=(
                f"Una pérdida de {_fmt(drop, 1)}pp de espacio anticipa caída de sell-out: "
                f"renegociar espacio y visibilidad antes del próximo ciclo de reposición."
            ),
            country_code=signal.get("country_code"),
        ))
    return drafts


def _rule_competitor_momentum(ctx: IntelContext) -> list[OpportunityDraft]:
    """Un competidor acelera en señales de mercado (social/editorial/reviews)."""
    cfg = _rule_cfg("competitor_momentum")
    min_accel = float(cfg.get("min_acceleration", 0.0))
    drafts: list[OpportunityDraft] = []

    for comp_id in ctx.competitor_ids:
        momentum = ctx.momentum(comp_id)
        accel = momentum.get("acceleration")
        if accel is None or accel < min_accel:
            continue
        nike_id = _nike_counterpart(ctx, comp_id)
        drafts.append(OpportunityDraft(
            opportunity_type="competitor_momentum",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=None,
            title=f"Momentum del competidor {ctx.name(comp_id)}",
            description=(
                f"{ctx.full_name(comp_id)} acelera "
                f"{accel * 100:.0f}% en señales de mercado ({momentum.get('source')}) dentro "
                f"del segmento '{ctx.segment_of(comp_id)}', por encima del umbral de "
                f"{min_accel * 100:.0f}%."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id,
                social_momentum=momentum.get("value"),
            ),
            action=ACTIONS["competitor_momentum"],
            rationale=(
                f"El competidor está ganando conversación a {accel * 100:.0f}% de aceleración: "
                f"reforzar visibilidad y contenido del equivalente Nike antes de que se "
                f"consolide la preferencia."
            ),
            country_code=ctx.product(comp_id).get("country_code"),
        ))
    return drafts


def _rule_competitor_stockout_opportunity(ctx: IntelContext) -> list[OpportunityDraft]:
    """El competidor entró en quiebre y Nike tiene stock para capturar demanda."""
    cfg = _rule_cfg("competitor_stockout_opportunity")
    max_comp = float(cfg.get("max_competitor_availability_pct", 0.0))
    min_nike = float(cfg.get("min_nike_availability_pct", 100.0))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        for match in ctx.matched(nike_id):
            comp_id = int(match["competitor_product_id"])
            shared = sorted(ctx.retailers_of(nike_id) & ctx.retailers_of(comp_id))

            # Un quiebre es una condición POR RETAILER, no global: promediar la
            # disponibilidad entre canales diluye justamente la señal que importa
            # (un competidor al 25% en un retailer estratégico puede dar 63% de
            # promedio y quedar invisible). Evaluamos canal por canal.
            hits = [
                (rid, ctx.availability_at(comp_id, rid), ctx.availability_at(nike_id, rid))
                for rid in shared
            ]
            hits = [
                (rid, comp_av, nike_av) for rid, comp_av, nike_av in hits
                if comp_av is not None and nike_av is not None
                and comp_av <= max_comp and nike_av >= min_nike
            ]
            if not hits:
                continue

            # El retailer más estratégico primero: es donde conviene capturar.
            hits.sort(key=lambda h: (-ctx.retailer_importance(h[0]), h[1]))
            rid, comp_avail, nike_avail = hits[0]
            names = ", ".join(ctx.retailer_name(r) for r, _, _ in hits[:3])

            drafts.append(OpportunityDraft(
                opportunity_type="competitor_stockout_opportunity",
                nike_product_id=nike_id,
                competitor_product_id=comp_id,
                retailer_id=rid,
                title=f"Quiebre del competidor frente a {ctx.name(nike_id)}",
                description=(
                    f"La disponibilidad de {ctx.name(comp_id)} cayó a {_fmt(comp_avail)}% en "
                    f"{ctx.retailer_name(rid)}, mientras {ctx.name(nike_id)} se mantiene en "
                    f"{_fmt(nike_avail)}%. Afecta {len(hits)} retailer(s): {names}."
                ),
                drivers=[],
                importance_inputs=ctx.importance_inputs(
                    nike_id, competitor_id=comp_id, retailer_ids=[r for r, _, _ in hits],
                ),
                action=ACTIONS["competitor_stockout_opportunity"],
                rationale=(
                    f"Ventana de captura: con el competidor en {_fmt(comp_avail)}% de "
                    f"disponibilidad, empujar visibilidad y asegurar reposición Nike convierte "
                    f"demanda insatisfecha sin resignar precio."
                ),
                country_code=ctx.product(nike_id).get("country_code"),
            ))
            break  # una oportunidad por producto Nike (el mejor match)
    return drafts


def _rule_assortment_white_space(ctx: IntelContext) -> list[OpportunityDraft]:
    """Segmento con demanda observada alta y baja participación Nike."""
    cfg = _rule_cfg("assortment_white_space")
    min_demand = float(cfg.get("min_demand_signal", 0.0))
    max_share = float(cfg.get("max_nike_share", 1.0))

    demand: dict[str, float] = {}
    for segment in ctx.segments():
        volume = 0.0
        for pid in ctx.products:
            if ctx.segment_of(pid) != segment:
                continue
            volume += ctx.social_volume(pid) + (ctx.review_count(pid) or 0.0)
        demand[segment] = volume
    top = max(demand.values(), default=0.0)

    drafts: list[OpportunityDraft] = []
    for segment, volume in demand.items():
        signal = (volume / top) if top > 0 else 0.0
        if signal < min_demand:
            continue
        nike_skus = ctx.products_in_segment(segment, nike=True)
        comp_skus = ctx.products_in_segment(segment, nike=False)
        total = len(nike_skus) + len(comp_skus)
        if not total:
            continue
        share = len(nike_skus) / total
        if share > max_share:
            continue
        representative = _representative(ctx, nike_skus)
        drafts.append(OpportunityDraft(
            opportunity_type="assortment_white_space",
            nike_product_id=representative,
            competitor_product_id=None,
            retailer_id=None,
            title=f"Espacio en blanco de surtido en {segment}",
            description=(
                f"El segmento '{segment}' concentra una señal de demanda de "
                f"{signal * 100:.0f}% del máximo relevado ({volume:.0f} señales entre social y "
                f"reviews) y Nike sólo participa con {share * 100:.0f}% de los SKUs "
                f"({len(nike_skus)} de {total})."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                representative,
                competitor_id=_top_competitor(ctx, representative) if representative else None,
                nike_shelf_share=share, social_momentum=clamp(signal),
            ),
            action=ACTIONS["assortment_white_space"],
            rationale=(
                f"Hay demanda sin oferta Nike proporcional: con {share * 100:.0f}% de share de "
                f"SKUs frente a {signal * 100:.0f}% de señal de demanda, el segmento justifica "
                f"nuevo surtido o extensión de línea."
            ),
            country_code=ctx.product(representative).get("country_code"),
        ))
    return drafts


def _rule_premiumization_opportunity(ctx: IntelContext) -> list[OpportunityDraft]:
    """Nike está por debajo del competidor con un match muy alto: hay headroom."""
    cfg = _rule_cfg("premiumization_opportunity")
    min_match = float(cfg.get("min_match_score", 0.0))
    min_cheaper = float(cfg.get("min_nike_cheaper_pct", 0.0))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        for match in ctx.matched(nike_id):
            score = float(match["match_score"])
            if score < min_match:
                continue
            comp_id = int(match["competitor_product_id"])
            gap = price_comparison(ctx, nike_id, comp_id)["gap_pct"]
            if gap is None:
                continue
            nike_cheaper = -gap  # gap negativo => Nike más barato
            if nike_cheaper < min_cheaper:
                continue
            drafts.append(OpportunityDraft(
                opportunity_type="premiumization_opportunity",
                nike_product_id=nike_id,
                competitor_product_id=comp_id,
                retailer_id=None,
                title=f"Oportunidad de premiumización en {ctx.name(nike_id)}",
                description=(
                    f"{ctx.name(nike_id)} se vende {_fmt(nike_cheaper, 1)}% por debajo de "
                    f"{ctx.full_name(comp_id)}, un producto con "
                    f"{_fmt(score, 1)} de match competitivo: hay headroom de precio sin perder "
                    f"posicionamiento relativo."
                ),
                drivers=[],
                importance_inputs=ctx.importance_inputs(
                    nike_id, competitor_id=comp_id, price_gap_pct=nike_cheaper,
                ),
                action=ACTIONS["premiumization_opportunity"],
                rationale=(
                    f"Cerrar parte del {_fmt(nike_cheaper, 1)}% de brecha eleva el precio "
                    f"realizado frente a un competidor equivalente (match {_fmt(score, 1)})."
                ),
                country_code=ctx.product(nike_id).get("country_code"),
            ))
            break
    return drafts


def _rule_promotional_pressure(ctx: IntelContext) -> list[OpportunityDraft]:
    """Varios competidores comparables entraron en markdown profundo."""
    cfg = _rule_cfg("promotional_pressure")
    min_competitors = int(cfg.get("min_competitors_on_markdown", 1))
    min_discount = float(cfg.get("min_avg_discount_pct", 0.0))
    drafts: list[OpportunityDraft] = []

    for nike_id in ctx.nike_ids:
        on_markdown: list[tuple[int, float]] = []
        for comp_id in _competitor_pool(ctx, nike_id):
            discount = ctx.avg_discount(comp_id)
            if discount is not None and discount >= min_discount:
                on_markdown.append((comp_id, discount))
        if len(on_markdown) < min_competitors:
            continue
        avg = sum(d for _, d in on_markdown) / len(on_markdown)
        nike_disc = ctx.avg_discount(nike_id)
        names = ", ".join(f"{ctx.name(c)} ({_fmt(d, 1)}%)" for c, d in on_markdown)
        drafts.append(OpportunityDraft(
            opportunity_type="promotional_pressure",
            nike_product_id=nike_id,
            competitor_product_id=on_markdown[0][0],
            retailer_id=None,
            title=f"Presión promocional sobre {ctx.name(nike_id)}",
            description=(
                f"{len(on_markdown)} competidores comparables están en markdown con un "
                f"descuento promedio de {_fmt(avg, 1)}% ({names}), frente a "
                f"{_fmt(nike_disc, 1)}% de Nike."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=on_markdown[0][0], promo_intensity_pct=avg,
            ),
            action=ACTIONS["promotional_pressure"],
            rationale=(
                f"Con {len(on_markdown)} competidores a {_fmt(avg, 1)}% de descuento, definir "
                f"por anticipado la respuesta (visibilidad vs markdown) evita reaccionar tarde "
                f"y con más profundidad de la necesaria."
            ),
            country_code=ctx.product(nike_id).get("country_code"),
        ))
    return drafts


def _rule_product_launch_threat(ctx: IntelContext) -> list[OpportunityDraft]:
    """Lanzamiento reciente de un competidor ya distribuido en varios retailers."""
    cfg = _rule_cfg("product_launch_threat")
    max_days = float(cfg.get("max_days_since_launch", 0.0))
    min_coverage = int(cfg.get("min_retailer_coverage", 1))
    drafts: list[OpportunityDraft] = []

    for comp_id in ctx.competitor_ids:
        launch = parse_date(ctx.product(comp_id).get("launch_date"))
        if launch is None:
            continue
        days = (ctx.today - launch).days
        if days < 0 or days > max_days:
            continue
        coverage = ctx.retailers_of(comp_id)
        if len(coverage) < min_coverage:
            continue
        nike_id = _nike_counterpart(ctx, comp_id)
        drafts.append(OpportunityDraft(
            opportunity_type="product_launch_threat",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=sorted(coverage)[0],
            title=f"Amenaza de lanzamiento: {ctx.name(comp_id)}",
            description=(
                f"{ctx.full_name(comp_id)} salió al mercado hace {days} días y ya "
                f"está distribuido en {len(coverage)} retailers del segmento "
                f"'{ctx.segment_of(comp_id)}'"
                + (f", donde Nike compite con {ctx.name(nike_id)}." if nike_id else ".")
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id, retailer_ids=sorted(coverage),
            ),
            action=ACTIONS["product_launch_threat"],
            rationale=(
                f"Un lanzamiento de {days} días con {len(coverage)} retailers gana espacio "
                f"rápido: asegurar visibilidad y narrativa de producto Nike en esos mismos "
                f"puntos antes de que consolide share."
            ),
            country_code=ctx.product(comp_id).get("country_code"),
        ))
    return drafts


RULES = {
    "price_competitiveness_risk": _rule_price_competitiveness_risk,
    "over_discounting_risk": _rule_over_discounting_risk,
    "full_price_opportunity": _rule_full_price_opportunity,
    "assortment_gap": _rule_assortment_gap,
    "distribution_gap": _rule_distribution_gap,
    "share_of_shelf_risk": _rule_share_of_shelf_risk,
    "competitor_momentum": _rule_competitor_momentum,
    "competitor_stockout_opportunity": _rule_competitor_stockout_opportunity,
    "assortment_white_space": _rule_assortment_white_space,
    "premiumization_opportunity": _rule_premiumization_opportunity,
    "promotional_pressure": _rule_promotional_pressure,
    "product_launch_threat": _rule_product_launch_threat,
}


# ── helpers de selección ────────────────────────────────────


def _representative(ctx: IntelContext, candidates: list[int]) -> int | None:
    """Producto Nike más relevante de una lista (por importancia de franquicia)."""
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda pid: (
            scoring.franchise_importance(ctx.product(pid).get("franchise")),
            ctx.revenue_proxy_raw(pid) or 0.0,
        ),
    )


def _top_competitor(ctx: IntelContext, nike_id: int | None) -> int | None:
    if nike_id is None:
        return None
    match = ctx.best_match(nike_id)
    return int(match["competitor_product_id"]) if match else None


def _nike_counterpart(ctx: IntelContext, comp_id: int) -> int | None:
    """Producto Nike enfrentado a un competidor: por match, si no por segmento."""
    best: tuple[float, int] | None = None
    for (nike_id, other), score in ctx.pair_scores.items():
        if other == comp_id and (best is None or score > best[0]):
            best = (score, nike_id)
    if best:
        return best[1]
    return _representative(ctx, ctx.products_in_segment(ctx.segment_of(comp_id), nike=True))


# ── persistencia ────────────────────────────────────────────


def _drivers_from(score: Any) -> list[dict]:
    """[{name, value, contribution}] ordenado por contribución."""
    return [
        {
            "name": row["factor"],
            "value": row["raw_score"],
            "contribution": row["contribution"],
            "weight": row["weight"],
            "detail": row["detail"],
        }
        for row in score.drivers
    ]


def run_opportunities(db_path: Any = DB_PATH) -> dict[str, int]:
    """Ejecuta las 12 reglas, puntúa, y persiste. Idempotente (borra y recalcula)."""
    ctx = build_context(db_path)
    families = section("opportunities", "families", default={}) or {}

    counts: dict[str, int] = {name: 0 for name in OPPORTUNITY_TYPES}
    rows: list[tuple] = []

    for opportunity_type in OPPORTUNITY_TYPES:
        for draft in RULES[opportunity_type](ctx):
            importance = scoring.business_importance(draft.importance_inputs, ctx)
            drivers = draft.drivers or _drivers_from(importance)
            rows.append((draft, importance, drivers))
            counts[opportunity_type] += 1

    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM recommendations")
        conn.execute("DELETE FROM opportunities")
        for draft, importance, drivers in rows:
            cur = conn.execute(
                "INSERT INTO opportunities (opportunity_type, family, severity, "
                "nike_product_id, competitor_product_id, retailer_id, country_code, title, "
                "description, business_importance, confidence, drivers) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    draft.opportunity_type,
                    families.get(draft.opportunity_type),
                    scoring.severity(importance.score),
                    draft.nike_product_id,
                    draft.competitor_product_id,
                    draft.retailer_id,
                    draft.country_code,
                    draft.title,
                    draft.description,
                    round(importance.score, 2),
                    importance.confidence,
                    to_json(drivers),
                ),
            )
            conn.execute(
                "INSERT INTO recommendations (opportunity_id, action, rationale, score, "
                "confidence, drivers) VALUES (?,?,?,?,?,?)",
                (
                    cur.lastrowid,
                    draft.action,
                    draft.rationale,
                    round(importance.score, 2),
                    importance.confidence,
                    to_json(drivers),
                ),
            )

    counts["opportunities"] = len(rows)
    counts["recommendations"] = len(rows)
    return counts
