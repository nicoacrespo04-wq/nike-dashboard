import Link from 'next/link'
import { fetchMatch } from '@/lib/intelligence/server'
import { Card, EmptyState } from '@/components/ui'
import MatchExplain from '../../_components/MatchExplain'
import ServerError from '../../_components/ServerError'

/**
 * Match Explainability — Server Component.
 *
 * Una ficha de explicación es contenido, no una aplicación: no hay filtros ni
 * selección. Se resuelve entera en el servidor (`/matches/{id}`, cacheado 120s)
 * y llega renderizada.
 */
export const dynamic = 'force-dynamic'

export default async function MatchExplainabilityPage({
  params,
}: {
  params: { id: string }
}) {
  const matchId = Number(params.id)
  const result =
    Number.isFinite(matchId) && matchId > 0 ? await fetchMatch(matchId) : null

  return (
    <div>
      <Link
        href="/intelligence/matches"
        className="mb-3 inline-block text-2xs font-semibold text-nike-red hover:underline"
      >
        ← Volver a Competitive Matches
      </Link>

      {result === null || (!result.ok && result.status === 404) ? (
        <Card>
          <EmptyState
            title="Match no encontrado"
            description="El id solicitado no existe o el motor de matching todavía no persistió resultados."
          />
        </Card>
      ) : !result.ok ? (
        <Card>
          <ServerError description={result.error} />
        </Card>
      ) : (
        <MatchExplain match={result.data} />
      )}
    </div>
  )
}
