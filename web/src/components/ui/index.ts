/**
 * Primitivas de UI compartidas.
 *
 * Importar desde acá: `import { EmptyState, SectionHeader } from '@/components/ui'`
 */

export { default as EmptyState } from './EmptyState'
export type { EmptyStateProps } from './EmptyState'

export { default as ErrorState } from './ErrorState'
export type { ErrorStateProps } from './ErrorState'

export { default as Skeleton, SkeletonText, SkeletonChart } from './Skeleton'
export type { SkeletonProps, SkeletonTextProps, SkeletonChartProps } from './Skeleton'

export { default as SectionHeader } from './SectionHeader'
export type { SectionHeaderProps } from './SectionHeader'

export { default as Tooltip, InfoTip } from './Tooltip'
export type { TooltipProps, InfoTipProps } from './Tooltip'

export {
  default as FilterBar,
  FilterSelect,
  SegmentedControl,
  FilterSearch,
  FilterChip,
} from './FilterBar'
export type {
  FilterBarProps,
  FilterOption,
  FilterSelectProps,
  SegmentedControlProps,
  FilterSearchProps,
  FilterChipProps,
} from './FilterBar'

export { default as Badge, BrandBadge, BMLBadge, brandTone } from './Badge'
export type { BadgeProps, BadgeTone, BrandBadgeProps, BMLBadgeProps } from './Badge'

export { default as KPICard } from './KPICard'
export type { KPICardProps, KPIDelta, DeltaDirection } from './KPICard'

export {
  ND,
  MAX_PLAUSIBLE_PRICE,
  isPlausiblePrice,
  isEmptyMetric,
  formatPriceSafe,
  formatPctSafe,
  formatCountSafe,
  formatTextSafe,
} from './format'
