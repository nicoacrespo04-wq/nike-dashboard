/**
 * /api/diagnostics — "¿qué está corriendo realmente y contra qué datos?"
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUÉ EXISTE
 * ─────────────────────────────────────────────────────────────────────────
 * Se perdieron varias vueltas depurando el deploy a ciegas. Los KPIs de Share
 * of Shelf salían en `N/D` en Vercel mientras el MISMO código, corrido contra
 * un Postgres local con el CSV real (`db/retail_media_search.csv`), devolvía
 * 14,0% / 23,9% / 9,0% sin una sola marca faltante. O sea: el síntoma no
 * distinguía entre tres causas completamente distintas
 *
 *   1. el deploy está construyendo OTRO commit (rama de producción vieja),
 *   2. faltan variables de entorno (`DATABASE_URL`, `NEXTAUTH_*`),
 *   3. la base apuntada no tiene los datos (tabla vacía, o la columna que
 *      alimenta el KPI toda en NULL),
 *
 * y cada una se arregla en un lugar distinto. Abrir la pantalla no las separa;
 * este endpoint sí, en una sola request y sin entrar al panel de Vercel.
 *
 * Devuelve SIEMPRE 200 con el detalle de lo que pudo y no pudo averiguar: un
 * 500 acá volvería a esconder justo la información que se vino a buscar. Cada
 * bloque va envuelto por separado, así una base caída no se lleva puesto el
 * dato de qué commit está desplegado —que es el que más falta hace cuando la
 * base está caída—.
 *
 * NO expone secretos: de cada variable sensible informa sólo si está definida.
 */

import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

/** Nunca devolver el valor de una variable sensible: sólo si está o no. */
function envPresence(name: string): 'definida' | 'FALTA' {
  const value = process.env[name]
  return value && value.length > 0 ? 'definida' : 'FALTA'
}

async function safe<T>(fn: () => Promise<T>): Promise<T | { error: string }> {
  try {
    return await fn()
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) }
  }
}

export async function GET() {
  // Vercel inyecta estas variables en el build. Si `commit` viene vacío, esto
  // no se está sirviendo desde Vercel (o el proyecto no está conectado a Git).
  const build = {
    commit: process.env.VERCEL_GIT_COMMIT_SHA ?? null,
    rama: process.env.VERCEL_GIT_COMMIT_REF ?? null,
    mensaje: process.env.VERCEL_GIT_COMMIT_MESSAGE ?? null,
    entorno: process.env.VERCEL_ENV ?? 'local',
  }

  const entorno = {
    DATABASE_URL: envPresence('DATABASE_URL'),
    NEXTAUTH_SECRET: envPresence('NEXTAUTH_SECRET'),
    NEXTAUTH_URL: envPresence('NEXTAUTH_URL'),
    INTELLIGENCE_API_URL: process.env.INTELLIGENCE_API_URL ?? 'FALTA (default http://localhost:8000)',
  }

  const base = await safe(async () => {
    const [{ ahora, version }] = await query<{ ahora: string; version: string }>(
      'SELECT now()::text AS ahora, version() AS version',
    )
    return { ahora, version: version.split(',')[0] }
  })

  // Las dos tablas que alimentan el dashboard, cada una con la columna de la
  // que depende su pantalla. `filas > 0` con `con_dato = 0` es un caso real y
  // distinto de "la tabla está vacía": la carga corrió pero se comió la
  // columna, y la pantalla queda en N/D igual.
  const tablas = await safe(async () => {
    const shelf = await query<{ filas: string; con_visibilidad: string; marcas: string }>(`
      SELECT COUNT(*)::text                     AS filas,
             COUNT(nike_visibility)::text       AS con_visibilidad,
             COUNT(DISTINCT marca)::text        AS marcas
      FROM retail_media_search
    `)
    const pricing = await query<{ filas: string; con_precio: string; marcas: string }>(`
      SELECT COUNT(*)::text                              AS filas,
             COUNT(competitor_final_price)::text         AS con_precio,
             COUNT(DISTINCT marca)::text                 AS marcas
      FROM pricing_data
    `)
    return { retail_media_search: shelf[0], pricing_data: pricing[0] }
  })

  // El motor de intelligence es un servicio aparte: si no está desplegado, las
  // 6 pestañas de Intelligence muestran "motor no disponible" y NINGÚN cambio
  // en el frontend lo arregla. Conviene verlo acá antes de buscar el bug en la
  // pantalla equivocada.
  const intelligence = await safe(async () => {
    const url = process.env.INTELLIGENCE_API_URL
    if (!url) return { estado: 'no configurado', detalle: 'INTELLIGENCE_API_URL no está definida' }
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 5000)
    try {
      const res = await fetch(`${url}/api/health`, { signal: ctrl.signal, cache: 'no-store' })
      return { estado: res.ok ? 'ok' : `HTTP ${res.status}`, url }
    } finally {
      clearTimeout(timer)
    }
  })

  return NextResponse.json(
    { build, entorno, base, tablas, intelligence },
    { headers: { 'Cache-Control': 'no-store' } },
  )
}
