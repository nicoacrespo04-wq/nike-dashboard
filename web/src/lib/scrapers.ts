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

/**
 * Los 8 retailers argentinos: todo lo que no es un sitio de marca. Es el
 * universo "góndola", el único donde las tres marcas se observan de verdad una
 * al lado de la otra.
 *
 * Se define por exclusión y no por lista blanca a propósito: cuando se suma un
 * retailer nuevo al workflow (ver `docs/scrapers.md` §6) entra solo, sin que
 * haya que acordarse de tocar el dashboard.
 */
export const RETAILERS_AR_SQL = scraperNotInSql(BRAND_SITE_SCRAPERS)

/**
 * Universos comparables que la UI puede elegir. La clave viaja en la query
 * string; el label y la explicación se muestran tal cual en la tarjeta, para
 * que ningún número quede sin decir de dónde sale.
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
    sql: scraperInSql(D2C_AR_SCRAPERS),
  },
  all_ar: {
    label: 'Todo Argentina',
    description: 'Góndola + sitio de marca, sin los catálogos Nike de otros países.',
    sql: scraperNotInSql(NIKE_D2C_FOREIGN),
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
