import { Suspense } from 'react'
import { fetchBrandInsights, fetchBrandMomentum, fetchBrandTopics } from '@/lib/intelligence/server'
import { Card, EmptyState, PageIntro, SectionHeader } from '@/components/ui'
import BrandInsightsPanel from '../_components/BrandInsightsPanel'
import MomentumTable, { MomentumFooter } from '../_components/MomentumTable'
import ServerError from '../_components/ServerError'
import TopicsChart from '../_components/TopicsChart'
import WindowNote from '../_components/WindowNote'
import WindowSelector from '../_components/WindowSelector'
import {
  BRAND_COUNTRY,
  BRAND_MOMENTUM_LIMIT,
  BRAND_TOPICS_LIMIT,
  type BrandState,
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
 * La ventana de comparación (`?win=`) es la excepción: es un control de página,
 * no de panel, y navega — así los tres bloques se rearman contra el mismo
 * período en vez de quedar comparando cosas distintas entre sí.
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
  const state = brandStateFromParams(searchParams)

  return (
    <div>
      <PageIntro
        question="¿Qué está pasando? — Argentina"
        description="Percepción de marca, tópicos en tendencia, quejas, drivers positivos y momentum, siempre a partir de señal pública agregada. Regla dura del producto: un insight sin evidencia no se muestra."
      />

      <div className="mb-4 flex flex-wrap items-center justify-end gap-3">
        <WindowSelector value={state.window} />
      </div>

      <div className="space-y-5">
        <Suspense key={`insights-${state.window}`} fallback={<BrandInsightsSkeleton />}>
          <InsightsSection state={state} />
        </Suspense>

        <div className="grid gap-gutter xl:grid-cols-2">
          <Card>
            <SectionHeader
              eyebrow="Momentum"
              title="Marcas y franquicias que aceleran"
              subtitle="Separado por familia de señal: el momentum de conversación y la presencia en góndola no comparten unidad ni escala."
              hint="Cada bloque declara la unidad de su valor y de su variación. Un delta en ratio (0,62 = +62%) y uno en puntos porcentuales no se pueden leer con el mismo criterio."
              className="mb-3"
            />
            <Suspense key={`momentum-${state.window}`} fallback={<MomentumTableSkeleton />}>
              <MomentumSection state={state} />
            </Suspense>
          </Card>

          <Card>
            <SectionHeader
              eyebrow="Conversación"
              title="Tópicos en tendencia"
              subtitle="Volumen de menciones agregadas por tópico e intención, con sentimiento medio."
              className="mb-3"
            />
            <Suspense key={`topics-${state.window}`} fallback={<BarChartSkeleton />}>
              <TopicsSection state={state} />
            </Suspense>
          </Card>
        </div>
      </div>
    </div>
  )
}

async function InsightsSection({ state }: { state: BrandState }) {
  const insights = await fetchBrandInsights(brandInsightsQueryFrom(state))
  return (
    <BrandInsightsPanel
      initialState={state}
      initialData={insights.ok ? insights.data : null}
      initialError={insights.ok ? null : insights.error}
    />
  )
}

async function MomentumSection({ state }: { state: BrandState }) {
  const momentum = await fetchBrandMomentum({
    country: BRAND_COUNTRY,
    window: state.window,
    limit: BRAND_MOMENTUM_LIMIT,
  })
  if (!momentum.ok) {
    return <ServerError description={momentum.error} title="Sin momentum" size="sm" />
  }
  return (
    <div className="space-y-3">
      <WindowNote window={momentum.data.window} recomputed={momentum.data.recomputed} />
      <MomentumTable data={momentum.data} />
      <MomentumFooter items={momentum.data.items} />
    </div>
  )
}

async function TopicsSection({ state }: { state: BrandState }) {
  const topics = await fetchBrandTopics({
    country: BRAND_COUNTRY,
    window: state.window,
    limit: BRAND_TOPICS_LIMIT,
  })
  if (!topics.ok) {
    return <ServerError description={topics.error} title="Sin tópicos" size="sm" />
  }
  if (topics.data.items.length === 0) {
    return (
      <div className="space-y-3">
        <WindowNote window={topics.data.window} recomputed={topics.data.recomputed} />
        <EmptyState
          title="Sin tópicos de conversación"
          description="No hay menciones agregadas en el período elegido. Sin señal social no hay tópicos que reportar."
          size="sm"
        />
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <WindowNote window={topics.data.window} recomputed={topics.data.recomputed} />
      <TopicsChart items={topics.data.items} />
    </div>
  )
}
