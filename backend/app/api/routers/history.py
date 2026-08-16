"""Historial: corridas, series temporales y antigüedad de oportunidades.

Todo se indexa por `entity_key` (ver `app/services/history.py`), la identidad
estable que sobrevive al recálculo. Los `id` de `opportunities` y
`competitive_matches` NO sirven acá: cambian en cada corrida.

Registrar en `app/main.py` (archivo de otro módulo) con dos cambios::

    from app.api.routers import brand, history, matches, opportunities, overview, products, retail_media

    for router in (overview.router, products.router, matches.router,
                   opportunities.router, retail_media.router, brand.router,
                   history.router):
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.serializers import product_card, products_by_id, retailers_by_id, table_exists
from app.services import history as H

router = APIRouter(prefix="/api/history", tags=["history"])


def _summary(points: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Resumen de una serie: primer/último valor, variación total y tendencia."""
    values = [p.get(field) for p in points]
    clean = [float(v) for v in values if v is not None]
    return {
        "points": len(points),
        "first": clean[0] if clean else None,
        "last": clean[-1] if clean else None,
        "change": round(clean[-1] - clean[0], 4) if len(clean) >= 2 else None,
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
        "trend": H.classify_trend(clean),
        "first_seen": points[0].get("observed_at") if points else None,
        "last_seen": points[-1].get("observed_at") if points else None,
    }


@router.get("/runs")
def list_runs(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Corridas del pipeline, de la más reciente a la más vieja, con sus conteos."""
    if not table_exists("pipeline_runs"):
        return {"total": 0, "items": []}
    runs = H.list_runs(limit=limit)
    return {"total": len(runs), "items": runs}


@router.get("/matches/{entity_key}")
def match_history(entity_key: str) -> dict[str, Any]:
    """Serie temporal de un match: ¿el match con Novablast viene subiendo?"""
    if not table_exists("match_history"):
        raise HTTPException(status_code=404, detail="Sin historial de matches")

    points = H.match_trend(entity_key)
    if not points:
        raise HTTPException(status_code=404, detail="entity_key sin historial")

    last = points[-1]
    prods = products_by_id([last.get("nike_product_id"), last.get("competitor_product_id")])
    return {
        "entity_key": entity_key,
        "nike_product": product_card(prods.get(last.get("nike_product_id"))),
        "competitor_product": product_card(prods.get(last.get("competitor_product_id"))),
        "summary": _summary(points, "match_score"),
        "points": points,
    }


@router.get("/opportunities")
def opportunity_ages(only_open: bool = True,
                     limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    """Antigüedad + tendencia de las oportunidades abiertas.

    Ordenadas por racha (`runs_open`) y luego por importancia: arriba quedan las
    que llevan más tiempo sin resolverse — exactamente lo que antes no se podía
    preguntar.
    """
    if not table_exists("opportunity_history"):
        return {"total": 0, "runs": 0, "items": []}

    ages = H.opportunity_age(only_open=only_open)
    items = sorted(ages.values(),
                   key=lambda a: (a.get("runs_open") or 0,
                                  a.get("business_importance") or 0.0),
                   reverse=True)[:limit]

    prods = products_by_id([a.get("nike_product_id") for a in items]
                           + [a.get("competitor_product_id") for a in items])
    rets = retailers_by_id([a.get("retailer_id") for a in items])
    enriched = [{
        **a,
        "nike_product": product_card(prods.get(a.get("nike_product_id"))),
        "competitor_product": product_card(prods.get(a.get("competitor_product_id"))),
        "retailer": rets.get(a.get("retailer_id")),
    } for a in items]

    return {
        "total": len(ages),
        "runs": len(H.list_runs(limit=1000)),
        "items": enriched,
    }


@router.get("/opportunities/{entity_key}")
def opportunity_history(entity_key: str) -> dict[str, Any]:
    """Serie temporal de una oportunidad: ¿lleva 3 semanas abierta? ¿se agrava?"""
    if not table_exists("opportunity_history"):
        raise HTTPException(status_code=404, detail="Sin historial de oportunidades")

    points = H.opportunity_trend(entity_key)
    if not points:
        raise HTTPException(status_code=404, detail="entity_key sin historial")

    age = H.opportunity_age(only_open=False).get(entity_key, {})
    last = points[-1]
    prods = products_by_id([last.get("nike_product_id"), last.get("competitor_product_id")])
    rets = retailers_by_id([last.get("retailer_id")])
    return {
        "entity_key": entity_key,
        "age": age,
        "nike_product": product_card(prods.get(last.get("nike_product_id"))),
        "competitor_product": product_card(prods.get(last.get("competitor_product_id"))),
        "retailer": rets.get(last.get("retailer_id")),
        "summary": _summary(points, "business_importance"),
        "points": points,
    }


@router.get("/signals")
def signal_history(signal_type: str = Query(..., description="social_momentum|share_of_shelf|..."),
                   entity_type: str = Query(..., description="product|brand|franchise|retailer"),
                   entity_id: str = Query(...)) -> dict[str, Any]:
    """Serie de una señal + la `acceleration` que el historial hace posible.

    `market_signals.acceleration` queda NULL para las señales de momentum porque
    hace falta una tercera ventana temporal; con ≥3 snapshots se deriva acá.
    """
    if not table_exists("signal_history"):
        return {"signal_type": signal_type, "entity_type": entity_type,
                "entity_id": entity_id, "points": [], "acceleration": None}

    points = H.signal_trend(signal_type, entity_type, entity_id)
    return {
        "signal_type": signal_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "summary": _summary(points, "value"),
        "acceleration": H.signal_acceleration(signal_type, entity_type, entity_id),
        "points": points,
    }
