import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

// Detalle por término de búsqueda + canal, con quién "gana" ese término.
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url)
  const canal = searchParams.get('canal')

  const rows = await query<{
    canal: string
    search_term: string
    marca: string
    nike_visibility: number
  }>(
    `SELECT canal, search_term, marca, nike_visibility
     FROM retail_media_search
     WHERE nike_visibility IS NOT NULL
     ${canal ? 'AND canal = $1' : ''}
     ORDER BY canal, search_term, nike_visibility DESC`,
    canal ? [canal] : []
  )

  const grouped: Record<string, { canal: string; search_term: string; nike?: number; adidas?: number; puma?: number; winner: string }> = {}
  for (const r of rows) {
    const key = `${r.canal}|${r.search_term}`
    if (!grouped[key]) grouped[key] = { canal: r.canal, search_term: r.search_term, winner: '' }
    const marcaKey = r.marca.toLowerCase() as 'nike' | 'adidas' | 'PUMA'
    grouped[key][marcaKey] = r.nike_visibility
  }
  for (const g of Object.values(grouped)) {
    const entries: [string, number][] = [
      ['Nike', g.nike ?? -1],
      ['Adidas', g.adidas ?? -1],
      ['PUMA', g.puma ?? -1],
    ]
    entries.sort((a, b) => b[1] - a[1])
    g.winner = entries[0][0]
  }

  return NextResponse.json({ rows: Object.values(grouped) })
}
