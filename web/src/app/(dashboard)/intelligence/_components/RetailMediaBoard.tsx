'use client'

import { useEffect, useMemo, useState } from 'react'
import type { RetailMedia, RetailMediaResponse } from '@/types/intelligence'
import { getRetailMedia } from '@/lib/intelligence/api'
import { depsKeyOf } from '@/lib/intelligence/depsKey'
import { useApi, useDebounced } from '@/lib/intelligence/useApi'
import {
  driverMetrics,
  driverValue,
  driversRationale,
  normalizeDrivers,
} from '@/lib/intelligence/drivers'
import { dec, money, num, pct, score } from '@/lib/format'
import { recommendationStyle } from '@/components/charts/palette'
import { Card, EmptyState, ErrorState, MeterBar } from '@/components/ui'
import { ConfidenceBadge } from '@/components/intelligence/badges'
import { DriverList } from '@/components/intelligence/DriverList'
import { CommandHint } from '@/components/intelligence/hints'
import { ProductLine } from '@/components/intelligence/ProductLine'
import Pager from './Pager'
import {
  EMPTY_RETAIL_MEDIA_STATE,
  RETAIL_MEDIA_PAGE_SIZE,
  type RetailMediaState,
  retailMediaQueryFrom,
  retailMediaQueryKey,
} from './retailMediaParams'
import { RetailMediaListSkeleton } from './skeletons'
import { syncUrl } from './urlState'

export interface RetailMediaBoardProps {
  initialState: RetailMediaState
  initialData: RetailMediaResponse | null
  initialError: string | null
}

/**
 * Retail Media — mitad cliente.
 *
 * Cliente por los dos filtros (recomendación y score mínimo), que el backend
 * resuelve con `recommendation` y `min_score`. La paginación también es del
 * backend: cada fila trae producto Nike, competidor, retailer y siete drivers,
 * así que traer más de una página es caro y no se usa.
 */
export default function RetailMediaBoard({
  initialState,
  initialData,
  initialError,
}: RetailMediaBoardProps) {
  const [state, setState] = useState<RetailMediaState>(initialState)

  // Igual que en oportunidades: el slider pinta al instante y consulta al soltar.
  const [scoreDraft, setScoreDraft] = useState(initialState.min_score)
  const debouncedScore = useDebounced(scoreDraft, 300)

  useEffect(() => {
    setState((prev) =>
      prev.min_score === debouncedScore ? prev : { ...prev, min_score: debouncedScore, page: 0 },
    )
  }, [debouncedScore])

  const query = useMemo(() => retailMediaQueryFrom(state), [state])
  const queryKey = retailMediaQueryKey(query)

  const mediaState = useApi((signal) => getRetailMedia(query, signal), [queryKey], {
    initialData,
    initialKey: depsKeyOf([retailMediaQueryKey(retailMediaQueryFrom(initialState))]),
  })

  useEffect(() => {
    syncUrl({
      rec: state.recommendation,
      min: state.min_score,
      page: state.page || null,
    })
  }, [state])

  const error = mediaState.error ?? (mediaState.data === null ? initialError : null)

  if (error) {
    return (
      <Card>
        <ErrorState
          title="No pudimos cargar el motor de inteligencia"
          description={error}
          onRetry={mediaState.reload}
        />
      </Card>
    )
  }

  const data = mediaState.data
  if (data === null) return <RetailMediaListSkeleton />

  const facets = data.facets?.by_recommendation ?? []
  const thresholds = data.thresholds ?? {}
  const hasFilters = state.recommendation !== '' || state.min_score > 0

  if (data.total === 0 && !hasFilters) {
    return (
      <Card>
        <EmptyState
          title="Sin oportunidades de retail media"
          description="El motor no generó casos. Cuando corra, verás producto, retailer, competidor, stock, gap de precio y la acción recomendada."
          action={<CommandHint />}
        />
      </Card>
    )
  }

  return (
    <div
      className={`space-y-4 ${
        mediaState.refreshing ? 'opacity-60 transition-opacity duration-fast' : ''
      }`}
      aria-busy={mediaState.refreshing}
    >
      {/* Distribución de recomendaciones — sobre el universo completo */}
      {facets.length > 0 && (
        <div className="grid gap-gutter sm:grid-cols-2 xl:grid-cols-3">
          {facets.map((f) => {
            const rec = recommendationStyle(f.recommendation)
            const active = state.recommendation === f.recommendation
            return (
              <button
                key={f.recommendation ?? 'none'}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setState((prev) => ({
                    ...prev,
                    recommendation: active ? '' : (f.recommendation ?? ''),
                    page: 0,
                  }))
                }
                className="rounded-card border bg-white px-4 py-3 text-left shadow-card transition-shadow duration-fast hover:shadow-card-hover"
                style={{
                  borderColor: active ? rec.color : '#EDEDED',
                  borderLeftWidth: 4,
                  borderLeftColor: rec.color,
                }}
              >
                <p className="text-xs font-bold leading-snug text-nike-ink">{rec.label}</p>
                <div className="mt-1.5 flex items-baseline gap-2">
                  <span className="tabular text-xl font-bold" style={{ color: rec.color }}>
                    {num(f.n)}
                  </span>
                  <span className="tabular text-2xs text-nike-muted">
                    caso(s) · score prom. {dec(f.avg_score)}
                  </span>
                </div>
                <p className="mt-1.5 text-2xs leading-relaxed text-nike-ink-soft">{rec.blurb}</p>
              </button>
            )
          })}
        </div>
      )}

      {/* Filtros + umbrales */}
      <Card>
        <div className="flex flex-wrap items-end gap-5">
          <label className="block">
            <span className="label-caps mb-1 block">
              Score mínimo: <span className="tabular font-bold">{scoreDraft}</span>
            </span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={scoreDraft}
              onChange={(e) => setScoreDraft(Number(e.target.value))}
              className="w-48 accent-nike-red"
            />
          </label>

          <div className="flex items-center gap-3">
            <span className="text-xs text-nike-ink-soft">
              <span className="tabular font-bold text-nike-ink">{num(data.total)}</span> caso(s)
            </span>
            {hasFilters && (
              <button
                type="button"
                onClick={() => {
                  setScoreDraft(0)
                  setState({ ...EMPTY_RETAIL_MEDIA_STATE })
                }}
                className="text-2xs font-semibold text-nike-red hover:underline"
              >
                Limpiar filtros
              </button>
            )}
          </div>

          {Object.keys(thresholds).length > 0 && (
            <div className="ml-auto max-w-lg">
              <p className="label-caps mb-1">Umbrales vigentes (config/weights.yaml)</p>
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-nike-ink-soft">
                {Object.entries(thresholds).map(([k, v]) => (
                  <span key={k} className="tabular">
                    <span className="font-mono text-nike-muted">{k}</span>{' '}
                    <span className="font-semibold text-nike-ink">{v}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </Card>

      {data.items.length === 0 ? (
        <Card>
          <EmptyState
            title="Ningún caso coincide con el filtro"
            description="Bajá el score mínimo o quitá el filtro de recomendación."
          />
        </Card>
      ) : (
        <div className="space-y-3">
          <Pager
            page={state.page}
            pageSize={RETAIL_MEDIA_PAGE_SIZE}
            offset={data.offset ?? state.page * RETAIL_MEDIA_PAGE_SIZE}
            shown={data.items.length}
            total={data.total}
            noun="caso"
            busy={mediaState.refreshing}
            onPage={(page) => setState((prev) => ({ ...prev, page }))}
          />
          {data.items.map((item) => (
            <RetailMediaRow key={item.id} item={item} />
          ))}
          <Pager
            page={state.page}
            pageSize={RETAIL_MEDIA_PAGE_SIZE}
            offset={data.offset ?? state.page * RETAIL_MEDIA_PAGE_SIZE}
            shown={data.items.length}
            total={data.total}
            noun="caso"
            busy={mediaState.refreshing}
            onPage={(page) => {
              setState((prev) => ({ ...prev, page }))
              window.scrollTo({ top: 0, behavior: 'smooth' })
            }}
          />
        </div>
      )}
    </div>
  )
}

function RetailMediaRow({ item }: { item: RetailMedia }) {
  const rec = recommendationStyle(item.recommendation)

  const drivers = normalizeDrivers(item.drivers)
  const metrics = driverMetrics(item.drivers)
  const rationale = driversRationale(item.drivers)

  const stockHealth = driverValue(drivers, 'nike_stock_health')
  const priceCompetitiveness = driverValue(drivers, 'price_competitiveness')
  const competitiveRelevance = driverValue(drivers, 'competitive_relevance')
  const competitorStockGap = driverValue(drivers, 'competitor_stock_gap')

  return (
    <article
      className="overflow-hidden rounded-card border border-surface-border bg-white shadow-card"
      style={{ borderLeft: `4px solid ${rec.color}` }}
    >
      <div className="grid gap-4 p-4 xl:grid-cols-[1.5fr_1fr_1fr]">
        {/* Sujetos */}
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="rounded px-2 py-0.5 text-2xs font-bold uppercase tracking-wide"
              style={{ backgroundColor: rec.bg, color: rec.text, border: `1px solid ${rec.border}` }}
            >
              {rec.label}
            </span>
            <ConfidenceBadge confidence={item.confidence} />
            {item.retailer && (
              <span className="text-2xs font-semibold text-nike-ink-soft">
                {item.retailer.name}
                {item.retailer.channel ? ` · ${item.retailer.channel}` : ''}
              </span>
            )}
          </div>

          <ProductLine product={item.nike_product} role="nike" />
          <div className="flex items-center gap-2 pl-1">
            <span aria-hidden="true" className="text-2xs font-bold text-nike-muted">
              compite con
            </span>
            <span className="h-px flex-1 bg-surface-border" />
          </div>
          <ProductLine product={item.competitor_product} role="competitor" />

          <p className="text-2xs leading-relaxed text-nike-ink-soft">{rationale ?? rec.blurb}</p>
        </div>

        {/* Señales operativas */}
        <div className="space-y-2.5 border-t border-surface-border pt-3 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0">
          <p className="label-caps">Señales del caso</p>
          <Signal
            label="Salud de stock Nike"
            value={stockHealth}
            hint="Disponibilidad de talles: pautar sin stock es tirar plata."
          />
          <Signal
            label="Competitividad de precio"
            value={priceCompetitiveness}
            hint="Qué tan cerca está Nike del precio del competidor."
          />
          <Signal
            label="Relevancia competitiva"
            value={competitiveRelevance}
            hint="Match score con el competidor del caso."
          />
          <Signal
            label="Quiebre del competidor"
            value={competitorStockGap}
            hint="Cuánto stock le falta al competidor: ventana para capturar demanda."
          />
          <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-surface-border pt-2">
            <Metric label="Stock Nike" value={metrics['nike_stock_pct']} suffix="%" />
            <Metric label="Stock competidor" value={metrics['competitor_stock_pct']} suffix="%" />
            <Metric label="Gap de precio" value={metrics['price_gap_pct']} suffix="%" signedValue />
            <Metric label="Descuento Nike" value={metrics['nike_discount_pct']} suffix="%" />
            <Metric
              label="Share of shelf Nike"
              value={
                metrics['nike_shelf_share'] !== undefined
                  ? metrics['nike_shelf_share'] * 100
                  : undefined
              }
              suffix="%"
            />
            <Metric label="Business importance" value={metrics['business_importance']} />
          </dl>
          {item.nike_product?.msrp !== null && item.nike_product?.msrp !== undefined && (
            <p className="tabular text-2xs text-nike-muted">
              MSRP Nike {money(item.nike_product.msrp)}
              {item.competitor_product?.msrp !== null && item.competitor_product?.msrp !== undefined
                ? ` · competidor ${money(item.competitor_product.msrp)}`
                : ''}
            </p>
          )}
        </div>

        {/* Score + drivers */}
        <div className="border-t border-surface-border pt-3 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="label-caps">Opportunity score</span>
            <span
              className="tabular text-metric-sm font-extrabold leading-none"
              style={{ color: rec.color }}
            >
              {score(item.score)}
            </span>
          </div>
          <MeterBar value={item.score} max={100} color={rec.color} height={8} />

          <p className="label-caps mb-1.5 mt-3">Por qué — drivers</p>
          <DriverList drivers={item.drivers} color={rec.color} max={7} />
        </div>
      </div>
    </article>
  )
}

function Signal({ label, value, hint }: { label: string; value: number | null; hint: string }) {
  if (value === null) {
    return (
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-2xs text-nike-ink-soft">{label}</span>
          <span className="text-2xs italic text-nike-muted">sin dato</span>
        </div>
        <div className="hatch-muted mt-0.5 h-1.5 w-full rounded-sm" title={hint} />
      </div>
    )
  }

  // Los drivers vienen normalizados 0..1 desde el motor.
  const normalized = value <= 1 ? value * 100 : value
  const color = normalized >= 70 ? '#0CA30C' : normalized >= 40 ? '#FAB219' : '#D03B3B'

  return (
    <div title={hint}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-2xs text-nike-ink-soft">{label}</span>
        <span className="tabular text-2xs font-bold text-nike-ink">{pct(normalized, 0)}</span>
      </div>
      <div className="mt-0.5">
        <MeterBar value={normalized} max={100} color={color} height={6} />
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  suffix = '',
  signedValue = false,
}: {
  label: string
  value: number | undefined
  suffix?: string
  signedValue?: boolean
}) {
  const has = value !== undefined && Number.isFinite(value)
  const sign = signedValue && has && value > 0 ? '+' : ''
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-nike-muted">{label}</dt>
      <dd className="tabular text-2xs font-semibold text-nike-ink">
        {has ? `${sign}${value.toFixed(1)}${suffix}` : 'sin dato'}
      </dd>
    </div>
  )
}
