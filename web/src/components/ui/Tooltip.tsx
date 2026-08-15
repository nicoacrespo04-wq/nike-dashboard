'use client'

import { cloneElement, isValidElement, useCallback, useId, useState } from 'react'
import { Info } from 'lucide-react'
import { cn } from '@/lib/utils'

type Side = 'top' | 'bottom' | 'left' | 'right'

export interface TooltipProps {
  /** Contenido del globo. Texto corto; explica, no repite. */
  content: React.ReactNode
  /** Disparador. Debe ser enfocable (botón, link) para ser accesible. */
  children: React.ReactNode
  side?: Side
  /** Ancho máximo del globo en px. */
  maxWidth?: number
  className?: string
  /** Clase del wrapper que envuelve al disparador. */
  wrapperClassName?: string
}

const SIDE_CLASS: Record<Side, string> = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
  left: 'right-full top-1/2 -translate-y-1/2 mr-2',
  right: 'left-full top-1/2 -translate-y-1/2 ml-2',
}

/**
 * Tooltip accesible sin dependencias.
 *
 * - Se abre con hover **y** con foco de teclado.
 * - Se cierra con `Escape`.
 * - El globo se anuncia vía `aria-describedby` sobre el disparador, así que el
 *   contenido es leído por lectores de pantalla en lugar de perderse.
 */
export default function Tooltip({
  content,
  children,
  side = 'top',
  maxWidth = 260,
  className,
  wrapperClassName,
}: TooltipProps) {
  const id = useId()
  const [open, setOpen] = useState(false)

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') setOpen(false)
  }, [])

  // Enlaza el disparador con el globo para lectores de pantalla.
  const trigger = isValidElement(children)
    ? cloneElement(children as React.ReactElement<Record<string, unknown>>, {
        'aria-describedby': id,
      })
    : children

  return (
    <span
      className={cn('relative inline-flex', wrapperClassName)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={onKeyDown}
    >
      {trigger}
      <span
        id={id}
        role="tooltip"
        // `hidden` en vez de desmontar: el id existe siempre para aria-describedby.
        hidden={!open}
        style={{ maxWidth }}
        className={cn(
          'absolute z-50 w-max animate-fade-in rounded-lg bg-nike-black px-3 py-2',
          'text-left text-[11px] font-normal normal-case leading-snug tracking-normal text-white shadow-popover',
          'pointer-events-none whitespace-normal',
          SIDE_CLASS[side],
          className,
        )}
      >
        {content}
      </span>
    </span>
  )
}

export interface InfoTipProps {
  /** Explicación de la métrica: de dónde sale el número. */
  content: React.ReactNode
  /** Label accesible del botón. Ej: "Cómo se calcula: Gap %". */
  label: string
  side?: Side
  size?: number
  className?: string
}

/**
 * Ícono "i" con tooltip. Es el patrón estándar para explicar **cómo se calcula**
 * una métrica: en una herramienta de inteligencia competitiva, entender el
 * origen del número es parte del producto.
 */
export function InfoTip({ content, label, side = 'top', size = 13, className }: InfoTipProps) {
  return (
    <Tooltip content={content} side={side}>
      <button
        type="button"
        aria-label={label}
        className={cn(
          'inline-flex items-center justify-center rounded-full text-nike-mid-gray',
          'transition-colors duration-fast hover:text-nike-ink',
          className,
        )}
        // No dispara ninguna acción: el tooltip se abre con hover/foco.
        onClick={(e) => e.preventDefault()}
      >
        <Info size={size} aria-hidden="true" />
      </button>
    </Tooltip>
  )
}
