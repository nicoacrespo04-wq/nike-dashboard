import { pickLabel } from '@/lib/labels'

/**
 * scrapers.ts — Qué universo mira cada consulta (una sola definición).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUÉ EXISTE ESTE ARCHIVO (bug confirmado, no supuesto)
 * ─────────────────────────────────────────────────────────────────────────
 * El home mostraba "SKUs únicos Nike 27.358" contra "Adidas 9.052" y
 * "Puma 6.789". El negocio lo leyó como una comparación de surtido y no cierra:
 * Nike no puede tener más SKUs que las otras dos sumadas.
 *
 * No era deduplicación (ya usaba `COUNT(DISTINCT style_color)`). Eran DOS
 * problemas encadenados, los dos verificados contra un Postgres local cargado
 * con `db/schema.sql` y el fixture del propio repo
 * (`backend/tools/generate_scale_fixture.py`, que replica la forma y la
 * suciedad de los datos reales):
 *
 * 1. **Se contaba la columna equivocada.** En `pricing_data` cada fila es UNA
 *    COMPARACIÓN: un producto de competidor visto en un retailer contra el
 *    producto Nike comparable. `style_color` pertenece al **bloque Nike** de la
 *    fila, no al competidor — lo dice el propio ingest
 *    (`backend/app/ingest/mapping.py::map_product`: para `side='nike'`,
 *    `sku = style_color`; para el competidor, `sku = productcode_competitor`).
 *    Se comprobó en la base: el mismo `style_color` aparece bajo 11 `marca`
 *    distintas a la vez (ADIDAS, PUMA, TOPPER, MIZUNO…), y `pdp_nike` es
 *    siempre `nike.com.ar/p/<style_color>`. O sea que
 *    `COUNT(DISTINCT style_color) FILTER (marca='ADIDAS')` no contaba SKUs de
 *    Adidas: contaba **SKUs de Nike que fueron comparados contra un Adidas**.
 *    El código propio del producto observado es `productcode_competitor`
 *    (con `product_code_competitor` como respaldo).
 *
 * 2. **Se mezclaban universos.** `/api/pricing/summary` era la ÚNICA route de
 *    pricing sin filtro de `scraper`, así que el número de Nike sumaba los 8
 *    retailers argentinos + nike.com.ar + **nike.com.co + nike.com + URU/USA**,
 *    mientras Adidas y Puma no tienen ningún feed equivalente de otro país.
 *    En el fixture eso solo ya explica el orden de magnitud: 13.653 style_color
 *    distintos de Nike vienen de los sitios de otros países contra 270 del
 *    universo góndola AR.
 *
 * De ahí este módulo: los nombres de scraper estaban repetidos como literales
 * en 5 routes distintas y con casing fijo (`'ADIDAS_7'`, `'Puma_AR'`), cuando
 * en los datos reales el mismo scraper aparece como `adidas_7`, `ADIDAS_7`,
 * `Dexter` / `Dexter_AR` / `dexter_ar` (ver el fixture y
 * `backend/app/ingest/mapping.py::canon_key`). Un `scraper NOT IN ('ADIDAS_7')`
 * deja pasar `adidas_7`. Acá todas las comparaciones van por clave canónica.
 */

/**
 * Clave canónica de un nombre de scraper: minúsculas, sin nada que no sea
 * alfanumérico. Mismo criterio que `canon_key()` del ingest, para que
 * `'Stock Center'`, `'StockCenter'` y `'stock_center'` sean el mismo retailer.
 */
export function scraperKey(raw: string | null | undefined): string {
  if (!raw) return ''
  return raw.toLowerCase().replace(/[^a-z0-9]+/g, '')
}

/** Fragmento SQL equivalente a `scraperKey()`. */
export function scraperKeySql(col = 'scraper'): string {
  return `REGEXP_REPLACE(LOWER(COALESCE(${col}, '')), '[^a-z0-9]+', '', 'g')`
}

/** nike.com.ar — el D2C de Nike en Argentina. */
export const NIKE_D2C_AR = ['nike_ar_general'] as const

/** Sitios propios de la competencia en Argentina. */
export const COMPETITOR_D2C_AR = ['ADIDAS_7', 'Puma_AR'] as const

/**
 * Sitios de Nike de OTROS países. No comparan contra nada argentino y no
 * tienen equivalente del lado de la competencia: cualquier KPI que los sume
 * del lado de Nike y no del lado de Adidas/Puma está comparando peras con
 * manzanas.
 */
export const NIKE_D2C_FOREIGN = ['nike_co_general', 'nike_us_general', 'URU', 'USA'] as const

/** Todos los scrapers de sitio de marca (D2C), de cualquier país. */
export const BRAND_SITE_SCRAPERS = [
  ...NIKE_D2C_AR,
  ...COMPETITOR_D2C_AR,
  ...NIKE_D2C_FOREIGN,
] as const

/** D2C argentino: las tres marcas en su propio sitio, mismo país. */
export const D2C_AR_SCRAPERS = [...NIKE_D2C_AR, ...COMPETITOR_D2C_AR] as const

function keyList(names: readonly string[]): string {
  const keys = Array.from(new Set(names.map(scraperKey))).filter(Boolean)
  return keys.map((k) => `'${k}'`).join(',')
}

/** `TRUE` si la fila viene de alguno de esos scrapers (case/punctuation-insensitive). */
export function scraperInSql(names: readonly string[], col = 'scraper'): string {
  return `${scraperKeySql(col)} IN (${keyList(names)})`
}

/** `TRUE` si la fila NO viene de ninguno de esos scrapers. */
export function scraperNotInSql(names: readonly string[], col = 'scraper'): string {
  return `${scraperKeySql(col)} NOT IN (${keyList(names)})`
}

/* ═══════════════════════════════════════════════════════════════════════════
 * RETAILER CANÓNICO Y PAÍS — dos bugs confirmados sobre los mismos datos
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `scraperKey()` (arriba) alcanza para comparar contra una lista blanca de
 * nombres FIJOS ('ADIDAS_7' vs 'adidas_7'), que es para lo que se escribió.
 * NO alcanza para AGRUPAR retailers, y medirlo lo deja claro. Sobre el
 * Postgres local con el fixture del repo:
 *
 *   psql -c "SELECT scraper, COUNT(*) FROM pricing_data GROUP BY 1 ORDER BY 2 DESC"
 *     opensports       11.769      scraperKey() -> 'opensports'
 *     OpenSports_AR    11.639      scraperKey() -> 'opensportsar'   <- OTRA clave
 *     Open Sports      11.384      scraperKey() -> 'opensports'
 *
 * O sea: `scraperKey()` junta dos de las tres y deja la del sufijo de país
 * afuera. Efecto medido en la UI (`curl /api/pricing/pvp-compliance`): la
 * tabla de cumplimiento de PVP listaba 40 retailers, con Open Sports partido
 * en tres filas de 2.281 / 2.278 / 2.205 y compliance 5% / 7% / 6% — tres
 * números distintos para UN retailer, ninguno de ellos el real. Lo mismo en
 * `/api/pricing/bml-heatmap` (33 columnas para 10 retailers) y en
 * `/api/pricing/markdown-analysis` (40 filas por retailer).
 *
 * SEGUNDO BUG, EL MISMO SUFIJO: el universo extranjero.
 * `RETAILERS_AR_SQL` se define por exclusión ("todo lo que no es sitio de
 * marca") y su comentario dice "los 8 retailers argentinos". No era cierto:
 * los catálogos chilenos entran por la puerta de atrás, porque `Dexter_CL`
 * tampoco es un sitio de marca. Medido:
 *
 *   psql -c "SELECT scraper, COUNT(*) FROM pricing_data
 *            WHERE scraper LIKE '%\_CL' GROUP BY 1"
 *     OpenSports_CL 680 · SoloDeportes_CL 82 · Dexter_CL 82 · DigitalSport_CL 78
 *     MercadoLibre_CL 71 · Grid_CL 70 · Sporting_CL 66 · Dafiti_CL 65
 *     StockCenter_CL 63 · Moov_CL 60          (10 retailers, 1.317 filas)
 *
 * Y aparecían de verdad en la UI: `/api/pricing/markdown-analysis` devolvía
 * filas de `Dexter_CL`, `Dafiti_CL`, `MercadoLibre_CL` y `DigitalSport_CL`;
 * `/api/pricing/pvp-compliance` medía "cumplimiento de PVP" de `OpenSports_CL`
 * contra un `precio_sugerido` que es de la lista argentina. Es exactamente el
 * bug que el encabezado de este archivo documenta para el KPI de SKUs, sólo
 * que por país de RETAILER en vez de por país de sitio Nike.
 *
 * LAS DOS COSAS SON EL MISMO DATO. El sufijo de país es la única pista de país
 * que traen las filas de retailer — lo dice el propio ingest
 * (`backend/app/ingest/mapping.py`: `country_suffix_map`, `_country_from_suffix`,
 * `_strip_country_suffix`) y por eso su clave natural de retailer es
 * `(nombre canónico SIN sufijo, país)`. Acá se replica ese mismo par:
 * `retailerKeySql()` da el nombre, `scraperCountrySql()` da el país, y recién
 * juntos identifican al retailer. Agrupar por el nombre solo volvería a
 * mezclar `Dexter_AR` con `Dexter_CL`.
 */

/**
 * Sufijo de país → código ISO. Copiado de
 * `backend/app/ingest/mapping.py::country_suffix_map`, que es la definición
 * autoritativa. Si allá se suma un país, sumarlo acá.
 */
const COUNTRY_SUFFIXES: Readonly<Record<string, string>> = {
  ar: 'AR', uy: 'UY', uru: 'UY', us: 'US', usa: 'US',
  co: 'CO', cl: 'CL', br: 'BR', mx: 'MX', pe: 'PE', py: 'PY',
}

/**
 * País de los scrapers de sitio de marca, que NO lo declaran por sufijo:
 * `nike_co_general` termina en 'general' y `USA` no tiene separador, así que
 * la regla del sufijo los dejaría a los dos en el default 'AR' — que es
 * justo el error que este módulo existe para no cometer. Mismo mapa que
 * `backend/app/ingest/mapping.py::brand_sites` (`country_code`).
 */
const BRAND_SITE_COUNTRY: Readonly<Record<string, string>> = {
  nike_ar_general: 'AR',
  nike_co_general: 'CO',
  nike_us_general: 'US',
  uru: 'UY',
  usa: 'US',
  adidas_7: 'AR',
  puma_ar: 'AR',
}

/** País por defecto cuando el nombre no declara ninguno (mismo que el ingest). */
export const DEFAULT_COUNTRY = 'AR'

/**
 * Regex del sufijo de país al final del nombre. Exige un separador NO
 * alfanumérico antes del sufijo, que es la forma de replicar el
 * `len(tokens) > 1` de `_strip_country_suffix()`: así `'Dexter_AR'` pierde el
 * sufijo pero `'USA'` (que es un scraper entero, no un sufijo) queda intacto.
 */
const COUNTRY_SUFFIX_RE_SQL = `[^A-Za-z0-9](${Object.keys(COUNTRY_SUFFIXES).join('|')})$`
const COUNTRY_SUFFIX_RE_JS = new RegExp(
  `[^A-Za-z0-9](${Object.keys(COUNTRY_SUFFIXES).join('|')})$`,
  'i',
)

function sqlLiteral(text: string): string {
  return `'${text.replace(/'/g, "''")}'`
}

/**
 * País de la fila a partir del nombre del scraper. Misma prioridad que
 * `map_country()` del ingest: sitio de marca > sufijo de país > default 'AR'.
 */
export function scraperCountry(raw: string | null | undefined): string {
  const trimmed = (raw ?? '').trim()
  if (!trimmed) return DEFAULT_COUNTRY
  const site = BRAND_SITE_COUNTRY[trimmed.toLowerCase()]
  if (site) return site
  const match = COUNTRY_SUFFIX_RE_JS.exec(trimmed)
  if (match) return COUNTRY_SUFFIXES[match[1].toLowerCase()] ?? DEFAULT_COUNTRY
  return DEFAULT_COUNTRY
}

/** Fragmento SQL equivalente a `scraperCountry()`. */
export function scraperCountrySql(col = 'scraper'): string {
  const site = Object.entries(BRAND_SITE_COUNTRY)
    .map(([name, code]) => `WHEN LOWER(BTRIM(${col})) = ${sqlLiteral(name)} THEN ${sqlLiteral(code)}`)
    .join(' ')
  const suffix = Object.entries(COUNTRY_SUFFIXES)
    .map(([sfx, code]) => `WHEN ${sqlLiteral(sfx)} THEN ${sqlLiteral(code)}`)
    .join(' ')
  return `
    COALESCE(
      CASE ${site} END,
      CASE LOWER((REGEXP_MATCH(BTRIM(COALESCE(${col}, '')), '${COUNTRY_SUFFIX_RE_SQL}', 'i'))[1])
        ${suffix}
      END,
      ${sqlLiteral(DEFAULT_COUNTRY)})`
}

/**
 * Clave canónica del RETAILER: como `scraperKey()` pero sacando antes el
 * sufijo de país, que pertenece al par `(retailer, país)` y no al nombre.
 *
 *   'Open Sports' · 'OpenSports_AR' · 'opensports'  -> 'opensports'
 *   'Dexter_CL'                                     -> 'dexter'  (país 'CL')
 *
 * Ojo: por sí sola NO identifica al retailer — `Dexter_AR` y `Dexter_CL` dan
 * la misma clave. Va SIEMPRE junto a `scraperCountrySql()` (o dentro de un
 * universo que ya acotó el país).
 */
export function retailerKey(raw: string | null | undefined): string {
  const trimmed = (raw ?? '').trim()
  if (!trimmed) return ''
  return scraperKey(trimmed.replace(COUNTRY_SUFFIX_RE_JS, ''))
}

/**
 * Etiqueta legible de un retailer a partir de TODAS las escrituras con que el
 * scraper lo nombró. Determinística y estable (ver `lib/labels.ts::pickLabel`).
 *
 * El sufijo de país se saca ANTES de puntuar, por dos motivos:
 *   · El país ya lo fija el universo, así que repetirlo en la etiqueta es
 *     ruido ("OpenSports_AR" en una tabla que sólo tiene retailers argentinos).
 *   · Sin sacarlo, el `_` del sufijo cuenta como separador de palabras y
 *     `'OpenSports_AR'` le ganaba a `'Open Sports'` — medido: la primera
 *     versión de este arreglo etiquetaba las columnas 'OpenSports_AR' y
 *     'stockcenter_AR' en vez de 'Open Sports' y 'Stock Center'.
 */
export function retailerLabel(variants: readonly (string | null | undefined)[]): string {
  return pickLabel(variants.map((v) => (v ?? '').trim().replace(COUNTRY_SUFFIX_RE_JS, '')))
}

/** Fragmento SQL equivalente a `retailerKey()`. */
export function retailerKeySql(col = 'scraper'): string {
  return scraperKey_sqlOn(
    `REGEXP_REPLACE(BTRIM(COALESCE(${col}, '')), '${COUNTRY_SUFFIX_RE_SQL}', '', 'i')`,
  )
}

/** `scraperKeySql()` aplicado a una EXPRESIÓN ya construida, no a una columna. */
function scraperKey_sqlOn(expr: string): string {
  return `REGEXP_REPLACE(LOWER(${expr}), '[^a-z0-9]+', '', 'g')`
}

/**
 * `TRUE` si el scraper trae un país que NO es Argentina.
 *
 * Antes esto tenía su propia lista de sufijos (`['CL','UY','BR',…]`) y su
 * propio regex `_(XX)$`. Se unificó contra `scraperCountrySql()` por dos
 * motivos medidos, no estéticos:
 *
 *   · La lista duplicada se desincronizaba sola: la de acá no tenía 'PY' y la
 *     del ingest sí, así que un `Moov_PY` habría entrado al universo argentino.
 *   · El regex `_(XX)$` sólo ve el sufijo. `nike_co_general` (Colombia) y
 *     `USA` / `URU` NO terminan en `_XX`, así que se los daba por argentinos —
 *     justo los tres casos que el encabezado de este archivo documenta como el
 *     bug original de mezclar universos. `scraperCountrySql()` los resuelve por
 *     el mapa de sitios de marca, igual que `map_country()` en el ingest.
 */
export function foreignCountrySql(col = 'scraper'): string {
  return `(${scraperCountrySql(col)} <> ${sqlLiteral(DEFAULT_COUNTRY)})`
}

/**
 * `TRUE` si la fila es del mercado argentino.
 *
 * Ya NO hace falta escribirlo a mano en cada route: está plegado dentro de los
 * universos argentinos de `UNIVERSES` (ver abajo). Se sigue exportando porque
 * es el predicado explícito para una consulta que arme su propio `WHERE`.
 */
export const AR_ONLY_SQL = `NOT ${foreignCountrySql()}`

/**
 * Los retailers argentinos: todo lo que no es un sitio de marca Y fue
 * capturado en Argentina. Es el universo "góndola", el único donde las tres
 * marcas se observan de verdad una al lado de la otra.
 *
 * El canal (no es sitio de marca) se define por EXCLUSIÓN a propósito: cuando
 * se suma un retailer nuevo al workflow (ver `docs/scrapers.md` §6) entra
 * solo, sin que haya que acordarse de tocar el dashboard.
 *
 * PERO la exclusión sola no alcanzaba, y el comentario que decía "los 8
 * retailers argentinos" era falso: `Dexter_CL` tampoco es un sitio de marca,
 * así que los 10 retailers chilenos entraban al universo argentino sin que
 * nadie lo decidiera. Medido antes del arreglo, `curl /api/pricing/pvp-compliance`
 * devolvía `OpenSports_CL` (135 filas), `StockCenter_CL` (24), `Sporting_CL`
 * (15)… midiendo "cumplimiento de PVP" contra un `precio_sugerido` que es de
 * la lista de precios ARGENTINA. Por eso el país va acá adentro y no como un
 * `AND` que cada route se tiene que acordar de escribir.
 */
export const RETAILERS_AR_SQL = `(${scraperNotInSql(BRAND_SITE_SCRAPERS)} AND ${AR_ONLY_SQL})`

/**
 * Universos comparables que la UI puede elegir. La clave viaja en la query
 * string; el label y la explicación se muestran tal cual en la tarjeta, para
 * que ningún número quede sin decir de dónde sale.
 *
 * LOS TRES SON ARGENTINOS: los tres llevan `AR_ONLY_SQL` adentro. El universo
 * dice QUÉ CANAL se mira; el país no es una opción, porque ninguna de estas
 * pantallas compara contra un catálogo de otro país (los precios están en
 * otra moneda y el `precio_sugerido` es de la lista argentina). Una pantalla
 * que legítimamente quiera ver otro país tiene que agregar su universo acá,
 * con su etiqueta, y no colarlo por omisión de un filtro.
 */
export const UNIVERSES = {
  gondola: {
    label: 'Góndola (retailers AR)',
    description: 'Surtido de cada marca visto en los retailers argentinos monitoreados.',
    sql: RETAILERS_AR_SQL,
  },
  d2c: {
    label: 'Sitio de marca (D2C AR)',
    description: 'Surtido publicado por cada marca en su propio sitio argentino.',
    sql: `(${scraperInSql(D2C_AR_SCRAPERS)} AND ${AR_ONLY_SQL})`,
  },
  all_ar: {
    label: 'Todo Argentina',
    description: 'Góndola + sitio de marca, sin los catálogos Nike de otros países.',
    sql: `(${scraperNotInSql(NIKE_D2C_FOREIGN)} AND ${AR_ONLY_SQL})`,
  },
} as const

export type UniverseKey = keyof typeof UNIVERSES

export const UNIVERSE_KEYS = Object.keys(UNIVERSES) as UniverseKey[]

/** Universo por defecto: el único en el que la comparación entre marcas es válida. */
export const DEFAULT_UNIVERSE: UniverseKey = 'gondola'

/** Resuelve el parámetro `universe` de la query string. */
export function parseUniverse(
  raw: string | null | undefined,
  fallback: UniverseKey = DEFAULT_UNIVERSE,
): UniverseKey {
  if (raw && (UNIVERSE_KEYS as string[]).includes(raw)) return raw as UniverseKey
  return fallback
}

/**
 * Identificador propio del producto OBSERVADO en la fila (el del bloque
 * competidor, sea de la marca que sea — incluido Nike cuando se lo captura en
 * un retailer). Es lo que hay que contar para "SKUs de la marca X".
 *
 * NO usar `style_color` para esto: es el SKU del producto Nike de referencia
 * de la comparación. Ver el bloque de arriba.
 */
export const OBSERVED_SKU_SQL = `
  COALESCE(
    NULLIF(BTRIM(productcode_competitor), ''),
    NULLIF(BTRIM(product_code_competitor), ''),
    NULLIF(BTRIM(product_name_competitor), '')
  )`
