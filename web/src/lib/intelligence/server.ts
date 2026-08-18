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
  Glossary,
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
import type {
  BrandInsightsParams,
  BrandMomentumParams,
  BrandTopicsParams,
  OpportunityQuery,
  ProductQuery,
  QueryParams,
  RetailMediaQuery,
} from './api'

/**
 * ¿Alguien configuró dónde vive el motor?
 *
 * Es la distinción que separa los dos únicos motivos por los que estas
 * pantallas fallan, y que exigen respuestas opuestas: "nadie lo desplegó
 * todavía" vs. "está desplegado y se cayó". Sin esto el dashboard decía lo
 * mismo en los dos casos.
 */
export function intelligenceApiConfigured(): boolean {
  return Boolean(process.env.INTELLIGENCE_API_URL?.trim())
}

/**
 * Base del backend de inteligencia.
 *
 * El default a localhost es para desarrollo: `npm run dev` con el uvicorn al
 * lado funciona sin configurar nada. En Vercel ese default no sirve para nada
 * —no hay ningún :8000 dentro del contenedor de la Lambda— y por eso las 6
 * solapas de Intelligence aparecen caídas en un deploy donde nadie seteó
 * `INTELLIGENCE_API_URL`. No es un bug del dashboard: falta desplegar el motor.
 */
export const INTELLIGENCE_API_BASE = (
  process.env.INTELLIGENCE_API_URL ?? 'http://localhost:8000'
).replace(/\/+$/, '')

/**
 * API key del motor (`CI_API_KEY` del lado del backend, ver `backend/app/auth.py`).
 *
 * La autenticación del motor es OPCIONAL: sin `CI_API_KEY` en el backend no se
 * exige nada y esta variable puede no existir. Cuando existe, todo lo que no
 * sea público pide el header `X-API-Key`.
 *
 * La variable se llama `INTELLIGENCE_API_KEY`, **sin** el prefijo
 * `NEXT_PUBLIC_`: con ese prefijo Next la inlinea en el bundle del cliente y la
 * key queda a la vista de cualquiera con las devtools abiertas. Como todas las
 * llamadas al motor salen del servidor (este módulo y el proxy
 * `/api/intelligence/[...path]`), el browser nunca necesita conocerla.
 *
 * Se lee en cada llamada, no en el módulo: así el valor es el del proceso que
 * corre y no queda congelado en el build.
 */
export function intelligenceApiKey(): string {
  return (process.env.INTELLIGENCE_API_KEY ?? '').trim()
}

/**
 * Headers de salida hacia el motor: `Accept` siempre, `X-API-Key` sólo si la
 * variable está definida. Único lugar donde se arma, para que el proxy y los
 * Server Components no puedan autenticarse distinto.
 */
export function intelligenceHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json', ...extra }
  const key = intelligenceApiKey()
  if (key) headers['X-API-Key'] = key
  return headers
}

/** Cuánto esperamos al backend antes de cortar (el pipeline puede ser lento). */
export const TIMEOUT_MS = 20_000

/**
 * Copy única de "el motor no contestó". La ve el usuario final, no un dev.
 *
 * Antes era una constante con el comando de uvicorn adentro. El problema no era
 * el tono sino que era FALSA en producción: a alguien mirando el dashboard en
 * Vercel, "levantalo con uvicorn desde backend/" lo manda a un repo que no tiene
 * y a una terminal que no va a abrir, para arreglar algo que no está roto en su
 * máquina. Y peor: escondía el diagnóstico real, que es que el motor nunca se
 * desplegó.
 *
 * Es una función y no una constante porque el mensaje correcto depende de si
 * `INTELLIGENCE_API_URL` está seteada, y eso se resuelve por request en el
 * servidor (una constante de módulo lo congelaría en el build).
 *
 * Sigue siendo UNA sola definición: la usan el proxy `/api/intelligence/*` y
 * `fetchIntelligence`, que son los dos únicos lugares donde se sabe que el
 * motor no contestó.
 */
export function offlineMessage(): string {
  if (!intelligenceApiConfigured()) {
    return (
      'Las 6 solapas de Intelligence las calcula un servicio aparte del dashboard ' +
      '(el motor de inteligencia), y todavía no está configurado: falta la variable ' +
      'INTELLIGENCE_API_URL. El resto del dashboard —Competencia, Share of Shelf, ' +
      'Control Retailers y Assortment— no depende del motor y sigue funcionando ' +
      'normal. Para activarlo hay que desplegar el motor y pegar su URL en esa ' +
      'variable: los pasos están en docs/deploy.md del repo.'
    )
  }
  // Se relee del entorno en vez de usar `INTELLIGENCE_API_BASE`, que se congela
  // al importar el módulo. En Next da lo mismo (el módulo se importa con el
  // entorno ya cargado), pero este mensaje es justamente el que lee alguien
  // depurando la configuración: tiene que mostrar el valor VIGENTE, no el que
  // había cuando el proceso levantó.
  const base = (process.env.INTELLIGENCE_API_URL ?? '').trim().replace(/\/+$/, '')
  return (
    `El motor de inteligencia está configurado en ${base} pero no ` +
    'respondió. Suele ser una de tres: el servicio está dormido o caído (si está en ' +
    'el plan free de Render se duerme solo tras 15 minutos sin uso y la primera ' +
    'visita tarda cerca de un minuto en despertarlo — reintentá), la URL de ' +
    'INTELLIGENCE_API_URL está mal escrita, o el servicio se quedó sin desplegar. ' +
    'Abrí esa URL con /api/health al final para ver cuál de las tres es.'
  )
}

export const TIMEOUT_MESSAGE =
  'El motor de inteligencia tardó demasiado en responder. Si acaba de arrancar, ' +
  'está reconstruyendo su base de datos (tarda cerca de un minuto) — esperá y ' +
  'reintentá. Si sigue igual después de un par de intentos, revisá los logs del ' +
  'servicio: probablemente el pipeline no llegó a terminar.'

// ── Política de caché por endpoint ──────────────────────────────────

export interface CacheRule {
  /** Segundos de `revalidate`. `0` = nunca se cachea. */
  revalidate: number
  /** Por qué ese número. Se documenta acá para no discutirlo en cada page. */
  reason: string
}

/**
 * Etiqueta con la que se marca TODO lo cacheado del motor.
 *
 * Existe por una razón concreta: el triaje escribe. Sin una etiqueta común, una
 * oportunidad recién descartada seguía apareciendo abierta hasta un minuto
 * después —la lista servida desde el Data Cache era anterior al `POST`—, y el
 * usuario veía revertirse una decisión que en la base sí estaba guardada. El
 * proxy invalida esta etiqueta después de cada escritura exitosa, así la
 * siguiente lectura vuelve a preguntarle al backend.
 */
export const INTELLIGENCE_CACHE_TAG = 'intelligence'

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
      headers: intelligenceHeaders(),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      ...(rule.revalidate > 0
        ? { next: { revalidate: rule.revalidate, tags: [INTELLIGENCE_CACHE_TAG] } }
        : { cache: 'no-store' as const }),
    })
  } catch (cause) {
    const timedOut = cause instanceof DOMException && cause.name === 'TimeoutError'
    return {
      ok: false,
      error: timedOut ? TIMEOUT_MESSAGE : offlineMessage(),
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

export const fetchBrandInsights = (params?: BrandInsightsParams) =>
  fetchIntelligence<BrandInsightsResponse>('/brand/insights', params)

export const fetchBrandMomentum = (params?: BrandMomentumParams) =>
  fetchIntelligence<MomentumResponse>('/brand/momentum', params)

export const fetchBrandTopics = (params?: BrandTopicsParams) =>
  fetchIntelligence<TopicsResponse>('/brand/topics', params)

/**
 * Glosario de los componentes de business importance.
 *
 * `/api/opportunities` publica la contribución de cada driver pero todavía no
 * adjunta el glosario; la MISMA definición sí viaja en `/api/retail-media`, que
 * devuelve las dos familias (`retail_media` + `business_importance`) porque uno
 * de sus factores es justamente el score de business importance.
 *
 * Se pide una sola fila (la respuesta es la más chica posible que incluya el
 * bloque) y el resultado lo cachea el Data Cache como cualquier listado. Si el
 * motor no lo publica, se devuelve `null` y las tarjetas quedan sin tooltip:
 * degradar es aceptable, mentir sobre qué mide una variable no.
 *
 * Cuando el backend adjunte `glossary` a `/api/opportunities`, esto se borra y
 * se lee de la propia respuesta.
 */

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
