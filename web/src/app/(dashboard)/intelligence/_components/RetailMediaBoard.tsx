'use client'

import { useEffect, useMemo, useState } from 'react'
import type {
  RetailMedia,
  RetailMediaCompetitor,
  RetailMediaResponse,
  SignalValue,
} from '@/types/intelligence'
import { getRetailMedia } from '@/lib/intelligence/api'
import { depsKeyOf } from '@/lib/intelligence/depsKey'
import { useApi, useDebounced } from '@/lib/intelligence/useApi'
import { driverValue, driversRationale, normalizeDrivers, signalIndex } from '@/lib/intelligence/drivers'
import { type GlossaryTerms, glossaryGroup, termIndex } from '@/lib/intelligence/glossary'
import { formatMagnitude } from '@/lib/intelligence/units'
import { dec, money, num, pct, score, text } from '@/lib/format'
import { recommendationStyle } from '@/components/charts/palette'
import { Card, EmptyState, ErrorState, InfoTip, MeterBar } from '@/components/ui'
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
 * backend: cada fila trae producto Nike, el set competidor, retailer y siete
 * drivers, así que traer más de una página es caro y no se usa.
 *
 * Contrato: el motor agrupa por CUADRO (producto Nike × retailer) con varios
 * competidores adentro, no una fila por rival. Cada ítem trae `rationale` como
 * campo propio, `drivers` (los factores ponderados, `contribution` suma 100),
 * `signals` (las métricas observadas, cada una con su unidad) y `competitors`
 * (el set completo). La decisión de invertir en visibilidad se toma mirando el
 * conjunto: por eso el cuadro muestra todos los rivales y no sólo el líder.
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
  // Los drivers del cuadro mezclan dos familias: los 7 de retail media y el
  // score de business importance, que es uno de ellos.
  const terms = termIndex(data.glossary, 'retail_media', 'business_importance')
  const glossaryNote = glossaryGroup(data.glossary, 'retail_media')?.description ?? null

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
            <RetailMediaRow key={item.id} item={item} terms={terms} note={glossaryNote} />
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

function RetailMediaRow({
  item,
  terms,
  note,
}: {
  item: RetailMedia
  terms: GlossaryTerms
  note: string | null
}) {
  const rec = recommendationStyle(item.recommendation)

  const drivers = normalizeDrivers(item.drivers)
  // El racional es un campo propio del ítem. `driversRationale` queda de
  // respaldo por si el motor todavía lo manda dentro del sobre viejo.
  const rationale = item.rationale ?? driversRationale(item.drivers)
  // Las métricas observadas viven en `signals`, con su unidad declarada.
  const signals = signalIndex(item.signals)

  const stockHealth = driverValue(drivers, 'nike_stock_health')
  const priceCompetitiveness = driverValue(drivers, 'price_competitiveness')
  const competitiveRelevance = driverValue(drivers, 'competitive_relevance')
  const competitorStockGap = driverValue(drivers, 'competitor_stock_gap')

  const competitors = item.competitors ?? []
  const competitorCount = item.competitor_count ?? competitors.length

  return (
    <article
      className="overflow-hidden rounded-card border border-surface-border bg-white shadow-card"
      style={{ borderLeft: `4px solid ${rec.color}` }}
    >
      <div className="grid gap-4 p-4 xl:grid-cols-[1.5fr_1fr_1fr]">
        {/* Sujetos: el cuadro es producto Nike × retailer, con TODO su set rival */}
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

          <CompetitorSet competitors={competitors} count={competitorCount} fallback={item.competitor_product} />

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
            hint="Qué tan competitivo está el precio Nike contra el peor caso del set."
          />
          <Signal
            label="Relevancia competitiva"
            value={competitiveRelevance}
            hint="Match score del competidor líder del cuadro."
          />
          <Signal
            label="Quiebre del competidor"
            value={competitorStockGap}
            hint="Cuánto stock le falta al set rival: ventana para capturar demanda."
          />

          {/* Métricas observadas: cada una con la unidad que declara el backend */}
          <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-surface-border pt-2">
            <Metric signal={signals['nike_stock_pct']} label="Stock Nike" />
            <Metric signal={signals['competitor_stock_pct']} label="Stock del set rival" />
            <Metric signal={signals['price_gap_pct']} label="Gap de precio" signedValue />
            <Metric signal={signals['nike_discount_pct']} label="Descuento Nike" />
            <Metric signal={signals['nike_shelf_share']} label="Share of shelf Nike" />
            <Metric signal={signals['business_importance']} label="Importancia de negocio" />
          </dl>
          {item.nike_product?.msrp !== null && item.nike_product?.msrp !== undefined && (
            <p className="tabular text-2xs text-nike-muted">
              MSRP Nike {money(item.nike_product.msrp)}
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

          <p className="label-caps mb-1.5 mt-3 flex items-center gap-1">
            Por qué — drivers
            {note && <InfoTip label="Cómo se combinan los drivers" content={note} side="left" />}
          </p>
          <DriverList drivers={item.drivers} color={rec.color} max={7} terms={terms} />
        </div>
      </div>
    </article>
  )
}

/**
 * El set competidor del cuadro.
 *
 * Mostrar un solo rival era la versión vieja del contrato y llevaba a decidir
 * mirando a medias: si tres marcas empujan la misma franquicia en el mismo
 * retailer, la inversión en visibilidad se justifica por el conjunto. Cada fila
 * dice qué papel jugó el rival en el score (líder de relevancia, referencia de
 * precio —que va a PEOR CASO— o motor del momentum) y si está presente en ese
 * retailer.
 */
function CompetitorSet({
  competitors,
  count,
  fallback,
}: {
  competitors: RetailMediaCompetitor[]
  count: number
  fallback: RetailMedia['competitor_product']
}) {
  if (competitors.length === 0) {
    // Contrato viejo (o cuadro sin set persistido): al menos el rival de referencia.
    return (
      <>
        <SetHeading count={count} />
        <ProductLine product={fallback} role="competitor" />
      </>
    )
  }

  return (
    <div>
      <SetHeading count={count} />
      <ul className="space-y-1.5">
        {competitors.map((c) => (
          <li
            key={c.competitor_product_id}
            className="rounded-lg border border-surface-border bg-surface-muted px-2.5 py-1.5"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate text-2xs font-semibold text-nike-ink">
                {text(c.product?.product_name)}
              </span>
              <span className="tabular flex-shrink-0 text-2xs font-bold text-nike-ink-soft">
                match {dec(c.match_score, 1)}
              </span>
            </div>

            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-nike-muted">
              <span className="tabular">peso {pct((c.relevance_weight ?? 0) * 100, 0)}</span>
              {c.stock_pct !== null && <span className="tabular">stock {dec(c.stock_pct, 0)}%</span>}
              {c.price_gap_pct !== null && (
                <span className="tabular">
                  gap {c.price_gap_pct > 0 ? '+' : ''}
                  {dec(c.price_gap_pct, 1)}%
                </span>
              )}
              {c.momentum !== null && <span className="tabular">momentum {dec(c.momentum, 2)}</span>}
              <span>{c.present_at_retailer ? 'en el retailer' : 'no listado acá'}</span>
            </div>

            <div className="mt-1 flex flex-wrap gap-1">
              {c.is_leader && <RoleTag>líder de relevancia</RoleTag>}
              {c.is_price_reference && <RoleTag>referencia de precio</RoleTag>}
              {c.is_momentum_reference && <RoleTag>tracciona el momentum</RoleTag>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function SetHeading({ count }: { count: number }) {
  return (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="text-2xs font-bold text-nike-muted">
        compite con {num(count)} {count === 1 ? 'rival relevante' : 'rivales relevantes'}
      </span>
      <span className="h-px flex-1 bg-surface-border" />
    </div>
  )
}

function RoleTag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-pill border border-surface-border-strong bg-white px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-nike-ink-soft">
      {children}
    </span>
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

/**
 * Una métrica observada del cuadro.
 *
 * Se lee de `signals`, no de los drivers: el motor separó las dos cosas y cada
 * señal viaja con su unidad, así que el formato lo decide la unidad y no una
 * suposición de la pantalla (`nike_shelf_share` llega como fracción 0,4 y se
 * muestra 40%; `price_gap_pct` llega ya en porcentaje).
 */
function Metric({
  signal,
  label,
  signedValue = false,
}: {
  signal: SignalValue | undefined
  /** Etiqueta local. La del backend se usa si no se pasa ninguna. */
  label?: string
  signedValue?: boolean
}) {
  const value = signal?.value
  const has = value !== null && value !== undefined && Number.isFinite(value)
  const shown = has ? formatMagnitude(value, signal?.unit) : 'sin dato'
  const sign = signedValue && has && value > 0 ? '+' : ''

  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-nike-muted">
        {label ?? signal?.label ?? '—'}
      </dt>
      <dd className="tabular text-2xs font-semibold text-nike-ink">
        {has ? `${sign}${shown}` : shown}
      </dd>
    </div>
  )
}
