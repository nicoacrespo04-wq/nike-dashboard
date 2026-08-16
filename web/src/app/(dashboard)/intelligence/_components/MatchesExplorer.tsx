'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import type { Product, ProductListResponse, ProductMatchesResponse } from '@/types/intelligence'
import { getMatches, getProductMatches, getProducts } from '@/lib/intelligence/api'
import { depsKeyOf } from '@/lib/intelligence/depsKey'
import { useApi, useDebounced } from '@/lib/intelligence/useApi'
import { num, pctFromFraction, score, text } from '@/lib/format'
import { termIndex } from '@/lib/intelligence/glossary'
import { scoreTone } from '@/components/charts/palette'
import { Card, EmptyState, ErrorState, SectionHeader } from '@/components/ui'
import { ConfidenceBadge } from '@/components/intelligence/badges'
import { ContributionStack, FactorLegend } from '@/components/intelligence/FactorBreakdown'
import { CommandHint } from '@/components/intelligence/hints'
import { ProductLine, VersusLine } from '@/components/intelligence/ProductLine'
import {
  MATCH_PRODUCTS_PAGE_SIZE,
  MATCH_RANKING_LIMIT,
  type MatchesState,
  matchProductsQueryFrom,
  matchProductsQueryKey,
} from './matchParams'
import { MatchRankingSkeleton } from './skeletons'
import { syncUrl } from './urlState'

export interface MatchesExplorerProps {
  initialState: MatchesState
  /**
   * Marca foco resuelta en el servidor. Si viene `null`, el backend no permitió
   * identificarla y el selector cae al filtrado en el cliente.
   */
  focusBrand: string | null
  initialProducts: ProductListResponse | null
  initialMatches: ProductMatchesResponse | null
  initialError: string | null
}

/**
 * Competitive Matches — mitad cliente.
 *
 * Lo que cambió respecto de la versión anterior: el catálogo del selector se
 * pedía con `limit: 300` y después se filtraba `is_focus === 1` en el browser —
 * con un catálogo real eso es traer miles de productos para mostrar quince.
 * Ahora el servidor resuelve cuál es la marca foco y el listado va filtrado y
 * paginado por el backend (`brand=…&limit=25&offset=…`).
 *
 * Queda en el cliente porque el buscador y la selección de producto son
 * interacción pura: cambiar de producto no debería costar una navegación.
 */
export default function MatchesExplorer({
  initialState,
  focusBrand,
  initialProducts,
  initialMatches,
  initialError,
}: MatchesExplorerProps) {
  const [typed, setTyped] = useState(initialState.q)
  const debouncedQ = useDebounced(typed, 350)
  const [page, setPage] = useState(initialState.page)
  const [selectedId, setSelectedId] = useState<number | null>(initialState.selectedId)

  // Buscar reinicia la paginación: la página 3 de otra búsqueda no significa nada.
  useEffect(() => {
    setPage(0)
  }, [debouncedQ])

  const productsQuery = useMemo(
    () => matchProductsQueryFrom({ q: debouncedQ, page }, focusBrand),
    [debouncedQ, page, focusBrand],
  )
  const productsKey = matchProductsQueryKey(productsQuery)

  const productsState = useApi((signal) => getProducts(productsQuery, signal), [productsKey], {
    initialData: initialProducts,
    initialKey: depsKeyOf([
      matchProductsQueryKey(
        matchProductsQueryFrom({ q: initialState.q, page: initialState.page }, focusBrand),
      ),
    ]),
  })

  // Con `focusBrand` el backend ya devolvió sólo productos de la marca foco;
  // el filtro local queda como red de seguridad del camino degradado.
  const focusProducts: Product[] = useMemo(
    () => (productsState.data?.items ?? []).filter((p) => p.is_focus === 1),
    [productsState.data],
  )

  // Auto-selección: si la URL no traía producto, se analiza el primero.
  useEffect(() => {
    if (selectedId === null && focusProducts.length > 0) {
      const first = focusProducts[0]
      if (first) setSelectedId(first.id)
    }
  }, [selectedId, focusProducts])

  const matchesState = useApi(
    (signal) =>
      getProductMatches(
        selectedId ?? 0,
        { limit: MATCH_RANKING_LIMIT, with_factors: true },
        signal,
      ),
    [selectedId],
    {
      enabled: selectedId !== null && selectedId > 0,
      initialData: initialMatches,
      initialKey: depsKeyOf([initialState.selectedId]),
    },
  )

  // Fallback: sin productos foco (catálogo vacío) mostramos matches globales.
  const globalState = useApi((signal) => getMatches({ limit: 20 }, signal), [], {
    enabled: productsState.data !== null && focusProducts.length === 0,
  })

  useEffect(() => {
    syncUrl({ product: selectedId, q: debouncedQ, page: page || null })
  }, [selectedId, debouncedQ, page])

  const productsError = productsState.error ?? (productsState.data === null ? initialError : null)
  const total = productsState.data?.total ?? 0
  const shown = productsState.data?.items.length ?? 0
  const offset = productsState.data?.offset ?? 0
  const paged = focusBrand !== null

  return (
    <div className="grid gap-gutter xl:grid-cols-[320px_1fr]">
      {/* ── Selector de producto foco ─────────────────────────── */}
      <Card className="h-fit xl:sticky xl:top-0">
        <SectionHeader
          title="Producto Nike"
          subtitle="Elegí el producto a analizar."
          className="mb-3"
        />
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Buscar Pegasus, Air Max…"
          className="field-control mb-1 w-full"
        />
        <p className="mb-2 h-3 text-2xs text-nike-muted">
          {typed !== debouncedQ ? 'Buscando…' : ''}
        </p>

        {productsError ? (
          // Sin catálogo no hay nada que rankear: el error se dice, no se
          // disfraza de "no hay productos".
          <ErrorState
            title="No pudimos cargar el catálogo"
            description={productsError}
            onRetry={productsState.reload}
            size="sm"
          />
        ) : productsState.data === null ? (
          <ul className="space-y-1" aria-busy="true">
            {Array.from({ length: 8 }).map((_, i) => (
              <li key={i} className="rounded-lg px-2.5 py-2">
                <div className="skeleton h-3 w-4/5 rounded" />
                <div className="skeleton mt-1 h-2.5 w-1/2 rounded" />
              </li>
            ))}
          </ul>
        ) : focusProducts.length === 0 ? (
          <EmptyState
            title={debouncedQ ? 'Sin resultados' : 'Sin productos Nike'}
            description={
              debouncedQ
                ? 'Ningún producto de la marca foco coincide con la búsqueda.'
                : 'No hay productos de la marca foco en el catálogo cargado. El pipeline todavía no pobló products/brands.'
            }
            size="sm"
          />
        ) : (
          <div className={productsState.refreshing ? 'opacity-60' : undefined}>
            <ul className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
              {focusProducts.map((p) => {
                const active = p.id === selectedId
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(p.id)}
                      aria-pressed={active}
                      className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors duration-fast ${
                        active
                          ? 'border-nike-red bg-bml-lose-soft'
                          : 'border-transparent hover:border-surface-border hover:bg-surface-muted'
                      }`}
                    >
                      <span className="block truncate text-xs font-semibold text-nike-ink">
                        {p.product_name}
                      </span>
                      <span className="block truncate text-2xs text-nike-muted">
                        {[p.franchise, p.use_case].filter(Boolean).join(' · ') || '—'}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>

            {paged && (
              <div className="mt-3 flex items-center justify-between border-t border-surface-border pt-2">
                <span className="tabular text-2xs text-nike-muted">
                  {num(offset + 1)}–{num(offset + shown)} de {num(total)}
                </span>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    disabled={page === 0 || productsState.refreshing}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    className="rounded border border-surface-border px-2 py-0.5 text-2xs font-semibold text-nike-ink-soft disabled:opacity-40"
                  >
                    ←
                  </button>
                  <button
                    type="button"
                    disabled={offset + shown >= total || productsState.refreshing}
                    onClick={() => setPage((p) => p + 1)}
                    className="rounded border border-surface-border px-2 py-0.5 text-2xs font-semibold text-nike-ink-soft disabled:opacity-40"
                  >
                    →
                  </button>
                </div>
              </div>
            )}
            {!paged && total > MATCH_PRODUCTS_PAGE_SIZE && (
              <p className="mt-3 border-t border-surface-border pt-2 text-2xs text-nike-muted">
                Mostrando los primeros {num(shown)} productos foco del lote: el backend todavía no
                expone un filtro por marca foco, así que la lista no se puede paginar.
              </p>
            )}
          </div>
        )}
      </Card>

      {/* ── Ranking ───────────────────────────────────────────── */}
      <div className="space-y-4">
        {focusProducts.length === 0 && globalState.data && (
          <Card>
            <SectionHeader
              title="Matches persistidos"
              subtitle="Vista global mientras el catálogo Nike no esté disponible."
              className="mb-3"
            />
            {globalState.data.items.length === 0 ? (
              <EmptyState
                title="Sin matches competitivos"
                description="La tabla competitive_matches está vacía. El motor necesita productos enriquecidos de Nike y de la competencia para poder emparejarlos."
                action={<CommandHint />}
              />
            ) : (
              <ul className="divide-y divide-surface-border">
                {globalState.data.items.map((m) => (
                  <li key={m.id} className="py-2.5">
                    <Link
                      href={`/intelligence/matches/${m.id}`}
                      prefetch={false}
                      className="flex items-center gap-3"
                    >
                      <span
                        className="tabular w-14 text-base font-bold"
                        style={{ color: scoreTone(m.match_score) }}
                      >
                        {score(m.match_score)}%
                      </span>
                      <span className="min-w-0 flex-1">
                        <VersusLine nike={m.nike_product} competitor={m.competitor_product} />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {selectedId !== null &&
          (matchesState.error ? (
            <Card>
              <ErrorState
                title="No pudimos cargar el ranking competitivo"
                description={matchesState.error}
                onRetry={matchesState.reload}
              />
            </Card>
          ) : matchesState.data === null ? (
            <Card>
              <MatchRankingSkeleton />
            </Card>
          ) : matchesState.data.matches.length === 0 ? (
            <Card>
              <EmptyState
                title="Este producto no tiene competidores calculados"
                description="Ningún candidato superó el score mínimo de persistencia, o el motor de matching todavía no corrió para este producto."
              />
            </Card>
          ) : (
            <div
              className={matchesState.refreshing ? 'space-y-4 opacity-60' : 'space-y-4'}
              aria-busy={matchesState.refreshing}
            >
              <Card>
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-label font-bold uppercase text-nike-red">
                      Producto analizado
                    </p>
                    <h2 className="mt-1 text-xl font-bold text-nike-ink">
                      {text(matchesState.data.product?.product_name)}
                    </h2>
                    <p className="mt-0.5 text-xs text-nike-ink-soft">
                      {[
                        matchesState.data.product?.franchise,
                        matchesState.data.product?.use_case,
                        matchesState.data.product?.category,
                      ]
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="tabular text-metric-sm font-extrabold text-nike-ink">
                      {num(matchesState.data.matches.length)}
                    </p>
                    <p className="label-caps">competidores reales</p>
                  </div>
                </div>
              </Card>

              <Card>
                <SectionHeader
                  eyebrow="Ranking competitivo"
                  title={`Compiten con ${text(matchesState.data.product?.product_name)}`}
                  subtitle="Ordenado por match score. La barra apilada muestra qué factor sostiene cada match; los factores sin datos quedan fuera y se ven en la leyenda del detalle."
                  className="mb-3"
                />
                <ol className="space-y-2.5">
                  {matchesState.data.matches.map((m, i) => (
                    <li key={m.id}>
                      <Link
                        href={`/intelligence/matches/${m.id}`}
                        prefetch={false}
                        className="block rounded-lg border border-surface-border p-3 transition-colors duration-fast hover:border-surface-border-strong hover:bg-surface-muted"
                      >
                        <div className="flex items-start gap-3">
                          <span className="tabular w-6 flex-shrink-0 pt-1 text-center text-sm font-bold text-nike-muted">
                            {i + 1}
                          </span>

                          <span className="min-w-0 flex-1">
                            <ProductLine product={m.competitor} role="competitor" link={false} />
                          </span>

                          <span className="w-40 flex-shrink-0 text-right">
                            <span
                              className="tabular block text-metric-sm font-extrabold leading-none"
                              style={{ color: scoreTone(m.match_score) }}
                            >
                              {score(m.match_score)}
                              <span className="text-sm">%</span>
                            </span>
                            <span className="mt-1 block">
                              <ConfidenceBadge confidence={m.confidence} coverage={m.coverage} />
                            </span>
                            <span className="tabular mt-1 block text-2xs text-nike-muted">
                              cobertura {pctFromFraction(m.coverage, 0)}
                            </span>
                          </span>
                        </div>

                        <div className="mt-3 pl-9">
                          <ContributionStack factors={m.factors} height={14} />
                        </div>

                        <p className="mt-2 pl-9 text-2xs font-semibold text-nike-red">
                          Ver por qué el motor cree esto →
                        </p>
                      </Link>

                      {/* La leyenda va FUERA del link: sus tooltips del glosario
                          son botones y un `<a>` no puede contenerlos. */}
                      {i === 0 && (
                        <div className="px-3 pl-12">
                          <FactorLegend
                            factors={m.factors}
                            terms={termIndex(matchesState.data?.glossary, 'competitive_match')}
                          />
                        </div>
                      )}
                    </li>
                  ))}
                </ol>
              </Card>
            </div>
          ))}
      </div>
    </div>
  )
}
