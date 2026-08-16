import type { Factor, MatchDetail } from '@/types/intelligence'
import { pct, pctFromFraction, score, text } from '@/lib/format'
import { factorLabel, scoreTone, sortFactors } from '@/components/charts/palette'
import { Card, PageIntro, SectionHeader } from '@/components/ui'
import { ConfidenceBadge } from '@/components/intelligence/badges'
import {
  ContributionStack,
  FactorLegend,
  FactorTable,
} from '@/components/intelligence/FactorBreakdown'
import { ProductLine } from '@/components/intelligence/ProductLine'
import WeightComparison from './WeightComparison'

/**
 * Cuerpo del Match Explainability.
 *
 * Server Component: es una explicación cerrada de un match ya calculado, sin un
 * solo control que el usuario pueda mover. Lo único que baja al browser es la
 * tabla plegable de factores y el gráfico de recharts.
 */
export default function MatchExplain({ match }: { match: MatchDetail }) {
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
