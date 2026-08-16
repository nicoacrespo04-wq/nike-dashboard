'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import type { Product, ProductFilters, ProductListResponse } from '@/types/intelligence'
import { getProducts } from '@/lib/intelligence/api'
import { depsKeyOf } from '@/lib/intelligence/depsKey'
import { useApi, useDebounced } from '@/lib/intelligence/useApi'
import { money, text } from '@/lib/format'
import { Card, EmptyState, ErrorState } from '@/components/ui'
import { LifecycleBadge, Tag } from '@/components/intelligence/badges'
import { CommandHint } from '@/components/intelligence/hints'
import Pager from './Pager'
import {
  EMPTY_PRODUCT_STATE,
  PRODUCTS_PAGE_SIZE,
  type ProductExplorerState,
  type ProductFilterKey,
  activeProductFilters,
  productQueryFrom,
  productQueryKey,
} from './productParams'
import { ProductGridSkeleton } from './skeletons'
import { syncUrl } from './urlState'

export interface ProductExplorerProps {
  /** Estado con el que el servidor resolvió la primera página. */
  initialState: ProductExplorerState
  /** Opciones de los selects, resueltas en el servidor (cacheadas 10 min). */
  filterOptions: ProductFilters | null
  /** Primera página ya resuelta en el servidor. */
  initialData: ProductListResponse | null
  /** Error del servidor, si el motor no respondió. */
  initialError: string | null
}

/**
 * Product Explorer — mitad cliente.
 *
 * Queda en el cliente porque es lo contrario del Overview: nueve selects, un
 * buscador y paginación, todo interactivo. Pero **el filtrado y la paginación
 * ocurren en el backend**: cada cambio manda `limit`/`offset` y los filtros
 * como query params, y el total que se muestra es el que informa el backend,
 * no el largo del array recibido. La primera pantalla llega renderizada desde
 * el servidor: si la firma de la query no cambió, el cliente no pide nada.
 */
export default function ProductExplorer({
  initialState,
  filterOptions,
  initialData,
  initialError,
}: ProductExplorerProps) {
  const [state, setState] = useState<ProductExplorerState>(initialState)
  // El texto tipeado va aparte del estado que dispara la consulta: se debouncea
  // para no pedir una página por tecla.
  const [typed, setTyped] = useState(initialState.q)
  const debouncedQ = useDebounced(typed, 350)

  useEffect(() => {
    setState((prev) => (prev.q === debouncedQ ? prev : { ...prev, q: debouncedQ, page: 0 }))
  }, [debouncedQ])

  const query = useMemo(() => productQueryFrom(state), [state])
  const queryKey = productQueryKey(query)

  const productsState = useApi((signal) => getProducts(query, signal), [queryKey], {
    initialData,
    initialKey: depsKeyOf([productQueryKey(productQueryFrom(initialState))]),
  })

  // La URL refleja el estado sin volver al servidor: se puede compartir un
  // filtro o recargar la pestaña y caer en la misma vista.
  useEffect(() => {
    syncUrl({ ...state, page: state.page || null })
  }, [state])

  const set = (key: ProductFilterKey, value: string) =>
    setState((prev) => ({ ...prev, [key]: value, page: 0 }))

  const activeCount = activeProductFilters(state)
  const searchPending = typed !== state.q
  const error = productsState.error ?? (productsState.data === null ? initialError : null)

  return (
    <div>
      {/* ── Filtros ─────────────────────────────────────────────── */}
      <Card className="mb-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="block xl:col-span-2">
            <span className="label-caps mb-1 block">
              Búsqueda libre (nombre, franquicia, SKU, style code)
            </span>
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Pegasus, Novablast, DV3853…"
              className="field-control w-full"
              aria-describedby="products-search-status"
            />
            <span id="products-search-status" className="mt-1 block h-3 text-2xs text-nike-muted">
              {searchPending ? 'Buscando…' : ''}
            </span>
          </label>
        </div>

        {filterOptions ? (
          <div className="mt-1 grid gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
            <Select
              label="Marca"
              value={state.brand}
              options={filterOptions.brands}
              onChange={(v) => set('brand', v)}
            />
            <Select
              label="Franquicia"
              value={state.franchise}
              options={filterOptions.franchises}
              onChange={(v) => set('franchise', v)}
            />
            <Select
              label="Categoría"
              value={state.category}
              options={filterOptions.categories}
              onChange={(v) => set('category', v)}
            />
            <Select
              label="Deporte"
              value={state.sport}
              options={filterOptions.sports}
              onChange={(v) => set('sport', v)}
            />
            <Select
              label="Use case"
              value={state.use_case}
              options={filterOptions.use_cases}
              onChange={(v) => set('use_case', v)}
            />
            <Select
              label="Género"
              value={state.gender}
              options={filterOptions.genders}
              onChange={(v) => set('gender', v)}
            />
            <Select
              label="Banda de precio"
              value={state.price_band}
              options={filterOptions.price_bands}
              onChange={(v) => set('price_band', v)}
            />
            <Select
              label="País"
              value={state.country}
              options={filterOptions.countries}
              onChange={(v) => set('country', v)}
            />
            <Select
              label="Retailer"
              value={state.retailer}
              options={filterOptions.retailers.map((r) => ({ value: String(r.id), label: r.name }))}
              onChange={(v) => set('retailer', v)}
            />
          </div>
        ) : (
          <p className="mt-1 text-2xs text-nike-muted">
            No pudimos cargar las opciones de filtro. La búsqueda libre y la paginación siguen
            funcionando.
          </p>
        )}

        {activeCount > 0 && (
          <div className="mt-3 flex items-center gap-3 border-t border-surface-border pt-3">
            <span className="text-2xs text-nike-ink-soft">{activeCount} filtro(s) activo(s)</span>
            <button
              type="button"
              onClick={() => {
                setTyped('')
                setState({ ...EMPTY_PRODUCT_STATE })
              }}
              className="text-2xs font-semibold text-nike-red hover:underline"
            >
              Limpiar todo
            </button>
          </div>
        )}
      </Card>

      {/* ── Resultados ──────────────────────────────────────────── */}
      {error ? (
        <Card>
          <ErrorState
            title="No pudimos cargar el motor de inteligencia"
            description={error}
            onRetry={productsState.reload}
          />
        </Card>
      ) : productsState.data === null ? (
        <ProductGridSkeleton />
      ) : productsState.data.total === 0 ? (
        <Card>
          <EmptyState
            title={activeCount > 0 ? 'Ningún producto coincide con el filtro' : 'Sin productos'}
            description={
              activeCount > 0
                ? 'Probá quitar algún filtro o buscar por otro término.'
                : 'La tabla products está vacía. La etapa de seed/enrichment del pipeline todavía no cargó el catálogo.'
            }
            action={activeCount > 0 ? undefined : <CommandHint />}
          />
        </Card>
      ) : (
        <div
          aria-busy={productsState.refreshing}
          className={
            productsState.refreshing ? 'opacity-60 transition-opacity duration-fast' : undefined
          }
        >
          <div className="mb-3">
            <Pager
              page={state.page}
              pageSize={PRODUCTS_PAGE_SIZE}
              offset={productsState.data.offset}
              shown={productsState.data.items.length}
              total={productsState.data.total}
              noun="producto"
              busy={productsState.refreshing}
              onPage={(page) => setState((prev) => ({ ...prev, page }))}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {productsState.data.items.map((p) => (
              <ProductGridCard key={p.id} product={p} />
            ))}
          </div>

          <div className="mt-4">
            <Pager
              page={state.page}
              pageSize={PRODUCTS_PAGE_SIZE}
              offset={productsState.data.offset}
              shown={productsState.data.items.length}
              total={productsState.data.total}
              noun="producto"
              busy={productsState.refreshing}
              onPage={(page) => {
                setState((prev) => ({ ...prev, page }))
                window.scrollTo({ top: 0, behavior: 'smooth' })
              }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

type Option = string | { value: string; label: string }

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Option[]
  onChange: (value: string) => void
}) {
  const normalized = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o))
  return (
    <label className="block">
      <span className="label-caps mb-1 block">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={normalized.length === 0}
        className="field-control w-full text-xs disabled:bg-surface-sunken disabled:text-nike-muted"
      >
        <option value="">
          {normalized.length === 0 ? 'sin valores' : `Todos (${normalized.length})`}
        </option>
        {normalized.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function ProductGridCard({ product }: { product: Product }) {
  const isNike = product.is_focus === 1
  return (
    <Link
      href={`/intelligence/products/${product.id}`}
      // 40 tarjetas por página = 40 prefetch de RSC apenas se pinta la grilla.
      // Eso satura el main thread justo cuando el usuario está leyendo la lista,
      // para páginas que en su enorme mayoría no va a abrir.
      prefetch={false}
      className="flex min-h-[168px] flex-col rounded-card border bg-white p-3 shadow-card transition-shadow duration-fast hover:shadow-card-hover"
      style={{ borderColor: isNike ? 'rgba(227,24,55,0.35)' : '#EDEDED' }}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span
          className={`text-2xs font-bold uppercase tracking-wide ${
            isNike ? 'text-nike-red' : 'text-nike-muted'
          }`}
        >
          {text(product.brand)}
        </span>
        {product.country_code && <Tag>{product.country_code}</Tag>}
      </div>
      <p className="text-sm font-semibold leading-snug text-nike-ink">{product.product_name}</p>
      <p className="mt-0.5 text-2xs text-nike-muted">
        {[product.franchise, product.model, product.version].filter(Boolean).join(' · ') || '—'}
      </p>

      <div className="mt-2 flex flex-wrap gap-1">
        {product.category && <Tag title="Categoría">{product.category}</Tag>}
        {product.use_case && <Tag title="Use case">{product.use_case}</Tag>}
        {product.gender && <Tag title="Género">{product.gender}</Tag>}
        {product.performance_vs_lifestyle && (
          <Tag title="Performance vs lifestyle">{product.performance_vs_lifestyle}</Tag>
        )}
      </div>

      <div className="mt-auto flex items-end justify-between gap-2 pt-3">
        <div>
          <p className="tabular text-sm font-bold text-nike-ink">{money(product.msrp)}</p>
          <p className="text-2xs text-nike-muted">{text(product.price_band)}</p>
        </div>
        <LifecycleBadge stage={product.lifecycle_stage} />
      </div>
    </Link>
  )
}
