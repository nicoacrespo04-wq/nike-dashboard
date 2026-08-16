import Link from 'next/link'
import { Suspense } from 'react'
import { fetchOverview } from '@/lib/intelligence/server'
import { Card, EmptyState, PageIntro } from '@/components/ui'
import { CommandHint } from '@/components/intelligence/hints'
import OverviewContent from '../_components/OverviewContent'
import ServerError from '../_components/ServerError'
import { OverviewSkeleton } from '../_components/skeletons'

/**
 * Executive Overview — Server Component.
 *
 * Es el caso más claro de la sección: una foto sin filtros, sin selección y
 * sin nada que el usuario pueda tocar. Antes viajaba al browser como un
 * componente cliente que, en cada visita, pedía `/overview` completo y recién
 * después pintaba. Ahora el HTML sale del servidor con los datos adentro y la
 * respuesta del backend se comparte entre visitas durante 30 segundos
 * (`cacheRuleFor('/overview')`), que es lo que tolera una foto ejecutiva.
 *
 * `force-dynamic` porque la página vive detrás del login: se renderiza por
 * request, pero el `fetch` de adentro sí participa del Data Cache.
 */
export const dynamic = 'force-dynamic'

export default function IntelligenceOverviewPage() {
  return (
    <div>
      <PageIntro
        question="¿Qué está pasando?"
        description="La foto del mercado en 10 segundos: dónde está el riesgo, dónde está la oportunidad, quién nos está compitiendo y qué hacer al respecto."
        actions={
          <Link
            href="/intelligence/opportunities"
            className="inline-block rounded-lg bg-nike-red px-4 py-2 text-xs font-bold text-white transition-colors duration-fast hover:bg-nike-red-dark"
          >
            Ir al Opportunity Center →
          </Link>
        }
      />

      <Suspense fallback={<OverviewSkeleton />}>
        <OverviewSection />
      </Suspense>
    </div>
  )
}

async function OverviewSection() {
  const result = await fetchOverview({ country: 'AR', limit: 6 })
  return (
    <>
      {!result.ok ? (
        <Card>
          <ServerError description={result.error} />
        </Card>
      ) : result.data.kpis.products === 0 && result.data.kpis.opportunities === 0 ? (
        <Card>
          <EmptyState
            title="El motor todavía no tiene datos"
            description="El backend responde correctamente pero ninguna etapa del pipeline pobló sus tablas."
            action={<CommandHint />}
          />
        </Card>
      ) : (
        <OverviewContent data={result.data} />
      )}
    </>
  )
}

