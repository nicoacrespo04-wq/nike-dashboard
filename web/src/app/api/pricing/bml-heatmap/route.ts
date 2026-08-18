import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { canonicalMarcaSql } from '@/lib/marca'
import { parseUniverse, retailerKeySql, retailerLabel, UNIVERSES } from '@/lib/scrapers'
import { presentOrNullSql } from '@/lib/missing'

export const dynamic = 'force-dynamic'

// Marca canónica y scrapers por clave canónica: `UPPER(marca) = 'NIKE'` y un
// `NOT IN ('ADIDAS_7')` fallan cerrados ante ' Nike ' o 'adidas_7' y dejan el
// heat-map vacío sin decir por qué (ver `lib/marca.ts` y `lib/scrapers.ts`).
const IS_NIKE = `${canonicalMarcaSql('marca')} = 'NIKE'`

// Un retailer = una clave canónica. Antes el heat-map se agrupaba por el
// nombre crudo del scraper y salían 33 columnas para 10 retailers: Open Sports
// ocupaba tres ('OpenSports_AR', 'opensports', 'Open Sports') y el usuario
// comparaba un retailer contra sí mismo. Medido con
// `curl /api/pricing/bml-heatmap`. Ver `lib/scrapers.ts::retailerKeySql`.
const RETAILER = retailerKeySql('scraper')

// La franquicia ausente se AGRUPA (no se descarta): el heat-map reparte el
// total de cada retailer y tirar la fila haría que las celdas no sumen. Pero
// `NULLIF(franchise_scrapper, '')` sólo atrapaba el vacío, así que `'-'` y
// `'s/d'` se dibujaban como dos franquicias más (28 celdas medidas con
// `curl /api/pricing/bml-heatmap`). Ver `lib/missing.ts`.
const FRANCHISE = `COALESCE(${presentOrNullSql('franchise_scrapper')}, 'Sin franchise')`

/**
 * Heat-map BML (Beat/Meet/Lose) por retailer × franchise, SOLO filas de
 * productos Nike (comparadas contra el precio de nike.com.ar, que es lo que ya
 * calcula `bml_final_price` en el pipeline).
 *
 * UNIVERSO: por defecto `gondola` — los retailers ARGENTINOS. Antes la route
 * no tenía universo y usaba `RETAILERS_AR_SQL`, que se definía sólo por "no es
 * un sitio de marca" y por lo tanto dejaba entrar los catálogos chilenos: el
 * heat-map mostraba columnas `MercadoLibre_CL`, `OpenSports_CL` y
 * `StockCenter_CL` mezcladas con las argentinas. Ahora el universo es un
 * parámetro explícito (`?universe=gondola|d2c|all_ar`) con default seguro, y
 * los tres universos son argentinos (ver `lib/scrapers.ts`).
 */
export async function GET(req: NextRequest) {
  const universe = parseUniverse(req.nextUrl.searchParams.get('universe'))
  try {
    const rows = await query<{
      scraper: string
      variants: string[]
      franchise: string
      total: number
      beat: number
      meet: number
      lose: number
      nd: number
      avg_gap_pct: number
    }>(`
      SELECT
        ${RETAILER}                                       AS scraper,
        ARRAY_AGG(DISTINCT scraper)                       AS variants,
        ${FRANCHISE}                                      AS franchise,
        COUNT(*)                                          AS total,
        COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')  AS beat,
        COUNT(*) FILTER (WHERE bml_final_price = 'MEET')  AS meet,
        COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')  AS lose,
        COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd,
        ROUND(AVG(gap_final_price_pct) FILTER (WHERE gap_final_price_pct IS NOT NULL)::numeric, 4) AS avg_gap_pct
      FROM pricing_data
      WHERE ${IS_NIKE}
        AND ${UNIVERSES[universe].sql}
      GROUP BY ${RETAILER}, ${FRANCHISE}
      HAVING COUNT(*) >= 3
      ORDER BY 1, total DESC
    `)

    // Una etiqueta por retailer, elegida una sola vez sobre TODAS sus
    // escrituras (no por fila): así la columna se llama igual en todas las
    // franquicias. Ver `lib/scrapers.ts::retailerLabel`.
    const variantsByKey = new Map<string, Set<string>>()
    for (const r of rows) {
      const set = variantsByKey.get(r.scraper) ?? new Set<string>()
      for (const v of r.variants ?? []) set.add(v)
      variantsByKey.set(r.scraper, set)
    }
    const labelByKey = new Map(
      Array.from(variantsByKey.entries()).map(([key, set]) => [key, retailerLabel(Array.from(set))]),
    )
    for (const r of rows) r.scraper = labelByKey.get(r.scraper) ?? r.scraper

    const retailers = Array.from(new Set(rows.map((r) => r.scraper))).sort()
    const franchises = Array.from(new Set(rows.map((r) => r.franchise)))
      .sort((a, b) => {
        const totalA = rows.filter((r) => r.franchise === a).reduce((s, r) => s + Number(r.total), 0)
        const totalB = rows.filter((r) => r.franchise === b).reduce((s, r) => s + Number(r.total), 0)
        return totalB - totalA
      })
      .slice(0, 25)

    return NextResponse.json({
      rows,
      retailers,
      franchises,
      universe,
      universeLabel: UNIVERSES[universe].label,
      universeDescription: UNIVERSES[universe].description,
    })
  } catch (err) {
    console.error('[/api/pricing/bml-heatmap]', err)
    return NextResponse.json({ error: 'Error al obtener heatmap BML' }, { status: 500 })
  }
}
