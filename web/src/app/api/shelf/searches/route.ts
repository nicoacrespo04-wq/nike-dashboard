import { NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { canonicalMarca, marcaKey, marcaLabel, marcaNormSql, type MarcaKey } from '@/lib/marca'

export const dynamic = 'force-dynamic'

// Mismo bug que el bloque global de `/api/shelf/summary`: acá la marca se
// resolvía con `MARCA_KEYS[UPPER(marca)]`, y con un ' Nike ' de la base ese
// lookup daba `undefined` y la fila se descartaba en silencio — el detalle por
// término mostraba "N/D" en las tres columnas y en el ganador. Se normaliza con
// el mismo helper compartido (ver la causa raíz en `lib/marca.ts`).
const MARCA_NORM = marcaNormSql('marca')

interface RawRow {
  canal: string
  search_term: string
  marca: string
  nike_visibility: number
}

export interface SearchTermRow {
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
    const rows = await query<RawRow>(
      `SELECT canal, search_term, ${MARCA_NORM} AS marca, nike_visibility
       FROM retail_media_search
       WHERE nike_visibility IS NOT NULL
       ${canal ? 'AND canal = $1' : ''}
       ORDER BY canal, search_term, nike_visibility DESC`,
      canal ? [canal] : [],
    )

    const grouped = new Map<string, SearchTermRow>()
    for (const r of rows) {
      const canonical = canonicalMarca(r.marca)
      if (!canonical) continue // marca fuera de Nike/Adidas/Puma: la ignoramos
      const key = `${r.canal}|${r.search_term}`
      let row = grouped.get(key)
      if (!row) {
        row = { canal: r.canal, search_term: r.search_term, winner: '' }
        grouped.set(key, row)
      }
      const value = Number(r.nike_visibility)
      if (Number.isFinite(value)) row[marcaKey(canonical)] = value
    }

    for (const g of grouped.values()) {
      const entries: [string, number][] = (['NIKE', 'ADIDAS', 'PUMA'] as const).map((m) => [
        marcaLabel(m),
        g[marcaKey(m) as MarcaKey] ?? -1,
      ])
      entries.sort((a, b) => b[1] - a[1])
      g.winner = entries[0][1] >= 0 ? entries[0][0] : 'N/D'
    }

    return NextResponse.json({ rows: Array.from(grouped.values()) })
  } catch (err) {
    console.error('[/api/shelf/searches]', err)
    return NextResponse.json({ error: 'Error al obtener búsquedas' }, { status: 500 })
  }
}
