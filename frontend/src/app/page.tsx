"use client";

import Link from "next/link";
import type { BrandInsight, MarketSignal, MatchListItem, Opportunity, RetailMedia } from "@/types";
import { getOverview } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { evidenceOf, hasEvidence } from "@/lib/insights";
import { dec, num, pct, score, signed, text } from "@/lib/format";
import { entityLabel, recommendationStyle, scoreTone, severityStyle, signalTypeLabel } from "@/lib/viz";
import { ConfidenceBadge, SeverityBadge } from "@/components/badges";
import { OpportunityCard } from "@/components/OpportunityCard";
import { VersusLine } from "@/components/ProductLine";
import {
  AsyncSection,
  Card,
  EmptyState,
  MeterBar,
  PageHeader,
  SectionHeader,
  StatTile,
} from "@/components/ui";

const PIPELINE_HINT =
  "cd backend && python -m app.pipeline   # poblar la base    ·    uvicorn app.main:app --port 8000";

export default function OverviewPage() {
  const state = useApi((signal) => getOverview({ country: "AR", limit: 6 }, signal), []);

  return (
    <div>
      <PageHeader
        question="What is happening?"
        title="Executive Overview"
        description="La foto del mercado en 10 segundos: dónde está el riesgo, dónde está la oportunidad, quién nos está compitiendo y qué hacer al respecto."
        right={
          <Link
            href="/opportunities"
            className="inline-block rounded bg-nike-red px-4 py-2 text-xs font-bold text-white transition hover:bg-nike-red-dark"
          >
            Ir al Opportunity Center →
          </Link>
        }
      />

      <AsyncSection
        state={state}
        loadingRows={6}
        empty={
          <EmptyState
            title="El motor todavía no tiene datos"
            description="El backend responde correctamente pero ninguna etapa del pipeline pobló sus tablas."
            hint={PIPELINE_HINT}
          />
        }
      >
        {(data) => {
          const k = data.kpis;
          const nothingAtAll =
            k.products === 0 &&
            k.matches === 0 &&
            k.opportunities === 0 &&
            k.brand_insights === 0 &&
            k.retail_media_opportunities === 0;

          return (
            <div className="space-y-6">
              {/* ── KPIs ─────────────────────────────────────────── */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                <StatTile
                  label="Oportunidades críticas"
                  value={k.critical_opportunities}
                  sub={`${num(k.high_opportunities)} de severidad alta`}
                  accent
                  tone={severityStyle("CRITICAL").color}
                />
                <StatTile
                  label="Oportunidades totales"
                  value={k.opportunities}
                  sub="ordenadas por Business Importance"
                />
                <StatTile
                  label="Matches competitivos"
                  value={k.matches}
                  sub="pares Nike ↔ competidor explicados"
                />
                <StatTile
                  label="Retail media"
                  value={k.retail_media_opportunities}
                  sub="casos visibilidad vs. markdown"
                />
                <StatTile
                  label="Catálogo analizado"
                  value={k.products}
                  sub={`${num(k.nike_products)} Nike · ${num(k.brands)} marcas · ${num(k.retailers)} retailers`}
                />
                <StatTile
                  label="Insights de consumidor"
                  value={k.brand_insights}
                  sub="Argentina, con evidencia"
                />
              </div>

              {nothingAtAll ? (
                <Card>
                  <EmptyState
                    title="Pipeline vacío"
                    description="Todas las tablas de inteligencia están sin datos. La API responde 200, así que la UI está sana: falta correr el pipeline (o terminar las etapas que aún no existen)."
                    hint={PIPELINE_HINT}
                  />
                </Card>
              ) : null}

              {/* ── Riesgos y oportunidades ──────────────────────── */}
              <div className="grid gap-4 xl:grid-cols-2">
                <Card>
                  <SectionHeader
                    eyebrow="Does it matter?"
                    title="Riesgos principales"
                    hint="Precio, amenaza competitiva y distribución, ordenados por importancia comercial."
                    right={
                      <Link href="/opportunities" className="text-2xs font-semibold text-nike-red">
                        Ver todos →
                      </Link>
                    }
                  />
                  <RiskList items={data.top_risks} />
                </Card>

                <Card>
                  <SectionHeader
                    eyebrow="What should we do?"
                    title="Oportunidades principales"
                    hint="Lo que más mueve la aguja comercial ahora mismo."
                  />
                  <RiskList items={data.top_opportunities} />
                </Card>
              </div>

              {/* ── Match destacado ──────────────────────────────── */}
              <Card>
                <SectionHeader
                  eyebrow="Who is competing?"
                  title="Matches competitivos más fuertes"
                  hint="Qué productos compiten realmente entre sí, según los 7 factores del motor."
                  right={
                    <Link href="/matches" className="text-2xs font-semibold text-nike-red">
                      Explorar matches →
                    </Link>
                  }
                />
                <MatchStrip items={data.top_matches} />
              </Card>

              {/* ── Momentum + retail media ──────────────────────── */}
              <div className="grid gap-4 xl:grid-cols-2">
                <Card>
                  <SectionHeader
                    eyebrow="What is happening?"
                    title="Momentum de competidores"
                    hint="Señales de mercado con mayor aceleración en el período."
                  />
                  <MomentumList items={data.competitor_momentum} />
                </Card>

                <Card>
                  <SectionHeader
                    eyebrow="What should we do?"
                    title="Oportunidades de retail media"
                    hint="Casos donde conviene reasignar inversión de markdown a visibilidad."
                    right={
                      <Link href="/retail-media" className="text-2xs font-semibold text-nike-red">
                        Ver todas →
                      </Link>
                    }
                  />
                  <RetailMediaStrip items={data.retail_media} />
                </Card>
              </div>

              {/* ── Assortment gaps + brand ──────────────────────── */}
              <div className="grid gap-4 xl:grid-cols-2">
                <Card>
                  <SectionHeader
                    eyebrow="Does it matter?"
                    title="Gaps de surtido"
                    hint="Segmentos donde la competencia tiene profundidad y Nike no."
                  />
                  {data.assortment_gaps.length === 0 ? (
                    <EmptyState
                      title="Sin gaps de surtido detectados"
                      description="No hay oportunidades de la familia assortment entre las de mayor importancia. Puede ser buena noticia — o que la regla todavía no corrió."
                      icon="▤"
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
                    eyebrow="What is happening?"
                    title="Consumidor argentino — destacados"
                    hint="Insights con respaldo cuantitativo y evidencia pública agregada."
                    right={
                      <Link href="/brand" className="text-2xs font-semibold text-nike-red">
                        Ver panel completo →
                      </Link>
                    }
                  />
                  <BrandStrip items={data.brand_highlights} />
                </Card>
              </div>
            </div>
          );
        }}
      </AsyncSection>
    </div>
  );
}

// ── Sub-vistas ──────────────────────────────────────────────────────

function RiskList({ items }: { items: Opportunity[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin oportunidades calculadas"
        description="La etapa de opportunities no produjo resultados todavía. Cuando corra, esta lista se ordena sola por Business Importance."
        icon="◔"
      />
    );
  }
  return (
    <ul className="divide-y divide-line">
      {items.map((o) => {
        const sev = severityStyle(o.severity);
        return (
          <li key={o.id} className="py-3 first:pt-0 last:pb-0">
            <div className="flex items-start gap-3">
              <div className="w-12 shrink-0 text-center">
                <span className="tabular block text-base font-bold leading-none" style={{ color: sev.color }}>
                  {score(o.business_importance)}
                </span>
                <span className="text-2xs text-ink-muted">imp.</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <SeverityBadge severity={o.severity} />
                  <ConfidenceBadge confidence={o.confidence} />
                </div>
                <p className="text-xs font-semibold leading-snug text-ink-primary">{o.title}</p>
                {o.recommendation?.action ? (
                  <p className="mt-1 text-2xs leading-relaxed text-ink-secondary">
                    <span className="font-semibold text-nike-red">Acción: </span>
                    {o.recommendation.action.replace(/_/g, " ").toLowerCase()}
                  </p>
                ) : null}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function MatchStrip({ items }: { items: MatchListItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin matches competitivos"
        description="La tabla competitive_matches está vacía. El motor de matching necesita productos enriquecidos para poder comparar."
        icon="⇄"
      />
    );
  }
  return (
    <ul className="grid gap-2 lg:grid-cols-2">
      {items.map((m) => (
        <li key={m.id}>
          <Link
            href={`/matches/${m.id}`}
            className="block rounded-md border border-line px-3 py-2.5 transition hover:border-line-strong hover:bg-surface-sunken"
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
  );
}

function MomentumList({ items }: { items: MarketSignal[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin señales de mercado"
        description="La tabla market_signals está vacía. La etapa de brand intelligence es la que produce momentum de marcas y franquicias."
        icon="◠"
      />
    );
  }
  const maxValue = Math.max(...items.map((s) => Math.abs(s.value ?? 0)), 1);
  return (
    <ul className="space-y-2.5">
      {items.map((s) => {
        const accel = s.acceleration ?? 0;
        const up = accel >= 0;
        return (
          <li key={s.id} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-ink-primary">
                {entityLabel(s.entity_type, s.entity_id)}
              </p>
              <p className="text-2xs text-ink-muted">
                {signalTypeLabel(s.signal_type)} · {s.entity_type}
              </p>
              <div className="mt-1">
                <MeterBar value={Math.abs(s.value ?? 0)} max={maxValue} color="#2A78D6" height={5} />
              </div>
            </div>
            <div className="text-right">
              <p
                className="tabular text-sm font-bold"
                style={{ color: up ? "#0A6B0A" : "#8E2020" }}
              >
                <span aria-hidden>{up ? "▲" : "▼"}</span> {signed(accel, 2)}
              </p>
              <p className="text-2xs text-ink-muted">aceleración</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function RetailMediaStrip({ items }: { items: RetailMedia[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin oportunidades de retail media"
        description="La etapa retail_media no generó casos. Necesita precios, stock y matches para poder decidir entre visibilidad y descuento."
        icon="◫"
      />
    );
  }
  return (
    <ul className="space-y-2">
      {items.map((rm) => {
        const rec = recommendationStyle(rm.recommendation);
        return (
          <li
            key={rm.id}
            className="rounded-md border border-line px-3 py-2.5"
            style={{ borderLeft: `3px solid ${rec.color}` }}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-bold text-ink-primary">{rec.label}</p>
                <p className="mt-0.5 truncate text-2xs text-ink-secondary">
                  {text(rm.nike_product?.product_name)}
                  {rm.retailer ? ` · ${rm.retailer.name}` : ""}
                </p>
              </div>
              <span className="tabular shrink-0 text-base font-bold" style={{ color: rec.color }}>
                {score(rm.score)}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function BrandStrip({ items }: { items: BrandInsight[] }) {
  const withEvidence = items.filter(hasEvidence);
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin insights de consumidor"
        description="La tabla brand_insights está vacía. Cada insight requiere volumen de señal y evidencia — sin respaldo, no se genera."
        icon="◍"
      />
    );
  }
  if (withEvidence.length === 0) {
    return (
      <EmptyState
        title="Insights sin evidencia adjunta"
        description={`Hay ${items.length} insight(s) pero ninguno trae evidencia. Por regla del producto no se muestran insights sin respaldo.`}
        icon="◍"
      />
    );
  }
  return (
    <ul className="space-y-2.5">
      {withEvidence.slice(0, 5).map((i) => (
        <li key={i.id} className="border-l-2 border-line pl-3">
          <div className="flex items-center gap-2">
            <span className="text-2xs font-semibold uppercase tracking-wide text-ink-muted">
              {text(i.brand)} · {text(i.topic)}
            </span>
            <ConfidenceBadge confidence={i.confidence} />
          </div>
          <p className="mt-0.5 text-xs leading-snug text-ink-primary">{text(i.insight_text)}</p>
          <p className="tabular mt-1 text-2xs text-ink-muted">
            {num(i.signal_volume)} señales · tendencia {pct(i.trend, 0)} · sentimiento{" "}
            {dec(i.sentiment, 2)} · {evidenceOf(i).length} evidencia(s)
          </p>
        </li>
      ))}
    </ul>
  );
}
