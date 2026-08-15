/**
 * Paleta compartida por los gráficos.
 *
 * Un único lugar donde vive el color de cada entidad, para que una marca o un
 * estado BML se vea igual en la dona, en las barras y en la tabla.
 *
 * Regla: el color sigue a la **entidad**, nunca al ranking. Si un filtro cambia
 * la cantidad de series, los colores de las que quedan no se repintan.
 */

export type BMLKey = 'BEAT' | 'MEET' | 'LOSE' | 'N/D'

/**
 * Semántica BML — NO invertir:
 *   BEAT = Nike más barato  → verde
 *   MEET = precio similar   → naranja
 *   LOSE = Nike más caro    → rojo
 */
export const BML_COLORS: Record<BMLKey, string> = {
  BEAT: '#27AE60',
  MEET: '#F5A623',
  LOSE: '#E31837',
  'N/D': '#9B9B9B',
}

export const BML_DESCRIPTION: Record<BMLKey, string> = {
  BEAT: 'Nike está más barato que el competidor',
  MEET: 'Precios equivalentes (diferencia mínima)',
  LOSE: 'Nike está más caro que el competidor',
  'N/D': 'Sin precio Nike equivalente para comparar',
}

/** Colores oficiales de marca. */
export const BRAND_COLORS: Record<string, string> = {
  NIKE: '#E31837',
  ADIDAS: '#0046CC',
  PUMA: '#E4032E',
}

/** Color de marca, con fallback al negro Nike. */
export function brandColor(marca: string | null | undefined): string {
  return BRAND_COLORS[marca?.toUpperCase() ?? ''] ?? '#111111'
}

/** Grises de ejes y grillas: recesivos, nunca compiten con los datos. */
export const CHART_INK = {
  axis: '#9B9B9B',
  axisStrong: '#444444',
  grid: '#F0F0F0',
  cursor: '#F5F5F5',
  surface: '#FFFFFF',
  selected: '#111111',
}
