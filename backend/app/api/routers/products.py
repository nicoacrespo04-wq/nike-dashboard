"""Product Explorer: catálogo enriquecido con filtros."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.serializers import PRODUCT_CARD_SQL, table_exists
from app.db import query

router = APIRouter(prefix="/api", tags=["products"])


@router.get("/products")
def list_products(
    brand: str | None = None,
    franchise: str | None = None,
    category: str | None = None,
    sport: str | None = None,
    use_case: str | None = None,
    gender: str | None = None,
    price_band: str | None = None,
    country: str | None = None,
    retailer: int | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if not table_exists("products"):
        return {"total": 0, "items": [], "limit": limit, "offset": offset}

    where: list[str] = []
    params: list[Any] = []

    for column, value in (
        ("b.name", brand), ("p.franchise", franchise), ("p.category", category),
        ("p.sport", sport), ("p.use_case", use_case), ("p.gender", gender),
        ("p.price_band", price_band), ("p.country_code", country),
    ):
        if value:
            where.append(f"{column} = ?")
            params.append(value)

    if q:
        where.append("(p.product_name LIKE ? OR p.franchise LIKE ? OR p.sku LIKE ? OR p.style_code LIKE ?)")
        params.extend([f"%{q}%"] * 4)

    if retailer is not None:
        where.append(
            "EXISTS (SELECT 1 FROM price_observations po "
            "WHERE po.product_id = p.id AND po.retailer_id = ?)"
        )
        params.append(retailer)

    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = query(
        f"SELECT COUNT(*) AS n FROM products p JOIN brands b ON b.id = p.brand_id{clause}",  # noqa: S608
        params,
    )[0]["n"]

    items = query(
        f"{PRODUCT_CARD_SQL}{clause} ORDER BY b.is_focus DESC, b.name, p.franchise, p.product_name "
        f"LIMIT ? OFFSET ?",  # noqa: S608
        [*params, limit, offset],
    )

    return {"total": int(total), "items": items, "limit": limit, "offset": offset}


@router.get("/products/filters")
def product_filters() -> dict[str, list[Any]]:
    """Valores disponibles para poblar los selects del Product Explorer."""
    if not table_exists("products"):
        return {k: [] for k in ("brands", "franchises", "categories", "sports",
                                "use_cases", "genders", "price_bands", "countries", "retailers")}

    def distinct(column: str, table: str = "products p JOIN brands b ON b.id = p.brand_id") -> list[Any]:
        rows = query(
            f"SELECT DISTINCT {column} AS v FROM {table} WHERE {column} IS NOT NULL "  # noqa: S608
            f"AND {column} != '' ORDER BY v"
        )
        return [r["v"] for r in rows]

    return {
        "brands":      distinct("b.name"),
        "franchises":  distinct("p.franchise"),
        "categories":  distinct("p.category"),
        "sports":      distinct("p.sport"),
        "use_cases":   distinct("p.use_case"),
        "genders":     distinct("p.gender"),
        "price_bands": distinct("p.price_band"),
        "countries":   distinct("p.country_code"),
        "retailers":   query("SELECT id, name, channel, importance FROM retailers ORDER BY importance DESC")
                       if table_exists("retailers") else [],
    }


@router.get("/products/{product_id}")
def get_product(product_id: int) -> dict[str, Any]:
    rows = query(f"{PRODUCT_CARD_SQL} WHERE p.id = ?", (product_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    product = rows[0]

    attributes: dict[str, list[dict[str, Any]]] = {}
    if table_exists("product_attributes"):
        for a in query(
            "SELECT attr_group, attr_name, value_text, value_num, confidence, source "
            "FROM product_attributes WHERE product_id = ? ORDER BY attr_group, attr_name",
            (product_id,),
        ):
            attributes.setdefault(a["attr_group"] or "other", []).append(a)

    prices = query(
        "SELECT po.*, r.name AS retailer_name, r.channel, r.importance "
        "FROM price_observations po JOIN retailers r ON r.id = po.retailer_id "
        "WHERE po.product_id = ? ORDER BY po.observed_at DESC, r.importance DESC",
        (product_id,),
    ) if table_exists("price_observations") else []

    stock = query(
        "SELECT so.*, r.name AS retailer_name "
        "FROM stock_observations so JOIN retailers r ON r.id = so.retailer_id "
        "WHERE so.product_id = ? ORDER BY so.observed_at DESC",
        (product_id,),
    ) if table_exists("stock_observations") else []

    reviews = query(
        "SELECT rating, review_count, review_text, source, observed_at "
        "FROM reviews WHERE product_id = ? ORDER BY observed_at DESC LIMIT 50",
        (product_id,),
    ) if table_exists("reviews") else []

    # Resumen de precio: último snapshot por retailer
    latest: dict[int, dict[str, Any]] = {}
    for p in prices:
        latest.setdefault(p["retailer_id"], p)
    current = [p["current_price"] for p in latest.values() if p.get("current_price") is not None]
    discounts = [p["discount_pct"] for p in latest.values() if p.get("discount_pct") is not None]
    ratings = [r["rating"] for r in reviews if r.get("rating") is not None]

    return {
        **product,
        "attributes": attributes,
        "prices": prices,
        "stock": stock,
        "reviews": reviews,
        "summary": {
            "retailers": len(latest),
            "min_price": min(current) if current else None,
            "max_price": max(current) if current else None,
            "avg_price": round(sum(current) / len(current), 2) if current else None,
            "avg_discount_pct": round(sum(discounts) / len(discounts), 2) if discounts else None,
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        },
    }
