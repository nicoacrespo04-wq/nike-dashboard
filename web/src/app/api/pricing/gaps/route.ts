import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'
import { canonicalMarcaSql, marcaLabel } from '@/lib/marca'
import { OBSERVED_SKU_SQL, parseUniverse, UNIVERSES } from '@/lib/scrapers'
import { PRICE_BANDS, priceBandLabel, priceBandOrder, priceBandSql } from '@/lib/priceBands'
import { isPresentSql } from '@/lib/missing'

export const dynamic = 'force-dynamic'

/**
 * `/api/pricing/gaps` — ¿en qué segmentos comparables la competencia tiene
 * surtido y Nike casi no tiene?
 *
 * ─────────────────────────────────────────────────────────────────────────
 * POR QUÉ EXISTE ESTE ENDPOINT (bug confirmado, no supuesto)
 * ─────────────────────────────────────────────────────────────────────────
 * La pestaña "Product Gap Analysis" calculaba los gaps en el cliente así:
 *
 *     const nikeNames = new Set(nikeF.map((f) => f.franchise?.toLowerCase()))
 *     const gapsAdidas = adidasF.filter((f) => !nikeNames.has(f.franchise?.toLowerCase()))
 *
 * Es decir: declaraba "gap" a toda franquicia de la competencia cuyo NOMBRE no
 * existiera idéntico entre las franquicias Nike. La premisa está mal de raíz:
 * **el nombre de franquicia es propiedad de cada marca y nunca coincide entre
 * marcas**. Adidas tiene Ultraboost, Samba, Adizero; Nike tiene Pegasus, Air
 * Force, Vomero. No hay ningún universo en el que "Ultraboost" aparezca del
 * lado Nike, así que el filtro no filtraba nada.
 *
 * Medido contra el Postgres local con el fixture del repo (70.000 filas):
 * de 33 franquicias Adidas, 27 salían "gap"; de 27 franquicias Puma, 21. Y las
 * 6 que en cada marca "sí tenían equivalente Nike" eran exactamente los
 * marcadores de dato ausente `'-'` y `'s/d'` (ver `lib/missing.ts`), los únicos
 * strings que efectivamente existen en las dos marcas. O sea: el KPI de gaps
 * era el KPI de totales menos las filas basura. El usuario lo reportó como
 * "Assortment: gaps = totals" y tenía razón.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * QUÉ CALCULA EN VEZ DE ESO
 * ─────────────────────────────────────────────────────────────────────────
 * Un gap se mide sobre los ejes que SÍ son comunes entre marcas — los que
 * describen el producto, no cómo lo bautizó su dueña:
 *
 *     silueta × división × categoría × género × BANDA DE PRECIO
 *
 * La banda de precio va en MONEDA, no en etiquetas de gama, y son las mismas
 * de `backend/config/weights.yaml → enrichment.price_bands.AR` que ya usa la
 * pestaña "Siluetas" (ver `lib/priceBands.ts`): no puede haber dos
 * definiciones de banda en la misma pantalla.
 *
 * Cada SKU cae en UNA sola banda: primero se colapsa el SKU a un precio (el
 * promedio de sus observaciones válidas) y recién con ese precio se decide la
 * banda. Clasificando observación por observación, el mismo SKU visto en tres
 * retailers a tres precios aparece en tres bandas y los totales dejan de
 * sumar (es el mismo cuidado que toma `/api/pricing/siluetas`).
 */

// ─────────────────────────────────────────────────────────────────────────
// EL CRITERIO DE GAP (explícito y configurable, no un `if` con un número)
// ─────────────────────────────────────────────────────────────────────────
//
// "Gap" NO es "Nike tiene 0 SKUs". Con esa definición, cualquier segmento
// residual —un SKU mal clasificado de un retailer— se convierte en una
// oportunidad de negocio, y la pantalla vuelve a mentir. Un gap es:
//
//   (a) la competencia tiene PRESENCIA RELEVANTE en el segmento, y
//   (b) Nike está MUY POR DEBAJO de esa presencia.
//
// Los dos umbrales viven acá arriba, se pueden mover por env var sin tocar
// código (mismo patrón que `lib/price.ts`) o por query string, y viajan en la
// respuesta para que la UI pueda mostrar textual qué se consideró gap. Un
// umbral que el negocio no puede leer es un umbral que el negocio no puede
// discutir.

function envNumber(raw: string | undefined, fallback: number): number {
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : fallback
}

/**
 * (a) Mínimo de SKUs distintos del competidor para que el segmento cuente.
 *
 * Con 1 SKU no hay surtido: hay un producto. Puede ser una fila mal
 * clasificada, un producto de otra categoría que cayó ahí por un
 * `category_competitor` sucio, o un SKU visto una sola vez. Además el ratio
 * degenera: 1 contra 0 da "cobertura 0%" y ordena arriba de un segmento con
 * 40 SKUs de la competencia. Desde 2 SKUs distintos ya hay una decisión de
 * surtido deliberada.
 *
 * En un dataset más grande que el fixture este número quiere subir; por eso
 * es una constante con env var y no un literal enterrado en el `WHERE`.
 */
const GAP_MIN_COMPETITOR_SKUS = envNumber(process.env.GAP_MIN_COMPETITOR_SKUS, 2)

/**
 * (b) Cobertura máxima de Nike para que el segmento sea gap.
 *
 *     cobertura = SKUs Nike / SKUs del competidor
 *
 * 0,5 = Nike tiene menos de la mitad del surtido que la competencia puso ahí.
 * Por encima de eso Nike está presente y la conversación es de precio o de
 * mix, no de surtido faltante — y ese análisis ya lo dan las otras pestañas.
 */
const GAP_MAX_COVERAGE_RATIO = envNumber(process.env.GAP_MAX_COVERAGE_RATIO, 0.5)

/** Techo de filas de detalle que se devuelven por competidor. */
const MAX_GAP_ROWS = 200

// Precio saneado: un 0 o un precio inflado por cuotas no puede decidir una
// banda ni mover un promedio (ver `lib/price.ts`).
const FINAL = validPriceSql('competitor_final_price')

// Marca canónica resuelta en SQL con el mismo criterio que TypeScript. Nunca
// `UPPER(marca) = 'NIKE'`: un ' Nike ' con espacio invisible vacía el bloque
// entero sin decir por qué (ver `lib/marca.ts`).
const MARCA_CANON = canonicalMarcaSql('marca')

// SKU del producto OBSERVADO. `style_color` es el SKU del producto Nike de
// referencia de la comparación, no el del producto que se está contando
// (ver `lib/scrapers.ts`).
const SKU = OBSERVED_SKU_SQL

/**
 * Género canónico.
 *
 * POR QUÉ: `gender_competitor` llega con ocho grafías para cuatro géneros
 * (`'MENS'`, `'Mens'`, `'M'`, `'WOMENS'`, `'Womens'`, `'W'`, `'KIDS'`,
 * `'UNISEX'`), y en el fixture NO se reparten igual entre marcas: Adidas
 * aparece como `'W'`/`'WOMENS'`/`'Womens'` y Puma como `'M'`/`'MENS'`/`'Mens'`.
 * Sin canonicalizar, "Adidas mujer" y "Nike mujer" caen en segmentos
 * DISTINTOS y todo segmento de la competencia sale gap — exactamente el mismo
 * error de comparar strings que tenía el código viejo, un eje más abajo.
 *
 * El valor desconocido NO se descarta ni se fuerza a una categoría: se deja
 * pasar en mayúsculas y forma su propio segmento. Igual que `lib/marca.ts`,
 * un valor sucio nuevo degrada UN segmento en vez de hacer desaparecer filas.
 *
 * El orden importa: 'WOMENS' contiene 'MEN', así que mujer se evalúa antes.
 */
const GENDER_CANON = `
  CASE
    WHEN UPPER(BTRIM(gender_competitor)) LIKE '%KID%'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%JUNIOR%'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%YOUTH%'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%NI_O%'      THEN 'KIDS'  -- '_' es comodín: cubre NIÑO y NINO
    WHEN UPPER(BTRIM(gender_competitor)) IN ('W', 'F')
      OR UPPER(BTRIM(gender_competitor)) LIKE '%WOMEN%'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%MUJER%'     THEN 'WOMENS'
    WHEN UPPER(BTRIM(gender_competitor)) = 'M'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%MEN%'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%HOMBRE%'    THEN 'MENS'
    WHEN UPPER(BTRIM(gender_competitor)) = 'U'
      OR UPPER(BTRIM(gender_competitor)) LIKE '%UNISEX%'    THEN 'UNISEX'
    ELSE UPPER(BTRIM(gender_competitor))
  END`

/** Etiqueta en español del género canónico. Un valor nuevo se muestra tal cual. */
const GENDER_LABELS: Readonly<Record<string, string>> = {
  MENS: 'Hombre',
  WOMENS: 'Mujer',
  KIDS: 'Niños',
  UNISEX: 'Unisex',
}

function genderLabel(key: string): string {
  return GENDER_LABELS[key] ?? key
}

/** Los dos competidores que el dashboard compara contra Nike. */
const COMPETITORS = ['ADIDAS', 'PUMA'] as const
type Competitor = (typeof COMPETITORS)[number]
type CompetitorKey = Lowercase<Competitor>

/** Un segmento comparable, con el surtido de las tres marcas adentro. */
export interface GapSegmentRow {
  silueta: string
  division: string
  category: string
  /** Género canónico (`MENS`, `WOMENS`, `KIDS`, `UNISEX`). */
  gender: string
  /** Género para mostrar (`Hombre`, `Mujer`…). */
  gender_label: string
  /** Clave de banda de `lib/priceBands.ts` (`'90.000-160.000'`). */
  band: string
  /** Banda para mostrar (`'$90.000 - $160.000'`). */
  band_label: string
  nike_skus: number
  competitor_skus: number
  /** SKUs que le faltan a Nike para igualar el surtido del competidor. */
  gap_skus: number
  /** `nike_skus / competitor_skus`. Nunca `null`: el denominador es ≥ 1. */
  coverage: number
  nike_avg_price: number | null
  competitor_avg_price: number | null
}

/** El bloque de un competidor: sus gaps y el contexto para leerlos. */
export interface GapCompetitorBlock {
  marca: Competitor
  label: string
  /** Segmentos donde el competidor supera `minCompetitorSkus`: el denominador. */
  relevant_segments: number
  /** De esos, cuántos cumplen además el criterio de cobertura. */
  gap_segments: number
  /** Suma de `gap_skus` de los segmentos gap. El tamaño del agujero. */
  gap_skus: number
  /** Detalle ordenado por tamaño del gap. */
  gaps: GapSegmentRow[]
}

interface RawSegmentRow {
  silueta: string
  division: string
  category: string
  gender: string
  band: string | null
  nike_skus: string
  adidas_skus: string
  puma_skus: string
  nike_avg_price: string | null
  adidas_avg_price: string | null
  puma_avg_price: string | null
}

const int = (v: string | null): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

const numOrNull = (v: string | null): number | null => {
  if (v === null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const universe = parseUniverse(searchParams.get('universe'))

  // El criterio se puede mover desde la UI sin redeploy. Se clampea para que
  // un `?minSkus=0` no reviva el "gap = la competencia tiene algo".
  const minCompetitorSkus = Math.max(
    1,
    Math.round(envNumber(searchParams.get('minSkus') ?? undefined, GAP_MIN_COMPETITOR_SKUS)),
  )
  const maxCoverageRatio = Math.min(
    1,
    envNumber(searchParams.get('maxRatio') ?? undefined, GAP_MAX_COVERAGE_RATIO),
  )

  // Qué universo se mira. Los tres universos de `lib/scrapers.ts` ya son
  // argentinos (llevan `AR_ONLY_SQL` adentro), y eso importa acá: si se colaran
  // las filas `*_CL` del fixture —1.317 de 70.000, 393 de ellas Nike/Adidas/
  // Puma— el surtido de otro mercado y otra moneda entraría a decidir en qué
  // banda de precio cae un segmento. Mezclar universos ya fue un bug real; ver
  // el encabezado de `lib/scrapers.ts`.
  const universeSql = UNIVERSES[universe].sql

  // Los cuatro ejes son la CLAVE del segmento: si falta uno, la fila no se
  // puede ubicar y se la deja afuera en vez de inventarle un "Sin categoría"
  // que después compite por el primer puesto del ranking (ver `lib/missing.ts`).
  const where = [
    universeSql,
    `${MARCA_CANON} IS NOT NULL`,
    isPresentSql('silueta'),
    isPresentSql('division_competitor'),
    isPresentSql('category_competitor'),
    isPresentSql('gender_competitor'),
  ].join(' AND ')

  try {
    const rows = await query<RawSegmentRow>(`
      WITH sku_price AS (
        -- Un SKU por segmento y marca, con UN precio: el promedio de sus
        -- observaciones válidas. Sin este colapso el mismo SKU visto en tres
        -- retailers cae en tres bandas y las bandas dejan de particionar.
        SELECT
          UPPER(BTRIM(silueta))             AS silueta,
          UPPER(BTRIM(division_competitor)) AS division,
          UPPER(BTRIM(category_competitor)) AS category,
          ${GENDER_CANON}                   AS gender,
          ${MARCA_CANON}                    AS marca,
          ${SKU}                            AS sku,
          AVG(${FINAL})                     AS price
        FROM pricing_data
        WHERE ${where}
        GROUP BY 1, 2, 3, 4, 5, 6
      )
      SELECT
        silueta,
        division,
        category,
        gender,
        ${priceBandSql('price')}                                        AS band,
        COUNT(DISTINCT sku) FILTER (WHERE marca = 'NIKE')               AS nike_skus,
        COUNT(DISTINCT sku) FILTER (WHERE marca = 'ADIDAS')             AS adidas_skus,
        COUNT(DISTINCT sku) FILTER (WHERE marca = 'PUMA')               AS puma_skus,
        ROUND(AVG(price) FILTER (WHERE marca = 'NIKE')::numeric, 0)     AS nike_avg_price,
        ROUND(AVG(price) FILTER (WHERE marca = 'ADIDAS')::numeric, 0)   AS adidas_avg_price,
        ROUND(AVG(price) FILTER (WHERE marca = 'PUMA')::numeric, 0)     AS puma_avg_price
      FROM sku_price
      WHERE sku IS NOT NULL
      GROUP BY 1, 2, 3, 4, 5
    `)

    // `band = NULL` son los SKUs cuyo precio no es utilizable (0, o inflado por
    // cuotas). No se los reparte ni se los mete en la banda de entrada: quedan
    // fuera del análisis y se los CUENTA, para poder decirlo en pantalla.
    const placed = rows.filter((r) => r.band !== null)
    const unpriced = rows
      .filter((r) => r.band === null)
      .reduce(
        (acc, r) => ({
          nike: acc.nike + int(r.nike_skus),
          adidas: acc.adidas + int(r.adidas_skus),
          puma: acc.puma + int(r.puma_skus),
        }),
        { nike: 0, adidas: 0, puma: 0 },
      )

    const competitors: Record<CompetitorKey, GapCompetitorBlock> = {
      adidas: emptyBlock('ADIDAS'),
      puma: emptyBlock('PUMA'),
    }

    for (const raw of placed) {
      const band = raw.band as string
      const nikeSkus = int(raw.nike_skus)

      for (const marca of COMPETITORS) {
        const key = marcaKeyOf(marca)
        const competitorSkus = int(marca === 'ADIDAS' ? raw.adidas_skus : raw.puma_skus)
        if (competitorSkus < minCompetitorSkus) continue

        const block = competitors[key]
        block.relevant_segments += 1

        const coverage = nikeSkus / competitorSkus
        if (coverage >= maxCoverageRatio) continue

        const gapSkus = competitorSkus - nikeSkus
        block.gap_segments += 1
        block.gap_skus += gapSkus
        block.gaps.push({
          silueta: raw.silueta,
          division: raw.division,
          category: raw.category,
          gender: raw.gender,
          gender_label: genderLabel(raw.gender),
          band,
          band_label: priceBandLabel(band),
          nike_skus: nikeSkus,
          competitor_skus: competitorSkus,
          gap_skus: gapSkus,
          coverage: Math.round(coverage * 1000) / 1000,
          nike_avg_price: numOrNull(raw.nike_avg_price),
          competitor_avg_price: numOrNull(
            marca === 'ADIDAS' ? raw.adidas_avg_price : raw.puma_avg_price,
          ),
        })
      }
    }

    // Orden: primero el agujero más grande. A igual tamaño, el segmento donde
    // la competencia tiene más surtido; después, banda de precio de menor a
    // mayor, para que el detalle se lea como el resto de la pantalla.
    for (const key of Object.keys(competitors) as CompetitorKey[]) {
      const block = competitors[key]
      block.gaps.sort(
        (a, b) =>
          b.gap_skus - a.gap_skus ||
          b.competitor_skus - a.competitor_skus ||
          priceBandOrder(a.band) - priceBandOrder(b.band) ||
          a.silueta.localeCompare(b.silueta, 'es'),
      )
      block.gaps = block.gaps.slice(0, MAX_GAP_ROWS)
    }

    return NextResponse.json({
      universe,
      universeLabel: UNIVERSES[universe].label,
      universeDescription: UNIVERSES[universe].description,
      bands: PRICE_BANDS,
      /** Qué se consideró gap. La UI lo muestra textual; no hay umbral oculto. */
      criteria: {
        minCompetitorSkus,
        maxCoverageRatio,
        segmentAxes: ['Silueta', 'División', 'Categoría', 'Género', 'Banda de precio'],
        description:
          `Un segmento es gap cuando el competidor tiene ${minCompetitorSkus} SKUs distintos o más ` +
          `y Nike cubre menos del ${Math.round(maxCoverageRatio * 100)}% de ese surtido. ` +
          `El segmento es la combinación de silueta, división, categoría, género y banda de precio.`,
      },
      /** Cuántos segmentos comparables se pudieron construir en total. */
      segments: placed.length,
      /**
       * SKUs sin un precio utilizable: no caen en ninguna banda y por lo tanto
       * no participan de ningún segmento. Se informan para que el total de la
       * pantalla no parezca que se perdió dato en silencio.
       */
      skusSinBanda: unpriced,
      competitors,
    })
  } catch (err) {
    console.error('[/api/pricing/gaps]', err)
    return NextResponse.json({ error: 'Error al calcular los gaps de surtido' }, { status: 500 })
  }
}

function marcaKeyOf(marca: Competitor): CompetitorKey {
  return marca.toLowerCase() as CompetitorKey
}

function emptyBlock(marca: Competitor): GapCompetitorBlock {
  return {
    marca,
    label: marcaLabel(marca),
    relevant_segments: 0,
    gap_segments: 0,
    gap_skus: 0,
    gaps: [],
  }
}
