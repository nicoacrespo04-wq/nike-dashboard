"""Tests de app.services.embeddings.

Todo corre offline: sin red, sin descargar modelos. La DB temporal se crea con
``init_db()`` y se puebla a mano (no depende de app/seed.py).
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from app.db import get_conn, init_db
from app.services import embeddings as emb


@pytest.fixture(autouse=True)
def _isolated_image_index(tmp_path):
    """Ningún test hereda el índice de imágenes de la base real del repo."""
    emb.load_image_index(tmp_path / "sin-imagenes.db")
    yield
    emb.reset_image_index()
    emb._IMAGE_VEC_CACHE.clear()


RUNNING_A ="Zapatilla de running con amortiguacion Zoom Air para entrenamientos diarios en asfalto"
RUNNING_B = "Zapatilla para correr con espuma reactiva y amortiguacion, ideal para rodajes diarios"
FOOTBALL = "Botines de futbol con tapones FG para cesped natural y toque preciso en cancha"


# ── setup de DB temporal ────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """DB temporal con dos productos y sus atributos visuales."""
    path = tmp_path / "embeddings.db"
    init_db(path, drop=True)
    with get_conn(path) as conn:
        conn.execute("INSERT INTO countries (code, name, currency) VALUES ('AR','Argentina','ARS')")
        conn.execute("INSERT INTO brands (id, name, is_focus) VALUES (1,'Nike',1), (2,'Adidas',0)")
        conn.executemany(
            "INSERT INTO products (id, brand_id, country_code, product_name, category) VALUES (?,?,?,?,?)",
            [
                (1, 1, "AR", "Nike Air Zoom Pegasus 41", "running"),
                (2, 2, "AR", "Adidas Ultraboost 5", "running"),
            ],
        )
        conn.executemany(
            "INSERT INTO product_attributes (product_id, attr_group, attr_name, value_text, confidence, source)"
            " VALUES (?,?,?,?,?,?)",
            [
                (1, "visual", "silhouette", "runner", 0.8, "rules"),
                (1, "visual", "dominant_color", "black", 0.8, "rules"),
                (1, "visual", "secondary_colors", "white,grey", 0.7, "rules"),
                (1, "physical", "upper_material", "mesh", 0.8, "rules"),
                (1, "physical", "sole_type", "rubber", 0.7, "rules"),
                (2, "visual", "silhouette", "runner", 0.8, "rules"),
                (2, "visual", "dominant_color", "black", 0.8, "rules"),
                (2, "visual", "secondary_colors", "white,red", 0.7, "rules"),
                (2, "physical", "upper_material", "knit", 0.8, "rules"),
                (2, "physical", "sole_type", "rubber", 0.7, "rules"),
            ],
        )
    return path


def _product_with_attrs(path, product_id: int) -> dict:
    with get_conn(path) as conn:
        product = dict(conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())
        product["attributes"] = [
            dict(row) for row in conn.execute(
                "SELECT attr_name, value_text, value_num FROM product_attributes WHERE product_id = ?",
                (product_id,),
            ).fetchall()
        ]
    return product


# ── backend ─────────────────────────────────────────────────

def test_backend_name_is_local_and_stable():
    name = emb.backend_name()
    assert name in {"sentence_transformers", "tfidf"}
    assert emb.backend_name() == name


def test_backend_falls_back_to_tfidf_without_sentence_transformers():
    """Sin sentence-transformers instalado el camino por defecto es TF-IDF."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        assert emb.backend_name() == "tfidf"
    else:
        pytest.skip("sentence-transformers instalado: el backend puede ser el modelo local")


# ── vectores de texto ───────────────────────────────────────

def test_text_vectors_shape_and_l2_norm():
    matrix = emb.text_vectors([RUNNING_A, RUNNING_B, FOOTBALL])
    assert isinstance(matrix, np.ndarray)
    assert matrix.ndim == 2
    assert matrix.shape[0] == 3
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_text_vectors_empty_input():
    assert emb.text_vectors([]).shape[0] == 0


def test_text_vectors_blank_text_gives_zero_row():
    matrix = emb.text_vectors(["", "   "])
    assert matrix.shape[0] == 2
    assert np.allclose(np.linalg.norm(matrix, axis=1), 0.0)


def test_text_vectors_are_cached():
    first = emb.text_vectors([RUNNING_A, FOOTBALL])
    second = emb.text_vectors([RUNNING_A, FOOTBALL])
    assert first is second           # mismo objeto => vino de cache


# ── similitud de texto ──────────────────────────────────────

def test_text_similarity_identical_is_one():
    assert emb.text_similarity(RUNNING_A, RUNNING_A) == pytest.approx(1.0, abs=1e-6)
    # Insensible a mayúsculas/acentos/puntuación.
    assert emb.text_similarity("Pegasus 41", "  pegasus, 41!  ") == pytest.approx(1.0, abs=1e-6)


def test_text_similarity_different_is_lower():
    same = emb.text_similarity(RUNNING_A, RUNNING_A)
    similar = emb.text_similarity(RUNNING_A, RUNNING_B)
    different = emb.text_similarity(RUNNING_A, FOOTBALL)
    assert similar is not None and different is not None
    assert 0.0 <= different < similar < same


def test_text_similarity_missing_text_returns_none():
    assert emb.text_similarity(None, RUNNING_A) is None
    assert emb.text_similarity(RUNNING_A, None) is None
    assert emb.text_similarity(None, None) is None
    assert emb.text_similarity("", RUNNING_A) is None
    assert emb.text_similarity("   ", RUNNING_A) is None
    assert emb.text_similarity("!!!", RUNNING_A) is None   # sin señal textual


def test_text_similarity_is_symmetric_and_bounded():
    ab = emb.text_similarity(RUNNING_A, FOOTBALL)
    ba = emb.text_similarity(FOOTBALL, RUNNING_A)
    assert ab == pytest.approx(ba)
    assert 0.0 <= ab <= 1.0


# ── serialización de embeddings ─────────────────────────────

def test_pack_unpack_roundtrip():
    vector = np.array([0.1, -0.25, 0.5, 0.75], dtype=np.float32)
    blob = emb.pack(vector)
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * 4
    restored = emb.unpack(blob, 4)
    assert restored.shape == (4,)
    assert np.allclose(restored, vector, atol=1e-6)


def test_pack_unpack_matrix():
    matrix = np.arange(12, dtype=np.float32).reshape(3, 4)
    restored = emb.unpack(emb.pack(matrix), 4)
    assert restored.shape == (3, 4)
    assert np.allclose(restored, matrix)


def test_unpack_empty_blob():
    assert emb.unpack(None, 8).size == 0
    assert emb.unpack(b"", 8).size == 0


def test_embedding_blob_persists_in_product_images(db):
    vector = emb.text_vectors([RUNNING_A])[0]
    with get_conn(db) as conn:
        conn.execute(
            "INSERT INTO product_images (product_id, url, is_primary, embedding, embedding_model, embedding_dim)"
            " VALUES (?,?,?,?,?,?)",
            (1, "http://x/img.jpg", 1, emb.pack(vector), emb.backend_name(), int(vector.shape[0])),
        )
        row = dict(conn.execute("SELECT * FROM product_images WHERE product_id = 1").fetchone())
    restored = emb.unpack(row["embedding"], row["embedding_dim"])
    assert restored.shape == (vector.shape[0],)
    assert np.allclose(restored, vector, atol=1e-6)


# ── similitud de imagen ─────────────────────────────────────

def test_image_similarity_attribute_fallback_from_db(db):
    a = _product_with_attrs(db, 1)
    b = _product_with_attrs(db, 2)
    score, method = emb.image_similarity(a, b)
    assert method == "attribute-fallback"
    assert score is not None and 0.0 < score < 1.0


def test_image_similarity_identical_attributes_is_max(db):
    a = _product_with_attrs(db, 1)
    score, method = emb.image_similarity(a, dict(a))
    assert method == "attribute-fallback"
    assert score == pytest.approx(1.0, abs=1e-6)


def test_image_similarity_accepts_flat_attributes():
    a = {"silhouette": "runner", "dominant_color": "black", "upper_material": "mesh"}
    b = {"silhouette": "court", "dominant_color": "white", "upper_material": "leather"}
    score, method = emb.image_similarity(a, b)
    assert method == "attribute-fallback"
    assert score == pytest.approx(0.0, abs=1e-6)


def test_image_similarity_unavailable_without_visual_evidence():
    assert emb.image_similarity({}, {}) == (None, "unavailable")
    assert emb.image_similarity({"product_name": "x"}, {"silhouette": "runner"}) == (None, "unavailable")


def test_image_similarity_uses_clip_when_embeddings_present():
    vector = np.array([0.6, 0.8, 0.0], dtype=np.float32)
    a = {"image_embedding": vector, "embedding_model": "clip-vit-b32"}
    b = {"image_embedding": vector.copy(), "embedding_model": "clip-vit-b32"}
    score, method = emb.image_similarity(a, b)
    assert method == "clip"
    assert score == pytest.approx(1.0, abs=1e-4)


def test_image_similarity_ignores_fallback_placeholder_embeddings():
    vector = np.array([0.6, 0.8, 0.0], dtype=np.float32)
    a = {"image_embedding": emb.pack(vector), "embedding_dim": 3,
         "embedding_model": "attribute-fallback", "silhouette": "runner"}
    b = {"image_embedding": emb.pack(vector), "embedding_dim": 3,
         "embedding_model": "attribute-fallback", "silhouette": "runner"}
    score, method = emb.image_similarity(a, b)
    assert method == "attribute-fallback"
    assert score == pytest.approx(1.0, abs=1e-6)


def test_image_similarity_partial_evidence_still_scores():
    a = {"attributes": [{"attr_name": "dominant_color", "value_text": "black"}]}
    b = {"attributes": [{"attr_name": "dominant_color", "value_text": "black"}]}
    score, method = emb.image_similarity(a, b)
    assert method == "attribute-fallback"
    assert score == pytest.approx(1.0, abs=1e-6)


# ============================================================
# Embeddings persistidos en product_images
# ============================================================

def _clip_like(seed: int, dim: int = 16) -> np.ndarray:
    """Vector determinístico con pinta de embedding CLIP (L2-normalizado)."""
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=dim).astype(np.float32)
    return vector / float(np.linalg.norm(vector))


def _store_embedding(path, product_id: int, url: str, vector: np.ndarray,
                     model: str = "openai/clip-vit-base-patch32") -> None:
    with get_conn(path) as conn:
        conn.execute("UPDATE products SET image_url = ? WHERE id = ?", (url, product_id))
        conn.execute(
            "INSERT INTO product_images (product_id, url, is_primary, embedding, embedding_model, embedding_dim)"
            " VALUES (?,?,1,?,?,?)",
            (product_id, url, emb.pack(vector), model, int(vector.shape[0])),
        )


def test_image_similarity_usa_embeddings_persistidos(db):
    vector = _clip_like(7)
    _store_embedding(db, 1, "https://cdn.local/a.jpg", vector)
    _store_embedding(db, 2, "https://cdn.local/b.jpg", vector.copy())
    assert emb.load_image_index(db) == 2

    score, method = emb.image_similarity(_product_with_attrs(db, 1), _product_with_attrs(db, 2))
    assert method == "clip-persisted"
    assert score == pytest.approx(1.0, abs=1e-4)


def test_embeddings_persistidos_distintos_bajan_el_score(db):
    _store_embedding(db, 1, "https://cdn.local/a.jpg", _clip_like(1))
    _store_embedding(db, 2, "https://cdn.local/b.jpg", _clip_like(2))
    emb.load_image_index(db)
    score, method = emb.image_similarity(_product_with_attrs(db, 1), _product_with_attrs(db, 2))
    assert method == "clip-persisted"
    assert 0.0 <= score < 1.0


def test_modelo_no_clip_se_reporta_como_embedding_persistido(db):
    vector = _clip_like(3)
    _store_embedding(db, 1, "https://cdn.local/a.jpg", vector, model=emb.SYNTHETIC_MODEL)
    _store_embedding(db, 2, "https://cdn.local/b.jpg", vector.copy(), model=emb.SYNTHETIC_MODEL)
    emb.load_image_index(db)
    _score, method = emb.image_similarity(_product_with_attrs(db, 1), _product_with_attrs(db, 2))
    assert method == "embedding-persisted"


def test_indice_ignora_placeholders_de_fallback(db):
    vector = _clip_like(4)
    _store_embedding(db, 1, "https://cdn.local/a.jpg", vector, model="attribute-fallback")
    _store_embedding(db, 2, "https://cdn.local/b.jpg", vector.copy(), model="attribute-fallback")
    assert emb.load_image_index(db) == 0
    _score, method = emb.image_similarity(_product_with_attrs(db, 1), _product_with_attrs(db, 2))
    assert method == "attribute-fallback"


def test_persistido_exige_que_la_url_coincida(db):
    """El índice nunca adivina por id: si la URL no coincide, no hay embedding.

    Es lo que evita que un producto sin imagen (el catálogo de hoy) tome
    prestado el vector de otro registro.
    """
    vector = _clip_like(5)
    _store_embedding(db, 1, "https://cdn.local/a.jpg", vector)
    _store_embedding(db, 2, "https://cdn.local/b.jpg", vector.copy())
    emb.load_image_index(db)

    a = _product_with_attrs(db, 1)
    b = _product_with_attrs(db, 2)
    b["image_url"] = None                      # el scraper no trajo la imagen
    _score, method = emb.image_similarity(a, b)
    assert method == "attribute-fallback"

    b = _product_with_attrs(db, 2)
    b["id"] = 99                               # URL de otro producto
    _score, method = emb.image_similarity(a, b)
    assert method == "attribute-fallback"


def test_modelos_o_dimensiones_incompatibles_caen_al_fallback(db):
    _store_embedding(db, 1, "https://cdn.local/a.jpg", _clip_like(6, dim=16))
    _store_embedding(db, 2, "https://cdn.local/b.jpg", _clip_like(6, dim=32),
                     model="google/siglip-base-patch16-224")
    emb.load_image_index(db)
    _score, method = emb.image_similarity(_product_with_attrs(db, 1), _product_with_attrs(db, 2))
    assert method == "attribute-fallback"


def test_indice_tolera_base_inexistente_o_rota(tmp_path):
    assert emb.load_image_index(tmp_path / "no-existe.db") == 0
    roto = tmp_path / "roto.db"
    roto.write_bytes(b"esto no es sqlite")
    assert emb.load_image_index(roto) == 0
    assert emb.image_similarity({}, {}) == (None, "unavailable")


def test_load_image_index_no_crea_la_base(tmp_path):
    missing = tmp_path / "jamas.db"
    emb.load_image_index(missing)
    assert not missing.exists()


# ============================================================
# CLIP local: ausente, simulado y en vivo
# ============================================================

def test_sin_torch_el_backend_de_imagen_es_el_fallback():
    emb.reset_cache()
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        assert emb.clip_available() is False
        assert emb.image_backend_name() == "attribute_fallback"
        assert "dependencia opcional ausente" in emb.clip_status()
    else:
        pytest.skip("torch/transformers instalados: este test cubre el camino sin CLIP")
    finally:
        emb.reset_cache()


class _FakeTensor:
    """Imita lo justo de un tensor de torch: detach/cpu/numpy."""

    def __init__(self, array):
        self._array = np.asarray(array, dtype=np.float32)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._array


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
    """Instala un torch/transformers/PIL simulados (sin descargar nada)."""
    import types

    state = {"loads": [], "encodes": 0}

    def _features_from(payload: bytes, dim: int = 16) -> np.ndarray:
        digest = np.frombuffer(payload[:dim].ljust(dim, b"\0"), dtype=np.uint8).astype(np.float32)
        return digest / (float(np.linalg.norm(digest)) or 1.0)

    class FakeModel:
        def eval(self):
            return self

        def get_image_features(self, **inputs):
            state["encodes"] += 1
            return _FakeTensor(_features_from(inputs["pixel_values"])[None, :])

    class FakeProcessor:
        def __call__(self, images=None, return_tensors=None):
            return {"pixel_values": images.payload}

    def from_pretrained_model(name, **kwargs):
        state["loads"].append(("model", name, kwargs))
        return FakeModel()

    def from_pretrained_proc(name, **kwargs):
        state["loads"].append(("processor", name, kwargs))
        return FakeProcessor()

    torch_mod = types.SimpleNamespace(no_grad=_NoGrad)
    transformers_mod = types.SimpleNamespace(
        CLIPModel=types.SimpleNamespace(from_pretrained=from_pretrained_model),
        CLIPProcessor=types.SimpleNamespace(from_pretrained=from_pretrained_proc),
    )
    pil_mod = types.ModuleType("PIL")
    image_mod = types.SimpleNamespace(open=lambda path: _FakeImage(open(path, "rb").read()))
    pil_mod.Image = image_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)
    monkeypatch.setitem(sys.modules, "PIL", pil_mod)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_mod)
    emb.reset_cache()
    yield state
    emb.reset_cache()


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_clip_simulado_encodea_y_nunca_descarga(fake_clip, tmp_path):
    image = tmp_path / "a.ppm"
    image.write_bytes(emb.render_synthetic_image({"id": 1, "product_name": "Nike Pegasus 41",
                                                  "attributes": {"silhouette": "runner"}}))
    assert emb.clip_available() is True
    assert emb.image_backend_name() == "clip"

    vector = emb.clip_image_vector(image)
    assert vector is not None and vector.shape == (16,)
    # local_files_only=True en ambas cargas: prohibido bajar el modelo.
    assert all(kwargs.get("local_files_only") is True for _kind, _name, kwargs in fake_clip["loads"])


def test_clip_en_vivo_sobre_archivo_cacheado(fake_clip, tmp_path):
    a = tmp_path / "a.ppm"
    b = tmp_path / "b.ppm"
    a.write_bytes(emb.render_synthetic_image({"id": 1, "product_name": "Pegasus",
                                              "attributes": {"silhouette": "runner"}}))
    b.write_bytes(emb.render_synthetic_image({"id": 2, "product_name": "Jordan",
                                              "attributes": {"silhouette": "court",
                                                             "dominant_color": "white"}}))
    pa = {"id": 1, "image_path": str(a), "silhouette": "runner"}
    pb = {"id": 2, "image_path": str(b), "silhouette": "court"}

    score, method = emb.image_similarity(pa, pb)
    assert method == "clip-live"
    assert score is not None and 0.0 <= score <= 1.0

    # Misma imagen => 1.0, y el vector se cachea (no se re-encodea por par).
    encodes = fake_clip["encodes"]
    score, method = emb.image_similarity(pa, dict(pa))
    assert method == "clip-live"
    assert score == pytest.approx(1.0, abs=1e-4)
    assert fake_clip["encodes"] == encodes


def test_clip_roto_degrada_al_fallback(monkeypatch, tmp_path):
    """El modelo no está cacheado localmente: se degrada, no se rompe."""
    import types

    def boom(name, **kwargs):
        raise OSError(f"{name} no está en la caché local")

    pil_mod = types.ModuleType("PIL")
    image_mod = types.SimpleNamespace(open=lambda path: _FakeImage(b""))
    pil_mod.Image = image_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(no_grad=_NoGrad))
    monkeypatch.setitem(sys.modules, "PIL", pil_mod)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_mod)
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        CLIPModel=types.SimpleNamespace(from_pretrained=boom),
        CLIPProcessor=types.SimpleNamespace(from_pretrained=boom),
    ))
    emb.reset_cache()
    try:
        assert emb.clip_available() is False
        assert "no disponible localmente" in emb.clip_status()
        image = tmp_path / "a.ppm"
        image.write_bytes(emb.render_synthetic_image({"id": 1}))
        assert emb.clip_image_vector(image) is None
        score, method = emb.image_similarity(
            {"id": 1, "image_path": str(image), "silhouette": "runner", "dominant_color": "black"},
            {"id": 2, "image_path": str(image), "silhouette": "runner", "dominant_color": "black"},
        )
        assert method == "attribute-fallback"
        assert score == pytest.approx(1.0, abs=1e-6)
    finally:
        emb.reset_cache()


def test_backend_de_imagen_se_puede_apagar_por_config(monkeypatch, fake_clip, tmp_path):
    monkeypatch.setattr(emb, "_image_backend_config", lambda: "attribute_fallback")
    emb.reset_cache()
    assert emb.clip_available() is False
    assert "deshabilitado" in emb.clip_status()


# ============================================================
# Encoder determinístico por píxeles (verificación offline)
# ============================================================

def test_render_synthetic_image_es_determinista():
    product = {"id": 1, "product_name": "Nike Pegasus 41",
               "attributes": {"silhouette": "runner", "dominant_color": "black"}}
    first = emb.render_synthetic_image(product)
    assert first.startswith(b"P6\n96 96\n255\n")
    assert first == emb.render_synthetic_image(dict(product))
    otro = emb.render_synthetic_image({"id": 2, "product_name": "Nike Jordan 1",
                                       "attributes": {"silhouette": "court",
                                                      "dominant_color": "white"}})
    assert otro != first


def test_synthetic_image_vector_es_determinista_y_normalizado(tmp_path):
    path = tmp_path / "a.ppm"
    path.write_bytes(emb.render_synthetic_image({"id": 1, "product_name": "Pegasus",
                                                 "attributes": {"silhouette": "runner"}}))
    vector = emb.synthetic_image_vector(path)
    assert vector is not None
    assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)
    assert np.allclose(vector, emb.synthetic_image_vector(path))


def test_synthetic_image_vector_con_archivo_invalido(tmp_path):
    basura = tmp_path / "basura.ppm"
    basura.write_bytes(b"no soy una imagen")
    assert emb.synthetic_image_vector(basura) is None
    assert emb.synthetic_image_vector(tmp_path / "no-existe.ppm") is None


def test_synthetic_vectors_discriminan(tmp_path):
    def vector(pid, silhouette, color):
        path = tmp_path / f"{pid}.ppm"
        path.write_bytes(emb.render_synthetic_image(
            {"id": pid, "product_name": f"p{pid}",
             "attributes": {"silhouette": silhouette, "dominant_color": color}}))
        return emb.synthetic_image_vector(path)

    runner_a = vector(1, "runner", "black")
    runner_b = vector(2, "runner", "black")
    court = vector(3, "court", "white")
    assert float(np.dot(runner_a, runner_b)) > float(np.dot(runner_a, court))
