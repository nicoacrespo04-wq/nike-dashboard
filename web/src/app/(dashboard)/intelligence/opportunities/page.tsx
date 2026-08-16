import { Suspense } from 'react'
import { fetchOpportunities } from '@/lib/intelligence/server'
import { PageIntro } from '@/components/ui'
import OpportunityCenter from '../_components/OpportunityCenter'
import {
  opportunityQueryFrom,
  opportunityStateFromParams,
} from '../_components/opportunityParams'
import { OpportunityCenterSkeleton } from '../_components/skeletons'

/**
 * Opportunity Center — mitad servidor.
 *
 * Resuelve la primera página (filtrada y paginada por el backend) y la pasa
 * como semilla al componente cliente, que es el que maneja los filtros.
 */
export const dynamic = 'force-dynamic'

export default function OpportunityCenterPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  return (
    <div>
      <PageIntro
        question="¿Cuánto importa? · ¿Por qué? · ¿Qué hacemos?"
        description="Cada tarjeta responde cuatro cosas: qué está pasando, cuánto importa comercialmente (Business Importance), por qué el motor lo cree (drivers) y qué acción se recomienda."
      />
      <Suspense fallback={<OpportunityCenterSkeleton />}>
        <OpportunitySection searchParams={searchParams} />
      </Suspense>
    </div>
  )
}

async function OpportunitySection({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  const state = opportunityStateFromParams(searchParams)
  const opportunities = await fetchOpportunities(opportunityQueryFrom(state))

  return (
    <OpportunityCenter
      initialState={state}
      initialData={opportunities.ok ? opportunities.data : null}
      initialError={opportunities.ok ? null : opportunities.error}
    />
  )
}
