import { NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { isValidPriceSql, validPriceSql } from '@/lib/price'
import { canonicalMarcaSql } from '@/lib/marca'
import { NIKE_D2C_FOREIGN, RETAILERS_AR_SQL, scraperNotInSql } from '@/lib/scrapers'

export const dynamic = 'force-dynamic'

// Sólo góndola: los sitios de marca no son retailers. La comparación va por
// clave canónica de scraper ('ADIDAS_7' y 'adidas_7' son el mismo canal).
const ONLY_RETAILERS = RETAILERS_AR_SQL

// Marca canónica en vez de `UPPER(marca)`: un ' Nike ' con espacio invisible
// abría una fila aparte y partía el conteo de la marca (ver `lib/marca.ts`).
const MARCA_CANON = canonicalMarcaSql('marca')

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
          ${MARCA_CANON} AS marca,
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
        WHERE ${ONLY_RETAILERS}
        GROUP BY scraper, ${MARCA_CANON}
        ORDER BY promo_pct DESC NULLS LAST
      `),

      // Markdown por marca (Nike vs Adidas vs Puma en retailers).
      // UPPER(marca) normaliza el casing: la UI busca 'NIKE' / 'ADIDAS' /
      // 'PUMA' y en la base conviven 'Puma' y 'PUMA'.
      query(`
        SELECT
          ${MARCA_CANON} AS marca,
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
        WHERE ${ONLY_RETAILERS}
        GROUP BY ${MARCA_CANON}
        ORDER BY promo_pct DESC NULLS LAST
      `),

      // Top productos con mayor markdown
      query(`
        SELECT
          scraper, ${MARCA_CANON} AS marca, style_color, product_name_competitor,
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
          AND ${scraperNotInSql(NIKE_D2C_FOREIGN)}
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
