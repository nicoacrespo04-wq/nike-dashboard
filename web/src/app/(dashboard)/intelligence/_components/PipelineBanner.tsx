import { AlertTriangle, Database } from 'lucide-react'
import { fetchHealth } from '@/lib/intelligence/server'
import { num } from '@/lib/format'
import RefreshButton from './RefreshButton'

/**
 * Cinta de estado del motor de inteligencia, resuelta en el servidor.
 *
 * Responde dos preguntas antes de que el usuario se pregunte por qué una
 * pantalla está vacía:
 *   1. ¿El motor está vivo?    (si no, por qué y qué hacer — ver `offlineMessage`)
 *   2. ¿Está cargando todavía? (arranque en frío: no hay nada que arreglar)
 *   3. ¿El pipeline corrió?    (qué tablas quedaron sin datos)
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
          Las solapas de Intelligence no están disponibles
        </span>
        <span className="min-w-0 flex-1 text-2xs leading-relaxed text-bml-lose-ink/80">
          {result.error}
        </span>
        <RefreshButton />
      </div>
    )
  }

  const data = result.data

  // Arranque en frío: el motor está vivo pero todavía cargando su base. Sin este
  // caso, durante ese minuto la cinta muestra "3/17 tablas con datos" en rojo —
  // que es cierto y a la vez completamente engañoso: no falta nada, está
  // trabajando. El free tier de Render pasa por acá cada vez que se despierta.
  if (data.data?.status === 'building') {
    return (
      <div
        role="status"
        className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-bml-meet-soft bg-bml-meet-soft px-4 py-2.5"
      >
        <Database size={13} className="flex-shrink-0 text-bml-meet-ink" aria-hidden="true" />
        <span className="text-xs font-semibold text-bml-meet-ink">
          El motor está cargando sus datos
        </span>
        <span className="min-w-0 flex-1 text-2xs leading-relaxed text-bml-meet-ink/80">
          Tarda alrededor de un minuto. Las pantallas de Intelligence van a estar
          incompletas hasta que termine; el resto del dashboard no se ve afectado.
        </span>
        <RefreshButton />
      </div>
    )
  }

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
