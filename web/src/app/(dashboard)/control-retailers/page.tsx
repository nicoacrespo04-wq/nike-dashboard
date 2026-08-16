'use client'

import { useEffect, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { AlertTriangle, ChevronLeft, ChevronRight } from 'lucide-react'
import KPICard from '@/components/ui/KPICard'
import { formatPrice, cn } from '@/lib/utils'
import { fetchJson, errorMessage } from '@/lib/fetchJson'


// ── Tipos de las respuestas de la API ─────────────────────────
/** Un número que Postgres puede devolver como string. */
type Numeric = number | string | null

interface RetailerCompliance {
  scraper: string
  total: Numeric
  with_pvp: Numeric
  compliant: Numeric
  below_pvp: Numeric
  above_pvp: Numeric
  without_price: Numeric
  compliance_pct: number | null
}

interface PvpViolation {
  scraper: string
  style_color: string | null
  marketing_name: string | null
  franchise_scrapper: string | null
  competitor_final_price: number | null
  precio_sugerido: number | null
  diff_pct: Numeric
  link_pdp_competitor: string | null
}

interface PvpResponse {
  retailers: RetailerCompliance[]
  violations: PvpViolation[]
  violations_total: Numeric
  violations_page_size: Numeric
  violations_total_pages: Numeric
}

interface MarkdownByMarca {
  marca: string
  promo_pct: Numeric
  avg_markdown_pct: Numeric
  in_promo: Numeric
}

interface MarkdownByRetailer {
  scraper: string
  marca: string
  promo_pct: Numeric
}

interface TopMarkdown {
  marca: string
  scraper: string
  style_color: string | null
  product_name_competitor: string | null
  franchise_competitor: string | null
  competitor_full_price: number | null
  competitor_final_price: number | null
  markdown_pct: Numeric
  bml_final_price: string | null
}

interface MarkdownResponse {
  by_marca: MarkdownByMarca[]
  by_retailer: MarkdownByRetailer[]
  top_markdowns: TopMarkdown[]
}

const num = (v: Numeric | undefined): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

// Filas por página de la tabla de violaciones. El KPI NO usa este número:
// usa `violations_total`, que viene de un COUNT(*) sin LIMIT.
const VIOLATIONS_PAGE_SIZE = 50

export default function ControlRetailersPage() {
  const [pvp, setPvp]           = useState<PvpResponse | null>(null)
  const [markdown, setMarkdown] = useState<MarkdownResponse | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [tab, setTab]           = useState<'pvp'|'markdown'|'bml'>('pvp')
  const [violationsPage, setViolationsPage] = useState(1)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      fetchJson<PvpResponse>(`/api/pricing/pvp-compliance?page=${violationsPage}&pageSize=${VIOLATIONS_PAGE_SIZE}`),
      fetchJson<MarkdownResponse>('/api/pricing/markdown-analysis'),
    ])
      .then(([p, m]) => {
        if (cancelled) return
        setPvp(p); setMarkdown(m)
      })
      .catch(err => {
        if (cancelled) return
        setError(errorMessage(err))
        setPvp(null); setMarkdown(null)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [violationsPage])

  const retailers   = pvp?.retailers   ?? []
  const violations  = pvp?.violations  ?? []
  // Total REAL de violaciones (COUNT(*) en el endpoint). Antes el KPI usaba
  // `violations.length`, que estaba topeado por el LIMIT de la tabla y por eso
  // siempre decía 100.
  const violationsTotal = num(pvp?.violations_total)
  const violationsPageSize = num(pvp?.violations_page_size) || VIOLATIONS_PAGE_SIZE
  const violationsTotalPages = num(pvp?.violations_total_pages)
  const violationsFrom = violationsTotal === 0 ? 0 : (violationsPage - 1) * violationsPageSize + 1
  const violationsTo   = Math.min(violationsPage * violationsPageSize, violationsTotal)
  const byMarca     = markdown?.by_marca    ?? []
  const byRetailer  = markdown?.by_retailer ?? []
  const topMarkdowns = markdown?.top_markdowns ?? []

  const avgCompliance = retailers.length > 0
    ? Math.round(retailers.reduce((s, r) => s + (r.compliance_pct ?? 0), 0) / retailers.length)
    : 0

  const nikeMarkdown   = byMarca.find((m) => m.marca === 'NIKE')
  const adidasMarkdown = byMarca.find((m) => m.marca === 'ADIDAS')
  const pumaMarkdown   = byMarca.find((m) => m.marca === 'PUMA')

  function ComplianceBadge({ pct }: { pct: number | null }) {
    if (pct === null) return <span className="text-xs text-gray-400">Sin datos</span>
    const color = pct >= 90 ? 'bg-green-100 text-green-700' : pct >= 70 ? 'bg-yellow-100 text-yellow-700' : 'bg-red-100 text-red-700'
    const dot   = pct >= 90 ? 'bg-green-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-red-500'
    return (
      <span className={cn('inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold', color)}>
        <span className={cn('w-1.5 h-1.5 rounded-full', dot)} />
        {pct}%
      </span>
    )
  }

  return (
    <div className="space-y-6">

      {error && (
        <div className="nike-card border border-red-200 bg-red-50 flex items-start gap-3">
          <AlertTriangle size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-red-700">No se pudieron cargar los datos de Control de Retailers</p>
            <p className="text-xs text-red-600 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <KPICard loading={loading} title="Cumplimiento PVP promedio" value={`${avgCompliance}%`}
          subtitle="todos los retailers Nike" color={avgCompliance >= 90 ? '#27AE60' : avgCompliance >= 70 ? '#F5A623' : '#E31837'} />
        <KPICard loading={loading} title="Violaciones PVP" value={violationsTotal.toLocaleString('es-AR')}
          subtitle="SKUs por debajo del precio sugerido" color="#E31837" />
        <KPICard loading={loading} title="Catálogo Nike en promo"
          value={nikeMarkdown ? `${nikeMarkdown.promo_pct ?? 'N/D'}%` : 'N/D'}
          subtitle={`~${nikeMarkdown?.avg_markdown_pct ?? 0}% descuento promedio`} color="#F5A623" />
        <KPICard loading={loading} title="Catálogo Adidas en promo"
          value={adidasMarkdown ? `${adidasMarkdown.promo_pct ?? 'N/D'}%` : 'N/D'}
          subtitle="vs Nike en retailers compartidos" color="#0046CC" />
        <KPICard loading={loading} title="Catálogo Puma en promo"
          value={pumaMarkdown ? `${pumaMarkdown.promo_pct ?? 'N/D'}%` : 'N/D'}
          subtitle="vs Nike en retailers compartidos" color="#E4032E" />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {([['pvp','Control PVP'],['markdown','Análisis Markdown'],['bml','BML vs Nike.com.ar']] as const).map(([val, label]) => (
          <button key={val} onClick={() => setTab(val)}
            className={`px-5 py-3 text-sm font-semibold transition-all border-b-2 -mb-px ${
              tab === val ? 'border-[#E31837] text-[#111111]' : 'border-transparent text-gray-400 hover:text-gray-700'
            }`}
          >{label}</button>
        ))}
      </div>

      {/* TAB: Control PVP */}
      {tab === 'pvp' && (
        <div className="space-y-4">
          <div className="nike-card !p-0 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100">
              <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">Cumplimiento por Retailer</h2>
              <p className="text-xs text-gray-400 mt-0.5">
                Verde ≥90% · Amarillo 70-90% · Rojo &lt;70% · Cumplen + Bajo + Sobre = Con PVP
              </p>
            </div>
            <table className="nike-table">
              <thead><tr>
                <th>Retailer</th>
                <th className="text-right">SKUs Nike</th>
                <th className="text-right">Con PVP</th>
                <th className="text-right">Cumplen</th>
                <th className="text-right">Bajo PVP</th>
                <th className="text-right">Sobre PVP</th>
                <th className="text-right">Sin precio</th>
                <th className="text-center">Cumplimiento</th>
              </tr></thead>
              <tbody>
                {loading
                  ? Array.from({length:8}).map((_,i) => <tr key={i} className="animate-pulse border-b"><td colSpan={8}><div className="h-3 bg-gray-200 rounded my-3 w-2/3 mx-3"/></td></tr>)
                  : retailers.map((r) => (
                    <tr key={r.scraper}>
                      <td className="font-semibold">{r.scraper}</td>
                      <td className="text-right font-mono">{num(r.total).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono">{num(r.with_pvp).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono text-green-600">{num(r.compliant).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono text-red-600">{num(r.below_pvp).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono text-yellow-600">{num(r.above_pvp).toLocaleString('es-AR')}</td>
                      {/* Filas con PVP pero sin precio usable (0 o inflado por
                          cuotas): quedan fuera de los 3 buckets a propósito. */}
                      <td className="text-right font-mono text-gray-400" title="Con PVP pero sin precio de competidor válido (N/D)">
                        {num(r.without_price).toLocaleString('es-AR')}
                      </td>
                      <td className="text-center"><ComplianceBadge pct={r.compliance_pct} /></td>
                    </tr>
                  ))
                }
              </tbody>
            </table>
          </div>

          {!loading && violationsTotal > 0 && (
            <div className="nike-card !p-0 overflow-hidden">
              <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-red-500" />
                <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">Violaciones de Precio Sugerido</h2>
                <span className="text-xs text-gray-400 ml-auto">
                  Mostrando {violationsFrom.toLocaleString('es-AR')}–{violationsTo.toLocaleString('es-AR')} de {violationsTotal.toLocaleString('es-AR')} productos
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="nike-table">
                  <thead><tr><th>Retailer</th><th>StyleColor</th><th>Producto</th><th>Franchise</th><th className="text-right">Precio Comp.</th><th className="text-right">PVP Sugerido</th><th className="text-right">Diferencia</th><th>Link</th></tr></thead>
                  <tbody>
                    {violations.map((v, i) => (
                      <tr key={`${v.scraper}-${v.style_color}-${i}`}>
                        <td><span className="text-xs bg-red-50 text-red-700 px-2 py-0.5 rounded font-medium">{v.scraper}</span></td>
                        <td className="font-mono text-xs text-gray-600">{v.style_color ?? '—'}</td>
                        <td className="text-gray-800">{v.marketing_name ?? '—'}</td>
                        <td className="text-xs text-gray-500">{v.franchise_scrapper ?? '—'}</td>
                        {/* formatPrice devuelve "N/D" para null: nunca "$0". */}
                        <td className="text-right text-red-600 font-semibold">{formatPrice(v.competitor_final_price)}</td>
                        <td className="text-right text-gray-600">{formatPrice(v.precio_sugerido)}</td>
                        <td className="text-right font-bold text-red-600">{v.diff_pct != null ? `${v.diff_pct}%` : 'N/D'}</td>
                        <td>{v.link_pdp_competitor ? <a href={v.link_pdp_competitor} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-500 hover:underline">Ver →</a> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {violationsTotalPages > 1 && (
                <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-between">
                  <span className="text-xs text-gray-400">
                    Página {violationsPage} de {violationsTotalPages.toLocaleString('es-AR')}
                  </span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setViolationsPage(p => Math.max(1, p - 1))}
                      disabled={violationsPage <= 1}
                      className="inline-flex items-center gap-1 text-xs border border-gray-200 rounded-lg px-3 py-1.5 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <ChevronLeft size={13} /> Anterior
                    </button>
                    <button
                      onClick={() => setViolationsPage(p => Math.min(violationsTotalPages, p + 1))}
                      disabled={violationsPage >= violationsTotalPages}
                      className="inline-flex items-center gap-1 text-xs border border-gray-200 rounded-lg px-3 py-1.5 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Siguiente <ChevronRight size={13} />
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB: Markdown */}
      {tab === 'markdown' && (
        <div className="space-y-4">
          {/* Markdown por marca */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {[
              { marca: 'NIKE',  data: nikeMarkdown,   color: '#111111' },
              { marca: 'ADIDAS', data: adidasMarkdown, color: '#0046CC' },
              { marca: 'PUMA',   data: pumaMarkdown,   color: '#E4032E' },
            ].map(({ marca, data: d, color }) => (
              <div key={marca} className="nike-card">
                <p className="text-xs font-bold uppercase tracking-wider mb-3" style={{ color }}>{marca}</p>
                <p className="text-3xl font-bold text-gray-900 mb-1">
                  {d?.promo_pct != null ? `${d.promo_pct}%` : 'N/D'}
                </p>
                <p className="text-xs text-gray-400">catálogo en promo</p>
                <div className="mt-3 pt-3 border-t border-gray-100 flex justify-between text-xs">
                  <span className="text-gray-500">Dto. promedio</span>
                  <strong className="text-orange-600">
                    {d?.avg_markdown_pct != null ? `${d.avg_markdown_pct}%` : 'N/D'}
                  </strong>
                </div>
                <div className="flex justify-between text-xs mt-1">
                  <span className="text-gray-500">SKUs en promo</span>
                  <strong>{num(d?.in_promo).toLocaleString('es-AR')}</strong>
                </div>
              </div>
            ))}
          </div>

          {/* Top markdowns */}
          <div className="nike-card !p-0 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100">
              <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">Top Descuentos Activos</h2>
            </div>
            <div className="overflow-x-auto">
            <table className="nike-table">
              <thead><tr><th>Marca</th><th>StyleColor</th><th>Producto</th><th>Franchise</th><th>Retailer</th><th className="text-right">Precio Original</th><th className="text-right">Precio Final</th><th className="text-right">Descuento</th><th className="text-center">BML</th></tr></thead>
              <tbody>
                {loading
                  ? Array.from({length:8}).map((_,i) => <tr key={i} className="animate-pulse border-b"><td colSpan={9}><div className="h-3 bg-gray-200 rounded my-3 w-2/3 mx-3"/></td></tr>)
                  : topMarkdowns.slice(0,50).map((p, i) => (
                    <tr key={i}>
                      <td><span className={`text-[10px] font-bold px-2 py-0.5 rounded ${p.marca === 'NIKE' ? 'bg-gray-900 text-white' : p.marca === 'ADIDAS' ? 'bg-blue-100 text-blue-800' : 'bg-orange-100 text-orange-800'}`}>{p.marca}</span></td>
                      <td className="font-mono text-[11px] text-gray-600">{p.style_color ?? '—'}</td>
                      <td className="text-gray-800 text-xs">{p.product_name_competitor?.slice(0,35) ?? '—'}</td>
                      <td className="text-xs text-gray-500">{p.franchise_competitor ?? '—'}</td>
                      <td className="text-xs"><span className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded">{p.scraper}</span></td>
                      <td className="text-right text-xs text-gray-400 line-through">{formatPrice(p.competitor_full_price)}</td>
                      <td className="text-right text-xs font-semibold text-green-600">{formatPrice(p.competitor_final_price)}</td>
                      <td className="text-right font-bold text-orange-600">{p.markdown_pct}%</td>
                      <td className="text-center"><span className={`text-[10px] px-2 py-0.5 rounded font-semibold ${p.bml_final_price === 'BEAT' ? 'bg-red-100 text-red-700' : p.bml_final_price === 'MEET' ? 'bg-yellow-100 text-yellow-700' : p.bml_final_price === 'LOSE' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>{p.bml_final_price ?? 'N/D'}</span></td>
                    </tr>
                  ))
                }
              </tbody>
            </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB: BML */}
      {tab === 'bml' && (
        <div className="space-y-4">
          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-4">Markdown por Retailer y Marca</h2>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart
                data={byRetailer.slice(0, 20)}
                margin={{ top: 4, right: 16, left: 0, bottom: 40 }}
              >
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F0F0F0" />
                <XAxis dataKey="scraper" tick={{ fontSize: 10, fill: '#666' }} angle={-30} textAnchor="end" interval={0} height={60} />
                <YAxis tickFormatter={v => `${v}%`} tick={{ fontSize: 11, fill: '#9B9B9B' }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(v: number) => [`${v}%`, 'En promo']} />
                <Bar dataKey="promo_pct" fill="#F5A623" radius={[3,3,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

    </div>
  )
}
