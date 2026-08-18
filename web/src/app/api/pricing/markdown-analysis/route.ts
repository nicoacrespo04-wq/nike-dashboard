import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { isValidPriceSql, validPriceSql } from '@/lib/price'
import { canonicalMarcaSql } from '@/lib/marca'
import { parseUniverse, retailerKeySql, retailerLabel, UNIVERSES } from '@/lib/scrapers'

export const dynamic = 'force-dynamic'

// Un retailer = una clave canónica, no una escritura del scraper. Antes
// `GROUP BY scraper` devolvía 40 filas para 10 retailers y el ranking de
// "% en promo" comparaba a Open Sports contra sí mismo tres veces. Medido con
// `curl /api/pricing/markdown-analysis`. Ver `lib/scrapers.ts::retailerKeySql`.
const RETAILER = retailerKeySql('scraper')

// Marca canónica en vez de `UPPER(marca)`: un ' Nike ' con espacio invisible
// abría una fila aparte y partía el conteo de la marca (ver `lib/marca.ts`).
const MARCA_CANON = canonicalMarcaSql('marca')

// Precios saneados (ver web/src/lib/price.ts): `<= 0` y outliers del bug de
// cuotas quedan NULL y no entran en promedios ni en los conteos de promo.
const FINAL = validPriceSql('competitor_final_price')
const FULL = validPriceSql('competitor_full_price')
const HAS_FINAL = isValidPriceSql('competitor_final_price')
const HAS_FULL = isValidPriceSql('competitor_full_price')

/**
 * `/api/pricing/markdown-analysis` — presión promocional por retailer y por marca.
 *
 * UNIVERSO: por defecto `gondola` (los retailers ARGENTINOS), como parámetro
 * explícito `?universe=gondola|d2c|all_ar`.
 *
 * POR QUÉ IMPORTA ACÁ: este endpoint devolvía filas de `Dexter_CL`,
 * `Dafiti_CL`, `MercadoLibre_CL` y `DigitalSport_CL` — 10 retailers chilenos en
 * total (medido con `curl /api/pricing/markdown-analysis`). El markdown se
 * calcula como `competitor_markdown / competitor_full_price`, así que el
 * porcentaje en sí no explota por la moneda; pero `avg_markdown_abs` es un
 * monto que se mostraba mezclando CLP con ARS, y sobre todo el ranking ponía a
 * un retailer chileno a competir por "quién descuenta más en Argentina".
 * Además `top_markdowns` sólo excluía `NIKE_D2C_FOREIGN` (los sitios Nike de
 * otros países) y no los retailers de otro país, que son un agujero distinto.
 */
export async function GET(req: NextRequest) {
  const universe = parseUniverse(req.nextUrl.searchParams.get('universe'))
  const UNIVERSE_SQL = UNIVERSES[universe].sql
  try {
    const [byRetailer, byMarca, topMarkdowns] = await Promise.all([
      // Markdown por retailer.
      // `promo_pct` se calcula sobre `with_price` (filas con precio usable),
      // no sobre el total crudo: si no hay precio válido no sabemos si está
      // en promo, y contarla como "sin promo" subestimaría el %.
      query<{ scraper: string; variants: string[] }>(`
        SELECT
          ${RETAILER}                 AS scraper,
          ARRAY_AGG(DISTINCT scraper) AS variants,
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
        WHERE ${UNIVERSE_SQL}
        GROUP BY ${RETAILER}, ${MARCA_CANON}
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
        WHERE ${UNIVERSE_SQL}
        GROUP BY ${MARCA_CANON}
        ORDER BY promo_pct DESC NULLS LAST
      `),

      // Top productos con mayor markdown. Mismo universo que los dos
      // agregados de arriba: antes acá se filtraba a mano con
      // `scraperNotInSql(NIKE_D2C_FOREIGN)`, que excluye los SITIOS Nike de
      // otros países pero no los RETAILERS de otros países — dos agujeros
      // distintos, y el segundo quedaba abierto.
      //
      // Es una lista fila-a-fila (no un agregado), así que la clave canónica
      // viaja al lado del nombre crudo y la etiqueta se resuelve en TypeScript
      // con el mismo mapa que usa `by_retailer`.
      query<{ scraper_key: string; scraper: string }>(`
        SELECT
          ${RETAILER} AS scraper_key, scraper,
          ${MARCA_CANON} AS marca, style_color, product_name_competitor,
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
          AND ${UNIVERSE_SQL}
        -- ORDER BY DETERMINISTICO. markdown_pct solo NO alcanza: el markdown
        -- viene de una grilla de descuentos redondos, así que hay 5.429 filas
        -- empatadas en 50,0% (medido:
        --   SELECT ROUND((competitor_markdown/competitor_full_price*100)::numeric,1) pct,
        --          COUNT(*) FROM pricing_data ... GROUP BY 1 ORDER BY 1 DESC
        --   -> 50.0 = 5429 filas)
        -- y este LIMIT 100 se quedaba con un 1,8% ARBITRARIO de ese empate:
        -- dos requests idénticas devolvían productos distintos, y bastó
        -- cambiar el plan de la consulta para que las 100 filas pasaran de 32
        -- retailers a uno solo. El desempate por (scraper, style_color, id)
        -- hace que la lista sea siempre la misma para los mismos datos.
        --
        -- Dentro del empate se ordena por el MONTO del descuento: a igual
        -- porcentaje, el producto que resigna mas pesos es el que el negocio
        -- quiere ver primero. Desempatar solo por nombre dejaba las 100 filas
        -- en el primer retailer alfabetico (medido: 100 de 100 'Dafiti').
        ORDER BY markdown_pct DESC, competitor_markdown DESC, scraper, style_color, id
        LIMIT 100
      `),
    ])

    // Etiqueta legible del retailer, decidida UNA sola vez por clave sobre
    // todas sus escrituras, y compartida por las dos tablas: así el mismo
    // retailer se llama igual arriba y abajo (ver `lib/scrapers.ts::retailerLabel`).
    const variantsByKey = new Map<string, Set<string>>()
    for (const r of byRetailer) {
      const set = variantsByKey.get(r.scraper) ?? new Set<string>()
      for (const v of r.variants ?? []) set.add(v)
      variantsByKey.set(r.scraper, set)
    }
    const labelByKey = new Map(
      Array.from(variantsByKey.entries()).map(([key, set]) => [key, retailerLabel(Array.from(set))]),
    )

    return NextResponse.json({
      // `variants` no viaja a la UI: es el insumo de la etiqueta, no un dato.
      by_retailer: byRetailer.map(({ variants: _variants, ...r }) => ({
        ...r,
        scraper: labelByKey.get(r.scraper) ?? r.scraper,
        scraper_key: r.scraper,
      })),
      by_marca: byMarca,
      top_markdowns: topMarkdowns.map(({ scraper_key, ...r }) => ({
        ...r,
        scraper: labelByKey.get(scraper_key) ?? retailerLabel([r.scraper]),
        scraper_key,
      })),
      universe,
      universeLabel: UNIVERSES[universe].label,
      universeDescription: UNIVERSES[universe].description,
    })
  } catch (err) {
    console.error('[/api/pricing/markdown-analysis]', err)
    return NextResponse.json({ error: 'Error al obtener markdown' }, { status: 500 })
  }
}
