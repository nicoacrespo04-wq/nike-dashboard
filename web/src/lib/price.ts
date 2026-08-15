/**
 * price.ts — Sanitización de precios (ARS) compartida por las API routes.
 *
 * CONTEXTO / POR QUÉ EXISTE ESTE ARCHIVO
 * ──────────────────────────────────────
 * Los scrapers originales no viven en este repo y la tabla `pricing_data`
 * llega con dos tipos de basura conocidos:
 *
 *  1) PRECIO INFLADO POR CUOTAS.
 *     El parser del scraper se comió el "N cuotas sin interés" y guardó el
 *     TOTAL financiado en lugar del precio unitario. Los casos reportados son
 *     exactamente 8x el precio real:
 *         2.639.992 = 329.999 x 8
 *         2.399.992 = 299.999 x 8
 *         2.079.992 = 259.999 x 8
 *     Se detecta porque el valor cae fuera del rango plausible y, dividido por
 *     la cantidad de cuotas declarada en `cuotas_competitor`, vuelve a caer
 *     dentro del rango.
 *
 *  2) PRECIO EN 0.
 *     Filas con `competitor_final_price = 0`. NO son "gratis": son dato
 *     ausente. Si se dejan pasar contaminan compliance, violaciones de PVP
 *     (aparecen como -100%), gaps, promedios, markdown y BML.
 *
 * CRITERIO ELEGIDO (heurística, ajustable)
 * ────────────────────────────────────────
 *  - `<= 0`  → dato ausente (null). Nunca se muestra como "$0".
 *  - Fuera de [PRICE_MIN_ARS, PRICE_MAX_ARS] → sospechoso.
 *      · Si conocemos las cuotas y `precio / cuotas` cae dentro del rango,
 *        asumimos el bug de cuotas y corregimos dividiendo.
 *      · Si no, se descarta (null). Preferimos "N/D" antes que un número
 *        que el negocio no puede usar.
 *  - El rango es deliberadamente ancho: cubre desde una media deportiva hasta
 *     un botín premium en ARS. Se ajusta por env var sin tocar código.
 *
 * ENV VARS (server-side)
 * ──────────────────────
 *   PRICE_MIN_ARS    (default 1000)      piso plausible en ARS
 *   PRICE_MAX_ARS    (default 2000000)   techo plausible en ARS
 *   PRICE_MAX_CUOTAS (default 24)        máximo de cuotas que aceptamos parsear
 *
 * El mismo criterio está replicado en `db/load_csv.py` (capa de carga). Si se
 * cambia acá, cambiarlo allá también.
 */

function envNumber(raw: string | undefined, fallback: number): number {
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : fallback
}

export const PRICE_MIN_ARS = envNumber(process.env.PRICE_MIN_ARS, 1_000)
export const PRICE_MAX_ARS = envNumber(process.env.PRICE_MAX_ARS, 2_000_000)
export const PRICE_MAX_CUOTAS = envNumber(process.env.PRICE_MAX_CUOTAS, 24)

/** Convierte a number lo que venga de `pg` (NUMERIC llega como string). */
export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

/**
 * ¿El valor es un precio que el negocio puede usar?
 * `<= 0` y los outliers del bug de cuotas devuelven false.
 */
export function isPlausiblePrice(value: unknown): boolean {
  const n = toNumber(value)
  if (n === null) return false
  return n >= PRICE_MIN_ARS && n <= PRICE_MAX_ARS
}

/**
 * Extrae la cantidad de cuotas de textos tipo "8 cuotas sin interés",
 * "12x sin interés", "3 CUOTAS", "6". Devuelve null si no se puede inferir.
 */
export function parseCuotas(raw: unknown): number | null {
  if (raw === null || raw === undefined) return null
  const n = typeof raw === 'number' ? raw : Number(String(raw).match(/\d+/)?.[0])
  if (!Number.isFinite(n)) return null
  const cuotas = Math.trunc(n)
  if (cuotas < 2 || cuotas > PRICE_MAX_CUOTAS) return null
  return cuotas
}

export interface SanitizePriceOptions {
  /** Valor crudo de `cuotas_competitor` / `cuotas_nike` para la corrección x N. */
  cuotas?: unknown
}

/**
 * Devuelve el precio saneado, o `null` si el dato no es utilizable.
 * `null` se renderiza como "N/D" vía `formatPrice()`; nunca como "$0".
 */
export function sanitizePrice(value: unknown, opts: SanitizePriceOptions = {}): number | null {
  const n = toNumber(value)
  if (n === null) return null

  // Bug 2: 0 (o negativos) = dato ausente, no "gratis".
  if (n <= 0) return null

  if (isPlausiblePrice(n)) return n

  // Bug 1: precio inflado por cuotas. Sólo corregimos si las cuotas están
  // declaradas y la división devuelve un precio plausible — no adivinamos el
  // divisor, porque varios divisores podrían "funcionar" y sería inventar dato.
  const cuotas = parseCuotas(opts.cuotas)
  if (cuotas) {
    const unit = n / cuotas
    if (isPlausiblePrice(unit)) return Math.round(unit * 100) / 100
  }

  // Fuera de rango y sin forma de corregirlo → dato ausente.
  return null
}

/**
 * Sanea, in-place sobre una copia, los campos de precio de una fila devuelta
 * por `pg`. Pensado para usarse en las API routes antes de responder.
 */
export function sanitizeRowPrices<T extends Record<string, unknown>>(
  row: T,
  priceFields: readonly string[],
  cuotasField?: string
): T {
  const out: Record<string, unknown> = { ...row }
  const cuotas = cuotasField ? row[cuotasField] : undefined
  for (const field of priceFields) {
    if (field in out) out[field] = sanitizePrice(out[field], { cuotas })
  }
  return out as T
}

/** Igual que `sanitizeRowPrices` pero para un array de filas. */
export function sanitizeRowsPrices<T extends Record<string, unknown>>(
  rows: T[],
  priceFields: readonly string[],
  cuotasField?: string
): T[] {
  return rows.map((r) => sanitizeRowPrices(r, priceFields, cuotasField))
}

/**
 * Fragmento SQL que devuelve la columna sólo si el precio es plausible, y
 * NULL en caso contrario (incluye el caso `<= 0` del bug 2).
 *
 * Usarlo en TODAS las queries de pricing (compliance, violaciones, gaps,
 * promedios, markdown y BML) para que un precio sucio nunca participe de un
 * agregado ni llegue a la UI como si fuera válido.
 *
 * ⚠ `col` se interpola directo en el SQL: pasar SIEMPRE nombres de columna
 * literales del código, nunca input del usuario.
 */
export function validPriceSql(col: string): string {
  return `(CASE WHEN ${col} >= ${PRICE_MIN_ARS} AND ${col} <= ${PRICE_MAX_ARS} THEN ${col} END)`
}

/** Predicado booleano equivalente a `validPriceSql(col) IS NOT NULL`. */
export function isValidPriceSql(col: string): string {
  return `(${col} IS NOT NULL AND ${col} >= ${PRICE_MIN_ARS} AND ${col} <= ${PRICE_MAX_ARS})`
}
