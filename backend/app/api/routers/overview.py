"""Executive Overview: las 5 preguntas del producto en una sola pantalla.

    1. WHAT IS HAPPENING?   -> kpis + momentum
    2. WHO IS COMPETING?    -> top_matches
    3. DOES IT MATTER?      -> ordenado por business_importance
    4. WHY?                 -> drivers de cada item
    5. WHAT SHOULD WE DO?   -> recommendation / retail media
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.serializers import (count, expand_opportunities, expand_retail_media,
                                 product_card, products_by_id, table_exists)
from app.db import query

router = APIRouter(prefix="/api", tags=["overview"])

RISK_FAMILIES = ("pricing", "competitive_threat", "distribution")


@router.get("/overview")
def overview(country: str = "AR", limit: int = Query(6, ge=1, le=25)) -> dict[str, Any]:
    kpis = {
        "products": count("products"),
        "nike_products": count(
            "products", "brand_id IN (SELECT id FROM brands WHERE is_focus = 1)")
            if table_exists("brands") else 0,
        "brands": count("brands"),
        "retailers": count("retailers"),
        "matches": count("competitive_matches"),
        "opportunities": count("opportunities"),
        "critical_opportunities": count("opportunities", "severity = 'CRITICAL'"),
        "high_opportunities": count("opportunities", "severity = 'HIGH'"),
        "retail_media_opportunities": count("retail_media_opportunities"),
        "brand_insights": count("brand_insights"),
    }

    opportunities: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    if table_exists("opportunities"):
        opportunities = expand_opportunities(query(
            "SELECT * FROM opportunities ORDER BY business_importance DESC LIMIT ?", (limit,)))
        placeholders = ",".join("?" * len(RISK_FAMILIES))
        risks = expand_opportunities(query(
            f"SELECT * FROM opportunities WHERE family IN ({placeholders}) "  # noqa: S608
            f"ORDER BY business_importance DESC LIMIT ?",
            [*RISK_FAMILIES, limit]))

    retail_media: list[dict[str, Any]] = []
    if table_exists("retail_media_opportunities"):
        retail_media = expand_retail_media(query(
            "SELECT * FROM retail_media_opportunities ORDER BY score DESC LIMIT ?", (limit,)))

    momentum: list[dict[str, Any]] = []
    if table_exists("market_signals"):
        momentum = query(
            "SELECT * FROM market_signals "
            "WHERE signal_type IN ('social_momentum','editorial_momentum','review_momentum') "
            "ORDER BY ABS(COALESCE(acceleration, 0)) DESC LIMIT ?", (limit,))

    top_matches: list[dict[str, Any]] = []
    if table_exists("competitive_matches"):
        rows = query(
            "SELECT * FROM competitive_matches ORDER BY match_score DESC LIMIT ?", (limit,))
        prods = products_by_id([r["nike_product_id"] for r in rows]
                               + [r["competitor_product_id"] for r in rows])
        top_matches = [{
            "id": r["id"],
            "match_score": r["match_score"],
            "confidence": r["confidence"],
            "nike_product": product_card(prods.get(r["nike_product_id"])),
            "competitor_product": product_card(prods.get(r["competitor_product_id"])),
        } for r in rows]

    brand_highlights: list[dict[str, Any]] = []
    if table_exists("brand_insights"):
        brand_highlights = query(
            "SELECT bi.*, b.name AS brand FROM brand_insights bi "
            "LEFT JOIN brands b ON b.id = bi.brand_id "
            "WHERE bi.country_code = ? AND bi.confidence IN ('HIGH','MEDIUM') "
            "ORDER BY bi.signal_volume DESC LIMIT ?", (country, limit))

    assortment_gaps = [o for o in opportunities if o.get("family") == "assortment"]

    return {
        "kpis": kpis,
        "top_opportunities": opportunities,
        "top_risks": risks,
        "retail_media": retail_media,
        "competitor_momentum": momentum,
        "top_matches": top_matches,
        "brand_highlights": brand_highlights,
        "assortment_gaps": assortment_gaps,
    }


@router.get("/health")
def health() -> dict[str, Any]:
    """Estado del pipeline: qué tablas tienen datos y cuáles faltan."""
    tables = ["brands", "countries", "retailers", "products", "product_attributes",
              "price_observations", "stock_observations", "reviews", "editorial_mentions",
              "social_mention_aggregates", "competitive_matches", "competitive_match_factors",
              "market_signals", "brand_insights", "opportunities", "recommendations",
              "retail_media_opportunities"]
    counts = {t: count(t) for t in tables}
    return {
        "status": "ok",
        "tables": counts,
        "empty_tables": [t for t, n in counts.items() if n == 0],
    }
