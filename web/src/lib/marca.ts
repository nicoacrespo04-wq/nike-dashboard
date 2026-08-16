/**
 * marca.ts — Normalización de la columna `marca` (una sola definición).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUÉ EXISTE ESTE ARCHIVO (bug confirmado, no supuesto)
 * ─────────────────────────────────────────────────────────────────────────
 * Los KPIs de Share of Shelf (VISIBILIDAD NIKE / ADIDAS / PUMA) mostraban
 * `N/D` — "Sin datos disponibles" — MIENTRAS las barras de "Share of Shelf por
 * retailer", que salen de la MISMA tabla y de la MISMA request, mostraban
 * porcentajes correctos.
 *
 * Se reprodujo con un Postgres local cargado con `db/schema_shelf.sql` +
 * `db/retail_media_search.csv`, aplicando a `marca` la suciedad que el propio
 * repo documenta como real (`backend/tools/generate_scale_fixture.py`:
 * `nike_casings = ("Nike", "NIKE", "nike", " Nike ")`, `BRAND_CASINGS['ADIDAS']`
 * incluye `" Adidas "`). Resultado del experimento:
 *
 *     BARRAS  (INITCAP(marca), sin WHERE de marca) →  Adidas 49.2 · Nike 39 · Puma 10.2   ✅
 *     GLOBAL  (UPPER(marca) = ANY($1))             →  []  →  nike/adidas/puma = null      ❌
 *
 * Es decir:
 *
 *   1. **NO era el binding de `= ANY($1)`.** Se verificó aparte contra el mismo
 *      Postgres con `pg`: con datos limpios `UPPER(marca) = ANY($1)` y un
 *      `IN ('NIKE','ADIDAS','PUMA')` literal devuelven exactamente las mismas
 *      filas. El driver serializa el array de JS a `text[]` sin problema.
 *
 *   2. **Era el `marca` con espacios invisibles.** `' Nike '` sobrevive a
 *      `INITCAP` (que sólo agrupa, no filtra: devuelve `' Nike '` y la barra se
 *      dibuja igual porque HTML colapsa el espacio al renderizar), pero
 *      `UPPER(' Nike ') = ' NIKE '` NO matchea `'NIKE'`, así que el WHERE del
 *      bloque global se queda con CERO filas y los tres KPIs quedan en null.
 *
 *   3. **La causa de fondo es de diseño, no de datos**: filtrar por marca en el
 *      `WHERE` *falla en silencio* (cero filas → null → N/D), mientras agrupar
 *      *falla mostrando lo que hay*. Por eso las barras "aguantaban" la
 *      suciedad y los KPIs no. La regla que queda: **agrupar por marca
 *      normalizada y resolver la marca canónica en TypeScript; nunca filtrar
 *      por un literal de marca.**
 *
 * Se arregló una vez el casing (`Puma` vs `PUMA`) y por eso pasó desapercibido:
 * `UPPER()` cubre el casing pero no los espacios. Acá se cubren los dos, más
 * los espacios que no se ven (NBSP U+00A0,
 * zero-width U+200B/U+200C/U+200D y BOM U+FEFF), que son los que sobreviven a un `TRIM()` normal en algunos
 * clientes y a un `.strip()` de Python según cómo se haya cargado la tabla.
 */

/**
 * Clase de caracteres "espacio" que se normalizan, en sintaxis de regex de
 * Postgres. Incluye los invisibles que un `BTRIM(col)` pelado NO saca en todos
 * los casos y que son justo los que rompían el filtro.
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
 * Se usa sólo cuando una marca esperada no aparece (ver los endpoints).
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
 * Alias → marca canónica. Mismo criterio que `backend/app/ingest/mapping.py`
 * (`brand_aliases`) y `db/normalize_marca.py`: sub-marcas y variantes que el
 * scraper trae como marca propia pero que el negocio lee como la marca madre.
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
 * Marca canónica de un valor crudo, o `null` si no es una de las tres que el
 * dashboard compara. Resolver acá (y no en el `WHERE`) es lo que hace que un
 * valor sucio nuevo degrade el dato de UNA marca en vez de vaciar el bloque
 * entero.
 */
export function canonicalMarca(raw: string | null | undefined): Marca | null {
  const norm = normalizeMarca(raw)
  if (!norm) return null
  return MARCA_ALIASES[norm] ?? null
}

/** `'NIKE'` → `'nike'`, para las claves del JSON que consume la UI. */
export function marcaKey(marca: Marca): MarcaKey {
  return marca.toLowerCase() as MarcaKey
}

/** `'NIKE'` → `'Nike'`, para mostrar en la UI. */
export function marcaLabel(marca: Marca): string {
  return marca.charAt(0) + marca.slice(1).toLowerCase()
}
