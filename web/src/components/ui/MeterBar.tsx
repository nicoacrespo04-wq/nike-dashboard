import { cn } from '@/lib/utils'

export interface MeterBarProps {
  /** Valor a representar. `null` → barra vacía (o tramada si `hatched`). */
  value: number | null | undefined
  /** Tope de la escala. */
  max?: number
  /** Color del relleno. Sale siempre de la paleta compartida. */
  color: string
  /** Trama diagonal para "sin datos": la ausencia se ve, no se adivina. */
  hatched?: boolean
  /** Alto en px. */
  height?: number
  className?: string
}

/**
 * Barra horizontal simple.
 *
 * Se usa para contribuciones de factores, drivers, disponibilidad y volúmenes.
 * Siempre acompañada de una etiqueta numérica: el color nunca es el único
 * canal de información.
 */
export default function MeterBar({
  value,
  max = 100,
  color,
  hatched = false,
  height = 8,
  className,
}: MeterBarProps) {
  const width =
    value === null || value === undefined || !Number.isFinite(value) || max <= 0
      ? 0
      : Math.max(0, Math.min(100, (value / max) * 100))

  return (
    <div
      className={cn('w-full overflow-hidden rounded-sm bg-surface-sunken', className)}
      style={{ height }}
      role="presentation"
    >
      <div
        className={cn('h-full rounded-sm', hatched && 'hatch-muted')}
        style={{ width: `${hatched ? 100 : width}%`, backgroundColor: hatched ? undefined : color }}
      />
    </div>
  )
}
