"""Tests de app.services.images.

Todo corre **sin red**: ``httpx`` se reemplaza por un doble determinístico y las
imágenes se generan localmente. Ningún test descarga nada ni necesita ``torch``.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from app.db import get_conn, init_db
from app.services import embeddings as emb
from app.services import images as im

URL_A = "https://cdn.demo-intel.local/img/pegasus-41.jpg"
URL_B = "https://cdn.demo-intel.local/img/ultraboost-5.jpg"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


# ============================================================
# Doble de httpx (sin red)
# ============================================================

class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None, text=""):
        self.status_code = status_code
        self._content = content
        self.headers = headers if headers is not None else {"content-type": "image/jpeg"}
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size: int | None = None):
        yield self._content


class FakeHttpx:
    """Cliente falso: cuenta llamadas y devuelve respuestas programadas."""

    def __init__(self, *, response=None, robots: str | None = None, error: Exception | None = None):
        self.response = response if response is not None else FakeResponse(content=PNG_BYTES)
        self.robots = robots
        self.error = error
        self.stream_calls: list[str] = []
        self.get_calls: list[str] = []

    def stream(self, method, url, **kwargs):
        self.stream_calls.append(url)
        if self.error is not None:
            raise self.error
        return self.response

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if self.robots is None:
            return FakeResponse(status_code=404, text="")
        return FakeResponse(status_code=200, text=self.robots)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Caché en tmp, sin robots cacheados, sin esperas y sin índice heredado."""
    cache = tmp_path / "image-cache"
    monkeypatch.setattr(im, "cache_dir", lambda base=None: cache)
    monkeypatch.setattr(im.time, "sleep", lambda *_a, **_k: None)
    im.reset_robots_cache()
    emb.reset_image_index()
    yield cache
    im.reset_robots_cache()
    emb.reset_image_index()


@pytest.fixture()
def fake_httpx(monkeypatch):
    """Instala un ``httpx`` falso; devuelve una factory para configurarlo."""
    def install(**kwargs) -> FakeHttpx:
        fake = FakeHttpx(**kwargs)
        monkeypatch.setitem(sys.modules, "httpx", fake)
        return fake
    return install


@pytest.fixture()
def db(tmp_path):
    """DB temporal: 4 productos, dos de ellos comparten la misma image_url."""
    path = tmp_path / "images.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1),(2,'Adidas',0)")
        conn.executemany(
            "INSERT INTO products (id, brand_id, country_code, product_name, category, image_url)"
            " VALUES (?,?,?,?,?,?)",
            [
                (1, 1, "AR", "Nike Pegasus 41", "footwear", URL_A),
                (2, 2, "AR", "Adidas Ultraboost 5", "footwear", URL_B),
                (3, 1, "AR", "Nike Pegasus 41 (repost)", "footwear", URL_A),   # misma URL
                (4, 1, "AR", "Nike Jordan 1 Low", "footwear", None),           # sin imagen
            ],
        )
        conn.executemany(
            "INSERT INTO product_attributes (product_id, attr_group, attr_name, value_text)"
            " VALUES (?,?,?,?)",
            [
                (1, "visual", "silhouette", "runner"),
                (1, "visual", "dominant_color", "black"),
                (2, "visual", "silhouette", "runner"),
                (2, "visual", "dominant_color", "black"),
                (3, "visual", "silhouette", "runner"),
                (3, "visual", "dominant_color", "black"),
                (4, "visual", "silhouette", "court"),
                (4, "visual", "dominant_color", "white"),
            ],
        )
    return path


# ============================================================
# fetch_image
# ============================================================

def test_fetch_image_descarga_y_cachea(fake_httpx, isolated):
    fake = fake_httpx()
    path = im.fetch_image(URL_A)
    assert path is not None and path.exists()
    assert path.read_bytes() == PNG_BYTES
    assert path.parent == isolated

    # Segunda llamada: sale de la caché en disco, sin tocar la red.
    again = im.fetch_image(URL_A)
    assert again == path
    assert len(fake.stream_calls) == 1


def test_fetch_image_deduplica_por_hash_de_url():
    assert im.cache_path_for(URL_A) == im.cache_path_for(URL_A)
    assert im.cache_path_for(URL_A) != im.cache_path_for(URL_B)
    assert im.cache_path_for(URL_A).suffix == ".jpg"
    assert im.cache_path_for("https://x.local/foo").suffix == ".img"


def test_fetch_image_sin_red_devuelve_none(fake_httpx):
    fake = fake_httpx(error=OSError("Network is unreachable"))
    assert im.fetch_image(URL_A) is None
    assert len(fake.stream_calls) == 3          # 1 intento + 2 reintentos acotados


def test_fetch_image_sin_httpx_no_rompe(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)   # `import httpx` -> ImportError
    assert im.fetch_image(URL_A) is None


def test_fetch_image_reintenta_solo_errores_transitorios(fake_httpx):
    fake = fake_httpx(response=FakeResponse(status_code=503))
    assert im.fetch_image(URL_A) is None
    assert len(fake.stream_calls) == 3

    permanente = fake_httpx(response=FakeResponse(status_code=404))
    assert im.fetch_image(URL_B) is None
    assert len(permanente.stream_calls) == 1     # 404 no se reintenta


def test_fetch_image_rechaza_contenido_que_no_es_imagen(fake_httpx):
    fake_httpx(response=FakeResponse(content=b"<html/>", headers={"content-type": "text/html"}))
    assert im.fetch_image(URL_A) is None


def test_fetch_image_rechaza_imagen_desproporcionada(fake_httpx, monkeypatch):
    monkeypatch.setattr(im, "_DEFAULT_MAX_BYTES", 8)
    fake_httpx(response=FakeResponse(content=b"0" * 4096))
    assert im.fetch_image(URL_A) is None


def test_fetch_image_respeta_robots(fake_httpx):
    fake = fake_httpx(robots="User-agent: *\nDisallow: /img/")
    assert im.fetch_image(URL_A) is None
    assert fake.stream_calls == []               # ni se intentó la descarga
    assert fake.get_calls and fake.get_calls[0].endswith("/robots.txt")

    # El robots.txt se cachea por host: una sola lectura para varias URLs.
    assert im.fetch_image(URL_B) is None
    assert len(fake.get_calls) == 1


def test_fetch_image_sin_robots_txt_permite(fake_httpx):
    fake = fake_httpx(robots=None)               # 404 en /robots.txt
    assert im.fetch_image(URL_A) is not None
    assert len(fake.stream_calls) == 1


def test_robots_allows_no_aplica_a_esquemas_locales():
    assert im.robots_allows("file:///tmp/x.jpg") is True
    assert im.robots_allows("synthetic://product/1") is True


def test_fetch_image_url_local(tmp_path):
    source = tmp_path / "local.png"
    source.write_bytes(PNG_BYTES)
    assert im.fetch_image(source.as_uri()) == source
    assert im.fetch_image(f"file://{tmp_path / 'no-existe.png'}") is None


def test_fetch_image_entrada_invalida():
    assert im.fetch_image("") is None
    assert im.fetch_image(None) is None          # type: ignore[arg-type]
    assert im.fetch_image("ftp://x.local/a.jpg") is None


def test_cached_image_path_nunca_descarga(fake_httpx):
    fake = fake_httpx()
    assert im.cached_image_path(URL_A) is None
    assert fake.stream_calls == []
    im.fetch_image(URL_A)
    assert im.cached_image_path(URL_A) is not None


# ============================================================
# ingest_images
# ============================================================

def test_ingest_images_deduplica_urls(db, fake_httpx):
    fake = fake_httpx()
    counts = im.ingest_images(db)
    assert counts["products_with_url"] == 3      # el producto 4 no tiene image_url
    assert counts["unique_urls"] == 2            # dos productos comparten URL
    assert len(fake.stream_calls) == 2           # ...y se descarga una sola vez
    assert counts["downloaded"] == 2
    assert counts["rows_inserted"] == 3

    with get_conn(db) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM product_images ORDER BY id")]
    assert {r["product_id"] for r in rows} == {1, 2, 3}
    assert all(r["is_primary"] == 1 for r in rows)
    assert all(r["embedding"] is None for r in rows)   # el embedding es otra etapa


def test_ingest_images_es_idempotente(db, fake_httpx):
    fake = fake_httpx()
    im.ingest_images(db)
    counts = im.ingest_images(db)
    assert counts["rows_inserted"] == 0
    assert counts["rows_existing"] == 3
    assert counts["cached"] == 2                 # ya estaban en disco
    assert len(fake.stream_calls) == 2           # no se volvió a bajar nada

    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0] == 3


def test_ingest_images_sin_red_devuelve_ceros(db, fake_httpx):
    fake_httpx(error=OSError("no route to host"))
    counts = im.ingest_images(db)
    assert counts["downloaded"] == 0
    assert counts["failed"] == 2                 # por URL única, no por producto
    assert counts["rows_inserted"] == 0
    with get_conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0] == 0


def test_ingest_images_sin_base_no_crea_nada(tmp_path):
    missing = tmp_path / "no-existe.db"
    counts = im.ingest_images(missing)
    assert set(counts.values()) == {0}
    assert not missing.exists()


def test_ingest_images_respeta_limit(db, fake_httpx):
    fake_httpx()
    counts = im.ingest_images(db, limit=1)
    assert counts["products_with_url"] == 1
    assert counts["rows_inserted"] == 1


# ============================================================
# Imágenes sintéticas + embeddings (verificación offline)
# ============================================================

def test_generate_synthetic_images(db):
    counts = im.generate_synthetic_images(db)
    assert counts["products"] == 4
    assert counts["rendered"] == 4
    assert counts["rows_inserted"] == 4

    with get_conn(db) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM product_images ORDER BY product_id")]
    assert [r["url"] for r in rows] == [f"synthetic://product/{i}" for i in (1, 2, 3, 4)]
    for row in rows:
        path = im.cached_image_path(row["url"])
        assert path is not None and path.read_bytes().startswith(b"P6")

    # Idempotente: no duplica filas para productos que ya tienen imagen.
    again = im.generate_synthetic_images(db)
    assert again["rows_inserted"] == 0
    assert again["skipped_existing"] == 4


def test_compute_image_embeddings_persiste_blobs(db):
    im.generate_synthetic_images(db)
    counts = im.compute_image_embeddings(db, model="pixel")
    assert counts["images"] == 4
    assert counts["encoded"] == 4
    assert counts["failed"] == 0
    assert counts["clip_available"] == 0

    with get_conn(db) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM product_images ORDER BY product_id")]
    for row in rows:
        assert row["embedding_model"] == emb.SYNTHETIC_MODEL
        vector = emb.unpack(row["embedding"], row["embedding_dim"])
        assert vector.shape == (row["embedding_dim"],)
        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)

    # Segunda corrida: no recalcula lo que ya está al día.
    again = im.compute_image_embeddings(db, model="pixel")
    assert again["encoded"] == 0 and again["up_to_date"] == 4
    forced = im.compute_image_embeddings(db, model="pixel", force=True)
    assert forced["encoded"] == 4


def test_compute_image_embeddings_sin_modelo_no_inventa_nada(db):
    """Sin CLIP instalado y sin pedir el encoder sintético: no se persiste nada."""
    im.generate_synthetic_images(db)
    counts = im.compute_image_embeddings(db)          # model=None -> intenta CLIP
    if counts["clip_available"]:
        pytest.skip("hay CLIP local instalado: este test cubre el camino sin torch")
    assert counts["encoded"] == 0
    assert counts["images"] == 4
    with get_conn(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM product_images WHERE embedding IS NOT NULL"
        ).fetchone()[0] == 0


def test_compute_image_embeddings_sin_archivo_no_falla(db):
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO product_images (product_id, url, is_primary) VALUES (1, ?, 1)",
            ("https://cdn.demo-intel.local/img/jamas-descargada.jpg",),
        )
    counts = im.compute_image_embeddings(db, model="pixel")
    assert counts["missing_file"] == 1
    assert counts["encoded"] == 0


def test_compute_image_embeddings_sin_base(tmp_path):
    counts = im.compute_image_embeddings(tmp_path / "no-existe.db", model="pixel")
    assert counts["encoded"] == 0 and counts["images"] == 0


def test_run_synthetic_check_end_to_end(db):
    """El camino completo (imagen -> vector -> BLOB -> score) sin red ni torch."""
    report = im.run_synthetic_check(db)
    assert report["generated"]["rows_inserted"] == 4
    assert report["embedded"]["encoded"] == 4
    assert report["index_size"] == 4
    assert report["sample"] is not None
    assert report["sample"]["method"].endswith("persisted")
    assert 0.0 <= report["sample"]["score"] <= 1.0


def test_imagenes_sinteticas_discriminan_siluetas(db):
    im.run_synthetic_check(db)
    emb.load_image_index(db)
    with get_conn(db) as conn:
        products = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM products")}
        urls = {r["product_id"]: r["url"] for r in conn.execute("SELECT product_id, url FROM product_images")}
    for pid, product in products.items():
        product["image_url"] = urls[pid]

    similar, distinto = None, None
    similar, method_sim = emb.image_similarity(products[1], products[2])   # runner vs runner
    distinto, method_dif = emb.image_similarity(products[1], products[4])  # runner vs court
    assert method_sim.endswith("persisted") and method_dif.endswith("persisted")
    assert similar > distinto


def test_pipeline_de_imagenes_no_rompe_sin_nada(tmp_path, fake_httpx):
    """El escenario real de hoy: sin red, sin torch y sin imágenes reales."""
    fake_httpx(error=OSError("offline"))
    path = tmp_path / "vacia.db"
    init_db(path, drop=True)
    assert im.ingest_images(path)["rows_inserted"] == 0
    assert im.compute_image_embeddings(path)["encoded"] == 0
    assert im.generate_synthetic_images(path)["products"] == 0


# ============================================================
# Camino CLIP con un runtime simulado (sin torch instalado)
# ============================================================

class _FakeTensor:
    def __init__(self, array):
        self._array = np.asarray(array, dtype=np.float32)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._array


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeImage:
    def __init__(self, payload: bytes):
        self.payload = payload

    def convert(self, _mode):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def fake_clip(monkeypatch):
    """torch/transformers/PIL simulados: ejercita el camino CLIP sin instalarlo."""
    import types

    class FakeModel:
        def eval(self):
            return self

        def get_image_features(self, **inputs):
            payload = inputs["pixel_values"][:32].ljust(32, b"\0")
            vector = np.frombuffer(payload, dtype=np.uint8).astype(np.float32)
            return _FakeTensor(vector[None, :])

    class FakeProcessor:
        def __call__(self, images=None, return_tensors=None):
            return {"pixel_values": images.payload}

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(no_grad=_NoGrad))
    transformers = types.SimpleNamespace(
        CLIPModel=types.SimpleNamespace(from_pretrained=lambda name, **kw: FakeModel()),
        CLIPProcessor=types.SimpleNamespace(from_pretrained=lambda name, **kw: FakeProcessor()),
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    pil = types.ModuleType("PIL")
    image_mod = types.SimpleNamespace(open=lambda path: _FakeImage(open(path, "rb").read()))
    pil.Image = image_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_mod)
    emb.reset_cache()
    yield
    emb.reset_cache()


def test_compute_image_embeddings_con_clip_simulado(db, fake_clip):
    im.generate_synthetic_images(db)
    counts = im.compute_image_embeddings(db)
    assert counts["clip_available"] == 1
    assert counts["encoded"] == 4

    with get_conn(db) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM product_images ORDER BY product_id")]
        products = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM products")}
    assert {r["embedding_model"] for r in rows} == {"openai/clip-vit-base-patch32"}
    assert {r["embedding_dim"] for r in rows} == {32}

    emb.load_image_index(db)
    for row in rows:
        products[row["product_id"]]["image_url"] = row["url"]
    score, method = emb.image_similarity(products[1], products[2])
    assert method == "clip-persisted"
    assert score is not None and 0.0 <= score <= 1.0


# ============================================================
# Cortacircuitos: sin red, la ingesta no puede tardar horas
# ============================================================

def _db_con_muchas_urls(tmp_path, n: int = 8):
    path = tmp_path / "muchas.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1)")
        conn.executemany(
            "INSERT INTO products (id, brand_id, country_code, product_name, image_url)"
            " VALUES (?,?,?,?,?)",
            [(i, 1, "AR", f"Producto {i}", f"https://cdn.demo-intel.local/img/{i}.jpg")
             for i in range(1, n + 1)],
        )
    return path


def test_ingest_corta_por_host_caido(tmp_path, fake_httpx):
    """Tras N fallos seguidos en un host, no se sigue esperando el timeout."""
    db_path = _db_con_muchas_urls(tmp_path, n=8)
    fake = fake_httpx(error=OSError("connection timed out"))
    counts = im.ingest_images(db_path)
    assert counts["failed"] == im._DEFAULT_MAX_HOST_FAILURES
    assert counts["skipped_dead_host"] == 8 - im._DEFAULT_MAX_HOST_FAILURES
    assert len(fake.stream_calls) == im._DEFAULT_MAX_HOST_FAILURES * 3
    assert counts["rows_inserted"] == 0


def test_ingest_corta_por_tiempo_maximo(tmp_path, fake_httpx, monkeypatch):
    db_path = _db_con_muchas_urls(tmp_path, n=4)
    monkeypatch.setattr(im, "_DEFAULT_MAX_SECONDS", 0.0)
    fake = fake_httpx()
    counts = im.ingest_images(db_path)
    assert counts["skipped_deadline"] == 4
    assert fake.stream_calls == []
    assert counts["rows_inserted"] == 0
