import { Suspense } from 'react'
import { fetchProductFilters, fetchProducts } from '@/lib/intelligence/server'
import { PageIntro } from '@/components/ui'
import ProductExplorer from '../_components/ProductExplorer'
import { productQueryFrom, productStateFromParams } from '../_components/productParams'
import { ProductExplorerSkeleton } from '../_components/skeletons'

/**
 * Product Explorer — mitad servidor.
 *
 * Resuelve dos cosas antes de mandar nada al browser:
 *  1. Las opciones de los selects (`/products/filters`), que cambian sólo
 *     cuando vuelve a correr el pipeline y se cachean 10 minutos.
 *  2. La primera página del catálogo, ya filtrada y paginada por el backend
 *     según los query params de la URL.
 *
 * El componente cliente recibe esa página como semilla: mientras el usuario no
 * toque un filtro, no hay una sola request desde el browser.
 *
 * El encabezado se pinta fuera del `Suspense`, así la pantalla nunca aparece
 * en blanco; adentro va lo que depende del backend, con su silueta de carga.
 */
export const dynamic = 'force-dynamic'

export default function ProductExplorerPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  return (
    <div>
      <PageIntro
        question="¿Qué está pasando?"
        description="El catálogo normalizado y enriquecido: taxonomía inferida, banda de precio, ciclo de vida y atributos con su nivel de confianza. La catalogación es parte del valor, no un paso invisible."
      />
      <Suspense fallback={<ProductExplorerSkeleton />}>
        <ProductSection searchParams={searchParams} />
      </Suspense>
    </div>
  )
}

async function ProductSection({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  const state = productStateFromParams(searchParams)
  const [filters, products] = await Promise.all([
    fetchProductFilters(),
    fetchProducts(productQueryFrom(state)),
  ])

  return (
    <ProductExplorer
      initialState={state}
      filterOptions={filters.ok ? filters.data : null}
      initialData={products.ok ? products.data : null}
      initialError={products.ok ? null : products.error}
    />
  )
}
