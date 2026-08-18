import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'
import {
  canonicalMarca,
  canonicalMarcaSql,
  marcaDiagnosticSql,
  marcaKey,
  marcaNormSql,
  type MarcaDiagnosticRow,
  type MarcaKey,
} from '@/lib/marca'
import { isPresentSql } from '@/lib/missing'
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

// Marca normalizada y marca canónica. `UPPER(marca)` no alcanza: la base trae
// ' Nike ' con espacios (incluso invisibles) y cualquier comparación contra el
// literal 'NIKE' se queda con cero filas en silencio. Ver `lib/marca.ts`.
const MARCA_NORM = marcaNormSql('marca')
const MARCA_CANON = canonicalMarcaSql('marca')

// ─────────────────────────────────────────────────────────────────────────
// EL KPI DE "SKUs ÚNICOS" — QUÉ SE CUENTA Y EN QUÉ UNIVERSO
// ─────────────────────────────────────────────────────────────────────────
// Antes: `COUNT(DISTINCT style_color) FILTER (marca = 'X')` sobre TODA la
// tabla. Daba Nike 27.358 vs Adidas 9.052 vs Puma 6.789, una comparación que
// el negocio no puede usar. Dos causas, las dos verificadas contra un Postgres
// local con el fixture del propio repo (detalle y números en `lib/scrapers.ts`):
//
//   · `style_color` es el SKU del producto NIKE de referencia de la fila, no
//     el del producto observado (medido: el mismo `style_color` aparece bajo
//     10 `marca` distintas a la vez). Se cuenta `OBSERVED_SKU_SQL`, que es el
//     código propio del producto observado.
//
//   · No había filtro de canal, así que Nike sumaba los retailers argentinos
//     + nike.com.ar + nike.com.co + nike.com + URU/USA, mientras Adidas y Puma
//     sólo tienen feeds argentinos (medido: 19.625 SKUs Nike de otros países
//     contra 0 de Adidas y 0 de Puma).
//
// DECISIÓN: el KPI se calcula SIEMPRE dentro de un universo explícito, el
// universo viaja en la respuesta y la UI lo muestra al lado del número. El
// default es `gondola` (los retailers AR), que es el único donde las tres
// marcas se observan realmente una al lado de la otra y el mismo universo que
// usan el BML y las franquicias de esa pantalla. Los otros universos se
// devuelven también —etiquetados— para poder mirarlos sin que nadie los
// confunda con una comparación de surtido total.
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
  marca: string | null
  is_total: number
  beat: string
  meet: string
  lose: string
  nd: string
}

interface FranchiseRow {
  marca: string | null
  franchise: string
  count: string
  avg_price: string | null
  avg_gap_pct: string | null
}

export interface BmlCounts {
  beat: number
  meet: number
  lose: number
  nd: number
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

function emptyBml(): BmlCounts {
  return { beat: 0, meet: 0, lose: 0, nd: 0 }
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

const BML_COLUMNS = `
  COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
  COUNT(*) FILTER (WHERE bml_final_price = 'MEET') AS meet,
  COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose,
  COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE')
                      OR bml_final_price IS NULL) AS nd`

export async function GET(req: NextRequest) {
  const universe = parseUniverse(req.nextUrl.searchParams.get('universe'))
  const universeSql = UNIVERSES[universe].sql

  try {
    const [skuRows, bmlRows, franchiseRows, marcasEnLaBase] = await Promise.all([
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

      // BEAT / MEET / LOSE en el MISMO universo que los SKUs: el total (que
      // alimenta "LOSE Nike" y "Nike Gana") y el desglose por marca, en una
      // sola pasada con GROUPING SETS. Antes el total respetaba el universo y
      // los donuts de Adidas/Puma no, así que los tres números de la misma
      // pantalla salían de poblaciones distintas.
      // `GROUPING()` distingue la fila del total (grouping set vacío) de la
      // fila de "marca que no es ninguna de las tres", que también sale con
      // marca NULL.
      query<BmlRow>(`
        SELECT
          ${MARCA_CANON}                  AS marca,
          GROUPING(${MARCA_CANON})::int   AS is_total,
          ${BML_COLUMNS}
        FROM pricing_data
        WHERE ${universeSql}
        GROUP BY GROUPING SETS ((${MARCA_CANON}), ())
      `),

      // Top franquicias por marca, mismo universo. `COUNT(DISTINCT ${SKU})`:
      // el conteo por franquicia también contaba style_color (SKUs Nike) antes.
      query<FranchiseRow>(`
        SELECT
          ${MARCA_CANON}                             AS marca,
          franchise_competitor                       AS franchise,
          COUNT(DISTINCT ${SKU})                     AS count,
          ROUND(AVG(${FINAL})::numeric,0)            AS avg_price,
          ROUND(AVG(gap_final_price_pct)::numeric,4) AS avg_gap_pct
        FROM pricing_data
        WHERE ${universeSql}
          AND ${MARCA_CANON} IS NOT NULL
          -- Mismo criterio que /api/pricing/franchises: el <> '' de antes
          -- dejaba pasar los "nulos disfrazados", y top_adidas / top_puma
          -- encabezaban con 's/d' (27 SKUs) y '-' (23). Medido con
          -- curl /api/pricing/summary. Ver lib/missing.ts.
          AND ${isPresentSql('franchise_competitor')}
        GROUP BY 1, 2
        ORDER BY count DESC
      `),

      query<MarcaDiagnosticRow>(marcaDiagnosticSql('pricing_data')),
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

    const bmlTotal = emptyBml()
    const bmlByMarca: Record<MarcaKey, BmlCounts> = {
      nike: emptyBml(),
      adidas: emptyBml(),
      puma: emptyBml(),
    }
    for (const row of bmlRows) {
      const counts: BmlCounts = {
        beat: Number(row.beat ?? 0),
        meet: Number(row.meet ?? 0),
        lose: Number(row.lose ?? 0),
        nd: Number(row.nd ?? 0),
      }
      if (Number(row.is_total) === 1) {
        Object.assign(bmlTotal, counts)
        continue
      }
      const canonical = canonicalMarca(row.marca)
      if (canonical) bmlByMarca[marcaKey(canonical)] = counts
    }

    const topByMarca: Record<MarcaKey, FranchiseRow[]> = { nike: [], adidas: [], puma: [] }
    for (const row of franchiseRows) {
      const canonical = canonicalMarca(row.marca)
      if (!canonical) continue
      const bucket = topByMarca[marcaKey(canonical)]
      if (bucket.length < 10) bucket.push(row)
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
        total_beat: bmlTotal.beat,
        total_meet: bmlTotal.meet,
        total_lose: bmlTotal.lose,
        total_nd: bmlTotal.nd,
      },
      skusByUniverse,
      bml_adidas: bmlByMarca.adidas,
      bml_puma: bmlByMarca.puma,
      bml_nike: bmlByMarca.nike,
      top_adidas: topByMarca.adidas,
      top_puma: topByMarca.puma,
      // Mismo criterio que /api/shelf/summary: los valores crudos de `marca`
      // viajan siempre, para no tener que adivinar si un número queda en cero.
      diagnostics: { marcasEnLaBase },
    })
  } catch (err) {
    console.error('[/api/pricing/summary]', err)
    return NextResponse.json({ error: 'Error al obtener resumen' }, { status: 500 })
  }
}
