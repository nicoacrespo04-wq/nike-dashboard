"use client";

import { useMemo, useState } from "react";
import type { RetailMedia } from "@/types";
import { getRetailMedia, type RetailMediaQuery } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { driverMetrics, driverValue, driversRationale, normalizeDrivers } from "@/lib/drivers";
import { dec, money, num, pct, score } from "@/lib/format";
import { recommendationStyle } from "@/lib/viz";
import { ConfidenceBadge } from "@/components/badges";
import { DriverList } from "@/components/DriverList";
import { ProductLine } from "@/components/ProductLine";
import {
  AsyncSection,
  Card,
  EmptyState,
  MeterBar,
  PageHeader,
} from "@/components/ui";

const PAGE_SIZE = 25;

export default function RetailMediaPage() {
  const [recommendation, setRecommendation] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [page, setPage] = useState(0);

  const query: RetailMediaQuery = useMemo(
    () => ({
      recommendation: recommendation || undefined,
      min_score: minScore || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [recommendation, minScore, page],
  );

  const state = useApi((signal) => getRetailMedia(query, signal), [JSON.stringify(query)]);

  return (
    <div>
      <PageHeader
        question="What should we do?"
        title="Retail Media Opportunities"
        description="El caso central del producto: en vez de financiar otro markdown, reasignar esa inversión a visibilidad. Cada caso combina salud de stock, competitividad de precio, relevancia competitiva y momentum del competidor para decidir dónde pauta el peso."
      />

      <AsyncSection
        state={state}
        loadingRows={5}
        empty={
          <Card>
            <EmptyState
              title="Sin oportunidades de retail media"
              description="La tabla retail_media_opportunities está vacía. Esta etapa necesita precios, stock y matches competitivos para poder decidir entre visibilidad y descuento."
              hint="cd backend && python -m app.pipeline"
            />
          </Card>
        }
      >
        {(data) => {
          const facets = data.facets?.by_recommendation ?? [];
          const thresholds = data.thresholds ?? {};

          if (data.total === 0 && !recommendation && minScore === 0) {
            return (
              <Card>
                <EmptyState
                  title="Sin oportunidades de retail media"
                  description="El motor no generó casos. Cuando corra, verás producto, retailer, competidor, stock, gap de precio y la acción recomendada."
                  hint="cd backend && python -m app.pipeline"
                />
              </Card>
            );
          }

          return (
            <div className="space-y-4">
              {/* Distribución de recomendaciones */}
              {facets.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {facets.map((f) => {
                    const rec = recommendationStyle(f.recommendation);
                    const active = recommendation === f.recommendation;
                    return (
                      <button
                        key={f.recommendation ?? "none"}
                        type="button"
                        aria-pressed={active}
                        onClick={() => {
                          setRecommendation(active ? "" : (f.recommendation ?? ""));
                          setPage(0);
                        }}
                        className="rounded-lg border bg-surface-card px-4 py-3 text-left shadow-card transition hover:shadow-pop"
                        style={{
                          borderColor: active ? rec.color : "#E3E3DF",
                          borderLeftWidth: 4,
                          borderLeftColor: rec.color,
                        }}
                      >
                        <p className="text-xs font-bold leading-snug text-ink-primary">{rec.label}</p>
                        <div className="mt-1.5 flex items-baseline gap-2">
                          <span className="tabular text-xl font-bold" style={{ color: rec.color }}>
                            {num(f.n)}
                          </span>
                          <span className="tabular text-2xs text-ink-muted">
                            caso(s) · score prom. {dec(f.avg_score)}
                          </span>
                        </div>
                        <p className="mt-1.5 text-2xs leading-relaxed text-ink-secondary">{rec.blurb}</p>
                      </button>
                    );
                  })}
                </div>
              ) : null}

              {/* Filtros + umbrales */}
              <Card>
                <div className="flex flex-wrap items-end gap-5">
                  <label className="block">
                    <span className="mb-1 block text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                      Score mínimo: <span className="tabular font-bold">{minScore}</span>
                    </span>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={minScore}
                      onChange={(e) => {
                        setMinScore(Number(e.target.value));
                        setPage(0);
                      }}
                      className="w-48 accent-nike-red"
                    />
                  </label>

                  <div className="flex items-center gap-3">
                    <span className="text-xs text-ink-secondary">
                      <span className="tabular font-bold text-ink-primary">{num(data.total)}</span>{" "}
                      caso(s)
                    </span>
                    {recommendation || minScore > 0 ? (
                      <button
                        type="button"
                        onClick={() => {
                          setRecommendation("");
                          setMinScore(0);
                          setPage(0);
                        }}
                        className="text-2xs font-semibold text-nike-red hover:underline"
                      >
                        Limpiar filtros
                      </button>
                    ) : null}
                  </div>

                  {Object.keys(thresholds).length > 0 ? (
                    <div className="ml-auto max-w-lg">
                      <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                        Umbrales vigentes (config/weights.yaml)
                      </p>
                      <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-ink-secondary">
                        {Object.entries(thresholds).map(([k, v]) => (
                          <span key={k} className="tabular">
                            <span className="font-mono text-ink-muted">{k}</span>{" "}
                            <span className="font-semibold text-ink-primary">{v}</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              </Card>

              {data.items.length === 0 ? (
                <Card>
                  <EmptyState
                    title="Ningún caso coincide con el filtro"
                    description="Bajá el score mínimo o quitá el filtro de recomendación."
                    icon="◫"
                  />
                </Card>
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center justify-between px-1">
                    <p className="text-xs text-ink-secondary">
                      Mostrando {(data.offset ?? 0) + 1}–{(data.offset ?? 0) + data.items.length} de{" "}
                      <span className="tabular font-bold text-ink-primary">{num(data.total)}</span>,
                      ordenados por opportunity score
                    </p>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={page === 0}
                        onClick={() => setPage((prev) => Math.max(0, prev - 1))}
                        className="rounded border border-line px-3 py-1 text-2xs font-semibold text-ink-secondary disabled:opacity-40"
                      >
                        ← Anterior
                      </button>
                      <button
                        type="button"
                        disabled={(data.offset ?? 0) + data.items.length >= data.total}
                        onClick={() => setPage((prev) => prev + 1)}
                        className="rounded border border-line px-3 py-1 text-2xs font-semibold text-ink-secondary disabled:opacity-40"
                      >
                        Siguiente →
                      </button>
                    </div>
                  </div>
                  {data.items.map((item) => (
                    <RetailMediaRow key={item.id} item={item} />
                  ))}
                </div>
              )}
            </div>
          );
        }}
      </AsyncSection>
    </div>
  );
}

function RetailMediaRow({ item }: { item: RetailMedia }) {
  const rec = recommendationStyle(item.recommendation);

  const drivers = normalizeDrivers(item.drivers);
  const metrics = driverMetrics(item.drivers);
  const rationale = driversRationale(item.drivers);

  const stockHealth = driverValue(drivers, "nike_stock_health");
  const priceCompetitiveness = driverValue(drivers, "price_competitiveness");
  const competitiveRelevance = driverValue(drivers, "competitive_relevance");
  const competitorStockGap = driverValue(drivers, "competitor_stock_gap");

  return (
    <article
      className="overflow-hidden rounded-lg border border-line bg-surface-card shadow-card"
      style={{ borderLeft: `4px solid ${rec.color}` }}
    >
      <div className="grid gap-4 p-4 xl:grid-cols-[1.5fr_1fr_1fr]">
        {/* Sujetos */}
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="rounded px-2 py-0.5 text-2xs font-bold uppercase tracking-wide"
              style={{ backgroundColor: rec.bg, color: rec.text, border: `1px solid ${rec.border}` }}
            >
              {rec.label}
            </span>
            <ConfidenceBadge confidence={item.confidence} />
            {item.retailer ? (
              <span className="text-2xs font-semibold text-ink-secondary">
                {item.retailer.name}
                {item.retailer.channel ? ` · ${item.retailer.channel}` : ""}
              </span>
            ) : null}
          </div>

          <ProductLine product={item.nike_product} role="nike" />
          <div className="flex items-center gap-2 pl-1">
            <span aria-hidden className="text-2xs font-bold text-ink-muted">
              compite con
            </span>
            <span className="h-px flex-1 bg-line" />
          </div>
          <ProductLine product={item.competitor_product} role="competitor" />

          <p className="text-2xs leading-relaxed text-ink-secondary">{rationale ?? rec.blurb}</p>
        </div>

        {/* Señales operativas */}
        <div className="space-y-2.5 border-t border-line pt-3 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0">
          <p className="text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">
            Señales del caso
          </p>
          <Signal
            label="Salud de stock Nike"
            value={stockHealth}
            hint="Disponibilidad de talles: pautar sin stock es tirar plata."
            good
          />
          <Signal
            label="Competitividad de precio"
            value={priceCompetitiveness}
            hint="Qué tan cerca está Nike del precio del competidor."
            good
          />
          <Signal
            label="Relevancia competitiva"
            value={competitiveRelevance}
            hint="Match score con el competidor del caso."
            good
          />
          <Signal
            label="Quiebre del competidor"
            value={competitorStockGap}
            hint="Cuánto stock le falta al competidor: ventana para capturar demanda."
            good
          />
          <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-line pt-2">
            <Metric label="Stock Nike" value={metrics["nike_stock_pct"]} suffix="%" />
            <Metric label="Stock competidor" value={metrics["competitor_stock_pct"]} suffix="%" />
            <Metric label="Gap de precio" value={metrics["price_gap_pct"]} suffix="%" signedValue />
            <Metric label="Descuento Nike" value={metrics["nike_discount_pct"]} suffix="%" />
            <Metric
              label="Share of shelf Nike"
              value={
                metrics["nike_shelf_share"] !== undefined ? metrics["nike_shelf_share"] * 100 : undefined
              }
              suffix="%"
            />
            <Metric label="Business importance" value={metrics["business_importance"]} />
          </dl>
          {item.nike_product?.msrp !== null && item.nike_product?.msrp !== undefined ? (
            <p className="tabular text-2xs text-ink-muted">
              MSRP Nike {money(item.nike_product.msrp)}
              {item.competitor_product?.msrp !== null && item.competitor_product?.msrp !== undefined
                ? ` · competidor ${money(item.competitor_product.msrp)}`
                : ""}
            </p>
          ) : null}
        </div>

        {/* Score + drivers */}
        <div className="border-t border-line pt-3 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">
              Opportunity score
            </span>
            <span className="tabular text-2xl font-bold leading-none" style={{ color: rec.color }}>
              {score(item.score)}
            </span>
          </div>
          <MeterBar value={item.score} max={100} color={rec.color} height={8} />

          <p className="mb-1.5 mt-3 text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">
            Por qué — drivers
          </p>
          <DriverList drivers={item.drivers} color={rec.color} max={7} />
        </div>
      </div>
    </article>
  );
}

function Signal({
  label,
  value,
  hint,
  good = true,
}: {
  label: string;
  value: number | null;
  hint: string;
  good?: boolean;
}) {
  if (value === null) {
    return (
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-2xs text-ink-secondary">{label}</span>
          <span className="text-2xs italic text-ink-muted">sin dato</span>
        </div>
        <div className="hatch-muted mt-0.5 h-1.5 w-full rounded-sm" title={hint} />
      </div>
    );
  }
  // Los drivers vienen normalizados 0..1 desde el motor.
  const normalized = value <= 1 ? value * 100 : value;
  const color = good
    ? normalized >= 70
      ? "#0CA30C"
      : normalized >= 40
        ? "#FAB219"
        : "#D03B3B"
    : "#2A78D6";
  return (
    <div title={hint}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-2xs text-ink-secondary">{label}</span>
        <span className="tabular text-2xs font-bold text-ink-primary">{pct(normalized, 0)}</span>
      </div>
      <div className="mt-0.5">
        <MeterBar value={normalized} max={100} color={color} height={6} />
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  suffix = "",
  signedValue = false,
}: {
  label: string;
  value: number | undefined;
  suffix?: string;
  signedValue?: boolean;
}) {
  const has = value !== undefined && Number.isFinite(value);
  const sign = signedValue && has && value > 0 ? "+" : "";
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className="tabular text-2xs font-semibold text-ink-primary">
        {has ? `${sign}${value.toFixed(1)}${suffix}` : "sin dato"}
      </dd>
    </div>
  );
}
