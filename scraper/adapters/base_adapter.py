# -*- coding: utf-8 -*-
"""
base_adapter.py — Adapter base para scrapers del Nike Dashboard.

Arquitectura:
  - Cada retailer hereda de BaseAdapter e implementa scrape()
  - Cascada de fallbacks: requests → curl_cffi → playwright
  - Retry automático con backoff exponencial
  - Alertas por email a nicolas.crespo@southbay.com.ar si falla
  - Soporte para scraping por StyleColor (Nike) o por catálogo completo
  - Categorización por silueta/franchise via OpenAI (opcional, con cache)
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ── Config ────────────────────────────────────────────────────
ALERT_EMAIL     = os.getenv("ALERT_EMAIL",     "nicolas.crespo@southbay.com.ar")
SMTP_HOST       = os.getenv("SMTP_HOST",       "smtp.gmail.com")
SMTP_PORT       = int(os.getenv("SMTP_PORT",   "587"))
SMTP_USER       = os.getenv("SMTP_USER",       "")
SMTP_PASS       = os.getenv("SMTP_PASS",       "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY",  "")
DATABASE_URL    = os.getenv("DATABASE_URL",    "")

# Siluetas válidas del vocabulario Nike
NIKE_SILUETAS = {
    "FOOTWEAR", "BAGS", "SHIRTS", "PANTS", "SHORTS", "JACKETS",
    "BRAS", "TIGHTS", "SOCKS", "CAPS", "BACKPACK", "JERSEY",
    "DRESS", "VEST", "HOODIE", "TRACKSUIT", "UNDERWEAR", "OTHER",
}

# Categorías válidas (deportes)
NIKE_CATEGORIES = {
    "RUNNING", "FOOTBALL/SOCCER", "BASKETBALL", "TRAINING",
    "SPORTSWEAR", "SWIMMING", "TENNIS", "GOLF", "JORDAN",
    "OUTDOOR", "LIFESTYLE", "OTHER",
}


# ── Data classes ─────────────────────────────────────────────

@dataclass
class ScrapedProduct:
    """Producto scrapeado, compatible con el schema pricing_data."""
    scraper:                  str
    canal:                    str
    marca:                    str          # NIKE | ADIDAS | PUMA
    season:                   str = "FA26"
    style_color:              Optional[str] = None
    product_code_competitor:  Optional[str] = None
    marketing_name:           Optional[str] = None
    product_name_competitor:  Optional[str] = None
    division:                 Optional[str] = None
    category:                 Optional[str] = None
    category_competitor:      Optional[str] = None
    franchise_scrapper:       Optional[str] = None
    franchise_competitor:     Optional[str] = None
    silueta:                  Optional[str] = None
    gender:                   Optional[str] = None
    gender_competitor:        Optional[str] = None
    division_competitor:      Optional[str] = None
    size_available_competitor: Optional[int] = None
    text_sizes_competitor:    Optional[str] = None
    link_pdp_competitor:      Optional[str] = None
    competitor_full_price:    Optional[float] = None
    competitor_markdown:      Optional[float] = None
    competitor_final_price:   Optional[float] = None
    competitor_shipping:      Optional[float] = None
    cuotas_competitor:        Optional[str] = None
    nike_full_price:          Optional[float] = None
    nike_final_price:         Optional[float] = None
    gap_final_price_pct:      Optional[float] = None
    bml_final_price:          Optional[str] = None
    precio_sugerido:          Optional[float] = None
    extra:                    Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrapeResult:
    scraper:    str
    success:    bool
    products:   List[ScrapedProduct] = field(default_factory=list)
    error:      Optional[str] = None
    duration_s: float = 0.0
    run_id:     str = field(default_factory=lambda: str(uuid.uuid4()))


# ── Base Adapter ──────────────────────────────────────────────

class BaseAdapter(ABC):
    """
    Clase base para todos los scrapers del Nike Dashboard.

    Subclases deben implementar:
        scrape() → List[ScrapedProduct]

    Opcionalmente pueden sobreescribir:
        scrape_by_style_color(style_colors) → para scraping dirigido por SKU Nike
    """

    # Configuración por subclase
    SCRAPER_NAME: str = "base"
    CANAL:        str = "RETAIL_ARG"
    MARCA:        str = "NIKE"
    BASE_URL:     str = ""

    # Timeouts y retries
    REQUEST_TIMEOUT   = 30
    MAX_RETRIES       = 3
    BACKOFF_FACTOR    = 2.0
    INTER_REQUEST_DELAY = 1.5   # segundos entre requests

    # User agents rotativos
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    ]

    def __init__(self):
        self.log        = logging.getLogger(self.SCRAPER_NAME)
        self._session   = self._build_session()
        self._ua_idx    = 0
        self._franchise_cache: Dict[str, str] = self._load_cache("franchise_cache.json")
        self._category_cache:  Dict[str, str] = self._load_cache("category_cache.json")

    # ── Session con retry ─────────────────────────────────────

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=self.MAX_RETRIES,
            backoff_factor=self.BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://",  HTTPAdapter(max_retries=retry))
        return s

    def _next_ua(self) -> str:
        ua = self.USER_AGENTS[self._ua_idx % len(self.USER_AGENTS)]
        self._ua_idx += 1
        return ua

    # ── HTTP helpers ──────────────────────────────────────────

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET con fallback: requests → curl_cffi → error."""
        headers = kwargs.pop("headers", {})
        headers.setdefault("User-Agent", self._next_ua())
        headers.setdefault("Accept-Language", "es-AR,es;q=0.9,en;q=0.8")

        # Intento 1: requests estándar
        try:
            resp = self._session.get(
                url, headers=headers, timeout=self.REQUEST_TIMEOUT, **kwargs
            )
            if resp.status_code < 400:
                return resp
            self.log.warning("GET %s → %d, probando curl_cffi...", url, resp.status_code)
        except Exception as e:
            self.log.warning("GET %s requests error: %s, probando curl_cffi...", url, e)

        # Intento 2: curl_cffi (impersonation Chrome)
        try:
            from curl_cffi import requests as cffi_requests
            resp = cffi_requests.get(
                url, headers=headers, impersonate="chrome124", timeout=self.REQUEST_TIMEOUT
            )
            if resp.status_code < 400:
                return resp
        except ImportError:
            self.log.debug("curl_cffi no instalado")
        except Exception as e:
            self.log.warning("curl_cffi error: %s", e)

        # Intento 3: Playwright headless
        return self._get_playwright(url, headers)

    def _get_playwright(self, url: str, headers: dict) -> requests.Response:
        """Fallback con Playwright. Devuelve un Response-like."""
        try:
            from playwright.sync_api import sync_playwright

            class _FakeResponse:
                def __init__(self, text: str, status: int = 200):
                    self.text      = text
                    self.status_code = status
                    self.content   = text.encode()
                def json(self):
                    import json
                    return json.loads(self.text)
                def raise_for_status(self): pass

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page    = browser.new_page(extra_http_headers=headers)
                page.goto(url, timeout=60_000, wait_until="networkidle")
                content = page.content()
                browser.close()
                return _FakeResponse(content)
        except Exception as e:
            self.log.error("Playwright fallback falló: %s", e)
            raise RuntimeError(f"Todos los métodos de fetch fallaron para {url}: {e}")

    def sleep(self, seconds: Optional[float] = None):
        time.sleep(seconds if seconds is not None else self.INTER_REQUEST_DELAY)

    # ── Abstract ──────────────────────────────────────────────

    @abstractmethod
    def scrape(self) -> List[ScrapedProduct]:
        """Scraping completo del catálogo. Implementar en subclase."""
        ...

    def scrape_by_style_color(self, style_colors: List[str]) -> List[ScrapedProduct]:
        """
        Scraping dirigido por lista de StyleColors de Nike.
        Por defecto corre scrape() completo y filtra.
        Subclases pueden sobreescribir para mayor eficiencia.
        """
        all_products = self.scrape()
        return [p for p in all_products if p.style_color in style_colors]

    # ── Runner ────────────────────────────────────────────────

    def run(self, style_colors: Optional[List[str]] = None) -> ScrapeResult:
        """
        Ejecuta el scraper con manejo de errores, timing y alertas.
        Si style_colors es provisto, usa scrape_by_style_color().
        """
        start = time.time()
        self.log.info("▶ Iniciando %s", self.SCRAPER_NAME)

        try:
            if style_colors:
                products = self.scrape_by_style_color(style_colors)
            else:
                products = self.scrape()

            duration = time.time() - start
            self.log.info("✅ %s: %d productos en %.1fs", self.SCRAPER_NAME, len(products), duration)

            return ScrapeResult(
                scraper=self.SCRAPER_NAME,
                success=True,
                products=products,
                duration_s=duration,
            )

        except Exception as e:
            duration = time.time() - start
            error_msg = str(e)
            self.log.error("❌ %s falló en %.1fs: %s", self.SCRAPER_NAME, duration, error_msg)

            # Enviar alerta
            self._send_alert(
                subject=f"[ALERTA] Scraper {self.SCRAPER_NAME} falló",
                body=f"El scraper {self.SCRAPER_NAME} falló después de {duration:.1f}s.\n\nError:\n{error_msg}",
            )

            return ScrapeResult(
                scraper=self.SCRAPER_NAME,
                success=False,
                error=error_msg,
                duration_s=duration,
            )

    # ── Alertas ───────────────────────────────────────────────

    def _send_alert(self, subject: str, body: str):
        """Envía email de alerta. Silencioso si no hay credenciales SMTP."""
        if not SMTP_USER or not SMTP_PASS:
            self.log.warning("SMTP no configurado — alerta no enviada: %s", subject)
            self._log_alert_to_db(subject, body)
            return

        try:
            msg = MIMEMultipart()
            msg["From"]    = SMTP_USER
            msg["To"]      = ALERT_EMAIL
            msg["Subject"] = subject

            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
              <div style="background:#111;padding:20px;border-radius:8px 8px 0 0">
                <h2 style="color:#E31837;margin:0">⚠️ Nike Dashboard Alert</h2>
              </div>
              <div style="background:#f9f9f9;padding:20px;border:1px solid #eee">
                <h3 style="color:#333">{subject}</h3>
                <pre style="background:#fff;padding:15px;border-radius:6px;border:1px solid #ddd;font-size:12px;overflow-x:auto">{body}</pre>
                <p style="color:#999;font-size:11px;margin-top:20px">
                  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · Nike Dashboard · Southbay
                </p>
              </div>
            </div>
            """
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)

            self.log.info("📧 Alerta enviada a %s", ALERT_EMAIL)
        except Exception as e:
            self.log.error("Error enviando alerta: %s", e)
        finally:
            self._log_alert_to_db(subject, body)

    def _log_alert_to_db(self, subject: str, body: str):
        """Persiste la alerta en la tabla scraper_alerts de Supabase."""
        if not DATABASE_URL:
            return
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL, sslmode="require")
            conn.autocommit = True
            cur  = conn.cursor()
            cur.execute(
                "INSERT INTO scraper_alerts (scraper_name, alert_type, message) VALUES (%s, %s, %s)",
                (self.SCRAPER_NAME, "scraper_failed", f"{subject}\n\n{body}"),
            )
            conn.close()
        except Exception:
            pass  # No bloquear si la DB no está disponible

    # ── Clasificación IA ──────────────────────────────────────

    def classify_franchise(self, product_name: str, style_color: Optional[str] = None) -> Optional[str]:
        """
        Clasifica la franchise (modelo) de un producto usando OpenAI con cache.
        Solo para División Footwear (calzado).
        """
        key = f"{self.MARCA}|{style_color or product_name}"
        if key in self._franchise_cache:
            return self._franchise_cache[key]

        if not OPENAI_API_KEY:
            return None

        try:
            import openai
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Dado el nombre de producto de {self.MARCA}: '{product_name}', "
                        "¿cuál es la franchise/modelo de zapatilla (ej: Samba OG, Superstar, Suede, "
                        "Air Force 1, Pegasus, etc.)? Responde SOLO con el nombre del modelo, "
                        "sin explicaciones. Si no hay modelo identificable, responde 'OTHER'."
                    ),
                }],
                max_tokens=30,
                temperature=0,
            )
            franchise = resp.choices[0].message.content.strip()
            self._franchise_cache[key] = franchise
            self._save_cache("franchise_cache.json", self._franchise_cache)
            return franchise
        except Exception as e:
            self.log.debug("classify_franchise error: %s", e)
            return None

    def classify_silueta(self, product_name: str, division: str = "") -> str:
        """
        Clasifica la silueta del producto. Primero intenta reglas heurísticas,
        luego OpenAI como fallback.
        """
        name_upper = product_name.upper()

        # Reglas heurísticas primero (gratis, sin API)
        if any(k in name_upper for k in ["ZAPATILLA", "SNEAKER", "SHOE", "CALZADO", "BOOT"]):
            return "FOOTWEAR"
        if any(k in name_upper for k in ["REMERA", "CAMISETA", "SHIRT", "TEE", "JERSEY", "POLO"]):
            return "SHIRTS"
        if any(k in name_upper for k in ["PANTALON", "PANT", "LEGGING", "TIGHT", "JOGGER"]):
            return "PANTS"
        if any(k in name_upper for k in ["SHORT", "BERMUDA"]):
            return "SHORTS"
        if any(k in name_upper for k in ["CAMPERA", "JACKET", "HOODIE", "BUZO", "SWEATER"]):
            return "JACKETS"
        if any(k in name_upper for k in ["MOCHILA", "BOLSO", "BAG", "BACKPACK", "WAIST"]):
            return "BAGS"
        if any(k in name_upper for k in ["CALCETIN", "SOCK", "MEDIA"]):
            return "SOCKS"
        if any(k in name_upper for k in ["GORRA", "CAP", "HAT", "VISERA"]):
            return "CAPS"
        if any(k in name_upper for k in ["BRA", "TOP", "CORPIÑO"]):
            return "BRAS"

        if division.upper() in ("FW", "FOOTWEAR", "FOOTWEAR DIVISION"):
            return "FOOTWEAR"
        if division.upper() in ("AP", "APPAREL", "APPAREL DIVISION"):
            return "SHIRTS"
        if division.upper() in ("EQ", "EQUIPMENT", "EQUIPMENT DIVISION"):
            return "BAGS"

        return "OTHER"

    def calculate_bml(
        self,
        competitor_price: float,
        nike_price: float,
        threshold: float = 0.02,
    ) -> str:
        """
        Calcula BML (Beat/Meet/Lose) comparando precio competencia vs Nike.
        BEAT = competencia más barata que Nike (Nike pierde)
        MEET = precio similar (±threshold%)
        LOSE = Nike más barata (Nike gana)
        """
        if not competitor_price or not nike_price:
            return "N/D"
        gap = (competitor_price - nike_price) / nike_price
        if gap < -threshold:
            return "BEAT"   # competencia BEAT a Nike (más barata)
        if gap > threshold:
            return "LOSE"   # Nike LOSE (más cara)
        return "MEET"

    def calculate_gap_pct(self, competitor_price: float, nike_price: float) -> Optional[float]:
        if not competitor_price or not nike_price:
            return None
        return (competitor_price - nike_price) / nike_price

    # ── Cache ─────────────────────────────────────────────────

    def _cache_path(self, filename: str) -> Path:
        cache_dir = Path(__file__).parent.parent / "cache"
        cache_dir.mkdir(exist_ok=True)
        return cache_dir / filename

    def _load_cache(self, filename: str) -> Dict[str, str]:
        p = self._cache_path(filename)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cache(self, filename: str, data: Dict[str, str]):
        try:
            p = self._cache_path(filename)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.log.debug("Error guardando cache %s: %s", filename, e)

    # ── DB ────────────────────────────────────────────────────

    def save_to_db(self, products: List[ScrapedProduct]) -> int:
        """Guarda los productos scrapeados en pricing_data de Supabase."""
        if not DATABASE_URL or not products:
            return 0
        try:
            import psycopg2
            import psycopg2.extras

            conn = psycopg2.connect(DATABASE_URL, sslmode="require")
            conn.autocommit = False
            cur  = conn.cursor()

            rows = []
            for p in products:
                rows.append((
                    datetime.now().date(), p.scraper, p.canal, p.marca, p.season,
                    p.style_color, p.product_code_competitor, p.marketing_name,
                    p.division, p.category, p.franchise_scrapper, p.gender,
                    None, None, None,
                    p.product_code_competitor, p.product_name_competitor,
                    p.category_competitor, p.division_competitor,
                    p.franchise_competitor, p.gender_competitor,
                    p.size_available_competitor, None, p.link_pdp_competitor,
                    p.competitor_full_price, p.competitor_markdown, p.competitor_final_price,
                    p.competitor_shipping, None, p.cuotas_competitor,
                    p.nike_full_price, None, p.nike_final_price,
                    None, None, None,
                    p.gap_final_price_pct, p.gap_final_price_pct, None,
                    p.bml_final_price, p.bml_final_price, None, None,
                    None, None, None, None, None, None, None, None, None,
                    None, None, None, None,
                    p.text_sizes_competitor, None, p.precio_sugerido, None, p.silueta,
                ))

            sql = """
                INSERT INTO pricing_data (
                    fecha_corrida, scraper, canal, marca, season,
                    style_color, product_code_competitor, marketing_name,
                    division, category, franchise_scrapper, gender,
                    gama_botin, plato, nddc,
                    productcode_competitor, product_name_competitor,
                    category_competitor, division_competitor,
                    franchise_competitor, gender_competitor,
                    size_available_competitor, size_available_nike, link_pdp_competitor,
                    competitor_full_price, competitor_markdown, competitor_final_price,
                    competitor_shipping, competitor_price_shipping, cuotas_competitor,
                    nike_full_price, nike_markdown, nike_final_price,
                    nike_shipping, nike_price_shipping, cuotas_nike,
                    gap_final_price_pct, gap_full_price_pct, gap_shipping_pct,
                    bml_final_price, bml_full_price, bml_with_shipping, bml_cuotas,
                    fx_ars_usd, competitor_price_usd, competitor_price_usd_iva,
                    competitor_price_usd_iva_bf, gap_full_price_usd,
                    gap_full_price_usd_iva, gap_full_price_usd_iva_bf,
                    gap_final_price_usd, gap_markdown_price_usd,
                    bml_vs_nikear, bml_vs_nikear_iva, bml_vs_nikear_iva_bf,
                    source_file, text_sizes_nike, text_sizes_competitor,
                    precio_sugerido, price_index, silueta
                ) VALUES %s
            """
            psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
            conn.commit()
            inserted = len(rows)
            conn.close()
            self.log.info("💾 %d productos guardados en DB", inserted)
            return inserted
        except Exception as e:
            self.log.error("Error guardando en DB: %s", e)
            return 0
