import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'

export const dynamic = 'force-dynamic'

// Precios saneados (ver web/src/lib/price.ts): un precio en 0 o inflado por
// el bug de cuotas no debe mover el precio promedio de la franchise.
const FINAL = validPriceSql('competitor_final_price')
const FULL = validPriceSql('competitor_full_price')

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const marca    = searchParams.get('marca')    ?? ''
  const division = searchParams.get('division') ?? ''
  const category = searchParams.get('category') ?? ''
  const gender   = searchParams.get('gender')   ?? ''
  const canal    = searchParams.get('canal')    ?? '' // 'd2c' | 'b2b' | ''

  const baseConditions: string[] = [
    `franchise_competitor IS NOT NULL`,
    `franchise_competitor <> ''`,
  ]
  const params: unknown[] = []
  let idx = 1

  // UPPER() en ambos lados: en la base conviven 'Puma'/'PUMA' y 'Nike'/'NIKE'.
  if (marca) { baseConditions.push(`UPPER(marca) = UPPER($${idx++})`); params.push(marca) }
  else       { baseConditions.push(`UPPER(marca) IN ('ADIDAS','PUMA')`) }

  if (division) { baseConditions.push(`UPPER(division_competitor) LIKE $${idx++}`); params.push(`%${division.toUpperCase()}%`) }
  if (gender)   { baseConditions.push(`gender_competitor = $${idx++}`); params.push(gender) }

  if (canal === 'd2c') {
    baseConditions.push(`scraper IN ('ADIDAS_7','Puma_AR')`)
  } else if (canal === 'b2b') {
    baseConditions.push(`scraper NOT IN ('ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA')`)
  }

  // El filtro de categoría NO se aplica al listado de categorías disponibles,
  // si no el <select> se colapsaría a la opción ya elegida.
  const categoriesWhere = baseConditions.join(' AND ')
  const categoriesParams = [...params]

  const conditions = [...baseConditions]
  if (category) { conditions.push(`category_competitor = $${idx++}`); params.push(category) }
  const where = conditions.join(' AND ')

  try {
    const [rows, categories] = await Promise.all([
      // COUNT(DISTINCT style_color): un mismo SKU aparece en varios retailers.
      // Con COUNT(*) los conteos salían inflados (ver commit abbce1a).
      query(`
        SELECT
          franchise_competitor                                        AS franchise,
          UPPER(marca)                                                AS marca,
          division_competitor                                         AS division,
          COUNT(DISTINCT style_color)                                 AS count,
          COUNT(*)                                                    AS rows_count,
          ROUND(AVG(${FINAL})::numeric, 0)                            AS avg_price,
          ROUND(AVG(${FULL})::numeric, 0)                             AS avg_full_price,
          ROUND(AVG(gap_final_price_pct)::numeric, 4)                 AS avg_gap_pct,
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')            AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET')            AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')            AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd,
          ROUND(AVG(size_available_competitor)::numeric, 1)           AS avg_sizes,
          COUNT(DISTINCT style_color) FILTER (WHERE competitor_markdown > 0) AS in_promo,
          ROUND((
            COUNT(DISTINCT style_color) FILTER (WHERE competitor_markdown > 0) * 100.0
            / NULLIF(COUNT(DISTINCT style_color), 0)
          )::numeric, 1)                                              AS promo_pct,
          ROUND(AVG(competitor_markdown) FILTER (WHERE competitor_markdown > 0)::numeric, 0) AS avg_markdown
        FROM pricing_data
        WHERE ${where}
        GROUP BY franchise_competitor, UPPER(marca), division_competitor
        ORDER BY count DESC
        LIMIT 100
      `, params),

      query<{ category: string }>(`
        SELECT DISTINCT category_competitor AS category
        FROM pricing_data
        WHERE ${categoriesWhere}
          AND category_competitor IS NOT NULL
          AND category_competitor <> ''
        ORDER BY category
      `, categoriesParams),
    ])

    return NextResponse.json({
      franchises: rows,
      categories: categories.map((c) => c.category),
    })
  } catch (err) {
    console.error('[/api/pricing/franchises]', err)
    return NextResponse.json({ error: 'Error al obtener franchises' }, { status: 500 })
  }
}
