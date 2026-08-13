import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const marca    = searchParams.get('marca')    ?? ''
  const division = searchParams.get('division') ?? ''
  const category = searchParams.get('category') ?? ''
  const gender   = searchParams.get('gender')   ?? ''
  const canal    = searchParams.get('canal')    ?? '' // 'd2c' | 'b2b' | ''

  const conditions: string[] = [
    `franchise_competitor IS NOT NULL`,
    `franchise_competitor <> ''`,
  ]
  const params: unknown[] = []
  let idx = 1

  if (marca) { conditions.push(`marca = $${idx++}`); params.push(marca) }
  else        { conditions.push(`marca IN ('ADIDAS','Puma')`) }

  if (division) { conditions.push(`UPPER(division_competitor) LIKE $${idx++}`); params.push(`%${division.toUpperCase()}%`) }
  if (category) { conditions.push(`category_competitor = $${idx++}`); params.push(category) }
  if (gender)   { conditions.push(`gender_competitor = $${idx++}`); params.push(gender) }

  if (canal === 'd2c') {
    conditions.push(`scraper IN ('ADIDAS_7','Puma_AR')`)
  } else if (canal === 'b2b') {
    conditions.push(`scraper NOT IN ('ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA')`)
  }

  const where = conditions.join(' AND ')

  try {
    const rows = await query(`
      SELECT
        franchise_competitor                                        AS franchise,
        marca,
        division_competitor                                         AS division,
        COUNT(*)                                                    AS count,
        ROUND(AVG(competitor_final_price)::numeric, 0)             AS avg_price,
        ROUND(AVG(competitor_full_price)::numeric, 0)              AS avg_full_price,
        ROUND(AVG(gap_final_price_pct)::numeric, 4)                AS avg_gap_pct,
        COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')           AS beat,
        COUNT(*) FILTER (WHERE bml_final_price = 'MEET')           AS meet,
        COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')           AS lose,
        COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd,
        ROUND(AVG(size_available_competitor)::numeric, 1)          AS avg_sizes,
        COUNT(*) FILTER (WHERE competitor_markdown > 0)            AS in_promo,
        ROUND(AVG(competitor_markdown) FILTER (WHERE competitor_markdown > 0)::numeric, 0) AS avg_markdown
      FROM pricing_data
      WHERE ${where}
      GROUP BY franchise_competitor, marca, division_competitor
      ORDER BY count DESC
      LIMIT 100
    `, params)

    return NextResponse.json({ franchises: rows })
  } catch (err) {
    console.error('[/api/pricing/franchises]', err)
    return NextResponse.json({ error: 'Error al obtener franchises' }, { status: 500 })
  }
}
