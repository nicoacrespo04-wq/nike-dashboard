"""Capa de adquisición: interfaz, registro y persistencia de colectores.

Diseño (ver ``backend/docs/collectors.md``):

  * **Desacoplado**: agregar una fuente = agregar un módulo que instancie un
    colector y llame a ``register()``. Nada más del backend cambia.
  * **Cada colector declara su política** (``SourcePolicy``): nombre de la
    fuente, tipo de acceso (API / feed / fixture / prohibido), licencia y ToS,
    rate limit y si respeta ``robots.txt``. Sin política no hay colector.
  * **Nada bloquea el proyecto**: si no hay red, credenciales o la fuente
    falla, ``collect()`` devuelve ``[]`` y el pipeline sigue. Ningún colector
    propaga excepciones hacia arriba.
  * **Idempotente**: la persistencia deduplica contra lo ya guardado usando una
    clave natural por tabla (el esquema no se toca: no hay UNIQUE nuevo).
  * **Privacidad**: las filas sociales pasan por un guard que rechaza cualquier
    campo que identifique a una persona. El esquema es agregado y se mantiene
    agregado.

Los umbrales viven en ``config/weights.yaml`` bajo la clave ``collectors``
(vía :func:`setting`); los defaults de este módulo son el fallback declarado en
un único lugar, para no hardcodear valores sueltos por el código.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from app.config import DB_PATH, section
from app.db import get_conn, init_db
from app.services.common import parse_date

log = logging.getLogger(__name__)

#: Defaults de la capa de adquisición. Todo valor es sobreescribible desde
#: ``config/weights.yaml`` con un bloque ``collectors:`` (ver docs).
DEFAULTS: dict[str, Any] = {
    "resolve": {
        # Un producto mal resuelto INVENTA competencia: preferimos perder la
        # mención. Umbral alto + margen contra el segundo candidato.
        "accept_threshold": 0.82,
        "ambiguity_margin": 0.05,
        "max_ngram": 5,
    },
    "editorial": {
        "max_excerpt_chars": 280,
        "max_same_list_pairs": 20,
        "max_products_per_list": 12,
    },
    "social": {
        "period_days": 30,
        "max_evidence_examples": 3,
        "max_evidence_chars": 220,
        "min_mentions_per_row": 1,
    },
    "http": {
        "timeout_seconds": 6.0,
        "user_agent": "NikeAR-CompetitiveIntelligence/1.0 (+contacto: data@nike-ar.example)",
        "default_rate_limit_seconds": 2.0,
        "max_items_per_run": 60,
    },
}

#: Tablas que puede escribir un colector. Ninguna otra está permitida.
ALLOWED_TABLES = ("editorial_mentions", "social_mention_aggregates")

#: Clave natural para deduplicar (idempotencia sin tocar el esquema).
DEDUP_KEYS: dict[str, tuple[str, ...]] = {
    "editorial_mentions": ("url", "mention_type", "product_a_id", "product_b_id", "list_key"),
    "social_mention_aggregates": (
        "period_start", "period_end", "country_code", "source_type",
        "brand_id", "product_id", "co_product_id", "topic", "intent",
    ),
}

#: Columna de fecha usada por el filtro ``--since``.
DATE_COLUMN: dict[str, str] = {
    "editorial_mentions": "published_at",
    "social_mention_aggregates": "period_end",
}

#: Campos que jamás pueden llegar a la base: identifican a una persona.
FORBIDDEN_FIELDS = frozenset({
    "author", "author_id", "author_name", "user", "user_id", "username", "usuario",
    "handle", "screen_name", "account", "profile", "profile_url", "email", "mail",
    "phone", "telefono", "dni", "ip", "device_id", "session_id", "avatar",
})


def collector_name(prefix: str, source_name: str) -> str:
    """Nombre de colector estable a partir del nombre de la fuente."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", source_name.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    slug = "".join(ch if ch.isalnum() else "_" for ch in plain)
    return f"{prefix}_" + "_".join(filter(None, slug.split("_")))


def setting(*keys: str, default: Any = None) -> Any:
    """Lee ``collectors.<keys>`` de weights.yaml; si no está, usa DEFAULTS."""
    value = section("collectors", *keys, default=None)
    if value is not None:
        return value
    node: Any = DEFAULTS
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node if node is not None else default


# ── política de fuente ──────────────────────────────────────

#: Tipos de acceso, de más a menos preferible.
ACCESS_API = "api"                 # API pública/oficial con términos de uso claros
ACCESS_FEED = "feed"               # RSS/Atom publicado por la propia fuente
ACCESS_FIXTURE = "fixture"         # archivos locales (tests, backfills manuales)
ACCESS_REVIEW = "review_required"  # scraping técnicamente posible, falta validar ToS/robots
ACCESS_PROHIBITED = "prohibited"   # los ToS lo prohíben: NO se implementa


@dataclass(frozen=True)
class SourcePolicy:
    """Ficha legal y operativa de una fuente. Requisito, no adorno."""

    source_name: str
    homepage: str
    access: str
    terms: str                       # resumen del ToS / licencia aplicable
    rate_limit_seconds: float = 2.0
    respects_robots_txt: bool = True
    country_code: str | None = None
    requires: tuple[str, ...] = ()   # credenciales/env vars necesarias
    stores: str = "metadatos (título, URL, fecha) + extracto breve con atribución"
    official_api: str | None = None  # qué haría falta si access == prohibited
    notes: str = ""

    @property
    def allowed(self) -> bool:
        """False si los ToS prohíben la adquisición automatizada."""
        return self.access != ACCESS_PROHIBITED

    @property
    def needs_network(self) -> bool:
        return self.access in (ACCESS_API, ACCESS_FEED, ACCESS_REVIEW)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "homepage": self.homepage,
            "access": self.access,
            "terms": self.terms,
            "rate_limit_seconds": self.rate_limit_seconds,
            "respects_robots_txt": self.respects_robots_txt,
            "country_code": self.country_code,
            "requires": list(self.requires),
            "stores": self.stores,
            "official_api": self.official_api,
            "notes": self.notes,
        }


# ── interfaz ────────────────────────────────────────────────


@runtime_checkable
class Collector(Protocol):
    """Contrato mínimo de un colector.

    ``collect`` NUNCA lanza: sin red, sin credenciales o con la fuente caída
    devuelve ``[]``. Las filas son dicts con columnas de ``table``.
    """

    name: str
    table: str
    policy: SourcePolicy

    def collect(self, since: date | None = None) -> list[dict]: ...


@dataclass
class BaseCollector:
    """Implementación base: nombre, tabla, política y estadísticas de la corrida."""

    name: str
    table: str
    policy: SourcePolicy
    enabled_by_default: bool = True
    stats: dict[str, Any] = field(default_factory=dict, repr=False)

    def collect(self, since: date | None = None) -> list[dict]:  # pragma: no cover - abstracto
        raise NotImplementedError

    # -- helpers compartidos -------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Una fuente prohibida por ToS nunca se habilita.

        Las fuentes de red arrancan apagadas y se encienden desde
        ``collectors.sources.<name>.enabled`` en weights.yaml, después de
        verificar robots.txt y términos (ver docs/collectors.md).
        """
        if not self.policy.allowed:
            return False
        configured = section("collectors", "sources", self.name, "enabled", default=None)
        if configured is not None:
            return bool(configured)
        return self.enabled_by_default

    def reset_stats(self) -> None:
        self.stats = {}

    def bump(self, key: str, amount: int = 1) -> None:
        self.stats[key] = int(self.stats.get(key, 0)) + amount


class DisabledCollector(BaseCollector):
    """Fuente cuya adquisición automatizada NO está permitida.

    Existe para dejar la interfaz declarada y documentada: qué plataforma es,
    por qué no se toca y qué API oficial haría falta para activarla. Siempre
    devuelve ``[]`` — no hay ninguna ruta de código que salga a la red.
    """

    def collect(self, since: date | None = None) -> list[dict]:
        log.info("colector '%s' inactivo por ToS: %s | API oficial requerida: %s",
                 self.name, self.policy.terms, self.policy.official_api)
        self.reset_stats()
        self.stats["skipped_reason"] = "prohibido por los términos de la fuente"
        return []


class FixtureCollector(BaseCollector):
    """Lee archivos locales en vez de salir a internet.

    Permite ejercitar toda la cadena (extracción → resolución → persistencia)
    sin red. Las subclases implementan ``parse_file``.
    """

    def __init__(self, name: str, table: str, directory: Path | str,
                 policy: SourcePolicy | None = None, patterns: Iterable[str] = ("*",),
                 country_code: str = "AR") -> None:
        super().__init__(
            name=name,
            table=table,
            policy=policy or SourcePolicy(
                source_name=name,
                homepage="file://" + str(directory),
                access=ACCESS_FIXTURE,
                terms="Archivos locales del repositorio; sin adquisición remota.",
                rate_limit_seconds=0.0,
                country_code=country_code,
            ),
        )
        self.directory = Path(directory)
        self.patterns = tuple(patterns)
        self.country_code = country_code

    def files(self) -> Iterator[Path]:
        if not self.directory.exists():
            return iter(())
        seen: set[Path] = set()
        out: list[Path] = []
        for pattern in self.patterns:
            for path in sorted(self.directory.rglob(pattern)):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    out.append(path)
        return iter(out)

    def parse_file(self, path: Path, since: date | None) -> list[dict]:  # pragma: no cover - abstracto
        raise NotImplementedError

    def collect(self, since: date | None = None) -> list[dict]:
        self.reset_stats()
        rows: list[dict] = []
        for path in self.files():
            try:
                rows.extend(self.parse_file(path, since))
                self.bump("files_read")
            except Exception as exc:  # noqa: BLE001 - un archivo roto no frena la corrida
                log.warning("fixture ilegible %s: %s", path, exc)
                self.bump("files_failed")
        self.bump("rows", len(rows))
        return rows


# ── registro ────────────────────────────────────────────────

_REGISTRY: dict[str, Collector] = {}


def register(collector: Collector) -> None:
    """Registra un colector. Reemplaza si ya existía uno con el mismo nombre."""
    name = getattr(collector, "name", None)
    table = getattr(collector, "table", None)
    policy = getattr(collector, "policy", None)
    if not name:
        raise ValueError("el colector no declara 'name'")
    if table not in ALLOWED_TABLES:
        raise ValueError(f"'{name}': tabla no permitida {table!r} (permitidas: {ALLOWED_TABLES})")
    if not isinstance(policy, SourcePolicy):
        raise ValueError(f"'{name}': falta declarar SourcePolicy (fuente, licencia/ToS, rate limit)")
    if not callable(getattr(collector, "collect", None)):
        raise ValueError(f"'{name}': no implementa collect()")
    _REGISTRY[name] = collector


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def registered() -> dict[str, Collector]:
    """Copia del registro (nombre -> colector)."""
    return dict(_REGISTRY)


def get_collector(name: str) -> Collector | None:
    return _REGISTRY.get(name)


def clear_registry() -> None:
    """Vacía el registro (tests)."""
    _REGISTRY.clear()


def catalog() -> list[dict[str, Any]]:
    """Ficha de todas las fuentes registradas: qué se puede y qué no."""
    out = []
    for name, collector in sorted(_REGISTRY.items()):
        policy: SourcePolicy = collector.policy  # type: ignore[attr-defined]
        out.append({
            "name": name,
            "table": collector.table,  # type: ignore[attr-defined]
            "enabled": bool(getattr(collector, "enabled", True)),
            **policy.as_dict(),
        })
    return out


# ── persistencia idempotente ────────────────────────────────


def _table_columns(conn, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _key_of(row: dict, keys: tuple[str, ...]) -> tuple:
    return tuple("" if row.get(k) in (None, "") else str(row.get(k)) for k in keys)


def _existing_keys(conn, table: str, keys: tuple[str, ...]) -> set[tuple]:
    cols = ", ".join(keys)
    return {
        _key_of(dict(row), keys)
        for row in conn.execute(f"SELECT {cols} FROM {table}").fetchall()
    }


def assert_aggregate_only(row: dict, table: str) -> None:
    """Guard de privacidad: ninguna fila puede identificar a una persona."""
    offending = sorted(set(row) & FORBIDDEN_FIELDS)
    if offending:
        raise ValueError(
            f"fila para '{table}' con campos que identifican individuos: {offending}. "
            "La capa social es SIEMPRE agregada."
        )


def _filter_since(rows: list[dict], table: str, since: date | None) -> list[dict]:
    if since is None:
        return rows
    column = DATE_COLUMN.get(table)
    if not column:
        return rows
    kept = []
    for row in rows:
        moment = parse_date(row.get(column))
        if moment is None or moment >= since:
            kept.append(row)
    return kept


def persist(rows: list[dict], table: str, db_path: Path | str = DB_PATH,
            *, dry_run: bool = False) -> int:
    """Inserta las filas nuevas y devuelve cuántas se insertaron.

    Deduplica contra lo ya guardado y dentro del propio lote (idempotencia).
    Con ``dry_run=True`` calcula el resultado sin escribir.
    """
    if table not in ALLOWED_TABLES:
        raise ValueError(f"tabla no permitida: {table}")
    if not rows:
        return 0

    keys = DEDUP_KEYS[table]
    with get_conn(db_path) as conn:
        columns = set(_table_columns(conn, table))
        if not columns:
            raise ValueError(f"la tabla '{table}' no existe en el esquema")
        seen = _existing_keys(conn, table, keys)

        payload: list[dict] = []
        for row in rows:
            assert_aggregate_only(row, table)
            unknown = sorted(set(row) - columns)
            if unknown:
                raise ValueError(f"columnas inexistentes en '{table}': {unknown}")
            key = _key_of(row, keys)
            if key in seen:
                continue
            seen.add(key)
            payload.append(row)

        if dry_run or not payload:
            return len(payload)

        for row in payload:
            cols = [c for c in row if c != "id"]
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
                   f"VALUES ({', '.join('?' for _ in cols)})")
            conn.execute(sql, tuple(row[c] for c in cols))
    return len(payload)


# ── orquestación ────────────────────────────────────────────


def run_collectors(db_path: Path | str = DB_PATH, *, names: list[str] | None = None,
                   since: date | None = None, dry_run: bool = False) -> dict[str, int]:
    """Corre los colectores y devuelve ``{nombre: filas nuevas insertadas}``.

    * ``names=None`` corre los habilitados; nombrar una fuente explícitamente
      la corre aunque esté apagada por configuración (nunca si está prohibida).
    * Un colector que falla se registra y devuelve 0: el pipeline no se frena.
    * ``dry_run=True`` no escribe nada; informa cuántas filas se insertarían.
    """
    init_db(db_path)  # idempotente: crea el esquema si la base es nueva

    selected: list[Collector] = []
    skipped: set[str] = set()
    for name, collector in sorted(_REGISTRY.items()):
        if names is not None and name not in names:
            continue
        policy: SourcePolicy = collector.policy  # type: ignore[attr-defined]
        if not policy.allowed:
            log.info("colector '%s' omitido: prohibido por los ToS de la fuente "
                     "(API oficial necesaria: %s)", name, policy.official_api or "n/d")
            skipped.add(name)
            continue
        if names is None and not getattr(collector, "enabled", True):
            log.info("colector '%s' omitido: deshabilitado en configuración", name)
            skipped.add(name)
            continue
        selected.append(collector)

    if names:
        known = {c.name for c in selected} | skipped  # type: ignore[attr-defined]
        for missing in sorted(set(names) - known):
            log.warning("colector '%s' inexistente", missing)

    report: dict[str, int] = {}
    for collector in selected:
        name = collector.name  # type: ignore[attr-defined]
        table = collector.table  # type: ignore[attr-defined]
        # Convención: el orquestador manda la base. Un colector que resuelve
        # productos contra el catálogo (atributo ``db_path``) usa ésta, no la
        # que tenía al registrarse.
        if getattr(collector, "db_path", None) is not None:
            try:
                collector.db_path = db_path  # type: ignore[attr-defined]
            except AttributeError:  # pragma: no cover - colector inmutable
                pass
        try:
            rows = collector.collect(since=since) or []
        except Exception as exc:  # noqa: BLE001 - degradación elegante: nada bloquea
            log.warning("colector '%s' falló (%s: %s); se continúa con 0 filas",
                        name, type(exc).__name__, exc)
            report[name] = 0
            continue

        rows = _filter_since(list(rows), table, since)
        try:
            report[name] = persist(rows, table, db_path, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            log.warning("persistencia de '%s' falló (%s: %s)", name, type(exc).__name__, exc)
            report[name] = 0

    return report
