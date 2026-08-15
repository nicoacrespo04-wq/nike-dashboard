import { cn } from '@/lib/utils'

export interface SkeletonProps {
  className?: string
  /** Ancho CSS (ej. `'60%'`, `120`). Si se omite, ocupa el ancho disponible. */
  width?: string | number
  /** Alto CSS. Por defecto lo define la clase (`h-3`). */
  height?: string | number
  /** Forma del placeholder. */
  variant?: 'line' | 'block' | 'circle'
  /** Etiqueta para lectores de pantalla (solo en el contenedor raíz). */
  label?: string
}

/**
 * Placeholder de carga.
 *
 * Se anuncia como `aria-busy` y `aria-hidden` para que los lectores de pantalla
 * no lean cajas vacías; el contenedor que lo usa debe exponer el `aria-live`.
 */
export function Skeleton({ className, width, height, variant = 'line', label }: SkeletonProps) {
  return (
    <div
      className={cn(
        'skeleton',
        variant === 'line' && 'h-3 rounded',
        variant === 'block' && 'h-full rounded-lg',
        variant === 'circle' && 'rounded-full aspect-square',
        className,
      )}
      style={{ width, height }}
      aria-hidden={label ? undefined : true}
      role={label ? 'status' : undefined}
      aria-label={label}
    />
  )
}

export interface SkeletonTextProps {
  /** Cantidad de renglones. */
  lines?: number
  className?: string
  /** Ancho del último renglón, para que no se vea un bloque perfecto. */
  lastLineWidth?: string
}

/** Varios renglones de texto simulado. */
export function SkeletonText({ lines = 3, className, lastLineWidth = '60%' }: SkeletonTextProps) {
  return (
    <div className={cn('flex flex-col gap-2', className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? lastLineWidth : '100%'} />
      ))}
    </div>
  )
}

export interface SkeletonChartProps {
  height?: number
  className?: string
  variant?: 'bars' | 'donut'
}

/**
 * Silueta de un gráfico cargando. Imita la forma final (barras o dona) para que
 * el layout no salte cuando llegan los datos.
 */
export function SkeletonChart({ height = 240, className, variant = 'bars' }: SkeletonChartProps) {
  if (variant === 'donut') {
    return (
      <div
        className={cn('flex items-center justify-center', className)}
        style={{ height }}
        aria-hidden="true"
      >
        <Skeleton variant="circle" height={height * 0.8} width={height * 0.8} />
      </div>
    )
  }

  const widths = ['92%', '78%', '70%', '61%', '54%', '44%', '36%', '28%']
  return (
    <div
      className={cn('flex flex-col justify-center gap-2.5', className)}
      style={{ height }}
      aria-hidden="true"
    >
      {widths.map((w, i) => (
        <Skeleton key={i} width={w} className="h-4 rounded-sm" />
      ))}
    </div>
  )
}

export default Skeleton
