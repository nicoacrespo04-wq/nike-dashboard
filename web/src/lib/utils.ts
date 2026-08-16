import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatPrice(value: number | null | undefined, currency = 'ARS'): string {
  if (value == null || isNaN(value)) return 'N/D'
  
  // Formato manual sin espacios: $123.456
  const rounded = Math.round(value)
  const formatted = rounded.toLocaleString('es-AR')
  
  return currency === 'ARS' ? `$${formatted}` : `${currency} ${formatted}`
}

/**
 * Formatea un ratio 0..1 (ej. `nike_visibility`) como porcentaje.
 * Devuelve `emptyLabel` ("N/D" por defecto) cuando no hay dato, para que la
 * tarjeta nunca quede vacía ni muestre un valor engañoso.
 *
 * ⚠ UN DECIMAL A PROPÓSITO, no es cosmética. `KPICard` decide si una métrica
 * está vacía con `isEmptyMetric()` de `components/ui/format.ts`, que trata la
 * cadena `'0%'` como placeholder de "sin dato" (está en su `EMPTY_TOKENS`).
 * Con redondeo a entero, una visibilidad real pero baja (0,004 → `'0%'`) se
 * renderizaba como "N/D — Sin datos disponibles", o sea el mismo síntoma que
 * el bug de `marca` pero por otra causa. Con un decimal el 0 real sale como
 * `'0,0%'`, que no es un token vacío, y "no hay dato" sigue siendo `N/D`.
 * (El arreglo de fondo — sacar `'0%'` de `EMPTY_TOKENS` — vive en
 * `components/ui/format.ts`, que es de otro dueño: queda como handoff.)
 */
export function formatRatioPct(
  value: number | string | null | undefined,
  emptyLabel = 'N/D'
): string {
  if (value === null || value === undefined || value === '') return emptyLabel
  const n = Number(value)
  if (!Number.isFinite(n)) return emptyLabel
  return `${(n * 100).toLocaleString('es-AR', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`
}

export function formatPct(value: number | null | undefined): string {
  if (value == null || isNaN(value)) return 'N/D'
  const pct = value * 100
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null || isNaN(value)) return '0'
  return new Intl.NumberFormat('es-AR').format(value)
}

export type BMLValue = 'BEAT' | 'MEET' | 'LOSE' | 'N/D' | 'NO_US_DATA' | string

export function getBMLColor(bml: BMLValue): string {
  switch (bml?.toUpperCase()) {
    case 'BEAT': return '#27AE60'  // Nike más barato (verde)
    case 'MEET': return '#F5A623'  // Precio similar (naranja)
    case 'LOSE': return '#E31837'  // Nike más caro (rojo)
    default:     return '#9B9B9B'
  }
}

export function getBMLBadgeClass(bml: BMLValue): string {
  switch (bml?.toUpperCase()) {
    case 'BEAT': return 'bml-beat'
    case 'MEET': return 'bml-meet'
    case 'LOSE': return 'bml-lose'
    default:     return 'bml-nd'
  }
}

export function getBMLLabel(bml: BMLValue): string {
  switch (bml?.toUpperCase()) {
    case 'BEAT': return 'BEAT — Nike más barato'
    case 'MEET': return 'MEET — Precio similar'
    case 'LOSE': return 'LOSE — Nike más caro'
    default:     return 'N/D'
  }
}

export function normalizeDivision(raw: string | null): string {
  if (!raw) return 'N/D'
  const r = raw.toUpperCase()
  if (r.includes('FOOT') || r === 'FW') return 'Footwear'
  if (r.includes('APP') || r === 'AP')  return 'Apparel'
  if (r.includes('EQUIP') || r === 'EQ') return 'Equipment'
  return raw
}

export function truncate(str: string, n: number): string {
  return str?.length > n ? str.slice(0, n - 1) + '…' : str
}
