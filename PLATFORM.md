# Competitive & Consumer Intelligence Decision Engine

Plataforma que convierte datos de retailers, sitios de marca, reviews, medios
especializados y señales sociales públicas en **decisiones comerciales
explicables** para Nike Argentina.

El scraping es sólo la capa de adquisición. El producto es lo que viene después.

---

## 1. Las cinco preguntas

Toda la arquitectura y la UI están organizadas alrededor de este flujo:

| # | Pregunta | Capa que la responde |
|---|---|---|
| 1 | **What is happening?** | Observaciones + Market Signals |
| 2 | **Who is actually competing?** | Competitive Matching Engine |
| 3 | **Does it matter?** | Business Importance Score |
| 4 | **Why?** | Feature importance por factor |
| 5 | **What should we do?** | Opportunity + Retail Media Engine |

Si una pantalla no aporta a una de estas cinco, no va en el MVP.

---

## 2. Arquitectura

```
   ADQUISICIÓN            NORMALIZACIÓN          INTELIGENCIA           DECISIÓN
┌────────────────┐   ┌───────────────────┐  ┌──────────────────┐  ┌────────────────┐
│ scrapers       │   │ enrichment        │  │ matching engine  │  │ opportunity    │
│ retailers      │──▶│  · normalize name │─▶│  7 factores      │─▶│ engine         │
│ marcas         │   │  · taxonomía      │  │  → match score   │  │  12 reglas     │
│ reviews        │   │  · use case       │  │  → factores      │  │                │
│ editorial      │   │  · price band     │  ├──────────────────┤  ├────────────────┤
│ social (agreg.)│   │  · lifecycle      │  │ brand intel AR   │  │ retail media   │
└────────────────┘   │  · atributos EAV  │  │  → insights      │  │ engine         │
                     └───────────────────┘  │  → momentum      │  │  5 casos       │
                              │              └──────────────────┘  └────────────────┘
                              ▼                       │                    │
                     ┌────────────────────────────────┴────────────────────┘
                     │  SQLite (MVP) · portable a Postgres
                     └────────────────────────┬───────────────────────────
                                              ▼
                              FastAPI  ──▶  Next.js (web/)
```

**Decisión clave: los servicios escriben, la API sólo lee.** Los routers hacen
SQL directo contra las tablas, no llaman a los servicios. Consecuencia práctica:
si una etapa del pipeline falla o todavía no corrió, la API sigue respondiendo y
el frontend muestra estados vacíos en vez de romperse. Verificado: los 11
endpoints devuelven 200 contra una base vacía.

**Decisión clave: nada de LLMs cloud.** Restricción dura del proyecto. Todo es
lógica determinística + scikit-learn, con modelos locales *opcionales*
(sentence-transformers, CLIP). El camino por defecto no descarga nada ni sale a
la red: TF-IDF para texto, comparación de atributos para imagen. Si el modelo
local está instalado, se usa y se informa cuál.

---

## 3. Modelo de datos

17 tablas en 5 capas (`backend/app/schema.sql`).

**Referencia** — `brands`, `countries`, `retailers`
`retailers.importance` (0..1) es el peso estratégico del canal: alimenta
Business Importance y Retail Media. No todos los retailers valen lo mismo.

**Catálogo** — `products`, `product_attributes`, `product_images`
`products` tiene los campos densos (identificación, taxonomía, precio de
referencia). `product_attributes` es **EAV a propósito**: los atributos físicos,
visuales y de performance son esparsos y van a crecer. Un atributo nuevo no
requiere migración, y cada uno guarda su `confidence` y su `source`
(scraper/rules/model/manual) — así se distingue lo observado de lo inferido.

**Observaciones** — `price_observations`, `stock_observations`, `reviews`,
`editorial_mentions`, `social_mention_aggregates`
Series temporales, nunca sobrescritas. De acá salen histórico de precios,
markdown, disponibilidad y momentum.
`social_mention_aggregates` es **siempre agregado**: períodos, conteos y
sentimiento. No se modelan individuos. Sólo información pública y agregada.

**Inteligencia** — `competitive_matches`, `competitive_match_factors`,
`market_signals`, `brand_insights`
`competitive_match_factors` guarda **una fila por factor** con su `raw_score`,
`weight`, `contribution` y `available`. Esa tabla *es* la explicabilidad: sin
ella el score sería una caja negra.

**Decisión** — `opportunities`, `recommendations`, `retail_media_opportunities`
Cada fila lleva `drivers` en JSON y `confidence`. Una recomendación sin drivers
no se persiste.

### Relaciones

```
brands ──< products >── countries
              │
              ├──< product_attributes        (EAV, atributos esparsos)
              ├──< product_images            (embedding BLOB opcional)
              ├──< price_observations >── retailers
              ├──< stock_observations >── retailers
              ├──< reviews
              ├──< editorial_mentions        (par A/B + list_key)
              └──< social_mention_aggregates (product_id + co_product_id)

products (Nike) ──< competitive_matches >── products (competidor)
                          └──< competitive_match_factors   ← explicabilidad

opportunities ──< recommendations
opportunities >── products, retailers
```

---

## 4. Fórmulas de scoring

Todos los pesos viven en `backend/config/weights.yaml`. **Ninguna función
hardcodea un peso.**

### 4.1 Regla transversal: los datos faltantes no penalizan

```
score = escala × Σ(wᵢ · sᵢ) / Σ(wᵢ)      para i ∈ factores CON datos
cobertura = Σ(wᵢ disponibles) / Σ(w total)
confianza = HIGH si cobertura ≥ 0.70 · MEDIUM si ≥ 0.45 · LOW si no
```

Un factor sin datos se marca `available=false`, se excluye y se renormaliza. La
alternativa —asumir 0— castigaría a los productos con menos datos, que es
exactamente al revés de lo que uno quiere. El costo de no tener datos se paga en
la **confianza**, no en el score. Implementado una sola vez en
`services/common.combine()`; todo score compuesto pasa por ahí.

### 4.2 Competitive Match Score (0..100)

Siete factores. Pesos por defecto:

| Factor | Peso | Qué mide |
|---|---|---|
| `semantic` | 0.25 | use case, categoría, sport, tecnología, descripción |
| `visual` | 0.15 | silueta, forma, colores, materiales (CLIP o atributos) |
| `price` | 0.15 | MSRP, precio actual, price band, comportamiento promocional |
| `editorial` | 0.15 | "X vs Y", "alternativa a X", misma lista, rankings |
| `retailer_overlap` | 0.10 | Jaccard de los retailers donde se vende cada uno |
| `social` | 0.10 | co-menciones públicas agregadas del par |
| `reviews` | 0.10 | atributos valorados en común + cercanía de rating |

Dos productos pueden verse distintos y competir por el mismo consumidor: por eso
`semantic` pesa más que `visual`, y por eso existen `editorial` y `social` — si
los medios y los consumidores los comparan, compiten, sin importar la silueta.

Las señales que saturan (editorial, social) usan `1 − e^(−puntos/k)`: la décima
mención vale mucho menos que la primera. Las menciones se ponderan por recencia
con vida media configurable.

**Explicabilidad**: `contribution` = `wᵢ·sᵢ / Σ(w·s) × 100`. Suma 100 entre los
factores disponibles y es exactamente lo que dibuja el frontend.

### 4.3 Business Importance Score (0..100)

Separa *"hay una diferencia"* de *"esta diferencia importa"*. Sin esto la
plataforma escupe miles de gaps irrelevantes.

```
importance = gate × lifecycle_mult × 100 × Σ(wᵢ·sᵢ)/Σ(wᵢ)
gate       = clamp(relevancia_competitiva, 0.35, 1.0)
```

11 componentes: relevancia competitiva, importancia de franquicia, revenue proxy,
importancia de retailer, cobertura de mercado, gap de precio, volumen de reviews,
momentum social/editorial, share of shelf e intensidad promocional.

El `gate` es **multiplicativo, no aditivo**: un gap contra un producto que no
compite de verdad queda apagado por más alto que puntúe en todo lo demás.
Severidad: CRITICAL ≥78 · HIGH ≥60 · MEDIUM ≥40.

### 4.4 Retail Media Opportunity Score (0..100)

Responde: **¿conviene más visibilidad o más descuento?**

Pesos: stock Nike 0.20 · competitividad de precio 0.20 · relevancia competitiva
0.15 · business importance 0.15 · momentum del competidor 0.12 · shelf gap 0.10 ·
quiebre del competidor 0.08.

Cinco casos, con umbrales configurables:

| Situación | Recomendación |
|---|---|
| Nike con stock y precio competitivo, competidor con momentum | `INVEST_IN_RETAIL_MEDIA` |
| Nike con stock pero precio muy por encima | `EVALUATE_PRICE_ACTION_BEFORE_MEDIA` |
| Nike con poco stock y alta demanda | `DO_NOT_INCREASE_MEDIA` |
| Competidor en quiebre, Nike con stock | `CAPTURE_COMPETITOR_STOCKOUT` |
| Nike con mucho stock, ya competitivo, bajo share of shelf | `PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN` |

El último es el caso que justifica el módulo: en vez de financiar otro markdown
compartido con el retailer, reasignar parte de esa inversión a visibilidad.

### 4.5 Brand & Consumer Intelligence (Argentina)

`Brand Momentum = f(volumen, tendencia, aceleración)` — no sólo volumen: importa
la derivada. Se detectan picos, tópicos emergentes, sentimiento negativo
creciente y competidores emergentes.

**Ningún insight se inventa.** Se generan desde plantillas parametrizadas por
métricas reales. Si la señal no alcanza el volumen mínimo, el insight no se
emite. Cada uno guarda `signal_volume`, `trend`, `direction`, `confidence` y
`evidence` (fuentes + ejemplos públicos). Sin evidencia no se persiste.

### 4.6 El puente entre ambos mundos

Es la parte más interesante y va en las dos direcciones:

- Las co-menciones AR de un par de productos suben su `social_competition_score`
  → *"los consumidores comparan cada vez más Pegasus con Novablast"* cambia
  quién compite con quién.
- Alta conversación sobre un segmento + bajo assortment Nike ahí → **assortment
  opportunity**.
- Competidor ganando momentum + Nike con stock y precio competitivo → **retail
  media opportunity**.

---

## 5. Estructura del repositorio

```
backend/
  app/
    schema.sql              modelo de datos (fuente de verdad)
    config.py  db.py        configuración y acceso a SQLite
    pipeline.py             orquestador, tolerante a etapas ausentes
    seed.py                 carga del dataset demo
    services/
      common.py             combine() — explicabilidad unificada
      embeddings.py         vectores locales (ST/TF-IDF, CLIP/atributos)
      enrichment.py         catalogación y atributos
      matching.py           motor competitivo (7 factores)
      scoring.py            business importance
      opportunities.py      12 reglas
      retail_media.py       5 casos
      brand_intelligence.py insights AR
    api/routers/            overview · products · matches · opportunities
                            retail_media · brand
  config/weights.yaml       TODO el scoring, editable sin tocar código
  data/{raw,processed,sample}
  tests/
web/                        dashboard único — 10 solapas en una sola app
  src/app/(dashboard)/        RETAIL & PRICING (4) + intelligence/ (6)
  src/app/api/intelligence/   proxy al backend FastAPI
db/  scraper/               carga a Postgres y adapters de scraping existentes
```

Hubo un `frontend/` separado mientras se construía la plataforma, para no
acoplarlo al dashboard en producción. Una vez estable se unificó en `web/`:
mantener dos apps Next.js con la misma capa de presentación era pedir que se
desincronizaran.

---

## 6. Cómo correrlo

```bash
# Backend
cd backend
pip install -r requirements.txt
python -m app.pipeline                        # construye y puebla la base
uvicorn app.main:app --reload --port 8000     # http://localhost:8000/docs

# Frontend (las 10 solapas)
cd web
npm install && npm run dev                    # http://localhost:3000
```

Para ajustar el criterio del motor: editá `backend/config/weights.yaml` y
recalculá con `python -m app.pipeline`. No hay que tocar código.
`GET /api/config` devuelve los pesos vigentes — el frontend los muestra junto a
los scores, porque entender con qué criterio se calculó un número es parte del
producto.
