"""HTTP cortés y parseo de feeds/HTML — sin dependencias nuevas.

Reglas de la casa:

  * **robots.txt manda.** Antes de pedir una URL se consulta el robots.txt del
    host con ``urllib.robotparser``. Si no se puede leer (sin red, 4xx, timeout)
    la respuesta es *no permitido*: en la duda, no se pide.
  * **Rate limit por host**, declarado por la fuente en su ``SourcePolicy``.
  * **Sin red no pasa nada malo**: toda función devuelve ``None``/``[]`` en vez
    de propagar la excepción.
  * Sólo se guardan metadatos (título, URL, fecha) y un extracto breve con
    atribución a la fuente. Nunca el artículo completo.

Parseo con la librería estándar (``xml.etree`` + ``html.parser``): un feed roto
no puede tumbar la corrida.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from app.collectors.base import SourcePolicy, setting

log = logging.getLogger(__name__)


# ── rate limit ──────────────────────────────────────────────


class RateLimiter:
    """Intervalo mínimo entre pedidos al mismo host."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        previous = self._last.get(host)
        now = time.monotonic()
        if previous is not None:
            remaining = self.min_interval - (now - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()


# ── cliente ─────────────────────────────────────────────────


class PoliteFetcher:
    """Cliente HTTP que respeta robots.txt, rate limit y ToS de la fuente."""

    def __init__(self, policy: SourcePolicy, *, timeout: float | None = None,
                 user_agent: str | None = None) -> None:
        self.policy = policy
        self.timeout = float(timeout if timeout is not None
                             else setting("http", "timeout_seconds", default=6.0))
        self.user_agent = user_agent or str(setting("http", "user_agent", default="CI-Collector/1.0"))
        rate = policy.rate_limit_seconds or float(
            setting("http", "default_rate_limit_seconds", default=2.0))
        self.limiter = RateLimiter(rate)
        self._robots: dict[str, RobotFileParser | None] = {}

    # -- robots.txt ----------------------------------------------------------

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        parser: RobotFileParser | None = None
        body = self._raw_get(urljoin(origin, "/robots.txt"), robots_check=False)
        if body is not None:
            parser = RobotFileParser()
            parser.parse(body.splitlines())
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        """True sólo si robots.txt existe y permite explícitamente la URL."""
        if not self.policy.allowed:
            return False
        if not self.policy.respects_robots_txt:  # pragma: no cover - no se usa hoy
            return True
        parser = self._robots_for(url)
        if parser is None:
            log.info("sin robots.txt legible para %s: no se pide (criterio conservador)", url)
            return False
        return bool(parser.can_fetch(self.user_agent, url))

    # -- GET -----------------------------------------------------------------

    def _raw_get(self, url: str, *, robots_check: bool = True) -> str | None:
        try:
            import httpx  # import local: la dependencia es opcional en runtime
        except ImportError:  # pragma: no cover - httpx está en requirements
            log.info("httpx no disponible: la adquisición remota queda inactiva")
            return None

        host = urlparse(url).netloc
        self.limiter.wait(host)
        try:
            response = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"},
            )
        except Exception as exc:  # noqa: BLE001 - sin red el colector devuelve vacío
            log.info("sin acceso a %s (%s): el colector devuelve vacío", url, type(exc).__name__)
            return None
        if response.status_code != 200:
            log.info("%s respondió %s: se descarta", url, response.status_code)
            return None
        return response.text

    def get(self, url: str) -> str | None:
        """Pide una URL si los ToS y robots.txt lo permiten. Nunca lanza."""
        if not self.policy.allowed:
            log.info("fuente '%s' prohibida por ToS: no se pide %s", self.policy.source_name, url)
            return None
        if self.policy.requires:
            log.info("fuente '%s' requiere credenciales (%s)",
                     self.policy.source_name, ", ".join(self.policy.requires))
        if not self.allowed(url):
            return None
        return self._raw_get(url)


# ── feeds RSS / Atom ────────────────────────────────────────

_NS = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/"}


@dataclass(frozen=True)
class FeedItem:
    """Ítem de feed ya normalizado (metadatos + resumen breve)."""

    title: str
    url: str
    summary: str
    published_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "summary": self.summary,
                "published_at": self.published_at}


_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RFC822_RE = re.compile(
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{4})", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def normalize_date(value: str | None) -> str | None:
    """Fecha de publicación a ISO ``YYYY-MM-DD``. ``None`` si no se entiende."""
    if not value:
        return None
    text = str(value).strip()
    iso = _DATE_RE.search(text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    rfc = _RFC822_RE.search(text)
    if rfc:
        day, month, year = int(rfc.group(1)), _MONTHS[rfc.group(2).lower()], int(rfc.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def parse_feed(xml_text: str) -> list[FeedItem]:
    """Parsea RSS 2.0 o Atom. Devuelve ``[]`` si el XML está roto."""
    try:
        from xml.etree import ElementTree
        root = ElementTree.fromstring(xml_text)
    except Exception as exc:  # noqa: BLE001
        log.info("feed ilegible (%s)", type(exc).__name__)
        return []

    items: list[FeedItem] = []

    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = _child_text(node, ("title",))
        link = _child_text(node, ("link", "id", "guid")) or _atom_link(node)
        summary = _child_text(node, ("description", "summary", "content", "encoded"))
        published = _child_text(node, ("pubDate", "published", "updated", "date"))
        if not title and not link:
            continue
        items.append(FeedItem(
            title=html_to_text(title or ""),
            url=(link or "").strip(),
            summary=html_to_text(summary or ""),
            published_at=normalize_date(published),
        ))
    return items


def _child_text(node, names: tuple[str, ...]) -> str | None:
    for child in node:
        tag = child.tag.split("}")[-1]
        if tag in names:
            text = (child.text or "").strip()
            if text:
                return text
    return None


def _atom_link(node) -> str | None:
    for child in node:
        if child.tag.split("}")[-1] == "link":
            href = child.attrib.get("href")
            if href:
                return href
    return None


# ── HTML ────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Extrae texto visible, título y metadatos básicos de un HTML."""

    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self.published: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._list_items: list[str] = []
        self._in_li = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "li":
            self._in_li = True
            self._list_items.append("")
        if tag == "meta":
            data = {k: (v or "") for k, v in attrs}
            key = (data.get("property") or data.get("name") or "").lower()
            if key in ("article:published_time", "datepublished", "pubdate", "date"):
                self.published = normalize_date(data.get("content"))
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "li":
            self._in_li = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title and self.title is None:
            self.title = data.strip() or None
        self.parts.append(data)
        if self._in_li and self._list_items:
            self._list_items[-1] += data

    @property
    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)

    @property
    def list_items(self) -> list[str]:
        return [re.sub(r"\s+", " ", item).strip() for item in self._list_items if item.strip()]


def html_to_text(html: str) -> str:
    """Texto visible de un fragmento HTML (o del texto plano tal cual)."""
    if not html:
        return ""
    if "<" not in html:
        return re.sub(r"\s+", " ", unescape(html)).strip()
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", unescape(html)).strip()
    return re.sub(r"[ \t]+", " ", parser.text).strip()


def parse_html_document(html: str) -> dict[str, Any]:
    """Título, texto e ítems de lista de un artículo HTML."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        return {"title": None, "text": re.sub(r"<[^>]+>", " ", html), "published_at": None,
                "list_items": []}
    text = parser.text
    title = parser.title
    if not title:
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        title = html_to_text(heading.group(1)) if heading else None
    return {"title": title, "text": text, "published_at": parser.published,
            "list_items": parser.list_items}
