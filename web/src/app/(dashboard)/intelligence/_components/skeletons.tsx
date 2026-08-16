/**
 * Siluetas de carga de las pantallas de INTELLIGENCE.
 *
 * Regla: el esqueleto tiene la FORMA del contenido final — misma grilla, misma
 * cantidad de columnas, misma altura de tarjeta — para que cuando lleguen los
 * datos nada se mueva. Un esqueleto genérico de tres renglones no evita el
 * salto de layout: lo disimula durante 200ms y después empuja media pantalla.
 *
 * Usa las primitivas compartidas (`Skeleton`, `SkeletonText`), no versiones
 * paralelas.
 */

import { Card, Skeleton, SkeletonText } from '@/components/ui'

/** Envoltorio accesible: un solo `aria-live` por pantalla en carga. */
export function LoadingRegion({ children }: { children: React.ReactNode }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className="space-y-6">
      <span className="sr-only">Cargando datos del motor de inteligencia</span>
      {children}
    </div>
  )
}

/** Encabezado de pantalla: pregunta + descripción + acción. */
export function PageIntroSkeleton({ action = false }: { action?: boolean }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton width="180px" className="h-3" />
        <Skeleton width="min(680px, 90%)" className="h-3" />
        <Skeleton width="min(520px, 70%)" className="h-3" />
      </div>
      {action && <Skeleton width="200px" className="h-9 rounded-lg" />}
    </div>
  )
}

/** Cinta de estado del pipeline (altura exacta del banner real). */
export function PipelineBannerSkeleton() {
  return <Skeleton className="mb-4 h-9 w-full rounded-lg" />
}

/** Grilla de KPIs. `md:grid-cols-3` como el Executive Overview. */
export function KpiGridSkeleton({ count = 6, columns = 3 }: { count?: number; columns?: 3 | 4 }) {
  return (
    <div
      className={`grid grid-cols-2 gap-gutter ${columns === 4 ? 'lg:grid-cols-4' : 'md:grid-cols-3'}`}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-card border border-surface-border bg-white p-4 shadow-card">
          <Skeleton width="70%" className="h-2.5" />
          <Skeleton width="45%" className="mt-3 h-7 rounded" />
          <Skeleton width="85%" className="mt-2.5 h-2.5" />
        </div>
      ))}
    </div>
  )
}

/** Lista de renglones con score a la izquierda (riesgos / oportunidades). */
export function RankedListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <ul className="divide-y divide-surface-border">
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
          <Skeleton width="48px" className="h-6 rounded" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton width="120px" className="h-3 rounded-pill" />
            <Skeleton width="92%" className="h-3" />
            <Skeleton width="60%" className="h-2.5" />
          </div>
        </li>
      ))}
    </ul>
  )
}

/** Tarjeta con encabezado de sección + cuerpo. */
export function PanelSkeleton({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <Card className={className}>
      <div className="mb-3 space-y-1.5">
        <Skeleton width="110px" className="h-2.5" />
        <Skeleton width="260px" className="h-4" />
        <Skeleton width="min(420px, 80%)" className="h-2.5" />
      </div>
      {children}
    </Card>
  )
}

/** Grilla del Product Explorer: misma densidad que las tarjetas reales. */
export function ProductGridSkeleton({ cards = 12 }: { cards?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      {Array.from({ length: cards }).map((_, i) => (
        <div
          key={i}
          className="flex h-[168px] flex-col rounded-card border border-surface-border bg-white p-3 shadow-card"
        >
          <div className="mb-1.5 flex items-center justify-between">
            <Skeleton width="60px" className="h-2.5" />
            <Skeleton width="28px" className="h-3.5 rounded" />
          </div>
          <Skeleton width="88%" className="h-3.5" />
          <Skeleton width="55%" className="mt-1.5 h-2.5" />
          <div className="mt-2 flex gap-1">
            <Skeleton width="52px" className="h-4 rounded" />
            <Skeleton width="44px" className="h-4 rounded" />
            <Skeleton width="38px" className="h-4 rounded" />
          </div>
          <div className="mt-auto flex items-end justify-between pt-3">
            <div className="space-y-1">
              <Skeleton width="70px" className="h-4" />
              <Skeleton width="46px" className="h-2.5" />
            </div>
            <Skeleton width="64px" className="h-4 rounded-pill" />
          </div>
        </div>
      ))}
    </div>
  )
}

/** Barra de filtros del Product Explorer (búsqueda + 9 selects). */
export function FilterPanelSkeleton({ selects = 9 }: { selects?: number }) {
  return (
    <Card className="mb-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="xl:col-span-2">
          <Skeleton width="260px" className="mb-1 h-2.5" />
          <Skeleton className="h-9 rounded-lg" />
        </div>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: selects }).map((_, i) => (
          <div key={i}>
            <Skeleton width="72px" className="mb-1 h-2.5" />
            <Skeleton className="h-9 rounded-lg" />
          </div>
        ))}
      </div>
    </Card>
  )
}

/** Grilla de tarjetas de oportunidad. */
export function OpportunityGridSkeleton({ cards = 6 }: { cards?: number }) {
  return (
    <div className="grid gap-gutter lg:grid-cols-2 2xl:grid-cols-3">
      {Array.from({ length: cards }).map((_, i) => (
        <div
          key={i}
          className="h-[260px] rounded-card border border-surface-border bg-white p-4 shadow-card"
        >
          <div className="flex items-center gap-2">
            <Skeleton width="70px" className="h-4 rounded" />
            <Skeleton width="58px" className="h-4 rounded" />
          </div>
          <Skeleton width="92%" className="mt-3 h-4" />
          <SkeletonText lines={3} className="mt-2.5" />
          <Skeleton className="mt-4 h-2 rounded-pill" />
          <SkeletonText lines={2} className="mt-3" lastLineWidth="45%" />
        </div>
      ))}
    </div>
  )
}

/** Filas anchas de retail media (3 columnas internas). */
export function RetailMediaListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="grid gap-4 rounded-card border border-surface-border bg-white p-4 shadow-card xl:grid-cols-[1.5fr_1fr_1fr]"
        >
          <div className="space-y-2.5">
            <div className="flex gap-2">
              <Skeleton width="150px" className="h-4 rounded" />
              <Skeleton width="60px" className="h-4 rounded" />
            </div>
            <Skeleton width="85%" className="h-3.5" />
            <Skeleton width="65%" className="h-3.5" />
            <SkeletonText lines={2} />
          </div>
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, j) => (
              <div key={j}>
                <Skeleton width="70%" className="h-2.5" />
                <Skeleton className="mt-1 h-1.5 rounded-sm" />
              </div>
            ))}
          </div>
          <div className="space-y-3">
            <Skeleton width="60%" className="h-6" />
            <Skeleton className="h-2 rounded-pill" />
            <SkeletonText lines={4} />
          </div>
        </div>
      ))}
    </div>
  )
}

/** Ranking de matches: número, producto y barra de contribución. */
export function MatchRankingSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <ol className="space-y-2.5">
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i} className="rounded-lg border border-surface-border p-3">
          <div className="flex items-start gap-3">
            <Skeleton width="20px" className="h-4" />
            <div className="min-w-0 flex-1 space-y-1.5">
              <Skeleton width="70%" className="h-3.5" />
              <Skeleton width="45%" className="h-2.5" />
            </div>
            <div className="w-40 space-y-1.5">
              <Skeleton width="80px" className="ml-auto h-6" />
              <Skeleton width="64px" className="ml-auto h-3.5 rounded-pill" />
            </div>
          </div>
          <Skeleton className="mt-3 h-3.5 rounded" />
        </li>
      ))}
    </ol>
  )
}

/** Columna izquierda del selector de producto Nike. */
export function ProductPickerSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <Card className="h-fit">
      <div className="mb-3 space-y-1.5">
        <Skeleton width="140px" className="h-4" />
        <Skeleton width="200px" className="h-2.5" />
      </div>
      <Skeleton className="mb-3 h-9 rounded-lg" />
      <ul className="space-y-1">
        {Array.from({ length: rows }).map((_, i) => (
          <li key={i} className="rounded-lg px-2.5 py-2">
            <Skeleton width="85%" className="h-3" />
            <Skeleton width="55%" className="mt-1 h-2.5" />
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** Tarjetas de insight de consumidor (dos columnas). */
export function InsightGridSkeleton({ cards = 4 }: { cards?: number }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {Array.from({ length: cards }).map((_, i) => (
        <div
          key={i}
          className="h-[300px] rounded-card border border-surface-border bg-white p-4 shadow-card"
        >
          <div className="flex gap-1.5">
            <Skeleton width="60px" className="h-4 rounded" />
            <Skeleton width="80px" className="h-4 rounded" />
            <Skeleton width="52px" className="h-4 rounded" />
          </div>
          <SkeletonText lines={2} className="mt-3" lastLineWidth="70%" />
          <div className="mt-3 grid grid-cols-3 gap-2 rounded-lg border border-surface-border bg-surface-muted px-3 py-2">
            {Array.from({ length: 3 }).map((_, j) => (
              <div key={j}>
                <Skeleton width="60%" className="h-2" />
                <Skeleton width="70%" className="mt-1.5 h-4" />
              </div>
            ))}
          </div>
          <Skeleton className="mt-3 h-1.5 rounded-sm" />
          <SkeletonText lines={4} className="mt-4" />
        </div>
      ))}
    </div>
  )
}

/** Silueta de un gráfico de barras horizontales. */
export function BarChartSkeleton({ bars = 10 }: { bars?: number }) {
  const widths = ['92%', '84%', '77%', '70%', '63%', '57%', '50%', '43%', '36%', '29%', '24%', '18%']
  return (
    <div className="space-y-2.5" aria-hidden="true">
      {Array.from({ length: bars }).map((_, i) => (
        <div key={i} className="grid grid-cols-[150px_1fr] items-center gap-3">
          <Skeleton width="90%" className="h-2.5" />
          <Skeleton width={widths[i % widths.length]} className="h-3 rounded-sm" />
        </div>
      ))}
    </div>
  )
}

/** Tabla de momentum. */
export function MomentumTableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      <Skeleton className="h-3" />
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="grid grid-cols-[1.4fr_1fr_1fr_0.6fr_0.7fr] items-center gap-3 py-1">
          <Skeleton width="85%" className="h-3" />
          <Skeleton width="70%" className="h-2.5" />
          <Skeleton width="80%" className="h-2.5" />
          <Skeleton width="60%" className="h-2.5" />
          <Skeleton width="70%" className="h-2.5" />
        </div>
      ))}
    </div>
  )
}

// ── Siluetas de pantalla completa ───────────────────────────────────
//
// Se usan como `fallback` del `<Suspense>` que envuelve la parte de cada
// página que depende del backend. NO se usan como `loading.tsx`: un
// `loading.tsx` viaja en el prefetch y React lo commitea sí o sí en cada
// navegación —con su throttle de fallback incluido—, aunque el servidor tenga
// la respuesta cacheada y pueda contestar en 5ms. Medido en esta app, eso
// costaba ~230ms extra por navegación. Como `fallback` de un `Suspense` de
// página, en cambio, el esqueleto sólo se pinta cuando el servidor realmente
// tarda: con datos reales y el backend frío se ve; con caché caliente el
// contenido llega directo.

export function OverviewSkeleton() {
  return (
    <LoadingRegion>
      <KpiGridSkeleton count={6} />
      <div className="grid gap-gutter xl:grid-cols-2">
        <PanelSkeleton>
          <RankedListSkeleton rows={6} />
        </PanelSkeleton>
        <PanelSkeleton>
          <RankedListSkeleton rows={6} />
        </PanelSkeleton>
      </div>
      <PanelSkeleton>
        <div className="grid gap-2 lg:grid-cols-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-[72px] rounded-lg border border-surface-border px-3 py-2.5">
              <Skeleton width="64px" className="h-5" />
              <Skeleton width="80%" className="mt-2 h-3" />
            </div>
          ))}
        </div>
      </PanelSkeleton>
      <div className="grid gap-gutter xl:grid-cols-2">
        <PanelSkeleton>
          <RankedListSkeleton rows={5} />
        </PanelSkeleton>
        <PanelSkeleton>
          <RankedListSkeleton rows={5} />
        </PanelSkeleton>
      </div>
    </LoadingRegion>
  )
}

export function ProductExplorerSkeleton() {
  return (
    <LoadingRegion>
      <FilterPanelSkeleton />
      <div className="flex items-center justify-between">
        <Skeleton width="280px" className="h-3" />
        <Skeleton width="330px" className="h-6 rounded-lg" />
      </div>
      <ProductGridSkeleton />
    </LoadingRegion>
  )
}

export function OpportunityCenterSkeleton() {
  return (
    <LoadingRegion>
      <KpiGridSkeleton count={4} columns={4} />
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          {['110px', '150px', '160px', '170px'].map((w, i) => (
            <div key={i}>
              <Skeleton width={w} className="mb-1 h-2.5" />
              <Skeleton width={w} className="h-8 rounded-lg" />
            </div>
          ))}
        </div>
      </Card>
      <OpportunityGridSkeleton />
    </LoadingRegion>
  )
}

export function RetailMediaSkeleton() {
  return (
    <LoadingRegion>
      <div className="grid gap-gutter sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="h-[124px] rounded-card border border-surface-border bg-white px-4 py-3 shadow-card"
          >
            <Skeleton width="80%" className="h-3" />
            <Skeleton width="40%" className="mt-2 h-5" />
            <Skeleton width="90%" className="mt-3 h-2.5" />
          </div>
        ))}
      </div>
      <RetailMediaListSkeleton rows={3} />
    </LoadingRegion>
  )
}

export function MatchesSkeleton() {
  return (
    <LoadingRegion>
      <div className="grid gap-gutter xl:grid-cols-[320px_1fr]">
        <ProductPickerSkeleton />
        <Card>
          <MatchRankingSkeleton rows={6} />
        </Card>
      </div>
    </LoadingRegion>
  )
}

export function BrandInsightsSkeleton() {
  return (
    <LoadingRegion>
      <KpiGridSkeleton count={4} columns={4} />
      <Card>
        <InsightGridSkeleton />
      </Card>
    </LoadingRegion>
  )
}
