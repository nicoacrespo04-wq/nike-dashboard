'use client'

import { useRouter } from 'next/navigation'
import { useTransition } from 'react'
import type { WindowKey } from '@/types/intelligence'
import { cn } from '@/lib/utils'
import { InfoTip } from '@/components/ui'
import { WINDOW_OPTIONS } from './brandParams'

/**
 * Contra qué período se compara toda la solapa.
 *
 * Hasta acá la comparación estaba clavada contra el mes anterior. Un mes es una
 * ventana útil para reaccionar, pero no para decidir surtido: una franquicia que
 * cae 5% en el mes puede estar 40% arriba en el año, y con una sola ventana esa
 * diferencia no existía en pantalla.
 *
 * Por qué navega en vez de mover estado local: la ventana cambia los TRES
 * paneles de la solapa (insights, momentum y tópicos) y dos de ellos se
 * resuelven en el servidor. Una navegación los vuelve a pedir a los tres con la
 * misma ventana; con estado local quedarían desalineados —el peor resultado
 * posible en una pantalla de comparación—.
 *
 * La URL de destino se arma desde `window.location`, no desde props: el resto de
 * los filtros de la solapa se sincronizan con `history.replaceState` (ver
 * `urlState.ts`), así que la URL viva es la única fuente confiable de lo que el
 * usuario tiene puesto.
 */
export default function WindowSelector({ value }: { value: WindowKey }) {
  const router = useRouter()
  const [pending, startTransition] = useTransition()

  const select = (next: WindowKey) => {
    if (next === value) return
    const url = new URL(window.location.href)
    url.searchParams.set('win', next)
    // Cambiar de ventana es empezar de nuevo la lectura: las tandas extra de
    // insights que se pidieron para la ventana anterior no aplican.
    url.searchParams.delete('batches')
    startTransition(() => router.replace(`${url.pathname}${url.search}`, { scroll: false }))
  }

  return (
    <div className="flex items-center gap-2" aria-busy={pending}>
      <span className="label-caps">Comparar contra</span>
      <div
        role="group"
        aria-label="Ventana de comparación"
        className="inline-flex overflow-hidden rounded-lg border border-surface-border-strong"
      >
        {WINDOW_OPTIONS.map((option) => {
          const active = option.value === value
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              disabled={pending}
              title={option.hint}
              onClick={() => select(option.value)}
              className={cn(
                'px-2.5 py-1 text-2xs font-semibold transition-colors duration-fast',
                'disabled:cursor-wait',
                active
                  ? 'bg-nike-black text-white'
                  : 'bg-white text-nike-ink-soft hover:bg-surface-muted',
              )}
            >
              {option.label}
            </button>
          )
        })}
      </div>
      <InfoTip
        label="Cómo funciona la ventana de comparación"
        side="left"
        content="El período actual se compara contra el inmediatamente anterior de la misma longitud. Si el histórico cargado no llega a cubrir los dos períodos, el motor no publica variaciones inventadas: lo dice y explica por qué."
      />
    </div>
  )
}
