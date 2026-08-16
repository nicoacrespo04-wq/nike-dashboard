#!/usr/bin/env python
"""Genera un `pricing_data` SINTÉTICO a escala real, con la misma suciedad.

Por qué
-------
La Supabase real no está disponible para probar (y su credencial está publicada
en el repo sin rotar: NO se usa). Pero un motor que anduvo con 45 productos demo
no prueba nada sobre 1.000 productos y 70.000 filas: el matching es
O(nike × competidores) y la calibración se hizo contra una distribución de 45
productos. Hace falta un dataset del tamaño del real para medir.

Qué genera
----------
Una tabla `pricing_data` con la MISMA forma que `db/schema.sql` y con la misma
suciedad observada en los datos reales:

  * **casing mixto de marca**: ``'Adidas'`` / ``'ADIDAS'`` / ``'adidas'`` /
    ``' Adidas '`` — la misma marca escrita de cuatro formas.
  * **precios multiplicados por cuotas**: el scraper capturó el total en N
    cuotas en vez del precio unitario (``296.999 * 6``), con y sin la columna
    de cuotas declarada.
  * **precios en 0**: el ``0`` que en realidad significa "no lo pude leer".
  * **campos faltantes** en todos los disfraces del scraper: ``''``, ``'N/A'``,
    ``'#N/A'``, ``'nan'``, ``NULL``.
  * **el mismo `style_color` en varios retailers** (y el mismo código de
    competidor): es exactamente el caso que duplicaba productos.
  * **nombres de scraper inconsistentes**: ``'Dexter_AR'`` / ``'dexter_ar'`` /
    ``'Dexter'``, más los scrapers de sitio de marca (``nike_ar_general``,
    ``adidas_7``, ``puma_ar``) que NO son retailers.
  * **filas de otro país** (``Moov_CL``) que la ingesta tiene que descartar.
  * **filas sin `fecha_corrida`**.

Y una estructura del dato real que NO es suciedad pero cambia todo lo que el
motor puede concluir: **~20% de las filas traen un producto NIKE en el bloque
"competidor"** (``marca='Nike'``), es decir Nike capturado EN un retailer. Es la
única evidencia de presencia de Nike en góndola: sin esas filas Nike existe sólo
en su D2C, ninguna comparación "en el mismo retailer" es posible y las reglas
que la necesitan (`price_competitiveness_risk`, retail media) se quedan sin
entrada.

Es determinístico: mismo ``--seed``, mismo dataset.

Uso
---
    # a Postgres local (crea la tabla y la puebla)
    python tools/generate_scale_fixture.py --dsn postgresql://localhost/ci_scale

    # a CSV (mismos encabezados que la tabla)
    python tools/generate_scale_fixture.py --csv /tmp/pricing_scale.csv

    # tamaño a medida
    python tools/generate_scale_fixture.py --dsn ... --rows 70000 \
        --products 1000 --retailers 10 --dates 5
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

# Permite ejecutarlo como script desde backend/ sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ============================================================
# Catálogo sintético (nombres reales del mercado AR de sneakers)
# ============================================================

#: Columnas que se generan (subconjunto de `db/schema.sql::pricing_data`: las
#: derivadas —gaps, BML, USD— las recalcula el motor y la ingesta no las lee).
COLUMNS: tuple[str, ...] = (
    "fecha_corrida", "scraper", "canal", "marca", "season", "style_color",
    "product_code_competitor", "marketing_name", "division", "category",
    "franchise_scrapper", "gender", "productcode_competitor",
    "product_name_competitor", "category_competitor", "division_competitor",
    "franchise_competitor", "gender_competitor", "size_available_competitor",
    "size_available_nike", "link_pdp_competitor", "competitor_full_price",
    "competitor_final_price", "cuotas_competitor", "nike_full_price",
    "nike_final_price", "cuotas_nike", "text_sizes_nike", "text_sizes_competitor",
    "pdp_nike", "precio_sugerido", "silueta",
)

#: DDL mínima compatible con `db/schema.sql` (mismos nombres y tipos).
DDL = """
DROP TABLE IF EXISTS pricing_data;
CREATE TABLE pricing_data (
    id                          BIGSERIAL PRIMARY KEY,
    fecha_corrida               DATE,
    scraper                     TEXT,
    canal                       TEXT,
    marca                       TEXT,
    season                      TEXT,
    style_color                 TEXT,
    product_code_competitor     TEXT,
    marketing_name              TEXT,
    division                    TEXT,
    category                    TEXT,
    franchise_scrapper          TEXT,
    gender                      TEXT,
    productcode_competitor      TEXT,
    product_name_competitor     TEXT,
    category_competitor         TEXT,
    division_competitor         TEXT,
    franchise_competitor        TEXT,
    gender_competitor           TEXT,
    size_available_competitor   INTEGER,
    size_available_nike         INTEGER,
    link_pdp_competitor         TEXT,
    competitor_full_price       NUMERIC(12,2),
    competitor_final_price      NUMERIC(12,2),
    cuotas_competitor           TEXT,
    nike_full_price             NUMERIC(12,2),
    nike_final_price            NUMERIC(12,2),
    cuotas_nike                 TEXT,
    text_sizes_nike             TEXT,
    text_sizes_competitor       TEXT,
    pdp_nike                    TEXT,
    precio_sugerido             NUMERIC(12,2),
    silueta                     TEXT
);
CREATE INDEX idx_pricing_fecha ON pricing_data(fecha_corrida DESC);
CREATE INDEX idx_pricing_scraper ON pricing_data(scraper);
"""

RETAILERS: tuple[tuple[str, ...], ...] = (
    ("Dexter", "Dexter_AR", "dexter_ar"),
    ("StockCenter", "Stock Center", "stockcenter_AR"),
    ("Solo Deportes", "SoloDeportes_AR", "solodeportes"),
    ("Open Sports", "OpenSports_AR", "opensports"),
    ("Moov", "Moov_AR", "moov"),
    ("Sporting", "Sporting_AR", "sporting"),
    ("Grid", "Grid_AR", "grid"),
    ("Digital Sport", "DigitalSport_AR", "digitalsport"),
    ("Mercado Libre", "MercadoLibre_AR", "mercadolibre"),
    ("Dafiti", "Dafiti_AR", "dafiti"),
)

#: Marcas competidoras con el casing mixto tal cual llega del scraper.
BRAND_CASINGS: dict[str, tuple[str, ...]] = {
    "ADIDAS": ("Adidas", "ADIDAS", "adidas", " Adidas "),
    "PUMA": ("Puma", "PUMA", "puma"),
    "NEW BALANCE": ("New Balance", "NEW BALANCE", "new balance", "NB"),
    "UNDER ARMOUR": ("Under Armour", "UNDER ARMOUR", "under armour", "UA"),
    "ASICS": ("Asics", "ASICS", "asics"),
    "TOPPER": ("Topper", "TOPPER"),
    "FILA": ("Fila", "FILA"),
    "REEBOK": ("Reebok", "REEBOK"),
    "MIZUNO": ("Mizuno", "MIZUNO"),
    "UMBRO": ("Umbro", "UMBRO"),
}

#: (silueta/uso, división, categoría deportiva) — la taxonomía que llega sucia.
SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("RUNNING", "FOOTWEAR DIVISION", "RUNNING"),
    ("RUNNING", "FOOTWEAR DIVISION", "Running"),
    ("BOTIN", "FOOTWEAR DIVISION", "FOOTBALL/SOCCER"),
    ("BASKET", "FOOTWEAR DIVISION", "BASKETBALL"),
    ("TRAINING", "FOOTWEAR DIVISION", "TRAINING"),
    ("LIFESTYLE", "FOOTWEAR DIVISION", "SPORTSWEAR"),
    ("REMERA", "APPAREL DIVISION", "TRAINING"),
    ("CAMPERA", "APPAREL DIVISION", "SPORTSWEAR"),
    ("SHORT", "APPAREL DIVISION", "RUNNING"),
    ("MOCHILA", "ACCESSORIES DIVISION", "SPORTSWEAR"),
)

GENDERS: tuple[str, ...] = ("MENS", "WOMENS", "Mens", "UNISEX", "KIDS", "Womens", "M", "W")

#: Franquicias por silueta: una "Pegasus" es running, no una mochila. Sin esto
#: el corpus no tiene estructura de segmento y el matching no se puede bloquear.
NIKE_FRANCHISES: dict[str, tuple[str, ...]] = {
    "RUNNING": ("Pegasus", "Vomero", "Structure", "Invincible", "Infinity",
                "Winflo", "Revolution", "Downshifter"),
    "BOTIN": ("Mercurial", "Tiempo", "Phantom", "Premier"),
    "BASKET": ("Giannis", "LeBron", "KD", "Kobe", "Jordan 1", "Jordan 4"),
    "TRAINING": ("Metcon", "Free Metcon", "Zoom Fly", "Flex"),
    "LIFESTYLE": ("Air Force 1", "Dunk", "Blazer", "Air Max", "Court Vision", "Cortez"),
    "REMERA": ("Dri-FIT Miler", "Sportswear Club", "Pro Tee", "Academy Top"),
    "CAMPERA": ("Windrunner", "Sportswear Tech", "Therma-FIT", "Repel"),
    "SHORT": ("Challenger", "Flex Stride", "Dri-FIT Stride", "Totality"),
    "MOCHILA": ("Brasilia", "Elemental", "Heritage", "Academy Team"),
}

COMPETITOR_FRANCHISES: dict[str, dict[str, tuple[str, ...]]] = {
    "ADIDAS": {
        "RUNNING": ("Ultraboost", "Adizero", "Supernova", "Duramo"),
        "BOTIN": ("Predator", "Copa", "X Crazyfast"),
        "BASKET": ("Harden", "Dame", "Trae Young"),
        "TRAINING": ("Dropset", "Rapidmove", "Amplimove"),
        "LIFESTYLE": ("Forum", "Samba", "Gazelle", "Campus"),
        "REMERA": ("Essentials Tee", "Train Essentials", "Own The Run Tee"),
        "CAMPERA": ("Tiro Track", "Essentials Hoodie", "Terrex Jacket"),
        "SHORT": ("Own The Run Short", "Train Essentials Short"),
        "MOCHILA": ("Tiro Backpack", "Power Backpack"),
    },
    "PUMA": {
        "RUNNING": ("Velocity Nitro", "Deviate Nitro", "Magnify Nitro"),
        "BOTIN": ("Future", "Ultra", "King"),
        "BASKET": ("MB.03", "All-Pro Nitro"),
        "TRAINING": ("Fuse", "Softride"),
        "LIFESTYLE": ("Suede", "RS-X", "Palermo"),
        "REMERA": ("Essentials Tee", "Run Favorite Tee"),
        "CAMPERA": ("Iconic T7", "Evostripe"),
        "SHORT": ("Run Favorite Short", "Essentials Short"),
        "MOCHILA": ("Phase Backpack", "Buzz Backpack"),
    },
    "NEW BALANCE": {
        "RUNNING": ("1080", "880", "860", "FuelCell Rebel"),
        "LIFESTYLE": ("550", "574", "9060"),
        "TRAINING": ("Minimus", "Dynasoft"),
        "REMERA": ("Athletics Tee", "Impact Run Tee"),
        "CAMPERA": ("Athletics Jacket",),
        "SHORT": ("Impact Run Short",),
        "MOCHILA": ("Team Backpack",),
        "BOTIN": ("Furon", "Tekela"),
        "BASKET": ("TWO WXY", "Hesi"),
    },
    "UNDER ARMOUR": {
        "RUNNING": ("HOVR Machina", "HOVR Phantom", "Flow Velociti"),
        "TRAINING": ("Charged Assert", "Charged Rogue", "Project Rock"),
        "LIFESTYLE": ("Essential", "Forge 96"),
        "REMERA": ("Tech Tee", "Rush Tee"),
        "CAMPERA": ("Storm Jacket", "Rival Hoodie"),
        "SHORT": ("Launch Short", "Tech Short"),
        "MOCHILA": ("Hustle Backpack",),
        "BOTIN": ("Magnetico", "Shadow"),
        "BASKET": ("Curry Flow", "Embiid"),
    },
    "ASICS": {
        "RUNNING": ("Gel Nimbus", "Gel Kayano", "Gel Cumulus", "Novablast", "Gel Contend"),
        "LIFESTYLE": ("Gel 1130", "Gel Lyte III", "Japan S"),
        "TRAINING": ("Gel Quantum", "Upcourt"),
        "REMERA": ("Core Tee", "Icon Tee"),
        "CAMPERA": ("Lite Show Jacket",),
        "SHORT": ("Core Short",),
        "MOCHILA": ("Sport Backpack",),
        "BOTIN": ("Ultrezza",),
        "BASKET": ("Nova Surge",),
    },
    "TOPPER": {
        "RUNNING": ("Squad", "Volcano", "Vector"),
        "BOTIN": ("Dominator", "Wander"),
        "LIFESTYLE": ("Motion", "Trainer"),
        "TRAINING": ("Fitness",),
        "REMERA": ("Basic Tee",),
        "CAMPERA": ("Rompeviento",),
        "SHORT": ("Short Basic",),
        "MOCHILA": ("Mochila Classic",),
        "BASKET": ("Court",),
    },
    "FILA": {
        "RUNNING": ("Racer", "Fluid", "Windshift"),
        "LIFESTYLE": ("Disruptor", "Ray Tracer"),
        "TRAINING": ("Trainer", "Fitness Pro"),
        "REMERA": ("Basic Tee",),
        "CAMPERA": ("Track Jacket",),
        "SHORT": ("Sport Short",),
        "MOCHILA": ("Backpack Basic",),
        "BOTIN": ("Futsal",),
        "BASKET": ("Grant Hill",),
    },
    "REEBOK": {
        "TRAINING": ("Nano", "Speed"),
        "RUNNING": ("Floatride", "Energen"),
        "LIFESTYLE": ("Club C", "Classic Leather"),
        "REMERA": ("Identity Tee",),
        "CAMPERA": ("Identity Jacket",),
        "SHORT": ("Workout Short",),
        "MOCHILA": ("Active Backpack",),
        "BOTIN": ("Futsal Pro",),
        "BASKET": ("Question", "Shaqnosis"),
    },
    "MIZUNO": {
        "RUNNING": ("Wave Rider", "Wave Inspire", "Wave Prophecy"),
        "TRAINING": ("Wave Exceed", "TC-01"),
        "LIFESTYLE": ("Contender",),
        "REMERA": ("Core Tee",),
        "CAMPERA": ("Impulse Jacket",),
        "SHORT": ("Core Short",),
        "MOCHILA": ("Backpack",),
        "BOTIN": ("Morelia", "Monarcida"),
        "BASKET": ("Wave Court",),
    },
    "UMBRO": {
        "BOTIN": ("Speciali", "Velocita", "Tocco"),
        "RUNNING": ("Runner",),
        "LIFESTYLE": ("Retro",),
        "TRAINING": ("Training Pro",),
        "REMERA": ("Poly Tee",),
        "CAMPERA": ("Windbreaker",),
        "SHORT": ("Poly Short",),
        "MOCHILA": ("Team Backpack",),
        "BASKET": ("Court Pro",),
    },
}

SEASONS: tuple[str, ...] = ("SP26", "SU26", "FA26", "HO25")

#: Cómo se disfraza un dato faltante (todos aparecen en los datos reales).
MISSING_TOKENS: tuple[Any, ...] = (None, "", "N/A", "#N/A", "nan", "-", "s/d")

#: Talles por división.
SIZE_GRIDS: dict[str, tuple[str, ...]] = {
    "FOOTWEAR DIVISION": ("38", "38.5", "39", "40", "40.5", "41", "42", "42.5", "43", "44", "45"),
    "APPAREL DIVISION": ("XS", "S", "M", "L", "XL", "XXL"),
    "ACCESSORIES DIVISION": ("U",),
}


# ============================================================
# Perfil de suciedad (todo configurable para poder medir sin ruido)
# ============================================================

class Dirt:
    """Probabilidades de cada tipo de suciedad. Los defaults imitan los datos reales."""

    def __init__(self, level: float = 1.0) -> None:
        f = max(0.0, float(level))
        self.price_by_cuotas = 0.12 * f      # precio total en N cuotas, no unitario
        self.cuotas_undeclared = 0.30        # ...y de esos, cuántos sin declarar cuotas
        self.price_zero = 0.05 * f           # 0 = "no lo pude leer"
        self.missing_field = 0.07 * f        # campo faltante (en cualquier disfraz)
        self.missing_date = 0.004 * f        # fila sin fecha_corrida
        self.foreign_country = 0.02 * f      # fila de otro país (Moov_CL)
        self.brand_site = 0.05 * f           # captura del sitio de marca (no retailer)
        self.no_sizes = 0.10 * f             # sin text_sizes_* (no hay grid inferible)
        self.missing_full_price = 0.15 * f   # sin precio de lista
        # NO es suciedad: es estructura del dato real. En `pricing_data` el
        # bloque "competidor" a veces trae un producto NIKE capturado EN el
        # retailer (marca='Nike'). Es la única fuente de presencia de Nike en
        # góndola: sin estas filas, Nike sólo existe en su D2C y ninguna regla
        # que compare "en el mismo retailer" puede disparar.
        self.nike_at_retailer = 0.20


# ============================================================
# Generación
# ============================================================

def _style_color(rng: random.Random, index: int) -> str:
    letters = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(2))
    return f"{letters}{1000 + index}-{rng.choice((100, 101, 200, 300, 400, 600, 700))}"


def _competitor_code(rng: random.Random, brand: str, index: int) -> str:
    if brand == "NEW BALANCE":
        return f"M{700 + index % 400}{rng.choice('KVSR')}{index % 20:02d}"
    if brand == "ASICS":
        return f"1011B{100 + index % 800}"
    return f"{rng.choice('IHGJQ')}{rng.randrange(1000, 9999)}{index % 100:02d}"


def build_catalog(rng: random.Random, n_nike: int, n_competitor: int) -> tuple[list[dict], list[dict]]:
    """Catálogo estable: los productos que se van a ver capturados N veces."""
    nike: list[dict] = []
    for i in range(n_nike):
        silueta, division, category = SEGMENTS[i % len(SEGMENTS)]
        pool = NIKE_FRANCHISES[silueta]
        franchise = pool[(i // len(SEGMENTS)) % len(pool)]
        version = 30 + (i // (len(SEGMENTS) * len(pool))) % 20
        nike.append({
            "style_color": _style_color(rng, i),
            "marketing_name": f"Nike {franchise} {version}",
            "franchise": franchise,
            "division": division,
            "category": category,
            "silueta": silueta,
            "gender": GENDERS[i % len(GENDERS)],
            "season": SEASONS[i % len(SEASONS)],
            "msrp": float(rng.randrange(45, 260) * 1000 + 999),
        })

    brands = list(BRAND_CASINGS)
    competitors: list[dict] = []
    for i in range(n_competitor):
        # Marca y segmento avanzan a distinto ritmo: si no, cada marca quedaría
        # encerrada en un solo segmento y el bloqueo por segmento sería trivial.
        brand = brands[i % len(brands)]
        silueta, division, category = SEGMENTS[(i // len(brands)) % len(SEGMENTS)]
        franchises = COMPETITOR_FRANCHISES[brand][silueta]
        franchise = franchises[(i // (len(brands) * len(SEGMENTS))) % len(franchises)]
        version = 1 + (i // (len(brands) * len(SEGMENTS) * len(franchises))) % 15
        competitors.append({
            "brand": brand,
            "code": _competitor_code(rng, brand, i),
            "name": f"{brand.title()} {franchise} {version}",
            "franchise": franchise,
            "division": division,
            "category": category,
            "silueta": silueta,
            "gender": GENDERS[(i + 3) % len(GENDERS)],
            "msrp": float(rng.randrange(40, 250) * 1000 + 999),
        })
    return nike, competitors


def build_triples(rng: random.Random, nike: list[dict], competitors: list[dict],
                  n_retailers: int, target_per_date: int) -> list[tuple[int, int, int]]:
    """``(idx_nike, idx_competidor, idx_retailer)`` — una comparación por fecha.

    Cada competidor se compara contra 1..4 referencias Nike **del mismo
    segmento** (así lo arma el equipo comercial) y se ve en un subconjunto de
    retailers. Se garantiza que todo producto del catálogo aparezca al menos una
    vez, y después se completa hasta el volumen pedido.
    """
    by_segment: dict[str, list[int]] = {}
    for idx, product in enumerate(nike):
        by_segment.setdefault(product["silueta"], []).append(idx)

    triples: set[tuple[int, int, int]] = set()

    # 1. Cobertura: cada competidor visto al menos en un retailer contra un Nike.
    for c_idx, competitor in enumerate(competitors):
        pool = by_segment.get(competitor["silueta"]) or list(range(len(nike)))
        for n_idx in rng.sample(pool, min(len(pool), rng.randint(1, 4))):
            for r_idx in rng.sample(range(n_retailers), rng.randint(2, min(8, n_retailers))):
                triples.add((n_idx, c_idx, r_idx))

    # 2. Cobertura del lado Nike (una referencia sin ningún competidor no existe
    #    en `pricing_data`: la fila ES la comparación).
    seen_nike = {t[0] for t in triples}
    for n_idx in range(len(nike)):
        if n_idx in seen_nike:
            continue
        triples.add((n_idx, rng.randrange(len(competitors)), rng.randrange(n_retailers)))

    ordered = sorted(triples)
    rng.shuffle(ordered)
    if len(ordered) > target_per_date:
        # Se recorta preservando la cobertura: primero una tripla por competidor.
        first: dict[int, tuple[int, int, int]] = {}
        rest: list[tuple[int, int, int]] = []
        for triple in ordered:
            if triple[1] not in first:
                first[triple[1]] = triple
            else:
                rest.append(triple)
        keep = list(first.values())
        keep.extend(rest[: max(0, target_per_date - len(keep))])
        ordered = keep
    while len(ordered) < target_per_date:       # muy pocos productos: se repite
        ordered.append(ordered[len(ordered) % max(1, len(ordered))])
    return ordered


def _maybe_missing(rng: random.Random, value: Any, dirt: Dirt) -> Any:
    if rng.random() < dirt.missing_field:
        return rng.choice(MISSING_TOKENS)
    return value


def _price_pair(rng: random.Random, msrp: float, dirt: Dirt,
                drift: float) -> tuple[Any, Any, Any]:
    """``(full_price, final_price, cuotas)`` ya ensuciados.

    Simula lo que hace el scraper mal: a veces captura el total en N cuotas en
    vez del precio unitario (y a veces ni siquiera declara las cuotas), y a
    veces escribe 0 cuando no pudo leer el precio.
    """
    full = round(msrp * drift, 2)
    discount = rng.choice((0.0, 0.0, 0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50))
    final = round(full * (1.0 - discount), 2)
    cuotas: Any = rng.choice((None, "3 cuotas sin interés", "6 cuotas sin interés",
                              "9 cuotas sin interés", "12 cuotas sin interés"))

    if rng.random() < dirt.price_zero:
        return (0, 0, cuotas)

    if rng.random() < dirt.price_by_cuotas:
        n = rng.choice((3, 6, 9, 12))
        full, final = round(full * n, 2), round(final * n, 2)
        cuotas = None if rng.random() < dirt.cuotas_undeclared else f"{n} cuotas sin interés"

    if rng.random() < dirt.missing_full_price:
        full = rng.choice(MISSING_TOKENS)
    return (full, final, cuotas)


def _sizes(rng: random.Random, division: str, dirt: Dirt) -> tuple[Any, Any]:
    """``(texto_del_grid, talles_disponibles)``."""
    grid = SIZE_GRIDS.get(division, SIZE_GRIDS["FOOTWEAR DIVISION"])
    if rng.random() < dirt.no_sizes:
        return (rng.choice(MISSING_TOKENS), rng.randrange(0, len(grid) + 1))
    available = rng.randrange(0, len(grid) + 1)
    return (" | ".join(grid), available)


def generate_rows(*, rows: int = 70_000, products: int = 1_000, retailers: int = 10,
                  dates: int = 5, seed: int = 20260816, dirt_level: float = 1.0,
                  end_date: date | None = None) -> Iterator[dict[str, Any]]:
    """Emite filas de `pricing_data` sintéticas. Determinístico por ``seed``."""
    rng = random.Random(seed)
    dirt = Dirt(dirt_level)

    n_retailers = max(1, min(retailers, len(RETAILERS)))
    dates = max(1, dates)
    n_nike = max(1, int(products * 0.4))
    n_competitor = max(1, products - n_nike)

    nike_catalog, competitor_catalog = build_catalog(rng, n_nike, n_competitor)
    triples = build_triples(rng, nike_catalog, competitor_catalog, n_retailers,
                            max(1, rows // dates))

    last = end_date or date.today()
    capture_dates = [last - timedelta(days=7 * (dates - 1 - i)) for i in range(dates)]

    # Índice por segmento para elegir el producto Nike que aparece capturado EN
    # un retailer (con la misma silueta que la referencia de la fila).
    nike_by_segment: dict[str, list[int]] = {}
    for idx, product in enumerate(nike_catalog):
        nike_by_segment.setdefault(product["silueta"], []).append(idx)

    nike_casings = ("Nike", "NIKE", "nike", " Nike ")

    for d_idx, fecha in enumerate(capture_dates):
        # Deriva de precios semana a semana (inflación + promos): sin esto no
        # hay serie temporal y el momentum es plano.
        for n_idx, c_idx, r_idx in triples:
            nike = nike_catalog[n_idx]
            competitor = competitor_catalog[c_idx]
            canal, scraper_a, scraper_b = RETAILERS[r_idx]

            # El bloque "competidor" de la fila: normalmente una marca rival,
            # a veces un producto Nike capturado en ese mismo retailer.
            rival = competitor
            if rng.random() < dirt.nike_at_retailer:
                pool = [i for i in nike_by_segment.get(nike["silueta"], []) if i != n_idx]
                if pool:
                    other = nike_catalog[rng.choice(pool)]
                    rival = {
                        "brand": "NIKE",
                        # El código del bloque competidor ES el style_color Nike:
                        # así la fila se consolida en el MISMO producto que el
                        # bloque Nike de referencia (misma clave natural).
                        "code": other["style_color"],
                        "name": other["marketing_name"],
                        "franchise": other["franchise"],
                        "division": other["division"],
                        "category": other["category"],
                        "silueta": other["silueta"],
                        "gender": other["gender"],
                        "msrp": other["msrp"],
                    }

            scraper = rng.choice((canal, scraper_a, scraper_b))
            canal_value: Any = canal
            marca: Any = (rng.choice(nike_casings) if rival["brand"] == "NIKE"
                          else rng.choice(BRAND_CASINGS[rival["brand"]]))

            if rng.random() < dirt.foreign_country:      # fila de otro país
                scraper = f"{canal.replace(' ', '')}_CL"
                canal_value = f"{canal}_CL"
            elif rng.random() < dirt.brand_site:         # captura del sitio de marca
                site = rng.choice(("nike_ar_general", "adidas_7", "puma_ar"))
                scraper, canal_value = site, site

            drift = 1.0 + 0.018 * d_idx + rng.uniform(-0.01, 0.01)
            c_full, c_final, c_cuotas = _price_pair(rng, rival["msrp"], dirt, drift)
            n_full, n_final, n_cuotas = _price_pair(rng, nike["msrp"], dirt, drift)
            n_sizes_text, n_sizes_available = _sizes(rng, nike["division"], dirt)
            c_sizes_text, c_sizes_available = _sizes(rng, rival["division"], dirt)

            fecha_value: Any = fecha.isoformat()
            if rng.random() < dirt.missing_date:
                fecha_value = None

            yield {
                "fecha_corrida": fecha_value,
                "scraper": scraper,
                "canal": canal_value,
                "marca": marca,
                "season": _maybe_missing(rng, nike["season"], dirt),
                # El mismo style_color se repite en cada retailer y cada fecha:
                # es el caso que duplicaba productos.
                "style_color": nike["style_color"],
                "product_code_competitor": nike["style_color"].split("-")[0],
                "marketing_name": nike["marketing_name"],
                "division": nike["division"],
                "category": nike["category"],
                "franchise_scrapper": _maybe_missing(rng, nike["franchise"], dirt),
                "gender": nike["gender"],
                "productcode_competitor": rival["code"],
                "product_name_competitor": rival["name"],
                "category_competitor": _maybe_missing(rng, rival["category"], dirt),
                "division_competitor": rival["division"],
                "franchise_competitor": _maybe_missing(rng, rival["franchise"], dirt),
                "gender_competitor": _maybe_missing(rng, rival["gender"], dirt),
                "size_available_competitor": c_sizes_available,
                "size_available_nike": n_sizes_available,
                "link_pdp_competitor": _maybe_missing(
                    rng, f"https://{canal.lower().replace(' ', '')}.com.ar/p/{rival['code']}", dirt),
                "competitor_full_price": c_full,
                "competitor_final_price": c_final,
                "cuotas_competitor": c_cuotas,
                "nike_full_price": n_full,
                "nike_final_price": n_final,
                "cuotas_nike": n_cuotas,
                "text_sizes_nike": n_sizes_text,
                "text_sizes_competitor": c_sizes_text,
                "pdp_nike": f"https://www.nike.com.ar/p/{nike['style_color']}",
                "precio_sugerido": _maybe_missing(rng, nike["msrp"], dirt),
                "silueta": _maybe_missing(rng, rival["silueta"], dirt),
            }


# ============================================================
# Salidas
# ============================================================

def write_csv(path: str | Path, rows: Iterator[dict[str, Any]]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in COLUMNS})
            n += 1
    return n


def _copy_field(value: Any) -> str:
    """Un valor en el formato TEXT de ``COPY``: ``\\N`` es NULL, y se escapan
    backslash / tab / newline (que es justo lo que los datos sucios traen)."""
    if value is None or value == "":
        return r"\N"
    return (str(value).replace("\\", r"\\").replace("\t", r"\t")
            .replace("\n", r"\n").replace("\r", r"\r"))


def load_postgres(dsn: str, rows: Iterator[dict[str, Any]], *, batch: int = 10_000) -> int:
    """Crea `pricing_data` y la puebla con COPY (rápido incluso con 70k filas).

    Ojo: las columnas numéricas son ``NUMERIC``, así que el ``0`` y los precios
    inflados por cuotas viajan como números; los "nulos disfrazados" (``'N/A'``,
    ``'nan'``) sólo pueden ir en las columnas TEXT — igual que en la tabla real.
    """
    import psycopg2  # noqa: PLC0415 - dependencia opcional

    numeric = {"competitor_full_price", "competitor_final_price", "nike_full_price",
               "nike_final_price", "precio_sugerido", "size_available_competitor",
               "size_available_nike"}

    total = 0
    connection = psycopg2.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(DDL)
            connection.commit()

            def flush(lines: list[str]) -> None:
                if not lines:
                    return
                buffer = io.StringIO("".join(lines))
                cursor.copy_from(buffer, "pricing_data", columns=COLUMNS, null=r"\N")

            pending: list[str] = []
            for row in rows:
                fields = []
                for column in COLUMNS:
                    value = row.get(column)
                    if column in numeric and isinstance(value, str):
                        value = None            # 'N/A' no entra en un NUMERIC
                    fields.append(_copy_field(value))
                pending.append("\t".join(fields) + "\n")
                total += 1
                if len(pending) >= batch:
                    flush(pending)
                    pending = []
            flush(pending)
            connection.commit()
            cursor.execute("ANALYZE pricing_data")
            connection.commit()
    finally:
        connection.close()
    return total


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/generate_scale_fixture.py",
        description="Genera un pricing_data sintético a escala real (mismo formato y misma suciedad)",
    )
    destino = parser.add_mutually_exclusive_group(required=True)
    destino.add_argument("--dsn", help="Postgres destino: crea `pricing_data` y la puebla")
    destino.add_argument("--csv", help="Ruta del CSV destino")
    parser.add_argument("--rows", type=int, default=70_000, help="Filas a generar (default 70000)")
    parser.add_argument("--products", type=int, default=1_000,
                        help="Productos distintos, 40%% Nike / 60%% competencia (default 1000)")
    parser.add_argument("--retailers", type=int, default=10, help="Retailers (default 10, máx 10)")
    parser.add_argument("--dates", type=int, default=5,
                        help="Fechas de captura semanales (default 5)")
    parser.add_argument("--seed", type=int, default=20260816, help="Semilla (determinístico)")
    parser.add_argument("--dirt", type=float, default=1.0,
                        help="Multiplicador de suciedad: 0 = limpio, 1 = como los datos reales")
    parser.add_argument("--end-date", help="Fecha de la última captura (YYYY-MM-DD)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    end = date.fromisoformat(args.end_date) if args.end_date else None

    def rows() -> Iterator[dict[str, Any]]:
        return generate_rows(rows=args.rows, products=args.products,
                             retailers=args.retailers, dates=args.dates,
                             seed=args.seed, dirt_level=args.dirt, end_date=end)

    if args.csv:
        total = write_csv(args.csv, rows())
        print(f"{total:,} filas -> {args.csv}")
    else:
        total = load_postgres(args.dsn, rows())
        print(f"{total:,} filas -> pricing_data en {args.dsn.split('@')[-1]}")

    n_nike = max(1, int(args.products * 0.4))
    print(f"  productos en el catálogo : {n_nike:,} Nike + {args.products - n_nike:,} competencia")
    print(f"  retailers / fechas        : {min(args.retailers, len(RETAILERS))} / {args.dates}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
