/**
 * fetchJson — wrapper de `fetch` para las páginas del dashboard.
 *
 * Varios `fetch(...).then(r => r.json())` no manejaban el error: si la API
 * devolvía 500 (o el body no era JSON) la promesa quedaba rechazada, el
 * `setLoading(false)` nunca corría y la UI se quedaba en "loading" para
 * siempre. Acá el error se normaliza a un `Error` con mensaje mostrable.
 */
export async function fetchJson<T = any>(url: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new Error(`No se pudo conectar con ${url}. Revisá tu conexión.`)
  }

  let body: any = null
  try {
    body = await res.json()
  } catch {
    body = null
  }

  if (!res.ok) {
    throw new Error(body?.error ?? `Error ${res.status} al consultar ${url}`)
  }
  if (body && typeof body === 'object' && typeof body.error === 'string') {
    throw new Error(body.error)
  }
  return body as T
}

/** Mensaje legible a partir de cualquier cosa que caiga en un `catch`. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return 'Error inesperado al cargar los datos.'
}
