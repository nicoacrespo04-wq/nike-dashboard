# Ingesta de datos reales (`app.ingest`)

De `pricing_data` (PostgreSQL / Supabase) al modelo normalizado del motor.

Módulos:

| módulo | responsabilidad |
|---|---|
| `app/ingest/mapping.py` | mapeo campo por campo, funciones **puras** (se testean sin Postgres) |
| `app/ingest/pricing_data.py` | lectura, deduplicación y persistencia |
| `app/ingest/incremental.py` | carga semanal **append-only** y deducción de `since` |
| `app/ingest/retail_media.py` | `retail_media_search` → share of shelf |
| `tools/generate_scale_fixture.py` | `pricing_data` sintético a escala real |
| `tools/bench_scale.py` | medición end to end + prototipo de candidate generation |

---

## 1. Cómo se corre

### Full refresh (arranque, o cuando cambia el mapeo)

```bash
python -m app.ingest --dsn "$DATABASE_URL" --country AR
```

Recrea la base (`drop=True`) y carga todo. Es lo que hay que correr la primera
vez y cada vez que se toca `mapping.py`: el mapeo cambió, así que lo ya escrito
no vale.

### Incremental (operación semanal)

```bash
python -m app.ingest --dsn "$DATABASE_URL" --incremental
python -m app.ingest --dsn "$DATABASE_URL" --incremental --since 2026-08-01
```

* Sin `--since`, la fecha se deduce de lo ya cargado (`last_ingested_at`).
* **Nunca** recrea la base y **nunca** pisa una observación ya escrita.

### Cargas parciales / correcciones

```bash
# recargar un rango REEMPLAZANDO lo que haya (la fuente corrigió un precio)
python -m app.ingest --dsn "$DATABASE_URL" --keep --since 2026-08-01 --until 2026-08-10

# desde un CSV con los encabezados del archivo original
python -m app.ingest --csv pricing_combinado.csv --country AR --keep
```

### Los tres modos, en una tabla

| modo | flag | base | observaciones ya existentes | cuándo |
|---|---|---|---|---|
| full refresh | *(ninguno)* | se recrea | — | arranque, cambio de mapeo |
| upsert / replace | `--keep` | se conserva | **se reemplazan** | corregir un rango |
| incremental | `--incremental` | se conserva | **se saltean** (append-only) | carga semanal |

### Programarlo semanalmente

Los scrapers corren y escriben `pricing_data`; la ingesta va después. Cron el
lunes a las 06:00 ART (09:00 UTC):

```cron
0 9 * * 1  cd /srv/ci/backend && \
  /srv/ci/.venv/bin/python -m app.ingest --dsn "$DATABASE_URL" --incremental --country AR \
  >> /var/log/ci/ingest.log 2>&1 && \
  /srv/ci/.venv/bin/python -m app.pipeline --keep >> /var/log/ci/pipeline.log 2>&1
```

Tres cosas que hacen que esto sea seguro de programar:

1. **Es idempotente.** Si el cron corre dos veces (reintento, doble scheduler),
   la segunda pasada escribe cero filas. No hace falta un lock.
2. **Se recupera solo.** Si una semana falla, la siguiente corrida deduce
   `since` de lo último cargado y trae las dos semanas juntas.
3. **`--incremental` no borra nada**, así que un delta corrupto se arregla
   volviendo a correr con `--keep --since <fecha>` (que sí reemplaza), sin tener
   que recargar el histórico entero.

`python -m app.pipeline --keep` recalcula el motor sin resetear la base — el
histórico de precios y stock es la entrada de las tendencias, así que **no** hay
que correr el pipeline sin `--keep` después de una ingesta incremental.

---

## 2. Qué hace la carga incremental

### Deducción de `since`

```python
from app.ingest import last_ingested_at, resolve_since, ingest_incremental

last_ingested_at(db_path, country="AR")   # -> date | None
resolve_since(db_path, country="AR")      # -> (date | None, motivo)
ingest_incremental(dsn, db_path, country="AR")
```

`last_ingested_at` mira el `MAX(observed_at)` de `price_observations` **y** de
`stock_observations` (una captura puede traer stock sin ningún precio
utilizable; quedarse con una sola tabla saltearía ese día), filtrando por el
país del retailer.

El `since` deducido es **inclusive**: se relee el último día ya cargado. La
captura de ese día pudo haber quedado a medias — el scraper corre retailer por
retailer y puede fallar uno — y releerla es barato porque lo ya cargado se
saltea. Esa garantía es justamente el modo append-only.

### Qué se escribe

* **Productos — UPSERT por identidad estable.** La clave natural es
  `(marca, país, style_code|sku)` normalizados (`mapping.product_key`;
  `canon_key` saca acentos, puntuación y espacios). Un SKU que ya existe se
  actualiza campo a campo con `COALESCE(nuevo, viejo)`: nunca se duplica y nunca
  se le borra un valor con un NULL. Las columnas de `enrichment`
  (`normalized_product_name`, `use_case`, `price_band`, `lifecycle_stage`,
  `enrichment_version`) no se tocan.
* **Observaciones — append-only.** La clave `(producto, retailer, fecha)` que ya
  está en la base **no se toca**: se cuenta como salteada. Si la fuente cambia
  un precio de hace tres semanas, el histórico conserva el original.

### Qué reporta

```
  products_inserted                   0      productos nuevos
  products_updated                  995      identidades ya conocidas (upsert)
  price_observations             11,009      filas de precio NUEVAS
  price_observations_skipped      5,534      ya existían: no se pisaron
  stock_observations             11,411
  stock_observations_skipped      5,739
  observations_inserted          22,420      total nuevas
  observations_skipped           11,273      total salteadas
```

---

## 3. Columnas que se mapean

Cada fila de `pricing_data` es **una comparación**: de ahí salen **dos**
productos (el del competidor y el de Nike), dos observaciones de precio y dos de
stock.

### Productos

| destino (`products`) | bloque competidor | bloque Nike |
|---|---|---|
| `sku` | `productcode_competitor` → `product_code_competitor` | `style_color` |
| `style_code` | *(NULL)* | `style_color` |
| `product_name` | `product_name_competitor` → `sku` | `marketing_name` → `style_color` |
| `franchise` | `franchise_competitor` | `franchise_scrapper` |
| `category` | `division_competitor` vía `division_map` | `division` vía `division_map` |
| `sport` | `category_competitor` vía `sport_map` | `category` vía `sport_map` |
| `subcategory` | `silueta` | `silueta` |
| `gender` | `gender_competitor` vía `gender_map` | `gender` vía `gender_map` |
| `age_segment` | `'kids'` si el género es kids, si no NULL | ídem |
| `msrp` | `competitor_full_price` saneado | `precio_sugerido` → `nike_full_price` |
| `url` | `link_pdp_competitor` | `pdp_nike` |
| `country_code` | scraper de sitio de marca → sufijo del scraper → `--country` | ídem |

`brand` sale de `marca` normalizada a UPPER con alias (`NB`→`NEW BALANCE`,
`JORDAN`→`NIKE`); el bloque Nike es siempre `NIKE`. Si `marca` falta y el
scraper es un sitio de marca, la marca sale de ahí.

`model`, `version`, `launch_date`, `activity`, `performance_vs_lifestyle`,
`consumer_segment`, `image_url` y `description` quedan en **NULL**:
`pricing_data` no los trae y no se inventan.

### Observaciones

| destino | competidor | Nike |
|---|---|---|
| `price_observations.full_price` | `competitor_full_price` | `nike_full_price` |
| `price_observations.current_price` | `competitor_final_price` | `nike_final_price` |
| `price_observations.discount_pct` | calculado sobre los precios ya saneados | ídem |
| `price_observations.currency` | según el país (`ARS`, `USD`, …) | ídem |
| `stock_observations.sizes_available` | `size_available_competitor` | `size_available_nike` |
| `stock_observations.in_stock` | `sizes_available > 0` | ídem |
| `stock_observations.sizes_total` | de `text_sizes_competitor`, si se puede | de `text_sizes_nike` |
| `observed_at` | `fecha_corrida` | ídem |

`availability_pct` se deriva **sólo** si `text_sizes_*` permite reconstruir el
grid de talles. Sin grid no hay denominador y queda NULL: no se inventa.

### Retailers

`canal` / `scraper` → nombre canónico (`retailer_names`), con el sufijo de país
sacado aparte: `'Dexter_AR'`, `'dexter_ar'` y `'Dexter'` son **un** retailer.
`importance` y `channel` salen de `weights.yaml:ingest` (o de
`$CI_INGEST_CONFIG`); el código sólo trae los defaults.

Los scrapers de sitio de marca (`nike_ar_general`, `adidas_7`, `puma_ar`, `URU`,
`USA`) **no** son retailers: entran como canal D2C para no perder su catálogo ni
sus precios, pero se cuentan aparte (`retailers_non_retail_d2c`).

El precio del bloque Nike se imputa al **D2C del país** (`nike.com.ar`), no al
retailer de la fila: `nike_price_channel: 'd2c'`. La presencia de Nike *en* un
retailer se deriva de filas con `marca='NIKE'` capturadas ahí — ver §5, porque
tiene consecuencias grandes aguas abajo.

---

## 4. Qué pasa con los precios sucios

Mismo criterio que `web/src/lib/price.ts` y `db/load_csv.py` — no se inventa uno
nuevo. `sanitize_price(valor, cuotas)` devuelve `(precio, motivo)`:

| entrada | resultado | motivo |
|---|---|---|
| `0`, `-1`, `"0"` | `None` | `zero` — el 0 significa "no lo pude leer", no "gratis" |
| `"abc"`, `"#N/A"`, `"nan"` | `None` | `not_numeric` |
| `1.781.994` con `"6 cuotas sin interés"` | `296.999` | `cuotas` — se divide por N |
| `1.781.994` sin cuotas declaradas | `None` | `out_of_range` — **no se adivina el divisor** |
| `296.999` | `296.999` | *(sin motivo: pasa intacto)* |

Rango plausible y cuotas máximas: `PRICE_MIN_ARS` (1.000), `PRICE_MAX_ARS`
(2.000.000), `PRICE_MAX_CUOTAS` (24). Cuotas válidas: 2..24; fuera de ese rango
el número no se interpreta como cuotas.

Un precio descartado **no** rompe la fila: si queda al menos uno de los dos
(lista o final), la observación se escribe con el otro en NULL. Sólo si no queda
ninguno se descarta (`price_rows_unusable`).

Todo esto se reporta en el resumen de cada corrida (`prices_zero_discarded`,
`prices_fixed_by_cuotas`, `prices_out_of_range_discarded`, …), así que una
degradación del scraper se ve en los conteos antes que en el dashboard.

---

## 5. La prueba a escala real

> La Supabase real no está disponible y su credencial está publicada en el repo
> sin rotar — **no se usó**. La prueba corre contra un `pricing_data` sintético
> del mismo tamaño y con la misma suciedad, cargado en un PostgreSQL local.

### El fixture

```bash
python tools/generate_scale_fixture.py --dsn "$SCALE_DSN" \
    --rows 70000 --products 1000 --retailers 10 --dates 5 --end-date 2026-08-10
```

Determinístico por `--seed`. 70.000 filas, 5 capturas semanales, 10 retailers.
Suciedad medida sobre las filas generadas:

| suciedad | filas |
|---|---|
| `marca` con casing mixto (32 variantes de 11 marcas) | todas |
| precio en `0` | 6.832 |
| precio inflado por cuotas (con y sin cuotas declaradas) | 744 |
| sin precio de lista | 10.006 |
| sin `fecha_corrida` | 289 |
| de otro país (`*_CL`) | 1.317 |
| de un sitio de marca (no retailer) | 3.331 |
| sin grilla de talles | 1.973 |
| **con `marca='Nike'`** (Nike capturado *en* un retailer) | 13.751 |

Ese último no es suciedad: es estructura. En `pricing_data` real ~1 de cada 5
filas trae un producto Nike en el bloque "competidor", y es la **única**
evidencia de presencia de Nike en góndola. Sin esas filas, Nike existe sólo en
su D2C, ningún par comparte retailer y las reglas que comparan "en el mismo
retailer" se quedan sin entrada — lo verificamos: con un fixture sin ellas,
`price_competitiveness_risk` produce **0** oportunidades y
`premiumization_opportunity.min_match_score` queda UNREACHABLE.

### Resultado de la ingesta

70.000 filas → **995 productos** (400 Nike + 595 competidores), 13 retailers,
11 marcas, 27.593 observaciones de precio y 28.583 de stock. Del lado sucio:
13.700 precios en 0 descartados, 1.976 corregidos dividiendo por cuotas, 938
fuera de rango descartados, 1.317 filas de otro país y 280 sin fecha omitidas.
Escala equivalente a la corrida real (~73.000 filas → ~984 productos).

### Tiempos por etapa

`python tools/bench_scale.py --dsn "$SCALE_DSN" --db /tmp/scale.db --blocking`

| etapa | segundos | % | salida |
|---|---:|---:|---|
| **matching** | **45.21** | **49.1%** | 238.000 pares, 4.000 matches |
| **retail_media** | **26.60** | **28.9%** | 31.723 tripletes, 23.042 oportunidades |
| ingest | 18.26 | 19.8% | 70.000 filas → 995 productos |
| opportunities | 1.50 | 1.6% | 1.985 oportunidades |
| enrichment | 0.29 | 0.3% | 995 productos |
| shelf_signals | 0.15 | 0.2% | 13 señales |
| brand_intelligence | 0.01 | 0.0% | 0 (sin señales sociales/editoriales) |
| **TOTAL** | **92.02** | | |

La ingesta escala **lineal** (18s para 70.000 filas ≈ 0,26 ms/fila) y no es el
problema. El problema es `matching`.

### Dónde explota: `matching` es O(nike × competidores)

Misma corrida a distintos tamaños de catálogo:

| productos | Nike × comp | pares | matching | ms/par | con bloqueo | speedup |
|---:|---|---:|---:|---:|---:|---:|
| 250 | 100 × 150 | 15.000 | 2,8 s | 0,189 | 0,3 s | 11,1× |
| 500 | 200 × 300 | 60.000 | 10,5 s | 0,175 | 1,4 s | 7,3× |
| 1.000 | 400 × 595 | 238.000 | 43,4 s | 0,182 | 3,3 s | 13,1× |
| 2.000 | 800 × 1.131 | 904.800 | 158,6 s | 0,175 | 12,9 s | 12,3× |

El costo por par es **constante** (~0,18 ms): duplicar el catálogo cuadruplica
los pares y cuadruplica el tiempo. Es O(N²) puro. La proyección para el catálogo
completo (los ~1.000 productos son sólo AR; con CO, UY y US el catálogo se
multiplica) es la que hay que mirar: 4.000 productos ≈ **10 minutos** de
matching por corrida.

Dos matices que el número solo no muestra:

* Los 0,18 ms/par son con el corpus **ingerido**, que no tiene reviews, ni
  menciones editoriales, ni co-menciones sociales, ni descripciones: cuatro de
  los siete factores cortan de entrada. Sobre el dataset demo, que sí las tiene,
  el mismo `compute_match` cuesta ~7 ms/par la primera vez (fit del TF-IDF) y el
  factor `semantic` domina. **Cuando `app/collectors` empiece a poblar reviews y
  editorial, el costo por par sube y la cuadrática pega mucho más fuerte.**
* `retail_media` (26,6 s) crece **lineal** en matches × retailers, no
  cuadrático. No es el cuello de botella estructural, pero 23.042
  oportunidades persistidas de 31.723 tripletes evaluados es un problema de
  umbral, no de performance (ver §6).

### Candidate generation: 12,9× con recall 100%

Prototipo en `tools/bench_scale.py` (`is_candidate`, `candidates_for`,
`build_candidate_index`). Un par se puntúa sólo si:

1. **comparte `category`** (footwear / apparel / accessories) — si a alguno le
   falta, no se filtra;
2. **comparte al menos un campo de uso**: `use_case` | `sport` | `subcategory`
   (se pide **uno**, no todos, para que un producto con `use_case` en NULL siga
   bloqueando por `sport`);
3. **no tiene conflicto duro de género** (`matching._gender_conflict`: men vs.
   women; `unisex` no es conflicto).

Los tres criterios salen de lo que el scoring **ya** considera decisivo
(`hard_mismatch_penalty` para categoría y género; `field_weights` de 0.30 + 0.10
+ 0.05 para los campos de uso). No es una heurística nueva.

Medido sobre los 995 productos:

| | pares evaluados | segundos |
|---|---:|---:|
| barrido completo | 238.000 | 46,38 |
| con bloqueo | 15.360 (**−93,55%**) | **3,59** (**12,9×**) |

* **Recall de candidatos: 100,0%.** Los 4.000 pares que el barrido completo
  persistió fueron *todos* evaluados por el bloqueo. No se pierde nada por
  construcción.
* Diferencias en el conjunto final: 9 perdidos / 9 agregados, y los 9 son
  **empates exactos** (score 45.0) en el corte del `top_n_per_product`; los 20
  productos Nike afectados tienen el mismo score en el puesto 10 y en el 11. No
  es pérdida de recall, es desempate.

Un barrido de variantes (mismo script) muestra por qué hacen falta las tres
condiciones juntas:

| bloqueo | pares | % de la grilla | recall |
|---|---:|---:|---:|
| unión(use_case, sport, subcategory, category) | 138.536 | 58,2% | 100% |
| unión(use_case, sport, subcategory) | 57.953 | 24,4% | 100% |
| category **Y** (use_case \| sport \| subcategory) | 28.977 | 12,2% | 100% |
| + sin conflicto de género | **15.522** | **6,5%** | **100%** |

**El arreglo va en `app/services/matching.py`, que NO es de este módulo.** Ver §7.

---

## 6. Calibración: qué cambia con datos reales

`python -m app.calibration --db <base>` sobre las dos bases, con el mismo
`weights.yaml`:

| | demo (45 productos) | escala (995 productos) |
|---|---:|---:|
| umbrales evaluados | 41 | 41 |
| OK | 35 | 26 |
| UNREACHABLE | 0 | **5** |
| NO_DATA | 0 | **8** |
| TRIVIAL | 6 | 2 |
| defectos de calibración | 3 | **6** |
| reglas que producen | 12 / 12 | **7 / 12** |
| oportunidades | 52 | 1.985 |

**15 de 41 umbrales cambian de veredicto.** La respuesta a "¿la calibración
actual sirve con datos reales?" es **no**, y por dos motivos distintos que
conviene no mezclar.

### (a) Cinco umbrales quedan INALCANZABLES — son bugs de calibración

| umbral | valor | escala real | veredicto |
|---|---:|---|---|
| `competitive_match.visual.min_evidence_weight` | 0.35 | techo **analítico** 0.20 | el factor `visual` queda apagado en el **100%** de los pares |
| `competitive_match.confidence_thresholds.high` | 0.70 | cobertura observada **0.50 exacta** en los 4.000 matches | ningún match puede ser HIGH |
| `business_importance.severity_thresholds.critical` | 78 | techo analítico **71.16**, máximo observado 71.97 | ninguna oportunidad puede ser CRITICAL |
| `opportunities.share_of_shelf_risk.min_shelf_drop_pp` | 4 | máximo observado **1.93 pp** | la regla nunca dispara |
| `opportunities.assortment_white_space.min_demand_signal` | 0.5 | máximo observado **0.00** | la regla nunca dispara |

Los dos primeros están **encadenados**, y ese encadenamiento es el hallazgo
central: los productos ingeridos no tienen atributos de color ni de material
(`pricing_data` no los trae), así que la única sub-señal visual es la silueta
→ evidencia 0.20 < 0.35 → **`visual` se declara sin datos en todos los pares**
→ su peso (0.15) sale de la cobertura → la cobertura queda clavada en **0.50
exacta** para los 4.000 matches → `confidence_thresholds.high` (0.70) es
imposible y `medium` (0.45) lo pasa el 100% (TRIVIAL). **La etiqueta de
confianza del match no informa nada con datos reales: todos los matches son
MEDIUM.** Y como el score ajustado es `crudo * cobertura + prior * (1 -
cobertura)`, la cobertura clavada en 0.50 le pone techo al match (66.35
observado vs. 75.20 en demo), que a su vez le pone techo a
`business_importance` (71.97 vs. 82.09) y deja CRITICAL vacío.

En el demo nada de esto se ve porque `seed` escribe atributos visuales a mano.

### (b) Ocho umbrales quedan sin datos — no son bugs, es lo que la fuente trae

`insight_signal_volume` (×2), `product_review_volume`, `pair_comentions`,
`competitor_acceleration`, `days_since_launch`,
`competitor_retailer_coverage`, `competitor_momentum`.

`pricing_data` es precio y stock. **No trae reviews, ni menciones editoriales,
ni señales sociales, ni fecha de lanzamiento.** Esas las tiene que traer
`app/collectors`. Consecuencia directa: `brand_intelligence` produce **0**
insights y **5 de las 12 reglas** de oportunidades no pueden disparar
(`competitor_momentum`, `product_launch_threat`, `assortment_white_space`,
`share_of_shelf_risk`, `assortment_gap`). El motor corre, pero con datos reales
hoy usa 7 de 12 reglas y 3 de 7 factores de matching.

### (c) Dos umbrales dejan de ser TRIVIAL y pasan a discriminar

`full_price_opportunity.min_nike_discount_pct` (10) y
`promotional_pressure.min_competitors_on_markdown` (2): en el demo los pasaba el
100%, a escala filtran 95% y 98%. Están bien donde están; el demo era demasiado
chico para juzgarlos.

### Cortes sugeridos (NO aplicados — `weights.yaml` no es de este módulo)

`app.calibration` los emite y hay que decidirlos a mano:

```yaml
business_importance:
  severity_thresholds:
    critical: 59.3   # actual 78 — hoy CRITICAL queda vacío sobre n=1985
    high:     54.0   # actual 60
    medium:   48.9   # actual 40
```

Para `competitive_match.visual.min_evidence_weight` la salida **no** es bajarlo
a 0.20: eso admite como evidencia visual una etiqueta de silueta sola, que es
exactamente el bug que el umbral existe para prevenir (está documentado en
`weights.yaml`). La salida es alimentar la sub-señal — imágenes vía
`app/services/images.py` + `embeddings`, o atributos de color/material — o
aceptar que el factor `visual` no aplica a datos de `pricing_data` y sacarlo del
peso, que hoy es una decisión de producto, no de calibración.

---

## 7. Cambios necesarios fuera de este módulo

### 7.1 `app/services/matching.py` — candidate generation (ALTA)

**Qué:** en `run_matching`, reemplazar el doble `for` sobre
`ctx.products.values()` por un índice invertido + filtro de candidatos.

**Por qué, con números:** 238.000 pares para 995 productos, 904.800 para 2.000;
0,18 ms/par constante ⇒ O(N²). El bloqueo evalúa 15.360 pares (−93,55%) y baja
el matching de 46,4 s a 3,6 s (**12,9×**) con **recall de candidatos 100%** (las
únicas diferencias son 9 empates exactos en el corte del top-N). Hoy `matching`
es el 49% del pipeline; a 4.000 productos son ~10 minutos por corrida.

**Cómo (patch conceptual, ~25 líneas):**

```python
# app/services/matching.py

_USE_FIELDS = ("use_case", "sport", "subcategory")

def _use_keys(product: dict) -> set[tuple[str, str]]:
    return {(f, _norm(product.get(f))) for f in _USE_FIELDS if _norm(product.get(f))}

def is_candidate(nike: dict, comp: dict) -> bool:
    """Filtro previo al scoring. Los tres criterios salen de lo que el propio
    scoring ya considera decisivo, así que no cambia el ranking."""
    cat_a, cat_b = _norm(nike.get("category")), _norm(comp.get("category"))
    if cat_a and cat_b and cat_a != cat_b:      # ya penalizado por hard_mismatch
        return False
    if not (_use_keys(nike) & _use_keys(comp)): # use_case 0.30 + sport 0.10 + subcat 0.05
        return False
    return not _gender_conflict(nike.get("gender"), comp.get("gender"))

def build_candidate_index(ctx) -> dict[tuple, list[dict]]:
    index = defaultdict(list)
    for p in ctx.products.values():
        if p.get("brand_is_focus"):
            continue
        for field, value in _use_keys(p):
            index[(p.get("country_code"), field, value)].append(p)
    return index

# en run_matching, en vez de `for comp in ctx.products.values()`:
index = build_candidate_index(ctx)
...
    seen = {}
    for field, value in _use_keys(nike):
        for comp in index.get((nike.get("country_code"), field, value), ()):
            if comp["id"] in seen or comp["brand_id"] == nike["brand_id"]:
                continue
            if is_candidate(nike, comp):
                seen[comp["id"]] = comp
    for comp in seen.values():
        ...   # el scoring queda igual
```

Recomendación: detrás de una clave de config
(`competitive_match.candidate_generation.enabled`, default `true`) para poder
volver al barrido completo y comparar. Un producto **sin ninguna** clave de uso
(taxonomía vacía) no entra en ningún bloque: hay que decidir si cae al barrido
completo o si se reporta como producto sin clasificar — hoy el enrichment le
pone `use_case` a los 995, así que no pasa, pero es el caso borde a cubrir.

El prototipo medido está en `tools/bench_scale.py`; se puede correr
`python tools/bench_scale.py --db <base> --stage matching --blocking` para
reproducir los números antes y después.

### 7.2 `app/services/matching.py` — `avg_current_price` (MEDIA)

`MatchContext.avg_current_price` recorre **todo** `latest_price` para cada
producto (`if pid == product_id`). Está memoizado por producto, así que el costo
total es O(productos × observaciones) = 995 × 24.471 ≈ 24 M iteraciones por
corrida. Con el bloqueo aplicado pasa a ser una fracción visible del tiempo
restante. Se arregla precomputando un `dict[product_id, list[float]]` en
`build_context`, en la misma pasada que ya hace sobre `price_observations`.

### 7.3 `config/weights.yaml` (ALTA) — ver §6

`business_importance.severity_thresholds` (78/60/40 → CRITICAL vacío) y
`competitive_match.confidence_thresholds.high` (0.70 → inalcanzable con
cobertura clavada en 0.50). No se tocaron acá.

### 7.4 `app/collectors/**` (ALTA, de producto)

Cuatro de los siete factores de matching y cinco de las doce reglas de
oportunidades no tienen entrada con `pricing_data` sola. Sin reviews, editorial,
social ni `launch_date`, el motor a escala real corre al 58% de sus reglas.

### 7.5 `app/services/retail_media.py` (MEDIA)

23.042 oportunidades persistidas sobre 31.723 tripletes evaluados (73% pasa
`min_score_to_report = 40`). No es un problema de performance sino de volumen:
23.000 recomendaciones no son accionables. El umbral está calibrado contra 392
tripletes del demo; a escala hace falta o subirlo o agregar un top-N por
producto Nike, como ya hace `matching` con `top_n_per_product`.

---

## 8. Tests

```bash
cd backend && python -m pytest tests/test_ingest.py tests/test_ingest_incremental.py -q
```

* `tests/test_ingest.py` — mapeo puro, deduplicación, saneamiento de precios,
  idempotencia del modo full/replace.
* `tests/test_ingest_incremental.py` — `last_ingested_at` (base inexistente,
  vacía, por país, stock sin precio), deducción de `since`, append-only (el
  histórico no se pisa aunque la fuente cambie un precio viejo), UPSERT de
  productos, e idempotencia (correr dos veces el mismo delta deja la base
  idéntica y escribe 0 filas).

La carga incremental también se verificó contra el PostgreSQL local: cargar 3
capturas full y después dos incrementales deja **exactamente** la misma base que
un full refresh de las 5 capturas (mismos conteos, misma suma de precios y de
talles disponibles).
