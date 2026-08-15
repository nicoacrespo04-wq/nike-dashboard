# Capa de adquisición (`app/collectors`)

Los factores `editorial` (peso 0.15) y `social` (peso 0.10) del Competitive
Match Score, y todo el módulo de Consumer & Brand Intelligence AR, se
alimentaban de los datos de muestra de `app/seed.py`. Este paquete es la capa
de adquisición real: mismas tablas, mismo contrato, datos de verdad.

```
fuentes  ──▶  collect()  ──▶  extract  ──▶  resolve  ──▶  persist (dedup)
(feed/API/     por fuente     patrones     texto →         editorial_mentions
 fixture)                     regex        product_id      social_mention_aggregates
```

Nada de LLMs: regex, reglas y difflib (scikit-learn sólo como prefiltro de
candidatos en catálogos grandes).

---

## 1. Cómo se usa

```bash
python -m app.collectors --list                       # ficha legal de cada fuente
python -m app.collectors                              # corre las fuentes habilitadas
python -m app.collectors --source editorial_fixture   # una fuente puntual
python -m app.collectors --since 2026-07-01           # sólo lo publicado desde esa fecha
python -m app.collectors --dry-run                    # simulacro: no escribe nada
```

```python
from app.collectors import run_collectors
run_collectors(db_path, names=["social_fixture"], since=date(2026, 7, 1), dry_run=False)
# -> {"social_fixture": 30}   filas NUEVAS insertadas
```

Es **idempotente**: correrlo dos veces no duplica menciones (la persistencia
deduplica por clave natural, sin tocar el esquema). Y **nunca frena el
proyecto**: sin red, sin credenciales o con una fuente caída, el colector
devuelve `[]`, se informa y el pipeline sigue.

Para engancharlo al pipeline alcanza con agregar una etapa en
`app/pipeline.py` (la firma ya es compatible):

```python
("collectors", "app.collectors", "run_collectors"),   # entre seed y enrichment
```

Se deja fuera a propósito: hoy el pipeline reconstruye la base desde el
dataset demo y la adquisición se corre por separado (o por cron), para que un
feed caído nunca afecte a la demo.

---

## 2. Módulos

| Archivo | Qué hace |
|---|---|
| `base.py` | `Collector` (Protocol), `SourcePolicy`, registro (`register`/`registered`), `run_collectors`, persistencia idempotente y guard de privacidad. |
| `fetch.py` | HTTP cortés: `robots.txt` + rate limit por host + timeouts; parseo de RSS/Atom y HTML con la librería estándar. |
| `extract.py` | Detección determinística de patrones competitivos (`versus`, `alternative`, `same_list`, `ranking`, `review`). |
| `resolve.py` | Texto libre → `product_id`, con score de confianza y rechazo por baja confianza / ambigüedad / conflicto de versión o marca. |
| `editorial.py` | Colectores que escriben `editorial_mentions` (fixtures locales + feeds RSS/Atom). |
| `social.py` | Colectores que escriben `social_mention_aggregates`, **siempre agregados**. |
| `sentiment.py` | Léxico determinístico en español rioplatense (-1..1). |
| `fixtures/` | Artículos y posts de ejemplo (RSS, HTML, JSON) para probar la cadena entera sin internet. |

### Agregar una fuente nueva

No hay que tocar nada más del backend:

```python
from app.collectors import SourcePolicy, register
from app.collectors.editorial import FeedEditorialCollector

register(FeedEditorialCollector(
    name="mi_medio",
    policy=SourcePolicy(
        source_name="Mi Medio",
        homepage="https://mimedio.example",
        access="feed",                      # api | feed | fixture | review_required | prohibited
        terms="RSS público; sólo metadatos y extracto con atribución.",
        rate_limit_seconds=3.0,
        country_code="AR",
    ),
    feed_urls=("https://mimedio.example/feed/",),
))
```

Sin `SourcePolicy` el registro falla: **declarar fuente, licencia/ToS y rate
limit es parte del contrato**, no un comentario.

---

## 3. Extracción de patrones (`extract.py`)

Sobre título, cuerpo e ítems de lista, con soporte de español rioplatense e
inglés:

| Patrón detectado | `mention_type` | Salida |
|---|---|---|
| `X vs Y`, `X versus Y`, `X contra Y` | `versus` | par (a, b) |
| `alternative(s) to X`, `best alternatives to X`, `similar to X`, `alternativas a la X`, `parecidas a las X`, `en vez de X` | `alternative` | par (ancla, alternativa) |
| `best running shoes`, `best daily trainers`, `best basketball shoes`, `las mejores zapatillas…`, guías de compra | `same_list` | pares dentro de la lista + `list_key` |
| `Ranking…`, `Top 5`, listas numeradas | `ranking` | un producto por fila + posición + `list_key` |
| `Review: X`, `Análisis de la X`, `probamos la X` | `review` | un producto |

Reglas de calidad:

* Un par se guarda **una sola vez por artículo**, con el vínculo más fuerte
  (`versus` > `alternative` > `same_list`).
* Un título con dos productos y la palabra "review" es una comparación, no una
  review.
* `list_key` sale del último segmento de la URL (estable entre corridas), con
  fallback al slug del título.
* Se guardan metadatos + un **extracto ≤280 caracteres** con la fuente y el
  enlace: nunca el artículo completo.

---

## 4. Resolución de producto (`resolve.py`)

> **Es preferible perder una mención que atribuirla al producto equivocado.**
> Una mención mal resuelta inventa competencia que no existe, y esa
> competencia falsa se propaga al match score, al business importance y a las
> recomendaciones.

Cómo se evita la atribución errónea:

1. **Score de confianza 0..1** por resolución; por debajo de
   `accept_threshold` (0.82) se descarta.
2. **Margen de ambigüedad** (0.05): si el segundo candidato está muy cerca, no
   se elige ninguno — `"Pegasus"` a secas puede ser la Pegasus 41 o la Pegasus
   Trail 5.
3. **Conflicto de versión**: si la mención trae un número que no figura en
   ningún alias del producto, se descarta el producto entero. `Pegasus 40` y
   `Novablast 4` no resuelven a la 41 ni a la 5.
4. **Conflicto de marca**: `"Adidas Pegasus 41"` nunca resuelve a un producto
   Nike.
5. **Fronteras**: un nombre no cruza `vs`, `y`, `o`, `contra` ni signos de
   puntuación, así el número de un producto no contamina al otro.
6. Aliases del catálogo: nombre completo, nombre sin marca, nombre
   normalizado, franquicia + versión y franquicia sola (esta última existe
   justamente para *detectar* ambigüedad).

Las estadísticas de cada corrida (`accepted`, `rejected_low_score`,
`rejected_ambiguous`, `rejected_version_conflict`, `rejected_brand_conflict`)
quedan en `collector.stats` y las imprime la CLI.

---

## 5. Social: siempre agregado

* Nunca se modelan individuos: el post se normaliza a `(texto, fecha, tipo de
  fuente, país)` y **cualquier campo de identidad del payload original se
  descarta** (`author`, `user_id`, `permalink`, perfil, avatar…).
* `base.assert_aggregate_only()` vuelve a verificarlo antes de escribir: si
  alguna vez se cuela un campo de identidad, la escritura **falla** en vez de
  contaminar la base.
* `sample_evidence` guarda 1..3 ejemplos públicos pasados por `scrub()`: sin
  @menciones, sin URLs, sin mails ni teléfonos. El mismo texto limpio es el que
  se usa para resolver productos y clasificar: lo que no se puede citar,
  tampoco se usa para atribuir.
* Se emiten filas por período (30 días por defecto, anclados a la observación
  más reciente): conteos por producto, **co-menciones producto↔producto**
  (marca foco primero, como espera el matching engine) y conversación de marca
  cuando no hay producto identificable.
* `sentiment_score` sale de un léxico rioplatense determinístico con negación
  ("no son cómodas") e intensificadores ("re caras"), alineado con el
  vocabulario de `services/brand_intelligence.py`. Sin señal léxica el
  sentimiento queda `NULL` en vez de inventar un 0 neutro.
* `topic` e `intent` se clasifican con el **mismo léxico taxonómico** que ya
  usa `brand_intelligence`, así ningún agregado contradice al servicio que lo
  consume.

---

## 6. Fuentes: estado legal, una por una

Principio: **API oficial > feed publicado por la fuente > scraping (sólo si los
ToS y robots.txt lo permiten) > nada**. Ninguna fuente de red viene encendida:
se habilitan de a una en `config/weights.yaml` después de verificar términos y
robots.txt vigentes.

### 6.1 Editorial

| Fuente | Acceso | Estado legal | Qué se guarda |
|---|---|---|---|
| **Believe in the Run** | RSS (`/feed/`) | Feed público del propio medio. Contenido con copyright. | Título, URL, fecha, extracto ≤280 con atribución |
| **Road Trail Run** | Atom de Blogger | Feed público. | Ídem |
| **Doctors of Running** | Atom de Blogger | Feed público. | Ídem |
| **Runner's World** (Hearst) | RSS | Feed público; los ToS prohíben reproducir el artículo. | Sólo metadatos + extracto corto con enlace canónico |
| **Google News AR** (consulta) | RSS de búsqueda | Feed de resultados: titular, enlace y fecha. No se descarga el artículo del medio de destino. | Titular + enlace + fecha |
| **Wirecutter / NYT** | ❌ **prohibido** | Los ToS del NYT prohíben crawling y uso automatizado sin licencia. | — (haría falta licencia de contenido del NYT) |
| **Mercado Libre** (reseñas/preguntas) | ❌ **prohibido scrapear** | Los términos prohíben el scraping; existe API oficial. | — (API de Mercado Libre con app registrada y OAuth; alimentaría `reviews`) |

### 6.2 Social

| Plataforma | Acceso | Estado legal | Qué haría falta |
|---|---|---|---|
| **Reddit** (r/RunningShoeGeeks, r/RunningArgentina, r/argentina) | API oficial | Data API con app registrada + OAuth; el scraping de HTML está prohibido por ToS y robots.txt. Rate limit oficial 100 QPM. | `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` |
| **Instagram** | ❌ **prohibido** | Los Términos de Meta prohíben la recolección automatizada. | Instagram Graph API (sólo cuentas propias / Business Discovery) o Meta Content Library vía proveedor licenciado |
| **TikTok** | ❌ **prohibido** | ToS prohíben scraping. | TikTok Research API (institucional, con solicitud aprobada) |
| **X / Twitter** | ❌ **prohibido** | ToS prohíben scraping; sólo API paga. | X API v2 tier Basic/Pro, guardando **sólo** agregados |
| **Facebook** (grupos/comentarios) | ❌ **prohibido** | Términos de Meta + los grupos suelen ser privados. | Meta Content Library (académico/licenciado); los grupos privados quedan fuera por diseño |
| **WhatsApp** | ❌ **fuera de alcance** | Conversación privada y cifrada: no corresponde recolectarla. | Ninguna |

Las fuentes prohibidas están **declaradas como colectores inhabilitados**
(`DisabledCollector`): tienen su ficha, su motivo y la API oficial que haría
falta, y `collect()` devuelve `[]` sin ninguna ruta de código que salga a la
red. Aparecen en `--list` para que la decisión quede a la vista y auditable.

### 6.3 Reglas de higiene que aplican a todas

* `robots.txt` se consulta **antes** de cada host y se cachea; si no se puede
  leer (sin red, 4xx, timeout) **no se pide nada**: en la duda, no se pide.
* Rate limit por host, declarado por la fuente (2–5 s).
* User-Agent identificable con contacto (configurable en
  `collectors.http.user_agent`).
* Se almacenan metadatos + extracto breve con atribución y enlace; nunca el
  artículo completo.
* Sin credenciales, el colector de API devuelve `[]` y lo informa.

---

## 7. Activar una fuente en producción

1. Verificar los términos vigentes y el `robots.txt` del host (la ficha en
   `--list` es el punto de partida, no la última palabra).
2. Cargar credenciales si la fuente las requiere (`REDDIT_CLIENT_ID`, …).
3. Encenderla en `config/weights.yaml`:

```yaml
collectors:
  sources:
    believe_in_the_run: { enabled: true }
    runners_world:      { enabled: true }
    reddit_public_api:  { enabled: true }
```

4. Correr primero en seco: `python -m app.collectors --source X --dry-run -v`.
5. Programar la corrida (cron/CI) con `--since` de la última ejecución.

### Configuración disponible

Todo lo ajustable vive bajo la clave `collectors` de `config/weights.yaml`
(los defaults están en `base.DEFAULTS`, en un solo lugar):

```yaml
collectors:
  resolve:
    accept_threshold: 0.82     # confianza mínima para atribuir una mención
    ambiguity_margin: 0.05     # distancia mínima contra el segundo candidato
    max_ngram: 5
    tfidf_min_aliases: 800     # prefiltro sklearn a partir de este tamaño de catálogo
  editorial:
    max_excerpt_chars: 280
    max_same_list_pairs: 20
    max_products_per_list: 12
  social:
    period_days: 30
    max_evidence_examples: 3
    max_evidence_chars: 220
    min_mentions_per_row: 1
  http:
    timeout_seconds: 6.0
    default_rate_limit_seconds: 2.0
    user_agent: "..."
    max_items_per_run: 60
  sources:
    <nombre>: { enabled: true }
```

Subir `accept_threshold` = menos menciones y más precisión; bajarlo = más
cobertura y más riesgo de inventar competencia. El default está calibrado para
equivocarse hacia el lado de perder menciones.

---

## 8. Fixtures y tests

`app/collectors/fixtures/` trae ejemplos con los patrones reales del dominio
("Nike Pegasus 41 vs ASICS Novablast 5", "best daily trainers 2026",
"Ranking: máxima amortiguación", alternativas, hilos de foro AR):

```
fixtures/editorial/doble_ritmo.xml                    RSS con versus, review y alternativas
fixtures/editorial/mejores_daily_trainers_2026.html   guía de compra (same_list)
fixtures/editorial/ranking_max_cushion_2026.html      ranking numerado
fixtures/editorial/notas_sueltas.json                 alternativas + best basketball shoes
fixtures/social/foro_running_ar.json                  hilos de foro (co-menciones)
fixtures/social/comunidad_sneakers_ar.json            comunidad sneakers (con PII a limpiar)
```

`tests/test_collectors.py` corre entero **sin red** y cubre: extracción de cada
`mention_type`, resolución correcta y rechazo por baja confianza, agregación
social sin individuos, idempotencia, `--since`, degradación (sin red, sin
credenciales, colector roto, feed corrupto), respeto de `robots.txt` y que
ninguna fuente prohibida pida nada.

```bash
cd backend && pytest tests/ -q
```
