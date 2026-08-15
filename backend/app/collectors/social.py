"""Adquisición social — **SIEMPRE AGREGADA**.

Regla no negociable: acá no se modelan personas. El colector recibe textos
públicos, los cuenta por período y los tira; lo único que se persiste son
conteos, co-menciones producto↔producto, sentimiento agregado, tópico e intent,
más 1..3 ejemplos textuales públicos **sin autor ni identificador**.

  * Los campos de identidad (autor, handle, id de usuario, avatar, perfil) se
    descartan al normalizar el post y ``base.assert_aggregate_only`` vuelve a
    verificarlo antes de escribir: si alguna vez se cuela uno, la escritura
    falla en vez de contaminar la base.
  * ``sample_evidence`` pasa por :func:`scrub`: sin @menciones, sin URLs, sin
    mails ni teléfonos.
  * Sentimiento: léxico determinístico rioplatense (``collectors.sentiment``).
  * Tópico e intent: el mismo léxico taxonómico que ya usa
    ``services/brand_intelligence.py``, para no contradecirlo.

Fuentes: sólo APIs oficiales o contenido público con ToS que lo permitan. El
scraping de plataformas cerradas (Instagram, TikTok, Facebook, X) **no se
implementa**: quedan declaradas como colectores inhabilitados con la API
oficial que haría falta para activarlas.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

from app.collectors.base import (
    ACCESS_API,
    ACCESS_FIXTURE,
    ACCESS_PROHIBITED,
    BaseCollector,
    DisabledCollector,
    FixtureCollector,
    SourcePolicy,
    collector_name,
    register,
    setting,
)
from app.collectors.resolve import CatalogResolver, default_resolver
from app.collectors.sentiment import aggregate_sentiment
from app.config import DB_PATH, section
from app.services.common import parse_date

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "social"

#: Tipos de fuente admitidos por el esquema.
SOURCE_TYPES = ("forum", "social", "community", "blog_comments")

#: Prioridad de dimensiones para elegir el ``topic`` de un agregado.
TOPIC_DIMENSION_PRIORITY = (
    "product_perception", "price_perception", "availability",
    "brand_perception", "cultural_relevance",
)

_HANDLE_RE = re.compile(r"(?<![\w])@[\w.\-]+")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_RE = re.compile(r"[\w.\-]+@[\w\-]+(?:\.[a-z]{2,})+", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?54\s?)?(?:\d[\s\-]?){8,13}(?!\d)")
_USER_PATH_RE = re.compile(r"\b(?:u/|r/u/|/u/)[\w\-]+")


def scrub(text: str | None, *, max_chars: int | None = None) -> str:
    """Deja sólo el texto: sin handles, URLs, mails, teléfonos ni rutas de perfil."""
    if not text:
        return ""
    clean = _URL_RE.sub(" ", str(text))
    clean = _EMAIL_RE.sub(" ", clean)
    clean = _USER_PATH_RE.sub(" ", clean)
    clean = _HANDLE_RE.sub(" ", clean)
    clean = _PHONE_RE.sub(" ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -–—:;,")
    limit = int(max_chars if max_chars is not None
                else setting("social", "max_evidence_chars", default=220))
    if len(clean) > limit:
        clean = clean[:limit].rsplit(" ", 1)[0] + "…"
    return clean


# ── post normalizado (sin identidad) ────────────────────────


@dataclass(frozen=True)
class SocialPost:
    """Observación pública mínima. No tiene —ni puede tener— autor."""

    text: str
    observed_at: date
    source_type: str = "social"
    country_code: str = "AR"


def normalize_post(raw: dict[str, Any], *, default_source: str = "social",
                   default_country: str = "AR") -> SocialPost | None:
    """Convierte un post crudo en :class:`SocialPost`, **descartando la identidad**.

    Sólo se leen tres campos: texto, fecha y tipo de fuente. Cualquier otro dato
    del payload original (autor, id, perfil, geolocalización) se ignora acá y no
    existe aguas abajo.
    """
    text = raw.get("text") or raw.get("body") or raw.get("title") or raw.get("selftext")
    if isinstance(text, str) and raw.get("title") and raw.get("selftext"):
        text = f"{raw['title']} {raw['selftext']}"
    if not text or not str(text).strip():
        return None
    moment = parse_date(raw.get("created_at") or raw.get("date") or raw.get("observed_at"))
    if moment is None:
        return None
    source_type = str(raw.get("source_type") or default_source).lower()
    if source_type not in SOURCE_TYPES:
        source_type = default_source
    country = str(raw.get("country_code") or default_country).upper()[:2]
    return SocialPost(text=str(text).strip(), observed_at=moment,
                      source_type=source_type, country_code=country)


# ── clasificación de tópicos (compartida con brand_intelligence) ──


def _taxonomy() -> dict[str, list[str]]:
    raw = section("brand_intelligence", "taxonomy", default={}) or {}
    return {dim: list(topics or []) for dim, topics in raw.items()}


def _classifier():
    """Usa el léxico de ``brand_intelligence`` si está; si no, no clasifica."""
    try:
        from app.services.brand_intelligence import classify_text
    except ImportError:  # pragma: no cover - el servicio es parte del backend
        return None
    return classify_text


def classify(text: str) -> tuple[str | None, str | None]:
    """``(topic, intent)`` según la taxonomía configurada. Determinístico."""
    classify_text = _classifier()
    if classify_text is None:
        return None, None
    taxonomy = _taxonomy()
    hits = classify_text(text, taxonomy)
    if not hits:
        return None, None
    intent = next((topic for dimension, topic in hits if dimension == "consumer_intent"), None)
    topic = None
    for dimension in TOPIC_DIMENSION_PRIORITY:
        topic = next((t for d, t in hits if d == dimension), None)
        if topic:
            break
    if topic is None:
        topic = next((t for d, t in hits if d != "consumer_intent"), None)
    return topic, intent


# ── agregación ──────────────────────────────────────────────


@dataclass
class _Bucket:
    mention_count: int = 0
    comention_count: int = 0

    def __post_init__(self) -> None:
        self.texts: list[str] = []


class SocialAggregator:
    """Convierte posts públicos en filas de ``social_mention_aggregates``."""

    def __init__(self, resolver: CatalogResolver, *, period_days: int | None = None,
                 max_evidence: int | None = None, min_mentions: int | None = None,
                 focus_brand_id: int | None = None) -> None:
        self.resolver = resolver
        self.period_days = int(period_days if period_days is not None
                               else setting("social", "period_days", default=30))
        self.max_evidence = int(max_evidence if max_evidence is not None
                                else setting("social", "max_evidence_examples", default=3))
        self.min_mentions = int(min_mentions if min_mentions is not None
                                else setting("social", "min_mentions_per_row", default=1))
        self.focus_brand_id: int | None = focus_brand_id
        self.stats: dict[str, int] = {}

    def _period_of(self, moment: date, reference: date) -> tuple[str, str]:
        """Ventana fija de ``period_days`` anclada a la observación más reciente."""
        index = max(0, (reference - moment).days) // self.period_days
        end = reference - timedelta(days=index * self.period_days)
        start = end - timedelta(days=self.period_days - 1)
        return start.isoformat(), end.isoformat()

    def _canonical_pair(self, a: int, b: int) -> tuple[int, int]:
        """Producto de la marca foco primero (lo espera el matching engine)."""
        focus = self.focus_brand_id
        brand_a = self.resolver.brand_of(a)
        brand_b = self.resolver.brand_of(b)
        if focus is not None and brand_b == focus and brand_a != focus:
            return b, a
        if focus is not None and brand_a == focus and brand_b != focus:
            return a, b
        return (a, b) if a <= b else (b, a)

    def aggregate(self, posts: Sequence[SocialPost], *,
                  reference: date | None = None) -> list[dict]:
        """Filas agregadas. Nunca devuelve nada a nivel individuo."""
        self.stats = {}
        posts = [p for p in posts if p and p.text]
        if not posts:
            return []

        # "Hoy" determinístico: la observación más reciente del lote.
        ref = reference or max(p.observed_at for p in posts)

        buckets: dict[tuple, _Bucket] = defaultdict(_Bucket)
        for post in posts:
            self.stats["posts"] = self.stats.get("posts", 0) + 1
            period = self._period_of(post.observed_at, ref)
            # Todo se analiza sobre el texto ya limpio: lo que no se puede
            # citar tampoco se usa para atribuir productos.
            text = scrub(post.text, max_chars=4000)
            if not text:
                self.stats["unattributed"] = self.stats.get("unattributed", 0) + 1
                continue
            topic, intent = classify(text)
            hits = self.resolver.find_products(text)
            product_ids = sorted({hit.product_id for hit in hits})
            named_brands = self.resolver.find_brands(text)

            if product_ids:
                for product_id in product_ids:
                    brand_id = self.resolver.brand_of(product_id)
                    key = (period, post.country_code, post.source_type, brand_id,
                           product_id, None, topic, intent)
                    bucket = buckets[key]
                    bucket.mention_count += 1
                    bucket.texts.append(text)
                    self.stats["product_mentions"] = self.stats.get("product_mentions", 0) + 1

                for i in range(len(product_ids)):
                    for j in range(i + 1, len(product_ids)):
                        a, b = self._canonical_pair(product_ids[i], product_ids[j])
                        key = (period, post.country_code, post.source_type,
                               self.resolver.brand_of(a), a, b, topic, intent)
                        bucket = buckets[key]
                        bucket.mention_count += 1
                        bucket.comention_count += 1
                        bucket.texts.append(text)
                        self.stats["comentions"] = self.stats.get("comentions", 0) + 1
            elif named_brands:
                # Conversación de marca sin producto identificado: se cuenta a
                # nivel marca (no se adivina el producto).
                for brand_id in sorted(named_brands):
                    key = (period, post.country_code, post.source_type, brand_id,
                           None, None, topic, intent)
                    bucket = buckets[key]
                    bucket.mention_count += 1
                    bucket.texts.append(text)
                    self.stats["brand_mentions"] = self.stats.get("brand_mentions", 0) + 1
            else:
                self.stats["unattributed"] = self.stats.get("unattributed", 0) + 1

        rows: list[dict] = []
        for key, bucket in sorted(buckets.items(), key=lambda kv: str(kv[0])):
            (period, country, source_type, brand_id, product_id, co_product_id,
             topic, intent) = key
            if bucket.mention_count < self.min_mentions:
                continue
            evidence = []
            for text in bucket.texts:
                clean = scrub(text)
                if clean and clean not in evidence:
                    evidence.append(clean)
                if len(evidence) >= self.max_evidence:
                    break
            rows.append({
                "period_start": period[0],
                "period_end": period[1],
                "country_code": country,
                "brand_id": brand_id,
                "product_id": product_id,
                "co_product_id": co_product_id,
                "source_type": source_type,
                "mention_count": bucket.mention_count,
                "comention_count": bucket.comention_count,
                "sentiment_score": aggregate_sentiment(bucket.texts),
                "intent": intent,
                "topic": topic,
                "sample_evidence": json.dumps(evidence, ensure_ascii=False),
            })
        self.stats["rows"] = len(rows)
        return rows


def focus_brand_id(db_path: Path | str = DB_PATH) -> int | None:
    """Marca analizada (Nike). ``None`` si la base todavía no existe."""
    try:
        from app.db import query
        rows = query("SELECT id FROM brands WHERE is_focus = 1 ORDER BY id", path=db_path)
    except Exception:  # noqa: BLE001 - sin DB accesible no hay marca foco
        return None
    return int(rows[0]["id"]) if rows else None


# ── colectores ──────────────────────────────────────────────


class SocialFixtureCollector(FixtureCollector):
    """Lee posts públicos desde JSON local y los agrega. Sin red.

    Formato aceptado por archivo: una lista de posts, o
    ``{"source_type": ..., "country_code": ..., "posts": [...]}``.
    Cada post necesita ``text`` y ``created_at``; cualquier otro campo se ignora.
    """

    def __init__(self, name: str = "social_fixture", directory: Path | str = FIXTURE_DIR,
                 *, db_path: Path | str = DB_PATH, resolver: CatalogResolver | None = None,
                 country_code: str = "AR") -> None:
        super().__init__(
            name=name,
            table="social_mention_aggregates",
            directory=directory,
            policy=SourcePolicy(
                source_name="fixtures locales (social)",
                homepage=f"file://{directory}",
                access=ACCESS_FIXTURE,
                terms="Textos de ejemplo del repositorio. Sin adquisición remota.",
                rate_limit_seconds=0.0,
                country_code=country_code,
                stores="conteos agregados + hasta 3 ejemplos públicos sin autor",
                notes="Permite ejercitar toda la cadena social sin internet.",
            ),
            patterns=("*.json",),
            country_code=country_code,
        )
        self.db_path = db_path
        self._resolver = resolver

    @property
    def resolver(self) -> CatalogResolver:
        return self._resolver or default_resolver(self.db_path)

    def _posts(self) -> list[SocialPost]:
        posts: list[SocialPost] = []
        for path in self.files():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("fixture social ilegible %s: %s", path, exc)
                self.bump("files_failed")
                continue
            self.bump("files_read")
            if isinstance(payload, dict):
                default_source = str(payload.get("source_type") or "social")
                default_country = str(payload.get("country_code") or self.country_code)
                raw_posts = payload.get("posts") or []
            else:
                default_source, default_country = "social", self.country_code
                raw_posts = payload
            for raw in raw_posts:
                if not isinstance(raw, dict):
                    continue
                post = normalize_post(raw, default_source=default_source,
                                      default_country=default_country)
                if post is not None:
                    posts.append(post)
        return posts

    def collect(self, since: date | None = None) -> list[dict]:
        self.reset_stats()
        posts = self._posts()
        self.bump("posts", len(posts))
        if not posts:
            return []
        aggregator = SocialAggregator(self.resolver,
                                      focus_brand_id=focus_brand_id(self.db_path))
        rows = aggregator.aggregate(posts)
        self.stats.update({f"agg_{k}": v for k, v in aggregator.stats.items()})
        return rows


def posts_from_reddit_listing(payload: dict[str, Any], *, source_type: str = "forum",
                              country_code: str = "AR") -> list[SocialPost]:
    """Mapea un listing de la API de Reddit a posts anónimos.

    Se leen ``title``/``selftext`` y ``created_utc``. **El campo ``author`` y
    cualquier otro identificador se descartan explícitamente**: no entran al
    modelo ni a la evidencia.
    """
    posts: list[SocialPost] = []
    children = ((payload or {}).get("data") or {}).get("children") or []
    for child in children:
        data = (child or {}).get("data") or {}
        created = data.get("created_utc")
        moment: date | None = None
        if created is not None:
            try:
                from datetime import datetime, timezone
                moment = datetime.fromtimestamp(float(created), tz=timezone.utc).date()
            except (TypeError, ValueError, OSError):
                moment = None
        text = " ".join(filter(None, [str(data.get("title") or ""),
                                      str(data.get("selftext") or "")])).strip()
        if not text or moment is None:
            continue
        posts.append(SocialPost(text=text, observed_at=moment,
                                source_type=source_type, country_code=country_code))
    return posts


class PublicApiSocialCollector(BaseCollector):
    """Colector para APIs sociales oficiales (hoy: Reddit, con credenciales).

    Sin credenciales o sin red devuelve ``[]`` y lo informa. Nunca hace
    scraping: si la plataforma no ofrece API pública, el colector no existe
    (ver los ``DisabledCollector`` de abajo).
    """

    def __init__(self, name: str, policy: SourcePolicy, endpoints: Sequence[str],
                 *, db_path: Path | str = DB_PATH, resolver: CatalogResolver | None = None,
                 source_type: str = "forum") -> None:
        super().__init__(name=name, table="social_mention_aggregates", policy=policy,
                         enabled_by_default=False)
        self.endpoints = tuple(endpoints)
        self.db_path = db_path
        self._resolver = resolver
        self.source_type = source_type

    @property
    def resolver(self) -> CatalogResolver:
        return self._resolver or default_resolver(self.db_path)

    def _credentials(self) -> dict[str, str]:
        return {var: os.environ[var] for var in self.policy.requires if os.environ.get(var)}

    def fetch_posts(self, since: date | None = None) -> list[SocialPost]:
        """Trae posts públicos. Sin credenciales/red devuelve ``[]``."""
        missing = [var for var in self.policy.requires if var not in self._credentials()]
        if missing:
            log.info("colector '%s' sin credenciales (%s): devuelve vacío",
                     self.name, ", ".join(missing))
            self.bump("skipped_no_credentials")
            return []

        from app.collectors.fetch import PoliteFetcher  # import perezoso: sólo con credenciales
        fetcher = PoliteFetcher(self.policy)
        posts: list[SocialPost] = []
        for url in self.endpoints:
            body = fetcher.get(url)
            if not body:
                self.bump("endpoints_unreachable")
                continue
            try:
                payload = json.loads(body)
            except ValueError:
                self.bump("endpoints_unparsable")
                continue
            posts.extend(posts_from_reddit_listing(
                payload, source_type=self.source_type,
                country_code=self.policy.country_code or "AR"))
        return posts

    def collect(self, since: date | None = None) -> list[dict]:
        self.reset_stats()
        posts = self.fetch_posts(since)
        if since is not None:
            posts = [p for p in posts if p.observed_at >= since]
        self.bump("posts", len(posts))
        if not posts:
            return []
        aggregator = SocialAggregator(self.resolver,
                                      focus_brand_id=focus_brand_id(self.db_path))
        rows = aggregator.aggregate(posts)
        self.stats.update({f"agg_{k}": v for k, v in aggregator.stats.items()})
        return rows


# ── fuentes prohibidas: interfaz declarada, cero implementación ──

PROHIBITED_SOURCES: tuple[SourcePolicy, ...] = (
    SourcePolicy(
        source_name="Instagram (perfiles y comentarios públicos)",
        homepage="https://www.instagram.com",
        access=ACCESS_PROHIBITED,
        terms="Los Términos de Meta prohíben la recolección automatizada sin permiso escrito.",
        country_code="AR",
        official_api="Instagram Graph API con app revisada + permisos "
                     "instagram_basic / instagram_manage_insights. Sólo da datos de cuentas "
                     "propias o de Business Discovery, nunca conversación de terceros.",
        notes="Para conversación de marca AR se necesita un proveedor licenciado "
              "(Meta Content Library o partner de datos).",
    ),
    SourcePolicy(
        source_name="TikTok",
        homepage="https://www.tiktok.com",
        access=ACCESS_PROHIBITED,
        terms="Los ToS prohíben scraping; el acceso a datos es sólo por programa oficial.",
        country_code="AR",
        official_api="TikTok Research API (institucional, con solicitud aprobada) o "
                     "Commercial Content API para anuncios.",
    ),
    SourcePolicy(
        source_name="X / Twitter",
        homepage="https://x.com",
        access=ACCESS_PROHIBITED,
        terms="Los ToS prohíben scraping; sólo API paga.",
        country_code="AR",
        official_api="X API v2 (tier Basic/Pro) con endpoint de búsqueda reciente. "
                     "Guardar sólo métricas agregadas y textos sin autor.",
    ),
    SourcePolicy(
        source_name="Facebook (grupos y comentarios)",
        homepage="https://www.facebook.com",
        access=ACCESS_PROHIBITED,
        terms="Prohibido por los Términos de Meta; además los grupos suelen ser privados.",
        country_code="AR",
        official_api="Meta Content Library (acceso académico/licenciado). Grupos privados "
                     "quedan fuera de alcance por diseño.",
    ),
    SourcePolicy(
        source_name="WhatsApp",
        homepage="https://www.whatsapp.com",
        access=ACCESS_PROHIBITED,
        terms="Conversación privada y cifrada: fuera de alcance por privacidad, no sólo por ToS.",
        country_code="AR",
        official_api="Ninguna. No corresponde recolectar conversación privada.",
    ),
)


REDDIT_POLICY = SourcePolicy(
    source_name="Reddit (r/RunningArgentina, r/RunningShoeGeeks, r/argentina)",
    homepage="https://www.reddit.com",
    access=ACCESS_API,
    terms="Reddit Data API: uso no comercial con registro de app y OAuth; el scraping "
          "de HTML está prohibido por los ToS y por robots.txt. Rate limit oficial: "
          "100 QPM por client_id autenticado.",
    rate_limit_seconds=1.0,
    country_code="AR",
    requires=("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"),
    stores="conteos agregados por período + hasta 3 citas públicas sin autor",
    notes="Se guardan agregados: ni author, ni id de post, ni permalink de usuario.",
)


def register_collectors(db_path: Path | str = DB_PATH) -> None:
    """Registra los colectores sociales incluidos."""
    register(SocialFixtureCollector(db_path=db_path))
    register(PublicApiSocialCollector(
        name="reddit_public_api",
        policy=REDDIT_POLICY,
        endpoints=(
            "https://oauth.reddit.com/r/RunningShoeGeeks/search?q=nike&restrict_sr=1&limit=100",
            "https://oauth.reddit.com/r/argentina/search?q=zapatillas&restrict_sr=1&limit=100",
        ),
        db_path=db_path,
        source_type="forum",
    ))
    for policy in PROHIBITED_SOURCES:
        register(DisabledCollector(name=collector_name("blocked", policy.source_name),
                                   table="social_mention_aggregates", policy=policy))
