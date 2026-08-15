/** Formateo consistente de números, precios y fechas en toda la app. */

const NUMBER_AR = new Intl.NumberFormat("es-AR");
const DECIMAL_AR = new Intl.NumberFormat("es-AR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return NUMBER_AR.format(value);
}

export function dec(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (digits === 1) return DECIMAL_AR.format(value);
  return value.toFixed(digits);
}

/** Score 0..100 -> "87.4" */
export function score(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return DECIMAL_AR.format(value);
}

/** Fracción 0..1 -> "72%" */
export function pctFromFraction(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** Valor ya en 0..100 -> "72%" */
export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function money(value: number | null | undefined, currency = "ARS"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  try {
    return new Intl.NumberFormat("es-AR", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${NUMBER_AR.format(Math.round(value))}`;
  }
}

export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}`;
}

export function date(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("es-AR", { day: "2-digit", month: "short", year: "numeric" });
}

export function period(start: string | null | undefined, end: string | null | undefined): string {
  if (!start && !end) return "—";
  return `${date(start)} → ${date(end)}`;
}

export function truncate(value: string | null | undefined, max = 140): string {
  if (!value) return "";
  return value.length <= max ? value : `${value.slice(0, max - 1).trimEnd()}…`;
}

/** "—" seguro para cualquier texto vacío. */
export function text(value: string | null | undefined): string {
  return value && value.trim() !== "" ? value : "—";
}
