'use client'

import type { GlossaryTerm } from '@/types/intelligence'
import { pctFromFraction } from '@/lib/format'
import { isDefined } from '@/lib/intelligence/glossary'
import { InfoTip } from '@/components/ui'

/**
 * El "¿qué mide esto?" de un factor, colgado del ícono `i` de siempre.
 *
 * La explicabilidad es la razón de ser del producto: un factor con su
 * contribución al lado no dice nada si el usuario no sabe qué variable es. Cada
 * término contesta las tres preguntas que hacen falta para juzgar si el score
 * tiene sentido — qué mide, con qué datos se calcula y cómo se lee un valor
 * alto o bajo — y el peso configurado cierra el círculo con `weights.yaml`.
 *
 * Si el backend no publicó el término (contrato viejo), no se renderiza nada:
 * un tooltip vacío es peor que ninguno.
 */
export function GlossaryTip({
  term,
  side = 'top',
  size = 12,
}: {
  term: GlossaryTerm | null | undefined
  side?: 'top' | 'bottom' | 'left' | 'right'
  size?: number
}) {
  if (!isDefined(term ?? null) || !term) return null

  return (
    <InfoTip
      label={`Qué mide ${term.label}`}
      side={side}
      size={size}
      className="align-middle"
      content={
        <span className="block space-y-1.5">
          <span className="block text-[11px] font-bold">{term.label}</span>
          <span className="block">{term.definition}</span>
          {term.data && (
            <span className="block text-nike-faint">
              <span className="font-semibold">Datos: </span>
              {term.data}
            </span>
          )}
          {term.high && <span className="block">{term.high}</span>}
          {term.low && <span className="block">{term.low}</span>}
          {typeof term.weight === 'number' && (
            <span className="block text-nike-faint">
              Peso configurado: {pctFromFraction(term.weight, 0)} (config/weights.yaml)
            </span>
          )}
        </span>
      }
    />
  )
}

/**
 * Bajada de una familia completa del glosario, para encabezar una tabla de
 * factores: dice de entrada cómo se lee el bloque entero.
 */
export function GlossaryGroupNote({ description }: { description: string | null | undefined }) {
  if (!description) return null
  return <p className="text-2xs leading-relaxed text-nike-ink-soft">{description}</p>
}
