import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const [byRetailer, violations] = await Promise.all([
      query(`
        SELECT
          scraper,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE precio_sugerido IS NOT NULL) AS with_pvp,
          COUNT(*) FILTER (
            WHERE precio_sugerido IS NOT NULL
              AND ABS((competitor_final_price - precio_sugerido) / NULLIF(precio_sugerido, 0)) <= 0.05
          ) AS compliant,
          COUNT(*) FILTER (
            WHERE precio_sugerido IS NOT NULL
              AND competitor_final_price < precio_sugerido * 0.95
          ) AS below_pvp,
          COUNT(*) FILTER (
            WHERE precio_sugerido IS NOT NULL
              AND competitor_final_price > precio_sugerido * 1.05
          ) AS above_pvp,
          ROUND(AVG(competitor_final_price)::numeric, 0)  AS avg_price,
          ROUND(AVG(precio_sugerido)::numeric, 0)         AS avg_pvp
        FROM pricing_data
        WHERE marca = 'NIKE'
          AND scraper NOT IN ('nike_ar_general','nike_co_general','nike_us_general','URU','USA','ADIDAS_7','Puma_AR')
        GROUP BY scraper
        ORDER BY total DESC
      `),

      query(`
        SELECT
          scraper, style_color, marketing_name, franchise_scrapper,
          competitor_final_price, precio_sugerido,
          ROUND(((competitor_final_price - precio_sugerido) / NULLIF(precio_sugerido,0) * 100)::numeric, 1) AS diff_pct,
          link_pdp_competitor
        FROM pricing_data
        WHERE marca = 'NIKE'
          AND precio_sugerido IS NOT NULL
          AND competitor_final_price < precio_sugerido * 0.95
          AND scraper NOT IN ('nike_ar_general','nike_co_general','nike_us_general','URU','USA')
        ORDER BY diff_pct ASC
        LIMIT 100
      `),
    ])

    const retailersWithPVP = byRetailer.map((r: any) => ({
      ...r,
      compliance_pct: r.with_pvp > 0
        ? Math.round((r.compliant / r.with_pvp) * 100)
        : null,
    }))

    return NextResponse.json({ retailers: retailersWithPVP, violations })
  } catch (err) {
    console.error('[/api/pricing/pvp-compliance]', err)
    return NextResponse.json({ error: 'Error al obtener compliance' }, { status: 500 })
  }
}
