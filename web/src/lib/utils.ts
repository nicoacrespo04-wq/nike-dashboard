import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatPrice(value: number | null | undefined, currency = 'ARS'): string {
  if (value == null || isNaN(value)) return 'N/D'
  return new Intl.NumberFormat('es-AR', {
    style: 'currency',
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
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
    case 'BEAT': return '#E31837'
    case 'MEET': return '#F5A623'
    case 'LOSE': return '#27AE60'
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
    case 'BEAT': return 'BEAT — Comp. más barata'
    case 'MEET': return 'MEET — Precio similar'
    case 'LOSE': return 'LOSE — Nike más barata'
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
