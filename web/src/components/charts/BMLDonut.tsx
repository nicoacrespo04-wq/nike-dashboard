'use client'

import { useMemo } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { cn } from '@/lib/utils'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import { SkeletonChart } from '@/components/ui/Skeleton'
import { BML_COLORS, BML_DESCRIPTION, type BMLKey } from './palette'

export interface BMLDonutProps {
  data: { beat: number; meet: number; lose: number; nd: number }
  title?: string
  size?: number
  showLegend?: boolean
  loading?: boolean
  error?: string | boolean | null
  onRetry?: () => void
  className?: string
}

const SEGMENTS: BMLKey[] = ['BEAT', 'MEET', 'LOSE']

interface Slice {
  name: BMLKey
  value: number
  total: number
}

function BMLTooltip({ active, payload }: { active?: boolean; payload?: { payload: Slice }[] }) {
  if (!active || !payload?.length) return null
  const { name, value, total } = payload[0].payload
  const pct = total > 0 ? ((value / total) * 100).toFixed(1) : '0,0'
  return (
    <div className="rounded-lg border border-surface-border bg-white px-3 py-2 text-sm shadow-popover">
      <p className="flex items-center gap-1.5 font-bold text-nike-ink">
        <span
          className="inline-block h-2.5 w-2.5 rounded-sm"
          style={{ background: BML_COLORS[name] }}
          aria-hidden="true"
        />
        {name}
      </p>
      <p className="mt-0.5 text-xs text-nike-muted">{BML_DESCRIPTION[name]}</p>
      <p className="mt-1 text-xs tabnum text-nike-ink-soft">
        <strong>{value.toLocaleString('es-AR')}</strong> SKUs — <strong>{pct}%</strong>
      </p>
    </div>
  )
}

/**
 * Distribución BML como dona.
 *
 * Decisiones de lectura:
 * - El anillo sólo muestra SKUs **con** comparable (BEAT/MEET/LOSE). Los N/D no
 *   son una categoría competitiva, son ausencia de dato: se reportan aparte,
 *   bajo la leyenda, como cobertura.
 * - Leyenda siempre visible con sigla + porcentaje: el par verde/naranja de la
 *   paleta de marca queda por debajo del umbral de separación para daltonismo
 *   (ΔE ≈ 7 en protanopía), así que el color nunca es el único canal.
 * - Separación de 2px entre gajos, en el color de la superficie, para que los
 *   límites se lean sin bordes duros.
 */
export default function BMLDonut({
  data,
  title,
  size = 220,
  showLegend = true,
  loading = false,
  error,
  onRetry,
  className,
}: BMLDonutProps) {
  const beat = Number(data?.beat ?? 0) || 0
  const meet = Number(data?.meet ?? 0) || 0
  const lose = Number(data?.lose ?? 0) || 0
  const nd = Number(data?.nd ?? 0) || 0

  const totalWithData = beat + meet + lose
  const totalAll = totalWithData + nd
  const coverage = totalAll > 0 ? Math.round((totalWithData / totalAll) * 100) : 0

  const chartData = useMemo<Slice[]>(
    () =>
      ([
        { name: 'BEAT', value: beat, total: totalWithData },
        { name: 'MEET', value: meet, total: totalWithData },
        { name: 'LOSE', value: lose, total: totalWithData },
      ] as Slice[]).filter((d) => d.value > 0),
    [beat, meet, lose, totalWithData],
  )

  if (loading) {
    return (
      <div className={cn('flex flex-col items-center', className)}>
        {title && <p className="label-caps mb-3">{title}</p>}
        <SkeletonChart variant="donut" height={size} />
      </div>
    )
  }

  if (error) {
    return (
      <ErrorState
        size="sm"
        className={className}
        title="No pudimos calcular la distribución BML"
        description={typeof error === 'string' ? error : undefined}
        onRetry={onRetry}
      />
    )
  }

  if (totalWithData === 0) {
    return (
      <EmptyState
        size="sm"
        className={className}
        title="Sin comparables de precio"
        description={
          nd > 0
            ? `${nd.toLocaleString('es-AR')} SKUs monitoreados, ninguno con precio Nike equivalente para comparar.`
            : 'Todavía no hay productos monitoreados para este corte.'
        }
      />
    )
  }

  return (
    <figure className={cn('flex flex-col items-center', className)}>
      {title && <figcaption className="label-caps mb-3">{title}</figcaption>}

      <div className="relative" style={{ width: size, height: size }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={size * 0.32}
              outerRadius={size * 0.48}
              paddingAngle={2}
              dataKey="value"
              nameKey="name"
              /* Anillo del color de la superficie: separa gajos sin borde duro. */
              stroke="#FFFFFF"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {chartData.map((entry) => (
                <Cell key={entry.name} fill={BML_COLORS[entry.name]} />
              ))}
            </Pie>
            <Tooltip content={<BMLTooltip />} />
          </PieChart>
        </ResponsiveContainer>

        {/* Número protagonista en el centro */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-metric-md font-extrabold tabnum text-nike-ink">
            {totalWithData.toLocaleString('es-AR')}
          </span>
          <span className="text-micro uppercase tracking-wide text-nike-faint">SKUs comparados</span>
        </div>
      </div>

      {showLegend && (
        <div className="mt-4 w-full max-w-[220px]">
          <ul className="flex flex-col gap-1.5 text-xs">
            {SEGMENTS.map((key) => {
              const value = key === 'BEAT' ? beat : key === 'MEET' ? meet : lose
              const pct = totalWithData > 0 ? Math.round((value / totalWithData) * 100) : 0
              return (
                <li key={key} className="flex items-center gap-2" title={BML_DESCRIPTION[key]}>
                  <span
                    className="h-2.5 w-2.5 flex-shrink-0 rounded-sm"
                    style={{ background: BML_COLORS[key] }}
                    aria-hidden="true"
                  />
                  <span className="text-[11px] font-semibold text-nike-ink-soft">{key}</span>
                  <span className="ml-auto tabnum text-[11px] text-nike-muted">
                    {value.toLocaleString('es-AR')}
                  </span>
                  <strong className="w-9 text-right tabnum text-[11px] text-nike-ink">{pct}%</strong>
                </li>
              )
            })}
          </ul>

          {/* Cobertura: hace visible cuántos SKUs quedaron sin comparar. */}
          {nd > 0 && (
            <p className="mt-2 border-t border-surface-border pt-2 text-micro leading-relaxed text-nike-faint">
              {nd.toLocaleString('es-AR')} SKUs sin comparable ·{' '}
              <span className="tabnum">{coverage}%</span> de cobertura
            </p>
          )}
        </div>
      )}
    </figure>
  )
}
