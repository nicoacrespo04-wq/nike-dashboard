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
from app.services.matching import Generation, generation_map
from app.services.common import clamp, parse_date, recency_weight, to_json

# Los 12 tipos históricos, en el orden en que se ejecutan.
# NO agregar acá tipos nuevos: `app.calibration` declara un umbral y una señal
# de entrada por cada elemento de esta tupla (ver `calibration.THRESHOLDS` y
# `calibration.RULE_INPUTS`). Los tipos que todavía no tienen ficha de
# calibración viven en `EXTRA_OPPORTUNITY_TYPES`.
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

# Reglas nuevas, todavía sin ficha en `app.calibration`.
# `clearance_needed` es la hermana de `full_price_opportunity`: las dos deciden
# sobre el MISMO stylecolor y con la MISMA evidencia de rotación, en sentidos
# opuestos. Ver `_rule_clearance_needed`.
EXTRA_OPPORTUNITY_TYPES: tuple[str, ...] = (
    "clearance_needed",
)

ALL_OPPORTUNITY_TYPES: tuple[str, ...] = OPPORTUNITY_TYPES + EXTRA_OPPORTUNITY_TYPES

# Acción sugerida por tipo (texto de UI en español, códigos en inglés).
ACTIONS: dict[str, str] = {
    "price_competitiveness_risk": "REVIEW_PRICE_POSITIONING",
    "over_discounting_risk": "REDUCE_DISCOUNT_DEPTH",
    "full_price_opportunity": "RETURN_TO_FULL_PRICE",
    "clearance_needed": "LIQUIDATE_STYLECOLOR",
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

# Familia por tipo cuando `opportunities.families` (weights.yaml) todavía no la
# declara. Sólo cubre los tipos nuevos: lo que ya está en config manda siempre.
#   PENDIENTE en config/weights.yaml:  opportunities.families.clearance_needed: pricing
_FALLBACK_FAMILIES: dict[str, str] = {
    "clearance_needed": "pricing",
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
        return family_of(self.opportunity_type)


def family_of(opportunity_type: str) -> str | None:
    """Familia declarada en config; si falta, la de `_FALLBACK_FAMILIES`."""
    families = section("opportunities", "families", default={}) or {}
    return families.get(opportunity_type) or _FALLBACK_FAMILIES.get(opportunity_type)


# ── contexto ────────────────────────────────────────────────


def _rule_cfg(rule: str) -> dict[str, Any]:
    return section("opportunities", rule, default={}) or {}


def _fmt(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "s/d"
    return f"{value:.{digits}f}"


def _rotation_cfg(key: str, default: Any) -> Any:
    return section("opportunities", "rotation", key, default=default)


# ── rotación por STYLECOLOR (proxy de WOH) ──────────────────
#
# La decisión de volver a precio lleno NO es de la silueta ni de la franquicia:
# es del stylecolor. Dentro de una misma silueta conviven colorways que vuelan y
# colorways con exceso de inventario que hay que liquidar sí o sí. Por eso la
# unidad de análisis acá es `style_code` (el stylecolor), no `product`.
#
# No hay tabla de ventas ni de inventario: la rotación se ESTIMA de dos series
# que sí existen, agregadas por stylecolor y por fecha de observación:
#
#   * `stock_observations.availability_pct` (talles disponibles / talles totales)
#     -> cuánto del size run queda vivo, y a qué ritmo se está drenando.
#   * `price_observations.discount_pct` -> hacia dónde va el markdown.
#
#     drenaje  = (disponibilidad_inicial - disponibilidad_final) / días × 30
#     WOH      = disponibilidad_actual / (drenaje semanal)      # semanas de cobertura
#
# Un stylecolor que ROTA drena su curva de talles y no necesita profundizar
# descuento. Uno con exceso de inventario mantiene la disponibilidad alta y
# plana mientras el descuento sube: ése hay que liquidarlo, no devolverlo a full
# price. Si no hay serie suficiente para medir el drenaje, `sufficient=False` y
# NINGUNA de las dos reglas dispara: vale más callarse.


@dataclass(frozen=True)
class Rotation:
    """Estimación de rotación de un stylecolor a partir de series observadas."""

    style_key: str
    product_ids: tuple[int, ...]
    n_observations: int                    # fechas distintas con disponibilidad
    span_days: int
    availability_now: float | None
    availability_avg: float | None
    depletion_pp_per_month: float | None   # >0 = el size run se está drenando
    weeks_on_hand: float | None            # None = no drena (cobertura infinita)
    discount_now: float | None
    discount_trend_pp_per_month: float | None
    sufficient: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "style_key": self.style_key,
            "n_observations": self.n_observations,
            "span_days": self.span_days,
            "availability_now_pct": _round(self.availability_now, 1),
            "availability_avg_pct": _round(self.availability_avg, 1),
            "depletion_pp_per_month": _round(self.depletion_pp_per_month, 2),
            "weeks_on_hand": _round(self.weeks_on_hand, 1),
            "discount_now_pct": _round(self.discount_now, 1),
            "discount_trend_pp_per_month": _round(self.discount_trend_pp_per_month, 2),
            "sufficient": self.sufficient,
            "reason": self.reason,
        }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def style_key(product: dict) -> str:
    """Identidad del STYLECOLOR: ``style_code`` -> ``sku`` -> id del producto.

    El `style_code` de Nike ya identifica silueta + colorway; el `sku` es el
    respaldo cuando el scraper no lo trajo. El id del producto es el último
    recurso y mantiene la regla operando a la granularidad más fina disponible.
    """
    for key in ("style_code", "sku"):
        value = product.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return f"product:{product.get('id')}"


def _daily_series(rows: list[dict], value_of) -> list[tuple[date, float]]:
    """Serie (fecha, promedio entre retailers) ordenada, descartando huecos."""
    buckets: dict[date, list[float]] = {}
    for row in rows:
        when = parse_date(row.get("observed_at"))
        if when is None:
            continue
        value = value_of(row)
        if value is None:
            continue
        buckets.setdefault(when, []).append(float(value))
    return [(day, sum(v) / len(v)) for day, v in sorted(buckets.items())]


def _availability_of(row: dict) -> float | None:
    pct = row.get("availability_pct")
    if pct is not None:
        return float(pct)
    total, available = row.get("sizes_total"), row.get("sizes_available")
    if total and available is not None:
        return float(available) / float(total) * 100.0
    if row.get("in_stock") is not None:
        return 100.0 if int(row["in_stock"]) else 0.0
    return None


def _slope_per_month(series: list[tuple[date, float]]) -> tuple[float | None, int]:
    """Pendiente extremo-a-extremo en unidades/30 días y span en días."""
    if len(series) < 2:
        return None, 0
    span = (series[-1][0] - series[0][0]).days
    if span <= 0:
        return None, 0
    return (series[-1][1] - series[0][1]) / span * 30.0, span


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
    # Series completas (todas las observaciones, no sólo la última): son la
    # materia prima del proxy de rotación por stylecolor.
    price_series: dict[int, list[dict]] = field(default_factory=dict)
    stock_series: dict[int, list[dict]] = field(default_factory=dict)
    stylecolors: dict[str, list[int]] = field(default_factory=dict)    # style_key -> products
    generations: dict[int, Generation] = field(default_factory=dict)
    _rotation_cache: dict[str, Rotation] = field(default_factory=dict, repr=False)
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

    # — stylecolor y rotación —

    def style_key_of(self, pid: int | None) -> str | None:
        product = self.product(pid)
        return style_key(product) if product else None

    def stylecolor_products(self, key: str) -> list[int]:
        return list(self.stylecolors.get(key) or [])

    def nike_stylecolors(self) -> list[str]:
        """Stylecolors Nike, en orden estable."""
        seen: list[str] = []
        for pid in self.nike_ids:
            key = self.style_key_of(pid)
            if key and key not in seen:
                seen.append(key)
        return seen

    def rotation(self, key: str) -> Rotation:
        """Proxy de rotación / WOH del stylecolor (memoizado).

        ``sufficient=False`` cuando la serie no alcanza para afirmar nada: sin
        eso, un stylecolor observado una sola vez parecería "sin exceso de
        inventario" simplemente porque no hay con qué contradecirlo.
        """
        cached = self._rotation_cache.get(key)
        if cached is not None:
            return cached

        pids = self.stylecolor_products(key)
        stock_rows = [row for pid in pids for row in (self.stock_series.get(pid) or [])]
        price_rows = [row for pid in pids for row in (self.price_series.get(pid) or [])]

        availability = _daily_series(stock_rows, _availability_of)
        discounts = _daily_series(
            price_rows,
            lambda r: float(r["discount_pct"]) if r.get("discount_pct") is not None else None,
        )

        min_obs = int(_rotation_cfg("min_observations", 3))
        min_span = float(_rotation_cfg("min_span_days", 45.0))

        slope, span = _slope_per_month(availability)
        depletion = None if slope is None else -slope          # >0 = drena
        discount_trend, _ = _slope_per_month(discounts)

        reason: str | None = None
        if len(availability) < min_obs:
            reason = (f"serie de stock insuficiente: {len(availability)} observación/es "
                      f"(mínimo {min_obs})")
        elif span < min_span:
            reason = f"la serie cubre {span} días (mínimo {min_span:.0f})"
        elif depletion is None:
            reason = "no se pudo estimar el drenaje de talles"

        now = availability[-1][1] if availability else None
        avg = (sum(v for _, v in availability) / len(availability)) if availability else None
        weeks = None
        if depletion is not None and depletion > 0 and now is not None:
            weeks = now / (depletion / 30.0 * 7.0)

        rotation = Rotation(
            style_key=key,
            product_ids=tuple(pids),
            n_observations=len(availability),
            span_days=span,
            availability_now=now,
            availability_avg=avg,
            depletion_pp_per_month=depletion,
            weeks_on_hand=weeks,
            discount_now=discounts[-1][1] if discounts else None,
            discount_trend_pp_per_month=discount_trend,
            sufficient=reason is None,
            reason=reason,
        )
        self._rotation_cache[key] = rotation
        return rotation

    # — vigencia de generación —

    def generation(self, pid: int | None) -> Generation | None:
        return self.generations.get(pid) if pid is not None else None

    def is_current_generation(self, pid: int | None) -> bool:
        gen = self.generation(pid)
        return True if gen is None else gen.is_current

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

    # Última observación por (producto, retailer) + serie completa (rotación).
    for row in query("SELECT * FROM price_observations ORDER BY observed_at, id", path=db_path):
        ctx.prices.setdefault(int(row["product_id"]), {})[int(row["retailer_id"])] = row
        ctx.price_series.setdefault(int(row["product_id"]), []).append(row)
    for row in query("SELECT * FROM stock_observations ORDER BY observed_at, id", path=db_path):
        ctx.stock.setdefault(int(row["product_id"]), {})[int(row["retailer_id"])] = row
        ctx.stock_series.setdefault(int(row["product_id"]), []).append(row)

    # Stylecolors (agrupa productos que comparten style_code/sku) y vigencia de
    # generación por linaje (ver `matching.generation_map`).
    for pid, product in ctx.products.items():
        ctx.stylecolors.setdefault(style_key(product), []).append(pid)
    ctx.generations = generation_map(ctx.products)

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


def _stylecolor_label(ctx: IntelContext, key: str, pid: int) -> str:
    """Nombre legible del stylecolor: producto + código de color."""
    name = ctx.name(pid)
    return name if key.startswith("product:") else f"{name} ({key})"


def _rule_full_price_opportunity(ctx: IntelContext) -> list[OpportunityDraft]:
    """Volver a precio lleno un STYLECOLOR que rota y no está bajo presión.

    Tres condiciones, y las tres son del stylecolor — no de la silueta ni de la
    franquicia:

      1. está descontado por encima de ``min_nike_discount_pct``;
      2. el competidor comparable NO está más barato que
         ``max_competitor_cheaper_pct`` (el descuento no responde a presión real);
      3. **rota**: su curva de talles se está drenando al menos
         ``rotation.rotating_min_depletion_pp_per_month`` y el markdown no viene
         profundizándose más rápido que ``rotation.markdown_trend_pp_per_month``.

    Sin serie suficiente para estimar (3) la regla NO dispara. Recomendar full
    price sobre un colorway que en realidad hay que liquidar cuesta plata; no
    decir nada, no.
    """
    cfg = _rule_cfg("full_price_opportunity")
    max_cheaper = float(cfg.get("max_competitor_cheaper_pct", 0.0))
    min_discount = float(cfg.get("min_nike_discount_pct", 0.0))
    min_depletion = float(_rotation_cfg("rotating_min_depletion_pp_per_month", 2.0))
    max_markdown_trend = float(_rotation_cfg("markdown_trend_pp_per_month", 3.0))
    drafts: list[OpportunityDraft] = []

    nike_set = set(ctx.nike_ids)
    for key in ctx.nike_stylecolors():
        pids = [p for p in ctx.stylecolor_products(key) if p in nike_set]
        nike_id = _representative(ctx, pids)
        if nike_id is None:
            continue

        rotation = ctx.rotation(key)
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

        # — gate de rotación: sin datos no se opina —
        if not rotation.sufficient:
            continue
        depletion = rotation.depletion_pp_per_month or 0.0
        trend = rotation.discount_trend_pp_per_month
        if depletion < min_depletion:
            continue
        if trend is not None and trend >= max_markdown_trend:
            continue

        woh = (f"{rotation.weeks_on_hand:.0f} semanas de cobertura"
               if rotation.weeks_on_hand is not None else "cobertura no acotada")
        drafts.append(OpportunityDraft(
            opportunity_type="full_price_opportunity",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=None,
            title=f"Oportunidad de full price en {_stylecolor_label(ctx, key, nike_id)}",
            description=(
                f"El stylecolor {key} sostiene {_fmt(nike_disc, 1)}% de descuento mientras el "
                f"competidor comparable ({ctx.name(comp_id)}) sólo está "
                f"{_fmt(max(gap, 0.0), 1)}% por debajo, y rota: su curva de talles drena "
                f"{_fmt(depletion, 1)}pp por mes ({woh}) con el markdown estable "
                f"({_fmt(trend, 1)}pp/mes). El descuento no responde a presión competitiva "
                f"real ni a exceso de inventario."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id, price_gap_pct=gap,
                promo_intensity_pct=nike_disc,
            ),
            action=ACTIONS["full_price_opportunity"],
            rationale=(
                f"Migrar el stylecolor {key} a venta full price recupera hasta "
                f"{_fmt(nike_disc, 1)}pp de margen: el gap competitivo ({_fmt(gap, 1)}%) está "
                f"por debajo del umbral de {_fmt(max_cheaper, 1)}% y la rotación "
                f"({_fmt(depletion, 1)}pp/mes) no exige liquidar. La decisión es de ESTE "
                f"colorway: otros de la misma silueta pueden necesitar lo contrario."
            ),
            country_code=ctx.product(nike_id).get("country_code"),
        ))
    return drafts


def _rule_clearance_needed(ctx: IntelContext) -> list[OpportunityDraft]:
    """Stylecolor con exceso de inventario: hay que liquidarlo sí o sí.

    La hermana simétrica de `full_price_opportunity`, sobre la misma evidencia:
    disponibilidad ALTA y SOSTENIDA (el size run no se drena) mientras el
    descuento se profundiza. Ese colorway acumula WOH y hay que sacarlo, aunque
    su franquicia esté competitiva en precio.
    """
    cfg = _rule_cfg("clearance_needed")
    min_availability = float(cfg.get("min_availability_pct",
                                     _rotation_cfg("high_availability_pct", 60.0)))
    max_depletion = float(cfg.get("max_depletion_pp_per_month",
                                  _rotation_cfg("rotating_min_depletion_pp_per_month", 2.0)))
    min_trend = float(cfg.get("min_discount_trend_pp_per_month",
                              _rotation_cfg("markdown_trend_pp_per_month", 3.0)))
    min_discount = float(cfg.get("min_nike_discount_pct", 10.0))
    drafts: list[OpportunityDraft] = []

    nike_set = set(ctx.nike_ids)
    for key in ctx.nike_stylecolors():
        pids = [p for p in ctx.stylecolor_products(key) if p in nike_set]
        nike_id = _representative(ctx, pids)
        if nike_id is None:
            continue

        rotation = ctx.rotation(key)
        if not rotation.sufficient:
            continue
        depletion = rotation.depletion_pp_per_month
        trend = rotation.discount_trend_pp_per_month
        level = rotation.availability_avg
        discount = rotation.discount_now if rotation.discount_now is not None else ctx.avg_discount(nike_id)
        if depletion is None or level is None or trend is None or discount is None:
            continue
        if level < min_availability or depletion >= max_depletion or trend < min_trend:
            continue
        if discount < min_discount:
            continue

        comp_id = _top_competitor(ctx, nike_id)
        drafts.append(OpportunityDraft(
            opportunity_type="clearance_needed",
            nike_product_id=nike_id,
            competitor_product_id=comp_id,
            retailer_id=None,
            title=f"Liquidación necesaria en {_stylecolor_label(ctx, key, nike_id)}",
            description=(
                f"El stylecolor {key} sostiene {_fmt(level, 1)}% de disponibilidad de talles "
                f"con un drenaje de apenas {_fmt(depletion, 1)}pp por mes (umbral de rotación: "
                f"{_fmt(max_depletion, 1)}pp) mientras el descuento se profundiza "
                f"{_fmt(trend, 1)}pp por mes (hoy {_fmt(discount, 1)}%): el inventario de ESTE "
                f"colorway no está rotando."
            ),
            drivers=[],
            importance_inputs=ctx.importance_inputs(
                nike_id, competitor_id=comp_id, promo_intensity_pct=discount,
            ),
            action=ACTIONS["clearance_needed"],
            rationale=(
                f"Con la curva de talles estancada en {_fmt(level, 1)}% y el markdown subiendo "
                f"{_fmt(trend, 1)}pp/mes, este colorway acumula semanas de cobertura: conviene "
                f"un plan de salida (profundidad de descuento, reubicación de stock) antes de "
                f"que el markdown se coma el margen igual pero más tarde. NO aplica al resto "
                f"de la silueta: cada stylecolor rota distinto."
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


# ── diversidad: agrupación de oportunidades del mismo SKU ───
#
# Un mismo producto Nike puede disparar varias veces la MISMA regla: tres
# lanzamientos de competidores distintos contra la Pegasus son tres filas de
# `product_launch_threat` que dicen lo mismo y ocupan tres lugares del ranking.
#
# Se AGRUPAN, no se descartan: la fila resultante conserva los datos de todos
# los casos (los describe uno por uno) y apunta al más grave, que es el que
# define la acción. Esto vive DENTRO de las reglas, no en el persistidor,
# porque `app.calibration` audita "drafts producidos vs filas persistidas" y
# esa igualdad es lo que detecta una regla rota.


def _group_by_product(drafts: list[OpportunityDraft], *,
                      connector: str = "Además") -> list[OpportunityDraft]:
    """Colapsa a una sola oportunidad los drafts del mismo producto Nike.

    Preserva el orden de aparición y deja intactos los drafts sin producto
    (las oportunidades de retailer o de segmento sin representante).
    """
    if not bool(section("opportunities", "diversity", "group_same_product", default=True)):
        return drafts

    grouped: dict[int, OpportunityDraft] = {}
    extras: dict[int, list[OpportunityDraft]] = {}
    out: list[OpportunityDraft] = []

    for draft in drafts:
        pid = draft.nike_product_id
        if pid is None:
            out.append(draft)
            continue
        if pid not in grouped:
            grouped[pid] = draft
            extras[pid] = []
            out.append(draft)
        else:
            extras[pid].append(draft)

    for pid, others in extras.items():
        if not others:
            continue
        head = grouped[pid]
        cases = " ".join(f"{connector}: {o.description}" for o in others)
        head.description = f"{head.description} {cases}"
        head.rationale = (
            f"{head.rationale} Se agruparon {len(others) + 1} casos del mismo producto "
            f"para no saturar el ranking; la acción cubre a todos."
        )
        head.importance_inputs = dict(head.importance_inputs)
        head.importance_inputs["grouped_cases"] = len(others) + 1
    return out


def _grouped(rule):
    """Envuelve una regla para que agrupe sus drafts por producto Nike."""

    def wrapped(ctx: IntelContext) -> list[OpportunityDraft]:
        return _group_by_product(rule(ctx))

    wrapped.__name__ = getattr(rule, "__name__", "rule")
    wrapped.__doc__ = rule.__doc__
    return wrapped


RULES = {
    "price_competitiveness_risk": _grouped(_rule_price_competitiveness_risk),
    "over_discounting_risk": _grouped(_rule_over_discounting_risk),
    "full_price_opportunity": _grouped(_rule_full_price_opportunity),
    "clearance_needed": _grouped(_rule_clearance_needed),
    "assortment_gap": _grouped(_rule_assortment_gap),
    "distribution_gap": _grouped(_rule_distribution_gap),
    "share_of_shelf_risk": _grouped(_rule_share_of_shelf_risk),
    "competitor_momentum": _grouped(_rule_competitor_momentum),
    "competitor_stockout_opportunity": _grouped(_rule_competitor_stockout_opportunity),
    "assortment_white_space": _grouped(_rule_assortment_white_space),
    "premiumization_opportunity": _grouped(_rule_premiumization_opportunity),
    "promotional_pressure": _grouped(_rule_promotional_pressure),
    "product_launch_threat": _grouped(_rule_product_launch_threat),
}


# ── helpers de selección ────────────────────────────────────


def _representative(ctx: IntelContext, candidates: list[int]) -> int | None:
    """Producto Nike de referencia de un conjunto (segmento, stylecolor, ...).

    Criterio, en orden: importancia de la franquicia -> **generación vigente**
    -> proxy de facturación. La vigencia entra en el medio a propósito: define
    QUÉ Pegasus representa a "daily running" (la 42, no la 41), pero no puede
    hacer que una franquicia menor le gane a una franquicia mayor.
    """
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda pid: (
            scoring.franchise_importance(ctx.product(pid).get("franchise")),
            1 if ctx.is_current_generation(pid) else 0,
            ctx.revenue_proxy_raw(pid) or 0.0,
        ),
    )


def _top_competitor(ctx: IntelContext, nike_id: int | None) -> int | None:
    if nike_id is None:
        return None
    match = ctx.best_match(nike_id)
    return int(match["competitor_product_id"]) if match else None


def _nike_counterpart(ctx: IntelContext, comp_id: int) -> int | None:
    """Producto Nike enfrentado a un competidor: por match, si no por segmento.

    A igualdad de match gana la generación vigente: el equipo comercial defiende
    la Metcon 10, no la 9. (El score de match ya viene atenuado para las
    generaciones salientes — ver `matching._apply_generation_penalty` —, esto
    resuelve los empates que quedan.)
    """
    best: tuple[float, int, int] | None = None
    for (nike_id, other), score in ctx.pair_scores.items():
        if other != comp_id:
            continue
        candidate = (score, 1 if ctx.is_current_generation(nike_id) else 0, nike_id)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best:
        return best[2]
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


def _diversity_factors(rows: list[tuple]) -> list[float]:
    """Factor de prioridad por repetición del mismo producto Nike.

    El Opportunity Center ordena por `business_importance`, así que un producto
    con muchas oportunidades legítimas se queda con toda la primera pantalla:
    en la base demo, 8 de las 10 primeras filas eran la misma zapatilla. Una
    lista así no sirve para decidir — el que la lee ya sabía que ese producto
    importa.

    En vez de descartar filas (perdería oportunidades reales), la n-ésima
    oportunidad de un mismo producto entra al ranking con la prioridad
    atenuada:

        factor = max(min_factor, repeat_decay ** k)      # k = 0, 1, 2, ...

    La más grave de cada producto NO se toca (k=0, factor 1.0): compite de igual
    a igual. Las repeticiones bajan pero nunca desaparecen — con `min_factor`
    conservan un piso — así que siguen estando en la lista, filtrables por
    producto y por tipo, con su importancia original guardada en `drivers`.
    """
    cfg = section("opportunities", "diversity", default={}) or {}
    if not bool(cfg.get("enabled", True)):
        return [1.0] * len(rows)

    decay = float(cfg.get("repeat_decay", 0.80))
    floor = float(cfg.get("min_factor", 0.40))

    order = sorted(range(len(rows)), key=lambda i: -float(rows[i][1].score))
    seen: dict[int, int] = {}
    factors = [1.0] * len(rows)
    for i in order:
        pid = rows[i][0].nike_product_id
        if pid is None:                      # oportunidades de retailer/segmento
            continue
        k = seen.get(pid, 0)
        seen[pid] = k + 1
        factors[i] = max(floor, decay ** k) if k else 1.0
    return factors


def _diversity_driver(base: float, adjusted: float, factor: float) -> dict:
    """Driver informativo: qué importancia tenía la fila antes de la atenuación."""
    return {
        "name": "diversity_factor",
        "value": round(factor, 4),
        "contribution": 0.0,
        "weight": 0.0,
        "detail": {
            "base_importance": round(base, 2),
            "adjusted_importance": round(adjusted, 2),
            "reason": ("no es la oportunidad más grave de este producto Nike: baja en el "
                       "ranking para que la lista no la ocupe un solo SKU"),
        },
    }


def run_opportunities(db_path: Any = DB_PATH) -> dict[str, int]:
    """Ejecuta las reglas, puntúa, y persiste. Idempotente (borra y recalcula)."""
    ctx = build_context(db_path)

    counts: dict[str, int] = {name: 0 for name in ALL_OPPORTUNITY_TYPES}
    rows: list[tuple] = []

    for opportunity_type in ALL_OPPORTUNITY_TYPES:
        for draft in RULES[opportunity_type](ctx):
            importance = scoring.business_importance(draft.importance_inputs, ctx)
            drivers = draft.drivers or _drivers_from(importance)
            rows.append((draft, importance, drivers))
            counts[opportunity_type] += 1

    factors = _diversity_factors(rows)

    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM recommendations")
        conn.execute("DELETE FROM opportunities")
        for (draft, importance, drivers), factor in zip(rows, factors):
            score = round(importance.score * factor, 2)
            if factor < 1.0:
                drivers = [*drivers, _diversity_driver(importance.score, score, factor)]
            cur = conn.execute(
                "INSERT INTO opportunities (opportunity_type, family, severity, "
                "nike_product_id, competitor_product_id, retailer_id, country_code, title, "
                "description, business_importance, confidence, drivers) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    draft.opportunity_type,
                    family_of(draft.opportunity_type),
                    scoring.severity(score),
                    draft.nike_product_id,
                    draft.competitor_product_id,
                    draft.retailer_id,
                    draft.country_code,
                    draft.title,
                    draft.description,
                    score,
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
                    score,
                    importance.confidence,
                    to_json(drivers),
                ),
            )

    counts["opportunities"] = len(rows)
    counts["recommendations"] = len(rows)
    return counts
