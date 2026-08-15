"""Carga de configuración de scoring.

Todos los pesos y umbrales viven en backend/config/weights.yaml.
Ningún módulo debe hardcodear pesos: siempre pedirlos acá.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CONFIG_PATH = Path(os.getenv("CI_CONFIG_PATH", BACKEND_DIR / "config" / "weights.yaml"))
DATA_DIR = Path(os.getenv("CI_DATA_DIR", BACKEND_DIR / "data"))
DB_PATH = Path(os.getenv("CI_DB_PATH", DATA_DIR / "processed" / "intelligence.db"))


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Devuelve la configuración completa (cacheada)."""
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def reload_config() -> dict[str, Any]:
    """Invalida la cache y recarga desde disco (útil en tests y al tunear pesos)."""
    get_config.cache_clear()
    return get_config()


def section(*keys: str, default: Any = None) -> Any:
    """Acceso anidado seguro: ``section("competitive_match", "weights")``."""
    node: Any = get_config()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def weights(*keys: str) -> dict[str, float]:
    """Atajo para leer un bloque de pesos como dict[str, float]."""
    raw = section(*keys, default={}) or {}
    return {k: float(v) for k, v in raw.items()}
