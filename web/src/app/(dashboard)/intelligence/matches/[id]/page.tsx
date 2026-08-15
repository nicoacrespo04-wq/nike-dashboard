'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
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
import type { Factor, MatchDetail } from '@/types/intelligence'
import { getMatch } from '@/lib/intelligence/api'
import { useApi } from '@/lib/intelligence/useApi'
import { pct, pctFromFraction, score, text } from '@/lib/format'
import {
  CHART_INK,
  UNAVAILABLE_COLOR,
  factorColor,
  factorLabel,
  scoreTone,
  sortFactors,
} from '@/components/charts/palette'
import { AsyncSection, Card, EmptyState, PageIntro, SectionHeader } from '@/components/ui'
import { ConfidenceBadge } from '@/components/intelligence/badges'
import {
  ContributionStack,
  FactorLegend,
  FactorTable,
} from '@/components/intelligence/FactorBreakdown'
import { ProductLine } from '@/components/intelligence/ProductLine'

export default function MatchExplainabilityPage() {
  const params = useParams<{ id: string }>()
  const matchId = Number(params?.id ?? 0)

  const state = useApi((signal) => getMatch(matchId, signal), [matchId], {
    enabled: Number.isFinite(matchId) && matchId > 0,
  })

  return (
    <div>
      <Link
        href="/intelligence/matches"
        className="mb-3 inline-block text-2xs font-semibold text-nike-red hover:underline"
      >
        ← Volver a Competitive Matches
      </Link>

      <AsyncSection
        state={state}
        loadingRows={6}
        empty={
          <Card>
            <EmptyState
              title="Match no encontrado"
              description="El id solicitado no existe o el motor de matching todavía no persistió resultados."
            />
          </Card>
        }
      >
        {(match) => <MatchExplain match={match} />}
      </AsyncSection>
    </div>
  )
}

function MatchExplain({ match }: { match: MatchDetail }) {
  const factors = match.factors
  const available = factors.filter((f) => f.available)
  const missing = factors.filter((f) => !f.available)

  const weightAvailable = available.reduce((acc, f) => acc + (f.weight ?? 0), 0)
  const weightMissing = missing.reduce((acc, f) => acc + (f.weight ?? 0), 0)
  const weightTotal = weightAvailable + weightMissing

  const top = sortFactors(available)[0]

  return (
    <div className="space-y-5">
      <PageIntro
        question="¿Por qué? — Match Explainability"
        title={`${text(match.nike_product?.product_name)} vs ${text(match.competitor_product?.product_name)}`}
        description="No alcanza con decir “match = 89%”. Acá está la feature importance completa: qué factor sostiene el score, cuánto pesa cada uno y —sobre todo— qué factores no tenían datos y quedaron fuera del cálculo."
      />

      {/* ── Encabezado del match ─────────────────────────────────── */}
      <Card>
        <div className="grid items-center gap-5 lg:grid-cols-[1fr_auto_1fr]">
          <div>
            <p className="mb-1.5 text-label font-bold uppercase text-nike-red">Producto Nike</p>
            <ProductLine product={match.nike_product} role="nike" />
          </div>

          <div className="flex flex-col items-center gap-2 border-y border-surface-border py-4 lg:border-x lg:border-y-0 lg:px-8 lg:py-0">
            <span
              className="tabular text-metric-xl font-black leading-none"
              style={{ color: scoreTone(match.match_score) }}
            >
              {score(match.match_score)}
              <span className="text-2xl">%</span>
            </span>
            <span className="label-caps">Match score</span>
            <ConfidenceBadge confidence={match.confidence} coverage={match.coverage} />
          </div>

          <div>
            <p className="label-caps mb-1.5">Competidor</p>
            <ProductLine product={match.competitor_product} role="competitor" />
          </div>
        </div>
      </Card>

      {/* ── Cobertura: la honestidad primero ─────────────────────── */}
      <Card>
        <SectionHeader
          eyebrow="Cobertura de la evidencia"
          title="Con cuánta información se calculó este score"
          subtitle="Un factor sin datos no vale cero: se excluye y su peso se redistribuye entre los que sí tienen datos."
          hint="Por eso mostramos siempre qué quedó adentro y qué quedó afuera del cálculo."
          className="mb-4"
        />

        <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
          <div>
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="label-caps">Peso con datos reales</span>
              <span className="tabular text-sm font-bold text-nike-ink">
                {pctFromFraction(
                  match.coverage ?? (weightTotal > 0 ? weightAvailable / weightTotal : null),
                  0,
                )}
              </span>
            </div>
            <div className="flex h-4 w-full gap-[2px] overflow-hidden rounded-sm">
              <div
                className="bg-[#256ABF]"
                style={{ width: `${weightTotal > 0 ? (weightAvailable / weightTotal) * 100 : 0}%` }}
                title="Peso de los factores con datos"
              />
              <div
                className="hatch-muted"
                style={{ width: `${weightTotal > 0 ? (weightMissing / weightTotal) * 100 : 100}%` }}
                title="Peso de los factores sin datos, redistribuido"
              />
            </div>
            <div className="mt-2 flex gap-4 text-2xs">
              <span className="flex items-center gap-1.5 text-nike-ink-soft">
                <span
                  aria-hidden="true"
                  className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#256ABF]"
                />
                {available.length} factor(es) con datos
              </span>
              <span className="flex items-center gap-1.5 text-nike-muted">
                <span
                  aria-hidden="true"
                  className="hatch-muted inline-block h-2.5 w-2.5 rounded-[2px]"
                />
                {missing.length} sin datos, excluido(s)
              </span>
            </div>

            <div className="mt-4 rounded-lg border border-surface-border bg-surface-muted p-3">
              <p className="text-2xs leading-relaxed text-nike-ink-soft">
                <span className="font-semibold text-nike-ink">Cómo se calcula: </span>
                score = 100 × Σ(peso<sub>i</sub> × score<sub>i</sub>) ⁄ Σ(peso<sub>i</sub>) sobre los
                factores <span className="font-semibold">disponibles</span>. La confianza se deriva
                de la cobertura: cuanto menor la cobertura, menor la confianza declarada.
              </p>
            </div>
          </div>

          <div>
            <p className="label-caps mb-2">Reparto del score entre factores disponibles</p>
            <ContributionStack factors={factors} height={26} />
            <FactorLegend factors={factors} />
            {top ? (
              <p className="mt-3 text-xs leading-relaxed text-nike-ink-soft">
                El factor que más sostiene este match es{' '}
                <span className="font-bold text-nike-ink">{factorLabel(top.factor)}</span>, con{' '}
                <span className="tabular font-bold text-nike-ink">{pct(top.contribution, 1)}</span>{' '}
                del score.
              </p>
            ) : (
              <p className="mt-3 text-xs italic text-nike-muted">
                Ningún factor aportó evidencia: el score no es interpretable.
              </p>
            )}
          </div>
        </div>
      </Card>

      {/* ── Feature importance ───────────────────────────────────── */}
      <Card>
        <SectionHeader
          eyebrow="¿Por qué?"
          title="Feature importance de los 7 factores"
          subtitle="Tocá una fila para ver las sub-señales que produjeron ese score."
          hint="Los factores sin datos se listan igual — la ausencia de evidencia es información."
          className="mb-3"
        />
        <FactorTable factors={factors} configuredWeights={match.configured_weights} />
      </Card>

      {/* ── Configurado vs efectivo ──────────────────────────────── */}
      <Card>
        <SectionHeader
          eyebrow="Transparencia del modelo"
          title="Peso configurado vs. contribución efectiva"
          subtitle="El peso configurado sale de config/weights.yaml."
          hint="La contribución efectiva es lo que ese factor terminó aportando después de excluir los factores sin datos y renormalizar."
          className="mb-3"
        />
        <WeightComparison factors={factors} configuredWeights={match.configured_weights} />
      </Card>

      {/* ── Factores excluidos ───────────────────────────────────── */}
      {missing.length > 0 && (
        <Card>
          <SectionHeader
            eyebrow="Límites del análisis"
            title="Qué no sabemos de este par"
            subtitle="Estos factores no tenían datos. Si aportaran evidencia, el score podría moverse en cualquier dirección."
            className="mb-3"
          />
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {missing.map((f) => (
              <li
                key={f.factor}
                className="rounded-lg border border-dashed border-surface-border-strong bg-surface-muted p-3"
              >
                <p className="flex items-center gap-2 text-xs font-semibold text-nike-ink">
                  <span
                    aria-hidden="true"
                    className="hatch-muted inline-block h-3 w-3 rounded-[2px]"
                  />
                  {factorLabel(f.factor)}
                </p>
                <p className="tabular mt-1 text-2xs text-nike-muted">
                  peso configurado{' '}
                  {pctFromFraction(match.configured_weights?.[f.factor] ?? f.weight, 0)} —
                  redistribuido
                </p>
                {typeof f.detail?.['reason'] === 'string' && (
                  <p className="mt-1 text-2xs italic text-nike-ink-soft">{f.detail['reason']}</p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

interface WeightDatum {
  factor: string
  label: string
  configurado: number
  efectivo: number
  available: boolean
}

function WeightComparison({
  factors,
  configuredWeights,
}: {
  factors: Factor[]
  configuredWeights: Record<string, number>
}) {
  const data: WeightDatum[] = sortFactors(factors).map((f) => ({
    factor: f.factor,
    label: factorLabel(f.factor),
    configurado: (configuredWeights?.[f.factor] ?? f.weight ?? 0) * 100,
    efectivo: f.available ? (f.contribution ?? 0) : 0,
    available: f.available,
  }))

  if (data.length === 0) {
    return (
      <EmptyState
        title="Sin factores"
        description="No hay factores persistidos para comparar contra la configuración."
        size="sm"
      />
    )
  }

  return (
    <div>
      <div className="h-[280px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} stroke={CHART_INK.grid} />
            <XAxis
              type="number"
              domain={[0, 'dataMax']}
              tick={{ fontSize: 10, fill: CHART_INK.axis }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
              unit="%"
            />
            <YAxis
              type="category"
              dataKey="label"
              width={120}
              tick={{ fontSize: 11, fill: CHART_INK.axisStrong }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
            />
            <RechartsTooltip
              cursor={{ fill: CHART_INK.cursor }}
              contentStyle={{
                fontSize: 11,
                borderRadius: 6,
                border: `1px solid ${CHART_INK.line}`,
                boxShadow: '0 8px 24px rgba(17,17,17,0.10)',
              }}
              formatter={(value: number, name: string) => [`${value.toFixed(1)}%`, name]}
            />
            <Bar
              dataKey="configurado"
              name="Peso configurado"
              fill="#C9C9C3"
              radius={[0, 3, 3, 0]}
              barSize={9}
            />
            <Bar dataKey="efectivo" name="Contribución efectiva" radius={[0, 3, 3, 0]} barSize={9}>
              {data.map((d) => (
                <Cell key={d.factor} fill={d.available ? factorColor(d.factor) : UNAVAILABLE_COLOR} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Leyenda propia: el color de la barra efectiva es el del factor, no una serie. */}
      <ul className="mt-1 flex flex-wrap gap-x-5 gap-y-1 pl-[128px] text-2xs text-nike-ink-soft">
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#C9C9C3]" />
          Peso configurado en weights.yaml
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-flex gap-[2px]">
            <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-[#2A78D6]" />
            <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-[#1BAF7A]" />
            <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-[#EDA100]" />
          </span>
          Contribución efectiva (color del factor)
        </li>
      </ul>

      {/* Vista de tabla: el color nunca es el único canal. */}
      <div className="nike-table-wrap mt-4">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-surface-border text-left text-label uppercase text-nike-muted">
              <th className="py-1.5 pr-3 font-semibold">Factor</th>
              <th className="py-1.5 pr-3 text-right font-semibold">Peso configurado</th>
              <th className="py-1.5 pr-3 text-right font-semibold">Contribución efectiva</th>
              <th className="py-1.5 text-right font-semibold">Estado</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {data.map((d) => (
              <tr key={d.factor} className={d.available ? '' : 'text-nike-muted'}>
                <td className="py-1.5 pr-3 font-medium">{d.label}</td>
                <td className="tabular py-1.5 pr-3 text-right">{d.configurado.toFixed(0)}%</td>
                <td className="tabular py-1.5 pr-3 text-right font-semibold">
                  {d.available ? `${d.efectivo.toFixed(1)}%` : '—'}
                </td>
                <td className="py-1.5 text-right text-2xs">
                  {d.available ? 'con datos' : 'sin datos · excluido'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
