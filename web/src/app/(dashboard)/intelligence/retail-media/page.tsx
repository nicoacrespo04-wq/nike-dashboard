import { Suspense } from 'react'
import { fetchRetailMedia } from '@/lib/intelligence/server'
import { PageIntro } from '@/components/ui'
import RetailMediaBoard from '../_components/RetailMediaBoard'
import {
  retailMediaQueryFrom,
  retailMediaStateFromParams,
} from '../_components/retailMediaParams'
import { RetailMediaSkeleton } from '../_components/skeletons'

/** Retail Media — mitad servidor: resuelve la primera página ya filtrada. */
export const dynamic = 'force-dynamic'

export default function RetailMediaPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  return (
    <div>
      <PageIntro
        question="¿Qué hacemos?"
        description="El caso central del producto: en vez de financiar otro markdown, reasignar esa inversión a visibilidad. Cada caso combina salud de stock, competitividad de precio, relevancia competitiva y momentum del competidor para decidir dónde pauta el peso."
      />
      <Suspense fallback={<RetailMediaSkeleton />}>
        <RetailMediaSection searchParams={searchParams} />
      </Suspense>
    </div>
  )
}

async function RetailMediaSection({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  const state = retailMediaStateFromParams(searchParams)
  const media = await fetchRetailMedia(retailMediaQueryFrom(state))

  return (
    <RetailMediaBoard
      initialState={state}
      initialData={media.ok ? media.data : null}
      initialError={media.ok ? null : media.error}
    />
  )
}
