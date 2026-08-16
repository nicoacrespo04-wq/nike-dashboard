'use client'

import { useTransition } from 'react'
import { RotateCw } from 'lucide-react'

/**
 * Reintento de una vista resuelta en el servidor.
 *
 * `router.refresh()` no sirve acá: la cinta de estado se pinta desde un fetch
 * `no-store` dentro del layout, y lo que queremos es volver a pedirlo. Un
 * `refresh` del router alcanza — pero envuelto en `useTransition` para que el
 * botón muestre que está trabajando en vez de parecer que no hizo nada.
 */
export default function RefreshButton({
  label = 'Reintentar',
  className,
}: {
  label?: string
  className?: string
}) {
  const [pending, startTransition] = useTransition()

  return (
    <button
      type="button"
      disabled={pending}
      onClick={() => {
        startTransition(() => {
          // `location.reload` re-ejecuta el layout entero, que es donde vive el
          // fetch de `/health`. Es el reintento honesto para una cinta que se
          // resuelve en el servidor.
          window.location.reload()
        })
      }}
      className={
        className ??
        'inline-flex flex-shrink-0 items-center gap-1 rounded border border-bml-lose px-2 py-1 text-2xs font-semibold text-bml-lose-ink transition-colors duration-fast hover:bg-white disabled:opacity-50'
      }
    >
      <RotateCw size={11} aria-hidden="true" className={pending ? 'animate-spin' : undefined} />
      {label}
    </button>
  )
}
