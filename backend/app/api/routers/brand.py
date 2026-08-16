"""Argentina Consumer & Brand Intelligence.

Tres cosas que este router garantiza y que antes no estaban:

1. **Trazabilidad**. Cada ejemplo de evidencia sale con
   ``{text, type, source_name, url, date}``. Si no hay URL, sale ``url: null``
   con ``url_status`` + ``url_reason`` — nunca se omite la evidencia, porque el
   usuario tiene que poder distinguir "no hay link" de "no hay evidencia".
2. **Ventana de comparación configurable**. ``?window=month|quarter|year`` o
   ``?window_days=N&compare_days=M``. El default es el histórico (mes contra mes
   anterior) y se sirve desde lo persistido; cualquier otra ventana se recalcula
   en memoria (no escribe nada). Si el histórico no alcanza, la respuesta lo
   dice (``window.available = false`` + ``window.reason``).
3. **Momentum legible**. Cada fila trae ``entity_label`` (nombre real, no
   "Producto #17"), el tipo de señal en castellano y **la unidad de cada
   número**: ``value`` puede ser un score 0..100 o un %, ``delta`` puede ser un
   RATIO (0.62 = +62%) o puntos porcentuales. Mezclar esas unidades en una
   misma grilla sin decirlo era el motivo de que la tabla no se entendiera.

Nota de arquitectura: ``_entity_labels()`` replica el criterio de
``overview.py::_resolve_entities`` y le agrega ``retailer`` (que es justamente
el ``entity_type`` de ``share_of_shelf``, el que mostraba "Retailer #5").
**Conviene mover ese helper a ``api/serializers.py``** y que los dos routers lo
consuman: hoy está duplicado porque `overview.py` es de otro dueño.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.serializers import table_exists
from app.config import section
from app.db import query
from app.services.common import from_json

router = APIRouter(prefix="/api/brand", tags=["brand"])


# ── unidades ────────────────────────────────────────────────
#
# Vocabulario de unidades de la API (mismo espíritu que `serializers.SIGNAL_META`):
#   score_0_100 · score normalizado, NO un volumen
#   pct         · porcentaje 0..100
#   pp          · puntos porcentuales (variación de un %)
#   ratio       · variación relativa (0.62 = +62%)
#   count       · conteo absoluto, con su `volume_unit` (menciones/notas/reviews)

UNIT_LABELS: dict[str, str] = {
    "score_0_100": "score 0..100",
    "pct": "%",
    "pp": "puntos porcentuales",
    "ratio": "ratio (0,62 = +62%)",
    "count": "conteo absoluto",
}

#: Ficha de cada `signal_type` de `market_signals`: qué mide, en qué familia
#: vive y —sobre todo— en qué unidad está cada columna. `momentum` y `shelf`
#: NO son comparables entre sí: por eso la UI tiene que poder separarlas.
SIGNAL_META: dict[str, dict[str, Any]] = {
    "social_momentum": {
        "label": "Momentum social",
        "family": "momentum",
        "description": "Momentum de la conversación social agregada (volumen, "
                       "tendencia y aceleración combinados).",
        "value_unit": "score_0_100", "value_label": "Momentum score",
        "delta_unit": "ratio", "delta_label": "Variación de volumen vs período anterior",
        "acceleration_unit": "ratio",
        "volume_unit": "menciones",
    },
    "editorial_momentum": {
        "label": "Momentum editorial",
        "family": "momentum",
        "description": "Momentum de las menciones en medios y blogs especializados.",
        "value_unit": "score_0_100", "value_label": "Momentum score",
        "delta_unit": "ratio", "delta_label": "Variación de volumen vs período anterior",
        "acceleration_unit": "ratio",
        "volume_unit": "notas",
    },
    "review_momentum": {
        "label": "Momentum de reviews",
        "family": "momentum",
        "description": "Momentum del volumen de reseñas de consumidores.",
        "value_unit": "score_0_100", "value_label": "Momentum score",
        "delta_unit": "ratio", "delta_label": "Variación de volumen vs período anterior",
        "acceleration_unit": "ratio",
        "volume_unit": "reviews",
    },
    "share_of_shelf": {
        "label": "Share of shelf",
        "family": "shelf",
        "description": "Porcentaje de SKUs Nike sobre el surtido del retailer.",
        "value_unit": "pct", "value_label": "Share of shelf Nike",
        "delta_unit": "pp", "delta_label": "Variación vs la captura anterior",
        "acceleration_unit": "pp",
        "volume_unit": None,
    },
    "promo_intensity": {
        "label": "Intensidad promocional",
        "family": "shelf",
        "description": "Presión de descuentos observada en el canal.",
        "value_unit": "pct", "value_label": "Intensidad promocional",
        "delta_unit": "pp", "delta_label": "Variación vs la captura anterior",
        "acceleration_unit": "pp",
        "volume_unit": None,
    },
}

_UNKNOWN_SIGNAL: dict[str, Any] = {
    "label": None, "family": "other",
    "description": "Señal producida por otro módulo: la unidad no está declarada.",
    "value_unit": None, "value_label": "Valor",
    "delta_unit": None, "delta_label": "Variación",
    "acceleration_unit": None, "volume_unit": None,
}

ENTITY_TYPE_LABELS: dict[str, str] = {
    "brand": "Marca",
    "product": "Producto",
    "franchise": "Franquicia",
    "retailer": "Retailer",
    "topic": "Tópico",
}

#: Motivos por los que una fila no publica volumen absoluto.
_VOLUME_REASON_NO_COLUMN = (
    "`market_signals` no guarda el volumen absoluto (no existe la columna): se "
    "recalcula en memoria desde las observaciones del período.")
_VOLUME_REASON_NOT_APPLICABLE = (
    "La señal ya es un porcentaje de surtido, no un conteo: su volumen (SKUs) "
    "lo produce `services/shelf.py` y no viaja en `market_signals`.")
_VOLUME_REASON_UNAVAILABLE = (
    "No se pudo recalcular el volumen absoluto para esta fila (país distinto al "
    "configurado en `brand_intelligence.country`, o el recálculo no estuvo "
    "disponible).")

_DELTA_REASON_NO_BASE = (
    "Sin base de comparación medible en el período anterior "
    "(`brand_intelligence.momentum.min_base_volume`): una variación sobre una "
    "base minúscula es ruido amplificado, así que no se publica.")
_ACCEL_REASON_NO_THIRD = (
    "La aceleración es la derivada segunda: necesita tres ventanas con base "
    "suficiente y el histórico disponible no llega.")


def signal_meta(signal_type: str | None) -> dict[str, Any]:
    meta = SIGNAL_META.get(str(signal_type or ""))
    if meta:
        return meta
    return {**_UNKNOWN_SIGNAL,
            "label": str(signal_type or "").replace("_", " ").capitalize() or "Señal"}


# ── ventana de comparación ──────────────────────────────────


def _window_kwargs(window: str | None, window_days: int | None,
                   compare_days: int | None) -> dict[str, Any]:
    return {"window": window, "window_days": window_days, "compare_days": compare_days}


def _window_requested(window: str | None, window_days: int | None,
                      compare_days: int | None) -> bool:
    """¿Pidió el cliente una ventana distinta del default persistido?"""
    if window_days is not None or compare_days is not None:
        return True
    return bool(window) and window.strip().lower() != "month"


def _context(country: str, **window_kwargs: Any):
    """Contexto del servicio, o ``None`` si el módulo/DB no están disponibles.

    La API nunca escribe: recalcula en memoria y devuelve. Si el país pedido no
    es el que analiza el motor (``brand_intelligence.country``), no hay contexto
    aplicable y quien llama degrada.
    """
    try:
        from app.services import brand_intelligence as bi
    except ImportError:  # pragma: no cover - el servicio es parte del backend
        return None
    try:
        ctx = bi.build_context(**window_kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 - la API no se cae por el recálculo
        return None
    if country and ctx.country and country.upper() != str(ctx.country).upper():
        return None
    return ctx


def _window_block(country: str, window: str | None, window_days: int | None,
                  compare_days: int | None) -> dict[str, Any]:
    """Sobre de ventana barato: sólo consulta los bordes del histórico.

    No carga el análisis (eso es ``build_context``): para decir "esta ventana no
    tiene con qué compararse" alcanza con la primera y la última observación.
    """
    try:
        from app.services.brand_intelligence import window_report
    except ImportError:  # pragma: no cover
        return {"window": window or "month", "available": False,
                "reason": "El servicio de brand intelligence no está disponible."}
    try:
        return window_report(country=country, window=window, window_days=window_days,
                             compare_days=compare_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sources_block() -> dict[str, Any]:
    """Catálogo de fuentes + leyenda de motivos de "sin URL"."""
    try:
        from app.services.brand_intelligence import URL_REASONS, source_catalog
    except ImportError:  # pragma: no cover
        return {"sources": [], "url_reasons": {}}
    try:
        return {"sources": source_catalog(),
                "url_reasons": {k: v for k, v in URL_REASONS.items() if v}}
    except Exception:  # noqa: BLE001
        return {"sources": [], "url_reasons": {}}


# ── evidencia ───────────────────────────────────────────────

_EVIDENCE_DEFAULTS: dict[str, Any] = {
    "text": "", "type": None, "type_label": None, "source": None,
    "source_name": None, "source_label": None, "url": None, "url_available": False,
    "url_status": "unknown", "url_reason": None, "date": None, "date_field": None,
    "source_policy": None,
}

_LEGACY_URL_REASON = (
    "Evidencia guardada por una corrida anterior del pipeline, sin campos de "
    "trazabilidad. Volvé a correr `python -m app.pipeline` para recalcularla.")


def _normalize_example(raw: Any) -> dict[str, Any] | None:
    """Lleva un ejemplo de evidencia a la forma canónica, tolerando lo viejo."""
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    item = {**_EVIDENCE_DEFAULTS, **raw}
    item["text"] = str(item.get("text") or raw.get("excerpt") or raw.get("quote") or "").strip()
    url = item.get("url")
    item["url"] = str(url).strip() if url not in (None, "") else None
    item["url_available"] = item["url"] is not None
    if item["url_available"]:
        item["url_status"], item["url_reason"] = "ok", None
    elif item["url_status"] == "unknown" and not item["url_reason"]:
        item["url_reason"] = _LEGACY_URL_REASON
    if item.get("type") is None:
        item["type"] = item.get("source")
    if item.get("date") is None:
        item["date"] = raw.get("published_at") or raw.get("observed_at") or raw.get("period_end")
    if item.get("source_label") is None:
        item["source_label"] = item.get("source_name")
    return item


def _normalize_evidence(payload: Any) -> dict[str, Any]:
    """Sobre de evidencia canónico: ``{sources, examples, metrics, window, ...}``.

    Acepta las tres formas que existieron: el sobre nuevo, una lista suelta de
    ejemplos y el texto JSON crudo.
    """
    value = from_json(payload, []) if isinstance(payload, str) else payload
    envelope: dict[str, Any]
    if isinstance(value, dict):
        envelope = dict(value)
        examples = value.get("examples") or []
    elif isinstance(value, list):
        envelope, examples = {}, value
    else:
        envelope, examples = {}, []

    normalized = [e for e in (_normalize_example(x) for x in examples) if e and e["text"]]
    envelope["examples"] = normalized
    envelope.setdefault("sources", [])
    envelope["evidence_count"] = len(normalized)
    envelope["linked_count"] = sum(1 for e in normalized if e["url_available"])
    envelope["unlinked_count"] = len(normalized) - envelope["linked_count"]
    return envelope


# ── entidades ───────────────────────────────────────────────


def _entity_labels(rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """``{(entity_type, entity_id): nombre legible}`` en un query por tabla.

    Mismo criterio que ``overview.py::_resolve_entities`` **más `retailer`**:
    `share_of_shelf` es por retailer y sin esto la grilla mostraba "Retailer #5".
    Para `franchise`/`topic` el `entity_id` ya ES el nombre.
    """
    wanted: dict[str, set[str]] = {}
    for row in rows:
        entity_type, entity_id = row.get("entity_type"), row.get("entity_id")
        if entity_type in ("brand", "product", "retailer") and entity_id is not None:
            wanted.setdefault(str(entity_type), set()).add(str(entity_id))

    tables = {"brand": ("brands", "name"), "product": ("products", "product_name"),
              "retailer": ("retailers", "name")}
    out: dict[tuple[str, str], str] = {}
    for entity_type, ids in wanted.items():
        table, column = tables[entity_type]
        clean = sorted(i for i in ids if i)
        if not clean or not table_exists(table):
            continue
        placeholders = ",".join("?" * len(clean))
        for row in query(
            f"SELECT id, {column} AS label FROM {table} WHERE id IN ({placeholders})",  # noqa: S608
            clean,
        ):
            out[(entity_type, str(row["id"]))] = row["label"]
    return out


# ── endpoints ───────────────────────────────────────────────


@router.get("/insights")
def list_insights(
    country: str = "AR",
    dimension: str | None = None,
    brand: str | None = None,
    min_confidence: str | None = None,
    window: str | None = Query(None, description="month | quarter | year"),
    window_days: int | None = Query(None, ge=1, le=1825),
    compare_days: int | None = Query(None, ge=1, le=1825),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Insights de marca respaldados por señal cuantitativa + evidencia navegable.

    Cada `evidence.examples[i]` trae `{text, type, source_name, url, date}`; si
    `url` es `null` viene con `url_status` y `url_reason`.

    Con una ventana distinta del default los insights se **recalculan en
    memoria** (no se persiste nada). Si el histórico no alcanza para la ventana
    pedida, `window.available` es `false`, se explica el motivo y **no se
    devuelven insights**: un insight es una conclusión, y una conclusión contra
    una ventana vacía sería inventada.
    """
    empty = {"total": 0, "items": [],
             "taxonomy": section("brand_intelligence", "taxonomy", default={}),
             **_sources_block()}

    if _window_requested(window, window_days, compare_days):
        return _recomputed_insights(country, dimension, brand, min_confidence, limit,
                                    **_window_kwargs(window, window_days, compare_days))

    if not table_exists("brand_insights"):
        return {**empty, "window": _window_block(country, window, window_days, compare_days),
                "recomputed": False}

    where = ["bi.country_code = ?"]
    params: list[Any] = [country]
    if dimension:
        where.append("bi.dimension = ?")
        params.append(dimension)
    if brand:
        where.append("b.name = ?")
        params.append(brand)
    if min_confidence:
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        allowed = [k for k, v in order.items() if v >= order.get(min_confidence.upper(), 0)]
        where.append(f"bi.confidence IN ({','.join('?' * len(allowed))})")
        params.extend(allowed)

    clause = " WHERE " + " AND ".join(where)
    rows = query(
        f"SELECT bi.*, b.name AS brand FROM brand_insights bi "  # noqa: S608
        f"LEFT JOIN brands b ON b.id = bi.brand_id{clause} "
        f"ORDER BY bi.signal_volume DESC, bi.id LIMIT ?",
        [*params, limit],
    )

    items = [{**r, "evidence": _normalize_evidence(r.get("evidence"))} for r in rows]

    return {
        "total": len(items),
        "items": items,
        "taxonomy": section("brand_intelligence", "taxonomy", default={}),
        "window": _window_block(country, window, window_days, compare_days),
        "recomputed": False,
        **_sources_block(),
    }


def _recomputed_insights(country: str, dimension: str | None, brand: str | None,
                         min_confidence: str | None, limit: int,
                         **window_kwargs: Any) -> dict[str, Any]:
    """Insights para una ventana a medida. Calcula en memoria, no persiste."""
    from app.services import brand_intelligence as bi

    ctx = _context(country, **window_kwargs)
    base = {"taxonomy": section("brand_intelligence", "taxonomy", default={}),
            "recomputed": True, **_sources_block()}
    if ctx is None:
        return {"total": 0, "items": [],
                "window": _window_block(country, **window_kwargs), **base}

    info = ctx.window_info()
    if not info["available"]:
        # Nada de conclusiones sobre una ventana que los datos no sostienen.
        return {"total": 0, "items": [], "window": info, **base}

    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    floor = order.get((min_confidence or "").upper(), 0) if min_confidence else 0
    brand_ids = {bid for bid, row in ctx.brands.items()
                 if brand and str(row.get("name")) == brand}

    items: list[dict[str, Any]] = []
    for insight in bi.generate_insights(ctx):
        if dimension and insight.get("dimension") != dimension:
            continue
        if brand and insight.get("brand_id") not in brand_ids:
            continue
        if order.get(str(insight.get("confidence")), 0) < floor:
            continue
        items.append({
            **insight,
            "id": None,   # no persistido: esta lectura es a medida
            "brand": ctx.brand_name(insight["brand_id"]) if insight.get("brand_id") else None,
            "evidence": _normalize_evidence(insight.get("evidence")),
        })
        if len(items) >= limit:
            break

    return {"total": len(items), "items": items, "window": info, **base}


@router.get("/momentum")
def momentum(
    country: str = "AR",
    signal_type: str | None = Query(None, description="uno o varios, separados por coma"),
    signal_family: str | None = Query(None, description="momentum | shelf"),
    entity_type: str | None = None,
    window: str | None = Query(None, description="month | quarter | year"),
    window_days: int | None = Query(None, ge=1, le=1825),
    compare_days: int | None = Query(None, ge=1, le=1825),
    sort: str = Query("impact", description="impact | value | delta | volume | entity"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Brand / Conversation Momentum con nombres, unidades y filtros.

    Cada fila declara la unidad de cada número (`value_unit`, `delta_unit`,
    `acceleration_unit`) porque en esta tabla conviven dos familias que NO son
    comparables: `momentum` (score 0..100, delta en RATIO) y `shelf`
    (share en %, delta en puntos porcentuales). Filtrá por `signal_type` o
    `signal_family` para no mezclarlas.
    """
    window_kwargs = _window_kwargs(window, window_days, compare_days)
    custom = _window_requested(window, window_days, compare_days)

    if not table_exists("market_signals"):
        return {"total": 0, "items": [], "signal_types": [], "entity_types": [],
                "units": UNIT_LABELS, "recomputed": custom,
                "window": _window_block(country, window, window_days, compare_days)}

    wanted_types = [s.strip() for s in signal_type.split(",")] if signal_type else []
    wanted_types = [s for s in wanted_types if s]

    where = ["(country_code = ? OR country_code IS NULL)"]
    params: list[Any] = [country]
    if wanted_types:
        where.append(f"signal_type IN ({','.join('?' * len(wanted_types))})")
        params.extend(wanted_types)
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    clause = " WHERE " + " AND ".join(where)

    rows = query(f"SELECT * FROM market_signals{clause} LIMIT 5000", params)  # noqa: S608

    # Volumen absoluto y, si la ventana es a medida, los valores recalculados.
    ctx = _context(country, **window_kwargs)
    recomputed = _recomputed_momentum(ctx) if ctx is not None else {}
    window_block = (ctx.window_info() if ctx is not None
                    else _window_block(country, window, window_days, compare_days))

    if custom:
        # Las filas de momentum se reemplazan por las de la ventana pedida; las
        # de otras familias (share_of_shelf) las produce otro servicio con su
        # propio período y se marcan como NO afectadas por la ventana.
        momentum_types = set(_momentum_signal_types())
        rows = [r for r in rows if r.get("signal_type") not in momentum_types]
        for key, signal in sorted(recomputed.items()):
            if wanted_types and signal["signal_type"] not in wanted_types:
                continue
            if entity_type and signal["entity_type"] != entity_type:
                continue
            rows.append({"id": None, "computed_at": None,
                         **{k: signal.get(k) for k in
                            ("signal_type", "entity_type", "entity_id", "country_code",
                             "value", "delta", "acceleration", "period_start", "period_end")}})

    labels = _entity_labels(rows)
    items = [_momentum_row(r, labels, recomputed, window_block, custom) for r in rows]

    if signal_family:
        items = [i for i in items if i["signal_family"] == signal_family]

    items = _sort_momentum(items, sort)
    summary_types = _summarize(items, "signal_type")
    summary_entities = _summarize(items, "entity_type")

    return {
        "total": len(items),
        "items": items[:limit],
        "signal_types": summary_types,
        "entity_types": summary_entities,
        "units": UNIT_LABELS,
        "window": window_block,
        "recomputed": custom,
    }


def _momentum_signal_types() -> tuple[str, ...]:
    """Señales que produce `brand_intelligence` (las únicas que la ventana mueve)."""
    try:
        from app.services.brand_intelligence import SIGNAL_TYPES
        return tuple(SIGNAL_TYPES)
    except ImportError:  # pragma: no cover
        return ("social_momentum", "editorial_momentum", "review_momentum")


def _recomputed_momentum(ctx: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
    """``{(signal_type, entity_type, entity_id): señal}`` recalculada en memoria.

    Es de dónde sale el **volumen absoluto**: `market_signals` no tiene columna
    de volumen, así que la única forma honesta de publicarlo es recomputarlo
    desde las observaciones del período (ver el reporte: agregar
    `market_signals.volume` evitaría este recálculo por request).
    """
    try:
        from app.services.brand_intelligence import build_market_signals
        signals = build_market_signals(ctx)
    except Exception:  # noqa: BLE001 - el endpoint responde igual, sin volumen
        return {}
    return {(s["signal_type"], s["entity_type"], str(s["entity_id"])): s for s in signals}


def _momentum_row(row: dict[str, Any], labels: dict[tuple[str, str], str],
                  recomputed: dict[tuple[str, str, str], dict[str, Any]],
                  window_block: dict[str, Any], custom: bool) -> dict[str, Any]:
    """Fila legible: nombre resuelto, unidad por número y disponibilidad explícita."""
    signal = str(row.get("signal_type") or "")
    entity_type = str(row.get("entity_type") or "")
    entity_id = str(row.get("entity_id"))
    meta = signal_meta(signal)
    key = (signal, entity_type, entity_id)
    extra = recomputed.get(key, {})

    label = labels.get((entity_type, entity_id))
    if label is None:
        label = extra.get("entity_label")
    if label is None and entity_type in ("franchise", "topic"):
        label = entity_id   # el id ya es el nombre
    resolved = label is not None

    delta = row.get("delta")
    accel = row.get("acceleration")
    delta_unit = meta["delta_unit"]

    volume = extra.get("volume")
    if meta["family"] != "momentum":
        volume_reason = _VOLUME_REASON_NOT_APPLICABLE
    elif volume is None:
        volume_reason = _VOLUME_REASON_UNAVAILABLE
    else:
        volume_reason = None

    item: dict[str, Any] = {
        # ── columnas persistidas, sin tocar ──
        "id": row.get("id"),
        "signal_type": signal,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "country_code": row.get("country_code"),
        "value": row.get("value"),
        "delta": delta,
        "acceleration": accel,
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "computed_at": row.get("computed_at"),
        # ── legibilidad ──
        "signal_label": meta["label"],
        "signal_family": meta["family"],
        "signal_description": meta["description"],
        "entity_label": label if resolved else f"{ENTITY_TYPE_LABELS.get(entity_type, entity_type)} #{entity_id}",
        "entity_label_resolved": resolved,
        "entity_type_label": ENTITY_TYPE_LABELS.get(entity_type, entity_type),
        # ── unidades explícitas, una por número ──
        "value_unit": meta["value_unit"],
        "value_unit_label": UNIT_LABELS.get(meta["value_unit"] or "", None),
        "value_label": meta["value_label"],
        "delta_unit": delta_unit,
        "delta_unit_label": UNIT_LABELS.get(delta_unit or "", None),
        "delta_label": meta["delta_label"],
        "delta_available": delta is not None,
        "delta_reason": None if delta is not None else _DELTA_REASON_NO_BASE,
        # Mismo número en % para que la UI no tenga que saber la convención:
        # sólo existe cuando `delta` es un ratio.
        "delta_pct": (round(float(delta) * 100.0, 2)
                      if delta is not None and delta_unit == "ratio" else None),
        "acceleration_unit": meta["acceleration_unit"],
        "acceleration_unit_label": UNIT_LABELS.get(meta["acceleration_unit"] or "", None),
        "acceleration_available": accel is not None,
        "acceleration_reason": None if accel is not None else _ACCEL_REASON_NO_THIRD,
        "acceleration_pct": (round(float(accel) * 100.0, 2)
                             if accel is not None and meta["acceleration_unit"] == "ratio"
                             else None),
        # ── volumen absoluto (lo que la columna "VOLUMEN" debería mostrar) ──
        "volume": volume,
        "volume_previous": extra.get("volume_previous"),
        "volume_unit": extra.get("volume_unit") or meta["volume_unit"],
        "volume_available": volume is not None,
        "volume_reason": volume_reason,
        # ── contexto ──
        "direction": extra.get("direction"),
        "confidence": extra.get("confidence"),
        "coverage": extra.get("coverage"),
        "sources": extra.get("sources") or [],
        "window_applies": meta["family"] == "momentum",
        "window": window_block if meta["family"] == "momentum" else None,
    }
    if custom and meta["family"] != "momentum":
        item["window_reason"] = (
            "Esta señal la produce otro servicio (`services/shelf.py`) con su propio "
            "período de captura: la ventana pedida no la afecta.")
    if volume is None and meta["family"] == "momentum" and not extra:
        item["volume_reason"] = _VOLUME_REASON_NO_COLUMN
    return item


_SORT_KEYS: dict[str, Any] = {
    # Orden histórico: primero lo que más se mueve. Sigue mezclando familias,
    # por eso la UI debería filtrar por `signal_type` / `signal_family`.
    "impact": lambda i: (-abs(i["acceleration"] or 0.0), -(i["value"] or 0.0)),
    "value": lambda i: (-(i["value"] or 0.0),),
    "delta": lambda i: (-abs(i["delta"] or 0.0),),
    "volume": lambda i: (-(i["volume"] or 0.0),),
    "entity": lambda i: (str(i["entity_label"] or "").lower(),),
}


def _sort_momentum(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    keyfn = _SORT_KEYS.get(sort)
    if keyfn is None:
        raise HTTPException(status_code=400,
                            detail=f"sort desconocido: {sort!r}. Opciones: "
                                   f"{', '.join(sorted(_SORT_KEYS))}.")
    # El desempate por (signal_type, entity_id) mantiene la respuesta estable.
    return sorted(items, key=lambda i: (*keyfn(i), i["signal_type"], i["entity_id"]))


def _summarize(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Conteo por `signal_type` / `entity_type`, para que la UI arme los filtros."""
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item.get(field))] = counts.get(str(item.get(field)), 0) + 1
    out = []
    for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        entry: dict[str, Any] = {field: value, "count": count}
        if field == "signal_type":
            meta = signal_meta(value)
            entry.update({"label": meta["label"], "family": meta["family"],
                          "value_unit": meta["value_unit"], "delta_unit": meta["delta_unit"]})
        else:
            entry["label"] = ENTITY_TYPE_LABELS.get(value, value)
        out.append(entry)
    return out


@router.get("/topics")
def topics(
    country: str = "AR",
    topic: str | None = None,
    brand: str | None = None,
    window: str | None = Query(None, description="month | quarter | year"),
    window_days: int | None = Query(None, ge=1, le=1825),
    compare_days: int | None = Query(None, ge=1, le=1825),
    limit: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    """Tópicos de conversación con volumen, sentimiento y evidencia navegable.

    Sin `window` agrega TODO el histórico (comportamiento histórico del
    endpoint). Con `window` se recorta al período actual y se agrega
    `mentions_previous` + `delta` contra el período de comparación.
    """
    custom = _window_requested(window, window_days, compare_days)
    window_kwargs = _window_kwargs(window, window_days, compare_days)
    window_block = _window_block(country, window, window_days, compare_days)

    if not table_exists("social_mention_aggregates"):
        return {"total": 0, "items": [], "window": window_block, "recomputed": custom,
                **_sources_block()}

    # El contexto sólo hace falta para clasificar cada fila en actual/anterior.
    ctx = _context(country, **window_kwargs) if custom else None
    if custom and ctx is not None:
        window_block = ctx.window_info()

    params: list[Any] = [country]
    where = ["sma.country_code = ?", "sma.topic IS NOT NULL"]
    if topic:
        where.append("sma.topic = ?")
        params.append(topic)
    if brand:
        where.append("b.name = ?")
        params.append(brand)
    rows = query(
        "SELECT sma.topic, sma.intent, sma.source_type, sma.mention_count, "
        "       sma.sentiment_score, sma.period_start, sma.period_end, "
        "       sma.sample_evidence, b.name AS brand "
        "FROM social_mention_aggregates sma "
        "LEFT JOIN brands b ON b.id = sma.brand_id "
        f"WHERE {' AND '.join(where)}",  # noqa: S608
        params,
    )

    groups: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for row in rows:
        bucket = ctx.window_of(row.get("period_end")) if (custom and ctx is not None) else "current"
        if bucket not in ("current", "previous"):
            continue
        key = (row.get("topic"), row.get("intent"), row.get("brand"))
        group = groups.setdefault(key, {
            "topic": row.get("topic"), "intent": row.get("intent"), "brand": row.get("brand"),
            "mentions": 0.0, "mentions_previous": 0.0, "_sent": [], "_evidence": [],
            "period_start": None, "period_end": None, "source_types": set(),
        })
        mentions = float(row.get("mention_count") or 0.0)
        if bucket == "previous":
            group["mentions_previous"] += mentions
            continue
        group["mentions"] += mentions
        if row.get("sentiment_score") is not None:
            group["_sent"].append(float(row["sentiment_score"]))
        group["source_types"].add(row.get("source_type"))
        start, end = row.get("period_start"), row.get("period_end")
        if start and (group["period_start"] is None or start < group["period_start"]):
            group["period_start"] = start
        if end and (group["period_end"] is None or end > group["period_end"]):
            group["period_end"] = end
        group["_evidence"].append(row)

    items = [_topic_row(g, custom, window_block) for g in groups.values()]
    items.sort(key=lambda t: (-(t["mentions"] or 0), str(t["topic"]), str(t["brand"])))

    return {"total": len(items), "items": items[:limit], "window": window_block,
            "recomputed": custom, **_sources_block()}


def _topic_row(group: dict[str, Any], custom: bool,
               window_block: dict[str, Any]) -> dict[str, Any]:
    """Fila de tópico con evidencia trazable y unidades declaradas."""
    try:
        from app.services.brand_intelligence import social_evidence_examples
        examples = social_evidence_examples(
            sorted(group["_evidence"], key=lambda r: str(r.get("period_end") or ""),
                   reverse=True))
    except Exception:  # noqa: BLE001 - la fila sale igual, sin evidencia
        examples = []

    sentiments = group["_sent"]
    mentions = group["mentions"]
    previous = group["mentions_previous"]
    delta = ((mentions - previous) / previous) if custom and previous > 0 else None

    return {
        "topic": group["topic"],
        "intent": group["intent"],
        "brand": group["brand"],
        "mentions": int(round(mentions)),
        "mentions_unit": "menciones",
        "mentions_previous": int(round(previous)) if custom else None,
        "delta": round(delta, 4) if delta is not None else None,
        "delta_unit": "ratio",
        "delta_pct": round(delta * 100.0, 2) if delta is not None else None,
        "delta_available": delta is not None,
        "delta_reason": None if delta is not None else (
            "Sin ventana de comparación: pedí `?window=month|quarter|year` para "
            "obtener la variación." if not custom else
            "El período de comparación no tiene menciones de este tópico."),
        "sentiment": round(sum(sentiments) / len(sentiments), 3) if sentiments else None,
        "sentiment_unit": "score_-1_1",
        "source_types": sorted(s for s in group["source_types"] if s),
        "period_start": group["period_start"],
        "period_end": group["period_end"],
        "evidence": {
            "examples": examples,
            "evidence_count": len(examples),
            "linked_count": sum(1 for e in examples if e.get("url_available")),
            "unlinked_count": sum(1 for e in examples if not e.get("url_available")),
        },
        "window": window_block if custom else None,
    }


