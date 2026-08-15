"""Sentimiento determinístico en español rioplatense (sin LLMs).

Se usa para poblar ``social_mention_aggregates.sentiment_score`` (-1..1), que
es lo que después promedia ``services/brand_intelligence.py`` ponderado por
volumen.

Criterios:

  * **Mismo vocabulario** que el léxico de tópicos de
    ``brand_intelligence.TAXONOMY_LEXICON`` ("caro", "cómodas", "no vale lo que
    cuesta", "sin stock", "me arrepentí"…), para que un texto clasificado como
    ``price_perception/expensive`` no salga con sentimiento positivo.
  * **Negación**: "no son cómodas" invierte el signo del término si el negador
    aparece en las 3 palabras previas.
  * **Intensificadores rioplatenses**: "re", "recontra", "súper", "zarpado de"
    amplifican; "medio", "un poco" atenúan.
  * Sin señal léxica -> ``None`` (el agregado queda sin sentimiento en vez de
    inventar un 0 neutro que diluiría el promedio).
"""

from __future__ import annotations

import re
from typing import Iterable

from app.collectors.resolve import fold

# ── léxico ──────────────────────────────────────────────────

POSITIVE_TERMS: dict[str, float] = {
    # comodidad / calce
    "comoda": 1.0, "comodas": 1.0, "comodo": 1.0, "comodos": 1.0, "comodisimas": 1.3,
    "comodisimo": 1.3, "comodidad": 0.8, "como una nube": 1.2, "mullida": 0.7,
    "calzan barbaro": 1.1, "calzan perfecto": 1.1, "horma perfecta": 1.0,
    # performance
    "livianas": 0.8, "liviana": 0.8, "rapidas": 0.8, "vuelan": 1.0, "me volaron": 1.1,
    "rinden": 0.9, "rinden un monton": 1.2, "responden": 0.8, "amortiguacion excelente": 1.2,
    "aguantan": 0.8, "duran un monton": 1.2, "duraderas": 0.9, "resistentes": 0.8,
    # valoración general
    "excelente": 1.2, "excelentes": 1.2, "buenisimas": 1.2, "buenisimo": 1.2,
    "una masa": 1.2, "un golazo": 1.2, "de diez": 1.1, "espectaculares": 1.2,
    "impecables": 1.0, "zarpadas": 1.1, "las mejores": 1.1, "me encantan": 1.2,
    "me encantaron": 1.2, "las amo": 1.2, "recomiendo": 1.0, "las recomiendo": 1.1,
    "super recomendables": 1.2, "recomendadisimas": 1.2, "cumplen": 0.6,
    "valen la pena": 1.1, "vale la pena": 1.1, "vale lo que cuesta": 1.1,
    "vale cada peso": 1.2, "buena inversion": 0.9, "relacion precio calidad": 0.8,
    "precio justo": 0.9, "precio razonable": 0.9, "bien de precio": 0.9,
    "buena calidad": 1.0, "calidad impecable": 1.2, "las volveria a comprar": 1.2,
    "segundo par": 0.7, "nunca fallan": 1.1,
    # estética
    "lindas": 0.8, "lindisimas": 1.0, "hermosas": 1.1, "canchera": 0.8, "cancheras": 0.8,
    "quedan re bien": 1.0, "combinan con todo": 0.8, "estilosas": 0.8,
    # disponibilidad favorable
    "las consegui": 0.6, "habia stock": 0.6, "buen descuento": 0.8, "buena oferta": 0.8,
}

NEGATIVE_TERMS: dict[str, float] = {
    # precio
    "caras": 1.0, "cara": 0.9, "caro": 0.9, "carisimas": 1.3, "carisimo": 1.3,
    "un fangote": 1.2, "impagable": 1.3, "impagables": 1.3, "una fortuna": 1.2,
    "precio excesivo": 1.1, "precio alto": 0.8, "sobrevaloradas": 1.2,
    "sobrevalorado": 1.2, "no vale lo que cuesta": 1.3, "no justifica el precio": 1.2,
    "carisimas para lo que son": 1.3, "plata tirada": 1.4, "un robo": 1.2,
    # calidad / durabilidad
    "mala calidad": 1.3, "se rompio": 1.2, "se rompieron": 1.2, "se despego": 1.2,
    "se despegaron": 1.2, "se gastaron": 0.9, "se desarmaron": 1.2, "duraron nada": 1.3,
    "no duran": 1.1, "flojas": 0.8, "decepcion": 1.2, "decepcionantes": 1.2,
    "una porqueria": 1.4, "un desastre": 1.3, "malisimas": 1.3,
    # calce / comodidad
    "incomodas": 1.2, "incomodo": 1.2, "me lastiman": 1.2, "ampollas": 1.1,
    "angostas": 0.8, "aprietan": 0.9, "me quedan justas": 0.7, "vienen chicas": 0.7,
    "pesadas": 0.8, "resbalan": 1.0, "duras": 0.7,
    # disponibilidad
    "sin stock": 1.0, "no hay stock": 1.1, "agotadas": 0.9, "agotado": 0.9,
    "no se consigue": 1.1, "no las consigo": 1.1, "imposible de conseguir": 1.2,
    "no hay talles": 1.1, "no habia talles": 1.1, "faltan talles": 1.0,
    "no tenian mi talle": 1.0, "no reponen": 1.0, "poca variedad": 0.8,
    # arrepentimiento
    "me arrepenti": 1.3, "mala compra": 1.3, "no las compraria de nuevo": 1.3,
    "me cambio a": 0.8, "me pase a": 0.8, "dejo la marca": 1.0,
}

#: Negadores: invierten el término si aparecen justo antes.
NEGATORS = ("no", "nunca", "jamas", "ni", "tampoco", "nada")
NEGATION_WINDOW = 3

INTENSIFIERS: dict[str, float] = {
    "re": 1.30, "recontra": 1.45, "muy": 1.25, "super": 1.30, "hiper": 1.35,
    "demasiado": 1.25, "increiblemente": 1.35, "zarpado": 1.35, "full": 1.15,
}
DIMINISHERS: dict[str, float] = {
    "medio": 0.70, "algo": 0.75, "un poco": 0.70, "bastante": 0.90, "masomenos": 0.60,
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _phrase_pattern(term: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


_COMPILED: tuple[tuple[re.Pattern[str], float, str], ...] = tuple(
    [(_phrase_pattern(term), weight, "pos") for term, weight in POSITIVE_TERMS.items()]
    + [(_phrase_pattern(term), weight, "neg") for term, weight in NEGATIVE_TERMS.items()]
)


def _context_multiplier(prefix: str) -> tuple[float, bool]:
    """Intensificadores/atenuadores y negación en las palabras previas."""
    words = _WORD_RE.findall(prefix)[-NEGATION_WINDOW:]
    multiplier = 1.0
    negated = False
    joined = " ".join(words)
    for phrase, factor in DIMINISHERS.items():
        if phrase in joined:
            multiplier *= factor
    for word in words:
        if word in INTENSIFIERS:
            multiplier *= INTENSIFIERS[word]
        if word in NEGATORS:
            negated = True
    return multiplier, negated


def score_text(text: str | None) -> float | None:
    """Sentimiento de un texto público en -1..1. ``None`` si no hay señal léxica."""
    if not text:
        return None
    folded = fold(str(text))
    positive = 0.0
    negative = 0.0
    for pattern, weight, polarity in _COMPILED:
        for match in pattern.finditer(folded):
            multiplier, negated = _context_multiplier(folded[max(0, match.start() - 40):match.start()])
            value = weight * multiplier
            is_positive = (polarity == "pos") != negated
            if is_positive:
                positive += value
            else:
                negative += value
    total = positive + negative
    if total <= 0:
        return None
    return max(-1.0, min(1.0, (positive - negative) / total))


def aggregate_sentiment(texts: Iterable[str | None]) -> float | None:
    """Promedio del sentimiento de los textos que sí tienen señal."""
    scores = [s for s in (score_text(t) for t in texts) if s is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)
