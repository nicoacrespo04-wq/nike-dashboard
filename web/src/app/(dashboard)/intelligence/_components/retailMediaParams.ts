import type { RetailMediaQuery } from '@/lib/intelligence/api'
import { intParam, param } from './urlState'

/** Estado de Retail Media: de la URL a la query del backend. */

export const RETAIL_MEDIA_PAGE_SIZE = 25

export interface RetailMediaState {
  recommendation: string
  min_score: number
  page: number
}

export const EMPTY_RETAIL_MEDIA_STATE: RetailMediaState = {
  recommendation: '',
  min_score: 0,
  page: 0,
}

export function retailMediaStateFromParams(
  searchParams: Record<string, string | string[] | undefined>,
): RetailMediaState {
  return {
    recommendation: param(searchParams, 'rec'),
    min_score: intParam(searchParams, 'min', 0),
    page: intParam(searchParams, 'page', 0),
  }
}

export function retailMediaQueryFrom(state: RetailMediaState): RetailMediaQuery {
  return {
    recommendation: state.recommendation || undefined,
    min_score: state.min_score || undefined,
    limit: RETAIL_MEDIA_PAGE_SIZE,
    offset: state.page * RETAIL_MEDIA_PAGE_SIZE,
  }
}

export function retailMediaQueryKey(query: RetailMediaQuery): string {
  return JSON.stringify(query)
}
