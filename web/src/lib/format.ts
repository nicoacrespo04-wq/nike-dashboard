/**
 * Formateo de números, precios, porcentajes y fechas — un solo lugar.
 *
 * Antes había dos juegos de formatters: los del dashboard (`lib/utils.ts`:
 * `formatPrice`, `formatPct`, `formatNumber`) y los del Decision Engine
 * (`num`, `dec`, `money`, `pct`…). Convivían con criterios distintos para
 * "sin dato" (`N/D` vs `—`) y para moneda.
 *
 * Regla unificada:
 *  - El texto de "sin dato" es SIEMPRE `N/D` (constante `ND`).
 *  - El precio lo formatea `formatPrice` de `lib/utils`, para que un ARS se
 *    vea igual en Competencia y en Retail Media.
 *
 * `lib/utils.ts` conserva sus exports (las 4 solapas viejas los importan de
 * ahí); acá se agregan los que faltaban y se reutiliza su implementación.
 */

import { formatPrice } from '@/lib/utils'

/** Texto canónico para "no hay dato" en toda la aplicación. */
export const ND = 'N/D'

const NUMBER_AR = new Intl.NumberFormat('es-AR')
const DECIMAL_AR = new Intl.NumberFormat('es-AR', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

function missing(value: number | null | undefined): value is null | undefined {
  return value === null || value === undefined || !Number.isFinite(value)
}

/** Entero con separador de miles es-AR. */
export function num(value: number | null | undefined): string {
  if (missing(value)) return ND
  return NUMBER_AR.format(value)
}

/** Decimal con `digits` cifras (1 por defecto). */
export function dec(value: number | null | undefined, digits = 1): string {
  if (missing(value)) return ND
  if (digits === 1) return DECIMAL_AR.format(value)
  return value.toFixed(digits)
}

/** Score 0..100 → "87,4". */
export function score(value: number | null | undefined): string {
  if (missing(value)) return ND
  return DECIMAL_AR.format(value)
}

/** Fracción 0..1 → "72%". */
export function pctFromFraction(value: number | null | undefined, digits = 0): string {
  if (missing(value)) return ND
  return `${(value * 100).toFixed(digits)}%`
}

/** Valor ya expresado en 0..100 → "72%". */
export function pct(value: number | null | undefined, digits = 0): string {
  if (missing(value)) return ND
  return `${value.toFixed(digits)}%`
}

/**
 * Precio. Delega en `formatPrice` para que la moneda se vea idéntica en todo
 * el dashboard (`$123.456`, sin espacios raros).
 */
export function money(value: number | null | undefined, currency = 'ARS'): string {
  if (missing(value)) return ND
  return formatPrice(value, currency)
}

/** Número con signo explícito: "+1,25" / "-0,40". */
export function signed(value: number | null | undefined, digits = 1): string {
  if (missing(value)) return ND
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

/** Fecha ISO (o `YYYY-MM-DD`) → "10 ago 2026". */
export function date(value: string | null | undefined): string {
  if (!value) return ND
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00` : value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString('es-AR', { day: '2-digit', month: 'short', year: 'numeric' })
}

/** Rango de fechas de un período. */
export function period(start: string | null | undefined, end: string | null | undefined): string {
  if (!start && !end) return ND
  return `${date(start)} → ${date(end)}`
}

/** Recorta texto largo agregando elipsis. */
export function truncateText(value: string | null | undefined, max = 140): string {
  if (!value) return ''
  return value.length <= max ? value : `${value.slice(0, max - 1).trimEnd()}…`
}

/** Texto no vacío, o `N/D`. */
export function text(value: string | null | undefined): string {
  return value && value.trim() !== '' ? value : ND
}
