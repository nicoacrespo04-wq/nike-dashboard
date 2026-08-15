"""Capa de adquisición de datos reales para el motor de inteligencia.

Reemplaza los datos de muestra de ``app/seed.py`` en las dos señales que hoy
dependen de ellos:

  * ``editorial_mentions``          -> factor ``editorial`` (peso 0.15)
  * ``social_mention_aggregates``   -> factor ``social`` (peso 0.10) y todo el
    módulo de Consumer & Brand Intelligence AR

Uso programático::

    from app.collectors import run_collectors
    run_collectors(db_path, names=["editorial_fixture"], dry_run=True)

Uso por CLI::

    python -m app.collectors --list
    python -m app.collectors --source editorial_fixture --since 2026-01-01
    python -m app.collectors --dry-run

Agregar una fuente nueva: crear el colector (o instanciar
``FeedEditorialCollector`` / ``PublicApiSocialCollector`` con su
``SourcePolicy``) y llamar a ``register()``. Nada más del backend cambia.
"""

from __future__ import annotations

from pathlib import Path

from app.collectors.base import (  # noqa: F401 - API pública del paquete
    ACCESS_API,
    ACCESS_FEED,
    ACCESS_FIXTURE,
    ACCESS_PROHIBITED,
    ACCESS_REVIEW,
    BaseCollector,
    Collector,
    DisabledCollector,
    FixtureCollector,
    SourcePolicy,
    catalog,
    clear_registry,
    get_collector,
    persist,
    register,
    registered,
    run_collectors,
    unregister,
)
from app.config import DB_PATH

__all__ = [
    "ACCESS_API", "ACCESS_FEED", "ACCESS_FIXTURE", "ACCESS_PROHIBITED", "ACCESS_REVIEW",
    "BaseCollector", "Collector", "DisabledCollector", "FixtureCollector", "SourcePolicy",
    "catalog", "clear_registry", "get_collector", "persist", "register", "registered",
    "register_builtin_collectors", "run_collectors", "unregister",
]


def register_builtin_collectors(db_path: Path | str = DB_PATH) -> None:
    """Registra los colectores incluidos (editoriales y sociales)."""
    from app.collectors import editorial, social

    editorial.register_collectors(db_path)
    social.register_collectors(db_path)


register_builtin_collectors()
