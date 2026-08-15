'use client'

import { useMemo, useState } from 'react'
import type { Opportunity } from '@/types/intelligence'
import { getOpportunities, type OpportunityQuery } from '@/lib/intelligence/api'
import { useApi } from '@/lib/intelligence/useApi'
import { num } from '@/lib/format'
import {
  SEVERITY_ORDER,
  familyLabel,
  humanize,
  severityRank,
  severityStyle,
} from '@/components/charts/palette'
import { AsyncSection, Card, EmptyState, PageIntro } from '@/components/ui'
import { CommandHint } from '@/components/intelligence/hints'
import { OpportunityCard } from '@/components/intelligence/OpportunityCard'

type GroupMode = 'none' | 'family' | 'severity'

export default function OpportunityCenterPage() {
  const [family, setFamily] = useState('')
  const [severity, setSeverity] = useState('')
  const [type, setType] = useState('')
  const [minImportance, setMinImportance] = useState(0)
  const [group, setGroup] = useState<GroupMode>('none')

  const query: OpportunityQuery = useMemo(
    () => ({
      family: family || undefined,
      severity: severity || undefined,
      opportunity_type: type || undefined,
      min_importance: minImportance || undefined,
      limit: 200,
    }),
    [family, severity, type, minImportance],
  )

  const state = useApi((signal) => getOpportunities(query, signal), [JSON.stringify(query)])

  return (
    <div>
      <PageIntro
        question="¿Cuánto importa? · ¿Por qué? · ¿Qué hacemos?"
        description="Cada tarjeta responde cuatro cosas: qué está pasando, cuánto importa comercialmente (Business Importance), por qué el motor lo cree (drivers) y qué acción se recomienda."
      />

      <AsyncSection
        state={state}
        loadingRows={6}
        empty={
          <Card>
            <EmptyState
              title="Sin oportunidades"
              description="La tabla opportunities está vacía. El motor evalúa reglas sobre precios, surtido, distribución, stock y momentum: necesita que las etapas previas del pipeline hayan corrido."
              action={<CommandHint />}
            />
          </Card>
        }
      >
        {(data) => {
          const facets = data.facets ?? {}
          const noneAtAll = data.total === 0 && !family && !severity && !type && minImportance === 0

          if (noneAtAll) {
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
            <div className="space-y-4">
              {/* Resumen por severidad */}
              <div className="grid grid-cols-2 gap-gutter lg:grid-cols-4">
                {SEVERITY_ORDER.map((sev) => {
                  const sty = severityStyle(sev)
                  const n = facets.by_severity?.find((f) => f.severity === sev)?.n ?? 0
                  const active = severity === sev
                  return (
                    <button
                      key={sev}
                      type="button"
                      onClick={() => setSeverity(active ? '' : sev)}
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
                      value={family}
                      onChange={(e) => setFamily(e.target.value)}
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
                      value={type}
                      onChange={(e) => setType(e.target.value)}
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
                      Importancia mínima: <span className="tabular font-bold">{minImportance}</span>
                    </span>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={minImportance}
                      onChange={(e) => setMinImportance(Number(e.target.value))}
                      className="w-40 accent-nike-red"
                    />
                  </label>

                  <label className="block">
                    <span className="label-caps mb-1 block">Agrupar por</span>
                    <select
                      value={group}
                      onChange={(e) => setGroup(e.target.value as GroupMode)}
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
                    {(family || severity || type || minImportance > 0) && (
                      <button
                        type="button"
                        onClick={() => {
                          setFamily('')
                          setSeverity('')
                          setType('')
                          setMinImportance(0)
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
                <OpportunityGroups items={data.items} group={group} />
              )}
            </div>
          )
        }}
      </AsyncSection>
    </div>
  )
}

function OpportunityGroups({ items, group }: { items: Opportunity[]; group: GroupMode }) {
  if (group === 'none') {
    return (
      <div className="grid gap-gutter lg:grid-cols-2 2xl:grid-cols-3">
        {items.map((o) => (
          <OpportunityCard key={o.id} opportunity={o} />
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
              <span className="tabular text-2xs font-medium text-nike-muted">({list.length})</span>
            </h2>
            <div className="grid gap-gutter lg:grid-cols-2 2xl:grid-cols-3">
              {list.map((o) => (
                <OpportunityCard key={o.id} opportunity={o} />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
