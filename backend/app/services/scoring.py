"""Business Importance Score.

Separa "hay una diferencia" de "esta diferencia IMPORTA".

    importance = 100 * gate * lifecycle * sum(w_i * s_i) / sum(w_i disponibles)

* Los 11 componentes viven en ``business_importance.weights`` (weights.yaml).
  NINGUNO está hardcodeado acá: se recorre el bloque de config tal cual está.
* Cada componente se normaliza a 0..1. Si falta el dato, el factor queda
  ``raw_score=None`` -> se excluye y se renormaliza (nunca se asume 0).
* ``gate = clamp(competitive_relevance / gate_full_relevance, gate_floor, 1.0)``:
  una diferencia sin competencia real no puede generar una oportunidad
  importante. Es un VETO de la cola baja, no un impuesto: a partir de
  ``gate_full_relevance`` el competidor ya es real y el gate deja de atenuar,
  así la escala 0..100 se puede usar entera (ver ``relevance_gate``). Si no hay
  relevancia competitiva medible se usa el piso (``gate_floor``), no 1.0.
* ``lifecycle_multiplier`` pondera según la etapa del producto Nike.

Entrada (``subject``): dict flexible. Para cada componente ``X`` acepta
    1. ``X``            -> valor ya normalizado 0..1
    2. ``X_raw`` (+ ``X_max`` o ``ctx.maxima["X"]``) -> normalización relativa
       al corpus (sin constantes mágicas)
    3. derivaciones específicas por componente (ver ``_DERIVATIONS``)
"""

from __future__ import annotations

from typing import Any, Callable

from app.config import section, weights
from app.services.common import (
    CompositeScore,
    Factor,
    clamp,
    combine,
    severity_from_score,
)

# ── helpers de configuración ────────────────────────────────


def franchise_importance(franchise: str | None) -> float:
    """Importancia 0..1 de una franquicia según el mapa de config.

    Las franquicias no listadas caen en ``default``.
    """
    fmap = section("business_importance", "franchise_importance", default={}) or {}
    fallback = float(fmap.get("default", 0.0))
    if not franchise:
        return fallback
    key = str(franchise).strip()
    if key in fmap:
        return float(fmap[key])
    lowered = {str(k).lower(): v for k, v in fmap.items()}
    return float(lowered.get(key.lower(), fallback))


def lifecycle_multiplier(stage: str | None) -> float:
    """Multiplicador por etapa de ciclo de vida (1.0 si no está mapeada)."""
    lmap = section("business_importance", "lifecycle_multiplier", default={}) or {}
    if not stage:
        return 1.0
    lowered = {str(k).lower(): v for k, v in lmap.items()}
    return float(lowered.get(str(stage).strip().lower(), 1.0))


def gate_floor() -> float:
    return float(section("business_importance", "gate_floor", default=0.0) or 0.0)


def gate_full_relevance() -> float:
    """Relevancia competitiva a partir de la cual el gate deja de atenuar.

    Si es 0 (o falta) el gate vuelve a ser el multiplicador puro histórico.
    """
    return float(section("business_importance", "gate_full_relevance", default=0.0) or 0.0)


def relevance_gate(relevance: float | None) -> float:
    """Gate de relevancia competitiva: **veto**, no impuesto permanente.

        gate = clamp(relevance / gate_full_relevance, gate_floor, 1.0)

    Sin relevancia medible se aplica el piso (``gate_floor``), nunca 1.0.

    Por qué una rampa que satura y no ``clamp(relevance, floor, 1)``:
    el gate existe para APAGAR lo que no tiene competencia real ("un gap de
    precio contra nadie no importa"). Multiplicar SIEMPRE por la relevancia
    hacía otra cosa: (a) le ponía a la escala un techo igual al mejor match del
    corpus —con ``match_score`` máximo 75.2, la importancia no podía pasar de
    ~82 aunque los 11 factores dieran 1.0, y ``CRITICAL`` era inalcanzable por
    construcción, no por falta de casos graves—, y (b) contaba la relevancia
    dos veces, porque ``competitive_relevance`` ya es uno de los 11 factores
    ponderados (w=0.20). Con la rampa, por encima de ``gate_full_relevance``
    el competidor ya es "real" y la escala se usa entera; por debajo, la
    atenuación sigue existiendo y llega hasta el piso.
    """
    floor = gate_floor()
    if relevance is None:
        return clamp(floor, 0.0, 1.0)
    full = gate_full_relevance()
    ratio = (relevance / full) if full > 0 else relevance
    return clamp(ratio, floor, 1.0)


def severity(score: float) -> str:
    """CRITICAL | HIGH | MEDIUM | LOW según ``severity_thresholds`` de config."""
    thresholds = section("business_importance", "severity_thresholds", default={}) or {}
    return severity_from_score(float(score), thresholds)


# ── normalizadores por componente ───────────────────────────


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # descarta NaN


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num, den = _num(numerator), _num(denominator)
    if num is None or den is None or den <= 0:
        return None
    return clamp(num / den)


def _mean(values: Any) -> float | None:
    if not isinstance(values, (list, tuple, set)):
        return None
    nums = [_num(v) for v in values]
    nums = [n for n in nums if n is not None]
    if not nums:
        return None
    return clamp(sum(nums) / len(nums))


def _derive_competitive_relevance(s: dict, _m: dict) -> tuple[float | None, dict]:
    score = _num(s.get("match_score"))
    if score is None:
        return None, {}
    return clamp(score / 100.0), {"match_score": round(score, 2), "source": "competitive_matches"}


def _derive_franchise(s: dict, _m: dict) -> tuple[float | None, dict]:
    if "franchise" not in s:
        return None, {}
    franchise = s.get("franchise")
    value = franchise_importance(franchise)
    return clamp(value), {"franchise": franchise or "(sin franquicia)", "source": "config"}


def _derive_retailer_importance(s: dict, _m: dict) -> tuple[float | None, dict]:
    value = _mean(s.get("retailer_importances"))
    if value is None:
        return None, {}
    return value, {"retailers": len(s.get("retailer_importances") or []), "source": "retailers.importance"}


def _derive_market_coverage(s: dict, _m: dict) -> tuple[float | None, dict]:
    value = _ratio(s.get("retailers_present"), s.get("retailers_total"))
    if value is None:
        return None, {}
    return value, {
        "retailers_present": s.get("retailers_present"),
        "retailers_total": s.get("retailers_total"),
    }


def _derive_price_gap(s: dict, _m: dict) -> tuple[float | None, dict]:
    gap = _num(s.get("price_gap_pct"))
    if gap is None:
        return None, {}
    return clamp(abs(gap) / 100.0), {"price_gap_pct": round(gap, 2)}


def _derive_share_of_shelf(s: dict, _m: dict) -> tuple[float | None, dict]:
    share = _num(s.get("nike_shelf_share"))
    if share is None:
        return None, {}
    # Cuanto menor es el share of shelf de Nike, mayor la presión competitiva.
    return clamp(1.0 - clamp(share)), {"nike_shelf_share": round(share, 4)}


def _derive_promo_intensity(s: dict, _m: dict) -> tuple[float | None, dict]:
    pct = _num(s.get("promo_intensity_pct"))
    if pct is None:
        return None, {}
    return clamp(pct / 100.0), {"avg_discount_pct": round(pct, 2)}


# Derivaciones alternativas cuando el valor no viene ya normalizado.
_DERIVATIONS: dict[str, Callable[[dict, dict], tuple[float | None, dict]]] = {
    "competitive_relevance": _derive_competitive_relevance,
    "franchise_importance": _derive_franchise,
    "retailer_importance": _derive_retailer_importance,
    "market_coverage": _derive_market_coverage,
    "price_gap": _derive_price_gap,
    "share_of_shelf": _derive_share_of_shelf,
    "promo_intensity": _derive_promo_intensity,
}


def _resolve(name: str, subject: dict, maxima: dict) -> tuple[float | None, dict]:
    """Resuelve un componente a 0..1 con su detalle explicativo."""
    # 1. valor directo ya normalizado
    direct = _num(subject.get(name))
    if direct is not None:
        return clamp(direct), {"source": "input"}

    # 2. valor crudo + máximo del corpus (normalización relativa, sin constantes)
    raw = _num(subject.get(f"{name}_raw"))
    if raw is not None:
        ceiling = _num(subject.get(f"{name}_max"))
        if ceiling is None:
            ceiling = _num(maxima.get(name))
        value = _ratio(raw, ceiling)
        if value is not None:
            return value, {"raw": round(raw, 4), "corpus_max": round(float(ceiling), 4)}

    # 3. derivación específica del componente
    derive = _DERIVATIONS.get(name)
    if derive is not None:
        return derive(subject, maxima)

    return None, {"reason": "sin datos"}


# ── score compuesto ─────────────────────────────────────────


def business_importance(subject: dict[str, Any], ctx: Any = None) -> CompositeScore:
    """Business Importance Score (0..100) con explicabilidad completa.

    ``ctx`` es opcional; si expone ``maxima`` (dict) se usa para normalizar los
    componentes crudos contra el máximo observado en el corpus.
    """
    subject = dict(subject or {})
    maxima = dict(getattr(ctx, "maxima", None) or {})
    if isinstance(subject.get("maxima"), dict):
        maxima.update(subject["maxima"])

    w = weights("business_importance", "weights")

    factors: list[Factor] = []
    values: dict[str, float | None] = {}
    for name, weight in w.items():
        raw, detail = _resolve(name, subject, maxima)
        values[name] = raw
        factors.append(Factor(name, raw, float(weight), detail))

    base = combine(factors, section("business_importance", "confidence_thresholds"))

    floor = gate_floor()
    relevance = values.get("competitive_relevance")
    # Sin relevancia competitiva medible se aplica el piso: nunca se asume 1.0.
    gate = relevance_gate(relevance)
    stage = subject.get("lifecycle_stage")
    multiplier = lifecycle_multiplier(stage)

    final = clamp(base.score * gate * multiplier, 0.0, 100.0)

    meta = {
        "base_score": round(base.score, 2),
        "gate": round(gate, 4),
        "gate_floor": floor,
        "gate_full_relevance": gate_full_relevance(),
        "gate_source": "competitive_relevance" if relevance is not None else "gate_floor",
        "lifecycle_stage": stage,
        "lifecycle_multiplier": multiplier,
        "final_score": round(final, 2),
    }
    for row in base.factors:
        if row["factor"] == "competitive_relevance":
            row["detail"] = {**(row.get("detail") or {}), **meta}

    return CompositeScore(
        score=final,
        coverage=base.coverage,
        confidence=base.confidence,
        factors=base.factors,
    )
