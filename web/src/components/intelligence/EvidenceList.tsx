import { ExternalLink, LinkIcon } from 'lucide-react'
import type { EvidenceItem } from '@/types/intelligence'
import { date, truncateText } from '@/lib/format'
import { evidenceSource, evidenceText } from '@/lib/intelligence/insights'
import { InfoTip } from '@/components/ui'

/**
 * Evidencia navegable de un insight o un tópico.
 *
 * Dos reglas que definen este componente:
 *
 *  1. **Si hay URL, se puede ir al original.** Una cita sin forma de verificarla
 *     obliga a creerle al motor. El link abre el comentario, la reseña o la nota
 *     de donde salió la frase.
 *  2. **Si no hay URL, la evidencia NO se esconde.** Se marca "sin link" y se
 *     dice por qué (`url_status` / `url_reason`): la señal social agregada no
 *     guarda posts por privacidad, la tabla de reviews no tiene permalink, la
 *     mención editorial se guardó sin URL. "No hay link" y "no hay evidencia"
 *     son cosas distintas, y esconder la segunda por culpa de la primera sería
 *     ocultarle al usuario el respaldo que sí existe.
 */
export function EvidenceList({
  items,
  max = 3,
  showCounts = true,
}: {
  items: EvidenceItem[]
  max?: number
  /** Resumen "N evidencias · M con link". */
  showCounts?: boolean
}) {
  if (items.length === 0) {
    return (
      <p className="text-2xs italic text-nike-muted">
        Sin evidencia adjunta: el insight no es publicable.
      </p>
    )
  }

  const linked = items.filter((e) => typeof e.url === 'string' && e.url !== '').length

  return (
    <div>
      {showCounts && (
        <p className="label-caps mb-1.5">
          Evidencia ({items.length})
          {items.length > 0 && (
            <span className="ml-1 font-normal normal-case tracking-normal text-nike-muted">
              {linked > 0 ? `· ${linked} con link al original` : '· sin links disponibles'}
            </span>
          )}
        </p>
      )}
      <ul className="space-y-1.5">
        {items.slice(0, max).map((item, i) => (
          <EvidenceRow key={i} item={item} />
        ))}
      </ul>
    </div>
  )
}

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const url = typeof item.url === 'string' && item.url !== '' ? item.url : null
  const quote = truncateText(evidenceText(item), 160)
  const when = typeof item.date === 'string' ? item.date : null
  const origin =
    typeof item.source_name === 'string' && item.source_name !== ''
      ? item.source_name
      : evidenceSource(item)
  const kind =
    (typeof item.type_label === 'string' && item.type_label) ||
    (typeof item.source_label === 'string' && item.source_label) ||
    null

  return (
    <li className="border-l-2 border-surface-border pl-2.5">
      <p className="text-2xs leading-relaxed text-nike-ink-soft">“{quote}”</p>

      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px] text-nike-muted">
        <span className="font-semibold text-nike-ink-soft">{origin}</span>
        {kind && <span>· {kind}</span>}
        {when && <span>· {date(when)}</span>}

        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 font-semibold text-nike-red hover:underline"
          >
            ver original
            <ExternalLink size={9} aria-hidden="true" />
          </a>
        ) : (
          <span className="inline-flex items-center gap-0.5 italic">
            <LinkIcon size={9} aria-hidden="true" className="opacity-60" />
            sin link
            {typeof item.url_reason === 'string' && item.url_reason !== '' && (
              <InfoTip
                label="Por qué esta evidencia no tiene link"
                content={item.url_reason}
                size={10}
              />
            )}
          </span>
        )}
      </p>
    </li>
  )
}
