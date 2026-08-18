import { NextRequest, NextResponse } from 'next/server'
import { query } from '@/lib/db'
import { validPriceSql } from '@/lib/price'
import { canonicalMarca, canonicalMarcaSql } from '@/lib/marca'
import { AR_ONLY_SQL, OBSERVED_SKU_SQL, scraperInSql, scraperNotInSql, BRAND_SITE_SCRAPERS, COMPETITOR_D2C_AR } from '@/lib/scrapers'
import { isPresentSql } from '@/lib/missing'
import { labelKey, labelKeySql, pickLabel } from '@/lib/labels'

export const dynamic = 'force-dynamic'

// Precios saneados (ver web/src/lib/price.ts): un precio en 0 o inflado por
// el bug de cuotas no debe mover el precio promedio de la franchise.
const FINAL = validPriceSql('competitor_final_price')
const FULL = validPriceSql('competitor_full_price')

// Marca canónica resuelta en SQL con el mismo criterio que TypeScript. Un
// `UPPER(marca) = 'NIKE'` se queda con CERO filas ante un ' Nike ' con espacio
// invisible y el bloque entero sale vacío sin decir por qué (ver `lib/marca.ts`).
const MARCA_CANON = canonicalMarcaSql('marca')

// SKU del producto OBSERVADO. `style_color` es el SKU del producto Nike de
// referencia de la comparación, no el de la franquicia que se está contando
// (ver `lib/scrapers.ts`).
const SKU = OBSERVED_SKU_SQL

/** Una fila del agregado por franquicia, tal cual sale de Postgres. */
export interface FranchiseQueryRow {
  franchise: string | null
  marca: string | null
  division: string | null
  count: string
  rows_count: string
  avg_price: string | null
  avg_full_price: string | null
  avg_gap_pct: string | null
  beat: string
  meet: string
  lose: string
  nd: string
  avg_sizes: string | null
  in_promo: string
  promo_pct: string | null
  avg_markdown: string | null
}

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl
  const marca    = searchParams.get('marca')    ?? ''
  const division = searchParams.get('division') ?? ''
  const category = searchParams.get('category') ?? ''
  const gender   = searchParams.get('gender')   ?? ''
  const canal    = searchParams.get('canal')    ?? '' // 'd2c' | 'b2b' | ''

  // El `IS NOT NULL AND <> ''` de antes dejaba pasar los "nulos disfrazados"
  // del scraper, y no era un detalle: medido con
  // `curl /api/pricing/franchises`, las DOS franquicias más grandes que
  // devolvía este endpoint eran `'-'` (21 SKUs de Puma) y `'s/d'` (17 de
  // Adidas), o sea que el gráfico "Top Franchises" encabezaba con dos cosas
  // que no son franquicias. Un solo predicado compartido, ver `lib/missing.ts`.
  const baseConditions: string[] = [
    isPresentSql('franchise_competitor'),
    // Universo: esta pantalla compara marcas EN ARGENTINA. El filtro de
    // `canal` decide el canal (D2C vs retailer) pero no el país, así que sin
    // esto los 10 retailers chilenos (`Dexter_CL`, `OpenSports_CL`…) sumaban
    // sus SKUs y sus precios en CLP a las franquicias argentinas. Es el mismo
    // error de mezclar universos que documenta `lib/scrapers.ts`; acá va
    // explícito y no como efecto colateral de otro filtro.
    AR_ONLY_SQL,
  ]
  const params: unknown[] = []
  let idx = 1

  // El parámetro de marca se canonicaliza ANTES de entrar a la query: si la UI
  // manda 'adidas', 'Adidas' o 'ADIDAS ORIGINALS' se resuelve al mismo 'ADIDAS'.
  const marcaCanonica = canonicalMarca(marca)
  if (marcaCanonica) { baseConditions.push(`${MARCA_CANON} = $${idx++}`); params.push(marcaCanonica) }
  else               { baseConditions.push(`${MARCA_CANON} IN ('ADIDAS','PUMA')`) }

  if (division) { baseConditions.push(`UPPER(division_competitor) LIKE $${idx++}`); params.push(`%${division.toUpperCase()}%`) }
  // `gender_competitor` tiene el mismo problema de casing que `category`:
  // conviven 'MENS'/'Mens' y 'WOMENS'/'Womens', así que un `= 'MENS'` literal
  // se comía la mitad de las filas. Se compara por clave normalizada.
  if (gender)   { baseConditions.push(`${labelKeySql('gender_competitor')} = $${idx++}`); params.push(labelKey(gender)) }

  // Los nombres de scraper llegan con casing y separadores inconsistentes
  // ('ADIDAS_7' / 'adidas_7'): las comparaciones van por clave canónica.
  if (canal === 'd2c') {
    baseConditions.push(scraperInSql(COMPETITOR_D2C_AR))
  } else if (canal === 'b2b') {
    baseConditions.push(scraperNotInSql(BRAND_SITE_SCRAPERS))
  }

  // El filtro de categoría NO se aplica al listado de categorías disponibles,
  // si no el <select> se colapsaría a la opción ya elegida.
  const categoriesWhere = baseConditions.join(' AND ')
  const categoriesParams = [...params]

  const conditions = [...baseConditions]
  // La categoría llega desde el `<select>` que arma este mismo endpoint, así
  // que el valor elegido es la ETIQUETA de un grupo, no un valor crudo de la
  // columna. Se compara por clave normalizada (`labelKeySql`) para que elegir
  // "Running" traiga también las filas escritas 'RUNNING' — que en el fixture
  // son 35.113 contra 6.508, o sea que el filtro viejo escondía el 84% de la
  // categoría según qué variante hubiera quedado en el `<select>`.
  if (category) { conditions.push(`${labelKeySql('category_competitor')} = $${idx++}`); params.push(labelKey(category)) }
  const where = conditions.join(' AND ')

  try {
    const [rows, categories] = await Promise.all([
      // COUNT(DISTINCT <código del observado>): un mismo SKU aparece en varios
      // retailers y en varias fechas. Con COUNT(*) los conteos salían inflados
      // (ver commit abbce1a); con `style_color` se contaban SKUs de Nike.
      query<FranchiseQueryRow>(`
        SELECT
          franchise_competitor                                        AS franchise,
          ${MARCA_CANON}                                              AS marca,
          division_competitor                                         AS division,
          COUNT(DISTINCT ${SKU})                                      AS count,
          COUNT(*)                                                    AS rows_count,
          ROUND(AVG(${FINAL})::numeric, 0)                            AS avg_price,
          ROUND(AVG(${FULL})::numeric, 0)                             AS avg_full_price,
          ROUND(AVG(gap_final_price_pct)::numeric, 4)                 AS avg_gap_pct,
          COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')            AS beat,
          COUNT(*) FILTER (WHERE bml_final_price = 'MEET')            AS meet,
          COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')            AS lose,
          COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE') OR bml_final_price IS NULL) AS nd,
          ROUND(AVG(size_available_competitor)::numeric, 1)           AS avg_sizes,
          COUNT(DISTINCT ${SKU}) FILTER (WHERE competitor_markdown > 0) AS in_promo,
          ROUND((
            COUNT(DISTINCT ${SKU}) FILTER (WHERE competitor_markdown > 0) * 100.0
            / NULLIF(COUNT(DISTINCT ${SKU}), 0)
          )::numeric, 1)                                              AS promo_pct,
          ROUND(AVG(competitor_markdown) FILTER (WHERE competitor_markdown > 0)::numeric, 0) AS avg_markdown
        FROM pricing_data
        WHERE ${where}
        GROUP BY franchise_competitor, ${MARCA_CANON}, division_competitor
        ORDER BY count DESC
        LIMIT 100
      `, params),

      // Una fila por categoría CANÓNICA, con todas las escrituras crudas que
      // el scraper usó para ella. La etiqueta se elige en TypeScript con
      // `pickLabel()` (criterio determinístico y estable, ver `lib/labels.ts`):
      // antes este `SELECT DISTINCT` devolvía
      // `['-','BASKETBALL','FOOTBALL/SOCCER','RUNNING','Running','SPORTSWEAR','TRAINING','s/d']`
      // — 8 opciones para 5 categorías reales, con 's/d' ofrecido al usuario.
      query<{ key: string; variants: string[] }>(`
        SELECT
          ${labelKeySql('category_competitor')}      AS key,
          ARRAY_AGG(DISTINCT category_competitor)    AS variants
        FROM pricing_data
        WHERE ${categoriesWhere}
          AND ${isPresentSql('category_competitor')}
        GROUP BY 1
        ORDER BY 1
      `, categoriesParams),
    ])

    return NextResponse.json({
      franchises: rows,
      categories: categories.map((c) => pickLabel(c.variants)),
    })
  } catch (err) {
    console.error('[/api/pricing/franchises]', err)
    return NextResponse.json({ error: 'Error al obtener franchises' }, { status: 500 })
  }
}
