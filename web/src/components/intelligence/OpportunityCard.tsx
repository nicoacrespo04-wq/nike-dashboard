'use client'

import { useState } from 'react'
import Link from 'next/link'
import type { Opportunity } from '@/types/intelligence'
import { score, text } from '@/lib/format'
import { humanize, severityStyle } from '@/components/charts/palette'
import { MeterBar } from '@/components/ui'
import { ConfidenceBadge, FamilyBadge, SeverityBadge } from './badges'
import { DriverList } from './DriverList'
import { ProductLine } from './ProductLine'
import {
  TRIAGE_STYLES,
  TriageBadge,
  TriageControls,
  defaultTriage,
  type TriageRecord,
} from './TriageControls'

/**
 * Oportunidad enriquecida con su triaje.
 *
 * Los dos campos son opcionales a propósito: el backend los adjunta cuando
 * `app/services/triage.py::apply_to` está enganchado en el serializer, y la
 * tarjeta tiene que seguir funcionando igual si todavía no lo está.
 */
export interface TriagedOpportunity extends Opportunity {
  /** Hash estable de la oportunidad. Es la ANCLA del triaje: `id` no sobrevive al pipeline. */
  entity_key?: string
  triage?: TriageRecord | null
}

/**
 * Una oportunidad = las cuatro preguntas en una tarjeta:
 *   QUÉ PASA (título + descripción)  ·  CUÁNTO IMPORTA (business importance)
 *   POR QUÉ (drivers)                ·  QUÉ HACER (recomendación)
 *
 * Y abajo la quinta, que es la que hace que la pantalla se siga usando en dos
 * semanas: QUÉ HICIMOS (triaje). Una oportunidad descartada o pospuesta se ve
 * distinta — atenuada y con su etiqueta — para que la bandeja muestre trabajo,
 * no sólo output del motor.
 */
export function OpportunityCard({
  opportunity,
  compact = false,
  showTriage,
  updatedBy,
  onTriageChange,
}: {
  opportunity: TriagedOpportunity
  compact?: boolean
  /**
   * Controles de triaje. Por defecto siguen a `compact`: en el resumen del
   * overview la tarjeta muestra el estado pero no se opera desde ahí — el lugar
   * para triajear es el Opportunity Center.
   */
  showTriage?: boolean
  /** Quién opera, para la auditoría del backend. */
  updatedBy?: string
  /** Avisa al listado para que recalcule filtros/contadores. */
  onTriageChange?: (record: TriageRecord) => void
}) {
  const sev = severityStyle(opportunity.severity)
  const rec = opportunity.recommendation
  const entityKey = opportunity.entity_key ?? null
  const withTriage = showTriage ?? !compact

  // El triaje se refleja acá apenas cambia (el componente ya revirtió si falló).
  const [triage, setTriage] = useState<TriageRecord | null>(opportunity.triage ?? null)
  const current = triage ?? opportunity.triage ?? (entityKey ? defaultTriage(entityKey) : null)
  const triaged = current !== null && current.state !== 'open'
  const style = current ? TRIAGE_STYLES[current.state] : null

  const handleChange = (record: TriageRecord) => {
    setTriage(record)
    onTriageChange?.(record)
  }

  return (
    <article
      className={[
        'flex flex-col overflow-hidden rounded-card border border-surface-border bg-white shadow-card',
        'transition-opacity duration-fast',
        triaged ? 'opacity-70 hover:opacity-100' : '',
      ].join(' ')}
      style={{ borderTop: `3px solid ${triaged ? '#C9C9C2' : sev.color}` }}
      data-triage-state={current?.state ?? 'open'}
    >
      {/* QUÉ PASA */}
      <div className="p-4">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <SeverityBadge severity={opportunity.severity} />
          <FamilyBadge family={opportunity.family} />
          <ConfidenceBadge confidence={opportunity.confidence} />
          {current && triaged && <TriageBadge triage={current} />}
        </div>

        <h3
          className={[
            'text-sm font-bold leading-snug text-nike-ink',
            current?.state === 'dismissed' ? 'line-through decoration-nike-faint' : '',
          ].join(' ')}
        >
          {opportunity.title}
        </h3>
        <p className="mt-1 text-2xs uppercase tracking-wide text-nike-muted">
          {humanize(opportunity.opportunity_type)}
          {opportunity.retailer ? ` · ${opportunity.retailer.name}` : ''}
          {opportunity.country_code ? ` · ${opportunity.country_code}` : ''}
        </p>
        {opportunity.description && (
          <p className="mt-2 text-xs leading-relaxed text-nike-ink-soft">{opportunity.description}</p>
        )}
      </div>

      {/* CUÁNTO IMPORTA */}
      <div className="border-t border-surface-border bg-surface-muted px-4 py-3">
        <div className="flex items-baseline justify-between">
          <span className="label-caps">Business Importance</span>
          <span className="tabular text-lg font-bold leading-none" style={{ color: sev.color }}>
            {score(opportunity.business_importance)}
            <span className="text-2xs font-medium text-nike-muted"> /100</span>
          </span>
        </div>
        <div className="mt-1.5">
          <MeterBar value={opportunity.business_importance} max={100} color={sev.color} height={6} />
        </div>
      </div>

      {!compact && (
        <>
          {/* Sujetos */}
          {(opportunity.nike_product || opportunity.competitor_product) && (
            <div className="grid gap-3 border-t border-surface-border px-4 py-3 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-nike-muted">
                  Nike
                </p>
                <ProductLine product={opportunity.nike_product} role="nike" compact />
              </div>
              <div>
                <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-nike-muted">
                  Competidor
                </p>
                <ProductLine product={opportunity.competitor_product} role="competitor" compact />
              </div>
            </div>
          )}

          {/* POR QUÉ */}
          <div className="border-t border-surface-border px-4 py-3">
            <p className="label-caps mb-2">Por qué — drivers del score</p>
            <DriverList drivers={opportunity.drivers} color={sev.color} />
          </div>
        </>
      )}

      {/* QUÉ HACER */}
      <div className="mt-auto border-t-2 border-surface-border-strong bg-surface-muted px-4 py-3">
        <p className="mb-1 text-label font-bold uppercase text-nike-red">Acción recomendada</p>
        {rec && rec.action ? (
          <>
            <p className="text-xs font-bold leading-snug text-nike-ink">{humanize(rec.action)}</p>
            {rec.rationale && (
              <p className="mt-1 text-2xs leading-relaxed text-nike-ink-soft">{rec.rationale}</p>
            )}
          </>
        ) : (
          <p className="text-2xs italic text-nike-muted">
            El motor todavía no generó una recomendación asociada.
          </p>
        )}
        {!withTriage && triaged && style && (
          <p className="mt-2 text-2xs font-semibold text-nike-muted">
            {style.label} por el equipo{current?.assignee ? ` · ${current.assignee}` : ''}
          </p>
        )}
      </div>

      {/* QUÉ HICIMOS */}
      {withTriage && (
        <TriageControls
          entityKey={entityKey}
          triage={current}
          onChange={handleChange}
          updatedBy={updatedBy}
        />
      )}

      {opportunity.nike_product && (
        <Link
          href={`/intelligence/matches?product=${opportunity.nike_product.id}`}
          className="border-t border-surface-border px-4 py-2 text-2xs font-semibold text-nike-red transition-colors duration-fast hover:bg-surface-muted"
        >
          Ver competidores de {text(opportunity.nike_product.product_name)} →
        </Link>
      )}
    </article>
  )
}
