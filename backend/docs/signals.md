# Señales del motor: qué miden y contra qué escala se leen

Referencia de las tres señales que el tablero publica como número: **momentum de
mercado**, **confianza de un brand insight** y **severidad de una oportunidad**.
Para cada una: qué mide, cómo se normaliza, **por qué esa escala** y **cómo
verificar que sigue discriminando**.

Módulos: `app/services/brand_intelligence.py` · `app/services/scoring.py` ·
Config: `config/weights.yaml` · Tests: `tests/test_brand_intelligence.py`,
`tests/test_scoring.py`

```bash
cd backend
python -m app.pipeline          # recalcula la base
python -m app.calibration       # sección 1 = distribuciones, sección 2 = alcanzabilidad
```

---

## 0. La regla que las tres comparten

> **Una señal que da siempre el mismo valor ocupa lugar en la pantalla y no
> aporta información.** Peor que no mostrarla: da falsa seguridad.

Las tres fallas que documenta este archivo eran la misma falla con tres caras:
una escala cuyo **tope o cuyo piso estaba fijado por construcción**, no por los
datos. Y las tres se detectan igual — mirando la distribución, no el código:

| Síntoma | Diagnóstico |
|---|---|
| El máximo de la escala vale siempre 100 | La normalización es contra el máximo del cohorte |
| Varias entidades empatadas en el tope | Un factor sin datos se está rellenando con 1.0 |
| Una etiqueta con una sola clase | El corte está fuera del rango real de la métrica |
| Una banda vacía para cualquier corte | Un multiplicador le puso techo a la escala |

Y hay una regla de oro que las tres respetan: **un factor que no se puede medir
se excluye y se renormaliza (baja la `coverage`), nunca se rellena con el
máximo.** Rellenar con 1.0 es afirmar lo más fuerte posible justo cuando menos
se sabe.

---

## 1. Momentum de mercado (`market_signals.value`, 0..100)

### Qué mide

Cuánta conversación pública tiene una entidad (marca, producto o franquicia) en
la ventana actual **y hacia dónde va**. Se calcula por separado para cada fuente
— `social_momentum`, `editorial_momentum`, `review_momentum` — porque las tres
tienen unidades distintas (menciones, notas, reviews).

Lo consumen: el panel "Momentum de competidores" del Executive Overview, la
regla `competitor_momentum` del opportunity engine y el factor
`competitor_momentum` del score de retail media.

### Cómo se normaliza

```
momentum = 100 × Σ(w_i · s_i) / Σ(w_i disponibles)        # common.combine
```

con tres factores (pesos en `brand_intelligence.momentum.weights`):

| Factor | Peso | 0..1 | Disponible cuando |
|---|---|---|---|
| `volume` | 0.35 | `0.5 + 0.5·log10(actual / mediana) / log10(volume_decade_ratio)` | el cohorte tiene alguna entidad con señal |
| `trend` | 0.35 | `0.5 + 0.5·clamp(variación / spike_threshold, -1, 1)` | `previo >= min_base_volume` |
| `acceleration` | 0.30 | ídem sobre la derivada segunda | `previo` **y** `anterior` >= `min_base_volume` |

Lecturas de referencia del factor `volume`: **0.5 = la mediana de su cohorte**,
1.0 = una década por encima, 0.0 = una década por debajo. Del factor `trend`:
0.5 = plano, 1.0 = creciendo al `spike_threshold` (+50%) o más, 0.0 = cayendo
otro tanto.

### Por qué esa escala

**El problema.** Antes el volumen se normalizaba contra el **máximo del
cohorte** (`volume = actual / max`) y la tendencia se rellenaba con `1.0`
("aparición nueva = crecimiento máximo") cuando no había ventana previa. Las dos
decisiones saturan:

* normalizar contra el máximo garantiza que **la entidad más grande valga
  siempre exactamente 1.0**, pase lo que pase en el mercado;
* con cohortes discretas y chicas el máximo es 1 — cada producto tenía **una**
  nota editorial —, así que **todas** valían 1.0;
* sin ventana previa, `trend` valía 1.0 y `acceleration` quedaba no disponible,
  con lo cual el score era `(0.35·1 + 0.35·1)/0.70 = 100`.

Resultado: **15 entidades con `value = 100.0` exacto** y un panel de 6 filas
empatadas arriba, ordenadas de forma arbitraria. El panel decía "estas 6 son las
que más momentum tienen" cuando el dato real era "estas 6 tienen una mención y
ninguna historia".

**La decisión.** Tres cambios, ninguno de ellos un percentil:

1. **Mediana en vez de máximo** como referencia del cohorte
   (`cohort_reference`). La mediana es el centro de masa de la distribución: no
   la fija ningún dato en particular, es robusta al outlier (la marca foco
   concentra un orden de magnitud más que la cola) y **deja recorrido hacia
   arriba**: el tope ya no está garantizado para nadie.
2. **Escala logarítmica** en vez de cociente lineal. Los volúmenes de
   conversación son log-normales: en el corpus AR la conversación social por
   marca va de 265 a 6031 menciones (~1,4 décadas). Un cociente lineal contra el
   máximo aplasta toda la cola contra 0; el log reparte masa a lo largo del rango
   y — clave — hace la escala **independiente de la unidad de cada fuente**, que
   es lo que permite que menciones, notas y reviews convivan en el mismo panel.
   `volume_decade_ratio: 10` (una década arriba/abajo de la mediana) cubre la
   dispersión observada sin saturar en los extremos.
3. **Base mínima para publicar una variación** (`min_base_volume: 25`). Con 1
   mención previa, `(835-1)/1 = +83.400%` no dice nada del mercado: dice que la
   base era 1. Por debajo del piso, `trend` y `acceleration` se marcan **no
   disponibles** y el score se renormaliza sobre lo que sí se midió. El anclaje
   es estadístico: el error relativo de Poisson de un conteo es `1/√n`, y con
   n=25 queda en ±20%, menos de la mitad del `spike_threshold` (50%) que el
   módulo usa para llamar "pico" a una variación.

**Por qué no una escala absoluta.** Requeriría una constante de saturación por
fuente (menciones ≠ notas ≠ reviews) y quedaría desactualizada apenas los
collectors cambien de volumen. La mediana del cohorte se recalibra sola. El
costo, explícito: si **todo** el mercado crece 3×, los scores no se mueven — es
una medida **relativa a la conversación del período**, y para un panel
competitivo ("¿quién está creciendo más que el resto?") eso es lo correcto. Las
magnitudes absolutas siguen publicadas en `volume` / `volume_previous`.

### Efecto medido

| | Antes | Después |
|---|---|---|
| Entidades con `value = 100.0` exacto | **15** | **0** |
| Valores distintos (n=176) | 103 | **138** |
| p25 / p50 / p75 | 45,6 / 73,7 / 87,9 | 42,1 / 50,0 / 57,3 |
| Máximo | 100,0 | 85,2 |

Top del panel "Momentum de competidores":

| Antes | Después |
|---|---|
| 6 filas con `value=100.0`, todas editoriales con **1 mención** y ventana previa vacía | 6 filas con valores distintos (85,2 · 84,1 · 81,0 · 79,9 · 78,3 · 75,8), todas con volumen real y crecimiento medido entre +26% y +62% |

Las entidades que antes lideraban (1 mención, sin historia) ahora puntúan 50,0
— la mediana de su cohorte, sin tendencia medible —, que es exactamente lo que
son: presencia normal, nada que reportar.

### Cómo verificar que sigue discriminando

1. `python -m app.calibration`, sección 1: la métrica **`competitor_momentum`**
   (el mismo score leído 0..1 por el opportunity engine) no debe tener `p95 =
   max = 1.00`, y `p25 != p75`. Hoy: `min 0.00 · p50 0.50 · max 0.85`.
2. Sección 2: `retail_media.thresholds.high_momentum` debe estar en `OK`. Si
   aparece `TRIVIAL` (lo pasa todo) la escala volvió a saturar por arriba; si
   aparece `UNREACHABLE`, se aplastó por abajo.
3. Consulta directa — **ninguna fila debería empatar en el tope**:

```sql
SELECT value, COUNT(*) FROM market_signals
 WHERE signal_type LIKE '%momentum'
 GROUP BY value HAVING COUNT(*) > 3 ORDER BY value DESC;
```

4. La propiedad que define la señal: **a igual volumen, el momentum tiene que
   separar creciendo / estable / cayendo**. Con el volumen en la mediana
   (`volume = 0.5`) y sólo `trend` disponible, el score vale `50·vol + 50·trend`:

| Variación vs. ventana anterior | `trend` | Momentum |
|---|---|---|
| −50% o peor | 0.0 | **25** |
| plano | 0.5 | **50** |
| +50% o más | 1.0 | **75** |

   Lo cubren `test_trend_and_direction_between_windows` y
   `test_momentum_score_is_bounded_and_explainable`.

### Unidades de `market_signals` (ojo al integrar)

Para una fila de momentum, **`delta` y `acceleration` van en la misma unidad:
ratio** (`0.62` = +62%), no porcentaje. La aceleración siempre fue un ratio, y
`opportunities.IntelContext.momentum()` usa `delta` como reemplazo cuando falta
la tercera ventana: mientras acá se persistió el porcentaje, ese fallback
comparaba `61.54` contra `min_acceleration: 0.35` y el motor publicaba textos
como *"acelera 2619%"* para un competidor que creció 26%. **La unidad legible
(%) se calcula al mostrar, no se persiste.** Las filas `share_of_shelf` (otro
servicio) siguen usando `delta` en puntos porcentuales.

---

## 2. Confianza de un brand insight (`brand_insights.confidence`)

### Qué mide

Con **cuánta precisión** se afirma lo que dice el insight. No mide si el insight
es interesante ni cuán grande es el problema: mide el volumen de señal que hay
detrás de la magnitud reportada.

### Cómo se normaliza

Bandas sobre `signal_volume` (menciones + reviews + notas del período), en
`brand_intelligence.confidence`:

| Banda | Corte | Margen de error de una proporción (95%) |
|---|---|---|
| `HIGH` | `signal_volume >= 1000` | ±3,1 pp |
| `MEDIUM` | `>= 250` | ±6,2 pp |
| `LOW` | por debajo | peor que ±6,2 pp |

### Por qué esa escala

Los cortes estaban en **80 / 25**, por debajo del volumen típico de un insight
(el mínimo observado es 123): **los 13 insights salían `HIGH`**. Una confianza
que dice siempre lo mismo es peor que no mostrarla.

Los cortes nuevos **no son percentiles del dataset**, son precisión estadística:
el margen de error de una proporción al 95% es `~0.98/√n`, así que n=1000 ⇒
±3 pp y n=250 ⇒ ±6 pp. Sobreviven a un cambio de corpus: si mañana entra 10× más
conversación, los insights suben de banda porque efectivamente se afirman con
más precisión, no porque se movió un percentil.

**La decisión de fondo: separar dos preguntas que estaban acopladas.** El corte
de `MEDIUM` hacía doble función — era también el piso de emisión (`ctx.min_volume`:
"por debajo de esto el insight no se emite"). Con eso, subir el corte para que la
confianza discriminara **borraba insights reales del tablero**, y bajarlo para no
perderlos dejaba las tres bandas colapsadas en `HIGH`. Son dos decisiones
distintas:

* `min_volume_to_emit: 25` — regla **anti-invención**, de producto: "no se
  concluye nada sobre tres comentarios". Se queda donde estaba.
* `medium_min_volume` / `high_min_volume` — afirmación de **precisión**. Se
  calibran contra el volumen real.

Si se borra `min_volume_to_emit`, el módulo vuelve a usar `medium_min_volume`
(comportamiento histórico).

### Efecto medido

| Banda | Antes | Después |
|---|---|---|
| `HIGH` | **13** | 4 |
| `MEDIUM` | 0 | 5 |
| `LOW` | 0 | 4 |

Sobre los mismos 13 insights: no se perdió ninguno, se les puso una etiqueta que
significa algo. Los cuatro `LOW` (volumen 123–235) siguen publicados en
`/api/brand/insights`; el Executive Overview ya filtraba por
`confidence IN ('HIGH','MEDIUM')`, así que ahora su carril de destacados
efectivamente prioriza.

### Cómo verificar que sigue discriminando

1. `python -m app.calibration`, sección 2: `brand_intelligence.confidence.*`
   deben estar en `OK`. `TRIVIAL` significa que el corte quedó por debajo del
   mínimo observado — la banda de abajo está vacía otra vez.
2. Las tres bandas con masa:

```sql
SELECT confidence, COUNT(*), MIN(signal_volume), MAX(signal_volume)
  FROM brand_insights GROUP BY confidence;
```

3. Si con datos reales **no hay volumen suficiente para llenar las tres bandas**,
   el camino correcto es **decirlo**, no mover los cortes: los cortes son una
   afirmación de precisión estadística. Que todo un corpus sea `LOW` es
   información legítima ("todavía no hay conversación suficiente"); que todo sea
   `HIGH` porque se bajó el corte, no.

---

## 3. Severidad de una oportunidad (`opportunities.severity`)

### Qué mide

Cuánta relevancia de negocio concentra una oportunidad, en 0..100:

```
business_importance = 100 × gate × lifecycle × Σ(w_i · s_i) / Σ(w_i disponibles)
```

11 factores en `business_importance.weights` (relevancia competitiva, franquicia,
revenue proxy, retailers, cobertura, gap de precio, reviews, momentum social y
editorial, share of shelf, intensidad promocional).

### Cómo se normaliza

| Banda | Corte | Lectura |
|---|---|---|
| `CRITICAL` | `>= 78` | casi todo el peso de negocio alineado |
| `HIGH` | `>= 60` | más de la mitad |
| `MEDIUM` | `>= 40` | un tercio largo |
| `LOW` | por debajo | |

Y el gate de relevancia competitiva:

```
gate = clamp(competitive_relevance / gate_full_relevance, gate_floor, 1.0)
     con gate_floor: 0.35   ·   gate_full_relevance: 0.60
```

### Por qué esa escala — la decisión sobre `CRITICAL`

**El problema.** El gate era `clamp(competitive_relevance, gate_floor, 1.0)`, es
decir una multiplicación por la relevancia **siempre**. Como
`competitive_relevance = match_score / 100` y el mejor match del corpus es 75,2,
la importancia tenía un **techo estructural de ~82** con los 11 factores en 1.0,
y el máximo observado era **56,1**. Con `critical: 62`, la banda superior era
**inalcanzable por construcción**: el motor no podía decir "esto es grave" ni
aunque lo fuera. La tarjeta "Oportunidades críticas" del Executive Overview
mostraba 0 permanentemente.

Se evaluaron tres salidas:

**(a) Aceptar que son 3 bandas y sacar `CRITICAL`.** Honesta, pero resuelve el
síntoma equivocado: el problema no es "no hay casos graves", es "la escala no
puede expresar gravedad". Además tira una distinción que el negocio sí necesita
y deja el defecto en el reporte de calibración (el harness marca el umbral
faltante como `NO_DATA` / defecto). **Descartada.**

**(c) Recalibrar los cortes a la cota analítica real (~82).** Deja el gate roto:
la escala 0..100 seguiría sin poder usarse entera, la cota se movería sola cada
vez que cambie el techo de match, y para que `CRITICAL` tuviera masa habría que
ponerlo cerca del máximo observado — que es un percentil disfrazado. **Descartada
como solución de fondo** (aunque los cortes sí se revisaron, ver abajo).

**(b) Que el gate deje de topear la escala. ← elegida.**

El gate existe para **apagar lo que no tiene competencia real** ("un gap de
precio contra nadie no importa"). Eso es un **veto de la cola baja**, no un
impuesto permanente. Multiplicar siempre hacía otras dos cosas que nadie pidió:

1. **le ponía a la escala un techo igual al mejor match del corpus** — incluso
   el caso perfecto perdía 25% del score por existir;
2. **contaba la relevancia dos veces**, porque `competitive_relevance` ya es uno
   de los 11 factores ponderados (w = 0.20).

Y encima no estaba haciendo su trabajo: `gate_floor` nunca llegaba a activarse
(el harness lo reporta `TRIVIAL`: la relevancia mínima observada, 0,37, ya está
por encima del piso), así que en la práctica era **pura atenuación lineal**.

La forma nueva es una rampa que satura: por encima de `gate_full_relevance` el
competidor ya es real y el gate deja de atenuar; por debajo, la atenuación sigue
existiendo y baja hasta el piso. `gate_full_relevance: 0.60` **no es un
percentil**: es el mismo `match_score` que el motor ya usa como "comparable
suficiente para actuar" en
`opportunities.premiumization_opportunity.min_match_score`.

**Los cortes de severidad, entonces.** Con la escala arreglada volvieron a
**78 / 60 / 40** — que son los que `common.severity_from_score` trae por defecto:
el diseño original del score. Se habían bajado a 62/50/35 para compensar una
escala topeada en ~56; arreglado el gate, la compensación sobra. Son fracciones
de la escala ("casi todo el peso alineado" / "más de la mitad" / "un tercio
largo"), **no percentiles**: una severidad definida como "el top 10%" no informa
nada — sólo re-etiqueta un ranking y tiene la misma forma pase lo que pase en el
mercado.

### Efecto medido

| | Antes | Después |
|---|---|---|
| `business_importance` p25 / p50 / p75 | 23,5 / 31,8 / 45,5 | 37,1 / 48,1 / 70,1 |
| Máximo observado | 56,1 (techo estructural ~82) | 82,1 (escala completa disponible) |
| `CRITICAL` | **0** | 6 |
| `HIGH` | 8 | 12 |
| `MEDIUM` | 20 | 16 |
| `LOW` | 33 | 18 |

La forma de pirámide (6 / 12 / 16 / 18) es la que se espera de una severidad: la
banda superior es la excepción, no un cuartil.

> El total de oportunidades bajó de 61 a 52 por un efecto colateral del punto 1:
> `competitor_momentum` pasó de 11 a 2 casos porque dejó de disparar con
> variaciones calculadas sobre bases de 1 o 2 menciones. Las 12 reglas siguen
> produciendo.

### Cómo verificar que sigue discriminando

1. `python -m app.calibration`, sección 2: `business_importance.severity_thresholds.*`
   los tres en `OK`. `UNREACHABLE` en `critical` = la escala volvió a tener techo.
2. Las cuatro bandas con masa, y `CRITICAL` como minoría:

```sql
SELECT severity, COUNT(*), ROUND(MIN(business_importance),1), ROUND(MAX(business_importance),1)
  FROM opportunities GROUP BY severity;
```

3. El test `test_la_escala_de_importancia_llega_a_la_banda_critica`
   (`tests/test_scoring.py`) fija la propiedad: con todos los factores altos y un
   competidor real, la severidad **tiene que** dar `CRITICAL`. Si ese test se
   pone rojo, alguien volvió a ponerle techo a la escala.
4. La forma del gate está fijada por `test_gate_deja_de_atenuar_con_un_competidor_real`
   y `test_gate_atenua_de_forma_monotona_por_debajo_del_umbral`.

### Deuda conocida

`app/calibration.py::_analytic_bounds` todavía modela el gate viejo
(`gate = clamp(rel, floor, 1)`) para calcular la cota analítica de
`business_importance`, y por eso informa un techo de **82,19** que hoy
**subestima** el real (con la rampa, la cota es `base_max × lifecycle_max`,
clampeada a 100). No invalida ningún veredicto — el harness usa
`min(analítica, observada)` y ningún umbral queda del lado equivocado —, pero la
fórmula del reporte hay que actualizarla. `calibration.py` es de otro dueño:
queda anotado como handoff.

---

## 4. Checklist rápido después de tocar cualquier peso

```bash
cd backend
python -m app.pipeline
python -m app.calibration        # secciones 1 y 2
python -m pytest tests/ -q
```

* Ninguna métrica de la sección 1 con `p25 == p75` o con `p95 == max` pegado al
  tope de su escala.
* Ningún `✗ UNREACHABLE` y ningún `✗ TRIVIAL` nuevo en la sección 2.
* Las 12 reglas produciendo en la sección 3.
* Y la pregunta que ninguna herramienta contesta sola: **si esta señal diera el
  mismo número para todas las filas, ¿alguien se daría cuenta?** Si la respuesta
  es no, falta un test que fije la propiedad.
