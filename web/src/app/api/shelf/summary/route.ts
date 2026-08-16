import { NextResponse } from 'next/server'
import { query } from '@/lib/db'
import {
  MARCAS,
  canonicalMarca,
  marcaDiagnosticSql,
  marcaKey,
  marcaLabel,
  marcaNormSql,
  type Marca,
  type MarcaDiagnosticRow,
  type MarcaKey,
} from '@/lib/marca'

// Sin esto Next.js 14 trata este handler (GET sin Request) como estático y lo
// prerenderiza en build time, sirviendo para siempre el snapshot de datos que
// existía al compilar.
export const dynamic = 'force-dynamic'

// ─────────────────────────────────────────────────────────────────────────
// POR QUÉ ESTE ENDPOINT NO FILTRA POR MARCA
// ─────────────────────────────────────────────────────────────────────────
// Los KPIs globales quedaban en N/D mientras las barras de la misma respuesta
// funcionaban. Causa raíz confirmada (reproducida en un Postgres local, ver el
// bloque largo de `lib/marca.ts`): `marca` llega con espacios — ' Nike ' — que
// `INITCAP` de las barras tolera porque sólo AGRUPA, y que el
// `WHERE UPPER(marca) = ANY($1)` del bloque global NO tolera porque FILTRA:
// cero filas → null → N/D. El binding de `= ANY($1)` estaba bien; se verificó
// contra la misma base que devuelve lo mismo que un `IN` literal.
//
// La lección quedó como regla de este archivo: **agrupar por marca normalizada
// y resolver la marca canónica en TypeScript.** Un valor sucio nuevo degrada
// una marca, nunca vacía el bloque entero.
const MARCA_NORM = marcaNormSql('marca')

interface WinnerRow {
  canal: string
  marca: string
  wins: number
}

interface CanalTotalRow {
  canal: string
  total: number
}

interface GlobalRow {
  marca: string
  avg_visibility: number | null
  n: number
  terms: number
}

export interface ShelfCanalShare {
  marca: string
  wins: number
  pct: number
}

export interface ShelfSummaryResponse {
  byCanal: Record<string, ShelfCanalShare[]>
  global: Record<MarcaKey, number | null> & { n: number }
  totalByCanal: Record<string, number>
  /** Sólo viene cuando alguna marca esperada no apareció. Ver más abajo. */
  diagnostics?: {
    message: string
    marcasFaltantes: string[]
    marcasEnLaBase: MarcaDiagnosticRow[]
  }
}

// Share of Shelf por retailer: para cada término de búsqueda, "gana" la
// marca con mayor Nike_Visibility (posición relativa 0-1, 1 = mejor
// posición posible). Share = % de términos ganados por marca en ese canal.
export async function GET() {
  try {
    const [winners, totals, globalRows] = await Promise.all([
      query<WinnerRow>(`
        WITH ranked AS (
          SELECT
            canal, search_term, ${MARCA_NORM} AS marca, nike_visibility,
            ROW_NUMBER() OVER (
              PARTITION BY canal, search_term
              ORDER BY nike_visibility DESC NULLS LAST
            ) AS rn
          FROM retail_media_search
          WHERE nike_visibility IS NOT NULL
        )
        SELECT canal, marca, COUNT(*)::int AS wins
        FROM ranked
        WHERE rn = 1
        GROUP BY canal, marca
        ORDER BY canal, wins DESC
      `),

      query<CanalTotalRow>(`
        SELECT canal, COUNT(DISTINCT search_term)::int AS total
        FROM retail_media_search
        GROUP BY canal
      `),

      // Visibilidad promedio global por marca. SIN filtro de marca: se agrupa
      // todo lo que haya y las tres marcas se resuelven abajo.
      query<GlobalRow>(`
        SELECT
          ${MARCA_NORM}                        AS marca,
          AVG(nike_visibility)::float          AS avg_visibility,
          COUNT(*)::int                        AS n,
          COUNT(DISTINCT search_term)::int     AS terms
        FROM retail_media_search
        WHERE nike_visibility IS NOT NULL
        GROUP BY ${MARCA_NORM}
      `),
    ])

    const totalByCanal: Record<string, number> = {}
    for (const t of totals) totalByCanal[t.canal] = t.total

    // Las barras muestran la marca canónica ('Nike'), no el valor crudo: con
    // ' Nike ' el color de marca no matcheaba y las barras salían grises.
    const byCanal: Record<string, ShelfCanalShare[]> = {}
    for (const w of winners) {
      const canonical = canonicalMarca(w.marca)
      const label = canonical ? marcaLabel(canonical) : w.marca.trim()
      const total = totalByCanal[w.canal] || 1
      const bucket = (byCanal[w.canal] ??= [])
      const existing = bucket.find((b) => b.marca === label)
      if (existing) {
        existing.wins += w.wins
        existing.pct = Math.round((existing.wins / total) * 1000) / 10
      } else {
        bucket.push({ marca: label, wins: w.wins, pct: Math.round((w.wins / total) * 1000) / 10 })
      }
    }
    for (const bucket of Object.values(byCanal)) bucket.sort((a, b) => b.pct - a.pct)

    // Varias filas crudas pueden colapsar en la misma marca canónica (alias,
    // sub-marcas): el promedio se recompone ponderado por cantidad de filas,
    // que es el promedio correcto, no el promedio de promedios.
    const accumulated = new Map<Marca, { sum: number; n: number; terms: number }>()
    for (const row of globalRows) {
      const canonical = canonicalMarca(row.marca)
      if (!canonical) continue
      const avg = row.avg_visibility === null ? null : Number(row.avg_visibility)
      if (avg === null || !Number.isFinite(avg)) continue
      const acc = accumulated.get(canonical) ?? { sum: 0, n: 0, terms: 0 }
      acc.sum += avg * row.n
      acc.n += row.n
      acc.terms = Math.max(acc.terms, row.terms)
      accumulated.set(canonical, acc)
    }

    const global = { n: 0 } as Record<MarcaKey, number | null> & { n: number }
    const marcasFaltantes: string[] = []
    for (const marca of MARCAS) {
      const acc = accumulated.get(marca)
      global[marcaKey(marca)] = acc && acc.n > 0 ? acc.sum / acc.n : null
      if (!acc || acc.n === 0) marcasFaltantes.push(marca)
    }
    global.n = accumulated.get('NIKE')?.terms ?? 0

    const payload: ShelfSummaryResponse = { byCanal, global, totalByCanal }

    // Diagnóstico: si alguna marca sigue sin aparecer es que la base trae un
    // valor de `marca` que la normalización todavía no contempla. En vez de
    // devolver N/D a secas, se listan los valores CRUDOS con su hexadecimal
    // (así se ven los caracteres invisibles) por log y en la respuesta, que es
    // exactamente lo que hizo falta para encontrar este bug.
    if (marcasFaltantes.length > 0) {
      const marcasEnLaBase = await query<MarcaDiagnosticRow>(
        marcaDiagnosticSql('retail_media_search'),
      )
      console.warn(
        '[/api/shelf/summary] marcas sin datos:', marcasFaltantes.join(', '),
        '· valores DISTINCT de `marca` en la base:',
        JSON.stringify(marcasEnLaBase),
      )
      payload.diagnostics = {
        message:
          `No se encontraron filas para: ${marcasFaltantes.join(', ')}. ` +
          'Los valores de `marca` que sí están en la base se listan abajo ' +
          '(la columna hex muestra los caracteres invisibles).',
        marcasFaltantes,
        marcasEnLaBase,
      }
    }

    return NextResponse.json(payload)
  } catch (err) {
    console.error('[/api/shelf/summary]', err)
    return NextResponse.json({ error: 'Error al obtener share of shelf' }, { status: 500 })
  }
}
