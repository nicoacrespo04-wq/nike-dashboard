/**
 * fetchJson — wrapper de `fetch` para las páginas del dashboard.
 *
 * Varios `fetch(...).then(r => r.json())` no manejaban el error: si la API
 * devolvía 500 (o el body no era JSON) la promesa quedaba rechazada, el
 * `setLoading(false)` nunca corría y la UI se quedaba en "loading" para
 * siempre. Acá el error se normaliza a un `Error` con mensaje mostrable.
 */
/** Body de error que devuelven las routes: `{ error: '...' }`. */
function errorFromBody(body: unknown): string | null {
  if (body && typeof body === 'object' && 'error' in body) {
    const { error } = body as { error: unknown }
    if (typeof error === 'string') return error
  }
  return null
}

export async function fetchJson<T = unknown>(url: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new Error(`No se pudo conectar con ${url}. Revisá tu conexión.`)
  }

  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }

  const bodyError = errorFromBody(body)
  if (!res.ok) {
    throw new Error(bodyError ?? `Error ${res.status} al consultar ${url}`)
  }
  if (bodyError) {
    throw new Error(bodyError)
  }
  return body as T
}

/** Mensaje legible a partir de cualquier cosa que caiga en un `catch`. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return 'Error inesperado al cargar los datos.'
}
