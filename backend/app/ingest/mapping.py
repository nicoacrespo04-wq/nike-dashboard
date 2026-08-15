"""Mapeo `pricing_data` (Postgres, ancha y desnormalizada) -> modelo normalizado.

Cada fila de `pricing_data` es *una comparación*: un producto de competidor
observado en un retailer y una fecha, enfrentado contra el producto Nike
comparable. De ahí salen **dos productos** (el del competidor y el de Nike),
sus observaciones de precio y sus observaciones de stock.

Todo lo de este módulo son funciones puras: reciben un `dict` con las columnas
de `pricing_data` (nombres de columna en snake_case, tal cual el esquema de
`db/schema.sql`) y devuelven dicts listos para persistir. No tocan la base ni
la red, así se testean sin Postgres.

Reglas que se respetan acá:
  * **Precios sucios**: mismo criterio que `web/src/lib/price.ts` y
    `db/load_csv.py` (ver `sanitize_price`). No se inventa un criterio nuevo.
  * **Nada de enrichment**: `normalized_product_name`, `use_case`,
    `price_band` y `lifecycle_stage` quedan en NULL. Los completa
    `app.services.enrichment`, que es su dueño.
  * **Cero valores inventados**: lo que la fuente no trae queda en NULL.
  * **Configurable**: los mapas de retailer/importancia/canal salen de
    `weights.yaml` (sección `ingest`) o de un archivo apuntado por
    `CI_INGEST_CONFIG`; el código sólo trae los defaults.
"""

from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from app.config import section

# Lados de la fila ancha.
COMPETITOR = "competitor"
NIKE = "nike"
SIDES = (COMPETITOR, NIKE)

#: Marca analizada (la única con `is_focus = 1`).
NIKE_BRAND = "NIKE"

# ============================================================
# Configuración (defaults en código, override por weights.yaml / archivo)
# ============================================================

_DEFAULTS: dict[str, Any] = {
    # ── Marca ────────────────────────────────────────────────
    # `marca` llega como 'Nike' / 'NIKE' / 'nike'. Se normaliza a UPPER y se
    # resuelven alias (mismo criterio que db/normalize_marca.py).
    "brand_aliases": {
        "NIKE": "NIKE", "NIKE SB": "NIKE", "JORDAN": "NIKE", "NIKE AR": "NIKE",
        "ADIDAS": "ADIDAS", "ADIDAS ORIGINALS": "ADIDAS", "ADIDAS PERFORMANCE": "ADIDAS",
        "PUMA": "PUMA",
        "NB": "NEW BALANCE", "NEW BALANCE": "NEW BALANCE",
        "UA": "UNDER ARMOUR", "UNDER ARMOUR": "UNDER ARMOUR",
        "ASICS": "ASICS", "TOPPER": "TOPPER", "REEBOK": "REEBOK",
        "FILA": "FILA", "UMBRO": "UMBRO", "MIZUNO": "MIZUNO",
    },

    # ── Scrapers que NO son retailers ────────────────────────
    # Son capturas del sitio propio de una marca. NO entran a la lista de
    # retailers (`is_retailer: false`) pero sí alimentan el catálogo D2C: sus
    # productos y precios son reales y hay que conservarlos.
    "brand_site_scrapers": {
        "nike_ar_general": {"name": "nike.com.ar", "country_code": "AR", "brand": "NIKE"},
        "nike_co_general": {"name": "nike.com.co", "country_code": "CO", "brand": "NIKE"},
        "nike_us_general": {"name": "nike.com",    "country_code": "US", "brand": "NIKE"},
        "uru":             {"name": "nike.com.uy", "country_code": "UY", "brand": "NIKE"},
        "usa":             {"name": "nike.com",    "country_code": "US", "brand": "NIKE"},
        "adidas_7":        {"name": "adidas.com.ar", "country_code": "AR", "brand": "ADIDAS"},
        "puma_ar":         {"name": "puma.com.ar",   "country_code": "AR", "brand": "PUMA"},
    },

    # Nombre canónico por clave normalizada (así 'StockCenter', 'Stock Center'
    # y 'stock_center' terminan en un único retailer).
    "retailer_names": {
        "dexter": "Dexter",
        "stockcenter": "Stock Center",
        "solodeportes": "Solo Deportes",
        "opensports": "Open Sports",
        "moov": "Moov",
        "sporting": "Sporting",
        "grid": "Grid",
        "digitalsport": "Digital Sport",
        "mercadolibre": "Mercado Libre",
        "dafiti": "Dafiti",
        "netshoes": "Netshoes",
    },

    # Peso estratégico del canal (0..1) -> `retailers.importance`.
    "retailer_importance": {
        "default": 0.5,
        "nikecomar": 0.95,
        "nikecom": 0.9,
        "nikecomco": 0.85,
        "nikecomuy": 0.8,
        "dexter": 0.85,
        "stockcenter": 0.8,
        "solodeportes": 0.7,
        "opensports": 0.65,
        "moov": 0.6,
        "sporting": 0.55,
        "grid": 0.45,
        "adidascomar": 0.6,
        "pumacomar": 0.6,
    },

    # Canal comercial -> `retailers.channel`.
    "retailer_channels": {
        "default": "B2B",
        "moov": "MARKETPLACE",
        "mercadolibre": "MARKETPLACE",
        "dafiti": "MARKETPLACE",
        "netshoes": "MARKETPLACE",
    },

    # Tienda propia de Nike por país: es donde se imputa el precio del bloque
    # Nike de la fila (ver `nike_price_channel`).
    "nike_d2c_by_country": {
        "AR": "nike.com.ar",
        "US": "nike.com",
        "CO": "nike.com.co",
        "UY": "nike.com.uy",
    },

    # A qué retailer se imputa el precio del bloque Nike:
    #   'd2c'          -> a la tienda propia de Nike del país (default).
    #   'row_retailer' -> al retailer de la fila.
    # El default es el conservador: la presencia de Nike EN un retailer se
    # deriva de filas con marca='NIKE' capturadas en ese retailer, no de la
    # columna de referencia.
    "nike_price_channel": "d2c",

    "countries": {
        "AR": {"name": "Argentina", "currency": "ARS"},
        "US": {"name": "Estados Unidos", "currency": "USD"},
        "UY": {"name": "Uruguay", "currency": "UYU"},
        "CO": {"name": "Colombia", "currency": "COP"},
        "BR": {"name": "Brasil", "currency": "BRL"},
        "CL": {"name": "Chile", "currency": "CLP"},
    },
    "default_country": "AR",

    # Sufijo de país en el nombre del scraper/canal ('Dexter_AR', 'Moov_CL').
    # Es la única pista de país que traen las filas de retailer: si contradice
    # al país pedido, la fila se descarta (carga parcial por país).
    "country_suffix_map": {
        "ar": "AR", "uy": "UY", "uru": "UY", "us": "US", "usa": "US",
        "co": "CO", "cl": "CL", "br": "BR", "mx": "MX", "pe": "PE", "py": "PY",
    },

    # ── Taxonomía ────────────────────────────────────────────
    # `division` / `division_competitor` -> products.category
    "division_map": {
        "footwear": "footwear", "footwear division": "footwear", "calzado": "footwear",
        "apparel": "apparel", "apparel division": "apparel", "indumentaria": "apparel",
        "accessories": "accessories", "equipment": "accessories",
        "accessories division": "accessories", "equipment division": "accessories",
        "accesorios": "accessories",
    },
    # `category` / `category_competitor` -> products.sport
    "sport_map": {
        "football/soccer": "football", "football": "football", "soccer": "football",
        "futbol": "football", "fútbol": "football",
        "running": "running", "run": "running",
        "basketball": "basketball", "basket": "basketball", "jordan": "basketball",
        "training": "training", "training/gym": "training", "gym": "training",
        "tennis": "tennis", "tenis": "tennis",
        "sportswear": "lifestyle", "originals": "lifestyle", "lifestyle": "lifestyle",
        "skateboarding": "skateboarding", "swim": "swim",
    },
    # `gender` / `gender_competitor` -> products.gender
    "gender_map": {
        "mens": "men", "men": "men", "man": "men", "male": "men", "hombre": "men",
        "m": "men", "adult unisex men": "men",
        "womens": "women", "women": "women", "woman": "women", "female": "women",
        "mujer": "women", "w": "women", "dama": "women",
        "unisex": "unisex", "adult unisex": "unisex", "u": "unisex",
        "kids": "kids", "kid": "kids", "junior": "kids", "juniors": "kids",
        "boys": "kids", "girls": "kids", "ninos": "kids", "niños": "kids",
        "grade school": "kids", "preschool": "kids", "infant": "kids", "youth": "kids",
    },
    # Géneros que además fijan `age_segment = 'kids'` (el resto queda NULL: no
    # se afirma "adult" sin evidencia).
    "kids_genders": ["kids"],

    # Orden de preferencia para identificar al SKU del competidor.
    # `productcode_competitor` va primero porque en el CSV original pertenece
    # al bloque del competidor; `product_code_competitor` (con guión bajo) cae
    # dentro del bloque Nike y se usa sólo como respaldo.
    "competitor_code_columns": ["productcode_competitor", "product_code_competitor"],

    # `availability_pct` se deriva sólo si podemos inferir el grid de talles a
    # partir de `text_sizes_*`. Si no, queda NULL (nunca se inventa el
    # denominador).
    "derive_availability_pct": True,
}


@lru_cache(maxsize=1)
def _override_file() -> dict[str, Any]:
    path = os.getenv("CI_INGEST_CONFIG")
    if not path or not os.path.exists(path):
        return {}
    text = open(path, encoding="utf-8").read()
    if path.endswith((".yaml", ".yml")):
        import yaml  # dependencia ya presente

        return yaml.safe_load(text) or {}
    return json.loads(text or "{}")


#: Mapas cuyas claves son nombres de scraper/retailer: se canonicalizan para
#: que 'nike_ar_general', 'Nike AR General' y 'nikeargeneral' sean la misma.
_CANON_KEY_MAPS = ("brand_site_scrapers", "retailer_names",
                   "retailer_importance", "retailer_channels")


@lru_cache(maxsize=1)
def ingest_config() -> dict[str, Any]:
    """Defaults del módulo + `weights.yaml:ingest` + `CI_INGEST_CONFIG`."""
    cfg = copy.deepcopy(_DEFAULTS)
    for override in (section("ingest", default=None), _override_file()):
        if not isinstance(override, dict):
            continue
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key] = {**cfg[key], **value}
            else:
                cfg[key] = value

    for name in _CANON_KEY_MAPS:
        cfg[name] = {(key if key == "default" else canon_key(key)): value
                     for key, value in cfg[name].items()}
    cfg["brand_aliases"] = {str(k).upper(): v for k, v in cfg["brand_aliases"].items()}
    cfg["countries"] = {str(k).upper(): v for k, v in cfg["countries"].items()}
    cfg["nike_d2c_by_country"] = {str(k).upper(): v
                                  for k, v in cfg["nike_d2c_by_country"].items()}
    return cfg


@lru_cache(maxsize=1)
def price_limits() -> tuple[float, float, int]:
    """`(min_ars, max_ars, max_cuotas)` — mismas env vars que price.ts/load_csv.py."""
    def _num(name: str, fallback: float) -> float:
        try:
            value = float(os.getenv(name, "") or fallback)
        except ValueError:
            return fallback
        return value if value > 0 else fallback

    return (_num("PRICE_MIN_ARS", 1000.0),
            _num("PRICE_MAX_ARS", 2_000_000.0),
            int(_num("PRICE_MAX_CUOTAS", 24.0)))


def reset_config_cache() -> None:
    """Invalida la config cacheada (tests / cambio de env vars)."""
    ingest_config.cache_clear()
    price_limits.cache_clear()
    _override_file.cache_clear()
    country_meta.cache_clear()
    currency_for.cache_clear()


# ============================================================
# Utilidades de normalización
# ============================================================

_NULLISH = {"", "nan", "none", "null", "n/a", "#n/a", "na", "-", "--",
            "n/d", "nd", "s/d", "sin dato", "sin datos", "#value!", "#ref!"}
_CUOTAS_RE = re.compile(r"\d+")
_SIZE_SPLIT_RE = re.compile(r"[^0-9A-Za-z.,/]+")


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean_text(value: Any) -> str | None:
    """Texto usable o ``None``. Convierte los 'nulos disfrazados' del scraper."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:      # NaN
        return None
    text = str(value).replace(" ", " ").strip()
    if text.lower() in _NULLISH:
        return None
    return text


def canon_key(value: Any) -> str:
    """Clave canónica: sin acentos, sin puntuación, minúsculas, sin espacios.

    ``'Stock Center'``, ``'StockCenter'`` y ``'stock_center'`` -> ``'stockcenter'``.
    """
    text = clean_text(value)
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", _strip_accents(text).lower())


def norm_lower(value: Any) -> str | None:
    """Minúsculas, sin acentos, espacios colapsados (para mapas de taxonomía)."""
    text = clean_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", _strip_accents(text).lower()).strip() or None


def to_number(value: Any) -> float | None:
    """`Decimal`/`str`/`int` -> float. Devuelve ``None`` si no es numérico."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return None if number != number else number
    text = clean_text(value)
    if text is None:
        return None
    text = text.replace(" ", "")
    # '1.234.567,89' (es-AR) -> 1234567.89 ; '1234.56' se deja igual.
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_number(value)
    return None if number is None else int(number)


def to_iso_date(value: Any) -> str | None:
    """`date`/`datetime`/`'2026-08-10'`/`'10/08/2026'` -> ``'YYYY-MM-DD'``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = clean_text(value)
    if text is None:
        return None
    text = text.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ============================================================
# 1. Marca
# ============================================================

def map_brand(row: dict, side: str = COMPETITOR) -> str:
    """Marca normalizada en UPPER. El lado Nike siempre es ``'NIKE'``.

    ``'Nike'``/``'NIKE'``/``'nike'`` -> ``'NIKE'``; ``'Puma'`` -> ``'PUMA'``.
    Mismo criterio que `db/normalize_marca.py`, extendido con alias.
    """
    if side == NIKE:
        return NIKE_BRAND

    raw = clean_text(row.get("marca"))
    if raw is None:
        # Sin marca declarada: si el scraper es el sitio de una marca, se usa esa.
        site = _brand_site(row)
        return str(site["brand"]).upper() if site else "UNKNOWN"

    upper = re.sub(r"\s+", " ", _strip_accents(raw).upper()).strip()
    aliases = ingest_config()["brand_aliases"]
    return str(aliases.get(upper, upper))


def is_nike_brand(name: str | None) -> bool:
    return (name or "").strip().upper() == NIKE_BRAND


# ============================================================
# 2. País y retailer
# ============================================================

def _brand_site(row: dict) -> dict[str, Any] | None:
    """Definición del sitio de marca si el scraper de la fila es uno de ellos."""
    sites = ingest_config()["brand_site_scrapers"]
    for column in ("scraper", "canal"):
        key = canon_key(row.get(column))
        if key and key in sites:
            return sites[key]
    return None


def _country_from_suffix(row: dict) -> str | None:
    """País declarado en el nombre del scraper/canal: ``'Dexter_AR'`` -> ``'AR'``."""
    suffixes = ingest_config()["country_suffix_map"]
    for column in ("scraper", "canal"):
        raw = clean_text(row.get(column))
        if not raw:
            continue
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", _strip_accents(raw)) if t]
        if not tokens:
            continue
        last = tokens[-1].lower()
        if len(last) <= 3 and last in suffixes:
            return str(suffixes[last]).upper()
    return None


def map_country(row: dict, default: str | None = None) -> str:
    """País de la fila.

    Prioridad: scraper de sitio de marca (URU/USA/nike_co_general) > sufijo de
    país en el nombre del scraper ('Dexter_AR') > país pedido en la ingesta.
    """
    cfg = ingest_config()
    site = _brand_site(row)
    if site and site.get("country_code"):
        return str(site["country_code"]).upper()
    suffix = _country_from_suffix(row)
    if suffix:
        return suffix
    return str(default or cfg["default_country"]).upper()


@lru_cache(maxsize=64)
def country_meta(code: str) -> dict[str, str]:
    cfg = ingest_config()
    meta = cfg["countries"].get(code.upper(), {})
    return {
        "code": code.upper(),
        "name": str(meta.get("name") or code.upper()),
        "currency": str(meta.get("currency") or ""),
    }


@lru_cache(maxsize=64)
def currency_for(country_code: str) -> str:
    return country_meta(country_code)["currency"] or "ARS"


def _strip_country_suffix(raw: str) -> str:
    """``'Dexter_AR'`` -> ``'Dexter'``: el país ya se resolvió aparte.

    Sin esto, 'Dexter' y 'Dexter_AR' serían dos retailers distintos y el del
    sufijo perdería su importancia configurada.
    """
    suffixes = ingest_config()["country_suffix_map"]
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", _strip_accents(raw)) if t]
    if len(tokens) > 1 and len(tokens[-1]) <= 3 and tokens[-1].lower() in suffixes:
        return " ".join(tokens[:-1])
    return raw


def retailer_key(name: str, country_code: str) -> str:
    """Clave natural del retailer: ``(nombre canónico, país)`` — como el UNIQUE."""
    return f"{canon_key(name)}|{str(country_code or '').upper()}"


def map_retailer(row: dict, default_country: str | None = None) -> dict[str, Any]:
    """Retailer donde se capturó la fila.

    Devuelve ``{key, name, country_code, channel, importance, is_retailer}``.

    `is_retailer=False` para los scrapers de sitios de marca
    ('nike_ar_general', 'URU', 'ADIDAS_7', ...): NO son retailers y por eso
    quedan fuera de esa lista, pero sí se conservan como canal D2C para no
    perder su catálogo ni sus precios.
    """
    cfg = ingest_config()
    country = map_country(row, default_country)

    site = _brand_site(row)
    if site:
        name = str(site["name"])
        site_country = str(site.get("country_code") or country).upper()
        return {
            "key": retailer_key(name, site_country),
            "name": name,
            "country_code": site_country,
            "channel": "D2C",
            "importance": float(cfg["retailer_importance"].get(
                canon_key(name), cfg["retailer_importance"]["default"])),
            "is_retailer": False,
        }

    raw = clean_text(row.get("canal")) or clean_text(row.get("scraper")) or "UNKNOWN"
    raw = _strip_country_suffix(raw)
    key = canon_key(raw)
    name = cfg["retailer_names"].get(key) or re.sub(r"[_\-]+", " ", raw).strip().title()
    return {
        "key": retailer_key(name, country),
        "name": name,
        "country_code": country,
        "channel": str(cfg["retailer_channels"].get(key, cfg["retailer_channels"]["default"])),
        "importance": float(cfg["retailer_importance"].get(
            key, cfg["retailer_importance"]["default"])),
        "is_retailer": True,
    }


def nike_d2c_retailer(country_code: str) -> dict[str, Any]:
    """Tienda propia de Nike del país (destino del precio del bloque Nike)."""
    cfg = ingest_config()
    country = (country_code or cfg["default_country"]).upper()
    name = cfg["nike_d2c_by_country"].get(country) or f"nike.com.{country.lower()}"
    key = canon_key(name)
    return {
        "key": retailer_key(name, country),
        "name": name,
        "country_code": country,
        "channel": "D2C",
        "importance": float(cfg["retailer_importance"].get(
            key, cfg["retailer_importance"]["default"])),
        "is_retailer": False,
    }


def retailer_for_side(row: dict, side: str, default_country: str | None = None) -> dict[str, Any]:
    """Retailer al que se imputa la observación de cada lado de la fila."""
    retailer = map_retailer(row, default_country)
    if side != NIKE:
        return retailer
    if ingest_config()["nike_price_channel"] == "row_retailer":
        return retailer
    if retailer["channel"] == "D2C" and canon_key(retailer["name"]).startswith("nikecom"):
        return retailer                     # la fila ya es de nike.com.*
    return nike_d2c_retailer(retailer["country_code"])


# ============================================================
# 3. Taxonomía
# ============================================================

def map_category(value: Any) -> str | None:
    """`division` -> `products.category` ('FOOTWEAR DIVISION' -> 'footwear')."""
    text = norm_lower(value)
    if text is None:
        return None
    mapped = ingest_config()["division_map"].get(text)
    if mapped:
        return mapped
    for needle, canonical in ingest_config()["division_map"].items():
        if needle in text:
            return canonical
    return text


def map_sport(value: Any) -> str | None:
    """`category` -> `products.sport` ('FOOTBALL/SOCCER' -> 'football')."""
    text = norm_lower(value)
    if text is None:
        return None
    mapped = ingest_config()["sport_map"].get(text)
    if mapped:
        return mapped
    head = text.split("/")[0].strip()
    return ingest_config()["sport_map"].get(head, head) or None


def map_gender(value: Any) -> str | None:
    text = norm_lower(value)
    if text is None:
        return None
    return ingest_config()["gender_map"].get(text, text)


def map_age_segment(gender: str | None) -> str | None:
    """Sólo se afirma 'kids' cuando el género lo dice. El resto queda NULL."""
    if gender and gender in set(ingest_config()["kids_genders"]):
        return "kids"
    return None


# ============================================================
# 4. Precios sucios
# ============================================================
# CRITERIO REPLICADO de `web/src/lib/price.ts` y `db/load_csv.py`
# (si se cambia acá, cambiarlo allá también):
#
#   · valor <= 0                     -> None  (dato ausente, NUNCA 0)
#   · dentro de [MIN, MAX]           -> se deja como está
#   · fuera de rango + cuotas declaradas y `precio/cuotas` plausible
#                                    -> se corrige dividiendo (2.639.992 / 8)
#   · fuera de rango sin corrección  -> None  (no se adivina el divisor)
# ============================================================

def parse_cuotas(raw: Any) -> int | None:
    """``'8 cuotas sin interés'`` / ``'12x'`` / ``6`` -> ``int``; ``None`` si no se infiere."""
    if raw is None:
        return None
    match = _CUOTAS_RE.search(str(raw))
    if not match:
        return None
    cuotas = int(match.group(0))
    _, _, max_cuotas = price_limits()
    if cuotas < 2 or cuotas > max_cuotas:
        return None
    return cuotas


def is_plausible_price(value: Any) -> bool:
    number = to_number(value)
    if number is None:
        return False
    minimum, maximum, _ = price_limits()
    return minimum <= number <= maximum


def sanitize_price(value: Any, cuotas: Any = None) -> tuple[float | None, str | None]:
    """Devuelve ``(precio_saneado, motivo)``.

    ``motivo`` ∈ ``{None, 'zero', 'cuotas', 'out_of_range', 'not_numeric'}``.
    ``'cuotas'`` es el único caso en que el precio se corrige; el resto de los
    motivos implican descarte (el valor devuelto es ``None``).
    """
    if value is None or (isinstance(value, str) and clean_text(value) is None):
        return None, None
    number = to_number(value)
    if number is None:
        return None, "not_numeric"
    if number <= 0:
        return None, "zero"
    if is_plausible_price(number):
        return number, None
    installments = cuotas if isinstance(cuotas, int) else parse_cuotas(cuotas)
    if installments:
        unit = number / installments
        if is_plausible_price(unit):
            return round(unit, 2), "cuotas"
    return None, "out_of_range"


def discount_pct(full_price: float | None, current_price: float | None) -> float | None:
    """Descuento 0..100 a partir de precios ya saneados. ``None`` si no se puede."""
    if full_price is None or current_price is None or full_price <= 0:
        return None
    pct = (full_price - current_price) / full_price * 100.0
    if pct < -0.01 or pct > 100.0:
        return None                       # dato inconsistente: mejor N/D
    return round(max(pct, 0.0), 3)


# ============================================================
# 5. Producto
# ============================================================

def _competitor_code(row: dict) -> str | None:
    for column in ingest_config()["competitor_code_columns"]:
        code = clean_text(row.get(column))
        if code:
            return code
    return None


def map_product(row: dict, side: str = COMPETITOR, *, country: str | None = None) -> dict[str, Any]:
    """Fila ancha -> columnas de `products` para un lado de la comparación.

    `side='competitor'` usa el bloque del competidor (lo que se vio en el
    retailer); `side='nike'` usa el bloque Nike de referencia.

    Los campos que son responsabilidad de `enrichment`
    (`normalized_product_name`, `use_case`, `price_band`, `lifecycle_stage`,
    `enrichment_version`) NO se escriben acá.
    """
    country_code = (country or map_country(row)).upper()
    brand = map_brand(row, side)

    if side == NIKE:
        style_color = clean_text(row.get("style_color"))
        name = clean_text(row.get("marketing_name")) or style_color
        sku = style_color
        style_code = style_color
        franchise = clean_text(row.get("franchise_scrapper"))
        category = map_category(row.get("division"))
        sport = map_sport(row.get("category"))
        gender = map_gender(row.get("gender"))
        msrp, _ = sanitize_price(row.get("precio_sugerido"), row.get("cuotas_nike"))
        if msrp is None:
            msrp, _ = sanitize_price(row.get("nike_full_price"), row.get("cuotas_nike"))
        url = clean_text(row.get("pdp_nike"))
    else:
        sku = _competitor_code(row)
        name = clean_text(row.get("product_name_competitor")) or sku
        style_code = None
        franchise = clean_text(row.get("franchise_competitor"))
        category = map_category(row.get("division_competitor"))
        sport = map_sport(row.get("category_competitor"))
        gender = map_gender(row.get("gender_competitor"))
        msrp, _ = sanitize_price(row.get("competitor_full_price"), row.get("cuotas_competitor"))
        url = clean_text(row.get("link_pdp_competitor"))

    product = {
        "side": side,
        "brand": brand,
        "is_focus": 1 if is_nike_brand(brand) else 0,
        "country_code": country_code,
        "product_name": name,
        "sku": sku,
        "style_code": style_code,
        "franchise": franchise,
        "category": category,
        # `silueta` describe la silueta comparada (BOTIN, RUNNING, ...); aplica
        # a ambos lados porque la fila es una comparación like-for-like.
        "subcategory": norm_lower(row.get("silueta")),
        "sport": sport,
        "gender": gender,
        "age_segment": map_age_segment(gender),
        "msrp": msrp,
        "url": url,
        # Sin fuente en `pricing_data` -> NULL (los completa enrichment o nadie).
        "model": None,
        "version": None,
        "launch_date": None,
        "activity": None,
        "performance_vs_lifestyle": None,
        "consumer_segment": None,
        "image_url": None,
        "description": None,
        "observed_at": to_iso_date(row.get("fecha_corrida")),
    }
    product["key"] = product_key(product)
    return product


def product_key(product: dict) -> tuple[str, str, str]:
    """Clave natural de deduplicación: ``(marca, país, código|nombre)``.

    El mismo SKU aparece en muchos retailers y muchas fechas: todas esas filas
    deben colapsar en UN registro de `products`. Si no hay código, se usa el
    nombre normalizado como último recurso (prefijado para no chocar con un
    código real).
    """
    brand = str(product.get("brand") or "").upper()
    country = str(product.get("country_code") or "").upper()
    code = canon_key(product.get("sku")) or canon_key(product.get("style_code"))
    if not code:
        code = "name:" + (norm_lower(product.get("product_name")) or "")
    return (brand, country, code)


def is_mappable_product(product: dict) -> bool:
    """Un producto sin nombre ni código no es identificable: se descarta."""
    if not clean_text(product.get("product_name")):
        return False
    key = product.get("key") or product_key(product)
    return key[2] not in ("", "name:")


# ============================================================
# 6. Observaciones de precio
# ============================================================

def map_price_observation(row: dict, side: str = COMPETITOR, *,
                          country: str | None = None,
                          product: dict | None = None,
                          retailer: dict | None = None) -> dict[str, Any]:
    """Fila ancha -> fila de `price_observations` para un lado.

    Devuelve además ``flags`` (motivos de saneamiento por columna) para poder
    reportar cuántos precios se descartaron o corrigieron y por qué, y
    ``usable=False`` cuando no queda ningún precio utilizable.
    """
    country_code = (country or map_country(row)).upper()
    # `product` / `retailer` se pueden pasar ya calculados (la ingesta los
    # reutiliza para no remapear la misma fila tres veces).
    retailer = retailer or retailer_for_side(row, side, country_code)
    product = product or map_product(row, side, country=country_code)

    if side == NIKE:
        cuotas_raw, full_col, final_col = "cuotas_nike", "nike_full_price", "nike_final_price"
    else:
        cuotas_raw, full_col, final_col = ("cuotas_competitor", "competitor_full_price",
                                           "competitor_final_price")

    cuotas = parse_cuotas(row.get(cuotas_raw))
    full_price, full_reason = sanitize_price(row.get(full_col), cuotas)
    current_price, current_reason = sanitize_price(row.get(final_col), cuotas)

    flags = {col: reason for col, reason in
             ((full_col, full_reason), (final_col, current_reason)) if reason}

    return {
        "side": side,
        "product_key": product["key"],
        "retailer_key": retailer["key"],
        "observed_at": to_iso_date(row.get("fecha_corrida")),
        "full_price": full_price,
        "current_price": current_price,
        "discount_pct": discount_pct(full_price, current_price),
        "currency": currency_for(country_code),
        "cuotas": cuotas,
        "flags": flags,
        "usable": full_price is not None or current_price is not None,
    }


# ============================================================
# 7. Observaciones de stock
# ============================================================

def parse_sizes(raw: Any) -> list[str]:
    """``'38 | 39 | 40.5'`` -> ``['38', '39', '40.5']``. Vacío si no se puede."""
    text = clean_text(raw)
    if text is None:
        return []
    tokens = [t.strip().upper().rstrip(".") for t in _SIZE_SPLIT_RE.split(text)]
    seen: dict[str, None] = {}
    for token in tokens:
        if token and len(token) <= 6:
            seen.setdefault(token, None)
    return list(seen)


def map_stock_observation(row: dict, side: str = COMPETITOR, *,
                          country: str | None = None,
                          product: dict | None = None,
                          retailer: dict | None = None) -> dict[str, Any]:
    """Fila ancha -> fila de `stock_observations` para un lado.

    `size_available_*` -> `sizes_available`. `availability_pct` y `sizes_total`
    quedan en NULL: el total de talles del producto no está en la fila y se
    infiere después, sólo si `text_sizes_*` permite reconstruir el grid
    (ver `pricing_data._apply_size_grid`). Nunca se inventa un denominador.
    """
    country_code = (country or map_country(row)).upper()
    retailer = retailer or retailer_for_side(row, side, country_code)
    product = product or map_product(row, side, country=country_code)

    if side == NIKE:
        sizes_available = to_int(row.get("size_available_nike"))
        size_tokens = parse_sizes(row.get("text_sizes_nike"))
    else:
        sizes_available = to_int(row.get("size_available_competitor"))
        size_tokens = parse_sizes(row.get("text_sizes_competitor"))

    if sizes_available is not None and sizes_available < 0:
        sizes_available = None

    return {
        "side": side,
        "product_key": product["key"],
        "retailer_key": retailer["key"],
        "observed_at": to_iso_date(row.get("fecha_corrida")),
        "in_stock": None if sizes_available is None else int(sizes_available > 0),
        "sizes_available": sizes_available,
        "sizes_total": None,
        "availability_pct": None,
        "size_tokens": size_tokens,
        "usable": sizes_available is not None,
    }
