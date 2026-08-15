'use client'

import { AlertTriangle, RotateCw } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ErrorStateProps {
  /** Mensaje corto y en lenguaje de negocio. */
  title?: string
  /** Explicación de qué se rompió y qué implica. */
  description?: string
  /** Detalle técnico opcional (se muestra plegado, en mono). */
  detail?: string | null
  /** Si se pasa, se muestra el botón "Reintentar". */
  onRetry?: () => void
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const SIZE = {
  sm: { pad: 'py-6', icon: 14 },
  md: { pad: 'py-12', icon: 18 },
  lg: { pad: 'py-20', icon: 22 },
}

/**
 * Estado de error.
 *
 * Diferenciado visualmente del estado vacío: acá algo falló y hay una acción
 * de recuperación. El vacío es gris/neutro, el error usa el rojo Nike.
 */
export default function ErrorState({
  title = 'No pudimos cargar estos datos',
  description = 'Revisá la conexión o volvé a intentar en unos segundos.',
  detail,
  onRetry,
  size = 'md',
  className,
}: ErrorStateProps) {
  const s = SIZE[size]
  return (
    <div
      className={cn('flex flex-col items-center justify-center text-center gap-2 px-4', s.pad, className)}
      role="alert"
    >
      <div
        className="flex items-center justify-center rounded-full bg-bml-lose-soft text-bml-lose-ink h-10 w-10"
        aria-hidden="true"
      >
        <AlertTriangle size={s.icon} strokeWidth={2} />
      </div>
      <p className="font-semibold text-sm text-nike-ink">{title}</p>
      <p className="text-xs text-nike-muted max-w-sm leading-relaxed">{description}</p>

      {detail && (
        <details className="mt-1 max-w-full">
          <summary className="text-micro uppercase tracking-wide text-nike-faint cursor-pointer hover:text-nike-muted">
            Detalle técnico
          </summary>
          <pre className="mt-1 max-w-sm overflow-x-auto rounded bg-surface-sunken px-2 py-1 text-left text-[10px] font-mono text-nike-muted">
            {detail}
          </pre>
        </details>
      )}

      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-surface-border-strong bg-white px-3 py-1.5 text-xs font-semibold text-nike-ink transition-colors duration-fast hover:border-nike-black hover:bg-gray-50"
        >
          <RotateCw size={13} aria-hidden="true" />
          Reintentar
        </button>
      )}
    </div>
  )
}
