# Competitive Product Matching Engine — rendimiento y escala

Cómo corre `app/services/matching.py`, dónde se iba el tiempo, qué se optimizó,
qué prueba que los scores **no cambiaron** y hasta dónde escala hoy.

> El "qué mide" de cada factor está en `docs/glossary.md` y `docs/signals.md`;
> el backend de texto e imagen, en `docs/embeddings.md`. Este documento es sobre
> **cuánto cuesta** calcularlo.

Herramientas:

| Archivo | Rol |
|---|---|
| `tools/profile_matching.py` | perfila por fase y por factor, vuelca y compara scores, arma el catálogo sintético |
| `tools/bench_scale.py` | benchmark end-to-end del pipeline entero (otras etapas incluidas) |
| `tools/generate_scale_fixture.py` | `pricing_data` sintético con la suciedad del dato real |

```bash
# perfil sobre la base demo
python tools/profile_matching.py

# línea base ANTES de tocar nada, y verificación DESPUÉS
python tools/profile_matching.py --dump-scores /tmp/before.json
python tools/profile_matching.py --dump-scores /tmp/after.json --compare /tmp/before.json

# a escala: catálogo sintético de ~1.000 productos en una base temporal
python tools/profile_matching.py --scale 1000 --scale-db /tmp/scale.db --sample-nike 8
```

---

## 1. El problema: O(nike × competidores)

`run_matching` evalúa cada producto Nike contra cada producto de otra marca del
mismo país. Con el catálogo demo son 15 × 30 = **450 pares**; con el catálogo
real (~1.000 productos, 400 Nike) son **238.000**; con 5.000 productos, **6
millones**. Cualquier costo por par se multiplica por eso.

En el estado previo a esta optimización el motor costaba **~4 ms por par**, o
sea 45 minutos de proyección para 5.000 productos. No era una constante fea: era
trabajo que **no dependía del par** y se repetía en cada uno.

> **Ojo con medir dos veces.** `embeddings` cachea en memoria la similitud de
> cada par de textos. Una segunda pasada sobre los mismos pares no recalcula
> nada y mide ~20× más rápido que una corrida real, que ve cada par una sola
> vez. `profile_matching.py` resetea las caches antes de cada pasada y paga
> aparte el import de sklearn (`warmup()`), que es constante por proceso.

---

## 2. El perfil ANTES

Base demo (`data/processed/intelligence.db`), 15 Nike × 30 competidores = 450
pares, caches frías, import de sklearn ya pago:

| fase | segundos | % |
|---|---:|---:|
| `build_context` (SQL + índices) | 0,007 | 0,4% |
| **scoring** (7 factores × 450 pares) | **1,788** | **99,3%** |
| persistencia (DELETE + INSERT + JSON) | 0,005 | 0,3% |
| **total `run_matching`** | **1,800** | 100% |

**3,97 ms por par.** Y adentro del scoring:

| factor | µs/par | % del scoring |
|---|---:|---:|
| **semantic** | **3.587** | **91,2%** |
| visual | 197 | 5,0% |
| reviews | 89 | 2,3% |
| price | 25 | 0,6% |
| editorial | 18 | 0,5% |
| retailer_overlap | 11 | 0,3% |
| social | 6 | 0,1% |

El mismo perfil sobre el catálogo sintético de 1.000 productos (400 Nike × 595
competidores, con descripciones, reviews, menciones editoriales y sociales
cargadas; muestra de 8 productos Nike = 4.760 pares):

| factor | µs/par | % |
|---|---:|---:|
| **semantic** | **2.780** | **91,3%** |
| reviews | 77 | 2,5% |
| visual | 75 | 2,5% |
| price | 72 | 2,4% |
| editorial | 22 | 0,7% |
| retailer_overlap | 15 | 0,5% |
| social | 5 | 0,2% |
| **total** | **3.213** | |

### Qué era realmente

`cProfile` sobre la corrida completa:

```
450  compute_match                          5,58 s cum
450  _score_semantic                        4,93 s cum
450  embeddings.text_similarity             4,81 s cum
405  embeddings.text_vectors                4,69 s cum
405  embeddings._tfidf_matrix               4,54 s cum
810  sklearn TfidfVectorizer.fit_transform  2,66 s cum
```

**Un `TfidfVectorizer` ajustado por par.** `embeddings.text_similarity(a, b)`
arma un lote de dos documentos y ajusta DOS vectorizadores (word 1-2gram y
char_wb 3-5gram) sobre ese lote. Como el vocabulario se ajusta al par, no hay
vector reutilizable: 450 pares distintos = 405 ajustes de sklearn a ~4 ms cada
uno. Eso es el 91% del motor.

Las otras sospechas del brief, verificadas:

| Sospecha | Veredicto |
|---|---|
| `generation_map()` / `lineage_key()` por par | **No.** Se calculan una vez en `build_context`. Lo que sí se repetía por par era `section("competitive_match","generation")` y cuatro `ctx.generation()` por par: ruido, pero se arregló. |
| regex recompilándose | **No.** `_TOKEN_RE`, `_VERSION_NUM_RE` y `_TRAILING_VERSION_RE` son constantes de módulo. Sí se **aplicaban** de más: `_norm()` normalizaba Unicode 220.000 veces sobre el mismo puñado de strings. |
| `_norm()` / `_tokens()` sin cache | **Sí.** Ver §3.3. |
| `text_similarity` recomputando TF-IDF por par | **Sí, y era el 91%.** Ver §3.1. |
| (no estaba en la lista) `avg_current_price` | **Sí, y a escala era el segundo:** barría el índice completo de precios (27.000 observaciones) la primera vez que aparecía cada producto → 16 millones de comparaciones por corrida. |

---

## 3. Qué se optimizó

Regla que ordenó todo el trabajo: **nada que dependa de un solo producto se
calcula por par**, y **acelerar no puede mover un score**.

### 3.1 Similitud textual con forma cerrada (`_TextFastPath`)

El TF-IDF de un lote de DOS documentos se puede resolver analíticamente. Con
`smooth_idf` y n = 2 documentos:

```
idf(t) = ln((1 + 2) / (1 + df(t))) + 1
       = 1.0            si t está en los dos documentos
       = 1 + ln(3/2)    si está en uno solo
```

En el coseno sólo sobreviven los términos **compartidos** (los demás multiplican
por cero del otro lado) y ahí el idf vale exactamente 1. Entonces:

```
cos   = Σ_{t∈a∩b} tf_a(t)·tf_b(t) / (‖a‖·‖b‖)
‖a‖²  = Σ_{t∈a} (tf_a(t)·idf(t))² = K²·S_a − (K²−1)·Q_a      con K = 1 + ln(3/2)
```

donde `S_a = Σ tf_a(t)²` (todo el documento) y `Q_a = Σ_{t∈a∩b} tf_a(t)²`. Es
decir: alcanza con los **conteos por documento** —que se calculan una sola vez
por descripción— más una intersección de claves por par. Los dos bloques
(word y char_wb) salen L2-normalizados de sklearn, así que sobre la
concatenación que normaliza `embeddings.text_vectors` queda
`score = Σ_bloques cos_b / √(n_a·n_b)`, con `n_d` = bloques donde el documento
`d` no es el vector nulo.

Tres decisiones que hacen que esto sea seguro y no un "casi igual":

1. **La tokenización no se reimplementa.** Los analizadores salen del propio
   sklearn (`TfidfVectorizer(...).build_analyzer()`) con los mismos parámetros
   que usa `embeddings._tfidf_matrix`. `char_wb` es de todo menos obvio; acá no
   se lo copia.
2. **Autoverificación con datos reales.** `build_context` llama a
   `verify_text_fast_path()`, que compara el atajo contra
   `embeddings.text_similarity` sobre descripciones **de ese catálogo**. Si el
   módulo de embeddings cambia de backend, de n-gramas o de normalización, el
   atajo se apaga solo y la corrida entera usa sklearn: más lento, pero
   correcto.
3. **Salidas por el camino lento.** Backend `sentence_transformers` (ahí el
   vector por texto ya se cachea y no hay nada que atajar), sklearn ausente,
   módulo de embeddings que no expone sus internos, o un par cuyo vocabulario
   superaría `max_features` (el recorte cambiaría las normas) → `text_similarity`
   delega en el módulo.

La tolerancia de la autoverificación es `1e-6`. No es "casi igual": es el ruido
de que sklearn hace la cuenta en float32 (eps ≈ 1,2e-7) y el atajo en float64.
La diferencia máxima medida sobre 300 pares del catálogo demo es **3,8e-8**, que
sobre el score final (0..100) son ~2e-7 puntos: **tres órdenes de magnitud por
debajo de los 4 decimales con los que el score se persiste**.

### 3.2 Índices por producto en `MatchContext`

| Qué | Antes | Ahora |
|---|---|---|
| `avg_current_price` | barría `latest_price` entero la 1ª vez por producto — O(observaciones) por producto | una pasada en `build_context`, en el **mismo orden de inserción** (mismo float, bit a bit) |
| `review_signals` (volumen, atributos del léxico, rating) | se re-extraían las reviews de los dos lados **en cada par** | memo por producto |
| `attr_tokens` (silueta / colores / materiales) | recorría y normalizaba los nombres de atributo en cada par | memo por `(producto, grupo)` |
| listas editoriales compartidas | recorría **todas** las listas del corpus en cada par | intersección de las listas de los dos productos (`lists_by_product` + `list_sets`), en el orden original |
| recencia de cada lista | se recalculaba por par | precalculada por lista |

### 3.3 `_norm` y `_tokens` cacheados

`_norm_str` y `_tokens_str` con `lru_cache(100_000)`. La normalización Unicode
(NFKD + filtrado de combinantes) es cara y se aplicaba sobre los mismos valores
—taxonomía, nombres de atributo, etapas de lifecycle— cientos de miles de veces.

### 3.4 `MatchParams`: la config se lee una vez por corrida

Los pesos siguen viniendo **enteros de `weights.yaml`** (cero hardcodeo); lo que
cambió es *cuándo* se leen. `weights()` reconstruye un dict en cada llamada y los
factores hacían ~19 lecturas por par. Ahora `build_context` arma un `MatchParams`
congelado y los factores leen `ctx.p.*`. El alcance es exactamente el de una
corrida: `run_matching` reconstruye el contexto siempre, y los barridos de
`app/calibration.py` también (cambian la config y vuelven a construir contexto).

---

## 4. El perfil DESPUÉS

Base demo, mismas condiciones:

| fase | ANTES | DESPUÉS |
|---|---:|---:|
| `build_context` | 0,007 s | 0,056 s |
| scoring (450 pares) | 1,788 s | **0,132 s** |
| persistencia | 0,005 s | 0,036 s |
| **total `run_matching`** | **1,800 s** | **0,224 s** (**8,0×**) |
| **por par** | 3,97 ms | **0,29 ms** (**13,5×**) |

`build_context` sube de 7 ms a 56 ms porque ahora arma los índices por producto y
autoverifica el atajo textual: es trabajo que se hace **una vez** y que le ahorra
al scoring 1,65 s.

Etapa `matching` del pipeline (`python -m app.pipeline`), reloj de pared:
**3,25 s → 1,29 s**. De ese 1,29 s, ~1,1 s es el import de sklearn/numpy, que se
paga una sola vez por proceso.

Catálogo sintético de 1.000 productos (muestra de 4.760 pares):

| factor | ANTES µs/par | DESPUÉS µs/par |
|---|---:|---:|
| semantic | 2.780 | **74** |
| visual | 75 | 31 |
| price | 72 | 10 |
| reviews | 77 | 9 |
| retailer_overlap | 15 | 9 |
| editorial | 22 | 4 |
| social | 5 | 2 |
| **total** | **3.213** | **178** (**18,0×**) |

De los 178 µs que quedan, ~47 µs son la intersección de n-gramas del atajo
textual y ~31 µs son `embeddings.image_similarity`, que sin imágenes cae al
fallback por atributos (vive en `app/services/embeddings.py`, de otro dueño).

---

## 5. La prueba de que los scores no cambiaron

Tres capas, de la más específica a la más amplia:

1. **`tools/profile_matching.py --dump-scores/--compare`** vuelca el score de
   **cada par evaluado** (crudo, ajustado, cobertura, confianza y el raw de cada
   factor) y los compara uno a uno. Resultado:

   | base | pares | idénticos a 4 decimales (lo que se persiste) | max \|Δ\| |
   |---|---:|---|---:|
   | demo | 450 | **sí, 450/450** | 1e-6 |
   | sintética 1.000 productos (muestra) | 4.760 | **sí, 4.760/4.760** | 1e-6 |

   (el 1e-6 es el paso de redondeo del propio volcado; el delta real del atajo
   textual es ~4e-8 sobre 0..1).

2. **Tests de regresión** en `tests/test_matching.py` §7:
   * `test_scores_no_cambian_con_las_optimizaciones`: valores dorados (score
     crudo, ajustado, cobertura y confianza) de un catálogo fijo.
   * `test_el_score_completo_es_igual_con_y_sin_atajo_textual`: el par completo
     —los siete factores— puntúa igual por el camino rápido y por el lento.
   * `test_el_atajo_textual_reproduce_a_sklearn`: el TF-IDF analítico contra el
     de sklearn, par por par.
   * `test_el_contexto_memoiza_por_producto_sin_cambiar_resultados`: cada memo
     devuelve lo mismo que la función pura.
   * `test_el_atajo_textual_se_apaga_si_embeddings_cambia`: la degradación.

3. **Suite completa**: 597 tests en verde (589 previos + 8 nuevos), pipeline
   completo con los mismos 140 matches de siempre.

Detalles que hubo que cuidar para que la igualdad sea exacta y no aproximada:

* el promedio de precios se suma **en el mismo orden** que antes (los floats no
  son asociativos);
* las listas editoriales compartidas se recorren en el orden original de
  `editorial_lists`, así los puntos se acumulan igual;
* el prefiltro devuelve los candidatos en el orden de `ctx.products`, porque el
  desempate del top-N es un `sort` estable.

---

## 6. Candidate generation (prefiltro)

Bajar la constante no cambia el orden: sigue siendo O(N²). El prefiltro ataca el
otro lado — **no puntuar los pares que no pueden ganar**.

### Configuración

`competitive_match.candidate_filter` (la clave **no está** en `weights.yaml`: los
defaults viven en `matching._CANDIDATE_FILTER_DEFAULTS` y se leen con
`section(..., default=...)`; agregarla al YAML los pisa).

| clave | default | qué hace |
|---|---|---|
| `enabled` | `true` | `false` = barrido completo, idéntico al de antes |
| `same_category` | `true` | distinta `category` con las dos presentes ⇒ no se puntúa |
| `same_division` | `false` | ídem con `division` (FW/AP/EQ) |
| `gender_conflict` | `false` | descarta men vs women (`_gender_conflict`) |
| `shared_use_field` | `false` | exige compartir `use_case` \| `sport` \| `subcategory` |
| `keep_documented_pairs` | `true` | **rescate**: un par con mención editorial, co-mención social o lista compartida nunca se descarta |
| `min_candidates_per_product` | `null` → 3 × `top_n_per_product` | **red de seguridad**: si el bloque queda más chico, ese producto Nike vuelve al barrido completo |

Los criterios no son heurísticas nuevas de parecido: `same_category` es el mismo
veto que `_score_semantic` ya castiga como `hard_mismatch`, y los campos de uso
son los que más pesan en `field_weights` (0.30 + 0.10 + 0.05).

### Las dos redes de seguridad, y por qué

El brief del producto es explícito: **dos productos pueden verse distintos y
competir igual**. Por eso:

* **`keep_documented_pairs`** — si el mercado ya los compara (una nota "Pegasus
  vs Terrex", una co-mención social, la misma lista de "mejores X"), la evidencia
  externa manda sobre la etiqueta de categoría. Son tres lookups de diccionario
  sobre índices ya precargados.
* **`min_candidates_per_product`** — un catálogo chico o disperso llena su cupo
  de top-N con pares de otra categoría, y sacarlos no sería acelerar sino cambiar
  el resultado. Sobre la base demo la red se activa para **los 15** productos
  Nike: el prefiltro no descarta **nada** y la salida es idéntica.

### Lo medido

Base demo (450 pares):

| | pares evaluados | matches | perdidos |
|---|---:|---:|---:|
| sin prefiltro | 450 | 140 | — |
| con prefiltro (default) | 450 | 140 | **0** |

Catálogo sintético de 1.000 productos (995 productos, 400 Nike × 595
competidores, 238.000 pares, las siete señales prendidas), end to end incluida
la persistencia:

| configuración | pares evaluados | segundos | matches | perdidos | recall |
|---|---:|---:|---:|---:|---:|
| sin prefiltro | 238.000 | 44,98 | 4.000 | — | — |
| **`same_category` (default)** | **79.383 (−66,6%)** | **17,68 (2,5×)** | 4.000 | **29** | **99,28%** |
| `same_category` + `shared_use_field` | 79.383 | 16,48 | 4.000 | 29 | 99,28% |
| + `gender_conflict` | 56.471 | 12,17 | 4.000 | 96 | 97,60% |

El costo del prefiltro es **1,5 µs por par de grilla** contra los ~180 µs que
cuesta puntuar: se paga solo apenas descarta el 1% de los pares.

### Los 29 pares que sí se pierden, y por qué no se puede bajar a cero

Son **29 de 4.000 (0,72%)** y todos están en el **margen del top-10**: se pierde
el puesto 9 ó 10 de 24 productos Nike (de 400) y entra otro par con un score a
0,2–2 puntos de distancia. Ninguno tiene rivalidad documentada (el rescate los
habría salvado). El total de matches no cambia: 4.000 → 4.000.

Vale mirarlos de cerca, porque no todos son pérdidas en el sentido del negocio:

```
! perdido Nike Zoom Fly 38   vs Adidas Tiro Backpack 3   (score 42.54)
! perdido Nike Repel 35      vs Mizuno Core Short 1      (score 48.58)
! perdido Nike Academy Top 30 vs Mizuno Wave Prophecy 1  (score 43.59)
```

Una mochila y un short en el top-10 de una zapatilla de running son justamente el
síntoma de la distribución comprimida: el prefiltro los saca porque el scoring
no los sabía sacar. Aun así **se reportan como pérdida**, porque el criterio de
este trabajo es "acelerar sin cambiar resultados", no "mejorar el ranking de
paso". Cambiar el ranking es una decisión de calibración y va con su propia
evidencia.

Bajarlo a cero **no depende del prefiltro**, y conviene que quede escrito para
quien administre `weights.yaml`:

* `evidence_shrinkage.prior` = 0.35 → el score ajustado de un par sin evidencia
  tiende a **35,0**, que es exactamente `min_score_to_persist`. Así, *cualquier*
  par con cobertura baja "supera el umbral": sobre el catálogo sintético 1.264 de
  4.760 pares son persistibles y casi todos rondan 41–48. Con la distribución
  comprimida en 7 puntos, el ranking del top-N se decide por diferencias de
  décimas y **ningún** prefiltro puede garantizar que no cambie.
* El desajuste de categoría **atenúa** (`hard_mismatch_penalty` = 0.45) en lugar
  de vetar. Una cota superior formal para un par de categoría distinta da ~54
  puntos ajustados: muy por encima del corte real (~44). O sea: no existe una
  poda demostrablemente sin pérdida con esta calibración.

Con `min_score_to_persist` por encima del prior (por ejemplo 45) o con el
desajuste de categoría como veto, el prefiltro pasa a ser exacto. Es una decisión
de calibración, no de este módulo.

**Cómo verificarlo sobre tus propios datos** (el número no se hereda, se mide):

```bash
python tools/profile_matching.py --db <base>     # imprime descarte, recall y pares perdidos
```

---

## 7. Proyección

Costo medido **después** de optimizar: **0,18 ms/par** sobre el corpus a escala
con las siete señales cargadas (0,29 ms/par en la demo, que tiene más señales por
producto). La proporción Nike/competencia se toma del catálogo real sintético
(40% Nike).

| catálogo | pares (grilla) | barrido completo | pares con prefiltro | con prefiltro | ANTES (3,2 ms/par) |
|---:|---:|---:|---:|---:|---:|
| 1.000 productos | 240.396 | 44 s | 78.000 | **14 s** | 12,9 min |
| 5.000 productos | 6.009.900 | 18,4 min | 1,95 M | **6,0 min** | 5,4 h |

Medición real end-to-end (no proyección) sobre 995 productos: **44,98 s** sin
prefiltro y **17,68 s** con prefiltro, contra los **~12,7 min** que habría
costado antes de esta optimización. La corrida de 1.000 productos pasa de "no se
puede correr en el pipeline" a **17 segundos**.

Qué se rompe después: a 5.000 productos, 6 minutos siguen siendo caros para una
corrida completa. Los dos caminos que quedan, en orden de rendimiento esperado:

1. **Bloqueo por índice invertido** — hoy `candidates_for` recorre el catálogo
   entero por producto Nike (O(N²) de comparaciones baratas, 1,5 µs cada una:
   36 s de puro filtro a 5.000 productos). Un índice `(país, categoría) →
   productos` lo baja a O(tamaño del bloque).
2. **Matching incremental** — recalcular sólo los productos que cambiaron desde
   la última corrida (`app/ingest/incremental.py` ya sabe qué cambió).

---

## 8. Cómo no volver atrás

* Antes de tocar el scoring: `python tools/profile_matching.py --dump-scores
  /tmp/before.json`. Después: `--dump-scores /tmp/after.json --compare
  /tmp/before.json`. Devuelve `1` si algún score se movió en el 4º decimal.
* Nada que dependa de **un solo producto** va adentro del bucle de pares: va a
  `build_context` o a un memo de `MatchContext`.
* Nada que dependa de la **config** se lee por par: va a `MatchParams`.
* Si el atajo textual empieza a decir `enabled: False` en
  `verify_text_fast_path`, no es un bug: es la autoverificación avisando que
  `embeddings` cambió de fórmula. El motor sigue dando el número correcto, más
  lento, hasta que se actualice `_TextFastPath`.
