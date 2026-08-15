import PipelineStatus from '@/components/intelligence/PipelineStatus'

/**
 * Layout de la sección INTELLIGENCE.
 *
 * Vive dentro del grupo `(dashboard)`, así que hereda sidebar, barra superior
 * y la protección de NextAuth (`src/middleware.ts`). Lo único que agrega es la
 * cinta de estado del motor: si el backend no está levantado, el usuario lo ve
 * acá arriba una sola vez en vez de deducirlo pantalla por pantalla.
 */
export default function IntelligenceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <PipelineStatus />
      {children}
    </div>
  )
}
