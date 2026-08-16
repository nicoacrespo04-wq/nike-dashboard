"""Tests del harness de calibración.

El test central es `test_redescubre_los_dos_casos_historicos`: se arma una config
TEMPORAL con los valores que ya rompieron el motor una vez
(``severity_thresholds.critical: 78`` y ``premiumization.min_match_score: 70``)
y se verifica que el harness los marca ``UNREACHABLE`` por su cuenta, sin que
nadie le diga dónde mirar.

La base se construye una sola vez por módulo corriendo el pipeline completo
sobre un SQLite temporal: el harness se juzga contra el motor real, no contra un
fixture inventado.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from app import calibration
from app import config as config_module
from app.config import get_config, reload_config, section
from app.db import init_db


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory) -> Path:
    """Base demo completa (pipeline real) reutilizada por todo el módulo."""
    from app import pipeline

    db = tmp_path_factory.mktemp("calibration") / "intelligence.db"
    report = pipeline.run_all(db, reset=True)
    assert report["matching"]["status"] == "ok", report
    assert report["opportunities"]["status"] == "ok", report
    return db


@pytest.fixture(scope="module")
def data(demo_db):
    """Muestra del harness sobre la base demo (cara de calcular: una sola vez)."""
    return calibration.collect(demo_db)


@pytest.fixture()
def temp_config(tmp_path, monkeypatch):
    """Escribe una copia modificada de weights.yaml y la deja activa.

    No toca ``backend/config/weights.yaml``: apunta ``CONFIG_PATH`` a una copia
    temporal y restaura la config real al terminar.
    """
    def _apply(**overrides):
        raw = yaml.safe_load(Path(config_module.CONFIG_PATH).read_text(encoding="utf-8"))
        for path, value in overrides.items():
            node = raw
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        target = tmp_path / "weights.yaml"
        target.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
        monkeypatch.setattr(config_module, "CONFIG_PATH", target)
        reload_config()
        return target

    yield _apply
    monkeypatch.undo()
    reload_config()


# ── 1. los dos casos históricos ─────────────────────────────


def test_redescubre_los_dos_casos_historicos(demo_db, temp_config):
    """Con la calibración vieja, el harness marca los dos umbrales UNREACHABLE."""
    # Los valores históricos fueron 78/60 y 70, calibrados cuando el techo de
    # match era 69.1. Al recuperar el factor visual la escala subió a ~75, así
    # que fijarlos literalmente ya no reproduce el incidente. Se derivan del
    # dato vigente: lo que se prueba es el MECANISMO —un umbral por encima del
    # techo se reporta UNREACHABLE— no unos números que envejecen.
    # CRITICAL se pone por encima de la COTA ANALÍTICA (no del máximo
    # observado): así el veredicto tiene que apoyarse en la fórmula del gate y
    # no en que al dataset no le haya tocado. HIGH sólo supera lo observado,
    # que es el caso empírico.
    base = {r["path"]: r for r in calibration.reachability_report(demo_db)}
    bi_analytic = base["business_importance.severity_thresholds.high"]["analytic_max"]
    bi_max = calibration.score_distributions(demo_db)["business_importance"]["max"]
    match_max = calibration.score_distributions(demo_db)["match_score"]["max"]
    temp_config(**{
        "business_importance.severity_thresholds": {"critical": bi_analytic + 5.0,
                                                    "high": bi_max + 5.0,
                                                    "medium": 40.0},
        "opportunities.premiumization_opportunity.min_match_score": match_max + 5.0,
    })

    rows = {r["path"]: r for r in calibration.reachability_report(demo_db)}

    critical = rows["business_importance.severity_thresholds.critical"]
    assert critical["status"] == calibration.STATUS_UNREACHABLE
    assert critical["n_pass"] == 0
    # Con el umbral por encima de la cota derivada del gate, el veredicto se
    # apoya en la fórmula y no en la suerte del dataset.
    assert critical["basis"] == "analítica"
    assert critical["analytic_max"] < bi_analytic + 5.0

    high = rows["business_importance.severity_thresholds.high"]
    assert high["status"] == calibration.STATUS_UNREACHABLE
    assert high["n_pass"] == 0

    premium = rows["opportunities.premiumization_opportunity.min_match_score"]
    assert premium["status"] == calibration.STATUS_UNREACHABLE
    assert premium["n_pass"] == 0
    assert premium["observed_max"] < match_max + 5.0

    # Y quedan listados como problemas en el resumen del reporte.
    summary = calibration.report(demo_db)["summary"]
    assert summary["unreachable"] >= 3
    assert "opportunities.premiumization_opportunity.min_match_score" in summary["problem_paths"]


def test_regla_apagada_por_umbral_se_reporta_como_rota(demo_db, data, temp_config):
    """min_match_score=70 deja premiumization en 0: eso es ROTA, no 'nada que reportar'."""
    # Con la config real la regla produce y no aparece bloqueada.
    healthy = {r["rule"]: r for r in calibration.rule_yield_report(demo_db, data)}
    assert healthy["premiumization_opportunity"]["status"] == calibration.YIELD_OK
    assert healthy["premiumization_opportunity"]["blocking"] == []

    match_max = calibration.score_distributions(demo_db)["match_score"]["max"]
    temp_config(**{
        "opportunities.premiumization_opportunity.min_match_score": match_max + 5.0})

    rows = {r["rule"]: r for r in calibration.rule_yield_report(demo_db)}
    premium = rows["premiumization_opportunity"]

    assert premium["n"] == 0
    assert premium["status"] == calibration.YIELD_BROKEN
    assert "opportunities.premiumization_opportunity.min_match_score" in premium["blocking"]
    assert "inalcanzable" in premium["diagnosis"]


def test_severidad_sin_masa_arriba_se_propone_reparto(demo_db, temp_config):
    """Con las bandas altas vacías, la sugerencia devuelve masa a cada banda.

    Los cortes se DERIVAN del máximo observado en vez de fijar 78/60/40: esos
    números reproducían el incidente sólo mientras el gate multiplicativo le
    ponía techo a la escala (importancia máxima ~56). Arreglado el gate, 78/60
    son alcanzables y ya no arman el escenario; lo que se prueba acá es el
    MECANISMO —bandas superiores sin un solo registro => propuesta de reparto—
    no unos números que envejecen con la escala.
    """
    bi_max = calibration.score_distributions(demo_db)["business_importance"]["max"]
    dead_critical, dead_high = bi_max + 10.0, bi_max + 5.0
    temp_config(**{"business_importance.severity_thresholds": {"critical": dead_critical,
                                                               "high": dead_high,
                                                               "medium": 40.0}})
    suggestions = calibration.suggest_thresholds(demo_db)
    critical = suggestions["business_importance.severity_thresholds.critical"]

    assert critical["actual"] == dead_critical
    assert critical["sugerido"] < dead_critical
    assert critical["distribucion_actual"].get("CRITICAL", 0) == 0
    assert critical["distribucion_actual"].get("HIGH", 0) == 0
    # Cada banda con masa después del cambio.
    after = critical["distribucion_sugerida"]
    assert all(after.get(band, 0) > 0 for band in ("CRITICAL", "HIGH", "MEDIUM", "LOW"))


# ── 2. clasificación de umbrales ────────────────────────────


def test_umbral_trivial_se_detecta(demo_db, temp_config):
    """Un umbral por debajo del mínimo observado no filtra nada."""
    temp_config(**{"opportunities.full_price_opportunity.min_nike_discount_pct": 0.0})
    rows = {r["path"]: r for r in calibration.reachability_report(demo_db)}
    row = rows["opportunities.full_price_opportunity.min_nike_discount_pct"]

    assert row["status"] == calibration.STATUS_TRIVIAL
    assert row["n_pass"] == row["n"] > 0
    assert row["defect"] is True


def test_gate_trivial_no_es_defecto(data):
    """Un mínimo de evidencia que todos superan protege, no molesta."""
    rows = {r["path"]: r for r in calibration.reachability_report(data.db_path, data)}
    row = rows["competitive_match.social.min_comentions"]
    assert row["kind"] == calibration.KIND_GATE
    if row["status"] == calibration.STATUS_TRIVIAL:
        assert row["defect"] is False
        assert "no es un bug" in row["reason"]


def test_umbral_ok_reporta_masa_de_ambos_lados(data):
    rows = {r["path"]: r for r in calibration.reachability_report(data.db_path, data)}
    row = rows["opportunities.premiumization_opportunity.min_match_score"]
    assert row["status"] == calibration.STATUS_OK
    assert 0 < row["n_pass"] < row["n"]


def test_metrica_sin_datos_devuelve_no_data(tmp_path):
    """Base vacía: nada es 'inalcanzable', todo es 'no hay señal'."""
    db = tmp_path / "empty.db"
    init_db(db, drop=True)

    rows = calibration.reachability_report(db)
    assert rows and all(r["status"] == calibration.STATUS_NO_DATA for r in rows)

    yields = calibration.rule_yield_report(db)
    assert len(yields) == 12
    assert all(r["status"] == calibration.YIELD_NO_SIGNAL for r in yields)
    assert all(r["missing_inputs"] for r in yields)


# ── 3. cotas analíticas ─────────────────────────────────────


def test_cota_analitica_de_business_importance(data):
    """El techo sale de la fórmula, no del máximo observado."""
    metric = data.metric("business_importance")
    match = data.metric("match_score")
    w = section("business_importance", "weights")
    total_w = sum(float(v) for v in w.values())
    w_rel = float(w["competitive_relevance"])
    life_max = max(float(v) for v in
                   section("business_importance", "lifecycle_multiplier").values())
    floor = float(section("business_importance", "gate_floor"))
    r_max = match.observed_max / 100.0

    expected = max(
        100.0 * (total_w - w_rel * (1.0 - r_max)) / total_w * r_max * life_max,
        100.0 * floor * life_max,
    )
    assert metric.analytic_max == pytest.approx(min(100.0, expected), rel=1e-6)
    # El techo es MUY inferior a 100 —eso es lo que hacía inalcanzable al 78
    # original— y además tiene que ser una cota VÁLIDA: nunca por debajo de lo
    # observado. No se fija un número: depende de la escala de match vigente.
    assert metric.analytic_max < 100.0
    assert metric.analytic_max >= metric.observed_max
    assert metric.observed_max <= metric.analytic_max


def test_cota_analitica_del_match_ajustado(data):
    """El shrinkage por evidencia acota el score con la cobertura máxima."""
    metric = data.metric("match_score")
    coverage = data.metric("match_coverage_all_pairs")
    prior = float(section("competitive_match", "evidence_shrinkage", "prior"))
    expected = 100.0 * coverage.observed_max + 100.0 * prior * (1.0 - coverage.observed_max)

    assert metric.analytic_max == pytest.approx(expected, rel=1e-6)
    assert metric.observed_max <= metric.analytic_max
    assert "prior" in metric.analytic_note


def test_piso_analitico_de_la_cobertura(data):
    """Los factores con datos en todos los pares le ponen piso a la cobertura."""
    coverage = data.metric("match_coverage")
    assert coverage.analytic_min is not None
    assert coverage.observed_min >= coverage.analytic_min - 1e-9
    assert 0.0 < coverage.analytic_min <= 1.0


# ── 4. distribuciones ───────────────────────────────────────


def test_score_distributions_expone_percentiles(data):
    dist = calibration.score_distributions(data.db_path, data)
    assert "business_importance" in dist and "match_score" in dist

    row = dist["match_score"]
    for p in calibration.PERCENTILES:
        assert f"p{p}" in row
    assert row["n"] > 0
    assert row["min"] <= row["p50"] <= row["max"]
    assert row["p5"] <= row["p95"]


def test_distribucion_de_pares_no_esta_censurada(data):
    """El umbral de persistencia no puede juzgarse contra su propia salida."""
    todos = data.metric("match_score_all_pairs")
    persistidos = data.metric("match_score")
    assert todos.n > persistidos.n
    assert todos.observed_min < persistidos.observed_min


# ── 5. sugerencias ──────────────────────────────────────────


def test_suggest_thresholds_tiene_la_forma_del_contrato(demo_db, temp_config):
    temp_config(**{"business_importance.severity_thresholds": {"critical": 78.0, "high": 60.0,
                                                              "medium": 40.0}})
    suggestions = calibration.suggest_thresholds(demo_db)
    assert suggestions
    for path, info in suggestions.items():
        assert "." in path
        assert set(info) >= {"actual", "sugerido", "motivo", "n_afectados"}
        assert isinstance(info["motivo"], str) and info["motivo"]
        assert isinstance(info["n_afectados"], int)


def test_suggest_thresholds_no_escribe_el_yaml(demo_db):
    """La decisión es humana: el harness NUNCA toca weights.yaml."""
    path = Path(config_module.CONFIG_PATH)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    calibration.report(demo_db)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_suggested_yaml_es_yaml_valido(demo_db, temp_config):
    # Igual que arriba: el escenario "banda vacía" se deriva del dato vigente.
    bi_max = calibration.score_distributions(demo_db)["business_importance"]["max"]
    dead_critical = bi_max + 10.0
    temp_config(**{"business_importance.severity_thresholds": {"critical": dead_critical,
                                                               "high": bi_max + 5.0,
                                                               "medium": 40.0}})
    text = calibration.suggested_yaml(demo_db)
    parsed = yaml.safe_load(text)

    assert parsed["business_importance"]["severity_thresholds"]["critical"] < dead_critical
    assert "# " in text  # cada sugerencia lleva su motivo como comentario


def test_sugerencia_de_conteo_queda_entera(demo_db, temp_config):
    """Un umbral que cuenta cosas no puede quedar en 5.5."""
    temp_config(**{"opportunities.promotional_pressure.min_competitors_on_markdown": 1})
    suggestions = calibration.suggest_thresholds(demo_db)
    info = suggestions.get("opportunities.promotional_pressure.min_competitors_on_markdown")
    assert info is not None
    assert float(info["sugerido"]) == int(info["sugerido"])


# ── 6. rendimiento por regla ────────────────────────────────


def test_rule_yield_cubre_las_12_reglas(data):
    from app.services.opportunities import OPPORTUNITY_TYPES

    rows = calibration.rule_yield_report(data.db_path, data)
    assert [r["rule"] for r in rows] == list(OPPORTUNITY_TYPES)
    assert all(r["family"] for r in rows)
    assert all(r["status"] in {calibration.YIELD_OK, calibration.YIELD_BROKEN,
                               calibration.YIELD_NO_SIGNAL, calibration.YIELD_NOTHING}
               for r in rows)
    assert all(r["diagnosis"] for r in rows)


def test_rule_yield_coincide_con_lo_persistido(data):
    rows = {r["rule"]: r for r in calibration.rule_yield_report(data.db_path, data)}
    for rule, row in rows.items():
        assert row["n"] == row["n_persisted"], rule


def test_todas_las_reglas_tienen_umbrales_y_señales_declaradas():
    from app.services.opportunities import OPPORTUNITY_TYPES

    for rule in OPPORTUNITY_TYPES:
        assert any(s.rule == rule for s in calibration.THRESHOLDS), rule
        assert calibration.RULE_INPUTS.get(rule), rule


# ── 7. sensibilidad ─────────────────────────────────────────


def _config_snapshot() -> str:
    return json.dumps(get_config(), sort_keys=True, default=str)


def test_sensitivity_barre_y_restaura_la_config(demo_db):
    before = _config_snapshot()

    rows = calibration.sensitivity("competitive_match.evidence_shrinkage.prior",
                                   [0.2, 0.35], demo_db)

    assert _config_snapshot() == before, "la config quedó modificada"
    assert len(rows) == 2
    assert all(r["param"] == "competitive_match.evidence_shrinkage.prior" for r in rows)
    assert all("n_matches" in r and "n_opportunities" in r for r in rows)
    assert any(r["is_current"] for r in rows)
    # El segundo valor se compara contra el primero.
    assert rows[1]["spearman_vs_first"] is not None
    assert 0.0 <= rows[1]["top10_overlap_vs_first"] <= 1.0
    # Un prior distinto mueve la escala.
    assert rows[0]["match_score_p50"] != rows[1]["match_score_p50"]


def test_sensitivity_restaura_la_config_aunque_falle(demo_db, monkeypatch):
    from app.services import matching

    def boom(*_args, **_kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(matching, "run_matching", boom)
    before = _config_snapshot()

    rows = calibration.sensitivity("competitive_match.evidence_shrinkage.prior", [0.9], demo_db)

    assert _config_snapshot() == before
    assert "error" in rows[0] and "fallo simulado" in rows[0]["error"]


def test_sensitivity_no_toca_la_base(demo_db):
    before = hashlib.sha256(Path(demo_db).read_bytes()).hexdigest()
    calibration.sensitivity("competitive_match.evidence_shrinkage.prior", [0.6], demo_db)
    assert hashlib.sha256(Path(demo_db).read_bytes()).hexdigest() == before


def test_temporary_param_restaura_incluso_con_excepcion():
    before = _config_snapshot()
    with pytest.raises(ValueError):
        with calibration.temporary_param("competitive_match.evidence_shrinkage.prior", 0.99):
            assert section("competitive_match", "evidence_shrinkage", "prior") == 0.99
            raise ValueError("boom")
    assert _config_snapshot() == before


# ── 8. reporte y CLI ────────────────────────────────────────


def test_report_es_serializable_a_json(demo_db):
    rep = calibration.report(demo_db)
    text = json.dumps(rep, ensure_ascii=False, default=str)
    assert len(text) > 1000
    assert set(rep) >= {"distributions", "reachability", "rule_yield", "suggestions",
                        "suggested_yaml", "summary"}
    assert rep["summary"]["thresholds_checked"] == len(calibration.THRESHOLDS)


def test_render_produce_tablas_legibles(demo_db):
    text = calibration.render(calibration.report(demo_db))
    assert "ALCANZABILIDAD DE LOS UMBRALES" in text
    assert "RENDIMIENTO DE LAS 12 REGLAS" in text
    assert "UMBRALES SUGERIDOS" in text
    assert "NO se modificó weights.yaml" in text


def test_cli_corre_y_devuelve_cero(demo_db, capsys):
    assert calibration.main(["--db", str(demo_db)]) == 0
    out = capsys.readouterr().out
    assert "HARNESS DE CALIBRACIÓN" in out

    assert calibration.main(["--db", str(demo_db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["thresholds_checked"] > 0


def test_cli_strict_falla_con_umbral_inalcanzable(demo_db, temp_config, capsys):
    match_max = calibration.score_distributions(demo_db)["match_score"]["max"]
    temp_config(**{
        "opportunities.premiumization_opportunity.min_match_score": match_max + 5.0})
    assert calibration.main(["--db", str(demo_db), "--strict"]) == 1
    capsys.readouterr()
