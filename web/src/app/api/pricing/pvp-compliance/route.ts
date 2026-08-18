import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { isValidPriceSql, validPriceSql } from '@/lib/price'
import { canonicalMarcaSql } from '@/lib/marca'
import { RETAILERS_AR_SQL, retailerKey, retailerKeySql, retailerLabel } from '@/lib/scrapers'

export const dynamic = 'force-dynamic'

// Excluir canales propios de Nike y D2C de la competencia: acá medimos
// cumplimiento de PVP de los RETAILERS ARGENTINOS. La comparación va por clave
// canónica de scraper: en los datos reales conviven 'ADIDAS_7' y 'adidas_7', y
// un `NOT IN ('ADIDAS_7')` deja pasar el segundo (ver `lib/scrapers.ts`).
//
// `RETAILERS_AR_SQL` ahora también acota el PAÍS. Antes no lo hacía y este
// endpoint devolvía `OpenSports_CL` (135 filas), `StockCenter_CL` (24),
// `Sporting_CL` (15) y 7 retailers chilenos más, midiendo su "cumplimiento"
// contra un `precio_sugerido` que es de la lista de precios ARGENTINA — o sea
// comparando un precio en CLP contra un PVP en ARS. Medido con
// `curl /api/pricing/pvp-compliance`.
const ONLY_RETAILERS = RETAILERS_AR_SQL

// Un retailer = una clave canónica, no una escritura del scraper. Sin esto la
// tabla listaba 40 filas para 10 retailers: Open Sports aparecía tres veces
// ('OpenSports_AR' 2.281 filas / 'opensports' 2.278 / 'Open Sports' 2.205) con
// tres porcentajes de cumplimiento distintos (5% / 7% / 6%), ninguno el real.
// Ver `lib/scrapers.ts::retailerKeySql` y `retailerLabel`.
const RETAILER = retailerKeySql('scraper')

// Marca canónica: `UPPER(marca) = 'NIKE'` se queda con CERO filas ante un
// ' Nike ' con un espacio invisible, y la pantalla entera queda vacía sin
// decir por qué (ver `lib/marca.ts`).
const IS_NIKE = `${canonicalMarcaSql('marca')} = 'NIKE'`

// Precios y PVP saneados: `<= 0` y outliers del bug de cuotas quedan en NULL
// y no participan de ningún conteo ni promedio (ver web/src/lib/price.ts).
const PRICE = validPriceSql('competitor_final_price')
const PVP = validPriceSql('precio_sugerido')
const HAS_PRICE = isValidPriceSql('competitor_final_price')
const HAS_PVP = isValidPriceSql('precio_sugerido')

/** Cumplimiento de PVP de un retailer, tal cual sale de Postgres. */
interface RetailerComplianceRow {
  /** Clave canónica del retailer (sin casing, sin puntuación, sin sufijo de país). */
  scraper: string
  /** Todas las escrituras crudas con las que el scraper nombró a este retailer. */
  variants: string[]
  total: string
  with_pvp: string
  compliant: string
  below_pvp: string
  above_pvp: string
  without_price: string
  avg_price: string | null
  avg_pvp: string | null
}

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
      query<RetailerComplianceRow>(`
        SELECT
          ${RETAILER}                 AS scraper,
          ARRAY_AGG(DISTINCT scraper) AS variants,
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
        WHERE ${IS_NIKE}
          AND ${ONLY_RETAILERS}
        GROUP BY ${RETAILER}
        ORDER BY total DESC
      `),

      // Tabla de violaciones — PAGINADA. El KPI NO se calcula con el largo de
      // este array (ése era el bug: LIMIT 100 fijo => el KPI decía 100).
      query(`
        SELECT
          ${RETAILER} AS scraper_key, scraper, style_color, marketing_name, franchise_scrapper,
          ${PRICE} AS competitor_final_price,
          ${PVP}   AS precio_sugerido,
          ROUND(((${PRICE} - ${PVP}) / ${PVP} * 100)::numeric, 1) AS diff_pct,
          link_pdp_competitor
        FROM pricing_data
        WHERE ${IS_NIKE}
          AND ${HAS_PVP}
          AND ${HAS_PRICE}
          AND ${PRICE} < ${PVP} * 0.95
          AND ${ONLY_RETAILERS}
        ORDER BY diff_pct ASC, scraper, style_color
        LIMIT $1 OFFSET $2
      `, [pageSize, offset]),

      // Total real de violaciones (misma condición, sin LIMIT).
      query<{ total: string }>(`
        SELECT COUNT(*) AS total
        FROM pricing_data
        WHERE ${IS_NIKE}
          AND ${HAS_PVP}
          AND ${HAS_PRICE}
          AND ${PRICE} < ${PVP} * 0.95
          AND ${ONLY_RETAILERS}
      `),
    ])

    // La etiqueta se decide una sola vez por retailer y se reusa en las dos
    // tablas, para que "Open Sports" se llame igual arriba y abajo.
    const labelByKey = new Map(byRetailer.map((r) => [r.scraper, retailerLabel(r.variants)]))

    const retailersWithPVP = byRetailer.map((r) => ({
      ...r,
      // `scraper` viaja como la ETIQUETA legible (es lo que la UI ya
      // renderiza tal cual); la clave canónica queda aparte por si hace falta.
      scraper: labelByKey.get(r.scraper) ?? r.scraper,
      scraper_key: r.scraper,
      compliance_pct: Number(r.with_pvp) > 0
        ? Math.round((Number(r.compliant) / Number(r.with_pvp)) * 100)
        : null,
    }))

    // Misma etiqueta en el detalle de violaciones. Si una violación viniera de
    // un retailer que no quedó en el agregado, se recalcula su clave en vez de
    // mostrar el nombre crudo.
    const violationsLabeled = (violations as Record<string, unknown>[]).map((v) => ({
      ...v,
      scraper:
        labelByKey.get(String(v.scraper_key ?? retailerKey(String(v.scraper ?? '')))) ??
        String(v.scraper ?? ''),
    }))

    const violationsTotal = parseInt(String(violationsCount[0]?.total ?? '0')) || 0

    return NextResponse.json({
      retailers: retailersWithPVP,
      violations: violationsLabeled,
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
