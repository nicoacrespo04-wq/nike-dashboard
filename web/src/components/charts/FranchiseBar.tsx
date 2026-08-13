'use client'

import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts'
import { formatPrice } from '@/lib/utils'

export interface FranchiseDataPoint {
  franchise: string
  count: number
  avg_price: number
  avg_gap_pct: number
  beat: number
  meet: number
  lose: number
}

interface FranchiseBarProps {
  data: FranchiseDataPoint[]
  marca: string
  limit?: number
  height?: number
  onSelect?: (franchise: string) => void
  selectedFranchise?: string
}

const MARCA_COLOR: Record<string, string> = {
  ADIDAS: '#0046CC',
  PUMA:   '#E4032E',
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const total = d.beat + d.meet + d.lose
  const beatPct = total > 0 ? ((d.beat / total) * 100).toFixed(0) : '0'
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 py-3 shadow-xl text-sm max-w-xs">
      <p className="font-bold text-gray-900 mb-2 leading-tight">{d.franchise}</p>
      <div className="space-y-1 text-xs text-gray-600">
        <p>SKUs monitoreados: <strong className="text-gray-900">{d.count}</strong></p>
        <p>Precio promedio: <strong className="text-gray-900">{formatPrice(d.avg_price)}</strong></p>
        <p>Gap vs Nike: <strong className={d.avg_gap_pct < 0 ? 'text-red-600' : 'text-green-600'}>
          {d.avg_gap_pct > 0 ? '+' : ''}{(d.avg_gap_pct * 100).toFixed(1)}%
        </strong></p>
        <p>BEAT Nike: <strong className="text-red-600">{beatPct}%</strong></p>
      </div>
    </div>
  )
}

export default function FranchiseBar({
  data,
  marca,
  limit = 15,
  height = 420,
  onSelect,
  selectedFranchise,
}: FranchiseBarProps) {
  if (!data?.length) {
    return (
      <div className="flex items-center justify-center text-gray-400 text-sm" style={{ height }}>
        Sin datos disponibles
      </div>
    )
  }

  const sorted = [...data]
    .sort((a, b) => b.count - a.count)
    .slice(0, limit)
    .reverse() // reversed so top is at top in horizontal chart

  const barColor = MARCA_COLOR[marca.toUpperCase()] ?? '#111111'

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={sorted}
        layout="vertical"
        margin={{ top: 4, right: 40, left: 8, bottom: 4 }}
        onClick={(e) => { if (e?.activePayload?.[0] && onSelect) onSelect(e.activePayload[0].payload.franchise) }}
      >
        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F0F0F0" />
        <XAxis type="number" tick={{ fontSize: 11, fill: '#9B9B9B' }} axisLine={false} tickLine={false} />
        <YAxis
          type="category"
          dataKey="franchise"
          width={130}
          tick={{ fontSize: 11, fill: '#444' }}
          tickFormatter={(v: string) => v?.length > 18 ? v.slice(0, 17) + '…' : v}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip content={<CustomTooltip />} cursor={{ fill: '#F5F5F5' }} />
        <Bar dataKey="count" radius={[0, 4, 4, 0]} cursor={onSelect ? 'pointer' : 'default'}>
          {sorted.map((entry) => (
            <Cell
              key={entry.franchise}
              fill={selectedFranchise === entry.franchise ? '#111111' : barColor}
              opacity={selectedFranchise && selectedFranchise !== entry.franchise ? 0.5 : 1}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
