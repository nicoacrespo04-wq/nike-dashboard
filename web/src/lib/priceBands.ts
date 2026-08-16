/**
 * priceBands.ts — Bandas de precio del análisis de surtido.
 *
 * El eje de precio es la forma en que el negocio lee un catálogo: dónde pone
 * cada franquicia su volumen, si compite en entrada de gama o en premium, y
 * dónde hay huecos. `pricing_data` trae una columna `rango_precio`, pero viene
 * del scraper con valores libres y no está garantizada fila a fila, así que la
 * banda se calcula acá a partir del precio final ya saneado
 * (`lib/price.ts::validPriceSql`, que anula los 0 y los inflados por cuotas).
 *
 * Los cortes son una CONVENCIÓN DE NEGOCIO en ARS, no un dato: se definen una
 * sola vez acá y se pueden mover sin tocar SQL ni UI. Un precio que no pasa el
 * saneamiento no cae en ninguna banda: queda fuera del desglose en vez de
 * ensuciar la banda de entrada.
 */

export interface PriceBand {
  /** Clave estable (viaja en el JSON y ordena el desglose). */
  key: string
  /** Etiqueta para la UI. */
  label: string
  /** Piso inclusive, en ARS. */
  min: number
  /** Techo exclusivo, en ARS. `null` = sin techo. */
  max: number | null
}

/** Bandas de menor a mayor. El orden es el orden en que se muestran. */
export const PRICE_BANDS: readonly PriceBand[] = [
  { key: 'entrada',    label: 'Entrada · < $60.000',        min: 0,       max: 60_000 },
  { key: 'medio',      label: 'Medio · $60.000-$99.999',    min: 60_000,  max: 100_000 },
  { key: 'medio_alto', label: 'Medio alto · $100.000-$149.999', min: 100_000, max: 150_000 },
  { key: 'alto',       label: 'Alto · $150.000-$199.999',   min: 150_000, max: 200_000 },
  { key: 'premium',    label: 'Premium · $200.000+',        min: 200_000, max: null },
] as const

/** Etiqueta de una banda por su clave. */
export function priceBandLabel(key: string): string {
  return PRICE_BANDS.find((b) => b.key === key)?.label ?? key
}

/** Posición de la banda para ordenar (las desconocidas van al final). */
export function priceBandOrder(key: string): number {
  const index = PRICE_BANDS.findIndex((b) => b.key === key)
  return index === -1 ? PRICE_BANDS.length : index
}

/**
 * `CASE` que traduce una columna de precio a la clave de su banda.
 * Devuelve `NULL` cuando el precio no es utilizable, para que esas filas
 * queden explícitamente fuera del desglose.
 *
 * ⚠ `priceExpr` se interpola directo en el SQL: pasar SIEMPRE expresiones
 * construidas en el código (por ejemplo `validPriceSql('competitor_final_price')`),
 * nunca input del usuario.
 */
export function priceBandSql(priceExpr: string): string {
  const branches = PRICE_BANDS.map((band) => {
    const conditions = [`${priceExpr} >= ${band.min}`]
    if (band.max !== null) conditions.push(`${priceExpr} < ${band.max}`)
    return `WHEN ${conditions.join(' AND ')} THEN '${band.key}'`
  })
  return `CASE WHEN ${priceExpr} IS NULL THEN NULL ${branches.join(' ')} END`
}
