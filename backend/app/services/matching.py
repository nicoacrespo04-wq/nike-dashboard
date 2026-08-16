"""Competitive Product Matching Engine.

Módulo central del producto: dado un producto Nike, encuentra y puntúa los
productos competidores equivalentes combinando SIETE factores independientes.

Principios (ver backend/CONTRACTS.md):
  * Cada factor es una función pura ``_score_x(nike, comp, ctx) -> (score|None, detail)``
    donde ``score`` vive en 0..1 y ``None`` significa "sin datos".
  * Un factor sin datos NO penaliza: se excluye y se renormaliza el peso
    (lo hace ``common.combine``).
  * Cero pesos hardcodeados: todo sale de ``config/weights.yaml``.
  * Cero llamadas a LLMs cloud. ``app.services.embeddings`` es OPCIONAL:
    si no está disponible se degrada al fallback determinístico.
  * ``build_context`` precarga TODO de la DB una sola vez: el scoring de un par
    no toca la base.
"""

from __future__ import annotations

import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import DB_PATH, section, weights
from app.db import get_conn
from app.services.common import (
    CompositeScore,
    Factor,
    clamp,
    combine,
    gap_similarity,
    jaccard,
    recency_weight,
    saturate,
    to_json,
)

# Escala del rating en el esquema (reviews.rating -> 0..5). No es un peso:
# es la unidad del dato, por eso vive acá y no en weights.yaml.
_RATING_SCALE = 5.0

# ── dependencia opcional: embeddings locales ────────────────
# La escribe otro módulo; si no existe, los factores que la usan degradan
# elegantemente al fallback determinístico por atributos.
_EMBEDDINGS_SENTINEL = object()
_embeddings_module: Any = _EMBEDDINGS_SENTINEL


def _embeddings() -> Any | None:
    """Importa ``app.services.embeddings`` si existe (cacheado). None si no."""
    global _embeddings_module
    if _embeddings_module is _EMBEDDINGS_SENTINEL:
        try:  # pragma: no cover - depende de si el módulo hermano existe
            from app.services import embeddings as _mod
        except ImportError:
            _mod = None
        _embeddings_module = _mod
    return _embeddings_module


def reset_embeddings_cache() -> None:
    """Olvida el resultado del import opcional (útil en tests)."""
    global _embeddings_module, _text_fast_path
    _embeddings_module = _EMBEDDINGS_SENTINEL
    _text_fast_path = None


# ── normalización de texto ──────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=100_000)
def _norm_str(text: str) -> str:
    """Núcleo cacheado de `_norm` (la normalización Unicode es cara y se repite
    sobre los MISMOS valores en cada par: nombres de atributo, taxonomía,
    etiquetas de lifecycle...)."""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(normalized.lower().split())


def _norm(value: Any) -> str:
    """Minúsculas, sin acentos, sin espacios de más."""
    if value is None:
        return ""
    return _norm_str(value if type(value) is str else str(value))


@lru_cache(maxsize=100_000)
def _tokens_str(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text))


def _tokens(value: Any) -> set[str]:
    # El resultado es inmutable y compartido: los llamadores sólo lo leen o lo
    # combinan con `|` (que ya devuelve un objeto nuevo).
    return _tokens_str(_norm(value))  # type: ignore[return-value]


def _field_similarity(a: Any, b: Any) -> float | None:
    """Similitud entre dos valores de taxonomía.

    1.0 si son iguales (normalizados), si no Jaccard de tokens
    ("daily running" vs "trail running" -> 0.33). None si falta alguno.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return None
    if na == nb:
        return 1.0
    return jaccard(_tokens(na), _tokens(nb)) or 0.0


def _pair_key(a: int, b: int) -> tuple[int, int]:
    """Clave de par ordenada y normalizada (simétrica)."""
    return (a, b) if a <= b else (b, a)


# ── similitud textual: el mismo número, sin un TF-IDF por par ──
#
# `embeddings.text_similarity(a, b)` ajusta DOS `TfidfVectorizer` sobre un lote
# de dos documentos y devuelve el coseno de las filas. Como el vocabulario se
# ajusta AL PAR, no hay vector reutilizable entre pares: en una corrida de N
# pares se pagan N ajustes de sklearn (~4 ms cada uno = el 91% del motor).
#
# Pero con n=2 documentos el resultado tiene forma cerrada. Con `smooth_idf`:
#
#     idf(t) = ln((1 + 2) / (1 + df(t))) + 1
#            = 1.0                 si t aparece en los dos documentos
#            = 1 + ln(3/2) = K     si aparece en uno solo
#
# En el coseno sólo sobreviven los términos COMPARTIDOS (los demás multiplican
# por cero del otro lado) y ahí el idf vale exactamente 1. Por lo tanto:
#
#     cos = Σ_{t∈a∩b} tf_a(t)·tf_b(t) / (‖a‖·‖b‖)
#     ‖a‖² = Σ_{t∈a} (tf_a(t)·idf(t))² = K²·S_a − (K²−1)·Q_a
#
# con S_a = Σ_{t∈a} tf_a(t)² y Q_a = Σ_{t∈a∩b} tf_a(t)². Es decir: alcanza con
# los CONTEOS POR DOCUMENTO —que se calculan una sola vez por descripción— y
# una intersección por par. Los dos bloques (word y char_wb) salen ya
# L2-normalizados de sklearn, así que la concatenación normalizada de
# `embeddings.text_vectors` da  score = Σ_bloques cos_b / √(n_a·n_b),  donde
# n_d es la cantidad de bloques en los que el documento d no es el vector nulo.
#
# El camino rápido se AUTOVERIFICA contra `embeddings.text_similarity` la
# primera vez que se usa (ver `_TextFastPath.verify`): si el módulo de
# embeddings cambia de backend, de n-gramas o de parámetros, el atajo se apaga
# solo y el motor vuelve al camino lento —lento pero correcto—.

_IDF_UNIQUE = 1.0 + math.log(1.5)      # idf de un término presente en un solo doc

# Tolerancia de la autoverificación. No es "casi igual": es el ruido de que
# sklearn hace toda la cuenta en float32 (eps ≈ 1.2e-7) y el atajo la hace en
# float64. Medido sobre el catálogo demo la diferencia máxima es ~4e-8, que
# sobre el score final (0..100) son ~2e-7 puntos — tres órdenes de magnitud por
# debajo de la precisión con la que el score se persiste (4 decimales = 1e-4).
# Cualquier diferencia MAYOR ya no es redondeo sino otra fórmula: ahí el atajo
# se apaga solo.
_FAST_TEXT_TOLERANCE = 1e-6
_FAST_TEXT_SAMPLES = 12                # pares de muestra para la autoverificación

_text_fast_path: Any = None


class _TextFastPath:
    """Similitud TF-IDF por par en O(términos) en lugar de un fit de sklearn."""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.reason: str | None = None
        self.verified = False
        self.blocks: list[tuple[Any, int]] = []          # (analyzer, max_features)
        self._docs: dict[str, list[tuple[dict[str, float], float]]] = {}
        self.available = self._prepare()

    # -- preparación ---------------------------------------------------------

    def _prepare(self) -> bool:
        module = self.module
        needed = ("_normalize_text", "_WORD_NGRAMS", "_CHAR_NGRAMS",
                  "_WORD_MAX_FEATURES", "_CHAR_MAX_FEATURES", "text_similarity")
        missing = [name for name in needed if not hasattr(module, name)]
        if missing:
            self.reason = f"el módulo de embeddings no expone {', '.join(missing)}"
            return False
        try:
            if module.backend_name() != "tfidf":
                self.reason = f"backend de texto = {module.backend_name()} (no TF-IDF)"
                return False
        except Exception as exc:  # noqa: BLE001 - dependencia opcional
            self.reason = f"backend_name() falló: {exc.__class__.__name__}"
            return False
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError:
            self.reason = "sklearn no disponible"
            return False

        # Los analizadores salen del PROPIO sklearn con los mismos parámetros que
        # usa `embeddings._tfidf_matrix`: acá no se reimplementa la tokenización.
        for analyzer, ngram_range, max_features in (
            ("word", module._WORD_NGRAMS, module._WORD_MAX_FEATURES),
            ("char_wb", module._CHAR_NGRAMS, module._CHAR_MAX_FEATURES),
        ):
            vectorizer = TfidfVectorizer(
                analyzer=analyzer, ngram_range=ngram_range, max_features=max_features,
                sublinear_tf=True, lowercase=False,
            )
            self.blocks.append((vectorizer.build_analyzer(), int(max_features)))
        return True

    # -- documentos ----------------------------------------------------------

    def _document(self, text: str) -> list[tuple[dict[str, float], float]]:
        """``[(tf por término, Σ tf²)]`` por bloque. Se calcula una vez por texto."""
        cached = self._docs.get(text)
        if cached is not None:
            return cached
        blocks: list[tuple[dict[str, float], float]] = []
        for analyze, _max_features in self.blocks:
            counts: dict[str, float] = {}
            for term in analyze(text):
                counts[term] = counts.get(term, 0.0) + 1.0
            tf = {term: 1.0 + math.log(count) for term, count in counts.items()}
            blocks.append((tf, sum(v * v for v in tf.values())))
        self._docs[text] = blocks
        return blocks

    # -- similitud -----------------------------------------------------------

    def similarity(self, norm_a: str, norm_b: str) -> float | None:
        """Coseno equivalente al de ``embeddings.text_similarity`` (textos ya
        normalizados y distintos). ``None`` = sin vocabulario; ``NotImplemented``
        = este par no se puede resolver por el atajo (usar el camino lento)."""
        doc_a, doc_b = self._document(norm_a), self._document(norm_b)
        total = 0.0
        n_a = n_b = 0
        for (tf_a, s_a), (tf_b, s_b), (_analyze, max_features) in zip(doc_a, doc_b, self.blocks):
            if not tf_a and not tf_b:
                continue                       # bloque ausente: vocabulario vacío
            # `max_features` recortaría el vocabulario y cambiaría las normas:
            # ese par se resuelve por el camino lento.
            if len(tf_a) + len(tf_b) > max_features and len(set(tf_a) | set(tf_b)) > max_features:
                return NotImplemented
            n_a += 1 if tf_a else 0
            n_b += 1 if tf_b else 0
            if not tf_a or not tf_b:
                continue                       # una fila nula: coseno 0 en el bloque
            # La intersección de claves la hace C; el bucle en Python recorre
            # sólo los términos compartidos, que son los únicos que aportan.
            dot = q_a = q_b = 0.0
            for term in tf_a.keys() & tf_b.keys():
                value_a, value_b = tf_a[term], tf_b[term]
                dot += value_a * value_b
                q_a += value_a * value_a
                q_b += value_b * value_b
            if dot <= 0.0:
                continue
            k2 = _IDF_UNIQUE * _IDF_UNIQUE
            norm_sq_a = k2 * s_a - (k2 - 1.0) * q_a
            norm_sq_b = k2 * s_b - (k2 - 1.0) * q_b
            if norm_sq_a <= 0.0 or norm_sq_b <= 0.0:
                continue
            total += dot / math.sqrt(norm_sq_a * norm_sq_b)

        if not n_a or not n_b:
            return None                        # vector nulo: `text_similarity` da None
        score = total / math.sqrt(n_a * n_b)
        return clamp(score) if math.isfinite(score) else None

    # -- autoverificación ----------------------------------------------------

    def verify(self, samples: list[tuple[str, str]]) -> bool:
        """Compara el atajo contra `embeddings.text_similarity` en pares reales.

        Si algo no coincide (otro backend, otros n-gramas, otra normalización)
        el atajo se apaga para toda la corrida.
        """
        self.verified = True
        for raw_a, raw_b in samples[:_FAST_TEXT_SAMPLES]:
            slow = self.module.text_similarity(raw_a, raw_b)
            fast = self.fast(raw_a, raw_b)
            if fast is NotImplemented:
                continue
            if (slow is None) != (fast is None):
                self.available = False
                self.reason = "autoverificación: disponibilidad distinta al camino lento"
                return False
            if slow is not None and abs(float(slow) - float(fast)) > _FAST_TEXT_TOLERANCE:
                self.available = False
                self.reason = (f"autoverificación: Δ={abs(float(slow) - float(fast)):.2e} "
                               f"> {_FAST_TEXT_TOLERANCE:.0e}")
                return False
        return True

    def fast(self, a: Any, b: Any) -> Any:
        """Igual que ``embeddings.text_similarity`` pero por el atajo analítico."""
        if a is None or b is None:
            return None
        norm_a = self.module._normalize_text(a)
        norm_b = self.module._normalize_text(b)
        if not norm_a or not norm_b:
            return None
        if norm_a == norm_b:
            return 1.0
        return self.similarity(norm_a, norm_b)


def verify_text_fast_path(products: dict[int, dict]) -> dict[str, Any]:
    """Autoverifica el atajo textual con descripciones REALES del catálogo.

    Se corre una vez por `build_context`: si el atajo no reproduce exactamente
    lo que devuelve `embeddings.text_similarity`, queda desactivado y la corrida
    entera usa sklearn.
    """
    module = _embeddings()
    if module is None or not hasattr(module, "text_similarity"):
        return {"enabled": False, "reason": "sin módulo de embeddings"}
    engine = _text_similarity_engine(module)
    if engine is None:
        return {"enabled": False, "reason": (_text_fast_path.reason if _text_fast_path else None)}

    descriptions = [p.get("description") for p in products.values() if p.get("description")]
    samples = [(descriptions[i], descriptions[i + 1])
               for i in range(0, min(len(descriptions) - 1, 2 * _FAST_TEXT_SAMPLES), 2)]
    ok = engine.verify(samples) if samples else True
    return {"enabled": bool(ok and engine.available), "samples": len(samples),
            "reason": engine.reason}


def _text_similarity_engine(module: Any) -> _TextFastPath | None:
    """Camino rápido de similitud textual para el módulo de embeddings activo."""
    global _text_fast_path
    if _text_fast_path is None or _text_fast_path.module is not module:
        _text_fast_path = _TextFastPath(module)
    return _text_fast_path if _text_fast_path.available else None


def text_similarity(a: Any, b: Any, module: Any) -> float | None:
    """Similitud textual del par: atajo analítico si aplica, sklearn si no."""
    engine = _text_similarity_engine(module)
    if engine is not None:
        if not engine.verified:
            engine.verify([(a, b)])
            engine = _text_similarity_engine(module)
        if engine is not None:
            value = engine.fast(a, b)
            if value is not NotImplemented:
                return value
    return module.text_similarity(a, b)


# ── vigencia de generación (franquicia · modelo · versión) ──
#
# Una franquicia ("Pegasus", "Metcon") vive en el catálogo como una SUCESIÓN de
# generaciones: Metcon 9 -> Metcon 10, Pegasus 41 -> Pegasus 42. Comparar un
# competidor contra la generación que ya salió de la góndola produce
# conclusiones sobre un producto que el negocio no está empujando.
#
# La vigencia se DERIVA del catálogo, no se declara: dentro de un mismo linaje
# (marca + país + línea de modelo) gana la versión más alta y, a igualdad, la
# fecha de lanzamiento más reciente. Nada de listas de "modelos vigentes" en
# config, que envejecen con cada lanzamiento.
#
# Ojo con la granularidad: el linaje NO es la franquicia sino la LÍNEA DE
# MODELO. "Pegasus" y "Pegasus Trail" comparten franquicia y son siluetas
# distintas que conviven; tratarlas como generaciones de lo mismo declararía
# discontinuada a una de las dos.

_VERSION_NUM_RE = re.compile(r"\d+")
_TRAILING_VERSION_RE = re.compile(r"[\s\-_.]*(?:v)?\d+[a-z]*$")


def _version_number(product: dict) -> float | None:
    """Número de generación del producto, o None si no se puede leer.

    Busca en ``version`` y, si no hay, en ``model`` / ``product_name``. Toma el
    ÚLTIMO número del texto: "v14" -> 14, "Rise 2" -> 2, "'07" -> 7,
    "Adizero Adios Pro 3" -> 3. Un versionado no numérico ("Classic XXI", "OG")
    devuelve None y el linaje se ordena por fecha.
    """
    for value in (product.get("version"), product.get("model"), product.get("product_name")):
        if value in (None, ""):
            continue
        nums = _VERSION_NUM_RE.findall(str(value))
        if nums:
            return float(nums[-1])
    return None


def lineage_key(product: dict) -> tuple:
    """Identidad de la LÍNEA de producto a la que pertenece una generación.

    marca + país + línea de modelo (``model``, si no ``franchise``, si no el
    nombre sin su número de versión). Marca y país entran porque una sucesión
    de generaciones sólo tiene sentido dentro del mismo catálogo comercial.
    """
    base = _norm(product.get("model")) or _norm(product.get("franchise"))
    if not base:
        base = _TRAILING_VERSION_RE.sub("", _norm(product.get("product_name"))).strip()
    return (product.get("brand_id"), product.get("country_code"), base)


@dataclass(frozen=True)
class Generation:
    """Dónde está un producto dentro de la sucesión de su línea."""

    lineage: tuple
    version: float | None
    is_current: bool                 # es la última generación del linaje
    generations_behind: int          # 0 = vigente, 1 = la anterior, ...
    successor_id: int | None
    successor_name: str | None
    lifecycle_stage: str | None
    stale: bool                      # generación saliente: no comparar contra ella
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lineage": self.lineage[-1] if self.lineage else None,
            "version": self.version,
            "is_current": self.is_current,
            "generations_behind": self.generations_behind,
            "successor": self.successor_name,
            "lifecycle_stage": self.lifecycle_stage,
            "stale": self.stale,
            "reason": self.reason,
        }


def stale_lifecycle_stages() -> set[str]:
    """Etapas de ``lifecycle_stage`` que marcan una generación saliente.

    Se apoya en el `lifecycle_stage` que ya calcula `enrichment` — acá no se
    vuelve a derivar nada de `launch_date`. Por defecto sólo ``clearance``:
    ``decline`` es la segunda mitad normal de la vida de un producto vigente y
    castigar ahí apagaría medio catálogo.
    """
    raw = section("competitive_match", "generation", "stale_lifecycle_stages",
                  default=["clearance"]) or []
    return {_norm(s) for s in raw if s}


def generation_map(products: dict[int, dict]) -> dict[int, Generation]:
    """Ubica cada producto en su linaje y marca las generaciones salientes.

    Función pura sobre el catálogo en memoria: la usan `matching` (para
    penalizar pares contra generaciones discontinuadas) y `opportunities`
    (para elegir el producto Nike de referencia de una franquicia).
    """
    stale_stages = stale_lifecycle_stages()

    lineages: dict[tuple, list[int]] = defaultdict(list)
    for pid, product in products.items():
        lineages[lineage_key(product)].append(pid)

    out: dict[int, Generation] = {}
    for key, pids in lineages.items():
        # Orden de la sucesión: versión numérica y, a igualdad (o sin versión),
        # fecha de lanzamiento. El último elemento es la generación vigente.
        ordered = sorted(
            pids,
            key=lambda pid: (
                _version_number(products[pid]) is not None,
                _version_number(products[pid]) or 0.0,
                str(products[pid].get("launch_date") or ""),
                pid,
            ),
        )
        latest = ordered[-1]
        for position, pid in enumerate(ordered):
            behind = len(ordered) - 1 - position
            stage = products[pid].get("lifecycle_stage")
            superseded = behind > 0
            by_stage = _norm(stage) in stale_stages
            reason = None
            if superseded:
                reason = (f"generación superada por {products[latest].get('product_name')} "
                          f"({behind} generación/es atrás)")
            elif by_stage:
                reason = f"lifecycle_stage={stage}"
            out[pid] = Generation(
                lineage=key,
                version=_version_number(products[pid]),
                is_current=not superseded,
                generations_behind=behind,
                successor_id=None if not superseded else latest,
                successor_name=None if not superseded else products[latest].get("product_name"),
                lifecycle_stage=stage,
                stale=superseded or by_stage,
                reason=reason,
            )
    return out


def current_generation_ids(products: dict[int, dict], *,
                           brand_is_focus: bool | None = None) -> set[int]:
    """Ids de los productos que son la generación vigente de su linaje."""
    gens = generation_map(products)
    return {
        pid for pid, gen in gens.items()
        if gen.is_current
        and (brand_is_focus is None
             or bool(products[pid].get("brand_is_focus")) is brand_is_focus)
    }


def _blend(parts: dict[str, float | None], sub_weights: dict[str, float]) -> float | None:
    """Combina sub-señales renormalizando sobre las disponibles.

    Devuelve None si ninguna sub-señal tiene datos.
    """
    total = 0.0
    acc = 0.0
    for name, value in parts.items():
        if value is None:
            continue
        w = float(sub_weights.get(name, 0.0))
        if w <= 0:
            continue
        total += w
        acc += w * clamp(float(value))
    if total <= 0:
        return None
    return clamp(acc / total)


# ── contexto precargado ─────────────────────────────────────


@dataclass(frozen=True)
class MatchParams:
    """Fotografía de ``weights.yaml`` para UNA corrida.

    Los pesos siguen viniendo enteros de la config (cero hardcodeo): lo único
    que cambia es CUÁNDO se leen. Antes cada factor resolvía sus claves por par
    —``weights()`` reconstruye un dict en cada llamada—, así que un catálogo de
    1.000 productos releía la misma configuración doscientas mil veces. Ahora se
    resuelve una vez por `build_context`, que es exactamente el alcance de una
    corrida: `run_matching` la reconstruye siempre, y los barridos de
    `calibration` también (cambian la config y vuelven a construir contexto).
    """

    factor_weights: dict[str, float]
    confidence_thresholds: dict[str, float] | None
    visual_sub_weights: dict[str, float]
    visual_total_weight: float
    visual_min_evidence: float
    semantic_field_weights: dict[str, float]
    semantic_penalty: float
    generation_enabled: bool
    generation_stale_penalty: float
    price_sub_weights: dict[str, float]
    price_tolerance: float
    price_band_order: dict[Any, list[str]]
    editorial_type_scores: dict[str, float]
    editorial_same_list_score: float
    editorial_k: float
    editorial_half_life: float
    social_k: float
    social_half_life: float
    social_min_comentions: float
    reviews_min: float
    reviews_rating_weight: float
    candidate_filter: dict[str, Any]

    @classmethod
    def load(cls, countries: set[Any] | None = None) -> "MatchParams":
        visual = weights("competitive_match", "visual", "sub_weights")
        generation = section("competitive_match", "generation", default={}) or {}
        editorial = weights("competitive_match", "editorial", "mention_type_scores")
        bands: dict[Any, list[str]] = {}
        for country in (countries or set()) | {None}:
            raw = section("enrichment", "price_bands", country, default=None) or {}
            bands[country] = [_norm(k) for k in raw]
        return cls(
            factor_weights=weights("competitive_match", "weights"),
            confidence_thresholds=section("competitive_match", "confidence_thresholds"),
            visual_sub_weights=visual,
            visual_total_weight=sum(visual.values()) or 1.0,
            visual_min_evidence=float(section("competitive_match", "visual",
                                              "min_evidence_weight", default=0.0)),
            semantic_field_weights=weights("competitive_match", "semantic", "field_weights"),
            semantic_penalty=float(section("competitive_match", "semantic",
                                           "hard_mismatch_penalty", default=1.0)),
            generation_enabled=bool(generation.get("enabled", True)),
            generation_stale_penalty=float(generation.get("stale_penalty", 0.85)),
            price_sub_weights=weights("competitive_match", "price", "sub_weights"),
            price_tolerance=float(section("competitive_match", "price",
                                          "gap_tolerance_pct", default=0.0)),
            price_band_order=bands,
            editorial_type_scores=editorial,
            editorial_same_list_score=float(editorial.get("same_list", 0.0)),
            editorial_k=float(section("competitive_match", "editorial",
                                      "saturation_k", default=1.0)),
            editorial_half_life=float(section("competitive_match", "editorial",
                                              "recency_half_life_days", default=0.0)),
            social_k=float(section("competitive_match", "social", "saturation_k", default=1.0)),
            social_half_life=float(section("competitive_match", "social",
                                           "recency_half_life_days", default=0.0)),
            social_min_comentions=float(section("competitive_match", "social",
                                                "min_comentions", default=0)),
            reviews_min=float(section("competitive_match", "reviews",
                                      "min_reviews_for_signal", default=0)),
            reviews_rating_weight=float(section("competitive_match", "reviews",
                                                "rating_weight", default=0.0)),
            candidate_filter=candidate_filter_config(),
        )


@dataclass
class MatchContext:
    """Todo lo que el scoring necesita, precargado en memoria.

    Se construye UNA vez por corrida: ``compute_match`` nunca toca la DB.
    """

    products: dict[int, dict]                       # product_id -> fila (+ brand_name, brand_is_focus)
    attributes: dict[int, dict[str, Any]]           # product_id -> {attr_name: valor}
    retailers_by_product: dict[int, set[int]]       # product_id -> {retailer_id}
    latest_price: dict[tuple[int, int], dict]       # (product_id, retailer_id) -> última observación
    latest_stock: dict[tuple[int, int], dict]       # (product_id, retailer_id) -> última observación
    reviews: dict[int, list[dict]]                  # product_id -> reviews
    editorial: dict[tuple[int, int], list[dict]]    # par ordenado normalizado -> menciones
    editorial_lists: dict[str, set[int]]            # list_key -> product_ids
    social: dict[tuple[int, int], list[dict]]       # par ordenado normalizado -> agregados sociales
    # Auxiliar: fechas de publicación por lista editorial (para ponderar recencia).
    editorial_list_dates: dict[str, list[str | None]] = field(default_factory=dict)
    # Vigencia de generación por producto (ver `generation_map`).
    generations: dict[int, Generation] = field(default_factory=dict)
    # Listas editoriales POR PRODUCTO (en el orden en que aparecen en
    # `editorial_lists`, para que los puntos se sumen en el mismo orden):
    # intersectar por par cuesta O(listas del producto) en vez de O(listas del
    # corpus).
    lists_by_product: dict[int, tuple[str, ...]] = field(default_factory=dict)
    list_sets: dict[int, frozenset[str]] = field(default_factory=dict)
    # Recencia media ya ponderada de cada lista editorial (constante en la corrida).
    list_recency: dict[str, float] = field(default_factory=dict)
    # Fotografía de la config (ver `MatchParams`).
    params: MatchParams | None = None
    # Memos internos por PRODUCTO (no por par): el scoring de un par no los recalcula.
    _avg_price_cache: dict[int, float | None] = field(default_factory=dict, repr=False)
    _attr_token_cache: dict[tuple, frozenset[str]] = field(default_factory=dict, repr=False)
    _review_cache: dict[int, tuple[float, frozenset[str], float | None]] = field(
        default_factory=dict, repr=False)

    # -- accesos convenientes ------------------------------------------------

    @property
    def p(self) -> MatchParams:
        """Parámetros de la corrida (se resuelven al vuelo si el contexto se
        construyó a mano, p. ej. en un test)."""
        if self.params is None:
            self.params = MatchParams.load({p.get("country_code") for p in self.products.values()})
        return self.params

    def generation(self, product_id: int) -> Generation | None:
        return self.generations.get(product_id)

    def review_signals(self, product_id: int) -> tuple[float, frozenset[str], float | None]:
        """``(volumen, atributos valorados, rating promedio)`` del producto.

        Depende de UN producto, no del par: se calcula una vez y se reusa en
        todos los pares que lo involucran (antes se re-extraía el léxico de
        todas sus reviews en cada par).
        """
        cached = self._review_cache.get(product_id)
        if cached is None:
            rows = self.reviews.get(product_id, [])
            cached = (_review_volume(rows), frozenset(_review_attributes(rows)), _avg_rating(rows))
            self._review_cache[product_id] = cached
        return cached

    def attrs(self, product_id: int) -> dict[str, Any]:
        return self.attributes.get(product_id, {})

    def attr(self, product_id: int, *names: str) -> Any:
        """Primer atributo no vacío entre varios nombres candidatos."""
        bag = self.attributes.get(product_id, {})
        for name in names:
            value = bag.get(name)
            if value not in (None, ""):
                return value
        return None

    def attr_tokens(self, product_id: int, names: tuple[str, ...],
                    contains: tuple[str, ...] = ()) -> frozenset[str]:
        """Tokens de todos los atributos que matcheen por nombre exacto o por substring.

        Memoizado por (producto, grupo de atributos): el resultado no depende
        del par y los grupos son tres constantes del módulo.
        """
        cache_key = (product_id, names, contains)
        cached = self._attr_token_cache.get(cache_key)
        if cached is not None:
            return cached
        out: set[str] = set()
        for key, value in self.attributes.get(product_id, {}).items():
            nkey = _norm(key)
            if nkey in names or any(part in nkey for part in contains):
                out |= _tokens(value)
        frozen = frozenset(out)
        self._attr_token_cache[cache_key] = frozen
        return frozen

    def avg_current_price(self, product_id: int) -> float | None:
        """Promedio del último precio observado en cada retailer.

        Se precalcula para TODOS los productos en `build_context` (una pasada
        sobre las observaciones). Antes cada producto barría el índice completo
        de precios la primera vez que aparecía en un par: con 27.000
        observaciones y 600 competidores eso son 16 millones de comparaciones
        que no dependen del par.
        """
        return self._avg_price_cache.get(product_id)


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict]:
    return [dict(r) for r in conn.execute(sql).fetchall()]


def build_context(db_path: Path | str = DB_PATH) -> MatchContext:
    """Precarga catálogo + observaciones. Una sola pasada por tabla."""
    with get_conn(db_path) as conn:
        products = {
            r["id"]: r
            for r in _rows(
                conn,
                "SELECT p.*, b.name AS brand_name, b.is_focus AS brand_is_focus "
                "FROM products p JOIN brands b ON b.id = p.brand_id",
            )
        }

        attributes: dict[int, dict[str, Any]] = defaultdict(dict)
        for r in _rows(conn, "SELECT product_id, attr_name, value_text, value_num FROM product_attributes"):
            value = r["value_text"] if r["value_text"] not in (None, "") else r["value_num"]
            attributes[r["product_id"]][r["attr_name"]] = value

        retailers_by_product: dict[int, set[int]] = defaultdict(set)
        latest_price: dict[tuple[int, int], dict] = {}
        for r in _rows(
            conn,
            "SELECT * FROM price_observations ORDER BY COALESCE(observed_at, ''), id",
        ):
            retailers_by_product[r["product_id"]].add(r["retailer_id"])
            latest_price[(r["product_id"], r["retailer_id"])] = r  # el último gana

        latest_stock: dict[tuple[int, int], dict] = {}
        for r in _rows(
            conn,
            "SELECT * FROM stock_observations ORDER BY COALESCE(observed_at, ''), id",
        ):
            retailers_by_product[r["product_id"]].add(r["retailer_id"])
            latest_stock[(r["product_id"], r["retailer_id"])] = r

        reviews: dict[int, list[dict]] = defaultdict(list)
        for r in _rows(conn, "SELECT * FROM reviews"):
            reviews[r["product_id"]].append(r)

        editorial: dict[tuple[int, int], list[dict]] = defaultdict(list)
        editorial_lists: dict[str, set[int]] = defaultdict(set)
        editorial_list_dates: dict[str, list[str | None]] = defaultdict(list)
        for r in _rows(conn, "SELECT * FROM editorial_mentions"):
            a, b = r["product_a_id"], r["product_b_id"]
            if a is not None and b is not None and a != b:
                editorial[_pair_key(a, b)].append(r)
            key = r["list_key"]
            if key:
                for pid in (a, b):
                    if pid is not None:
                        editorial_lists[key].add(pid)
                editorial_list_dates[key].append(r["published_at"])

        social: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for r in _rows(conn, "SELECT * FROM social_mention_aggregates"):
            a, b = r["product_id"], r["co_product_id"]
            if a is not None and b is not None and a != b:
                social[_pair_key(a, b)].append(r)

    # El atajo de similitud textual se valida contra el camino lento con
    # descripciones de ESTE catálogo antes de usarlo (ver `verify_text_fast_path`).
    verify_text_fast_path(products)

    params = MatchParams.load({p.get("country_code") for p in products.values()})

    # -- índices derivados: todo lo que depende de UN producto, no del par ----
    # Promedio de precio actual. Se recorre `latest_price` UNA vez y en su mismo
    # orden de inserción, así el promedio se suma en el mismo orden que antes y
    # da el mismo float bit a bit.
    prices_by_product: dict[int, list[float]] = defaultdict(list)
    for (pid, _rid), obs in latest_price.items():
        if obs.get("current_price") is not None:
            prices_by_product[pid].append(float(obs["current_price"]))
    avg_price = {pid: (sum(values) / len(values) if values else None)
                 for pid, values in prices_by_product.items()}
    for pid in products:
        avg_price.setdefault(pid, None)

    # Listas editoriales por producto + recencia media de cada lista.
    lists_by_product: dict[int, list[str]] = defaultdict(list)
    for list_key, pids in editorial_lists.items():
        for pid in pids:
            lists_by_product[pid].append(list_key)
    list_recency = {
        list_key: (sum(recency_weight(d, params.editorial_half_life)
                       for d in (editorial_list_dates.get(list_key) or [None]))
                   / len(editorial_list_dates.get(list_key) or [None]))
        for list_key in editorial_lists
    }

    return MatchContext(
        generations=generation_map(products),
        products=products,
        attributes=dict(attributes),
        retailers_by_product=dict(retailers_by_product),
        latest_price=latest_price,
        latest_stock=latest_stock,
        reviews=dict(reviews),
        editorial=dict(editorial),
        editorial_lists=dict(editorial_lists),
        social=dict(social),
        editorial_list_dates=dict(editorial_list_dates),
        lists_by_product={pid: tuple(keys) for pid, keys in lists_by_product.items()},
        list_sets={pid: frozenset(keys) for pid, keys in lists_by_product.items()},
        list_recency=list_recency,
        params=params,
        _avg_price_cache=avg_price,
    )


# ── candidate generation (bloqueo) ──────────────────────────
#
# El motor es O(nike × competidores). Bajar la constante no cambia el orden: con
# 5.000 productos son 6 millones de pares y cada uno cuesta lo que cuesta. El
# bloqueo ataca el otro lado: no puntuar los pares que no pueden ganar.
#
# CUIDADO — el brief del producto dice explícitamente que dos productos pueden
# verse distintos y competir igual, así que el prefiltro NO puede ser una
# heurística de parecido. Acá sólo se usan vetos que el scoring YA trata como
# incompatibilidad dura (`hard_mismatch`) y, sobre todo, hay una red de
# seguridad: si el bloque de un producto Nike queda con menos candidatos que los
# que va a persistir, ese producto vuelve al barrido completo. Un catálogo chico
# o disperso (la demo: 30 competidores, casi ninguno de la misma categoría)
# llena su cupo de top-N con pares de otra categoría, y perderlos sería cambiar
# el resultado, no acelerarlo.
#
# Todo es configurable bajo `competitive_match.candidate_filter` (ver
# backend/docs/matching.md). La clave no existe en weights.yaml: los defaults
# viven acá y se leen con `section(..., default=...)`.

_CANDIDATE_FILTER_DEFAULTS: dict[str, Any] = {
    # Interruptor general: false => barrido completo, como antes del cambio.
    "enabled": True,
    # Distinta `category` (categoría deportiva) y las dos presentes => no compiten.
    # Es el mismo criterio que `_score_semantic` ya castiga como hard_mismatch.
    "same_category": True,
    # Distinta `division` (FW/AP/EQ). APAGADO por defecto: hoy `division` no
    # pesa en el score (no está en `_SEMANTIC_FIELDS`), así que filtrar por ella
    # SÍ pierde pares persistibles — medido sobre el catálogo sintético: 36 de
    # 80 matches. Se prende cuando la división entre en el scoring.
    "same_division": False,
    # Conflicto duro de género (men vs women). APAGADO por defecto por lo mismo:
    # el scoring lo atenúa, no lo veta, y hay matches persistidos que lo cruzan.
    "gender_conflict": False,
    # Compartir al menos UN campo de uso (`use_case` | `sport` | `subcategory`).
    # Es el bloqueo que prototipó `tools/bench_scale.py`. Se pide uno solo para
    # que un producto sin `use_case` siga bloqueando por `sport`. APAGADO por
    # defecto: sobre un corpus CON las siete señales prendidas descarta mucho
    # más (90% de la grilla) pero cambia más el margen del top-N que
    # `same_category` sola. Ver docs/matching.md § prefiltro.
    "shared_use_field": False,
    # Red de seguridad: mínimo de candidatos por producto Nike antes de aceptar
    # el bloqueo. `null` => 3 × top_n_per_product.
    "min_candidates_per_product": None,
    # Rescate: un par con rivalidad DOCUMENTADA (mención editorial, co-mención
    # social o la misma lista de "mejores X") nunca se descarta por taxonomía.
    # Si el mercado los trata como alternativas, eso manda sobre la etiqueta de
    # categoría — es justo el caso del brief: "dos productos pueden verse
    # distintos y competir igual".
    "keep_documented_pairs": True,
}


def candidate_filter_config() -> dict[str, Any]:
    """Configuración del prefiltro, con defaults del módulo."""
    cfg = dict(_CANDIDATE_FILTER_DEFAULTS)
    cfg.update(section("competitive_match", "candidate_filter", default=None) or {})
    if cfg.get("min_candidates_per_product") is None:
        top_n = int((section("competitive_match", default={}) or {}).get("top_n_per_product", 10) or 10)
        cfg["min_candidates_per_product"] = 3 * top_n
    return cfg


#: Campos "de uso" del producto: los que el scoring semántico pondera más
#: fuerte (use_case 0.30, sport 0.10, subcategory 0.05 de `field_weights`).
_USE_FIELDS: tuple[str, ...] = ("use_case", "sport", "subcategory")


def _use_keys(product: dict) -> frozenset[tuple[str, str]]:
    """Claves de uso del producto: las que definen su bloque."""
    return frozenset((field, _norm(product.get(field))) for field in _USE_FIELDS
                     if _norm(product.get(field)))


def _has_documented_rivalry(nike_id: int, comp_id: int, ctx: MatchContext) -> bool:
    """¿Hay evidencia externa de que el mercado compara estos dos productos?

    Menciones editoriales del par, co-menciones sociales o una lista editorial
    compartida. Son tres lookups de diccionario sobre índices ya precargados.
    """
    key = _pair_key(nike_id, comp_id)
    if ctx.editorial.get(key) or ctx.social.get(key):
        return True
    return bool(ctx.list_sets.get(nike_id, frozenset()) & ctx.list_sets.get(comp_id, frozenset()))


def is_candidate(nike: dict, comp: dict, ctx: MatchContext | None = None) -> bool:
    """¿Este par merece que se lo puntúe? (sin la red de seguridad por producto).

    Sólo mira campos declarados del catálogo: es aritmética de diccionario,
    ~1 µs contra los ~250 µs que cuesta `compute_match`.
    """
    cfg = ctx.p.candidate_filter if ctx is not None else candidate_filter_config()
    if not cfg.get("enabled", True):
        return True
    if ctx is not None and cfg.get("keep_documented_pairs", True) and _has_documented_rivalry(
            nike["id"], comp["id"], ctx):
        return True
    if cfg.get("same_category", True):
        a, b = _norm(nike.get("category")), _norm(comp.get("category"))
        if a and b and a != b:
            return False
    if cfg.get("same_division", False):
        a, b = _norm(nike.get("division")), _norm(comp.get("division"))
        if a and b and a != b:
            return False
    if cfg.get("gender_conflict", False) and _gender_conflict(nike.get("gender"), comp.get("gender")):
        return False
    if cfg.get("shared_use_field", False) and not (_use_keys(nike) & _use_keys(comp)):
        return False
    return True


def candidates_for(nike: dict, ctx: MatchContext) -> tuple[list[dict], int]:
    """Competidores a puntuar para un producto Nike y cuántos se descartaron.

    Devuelve los productos en el MISMO orden en que aparecen en `ctx.products`:
    el desempate del top-N es un `sort` estable, así que conservar el orden
    conserva el resultado.
    """
    full = [
        comp for comp in ctx.products.values()
        if comp["brand_id"] != nike["brand_id"]
        and comp.get("country_code") == nike.get("country_code")
    ]
    cfg = ctx.p.candidate_filter
    if not cfg.get("enabled", True):
        return full, 0

    kept = [comp for comp in full if is_candidate(nike, comp, ctx)]
    # Red de seguridad: un bloque más chico que el cupo de persistencia se
    # descarta y ese producto Nike vuelve al barrido completo.
    if len(kept) < int(cfg.get("min_candidates_per_product") or 0):
        return full, 0
    return kept, len(full) - len(kept)


# ── FACTOR 1: visual ────────────────────────────────────────

_SILHOUETTE_ATTRS = ("silhouette", "silueta", "shape", "profile")
_COLOR_ATTRS = ("colors", "colorway", "color", "colores", "primary_color", "secondary_color")
_MATERIAL_ATTRS = ("materials", "material", "materiales", "upper_material", "midsole_material", "outsole_material")


def _score_visual(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Similitud visual: embedding (CLIP, opcional) + silueta + colores + materiales."""
    sub_weights = ctx.p.visual_sub_weights
    detail: dict[str, Any] = {}
    parts: dict[str, float | None] = {}

    # -- embedding (opcional) --
    emb_score: float | None = None
    module = _embeddings()
    if module is not None and hasattr(module, "image_similarity"):
        try:
            emb_score, method = module.image_similarity(nike, comp)
        except Exception as exc:  # pragma: no cover - la dependencia es opcional
            emb_score, method = None, f"error: {exc.__class__.__name__}"
        detail["embedding_method"] = method
    else:
        detail["embedding_method"] = "unavailable"
        detail["embedding_reason"] = "app.services.embeddings no disponible"
    parts["embedding"] = emb_score

    # -- fallback determinístico por atributos visuales --
    sil_a = ctx.attr(nike["id"], *_SILHOUETTE_ATTRS)
    sil_b = ctx.attr(comp["id"], *_SILHOUETTE_ATTRS)
    parts["silhouette"] = _field_similarity(sil_a, sil_b)
    detail["silhouette"] = {"nike": sil_a, "competitor": sil_b, "score": parts["silhouette"]}

    colors_a = ctx.attr_tokens(nike["id"], _COLOR_ATTRS, contains=("color",))
    colors_b = ctx.attr_tokens(comp["id"], _COLOR_ATTRS, contains=("color",))
    parts["colors"] = jaccard(colors_a, colors_b)
    detail["colors"] = {"nike": sorted(colors_a), "competitor": sorted(colors_b), "score": parts["colors"]}

    mat_a = ctx.attr_tokens(nike["id"], _MATERIAL_ATTRS, contains=("material",))
    mat_b = ctx.attr_tokens(comp["id"], _MATERIAL_ATTRS, contains=("material",))
    parts["materials"] = jaccard(mat_a, mat_b)
    detail["materials"] = {"nike": sorted(mat_a), "competitor": sorted(mat_b), "score": parts["materials"]}

    detail["sub_scores"] = {k: (round(v, 4) if v is not None else None) for k, v in parts.items()}
    detail["sub_weights_used"] = {k: sub_weights.get(k, 0.0) for k, v in parts.items() if v is not None}

    score = _blend(parts, sub_weights)
    if score is None:
        detail["reason"] = "sin señales visuales (ni embedding ni atributos)"
        return score, detail

    # Un score visual apoyado en una sola sub-señal débil no es creíble: si la
    # única evidencia es la etiqueta de silueta, "trainer" vs "runner" produce
    # un 0.0 rotundo sobre dos zapatillas que son el mismo tipo de producto.
    # Exigimos una fracción mínima del peso visual con datos reales; por debajo,
    # el factor se declara sin datos y `combine()` lo renormaliza.
    total_weight = ctx.p.visual_total_weight
    evidence = sum(sub_weights.get(k, 0.0) for k, v in parts.items() if v is not None)
    min_evidence = ctx.p.visual_min_evidence
    detail["evidence_weight"] = round(evidence / total_weight, 4)
    if evidence / total_weight < min_evidence:
        detail["reason"] = (
            f"evidencia visual insuficiente ({evidence / total_weight:.0%} del peso, "
            f"mínimo {min_evidence:.0%}): "
            + ", ".join(k for k, v in parts.items() if v is not None)
        )
        return None, detail

    return score, detail


# ── FACTOR 2: semantic ──────────────────────────────────────

_SEMANTIC_FIELDS = (
    "use_case",
    "category",
    "sport",
    "performance_vs_lifestyle",
    "gender",
    "subcategory",
)


# "unisex" no es un segmento distinto de "men"/"women": los incluye. Tratarlo
# como mismatch castiga a marcas que etiquetan unisex (ASICS, Adidas) contra
# marcas que separan por género — un artefacto de etiquetado, no competencia real.
_GENDER_UNIVERSAL = {"unisex", "uni", "todos", "all"}


def _gender_similarity(a: Any, b: Any) -> float | None:
    """Similitud de género tolerante a 'unisex'. None si falta alguno."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return None
    if na == nb:
        return 1.0
    if na in _GENDER_UNIVERSAL or nb in _GENDER_UNIVERSAL:
        return 1.0
    return 0.0


def _gender_conflict(a: Any, b: Any) -> bool:
    """Sólo hay conflicto real de género entre dos segmentos opuestos."""
    sim = _gender_similarity(a, b)
    return sim is not None and sim == 0.0


def _score_semantic(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Similitud de propósito: taxonomía ponderada + similitud textual de descripciones."""
    field_weights = ctx.p.semantic_field_weights
    penalty = ctx.p.semantic_penalty

    parts: dict[str, float | None] = {}
    detail: dict[str, Any] = {"fields": {}}

    for fname in _SEMANTIC_FIELDS:
        if fname == "gender":
            sim = _gender_similarity(nike.get(fname), comp.get(fname))
        else:
            sim = _field_similarity(nike.get(fname), comp.get(fname))
        parts[fname] = sim
        detail["fields"][fname] = {
            "nike": nike.get(fname),
            "competitor": comp.get(fname),
            "score": round(sim, 4) if sim is not None else None,
        }

    # -- similitud textual (embeddings/TF-IDF, opcional) --
    text_sim: float | None = None
    module = _embeddings()
    if module is not None and hasattr(module, "text_similarity"):
        try:
            text_sim = text_similarity(nike.get("description"), comp.get("description"), module)
            detail["text_similarity_backend"] = (
                module.backend_name() if hasattr(module, "backend_name") else "embeddings"
            )
        except Exception as exc:  # pragma: no cover - dependencia opcional
            text_sim = None
            detail["text_similarity_backend"] = f"error: {exc.__class__.__name__}"
    else:
        detail["text_similarity_backend"] = "unavailable"
        detail["text_similarity_reason"] = "app.services.embeddings no disponible"
    parts["text_similarity"] = text_sim
    detail["fields"]["text_similarity"] = {"score": round(text_sim, 4) if text_sim is not None else None}

    score = _blend(parts, field_weights)
    detail["field_weights_used"] = {
        k: field_weights.get(k, 0.0) for k, v in parts.items() if v is not None and field_weights.get(k, 0.0) > 0
    }

    if score is None:
        detail["reason"] = "sin taxonomía ni descripciones comparables"
        return None, detail

    # -- penalización dura: no comparten gender o category --
    # Ojo: para gender no alcanza con la desigualdad literal (ver _gender_conflict).
    mismatches = []
    if _gender_conflict(nike.get("gender"), comp.get("gender")):
        mismatches.append("gender")
    cat_a, cat_b = _norm(nike.get("category")), _norm(comp.get("category"))
    if cat_a and cat_b and cat_a != cat_b:
        mismatches.append("category")
    if mismatches:
        score = clamp(score * penalty)
        detail["hard_mismatch"] = {"fields": mismatches, "penalty": penalty}
    else:
        detail["hard_mismatch"] = None

    # -- atenuación por generación discontinuada --
    # Dos productos pueden describir exactamente el mismo propósito y aun así
    # no ser comparables HOY si alguno de los dos ya fue reemplazado por su
    # siguiente generación (Metcon 9 cuando el negocio empuja la Metcon 10).
    # Es una atenuación, no un veto: el par sigue existiendo y explicado.
    score, gen_detail = _apply_generation_penalty(score, nike, comp, ctx)
    detail["generation"] = gen_detail

    detail["score"] = round(score, 4)
    return score, detail


def _apply_generation_penalty(score: float, nike: dict, comp: dict,
                              ctx: MatchContext) -> tuple[float, dict]:
    """Atenúa el score semántico por cada lado del par que sea generación saliente.

    ``competitive_match.generation.stale_penalty`` (default 0.85) se aplica una
    vez por lado marcado ``stale``: dos generaciones viejas comparadas entre sí
    pesan aún menos que una sola.
    """
    gen_nike, gen_comp = ctx.generation(nike["id"]), ctx.generation(comp["id"])
    detail: dict[str, Any] = {
        "nike": (gen_nike.as_dict() if gen_nike else None),
        "competitor": (gen_comp.as_dict() if gen_comp else None),
    }
    if not ctx.p.generation_enabled:
        detail["penalty"] = None
        detail["reason"] = "competitive_match.generation.enabled = false"
        return score, detail

    penalty = ctx.p.generation_stale_penalty
    stale_sides = [
        side for side, gen in (("nike", gen_nike), ("competitor", gen_comp))
        if gen is not None and gen.stale
    ]
    detail["stale_sides"] = stale_sides
    detail["stale_penalty"] = penalty
    if not stale_sides:
        detail["penalty"] = None
        return score, detail

    applied = penalty ** len(stale_sides)
    detail["penalty"] = round(applied, 4)
    return clamp(score * applied), detail


# ── FACTOR 3: price ─────────────────────────────────────────


def _price_band_similarity(nike: dict, comp: dict, params: MatchParams | None = None) -> float | None:
    """1.0 si comparten banda; si no, decae según la distancia ordinal entre bandas."""
    a, b = _norm(nike.get("price_band")), _norm(comp.get("price_band"))
    if not a or not b:
        return None
    if a == b:
        return 1.0
    country = nike.get("country_code") or comp.get("country_code")
    if params is not None and country in params.price_band_order:
        order = params.price_band_order[country]
    else:
        bands = section("enrichment", "price_bands", country, default=None) or {}
        order = [_norm(k) for k in bands]
    if a in order and b in order and len(order) > 1:
        return clamp(1.0 - abs(order.index(a) - order.index(b)) / (len(order) - 1))
    return 0.0


def _score_price(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Cercanía de posicionamiento de precio: MSRP + precio actual + banda."""
    sub_weights = ctx.p.price_sub_weights
    tolerance = ctx.p.price_tolerance

    msrp_a, msrp_b = nike.get("msrp"), comp.get("msrp")
    cur_a = ctx.avg_current_price(nike["id"])
    cur_b = ctx.avg_current_price(comp["id"])

    parts: dict[str, float | None] = {
        "msrp": gap_similarity(msrp_a, msrp_b, tolerance),
        "current_price": gap_similarity(cur_a, cur_b, tolerance),
        "price_band": _price_band_similarity(nike, comp, ctx.p),
    }

    detail: dict[str, Any] = {
        "gap_tolerance_pct": tolerance,
        "msrp": {"nike": msrp_a, "competitor": msrp_b, "score": parts["msrp"]},
        "current_price": {"nike": cur_a, "competitor": cur_b, "score": parts["current_price"]},
        "price_band": {
            "nike": nike.get("price_band"),
            "competitor": comp.get("price_band"),
            "score": parts["price_band"],
        },
        "sub_weights_used": {k: sub_weights.get(k, 0.0) for k, v in parts.items() if v is not None},
    }
    if msrp_a is not None and msrp_b is not None:
        base = max(abs(float(msrp_a)), abs(float(msrp_b))) or 1.0
        detail["msrp_gap_pct"] = round((float(comp["msrp"]) - float(nike["msrp"])) / base * 100.0, 2)

    score = _blend(parts, sub_weights)
    if score is None:
        detail["reason"] = "sin MSRP, ni precios observados, ni banda de precio"
    return score, detail


# ── FACTOR 4: retailer overlap ──────────────────────────────


def _score_retailer_overlap(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Jaccard de los retailers donde se vende cada producto (competencia real en góndola)."""
    a = ctx.retailers_by_product.get(nike["id"], set())
    b = ctx.retailers_by_product.get(comp["id"], set())
    score = jaccard({str(x) for x in a}, {str(x) for x in b})
    detail: dict[str, Any] = {
        "nike_retailers": sorted(a),
        "competitor_retailers": sorted(b),
        "shared_retailers": sorted(a & b),
        "n_shared": len(a & b),
        "n_union": len(a | b),
    }
    if score is None:
        detail["reason"] = "alguno de los productos no tiene retailers observados"
    return score, detail


# ── FACTOR 5: editorial ─────────────────────────────────────


def _score_editorial(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Cuántas veces el mercado (medios) trata a estos productos como alternativas."""
    type_scores = ctx.p.editorial_type_scores
    k = ctx.p.editorial_k
    half_life = ctx.p.editorial_half_life

    key = _pair_key(nike["id"], comp["id"])
    points = 0.0
    mentions: list[dict] = []

    for row in ctx.editorial.get(key, ()):
        mtype = _norm(row.get("mention_type"))
        base = float(type_scores.get(mtype, 0.0))
        rw = recency_weight(row.get("published_at"), half_life)
        contrib = base * rw
        points += contrib
        mentions.append({
            "mention_type": row.get("mention_type"),
            "source_name": row.get("source_name"),
            "title": row.get("title"),
            "published_at": row.get("published_at"),
            "base": round(base, 4),
            "recency": round(rw, 4),
            "points": round(contrib, 4),
        })

    # Co-apariciones en la misma lista editorial (tipo same_list).
    # Se intersecan las listas de los dos productos (índice `lists_by_product`)
    # en vez de recorrer TODAS las listas del corpus en cada par.
    same_list_base = ctx.p.editorial_same_list_score
    shared_lists: list[dict] = []
    comp_lists = ctx.list_sets.get(comp["id"], frozenset())
    for list_key in ctx.lists_by_product.get(nike["id"], ()):
        if list_key not in comp_lists:
            continue
        rw = ctx.list_recency.get(list_key)
        if rw is None:
            dates = ctx.editorial_list_dates.get(list_key) or [None]
            rw = sum(recency_weight(d, half_life) for d in dates) / len(dates)
        contrib = same_list_base * rw
        points += contrib
        shared_lists.append({
            "list_key": list_key,
            "recency": round(rw, 4),
            "points": round(contrib, 4),
        })

    detail: dict[str, Any] = {
        "mentions": mentions,
        "n_mentions": len(mentions),
        "shared_lists": shared_lists,
        "n_shared_lists": len(shared_lists),
        "points": round(points, 4),
        "saturation_k": k,
        "recency_half_life_days": half_life,
    }

    if not mentions and not shared_lists:
        detail["reason"] = "ninguna mención editorial involucra a este par"
        return None, detail

    score = saturate(points, k)
    detail["score"] = round(score, 4)
    return score, detail


# ── FACTOR 6: social ────────────────────────────────────────


def _score_social(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Co-menciones del par en conversación pública. SIEMPRE agregado, nunca individuo."""
    k = ctx.p.social_k
    half_life = ctx.p.social_half_life
    min_comentions = ctx.p.social_min_comentions

    rows = ctx.social.get(_pair_key(nike["id"], comp["id"]), ())
    raw_comentions = 0.0
    weighted = 0.0
    periods: list[dict] = []
    for row in rows:
        count = float(row.get("comention_count") or 0)
        if count <= 0:
            continue
        rw = recency_weight(row.get("period_end") or row.get("period_start"), half_life)
        raw_comentions += count
        weighted += count * rw
        periods.append({
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "source_type": row.get("source_type"),
            "comention_count": count,
            "recency": round(rw, 4),
            "weighted": round(count * rw, 4),
        })

    detail: dict[str, Any] = {
        "periods": periods,
        "comentions": raw_comentions,
        "weighted_comentions": round(weighted, 4),
        "min_comentions": min_comentions,
        "saturation_k": k,
        "recency_half_life_days": half_life,
        "aggregate_only": True,
    }

    if not periods:
        detail["reason"] = "sin co-menciones agregadas para el par"
        return None, detail
    if raw_comentions < min_comentions:
        detail["reason"] = f"co-menciones ({raw_comentions:g}) por debajo de min_comentions ({min_comentions:g})"
        return None, detail

    score = saturate(weighted, k)
    detail["score"] = round(score, 4)
    return score, detail


# ── FACTOR 7: reviews ───────────────────────────────────────

# Léxico determinístico ES: sinónimo -> atributo canónico valorado por el consumidor.
# No es un peso (no va a weights.yaml): es el diccionario de extracción.
_REVIEW_LEXICON: dict[str, tuple[str, ...]] = {
    "comodidad": ("comodidad", "comodo", "comoda", "confort", "confortable"),
    "amortiguacion": ("amortiguacion", "amortigua", "acolchado", "cushion", "espuma", "blandita", "mullido"),
    "durabilidad": ("durabilidad", "durable", "duradero", "duran", "resistente", "aguanta", "desgaste"),
    "calce": ("calce", "calza", "horma", "ajuste", "ajusta", "fit"),
    "peso": ("peso", "liviana", "liviano", "ligera", "ligero", "pesada", "pesado"),
    "agarre": ("agarre", "adherencia", "traccion", "grip", "resbala", "antideslizante"),
    "transpirabilidad": ("transpirabilidad", "transpirable", "transpira", "ventilacion", "fresca", "fresco", "calor"),
    "estabilidad": ("estabilidad", "estable", "soporte", "pronacion", "sujecion", "firme"),
    "precio": ("precio", "cara", "caro", "barata", "barato", "vale lo que cuesta", "relacion precio", "costosa"),
    "talles": ("talle", "talles", "numero", "grande", "chico", "pequeno", "un numero mas", "un numero menos"),
    "diseno": ("diseno", "estetica", "linda", "lindo", "bonita", "bonito", "colores"),
    "calidad": ("calidad", "terminaciones", "materiales", "acabado"),
}


def _review_volume(rows: list[dict]) -> float:
    """Volumen de reviews: usa ``review_count`` si la fila es agregada, si no cuenta 1."""
    total = 0.0
    for row in rows:
        count = row.get("review_count")
        total += float(count) if count else 1.0
    return total


def _review_attributes(rows: list[dict]) -> set[str]:
    """Atributos valorados extraídos de los textos (léxico determinístico)."""
    found: set[str] = set()
    for row in rows:
        text = _norm(row.get("review_text"))
        if not text:
            continue
        for attr, synonyms in _REVIEW_LEXICON.items():
            if any(word in text for word in synonyms):
                found.add(attr)
    return found


def _avg_rating(rows: list[dict]) -> float | None:
    num = 0.0
    den = 0.0
    for row in rows:
        rating = row.get("rating")
        if rating is None:
            continue
        w = float(row.get("review_count") or 1)
        num += float(rating) * w
        den += w
    return num / den if den else None


def _score_reviews(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """¿Los consumidores valoran los mismos atributos y con qué satisfacción?"""
    min_reviews = ctx.p.reviews_min
    rating_weight = ctx.p.reviews_rating_weight

    # Volumen, atributos y rating dependen de UN producto: se memoizan por
    # producto en el contexto (ver `MatchContext.review_signals`).
    vol_a, attrs_a, rating_a = ctx.review_signals(nike["id"])
    vol_b, attrs_b, rating_b = ctx.review_signals(comp["id"])

    detail: dict[str, Any] = {
        "nike_review_volume": vol_a,
        "competitor_review_volume": vol_b,
        "min_reviews_for_signal": min_reviews,
        "rating_weight": rating_weight,
    }

    if vol_a < min_reviews or vol_b < min_reviews:
        detail["reason"] = "volumen de reviews por debajo de min_reviews_for_signal"
        return None, detail

    attr_sim = jaccard(attrs_a, attrs_b)

    rating_sim = None
    if rating_a is not None and rating_b is not None:
        rating_sim = clamp(1.0 - abs(rating_a - rating_b) / _RATING_SCALE)

    detail["nike_attributes"] = sorted(attrs_a)
    detail["competitor_attributes"] = sorted(attrs_b)
    detail["shared_attributes"] = sorted(attrs_a & attrs_b)
    detail["attribute_similarity"] = round(attr_sim, 4) if attr_sim is not None else None
    detail["nike_avg_rating"] = round(rating_a, 3) if rating_a is not None else None
    detail["competitor_avg_rating"] = round(rating_b, 3) if rating_b is not None else None
    detail["rating_similarity"] = round(rating_sim, 4) if rating_sim is not None else None

    # rating_weight es la porción del score que aporta la cercanía de rating.
    score = _blend(
        {"attributes": attr_sim, "rating": rating_sim},
        {"attributes": 1.0 - rating_weight, "rating": rating_weight},
    )
    if score is None:
        detail["reason"] = "reviews sin texto reconocible ni rating"
        return None, detail

    detail["score"] = round(score, 4)
    return score, detail


# ── combinación ─────────────────────────────────────────────

FACTOR_FUNCS = {
    "visual": _score_visual,
    "semantic": _score_semantic,
    "price": _score_price,
    "retailer_overlap": _score_retailer_overlap,
    "editorial": _score_editorial,
    "social": _score_social,
    "reviews": _score_reviews,
}


def compute_match(nike: dict, competitor: dict, ctx: MatchContext) -> CompositeScore:
    """Score competitivo 0..100 entre un producto Nike y uno competidor.

    Los factores sin datos se marcan ``available=False``, se excluyen y se
    renormaliza el peso: nunca penalizan. ``contribution`` (suma 100 entre los
    disponibles) es la feature importance que consume el frontend.
    """
    w = ctx.p.factor_weights
    factors: list[Factor] = []
    for name, func in FACTOR_FUNCS.items():
        score, detail = func(nike, competitor, ctx)
        factors.append(Factor(name=name, raw_score=score, weight=w.get(name, 0.0), detail=detail))
    return combine(factors, ctx.p.confidence_thresholds)


# ── persistencia ────────────────────────────────────────────


def evidence_adjusted(score: float, coverage: float) -> float:
    """Arrastra el score hacia un prior neutro según lo que NO se pudo medir.

    `compute_match` devuelve el score crudo, que responde "de lo que pudimos
    medir, ¿cuánto se parecen?" — y para eso renormalizar sobre los factores
    disponibles es lo correcto. Pero el ranking responde otra pregunta: "¿de
    todos los pares, cuáles compiten de verdad?". Ahí la falta de evidencia sí
    tiene que costar, o los pares sin datos editoriales ni sociales le ganan a
    los que tienen rivalidad documentada, sólo porque se los evalúa con los
    factores más fáciles.

    Ambos números se persisten: `raw_match_score` explica, `match_score` ordena.
    """
    cfg = section("competitive_match", "evidence_shrinkage", default={}) or {}
    if not cfg.get("enabled", False):
        return score
    prior = float(cfg.get("prior", 0.0)) * 100.0
    cov = clamp(float(coverage))
    return score * cov + prior * (1.0 - cov)


def run_matching(db_path: Path | str = DB_PATH) -> dict[str, int]:
    """Recalcula TODOS los matches competitivos y los persiste (idempotente).

    Para cada producto Nike (marca con ``is_focus=1``) evalúa contra todos los
    productos de otras marcas del mismo país, se queda con los que superan
    ``min_score_to_persist`` y persiste el ``top_n_per_product``.

    Al truncar en ``top_n_per_product`` desempata a favor de la generación
    VIGENTE del competidor: si dos generaciones de la misma línea rival puntúan
    casi igual, el cupo se lo lleva la que se está vendiendo hoy.
    """
    cfg = section("competitive_match", default={}) or {}
    min_score = float(cfg.get("min_score_to_persist", 0.0))
    top_n = int(cfg.get("top_n_per_product", 0)) or None

    ctx = build_context(db_path)

    nike_products = [p for p in ctx.products.values() if p.get("brand_is_focus")]
    pairs_evaluated = 0
    pairs_skipped = 0
    products_unfiltered = 0
    stale_pairs = 0
    kept: list[tuple[dict, dict, CompositeScore]] = []

    def _is_stale(pid: int) -> bool:
        gen = ctx.generation(pid)
        return bool(gen and gen.stale)

    for nike in nike_products:
        candidates: list[tuple[dict, CompositeScore]] = []
        block, skipped = candidates_for(nike, ctx)
        pairs_skipped += skipped
        products_unfiltered += 1 if skipped == 0 else 0
        for comp in block:
            pairs_evaluated += 1
            result = compute_match(nike, comp, ctx)
            if _is_stale(nike["id"]) or _is_stale(comp["id"]):
                stale_pairs += 1
            # El umbral y el orden usan el score ajustado por evidencia.
            if evidence_adjusted(result.score, result.coverage) >= min_score:
                candidates.append((comp, result))
        candidates.sort(
            key=lambda item: (evidence_adjusted(item[1].score, item[1].coverage),
                              0 if _is_stale(item[0]["id"]) else 1),
            reverse=True,
        )
        if top_n:
            candidates = candidates[:top_n]
        kept.extend((nike, comp, res) for comp, res in candidates)

    match_rows = [
        (nike["id"], comp["id"],
         round(evidence_adjusted(res.score, res.coverage), 4),
         round(res.score, 4), res.confidence, round(res.coverage, 4))
        for nike, comp, res in kept
    ]

    with get_conn(db_path) as conn:
        # Idempotencia: se borra todo antes de recalcular.
        conn.execute("DELETE FROM competitive_match_factors")
        conn.execute("DELETE FROM competitive_matches")
        conn.executemany(
            "INSERT INTO competitive_matches "
            "(nike_product_id, competitor_product_id, match_score, raw_match_score, "
            " confidence, coverage) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            match_rows,
        )
        ids = {
            (r["nike_product_id"], r["competitor_product_id"]): r["id"]
            for r in conn.execute(
                "SELECT id, nike_product_id, competitor_product_id FROM competitive_matches"
            ).fetchall()
        }
        factor_rows = [
            (
                ids[(nike["id"], comp["id"])],
                f["factor"],
                f["raw_score"],
                f["weight"],
                f["contribution"],
                1 if f["available"] else 0,
                to_json(f["detail"]),
            )
            for nike, comp, res in kept
            for f in res.factors
        ]
        conn.executemany(
            "INSERT INTO competitive_match_factors "
            "(match_id, factor, raw_score, weight, contribution, available, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            factor_rows,
        )

    return {
        "nike_products": len(nike_products),
        "pairs_evaluated": pairs_evaluated,
        # Candidate generation: pares que el prefiltro evitó puntuar y productos
        # Nike que igual barrieron el catálogo entero (red de seguridad).
        "pairs_skipped_by_filter": pairs_skipped,
        "products_without_filter": products_unfiltered,
        "matches": len(match_rows),
        "factors": len(factor_rows),
        # Diagnóstico de vigencia: cuántos pares involucraban una generación
        # saliente (atenuados en `semantic`) y cuántos Nike ya tienen sucesor.
        "stale_pairs": stale_pairs,
        "superseded_nike_products": sum(
            1 for p in nike_products
            if (ctx.generation(p["id"]) and ctx.generation(p["id"]).generations_behind > 0)
        ),
    }
