"""Helpers compartidos por los servicios de scoring.

Reglas transversales del motor:
  * Todo sub-score vive en 0..1. Los scores publicados son 0..100.
  * Un factor sin datos NO penaliza: se marca ``available=False``, se excluye
    de la suma y se renormaliza sobre los pesos disponibles.
  * La cobertura (suma de pesos con datos) determina la confianza.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ── numéricos ───────────────────────────────────────────────


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def saturate(points: float, k: float) -> float:
    """Saturación exponencial: 0 puntos -> 0, mucho -> ~1. Nunca supera 1."""
    if k <= 0:
        return 0.0
    return 1.0 - math.exp(-max(0.0, points) / k)


def gap_similarity(a: float | None, b: float | None, tolerance_pct: float) -> float | None:
    """Similitud 0..1 entre dos magnitudes según su gap relativo.

    Devuelve ``None`` si falta algún valor (factor no disponible).
    """
    if a is None or b is None:
        return None
    base = max(abs(float(a)), abs(float(b)))
    if base == 0:
        return 1.0
    gap_pct = abs(float(a) - float(b)) / base * 100.0
    if tolerance_pct <= 0:
        return 0.0
    return clamp(1.0 - gap_pct / tolerance_pct)


def recency_weight(when: str | date | datetime | None, half_life_days: float,
                   reference: date | None = None) -> float:
    """Decaimiento exponencial por antigüedad. Sin fecha => 0.5 (neutro)."""
    parsed = parse_date(when)
    if parsed is None or half_life_days <= 0:
        return 0.5
    ref = reference or date.today()
    age = max(0, (ref - parsed).days)
    return 0.5 ** (age / half_life_days)


def parse_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def jaccard(a: set[str], b: set[str]) -> float | None:
    if not a or not b:
        return None
    union = a | b
    return len(a & b) / len(union) if union else None


# ── scoring ponderado con factores opcionales ───────────────


@dataclass
class Factor:
    """Un componente de un score compuesto."""

    name: str
    raw_score: float | None          # 0..1; None => sin datos
    weight: float
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.raw_score is not None


@dataclass
class CompositeScore:
    """Resultado de combinar factores: score, cobertura y explicabilidad."""

    score: float                     # 0..100
    coverage: float                  # 0..1 (suma de pesos con datos)
    confidence: str                  # LOW | MEDIUM | HIGH
    factors: list[dict[str, Any]]    # incluye contribution en % (suma 100)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "coverage": round(self.coverage, 4),
            "confidence": self.confidence,
            "factors": self.factors,
        }

    @property
    def drivers(self) -> list[dict[str, Any]]:
        """Factores disponibles ordenados por contribución descendente."""
        return sorted(
            [f for f in self.factors if f["available"]],
            key=lambda f: f["contribution"],
            reverse=True,
        )


def combine(factors: list[Factor], thresholds: dict[str, float] | None = None,
            scale: float = 100.0) -> CompositeScore:
    """Combina factores renormalizando sobre los que tienen datos.

    ``contribution`` es el porcentaje del score final aportado por cada factor
    (suma 100 entre los disponibles) — es la feature importance que consume el
    frontend.
    """
    available = [f for f in factors if f.available]
    total_weight = sum(f.weight for f in factors) or 1.0
    avail_weight = sum(f.weight for f in available)

    if avail_weight <= 0:
        score = 0.0
        weighted: dict[str, float] = {}
    else:
        weighted = {f.name: f.weight * float(f.raw_score) for f in available}
        score = scale * sum(weighted.values()) / avail_weight

    total_contrib = sum(weighted.values()) or 1.0
    rows: list[dict[str, Any]] = []
    for f in factors:
        contribution = round(100.0 * weighted.get(f.name, 0.0) / total_contrib, 2) if f.available else 0.0
        rows.append({
            "factor": f.name,
            "raw_score": round(float(f.raw_score), 4) if f.available else None,
            "weight": round(f.weight, 4),
            "contribution": contribution,
            "available": f.available,
            "detail": f.detail,
        })

    coverage = avail_weight / total_weight
    return CompositeScore(score=score, coverage=coverage,
                          confidence=confidence_from_coverage(coverage, thresholds),
                          factors=rows)


def confidence_from_coverage(coverage: float, thresholds: dict[str, float] | None = None) -> str:
    th = thresholds or {"high": 0.70, "medium": 0.45}
    if coverage >= float(th.get("high", 0.70)):
        return "HIGH"
    if coverage >= float(th.get("medium", 0.45)):
        return "MEDIUM"
    return "LOW"


def severity_from_score(score: float, thresholds: dict[str, float]) -> str:
    if score >= float(thresholds.get("critical", 78)):
        return "CRITICAL"
    if score >= float(thresholds.get("high", 60)):
        return "HIGH"
    if score >= float(thresholds.get("medium", 40)):
        return "MEDIUM"
    return "LOW"


# ── serialización ───────────────────────────────────────────


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
