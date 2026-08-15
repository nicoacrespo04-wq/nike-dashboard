"""Retail Media Opportunities: cuándo invertir en visibilidad en vez de descuento."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.serializers import expand_retail_media, table_exists
from app.config import section, weights
from app.db import query

router = APIRouter(prefix="/api", tags=["retail-media"])


@router.get("/retail-media")
def list_retail_media(
    recommendation: str | None = None,
    retailer: int | None = None,
    min_score: float = Query(0, ge=0, le=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if not table_exists("retail_media_opportunities"):
        return {"total": 0, "items": [], "facets": {}}

    where = ["score >= ?"]
    params: list[Any] = [min_score]
    if recommendation:
        where.append("recommendation = ?")
        params.append(recommendation)
    if retailer is not None:
        where.append("retailer_id = ?")
        params.append(retailer)
    clause = " WHERE " + " AND ".join(where)

    total = query(
        f"SELECT COUNT(*) AS n FROM retail_media_opportunities{clause}", params)[0]["n"]  # noqa: S608
    rows = query(
        f"SELECT * FROM retail_media_opportunities{clause} "  # noqa: S608
        f"ORDER BY score DESC, id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )

    return {
        "total": int(total),
        "items": expand_retail_media(rows),
        "facets": {
            "by_recommendation": query(
                "SELECT recommendation, COUNT(*) AS n, ROUND(AVG(score), 1) AS avg_score "
                "FROM retail_media_opportunities GROUP BY recommendation ORDER BY n DESC"),
        },
        "configured_weights": weights("retail_media", "weights"),
        "thresholds": section("retail_media", "thresholds", default={}),
        "limit": limit,
        "offset": offset,
    }
