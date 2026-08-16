"""Seguridad de borde de la API: API key por header + rate limiting en memoria.

Dos decisiones de diseño, ambas pensadas para que nada de esto moleste en
desarrollo y todo esté listo el día del deploy:

1. **La autenticación es opcional y se activa por entorno.** Sin ``CI_API_KEY``
   la API queda ABIERTA exactamente como estaba (y lo avisa por log al
   arrancar). Con ``CI_API_KEY`` definida, todo endpoint que no sea público
   exige el header ``X-API-Key``.
2. **El rate limiting no depende de nadie.** Ventana deslizante en memoria del
   proceso, sin Redis ni slowapi. Si mañana hay varios workers, cada uno lleva
   su propia ventana: es un techo por proceso, no una cuota global. Alcanza
   para frenar un scrapeo del análisis competitivo; no pretende ser un WAF.

Endpoints públicos (nunca piden key): ``/api/health`` — es el latido que
consulta la cinta de estado del dashboard, si pidiera key el dashboard
mostraría "motor caído" ante un error de configuración — y la documentación
(``/docs``, ``/redoc``, ``/openapi.json``).

Variables de entorno
--------------------
==============================  =========  ===================================
Variable                        Default    Qué hace
==============================  =========  ===================================
``CI_API_KEY``                  (vacío)    Key(s) válidas, separadas por coma.
                                           Vacío ⇒ API abierta.
``CI_RATE_LIMIT``               ``600``    Requests por ventana y por bucket.
                                           ``0`` desactiva el límite.
``CI_RATE_LIMIT_WINDOW``        ``60``     Tamaño de la ventana, en segundos.
``CI_TRUST_FORWARDED_FOR``      ``0``      ``1`` ⇒ usar el primer hop de
                                           ``X-Forwarded-For`` como IP cliente
                                           (sólo detrás de un proxy propio).
``CI_PUBLIC_PATHS``             (vacío)    Rutas extra sin auth, separadas por
                                           coma.
==============================  =========  ===================================

El bucket del rate limit es ``(IP del cliente, identidad de la key, ámbito)``.
El ámbito separa las rutas públicas de las privadas a propósito: un flood
contra ``/api/opportunities`` no debe dejar sin respuesta al health check.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOGGER = logging.getLogger("app.auth")

API_KEY_HEADER = "X-API-Key"

ENV_API_KEY = "CI_API_KEY"
ENV_RATE_LIMIT = "CI_RATE_LIMIT"
ENV_RATE_LIMIT_WINDOW = "CI_RATE_LIMIT_WINDOW"
ENV_TRUST_FORWARDED_FOR = "CI_TRUST_FORWARDED_FOR"
ENV_PUBLIC_PATHS = "CI_PUBLIC_PATHS"

DEFAULT_RATE_LIMIT = 600
DEFAULT_RATE_LIMIT_WINDOW = 60.0

#: Rutas que nunca piden key. `/docs` cubre también sus assets (`/docs/...`).
PUBLIC_PATHS: tuple[str, ...] = (
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

UNAUTHORIZED_DETAIL = (
    f"API key inválida o ausente. Mandá el header {API_KEY_HEADER} con la key "
    f"configurada en {ENV_API_KEY}."
)
RATE_LIMITED_DETAIL = (
    "Demasiadas requests. Esperá los segundos que indica el header Retry-After."
)


# ── configuración ───────────────────────────────────────────


@dataclass(frozen=True)
class SecuritySettings:
    """Foto del entorno para una request. Se relee en cada una a propósito:
    así un test (o un reload de uvicorn) puede cambiar la config sin reiniciar.
    """

    api_keys: tuple[str, ...]
    rate_limit: int
    window: float
    trust_forwarded_for: bool
    public_paths: tuple[str, ...]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)

    @property
    def rate_limit_enabled(self) -> bool:
        return self.rate_limit > 0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SecuritySettings:
        source = os.environ if env is None else env
        keys = tuple(k.strip() for k in source.get(ENV_API_KEY, "").split(",") if k.strip())
        extra = tuple(p.strip() for p in source.get(ENV_PUBLIC_PATHS, "").split(",") if p.strip())
        return cls(
            api_keys=keys,
            rate_limit=_int_env(source, ENV_RATE_LIMIT, DEFAULT_RATE_LIMIT),
            window=_float_env(source, ENV_RATE_LIMIT_WINDOW, DEFAULT_RATE_LIMIT_WINDOW),
            trust_forwarded_for=_bool_env(source, ENV_TRUST_FORWARDED_FOR),
            public_paths=PUBLIC_PATHS + extra,
        )


def _int_env(source: Any, name: str, default: int) -> int:
    raw = str(source.get(name, "")).strip()
    if not raw:
        return default
    try:
        return max(0, int(float(raw)))
    except ValueError:
        LOGGER.warning("%s='%s' no es un número; se usa el default %s", name, raw, default)
        return default


def _float_env(source: Any, name: str, default: float) -> float:
    raw = str(source.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("%s='%s' no es un número; se usa el default %s", name, raw, default)
        return default
    return value if value > 0 else default


def _bool_env(source: Any, name: str) -> bool:
    return str(source.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def settings() -> SecuritySettings:
    return SecuritySettings.from_env()


def is_public_path(path: str, config: SecuritySettings | None = None) -> bool:
    """`/api/health` y la documentación quedan siempre fuera de la auth."""
    config = config or settings()
    clean = path.rstrip("/") or "/"
    for public in config.public_paths:
        target = public.rstrip("/") or "/"
        if clean == target or clean.startswith(f"{target}/"):
            return True
    return False


# ── identidad ───────────────────────────────────────────────


def key_id(key: str) -> str:
    """Identificador corto y no reversible de una key (para logs y buckets)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def resolve_identity(presented: str | None, config: SecuritySettings) -> tuple[str, bool]:
    """Devuelve ``(identidad para el bucket, key válida?)``.

    Con la auth apagada todo el mundo es ``open``; con la auth prendida, una key
    válida se identifica por su hash y cualquier otra cosa cae en ``anon`` — así
    un atacante que rota keys inválidas comparte un único bucket por IP en vez
    de estrenar cuota con cada intento.
    """
    if not config.auth_enabled:
        return "open", True
    if presented:
        for valid in config.api_keys:
            if hmac.compare_digest(presented, valid):
                return f"key:{key_id(valid)}", True
    return "anon", False


def client_ip(request: Request, config: SecuritySettings) -> str:
    if config.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return client.host if client and client.host else "unknown"


# ── rate limiting ───────────────────────────────────────────


class SlidingWindowLimiter:
    """Ventana deslizante en memoria: timestamps por bucket, sin dependencias.

    Guarda los hits de la ventana vigente en un ``deque`` por bucket. Cada
    consulta descarta los que ya salieron de la ventana, así el conteo es
    exacto (no hay el salto que tienen las ventanas fijas al cambiar de minuto).
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._since_sweep = 0

    def check(self, bucket: str, limit: int, window: float,
              now: float | None = None) -> tuple[bool, int, float]:
        """``(permitido, requests restantes, segundos hasta que se libere)``."""
        if limit <= 0:
            return True, -1, 0.0
        moment = time.monotonic() if now is None else now
        cutoff = moment - window

        with self._lock:
            hits = self._hits.setdefault(bucket, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= limit:
                retry_after = max(0.0, window - (moment - hits[0]))
                return False, 0, retry_after

            hits.append(moment)
            self._sweep(cutoff)
            return True, limit - len(hits), 0.0

    def _sweep(self, cutoff: float) -> None:
        """Descarta buckets inactivos. Cada 500 requests alcanza: mantiene la
        memoria acotada sin pagar el barrido en cada llamada."""
        self._since_sweep += 1
        if self._since_sweep < 500:
            return
        self._since_sweep = 0
        for bucket in [b for b, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            self._hits.pop(bucket, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._since_sweep = 0


#: Limitador del proceso. `reset_rate_limits()` lo vacía (tests, reload).
LIMITER = SlidingWindowLimiter()


def reset_rate_limits() -> None:
    LIMITER.reset()


# ── middleware ──────────────────────────────────────────────


def _json_error(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "source": "intelligence-api"},
        headers=headers or {},
    )


async def security_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Rate limit primero, auth después.

    El orden importa: contando también los intentos fallidos, un ataque de
    fuerza bruta contra la key se topa con el 429 antes de poder barrer el
    espacio de claves.
    """
    config = settings()

    # El preflight de CORS viaja sin headers propios: bloquearlo rompería el
    # navegador antes de que pueda mandar la key.
    if request.method == "OPTIONS":
        return await call_next(request)

    public = is_public_path(request.url.path, config)
    identity, authorized = resolve_identity(request.headers.get(API_KEY_HEADER), config)
    bucket = f"{client_ip(request, config)}|{identity}|{'public' if public else 'api'}"

    allowed, remaining, retry_after = LIMITER.check(bucket, config.rate_limit, config.window)
    if not allowed:
        seconds = max(1, int(retry_after + 0.999))
        return _json_error(429, RATE_LIMITED_DETAIL, {
            "Retry-After": str(seconds),
            "X-RateLimit-Limit": str(config.rate_limit),
            "X-RateLimit-Remaining": "0",
        })

    if not public and config.auth_enabled and not authorized:
        return _json_error(401, UNAUTHORIZED_DETAIL, {"WWW-Authenticate": API_KEY_HEADER})

    response = await call_next(request)
    if config.rate_limit_enabled:
        response.headers["X-RateLimit-Limit"] = str(config.rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
    return response


def log_security_banner(config: SecuritySettings | None = None) -> None:
    """Avisa en el arranque en qué modo quedó la API."""
    config = config or settings()
    if config.auth_enabled:
        LOGGER.info(
            "Auth ACTIVA: %d key(s) en %s; mandá el header %s en cada request "
            "(públicos: %s).",
            len(config.api_keys), ENV_API_KEY, API_KEY_HEADER, ", ".join(config.public_paths),
        )
    else:
        LOGGER.warning(
            "%s no está definida: la API queda ABIERTA, sin autenticación. Está bien en "
            "localhost, pero NO la expongas así: cualquiera puede leer todo el análisis "
            "competitivo. Definí %s y mandá la key en el header %s.",
            ENV_API_KEY, ENV_API_KEY, API_KEY_HEADER,
        )
    if config.rate_limit_enabled:
        LOGGER.info("Rate limit: %d requests / %.0fs por IP+key (%s, %s).",
                    config.rate_limit, config.window, ENV_RATE_LIMIT, ENV_RATE_LIMIT_WINDOW)
    else:
        LOGGER.warning("Rate limit DESACTIVADO (%s=0).", ENV_RATE_LIMIT)


def install_security(app: FastAPI) -> None:
    """Registra el middleware de seguridad y loguea el modo de arranque.

    Llamar ANTES de agregar el middleware de CORS: en Starlette el último
    middleware agregado queda más afuera, así las respuestas 401/429 salen con
    los headers de CORS puestos (si no, el browser ve un error opaco).
    """
    app.middleware("http")(security_middleware)
    log_security_banner()


def security_status() -> dict[str, Any]:
    """Estado de seguridad para `/api/health` (sin filtrar la key)."""
    config = settings()
    return {
        "auth_required": config.auth_enabled,
        "api_key_header": API_KEY_HEADER,
        "rate_limit": config.rate_limit,
        "rate_limit_window_seconds": config.window,
        "public_paths": list(config.public_paths),
    }
