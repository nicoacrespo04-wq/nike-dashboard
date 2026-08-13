import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

// Share of Shelf por retailer: para cada término de búsqueda, "gana" la
// marca con mayor Nike_Visibility (posición relativa 0-1, 1 = mejor
// posición posible). Share = % de términos ganados por marca en ese canal.
export async function GET() {
  const winners = await query<{ canal: string; marca: string; wins: number }>(`
    WITH ranked AS (
      SELECT
        canal, search_term, marca, nike_visibility,
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

  const nikeGlobal = await query<{ avg_visibility: number; n: number }>(`
    SELECT AVG(nike_visibility)::float AS avg_visibility, COUNT(*)::int AS n
    FROM retail_media_search WHERE marca = 'Nike' AND nike_visibility IS NOT NULL
  `)
  const adidasGlobal = await query<{ avg_visibility: number }>(`
    SELECT AVG(nike_visibility)::float AS avg_visibility
    FROM retail_media_search WHERE marca = 'Adidas' AND nike_visibility IS NOT NULL
  `)
  const pumaGlobal = await query<{ avg_visibility: number }>(`
    SELECT AVG(nike_visibility)::float AS avg_visibility
    FROM retail_media_search WHERE marca = 'Puma' AND nike_visibility IS NOT NULL
  `)

  return NextResponse.json({
    byCanal,
    global: {
      nike: nikeGlobal[0]?.avg_visibility ?? null,
      adidas: adidasGlobal[0]?.avg_visibility ?? null,
      puma: pumaGlobal[0]?.avg_visibility ?? null,
      n: nikeGlobal[0]?.n ?? 0,
    },
    totalByCanal,
  })
}
