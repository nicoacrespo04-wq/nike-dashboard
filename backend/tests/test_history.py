"""Tests del historial temporal.

Dos niveles:
  * unitarios sobre una DB armada a mano (identidad estable, snapshots, series);
  * integración real corriendo `pipeline.run_all` varias veces seguidas, que es
    donde se ve lo importante: el pipeline resetea la base y el historial
    sobrevive, con el mismo `entity_key` aunque los `id` cambien.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_conn, init_db, query
from app.services import history


# ── helpers ─────────────────────────────────────────────────


def _insert(conn, table: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


@pytest.fixture()
def db(tmp_path):
    """DB temporal con datos de referencia (marcas, país, retailers, productos)."""
    path = tmp_path / "history.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        _insert(conn, "countries", [{"code": "AR", "name": "Argentina", "currency": "ARS"}])
        _insert(conn, "brands", [{"id": 1, "name": "Nike", "is_focus": 1},
                                 {"id": 2, "name": "Asics", "is_focus": 0}])
        _insert(conn, "retailers", [{"id": 1, "name": "Dexter", "country_code": "AR",
                                     "channel": "B2B", "importance": 0.8}])
        _insert(conn, "products", [
            {"id": 10, "brand_id": 1, "country_code": "AR", "product_name": "Pegasus 41"},
            {"id": 20, "brand_id": 2, "country_code": "AR", "product_name": "Novablast 4"},
        ])
    return path


OPP = {"opportunity_type": "price_competitiveness_risk", "family": "pricing",
       "nike_product_id": 10, "competitor_product_id": 20, "retailer_id": 1,
       "country_code": "AR", "title": "Riesgo de precio", "severity": "HIGH",
       "confidence": "HIGH"}


def _write_state(db, *, importance: float, match_score: float, signal_value: float,
                 id_offset: int = 0) -> None:
    """Simula una corrida del pipeline: borra y recalcula con ids nuevos.

    ``id_offset`` mueve los ids autoincrementales a propósito: el historial no
    puede depender de ellos.
    """
    with get_conn(db) as conn:
        conn.execute("DELETE FROM opportunities")
        conn.execute("DELETE FROM competitive_matches")
        conn.execute("DELETE FROM market_signals")
        _insert(conn, "opportunities", [{**OPP, "id": 500 + id_offset,
                                         "business_importance": importance}])
        _insert(conn, "competitive_matches", [{
            "id": 900 + id_offset, "nike_product_id": 10, "competitor_product_id": 20,
            "match_score": match_score, "raw_match_score": match_score, "coverage": 0.8,
            "confidence": "HIGH"}])
        _insert(conn, "market_signals", [{
            "id": 700 + id_offset, "signal_type": "social_momentum", "entity_type": "brand",
            "entity_id": "2", "country_code": "AR", "value": signal_value, "delta": 1.0}])


def _fake_run(db, **state) -> int:
    run_id = history.start_run(db, source="test")
    history.snapshot(run_id, db)
    history.finish_run(run_id, db, status="ok", counts={"stage": 1})
    return run_id


# ── entity_key: el contrato ─────────────────────────────────


def test_entity_key_es_determinista_y_corto():
    a = history.entity_key("match", nike_product_id=10, competitor_product_id=20)
    b = history.entity_key("match", nike_product_id=10, competitor_product_id=20)
    assert a == b
    assert len(a) == history.KEY_LENGTH
    assert a.isalnum() and a.islower()


def test_entity_key_normaliza_tipos_y_formato():
    """`10`, `"10"` y `10.0` son el mismo producto; `AR` y `ar` el mismo país."""
    assert history.entity_key("match", nike_product_id=10, competitor_product_id=20) == \
        history.entity_key("match", nike_product_id="10", competitor_product_id=20.0)
    assert history.entity_key("opportunity", opportunity_type="X", country_code="AR") == \
        history.entity_key("opportunity", opportunity_type=" x ", country_code="ar")


def test_entity_key_ignora_campos_no_identitarios():
    """Se puede pasar la fila entera: sólo cuentan los campos canónicos."""
    base = history.entity_key("opportunity", **OPP)
    ruidosa = history.entity_key("opportunity", **{**OPP, "id": 99, "business_importance": 12.5,
                                                   "computed_at": "2026-01-01"})
    assert base == ruidosa


def test_entity_key_distingue_entidades_distintas():
    keys = {
        history.entity_key("opportunity", **OPP),
        history.entity_key("opportunity", **{**OPP, "opportunity_type": "distribution_gap"}),
        history.entity_key("opportunity", **{**OPP, "retailer_id": 2}),
        history.entity_key("opportunity", **{**OPP, "country_code": "CL"}),
        history.entity_key("match", nike_product_id=10, competitor_product_id=20),
    }
    assert len(keys) == 5


def test_entity_key_coincide_con_el_triaje():
    """El contrato con `app.services.triage`: la misma oportunidad, la misma clave.

    Si divergen, `opportunity_triage` deja de poder unirse con
    `opportunity_history` y el trabajo del equipo se pierde en cada corrida.
    """
    triage = pytest.importorskip("app.services.triage")
    assert triage.entity_key(OPP) == history.entity_key("opportunity", **OPP)


def test_entity_key_acepta_la_fila_directa():
    """Atajo usado por el triaje: `entity_key(row)` == `entity_key("opportunity", **row)`."""
    assert history.entity_key(OPP) == history.entity_key("opportunity", **OPP)


def test_entity_key_no_confunde_campos_faltantes_con_vacios():
    """Un campo ausente vale None, no arrastra el valor del vecino."""
    assert history.entity_key("match", nike_product_id=1) != \
        history.entity_key("match", competitor_product_id=1)


# ── corridas ────────────────────────────────────────────────


def test_start_y_finish_run(db):
    run_id = history.start_run(db, source="demo")
    rows = query("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,), path=db)
    assert rows[0]["status"] == "running" and rows[0]["source"] == "demo"
    assert rows[0]["finished_at"] is None

    history.finish_run(run_id, db, status="ok", counts={"opportunities": 3})
    run = history.list_runs(db)[0]
    assert run["status"] == "ok" and run["finished_at"]
    assert run["counts"] == {"opportunities": 3}


def test_run_status_from_report():
    assert history.run_status_from_report({"a": {"status": "ok"}}) == "ok"
    assert history.run_status_from_report({"a": {"status": "ok"},
                                           "b": {"status": "error"}}) == "partial"
    assert history.run_status_from_report({"a": {"status": "error"}}) == "error"
    assert history.run_status_from_report({"a": {"status": "ok"},
                                           "b": {"status": "skipped"}}) == "ok"


# ── snapshot ────────────────────────────────────────────────


def test_snapshot_copia_el_estado_actual(db):
    _write_state(db, importance=50.0, match_score=70.0, signal_value=10.0)
    run_id = history.start_run(db, source="test")
    counts = history.snapshot(run_id, db)
    assert counts == {"matches": 1, "opportunities": 1, "signals": 1}

    row = query("SELECT * FROM opportunity_history", path=db)[0]
    assert row["entity_key"] == history.entity_key("opportunity", **OPP)
    assert row["business_importance"] == 50.0 and row["run_id"] == run_id


def test_snapshot_es_idempotente_por_run(db):
    _write_state(db, importance=50.0, match_score=70.0, signal_value=10.0)
    run_id = history.start_run(db, source="test")
    history.snapshot(run_id, db)
    history.snapshot(run_id, db)
    assert query("SELECT COUNT(*) AS n FROM opportunity_history", path=db)[0]["n"] == 1
    assert query("SELECT COUNT(*) AS n FROM match_history", path=db)[0]["n"] == 1


def test_snapshot_no_toca_las_tablas_de_scoring(db):
    """El snapshot es de sólo lectura sobre lo que calcularon los otros módulos."""
    _write_state(db, importance=50.0, match_score=70.0, signal_value=10.0)
    before = [query(f"SELECT * FROM {t}", path=db)
              for t in ("opportunities", "competitive_matches", "market_signals")]
    history.snapshot(history.start_run(db, source="test"), db)
    after = [query(f"SELECT * FROM {t}", path=db)
             for t in ("opportunities", "competitive_matches", "market_signals")]
    assert before == after


def test_snapshot_tolera_base_vacia(db):
    counts = history.snapshot(history.start_run(db, source="test"), db)
    assert counts == {"matches": 0, "opportunities": 0, "signals": 0}


# ── series ──────────────────────────────────────────────────


@pytest.fixture()
def tres_corridas(db):
    """Tres corridas con ids distintos y scores en alza."""
    for i, (imp, match, signal) in enumerate([(40.0, 60.0, 10.0),
                                              (50.0, 65.0, 12.0),
                                              (65.0, 72.0, 18.0)]):
        _write_state(db, importance=imp, match_score=match, signal_value=signal,
                     id_offset=i * 7)
        _fake_run(db)
    return db


def test_series_ordenadas_y_con_delta(tres_corridas):
    key = history.entity_key("opportunity", **OPP)
    points = history.opportunity_trend(key, tres_corridas)
    assert [p["business_importance"] for p in points] == [40.0, 50.0, 65.0]
    assert [p["run_id"] for p in points] == sorted(p["run_id"] for p in points)
    assert [p["delta"] for p in points] == [None, 10.0, 15.0]


def test_entity_key_estable_aunque_cambien_los_ids(tres_corridas):
    """Los ids de `opportunities` cambiaron en cada corrida; la clave no."""
    keys = query("SELECT DISTINCT entity_key FROM opportunity_history", path=tres_corridas)
    assert len(keys) == 1
    assert len(query("SELECT DISTINCT entity_key FROM match_history", path=tres_corridas)) == 1


def test_match_trend(tres_corridas):
    key = history.entity_key("match", nike_product_id=10, competitor_product_id=20)
    points = history.match_trend(key, tres_corridas)
    assert [p["match_score"] for p in points] == [60.0, 65.0, 72.0]
    assert points[-1]["delta"] == 7.0
    assert history.match_trend("no-existe", tres_corridas) == []


def test_signal_trend_ordenada(tres_corridas):
    points = history.signal_trend("social_momentum", "brand", 2, tres_corridas)
    assert [p["value"] for p in points] == [10.0, 12.0, 18.0]
    assert [p["delta"] for p in points] == [None, 2.0, 6.0]
    # el delta que reportó el módulo de origen se conserva aparte
    assert points[-1]["reported_delta"] == 1.0


# ── antigüedad ──────────────────────────────────────────────


def test_opportunity_age_cuenta_corridas_abiertas(tres_corridas):
    ages = history.opportunity_age(tres_corridas)
    key = history.entity_key("opportunity", **OPP)
    info = ages[key]
    assert info["runs_open"] == 3 and info["runs_seen"] == 3
    assert info["trend"] == "rising"
    assert info["first_seen"] <= info["last_seen"]
    assert info["importance_delta"] == 25.0
    assert info["is_open"] is True


def test_opportunity_age_excluye_las_cerradas(tres_corridas):
    """Si el recálculo ya no la produce, deja de estar abierta."""
    with get_conn(tres_corridas) as conn:
        conn.execute("DELETE FROM opportunities")
    _fake_run(tres_corridas)

    assert history.opportunity_age(tres_corridas) == {}
    cerradas = history.opportunity_age(tres_corridas, only_open=False)
    key = history.entity_key("opportunity", **OPP)
    assert cerradas[key]["is_open"] is False
    assert cerradas[key]["runs_seen"] == 3


def test_opportunity_age_racha_se_corta_si_desaparece(db):
    """`runs_open` es la racha final, no el total histórico."""
    _write_state(db, importance=40.0, match_score=60.0, signal_value=10.0)
    _fake_run(db)                                  # corrida 1: presente
    with get_conn(db) as conn:
        conn.execute("DELETE FROM opportunities")
    _fake_run(db)                                  # corrida 2: ausente
    _write_state(db, importance=44.0, match_score=60.0, signal_value=10.0, id_offset=3)
    _fake_run(db)                                  # corrida 3: vuelve

    info = history.opportunity_age(db)[history.entity_key("opportunity", **OPP)]
    assert info["runs_open"] == 1 and info["runs_seen"] == 2


def test_opportunity_age_incluye_el_triaje(tres_corridas):
    key = history.entity_key("opportunity", **OPP)
    with get_conn(tres_corridas) as conn:
        conn.execute("INSERT INTO opportunity_triage (entity_key, state, assignee) "
                     "VALUES (?,?,?)", (key, "snoozed", "nico"))
    info = history.opportunity_age(tres_corridas)[key]
    assert info["triage"] == {"state": "snoozed", "assignee": "nico", "snooze_until": None}


def test_classify_trend():
    assert history.classify_trend([10.0]) == "new"
    assert history.classify_trend([10.0, 10.2]) == "stable"
    assert history.classify_trend([10.0, 30.0]) == "rising"
    assert history.classify_trend([30.0, 10.0]) == "falling"
    assert history.classify_trend([]) == "new"


# ── aceleración (lo que market_signals no puede calcular) ────


def test_acceleration_necesita_tres_ventanas(db):
    _write_state(db, importance=40.0, match_score=60.0, signal_value=10.0)
    _fake_run(db)
    _write_state(db, importance=40.0, match_score=60.0, signal_value=12.0, id_offset=1)
    _fake_run(db)
    assert history.signal_acceleration("social_momentum", "brand", "2", db) is None

    _write_state(db, importance=40.0, match_score=60.0, signal_value=18.0, id_offset=2)
    _fake_run(db)
    acc = history.signal_acceleration("social_momentum", "brand", "2", db)
    # deltas: +2 y +6 -> la señal se está acelerando en +4
    assert acc["delta"] == 6.0 and acc["acceleration"] == 4.0 and acc["points"] == 3


def test_signal_accelerations_lista_solo_las_que_tienen_historial(tres_corridas):
    todas = history.signal_accelerations(tres_corridas)
    assert len(todas) == 1
    assert todas[0]["signal_type"] == "social_momentum"
    assert history.signal_accelerations(tres_corridas, signal_type="share_of_shelf") == []


def test_acceleration_no_escribe_en_market_signals(tres_corridas):
    """El dato se devuelve; escribir `market_signals` es de otro módulo."""
    before = query("SELECT acceleration FROM market_signals", path=tres_corridas)
    history.signal_accelerations(tres_corridas)
    assert query("SELECT acceleration FROM market_signals", path=tres_corridas) == before


# ── preservación entre resets ───────────────────────────────


def test_capture_restore_sobrevive_al_drop(tres_corridas):
    key = history.entity_key("opportunity", **OPP)
    with get_conn(tres_corridas) as conn:
        conn.execute("INSERT INTO opportunity_triage (entity_key, state) VALUES (?,?)",
                     (key, "open"))

    carried = history.capture(tres_corridas)
    init_db(tres_corridas, drop=True)              # el pipeline borra el archivo entero
    assert history.opportunity_trend(key, tres_corridas) == []

    history.restore(carried, tres_corridas)
    assert len(history.opportunity_trend(key, tres_corridas)) == 3
    assert len(history.list_runs(tres_corridas)) == 3
    assert query("SELECT * FROM opportunity_triage", path=tres_corridas)[0]["state"] == "open"


def test_capture_tolera_base_inexistente(tmp_path):
    assert history.capture(tmp_path / "no-existe.db") == {}
    assert history.restore({}, tmp_path / "no-existe.db") == {}


# ── integración con el pipeline real ────────────────────────


@pytest.fixture(scope="module")
def pipeline_3x(tmp_path_factory):
    """Tres corridas reales del pipeline sobre la misma base."""
    from app import pipeline

    db = tmp_path_factory.mktemp("pipeline") / "intelligence.db"
    reports = [pipeline.run_all(db, reset=True, source="demo") for _ in range(3)]
    return db, reports


def test_tres_corridas_acumulan_tres_snapshots(pipeline_3x):
    db, reports = pipeline_3x
    assert all(r["snapshot"]["status"] == "ok" for r in reports)

    runs = history.list_runs(db)
    assert len(runs) == 3
    # Cerradas y con conteos; el estado depende de las etapas (`ok` o `partial`).
    assert all(r["finished_at"] and r["status"] in ("ok", "partial") for r in runs)
    assert all(r["snapshot"]["opportunities"] > 0 for r in runs)

    corridas = query("SELECT COUNT(DISTINCT run_id) AS n FROM opportunity_history", path=db)
    assert corridas[0]["n"] == 3


def test_el_historial_sobrevive_al_reset_del_pipeline(pipeline_3x):
    """El pipeline hace `init_db(drop=True)` y aun así quedan las 3 corridas."""
    db, _ = pipeline_3x
    ages = history.opportunity_age(db)
    assert ages, "sin oportunidades abiertas"
    assert max(a["runs_open"] for a in ages.values()) == 3
    # una oportunidad que sigue apareciendo tiene serie completa y ordenada
    key = max(ages, key=lambda k: ages[k]["runs_open"])
    points = history.opportunity_trend(key, db)
    assert len(points) == 3
    assert [p["observed_at"] for p in points] == sorted(p["observed_at"] for p in points)


def test_entity_key_del_pipeline_no_depende_de_los_ids(pipeline_3x):
    """Cada corrida recalcula `opportunities` desde cero: mismas claves igual."""
    db, _ = pipeline_3x
    por_corrida = {}
    for row in query("SELECT run_id, entity_key FROM opportunity_history", path=db):
        por_corrida.setdefault(row["run_id"], set()).add(row["entity_key"])
    conjuntos = list(por_corrida.values())
    assert len(conjuntos) == 3
    assert conjuntos[0] == conjuntos[1] == conjuntos[2]

    # y la clave se puede recalcular desde la tabla viva, sin pasar por el id
    opp = query("SELECT * FROM opportunities LIMIT 1", path=db)[0]
    assert history.opportunity_key(opp) in conjuntos[0]


def test_pipeline_sin_historial(tmp_path):
    """`history=False` deja el pipeline exactamente como estaba antes."""
    from app import pipeline

    db = tmp_path / "sin-historial.db"
    report = pipeline.run_all(db, reset=True, stages=["seed"], history=False)
    assert "snapshot" not in report
    assert query("SELECT COUNT(*) AS n FROM pipeline_runs", path=db)[0]["n"] == 0


# ── API ─────────────────────────────────────────────────────


@pytest.fixture()
def client(tres_corridas, monkeypatch):
    """API montada sobre la DB de prueba (main.py es de otro módulo)."""
    import app.api.serializers as serializers
    import app.db as appdb
    from app.api.routers import history as router_module

    def _query(sql, params=(), path=None):
        return appdb.query(sql, params, tres_corridas)

    monkeypatch.setattr(history, "query", _query)
    monkeypatch.setattr(serializers, "query", _query)
    monkeypatch.setattr(history, "get_conn", lambda path=None: appdb.get_conn(tres_corridas))

    api = FastAPI()
    api.include_router(router_module.router)
    return TestClient(api)


def test_api_runs(client):
    body = client.get("/api/history/runs").json()
    assert body["total"] == 3
    assert body["items"][0]["id"] > body["items"][-1]["id"]      # más nueva primero
    assert body["items"][0]["snapshot"]["opportunities"] == 1


def test_api_opportunity_trend(client):
    key = history.entity_key("opportunity", **OPP)
    body = client.get(f"/api/history/opportunities/{key}").json()
    assert [p["business_importance"] for p in body["points"]] == [40.0, 50.0, 65.0]
    assert body["summary"]["trend"] == "rising" and body["summary"]["change"] == 25.0
    assert body["age"]["runs_open"] == 3
    assert body["nike_product"]["product_name"] == "Pegasus 41"
    assert body["competitor_product"]["product_name"] == "Novablast 4"


def test_api_match_trend(client):
    key = history.entity_key("match", nike_product_id=10, competitor_product_id=20)
    body = client.get(f"/api/history/matches/{key}").json()
    assert [p["match_score"] for p in body["points"]] == [60.0, 65.0, 72.0]
    assert body["summary"]["trend"] == "rising"


def test_api_404_con_clave_desconocida(client):
    assert client.get("/api/history/matches/deadbeef").status_code == 404
    assert client.get("/api/history/opportunities/deadbeef").status_code == 404


def test_api_opportunity_ages(client):
    body = client.get("/api/history/opportunities").json()
    assert body["total"] == 1 and body["runs"] == 3
    item = body["items"][0]
    assert item["runs_open"] == 3 and item["trend"] == "rising"
    assert item["retailer"]["name"] == "Dexter"


def test_api_signals_con_aceleracion(client):
    body = client.get("/api/history/signals", params={
        "signal_type": "social_momentum", "entity_type": "brand", "entity_id": "2"}).json()
    assert [p["value"] for p in body["points"]] == [10.0, 12.0, 18.0]
    assert body["acceleration"]["acceleration"] == 4.0

    vacio = client.get("/api/history/signals", params={
        "signal_type": "social_momentum", "entity_type": "brand", "entity_id": "999"}).json()
    assert vacio["points"] == [] and vacio["acceleration"] is None
