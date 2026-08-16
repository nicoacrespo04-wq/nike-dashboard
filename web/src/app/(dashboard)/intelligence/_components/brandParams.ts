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
export const BRAND_MOMENTUM_LIMIT = 30
export const BRAND_TOPICS_LIMIT = 40

export interface BrandState {
  dimension: string
  min_confidence: string
  /** Cuántas tandas de `BRAND_INSIGHTS_BATCH` pidió el usuario. */
  batches: number
}

export interface BrandInsightsQuery {
  country: string
  dimension?: string
  min_confidence?: string
  limit: number
  [key: string]: string | number | undefined
}

export function brandStateFromParams(
  searchParams: Record<string, string | string[] | undefined>,
): BrandState {
  return {
    dimension: param(searchParams, 'dim'),
    min_confidence: param(searchParams, 'conf'),
    batches: Math.max(1, intParam(searchParams, 'batches', 1)),
  }
}

export function brandInsightsQueryFrom(state: BrandState): BrandInsightsQuery {
  return {
    country: BRAND_COUNTRY,
    dimension: state.dimension || undefined,
    min_confidence: state.min_confidence || undefined,
    limit: state.batches * BRAND_INSIGHTS_BATCH,
  }
}

export function brandInsightsQueryKey(query: BrandInsightsQuery): string {
  return JSON.stringify(query)
}
