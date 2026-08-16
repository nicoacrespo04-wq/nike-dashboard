'use client'

import { useEffect, useState } from 'react'
import { Search, Star, Share2, Lock, AlertTriangle } from 'lucide-react'
import KPICard from '@/components/ui/KPICard'
import { fetchJson, errorMessage } from '@/lib/fetchJson'
import { formatRatioPct } from '@/lib/utils'

interface MarcaDiagnosticRow {
  raw: string
  hex: string
  n: number
}

interface BrandVisibility {
  value: number | null
  rows: number
  terms: number
}

interface SummaryResponse {
  byCanal: Record<string, { marca: string; wins: number; pct: number }[]>
  global: { nike: number | null; adidas: number | null; puma: number | null; n: number }
  visibility: { nike: BrandVisibility; adidas: BrandVisibility; puma: BrandVisibility }
  totalByCanal: Record<string, number>
  /** Siempre llega; `marcasFaltantes` está vacío cuando las tres resolvieron. */
  diagnostics?: {
    message?: string
    marcasFaltantes: string[]
    marcasEnLaBase: MarcaDiagnosticRow[]
  }
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
  Adidas: '#0046CC',
  Puma: '#E4032E',
}

/** Las tres tarjetas de visibilidad, en orden. */
const MARCAS_KPI = [
  { key: 'nike', label: 'Nike' },
  { key: 'adidas', label: 'Adidas' },
  { key: 'puma', label: 'Puma' },
] as const satisfies readonly { key: 'nike' | 'adidas' | 'puma'; label: string }[]

const VISIBILIDAD_HINT =
  'Promedio de Nike_Visibility (0-1, donde 1 es la mejor posición posible) de ' +
  'todos los términos de búsqueda en los que se observó la marca, en todos los ' +
  'retailers monitoreados. Se pondera por cantidad de observaciones.'

// `nike_visibility` es un float 0..1: hay que formatearlo explícitamente como
// porcentaje. Si de verdad no hay dato mostramos "N/D", nunca vacío.
const pct = (v: number | null | undefined) => formatRatioPct(v)

export default function ShelfPage() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null)
  const [rows, setRows] = useState<SearchRow[]>([])
  const [canalFilter, setCanalFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      fetchJson<SummaryResponse>('/api/shelf/summary'),
      fetchJson<{ rows: SearchRow[] }>(
        `/api/shelf/searches${canalFilter ? `?canal=${encodeURIComponent(canalFilter)}` : ''}`
      ),
    ])
      .then(([s, d]) => {
        if (cancelled) return
        setSummary(s)
        setRows(d.rows ?? [])
      })
      .catch((err) => {
        if (cancelled) return
        setError(errorMessage(err))
        setSummary(null)
        setRows([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [canalFilter])

  const canales = summary ? Object.keys(summary.byCanal).sort() : []

  return (
    <div className="space-y-6">
      {error && (
        <div className="nike-card border border-red-200 bg-red-50 flex items-start gap-3">
          <AlertTriangle size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-red-700">No se pudieron cargar los datos de Share of Shelf</p>
            <p className="text-xs text-red-600 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/*
        Diagnóstico de marcas. Sólo aparece si el endpoint no pudo resolver
        alguna de las tres marcas: entonces muestra los valores CRUDOS de
        `marca` que sí hay en la base, con su hexadecimal, que es lo que
        permite ver los espacios invisibles que causaron el bug original
        (' Nike ' pasaba INITCAP pero no el filtro). Así el próximo valor raro
        se diagnostica desde la propia pantalla, sin abrir la base.
      */}
      {summary?.diagnostics && summary.diagnostics.marcasFaltantes.length > 0 && (
        <div className="nike-card border border-amber-200 bg-amber-50">
          <div className="flex items-start gap-3">
            <AlertTriangle size={18} className="text-amber-600 flex-shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-sm font-bold text-amber-800">
                Faltan datos de {summary.diagnostics.marcasFaltantes.join(', ')}
              </p>
              <p className="text-xs text-amber-700 mt-0.5">{summary.diagnostics.message}</p>
              <div className="overflow-x-auto mt-2">
                <table className="text-[11px]">
                  <thead>
                    <tr className="text-left text-amber-700">
                      <th className="pr-4 py-1">Valor de marca</th>
                      <th className="pr-4 py-1">Hex (UTF-8)</th>
                      <th className="pr-4 py-1">Filas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.diagnostics.marcasEnLaBase.map((m) => (
                      <tr key={m.hex} className="text-amber-900">
                        <td className="pr-4 py-0.5 font-mono">{JSON.stringify(m.raw)}</td>
                        <td className="pr-4 py-0.5 font-mono">{m.hex}</td>
                        <td className="pr-4 py-0.5 tabular-nums">{m.n}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* KPIs globales */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {MARCAS_KPI.map((m) => {
          const vis = summary?.visibility?.[m.key]
          return (
            <KPICard
              key={m.key}
              title={`Visibilidad ${m.label}`}
              value={pct(vis ? vis.value : summary?.global?.[m.key])}
              subtitle={
                vis && vis.terms > 0
                  ? `Promedio en ${vis.terms.toLocaleString('es-AR')} búsquedas`
                  : 'Promedio en los buscadores de los retailers'
              }
              hint={VISIBILIDAD_HINT}
              icon={<Search size={18} />}
              color={MARCA_COLOR[m.label]}
              loading={loading}
            />
          )
        })}
        <KPICard
          title="Retailers"
          value={canales.length.toString()}
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
        {!loading && canales.length === 0 && (
          <p className="text-xs text-gray-400 py-6 text-center">
            {error ? 'Sin datos por el error de arriba.' : 'Sin datos de búsquedas todavía.'}
          </p>
        )}
        <div className="space-y-4">
          {canales.map((canal) => {
            const marcas = [...summary!.byCanal[canal]].sort((a, b) => b.pct - a.pct)
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
