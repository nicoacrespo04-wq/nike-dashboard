'use client'

import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown, ExternalLink } from 'lucide-react'
import type { PricingRow } from '@/lib/db'
import { cn } from '@/lib/utils'
import { BMLBadge, Badge, BrandBadge } from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import ErrorState from '@/components/ui/ErrorState'
import { InfoTip } from '@/components/ui/Tooltip'
import { ND, formatPriceSafe, isPlausiblePrice } from '@/components/ui/format'

/* ────────────────────────────────────────────────────────────────────────────
   Modelo de columnas
   ──────────────────────────────────────────────────────────────────────────── */

type SortKey =
  | 'marca' | 'style_color' | 'product' | 'franchise' | 'silueta'
  | 'retailer' | 'comp_price' | 'nike_price' | 'gap' | 'bml' | 'sizes'

type SortDir = 'asc' | 'desc'

interface ColumnDef {
  key: SortKey | 'link'
  label: string
  align?: 'left' | 'right' | 'center'
  sortable?: boolean
  /** Explicación de la columna (tooltip "i" en el encabezado). */
  hint?: string
  /** Ancho sugerido; evita que el nombre de producto coma toda la tabla. */
  className?: string
}

const BML_ORDER: Record<string, number> = { BEAT: 3, MEET: 2, LOSE: 1 }

const COLUMNS: ColumnDef[] = [
  { key: 'marca',       label: 'Marca',        sortable: true },
  { key: 'style_color', label: 'StyleColor',   sortable: true },
  { key: 'product',     label: 'Producto',     sortable: true, className: 'min-w-[220px]' },
  { key: 'franchise',   label: 'Franchise',    sortable: true },
  { key: 'silueta',     label: 'Silueta',      sortable: true },
  { key: 'retailer',    label: 'Retailer',     sortable: true },
  {
    key: 'comp_price', label: 'Precio comp.', align: 'right', sortable: true,
    hint: 'Precio final publicado por el competidor (con descuento aplicado). Si el scraper no devolvió un precio válido se muestra N/D.',
  },
  {
    key: 'nike_price', label: 'Precio Nike', align: 'right', sortable: true,
    hint: 'Precio final del artículo Nike equivalente en el mismo canal.',
  },
  {
    key: 'gap', label: 'Gap %', align: 'right', sortable: true,
    hint: 'Diferencia porcentual del precio del competidor respecto de Nike. Negativo = el competidor está más barato.',
  },
  {
    key: 'bml', label: 'BML', align: 'center', sortable: true,
    hint: 'BEAT = Nike más barato · MEET = precio similar · LOSE = Nike más caro.',
  },
  { key: 'sizes', label: 'Talles', align: 'center', sortable: true, hint: 'Cantidad de talles con stock en el competidor.' },
  { key: 'link',  label: 'Link',   align: 'center' },
]

/** Valor comparable por columna. `null` siempre ordena al final. */
function sortValue(row: PricingRow, key: SortKey): string | number | null {
  switch (key) {
    case 'marca':       return row.marca ?? null
    case 'style_color': return row.style_color ?? row.productcode_competitor ?? null
    case 'product':     return row.product_name_competitor ?? row.marketing_name ?? null
    case 'franchise':   return row.franchise_competitor ?? null
    case 'silueta':     return row.silueta ?? null
    case 'retailer':    return row.scraper ?? null
    case 'comp_price':  return isPlausiblePrice(row.competitor_final_price) ? row.competitor_final_price : null
    case 'nike_price':  return isPlausiblePrice(row.nike_final_price) ? row.nike_final_price : null
    case 'gap':         return row.gap_final_price_pct ?? null
    case 'bml':         return BML_ORDER[row.bml_final_price?.toUpperCase() ?? ''] ?? null
    case 'sizes':       return row.size_available_competitor ?? null
  }
}

/* ────────────────────────────────────────────────────────────────────────────
   Componente
   ──────────────────────────────────────────────────────────────────────────── */

export interface PricingTableProps {
  data: PricingRow[]
  loading?: boolean
  /** Mensaje de error; reemplaza el cuerpo de la tabla por un estado de error. */
  error?: string | boolean | null
  onRetry?: () => void
  totalCount?: number
  page?: number
  pageSize?: number
  onPageChange?: (page: number) => void
  /** Acción del estado vacío (ej. limpiar filtros). */
  emptyAction?: React.ReactNode
  /** Alto máximo del área scrolleable; habilita el encabezado sticky. */
  maxHeight?: number | string
  className?: string
}

export default function PricingTable({
  data,
  loading = false,
  error,
  onRetry,
  totalCount = 0,
  page = 1,
  pageSize = 50,
  onPageChange,
  emptyAction,
  maxHeight = '65vh',
  className,
}: PricingTableProps) {
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null)
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize))

  /**
   * Orden client-side sobre la página visible. La paginación es server-side,
   * así que esto ordena las 50 filas en pantalla — suficiente para escanear un
   * resultado filtrado sin un round-trip extra.
   */
  const rows = useMemo(() => {
    if (!sort) return data
    const factor = sort.dir === 'asc' ? 1 : -1
    return [...data].sort((a, b) => {
      const va = sortValue(a, sort.key)
      const vb = sortValue(b, sort.key)
      if (va == null && vb == null) return 0
      if (va == null) return 1   // nulos siempre al final
      if (vb == null) return -1
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * factor
      return String(va).localeCompare(String(vb), 'es-AR', { sensitivity: 'base' }) * factor
    })
  }, [data, sort])

  const toggleSort = (key: SortKey) => {
    setSort((prev) => {
      if (prev?.key !== key) return { key, dir: 'asc' }
      if (prev.dir === 'asc') return { key, dir: 'desc' }
      return null // tercer click: vuelve al orden original del servidor
    })
  }

  const bodyState: 'loading' | 'error' | 'empty' | 'ready' =
    loading ? 'loading' : error ? 'error' : rows.length === 0 ? 'empty' : 'ready'

  return (
    <div className={cn('flex min-w-0 flex-col gap-3', className)}>
      {/* La tabla scrollea dentro de su contenedor: nunca empuja el layout. */}
      <div
        className="nike-table-wrap rounded-xl border border-surface-border bg-white"
        style={{ maxHeight: bodyState === 'ready' ? maxHeight : undefined }}
      >
        <table className="nike-table min-w-[1180px]" aria-busy={loading}>
          <caption className="sr-only">
            Detalle de productos de la competencia con precios, gap vs. Nike y clasificación BML
          </caption>
          <thead>
            <tr>
              {COLUMNS.map((col) => {
                const isSorted = sort?.key === col.key
                const ariaSort = isSorted ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'
                return (
                  <th
                    key={col.key}
                    scope="col"
                    aria-sort={col.sortable ? ariaSort : undefined}
                    className={cn(
                      col.align === 'right' && 'text-right',
                      col.align === 'center' && 'text-center',
                      col.className,
                    )}
                  >
                    <span
                      className={cn(
                        'inline-flex items-center gap-1',
                        col.align === 'right' && 'flex-row-reverse',
                      )}
                    >
                      {col.sortable ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(col.key as SortKey)}
                          aria-label={`Ordenar por ${col.label}`}
                          className="inline-flex items-center gap-1 rounded transition-opacity duration-fast hover:opacity-75"
                        >
                          {col.label}
                          {isSorted ? (
                            sort!.dir === 'asc'
                              ? <ArrowUp size={11} aria-hidden="true" />
                              : <ArrowDown size={11} aria-hidden="true" />
                          ) : (
                            <ChevronsUpDown size={11} className="opacity-40" aria-hidden="true" />
                          )}
                        </button>
                      ) : (
                        col.label
                      )}
                      {col.hint && <InfoTip content={col.hint} label={`Cómo se calcula: ${col.label}`} side="bottom" />}
                    </span>
                  </th>
                )
              })}
            </tr>
          </thead>

          <tbody>
            {bodyState === 'loading' &&
              Array.from({ length: 10 }).map((_, i) => <SkeletonRow key={i} index={i} />)}

            {bodyState === 'error' && (
              <tr className="!bg-white hover:!bg-white">
                <td colSpan={COLUMNS.length}>
                  <ErrorState
                    title="No pudimos cargar los productos"
                    description={typeof error === 'string' ? error : 'La consulta al pricing falló. Volvé a intentar.'}
                    onRetry={onRetry}
                  />
                </td>
              </tr>
            )}

            {bodyState === 'empty' && (
              <tr className="!bg-white hover:!bg-white">
                <td colSpan={COLUMNS.length}>
                  <EmptyState
                    title="Sin resultados con los filtros actuales"
                    description="Probá ampliar la marca, el canal o la división, o limpiá la búsqueda por texto."
                    action={emptyAction}
                  />
                </td>
              </tr>
            )}

            {bodyState === 'ready' && rows.map((row, i) => <Row key={row.id ?? i} row={row} />)}
          </tbody>
        </table>
      </div>

      {/* ── Paginación ─────────────────────────────────────────────── */}
      {totalPages > 1 && (
        <nav className="flex flex-wrap items-center justify-between gap-2 px-1" aria-label="Paginación de productos">
          <p className="text-xs tabnum text-nike-muted">
            {totalCount.toLocaleString('es-AR')} productos · página {page} de {totalPages}
          </p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onPageChange?.(page - 1)}
              disabled={page <= 1}
              aria-label="Página anterior"
              className="rounded-lg border border-surface-border-strong p-1.5 text-gray-500 transition-colors duration-fast hover:border-nike-black hover:text-nike-black disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronLeft size={14} aria-hidden="true" />
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const p = Math.max(1, Math.min(page - 2, totalPages - 4)) + i
              const current = p === page
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => onPageChange?.(p)}
                  aria-label={`Ir a la página ${p}`}
                  aria-current={current ? 'page' : undefined}
                  className={cn(
                    'h-8 w-8 rounded-lg text-xs font-semibold tabnum transition-colors duration-fast',
                    current
                      ? 'bg-nike-black text-white'
                      : 'border border-surface-border-strong text-gray-600 hover:border-nike-black hover:text-nike-black',
                  )}
                >
                  {p}
                </button>
              )
            })}
            <button
              type="button"
              onClick={() => onPageChange?.(page + 1)}
              disabled={page >= totalPages}
              aria-label="Página siguiente"
              className="rounded-lg border border-surface-border-strong p-1.5 text-gray-500 transition-colors duration-fast hover:border-nike-black hover:text-nike-black disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronRight size={14} aria-hidden="true" />
            </button>
          </div>
        </nav>
      )}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────────
   Fila
   ──────────────────────────────────────────────────────────────────────────── */

function Row({ row }: { row: PricingRow }) {
  const compPrice = row.competitor_final_price
  const fullPrice = row.competitor_full_price
  // Sólo mostramos el precio tachado si el "full price" es plausible y mayor.
  const showMarkdown =
    isPlausiblePrice(compPrice) && isPlausiblePrice(fullPrice) && (fullPrice as number) > (compPrice as number)

  const gapFraction = row.gap_final_price_pct
  const gapPct = gapFraction != null && Number.isFinite(gapFraction) ? gapFraction * 100 : null

  const productName = row.product_name_competitor ?? row.marketing_name ?? null
  const styleColor = row.style_color ?? row.productcode_competitor ?? null

  return (
    <tr>
      {/* Marca */}
      <td><BrandBadge marca={row.marca} /></td>

      {/* StyleColor — mono para que los códigos se comparen de un vistazo */}
      <td>
        <span className="font-mono text-xs text-gray-600">{styleColor ?? ND}</span>
      </td>

      {/* Producto — el ancla de la fila: es el texto con más peso visual */}
      <td className="max-w-[260px]">
        <span
          title={productName ?? undefined}
          className="block truncate font-medium text-nike-ink"
        >
          {productName ?? ND}
        </span>
      </td>

      {/* Franchise */}
      <td className="max-w-[150px]">
        <span title={row.franchise_competitor ?? undefined} className="block truncate text-xs text-gray-600">
          {row.franchise_competitor ?? ND}
        </span>
      </td>

      {/* Silueta */}
      <td className="max-w-[130px]">
        <span title={row.silueta ?? undefined} className="block truncate text-xs text-nike-muted">
          {row.silueta ?? ND}
        </span>
      </td>

      {/* Retailer */}
      <td>
        <Badge tone="neutral" className="normal-case tracking-normal">{row.scraper ?? ND}</Badge>
      </td>

      {/* Precio competidor */}
      <td className="num">
        <span className={cn('font-semibold', showMarkdown ? 'text-bml-lose-ink' : 'text-nike-ink')}>
          {formatPriceSafe(compPrice)}
        </span>
        {showMarkdown && (
          <span className="block text-micro tabnum text-nike-faint line-through">
            {formatPriceSafe(fullPrice)}
          </span>
        )}
      </td>

      {/* Precio Nike */}
      <td className="num text-gray-600">{formatPriceSafe(row.nike_final_price)}</td>

      {/* Gap % */}
      <td className="num">
        {gapPct !== null ? (
          <span
            className={cn('text-xs font-semibold', gapPct < 0 ? 'text-bml-lose-ink' : 'text-bml-beat-ink')}
          >
            {gapPct > 0 ? '+' : ''}{gapPct.toFixed(1)}%
          </span>
        ) : (
          <span className="text-xs text-nike-faint">{ND}</span>
        )}
      </td>

      {/* BML */}
      <td className="text-center"><BMLBadge value={row.bml_final_price} /></td>

      {/* Talles */}
      <td className="text-center text-xs tabnum text-nike-muted">
        {row.size_available_competitor ?? ND}
      </td>

      {/* Link PDP */}
      <td className="text-center">
        {row.link_pdp_competitor ? (
          <a
            href={row.link_pdp_competitor}
            target="_blank"
            rel="noopener noreferrer"
            title="Abrir la ficha del producto en el sitio del competidor"
            aria-label={`Abrir ficha de ${productName ?? 'producto'} en ${row.scraper ?? 'el retailer'} (nueva pestaña)`}
            className="inline-flex rounded p-1 text-nike-mid-gray transition-colors duration-fast hover:text-nike-red"
          >
            <ExternalLink size={13} aria-hidden="true" />
          </a>
        ) : (
          <span className="text-xs text-nike-faint">{ND}</span>
        )}
      </td>
    </tr>
  )
}

/* ────────────────────────────────────────────────────────────────────────────
   Skeleton
   ──────────────────────────────────────────────────────────────────────────── */

/** Anchos fijos (no aleatorios) para que el skeleton no parpadee entre renders. */
const SKELETON_WIDTHS = ['55%', '70%', '88%', '62%', '48%', '58%', '64%', '60%', '42%', '46%', '38%', '30%']

function SkeletonRow({ index }: { index: number }) {
  return (
    <tr aria-hidden="true">
      {SKELETON_WIDTHS.map((w, i) => (
        <td key={i} className="px-3 py-3">
          <div
            className="skeleton h-3"
            style={{ width: w, animationDelay: `${(index % 5) * 80}ms` }}
          />
        </td>
      ))}
    </tr>
  )
}
