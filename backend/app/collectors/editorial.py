"""Colectores editoriales -> filas de ``editorial_mentions``.

Qué se adquiere y qué NO:

  * Se prefieren **feeds RSS/Atom publicados por la propia fuente** — el medio
    los publica justamente para ser leídos por máquinas — y APIs oficiales.
  * De cada artículo se guardan **metadatos** (fuente, URL, título, fecha) y un
    **extracto breve con atribución**, nunca el texto completo: el contenido
    editorial está protegido por derecho de autor.
  * Antes de pedir cualquier URL se consulta ``robots.txt`` y se respeta el
    rate limit declarado por la fuente (``fetch.PoliteFetcher``).
  * Las fuentes de red arrancan **apagadas**: se habilitan una por una desde
    ``collectors.sources.<nombre>.enabled`` en ``config/weights.yaml``, después
    de verificar términos y robots.txt (ver ``backend/docs/collectors.md``).

Sin red, sin feed o con el feed roto, cada colector devuelve ``[]``: el
pipeline sigue corriendo igual.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from app.collectors.base import (
    ACCESS_FEED,
    ACCESS_PROHIBITED,
    BaseCollector,
    DisabledCollector,
    FixtureCollector,
    SourcePolicy,
    collector_name,
    register,
    setting,
)
from app.collectors.extract import Document, extract_mentions
from app.collectors.fetch import PoliteFetcher, normalize_date, parse_feed, parse_html_document
from app.collectors.resolve import CatalogResolver, default_resolver
from app.config import DB_PATH

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "editorial"


def documents_from_feed(xml_text: str, *, source_name: str,
                        country_code: str | None) -> list[Document]:
    """Ítems de un feed RSS/Atom convertidos en documentos analizables."""
    return [
        Document(
            title=item.title,
            text=item.summary,
            url=item.url,
            source_name=source_name,
            published_at=item.published_at,
            country_code=country_code,
        )
        for item in parse_feed(xml_text)
        if item.title or item.summary
    ]


def document_from_html(html: str, *, url: str, source_name: str,
                       country_code: str | None,
                       published_at: str | None = None) -> Document | None:
    """Artículo HTML -> documento (título, texto visible e ítems de lista)."""
    parsed = parse_html_document(html)
    if not parsed.get("title") and not parsed.get("text"):
        return None
    return Document(
        title=parsed.get("title") or "",
        text=parsed.get("text") or "",
        url=url,
        source_name=source_name,
        published_at=published_at or parsed.get("published_at"),
        country_code=country_code,
        list_items=tuple(parsed.get("list_items") or ()),
    )


class EditorialCollectorMixin:
    """Convierte documentos en filas usando extract + resolve."""

    db_path: Path | str = DB_PATH
    _resolver: CatalogResolver | None = None

    @property
    def resolver(self) -> CatalogResolver:
        return self._resolver or default_resolver(self.db_path)

    def rows_from_documents(self, documents: Sequence[Document]) -> list[dict]:
        resolver = self.resolver
        before = dict(resolver.stats)
        rows: list[dict] = []
        for doc in documents:
            try:
                rows.extend(extract_mentions(doc, resolver))
            except Exception as exc:  # noqa: BLE001 - un artículo raro no frena la corrida
                log.warning("no se pudo extraer de %s: %s", doc.url or doc.title, exc)
                self.bump("documents_failed")  # type: ignore[attr-defined]
        self.bump("documents", len(documents))  # type: ignore[attr-defined]
        for key in ("accepted", "rejected_low_score", "rejected_ambiguous",
                    "rejected_version_conflict", "rejected_brand_conflict"):
            delta = resolver.stats.get(key, 0) - before.get(key, 0)
            if delta:
                self.bump(f"resolve_{key}", delta)  # type: ignore[attr-defined]
        return rows


class EditorialFixtureCollector(EditorialCollectorMixin, FixtureCollector):
    """Lee artículos locales (RSS/Atom, HTML o JSON) — la cadena completa sin red."""

    def __init__(self, name: str = "editorial_fixture", directory: Path | str = FIXTURE_DIR,
                 *, db_path: Path | str = DB_PATH, resolver: CatalogResolver | None = None,
                 country_code: str = "AR") -> None:
        FixtureCollector.__init__(
            self,
            name=name,
            table="editorial_mentions",
            directory=directory,
            policy=SourcePolicy(
                source_name="fixtures locales (editorial)",
                homepage=f"file://{directory}",
                access="fixture",
                terms="Artículos de ejemplo del repositorio. Sin adquisición remota.",
                rate_limit_seconds=0.0,
                country_code=country_code,
                stores="metadatos + extracto breve",
                notes="Permite probar extracción y resolución sin internet.",
            ),
            patterns=("*.xml", "*.rss", "*.atom", "*.html", "*.htm", "*.json"),
            country_code=country_code,
        )
        self.db_path = db_path
        self._resolver = resolver

    def documents_of(self, path: Path) -> list[Document]:
        raw = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in (".xml", ".rss", ".atom"):
            return documents_from_feed(raw, source_name=path.stem.replace("_", " ").title(),
                                       country_code=self.country_code)
        if suffix in (".html", ".htm"):
            meta_path = path.with_suffix(".meta.json")
            meta: dict[str, Any] = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            doc = document_from_html(
                raw,
                url=str(meta.get("url") or f"file://{path.name}"),
                source_name=str(meta.get("source_name") or path.stem.replace("_", " ").title()),
                country_code=str(meta.get("country_code") or self.country_code),
                published_at=normalize_date(meta.get("published_at")),
            )
            return [doc] if doc else []
        if suffix == ".json":
            if path.name.endswith(".meta.json"):
                return []
            payload = json.loads(raw)
            items = payload if isinstance(payload, list) else payload.get("articles") or []
            return [
                Document(
                    title=str(item.get("title") or ""),
                    text=str(item.get("text") or item.get("excerpt") or ""),
                    url=str(item.get("url") or ""),
                    source_name=str(item.get("source_name") or path.stem),
                    published_at=normalize_date(item.get("published_at")),
                    country_code=str(item.get("country_code") or self.country_code),
                    list_items=tuple(item.get("list_items") or ()),
                )
                for item in items if isinstance(item, dict)
            ]
        return []

    def parse_file(self, path: Path, since: date | None) -> list[dict]:
        documents = self.documents_of(path)
        return self.rows_from_documents(documents)


class FeedEditorialCollector(EditorialCollectorMixin, BaseCollector):
    """Colector genérico de feeds RSS/Atom de medios especializados.

    Agregar una fuente nueva es instanciar esta clase con su ``SourcePolicy``
    y su URL de feed. No hay que tocar nada más del backend.
    """

    def __init__(self, name: str, policy: SourcePolicy, feed_urls: Sequence[str],
                 *, db_path: Path | str = DB_PATH,
                 resolver: CatalogResolver | None = None) -> None:
        BaseCollector.__init__(self, name=name, table="editorial_mentions", policy=policy,
                               enabled_by_default=False)
        self.feed_urls = tuple(feed_urls)
        self.db_path = db_path
        self._resolver = resolver

    def collect(self, since: date | None = None) -> list[dict]:
        self.reset_stats()
        fetcher = PoliteFetcher(self.policy)
        max_items = int(setting("http", "max_items_per_run", default=60))
        documents: list[Document] = []
        for url in self.feed_urls:
            body = fetcher.get(url)
            if not body:
                self.bump("feeds_unreachable")
                continue
            self.bump("feeds_read")
            documents.extend(documents_from_feed(
                body, source_name=self.policy.source_name,
                country_code=self.policy.country_code))
        if since is not None:
            documents = [d for d in documents
                         if not d.published_at or d.published_at >= since.isoformat()]
        return self.rows_from_documents(documents[:max_items])


# ── fuentes propuestas ──────────────────────────────────────
# Estado legal declarado por fuente. Todas arrancan DESHABILITADAS: antes de
# encender una hay que verificar su robots.txt y sus términos vigentes
# (`python -m app.collectors --list` muestra la ficha de cada una).

FEED_SOURCES: tuple[tuple[str, SourcePolicy, tuple[str, ...]], ...] = (
    (
        "believe_in_the_run",
        SourcePolicy(
            source_name="Believe in the Run",
            homepage="https://believeintherun.com",
            access=ACCESS_FEED,
            terms="Feed RSS público del propio medio. Contenido con copyright: se "
                  "guardan título, URL, fecha y un extracto breve con atribución.",
            rate_limit_seconds=3.0,
            country_code="US",
            stores="metadatos + extracto ≤280 caracteres con enlace a la nota",
            notes="Fuente rica en 'X vs Y' y rankings de daily trainers.",
        ),
        ("https://believeintherun.com/feed/",),
    ),
    (
        "road_trail_run",
        SourcePolicy(
            source_name="Road Trail Run",
            homepage="https://www.roadtrailrun.com",
            access=ACCESS_FEED,
            terms="Feed Atom de Blogger, público. Sólo metadatos + extracto.",
            rate_limit_seconds=3.0,
            country_code="US",
            notes="Reviews comparativas y rankings anuales (super trainers, max cushion).",
        ),
        ("https://www.roadtrailrun.com/feeds/posts/default?alt=rss",),
    ),
    (
        "doctors_of_running",
        SourcePolicy(
            source_name="Doctors of Running",
            homepage="https://www.doctorsofrunning.com",
            access=ACCESS_FEED,
            terms="Feed Atom de Blogger, público. Sólo metadatos + extracto.",
            rate_limit_seconds=3.0,
            country_code="US",
            notes="Muchas notas 'X vs Y' con criterio biomecánico.",
        ),
        ("https://www.doctorsofrunning.com/feeds/posts/default?alt=rss",),
    ),
    (
        "runners_world",
        SourcePolicy(
            source_name="Runner's World",
            homepage="https://www.runnersworld.com",
            access=ACCESS_FEED,
            terms="RSS público de Hearst. Los ToS prohíben reproducir el artículo: "
                  "se almacenan sólo metadatos y un extracto corto con enlace.",
            rate_limit_seconds=5.0,
            country_code="US",
            stores="metadatos + extracto ≤280 caracteres con enlace canónico",
            notes="Fuente principal de 'best running shoes' y guías de compra.",
        ),
        ("https://www.runnersworld.com/rss/all.xml/",),
    ),
    (
        "google_news_ar_running",
        SourcePolicy(
            source_name="Google News (consulta AR)",
            homepage="https://news.google.com",
            access=ACCESS_FEED,
            terms="Feed RSS de resultados de Google News: devuelve titular, enlace y "
                  "fecha. No se descarga el artículo del medio de destino.",
            rate_limit_seconds=5.0,
            country_code="AR",
            stores="titular + enlace + fecha (sin extracto del medio de destino)",
            notes="Cobertura de medios argentinos sin scrapear cada sitio.",
        ),
        (
            "https://news.google.com/rss/search?q=zapatillas+running+nike+adidas"
            "&hl=es-419&gl=AR&ceid=AR:es-419",
        ),
    ),
)

#: Fuentes que NO se pueden adquirir automáticamente (interfaz declarada, sin
#: implementación posible dentro de sus términos).
PROHIBITED_EDITORIAL: tuple[SourcePolicy, ...] = (
    SourcePolicy(
        source_name="Wirecutter / The New York Times",
        homepage="https://www.nytimes.com/wirecutter/",
        access=ACCESS_PROHIBITED,
        terms="Los ToS del NYT prohíben expresamente el crawling y el uso automatizado "
              "del contenido sin licencia.",
        country_code="US",
        official_api="Licencia de contenido del NYT (o su API de artículos con "
                     "acuerdo comercial). Sin eso, la fuente queda fuera.",
    ),
    SourcePolicy(
        source_name="Mercado Libre — reseñas y preguntas",
        homepage="https://www.mercadolibre.com.ar",
        access=ACCESS_PROHIBITED,
        terms="Los términos prohíben el scraping; hay API oficial con app registrada.",
        country_code="AR",
        official_api="API de Mercado Libre (OAuth de aplicación) para reviews y precios. "
                     "Sería el camino correcto para reviews AR, no scraping del HTML.",
        notes="Alimentaría `reviews`, no `editorial_mentions`.",
    ),
)


def register_collectors(db_path: Path | str = DB_PATH) -> None:
    """Registra los colectores editoriales incluidos."""
    register(EditorialFixtureCollector(db_path=db_path))
    for name, policy, feeds in FEED_SOURCES:
        register(FeedEditorialCollector(name=name, policy=policy, feed_urls=feeds,
                                        db_path=db_path))
    for policy in PROHIBITED_EDITORIAL:
        register(DisabledCollector(name=collector_name("blocked", policy.source_name),
                                   table="editorial_mentions", policy=policy))
