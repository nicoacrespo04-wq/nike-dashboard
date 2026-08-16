"""Competitive Matches + explicabilidad (feature importance)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.glossary import glossary_payload
from app.api.serializers import PRODUCT_CARD_SQL, product_card, products_by_id, table_exists
from app.config import weights
from app.db import query
from app.services.common import from_json

router = APIRouter(prefix="/api", tags=["matches"])


def _factors(match_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not match_ids or not table_exists("competitive_match_factors"):
        return {}
    placeholders = ",".join("?" * len(match_ids))
    rows = query(
        f"SELECT * FROM competitive_match_factors WHERE match_id IN ({placeholders}) "  # noqa: S608
        f"ORDER BY contribution DESC",
        match_ids,
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(int(r["match_id"]), []).append({
            "factor": r["factor"],
            "raw_score": r["raw_score"],
            "weight": r["weight"],
            "contribution": r["contribution"],
            "available": bool(r["available"]),
            "detail": from_json(r.get("detail"), {}),
        })
    return out


@router.get("/products/{product_id}/matches")
def product_matches(product_id: int, limit: int = Query(10, ge=1, le=50),
                    with_factors: bool = True) -> dict[str, Any]:
    """Top competidores de un producto Nike, con su desglose de factores."""
    if not table_exists("competitive_matches"):
        return {"product": None, "matches": []}

    subject = query(f"{PRODUCT_CARD_SQL} WHERE p.id = ?", (product_id,))
    if not subject:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    rows = query(
        "SELECT * FROM competitive_matches WHERE nike_product_id = ? "
        "ORDER BY match_score DESC LIMIT ?",
        (product_id, limit),
    )
    prods = products_by_id([r["competitor_product_id"] for r in rows])
    factors = _factors([int(r["id"]) for r in rows]) if with_factors else {}

    return {
        "product": product_card(subject[0]),
        # Qué mide cada factor (mismo texto que `docs/glossary.md`).
        "glossary": glossary_payload("competitive_match"),
        "matches": [{
            "id": r["id"],
            "match_score": r["match_score"],
            "raw_match_score": r["raw_match_score"],
            "confidence": r["confidence"],
            "coverage": r["coverage"],
            "competitor": product_card(prods.get(r["competitor_product_id"])),
            "factors": factors.get(int(r["id"]), []),
            "computed_at": r.get("computed_at"),
        } for r in rows],
    }


@router.get("/matches/{match_id}")
def get_match(match_id: int) -> dict[str, Any]:
    """Detalle completo de un match: ambos productos + feature importance."""
    if not table_exists("competitive_matches"):
        raise HTTPException(status_code=404, detail="Sin matches calculados")

    rows = query("SELECT * FROM competitive_matches WHERE id = ?", (match_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Match no encontrado")
    match = rows[0]

    prods = products_by_id([match["nike_product_id"], match["competitor_product_id"]])
    detail = _factors([match_id]).get(match_id, [])

    return {
        "id": match["id"],
        "match_score": match["match_score"],
        "raw_match_score": match["raw_match_score"],
        "confidence": match["confidence"],
        "coverage": match["coverage"],
        "nike_product": product_card(prods.get(match["nike_product_id"])),
        "competitor_product": product_card(prods.get(match["competitor_product_id"])),
        "factors": detail,
        "configured_weights": weights("competitive_match", "weights"),
        # Junto a los pesos configurados viaja QUÉ MIDE cada factor: sin eso la
        # pantalla de explicabilidad muestra siete nombres y ningún significado.
        "glossary": glossary_payload("competitive_match"),
        "computed_at": match.get("computed_at"),
    }


@router.get("/matches")
def list_matches(min_score: float = Query(0, ge=0, le=100),
                 limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Listado plano de matches, para vistas de exploración."""
    if not table_exists("competitive_matches"):
        return {"total": 0, "items": []}

    rows = query(
        "SELECT * FROM competitive_matches WHERE match_score >= ? "
        "ORDER BY match_score DESC LIMIT ?",
        (min_score, limit),
    )
    prods = products_by_id([r["nike_product_id"] for r in rows]
                           + [r["competitor_product_id"] for r in rows])
    return {
        "total": len(rows),
        "items": [{
            "id": r["id"],
            "match_score": r["match_score"],
            "raw_match_score": r["raw_match_score"],
            "confidence": r["confidence"],
            "nike_product": product_card(prods.get(r["nike_product_id"])),
            "competitor_product": product_card(prods.get(r["competitor_product_id"])),
        } for r in rows],
    }
