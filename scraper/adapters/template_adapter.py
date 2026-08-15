# -*- coding: utf-8 -*-
"""
template_adapter.py — Molde para migrar un scraper legacy a `BaseAdapter`.

CONTEXTO
────────
Los scrapers que corre el workflow semanal (`codigo_*.py`) viven en el repo
privado `nicoacrespo04-wq/Nike_Scrapper_Final`, NO acá. Este paquete es el
destino al que se los quiere migrar: un adapter por retailer, con la cascada
de fetch (requests → curl_cffi → Playwright), retries, alertas y escritura a
`pricing_data` ya resueltas en `BaseAdapter`.

Hoy están migrados sólo dos: `NikeARAdapter` y `AdidasARAdapter`. El resto
figura en `RETAILERS` (ver `__init__.py`) como pendiente.

CÓMO MIGRAR UN RETAILER (checklist)
───────────────────────────────────
 1. Copiá este archivo a `<retailer>_adapter.py` y renombrá la clase.
 2. Completá los atributos de clase:
      SCRAPER_NAME  → el mismo string que usa el CSV/DB en la columna `Scraper`
                      (para no romper el histórico ya cargado en pricing_data).
      CANAL         → cómo se llama el canal en el dashboard (ej. "Dexter").
      MARCA         → marca del producto scrapeado (NIKE / ADIDAS / PUMA).
      BASE_URL      → home del sitio.
 3. Implementá `scrape()`:
      · Preferí SIEMPRE la API JSON del sitio (VTEX, Salesforce, Shopify…)
        antes que parsear HTML: es más estable y no necesita Playwright.
        Muchos retailers argentinos son VTEX:
            /api/catalog_system/pub/products/search?_from=0&_to=49
      · Paginá hasta que la respuesta venga vacía, con `self.sleep()` entre
        requests (rate limit).
      · Usá `self.get(url, params=..., headers=...)` — ya trae fallback a
        curl_cffi y Playwright, retries y rotación de User-Agent.
      · Devolvé `List[ScrapedProduct]`; los campos mínimos útiles para el
        dashboard están marcados abajo con "OBLIGATORIO".
 4. Precios: cargá SIEMPRE el precio unitario, nunca el total financiado en
    cuotas, y dejá `competitor_final_price=None` cuando el dato no está
    (nunca 0). Si el sitio muestra "N cuotas sin interés", poné la cantidad en
    `cuotas_competitor` — `db/load_csv.py` la usa para recuperar precios que
    llegaron multiplicados. Ver el bloque "SANITIZACIÓN DE PRECIOS" ahí.
 5. Talles: `size_available_competitor` = cantidad de talles CON STOCK, y
    `text_sizes_competitor` = la lista separada por comas.
 6. Clasificación: `self.classify_silueta(nombre, division)` (heurística
    gratis) y `self.classify_franchise(nombre, style_color)` (OpenAI + cache,
    sólo para calzado y sólo si hay `OPENAI_API_KEY`).
 7. Comparación vs Nike: si el scraper matchea contra el catálogo Nike,
    completá `nike_full_price` / `nike_final_price` y calculá
    `gap_final_price_pct` y `bml_final_price` con
    `self.calculate_gap_pct()` / `self.calculate_bml()`.
 8. Registralo en `RETAILERS` (`scraper/adapters/__init__.py`) con
    `adapter=<TuClase>` y probalo con:
        python scraper/run_adapter.py <nombre> --limit 20 --csv salida.csv
 9. Recién cuando el adapter produce el mismo CSV que el script legacy,
    cambiá el paso correspondiente del workflow para que llame al adapter en
    vez de al `codigo_*.py`.

QUÉ NO HACER
────────────
No inventes selectores ni endpoints: hay que mirar el sitio real y el script
legacy correspondiente. Un adapter que "compila" pero devuelve productos con
precios inventados es peor que no tenerlo.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base_adapter import BaseAdapter, ScrapedProduct


class TemplateAdapter(BaseAdapter):
    """Plantilla no funcional. Copiar, renombrar e implementar `scrape()`."""

    SCRAPER_NAME = "TEMPLATE"          # OBLIGATORIO — igual al del CSV legacy
    CANAL        = "RETAIL_ARG"        # OBLIGATORIO — canal del dashboard
    MARCA        = "NIKE"              # NIKE | ADIDAS | PUMA
    BASE_URL     = ""                  # https://www.retailer.com.ar

    # Endpoint de catálogo. Los VTEX suelen ser:
    #   f"{BASE_URL}/api/catalog_system/pub/products/search"
    CATALOG_API  = ""
    PAGE_SIZE    = 50

    def scrape(self) -> List[ScrapedProduct]:
        raise NotImplementedError(
            f"{self.__class__.__name__}.scrape() no está implementado. "
            "Seguí el checklist del docstring de template_adapter.py: el script "
            "legacy equivalente vive en el repo privado Nike_Scrapper_Final."
        )

    # ── Ejemplo de paginación VTEX (dejar como referencia, no se usa) ──────
    def _scrape_vtex_page(self, offset: int) -> List[Dict[str, Any]]:
        resp = self.get(
            self.CATALOG_API,
            params={"_from": offset, "_to": offset + self.PAGE_SIZE - 1},
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        return data if isinstance(data, list) else []

    def _parse_item(self, item: Dict[str, Any]) -> Optional[ScrapedProduct]:
        """
        Convertir un item crudo del sitio a ScrapedProduct.

        Campos mínimos que el dashboard necesita para que la fila sirva:
          · product_name_competitor   (OBLIGATORIO)
          · link_pdp_competitor       (OBLIGATORIO — para auditar el dato)
          · competitor_final_price    (OBLIGATORIO si hay precio; None si no)
          · silueta / division        (para los cortes por categoría)
          · size_available_competitor (para share of shelf)
        """
        raise NotImplementedError
