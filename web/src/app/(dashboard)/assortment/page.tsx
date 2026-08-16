'use client'

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { AlertTriangle, ChevronRight } from 'lucide-react'
import KPICard from '@/components/ui/KPICard'
import { formatPrice } from '@/lib/utils'
import { fetchJson, errorMessage } from '@/lib/fetchJson'
import { priceBandLabel, type PriceBand } from '@/lib/priceBands'

// ── Tipos de las respuestas de la API ────────────────────────────────
interface FranchiseRow {
  franchise: string
  marca: string
  division: string | null
  count: number | string
  avg_price: number | string | null
  in_promo: number | string | null
}

interface SiluetaRow {
  silueta: string
  marca: string
  count: number
  avg_price: number | null
}

/** Una celda del desglose: los SKUs de una franquicia dentro de una banda. */
interface BandCell {
  band: string
  skus: number
  avg_price: number | null
}

interface SiluetaFranchiseRow {
  franchise: string
  marca: string
  skus: number
  avg_price: number | null
  skus_sin_precio: number
  bands: BandCell[]
}

interface SiluetaDetail {
  silueta: string
  franchises: SiluetaFranchiseRow[]
  totals: BandCell[]
  skusByMarca: { nike: number; adidas: number; puma: number }
}

interface SiluetasResponse {
  siluetas: SiluetaRow[]
  bands: PriceBand[]
  universeLabel: string
  universeDescription: string
  detail?: SiluetaDetail
}

interface FranchisesResponse {
  franchises: FranchiseRow[]
}

type Tab = 'franchises' | 'siluetas' | 'gaps'

const MARCA_COLOR: Record<string, string> = {
  NIKE: '#111111',
  ADIDAS: '#0046CC',
  PUMA: '#E4032E',
}

const TABS: readonly (readonly [Tab, string])[] = [
  ['franchises', 'Franchises por Marca'],
  ['siluetas', 'Siluetas'],
  ['gaps', 'Product Gap Analysis'],
]

/** Fila del gráfico: una silueta con el conteo de SKUs de cada marca. */
interface SiluetaChartRow {
  silueta: string
  NIKE: number
  ADIDAS: number
  PUMA: number
  total: number
}

const num = (v: number | string | null | undefined): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

const price = (v: number | string | null | undefined): string =>
  v === null || v === undefined || v === '' ? '—' : formatPrice(Number(v))

export default function AssortmentPage() {
  const [franchises, setFranchises] = useState<FranchiseRow[]>([])
  const [siluetasData, setSiluetasData] = useState<SiluetasResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('franchises')

  // ── Desglose por banda de precio de la silueta seleccionada ────────
  const [selectedSilueta, setSelectedSilueta] = useState<string | null>(null)
  const [detail, setDetail] = useState<SiluetaDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetchJson<FranchisesResponse>('/api/pricing/franchises'),
      fetchJson<SiluetasResponse>('/api/pricing/siluetas'),
    ])
      .then(([fr, si]) => {
        if (cancelled) return
        setFranchises(fr.franchises ?? [])
        setSiluetasData(si)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(errorMessage(err))
        setFranchises([])
        setSiluetasData(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // El desglose se pide sólo cuando se clickea una silueta: es una query más
  // pesada (agrupa por franquicia × banda) y no tiene sentido traerla entera.
  useEffect(() => {
    if (!selectedSilueta) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    fetchJson<SiluetasResponse>(
      `/api/pricing/siluetas?silueta=${encodeURIComponent(selectedSilueta)}`,
    )
      .then((d) => {
        if (!cancelled) setDetail(d.detail ?? null)
      })
      .catch((err) => {
        if (cancelled) return
        setDetailError(errorMessage(err))
        setDetail(null)
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedSilueta])

  const siluetas = useMemo(() => siluetasData?.siluetas ?? [], [siluetasData])
  const bands = useMemo(() => siluetasData?.bands ?? [], [siluetasData])

  const nikeF = franchises.filter((f) => f.marca === 'NIKE')
  const adidasF = franchises.filter((f) => f.marca === 'ADIDAS')
  const pumaF = franchises.filter((f) => f.marca === 'PUMA')

  // Gap analysis: franchises Adidas/Puma sin equivalente Nike
  const nikeNames = new Set(nikeF.map((f) => f.franchise?.toLowerCase()))
  const gapsAdidas = adidasF.filter((f) => !nikeNames.has(f.franchise?.toLowerCase()))
  const gapsPuma = pumaF.filter((f) => !nikeNames.has(f.franchise?.toLowerCase()))

  // Una fila por silueta, con las tres marcas como series del gráfico.
  const chartRows: SiluetaChartRow[] = useMemo(() => {
    const bySilueta = new Map<string, SiluetaChartRow>()
    for (const s of siluetas) {
      const row =
        bySilueta.get(s.silueta) ??
        { silueta: s.silueta, NIKE: 0, ADIDAS: 0, PUMA: 0, total: 0 }
      if (s.marca === 'NIKE' || s.marca === 'ADIDAS' || s.marca === 'PUMA') {
        row[s.marca] += s.count
        row.total += s.count
      }
      bySilueta.set(s.silueta, row)
    }
    return Array.from(bySilueta.values()).sort((a, b) => b.total - a.total)
  }, [siluetas])

  const topSiluetas = chartRows.slice(0, 15)

  const toggleSilueta = useCallback((silueta: string) => {
    setSelectedSilueta((current) => (current === silueta ? null : silueta))
  }, [])

  return (
    <div className="space-y-6">
      {error && (
        <div className="nike-card border border-red-200 bg-red-50 flex items-start gap-3">
          <AlertTriangle size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-red-700">No se pudieron cargar los datos de Assortment</p>
            <p className="text-xs text-red-600 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KPICard loading={loading} title="Franchises Adidas" value={adidasF.length} subtitle="modelos monitoreados" color="#0046CC" />
        <KPICard loading={loading} title="Franchises Puma" value={pumaF.length} subtitle="modelos monitoreados" color="#E4032E" />
        <KPICard loading={loading} title="Gaps Adidas vs Nike" value={gapsAdidas.length} subtitle="sin equivalente Nike" color="#E31837" />
        <KPICard loading={loading} title="Gaps Puma vs Nike" value={gapsPuma.length} subtitle="sin equivalente Nike" color="#F5A623" />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {TABS.map(([val, label]) => (
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
          {([
            ['Top Franchises Adidas', '#0046CC', adidasF],
            ['Top Franchises Puma', '#E4032E', pumaF],
          ] as const).map(([title, color, rows]) => (
            <div className="nike-card" key={title}>
              <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-4 flex items-center gap-2">
                <span className="w-3 h-3 rounded-full" style={{ background: color }} />
                {title}
              </h2>
              <div className="overflow-x-auto">
                <table className="nike-table">
                  <thead>
                    <tr>
                      <th>Franchise</th>
                      <th className="text-right">SKUs</th>
                      <th className="text-right">Precio Prom.</th>
                      <th className="text-right">En Promo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading
                      ? Array.from({ length: 8 }).map((_, i) => (
                          <tr key={i} className="animate-pulse border-b">
                            <td colSpan={4}><div className="h-3 bg-gray-200 rounded my-2 w-3/4" /></td>
                          </tr>
                        ))
                      : rows.slice(0, 15).map((f) => (
                          <tr key={f.franchise}>
                            <td className="font-medium">{f.franchise}</td>
                            <td className="text-right font-mono">{num(f.count).toLocaleString('es-AR')}</td>
                            <td className="text-right">{price(f.avg_price)}</td>
                            <td className="text-right text-orange-600">
                              {num(f.in_promo) > 0 ? num(f.in_promo).toLocaleString('es-AR') : '—'}
                            </td>
                          </tr>
                        ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tab: Siluetas */}
      {tab === 'siluetas' && (
        <div className="space-y-4">
          <div className="nike-card">
            <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1">
              Distribución por Silueta — Nike vs Adidas + Puma
            </h2>
            <p className="text-xs text-gray-400 mb-4">
              SKUs distintos por silueta.
              {siluetasData ? ` Universo: ${siluetasData.universeLabel}.` : ''}
            </p>
            <ResponsiveContainer width="100%" height={420}>
              <BarChart data={topSiluetas} margin={{ top: 4, right: 16, left: 0, bottom: 60 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F0F0F0" />
                <XAxis dataKey="silueta" tick={{ fontSize: 10, fill: '#666' }} angle={-35} textAnchor="end" interval={0} height={80} />
                <YAxis tick={{ fontSize: 11, fill: '#9B9B9B' }} axisLine={false} tickLine={false} />
                <Tooltip
                  formatter={(value: number, name: string) => [
                    `${Number(value).toLocaleString('es-AR')} SKUs`,
                    name,
                  ]}
                />
                <Legend />
                <Bar dataKey="NIKE" fill={MARCA_COLOR.NIKE} radius={[3, 3, 0, 0]} />
                <Bar dataKey="ADIDAS" fill={MARCA_COLOR.ADIDAS} radius={[3, 3, 0, 0]} />
                <Bar dataKey="PUMA" fill={MARCA_COLOR.PUMA} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <SiluetaBandTable
            rows={chartRows}
            bands={bands}
            loading={loading}
            selected={selectedSilueta}
            onSelect={toggleSilueta}
            detail={detail}
            detailLoading={detailLoading}
            detailError={detailError}
          />
        </div>
      )}

      {/* Tab: Product Gap Analysis */}
      {tab === 'gaps' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {([
            ['Franchises Adidas sin equivalente Nike', '#0046CC', gapsAdidas, 'Adidas'],
            ['Franchises Puma sin equivalente Nike', '#E4032E', gapsPuma, 'Puma'],
          ] as const).map(([title, color, rows, marca]) => (
            <div className="nike-card" key={title}>
              <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide mb-1 flex items-center gap-2">
                <span className="w-3 h-3 rounded-full" style={{ background: color }} />
                {title}
              </h2>
              <p className="text-xs text-gray-400 mb-4">
                {rows.length} modelos {marca} no matcheados contra Nike
              </p>
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {rows.slice(0, 30).map((f) => (
                  <div key={f.franchise} className="flex items-center justify-between py-2 border-b border-gray-50">
                    <span className="text-sm font-medium text-gray-800">{f.franchise}</span>
                    <div className="flex items-center gap-3 text-xs text-gray-500">
                      <span>{num(f.count)} SKUs</span>
                      <span className="font-semibold text-gray-700">{price(f.avg_price)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Tabla de siluetas clickeable + desglose por banda de precio
// ─────────────────────────────────────────────────────────────────────

interface SiluetaBandTableProps {
  rows: SiluetaChartRow[]
  bands: PriceBand[]
  loading: boolean
  selected: string | null
  onSelect: (silueta: string) => void
  detail: SiluetaDetail | null
  detailLoading: boolean
  detailError: string | null
}

/**
 * Análisis de surtido sobre el eje de precio: al clickear una silueta se abre,
 * por cada franquicia, cuántos SKUs tiene y a qué precio promedio EN CADA BANDA.
 * Las bandas son las de `backend/config/weights.yaml` (ver `lib/priceBands.ts`)
 * y particionan el surtido: cada SKU cae en una sola.
 */
function SiluetaBandTable({
  rows,
  bands,
  loading,
  selected,
  onSelect,
  detail,
  detailLoading,
  detailError,
}: SiluetaBandTableProps) {
  return (
    <div className="nike-card !p-0 overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100">
        <h2 className="font-bold text-gray-900 text-sm uppercase tracking-wide">
          Surtido por banda de precio
        </h2>
        <p className="text-xs text-gray-400 mt-0.5">
          Clickeá una silueta para ver, por franquicia, cuántos SKUs tiene y a qué precio
          promedio en cada banda. Cada SKU cae en una sola banda, según el promedio de sus
          precios observados.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="nike-table">
          <thead>
            <tr>
              <th className="w-8" />
              <th>Silueta</th>
              <th className="text-right">Nike</th>
              <th className="text-right">Adidas</th>
              <th className="text-right">Puma</th>
              <th className="text-right">Total SKUs</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={6} className="text-center text-xs text-gray-400 py-6">
                  Cargando siluetas…
                </td>
              </tr>
            )}

            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={6} className="text-center text-xs text-gray-400 py-6">
                  Sin siluetas con datos en este universo.
                </td>
              </tr>
            )}

            {!loading &&
              rows.map((row) => {
                const isOpen = selected === row.silueta
                return (
                  <Fragment key={row.silueta}>
                    <tr
                      onClick={() => onSelect(row.silueta)}
                      className={`cursor-pointer transition-colors ${isOpen ? 'bg-gray-50' : 'hover:bg-gray-50'}`}
                      aria-expanded={isOpen}
                    >
                      <td className="text-gray-400">
                        <ChevronRight
                          size={14}
                          className={`transition-transform ${isOpen ? 'rotate-90' : ''}`}
                          aria-hidden="true"
                        />
                      </td>
                      <td className="font-medium">{row.silueta}</td>
                      <td className="text-right font-mono">{row.NIKE.toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono">{row.ADIDAS.toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono">{row.PUMA.toLocaleString('es-AR')}</td>
                      <td className="text-right font-mono font-bold">{row.total.toLocaleString('es-AR')}</td>
                    </tr>

                    {isOpen && (
                      <tr>
                        <td colSpan={6} className="!p-0 bg-gray-50">
                          <SiluetaDetailPanel
                            silueta={row.silueta}
                            bands={bands}
                            detail={detail}
                            loading={detailLoading}
                            error={detailError}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

interface SiluetaDetailPanelProps {
  silueta: string
  bands: PriceBand[]
  detail: SiluetaDetail | null
  loading: boolean
  error: string | null
}

function SiluetaDetailPanel({ silueta, bands, detail, loading, error }: SiluetaDetailPanelProps) {
  if (loading) {
    return <p className="px-5 py-6 text-xs text-gray-400">Cargando el desglose de {silueta}…</p>
  }
  if (error) {
    return (
      <div className="px-5 py-4 flex items-start gap-2">
        <AlertTriangle size={14} className="text-red-600 flex-shrink-0 mt-0.5" />
        <p className="text-xs text-red-600">{error}</p>
      </div>
    )
  }
  if (!detail || detail.franchises.length === 0) {
    return (
      <p className="px-5 py-6 text-xs text-gray-400">
        No hay franquicias con SKUs para {silueta} en este universo.
      </p>
    )
  }

  const cellOf = (row: SiluetaFranchiseRow, band: string): BandCell | undefined =>
    row.bands.find((b) => b.band === band)

  return (
    <div className="px-5 py-4 overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-gray-400 border-b border-gray-200">
            <th className="text-left py-2 pr-4 font-semibold">Franquicia</th>
            <th className="text-left py-2 pr-4 font-semibold">Marca</th>
            {bands.map((b) => (
              <th key={b.key} className="text-right py-2 px-3 font-semibold whitespace-nowrap">
                {priceBandLabel(b.key)}
              </th>
            ))}
            <th className="text-right py-2 pl-3 font-semibold">Total</th>
          </tr>
        </thead>
        <tbody>
          {detail.franchises.map((row) => (
            <tr key={`${row.marca}-${row.franchise}`} className="border-b border-gray-100">
              <td className="py-2 pr-4 font-medium text-gray-800">{row.franchise}</td>
              <td className="py-2 pr-4">
                <span
                  className="px-2 py-0.5 rounded-full text-[10px] font-bold text-white"
                  style={{ backgroundColor: MARCA_COLOR[row.marca] ?? '#999' }}
                >
                  {row.marca}
                </span>
              </td>
              {bands.map((b) => {
                const cell = cellOf(row, b.key)
                return (
                  <td key={b.key} className="py-2 px-3 text-right tabular-nums">
                    {cell && cell.skus > 0 ? (
                      <>
                        <span className="font-mono font-semibold text-gray-800">{cell.skus}</span>
                        <span className="block text-[10px] text-gray-500">{price(cell.avg_price)}</span>
                      </>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                )
              })}
              <td className="py-2 pl-3 text-right tabular-nums">
                <span className="font-mono font-bold text-gray-900">{row.skus}</span>
                <span className="block text-[10px] text-gray-500">{price(row.avg_price)}</span>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-gray-300 font-semibold text-gray-700">
            <td className="py-2 pr-4">Total {silueta}</td>
            <td className="py-2 pr-4" />
            {bands.map((b) => {
              const total = detail.totals.find((t) => t.band === b.key)
              return (
                <td key={b.key} className="py-2 px-3 text-right tabular-nums">
                  <span className="font-mono">{total?.skus ?? 0}</span>
                  <span className="block text-[10px] font-normal text-gray-500">
                    {price(total?.avg_price ?? null)}
                  </span>
                </td>
              )
            })}
            <td className="py-2 pl-3 text-right font-mono">
              {(detail.skusByMarca.nike + detail.skusByMarca.adidas + detail.skusByMarca.puma).toLocaleString('es-AR')}
            </td>
          </tr>
        </tfoot>
      </table>

      {detail.franchises.some((f) => f.skus_sin_precio > 0) && (
        <p className="mt-2 text-[10px] text-gray-400">
          Los SKUs sin un precio utilizable (0, o inflado por cuotas) cuentan en el total de
          la franquicia pero no caen en ninguna banda, así que las bandas pueden sumar menos
          que el total.
        </p>
      )}
    </div>
  )
}
