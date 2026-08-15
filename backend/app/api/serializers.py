"""Helpers de serialización compartidos por los routers.

Los routers leen SQL directo (no dependen de los servicios) para que la API
siga respondiendo aunque una etapa del pipeline no haya corrido.
"""

from __future__ import annotations

from typing import Any

from app.db import query
from app.services.common import from_json

PRODUCT_CARD_SQL = """
SELECT p.id, p.product_name, p.normalized_product_name, p.franchise, p.model, p.version,
       p.sku, p.style_code, p.category, p.subcategory, p.sport, p.activity, p.use_case,
       p.gender, p.age_segment, p.performance_vs_lifestyle, p.consumer_segment,
       p.lifecycle_stage, p.msrp, p.price_band, p.url, p.image_url, p.description,
       p.launch_date, p.country_code,
       b.name AS brand, b.is_focus
FROM products p
JOIN brands b ON b.id = p.brand_id
"""


def table_exists(name: str) -> bool:
    rows = query("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,))
    return bool(rows)


def count(table: str, where: str = "", params: tuple = ()) -> int:
    if not table_exists(table):
        return 0
    sql = f"SELECT COUNT(*) AS n FROM {table}"  # noqa: S608 - tabla de lista fija interna
    if where:
        sql += f" WHERE {where}"
    rows = query(sql, params)
    return int(rows[0]["n"]) if rows else 0


def product_card(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Versión reducida de un producto, para embeber en matches/oportunidades."""
    if not row:
        return None
    return {
        "id": row.get("id"),
        "brand": row.get("brand"),
        "product_name": row.get("product_name"),
        "franchise": row.get("franchise"),
        "use_case": row.get("use_case"),
        "category": row.get("category"),
        "price_band": row.get("price_band"),
        "msrp": row.get("msrp"),
        "image_url": row.get("image_url"),
        "lifecycle_stage": row.get("lifecycle_stage"),
    }


def products_by_id(ids: list[int]) -> dict[int, dict[str, Any]]:
    """Carga en un solo query las tarjetas de producto pedidas."""
    clean = sorted({int(i) for i in ids if i is not None})
    if not clean:
        return {}
    placeholders = ",".join("?" * len(clean))
    rows = query(f"{PRODUCT_CARD_SQL} WHERE p.id IN ({placeholders})", clean)  # noqa: S608
    return {int(r["id"]): r for r in rows}


def retailers_by_id(ids: list[int]) -> dict[int, dict[str, Any]]:
    clean = sorted({int(i) for i in ids if i is not None})
    if not clean:
        return {}
    placeholders = ",".join("?" * len(clean))
    rows = query(
        f"SELECT id, name, channel, importance, country_code FROM retailers WHERE id IN ({placeholders})",  # noqa: S608
        clean,
    )
    return {int(r["id"]): r for r in rows}


def expand_opportunities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adjunta producto Nike, competidor, retailer y recomendación a cada oportunidad."""
    if not rows:
        return []

    prods = products_by_id([r.get("nike_product_id") for r in rows]
                           + [r.get("competitor_product_id") for r in rows])
    rets = retailers_by_id([r.get("retailer_id") for r in rows])

    recs: dict[int, dict[str, Any]] = {}
    if table_exists("recommendations"):
        ids = [int(r["id"]) for r in rows]
        placeholders = ",".join("?" * len(ids))
        for rec in query(
            f"SELECT * FROM recommendations WHERE opportunity_id IN ({placeholders})",  # noqa: S608
            ids,
        ):
            recs[int(rec["opportunity_id"])] = {
                "action": rec.get("action"),
                "rationale": rec.get("rationale"),
                "score": rec.get("score"),
                "confidence": rec.get("confidence"),
                "drivers": from_json(rec.get("drivers"), []),
            }

    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "opportunity_type": r.get("opportunity_type"),
            "family": r.get("family"),
            "severity": r.get("severity"),
            "title": r.get("title"),
            "description": r.get("description"),
            "business_importance": r.get("business_importance"),
            "confidence": r.get("confidence"),
            "country_code": r.get("country_code"),
            "drivers": from_json(r.get("drivers"), []),
            "nike_product": product_card(prods.get(r.get("nike_product_id"))),
            "competitor_product": product_card(prods.get(r.get("competitor_product_id"))),
            "retailer": rets.get(r.get("retailer_id")),
            "recommendation": recs.get(int(r["id"])),
            "computed_at": r.get("computed_at"),
        })
    return out


def expand_retail_media(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    prods = products_by_id([r.get("nike_product_id") for r in rows]
                           + [r.get("competitor_product_id") for r in rows])
    rets = retailers_by_id([r.get("retailer_id") for r in rows])
    return [{
        "id": r["id"],
        "score": r.get("score"),
        "recommendation": r.get("recommendation"),
        "confidence": r.get("confidence"),
        "drivers": from_json(r.get("drivers"), []),
        "nike_product": product_card(prods.get(r.get("nike_product_id"))),
        "competitor_product": product_card(prods.get(r.get("competitor_product_id"))),
        "retailer": rets.get(r.get("retailer_id")),
        "country_code": r.get("country_code"),
        "computed_at": r.get("computed_at"),
    } for r in rows]
