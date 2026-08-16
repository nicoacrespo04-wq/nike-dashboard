import type { OpportunityQuery } from '@/lib/intelligence/api'
import { intParam, param } from './urlState'

/** Estado del Opportunity Center: de la URL a la query del backend. */

/** Tamaño de página: 24 llena tres columnas de 8 filas sin traer 200 tarjetas. */
export const OPPORTUNITIES_PAGE_SIZE = 24

export type GroupMode = 'none' | 'family' | 'severity'

export interface OpportunityState {
  family: string
  severity: string
  opportunity_type: string
  min_importance: number
  group: GroupMode
  page: number
}

export const EMPTY_OPPORTUNITY_STATE: OpportunityState = {
  family: '',
  severity: '',
  opportunity_type: '',
  min_importance: 0,
  group: 'none',
  page: 0,
}

function groupOf(value: string): GroupMode {
  return value === 'family' || value === 'severity' ? value : 'none'
}

export function opportunityStateFromParams(
  searchParams: Record<string, string | string[] | undefined>,
): OpportunityState {
  return {
    family: param(searchParams, 'family'),
    severity: param(searchParams, 'severity'),
    opportunity_type: param(searchParams, 'type'),
    min_importance: intParam(searchParams, 'min', 0),
    group: groupOf(param(searchParams, 'group')),
    page: intParam(searchParams, 'page', 0),
  }
}

/** `group` no viaja al backend: es cómo se ordena la página ya recibida. */
export function opportunityQueryFrom(state: OpportunityState): OpportunityQuery {
  return {
    family: state.family || undefined,
    severity: state.severity || undefined,
    opportunity_type: state.opportunity_type || undefined,
    min_importance: state.min_importance || undefined,
    limit: OPPORTUNITIES_PAGE_SIZE,
    offset: state.page * OPPORTUNITIES_PAGE_SIZE,
  }
}

export function opportunityQueryKey(query: OpportunityQuery): string {
  return JSON.stringify(query)
}

export function activeOpportunityFilters(state: OpportunityState): number {
  return (
    (state.family ? 1 : 0) +
    (state.severity ? 1 : 0) +
    (state.opportunity_type ? 1 : 0) +
    (state.min_importance > 0 ? 1 : 0)
  )
}
