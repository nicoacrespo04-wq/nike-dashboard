'use client'

import { useRouter } from 'next/navigation'
import { ErrorState } from '@/components/ui'

/**
 * Estado de error de una pantalla resuelta en el servidor.
 *
 * El `ErrorState` compartido recibe `onRetry` como función, y un Server
 * Component no puede pasar funciones a través del límite cliente/servidor. Por
 * eso el botón vive acá: reintentar es volver a renderizar la ruta en el
 * servidor (`router.refresh()`), no re-pedir desde el browser.
 *
 * El mensaje que llega ya es accionable y viene de una sola definición
 * (`offlineMessage()` en `lib/intelligence/server`), que distingue "el motor no
 * está configurado" de "está configurado y no responde".
 *
 * Acá NO va el comando de `uvicorn`. Lo tenía, y era una respuesta equivocada
 * para el caso más común de todos: alguien mirando el dashboard en Vercel, donde
 * no hay ninguna terminal en la que escribirlo y donde el problema real es que
 * el motor nunca se desplegó. El comando sigue vivo en `CommandHint`, que es lo
 * que muestran los estados VACÍOS (base levantada pero sin filas) — ahí sí es la
 * acción correcta, y se ve casi siempre en desarrollo local.
 */
export default function ServerError({
  description,
  title = 'Las solapas de Intelligence no están disponibles',
  size = 'md',
}: {
  description: string
  title?: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const router = useRouter()
  return (
    <ErrorState
      title={title}
      description={description}
      onRetry={() => router.refresh()}
      size={size}
    />
  )
}
