/**
 * Formateo de los números del motor SEGÚN SU UNIDAD DECLARADA.
 *
 * El backend publica la unidad de cada número (`unit`, `value_unit`,
 * `delta_unit`) porque en una misma pantalla conviven magnitudes que no son
 * comparables. El caso que rompía la lectura: en la tabla de momentum, el delta
 * de `share_of_shelf` está en PUNTOS PORCENTUALES (-7,7 pp) y el de
 * `editorial_momentum` es un RATIO (0,62 = +62%). Mostrados como "-7,7" y
 * "0,62" en la misma columna, no hay forma de saber qué se está mirando.
 *
 * Vocabulario (ver `UNIT_LABELS` en `backend/app/api/routers/brand.py`):
 *   score_0_100 · score normalizado, NO un volumen
 *   score_0_1   · lo mismo en 0..1 (los drivers del motor viven acá)
 *   pct         · porcentaje 0..100
 *   pp          · puntos porcentuales (variación de un %)
 *   ratio       · ver abajo
 *   count       · conteo absoluto
 *
 * Sobre `ratio`, que tiene dos lecturas según dónde aparezca:
 *   - como MAGNITUD es una fracción 0..1 (`nike_shelf_share: 0.4` = 40% del
 *     surtido) → se muestra como porcentaje;
 *   - como VARIACIÓN es un cambio relativo (`delta: 0.62` = +62%) → para eso
 *     está `formatDelta`, que prefiere el `delta_pct` que ya calculó el backend.
 * Por eso hay dos funciones y no una: la unidad sola no alcanza, hace falta
 * saber si el número es un nivel o un cambio.
 */

import type { Unit } from "@/types/intelligence";
import { ND, dec, num, pctFromFraction, signed } from "@/lib/format";

/** Etiqueta corta de la unidad, para encabezados de columna. */
export const UNIT_LABELS: Record<string, string> = {
  score_0_100: "score 0..100",
  score_0_1: "score 0..1",
  pct: "%",
  pp: "puntos porcentuales",
  ratio: "ratio (0,62 = +62%)",
  count: "conteo absoluto",
  number: "conteo absoluto",
  "score_-1_1": "score -1..1",
};

export function unitLabel(unit: Unit | null | undefined): string | null {
  if (typeof unit !== "string" || unit === "") return null;
  return UNIT_LABELS[unit] ?? unit;
}

/**
 * Un NIVEL medido (stock 90%, share of shelf 0,4, momentum score 85,2).
 * Nunca antepone signo: un nivel no es una variación.
 */
export function formatMagnitude(
  value: number | null | undefined,
  unit: Unit | null | undefined,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return ND;

  switch (unit) {
    case "pct":
      return `${dec(value, 1)}%`;
    case "pp":
      return `${dec(value, 1)} pp`;
    case "ratio":
      // Magnitud: fracción 0..1 del total.
      return pctFromFraction(value, 1);
    case "score_0_1":
      return dec(value, 2);
    case "score_0_100":
      return dec(value, 1);
    case "score_-1_1":
      return dec(value, 2);
    case "count":
    case "number":
      return num(value);
    default:
      return dec(value, 2);
  }
}

/**
 * Una VARIACIÓN respecto del período anterior, con signo y unidad a la vista.
 *
 * `percent` es el `delta_pct` que ya publicó el backend para los deltas en
 * ratio: usarlo evita recalcular +62% desde 0,62 en la UI y evita que dos
 * pantallas lo redondeen distinto.
 */
export function formatDelta(
  value: number | null | undefined,
  unit: Unit | null | undefined,
  percent?: number | null,
): string {
  if (unit === "ratio") {
    if (percent !== null && percent !== undefined && Number.isFinite(percent)) {
      return `${signed(percent, 1)}%`;
    }
    if (value === null || value === undefined || !Number.isFinite(value)) return ND;
    return `${signed(value * 100, 1)}%`;
  }
  if (value === null || value === undefined || !Number.isFinite(value)) return ND;

  switch (unit) {
    case "pp":
      return `${signed(value, 1)} pp`;
    case "pct":
      return `${signed(value, 1)}%`;
    case "count":
    case "number":
      return signed(value, 0);
    default:
      return signed(value, 2);
  }
}

/** Sufijo aclaratorio para una variación: "en puntos porcentuales", etc. */
export function deltaHint(unit: Unit | null | undefined): string | null {
  switch (unit) {
    case "pp":
      return "Variación en puntos porcentuales: la diferencia absoluta entre dos porcentajes.";
    case "ratio":
      return "Variación relativa contra el período anterior (0,62 = +62%).";
    case "pct":
      return "Variación expresada en porcentaje.";
    default:
      return null;
  }
}
