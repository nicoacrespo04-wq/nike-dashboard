'use client'

import { useId } from 'react'
import { Filter, Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface FilterOption<T extends string = string> {
  value: T
  label: string
  /** Conteo opcional que se muestra al lado de la etiqueta. */
  count?: number
}

/* ────────────────────────────────────────────────────────────────────────────
   Contenedor
   ──────────────────────────────────────────────────────────────────────────── */

export interface FilterBarProps {
  children: React.ReactNode
  /** Muestra el ícono de embudo a la izquierda. */
  showIcon?: boolean
  /** Etiqueta accesible de la región de filtros. */
  label?: string
  className?: string
}

/**
 * Barra de filtros.
 *
 * Agrupa los controles en una sola fila (envolvible en mobile) con la misma
 * altura y el mismo tratamiento de foco, en vez de `<select>` sueltos con
 * clases ad-hoc repetidas en cada página.
 */
export default function FilterBar({
  children,
  showIcon = true,
  label = 'Filtros',
  className,
}: FilterBarProps) {
  return (
    <section
      aria-label={label}
      className={cn('nike-card flex flex-wrap items-end gap-3', className)}
    >
      {showIcon && (
        <Filter size={15} className="mb-2.5 flex-shrink-0 text-nike-faint" aria-hidden="true" />
      )}
      {children}
    </section>
  )
}

/* ────────────────────────────────────────────────────────────────────────────
   Select
   ──────────────────────────────────────────────────────────────────────────── */

export interface FilterSelectProps<T extends string = string> {
  label: string
  value: T
  options: FilterOption<T>[]
  onChange: (value: T) => void
  /** Oculta visualmente el label (queda para lectores de pantalla). */
  hideLabel?: boolean
  className?: string
  disabled?: boolean
}

export function FilterSelect<T extends string = string>({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
  className,
  disabled,
}: FilterSelectProps<T>) {
  const id = useId()
  return (
    <div className={cn('flex min-w-0 flex-col gap-1', className)}>
      <label
        htmlFor={id}
        className={cn('label-caps', hideLabel && 'sr-only')}
      >
        {label}
      </label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as T)}
        className="field-control disabled:cursor-not-allowed disabled:opacity-50"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.count != null ? `${o.label} (${o.count.toLocaleString('es-AR')})` : o.label}
          </option>
        ))}
      </select>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────────
   Segmented control
   ──────────────────────────────────────────────────────────────────────────── */

export interface SegmentedControlProps<T extends string = string> {
  label: string
  value: T
  options: FilterOption<T>[]
  onChange: (value: T) => void
  hideLabel?: boolean
  size?: 'sm' | 'md'
  className?: string
}

/**
 * Control segmentado (tabs de filtro).
 *
 * Implementado con `role="group"` + `aria-pressed` en vez de tabs ARIA: acá los
 * botones filtran contenido en la misma vista, no cambian de panel.
 */
export function SegmentedControl<T extends string = string>({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
  size = 'md',
  className,
}: SegmentedControlProps<T>) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1', className)}>
      <span className={cn('label-caps', hideLabel && 'sr-only')}>{label}</span>
      <div className="segmented" role="group" aria-label={label}>
        {options.map((o) => {
          const active = o.value === value
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(o.value)}
              className={cn('segmented-item', size === 'sm' && 'px-3 py-1.5')}
            >
              {o.label}
              {o.count != null && (
                <span className="ml-1.5 tabnum opacity-60">{o.count.toLocaleString('es-AR')}</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────────
   Search
   ──────────────────────────────────────────────────────────────────────────── */

export interface FilterSearchProps {
  label?: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  hideLabel?: boolean
  className?: string
}

export function FilterSearch({
  label = 'Buscar',
  value,
  onChange,
  placeholder = 'Buscar producto, franchise, SKU…',
  hideLabel = false,
  className,
}: FilterSearchProps) {
  const id = useId()
  return (
    <div className={cn('flex min-w-0 flex-1 flex-col gap-1', className)}>
      <label htmlFor={id} className={cn('label-caps', hideLabel && 'sr-only')}>
        {label}
      </label>
      <div className="relative flex items-center">
        <Search
          size={14}
          className="pointer-events-none absolute left-3 text-nike-faint"
          aria-hidden="true"
        />
        <input
          id={id}
          type="search"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="field-control w-full pl-9"
        />
      </div>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────────
   Chip de filtro activo
   ──────────────────────────────────────────────────────────────────────────── */

export interface FilterChipProps {
  /** Dimensión filtrada, ej. "Franchise". */
  label: string
  /** Valor aplicado. */
  value: string
  onClear: () => void
  className?: string
}

/** Chip removible que hace visible un filtro activo (ej. franchise elegida). */
export function FilterChip({ label, value, onClear, className }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClear}
      aria-label={`Quitar filtro ${label}: ${value}`}
      className={cn(
        'mb-0.5 inline-flex items-center gap-1.5 rounded-pill border border-nike-red/30 bg-nike-red/5 px-3 py-1.5',
        'text-xs font-semibold text-nike-red transition-colors duration-fast hover:bg-nike-red/10',
        className,
      )}
    >
      <span className="opacity-70">{label}:</span>
      <span className="max-w-[14rem] truncate">{value}</span>
      <X size={13} aria-hidden="true" />
    </button>
  )
}
