/**
 * Sincronización de filtros con la URL sin volver al servidor.
 *
 * `router.replace()` de App Router vuelve a renderizar el Server Component de
 * la ruta: con filtros interactivos eso duplicaría el trabajo (una consulta en
 * el servidor + la del cliente) por cada tecla. `history.replaceState` deja la
 * URL compartible y el botón "atrás" coherente sin disparar nada.
 */
export type UrlValue = string | number | null | undefined

export function syncUrl(values: Record<string, UrlValue>): void {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === '' || value === 0) {
      url.searchParams.delete(key)
    } else {
      url.searchParams.set(key, String(value))
    }
  }
  window.history.replaceState(null, '', `${url.pathname}${url.search}`)
}

/** Primer valor de un `searchParam` de App Router, ya normalizado a string. */
export function param(
  searchParams: Record<string, string | string[] | undefined>,
  key: string,
): string {
  const raw = searchParams[key]
  const value = Array.isArray(raw) ? raw[0] : raw
  return value ?? ''
}

/** Entero no negativo de un `searchParam`, con valor por defecto. */
export function intParam(
  searchParams: Record<string, string | string[] | undefined>,
  key: string,
  fallback = 0,
): number {
  const parsed = Number(param(searchParams, key))
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : fallback
}
