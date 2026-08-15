import { NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { isValidPriceSql, validPriceSql } from '@/lib/price'

export const dynamic = 'force-dynamic'

const EXCLUDED_SCRAPERS = `'ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA'`

// Precios saneados (ver web/src/lib/price.ts): `<= 0` y outliers del bug de
// cuotas quedan NULL y no entran en promedios ni en los conteos de promo.
const FINAL = validPriceSql('competitor_final_price')
const FULL = validPriceSql('competitor_full_price')
const HAS_FINAL = isValidPriceSql('competitor_final_price')
const HAS_FULL = isValidPriceSql('competitor_full_price')

export async function GET() {
  try {
    const [byRetailer, byMarca, topMarkdowns] = await Promise.all([
      // Markdown por retailer.
      // `promo_pct` se calcula sobre `with_price` (filas con precio usable),
      // no sobre el total crudo: si no hay precio válido no sabemos si está
      // en promo, y contarla como "sin promo" subestimaría el %.
      query(`
        SELECT
          scraper,
          UPPER(marca) AS marca,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ${HAS_FINAL}) AS with_price,
          COUNT(*) FILTER (WHERE ${HAS_FINAL} AND competitor_markdown > 0) AS in_promo,
          ROUND((
            COUNT(*) FILTER (WHERE ${HAS_FINAL} AND competitor_markdown > 0) * 100.0
            / NULLIF(COUNT(*) FILTER (WHERE ${HAS_FINAL}), 0)
          )::numeric, 1) AS promo_pct,
          ROUND(AVG(competitor_markdown) FILTER (WHERE ${HAS_FINAL} AND competitor_markdown > 0)::numeric, 0) AS avg_markdown_abs,
          ROUND(
            AVG((competitor_markdown / ${FULL}) * 100)
            FILTER (WHERE ${HAS_FULL} AND competitor_markdown > 0)::numeric, 1
          ) AS avg_markdown_pct
        FROM pricing_data
        WHERE scraper NOT IN (${EXCLUDED_SCRAPERS})
        GROUP BY scraper, UPPER(marca)
        ORDER BY promo_pct DESC NULLS LAST
      `),

      // Markdown por marca (Nike vs Adidas vs Puma en retailers).
      // UPPER(marca) normaliza el casing: la UI busca 'NIKE' / 'ADIDAS' /
      // 'PUMA' y en la base conviven 'Puma' y 'PUMA'.
      query(`
        SELECT
          UPPER(marca) AS marca,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ${HAS_FINAL}) AS with_price,
          COUNT(*) FILTER (WHERE ${HAS_FINAL} AND competitor_markdown > 0) AS in_promo,
          ROUND((
            COUNT(*) FILTER (WHERE ${HAS_FINAL} AND competitor_markdown > 0) * 100.0
            / NULLIF(COUNT(*) FILTER (WHERE ${HAS_FINAL}), 0)
          )::numeric, 1) AS promo_pct,
          ROUND(AVG((competitor_markdown / ${FULL}) * 100)
            FILTER (WHERE ${HAS_FULL} AND competitor_markdown > 0)::numeric, 1) AS avg_markdown_pct
        FROM pricing_data
        WHERE scraper NOT IN (${EXCLUDED_SCRAPERS})
        GROUP BY UPPER(marca)
        ORDER BY promo_pct DESC NULLS LAST
      `),

      // Top productos con mayor markdown
      query(`
        SELECT
          scraper, UPPER(marca) AS marca, style_color, product_name_competitor,
          franchise_competitor, silueta,
          ${FULL}  AS competitor_full_price,
          ${FINAL} AS competitor_final_price,
          competitor_markdown,
          ROUND((competitor_markdown / ${FULL} * 100)::numeric, 1) AS markdown_pct,
          bml_final_price, link_pdp_competitor
        FROM pricing_data
        WHERE competitor_markdown > 0
          AND ${HAS_FULL}
          AND ${HAS_FINAL}
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
