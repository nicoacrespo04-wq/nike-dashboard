"""Vectores y similitudes locales (texto e imagen).

Restricción dura del proyecto: **cero llamadas de red obligatorias y cero APIs
de LLM cloud**. Todo camino por defecto es determinístico y offline.

Texto
    * ``sentence-transformers`` si está instalado y el modelo carga sin red.
    * Fallback (camino por defecto acá): TF-IDF de scikit-learn combinando
      n-gramas de palabra y de carácter, con similitud coseno.

Imagen — cascada de decisión de ``image_similarity`` (de más fuerte a más débil):

    1. ``clip``                  vectores que ya vienen en los dicts recibidos.
    2. ``clip-persisted`` /      vectores guardados en ``product_images.embedding``
       ``embedding-persisted``   (los calcula ``app.services.images``). No se recalcula nada.
    3. ``clip-live``             CLIP local sobre una imagen **ya cacheada en disco**.
    4. ``attribute-fallback``    comparación determinística de atributos visuales
                                 (silhouette, colores, materiales, suela, estilo).
    5. ``unavailable``           sin ninguna evidencia visual -> ``(None, 'unavailable')``.

Nada de esto sale a la red ni usa APIs cloud: CLIP se carga con
``local_files_only`` y las imágenes las descarga ``app.services.images`` (con
caché, robots.txt y degradación a cero si no hay conectividad).
``torch``/``transformers`` son OPCIONALES: sin ellos el camino por defecto es
exactamente el de siempre (fallback por atributos).

Degradación elegante: si falta el dato, se devuelve ``None`` en vez de asumir 0.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

from app.config import BACKEND_DIR, DB_PATH, section, weights
from app.services.common import Factor, clamp, combine, jaccard

# ── parámetros del vectorizador TF-IDF ──────────────────────
# No son pesos de scoring (weights.yaml manda en eso): son límites técnicos
# para que la matriz densa no explote en memoria.
_WORD_NGRAMS = (1, 2)
_CHAR_NGRAMS = (3, 5)
_WORD_MAX_FEATURES = 4096
_CHAR_MAX_FEATURES = 8192

# La cache en disco sólo tiene sentido para corpus (no para pares sueltos:
# esos ya viven en la cache de similitudes en memoria).
_DISK_CACHE_MIN_BATCH = 8
_MAX_MEMORY_BATCHES = 2048

# Nombres canónicos de backend
_BACKEND_ST = "sentence_transformers"
_BACKEND_TFIDF = "tfidf"
_METHOD_CLIP = "clip"                       # embedding traído en el propio dict
_METHOD_CLIP_PERSISTED = "clip-persisted"   # embedding CLIP leído de product_images
_METHOD_EMB_PERSISTED = "embedding-persisted"  # ídem, con otro modelo (ej. verificación)
_METHOD_CLIP_LIVE = "clip-live"             # CLIP calculado al vuelo sobre archivo local
_METHOD_ATTRS = "attribute-fallback"
_METHOD_NONE = "unavailable"

# Modelo del encoder determinístico de verificación (numpy puro, sin descargas).
SYNTHETIC_MODEL = "pixel-hist-v1"

# Tags que NO son un embedding visual real: si aparecen en `embedding_model`
# el vector se ignora y se sigue bajando por la cascada.
_PLACEHOLDER_MODELS = {_METHOD_ATTRS, "attribute_fallback", "placeholder"}

# Prioridad de las fuentes de vector (mayor = más abajo en la cascada).
_SOURCE_RANK = {"dict": 0, "persisted": 1, "live": 2}

# Tamaño de la grilla del encoder sintético (grid*grid*3 + histograma).
_PIXEL_GRID = 8
_PIXEL_HIST_BINS = 8
_SYNTHETIC_IMAGE_SIZE = 96

# ── caches en memoria ───────────────────────────────────────
_BACKEND: str | None = None
_ST_MODEL: Any = None
_ST_TRIED = False

_BATCH_CACHE: dict[str, np.ndarray] = {}       # matriz por lote (clave = hash del lote)
_TEXT_CACHE: dict[str, np.ndarray] = {}        # vector por texto (sólo backends estables)
_SIM_CACHE: dict[tuple[str, str], float | None] = {}

_CLIP_RUNTIME: Any = None                      # (model, processor, torch, Image)
_CLIP_TRIED = False
_CLIP_MODEL_NAME: str | None = None
_CLIP_STATUS: str = "no evaluado"
_IMAGE_VEC_CACHE: dict[str, np.ndarray | None] = {}   # ruta local -> vector CLIP
_IMAGE_INDEX: dict[str, Any] | None = None            # product_images cacheado en memoria
_IMAGE_INDEX_SOURCE: str | None = None


# ============================================================
# Utilidades de texto
# ============================================================

def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def _normalize_text(value: Any) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios colapsados."""
    if value is None:
        return ""
    text = _strip_accents(str(value)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _batch_key(texts: list[str], backend: str) -> str:
    digest = hashlib.sha1(("\x00".join(texts)).encode("utf-8")).hexdigest()
    return f"{backend}-{digest}"


# ── cache en disco (opcional, embeddings.cache_dir) ──────────

def _cache_dir() -> Path | None:
    raw = section("embeddings", "cache_dir", default=None)
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def _disk_load(key: str) -> np.ndarray | None:
    directory = _cache_dir()
    if directory is None:
        return None
    path = directory / f"{key}.npy"
    try:
        if path.exists():
            return np.load(path)
    except (OSError, ValueError):
        return None
    return None


def _disk_store(key: str, matrix: np.ndarray) -> None:
    directory = _cache_dir()
    if directory is None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / f"{key}.npy", matrix)
    except (OSError, ValueError):
        pass  # la cache es un lujo, nunca un requisito


# ============================================================
# Backend de texto
# ============================================================

def _load_sentence_transformer() -> Any:
    """Carga perezosa del modelo local. Nunca obliga a tener red."""
    global _ST_MODEL, _ST_TRIED
    if _ST_TRIED:
        return _ST_MODEL
    _ST_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        _ST_MODEL = None
        return None
    model_name = str(section("embeddings", "text", "model",
                             default="sentence-transformers/all-MiniLM-L6-v2"))
    try:
        # Si el modelo no está cacheado localmente esto necesitaría red:
        # ante cualquier falla caemos a TF-IDF sin romper el pipeline.
        _ST_MODEL = SentenceTransformer(model_name)
    except Exception:  # noqa: BLE001 - degradación elegante ante cualquier fallo
        _ST_MODEL = None
    return _ST_MODEL


def _resolve_backend() -> str:
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    configured = str(section("embeddings", "text", "backend", default="auto") or "auto")
    configured = configured.strip().lower().replace("-", "_")
    if configured == _BACKEND_TFIDF or configured == "tf_idf":
        _BACKEND = _BACKEND_TFIDF
    elif configured in {"auto", _BACKEND_ST}:
        _BACKEND = _BACKEND_ST if _load_sentence_transformer() is not None else _BACKEND_TFIDF
    else:
        _BACKEND = _BACKEND_TFIDF
    return _BACKEND


def backend_name() -> str:
    """Backend de texto realmente activo: ``sentence_transformers`` | ``tfidf``."""
    return _resolve_backend()


def reset_cache() -> None:
    """Limpia caches en memoria y la resolución de backend (útil en tests)."""
    global _BACKEND, _ST_MODEL, _ST_TRIED
    global _CLIP_RUNTIME, _CLIP_TRIED, _CLIP_MODEL_NAME, _CLIP_STATUS
    _BACKEND = None
    _ST_MODEL = None
    _ST_TRIED = False
    _BATCH_CACHE.clear()
    _TEXT_CACHE.clear()
    _SIM_CACHE.clear()
    _CLIP_RUNTIME = None
    _CLIP_TRIED = False
    _CLIP_MODEL_NAME = None
    _CLIP_STATUS = "no evaluado"
    _IMAGE_VEC_CACHE.clear()
    reset_image_index()


# ============================================================
# Vectores de texto
# ============================================================

def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0          # los vectores nulos quedan nulos, no NaN
    return (matrix / norms).astype(np.float32)


def _tfidf_matrix(texts: list[str]) -> np.ndarray:
    """TF-IDF palabra + carácter concatenados (cada bloque ya L2-normalizado)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    blocks: list[np.ndarray] = []
    for analyzer, ngram_range, max_features in (
        ("word", _WORD_NGRAMS, _WORD_MAX_FEATURES),
        ("char_wb", _CHAR_NGRAMS, _CHAR_MAX_FEATURES),
    ):
        vectorizer = TfidfVectorizer(
            analyzer=analyzer,
            ngram_range=ngram_range,
            max_features=max_features,
            sublinear_tf=True,
            lowercase=False,          # ya normalizamos nosotros
            dtype=np.float32,
        )
        try:
            block = vectorizer.fit_transform(texts).toarray()
        except ValueError:
            continue                  # vocabulario vacío para ese analizador
        blocks.append(np.asarray(block, dtype=np.float32))

    if not blocks:
        return np.zeros((len(texts), 1), dtype=np.float32)
    return np.hstack(blocks)


def _st_matrix(texts: list[str]) -> np.ndarray:
    model = _load_sentence_transformer()
    if model is None:
        return _tfidf_matrix(texts)
    pending = [t for t in dict.fromkeys(texts) if t and t not in _TEXT_CACHE]
    if pending:
        encoded = np.asarray(model.encode(pending, normalize_embeddings=True), dtype=np.float32)
        for text, vector in zip(pending, encoded):
            _TEXT_CACHE[text] = vector
    dim = next((v.shape[0] for v in _TEXT_CACHE.values()), 1)
    return np.vstack([
        _TEXT_CACHE.get(t, np.zeros(dim, dtype=np.float32)) for t in texts
    ]).astype(np.float32)


def text_vectors(texts: list[str]) -> np.ndarray:
    """Matriz ``(n, d)`` con las filas L2-normalizadas.

    Con TF-IDF el vocabulario se ajusta sobre el lote recibido, así que la
    dimensión ``d`` depende del lote (los vectores sólo son comparables entre
    filas de una misma llamada). Un texto vacío produce una fila nula.
    """
    prepared = [_normalize_text(t) for t in (texts or [])]
    if not prepared:
        return np.zeros((0, 1), dtype=np.float32)

    backend = _resolve_backend()
    key = _batch_key(prepared, backend)
    cached = _BATCH_CACHE.get(key)
    if cached is None:
        cached = _disk_load(key)
        if cached is not None:
            _BATCH_CACHE[key] = cached
    if cached is not None:
        return cached

    if not any(prepared):
        matrix = np.zeros((len(prepared), 1), dtype=np.float32)
    elif backend == _BACKEND_ST:
        matrix = _l2_normalize(_st_matrix(prepared))
    else:
        matrix = _l2_normalize(_tfidf_matrix(prepared))

    if len(_BATCH_CACHE) >= _MAX_MEMORY_BATCHES:      # FIFO simple, sin dependencias
        _BATCH_CACHE.pop(next(iter(_BATCH_CACHE)))
    _BATCH_CACHE[key] = matrix
    if len(prepared) >= _DISK_CACHE_MIN_BATCH:
        _disk_store(key, matrix)
    return matrix


def text_similarity(a: str | None, b: str | None) -> float | None:
    """Similitud coseno 0..1. ``None`` si falta texto en alguno de los lados."""
    if a is None or b is None:
        return None
    norm_a, norm_b = _normalize_text(a), _normalize_text(b)
    if not norm_a or not norm_b:
        return None
    if norm_a == norm_b:
        return 1.0

    key = (norm_a, norm_b) if norm_a <= norm_b else (norm_b, norm_a)
    if key in _SIM_CACHE:
        return _SIM_CACHE[key]

    matrix = text_vectors([norm_a, norm_b])
    if matrix.shape[0] < 2 or np.linalg.norm(matrix[0]) == 0 or np.linalg.norm(matrix[1]) == 0:
        _SIM_CACHE[key] = None
        return None

    score = float(np.dot(matrix[0], matrix[1]))
    result = clamp(score) if np.isfinite(score) else None
    _SIM_CACHE[key] = result
    return result


# ============================================================
# Serialización de embeddings (product_images.embedding)
# ============================================================

def pack(vec: Any) -> bytes:
    """Serializa un vector a BLOB float32 (little-endian del host)."""
    array = np.asarray(vec, dtype=np.float32).ravel()
    return array.tobytes()


def unpack(blob: bytes | memoryview | None, dim: int) -> np.ndarray:
    """Deserializa un BLOB float32.

    Devuelve ``(dim,)`` si el blob contiene exactamente un vector, o
    ``(n, dim)`` si contiene varios. Blob vacío/inválido -> array vacío.
    """
    if not blob or not dim or int(dim) <= 0:
        return np.zeros((0,), dtype=np.float32)
    array = np.frombuffer(bytes(blob), dtype=np.float32).copy()
    dim = int(dim)
    if array.size < dim:
        return array
    usable = array.size - (array.size % dim)
    array = array[:usable]
    return array if usable == dim else array.reshape(-1, dim)


# ============================================================
# Similitud de imagen
# ============================================================

def _get_attr(product: dict, name: str) -> Any:
    """Busca un atributo en el dict del producto o en sus ``product_attributes``.

    Acepta el atributo plano (``product['silhouette']``), un dict
    ``product['attributes'] = {'silhouette': 'runner'}`` o la lista de filas
    tal cual sale de la tabla (``[{'attr_name': ..., 'value_text': ...}]``).
    """
    if not isinstance(product, dict):
        return None
    direct = product.get(name)
    if direct not in (None, ""):
        return direct
    for container_key in ("attributes", "attrs", "product_attributes"):
        container = product.get(container_key)
        if isinstance(container, dict):
            value = container.get(name)
            if isinstance(value, dict):
                value = value.get("value_text", value.get("value_num"))
            if value not in (None, ""):
                return value
        elif isinstance(container, (list, tuple)):
            for row in container:
                if not isinstance(row, dict) or row.get("attr_name") != name:
                    continue
                value = row.get("value_text")
                if value in (None, ""):
                    value = row.get("value_num")
                if value not in (None, ""):
                    return value
    return None


def _attr_similarity(a: Any, b: Any) -> float | None:
    """1.0 si coinciden; si no, Jaccard de tokens. ``None`` si falta un lado."""
    ta, tb = _normalize_text(a), _normalize_text(b)
    if not ta or not tb:
        return None
    if ta == tb:
        return 1.0
    return jaccard(set(ta.split()), set(tb.split()))


def _list_similarity(a: Any, b: Any) -> float | None:
    """Similitud de listas separadas por coma/barra (ej. secondary_colors)."""
    def split(value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, (list, tuple, set)):
            parts = [str(v) for v in value]
        else:
            parts = re.split(r"[,/|;]+", str(value))
        return {_normalize_text(p) for p in parts if _normalize_text(p)}

    sa, sb = split(a), split(b)
    if not sa or not sb:
        return None
    return jaccard(sa, sb)


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return float(sum(present) / len(present))


# ── CLIP local (opcional) ───────────────────────────────────

def _image_backend_config() -> str:
    raw = str(section("embeddings", "image", "backend", default="auto") or "auto")
    return raw.strip().lower().replace("-", "_")


def _load_clip(model: str | None = None) -> Any | None:
    """Carga perezosa de CLIP/SigLIP local: ``(model, processor, torch, Image)``.

    Dos redes de contención, como pide el contrato: ``ImportError`` si las
    dependencias opcionales no están instaladas, y ``Exception`` si el modelo no
    está cacheado localmente (``local_files_only=True`` evita cualquier descarga
    silenciosa). En ambos casos se degrada sin romper.
    """
    global _CLIP_RUNTIME, _CLIP_TRIED, _CLIP_MODEL_NAME, _CLIP_STATUS

    model_name = str(model or section("embeddings", "image", "model",
                                      default="openai/clip-vit-base-patch32"))
    if _CLIP_TRIED and model_name == _CLIP_MODEL_NAME:
        return _CLIP_RUNTIME

    _CLIP_TRIED = True
    _CLIP_MODEL_NAME = model_name
    _CLIP_RUNTIME = None

    if _image_backend_config() in {"attribute_fallback", "attributes", "off", "none", "tfidf"}:
        _CLIP_STATUS = "deshabilitado por embeddings.image.backend"
        return None

    try:  # dependencias opcionales y pesadas: NO están en requirements obligatorios
        import torch  # type: ignore
        from PIL import Image  # type: ignore
        from transformers import CLIPModel, CLIPProcessor  # type: ignore
    except ImportError as exc:
        _CLIP_STATUS = f"dependencia opcional ausente ({exc})"
        return None
    except Exception as exc:  # noqa: BLE001 - instalación rota, ABI incompatible, etc.
        _CLIP_STATUS = f"error importando dependencias ({type(exc).__name__})"
        return None

    local_only = bool(section("embeddings", "image", "local_files_only", default=True))
    try:
        clip_model = CLIPModel.from_pretrained(model_name, local_files_only=local_only)
        processor = CLIPProcessor.from_pretrained(model_name, local_files_only=local_only)
        if hasattr(clip_model, "eval"):
            clip_model.eval()
    except Exception as exc:  # noqa: BLE001 - modelo no cacheado => fallback, nunca descarga
        _CLIP_STATUS = f"modelo no disponible localmente ({type(exc).__name__})"
        return None

    _CLIP_RUNTIME = (clip_model, processor, torch, Image)
    _CLIP_STATUS = f"ok ({model_name})"
    return _CLIP_RUNTIME


def clip_available(model: str | None = None) -> bool:
    """¿Hay CLIP local utilizable? (no descarga nada para averiguarlo)."""
    return _load_clip(model) is not None


def clip_status() -> str:
    """Explicación legible de por qué CLIP está o no disponible."""
    _load_clip()
    return _CLIP_STATUS


def image_backend_name() -> str:
    """Backend de imagen realmente activo: ``clip`` | ``attribute_fallback``."""
    return "clip" if clip_available() else "attribute_fallback"


def _to_numpy(value: Any) -> np.ndarray:
    """Tensor de torch (o cualquier cosa parecida) -> ndarray float32."""
    for attr in ("detach", "cpu", "numpy"):
        if hasattr(value, attr):
            try:
                value = getattr(value, attr)()
            except Exception:  # noqa: BLE001
                break
    return np.asarray(value, dtype=np.float32)


def clip_image_vector(path: Path | str, model: str | None = None) -> np.ndarray | None:
    """Embedding CLIP de un archivo local. Nunca descarga la imagen ni el modelo."""
    runtime = _load_clip(model)
    if runtime is None:
        return None
    clip_model, processor, torch, Image = runtime
    try:
        with Image.open(str(path)) as raw:
            image = raw.convert("RGB")
        inputs = processor(images=image, return_tensors="pt")
        no_grad = getattr(torch, "no_grad", None)
        if callable(no_grad):
            with no_grad():
                features = clip_model.get_image_features(**inputs)
        else:  # runtime simulado en tests
            features = clip_model.get_image_features(**inputs)
        array = _to_numpy(features)
        if array.ndim > 1:
            array = array[0]
        array = array.reshape(-1)
        return array if array.size else None
    except Exception:  # noqa: BLE001 - imagen corrupta, modelo raro, OOM...
        return None


# ── encoder determinístico por píxeles (verificación offline) ──

def _read_ppm(path: Path) -> np.ndarray | None:
    """Lector mínimo de PPM/PGM binario (P5/P6): sin PIL, sin dependencias."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data.startswith((b"P5", b"P6")):
        return None
    magic = data[:2]
    fields: list[int] = []
    idx = 2
    while len(fields) < 3 and idx < len(data):
        while idx < len(data) and data[idx : idx + 1].isspace():
            idx += 1
        if data[idx : idx + 1] == b"#":
            while idx < len(data) and data[idx : idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while idx < len(data) and not data[idx : idx + 1].isspace():
            idx += 1
        try:
            fields.append(int(data[start:idx]))
        except ValueError:
            return None
    if len(fields) < 3:
        return None
    width, height, _maxval = fields
    idx += 1                                   # un único whitespace tras maxval
    channels = 3 if magic == b"P6" else 1
    expected = width * height * channels
    payload = data[idx : idx + expected]
    if len(payload) < expected or expected == 0:
        return None
    array = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, channels)
    if channels == 1:
        array = np.repeat(array, 3, axis=2)
    return array.astype(np.float32)


def _read_image_rgb(path: Path | str) -> np.ndarray | None:
    """Imagen como ndarray ``(h, w, 3)``. Usa PIL si está; si no, PPM."""
    path = Path(path)
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return _read_ppm(path)
    except Exception:  # noqa: BLE001
        return _read_ppm(path)
    try:
        with Image.open(str(path)) as raw:
            return np.asarray(raw.convert("RGB"), dtype=np.float32)
    except Exception:  # noqa: BLE001
        return _read_ppm(path)


def _block_mean(array: np.ndarray, grid: int) -> np.ndarray:
    """Reduce a ``(grid, grid, c)`` promediando bloques (resize sin dependencias)."""
    rows = np.array_split(array, grid, axis=0)
    out = []
    for row in rows:
        if row.size == 0:
            row = np.zeros((1, array.shape[1], array.shape[2]), dtype=np.float32)
        cells = [
            block.mean(axis=(0, 1)) if block.size else np.zeros(array.shape[2], dtype=np.float32)
            for block in np.array_split(row, grid, axis=1)
        ]
        out.append(np.stack(cells))
    return np.stack(out).astype(np.float32)


def synthetic_image_vector(path: Path | str) -> np.ndarray | None:
    """Embedding determinístico calculado sobre los píxeles reales de la imagen.

    No es CLIP: es un descriptor de forma (grilla 8x8) + color (histograma por
    canal) en numpy puro. Sirve para ejercitar **todo** el camino de imagen
    (archivo → vector → BLOB → ``image_similarity``) sin instalar ``torch`` ni
    descargar un modelo. Se persiste con el tag ``pixel-hist-v1``, así nunca se
    confunde con un embedding CLIP real.
    """
    array = _read_image_rgb(path)
    if array is None or array.size == 0:
        return None
    array = array[:, :, :3] if array.ndim == 3 else np.stack([array] * 3, axis=-1)

    grid = _block_mean(array, _PIXEL_GRID).reshape(-1) / 255.0
    hist = []
    for channel in range(3):
        counts, _ = np.histogram(array[:, :, channel], bins=_PIXEL_HIST_BINS, range=(0.0, 255.0))
        total = float(counts.sum()) or 1.0
        hist.append(counts.astype(np.float32) / total)
    vector = np.concatenate([grid.astype(np.float32), np.concatenate(hist)])
    # Centrado: sin esto el fondo blanco de catálogo domina el coseno y todas
    # las fotos "se parecen" 0.99. Centrado, el coseno mide correlación de
    # forma+color, que es lo que queremos verificar.
    vector = vector - float(vector.mean())
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return None
    return (vector / norm).astype(np.float32)


def render_synthetic_image(product: dict, size: int = _SYNTHETIC_IMAGE_SIZE) -> bytes:
    """Imagen PPM determinística derivada de los atributos visuales del producto.

    Mismo color + misma silueta => imágenes parecidas => score visual alto.
    Es la materia prima del modo de verificación (``images.run_synthetic_check``).
    """
    def attr(name: str) -> str:
        return _normalize_text(_get_attr(product, name))

    # Identidad visual: color -> paleta, silueta+altura -> forma.
    color = (attr("dominant_color") or attr("colors") or attr("color")
             or attr("upper_material") or attr("material")
             or _normalize_text(product.get("category")))
    silhouette = " ".join(x for x in (attr("silhouette"), attr("height")) if x) or \
        _normalize_text(product.get("category")) or "generic"
    # Ruido determinístico por producto: dos SKUs distintos nunca son idénticos.
    identity = f"{product.get('id')}-{_normalize_text(product.get('product_name'))}"

    palette = {
        "black": (30, 30, 34), "negro": (30, 30, 34),
        "white": (240, 240, 238), "blanco": (240, 240, 238),
        "grey": (140, 140, 145), "gris": (140, 140, 145),
        "blue": (40, 80, 200), "azul": (40, 80, 200),
        "red": (200, 50, 50), "rojo": (200, 50, 50),
        "green": (50, 160, 90), "verde": (50, 160, 90),
    }
    base = next((rgb for key, rgb in palette.items() if key and key in color), None)
    if base is None:                       # color desconocido: derivado estable del texto
        digest = hashlib.sha1((color or "sin-color").encode("utf-8")).digest()
        base = (digest[0], digest[1], digest[2])

    seed = int(hashlib.sha1(silhouette.encode("utf-8")).hexdigest()[:8], 16)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    cx, cy = size / 2.0, size * (0.45 + 0.1 * ((seed % 7) / 7.0))
    rx, ry = size * (0.20 + 0.22 * ((seed % 5) / 5.0)), size * (0.14 + 0.16 * ((seed % 3) / 3.0))
    mask = (((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2) <= 1.0

    canvas = np.full((size, size, 3), 235.0, dtype=np.float32)   # fondo de catálogo
    canvas[mask] = np.asarray(base, dtype=np.float32)
    stripe = ((ys.astype(int) // max(2, size // 12)) % 2 == 0) & mask
    canvas[stripe] = np.clip(np.asarray(base, dtype=np.float32) * 1.25, 0, 255)

    # Textura propia del SKU (amplitud baja: no borra la identidad de familia).
    rng = np.random.default_rng(int(hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8], 16))
    noise = rng.normal(0.0, 18.0, size=(size, size, 1)).astype(np.float32)
    canvas = canvas + noise * mask[:, :, None]

    payload = np.clip(canvas, 0, 255).astype(np.uint8)
    header = f"P6\n{size} {size}\n255\n".encode("ascii")
    return header + payload.tobytes()


# ── índice de embeddings persistidos (product_images) ───────

def _empty_index() -> dict[str, Any]:
    return {"by_product": {}, "by_url": {}, "size": 0, "source": None}


def _norm_url(value: Any) -> str:
    return str(value or "").strip()


def load_image_index(db_path: Path | str = DB_PATH) -> int:
    """Carga en memoria los embeddings de ``product_images``. Devuelve cuántos.

    Lectura **read-only** (nunca crea la base) y a prueba de todo: si la base no
    existe, la tabla no está o los BLOBs son inválidos, el índice queda vacío.
    """
    global _IMAGE_INDEX, _IMAGE_INDEX_SOURCE
    index = _empty_index()
    index["source"] = str(db_path)
    _IMAGE_INDEX_SOURCE = str(db_path)

    path = Path(db_path)
    rows: list[Any] = []
    if path.exists():
        conn = None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT product_id, url, is_primary, embedding, embedding_model, embedding_dim "
                "FROM product_images WHERE embedding IS NOT NULL "
                "ORDER BY COALESCE(is_primary, 0) DESC, id"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            if conn is not None:
                conn.close()

    for row in rows:
        model_tag = str(row["embedding_model"] or "")
        if model_tag.strip().lower() in _PLACEHOLDER_MODELS:
            continue
        dim = int(row["embedding_dim"] or 0)
        vector = unpack(row["embedding"], dim)
        if vector.size == 0:
            continue
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)[:dim] if dim else vector.reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            continue
        entry = {
            "product_id": int(row["product_id"]),
            "url": _norm_url(row["url"]),
            "model": model_tag,
            "vector": (vector / norm).astype(np.float32),
            "is_primary": int(row["is_primary"] or 0),
        }
        index["by_product"].setdefault(entry["product_id"], []).append(entry)
        if entry["url"]:
            index["by_url"].setdefault(entry["url"], entry)
        index["size"] += 1

    _IMAGE_INDEX = index
    return index["size"]


def reset_image_index() -> None:
    """Olvida el índice en memoria (tras un ingest, o en tests)."""
    global _IMAGE_INDEX, _IMAGE_INDEX_SOURCE
    _IMAGE_INDEX = None
    _IMAGE_INDEX_SOURCE = None


def image_index() -> dict[str, Any]:
    """Índice de embeddings persistidos (lo carga la primera vez que se usa)."""
    global _IMAGE_INDEX
    if _IMAGE_INDEX is not None:
        return _IMAGE_INDEX
    if not bool(section("embeddings", "image", "use_persisted", default=True)):
        _IMAGE_INDEX = _empty_index()
        return _IMAGE_INDEX
    try:
        load_image_index(DB_PATH)
    except Exception:  # noqa: BLE001 - el índice es una mejora, nunca un requisito
        _IMAGE_INDEX = _empty_index()
    return _IMAGE_INDEX or _empty_index()


# ── resolución del vector de un producto ────────────────────

def _dict_vector(product: dict) -> tuple[np.ndarray, str] | None:
    """Vector que ya viene dentro del dict recibido (``image_embedding``, ...)."""
    model_tag = str(product.get("embedding_model") or "")
    if model_tag.strip().lower() in _PLACEHOLDER_MODELS or _METHOD_ATTRS in model_tag.lower():
        return None                      # es un placeholder, no un embedding real

    raw = None
    for key in ("image_embedding", "embedding", "clip_embedding"):
        if product.get(key) is not None:
            raw = product[key]
            break
    if raw is None:
        return None

    vector: np.ndarray | None = None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        dim = product.get("embedding_dim") or 0
        unpacked = unpack(raw, int(dim) if dim else 0)
        if unpacked.size:
            vector = unpacked.reshape(-1)[: int(dim)] if dim else unpacked.reshape(-1)
    elif isinstance(raw, np.ndarray):
        vector = raw.astype(np.float32).reshape(-1)
    elif isinstance(raw, (list, tuple)) and raw:
        vector = np.asarray(raw, dtype=np.float32).reshape(-1)

    if vector is None or vector.size == 0:
        return None
    return vector, model_tag


def _persisted_vector(product: dict) -> tuple[np.ndarray, str] | None:
    """Embedding guardado en ``product_images`` para este producto.

    Se exige que la ``image_url`` del producto coincida con la URL registrada:
    así el índice nunca "adivina" por id y un dict sin imagen (el caso de los
    tests y del catálogo actual) sigue cayendo al fallback por atributos.
    """
    url = _norm_url(product.get("image_url"))
    if not url:
        return None
    index = image_index()
    if not index["size"]:
        return None

    entry = index["by_url"].get(url)
    if entry is None:
        return None
    pid = product.get("id", product.get("product_id"))
    if pid is not None:
        try:
            if int(pid) != entry["product_id"]:
                return None
        except (TypeError, ValueError):
            return None
    return entry["vector"], entry["model"]


def _live_vector(product: dict) -> tuple[np.ndarray, str] | None:
    """CLIP al vuelo sobre una imagen **ya cacheada** en disco (nunca descarga)."""
    path_value = product.get("image_path") or product.get("local_image_path")
    path = Path(str(path_value)) if path_value else None
    if path is not None and not path.exists():
        path = None
    if path is None:
        url = _norm_url(product.get("image_url"))
        if not url:
            return None
        try:  # import perezoso: images importa embeddings, no al revés
            from app.services import images as _images
        except ImportError:
            return None
        path = _images.cached_image_path(url)
    if path is None:
        return None

    key = str(path)
    if key in _IMAGE_VEC_CACHE:
        vector = _IMAGE_VEC_CACHE[key]
    else:
        vector = clip_image_vector(path)
        _IMAGE_VEC_CACHE[key] = vector
    if vector is None or vector.size == 0:
        return None
    return vector, str(_CLIP_MODEL_NAME or "clip")


def _resolve_image_vector(product: dict) -> tuple[np.ndarray, str, str] | None:
    """Baja por la cascada y devuelve ``(vector_normalizado, fuente, modelo)``."""
    for source, resolver in (("dict", _dict_vector),
                             ("persisted", _persisted_vector),
                             ("live", _live_vector)):
        try:
            found = resolver(product)
        except Exception:  # noqa: BLE001 - ninguna fuente puede tumbar el scoring
            found = None
        if found is None:
            continue
        vector, model_tag = found
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            continue
        return (vector / norm).astype(np.float32), source, model_tag
    return None


def _model_key(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(tag or "").lower())


def _method_label(source_a: str, source_b: str, model_a: str, model_b: str) -> str:
    """Etiqueta del método: la fuente más baja de la cascada es la que manda."""
    source = source_a if _SOURCE_RANK[source_a] >= _SOURCE_RANK[source_b] else source_b
    if source == "dict":
        return _METHOD_CLIP
    if source == "live":
        return _METHOD_CLIP_LIVE
    model = model_a if _SOURCE_RANK[source_a] >= _SOURCE_RANK[source_b] else model_b
    key = _model_key(model)
    return _METHOD_CLIP_PERSISTED if ("clip" in key or "siglip" in key) else _METHOD_EMB_PERSISTED


def _attribute_image_similarity(p_a: dict, p_b: dict) -> tuple[float | None, str]:
    """Fallback determinístico por atributos visuales.

    Los pesos salen de ``competitive_match.visual.sub_weights`` (weights.yaml);
    ``embedding`` se excluye porque acá justamente no hay embedding.
    """
    sub = weights("competitive_match", "visual", "sub_weights")

    shape = _mean([
        _attr_similarity(_get_attr(p_a, "silhouette"), _get_attr(p_b, "silhouette")),
        _attr_similarity(_get_attr(p_a, "height"), _get_attr(p_b, "height")),
        _attr_similarity(_get_attr(p_a, "design_style"), _get_attr(p_b, "design_style")),
    ])
    colors = _mean([
        _attr_similarity(_get_attr(p_a, "dominant_color"), _get_attr(p_b, "dominant_color")),
        _list_similarity(_get_attr(p_a, "secondary_colors"), _get_attr(p_b, "secondary_colors")),
    ])
    materials = _mean([
        _attr_similarity(_get_attr(p_a, "material"), _get_attr(p_b, "material")),
        _attr_similarity(_get_attr(p_a, "upper_material"), _get_attr(p_b, "upper_material")),
        _attr_similarity(_get_attr(p_a, "sole_type"), _get_attr(p_b, "sole_type")),
    ])

    factors = [
        Factor("silhouette", shape, float(sub.get("silhouette", 0.20)),
               {"note": "silhouette + height + design_style"}),
        Factor("colors", colors, float(sub.get("colors", 0.15)),
               {"note": "dominant_color + secondary_colors"}),
        Factor("materials", materials, float(sub.get("materials", 0.15)),
               {"note": "material + upper_material + sole_type"}),
    ]
    if not any(f.available for f in factors):
        return None, _METHOD_NONE

    result = combine(factors, scale=1.0)
    return round(clamp(result.score), 4), _METHOD_ATTRS


def image_similarity(p_a: dict, p_b: dict) -> tuple[float | None, str]:
    """Similitud visual 0..1 y método usado.

    Cascada de decisión (la primera opción que tenga vector **en los dos
    productos** gana; si no, se sigue bajando):

    ==========================  ==========================================
    método devuelto             de dónde salió el vector
    ==========================  ==========================================
    ``clip``                    embedding incluido en los dicts recibidos
    ``clip-persisted``          ``product_images.embedding`` (modelo CLIP)
    ``embedding-persisted``     ídem, con otro encoder local (verificación)
    ``clip-live``               CLIP local sobre una imagen ya cacheada
    ``attribute-fallback``      atributos visuales (silueta/color/material)
    ``unavailable``             sin evidencia visual -> score ``None``
    ==========================  ==========================================

    Los vectores persistidos **no se recalculan**: los produce
    ``app.services.images.compute_image_embeddings``. Dos vectores sólo se
    comparan si tienen la misma dimensión y el mismo modelo (o modelo
    desconocido); si no, se sigue bajando por la cascada.
    """
    if not isinstance(p_a, dict) or not isinstance(p_b, dict):
        return None, _METHOD_NONE

    resolved_a = _resolve_image_vector(p_a)
    resolved_b = _resolve_image_vector(p_b)
    if resolved_a is not None and resolved_b is not None:
        vec_a, src_a, model_a = resolved_a
        vec_b, src_b, model_b = resolved_b
        key_a, key_b = _model_key(model_a), _model_key(model_b)
        compatible = vec_a.shape == vec_b.shape and (not key_a or not key_b or key_a == key_b)
        if compatible:
            score = float(np.dot(vec_a, vec_b))
            if np.isfinite(score):
                return round(clamp(score), 4), _method_label(src_a, src_b, model_a, model_b)

    return _attribute_image_similarity(p_a, p_b)
