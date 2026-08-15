import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { sanitizeRowsPrices, validPriceSql } from '@/lib/price'

export const dynamic = 'force-dynamic'

// Endpoint de detalle (fila a fila): traemos el precio crudo + las cuotas y
// dejamos que `sanitizeRowsPrices` decida, porque a nivel fila SÍ podemos
// RECUPERAR el precio real dividiendo por las cuotas (bug del scraper que
// guardaba el total financiado). Los agregados, en cambio, se filtran en SQL.
const COMPETITOR_PRICE_FIELDS = [
  'competitor_full_price',
  'competitor_final_price',
  'competitor_shipping',
] as const

// Estos no dependen de `cuotas_competitor`: sólo se les aplica el chequeo de
// rango / `<= 0`.
const OTHER_PRICE_FIELDS = [
  'nike_full_price',
  'nike_final_price',
  'precio_sugerido',
] as const

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const page      = Math.max(1, parseInt(searchParams.get('page') ?? '1'))
  const pageSize  = Math.min(100, parseInt(searchParams.get('pageSize') ?? '50'))
  const offset    = (page - 1) * pageSize

  const marca     = searchParams.get('marca')     ?? ''
  const franchise = searchParams.get('franchise') ?? ''
  const silueta   = searchParams.get('silueta')   ?? ''
  const category  = searchParams.get('category')  ?? ''
  const bml       = searchParams.get('bml')       ?? ''
  const gender    = searchParams.get('gender')    ?? ''
  const scraper   = searchParams.get('scraper')   ?? ''
  const search    = searchParams.get('search')    ?? ''
  const canal     = searchParams.get('canal')     ?? ''

  const conditions: string[] = []
  const params: unknown[] = []
  let idx = 1

  // UPPER() en ambos lados: en la base conviven 'Puma'/'PUMA', 'Nike'/'NIKE'.
  if (marca)     { conditions.push(`UPPER(marca) = UPPER($${idx++})`);      params.push(marca) }
  if (franchise) { conditions.push(`franchise_competitor ILIKE $${idx++}`); params.push(`%${franchise}%`) }
  if (silueta)   { conditions.push(`silueta = $${idx++}`);                  params.push(silueta) }
  if (category)  { conditions.push(`category_competitor = $${idx++}`);      params.push(category) }
  if (bml)       { conditions.push(`bml_final_price = $${idx++}`);          params.push(bml) }
  if (gender)    { conditions.push(`gender_competitor = $${idx++}`);        params.push(gender) }
  if (scraper)   { conditions.push(`scraper = $${idx++}`);                  params.push(scraper) }
  if (search)    {
    conditions.push(`(product_name_competitor ILIKE $${idx} OR style_color ILIKE $${idx} OR franchise_competitor ILIKE $${idx})`)
    params.push(`%${search}%`); idx++
  }
  if (canal === 'd2c') conditions.push(`scraper IN ('ADIDAS_7','Puma_AR','nike_ar_general')`)
  if (canal === 'b2b') conditions.push(`scraper NOT IN ('ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA')`)

  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : ''

  try {
    const [rows, countRows] = await Promise.all([
      query(`
        SELECT id, fecha_corrida, scraper, canal, marca, season,
          style_color, productcode_competitor, product_name_competitor,
          marketing_name, category_competitor, division_competitor,
          franchise_competitor, gender_competitor, silueta,
          size_available_competitor, text_sizes_competitor,
          competitor_full_price, competitor_markdown, competitor_final_price,
          competitor_shipping, cuotas_competitor,
          nike_full_price, nike_final_price,
          gap_final_price_pct, gap_full_price_pct,
          bml_final_price, bml_full_price, bml_vs_nikear,
          link_pdp_competitor, precio_sugerido, fx_ars_usd,
          competitor_price_usd
        FROM pricing_data
        ${where}
        ORDER BY ${validPriceSql('competitor_final_price')} DESC NULLS LAST
        LIMIT $${idx} OFFSET $${idx + 1}
      `, [...params, pageSize, offset]),

      query(`SELECT COUNT(*) AS total FROM pricing_data ${where}`, params),
    ])

    // Defensa en la capa de lectura: un precio sucio nunca llega a la UI como
    // si fuera válido. `null` se renderiza como "N/D" (nunca "$0").
    const products = sanitizeRowsPrices(
      sanitizeRowsPrices(rows as Record<string, unknown>[], COMPETITOR_PRICE_FIELDS, 'cuotas_competitor'),
      OTHER_PRICE_FIELDS
    )

    return NextResponse.json({
      products,
      total:      parseInt(String(countRows[0]?.total ?? '0')),
      page,
      pageSize,
      totalPages: Math.ceil(parseInt(String(countRows[0]?.total ?? '0')) / pageSize),
    })
  } catch (err) {
    console.error('[/api/pricing/products]', err)
    return NextResponse.json({ error: 'Error al obtener productos' }, { status: 500 })
  }
}
