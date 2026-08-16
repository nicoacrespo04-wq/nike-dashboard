import { NextResponse } from 'next/server'
import { query } from '@/lib/db'
import {
  MARCAS,
  canonicalMarca,
  marcaDiagnosticSql,
  marcaKey,
  marcaLabel,
  marcaNormSql,
  type Marca,
  type MarcaDiagnosticRow,
  type MarcaKey,
} from '@/lib/marca'

// Sin esto Next.js 14 trata este handler (GET sin Request) como estático y lo
// prerenderiza en build time, sirviendo para siempre el snapshot de datos que
// existía al compilar.
export const dynamic = 'force-dynamic'

// ─────────────────────────────────────────────────────────────────────────
// POR QUÉ ESTE ENDPOINT NO FILTRA POR MARCA
// ─────────────────────────────────────────────────────────────────────────
// Los KPIs globales quedaban en N/D mientras las barras de la MISMA respuesta
// funcionaban. Causa raíz reproducida en un Postgres local (la matriz completa
// está en `lib/marca.ts`): no era el binding de `= ANY($1)` —da exactamente lo
// mismo que un `IN` literal— ni el casing —con casing mezclado el filtro viejo
// anda—. Era que `WHERE <marca> = 'NIKE'` FALLA CERRADO ante cualquier
// suciedad de caracteres (espacio, NBSP, zero-width, `\r` de CRLF), mientras
// el `INITCAP` de las barras sólo AGRUPA y por eso las tolera.
//
// La regla que queda: **agrupar por marca normalizada y resolver la marca
// canónica en TypeScript.** Un valor sucio nuevo degrada una marca, nunca vacía
// el bloque entero. Y `canonicalMarca()` tiene un último recurso por token, así
// que un valor desconocido pero reconocible igual cae en su marca.
const MARCA_NORM = marcaNormSql('marca')

interface WinnerRow {
  canal: string
  marca: string
  wins: number
}

interface CanalTotalRow {
  canal: string
  total: number
}

interface GlobalRow {
  marca: string
  avg_visibility: number | null
  n: number
  terms: number
}

export interface ShelfCanalShare {
  marca: string
  wins: number
  pct: number
}

/** Visibilidad promedio de una marca y sobre cuánta evidencia se calculó. */
export interface ShelfBrandVisibility {
  value: number | null
  /** Filas (marca × término × retailer) que entraron al promedio. */
  rows: number
  /** Términos de búsqueda distintos en los que se observó la marca. */
  terms: number
}

export interface ShelfSummaryResponse {
  byCanal: Record<string, ShelfCanalShare[]>
  /** Compat: promedio por marca + `n` de términos de Nike. */
  global: Record<MarcaKey, number | null> & { n: number }
  /** Lo mismo, con la evidencia detrás de cada número. */
  visibility: Record<MarcaKey, ShelfBrandVisibility>
  totalByCanal: Record<string, number>
  /**
   * Siempre presente. `marcasEnLaBase` son los valores DISTINCT CRUDOS de
   * `marca` con su hexadecimal: si un KPI vuelve a quedar sin dato, acá se ve
   * por qué sin tener que abrir la base ni adivinar.
   */
  diagnostics: {
    marcasFaltantes: string[]
    marcasEnLaBase: MarcaDiagnosticRow[]
    message?: string
  }
}

// Share of Shelf por retailer: para cada término de búsqueda, "gana" la
// marca con mayor Nike_Visibility (posición relativa 0-1, 1 = mejor
// posición posible). Share = % de términos ganados por marca en ese canal.
export async function GET() {
  try {
    const [winners, totals, globalRows, marcasEnLaBase] = await Promise.all([
      // Las barras agrupan por marca NORMALIZADA (antes `INITCAP`, que dejaba
      // pasar ' Nike ' y hacía que el color de marca no matcheara: barra gris).
      query<WinnerRow>(`
        WITH ranked AS (
          SELECT
            canal, search_term, ${MARCA_NORM} AS marca, nike_visibility,
            ROW_NUMBER() OVER (
              PARTITION BY canal, search_term
              ORDER BY nike_visibility DESC NULLS LAST, ${MARCA_NORM}
            ) AS rn
          FROM retail_media_search
          WHERE nike_visibility IS NOT NULL
        )
        SELECT canal, marca, COUNT(*)::int AS wins
        FROM ranked
        WHERE rn = 1
        GROUP BY canal, marca
        ORDER BY canal, wins DESC
      `),

      query<CanalTotalRow>(`
        SELECT canal, COUNT(DISTINCT search_term)::int AS total
        FROM retail_media_search
        GROUP BY canal
      `),

      // Visibilidad promedio global por marca. SIN filtro de marca: se agrupa
      // todo lo que haya y las tres marcas se resuelven abajo.
      query<GlobalRow>(`
        SELECT
          ${MARCA_NORM}                        AS marca,
          AVG(nike_visibility)::float          AS avg_visibility,
          COUNT(*)::int                        AS n,
          COUNT(DISTINCT search_term)::int     AS terms
        FROM retail_media_search
        WHERE nike_visibility IS NOT NULL
        GROUP BY ${MARCA_NORM}
      `),

      query<MarcaDiagnosticRow>(marcaDiagnosticSql('retail_media_search')),
    ])

    const totalByCanal: Record<string, number> = {}
    for (const t of totals) totalByCanal[t.canal] = t.total

    // Las barras muestran la marca canónica ('Nike'), no el valor crudo.
    const byCanal: Record<string, ShelfCanalShare[]> = {}
    for (const w of winners) {
      const canonical = canonicalMarca(w.marca)
      const label = canonical ? marcaLabel(canonical) : w.marca.trim()
      const total = totalByCanal[w.canal] || 1
      const bucket = (byCanal[w.canal] ??= [])
      const existing = bucket.find((b) => b.marca === label)
      if (existing) {
        existing.wins += w.wins
        existing.pct = Math.round((existing.wins / total) * 1000) / 10
      } else {
        bucket.push({ marca: label, wins: w.wins, pct: Math.round((w.wins / total) * 1000) / 10 })
      }
    }
    for (const bucket of Object.values(byCanal)) bucket.sort((a, b) => b.pct - a.pct)

    // Varias filas crudas pueden colapsar en la misma marca canónica (alias,
    // sub-marcas, valores sucios distintos): el promedio se recompone ponderado
    // por cantidad de filas, que es el promedio correcto, no el promedio de
    // promedios.
    const accumulated = new Map<Marca, { sum: number; rows: number; terms: number }>()
    for (const row of globalRows) {
      const canonical = canonicalMarca(row.marca)
      if (!canonical) continue
      const avg = row.avg_visibility === null ? null : Number(row.avg_visibility)
      if (avg === null || !Number.isFinite(avg)) continue
      const acc = accumulated.get(canonical) ?? { sum: 0, rows: 0, terms: 0 }
      acc.sum += avg * row.n
      acc.rows += row.n
      acc.terms = Math.max(acc.terms, row.terms)
      accumulated.set(canonical, acc)
    }

    const global = { n: 0 } as Record<MarcaKey, number | null> & { n: number }
    const visibility = {} as Record<MarcaKey, ShelfBrandVisibility>
    const marcasFaltantes: string[] = []
    for (const marca of MARCAS) {
      const acc = accumulated.get(marca)
      const value = acc && acc.rows > 0 ? acc.sum / acc.rows : null
      global[marcaKey(marca)] = value
      visibility[marcaKey(marca)] = {
        value,
        rows: acc?.rows ?? 0,
        terms: acc?.terms ?? 0,
      }
      if (value === null) marcasFaltantes.push(marca)
    }
    global.n = visibility.nike.terms

    // El diagnóstico va SIEMPRE (son ≤25 filas). Si además falta una marca se
    // deja el mensaje explícito y se loguea: es exactamente la información que
    // hizo falta para encontrar este bug.
    const diagnostics: ShelfSummaryResponse['diagnostics'] = {
      marcasFaltantes,
      marcasEnLaBase,
    }
    if (marcasFaltantes.length > 0) {
      diagnostics.message =
        `No se encontró ninguna fila para: ${marcasFaltantes.join(', ')}. ` +
        'Abajo están los valores de `marca` que sí hay en la base ' +
        '(la columna hex muestra los caracteres invisibles).'
      console.warn(
        '[/api/shelf/summary] marcas sin datos:',
        marcasFaltantes.join(', '),
        '· valores DISTINCT de `marca` en la base:',
        JSON.stringify(marcasEnLaBase),
      )
    }

    const payload: ShelfSummaryResponse = { byCanal, global, visibility, totalByCanal, diagnostics }
    return NextResponse.json(payload)
  } catch (err) {
    console.error('[/api/shelf/summary]', err)
    return NextResponse.json({ error: 'Error al obtener share of shelf' }, { status: 500 })
  }
}
