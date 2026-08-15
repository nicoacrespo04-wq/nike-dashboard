"use client";

import { Pill } from "@/components/ui";
import { confidenceStyle, familyLabel, severityStyle } from "@/lib/viz";

/** Severidad: color + ícono + texto. El color nunca carga el significado solo. */
export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  const style = severityStyle(severity);
  const icon =
    severity?.toUpperCase() === "CRITICAL"
      ? "▲"
      : severity?.toUpperCase() === "HIGH"
        ? "▲"
        : severity?.toUpperCase() === "MEDIUM"
          ? "◆"
          : "●";
  return (
    <Pill color={style.text} bg={style.bg} border={style.border} title={`Severidad ${style.label}`}>
      <span aria-hidden style={{ color: style.color }}>
        {icon}
      </span>
      {style.label}
    </Pill>
  );
}

/** Confianza: puntos llenos/vacíos + texto. */
export function ConfidenceBadge({
  confidence,
  coverage,
}: {
  confidence: string | null | undefined;
  coverage?: number | null;
}) {
  const style = confidenceStyle(confidence);
  const title =
    coverage !== null && coverage !== undefined
      ? `${style.label} · cobertura ${(coverage * 100).toFixed(0)}% del peso total`
      : style.label;
  return (
    <Pill color={style.text} bg={style.bg} border={style.border} title={title}>
      <span aria-hidden className="flex gap-0.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: i < style.dots ? style.text : "transparent", border: `1px solid ${style.text}` }}
          />
        ))}
      </span>
      {style.label}
    </Pill>
  );
}

export function FamilyBadge({ family }: { family: string | null | undefined }) {
  return <Pill title="Familia de oportunidad">{familyLabel(family)}</Pill>;
}

export function LifecycleBadge({ stage }: { stage: string | null | undefined }) {
  if (!stage) return null;
  const labels: Record<string, string> = {
    launch: "Lanzamiento",
    growth: "Crecimiento",
    mature: "Madurez",
    decline: "Declive",
    clearance: "Liquidación",
  };
  return <Pill title="Etapa del ciclo de vida">{labels[stage] ?? stage}</Pill>;
}
