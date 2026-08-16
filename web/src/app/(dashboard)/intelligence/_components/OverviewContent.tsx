import Link from 'next/link'
import type {
  BrandInsight,
  MarketSignal,
  MatchListItem,
  Opportunity,
  OverviewResponse,
  RetailMedia,
} from '@/types/intelligence'
import { evidenceOf, hasEvidence } from '@/lib/intelligence/insights'
import { dec, num, pct, score, signed, text } from '@/lib/format'
import {
  entityLabel,
  recommendationStyle,
  scoreTone,
  severityStyle,
  signalTypeLabel,
} from '@/components/charts/palette'
import { Card, EmptyState, KPICard, MeterBar, SectionHeader } from '@/components/ui'
import { ConfidenceBadge, SeverityBadge } from '@/components/intelligence/badges'
import { CommandHint } from '@/components/intelligence/hints'
import { OpportunityCard } from '@/components/intelligence/OpportunityCard'
import { VersusLine } from '@/components/intelligence/ProductLine'

/**
 * Cuerpo del Executive Overview.
 *
 * Todo Server Component: es una foto, no una app. No hay un solo filtro que el
 * usuario pueda mover acá, así que no hay razón para mandar el árbol entero al
 * browser ni para volver a pedir los datos en cada visita — llegan ya
 * renderizados en el HTML.
 */
export default function OverviewContent({ data }: { data: OverviewResponse }) {
  const k = data.kpis
  const nothingAtAll =
    k.products === 0 &&
    k.matches === 0 &&
    k.opportunities === 0 &&
    k.brand_insights === 0 &&
    k.retail_media_opportunities === 0

  return (
    <div className="space-y-6">
      {/* ── KPIs ─────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-gutter md:grid-cols-3">
        <KPICard
          title="Oportunidades críticas"
          value={k.critical_opportunities}
          subtitle={`${num(k.high_opportunities)} de severidad alta`}
          color={severityStyle('CRITICAL').color}
          valueSize="md"
          hint="Oportunidades con Business Importance por encima del umbral crítico de config/weights.yaml."
        />
        <KPICard
          title="Oportunidades totales"
          value={k.opportunities}
          subtitle="ordenadas por Business Importance"
          valueSize="md"
        />
        <KPICard
          title="Matches competitivos"
          value={k.matches}
          subtitle="pares Nike ↔ competidor explicados"
          valueSize="md"
        />
        <KPICard
          title="Retail media"
          value={k.retail_media_opportunities}
          subtitle="casos visibilidad vs. markdown"
          valueSize="md"
        />
        <KPICard
          title="Catálogo analizado"
          value={k.products}
          subtitle={`${num(k.nike_products)} Nike · ${num(k.brands)} marcas · ${num(k.retailers)} retailers`}
          valueSize="md"
        />
        <KPICard
          title="Insights de consumidor"
          value={k.brand_insights}
          subtitle="Argentina, con evidencia"
          valueSize="md"
        />
      </div>

      {nothingAtAll && (
        <Card>
          <EmptyState
            title="Pipeline vacío"
            description="Todas las tablas de inteligencia están sin datos. La API responde 200, así que la UI está sana: falta correr el pipeline."
            action={<CommandHint />}
          />
        </Card>
      )}

      {/* ── Riesgos y oportunidades ──────────────────────── */}
      <div className="grid gap-gutter xl:grid-cols-2">
        <Card>
          <SectionHeader
            eyebrow="¿Cuánto importa?"
            title="Riesgos principales"
            subtitle="Precio, amenaza competitiva y distribución, ordenados por importancia comercial."
            actions={
              <Link
                href="/intelligence/opportunities"
                className="text-2xs font-semibold text-nike-red hover:underline"
              >
                Ver todos →
              </Link>
            }
            className="mb-3"
          />
          <RiskList items={data.top_risks} />
        </Card>

        <Card>
          <SectionHeader
            eyebrow="¿Qué hacemos?"
            title="Oportunidades principales"
            subtitle="Lo que más mueve la aguja comercial ahora mismo."
            className="mb-3"
          />
          <RiskList items={data.top_opportunities} />
        </Card>
      </div>

      {/* ── Match destacado ──────────────────────────────── */}
      <Card>
        <SectionHeader
          eyebrow="¿Quién compite?"
          title="Matches competitivos más fuertes"
          subtitle="Qué productos compiten realmente entre sí, según los 7 factores del motor."
          actions={
            <Link
              href="/intelligence/matches"
              className="text-2xs font-semibold text-nike-red hover:underline"
            >
              Explorar matches →
            </Link>
          }
          className="mb-3"
        />
        <MatchStrip items={data.top_matches} />
      </Card>

      {/* ── Momentum + retail media ──────────────────────── */}
      <div className="grid gap-gutter xl:grid-cols-2">
        <Card>
          <SectionHeader
            eyebrow="¿Qué está pasando?"
            title="Momentum de competidores"
            subtitle="Señales de mercado con mayor aceleración en el período."
            className="mb-3"
          />
          <MomentumList items={data.competitor_momentum} />
        </Card>

        <Card>
          <SectionHeader
            eyebrow="¿Qué hacemos?"
            title="Oportunidades de retail media"
            subtitle="Casos donde conviene reasignar inversión de markdown a visibilidad."
            actions={
              <Link
                href="/intelligence/retail-media"
                className="text-2xs font-semibold text-nike-red hover:underline"
              >
                Ver todas →
              </Link>
            }
            className="mb-3"
          />
          <RetailMediaStrip items={data.retail_media} />
        </Card>
      </div>

      {/* ── Assortment gaps + brand ──────────────────────── */}
      <div className="grid gap-gutter xl:grid-cols-2">
        <Card>
          <SectionHeader
            eyebrow="¿Cuánto importa?"
            title="Gaps de surtido"
            subtitle="Segmentos donde la competencia tiene profundidad y Nike no."
            className="mb-3"
          />
          {data.assortment_gaps.length === 0 ? (
            <EmptyState
              title="Sin gaps de surtido detectados"
              description="No hay oportunidades de la familia assortment entre las de mayor importancia. Puede ser buena noticia — o que la regla todavía no corrió."
              size="sm"
            />
          ) : (
            <div className="grid gap-3">
              {data.assortment_gaps.slice(0, 2).map((o) => (
                <OpportunityCard key={o.id} opportunity={o} compact />
              ))}
            </div>
          )}
        </Card>

        <Card>
          <SectionHeader
            eyebrow="¿Qué está pasando?"
            title="Consumidor argentino — destacados"
            subtitle="Insights con respaldo cuantitativo y evidencia pública agregada."
            actions={
              <Link
                href="/intelligence/brand"
                className="text-2xs font-semibold text-nike-red hover:underline"
              >
                Ver panel completo →
              </Link>
            }
            className="mb-3"
          />
          <BrandStrip items={data.brand_highlights} />
        </Card>
      </div>
    </div>
  )
}

// ── Sub-vistas ──────────────────────────────────────────────────────

function RiskList({ items }: { items: Opportunity[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin oportunidades calculadas"
        description="La etapa de opportunities no produjo resultados todavía. Cuando corra, esta lista se ordena sola por Business Importance."
        size="sm"
      />
    )
  }
  return (
    <ul className="divide-y divide-surface-border">
      {items.map((o) => {
        const sev = severityStyle(o.severity)
        return (
          <li key={o.id} className="py-3 first:pt-0 last:pb-0">
            <div className="flex items-start gap-3">
              <div className="w-12 flex-shrink-0 text-center">
                <span
                  className="tabular block text-base font-bold leading-none"
                  style={{ color: sev.color }}
                >
                  {score(o.business_importance)}
                </span>
                <span className="text-2xs text-nike-muted">imp.</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <SeverityBadge severity={o.severity} />
                  <ConfidenceBadge confidence={o.confidence} />
                </div>
                <p className="text-xs font-semibold leading-snug text-nike-ink">{o.title}</p>
                {o.recommendation?.action && (
                  <p className="mt-1 text-2xs leading-relaxed text-nike-ink-soft">
                    <span className="font-semibold text-nike-red">Acción: </span>
                    {o.recommendation.action.replace(/_/g, ' ').toLowerCase()}
                  </p>
                )}
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function MatchStrip({ items }: { items: MatchListItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin matches competitivos"
        description="La tabla competitive_matches está vacía. El motor de matching necesita productos enriquecidos para poder comparar."
        size="sm"
      />
    )
  }
  return (
    <ul className="grid gap-2 lg:grid-cols-2">
      {items.map((m) => (
        <li key={m.id}>
          <Link
            href={`/intelligence/matches/${m.id}`}
            prefetch={false}
            className="block rounded-lg border border-surface-border px-3 py-2.5 transition-colors duration-fast hover:border-surface-border-strong hover:bg-surface-muted"
          >
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="tabular text-lg font-bold" style={{ color: scoreTone(m.match_score) }}>
                {score(m.match_score)}%
              </span>
              <ConfidenceBadge confidence={m.confidence} />
            </div>
            <VersusLine nike={m.nike_product} competitor={m.competitor_product} />
          </Link>
        </li>
      ))}
    </ul>
  )
}

function MomentumList({ items }: { items: MarketSignal[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin señales de mercado"
        description="La tabla market_signals está vacía. La etapa de brand intelligence es la que produce momentum de marcas y franquicias."
        size="sm"
      />
    )
  }
  const maxValue = Math.max(...items.map((s) => Math.abs(s.value ?? 0)), 1)
  return (
    <ul className="space-y-2.5">
      {items.map((s) => {
        const accel = s.acceleration ?? 0
        const up = accel >= 0
        return (
          <li key={s.id} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-nike-ink">
                {entityLabel(s.entity_type, s.entity_id)}
              </p>
              <p className="text-2xs text-nike-muted">
                {signalTypeLabel(s.signal_type)} · {s.entity_type}
              </p>
              <div className="mt-1">
                <MeterBar value={Math.abs(s.value ?? 0)} max={maxValue} color="#2A78D6" height={5} />
              </div>
            </div>
            <div className="text-right">
              <p className="tabular text-sm font-bold" style={{ color: up ? '#0A6B0A' : '#8E2020' }}>
                <span aria-hidden="true">{up ? '▲' : '▼'}</span> {signed(accel, 2)}
              </p>
              <p className="text-2xs text-nike-muted">aceleración</p>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function RetailMediaStrip({ items }: { items: RetailMedia[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin oportunidades de retail media"
        description="La etapa retail_media no generó casos. Necesita precios, stock y matches para poder decidir entre visibilidad y descuento."
        size="sm"
      />
    )
  }
  return (
    <ul className="space-y-2">
      {items.map((rm) => {
        const rec = recommendationStyle(rm.recommendation)
        return (
          <li
            key={rm.id}
            className="rounded-lg border border-surface-border px-3 py-2.5"
            style={{ borderLeft: `3px solid ${rec.color}` }}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-bold text-nike-ink">{rec.label}</p>
                <p className="mt-0.5 truncate text-2xs text-nike-ink-soft">
                  {text(rm.nike_product?.product_name)}
                  {rm.retailer ? ` · ${rm.retailer.name}` : ''}
                </p>
              </div>
              <span
                className="tabular flex-shrink-0 text-base font-bold"
                style={{ color: rec.color }}
              >
                {score(rm.score)}
              </span>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function BrandStrip({ items }: { items: BrandInsight[] }) {
  const withEvidence = items.filter(hasEvidence)

  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin insights de consumidor"
        description="La tabla brand_insights está vacía. Cada insight requiere volumen de señal y evidencia — sin respaldo, no se genera."
        size="sm"
      />
    )
  }
  if (withEvidence.length === 0) {
    return (
      <EmptyState
        title="Insights sin evidencia adjunta"
        description={`Hay ${items.length} insight(s) pero ninguno trae evidencia. Por regla del producto no se muestran insights sin respaldo.`}
        size="sm"
      />
    )
  }
  return (
    <ul className="space-y-2.5">
      {withEvidence.slice(0, 5).map((i) => (
        <li key={i.id} className="border-l-2 border-surface-border pl-3">
          <div className="flex items-center gap-2">
            <span className="text-2xs font-semibold uppercase tracking-wide text-nike-muted">
              {text(i.brand)} · {text(i.topic)}
            </span>
            <ConfidenceBadge confidence={i.confidence} />
          </div>
          <p className="mt-0.5 text-xs leading-snug text-nike-ink">{text(i.insight_text)}</p>
          <p className="tabular mt-1 text-2xs text-nike-muted">
            {num(i.signal_volume)} señales · tendencia {pct(i.trend, 0)} · sentimiento{' '}
            {dec(i.sentiment, 2)} · {evidenceOf(i).length} evidencia(s)
          </p>
        </li>
      ))}
    </ul>
  )
}
