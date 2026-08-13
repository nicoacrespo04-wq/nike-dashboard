import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const [byRetailer, byMarca, topMarkdowns] = await Promise.all([
      // Markdown por retailer
      query(`
        SELECT
          scraper,
          marca,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE competitor_markdown > 0) AS in_promo,
          ROUND((COUNT(*) FILTER (WHERE competitor_markdown > 0) * 100.0 / NULLIF(COUNT(*),0))::numeric, 1) AS promo_pct,
          ROUND(AVG(competitor_markdown) FILTER (WHERE competitor_markdown > 0)::numeric, 0) AS avg_markdown_abs,
          ROUND(
            AVG((competitor_markdown / NULLIF(competitor_full_price, 0)) * 100)
            FILTER (WHERE competitor_markdown > 0)::numeric, 1
          ) AS avg_markdown_pct
        FROM pricing_data
        WHERE scraper NOT IN ('ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA')
        GROUP BY scraper, marca
        ORDER BY promo_pct DESC
      `),

      // Markdown por marca (Adidas vs Puma vs Nike en retailers)
      query(`
        SELECT
          marca,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE competitor_markdown > 0) AS in_promo,
          ROUND((COUNT(*) FILTER (WHERE competitor_markdown > 0) * 100.0 / NULLIF(COUNT(*),0))::numeric, 1) AS promo_pct,
          ROUND(AVG((competitor_markdown / NULLIF(competitor_full_price,0)) * 100)
            FILTER (WHERE competitor_markdown > 0)::numeric, 1) AS avg_markdown_pct
        FROM pricing_data
        WHERE scraper NOT IN ('ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA')
        GROUP BY marca
        ORDER BY promo_pct DESC
      `),

      // Top productos con mayor markdown
      query(`
        SELECT
          scraper, marca, style_color, product_name_competitor,
          franchise_competitor, silueta,
          competitor_full_price, competitor_final_price, competitor_markdown,
          ROUND((competitor_markdown / NULLIF(competitor_full_price,0) * 100)::numeric, 1) AS markdown_pct,
          bml_final_price, link_pdp_competitor
        FROM pricing_data
        WHERE competitor_markdown > 0
          AND competitor_full_price > 0
          AND scraper NOT IN ('nike_ar_general','nike_co_general','nike_us_general','URU','USA')
        ORDER BY markdown_pct DESC
        LIMIT 100
      `),
    ])

    return NextResponse.json({ by_retailer: byRetailer, by_marca: byMarca, top_markdowns: topMarkdowns })
  } catch (err) {
    console.error('[/api/pricing/markdown-analysis]', err)
    return NextResponse.json({ error: 'Error al obtener markdown' }, { status: 500 })
  }
}
