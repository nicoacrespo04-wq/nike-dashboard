/**
 * Etiquetas del motor de decisión.
 *
 * Todas se construyen sobre el `Badge` del dashboard (mismo tamaño, mismo
 * radio, mismo tracking que los badges BEAT/MEET/LOSE de las solapas de
 * retail). Lo único que aportan es el color semántico, que sale de la paleta
 * compartida — nunca de un hex suelto.
 *
 * El color nunca carga el significado solo: siempre hay ícono y texto.
 */

import { Badge } from '@/components/ui'
import { confidenceStyle, familyLabel, severityStyle } from '@/components/charts/palette'

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  const style = severityStyle(severity)
  const key = severity?.toUpperCase()
  const icon = key === 'CRITICAL' || key === 'HIGH' ? '▲' : key === 'MEDIUM' ? '◆' : '●'

  return (
    <Badge
      colors={{ text: style.text, bg: style.bg, border: style.border }}
      title={`Severidad ${style.label}`}
    >
      <span aria-hidden="true" style={{ color: style.color }}>
        {icon}
      </span>
      {style.label}
    </Badge>
  )
}

export function ConfidenceBadge({
  confidence,
  coverage,
}: {
  confidence: string | null | undefined
  /** Fracción 0..1 del peso total que tenía datos. */
  coverage?: number | null
}) {
  const style = confidenceStyle(confidence)
  const title =
    coverage !== null && coverage !== undefined
      ? `${style.label} · cobertura ${(coverage * 100).toFixed(0)}% del peso total`
      : style.label

  return (
    <Badge colors={{ text: style.text, bg: style.bg, border: style.border }} title={title}>
      <span aria-hidden="true" className="flex gap-0.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{
              backgroundColor: i < style.dots ? style.text : 'transparent',
              border: `1px solid ${style.text}`,
            }}
          />
        ))}
      </span>
      {style.label}
    </Badge>
  )
}

export function FamilyBadge({ family }: { family: string | null | undefined }) {
  return (
    <Badge tone="neutral" title="Familia de oportunidad">
      {familyLabel(family)}
    </Badge>
  )
}

const LIFECYCLE_LABELS: Record<string, string> = {
  launch: 'Lanzamiento',
  growth: 'Crecimiento',
  mature: 'Madurez',
  decline: 'Declive',
  clearance: 'Liquidación',
}

export function LifecycleBadge({ stage }: { stage: string | null | undefined }) {
  if (!stage) return null
  return (
    <Badge tone="neutral" title="Etapa del ciclo de vida">
      {LIFECYCLE_LABELS[stage] ?? stage}
    </Badge>
  )
}

/** Chip neutro para metadatos (franquicia, use case, banda de precio). */
export function Tag({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <Badge tone="neutral" title={title} className="normal-case font-medium tracking-normal">
      {children}
    </Badge>
  )
}
