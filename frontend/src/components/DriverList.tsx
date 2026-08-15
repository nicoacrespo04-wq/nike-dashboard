"use client";

import type { DriversPayload } from "@/types";
import { dec, pct } from "@/lib/format";
import { normalizeDrivers } from "@/lib/drivers";
import { humanize } from "@/lib/viz";
import { MeterBar } from "@/components/ui";

/**
 * "¿Por qué?" de una oportunidad / recomendación: drivers ordenados por
 * contribución, con barra + etiqueta directa.
 *
 * Acepta el payload crudo del backend porque las dos formas (lista plana y
 * sobre con `factors` adentro) conviven en la API.
 */
export function DriverList({
  drivers,
  max = 6,
  color = "#2A78D6",
  emptyLabel = "El motor no registró drivers para este caso.",
}: {
  drivers: DriversPayload | null | undefined;
  max?: number;
  color?: string;
  emptyLabel?: string;
}) {
  const normalized = normalizeDrivers(drivers);

  if (normalized.length === 0) {
    return <p className="text-2xs italic text-ink-muted">{emptyLabel}</p>;
  }

  const ordered = [...normalized]
    .sort((a, b) => (b.contribution ?? 0) - (a.contribution ?? 0))
    .slice(0, max);
  const maxContribution = Math.max(...ordered.map((d) => d.contribution ?? 0), 1);

  return (
    <ul className="space-y-1.5">
      {ordered.map((driver, i) => (
        <li
          key={`${driver.name}-${i}`}
          className="grid grid-cols-[minmax(110px,1.3fr)_minmax(60px,1fr)_auto] items-center gap-2"
        >
          <span className="truncate text-2xs text-ink-secondary" title={humanize(driver.name)}>
            {humanize(driver.name)}
          </span>
          <MeterBar value={driver.contribution} max={maxContribution} color={color} height={6} />
          <span className="tabular w-16 text-right text-2xs font-semibold text-ink-primary">
            {driver.contribution !== null && driver.contribution !== undefined
              ? pct(driver.contribution, 0)
              : dec(driver.value)}
          </span>
        </li>
      ))}
    </ul>
  );
}
