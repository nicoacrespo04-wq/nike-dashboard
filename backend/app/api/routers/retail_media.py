"""Retail Media Opportunities: cuándo invertir en visibilidad en vez de descuento.

Unidad publicada: el **cuadro** = (producto Nike × retailer), con la lista de
competidores relevantes adentro (`competitors`). El motor decide sobre el
conjunto, así que la tarjeta muestra el conjunto.

Compatibilidad: `competitor_product` sigue viajando y apunta al competidor
LÍDER del cuadro (el más relevante), de modo que un consumidor viejo del
contrato ve un rival coherente en lugar de uno arbitrario.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.glossary import glossary_payload
from app.api.serializers import (expand_retail_media, product_card, products_by_id,
                                 table_exists)
from app.config import section, weights
from app.db import query
from app.services.common import from_json

router = APIRouter(prefix="/api", tags=["retail-media"])

#: Campos del bloque por competidor que el motor persiste dentro de `drivers`.
COMPETITOR_FIELDS = ("competitor_product_id", "match_score", "relevance_weight",
                     "stock_pct", "price_gap_pct", "nike_price", "competitor_price",
                     "price_basis", "momentum", "present_at_retailer", "is_leader",
                     "is_price_reference", "is_momentum_reference")


def _competitor_blocks(drivers: Any) -> list[dict[str, Any]]:
    """Bloques por competidor del sobre persistido, tolerando el formato viejo."""
    payload = drivers if isinstance(drivers, list) else [drivers]
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        blocks = entry.get("competitors")
        if isinstance(blocks, list):
            return [b for b in blocks if isinstance(b, dict)]
    return []


def _with_competitors(items: list[dict[str, Any]],
                      rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adjunta a cada cuadro su lista de competidores, ya resuelta contra el catálogo.

    Se hace acá y no en `expand_retail_media` porque la serialización canónica
    de `drivers` es transversal a todos los motores: la lista de competidores
    es propia de retail media.
    """
    by_row = {int(r["id"]): _competitor_blocks(from_json(r.get("drivers"), [])) for r in rows}
    ids = [b.get("competitor_product_id") for blocks in by_row.values() for b in blocks]
    prods = products_by_id([i for i in ids if isinstance(i, int)])

    for item in items:
        blocks = by_row.get(int(item["id"]), [])
        item["competitors"] = [
            {**{k: block.get(k) for k in COMPETITOR_FIELDS},
             "product": product_card(prods.get(block.get("competitor_product_id")))}
            for block in blocks
        ]
        item["competitor_count"] = len(blocks)
    return items


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
        "items": _with_competitors(expand_retail_media(rows), rows),
        "facets": {
            "by_recommendation": query(
                "SELECT recommendation, COUNT(*) AS n, ROUND(AVG(score), 1) AS avg_score "
                "FROM retail_media_opportunities GROUP BY recommendation ORDER BY n DESC"),
        },
        "configured_weights": weights("retail_media", "weights"),
        "thresholds": section("retail_media", "thresholds", default={}),
        # Qué mide cada factor: la UI lo muestra en el InfoTip de cada driver.
        # `business_importance` viaja porque es uno de los 7 factores del score.
        "glossary": glossary_payload("retail_media", "business_importance"),
        "limit": limit,
        "offset": offset,
    }
