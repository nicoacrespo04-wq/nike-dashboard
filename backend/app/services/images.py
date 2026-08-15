"""Pipeline de imágenes de producto: descarga, caché y embeddings visuales.

Por qué existe
--------------
El factor ``visual`` del motor de matching vale 0.15 del score competitivo, pero
hoy se apoya sólo en atributos de texto (silueta / color / material) extraídos de
descripciones. Dos vocabularios de enrichment que dicen "trainer" y "runner"
sobre la misma silueta producen un ``visual=0.0`` rotundo; por eso existe el piso
``competitive_match.visual.min_evidence_weight``. Con la imagen real y un CLIP
local ese factor pasa a medir lo que dice medir.

Tres etapas, cada una idempotente y tolerante a fallos:

1. ``ingest_images``   — ``products.image_url`` → archivo en caché de disco +
   fila en ``product_images`` (deduplicando por URL).
2. ``compute_image_embeddings`` — archivo en caché → vector CLIP (o el encoder
   sintético de verificación) → ``product_images.embedding`` (BLOB float32).
3. ``app.services.embeddings.image_similarity`` consume esos vectores.

Reglas duras (ver ``backend/CONTRACTS.md``)
-------------------------------------------
* **Cero APIs cloud.** Sólo descarga de imágenes por HTTP desde el propio
  retailer y modelos locales opcionales (CLIP/SigLIP vía ``torch`` +
  ``transformers``, que NO son dependencias obligatorias).
* **Nunca rompe el pipeline.** Sin red, sin ``httpx``, sin base, sin imágenes o
  sin ``torch``: se devuelven ceros y el resto del sistema sigue igual que hoy
  (fallback determinístico por atributos).
* **Respeta ``robots.txt``** para URLs http(s) y usa un User-Agent identificable.

Uso manual (el pipeline no lo corre solo: requiere red):

    python -m app.services.images --ingest            # descarga + registra
    python -m app.services.images --embeddings        # calcula vectores CLIP
    python -m app.services.images --synthetic-check   # verificación end-to-end offline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.robotparser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit
from urllib.request import url2pathname

import numpy as np

from app.config import BACKEND_DIR, DB_PATH, section
from app.db import get_conn
from app.services import embeddings as emb

# ── defaults técnicos ───────────────────────────────────────
# No son pesos de scoring (weights.yaml manda en eso): son límites de red.
# Todos se pueden pisar desde config con la sección `images:`.
_DEFAULT_CACHE_DIR = ".cache/images"
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_RETRIES = 2                  # => 3 intentos como máximo
_DEFAULT_BACKOFF = 0.5                # segundos, lineal
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 8 MB por imagen
# Cortacircuitos: un host caído (o un proxy que traga las conexiones) no puede
# convertir la ingesta en una espera de horas. Ver `ingest_images`.
_DEFAULT_MAX_HOST_FAILURES = 5
_DEFAULT_MAX_SECONDS = 120.0
_DEFAULT_USER_AGENT = (
    "NikeCompetitiveIntelligenceBot/1.0 (+local research crawler; respeta robots.txt)"
)
_ROBOTS_TIMEOUT = 5.0

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".ppm", ".avif"}
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

# URLs de las imágenes sintéticas de verificación (no salen a la red jamás).
SYNTHETIC_SCHEME = "synthetic"

_ROBOTS_CACHE: dict[tuple[str, str], Any] = {}


# ============================================================
# Configuración y caché en disco
# ============================================================

def _cfg(key: str, default: Any) -> Any:
    value = section("images", key, default=default)
    return default if value is None else value


def cache_dir(base: Path | str | None = None) -> Path:
    """Directorio de caché de imágenes (``backend/.cache/images`` por defecto)."""
    raw = base if base is not None else _cfg("cache_dir", _DEFAULT_CACHE_DIR)
    path = Path(str(raw))
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def url_hash(url: str) -> str:
    """Hash estable de la URL: la caché deduplica sin depender del nombre."""
    return hashlib.sha1(str(url).strip().encode("utf-8")).hexdigest()


def cache_path_for(url: str, base: Path | str | None = None) -> Path:
    """Ruta determinística en caché para una URL (no garantiza que exista)."""
    suffix = Path(urlsplit(str(url)).path).suffix.lower()
    if suffix not in _IMAGE_EXTENSIONS:
        suffix = ".img"
    return cache_dir(base) / f"{url_hash(url)}{suffix}"


def cached_image_path(url: str | None, base: Path | str | None = None) -> Path | None:
    """Devuelve la ruta en caché **sólo si el archivo ya está descargado**.

    Es el punto de entrada que usa ``embeddings.image_similarity`` para el
    camino "CLIP en vivo": nunca dispara una descarga.
    """
    if not url or not str(url).strip():
        return None
    try:
        path = cache_path_for(str(url), base)
    except (OSError, ValueError):
        return None
    try:
        if path.exists() and path.stat().st_size > 0:
            return path
    except OSError:
        return None
    return None


# ============================================================
# robots.txt
# ============================================================

def _robots_parser(scheme: str, netloc: str) -> Any | None:
    """Parser de robots.txt cacheado por host. ``None`` si no se pudo leer."""
    key = (scheme, netloc)
    if key in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[key]

    parser = None
    try:
        import httpx  # type: ignore
    except ImportError:
        _ROBOTS_CACHE[key] = None
        return None

    try:
        response = httpx.get(
            f"{scheme}://{netloc}/robots.txt",
            timeout=_ROBOTS_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": str(_cfg("user_agent", _DEFAULT_USER_AGENT))},
        )
        if response.status_code == 200 and response.text:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(response.text.splitlines())
    except Exception:  # noqa: BLE001 - sin robots legible seguimos las reglas por defecto
        parser = None

    _ROBOTS_CACHE[key] = parser
    return parser


def robots_allows(url: str, user_agent: str | None = None) -> bool:
    """¿``robots.txt`` permite bajar esta URL?

    Sólo aplica a http(s). Si el host no publica robots.txt (o no se puede
    leer), se asume permitido — que es el comportamiento estándar de un crawler.
    """
    parts = urlsplit(str(url))
    if parts.scheme not in {"http", "https"}:
        return True
    if not bool(_cfg("respect_robots", True)):
        return True
    parser = _robots_parser(parts.scheme, parts.netloc)
    if parser is None:
        return True
    agent = user_agent or str(_cfg("user_agent", _DEFAULT_USER_AGENT))
    try:
        return bool(parser.can_fetch(agent, str(url)))
    except Exception:  # noqa: BLE001
        return True


def reset_robots_cache() -> None:
    """Olvida los robots.txt cacheados (tests)."""
    _ROBOTS_CACHE.clear()


# ============================================================
# Descarga
# ============================================================

def _is_remote(url: str) -> bool:
    """¿Bajar esta URL cuesta red? (``file://`` y locales, no)."""
    return urlsplit(str(url)).scheme.lower() in {"http", "https"}


def _local_source(url: str) -> Path | None:
    """Resuelve ``file://`` (y rutas absolutas sueltas) a un Path existente."""
    parts = urlsplit(url)
    if parts.scheme == "file":
        try:
            path = Path(url2pathname(parts.path))
        except Exception:  # noqa: BLE001
            return None
    elif not parts.scheme:
        path = Path(url)
    else:
        return None
    try:
        return path if path.exists() and path.is_file() else None
    except OSError:
        return None


def _write_atomic(target: Path, payload: bytes) -> Path | None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        return target
    except OSError:
        return None


def _download(url: str, target: Path) -> Path | None:
    """Descarga con timeout y reintentos acotados. Nunca lanza."""
    try:
        import httpx  # type: ignore
    except ImportError:
        return None                    # sin cliente HTTP => no hay descarga, y está bien

    timeout = float(_cfg("timeout_seconds", _DEFAULT_TIMEOUT))
    retries = max(0, int(_cfg("max_retries", _DEFAULT_RETRIES)))
    backoff = float(_cfg("backoff_seconds", _DEFAULT_BACKOFF))
    max_bytes = int(_cfg("max_bytes", _DEFAULT_MAX_BYTES))
    headers = {"User-Agent": str(_cfg("user_agent", _DEFAULT_USER_AGENT))}

    for attempt in range(retries + 1):
        try:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=True,
                              headers=headers) as response:
                if response.status_code >= 400:
                    if response.status_code in _RETRY_STATUS and attempt < retries:
                        time.sleep(backoff * (attempt + 1))
                        continue
                    return None
                content_type = str(response.headers.get("content-type", "")).lower()
                if content_type and not (
                    content_type.startswith("image/")
                    or "octet-stream" in content_type
                ):
                    return None        # no es una imagen: no reintentar
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        return None    # imagen desproporcionada: se descarta
                    chunks.append(chunk)
            payload = b"".join(chunks)
            if not payload:
                return None
            return _write_atomic(target, payload)
        except Exception:  # noqa: BLE001 - red caída, DNS, TLS, timeout...
            if attempt < retries:
                try:
                    time.sleep(backoff * (attempt + 1))
                except Exception:  # noqa: BLE001
                    pass
                continue
            return None
    return None


def fetch_image(url: str, cache_dir: Path | None = None) -> Path | None:
    """Devuelve la ruta local de la imagen, descargándola si hace falta.

    * Caché por hash de URL: la misma URL no se baja dos veces (ni entre corridas).
    * ``file://`` y rutas locales se resuelven sin copiar nada.
    * Respeta ``robots.txt`` en http(s).
    * Ante cualquier problema (sin red, sin ``httpx``, 404, timeout, robots
      prohíbe, contenido que no es imagen) devuelve ``None`` sin lanzar.
    """
    if not url or not str(url).strip():
        return None
    url = str(url).strip()

    try:
        target = cache_path_for(url, cache_dir)
    except (OSError, ValueError):
        return None

    try:
        if target.exists() and target.stat().st_size > 0:
            return target              # ya cacheada: dedupe entre corridas
    except OSError:
        return None

    local = _local_source(url)
    if local is not None:
        return local

    scheme = urlsplit(url).scheme.lower()
    if scheme not in {"http", "https"}:
        return None                    # synthetic:// vive sólo en caché

    if not robots_allows(url):
        return None

    return _download(url, target)


# ============================================================
# Persistencia en product_images
# ============================================================

def _db_ready(db_path: Path | str) -> bool:
    """¿La base existe y tiene las tablas que necesitamos?"""
    path = Path(db_path)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return {"products", "product_images"} <= names


def _existing_rows(conn: sqlite3.Connection) -> dict[tuple[int, str], dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, product_id, url, is_primary FROM product_images"
    ).fetchall()
    return {(int(r["product_id"]), str(r["url"] or "")): dict(r) for r in rows}


def ingest_images(db_path: Path | str = DB_PATH, *, limit: int | None = None) -> dict[str, int]:
    """Descarga las imágenes de ``products.image_url`` y las registra.

    Devuelve conteos (``dict[str, int]``). **Nunca lanza**: sin red, sin base o
    sin URLs devuelve todo en cero y el pipeline sigue exactamente igual.
    """
    counts = {
        "products_with_url": 0, "unique_urls": 0, "downloaded": 0, "cached": 0,
        "failed": 0, "rows_inserted": 0, "rows_existing": 0,
        "skipped_dead_host": 0, "skipped_deadline": 0,
    }
    if not _db_ready(db_path):
        return counts

    try:
        with get_conn(db_path) as conn:
            products = [
                dict(r) for r in conn.execute(
                    "SELECT id, image_url FROM products "
                    "WHERE image_url IS NOT NULL AND TRIM(image_url) <> '' "
                    "ORDER BY id"
                ).fetchall()
            ]
            if limit is not None:
                products = products[: max(0, int(limit))]
            counts["products_with_url"] = len(products)
            if not products:
                return counts

            existing = _existing_rows(conn)
            primaries = {pid for (pid, _u), row in existing.items() if row.get("is_primary")}
            resolved: dict[str, Path | None] = {}     # dedupe por URL dentro de la corrida

            # Sin red, cada URL cuesta el timeout completo. Con un catálogo real
            # eso son horas: cortamos por host caído y por tiempo total.
            max_host_failures = max(1, int(_cfg("max_host_failures", _DEFAULT_MAX_HOST_FAILURES)))
            deadline = time.monotonic() + float(_cfg("max_seconds", _DEFAULT_MAX_SECONDS))
            host_failures: dict[str, int] = {}

            seen_urls: set[str] = set()
            for product in products:
                pid = int(product["id"])
                url = str(product["image_url"]).strip()
                seen_urls.add(url)

                if url in resolved:
                    path = resolved[url]
                elif cached_image_path(url) is not None:
                    path = fetch_image(url)              # sale de la caché, no toca la red
                    resolved[url] = path
                    counts["cached"] += 1
                # El cortacircuitos sólo aplica a lo que cuesta red: un archivo
                # local que falta se resuelve al instante.
                elif _is_remote(url) and time.monotonic() >= deadline:
                    counts["skipped_deadline"] += 1
                    continue
                elif _is_remote(url) and host_failures.get(urlsplit(url).netloc, 0) >= max_host_failures:
                    counts["skipped_dead_host"] += 1
                    continue
                else:
                    host = urlsplit(url).netloc
                    path = fetch_image(url)
                    resolved[url] = path
                    if path is None:
                        counts["failed"] += 1
                        host_failures[host] = host_failures.get(host, 0) + 1
                    else:
                        counts["downloaded"] += 1
                        host_failures[host] = 0

                if path is None:
                    continue
                if (pid, url) in existing:
                    counts["rows_existing"] += 1
                    continue

                is_primary = 0 if pid in primaries else 1
                conn.execute(
                    "INSERT INTO product_images (product_id, url, is_primary) VALUES (?,?,?)",
                    (pid, url, is_primary),
                )
                primaries.add(pid)
                existing[(pid, url)] = {"product_id": pid, "url": url, "is_primary": is_primary}
                counts["rows_inserted"] += 1

            counts["unique_urls"] = len(seen_urls)
    except Exception:  # noqa: BLE001 - la ingesta de imágenes nunca frena el pipeline
        return counts

    emb.reset_image_index()
    return counts


# ============================================================
# Embeddings
# ============================================================

def _resolve_encoder(model: str | None) -> tuple[Callable[[Path], np.ndarray | None], str] | None:
    """Elige el encoder de imagen y devuelve ``(función, nombre_del_modelo)``.

    * ``model="pixel"`` / ``"synthetic"`` fuerza el encoder determinístico por
      píxeles (numpy puro, sin descargas): es el modo de verificación.
    * Cualquier otro valor intenta CLIP local. Si ``torch``/``transformers`` no
      están o el modelo no está cacheado, devuelve ``None`` (no se inventa un
      embedding: se deja el fallback por atributos, que es honesto).
    """
    requested = (model or "").strip().lower()
    if requested in {"pixel", "synthetic", emb.SYNTHETIC_MODEL}:
        return emb.synthetic_image_vector, emb.SYNTHETIC_MODEL

    model_name = model or str(section("embeddings", "image", "model",
                                      default="openai/clip-vit-base-patch32"))
    if emb.clip_available(model_name):
        return (lambda path: emb.clip_image_vector(path, model=model_name)), model_name

    # Sin CLIP: sólo se usa el encoder sintético si está explícitamente permitido.
    if bool(section("embeddings", "image", "allow_pixel_fallback", default=False)):
        return emb.synthetic_image_vector, emb.SYNTHETIC_MODEL
    return None


def compute_image_embeddings(db_path: Path | str = DB_PATH, *,
                             model: str | None = None,
                             force: bool = False) -> dict[str, int]:
    """Calcula y persiste los embeddings de las imágenes ya descargadas.

    Sólo toca filas cuya imagen está en caché de disco y cuyo embedding falta o
    fue calculado con otro modelo (``force=True`` recalcula todo). Sin encoder
    disponible devuelve ceros.
    """
    counts = {
        "images": 0, "encoded": 0, "up_to_date": 0, "missing_file": 0,
        "failed": 0, "clip_available": 0,
    }
    if not _db_ready(db_path):
        return counts

    encoder_info = _resolve_encoder(model)
    if encoder_info is None:
        # Igual reportamos cuántas imágenes hay esperando un modelo.
        try:
            with get_conn(db_path) as conn:
                counts["images"] = int(
                    conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0]
                )
        except Exception:  # noqa: BLE001
            pass
        return counts

    encoder, model_name = encoder_info
    counts["clip_available"] = int(model_name != emb.SYNTHETIC_MODEL)

    try:
        with get_conn(db_path) as conn:
            rows = [
                dict(r) for r in conn.execute(
                    "SELECT id, product_id, url, embedding, embedding_model, embedding_dim "
                    "FROM product_images ORDER BY id"
                ).fetchall()
            ]
            counts["images"] = len(rows)

            for row in rows:
                if not force and row["embedding"] is not None and row["embedding_model"] == model_name:
                    counts["up_to_date"] += 1
                    continue

                path = cached_image_path(row["url"]) or _local_source(str(row["url"] or ""))
                if path is None:
                    counts["missing_file"] += 1
                    continue

                try:
                    vector = encoder(path)
                except Exception:  # noqa: BLE001 - un modelo roto no frena el resto
                    vector = None
                if vector is None or getattr(vector, "size", 0) == 0:
                    counts["failed"] += 1
                    continue

                vector = np.asarray(vector, dtype=np.float32).reshape(-1)
                norm = float(np.linalg.norm(vector))
                if norm > 0:
                    vector = (vector / norm).astype(np.float32)

                conn.execute(
                    "UPDATE product_images SET embedding = ?, embedding_model = ?, embedding_dim = ? "
                    "WHERE id = ?",
                    (emb.pack(vector), model_name, int(vector.shape[0]), int(row["id"])),
                )
                counts["encoded"] += 1
    except Exception:  # noqa: BLE001
        return counts

    emb.reset_image_index()
    return counts


# ============================================================
# Modo de verificación: imágenes sintéticas locales
# ============================================================

def generate_synthetic_images(db_path: Path | str = DB_PATH, *,
                              limit: int | None = None,
                              cache_dir: Path | None = None) -> dict[str, int]:
    """Genera imágenes deterministas por producto y las registra.

    Permite ejercitar el camino completo (archivo → encoder → BLOB →
    ``image_similarity``) **sin descargar nada y sin red**. La imagen se
    construye a partir de los atributos visuales del producto (silueta, color),
    así que dos productos "parecidos" producen imágenes parecidas y el score
    visual resultante es verificable.

    No pisa datos reales: sólo inserta filas ``synthetic://`` para productos que
    todavía no tienen una imagen registrada.
    """
    counts = {"products": 0, "rendered": 0, "rows_inserted": 0, "skipped_existing": 0}
    if not _db_ready(db_path):
        return counts

    try:
        with get_conn(db_path) as conn:
            products = [
                dict(r) for r in conn.execute(
                    "SELECT id, product_name, category, subcategory, franchise, "
                    "performance_vs_lifestyle FROM products ORDER BY id"
                ).fetchall()
            ]
            if limit is not None:
                products = products[: max(0, int(limit))]
            counts["products"] = len(products)
            if not products:
                return counts

            attrs: dict[int, dict[str, Any]] = {}
            for row in conn.execute(
                "SELECT product_id, attr_name, value_text FROM product_attributes"
            ).fetchall():
                attrs.setdefault(int(row["product_id"]), {})[row["attr_name"]] = row["value_text"]

            existing = {int(r["product_id"]) for r in
                        conn.execute("SELECT DISTINCT product_id FROM product_images").fetchall()}

            for product in products:
                pid = int(product["id"])
                if pid in existing:
                    counts["skipped_existing"] += 1
                    continue

                url = f"{SYNTHETIC_SCHEME}://product/{pid}"
                target = cache_path_for(url, cache_dir)
                if not (target.exists() and target.stat().st_size > 0):
                    payload = emb.render_synthetic_image(
                        {**product, "attributes": attrs.get(pid, {})}
                    )
                    if _write_atomic(target, payload) is None:
                        continue
                counts["rendered"] += 1

                conn.execute(
                    "INSERT INTO product_images (product_id, url, is_primary) VALUES (?,?,1)",
                    (pid, url),
                )
                counts["rows_inserted"] += 1
    except Exception:  # noqa: BLE001
        return counts

    emb.reset_image_index()
    return counts


def run_synthetic_check(db_path: Path | str = DB_PATH, *,
                        limit: int | None = None) -> dict[str, Any]:
    """Verificación end-to-end del camino visual, 100% offline.

    Genera imágenes sintéticas, calcula sus embeddings con el encoder
    determinístico (o con CLIP si está instalado), recarga el índice y mide un
    par real de la base. Devuelve un reporte legible.
    """
    report: dict[str, Any] = {"generated": {}, "embedded": {}, "sample": None}
    report["generated"] = generate_synthetic_images(db_path, limit=limit)
    model = None if emb.clip_available() else "pixel"
    report["embedded"] = compute_image_embeddings(db_path, model=model)
    report["model"] = model or str(section("embeddings", "image", "model", default="clip"))

    emb.load_image_index(db_path)
    try:
        with get_conn(db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT p.*, b.is_focus FROM products p JOIN brands b ON b.id = p.brand_id "
                "ORDER BY b.is_focus DESC, p.id"
            ).fetchall()]
    except Exception:  # noqa: BLE001
        rows = []

    # El motor exige que `products.image_url` coincida con la URL registrada en
    # `product_images` (así el índice nunca adivina por id). En la verificación
    # apuntamos el dict a la imagen sintética: es exactamente lo que vería el
    # motor si el scraper hubiese poblado `image_url` con una imagen real.
    index = emb.image_index()
    for product in rows:
        entry = index["by_product"].get(int(product["id"]))
        if entry and str(entry[0]["url"]).startswith(f"{SYNTHETIC_SCHEME}://"):
            product["image_url"] = entry[0]["url"]

    pairs = [(a, b) for a in rows[:1] for b in rows[1:6]]
    for a, b in pairs:
        score, method = emb.image_similarity(a, b)
        if method.startswith("clip") or method.endswith("persisted"):
            report["sample"] = {
                "nike": a.get("product_name"), "competitor": b.get("product_name"),
                "score": score, "method": method,
            }
            break
    report["index_size"] = index["size"]
    return report


# ============================================================
# CLI
# ============================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline de imágenes de producto")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=None,
                        help="modelo de imagen ('pixel' fuerza el encoder sintético)")
    parser.add_argument("--ingest", action="store_true", help="descargar y registrar imágenes")
    parser.add_argument("--embeddings", action="store_true", help="calcular embeddings")
    parser.add_argument("--synthetic-check", action="store_true",
                        help="verificación end-to-end con imágenes sintéticas (sin red)")
    args = parser.parse_args(argv)

    if not (args.ingest or args.embeddings or args.synthetic_check):
        args.ingest = args.embeddings = True

    report: dict[str, Any] = {"backend_imagen": emb.image_backend_name()}
    if args.ingest:
        report["ingest"] = ingest_images(args.db, limit=args.limit)
    if args.embeddings:
        report["embeddings"] = compute_image_embeddings(args.db, model=args.model)
    if args.synthetic_check:
        report["synthetic_check"] = run_synthetic_check(args.db, limit=args.limit)

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
