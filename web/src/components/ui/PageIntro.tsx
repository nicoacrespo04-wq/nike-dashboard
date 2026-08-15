import { cn } from '@/lib/utils'

export interface PageIntroProps {
  /**
   * La pregunta de negocio que responde la pantalla ("¿Quién compite?").
   * Es el hilo conductor del motor de decisión: qué pasa → quién compite →
   * cuánto importa → por qué → qué hacer.
   */
  question: string
  /**
   * Título propio de la vista. Se omite en las pantallas cuyo nombre ya
   * aparece en la barra superior del dashboard; se usa cuando el título es
   * dinámico (un producto, un match).
   */
  title?: string
  /** Qué muestra la pantalla y cómo leerla. */
  description: string
  /** Acción principal (link o botón) alineada a la derecha. */
  actions?: React.ReactNode
  className?: string
}

/**
 * Encabezado de una pantalla de INTELLIGENCE.
 *
 * Deliberadamente más liviano que el `PageHeader` original: el nombre de la
 * solapa ya lo muestra la barra superior del dashboard, así que acá sólo va la
 * pregunta que responde y la bajada explicativa. Evita el título repetido dos
 * veces en pantalla.
 */
export default function PageIntro({
  question,
  title,
  description,
  actions,
  className,
}: PageIntroProps) {
  return (
    <header
      className={cn(
        'mb-5 flex flex-col gap-3 border-b border-surface-border pb-4 lg:flex-row lg:items-end lg:justify-between',
        className,
      )}
    >
      <div className="max-w-3xl min-w-0">
        <p className="mb-1.5 inline-flex items-center gap-2 text-label font-bold uppercase text-nike-red">
          <span aria-hidden="true" className="inline-block h-3 w-[3px] rounded-pill bg-nike-red" />
          {question}
        </p>
        {title && (
          <h2 className="text-xl font-bold leading-tight tracking-tight text-nike-ink">{title}</h2>
        )}
        <p className={cn('text-sm leading-relaxed text-nike-muted', title && 'mt-1.5')}>
          {description}
        </p>
      </div>
      {actions && <div className="flex-shrink-0">{actions}</div>}
    </header>
  )
}
