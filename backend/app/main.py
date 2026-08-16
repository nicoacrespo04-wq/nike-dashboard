"""Competitive & Consumer Intelligence Decision Engine — API.

Levantar con:
    cd backend && uvicorn app.main:app --reload --port 8000

Seguridad (detalle completo en `backend/docs/api.md`):
  * `CI_API_KEY`  ⇒ exige el header `X-API-Key` en todo lo que no sea
    `/api/health` ni la documentación. Sin la variable, la API queda abierta
    como siempre y se avisa por log al arrancar.
  * `CI_RATE_LIMIT` / `CI_RATE_LIMIT_WINDOW` ⇒ ventana deslizante por IP+key.
  * `CI_CORS_ORIGINS` ⇒ orígenes permitidos para el browser.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (brand, history, matches, opportunities, overview,
                             products, retail_media, triage)
from app.auth import API_KEY_HEADER, install_security
from app.config import get_config, reload_config

#: Orígenes que pueden pegarle desde un browser. En producción el dashboard va
#: por el proxy server-side de Next (`/api/intelligence/...`), no directo: la
#: key vive sólo en el servidor. Por eso el default es localhost — el valor de
#: producción se setea a mano y, si nadie le pega desde el browser, puede
#: quedar vacío (`CI_CORS_ORIGINS=""` ⇒ sin orígenes permitidos).
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "CI_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",") if o.strip()
]
ALLOWED_METHODS = [
    m.strip().upper() for m in os.getenv("CI_CORS_METHODS", "GET,HEAD,OPTIONS").split(",")
    if m.strip()
]

app = FastAPI(
    title="Competitive & Consumer Intelligence Decision Engine",
    description=(
        "Convierte datos de retailers, marcas, reviews, medios y señales sociales "
        "públicas en decisiones comerciales explicables."
    ),
    version=get_config().get("version", "0.0.0"),
)

# Orden deliberado: seguridad primero, CORS después. Starlette ejecuta el
# último middleware agregado por fuera del resto, así el 401/429 sale con los
# headers de CORS y el browser puede leer el error en vez de ver algo opaco.
install_security(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=ALLOWED_METHODS,
    # `X-API-Key` tiene que estar permitido explícitamente si alguna vez se le
    # pega desde el browser; `*` ya lo cubre.
    allow_headers=["*", API_KEY_HEADER],
)

for router in (overview.router, products.router, matches.router,
               opportunities.router, retail_media.router, brand.router,
               history.router, triage.router):
    app.include_router(router)


@app.get("/api/config", tags=["config"])
def get_scoring_config() -> dict[str, Any]:
    """Configuración de scoring vigente.

    La expone la API a propósito: los pesos son parte del producto — el usuario
    tiene que poder ver con qué criterio se calculó cada score.
    """
    return reload_config()
