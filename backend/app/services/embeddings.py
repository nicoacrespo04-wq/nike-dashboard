"""Vectores y similitudes locales (texto e imagen).

Restricción dura del proyecto: **cero llamadas de red obligatorias y cero APIs
de LLM cloud**. Todo camino por defecto es determinístico y offline.

Texto
    * ``sentence-transformers`` si está instalado y el modelo carga sin red.
    * Fallback (camino por defecto acá): TF-IDF de scikit-learn combinando
      n-gramas de palabra y de carácter, con similitud coseno.

Imagen
    * CLIP local si está instalado *y* hay un embedding precomputado o un
      archivo local (nunca se descarga una imagen).
    * Fallback determinístico comparando los atributos visuales del producto
      (``product_attributes``): silhouette, colores, materiales, suela, estilo.

Degradación elegante: si falta el dato, se devuelve ``None`` en vez de asumir 0.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np

from app.config import BACKEND_DIR, section, weights
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
_METHOD_CLIP = "clip"
_METHOD_ATTRS = "attribute-fallback"
_METHOD_NONE = "unavailable"

# ── caches en memoria ───────────────────────────────────────
_BACKEND: str | None = None
_ST_MODEL: Any = None
_ST_TRIED = False

_BATCH_CACHE: dict[str, np.ndarray] = {}       # matriz por lote (clave = hash del lote)
_TEXT_CACHE: dict[str, np.ndarray] = {}        # vector por texto (sólo backends estables)
_SIM_CACHE: dict[tuple[str, str], float | None] = {}


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
    _BACKEND = None
    _ST_MODEL = None
    _ST_TRIED = False
    _BATCH_CACHE.clear()
    _TEXT_CACHE.clear()
    _SIM_CACHE.clear()


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


def _image_vector(product: dict) -> np.ndarray | None:
    """Embedding de imagen ya disponible (o calculable localmente con CLIP)."""
    if not isinstance(product, dict):
        return None

    model_tag = str(product.get("embedding_model") or "").lower()
    if model_tag and _METHOD_ATTRS in model_tag:
        return None                      # es un placeholder, no un embedding real

    raw = None
    for key in ("image_embedding", "embedding", "clip_embedding"):
        if product.get(key) is not None:
            raw = product[key]
            break

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

    if vector is None:
        vector = _clip_encode_local(product)
    if vector is None or vector.size == 0:
        return None
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return None
    return (vector / norm).astype(np.float32)


def _clip_encode_local(product: dict) -> np.ndarray | None:
    """CLIP local sobre un archivo en disco. Nunca descarga la imagen."""
    path_value = product.get("image_path") or product.get("local_image_path")
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists():
        return None
    try:  # dependencias opcionales
        import torch  # type: ignore
        from PIL import Image  # type: ignore
        from transformers import CLIPModel, CLIPProcessor  # type: ignore
    except ImportError:
        return None
    model_name = str(section("embeddings", "image", "model", default="openai/clip-vit-base-patch32"))
    try:
        model = CLIPModel.from_pretrained(model_name, local_files_only=True)
        processor = CLIPProcessor.from_pretrained(model_name, local_files_only=True)
        with torch.no_grad():
            inputs = processor(images=Image.open(path).convert("RGB"), return_tensors="pt")
            features = model.get_image_features(**inputs)
        return np.asarray(features[0].numpy(), dtype=np.float32)
    except Exception:  # noqa: BLE001 - sin modelo local => fallback por atributos
        return None


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

    Devuelve ``('clip')`` si ambos productos tienen embedding CLIP disponible,
    ``('attribute-fallback')`` si se resolvió por atributos visuales y
    ``(None, 'unavailable')`` si no hay evidencia visual suficiente.
    """
    if not isinstance(p_a, dict) or not isinstance(p_b, dict):
        return None, _METHOD_NONE

    vec_a, vec_b = _image_vector(p_a), _image_vector(p_b)
    if vec_a is not None and vec_b is not None and vec_a.shape == vec_b.shape:
        score = float(np.dot(vec_a, vec_b))
        if np.isfinite(score):
            return round(clamp(score), 4), _METHOD_CLIP

    return _attribute_image_similarity(p_a, p_b)
