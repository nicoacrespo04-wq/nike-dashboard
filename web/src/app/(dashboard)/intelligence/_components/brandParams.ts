import type { WindowKey } from '@/types/intelligence'
import { intParam, param } from './urlState'

/** Estado del panel de consumidor argentino. */

export const BRAND_COUNTRY = 'AR'

/**
 * Cuántos insights traer por tanda.
 *
 * `/api/brand/insights` acepta `limit` pero todavía NO acepta `offset`, y su
 * `total` es el largo de la página, no el del universo. Así que la única
 * paginación honesta que se puede hacer contra ese contrato es ampliar el
 * `limit` (el backend ordena por `signal_volume DESC`, así que ampliar agrega
 * al final sin reordenar lo ya visto). Cuando el endpoint acepte `offset` y
 * devuelva el total real, esto pasa a ser un `Pager` como el resto.
 */
export const BRAND_INSIGHTS_BATCH = 60

/** Cuántas señales de momentum y tópicos pedir (ambos son paneles fijos). */
export const BRAND_MOMENTUM_LIMIT = 60
export const BRAND_TOPICS_LIMIT = 40

/**
 * Ventana de comparación de toda la solapa (`?window=` del backend).
 *
 * Es una decisión de lectura, no un filtro de una tarjeta: cambia contra qué se
 * compara TODO lo que hay en pantalla (insights, momentum y tópicos). Por eso
 * vive en la URL y la maneja un solo control.
 */
export const WINDOW_OPTIONS: ReadonlyArray<{ value: WindowKey; label: string; hint: string }> = [
  { value: 'month', label: 'Mes', hint: 'Últimos 30 días contra los 30 anteriores.' },
  { value: 'quarter', label: 'Trimestre', hint: 'Últimos 90 días contra los 90 anteriores.' },
  { value: 'year', label: 'Año', hint: 'Últimos 365 días contra los 365 anteriores.' },
]

export const DEFAULT_WINDOW: WindowKey = 'month'

export function windowFromParam(raw: string): WindowKey {
  return WINDOW_OPTIONS.some((o) => o.value === raw) ? (raw as WindowKey) : DEFAULT_WINDOW
}

export interface BrandState {
  dimension: string
  min_confidence: string
  /** Ventana de comparación pedida. */
  window: WindowKey
  /** Cuántas tandas de `BRAND_INSIGHTS_BATCH` pidió el usuario. */
  batches: number
}

export interface BrandInsightsQuery {
  country: string
  dimension?: string
  min_confidence?: string
  window: WindowKey
  limit: number
  [key: string]: string | number | undefined
}

export function brandStateFromParams(
  searchParams: Record<string, string | string[] | undefined>,
): BrandState {
  return {
    dimension: param(searchParams, 'dim'),
    min_confidence: param(searchParams, 'conf'),
    window: windowFromParam(param(searchParams, 'win')),
    batches: Math.max(1, intParam(searchParams, 'batches', 1)),
  }
}

export function brandInsightsQueryFrom(state: BrandState): BrandInsightsQuery {
  return {
    country: BRAND_COUNTRY,
    dimension: state.dimension || undefined,
    min_confidence: state.min_confidence || undefined,
    window: state.window,
    limit: state.batches * BRAND_INSIGHTS_BATCH,
  }
}

export function brandInsightsQueryKey(query: BrandInsightsQuery): string {
  return JSON.stringify(query)
}
