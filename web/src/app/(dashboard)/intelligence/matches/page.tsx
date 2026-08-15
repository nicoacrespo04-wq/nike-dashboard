'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useMemo, useState } from 'react'
import type { Product } from '@/types/intelligence'
import { getMatches, getProductMatches, getProducts } from '@/lib/intelligence/api'
import { useApi, useDebounced } from '@/lib/intelligence/useApi'
import { num, pctFromFraction, score, text } from '@/lib/format'
import { scoreTone } from '@/components/charts/palette'
import {
  AsyncSection,
  Card,
  EmptyState,
  ErrorState,
  PageIntro,
  SectionHeader,
  SkeletonText,
} from '@/components/ui'
import { ConfidenceBadge } from '@/components/intelligence/badges'
import { ContributionStack, FactorLegend } from '@/components/intelligence/FactorBreakdown'
import { CommandHint } from '@/components/intelligence/hints'
import { ProductLine, VersusLine } from '@/components/intelligence/ProductLine'

export default function CompetitiveMatchesPage() {
  return (
    <Suspense fallback={<SkeletonText lines={6} />}>
      <MatchesView />
    </Suspense>
  )
}

function MatchesView() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const productParam = searchParams?.get('product')
  const selectedId = productParam ? Number(productParam) : null

  const [q, setQ] = useState('')
  const debouncedQ = useDebounced(q, 300)

  // Catálogo Nike: el motor rankea competidores POR producto Nike.
  const nikeState = useApi(
    (signal) => getProducts({ q: debouncedQ || undefined, limit: 300 }, signal),
    [debouncedQ],
  )

  const nikeProducts: Product[] = useMemo(
    () => (nikeState.data?.items ?? []).filter((p) => p.is_focus === 1),
    [nikeState.data],
  )

  // Auto-selección del primer producto Nike si no vino nada por URL.
  useEffect(() => {
    if (selectedId === null && nikeProducts.length > 0) {
      const first = nikeProducts[0]
      if (first) router.replace(`/intelligence/matches?product=${first.id}`, { scroll: false })
    }
  }, [selectedId, nikeProducts, router])

  const matchesState = useApi(
    (signal) => getProductMatches(selectedId ?? 0, { limit: 10, with_factors: true }, signal),
    [selectedId],
    { enabled: selectedId !== null && selectedId > 0 },
  )

  // Fallback: sin productos Nike (catálogo vacío) mostramos matches globales.
  const globalState = useApi((signal) => getMatches({ limit: 20 }, signal), [], {
    enabled: nikeState.data !== null && nikeProducts.length === 0,
  })

  return (
    <div>
      <PageIntro
        question="¿Quién compite?"
        description="Qué productos compiten realmente entre sí. El motor evalúa 7 factores por par y persiste sólo los que superan el score mínimo configurado. Cada ranking es explicable, no una lista opaca."
      />

      <div className="grid gap-gutter xl:grid-cols-[320px_1fr]">
        {/* ── Selector de producto Nike ─────────────────────────── */}
        <Card className="h-fit xl:sticky xl:top-0">
          <SectionHeader
            title="Producto Nike"
            subtitle="Elegí el producto a analizar."
            className="mb-3"
          />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar Pegasus, Air Max…"
            className="field-control mb-3 w-full"
          />

          {nikeState.loading && nikeState.data === null ? (
            <SkeletonText lines={5} />
          ) : nikeState.error ? (
            // Sin catálogo no hay nada que rankear: el error se dice, no se
            // disfraza de "no hay productos".
            <ErrorState
              title="No pudimos cargar el catálogo"
              description={nikeState.error}
              onRetry={nikeState.reload}
              size="sm"
            />
          ) : nikeProducts.length === 0 ? (
            <EmptyState
              title="Sin productos Nike"
              description="No hay productos de la marca foco en el catálogo cargado. El pipeline todavía no pobló products/brands."
              size="sm"
            />
          ) : (
            <ul className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
              {nikeProducts.map((p) => {
                const active = p.id === selectedId
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() =>
                        router.replace(`/intelligence/matches?product=${p.id}`, { scroll: false })
                      }
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
          )}
        </Card>

        {/* ── Ranking ───────────────────────────────────────────── */}
        <div className="space-y-4">
          {nikeState.error && (
            <Card>
              <ErrorState
                title="No pudimos cargar el motor de inteligencia"
                description={nikeState.error}
                onRetry={nikeState.reload}
              />
            </Card>
          )}

          {nikeProducts.length === 0 && globalState.data && (
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

          {selectedId !== null && (
            <AsyncSection
              state={matchesState}
              loadingRows={5}
              isEmpty={(d) => d.matches.length === 0}
              empty={
                <Card>
                  <EmptyState
                    title="Este producto no tiene competidores calculados"
                    description="Ningún candidato superó el score mínimo de persistencia, o el motor de matching todavía no corrió para este producto."
                  />
                </Card>
              }
            >
              {(data) => (
                <>
                  <Card>
                    <div className="flex flex-wrap items-end justify-between gap-4">
                      <div className="min-w-0">
                        <p className="text-label font-bold uppercase text-nike-red">
                          Producto analizado
                        </p>
                        <h2 className="mt-1 text-xl font-bold text-nike-ink">
                          {text(data.product?.product_name)}
                        </h2>
                        <p className="mt-0.5 text-xs text-nike-ink-soft">
                          {[data.product?.franchise, data.product?.use_case, data.product?.category]
                            .filter(Boolean)
                            .join(' · ') || '—'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="tabular text-metric-sm font-extrabold text-nike-ink">
                          {num(data.matches.length)}
                        </p>
                        <p className="label-caps">competidores reales</p>
                      </div>
                    </div>
                  </Card>

                  <Card>
                    <SectionHeader
                      eyebrow="Ranking competitivo"
                      title={`Compiten con ${text(data.product?.product_name)}`}
                      subtitle="Ordenado por match score. La barra apilada muestra qué factor sostiene cada match; los factores sin datos quedan fuera y se ven en la leyenda del detalle."
                      className="mb-3"
                    />
                    <ol className="space-y-2.5">
                      {data.matches.map((m, i) => (
                        <li key={m.id}>
                          <Link
                            href={`/intelligence/matches/${m.id}`}
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
                              {i === 0 && <FactorLegend factors={m.factors} />}
                            </div>

                            <p className="mt-2 pl-9 text-2xs font-semibold text-nike-red">
                              Ver por qué el motor cree esto →
                            </p>
                          </Link>
                        </li>
                      ))}
                    </ol>
                  </Card>
                </>
              )}
            </AsyncSection>
          )}
        </div>
      </div>
    </div>
  )
}
