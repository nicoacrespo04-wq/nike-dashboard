/**
 * missing.ts — Los "nulos disfrazados" que el scraper escribe como si fueran
 * datos, en un solo lugar.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUÉ EXISTE ESTE ARCHIVO (bug confirmado, no supuesto)
 * ─────────────────────────────────────────────────────────────────────────
 * Los scrapers no viven en este repo y, cuando no pueden leer un campo de
 * texto, no dejan la celda vacía: escriben un marcador. Los que llegan a
 * `pricing_data` están enumerados en el generador del fixture
 * (`backend/tools/generate_scale_fixture.py::MISSING_TOKENS`, que replica la
 * forma de los datos reales) y en el saneador del ingest
 * (`backend/app/ingest/mapping.py::_NULLISH`, que es el criterio canónico):
 *
 *     None · '' · 'N/A' · '#N/A' · 'nan' · '-' · 's/d' · 'NULL' · …
 *
 * Como son strings, cualquier `GROUP BY` los trata como un valor más. Medido
 * contra el Postgres local cargado con ese fixture (70.000 filas):
 *
 *     franchise_competitor = '-'    → 760 filas
 *     franchise_competitor = 's/d'  → 682 filas
 *
 * o sea que `'-'` y `'s/d'` entraban al listado de franquicias por encima de
 * `Pegasus` (941) o `Vomero` (894) — el negocio veía "las dos franquicias más
 * grandes de la competencia" y no eran franquicias. Lo mismo en `silueta`
 * (714 + 702 filas) y en `category_competitor` (715 + 631).
 *
 * Peor todavía en una comparación entre marcas: `'-'` y `'s/d'` son los ÚNICOS
 * valores de franquicia que existen a la vez del lado de Nike y del lado de la
 * competencia, así que cualquier match por nombre "acierta" exactamente ahí y
 * en ningún otro lado.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * CRITERIO
 * ─────────────────────────────────────────────────────────────────────────
 * Un marcador NO se convierte en un valor propio ni se rellena con nada: la
 * fila queda con el dato AUSENTE. Según el caso se la agrupa como "Sin
 * franquicia" (el surtido se sigue contando) o se la deja fuera del análisis
 * (cuando el campo es parte de la clave del segmento y sin él no se puede
 * ubicar la fila). Lo que nunca se hace es mostrar `'s/d'` como si fuera un
 * dato: para eso está `MISSING_LABEL`.
 *
 * La comparación es case-insensitive y con `BTRIM`, porque el mismo marcador
 * llega como `'S/D'`, `'s/d'` y `' - '`.
 */

/**
 * Marcadores de ausencia, ya normalizados (minúsculas, sin espacios en las
 * puntas). Copia de `_NULLISH` del ingest más los que agrega el fixture.
 * El string vacío está incluido: `''` y `NULL` son el mismo caso.
 */
export const MISSING_PLACEHOLDERS: readonly string[] = [
  '',
  '-',
  '--',
  '---',
  'n/a',
  '#n/a',
  'na',
  'n/d',
  'nd',
  's/d',
  'sd',
  'sin dato',
  'sin datos',
  'nan',
  'null',
  'none',
  '#value!',
  '#ref!',
] as const

/** Lo que se muestra en la UI cuando el dato no está. Nunca un 0 ni un guion. */
export const MISSING_LABEL = 'N/D'

/** `('', '-', 's/d', …)` listo para interpolar en un `IN (...)` de SQL. */
const PLACEHOLDER_SQL_LIST = MISSING_PLACEHOLDERS.map((p) => `'${p.replace(/'/g, "''")}'`).join(', ')

/** Normaliza igual que `isPresentSql()` lo hace en SQL. */
function normalize(raw: string | null | undefined): string {
  if (raw === null || raw === undefined) return ''
  return raw.trim().toLowerCase()
}

/** `true` si el valor es un marcador de ausencia (o directamente no está). */
export function isMissing(raw: string | null | undefined): boolean {
  return MISSING_PLACEHOLDERS.includes(normalize(raw))
}

/** `true` si el valor es un dato de verdad. Complemento de `isMissing()`. */
export function isPresent(raw: string | null | undefined): boolean {
  return !isMissing(raw)
}

/**
 * Predicado SQL: `TRUE` sólo si la columna trae un dato de verdad.
 * Cubre `NULL`, el string vacío y todos los marcadores, con el mismo
 * `BTRIM` + minúsculas que `isMissing()` en TypeScript.
 *
 *     WHERE ${isPresentSql('silueta')}
 *
 * ⚠ `col` se interpola directo en el SQL: pasar SIEMPRE nombres de columna
 * literales del código, nunca input del usuario.
 */
export function isPresentSql(col: string): string {
  return `(${col} IS NOT NULL AND LOWER(BTRIM(${col})) NOT IN (${PLACEHOLDER_SQL_LIST}))`
}

/** Negación de `isPresentSql()`, para contar cuánto dato falta y poder decirlo. */
export function isMissingSql(col: string): string {
  return `(${col} IS NULL OR LOWER(BTRIM(${col})) IN (${PLACEHOLDER_SQL_LIST}))`
}

/**
 * Expresión SQL que devuelve el valor limpio o `NULL` si es un marcador.
 * Útil para agrupar sin arrastrar el marcador como si fuera una categoría.
 *
 * ⚠ Misma advertencia de interpolación que `isPresentSql()`.
 */
export function presentOrNullSql(col: string): string {
  return `(CASE WHEN ${isPresentSql(col)} THEN BTRIM(${col}) END)`
}
