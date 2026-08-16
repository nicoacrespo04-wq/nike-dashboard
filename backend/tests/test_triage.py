"""Tests del triaje del Opportunity Center.

El test que justifica todo el diseño es
``test_el_triaje_sobrevive_a_una_corrida_completa_del_pipeline``: se descarta
una oportunidad, se corre el pipeline REAL (que borra el archivo SQLite entero
y recalcula las 61 oportunidades desde los CSV) y la oportunidad tiene que
seguir descartada. Si eso falla, la pantalla vuelve a ser un reporte lindo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import triage as triage_router
from app.db import get_conn, init_db, query
from app.services import triage

FUTURE = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
PAST = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

ROW = {
    "opportunity_type": "price_competitiveness_risk",
    "nike_product_id": 1,
    "competitor_product_id": 7,
    "retailer_id": 3,
    "country_code": "AR",
}


def _catalog(conn) -> None:
    """Mínimo indispensable para que `opportunities` no viole sus foreign keys."""
    conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
    conn.executemany("INSERT INTO brands (id, name, is_focus) VALUES (?,?,?)",
                     [(1, "Nike", 1), (2, "Adidas", 0)])
    conn.execute("INSERT INTO retailers (id, name, country_code, channel, importance) "
                 "VALUES (3,'Mercado Libre','AR','MARKETPLACE',0.9)")
    conn.executemany(
        "INSERT INTO products (id, brand_id, country_code, product_name) VALUES (?,?,?,?)",
        [(1, 1, "AR", "Nike Pegasus 41"), (2, 1, "AR", "Nike Vomero 17"),
         (3, 1, "AR", "Nike Invincible 3"), (7, 2, "AR", "Adidas Adizero")],
    )


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "triage.db"
    init_db(path, drop=True)
    return path


@pytest.fixture()
def key() -> str:
    return triage.entity_key(ROW)


@pytest.fixture()
def api(tmp_path, monkeypatch) -> TestClient:
    """App mínima con SÓLO el router de triaje, apuntada a una base temporal.

    Los routers y servicios leen la base por defecto (``DB_PATH``); acá se
    redirige el acceso para no tocar la base real del repo.
    """
    from app.db import get_conn as real_conn
    from app.db import query as real_query

    path = tmp_path / "api.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        _catalog(conn)
        conn.execute(
            "INSERT INTO opportunities (id, opportunity_type, family, severity, "
            "nike_product_id, competitor_product_id, retailer_id, country_code, title, "
            "business_importance) VALUES (1,'price_competitiveness_risk','pricing','HIGH',"
            "1,7,3,'AR','Pegasus 41 más cara que su comparable',82.5)"
        )

    def fake_query(sql, params=(), _path=None):
        return real_query(sql, params, path)

    def fake_conn(_path=None):
        return real_conn(path)

    for module in (triage, triage_router):
        monkeypatch.setattr(module, "query", fake_query, raising=False)
        monkeypatch.setattr(module, "get_conn", fake_conn, raising=False)
    # El journal se escribiría al lado de la base por defecto, no de la temporal.
    monkeypatch.setattr(triage, "JOURNAL_ENABLED", False)

    app = FastAPI()
    app.include_router(triage_router.router)
    return TestClient(app)


# ── entity_key: el contrato más delicado ────────────────────


def test_la_clave_es_determinista_e_insensible_al_formato():
    variant = {
        "opportunity_type": "  PRICE_Competitiveness_Risk ",
        "nike_product_id": "1",
        "competitor_product_id": 7.0,
        "retailer_id": " 3 ",
        "country_code": "ar",
    }
    assert triage.entity_key(ROW) == triage.entity_key(variant)
    assert triage.entity_key(ROW) == triage.entity_key(dict(ROW))


def test_la_clave_cambia_si_cambia_cualquier_campo_de_identidad():
    base = triage.entity_key(ROW)
    for field, other in (
        ("opportunity_type", "assortment_gap"),
        ("nike_product_id", 2),
        ("competitor_product_id", None),
        ("retailer_id", 4),
        ("country_code", "MX"),
    ):
        assert triage.entity_key({**ROW, field: other}) != base, field


def test_la_clave_no_confunde_none_con_cero():
    assert triage.entity_key({**ROW, "retailer_id": None}) != triage.entity_key({**ROW, "retailer_id": 0})


def test_la_clave_sale_igual_de_la_fila_cruda_y_de_la_oportunidad_serializada():
    """El serializer reemplaza los ids por objetos anidados: la clave no puede cambiar."""
    serialized = {
        "id": 99,
        "opportunity_type": "price_competitiveness_risk",
        "country_code": "AR",
        "nike_product": {"id": 1, "product_name": "Nike Pegasus 41"},
        "competitor_product": {"id": 7, "product_name": "Adidas Adizero"},
        "retailer": {"id": 3, "name": "Mercado Libre"},
    }
    assert triage.entity_key(serialized) == triage.entity_key(ROW)


def test_la_clave_es_un_hash_corto_y_urlsafe():
    key = triage.entity_key(ROW)
    assert len(key) == triage.KEY_LENGTH
    assert all(c in "0123456789abcdef" for c in key)


@pytest.mark.skipif(triage.KEY_SOURCE != "history",
                    reason="app.services.history todavía no existe: se usa el fallback local")
def test_la_clave_coincide_con_la_de_history():
    """EL test del contrato: si los dos hashes divergen, el triaje se pierde.

    `history` escribe `opportunity_history.entity_key` y el pipeline rescata
    `opportunity_triage` con esa misma identidad. Dos definiciones distintas =
    triaje huérfano en cada corrida.
    """
    from app.services import history

    for row in (ROW,
                {**ROW, "competitor_product_id": None, "retailer_id": None},
                {**ROW, "country_code": "ar", "nike_product_id": "1"}):
        assert triage.entity_key(row) == history.entity_key("opportunity", **row)
        assert triage.entity_key(row) == history.opportunity_key(row)
        # Y el fallback local tiene que dar lo mismo que la definición canónica:
        # es lo que corre si `history` desaparece o cambia de firma.
        assert triage.entity_key(row) == triage._fallback_entity_key(
            [row[f] for f in triage.KEY_FIELDS])


@pytest.mark.skipif(triage.KEY_SOURCE != "history", reason="sin history no hay qué comparar")
def test_la_clave_de_una_fila_entera_de_la_base_es_la_misma():
    """`history` acepta la fila completa; el triaje sólo mira los 5 campos."""
    from app.services import history

    full_row = {**ROW, "id": 42, "title": "Riesgo de precio", "severity": "HIGH",
                "family": "pricing", "business_importance": 82.5}
    assert triage.entity_key(full_row) == history.opportunity_key(full_row)


# ── estados y transiciones ──────────────────────────────────


def test_sin_fila_una_oportunidad_esta_abierta(db, key):
    assert triage.get_state(key, db) is None
    default = triage.default_state(key)
    assert default["state"] == "open"
    assert default["default"] is True
    assert default["actionable"] is True


def test_descartar_persiste_y_deja_de_ser_accionable(db, key):
    state = triage.set_state(key, "dismissed", db, note="No aplica a este mercado",
                             updated_by="nico")
    assert state["state"] == "dismissed"
    assert state["actionable"] is False
    assert state["note"] == "No aplica a este mercado"
    assert state["updated_by"] == "nico"
    assert triage.get_state(key, db)["state"] == "dismissed"


def test_no_se_materializan_filas_para_lo_que_nadie_toco(db, key):
    triage.set_state(key, "dismissed", db)
    assert query("SELECT COUNT(*) AS n FROM opportunity_triage", (), db)[0]["n"] == 1


def test_estado_invalido_explica_los_permitidos(db, key):
    with pytest.raises(triage.TriageError) as exc:
        triage.set_state(key, "descartada", db)
    assert "descartada" in str(exc.value)
    for state in triage.STATES:
        assert state in str(exc.value)


@pytest.mark.parametrize("target", ["snoozed", "resolved"])
def test_desde_descartada_solo_se_puede_reabrir(db, key, target):
    triage.set_state(key, "dismissed", db)
    with pytest.raises(triage.TriageError) as exc:
        triage.set_state(key, target, db, snooze_until=FUTURE)
    assert "dismissed" in str(exc.value)

    assert triage.set_state(key, "open", db)["state"] == "open"
    assert triage.set_state(key, target, db, snooze_until=FUTURE)["state"] == target


def test_resuelta_se_puede_reabrir_si_el_problema_vuelve(db, key):
    triage.set_state(key, "resolved", db)
    assert triage.set_state(key, "open", db, note="volvió a aparecer")["state"] == "open"


def test_todas_las_transiciones_declaradas_apuntan_a_estados_validos():
    assert set(triage.TRANSITIONS) == set(triage.STATES)
    for origin, targets in triage.TRANSITIONS.items():
        assert origin in targets, f"{origin} tiene que poder quedarse donde está"
        assert set(targets) <= set(triage.STATES)


# ── snooze ──────────────────────────────────────────────────


def test_posponer_exige_fecha(db, key):
    with pytest.raises(triage.TriageError, match="snooze_until"):
        triage.set_state(key, "snoozed", db)


def test_posponer_exige_fecha_futura(db, key):
    with pytest.raises(triage.TriageError, match="futuro"):
        triage.set_state(key, "snoozed", db, snooze_until=PAST)


def test_posponer_normaliza_una_fecha_al_final_del_dia(db, key):
    state = triage.set_state(key, "snoozed", db, snooze_until=FUTURE)
    assert state["snooze_until"] == f"{FUTURE} 23:59:59"


def test_asignar_sobre_algo_pospuesto_conserva_el_plazo(db, key):
    """Editar el responsable no puede obligar a reelegir la fecha (la UI no la manda)."""
    original = triage.set_state(key, "snoozed", db, snooze_until=FUTURE)
    same = triage.set_state(key, "snoozed", db, assignee="flor")
    assert same["snooze_until"] == original["snooze_until"]
    assert same["assignee"] == "flor"


def test_salir_de_snoozed_limpia_el_plazo(db, key):
    triage.set_state(key, "snoozed", db, snooze_until=FUTURE)
    assert triage.set_state(key, "dismissed", db)["snooze_until"] is None


def test_expire_snoozes_reabre_lo_vencido_y_respeta_lo_vigente(db, key):
    other = triage.entity_key({**ROW, "nike_product_id": 2})
    triage.set_state(key, "snoozed", db, snooze_until=FUTURE)
    triage.set_state(other, "snoozed", db, snooze_until=FUTURE)
    # Se vence uno a mano: la API nunca permitiría escribir una fecha pasada.
    with get_conn(db) as conn:
        conn.execute("UPDATE opportunity_triage SET snooze_until = ? WHERE entity_key = ?",
                     (f"{PAST} 10:00:00", key))

    assert triage.expire_snoozes(db) == 1
    assert triage.get_state(key, db)["state"] == "open"
    assert triage.get_state(key, db)["snooze_until"] is None
    assert triage.get_state(key, db)["updated_by"] == "system:snooze_expired"
    assert triage.get_state(other, db)["state"] == "snoozed"
    assert triage.expire_snoozes(db) == 0


def test_un_snooze_sin_fecha_no_esconde_la_oportunidad_para_siempre(db, key):
    triage.set_state(key, "snoozed", db, snooze_until=FUTURE)
    with get_conn(db) as conn:
        conn.execute("UPDATE opportunity_triage SET snooze_until = NULL WHERE entity_key = ?", (key,))
    assert triage.expire_snoozes(db) == 1
    assert triage.get_state(key, db)["state"] == "open"


# ── auditoría ───────────────────────────────────────────────


def test_first_seen_at_se_fija_una_vez_y_no_se_pisa(db, key):
    first = triage.set_state(key, "dismissed", db, updated_by="nico")
    with get_conn(db) as conn:  # envejecemos el updated_at para ver el cambio
        conn.execute("UPDATE opportunity_triage SET updated_at = '2000-01-01 00:00:00'")

    later = triage.set_state(key, "open", db, updated_by="flor")
    assert later["first_seen_at"] == first["first_seen_at"]
    assert later["updated_at"] != "2000-01-01 00:00:00"
    assert later["updated_by"] == "flor"


def test_asignar_no_pisa_la_nota_y_el_string_vacio_limpia(db, key):
    triage.set_state(key, "open", db, note="mirar con el equipo de pricing")
    assigned = triage.set_state(key, "open", db, assignee="  flor  ")
    assert assigned["assignee"] == "flor"
    assert assigned["note"] == "mirar con el equipo de pricing"

    cleared = triage.set_state(key, "open", db, assignee="")
    assert cleared["assignee"] is None
    assert cleared["note"] == "mirar con el equipo de pricing"


# ── lecturas masivas ────────────────────────────────────────


def test_bulk_states_devuelve_solo_lo_persistido(db, key):
    other = triage.entity_key({**ROW, "retailer_id": 9})
    triage.set_state(key, "dismissed", db)
    states = triage.bulk_states([key, other, key, ""], db)
    assert set(states) == {key}
    assert states[key]["state"] == "dismissed"
    assert triage.bulk_states([], db) == {}


def test_apply_to_adjunta_el_triaje_sin_mutar_la_entrada(db, key):
    triage.set_state(key, "dismissed", db, assignee="nico")
    opportunities = [dict(ROW, id=1, title="Riesgo de precio"),
                     dict(ROW, id=2, nike_product_id=2, title="Otra")]

    enriched = triage.apply_to(opportunities, db)
    assert enriched[0]["entity_key"] == key
    assert enriched[0]["triage"]["state"] == "dismissed"
    assert enriched[0]["triage"]["assignee"] == "nico"
    assert enriched[1]["triage"]["state"] == "open"
    assert enriched[1]["triage"]["default"] is True
    assert "triage" not in opportunities[0], "apply_to no puede mutar la entrada"
    assert triage.apply_to([], db) == []


def test_apply_to_vence_los_snoozes_al_leer(db, key):
    triage.set_state(key, "snoozed", db, snooze_until=FUTURE)
    with get_conn(db) as conn:
        conn.execute("UPDATE opportunity_triage SET snooze_until = ?", (f"{PAST} 10:00:00",))
    assert triage.apply_to([dict(ROW, id=1)], db)[0]["triage"]["state"] == "open"


def test_filter_by_state_deja_solo_lo_accionable(db, key):
    triage.set_state(key, "dismissed", db)
    enriched = triage.apply_to([dict(ROW, id=1), dict(ROW, id=2, nike_product_id=2)], db)
    assert [o["id"] for o in triage.filter_by_state(enriched)] == [2]
    assert [o["id"] for o in triage.filter_by_state(enriched, ["dismissed"])] == [1]


def test_stats_cuenta_filas_y_oportunidades_vigentes(db, key):
    with get_conn(db) as conn:
        _catalog(conn)
        for i in (1, 2, 3):
            conn.execute(
                "INSERT INTO opportunities (id, opportunity_type, nike_product_id, "
                "competitor_product_id, retailer_id, country_code, title) VALUES (?,?,?,?,?,?,?)",
                (i, ROW["opportunity_type"], i, 7, 3, "AR", f"Oportunidad {i}"),
            )
    triage.set_state(key, "dismissed", db, assignee="nico")

    stats = triage.stats(db)
    assert stats["tracked"] == 1
    assert stats["by_state"] == {"open": 0, "snoozed": 0, "dismissed": 1, "resolved": 0}
    assert stats["assignees"] == [{"assignee": "nico", "n": 1}]
    assert stats["opportunities"]["total"] == 3
    assert stats["opportunities"]["dismissed"] == 1
    assert stats["opportunities"]["actionable"] == 2


def test_una_base_sin_la_tabla_no_rompe_las_lecturas(tmp_path):
    path = tmp_path / "vieja.db"
    with get_conn(path) as conn:
        conn.execute("CREATE TABLE dummy (id INTEGER)")
    assert triage.get_state("abc", path) is None
    assert triage.bulk_states(["abc"], path) == {}
    assert triage.apply_to([dict(ROW)], path)[0]["triage"]["state"] == "open"
    assert triage.expire_snoozes(path) == 0
    assert triage.stats(path)["tracked"] == 0
    with pytest.raises(triage.TriageError, match="opportunity_triage"):
        triage.set_state("abc", "dismissed", path)


# ── journal: sobrevivir al borrado del archivo ──────────────


def test_el_journal_repone_el_triaje_si_la_base_se_recrea(db, key):
    triage.set_state(key, "dismissed", db, note="ruido conocido", updated_by="nico")
    assert triage.journal_path(db).exists()

    init_db(db, drop=True)  # exactamente lo que hace el pipeline
    assert query("SELECT COUNT(*) AS n FROM opportunity_triage", (), db)[0]["n"] == 0

    restored = triage.get_state(key, db)
    assert restored is not None and restored["state"] == "dismissed"
    assert restored["note"] == "ruido conocido"
    assert restored["updated_by"] == "nico"


def test_el_journal_no_resucita_lo_que_se_borro_a_proposito(db, key):
    triage.set_state(key, "dismissed", db)
    triage.clear_state(key, db)
    init_db(db, drop=True)
    assert triage.get_state(key, db) is None


def test_la_base_le_gana_al_journal(db, key):
    triage.set_state(key, "dismissed", db)
    with get_conn(db) as conn:  # cambio "externo", sin journal
        conn.execute("UPDATE opportunity_triage SET state = 'resolved' WHERE entity_key = ?", (key,))
    assert triage.get_state(key, db)["state"] == "resolved"


def test_una_linea_corrupta_del_journal_no_rompe_la_restauracion(db, key):
    triage.set_state(key, "dismissed", db)
    with triage.journal_path(db).open("a", encoding="utf-8") as fh:
        fh.write('{"op": "set", "entity_ke\n')  # línea a medio escribir
    init_db(db, drop=True)
    assert triage.get_state(key, db)["state"] == "dismissed"


# ── API ─────────────────────────────────────────────────────


def test_get_de_una_clave_sin_triaje_devuelve_open(api, key):
    body = api.get(f"/api/triage/{key}").json()
    assert body["state"] == "open"
    assert body["default"] is True


def test_post_aplica_la_transicion_y_el_get_la_ve(api, key):
    res = api.post(f"/api/triage/{key}",
                   json={"state": "dismissed", "note": "duplicada", "updated_by": "nico"})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "dismissed"
    assert api.get(f"/api/triage/{key}").json()["state"] == "dismissed"


def test_post_con_estado_invalido_devuelve_422_con_mensaje_claro(api, key):
    res = api.post(f"/api/triage/{key}", json={"state": "archivada"})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "archivada" in detail and "dismissed" in detail


def test_post_con_transicion_invalida_devuelve_422(api, key):
    api.post(f"/api/triage/{key}", json={"state": "dismissed"})
    res = api.post(f"/api/triage/{key}", json={"state": "resolved"})
    assert res.status_code == 422
    assert "dismissed" in res.json()["detail"]


def test_post_snoozed_sin_fecha_devuelve_422(api, key):
    res = api.post(f"/api/triage/{key}", json={"state": "snoozed"})
    assert res.status_code == 422
    assert "snooze_until" in res.json()["detail"]


def test_listado_trae_stats_y_el_contexto_de_la_oportunidad(api, key):
    api.post(f"/api/triage/{key}", json={"state": "dismissed", "assignee": "nico"})
    body = api.get("/api/triage").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["state"] == "dismissed"
    assert item["opportunity"]["title"] == "Pegasus 41 más cara que su comparable"
    assert body["stats"]["opportunities"]["actionable"] == 0
    assert body["states"] == list(triage.STATES)


def test_listado_filtra_por_estado(api, key):
    api.post(f"/api/triage/{key}", json={"state": "dismissed"})
    assert api.get("/api/triage", params={"state": "dismissed"}).json()["total"] == 1
    assert api.get("/api/triage", params={"state": "resolved"}).json()["total"] == 0
    assert api.get("/api/triage", params={"state": "papelera"}).status_code == 422


def test_bulk_aplica_a_varias_y_reporta_los_rechazos(api, key):
    other = triage.entity_key({**ROW, "nike_product_id": 2})
    api.post(f"/api/triage/{key}", json={"state": "dismissed"})

    res = api.post("/api/triage/bulk",
                   json={"entity_keys": [key, other], "state": "resolved", "updated_by": "flor"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["updated"] == 1
    assert body["items"][0]["entity_key"] == other
    assert body["rejected"][0]["entity_key"] == key
    assert "dismissed" in body["rejected"][0]["detail"]


def test_bulk_sin_claves_o_con_estado_invalido_devuelve_422(api, key):
    assert api.post("/api/triage/bulk", json={"entity_keys": [], "state": "open"}).status_code == 422
    res = api.post("/api/triage/bulk", json={"entity_keys": [key], "state": "nope"})
    assert res.status_code == 422
    assert "nope" in res.json()["detail"]


# ── el test que justifica el diseño ─────────────────────────


def test_el_triaje_sobrevive_a_una_corrida_completa_del_pipeline(tmp_path):
    """Descartar → correr el pipeline entero → sigue descartada.

    ``pipeline.run_all`` borra el archivo SQLite y recalcula las oportunidades
    desde cero: los ``opportunities.id`` cambian, las filas son otras. Lo único
    que ata la decisión del equipo con la oportunidad recalculada es
    ``entity_key`` (identidad) + el journal (durabilidad).
    """
    from app import pipeline

    db = tmp_path / "intelligence.db"
    assert pipeline.run_all(db, reset=True)["opportunities"]["status"] == "ok"

    before = query("SELECT * FROM opportunities ORDER BY business_importance DESC", (), db)
    assert before, "el pipeline demo tiene que generar oportunidades"
    target = before[0]
    key = triage.entity_key(target)

    triage.set_state(key, "dismissed", db, assignee="nico",
                     note="ya lo trabajamos con el retailer", updated_by="nico")
    triage.set_state(triage.entity_key(before[1]), "snoozed", db,
                     snooze_until=FUTURE, updated_by="nico")

    # Corrida completa: borra el archivo, re-seedea y recalcula todo.
    assert pipeline.run_all(db, reset=True)["opportunities"]["status"] == "ok"

    after = query("SELECT * FROM opportunities ORDER BY business_importance DESC", (), db)
    assert len(after) == len(before)
    same = [row for row in after if triage.entity_key(row) == key]
    assert len(same) == 1, "la oportunidad tiene que volver a existir con la misma identidad"

    state = triage.get_state(key, db)
    assert state is not None, "el triaje se perdió en la corrida"
    assert state["state"] == "dismissed"
    assert state["assignee"] == "nico"
    assert state["note"] == "ya lo trabajamos con el retailer"

    enriched = triage.apply_to(after, db)
    dismissed = [o for o in enriched if o["triage"]["state"] == "dismissed"]
    snoozed = [o for o in enriched if o["triage"]["state"] == "snoozed"]
    assert len(dismissed) == 1 and dismissed[0]["id"] == same[0]["id"]
    assert len(snoozed) == 1
    assert triage.stats(db)["opportunities"]["actionable"] == len(after) - 2
