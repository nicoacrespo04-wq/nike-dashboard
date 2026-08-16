import type { ProductQuery } from '@/lib/intelligence/api'
import { intParam, param } from './urlState'

/** Estado de Competitive Matches: de la URL a las queries del backend. */

/** Cuántos productos foco entran en el selector de la izquierda por página. */
export const MATCH_PRODUCTS_PAGE_SIZE = 25

/**
 * Cuántos productos pedir cuando NO se pudo resolver la marca foco.
 *
 * Es el camino degradado: sin filtro `brand` hay que traer un lote grande y
 * quedarse con los `is_focus === 1` en el cliente — exactamente lo que esta
 * pantalla hacía siempre. Se mantiene sólo como red de seguridad.
 */
export const MATCH_FALLBACK_LIMIT = 300

/** Cuántos competidores rankear por producto. */
export const MATCH_RANKING_LIMIT = 10

export interface MatchesState {
  q: string
  page: number
  selectedId: number | null
}

export function matchesStateFromParams(
  searchParams: Record<string, string | string[] | undefined>,
): MatchesState {
  const raw = param(searchParams, 'product')
  const parsed = Number(raw)
  return {
    q: param(searchParams, 'q'),
    page: intParam(searchParams, 'page', 0),
    selectedId: raw !== '' && Number.isFinite(parsed) && parsed > 0 ? parsed : null,
  }
}

/**
 * Query del selector de productos.
 *
 * Con `focusBrand` el filtrado es del backend y se pagina de verdad; sin él
 * se cae al lote grande + filtro en el cliente.
 */
export function matchProductsQueryFrom(
  state: Pick<MatchesState, 'q' | 'page'>,
  focusBrand: string | null,
): ProductQuery {
  if (!focusBrand) {
    return { q: state.q || undefined, limit: MATCH_FALLBACK_LIMIT }
  }
  return {
    brand: focusBrand,
    q: state.q || undefined,
    limit: MATCH_PRODUCTS_PAGE_SIZE,
    offset: state.page * MATCH_PRODUCTS_PAGE_SIZE,
  }
}

export function matchProductsQueryKey(query: ProductQuery): string {
  return JSON.stringify(query)
}
