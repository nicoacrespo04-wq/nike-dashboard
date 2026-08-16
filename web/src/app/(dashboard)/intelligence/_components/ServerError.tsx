'use client'

import { useRouter } from 'next/navigation'
import { ErrorState } from '@/components/ui'
import { CommandHint } from '@/components/intelligence/hints'

/**
 * Estado de error de una pantalla resuelta en el servidor.
 *
 * El `ErrorState` compartido recibe `onRetry` como función, y un Server
 * Component no puede pasar funciones a través del límite cliente/servidor. Por
 * eso el botón vive acá: reintentar es volver a renderizar la ruta en el
 * servidor (`router.refresh()`), no re-pedir desde el browser.
 *
 * El mensaje que llega ya trae el comando para levantar el motor — es la misma
 * copy que emite el proxy, definida una sola vez en `lib/intelligence/server`.
 */
export default function ServerError({
  description,
  title = 'No pudimos cargar el motor de inteligencia',
  size = 'md',
}: {
  description: string
  title?: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const router = useRouter()
  return (
    <div>
      <ErrorState
        title={title}
        description={description}
        onRetry={() => router.refresh()}
        size={size}
      />
      <div className="mx-auto max-w-md px-4 pb-2">
        <CommandHint />
      </div>
    </div>
  )
}
