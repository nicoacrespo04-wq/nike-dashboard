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

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
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
    global _embeddings_module
    _embeddings_module = _EMBEDDINGS_SENTINEL


# ── normalización de texto ──────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _norm(value: Any) -> str:
    """Minúsculas, sin acentos, sin espacios de más."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def _tokens(value: Any) -> set[str]:
    return set(_TOKEN_RE.findall(_norm(value)))


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
    # Memo interno: promedio de precio actual por producto.
    _avg_price_cache: dict[int, float | None] = field(default_factory=dict, repr=False)

    # -- accesos convenientes ------------------------------------------------

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

    def attr_tokens(self, product_id: int, names: tuple[str, ...], contains: tuple[str, ...] = ()) -> set[str]:
        """Tokens de todos los atributos que matcheen por nombre exacto o por substring."""
        bag = self.attributes.get(product_id, {})
        out: set[str] = set()
        for key, value in bag.items():
            nkey = _norm(key)
            if nkey in names or any(part in nkey for part in contains):
                out |= _tokens(value)
        return out

    def avg_current_price(self, product_id: int) -> float | None:
        """Promedio del último precio observado en cada retailer (memoizado)."""
        if product_id in self._avg_price_cache:
            return self._avg_price_cache[product_id]
        prices = [
            float(obs["current_price"])
            for (pid, _rid), obs in self.latest_price.items()
            if pid == product_id and obs.get("current_price") is not None
        ]
        value = sum(prices) / len(prices) if prices else None
        self._avg_price_cache[product_id] = value
        return value


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

    return MatchContext(
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
    )


# ── FACTOR 1: visual ────────────────────────────────────────

_SILHOUETTE_ATTRS = ("silhouette", "silueta", "shape", "profile")
_COLOR_ATTRS = ("colors", "colorway", "color", "colores", "primary_color", "secondary_color")
_MATERIAL_ATTRS = ("materials", "material", "materiales", "upper_material", "midsole_material", "outsole_material")


def _score_visual(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Similitud visual: embedding (CLIP, opcional) + silueta + colores + materiales."""
    sub_weights = weights("competitive_match", "visual", "sub_weights")
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


# ── FACTOR 2: semantic ──────────────────────────────────────

_SEMANTIC_FIELDS = (
    "use_case",
    "category",
    "sport",
    "performance_vs_lifestyle",
    "gender",
    "subcategory",
)


def _score_semantic(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Similitud de propósito: taxonomía ponderada + similitud textual de descripciones."""
    field_weights = weights("competitive_match", "semantic", "field_weights")
    penalty = float(section("competitive_match", "semantic", "hard_mismatch_penalty", default=1.0))

    parts: dict[str, float | None] = {}
    detail: dict[str, Any] = {"fields": {}}

    for fname in _SEMANTIC_FIELDS:
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
            text_sim = module.text_similarity(nike.get("description"), comp.get("description"))
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
    mismatches = []
    for fname in ("gender", "category"):
        a, b = _norm(nike.get(fname)), _norm(comp.get(fname))
        if a and b and a != b:
            mismatches.append(fname)
    if mismatches:
        score = clamp(score * penalty)
        detail["hard_mismatch"] = {"fields": mismatches, "penalty": penalty}
    else:
        detail["hard_mismatch"] = None

    detail["score"] = round(score, 4)
    return score, detail


# ── FACTOR 3: price ─────────────────────────────────────────


def _price_band_similarity(nike: dict, comp: dict) -> float | None:
    """1.0 si comparten banda; si no, decae según la distancia ordinal entre bandas."""
    a, b = _norm(nike.get("price_band")), _norm(comp.get("price_band"))
    if not a or not b:
        return None
    if a == b:
        return 1.0
    country = nike.get("country_code") or comp.get("country_code")
    bands = section("enrichment", "price_bands", country, default=None) or {}
    order = [_norm(k) for k in bands]
    if a in order and b in order and len(order) > 1:
        return clamp(1.0 - abs(order.index(a) - order.index(b)) / (len(order) - 1))
    return 0.0


def _score_price(nike: dict, comp: dict, ctx: MatchContext) -> tuple[float | None, dict]:
    """Cercanía de posicionamiento de precio: MSRP + precio actual + banda."""
    sub_weights = weights("competitive_match", "price", "sub_weights")
    tolerance = float(section("competitive_match", "price", "gap_tolerance_pct", default=0.0))

    msrp_a, msrp_b = nike.get("msrp"), comp.get("msrp")
    cur_a = ctx.avg_current_price(nike["id"])
    cur_b = ctx.avg_current_price(comp["id"])

    parts: dict[str, float | None] = {
        "msrp": gap_similarity(msrp_a, msrp_b, tolerance),
        "current_price": gap_similarity(cur_a, cur_b, tolerance),
        "price_band": _price_band_similarity(nike, comp),
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
    type_scores = weights("competitive_match", "editorial", "mention_type_scores")
    k = float(section("competitive_match", "editorial", "saturation_k", default=1.0))
    half_life = float(section("competitive_match", "editorial", "recency_half_life_days", default=0.0))

    key = _pair_key(nike["id"], comp["id"])
    points = 0.0
    mentions: list[dict] = []

    for row in ctx.editorial.get(key, []):
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
    same_list_base = float(type_scores.get("same_list", 0.0))
    shared_lists: list[dict] = []
    for list_key, pids in ctx.editorial_lists.items():
        if nike["id"] in pids and comp["id"] in pids:
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
    k = float(section("competitive_match", "social", "saturation_k", default=1.0))
    half_life = float(section("competitive_match", "social", "recency_half_life_days", default=0.0))
    min_comentions = float(section("competitive_match", "social", "min_comentions", default=0))

    rows = ctx.social.get(_pair_key(nike["id"], comp["id"]), [])
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
    min_reviews = float(section("competitive_match", "reviews", "min_reviews_for_signal", default=0))
    rating_weight = float(section("competitive_match", "reviews", "rating_weight", default=0.0))

    rows_a = ctx.reviews.get(nike["id"], [])
    rows_b = ctx.reviews.get(comp["id"], [])
    vol_a, vol_b = _review_volume(rows_a), _review_volume(rows_b)

    detail: dict[str, Any] = {
        "nike_review_volume": vol_a,
        "competitor_review_volume": vol_b,
        "min_reviews_for_signal": min_reviews,
        "rating_weight": rating_weight,
    }

    if vol_a < min_reviews or vol_b < min_reviews:
        detail["reason"] = "volumen de reviews por debajo de min_reviews_for_signal"
        return None, detail

    attrs_a, attrs_b = _review_attributes(rows_a), _review_attributes(rows_b)
    attr_sim = jaccard(attrs_a, attrs_b)

    rating_a, rating_b = _avg_rating(rows_a), _avg_rating(rows_b)
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
    w = weights("competitive_match", "weights")
    factors: list[Factor] = []
    for name, func in FACTOR_FUNCS.items():
        score, detail = func(nike, competitor, ctx)
        factors.append(Factor(name=name, raw_score=score, weight=float(w.get(name, 0.0)), detail=detail))
    return combine(factors, section("competitive_match", "confidence_thresholds"))


# ── persistencia ────────────────────────────────────────────


def run_matching(db_path: Path | str = DB_PATH) -> dict[str, int]:
    """Recalcula TODOS los matches competitivos y los persiste (idempotente).

    Para cada producto Nike (marca con ``is_focus=1``) evalúa contra todos los
    productos de otras marcas del mismo país, se queda con los que superan
    ``min_score_to_persist`` y persiste el ``top_n_per_product``.
    """
    cfg = section("competitive_match", default={}) or {}
    min_score = float(cfg.get("min_score_to_persist", 0.0))
    top_n = int(cfg.get("top_n_per_product", 0)) or None

    ctx = build_context(db_path)

    nike_products = [p for p in ctx.products.values() if p.get("brand_is_focus")]
    pairs_evaluated = 0
    kept: list[tuple[dict, dict, CompositeScore]] = []

    for nike in nike_products:
        candidates: list[tuple[dict, CompositeScore]] = []
        for comp in ctx.products.values():
            if comp["brand_id"] == nike["brand_id"]:
                continue
            if comp.get("country_code") != nike.get("country_code"):
                continue
            pairs_evaluated += 1
            result = compute_match(nike, comp, ctx)
            if result.score >= min_score:
                candidates.append((comp, result))
        candidates.sort(key=lambda item: item[1].score, reverse=True)
        if top_n:
            candidates = candidates[:top_n]
        kept.extend((nike, comp, res) for comp, res in candidates)

    match_rows = [
        (nike["id"], comp["id"], round(res.score, 4), res.confidence, round(res.coverage, 4))
        for nike, comp, res in kept
    ]

    with get_conn(db_path) as conn:
        # Idempotencia: se borra todo antes de recalcular.
        conn.execute("DELETE FROM competitive_match_factors")
        conn.execute("DELETE FROM competitive_matches")
        conn.executemany(
            "INSERT INTO competitive_matches "
            "(nike_product_id, competitor_product_id, match_score, confidence, coverage) "
            "VALUES (?, ?, ?, ?, ?)",
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
        "matches": len(match_rows),
        "factors": len(factor_rows),
    }
