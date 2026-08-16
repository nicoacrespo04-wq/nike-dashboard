import Link from 'next/link'
import { fetchProduct, fetchProductMatches } from '@/lib/intelligence/server'
import { Card, EmptyState } from '@/components/ui'
import ProductDetailView from '../../_components/ProductDetailView'
import ServerError from '../../_components/ServerError'

/**
 * Ficha de producto — Server Component.
 *
 * Antes eran dos `useApi` en paralelo desde el browser. Ahora las dos consultas
 * salen del servidor a la vez y la página llega con el contenido adentro; el
 * detalle se cachea 120s, que es la ventana entre corridas del pipeline.
 */
export const dynamic = 'force-dynamic'

export default async function ProductDetailPage({ params }: { params: { id: string } }) {
  const productId = Number(params.id)
  const valid = Number.isFinite(productId) && productId > 0

  const [product, matches] = valid
    ? await Promise.all([
        fetchProduct(productId),
        fetchProductMatches(productId, { limit: 5, with_factors: true }),
      ])
    : [null, null]

  return (
    <div>
      <Link
        href="/intelligence/products"
        className="mb-3 inline-block text-2xs font-semibold text-nike-red hover:underline"
      >
        ← Volver al Product Explorer
      </Link>

      {product === null || (!product.ok && product.status === 404) ? (
        <Card>
          <EmptyState
            title="Producto no encontrado"
            description="El id solicitado no existe en el catálogo cargado."
          />
        </Card>
      ) : !product.ok ? (
        <Card>
          <ServerError description={product.error} />
        </Card>
      ) : (
        <ProductDetailView
          product={product.data}
          matches={matches !== null && matches.ok ? matches.data.matches : []}
        />
      )}
    </div>
  )
}
