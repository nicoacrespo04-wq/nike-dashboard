# -*- coding: utf-8 -*-
"""
adidas_adapter.py — Scraper de adidas.com.ar via API pública.
Basado en codigo_adidas_7.py del repo Nike_Scrapper_Final.
Sin proxy, sin Playwright — usa la API pública de adidas.com.ar.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from .base_adapter import BaseAdapter, ScrapedProduct

ADIDAS_AR_API = "https://www.adidas.com.ar/api/plp/content-engine"
CATEGORIES    = ["calzado", "ropa", "accesorios"]
DIV_MAP       = {"calzado": "FW", "ropa": "AP", "accesorios": "EQ"}


class AdidasARAdapter(BaseAdapter):
    SCRAPER_NAME        = "ADIDAS_7"
    CANAL               = "Adidas.com.ar"
    MARCA               = "ADIDAS"
    BASE_URL            = "https://www.adidas.com.ar"
    INTER_REQUEST_DELAY = 1.2

    def scrape(self) -> List[ScrapedProduct]:
        products = []
        for cat in CATEGORIES:
            self.log.info("Scrapeando Adidas AR — %s", cat)
            cat_prods = self._scrape_category(cat)
            products.extend(cat_prods)
            self.log.info("  %d productos en %s", len(cat_prods), cat)
            self.sleep()
        self.log.info("Adidas AR total: %d productos", len(products))
        return products

    def _scrape_category(self, category: str) -> List[ScrapedProduct]:
        products = []
        start    = 0
        page_size = 48

        while True:
            try:
                params = {
                    "start":           start,
                    "sz":              page_size,
                    "prefn1":          "category",
                    "prefv1":          category,
                    "start":           start,
                }
                resp = self.get(ADIDAS_AR_API, params=params, headers={
                    "Accept":       "application/json",
                    "Referer":      f"{self.BASE_URL}/{category}",
                    "x-requested-with": "XMLHttpRequest",
                })
                data = resp.json()

                hits = data.get("hits", data.get("products", []))
                if not hits:
                    break

                for hit in hits:
                    try:
                        p = self._parse_hit(hit, category)
                        if p:
                            products.append(p)
                    except Exception as e:
                        self.log.debug("Error parseando hit: %s", e)

                total = data.get("total", data.get("count", 0))
                start += page_size
                if start >= total:
                    break
                self.sleep(1.0)

            except Exception as e:
                self.log.warning("Error en Adidas cat=%s start=%d: %s", category, start, e)
                break

        return products

    def _parse_hit(self, hit: Dict[str, Any], category: str) -> Optional[ScrapedProduct]:
        style_color = hit.get("id", hit.get("productId", ""))
        name        = hit.get("displayName", hit.get("name", ""))
        price_info  = hit.get("pricing", hit.get("price", {}))

        full_price  = float(price_info.get("standard",  price_info.get("original", 0)) or 0)
        final_price = float(price_info.get("sale",      price_info.get("current",  full_price)) or full_price)
        markdown    = max(0.0, full_price - final_price)

        sizes_raw   = hit.get("availableSizes", hit.get("sizes", []))
        sizes       = [str(s) for s in sizes_raw] if sizes_raw else []
        size_count  = len(sizes)
        sizes_text  = ", ".join(sizes)

        division    = DIV_MAP.get(category, "AP")
        silueta     = self.classify_silueta(name, division)
        franchise   = self.classify_franchise(name, style_color) if division == "FW" else None
        cat_norm    = self.classify_category(name, category)

        url = f"{self.BASE_URL}/{style_color}.html" if style_color else None

        return ScrapedProduct(
            scraper=self.SCRAPER_NAME,
            canal=self.CANAL,
            marca=self.MARCA,
            style_color=style_color,
            product_code_competitor=style_color,
            product_name_competitor=name,
            division=division,
            division_competitor=division,
            category_competitor=cat_norm,
            franchise_competitor=franchise,
            silueta=silueta,
            competitor_full_price=full_price,
            competitor_markdown=markdown,
            competitor_final_price=final_price,
            size_available_competitor=size_count,
            text_sizes_competitor=sizes_text,
            link_pdp_competitor=url,
        )

    def classify_category(self, name: str, slug: str) -> str:
        name_up = name.upper()
        if any(k in name_up for k in ["RUNNING", "RUN ", "RUNNER"]): return "RUNNING"
        if any(k in name_up for k in ["FUTBOL", "FOOTBALL", "SOCCER", "BOTIN"]): return "FOOTBALL/SOCCER"
        if any(k in name_up for k in ["BASKET", "BASKETBALL"]): return "BASKETBALL"
        if any(k in name_up for k in ["TRAINING", "GYM", "WORKOUT"]): return "TRAINING"
        if any(k in name_up for k in ["SAMBA","STAN SMITH","SUPERSTAR","GAZELLE","CAMPUS"]): return "SPORTSWEAR"
        if slug in ("ropa", "accesorios"): return "SPORTSWEAR"
        return "OTHER"
