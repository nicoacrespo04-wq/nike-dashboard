"""Resolución de texto libre → ``product_id`` del catálogo.

El problema: un artículo dice *"the Nova Blast 5"*, *"la Pegasus 41"* o
*"el New Balance 1080v13"*; la base tiene ``ASICS Novablast 5``,
``Nike Pegasus 41`` y ``New Balance 1080v13``.

**Criterio de diseño: es preferible perder una mención que atribuirla al
producto equivocado.** Una mención mal resuelta inventa competencia que no
existe, y esa competencia falsa se propaga al match score, al business
importance y a las recomendaciones. Por eso:

  1. Cada resolución devuelve un **score de confianza** 0..1.
  2. Por debajo de ``accept_threshold`` se descarta.
  3. Si el segundo mejor candidato está a menos de ``ambiguity_margin``, se
     descarta por **ambigüedad** (``"Pegasus"`` a secas puede ser la Pegasus 41
     o la Pegasus Trail 5: no se elige ninguna).
  4. **Conflicto de versión** (``Pegasus 40`` vs ``Pegasus 41``) y **conflicto
     de marca** (``Adidas Pegasus``) descartan el candidato de plano.

Sin LLMs: normalización + difflib, con scikit-learn como *prefiltro* opcional
de candidatos cuando el catálogo es grande (nunca cambia el criterio de corte).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from app.collectors.base import setting
from app.config import DB_PATH
from app.db import query

log = logging.getLogger(__name__)

#: Palabras que no identifican un producto: no se resuelven solas.
STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "and", "or", "vs", "versus", "best", "top",
    "review", "guide", "buying", "shoes", "shoe", "sneakers", "sneaker", "trainer",
    "trainers", "running", "runner", "runners", "daily", "basketball", "training",
    "trail", "road", "race", "racing", "walking", "gym",
    "la", "las", "el", "los", "un", "una", "unas", "unos", "de", "del", "al", "y", "o",
    "con", "sin", "para", "por", "en", "que", "mas", "menos", "mejor", "mejores",
    "peor", "peores", "zapatilla", "zapatillas", "modelo", "modelos", "contra",
    "alternativa", "alternativas", "similar", "similares", "parecida", "parecidas",
    "comparativa", "guia", "compra", "ranking", "analisis", "prueba", "probamos",
    "opinion", "resena", "puesto", "lugar", "primera", "segunda", "tercera",
})

_TOKEN_RE = re.compile(r"[0-9a-z]+")
_NUMBER_RE = re.compile(r"\d+")

#: Un nombre de producto nunca cruza uno de estos tokens ("Pegasus 41 vs
#: Novablast 5" son dos productos, no uno).
BOUNDARY_TOKENS = frozenset({
    "vs", "versus", "contra", "and", "or", "y", "o", "u", "e", "pero", "aunque",
    "que", "como", "frente", "ante", "entre", "sobre", "segun", "mientras",
})

#: Separadores admitidos DENTRO de un nombre ("Gel-Nimbus 26", "MB.04", "AF1 '07").
_INNER_GAP_RE = re.compile(r"[\s\-–—’'./&+]*")

# Traducción 1:1 (preserva longitud => los offsets del texto original siguen
# siendo válidos después de plegar acentos).
_FOLD_MAP = str.maketrans({
    "á": "a", "à": "a", "ä": "a", "â": "a", "ã": "a", "å": "a",
    "é": "e", "è": "e", "ë": "e", "ê": "e",
    "í": "i", "ì": "i", "ï": "i", "î": "i",
    "ó": "o", "ò": "o", "ö": "o", "ô": "o", "õ": "o",
    "ú": "u", "ù": "u", "ü": "u", "û": "u",
    "ñ": "n", "ç": "c", "ý": "y", "’": "'", "‘": "'", "´": "'", "`": "'",
})


def fold(text: str) -> str:
    """Minúsculas + acentos plegados **preservando la longitud** del string."""
    if not text:
        return ""
    lowered = text.lower()
    folded = lowered.translate(_FOLD_MAP)
    if len(folded) != len(text):  # pragma: no cover - salvaguarda de offsets
        folded = "".join(
            ch if len(ch) == 1 else ch[0]
            for ch in (unicodedata.normalize("NFKD", c)[0] for c in lowered)
        )
    return folded if len(folded) == len(text) else lowered


def normalize(text: str | None) -> str:
    """Forma canónica para comparar: minúsculas, sin acentos ni puntuación."""
    if not text:
        return ""
    return " ".join(_TOKEN_RE.findall(fold(str(text))))


def _squash(text: str) -> str:
    """Sin espacios: ``"nova blast 5"`` y ``"novablast 5"`` colapsan al mismo string."""
    return text.replace(" ", "")


def _numbers(text: str) -> frozenset[int]:
    """Números presentes (``"1080v13"`` -> {1080, 13}, ``"'07"`` -> {7})."""
    return frozenset(int(n) for n in _NUMBER_RE.findall(text))


def _token_f1(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    shared = len(sa & sb)
    return 2.0 * shared / (len(sa) + len(sb)) if shared else 0.0


@dataclass(frozen=True)
class Resolution:
    """Una mención textual atribuida a un producto del catálogo."""

    product_id: int
    score: float                  # 0..1, confianza de la atribución
    text: str                     # fragmento del texto original que matcheó
    matched_alias: str            # alias del catálogo que ganó
    start: int = 0                # offset en el texto de entrada
    end: int = 0
    runner_up: float = 0.0        # score del segundo producto (margen)
    method: str = "difflib"

    def as_dict(self) -> dict[str, Any]:
        return {"product_id": self.product_id, "score": round(self.score, 4),
                "text": self.text, "matched_alias": self.matched_alias,
                "runner_up": round(self.runner_up, 4), "method": self.method}


@dataclass
class _Alias:
    product_id: int
    text: str
    tokens: tuple[str, ...]
    squashed: str
    numbers: frozenset[int]
    brand_tokens: frozenset[str]


class CatalogResolver:
    """Índice del catálogo + matching difuso con umbral de confianza."""

    def __init__(self, products: list[dict], brands: list[dict] | None = None, *,
                 accept_threshold: float | None = None,
                 ambiguity_margin: float | None = None,
                 max_ngram: int | None = None) -> None:
        self.accept_threshold = float(
            accept_threshold if accept_threshold is not None
            else setting("resolve", "accept_threshold", default=0.82))
        self.ambiguity_margin = float(
            ambiguity_margin if ambiguity_margin is not None
            else setting("resolve", "ambiguity_margin", default=0.05))
        self.max_ngram = int(max_ngram if max_ngram is not None
                             else setting("resolve", "max_ngram", default=5))

        self.products: dict[int, dict] = {int(p["id"]): p for p in products}
        brand_rows = brands or []
        self.brand_names: dict[int, str] = {int(b["id"]): normalize(b.get("name"))
                                            for b in brand_rows}
        if not self.brand_names:
            # Sin tabla de marcas: se infieren de la primera palabra del nombre.
            self.brand_names = {}
        self.brand_phrases: dict[str, int] = {
            name: bid for bid, name in self.brand_names.items() if name
        }
        self.aliases: list[_Alias] = []
        self._build_aliases()
        self._vocab: frozenset[str] = frozenset(
            token for alias in self.aliases for token in alias.tokens
            if token not in STOPWORDS)
        # Números que identifican a cada producto (versión, modelo). Si la
        # mención trae un número y NINGUNO coincide con los del producto, es
        # otra versión: "Novablast 4" no es la Novablast 5.
        product_numbers: dict[int, set[int]] = {}
        for alias in self.aliases:
            product_numbers.setdefault(alias.product_id, set()).update(alias.numbers)
        self.product_numbers: dict[int, frozenset[int]] = {
            pid: frozenset(nums) for pid, nums in product_numbers.items()}
        self._vectorizer: Any = None
        self._matrix: Any = None
        self._maybe_build_tfidf()
        self.stats: dict[str, int] = {}

    # -- construcción --------------------------------------------------------

    @classmethod
    def from_db(cls, db_path: Path | str = DB_PATH, **kwargs: Any) -> "CatalogResolver":
        products = query(
            "SELECT id, brand_id, product_name, normalized_product_name, franchise, "
            "model, version, country_code FROM products", path=db_path)
        brands = query("SELECT id, name, is_focus FROM brands", path=db_path)
        return cls(products, brands, **kwargs)

    def _brand_tokens_for(self, product: dict) -> frozenset[str]:
        name = self.brand_names.get(int(product["brand_id"] or 0), "")
        return frozenset(name.split())

    def _add_alias(self, product: dict, text: str | None) -> None:
        norm = normalize(text)
        tokens = tuple(norm.split())
        if not tokens:
            return
        if all(t in STOPWORDS for t in tokens):
            return
        alias = _Alias(
            product_id=int(product["id"]),
            text=norm,
            tokens=tokens,
            squashed=_squash(norm),
            numbers=_numbers(norm),
            brand_tokens=self._brand_tokens_for(product),
        )
        if any(a.product_id == alias.product_id and a.text == alias.text for a in self.aliases):
            return
        self.aliases.append(alias)

    def _build_aliases(self) -> None:
        for product in self.products.values():
            name = product.get("product_name") or ""
            brand_tokens = self._brand_tokens_for(product)
            self._add_alias(product, name)
            self._add_alias(product, product.get("normalized_product_name"))

            # Nombre sin la marca: "ASICS Novablast 5" -> "novablast 5".
            norm_tokens = normalize(name).split()
            without_brand = [t for t in norm_tokens if t not in brand_tokens]
            if without_brand and len(without_brand) != len(norm_tokens):
                self._add_alias(product, " ".join(without_brand))

            franchise = normalize(product.get("franchise"))
            model = normalize(product.get("model"))
            version = normalize(product.get("version"))
            for stem in {franchise, model}:
                if not stem:
                    continue
                # Franquicia sola: a propósito. Sirve para detectar AMBIGÜEDAD
                # ("Pegasus" a secas) y para resolver franquicias únicas.
                self._add_alias(product, stem)
                if version:
                    self._add_alias(product, f"{stem} {version}")

    def _maybe_build_tfidf(self) -> None:
        """Prefiltro opcional de candidatos (sólo catálogos grandes)."""
        min_aliases = int(setting("resolve", "tfidf_min_aliases", default=800))
        if len(self.aliases) < min_aliases:
            return
        try:  # pragma: no cover - depende del tamaño del catálogo
            from sklearn.feature_extraction.text import TfidfVectorizer
        except ImportError:  # pragma: no cover
            return
        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        self._matrix = self._vectorizer.fit_transform([a.text for a in self.aliases])

    # -- scoring -------------------------------------------------------------

    def _candidates(self, mention: str) -> list[_Alias]:
        if self._vectorizer is None or self._matrix is None:
            return self.aliases
        import numpy as np  # pragma: no cover - sólo con prefiltro activo
        vector = self._vectorizer.transform([mention])
        scores = (self._matrix @ vector.T).toarray().ravel()
        top = np.argsort(scores)[::-1][:60]
        return [self.aliases[i] for i in top if scores[i] > 0]

    def _alias_score(self, m_tokens: tuple[str, ...], m_squash: str,
                     m_numbers: frozenset[int], m_brands: frozenset[str],
                     alias: _Alias) -> tuple[float, str | None]:
        """Score 0..1 de una mención contra un alias. Segundo valor = motivo de rechazo."""
        # Conflicto de marca: "Adidas Pegasus" no puede ser una Nike.
        if m_brands and alias.brand_tokens and not (m_brands & alias.brand_tokens):
            return 0.0, "brand_conflict"

        # Conflicto de versión: Pegasus 40 NO es Pegasus 41.
        if m_numbers and alias.numbers and not (m_numbers & alias.numbers):
            return 0.0, "version_conflict"

        shared_token = bool(set(m_tokens) & set(alias.tokens))
        contained = m_squash in alias.squashed or alias.squashed in m_squash
        if not shared_token and not contained:
            return 0.0, None

        ratio = SequenceMatcher(None, m_squash, alias.squashed).ratio()
        score = max(ratio, _token_f1(m_tokens, alias.tokens))

        # La mención trae una versión que el alias no tiene ("Pegasus 41" contra
        # el alias genérico "pegasus"): la evidencia es más débil, no alcanza sola.
        if m_numbers and not alias.numbers:
            score *= 0.90
        return score, None

    def score_all(self, mention: str) -> list[tuple[int, float, str]]:
        """``[(product_id, score, alias)]`` ordenado desc. Sin aplicar umbral."""
        norm = normalize(mention)
        if not norm:
            return []
        m_tokens = tuple(norm.split())
        m_squash = _squash(norm)
        m_numbers = _numbers(norm)
        m_brands = frozenset(
            token for phrase, _bid in self.brand_phrases.items()
            for token in phrase.split() if phrase and phrase in norm
        )

        best: dict[int, tuple[float, str]] = {}
        rejections: dict[int, str] = {}
        for alias in self._candidates(norm):
            # Conflicto de versión a nivel PRODUCTO: alcanza con que la mención
            # traiga un número ajeno a todos los alias del producto.
            product_nums = self.product_numbers.get(alias.product_id, frozenset())
            if m_numbers and product_nums and not (m_numbers & product_nums):
                rejections.setdefault(alias.product_id, "version_conflict")
                continue
            score, reason = self._alias_score(m_tokens, m_squash, m_numbers, m_brands, alias)
            if reason and alias.product_id not in best:
                rejections[alias.product_id] = reason
            if score <= 0:
                continue
            current = best.get(alias.product_id)
            if current is None or score > current[0]:
                best[alias.product_id] = (score, alias.text)
        self._last_rejections = rejections
        return sorted(((pid, s, a) for pid, (s, a) in best.items()),
                      key=lambda t: (-t[1], t[0]))

    def resolve(self, mention: str, *, start: int = 0, end: int | None = None) -> Resolution | None:
        """Resuelve un texto corto. ``None`` si no supera el umbral de confianza."""
        ranked = self.score_all(mention)
        if not ranked:
            for reason in getattr(self, "_last_rejections", {}).values():
                self._bump(f"rejected_{reason}")
                break
            else:
                self._bump("rejected_no_candidate")
            return None

        product_id, score, alias = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

        if score < self.accept_threshold:
            self._bump("rejected_low_score")
            log.debug("mención descartada por baja confianza: %r (%.3f < %.3f)",
                      mention, score, self.accept_threshold)
            return None
        if runner_up > 0 and (score - runner_up) < self.ambiguity_margin:
            self._bump("rejected_ambiguous")
            log.debug("mención ambigua descartada: %r (%.3f vs %.3f)",
                      mention, score, runner_up)
            return None

        self._bump("accepted")
        return Resolution(product_id=product_id, score=score, text=mention,
                          matched_alias=alias, start=start,
                          end=end if end is not None else len(mention),
                          runner_up=runner_up)

    def find_products(self, text: str, *, limit: int | None = None) -> list[Resolution]:
        """Escanea un texto y devuelve las menciones resueltas, sin solaparse.

        Recorre n-gramas de mayor a menor: gana el fragmento más largo
        ("nike pegasus 41" antes que "pegasus").
        """
        if not text:
            return []
        folded = fold(text)
        tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(folded)]
        if not tokens:
            return []

        scored: list[tuple[float, int, Resolution, range]] = []
        for n in range(min(self.max_ngram, len(tokens)), 0, -1):
            for i in range(0, len(tokens) - n + 1):
                idxs = range(i, i + n)
                window = tokens[i:i + n]
                # Recortar stopwords en los bordes: "the nova blast 5" -> "nova blast 5".
                while window and window[0][0] in STOPWORDS:
                    window = window[1:]
                while window and window[-1][0] in STOPWORDS:
                    window = window[:-1]
                if not window:
                    continue
                if all(len(tok) < 2 for tok, _s, _e in window):
                    continue
                # Un nombre no cruza un separador ("... 41 vs ASICS ...") ni
                # signos de puntuación: sin esto, los números de un producto se
                # mezclan con el nombre del otro.
                if any(tok in BOUNDARY_TOKENS for tok, _s, _e in window):
                    continue
                if any(not _INNER_GAP_RE.fullmatch(folded[window[k][2]:window[k + 1][1]])
                       for k in range(len(window) - 1)):
                    continue
                # Prefiltro barato: si ningún token del fragmento aparece en el
                # vocabulario del catálogo, no hay nada que resolver.
                if not any(tok in self._vocab for tok, _s, _e in window):
                    continue
                start, end = window[0][1], window[-1][2]
                fragment = text[start:end]
                resolution = self.resolve(fragment, start=start, end=end)
                if resolution is None:
                    continue
                scored.append((resolution.score, len(window), resolution, idxs))

        # Gana el fragmento con MÁS confianza (a igualdad, el más largo) y se
        # consume el span: así "New Balance 1080v13" le gana a variantes con
        # palabras de más pegadas al nombre.
        consumed: set[int] = set()
        found: dict[int, Resolution] = {}
        for _score, _length, resolution, idxs in sorted(
                scored, key=lambda item: (-item[0], -item[1], item[2].start)):
            if any(j in consumed for j in idxs):
                continue
            if resolution.product_id in found:
                consumed.update(idxs)
                continue
            found[resolution.product_id] = resolution
            consumed.update(idxs)

        ordered = sorted(found.values(), key=lambda r: r.start)
        return ordered[:limit] if limit else ordered

    # -- utilidades ----------------------------------------------------------

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    def reset_stats(self) -> None:
        self.stats = {}

    def brand_of(self, product_id: int) -> int | None:
        product = self.products.get(int(product_id))
        return int(product["brand_id"]) if product and product.get("brand_id") is not None else None

    def country_of(self, product_id: int) -> str | None:
        product = self.products.get(int(product_id))
        return product.get("country_code") if product else None

    def find_brands(self, text: str) -> set[int]:
        """Marcas nombradas explícitamente en un texto (por nombre de marca)."""
        norm = f" {normalize(text)} "
        return {bid for name, bid in self.brand_phrases.items()
                if name and f" {name} " in norm}


_DEFAULT: dict[str, CatalogResolver] = {}


def default_resolver(db_path: Path | str = DB_PATH, *, refresh: bool = False) -> CatalogResolver:
    """Resolver cacheado por base (evita releer el catálogo en cada colector)."""
    key = str(db_path)
    if refresh or key not in _DEFAULT:
        _DEFAULT[key] = CatalogResolver.from_db(db_path)
    return _DEFAULT[key]


def reset_default_resolver() -> None:
    _DEFAULT.clear()
