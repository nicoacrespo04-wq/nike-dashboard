'use client'

import { useState } from 'react'
import type { Factor } from '@/types/intelligence'
import { dec, pct, pctFromFraction } from '@/lib/format'
import { type GlossaryTerms, termFor } from '@/lib/intelligence/glossary'
import { factorColor, factorHelp, factorLabel, sortFactors } from '@/components/charts/palette'
import { EmptyState, MeterBar } from '@/components/ui'
import { GlossaryTip } from './GlossaryTip'

/** Formatea un valor del JSON `detail` sin recurrir a `any`. */
function renderDetailValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  if (typeof value === 'boolean') return value ? 'sí' : 'no'
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(renderDetailValue).join(', ')
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${renderDetailValue(v)}`)
      .join(' · ')
  }
  return String(value)
}

/**
 * Barra apilada al 100%: cómo se reparte el score entre los factores
 * DISPONIBLES. Separación de 2px entre segmentos y etiqueta directa en los
 * tramos anchos.
 */
export function ContributionStack({
  factors,
  height = 22,
  showLabels,
}: {
  factors: Factor[]
  height?: number
  showLabels?: boolean
}) {
  // Las etiquetas directas sólo entran si la barra es lo bastante alta.
  const withLabels = showLabels ?? height >= 14
  const available = factors.filter((f) => f.available && (f.contribution ?? 0) > 0)
  const total = available.reduce((acc, f) => acc + (f.contribution ?? 0), 0)

  if (available.length === 0 || total <= 0) {
    return (
      <div
        className="hatch-muted w-full rounded-sm"
        style={{ height }}
        title="Ningún factor tuvo datos suficientes"
      />
    )
  }

  const ordered = sortFactors(available)

  return (
    <div className="flex w-full gap-[2px] overflow-hidden rounded-sm" style={{ height }}>
      {ordered.map((f) => {
        const share = ((f.contribution ?? 0) / total) * 100
        return (
          <div
            key={f.factor}
            className="relative flex items-center justify-center"
            style={{ width: `${share}%`, backgroundColor: factorColor(f.factor) }}
            title={`${factorLabel(f.factor)}: ${pct(f.contribution, 1)} del score`}
          >
            {withLabels && share >= 12 && (
              <span className="tabular px-1 text-2xs font-bold text-white drop-shadow-sm">
                {share.toFixed(0)}%
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function FactorLegend({ factors, terms }: { factors: Factor[]; terms?: GlossaryTerms }) {
  const ordered = sortFactors(factors)
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
      {ordered.map((f) => (
        <li key={f.factor} className="flex items-center gap-1.5 text-2xs">
          <span
            aria-hidden="true"
            className={`inline-block h-2.5 w-2.5 rounded-[2px] ${f.available ? '' : 'hatch-muted'}`}
            style={{ backgroundColor: f.available ? factorColor(f.factor) : undefined }}
          />
          <span className={f.available ? 'text-nike-ink-soft' : 'text-nike-muted line-through'}>
            {factorLabel(f.factor)}
          </span>
          {terms && <GlossaryTip term={termFor(terms, f.factor)} size={11} />}
          <span className="tabular font-semibold text-nike-ink">
            {f.available ? pct(f.contribution, 0) : 's/d'}
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Feature importance completa: una fila por factor, ordenada por contribución.
 * Los factores sin datos se muestran explícitamente — la honestidad sobre la
 * cobertura es parte del producto, no se ocultan.
 */
export function FactorTable({
  factors,
  configuredWeights,
  terms,
}: {
  factors: Factor[]
  configuredWeights?: Record<string, number>
  /**
   * Glosario del backend. Con él cada fila explica qué mide el factor —dentro
   * del tooltip del encabezado y en prosa al abrir la fila—; sin él la tabla
   * cae al texto corto de `factorHelp`, que es lo único que había antes.
   */
  terms?: GlossaryTerms
}) {
  const [open, setOpen] = useState<string | null>(null)

  if (factors.length === 0) {
    return (
      <EmptyState
        title="Sin desglose de factores"
        description="Este match no tiene factores persistidos. La etapa de matching todavía no guardó la explicabilidad."
      />
    )
  }

  const ordered = sortFactors(factors)
  const maxContribution = Math.max(...ordered.map((f) => f.contribution ?? 0), 1)
  const availableCount = ordered.filter((f) => f.available).length

  return (
    <div>
      <div className="grid grid-cols-[minmax(140px,1.4fr)_minmax(90px,0.9fr)_minmax(150px,1.6fr)] items-center gap-3 border-b border-surface-border pb-2 text-label font-semibold uppercase text-nike-muted">
        <span>Factor</span>
        <span className="text-right">Score bruto</span>
        <span>Contribución al match</span>
      </div>

      <ul className="divide-y divide-surface-border">
        {ordered.map((f) => {
          const isOpen = open === f.factor
          const detailEntries = Object.entries(f.detail ?? {})
          const configured = configuredWeights?.[f.factor]
          return (
            <li key={f.factor} className={f.available ? '' : 'bg-surface-muted'}>
              {/* La fila no es un `<button>` entero: el ícono del glosario ya es
                  un botón y anidar botones es HTML inválido. El disparador es el
                  nombre del factor. */}
              <div className="grid w-full grid-cols-[minmax(140px,1.4fr)_minmax(90px,0.9fr)_minmax(150px,1.6fr)] items-center gap-3 py-2.5 text-left">
                {/* Identidad del factor */}
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    aria-hidden="true"
                    className={`inline-block h-3 w-3 flex-shrink-0 rounded-[2px] ${f.available ? '' : 'hatch-muted'}`}
                    style={{ backgroundColor: f.available ? factorColor(f.factor) : undefined }}
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setOpen(isOpen ? null : f.factor)}
                        aria-expanded={isOpen}
                        className={`min-w-0 truncate text-left text-xs font-semibold transition-colors duration-fast hover:underline ${
                          f.available ? 'text-nike-ink' : 'text-nike-muted'
                        }`}
                      >
                        {factorLabel(f.factor)}
                      </button>
                      {terms && <GlossaryTip term={termFor(terms, f.factor)} size={12} />}
                    </span>
                    <span className="tabular block text-2xs text-nike-muted">
                      peso {dec((configured ?? f.weight ?? 0) * 100, 0)}%
                    </span>
                  </span>
                </span>

                {/* Score bruto 0..1 */}
                <span className="text-right">
                  {f.available ? (
                    <span className="tabular text-xs font-semibold text-nike-ink">
                      {pctFromFraction(f.raw_score, 0)}
                    </span>
                  ) : (
                    <span className="text-2xs font-medium text-nike-muted">sin datos</span>
                  )}
                </span>

                {/* Contribución */}
                <span className="flex items-center gap-2">
                  <span className="flex-1">
                    <MeterBar
                      value={f.available ? f.contribution : maxContribution}
                      max={maxContribution}
                      color={factorColor(f.factor)}
                      hatched={!f.available}
                      height={10}
                    />
                  </span>
                  <span
                    className={`tabular w-14 flex-shrink-0 text-right text-xs font-bold ${
                      f.available ? 'text-nike-ink' : 'text-nike-muted'
                    }`}
                  >
                    {f.available ? pct(f.contribution, 1) : 'excluido'}
                  </span>
                </span>
              </div>

              {!f.available && (
                <p className="pb-2.5 pl-5 text-2xs italic leading-relaxed text-nike-muted">
                  Sin datos — excluido del cálculo y su peso se redistribuyó entre los{' '}
                  {availableCount} factores disponibles.
                  {typeof f.detail?.['reason'] === 'string' ? ` Motivo: ${f.detail['reason']}` : ''}
                </p>
              )}

              {isOpen && (
                <div className="border-l-2 border-surface-border pb-3 pl-4 text-2xs leading-relaxed text-nike-ink-soft">
                  <FactorMeaning factor={f.factor} terms={terms} />
                  {detailEntries.length > 0 ? (
                    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                      {detailEntries.map(([key, value]) => (
                        <div key={key} className="contents">
                          <dt className="font-mono text-nike-muted">{key}</dt>
                          <dd className="tabular text-nike-ink">{renderDetailValue(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="text-nike-muted">Sin sub-señales registradas para este factor.</p>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * Qué mide un factor, en prosa, dentro de la fila abierta.
 *
 * El glosario del backend contesta tres cosas —qué mide, con qué datos, cómo se
 * lee alto y bajo— y es la misma definición que publica `docs/glossary.md`. Sin
 * glosario cae al texto corto que ya existía en la paleta.
 */
function FactorMeaning({ factor, terms }: { factor: string; terms?: GlossaryTerms }) {
  const term = termFor(terms, factor)

  if (!term || term.definition.trim() === '') {
    return <p className="mb-1.5">{factorHelp(factor)}</p>
  }

  return (
    <div className="mb-2 space-y-1">
      <p className="font-semibold text-nike-ink">{term.definition}</p>
      {term.data && (
        <p>
          <span className="font-semibold text-nike-ink">Con qué datos: </span>
          {term.data}
        </p>
      )}
      {term.high && <p className="text-nike-muted">{term.high}</p>}
      {term.low && <p className="text-nike-muted">{term.low}</p>}
    </div>
  )
}

/** Versión micro para listas: la silueta del reparto, sin números. */
export function FactorSparkStack({ factors }: { factors: Factor[] }) {
  if (factors.length === 0) return null
  return (
    <div className="w-full">
      <ContributionStack factors={factors} height={6} />
    </div>
  )
}
