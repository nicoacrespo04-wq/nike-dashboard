"""Tests de app.services.embeddings.

Todo corre offline: sin red, sin descargar modelos. La DB temporal se crea con
``init_db()`` y se puebla a mano (no depende de app/seed.py).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.db import get_conn, init_db
from app.services import embeddings as emb

RUNNING_A = "Zapatilla de running con amortiguacion Zoom Air para entrenamientos diarios en asfalto"
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
