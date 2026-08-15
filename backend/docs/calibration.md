# Calibración del motor

Cómo se recalibra el scoring cuando entran datos reales, qué mirar primero, y
las cotas analíticas que hacen que un umbral se pueda declarar imposible **por
construcción** y no "por ahora".

Módulo: `app/calibration.py` · Tests: `tests/test_calibration.py`

```bash
cd backend
python -m app.pipeline          # recalcula la base
python -m app.calibration       # reporte de calibración
```

---

## 1. Por qué existe

Todos los pesos y umbrales viven en `config/weights.yaml`, pero **un umbral sólo
significa algo contra la escala real de la métrica que filtra**. Dos veces la
calibración quedó internamente inconsistente y nadie lo notó hasta mirar la
salida a ojo:

| Caso | Qué pasó | Por qué no se vio |
|---|---|---|
| `business_importance.severity_thresholds` = 78/60/40 | El gate es multiplicativo y `competitive_relevance = match_score/100`, con techo ~0.69. CRITICAL y HIGH eran **inalcanzables por construcción**: las 60 oportunidades caían en MEDIUM/LOW. | El motor no falla: devuelve severidades válidas. Sólo que todas iguales. |
| `premiumization_opportunity.min_match_score` = 70 | La escala ajustada por evidencia tiene máximo 69. La regla **nunca disparaba**. | Una regla en 0 se confunde con "no hay nada que reportar". |

Los dos son el mismo bug: **un número calibrado contra la escala equivocada**
(0..100 teórico en vez del rango realmente alcanzable). Eso no se detecta
leyendo el YAML; se detecta comparando cada umbral contra la distribución de su
métrica. Eso es este harness.

---

## 2. Qué mirar primero

Después de cada `python -m app.pipeline`, correr `python -m app.calibration` y
leer **en este orden**:

1. **Sección 2 — Alcanzabilidad.** Todo lo que diga `UNREACHABLE` marcado con
   `✗` es un bug de calibración, no una opinión: hay un umbral que ningún
   registro puede cruzar. Si además la columna `base` dice `analítica`, no es
   mala suerte del dataset: la fórmula lo prohíbe.
2. **Sección 3 — Rendimiento de las 12 reglas.** Cualquier regla en `ROTA` o
   `SIN_SEÑAL`. Una regla en `NADA_QUE_REPORTAR` está bien: es el motor diciendo
   que no hay problema, que es una respuesta legítima.
3. **Sección 2 otra vez — `TRIVIAL` marcados con `✗`.** Un discriminador o una
   banda que pasa el 100% de los registros no está decidiendo nada. Los
   `TRIVIAL` marcados con `·` (gates y parámetros de escala) son informativos:
   ahí que pase todo es lo esperable.
4. **Sección 4 — Sugerencias.** Sólo entonces. El YAML del final es una
   propuesta para copiar y pegar; **el harness nunca escribe `weights.yaml`**.
5. **Sección 1 — Distribuciones.** Cuando algo de lo anterior sorprende, acá
   está el porqué: percentiles, rango y techo de cada escala.

Para CI: `python -m app.calibration --strict` devuelve `1` si hay algún umbral
inalcanzable. `--json` emite el mismo reporte completo como JSON.

---

## 3. Las cuatro clasificaciones

| Status | Significa | Acción |
|---|---|---|
| `UNREACHABLE` | Ningún registro puede cruzarlo (por techo analítico o por máximo observado). | Bug. Bajar el umbral o conseguir la señal que falta. |
| `TRIVIAL` | Lo cruza el 100% de los registros. | Bug **si** es discriminador o banda; esperable si es gate o escala. |
| `OK` | Hay masa de los dos lados. | Nada. Vigilar si `pasan` es 1 o 2 registros: está al borde. |
| `NO_DATA` | La métrica no tiene ni una observación. | No es un problema de umbral sino de datos: falta la señal de entrada. |

El **tipo** de umbral cambia el veredicto, y por eso está declarado en
`THRESHOLDS`:

* `discriminator` — selecciona un subconjunto (qué dispara, qué se reporta).
  Que lo pase todo es un defecto.
* `band` — corte de una etiqueta ordinal (severidad, confianza). Se juzga **por
  grupo**: el defecto no es que un número sea alto, es que alguna banda quede
  vacía y la etiqueta deje de informar.
* `gate` — mínimo de evidencia para que una señal cuente (`min_comentions`,
  `min_reviews_for_signal`, `min_evidence_weight`). Que lo pase todo **no** es
  un bug: el corpus tiene evidencia de sobra y el gate protege contra datasets
  pobres. Que no lo pase nadie sí lo es: apaga el factor entero.
* `scale` — parámetro de forma (`gap_tolerance_pct`, `gate_floor`). Informativo.

---

## 4. Cotas analíticas

Comparar contra el máximo observado alcanza para detectar un umbral muerto, pero
no explica **por qué** está muerto ni sobrevive a un cambio de dataset. Donde la
fórmula permite razonar el techo, el harness lo razona y lo imprime.

### 4.1 `match_score` (ajustado por evidencia)

```
ajustado = crudo · C + 100 · prior · (1 − C)          C = cobertura
```

Con `crudo ≤ 100`:

```
ajustado ≤ 100·C_max + 100·prior·(1 − C_max)
```

`C_max` **no es 1.0**: un factor que nunca tiene datos en el corpus le pone
techo duro a la cobertura. Hoy `visual` no tiene datos en ningún par, así que
`C_max = 0.85` y el techo del match ajustado queda en **90.25** (con
`prior = 0.35`).

### 4.2 Piso de la cobertura

Simétricamente, los factores que tienen datos en el **100%** de los pares
garantizan cobertura:

```
C_min = Σ w_i  para todo i disponible en todos los pares
```

Hoy `semantic + price + retailer_overlap + reviews = 0.60`. Consecuencia
directa: con `confidence_thresholds.medium = 0.45` **ningún match puede ser
LOW**. La etiqueta LOW es código muerto, y no por el dataset: por construcción.

### 4.3 `business_importance` — la cota que importa

```
importance = base · gate · lifecycle
base       = 100 · Σ(w_i·s_i) / Σ(w_i disponibles)
gate       = clamp(competitive_relevance, gate_floor, 1)
competitive_relevance = match_score / 100
```

El gate es **multiplicativo**, así que el techo sale de dos ramas:

**(a) con relevancia medida** — `s_relevance ≤ R = max(match_score)/100`, y ese
componente entra además en `base` con su peso `w_rel`:

```
base ≤ 100 · (W − w_rel·(1 − R)) / W          W = Σ w
importance ≤ base_max · R · max(lifecycle_multiplier)
```

**(b) sin relevancia medida** — `base ≤ 100`, pero el gate cae al piso:

```
importance ≤ 100 · gate_floor · max(lifecycle_multiplier)
```

```
techo = min(100, max(a, b))
```

Con el dataset demo (`R = 0.691`, `w_rel = 0.20`, `W = 1.0`, `lifecycle ≤ 1.15`,
`gate_floor = 0.35`):

```
(a) 93.8 · 0.691 · 1.15 = 74.6
(b) 100  · 0.35  · 1.15 = 40.3
techo = 74.6
```

**Ese número es el que mata al 78.** No hace falta ver ninguna oportunidad para
saber que CRITICAL ≥ 78 es imposible: la fórmula no llega. El máximo observado
(56.1) es todavía más bajo, así que 62 también queda inalcanzable, pero eso ya
es un hecho del dataset y puede cambiar con datos nuevos.

> **Cuándo sube este techo:** cuando suba la escala de match. Más evidencia
> (editorial, social, visual) ⇒ más cobertura ⇒ matches más altos ⇒ `R` mayor
> ⇒ techo mayor. Por eso los cortes de severidad se **reajustan** cada vez que
> cambia el matching, y por eso el harness los recalcula en vez de fijarlos.

### 4.4 `retail_media` score

Mismo argumento, encadenado: `competitive_relevance` (w 0.15) y
`business_importance` (w 0.15) arrastran sus propios techos.

```
score ≤ 100 · (W − w_rel·(1 − R) − w_bi·(1 − B)) / W
```

Con `R = 0.691` y `B = 0.746` da **91.6**.

### 4.5 Evidencia visual

```
evidence_weight = Σ sub_pesos con datos / Σ sub_pesos
```

Si CLIP no está disponible, el sub-peso `embedding` (0.50) nunca suma: el techo
baja a 0.50 y hace falta silueta **+** colores **+** materiales en los dos
productos para cruzar `min_evidence_weight = 0.40`. Hoy ningún par lo logra
(máximo observado 0.35), así que **el factor visual está apagado para el 100% de
los pares** y su peso 0.15 no se usa.

---

## 5. Cómo se recalibra con datos reales

Cuando entra el primer dataset real (no los 45 productos demo):

1. **Correr el pipeline entero** y después el harness. Los techos se recalculan
   solos: dependen de la config y de los datos, no hay constantes escritas a
   mano en `calibration.py`.
2. **Arreglar primero los `UNREACHABLE`**, en este orden:
   * si es un `gate` ⇒ el problema casi siempre es de **datos**, no de umbral
     (falta una sub-señal). Bajar el gate admite evidencia más pobre; conseguir
     la señal es mejor. El harness lo dice explícitamente.
   * si es un `discriminator` ⇒ mover el umbral al percentil sugerido.
   * si es una `band` ⇒ mover **todo el grupo**, nunca un corte suelto: la
     etiqueta tiene que quedar monótona.
3. **Recalibrar las bandas de severidad** con la propuesta de la sección 4 del
   reporte: percentiles p90 / p75 / p50 sobre la distribución observada, de modo
   que CRITICAL ≈ 10%, HIGH ≈ 15%, MEDIUM ≈ 25%, LOW ≈ 50%. Los cortes se
   proponen **sólo** cuando alguna banda quedó vacía; si la banda vacía es
   estructural (como LOW de confianza, § 4.2) el harness no propone nada porque
   ningún número lo arregla.
4. **Revisar los `TRIVIAL` de tipo discriminador**: un umbral que hoy no filtra
   nada probablemente estaba calibrado contra otro universo de productos.
5. **Barrer los parámetros sensibles** antes de fijarlos (§ 6).
6. **Editar `weights.yaml` a mano** con el snippet sugerido, volver a correr el
   pipeline y el harness, y confirmar que los `✗` desaparecieron.

El harness **nunca escribe la config**: emite el YAML para copiar y pegar y la
decisión la toma una persona. Un umbral es una decisión de negocio con una
justificación estadística, no al revés.

---

## 6. Sensibilidad

```python
from app.calibration import sensitivity
sensitivity("competitive_match.evidence_shrinkage.prior", [0.2, 0.35, 0.5])
```

```bash
python -m app.calibration --sensitivity competitive_match.evidence_shrinkage.prior \
                          --values 0.2,0.35,0.5
```

Cada valor se evalúa recalculando matching + oportunidades + retail media sobre
una **copia** de la base (la base real nunca se toca) y devuelve conteos,
distribución de severidad, correlación de Spearman del ranking de pares y
solapamiento del top-10 contra el primer valor del barrido. La config original
se restaura **siempre**, incluso si una corrida falla — hay un test que lo
verifica forzando una excepción.

Ejemplo real sobre la demo:

| prior | matches | score p50 | oportunidades | premiumización | spearman |
|---|---|---|---|---|---|
| 0.20 | 140 | 45.4 | 61 | 1 | 1.000 |
| 0.35 (actual) | 150 | 49.9 | 60 | 1 | 0.991 |
| 0.50 | 150 | 55.1 | 64 | 5 | 0.971 |

Se lee así: el `prior` **corre toda la escala de match hacia arriba** (p50 de
45→55) sin reordenar mucho el ranking (spearman 0.97), pero como
`premiumization.min_match_score` es un corte absoluto sobre esa escala, la regla
pasa de 1 a 5 oportunidades. Es exactamente el acoplamiento que produjo el bug
histórico: **mover un parámetro de la escala descalibra todos los umbrales
absolutos que viven sobre ella.**

---

## 7. Qué cubre el harness

41 umbrales de `weights.yaml`: los 7 del matching, los 4 de business importance,
los 7 de retail media, los 21 de las 12 reglas de oportunidades y los 2 de brand
intelligence. Las métricas se recalculan de la base, y dos de ellas **sin
censurar**:

* `match_score_all_pairs` — todos los pares evaluados, no sólo los que superaron
  `min_score_to_persist`. Juzgar ese umbral contra su propia salida sería
  circular.
* `retail_media_score` — todos los tripletes, no sólo los que superaron
  `min_score_to_report`.

### Agregar un umbral nuevo

1. Agregar un `ThresholdSpec` a `THRESHOLDS` con su métrica, dirección
   (`min`/`max`), tipo y qué decide.
2. Si la métrica no existe, calcularla en `collect()` / `_collect_business_metrics()`
   con `_add(...)`, declarando unidad y fuente.
3. Si tiene cota analítica, escribirla en `_analytic_bounds()` **con su
   justificación en el `analytic_note`**: es lo que después se imprime y lo que
   distingue "no llega" de "no puede llegar".
4. Si el umbral pertenece a una regla, agregar la regla a `RULE_INPUTS` para que
   `rule_yield_report` pueda distinguir "rota" de "sin señal".

---

## 8. Hallazgos abiertos sobre la config actual

Estado al momento de escribir esto (dataset demo de 45 productos, 150 matches,
60 oportunidades):

| Umbral | Status | Detalle |
|---|---|---|
| `business_importance.severity_thresholds.critical = 62` | `UNREACHABLE` | Máximo observado 56.1 (techo analítico 74.6). CRITICAL sigue vacío: la severidad discrimina en 3 bandas, no en 4. Sugerido: 53.0 / 44.9 / 32.7. |
| `competitive_match.visual.min_evidence_weight = 0.40` | `UNREACHABLE` (analítica) | El factor visual está apagado en los 450 pares. Decisión de datos: CLIP o atributos visuales completos. |
| `brand_intelligence.confidence.high_min_volume = 80` | `TRIVIAL` | Los 13 insights son HIGH: la confianza no informa. |
| `competitive_match.confidence_thresholds.medium = 0.45` | `TRIVIAL` (analítica) | LOW inalcanzable con `C_min = 0.60`. Estructural: no hay número que lo arregle. |
| `full_price_opportunity.min_nike_discount_pct = 10` | `TRIVIAL` | Los 15 productos Nike descuentan ≥ 12%. |
| `promotional_pressure.min_competitors_on_markdown = 2` | `TRIVIAL` | Todos los pools tienen ≥ 4 competidores en markdown: la regla dispara para los 15 productos. |
| `business_importance.gate_floor = 0.35` | `TRIVIAL` (escala) | Ningún match persistido baja de 0.38: el piso sólo actúa cuando **no hay** relevancia medible. Correcto, pero conviene saberlo. |
