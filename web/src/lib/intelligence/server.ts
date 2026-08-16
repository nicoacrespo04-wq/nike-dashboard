/**
 * Cliente server-side del Decision Engine.
 *
 * Este módulo SÓLO corre en el servidor (Server Components, `loading`/`page`
 * de App Router y el proxy `/api/intelligence/[...path]`). Nunca importarlo
 * desde un componente `'use client'`: pega directo a `INTELLIGENCE_API_URL`,
 * que es una URL interna que el browser no tiene por qué conocer.
 *
 * Por qué existe además de `api.ts`:
 *  - `api.ts` es el cliente del browser y pega al proxy interno (mismo origen,
 *    detrás del login). No sabe caché: cada llamada es un round-trip.
 *  - Acá el `fetch` de Next participa del Data Cache: dos usuarios pidiendo
 *    la misma pantalla dentro de la ventana de `revalidate` comparten una
 *    sola consulta al backend.
 *
 * Sobre compartir caché entre usuarios: es seguro *porque* el Decision Engine
 * no tiene noción de usuario — las respuestas dependen sólo de la ruta y sus
 * query params, nunca de quién pregunta. La protección de sesión sigue viva
 * aguas arriba (`src/middleware.ts` bloquea todo lo que no sea `/login`), así
 * que nadie sin sesión llega a renderizar estas páginas. Si algún día el
 * backend devolviera datos por usuario, este caché tendría que pasar a
 * `cache: 'no-store'`.
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
  TopicsResponse,
} from '@/types/intelligence'
import type { OpportunityQuery, ProductQuery, QueryParams, RetailMediaQuery } from './api'

/** Base del backend de inteligencia. Configurable por entorno. */
export const INTELLIGENCE_API_BASE = (
  process.env.INTELLIGENCE_API_URL ?? 'http://localhost:8000'
).replace(/\/+$/, '')

/** Cuánto esperamos al backend antes de cortar (el pipeline puede ser lento). */
export const TIMEOUT_MS = 20_000

/** Mensaje único de "no está levantado". Es la copy que ve el usuario. */
export const OFFLINE_MESSAGE =
  'El motor de inteligencia no está disponible — levantalo con `uvicorn app.main:app --port 8000` desde la carpeta backend/.'

export const TIMEOUT_MESSAGE =
  'El motor de inteligencia no respondió a tiempo. Verificá que el proceso de `uvicorn` siga vivo y que el pipeline haya terminado.'

// ── Política de caché por endpoint ──────────────────────────────────

export interface CacheRule {
  /** Segundos de `revalidate`. `0` = nunca se cachea. */
  revalidate: number
  /** Por qué ese número. Se documenta acá para no discutirlo en cada page. */
  reason: string
}

const SECONDS = {
  /** Semáforo del backend: cachearlo sería mentir sobre si está vivo. */
  live: 0,
  /** Foto ejecutiva: tolera segundos de atraso sin cambiar ninguna decisión. */
  snapshot: 30,
  /** Listados que se filtran y paginan: el usuario los recorre en ráfagas. */
  list: 60,
  /** Fichas de detalle: cambian sólo cuando vuelve a correr el pipeline. */
  detail: 120,
  /** Metadatos casi estáticos (opciones de filtro, pesos de scoring). */
  metadata: 600,
} as const

/**
 * Cuánto puede cachearse una ruta del backend.
 *
 * Se decide por el *tipo* de endpoint, no por la pantalla que lo consume, así
 * el proxy y los Server Components no pueden contradecirse.
 */
export function cacheRuleFor(path: string): CacheRule {
  const clean = `/${path.replace(/^\/+/, '').replace(/\/+$/, '')}`

  if (clean === '/health') {
    return { revalidate: SECONDS.live, reason: 'estado del backend en vivo' }
  }
  if (clean === '/config' || clean === '/products/filters') {
    return { revalidate: SECONDS.metadata, reason: 'metadatos casi estáticos' }
  }
  if (clean === '/overview') {
    return { revalidate: SECONDS.snapshot, reason: 'foto ejecutiva' }
  }
  // /products/12, /products/12/matches, /matches/3
  if (/^\/(products|matches)\/\d+/.test(clean)) {
    return { revalidate: SECONDS.detail, reason: 'detalle de una entidad' }
  }
  return { revalidate: SECONDS.list, reason: 'listado paginado' }
}

// ── Fetch tipado ────────────────────────────────────────────────────

export type ServerResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number }

function buildUrl(path: string, params?: QueryParams): string {
  const search = new URLSearchParams()
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  return `${INTELLIGENCE_API_BASE}/api${path}${qs ? `?${qs}` : ''}`
}

/**
 * Pide un endpoint del motor desde el servidor.
 *
 * Nunca tira: devuelve un resultado discriminado para que la página pueda
 * pintar el estado de error accionable (con el comando para levantar el
 * backend) en vez de romper el árbol de React.
 */
export async function fetchIntelligence<T>(
  path: string,
  params?: QueryParams,
): Promise<ServerResult<T>> {
  const rule = cacheRuleFor(path)
  const url = buildUrl(path, params)

  let response: Response
  try {
    response = await fetch(url, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(TIMEOUT_MS),
      ...(rule.revalidate > 0
        ? { next: { revalidate: rule.revalidate } }
        : { cache: 'no-store' as const }),
    })
  } catch (cause) {
    const timedOut = cause instanceof DOMException && cause.name === 'TimeoutError'
    return {
      ok: false,
      error: timedOut ? TIMEOUT_MESSAGE : OFFLINE_MESSAGE,
      status: timedOut ? 504 : 503,
    }
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
    return { ok: false, error: detail, status: response.status }
  }

  return { ok: true, data: (await response.json()) as T }
}

// ── Atajos tipados por pantalla ─────────────────────────────────────

export const fetchHealth = () => fetchIntelligence<HealthResponse>('/health')

export const fetchOverview = (params?: { country?: string; limit?: number }) =>
  fetchIntelligence<OverviewResponse>('/overview', params)

export const fetchProducts = (params?: ProductQuery) =>
  fetchIntelligence<ProductListResponse>('/products', params)

export const fetchProductFilters = () =>
  fetchIntelligence<ProductFilters>('/products/filters')

export const fetchProduct = (id: number) =>
  fetchIntelligence<ProductDetail>(`/products/${id}`)

export const fetchMatch = (id: number) => fetchIntelligence<MatchDetail>(`/matches/${id}`)

export const fetchProductMatches = (
  id: number,
  params?: { limit?: number; with_factors?: boolean },
) => fetchIntelligence<ProductMatchesResponse>(`/products/${id}/matches`, params)

export const fetchMatches = (params?: { min_score?: number; limit?: number }) =>
  fetchIntelligence<MatchListResponse>('/matches', params)

export const fetchOpportunities = (params?: OpportunityQuery) =>
  fetchIntelligence<OpportunityListResponse>('/opportunities', params)

export const fetchRetailMedia = (params?: RetailMediaQuery) =>
  fetchIntelligence<RetailMediaResponse>('/retail-media', params)

export const fetchBrandInsights = (params?: {
  country?: string
  dimension?: string
  brand?: string
  min_confidence?: string
  limit?: number
}) => fetchIntelligence<BrandInsightsResponse>('/brand/insights', params)

export const fetchBrandMomentum = (params?: { country?: string; limit?: number }) =>
  fetchIntelligence<MomentumResponse>('/brand/momentum', params)

export const fetchBrandTopics = (params?: { country?: string; limit?: number }) =>
  fetchIntelligence<TopicsResponse>('/brand/topics', params)

/**
 * Nombre de la marca foco (Nike) para poder filtrar `/api/products` server-side.
 *
 * El backend no expone todavía un filtro `is_focus` ni publica cuál es la marca
 * foco en `/products/filters`; lo único que garantiza el contrato es el orden
 * del listado (`ORDER BY b.is_focus DESC, …`), así que el primer producto de la
 * primera página pertenece siempre a la marca foco si existe alguna. Pedimos
 * exactamente una fila, cacheada 10 minutos, y validamos `is_focus` antes de
 * confiar en el resultado: si el contrato cambiara, devolvemos `null` y el
 * llamador vuelve a filtrar del lado del cliente en vez de mostrar datos mal
 * filtrados.
 */
export async function fetchFocusBrand(): Promise<string | null> {
  const result = await fetchIntelligence<ProductListResponse>('/products', { limit: 1 })
  if (!result.ok) return null
  const first = result.data.items[0]
  if (!first || first.is_focus !== 1 || !first.brand) return null
  return first.brand
}
