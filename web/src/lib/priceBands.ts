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
 * ─────────────────────────────────────────────────────────────────────────
 * LOS CORTES NO SE INVENTAN ACÁ
 * ─────────────────────────────────────────────────────────────────────────
 * Son los de `backend/config/weights.yaml → enrichment.price_bands.AR`, que es
 * el único dueño del criterio (lo usa `services/enrichment.py` para persistir
 * `products.price_band` y `matching._price_band_similarity` para medir cercanía
 * entre bandas). Si el dashboard usara cortes propios, la misma franquicia
 * caería en una banda distinta según la pantalla.
 *
 *     "0-90.000":        [0,       90000]
 *     "90.000-160.000":  [90000,   160000]
 *     "160.000-260.000": [160000,  260000]
 *     "260.000+":        [260000,  99999999]   ← techo centinela = banda abierta
 *
 * El NOMBRE de la banda es el rango en plata, tal cual lo fija ese archivo:
 * el negocio razona en montos, no en etiquetas cualitativas (entry/mid/premium
 * quedaron como alias legacy en `price_band_tiers`, fuera de la UI).
 *
 * Intervalos `[min, max)`: mínimo inclusivo, máximo exclusivo. Un precio que no
 * pasa el saneamiento no cae en ninguna banda: queda fuera del desglose en vez
 * de ensuciar la banda de entrada.
 */

/** Techo centinela de `weights.yaml`: significa "banda abierta", no un monto. */
const OPEN_TOP_SENTINEL = 99_999_999

export interface PriceBand {
  /** Clave estable y label canónico (el mismo string que `products.price_band`). */
  key: string
  /** Piso inclusive, en ARS. */
  min: number
  /** Techo exclusivo, en ARS. `null` = banda abierta. */
  max: number | null
}

/**
 * Bandas de menor a mayor — copia literal de `enrichment.price_bands.AR`.
 * El orden es el orden en que se muestran y la distancia ordinal entre bandas.
 */
export const PRICE_BANDS: readonly PriceBand[] = [
  { key: '0-90.000', min: 0, max: 90_000 },
  { key: '90.000-160.000', min: 90_000, max: 160_000 },
  { key: '160.000-260.000', min: 160_000, max: 260_000 },
  { key: '260.000+', min: 260_000, max: null },
] as const

/**
 * Etiqueta para la UI: el rango en plata con el símbolo de moneda.
 * `'90.000-160.000'` → `'$90.000 - $160.000'` · `'260.000+'` → `'$260.000+'`.
 */
export function priceBandLabel(key: string): string {
  const band = PRICE_BANDS.find((b) => b.key === key)
  if (!band) return key
  const min = `$${band.min.toLocaleString('es-AR')}`
  if (band.max === null) return `${min}+`
  return `${min} - $${band.max.toLocaleString('es-AR')}`
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

/**
 * Sanidad de la copia: los cortes tienen que ser contiguos y crecientes, y la
 * última banda tiene que ser la abierta. Si alguien mueve un corte a mano y
 * deja un hueco, los SKUs de ese hueco desaparecerían del desglose sin ruido.
 */
function assertBandsAreContiguous(): void {
  PRICE_BANDS.forEach((band, i) => {
    const previous = PRICE_BANDS[i - 1]
    if (previous && previous.max !== band.min) {
      throw new Error(
        `priceBands: hueco entre '${previous.key}' y '${band.key}' (${previous.max} ≠ ${band.min})`,
      )
    }
    const isLast = i === PRICE_BANDS.length - 1
    if (!isLast && band.max === null) {
      throw new Error(`priceBands: sólo la última banda puede ser abierta ('${band.key}')`)
    }
    if (band.max !== null && band.max >= OPEN_TOP_SENTINEL) {
      throw new Error(
        `priceBands: '${band.key}' usa el centinela ${OPEN_TOP_SENTINEL} como techo real; debe ser null`,
      )
    }
  })
}

// Se corre al importar el módulo: si alguien mueve un corte y deja un hueco,
// falla el build en vez de hacer desaparecer en silencio los SKUs de ese hueco.
assertBandsAreContiguous()
