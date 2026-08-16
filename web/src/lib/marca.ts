/**
 * marca.ts — Normalización de la columna `marca` (una sola definición).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * CAUSA RAÍZ DE LOS KPIs EN "N/D" — REPRODUCIDA, NO SUPUESTA
 * ─────────────────────────────────────────────────────────────────────────
 * Los KPIs de Share of Shelf (VISIBILIDAD NIKE / ADIDAS / PUMA) mostraban
 * `N/D` — "Sin datos disponibles" — MIENTRAS las barras de "Share of Shelf por
 * retailer", que salen de la MISMA tabla y de la MISMA request, mostraban
 * porcentajes correctos.
 *
 * Se levantó un Postgres 16 local con `db/schema_shelf.sql`, se cargó
 * `db/retail_media_search.csv` TAL CUAL (marca limpia: 'Nike'/'Adidas'/'Puma')
 * y se corrió el código exacto de las dos versiones contra seis formas de
 * suciedad de `marca`. Resultado (una fila por suciedad, "N/D" = 0 filas):
 *
 *   suciedad             UPPER = ANY($1)  UPPER IN (lit.)  TRIM(UPPER)  marcaNormSql
 *   limpio               OK               OK               OK           OK
 *   espacio ASCII        N/D              N/D              OK           OK
 *   NBSP U+00A0          N/D              N/D              N/D          OK
 *   zero-width U+200B    N/D              N/D              N/D          OK
 *   CR final (CRLF)      N/D              N/D              N/D          OK
 *   casing mezclado      OK               OK               OK           OK
 *
 * Lo que ESO descarta y lo que confirma:
 *
 *   1. **NO es el binding de `= ANY($1)`.** `UPPER(marca) = ANY($1)` con el
 *      array de JS y `UPPER(marca) IN ('NIKE','ADIDAS','PUMA')` literal
 *      devuelven EXACTAMENTE las mismas filas en las seis condiciones. El
 *      driver serializa el array a `text[]` sin problema.
 *
 *   2. **NO es el casing.** Con 'NIKE'/'nike'/'Nike' mezclados el filtro viejo
 *      anda. Por eso el arreglo de casing (commit c2b134a) no cambió nada:
 *      atacó lo único que NO estaba roto.
 *
 *   3. **NO alcanza con `TRIM(UPPER(marca))`.** `BTRIM()` pelado saca el
 *      espacio ASCII y nada más: NBSP, zero-width y el `\r` de un CSV con
 *      CRLF lo atraviesan enteros. Verificado: `btrim(E' Nike')` sigue
 *      empezando con `c2a0`.
 *
 *   4. **Es cualquier suciedad de caracteres, y el problema de fondo es de
 *      DISEÑO.** `INITCAP` de las barras sólo AGRUPA: le da igual el ruido,
 *      devuelve `' Nike '` y la barra se dibuja igual (HTML colapsa el espacio
 *      al renderizar). El `WHERE ... = 'NIKE'` del bloque global FILTRA: falla
 *      cerrado, se queda con CERO filas, y cero filas → `null` → `N/D` en las
 *      tres tarjetas a la vez. Agrupar degrada mostrando lo que hay; filtrar
 *      por un literal de marca degrada vaciando el bloque entero.
 *
 * REGLA QUE QUEDA — y que es lo que hace que esto no vuelva:
 *
 *   · **Nunca filtrar por un literal de marca en el `WHERE`.** Se agrupa por
 *     marca normalizada, se traen TODAS las marcas y la canónica se resuelve
 *     en TypeScript. Un valor sucio nuevo degrada UNA marca, no el bloque.
 *   · `canonicalMarca()` tiene un último recurso por contención de token, así
 *     que un valor que la normalización todavía no conoce ('NIKE ARG.',
 *     'adidas Originals') igual cae en su marca en vez de en `N/D`.
 *   · Los endpoints devuelven SIEMPRE los valores DISTINCT crudos de `marca`
 *     con su hexadecimal (`marcaDiagnosticSql`), así el próximo valor raro se
 *     ve desde la propia pantalla sin abrir la base.
 */

/**
 * Clase de caracteres "espacio" que se normalizan, en sintaxis de regex de
 * Postgres (ARE soporta los escapes `\uXXXX` dentro de una clase; verificado
 * contra el motor, no asumido). Incluye los invisibles que un `BTRIM()` pelado
 * NO saca y que son justo los que rompían el filtro:
 * NBSP U+00A0, zero-width U+200B/U+200C/U+200D y BOM U+FEFF.
 */
const SQL_SPACE_CLASS = String.raw`[\s\u00a0\u200b\u200c\u200d\ufeff]`

/** La misma clase de caracteres, en sintaxis de regex de JavaScript. */
const JS_SPACE_CLASS = String.raw`[\s\u00a0\u200b\u200c\u200d\ufeff]`

/**
 * Fragmento SQL que normaliza una columna de marca: colapsa cualquier corrida
 * de espacios (visibles e invisibles) a uno solo, recorta las puntas y pasa a
 * mayúsculas.
 *
 *     ' Nike ' -> 'NIKE'  ·  'nike' -> 'NIKE'  ·  U+00A0 + 'Adidas' -> 'ADIDAS'
 *     'adidas  originals' → 'ADIDAS ORIGINALS'
 *
 * ⚠ `col` se interpola directo en el SQL: pasar SIEMPRE nombres de columna
 * literales del código, nunca input del usuario.
 */
export function marcaNormSql(col: string): string {
  return `UPPER(BTRIM(REGEXP_REPLACE(${col}, '${SQL_SPACE_CLASS}+', ' ', 'g')))`
}

/**
 * Fragmento SQL de diagnóstico: los valores crudos de `marca` con su
 * representación hexadecimal, para ver los caracteres invisibles.
 * Los endpoints lo devuelven SIEMPRE, no sólo cuando algo falla: es lo que
 * hizo falta para encontrar este bug y lo que evita tener que adivinar de nuevo.
 */
export function marcaDiagnosticSql(table: string): string {
  return `
    SELECT
      marca                                       AS raw,
      encode(convert_to(marca, 'UTF8'), 'hex')    AS hex,
      COUNT(*)::int                               AS n
    FROM ${table}
    GROUP BY marca
    ORDER BY n DESC
    LIMIT 25
  `
}

/** Fila del diagnóstico de marcas crudas. */
export interface MarcaDiagnosticRow {
  raw: string
  hex: string
  n: number
}

/** Las tres marcas que el dashboard compara. */
export const MARCAS = ['NIKE', 'ADIDAS', 'PUMA'] as const
export type Marca = (typeof MARCAS)[number]
export type MarcaKey = Lowercase<Marca>

/**
 * Alias exactos → marca canónica. Mismo criterio que
 * `backend/app/ingest/mapping.py` (`brand_aliases`) y `db/normalize_marca.py`:
 * sub-marcas y variantes que el scraper trae como marca propia pero que el
 * negocio lee como la marca madre.
 */
const MARCA_ALIASES: Readonly<Record<string, Marca>> = {
  NIKE: 'NIKE',
  'NIKE SB': 'NIKE',
  'NIKE AR': 'NIKE',
  'NIKE ARGENTINA': 'NIKE',
  JORDAN: 'NIKE',
  'AIR JORDAN': 'NIKE',
  ADIDAS: 'ADIDAS',
  'ADIDAS ORIGINALS': 'ADIDAS',
  'ADIDAS PERFORMANCE': 'ADIDAS',
  PUMA: 'PUMA',
}

/**
 * Último recurso: token que, si aparece dentro del valor normalizado, decide la
 * marca. Es lo que garantiza que un valor NUEVO y desconocido ('NIKE ARG.',
 * 'PUMA SE', 'adidas Terrex') caiga en su marca en vez de quedar en `N/D`.
 * El orden importa: se evalúa de arriba hacia abajo.
 */
const MARCA_TOKENS: readonly (readonly [string, Marca])[] = [
  ['JORDAN', 'NIKE'],
  ['NIKE', 'NIKE'],
  ['ADIDAS', 'ADIDAS'],
  ['PUMA', 'PUMA'],
]

/**
 * Normaliza en TypeScript igual que `marcaNormSql` en SQL. Se usa sobre valores
 * que ya vienen normalizados de la base (defensa barata) y sobre parámetros de
 * query que escribe la UI.
 */
export function normalizeMarca(raw: string | null | undefined): string {
  if (!raw) return ''
  return raw
    .replace(new RegExp(JS_SPACE_CLASS + '+', 'g'), ' ')
    .trim()
    .toUpperCase()
}

/**
 * Marca canónica de un valor crudo, o `null` si no se parece a ninguna de las
 * tres que el dashboard compara. Resolver acá (y no en el `WHERE`) es lo que
 * hace que un valor sucio nuevo degrade el dato de UNA marca en vez de vaciar
 * el bloque entero.
 */
export function canonicalMarca(raw: string | null | undefined): Marca | null {
  const norm = normalizeMarca(raw)
  if (!norm) return null
  const exact = MARCA_ALIASES[norm]
  if (exact) return exact
  // Sin acentos ni signos: 'NIKE-AR', 'NIKE®', 'ADIDAS.' entran igual.
  const letters = norm.replace(/[^A-Z0-9]+/g, '')
  const token = MARCA_TOKENS.find(([needle]) => letters.includes(needle))
  return token ? token[1] : null
}

/**
 * `CASE` que resuelve la marca canónica de una columna DENTRO de SQL, con el
 * mismo criterio (y el mismo último recurso) que `canonicalMarca()`. Devuelve
 * `NULL` para las marcas que no son Nike/Adidas/Puma.
 *
 * Se usa donde la consulta necesita agrupar o acotar por marca del lado del
 * motor (por ejemplo el bloque de franquicias de Adidas + Puma). Sigue siendo
 * preferible NO filtrar por marca cuando el resultado es un KPI: ver la regla
 * del encabezado.
 *
 * ⚠ `col` se interpola directo en el SQL: pasar SIEMPRE nombres de columna
 * literales del código, nunca input del usuario.
 */
export function canonicalMarcaSql(col: string): string {
  const norm = marcaNormSql(col)
  const letters = `REGEXP_REPLACE(${norm}, '[^A-Z0-9]+', '', 'g')`
  const exact = Object.entries(MARCA_ALIASES)
    .map(([alias, marca]) => `WHEN ${norm} = '${alias}' THEN '${marca}'`)
    .join(' ')
  const tokens = MARCA_TOKENS.map(
    ([needle, marca]) => `WHEN ${letters} LIKE '%${needle}%' THEN '${marca}'`,
  ).join(' ')
  return `CASE ${exact} ${tokens} END`
}

/** `'NIKE'` → `'nike'`, para las claves del JSON que consume la UI. */
export function marcaKey(marca: Marca): MarcaKey {
  return marca.toLowerCase() as MarcaKey
}

/** `'NIKE'` → `'Nike'`, para mostrar en la UI. */
export function marcaLabel(marca: Marca): string {
  return marca.charAt(0) + marca.slice(1).toLowerCase()
}
