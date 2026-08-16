import type { DriversPayload } from '@/types/intelligence'
import { dec, pct } from '@/lib/format'
import { normalizeDrivers } from '@/lib/intelligence/drivers'
import { type GlossaryTerms, termFor } from '@/lib/intelligence/glossary'
import { humanize } from '@/components/charts/palette'
import { MeterBar } from '@/components/ui'
import { GlossaryTip } from './GlossaryTip'

/**
 * El "¿por qué?" de una oportunidad o recomendación: drivers ordenados por
 * contribución, con barra + etiqueta directa.
 *
 * Acepta el payload crudo del backend porque las dos formas (lista plana y
 * sobre con `factors` adentro) conviven en la API.
 *
 * Con `terms` cada driver suma su ficha del glosario: sin saber qué mide
 * `editorial`, un "12% de contribución" no explica nada.
 */
export function DriverList({
  drivers,
  max = 6,
  color = '#2A78D6',
  emptyLabel = 'El motor no registró drivers para este caso.',
  terms,
}: {
  drivers: DriversPayload | null | undefined
  max?: number
  color?: string
  emptyLabel?: string
  /** Índice del glosario (`termIndex`). Sin él no se muestran tooltips. */
  terms?: GlossaryTerms
}) {
  const normalized = normalizeDrivers(drivers)

  if (normalized.length === 0) {
    return <p className="text-2xs italic text-nike-muted">{emptyLabel}</p>
  }

  const ordered = [...normalized]
    .sort((a, b) => (b.contribution ?? 0) - (a.contribution ?? 0))
    .slice(0, max)
  const maxContribution = Math.max(...ordered.map((d) => d.contribution ?? 0), 1)

  return (
    <ul className="space-y-1.5">
      {ordered.map((driver, i) => {
        // El backend ya manda el nombre de negocio; `humanize` es el respaldo.
        const label = driver.label ?? humanize(driver.name)
        return (
          <li
            key={`${driver.name}-${i}`}
            // Los labels vienen del backend en castellano de negocio y son más
            // largos que los nombres técnicos: la columna del nombre se lleva
            // el ancho, la barra puede achicarse sin perder legibilidad.
            className="grid grid-cols-[minmax(130px,1.7fr)_minmax(48px,0.8fr)_auto] items-center gap-2"
          >
            <span className="flex min-w-0 items-center gap-1">
              <span className="truncate text-2xs text-nike-ink-soft" title={label}>
                {label}
              </span>
              {terms && <GlossaryTip term={termFor(terms, driver.name)} size={11} />}
            </span>
            <MeterBar value={driver.contribution} max={maxContribution} color={color} height={6} />
            <span className="tabular w-16 text-right text-2xs font-semibold text-nike-ink">
              {driver.contribution !== null && driver.contribution !== undefined
                ? pct(driver.contribution, 0)
                : dec(driver.value)}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
