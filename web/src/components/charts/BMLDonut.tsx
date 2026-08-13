'use client'

import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface BMLDonutProps {
  data: { beat: number; meet: number; lose: number; nd: number }
  title?: string
  size?: number
  showLegend?: boolean
}

const COLORS = { BEAT: '#E31837', MEET: '#F5A623', LOSE: '#27AE60', 'N/D': '#9B9B9B' }

const CustomTooltip = ({ active, payload }: any) => {
  if (!active || !payload?.length) return null
  const { name, value, total } = payload[0].payload
  const pct = total > 0 ? ((value / total) * 100).toFixed(1) : '0'
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-3 py-2 shadow-lg text-sm">
      <p className="font-semibold text-gray-800">{name}</p>
      <p className="text-gray-500">{value.toLocaleString('es-AR')} SKUs — <span className="font-bold">{pct}%</span></p>
    </div>
  )
}

const CenterLabel = ({ cx, cy, total }: any) => (
  <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle">
    <tspan x={cx} dy="-8" fontSize="22" fontWeight="700" fill="#111111">
      {total.toLocaleString('es-AR')}
    </tspan>
    <tspan x={cx} dy="20" fontSize="11" fill="#757575">SKUs</tspan>
  </text>
)

export default function BMLDonut({ data, title, size = 220, showLegend = true }: BMLDonutProps) {
  const total = data.beat + data.meet + data.lose + data.nd
  const chartData = [
    { name: 'BEAT', value: data.beat, total },
    { name: 'MEET', value: data.meet, total },
    { name: 'LOSE', value: data.lose, total },
    { name: 'N/D',  value: data.nd,   total },
  ].filter(d => d.value > 0)

  if (total === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
        Sin datos disponibles
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center">
      {title && <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">{title}</p>}
      <div className="relative">
        <ResponsiveContainer width={size} height={size}>
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={size * 0.32}
              outerRadius={size * 0.48}
              paddingAngle={3}
              dataKey="value"
              strokeWidth={0}
            >
              {chartData.map((entry) => (
                <Cell key={entry.name} fill={COLORS[entry.name as keyof typeof COLORS]} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip />} />
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-3xl font-bold text-gray-900">{total.toLocaleString('es-AR')}</span>
          <span className="text-[10px] text-gray-400 uppercase tracking-wide">SKUs</span>
        </div>
      </div>
      {showLegend && (
        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs w-full max-w-[200px]">
          {chartData.map(d => {
            const pct = total > 0 ? ((d.value / total) * 100).toFixed(0) : '0'
            return (
              <div key={d.name} className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ background: COLORS[d.name as keyof typeof COLORS] }} />
                <span className="text-gray-600 text-[11px]">{d.name}</span>
                <strong className="ml-auto text-gray-900 text-[11px]">{pct}%</strong>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
