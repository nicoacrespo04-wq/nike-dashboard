"""Tests del Business Importance Score.

Se apoyan sólo en `app.db.init_db` + filas insertadas a mano: no dependen de
`app/seed.py` ni de `app/services/matching.py`.
"""

from __future__ import annotations

import pytest

from app.config import section, weights
from app.db import get_conn, init_db
from app.services import opportunities, scoring

# ── helpers ─────────────────────────────────────────────────

W = weights("business_importance", "weights")
GATE_FLOOR = float(section("business_importance", "gate_floor"))
THRESHOLDS = section("business_importance", "severity_thresholds")


def _strong_subject(**overrides):
    """Sujeto con TODOS los componentes altos (para aislar el efecto del gate)."""
    subject = {
        "competitive_relevance": 0.90,
        "franchise": "Pegasus",
        "revenue_proxy": 0.90,
        "retailer_importances": [0.9, 0.8],
        "retailers_present": 4,
        "retailers_total": 4,
        "price_gap_pct": 22.0,
        "review_volume": 0.85,
        "social_momentum": 0.80,
        "editorial_momentum": 0.70,
        "nike_shelf_share": 0.20,
        "promo_intensity_pct": 30.0,
        "lifecycle_stage": "mature",
    }
    subject.update(overrides)
    return subject


def _factor(result, name):
    return next(f for f in result.factors if f["factor"] == name)


def _empty_db(tmp_path):
    path = tmp_path / "scoring.db"
    init_db(path, drop=True)
    return path


# ── componentes y config ────────────────────────────────────


def test_usa_los_11_componentes_de_config():
    result = scoring.business_importance(_strong_subject())
    assert len(W) == 11
    assert {f["factor"] for f in result.factors} == set(W)
    for name, weight in W.items():
        assert _factor(result, name)["weight"] == pytest.approx(weight, abs=1e-4)


def test_todos_los_componentes_quedan_normalizados_0_1():
    result = scoring.business_importance(_strong_subject())
    for row in result.factors:
        assert row["available"], f"{row['factor']} debería estar disponible"
        assert 0.0 <= row["raw_score"] <= 1.0


def test_contribuciones_suman_100():
    result = scoring.business_importance(_strong_subject())
    total = sum(f["contribution"] for f in result.factors if f["available"])
    assert total == pytest.approx(100.0, abs=0.1)


# ── el gate ─────────────────────────────────────────────────


def test_gate_apaga_un_gap_sin_relevancia_competitiva():
    """Un gap de precio enorme SIN competencia real no puede ser importante."""
    con_competencia = scoring.business_importance(_strong_subject(competitive_relevance=0.95))
    sin_competencia = scoring.business_importance(_strong_subject(competitive_relevance=0.0))

    assert sin_competencia.score < con_competencia.score
    detalle = _factor(sin_competencia, "competitive_relevance")["detail"]
    # gate = clamp(relevance, gate_floor, 1.0) => piso cuando no hay competencia
    assert detalle["gate"] == pytest.approx(GATE_FLOOR)
    assert sin_competencia.score == pytest.approx(detalle["base_score"] * GATE_FLOOR, abs=0.05)
    # y con el piso el resultado deja de ser una prioridad alta
    assert scoring.severity(sin_competencia.score) in {"LOW", "MEDIUM"}
    assert scoring.severity(con_competencia.score) in {"HIGH", "CRITICAL"}


def test_gate_usa_el_piso_cuando_no_hay_relevancia_medible():
    subject = _strong_subject()
    subject.pop("competitive_relevance")
    result = scoring.business_importance(subject)
    detalle = _factor(result, "competitive_relevance")["detail"]

    assert _factor(result, "competitive_relevance")["available"] is False
    assert detalle["gate_source"] == "gate_floor"
    assert detalle["gate"] == pytest.approx(GATE_FLOOR)


def test_gate_nunca_supera_1():
    result = scoring.business_importance(_strong_subject(competitive_relevance=1.0))
    assert _factor(result, "competitive_relevance")["detail"]["gate"] == pytest.approx(1.0)


def test_match_score_alimenta_la_relevancia_competitiva():
    subject = _strong_subject()
    subject.pop("competitive_relevance")
    subject["match_score"] = 80.0
    result = scoring.business_importance(subject)
    assert _factor(result, "competitive_relevance")["raw_score"] == pytest.approx(0.80)
    assert _factor(result, "competitive_relevance")["detail"]["gate"] == pytest.approx(0.80)


# ── lifecycle y franquicia ──────────────────────────────────


def test_lifecycle_multiplier_de_config():
    lifecycle = section("business_importance", "lifecycle_multiplier")
    mature = scoring.business_importance(_strong_subject(lifecycle_stage="mature"))
    clearance = scoring.business_importance(_strong_subject(lifecycle_stage="clearance"))
    growth = scoring.business_importance(_strong_subject(lifecycle_stage="growth"))

    assert clearance.score == pytest.approx(mature.score * lifecycle["clearance"], abs=0.05)
    assert growth.score == pytest.approx(mature.score * lifecycle["growth"], abs=0.05)
    assert scoring.lifecycle_multiplier("desconocido") == 1.0


def test_franchise_importance_desde_config_con_default():
    fmap = section("business_importance", "franchise_importance")
    assert scoring.franchise_importance("Pegasus") == fmap["Pegasus"]
    assert scoring.franchise_importance("Air Force 1") == fmap["Air Force 1"]
    assert scoring.franchise_importance("Franquicia Inexistente") == fmap["default"]
    assert scoring.franchise_importance(None) == fmap["default"]

    result = scoring.business_importance(_strong_subject(franchise="Franquicia Inexistente"))
    assert _factor(result, "franchise_importance")["raw_score"] == pytest.approx(fmap["default"])


# ── degradación elegante ────────────────────────────────────


def test_factor_sin_datos_se_renormaliza_y_baja_la_confianza():
    subject = {"competitive_relevance": 0.9, "franchise": "Pegasus"}
    result = scoring.business_importance(subject)

    disponibles = [f for f in result.factors if f["available"]]
    assert {f["factor"] for f in disponibles} == {"competitive_relevance", "franchise_importance"}
    assert result.coverage == pytest.approx(
        (W["competitive_relevance"] + W["franchise_importance"]) / sum(W.values())
    )
    assert result.confidence == "LOW"
    assert sum(f["contribution"] for f in disponibles) == pytest.approx(100.0, abs=0.1)


def test_subject_vacio_no_rompe():
    result = scoring.business_importance({})
    assert result.score == 0.0
    assert result.confidence == "LOW"
    assert all(f["available"] is False for f in result.factors)


def test_valores_invalidos_se_tratan_como_sin_datos():
    result = scoring.business_importance({"revenue_proxy": "n/a", "review_volume": None})
    assert _factor(result, "revenue_proxy")["available"] is False
    assert _factor(result, "review_volume")["available"] is False


def test_normalizacion_relativa_al_corpus():
    class Ctx:
        maxima = {"review_volume": 400.0}

    result = scoring.business_importance({"review_volume_raw": 100.0}, Ctx())
    assert _factor(result, "review_volume")["raw_score"] == pytest.approx(0.25)


# ── severidad ───────────────────────────────────────────────


def test_severity_usa_los_umbrales_de_config():
    assert scoring.severity(THRESHOLDS["critical"]) == "CRITICAL"
    assert scoring.severity(THRESHOLDS["high"]) == "HIGH"
    assert scoring.severity(THRESHOLDS["medium"]) == "MEDIUM"
    assert scoring.severity(THRESHOLDS["medium"] - 0.1) == "LOW"


# ── integración con el contexto real ────────────────────────


def test_funciona_con_db_vacia(tmp_path):
    path = _empty_db(tmp_path)
    ctx = opportunities.build_context(path)
    result = scoring.business_importance(ctx.importance_inputs(None), ctx)
    assert 0.0 <= result.score <= 100.0
    assert result.confidence in {"LOW", "MEDIUM", "HIGH"}


def test_usa_maxima_del_contexto(tmp_path):
    path = _empty_db(tmp_path)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1)")
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute(
            "INSERT INTO products (id, brand_id, country_code, product_name, franchise, "
            "use_case, lifecycle_stage, msrp) VALUES (1,1,'AR','Nike Pegasus 41','Pegasus',"
            "'daily running','mature',200000)"
        )
        conn.execute("INSERT INTO reviews (product_id, rating, review_count) VALUES (1, 4.5, 120)")

    ctx = opportunities.build_context(path)
    assert ctx.maxima["review_volume"] == 120.0
    result = scoring.business_importance(ctx.importance_inputs(1), ctx)
    assert _factor(result, "review_volume")["raw_score"] == pytest.approx(1.0)
    assert _factor(result, "franchise_importance")["raw_score"] == pytest.approx(
        section("business_importance", "franchise_importance")["Pegasus"]
    )
