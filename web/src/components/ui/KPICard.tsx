'use client'

import { AlertTriangle, Minus, RotateCw, TrendingDown, TrendingUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isEmptyMetric, ND } from './format'
import { InfoTip } from './Tooltip'
import { Skeleton } from './Skeleton'

/** Dirección que se considera "buena" para la métrica. */
export type DeltaDirection = 'up-is-good' | 'down-is-good' | 'neutral'

export interface KPIDelta {
  /** Variación. Si `format` es `'pct'` se interpreta como porcentaje ya escalado (12 → 12%). */
  value: number
  /** Contexto del período, ej. "vs. semana anterior". */
  label?: string
  format?: 'pct' | 'abs'
  direction?: DeltaDirection
}

export interface KPICardProps {
  /** Nombre de la métrica. */
  title: string
  /**
   * Valor a mostrar. `null`, `undefined`, `NaN` o placeholders como `'—'`,
   * `'N/D'`, `'$0'` se renderizan como estado "sin dato" explícito.
   */
  value?: string | number | null
  /** Contexto bajo el número. */
  subtitle?: string
  /** Variación vs. período anterior (▲▼ con color semántico). */
  delta?: KPIDelta
  /** @deprecated Usar `delta`. Se mantiene por compatibilidad con las páginas. */
  trend?: number
  /** @deprecated Usar `delta.label`. */
  trendLabel?: string
  /** Explicación de cómo se calcula la métrica (tooltip "i"). */
  hint?: React.ReactNode
  /** Color de acento (marca o semántica). */
  color?: string
  icon?: React.ReactNode
  loading?: boolean
  /** Mensaje de error; si viene, la card pasa a estado de error. */
  error?: string | boolean | null
  /** Handler de reintento en estado de error. */
  onRetry?: () => void
  /** Texto del estado vacío. Por defecto: "Sin datos disponibles". */
  emptyLabel?: string
  className?: string
  valueSize?: 'sm' | 'md' | 'lg'
}

type KPIState = 'loading' | 'error' | 'empty' | 'ready'

const VALUE_SIZE = {
  sm: 'text-metric-sm',
  md: 'text-metric-md',
  lg: 'text-metric-lg',
} as const

function deltaTone(value: number, direction: DeltaDirection = 'up-is-good') {
  if (direction === 'neutral' || value === 0) return 'text-nike-muted bg-surface-sunken'
  const good = direction === 'up-is-good' ? value > 0 : value < 0
  return good ? 'text-bml-beat-ink bg-bml-beat-soft' : 'text-bml-lose-ink bg-bml-lose-soft'
}

/**
 * Tarjeta de KPI con cuatro estados explícitos y visualmente distintos:
 *
 * - **loading** → skeleton con la misma silueta que el contenido final.
 * - **error**   → ícono de alerta + reintento. Nunca se confunde con "sin dato".
 * - **empty**   → `N/D` grande en gris + microcopy. Se ve *intencional*, no roto:
 *                 era el problema principal reportado (cards que quedaban en
 *                 blanco con sólo una rayita de color).
 * - **ready**   → número con jerarquía tipográfica, delta y contexto.
 *
 * El estado se infiere solo: las páginas que hoy pasan `'—'` o `'$0'` mientras
 * no hay datos obtienen el estado vacío sin cambiar una línea.
 */
export default function KPICard({
  title,
  value,
  subtitle,
  delta,
  trend,
  trendLabel,
  hint,
  color,
  icon,
  loading = false,
  error,
  onRetry,
  emptyLabel = 'Sin datos disponibles',
  className,
  valueSize = 'lg',
}: KPICardProps) {
  const state: KPIState = loading
    ? 'loading'
    : error
      ? 'error'
      : isEmptyMetric(value)
        ? 'empty'
        : 'ready'

  // Compatibilidad: `trend`/`trendLabel` siguen funcionando como delta simple.
  const effectiveDelta: KPIDelta | undefined =
    delta ?? (trend !== undefined ? { value: trend, label: trendLabel, format: 'pct' } : undefined)

  const accent = color ?? '#111111'
  const displayValue =
    typeof value === 'number' ? value.toLocaleString('es-AR') : (value ?? '')

  return (
    <article
      className={cn('nike-card relative flex min-w-0 flex-col gap-2 overflow-hidden', className)}
      aria-busy={state === 'loading'}
    >
      {/* Acento de categoría: da identidad a la card incluso sin número. */}
      <span
        className="absolute left-0 top-0 h-full w-1"
        style={{ background: state === 'ready' ? accent : '#E0E0E0' }}
        aria-hidden="true"
      />

      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-2">
        <h3 className="label-caps flex min-w-0 items-center gap-1">
          <span className="truncate">{title}</span>
          {hint && <InfoTip content={hint} label={`Cómo se calcula: ${title}`} side="bottom" />}
        </h3>
        {icon && (
          <span className="flex-shrink-0 text-nike-mid-gray" aria-hidden="true">
            {icon}
          </span>
        )}
      </div>

      {/* ── Cuerpo por estado ──────────────────────────────────────── */}
      {state === 'loading' && (
        <>
          <span className="sr-only">Cargando {title}</span>
          <Skeleton className="h-9 w-2/3 rounded-md" />
          <Skeleton className="h-3 w-4/5" />
        </>
      )}

      {state === 'error' && (
        <>
          <div className="flex items-center gap-2 text-bml-lose-ink">
            <AlertTriangle size={18} aria-hidden="true" />
            <span className="text-lg font-bold leading-none">Error</span>
          </div>
          <p className="text-xs leading-relaxed text-nike-muted">
            {typeof error === 'string' ? error : 'No se pudo calcular esta métrica.'}
          </p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-0.5 inline-flex w-fit items-center gap-1 text-xs font-semibold text-nike-red hover:underline"
            >
              <RotateCw size={12} aria-hidden="true" />
              Reintentar
            </button>
          )}
        </>
      )}

      {state === 'empty' && (
        <>
          <div
            className={cn('metric-value leading-none text-nike-mid-gray', VALUE_SIZE[valueSize])}
            title={emptyLabel}
          >
            {ND}
          </div>
          <p className="text-xs text-nike-muted">{emptyLabel}</p>
        </>
      )}

      {state === 'ready' && (
        <>
          <div
            className={cn('metric-value leading-none', VALUE_SIZE[valueSize])}
            style={{ color: accent }}
          >
            {displayValue}
          </div>

          <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            {effectiveDelta && Number.isFinite(effectiveDelta.value) && (
              <span
                className={cn(
                  'inline-flex items-center gap-0.5 rounded-pill px-1.5 py-0.5 text-[11px] font-bold tabnum',
                  deltaTone(effectiveDelta.value, effectiveDelta.direction),
                )}
              >
                {effectiveDelta.value > 0 ? (
                  <TrendingUp size={11} aria-hidden="true" />
                ) : effectiveDelta.value < 0 ? (
                  <TrendingDown size={11} aria-hidden="true" />
                ) : (
                  <Minus size={11} aria-hidden="true" />
                )}
                <span className="sr-only">
                  {effectiveDelta.value > 0 ? 'Sube ' : effectiveDelta.value < 0 ? 'Baja ' : 'Sin cambios '}
                </span>
                {Math.abs(effectiveDelta.value).toFixed(1)}
                {effectiveDelta.format === 'abs' ? '' : '%'}
              </span>
            )}
            {effectiveDelta?.label && (
              <span className="metric-support min-w-0 truncate" title={effectiveDelta.label}>
                {effectiveDelta.label}
              </span>
            )}
            {subtitle && (
              <span className="metric-support min-w-0 truncate" title={subtitle}>
                {subtitle}
              </span>
            )}
          </div>
        </>
      )}
    </article>
  )
}
