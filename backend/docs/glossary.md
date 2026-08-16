<!-- GENERADO POR `python -m app.api.glossary` — NO EDITAR A MANO. -->
<!-- La fuente de verdad es `backend/app/api/glossary.py`. -->

# Glosario de factores y drivers

Qué mide cada variable que el motor publica con su contribución al score.

Es la MISMA definición que devuelve la API (`glossary` en `GET /api/matches/{id}`,
`GET /api/products/{id}/matches` y `GET /api/retail-media`) y que la UI muestra en
el `InfoTip` de cada factor: si cambia acá, cambia en las tres puntas.

Regla de lectura común a las tres familias: **todos los factores viven en 0..1**
y su `contribution` es el porcentaje del score que aportaron. Un factor sin
datos no puntúa 0: se excluye y su peso se reparte entre los demás, por eso la
cobertura y la confianza se publican aparte.

---

## Factores del match competitivo

Cuánto compiten de verdad dos productos. El score 0..100 es la suma ponderada de estos 7 factores; los que no tienen datos se excluyen y su peso se reparte entre el resto (por eso la cobertura se publica aparte).

Clave de config: `competitive_match.weights`.

### `visual` — Visual · peso 0.15

**Qué mide.** Qué tan parecidos se ven los dos productos en la foto y en sus atributos de diseño.

**Con qué datos.** Embedding de imagen (CLIP local, si hay fotos) más silueta, colores y materiales del enriquecimiento. Si sólo hay evidencia por debajo de `min_evidence_weight`, el factor se marca sin datos en vez de inventar un 0.

**Cómo leerlo.** Alto = el shopper los ve como el mismo tipo de zapatilla; se confunden en la góndola. Bajo = siluetas o materiales distintos, aunque compartan uso.

### `semantic` — Semántico · peso 0.25

**Qué mide.** Qué tan parecido es el PROPÓSITO declarado de los dos productos.

**Con qué datos.** Taxonomía ponderada (use case, categoría deportiva, división, performance vs lifestyle, género, subcategoría) más similitud de texto de las descripciones con embeddings locales o TF-IDF. Si no comparten género o categoría se aplica una penalización dura.

**Cómo leerlo.** Alto = resuelven la misma necesidad para el mismo consumidor (dos daily trainers de running de hombre). Bajo = distinto uso o distinto público; comparables sólo de nombre.

### `price` — Precio · peso 0.15

**Qué mide.** Qué tan cerca están en posicionamiento de precio, no en promoción puntual.

**Con qué datos.** MSRP, precio vigente promedio y banda de precio (entry/mid/premium/super-premium), con una tolerancia de gap declarada en config.

**Cómo leerlo.** Alto = juegan en el mismo bolsillo; el consumidor los evalúa como alternativas reales. Bajo = están en ligas de precio distintas y rara vez se comparan al momento de comprar.

### `retailer_overlap` — Solape de retailers · peso 0.1

**Qué mide.** Qué proporción de retailers venden ambos productos.

**Con qué datos.** Jaccard sobre los canales donde cada uno fue observado (precio o stock): compartidos sobre la unión.

**Cómo leerlo.** Alto = compiten en la misma góndola y el consumidor los ve juntos. Bajo = casi no coinciden en el mismo canal; la competencia es de categoría, no de góndola.

### `editorial` — Editorial · peso 0.15

**Qué mide.** Cuántas veces el mercado —medios, guías de compra, reviews— los trata como alternativas uno del otro.

**Con qué datos.** Menciones que enfrentan o listan juntos al par, puntuadas por tipo (versus > alternativa > misma lista > ranking > review), saturadas con una exponencial y descontadas por antigüedad.

**Cómo leerlo.** Alto = rivalidad documentada afuera: alguien ya escribió 'X o Y'. Bajo = nadie los compara públicamente; la rivalidad es una hipótesis nuestra.

### `social` — Social · peso 0.1

**Qué mide.** Cuánto aparecen juntos en la conversación pública.

**Con qué datos.** Co-menciones agregadas por período (nunca posteos individuales), saturadas y descontadas por antigüedad, con un mínimo de co-menciones para que la señal cuente.

**Cómo leerlo.** Alto = el consumidor los nombra en la misma frase: son sustitutos en su cabeza. Bajo = no conviven en la conversación.

### `reviews` — Reviews · peso 0.1

**Qué mide.** Si los consumidores valoran los mismos atributos y con qué nivel de satisfacción.

**Con qué datos.** Jaccard de los atributos que aparecen en las reviews de cada producto más la cercanía de rating promedio. Requiere un mínimo de reviews en ambos lados.

**Cómo leerlo.** Alto = se elogian y se critican por lo mismo (amortiguación, calce, durabilidad) con satisfacción parecida. Bajo = el consumidor valora cosas distintas en cada uno, o uno rinde claramente distinto.

---

## Componentes de la importancia de negocio

Separa 'hay una diferencia' de 'esta diferencia IMPORTA'. Estos 11 componentes ponderan cuánta plata y cuánta marca hay en juego detrás de un caso, y un gate de relevancia competitiva apaga lo que no tiene rival real.

Clave de config: `business_importance.weights`.

### `competitive_relevance` — Relevancia competitiva · peso 0.2

**Qué mide.** Cuán real es el competidor del caso.

**Con qué datos.** Match score del competidor principal, llevado a 0..1. Además actúa como gate: por debajo del piso de relevancia atenúa toda la importancia.

**Cómo leerlo.** Alto = el rival es un comparable de verdad; lo que pase con él afecta la venta. Bajo = diferencia contra alguien que el consumidor no considera alternativa: importa poco.

### `franchise_importance` — Peso de la franquicia · peso 0.12

**Qué mide.** Cuánto pesa la franquicia Nike involucrada en la estrategia de la marca.

**Con qué datos.** Mapa declarado en config (`business_importance.franchise_importance`): Pegasus, Air Force 1 y Jordan arriba; las no listadas usan el default.

**Cómo leerlo.** Alto = franquicia insignia; un problema acá se ve en el negocio y en la marca. Bajo = franquicia secundaria o sin clasificar.

### `revenue_proxy` — Proxy de facturación · peso 0.12

**Qué mide.** Cuánta plata hay detrás del producto, sin tener datos de venta.

**Con qué datos.** Precio promedio × cantidad de retailers donde está × disponibilidad observada, normalizado contra el máximo del corpus (sin constantes mágicas).

**Cómo leerlo.** Alto = producto caro y muy distribuido: mueve la aguja. Bajo = producto barato, con poca distribución o casi sin stock.

### `retailer_importance` — Importancia del retailer · peso 0.1

**Qué mide.** Cuánto pesan estratégicamente los canales involucrados en el caso.

**Con qué datos.** Promedio de `retailers.importance` (0..1) de los retailers del caso.

**Cómo leerlo.** Alto = pasa en cuentas clave, donde una decisión tiene consecuencias comerciales. Bajo = canales marginales.

### `market_coverage` — Cobertura de mercado · peso 0.08

**Qué mide.** En qué proporción de los retailers del país está presente el producto.

**Con qué datos.** Retailers donde fue observado sobre el total de retailers cargados.

**Cómo leerlo.** Alto = está en casi toda la plaza: lo que pase se replica en todos lados. Bajo = presencia acotada; el impacto queda contenido.

### `price_gap` — Gap de precio · peso 0.1

**Qué mide.** Magnitud de la diferencia de precio contra el competidor, sin importar el signo.

**Con qué datos.** Valor absoluto del gap porcentual contra el competidor del caso, dividido por 100.

**Cómo leerlo.** Alto = la brecha de precio es grande; hay algo que explicar o que corregir. Bajo = precios prácticamente iguales: el precio no es la palanca.

### `review_volume` — Volumen de reviews · peso 0.08

**Qué mide.** Cuánta evidencia de consumidor real existe sobre el producto.

**Con qué datos.** Cantidad total de reviews observadas, normalizada contra el máximo del corpus.

**Cómo leerlo.** Alto = producto con tracción y mucha voz del consumidor. Bajo = producto nuevo o de nicho; hay poco que leer.

### `social_momentum` — Momentum social · peso 0.08

**Qué mide.** Si la conversación sobre el producto está creciendo.

**Con qué datos.** Señal de momentum 0..1 de `market_signals`; si no está, se deriva comparando las menciones del último período contra el anterior.

**Cómo leerlo.** Alto = está caliente; la demanda tiende a acompañar. Bajo = conversación estable o en baja.

### `editorial_momentum` — Momentum editorial · peso 0.05

**Qué mide.** Cuánta presencia reciente tiene el producto en medios y guías.

**Con qué datos.** Menciones editoriales del producto descontadas por antigüedad, normalizadas contra el máximo del corpus.

**Cómo leerlo.** Alto = los medios lo están empujando; hay tracción prestada. Bajo = nadie lo está cubriendo.

### `share_of_shelf` — Share of shelf · peso 0.04

**Qué mide.** Cuánta presión competitiva hay en el segmento por falta de presencia Nike. Está INVERTIDO a propósito.

**Con qué datos.** 1 − (SKUs Nike / SKUs totales) del segmento del producto.

**Cómo leerlo.** Alto = Nike ocupa poca góndola frente a los competidores: hay presión y hay lugar para ganar. Bajo = Nike ya domina el segmento; el caso importa menos.

### `promo_intensity` — Intensidad promocional · peso 0.03

**Qué mide.** Cuán descontado está el producto Nike.

**Con qué datos.** Descuento porcentual promedio observado en los retailers, dividido por 100.

**Cómo leerlo.** Alto = ya se está resignando margen: cualquier decisión adicional se toma sobre un producto castigado. Bajo = producto a precio pleno.

---

## Factores de la oportunidad de retail media

Cuándo conviene invertir en visibilidad en vez de descuento, por cuadro (producto Nike × retailer). Las señales del set competidor se combinan ponderando por relevancia, salvo el precio, que va a peor caso.

Clave de config: `retail_media.weights`.

### `nike_stock_health` — Salud de stock Nike · peso 0.2

**Qué mide.** Si hay inventario para sostener el tráfico que la inversión en visibilidad va a generar.

**Con qué datos.** Disponibilidad observada del producto Nike en ese retailer (talles disponibles sobre el total); si no hay dato del retailer, el promedio de sus canales.

**Cómo leerlo.** Alto = hay con qué responder: pautar convierte. Bajo = pautar manda tráfico a una ficha sin talles; primero se repone.

### `price_competitiveness` — Competitividad de precio · peso 0.2

**Qué mide.** Si Nike ya está en precio frente al set competidor del cuadro.

**Con qué datos.** Gap de precio contra el PEOR CASO entre los comparables decisivos (los que superan el piso de match score), interpolado entre `price_competitive_pct` y `price_disadvantage_pct`.

**Cómo leerlo.** Alto = ningún comparable relevante está claramente más barato: la visibilidad rinde más que un descuento. Bajo = hay al menos un comparable visiblemente más barato en la misma góndola; pautar sobre eso quema inversión.

### `competitive_relevance` — Relevancia competitiva · peso 0.15

**Qué mide.** Cuán real es la competencia en ese cuadro.

**Con qué datos.** Match score del competidor LÍDER (el más relevante del cuadro), llevado a 0..1. Se usa el máximo y no el promedio para no castigar haber documentado una cola larga de rivales flojos.

**Cómo leerlo.** Alto = hay un comparable fuerte disputando la misma venta. Bajo = nadie comparable enfrente; la visibilidad no se está defendiendo de nada.

### `business_importance` — Importancia de negocio · peso 0.15

**Qué mide.** Cuánto importa el producto Nike de este cuadro, más allá del caso puntual.

**Con qué datos.** Score de business importance (0..100) calculado con el competidor líder y el gap combinado del cuadro. Ver la familia de componentes de importancia de negocio.

**Cómo leerlo.** Alto = producto relevante: vale la pena pelear su visibilidad. Bajo = producto marginal; hay mejores destinos para el presupuesto.

### `competitor_momentum` — Momentum del competidor · peso 0.12

**Qué mide.** Qué tan caliente está el set competidor del cuadro.

**Con qué datos.** Momentum (social, editorial y reviews) del comparable MÁS acelerado entre los decisivos del cuadro — alcanza con que uno relevante despegue para que la categoría esté en movimiento. Si ninguno supera el piso de match score, promedio ponderado por relevancia.

**Cómo leerlo.** Alto = hay un rival comparable ganando conversación: si Nike no aparece, la demanda se la llevan ellos. Bajo = categoría tranquila; menos urgencia por comprar visibilidad.

### `shelf_gap` — Brecha de share of shelf · peso 0.1

**Qué mide.** Cuánta góndola le falta a Nike en el segmento. Está INVERTIDO a propósito.

**Con qué datos.** 1 − share of shelf Nike (SKUs Nike sobre SKUs totales del segmento).

**Cómo leerlo.** Alto = Nike está poco expuesto frente a los competidores: el problema es exposición, y ahí la visibilidad paga. Bajo = Nike ya ocupa la góndola; sumar media agrega poco.

### `competitor_stock_gap` — Quiebre del competidor · peso 0.08

**Qué mide.** Cuánto inventario le falta al set competidor, es decir cuánta demanda queda sin atender.

**Con qué datos.** 1 − disponibilidad del set, con la disponibilidad ponderada por relevancia. Se exige que la góndola esté corta como conjunto: un rival de cinco sin stock no es una ventana.

**Cómo leerlo.** Alto = los rivales están quebrados y Nike disponible: ventana corta para capturar demanda sin resignar precio. Bajo = todos con stock; hay que ganar la venta por otro lado.
