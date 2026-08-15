'use client'

import ErrorState from './ErrorState'
import { Skeleton } from './Skeleton'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

export interface AsyncSectionProps<T> {
  state: AsyncState<T>
  /** Qué considerar "sin datos" cuando la respuesta llegó bien pero vacía. */
  isEmpty?: (data: T) => boolean
  /** Qué mostrar en ese caso (normalmente un `EmptyState`). */
  empty: React.ReactNode
  children: (data: T) => React.ReactNode
  /** Renglones del esqueleto de carga. */
  loadingRows?: number
}

/**
 * Cargando / error / vacío / contenido, resuelto en un solo lugar.
 *
 * Los cuatro estados son visualmente distintos a propósito: "no hay datos" no
 * se parece a "se rompió", y ninguno de los dos se parece a "está cargando".
 * Usa las primitivas del dashboard (`Skeleton`, `ErrorState`), no versiones
 * paralelas.
 */
export default function AsyncSection<T>({
  state,
  isEmpty,
  empty,
  children,
  loadingRows = 3,
}: AsyncSectionProps<T>) {
  if (state.loading && state.data === null) {
    return (
      <div className="flex flex-col gap-3" role="status" aria-live="polite" aria-busy="true">
        <span className="sr-only">Cargando datos del motor de inteligencia</span>
        {Array.from({ length: loadingRows }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full rounded-lg" />
        ))}
      </div>
    )
  }

  if (state.error) {
    return (
      <ErrorState
        title="No pudimos cargar el motor de inteligencia"
        description={state.error}
        onRetry={state.reload}
      />
    )
  }

  if (state.data === null) return <>{empty}</>
  if (isEmpty && isEmpty(state.data)) return <>{empty}</>
  return <>{children(state.data)}</>
}
