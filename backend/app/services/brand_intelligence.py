"""Consumer & Brand Intelligence — Argentina.

Consume ``social_mention_aggregates``, ``reviews`` y ``editorial_mentions``
filtrados por país (``brand_intelligence.country``) y produce:

  * **market_signals**  — ``social_momentum`` / ``editorial_momentum`` /
    ``review_momentum`` por marca, producto y franquicia (los consume el
    opportunity engine).
  * **brand_insights**  — insights accionables en español, SIEMPRE generados
    desde plantillas parametrizadas por métricas reales.
  * **tendencias**      — aceleración, picos, tópicos emergentes, deterioro de
    sentimiento, competidor emergente y momentum de franquicia.
  * **puente Brand ↔ Product Intelligence** — ``social_competition_signals()``
    devuelve pares producto↔co_producto con intensidad normalizada 0..1 para
    que el matching engine suba su ``social_competition_score``.

Reglas duras que respeta este módulo
------------------------------------
1. **Nada de LLMs.** La clasificación de `reviews.review_text` (español
   rioplatense) usa un léxico determinístico (`TAXONOMY_LEXICON`) mapeado a la
   taxonomía de ``config/weights.yaml``.
2. **Cero pesos hardcodeados.** Ventanas, pesos de momentum, ``spike_threshold``
   y umbrales de confianza salen de ``brand_intelligence`` en weights.yaml.
3. **Prohibido inventar conclusiones.** Todo insight nace de una plantilla
   parametrizada con métricas calculadas; ``_emit()`` es el único constructor de
   insights y descarta el insight si (a) el volumen de señal está por debajo del
   mínimo configurado o (b) no hay evidencia textual pública real
   (``sample_evidence`` / ``review_text`` / ``excerpt`` de la DB).
4. **Agregados sociales siempre agregados**: nunca se identifica a un individuo;
   sólo se citan los ejemplos públicos ya agregados en la DB.
5. **Determinismo**: la fecha "hoy" se deriva del MÁXIMO ``period_end``
   observado en los datos, nunca de ``date.today()``.
6. **Trazabilidad**: cada ejemplo de evidencia declara ``{text, type, source,
   url, date}``. Si NO hay URL, se publica ``url: null`` + ``url_reason``
   (motivo verificable), nunca se omite la evidencia: el usuario tiene que
   poder distinguir "no hay link" de "no hay evidencia".
7. **Ventana de comparación configurable**: ``build_context(window=...)``
   acepta ``month`` (default, = comportamiento histórico), ``quarter``,
   ``year`` o ``window_days``/``compare_days`` explícitos. Si el histórico
   disponible no alcanza para la ventana pedida, el contexto lo declara
   (``window_info()["available"] is False`` + ``reason``) en vez de publicar
   una variación contra una ventana vacía.

Todo score compuesto pasa por ``common.combine`` (explicabilidad uniforme).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import DB_PATH, section
from app.db import get_conn, query
from app.services.common import (
    Factor,
    clamp,
    combine,
    from_json,
    parse_date,
    to_json,
)

# Cantidad máxima de ejemplos textuales citados por insight (1..3 según el brief).
MAX_EVIDENCE_EXAMPLES = 3
# Señales de mercado que produce este módulo (las demás filas de market_signals
# pertenecen a otros servicios y no se tocan).
SIGNAL_TYPES = ("social_momentum", "editorial_momentum", "review_momentum")


# ── ventanas de comparación ─────────────────────────────────
#
# El default sigue siendo el histórico: `current_window_days` /
# `previous_window_days` de `brand_intelligence` en weights.yaml (30/30). Los
# presets nuevos se leen de `brand_intelligence.comparison_windows.<preset>`
# con `section(..., default=...)`; si esa clave no existe en weights.yaml (hoy
# no existe) se usan los fallbacks declarados acá, en un único lugar.

DEFAULT_WINDOW = "month"

#: Presets y su fallback ``(días de la ventana actual, días de comparación)``.
WINDOW_FALLBACK_DAYS: dict[str, tuple[int, int]] = {
    "month": (30, 30),
    "quarter": (90, 90),
    "year": (365, 365),
}

WINDOW_LABELS: dict[str, str] = {
    "month": "mes anterior",
    "quarter": "trimestre anterior",
    "year": "año anterior",
    "custom": "ventana a medida",
}


@dataclass(frozen=True)
class WindowSpec:
    """Ventana actual + ventana contra la que se compara, en días."""

    name: str
    label: str
    current_days: int
    compare_days: int

    @property
    def required_days(self) -> int:
        """Histórico mínimo para que la comparación sea medible."""
        return self.current_days + self.compare_days

    @property
    def acceleration_days(self) -> int:
        """Histórico mínimo para poder calcular la derivada segunda."""
        return self.current_days + 2 * self.compare_days

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": self.name,
            "label": self.label,
            "window_days": self.current_days,
            "compare_days": self.compare_days,
            "required_days": self.required_days,
        }


def _configured_window_days(name: str) -> tuple[int, int]:
    """Días de un preset, leídos de weights.yaml con fallback declarado."""
    if name == "month":
        # Claves históricas: cambiarlas cambia el default de todo el módulo.
        current = int(section("brand_intelligence", "current_window_days", default=30) or 30)
        compare = int(section("brand_intelligence", "previous_window_days", default=30) or 30)
        return current, compare
    fallback_current, fallback_compare = WINDOW_FALLBACK_DAYS[name]
    current = int(section("brand_intelligence", "comparison_windows", name, "window_days",
                          default=fallback_current) or fallback_current)
    compare = int(section("brand_intelligence", "comparison_windows", name, "compare_days",
                          default=fallback_compare) or fallback_compare)
    return current, compare


def resolve_window(window: str | None = None, *, window_days: int | None = None,
                   compare_days: int | None = None) -> WindowSpec:
    """Traduce ``window`` / ``window_days`` + ``compare_days`` a un :class:`WindowSpec`.

    * ``window=None`` ⇒ ``month`` ⇒ exactamente el comportamiento histórico.
    * ``window_days``/``compare_days`` explícitos ganan sobre el preset y
      producen una ventana ``custom``; si sólo se pasa uno, el otro lo iguala.
    * Un preset o un número inválido levanta ``ValueError``: preferimos el 400
      antes que calcular contra una ventana que el usuario no pidió.
    """
    if window_days is not None or compare_days is not None:
        current = int(window_days) if window_days is not None else int(compare_days)  # type: ignore[arg-type]
        compare = int(compare_days) if compare_days is not None else current
        if current < 1 or compare < 1:
            raise ValueError("window_days y compare_days tienen que ser >= 1")
        return WindowSpec("custom", WINDOW_LABELS["custom"], current, compare)

    name = (window or DEFAULT_WINDOW).strip().lower()
    if name not in WINDOW_FALLBACK_DAYS:
        raise ValueError(
            f"ventana desconocida: {window!r}. Opciones: "
            f"{', '.join(sorted(WINDOW_FALLBACK_DAYS))} "
            f"(o window_days=N y compare_days=M).")
    current, compare = _configured_window_days(name)
    return WindowSpec(name, WINDOW_LABELS[name], current, compare)


def window_info(spec: WindowSpec, *, data_start: date | None,
                data_end: date | None) -> dict[str, Any]:
    """Ventana pedida + si el histórico disponible alcanza para sostenerla.

    Cuando ``available`` es ``False`` la ventana de comparación cae fuera del
    histórico: los volúmenes del período actual siguen siendo hechos observados,
    pero no hay contra qué compararlos y toda variación sale ``null``. Nunca se
    rellena con un número inventado.
    """
    history = (data_end - data_start).days if (data_start and data_end) else None
    cur_end = data_end
    cur_start = (cur_end - timedelta(days=spec.current_days)) if cur_end else None
    prev_start = (cur_end - timedelta(days=spec.required_days)) if cur_end else None
    prior_start = (cur_end - timedelta(days=spec.acceleration_days)) if cur_end else None
    comparison = history is not None and history >= spec.required_days
    acceleration = history is not None and history >= spec.acceleration_days

    info: dict[str, Any] = {
        **spec.as_dict(),
        "current": [cur_start.isoformat() if cur_start else None,
                    cur_end.isoformat() if cur_end else None],
        "previous": [prev_start.isoformat() if prev_start else None,
                     cur_start.isoformat() if cur_start else None],
        "prior": [prior_start.isoformat() if prior_start else None,
                  prev_start.isoformat() if prev_start else None],
        "data_start": data_start.isoformat() if data_start else None,
        "data_end": data_end.isoformat() if data_end else None,
        "history_days": history,
        "available": comparison,
        "comparison_available": comparison,
        "acceleration_available": acceleration,
        "reason": None,
    }
    if history is None:
        info["reason"] = ("No hay ninguna observación en la base para este país: "
                          "sin datos no hay ventana que calcular.")
    elif not comparison:
        info["reason"] = (
            f"El histórico disponible cubre {history} días "
            f"({info['data_start']} → {info['data_end']}) y la comparación contra el "
            f"{spec.label} necesita {spec.required_days}. La ventana anterior queda "
            f"fuera de los datos, así que las variaciones no se publican (salen null) "
            f"en vez de compararse contra cero.")
    elif not acceleration:
        info["reason"] = (
            f"Alcanza para comparar contra el {spec.label} ({spec.required_days} días "
            f"sobre {history} disponibles) pero no para la aceleración, que necesita "
            f"una tercera ventana ({spec.acceleration_days} días).")
    return info


def observation_bounds(db_path: Path | str = DB_PATH,
                       country: str | None = None) -> tuple[date | None, date | None]:
    """``(primera, última)`` observación del país, con SQL de agregación barato.

    Permite responder "¿alcanza el histórico para esta ventana?" sin cargar
    todas las observaciones en memoria (que es lo que hace ``build_context``).
    """
    code = str(country or section("brand_intelligence", "country", default="AR") or "AR")
    queries = (
        ("SELECT MIN(period_end) AS lo, MAX(period_end) AS hi "
         "FROM social_mention_aggregates WHERE country_code = ?", (code,)),
        ("SELECT MIN(published_at) AS lo, MAX(published_at) AS hi "
         "FROM editorial_mentions WHERE country_code = ?", (code,)),
        ("SELECT MIN(r.observed_at) AS lo, MAX(r.observed_at) AS hi FROM reviews r "
         "JOIN products p ON p.id = r.product_id "
         "LEFT JOIN retailers rt ON rt.id = r.retailer_id "
         "WHERE p.country_code = ? OR rt.country_code = ?", (code, code)),
    )
    moments: list[date] = []
    for sql, params in queries:
        try:
            rows = query(sql, params, path=db_path)
        except Exception:  # noqa: BLE001 - tabla ausente: se ignora esa fuente
            continue
        for row in rows:
            for key in ("lo", "hi"):
                moment = parse_date(row.get(key))
                if moment:
                    moments.append(moment)
    if not moments:
        return None, None
    return min(moments), max(moments)


def window_report(db_path: Path | str = DB_PATH, *, country: str | None = None,
                  window: str | None = None, window_days: int | None = None,
                  compare_days: int | None = None) -> dict[str, Any]:
    """Sobre de ventana sin cargar el análisis completo (lo usa la API)."""
    spec = resolve_window(window, window_days=window_days, compare_days=compare_days)
    data_start, data_end = observation_bounds(db_path, country)
    return window_info(spec, data_start=data_start, data_end=data_end)


# ── léxico determinístico (español rioplatense) ─────────────
# dimensión -> tópico -> términos. Los tópicos que no existan en la taxonomía
# configurada se ignoran: weights.yaml sigue siendo la fuente de verdad.
TAXONOMY_LEXICON: dict[str, dict[str, tuple[str, ...]]] = {
    "brand_perception": {
        "premium": ("premium", "alta gama", "gama alta", "top de linea", "de lujo"),
        "aspirational": ("aspiracional", "las sonaba", "soñadas", "siempre las quise",
                         "de mis sueños", "un sueño"),
        "innovative": ("innovador", "innovadora", "innovacion", "ultima tecnologia",
                       "revolucionaria", "vanguardia"),
        "fashionable": ("a la moda", "fashion", "canchera", "cancheras", "estilosa",
                        "estilosas", "combinan con todo", "quedan re bien"),
        "performance": ("rendimiento", "performance", "para competir", "para entrenar",
                        "alto rendimiento"),
        "accessible": ("accesible", "al alcance", "precio accesible"),
        "expensive": ("marca cara", "nike es caro", "adidas es caro", "la marca es cara",
                      "todo lo de la marca es caro"),
        "good_value": ("buena marca por el precio", "la marca cumple", "vale la marca"),
        "exclusive": ("exclusivo", "exclusiva", "edicion limitada", "limitada",
                      "solo para pocos"),
        "mainstream": ("todo el mundo tiene", "las tiene todo el mundo", "muy comun",
                       "masivo", "las usa todo el mundo"),
    },
    "product_perception": {
        "comfort": ("comodo", "comoda", "comodidad", "comodisimo", "comodisima",
                    "incomodo", "incomoda", "mullido", "blandita", "como una nube"),
        "quality": ("calidad", "mala calidad", "buena calidad", "terminacion",
                    "terminaciones", "acabado", "materiales", "material"),
        "design": ("diseño", "estetica", "lindas", "lindisimas", "hermosas", "feas",
                   "pinta", "colorway", "colores lindos", "que lindas"),
        "durability": ("durabilidad", "duraron", "duran", "se rompio", "se despego",
                       "resistente", "aguanta", "desgaste", "gastadas"),
        "technology": ("amortiguacion", "zoomx", "react", "placa de carbono", "espuma",
                       "drop", "tecnologia", "retorno de energia", "boost"),
        "fit": ("horma", "calza", "calzan", "ajuste", "talle grande", "talle chico",
                "vienen grandes", "vienen chicas", "me quedan justas", "angosta",
                "angostas", "ancha", "anchas"),
        "performance": ("rinden", "responden", "livianas", "liviana", "veloces",
                        "para ritmos", "me volaron", "rapidas"),
    },
    "price_perception": {
        "expensive": ("caro", "cara", "carisimo", "carisima", "carisimas", "re caro",
                      "un fangote", "impagable", "precio alto", "una fortuna",
                      "precio excesivo"),
        "fair_price": ("precio justo", "precio razonable", "bien de precio",
                       "precio correcto"),
        "overpriced": ("no vale lo que cuesta", "sobrevalorado", "sobrevaloradas",
                       "no justifica el precio", "carisimas para lo que son"),
        "discounts": ("descuento", "oferta", "liquidacion", "rebaja", "promo",
                      "cuotas", "hot sale", "black friday", "outlet"),
        "value_for_money": ("relacion precio calidad", "vale lo que cuesta",
                            "vale cada peso", "valen la pena", "buena inversion"),
    },
    "availability": {
        "difficult_to_find": ("dificil de conseguir", "no se consigue", "imposible de conseguir",
                              "no lo consigo", "no las consigo", "hay que buscar mucho",
                              "cuesta conseguirlas"),
        "stock_problems": ("sin stock", "no hay stock", "quiebre de stock", "agotado",
                           "agotadas", "no reponen", "reposicion"),
        "sizing_availability": ("no hay talles", "no habia talles", "sin talles",
                                "faltan talles", "no habia mi talle", "no quedaban talles",
                                "no tenian mi talle", "talles agotados",
                                "poca variedad de talles"),
        "product_variety": ("poca variedad", "pocos colores", "mas colores",
                            "variedad de colores", "no hay variedad", "un solo color",
                            "colorways limitados"),
    },
    "cultural_relevance": {
        "fashion": ("moda", "tendencia", "outfit", "street style", "combinan con el jean"),
        "music": ("recital", "trap", "rap", "festival", "musica"),
        "football": ("futbol", "cancha", "picadito", "botines", "seleccion", "mundial",
                     "futbol 5"),
        "running": ("running", "correr", "maraton", "media maraton", "10k", "21k", "42k",
                    "fondo largo", "entrenamiento de fondo", "carrera de calle"),
        "gym": ("gimnasio", "gym", "pesas", "crossfit", "funcional", "musculacion"),
        "streetwear": ("streetwear", "urbano", "urbanas", "para la calle", "street"),
        "sneaker_culture": ("sneaker", "sneakers", "sneakerhead", "coleccion", "retro",
                            "colaboracion", "colab", "drop"),
    },
    "consumer_intent": {
        "want_to_buy": ("me las quiero comprar", "las quiero", "voy a comprar",
                        "quiero comprarme", "las voy a comprar"),
        "considering": ("estoy viendo", "estoy pensando", "dudando", "tengo dudas",
                        "estoy evaluando", "no me decido"),
        "comparing": ("vs", "versus", "comparando", "contra las", "cual conviene",
                      "cual me conviene", "mejor que las"),
        "recommendation": ("recomiendo", "las recomiendo", "recomendables",
                           "recomendadisimas", "super recomendables"),
        "regret": ("me arrepenti", "mala compra", "no las compraria de nuevo",
                   "me arrepiento", "plata tirada"),
        "repurchase": ("las volveria a comprar", "segundo par", "tercer par",
                       "recompra", "otra vez las mismas", "mi segundo par"),
        "switch_brand": ("me cambio a", "me pase a", "cambie de marca", "me fui a",
                         "dejo la marca", "cambio de marca"),
    },
}

# Etiquetas legibles en español para el texto de los insights.
TOPIC_LABELS_ES: dict[str, str] = {
    "premium": "posicionamiento premium",
    "aspirational": "carga aspiracional",
    "innovative": "innovación",
    "fashionable": "moda y estilo",
    "performance": "performance",
    "accessible": "accesibilidad",
    "expensive": "percepción de precio alto",
    "good_value": "buena relación precio/valor",
    "exclusive": "exclusividad",
    "mainstream": "masividad",
    "comfort": "comodidad",
    "quality": "calidad",
    "design": "diseño",
    "durability": "durabilidad",
    "technology": "tecnología",
    "fit": "horma y calce",
    "fair_price": "precio justo",
    "overpriced": "sensación de precio excesivo",
    "discounts": "descuentos y promociones",
    "value_for_money": "relación precio/valor",
    "difficult_to_find": "dificultad para conseguir el producto",
    "stock_problems": "problemas de stock",
    "sizing_availability": "disponibilidad de talles",
    "product_variety": "variedad de colorways y modelos",
    "fashion": "moda",
    "music": "música",
    "football": "fútbol",
    "running": "running",
    "gym": "gimnasio",
    "streetwear": "streetwear",
    "sneaker_culture": "cultura sneaker",
    "want_to_buy": "intención de compra",
    "considering": "consideración",
    "comparing": "comparación entre modelos",
    "recommendation": "recomendación",
    "regret": "arrepentimiento de compra",
    "repurchase": "recompra",
    "switch_brand": "cambio de marca",
}

DIMENSION_LABELS_ES: dict[str, str] = {
    "brand_perception": "percepción de marca",
    "product_perception": "percepción de producto",
    "price_perception": "percepción de precio",
    "availability": "disponibilidad",
    "cultural_relevance": "relevancia cultural",
    "consumer_intent": "intención del consumidor",
}


# ── normalización de texto ──────────────────────────────────


def _norm(text: str | None) -> str:
    """Minúsculas, sin acentos ni puntuación. Base del matching léxico."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", str(text).lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    cleaned = re.sub(r"[^a-z0-9]+", " ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


def _term_pattern(term: str) -> re.Pattern[str]:
    """Regex de término normalizado, tolerante a plural simple (s / es)."""
    words = _norm(term).split()
    body = r"\s+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![a-z0-9]){body}(?:es|s)?(?![a-z0-9])")


_COMPILED_LEXICON: dict[tuple[str, str], tuple[re.Pattern[str], ...]] = {
    (dim, topic): tuple(_term_pattern(t) for t in terms)
    for dim, topics in TAXONOMY_LEXICON.items()
    for topic, terms in topics.items()
}


def classify_text(text: str | None, taxonomy: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Clasifica texto libre en pares ``(dimensión, tópico)`` de la taxonomía.

    Multi-etiqueta y determinístico: una review puede tocar comodidad, precio y
    disponibilidad a la vez. Sólo devuelve tópicos declarados en weights.yaml.
    """
    normalized = _norm(text)
    if not normalized:
        return []
    hits: list[tuple[str, str]] = []
    for dimension, topics in taxonomy.items():
        for topic in topics:
            patterns = _COMPILED_LEXICON.get((dimension, topic))
            if not patterns:
                continue
            if any(p.search(normalized) for p in patterns):
                hits.append((dimension, topic))
    return hits


def _label_topic(topic: str | None) -> str:
    if not topic:
        return "la conversación general"
    return TOPIC_LABELS_ES.get(topic, topic.replace("_", " "))


def _label_dimension(dimension: str | None) -> str:
    if not dimension:
        return "la marca"
    return DIMENSION_LABELS_ES.get(dimension, dimension.replace("_", " "))


def _pct(value: float | None) -> str:
    """Formato de variación porcentual con signo explícito."""
    if value is None:
        return "s/d"
    return f"{value:+.0f}%"


# ── acumulador por ventana ──────────────────────────────────


@dataclass
class Bucket:
    """Volumen, sentimiento y evidencia acumulados por ventana temporal."""

    current: float = 0.0
    previous: float = 0.0
    prior: float = 0.0
    _sent_num: dict[str, float] = field(default_factory=dict)
    _sent_den: dict[str, float] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)

    def add(self, entry: dict[str, Any]) -> None:
        window = entry.get("window")
        if window not in ("current", "previous", "prior"):
            return
        volume = float(entry.get("volume") or 0.0)
        setattr(self, window, getattr(self, window) + volume)
        self.sources.add(entry.get("source_label", entry.get("source", "")))
        sentiment = entry.get("sentiment")
        if sentiment is not None and volume > 0:
            self._sent_num[window] = self._sent_num.get(window, 0.0) + float(sentiment) * volume
            self._sent_den[window] = self._sent_den.get(window, 0.0) + volume
        if window == "current":
            for item in entry.get("evidence") or []:
                self.evidence.append(item)

    def sentiment(self, window: str = "current") -> float | None:
        den = self._sent_den.get(window, 0.0)
        if den <= 0:
            return None
        return self._sent_num[window] / den

    @property
    def total(self) -> float:
        return self.current + self.previous + self.prior


def _accumulate(entries: Iterable[dict[str, Any]],
                keyfn: Callable[[dict[str, Any]], Any]) -> dict[Any, Bucket]:
    out: dict[Any, Bucket] = {}
    for entry in entries:
        key = keyfn(entry)
        if key is None:
            continue
        out.setdefault(key, Bucket()).add(entry)
    return out


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def cohort_reference(cohort: Iterable[Bucket]) -> float:
    """Referencia de volumen de un cohorte: la MEDIANA de los que tienen señal.

    Antes se usaba el máximo, y eso garantizaba que la entidad más grande
    valiera siempre 1.0 (y con cohortes discretas y chicas —una nota editorial
    por producto— que TODAS valieran 1.0). La mediana es el centro de masa de
    la distribución: no la fija ningún dato en particular, es robusta a un
    outlier (la marca foco) y deja la escala con recorrido hacia arriba.
    Se ignoran las entidades sin volumen actual: son ceros, no "el cohorte".
    """
    return _median([b.current for b in cohort if b.current > 0])


# ── contexto ────────────────────────────────────────────────


@dataclass
class BrandContext:
    """Todo lo necesario para el análisis, precargado y ya clasificado."""

    db_path: Path | str
    country: str
    country_name: str
    today: date | None
    cur_start: date | None
    cur_end: date | None
    prev_start: date | None
    prior_start: date | None
    taxonomy: dict[str, list[str]]
    topic_dimensions: dict[str, list[str]]
    brands: dict[int, dict[str, Any]]
    focus_brand_id: int | None
    products: dict[int, dict[str, Any]]
    entries: list[dict[str, Any]]          # una fila por observación
    records: list[dict[str, Any]]          # entries explotados por (dimensión, tópico)
    pair_entries: list[dict[str, Any]]     # co-menciones producto ↔ co_producto
    momentum_weights: dict[str, float]
    spike_threshold: float
    volume_decade_ratio: float
    min_base_volume: float
    high_min_volume: int
    medium_min_volume: int
    min_volume_to_emit: int
    window: WindowSpec = field(
        default_factory=lambda: WindowSpec("month", WINDOW_LABELS["month"], 30, 30))
    data_start: date | None = None
    data_end: date | None = None
    editorial_policies: dict[str, dict[str, Any]] = field(default_factory=dict)
    social_policies: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── ventanas ──
    @property
    def period_start(self) -> str | None:
        return self.cur_start.isoformat() if self.cur_start else None

    @property
    def period_end(self) -> str | None:
        return self.cur_end.isoformat() if self.cur_end else None

    @property
    def history_days(self) -> int | None:
        """Días de histórico realmente observados en la base."""
        if self.data_start is None or self.data_end is None:
            return None
        return (self.data_end - self.data_start).days

    @property
    def comparison_available(self) -> bool:
        """¿El histórico cubre la ventana de comparación pedida?"""
        history = self.history_days
        return history is not None and history >= self.window.required_days

    @property
    def acceleration_available(self) -> bool:
        """¿Y la tercera ventana, la que hace falta para la aceleración?"""
        history = self.history_days
        return history is not None and history >= self.window.acceleration_days

    def window_info(self) -> dict[str, Any]:
        """Ventana pedida + si los datos alcanzan para sostenerla."""
        return window_info(self.window, data_start=self.data_start, data_end=self.data_end)

    @property
    def min_volume(self) -> int:
        """Volumen mínimo de señal para que un insight EXISTA.

        Es una regla anti-invención ("no se concluye nada sobre tres
        comentarios"), no una afirmación de precisión: por eso vive en su
        propia clave (``confidence.min_volume_to_emit``) y NO en el corte de
        confianza MEDIUM. Mientras estuvieron acoplados, subir el corte de
        MEDIUM para que la etiqueta discriminara borraba insights reales, y
        bajarlo para no perderlos dejaba las tres bandas en HIGH.
        """
        return self.min_volume_to_emit

    @property
    def has_data(self) -> bool:
        return self.today is not None and bool(self.entries)

    def window_of(self, when: Any) -> str | None:
        """Ventana a la que pertenece una fecha: current / previous / prior."""
        moment = parse_date(when)
        if moment is None or self.today is None:
            return None
        if self.cur_start < moment <= self.cur_end:
            return "current"
        if self.prev_start < moment <= self.cur_start:
            return "previous"
        if self.prior_start < moment <= self.prev_start:
            return "prior"
        return None

    # ── helpers de lectura ──
    def brand_name(self, brand_id: int | None) -> str:
        brand = self.brands.get(brand_id or -1)
        return str(brand["name"]) if brand else "marca desconocida"

    def product_name(self, product_id: int | None) -> str:
        product = self.products.get(product_id or -1)
        return str(product["product_name"]) if product else "producto desconocido"

    def confidence_for_volume(self, volume: float) -> str:
        """Confianza de una métrica agregada según su volumen de señal.

        Los cortes son de precisión estadística, no percentiles del dataset:
        el margen de error de una proporción al 95% es ~0.98/sqrt(n), así que
        n>=1000 => ±3 pp (HIGH) y n>=250 => ±6 pp (MEDIUM). Ver
        ``brand_intelligence.confidence`` en weights.yaml.
        """
        if volume >= self.high_min_volume:
            return "HIGH"
        if volume >= self.medium_min_volume:
            return "MEDIUM"
        return "LOW"


def build_context(db_path: Path | str = DB_PATH, *, window: str | None = None,
                  window_days: int | None = None,
                  compare_days: int | None = None) -> BrandContext:
    """Carga y normaliza todas las señales de consumidor del país configurado.

    ``window`` / ``window_days`` / ``compare_days`` eligen contra qué período se
    compara (ver :func:`resolve_window`). El default reproduce exactamente el
    comportamiento histórico (mes contra mes anterior).
    """
    spec = resolve_window(window, window_days=window_days, compare_days=compare_days)
    country = str(section("brand_intelligence", "country", default="AR") or "AR")
    taxonomy_raw = section("brand_intelligence", "taxonomy", default={}) or {}
    taxonomy = {dim: list(topics or []) for dim, topics in taxonomy_raw.items()}
    topic_dimensions: dict[str, list[str]] = {}
    for dimension, topics in taxonomy.items():
        for topic in topics:
            topic_dimensions.setdefault(topic, []).append(dimension)

    momentum_weights = {
        k: float(v) for k, v in
        (section("brand_intelligence", "momentum", "weights", default={}) or {}).items()
    }
    spike_threshold = float(section("brand_intelligence", "momentum", "spike_threshold",
                                    default=0.5) or 0.5)
    volume_decade_ratio = float(section("brand_intelligence", "momentum",
                                        "volume_decade_ratio", default=10.0) or 10.0)
    min_base_volume = float(section("brand_intelligence", "momentum", "min_base_volume",
                                    default=0.0) or 0.0)
    high_min_volume = int(section("brand_intelligence", "confidence", "high_min_volume",
                                  default=0) or 0)
    medium_min_volume = int(section("brand_intelligence", "confidence", "medium_min_volume",
                                    default=0) or 0)
    # El piso de emisión es independiente del corte de confianza MEDIUM: si no
    # está configurado se cae al comportamiento histórico (= MEDIUM).
    min_volume_to_emit = int(section("brand_intelligence", "confidence", "min_volume_to_emit",
                                     default=medium_min_volume) or medium_min_volume)
    current_days, previous_days = spec.current_days, spec.compare_days

    brands = {int(r["id"]): r for r in query("SELECT id, name, is_focus FROM brands",
                                             path=db_path)}
    focus_brand_id = next((bid for bid, b in sorted(brands.items()) if b.get("is_focus")), None)
    products = {int(r["id"]): r for r in query(
        "SELECT id, brand_id, product_name, franchise, category, subcategory, sport, "
        "use_case, country_code, url FROM products", path=db_path)}
    country_row = query("SELECT name FROM countries WHERE code = ?", (country,), path=db_path)
    country_name = str(country_row[0]["name"]) if country_row else country

    social_rows = query(
        "SELECT s.*, p.brand_id AS product_brand_id, p.franchise AS product_franchise "
        "FROM social_mention_aggregates s "
        "LEFT JOIN products p ON p.id = s.product_id "
        "WHERE s.country_code = ?", (country,), path=db_path)
    review_rows = query(
        "SELECT r.*, p.brand_id AS brand_id, p.franchise AS franchise, "
        "       p.product_name AS product_name, rt.name AS retailer_name "
        "FROM reviews r "
        "JOIN products p ON p.id = r.product_id "
        "LEFT JOIN retailers rt ON rt.id = r.retailer_id "
        "WHERE p.country_code = ? OR rt.country_code = ?", (country, country), path=db_path)
    editorial_rows = query(
        "SELECT * FROM editorial_mentions WHERE country_code = ?", (country,), path=db_path)

    # "Hoy" determinístico: máximo period_end observado en los datos.
    candidates: list[date] = []
    for row in social_rows:
        moment = parse_date(row.get("period_end"))
        if moment:
            candidates.append(moment)
    for row in review_rows:
        moment = parse_date(row.get("observed_at"))
        if moment:
            candidates.append(moment)
    for row in editorial_rows:
        moment = parse_date(row.get("published_at"))
        if moment:
            candidates.append(moment)
    today = max(candidates) if candidates else None
    # `data_start` es el histórico REAL disponible: con él se decide si la
    # ventana pedida tiene con qué compararse (ver `BrandContext.window_info`).
    data_start = min(candidates) if candidates else None

    ctx = BrandContext(
        db_path=db_path,
        country=country,
        country_name=country_name,
        today=today,
        cur_end=today,
        cur_start=(today - timedelta(days=current_days)) if today else None,
        prev_start=(today - timedelta(days=current_days + previous_days)) if today else None,
        prior_start=(today - timedelta(days=current_days + 2 * previous_days)) if today else None,
        taxonomy=taxonomy,
        topic_dimensions=topic_dimensions,
        brands=brands,
        focus_brand_id=focus_brand_id,
        products=products,
        entries=[],
        records=[],
        pair_entries=[],
        momentum_weights=momentum_weights,
        spike_threshold=spike_threshold,
        volume_decade_ratio=volume_decade_ratio,
        min_base_volume=min_base_volume,
        high_min_volume=high_min_volume,
        medium_min_volume=medium_min_volume,
        min_volume_to_emit=min_volume_to_emit,
        window=spec,
        data_start=data_start,
        data_end=today,
        editorial_policies=editorial_source_policies(),
        social_policies=social_source_policies(),
    )

    ctx.entries = _build_entries(ctx, social_rows, review_rows, editorial_rows)
    ctx.records = _explode_records(ctx, ctx.entries)
    ctx.pair_entries = _build_pair_entries(ctx, social_rows)
    return ctx


# ── trazabilidad de fuentes ─────────────────────────────────
#
# Cada ejemplo de evidencia sale con `{text, type, source_name, url, date}` y,
# cuando NO hay URL, con `url_status` + `url_reason`. La regla es simétrica a
# la regla anti-invención: así como un insight sin evidencia no se emite, una
# evidencia sin link no se esconde — se publica diciendo por qué no lo tiene.

EVIDENCE_TYPE_LABELS: dict[str, str] = {
    "editorial": "Nota editorial",
    "review": "Reseña de producto",
    "social": "Conversación social agregada",
}

#: Etiqueta legible del `source_type` de un agregado social.
SOCIAL_SOURCE_LABELS: dict[str, str] = {
    "forum": "foro público",
    "social": "red social",
    "community": "comunidad",
    "blog_comments": "comentarios de blog",
}

URL_STATUS_OK = "ok"
URL_STATUS_SOCIAL_AGGREGATE = "social_aggregate_privacy"
URL_STATUS_REVIEW_NOT_STORED = "review_url_not_stored"
URL_STATUS_EDITORIAL_MISSING = "editorial_url_missing"

#: Motivo publicable de cada estado de URL. Textos verificables contra el
#: esquema o contra la política de privacidad — no excusas genéricas.
URL_REASONS: dict[str, str | None] = {
    URL_STATUS_OK: None,
    URL_STATUS_SOCIAL_AGGREGATE: (
        "Señal social agregada: `social_mention_aggregates` guarda conteos por "
        "período, no posts, y `collectors/social.py::scrub()` borra URLs, "
        "handles, perfiles, mails y teléfonos antes de persistir. Linkear al "
        "post original exigiría guardar el post (y con él a su autor), así que "
        "por política de privacidad esta evidencia no tiene URL."),
    URL_STATUS_REVIEW_NOT_STORED: (
        "La tabla `reviews` no tiene columna de URL: la reseña se guarda con "
        "producto, retailer, rating y fecha, pero sin permalink. Se publica la "
        "ficha del producto como contexto navegable."),
    URL_STATUS_EDITORIAL_MISSING: (
        "La mención editorial se guardó sin `url` en `editorial_mentions`: la "
        "fuente y la fecha sí están, el enlace directo no."),
}

#: Claves que SIEMPRE viajan en un ejemplo de evidencia, aunque valgan `null`.
#: Sin esto `url: null` se perdía al serializar y "no hay link" era
#: indistinguible de "no hay evidencia".
EVIDENCE_REQUIRED_KEYS = frozenset({
    "text", "type", "type_label", "source", "source_name", "source_label",
    "url", "url_available", "url_status", "url_reason", "date", "date_field",
    "source_policy",
})


def _collector_registry() -> dict[str, Any]:
    """Registro de colectores, o vacío si la capa de adquisición no carga."""
    try:
        from app.collectors import registered
    except Exception:  # noqa: BLE001 - la trazabilidad nunca tumba el análisis
        return {}
    try:
        return registered() or {}
    except Exception:  # noqa: BLE001
        return {}


def _policy_card(collector_name: str, collector: Any) -> dict[str, Any] | None:
    policy = getattr(collector, "policy", None)
    if policy is None:
        return None
    return {
        "collector": collector_name,
        "source_name": policy.source_name,
        "homepage": policy.homepage,
        "access": policy.access,
        "terms": policy.terms,
        "stores": policy.stores,
        "enabled": bool(getattr(collector, "enabled", False)),
    }


def source_catalog() -> list[dict[str, Any]]:
    """Ficha pública de las fuentes registradas (para el panel de fuentes).

    Es el "de dónde salen los datos" a nivel fuente: nombre, homepage, tipo de
    acceso, ToS y qué guarda cada colector. Complementa el link por evidencia,
    que es el "de dónde sale ESTE dato".
    """
    cards = []
    for name, collector in sorted(_collector_registry().items()):
        card = _policy_card(name, collector)
        if card:
            cards.append({**card, "table": getattr(collector, "table", None)})
    return cards


def editorial_source_policies() -> dict[str, dict[str, Any]]:
    """``{nombre de fuente normalizado: ficha}`` de los colectores editoriales.

    `editorial_mentions.source_name` es el mismo string que declara la
    `SourcePolicy` del colector de feed, así que el cruce es exacto (no es una
    heurística de nombres parecidos).
    """
    out: dict[str, dict[str, Any]] = {}
    for name, collector in sorted(_collector_registry().items()):
        if getattr(collector, "table", None) != "editorial_mentions":
            continue
        card = _policy_card(name, collector)
        if card:
            out.setdefault(_norm(card["source_name"]), card)
    return out


def social_source_policies() -> dict[str, dict[str, Any]]:
    """``{source_type: ficha}``, SÓLO cuando la atribución es inequívoca.

    `social_mention_aggregates` no guarda qué colector escribió la fila (el
    esquema no tiene esa columna) y las filas del pipeline demo vienen de
    `app/seed.py`, no de un colector. Atribuirle una plataforma concreta a un
    `source_type` genérico sería inventar la fuente: se resuelve sólo si UN
    ÚNICO colector habilitado declara ese `source_type`.
    """
    candidates: dict[str, list[dict[str, Any]]] = {}
    for name, collector in sorted(_collector_registry().items()):
        if getattr(collector, "table", None) != "social_mention_aggregates":
            continue
        source_type = getattr(collector, "source_type", None)
        if not source_type or not getattr(collector, "enabled", False):
            continue
        card = _policy_card(name, collector)
        if card:
            candidates.setdefault(str(source_type), []).append(card)
    return {k: v[0] for k, v in candidates.items() if len(v) == 1}


def _evidence_item(*, text: Any, kind: str, source_name: Any, url: Any,
                   url_status: str, when: Any, date_field: str,
                   source_label: str | None = None,
                   policy: dict[str, Any] | None = None,
                   **extra: Any) -> dict[str, Any]:
    """Ejemplo de evidencia en la forma canónica ``{texto, tipo, fuente, url, fecha}``."""
    clean_url = str(url).strip() if url not in (None, "") else None
    status = url_status if clean_url is None else URL_STATUS_OK
    item: dict[str, Any] = {
        "text": str(text).strip(),
        "type": kind,
        "type_label": EVIDENCE_TYPE_LABELS.get(kind, kind),
        # `source` se mantiene por compatibilidad: es el mismo valor que `type`
        # y ya lo consumen los clientes actuales.
        "source": kind,
        "source_name": str(source_name) if source_name else kind,
        "source_label": source_label or (str(source_name) if source_name else kind),
        "url": clean_url,
        "url_available": clean_url is not None,
        "url_status": status,
        "url_reason": URL_REASONS.get(status),
        "date": str(when) if when else None,
        "date_field": date_field,
        "source_policy": policy,
    }
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _social_evidence(ctx: "BrandContext", row: dict[str, Any]) -> list[dict[str, Any]]:
    """Ejemplos públicos ya agregados. Nunca identifica individuos.

    Sin URL por diseño: ver ``URL_REASONS[URL_STATUS_SOCIAL_AGGREGATE]``.
    """
    samples = from_json(row.get("sample_evidence"), default=[]) or []
    if isinstance(samples, str):
        samples = [samples]
    source_type = str(row.get("source_type") or "social")
    out: list[dict[str, Any]] = []
    for sample in samples:
        text = sample if isinstance(sample, str) else (
            sample.get("text") if isinstance(sample, dict) else None)
        if not text:
            continue
        out.append(_evidence_item(
            text=text, kind="social", source_name=source_type,
            source_label=SOCIAL_SOURCE_LABELS.get(source_type, source_type),
            url=None, url_status=URL_STATUS_SOCIAL_AGGREGATE,
            when=row.get("period_end"), date_field="period_end",
            policy=ctx.social_policies.get(source_type),
            period_start=row.get("period_start"),
            period_end=row.get("period_end")))
    return out


def social_evidence_examples(rows: Iterable[dict[str, Any]], *,
                             limit: int = MAX_EVIDENCE_EXAMPLES) -> list[dict[str, Any]]:
    """Ejemplos trazables de filas crudas de ``social_mention_aggregates``.

    Es la puerta pública para quien tiene las filas a mano y no quiere pagar un
    ``build_context`` completo (la API de tópicos). Devuelve los mismos ítems
    canónicos que la evidencia de un insight: con ``url: null`` y el motivo.
    """
    policies = social_source_policies()
    proxy = _EvidenceOnlyContext(policies)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        for item in _social_evidence(proxy, row):  # type: ignore[arg-type]
            text = (item.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(item)
            if len(out) >= limit:
                return out
    return out


@dataclass
class _EvidenceOnlyContext:
    """Lo mínimo que ``_social_evidence`` necesita del contexto."""

    social_policies: dict[str, dict[str, Any]]


def _build_entries(ctx: BrandContext, social_rows: list[dict[str, Any]],
                   review_rows: list[dict[str, Any]],
                   editorial_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normaliza las tres fuentes a un formato común de observación."""
    entries: list[dict[str, Any]] = []

    for row in social_rows:
        window = ctx.window_of(row.get("period_end"))
        if window is None:
            continue
        brand_id = row.get("brand_id") or row.get("product_brand_id")
        product = ctx.products.get(int(row["product_id"])) if row.get("product_id") else None
        entries.append({
            "source": "social",
            "source_label": f"social:{row.get('source_type') or 'social'}",
            "window": window,
            "brand_id": int(brand_id) if brand_id is not None else None,
            "product_id": row.get("product_id"),
            "franchise": row.get("product_franchise") or (product or {}).get("franchise"),
            "volume": float(row.get("mention_count") or 0.0),
            "sentiment": (float(row["sentiment_score"])
                          if row.get("sentiment_score") is not None else None),
            "topic_raw": row.get("topic"),
            "intent_raw": row.get("intent"),
            "text": None,
            "evidence": _social_evidence(ctx, row),
        })

    for row in review_rows:
        window = ctx.window_of(row.get("observed_at"))
        if window is None:
            continue
        rating = row.get("rating")
        sentiment = clamp((float(rating) - 2.5) / 2.5, -1.0, 1.0) if rating is not None else None
        text = (row.get("review_text") or "").strip()
        product = ctx.products.get(int(row["product_id"])) if row.get("product_id") else None
        evidence = [_evidence_item(
            text=text, kind="review",
            source_name=row.get("source") or row.get("retailer_name") or "review",
            # `reviews` no guarda permalink (ver URL_REASONS): la ficha del
            # producto es lo más cerca que se puede llevar al usuario.
            url=None, url_status=URL_STATUS_REVIEW_NOT_STORED,
            when=row.get("observed_at"), date_field="observed_at",
            rating=rating,
            product=row.get("product_name"),
            product_id=row.get("product_id"),
            retailer=row.get("retailer_name"),
            context_url=(product or {}).get("url"),
            context_label="Ficha del producto",
            observed_at=row.get("observed_at"),
        )] if text else []
        entries.append({
            "source": "review",
            "source_label": f"review:{row.get('source') or row.get('retailer_name') or 'review'}",
            "window": window,
            "brand_id": int(row["brand_id"]) if row.get("brand_id") is not None else None,
            "product_id": row.get("product_id"),
            "franchise": row.get("franchise"),
            "volume": float(row.get("review_count") or 1.0),
            "sentiment": sentiment,
            "topic_raw": None,
            "intent_raw": None,
            "text": text,
            "evidence": evidence,
        })

    for row in editorial_rows:
        window = ctx.window_of(row.get("published_at"))
        if window is None:
            continue
        text = " ".join(filter(None, [row.get("title"), row.get("excerpt")])).strip()
        source_name = row.get("source_name") or "editorial"
        evidence = [_evidence_item(
            text=(row.get("excerpt") or row.get("title") or "").strip(),
            kind="editorial", source_name=source_name,
            url=row.get("url"), url_status=URL_STATUS_EDITORIAL_MISSING,
            when=row.get("published_at"), date_field="published_at",
            policy=ctx.editorial_policies.get(_norm(source_name)),
            title=row.get("title"),
            mention_type=row.get("mention_type"),
            published_at=row.get("published_at"),
        )] if (row.get("excerpt") or row.get("title")) else []
        for side in ("product_a_id", "product_b_id"):
            product_id = row.get(side)
            if product_id is None:
                continue
            product = ctx.products.get(int(product_id))
            if product is None:
                continue
            entries.append({
                "source": "editorial",
                "source_label": f"editorial:{row.get('source_name') or 'editorial'}",
                "window": window,
                "brand_id": (int(product["brand_id"])
                             if product.get("brand_id") is not None else None),
                "product_id": int(product_id),
                "franchise": product.get("franchise"),
                "volume": 1.0,
                "sentiment": None,
                "topic_raw": row.get("mention_type"),
                "intent_raw": None,
                "text": text,
                "evidence": evidence,
            })

    return entries


def _dimension_for_topic(ctx: BrandContext, topic: str | None) -> str | None:
    """Primera dimensión de la taxonomía que declara el tópico (determinístico)."""
    if not topic:
        return None
    dims = ctx.topic_dimensions.get(topic)
    return dims[0] if dims else None


def _explode_records(ctx: BrandContext, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Explota cada observación en registros ``(dimensión, tópico)``.

    * Los agregados sociales ya traen ``topic`` e ``intent`` de la taxonomía.
    * El texto de reviews (y de menciones editoriales) se clasifica con el
      léxico determinístico.
    """
    records: list[dict[str, Any]] = []
    for entry in entries:
        pairs: list[tuple[str, str]] = []

        topic_raw = entry.get("topic_raw")
        if topic_raw:
            dimension = _dimension_for_topic(ctx, str(topic_raw))
            if dimension:
                pairs.append((dimension, str(topic_raw)))

        intent_raw = entry.get("intent_raw")
        if intent_raw and str(intent_raw) in (ctx.taxonomy.get("consumer_intent") or []):
            pairs.append(("consumer_intent", str(intent_raw)))

        if entry.get("text"):
            pairs.extend(classify_text(entry["text"], ctx.taxonomy))
        elif not pairs:
            # Agregado social sin tópico taxonómico: intentamos con la evidencia.
            sample_text = " ".join(e.get("text", "") for e in entry.get("evidence") or [])
            pairs.extend(classify_text(sample_text, ctx.taxonomy))

        seen: set[tuple[str, str]] = set()
        for dimension, topic in pairs:
            if (dimension, topic) in seen:
                continue
            seen.add((dimension, topic))
            record = dict(entry)
            record["dimension"] = dimension
            record["topic"] = topic
            records.append(record)
    return records


def _build_pair_entries(ctx: BrandContext,
                        social_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Co-menciones producto ↔ co_producto, canonicalizadas.

    Si uno de los dos productos es de la marca foco queda primero, para que el
    matching engine reciba pares ``(nike_product, competitor_product)``.
    """
    pairs: list[dict[str, Any]] = []
    for row in social_rows:
        product_id, co_product_id = row.get("product_id"), row.get("co_product_id")
        if not product_id or not co_product_id or product_id == co_product_id:
            continue
        window = ctx.window_of(row.get("period_end"))
        if window is None:
            continue
        a, b = int(product_id), int(co_product_id)
        brand_a = (ctx.products.get(a) or {}).get("brand_id")
        brand_b = (ctx.products.get(b) or {}).get("brand_id")
        focus = ctx.focus_brand_id
        if focus is not None and brand_b == focus and brand_a != focus:
            a, b = b, a
        elif (focus is None or focus not in (brand_a, brand_b)) and a > b:
            a, b = b, a
        value = row.get("comention_count") or row.get("mention_count") or 0
        pairs.append({
            "source": "social",
            "source_label": f"social:{row.get('source_type') or 'social'}",
            "window": window,
            "key": (a, b),
            "volume": float(value),
            "sentiment": (float(row["sentiment_score"])
                          if row.get("sentiment_score") is not None else None),
            "evidence": _social_evidence(ctx, row),
            "topic_raw": row.get("topic"),
        })
    return pairs


# ── momentum ────────────────────────────────────────────────


def _ratio(current: float, previous: float) -> float | None:
    """Variación relativa. ``None`` si no hay base de comparación."""
    if previous > 0:
        return (current - previous) / previous
    return None


def _direction(current: float, previous: float) -> str:
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def _volume_score(current: float, reference: float | None,
                  decade_ratio: float) -> float | None:
    """Volumen 0..1 en escala LOG anclada a la mediana del cohorte.

    ``0.5`` = la mediana del cohorte; ``1.0`` = ``decade_ratio`` veces la
    mediana; ``0.0`` = ``decade_ratio`` veces por debajo. Sin cohorte con señal
    el factor queda no disponible (``None``), nunca 0.

    Por qué log y no un cociente lineal: los volúmenes de conversación son
    log-normales (una marca foco concentra un orden de magnitud más que la
    cola). Un cociente lineal contra el máximo aplasta toda la cola contra 0 y
    fija el tope en 1.0; el log reparte masa a lo largo del rango y hace la
    escala independiente de la unidad de cada fuente (menciones, reviews,
    notas), que es lo que permite comparar los tres tipos de señal.
    """
    if reference is None or reference <= 0:
        return None
    if current <= 0:
        return 0.0
    span = math.log10(max(decade_ratio, 1.0000001))
    return clamp(0.5 + 0.5 * math.log10(current / float(reference)) / span)


def _momentum_from_bucket(ctx: BrandContext, bucket: Bucket,
                          reference: float | None = None) -> dict[str, Any]:
    """Momentum 0..100 a partir de volumen, tendencia y aceleración.

    * ``volume``       — volumen del período actual en escala log contra la
      MEDIANA del cohorte (``cohort_reference``). Ver ``_volume_score``.
    * ``trend``        — variación vs período anterior, escalada con
      ``momentum.spike_threshold`` (±threshold ⇒ 1.0 / 0.0, sin cambio ⇒ 0.5).
      Requiere una base de comparación medible (``momentum.min_base_volume``):
      sobre 0 ó 2 menciones la variación relativa no es una medición, es ruido
      amplificado, y el factor se marca NO DISPONIBLE en vez de saturar.
    * ``acceleration`` — derivada segunda: tendencia actual menos la anterior;
      requiere tres ventanas con base suficiente, si no el factor no está.

    Regla de oro del módulo: un factor que no se puede medir se excluye y se
    renormaliza (baja la ``coverage``), NUNCA se rellena con el máximo. Rellenar
    con 1.0 era la causa de que todo el panel empatara en 100.
    """
    current, previous, prior = bucket.current, bucket.previous, bucket.prior
    # Con bases minúsculas el cociente explota (se vieron +75.670%): la
    # variación relativa sólo se publica si hay con qué compararla.
    min_base = ctx.min_base_volume
    trend_ratio = _ratio(current, previous) if previous >= min_base else None
    reliable_prior = previous >= min_base and prior >= min_base
    prev_trend_ratio = _ratio(previous, prior) if reliable_prior else None
    threshold = ctx.spike_threshold or 1.0

    volume_score = _volume_score(current, reference, ctx.volume_decade_ratio)

    trend_score: float | None = None
    if trend_ratio is not None:
        trend_score = clamp(0.5 + 0.5 * clamp(trend_ratio / threshold, -1.0, 1.0))

    acceleration: float | None = None
    if trend_ratio is not None and prev_trend_ratio is not None:
        acceleration = trend_ratio - prev_trend_ratio
    accel_score = (clamp(0.5 + 0.5 * clamp(acceleration / threshold, -1.0, 1.0))
                   if acceleration is not None else None)

    weights_cfg = ctx.momentum_weights
    composite = combine([
        Factor("volume", volume_score, weights_cfg.get("volume", 0.0),
               {"current": current, "reference": reference,
                "reference_kind": "mediana del cohorte",
                "decade_ratio": ctx.volume_decade_ratio}),
        Factor("trend", trend_score, weights_cfg.get("trend", 0.0),
               {"previous": previous, "ratio": trend_ratio, "min_base_volume": min_base}),
        Factor("acceleration", accel_score, weights_cfg.get("acceleration", 0.0),
               {"prior": prior, "previous_ratio": prev_trend_ratio}),
    ])

    emerging = previous <= 0 and current > 0
    spike = bool((trend_ratio is not None and abs(trend_ratio) >= ctx.spike_threshold)
                 or (emerging and current >= ctx.min_volume))

    return {
        "score": round(composite.score, 2),
        "volume": current,
        "volume_previous": previous,
        "volume_prior": prior,
        "trend": round(trend_ratio * 100.0, 2) if trend_ratio is not None else None,
        "trend_ratio": trend_ratio,
        "direction": _direction(current, previous),
        "acceleration": round(acceleration, 4) if acceleration is not None else None,
        "spike": spike,
        "emerging": emerging,
        "sentiment": bucket.sentiment("current"),
        "sentiment_previous": bucket.sentiment("previous"),
        "confidence": ctx.confidence_for_volume(current),
        "coverage": round(composite.coverage, 4),
        "coverage_confidence": composite.confidence,
        "factors": composite.factors,
        "sources": sorted(s for s in bucket.sources if s),
        "period_start": ctx.period_start,
        "period_end": ctx.period_end,
        # Toda lectura de momentum viaja con la ventana con la que se calculó:
        # un número sin su período de comparación no es interpretable.
        "window": ctx.window_info(),
        # Unidades explícitas: `score` es 0..100, `trend` es %, `trend_ratio` y
        # `acceleration` son RATIOS (0.62 = +62%). Mezclarlas sin decirlo es
        # exactamente lo que hacía ilegible la tabla de momentum.
        "units": {"score": "score_0_100", "volume": "count", "trend": "pct",
                  "trend_ratio": "ratio", "acceleration": "ratio"},
    }


def brand_momentum(brand_id: int, ctx: BrandContext) -> dict[str, Any]:
    """Brand Momentum Score (0..100) de una marca en el país configurado."""
    cohort = _accumulate(ctx.entries, lambda e: e.get("brand_id"))
    reference = cohort_reference(cohort.values())
    bucket = cohort.get(brand_id, Bucket())
    result = _momentum_from_bucket(ctx, bucket, reference)
    result.update({
        "entity_type": "brand",
        "entity_id": str(brand_id),
        "brand_id": brand_id,
        "brand_name": ctx.brand_name(brand_id),
        "country_code": ctx.country,
        "is_focus": bool((ctx.brands.get(brand_id) or {}).get("is_focus")),
    })
    return result


def conversation_momentum(topic: str, ctx: BrandContext) -> dict[str, Any]:
    """Conversation Momentum de un tópico de la taxonomía o de una franquicia."""
    if topic in ctx.topic_dimensions:
        cohort = _accumulate(ctx.records, lambda r: r.get("topic"))
        entity_type, label = "topic", _label_topic(topic)
        dimension = _dimension_for_topic(ctx, topic)
    else:
        cohort = _accumulate(ctx.entries, lambda e: e.get("franchise"))
        entity_type, label, dimension = "franchise", topic, None
    reference = cohort_reference(cohort.values())
    bucket = cohort.get(topic, Bucket())
    result = _momentum_from_bucket(ctx, bucket, reference)
    result.update({
        "entity_type": entity_type,
        "entity_id": str(topic),
        "topic": topic if entity_type == "topic" else None,
        "franchise": topic if entity_type == "franchise" else None,
        "dimension": dimension,
        "label": label,
        "country_code": ctx.country,
    })
    return result


# ── evidencia e insights ────────────────────────────────────


def _collect_examples(buckets: Iterable[Bucket]) -> list[dict[str, Any]]:
    """1..3 ejemplos textuales públicos, deduplicados y deterministas.

    Los campos de trazabilidad (``url``, ``url_status``, ``url_reason``, ...)
    viajan SIEMPRE, incluso en ``null``: antes se filtraban todos los valores
    nulos y una evidencia sin link quedaba indistinguible de una sin fuente.
    El resto de las claves opcionales se sigue podando.
    """
    items: list[dict[str, Any]] = []
    for bucket in buckets:
        items.extend(bucket.evidence)
    seen: set[str] = set()
    examples: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda i: (i.get("source", ""), i.get("text", ""))):
        text = (item.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        examples.append({k: v for k, v in item.items()
                         if v is not None or k in EVIDENCE_REQUIRED_KEYS})
        if len(examples) >= MAX_EVIDENCE_EXAMPLES:
            break
    return examples


def _emit(ctx: BrandContext, *, insight_type: str, dimension: str | None, topic: str | None,
          brand_id: int | None, text: str, volume: float, trend: float | None,
          direction: str, sentiment: float | None, buckets: Iterable[Bucket],
          metrics: dict[str, Any]) -> dict[str, Any] | None:
    """Único constructor de insights: sin volumen o sin evidencia, no se emite.

    Es la garantía dura de que ningún insight se inventa: el texto ya viene de
    una plantilla parametrizada y acá se verifica el respaldo cuantitativo
    (volumen mínimo configurado) y cualitativo (ejemplos reales de la DB).
    """
    if volume < ctx.min_volume:
        return None
    bucket_list = list(buckets)
    examples = _collect_examples(bucket_list)
    if not examples:
        return None
    sources = sorted({s for bucket in bucket_list for s in bucket.sources if s})
    evidence = {
        "insight_type": insight_type,
        "sources": sources,
        "examples": examples,
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in metrics.items()},
        # `current` y `previous_days` se conservan (los consumen los clientes
        # actuales); el resto del sobre dice CONTRA QUÉ se comparó y si el
        # histórico alcanzaba.
        "window": {**ctx.window_info(),
                   "previous_days": (ctx.cur_start - ctx.prev_start).days
                   if ctx.cur_start and ctx.prev_start else None},
    }
    return {
        "insight_type": insight_type,
        "country_code": ctx.country,
        "brand_id": brand_id,
        "dimension": dimension,
        "topic": topic,
        "insight_text": text,
        "signal_volume": int(round(volume)),
        "trend": round(trend, 2) if trend is not None else None,
        "direction": direction,
        "sentiment": round(sentiment, 4) if sentiment is not None else None,
        "confidence": ctx.confidence_for_volume(volume),
        "evidence": evidence,
        "period_start": ctx.period_start,
        "period_end": ctx.period_end,
    }


def _brand_topic_buckets(ctx: BrandContext) -> dict[tuple[int, str, str], Bucket]:
    return _accumulate(
        ctx.records,
        lambda r: ((int(r["brand_id"]), r["dimension"], r["topic"])
                   if r.get("brand_id") is not None else None))


def _topic_buckets(ctx: BrandContext) -> dict[tuple[str, str], Bucket]:
    return _accumulate(ctx.records, lambda r: (r["dimension"], r["topic"]))


# La "asociación de marca" se mide sobre dimensiones de percepción/cultura;
# la intención del consumidor tiene su propia plantilla.
_NON_ASSOCIATION_DIMENSIONS = frozenset({"consumer_intent"})


def _insight_topic_leadership(ctx: BrandContext) -> list[dict[str, Any]]:
    """"Nike mantiene la asociación más fuerte con X, pero Y gana en Z"."""
    focus = ctx.focus_brand_id
    if focus is None:
        return []
    by_brand_topic = _brand_topic_buckets(ctx)
    by_topic = _topic_buckets(ctx)

    def share(brand_id: int, dimension: str, topic: str) -> float:
        total = by_topic.get((dimension, topic), Bucket()).current
        if total <= 0:
            return 0.0
        return by_brand_topic.get((brand_id, dimension, topic), Bucket()).current / total

    owned = []
    for (brand_id, dimension, topic), bucket in by_brand_topic.items():
        if brand_id != focus or bucket.current < ctx.min_volume:
            continue
        if dimension in _NON_ASSOCIATION_DIMENSIONS:
            continue
        focus_share = share(focus, dimension, topic)
        leads = all(
            share(other, dimension, topic) <= focus_share
            for other in ctx.brands if other != focus)
        if leads:
            # Fuerza de asociación = share × volumen (ni un tópico chico con 100%
            # de share ni uno grande con share bajo son "la asociación más fuerte").
            owned.append((focus_share * bucket.current, focus_share, bucket.current,
                          dimension, topic, bucket))
    if not owned:
        return []
    owned.sort(key=lambda t: (-t[0], t[4]))
    _, focus_share, focus_volume, focus_dim, focus_topic, focus_bucket = owned[0]

    contested = []
    for (brand_id, dimension, topic), bucket in by_brand_topic.items():
        if brand_id == focus or bucket.current < ctx.min_volume:
            continue
        if dimension in _NON_ASSOCIATION_DIMENSIONS:
            continue
        rival_share = share(brand_id, dimension, topic)
        focus_rival_share = share(focus, dimension, topic)
        if rival_share <= focus_rival_share:
            continue
        rival_trend = _ratio(bucket.current, bucket.previous)
        contested.append((bucket.current, rival_share, focus_rival_share, brand_id,
                          dimension, topic, rival_trend, bucket))
    if not contested:
        return []
    contested.sort(key=lambda t: (-t[0], t[5]))
    (rival_volume, rival_share, focus_rival_share, rival_id,
     rival_dim, rival_topic, rival_trend, rival_bucket) = contested[0]

    text = (
        f"{ctx.brand_name(focus)} mantiene la asociación más fuerte con "
        f"{_label_topic(focus_topic)} ({focus_share * 100:.0f}% de la conversación del tópico, "
        f"{focus_volume:.0f} menciones), pero {ctx.brand_name(rival_id)} gana conversación en "
        f"{_label_topic(rival_topic)} ({rival_share * 100:.0f}% vs {focus_rival_share * 100:.0f}% "
        f"de {ctx.brand_name(focus)}, {_pct(rival_trend * 100 if rival_trend is not None else None)} "
        f"vs el período anterior)."
    )
    insight = _emit(
        ctx, insight_type="brand_topic_leadership", dimension=focus_dim, topic=focus_topic,
        brand_id=focus, text=text, volume=focus_volume + rival_volume,
        trend=(_ratio(focus_bucket.current, focus_bucket.previous) or 0.0) * 100.0
        if focus_bucket.previous > 0 else None,
        direction=_direction(focus_bucket.current, focus_bucket.previous),
        sentiment=focus_bucket.sentiment("current"),
        buckets=[focus_bucket, rival_bucket],
        metrics={"focus_topic": focus_topic, "focus_share": focus_share,
                 "focus_volume": focus_volume, "rival_brand": ctx.brand_name(rival_id),
                 "rival_topic": rival_topic, "rival_share": rival_share,
                 "rival_volume": rival_volume, "rival_trend_ratio": rival_trend})
    return [insight] if insight else []


def _insight_negative_drivers(ctx: BrandContext) -> list[dict[str, Any]]:
    """"El precio es hoy uno de los drivers negativos más fuertes de Nike"."""
    focus = ctx.focus_brand_id
    if focus is None:
        return []
    candidates = []
    for (brand_id, dimension, topic), bucket in _brand_topic_buckets(ctx).items():
        if brand_id != focus or bucket.current < ctx.min_volume:
            continue
        sentiment = bucket.sentiment("current")
        if sentiment is None or sentiment >= 0:
            continue
        candidates.append((bucket.current * abs(sentiment), dimension, topic, bucket, sentiment))
    if not candidates:
        return []
    candidates.sort(key=lambda t: (-t[0], t[2]))

    out: list[dict[str, Any]] = []
    for weight, dimension, topic, bucket, sentiment in candidates[:2]:
        trend_ratio = _ratio(bucket.current, bucket.previous)
        text = (
            f"{_label_topic(topic).capitalize()} es hoy uno de los drivers negativos más fuertes "
            f"de {ctx.brand_name(focus)} en {ctx.country_name}: {bucket.current:.0f} menciones "
            f"con sentimiento promedio {sentiment:+.2f} y una variación de "
            f"{_pct(trend_ratio * 100 if trend_ratio is not None else None)} vs el período anterior."
        )
        insight = _emit(
            ctx, insight_type="negative_driver", dimension=dimension, topic=topic,
            brand_id=focus, text=text, volume=bucket.current,
            trend=trend_ratio * 100.0 if trend_ratio is not None else None,
            direction=_direction(bucket.current, bucket.previous), sentiment=sentiment,
            buckets=[bucket],
            metrics={"negativity_weight": weight, "dimension": dimension,
                     "sentiment_previous": bucket.sentiment("previous")})
        if insight:
            out.append(insight)
    return out


def _insight_praise_vs_friction(ctx: BrandContext) -> list[dict[str, Any]]:
    """"Elogian el diseño pero mencionan disponibilidad limitada"."""
    focus = ctx.focus_brand_id
    if focus is None:
        return []
    by_brand_topic = _brand_topic_buckets(ctx)
    positives, negatives = [], []
    for (brand_id, dimension, topic), bucket in by_brand_topic.items():
        if brand_id != focus or bucket.current < ctx.min_volume:
            continue
        sentiment = bucket.sentiment("current")
        if dimension == "product_perception" and (sentiment or 0) > 0:
            positives.append((bucket.current, dimension, topic, bucket, sentiment))
        if dimension == "availability" or (sentiment is not None and sentiment < 0):
            negatives.append((bucket.current, dimension, topic, bucket, sentiment))
    if not positives or not negatives:
        return []
    positives.sort(key=lambda t: (-t[0], t[2]))
    # La fricción de disponibilidad se prioriza: es la más accionable para retail.
    negatives.sort(key=lambda t: (t[1] != "availability", -t[0], t[2]))
    pos_vol, pos_dim, pos_topic, pos_bucket, pos_sent = positives[0]
    friction = next((n for n in negatives if (n[1], n[2]) != (pos_dim, pos_topic)), None)
    if friction is None:
        return []
    neg_vol, neg_dim, neg_topic, neg_bucket, neg_sent = friction
    neg_trend = _ratio(neg_bucket.current, neg_bucket.previous)
    text = (
        f"Los consumidores elogian a {ctx.brand_name(focus)} en {_label_topic(pos_topic)} "
        f"({pos_vol:.0f} menciones, sentimiento {pos_sent:+.2f}) pero mencionan con frecuencia "
        f"{_label_topic(neg_topic)} ({neg_vol:.0f} menciones, "
        f"{_pct(neg_trend * 100 if neg_trend is not None else None)} vs el período anterior)."
    )
    insight = _emit(
        ctx, insight_type="praise_vs_friction", dimension=neg_dim, topic=neg_topic,
        brand_id=focus, text=text, volume=pos_vol + neg_vol,
        trend=neg_trend * 100.0 if neg_trend is not None else None,
        direction=_direction(neg_bucket.current, neg_bucket.previous), sentiment=neg_sent,
        buckets=[pos_bucket, neg_bucket],
        metrics={"praised_topic": pos_topic, "praised_volume": pos_vol,
                 "praised_sentiment": pos_sent, "friction_topic": neg_topic,
                 "friction_volume": neg_vol, "friction_sentiment": neg_sent})
    return [insight] if insight else []


def _insight_comparison_shift(ctx: BrandContext) -> list[dict[str, Any]]:
    """"Comparan cada vez más Pegasus contra Novablast que contra ...".

    Se apoya en las co-menciones producto ↔ co_producto del período.
    """
    pairs = _accumulate(ctx.pair_entries, lambda e: e["key"])
    if not pairs:
        return []
    by_anchor: dict[int, list[tuple[tuple[int, int], Bucket]]] = {}
    for key, bucket in pairs.items():
        by_anchor.setdefault(key[0], []).append((key, bucket))

    out: list[dict[str, Any]] = []
    for anchor, items in sorted(by_anchor.items()):
        if len(items) < 2:
            continue
        scored = [(k, b, _ratio(b.current, b.previous)) for k, b in items]
        rising = max(((k, b, t) for k, b, t in scored if t is not None and t > 0),
                     key=lambda x: (x[2], x[1].current, -x[0][1]), default=None)
        if rising is None:
            continue
        key_a, bucket_a, trend_a = rising
        # El par de referencia es el más conversado entre los que crecen menos.
        reference = max(((k, b, t) for k, b, t in scored
                         if k != key_a and (t is None or t < trend_a)),
                        key=lambda x: (x[1].current, -x[0][1]), default=None)
        if reference is None:
            continue
        key_b, bucket_b, trend_b = reference
        product = ctx.products.get(anchor) or {}
        context_label = (product.get("use_case") or product.get("sport")
                         or product.get("category") or "la categoría")
        rival_a = ctx.products.get(key_a[1]) or {}
        rival_b = ctx.products.get(key_b[1]) or {}
        text = (
            f"En las conversaciones de {context_label}, {ctx.product_name(anchor)} se compara "
            f"cada vez más contra {ctx.product_name(key_a[1])} ({bucket_a.current:.0f} "
            f"co-menciones, {_pct(trend_a * 100)}) que contra {ctx.product_name(key_b[1])} "
            f"({bucket_b.current:.0f} co-menciones, "
            f"{_pct(trend_b * 100 if trend_b is not None else None)})."
        )
        insight = _emit(
            ctx, insight_type="comparison_shift", dimension="consumer_intent",
            topic="comparing", brand_id=product.get("brand_id"), text=text,
            volume=bucket_a.current + bucket_b.current, trend=trend_a * 100.0,
            direction=_direction(bucket_a.current, bucket_a.previous),
            sentiment=bucket_a.sentiment("current"), buckets=[bucket_a, bucket_b],
            metrics={"anchor_product_id": anchor,
                     "rising_product_id": key_a[1],
                     "rising_brand": ctx.brand_name(rival_a.get("brand_id")),
                     "rising_comentions": bucket_a.current,
                     "rising_trend_ratio": trend_a,
                     "reference_product_id": key_b[1],
                     "reference_brand": ctx.brand_name(rival_b.get("brand_id")),
                     "reference_comentions": bucket_b.current,
                     "reference_trend_ratio": trend_b})
        if insight:
            out.append(insight)
    return out


def _insight_emerging_topics(ctx: BrandContext) -> list[dict[str, Any]]:
    """Tópicos presentes en el período actual y ausentes en el anterior."""
    out: list[dict[str, Any]] = []
    for (brand_id, dimension, topic), bucket in sorted(
            _brand_topic_buckets(ctx).items(), key=lambda kv: (-kv[1].current, kv[0])):
        if bucket.previous > 0 or bucket.current < ctx.min_volume:
            continue
        text = (
            f"{_label_topic(topic).capitalize()} emerge como tema nuevo en la conversación sobre "
            f"{ctx.brand_name(brand_id)} en {ctx.country_name}: {bucket.current:.0f} menciones en "
            f"el período actual y ninguna en el anterior."
        )
        insight = _emit(
            ctx, insight_type="emerging_topic", dimension=dimension, topic=topic,
            brand_id=brand_id, text=text, volume=bucket.current, trend=None,
            direction=_direction(bucket.current, bucket.previous),
            sentiment=bucket.sentiment("current"), buckets=[bucket],
            metrics={"previous_volume": bucket.previous, "prior_volume": bucket.prior})
        if insight:
            out.append(insight)
    return out


def _insight_sentiment_deterioration(ctx: BrandContext) -> list[dict[str, Any]]:
    """Sentimiento negativo creciente: cae vs el período anterior Y ya es negativo."""
    out: list[dict[str, Any]] = []
    for (brand_id, dimension, topic), bucket in sorted(
            _brand_topic_buckets(ctx).items(), key=lambda kv: (-kv[1].current, kv[0])):
        current, previous = bucket.sentiment("current"), bucket.sentiment("previous")
        if current is None or previous is None or current >= previous or current >= 0:
            continue
        if bucket.current < ctx.min_volume:
            continue
        trend_ratio = _ratio(bucket.current, bucket.previous)
        text = (
            f"El sentimiento sobre {_label_topic(topic)} de {ctx.brand_name(brand_id)} se "
            f"deteriora en {ctx.country_name}: pasa de {previous:+.2f} a {current:+.2f} sobre "
            f"{bucket.current:.0f} menciones "
            f"({_pct(trend_ratio * 100 if trend_ratio is not None else None)} de volumen)."
        )
        insight = _emit(
            ctx, insight_type="sentiment_deterioration", dimension=dimension, topic=topic,
            brand_id=brand_id, text=text, volume=bucket.current,
            trend=trend_ratio * 100.0 if trend_ratio is not None else None,
            direction=_direction(bucket.current, bucket.previous), sentiment=current,
            buckets=[bucket],
            metrics={"sentiment_previous": previous, "sentiment_current": current,
                     "sentiment_delta": current - previous})
        if insight:
            out.append(insight)
    return out


def _insight_intent_momentum(ctx: BrandContext) -> list[dict[str, Any]]:
    """Momentum de la intención del consumidor (comprar, comparar, cambiar...)."""
    focus = ctx.focus_brand_id
    candidates = []
    for (brand_id, dimension, topic), bucket in _brand_topic_buckets(ctx).items():
        if dimension != "consumer_intent" or bucket.current < ctx.min_volume:
            continue
        if focus is not None and brand_id != focus:
            continue
        trend_ratio = _ratio(bucket.current, bucket.previous)
        if trend_ratio is None:
            continue
        candidates.append((abs(trend_ratio), trend_ratio, brand_id, topic, bucket))
    candidates.sort(key=lambda t: (-t[0], t[3]))

    out: list[dict[str, Any]] = []
    for _, trend_ratio, brand_id, topic, bucket in candidates[:2]:
        verb = "crece" if trend_ratio > 0 else ("cae" if trend_ratio < 0 else "se mantiene")
        text = (
            f"La intención de consumidor «{_label_topic(topic)}» sobre "
            f"{ctx.brand_name(brand_id)} {verb} "
            f"{abs(trend_ratio) * 100:.0f}% en {ctx.country_name} "
            f"({bucket.current:.0f} menciones en el período actual vs "
            f"{bucket.previous:.0f} en el anterior)."
        )
        insight = _emit(
            ctx, insight_type="intent_momentum", dimension="consumer_intent", topic=topic,
            brand_id=brand_id, text=text, volume=bucket.current, trend=trend_ratio * 100.0,
            direction=_direction(bucket.current, bucket.previous),
            sentiment=bucket.sentiment("current"), buckets=[bucket],
            metrics={"previous_volume": bucket.previous, "prior_volume": bucket.prior})
        if insight:
            out.append(insight)
    return out


def _insight_competitor_momentum(ctx: BrandContext) -> list[dict[str, Any]]:
    """Competidor que crece por encima de la marca foco."""
    focus = ctx.focus_brand_id
    if focus is None:
        return []
    cohort = _accumulate(ctx.entries, lambda e: e.get("brand_id"))
    focus_bucket = cohort.get(focus, Bucket())
    focus_trend = _ratio(focus_bucket.current, focus_bucket.previous)
    if focus_trend is None:
        return []
    rivals = []
    for brand_id, bucket in cohort.items():
        if brand_id is None or brand_id == focus or bucket.current < ctx.min_volume:
            continue
        trend_ratio = _ratio(bucket.current, bucket.previous)
        if trend_ratio is None or trend_ratio <= focus_trend:
            continue
        rivals.append((trend_ratio, brand_id, bucket))
    if not rivals:
        return []
    rivals.sort(key=lambda t: (-t[0], t[1]))
    trend_ratio, brand_id, bucket = rivals[0]
    text = (
        f"La conversación sobre {ctx.brand_name(brand_id)} crece "
        f"{_pct(trend_ratio * 100)} en {ctx.country_name} contra "
        f"{_pct(focus_trend * 100)} de {ctx.brand_name(focus)} "
        f"({bucket.current:.0f} menciones de {ctx.brand_name(brand_id)} vs "
        f"{focus_bucket.current:.0f} de {ctx.brand_name(focus)} en el período actual)."
    )
    insight = _emit(
        ctx, insight_type="competitor_momentum", dimension="brand_perception", topic=None,
        brand_id=brand_id, text=text, volume=bucket.current, trend=trend_ratio * 100.0,
        direction=_direction(bucket.current, bucket.previous),
        sentiment=bucket.sentiment("current"), buckets=[bucket],
        metrics={"focus_brand": ctx.brand_name(focus), "focus_trend_ratio": focus_trend,
                 "focus_volume": focus_bucket.current, "rival_trend_ratio": trend_ratio})
    return [insight] if insight else []


def _insight_franchise_momentum(ctx: BrandContext) -> list[dict[str, Any]]:
    """Pico de conversación sobre una franquicia (Pegasus, Air Max, ...)."""
    cohort = _accumulate(ctx.entries, lambda e: e.get("franchise"))
    reference = cohort_reference(cohort.values())
    out: list[dict[str, Any]] = []
    for franchise, bucket in sorted(cohort.items(), key=lambda kv: (-kv[1].current, str(kv[0]))):
        if not franchise or bucket.current < ctx.min_volume:
            continue
        momentum = _momentum_from_bucket(ctx, bucket, reference)
        if not momentum["spike"] or momentum["direction"] != "up":
            continue
        text = (
            f"La franquicia {franchise} concentra un pico de conversación en {ctx.country_name}: "
            f"{bucket.current:.0f} menciones ({_pct(momentum['trend'])} vs el período anterior, "
            f"momentum {momentum['score']:.0f}/100)."
        )
        insight = _emit(
            ctx, insight_type="franchise_momentum", dimension="cultural_relevance", topic=None,
            brand_id=None, text=text, volume=bucket.current, trend=momentum["trend"],
            direction=momentum["direction"], sentiment=bucket.sentiment("current"),
            buckets=[bucket],
            metrics={"franchise": franchise, "momentum_score": momentum["score"],
                     "acceleration": momentum["acceleration"],
                     "spike_threshold": ctx.spike_threshold})
        if insight:
            out.append(insight)
    return out


INSIGHT_GENERATORS: tuple[Callable[[BrandContext], list[dict[str, Any]]], ...] = (
    _insight_topic_leadership,
    _insight_emerging_topics,
    _insight_negative_drivers,
    _insight_praise_vs_friction,
    _insight_comparison_shift,
    _insight_sentiment_deterioration,
    _insight_intent_momentum,
    _insight_competitor_momentum,
    _insight_franchise_momentum,
)


# Plantillas que describen el MISMO tópico desde ángulos distintos: se emite
# sólo la primera (según el orden de INSIGHT_GENERATORS) para no repetir señal.
_TOPIC_LEVEL_TYPES = frozenset({"negative_driver", "emerging_topic",
                                "sentiment_deterioration"})


def generate_insights(ctx: BrandContext) -> list[dict[str, Any]]:
    """Insights accionables en español, todos respaldados por métricas reales."""
    if not ctx.has_data:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    covered_topics: set[tuple[Any, ...]] = set()
    for generator in INSIGHT_GENERATORS:
        for insight in generator(ctx):
            key = (insight["insight_type"], insight.get("brand_id"),
                   insight.get("dimension"), insight.get("topic"))
            if key in seen:
                continue
            topic_key = (insight.get("brand_id"), insight.get("dimension"),
                         insight.get("topic"))
            if insight["insight_type"] in _TOPIC_LEVEL_TYPES:
                if topic_key in covered_topics:
                    continue
                covered_topics.add(topic_key)
            seen.add(key)
            out.append(insight)
    out.sort(key=lambda i: (-i["signal_volume"], i["insight_type"], str(i.get("topic"))))
    return out


# ── tendencias ──────────────────────────────────────────────


def detect_trends(ctx: BrandContext) -> list[dict[str, Any]]:
    """Aceleración, picos, tópicos emergentes, deterioro y competidor emergente."""
    if not ctx.has_data:
        return []
    trends: list[dict[str, Any]] = []

    def push(trend_type: str, entity_type: str, entity_id: Any, label: str,
             momentum: dict[str, Any], **extra: Any) -> None:
        trends.append({
            "trend_type": trend_type,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "label": label,
            "country_code": ctx.country,
            "volume": momentum["volume"],
            "volume_previous": momentum["volume_previous"],
            "trend": momentum["trend"],
            "direction": momentum["direction"],
            "acceleration": momentum["acceleration"],
            "spike": momentum["spike"],
            "momentum_score": momentum["score"],
            "sentiment": momentum["sentiment"],
            "confidence": momentum["confidence"],
            "period_start": ctx.period_start,
            "period_end": ctx.period_end,
            "window": momentum["window"],
            "units": momentum["units"],
            **extra,
        })

    # 1-2. Tópicos: aceleración, picos y emergentes.
    topic_cohort = _accumulate(ctx.records, lambda r: (r["dimension"], r["topic"]))
    topic_reference = cohort_reference(topic_cohort.values())
    for (dimension, topic), bucket in sorted(topic_cohort.items(),
                                             key=lambda kv: (-kv[1].current, kv[0])):
        if bucket.current < ctx.min_volume:
            continue
        momentum = _momentum_from_bucket(ctx, bucket, topic_reference)
        labels = {"dimension": dimension, "dimension_label": _label_dimension(dimension)}
        if momentum["emerging"]:
            push("emerging_topic", "topic", topic, _label_topic(topic), momentum, **labels)
        elif momentum["spike"]:
            push("spike", "topic", topic, _label_topic(topic), momentum, **labels)
        if (momentum["acceleration"] or 0) >= ctx.spike_threshold:
            push("acceleration", "topic", topic, _label_topic(topic), momentum, **labels)
        sentiment_prev = bucket.sentiment("previous")
        if (momentum["sentiment"] is not None and sentiment_prev is not None
                and momentum["sentiment"] < sentiment_prev and momentum["sentiment"] < 0):
            push("negative_sentiment_rising", "topic", topic, _label_topic(topic), momentum,
                 sentiment_previous=round(sentiment_prev, 4), **labels)

    # 3. Competidor emergente.
    brand_cohort = _accumulate(ctx.entries, lambda e: e.get("brand_id"))
    brand_reference = cohort_reference(brand_cohort.values())
    focus_bucket = brand_cohort.get(ctx.focus_brand_id, Bucket())
    focus_trend = _ratio(focus_bucket.current, focus_bucket.previous)
    for brand_id, bucket in sorted(brand_cohort.items(), key=lambda kv: (-kv[1].current,
                                                                        kv[0] or 0)):
        if brand_id is None or brand_id == ctx.focus_brand_id or bucket.current < ctx.min_volume:
            continue
        momentum = _momentum_from_bucket(ctx, bucket, brand_reference)
        trend_ratio = momentum["trend_ratio"]
        if trend_ratio is None:
            continue
        if focus_trend is not None and trend_ratio > focus_trend:
            push("emerging_competitor", "brand", brand_id, ctx.brand_name(brand_id), momentum,
                 focus_trend=round(focus_trend * 100.0, 2))

    # 4. Momentum de franquicia.
    franchise_cohort = _accumulate(ctx.entries, lambda e: e.get("franchise"))
    franchise_reference = cohort_reference(franchise_cohort.values())
    for franchise, bucket in sorted(franchise_cohort.items(),
                                    key=lambda kv: (-kv[1].current, str(kv[0]))):
        if not franchise or bucket.current < ctx.min_volume:
            continue
        momentum = _momentum_from_bucket(ctx, bucket, franchise_reference)
        if momentum["spike"] or (momentum["acceleration"] or 0) >= ctx.spike_threshold:
            push("franchise_momentum", "franchise", franchise, str(franchise), momentum)

    return trends


# ── puente Brand ↔ Product Intelligence ─────────────────────


def social_competition_signals(ctx: BrandContext) -> list[dict[str, Any]]:
    """Pares producto ↔ co_producto con intensidad normalizada 0..1 y tendencia.

    Devuelve **datos** para que el matching engine suba el
    ``social_competition_score`` de esos pares. No escribe en
    ``competitive_matches`` (esa tabla es de otro módulo).
    """
    if not ctx.has_data:
        return []
    pairs = _accumulate(ctx.pair_entries, lambda e: e["key"])
    if not pairs:
        return []
    # Dos referencias distintas a propósito:
    #  * `intensity` es un peso 0..1 que consume el matching engine y tiene que
    #    seguir siendo "share contra el par más co-mencionado" => máximo.
    #  * el momentum del par se lee como el resto de las señales => mediana.
    intensity_reference = max((b.current for b in pairs.values()), default=0.0)
    reference = cohort_reference(pairs.values())
    out: list[dict[str, Any]] = []
    for (product_id, co_product_id), bucket in pairs.items():
        if bucket.total <= 0:
            continue
        momentum = _momentum_from_bucket(ctx, bucket, reference)
        product = ctx.products.get(product_id) or {}
        co_product = ctx.products.get(co_product_id) or {}
        out.append({
            "product_id": product_id,
            "co_product_id": co_product_id,
            "product_name": ctx.product_name(product_id),
            "co_product_name": ctx.product_name(co_product_id),
            "brand_id": product.get("brand_id"),
            "brand_name": ctx.brand_name(product.get("brand_id")),
            "co_brand_id": co_product.get("brand_id"),
            "co_brand_name": ctx.brand_name(co_product.get("brand_id")),
            "country_code": ctx.country,
            "comentions_current": bucket.current,
            "comentions_previous": bucket.previous,
            "comentions_prior": bucket.prior,
            # Intensidad normalizada contra el par más co-mencionado del período.
            "intensity": round(clamp(bucket.current / intensity_reference)
                               if intensity_reference > 0 else 0.0, 4),
            "trend": momentum["trend"],
            "direction": momentum["direction"],
            "acceleration": momentum["acceleration"],
            "spike": momentum["spike"],
            "momentum_score": momentum["score"],
            "sentiment": momentum["sentiment"],
            "confidence": momentum["confidence"],
            "evidence": {"sources": momentum["sources"],
                         "examples": _collect_examples([bucket])},
            "period_start": ctx.period_start,
            "period_end": ctx.period_end,
        })
    out.sort(key=lambda p: (-p["intensity"], p["product_id"], p["co_product_id"]))
    return out


# ── market_signals ──────────────────────────────────────────


_SIGNAL_BY_SOURCE = {"social": "social_momentum",
                     "editorial": "editorial_momentum",
                     "review": "review_momentum"}


#: Unidad de cada campo de una fila de momentum publicada por este módulo.
#: `value` es un score 0..100 (NO un volumen) y `delta`/`acceleration` son
#: RATIOS (0.62 = +62%). La confusión entre esto y `share_of_shelf` —que está
#: en % y en puntos porcentuales— es lo que hacía ilegible la tabla.
MOMENTUM_SIGNAL_UNITS: dict[str, str] = {
    "value": "score_0_100",
    "delta": "ratio",
    "acceleration": "ratio",
    "volume": "count",
}

#: Qué cuenta el volumen absoluto de cada fuente. "Volumen" a secas no dice
#: nada: 100 menciones de foro y 100 notas editoriales no son lo mismo.
VOLUME_UNITS: dict[str, str] = {
    "social": "menciones",
    "editorial": "notas",
    "review": "reviews",
}


def _volume_unit(source: str) -> str:
    return VOLUME_UNITS.get(source, "observaciones")


def _entity_label(ctx: BrandContext, entity_type: str, entity_id: Any) -> str:
    """Nombre legible de la entidad de una señal (nunca "Producto #17")."""
    if entity_type == "brand":
        try:
            return ctx.brand_name(int(entity_id))
        except (TypeError, ValueError):
            return str(entity_id)
    if entity_type == "product":
        try:
            return ctx.product_name(int(entity_id))
        except (TypeError, ValueError):
            return str(entity_id)
    # franchise / topic: el id ya es el nombre.
    return str(entity_id)


def build_market_signals(ctx: BrandContext) -> list[dict[str, Any]]:
    """Señales de mercado por marca, producto y franquicia (las tres fuentes).

    Las filas persisten sólo las columnas de ``_SIGNAL_COLUMNS``; el resto de
    las claves (``volume``, ``units``, ``entity_label``, ...) son contexto para
    quien consume la función en memoria — por ejemplo la API, que necesita
    publicar el volumen absoluto y la unidad de cada número.
    """
    if not ctx.has_data:
        return []
    signals: list[dict[str, Any]] = []
    entity_keys: tuple[tuple[str, Callable[[dict[str, Any]], Any]], ...] = (
        ("brand", lambda e: e.get("brand_id")),
        ("product", lambda e: e.get("product_id")),
        ("franchise", lambda e: e.get("franchise")),
    )
    for source, signal_type in _SIGNAL_BY_SOURCE.items():
        source_entries = [e for e in ctx.entries if e.get("source") == source]
        if not source_entries:
            continue
        for entity_type, keyfn in entity_keys:
            cohort = _accumulate(source_entries, keyfn)
            reference = cohort_reference(cohort.values())
            for entity_id, bucket in sorted(cohort.items(), key=lambda kv: str(kv[0])):
                if entity_id is None or bucket.total <= 0:
                    continue
                momentum = _momentum_from_bucket(ctx, bucket, reference)
                signals.append({
                    "signal_type": signal_type,
                    "entity_type": entity_type,
                    "entity_id": str(entity_id),
                    "country_code": ctx.country,
                    "value": momentum["score"],
                    # `delta` y `acceleration` de una fila de momentum van en la
                    # MISMA unidad: RATIO (0.62 = +62%), no porcentaje. La
                    # aceleración siempre fue un ratio y los consumidores tratan
                    # a `delta` como su reemplazo cuando falta la tercera
                    # ventana (opportunities.IntelContext.momentum). Mientras
                    # acá se escribió el porcentaje, ese fallback comparaba 61.54
                    # contra `min_acceleration: 0.35` y el motor publicaba
                    # "acelera 6154%". La unidad legible (%) se calcula al
                    # mostrar, no se persiste.
                    "delta": (round(momentum["trend_ratio"], 4)
                              if momentum["trend_ratio"] is not None else None),
                    "acceleration": momentum["acceleration"],
                    "period_start": ctx.period_start,
                    "period_end": ctx.period_end,
                    # ── no persistido: contexto para la API ──
                    "entity_label": _entity_label(ctx, entity_type, entity_id),
                    "volume": momentum["volume"],
                    "volume_previous": momentum["volume_previous"],
                    "volume_prior": momentum["volume_prior"],
                    "volume_unit": _volume_unit(source),
                    "trend_pct": momentum["trend"],
                    "direction": momentum["direction"],
                    "confidence": momentum["confidence"],
                    "coverage": momentum["coverage"],
                    "sources": momentum["sources"],
                    "units": MOMENTUM_SIGNAL_UNITS,
                    "window": momentum["window"],
                })
    return signals


# ── persistencia ────────────────────────────────────────────

_INSIGHT_COLUMNS = ("country_code", "brand_id", "dimension", "topic", "insight_text",
                    "signal_volume", "trend", "direction", "sentiment", "confidence",
                    "evidence", "period_start", "period_end")

_SIGNAL_COLUMNS = ("signal_type", "entity_type", "entity_id", "country_code", "value",
                   "delta", "acceleration", "period_start", "period_end")


def run_brand_intelligence(db_path: Path | str = DB_PATH, *, window: str | None = None,
                           window_days: int | None = None,
                           compare_days: int | None = None) -> dict[str, int]:
    """Calcula y persiste señales de mercado e insights de marca (idempotente).

    ``window``/``window_days``/``compare_days`` eligen el período de
    comparación y bajan por toda la cadena (``build_market_signals``,
    ``generate_insights``, ``detect_trends``, ``social_competition_signals``)
    vía el contexto. El default es el histórico: mes contra mes anterior.

    OJO: esto ESCRIBE. La API no lo usa para ventanas a medida — recalcula en
    memoria con ``build_context(window=...)`` y no toca lo persistido.
    """
    ctx = build_context(db_path, window=window, window_days=window_days,
                        compare_days=compare_days)
    signals = build_market_signals(ctx)
    insights = generate_insights(ctx)
    trends = detect_trends(ctx)
    competition = social_competition_signals(ctx)

    placeholders = ",".join("?" for _ in SIGNAL_TYPES)
    with get_conn(db_path) as conn:
        conn.execute(
            f"DELETE FROM market_signals WHERE country_code = ? "
            f"AND signal_type IN ({placeholders})",
            (ctx.country, *SIGNAL_TYPES))
        conn.execute("DELETE FROM brand_insights WHERE country_code = ?", (ctx.country,))
        if signals:
            conn.executemany(
                f"INSERT INTO market_signals ({','.join(_SIGNAL_COLUMNS)}) "
                f"VALUES ({','.join('?' for _ in _SIGNAL_COLUMNS)})",
                [tuple(s[c] for c in _SIGNAL_COLUMNS) for s in signals])
        if insights:
            conn.executemany(
                f"INSERT INTO brand_insights ({','.join(_INSIGHT_COLUMNS)}) "
                f"VALUES ({','.join('?' for _ in _INSIGHT_COLUMNS)})",
                [tuple(to_json(i[c]) if c == "evidence" else i[c] for c in _INSIGHT_COLUMNS)
                 for i in insights])

    return {
        "market_signals": len(signals),
        "brand_insights": len(insights),
        "trends": len(trends),
        "social_competition_pairs": len(competition),
    }
