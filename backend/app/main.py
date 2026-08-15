"""Competitive & Consumer Intelligence Decision Engine — API.

Levantar con:
    cd backend && uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import brand, matches, opportunities, overview, products, retail_media
from app.config import get_config, reload_config

ALLOWED_ORIGINS = os.getenv(
    "CI_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")

app = FastAPI(
    title="Competitive & Consumer Intelligence Decision Engine",
    description=(
        "Convierte datos de retailers, marcas, reviews, medios y señales sociales "
        "públicas en decisiones comerciales explicables."
    ),
    version=get_config().get("version", "0.0.0"),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS if o.strip()],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

for router in (overview.router, products.router, matches.router,
               opportunities.router, retail_media.router, brand.router):
    app.include_router(router)


@app.get("/api/config", tags=["config"])
def get_scoring_config() -> dict[str, Any]:
    """Configuración de scoring vigente.

    La expone la API a propósito: los pesos son parte del producto — el usuario
    tiene que poder ver con qué criterio se calculó cada score.
    """
    return reload_config()
