'use client'

import { useMemo } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { cn } from '@/lib/utils'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import { SkeletonChart } from '@/components/ui/Skeleton'
import { formatPctSafe, formatPriceSafe } from '@/components/ui/format'
import { BML_COLORS, CHART_INK, brandColor } from './palette'

export interface FranchiseDataPoint {
  franchise: string
  count: number
  avg_price: number
  avg_gap_pct: number
  beat: number
  meet: number
  lose: number
}

export interface FranchiseBarProps {
  data: FranchiseDataPoint[]
  marca: string
  limit?: number
  height?: number
  onSelect?: (franchise: string) => void
  selectedFranchise?: string
  loading?: boolean
  error?: string | boolean | null
  onRetry?: () => void
  className?: string
}

function FranchiseTooltip({ active, payload }: { active?: boolean; payload?: { payload: FranchiseDataPoint }[] }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const withData = (d.beat ?? 0) + (d.meet ?? 0) + (d.lose ?? 0)
  const beatPct = withData > 0 ? Math.round((d.beat / withData) * 100) : null

  return (
    <div className="max-w-xs rounded-xl border border-surface-border bg-white px-4 py-3 shadow-popover">
      <p className="mb-2 text-sm font-bold leading-tight text-nike-ink">{d.franchise}</p>
      <dl className="space-y-1 text-xs text-nike-muted">
        <div className="flex items-baseline justify-between gap-4">
          <dt>SKUs monitoreados</dt>
          <dd className="font-semibold tabnum text-nike-ink">{d.count?.toLocaleString('es-AR')}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt>Precio promedio</dt>
          <dd className="font-semibold tabnum text-nike-ink">{formatPriceSafe(d.avg_price)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <dt>Gap vs. Nike</dt>
          <dd
            className={cn(
              'font-semibold tabnum',
              d.avg_gap_pct == null ? 'text-nike-faint' : d.avg_gap_pct < 0 ? 'text-bml-lose-ink' : 'text-bml-beat-ink',
            )}
          >
            {formatPctSafe(d.avg_gap_pct)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-4 border-t border-surface-border pt-1">
          <dt className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: BML_COLORS.BEAT }} aria-hidden="true" />
            BEAT (Nike más barato)
          </dt>
          <dd className="font-semibold tabnum text-bml-beat-ink">
            {beatPct == null ? 'N/D' : `${beatPct}%`}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-micro text-nike-faint">Click en la barra para filtrar la tabla</p>
    </div>
  )
}

/**
 * Top franchises por cantidad de SKUs monitoreados.
 *
 * Barras horizontales porque las etiquetas son nombres largos (leerlas en
 * horizontal evita el texto rotado) y el orden por magnitud hace el ranking
 * evidente. Un solo color por gráfico: la serie es una sola (cantidad), la
 * identidad la da la marca del panel, así que no hace falta leyenda de color.
 */
export default function FranchiseBar({
  data,
  marca,
  limit = 15,
  height = 420,
  onSelect,
  selectedFranchise,
  loading = false,
  error,
  onRetry,
  className,
}: FranchiseBarProps) {
  const sorted = useMemo(
    () =>
      [...(data ?? [])]
        .filter((d) => d?.franchise && Number(d.count) > 0)
        .sort((a, b) => b.count - a.count)
        .slice(0, limit)
        .reverse(), // invertido: en un eje vertical el primero queda arriba
    [data, limit],
  )

  if (loading) return <SkeletonChart height={height} className={className} />

  if (error) {
    return (
      <ErrorState
        className={className}
        title={`No pudimos cargar las franchises de ${marca}`}
        description={typeof error === 'string' ? error : undefined}
        onRetry={onRetry}
      />
    )
  }

  if (!sorted.length) {
    return (
      <div className={cn('flex items-center justify-center', className)} style={{ minHeight: height / 2 }}>
        <EmptyState
          title={`Sin franchises de ${marca} para este corte`}
          description="Ajustá los filtros de canal o división para ver resultados."
        />
      </div>
    )
  }

  const barColor = brandColor(marca)
  const maxCount = Math.max(...sorted.map((d) => d.count))

  return (
    <div className={cn('min-w-0', className)}>
      <div
        role="img"
        aria-label={`Top ${sorted.length} franchises de ${marca} por cantidad de SKUs monitoreados. Máximo: ${sorted[sorted.length - 1]?.franchise} con ${maxCount} SKUs.`}
      >
        <ResponsiveContainer width="100%" height={height}>
          <BarChart
            data={sorted}
            layout="vertical"
            margin={{ top: 4, right: 48, left: 8, bottom: 4 }}
            onClick={(e) => {
              const point = e?.activePayload?.[0]?.payload as FranchiseDataPoint | undefined
              if (point && onSelect) onSelect(point.franchise)
            }}
          >
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke={CHART_INK.grid} />
            <XAxis
              type="number"
              tick={{ fontSize: 11, fill: CHART_INK.axis }}
              axisLine={false}
              tickLine={false}
              allowDecimals={false}
            />
            <YAxis
              type="category"
              dataKey="franchise"
              width={140}
              tick={{ fontSize: 11, fill: CHART_INK.axisStrong }}
              tickFormatter={(v: string) => (v?.length > 20 ? `${v.slice(0, 19)}…` : v)}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip content={<FranchiseTooltip />} cursor={{ fill: CHART_INK.cursor }} />
            <Bar
              dataKey="count"
              radius={[0, 4, 4, 0]}
              barSize={14}
              cursor={onSelect ? 'pointer' : 'default'}
              isAnimationActive={false}
            >
              {/* Etiqueta directa: evita rebotar al eje para leer la magnitud. */}
              <LabelList
                dataKey="count"
                position="right"
                offset={8}
                style={{ fill: CHART_INK.axisStrong, fontSize: 11, fontWeight: 600 }}
                formatter={(v: number) => v?.toLocaleString('es-AR')}
              />
              {sorted.map((entry) => {
                const dimmed = Boolean(selectedFranchise) && selectedFranchise !== entry.franchise
                return (
                  <Cell
                    key={entry.franchise}
                    fill={selectedFranchise === entry.franchise ? CHART_INK.selected : barColor}
                    opacity={dimmed ? 0.35 : 1}
                  />
                )
              })}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Alternativa accesible: las barras de recharts no son enfocables, así
          que exponemos la misma acción de filtrado por teclado. */}
      {onSelect && (
        <ul className="sr-only">
          {[...sorted].reverse().map((d) => (
            <li key={d.franchise}>
              <button type="button" onClick={() => onSelect(d.franchise)}>
                Filtrar por franchise {d.franchise}: {d.count} SKUs
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
