/**
 * Cliente HTTP tipado del Competitive & Consumer Intelligence Decision Engine.
 *
 * Reglas:
 *  - Toda llamada pasa por acá. Ninguna página hace `fetch` suelto.
 *  - El browser NUNCA le pega al :8000. Pega a `/api/intelligence/...`, que es
 *    el proxy interno (`app/api/intelligence/[...path]/route.ts`): mismo
 *    origen, sin CORS, detrás del login de NextAuth.
 *  - Los errores se normalizan a `ApiError` para que la UI muestre siempre
 *    algo útil (el proxy responde 503 con un mensaje accionable si el backend
 *    no está levantado).
 *  - El backend responde 200 incluso con tablas vacías: los estados vacíos son
 *    un caso normal, no un error.
 */

import type {
  BrandInsightsResponse,
  HealthResponse,
  MatchDetail,
  MatchListResponse,
  MomentumResponse,
  OpportunityListResponse,
  OverviewResponse,
  ProductDetail,
  ProductFilters,
  ProductListResponse,
  ProductMatchesResponse,
  RetailMediaResponse,
  ScoringConfig,
  TopicsResponse,
} from '@/types/intelligence'

/** Prefijo del proxy interno. No es la URL del backend: esa vive server-side. */
export const API_BASE = '/api/intelligence'

export class ApiError extends Error {
  readonly status: number
  readonly path: string

  constructor(message: string, status: number, path: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.path = path
  }
}

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue>

function buildUrl(path: string, params?: QueryParams): string {
  const search = new URLSearchParams()
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`
}

async function request<T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> {
  const url = buildUrl(path, params)

  let response: Response
  try {
    response = await fetch(url, { signal, headers: { Accept: 'application/json' }, cache: 'no-store' })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(
      'No se pudo contactar el dashboard. Revisá la conexión y volvé a intentar.',
      0,
      path,
    )
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body: unknown = await response.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const raw = (body as { detail: unknown }).detail
        if (typeof raw === 'string') detail = raw
      }
    } catch {
      /* respuesta sin cuerpo JSON: nos quedamos con el status */
    }
    throw new ApiError(detail, response.status, path)
  }

  return (await response.json()) as T
}

// ── Sistema ─────────────────────────────────────────────────────────
export const getHealth = (signal?: AbortSignal) =>
  request<HealthResponse>('/health', undefined, signal)

export const getScoringConfig = (signal?: AbortSignal) =>
  request<ScoringConfig>('/config', undefined, signal)

// ── Overview ────────────────────────────────────────────────────────
export const getOverview = (params?: { country?: string; limit?: number }, signal?: AbortSignal) =>
  request<OverviewResponse>('/overview', params, signal)

// ── Productos ───────────────────────────────────────────────────────
export interface ProductQuery extends QueryParams {
  brand?: string
  franchise?: string
  category?: string
  sport?: string
  use_case?: string
  gender?: string
  price_band?: string
  country?: string
  retailer?: number
  q?: string
  limit?: number
  offset?: number
}

export const getProducts = (params?: ProductQuery, signal?: AbortSignal) =>
  request<ProductListResponse>('/products', params, signal)

export const getProductFilters = (signal?: AbortSignal) =>
  request<ProductFilters>('/products/filters', undefined, signal)

export const getProduct = (id: number, signal?: AbortSignal) =>
  request<ProductDetail>(`/products/${id}`, undefined, signal)

// ── Matches competitivos ────────────────────────────────────────────
export const getProductMatches = (
  id: number,
  params?: { limit?: number; with_factors?: boolean },
  signal?: AbortSignal,
) => request<ProductMatchesResponse>(`/products/${id}/matches`, params, signal)

export const getMatch = (id: number, signal?: AbortSignal) =>
  request<MatchDetail>(`/matches/${id}`, undefined, signal)

export const getMatches = (params?: { min_score?: number; limit?: number }, signal?: AbortSignal) =>
  request<MatchListResponse>('/matches', params, signal)

// ── Oportunidades ───────────────────────────────────────────────────
export interface OpportunityQuery extends QueryParams {
  family?: string
  opportunity_type?: string
  severity?: string
  country?: string
  min_importance?: number
  limit?: number
  offset?: number
}

export const getOpportunities = (params?: OpportunityQuery, signal?: AbortSignal) =>
  request<OpportunityListResponse>('/opportunities', params, signal)

// ── Retail media ────────────────────────────────────────────────────
export interface RetailMediaQuery extends QueryParams {
  recommendation?: string
  retailer?: number
  min_score?: number
  limit?: number
  offset?: number
}

export const getRetailMedia = (params?: RetailMediaQuery, signal?: AbortSignal) =>
  request<RetailMediaResponse>('/retail-media', params, signal)

// ── Brand intelligence ──────────────────────────────────────────────
export const getBrandInsights = (
  params?: {
    country?: string
    dimension?: string
    brand?: string
    min_confidence?: string
    limit?: number
  },
  signal?: AbortSignal,
) => request<BrandInsightsResponse>('/brand/insights', params, signal)

export const getBrandMomentum = (
  params?: { country?: string; signal_type?: string; entity_type?: string; limit?: number },
  signal?: AbortSignal,
) => request<MomentumResponse>('/brand/momentum', params, signal)

export const getBrandTopics = (
  params?: { country?: string; limit?: number },
  signal?: AbortSignal,
) => request<TopicsResponse>('/brand/topics', params, signal)
