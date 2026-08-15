/**
 * Vocabulario visual del motor de decisión.
 *
 * Un color = un significado, siempre el mismo:
 *  - Severidad  -> paleta de estado (nunca reutilizada para series).
 *  - Factores   -> paleta categórica de orden FIJO, asignada por identidad del
 *                  factor (no por ranking), validada para daltonismo.
 *  - Rojo Nike  -> marca y foco, nunca dato.
 */

import type { Confidence, Severity } from "@/types";

// ── Severidad (paleta de estado) ────────────────────────────────────
export const SEVERITY_ORDER: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

interface SeverityStyle {
  label: string;
  color: string;
  bg: string;
  border: string;
  text: string;
}

const SEVERITY_STYLES: Record<Severity, SeverityStyle> = {
  CRITICAL: { label: "CRÍTICA", color: "#D03B3B", bg: "#FBECEC", border: "#F0C4C4", text: "#8E2020" },
  HIGH: { label: "ALTA", color: "#EC835A", bg: "#FDF0E9", border: "#F7D4C1", text: "#94431F" },
  MEDIUM: { label: "MEDIA", color: "#FAB219", bg: "#FEF6E4", border: "#F6E0AE", text: "#7A5600" },
  LOW: { label: "BAJA", color: "#8A8A83", bg: "#F2F2EF", border: "#DEDED9", text: "#55554F" },
};

const SEVERITY_FALLBACK: SeverityStyle = {
  label: "SIN CLASIFICAR",
  color: "#8A8A83",
  bg: "#F2F2EF",
  border: "#DEDED9",
  text: "#55554F",
};

export function severityStyle(severity: string | null | undefined): SeverityStyle {
  if (!severity) return SEVERITY_FALLBACK;
  const key = severity.toUpperCase() as Severity;
  return SEVERITY_STYLES[key] ?? SEVERITY_FALLBACK;
}

export function severityRank(severity: string | null | undefined): number {
  if (!severity) return 99;
  const idx = SEVERITY_ORDER.indexOf(severity.toUpperCase() as Severity);
  return idx === -1 ? 99 : idx;
}

// ── Confianza ───────────────────────────────────────────────────────
interface ConfidenceStyle {
  label: string;
  bg: string;
  text: string;
  border: string;
  dots: number;
}

const CONFIDENCE_STYLES: Record<Confidence, ConfidenceStyle> = {
  HIGH: { label: "Confianza alta", bg: "#EAF6EA", text: "#0A6B0A", border: "#C3E4C3", dots: 3 },
  MEDIUM: { label: "Confianza media", bg: "#FEF6E4", text: "#7A5600", border: "#F6E0AE", dots: 2 },
  LOW: { label: "Confianza baja", bg: "#F2F2EF", text: "#55554F", border: "#DEDED9", dots: 1 },
};

const CONFIDENCE_FALLBACK: ConfidenceStyle = {
  label: "Confianza s/d",
  bg: "#F2F2EF",
  text: "#8A8A83",
  border: "#DEDED9",
  dots: 0,
};

export function confidenceStyle(confidence: string | null | undefined): ConfidenceStyle {
  if (!confidence) return CONFIDENCE_FALLBACK;
  const key = confidence.toUpperCase() as Confidence;
  return CONFIDENCE_STYLES[key] ?? CONFIDENCE_FALLBACK;
}

// ── Factores del match competitivo ──────────────────────────────────
/**
 * Orden FIJO de la paleta categórica. El color acompaña al factor, no a su
 * posición en el ranking: si un filtro cambia el orden, los colores no se mueven.
 * Validado (ΔE CVD adyacente >= 8, visión normal >= 15) sobre fondo claro.
 */
export const FACTOR_ORDER = [
  "semantic",
  "visual",
  "price",
  "retailer_overlap",
  "editorial",
  "social",
  "reviews",
] as const;

const FACTOR_PALETTE: Record<string, string> = {
  semantic: "#2A78D6", // azul
  visual: "#EB6834", // naranja
  price: "#1BAF7A", // aqua
  retailer_overlap: "#EDA100", // amarillo
  editorial: "#E87BA4", // magenta
  social: "#008300", // verde
  reviews: "#4A3AA7", // violeta
};

export const UNAVAILABLE_COLOR = "#B8B8B1";

export function factorColor(factor: string): string {
  return FACTOR_PALETTE[factor] ?? "#8A8A83";
}

const FACTOR_LABELS: Record<string, string> = {
  semantic: "Semántico",
  visual: "Visual",
  price: "Precio",
  retailer_overlap: "Solape de retailers",
  editorial: "Editorial",
  social: "Social",
  reviews: "Reviews",
};

export function factorLabel(factor: string): string {
  return FACTOR_LABELS[factor] ?? humanize(factor);
}

const FACTOR_HELP: Record<string, string> = {
  semantic: "Cercanía de taxonomía y descripción: use case, categoría, deporte, género y similitud de texto.",
  visual: "Parecido de la imagen y de los atributos visuales: silueta, colores y materiales.",
  price: "Cercanía de precio: MSRP, precio vigente y banda de precio.",
  retailer_overlap: "En cuántos retailers conviven ambos productos compitiendo por el mismo shopper.",
  editorial: "Menciones en medios y guías de compra que los enfrentan o los listan juntos.",
  social: "Co-menciones en conversación pública agregada del período.",
  reviews: "Atributos valorados en reviews y cercanía de rating.",
};

export function factorHelp(factor: string): string {
  return FACTOR_HELP[factor] ?? "Factor del score competitivo.";
}

/** Ordena factores: disponibles por contribución desc, luego los sin datos. */
export function sortFactors<T extends { available: boolean; contribution: number | null }>(
  factors: T[],
): T[] {
  return [...factors].sort((a, b) => {
    if (a.available !== b.available) return a.available ? -1 : 1;
    return (b.contribution ?? 0) - (a.contribution ?? 0);
  });
}

// ── Familias de oportunidad ─────────────────────────────────────────
const FAMILY_LABELS: Record<string, string> = {
  pricing: "Precio",
  assortment: "Surtido",
  distribution: "Distribución",
  stock: "Stock",
  retail_media: "Retail media",
  competitive_threat: "Amenaza competitiva",
  brand_momentum: "Momentum de marca",
};

export function familyLabel(family: string | null | undefined): string {
  if (!family) return "Sin familia";
  return FAMILY_LABELS[family] ?? humanize(family);
}

// ── Recomendaciones de retail media ─────────────────────────────────
interface RecommendationStyle {
  label: string;
  short: string;
  tone: "invest" | "hold" | "evaluate" | "capture";
  color: string;
  bg: string;
  border: string;
  text: string;
  blurb: string;
}

const RECOMMENDATION_STYLES: Record<string, RecommendationStyle> = {
  INVEST_IN_RETAIL_MEDIA: {
    label: "Invertir en retail media",
    short: "Invertir",
    tone: "invest",
    color: "#0CA30C",
    bg: "#EAF6EA",
    border: "#C3E4C3",
    text: "#0A6B0A",
    blurb: "Producto sano y competitivo en precio: la visibilidad convierte mejor que el descuento.",
  },
  PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN: {
    label: "Priorizar retail media sobre markdown",
    short: "Media > markdown",
    tone: "invest",
    color: "#0CA30C",
    bg: "#EAF6EA",
    border: "#C3E4C3",
    text: "#0A6B0A",
    blurb: "En vez de financiar otro markdown, reasignar esa inversión a visibilidad.",
  },
  CAPTURE_COMPETITOR_STOCKOUT: {
    label: "Capturar quiebre del competidor",
    short: "Capturar quiebre",
    tone: "capture",
    color: "#2A78D6",
    bg: "#EAF1FB",
    border: "#C3D8F2",
    text: "#1A4E8F",
    blurb: "El competidor está quebrado y Nike disponible: ventana corta para capturar demanda.",
  },
  EVALUATE_PRICE_ACTION_BEFORE_MEDIA: {
    label: "Evaluar precio antes que media",
    short: "Precio primero",
    tone: "evaluate",
    color: "#FAB219",
    bg: "#FEF6E4",
    border: "#F6E0AE",
    text: "#7A5600",
    blurb: "Hay desventaja de precio: pautar sobre un precio no competitivo quema inversión.",
  },
  DO_NOT_INCREASE_MEDIA: {
    label: "No aumentar inversión en media",
    short: "No aumentar",
    tone: "hold",
    color: "#D03B3B",
    bg: "#FBECEC",
    border: "#F0C4C4",
    text: "#8E2020",
    blurb: "Sin stock, sin relevancia competitiva o sin retorno esperable: no sumar presupuesto.",
  },
};

const RECOMMENDATION_FALLBACK: RecommendationStyle = {
  label: "Sin recomendación",
  short: "Sin dato",
  tone: "hold",
  color: "#8A8A83",
  bg: "#F2F2EF",
  border: "#DEDED9",
  text: "#55554F",
  blurb: "El motor no produjo una acción para este caso.",
};

export function recommendationStyle(rec: string | null | undefined): RecommendationStyle {
  if (!rec) return RECOMMENDATION_FALLBACK;
  return RECOMMENDATION_STYLES[rec] ?? { ...RECOMMENDATION_FALLBACK, label: humanize(rec), short: humanize(rec) };
}

export const RETAIL_MEDIA_RECOMMENDATIONS = Object.keys(RECOMMENDATION_STYLES);

// ── Dimensiones de brand intelligence ───────────────────────────────
const DIMENSION_LABELS: Record<string, string> = {
  brand_perception: "Percepción de marca",
  product_perception: "Percepción de producto",
  price_perception: "Percepción de precio",
  availability: "Disponibilidad",
  cultural_relevance: "Relevancia cultural",
  consumer_intent: "Intención de compra",
};

export function dimensionLabel(dimension: string | null | undefined): string {
  if (!dimension) return "Sin dimensión";
  return DIMENSION_LABELS[dimension] ?? humanize(dimension);
}

const SIGNAL_TYPE_LABELS: Record<string, string> = {
  social_momentum: "Momentum social",
  editorial_momentum: "Momentum editorial",
  review_momentum: "Momentum de reviews",
  share_of_shelf: "Share of shelf",
  promo_intensity: "Intensidad promocional",
};

export function signalTypeLabel(signal: string | null | undefined): string {
  if (!signal) return "Señal";
  return SIGNAL_TYPE_LABELS[signal] ?? humanize(signal);
}

/**
 * Etiqueta de la entidad de un market_signal. El backend guarda `entity_id`
 * como texto libre: a veces es un nombre ("Samba"), a veces el id numérico de
 * la fila. Mostrar un "5" pelado no dice nada, así que se lo califica.
 */
export function entityLabel(entityType: string | null | undefined, entityId: string | null | undefined): string {
  const id = (entityId ?? "").trim();
  if (id === "") return humanize(entityType ?? "entidad");
  if (!/^\d+$/.test(id)) return id;
  const types: Record<string, string> = {
    brand: "Marca",
    product: "Producto",
    franchise: "Franquicia",
    retailer: "Retailer",
  };
  return `${types[entityType ?? ""] ?? humanize(entityType ?? "Entidad")} #${id}`;
}

// ── Utilidades de texto ─────────────────────────────────────────────
export function humanize(value: string): string {
  const clean = value.replace(/[_-]+/g, " ").trim();
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

/** Color del score 0..100 sobre una rampa secuencial de un solo tono. */
export function scoreTone(score: number | null | undefined): string {
  if (score === null || score === undefined) return "#C9C9C3";
  if (score >= 80) return "#0D366B";
  if (score >= 65) return "#256ABF";
  if (score >= 50) return "#3987E5";
  if (score >= 35) return "#86B6EF";
  return "#CDE2FB";
}
