'use client'

import { useEffect, useState, useCallback } from 'react'
import { BarChart2, Filter, RefreshCw, AlertTriangle } from 'lucide-react'
import KPICard from '@/components/ui/KPICard'
import BMLDonut from '@/components/charts/BMLDonut'
import FranchiseBar from '@/components/charts/FranchiseBar'
import PricingTable from '@/components/tables/PricingTable'
import { formatPrice, formatPct } from '@/lib/utils'
import { fetchJson, errorMessage } from '@/lib/fetchJson'

// ── Types ─────────────────────────────────────────────────────
interface Summary {
  kpis: { 
    adidas_total: number
    puma_total: number
    nike_total: number
    total_beat: number
    total_meet: number
    total_lose: number
    adidas_avg_price: number
    puma_avg_price: number
    nike_avg_price: number
  }
  bml_adidas: { beat: number; meet: number; lose: number; nd: number }
  bml_puma:   { beat: number; meet: number; lose: number; nd: number }
  top_adidas: any[]
  top_puma:   any[]
}

interface Filters {
  marca:    string
  division: string
  canal:    string
  search:   string
}

interface FranchiseRow {
  franchise:   string
  marca:       string
  division:    string | null
  count:       number | string
  avg_price:   number | string | null
  promo_pct:   number | string | null
  in_promo:    number | string | null
  avg_gap_pct: number | string | null
  beat:        number | string
  meet:        number | string
  lose:        number | string
}

interface FranchisesResponse {
  franchises: FranchiseRow[]
  categories: string[]
}

const DIVISIONES = [
  { value: '',         label: 'Todas las divisiones' },
  { value: 'FOOTWEAR', label: 'Footwear' },
  { value: 'APPAREL',  label: 'Apparel' },
  { value: 'EQUIP',    label: 'Equipment' },
]
const CANALES = [
  { value: '',    label: 'Todos los canales' },
  { value: 'd2c', label: 'D2C (adidas.com.ar / ar.puma.com)' },
  { value: 'b2b', label: 'B2B (Retailers)' },
]

// ── Component ────────────────────────────────────────────────
export default function CompetenciaPage() {
  const [summary, setSummary]     = useState<Summary | null>(null)
  const [franchises, setFranchises] = useState<any[]>([])
  const [products, setProducts]   = useState<any[]>([])
  const [totalProds, setTotalProds] = useState(0)
  const [page, setPage]           = useState(1)
  const [loading, setLoading]     = useState(true)
  const [loadingProds, setLoadingProds] = useState(false)
  const [selectedFranchise, setSelectedFranchise] = useState('')
  const [activeTab, setActiveTab] = useState<'adidas' | 'PUMA' | 'both'>('both')
  const [filters, setFilters]     = useState<Filters>({ marca: '', division: '', canal: '', search: '' })
  const [error, setError]         = useState<string | null>(null)

  // ── Top Franchises Nike (bloque propio, con su filtro de categoría) ──
  const [nikeFranchises, setNikeFranchises] = useState<FranchiseRow[]>([])
  const [nikeCategories, setNikeCategories] = useState<string[]>([])
  const [nikeCategory, setNikeCategory]     = useState('')
  const [loadingNike, setLoadingNike]       = useState(true)
  const [nikeError, setNikeError]           = useState<string | null>(null)

  // Fetch summary (KPIs + BML + top franchises)
  useEffect(() => {
    let cancelled = false
    fetchJson<Summary>('/api/pricing/summary')
      .then(d => { if (!cancelled) { setSummary(d); setError(null) } })
      .catch(err => { if (!cancelled) { setError(errorMessage(err)); setSummary(null) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  // Fetch franchises cuando cambian filtros
  const fetchFranchises = useCallback(() => {
    const params = new URLSearchParams()
    if (filters.marca)    params.set('marca',    filters.marca)
    if (filters.division) params.set('division', filters.division)
    if (filters.canal)    params.set('canal',    filters.canal)
    fetchJson<FranchisesResponse>(`/api/pricing/franchises?${params}`)
      .then(d => { setFranchises(d.franchises ?? []); setError(null) })
      .catch(err => { setError(errorMessage(err)); setFranchises([]) })
  }, [filters])

  useEffect(() => { fetchFranchises() }, [fetchFranchises])

  // Fetch franchises Nike — independiente de los filtros de arriba (que son
  // de Adidas/Puma); sólo lo afecta el filtro de categoría de su propio bloque.
  useEffect(() => {
    let cancelled = false
    setLoadingNike(true)
    const params = new URLSearchParams({ marca: 'NIKE' })
    if (nikeCategory) params.set('category', nikeCategory)
    fetchJson<FranchisesResponse>(`/api/pricing/franchises?${params}`)
      .then(d => {
        if (cancelled) return
        setNikeFranchises(d.franchises ?? [])
        setNikeCategories(d.categories ?? [])
        setNikeError(null)
      })
      .catch(err => {
        if (cancelled) return
        setNikeError(errorMessage(err))
        setNikeFranchises([])
      })
      .finally(() => { if (!cancelled) setLoadingNike(false) })
    return () => { cancelled = true }
  }, [nikeCategory])

  // Fetch products
  const fetchProducts = useCallback(() => {
    setLoadingProds(true)
    const params = new URLSearchParams({ page: String(page), pageSize: '50' })
    if (filters.marca)        params.set('marca',     filters.marca)
    if (filters.division)     params.set('division',  filters.division)
    if (filters.canal)        params.set('canal',     filters.canal)
    if (filters.search)       params.set('search',    filters.search)
    if (selectedFranchise)    params.set('franchise', selectedFranchise)
    if (!filters.marca)       params.set('marca',     activeTab === 'adidas' ? 'ADIDAS' : activeTab === 'PUMA' ? 'PUMA' : '')
    fetchJson<{ products: any[]; total: number }>(`/api/pricing/products?${params}`)
      .then(d => { setProducts(d.products ?? []); setTotalProds(d.total ?? 0); setError(null) })
      .catch(err => { setError(errorMessage(err)); setProducts([]); setTotalProds(0) })
      .finally(() => setLoadingProds(false))
  }, [page, filters, selectedFranchise, activeTab])

  useEffect(() => { fetchProducts() }, [fetchProducts])

  const bmlAdidas = summary?.bml_adidas ?? { beat: 0, meet: 0, lose: 0, nd: 0 }
  const bmlPuma   = summary?.bml_puma   ?? { beat: 0, meet: 0, lose: 0, nd: 0 }
  const totalBeat = Number(summary?.kpis?.total_beat ?? 0)
  const totalMeet = Number(summary?.kpis?.total_meet ?? 0)
  const totalLose = Number(summary?.kpis?.total_lose ?? 0)
  const totalAll  = totalBeat + totalMeet + totalLose
  // BEAT = Nike más barato (gana) · LOSE = Nike más caro (pierde). Ver lib/utils.ts.
  const beatPct   = totalAll > 0 ? Math.round((totalBeat / totalAll) * 100) : 0
  const losePct   = totalAll > 0 ? Math.round((totalLose / totalAll) * 100) : 0

  const adidasFranchises = franchises.filter(f => f.marca === 'ADIDAS')
  const pumaFranchises   = franchises.filter(f => f.marca === 'PUMA')

  return (
    <div className="space-y-6">

      {error && (
        <div className="nike-card border border-red-200 bg-red-50 flex items-start gap-3">
          <AlertTriangle size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-red-700">No se pudieron cargar los datos de Competencia</p>
            <p className="text-xs text-red-600 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <KPICard
          loading={loading}
          title="SKUs Únicos Nike"
          value={Number(summary?.kpis?.nike_total ?? 0).toLocaleString('es-AR')}
          subtitle={`Precio prom. ${formatPrice(summary?.kpis?.nike_avg_price)}`}
          color="#111111"
        />
        <KPICard
          loading={loading}
          title="SKUs Únicos Adidas"
          value={Number(summary?.kpis?.adidas_total ?? 0).toLocaleString('es-AR')}
          subtitle={`Precio prom. ${formatPrice(summary?.kpis?.adidas_avg_price)}`}
          color="#0046CC"
        />
        <KPICard
          loading={loading}
          title="SKUs Únicos Puma"
          value={Number(summary?.kpis?.puma_total ?? 0).toLocaleString('es-AR')}
          subtitle={`Precio prom. ${formatPrice(summary?.kpis?.puma_avg_price)}`}
          color="#E4032E"
        />
        <KPICard
          loading={loading}
          title="LOSE Nike"
          value={`${losePct}%`}
          subtitle={`Competencia más barata`}
          color="#E31837"
          valueSize="md"
        />
        <KPICard
          loading={loading}
          title="Nike Gana"
          value={`${beatPct}%`}
          subtitle={`Nike más barato`}
          color="#27AE60"
          valueSize="md"
        />
      </div>

      {/* ── Filtros ── */}
      <div className="nike-card flex flex-wrap gap-3 items-center">
        <Filter size={15} className="text-gray-400" />
        <select
          value={filters.marca}
          onChange={e => { setFilters(f => ({ ...f, marca: e.target.value })); setPage(1) }}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:border-gray-400"
        >
          <option value="">Adidas + Puma</option>
          <option value="ADIDAS">Solo Adidas</option>
          <option value="PUMA">Solo Puma</option>
        </select>

        <select
          value={filters.division}
          onChange={e => { setFilters(f => ({ ...f, division: e.target.value })); setPage(1) }}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:border-gray-400"
        >
          {DIVISIONES.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
        </select>

        <select
          value={filters.canal}
          onChange={e => { setFilters(f => ({ ...f, canal: e.target.value })); setPage(1) }}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:border-gray-400"
        >
          {CANALES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>

        <input
          type="text"
          placeholder="Buscar producto, franchise, SKU..."
          value={filters.search}
          onChange={e => { setFilters(f => ({ ...f, search: e.target.value })); setPage(1) }}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:border-gray-400 flex-1 min-w-48"
        />

        {selectedFranchise && (
          <button
            onClick={() => setSelectedFranchise('')}
            className="text-xs text-red-600 border border-red-200 rounded-lg px-3 py-2 hover:bg-red-50"
          >
            ✕ {selectedFranchise}
          </button>
        )}
      </div>

      {/* ── BML Donuts + Franchise Bars ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Adidas */}
        <div className="nike-card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#0046CC] inline-block" />
              Adidas — BML Distribution
            </h2>
            <span className="text-xs text-gray-400">{adidasFranchises.length} franchises</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <BMLDonut data={{ beat: Number(bmlAdidas.beat), meet: Number(bmlAdidas.meet), lose: Number(bmlAdidas.lose), nd: Number(bmlAdidas.nd) }} size={180} />
            <div className="flex flex-col justify-center gap-2 text-xs">
              {[
                { label: 'BEAT (Nike más barato)', val: bmlAdidas.beat, color: '#27AE60' },
                { label: 'MEET (precio similar)',   val: bmlAdidas.meet, color: '#F5A623' },
                { label: 'LOSE (Nike más caro)',  val: bmlAdidas.lose, color: '#E31837' },
                { label: 'Sin datos',               val: bmlAdidas.nd,   color: '#9B9B9B' },
              ].map(item => (
                <div key={item.label} className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-gray-600">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: item.color }} />
                    {item.label}
                  </span>
                  <strong>{Number(item.val).toLocaleString('es-AR')}</strong>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Puma */}
        <div className="nike-card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#E4032E] inline-block" />
              Puma — BML Distribution
            </h2>
            <span className="text-xs text-gray-400">{pumaFranchises.length} franchises</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <BMLDonut data={{ beat: Number(bmlPuma.beat), meet: Number(bmlPuma.meet), lose: Number(bmlPuma.lose), nd: Number(bmlPuma.nd) }} size={180} />
            <div className="flex flex-col justify-center gap-2 text-xs">
              {[
                { label: 'BEAT (Nike más barato)', val: bmlPuma.beat, color: '#27AE60' },
                { label: 'MEET (precio similar)',   val: bmlPuma.meet, color: '#F5A623' },
                { label: 'LOSE (Nike más caro)',  val: bmlPuma.lose, color: '#E31837' },
                { label: 'Sin datos',               val: bmlPuma.nd,   color: '#9B9B9B' },
              ].map(item => (
                <div key={item.label} className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-gray-600">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ background: item.color }} />
                    {item.label}
                  </span>
                  <strong>{Number(item.val).toLocaleString('es-AR')}</strong>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ── Top Franchises Charts ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="nike-card">
          <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1">
            Top Franchises — Adidas
          </h2>
          <p className="text-xs text-gray-400 mb-4">Click para filtrar tabla</p>
          <FranchiseBar
            data={adidasFranchises}
            marca="ADIDAS"
            onSelect={setSelectedFranchise}
            selectedFranchise={selectedFranchise}
            height={380}
          />
        </div>
        <div className="nike-card">
          <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1">
            Top Franchises — Puma
          </h2>
          <p className="text-xs text-gray-400 mb-4">Click para filtrar tabla</p>
          <FranchiseBar
            data={pumaFranchises}
            marca="PUMA"
            onSelect={setSelectedFranchise}
            selectedFranchise={selectedFranchise}
            height={380}
          />
        </div>
      </div>

      {/* ── Top Franchises Nike (con filtro por categoría) ── */}
      <div className="nike-card !p-0 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide flex items-center gap-2">
              <span className="w-3 h-3 rounded-full bg-[#E31837] inline-block" />
              Top Franchises Nike
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              SKUs únicos (StyleColor), precio promedio y % en promo
              {nikeCategory && ` · Categoría: ${nikeCategory}`}
            </p>
          </div>
          <select
            value={nikeCategory}
            onChange={e => setNikeCategory(e.target.value)}
            className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:border-gray-400"
          >
            <option value="">Todas las categorías</option>
            {nikeCategories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        {nikeError && (
          <div className="px-5 py-4 flex items-start gap-2 bg-red-50 border-b border-red-200">
            <AlertTriangle size={16} className="text-red-600 flex-shrink-0 mt-0.5" />
            <p className="text-xs text-red-600">{nikeError}</p>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="nike-table">
            <thead><tr>
              <th>Franchise</th>
              <th>División</th>
              <th className="text-right">SKUs</th>
              <th className="text-right">Precio Prom.</th>
              <th className="text-right">SKUs en promo</th>
              <th className="text-right">% en promo</th>
            </tr></thead>
            <tbody>
              {loadingNike
                ? Array.from({length:8}).map((_,i) => (
                    <tr key={i} className="animate-pulse border-b">
                      <td colSpan={6}><div className="h-3 bg-gray-200 rounded my-3 w-2/3 mx-3"/></td>
                    </tr>
                  ))
                : nikeFranchises.length === 0
                  ? <tr><td colSpan={6} className="text-center text-xs text-gray-400 py-6">
                      {nikeError ? 'Sin datos por el error de arriba.' : 'Sin franchises Nike para esta categoría.'}
                    </td></tr>
                  : nikeFranchises.slice(0, 20).map(f => (
                    <tr key={`${f.franchise}-${f.division ?? ''}`}>
                      <td className="font-medium">{f.franchise}</td>
                      <td className="text-xs text-gray-500">{f.division ?? '—'}</td>
                      <td className="text-right font-mono">{Number(f.count).toLocaleString('es-AR')}</td>
                      <td className="text-right">{formatPrice(f.avg_price != null ? Number(f.avg_price) : null)}</td>
                      <td className="text-right font-mono text-orange-600">
                        {Number(f.in_promo ?? 0) > 0 ? Number(f.in_promo).toLocaleString('es-AR') : '—'}
                      </td>
                      <td className="text-right font-bold text-orange-600">
                        {f.promo_pct != null ? `${f.promo_pct}%` : 'N/D'}
                      </td>
                    </tr>
                  ))
              }
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Tabla productos ── */}
      <div className="nike-card !p-0 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">
              Detalle de Productos
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {totalProds.toLocaleString('es-AR')} productos
              {selectedFranchise && ` · Franchise: ${selectedFranchise}`}
            </p>
          </div>
          {/* Tabs Adidas/Puma/Ambos */}
          <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
            {([['both','Ambas'],['adidas','Adidas'],['PUMA','PUMA']] as const).map(([val, label]) => (
              <button
                key={val}
                onClick={() => { setActiveTab(val); setPage(1) }}
                className={`px-4 py-2 transition-colors ${activeTab === val ? 'bg-[#111111] text-white' : 'text-gray-600 hover:bg-gray-50'}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="p-5">
          <PricingTable
            data={products}
            loading={loadingProds}
            totalCount={totalProds}
            page={page}
            pageSize={50}
            onPageChange={setPage}
          />
        </div>
      </div>

    </div>
  )
}
