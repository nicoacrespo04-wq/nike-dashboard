"""Triaje del Opportunity Center: descartar, posponer, asignar, resolver.

Todo se indexa por ``entity_key`` (ver ``app.services.triage``), nunca por
``opportunities.id``: ese id se pierde en cada corrida del pipeline.

Rutas:
    GET  /api/triage                 listado de filas de triaje + stats
    GET  /api/triage/{entity_key}    estado de una oportunidad
    POST /api/triage/bulk            misma transición para varias
    POST /api/triage/{entity_key}    transición de una
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import query
from app.services import triage

router = APIRouter(prefix="/api/triage", tags=["triage"])

_MAX_BULK = 200


class TriageUpdate(BaseModel):
    """Cuerpo de una transición. ``state`` se valida a mano para dar un 422 legible."""

    state: str = Field(..., description="open | snoozed | dismissed | resolved")
    assignee: str | None = Field(None, description="Responsable. '' limpia el campo.")
    note: str | None = Field(None, description="Nota del equipo. '' limpia el campo.")
    snooze_until: str | None = Field(
        None, description="Obligatorio y futuro si state=snoozed (YYYY-MM-DD o ISO).")
    updated_by: str | None = Field(None, description="Quién hizo el cambio (auditoría).")


class BulkTriageUpdate(TriageUpdate):
    entity_keys: list[str] = Field(..., description=f"Máximo {_MAX_BULK} claves.")


def _unprocessable(error: Exception) -> HTTPException:
    """Los errores de triaje ya traen el texto que tiene que leer un humano."""
    return HTTPException(status_code=422, detail=str(error))


def _opportunity_index() -> dict[str, dict[str, Any]]:
    """Mapa ``entity_key`` -> oportunidad vigente.

    Sirve para contestar "¿de qué era esto?" en el listado de triaje. Si la
    oportunidad ya no existe (el motor dejó de dispararla), el ítem igual se
    devuelve con ``opportunity: null``: que algo desaparezca del cálculo no
    borra la decisión que tomó el equipo.
    """
    if not query("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities'"):
        return {}
    rows = query(
        "SELECT id, opportunity_type, family, severity, title, business_importance, "
        "country_code, nike_product_id, competitor_product_id, retailer_id "
        "FROM opportunities ORDER BY business_importance DESC"
    )
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        index.setdefault(triage.entity_key(row), {
            "id": row["id"],
            "opportunity_type": row.get("opportunity_type"),
            "family": row.get("family"),
            "severity": row.get("severity"),
            "title": row.get("title"),
            "business_importance": row.get("business_importance"),
            "country_code": row.get("country_code"),
        })
    return index


@router.get("")
def list_triage(
    state: str | None = Query(None, description="Filtra por estado."),
    assignee: str | None = Query(None, description="Filtra por responsable."),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Filas de triaje + resumen. Vence los snoozes antes de contestar."""
    try:
        items = triage.list_states(state, assignee=assignee, limit=limit, offset=offset)
    except ValueError as exc:
        raise _unprocessable(exc) from exc

    index = _opportunity_index()
    for item in items:
        item["opportunity"] = index.get(str(item["entity_key"]))

    return {
        "total": len(items),
        "items": items,
        "stats": triage.stats(),
        "states": list(triage.STATES),
        "transitions": {k: list(v) for k, v in triage.TRANSITIONS.items()},
        "limit": limit,
        "offset": offset,
    }


@router.get("/{entity_key}")
def get_triage(entity_key: str) -> dict[str, Any]:
    """Estado de una oportunidad. Sin fila de triaje devuelve el ``open`` por defecto."""
    triage.expire_snoozes()
    try:
        state = triage.get_state(entity_key)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return state or triage.default_state(entity_key.strip())


@router.post("/bulk")
def bulk_update_triage(payload: BulkTriageUpdate) -> dict[str, Any]:
    """Misma transición para varias oportunidades.

    Parcialmente tolerante: aplica lo que puede y devuelve los rechazos con su
    motivo. Un `entity_key` que ya estaba descartado no debería frenar la
    operación sobre los otros 20.
    """
    keys = [k.strip() for k in payload.entity_keys if k and k.strip()]
    if not keys:
        raise HTTPException(status_code=422, detail="`entity_keys` no puede venir vacío.")
    if len(keys) > _MAX_BULK:
        raise HTTPException(
            status_code=422,
            detail=f"Demasiadas oportunidades en un solo pedido ({len(keys)}); el máximo es {_MAX_BULK}.",
        )
    if payload.state.strip().lower() not in triage.STATES:
        raise HTTPException(
            status_code=422,
            detail=f"Estado '{payload.state}' inválido. Estados permitidos: "
                   f"{', '.join(triage.STATES)}.",
        )

    updated: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for key in dict.fromkeys(keys):
        try:
            updated.append(triage.set_state(
                key, payload.state,
                assignee=payload.assignee, note=payload.note,
                snooze_until=payload.snooze_until, updated_by=payload.updated_by,
            ))
        except ValueError as exc:
            rejected.append({"entity_key": key, "detail": str(exc)})

    return {"updated": len(updated), "items": updated,
            "rejected": rejected, "stats": triage.stats()}


@router.post("/{entity_key}")
def update_triage(entity_key: str, payload: TriageUpdate) -> dict[str, Any]:
    """Aplica una transición a una oportunidad."""
    try:
        return triage.set_state(
            entity_key, payload.state,
            assignee=payload.assignee, note=payload.note,
            snooze_until=payload.snooze_until, updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise _unprocessable(exc) from exc
