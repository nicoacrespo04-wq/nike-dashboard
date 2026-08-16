"""Opportunity Center: oportunidades y riesgos ordenados por Business Importance."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.glossary import glossary_payload
from app.api.serializers import expand_opportunities, table_exists
from app.db import query

router = APIRouter(prefix="/api", tags=["opportunities"])


@router.get("/opportunities")
def list_opportunities(
    family: str | None = None,
    opportunity_type: str | None = None,
    severity: str | None = None,
    country: str | None = None,
    min_importance: float = Query(0, ge=0, le=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if not table_exists("opportunities"):
        return {"total": 0, "items": [], "facets": {},
                "glossary": glossary_payload("business_importance")}

    where = ["business_importance >= ?"]
    params: list[Any] = [min_importance]
    for column, value in (("family", family), ("opportunity_type", opportunity_type),
                          ("severity", severity), ("country_code", country)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    clause = " WHERE " + " AND ".join(where)

    total = query(f"SELECT COUNT(*) AS n FROM opportunities{clause}", params)[0]["n"]  # noqa: S608
    rows = query(
        f"SELECT * FROM opportunities{clause} "  # noqa: S608
        f"ORDER BY business_importance DESC, id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    )

    facets = {
        "by_family": query(
            "SELECT family, COUNT(*) AS n FROM opportunities GROUP BY family ORDER BY n DESC"),
        "by_severity": query(
            "SELECT severity, COUNT(*) AS n FROM opportunities GROUP BY severity"),
        "by_type": query(
            "SELECT opportunity_type, COUNT(*) AS n FROM opportunities "
            "GROUP BY opportunity_type ORDER BY n DESC"),
    }

    return {"total": int(total), "items": expand_opportunities(rows),
            "facets": facets,
            # Qué mide cada driver de Business Importance. Se publica acá y no
            # sólo en /api/retail-media para que el Opportunity Center no tenga
            # que ir a buscarlo a otro endpoint.
            "glossary": glossary_payload("business_importance"),
            "limit": limit, "offset": offset}


@router.get("/opportunities/{opportunity_id}")
def get_opportunity(opportunity_id: int) -> dict[str, Any]:
    if not table_exists("opportunities"):
        raise HTTPException(status_code=404, detail="Sin oportunidades calculadas")
    rows = query("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Oportunidad no encontrada")
    return expand_opportunities(rows)[0]
