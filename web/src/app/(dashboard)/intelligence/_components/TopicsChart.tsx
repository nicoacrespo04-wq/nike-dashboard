'use client'

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Topic } from '@/types/intelligence'
import { CHART_INK, humanize } from '@/components/charts/palette'
import { EmptyState } from '@/components/ui'

interface TopicDatum {
  label: string
  mentions: number
  sentiment: number
  intent: string
  brand: string
}

/**
 * Tópicos de conversación.
 *
 * Único trozo del panel de consumidor que sigue siendo cliente: recharts mide
 * el contenedor en el browser. Recibe los datos ya resueltos en el servidor, no
 * los pide.
 */
export default function TopicsChart({ items }: { items: Topic[] }) {
  const data: TopicDatum[] = items
    .filter((t) => (t.mentions ?? 0) > 0)
    .slice(0, 12)
    .map((t) => ({
      label: `${humanize(t.topic ?? '—')}${t.brand ? ` · ${t.brand}` : ''}`,
      mentions: t.mentions ?? 0,
      sentiment: t.sentiment ?? 0,
      intent: t.intent ?? '—',
      brand: t.brand ?? '—',
    }))

  if (data.length === 0) {
    return (
      <EmptyState
        title="Sin tópicos con volumen"
        description="Los agregados sociales existen pero ninguno declara menciones."
        size="sm"
      />
    )
  }

  return (
    <div>
      {/* Alto calculado a partir de la cantidad de barras: el contenedor mide
          lo mismo antes y después de que recharts pinte, así no hay salto. */}
      <div style={{ height: Math.max(200, data.length * 26 + 40) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 30, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} stroke={CHART_INK.grid} />
            <XAxis
              type="number"
              tick={{ fontSize: 10, fill: CHART_INK.axis }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={150}
              tick={{ fontSize: 10, fill: CHART_INK.axisStrong }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
            />
            <RechartsTooltip
              cursor={{ fill: CHART_INK.cursor }}
              contentStyle={{
                fontSize: 11,
                borderRadius: 6,
                border: `1px solid ${CHART_INK.line}`,
              }}
              formatter={(value: number) => [`${value} menciones`, 'Volumen']}
            />
            <Bar dataKey="mentions" name="Menciones" radius={[0, 3, 3, 0]} barSize={12}>
              {data.map((d, i) => (
                <Cell key={i} fill={d.sentiment >= 0 ? '#2A78D6' : '#D03B3B'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-nike-ink-soft">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#2A78D6]" />
          sentimiento medio positivo o neutro
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#D03B3B]" />
          sentimiento medio negativo
        </span>
      </p>
    </div>
  )
}
