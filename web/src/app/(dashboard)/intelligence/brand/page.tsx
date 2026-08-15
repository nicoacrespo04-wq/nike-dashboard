'use client'

import { useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { BrandInsight, MarketSignal, Topic } from '@/types/intelligence'
import { getBrandInsights, getBrandMomentum, getBrandTopics } from '@/lib/intelligence/api'
import { useApi } from '@/lib/intelligence/useApi'
import {
  evidenceOf,
  evidenceSource,
  evidenceText,
  hasEvidence,
} from '@/lib/intelligence/insights'
import { dec, num, pct, period, signed, text, truncateText } from '@/lib/format'
import {
  CHART_INK,
  dimensionLabel,
  entityLabel,
  humanize,
  signalTypeLabel,
} from '@/components/charts/palette'
import {
  AsyncSection,
  Card,
  EmptyState,
  KPICard,
  MeterBar,
  PageIntro,
  SectionHeader,
} from '@/components/ui'
import { ConfidenceBadge, Tag } from '@/components/intelligence/badges'
import { CommandHint } from '@/components/intelligence/hints'

const COUNTRY = 'AR'

export default function ConsumerBrandPage() {
  const [dimension, setDimension] = useState('')
  const [minConfidence, setMinConfidence] = useState('')

  const insightsState = useApi(
    (signal) =>
      getBrandInsights(
        {
          country: COUNTRY,
          dimension: dimension || undefined,
          min_confidence: minConfidence || undefined,
          limit: 200,
        },
        signal,
      ),
    [dimension, minConfidence],
  )

  const momentumState = useApi(
    (signal) => getBrandMomentum({ country: COUNTRY, limit: 30 }, signal),
    [],
  )
  const topicsState = useApi((signal) => getBrandTopics({ country: COUNTRY, limit: 40 }, signal), [])

  return (
    <div>
      <PageIntro
        question="¿Qué está pasando? — Argentina"
        description="Percepción de marca, tópicos en tendencia, quejas, drivers positivos y momentum, siempre a partir de señal pública agregada. Regla dura del producto: un insight sin evidencia no se muestra."
      />

      <div className="space-y-5">
        {/* ── Insights ─────────────────────────────────────────── */}
        <AsyncSection
          state={insightsState}
          loadingRows={5}
          empty={
            <Card>
              <EmptyState
                title="Sin insights de consumidor"
                description="La tabla brand_insights está vacía. Esta etapa consume reviews, menciones editoriales y agregados sociales de Argentina."
                action={<CommandHint />}
              />
            </Card>
          }
        >
          {(data) => {
            const withEvidence = data.items.filter(hasEvidence)
            const suppressed = data.items.length - withEvidence.length
            const dimensions = Object.keys(data.taxonomy ?? {})

            return (
              <>
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
                    subtitle="menciones agregadas del período"
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

                <Card>
                  <SectionHeader
                    eyebrow="Percepción"
                    title="Insights de marca y consumidor"
                    subtitle="Cada insight declara volumen de señal, tendencia, dirección, confianza y ejemplos de evidencia."
                    actions={
                      <div className="flex gap-2">
                        <select
                          value={dimension}
                          onChange={(e) => setDimension(e.target.value)}
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
                          value={minConfidence}
                          onChange={(e) => setMinConfidence(e.target.value)}
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

                  {withEvidence.length === 0 ? (
                    <EmptyState
                      title={
                        data.items.length === 0
                          ? 'Sin insights para este filtro'
                          : 'Insights sin evidencia adjunta'
                      }
                      description={
                        data.items.length === 0
                          ? 'No hay insights que cumplan el filtro seleccionado.'
                          : `Hay ${data.items.length} insight(s) sin evidencia. Por regla del producto no se muestran: sin respaldo cuantitativo y ejemplos, no hay insight.`
                      }
                    />
                  ) : (
                    <InsightGroups insights={withEvidence} />
                  )}
                </Card>
              </>
            )
          }}
        </AsyncSection>

        {/* ── Momentum + tópicos ───────────────────────────────── */}
        <div className="grid gap-gutter xl:grid-cols-2">
          <Card>
            <SectionHeader
              eyebrow="Momentum"
              title="Marcas y franquicias que aceleran"
              subtitle="Volumen normalizado, variación vs. período anterior y aceleración."
              className="mb-3"
            />
            <AsyncSection
              state={momentumState}
              loadingRows={4}
              isEmpty={(d) => d.items.length === 0}
              empty={
                <EmptyState
                  title="Sin señales de momentum"
                  description="La tabla market_signals está vacía para Argentina."
                  size="sm"
                />
              }
            >
              {(data) => <MomentumTable items={data.items} />}
            </AsyncSection>
          </Card>

          <Card>
            <SectionHeader
              eyebrow="Conversación"
              title="Tópicos en tendencia"
              subtitle="Volumen de menciones agregadas por tópico e intención, con sentimiento medio."
              className="mb-3"
            />
            <AsyncSection
              state={topicsState}
              loadingRows={4}
              isEmpty={(d) => d.items.length === 0}
              empty={
                <EmptyState
                  title="Sin tópicos de conversación"
                  description="La tabla social_mention_aggregates está vacía. Sin señal social no hay tópicos que reportar."
                  size="sm"
                />
              }
            >
              {(data) => <TopicsChart items={data.items} />}
            </AsyncSection>
          </Card>
        </div>
      </div>
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

      {/* Evidencia — sin esto el insight no existe */}
      <div className="mt-3">
        <p className="label-caps mb-1.5">Evidencia ({evidence.length})</p>
        <ul className="space-y-1.5">
          {evidence.slice(0, 3).map((e, i) => (
            <li key={i} className="border-l-2 border-surface-border pl-2.5">
              <p className="text-2xs leading-relaxed text-nike-ink-soft">
                “{truncateText(evidenceText(e), 160)}”
              </p>
              <p className="text-[10px] text-nike-muted">{evidenceSource(e)}</p>
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-2.5 text-[10px] text-nike-muted">
        Período {period(insight.period_start, insight.period_end)}
      </p>
    </article>
  )
}

// ── Momentum ────────────────────────────────────────────────────────
function MomentumTable({ items }: { items: MarketSignal[] }) {
  const maxValue = Math.max(...items.map((s) => Math.abs(s.value ?? 0)), 1)
  return (
    <div className="nike-table-wrap">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-surface-border text-left text-label uppercase text-nike-muted">
            <th className="py-1.5 pr-3 font-semibold">Entidad</th>
            <th className="py-1.5 pr-3 font-semibold">Señal</th>
            <th className="py-1.5 pr-3 font-semibold">Volumen</th>
            <th className="py-1.5 pr-3 text-right font-semibold">Δ</th>
            <th className="py-1.5 text-right font-semibold">Aceleración</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-border">
          {items.slice(0, 15).map((s) => {
            const accel = s.acceleration ?? 0
            return (
              <tr key={s.id}>
                <td className="py-1.5 pr-3">
                  <span className="block font-semibold text-nike-ink">
                    {entityLabel(s.entity_type, s.entity_id)}
                  </span>
                  <span className="text-2xs text-nike-muted">{s.entity_type}</span>
                </td>
                <td className="py-1.5 pr-3 text-nike-ink-soft">{signalTypeLabel(s.signal_type)}</td>
                <td className="w-28 py-1.5 pr-3">
                  <MeterBar
                    value={Math.abs(s.value ?? 0)}
                    max={maxValue}
                    color="#2A78D6"
                    height={6}
                  />
                  <span className="tabular text-[10px] text-nike-muted">{dec(s.value, 2)}</span>
                </td>
                <td className="tabular py-1.5 pr-3 text-right text-nike-ink-soft">
                  {signed(s.delta, 2)}
                </td>
                <td
                  className="tabular py-1.5 text-right font-bold"
                  style={{ color: accel >= 0 ? '#0A6B0A' : '#8E2020' }}
                >
                  <span aria-hidden="true">{accel >= 0 ? '▲' : '▼'}</span> {signed(accel, 2)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Tópicos ─────────────────────────────────────────────────────────
interface TopicDatum {
  label: string
  mentions: number
  sentiment: number
  intent: string
  brand: string
}

function TopicsChart({ items }: { items: Topic[] }) {
  const data: TopicDatum[] = items
    .filter((t) => (t.mentions ?? 0) > 0)
    .slice(0, 12)
    .map((t) => ({
      label: `${humanize(t.topic ?? '—')}${t.brand ? ` · ${t.brand}` : ''}`,
      mentions: t.mentions ?? 0,
      sentiment: t.sentiment ?? 0,
      intent: t.intent ?? '—',
      brand: t.brand ?? '—',
    }))

  if (data.length === 0) {
    return (
      <EmptyState
        title="Sin tópicos con volumen"
        description="Los agregados sociales existen pero ninguno declara menciones."
        size="sm"
      />
    )
  }

  return (
    <div>
      <div style={{ height: Math.max(200, data.length * 26 + 40) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 30, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} stroke={CHART_INK.grid} />
            <XAxis
              type="number"
              tick={{ fontSize: 10, fill: CHART_INK.axis }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tick={{ fontSize: 10, fill: CHART_INK.axisStrong }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
            />
            <RechartsTooltip
              cursor={{ fill: CHART_INK.cursor }}
              contentStyle={{
                fontSize: 11,
                borderRadius: 6,
                border: `1px solid ${CHART_INK.line}`,
              }}
              formatter={(value: number) => [`${value} menciones`, 'Volumen']}
            />
            <Bar dataKey="mentions" name="Menciones" radius={[0, 3, 3, 0]} barSize={12}>
              {data.map((d, i) => (
                <Cell key={i} fill={d.sentiment >= 0 ? '#2A78D6' : '#D03B3B'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-nike-ink-soft">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#2A78D6]" />
          sentimiento medio positivo o neutro
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#D03B3B]" />
          sentimiento medio negativo
        </span>
      </p>
    </div>
  )
}
