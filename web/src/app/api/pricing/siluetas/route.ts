import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const marca = searchParams.get('marca') ?? ''

  const conditions = [`silueta IS NOT NULL`, `silueta <> ''`]
  const params: unknown[] = []
  if (marca) { conditions.push(`marca = $1`); params.push(marca) }
  else        { conditions.push(`marca IN ('ADIDAS','PUMA')`) }

  try {
    const rows = await query(`
      SELECT
        silueta,
        marca,
        COUNT(*)                                           AS count,
        ROUND(AVG(competitor_final_price)::numeric, 0)    AS avg_price,
        COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')  AS beat,
        COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')  AS lose
      FROM pricing_data
      WHERE ${conditions.join(' AND ')}
      GROUP BY silueta, marca
      ORDER BY count DESC
    `, params)

    return NextResponse.json({ siluetas: rows })
  } catch (err) {
    console.error('[/api/pricing/siluetas]', err)
    return NextResponse.json({ error: 'Error al obtener siluetas' }, { status: 500 })
  }
}
