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

from app import build_state
from app.api.serializers import (count, expand_opportunities, expand_retail_media,
                                 product_card, products_by_id, table_exists)
from app.auth import security_status
from app.db import query
from app.services.common import from_json

router = APIRouter(prefix="/api", tags=["overview"])

RISK_FAMILIES = ("pricing", "competitive_threat", "distribution")


def _resolve_entities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Traduce `entity_id` a un nombre legible.

    Las señales guardan el id crudo, así que sin esto la UI muestra "Marca #5".
    Para `franchise` el `entity_id` ya es el nombre.
    """
    brand_ids = [r["entity_id"] for r in rows if r.get("entity_type") == "brand"]
    prod_ids = [r["entity_id"] for r in rows if r.get("entity_type") == "product"]

    def _lookup(table: str, ids: list[Any], column: str) -> dict[str, str]:
        clean = [str(i) for i in ids if i is not None]
        if not clean or not table_exists(table):
            return {}
        placeholders = ",".join("?" * len(clean))
        return {
            str(r["id"]): r[column]
            for r in query(f"SELECT id, {column} FROM {table} WHERE id IN ({placeholders})",  # noqa: S608
                           clean)
        }

    brands = _lookup("brands", brand_ids, "name")
    products = _lookup("products", prod_ids, "product_name")

    out = []
    for r in rows:
        entity_id, entity_type = str(r.get("entity_id")), r.get("entity_type")
        if entity_type == "brand":
            label = brands.get(entity_id)
        elif entity_type == "product":
            label = products.get(entity_id)
        else:
            label = entity_id          # franchise: el id ya es el nombre
        out.append({**r, "entity_label": label or f"{entity_type} {entity_id}"})
    return out


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
        # Se ordena por `value` (el momentum 0..100, que ya combina volumen,
        # tendencia y aceleración según config) y NO por `acceleration`: ésta
        # queda NULL cuando no hay una tercera ventana de datos, y ordenar por
        # una columna vacía daba un orden arbitrario con "▲ 0.00" en pantalla.
        # `delta` tampoco sirve para ordenar: sobre bases chicas produce ratios
        # absurdos (se vieron +493.200%).
        # El panel es "momentum de COMPETIDORES": se excluyen las señales cuya
        # entidad es la marca foco o un producto suyo.
        rows = query(
            "SELECT * FROM market_signals ms "
            "WHERE ms.signal_type IN ('social_momentum','editorial_momentum','review_momentum') "
            "  AND NOT (ms.entity_type = 'brand' AND ms.entity_id IN "
            "           (SELECT CAST(id AS TEXT) FROM brands WHERE is_focus = 1)) "
            "  AND NOT (ms.entity_type = 'product' AND ms.entity_id IN "
            "           (SELECT CAST(p.id AS TEXT) FROM products p "
            "            JOIN brands b ON b.id = p.brand_id WHERE b.is_focus = 1)) "
            "ORDER BY ms.value DESC, ABS(COALESCE(ms.delta, 0)) DESC LIMIT ?", (limit,))
        momentum = _resolve_entities(rows)

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
        # `evidence` se guarda como JSON en texto. Hay que parsearlo igual que en
        # /api/brand/insights: si no, el cliente recibe un string y un insight con
        # evidencia real parece no tenerla.
        brand_highlights = [
            {**r, "evidence": from_json(r.get("evidence"), [])}
            for r in query(
                "SELECT bi.*, b.name AS brand FROM brand_insights bi "
                "LEFT JOIN brands b ON b.id = bi.brand_id "
                "WHERE bi.country_code = ? AND bi.confidence IN ('HIGH','MEDIUM') "
                "ORDER BY bi.signal_volume DESC LIMIT ?", (country, limit))
        ]

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


#: Sin estas tablas el motor no puede responder NADA útil: no hay productos que
#: comparar ni precios con los que compararlos. El resto de las tablas pueden
#: estar vacías legítimamente (no hay reviews, no se corrieron los collectors)
#: y eso no convierte al servicio en inservible.
CORE_TABLES = ("products", "price_observations")

#: Todo lo que se cuenta en el health check. El orden es el del pipeline.
HEALTH_TABLES = ("brands", "countries", "retailers", "products", "product_attributes",
                 "price_observations", "stock_observations", "reviews", "editorial_mentions",
                 "social_mention_aggregates", "competitive_matches", "competitive_match_factors",
                 "market_signals", "brand_insights", "opportunities", "recommendations",
                 "retail_media_opportunities")


@router.get("/health")
def health() -> dict[str, Any]:
    """Estado del motor: si la base tiene datos utilizables, y si no, por qué.

    Endpoint público: nunca pide API key (lo consulta la cinta de estado del
    dashboard para saber si el motor está vivo). Publica además cómo quedó
    configurada la seguridad — sin filtrar la key — para poder verificar de un
    vistazo que un deploy quedó cerrado.

    `status` responde la única pregunta que importa en un deploy: **¿este motor
    sirve para algo ahora mismo?** Antes devolvía `"ok"` fijo, así que un
    contenedor recién levantado con la base vacía informaba exactamente lo mismo
    que uno con 995 productos cargados, y `curl /api/health` no servía para
    verificar un deploy. Los cuatro estados posibles:

    * ``ok``        — hay datos; el motor responde consultas reales.
    * ``building``  — el arranque todavía está construyendo la base (ver
                      `app.build_state`). Hay que esperar, no hay nada roto.
    * ``degraded``  — hay productos pero el pipeline no llegó a producir su
                      salida (matches/oportunidades). Suele ser una etapa que
                      falló: las pantallas cargan pero salen a medias.
    * ``empty``     — no hay datos y nadie los está cargando. Acá sí hay un
                      problema de configuración: falta `DATABASE_URL` o la
                      ingesta falló.

    El HTTP sigue siendo 200 en los cuatro casos, a propósito: el proceso ESTÁ
    vivo y respondiendo. Un 503 haría que el health check de Render matara el
    contenedor justo mientras construye su base, que es el único momento en que
    con seguridad no hay que matarlo.
    """
    counts = {t: count(t) for t in HEALTH_TABLES}
    empty = [t for t, n in counts.items() if n == 0]
    has_core = all(counts.get(t, 0) > 0 for t in CORE_TABLES)
    has_output = counts.get("opportunities", 0) > 0 or counts.get("competitive_matches", 0) > 0

    build = build_state.read()
    building = bool(build and build.get("state") == build_state.BUILDING)

    # `building` gana sobre el contenido, y el orden importa: durante los ~26 s
    # del pipeline las tablas se van llenando de a una, así que a mitad de camino
    # ya hay `competitive_matches` pero todavía no hay `opportunities`. Con el
    # chequeo de contenido primero, el health check anunciaba `ok` mientras el
    # pipeline seguía corriendo — verificado en una corrida real: a los 32 s
    # decía `ok` con `opportunities: 0`. Quien mira el health para saber si el
    # deploy terminó se lo creía y abría el dashboard a medio llenar.
    if building:
        status = "building"
    elif has_core and has_output:
        status = "ok"
    elif has_core:
        status = "degraded"
    else:
        status = "empty"

    data: dict[str, Any] = {
        "status": status,
        # Resumen en una línea para el humano que corre `curl` y no quiere leer
        # 17 contadores para saber si el deploy anduvo.
        "products": counts.get("products", 0),
        "price_observations": counts.get("price_observations", 0),
        "opportunities": counts.get("opportunities", 0),
        "competitive_matches": counts.get("competitive_matches", 0),
        # De dónde SALDRÍA la base en este entorno. Explica al toque una base
        # con 45 productos en producción: dice `demo` ⇒ falta `DATABASE_URL`.
        "expected_source": build_state.source_hint(),
    }
    if build is not None:
        data["build"] = build

    return {
        "status": status,
        "data": data,
        "tables": counts,
        "empty_tables": empty,
        "security": security_status(),
    }
