/**
 * Normalización de los `drivers` que devuelve el backend.
 *
 * El contrato nominal es `[{name, value, contribution}]` y así los emite el
 * motor de oportunidades. Retail media, en cambio, envuelve todo en un único
 * objeto de contexto: `[{rationale, nike_stock_pct, …, factors:[{name,…}]}]`.
 * La UI no debe romperse por eso, así que acá se aplanan ambas formas a una
 * sola y se rescatan además el racional y las métricas crudas del caso.
 */

import type { Driver, DriverEnvelope, DriversPayload, SignalValue } from "@/types/intelligence";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asDriver(value: unknown): Driver | null {
  if (!isRecord(value)) return null;
  const name = value["name"];
  if (typeof name !== "string" || name.trim() === "") return null;
  const num = (key: string): number | null => {
    const raw = value[key];
    return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
  };
  const str = (key: string): string | null => {
    const raw = value[key];
    return typeof raw === "string" && raw.trim() !== "" ? raw : null;
  };
  const detail = value["detail"];
  return {
    name,
    label: str("label"),
    value: num("value"),
    unit: str("unit"),
    contribution: num("contribution"),
    detail: isRecord(detail) ? detail : undefined,
  };
}

/** Aplana cualquiera de las dos formas a una lista de drivers usable. */
export function normalizeDrivers(payload: DriversPayload | null | undefined): Driver[] {
  if (!Array.isArray(payload)) return [];
  const out: Driver[] = [];
  for (const entry of payload) {
    const direct = asDriver(entry);
    if (direct) {
      out.push(direct);
      continue;
    }
    if (isRecord(entry) && Array.isArray(entry["factors"])) {
      for (const factor of entry["factors"]) {
        const nested = asDriver(factor);
        if (nested) out.push(nested);
      }
    }
  }
  return out;
}

/**
 * Índice `name` → señal observada, para pedir una métrica por nombre.
 *
 * `signals` es el campo hermano de `drivers`: los drivers son las VARIABLES DEL
 * MODELO (ponderadas, `contribution` suma 100) y las señales son las MÉTRICAS
 * OBSERVADAS del caso, cada una con su unidad. Antes las métricas viajaban
 * sueltas dentro del sobre de drivers y había que rescatarlas con
 * `driverMetrics()`, que contra el contrato nuevo devuelve vacío.
 */
export function signalIndex(
  signals: SignalValue[] | null | undefined,
): Record<string, SignalValue> {
  const index: Record<string, SignalValue> = {};
  if (!Array.isArray(signals)) return index;
  for (const signal of signals) {
    if (signal && typeof signal.name === "string") index[signal.name] = signal;
  }
  return index;
}

/** Racional en prosa que algunos motores adjuntan junto a los drivers. */
export function driversRationale(payload: DriversPayload | null | undefined): string | null {
  if (!Array.isArray(payload)) return null;
  for (const entry of payload) {
    if (isRecord(entry)) {
      const rationale = entry["rationale"];
      if (typeof rationale === "string" && rationale.trim() !== "") return rationale;
    }
  }
  return null;
}

/**
 * Métricas crudas del caso que viajaban en el sobre de retail media, cuando
 * `drivers` era `[{rationale, nike_stock_pct, …, factors:[…]}]`.
 *
 * @deprecated Contra el contrato nuevo devuelve `{}`: las métricas ahora son un
 * campo hermano (`signals`, con unidad declarada) y se leen con `signalIndex()`.
 * Se conserva sólo como respaldo de una base servida por un motor viejo.
 */
export function driverMetrics(payload: DriversPayload | null | undefined): Record<string, number> {
  const metrics: Record<string, number> = {};
  if (!Array.isArray(payload)) return metrics;
  for (const entry of payload) {
    if (!isRecord(entry) || typeof entry["name"] === "string") continue;
    for (const [key, value] of Object.entries(entry as DriverEnvelope)) {
      if (typeof value === "number" && Number.isFinite(value)) metrics[key] = value;
    }
  }
  return metrics;
}

/** Busca un driver por nombre y devuelve su `value` (0..1 normalizado). */
export function driverValue(drivers: Driver[], name: string): number | null {
  return drivers.find((d) => d.name === name)?.value ?? null;
}
