import { Suspense } from 'react'
import { fetchBrandInsights, fetchBrandMomentum, fetchBrandTopics } from '@/lib/intelligence/server'
import { Card, EmptyState, PageIntro, SectionHeader } from '@/components/ui'
import BrandInsightsPanel from '../_components/BrandInsightsPanel'
import MomentumTable from '../_components/MomentumTable'
import ServerError from '../_components/ServerError'
import TopicsChart from '../_components/TopicsChart'
import {
  BRAND_COUNTRY,
  BRAND_MOMENTUM_LIMIT,
  BRAND_TOPICS_LIMIT,
  brandInsightsQueryFrom,
  brandStateFromParams,
} from '../_components/brandParams'
import {
  BarChartSkeleton,
  BrandInsightsSkeleton,
  MomentumTableSkeleton,
} from '../_components/skeletons'

/**
 * Consumer & Brand Intelligence — Argentina.
 *
 * Reparto: los insights quedan en el cliente porque tienen dos filtros, pero
 * momentum y tópicos se resuelven en el servidor — son paneles fijos, sin un
 * control que el usuario pueda tocar, así que antes costaban dos requests por
 * visita para pintar siempre lo mismo.
 *
 * Los tres bloques tienen su propio `Suspense`: el panel de insights no espera
 * a que el backend conteste los tópicos, ni al revés.
 */
export const dynamic = 'force-dynamic'

export default function ConsumerBrandPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  return (
    <div>
      <PageIntro
        question="¿Qué está pasando? — Argentina"
        description="Percepción de marca, tópicos en tendencia, quejas, drivers positivos y momentum, siempre a partir de señal pública agregada. Regla dura del producto: un insight sin evidencia no se muestra."
      />

      <div className="space-y-5">
        <Suspense fallback={<BrandInsightsSkeleton />}>
          <InsightsSection searchParams={searchParams} />
        </Suspense>

        <div className="grid gap-gutter xl:grid-cols-2">
          <Card>
            <SectionHeader
              eyebrow="Momentum"
              title="Marcas y franquicias que aceleran"
              subtitle="Volumen normalizado, variación vs. período anterior y aceleración."
              className="mb-3"
            />
            <Suspense fallback={<MomentumTableSkeleton />}>
              <MomentumSection />
            </Suspense>
          </Card>

          <Card>
            <SectionHeader
              eyebrow="Conversación"
              title="Tópicos en tendencia"
              subtitle="Volumen de menciones agregadas por tópico e intención, con sentimiento medio."
              className="mb-3"
            />
            <Suspense fallback={<BarChartSkeleton />}>
              <TopicsSection />
            </Suspense>
          </Card>
        </div>
      </div>
    </div>
  )
}

async function InsightsSection({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  const state = brandStateFromParams(searchParams)
  const insights = await fetchBrandInsights(brandInsightsQueryFrom(state))
  return (
    <BrandInsightsPanel
      initialState={state}
      initialData={insights.ok ? insights.data : null}
      initialError={insights.ok ? null : insights.error}
    />
  )
}

async function MomentumSection() {
  const momentum = await fetchBrandMomentum({
    country: BRAND_COUNTRY,
    limit: BRAND_MOMENTUM_LIMIT,
  })
  if (!momentum.ok) {
    return <ServerError description={momentum.error} title="Sin momentum" size="sm" />
  }
  return <MomentumTable items={momentum.data.items} />
}

async function TopicsSection() {
  const topics = await fetchBrandTopics({ country: BRAND_COUNTRY, limit: BRAND_TOPICS_LIMIT })
  if (!topics.ok) {
    return <ServerError description={topics.error} title="Sin tópicos" size="sm" />
  }
  if (topics.data.items.length === 0) {
    return (
      <EmptyState
        title="Sin tópicos de conversación"
        description="La tabla social_mention_aggregates está vacía. Sin señal social no hay tópicos que reportar."
        size="sm"
      />
    )
  }
  return <TopicsChart items={topics.data.items} />
}
