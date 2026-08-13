'use client'

import { useEffect, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import KPICard from '@/components/ui/KPICard'
import { formatPrice } from '@/lib/utils'

export default function AssortmentPage() {
  const [data, setData]       = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab]         = useState<'franchises'|'siluetas'|'gaps'>('franchises')

  useEffect(() => {
    Promise.all([
      fetch('/api/pricing/franchises').then(r => r.json()),
      fetch('/api/pricing/siluetas').then(r => r.json()),
    ]).then(([fr, si]) => {
      setData({ franchises: fr.franchises ?? [], siluetas: si.siluetas ?? [] })
    }).finally(() => setLoading(false))
  }, [])

  const franchises = data?.franchises ?? []
  const siluetas   = data?.siluetas   ?? []

  // Agrupar franchises por marca para comparación
  const nikeF   = franchises.filter((f: any) => f.marca === 'NIKE')
  const adidasF = franchises.filter((f: any) => f.marca === 'ADIDAS')
  const pumaF   = franchises.filter((f: any) => f.marca === 'PUMA')

  // Gap analysis: franchises Adidas/Puma sin equivalente Nike
  const nikeNames = new Set(nikeF.map((f: any) => f.franchise?.toLowerCase()))
  const gapsAdidas = adidasF.filter((f: any) => !nikeNames.has(f.franchise?.toLowerCase()))
  const gapsPuma   = pumaF.filter((f: any) => !nikeNames.has(f.franchise?.toLowerCase()))

  // Top siluetas para el chart
  const topSiluetas = [...siluetas]
    .reduce((acc: any[], s: any) => {
      const existing = acc.find(x => x.silueta === s.silueta)
      if (existing) {
        existing[s.marca] = Number(s.count)
      } else {
        acc.push({ silueta: s.silueta, [s.marca]: Number(s.count) })
      }
      return acc
    }, [])
    .sort((a, b) => (b.NIKE || 0) + (b.ADIDAS || 0) + (b.Puma || 0) - ((a.NIKE || 0) + (a.ADIDAS || 0) + (a.Puma || 0)))
    .slice(0, 15)

  return (
    <div className="space-y-6">

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard loading={loading} title="Franchises Adidas" value={adidasF.length} subtitle="modelos monitoreados" color="#0046CC" />
        <KPICard loading={loading} title="Franchises Puma"   value={pumaF.length}   subtitle="modelos monitoreados" color="#E4032E" />
        <KPICard loading={loading} title="Gaps Adidas vs Nike" value={gapsAdidas.length} subtitle="sin equivalente Nike" color="#E31837" />
        <KPICard loading={loading} title="Gaps Puma vs Nike"   value={gapsPuma.length}   subtitle="sin equivalente Nike" color="#F5A623" />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {([['franchises','Franchises por Marca'],['siluetas','Siluetas'],['gaps','Product Gap Analysis']] as const).map(([val, label]) => (
          <button
            key={val}
            onClick={() => setTab(val)}
            className={`px-5 py-3 text-sm font-semibold transition-all border-b-2 -mb-px ${
              tab === val ? 'border-[#E31837] text-[#111111]' : 'border-transparent text-gray-400 hover:text-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab: Franchises */}
      {tab === 'franchises' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Adidas franchises */}
          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-4 flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#0046CC]" />
              Top Franchises Adidas
            </h2>
            <div className="overflow-x-auto">
              <table className="nike-table">
                <thead><tr><th>Franchise</th><th className="text-right">SKUs</th><th className="text-right">Precio Prom.</th><th className="text-right">En Promo</th></tr></thead>
                <tbody>
                  {loading
                    ? Array.from({length:8}).map((_,i) => <tr key={i} className="animate-pulse border-b"><td colSpan={4}><div className="h-3 bg-gray-200 rounded my-2 w-3/4"/></td></tr>)
                    : adidasF.slice(0,15).map((f: any) => (
                      <tr key={f.franchise}>
                        <td className="font-medium">{f.franchise}</td>
                        <td className="text-right font-mono">{Number(f.count).toLocaleString('es-AR')}</td>
                        <td className="text-right">{formatPrice(f.avg_price)}</td>
                        <td className="text-right text-orange-600">{Number(f.in_promo) > 0 ? `${f.in_promo}` : '—'}</td>
                      </tr>
                    ))
                  }
                </tbody>
              </table>
            </div>
          </div>

          {/* Puma franchises */}
          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-4 flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#E4032E]" />
              Top Franchises Puma
            </h2>
            <div className="overflow-x-auto">
              <table className="nike-table">
                <thead><tr><th>Franchise</th><th className="text-right">SKUs</th><th className="text-right">Precio Prom.</th><th className="text-right">En Promo</th></tr></thead>
                <tbody>
                  {loading
                    ? Array.from({length:8}).map((_,i) => <tr key={i} className="animate-pulse border-b"><td colSpan={4}><div className="h-3 bg-gray-200 rounded my-2 w-3/4"/></td></tr>)
                    : pumaF.slice(0,15).map((f: any) => (
                      <tr key={f.franchise}>
                        <td className="font-medium">{f.franchise}</td>
                        <td className="text-right font-mono">{Number(f.count).toLocaleString('es-AR')}</td>
                        <td className="text-right">{formatPrice(f.avg_price)}</td>
                        <td className="text-right text-orange-600">{Number(f.in_promo) > 0 ? `${f.in_promo}` : '—'}</td>
                      </tr>
                    ))
                  }
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Siluetas */}
      {tab === 'siluetas' && (
        <div className="nike-card">
          <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-4">
            Distribución por Silueta — Adidas + Puma vs Nike
          </h2>
          <ResponsiveContainer width="100%" height={420}>
            <BarChart data={topSiluetas} margin={{ top: 4, right: 16, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F0F0F0" />
              <XAxis dataKey="silueta" tick={{ fontSize: 10, fill: '#666' }} angle={-35} textAnchor="end" interval={0} height={80} />
              <YAxis tick={{ fontSize: 11, fill: '#9B9B9B' }} axisLine={false} tickLine={false} />
              <Tooltip formatter={(v: any, name: string) => [Number(v).toLocaleString('es-AR') + ' SKUs', name]} />
              <Legend />
              <Bar dataKey="NIKE"   fill="#111111" radius={[3,3,0,0]} />
              <Bar dataKey="ADIDAS" fill="#0046CC" radius={[3,3,0,0]} />
              <Bar dataKey="Puma"   fill="#E4032E" radius={[3,3,0,0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Tab: Product Gap Analysis */}
      {tab === 'gaps' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1 flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#0046CC]" />
              Franchises Adidas sin equivalente Nike
            </h2>
            <p className="text-xs text-gray-400 mb-4">{gapsAdidas.length} modelos Adidas no matcheados contra Nike</p>
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {gapsAdidas.slice(0,30).map((f: any) => (
                <div key={f.franchise} className="flex items-center justify-between py-2 border-b border-gray-50">
                  <span className="text-sm font-medium text-gray-800">{f.franchise}</span>
                  <div className="flex items-center gap-3 text-xs text-gray-500">
                    <span>{Number(f.count)} SKUs</span>
                    <span className="font-semibold text-gray-700">{formatPrice(f.avg_price)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1 flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#E4032E]" />
              Franchises Puma sin equivalente Nike
            </h2>
            <p className="text-xs text-gray-400 mb-4">{gapsPuma.length} modelos Puma no matcheados contra Nike</p>
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {gapsPuma.slice(0,30).map((f: any) => (
                <div key={f.franchise} className="flex items-center justify-between py-2 border-b border-gray-50">
                  <span className="text-sm font-medium text-gray-800">{f.franchise}</span>
                  <div className="flex items-center gap-3 text-xs text-gray-500">
                    <span>{Number(f.count)} SKUs</span>
                    <span className="font-semibold text-gray-700">{formatPrice(f.avg_price)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

    </div>
  )
}
