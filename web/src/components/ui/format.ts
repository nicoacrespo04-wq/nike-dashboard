/**
 * Formateo defensivo para datos sucios.
 *
 * El pipeline de scraping entrega precios en 0, negativos o absurdos. La UI
 * nunca debe mostrar "$0" como si fuera un precio real: eso se lee como
 * "el producto es gratis" en vez de "no tenemos el dato".
 *
 * Regla única: si el valor no es un precio plausible → `N/D`.
 *
 * RELACIÓN CON `lib/price.ts`
 * ───────────────────────────
 * La sanitización *autoritativa* (rango plausible, corrección del bug de
 * cuotas) vive server-side en `lib/price.ts` y corre en las API routes. Esto de
 * acá es la **última línea de defensa en el render**: deliberadamente más
 * permisiva, para no ocultar un valor que la capa de datos ya validó, pero
 * suficiente para que un `0` o un `NaN` que se escape nunca se pinte como
 * "$0".
 */

import { formatPrice } from '@/lib/utils'

/** Texto canónico para "no hay dato". Se usa en toda la UI. */
export const ND = 'N/D'

/** Techo de render. Muy por encima del techo de negocio (`PRICE_MAX_ARS`). */
export const MAX_PLAUSIBLE_PRICE = 50_000_000

/** Strings que las páginas ya usan como placeholder de "sin dato". */
const EMPTY_TOKENS = new Set(['', '-', '—', '–', 'n/d', 'nd', 'n/a', 'na', 'null', 'undefined', 'nan', '$0', '$nan', '0%', 'nan%'])

/** `true` si el número sirve como precio mostrable. */
export function isPlausiblePrice(value: number | null | undefined): value is number {
  if (value == null) return false
  const n = Number(value)
  return Number.isFinite(n) && n > 0 && n <= MAX_PLAUSIBLE_PRICE
}

/**
 * Precio formateado, o `N/D` si el dato es nulo, cero, negativo o absurdo.
 * Envuelve `formatPrice` de lib/utils (que ya maneja null/NaN) agregando el
 * filtro de valores basura.
 */
export function formatPriceSafe(value: number | string | null | undefined): string {
  const n = typeof value === 'string' ? Number(value) : value
  if (!isPlausiblePrice(n ?? null)) return ND
  return formatPrice(n as number)
}

/**
 * Porcentaje formateado a partir de una **fracción** (0.12 → "+12,0%").
 * Devuelve `N/D` si no hay dato.
 */
export function formatPctSafe(fraction: number | string | null | undefined, digits = 1): string {
  const n = typeof fraction === 'string' ? Number(fraction) : fraction
  if (n == null || !Number.isFinite(n)) return ND
  const pct = n * 100
  return `${pct > 0 ? '+' : ''}${pct.toFixed(digits)}%`
}

/** Entero con separador de miles es-AR, o `N/D`. */
export function formatCountSafe(value: number | string | null | undefined): string {
  const n = typeof value === 'string' ? Number(value) : value
  if (n == null || !Number.isFinite(n)) return ND
  return Math.round(n).toLocaleString('es-AR')
}

/** Texto no vacío, o `N/D`. */
export function formatTextSafe(value: string | null | undefined): string {
  const t = value?.trim()
  if (!t || EMPTY_TOKENS.has(t.toLowerCase())) return ND
  return t
}

/**
 * ¿Este valor de KPI debe renderizarse como "sin dato"?
 *
 * Detecta tanto nulos reales como los placeholders que las páginas ya pasan
 * (`'—'`, `'N/D'`, `'$0'`, `'0%'`…), para que las tarjetas dejen de verse rotas
 * sin necesidad de tocar las páginas.
 */
export function isEmptyMetric(value: unknown): boolean {
  if (value == null) return true
  if (typeof value === 'number') return !Number.isFinite(value)
  if (typeof value === 'string') {
    const t = value.trim()
    if (!t) return true
    return EMPTY_TOKENS.has(t.toLowerCase())
  }
  return false
}
