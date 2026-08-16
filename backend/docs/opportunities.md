# Opportunity Center: que la lista sirva para decidir

Cómo el motor de oportunidades evita que **un solo producto Nike acapare el
ranking**, sin perder ninguna oportunidad y sin romper el triaje ni el historial.

Módulo: `app/services/opportunities.py` · Config: `config/weights.yaml`
(`opportunities.diversity`) · Tests: `tests/test_opportunities.py`,
`tests/test_triage.py::test_el_triaje_sobrevive_a_una_corrida_completa_del_pipeline`

```bash
cd backend
python -m app.pipeline                                  # recalcula todo
python -c "from app.services.opportunities import concentration; \
           import json; print(json.dumps(concentration(), indent=1, ensure_ascii=False))"
```

---

## 1. El problema, medido

El Opportunity Center se ordena por `business_importance`. Un producto que es
genuinamente importante dispara muchas reglas a la vez, así que se queda con
toda la primera pantalla. Con el dataset demo:

| | antes | después |
|---|---|---|
| Oportunidades totales | 52 | **52** (no se pierde ninguna) |
| Productos Nike distintos | 16 | 16 |
| Producto más repetido | Pegasus 41 — 11 de 52 (21%) | Pegasus 41 — 11 de 52 (21%) |
| **Productos distintos en las primeras 10 filas** | **2** | **8** |
| **Máximo repetido en las primeras 10 filas** | **9** | **2** |
| Productos distintos en las primeras 20 filas | 6 | 14 |
| Máximo repetido en las primeras 20 filas | 11 | 3 |

Las 10 primeras filas, antes:

```
 82.28 CRITICAL Nike Pegasus 41    assortment_white_space
 80.90 CRITICAL Nike Pegasus 41    competitor_stockout_opportunity
 80.46 CRITICAL Nike Pegasus 41    promotional_pressure
 80.07 CRITICAL Nike Pegasus 41    assortment_gap
 79.83 CRITICAL Nike Pegasus 41    competitor_momentum
 78.96 CRITICAL Nike Pegasus 41    product_launch_threat
 78.11 CRITICAL Nike Pegasus 41    product_launch_threat
 77.29 HIGH     Nike Pegasus 41    distribution_gap
 75.23 HIGH     Nike Mercurial …   promotional_pressure
 73.25 HIGH     Nike Pegasus 41    price_competitiveness_risk
```

Y después:

```
 82.28 CRITICAL Nike Pegasus 41    assortment_white_space
 75.23 HIGH     Nike Mercurial …   promotional_pressure
 72.59 HIGH     Nike Vomero 18     clearance_needed
 68.99 HIGH     Nike Dunk Low      promotional_pressure
 65.03 HIGH     Nike Jordan 1 Low  distribution_gap
 60.68 CRITICAL Nike Pegasus 41    competitor_stockout_opportunity
 60.29 HIGH     Nike Structure 25  promotional_pressure
 56.25 MEDIUM   Nike Air Force 1   promotional_pressure
 54.38 HIGH     Nike Vomero 18     promotional_pressure
 49.97 MEDIUM   Nike Air Max 270   promotional_pressure
```

> El número que importa **no** es "cuántas oportunidades tiene el producto top" —
> ésas son reales y no hay que borrarlas — sino **cuántos productos distintos
> entra a ver el ejecutivo antes de scrollear**. Eso lo mide
> `opportunities.concentration()`.

## 2. La decisión de diseño: agrupar, no descartar

Se evaluaron tres caminos:

| Opción | Por qué NO / SÍ |
|---|---|
| **Top-N por producto** (quedarse con las N más graves de cada uno) | Descarta oportunidades reales. La 5ª de la Pegasus puede ser la única `clearance_needed` del trimestre. Además hace desaparecer su `entity_key`: el triaje y el historial que el equipo cargó sobre esa fila quedan huérfanos. |
| **Fusionar las filas del mismo SKU en una** (lo que había) | Pierde filas (52 → 50) y con ellas su identidad; los datos de las fusionadas quedaban concatenados en un texto, no filtrables ni direccionables. Tampoco cruzaba reglas: agrupaba dentro de cada regla, y la concentración es *entre* reglas. |
| **Agrupar con identidad de grupo + atenuar la repetición en el orden** ✅ | Ninguna fila se pierde ni cambia de identidad. Una tarjeta por producto con las variantes adentro, y cada variante sigue siendo una fila real, filtrable y direccionable. |

### Las tres piezas

1. **Nada se pierde.** Las 52 oportunidades se persisten con los mismos cinco
   campos de identidad de siempre. Lo único que se colapsa son los duplicados
   **exactos** (`_merge_duplicates`): dos drafts con la misma `entity_key` son,
   para el triaje, la misma oportunidad, y persistir los dos haría que el estado
   cargado se aplique a una fila sí y a la otra no según el orden de la query.
   Con los datos de hoy no dispara nunca (52 drafts → 52 claves distintas).

2. **Identidad de grupo NUEVA, que convive con la individual.** Las
   oportunidades del mismo producto Nike se atan con una `group_key`, calculada
   con la misma función de hash que `entity_key` pero con otro `kind`
   (`opportunity_group`), así que no puede colisionar con la clave de ninguna
   oportunidad. La más grave del producto es la **cabecera** y lleva la lista
   completa de sus variantes; cada variante apunta de vuelta con
   `head_entity_key`.

3. **La lista plana también se vuelve legible.** `/api/opportunities` ordena por
   `business_importance`, así que mientras la UI no agrupe nativo la única
   palanca sobre el orden es esa columna: la k-ésima oportunidad de un mismo
   producto entra con la prioridad atenuada. La cabecera nunca se toca.

```
factor(k) = 1.0                                        si k < full_weight_per_group
          = max(min_factor, repeat_decay ** (k - full_weight_per_group + 1))   si no

business_importance persistida = business_importance real × factor(k)
```

`repeat_decay` es una afirmación sobre utilidad marginal: *la segunda
oportunidad de un producto que ya estoy mirando vale 75% de lo que valdría
sola*. `min_factor` es el piso: una variante baja pero **nunca desaparece** —
conserva al menos el 35% de su importancia real, así que sigue por encima de
cualquier oportunidad genuinamente menor.

### Lo que explícitamente NO se hace: bajar la severidad

`severity` se calcula siempre sobre la importancia **base**, nunca sobre el
score atenuado. Qué tan grave es una oportunidad es una propiedad suya, no de su
posición en la lista: si se derivara del score atenuado, una oportunidad
`CRITICAL` se "curaría" sola porque otra del mismo producto la superó en el
ranking, y el filtro por severidad dejaría de encontrar la variante crítica que
quedó decimoquinta. En la corrida demo son 7 filas `CRITICAL`: la cabecera
(posición 1) y seis variantes de la Pegasus repartidas por las posiciones 6, 12,
24, 33, 34 y 35 — todas siguen apareciendo con el filtro `severity=CRITICAL`, que
es exactamente cómo un ejecutivo llega a la número 35.

## 3. Cómo se preserva `entity_key`

`entity_key = sha1("opportunity|opportunity_type=…|nike_product_id=…|competitor_product_id=…|retailer_id=…|country_code=…")[:16]`
(ver `app/services/history.py`). Es lo que ata el triaje (`opportunity_triage`) y
el historial (`opportunity_history`) a través de los recálculos.

La agrupación **no toca ninguno de esos cinco campos**:

* cada draft se persiste tal cual lo devolvió su regla — mismo tipo, mismos
  productos, mismo retailer, mismo país;
* la clave se calcula delegando en `app.services.triage.entity_key`, que a su
  vez delega en `history`: acá **no se reimplementa la fórmula**;
* la `group_key` es una clave **adicional**, con `kind` propio; vive en
  `drivers`, no reemplaza nada;
* si dos drafts colisionaran en `entity_key`, `_merge_duplicates` los une en vez
  de persistir dos filas indistinguibles — que es justo lo que rompería el
  triaje.

Verificación (además de los tests):

```
corrida 1 → 52 oportunidades, se marcan 3 (cabecera, variante, variante más degradada)
corrida 2 → 52 oportunidades; 52 de 52 entity_key reaparecen idénticas
            dismissed / snoozed / resolved sobreviven, con assignee y nota
```

**Caveat honesto:** con `rank_penalty` activo, lo que `opportunity_history`
snapshotea es la importancia **de ranking**, no la base. Si el conjunto de
oportunidades de un producto cambia entre corridas, la serie de una variante se
mueve por eso y no sólo por el negocio. La importancia real queda siempre en
`drivers.opportunity_group.detail.base_importance`, y cuando la UI agrupe nativo
se apaga la atenuación (`rank_penalty: false`) y la columna vuelve a ser
importancia pura, con las tarjetas intactas.

## 4. Qué publica el motor para la UI

En `drivers` (JSON, columna que la API publica tal cual vía
`expand_opportunities` → `canonical_drivers`) cada fila agrupada lleva:

```json
{
  "name": "opportunity_group",
  "value": 1.0,                       // = rank_factor (contrato canónico: 0..1)
  "contribution": 0.0,                // no participa del cálculo de importancia
  "detail": {
    "group_key": "6aad2e2af450261f",
    "group_axis": "nike_product",
    "group_label": "Nike Pegasus 41",
    "size": 11, "rank": 0, "role": "head",
    "entity_key": "3f46951a931c042b",
    "head_entity_key": "3f46951a931c042b",
    "base_importance": 82.28, "ranked_importance": 82.28, "rank_factor": 1.0,
    "members": [
      {"entity_key": "…", "opportunity_type": "competitor_stockout_opportunity",
       "family": "stock", "severity": "CRITICAL", "business_importance": 80.9,
       "ranked_importance": 60.68, "rank": 1, "role": "variant",
       "title": "…", "action": "CAPTURE_COMPETITOR_STOCKOUT"}
    ]
  }
}
```

* `role: "head"` → renderizar UNA tarjeta con `members` adentro.
* `role: "variant"` → plegable dentro de la tarjeta de `head_entity_key`; sigue
  siendo una fila real (`GET /api/opportunities/{id}`, filtros por tipo, familia,
  severidad y producto) y sigue teniendo su propio triaje.
* No hizo falta tocar el esquema, los serializers ni la API.

**Ejes de agrupación** (`GROUP_AXES`), en orden: `nike_product` y, para las
oportunidades sin producto (share of shelf por canal), `retailer`. Las que no
tienen ninguno de los dos quedan sueltas, sin grupo y sin atenuación.

## 5. Config

Bloque nuevo `opportunities.diversity`. **Todas las claves tienen default en
código**, así que el motor funciona sin tocar `weights.yaml`; declararlas ahí
las hace visibles y tuneables:

```yaml
opportunities:
  diversity:
    enabled:                true   # apaga toda la agrupación (ranking = importancia pura)
    repeat_decay:           0.75   # utilidad marginal de la k-ésima del mismo producto
    min_factor:             0.35   # piso: una variante nunca cae por debajo de esto
    full_weight_per_group:  1      # cuántas por producto conservan prioridad intacta
    rank_penalty:           true   # false = agrupa pero no toca `business_importance`
    screen_size:            10     # tamaño de "primera pantalla" para concentration()
```

Calibración de `repeat_decay` / `min_factor` contra el dataset demo
(productos distintos / máximo repetido en las 10 primeras filas):

| decay | piso | @10 | @20 |
|---|---|---|---|
| — (apagado) | — | 2 / 9 | 6 / 11 |
| 0.90 | 0.50 | 6 / 3 | 8 / 6 |
| **0.75** | **0.35** | **8 / 2** | **14 / 3** |
| 0.60 | 0.25 | 9 / 2 | 15 / 2 |
| 0.50 | 0.20 | 10 / 1 | 15 / 2 |

Por debajo de 0.6 la lista deja de estar ordenada por importancia y pasa a ser
un round-robin por producto: una oportunidad de 80 puntos aparece como 16 y el
ranking miente sobre lo que importa. 0.75/0.35 descongestiona la pantalla
manteniendo la lista reconocible como un ranking de importancia.

## 6. Invariantes que los tests fijan

* **Ninguna oportunidad se pierde**: drafts crudos por regla == filas
  persistidas por tipo (es también lo que audita `app.calibration`).
* **Ninguna identidad cambia**: el set de `entity_key` con agrupación es
  idéntico al de sin agrupación, y no hay dos filas con la misma clave.
* **El triaje sobrevive**: descartar una variante agrupada → recalcular → sigue
  descartada (unitario), y la corrida completa del pipeline (`test_triage.py`).
* **La cabecera lista todas sus variantes** y cada una es una fila real de la
  base; cada variante sabe volver a su cabecera.
* **La cabecera conserva su importancia** y las variantes bajan con piso.
* **La severidad no baja por la posición** en el ranking.
* **Las 13 reglas siguen disparando** (12 históricas + `clearance_needed`).
