import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'
import { canonicalMarca, marcaKey, marcaNormSql, type MarcaKey } from '@/lib/marca'
import {
  OBSERVED_SKU_SQL,
  parseUniverse,
  UNIVERSES,
  UNIVERSE_KEYS,
  type UniverseKey,
} from '@/lib/scrapers'

export const dynamic = 'force-dynamic'

// Precio saneado (ver web/src/lib/price.ts): los 0 y los inflados por cuotas
// quedan NULL y no arrastran los promedios que se muestran en los KPI.
const FINAL = validPriceSql('competitor_final_price')

// Marca normalizada. `UPPER(marca)` no alcanza: la base trae ' Nike ' con
// espacios (incluso invisibles) y cualquier comparación contra el literal
// 'NIKE' se queda con cero filas en silencio. Ver `lib/marca.ts`.
const MARCA_NORM = marcaNormSql('marca')

// ─────────────────────────────────────────────────────────────────────────
// EL KPI DE "SKUs ÚNICOS" — QUÉ SE CUENTA Y EN QUÉ UNIVERSO
// ─────────────────────────────────────────────────────────────────────────
// Antes: `COUNT(DISTINCT style_color) FILTER (marca = 'X')` sobre TODA la
// tabla. Daba Nike 27.358 vs Adidas 9.052 vs Puma 6.789, una comparación que
// el negocio no puede usar. Dos causas, las dos verificadas contra un Postgres
// local con el fixture del repo (detalle largo en `lib/scrapers.ts`):
//
//   · `style_color` es el SKU del producto NIKE de referencia de la fila, no
//     el del producto observado. El mismo `style_color` aparece bajo 11
//     `marca` distintas. Se cuenta `OBSERVED_SKU_SQL`, que es el código propio
//     del producto observado.
//
//   · No había filtro de canal, así que Nike sumaba los retailers argentinos
//     + nike.com.ar + nike.com.co + nike.com + URU/USA, mientras Adidas y Puma
//     sólo tienen feeds argentinos.
//
// DECISIÓN: el KPI se calcula SIEMPRE dentro de un universo explícito, y el
// universo viaja en la respuesta para que la UI lo muestre. El default es
// `gondola` (los 8 retailers AR), el único donde las tres marcas se observan
// realmente una al lado de la otra. Los otros universos se devuelven también
// —etiquetados— para que se puedan mirar sin que nadie los confunda con una
// comparación de surtido total.
const SKU = OBSERVED_SKU_SQL

/** `(clave, condición)` de cada universo, para el CROSS JOIN LATERAL. */
const UNIVERSE_VALUES = UNIVERSE_KEYS
  .map((key) => `('${key}', (${UNIVERSES[key].sql}))`)
  .join(', ')

interface SkuRow {
  universe: string
  marca: string
  skus: number
  price_sum: number | null
  price_rows: number
}

interface BmlRow {
  beat: string
  meet: string
  lose: string
  nd: string
}

interface FranchiseRow {
  franchise: string
  count: string
  avg_price: string | null
  avg_gap_pct: string | null
}

type BrandTotals = Record<MarcaKey, number>
/** Acumulador del promedio de precio: suma y cantidad de filas con precio válido. */
type BrandPriceAcc = Record<MarcaKey, { sum: number; rows: number }>
type BrandPrices = Record<MarcaKey, number | null>

function emptyTotals(): BrandTotals {
  return { nike: 0, adidas: 0, puma: 0 }
}

function emptyPriceAcc(): BrandPriceAcc {
  return { nike: { sum: 0, rows: 0 }, adidas: { sum: 0, rows: 0 }, puma: { sum: 0, rows: 0 } }
}

/** Promedio ponderado por filas; `null` si no hubo ningún precio utilizable. */
function toPrices(acc: BrandPriceAcc): BrandPrices {
  const out: BrandPrices = { nike: null, adidas: null, puma: null }
  for (const key of Object.keys(out) as MarcaKey[]) {
    const { sum, rows } = acc[key]
    out[key] = rows > 0 ? Math.round(sum / rows) : null
  }
  return out
}

export async function GET(req: NextRequest) {
  const universe = parseUniverse(req.nextUrl.searchParams.get('universe'))

  try {
    const [skuRows, bml, bmlAdidas, bmlPuma, topAdidas, topPuma] = await Promise.all([
      // SKUs y precio promedio por marca EN CADA UNIVERSO. Sin filtro de marca
      // en el WHERE: se agrupa por marca normalizada y las tres se resuelven en
      // TypeScript (ver la regla en `lib/marca.ts`).
      query<SkuRow>(`
        WITH base AS (
          SELECT
            ${MARCA_NORM}  AS marca,
            ${SKU}         AS sku,
            ${FINAL}       AS price,
            scraper
          FROM pricing_data
        )
        SELECT
          u.key                                AS universe,
          base.marca                           AS marca,
          COUNT(DISTINCT base.sku)::int        AS skus,
          SUM(base.price)::float               AS price_sum,
          COUNT(base.price)::int               AS price_rows
        FROM base
        CROSS JOIN LATERAL (VALUES ${UNIVERSE_VALUES}) AS u(key, member)
        WHERE u.member AND base.sku IS NOT NULL
        GROUP BY u.key, base.marca
      `),

      // BEAT / MEET / LOSE del bloque "LOSE Nike" y "Nike Gana", en el mismo
      // universo elegido: un BML sólo tiene sentido donde hay góndola.
      query<BmlRow>(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data
        WHERE ${UNIVERSES[universe].sql}
      `),

      query<BmlRow>(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data WHERE ${MARCA_NORM} = 'ADIDAS'
      `),

      query<BmlRow>(`
        SELECT
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd
        FROM pricing_data WHERE ${MARCA_NORM} = 'PUMA'
      `),

      // Top 10 franchises Adidas / Puma. `COUNT(DISTINCT ${SKU})`: el conteo
      // por franquicia también contaba style_color (SKUs Nike) antes.
      query<FranchiseRow>(`
        SELECT franchise_competitor AS franchise,
          COUNT(DISTINCT ${SKU})                     AS count,
          ROUND(AVG(${FINAL})::numeric,0)            AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4) AS avg_gap_pct
        FROM pricing_data
        WHERE ${MARCA_NORM} = 'ADIDAS'
          AND franchise_competitor IS NOT NULL AND franchise_competitor <> ''
        GROUP BY franchise_competitor
        ORDER BY count DESC LIMIT 10
      `),

      query<FranchiseRow>(`
        SELECT franchise_competitor AS franchise,
          COUNT(DISTINCT ${SKU})                     AS count,
          ROUND(AVG(${FINAL})::numeric,0)            AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4) AS avg_gap_pct
        FROM pricing_data
        WHERE ${MARCA_NORM} = 'PUMA'
          AND franchise_competitor IS NOT NULL AND franchise_competitor <> ''
        GROUP BY franchise_competitor
        ORDER BY count DESC LIMIT 10
      `),
    ])

    const skusByUniverse: Record<UniverseKey, BrandTotals> = {
      gondola: emptyTotals(),
      d2c: emptyTotals(),
      all_ar: emptyTotals(),
    }
    const priceAccByUniverse: Record<UniverseKey, BrandPriceAcc> = {
      gondola: emptyPriceAcc(),
      d2c: emptyPriceAcc(),
      all_ar: emptyPriceAcc(),
    }

    for (const row of skuRows) {
      const canonical = canonicalMarca(row.marca)
      if (!canonical) continue
      if (!(UNIVERSE_KEYS as string[]).includes(row.universe)) continue
      const key = row.universe as UniverseKey
      const brand = marcaKey(canonical)
      skusByUniverse[key][brand] += Number(row.skus) || 0
      // Varias filas crudas ('Adidas', ' ADIDAS ', 'adidas originals') colapsan
      // en la misma marca canónica: el promedio se recompone ponderado por
      // filas, que es el promedio real, no un promedio de promedios.
      const sum = Number(row.price_sum)
      const rows = Number(row.price_rows)
      if (Number.isFinite(sum) && rows > 0) {
        priceAccByUniverse[key][brand].sum += sum
        priceAccByUniverse[key][brand].rows += rows
      }
    }

    const totals = skusByUniverse[universe]
    const prices = toPrices(priceAccByUniverse[universe])

    return NextResponse.json({
      universe,
      universeLabel: UNIVERSES[universe].label,
      universeDescription: UNIVERSES[universe].description,
      universes: UNIVERSE_KEYS.map((key) => ({
        key,
        label: UNIVERSES[key].label,
        description: UNIVERSES[key].description,
      })),
      kpis: {
        nike_total: totals.nike,
        adidas_total: totals.adidas,
        puma_total: totals.puma,
        nike_avg_price: prices.nike,
        adidas_avg_price: prices.adidas,
        puma_avg_price: prices.puma,
        total_beat: Number(bml[0]?.beat ?? 0),
        total_meet: Number(bml[0]?.meet ?? 0),
        total_lose: Number(bml[0]?.lose ?? 0),
        total_nd: Number(bml[0]?.nd ?? 0),
      },
      skusByUniverse,
      bml_adidas: bmlAdidas[0],
      bml_puma: bmlPuma[0],
      top_adidas: topAdidas,
      top_puma: topPuma,
    })
  } catch (err) {
    console.error('[/api/pricing/summary]', err)
    return NextResponse.json({ error: 'Error al obtener resumen' }, { status: 500 })
  }
}
