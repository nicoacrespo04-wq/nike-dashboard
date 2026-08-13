'use client'

import { useEffect, useState } from 'react'
import { Search, Star, Share2, Lock } from 'lucide-react'
import KPICard from '@/components/ui/KPICard'

interface SummaryResponse {
  byCanal: Record<string, { marca: string; wins: number; pct: number }[]>
  global: { nike: number | null; adidas: number | null; puma: number | null; n: number }
  totalByCanal: Record<string, number>
}

interface SearchRow {
  canal: string
  search_term: string
  nike?: number
  adidas?: number
  puma?: number
  winner: string
}

const MARCA_COLOR: Record<string, string> = {
  Nike: '#E31837',
  Adidas: '#111111',
  Puma: '#6B7280',
}

function pct(v: number | null | undefined) {
  if (v === null || v === undefined || isNaN(v)) return '—'
  return `${Math.round(v * 100)}%`
}

export default function ShelfPage() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null)
  const [rows, setRows] = useState<SearchRow[]>([])
  const [canalFilter, setCanalFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch('/api/shelf/summary').then((r) => r.json()),
      fetch(`/api/shelf/searches${canalFilter ? `?canal=${canalFilter}` : ''}`).then((r) => r.json()),
    ]).then(([s, d]) => {
      setSummary(s)
      setRows(d.rows)
      setLoading(false)
    })
  }, [canalFilter])

  const canales = summary ? Object.keys(summary.byCanal).sort() : []

  return (
    <div className="space-y-6">
      {/* KPIs globales */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <KPICard
          title="Visibilidad Nike"
          value={loading ? '—' : pct(summary?.global.nike ?? null)}
          subtitle={`Promedio en ${summary?.global.n ?? 0} búsquedas`}
          icon={<Search size={18} />}
          color="#E31837"
          loading={loading}
        />
        <KPICard
          title="Visibilidad Adidas"
          value={loading ? '—' : pct(summary?.global.adidas ?? null)}
          subtitle="Promedio en buscadores"
          icon={<Search size={18} />}
          color="#0046CC"
          loading={loading}
        />
        <KPICard
          title="Visibilidad Puma"
          value={loading ? '—' : pct(summary?.global.puma ?? null)}
          subtitle="Promedio en buscadores"
          icon={<Search size={18} />}
          color="#E4032E"
          loading={loading}
        />
        <KPICard
          title="Retailers"
          value={loading ? '—' : canales.length.toString()}
          subtitle="Monitoreados con búsquedas"
          icon={<Share2 size={18} />}
          color="#111111"
          loading={loading}
        />
      </div>

      {/* Share of Shelf por retailer */}
      <div className="nike-card">
        <h3 className="text-sm font-bold text-[#111] mb-1">Share of Shelf por retailer</h3>
        <p className="text-xs text-gray-400 mb-4">
          % de términos de búsqueda donde la marca aparece primero (mejor posición) en el buscador del sitio.
        </p>
        <div className="space-y-4">
          {canales.map((canal) => {
            const marcas = summary!.byCanal[canal].sort((a, b) => b.pct - a.pct)
            return (
              <div key={canal}>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-sm font-semibold text-[#111]">{canal}</span>
                  <span className="text-xs text-gray-400">{summary!.totalByCanal[canal]} términos</span>
                </div>
                <div className="flex h-6 rounded-lg overflow-hidden bg-gray-100">
                  {marcas.map((m) => (
                    <div
                      key={m.marca}
                      className="flex items-center justify-center text-[10px] font-bold text-white transition-all"
                      style={{ width: `${m.pct}%`, backgroundColor: MARCA_COLOR[m.marca] ?? '#999' }}
                      title={`${m.marca}: ${m.pct}%`}
                    >
                      {m.pct >= 12 ? `${m.marca} ${m.pct}%` : ''}
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Detalle por término */}
      <div className="nike-card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-[#111]">Detalle por término de búsqueda</h3>
          <select
            className="text-xs border border-gray-200 rounded-lg px-3 py-1.5 text-gray-600"
            value={canalFilter}
            onChange={(e) => setCanalFilter(e.target.value)}
          >
            <option value="">Todos los retailers</option>
            {canales.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-400 border-b border-gray-100">
                <th className="py-2 pr-4">Retailer</th>
                <th className="py-2 pr-4">Búsqueda</th>
                <th className="py-2 pr-4">Nike</th>
                <th className="py-2 pr-4">Adidas</th>
                <th className="py-2 pr-4">Puma</th>
                <th className="py-2 pr-4">Gana</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 80).map((r, i) => (
                <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-2 pr-4 text-gray-500">{r.canal}</td>
                  <td className="py-2 pr-4 font-medium text-[#111]">{r.search_term}</td>
                  <td className="py-2 pr-4">{pct(r.nike ?? null)}</td>
                  <td className="py-2 pr-4">{pct(r.adidas ?? null)}</td>
                  <td className="py-2 pr-4">{pct(r.puma ?? null)}</td>
                  <td className="py-2 pr-4">
                    <span
                      className="px-2 py-0.5 rounded-full text-[10px] font-bold text-white"
                      style={{ backgroundColor: MARCA_COLOR[r.winner] ?? '#999' }}
                    >
                      {r.winner}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 80 && (
            <p className="text-[10px] text-gray-400 mt-2">Mostrando 80 de {rows.length} términos.</p>
          )}
        </div>
      </div>

      {/* Ratings/Reviews y Redes Sociales — sin datos reales todavia */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="nike-card border-2 border-dashed border-gray-200 flex flex-col items-center justify-center py-10 text-center">
          <Star className="text-gray-300 mb-2" size={28} />
          <h4 className="text-sm font-bold text-gray-400">Ratings & Reviews por retailer</h4>
          <p className="text-xs text-gray-400 mt-1 max-w-xs">
            Próximamente. Todavía no hay un scraper validado con datos reales para esta sección — en construcción.
          </p>
          <span className="mt-3 inline-flex items-center gap-1 text-[10px] font-semibold text-gray-400 bg-gray-100 px-2.5 py-1 rounded-full">
            <Lock size={10} /> Sin datos reales aún
          </span>
        </div>
        <div className="nike-card border-2 border-dashed border-gray-200 flex flex-col items-center justify-center py-10 text-center">
          <Share2 className="text-gray-300 mb-2" size={28} />
          <h4 className="text-sm font-bold text-gray-400">Branding en redes sociales</h4>
          <p className="text-xs text-gray-400 mt-1 max-w-xs">
            Próximamente. Todavía no hay un scraper/API conectado para esta sección — en construcción.
          </p>
          <span className="mt-3 inline-flex items-center gap-1 text-[10px] font-semibold text-gray-400 bg-gray-100 px-2.5 py-1 rounded-full">
            <Lock size={10} /> Sin datos reales aún
          </span>
        </div>
      </div>
    </div>
  )
}
