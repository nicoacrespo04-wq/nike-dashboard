"""Triaje del Opportunity Center: qué hicimos con cada oportunidad.

Un tablero de 61 oportunidades que no se puede tocar se vuelve ruido en dos
semanas. Este módulo le da a cada oportunidad un ciclo de vida operativo
(``open -> snoozed | dismissed | resolved``), un responsable y una nota, para
que la pantalla refleje el trabajo del equipo y no sólo el output del motor.

IDENTIDAD ESTABLE — el punto delicado
-------------------------------------
El pipeline BORRA Y RECALCULA ``opportunities`` en cada corrida, así que
``opportunities.id`` no sobrevive: guardar el triaje contra ese id significaría
perder todo el trabajo del equipo en cada run. El triaje se indexa por
``entity_key``: un hash determinístico de los campos que definen DE QUÉ se está
hablando — ``opportunity_type``, ``nike_product_id``, ``competitor_product_id``,
``retailer_id``, ``country_code``.

La definición canónica vive en ``app.services.history.entity_key``, que se llama
como ``entity_key("opportunity", **row)``. Acá se importa desde ahí; si ese
módulo no está, se usa un fallback IDÉNTICO (mismos campos, misma
normalización, mismo prefijo, mismo hash) para que las dos implementaciones
produzcan la misma clave. ``KEY_SOURCE`` dice cuál está activa y
``test_triage.py`` compara las dos: si divergen, el triaje se pierde en cada
corrida del pipeline.

Modelo de estados
-----------------
    open       la oportunidad está en la bandeja (estado por defecto)
    snoozed    pospuesta hasta ``snooze_until``; al vencer vuelve sola a open
    dismissed  descartada: no es accionable, no queremos verla más
    resolved   ya se actuó sobre ella

Una oportunidad SIN fila de triaje es ``open``: no se materializa nada hasta
que alguien toma una decisión. Las 61 filas fantasma no existen.

Journal: la segunda red
-----------------------
``entity_key`` resuelve el DELETE + recálculo de ``opportunities``, pero el
reset del pipeline es más agresivo: ``init_db(drop=True)`` BORRA EL ARCHIVO
SQLite entero, con la tabla de triaje adentro. El pipeline ya rescata
``opportunity_triage`` con ``history.capture()``/``restore()`` alrededor del
reset — ese es el mecanismo principal.

El journal es la red de abajo: cada cambio se apunta además en un archivo
append-only al lado de la base (``intelligence.triage.jsonl``). Si una lectura
encuentra la tabla vacía y hay journal, lo reproduce. Cubre los casos en que el
gancho del pipeline no corre (``run_all(history=False)``, un ``init_db(drop=True)``
a mano, una base restaurada de otro lado). La base manda siempre: el journal
sólo repone lo que falta. Se apaga con ``CI_TRIAGE_JOURNAL=0``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH
from app.db import get_conn, query

# ── identidad estable ───────────────────────────────────────

#: Campos que definen la identidad de una oportunidad, EN ESTE ORDEN.
KEY_FIELDS: tuple[str, ...] = (
    "opportunity_type",
    "nike_product_id",
    "competitor_product_id",
    "retailer_id",
    "country_code",
)

#: Discriminante del tipo de entidad dentro del hash (``history.ENTITY_FIELDS``).
KEY_KIND = "opportunity"

#: Longitud del hash publicado. Un sha1 truncado a 16 hex (64 bits) es
#: suficiente para unos pocos miles de oportunidades y entra cómodo en una URL.
KEY_LENGTH = 16

_INT_RE = re.compile(r"^-?\d+$")

try:  # definición canónica; la escribe `app.services.history`
    from app.services.history import entity_key as _history_entity_key
except ImportError:  # pragma: no cover - depende de qué módulos existan
    _history_entity_key = None


def _normalize_part(value: Any) -> str:
    """Normaliza un componente del hash. COPIA EXACTA de ``history._normalize_part``.

    ``None`` -> ``""``; ``12``, ``12.0`` y ``"12"`` -> ``"12"``; el texto se
    recorta, se colapsan espacios y se pasa a minúsculas (``"AR"`` == ``"ar"``).

    Si esta función se separa de la de ``history``, las dos mitades del sistema
    dejan de hablar de la misma oportunidad y el triaje se pierde en cada
    corrida. ``test_triage.py`` compara las dos implementaciones justamente por eso.
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


def _fallback_entity_key(values: Sequence[Any]) -> str:
    """Hash de los cinco campos. Réplica de ``history.entity_key("opportunity", ...)``."""
    payload = "|".join(
        [KEY_KIND] + [f"{field}={_normalize_part(value)}"
                      for field, value in zip(KEY_FIELDS, values)]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:KEY_LENGTH]  # noqa: S324


#: Formas de invocación soportadas para ``history.entity_key``, en orden de
#: preferencia. La primera es la firma real de hoy — ``entity_key(kind, **parts)``;
#: las otras cubren que el módulo se refactorice sin avisar. Una forma que no
#: corresponde levanta ``TypeError`` (no devuelve una clave equivocada), así que
#: probarlas en orden es seguro.
_CALL_STYLES: tuple[Any, ...] = (
    lambda fn, kw: fn(KEY_KIND, **kw),      # entity_key("opportunity", **row)
    lambda fn, kw: fn(**kw),                # entity_key(opportunity_type=..., ...)
    lambda fn, kw: fn(*kw.values()),        # entity_key(type, nike_id, ...)
    lambda fn, kw: fn(dict(kw)),            # entity_key(row)
)

#: ``"history"`` si la clave la calcula ``app.services.history``; ``"fallback"``
#: si la calcula este módulo (todavía no existe ese módulo o cambió su firma).
KEY_SOURCE = "history" if _history_entity_key is not None else "fallback"

_call_style: Any = None


def _field_value(row: Mapping[str, Any], field: str) -> Any:
    """Lee un campo de identidad de una fila cruda O de una oportunidad serializada.

    El serializer de la API reemplaza ``nike_product_id`` por el objeto
    ``nike_product``; la clave tiene que salir igual en los dos casos.
    """
    if field in row and row[field] is not None:
        return row[field]
    nested = {
        "nike_product_id": "nike_product",
        "competitor_product_id": "competitor_product",
        "retailer_id": "retailer",
    }.get(field)
    if nested:
        obj = row.get(nested)
        if isinstance(obj, Mapping):
            return obj.get("id")
    return None


def entity_key(opportunity: Mapping[str, Any]) -> str:
    """Clave estable de una oportunidad (fila cruda o serializada)."""
    values = [_field_value(opportunity, field) for field in KEY_FIELDS]
    return entity_key_from(*values)


def entity_key_from(
    opportunity_type: Any,
    nike_product_id: Any = None,
    competitor_product_id: Any = None,
    retailer_id: Any = None,
    country_code: Any = None,
) -> str:
    """Clave estable a partir de los cinco campos sueltos."""
    global _call_style
    values = (opportunity_type, nike_product_id, competitor_product_id,
              retailer_id, country_code)
    if _history_entity_key is not None:
        kwargs = dict(zip(KEY_FIELDS, values))
        for style in ([_call_style] if _call_style else _CALL_STYLES):
            try:
                key = str(style(_history_entity_key, kwargs))
            except TypeError:
                continue
            _call_style = style
            return key
    return _fallback_entity_key(values)


# ── modelo de estados ───────────────────────────────────────

OPEN = "open"
SNOOZED = "snoozed"
DISMISSED = "dismissed"
RESOLVED = "resolved"

#: Estados válidos, en orden de "cuánto pesa en la bandeja".
STATES: tuple[str, ...] = (OPEN, SNOOZED, DISMISSED, RESOLVED)

#: Transiciones permitidas. Explícitas a propósito: desde `dismissed` o
#: `resolved` lo único que se puede hacer es REABRIR — así "descartar" y
#: "resolver" son decisiones que quedan registradas y no se pisan de costado.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    OPEN:      (OPEN, SNOOZED, DISMISSED, RESOLVED),
    SNOOZED:   (OPEN, SNOOZED, DISMISSED, RESOLVED),   # re-posponer extiende el plazo
    DISMISSED: (OPEN, DISMISSED),                      # sólo reabrir (o editar nota/asignado)
    RESOLVED:  (OPEN, RESOLVED),
}

#: Estados que siguen exigiendo atención del equipo.
ACTIONABLE_STATES: tuple[str, ...] = (OPEN,)

_TABLE = "opportunity_triage"
_COLUMNS = ("entity_key", "state", "assignee", "note", "snooze_until",
            "first_seen_at", "updated_at", "updated_by")

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Autor que queda registrado cuando un snooze vence solo.
_SNOOZE_EXPIRED_BY = "system:snooze_expired"


class TriageError(ValueError):
    """Entrada inválida: estado desconocido, transición prohibida o snooze vencido.

    Hereda de ``ValueError`` para que el router la traduzca a un 422 con el
    mensaje tal cual — el texto ya está escrito para que lo lea un humano.
    """


# ── helpers de tiempo ───────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).strftime(_TS_FORMAT)


def _parse_moment(value: Any) -> datetime | None:
    """Parsea una fecha/fecha-hora en cualquiera de los formatos que llegan.

    Una fecha sola (``2026-09-01``) se interpreta como el FINAL de ese día:
    "posponer hasta el 1" significa "que vuelva el 2 a la mañana", no "a las
    00:00 del 1" (que ya sería pasado apenas se elige en el date picker).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    if len(text) == 10:  # YYYY-MM-DD
        try:
            day = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return day.replace(hour=23, minute=59, second=59)
    for fmt in (_TS_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).replace(microsecond=0)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


# ── acceso a la tabla ───────────────────────────────────────


def _table_exists(db_path: Any = DB_PATH) -> bool:
    rows = query("SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                 (_TABLE,), db_path)
    return bool(rows)


# ── journal (sobrevive al borrado del archivo SQLite) ───────

#: El journal se puede apagar (``CI_TRIAGE_JOURNAL=0``) — por ejemplo en tests
#: que quieren ver la base pelada.
JOURNAL_ENABLED = os.getenv("CI_TRIAGE_JOURNAL", "1").strip().lower() not in ("0", "off", "false", "no")


def journal_path(db_path: Any = DB_PATH) -> Path | None:
    """Archivo de respaldo del triaje, al lado de la base. ``None`` si no aplica."""
    if not JOURNAL_ENABLED:
        return None
    raw = str(db_path).strip()
    if not raw or raw == ":memory:":
        return None
    return Path(raw).with_suffix(".triage.jsonl")


def _journal_append(db_path: Any, entry: Mapping[str, Any]) -> None:
    """Apunta un cambio. Un journal roto nunca puede romper la operación."""
    path = journal_path(db_path)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(entry), ensure_ascii=False, default=str) + "\n")
    except OSError:  # pragma: no cover - disco lleno / permisos
        pass


def restore_from_journal(db_path: Any = DB_PATH) -> int:
    """Reproduce el journal y reinserta el triaje que falte. Devuelve cuántas filas repuso.

    Sólo agrega lo que no está: si la base ya tiene una fila para esa clave,
    gana la base (es más nueva que el journal en cualquier escenario normal).
    """
    path = journal_path(db_path)
    if path is None or not path.exists() or not _table_exists(db_path):
        return 0

    latest: dict[str, dict[str, Any] | None] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover
        return 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:  # línea a medio escribir: se ignora
            continue
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("entity_key") or "").strip()
        if not key:
            continue
        latest[key] = None if entry.get("op") == "delete" else entry

    pending = [entry for entry in latest.values() if entry]
    if not pending:
        return 0

    with get_conn(db_path) as conn:
        existing = {row[0] for row in conn.execute(f"SELECT entity_key FROM {_TABLE}")}  # noqa: S608
        rows = [
            tuple(entry.get(column) for column in _COLUMNS)
            for entry in pending
            if entry["entity_key"] not in existing
            and str(entry.get("state") or OPEN) in STATES
        ]
        if rows:
            conn.executemany(
                f"INSERT OR IGNORE INTO {_TABLE} ({', '.join(_COLUMNS)}) "  # noqa: S608
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                rows,
            )
    return len(rows)


def _autorestore(db_path: Any) -> None:
    """Si la tabla quedó vacía (base recreada por el pipeline), repone del journal."""
    if not JOURNAL_ENABLED or not _table_exists(db_path):
        return
    rows = query(f"SELECT COUNT(*) AS n FROM {_TABLE}", (), db_path)  # noqa: S608
    if rows and int(rows[0]["n"]) == 0:
        restore_from_journal(db_path)


def _clean_key(entity_key_value: Any) -> str:
    key = str(entity_key_value or "").strip()
    if not key:
        raise TriageError("Falta el `entity_key` de la oportunidad.")
    return key


def default_state(entity_key_value: str) -> dict[str, Any]:
    """Estado de una oportunidad que nadie tocó todavía: ``open`` virtual."""
    return {
        "entity_key": entity_key_value,
        "state": OPEN,
        "assignee": None,
        "note": None,
        "snooze_until": None,
        "first_seen_at": None,
        "updated_at": None,
        "updated_by": None,
        "actionable": True,
        "default": True,
    }


def _row_to_state(row: Mapping[str, Any]) -> dict[str, Any]:
    state = str(row.get("state") or OPEN)
    return {
        "entity_key": row.get("entity_key"),
        "state": state,
        "assignee": row.get("assignee"),
        "note": row.get("note"),
        "snooze_until": row.get("snooze_until"),
        "first_seen_at": row.get("first_seen_at"),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
        "actionable": state in ACTIONABLE_STATES,
        "default": False,
    }


def get_state(entity_key: str, db_path: Any = DB_PATH) -> dict[str, Any] | None:  # noqa: A002
    """Estado persistido, o ``None`` si la oportunidad nunca se triajeó.

    ``None`` significa ``open`` (ver ``default_state``): no se materializa una
    fila por cada oportunidad sólo para decir "no hicimos nada todavía".
    """
    key = _clean_key(entity_key)
    if not _table_exists(db_path):
        return None
    _autorestore(db_path)
    rows = query(f"SELECT * FROM {_TABLE} WHERE entity_key = ?", (key,), db_path)  # noqa: S608
    return _row_to_state(rows[0]) if rows else None


def bulk_states(entity_keys: list[str], db_path: Any = DB_PATH) -> dict[str, dict[str, Any]]:
    """Estados persistidos de muchas claves, en una sola pasada a la base.

    Sólo devuelve las claves QUE TIENEN fila. Las que faltan son ``open``.
    """
    keys = [str(k).strip() for k in (entity_keys or []) if str(k or "").strip()]
    if not keys or not _table_exists(db_path):
        return {}

    _autorestore(db_path)
    unique = list(dict.fromkeys(keys))
    out: dict[str, dict[str, Any]] = {}
    chunk = 400  # bien por debajo del límite de variables de SQLite
    for start in range(0, len(unique), chunk):
        batch = unique[start:start + chunk]
        placeholders = ",".join("?" * len(batch))
        for row in query(
            f"SELECT * FROM {_TABLE} WHERE entity_key IN ({placeholders})",  # noqa: S608
            batch, db_path,
        ):
            out[str(row["entity_key"])] = _row_to_state(row)
    return out


def set_state(
    entity_key: str,  # noqa: A002
    state: str,
    db_path: Any = DB_PATH,
    *,
    assignee: str | None = None,
    note: str | None = None,
    snooze_until: Any = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Aplica una transición y devuelve el estado resultante.

    Convenciones:
      * ``assignee``/``note`` en ``None`` NO pisan lo que había; ``""`` limpia.
      * ``snoozed`` EXIGE ``snooze_until`` en el futuro; cualquier otro estado
        lo borra (una oportunidad reabierta no arrastra el plazo viejo).
      * ``first_seen_at`` se fija la primera vez y nunca se pisa: es cuándo el
        equipo vio esto por primera vez, no cuándo lo tocó por última.
    """
    key = _clean_key(entity_key)
    new_state = str(state or "").strip().lower()
    if new_state not in STATES:
        raise TriageError(
            f"Estado '{state}' inválido. Estados permitidos: {', '.join(STATES)}."
        )
    if not _table_exists(db_path):
        raise TriageError(
            "La tabla `opportunity_triage` no existe en esta base. "
            "Corré `python -m app.pipeline` (o `app.db.init_db`) para crear el esquema."
        )

    _autorestore(db_path)
    current_rows = query(f"SELECT * FROM {_TABLE} WHERE entity_key = ?", (key,), db_path)  # noqa: S608
    current = current_rows[0] if current_rows else None
    current_state = str(current["state"]) if current else OPEN
    if current_state not in STATES:  # dato viejo o corrupto: tratarlo como open
        current_state = OPEN

    allowed = TRANSITIONS.get(current_state, (OPEN,))
    if new_state not in allowed:
        raise TriageError(
            f"Transición inválida: de '{current_state}' a '{new_state}'. "
            f"Desde '{current_state}' sólo se puede pasar a: {', '.join(allowed)}."
        )

    now = _now()
    normalized_snooze: str | None = None
    if new_state == SNOOZED:
        moment = _parse_moment(snooze_until)
        if moment is None and current_state == SNOOZED:
            # Editar la nota o el responsable de algo YA pospuesto no tiene por
            # qué obligar a reelegir la fecha: se conserva el plazo vigente.
            moment = _parse_moment(current["snooze_until"] if current else None)
        if moment is None:
            raise TriageError(
                "Para posponer hace falta una fecha `snooze_until` "
                "(formato YYYY-MM-DD o YYYY-MM-DDTHH:MM:SS)."
            )
        if moment <= now:
            raise TriageError(
                f"`snooze_until` tiene que ser futuro: {_stamp(moment)} ya pasó "
                f"(ahora es {_stamp(now)} UTC)."
            )
        normalized_snooze = _stamp(moment)

    new_assignee = current["assignee"] if current else None
    if assignee is not None:
        new_assignee = assignee.strip() or None
    new_note = current["note"] if current else None
    if note is not None:
        new_note = note.strip() or None
    author = (updated_by or "").strip() or None
    first_seen = (current["first_seen_at"] if current else None) or _stamp(now)

    with get_conn(db_path) as conn:
        if current:
            conn.execute(
                f"UPDATE {_TABLE} SET state = ?, assignee = ?, note = ?, snooze_until = ?, "  # noqa: S608
                "first_seen_at = ?, updated_at = ?, updated_by = ? WHERE entity_key = ?",
                (new_state, new_assignee, new_note, normalized_snooze, first_seen,
                 _stamp(now), author, key),
            )
        else:
            conn.execute(
                f"INSERT INTO {_TABLE} (entity_key, state, assignee, note, snooze_until, "  # noqa: S608
                "first_seen_at, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?)",
                (key, new_state, new_assignee, new_note, normalized_snooze, first_seen,
                 _stamp(now), author),
            )

    result = get_state(key, db_path)
    assert result is not None  # acabamos de escribirla
    _journal_append(db_path, {"op": "set", **{c: result.get(c) for c in _COLUMNS}})
    return result


def clear_state(entity_key: str, db_path: Any = DB_PATH) -> bool:  # noqa: A002
    """Borra la fila de triaje (vuelve al ``open`` por defecto). Para deshacer."""
    key = _clean_key(entity_key)
    if not _table_exists(db_path):
        return False
    with get_conn(db_path) as conn:
        cur = conn.execute(f"DELETE FROM {_TABLE} WHERE entity_key = ?", (key,))  # noqa: S608
        deleted = cur.rowcount > 0
    _journal_append(db_path, {"op": "delete", "entity_key": key})
    return deleted


def expire_snoozes(db_path: Any = DB_PATH) -> int:
    """Devuelve a ``open`` los snoozes vencidos. Retorna cuántos reabrió.

    Un ``snoozed`` sin fecha se considera vencido: no queremos que un dato
    incompleto esconda una oportunidad para siempre.
    """
    if not _table_exists(db_path):
        return 0
    _autorestore(db_path)
    rows = query(
        f"SELECT entity_key, snooze_until FROM {_TABLE} WHERE state = ?",  # noqa: S608
        (SNOOZED,), db_path,
    )
    now = _now()
    expired = [
        row["entity_key"] for row in rows
        if (_parse_moment(row.get("snooze_until")) or now) <= now
    ]
    if not expired:
        return 0

    stamp = _stamp(now)
    with get_conn(db_path) as conn:
        conn.executemany(
            f"UPDATE {_TABLE} SET state = ?, snooze_until = NULL, updated_at = ?, "  # noqa: S608
            "updated_by = ? WHERE entity_key = ?",
            [(OPEN, stamp, _SNOOZE_EXPIRED_BY, key) for key in expired],
        )
    for key in expired:
        row = query(f"SELECT * FROM {_TABLE} WHERE entity_key = ?", (key,), db_path)  # noqa: S608
        if row:
            _journal_append(db_path, {"op": "set", **{c: row[0].get(c) for c in _COLUMNS}})
    return len(expired)


def apply_to(opportunities: list[dict[str, Any]], db_path: Any = DB_PATH) -> list[dict[str, Any]]:
    """Adjunta ``entity_key`` y ``triage`` a cada oportunidad.

    Funciona igual sobre filas crudas de ``opportunities`` y sobre las
    oportunidades ya serializadas por la API. No muta la entrada.

    Vence los snoozes antes de leer: sin scheduler, el momento natural para
    reabrir lo pospuesto es cuando alguien mira la pantalla.
    """
    items = list(opportunities or [])
    if not items:
        return []

    expire_snoozes(db_path)
    keys = [entity_key(item) for item in items]
    states = bulk_states(keys, db_path)

    out: list[dict[str, Any]] = []
    for item, key in zip(items, keys):
        enriched = dict(item)
        enriched["entity_key"] = key
        enriched["triage"] = states.get(key) or default_state(key)
        out.append(enriched)
    return out


def filter_by_state(opportunities: Iterable[Mapping[str, Any]],
                    states: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Filtra oportunidades YA enriquecidas por ``apply_to`` según su estado."""
    wanted = tuple(states) if states else ACTIONABLE_STATES
    return [
        dict(item) for item in opportunities
        if str((item.get("triage") or {}).get("state") or OPEN) in wanted
    ]


def stats(db_path: Any = DB_PATH) -> dict[str, Any]:
    """Resumen del triaje: cuánto trabajo hay hecho y cuánto queda.

    ``by_state`` cuenta FILAS de triaje. ``opportunities`` cuenta las
    oportunidades vigentes repartidas por estado — ahí las que nadie tocó
    aparecen como ``open``, que es lo que el usuario ve en la pantalla.
    """
    if not _table_exists(db_path):
        return {"tracked": 0, "by_state": {s: 0 for s in STATES}, "assignees": [],
                "expired_snoozes": 0, "opportunities": None}

    expired = expire_snoozes(db_path)

    by_state = {s: 0 for s in STATES}
    for row in query(f"SELECT state, COUNT(*) AS n FROM {_TABLE} GROUP BY state", (), db_path):  # noqa: S608
        by_state[str(row["state"])] = int(row["n"])

    assignees = [
        {"assignee": row["assignee"], "n": int(row["n"])}
        for row in query(
            f"SELECT assignee, COUNT(*) AS n FROM {_TABLE} "  # noqa: S608
            "WHERE assignee IS NOT NULL AND assignee <> '' "
            "GROUP BY assignee ORDER BY n DESC, assignee",
            (), db_path,
        )
    ]

    opportunities: dict[str, Any] | None = None
    if query("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities'",
             (), db_path):
        rows = query(
            "SELECT opportunity_type, nike_product_id, competitor_product_id, "
            "retailer_id, country_code FROM opportunities", (), db_path,
        )
        keys = [entity_key(row) for row in rows]
        persisted = bulk_states(keys, db_path)
        counts = {s: 0 for s in STATES}
        for key in keys:
            counts[str(persisted.get(key, {}).get("state", OPEN))] += 1
        opportunities = {"total": len(keys), **counts,
                         "actionable": sum(counts[s] for s in ACTIONABLE_STATES)}

    return {
        "tracked": sum(by_state.values()),
        "by_state": by_state,
        "assignees": assignees,
        "expired_snoozes": expired,
        "opportunities": opportunities,
    }


def list_states(state: str | None = None, db_path: Any = DB_PATH, *,
                assignee: str | None = None, limit: int = 200,
                offset: int = 0) -> list[dict[str, Any]]:
    """Filas de triaje persistidas, opcionalmente filtradas por estado/asignado."""
    if not _table_exists(db_path):
        return []
    expire_snoozes(db_path)
    if state is not None:
        wanted = str(state).strip().lower()
        if wanted not in STATES:
            raise TriageError(
                f"Estado '{state}' inválido. Estados permitidos: {', '.join(STATES)}."
            )
    else:
        wanted = None

    where: list[str] = []
    params: list[Any] = []
    if wanted:
        where.append("state = ?")
        params.append(wanted)
    if assignee:
        where.append("assignee = ?")
        params.append(assignee)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    rows = query(
        f"SELECT * FROM {_TABLE}{clause} "  # noqa: S608
        "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        [*params, max(1, limit), max(0, offset)], db_path,
    )
    return [_row_to_state(row) for row in rows]
