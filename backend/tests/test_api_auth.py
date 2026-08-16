"""Seguridad de la API: API key opcional por header + rate limiting en memoria.

Los tres escenarios que importan:
  1. sin ``CI_API_KEY`` la API queda abierta (es como corre en desarrollo y de
     lo que dependen el resto de los tests y el dashboard local);
  2. con ``CI_API_KEY`` definida, 401 sin header y 200 con el header correcto;
  3. pasado el límite de requests, 429 con ``Retry-After``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import API_KEY_HEADER, SecuritySettings, SlidingWindowLimiter
from app.main import app

KEY = "clave-de-prueba"
PRIVATE_PATHS = ("/api/opportunities", "/api/retail-media", "/api/products", "/api/config")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Cada test arranca sin auth, sin límite y con el limitador vacío."""
    for var in (auth.ENV_API_KEY, auth.ENV_RATE_LIMIT, auth.ENV_RATE_LIMIT_WINDOW,
                auth.ENV_TRUST_FORWARDED_FOR, auth.ENV_PUBLIC_PATHS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "0")
    auth.reset_rate_limits()
    yield
    auth.reset_rate_limits()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── 1. sin CI_API_KEY: todo abierto ─────────────────────────


def test_sin_api_key_la_api_queda_abierta(client, monkeypatch):
    monkeypatch.delenv(auth.ENV_API_KEY, raising=False)
    assert auth.settings().auth_enabled is False
    for path in PRIVATE_PATHS:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/health").status_code == 200


def test_sin_api_key_health_reporta_que_no_hay_auth(client):
    security = client.get("/api/health").json()["security"]
    assert security["auth_required"] is False
    assert security["api_key_header"] == API_KEY_HEADER


def test_el_banner_de_arranque_advierte_cuando_no_hay_key(caplog):
    with caplog.at_level("WARNING", logger="app.auth"):
        auth.log_security_banner(SecuritySettings.from_env({}))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "arrancar sin key tiene que dejar una advertencia visible"
    assert any(auth.ENV_API_KEY in r.getMessage() for r in warnings)


# ── 2. con CI_API_KEY: 401 sin header, 200 con header ───────


def test_con_api_key_sin_header_devuelve_401(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    for path in PRIVATE_PATHS:
        response = client.get(path)
        assert response.status_code == 401, path
        assert API_KEY_HEADER in response.json()["detail"]


def test_con_api_key_y_header_correcto_devuelve_200(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    for path in PRIVATE_PATHS:
        assert client.get(path, headers={API_KEY_HEADER: KEY}).status_code == 200, path


def test_key_incorrecta_o_vacia_devuelve_401(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    for value in ("otra-cosa", "", f"{KEY} ", KEY.upper()):
        assert client.get("/api/opportunities", headers={API_KEY_HEADER: value}).status_code == 401


def test_acepta_varias_keys_separadas_por_coma(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, f"{KEY}, otra-key ")
    assert client.get("/api/opportunities", headers={API_KEY_HEADER: KEY}).status_code == 200
    assert client.get("/api/opportunities", headers={API_KEY_HEADER: "otra-key"}).status_code == 200


def test_health_y_docs_siguen_publicos_con_auth_activa(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").json()["security"]["auth_required"] is True
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_el_preflight_de_cors_no_se_bloquea(client, monkeypatch):
    """Un OPTIONS no puede llevar la key: bloquearlo rompe el browser."""
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    response = client.options("/api/opportunities", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_rutas_publicas_extra_por_entorno(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    monkeypatch.setenv(auth.ENV_PUBLIC_PATHS, "/api/config")
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/opportunities").status_code == 401


# ── 3. rate limiting ────────────────────────────────────────


def test_supera_el_limite_y_devuelve_429_con_retry_after(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "3")
    monkeypatch.setenv(auth.ENV_RATE_LIMIT_WINDOW, "60")

    for _ in range(3):
        assert client.get("/api/opportunities").status_code == 200

    blocked = client.get("/api/opportunities")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert blocked.headers["X-RateLimit-Limit"] == "3"
    assert "Retry-After" in blocked.json()["detail"]


def test_el_limite_cero_desactiva_el_rate_limiting(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "0")
    for _ in range(20):
        assert client.get("/api/opportunities").status_code == 200


def test_health_no_se_cae_por_un_flood_a_los_endpoints_privados(client, monkeypatch):
    """El health check tiene su propio bucket: si compartiera cuota, un scrapeo
    haría que el dashboard reporte el motor como caído."""
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "2")
    for _ in range(2):
        client.get("/api/opportunities")
    assert client.get("/api/opportunities").status_code == 429
    assert client.get("/api/health").status_code == 200


def test_cada_key_tiene_su_propia_cuota(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_API_KEY, f"{KEY},otra-key")
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "2")
    for _ in range(2):
        assert client.get("/api/opportunities", headers={API_KEY_HEADER: KEY}).status_code == 200
    assert client.get("/api/opportunities", headers={API_KEY_HEADER: KEY}).status_code == 429
    assert client.get("/api/opportunities",
                      headers={API_KEY_HEADER: "otra-key"}).status_code == 200


def test_los_intentos_fallidos_tambien_consumen_cuota(client, monkeypatch):
    """Fuerza bruta sobre la key: el 429 llega antes que el barrido de claves."""
    monkeypatch.setenv(auth.ENV_API_KEY, KEY)
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "2")
    assert client.get("/api/opportunities", headers={API_KEY_HEADER: "mal-1"}).status_code == 401
    assert client.get("/api/opportunities", headers={API_KEY_HEADER: "mal-2"}).status_code == 401
    assert client.get("/api/opportunities", headers={API_KEY_HEADER: "mal-3"}).status_code == 429


def test_headers_de_cuota_en_las_respuestas_ok(client, monkeypatch):
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "5")
    first = client.get("/api/health")
    assert first.headers["X-RateLimit-Limit"] == "5"
    assert first.headers["X-RateLimit-Remaining"] == "4"
    assert client.get("/api/health").headers["X-RateLimit-Remaining"] == "3"


# ── ventana deslizante (unidad) ─────────────────────────────


def test_la_ventana_es_deslizante_y_no_fija():
    limiter = SlidingWindowLimiter()
    assert limiter.check("b", 2, 60, now=0.0)[0] is True
    assert limiter.check("b", 2, 60, now=10.0)[0] is True

    allowed, remaining, retry_after = limiter.check("b", 2, 60, now=20.0)
    assert allowed is False and remaining == 0
    assert retry_after == pytest.approx(40.0)          # se libera 60s después del primer hit

    assert limiter.check("b", 2, 60, now=61.0)[0] is True    # expiró el primero
    assert limiter.check("b", 2, 60, now=61.5)[0] is False    # el de t=10 sigue vigente
    assert limiter.check("b", 2, 60, now=71.0)[0] is True


def test_buckets_independientes_y_reset():
    limiter = SlidingWindowLimiter()
    assert limiter.check("ip-a", 1, 60, now=0.0)[0] is True
    assert limiter.check("ip-a", 1, 60, now=1.0)[0] is False
    assert limiter.check("ip-b", 1, 60, now=1.0)[0] is True
    limiter.reset()
    assert limiter.check("ip-a", 1, 60, now=2.0)[0] is True


# ── lectura del entorno ─────────────────────────────────────


def test_settings_lee_el_entorno_con_defaults_sanos():
    vacio = SecuritySettings.from_env({})
    assert vacio.auth_enabled is False
    assert vacio.rate_limit == auth.DEFAULT_RATE_LIMIT
    assert vacio.window == auth.DEFAULT_RATE_LIMIT_WINDOW

    roto = SecuritySettings.from_env({auth.ENV_RATE_LIMIT: "no-es-un-numero"})
    assert roto.rate_limit == auth.DEFAULT_RATE_LIMIT      # no explota: usa el default

    configurado = SecuritySettings.from_env({
        auth.ENV_API_KEY: " k1 , k2 ",
        auth.ENV_RATE_LIMIT: "10",
        auth.ENV_RATE_LIMIT_WINDOW: "30",
    })
    assert configurado.api_keys == ("k1", "k2")
    assert configurado.auth_enabled is True
    assert (configurado.rate_limit, configurado.window) == (10, 30.0)


def test_rutas_publicas_incluyen_los_assets_de_docs():
    config = SecuritySettings.from_env({})
    assert auth.is_public_path("/api/health", config)
    assert auth.is_public_path("/api/health/", config)
    assert auth.is_public_path("/docs/oauth2-redirect", config)
    assert not auth.is_public_path("/api/healthcheck", config)
    assert not auth.is_public_path("/api/overview", config)


def test_x_forwarded_for_solo_se_usa_si_se_confia(client, monkeypatch):
    """Detrás del proxy de Next todas las requests comparten IP: separar por
    XFF sólo tiene sentido si el proxy es propio y setea el header."""
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "1")
    headers = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    assert client.get("/api/opportunities", headers=headers).status_code == 200
    assert client.get("/api/opportunities", headers=headers).status_code == 429  # misma IP real

    monkeypatch.setenv(auth.ENV_TRUST_FORWARDED_FOR, "1")
    auth.reset_rate_limits()
    assert client.get("/api/opportunities", headers=headers).status_code == 200
    assert client.get("/api/opportunities",
                      headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 200
