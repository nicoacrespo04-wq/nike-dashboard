import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

// Sin esto Next.js 14 trata este handler (GET sin Request) como estático y lo
// prerenderiza en build time, sirviendo para siempre el snapshot de datos que
// existía al compilar.
export const dynamic = 'force-dynamic'

// Marcas canónicas. En `retail_media_search` la marca viene del CSV con
// casing mezclado ('Nike'/'NIKE', 'Puma'/'PUMA', ...), así que TODAS las
// comparaciones van por UPPER(marca) en vez de literales.
const MARCAS = ['NIKE', 'ADIDAS', 'PUMA'] as const
type MarcaKey = 'nike' | 'adidas' | 'puma'

// Share of Shelf por retailer: para cada término de búsqueda, "gana" la
// marca con mayor Nike_Visibility (posición relativa 0-1, 1 = mejor
// posición posible). Share = % de términos ganados por marca en ese canal.
export async function GET() {
  try {
    const winners = await query<{ canal: string; marca: string; wins: number }>(`
      WITH ranked AS (
        SELECT
          canal, search_term, INITCAP(marca) AS marca, nike_visibility,
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
    `)

    const totals = await query<{ canal: string; total: number }>(`
      SELECT canal, COUNT(DISTINCT search_term)::int AS total
      FROM retail_media_search
      GROUP BY canal
    `)

    const totalByCanal = Object.fromEntries(totals.map((t) => [t.canal, t.total]))

    const byCanal: Record<string, { marca: string; wins: number; pct: number }[]> = {}
    for (const w of winners) {
      const total = totalByCanal[w.canal] || 1
      if (!byCanal[w.canal]) byCanal[w.canal] = []
      byCanal[w.canal].push({ marca: w.marca, wins: w.wins, pct: Math.round((w.wins / total) * 1000) / 10 })
    }

    // Visibilidad promedio global por marca, en una sola pasada.
    // `avg_visibility` es un float 0..1 (la UI lo formatea como %).
    const globalRows = await query<{ marca: string; avg_visibility: number | null; n: number }>(`
      SELECT
        UPPER(marca)                 AS marca,
        AVG(nike_visibility)::float  AS avg_visibility,
        COUNT(*)::int                AS n
      FROM retail_media_search
      WHERE nike_visibility IS NOT NULL
        AND UPPER(marca) = ANY($1)
      GROUP BY UPPER(marca)
    `, [MARCAS as unknown as string[]])

    const globalByMarca = Object.fromEntries(globalRows.map((r) => [r.marca, r]))

    const globalOut = {} as Record<MarcaKey, number | null> & { n: number }
    for (const marca of MARCAS) {
      const row = globalByMarca[marca]
      const raw = row?.avg_visibility
      const value = raw === null || raw === undefined ? null : Number(raw)
      globalOut[marca.toLowerCase() as MarcaKey] = value !== null && Number.isFinite(value) ? value : null
    }
    globalOut.n = globalByMarca['NIKE']?.n ?? 0

    return NextResponse.json({
      byCanal,
      global: globalOut,
      totalByCanal,
    })
  } catch (err) {
    console.error('[/api/shelf/summary]', err)
    return NextResponse.json({ error: 'Error al obtener share of shelf' }, { status: 500 })
  }
}
