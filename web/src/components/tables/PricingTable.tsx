'use client'

import { ExternalLink, ChevronLeft, ChevronRight } from 'lucide-react'
import { formatPrice, getBMLBadgeClass, truncate } from '@/lib/utils'
import type { PricingRow } from '@/lib/db'
import { cn } from '@/lib/utils'

interface PricingTableProps {
  data: PricingRow[]
  loading?: boolean
  totalCount?: number
  page?: number
  pageSize?: number
  onPageChange?: (page: number) => void
}

const MARCA_BADGE: Record<string, string> = {
  ADIDAS: 'bg-blue-100 text-blue-800',
  PUMA:   'bg-orange-100 text-orange-800',
  NIKE:   'bg-gray-900 text-white',
}

function SkeletonRow() {
  return (
    <tr className="border-b border-gray-100 animate-pulse">
      {Array.from({ length: 12 }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <div className="h-3 bg-gray-200 rounded" style={{ width: `${40 + Math.random() * 50}%` }} />
        </td>
      ))}
    </tr>
  )
}

export default function PricingTable({
  data,
  loading = false,
  totalCount = 0,
  page = 1,
  pageSize = 50,
  onPageChange,
}: PricingTableProps) {
  const totalPages = Math.ceil(totalCount / pageSize)

  return (
    <div className="flex flex-col gap-3">
      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="nike-table min-w-[1100px]">
          <thead>
            <tr>
              <th>Marca</th>
              <th>StyleColor</th>
              <th>Producto</th>
              <th>Franchise</th>
              <th>Silueta</th>
              <th>Retailer</th>
              <th className="text-right">Precio Comp.</th>
              <th className="text-right">Precio Nike</th>
              <th className="text-right">Gap %</th>
              <th className="text-center">BML</th>
              <th className="text-center">Talles</th>
              <th className="text-center">Link</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 10 }).map((_, i) => <SkeletonRow key={i} />)
              : data.length === 0
              ? (
                <tr>
                  <td colSpan={12} className="text-center py-16 text-gray-400">
                    <div className="flex flex-col items-center gap-2">
                      <span className="text-3xl">🔍</span>
                      <span className="font-medium">Sin resultados con los filtros actuales</span>
                    </div>
                  </td>
                </tr>
              )
              : data.map((row, i) => {
                const gapPct = row.gap_final_price_pct ? row.gap_final_price_pct * 100 : null
                const hasMarkdown = row.competitor_markdown && row.competitor_markdown > 0
                return (
                  <tr key={row.id ?? i}>
                    {/* Marca */}
                    <td>
                      <span className={cn(
                        'inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide',
                        MARCA_BADGE[row.marca?.toUpperCase()] ?? 'bg-gray-100 text-gray-700'
                      )}>
                        {row.marca}
                      </span>
                    </td>

                    {/* StyleColor */}
                    <td>
                      <span className="font-mono text-xs text-gray-700">
                        {row.style_color ?? row.productcode_competitor ?? '—'}
                      </span>
                    </td>

                    {/* Producto */}
                    <td>
                      <span
                        title={row.product_name_competitor ?? ''}
                        className="text-gray-800"
                      >
                        {truncate(row.product_name_competitor ?? row.marketing_name ?? '—', 35)}
                      </span>
                    </td>

                    {/* Franchise */}
                    <td className="text-gray-600 text-xs">
                      {truncate(row.franchise_competitor ?? '—', 20)}
                    </td>

                    {/* Silueta */}
                    <td className="text-gray-500 text-xs">
                      {row.silueta ?? '—'}
                    </td>

                    {/* Retailer */}
                    <td>
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded font-medium">
                        {row.scraper}
                      </span>
                    </td>

                    {/* Precio Comp */}
                    <td className="text-right font-semibold">
                      <span className={hasMarkdown ? 'text-red-600' : 'text-gray-800'}>
                        {formatPrice(row.competitor_final_price)}
                      </span>
                      {hasMarkdown && (
                        <span className="block text-[10px] text-gray-400 line-through">
                          {formatPrice(row.competitor_full_price)}
                        </span>
                      )}
                    </td>

                    {/* Precio Nike */}
                    <td className="text-right text-gray-600">
                      {formatPrice(row.nike_final_price)}
                    </td>

                    {/* Gap */}
                    <td className="text-right font-semibold text-xs">
                      {gapPct !== null ? (
                        <span className={gapPct < 0 ? 'text-red-600' : 'text-green-600'}>
                          {gapPct > 0 ? '+' : ''}{gapPct.toFixed(1)}%
                        </span>
                      ) : '—'}
                    </td>

                    {/* BML */}
                    <td className="text-center">
                      <span className={cn(
                        'inline-block px-2 py-0.5 rounded text-[10px] uppercase tracking-wide',
                        getBMLBadgeClass(row.bml_final_price ?? '')
                      )}>
                        {row.bml_final_price ?? 'N/D'}
                      </span>
                    </td>

                    {/* Talles */}
                    <td className="text-center text-xs text-gray-500">
                      {row.size_available_competitor ?? '—'}
                    </td>

                    {/* Link */}
                    <td className="text-center">
                      {row.link_pdp_competitor ? (
                        <a
                          href={row.link_pdp_competitor}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-gray-400 hover:text-[#E31837] transition-colors inline-flex"
                        >
                          <ExternalLink size={13} />
                        </a>
                      ) : '—'}
                    </td>
                  </tr>
                )
              })
            }
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-1">
          <p className="text-xs text-gray-400">
            {totalCount.toLocaleString('es-AR')} productos · página {page} de {totalPages}
          </p>
          <div className="flex items-center gap-1">
            <button
              onClick={() => onPageChange?.(page - 1)}
              disabled={page <= 1}
              className="p-1.5 rounded-lg border border-gray-200 text-gray-500 hover:border-gray-400 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            >
              <ChevronLeft size={14} />
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const p = Math.max(1, Math.min(page - 2, totalPages - 4)) + i
              return (
                <button
                  key={p}
                  onClick={() => onPageChange?.(p)}
                  className={cn(
                    'w-8 h-8 rounded-lg text-xs font-semibold transition-all',
                    p === page
                      ? 'bg-[#111111] text-white'
                      : 'border border-gray-200 text-gray-600 hover:border-gray-400'
                  )}
                >
                  {p}
                </button>
              )
            })}
            <button
              onClick={() => onPageChange?.(page + 1)}
              disabled={page >= totalPages}
              className="p-1.5 rounded-lg border border-gray-200 text-gray-500 hover:border-gray-400 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            >
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
