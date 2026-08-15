import { Pool } from 'pg'

declare global {
  // eslint-disable-next-line no-var
  var _pgPool: Pool | undefined
}

function createPool(): Pool {
  const connectionString = process.env.DATABASE_URL
  if (!connectionString) throw new Error('DATABASE_URL no está configurada')

  return new Pool({
    connectionString,
    ssl: connectionString.includes('supabase.co')
      ? { rejectUnauthorized: false }
      : false,
    max: 10,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
  })
}

/**
 * Singleton perezoso para evitar múltiples pools en dev (hot-reload).
 *
 * Se crea en el primer `query()`, NO al importar el módulo: durante
 * `next build` Next.js importa cada route handler para recolectar metadata y,
 * si el pool se creaba en el import, el build entero fallaba con
 * "DATABASE_URL no está configurada" en cualquier entorno sin base.
 */
export function getPool(): Pool {
  if (!global._pgPool) global._pgPool = createPool()
  return global._pgPool
}

export async function query<T = Record<string, unknown>>(
  sql: string,
  params?: unknown[]
): Promise<T[]> {
  const client = await getPool().connect()
  try {
    const result = await client.query(sql, params)
    return result.rows as T[]
  } finally {
    client.release()
  }
}

export async function queryOne<T = Record<string, unknown>>(
  sql: string,
  params?: unknown[]
): Promise<T | null> {
  const rows = await query<T>(sql, params)
  return rows[0] ?? null
}

export default getPool

// ── Tipos TypeScript para la tabla principal ──────────────────

export interface PricingRow {
  id: number
  fecha_corrida: string
  scraper: string
  canal: string
  marca: string
  season: string
  style_color: string | null
  product_code_competitor: string | null
  marketing_name: string | null
  division: string | null
  category: string | null
  franchise_scrapper: string | null
  gender: string | null
  productcode_competitor: string | null
  product_name_competitor: string | null
  category_competitor: string | null
  division_competitor: string | null
  franchise_competitor: string | null
  gender_competitor: string | null
  size_available_competitor: number | null
  size_available_nike: number | null
  link_pdp_competitor: string | null
  competitor_full_price: number | null
  competitor_markdown: number | null
  competitor_final_price: number | null
  competitor_shipping: number | null
  competitor_price_shipping: number | null
  cuotas_competitor: string | null
  nike_full_price: number | null
  nike_markdown: number | null
  nike_final_price: number | null
  gap_final_price_pct: number | null
  gap_full_price_pct: number | null
  bml_final_price: string | null
  bml_full_price: string | null
  bml_with_shipping: string | null
  fx_ars_usd: number | null
  competitor_price_usd: number | null
  bml_vs_nikear: string | null
  text_sizes_competitor: string | null
  pdp_nike: string | null
  rango_precio: string | null
  precio_sugerido: number | null
  silueta: string | null
  created_at: string
}

export interface FranchiseSummary {
  franchise: string
  marca: string
  count_products: number
  avg_price: number
  avg_gap_pct: number
  beat_count: number
  meet_count: number
  lose_count: number
  avg_sizes: number
}

export interface RetailerSummary {
  scraper: string
  nike_count: number
  adidas_count: number
  puma_count: number
  total: number
  nike_share_pct: number
  adidas_share_pct: number
  puma_share_pct: number
}
