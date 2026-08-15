"""Extracción determinística de patrones competitivos en texto editorial.

Es el corazón del factor ``editorial``: convierte títulos y cuerpos de artículos
en filas de ``editorial_mentions`` con su ``mention_type``.

**Cero LLMs**: regex + reglas + el resolvedor de catálogo. Todo lo que sale de
acá es reproducible y auditable — si una mención aparece, se puede señalar la
regla y el fragmento exacto que la produjo.

Patrones que detecta (español rioplatense e inglés):

| Patrón                                             | ``mention_type`` |
|----------------------------------------------------|------------------|
| "X vs Y", "X versus Y", "X contra Y"                | ``versus``       |
| "alternative(s) to X", "alternativas a la X",       | ``alternative``  |
| "similar to X", "parecidas a las X", "en vez de X"  |                  |
| "best running shoes", "best daily trainers",        | ``same_list``    |
| "best basketball shoes", "las mejores zapatillas",  |                  |
| guías de compra                                     |                  |
| rankings y listas numeradas ("Top 5", "Ranking")    | ``ranking``      |
| "Review: X", "Análisis de la X", "probamos la X"    | ``review``       |

Convenciones de salida (idénticas a las que ya consume
``services/matching.py`` y ``services/brand_intelligence.py``):

  * ``versus`` / ``alternative`` / ``same_list`` -> par (``product_a_id``,
    ``product_b_id``).
  * ``ranking`` / ``review`` -> un solo producto en ``product_a_id``.
  * ``list_key`` agrupa a todos los productos de una misma lista o guía; el
    motor lo usa para puntuar co-apariciones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.collectors.base import setting
from app.collectors.resolve import CatalogResolver, Resolution, fold

# ── patrones ────────────────────────────────────────────────

VERSUS_RE = re.compile(r"(?<![a-z0-9])(?:vs\.?|versus|contra)(?![a-z0-9])")

ALTERNATIVE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:best\s+|top\s+\d+\s+)?alternatives?\s+to\s+(?:the\s+)?"),
    re.compile(r"alternativas?\s+(?:a|de)\s+(?:la|las|el|los)?\s*"),
    re.compile(r"similar\s+to\s+(?:the\s+)?"),
    re.compile(r"similares?\s+a\s+(?:la|las|el|los)?\s*"),
    re.compile(r"parecidas?\s+a\s+(?:la|las|el|los)?\s*"),
    re.compile(r"(?:en\s+lugar\s+de|en\s+vez\s+de|reemplaz(?:o|ar)\s+(?:de|para|a)?)\s+(?:la|las|el|los)?\s*"),
    re.compile(r"(?:shoes|sneakers|zapatillas)\s+(?:like|como)\s+(?:the|la|las)?\s*"),
)

BEST_LIST_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bbest\s+(?:[a-z'\-]+\s+){0,3}(?:shoes|trainers|sneakers|runners|boots)\b"),
    re.compile(r"\bbest\s+(?:running|daily|basketball|trail|training|walking|gym|racing)\b"),
    re.compile(r"\b(?:las\s+)?mejores\s+(?:zapatillas|zapas|championes|botines|sneakers)\b"),
    re.compile(r"\bmejores\s+(?:[a-z'\-]+\s+){0,3}(?:de|para)\s+\d{4}\b"),
)

GUIDE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bbuying\s+guide\b"),
    re.compile(r"\bshopping\s+guide\b"),
    re.compile(r"\bgu[ií]a\s+de\s+compra\b"),
    re.compile(r"\bqu[eé]\s+zapatillas?\s+comprar\b"),
    re.compile(r"\bpara\s+comprar\s+en\s+\d{4}\b"),
)

RANKING_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\branking\b"),
    re.compile(r"\btop\s*\d{1,2}\b"),
    re.compile(r"\blos?\s+\d{1,2}\s+mejores\b"),
    re.compile(r"\bpuesto\s+n?[°º]?\s*\d{1,2}\b"),
)

RANK_ITEM_RE = re.compile(r"^\s*(?:#\s*)?(\d{1,2})\s*[\.\)\-–—:]\s+")

REVIEW_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\breview\b"),
    re.compile(r"\ban[aá]lisis\b"),
    re.compile(r"\bprobamos\b"),
    re.compile(r"\bpuesta\s+a\s+prueba\b"),
    re.compile(r"\bprimeras\s+impresiones\b"),
    re.compile(r"\bfirst\s+look\b"),
    re.compile(r"\brese[nñ]a\b"),
)

#: Orden de fuerza: si un par ya se detectó como "versus", no se degrada.
TYPE_PRIORITY = {"versus": 0, "alternative": 1, "same_list": 2, "ranking": 3, "review": 4}

_SENTENCE_SPLIT_RE = re.compile(r"[\.!?\n;]+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Document:
    """Artículo normalizado, listo para extraer patrones."""

    title: str
    text: str = ""
    url: str = ""
    source_name: str = ""
    published_at: str | None = None
    country_code: str | None = None
    list_items: tuple[str, ...] = ()

    @property
    def body(self) -> str:
        return "\n".join(filter(None, [self.text, *self.list_items]))

    @property
    def full(self) -> str:
        return "\n".join(filter(None, [self.title, self.body]))


@dataclass
class Candidate:
    """Una mención detectada, antes de convertirse en fila."""

    mention_type: str
    product_a_id: int
    product_b_id: int | None
    excerpt: str
    pattern: str
    list_key: str | None = None
    confidence: float = 1.0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def pair(self) -> frozenset[int]:
        ids = {self.product_a_id}
        if self.product_b_id is not None:
            ids.add(self.product_b_id)
        return frozenset(ids)


# ── utilidades ──────────────────────────────────────────────


def _matches_any(patterns: Iterable[re.Pattern[str]], text: str) -> re.Match[str] | None:
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found
    return None


def slugify(text: str, *, max_len: int = 60) -> str:
    slug = _SLUG_RE.sub("-", fold(text or "")).strip("-")
    return slug[:max_len].strip("-")


def list_key_for(doc: Document) -> str:
    """Clave estable que agrupa a los productos de una misma lista.

    Se prefiere el último segmento de la URL (estable entre corridas) y se cae
    al slug del título.
    """
    url = (doc.url or "").split("?")[0].rstrip("/")
    if url:
        tail = url.rsplit("/", 1)[-1]
        slug = slugify(tail)
        if slug and not slug.isdigit() and len(slug) > 3:
            return slug
    return slugify(doc.title) or "lista-sin-clave"


def _sentences(text: str) -> list[tuple[str, int]]:
    """Oraciones con su offset en el texto original."""
    out: list[tuple[str, int]] = []
    position = 0
    for chunk in _SENTENCE_SPLIT_RE.split(text or ""):
        start = text.find(chunk, position) if chunk else position
        if chunk.strip():
            out.append((chunk, max(start, 0)))
        position = (start + len(chunk)) if chunk else position + 1
    return out


def _excerpt(text: str, *, max_chars: int | None = None) -> str:
    limit = int(max_chars if max_chars is not None
                else setting("editorial", "max_excerpt_chars", default=280))
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0] + "…"


def is_list_document(doc: Document) -> bool:
    """¿Es una lista/guía de compra ("best X", "las mejores…", buying guide)?"""
    title = fold(doc.title or "")
    if _matches_any(BEST_LIST_RES, title) or _matches_any(GUIDE_RES, title):
        return True
    head = fold(doc.text or "")[:400]
    return bool(_matches_any(BEST_LIST_RES, head) or _matches_any(GUIDE_RES, head))


def is_ranking_document(doc: Document) -> bool:
    """¿La lista está ordenada (ranking, top N, ítems numerados)?"""
    title = fold(doc.title or "")
    if _matches_any(RANKING_RES, title):
        return True
    numbered = sum(1 for item in doc.list_items if RANK_ITEM_RE.match(item))
    if numbered >= 2:
        return True
    numbered_lines = sum(1 for line in (doc.text or "").splitlines() if RANK_ITEM_RE.match(line))
    return numbered_lines >= 2


def is_review_document(doc: Document) -> bool:
    return bool(_matches_any(REVIEW_RES, fold(doc.title or "")))


# ── detectores ──────────────────────────────────────────────


def _detect_versus(doc: Document, resolver: CatalogResolver,
                   list_key: str | None) -> list[Candidate]:
    """"X vs Y" / "X versus Y" / "X contra Y" en título y en cada oración."""
    out: list[Candidate] = []
    segments: list[str] = [doc.title or ""]
    segments += [sentence for sentence, _pos in _sentences(doc.body)]

    for segment in segments:
        folded = fold(segment)
        for match in VERSUS_RE.finditer(folded):
            left, right = segment[:match.start()], segment[match.end():]
            left_hits = resolver.find_products(left)
            right_hits = resolver.find_products(right)
            if not left_hits or not right_hits:
                continue
            a = left_hits[-1]      # el más cercano al separador
            b = right_hits[0]
            if a.product_id == b.product_id:
                continue
            out.append(Candidate(
                mention_type="versus",
                product_a_id=a.product_id,
                product_b_id=b.product_id,
                excerpt=_excerpt(segment),
                pattern=f"versus:{match.group(0)}",
                list_key=list_key,
                confidence=min(a.score, b.score),
                detail={"a": a.as_dict(), "b": b.as_dict()},
            ))
    return out


def _detect_alternative(doc: Document, resolver: CatalogResolver,
                        list_key: str | None) -> list[Candidate]:
    """"alternatives to X" / "alternativas a la X" / "similar to X"…

    El ancla X sale del patrón; los otros productos del artículo (o de la misma
    oración) son las alternativas propuestas.
    """
    out: list[Candidate] = []

    def anchor_in(segment: str) -> tuple[Resolution | None, str]:
        folded = fold(segment)
        for pattern in ALTERNATIVE_RES:
            match = pattern.search(folded)
            if not match:
                continue
            tail = segment[match.end():match.end() + 90]
            hits = resolver.find_products(tail)
            if hits:
                return hits[0], match.group(0).strip()
        return None, ""

    # 1. Ancla en el título: todo el artículo habla de alternativas a X.
    title_anchor, title_pattern = anchor_in(doc.title or "")
    if title_anchor is not None:
        for hit in resolver.find_products(doc.body):
            if hit.product_id == title_anchor.product_id:
                continue
            sentence = _sentence_around(doc.body, hit.start)
            out.append(Candidate(
                mention_type="alternative",
                product_a_id=title_anchor.product_id,
                product_b_id=hit.product_id,
                excerpt=_excerpt(sentence or doc.title),
                pattern=f"alternative:{title_pattern}",
                list_key=list_key,
                confidence=min(title_anchor.score, hit.score),
                detail={"anchor": title_anchor.as_dict(), "alternative": hit.as_dict()},
            ))

    # 2. Ancla dentro de una oración del cuerpo.
    for sentence, _pos in _sentences(doc.body):
        anchor, pattern = anchor_in(sentence)
        if anchor is None:
            continue
        for hit in resolver.find_products(sentence):
            if hit.product_id == anchor.product_id:
                continue
            out.append(Candidate(
                mention_type="alternative",
                product_a_id=anchor.product_id,
                product_b_id=hit.product_id,
                excerpt=_excerpt(sentence),
                pattern=f"alternative:{pattern}",
                list_key=list_key,
                confidence=min(anchor.score, hit.score),
                detail={"anchor": anchor.as_dict(), "alternative": hit.as_dict()},
            ))
    return out


def _sentence_around(text: str, offset: int) -> str:
    for sentence, start in _sentences(text):
        if start <= offset < start + len(sentence):
            return sentence
    return ""


def _list_products(doc: Document, resolver: CatalogResolver) -> list[tuple[Resolution, str]]:
    """Productos de la lista, en orden de aparición, con su fragmento."""
    max_products = int(setting("editorial", "max_products_per_list", default=12))
    seen: set[int] = set()
    out: list[tuple[Resolution, str]] = []

    for item in doc.list_items:
        for hit in resolver.find_products(item):
            if hit.product_id in seen:
                continue
            seen.add(hit.product_id)
            out.append((hit, item))

    body = doc.text or ""
    for hit in resolver.find_products(body):
        if hit.product_id in seen:
            continue
        seen.add(hit.product_id)
        out.append((hit, _sentence_around(body, hit.start) or doc.title))

    return out[:max_products]


def _detect_list(doc: Document, resolver: CatalogResolver,
                 list_key: str) -> list[Candidate]:
    """Listas "best X" / "las mejores…" / guías de compra y rankings."""
    products = _list_products(doc, resolver)
    if len(products) < 2:
        return []

    if is_ranking_document(doc):
        # Ranking: cada producto es una fila con su posición; el ``list_key``
        # compartido es lo que agrupa la lista (mismo criterio que el dataset).
        return [
            Candidate(
                mention_type="ranking",
                product_a_id=hit.product_id,
                product_b_id=None,
                excerpt=_excerpt(fragment),
                pattern="ranking",
                list_key=list_key,
                confidence=hit.score,
                detail={"position": position, "product": hit.as_dict()},
            )
            for position, (hit, fragment) in enumerate(products, start=1)
        ]

    # Lista sin orden explícito: pares "aparecen en la misma lista".
    max_pairs = int(setting("editorial", "max_same_list_pairs", default=20))
    pairs: list[tuple[int, int, int]] = []  # (distancia, i, j)
    for i in range(len(products)):
        for j in range(i + 1, len(products)):
            pairs.append((j - i, i, j))
    pairs.sort()

    out: list[Candidate] = []
    for _distance, i, j in pairs[:max_pairs]:
        hit_a, fragment_a = products[i]
        hit_b, fragment_b = products[j]
        shared = fragment_a if fragment_a == fragment_b else f"{fragment_a} {fragment_b}"
        out.append(Candidate(
            mention_type="same_list",
            product_a_id=hit_a.product_id,
            product_b_id=hit_b.product_id,
            excerpt=_excerpt(shared),
            pattern="same_list",
            list_key=list_key,
            confidence=min(hit_a.score, hit_b.score),
            detail={"a": hit_a.as_dict(), "b": hit_b.as_dict()},
        ))
    return out


def _detect_review(doc: Document, resolver: CatalogResolver) -> list[Candidate]:
    """Review/análisis de un solo producto."""
    if not is_review_document(doc):
        return []
    hits = resolver.find_products(doc.title or "")
    if len(hits) != 1:
        # Con dos productos en el título no es una review: es una comparación.
        return []
    hit = hits[0]
    return [Candidate(
        mention_type="review",
        product_a_id=hit.product_id,
        product_b_id=None,
        excerpt=_excerpt(doc.text or doc.title),
        pattern="review",
        list_key=None,
        confidence=hit.score,
        detail={"product": hit.as_dict()},
    )]


# ── API pública ─────────────────────────────────────────────


def extract_candidates(doc: Document, resolver: CatalogResolver) -> list[Candidate]:
    """Detecta todos los patrones competitivos de un documento.

    Deduplica por par: si un par ya apareció como ``versus`` no se vuelve a
    emitir como ``alternative`` o ``same_list`` (se queda el vínculo más fuerte).
    """
    is_list = is_list_document(doc) or is_ranking_document(doc)
    list_key = list_key_for(doc) if is_list else None

    candidates: list[Candidate] = []
    candidates += _detect_versus(doc, resolver, list_key)
    candidates += _detect_alternative(doc, resolver, list_key)
    if is_list:
        candidates += _detect_list(doc, resolver, list_key or list_key_for(doc))
    candidates += _detect_review(doc, resolver)

    best: dict[frozenset[int], Candidate] = {}
    singles: dict[tuple[str, int], Candidate] = {}
    out: list[Candidate] = []
    for candidate in candidates:
        if candidate.product_b_id is None:
            key = (candidate.mention_type, candidate.product_a_id)
            if key in singles:
                continue
            singles[key] = candidate
            out.append(candidate)
            continue
        pair = candidate.pair
        current = best.get(pair)
        if current is None:
            best[pair] = candidate
        elif TYPE_PRIORITY[candidate.mention_type] < TYPE_PRIORITY[current.mention_type]:
            best[pair] = candidate

    out.extend(best.values())
    out.sort(key=lambda c: (TYPE_PRIORITY[c.mention_type], c.product_a_id,
                            c.product_b_id or 0))
    return out


def extract_mentions(doc: Document, resolver: CatalogResolver) -> list[dict]:
    """Devuelve filas listas para insertar en ``editorial_mentions``."""
    rows: list[dict] = []
    for candidate in extract_candidates(doc, resolver):
        rows.append({
            "source_name": doc.source_name or None,
            "url": doc.url or None,
            "title": doc.title or None,
            "published_at": doc.published_at,
            "mention_type": candidate.mention_type,
            "product_a_id": candidate.product_a_id,
            "product_b_id": candidate.product_b_id,
            "list_key": candidate.list_key,
            "excerpt": candidate.excerpt or None,
            "country_code": doc.country_code,
        })
    return rows
