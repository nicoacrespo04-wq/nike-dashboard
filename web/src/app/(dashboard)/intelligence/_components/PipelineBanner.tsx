import { AlertTriangle, Database } from 'lucide-react'
import { fetchHealth } from '@/lib/intelligence/server'
import { num } from '@/lib/format'
import RefreshButton from './RefreshButton'

/**
 * Cinta de estado del motor de inteligencia, resuelta en el servidor.
 *
 * Responde dos preguntas antes de que el usuario se pregunte por qué una
 * pantalla está vacía:
 *   1. ¿El backend está vivo?  (si no, cómo levantarlo)
 *   2. ¿El pipeline corrió?    (qué tablas quedaron sin datos)
 *
 * Server Component a propósito: la cinta no tiene interacción salvo el
 * reintento, y resolverla en el servidor evita el round-trip cliente y —sobre
 * todo— el salto de layout de la versión anterior, que reservaba una franja
 * gris y después la reemplazaba por un banner de otra altura.
 *
 * `/health` es el único endpoint que NUNCA se cachea (`cacheRuleFor`): un
 * semáforo cacheado no es un semáforo.
 *
 * Es informativa, nunca bloqueante: las solapas de RETAIL & PRICING no
 * dependen de este servicio y siguen funcionando aunque esto esté en rojo.
 */
export default async function PipelineBanner() {
  const result = await fetchHealth()

  if (!result.ok) {
    return (
      <div
        role="alert"
        className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-bml-lose-soft bg-bml-lose-soft px-4 py-2.5"
      >
        <AlertTriangle size={15} className="flex-shrink-0 text-bml-lose-ink" aria-hidden="true" />
        <span className="text-xs font-semibold text-bml-lose-ink">
          Motor de inteligencia no disponible
        </span>
        <span className="min-w-0 flex-1 text-2xs leading-relaxed text-bml-lose-ink/80">
          {result.error}
        </span>
        <RefreshButton />
      </div>
    )
  }

  const data = result.data
  const total = Object.keys(data.tables).length
  const filled = total - data.empty_tables.length
  const rows = Object.values(data.tables).reduce((acc, n) => acc + n, 0)
  const complete = data.empty_tables.length === 0

  return (
    <div
      className={`mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-4 py-2 text-2xs ${
        complete
          ? 'border-surface-border bg-surface-muted text-nike-ink-soft'
          : 'border-bml-meet-soft bg-bml-meet-soft text-bml-meet-ink'
      }`}
    >
      <Database size={13} className="flex-shrink-0 text-nike-muted" aria-hidden="true" />
      <span className="tabular font-semibold">
        Pipeline: {filled}/{total} tablas con datos · {num(rows)} registros
      </span>
      {complete ? (
        <span className="text-nike-muted">Todas las etapas del pipeline poblaron su tabla.</span>
      ) : (
        <span className="min-w-0 truncate">
          Etapas pendientes → sin datos:{' '}
          <span className="font-mono">{data.empty_tables.join(', ')}</span>
        </span>
      )}
    </div>
  )
}
