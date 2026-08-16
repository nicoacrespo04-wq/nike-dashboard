'use client'

import { useEffect, useMemo, useState } from 'react'
import type { BrandInsight, BrandInsightsResponse } from '@/types/intelligence'
import { getBrandInsights } from '@/lib/intelligence/api'
import { depsKeyOf } from '@/lib/intelligence/depsKey'
import { useApi } from '@/lib/intelligence/useApi'
import { evidenceOf, hasEvidence } from '@/lib/intelligence/insights'
import { dec, num, pct, period, text } from '@/lib/format'
import { dimensionLabel, humanize } from '@/components/charts/palette'
import { Card, EmptyState, ErrorState, KPICard, SectionHeader } from '@/components/ui'
import { ConfidenceBadge, Tag } from '@/components/intelligence/badges'
import { EvidenceList } from '@/components/intelligence/EvidenceList'
import { CommandHint } from '@/components/intelligence/hints'
import WindowNote from './WindowNote'
import {
  BRAND_INSIGHTS_BATCH,
  type BrandState,
  brandInsightsQueryFrom,
  brandInsightsQueryKey,
} from './brandParams'
import { InsightGridSkeleton, KpiGridSkeleton } from './skeletons'
import { syncUrl } from './urlState'

export interface BrandInsightsPanelProps {
  initialState: BrandState
  initialData: BrandInsightsResponse | null
  initialError: string | null
}

/**
 * Insights de consumidor — mitad cliente.
 *
 * Los dos selects (dimensión y confianza) son filtros del backend, no del
 * array: se mandan como `dimension` y `min_confidence`. Antes se pedían 200
 * insights de una y se filtraba en pantalla.
 */
export default function BrandInsightsPanel({
  initialState,
  initialData,
  initialError,
}: BrandInsightsPanelProps) {
  const [state, setState] = useState<BrandState>(initialState)

  const query = useMemo(() => brandInsightsQueryFrom(state), [state])
  const queryKey = brandInsightsQueryKey(query)

  const insightsState = useApi((signal) => getBrandInsights(query, signal), [queryKey], {
    initialData,
    initialKey: depsKeyOf([brandInsightsQueryKey(brandInsightsQueryFrom(initialState))]),
  })

  useEffect(() => {
    syncUrl({
      dim: state.dimension,
      conf: state.min_confidence,
      batches: state.batches > 1 ? state.batches : null,
    })
  }, [state])

  const error = insightsState.error ?? (insightsState.data === null ? initialError : null)

  if (error) {
    return (
      <Card>
        <ErrorState
          title="No pudimos cargar el motor de inteligencia"
          description={error}
          onRetry={insightsState.reload}
        />
      </Card>
    )
  }

  const data = insightsState.data
  if (data === null) {
    return (
      <>
        <KpiGridSkeleton count={4} columns={4} />
        <Card className="mt-5">
          <InsightGridSkeleton />
        </Card>
      </>
    )
  }

  // Una ventana sin historia suficiente NO devuelve insights: un insight es una
  // conclusión y una conclusión contra una ventana vacía sería inventada. Eso no
  // es "no hay datos", así que se explica antes de mostrar cualquier vacío.
  if (data.window && data.window.available === false) {
    return (
      <Card>
        <div className="space-y-3">
          <WindowNote window={data.window} recomputed={data.recomputed} />
          <EmptyState
            title={`No hay insights comparables contra el ${data.window.label}`}
            description="El motor no publica conclusiones sobre una ventana que el histórico no cubre. Elegí una ventana más corta o cargá más historia con el pipeline."
            action={<CommandHint />}
          />
        </div>
      </Card>
    )
  }

  if (data.items.length === 0 && state.dimension === '' && state.min_confidence === '') {
    return (
      <Card>
        <EmptyState
          title="Sin insights de consumidor"
          description="La tabla brand_insights está vacía. Esta etapa consume reviews, menciones editoriales y agregados sociales de Argentina."
          action={<CommandHint />}
        />
      </Card>
    )
  }

  const withEvidence = data.items.filter(hasEvidence)
  const suppressed = data.items.length - withEvidence.length
  const dimensions = Object.keys(data.taxonomy ?? {})
  // El backend corta en `limit`: si devolvió exactamente lo pedido, es probable
  // que haya más. Se dice así, sin inventar un total que el endpoint no publica.
  const maybeMore = data.items.length >= query.limit

  return (
    <div
      className={insightsState.refreshing ? 'opacity-60 transition-opacity duration-fast' : undefined}
      aria-busy={insightsState.refreshing}
    >
      <div className="grid grid-cols-2 gap-gutter lg:grid-cols-4">
        <KPICard
          title="Insights con evidencia"
          value={withEvidence.length}
          subtitle={
            suppressed > 0
              ? `${suppressed} ocultado(s) por falta de respaldo`
              : 'todos respaldados'
          }
          color="#E31837"
          valueSize="md"
          hint="Un insight sin evidencia adjunta no se muestra: sin respaldo cuantitativo y ejemplos, no hay insight."
        />
        <KPICard
          title="Volumen de señal"
          value={num(withEvidence.reduce((acc, i) => acc + (i.signal_volume ?? 0), 0))}
          subtitle={`en los ${num(data.items.length)} insights cargados`}
          valueSize="md"
        />
        <KPICard
          title="Dimensiones cubiertas"
          value={new Set(withEvidence.map((i) => i.dimension)).size}
          subtitle={`de ${dimensions.length} en la taxonomía`}
          valueSize="md"
        />
        <KPICard
          title="Marcas observadas"
          value={new Set(withEvidence.map((i) => i.brand).filter(Boolean)).size}
          subtitle="con conversación medible"
          valueSize="md"
        />
      </div>

      <Card className="mt-5">
        <SectionHeader
          eyebrow="Percepción"
          title="Insights de marca y consumidor"
          subtitle="Cada insight declara volumen de señal, tendencia, dirección, confianza y ejemplos de evidencia."
          actions={
            <div className="flex gap-2">
              <select
                value={state.dimension}
                onChange={(e) =>
                  setState((prev) => ({ ...prev, dimension: e.target.value, batches: 1 }))
                }
                className="field-control text-xs"
                aria-label="Filtrar por dimensión"
              >
                <option value="">Todas las dimensiones</option>
                {dimensions.map((d) => (
                  <option key={d} value={d}>
                    {dimensionLabel(d)}
                  </option>
                ))}
              </select>
              <select
                value={state.min_confidence}
                onChange={(e) =>
                  setState((prev) => ({ ...prev, min_confidence: e.target.value, batches: 1 }))
                }
                className="field-control text-xs"
                aria-label="Filtrar por confianza"
              >
                <option value="">Toda confianza</option>
                <option value="MEDIUM">Media o alta</option>
                <option value="HIGH">Sólo alta</option>
              </select>
            </div>
          }
          className="mb-4"
        />

        <div className="mb-3">
          <WindowNote window={data.window} recomputed={data.recomputed} />
        </div>

        {withEvidence.length === 0 ? (
          <EmptyState
            title={
              data.items.length === 0 ? 'Sin insights para este filtro' : 'Insights sin evidencia adjunta'
            }
            description={
              data.items.length === 0
                ? 'No hay insights que cumplan el filtro seleccionado.'
                : `Hay ${data.items.length} insight(s) sin evidencia. Por regla del producto no se muestran: sin respaldo cuantitativo y ejemplos, no hay insight.`
            }
          />
        ) : (
          <>
            <InsightGroups insights={withEvidence} />
            <div className="mt-5 flex items-center justify-center gap-3 border-t border-surface-border pt-4">
              <span className="tabular text-2xs text-nike-muted">
                Mostrando los {num(data.items.length)} insights con más volumen de señal
              </span>
              {maybeMore && (
                <button
                  type="button"
                  disabled={insightsState.refreshing}
                  onClick={() => setState((prev) => ({ ...prev, batches: prev.batches + 1 }))}
                  className="rounded-lg border border-surface-border px-3 py-1 text-2xs font-semibold text-nike-ink-soft transition-colors duration-fast hover:border-surface-border-strong disabled:opacity-40"
                >
                  Cargar {BRAND_INSIGHTS_BATCH} más
                </button>
              )}
            </div>
          </>
        )}
      </Card>
    </div>
  )
}

// ── Insights agrupados por dimensión ────────────────────────────────
function InsightGroups({ insights }: { insights: BrandInsight[] }) {
  const groups = useMemo(() => {
    const map = new Map<string, BrandInsight[]>()
    for (const i of insights) {
      const key = i.dimension ?? 'sin_dimension'
      const list = map.get(key) ?? []
      list.push(i)
      map.set(key, list)
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [insights])

  return (
    <div className="space-y-6">
      {groups.map(([dimension, items]) => (
        <section key={dimension}>
          <h3 className="mb-2.5 flex items-center gap-2 border-b border-surface-border pb-1.5 text-sm font-bold text-nike-ink">
            <span aria-hidden="true" className="inline-block h-3 w-1 rounded-pill bg-nike-red" />
            {dimensionLabel(dimension)}
            <span className="tabular text-2xs font-medium text-nike-muted">({items.length})</span>
          </h3>
          <div className="grid gap-3 lg:grid-cols-2">
            {items.map((insight) => (
              <InsightCard key={insight.id} insight={insight} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function InsightCard({ insight }: { insight: BrandInsight }) {
  const evidence = evidenceOf(insight)
  const direction = insight.direction ?? 'flat'
  const dirColor = direction === 'up' ? '#0A6B0A' : direction === 'down' ? '#8E2020' : '#5A5A55'
  const dirIcon = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '▬'
  const sentiment = insight.sentiment ?? 0

  return (
    <article className="rounded-card border border-surface-border bg-white p-4 shadow-card">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <Tag title="Marca">{text(insight.brand)}</Tag>
        <Tag title="Tópico">{humanize(insight.topic ?? '—')}</Tag>
        <ConfidenceBadge confidence={insight.confidence} />
      </div>

      <p className="text-sm font-semibold leading-snug text-nike-ink">{text(insight.insight_text)}</p>

      {/* Respaldo cuantitativo */}
      <dl className="mt-3 grid grid-cols-3 gap-2 rounded-lg border border-surface-border bg-surface-muted px-3 py-2">
        <div>
          <dt className="text-2xs uppercase tracking-wide text-nike-muted">Señal</dt>
          <dd className="tabular text-sm font-bold text-nike-ink">{num(insight.signal_volume)}</dd>
        </div>
        <div>
          <dt className="text-2xs uppercase tracking-wide text-nike-muted">Tendencia</dt>
          <dd className="tabular text-sm font-bold" style={{ color: dirColor }}>
            <span aria-hidden="true">{dirIcon}</span> {pct(insight.trend, 0)}
          </dd>
        </div>
        <div>
          <dt className="text-2xs uppercase tracking-wide text-nike-muted">Sentimiento</dt>
          <dd className="tabular text-sm font-bold text-nike-ink">{dec(sentiment, 2)}</dd>
        </div>
      </dl>

      {/* Sentimiento -1..1 en escala divergente */}
      <div className="mt-2">
        <div className="relative h-1.5 w-full overflow-hidden rounded-sm bg-surface-sunken">
          <div
            className="absolute top-0 h-full"
            style={{
              left: sentiment >= 0 ? '50%' : `${50 + sentiment * 50}%`,
              width: `${Math.abs(sentiment) * 50}%`,
              backgroundColor: sentiment >= 0 ? '#2A78D6' : '#D03B3B',
            }}
          />
          <div className="absolute left-1/2 top-0 h-full w-px bg-surface-border-strong" />
        </div>
        <div className="mt-0.5 flex justify-between text-[9px] uppercase tracking-wide text-nike-muted">
          <span>negativo</span>
          <span>neutro</span>
          <span>positivo</span>
        </div>
      </div>

      {/* Evidencia — sin esto el insight no existe. Con URL se puede ir al
          comentario original; sin ella se dice por qué, pero no se esconde. */}
      <div className="mt-3">
        <EvidenceList items={evidence} max={3} />
      </div>

      <p className="mt-2.5 text-[10px] text-nike-muted">
        Período {period(insight.period_start, insight.period_end)}
      </p>
    </article>
  )
}
