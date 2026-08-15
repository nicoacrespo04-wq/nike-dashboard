import { SearchX } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface EmptyStateProps {
  /** Qué pasó, en una línea. Ej: "Sin resultados con los filtros actuales". */
  title: string
  /** Qué puede hacer el usuario al respecto. */
  description?: string
  /** Ícono lucide. Por defecto una lupa tachada. */
  icon?: React.ReactNode
  /** Acción primaria (ej. "Limpiar filtros"). */
  action?: React.ReactNode
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const SIZE = {
  sm: { pad: 'py-6', title: 'text-sm', icon: 'h-8 w-8' },
  md: { pad: 'py-12', title: 'text-sm', icon: 'h-10 w-10' },
  lg: { pad: 'py-20', title: 'text-base', icon: 'h-12 w-12' },
}

/**
 * Estado vacío.
 *
 * Un estado vacío bien hecho explica *por qué* está vacío y qué hacer; nunca
 * es una caja en blanco. Se usa en tablas, gráficos y grillas.
 */
export default function EmptyState({
  title,
  description,
  icon,
  action,
  size = 'md',
  className,
}: EmptyStateProps) {
  const s = SIZE[size]
  return (
    <div
      className={cn('flex flex-col items-center justify-center text-center gap-2 px-4', s.pad, className)}
      role="status"
    >
      <div
        className={cn(
          'flex items-center justify-center rounded-full bg-surface-sunken text-nike-faint',
          s.icon,
        )}
        aria-hidden="true"
      >
        {icon ?? <SearchX size={size === 'sm' ? 16 : 20} strokeWidth={1.8} />}
      </div>
      <p className={cn('font-semibold text-nike-ink-soft', s.title)}>{title}</p>
      {description && (
        <p className="text-xs text-nike-muted max-w-sm leading-relaxed">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
