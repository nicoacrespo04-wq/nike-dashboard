import { NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'

export const dynamic = 'force-dynamic'

// Precio saneado (ver web/src/lib/price.ts): los 0 y los inflados por cuotas
// quedan NULL y no arrastran los promedios que se muestran en los KPI.
const FINAL = validPriceSql('competitor_final_price')

export async function GET() {
  try {
    const [kpis, bmlAdidas, bmlPuma, topAdidas, topPuma] = await Promise.all([
      // KPIs generales — DISTINCT por StyleColor para no contar duplicados cross-retailer
      query(`
        SELECT
          COUNT(DISTINCT CASE WHEN UPPER(marca) = 'ADIDAS' THEN style_color END) AS adidas_total,
          COUNT(DISTINCT CASE WHEN UPPER(marca) = 'PUMA'   THEN style_color END) AS puma_total,
          COUNT(DISTINCT CASE WHEN UPPER(marca) = 'NIKE'   THEN style_color END) AS nike_total,
          ROUND(AVG(${FINAL}) FILTER (WHERE UPPER(marca) = 'ADIDAS')::numeric, 0) AS adidas_avg_price,
          ROUND(AVG(${FINAL}) FILTER (WHERE UPPER(marca) = 'PUMA')::numeric, 0)   AS puma_avg_price,
          ROUND(AVG(${FINAL}) FILTER (WHERE UPPER(marca) = 'NIKE')::numeric, 0)   AS nike_avg_price,
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS total_beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS total_meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS total_lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS total_nd
        FROM pricing_data
        WHERE UPPER(marca) IN ('ADIDAS','PUMA','NIKE')
      `),

      // BML Adidas
      query(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data WHERE UPPER(marca) = 'ADIDAS'
      `),

      // BML Puma
      query(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data WHERE UPPER(marca) = 'PUMA'
      `),

      // Top 10 franchises Adidas
      query(`
        SELECT franchise_competitor AS franchise,
          COUNT(DISTINCT style_color) AS count,
          ROUND(AVG(${FINAL})::numeric,0)            AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4) AS avg_gap_pct
        FROM pricing_data
        WHERE UPPER(marca) = 'ADIDAS'
          AND franchise_competitor IS NOT NULL AND franchise_competitor <> ''
        GROUP BY franchise_competitor
        ORDER BY count DESC LIMIT 10
      `),

      // Top 10 franchises Puma
      query(`
        SELECT franchise_competitor AS franchise,
          COUNT(DISTINCT style_color) AS count,
          ROUND(AVG(${FINAL})::numeric,0)            AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4) AS avg_gap_pct
        FROM pricing_data
        WHERE UPPER(marca) = 'PUMA'
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
