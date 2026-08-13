# -*- coding: utf-8 -*-
"""
nike_ar_adapter.py — Scraper de nike.com.ar en vivo por StyleColor.

Estrategia:
  1. Para cada StyleColor busca en la API de Nike AR
  2. Extrae precio, nombre, talles disponibles, URL PDP
  3. Fallback: si la API falla, usa el StatusBook cargado en DB
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base_adapter import BaseAdapter, ScrapedProduct

NIKE_AR_API   = "https://api.nike.com/crt/v1/pdp/product"
NIKE_AR_SEARCH = "https://api.nike.com/crt/v1/plp/threads"


class NikeARAdapter(BaseAdapter):
    SCRAPER_NAME = "nike_ar_general"
    CANAL        = "Nike"
    MARCA        = "NIKE"
    BASE_URL     = "https://www.nike.com/ar"

    # Headers específicos para la API de Nike
    NIKE_HEADERS = {
        "Nike-Country":  "AR",
        "Nike-Language": "es-AR",
        "Accept":        "application/json",
    }

    def scrape(self) -> List[ScrapedProduct]:
        """
        Scraping completo de Nike AR via API.
        Recorre categorías principales: calzado, ropa, accesorios.
        """
        products = []
        categories = [
            ("shoes",       "FW",  "Calzado"),
            ("clothing",    "AP",  "Apparel"),
            ("accessories", "EQ",  "Equipment"),
        ]

        for slug, division, div_label in categories:
            self.log.info("Scrapeando Nike AR — %s", div_label)
            cat_products = self._scrape_category(slug, division)
            products.extend(cat_products)
            self.log.info("  %d productos en %s", len(cat_products), div_label)
            self.sleep(2.0)

        self.log.info("Nike AR total: %d productos", len(products))
        return products

    def scrape_by_style_color(self, style_colors: List[str]) -> List[ScrapedProduct]:
        """Scraping dirigido por lista de StyleColors — más eficiente que catálogo completo."""
        products = []
        for sc in style_colors:
            try:
                product = self._fetch_by_style_color(sc)
                if product:
                    products.append(product)
                self.sleep(0.8)
            except Exception as e:
                self.log.warning("Error scrapeando StyleColor %s: %s", sc, e)
        return products

    def _fetch_by_style_color(self, style_color: str) -> Optional[ScrapedProduct]:
        """Busca un producto específico por StyleColor en Nike AR."""
        url = f"{self.BASE_URL}/w/{style_color.lower().replace('/', '-')}"
        try:
            resp = self.get(url, headers=self.NIKE_HEADERS)
            return self._parse_pdp(resp.text, style_color, url)
        except Exception as e:
            self.log.debug("_fetch_by_style_color %s: %s", style_color, e)
            return None

    def _scrape_category(self, slug: str, division: str) -> List[ScrapedProduct]:
        """Scraping de una categoría completa via endpoint de Nike."""
        products = []
        anchor = 0
        page_size = 24

        while True:
            try:
                params = {
                    "queryid":   "categories",
                    "anonymousId": "anonymous",
                    "country":   "AR",
                    "language":  "es-AR",
                    "localeSrc": "addressbook",
                    "path":      f"/{slug}",
                    "anchor":    anchor,
                    "count":     page_size,
                }
                resp = self.get(
                    "https://api.nike.com/crt/v1/plp/threads",
                    params=params,
                    headers=self.NIKE_HEADERS,
                )
                data = resp.json()

                objects = (
                    data.get("productGrouping", {})
                        .get("threads", {})
                        .get("objects", [])
                )

                if not objects:
                    break

                for obj in objects:
                    try:
                        p = self._parse_thread(obj, division)
                        if p:
                            products.append(p)
                    except Exception as e:
                        self.log.debug("Error parseando thread: %s", e)

                anchor += page_size
                if anchor >= data.get("productGrouping", {}).get("threads", {}).get("count", 0):
                    break

                self.sleep(1.2)

            except Exception as e:
                self.log.warning("Error en categoría %s anchor %d: %s", slug, anchor, e)
                break

        return products

    def _parse_thread(self, obj: Dict[str, Any], division: str) -> Optional[ScrapedProduct]:
        """Parsea un objeto de thread de la API de Nike."""
        try:
            product_info = obj.get("productInfo", [{}])[0]
            merchant     = product_info.get("merchProduct", {})
            price_info   = product_info.get("prices", {})
            product      = product_info.get("product", {})

            style_color   = merchant.get("styleColor", "")
            full_price    = float(price_info.get("currentRetail", 0) or 0)
            final_price   = float(price_info.get("currentRetail", 0) or 0)
            name          = product.get("title", "")
            subtitle      = product.get("subtitle", "")
            full_name     = f"{name} {subtitle}".strip()

            # Talles disponibles
            skus = product_info.get("availableSkus", [])
            sizes = [s.get("localizedSize", "") for s in skus if s.get("available")]
            size_count = len(sizes)
            sizes_text = ", ".join(sizes)

            silueta = self.classify_silueta(full_name, division)

            pdp_url = f"https://www.nike.com/ar/t/{style_color.lower()}"

            return ScrapedProduct(
                scraper=self.SCRAPER_NAME,
                canal=self.CANAL,
                marca=self.MARCA,
                style_color=style_color,
                marketing_name=full_name,
                product_name_competitor=full_name,
                division=division,
                division_competitor=division,
                competitor_full_price=full_price,
                competitor_final_price=final_price,
                size_available_competitor=size_count,
                text_sizes_competitor=sizes_text,
                link_pdp_competitor=pdp_url,
                silueta=silueta,
            )
        except Exception:
            return None

    def _parse_pdp(self, html: str, style_color: str, url: str) -> Optional[ScrapedProduct]:
        """Parsea una PDP de Nike AR para extraer precio y talles."""
        try:
            # Buscar el precio en el HTML
            price_match = re.search(r'"currentRetail"\s*:\s*(\d+(?:\.\d+)?)', html)
            if not price_match:
                return None

            full_price  = float(price_match.group(1))
            name_match  = re.search(r'"title"\s*:\s*"([^"]+)"', html)
            name        = name_match.group(1) if name_match else style_color

            return ScrapedProduct(
                scraper=self.SCRAPER_NAME,
                canal=self.CANAL,
                marca=self.MARCA,
                style_color=style_color,
                marketing_name=name,
                product_name_competitor=name,
                competitor_full_price=full_price,
                competitor_final_price=full_price,
                link_pdp_competitor=url,
                silueta=self.classify_silueta(name),
            )
        except Exception:
            return None
