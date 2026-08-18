/**
 * labels.ts — Agrupar por clave y elegir UNA etiqueta legible (una definición).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUÉ EXISTE ESTE ARCHIVO (dos bugs confirmados, no supuestos)
 * ─────────────────────────────────────────────────────────────────────────
 * Los scrapers no normalizan el casing de los campos de texto, así que el
 * MISMO valor de negocio llega escrito de varias formas y cualquier
 * `GROUP BY <columna>` lo parte en varias entidades. Medido contra el Postgres
 * local con el fixture del repo (70.000 filas):
 *
 *   psql -c "SELECT category_competitor, COUNT(*) FROM pricing_data
 *            GROUP BY 1 ORDER BY 1"
 *      'RUNNING'  35.113
 *      'Running'   6.508      <- la MISMA categoría, en otra fila
 *
 * Efecto en la UI: el `<select>` de categoría de "Top Franchises Nike"
 * ofrecía 'RUNNING' y 'Running' como dos opciones distintas, y elegir una
 * escondía el 16% del surtido que estaba escrito con la otra.
 *
 * El mismo patrón, más caro, en `scraper` (ver `lib/scrapers.ts`): tres
 * escrituras del mismo retailer daban tres filas de compliance.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * EL PROBLEMA DE LA ETIQUETA, Y EL CRITERIO ELEGIDO
 * ─────────────────────────────────────────────────────────────────────────
 * Agrupar es la mitad fácil. La otra mitad es: de las N formas en que el
 * scraper escribió el valor, ¿cuál se le muestra al usuario? Tiene que ser
 * DETERMINÍSTICA (la misma corrida dos veces da la misma etiqueta) y ESTABLE
 * (no cambia porque hoy entraron 3 filas más de una variante).
 *
 * Descartados a propósito:
 *   · "la variante más frecuente" — NO es estable: baila con los conteos de
 *     cada corrida, así que la etiqueta cambiaría sola.
 *   · "la primera alfabéticamente" — es estable pero elige mal: entre
 *     'Open Sports', 'OpenSports_AR' y 'opensports' la ordenación depende del
 *     collation de la base, no de qué se lee mejor.
 *   · `INITCAP()` — inventa una escritura que no está en los datos
 *     ('FOOTBALL/SOCCER' -> 'Football/Soccer') y pierde las siglas.
 *
 * ELEGIDO: `pickLabel()` puntúa las variantes OBSERVADAS y se queda con la
 * más legible, con un desempate total (ver la función). Depende sólo del
 * CONJUNTO de variantes, no de cuántas filas tiene cada una: mientras el
 * scraper no invente una escritura nueva, la etiqueta no se mueve.
 */

/** Espacios visibles e invisibles — mismo criterio que `lib/marca.ts`. */
const JS_SPACE_RE = /[\s\u00a0\u200b\u200c\u200d\ufeff]+/g
const SQL_SPACE_CLASS = String.raw`[\s\u00a0\u200b\u200c\u200d\ufeff]`

/**
 * Clave de agrupación de un texto libre: espacios colapsados, puntas
 * recortadas y todo a mayúsculas. `'Running'` y `'RUNNING'` caen en la misma
 * clave; `'FOOTBALL/SOCCER'` conserva la barra, que sí distingue categorías.
 */
export function labelKey(raw: string | null | undefined): string {
  if (!raw) return ''
  return raw.replace(JS_SPACE_RE, ' ').trim().toUpperCase()
}

/**
 * Fragmento SQL equivalente a `labelKey()`, para agrupar del lado del motor.
 *
 * ⚠ `col` se interpola directo en el SQL: pasar SIEMPRE nombres de columna
 * literales del código, nunca input del usuario.
 */
export function labelKeySql(col: string): string {
  return `UPPER(BTRIM(REGEXP_REPLACE(COALESCE(${col}, ''), '${SQL_SPACE_CLASS}+', ' ', 'g')))`
}

/** ¿El texto mezcla mayúsculas y minúsculas? ('Open Sports' sí, 'OPENSPORTS' no) */
function isMixedCase(text: string): boolean {
  return /[a-z]/.test(text) && /[A-Z]/.test(text)
}

/** ¿Tiene un separador de palabras interno? ('Open Sports' sí, 'OpenSports' no) */
function hasWordBreak(text: string): boolean {
  return /[A-Za-z0-9][ _-][A-Za-z0-9]/.test(text)
}

/**
 * Elige UNA etiqueta legible entre las variantes con que el scraper escribió
 * el mismo valor. Determinística y estable: sólo mira el conjunto de
 * variantes, nunca sus conteos.
 *
 * Se prefiere, en este orden:
 *   1. La que separa palabras       — 'Open Sports' antes que 'OpenSports'.
 *   2. La que mezcla mayús/minús    — 'Dexter' antes que 'DEXTER' o 'dexter'.
 *   3. La más larga                 — conserva la escritura más completa.
 *   4. El orden alfabético          — desempate total, para que NUNCA quede
 *                                     indefinida ante variantes empatadas.
 *
 * Verificado contra los 43 nombres de scraper del fixture: devuelve
 * 'Open Sports', 'Stock Center', 'Mercado Libre', 'Solo Deportes',
 * 'Digital Sport', 'Dexter', 'Dafiti', 'Grid', 'Moov', 'Sporting'.
 */
export function pickLabel(variants: readonly (string | null | undefined)[]): string {
  const clean = variants
    .map((v) => (v ?? '').replace(JS_SPACE_RE, ' ').trim())
    .filter((v) => v.length > 0)
  if (clean.length === 0) return ''

  // Sin duplicados: dos filas con la misma escritura no tienen que pesar más.
  const unique = Array.from(new Set(clean))

  return unique.reduce((best, candidate) => (isBetter(candidate, best) ? candidate : best))
}

/** `a` se lee mejor que `b`. Orden TOTAL: nunca devuelve "empate". */
function isBetter(a: string, b: string): boolean {
  const breakA = hasWordBreak(a) ? 1 : 0
  const breakB = hasWordBreak(b) ? 1 : 0
  if (breakA !== breakB) return breakA > breakB

  const caseA = isMixedCase(a) ? 1 : 0
  const caseB = isMixedCase(b) ? 1 : 0
  if (caseA !== caseB) return caseA > caseB

  if (a.length !== b.length) return a.length > b.length

  // Desempate final: alfabético con collation fija ('es'), para que dos
  // corridas sobre los mismos datos devuelvan SIEMPRE la misma etiqueta.
  return a.localeCompare(b, 'es') < 0
}
