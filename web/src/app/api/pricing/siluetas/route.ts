import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'
import { canonicalMarca, canonicalMarcaSql, marcaKey, type MarcaKey } from '@/lib/marca'
import { OBSERVED_SKU_SQL, parseUniverse, UNIVERSES } from '@/lib/scrapers'
import { PRICE_BANDS, priceBandOrder, priceBandSql } from '@/lib/priceBands'
import { isPresentSql, presentOrNullSql } from '@/lib/missing'

export const dynamic = 'force-dynamic'

// Precio saneado (ver web/src/lib/price.ts).
const FINAL = validPriceSql('competitor_final_price')
const MARCA_CANON = canonicalMarcaSql('marca')

// Banda del precio YA COLAPSADO POR SKU (ver el bloque grande de abajo).
const SKU_BAND = priceBandSql('price')

// Código propio del producto observado. NO `style_color`, que es el SKU del
// producto Nike de referencia de la comparación (ver `lib/scrapers.ts`).
const SKU = OBSERVED_SKU_SQL

/**
 * Marcadores de "no hay dato" que el scraper escribe como si fueran valores.
 * Sin esto el gráfico encabeza con '-' y 's/d', que no son siluetas, y el
 * desglose muestra una franquicia llamada 's/d'.
 *
 * La lista vivía acá como un literal propio y era la ÚNICA route que los
 * filtraba, así que el resto de `/api/pricing/*` seguía mostrándolos. Ahora
 * sale de `lib/missing.ts`, que es la definición compartida (y que agrega los
 * disfraces que a esta copia le faltaban: '#n/a', 'n/d', 'sin datos',
 * '#value!', '#ref!').
 */
const SILUETA_IS_REAL = isPresentSql('silueta')

/**
 * Una franquicia sin nombre (o con un marcador de ausencia) se agrupa como
 * "Sin franquicia": el surtido sigue contado, pero no se lo presenta como si
 * fuera una franquicia más.
 */
const FRANCHISE = `COALESCE(${presentOrNullSql('franchise_competitor')}, 'Sin franquicia')`

export interface SiluetaRow {
  silueta: string
  marca: string
  count: number
  avg_price: number | null
  beat: number
  lose: number
}

/** Una celda del desglose: los SKUs de una franquicia dentro de una banda. */
export interface SiluetaBandCell {
  band: string
  skus: number
  avg_price: number | null
}

/** Una franquicia de la silueta, con su reparto sobre el eje de precio. */
export interface SiluetaFranchiseRow {
  franchise: string
  marca: string
  skus: number
  avg_price: number | null
  /** SKUs sin precio utilizable: entran en `skus` pero en ninguna banda. */
  skus_sin_precio: number
  bands: SiluetaBandCell[]
}

interface RawSiluetaRow {
  silueta: string
  marca: string | null
  count: string
  avg_price: string | null
  beat: string
  lose: string
}

interface RawBreakdownRow {
  franchise: string | null
  marca: string | null
  band: string | null
  is_franchise_total: number
  is_band_total: number
  skus: string
  avg_price: string | null
}

/**
 * `/api/pricing/siluetas`
 *
 *   · sin `silueta`  → una fila por silueta × marca (alimenta el gráfico).
 *   · con `silueta`  → además, el DESGLOSE de esa silueta: por cada franquicia,
 *     cuántos SKUs y a qué precio promedio en CADA BANDA DE PRECIO.
 *
 * Las bandas son las de `backend/config/weights.yaml → enrichment.price_bands.AR`
 * (ver `lib/priceBands.ts`): montos, no etiquetas de gama.
 *
 * CADA SKU CAE EN UNA SOLA BANDA. El mismo SKU se observa en varios retailers y
 * varias fechas, a precios distintos: clasificándolo observación por
 * observación aparece en dos o tres bandas a la vez y las bandas suman más que
 * el total de la franquicia (medido sobre el fixture: una franquicia de 8 SKUs
 * sumaba 21 entre sus bandas). Por eso primero se colapsa a un precio por SKU
 * —el promedio de sus observaciones válidas— y recién con ese precio se decide
 * la banda. Así el desglose PARTICIONA el surtido y se lee como reparto.
 *
 * Marca: se agrupa por marca CANÓNICA resuelta en SQL con el mismo criterio que
 * TypeScript (`lib/marca.ts`). Nunca se compara `marca` contra un literal: es
 * lo que dejaba bloques enteros vacíos cuando el valor traía un espacio
 * invisible.
 */
export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const marcaParam = canonicalMarca(searchParams.get('marca'))
  const silueta = (searchParams.get('silueta') ?? '').trim()
  const universe = parseUniverse(searchParams.get('universe'))

  const conditions = [
    `silueta IS NOT NULL`,
    `BTRIM(silueta) <> ''`,
    SILUETA_IS_REAL,
    `${MARCA_CANON} IS NOT NULL`,
    UNIVERSES[universe].sql,
  ]
  const params: unknown[] = []
  if (marcaParam) {
    conditions.push(`${MARCA_CANON} = $${params.length + 1}`)
    params.push(marcaParam)
  }
  const where = conditions.join(' AND ')

  try {
    const rows = await query<RawSiluetaRow>(
      `
      SELECT
        BTRIM(silueta)                                   AS silueta,
        ${MARCA_CANON}                                   AS marca,
        COUNT(DISTINCT ${SKU})                           AS count,
        ROUND(AVG(${FINAL})::numeric, 0)                 AS avg_price,
        COUNT(*) FILTER (WHERE bml_final_price = 'BEAT') AS beat,
        COUNT(*) FILTER (WHERE bml_final_price = 'LOSE') AS lose
      FROM pricing_data
      WHERE ${where}
      GROUP BY BTRIM(silueta), ${MARCA_CANON}
      ORDER BY count DESC
    `,
      params,
    )

    const siluetas: SiluetaRow[] = rows
      .filter((r): r is RawSiluetaRow & { marca: string } => r.marca !== null)
      .map((r) => ({
        silueta: r.silueta,
        marca: r.marca,
        count: Number(r.count),
        avg_price: r.avg_price === null ? null : Number(r.avg_price),
        beat: Number(r.beat),
        lose: Number(r.lose),
      }))

    const base = {
      siluetas,
      universe,
      universeLabel: UNIVERSES[universe].label,
      universeDescription: UNIVERSES[universe].description,
      bands: PRICE_BANDS,
    }

    if (!silueta) return NextResponse.json(base)

    // ── Desglose de UNA silueta: franquicia × banda de precio ──────────────
    // `sku_price` colapsa cada SKU a un precio. Después, tres agregados en una
    // sola pasada:
    //   · franquicia × banda  → la celda de la tabla
    //   · franquicia          → el total de la fila (incluye los SKUs sin precio)
    //   · banda               → el total de la columna, sobre toda la silueta
    const breakdown = await query<RawBreakdownRow>(
      `
      WITH sku_price AS (
        SELECT
          ${FRANCHISE}   AS franchise,
          ${MARCA_CANON} AS marca,
          ${SKU}         AS sku,
          AVG(${FINAL})  AS price
        FROM pricing_data
        WHERE ${where} AND BTRIM(silueta) = $${params.length + 1}
        GROUP BY 1, 2, 3
      )
      SELECT
        franchise,
        marca,
        ${SKU_BAND}                       AS band,
        GROUPING(${SKU_BAND})::int        AS is_franchise_total,
        GROUPING(franchise, marca)::int   AS is_band_total,
        COUNT(*)                          AS skus,
        ROUND(AVG(price)::numeric, 0)     AS avg_price
      FROM sku_price
      WHERE sku IS NOT NULL
      GROUP BY GROUPING SETS (
        (franchise, marca, ${SKU_BAND}),
        (franchise, marca),
        (${SKU_BAND})
      )
    `,
      [...params, silueta],
    )

    const byFranchise = new Map<string, SiluetaFranchiseRow>()
    const bandsByFranchise = new Map<string, Map<string, SiluetaBandCell>>()
    const totalsByBand = new Map<string, SiluetaBandCell>()

    const ensureRow = (key: string, franchise: string, marca: string): SiluetaFranchiseRow => {
      const existing = byFranchise.get(key)
      if (existing) return existing
      const created: SiluetaFranchiseRow = {
        franchise,
        marca,
        skus: 0,
        avg_price: null,
        skus_sin_precio: 0,
        bands: [],
      }
      byFranchise.set(key, created)
      return created
    }

    for (const row of breakdown) {
      const skus = Number(row.skus) || 0
      const avgPrice = row.avg_price === null ? null : Number(row.avg_price)

      // Total de columna: agrupado sólo por banda (franquicia y marca en NULL,
      // por eso `GROUPING(franchise, marca)` vale 3 = 0b11).
      if (Number(row.is_band_total) === 3) {
        if (row.band !== null) {
          totalsByBand.set(row.band, { band: row.band, skus, avg_price: avgPrice })
        }
        continue
      }

      const canonical = canonicalMarca(row.marca)
      if (!canonical || row.franchise === null) continue
      const target = ensureRow(`${canonical}|${row.franchise}`, row.franchise, canonical)

      // Total de fila: `GROUPING()` lo distingue de la celda "sin banda", que
      // son los SKUs cuyo precio no es utilizable.
      if (Number(row.is_franchise_total) === 1) {
        target.skus = skus
        target.avg_price = avgPrice
        continue
      }

      if (row.band === null) {
        target.skus_sin_precio = skus
        continue
      }

      const key = `${canonical}|${row.franchise}`
      const bands = bandsByFranchise.get(key) ?? new Map<string, SiluetaBandCell>()
      bands.set(row.band, { band: row.band, skus, avg_price: avgPrice })
      bandsByFranchise.set(key, bands)
    }

    const franchises: SiluetaFranchiseRow[] = Array.from(byFranchise.entries())
      .map(([key, row]) => ({
        ...row,
        bands: Array.from(bandsByFranchise.get(key)?.values() ?? []).sort(
          (a, b) => priceBandOrder(a.band) - priceBandOrder(b.band),
        ),
      }))
      .sort((a, b) => b.skus - a.skus || a.franchise.localeCompare(b.franchise, 'es'))

    const totals: SiluetaBandCell[] = PRICE_BANDS.map(
      (band) => totalsByBand.get(band.key) ?? { band: band.key, skus: 0, avg_price: null },
    )

    const skusByMarca: Record<MarcaKey, number> = { nike: 0, adidas: 0, puma: 0 }
    for (const row of franchises) {
      const canonical = canonicalMarca(row.marca)
      if (canonical) skusByMarca[marcaKey(canonical)] += row.skus
    }

    return NextResponse.json({
      ...base,
      detail: { silueta, franchises, totals, skusByMarca },
    })
  } catch (err) {
    console.error('[/api/pricing/siluetas]', err)
    return NextResponse.json({ error: 'Error al obtener siluetas' }, { status: 500 })
  }
}
