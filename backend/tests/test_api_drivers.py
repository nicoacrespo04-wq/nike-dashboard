"""Contrato canónico de `drivers` (y su hermano `signals`) en la API.

Una sola forma para todos los endpoints de decisión:

    drivers: [{name, label, value, unit, contribution, detail}]
    signals: [{name, label, value, unit}]

`drivers` explica el score (valores 0..1, `contribution` suma 100 entre los
factores con datos). `signals` lleva las métricas observadas en su unidad
natural — stock %, gap de precio %, descuento %, share of shelf — que antes
viajaban dentro del sobre de retail media.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.api.serializers import canonical_driver, canonical_drivers
from app.db import query
from app.main import app

DRIVER_KEYS = {"name", "label", "value", "unit", "contribution", "detail"}
SIGNAL_KEYS = {"name", "label", "value", "unit"}

#: Métricas del sobre de retail media que el brief pide mostrar en pantalla.
BRIEF_SIGNALS = ("nike_stock_pct", "competitor_stock_pct", "price_gap_pct",
                 "nike_discount_pct", "nike_shelf_share")

RETAIL_MEDIA_ENVELOPE = [{
    "rationale": "Nike tiene stock y ya es competitivo en precio.",
    "nike_stock_pct": 85.7,
    "competitor_stock_pct": 25.0,
    "price_gap_pct": -3.5,
    "competitive_relevance": 68.3,
    "competitor_momentum": 0.62,
    "demand_signal": 0.79,
    "nike_shelf_share": 0.21,
    "business_importance": 51.03,
    "nike_discount_pct": 11.99,
    "coverage": 1.0,
    "factors": [
        {"name": "nike_stock_health", "value": 0.857, "contribution": 30.38,
         "weight": 0.25, "available": True, "detail": {}},
        {"name": "price_competitiveness", "value": 1.0, "contribution": 40.0,
         "weight": 0.2, "available": True, "detail": {"basis": "precios en Dexter"}},
        {"name": "shelf_gap", "value": 0.79, "contribution": 29.62,
         "weight": 0.15, "available": True, "detail": {}},
        {"name": "competitor_momentum", "value": None, "contribution": 0.0,
         "weight": 0.1, "available": False, "detail": {"reason": "sin datos"}},
    ],
}]

OPPORTUNITY_DRIVERS = [
    {"name": "competitive_relevance", "value": 0.6833, "contribution": 18.61,
     "weight": 0.2, "detail": {"match_score": 68.33}},
    {"name": "revenue_proxy", "value": 1.0, "contribution": 16.34,
     "weight": 0.12, "detail": {}},
]


@pytest.fixture(autouse=True)
def _sin_seguridad(monkeypatch):
    monkeypatch.delenv(auth.ENV_API_KEY, raising=False)
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "0")
    auth.reset_rate_limits()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _assert_canonical(drivers: list[dict], signals: list[dict]) -> None:
    for driver in drivers:
        assert set(driver) == DRIVER_KEYS, driver
        assert isinstance(driver["name"], str) and driver["name"]
        assert isinstance(driver["label"], str) and driver["label"]
        assert driver["value"] is not None, "un factor sin datos no se publica"
        assert driver["unit"] == "score_0_1"
        assert 0.0 <= driver["value"] <= 1.0
        assert isinstance(driver["detail"], dict)
    contributions = [d["contribution"] for d in drivers]
    assert contributions == sorted(contributions, reverse=True), "ordenado por contribución"
    if drivers:
        assert sum(contributions) == pytest.approx(100.0, abs=0.5)
    for signal in signals:
        assert set(signal) == SIGNAL_KEYS, signal
        assert isinstance(signal["value"], (int, float))
        assert signal["unit"] in {"pct", "ratio", "score_0_1", "score_0_100", "number"}


# ── normalización (unidad) ──────────────────────────────────


def test_el_sobre_de_retail_media_se_aplana_sin_perder_nada():
    drivers, signals, rationale = canonical_drivers(RETAIL_MEDIA_ENVELOPE)
    _assert_canonical(drivers, signals)

    assert [d["name"] for d in drivers] == ["price_competitiveness", "nike_stock_health",
                                            "shelf_gap"]
    assert "competitor_momentum" not in {d["name"] for d in drivers}   # sin datos, no se publica
    assert rationale == RETAIL_MEDIA_ENVELOPE[0]["rationale"]

    # Las métricas del brief sobreviven, con nombre y unidad propios.
    by_name = {s["name"]: s for s in signals}
    for name in BRIEF_SIGNALS:
        assert name in by_name, name
    assert by_name["nike_stock_pct"] == {"name": "nike_stock_pct", "label": "Stock Nike",
                                         "value": 85.7, "unit": "pct"}
    assert by_name["nike_shelf_share"]["unit"] == "ratio"      # 0..1, no porcentaje
    assert by_name["price_gap_pct"]["value"] == -3.5

    # Cada driver apunta a su métrica cruda, y el detalle del factor se conserva.
    assert by_name["price_gap_pct"]["name"] == \
        next(d for d in drivers if d["name"] == "price_competitiveness")["detail"]["signal"]
    assert next(d for d in drivers
                if d["name"] == "price_competitiveness")["detail"]["basis"] == "precios en Dexter"


def test_los_drivers_del_motor_de_oportunidades_ya_son_canonicos():
    drivers, signals, rationale = canonical_drivers(OPPORTUNITY_DRIVERS)
    assert [d["name"] for d in drivers] == ["competitive_relevance", "revenue_proxy"]
    assert drivers[0]["label"] == "Relevancia competitiva"
    assert drivers[0]["detail"]["weight"] == 0.2          # el peso viaja en detail
    assert drivers[0]["detail"]["match_score"] == 68.33   # sin pisar lo que ya traía
    assert signals == [] and rationale is None


def test_tolera_factores_crudos_de_composite_score():
    drivers, _, _ = canonical_drivers([
        {"factor": "visual", "raw_score": 0.5, "weight": 0.3, "contribution": 100.0,
         "available": True, "detail": {}},
        {"factor": "price", "raw_score": None, "weight": 0.2, "contribution": 0.0,
         "available": False, "detail": {}},
    ])
    assert [d["name"] for d in drivers] == ["visual"]
    assert drivers[0]["label"] == "Visual"


def test_payloads_invalidos_no_rompen_la_api():
    assert canonical_drivers(None) == ([], [], None)
    assert canonical_drivers("[]") == ([], [], None)
    assert canonical_drivers([None, 3, "x", {}, {"name": ""}]) == ([], [], None)
    assert canonical_driver({"name": "x", "value": None}) is None
    assert canonical_driver({"name": "x", "value": True}) is None      # bool no es métrica


def test_etiqueta_por_defecto_para_un_factor_desconocido():
    driver = canonical_driver({"name": "factor_nuevo", "value": 0.5, "contribution": 100.0})
    assert driver is not None and driver["label"] == "Factor nuevo"


# ── contrato en vivo (requiere pipeline corrido) ────────────


def _rows(table: str) -> int:
    try:
        return int(query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])  # noqa: S608
    except Exception:                                    # tabla inexistente: sin pipeline
        return 0


needs_pipeline = pytest.mark.skipif(
    _rows("retail_media_opportunities") == 0 or _rows("opportunities") == 0,
    reason="requiere `python -m app.pipeline` corrido sobre la DB por defecto",
)


def _items(client: TestClient, path: str) -> list[dict]:
    """Items del endpoint, o `skip` si la DB está vacía.

    El pipeline borra y recalcula: si justo está corriendo mientras corren los
    tests, la tabla puede verse vacía por un instante. Eso no es una falla del
    contrato."""
    items = client.get(path).json().get("items") or []
    if not items:
        pytest.skip(f"{path} sin datos (¿pipeline corriendo?)")
    return items


@needs_pipeline
def test_retail_media_publica_drivers_canonicos_y_signals(client):
    item = _items(client, "/api/retail-media?limit=1")[0]
    _assert_canonical(item["drivers"], item["signals"])

    assert isinstance(item["rationale"], str) and item["rationale"]
    assert all("rationale" not in d for d in item["drivers"])   # el racional no va en drivers
    nombres = {s["name"] for s in item["signals"]}
    for name in BRIEF_SIGNALS:
        assert name in nombres, f"{name} es parte de la pantalla del brief"


@needs_pipeline
def test_oportunidades_publica_el_mismo_contrato(client):
    item = _items(client, "/api/opportunities?limit=1")[0]
    _assert_canonical(item["drivers"], item["signals"])
    recommendation = item["recommendation"]
    _assert_canonical(recommendation["drivers"], recommendation["signals"])


@needs_pipeline
def test_overview_usa_el_mismo_contrato_en_los_tres_bloques(client):
    data = client.get("/api/overview").json()
    for block in ("top_opportunities", "top_risks", "retail_media"):
        for item in data[block]:
            _assert_canonical(item["drivers"], item["signals"])


@needs_pipeline
def test_ningun_endpoint_devuelve_ya_la_forma_vieja(client):
    """El sobre `[{rationale, …, factors:[…]}]` no puede salir más por la API."""
    for path in ("/api/retail-media?limit=5", "/api/opportunities?limit=5"):
        for item in _items(client, path):
            for driver in item["drivers"]:
                assert "factors" not in driver
                assert "rationale" not in driver
