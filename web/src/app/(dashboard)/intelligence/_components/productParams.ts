import type { ProductQuery } from '@/lib/intelligence/api'
import { intParam, param } from './urlState'

/**
 * Estado del Product Explorer: de la URL a la query del backend.
 *
 * Vive en un módulo neutro (ni cliente ni servidor) porque las dos mitades
 * tienen que coincidir carácter por carácter: el Server Component resuelve la
 * primera página con esta misma query y el cliente compara la firma para saber
 * que no necesita repetir el pedido.
 */

/** Tamaño de página. El backend acepta hasta 500; 40 llena la grilla sin pasarse. */
export const PRODUCTS_PAGE_SIZE = 40

export interface ProductExplorerState {
  brand: string
  franchise: string
  category: string
  sport: string
  use_case: string
  gender: string
  price_band: string
  country: string
  retailer: string
  q: string
  page: number
}

export const EMPTY_PRODUCT_STATE: ProductExplorerState = {
  brand: '',
  franchise: '',
  category: '',
  sport: '',
  use_case: '',
  gender: '',
  price_band: '',
  country: '',
  retailer: '',
  q: '',
  page: 0,
}

/** Claves de filtro (todo menos la página), en el orden en que se pintan. */
export const PRODUCT_FILTER_KEYS = [
  'brand',
  'franchise',
  'category',
  'sport',
  'use_case',
  'gender',
  'price_band',
  'country',
  'retailer',
] as const

export type ProductFilterKey = (typeof PRODUCT_FILTER_KEYS)[number]

export function productStateFromParams(
  searchParams: Record<string, string | string[] | undefined>,
): ProductExplorerState {
  const state = { ...EMPTY_PRODUCT_STATE, page: intParam(searchParams, 'page', 0) }
  for (const key of PRODUCT_FILTER_KEYS) state[key] = param(searchParams, key)
  state.q = param(searchParams, 'q')
  return state
}

/** Query real que viaja al backend: filtros + paginación, nada del lado cliente. */
export function productQueryFrom(state: ProductExplorerState): ProductQuery {
  return {
    brand: state.brand || undefined,
    franchise: state.franchise || undefined,
    category: state.category || undefined,
    sport: state.sport || undefined,
    use_case: state.use_case || undefined,
    gender: state.gender || undefined,
    price_band: state.price_band || undefined,
    country: state.country || undefined,
    retailer: state.retailer ? Number(state.retailer) : undefined,
    q: state.q || undefined,
    limit: PRODUCTS_PAGE_SIZE,
    offset: state.page * PRODUCTS_PAGE_SIZE,
  }
}

/** Firma de la query, compartida por el servidor y el hook del cliente. */
export function productQueryKey(query: ProductQuery): string {
  return JSON.stringify(query)
}

export function activeProductFilters(state: ProductExplorerState): number {
  return PRODUCT_FILTER_KEYS.filter((key) => state[key] !== '').length + (state.q ? 1 : 0)
}
