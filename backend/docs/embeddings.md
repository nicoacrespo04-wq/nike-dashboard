# Embeddings locales: texto e imagen

Cómo se calculan las similitudes `semantic` y `visual` del motor de matching,
qué backend se usa en cada situación, cómo activar CLIP y qué mejora esperar.

> **Regla dura del proyecto:** cero APIs de LLM cloud. Todo lo que hay acá es
> determinístico o corre en modelos locales opcionales. El camino por defecto
> —sin `torch`, sin red— funciona completo y es el que corre hoy en la demo.

Módulos:

| Archivo | Rol |
|---|---|
| `app/services/embeddings.py` | vectores y similitudes (`text_similarity`, `image_similarity`) |
| `app/services/images.py` | descarga de imágenes, caché en disco y persistencia de embeddings |
| `app/schema.sql` → `product_images` | dónde viven los vectores (`embedding` BLOB float32) |
| `config/weights.yaml` → `embeddings:` | backend y modelo configurados |

---

## 1. Texto — `text_similarity(a, b)`

| Situación | Backend | `backend_name()` |
|---|---|---|
| `sentence-transformers` instalado **y** el modelo carga localmente | modelo local (MiniLM por defecto) | `sentence_transformers` |
| Cualquier otro caso (el de hoy) | TF-IDF word 1-2gram + char_wb 3-5gram (scikit-learn), coseno | `tfidf` |
| Falta texto de un lado | — | devuelve `None` (el factor se marca sin datos y `combine()` renormaliza) |

Configuración: `embeddings.text.backend` (`auto` \| `sentence_transformers` \| `tfidf`)
y `embeddings.text.model`. La carga del modelo está envuelta en `try/except
ImportError` **y** `except Exception`: si no está cacheado localmente se degrada
a TF-IDF sin romper nada.

**Esta parte no cambió** y no debería cambiar: el contrato con `matching.py`
depende de ella.

---

## 2. Imagen — `image_similarity(p_a, p_b) -> (score|None, método)`

### Cascada de decisión

Se evalúa de arriba hacia abajo. Gana la primera fuente que tenga vector **en
los dos productos**, con la misma dimensión y el mismo modelo (o modelo
desconocido); si no, se sigue bajando.

| # | Método devuelto | De dónde sale el vector | Cuándo aplica |
|---|---|---|---|
| 1 | `clip` | `image_embedding` / `embedding` / `clip_embedding` dentro del propio dict recibido | quien llama ya trae los vectores (tests, callers avanzados) |
| 2 | `clip-persisted` | `product_images.embedding` con un modelo CLIP/SigLIP | pipeline normal, después de `compute_image_embeddings` |
| 2' | `embedding-persisted` | ídem, pero con otro encoder local (ej. `pixel-hist-v1` del modo verificación) | verificación offline |
| 3 | `clip-live` | CLIP local calculado al vuelo sobre una imagen **ya cacheada en disco** | hay `torch` + modelo local + imagen bajada, pero el embedding todavía no se persistió |
| 4 | `attribute-fallback` | atributos visuales (`silhouette`, `height`, `design_style`, `dominant_color`, `secondary_colors`, `material`, `upper_material`, `sole_type`) | **estado actual de la demo** |
| 5 | `unavailable` → `(None, 'unavailable')` | — | no hay ninguna evidencia visual |

Notas importantes:

* Los vectores persistidos **no se recalculan nunca**: los produce
  `images.compute_image_embeddings` y `image_similarity` sólo los lee (índice en
  memoria, una query por proceso).
* El camino `clip-live` **nunca descarga**: ni la imagen (tiene que estar en la
  caché de `images.py`) ni el modelo (`local_files_only=True`).
* Un `embedding_model = 'attribute-fallback'` se ignora: es un placeholder, no
  un embedding real.
* El índice exige que `products.image_url` coincida con `product_images.url`
  (y el `product_id`). Así un producto sin imagen nunca toma prestado el vector
  de otro registro.
* Todo está envuelto: si una fuente falla (BLOB corrupto, modelo roto, base
  ilegible) se sigue bajando por la cascada. `image_similarity` no lanza nunca.

### Índice de embeddings persistidos

```python
from app.services import embeddings as emb
emb.load_image_index(db_path)   # lectura read-only; nunca crea la base
emb.reset_image_index()         # tras un ingest (lo hace images.py solo)
```

Si no se llama explícitamente, el índice se carga solo desde `config.DB_PATH` la
primera vez que hace falta. Se puede apagar con `embeddings.image.use_persisted: false`.

---

## 3. Pipeline de imágenes — `app/services/images.py`

```python
fetch_image(url, cache_dir=None) -> Path | None
ingest_images(db_path=DB_PATH, *, limit=None) -> dict[str, int]
compute_image_embeddings(db_path=DB_PATH, *, model=None, force=False) -> dict[str, int]
generate_synthetic_images(db_path=DB_PATH, *, limit=None) -> dict[str, int]
run_synthetic_check(db_path=DB_PATH, *, limit=None) -> dict
```

```bash
cd backend
python -m app.services.images --ingest              # products.image_url -> caché + product_images
python -m app.services.images --embeddings          # imágenes cacheadas -> BLOB float32
python -m app.services.images --synthetic-check     # verificación end-to-end, sin red ni torch
```

* **Caché por hash de URL** (`backend/.cache/images/<sha1>.<ext>`, ignorado por git):
  la misma URL no se baja dos veces, ni siquiera entre corridas.
* **Dedupe por URL** dentro de la corrida: dos productos que comparten foto
  generan una sola descarga y dos filas en `product_images`.
* **Timeout, reintentos acotados** (sólo ante 408/425/429/5xx y errores de red;
  un 404 no se reintenta), **límite de tamaño** y verificación de `content-type`.
* **`robots.txt`**: se consulta una vez por host (cacheado). Si prohíbe la ruta,
  no se descarga. Si el host no publica robots.txt, se asume permitido.
* **Cortacircuitos**: tras 5 fallos seguidos contra un host se deja de
  intentarlo (`skipped_dead_host`), y toda la ingesta tiene un tope de tiempo
  total (`skipped_deadline`). Sin esto, un catálogo grande detrás de un proxy
  que traga conexiones tarda horas en "fallar".
* **Nunca rompe el pipeline**: sin red, sin `httpx`, sin base o sin `image_url`
  devuelve todos los contadores en cero.

Configuración opcional (sección `images:` en `weights.yaml`; todos tienen default):

| Clave | Default | Qué hace |
|---|---|---|
| `cache_dir` | `.cache/images` | caché de imágenes |
| `timeout_seconds` | `10` | timeout por request |
| `max_retries` | `2` | reintentos (=> 3 intentos) |
| `backoff_seconds` | `0.5` | espera lineal entre intentos |
| `max_bytes` | `8388608` | descarta imágenes desproporcionadas |
| `max_host_failures` | `5` | fallos seguidos antes de abandonar un host |
| `max_seconds` | `120` | tope de tiempo total de `ingest_images` |
| `respect_robots` | `true` | respeto de robots.txt |
| `user_agent` | `NikeCompetitiveIntelligenceBot/1.0` | UA identificable |

### Qué se persiste

En `product_images`, una fila por (producto, URL):

| Columna | Contenido |
|---|---|
| `url` | la misma URL de `products.image_url` (clave del match del índice) |
| `is_primary` | 1 para la primera imagen del producto |
| `embedding` | BLOB float32 L2-normalizado (`embeddings.pack`) |
| `embedding_model` | `openai/clip-vit-base-patch32`, `pixel-hist-v1`, … |
| `embedding_dim` | dimensión (512 en CLIP ViT-B/32) |

El archivo binario **no** se guarda en la base: vive en la caché de disco y se
resuelve por hash de URL.

---

## 4. Activar CLIP local

```bash
# 1. Dependencias opcionales (rueda CPU; ~200 MB torch + ~40 MB transformers + ~3 MB pillow)
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install transformers pillow

# 2. Descargar el modelo UNA vez (único momento que requiere red)
python -c "from transformers import CLIPModel, CLIPProcessor; \
  m='openai/clip-vit-base-patch32'; CLIPModel.from_pretrained(m); CLIPProcessor.from_pretrained(m)"

# 3. Imágenes + embeddings
cd backend
python -m app.services.images --ingest --embeddings
python -m app.pipeline        # el matching ya usa los vectores persistidos
```

| Modelo | Peso en disco | Dim | Notas |
|---|---|---|---|
| `openai/clip-vit-base-patch32` (default) | ~600 MB | 512 | ~30-60 ms/imagen en CPU |
| `laion/CLIP-ViT-B-32-laion2B-s34B-b79K` | ~600 MB | 512 | mejor en fotos de e-commerce |
| `google/siglip-base-patch16-224` | ~800 MB | 768 | más preciso, ~3x más lento |

Total con torch instalado: **~850 MB - 1 GB** de disco. Por eso son opcionales.

Config relevante (`weights.yaml`):

```yaml
embeddings:
  image:
    backend: auto     # auto | clip | attribute_fallback  (attribute_fallback apaga CLIP)
    model:   openai/clip-vit-base-patch32
    # opcionales, con default:
    # local_files_only: true      # false permite que transformers baje el modelo
    # use_persisted: true         # false ignora product_images.embedding
    # allow_pixel_fallback: false # true permite el encoder sintético en producción
```

Diagnóstico rápido:

```python
from app.services import embeddings as emb
emb.image_backend_name()   # 'clip' | 'attribute_fallback'
emb.clip_status()          # 'ok (openai/clip-vit-base-patch32)' | por qué no está
```

---

## 5. Verificación end-to-end sin descargar nada

Para probar toda la cadena (archivo → encoder → BLOB → `image_similarity` →
factor `visual`) sin red y sin `torch`:

```bash
cd backend
python -m app.services.images --synthetic-check
```

Qué hace:

1. `generate_synthetic_images` renderiza una imagen PPM determinística por
   producto a partir de sus atributos visuales (color → paleta, silueta+altura →
   forma, id+nombre → textura propia del SKU). Mismo tipo de producto ⇒ imágenes
   parecidas.
2. `compute_image_embeddings(model="pixel")` las codifica con
   `synthetic_image_vector`: descriptor de forma (grilla 8×8) + histograma de
   color por canal, centrado y L2-normalizado, en numpy puro. Se persiste con el
   tag **`pixel-hist-v1`**, así nunca se confunde con un embedding CLIP real.
3. Recarga el índice y mide un par real de la base.

Este encoder **no se activa solo en producción**: `compute_image_embeddings`
sin `model` intenta CLIP y, si no está, no escribe nada (prefiere el fallback
honesto por atributos antes que inventar un vector). Para habilitarlo hay que
pedirlo explícitamente (`model="pixel"` o `embeddings.image.allow_pixel_fallback: true`).

---

## 6. Qué mejora en el factor `visual`

Medición sobre la demo (45 productos, 450 pares Nike × competidor):

| | Hoy (sólo atributos) | Con imágenes + embeddings |
|---|---|---|
| Pares con factor `visual` disponible | **0 / 450** | **450 / 450** |
| Peso visual con datos (`evidence_weight`) | 0.20 (sólo `silhouette`) | 0.67 promedio |
| Score visual del par Pegasus 41 vs Vomero 18 | `None` — *"evidencia visual insuficiente (20% del peso, mínimo 40%)"* | 0.54 disponible |
| Cobertura del match | 0.75 | 0.90 |

Por qué: el sub-peso `embedding` vale **0.50** de
`competitive_match.visual.sub_weights`. Hoy nunca hay embedding, así que la
evidencia máxima posible es 0.50 (silueta+colores+materiales) y en la práctica
es 0.20, por debajo del piso `min_evidence_weight: 0.40`. El factor se declara
sin datos y desaparece del score. Con embeddings, la evidencia arranca en 0.50 y
el factor entra siempre.

Y sobre todo: el embedding **corrige** el ruido de vocabulario que motivó el
piso. Dos zapatillas idénticas etiquetadas `trainer` y `runner` dan
`silhouette=0.0`; con la imagen real el par da `embedding≈0.75` y el factor
queda en 0.54 en vez de desaparecer. El piso `min_evidence_weight` sigue siendo
la red de contención para los productos sin foto — no hay que sacarlo.

---

## 7. Qué falta del lado del scraper

El bloqueante no es el modelo: es que **`products.image_url` no llega**. Hoy
`backend/data/sample/products.csv` trae URLs de demo que no resuelven
(`https://cdn.demo-intel.local/...`) y el mapeo del ingest real
(`app/ingest/mapping.py`) escribe `"image_url": None` porque la fuente de
pricing no tiene esa columna. Para que esto rinda:

1. **Poblar `products.image_url`** en `scraper/adapters/*` con la URL de la
   imagen principal del PDP (`og:image` o el primer `src`/`data-src` de la
   galería), ya absoluta (`urljoin` con el dominio) y sin parámetros de
   redimensionado que cambien en cada scrape — si no, el hash de caché cambia y
   se re-descarga todo.
2. **Preferir la foto de producto sobre fondo neutro**, no la lifestyle: CLIP
   compara producto, y una foto de una modelo corriendo mete ruido de escena.
3. **Resolución media** (600-1000 px): CLIP reescala a 224 px, bajar 3000 px es
   desperdicio de ancho de banda y de la caché.
4. **URL estable por SKU** (no CDN firmado con token de expiración): la caché y
   el índice se apoyan en que la URL sea la clave.
5. Idealmente **varias imágenes por producto** (`product_images` ya soporta N
   filas con `is_primary`); hoy `ingest_images` toma sólo `products.image_url`.
   Cuando el scraper entregue una galería, alcanza con insertar las filas extra
   y `compute_image_embeddings` las codifica igual.
6. **Respetar el `robots.txt` del retailer** también del lado del scraper y
   mantener el mismo User-Agent identificable.

Mientras tanto el sistema no se rompe: sin `image_url` alcanzable, `ingest_images`
devuelve ceros y el factor `visual` sigue resolviéndose por atributos,
exactamente como hoy.
