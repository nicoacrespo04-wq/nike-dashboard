"""`/api/health`: el endpoint que responde "¿este deploy sirve para algo?".

Existe porque antes respondía `status: "ok"` fijo. Un contenedor recién levantado
con la base vacía informaba exactamente lo mismo que uno con 995 productos
cargados, así que `curl .../api/health` —el único paso de verificación que tiene
alguien desplegando esto— no servía para verificar nada.

El caso más delicado es `building`, y no es teórico: se detectó corriendo el
arranque real contra Postgres. Durante los ~26 s del pipeline las tablas se van
llenando de a una, así que a mitad de camino ya hay `competitive_matches` pero
todavía no hay `opportunities`. Con el chequeo de contenido antes que el de
estado, el health check anunciaba `ok` a los 32 s, con el pipeline todavía
corriendo y `opportunities: 0`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import auth, build_state
from app.api.routers import overview
from app.main import app

#: Una base cargada y completa.
LLENA = {"products": 995, "price_observations": 27_593,
         "competitive_matches": 4_000, "opportunities": 1_447}

#: El pipeline a mitad de camino: hay matches, todavía no hay oportunidades.
A_MEDIAS = {"products": 995, "price_observations": 27_593,
            "competitive_matches": 4_000, "opportunities": 0}

#: Sólo la ingesta terminó; el pipeline no produjo nada.
SOLO_INGESTA = {"products": 995, "price_observations": 27_593,
                "competitive_matches": 0, "opportunities": 0}


@pytest.fixture(autouse=True)
def _sin_auth(monkeypatch):
    """Sin rate limit ni key: acá se testea el contenido, no la seguridad."""
    monkeypatch.delenv(auth.ENV_API_KEY, raising=False)
    monkeypatch.setenv(auth.ENV_RATE_LIMIT, "0")
    auth.reset_rate_limits()
    yield
    auth.reset_rate_limits()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _con_tablas(monkeypatch, contadores: dict[str, int], build: dict | None = None):
    """Fija lo que ve el health check: los contadores y el estado de build.

    Se parchea `count` en vez de armar una base real porque lo que se testea es
    la MÁQUINA DE ESTADOS, no SQLite: cada combinación necesitaría una base a
    medio construir, que es justo lo que no se puede fabricar de forma estable.
    """
    monkeypatch.setattr(overview, "count", lambda t, *a, **k: contadores.get(t, 0))
    monkeypatch.setattr(overview.build_state, "read", lambda *a, **k: build)


def test_con_datos_completos_dice_ok(client, monkeypatch):
    _con_tablas(monkeypatch, LLENA)
    cuerpo = client.get("/api/health").json()

    assert cuerpo["status"] == "ok"
    assert cuerpo["data"]["products"] == 995
    assert cuerpo["data"]["opportunities"] == 1_447


def test_base_vacia_no_dice_ok(client, monkeypatch):
    """El bug original: una base vacía informaba `ok` como cualquier otra."""
    _con_tablas(monkeypatch, {})
    cuerpo = client.get("/api/health").json()

    assert cuerpo["status"] == "empty"
    assert cuerpo["data"]["products"] == 0


def test_mientras_construye_no_dice_ok_aunque_ya_haya_matches(client, monkeypatch):
    """La regresión concreta: `building` tiene que ganarle al contenido.

    Si esto se rompe, quien mira el health para saber si el deploy terminó ve
    `ok` a mitad del pipeline y abre el dashboard a medio llenar.
    """
    _con_tablas(monkeypatch, A_MEDIAS, build={"state": build_state.BUILDING})
    cuerpo = client.get("/api/health").json()

    assert cuerpo["status"] == "building"
    assert cuerpo["data"]["build"]["state"] == "building"


def test_ingesta_sin_pipeline_es_degraded(client, monkeypatch):
    """Hay productos pero el pipeline no produjo salida: las pantallas cargan
    a medias. No es `ok` ni es `empty`."""
    _con_tablas(monkeypatch, SOLO_INGESTA)
    assert client.get("/api/health").json()["status"] == "degraded"


def test_build_terminado_deja_de_frenar_el_ok(client, monkeypatch):
    """Un `ready` viejo no puede dejar el health clavado en `building`."""
    _con_tablas(monkeypatch, LLENA, build={"state": build_state.READY})
    assert client.get("/api/health").json()["status"] == "ok"


def test_responde_200_siempre(client, monkeypatch):
    """Nunca un 5xx, ni siquiera con la base vacía.

    El health check de Render mata el contenedor si esto no da 200, y el
    momento en que la base está vacía es justamente el único en que hay que
    dejarlo trabajar.
    """
    for contadores in ({}, SOLO_INGESTA, A_MEDIAS, LLENA):
        _con_tablas(monkeypatch, contadores)
        assert client.get("/api/health").status_code == 200


def test_avisa_si_va_a_levantar_el_dataset_demo(client, monkeypatch):
    """`expected_source` explica de una una base sospechosamente chica en
    producción: si dice `demo`, falta `DATABASE_URL`."""
    _con_tablas(monkeypatch, LLENA)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert client.get("/api/health").json()["data"]["expected_source"] == "demo"

    monkeypatch.setenv("DATABASE_URL", "postgresql://x@y/z")
    assert client.get("/api/health").json()["data"]["expected_source"] == "supabase"


def test_sigue_publicando_las_tablas_y_la_seguridad(client, monkeypatch):
    """Contrato viejo intacto: `PipelineBanner` lee `tables` y `empty_tables`."""
    _con_tablas(monkeypatch, LLENA)
    cuerpo = client.get("/api/health").json()

    assert cuerpo["tables"]["products"] == 995
    assert "reviews" in cuerpo["empty_tables"]
    assert cuerpo["security"]["auth_required"] is False


# ── app.build_state ─────────────────────────────────────────


def test_estado_va_y_vuelve(tmp_path):
    db = tmp_path / "intelligence.db"
    build_state.write(build_state.READY, db_path=db, detail="ingesta desde DATABASE_URL")

    leido = build_state.read(db)
    assert leido["state"] == "ready"
    assert leido["detail"] == "ingesta desde DATABASE_URL"
    assert leido["age_seconds"] >= 0


def test_sin_archivo_no_sabe_nada(tmp_path):
    """El caso de desarrollo local: nadie escribió el estado y eso está bien."""
    assert build_state.read(tmp_path / "no-existe.db") is None


def test_un_archivo_a_medio_escribir_no_tumba_el_health(tmp_path):
    """El entrypoint puede estar escribiendo justo cuando entra un health check.
    Un JSON partido no justifica un 500 en el latido del servicio."""
    db = tmp_path / "intelligence.db"
    build_state.state_path(db).write_text('{"state": "buil', encoding="utf-8")
    assert build_state.read(db) is None

    build_state.state_path(db).write_text("", encoding="utf-8")
    assert build_state.read(db) is None

    build_state.state_path(db).write_text(json.dumps(["no", "es", "un", "dict"]), encoding="utf-8")
    assert build_state.read(db) is None


def test_escribir_nunca_tira(tmp_path):
    """Esto es telemetría, no el producto: si el disco falla, el motor levanta
    igual y el health simplemente vuelve a no saber nada."""
    build_state.write(build_state.BUILDING, db_path=tmp_path / "sin" / "permiso" / "x.db")


def test_el_estado_vive_al_lado_de_la_base(tmp_path):
    """Mismo volumen que el `.db`: no pueden desincronizarse por estar en
    discos distintos."""
    assert build_state.state_path(tmp_path / "intelligence.db").parent == tmp_path
