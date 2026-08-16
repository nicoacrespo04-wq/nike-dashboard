"""Historial temporal del motor de decisión.

El pipeline **borra y recalcula** en cada corrida: los `id` de `opportunities`
y `competitive_matches` son efímeros y no sirven para seguir una entidad en el
tiempo. Este módulo agrega la dimensión que faltaba — *desde cuándo*, *viene
subiendo o bajando* — sin tocar ningún servicio existente.

Cómo funciona
-------------
1. `start_run()` abre una fila en `pipeline_runs` antes de que corra nada.
2. El pipeline hace lo suyo (esto no se entera).
3. `snapshot(run_id)` corre DESPUÉS y **sólo lee** lo que quedó persistido
   (`competitive_matches`, `opportunities`, `market_signals`), copiándolo a las
   tablas de historial con una identidad estable: `entity_key`.
4. `finish_run(run_id, status=..., counts=...)` cierra la corrida.

Que el snapshot sea un paso posterior y de sólo lectura es deliberado: ningún
servicio de scoring cambia, y una etapa rota no corrompe el historial — a lo
sumo esa corrida queda con menos filas.

`entity_key` — el contrato
--------------------------
Hash corto y determinístico sobre los campos NORMALIZADOS que identifican de qué
se está hablando. Idéntico entre corridas e independiente de los ids
autoincrementales::

    entity_key = sha1("kind|campo=valor|…")[:16]    # valores normalizados

    oportunidad -> (opportunity_type, nike_product_id, competitor_product_id,
                    retailer_id, country_code)
    match       -> (nike_product_id, competitor_product_id)
    señal       -> (signal_type, entity_type, entity_id, country_code)

Normalización: ``None`` -> ``""``; los números a entero en texto (``3`` ==
``"3"`` == ``3.0``); el texto recortado, sin espacios repetidos y en minúsculas
(``"AR"`` == ``"ar"``).

Es la clave que comparten el historial y el triaje (`opportunity_triage`), por
eso vive acá y se calcula siempre con esta función — nunca a mano. El módulo de
triaje trae su propia copia de la fórmula como fallback (por si este archivo no
existe todavía): las dos tienen que dar el MISMO hash, y hay un test que lo fija
en los dos lados.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH, section
from app.db import get_conn, query
from app.services.common import from_json, to_json

# ── contrato de identidad ───────────────────────────────────

#: Campos canónicos por tipo de entidad. El ORDEN importa: es parte del hash.
ENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "opportunity": ("opportunity_type", "nike_product_id", "competitor_product_id",
                    "retailer_id", "country_code"),
    "match": ("nike_product_id", "competitor_product_id"),
    "signal": ("signal_type", "entity_type", "entity_id", "country_code"),
}

#: Largo del hash publicado (hex). 16 => 64 bits, de sobra para este dominio.
KEY_LENGTH = 16

#: Cuánto tiene que moverse un score (puntos 0..100) para no ser "stable".
TREND_EPSILON = 1.0

_INT_RE = re.compile(r"^-?\d+$")

# Tablas de historial que deben SOBREVIVIR al reset del pipeline.
# `opportunity_triage` se incluye a propósito: es el estado humano (asignado,
# pospuesto, descartado) y perderlo en cada recálculo lo haría inútil.
CARRIED_TABLES: tuple[str, ...] = (
    "pipeline_runs",
    "match_history",
    "opportunity_history",
    "signal_history",
    "opportunity_triage",
)

_MATCH_COLUMNS = ("run_id", "entity_key", "nike_product_id", "competitor_product_id",
                  "match_score", "raw_match_score", "coverage", "confidence", "observed_at")

_OPP_COLUMNS = ("run_id", "entity_key", "opportunity_type", "family", "nike_product_id",
                "competitor_product_id", "retailer_id", "country_code",
                "business_importance", "severity", "confidence", "observed_at")

_SIGNAL_COLUMNS = ("run_id", "signal_type", "entity_type", "entity_id",
                   "value", "delta", "observed_at")


def _normalize_part(value: Any) -> str:
    """Normaliza un componente del hash.

    ``None`` -> ``""``; ``12``, ``12.0`` y ``"12"`` -> ``"12"``; el texto se
    recorta, se colapsan espacios y se pasa a minúsculas (``"AR"`` == ``"ar"``).

    `app.services.triage` tiene una copia exacta de esta función (necesita poder
    calcular la clave aunque este módulo no exista). Si las dos se separan, las
    dos mitades del sistema dejan de hablar de la misma oportunidad y el triaje
    se pierde en cada corrida: hay tests de los dos lados que lo fijan.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.6f}"
    text = " ".join(str(value).split()).lower()
    if _INT_RE.match(text):
        return str(int(text))
    return text


def entity_key(kind: Any, **parts: Any) -> str:
    """Identidad estable de una entidad recalculable.

    Para los tipos conocidos (``opportunity``, ``match``, ``signal``) se usan
    SÓLO los campos de :data:`ENTITY_FIELDS`, en ese orden: los que falten valen
    ``None`` y los extra se ignoran. Eso permite pasar una fila entera de la DB
    (``entity_key("opportunity", **row)``) y obtener siempre la misma clave, sin
    importar qué otras columnas traiga. Para un ``kind`` desconocido se usan
    todos los ``parts`` ordenados alfabéticamente.

    El hash es ``sha1("kind|campo=valor|…")[:16]`` sobre los valores
    normalizados en el orden canónico. Incluir el `kind` y el nombre del campo
    evita que dos entidades distintas con los mismos números colisionen.

    Como atajo, ``kind`` puede ser un Mapping: se interpreta como una fila de
    oportunidad (``entity_key(row)``).

    >>> entity_key("match", nike_product_id=1, competitor_product_id=16) == \\
    ...     entity_key("match", competitor_product_id="16", nike_product_id="1")
    True
    """
    if isinstance(kind, Mapping):
        kind, parts = "opportunity", {**kind, **parts}
    fields: Sequence[str] = ENTITY_FIELDS.get(kind) or tuple(sorted(parts))
    payload = "|".join([kind] + [f"{f}={_normalize_part(parts.get(f))}" for f in fields])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:KEY_LENGTH]  # noqa: S324 - no es criptográfico


def opportunity_key(row: dict[str, Any]) -> str:
    """`entity_key` de una fila de `opportunities` (o de cualquier dict equivalente)."""
    return entity_key("opportunity", **row)


def match_key(row: dict[str, Any]) -> str:
    """`entity_key` de una fila de `competitive_matches`."""
    return entity_key("match", **row)


def signal_key(row: dict[str, Any]) -> str:
    """`entity_key` de una fila de `market_signals`."""
    return entity_key("signal", **row)


# ── utilidades internas ─────────────────────────────────────


def _now() -> str:
    """Timestamp UTC con el mismo formato que ``datetime('now')`` de SQLite."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _table_exists(name: str, db_path: Path | str = DB_PATH) -> bool:
    rows = query("SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                 (name,), path=db_path)
    return bool(rows)


def _rows(sql: str, params: Iterable[Any] = (), db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    try:
        return query(sql, params, path=db_path)
    except Exception:  # noqa: BLE001 - tabla ausente o DB a medio construir
        return []


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _delta(current: Any, previous: Any) -> float | None:
    a, b = _num(current), _num(previous)
    return round(a - b, 4) if a is not None and b is not None else None


def classify_trend(values: Sequence[float | None], epsilon: float = TREND_EPSILON) -> str:
    """`new` (un solo punto) | `rising` | `falling` | `stable`."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return "new"
    change = clean[-1] - clean[0]
    if abs(change) < epsilon:
        return "stable"
    return "rising" if change > 0 else "falling"


def _with_deltas(points: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Agrega ``delta`` (vs punto anterior) a una serie ya ordenada."""
    previous: Any = None
    for point in points:
        point["delta"] = _delta(point.get(field), previous)
        previous = point.get(field)
    return points


# ── corridas ────────────────────────────────────────────────


def start_run(db_path: Path | str = DB_PATH, *, source: str) -> int:
    """Abre una corrida en `pipeline_runs` y devuelve su id."""
    try:
        config_version = str(section("version", default="") or "")
    except Exception:  # noqa: BLE001 - config rota no debe frenar el pipeline
        config_version = ""

    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO pipeline_runs (started_at, status, config_version, source) "
            "VALUES (?,?,?,?)",
            (_now(), "running", config_version, source),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, db_path: Path | str = DB_PATH, *,
               status: str, counts: dict[str, Any] | None = None) -> None:
    """Cierra la corrida con su estado final y el reporte por etapa."""
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE pipeline_runs SET finished_at = ?, status = ?, counts = ? WHERE id = ?",
            (_now(), status, to_json(counts or {}), int(run_id)),
        )


def run_status_from_report(report: dict[str, dict[str, Any]]) -> str:
    """`ok` | `partial` | `error` a partir del reporte del pipeline."""
    statuses = [str(info.get("status")) for info in report.values() if isinstance(info, dict)]
    if not statuses:
        return "error"
    failed = [s for s in statuses if s not in ("ok", "skipped")]
    if not failed:
        return "ok"
    return "error" if not any(s == "ok" for s in statuses) else "partial"


def list_runs(db_path: Path | str = DB_PATH, *, limit: int = 50) -> list[dict[str, Any]]:
    """Corridas de la más nueva a la más vieja, con sus conteos parseados."""
    rows = _rows("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (int(limit),), db_path)
    if not rows:
        return []

    ids = [int(r["id"]) for r in rows]
    marks = ",".join("?" * len(ids))
    snapshots: dict[int, dict[str, int]] = {i: {"matches": 0, "opportunities": 0, "signals": 0}
                                            for i in ids}
    for table, label in (("match_history", "matches"),
                         ("opportunity_history", "opportunities"),
                         ("signal_history", "signals")):
        for row in _rows(f"SELECT run_id, COUNT(*) AS n FROM {table} "  # noqa: S608 - tabla fija interna
                         f"WHERE run_id IN ({marks}) GROUP BY run_id", ids, db_path):
            snapshots[int(row["run_id"])][label] = int(row["n"])

    out = []
    for row in rows:
        out.append({
            "id": int(row["id"]),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "status": row.get("status"),
            "source": row.get("source"),
            "config_version": row.get("config_version"),
            "counts": from_json(row.get("counts"), {}),
            "snapshot": snapshots.get(int(row["id"]), {}),
        })
    return out


def snapshot_runs(db_path: Path | str = DB_PATH) -> list[int]:
    """Corridas que dejaron historial, de la más vieja a la más nueva.

    Se mira el UNION de las tres tablas y no sólo `opportunity_history`: una
    corrida en la que una oportunidad dejó de aparecer igual tiene matches y
    señales, y tiene que contar para cortar la racha de esa oportunidad.
    """
    rows = _rows("SELECT run_id FROM match_history "
                 "UNION SELECT run_id FROM opportunity_history "
                 "UNION SELECT run_id FROM signal_history ORDER BY run_id", (), db_path)
    return [int(r["run_id"]) for r in rows]


def latest_run_id(db_path: Path | str = DB_PATH) -> int | None:
    runs = snapshot_runs(db_path)
    return runs[-1] if runs else None


# ── snapshot ────────────────────────────────────────────────


def snapshot(run_id: int, db_path: Path | str = DB_PATH) -> dict[str, int]:
    """Congela el estado actual en las tablas de historial. Idempotente por `run_id`.

    Sólo LEE de `competitive_matches`, `opportunities` y `market_signals`: no
    modifica ninguna tabla de los servicios de scoring. Volver a llamarla con el
    mismo `run_id` reemplaza las filas de esa corrida (no las duplica).
    """
    run_id = int(run_id)
    runs = _rows("SELECT started_at FROM pipeline_runs WHERE id = ?", (run_id,), db_path)
    observed_at = (runs[0].get("started_at") if runs else None) or _now()

    if not runs:
        # Alguna etapa se llevó puesta la fila de la corrida (hay servicios que
        # hacen `init_db(drop=True)`). Se recrea con el mismo id para no perder
        # el snapshot ni romper las FKs del historial.
        with get_conn(db_path) as conn:
            conn.execute("INSERT INTO pipeline_runs (id, started_at, status, source) "
                         "VALUES (?,?,?,?)", (run_id, observed_at, "running", "recovered"))

    matches = _rows(
        "SELECT nike_product_id, competitor_product_id, match_score, raw_match_score, "
        "coverage, confidence FROM competitive_matches "
        "ORDER BY nike_product_id, competitor_product_id", (), db_path)

    opportunities = _rows(
        "SELECT opportunity_type, family, nike_product_id, competitor_product_id, "
        "retailer_id, country_code, business_importance, severity, confidence "
        "FROM opportunities ORDER BY id", (), db_path)

    signals = _rows(
        "SELECT signal_type, entity_type, entity_id, value, delta FROM market_signals "
        "ORDER BY signal_type, entity_type, entity_id", (), db_path)

    match_rows = [(run_id, match_key(m), m.get("nike_product_id"), m.get("competitor_product_id"),
                   m.get("match_score"), m.get("raw_match_score"), m.get("coverage"),
                   m.get("confidence"), observed_at) for m in matches]

    opp_rows = [(run_id, opportunity_key(o), o.get("opportunity_type"), o.get("family"),
                 o.get("nike_product_id"), o.get("competitor_product_id"), o.get("retailer_id"),
                 o.get("country_code"), o.get("business_importance"), o.get("severity"),
                 o.get("confidence"), observed_at) for o in opportunities]

    signal_rows = [(run_id, s.get("signal_type"), s.get("entity_type"), s.get("entity_id"),
                    s.get("value"), s.get("delta"), observed_at) for s in signals]

    with get_conn(db_path) as conn:
        for table in ("match_history", "opportunity_history", "signal_history"):
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))  # noqa: S608
        conn.executemany(
            f"INSERT INTO match_history ({','.join(_MATCH_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_MATCH_COLUMNS))})", match_rows)
        conn.executemany(
            f"INSERT INTO opportunity_history ({','.join(_OPP_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_OPP_COLUMNS))})", opp_rows)
        conn.executemany(
            f"INSERT INTO signal_history ({','.join(_SIGNAL_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_SIGNAL_COLUMNS))})", signal_rows)

    return {"matches": len(match_rows), "opportunities": len(opp_rows),
            "signals": len(signal_rows)}


# ── preservación entre resets ───────────────────────────────


def capture(db_path: Path | str = DB_PATH) -> dict[str, list[dict[str, Any]]]:
    """Lee las tablas que deben sobrevivir a ``init_db(drop=True)``.

    El pipeline borra el archivo entero en cada corrida; sin esto el historial
    duraría exactamente una corrida. Tolerante: si la DB no existe devuelve {}.
    """
    if not Path(db_path).exists():
        return {}
    data: dict[str, list[dict[str, Any]]] = {}
    for table in CARRIED_TABLES:
        if not _table_exists(table, db_path):
            continue
        rows = _rows(f"SELECT * FROM {table} ORDER BY id", (), db_path)  # noqa: S608
        if rows:
            data[table] = rows
    return data


def restore(data: dict[str, list[dict[str, Any]]], db_path: Path | str = DB_PATH) -> dict[str, int]:
    """Reinserta lo capturado por :func:`capture`, conservando los `id` originales.

    Conservar los ids es lo que mantiene válidas las FKs
    ``*_history.run_id -> pipeline_runs.id``. El orden de `CARRIED_TABLES` pone
    `pipeline_runs` primero por eso mismo.
    """
    if not data:
        return {}
    restored: dict[str, int] = {}
    with get_conn(db_path) as conn:
        for table in CARRIED_TABLES:
            rows = data.get(table) or []
            if not rows:
                continue
            columns = list(rows[0])
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) "  # noqa: S608
                f"VALUES ({','.join('?' * len(columns))})",
                [tuple(r.get(c) for c in columns) for r in rows])
            restored[table] = len(rows)
    return restored


# ── series ──────────────────────────────────────────────────


def match_trend(entity_key: str, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Serie temporal de un match, del más viejo al más nuevo."""
    rows = _rows(
        "SELECT run_id, observed_at, nike_product_id, competitor_product_id, match_score, "
        "raw_match_score, coverage, confidence FROM match_history WHERE entity_key = ? "
        "ORDER BY observed_at, run_id", (entity_key,), db_path)
    points = [{
        "run_id": int(r["run_id"]),
        "observed_at": r.get("observed_at"),
        "nike_product_id": r.get("nike_product_id"),
        "competitor_product_id": r.get("competitor_product_id"),
        "match_score": r.get("match_score"),
        "raw_match_score": r.get("raw_match_score"),
        "coverage": r.get("coverage"),
        "confidence": r.get("confidence"),
    } for r in rows]
    return _with_deltas(points, "match_score")


def opportunity_trend(entity_key: str, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Serie temporal de una oportunidad, del más viejo al más nuevo."""
    rows = _rows(
        "SELECT run_id, observed_at, opportunity_type, family, nike_product_id, "
        "competitor_product_id, retailer_id, country_code, business_importance, severity, "
        "confidence FROM opportunity_history WHERE entity_key = ? "
        "ORDER BY observed_at, run_id", (entity_key,), db_path)
    points = [{
        "run_id": int(r["run_id"]),
        "observed_at": r.get("observed_at"),
        "opportunity_type": r.get("opportunity_type"),
        "family": r.get("family"),
        "nike_product_id": r.get("nike_product_id"),
        "competitor_product_id": r.get("competitor_product_id"),
        "retailer_id": r.get("retailer_id"),
        "country_code": r.get("country_code"),
        "business_importance": r.get("business_importance"),
        "severity": r.get("severity"),
        "confidence": r.get("confidence"),
    } for r in rows]
    return _with_deltas(points, "business_importance")


def signal_trend(signal_type: str, entity_type: str, entity_id: Any,
                 db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Serie temporal de una señal de mercado, del más viejo al más nuevo."""
    rows = _rows(
        "SELECT run_id, observed_at, signal_type, entity_type, entity_id, value, delta "
        "FROM signal_history WHERE signal_type = ? AND entity_type = ? AND entity_id = ? "
        "ORDER BY observed_at, run_id",
        (signal_type, entity_type, str(entity_id)), db_path)
    points = [{
        "run_id": int(r["run_id"]),
        "observed_at": r.get("observed_at"),
        "signal_type": r.get("signal_type"),
        "entity_type": r.get("entity_type"),
        "entity_id": r.get("entity_id"),
        "value": r.get("value"),
        # `delta` tal como lo calculó el módulo de origen (ventana interna).
        "reported_delta": r.get("delta"),
    } for r in rows]
    # `delta` acá = variación entre corridas, que es lo que el historial aporta.
    return _with_deltas(points, "value")


# ── antigüedad de oportunidades ─────────────────────────────


def opportunity_age(db_path: Path | str = DB_PATH, *, only_open: bool = True,
                    epsilon: float = TREND_EPSILON) -> dict[str, dict[str, Any]]:
    """Antigüedad y tendencia de cada oportunidad, indexadas por `entity_key`.

    ``runs_open`` es la racha de corridas consecutivas (terminando en la última)
    en las que la oportunidad siguió apareciendo: es la respuesta a "¿esto lleva
    3 semanas abierto?". Con ``only_open=False`` incluye también las que ya se
    cerraron (dejaron de calcularse).
    """
    rows = _rows(
        "SELECT run_id, entity_key, observed_at, opportunity_type, family, nike_product_id, "
        "competitor_product_id, retailer_id, country_code, business_importance, severity, "
        "confidence FROM opportunity_history ORDER BY observed_at, run_id", (), db_path)
    if not rows:
        return {}

    run_order = snapshot_runs(db_path) or sorted({int(r["run_id"]) for r in rows})
    latest_run = run_order[-1]
    position = {run_id: i for i, run_id in enumerate(run_order)}

    triage: dict[str, dict[str, Any]] = {}
    if _table_exists("opportunity_triage", db_path):
        for t in _rows("SELECT entity_key, state, assignee, snooze_until FROM opportunity_triage",
                       (), db_path):
            triage[str(t["entity_key"])] = t

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["entity_key"]), []).append(row)

    out: dict[str, dict[str, Any]] = {}
    for key, points in grouped.items():
        runs_seen = sorted({int(p["run_id"]) for p in points})
        is_open = runs_seen[-1] == latest_run
        if only_open and not is_open:
            continue

        # Racha: cuántas corridas consecutivas (hacia atrás) siguió apareciendo.
        streak = 1
        while (len(runs_seen) > streak
               and position[runs_seen[-streak - 1]] == position[runs_seen[-streak]] - 1):
            streak += 1

        first, last = points[0], points[-1]
        values = [_num(p.get("business_importance")) for p in points]
        info: dict[str, Any] = {
            "entity_key": key,
            "first_seen": first.get("observed_at"),
            "last_seen": last.get("observed_at"),
            "first_run_id": int(first["run_id"]),
            "last_run_id": int(last["run_id"]),
            "runs_open": streak,
            "runs_seen": len(runs_seen),
            "is_open": is_open,
            "trend": classify_trend(values, epsilon),
            "first_importance": first.get("business_importance"),
            "business_importance": last.get("business_importance"),
            "importance_delta": _delta(last.get("business_importance"),
                                       first.get("business_importance")),
            "opportunity_type": last.get("opportunity_type"),
            "family": last.get("family"),
            "severity": last.get("severity"),
            "confidence": last.get("confidence"),
            "nike_product_id": last.get("nike_product_id"),
            "competitor_product_id": last.get("competitor_product_id"),
            "retailer_id": last.get("retailer_id"),
            "country_code": last.get("country_code"),
        }
        state = triage.get(key)
        if state:
            info["triage"] = {"state": state.get("state"), "assignee": state.get("assignee"),
                              "snooze_until": state.get("snooze_until")}
        out[key] = info
    return out


# ── aceleración derivada del historial (BONUS) ──────────────


def signal_acceleration(signal_type: str, entity_type: str, entity_id: Any,
                        db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """Aceleración real de una señal: segunda diferencia sobre las 3 últimas corridas.

    `market_signals.acceleration` está NULL para las señales de momentum porque
    el cálculo necesita TRES ventanas temporales y el pipeline sólo tenía dos
    (actual y anterior). Con ≥3 snapshots el historial las provee:

        delta_n   = v_n   - v_{n-1}
        delta_n-1 = v_{n-1} - v_{n-2}
        acceleration = delta_n - delta_n-1

    Devuelve ``None`` con menos de 3 puntos. **No escribe en `market_signals`**
    (esa tabla es de brand_intelligence/shelf); esto entrega el dato para que el
    dueño de ese módulo lo enganche — ver :func:`signal_accelerations`.
    """
    points = signal_trend(signal_type, entity_type, entity_id, db_path)
    values = [_num(p.get("value")) for p in points if _num(p.get("value")) is not None]
    if len(values) < 3:
        return None
    delta_now = values[-1] - values[-2]
    delta_prev = values[-2] - values[-3]
    return {
        "signal_type": signal_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "value": round(values[-1], 4),
        "delta": round(delta_now, 4),
        "acceleration": round(delta_now - delta_prev, 4),
        "points": len(values),
        "observed_at": points[-1].get("observed_at"),
        "trend": classify_trend(values[-3:], epsilon=0.001),
    }


def signal_accelerations(db_path: Path | str = DB_PATH,
                         *, signal_type: str | None = None) -> list[dict[str, Any]]:
    """Aceleración de todas las señales que ya tienen ≥3 snapshots.

    Cómo engancharla (lo haría el dueño de `market_signals`, no este módulo)::

        from app.services import history
        for row in history.signal_accelerations(db_path):
            conn.execute(
                "UPDATE market_signals SET acceleration = ? "
                "WHERE signal_type = ? AND entity_type = ? AND entity_id = ?",
                (row["acceleration"], row["signal_type"],
                 row["entity_type"], row["entity_id"]))

    Se llamaría después de `snapshot()`, ya con la corrida actual guardada.
    """
    clause, params = "", []
    if signal_type:
        clause, params = " WHERE signal_type = ?", [signal_type]
    entities = _rows(
        "SELECT signal_type, entity_type, entity_id, COUNT(DISTINCT run_id) AS runs "
        f"FROM signal_history{clause} GROUP BY signal_type, entity_type, entity_id "  # noqa: S608
        "HAVING runs >= 3 ORDER BY signal_type, entity_type, entity_id", params, db_path)

    out = []
    for e in entities:
        row = signal_acceleration(e["signal_type"], e["entity_type"], e["entity_id"], db_path)
        if row is not None:
            out.append(row)
    return out
