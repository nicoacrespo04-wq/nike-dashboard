"""Acceso a SQLite.

MVP sobre SQLite por velocidad. El esquema es portable a Postgres:
no se usan features exclusivas más allá de ``datetime('now')``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR, DB_PATH

SCHEMA_PATH = BACKEND_DIR / "app" / "schema.sql"


def _connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn(path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Conexión transaccional: commit al salir, rollback si hay excepción."""
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path | str = DB_PATH, *, drop: bool = False) -> None:
    """Crea el esquema. Con ``drop=True`` borra el archivo primero."""
    path = Path(path)
    if drop and path.exists():
        path.unlink()
    with get_conn(path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def query(sql: str, params: Iterable[Any] = (), path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Ejecuta un SELECT y devuelve filas como dicts."""
    with get_conn(path) as conn:
        return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = (), path: Path | str = DB_PATH) -> dict[str, Any] | None:
    rows = query(sql, params, path)
    return rows[0] if rows else None
