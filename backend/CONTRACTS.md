# CONTRATOS DE MÓDULOS — leer antes de escribir código

Spec vinculante para todos los módulos del backend. Si un módulo no respeta
estas firmas, la integración se rompe.

## Reglas duras

1. **Nada de LLMs cloud.** Prohibido `openai`, `anthropic`, `google.generativeai`
   o cualquier llamada a API de modelos. Sólo lógica determinística, scikit-learn
   y modelos locales opcionales (sentence-transformers / CLIP).
2. **Cero pesos hardcodeados.** Todo umbral/peso sale de `backend/config/weights.yaml`
   vía `app.config.section(...)` / `app.config.weights(...)`.
3. **Degradación elegante.** Si falta un dato o una dependencia opcional, el
   factor se marca no disponible (`raw_score=None`) y se renormaliza. Nunca
   se lanza excepción ni se asume 0.
4. **Todo score compuesto usa `app.services.common.combine()`** para garantizar
   explicabilidad uniforme (`contribution` suma 100 entre factores disponibles).
5. **Sólo librerías ya instaladas**: `fastapi`, `uvicorn`, `pandas`, `numpy`,
   `scikit-learn`, `pyyaml`, `pydantic`, `httpx`, `pytest`. No agregar deps
   pesadas obligatorias — las opcionales van bajo `try/except ImportError`.
6. **Imports absolutos desde `app.`** (ej. `from app.services.common import combine`).
   El backend se ejecuta con cwd=`backend/`.
7. Comentarios y textos de UI en español; nombres de código en inglés.

## Base ya escrita (NO reescribir)

| Archivo | Qué provee |
|---|---|
| `app/schema.sql` | Esquema SQLite completo. Fuente de verdad del modelo de datos. |
| `app/config.py` | `get_config()`, `section(*keys, default=)`, `weights(*keys)`, `DB_PATH`, `DATA_DIR`, `BACKEND_DIR` |
| `app/db.py` | `init_db(path, drop=)`, `get_conn()` (context manager), `query(sql, params)`, `query_one(...)` |
| `app/services/common.py` | `clamp`, `saturate`, `gap_similarity`, `recency_weight`, `parse_date`, `jaccard`, `Factor`, `CompositeScore`, `combine`, `confidence_from_coverage`, `severity_from_score`, `to_json`, `from_json` |
| `config/weights.yaml` | Toda la configuración de scoring. |

### Patrón de score compuesto

```python
from app.config import weights, section
from app.services.common import Factor, combine

w = weights("competitive_match", "weights")
factors = [
    Factor("visual",   visual_score,   w["visual"],   {"note": "..."}),
    Factor("semantic", semantic_score, w["semantic"], {}),
    Factor("price",    None,           w["price"],    {"reason": "sin MSRP"}),  # no disponible
]
result = combine(factors, section("competitive_match", "confidence_thresholds"))
# result.score (0..100), result.coverage, result.confidence, result.factors
```

## Módulos a construir

### `app/seed.py` — carga de datos demo
```python
def seed(db_path=DB_PATH, *, drop: bool = True) -> dict[str, int]
```
Lee los CSV de `backend/data/sample/` e inserta en la DB. Devuelve conteos por
tabla. Idempotente con `drop=True`.

### `app/services/embeddings.py` — vectores locales
```python
def backend_name() -> str                    # 'sentence_transformers' | 'tfidf' | ...
def text_vectors(texts: list[str]) -> np.ndarray      # (n, d), filas L2-normalizadas
def text_similarity(a: str | None, b: str | None) -> float | None   # 0..1 o None
def image_similarity(p_a: dict, p_b: dict) -> tuple[float | None, str]  # (score, método)
```
`text_similarity` usa sentence-transformers si está instalado; si no, TF-IDF
char+word n-grams de scikit-learn. `image_similarity` usa CLIP si está
instalado; si no, fallback determinístico por atributos visuales
(silhouette / colors / materials) y lo informa en el segundo valor.

### `app/services/enrichment.py` — catalogación
```python
def normalize_name(name: str) -> str
def infer_use_case(product: dict) -> tuple[str | None, float]     # (use_case, confianza)
def infer_price_band(price: float | None, country: str) -> str | None
def infer_lifecycle_stage(product: dict, avg_discount_pct: float | None) -> str | None
def enrich_product(product: dict, context: dict | None = None) -> dict
def run_enrichment(db_path=DB_PATH) -> dict[str, int]
```
`enrich_product` devuelve `{"fields": {...columnas de products...},
"attributes": [{"attr_group","attr_name","value_text","value_num","confidence","source"}]}`.
`run_enrichment` recorre todos los productos, aplica el enriquecimiento y
persiste en `products` + `product_attributes`.

### `app/services/matching.py` — motor competitivo
```python
def build_context(db_path=DB_PATH) -> MatchContext     # precarga precios/stock/reviews/editorial/social
def compute_match(nike: dict, competitor: dict, ctx: MatchContext) -> CompositeScore
def run_matching(db_path=DB_PATH) -> dict[str, int]
```
Siete factores, cada uno una función pura `_score_x(nike, comp, ctx) -> tuple[float|None, dict]`:
`visual`, `semantic`, `price`, `retailer_overlap`, `editorial`, `social`, `reviews`.
Persiste en `competitive_matches` + `competitive_match_factors` respetando
`min_score_to_persist` y `top_n_per_product`.

### `app/services/scoring.py` — Business Importance
```python
def business_importance(subject: dict, ctx) -> CompositeScore
def severity(score: float) -> str
```
Aplica el `gate` de relevancia competitiva y el `lifecycle_multiplier` de config.

### `app/services/opportunities.py` — motor de oportunidades
```python
def run_opportunities(db_path=DB_PATH) -> dict[str, int]
```
Una función por regla (12 tipos en `config.opportunities`), cada una:
```python
def _rule_price_competitiveness_risk(ctx) -> list[OpportunityDraft]
```
`OpportunityDraft` = dataclass con `opportunity_type, nike_product_id,
competitor_product_id, retailer_id, title, description, drivers, importance_inputs,
recommendation(action, rationale)`. Persiste en `opportunities` + `recommendations`.

### `app/services/retail_media.py` — Retail Media Opportunity
```python
def score_retail_media(nike_product, competitor_product, retailer, ctx) -> tuple[CompositeScore, str]
def run_retail_media(db_path=DB_PATH) -> dict[str, int]
```
El segundo valor es la recomendación: `INVEST_IN_RETAIL_MEDIA`,
`EVALUATE_PRICE_ACTION_BEFORE_MEDIA`, `DO_NOT_INCREASE_MEDIA`,
`CAPTURE_COMPETITOR_STOCKOUT`, `PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN`.
Implementa los 5 casos del brief. Persiste en `retail_media_opportunities`.

### `app/services/brand_intelligence.py` — Argentina
```python
def run_brand_intelligence(db_path=DB_PATH) -> dict[str, int]
def brand_momentum(brand_id: int, ctx) -> dict
```
Consume `social_mention_aggregates`, `reviews` y `editorial_mentions` filtrados
por `country_code='AR'`. Produce `brand_insights` y `market_signals`.
**Cada insight debe llevar `signal_volume`, `trend`, `direction`, `confidence`
y `evidence` (JSON con ejemplos y fuentes). Prohibido generar texto sin
respaldo cuantitativo.**

### `app/pipeline.py` — orquestador
```python
def run_all(db_path=DB_PATH, *, reset: bool = True) -> dict[str, dict]
```
Orden: `init_db` → `seed` → `enrichment` → `matching` → `brand_intelligence`
→ `opportunities` → `retail_media`.

### `app/api/` — FastAPI
`app/main.py` expone `app`. Routers en `app/api/routers/`:

| Router | Endpoints |
|---|---|
| `overview.py` | `GET /api/overview` — KPIs, top riesgos, top oportunidades, momentum |
| `products.py` | `GET /api/products` (filtros: brand, franchise, category, sport, retailer, price_band, country, q, limit/offset), `GET /api/products/{id}` |
| `matches.py` | `GET /api/products/{id}/matches`, `GET /api/matches/{match_id}` (con factores) |
| `opportunities.py` | `GET /api/opportunities` (filtros: family, type, severity, min_importance) |
| `retail_media.py` | `GET /api/retail-media` |
| `brand.py` | `GET /api/brand/insights`, `GET /api/brand/momentum`, `GET /api/brand/topics` |

CORS abierto a `http://localhost:3000`. Todas las respuestas JSON planas
(sin envoltorios innecesarios). Sin autenticación en el MVP.

## Datos demo (`backend/data/sample/*.csv`)

Archivos, en este orden de carga:
`brands.csv`, `countries.csv`, `retailers.csv`, `products.csv`,
`price_observations.csv`, `stock_observations.csv`, `reviews.csv`,
`editorial_mentions.csv`, `social_mention_aggregates.csv`.

Las columnas deben coincidir exactamente con las del `schema.sql`
(los `id` son explícitos para poder referenciarlos entre archivos).
