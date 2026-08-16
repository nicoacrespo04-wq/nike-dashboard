'use client'

import { useEffect, useMemo, useState } from 'react'
import type { Glossary, Opportunity, OpportunityListResponse } from '@/types/intelligence'
import { getOpportunities } from '@/lib/intelligence/api'
import { depsKeyOf } from '@/lib/intelligence/depsKey'
import { type GlossaryTerms, termIndex } from '@/lib/intelligence/glossary'
import { useApi, useDebounced } from '@/lib/intelligence/useApi'
import { num } from '@/lib/format'
import {
  SEVERITY_ORDER,
  familyLabel,
  humanize,
  severityRank,
  severityStyle,
} from '@/components/charts/palette'
import { Card, EmptyState, ErrorState } from '@/components/ui'
import { CommandHint } from '@/components/intelligence/hints'
import { OpportunityCard } from '@/components/intelligence/OpportunityCard'
import Pager from './Pager'
import {
  EMPTY_OPPORTUNITY_STATE,
  type GroupMode,
  OPPORTUNITIES_PAGE_SIZE,
  type OpportunityState,
  activeOpportunityFilters,
  opportunityQueryFrom,
  opportunityQueryKey,
} from './opportunityParams'
import { OpportunityGridSkeleton } from './skeletons'
import { syncUrl } from './urlState'

export interface OpportunityCenterProps {
  initialState: OpportunityState
  initialData: OpportunityListResponse | null
  initialError: string | null
  /**
   * Glosario de los componentes de business importance, resuelto en el servidor.
   * Es lo que hace que un driver con "18,7% de contribución" al lado se pueda
   * juzgar: sin saber qué mide la variable, el número no explica nada.
   */
  glossary?: Glossary | null
}

/**
 * Opportunity Center — mitad cliente.
 *
 * Cliente porque cada tarjeta de severidad, cada select y el slider de
 * importancia son filtros que el usuario mueve. Pero los cuatro filtros son
 * parámetros del backend (`family`, `severity`, `opportunity_type`,
 * `min_importance`) y la lista se pide de a `OPPORTUNITIES_PAGE_SIZE`: antes
 * se traían 200 oportunidades expandidas —con producto, competidor, retailer,
 * drivers y recomendación adentro— para mostrar las primeras.
 *
 * Las facetas (`by_severity`, `by_family`, `by_type`) las calcula el backend
 * sobre el total, no sobre la página: los contadores siguen siendo del universo
 * completo aunque en pantalla haya 24 tarjetas.
 */
export default function OpportunityCenter({
  initialState,
  initialData,
  initialError,
  glossary,
}: OpportunityCenterProps) {
  const [state, setState] = useState<OpportunityState>(initialState)
  const terms = useMemo(() => termIndex(glossary, 'business_importance'), [glossary])

  // El slider dispara `onChange` en cada paso del arrastre. El valor que se
  // pinta es el crudo (la UI responde al instante); el que consulta al backend
  // es el debounceado, así arrastrar de 0 a 100 es una consulta y no veinte.
  const [importanceDraft, setImportanceDraft] = useState(initialState.min_importance)
  const debouncedImportance = useDebounced(importanceDraft, 300)

  useEffect(() => {
    setState((prev) =>
      prev.min_importance === debouncedImportance
        ? prev
        : { ...prev, min_importance: debouncedImportance, page: 0 },
    )
  }, [debouncedImportance])

  const query = useMemo(() => opportunityQueryFrom(state), [state])
  const queryKey = opportunityQueryKey(query)

  const opportunitiesState = useApi((signal) => getOpportunities(query, signal), [queryKey], {
    initialData,
    initialKey: depsKeyOf([opportunityQueryKey(opportunityQueryFrom(initialState))]),
  })

  useEffect(() => {
    syncUrl({
      family: state.family,
      severity: state.severity,
      type: state.opportunity_type,
      min: state.min_importance,
      group: state.group === 'none' ? null : state.group,
      page: state.page || null,
    })
  }, [state])

  const activeCount = activeOpportunityFilters(state)
  const error = opportunitiesState.error ?? (opportunitiesState.data === null ? initialError : null)

  if (error) {
    return (
      <Card>
        <ErrorState
          title="No pudimos cargar el motor de inteligencia"
          description={error}
          onRetry={opportunitiesState.reload}
        />
      </Card>
    )
  }

  const data = opportunitiesState.data
  if (data === null) return <OpportunityGridSkeleton />

  const facets = data.facets ?? {}

  if (data.total === 0 && activeCount === 0) {
    return (
      <Card>
        <EmptyState
          title="Sin oportunidades"
          description="El motor no produjo oportunidades. Cuando corra, se ordenan solas por Business Importance y se agrupan por familia y severidad."
          action={<CommandHint />}
        />
      </Card>
    )
  }

  return (
    <div
      className={`space-y-4 ${
        opportunitiesState.refreshing ? 'opacity-60 transition-opacity duration-fast' : ''
      }`}
      aria-busy={opportunitiesState.refreshing}
    >
      {/* Resumen por severidad — contadores del universo completo */}
      <div className="grid grid-cols-2 gap-gutter lg:grid-cols-4">
        {SEVERITY_ORDER.map((sev) => {
          const sty = severityStyle(sev)
          const n = facets.by_severity?.find((f) => f.severity === sev)?.n ?? 0
          const active = state.severity === sev
          return (
            <button
              key={sev}
              type="button"
              onClick={() =>
                setState((prev) => ({ ...prev, severity: active ? '' : sev, page: 0 }))
              }
              aria-pressed={active}
              className="rounded-card border bg-white px-4 py-3 text-left shadow-card transition-shadow duration-fast hover:shadow-card-hover"
              style={{
                borderColor: active ? sty.color : '#EDEDED',
                borderLeftWidth: 4,
                borderLeftColor: sty.color,
              }}
            >
              <p className="label-caps">Severidad {sty.label}</p>
              <p
                className="tabular mt-1 text-metric-sm font-extrabold leading-none"
                style={{ color: sty.color }}
              >
                {num(n)}
              </p>
              <p className="mt-1 text-2xs text-nike-muted">
                {active ? 'Filtro activo — clic para quitar' : 'Clic para filtrar'}
              </p>
            </button>
          )
        })}
      </div>

      {/* Filtros */}
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="label-caps mb-1 block">Familia</span>
            <select
              value={state.family}
              onChange={(e) => setState((prev) => ({ ...prev, family: e.target.value, page: 0 }))}
              className="field-control text-xs"
            >
              <option value="">Todas</option>
              {(facets.by_family ?? []).map((f) => (
                <option key={f.family ?? 'none'} value={f.family ?? ''}>
                  {familyLabel(f.family)} ({f.n})
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="label-caps mb-1 block">Tipo de oportunidad</span>
            <select
              value={state.opportunity_type}
              onChange={(e) =>
                setState((prev) => ({ ...prev, opportunity_type: e.target.value, page: 0 }))
              }
              className="field-control text-xs"
            >
              <option value="">Todos</option>
              {(facets.by_type ?? []).map((f) => (
                <option key={f.opportunity_type ?? 'none'} value={f.opportunity_type ?? ''}>
                  {humanize(f.opportunity_type ?? '')} ({f.n})
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="label-caps mb-1 block">
              Importancia mínima: <span className="tabular font-bold">{importanceDraft}</span>
            </span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={importanceDraft}
              onChange={(e) => setImportanceDraft(Number(e.target.value))}
              className="w-40 accent-nike-red"
            />
          </label>

          <label className="block">
            <span className="label-caps mb-1 block">Agrupar por</span>
            <select
              value={state.group}
              onChange={(e) =>
                setState((prev) => ({ ...prev, group: e.target.value as GroupMode }))
              }
              className="field-control text-xs"
            >
              <option value="none">Sin agrupar (por importancia)</option>
              <option value="family">Familia</option>
              <option value="severity">Severidad</option>
            </select>
          </label>

          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-nike-ink-soft">
              <span className="tabular font-bold text-nike-ink">{num(data.total)}</span>{' '}
              resultado(s)
            </span>
            {activeCount > 0 && (
              <button
                type="button"
                onClick={() => {
                  setImportanceDraft(0)
                  setState((prev) => ({ ...EMPTY_OPPORTUNITY_STATE, group: prev.group }))
                }}
                className="text-2xs font-semibold text-nike-red hover:underline"
              >
                Limpiar filtros
              </button>
            )}
          </div>
        </div>
      </Card>

      {data.items.length === 0 ? (
        <Card>
          <EmptyState
            title="Ninguna oportunidad coincide con el filtro"
            description="Probá bajar la importancia mínima o quitar el filtro de familia/severidad."
          />
        </Card>
      ) : (
        <>
          <Pager
            page={state.page}
            pageSize={OPPORTUNITIES_PAGE_SIZE}
            offset={data.offset ?? state.page * OPPORTUNITIES_PAGE_SIZE}
            shown={data.items.length}
            total={data.total}
            noun="oportunidad"
            busy={opportunitiesState.refreshing}
            onPage={(page) => setState((prev) => ({ ...prev, page }))}
          />

          <OpportunityGroups items={data.items} group={state.group} terms={terms} />

          <Pager
            page={state.page}
            pageSize={OPPORTUNITIES_PAGE_SIZE}
            offset={data.offset ?? state.page * OPPORTUNITIES_PAGE_SIZE}
            shown={data.items.length}
            total={data.total}
            noun="oportunidad"
            busy={opportunitiesState.refreshing}
            onPage={(page) => {
              setState((prev) => ({ ...prev, page }))
              window.scrollTo({ top: 0, behavior: 'smooth' })
            }}
          />
        </>
      )}
    </div>
  )
}

function OpportunityGroups({
  items,
  group,
  terms,
}: {
  items: Opportunity[]
  group: GroupMode
  terms: GlossaryTerms
}) {
  if (group === 'none') {
    return (
      <div className="grid gap-gutter lg:grid-cols-2 2xl:grid-cols-3">
        {items.map((o) => (
          <OpportunityCard key={o.id} opportunity={o} terms={terms} />
        ))}
      </div>
    )
  }

  const buckets = new Map<string, Opportunity[]>()
  for (const o of items) {
    const key = group === 'family' ? (o.family ?? 'sin_familia') : (o.severity ?? 'SIN_SEVERIDAD')
    const list = buckets.get(key) ?? []
    list.push(o)
    buckets.set(key, list)
  }

  const keys = [...buckets.keys()].sort((a, b) => {
    if (group === 'severity') return severityRank(a) - severityRank(b)
    return (buckets.get(b)?.length ?? 0) - (buckets.get(a)?.length ?? 0)
  })

  return (
    <div className="space-y-6">
      {keys.map((key) => {
        const list = buckets.get(key) ?? []
        const label = group === 'family' ? familyLabel(key) : severityStyle(key).label
        const color = group === 'severity' ? severityStyle(key).color : '#111111'
        return (
          <section key={key}>
            <h2 className="mb-3 flex items-center gap-2 border-b border-surface-border pb-1.5 text-sm font-bold text-nike-ink">
              <span
                aria-hidden="true"
                className="inline-block h-3 w-1 rounded-pill"
                style={{ backgroundColor: color }}
              />
              {label}
              {/* El agrupado ordena la página en pantalla, no el total: el
                  número del universo completo está en las facetas de arriba. */}
              <span className="tabular text-2xs font-medium text-nike-muted">
                ({list.length} en esta página)
              </span>
            </h2>
            <div className="grid gap-gutter lg:grid-cols-2 2xl:grid-cols-3">
              {list.map((o) => (
                <OpportunityCard key={o.id} opportunity={o} terms={terms} />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
