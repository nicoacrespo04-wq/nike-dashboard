"use client";

import Link from "next/link";
import type { Opportunity } from "@/types";
import { score, text } from "@/lib/format";
import { humanize, severityStyle } from "@/lib/viz";
import { ConfidenceBadge, FamilyBadge, SeverityBadge } from "@/components/badges";
import { DriverList } from "@/components/DriverList";
import { ProductLine } from "@/components/ProductLine";
import { MeterBar } from "@/components/ui";

/**
 * Una oportunidad = las 4 preguntas en una tarjeta:
 *   QUÉ PASA (título+descripción) · CUÁNTO IMPORTA (business importance)
 *   POR QUÉ (drivers)             · QUÉ HACER (recomendación)
 */
export function OpportunityCard({
  opportunity,
  compact = false,
}: {
  opportunity: Opportunity;
  compact?: boolean;
}) {
  const sev = severityStyle(opportunity.severity);
  const rec = opportunity.recommendation;

  return (
    <article
      className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface-card shadow-card"
      style={{ borderTop: `3px solid ${sev.color}` }}
    >
      {/* QUÉ PASA */}
      <div className="p-4">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <SeverityBadge severity={opportunity.severity} />
          <FamilyBadge family={opportunity.family} />
          <ConfidenceBadge confidence={opportunity.confidence} />
        </div>

        <h3 className="text-sm font-bold leading-snug text-ink-primary">{opportunity.title}</h3>
        <p className="mt-1 text-2xs uppercase tracking-wide text-ink-muted">
          {humanize(opportunity.opportunity_type)}
          {opportunity.retailer ? ` · ${opportunity.retailer.name}` : ""}
          {opportunity.country_code ? ` · ${opportunity.country_code}` : ""}
        </p>
        {opportunity.description ? (
          <p className="mt-2 text-xs leading-relaxed text-ink-secondary">{opportunity.description}</p>
        ) : null}
      </div>

      {/* CUÁNTO IMPORTA */}
      <div className="border-t border-line bg-surface-sunken px-4 py-3">
        <div className="flex items-baseline justify-between">
          <span className="text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">
            Business Importance
          </span>
          <span className="tabular text-lg font-bold leading-none" style={{ color: sev.color }}>
            {score(opportunity.business_importance)}
            <span className="text-2xs font-medium text-ink-muted"> /100</span>
          </span>
        </div>
        <div className="mt-1.5">
          <MeterBar value={opportunity.business_importance} max={100} color={sev.color} height={6} />
        </div>
      </div>

      {!compact ? (
        <>
          {/* Sujetos */}
          {opportunity.nike_product || opportunity.competitor_product ? (
            <div className="grid gap-3 border-t border-line px-4 py-3 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                  Nike
                </p>
                <ProductLine product={opportunity.nike_product} role="nike" compact />
              </div>
              <div>
                <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                  Competidor
                </p>
                <ProductLine product={opportunity.competitor_product} role="competitor" compact />
              </div>
            </div>
          ) : null}

          {/* POR QUÉ */}
          <div className="border-t border-line px-4 py-3">
            <p className="mb-2 text-2xs font-semibold uppercase tracking-[0.1em] text-ink-muted">
              Por qué — drivers del score
            </p>
            <DriverList drivers={opportunity.drivers} color={sev.color} />
          </div>
        </>
      ) : null}

      {/* QUÉ HACER */}
      <div className="mt-auto border-t-2 border-nike-ink/10 bg-[#FAFAF8] px-4 py-3">
        <p className="mb-1 text-2xs font-semibold uppercase tracking-[0.1em] text-nike-red">
          Acción recomendada
        </p>
        {rec && rec.action ? (
          <>
            <p className="text-xs font-bold leading-snug text-ink-primary">{humanize(rec.action)}</p>
            {rec.rationale ? (
              <p className="mt-1 text-2xs leading-relaxed text-ink-secondary">{rec.rationale}</p>
            ) : null}
          </>
        ) : (
          <p className="text-2xs italic text-ink-muted">
            El motor todavía no generó una recomendación asociada.
          </p>
        )}
      </div>

      {opportunity.nike_product ? (
        <Link
          href={`/matches?product=${opportunity.nike_product.id}`}
          className="border-t border-line px-4 py-2 text-2xs font-semibold text-nike-red transition hover:bg-surface-sunken"
        >
          Ver competidores de {text(opportunity.nike_product.product_name)} →
        </Link>
      ) : null}
    </article>
  );
}
