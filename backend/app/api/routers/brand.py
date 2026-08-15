"""Argentina Consumer & Brand Intelligence."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.serializers import table_exists
from app.config import section
from app.db import query
from app.services.common import from_json

router = APIRouter(prefix="/api/brand", tags=["brand"])


@router.get("/insights")
def list_insights(
    country: str = "AR",
    dimension: str | None = None,
    brand: str | None = None,
    min_confidence: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Insights de marca respaldados por señal cuantitativa + evidencia."""
    if not table_exists("brand_insights"):
        return {"total": 0, "items": [], "taxonomy": section("brand_intelligence", "taxonomy", default={})}

    where = ["bi.country_code = ?"]
    params: list[Any] = [country]
    if dimension:
        where.append("bi.dimension = ?")
        params.append(dimension)
    if brand:
        where.append("b.name = ?")
        params.append(brand)
    if min_confidence:
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        allowed = [k for k, v in order.items() if v >= order.get(min_confidence.upper(), 0)]
        where.append(f"bi.confidence IN ({','.join('?' * len(allowed))})")
        params.extend(allowed)

    clause = " WHERE " + " AND ".join(where)
    rows = query(
        f"SELECT bi.*, b.name AS brand FROM brand_insights bi "  # noqa: S608
        f"LEFT JOIN brands b ON b.id = bi.brand_id{clause} "
        f"ORDER BY bi.signal_volume DESC, bi.id LIMIT ?",
        [*params, limit],
    )

    items = [{
        **r,
        "evidence": from_json(r.get("evidence"), []),
    } for r in rows]

    return {
        "total": len(items),
        "items": items,
        "taxonomy": section("brand_intelligence", "taxonomy", default={}),
    }


@router.get("/momentum")
def momentum(country: str = "AR", signal_type: str | None = None,
             entity_type: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Brand / Conversation Momentum: volumen, variación y aceleración."""
    if not table_exists("market_signals"):
        return {"items": []}

    where = ["(country_code = ? OR country_code IS NULL)"]
    params: list[Any] = [country]
    if signal_type:
        where.append("signal_type = ?")
        params.append(signal_type)
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    clause = " WHERE " + " AND ".join(where)

    rows = query(
        f"SELECT * FROM market_signals{clause} "  # noqa: S608
        f"ORDER BY ABS(COALESCE(acceleration, 0)) DESC, value DESC LIMIT ?",
        [*params, limit],
    )
    return {"items": rows}


@router.get("/topics")
def topics(country: str = "AR", limit: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
    """Tópicos de conversación con volumen y sentimiento agregados."""
    if not table_exists("social_mention_aggregates"):
        return {"items": []}

    rows = query(
        "SELECT sma.topic, sma.intent, b.name AS brand, "
        "       SUM(sma.mention_count) AS mentions, "
        "       ROUND(AVG(sma.sentiment_score), 3) AS sentiment, "
        "       MIN(sma.period_start) AS period_start, MAX(sma.period_end) AS period_end "
        "FROM social_mention_aggregates sma "
        "LEFT JOIN brands b ON b.id = sma.brand_id "
        "WHERE sma.country_code = ? AND sma.topic IS NOT NULL "
        "GROUP BY sma.topic, sma.intent, b.name "
        "ORDER BY mentions DESC LIMIT ?",
        (country, limit),
    )
    return {"items": rows}
