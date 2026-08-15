/**
 * Proxy interno hacia el backend FastAPI del Decision Engine.
 *
 * Por qué un proxy y no llamar al :8000 desde el browser:
 *  - Un solo origen: no hay CORS que configurar ni un segundo puerto que el
 *    usuario tenga que conocer.
 *  - Queda detrás del mismo login: `src/middleware.ts` intercepta todo lo que
 *    no sea `/login` ni `/api/auth`, así que este endpoint sólo responde a
 *    sesiones autenticadas.
 *  - Un único lugar donde traducir "backend caído" a un error accionable en
 *    vez de un stacktrace o un fetch que queda colgado.
 *
 * Mapeo de rutas:
 *    /api/intelligence/health            → ${INTELLIGENCE_API_URL}/api/health
 *    /api/intelligence/products/12       → ${INTELLIGENCE_API_URL}/api/products/12
 *    /api/intelligence/brand/insights?q= → ${INTELLIGENCE_API_URL}/api/brand/insights?q=
 */

import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

/** Base del backend de inteligencia. Configurable por entorno. */
const API_BASE = (process.env.INTELLIGENCE_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '')

/** Cuánto esperamos al backend antes de cortar (el pipeline puede ser lento). */
const TIMEOUT_MS = 20_000

/** Mensaje único de "no está levantado". Es la copy que ve el usuario. */
const OFFLINE_MESSAGE =
  'El motor de inteligencia no está disponible — levantalo con `uvicorn app.main:app --port 8000` desde la carpeta backend/.'

const TIMEOUT_MESSAGE =
  'El motor de inteligencia no respondió a tiempo. Verificá que el proceso de `uvicorn` siga vivo y que el pipeline haya terminado.'

interface RouteContext {
  params: { path?: string[] }
}

function errorResponse(message: string, status: number) {
  return NextResponse.json(
    { detail: message, source: 'intelligence-proxy', upstream: API_BASE },
    { status, headers: { 'Cache-Control': 'no-store' } },
  )
}

async function forward(request: Request, context: RouteContext): Promise<Response> {
  const segments = context.params.path ?? []
  if (segments.length === 0) {
    return errorResponse('Falta la ruta del endpoint de inteligencia.', 400)
  }

  const search = new URL(request.url).search
  const target = `${API_BASE}/api/${segments.map(encodeURIComponent).join('/')}${search}`

  let upstream: Response
  try {
    upstream = await fetch(target, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
  } catch (cause) {
    // `TimeoutError` / `AbortError` vienen como DOMException; el resto son
    // fallos de conexión (ECONNREFUSED, DNS, etc.).
    const timedOut = cause instanceof DOMException && cause.name === 'TimeoutError'
    return errorResponse(timedOut ? TIMEOUT_MESSAGE : OFFLINE_MESSAGE, timedOut ? 504 : 503)
  }

  const body = await upstream.text()

  // El backend siempre responde JSON. Si llega otra cosa (proxy corporativo,
  // página de error de un servidor equivocado), no se la pasamos cruda al
  // cliente: se convierte en un error legible.
  const contentType = upstream.headers.get('content-type') ?? ''
  if (!contentType.includes('json')) {
    return errorResponse(
      `El backend en ${API_BASE} respondió algo que no es JSON (${upstream.status}). ¿Está apuntando INTELLIGENCE_API_URL al servicio correcto?`,
      502,
    )
  }

  return new Response(body, {
    status: upstream.status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}

export async function GET(request: Request, context: RouteContext) {
  return forward(request, context)
}

export async function HEAD(request: Request, context: RouteContext) {
  return forward(request, context)
}
