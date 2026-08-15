"""Catalogación y enriquecimiento determinístico del catálogo.

Toma lo que trae el scraper (nombre, taxonomía, precio, descripción) y deriva:

  * ``normalized_product_name`` — clave de comparación entre marcas.
  * ``use_case`` — uno de la taxonomía cerrada del negocio.
  * ``price_band`` — según ``enrichment.price_bands[country]`` de weights.yaml.
  * ``lifecycle_stage`` — días desde el launch + presión de descuento.
  * atributos esparsos (``product_attributes``) físicos, visuales,
    de performance y ratings derivados 0..1.

Reglas del proyecto que se respetan acá:
  * Cero LLMs cloud, cero red: todo es reglas + léxicos locales.
  * Cero pesos hardcodeados: bandas y ciclo de vida salen de weights.yaml.
  * Sólo se emite un atributo si hay **evidencia** en el texto/taxonomía.
    Lo que no se puede afirmar, simplemente no se emite (el modelo EAV
    acepta atributos ausentes).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import DB_PATH, section
from app.db import get_conn
from app.services.common import clamp, parse_date

# ============================================================
# Léxicos (no son pesos de scoring: son vocabulario del dominio)
# ============================================================

# Confianzas heurísticas del inferidor de use_case.
_UC_BASE_CONFIDENCE = 0.55
_UC_STEP = 0.12
_UC_MIN_CONFIDENCE = 0.35
_UC_MAX_CONFIDENCE = 0.92
_UC_SCRAPER_CONFIDENCE = 1.0

# Bonus de confianza por evidencia corroborante en atributos.
_ATTR_HIT_BONUS = 0.04
_ATTR_MAX_CONFIDENCE = 0.95

_SOURCE = "rules"

# ── colores (canónico -> sinónimos ES/EN y colorways Nike) ───
_COLOR_LEXICON: dict[str, tuple[str, ...]] = {
    "black":      ("black", "negro", "negra", "noir"),
    "white":      ("white", "blanco", "blanca", "summit white", "sail"),
    "grey":       ("grey", "gray", "gris", "smoke grey", "wolf grey"),
    "red":        ("red", "rojo", "roja", "crimson", "carmesi"),
    "blue":       ("blue", "azul", "navy", "marino", "royal"),
    "green":      ("green", "verde", "olive", "oliva", "mint"),
    "yellow":     ("yellow", "amarillo", "amarilla", "volt", "lima", "lime"),
    "orange":     ("orange", "naranja", "coral"),
    "pink":       ("pink", "rosa", "rosado", "fucsia", "fuchsia"),
    "purple":     ("purple", "violeta", "morado", "lila"),
    "brown":      ("brown", "marron", "cafe", "khaki", "caqui"),
    "beige":      ("beige", "crema", "cream", "hueso"),
    "silver":     ("silver", "plata", "plateado", "metallic silver"),
    "gold":       ("gold", "dorado", "oro"),
    "turquoise":  ("turquesa", "turquoise", "teal", "aqua"),
    "burgundy":   ("burgundy", "bordo", "vino"),
    "multicolor": ("multicolor", "multicolour"),
}

# Tokens que se eliminan al normalizar el nombre.
_COLOR_TOKENS: set[str] = {
    token
    for synonyms in _COLOR_LEXICON.values()
    for synonym in synonyms
    for token in synonym.split()
}
_GENDER_TOKENS = {
    "men", "mens", "man", "male", "hombre", "hombres", "caballero",
    "women", "womens", "woman", "female", "mujer", "mujeres", "dama", "damas",
    "unisex", "kids", "kid", "nino", "ninos", "nina", "ninas", "junior", "juniors",
    "boys", "boy", "girls", "girl", "youth", "gs", "ps", "td", "bg",
}
_SIZE_TOKENS = {"xs", "xl", "xxl", "xxxl", "talle", "talles", "size", "sizes"}
_SIZE_PREFIXES = {"talle", "talles", "size", "sizes", "us", "uk", "eu", "eur", "br", "cm"}
_NOISE_TOKENS = {
    "shoe", "shoes", "sneaker", "sneakers", "zapatilla", "zapatillas",
    "calzado", "botin", "botines", "de", "del", "para", "the", "and", "y",
}

# ── use case: (valor canónico, evidencia fuerte, evidencia débil) ──
# El orden define el desempate: lo más específico va primero.
_USE_CASE_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("race day", (
        "race day", "racing", "carrera", "competicion", "maraton", "marathon",
        "vaporfly", "alphafly", "streakfly", "adizero", "carbon plate",
        "placa de carbono", "fibra de carbono", "media maraton",
    ), ("speed", "velocidad", "personal best", "tempo", "fast")),
    ("trail running", (
        "trail", "trail running", "sendero", "senderos", "montana", "off road",
        "wildhorse", "kiger", "terrex", "speedgoat", "senderismo", "terreno tecnico",
    ), ("barro", "rocas", "grip", "todo terreno")),
    ("skateboarding", (
        "skate", "skateboarding", "skateboard", "janoski", "nike sb", "sb",
        "patineta", "patinar",
    ), ("street", "vulcanizada")),
    ("basketball", (
        "basketball", "basquet", "basquetbol", "baloncesto", "jordan", "lebron",
        "kyrie", "kd", "gt cut", "gt jump", "basket",
    ), ("hoops", "cancha", "aro")),
    ("football", (
        "football", "futbol", "soccer", "mercurial", "phantom", "botines",
        "fg", "ag", "sg", "tf", "cesped", "tacos", "cancha de futbol",
    ), ("tiempo", "campo", "boots")),
    ("tennis", (
        "tennis", "tenis", "padel", "court shoe", "vapor pro", "gp challenge",
        "zoom vapor", "polvo de ladrillo",
    ), ("court", "cancha dura")),
    ("gym/training", (
        "training", "entrenamiento", "gym", "gimnasio", "crossfit", "metcon",
        "weightlifting", "levantamiento", "fitness", "hiit", "romaleos",
        "cross training", "sala de pesas",
    ), ("box", "circuito", "funcional")),
    ("walking", (
        "walking", "caminar", "caminata", "caminatas", "motiva", "walk",
    ), ("paseo", "andar")),
    ("daily running", (
        "daily running", "running", "correr", "pegasus", "vomero", "invincible",
        "jogging", "rodajes", "entrenamiento diario", "zoom fly", "kilometros diarios",
        "daily trainer", "asfalto",
    ), ("structure", "millas", "neutral", "ritmo")),
    ("lifestyle", (
        "lifestyle", "estilo de vida", "air force 1", "air max", "dunk", "blazer",
        "cortez", "sportswear", "streetwear", "moda", "iconico", "icono",
    ), ("urbano", "street style", "retro")),
    ("casual", (
        "casual", "uso casual", "everyday", "dia a dia", "informal", "todos los dias",
    ), ("comodo", "relajado")),
)

# Sinónimos del scraper -> valor canónico.
_USE_CASE_ALIASES: dict[str, str] = {
    "running": "daily running",
    "road running": "daily running",
    "daily trainer": "daily running",
    "daily run": "daily running",
    "race": "race day",
    "racing": "race day",
    "raceday": "race day",
    "trail": "trail running",
    "training": "gym/training",
    "gym": "gym/training",
    "gym training": "gym/training",
    "train": "gym/training",
    "fitness": "gym/training",
    "soccer": "football",
    "futbol": "football",
    "basket": "basketball",
    "basquet": "basketball",
    "baloncesto": "basketball",
    "skate": "skateboarding",
    "skateboard": "skateboarding",
    "tenis": "tennis",
    "walk": "walking",
    "lifestyle": "lifestyle",
    "casual": "casual",
}

# ── atributos categóricos: attr_name -> (grupo, [(valor, keywords, conf)]) ──
_CATEGORICAL_RULES: dict[str, tuple[str, tuple[tuple[str, tuple[str, ...], float], ...]]] = {
    "silhouette": ("visual", (
        ("slide",   ("slide", "sandalia", "ojota", "chancleta"), 0.85),
        ("boot",    ("boot", "bota", "botita", "botin"), 0.80),
        ("skate",   ("skate", "skateboarding", "janoski"), 0.80),
        ("chunky",  ("chunky", "dad shoe", "voluminosa", "suela voluminosa"), 0.75),
        ("court",   ("court", "cancha", "tennis", "basketball", "baloncesto"), 0.70),
        ("trainer", ("training shoe", "trainer", "zapatilla de entrenamiento", "metcon"), 0.75),
        ("runner",  ("running shoe", "runner", "zapatilla de running", "para correr"), 0.75),
    )),
    "height": ("physical", (
        ("high", ("high top", "hi top", "caña alta", "cana alta", "bota", "tobillera"), 0.80),
        ("mid",  ("mid top", "mid cut", "caña media", "cana media", "media cana"), 0.80),
        ("low",  ("low top", "low cut", "caña baja", "cana baja", "perfil bajo"), 0.80),
    )),
    "upper_material": ("physical", (
        ("flyknit",         ("flyknit",), 0.88),
        ("engineered mesh", ("engineered mesh", "malla ingenierizada"), 0.85),
        ("knit",            ("knit", "tejido", "tricot"), 0.75),
        ("mesh",            ("mesh", "malla"), 0.80),
        ("suede",           ("suede", "gamuza", "ante"), 0.82),
        ("leather",         ("leather", "cuero", "piel"), 0.82),
        ("canvas",          ("canvas", "lona"), 0.80),
        ("ripstop",         ("ripstop",), 0.80),
        ("synthetic",       ("synthetic", "sintetico", "sintetica", "sinteticos"), 0.72),
        ("textile",         ("textile", "textil"), 0.70),
    )),
    "sole_type": ("physical", (
        ("studded",    ("studs", "tapones", "tacos", "cleats", "fg", "ag", "sg"), 0.82),
        ("waffle",     ("waffle",), 0.85),
        ("gum",        ("gum sole", "suela gum", "suela de goma gum"), 0.80),
        ("cupsole",    ("cupsole", "suela copa"), 0.78),
        ("vulcanized", ("vulcanized", "vulcanizada"), 0.80),
        ("rubber",     ("rubber", "caucho", "suela de goma", "goma"), 0.72),
    )),
    "cushioning_type": ("performance", (
        ("zoomx",      ("zoomx", "zoom x"), 0.90),
        ("air max",    ("air max", "max air", "unidad air max"), 0.88),
        ("zoom air",   ("zoom air", "unidad zoom", "air zoom"), 0.88),
        ("react",      ("react", "reactx"), 0.85),
        ("boost",      ("boost",), 0.85),
        ("dna loft",   ("dna loft",), 0.85),
        ("fresh foam", ("fresh foam",), 0.85),
        ("fuelcell",   ("fuelcell", "fuel cell"), 0.85),
        ("floatride",  ("floatride",), 0.85),
        ("lightstrike", ("lightstrike",), 0.85),
        ("eva",        ("eva", "espuma eva"), 0.70),
        ("phylon",     ("phylon", "cushlon"), 0.75),
    )),
    "visible_technology": ("performance", (
        ("carbon plate",     ("carbon plate", "placa de carbono", "placa de fibra de carbono", "flyplate"), 0.88),
        ("visible air unit", ("visible air", "unidad de aire visible", "air unit", "burbuja de aire",
                              "max air", "air max"), 0.82),
        ("flywire",          ("flywire",), 0.85),
        ("flyknit upper",    ("flyknit",), 0.75),
        ("torsion system",   ("torsion system", "shank", "placa de torsion"), 0.75),
    )),
    "design_style": ("visual", (
        ("retro",      ("retro", "vintage", "heritage", "anos 80", "anos 90", "old school"), 0.80),
        ("chunky",     ("chunky", "dad shoe", "voluminosa"), 0.78),
        ("futuristic", ("futurista", "futuristic", "vanguardia"), 0.78),
        ("minimal",    ("minimal", "minimalista", "lineas limpias", "clean"), 0.75),
        ("technical",  ("technical", "tecnica", "tecnologia avanzada", "performance puro"), 0.72),
        ("streetwear", ("streetwear", "urbano", "street style"), 0.75),
        ("classic",    ("classic", "clasico", "clasica", "atemporal", "iconico", "icono"), 0.72),
    )),
}

# Familia de material a partir del material del upper.
_MATERIAL_FAMILY = {
    "flyknit": "textile", "engineered mesh": "textile", "knit": "textile",
    "mesh": "textile", "canvas": "textile", "ripstop": "textile", "textile": "textile",
    "suede": "leather", "leather": "leather",
    "synthetic": "synthetic",
}

# ── ratings derivados 0..1: [(valor, confianza, keywords)] ──
# Nota: ``weight`` se interpreta como LIGEREZA (1.0 = muy liviana).
_RATING_RULES: dict[str, tuple[tuple[float, float, tuple[str, ...]], ...]] = {
    "cushioning": (
        (0.92, 0.80, ("maxima amortiguacion", "max cushioning", "maximum cushioning",
                      "zoomx", "invincible", "ultra amortiguacion", "plush", "muy amortiguada")),
        (0.72, 0.70, ("amortiguacion", "cushioning", "cushioned", "react", "zoom air",
                      "air max", "espuma", "foam", "acolchado", "suave")),
        (0.30, 0.65, ("minimal cushioning", "minimalista", "perfil bajo", "low profile",
                      "firme", "firm ride", "sensacion de suelo")),
    ),
    "stability": (
        (0.88, 0.78, ("estabilidad", "stability", "soporte del arco", "pronacion",
                      "contrafuerte", "estabilizador", "estable", "structure")),
        (0.35, 0.62, ("neutral", "pisada neutra", "neutral ride")),
    ),
    "responsiveness": (
        (0.90, 0.80, ("respuesta explosiva", "responsive", "reactiva", "placa de carbono",
                      "carbon plate", "zoomx", "retorno de energia", "energy return",
                      "propulsion", "snappy")),
        (0.70, 0.70, ("zoom air", "react", "dinamica", "rebote", "bounce")),
        (0.35, 0.62, ("plush ride", "relajada", "suave y lenta")),
    ),
    "weight": (
        (0.92, 0.80, ("ultraliviana", "ultralight", "ultra ligera", "muy liviana",
                      "peso pluma", "superligera")),
        (0.75, 0.70, ("liviana", "lightweight", "ligera", "ligero", "liviano")),
        (0.30, 0.65, ("robusta", "pesada", "heavy", "estructura reforzada")),
    ),
    "durability": (
        (0.88, 0.78, ("durabilidad", "duradera", "durable", "resistente al desgaste",
                      "alta resistencia", "refuerzos", "kilometros de vida")),
        (0.35, 0.62, ("poco duradera", "desgaste rapido", "uso ocasional")),
    ),
    "comfort": (
        (0.90, 0.78, ("maxima comodidad", "supercomodo", "muy comoda", "all day comfort",
                      "comodidad todo el dia", "plush")),
        (0.72, 0.70, ("comodidad", "comoda", "comodo", "comfort", "confort",
                      "acolchado", "suave al pie")),
    ),
    "breathability": (
        (0.88, 0.78, ("muy transpirable", "altamente transpirable", "breathable",
                      "ventilacion", "transpirabilidad", "malla transpirable",
                      "engineered mesh")),
        (0.70, 0.70, ("transpirable", "mesh", "malla", "ventilada")),
        (0.30, 0.62, ("impermeable", "gore tex", "goretex", "waterproof", "sellada")),
    ),
    "traction": (
        (0.90, 0.78, ("traccion", "agarre", "grip", "tapones", "tacos", "multidireccional",
                      "adherencia", "trail", "sendero")),
        (0.70, 0.70, ("rubber outsole", "suela de goma", "caucho duradero", "waffle")),
        (0.35, 0.62, ("suela lisa", "poco agarre")),
    ),
    "support": (
        (0.88, 0.78, ("soporte", "support", "sujecion", "lockdown", "contrafuerte",
                      "sujecion del tobillo", "estabilizador")),
        (0.35, 0.62, ("libertad de movimiento", "sin restricciones", "flexible y libre")),
    ),
    "versatility": (
        (0.88, 0.75, ("versatil", "versatile", "multiproposito", "cross training",
                      "del gimnasio a la calle", "todo terreno", "dia a dia", "para todo")),
        (0.35, 0.62, ("especializada", "uso especifico", "solo para competencia")),
    ),
}

_RATING_GROUP = "derived"

# Campos del producto que alimentan el texto de evidencia.
_HAYSTACK_FIELDS = (
    "product_name", "franchise", "model", "version", "category", "subcategory",
    "sport", "activity", "performance_vs_lifestyle", "consumer_segment", "description",
)


# ============================================================
# Normalización de texto
# ============================================================

def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


@lru_cache(maxsize=4096)
def _norm(value: str) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios colapsados."""
    text = _strip_accents(str(value)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _haystack(product: dict) -> str:
    """Texto normalizado (con padding) donde se busca evidencia."""
    parts = [str(product.get(field) or "") for field in _HAYSTACK_FIELDS]
    return f" {_norm(' '.join(parts))} "


def _hits(haystack: str, keywords: tuple[str, ...]) -> int:
    """Cantidad de keywords presentes, respetando límites de palabra."""
    return sum(1 for kw in keywords if f" {_norm(kw)} " in haystack)


# ============================================================
# A) Normalización de nombre
# ============================================================

def normalize_name(name: str) -> str:
    """``'Nike Air Zoom Pegasus 41 Men's Black/White (Talle 42)'`` -> ``'nike air zoom pegasus 41'``.

    Minúsculas, sin acentos ni puntuación, sin tokens de color/talle ni
    sufijos de género. Determinística e idempotente.
    """
    if not name:
        return ""
    text = re.sub(r"\(.*?\)", " ", str(name))          # colorways entre paréntesis
    text = re.sub(r"['’´`]s\b", "", text, flags=re.IGNORECASE)   # posesivo: Men's
    tokens = _norm(text).split()

    kept: list[str] = []
    skipping_size = False
    for token in tokens:
        if skipping_size:
            if token.isdigit():          # 'talle 9 5' (venía de 9.5)
                continue
            skipping_size = False
        if token in _SIZE_PREFIXES:
            skipping_size = True
            continue
        if token in _COLOR_TOKENS or token in _GENDER_TOKENS:
            continue
        if token in _SIZE_TOKENS or token in _NOISE_TOKENS:
            continue
        if len(token) == 1 and token.isalpha():        # restos de puntuación
            continue
        kept.append(token)

    return " ".join(kept)


# ============================================================
# B) Use case
# ============================================================

def _canonical_use_case(value: Any) -> str | None:
    text = _norm(value or "")
    if not text:
        return None
    known = {uc for uc, _, _ in _USE_CASE_RULES}
    for use_case in known:
        if _norm(use_case) == text:
            return use_case
    return _USE_CASE_ALIASES.get(text)


def infer_use_case(product: dict) -> tuple[str | None, float]:
    """Devuelve ``(use_case, confianza)``.

    Si el scraper ya trajo un ``use_case`` reconocible, se respeta con
    confianza 1.0. Si no hay evidencia, devuelve ``(None, 0.0)``.
    """
    existing = _canonical_use_case(product.get("use_case"))
    if existing:
        return existing, _UC_SCRAPER_CONFIDENCE

    haystack = _haystack(product)
    best: tuple[str, float] | None = None
    for use_case, strong, weak in _USE_CASE_RULES:
        score = _hits(haystack, strong) * 1.0 + _hits(haystack, weak) * 0.5
        if score > 0 and (best is None or score > best[1]):
            best = (use_case, score)

    if best is None:
        return None, 0.0

    confidence = clamp(
        _UC_BASE_CONFIDENCE + _UC_STEP * (best[1] - 1.0),
        _UC_MIN_CONFIDENCE,
        _UC_MAX_CONFIDENCE,
    )
    return best[0], round(confidence, 3)


# ============================================================
# C) Banda de precio
# ============================================================

def infer_price_band(price: float | None, country: str) -> str | None:
    """Banda según ``enrichment.price_bands[country]``. Nunca hardcodeada."""
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None

    bands = section("enrichment", "price_bands", str(country or "").upper(), default=None)
    if not isinstance(bands, dict) or not bands:
        return None

    ordered = sorted(
        ((name, float(rng[0]), float(rng[1]))
         for name, rng in bands.items()
         if isinstance(rng, (list, tuple)) and len(rng) >= 2),
        key=lambda item: item[1],
    )
    if not ordered:
        return None

    for name, low, high in ordered:
        if low <= value < high:
            return name
    if value < ordered[0][1]:
        return ordered[0][0]
    return ordered[-1][0]          # por encima del techo => banda más alta


# ============================================================
# D) Ciclo de vida
# ============================================================

def infer_lifecycle_stage(product: dict, avg_discount_pct: float | None) -> str | None:
    """Etapa según días desde ``launch_date`` y presión de descuento.

    Un descuento promedio sostenido >= ``clearance_discount_pct`` fuerza
    ``clearance``. Sin ``launch_date`` se infiere sólo por descuento; si
    tampoco hay descuento devuelve ``None`` (no se inventa etapa).
    """
    cfg = section("enrichment", "lifecycle", default={}) or {}
    clearance_pct = float(cfg.get("clearance_discount_pct", 40.0))

    discount: float | None = None
    if avg_discount_pct is not None:
        try:
            discount = float(avg_discount_pct)
        except (TypeError, ValueError):
            discount = None

    if discount is not None and discount >= clearance_pct:
        return "clearance"

    launch = parse_date(product.get("launch_date"))
    if launch is None:
        if discount is None:
            return None
        # Descuento sostenido pero por debajo del umbral de liquidación.
        return "decline" if discount >= clearance_pct / 2.0 else None

    days = (date.today() - launch).days
    if days < 0:
        return "launch"                                    # lanzamiento futuro
    if days <= float(cfg.get("launch_max_days", 90)):
        return "launch"
    if days <= float(cfg.get("growth_max_days", 270)):
        return "growth"
    if days <= float(cfg.get("mature_max_days", 540)):
        return "mature"
    if days <= float(cfg.get("decline_max_days", 900)):
        return "decline"
    return "clearance"


# ============================================================
# E) Atributos
# ============================================================

def _attribute(group: str, name: str, *, text: str | None = None,
               num: float | None = None, confidence: float = 0.7) -> dict[str, Any]:
    return {
        "attr_group": group,
        "attr_name": name,
        "value_text": text,
        "value_num": num,
        "confidence": round(float(clamp(confidence)), 3),
        "source": _SOURCE,
    }


def _detect_colors(product: dict) -> list[tuple[str, int]]:
    """Colores detectados con su posición en el texto (para elegir el dominante)."""
    name = f" {_norm(product.get('product_name') or '')} "
    description = f" {_norm(product.get('description') or '')} "
    colorway = f" {_norm(product.get('colorway') or '')} "

    found: dict[str, int] = {}
    for canonical, synonyms in _COLOR_LEXICON.items():
        best: int | None = None
        for synonym in synonyms:
            needle = f" {_norm(synonym)} "
            # El colorway y el nombre mandan sobre la descripción.
            for haystack, offset in ((colorway, 0), (name, 1000), (description, 100000)):
                position = haystack.find(needle)
                if position >= 0:
                    candidate = position + offset
                    if best is None or candidate < best:
                        best = candidate
        if best is not None:
            found[canonical] = best
    return sorted(found.items(), key=lambda item: item[1])


def _categorical_attributes(haystack: str) -> list[dict[str, Any]]:
    attributes: list[dict[str, Any]] = []
    upper_material: str | None = None

    for attr_name, (group, rules) in _CATEGORICAL_RULES.items():
        best: tuple[str, float, int] | None = None
        for value, keywords, confidence in rules:
            hits = _hits(haystack, keywords)
            if hits and (best is None or hits > best[2]):
                best = (value, confidence, hits)
        if best is None:
            continue
        value, confidence, hits = best
        confidence = min(_ATTR_MAX_CONFIDENCE, confidence + _ATTR_HIT_BONUS * (hits - 1))
        attributes.append(_attribute(group, attr_name, text=value, confidence=confidence))
        if attr_name == "upper_material":
            upper_material = value

    # ``material`` es la familia del material del upper: se deriva, no se inventa.
    if upper_material and upper_material in _MATERIAL_FAMILY:
        source_conf = next(a["confidence"] for a in attributes if a["attr_name"] == "upper_material")
        attributes.append(_attribute("physical", "material",
                                     text=_MATERIAL_FAMILY[upper_material],
                                     confidence=source_conf))
    return attributes


def _rating_attributes(haystack: str) -> list[dict[str, Any]]:
    attributes: list[dict[str, Any]] = []
    for rating, tiers in _RATING_RULES.items():
        best: tuple[float, float, int] | None = None
        for value, confidence, keywords in tiers:
            hits = _hits(haystack, keywords)
            if hits and (best is None or hits > best[2]):
                best = (value, confidence, hits)
        if best is None:
            continue
        value, confidence, hits = best
        confidence = min(_ATTR_MAX_CONFIDENCE, confidence + _ATTR_HIT_BONUS * (hits - 1))
        attributes.append(_attribute(_RATING_GROUP, rating, num=round(value, 3),
                                     confidence=confidence))
    return attributes


def _height_from_silhouette(attributes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Si la silueta es inequívoca en altura y no hubo evidencia directa, derivarla."""
    by_name = {a["attr_name"]: a for a in attributes}
    if "height" in by_name or "silhouette" not in by_name:
        return None
    mapping = {"boot": "high", "runner": "low", "slide": "low", "skate": "low"}
    silhouette = by_name["silhouette"]["value_text"]
    if silhouette not in mapping:
        return None
    return _attribute("physical", "height", text=mapping[silhouette],
                      confidence=float(by_name["silhouette"]["confidence"]) * 0.8)


# ============================================================
# F) Enriquecimiento de un producto
# ============================================================

def enrichment_version() -> str:
    return str(section("enrichment", "version", default="1.0.0"))


def enrich_product(product: dict, context: dict | None = None) -> dict:
    """Devuelve ``{"fields": {...}, "attributes": [...]}``.

    ``context`` opcional: ``{"avg_discount_pct": float|None,
    "country_code": str}``. Sólo se emiten atributos con evidencia real en el
    nombre / descripción / taxonomía del producto.
    """
    ctx = context or {}
    product = dict(product or {})

    country = ctx.get("country_code") or product.get("country_code") or ""
    price = product.get("msrp") if product.get("msrp") is not None else ctx.get("price")
    avg_discount = ctx.get("avg_discount_pct")

    use_case, _use_case_confidence = infer_use_case(product)

    fields = {
        "normalized_product_name": normalize_name(product.get("product_name") or ""),
        "use_case": use_case,
        "price_band": infer_price_band(price, country),
        "lifecycle_stage": infer_lifecycle_stage(product, avg_discount),
        "enrichment_version": enrichment_version(),
    }

    haystack = _haystack(product)
    attributes: list[dict[str, Any]] = []

    # Colores (visual)
    colors = _detect_colors(product)
    if colors:
        attributes.append(_attribute("visual", "dominant_color", text=colors[0][0],
                                     confidence=0.8 if len(colors) == 1 else 0.72))
        if len(colors) > 1:
            attributes.append(_attribute("visual", "secondary_colors",
                                         text=",".join(name for name, _ in colors[1:]),
                                         confidence=0.7))

    attributes.extend(_categorical_attributes(haystack))
    derived_height = _height_from_silhouette(attributes)
    if derived_height is not None:
        attributes.append(derived_height)
    attributes.extend(_rating_attributes(haystack))

    return {"fields": fields, "attributes": attributes}


# ============================================================
# G) Corrida completa sobre la base
# ============================================================

_UPDATE_SQL = """
UPDATE products
   SET normalized_product_name = ?,
       use_case                = ?,
       price_band              = ?,
       lifecycle_stage         = ?,
       enrichment_version      = ?,
       updated_at              = datetime('now')
 WHERE id = ?
"""

# Sólo pisamos atributos generados por reglas/modelos: lo del scraper o lo
# cargado a mano manda.
_UPSERT_ATTR_SQL = """
INSERT INTO product_attributes
       (product_id, attr_group, attr_name, value_text, value_num, confidence, source)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (product_id, attr_name) DO UPDATE SET
       attr_group = excluded.attr_group,
       value_text = excluded.value_text,
       value_num  = excluded.value_num,
       confidence = excluded.confidence,
       source     = excluded.source
 WHERE product_attributes.source IS NULL
    OR product_attributes.source IN ('rules', 'model')
"""

_DISCOUNT_SQL = """
SELECT product_id,
       AVG(COALESCE(discount_pct,
                    CASE WHEN full_price > 0 AND current_price IS NOT NULL
                         THEN (full_price - current_price) / full_price * 100.0
                    END)) AS avg_discount_pct
  FROM price_observations
 GROUP BY product_id
"""


def run_enrichment(db_path: Path | str = DB_PATH) -> dict[str, int]:
    """Enriquece todos los productos y persiste columnas + atributos.

    Devuelve conteos: ``products``, ``updated``, ``attributes``,
    ``use_case_inferred``, ``price_band_set``, ``lifecycle_set``.
    """
    counts = {
        "products": 0,
        "updated": 0,
        "attributes": 0,
        "use_case_inferred": 0,
        "price_band_set": 0,
        "lifecycle_set": 0,
    }

    with get_conn(db_path) as conn:
        discounts: dict[int, float | None] = {
            row["product_id"]: row["avg_discount_pct"]
            for row in conn.execute(_DISCOUNT_SQL).fetchall()
        }
        products = [dict(row) for row in conn.execute("SELECT * FROM products").fetchall()]
        counts["products"] = len(products)

        for product in products:
            context = {
                "avg_discount_pct": discounts.get(product["id"]),
                "country_code": product.get("country_code"),
            }
            result = enrich_product(product, context)
            fields = result["fields"]

            # El use_case del scraper manda; sólo completamos si faltaba.
            existing_use_case = product.get("use_case")
            use_case = existing_use_case or fields["use_case"]
            if not existing_use_case and fields["use_case"]:
                counts["use_case_inferred"] += 1

            price_band = fields["price_band"] or product.get("price_band")
            lifecycle = fields["lifecycle_stage"] or product.get("lifecycle_stage")
            if fields["price_band"]:
                counts["price_band_set"] += 1
            if fields["lifecycle_stage"]:
                counts["lifecycle_set"] += 1

            cursor = conn.execute(_UPDATE_SQL, (
                fields["normalized_product_name"],
                use_case,
                price_band,
                lifecycle,
                fields["enrichment_version"],
                product["id"],
            ))
            counts["updated"] += cursor.rowcount or 0

            for attribute in result["attributes"]:
                conn.execute(_UPSERT_ATTR_SQL, (
                    product["id"],
                    attribute["attr_group"],
                    attribute["attr_name"],
                    attribute["value_text"],
                    attribute["value_num"],
                    attribute["confidence"],
                    attribute["source"],
                ))
                counts["attributes"] += 1

    return counts
