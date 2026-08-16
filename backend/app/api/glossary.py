"""Glosario de factores y drivers: qué mide cada variable del motor.

Fuente de verdad única para las tres familias de variables que el producto
publica con su contribución al score:

  * ``competitive_match``    — los 7 factores del match competitivo
  * ``business_importance``  — los 11 componentes de la importancia de negocio
  * ``retail_media``         — los 7 factores de la oportunidad de retail media

La explicabilidad es la razón de ser del producto: mostrar "editorial: 12% de
contribución" sin decir qué mide `editorial` no explica nada. Cada término trae
tres cosas, en castellano de negocio:

    definition  qué mide
    data        con qué datos se calcula
    reading     cómo leer un valor alto y uno bajo

Este módulo es la ÚNICA definición. De acá salen:
  * la API (``glossary_payload``), que lo adjunta a ``GET /api/matches/{id}``,
    ``GET /api/products/{id}/matches`` y ``GET /api/retail-media``;
  * ``backend/docs/glossary.md``, que se GENERA con ``python -m app.api.glossary``.

Así docs, API y UI no se pueden desincronizar: si alguien agrega un peso en
``weights.yaml`` sin definirlo acá, ``tests/test_glossary.py`` falla.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR, weights

#: Ruta del documento generado.
DOC_PATH = BACKEND_DIR / "docs" / "glossary.md"

#: Título y bajada de cada familia de variables.
GROUP_META: dict[str, dict[str, str]] = {
    "competitive_match": {
        "label": "Factores del match competitivo",
        "description": (
            "Cuánto compiten de verdad dos productos. El score 0..100 es la suma "
            "ponderada de estos 7 factores; los que no tienen datos se excluyen y su "
            "peso se reparte entre el resto (por eso la cobertura se publica aparte)."
        ),
    },
    "business_importance": {
        "label": "Componentes de la importancia de negocio",
        "description": (
            "Separa 'hay una diferencia' de 'esta diferencia IMPORTA'. Estos 11 "
            "componentes ponderan cuánta plata y cuánta marca hay en juego detrás de "
            "un caso, y un gate de relevancia competitiva apaga lo que no tiene rival real."
        ),
    },
    "retail_media": {
        "label": "Factores de la oportunidad de retail media",
        "description": (
            "Cuándo conviene invertir en visibilidad en vez de descuento, por cuadro "
            "(producto Nike × retailer). Las señales del set competidor se combinan "
            "ponderando por relevancia, salvo el precio, que va a peor caso."
        ),
    },
}

#: Definición de cada término: label, qué mide, con qué datos, alto y bajo.
TERMS: dict[str, dict[str, dict[str, str]]] = {
    # ── 7 factores del match competitivo ────────────────────
    "competitive_match": {
        "visual": {
            "label": "Visual",
            "definition": "Qué tan parecidos se ven los dos productos en la foto y en sus atributos de diseño.",
            "data": "Embedding de imagen (CLIP local, si hay fotos) más silueta, colores y materiales del enriquecimiento. Si sólo hay evidencia por debajo de `min_evidence_weight`, el factor se marca sin datos en vez de inventar un 0.",
            "high": "Alto = el shopper los ve como el mismo tipo de zapatilla; se confunden en la góndola.",
            "low": "Bajo = siluetas o materiales distintos, aunque compartan uso.",
        },
        "semantic": {
            "label": "Semántico",
            "definition": "Qué tan parecido es el PROPÓSITO declarado de los dos productos.",
            "data": "Taxonomía ponderada (use case, categoría deportiva, división, performance vs lifestyle, género, subcategoría) más similitud de texto de las descripciones con embeddings locales o TF-IDF. Si no comparten género o categoría se aplica una penalización dura.",
            "high": "Alto = resuelven la misma necesidad para el mismo consumidor (dos daily trainers de running de hombre).",
            "low": "Bajo = distinto uso o distinto público; comparables sólo de nombre.",
        },
        "price": {
            "label": "Precio",
            "definition": "Qué tan cerca están en posicionamiento de precio, no en promoción puntual.",
            "data": "MSRP, precio vigente promedio y banda de precio (entry/mid/premium/super-premium), con una tolerancia de gap declarada en config.",
            "high": "Alto = juegan en el mismo bolsillo; el consumidor los evalúa como alternativas reales.",
            "low": "Bajo = están en ligas de precio distintas y rara vez se comparan al momento de comprar.",
        },
        "retailer_overlap": {
            "label": "Solape de retailers",
            "definition": "Qué proporción de retailers venden ambos productos.",
            "data": "Jaccard sobre los canales donde cada uno fue observado (precio o stock): compartidos sobre la unión.",
            "high": "Alto = compiten en la misma góndola y el consumidor los ve juntos.",
            "low": "Bajo = casi no coinciden en el mismo canal; la competencia es de categoría, no de góndola.",
        },
        "editorial": {
            "label": "Editorial",
            "definition": "Cuántas veces el mercado —medios, guías de compra, reviews— los trata como alternativas uno del otro.",
            "data": "Menciones que enfrentan o listan juntos al par, puntuadas por tipo (versus > alternativa > misma lista > ranking > review), saturadas con una exponencial y descontadas por antigüedad.",
            "high": "Alto = rivalidad documentada afuera: alguien ya escribió 'X o Y'.",
            "low": "Bajo = nadie los compara públicamente; la rivalidad es una hipótesis nuestra.",
        },
        "social": {
            "label": "Social",
            "definition": "Cuánto aparecen juntos en la conversación pública.",
            "data": "Co-menciones agregadas por período (nunca posteos individuales), saturadas y descontadas por antigüedad, con un mínimo de co-menciones para que la señal cuente.",
            "high": "Alto = el consumidor los nombra en la misma frase: son sustitutos en su cabeza.",
            "low": "Bajo = no conviven en la conversación.",
        },
        "reviews": {
            "label": "Reviews",
            "definition": "Si los consumidores valoran los mismos atributos y con qué nivel de satisfacción.",
            "data": "Jaccard de los atributos que aparecen en las reviews de cada producto más la cercanía de rating promedio. Requiere un mínimo de reviews en ambos lados.",
            "high": "Alto = se elogian y se critican por lo mismo (amortiguación, calce, durabilidad) con satisfacción parecida.",
            "low": "Bajo = el consumidor valora cosas distintas en cada uno, o uno rinde claramente distinto.",
        },
    },
    # ── 11 componentes de business importance ───────────────
    "business_importance": {
        "competitive_relevance": {
            "label": "Relevancia competitiva",
            "definition": "Cuán real es el competidor del caso.",
            "data": "Match score del competidor principal, llevado a 0..1. Además actúa como gate: por debajo del piso de relevancia atenúa toda la importancia.",
            "high": "Alto = el rival es un comparable de verdad; lo que pase con él afecta la venta.",
            "low": "Bajo = diferencia contra alguien que el consumidor no considera alternativa: importa poco.",
        },
        "franchise_importance": {
            "label": "Peso de la franquicia",
            "definition": "Cuánto pesa la franquicia Nike involucrada en la estrategia de la marca.",
            "data": "Mapa declarado en config (`business_importance.franchise_importance`): Pegasus, Air Force 1 y Jordan arriba; las no listadas usan el default.",
            "high": "Alto = franquicia insignia; un problema acá se ve en el negocio y en la marca.",
            "low": "Bajo = franquicia secundaria o sin clasificar.",
        },
        "revenue_proxy": {
            "label": "Proxy de facturación",
            "definition": "Cuánta plata hay detrás del producto, sin tener datos de venta.",
            "data": "Precio promedio × cantidad de retailers donde está × disponibilidad observada, normalizado contra el máximo del corpus (sin constantes mágicas).",
            "high": "Alto = producto caro y muy distribuido: mueve la aguja.",
            "low": "Bajo = producto barato, con poca distribución o casi sin stock.",
        },
        "retailer_importance": {
            "label": "Importancia del retailer",
            "definition": "Cuánto pesan estratégicamente los canales involucrados en el caso.",
            "data": "Promedio de `retailers.importance` (0..1) de los retailers del caso.",
            "high": "Alto = pasa en cuentas clave, donde una decisión tiene consecuencias comerciales.",
            "low": "Bajo = canales marginales.",
        },
        "market_coverage": {
            "label": "Cobertura de mercado",
            "definition": "En qué proporción de los retailers del país está presente el producto.",
            "data": "Retailers donde fue observado sobre el total de retailers cargados.",
            "high": "Alto = está en casi toda la plaza: lo que pase se replica en todos lados.",
            "low": "Bajo = presencia acotada; el impacto queda contenido.",
        },
        "price_gap": {
            "label": "Gap de precio",
            "definition": "Magnitud de la diferencia de precio contra el competidor, sin importar el signo.",
            "data": "Valor absoluto del gap porcentual contra el competidor del caso, dividido por 100.",
            "high": "Alto = la brecha de precio es grande; hay algo que explicar o que corregir.",
            "low": "Bajo = precios prácticamente iguales: el precio no es la palanca.",
        },
        "review_volume": {
            "label": "Volumen de reviews",
            "definition": "Cuánta evidencia de consumidor real existe sobre el producto.",
            "data": "Cantidad total de reviews observadas, normalizada contra el máximo del corpus.",
            "high": "Alto = producto con tracción y mucha voz del consumidor.",
            "low": "Bajo = producto nuevo o de nicho; hay poco que leer.",
        },
        "social_momentum": {
            "label": "Momentum social",
            "definition": "Si la conversación sobre el producto está creciendo.",
            "data": "Señal de momentum 0..1 de `market_signals`; si no está, se deriva comparando las menciones del último período contra el anterior.",
            "high": "Alto = está caliente; la demanda tiende a acompañar.",
            "low": "Bajo = conversación estable o en baja.",
        },
        "editorial_momentum": {
            "label": "Momentum editorial",
            "definition": "Cuánta presencia reciente tiene el producto en medios y guías.",
            "data": "Menciones editoriales del producto descontadas por antigüedad, normalizadas contra el máximo del corpus.",
            "high": "Alto = los medios lo están empujando; hay tracción prestada.",
            "low": "Bajo = nadie lo está cubriendo.",
        },
        "share_of_shelf": {
            "label": "Share of shelf",
            "definition": "Cuánta presión competitiva hay en el segmento por falta de presencia Nike. Está INVERTIDO a propósito.",
            "data": "1 − (SKUs Nike / SKUs totales) del segmento del producto.",
            "high": "Alto = Nike ocupa poca góndola frente a los competidores: hay presión y hay lugar para ganar.",
            "low": "Bajo = Nike ya domina el segmento; el caso importa menos.",
        },
        "promo_intensity": {
            "label": "Intensidad promocional",
            "definition": "Cuán descontado está el producto Nike.",
            "data": "Descuento porcentual promedio observado en los retailers, dividido por 100.",
            "high": "Alto = ya se está resignando margen: cualquier decisión adicional se toma sobre un producto castigado.",
            "low": "Bajo = producto a precio pleno.",
        },
    },
    # ── 7 factores de retail media ──────────────────────────
    "retail_media": {
        "nike_stock_health": {
            "label": "Salud de stock Nike",
            "definition": "Si hay inventario para sostener el tráfico que la inversión en visibilidad va a generar.",
            "data": "Disponibilidad observada del producto Nike en ese retailer (talles disponibles sobre el total); si no hay dato del retailer, el promedio de sus canales.",
            "high": "Alto = hay con qué responder: pautar convierte.",
            "low": "Bajo = pautar manda tráfico a una ficha sin talles; primero se repone.",
        },
        "price_competitiveness": {
            "label": "Competitividad de precio",
            "definition": "Si Nike ya está en precio frente al set competidor del cuadro.",
            "data": "Gap de precio contra el PEOR CASO entre los comparables decisivos (los que superan el piso de match score), interpolado entre `price_competitive_pct` y `price_disadvantage_pct`.",
            "high": "Alto = ningún comparable relevante está claramente más barato: la visibilidad rinde más que un descuento.",
            "low": "Bajo = hay al menos un comparable visiblemente más barato en la misma góndola; pautar sobre eso quema inversión.",
        },
        "competitive_relevance": {
            "label": "Relevancia competitiva",
            "definition": "Cuán real es la competencia en ese cuadro.",
            "data": "Match score del competidor LÍDER (el más relevante del cuadro), llevado a 0..1. Se usa el máximo y no el promedio para no castigar haber documentado una cola larga de rivales flojos.",
            "high": "Alto = hay un comparable fuerte disputando la misma venta.",
            "low": "Bajo = nadie comparable enfrente; la visibilidad no se está defendiendo de nada.",
        },
        "business_importance": {
            "label": "Importancia de negocio",
            "definition": "Cuánto importa el producto Nike de este cuadro, más allá del caso puntual.",
            "data": "Score de business importance (0..100) calculado con el competidor líder y el gap combinado del cuadro. Ver la familia de componentes de importancia de negocio.",
            "high": "Alto = producto relevante: vale la pena pelear su visibilidad.",
            "low": "Bajo = producto marginal; hay mejores destinos para el presupuesto.",
        },
        "competitor_momentum": {
            "label": "Momentum del competidor",
            "definition": "Qué tan caliente está el set competidor del cuadro.",
            "data": "Momentum (social, editorial y reviews) del comparable MÁS acelerado entre los decisivos del cuadro — alcanza con que uno relevante despegue para que la categoría esté en movimiento. Si ninguno supera el piso de match score, promedio ponderado por relevancia.",
            "high": "Alto = hay un rival comparable ganando conversación: si Nike no aparece, la demanda se la llevan ellos.",
            "low": "Bajo = categoría tranquila; menos urgencia por comprar visibilidad.",
        },
        "shelf_gap": {
            "label": "Brecha de share of shelf",
            "definition": "Cuánta góndola le falta a Nike en el segmento. Está INVERTIDO a propósito.",
            "data": "1 − share of shelf Nike (SKUs Nike sobre SKUs totales del segmento).",
            "high": "Alto = Nike está poco expuesto frente a los competidores: el problema es exposición, y ahí la visibilidad paga.",
            "low": "Bajo = Nike ya ocupa la góndola; sumar media agrega poco.",
        },
        "competitor_stock_gap": {
            "label": "Quiebre del competidor",
            "definition": "Cuánto inventario le falta al set competidor, es decir cuánta demanda queda sin atender.",
            "data": "1 − disponibilidad del set, con la disponibilidad ponderada por relevancia. Se exige que la góndola esté corta como conjunto: un rival de cinco sin stock no es una ventana.",
            "high": "Alto = los rivales están quebrados y Nike disponible: ventana corta para capturar demanda sin resignar precio.",
            "low": "Bajo = todos con stock; hay que ganar la venta por otro lado.",
        },
    },
}


def group_terms(group: str) -> list[dict[str, Any]]:
    """Términos de una familia, en el orden de los pesos de config.

    El orden lo manda ``weights.yaml``: el glosario acompaña a la configuración
    vigente, no a una lista paralela. Un peso sin definición sale igual (con
    ``defined: False``) para que el hueco se vea en vez de desaparecer.
    """
    defined = TERMS.get(group, {})
    configured = weights(group, "weights")
    names = list(configured) + [n for n in defined if n not in configured]

    out: list[dict[str, Any]] = []
    for name in names:
        entry = defined.get(name)
        out.append({
            "name": name,
            "label": (entry or {}).get("label", name.replace("_", " ").capitalize()),
            "definition": (entry or {}).get("definition"),
            "data": (entry or {}).get("data"),
            "high": (entry or {}).get("high"),
            "low": (entry or {}).get("low"),
            "weight": configured.get(name),
            "defined": entry is not None,
        })
    return out


def glossary_payload(*groups: str) -> dict[str, Any]:
    """Payload publicable del glosario para las familias pedidas.

    Forma: ``{grupo: {label, description, terms: [{name, label, definition,
    data, high, low, weight}]}}``.
    """
    wanted = groups or tuple(GROUP_META)
    return {
        group: {
            "label": GROUP_META.get(group, {}).get("label", group),
            "description": GROUP_META.get(group, {}).get("description", ""),
            "terms": group_terms(group),
        }
        for group in wanted
    }


# ── documento generado ──────────────────────────────────────

_HEADER = """<!-- GENERADO POR `python -m app.api.glossary` — NO EDITAR A MANO. -->
<!-- La fuente de verdad es `backend/app/api/glossary.py`. -->

# Glosario de factores y drivers

Qué mide cada variable que el motor publica con su contribución al score.

Es la MISMA definición que devuelve la API (`glossary` en `GET /api/matches/{id}`,
`GET /api/products/{id}/matches` y `GET /api/retail-media`) y que la UI muestra en
el `InfoTip` de cada factor: si cambia acá, cambia en las tres puntas.

Regla de lectura común a las tres familias: **todos los factores viven en 0..1**
y su `contribution` es el porcentaje del score que aportaron. Un factor sin
datos no puntúa 0: se excluye y su peso se reparte entre los demás, por eso la
cobertura y la confianza se publican aparte.
"""


def render_markdown() -> str:
    """Genera el contenido completo de ``docs/glossary.md``."""
    parts = [_HEADER]
    for group, block in glossary_payload().items():
        parts.append(f"\n---\n\n## {block['label']}\n\n")
        parts.append(f"{block['description']}\n\n")
        parts.append(f"Clave de config: `{group}.weights`.\n")
        for term in block["terms"]:
            weight = term["weight"]
            peso = f" · peso {weight:g}" if isinstance(weight, (int, float)) else ""
            parts.append(f"\n### `{term['name']}` — {term['label']}{peso}\n\n")
            parts.append(f"**Qué mide.** {term['definition']}\n\n")
            parts.append(f"**Con qué datos.** {term['data']}\n\n")
            parts.append(f"**Cómo leerlo.** {term['high']} {term['low']}\n")
    return "".join(parts)


def write_doc(path: Path | str = DOC_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera backend/docs/glossary.md")
    parser.add_argument("--check", action="store_true",
                        help="No escribe: falla si el documento está desactualizado")
    args = parser.parse_args(argv)

    expected = render_markdown()
    if args.check:
        current = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if current != expected:
            print("docs/glossary.md desactualizado: correr `python -m app.api.glossary`")
            return 1
        print("docs/glossary.md al día")
        return 0

    print(f"escrito {write_doc()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
