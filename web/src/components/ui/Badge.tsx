import { cn, getBMLLabel, type BMLValue } from '@/lib/utils'
import Tooltip from './Tooltip'

export type BadgeTone =
  | 'neutral'
  | 'nike'
  | 'adidas'
  | 'puma'
  | 'beat'
  | 'meet'
  | 'lose'
  | 'nd'

/**
 * Colores explícitos para etiquetas cuyo color sale de la paleta de datos
 * (severidad, confianza, recomendación de retail media) y no de la paleta de
 * marca. Se pasan como estilo inline para no multiplicar clases utilitarias
 * por cada estado del motor.
 */
export interface BadgeColors {
  text: string
  bg: string
  border: string
}

export interface BadgeProps {
  children: React.ReactNode
  tone?: BadgeTone
  size?: 'sm' | 'md'
  /** Si viene, gana sobre `tone`. Ver `BadgeColors`. */
  colors?: BadgeColors
  className?: string
  title?: string
}

const TONE: Record<BadgeTone, string> = {
  neutral: 'badge-neutral',
  nike: 'badge-nike',
  adidas: 'badge-adidas',
  puma: 'badge-puma',
  beat: 'bml-beat',
  meet: 'bml-meet',
  lose: 'bml-lose',
  nd: 'bml-nd',
}

/** Badge consistente. Toda etiqueta corta de la app pasa por acá. */
export function Badge({
  children,
  tone = 'neutral',
  size = 'sm',
  colors,
  className,
  title,
}: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'badge',
        colors ? 'border' : TONE[tone],
        size === 'md' && 'text-[11px] px-2.5 py-1',
        className,
      )}
      style={
        colors
          ? { color: colors.text, backgroundColor: colors.bg, borderColor: colors.border }
          : undefined
      }
    >
      {children}
    </span>
  )
}

/** Mapea el nombre de marca a su tono de badge. */
export function brandTone(marca: string | null | undefined): BadgeTone {
  switch (marca?.toUpperCase()) {
    case 'NIKE': return 'nike'
    case 'ADIDAS': return 'adidas'
    case 'PUMA': return 'puma'
    default: return 'neutral'
  }
}

export interface BrandBadgeProps {
  marca: string | null | undefined
  size?: 'sm' | 'md'
  className?: string
}

/** Badge de marca con el color oficial de cada competidor. */
export function BrandBadge({ marca, size = 'sm', className }: BrandBadgeProps) {
  return (
    <Badge tone={brandTone(marca)} size={size} className={className}>
      {marca?.trim() || 'N/D'}
    </Badge>
  )
}

export interface BMLBadgeProps {
  value: BMLValue | null | undefined
  size?: 'sm' | 'md'
  /** Envuelve el badge en un tooltip que explica la sigla. */
  withTooltip?: boolean
  className?: string
}

/**
 * Badge BML.
 *
 * BEAT = Nike más barato (verde) · MEET = precio similar (naranja) ·
 * LOSE = Nike más caro (rojo) · N/D = sin comparable.
 *
 * El significado nunca queda librado sólo al color: el texto de la sigla y la
 * descripción lo explicitan (requisito de accesibilidad para daltonismo — el
 * par verde/naranja de esta paleta queda por debajo del umbral CVD).
 *
 * En tablas densas usar `withTooltip={false}`: el `title` nativo ya explica la
 * sigla sin agregar un tab stop por fila.
 */
export function BMLBadge({ value, size = 'sm', withTooltip = false, className }: BMLBadgeProps) {
  const raw = value?.toString().trim().toUpperCase()
  const known = raw === 'BEAT' || raw === 'MEET' || raw === 'LOSE'
  const label = known ? raw : 'N/D'
  const description = known
    ? getBMLLabel(label)
    : 'Sin precio comparable de Nike para este producto'

  const badge = (
    <Badge
      tone={known ? (raw!.toLowerCase() as BadgeTone) : 'nd'}
      size={size}
      title={description}
      className={className}
    >
      {label}
    </Badge>
  )

  if (!withTooltip) return badge

  return (
    <Tooltip content={description} side="left">
      <span tabIndex={0} className="inline-flex rounded">{badge}</span>
    </Tooltip>
  )
}

export default Badge
