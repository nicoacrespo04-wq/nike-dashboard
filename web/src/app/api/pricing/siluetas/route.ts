import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'

export const dynamic = 'force-dynamic'

// Precio saneado (ver web/src/lib/price.ts).
const FINAL = validPriceSql('competitor_final_price')

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const marca = searchParams.get('marca') ?? ''

  const conditions = [`silueta IS NOT NULL`, `silueta <> ''`]
  const params: unknown[] = []
  if (marca) { conditions.push(`UPPER(marca) = UPPER($1)`); params.push(marca) }
  else        { conditions.push(`UPPER(marca) IN ('ADIDAS','PUMA')`) }

  try {
    const rows = await query(`
      SELECT
        silueta,
        UPPER(marca)                                       AS marca,
        COUNT(DISTINCT style_color)                        AS count,
        ROUND(AVG(${FINAL})::numeric, 0)                   AS avg_price,
        COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')  AS beat,
        COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')  AS lose
      FROM pricing_data
      WHERE ${conditions.join(' AND ')}
      GROUP BY silueta, UPPER(marca)
      ORDER BY count DESC
    `, params)

    return NextResponse.json({ siluetas: rows })
  } catch (err) {
    console.error('[/api/pricing/siluetas]', err)
    return NextResponse.json({ error: 'Error al obtener siluetas' }, { status: 500 })
  }
}
