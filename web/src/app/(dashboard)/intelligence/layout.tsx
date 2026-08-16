import { Suspense } from 'react'
import PipelineBanner from './_components/PipelineBanner'
import { PipelineBannerSkeleton } from './_components/skeletons'

/**
 * Layout de la sección INTELLIGENCE.
 *
 * Vive dentro del grupo `(dashboard)`, así que hereda sidebar, barra superior
 * y la protección de NextAuth (`src/middleware.ts`). Lo único que agrega es la
 * cinta de estado del motor: si el backend no está levantado, el usuario lo ve
 * acá arriba una sola vez en vez de deducirlo pantalla por pantalla.
 *
 * El layout NO se vuelve a renderizar al navegar entre solapas hermanas, así
 * que la cinta se resuelve una vez por carga y no una vez por navegación. Va
 * dentro de un `Suspense` para que un backend lento no retrase el contenido de
 * la pantalla: primero aparece la solapa, la cinta llega cuando responde.
 */
export default function IntelligenceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <Suspense fallback={<PipelineBannerSkeleton />}>
        <PipelineBanner />
      </Suspense>
      {children}
    </div>
  )
}
