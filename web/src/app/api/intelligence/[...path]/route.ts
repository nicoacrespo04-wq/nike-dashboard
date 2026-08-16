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
 *  - Un único lugar donde decidir qué se cachea y por cuánto.
 *
 * Mapeo de rutas:
 *    /api/intelligence/health            → ${INTELLIGENCE_API_URL}/api/health
 *    /api/intelligence/products/12       → ${INTELLIGENCE_API_URL}/api/products/12
 *    /api/intelligence/brand/insights?q= → ${INTELLIGENCE_API_URL}/api/brand/insights?q=
 *
 * Caché (ver `cacheRuleFor` en `lib/intelligence/server.ts`):
 *  - `/health` nunca se cachea: es el semáforo del propio backend.
 *  - `/overview` 30s, listados 60s, detalles 120s, metadatos 600s.
 *  - Se cachea en dos niveles: el Data Cache de Next para el salto
 *    servidor→backend (compartido, y por eso el `Cache-Control` de salida es
 *    `private`: la copia del browser queda dentro de la sesión de cada
 *    usuario), y el browser del usuario para el salto browser→servidor.
 *  - Es seguro compartir la copia servidor-side porque el backend no tiene
 *    noción de usuario: la respuesta depende de la ruta y sus query params,
 *    nunca de quién pregunta. Los errores salen siempre `no-store`.
 */

import { NextResponse } from 'next/server'
import {
  INTELLIGENCE_API_BASE,
  OFFLINE_MESSAGE,
  TIMEOUT_MESSAGE,
  TIMEOUT_MS,
  cacheRuleFor,
} from '@/lib/intelligence/server'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

interface RouteContext {
  params: { path?: string[] }
}

function errorResponse(message: string, status: number) {
  return NextResponse.json(
    { detail: message, source: 'intelligence-proxy', upstream: INTELLIGENCE_API_BASE },
    { status, headers: { 'Cache-Control': 'no-store' } },
  )
}

async function forward(request: Request, context: RouteContext): Promise<Response> {
  const segments = context.params.path ?? []
  if (segments.length === 0) {
    return errorResponse('Falta la ruta del endpoint de inteligencia.', 400)
  }

  const path = `/${segments.join('/')}`
  const rule = cacheRuleFor(path)
  const search = new URL(request.url).search
  const target = `${INTELLIGENCE_API_BASE}/api/${segments.map(encodeURIComponent).join('/')}${search}`

  let upstream: Response
  try {
    upstream = await fetch(target, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(TIMEOUT_MS),
      ...(rule.revalidate > 0
        ? { next: { revalidate: rule.revalidate } }
        : { cache: 'no-store' as const }),
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
      `El backend en ${INTELLIGENCE_API_BASE} respondió algo que no es JSON (${upstream.status}). ¿Está apuntando INTELLIGENCE_API_URL al servicio correcto?`,
      502,
    )
  }

  // Un error del backend no se cachea aunque el endpoint sea cacheable: si no,
  // un 500 puntual se quedaría pegado en pantalla el resto de la ventana.
  const cacheControl =
    upstream.ok && rule.revalidate > 0
      ? `private, max-age=${rule.revalidate}, stale-while-revalidate=${rule.revalidate * 2}`
      : 'no-store'

  return new Response(body, {
    status: upstream.status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': cacheControl,
      // Trazabilidad: permite verificar la política desde devtools.
      'X-Intelligence-Cache': `${rule.revalidate}s · ${rule.reason}`,
    },
  })
}

export async function GET(request: Request, context: RouteContext) {
  return forward(request, context)
}

export async function HEAD(request: Request, context: RouteContext) {
  return forward(request, context)
}
