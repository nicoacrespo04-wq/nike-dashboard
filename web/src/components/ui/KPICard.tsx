import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { cn } from '@/lib/utils'

interface KPICardProps {
  title: string
  value: string | number
  subtitle?: string
  trend?: number        // positivo = sube, negativo = baja
  trendLabel?: string
  color?: string
  icon?: React.ReactNode
  loading?: boolean
  className?: string
  valueSize?: 'sm' | 'md' | 'lg'
}

export default function KPICard({
  title,
  value,
  subtitle,
  trend,
  trendLabel,
  color,
  icon,
  loading = false,
  className,
  valueSize = 'lg',
}: KPICardProps) {
  if (loading) {
    return (
      <div className={cn('nike-card animate-pulse', className)}>
        <div className="h-3 bg-gray-200 rounded w-1/2 mb-4" />
        <div className="h-8 bg-gray-200 rounded w-2/3 mb-3" />
        <div className="h-3 bg-gray-200 rounded w-3/4" />
      </div>
    )
  }

  const valueSizeClass = {
    sm: 'text-2xl',
    md: 'text-3xl',
    lg: 'text-4xl',
  }[valueSize]

  return (
    <div className={cn('nike-card flex flex-col gap-2', className)}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          {title}
        </span>
        {icon && (
          <span className="text-gray-300">{icon}</span>
        )}
      </div>

      {/* Value */}
      <div
        className={cn('font-bold leading-none', valueSizeClass)}
        style={color ? { color } : { color: '#111111' }}
      >
        {value}
      </div>

      {/* Footer */}
      <div className="flex items-center gap-2 mt-1">
        {trend !== undefined && (
          <span
            className={cn(
              'flex items-center gap-0.5 text-xs font-semibold',
              trend > 0 ? 'text-green-600' : trend < 0 ? 'text-red-600' : 'text-gray-400'
            )}
          >
            {trend > 0 ? <TrendingUp size={12} /> : trend < 0 ? <TrendingDown size={12} /> : <Minus size={12} />}
            {Math.abs(trend).toFixed(1)}%
          </span>
        )}
        {subtitle && (
          <span className="text-xs text-gray-400">{subtitle}</span>
        )}
        {trendLabel && (
          <span className="text-xs text-gray-400">{trendLabel}</span>
        )}
      </div>
    </div>
  )
}
