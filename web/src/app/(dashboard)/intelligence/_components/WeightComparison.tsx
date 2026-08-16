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
import type { Factor } from '@/types/intelligence'
import {
  CHART_INK,
  UNAVAILABLE_COLOR,
  factorColor,
  factorLabel,
  sortFactors,
} from '@/components/charts/palette'
import { EmptyState } from '@/components/ui'

/**
 * Peso configurado vs. contribución efectiva.
 *
 * Único trozo cliente del detalle de match: recharts necesita medir el
 * contenedor en el browser. Recibe los factores ya resueltos en el servidor.
 */
interface WeightDatum {
  factor: string
  label: string
  configurado: number
  efectivo: number
  available: boolean
}

export default function WeightComparison({
  factors,
  configuredWeights,
}: {
  factors: Factor[]
  configuredWeights: Record<string, number>
}) {
  const data: WeightDatum[] = sortFactors(factors).map((f) => ({
    factor: f.factor,
    label: factorLabel(f.factor),
    configurado: (configuredWeights?.[f.factor] ?? f.weight ?? 0) * 100,
    efectivo: f.available ? (f.contribution ?? 0) : 0,
    available: f.available,
  }))

  if (data.length === 0) {
    return (
      <EmptyState
        title="Sin factores"
        description="No hay factores persistidos para comparar contra la configuración."
        size="sm"
      />
    )
  }

  return (
    <div>
      <div className="h-[280px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} stroke={CHART_INK.grid} />
            <XAxis
              type="number"
              domain={[0, 'dataMax']}
              tick={{ fontSize: 10, fill: CHART_INK.axis }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
              unit="%"
            />
            <YAxis
              type="category"
              dataKey="label"
              width={120}
              tick={{ fontSize: 11, fill: CHART_INK.axisStrong }}
              tickLine={false}
              axisLine={{ stroke: CHART_INK.line }}
            />
            <RechartsTooltip
              cursor={{ fill: CHART_INK.cursor }}
              contentStyle={{
                fontSize: 11,
                borderRadius: 6,
                border: `1px solid ${CHART_INK.line}`,
                boxShadow: '0 8px 24px rgba(17,17,17,0.10)',
              }}
              formatter={(value: number, name: string) => [`${value.toFixed(1)}%`, name]}
            />
            <Bar
              dataKey="configurado"
              name="Peso configurado"
              fill="#C9C9C3"
              radius={[0, 3, 3, 0]}
              barSize={9}
            />
            <Bar dataKey="efectivo" name="Contribución efectiva" radius={[0, 3, 3, 0]} barSize={9}>
              {data.map((d) => (
                <Cell key={d.factor} fill={d.available ? factorColor(d.factor) : UNAVAILABLE_COLOR} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Leyenda propia: el color de la barra efectiva es el del factor, no una serie. */}
      <ul className="mt-1 flex flex-wrap gap-x-5 gap-y-1 pl-[128px] text-2xs text-nike-ink-soft">
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[2px] bg-[#C9C9C3]" />
          Peso configurado en weights.yaml
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-flex gap-[2px]">
            <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-[#2A78D6]" />
            <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-[#1BAF7A]" />
            <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-[#EDA100]" />
          </span>
          Contribución efectiva (color del factor)
        </li>
      </ul>

      {/* Vista de tabla: el color nunca es el único canal. */}
      <div className="nike-table-wrap mt-4">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-surface-border text-left text-label uppercase text-nike-muted">
              <th className="py-1.5 pr-3 font-semibold">Factor</th>
              <th className="py-1.5 pr-3 text-right font-semibold">Peso configurado</th>
              <th className="py-1.5 pr-3 text-right font-semibold">Contribución efectiva</th>
              <th className="py-1.5 text-right font-semibold">Estado</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {data.map((d) => (
              <tr key={d.factor} className={d.available ? '' : 'text-nike-muted'}>
                <td className="py-1.5 pr-3 font-medium">{d.label}</td>
                <td className="tabular py-1.5 pr-3 text-right">{d.configurado.toFixed(0)}%</td>
                <td className="tabular py-1.5 pr-3 text-right font-semibold">
                  {d.available ? `${d.efectivo.toFixed(1)}%` : '—'}
                </td>
                <td className="py-1.5 text-right text-2xs">
                  {d.available ? 'con datos' : 'sin datos · excluido'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
