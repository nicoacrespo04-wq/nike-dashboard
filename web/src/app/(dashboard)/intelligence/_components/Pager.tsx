'use client'

import { num } from '@/lib/format'

export interface PagerProps {
  /** Página actual, base 0. */
  page: number
  /** Filas por página. */
  pageSize: number
  /** `offset` que devolvió el backend para la página en pantalla. */
  offset: number
  /** Filas efectivamente recibidas. */
  shown: number
  /** Total REAL del backend, no el largo del array. */
  total: number
  /** Nombre de la entidad, en singular. Ej. `'producto'`. */
  noun: string
  onPage: (page: number) => void
  /** Se está pidiendo otra página: los botones se bloquean para no encolar. */
  busy?: boolean
}

/**
 * Controles de paginación server-side.
 *
 * Muestra siempre el total real que informó el backend (no el largo de la
 * página): sin ese número el usuario no sabe si está viendo 40 productos de 45
 * o de 40.000. Las páginas se cuentan desde el `offset` que devolvió el
 * backend, no desde el estado local, así un backend que recorta el `limit`
 * sigue reportando bien.
 */
export default function Pager({
  page,
  pageSize,
  offset,
  shown,
  total,
  noun,
  onPage,
  busy = false,
}: PagerProps) {
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + shown, total)
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const isFirst = page <= 0
  const isLast = offset + shown >= total

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-nike-ink-soft">
        <span className="tabular font-bold text-nike-ink">{num(total)}</span> {noun}(s) · mostrando{' '}
        <span className="tabular">
          {num(from)}–{num(to)}
        </span>{' '}
        · página <span className="tabular font-semibold">{page + 1}</span> de{' '}
        <span className="tabular">{num(pages)}</span>
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={isFirst || busy}
          onClick={() => onPage(0)}
          className="rounded-lg border border-surface-border px-2.5 py-1 text-2xs font-semibold text-nike-ink-soft transition-colors duration-fast hover:border-surface-border-strong disabled:opacity-40"
        >
          « Primera
        </button>
        <button
          type="button"
          disabled={isFirst || busy}
          onClick={() => onPage(Math.max(0, page - 1))}
          className="rounded-lg border border-surface-border px-3 py-1 text-2xs font-semibold text-nike-ink-soft transition-colors duration-fast hover:border-surface-border-strong disabled:opacity-40"
        >
          ← Anterior
        </button>
        <button
          type="button"
          disabled={isLast || busy}
          onClick={() => onPage(page + 1)}
          className="rounded-lg border border-surface-border px-3 py-1 text-2xs font-semibold text-nike-ink-soft transition-colors duration-fast hover:border-surface-border-strong disabled:opacity-40"
        >
          Siguiente →
        </button>
        <button
          type="button"
          disabled={isLast || busy}
          onClick={() => onPage(pages - 1)}
          className="rounded-lg border border-surface-border px-2.5 py-1 text-2xs font-semibold text-nike-ink-soft transition-colors duration-fast hover:border-surface-border-strong disabled:opacity-40"
        >
          Última »
        </button>
      </div>
    </div>
  )
}
