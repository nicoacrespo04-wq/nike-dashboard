# -*- coding: utf-8 -*-
"""
Adapters de scraping del Nike Dashboard.

Estado de la migración
──────────────────────
Los scrapers que hoy corre el workflow semanal son los `codigo_*.py` del repo
privado `nicoacrespo04-wq/Nike_Scrapper_Final`. La idea a mediano plazo es
migrarlos a este paquete (un adapter por retailer sobre `BaseAdapter`), para
tener retries, fallbacks de fetch, alertas y carga a `pricing_data` en un
único lugar.

`RETAILERS` es el inventario de esa migración: qué está hecho, qué falta y con
qué script legacy se corresponde cada cosa. Ver `template_adapter.py` para el
checklist de qué implementar en cada adapter nuevo, y `docs/scrapers.md` para
el flujo completo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Type

from .base_adapter import BaseAdapter, ScrapedProduct, ScrapeResult
from .nike_ar_adapter import NikeARAdapter
from .adidas_adapter import AdidasARAdapter
from .template_adapter import TemplateAdapter


@dataclass(frozen=True)
class RetailerSpec:
    """Un retailer del pipeline: su script legacy y su adapter (si ya existe)."""
    name:          str                          # id usado en el workflow y en la CLI
    legacy_script: str                          # script en Nike_Scrapper_Final
    canal:         str                          # cómo aparece en el dashboard
    adapter:       Optional[Type[BaseAdapter]] = None
    notes:         str = ""

    @property
    def implemented(self) -> bool:
        return self.adapter is not None


RETAILERS: dict[str, RetailerSpec] = {
    "nike_ar": RetailerSpec(
        name="nike_ar", legacy_script="codigo_nike_ar_general.py", canal="Nike",
        adapter=NikeARAdapter,
        notes="API de nike.com.ar. Es la referencia de precios contra la que se comparan los demás.",
    ),
    "adidas": RetailerSpec(
        name="adidas", legacy_script="codigo_adidas_7.py", canal="Adidas.com.ar",
        adapter=AdidasARAdapter,
        notes="API pública de adidas.com.ar, sin Playwright.",
    ),
    "puma": RetailerSpec(
        name="puma", legacy_script="codigo_puma.py", canal="Puma.com.ar",
        notes="PENDIENTE. Sitio VTEX; el legacy usa Playwright. Falta portar paginación y parseo de talles.",
    ),
    "dexter": RetailerSpec(
        name="dexter", legacy_script="codigo_dexter.py", canal="Dexter",
        notes="PENDIENTE. Retailer multimarca: hay que separar Nike/Adidas/Puma por `marca`.",
    ),
    "moov": RetailerSpec(
        name="moov", legacy_script="codigo_moov.py", canal="Moov",
        notes="PENDIENTE. Retailer multimarca.",
    ),
    "sporting": RetailerSpec(
        name="sporting", legacy_script="codigo_sporting3.py", canal="Sporting",
        notes="PENDIENTE. Retailer multimarca.",
    ),
    "soloDeportes": RetailerSpec(
        name="soloDeportes", legacy_script="codigo_soloDeportes.py", canal="Solo Deportes",
        notes="PENDIENTE. Ojo con el nombre de los CSV: mezcla mayúsculas y minúsculas.",
    ),
    "grid": RetailerSpec(
        name="grid", legacy_script="codigo_grid.py", canal="Grid",
        notes="PENDIENTE.",
    ),
    "dash": RetailerSpec(
        name="dash", legacy_script="codigo_dash.py", canal="Dash",
        notes="PENDIENTE.",
    ),
    "opensports": RetailerSpec(
        name="opensports", legacy_script="codigo_opensports.py", canal="Open Sports",
        notes="PENDIENTE.",
    ),
    "stockcenter": RetailerSpec(
        name="stockcenter", legacy_script="codigo_stockcenter_v6.py", canal="Stock Center",
        notes="PENDIENTE. El sufijo _v6 sugiere que el legacy cambió varias veces: revisar cuál corre hoy.",
    ),
}


def get_adapter(name: str) -> BaseAdapter:
    """
    Devuelve una instancia del adapter del retailer pedido.

    Lanza un error explícito (no un ImportError críptico) si el retailer existe
    en el pipeline pero todavía no fue migrado a un adapter.
    """
    spec = RETAILERS.get(name)
    if spec is None:
        disponibles = ", ".join(sorted(RETAILERS))
        raise KeyError(f"Retailer desconocido: '{name}'. Disponibles: {disponibles}")
    if spec.adapter is None:
        raise NotImplementedError(
            f"El retailer '{name}' todavía no tiene adapter en scraper/adapters/. "
            f"Hoy lo cubre el script legacy '{spec.legacy_script}' del repo "
            f"Nike_Scrapper_Final. {spec.notes} "
            f"Para migrarlo, seguí el checklist de scraper/adapters/template_adapter.py."
        )
    return spec.adapter()


def pending_retailers() -> list[str]:
    return [name for name, spec in RETAILERS.items() if not spec.implemented]


__all__ = [
    "BaseAdapter", "ScrapedProduct", "ScrapeResult",
    "NikeARAdapter", "AdidasARAdapter", "TemplateAdapter",
    "RetailerSpec", "RETAILERS", "get_adapter", "pending_retailers",
]
