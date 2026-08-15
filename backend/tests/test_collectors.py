"""Tests de la capa de adquisición (``app.collectors``).

Se asume **sin red**: todo se ejercita con fixtures locales. Lo que se verifica:

  1. Extracción de cada ``mention_type`` (versus, alternative, same_list,
     ranking, review) sobre patrones reales del dominio.
  2. Resolución correcta de texto libre a ``product_id`` y **rechazo** por baja
     confianza, ambigüedad, conflicto de versión y conflicto de marca.
  3. Agregación social sin individuos: nunca hay autores, la evidencia va
     limpia y el guard de persistencia rompe si alguna vez se cuela un campo
     de identidad.
  4. Idempotencia: correr dos veces no duplica.
  5. Degradación sin red / sin credenciales / con colector roto.
  6. Cada fuente registrada declara su política (licencia/ToS + rate limit) y
     ninguna fuente prohibida sale a internet.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.collectors import base as cbase
from app.collectors import social as csocial
from app.collectors.base import (
    ACCESS_PROHIBITED,
    BaseCollector,
    DisabledCollector,
    SourcePolicy,
    persist,
    register,
    registered,
    run_collectors,
)
from app.collectors.editorial import EditorialFixtureCollector, FeedEditorialCollector
from app.collectors.extract import Document, extract_candidates, extract_mentions, list_key_for
from app.collectors.fetch import PoliteFetcher, parse_feed
from app.collectors.resolve import CatalogResolver, reset_default_resolver
from app.collectors.sentiment import score_text
from app.collectors.social import (
    SocialAggregator,
    SocialFixtureCollector,
    SocialPost,
    normalize_post,
    posts_from_reddit_listing,
    scrub,
)
from app.db import query
from app.seed import seed

# ids del dataset demo (backend/data/sample/products.csv)
PEGASUS_41 = 1
VOMERO_18 = 2
INVINCIBLE_3 = 4
NOVABLAST_5 = 16
GEL_NIMBUS_26 = 17
SUPERNOVA_RISE_2 = 22
ULTRABOOST_5 = 23
NB_1080V13 = 31
NIKE_BRAND = 1


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """Base sembrada con el dataset demo (catálogo real para resolver)."""
    path = tmp_path_factory.mktemp("collectors") / "intelligence.db"
    seed(path, drop=True)
    return path


@pytest.fixture(scope="module")
def resolver(db):
    return CatalogResolver.from_db(db)


@pytest.fixture
def clean_db(tmp_path):
    """Base propia por test, sin menciones ni agregados sociales."""
    path = tmp_path / "run.db"
    seed(path, drop=True)
    from app.db import get_conn
    with get_conn(path) as conn:
        conn.execute("DELETE FROM editorial_mentions")
        conn.execute("DELETE FROM social_mention_aggregates")
    reset_default_resolver()
    yield path
    reset_default_resolver()


@pytest.fixture
def registry_sandbox():
    """Aísla el registro global: cada test registra lo suyo y se restaura."""
    saved = registered()
    cbase.clear_registry()
    yield
    cbase.clear_registry()
    for collector in saved.values():
        register(collector)


def doc(**kwargs) -> Document:
    base = {"title": "", "text": "", "url": "https://medio.example/nota/",
            "source_name": "Medio", "published_at": "2026-06-01", "country_code": "AR"}
    base.update(kwargs)
    return Document(**base)


def types_of(candidates) -> set[str]:
    return {c.mention_type for c in candidates}


# ── 1. RESOLUCIÓN ───────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("Pegasus 41", PEGASUS_41),
    ("Nike Pegasus 41", PEGASUS_41),
    ("la Vomero 18", VOMERO_18),
    ("the Nova Blast 5", NOVABLAST_5),          # separación distinta
    ("Gel-Nimbus 26", GEL_NIMBUS_26),           # guion
    ("el New Balance 1080v13", NB_1080V13),     # versión pegada
    ("Adidas Supernova Rise 2", SUPERNOVA_RISE_2),
])
def test_resuelve_texto_libre_al_producto_correcto(resolver, text, expected):
    resolution = resolver.resolve(text)
    assert resolution is not None, f"no resolvió {text!r}"
    assert resolution.product_id == expected
    assert 0.0 < resolution.score <= 1.0


@pytest.mark.parametrize("text", [
    "zapatillas de running",          # genérico: no es un producto
    "las mejores zapatillas de 2026",
    "unas zapatillas cómodas",
])
def test_rechaza_texto_generico(resolver, text):
    assert resolver.resolve(text) is None


def test_rechaza_version_equivocada_en_vez_de_atribuir_mal(resolver):
    """Pegasus 40 NO es Pegasus 41: preferimos perder la mención."""
    assert resolver.resolve("Pegasus 40") is None
    assert resolver.resolve("Novablast 4") is None


def test_rechaza_mencion_ambigua(resolver):
    """"Pegasus" a secas puede ser la 41 o la Trail 5: no se elige ninguna."""
    assert resolver.resolve("Pegasus") is None
    ranked = resolver.score_all("Pegasus")
    assert ranked[0][1] - ranked[1][1] < resolver.ambiguity_margin


def test_rechaza_conflicto_de_marca(resolver):
    """"Adidas Pegasus 41" no puede resolverse a un producto Nike."""
    resolution = resolver.resolve("Adidas Pegasus 41")
    assert resolution is None or resolver.brand_of(resolution.product_id) != NIKE_BRAND


def test_umbral_configurable_es_el_que_decide(db):
    """Bajar el umbral acepta lo que el umbral por defecto descarta."""
    estricto = CatalogResolver.from_db(db, accept_threshold=0.95, ambiguity_margin=0.05)
    permisivo = CatalogResolver.from_db(db, accept_threshold=0.50, ambiguity_margin=0.0)
    assert estricto.resolve("the Nova Blast 5") is None
    assert permisivo.resolve("the Nova Blast 5").product_id == NOVABLAST_5


def test_find_products_no_cruza_separadores(resolver):
    """"X vs Y" son dos productos: el número de uno no contamina al otro."""
    hits = resolver.find_products("Nike Pegasus 41 vs ASICS Novablast 5")
    assert [h.product_id for h in hits] == [PEGASUS_41, NOVABLAST_5]
    assert {h.text for h in hits} == {"Nike Pegasus 41", "ASICS Novablast 5"}


def test_estadisticas_de_rechazo(db):
    r = CatalogResolver.from_db(db)
    r.reset_stats()
    r.resolve("Pegasus 41")
    r.resolve("Pegasus 40")
    r.resolve("Pegasus")
    assert r.stats.get("accepted") == 1
    assert sum(v for k, v in r.stats.items() if k.startswith("rejected")) == 2


# ── 2. EXTRACCIÓN DE PATRONES ───────────────────────────────


@pytest.mark.parametrize("title", [
    "Nike Pegasus 41 vs ASICS Novablast 5: cuál es el mejor daily trainer",
    "Nike Pegasus 41 versus ASICS Novablast 5",
    "Comparamos la Nike Pegasus 41 contra la ASICS Novablast 5",
])
def test_extrae_versus(resolver, title):
    candidates = extract_candidates(doc(title=title), resolver)
    versus = [c for c in candidates if c.mention_type == "versus"]
    assert versus, f"no detectó versus en {title!r}"
    assert versus[0].pair == frozenset({PEGASUS_41, NOVABLAST_5})


def test_extrae_versus_en_el_cuerpo(resolver):
    candidates = extract_candidates(doc(
        title="Novedades de running",
        text="Esta semana probamos varias. Nike Vomero 18 vs ASICS Gel-Nimbus 26: "
             "la pelea por el máximo confort."), resolver)
    pares = {c.pair for c in candidates if c.mention_type == "versus"}
    assert frozenset({VOMERO_18, GEL_NIMBUS_26}) in pares


@pytest.mark.parametrize("title,text", [
    ("Cinco alternativas a la Nike Vomero 18 en máxima amortiguación",
     "El New Balance 1080v13 ofrece un rocker más suave."),
    ("Best alternatives to the Nike Vomero 18",
     "The New Balance 1080v13 is the smoother option."),
    ("Zapatillas similares a la Nike Vomero 18",
     "El New Balance 1080v13 es la opción más estable."),
])
def test_extrae_alternative(resolver, title, text):
    candidates = extract_candidates(doc(title=title, text=text), resolver)
    alternativas = [c for c in candidates if c.mention_type == "alternative"]
    assert alternativas
    assert any(c.pair == frozenset({VOMERO_18, NB_1080V13}) for c in alternativas)


@pytest.mark.parametrize("title", [
    "Las mejores zapatillas de entrenamiento diario de 2026",
    "Best daily trainers of 2026",
    "Best running shoes for 2026",
    "Guía de compra: qué zapatillas comprar en 2026",
])
def test_extrae_same_list_de_guias_y_best_of(resolver, title):
    candidates = extract_candidates(doc(
        title=title,
        text="Probamos veinte modelos durante seis meses.",
        list_items=("Nike Pegasus 41 — la más previsible.",
                    "ASICS Novablast 5 — la más divertida.",
                    "Adidas Supernova Rise 2 — la mejor por debajo de 140 dólares.")),
        resolver)
    same_list = [c for c in candidates if c.mention_type == "same_list"]
    assert same_list, f"no detectó lista en {title!r}"
    assert all(c.list_key for c in same_list)
    assert len({c.list_key for c in same_list}) == 1
    pares = {c.pair for c in same_list}
    assert frozenset({PEGASUS_41, NOVABLAST_5}) in pares
    assert frozenset({PEGASUS_41, SUPERNOVA_RISE_2}) in pares


def test_extrae_best_basketball_shoes(resolver):
    candidates = extract_candidates(doc(
        title="Best basketball shoes 2026",
        list_items=("Nike Jordan 1 Low — la más versátil.",
                    "Under Armour Curry 12 — la mejor para bases rápidos.")), resolver)
    assert "same_list" in types_of(candidates)


def test_extrae_ranking_con_posiciones(resolver):
    candidates = extract_candidates(doc(
        title="Ranking: las zapatillas de máxima amortiguación de 2026",
        text="El top 5 después de 2.000 km de prueba.",
        list_items=("1. ASICS Gel-Nimbus 26 — la más blanda.",
                    "2. Nike Vomero 18 — sube al segundo puesto.",
                    "3. New Balance 1080v13 — el rocker más suave.")), resolver)
    rankings = [c for c in candidates if c.mention_type == "ranking"]
    assert len(rankings) == 3
    assert all(c.product_b_id is None for c in rankings)          # ranking = un producto
    assert len({c.list_key for c in rankings}) == 1               # misma lista
    posiciones = {c.detail["position"]: c.product_a_id for c in rankings}
    assert posiciones[1] == GEL_NIMBUS_26 and posiciones[2] == VOMERO_18


def test_extrae_review_de_un_solo_producto(resolver):
    candidates = extract_candidates(doc(
        title="Review: Nike Vomero 18, la más cómoda del line-up",
        text="Se siente más dinámica gracias al ZoomX."), resolver)
    reviews = [c for c in candidates if c.mention_type == "review"]
    assert len(reviews) == 1
    assert reviews[0].product_a_id == VOMERO_18 and reviews[0].product_b_id is None


def test_una_comparacion_no_se_confunde_con_review(resolver):
    candidates = extract_candidates(doc(
        title="Review: Nike Pegasus 41 vs ASICS Novablast 5"), resolver)
    assert "review" not in types_of(candidates)
    assert "versus" in types_of(candidates)


def test_el_par_conserva_el_vinculo_mas_fuerte(resolver):
    """Si el par aparece como versus y como lista, gana versus."""
    candidates = extract_candidates(doc(
        title="Best daily trainers 2026: Nike Pegasus 41 vs ASICS Novablast 5",
        list_items=("Nike Pegasus 41", "ASICS Novablast 5")), resolver)
    pares = [c for c in candidates if c.pair == frozenset({PEGASUS_41, NOVABLAST_5})]
    assert len(pares) == 1 and pares[0].mention_type == "versus"


def test_no_inventa_menciones_sin_productos(resolver):
    candidates = extract_candidates(doc(
        title="Las mejores zapatillas de trekking del mercado",
        text="Analizamos modelos que no están en el catálogo."), resolver)
    assert candidates == []


def test_filas_editoriales_tienen_el_esquema_esperado(resolver):
    rows = extract_mentions(doc(
        title="Nike Pegasus 41 vs ASICS Novablast 5",
        text="La Pegasus dura más; la Novablast rebota más."), resolver)
    assert rows
    row = rows[0]
    assert set(row) == {"source_name", "url", "title", "published_at", "mention_type",
                        "product_a_id", "product_b_id", "list_key", "excerpt", "country_code"}
    assert row["mention_type"] in {"versus", "alternative", "same_list", "ranking", "review"}
    assert row["country_code"] == "AR" and row["published_at"] == "2026-06-01"


def test_list_key_es_estable(resolver):
    documento = doc(title="Best daily trainers 2026",
                    url="https://medio.example/best-daily-trainers-2026/")
    assert list_key_for(documento) == "best-daily-trainers-2026"
    assert list_key_for(documento) == list_key_for(documento)


# ── 3. COLECTOR EDITORIAL CON FIXTURES ──────────────────────


def test_colector_editorial_lee_fixtures_locales(clean_db):
    collector = EditorialFixtureCollector(db_path=clean_db)
    rows = collector.collect()
    assert rows, "los fixtures del repo deberían producir menciones"
    tipos = {r["mention_type"] for r in rows}
    assert {"versus", "alternative", "same_list", "ranking", "review"} <= tipos
    assert all(r["product_a_id"] for r in rows)
    assert collector.stats["files_read"] >= 4


def test_colector_editorial_parsea_rss_escrito_a_mano(tmp_path, clean_db):
    feed = tmp_path / "medio.xml"
    feed.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Medio</title>
  <item>
    <title>Nike Pegasus 41 vs ASICS Novablast 5: el duelo del daily trainer</title>
    <link>https://medio.example/pegasus-vs-novablast/</link>
    <pubDate>Tue, 12 May 2026 10:30:00 -0300</pubDate>
    <description>La Pegasus 41 gana en durabilidad; la Novablast 5 devuelve más energía.</description>
  </item>
</channel></rss>""", encoding="utf-8")
    collector = EditorialFixtureCollector(name="rss_test", directory=tmp_path, db_path=clean_db)
    rows = collector.collect()
    assert len(rows) == 1
    assert rows[0]["mention_type"] == "versus"
    assert {rows[0]["product_a_id"], rows[0]["product_b_id"]} == {PEGASUS_41, NOVABLAST_5}
    assert rows[0]["published_at"] == "2026-05-12"
    assert rows[0]["url"] == "https://medio.example/pegasus-vs-novablast/"


def test_fixture_ilegible_no_rompe_la_corrida(tmp_path, clean_db):
    (tmp_path / "roto.json").write_text("{ esto no es json", encoding="utf-8")
    (tmp_path / "ok.json").write_text(json.dumps({"articles": [{
        "title": "Nike Pegasus 41 vs ASICS Novablast 5",
        "url": "https://medio.example/x/", "published_at": "2026-05-12"}]}), encoding="utf-8")
    collector = EditorialFixtureCollector(name="mixto", directory=tmp_path, db_path=clean_db)
    rows = collector.collect()
    assert len(rows) == 1
    assert collector.stats["files_failed"] == 1


# ── 4. SOCIAL: SIEMPRE AGREGADO, NUNCA INDIVIDUOS ───────────


def test_normalize_post_descarta_la_identidad():
    post = normalize_post({
        "text": "Las Pegasus 41 son comodísimas",
        "created_at": "2026-08-01",
        "author": "juan_perez", "user_id": 12345, "profile_url": "https://foro/u/juan",
    })
    assert isinstance(post, SocialPost)
    assert set(post.__dataclass_fields__) == {"text", "observed_at", "source_type", "country_code"}
    assert "juan" not in json.dumps(post.__dict__, default=str)


def test_scrub_limpia_identificadores():
    limpio = scrub("Escribime a @sneakerhead_ba o a hola@ejemplo.com.ar, o entrá a "
                   "https://tienda.example/x — tengo Samba OG")
    assert "@" not in limpio and "http" not in limpio
    assert "Samba OG" in limpio


def test_agrega_por_periodo_sin_individuos(resolver):
    posts = [
        SocialPost("Estoy entre las Pegasus 41 y las Novablast 5, cuál compro?",
                   date(2026, 8, 2), "forum", "AR"),
        SocialPost("Tuve las dos: la Novablast 5 rebota más, la Pegasus 41 dura más",
                   date(2026, 8, 5), "forum", "AR"),
        SocialPost("Las Pegasus 41 son comodísimas para fondo largo",
                   date(2026, 8, 10), "forum", "AR"),
    ]
    rows = SocialAggregator(resolver, focus_brand_id=NIKE_BRAND).aggregate(posts)
    assert rows
    for row in rows:
        assert not (set(row) & cbase.FORBIDDEN_FIELDS)
        assert row["period_start"] <= row["period_end"]
        assert row["mention_count"] >= 1
        evidencia = json.loads(row["sample_evidence"])
        assert isinstance(evidencia, list) and len(evidencia) <= 3
        assert all(isinstance(e, str) and "@" not in e for e in evidencia)

    pares = [r for r in rows if r["co_product_id"] is not None]
    assert pares, "las co-menciones del par deberían generar filas"
    par = pares[0]
    assert (par["product_id"], par["co_product_id"]) == (PEGASUS_41, NOVABLAST_5)  # Nike primero
    assert par["comention_count"] == 2
    assert par["mention_count"] == par["comention_count"]


def test_sentimiento_agregado_es_deterministico_y_rioplatense(resolver):
    negativos = [SocialPost("Las Nike están carísimas, un fangote y encima no vale lo que cuesta",
                            date(2026, 8, 1), "forum", "AR")]
    positivos = [SocialPost("Las Pegasus 41 son comodísimas y valen cada peso",
                            date(2026, 8, 1), "forum", "AR")]
    fila_neg = SocialAggregator(resolver).aggregate(negativos)
    fila_pos = SocialAggregator(resolver).aggregate(positivos)
    assert fila_pos and fila_pos[0]["sentiment_score"] > 0
    if fila_neg:
        assert fila_neg[0]["sentiment_score"] < 0
    assert score_text("son comodísimas") > 0
    assert score_text("no son cómodas") < 0          # negación
    assert score_text("hoy corrí 10k") is None       # sin señal léxica


def test_topico_e_intent_salen_de_la_taxonomia_configurada(resolver):
    from app.config import section
    taxonomy = section("brand_intelligence", "taxonomy", default={}) or {}
    posts = [SocialPost("Me quiero comprar unas Air Force 1, son comodísimas",
                        date(2026, 8, 1), "social", "AR")]
    rows = SocialAggregator(resolver).aggregate(posts)
    assert rows
    for row in rows:
        if row["topic"]:
            assert any(row["topic"] in topics for topics in taxonomy.values())
        if row["intent"]:
            assert row["intent"] in (taxonomy.get("consumer_intent") or [])


def test_reddit_listing_no_conserva_autores():
    payload = {"data": {"children": [
        {"data": {"title": "Pegasus 41 vs Novablast 5", "selftext": "cuál compro?",
                  "created_utc": 1786000000, "author": "u/alguien", "id": "abc123",
                  "permalink": "/r/x/comments/abc123/"}},
    ]}}
    posts = posts_from_reddit_listing(payload)
    assert len(posts) == 1
    serializado = json.dumps([p.__dict__ for p in posts], default=str)
    assert "alguien" not in serializado and "abc123" not in serializado


def test_persistencia_rechaza_campos_de_identidad(clean_db):
    fila = {"period_start": "2026-07-01", "period_end": "2026-07-30", "country_code": "AR",
            "source_type": "forum", "mention_count": 3, "author": "juan"}
    with pytest.raises(ValueError, match="identifican individuos"):
        persist([fila], "social_mention_aggregates", clean_db)


def test_colector_social_de_fixtures(clean_db):
    collector = SocialFixtureCollector(db_path=clean_db)
    rows = collector.collect()
    assert rows
    assert all(r["mention_count"] >= 1 for r in rows)
    assert any(r["co_product_id"] for r in rows), "debería haber co-menciones"
    assert all(r["country_code"] == "AR" for r in rows)
    assert collector.stats["posts"] >= 10


# ── 5. PERSISTENCIA E IDEMPOTENCIA ──────────────────────────


def test_run_collectors_es_idempotente(clean_db, registry_sandbox):
    register(EditorialFixtureCollector(db_path=clean_db))
    register(SocialFixtureCollector(db_path=clean_db))

    primera = run_collectors(clean_db)
    assert sum(primera.values()) > 0
    segunda = run_collectors(clean_db)
    assert segunda == {name: 0 for name in primera}

    total_editorial = query("SELECT COUNT(*) c FROM editorial_mentions", path=clean_db)[0]["c"]
    assert total_editorial == primera["editorial_fixture"]


def test_dry_run_no_escribe(clean_db, registry_sandbox):
    register(EditorialFixtureCollector(db_path=clean_db))
    simulacro = run_collectors(clean_db, dry_run=True)
    assert simulacro["editorial_fixture"] > 0
    assert query("SELECT COUNT(*) c FROM editorial_mentions", path=clean_db)[0]["c"] == 0


def test_since_descarta_lo_viejo(clean_db, registry_sandbox):
    register(EditorialFixtureCollector(db_path=clean_db))
    reciente = run_collectors(clean_db, since=date(2026, 7, 1))
    fechas = [r["published_at"] for r in
              query("SELECT published_at FROM editorial_mentions", path=clean_db)]
    assert reciente["editorial_fixture"] > 0
    assert all(f is None or f >= "2026-07-01" for f in fechas)


def test_deduplica_dentro_del_mismo_lote(clean_db):
    fila = {"source_name": "Medio", "url": "https://medio.example/x/", "title": "X vs Y",
            "published_at": "2026-06-01", "mention_type": "versus",
            "product_a_id": PEGASUS_41, "product_b_id": NOVABLAST_5,
            "list_key": None, "excerpt": "...", "country_code": "AR"}
    assert persist([fila, dict(fila)], "editorial_mentions", clean_db) == 1
    assert persist([fila], "editorial_mentions", clean_db) == 0


def test_persist_rechaza_columnas_inexistentes(clean_db):
    with pytest.raises(ValueError, match="columnas inexistentes"):
        persist([{"mention_type": "versus", "inventada": 1}], "editorial_mentions", clean_db)


def test_las_filas_adquiridas_alimentan_el_factor_editorial(clean_db, registry_sandbox):
    """Integración: lo adquirido lo consume el motor competitivo tal cual."""
    register(EditorialFixtureCollector(db_path=clean_db))
    register(SocialFixtureCollector(db_path=clean_db))
    run_collectors(clean_db)

    from app.services.matching import _score_editorial, _score_social, build_context
    ctx = build_context(clean_db)
    nike, comp = ctx.products[PEGASUS_41], ctx.products[NOVABLAST_5]
    editorial_score, editorial_detail = _score_editorial(nike, comp, ctx)
    social_score, social_detail = _score_social(nike, comp, ctx)
    assert editorial_score is not None and 0 < editorial_score <= 1
    assert editorial_detail["n_mentions"] >= 1
    assert social_score is None or 0 <= social_score <= 1
    assert social_detail["aggregate_only"] is True


# ── 6. DEGRADACIÓN: SIN RED, SIN CREDENCIALES, CON FALLAS ───


class _RaisingCollector(BaseCollector):
    def collect(self, since=None):
        raise RuntimeError("la fuente explotó")


def test_un_colector_roto_no_frena_al_resto(clean_db, registry_sandbox):
    register(EditorialFixtureCollector(db_path=clean_db))
    register(_RaisingCollector(
        name="fuente_rota", table="editorial_mentions",
        policy=SourcePolicy(source_name="Rota", homepage="https://rota.example",
                            access="feed", terms="test")))
    report = run_collectors(clean_db)
    assert report["fuente_rota"] == 0
    assert report["editorial_fixture"] > 0


def test_sin_red_el_colector_de_feed_devuelve_vacio(monkeypatch, clean_db):
    import httpx

    def sin_red(*args, **kwargs):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx, "get", sin_red)
    collector = FeedEditorialCollector(
        name="feed_test",
        policy=SourcePolicy(source_name="Medio", homepage="https://medio.example",
                            access="feed", terms="RSS público", rate_limit_seconds=0.0),
        feed_urls=("https://medio.example/feed/",),
        db_path=clean_db)
    assert collector.collect() == []
    assert collector.stats["feeds_unreachable"] == 1


def test_sin_robots_txt_no_se_pide_nada(monkeypatch):
    import httpx
    pedidos: list[str] = []

    def registrar(url, *args, **kwargs):
        pedidos.append(url)
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx, "get", registrar)
    fetcher = PoliteFetcher(SourcePolicy(source_name="Medio", homepage="https://medio.example",
                                         access="feed", terms="RSS", rate_limit_seconds=0.0))
    assert fetcher.get("https://medio.example/articulo/") is None
    assert pedidos == ["https://medio.example/robots.txt"]   # nunca se pidió el artículo


def test_robots_txt_prohibido_bloquea_la_url(monkeypatch):
    import httpx

    class _Respuesta:
        status_code = 200
        text = "User-agent: *\nDisallow: /articulo/\n"

    pedidos: list[str] = []

    def fake_get(url, *args, **kwargs):
        pedidos.append(url)
        return _Respuesta()

    monkeypatch.setattr(httpx, "get", fake_get)
    fetcher = PoliteFetcher(SourcePolicy(source_name="Medio", homepage="https://medio.example",
                                         access="feed", terms="RSS", rate_limit_seconds=0.0))
    assert fetcher.get("https://medio.example/articulo/1") is None
    assert fetcher.get("https://medio.example/feed/") is not None
    assert pedidos.count("https://medio.example/robots.txt") == 1   # robots cacheado


def test_fuente_prohibida_nunca_sale_a_la_red(monkeypatch):
    import httpx

    def prohibido(*args, **kwargs):
        raise AssertionError("una fuente prohibida no puede pedir nada")

    monkeypatch.setattr(httpx, "get", prohibido)
    policy = SourcePolicy(source_name="Red cerrada", homepage="https://cerrada.example",
                          access=ACCESS_PROHIBITED, terms="ToS prohíben scraping",
                          official_api="API oficial con acuerdo comercial")
    collector = DisabledCollector(name="blocked_test", table="social_mention_aggregates",
                                  policy=policy)
    assert collector.collect() == []
    assert PoliteFetcher(policy).get("https://cerrada.example/x") is None


def test_colector_sin_credenciales_devuelve_vacio(monkeypatch, clean_db):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    collector = csocial.PublicApiSocialCollector(
        name="reddit_test", policy=csocial.REDDIT_POLICY,
        endpoints=("https://oauth.reddit.com/r/x/search",), db_path=clean_db)
    assert collector.collect() == []
    assert collector.stats.get("skipped_no_credentials") == 1


def test_run_collectors_sin_colectores_no_falla(clean_db, registry_sandbox):
    assert run_collectors(clean_db) == {}


def test_feed_roto_no_lanza():
    assert parse_feed("<rss><channel><item>") == []
    assert parse_feed("") == []


# ── 7. POLÍTICA DE FUENTES (legalidad declarada) ────────────


def test_toda_fuente_registrada_declara_su_politica():
    for name, collector in registered().items():
        policy = collector.policy
        assert isinstance(policy, SourcePolicy), name
        assert policy.source_name and policy.terms, name
        assert policy.access in {"api", "feed", "fixture", "review_required", "prohibited"}, name
        assert policy.rate_limit_seconds >= 0, name
        if policy.access == "prohibited":
            assert policy.official_api, f"{name}: falta documentar la API oficial necesaria"


def test_las_fuentes_de_red_arrancan_apagadas():
    """Ninguna fuente remota se enciende sola: primero hay que verificar ToS."""
    for name, collector in registered().items():
        if collector.policy.needs_network:
            assert not collector.enabled, f"{name} no debería estar habilitada por defecto"


def test_hay_fuentes_prohibidas_documentadas():
    prohibidas = [c for c in registered().values() if c.policy.access == "prohibited"]
    assert len(prohibidas) >= 4
    assert all(c.collect() == [] for c in prohibidas)


def test_registro_valida_el_contrato(registry_sandbox):
    with pytest.raises(ValueError, match="SourcePolicy"):
        register(BaseCollector(name="x", table="editorial_mentions", policy=None))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="tabla no permitida"):
        register(BaseCollector(name="x", table="products",
                               policy=SourcePolicy(source_name="s", homepage="h",
                                                   access="fixture", terms="t")))


def test_catalogo_de_fuentes_para_auditoria():
    fichas = {row["name"]: row for row in cbase.catalog()}
    assert "editorial_fixture" in fichas and "social_fixture" in fichas
    for ficha in fichas.values():
        assert ficha["terms"] and ficha["stores"]


def test_cli_lista_fuentes_y_corre_en_seco(capsys, clean_db):
    from app.collectors.__main__ import main
    assert main(["--list"]) == 0
    assert "Fuentes registradas" in capsys.readouterr().out
    assert main(["--db", str(clean_db), "--dry-run"]) == 0
    salida = capsys.readouterr().out
    assert "simulacro" in salida
    assert query("SELECT COUNT(*) c FROM editorial_mentions", path=clean_db)[0]["c"] == 0
