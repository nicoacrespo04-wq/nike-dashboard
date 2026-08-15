/**
 * Normalización de la evidencia de un brand insight.
 *
 * El backend la emite en tres formas distintas según el endpoint y el motor:
 *   1. Lista de ejemplos:      `[{source, excerpt, …}]`
 *   2. Sobre con contexto:     `{insight_type, sources:[…], examples:[{…}]}`
 *   3. El JSON crudo sin parsear (así lo devuelve `/api/overview`, que hace
 *      SELECT directo sin `from_json`).
 *
 * La UI necesita una sola forma, y la regla del producto es dura: un insight
 * sin evidencia no se muestra. Por eso conviene ser tolerante al leer, pero
 * estricto al decidir si hay respaldo.
 */

import type { BrandInsight, EvidenceItem } from "@/types/intelligence";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function itemsFrom(value: unknown): EvidenceItem[] {
  if (Array.isArray(value)) return value.filter(isRecord);
  if (!isRecord(value)) return [];

  // Forma 2: sobre con los ejemplos adentro.
  const examples = value["examples"];
  if (Array.isArray(examples)) {
    const parsed = examples.filter(isRecord);
    if (parsed.length > 0) return parsed;
  }

  // Sólo hay lista de fuentes: cada fuente vale como evidencia mínima.
  const sources = value["sources"];
  if (Array.isArray(sources)) {
    const named = sources.filter((s): s is string => typeof s === "string" && s.trim() !== "");
    if (named.length > 0) return named.map((source) => ({ source }));
  }

  // Un único ejemplo suelto.
  return value["text"] || value["excerpt"] || value["quote"] ? [value] : [];
}

export function evidenceOf(insight: BrandInsight): EvidenceItem[] {
  const raw = insight.evidence;
  if (typeof raw === "string") {
    if (raw.trim() === "") return [];
    try {
      return itemsFrom(JSON.parse(raw) as unknown);
    } catch {
      return [];
    }
  }
  return itemsFrom(raw);
}

export function hasEvidence(insight: BrandInsight): boolean {
  return evidenceOf(insight).length > 0;
}

/** Texto legible de un ítem de evidencia, sea cual sea su forma. */
export function evidenceText(item: EvidenceItem): string {
  for (const key of ["text", "excerpt", "quote"] as const) {
    const candidate = item[key];
    if (typeof candidate === "string" && candidate.trim() !== "") return candidate;
  }
  const entries = Object.entries(item)
    .filter(([key]) => !["url", "source", "source_name", "period_start", "period_end"].includes(key))
    .map(([key, value]) => `${key}: ${String(value)}`);
  return entries.join(" · ") || "Evidencia sin texto";
}

/** Fuente + fecha del ítem, para que el respaldo sea rastreable. */
export function evidenceSource(item: EvidenceItem): string {
  const name = [item["source_name"], item["source"]].find(
    (v): v is string => typeof v === "string" && v.trim() !== "",
  );
  const when = [item["observed_at"], item["date"], item["period_end"]].find(
    (v): v is string => typeof v === "string" && v.trim() !== "",
  );
  return [name, when].filter(Boolean).join(" · ") || "fuente agregada";
}
