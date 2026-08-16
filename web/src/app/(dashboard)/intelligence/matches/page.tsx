import { Suspense } from 'react'
import { fetchFocusBrand, fetchProductMatches, fetchProducts } from '@/lib/intelligence/server'
import { PageIntro } from '@/components/ui'
import MatchesExplorer from '../_components/MatchesExplorer'
import {
  MATCH_RANKING_LIMIT,
  matchProductsQueryFrom,
  matchesStateFromParams,
} from '../_components/matchParams'
import { MatchesSkeleton } from '../_components/skeletons'

/**
 * Competitive Matches — mitad servidor.
 *
 * Hace tres cosas antes de mandar nada al browser:
 *  1. Resuelve cuál es la marca foco (cacheado 10 min) para poder pedirle al
 *     backend SÓLO sus productos en vez de traer 300 y filtrarlos acá.
 *  2. Trae la primera página del selector.
 *  3. Elige el producto a analizar —el de la URL, o el primero de la lista— y
 *     resuelve su ranking competitivo.
 *
 * Así la pantalla llega completa en el primer render: sin la cascada
 * "pedir catálogo → elegir producto → pedir su ranking" que costaba dos
 * round-trips encadenados en cada visita.
 */
export const dynamic = 'force-dynamic'

export default function CompetitiveMatchesPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  return (
    <div>
      <PageIntro
        question="¿Quién compite?"
        description="Qué productos compiten realmente entre sí. El motor evalúa 7 factores por par y persiste sólo los que superan el score mínimo configurado. Cada ranking es explicable, no una lista opaca."
      />
      <Suspense fallback={<MatchesSkeleton />}>
        <MatchesSection searchParams={searchParams} />
      </Suspense>
    </div>
  )
}

async function MatchesSection({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  const state = matchesStateFromParams(searchParams)
  const focusBrand = await fetchFocusBrand()
  const products = await fetchProducts(matchProductsQueryFrom(state, focusBrand))

  const firstFocus = products.ok ? products.data.items.find((p) => p.is_focus === 1) : undefined
  const selectedId = state.selectedId ?? firstFocus?.id ?? null

  const matches =
    selectedId !== null
      ? await fetchProductMatches(selectedId, {
          limit: MATCH_RANKING_LIMIT,
          with_factors: true,
        })
      : null

  return (
    <MatchesExplorer
      initialState={{ ...state, selectedId }}
      focusBrand={focusBrand}
      initialProducts={products.ok ? products.data : null}
      initialMatches={matches !== null && matches.ok ? matches.data : null}
      initialError={products.ok ? null : products.error}
    />
  )
}
