'use client'

import { useEffect, useState, useCallback } from 'react'
import { Filter, AlertTriangle, Info } from 'lucide-react'
import KPICard from '@/components/ui/KPICard'
import BMLDonut from '@/components/charts/BMLDonut'
import FranchiseBar from '@/components/charts/FranchiseBar'
import PricingTable from '@/components/tables/PricingTable'
import { formatPrice } from '@/lib/utils'
import { fetchJson, errorMessage } from '@/lib/fetchJson'
import type { PricingRow } from '@/lib/db'
import type { FranchiseDataPoint } from '@/components/charts/FranchiseBar'

// ── Types ─────────────────────────────────────────────────────
interface BmlCounts {
  beat: number | string
  meet: number | string
  lose: number | string
  nd: number | string
}

interface UniverseOption {
  key: string
  label: string
  description: string
}

interface TopFranchise {
  franchise: string
  count: number | string
  avg_price: number | string | null
  avg_gap_pct: number | string | null
}

interface Summary {
  universe: string
  universeLabel: string
  universeDescription: string
  universes: UniverseOption[]
  kpis: {
    adidas_total: number
    puma_total: number
    nike_total: number
    total_beat: number
    total_meet: number
    total_lose: number
    adidas_avg_price: number | null
    puma_avg_price: number | null
    nike_avg_price: number | null
  }
  bml_adidas: BmlCounts
  bml_puma: BmlCounts
  top_adidas: TopFranchise[]
  top_puma: TopFranchise[]
}

interface Filters {
  marca: string
  division: string
  canal: string
  search: string
}

interface FranchiseRow {
  franchise: string
  marca: string
  division: string | null
  count: number | string
  avg_price: number | string | null
  promo_pct: number | string | null
  in_promo: number | string | null
  avg_gap_pct: number | string | null
  beat: number | string
  meet: number | string
  lose: number | string
}

interface FranchisesResponse {
  franchises: FranchiseRow[]
  categories: string[]
}

interface ProductsResponse {
  products: PricingRow[]
  total: number
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

// El KPI de SKUs sólo tiene sentido junto al universo donde se contó: sin eso
// invita a una comparación inválida (ver /api/pricing/summary y lib/scrapers.ts).
const SKU_HINT =
  'SKUs distintos de la marca observados en el universo elegido. Se cuenta el ' +
  'código propio del producto observado, no el style_color (que es el SKU del ' +
  'producto Nike de referencia de cada comparación).'

const toNumber = (v: number | string | null | undefined): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/**
 * `FranchiseBar` pide números; Postgres devuelve los agregados como string.
 * La conversión va acá y no en el gráfico (que es de otro dueño).
 */
const toDataPoints = (rows: FranchiseRow[]): FranchiseDataPoint[] =>
  rows.map(r => ({
    franchise: r.franchise,
    count: toNumber(r.count),
    avg_price: toNumber(r.avg_price),
    avg_gap_pct: toNumber(r.avg_gap_pct),
    beat: toNumber(r.beat),
    meet: toNumber(r.meet),
    lose: toNumber(r.lose),
  }))

const toBml = (b: BmlCounts | undefined) => ({
  beat: toNumber(b?.beat),
  meet: toNumber(b?.meet),
  lose: toNumber(b?.lose),
  nd: toNumber(b?.nd),
})

// ── Component ────────────────────────────────────────────────
export default function CompetenciaPage() {
  const [summary, setSummary]     = useState<Summary | null>(null)
  const [universe, setUniverse]   = useState<string>('')
  const [franchises, setFranchises] = useState<FranchiseRow[]>([])
  const [products, setProducts]   = useState<PricingRow[]>([])
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

  // Fetch summary (KPIs + BML + top franchises) para el universo elegido
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const qs = universe ? `?universe=${encodeURIComponent(universe)}` : ''
    fetchJson<Summary>(`/api/pricing/summary${qs}`)
      .then(d => { if (!cancelled) { setSummary(d); setError(null) } })
      .catch(err => { if (!cancelled) { setError(errorMessage(err)); setSummary(null) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [universe])

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
    fetchJson<ProductsResponse>(`/api/pricing/products?${params}`)
      .then(d => { setProducts(d.products ?? []); setTotalProds(d.total ?? 0); setError(null) })
      .catch(err => { setError(errorMessage(err)); setProducts([]); setTotalProds(0) })
      .finally(() => setLoadingProds(false))
  }, [page, filters, selectedFranchise, activeTab])

  useEffect(() => { fetchProducts() }, [fetchProducts])

  const bmlAdidas = toBml(summary?.bml_adidas)
  const bmlPuma   = toBml(summary?.bml_puma)
  const totalBeat = toNumber(summary?.kpis?.total_beat)
  const totalMeet = toNumber(summary?.kpis?.total_meet)
  const totalLose = toNumber(summary?.kpis?.total_lose)
  const totalAll  = totalBeat + totalMeet + totalLose
  // BEAT = Nike más barato (gana) · LOSE = Nike más caro (pierde). Ver lib/utils.ts.
  const beatPct   = totalAll > 0 ? Math.round((totalBeat / totalAll) * 100) : 0
  const losePct   = totalAll > 0 ? Math.round((totalLose / totalAll) * 100) : 0

  const adidasFranchises = franchises.filter(f => f.marca === 'ADIDAS')
  const pumaFranchises   = franchises.filter(f => f.marca === 'PUMA')

  const universeLabel = summary?.universeLabel ?? ''
  const universeDescription = summary?.universeDescription ?? ''

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

      {/*
        ── Universo de comparación ──────────────────────────────────────
        Los SKUs de Nike venían de los retailers + nike.com.ar + los catálogos
        Nike de otros países, mientras Adidas y Puma no tienen ningún feed
        equivalente: el número invitaba a una comparación que no existe. Ahora
        el universo se elige, se nombra al lado del número y es el mismo para
        los SKUs, el BML y las franquicias de esta pantalla.
      */}
      <div className="nike-card flex flex-wrap items-center gap-3">
        <Info size={15} className="text-gray-400 flex-shrink-0" />
        <label htmlFor="universo" className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
          Universo de comparación
        </label>
        <select
          id="universo"
          value={summary?.universe ?? universe}
          onChange={e => setUniverse(e.target.value)}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:border-gray-400"
        >
          {(summary?.universes ?? []).map(u => (
            <option key={u.key} value={u.key}>{u.label}</option>
          ))}
        </select>
        <p className="text-xs text-gray-400 flex-1 min-w-48">{universeDescription}</p>
      </div>

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <KPICard
          loading={loading}
          title="SKUs Únicos Nike"
          value={toNumber(summary?.kpis?.nike_total).toLocaleString('es-AR')}
          subtitle={`${universeLabel} · precio prom. ${formatPrice(summary?.kpis?.nike_avg_price)}`}
          hint={SKU_HINT}
          color="#111111"
        />
        <KPICard
          loading={loading}
          title="SKUs Únicos Adidas"
          value={toNumber(summary?.kpis?.adidas_total).toLocaleString('es-AR')}
          subtitle={`${universeLabel} · precio prom. ${formatPrice(summary?.kpis?.adidas_avg_price)}`}
          hint={SKU_HINT}
          color="#0046CC"
        />
        <KPICard
          loading={loading}
          title="SKUs Únicos Puma"
          value={toNumber(summary?.kpis?.puma_total).toLocaleString('es-AR')}
          subtitle={`${universeLabel} · precio prom. ${formatPrice(summary?.kpis?.puma_avg_price)}`}
          hint={SKU_HINT}
          color="#E4032E"
        />
        <KPICard
          loading={loading}
          title="LOSE Nike"
          value={`${losePct}%`}
          subtitle="Competencia más barata"
          color="#E31837"
          valueSize="md"
        />
        <KPICard
          loading={loading}
          title="Nike Gana"
          value={`${beatPct}%`}
          subtitle="Nike más barato"
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

      {/* ── BML Donuts ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {([
          ['Adidas', '#0046CC', bmlAdidas, adidasFranchises.length],
          ['Puma', '#E4032E', bmlPuma, pumaFranchises.length],
        ] as const).map(([marca, color, bml, nFranchises]) => (
          <div className="nike-card" key={marca}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide flex items-center gap-2">
                <span className="w-3 h-3 rounded-full inline-block" style={{ background: color }} />
                {marca} — BML Distribution
              </h2>
              <span className="text-xs text-gray-400">{nFranchises} franchises</span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <BMLDonut data={bml} size={180} />
              <div className="flex flex-col justify-center gap-2 text-xs">
                {[
                  { label: 'BEAT (Nike más barato)', val: bml.beat, color: '#27AE60' },
                  { label: 'MEET (precio similar)',  val: bml.meet, color: '#F5A623' },
                  { label: 'LOSE (Nike más caro)',   val: bml.lose, color: '#E31837' },
                  { label: 'Sin datos',              val: bml.nd,   color: '#9B9B9B' },
                ].map(item => (
                  <div key={item.label} className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-gray-600">
                      <span className="w-2.5 h-2.5 rounded-sm" style={{ background: item.color }} />
                      {item.label}
                    </span>
                    <strong>{item.val.toLocaleString('es-AR')}</strong>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ── Top Franchises Charts ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="nike-card">
          <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1">
            Top Franchises — Adidas
          </h2>
          <p className="text-xs text-gray-400 mb-4">Click para filtrar tabla</p>
          <FranchiseBar
            data={toDataPoints(adidasFranchises)}
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
            data={toDataPoints(pumaFranchises)}
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
              SKUs únicos observados, precio promedio y % en promo
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
                      <td className="text-right font-mono">{toNumber(f.count).toLocaleString('es-AR')}</td>
                      <td className="text-right">{formatPrice(f.avg_price != null ? Number(f.avg_price) : null)}</td>
                      <td className="text-right font-mono text-orange-600">
                        {toNumber(f.in_promo) > 0 ? toNumber(f.in_promo).toLocaleString('es-AR') : '—'}
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
