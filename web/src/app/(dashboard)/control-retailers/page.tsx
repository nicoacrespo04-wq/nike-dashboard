'use client'

import { useEffect, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import KPICard from '@/components/ui/KPICard'
import { formatPrice, cn } from '@/lib/utils'

export default function ControlRetailersPage() {
  const [pvp, setPvp]           = useState<any>(null)
  const [markdown, setMarkdown] = useState<any>(null)
  const [loading, setLoading]   = useState(true)
  const [tab, setTab]           = useState<'pvp'|'markdown'|'bml'>('pvp')

  useEffect(() => {
    Promise.all([
      fetch('/api/pricing/pvp-compliance').then(r => r.json()),
      fetch('/api/pricing/markdown-analysis').then(r => r.json()),
    ]).then(([p, m]) => { setPvp(p); setMarkdown(m) })
    .finally(() => setLoading(false))
  }, [])

  const retailers   = pvp?.retailers   ?? []
  const violations  = pvp?.violations  ?? []
  const byMarca     = markdown?.by_marca    ?? []
  const byRetailer  = markdown?.by_retailer ?? []
  const topMarkdowns = markdown?.top_markdowns ?? []

  const avgCompliance = retailers.length > 0
    ? Math.round(retailers.reduce((s: number, r: any) => s + (r.compliance_pct ?? 0), 0) / retailers.length)
    : 0

  const nikeMarkdown  = byMarca.find((m: any) => m.marca === 'NIKE')
  const adidasMarkdown = byMarca.find((m: any) => m.marca === 'ADIDAS')
  const pumaMarkdown  = byMarca.find((m: any) => m.marca === 'PUMA')

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

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard loading={loading} title="Cumplimiento PVP promedio" value={`${avgCompliance}%`}
          subtitle="todos los retailers Nike" color={avgCompliance >= 90 ? '#27AE60' : avgCompliance >= 70 ? '#F5A623' : '#E31837'} />
        <KPICard loading={loading} title="Violaciones PVP" value={violations.length}
          subtitle="retailers vendiendo bajo precio sugerido" color="#E31837" />
        <KPICard loading={loading} title="Catálogo Nike en promo"
          value={nikeMarkdown ? `${nikeMarkdown.promo_pct}%` : '—'}
          subtitle={`~${nikeMarkdown?.avg_markdown_pct ?? 0}% descuento promedio`} color="#F5A623" />
        <KPICard loading={loading} title="Catálogo Adidas en promo"
          value={adidasMarkdown ? `${adidasMarkdown.promo_pct}%` : '—'}
          subtitle="vs Nike en retailers compartidos" color="#0046CC" />
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
              <p className="text-xs text-gray-400 mt-0.5">Verde ≥90% · Amarillo 70-90% · Rojo &lt;70%</p>
            </div>
            <table className="nike-table">
              <thead><tr>
                <th>Retailer</th>
                <th className="text-right">SKUs Nike</th>
                <th className="text-right">Con PVP</th>
                <th className="text-right">Cumplen</th>
                <th className="text-right">Bajo PVP</th>
                <th className="text-right">Sobre PVP</th>
                <th className="text-center">Cumplimiento</th>
              </tr></thead>
              <tbody>
                {loading
                  ? Array.from({length:8}).map((_,i) => <tr key={i} className="animate-pulse border-b"><td colSpan={7}><div className="h-3 bg-gray-200 rounded my-3 w-2/3 mx-3"/></td></tr>)
                  : retailers.map((r: any) => (
                    <tr key={r.scraper}>
                      <td className="font-semibold">{r.scraper}</td>
                      <td className="text-right font-mono">{Number(r.total).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono">{Number(r.with_pvp).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono text-green-600">{Number(r.compliant).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono text-red-600">{Number(r.below_pvp).toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono text-yellow-600">{Number(r.above_pvp).toLocaleString('es-AR')}</td>
                      <td className="text-center"><ComplianceBadge pct={r.compliance_pct} /></td>
                    </tr>
                  ))
                }
              </tbody>
            </table>
          </div>

          {violations.length > 0 && (
            <div className="nike-card !p-0 overflow-hidden">
              <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-red-500" />
                <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">Violaciones de Precio Sugerido</h2>
                <span className="text-xs text-gray-400 ml-auto">{violations.length} productos</span>
              </div>
              <table className="nike-table">
                <thead><tr><th>Retailer</th><th>Producto</th><th>Franchise</th><th className="text-right">Precio Comp.</th><th className="text-right">PVP Sugerido</th><th className="text-right">Diferencia</th><th>Link</th></tr></thead>
                <tbody>
                  {violations.slice(0,50).map((v: any, i: number) => (
                    <tr key={i}>
                      <td><span className="text-xs bg-red-50 text-red-700 px-2 py-0.5 rounded font-medium">{v.scraper}</span></td>
                      <td className="text-gray-800">{v.marketing_name}</td>
                      <td className="text-xs text-gray-500">{v.franchise_scrapper ?? '—'}</td>
                      <td className="text-right text-red-600 font-semibold">{formatPrice(v.competitor_final_price)}</td>
                      <td className="text-right text-gray-600">{formatPrice(v.precio_sugerido)}</td>
                      <td className="text-right font-bold text-red-600">{v.diff_pct}%</td>
                      <td>{v.link_pdp_competitor ? <a href={v.link_pdp_competitor} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-500 hover:underline">Ver →</a> : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
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
                <p className="text-3xl font-bold text-gray-900 mb-1">{d?.promo_pct ?? '—'}%</p>
                <p className="text-xs text-gray-400">catálogo en promo</p>
                <div className="mt-3 pt-3 border-t border-gray-100 flex justify-between text-xs">
                  <span className="text-gray-500">Dto. promedio</span>
                  <strong className="text-orange-600">{d?.avg_markdown_pct ?? '—'}%</strong>
                </div>
                <div className="flex justify-between text-xs mt-1">
                  <span className="text-gray-500">SKUs en promo</span>
                  <strong>{Number(d?.in_promo ?? 0).toLocaleString('es-AR')}</strong>
                </div>
              </div>
            ))}
          </div>

          {/* Top markdowns */}
          <div className="nike-card !p-0 overflow-hidden">
            <div className="px-5 py-4 border-b border-gray-100">
              <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">Top Descuentos Activos</h2>
            </div>
            <table className="nike-table">
              <thead><tr><th>Marca</th><th>Producto</th><th>Franchise</th><th>Retailer</th><th className="text-right">Precio Original</th><th className="text-right">Precio Final</th><th className="text-right">Descuento</th><th className="text-center">BML</th></tr></thead>
              <tbody>
                {loading
                  ? Array.from({length:8}).map((_,i) => <tr key={i} className="animate-pulse border-b"><td colSpan={8}><div className="h-3 bg-gray-200 rounded my-3 w-2/3 mx-3"/></td></tr>)
                  : topMarkdowns.slice(0,50).map((p: any, i: number) => (
                    <tr key={i}>
                      <td><span className={`text-[10px] font-bold px-2 py-0.5 rounded ${p.marca === 'NIKE' ? 'bg-gray-900 text-white' : p.marca === 'ADIDAS' ? 'bg-blue-100 text-blue-800' : 'bg-orange-100 text-orange-800'}`}>{p.marca}</span></td>
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
      )}

      {/* TAB: BML */}
      {tab === 'bml' && (
        <div className="space-y-4">
          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-4">Markdown por Retailer y Marca</h2>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart
                data={byRetailer.filter((r: any) => !['ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','URU','USA'].includes(r.scraper)).slice(0,20)}
                margin={{ top: 4, right: 16, left: 0, bottom: 40 }}
              >
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F0F0F0" />
                <XAxis dataKey="scraper" tick={{ fontSize: 10, fill: '#666' }} angle={-30} textAnchor="end" interval={0} height={60} />
                <YAxis tickFormatter={v => `${v}%`} tick={{ fontSize: 11, fill: '#9B9B9B' }} axisLine={false} tickLine={false} />
                <Tooltip formatter={(v: any) => [`${v}%`, 'En promo']} />
                <Bar dataKey="promo_pct" fill="#F5A623" radius={[3,3,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

    </div>
  )
}
