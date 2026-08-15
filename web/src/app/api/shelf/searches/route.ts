import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

// La marca viene del CSV con casing mezclado ('Nike'/'NIKE', 'Puma'/'PUMA').
// Normalizamos a una clave interna y a una etiqueta canónica, que es la que
// usa la UI para colorear al ganador.
const MARCA_KEYS = { NIKE: 'nike', ADIDAS: 'adidas', PUMA: 'puma' } as const
const MARCA_LABEL = { nike: 'Nike', adidas: 'Adidas', puma: 'Puma' } as const
type MarcaKey = (typeof MARCA_KEYS)[keyof typeof MARCA_KEYS]

interface SearchTermRow {
  canal: string
  search_term: string
  nike?: number
  adidas?: number
  puma?: number
  winner: string
}

// Detalle por término de búsqueda + canal, con quién "gana" ese término.
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url)
  const canal = searchParams.get('canal')

  try {
    const rows = await query<{
      canal: string
      search_term: string
      marca: string
      nike_visibility: number
    }>(
      `SELECT canal, search_term, UPPER(marca) AS marca, nike_visibility
       FROM retail_media_search
       WHERE nike_visibility IS NOT NULL
       ${canal ? 'AND canal = $1' : ''}
       ORDER BY canal, search_term, nike_visibility DESC`,
      canal ? [canal] : []
    )

    const grouped: Record<string, SearchTermRow> = {}
    for (const r of rows) {
      const key = `${r.canal}|${r.search_term}`
      if (!grouped[key]) grouped[key] = { canal: r.canal, search_term: r.search_term, winner: '' }
      const marcaKey: MarcaKey | undefined = MARCA_KEYS[r.marca as keyof typeof MARCA_KEYS]
      if (!marcaKey) continue // marca fuera de Nike/Adidas/Puma: la ignoramos
      const value = Number(r.nike_visibility)
      grouped[key][marcaKey] = Number.isFinite(value) ? value : undefined
    }

    for (const g of Object.values(grouped)) {
      const entries: [string, number][] = [
        [MARCA_LABEL.nike, g.nike ?? -1],
        [MARCA_LABEL.adidas, g.adidas ?? -1],
        [MARCA_LABEL.puma, g.puma ?? -1],
      ]
      entries.sort((a, b) => b[1] - a[1])
      g.winner = entries[0][1] >= 0 ? entries[0][0] : 'N/D'
    }

    return NextResponse.json({ rows: Object.values(grouped) })
  } catch (err) {
    console.error('[/api/shelf/searches]', err)
    return NextResponse.json({ error: 'Error al obtener búsquedas' }, { status: 500 })
  }
}
