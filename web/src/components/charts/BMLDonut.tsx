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
      <ResponsiveContainer width="100%" height={size}>
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius={size * 0.27}
            outerRadius={size * 0.4}
            paddingAngle={2}
            dataKey="value"
          >
            {chartData.map((entry) => (
              <Cell key={entry.name} fill={COLORS[entry.name as keyof typeof COLORS]} />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
          {showLegend && (
            <Legend
              formatter={(value, entry: any) => {
                const pct = total > 0 ? ((entry.payload.value / total) * 100).toFixed(0) : '0'
                return <span className="text-xs text-gray-600">{value} <strong>{pct}%</strong></span>
              }}
            />
          )}
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}
