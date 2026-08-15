"""Harness de calibración del motor.

Problema que resuelve
---------------------
Todos los pesos y umbrales viven en ``config/weights.yaml``, pero un umbral sólo
tiene sentido CONTRA LA ESCALA REAL de la métrica que filtra. Dos veces ya pasó
que la calibración era internamente inconsistente y nadie lo vio hasta mirar la
salida:

1. ``business_importance.severity_thresholds`` en 78/60/40 sobre una escala cuyo
   techo alcanzable era ~0.69 * 100: CRITICAL y HIGH eran imposibles POR
   CONSTRUCCIÓN y las 60 oportunidades caían todas en MEDIUM/LOW.
2. ``opportunities.premiumization_opportunity.min_match_score`` en 70 sobre una
   escala cuyo máximo observado era 69: la regla nunca disparaba.

Los dos se descubrieron a ojo. Este módulo los descubre solo.

Qué hace
--------
* ``score_distributions``  distribución empírica (percentiles) de cada métrica
  que algún umbral filtra.
* ``reachability_report``  cada umbral de weights.yaml contra el rango REALMENTE
  alcanzable de su métrica -> UNREACHABLE / TRIVIAL / OK / NO_DATA.
* ``suggest_thresholds``   cortes propuestos desde la distribución observada.
  NO escribe el YAML: emite el snippet para que la decisión la tome una persona.
* ``rule_yield_report``    cuántas oportunidades produce cada una de las 12
  reglas y, para las que dan 0, si están ROTAS o simplemente no hay nada que
  reportar.
* ``sensitivity``          barrido de un parámetro: cómo cambian conteos y
  rankings. Restaura SIEMPRE la config original.
* ``report``               todo junto. CLI: ``python -m app.calibration``.

Cotas analíticas (el corazón de ``reachability_report``)
-------------------------------------------------------
Comparar contra el máximo observado alcanza para detectar un umbral muerto, pero
no explica POR QUÉ está muerto ni sobrevive a un cambio de dataset. Donde la
fórmula permite razonar el techo, se razona:

``match_score`` (ajustado por evidencia)
    ``ajustado = crudo * cobertura + 100 * prior * (1 - cobertura)``.
    Con ``crudo <= 100``: ``ajustado <= 100 * C + 100 * prior * (1 - C)`` donde
    ``C`` es la cobertura máxima ALCANZABLE del corpus (no 1.0: un factor que
    nunca tiene datos —p.ej. ``visual`` sin CLIP ni atributos visuales— le pone
    un techo duro a la cobertura y por lo tanto al score ajustado).

``business_importance``
    ``importance = base * gate * lifecycle``, con
    ``base = 100 * sum(w_i s_i) / sum(w_i disponibles)``,
    ``gate = clamp(competitive_relevance, gate_floor, 1)`` y
    ``competitive_relevance = match_score / 100``.
    El gate es MULTIPLICATIVO, así que el techo sale de dos ramas:

      a) con relevancia medida: ``s_relevance <= R = max(match_score)/100``, y
         como ese componente entra en ``base`` con peso ``w_rel``:
         ``base <= 100 * (W - w_rel * (1 - R)) / W`` con ``W = sum(w)``;
         entonces ``importance <= base_max * R * max(lifecycle_multiplier)``.
      b) sin relevancia medida: ``base <= 100`` pero ``gate = gate_floor``:
         ``importance <= 100 * gate_floor * max(lifecycle_multiplier)``.

    El techo es el máximo de (a) y (b), acotado a 100. Con el dataset demo
    (R=0.69, w_rel=0.20, W=1.0, lifecycle max 1.15) da ~74.6: cualquier umbral
    de severidad por encima de ese número es inalcanzable POR CONSTRUCCIÓN,
    aunque el dataset cambie de valores (mientras no cambie la escala de match).

``retail_media`` score
    Mismo argumento encadenado: ``competitive_relevance`` (w 0.15) y
    ``business_importance`` (w 0.15) arrastran sus propios techos, así que
    ``score <= 100 * (W - w_rel*(1-R) - w_bi*(1-B/100)) / W``.

Cuando la cota analítica no es más ajustada que el máximo observado se informa
igual el máximo observado, y el motivo dice cuál de las dos mandó.

Uso
---
    python -m app.pipeline           # puebla la base
    python -m app.calibration        # reporte legible
    python -m app.calibration --json # mismo reporte, JSON
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np

from app.config import DB_PATH, get_config, reload_config, section, weights
from app.db import query
from app.services import matching, opportunities, retail_media, scoring
from app.services.common import clamp, parse_date

# Percentiles reportados para toda métrica.
PERCENTILES: tuple[int, ...] = (5, 10, 25, 50, 75, 90, 95)

# Fracción de registros que debería dejar pasar un umbral que hoy no filtra
# nada o que no deja pasar nada. Es un punto de partida, no una verdad.
DEFAULT_TARGET_PASS = 0.25

STATUS_OK = "OK"
STATUS_UNREACHABLE = "UNREACHABLE"
STATUS_TRIVIAL = "TRIVIAL"
STATUS_NO_DATA = "NO_DATA"


# ════════════════════════════════════════════════════════════
#  Métricas: qué mide cada escala y cuál es su techo
# ════════════════════════════════════════════════════════════


@dataclass
class Metric:
    """Una escala del motor con su muestra observada y su cota analítica."""

    key: str
    label: str
    values: list[float] = field(default_factory=list)
    unit: str = ""
    source: str = ""
    # Cotas DURAS por definición de la métrica (p.ej. un porcentaje: 0..100).
    hard_min: float | None = None
    hard_max: float | None = None
    # Cota ALCANZABLE razonada desde las fórmulas (None => se usa el máximo
    # observado como único criterio).
    analytic_max: float | None = None
    analytic_min: float | None = None
    analytic_note: str = ""

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def observed_max(self) -> float | None:
        return max(self.values) if self.values else None

    @property
    def observed_min(self) -> float | None:
        return min(self.values) if self.values else None

    def ceiling(self) -> float | None:
        """Techo efectivo: el más ajustado entre la cota analítica y lo observado."""
        candidates = [c for c in (self.analytic_max, self.observed_max) if c is not None]
        return min(candidates) if candidates else None

    def floor(self) -> float | None:
        candidates = [c for c in (self.analytic_min, self.observed_min) if c is not None]
        return max(candidates) if candidates else None

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "label": self.label,
            "unit": self.unit,
            "source": self.source,
            "n": self.n,
            "analytic_max": _round(self.analytic_max),
            "analytic_min": _round(self.analytic_min),
            "analytic_note": self.analytic_note,
            "hard_scale": [self.hard_min, self.hard_max],
        }
        if not self.values:
            out.update({p_key(p): None for p in PERCENTILES})
            out.update({"min": None, "max": None, "mean": None, "std": None})
            return out
        arr = np.asarray(self.values, dtype=float)
        out.update({
            "min": _round(float(arr.min())),
            "max": _round(float(arr.max())),
            "mean": _round(float(arr.mean())),
            "std": _round(float(arr.std())),
        })
        for p in PERCENTILES:
            out[p_key(p)] = _round(float(np.percentile(arr, p)))
        return out

    def percentile(self, p: float) -> float | None:
        if not self.values:
            return None
        return float(np.percentile(np.asarray(self.values, dtype=float), p))

    def pass_count(self, threshold: float, direction: str) -> int:
        if direction == "min":
            return sum(1 for v in self.values if v >= threshold)
        return sum(1 for v in self.values if v <= threshold)


def p_key(p: int) -> str:
    return f"p{p}"


def _round(value: Any, digits: int = 4) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


# ════════════════════════════════════════════════════════════
#  Umbrales declarados en weights.yaml
# ════════════════════════════════════════════════════════════


# Tipos de umbral. La distinción importa porque TRIVIAL significa cosas
# distintas según para qué existe el umbral:
#   discriminator  selecciona un subconjunto (qué dispara, qué se reporta).
#                  Que lo pase todo es un defecto: no está decidiendo nada.
#   band           corte de una etiqueta ordinal (severidad, confianza). Se
#                  juzga por grupo: cada banda tiene que tener masa.
#   gate           mínimo de evidencia para que una señal cuente. Que lo pase
#                  todo NO es un defecto (el corpus tiene evidencia de sobra);
#                  que no lo pase nadie SÍ lo es: apaga la señal entera.
#   scale          parámetro de forma (tolerancias, pisos). Sólo informativo.
KIND_DISCRIMINATOR = "discriminator"
KIND_BAND = "band"
KIND_GATE = "gate"
KIND_SCALE = "scale"


@dataclass(frozen=True)
class ThresholdSpec:
    """Un umbral de config y la métrica contra la que hay que juzgarlo.

    ``direction``:
      * ``min``: un registro pasa si ``metrica >= umbral``
      * ``max``: un registro pasa si ``metrica <= umbral``
    """

    path: str
    metric: str
    direction: str
    what: str                       # qué decide el umbral, en una línea
    rule: str | None = None         # regla / módulo que lo consume
    target_pass: float = DEFAULT_TARGET_PASS
    kind: str = KIND_DISCRIMINATOR

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.path.split("."))

    def is_defect(self, status: str) -> bool:
        """¿El veredicto es un bug de calibración o una observación?"""
        if status == STATUS_UNREACHABLE:
            return True
        if status == STATUS_TRIVIAL:
            return self.kind in (KIND_DISCRIMINATOR, KIND_BAND)
        return False


@dataclass(frozen=True)
class BandGroup:
    """Grupo de cortes que producen una etiqueta ordinal.

    Un corte de banda no se juzga solo sino contra sus hermanos: el defecto no
    es "78 es alto", es "con 78/60/40 NINGUNA oportunidad cae en CRITICAL ni en
    HIGH, así que la etiqueta dejó de informar".
    """

    name: str
    metric: str
    paths: dict[str, str]           # banda -> ruta en el yaml (de mayor a menor)
    targets: dict[str, float]       # banda -> fracción de registros por encima
    bottom_label: str               # etiqueta para lo que queda por debajo


BAND_GROUPS: tuple[BandGroup, ...] = (
    BandGroup(
        name="severidad de oportunidades",
        metric="business_importance",
        paths={
            "CRITICAL": "business_importance.severity_thresholds.critical",
            "HIGH": "business_importance.severity_thresholds.high",
            "MEDIUM": "business_importance.severity_thresholds.medium",
        },
        targets={"CRITICAL": 0.10, "HIGH": 0.25, "MEDIUM": 0.50},
        bottom_label="LOW",
    ),
    BandGroup(
        name="confianza del match",
        metric="match_coverage",
        paths={
            "HIGH": "competitive_match.confidence_thresholds.high",
            "MEDIUM": "competitive_match.confidence_thresholds.medium",
        },
        targets={"HIGH": 0.25, "MEDIUM": 0.60},
        bottom_label="LOW",
    ),
    BandGroup(
        name="confianza de los brand insights",
        metric="insight_signal_volume",
        paths={
            "HIGH": "brand_intelligence.confidence.high_min_volume",
            "MEDIUM": "brand_intelligence.confidence.medium_min_volume",
        },
        targets={"HIGH": 0.25, "MEDIUM": 0.60},
        bottom_label="LOW",
    ),
)


THRESHOLDS: tuple[ThresholdSpec, ...] = (
    # ── competitive match ──────────────────────────────────
    ThresholdSpec(
        "competitive_match.min_score_to_persist", "match_score_all_pairs", "min",
        "qué pares se guardan como competidores", rule="matching",
    ),
    ThresholdSpec(
        "competitive_match.confidence_thresholds.high", "match_coverage", "min",
        "cobertura mínima para confianza HIGH", rule="matching", kind=KIND_BAND,
    ),
    ThresholdSpec(
        "competitive_match.confidence_thresholds.medium", "match_coverage", "min",
        "cobertura mínima para confianza MEDIUM", rule="matching", kind=KIND_BAND,
    ),
    ThresholdSpec(
        "competitive_match.visual.min_evidence_weight", "visual_evidence_weight", "min",
        "evidencia visual mínima para que el factor cuente", rule="matching", kind=KIND_GATE,
    ),
    ThresholdSpec(
        "competitive_match.social.min_comentions", "pair_comentions", "min",
        "co-menciones mínimas para que el factor social cuente", rule="matching", kind=KIND_GATE,
    ),
    ThresholdSpec(
        "competitive_match.reviews.min_reviews_for_signal", "product_review_volume", "min",
        "volumen de reviews mínimo para que el factor reviews cuente", rule="matching",
        kind=KIND_GATE,
    ),
    ThresholdSpec(
        "competitive_match.price.gap_tolerance_pct", "abs_msrp_gap_pct", "max",
        "gap de precio a partir del cual la similitud de precio es 0",
        rule="matching", kind=KIND_SCALE,
    ),
    # ── business importance ────────────────────────────────
    ThresholdSpec(
        "business_importance.severity_thresholds.critical", "business_importance", "min",
        "corte de severidad CRITICAL", rule="scoring", target_pass=0.10, kind=KIND_BAND,
    ),
    ThresholdSpec(
        "business_importance.severity_thresholds.high", "business_importance", "min",
        "corte de severidad HIGH", rule="scoring", target_pass=0.25, kind=KIND_BAND,
    ),
    ThresholdSpec(
        "business_importance.severity_thresholds.medium", "business_importance", "min",
        "corte de severidad MEDIUM", rule="scoring", target_pass=0.50, kind=KIND_BAND,
    ),
    ThresholdSpec(
        "business_importance.gate_floor", "competitive_relevance", "min",
        "piso del gate multiplicativo de relevancia competitiva",
        rule="scoring", kind=KIND_SCALE,
    ),
    # ── retail media ───────────────────────────────────────
    ThresholdSpec(
        "retail_media.thresholds.min_score_to_report", "retail_media_score", "min",
        "qué tripletes (Nike, competidor, retailer) se reportan", rule="retail_media",
    ),
    ThresholdSpec(
        "retail_media.thresholds.price_competitive_pct", "price_gap_pct", "max",
        "hasta qué gap Nike se considera competitivo en precio", rule="retail_media",
    ),
    ThresholdSpec(
        "retail_media.thresholds.price_disadvantage_pct", "price_gap_pct", "min",
        "desde qué gap hay que revisar precio antes que media", rule="retail_media",
    ),
    ThresholdSpec(
        "retail_media.thresholds.nike_stock_high_pct", "nike_availability_pct", "min",
        "stock Nike considerado alto", rule="retail_media",
    ),
    ThresholdSpec(
        "retail_media.thresholds.nike_stock_low_pct", "nike_availability_pct", "max",
        "stock Nike considerado bajo", rule="retail_media",
    ),
    ThresholdSpec(
        "retail_media.thresholds.competitor_stockout_pct", "competitor_availability_pct", "max",
        "disponibilidad del competidor considerada quiebre", rule="retail_media",
    ),
    ThresholdSpec(
        "retail_media.thresholds.high_momentum", "competitor_momentum", "min",
        "momentum del competidor considerado alto", rule="retail_media",
    ),
    # ── las 12 reglas de oportunidades ─────────────────────
    ThresholdSpec(
        "opportunities.price_competitiveness_risk.min_competitor_cheaper_pct",
        "price_gap_pct_retailer", "min",
        "cuánto más barato tiene que estar el competidor en un retailer",
        rule="price_competitiveness_risk",
    ),
    ThresholdSpec(
        "opportunities.price_competitiveness_risk.min_retailers",
        "cheaper_retailers_count", "min",
        "en cuántos retailers tiene que repetirse el gap",
        rule="price_competitiveness_risk",
    ),
    ThresholdSpec(
        "opportunities.over_discounting_risk.min_discount_gap_pp", "discount_gap_pp", "min",
        "cuántos pp más que los comparables descuenta Nike",
        rule="over_discounting_risk",
    ),
    ThresholdSpec(
        "opportunities.over_discounting_risk.max_price_disadvantage_pct", "price_gap_pct", "max",
        "desventaja de precio máxima para considerar el descuento innecesario",
        rule="over_discounting_risk",
    ),
    ThresholdSpec(
        "opportunities.full_price_opportunity.max_competitor_cheaper_pct", "price_gap_pct", "max",
        "hasta qué gap el descuento Nike no responde a presión real",
        rule="full_price_opportunity",
    ),
    ThresholdSpec(
        "opportunities.full_price_opportunity.min_nike_discount_pct", "nike_discount_pct", "min",
        "descuento Nike mínimo para que valga volver a full price",
        rule="full_price_opportunity",
    ),
    ThresholdSpec(
        "opportunities.assortment_gap.min_sku_ratio", "segment_sku_ratio", "min",
        "ratio SKUs competidores / Nike en el segmento", rule="assortment_gap",
    ),
    ThresholdSpec(
        "opportunities.assortment_gap.min_competitor_skus", "segment_competitor_skus", "min",
        "SKUs de competidores mínimos en el segmento", rule="assortment_gap",
    ),
    ThresholdSpec(
        "opportunities.distribution_gap.min_retailer_coverage_gap", "retailer_coverage_gap", "min",
        "retailers donde está el competidor y Nike no", rule="distribution_gap",
    ),
    ThresholdSpec(
        "opportunities.share_of_shelf_risk.min_shelf_drop_pp", "shelf_drop_pp", "min",
        "caída de share of shelf en pp", rule="share_of_shelf_risk",
    ),
    ThresholdSpec(
        "opportunities.competitor_momentum.min_acceleration", "competitor_acceleration", "min",
        "aceleración del competidor en señales de mercado", rule="competitor_momentum",
    ),
    ThresholdSpec(
        "opportunities.competitor_stockout_opportunity.max_competitor_availability_pct",
        "competitor_availability_pct", "max",
        "disponibilidad del competidor considerada quiebre",
        rule="competitor_stockout_opportunity",
    ),
    ThresholdSpec(
        "opportunities.competitor_stockout_opportunity.min_nike_availability_pct",
        "nike_availability_pct", "min",
        "disponibilidad Nike mínima para capturar el quiebre",
        rule="competitor_stockout_opportunity",
    ),
    ThresholdSpec(
        "opportunities.assortment_white_space.min_demand_signal", "segment_demand_signal", "min",
        "señal de demanda relativa del segmento", rule="assortment_white_space",
    ),
    ThresholdSpec(
        "opportunities.assortment_white_space.max_nike_share", "segment_nike_share", "max",
        "share de SKUs Nike por debajo del cual hay espacio en blanco",
        rule="assortment_white_space",
    ),
    ThresholdSpec(
        "opportunities.premiumization_opportunity.min_match_score", "match_score", "min",
        "match competitivo mínimo para justificar subir precio",
        rule="premiumization_opportunity",
    ),
    ThresholdSpec(
        "opportunities.premiumization_opportunity.min_nike_cheaper_pct", "nike_cheaper_pct", "min",
        "cuánto más barato está Nike que el competidor equivalente",
        rule="premiumization_opportunity",
    ),
    ThresholdSpec(
        "opportunities.promotional_pressure.min_competitors_on_markdown",
        "competitors_on_markdown", "min",
        "competidores comparables en markdown", rule="promotional_pressure",
    ),
    ThresholdSpec(
        "opportunities.promotional_pressure.min_avg_discount_pct", "competitor_discount_pct", "min",
        "descuento a partir del cual un competidor cuenta como markdown",
        rule="promotional_pressure",
    ),
    ThresholdSpec(
        "opportunities.product_launch_threat.max_days_since_launch", "days_since_launch", "max",
        "antigüedad máxima de un lanzamiento para considerarlo amenaza",
        rule="product_launch_threat",
    ),
    ThresholdSpec(
        "opportunities.product_launch_threat.min_retailer_coverage",
        "competitor_retailer_coverage", "min",
        "retailers mínimos donde ya está el lanzamiento", rule="product_launch_threat",
    ),
    # ── brand intelligence ─────────────────────────────────
    ThresholdSpec(
        "brand_intelligence.confidence.high_min_volume", "insight_signal_volume", "min",
        "volumen de señal para confianza HIGH de un insight", rule="brand_intelligence",
        kind=KIND_BAND,
    ),
    ThresholdSpec(
        "brand_intelligence.confidence.medium_min_volume", "insight_signal_volume", "min",
        "volumen de señal para confianza MEDIUM de un insight", rule="brand_intelligence",
        kind=KIND_BAND,
    ),
)

# Señales de entrada por regla: sirve para distinguir "la regla está rota"
# (umbral inalcanzable) de "no hay nada que reportar" (no hay señal de entrada).
RULE_INPUTS: dict[str, tuple[str, ...]] = {
    "price_competitiveness_risk": ("price_gap_pct_retailer",),
    "over_discounting_risk": ("discount_gap_pp", "price_gap_pct"),
    "full_price_opportunity": ("nike_discount_pct", "price_gap_pct"),
    "assortment_gap": ("segment_sku_ratio", "segment_competitor_skus"),
    "distribution_gap": ("retailer_coverage_gap",),
    "share_of_shelf_risk": ("shelf_drop_pp",),
    "competitor_momentum": ("competitor_acceleration",),
    "competitor_stockout_opportunity": ("competitor_availability_pct", "nike_availability_pct"),
    "assortment_white_space": ("segment_demand_signal", "segment_nike_share"),
    "premiumization_opportunity": ("match_score", "nike_cheaper_pct"),
    "promotional_pressure": ("competitors_on_markdown", "competitor_discount_pct"),
    "product_launch_threat": ("days_since_launch", "competitor_retailer_coverage"),
}


# ════════════════════════════════════════════════════════════
#  Recolección de la muestra
# ════════════════════════════════════════════════════════════


@dataclass
class CalibrationData:
    """Todo lo que el harness necesita, calculado una sola vez."""

    db_path: Path
    metrics: dict[str, Metric]
    ctx: Any                                  # opportunities.IntelContext
    drafts: dict[str, list] = field(default_factory=dict)
    persisted_opportunities: dict[str, int] = field(default_factory=dict)
    importance_by_rule: dict[str, list[float]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def metric(self, key: str) -> Metric:
        return self.metrics.get(key) or Metric(key=key, label=key)


def _add(metrics: dict[str, Metric], key: str, label: str, values: Sequence[float],
         **kwargs: Any) -> Metric:
    clean = [float(v) for v in values if v is not None and float(v) == float(v)]
    metric = Metric(key=key, label=label, values=clean, **kwargs)
    metrics[key] = metric
    return metric


def _pair_recompute(db_path: Path | str) -> list[dict[str, Any]]:
    """Recalcula TODOS los pares evaluables (sin censurar por umbral).

    La tabla ``competitive_matches`` sólo guarda lo que superó
    ``min_score_to_persist``: juzgar ese umbral contra su propia salida es
    circular. Acá se reconstruye la distribución completa.
    """
    mctx = matching.build_context(db_path)
    nike_products = [p for p in mctx.products.values() if p.get("brand_is_focus")]
    rows: list[dict[str, Any]] = []
    for nike in nike_products:
        for comp in mctx.products.values():
            if comp["brand_id"] == nike["brand_id"]:
                continue
            if comp.get("country_code") != nike.get("country_code"):
                continue
            result = matching.compute_match(nike, comp, mctx)
            rows.append({
                "nike_product_id": nike["id"],
                "competitor_product_id": comp["id"],
                "raw": result.score,
                "adjusted": matching.evidence_adjusted(result.score, result.coverage),
                "coverage": result.coverage,
                "factors": result.factors,
            })
    return rows


def _retail_media_recompute(ctx: Any) -> list[float]:
    """Scores de retail media de todos los tripletes, sin censurar por umbral."""
    scores: list[float] = []
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
                signals = retail_media.build_signals(nike_product, competitor, retailer, ctx)
                scores.append(retail_media.score_from_signals(signals).score)
    return scores


def _analytic_bounds(metrics: dict[str, Metric]) -> None:
    """Escribe las cotas analíticas razonadas en el docstring del módulo."""
    cfg_shrink = section("competitive_match", "evidence_shrinkage", default={}) or {}
    prior = float(cfg_shrink.get("prior", 0.0)) if cfg_shrink.get("enabled", False) else None

    # — match_score ajustado —
    coverage = metrics.get("match_coverage_all_pairs") or metrics.get("match_coverage")
    cov_max = coverage.observed_max if coverage and coverage.values else None
    for key in ("match_score", "match_score_all_pairs"):
        metric = metrics.get(key)
        if metric is None:
            continue
        if prior is None or cov_max is None:
            metric.analytic_max = 100.0
            metric.analytic_note = "crudo <= 100 (sin shrinkage activo)"
            continue
        ceiling = 100.0 * cov_max + 100.0 * prior * (1.0 - cov_max)
        metric.analytic_max = ceiling
        metric.analytic_note = (
            f"ajustado = crudo*C + 100*prior*(1-C); crudo<=100, C_max={cov_max:.2f} "
            f"(un factor sin datos nunca en el corpus le pone techo a la cobertura), "
            f"prior={prior:.2f} => techo {ceiling:.1f}"
        )

    for key in ("match_coverage", "match_coverage_all_pairs"):
        metric = metrics.get(key)
        if metric is not None:
            metric.analytic_max = 1.0
            base = "cobertura = suma de pesos con datos / peso total <= 1.0"
            metric.analytic_note = f"{base}; {metric.analytic_note}" if metric.analytic_note else base

    # — competitive_relevance = match_score / 100 —
    match = metrics.get("match_score")
    relevance = metrics.get("competitive_relevance")
    if relevance is not None and match is not None and match.analytic_max is not None:
        relevance.analytic_max = match.analytic_max / 100.0
        relevance.analytic_note = "= match_score persistido / 100"

    # — business_importance —
    bi = metrics.get("business_importance")
    if bi is not None:
        w = weights("business_importance", "weights")
        total_w = sum(w.values()) or 1.0
        w_rel = float(w.get("competitive_relevance", 0.0))
        floor = float(section("business_importance", "gate_floor", default=0.0) or 0.0)
        lifecycle = section("business_importance", "lifecycle_multiplier", default={}) or {}
        life_max = max([float(v) for v in lifecycle.values()] or [1.0])
        r_max = (match.observed_max / 100.0) if (match and match.values) else 1.0

        with_relevance = 100.0 * (total_w - w_rel * (1.0 - r_max)) / total_w * r_max * life_max
        without_relevance = 100.0 * floor * life_max
        ceiling = min(100.0, max(with_relevance, without_relevance))
        bi.analytic_max = ceiling
        bi.analytic_note = (
            f"importance = base*gate*lifecycle; gate=clamp(rel,{floor:g},1) con "
            f"rel=match/100<={r_max:.3f}; base<=100*(W-w_rel*(1-R))/W={100.0 * (total_w - w_rel * (1.0 - r_max)) / total_w:.1f} "
            f"y lifecycle<={life_max:g} => rama con relevancia {with_relevance:.1f}, "
            f"rama sin relevancia (gate=piso) {without_relevance:.1f} => techo {ceiling:.1f}"
        )

    # — retail_media —
    rm = metrics.get("retail_media_score")
    if rm is not None:
        w = weights("retail_media", "weights")
        total_w = sum(w.values()) or 1.0
        r_max = (match.observed_max / 100.0) if (match and match.values) else 1.0
        b_max = (bi.analytic_max / 100.0) if (bi and bi.analytic_max is not None) else 1.0
        deficit = (float(w.get("competitive_relevance", 0.0)) * (1.0 - r_max)
                   + float(w.get("business_importance", 0.0)) * (1.0 - b_max))
        ceiling = 100.0 * (total_w - deficit) / total_w
        rm.analytic_max = ceiling
        rm.analytic_note = (
            f"score <= 100*(W - w_rel*(1-R) - w_bi*(1-B))/W con R={r_max:.3f}, "
            f"B={b_max:.3f} => techo {ceiling:.1f} (arrastra los techos de match y "
            f"business importance)"
        )


def collect(db_path: Path | str = DB_PATH) -> CalibrationData:
    """Calcula todas las muestras del harness. Una pasada por la base."""
    db_path = Path(db_path)
    metrics: dict[str, Metric] = {}
    notes: list[str] = []

    ctx = opportunities.build_context(db_path)

    # ── matches: persistidos y recalculados sin censura ────
    persisted = query(
        "SELECT nike_product_id, competitor_product_id, match_score, raw_match_score, coverage "
        "FROM competitive_matches", path=db_path)
    _add(metrics, "match_score", "Match score (ajustado, persistido)",
         [r["match_score"] for r in persisted], unit="0..100",
         source="competitive_matches.match_score", hard_min=0.0, hard_max=100.0)
    _add(metrics, "raw_match_score", "Match score crudo (explicación)",
         [r["raw_match_score"] for r in persisted], unit="0..100",
         source="competitive_matches.raw_match_score", hard_min=0.0, hard_max=100.0)
    _add(metrics, "match_coverage", "Cobertura del match (persistido)",
         [r["coverage"] for r in persisted], unit="0..1",
         source="competitive_matches.coverage", hard_min=0.0, hard_max=1.0)
    _add(metrics, "competitive_relevance", "Relevancia competitiva (gate)",
         [(r["match_score"] or 0.0) / 100.0 for r in persisted], unit="0..1",
         source="match_score/100", hard_min=0.0, hard_max=1.0)

    try:
        pairs = _pair_recompute(db_path)
    except Exception as exc:  # noqa: BLE001 - el harness nunca debe romper el pipeline
        pairs = []
        notes.append(f"no se pudo recalcular la grilla de pares: {type(exc).__name__}: {exc}")

    _add(metrics, "match_score_all_pairs", "Match score de TODOS los pares evaluados",
         [p["adjusted"] for p in pairs], unit="0..100",
         source="recálculo in-memory (sin censurar por min_score_to_persist)",
         hard_min=0.0, hard_max=100.0)
    _add(metrics, "match_coverage_all_pairs", "Cobertura de todos los pares",
         [p["coverage"] for p in pairs], unit="0..1", source="recálculo in-memory",
         hard_min=0.0, hard_max=1.0)

    # Sub-señales por factor (para juzgar los umbrales internos del matching).
    visual_evidence: list[float] = []
    comentions: list[float] = []
    availability_by_factor: dict[str, int] = {}
    visual_subsignals: set[str] = set()
    for pair in pairs:
        for row in pair["factors"]:
            if row["available"]:
                availability_by_factor[row["factor"]] = availability_by_factor.get(row["factor"], 0) + 1
            detail = row.get("detail") or {}
            if row["factor"] == "visual" and "evidence_weight" in detail:
                visual_evidence.append(float(detail["evidence_weight"]))
                visual_subsignals |= {k for k, v in (detail.get("sub_scores") or {}).items()
                                      if v is not None}
            if row["factor"] == "social" and detail.get("comentions"):
                comentions.append(float(detail["comentions"]))
    visual_metric = _add(
        metrics, "visual_evidence_weight", "Fracción del peso visual con datos",
        visual_evidence, unit="0..1", source="matching._score_visual detail.evidence_weight",
        hard_min=0.0, hard_max=1.0)
    if pairs:
        # Cota analítica: sólo las sub-señales que EXISTEN en la corrida pueden
        # sumar evidencia. Si el embedding (CLIP) no está disponible, el techo
        # baja a la suma de los pesos de silueta + colores + materiales.
        sub_weights = weights("competitive_match", "visual", "sub_weights")
        total = sum(sub_weights.values()) or 1.0
        reachable = sum(w for k, w in sub_weights.items() if k in visual_subsignals)
        visual_metric.analytic_max = reachable / total
        missing = sorted(set(sub_weights) - visual_subsignals)
        visual_metric.analytic_note = (
            f"evidencia = suma de sub-pesos con datos / {total:g}; sub-señales sin datos "
            f"en ningún par: {', '.join(missing) if missing else 'ninguna'} => techo "
            f"{reachable / total:.2f}"
        )
    _add(metrics, "pair_comentions", "Co-menciones sociales por par",
         comentions, unit="conteo", source="social_mention_aggregates", hard_min=0.0)
    _add(metrics, "product_review_volume", "Volumen de reviews por producto",
         [row["count"] for row in ctx.reviews.values()], unit="conteo",
         source="reviews", hard_min=0.0)

    msrp_gaps: list[float] = []
    for pair in pairs:
        for row in pair["factors"]:
            if row["factor"] == "price":
                gap = (row.get("detail") or {}).get("msrp_gap_pct")
                if gap is not None:
                    msrp_gaps.append(abs(float(gap)))
    _add(metrics, "abs_msrp_gap_pct", "|gap| de MSRP entre pares", msrp_gaps, unit="%",
         source="matching._score_price detail.msrp_gap_pct", hard_min=0.0)

    if pairs:
        # Piso analítico de la cobertura: los factores que tienen datos en TODOS
        # los pares no pueden faltar, así que su peso es cobertura garantizada.
        # De ahí sale, por ejemplo, que la confianza LOW sea inalcanzable.
        factor_weights = weights("competitive_match", "weights")
        total_w = sum(factor_weights.values()) or 1.0
        always = [f for f, count in availability_by_factor.items() if count == len(pairs)]
        floor_cov = sum(float(factor_weights.get(f, 0.0)) for f in always) / total_w
        note = (f"factores con datos en el 100% de los pares ({', '.join(sorted(always))}): "
                f"la cobertura nunca baja de {floor_cov:.2f}")
        for key in ("match_coverage", "match_coverage_all_pairs"):
            metric = metrics.get(key)
            if metric is not None:
                metric.analytic_min = floor_cov
                metric.analytic_note = note

    if pairs and availability_by_factor.get("visual", 0) == 0:
        notes.append(
            "el factor `visual` no tiene datos en NINGÚN par: aporta 0 a la cobertura "
            "y le baja el techo al match ajustado (ver competitive_match.visual.min_evidence_weight)"
        )

    # ── reglas de oportunidades (recalculadas con la config vigente) ──
    drafts: dict[str, list] = {}
    importance_by_rule: dict[str, list[float]] = {}
    importance_values: list[float] = []
    for rule_name in opportunities.OPPORTUNITY_TYPES:
        func = opportunities.RULES.get(rule_name)
        if func is None:
            drafts[rule_name] = []
            continue
        try:
            produced = func(ctx)
        except Exception as exc:  # noqa: BLE001
            produced = []
            notes.append(f"la regla {rule_name} lanzó {type(exc).__name__}: {exc}")
        drafts[rule_name] = produced
        scores = [scoring.business_importance(d.importance_inputs, ctx).score for d in produced]
        importance_by_rule[rule_name] = scores
        importance_values.extend(scores)

    if not importance_values:
        # Sin drafts (reglas rotas o base vacía) se cae a lo persistido.
        importance_values = [
            float(r["business_importance"])
            for r in query("SELECT business_importance FROM opportunities", path=db_path)
            if r["business_importance"] is not None
        ]
    _add(metrics, "business_importance", "Business Importance de las oportunidades",
         importance_values, unit="0..100", source="scoring.business_importance sobre las 12 reglas",
         hard_min=0.0, hard_max=100.0)

    persisted_counts: dict[str, int] = {
        r["opportunity_type"]: int(r["n"])
        for r in query("SELECT opportunity_type, COUNT(*) AS n FROM opportunities "
                       "GROUP BY opportunity_type", path=db_path)
    }

    # ── retail media ───────────────────────────────────────
    try:
        rm_scores = _retail_media_recompute(ctx)
    except Exception as exc:  # noqa: BLE001
        rm_scores = []
        notes.append(f"no se pudo recalcular retail media: {type(exc).__name__}: {exc}")
    _add(metrics, "retail_media_score", "Retail Media score (todos los tripletes)",
         rm_scores, unit="0..100", source="recálculo in-memory (sin censurar por min_score_to_report)",
         hard_min=0.0, hard_max=100.0)

    # ── señales de negocio que alimentan las reglas ────────
    _collect_business_metrics(ctx, metrics, db_path)

    _add(metrics, "insight_signal_volume", "Volumen de señal de los brand insights",
         [r["signal_volume"] for r in
          query("SELECT signal_volume FROM brand_insights", path=db_path)
          if r["signal_volume"] is not None],
         unit="conteo", source="brand_insights.signal_volume", hard_min=0.0)

    _analytic_bounds(metrics)

    return CalibrationData(
        db_path=db_path, metrics=metrics, ctx=ctx, drafts=drafts,
        persisted_opportunities=persisted_counts, importance_by_rule=importance_by_rule,
        notes=notes,
    )


def _collect_business_metrics(ctx: Any, metrics: dict[str, Metric],
                              db_path: Path | str) -> None:
    """Distribuciones de las señales que filtran las 12 reglas."""
    pair_gaps: list[float] = []
    retailer_gaps: list[float] = []
    nike_cheaper: list[float] = []
    coverage_gaps: list[float] = []
    comp_availability: list[float] = []
    min_cheaper = float(section("opportunities", "price_competitiveness_risk",
                                "min_competitor_cheaper_pct", default=0.0) or 0.0)
    cheaper_counts: list[float] = []

    for nike_id in ctx.nike_ids:
        for match in ctx.matched(nike_id):
            comp_id = int(match["competitor_product_id"])
            comparison = opportunities.price_comparison(ctx, nike_id, comp_id)
            gap = comparison["gap_pct"]
            if gap is not None:
                pair_gaps.append(gap)
                nike_cheaper.append(-gap)
            for row in comparison["per_retailer"]:
                retailer_gaps.append(row["gap_pct"])
            coverage_gaps.append(float(len(ctx.retailers_of(comp_id) - ctx.retailers_of(nike_id))))
            for rid in sorted(ctx.retailers_of(nike_id) & ctx.retailers_of(comp_id)):
                value = ctx.availability_at(comp_id, rid)
                if value is not None:
                    comp_availability.append(value)
        best = ctx.best_match(nike_id)
        if best is not None:
            comparison = opportunities.price_comparison(ctx, nike_id, int(best["competitor_product_id"]))
            cheaper_counts.append(float(sum(1 for r in comparison["per_retailer"]
                                            if r["gap_pct"] >= min_cheaper)))

    _add(metrics, "price_gap_pct", "Gap de precio Nike vs competidor (par)", pair_gaps,
         unit="% (>0 = competidor más barato)", source="opportunities.price_comparison")
    _add(metrics, "price_gap_pct_retailer", "Gap de precio por retailer", retailer_gaps,
         unit="% (>0 = competidor más barato)", source="opportunities.price_comparison")
    _add(metrics, "nike_cheaper_pct", "Cuánto más barato está Nike", nike_cheaper,
         unit="% (>0 = Nike más barato)", source="-price_gap_pct")
    _add(metrics, "cheaper_retailers_count", "Retailers con el competidor más barato",
         cheaper_counts, unit="conteo",
         source=f"por producto Nike, con min_competitor_cheaper_pct={min_cheaper:g}", hard_min=0.0)
    _add(metrics, "retailer_coverage_gap", "Retailers del competidor donde Nike no está",
         coverage_gaps, unit="conteo", source="opportunities.retailers_of", hard_min=0.0)
    _add(metrics, "competitor_availability_pct", "Disponibilidad del competidor por retailer",
         comp_availability, unit="%", source="stock_observations", hard_min=0.0, hard_max=100.0)

    nike_availability: list[float] = []
    for nike_id in ctx.nike_ids:
        for rid in sorted(ctx.retailers_of(nike_id)):
            value = ctx.availability_at(nike_id, rid)
            if value is not None:
                nike_availability.append(value)
    _add(metrics, "nike_availability_pct", "Disponibilidad Nike por retailer", nike_availability,
         unit="%", source="stock_observations", hard_min=0.0, hard_max=100.0)

    _add(metrics, "nike_discount_pct", "Descuento promedio de cada producto Nike",
         [ctx.avg_discount(pid) for pid in ctx.nike_ids], unit="%",
         source="price_observations.discount_pct", hard_min=0.0, hard_max=100.0)
    _add(metrics, "competitor_discount_pct", "Descuento promedio de cada competidor",
         [ctx.avg_discount(pid) for pid in ctx.competitor_ids], unit="%",
         source="price_observations.discount_pct", hard_min=0.0, hard_max=100.0)

    # over_discounting: pp de más que descuenta Nike contra su pool comparable.
    discount_gaps: list[float] = []
    markdown_counts: list[float] = []
    min_markdown = float(section("opportunities", "promotional_pressure",
                                 "min_avg_discount_pct", default=0.0) or 0.0)
    for nike_id in ctx.nike_ids:
        nike_disc = ctx.avg_discount(nike_id)
        pool = opportunities._competitor_pool(ctx, nike_id)
        pool_discounts = [d for d in (ctx.avg_discount(c) for c in pool) if d is not None]
        if nike_disc is not None and pool_discounts:
            discount_gaps.append(nike_disc - sum(pool_discounts) / len(pool_discounts))
        markdown_counts.append(float(sum(1 for d in pool_discounts if d >= min_markdown)))
    _add(metrics, "discount_gap_pp", "pp de descuento Nike sobre sus comparables",
         discount_gaps, unit="pp", source="opportunities.avg_discount")
    _add(metrics, "competitors_on_markdown", "Competidores comparables en markdown",
         markdown_counts, unit="conteo",
         source=f"por producto Nike, con min_avg_discount_pct={min_markdown:g}", hard_min=0.0)

    # Segmentos.
    sku_ratios: list[float] = []
    comp_skus: list[float] = []
    nike_shares: list[float] = []
    demand: dict[str, float] = {}
    for segment in ctx.segments():
        nike_skus = ctx.products_in_segment(segment, nike=True)
        competitors = ctx.products_in_segment(segment, nike=False)
        comp_skus.append(float(len(competitors)))
        sku_ratios.append(len(competitors) / max(len(nike_skus), 1))
        total = len(nike_skus) + len(competitors)
        if total:
            nike_shares.append(len(nike_skus) / total)
        volume = 0.0
        for pid in ctx.products:
            if ctx.segment_of(pid) == segment:
                volume += ctx.social_volume(pid) + (ctx.review_count(pid) or 0.0)
        demand[segment] = volume
    top = max(demand.values(), default=0.0)
    _add(metrics, "segment_sku_ratio", "Ratio SKUs competidores / Nike por segmento",
         sku_ratios, unit="x", source="products_in_segment", hard_min=0.0)
    _add(metrics, "segment_competitor_skus", "SKUs de competidores por segmento", comp_skus,
         unit="conteo", source="products_in_segment", hard_min=0.0)
    _add(metrics, "segment_nike_share", "Share de SKUs Nike por segmento", nike_shares,
         unit="0..1", source="products_in_segment", hard_min=0.0, hard_max=1.0)
    _add(metrics, "segment_demand_signal", "Señal de demanda relativa por segmento",
         [(v / top) if top > 0 else 0.0 for v in demand.values()], unit="0..1",
         source="social + reviews normalizados al máximo", hard_min=0.0, hard_max=1.0)

    # Momentum y aceleración de competidores.
    momentum_values: list[float] = []
    accelerations: list[float] = []
    for comp_id in ctx.competitor_ids:
        info = ctx.momentum(comp_id)
        if info.get("value") is not None:
            momentum_values.append(float(info["value"]))
        if info.get("acceleration") is not None:
            accelerations.append(float(info["acceleration"]))
    _add(metrics, "competitor_momentum", "Momentum del competidor", momentum_values,
         unit="0..1", source="market_signals / social_mention_aggregates",
         hard_min=0.0, hard_max=1.0)
    _add(metrics, "competitor_acceleration", "Aceleración del competidor", accelerations,
         unit="ratio", source="market_signals.acceleration | delta social")

    # Share of shelf: sólo las caídas (la regla mira drops).
    drops = [-float(s["delta"]) for s in ctx.signals
             if s.get("signal_type") == "share_of_shelf" and s.get("delta") is not None]
    _add(metrics, "shelf_drop_pp", "Caída de share of shelf", drops, unit="pp",
         source="market_signals(share_of_shelf).delta")

    # Lanzamientos de competidores.
    days: list[float] = []
    coverage: list[float] = []
    for comp_id in ctx.competitor_ids:
        launch = parse_date(ctx.product(comp_id).get("launch_date"))
        if launch is None:
            continue
        elapsed = (ctx.today - launch).days
        if elapsed < 0:
            continue
        days.append(float(elapsed))
        coverage.append(float(len(ctx.retailers_of(comp_id))))
    _add(metrics, "days_since_launch", "Días desde el lanzamiento del competidor", days,
         unit="días", source="products.launch_date", hard_min=0.0)
    _add(metrics, "competitor_retailer_coverage", "Retailers donde está el competidor",
         coverage, unit="conteo", source="price/stock observations", hard_min=0.0)


# ════════════════════════════════════════════════════════════
#  1. Distribuciones
# ════════════════════════════════════════════════════════════


def score_distributions(db_path: Path | str = DB_PATH,
                        data: CalibrationData | None = None) -> dict[str, Any]:
    """Percentiles p5..p95, min, max y n de cada métrica del motor."""
    data = data or collect(db_path)
    return {key: metric.describe() for key, metric in sorted(data.metrics.items())}


# ════════════════════════════════════════════════════════════
#  2. Alcanzabilidad
# ════════════════════════════════════════════════════════════


def _config_value(keys: Sequence[str]) -> Any:
    return section(*keys, default=None)


def _classify(spec: ThresholdSpec, value: float, metric: Metric) -> dict[str, Any]:
    """UNREACHABLE / TRIVIAL / OK / NO_DATA para un umbral contra su métrica."""
    n = metric.n
    ceiling = metric.ceiling()
    floor = metric.floor()
    analytic = metric.analytic_max if spec.direction == "min" else metric.analytic_min

    if n == 0:
        return {
            "status": STATUS_NO_DATA,
            "basis": "sin muestra",
            "n_pass": 0,
            "pct_pass": None,
            "reason": (f"no hay ni un registro de `{metric.key}` en la base: no se puede "
                       f"juzgar el umbral (¿falta la señal de entrada?)"),
        }

    n_pass = metric.pass_count(value, spec.direction)
    pct = n_pass / n

    if spec.direction == "min":
        analytically_dead = analytic is not None and value > analytic
        if n_pass == 0:
            basis = "analítica" if analytically_dead else "empírica"
            limit = analytic if analytically_dead else metric.observed_max
            reason = (f"ningún registro llega: el umbral pide >= {value:g} y el techo "
                      f"{'analítico' if analytically_dead else 'observado'} de "
                      f"`{metric.key}` es {limit:.2f}")
            if metric.hard_max is not None and value > metric.hard_max:
                basis = "analítica"
                reason += (f" — y el umbral está FUERA de la escala de la métrica "
                           f"(máximo posible {metric.hard_max:g}): revisar unidades "
                           f"(0..1 vs 0..100)")
            return {"status": STATUS_UNREACHABLE, "basis": basis, "n_pass": 0,
                    "pct_pass": 0.0, "reason": reason}
        if n_pass == n:
            structural = metric.analytic_min is not None and value <= metric.analytic_min
            reason = (f"lo supera el 100% de los registros (mínimo observado "
                      f"{metric.observed_min:.2f} >= {value:g}): el umbral no filtra nada")
            if structural:
                reason += (f" — y no es casualidad del dataset: el piso analítico de "
                           f"`{metric.key}` es {metric.analytic_min:.2f} ({metric.analytic_note})")
            return {"status": STATUS_TRIVIAL, "basis": "analítica" if structural else "empírica",
                    "n_pass": n_pass, "pct_pass": 1.0, "reason": reason}
    else:
        analytically_dead = analytic is not None and value < analytic
        if n_pass == 0:
            basis = "analítica" if analytically_dead else "empírica"
            limit = analytic if analytically_dead else metric.observed_min
            reason = (f"ningún registro entra: el umbral pide <= {value:g} y el piso "
                      f"{'analítico' if analytically_dead else 'observado'} de "
                      f"`{metric.key}` es {limit:.2f}")
            if metric.hard_min is not None and value < metric.hard_min:
                basis = "analítica"
                reason += (f" — y el umbral está FUERA de la escala de la métrica "
                           f"(mínimo posible {metric.hard_min:g}): revisar unidades "
                           f"(0..1 vs 0..100)")
            return {"status": STATUS_UNREACHABLE, "basis": basis, "n_pass": 0,
                    "pct_pass": 0.0, "reason": reason}
        if n_pass == n:
            structural = metric.analytic_max is not None and value >= metric.analytic_max
            reason = (f"lo cumple el 100% de los registros (máximo observado "
                      f"{metric.observed_max:.2f} <= {value:g}): el umbral no filtra nada")
            if structural:
                reason += (f" — y no es casualidad del dataset: el techo analítico de "
                           f"`{metric.key}` es {metric.analytic_max:.2f}")
            return {"status": STATUS_TRIVIAL, "basis": "analítica" if structural else "empírica",
                    "n_pass": n_pass, "pct_pass": 1.0, "reason": reason}

    reason = (f"{n_pass}/{n} registros ({pct:.0%}) del lado activo; rango observado "
              f"[{metric.observed_min:.2f}, {metric.observed_max:.2f}]")
    if pct <= 0.05:
        reason += " — al borde: un solo dato lo apaga"
    return {"status": STATUS_OK, "basis": "empírica", "n_pass": n_pass,
            "pct_pass": pct, "reason": reason}


def reachability_report(db_path: Path | str = DB_PATH,
                        data: CalibrationData | None = None) -> list[dict[str, Any]]:
    """Cada umbral de weights.yaml contra el rango alcanzable de su métrica.

    Devuelve una fila por umbral con ``status`` en
    ``UNREACHABLE`` (nada puede superarlo) / ``TRIVIAL`` (lo supera todo) /
    ``OK`` / ``NO_DATA`` (la métrica no tiene ni una observación), y el motivo
    en castellano. ``basis`` dice si el veredicto se apoya en la cota analítica
    (sobrevive a un cambio de dataset) o sólo en lo observado.
    """
    data = data or collect(db_path)
    rows: list[dict[str, Any]] = []

    for spec in THRESHOLDS:
        value = _config_value(spec.keys)
        if value is None:
            rows.append({
                "path": spec.path, "value": None, "metric": spec.metric,
                "direction": spec.direction, "rule": spec.rule, "what": spec.what,
                "status": STATUS_NO_DATA, "basis": "sin config", "n": 0, "n_pass": 0,
                "pct_pass": None, "kind": spec.kind, "defect": True,
                "reason": "el umbral no existe en weights.yaml",
            })
            continue
        metric = data.metric(spec.metric)
        verdict = _classify(spec, float(value), metric)
        if verdict["status"] == STATUS_TRIVIAL and spec.kind == KIND_GATE:
            verdict["reason"] += (
                " — en un gate de evidencia eso no es un bug: el corpus supera el mínimo. "
                "Protege contra datasets pobres; subirlo apagaría señal real"
            )
        elif verdict["status"] == STATUS_TRIVIAL and spec.kind == KIND_SCALE:
            verdict["reason"] += " — es un parámetro de forma, no un filtro: hoy queda inerte"
        rows.append({
            "path": spec.path,
            "value": float(value),
            "metric": spec.metric,
            "metric_label": metric.label,
            "direction": spec.direction,
            "rule": spec.rule,
            "what": spec.what,
            "kind": spec.kind,
            "defect": spec.is_defect(verdict["status"]),
            "n": metric.n,
            "observed_min": _round(metric.observed_min, 3),
            "observed_max": _round(metric.observed_max, 3),
            "analytic_max": _round(metric.analytic_max, 3),
            "analytic_note": metric.analytic_note,
            "headroom": _round(
                (metric.ceiling() - float(value)) if (spec.direction == "min" and metric.ceiling() is not None)
                else ((float(value) - metric.floor()) if (spec.direction == "max" and metric.floor() is not None)
                      else None), 3),
            **verdict,
        })

    order = {STATUS_UNREACHABLE: 0, STATUS_NO_DATA: 1, STATUS_TRIVIAL: 2, STATUS_OK: 3}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["path"]))
    return rows


# ════════════════════════════════════════════════════════════
#  3. Umbrales sugeridos
# ════════════════════════════════════════════════════════════


def _label_with(cuts: dict[str, float], bottom: str, value: float) -> str:
    """Etiqueta ordinal de un valor dados los cortes (de mayor a menor)."""
    for band, cut in sorted(cuts.items(), key=lambda kv: kv[1], reverse=True):
        if value >= cut:
            return band
    return bottom


def _band_cuts(metric: Metric, targets: dict[str, float]) -> dict[str, float]:
    """Cortes por percentil que dejan ``target`` de la masa por encima.

    Se fuerza monotonía estricta: si dos bandas colapsan en el mismo valor
    (distribuciones con empates, típico de escalas discretas) la banda inferior
    baja al siguiente valor observado.
    """
    cuts: dict[str, float] = {}
    unique = sorted(set(metric.values))
    previous: float | None = None
    for band, share in sorted(targets.items(), key=lambda kv: kv[1]):
        cut = metric.percentile((1.0 - clamp(share, 0.0, 1.0)) * 100.0)
        cut = float(np.floor(float(cut) * 10.0) / 10.0)   # 1 decimal, sin excluir el borde
        if previous is not None and cut >= previous:
            lower = [v for v in unique if v < previous]
            cut = float(np.floor(max(lower) * 10.0) / 10.0) if lower else previous
        cuts[band] = cut
        previous = cut
    return cuts


def _band_suggestions(data: CalibrationData) -> dict[str, dict[str, Any]]:
    """Cortes de etiquetas ordinales (severidad, confianza) en percentiles.

    Sólo propone cambios cuando la etiqueta HOY no discrimina: alguna banda sin
    un solo registro. Es exactamente el caso 78/60/40 (CRITICAL y HIGH vacías).
    """
    out: dict[str, dict[str, Any]] = {}
    for group in BAND_GROUPS:
        metric = data.metric(group.metric)
        if metric.n == 0:
            continue
        current = {band: _config_value(tuple(path.split(".")))
                   for band, path in group.paths.items()}
        if any(v is None for v in current.values()):
            continue
        current_cuts = {band: float(v) for band, v in current.items()}
        before = _counter([_label_with(current_cuts, group.bottom_label, v)
                           for v in metric.values])
        empty = [band for band in list(group.paths) + [group.bottom_label]
                 if before.get(band, 0) == 0]
        if not empty:
            continue

        cuts = _band_cuts(metric, group.targets)
        after = _counter([_label_with(cuts, group.bottom_label, v) for v in metric.values])
        labels = list(group.paths) + [group.bottom_label]
        still_empty = [band for band in labels if after.get(band, 0) == 0]
        if len(still_empty) >= len(empty):
            # Ningún corte mejora el reparto: la banda vacía es ESTRUCTURAL
            # (p.ej. LOW de confianza, con un piso analítico de cobertura de
            # 0.60 no hay match que pueda caer ahí). Proponer números nuevos
            # sería ruido; queda documentado en reachability_report.
            continue
        changed = sum(1 for v in metric.values
                      if _label_with(current_cuts, group.bottom_label, v)
                      != _label_with(cuts, group.bottom_label, v))
        for band, path in group.paths.items():
            n_above = sum(1 for v in metric.values if v >= cuts[band])
            out[path] = {
                "actual": current_cuts[band],
                "sugerido": cuts[band],
                "motivo": (
                    f"banda {band} de {group.name}: hoy queda(n) vacía(s) "
                    f"{', '.join(empty)} sobre n={metric.n}, así que la etiqueta no "
                    f"discrimina. Corte en el percentil que deja "
                    f"{group.targets[band]:.0%} por encima ({n_above} registros) sobre "
                    f"`{group.metric}` en [{metric.observed_min:.2f}, {metric.observed_max:.2f}]"
                    + (f", techo analítico {metric.analytic_max:.2f}"
                       if metric.analytic_max is not None else "")
                ),
                "n_afectados": _flip_count(metric.values, current_cuts[band], cuts[band], "min"),
                "n_reclasificados_total": changed,
                "distribucion_actual": before,
                "distribucion_sugerida": after,
            }
    return out


def _counter(values: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _flip_count(values: Sequence[float], current: Any, suggested: float, direction: str) -> int:
    """Registros que cambian de lado entre el umbral actual y el sugerido."""
    if current is None:
        return 0
    current = float(current)
    if direction == "min":
        return sum(1 for v in values if (v >= current) != (v >= suggested))
    return sum(1 for v in values if (v <= current) != (v <= suggested))


def suggest_thresholds(db_path: Path | str = DB_PATH,
                       data: CalibrationData | None = None) -> dict[str, dict[str, Any]]:
    """Propone cortes fundados en la distribución observada.

    NO escribe ``weights.yaml``: la decisión es humana. Para el snippet listo
    para copiar y pegar, ver :func:`suggested_yaml`.

    Devuelve ``{ruta_en_yaml: {actual, sugerido, motivo, n_afectados}}``.
    """
    data = data or collect(db_path)
    suggestions: dict[str, dict[str, Any]] = {}
    suggestions.update(_band_suggestions(data))

    by_path = {row["path"]: row for row in reachability_report(db_path, data)}
    for spec in THRESHOLDS:
        row = by_path.get(spec.path)
        if row is None or spec.path in suggestions or not row["defect"]:
            continue
        if spec.kind == KIND_BAND:
            # Los cortes de banda se proponen sólo por grupo (_band_suggestions):
            # moverlos de a uno rompe la monotonía de la etiqueta.
            continue
        metric = data.metric(spec.metric)
        if metric.n == 0:
            continue

        if spec.kind == KIND_GATE:
            # Un gate inalcanzable no se arregla bajando el número: significa que
            # a la señal le falta una sub-fuente. Se propone el máximo alcanzable
            # y se dice explícitamente que la decisión es de DATOS, no de umbral.
            suggested = _round_like(metric.observed_max, row["value"], metric, spec.direction)
            suggestions[spec.path] = {
                "actual": row["value"],
                "sugerido": suggested,
                "motivo": (
                    f"UNREACHABLE: {row['reason']}. El factor queda apagado para el 100% "
                    f"de los registros, así que su peso no se usa y baja la cobertura. "
                    f"Arreglarlo bajando el gate a {suggested:g} admite evidencia más "
                    f"pobre; la alternativa —preferible— es alimentar la sub-señal que "
                    f"falta. DECISIÓN DE DATOS, no de umbral"
                ),
                "n_afectados": _flip_count(metric.values, row["value"], suggested, spec.direction),
                "accion": "revisar la señal de entrada antes que el umbral",
            }
            continue

        # Percentil que deja pasar aproximadamente `target_pass` de los registros.
        pct = clamp(spec.target_pass, 0.01, 0.99)
        percentile = (1.0 - pct) * 100.0 if spec.direction == "min" else pct * 100.0
        suggested = _round_like(metric.percentile(percentile), row["value"], metric,
                                spec.direction)
        n_pass = metric.pass_count(suggested, spec.direction)
        suggestions[spec.path] = {
            "actual": row["value"],
            "sugerido": suggested,
            "motivo": (
                f"{row['status']}: {row['reason']}. p{percentile:.0f} de `{spec.metric}` "
                f"(n={metric.n}) deja {n_pass} registros activos "
                f"(~{spec.target_pass:.0%} objetivo)"
            ),
            "n_afectados": _flip_count(metric.values, row["value"], suggested, spec.direction),
        }
    return suggestions


def _round_like(suggested: float | None, current: Any, metric: Metric,
                direction: str) -> float:
    """Redondea la sugerencia a la misma granularidad que el valor actual.

    Un umbral que cuenta cosas (retailers, SKUs, días) tiene que quedar entero:
    ``min_competitors_on_markdown: 5.5`` no significa nada. Se redondea en la
    dirección conservadora (hacia el lado que NO agranda el conjunto que pasa).
    """
    if suggested is None:
        return float(current)
    value = float(suggested)
    discrete = metric.unit in ("conteo", "días") or (
        current is not None and float(current) == int(float(current))
        and all(float(v) == int(float(v)) for v in metric.values[:50])
    )
    if discrete:
        return float(int(np.ceil(value)) if direction == "min" else int(np.floor(value)))
    return round(value, 2)


def suggested_yaml(db_path: Path | str = DB_PATH,
                   suggestions: dict[str, dict[str, Any]] | None = None,
                   data: CalibrationData | None = None) -> str:
    """Snippet YAML con los cambios propuestos, listo para copiar y pegar."""
    if suggestions is None:
        suggestions = suggest_thresholds(db_path, data)
    if not suggestions:
        return ("# sin cambios sugeridos: todos los umbrales caen dentro del rango "
                "alcanzable (o no hay muestra para proponer nada)\n")

    tree: dict[str, Any] = {}
    for path, info in sorted(suggestions.items()):
        node = tree
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = info

    lines = [
        "# ── Sugerencias de calibración (app.calibration) ─────────────",
        "# Generado desde la distribución observada. NO se aplicó nada:",
        "# revisar, decidir y pegar en backend/config/weights.yaml.",
    ]

    def walk(node: dict[str, Any], depth: int) -> None:
        pad = "  " * depth
        for key, value in node.items():
            if isinstance(value, dict) and "sugerido" in value:
                actual = value["actual"]
                actual_txt = "s/d" if actual is None else f"{actual:g}"
                lines.append(f"{pad}{key}: {value['sugerido']:g}   # actual {actual_txt} — "
                             f"{value['motivo']}")
            else:
                lines.append(f"{pad}{key}:")
                walk(value, depth + 1)

    walk(tree, 0)
    return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════
#  4. Rendimiento por regla
# ════════════════════════════════════════════════════════════

YIELD_OK = "PRODUCE"
YIELD_BROKEN = "ROTA"
YIELD_NO_SIGNAL = "SIN_SEÑAL"
YIELD_NOTHING = "NADA_QUE_REPORTAR"


def rule_yield_report(db_path: Path | str = DB_PATH,
                      data: CalibrationData | None = None) -> list[dict[str, Any]]:
    """Cuántas oportunidades produce cada regla y, si produce 0, por qué.

    La distinción que importa: **la regla está rota** (un umbral inalcanzable la
    apaga por construcción, o le falta la señal de entrada) vs **no hay nada que
    reportar** (los datos existen, los umbrales son alcanzables, pero hoy ningún
    registro califica). Lo primero es un bug de calibración; lo segundo es una
    respuesta legítima del motor.
    """
    data = data or collect(db_path)
    families = section("opportunities", "families", default={}) or {}
    reach = {row["path"]: row for row in reachability_report(db_path, data)}

    rows: list[dict[str, Any]] = []
    for rule in opportunities.OPPORTUNITY_TYPES:
        drafts = data.drafts.get(rule, [])
        count = len(drafts)
        specs = [s for s in THRESHOLDS if s.rule == rule]
        thresholds = [reach[s.path] for s in specs if s.path in reach]

        unreachable = [t for t in thresholds if t["status"] == STATUS_UNREACHABLE]
        missing_input = [key for key in RULE_INPUTS.get(rule, ())
                         if data.metric(key).n == 0]
        trivial = [t for t in thresholds if t["status"] == STATUS_TRIVIAL]

        importances = data.importance_by_rule.get(rule, [])
        row: dict[str, Any] = {
            "rule": rule,
            "family": families.get(rule),
            "n": count,
            "n_persisted": data.persisted_opportunities.get(rule, 0),
            "thresholds": {t["path"].split(".")[-1]: t["value"] for t in thresholds},
            "blocking": [t["path"] for t in unreachable],
            "missing_inputs": missing_input,
            "importance_max": _round(max(importances), 2) if importances else None,
            "importance_mean": _round(sum(importances) / len(importances), 2) if importances else None,
        }

        if count > 0:
            row["status"] = YIELD_OK
            note = f"{count} oportunidad(es)"
            if unreachable:
                note += (f"; ATENCIÓN: {len(unreachable)} umbral(es) inalcanzable(s) "
                         f"({', '.join(t['path'].split('.')[-1] for t in unreachable)}) "
                         f"— la regla dispara por otro camino")
            elif len(trivial) == len(thresholds) and thresholds:
                note += "; todos sus umbrales son TRIVIALES: dispara para todo el universo"
            row["diagnosis"] = note
        elif unreachable:
            row["status"] = YIELD_BROKEN
            row["diagnosis"] = ("umbral inalcanzable — " + "; ".join(
                f"{t['path']}={t['value']:g}: {t['reason']}" for t in unreachable))
        elif missing_input:
            row["status"] = YIELD_NO_SIGNAL
            row["diagnosis"] = ("falta la señal de entrada: sin datos en " +
                                ", ".join(f"`{k}`" for k in missing_input))
        else:
            near = [t for t in thresholds
                    if t["status"] == STATUS_OK and (t.get("pct_pass") or 0) <= 0.05]
            row["status"] = YIELD_NOTHING
            row["diagnosis"] = (
                "los datos existen y los umbrales son alcanzables, pero hoy ningún caso "
                "califica" + (f" (al borde: {', '.join(t['path'].split('.')[-1] for t in near)})"
                              if near else "")
            )
        rows.append(row)
    return rows


# ════════════════════════════════════════════════════════════
#  5. Sensibilidad
# ════════════════════════════════════════════════════════════


def _set_path(node: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _deep_copy(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _deep_copy(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_copy(v) for v in node]
    return node


@contextmanager
def temporary_param(path: str, value: Any) -> Iterator[dict[str, Any]]:
    """Parchea un parámetro de config EN MEMORIA y lo restaura al salir.

    Se restaura desde un snapshot del dict cacheado (y no con
    ``reload_config()``) porque eso preserva cualquier override que el llamador
    ya hubiera aplicado —un test corriendo con una config temporal, por
    ejemplo—. ``reload_config()` queda como escape hatch manual para volver a
    lo que hay en disco.
    """
    cfg = get_config()
    snapshot = _deep_copy(cfg)
    try:
        _set_path(cfg, path, value)
        yield cfg
    finally:
        cfg.clear()
        cfg.update(snapshot)


def restore_config_from_disk() -> dict[str, Any]:
    """Descarta CUALQUIER override en memoria y relee ``weights.yaml``.

    ``temporary_param`` alcanza para restaurar un barrido, pero después de una
    corrida larga (o de un parcheo manual en una sesión interactiva) esto
    garantiza que lo que hay en memoria es exactamente lo que hay en disco.
    """
    return reload_config()


@contextmanager
def _db_copy(db_path: Path | str) -> Iterator[Path]:
    """Copia descartable de la base: el barrido nunca toca la base real."""
    tmpdir = Path(tempfile.mkdtemp(prefix="calibration-"))
    try:
        target = tmpdir / "sensitivity.db"
        shutil.copy2(Path(db_path), target)
        yield target
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _rankdata(values: Sequence[float]) -> np.ndarray:
    """Rangos con empates promediados (equivalente a scipy.stats.rankdata)."""
    arr = np.asarray(values, dtype=float)
    order = arr.argsort()
    ranks = np.empty(len(arr), dtype=float)
    ranks[order] = np.arange(1, len(arr) + 1, dtype=float)
    # Promedio de rangos entre empates.
    for value in np.unique(arr):
        mask = arr == value
        if mask.sum() > 1:
            ranks[mask] = ranks[mask].mean()
    return ranks


def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None
    ra, rb = _rankdata(a), _rankdata(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _sweep_state(db_path: Path | str) -> dict[str, Any]:
    """Fotografía del motor tras recalcular con una config dada."""
    matches = query(
        "SELECT nike_product_id, competitor_product_id, match_score FROM competitive_matches",
        path=db_path)
    scores = {(int(r["nike_product_id"]), int(r["competitor_product_id"])): float(r["match_score"])
              for r in matches}
    opps = query("SELECT opportunity_type, severity, business_importance FROM opportunities",
                 path=db_path)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    values = [v for v in scores.values()]
    importance = [float(o["business_importance"]) for o in opps
                  if o["business_importance"] is not None]
    return {
        "n_matches": len(scores),
        "match_score_p50": _round(float(np.median(values)), 2) if values else None,
        "match_score_max": _round(max(values), 2) if values else None,
        "n_opportunities": len(opps),
        "severity": _counter([str(o["severity"]) for o in opps]),
        "rules": _counter([str(o["opportunity_type"]) for o in opps]),
        "importance_p50": _round(float(np.median(importance)), 2) if importance else None,
        "top_pairs": [list(pair) for pair, _ in ranked[:10]],
        "_scores": scores,
    }


def sensitivity(param_path: str, values: list[float],
                db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Barre un parámetro y muestra cómo cambian conteos y rankings.

    Ejemplo: ``sensitivity("competitive_match.evidence_shrinkage.prior", [0.2, 0.35, 0.5])``.

    Cada valor se evalúa recalculando matching + oportunidades + retail media
    sobre una COPIA de la base (la base real nunca se toca) y la config original
    se restaura SIEMPRE, incluso si una corrida falla.
    """
    results: list[dict[str, Any]] = []
    reference: dict[str, Any] | None = None
    current_value = _config_value(tuple(param_path.split(".")))

    for value in values:
        row: dict[str, Any] = {"param": param_path, "value": value,
                               "is_current": current_value is not None
                               and float(current_value) == float(value)}
        try:
            with temporary_param(param_path, value):
                with _db_copy(db_path) as tmp_db:
                    matching.run_matching(tmp_db)
                    opportunities.run_opportunities(tmp_db)
                    try:
                        retail_media.run_retail_media(tmp_db)
                    except Exception:  # noqa: BLE001 - etapa opcional para el barrido
                        pass
                    state = _sweep_state(tmp_db)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            results.append(row)
            continue

        scores = state.pop("_scores")
        row.update(state)
        if reference is None:
            reference = {"scores": scores, "top": state["top_pairs"]}
            row["spearman_vs_first"] = 1.0
            row["top10_overlap_vs_first"] = 1.0
        else:
            shared = sorted(set(scores) & set(reference["scores"]))
            row["spearman_vs_first"] = _round(
                _spearman([scores[k] for k in shared],
                          [reference["scores"][k] for k in shared]), 4)
            top_now = {tuple(p) for p in state["top_pairs"]}
            top_ref = {tuple(p) for p in reference["top"]}
            union = top_now | top_ref
            row["top10_overlap_vs_first"] = _round(len(top_now & top_ref) / len(union), 4) if union else None
            row["pairs_shared_vs_first"] = len(shared)
        results.append(row)

    return results


# ════════════════════════════════════════════════════════════
#  6. Reporte completo
# ════════════════════════════════════════════════════════════


def report(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    """Todo el harness en un solo dict (lo que imprime el CLI)."""
    data = collect(db_path)
    reach = reachability_report(db_path, data)
    suggestions = suggest_thresholds(db_path, data)
    yields = rule_yield_report(db_path, data)

    defects = [r for r in reach if r["defect"]]
    trivial = [r for r in reach if r["status"] == STATUS_TRIVIAL]
    broken_rules = [r for r in yields if r["status"] in (YIELD_BROKEN, YIELD_NO_SIGNAL)]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(db_path),
        "config_version": section("version", default="s/d"),
        "distributions": score_distributions(db_path, data),
        "reachability": reach,
        "rule_yield": yields,
        "suggestions": suggestions,
        "suggested_yaml": suggested_yaml(db_path, suggestions, data),
        "notes": data.notes,
        "summary": {
            "thresholds_checked": len(reach),
            "unreachable": sum(1 for r in reach if r["status"] == STATUS_UNREACHABLE),
            "trivial": len(trivial),
            "trivial_defects": sum(1 for r in trivial if r["defect"]),
            "defects": len(defects),
            "no_data": sum(1 for r in reach if r["status"] == STATUS_NO_DATA),
            "ok": sum(1 for r in reach if r["status"] == STATUS_OK),
            "rules_producing": sum(1 for r in yields if r["n"] > 0),
            "rules_broken": len(broken_rules),
            "opportunities": sum(r["n"] for r in yields),
            "problem_paths": [r["path"] for r in defects],
        },
    }


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]],
           aligns: Sequence[str] | None = None) -> str:
    cells = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    aligns = aligns or ["<"] * len(headers)

    def line(values: Sequence[str]) -> str:
        return "  ".join(f"{v:{aligns[i]}{widths[i]}}" for i, v in enumerate(values))

    out = [line(list(headers)), "  ".join("─" * w for w in widths)]
    out.extend(line(row) for row in cells)
    return "\n".join(out)


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


_ICON = {STATUS_UNREACHABLE: "✗", STATUS_NO_DATA: "?", STATUS_TRIVIAL: "!", STATUS_OK: "✓",
         YIELD_BROKEN: "✗", YIELD_NO_SIGNAL: "?", YIELD_NOTHING: "·", YIELD_OK: "✓"}


def render(rep: dict[str, Any]) -> str:
    """Reporte legible en consola."""
    out: list[str] = []
    add = out.append

    add("═" * 100)
    add("  HARNESS DE CALIBRACIÓN — Competitive & Consumer Intelligence")
    add(f"  base: {rep['db']}   ·   config v{rep['config_version']}   ·   {rep['generated_at']}")
    add("═" * 100)

    s = rep["summary"]
    add("")
    add(f"  {s['thresholds_checked']} umbrales revisados · "
        f"{s['unreachable']} INALCANZABLES · {s['trivial']} TRIVIALES "
        f"({s['trivial_defects']} de ellos, defectos) · "
        f"{s['no_data']} sin datos · {s['ok']} OK")
    add(f"  {s['defects']} defecto(s) de calibración a decidir")
    add(f"  {s['rules_producing']}/12 reglas producen · {s['opportunities']} oportunidades · "
        f"{s['rules_broken']} regla(s) con problema")
    for note in rep.get("notes", []):
        add(f"  ⚠ {note}")

    # ── 1. distribuciones ──
    add("")
    add("─" * 100)
    add("  1. DISTRIBUCIÓN DE LOS SCORES  (contra qué escala hay que leer cada umbral)")
    add("─" * 100)
    rows = []
    for key, d in rep["distributions"].items():
        if not d["n"]:
            continue
        rows.append([key, d["n"], _fmt(d["min"]), _fmt(d["p25"]), _fmt(d["p50"]),
                     _fmt(d["p75"]), _fmt(d["p95"]), _fmt(d["max"]),
                     _fmt(d["analytic_max"]) if d["analytic_max"] is not None else "—"])
    add(_table(["métrica", "n", "min", "p25", "p50", "p75", "p95", "max", "techo analítico"],
               rows, ["<", ">", ">", ">", ">", ">", ">", ">", ">"]))

    ceilings = [(k, d) for k, d in rep["distributions"].items()
                if d.get("analytic_note")]
    if ceilings:
        add("")
        add("  Cotas analíticas (no dependen de que el dataset tenga suerte):")
        for key, d in ceilings:
            add(f"    · {key}: {d['analytic_note']}")

    # ── 2. alcanzabilidad ──
    add("")
    add("─" * 100)
    add("  2. ALCANZABILIDAD DE LOS UMBRALES")
    add("─" * 100)
    rows = []
    for r in rep["reachability"]:
        rows.append([
            "✗" if r["defect"] else _ICON.get(r["status"], " "), r["status"], r["path"],
            _fmt(r["value"], 2) if r["value"] is not None else "—",
            f"{'≥' if r['direction'] == 'min' else '≤'} {r['metric']}",
            f"{r['n_pass']}/{r['n']}" if r["n"] else "0/0",
            r.get("kind", ""), r["basis"],
        ])
    add(_table(["", "status", "ruta en weights.yaml", "valor", "filtra", "pasan", "tipo", "base"],
               rows))

    problems = [r for r in rep["reachability"]
                if r["status"] in (STATUS_UNREACHABLE, STATUS_NO_DATA, STATUS_TRIVIAL)]
    if problems:
        add("")
        add("  Detalle de los umbrales que no discriminan  "
            "(✗ = defecto de calibración · · = observación):")
        for r in problems:
            mark = "✗" if r["defect"] else "·"
            add(f"    {mark} {r['path']} = {_fmt(r['value'])}   [{r.get('kind')}]")
            add(f"        {r['reason']}")
            if r.get("what"):
                add(f"        decide: {r['what']}")

    # ── 3. rendimiento por regla ──
    add("")
    add("─" * 100)
    add("  3. RENDIMIENTO DE LAS 12 REGLAS")
    add("─" * 100)
    rows = []
    for r in rep["rule_yield"]:
        rows.append([_ICON.get(r["status"], " "), r["rule"], r["family"], r["n"],
                     r["status"], _fmt(r["importance_max"], 1)])
    add(_table(["", "regla", "familia", "n", "status", "imp.max"], rows,
               ["<", "<", "<", ">", "<", ">"]))
    add("")
    for r in rep["rule_yield"]:
        if r["status"] != YIELD_OK or r.get("blocking"):
            add(f"    {_ICON.get(r['status'], ' ')} {r['rule']}: {r['diagnosis']}")

    # ── 4. sugerencias ──
    add("")
    add("─" * 100)
    add("  4. UMBRALES SUGERIDOS  (propuesta — NO se modificó weights.yaml)")
    add("─" * 100)
    if not rep["suggestions"]:
        add("  Sin cambios: todos los umbrales caen dentro del rango alcanzable.")
    else:
        rows = []
        for path, info in rep["suggestions"].items():
            rows.append([path, _fmt(info["actual"]), _fmt(info["sugerido"]),
                         info["n_afectados"]])
        add(_table(["ruta", "actual", "sugerido", "n afectados"], rows,
                   ["<", ">", ">", ">"]))
        add("")
        for path, info in rep["suggestions"].items():
            add(f"    · {path}: {info['motivo']}")
        add("")
        add("  YAML para copiar y pegar:")
        add("")
        for line in rep["suggested_yaml"].splitlines():
            add(f"    {line}")

    add("")
    return "\n".join(out)


def render_sensitivity(rows: list[dict[str, Any]]) -> str:
    out = ["", "─" * 100, "  5. SENSIBILIDAD", "─" * 100]
    table_rows = []
    for r in rows:
        if "error" in r:
            table_rows.append([_fmt(r["value"], 3), "ERROR", r["error"], "", "", "", ""])
            continue
        table_rows.append([
            _fmt(r["value"], 3) + (" (actual)" if r.get("is_current") else ""),
            r["n_matches"], _fmt(r["match_score_p50"], 1), _fmt(r["match_score_max"], 1),
            r["n_opportunities"],
            " ".join(f"{k}:{v}" for k, v in (r.get("severity") or {}).items()),
            _fmt(r.get("spearman_vs_first"), 3),
            _fmt(r.get("top10_overlap_vs_first"), 3),
        ])
    out.append(_table(
        ["valor", "matches", "score p50", "score max", "oport.", "severidad",
         "spearman vs 1º", "top10 vs 1º"],
        table_rows, ["<", ">", ">", ">", ">", "<", ">", ">"]))
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Harness de calibración: detecta umbrales inalcanzables o triviales "
                    "y propone cortes desde la distribución real de los scores.")
    parser.add_argument("--db", default=str(DB_PATH), help="Ruta del archivo SQLite")
    parser.add_argument("--json", action="store_true", help="Salida JSON en vez de tablas")
    parser.add_argument("--sensitivity", metavar="PARAM",
                        help="Ruta del parámetro a barrer "
                             "(ej. competitive_match.evidence_shrinkage.prior)")
    parser.add_argument("--values", metavar="V1,V2,...",
                        help="Valores del barrido, separados por coma")
    parser.add_argument("--strict", action="store_true",
                        help="Devuelve 1 si hay algún umbral inalcanzable (para CI)")
    args = parser.parse_args(argv)

    rep = report(args.db)

    sweep: list[dict[str, Any]] | None = None
    if args.sensitivity:
        values = [float(v) for v in (args.values or "").split(",") if v.strip()]
        if not values:
            parser.error("--sensitivity requiere --values")
        sweep = sensitivity(args.sensitivity, values, args.db)
        rep["sensitivity"] = sweep
        # El barrido ya restaura la config; releerla de disco deja el proceso en
        # un estado idéntico al de arranque, pase lo que pase.
        restore_config_from_disk()

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(rep))
        if sweep is not None:
            print(render_sensitivity(sweep))

    if args.strict and rep["summary"]["unreachable"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
