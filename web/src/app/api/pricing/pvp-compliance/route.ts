import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { isValidPriceSql, validPriceSql } from '@/lib/price'

export const dynamic = 'force-dynamic'

// Excluir canales propios de Nike y D2C de la competencia: acá medimos
// cumplimiento de PVP de los RETAILERS.
const EXCLUDED_SCRAPERS = `'nike_ar_general','nike_co_general','nike_us_general','URU','USA','ADIDAS_7','Puma_AR'`

// Precios y PVP saneados: `<= 0` y outliers del bug de cuotas quedan en NULL
// y no participan de ningún conteo ni promedio (ver web/src/lib/price.ts).
const PRICE = validPriceSql('competitor_final_price')
const PVP = validPriceSql('precio_sugerido')
const HAS_PRICE = isValidPriceSql('competitor_final_price')
const HAS_PVP = isValidPriceSql('precio_sugerido')

const MAX_PAGE_SIZE = 500
const DEFAULT_PAGE_SIZE = 50

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const page = Math.max(1, parseInt(searchParams.get('page') ?? '1') || 1)
  const pageSize = Math.min(
    MAX_PAGE_SIZE,
    Math.max(1, parseInt(searchParams.get('pageSize') ?? String(DEFAULT_PAGE_SIZE)) || DEFAULT_PAGE_SIZE)
  )
  const offset = (page - 1) * pageSize

  try {
    const [byRetailer, violations, violationsCount] = await Promise.all([
      // Cumplimiento por retailer.
      // `with_pvp` exige PVP **y** precio válidos, para que
      // compliant + below_pvp + above_pvp === with_pvp siempre cierre.
      // Las filas con PVP pero sin precio usable se reportan aparte en
      // `without_price` en vez de desbalancear la tabla.
      query(`
        SELECT
          scraper,
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE ${HAS_PVP} AND ${HAS_PRICE}) AS with_pvp,
          COUNT(*) FILTER (
            WHERE ${HAS_PVP} AND ${HAS_PRICE}
              AND ABS((${PRICE} - ${PVP}) / ${PVP}) <= 0.05
          ) AS compliant,
          COUNT(*) FILTER (
            WHERE ${HAS_PVP} AND ${HAS_PRICE}
              AND ${PRICE} < ${PVP} * 0.95
          ) AS below_pvp,
          COUNT(*) FILTER (
            WHERE ${HAS_PVP} AND ${HAS_PRICE}
              AND ${PRICE} > ${PVP} * 1.05
          ) AS above_pvp,
          COUNT(*) FILTER (WHERE ${HAS_PVP} AND NOT ${HAS_PRICE}) AS without_price,
          ROUND(AVG(${PRICE})::numeric, 0) AS avg_price,
          ROUND(AVG(${PVP})::numeric, 0)   AS avg_pvp
        FROM pricing_data
        WHERE UPPER(marca) = 'NIKE'
          AND scraper NOT IN (${EXCLUDED_SCRAPERS})
        GROUP BY scraper
        ORDER BY total DESC
      `),

      // Tabla de violaciones — PAGINADA. El KPI NO se calcula con el largo de
      // este array (ése era el bug: LIMIT 100 fijo => el KPI decía 100).
      query(`
        SELECT
          scraper, style_color, marketing_name, franchise_scrapper,
          ${PRICE} AS competitor_final_price,
          ${PVP}   AS precio_sugerido,
          ROUND(((${PRICE} - ${PVP}) / ${PVP} * 100)::numeric, 1) AS diff_pct,
          link_pdp_competitor
        FROM pricing_data
        WHERE UPPER(marca) = 'NIKE'
          AND ${HAS_PVP}
          AND ${HAS_PRICE}
          AND ${PRICE} < ${PVP} * 0.95
          AND scraper NOT IN (${EXCLUDED_SCRAPERS})
        ORDER BY diff_pct ASC, scraper, style_color
        LIMIT $1 OFFSET $2
      `, [pageSize, offset]),

      // Total real de violaciones (misma condición, sin LIMIT).
      query<{ total: string }>(`
        SELECT COUNT(*) AS total
        FROM pricing_data
        WHERE UPPER(marca) = 'NIKE'
          AND ${HAS_PVP}
          AND ${HAS_PRICE}
          AND ${PRICE} < ${PVP} * 0.95
          AND scraper NOT IN (${EXCLUDED_SCRAPERS})
      `),
    ])

    const retailersWithPVP = byRetailer.map((r: any) => ({
      ...r,
      compliance_pct: Number(r.with_pvp) > 0
        ? Math.round((Number(r.compliant) / Number(r.with_pvp)) * 100)
        : null,
    }))

    const violationsTotal = parseInt(String(violationsCount[0]?.total ?? '0')) || 0

    return NextResponse.json({
      retailers: retailersWithPVP,
      violations,
      violations_total: violationsTotal,
      violations_page: page,
      violations_page_size: pageSize,
      violations_total_pages: Math.ceil(violationsTotal / pageSize),
    })
  } catch (err) {
    console.error('[/api/pricing/pvp-compliance]', err)
    return NextResponse.json({ error: 'Error al obtener compliance' }, { status: 500 })
  }
}
