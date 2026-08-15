import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'


// Heat-map BML (Beat/Meet/Lose) por retailer x franchise, SOLO filas de
// productos Nike vendidos en retailers B2B (comparados contra el precio
// de nike.com.ar via nike_ar_general, que es lo que ya calcula
// bml_final_price en el pipeline). Excluye D2C/US/URU/CO propios de Nike.
export async function GET() {
  try {
    const rows = await query<{
      scraper: string
      franchise: string
      total: number
      beat: number
      meet: number
      lose: number
      nd: number
      avg_gap_pct: number
    }>(`
      SELECT
        scraper,
        COALESCE(NULLIF(franchise_scrapper, ''), 'Sin franchise') AS franchise,
        COUNT(*)                                          AS total,
        COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')  AS beat,
        COUNT(*) FILTER (WHERE bml_final_price = 'MEET')  AS meet,
        COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')  AS lose,
        COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd,
        ROUND(AVG(gap_final_price_pct) FILTER (WHERE gap_final_price_pct IS NOT NULL)::numeric, 4) AS avg_gap_pct
      FROM pricing_data
      WHERE UPPER(marca) = 'NIKE'
        AND scraper NOT IN ('nike_ar_general','nike_co_general','nike_us_general','URU','USA','ADIDAS_7','Puma_AR')
      GROUP BY scraper, franchise
      HAVING COUNT(*) >= 3
      ORDER BY scraper, total DESC
    `)

    const retailers = Array.from(new Set(rows.map((r) => r.scraper))).sort()
    const franchises = Array.from(new Set(rows.map((r) => r.franchise)))
      .sort((a, b) => {
        const totalA = rows.filter((r) => r.franchise === a).reduce((s, r) => s + Number(r.total), 0)
        const totalB = rows.filter((r) => r.franchise === b).reduce((s, r) => s + Number(r.total), 0)
        return totalB - totalA
      })
      .slice(0, 25)

    return NextResponse.json({ rows, retailers, franchises })
  } catch (err) {
    console.error('[/api/pricing/bml-heatmap]', err)
    return NextResponse.json({ error: 'Error al obtener heatmap BML' }, { status: 500 })
  }
}
