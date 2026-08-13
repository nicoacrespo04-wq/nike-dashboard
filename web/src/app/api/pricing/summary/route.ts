import { NextResponse } from 'next/server'
import { query } from '@/lib/db'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const [kpis, bmlAdidas, bmlPuma, topAdidas, topPuma] = await Promise.all([
      // KPIs generales — DISTINCT por StyleColor para no contar duplicados cross-retailer
      query(`
        SELECT
          COUNT(DISTINCT CASE WHEN marca = 'ADIDAS' THEN style_color END) AS adidas_total,
          COUNT(DISTINCT CASE WHEN marca = 'PUMA'   THEN style_color END) AS puma_total,
          COUNT(DISTINCT CASE WHEN marca = 'NIKE'   THEN style_color END) AS nike_total,
          ROUND(AVG(competitor_final_price) FILTER (WHERE marca = 'ADIDAS')::numeric, 0) AS adidas_avg_price,
          ROUND(AVG(competitor_final_price) FILTER (WHERE marca = 'PUMA')::numeric, 0)   AS puma_avg_price,
          ROUND(AVG(competitor_final_price) FILTER (WHERE marca = 'NIKE')::numeric, 0)   AS nike_avg_price,
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS total_beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS total_meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS total_lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS total_nd
        FROM pricing_data
        WHERE marca IN ('ADIDAS','PUMA','NIKE')
      `),

      // BML Adidas
      query(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data WHERE marca = 'ADIDAS'
      `),

      // BML Puma
      query(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data WHERE marca = 'PUMA'
      `),

      // Top 10 franchises Adidas
      query(`
        SELECT franchise_competitor AS franchise, COUNT(*) AS count,
          ROUND(AVG(competitor_final_price)::numeric,0) AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4)    AS avg_gap_pct
        FROM pricing_data
        WHERE marca = 'ADIDAS'
          AND franchise_competitor IS NOT NULL AND franchise_competitor <> ''
        GROUP BY franchise_competitor
        ORDER BY count DESC LIMIT 10
      `),

      // Top 10 franchises Puma
      query(`
        SELECT franchise_competitor AS franchise, COUNT(*) AS count,
          ROUND(AVG(competitor_final_price)::numeric,0) AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4)    AS avg_gap_pct
        FROM pricing_data
        WHERE marca = 'PUMA'
          AND franchise_competitor IS NOT NULL AND franchise_competitor <> ''
        GROUP BY franchise_competitor
        ORDER BY count DESC LIMIT 10
      `),
    ])

    return NextResponse.json({
      kpis:       kpis[0],
      bml_adidas: bmlAdidas[0],
      bml_puma:   bmlPuma[0],
      top_adidas: topAdidas,
      top_puma:   topPuma,
    })
  } catch (err) {
    console.error('[/api/pricing/summary]', err)
    return NextResponse.json({ error: 'Error al obtener resumen' }, { status: 500 })
  }
}
