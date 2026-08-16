# API — seguridad y contrato de `drivers`

Complementa `backend/CONTRACTS.md` (que sigue siendo la spec de módulos) con lo
que se agregó del lado de la API: **autenticación opcional, rate limiting, CORS
configurable** y la **forma canónica de `drivers`**.

Levantar el motor:

```bash
cd backend
python -m app.pipeline                      # pobla la base (una vez)
uvicorn app.main:app --reload --port 8000   # sirve la API
```

---

## 1. Autenticación por API key

La API **arranca abierta** — igual que siempre — y se cierra sola en cuanto
existe la variable de entorno `CI_API_KEY`:

| `CI_API_KEY` | Comportamiento |
|---|---|
| sin definir | Sin autenticación. Se loguea una **advertencia** al arrancar. Es el modo de desarrollo y el que usan los tests. |
| definida | Todo endpoint privado exige el header `X-API-Key`. Sin él, o con una key que no coincide: **401**. |

```bash
# Desarrollo: nada que hacer.
uvicorn app.main:app --port 8000
#   WARNING  CI_API_KEY no está definida: la API queda ABIERTA, sin autenticación…

# Producción:
export CI_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app.main:app --port 8000

curl -s localhost:8000/api/opportunities            # 401
curl -s -H "X-API-Key: $CI_API_KEY" \
        localhost:8000/api/opportunities            # 200
```

`CI_API_KEY` acepta **varias keys separadas por coma** (`k_dashboard,k_notebook`):
sirve para rotar sin cortar el servicio y para revocar una sola de ellas.

### Endpoints públicos (nunca piden key)

| Ruta | Por qué |
|---|---|
| `/api/health` | Es el latido que consulta la cinta de estado del dashboard. Si pidiera key, un error de configuración se vería como "motor caído" en vez de como un 401. |
| `/docs`, `/redoc`, `/openapi.json` | Documentación. |

Se pueden agregar más con `CI_PUBLIC_PATHS` (lista separada por comas). El
resto — `/api/overview`, `/api/products`, `/api/matches`, `/api/opportunities`,
`/api/retail-media`, `/api/brand/*`, `/api/config` — queda detrás de la key.

`/api/health` publica además cómo quedó configurada la seguridad, sin filtrar la
key, para poder verificar de un vistazo que un deploy quedó cerrado:

```json
{
  "status": "ok",
  "tables": { "...": 0 },
  "security": {
    "auth_required": true,
    "api_key_header": "X-API-Key",
    "rate_limit": 120,
    "rate_limit_window_seconds": 60.0,
    "public_paths": ["/api/health", "/docs", "/redoc", "/openapi.json"]
  }
}
```

La comparación de la key usa `hmac.compare_digest` (tiempo constante) y en los
logs sólo aparece un hash corto, nunca la key.

---

## 2. Rate limiting

Ventana deslizante **en memoria del proceso**, sin dependencias nuevas (no hay
slowapi ni Redis). Bucket = `IP del cliente + identidad de la key + ámbito`.

```
HTTP/1.1 429 Too Many Requests
Retry-After: 37
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0

{"detail": "Demasiadas requests. Esperá los segundos que indica el header Retry-After.", …}
```

Las respuestas exitosas llevan `X-RateLimit-Limit` y `X-RateLimit-Remaining`.

Detalles que importan:

* **Los intentos con key inválida también consumen cuota**: el 429 llega antes
  de que una fuerza bruta pueda barrer el espacio de claves.
* **`/api/health` tiene su propio bucket** (ámbito público). Un scrapeo contra
  `/api/opportunities` no puede dejar sin respuesta al health check y hacer que
  el dashboard reporte el motor como caído.
* **Es un techo por proceso, no una cuota global.** Con varios workers de
  uvicorn cada uno lleva su propia ventana: dividí el número por la cantidad de
  workers. Alcanza para frenar un scrapeo; no es un WAF.
* **Detrás del proxy de Next todas las requests comparten IP.** El límite es
  entonces por *instancia del dashboard*, no por usuario: dimensionalo pensando
  en el tráfico agregado, o activá `CI_TRUST_FORWARDED_FOR=1` si el proxy es
  propio y setea `X-Forwarded-For` de forma confiable.

---

## 3. CORS

`CI_CORS_ORIGINS` (lista separada por comas) define qué orígenes pueden pegarle
desde un browser. Default: `http://localhost:3000,http://127.0.0.1:3000`.
`CI_CORS_METHODS` hace lo propio con los métodos (default `GET,HEAD,OPTIONS`).

**En producción el browser no debería pegarle directo a esta API.** El
dashboard consume `/api/intelligence/...` de Next, que reenvía server-side: así
la key vive sólo en el servidor y nunca llega al cliente. Si nadie le pega desde
un browser, lo correcto es dejar `CI_CORS_ORIGINS=""` (ningún origen permitido);
el proxy no se ve afectado, porque CORS es una política del browser, no del
servidor. Poner `CI_CORS_ORIGINS=*` con la key activa sería el peor de los
mundos: cualquier página podría intentar leer la API y la key terminaría en el
bundle del cliente.

El preflight `OPTIONS` nunca se autentica (no puede llevar headers propios).

---

## 4. Variables de entorno

### Backend

| Variable | Default | Qué hace |
|---|---|---|
| `CI_API_KEY` | *(vacío)* | Key(s) válidas, separadas por coma. Vacío ⇒ API abierta + warning al arrancar. |
| `CI_RATE_LIMIT` | `600` | Requests por ventana y por bucket. `0` desactiva el rate limiting. |
| `CI_RATE_LIMIT_WINDOW` | `60` | Tamaño de la ventana, en segundos. |
| `CI_TRUST_FORWARDED_FOR` | `0` | `1` ⇒ usar el primer hop de `X-Forwarded-For` como IP del cliente. Sólo detrás de un proxy propio. |
| `CI_PUBLIC_PATHS` | *(vacío)* | Rutas extra sin auth, separadas por coma. |
| `CI_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Orígenes permitidos para el browser. |
| `CI_CORS_METHODS` | `GET,HEAD,OPTIONS` | Métodos permitidos por CORS. |

Valor sugerido en producción: `CI_RATE_LIMIT=120` con `CI_RATE_LIMIT_WINDOW=60`
(el dashboard dispara del orden de 10 requests por pantalla).

### Dashboard (`web/.env.local`)

```bash
INTELLIGENCE_API_URL=http://localhost:8000
INTELLIGENCE_API_KEY=<el mismo valor que CI_API_KEY>   # ⇐ agregar al desplegar
```

`INTELLIGENCE_API_KEY` **no lleva prefijo `NEXT_PUBLIC_`**, a propósito: sólo la
leen el proxy (`web/src/app/api/intelligence/[...path]/route.ts`) y el cliente
server-side (`web/src/lib/intelligence/server.ts`), que corren en Node y mandan
el header `X-API-Key` en su `fetch`. Si no está definida, el proxy no manda el
header y todo sigue funcionando contra un backend abierto.

---

## 5. Contrato canónico de `drivers`

Todos los endpoints de decisión publican la **misma** forma. Antes había dos:
`opportunities.drivers` emitía `[{name, value, contribution}]` y
`retail_media_opportunities.drivers` un sobre
`[{rationale, nike_stock_pct, …, factors:[…]}]` que obligaba al frontend a
normalizar.

```jsonc
"drivers": [                      // explicabilidad ponderada del score
  {
    "name": "nike_stock_health",  // id estable (inglés, snake_case)
    "label": "Salud de stock Nike", // etiqueta lista para la UI (español)
    "value": 0.938,               // SIEMPRE 0..1
    "unit": "score_0_1",
    "contribution": 23.16,        // % del score; suma 100 entre los publicados
    "detail": { "nike_stock_pct": 93.8, "weight": 0.2, "signal": "nike_stock_pct" }
  }
],
"signals": [                      // métricas observadas, en su unidad natural
  { "name": "nike_stock_pct", "label": "Stock Nike", "value": 93.8, "unit": "pct" }
]
```

Reglas:

1. `drivers` viene **ordenado por `contribution` descendente**.
2. Un factor **sin datos no se publica**. Su ausencia significa "no había
   señal"; nunca hay que leerla como cero (misma regla de degradación elegante
   que el resto del motor). Por eso `contribution` suma 100 entre los presentes.
3. `value` está siempre en 0..1 (`unit: "score_0_1"`): los drivers son
   comparables entre sí y entre endpoints.
4. `detail` es libre: trae el peso configurado (`weight`), el nombre de la
   métrica cruda que alimenta al factor (`signal`) y lo que haya aportado el
   motor (por ejemplo, sobre qué precios y en qué retailer se calculó el gap).
5. `signals` es la **misma forma sin `contribution`**: `{name, label, value, unit}`.
   Unidades posibles: `pct` (0..100), `ratio` (0..1), `score_0_1`, `score_0_100`,
   `number`.

### Por qué `signals` es un campo hermano y no más entradas de `drivers`

Las métricas del sobre de retail media que el brief pide mostrar —stock Nike,
stock del competidor, gap de precio, descuento y share of shelf— **no se
perdieron**: viven en `signals`, con nombre y unidad propios. No se mezclan
dentro de `drivers` por tres razones concretas:

* Dos de ellas son la **inversa** del factor que alimentan
  (`competitor_stock_gap` ↔ `competitor_stock_pct`, `shelf_gap` ↔
  `nike_shelf_share`): meterlas en la misma lista daría dos entradas que dicen
  lo contrario con nombres parecidos.
* El **descuento** no pondera ningún factor: no tiene `contribution` y rompería
  la suma 100 o entraría con un `null` que nadie sabe graficar.
* `drivers` con unidades mezcladas deja de ser graficable: hoy una barra de
  contribución se dibuja sin mirar el `unit`.

El vínculo entre ambos es explícito: cada driver trae `detail.signal` con el
nombre de su métrica.

### `GET /api/retail-media` — ejemplo real

```jsonc
{
  "id": 12,
  "score": 81.01,
  "recommendation": "PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN",
  "rationale": "Nike tiene 94% de stock, ya es competitivo en precio (1.5% de gap) y el producto es relevante (importancia 71.4), pero el competidor acelera (0.84) y el share of shelf Nike es de sólo 21%. En vez de financiar un markdown adicional con el retailer, reasignar parte de esa inversión a visibilidad/retail media: el problema es exposición, no precio.",
  "confidence": "HIGH",
  "drivers": [
    { "name": "price_competitiveness", "label": "Competitividad de precio", "value": 1.0,
      "unit": "score_0_1", "contribution": 24.69,
      "detail": { "price_gap_pct": 1.46, "basis": "precios en Stock Center",
                  "nike_price": 232400.0, "competitor_price": 229000.0,
                  "weight": 0.2, "signal": "price_gap_pct" } },
    { "name": "nike_stock_health", "label": "Salud de stock Nike", "value": 0.938,
      "unit": "score_0_1", "contribution": 23.16,
      "detail": { "nike_stock_pct": 93.8, "weight": 0.2, "signal": "nike_stock_pct" } },
    { "name": "business_importance", "label": "Importancia de negocio", "value": 0.7137,
      "unit": "score_0_1", "contribution": 13.21,
      "detail": { "score": 71.37, "weight": 0.15, "signal": "business_importance" } },
    { "name": "competitor_momentum", "label": "Momentum del competidor", "value": 0.8412,
      "unit": "score_0_1", "contribution": 12.46, "detail": { "weight": 0.12, "signal": "competitor_momentum" } },
    { "name": "competitive_relevance", "label": "Relevancia competitiva", "value": 0.637,
      "unit": "score_0_1", "contribution": 11.79, "detail": { "match_score": 63.7, "weight": 0.15, "signal": "competitive_relevance" } },
    { "name": "shelf_gap", "label": "Brecha de share of shelf", "value": 0.7895,
      "unit": "score_0_1", "contribution": 9.75, "detail": { "nike_shelf_share": 0.2105, "weight": 0.1, "signal": "nike_shelf_share" } },
    { "name": "competitor_stock_gap", "label": "Quiebre del competidor", "value": 0.5,
      "unit": "score_0_1", "contribution": 4.94, "detail": { "competitor_stock_pct": 50.0, "weight": 0.08, "signal": "competitor_stock_pct" } }
  ],
  "signals": [
    { "name": "nike_stock_pct",        "label": "Stock Nike",                        "value": 93.8,   "unit": "pct" },
    { "name": "competitor_stock_pct",  "label": "Stock del competidor",              "value": 50.0,   "unit": "pct" },
    { "name": "price_gap_pct",         "label": "Gap de precio (>0 = Nike más caro)","value": 1.46,   "unit": "pct" },
    { "name": "competitive_relevance", "label": "Relevancia competitiva",            "value": 63.7,   "unit": "score_0_100" },
    { "name": "competitor_momentum",   "label": "Momentum del competidor",           "value": 0.8412, "unit": "score_0_1" },
    { "name": "demand_signal",         "label": "Señal de demanda",                  "value": 0.8412, "unit": "score_0_1" },
    { "name": "nike_shelf_share",      "label": "Share of shelf Nike",               "value": 0.2105, "unit": "ratio" },
    { "name": "business_importance",   "label": "Importancia de negocio",            "value": 71.37,  "unit": "score_0_100" },
    { "name": "nike_discount_pct",     "label": "Descuento Nike",                    "value": 11.99,  "unit": "pct" },
    { "name": "coverage",              "label": "Cobertura de datos",                "value": 1.0,    "unit": "ratio" }
  ],
  "nike_product": { "…": "ProductCard" },
  "competitor_product": { "…": "ProductCard" },
  "retailer": { "…": "retailers row" },
  "country_code": "AR",
  "computed_at": "2026-08-16 13:59:01"
}
```

El **racional en prosa dejó de viajar dentro de `drivers`**: ahora es el campo
`rationale` del item — el equivalente de `recommendation.rationale` en una
oportunidad.

### `GET /api/opportunities` — ejemplo real

```jsonc
{
  "id": 32,
  "opportunity_type": "assortment_white_space",
  "family": "assortment",
  "severity": "CRITICAL",
  "business_importance": 82.09,
  "confidence": "HIGH",
  "drivers": [
    { "name": "competitive_relevance", "label": "Relevancia competitiva", "value": 0.6833,
      "unit": "score_0_1", "contribution": 18.5,
      "detail": { "match_score": 68.33, "source": "competitive_matches", "gate": 1.0,
                  "base_score": 82.09, "lifecycle_multiplier": 1.0, "weight": 0.2,
                  "signal": "competitive_relevance" } },
    { "name": "revenue_proxy", "label": "Proxy de facturación", "value": 1.0,
      "unit": "score_0_1", "contribution": 16.24,
      "detail": { "raw": 1197113.07, "corpus_max": 1197113.07, "weight": 0.12 } },
    { "name": "franchise_importance", "label": "Peso de la franquicia", "value": 0.95,
      "unit": "score_0_1", "contribution": 15.43,
      "detail": { "franchise": "Pegasus", "source": "config", "weight": 0.12 } }
  ],
  "signals": [],
  "recommendation": {
    "action": "…", "rationale": "…", "score": 82.09, "confidence": "HIGH",
    "drivers": [ "…misma forma…" ], "signals": []
  }
}
```

Los drivers de una oportunidad son los factores del **Business Importance
Score**, así que `signals` viene vacío: sus métricas crudas ya viajan dentro de
`detail`. El campo existe igual en los dos endpoints para que un consumidor
pueda escribir un solo renderer.

### Dónde vive la normalización

En `app/api/serializers.py::canonical_drivers()`, es decir en el **borde de la
API**. La columna `drivers` de cada tabla sigue siendo formato de
persistencia (y los tests de cada motor la fijan); el contrato público es éste.
La función tolera las tres formas que existieron —canónica, lista del motor de
oportunidades y sobre de retail media—, así que una base vieja se sirve igual de
bien sin volver a correr el pipeline.

`GET /api/matches/{id}` **no** usa este contrato: expone `factors` con la forma
cruda de `CompositeScore` (`{factor, raw_score, weight, contribution, available,
detail}`), incluidos los factores sin datos, porque esa pantalla explica el
score completo — los que faltan son parte de la explicación.
