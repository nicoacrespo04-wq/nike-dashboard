import { cn } from '@/lib/utils'
import { InfoTip } from './Tooltip'

export interface SectionHeaderProps {
  /**
   * Línea corta en rojo sobre el título. La usan las pantallas de
   * INTELLIGENCE para declarar qué pregunta responde la sección
   * ("¿Quién compite?", "¿Cuánto importa?").
   */
  eyebrow?: string
  /** Título de la sección. */
  title: string
  /** Bajada: qué muestra esta sección o cómo leerla. */
  subtitle?: string
  /** Explicación de la metodología (aparece en un tooltip "i"). */
  hint?: React.ReactNode
  /** Punto de color a la izquierda del título (marca, serie). */
  accentColor?: string
  /** Métrica secundaria alineada a la derecha (ej. "128 franchises"). */
  meta?: React.ReactNode
  /** Controles a la derecha (tabs, botones). */
  actions?: React.ReactNode
  /** Nivel semántico del encabezado. */
  as?: 'h1' | 'h2' | 'h3'
  size?: 'sm' | 'md'
  className?: string
}

/**
 * Encabezado de sección.
 *
 * Unifica el patrón repetido en las páginas (`<h2 class="font-bold text-sm
 * uppercase…"> + <p class="text-xs text-gray-400">`) en un solo componente con
 * jerarquía consistente y soporte de tooltip metodológico.
 */
export default function SectionHeader({
  eyebrow,
  title,
  subtitle,
  hint,
  accentColor,
  meta,
  actions,
  as: Heading = 'h2',
  size = 'md',
  className,
}: SectionHeaderProps) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        {eyebrow && (
          <p className="mb-1 text-label font-bold uppercase text-nike-red">{eyebrow}</p>
        )}
        <Heading
          className={cn(
            'flex items-center gap-2 font-bold uppercase tracking-wide text-nike-ink',
            size === 'md' ? 'text-sm' : 'text-label',
          )}
        >
          {accentColor && (
            <span
              className="inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full"
              style={{ background: accentColor }}
              aria-hidden="true"
            />
          )}
          <span className="truncate">{title}</span>
          {hint && <InfoTip content={hint} label={`Cómo se calcula: ${title}`} side="bottom" />}
        </Heading>
        {subtitle && (
          <p className="mt-0.5 text-xs leading-relaxed text-nike-muted">{subtitle}</p>
        )}
      </div>

      {(meta || actions) && (
        <div className="flex flex-shrink-0 items-center gap-3">
          {meta && <span className="text-xs tabnum text-nike-muted">{meta}</span>}
          {actions}
        </div>
      )}
    </div>
  )
}
