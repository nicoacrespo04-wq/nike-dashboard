import { cn } from '@/lib/utils'

export interface CardProps {
  children: React.ReactNode
  className?: string
  /** `false` para contenido que sangra hasta el borde (tablas). */
  padded?: boolean
  /** Elemento HTML del contenedor. */
  as?: 'section' | 'article' | 'div'
}

/**
 * Contenedor estándar de contenido.
 *
 * No define estilos propios: usa las clases `.nike-card` / `.nike-card-flush`
 * de `globals.css`, que son las mismas que ya usan las solapas de retail. Así
 * una card de Opportunity Center y una de Control Retailers son la misma card.
 */
export default function Card({ children, className, padded = true, as: Tag = 'section' }: CardProps) {
  return (
    <Tag className={cn(padded ? 'nike-card' : 'nike-card-flush', className)}>{children}</Tag>
  )
}
