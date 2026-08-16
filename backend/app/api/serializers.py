"""Helpers de serialización compartidos por los routers.

Los routers leen SQL directo (no dependen de los servicios) para que la API
siga respondiendo aunque una etapa del pipeline no haya corrido.

Acá vive además la **forma canónica de `drivers`** que publican todos los
endpoints de decisión (oportunidades, retail media y las recomendaciones):

    drivers: [{name, label, value, unit, contribution, detail}]
    signals: [{name, label, value, unit}]

`drivers` es explicabilidad ponderada: `value` siempre en 0..1 y `contribution`
en porcentaje del score, sumando 100 entre los factores con datos (los que no
tienen datos no se publican). `signals` es su hermano: las métricas observadas
en su unidad natural — stock %, gap de precio %, descuento %, share of shelf —
que el brief pide mostrar en la pantalla de retail media.

Por qué dos campos y no uno: media docena de esas métricas es la INVERSA del
factor que alimenta (`competitor_stock_gap` ↔ `competitor_stock_pct`,
`shelf_gap` ↔ `nike_shelf_share`) y el descuento no pondera ningún factor.
Mezclarlas dentro de `drivers` daría una lista con dos semánticas, unidades
distintas y contribuciones que no suman 100. Cada driver apunta a su métrica
con `detail.signal`, así el vínculo queda explícito sin romper la lista.

La normalización se hace acá, en el borde de la API, y no en los motores: la
columna `drivers` de cada tabla es formato de persistencia (y los tests de los
motores la fijan), mientras que el contrato público es este.
"""

from __future__ import annotations

from typing import Any

from app.db import query
from app.services.common import from_json

# ── contrato canónico de drivers ────────────────────────────

#: Unidad de los factores ponderados: todos viven en 0..1 (ver `common.combine`).
UNIT_SCORE_0_1 = "score_0_1"

#: Etiquetas en español de los factores conocidos (business importance, retail
#: media y match competitivo). Lo desconocido se humaniza a partir del nombre.
DRIVER_LABELS: dict[str, str] = {
    # business_importance
    "competitive_relevance": "Relevancia competitiva",
    "franchise_importance": "Peso de la franquicia",
    "revenue_proxy": "Proxy de facturación",
    "retailer_importance": "Importancia del retailer",
    "market_coverage": "Cobertura de mercado",
    "price_gap": "Gap de precio",
    "review_volume": "Volumen de reviews",
    "social_momentum": "Momentum social",
    "editorial_momentum": "Momentum editorial",
    "share_of_shelf": "Share of shelf",
    "promo_intensity": "Intensidad promocional",
    # retail_media
    "nike_stock_health": "Salud de stock Nike",
    "price_competitiveness": "Competitividad de precio",
    "business_importance": "Importancia de negocio",
    "competitor_momentum": "Momentum del competidor",
    "shelf_gap": "Brecha de share of shelf",
    "competitor_stock_gap": "Quiebre del competidor",
    # competitive_match
    "visual": "Visual",
    "semantic": "Semántico",
    "price": "Precio",
    "retailer_overlap": "Solape de retailers",
    "editorial": "Editorial",
    "social": "Social",
    "reviews": "Reviews",
}

#: Métrica observada que alimenta cada factor. Viaja en `detail.signal` para
#: que la UI pueda mostrar el driver y su número crudo sin adivinar.
DRIVER_SIGNAL: dict[str, str] = {
    "nike_stock_health": "nike_stock_pct",
    "price_competitiveness": "price_gap_pct",
    "competitive_relevance": "competitive_relevance",
    "business_importance": "business_importance",
    "competitor_momentum": "competitor_momentum",
    "shelf_gap": "nike_shelf_share",
    "competitor_stock_gap": "competitor_stock_pct",
}

#: Etiqueta + unidad de las métricas observadas. `pct` = 0..100, `ratio` = 0..1.
SIGNAL_META: dict[str, tuple[str, str]] = {
    "nike_stock_pct": ("Stock Nike", "pct"),
    "competitor_stock_pct": ("Stock del competidor", "pct"),
    "price_gap_pct": ("Gap de precio (>0 = Nike más caro)", "pct"),
    "nike_discount_pct": ("Descuento Nike", "pct"),
    "nike_shelf_share": ("Share of shelf Nike", "ratio"),
    "business_importance": ("Importancia de negocio", "score_0_100"),
    "competitive_relevance": ("Relevancia competitiva", "score_0_100"),
    "competitor_momentum": ("Momentum del competidor", UNIT_SCORE_0_1),
    "nike_momentum": ("Momentum Nike", UNIT_SCORE_0_1),
    "demand_signal": ("Señal de demanda", UNIT_SCORE_0_1),
    "match_score": ("Match score", "score_0_100"),
    "coverage": ("Cobertura de datos", "ratio"),
}

#: Claves del sobre que NO son métricas: son estructura o texto.
_ENVELOPE_STRUCTURAL = frozenset(
    {"factors", "rationale", "name", "label", "value", "unit", "contribution",
     "weight", "detail", "available", "raw_score", "signals"}
)


def driver_label(name: str) -> str:
    return DRIVER_LABELS.get(name) or name.replace("_", " ").capitalize()


def _signal_meta(name: str) -> tuple[str, str]:
    if name in SIGNAL_META:
        return SIGNAL_META[name]
    label = name.replace("_pct", "").replace("_", " ").capitalize()
    if name.endswith("_pct"):
        return label, "pct"
    if name.endswith("_share") or name.endswith("_ratio"):
        return label, "ratio"
    return name.replace("_", " ").capitalize(), "number"


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def canonical_driver(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Lleva un driver persistido a la forma canónica.

    Acepta las dos formas que hay en la base: la del motor de oportunidades
    (`{name, value, contribution, weight, detail}`) y la de un factor crudo de
    `CompositeScore` (`{factor, raw_score, weight, contribution, available}`).
    Un factor sin datos devuelve ``None``: no se publica.
    """
    name = entry.get("name") or entry.get("factor")
    if not isinstance(name, str) or not name.strip():
        return None
    if entry.get("available") is False:
        return None

    value = _as_number(entry.get("value") if "value" in entry else entry.get("raw_score"))
    if value is None:
        return None

    detail = dict(entry.get("detail") or {})
    weight = _as_number(entry.get("weight"))
    if weight is not None:
        detail.setdefault("weight", weight)
    signal = DRIVER_SIGNAL.get(name)
    if signal:
        detail.setdefault("signal", signal)

    return {
        "name": name,
        "label": driver_label(name),
        "value": round(value, 4),
        "unit": UNIT_SCORE_0_1,
        "contribution": _as_number(entry.get("contribution")),
        "detail": detail,
    }


def canonical_signal(name: str, value: Any) -> dict[str, Any] | None:
    number = _as_number(value)
    if number is None:
        return None
    label, unit = _signal_meta(name)
    return {"name": name, "label": label, "value": number, "unit": unit}


def canonical_drivers(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Normaliza cualquier `drivers` persistido a ``(drivers, signals, rationale)``.

    Tolera las tres formas que existieron: lista canónica, lista de drivers del
    motor de oportunidades y el sobre de retail media
    (``[{rationale, nike_stock_pct, …, factors:[…]}]``). Del sobre saca los
    factores como drivers, las métricas numéricas como `signals` y el racional
    aparte — nada se pierde y nada queda mezclado.
    """
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return [], [], None

    drivers: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    seen_signals: set[str] = set()
    rationale: str | None = None

    for entry in payload:
        if not isinstance(entry, dict):
            continue

        nested = entry.get("factors")
        if isinstance(nested, list):
            # Sobre de retail media: factores adentro, métricas crudas afuera.
            for factor in nested:
                if isinstance(factor, dict):
                    driver = canonical_driver(factor)
                    if driver:
                        drivers.append(driver)
            text = entry.get("rationale")
            if rationale is None and isinstance(text, str) and text.strip():
                rationale = text.strip()
            for key, value in entry.items():
                if key in _ENVELOPE_STRUCTURAL or key in seen_signals:
                    continue
                signal = canonical_signal(key, value)
                if signal:
                    signals.append(signal)
                    seen_signals.add(key)
            continue

        driver = canonical_driver(entry)
        if driver:
            drivers.append(driver)

    drivers.sort(key=lambda d: d["contribution"] if d["contribution"] is not None else -1.0,
                 reverse=True)
    return drivers, signals, rationale

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
            rec_drivers, rec_signals, _ = canonical_drivers(from_json(rec.get("drivers"), []))
            recs[int(rec["opportunity_id"])] = {
                "action": rec.get("action"),
                "rationale": rec.get("rationale"),
                "score": rec.get("score"),
                "confidence": rec.get("confidence"),
                "drivers": rec_drivers,
                "signals": rec_signals,
            }

    out = []
    for r in rows:
        drivers, signals, _ = canonical_drivers(from_json(r.get("drivers"), []))
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
            "drivers": drivers,
            "signals": signals,
            "nike_product": product_card(prods.get(r.get("nike_product_id"))),
            "competitor_product": product_card(prods.get(r.get("competitor_product_id"))),
            "retailer": rets.get(r.get("retailer_id")),
            "recommendation": recs.get(int(r["id"])),
            "computed_at": r.get("computed_at"),
        })
    return _with_triage(out)


def _with_triage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adjunta `entity_key` y el estado de triaje a cada oportunidad.

    `opportunities.id` se regenera en cada corrida, así que la UI necesita la
    clave estable para poder descartar o posponer algo que sobreviva al
    recálculo. Tolerante: si el módulo de triaje no está, las tarjetas salen
    igual y la UI muestra los controles deshabilitados.
    """
    try:
        from app.services import triage
    except ImportError:
        return items
    try:
        return triage.apply_to(items)
    except Exception:  # noqa: BLE001 - el triaje nunca puede tumbar el listado
        return items


def expand_retail_media(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mismo contrato de `drivers`/`signals` que las oportunidades.

    El racional en prosa del motor viaja en `rationale` (el equivalente a
    `recommendation.rationale` de una oportunidad), no adentro de `drivers`.
    """
    if not rows:
        return []
    prods = products_by_id([r.get("nike_product_id") for r in rows]
                           + [r.get("competitor_product_id") for r in rows])
    rets = retailers_by_id([r.get("retailer_id") for r in rows])

    out: list[dict[str, Any]] = []
    for r in rows:
        drivers, signals, rationale = canonical_drivers(from_json(r.get("drivers"), []))
        out.append({
            "id": r["id"],
            "score": r.get("score"),
            "recommendation": r.get("recommendation"),
            "rationale": rationale,
            "confidence": r.get("confidence"),
            "drivers": drivers,
            "signals": signals,
            "nike_product": product_card(prods.get(r.get("nike_product_id"))),
            "competitor_product": product_card(prods.get(r.get("competitor_product_id"))),
            "retailer": rets.get(r.get("retailer_id")),
            "country_code": r.get("country_code"),
            "computed_at": r.get("computed_at"),
        })
    return out
