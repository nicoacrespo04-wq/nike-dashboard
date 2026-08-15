import Link from 'next/link'
import type { ProductCard } from '@/types/intelligence'
import { money, text } from '@/lib/format'
import { Tag } from './badges'

/**
 * Representación compacta y reutilizable de un producto.
 * `role` pinta el borde: Nike (rojo de marca) vs competidor (negro).
 */
export function ProductLine({
  product,
  role = 'neutral',
  link = true,
  compact = false,
}: {
  product: ProductCard | null
  role?: 'nike' | 'competitor' | 'neutral'
  link?: boolean
  compact?: boolean
}) {
  if (!product) {
    return (
      <div className="rounded border border-dashed border-surface-border-strong px-3 py-2 text-2xs text-nike-muted">
        Producto no disponible
      </div>
    )
  }

  const accent =
    role === 'nike'
      ? 'border-l-nike-red'
      : role === 'competitor'
        ? 'border-l-nike-black'
        : 'border-l-surface-border-strong'

  const body = (
    <div className={`min-w-0 border-l-[3px] ${accent} pl-3`}>
      <p className="truncate text-2xs font-semibold uppercase tracking-wide text-nike-muted">
        {text(product.brand)}
      </p>
      <p className="truncate text-sm font-semibold leading-tight text-nike-ink">
        {text(product.product_name)}
      </p>
      {!compact && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {product.franchise && <Tag title="Franquicia">{product.franchise}</Tag>}
          {product.use_case && <Tag title="Use case">{product.use_case}</Tag>}
          {product.price_band && <Tag title="Banda de precio">{product.price_band}</Tag>}
          {product.msrp !== null && product.msrp !== undefined && (
            <Tag title="MSRP">{money(product.msrp)}</Tag>
          )}
        </div>
      )}
    </div>
  )

  if (!link) return body

  return (
    <Link
      href={`/intelligence/products/${product.id}`}
      className="block rounded transition-colors duration-fast hover:bg-surface-muted"
    >
      {body}
    </Link>
  )
}

/** "Nike Pegasus 41 vs ASICS Novablast 5": el enfrentamiento en una línea. */
export function VersusLine({
  nike,
  competitor,
}: {
  nike: ProductCard | null
  competitor: ProductCard | null
}) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
      <ProductLine product={nike} role="nike" compact link={false} />
      <span aria-hidden="true" className="text-xs font-bold text-nike-muted">
        vs
      </span>
      <ProductLine product={competitor} role="competitor" compact link={false} />
    </div>
  )
}
