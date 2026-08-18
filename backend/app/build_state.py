"""Estado de construcción de la base, para que `/api/health` no mienta.

Existe por un caso muy concreto del deploy en el free tier de Render: el disco
es efímero, así que en cada arranque en frío el contenedor **reconstruye** la
base (ingesta desde Supabase + pipeline, ~46 s medidos con 70.000 filas). Durante
esa ventana la base existe pero está vacía.

Sin este archivo, `/api/health` no puede distinguir dos situaciones que se ven
idénticas desde SQLite y que exigen reacciones opuestas:

* **`building`** — el motor arrancó hace 20 s y todavía está cargando. Hay que
  esperar; no hay nada que arreglar.
* **`empty`** — la construcción terminó (o nunca corrió) y no quedó nada. Acá sí
  hay un problema real: falta `DATABASE_URL`, o la ingesta falló.

El contrato es deliberadamente pobre —un archivo de texto al lado del `.db`—
para que el entrypoint del contenedor pueda escribirlo con `echo` y para que la
ausencia del archivo sea un estado válido (`None`), que es exactamente lo que
pasa cuando alguien corre el pipeline a mano en su máquina.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from app.config import DB_PATH

#: Estados que escribe `docker-entrypoint.sh`.
BUILDING = "building"
READY = "ready"
FAILED = "failed"


def state_path(db_path: str | Path | None = None) -> Path:
    """Archivo de estado que acompaña a la base: `<base>.build`.

    Va al lado del `.db` a propósito: comparten el mismo volumen (o la misma
    falta de volumen), así que nunca pueden quedar desincronizados por estar en
    discos distintos.
    """
    return Path(db_path or DB_PATH).with_suffix(".build")


def write(state: str, *, db_path: str | Path | None = None, detail: str = "") -> None:
    """Deja constancia del estado. Nunca tira: esto es telemetría, no el producto.

    Si falla la escritura (disco lleno, permisos), el motor tiene que seguir
    levantando igual — `/api/health` simplemente vuelve a no saber nada.
    """
    payload = {"state": state, "at": int(time.time()), "detail": detail}
    try:
        path = state_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def read(db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Estado actual, o `None` si nadie lo escribió (el caso de desarrollo local).

    Tolera un archivo corrupto o a medio escribir: el entrypoint puede estar
    escribiéndolo justo cuando entra un health check, y un JSON partido no
    justifica un 500 en el latido del servicio.
    """
    try:
        raw = state_path(db_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or "state" not in data:
        return None

    result: dict[str, Any] = {"state": str(data["state"])}
    if isinstance(data.get("at"), (int, float)):
        result["at"] = int(data["at"])
        result["age_seconds"] = max(0, int(time.time()) - result["at"])
    if data.get("detail"):
        result["detail"] = str(data["detail"])
    return result


def source_hint() -> str:
    """De dónde va a salir la base en este entorno, según cómo esté configurado.

    Se publica en `/api/health` porque es la pregunta que más rápido explica una
    base vacía en producción: si dice `demo`, es que falta `DATABASE_URL`.
    """
    return "supabase" if os.getenv("DATABASE_URL", "").strip() else "demo"
