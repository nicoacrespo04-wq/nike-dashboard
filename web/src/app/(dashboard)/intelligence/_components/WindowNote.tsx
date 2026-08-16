import { AlertTriangle, CalendarRange } from 'lucide-react'
import type { ComparisonWindow } from '@/types/intelligence'
import { period } from '@/lib/format'

/**
 * Qué período se está mirando y contra cuál se compara.
 *
 * Cuando el histórico no alcanza para la ventana pedida, el backend responde
 * `available: false` con el motivo y NO publica variaciones: preferir el silencio
 * a comparar contra cero es una decisión de producto. La UI tiene que decirlo —
 * si no, la pantalla se ve simplemente vacía y el usuario concluye que no hay
 * datos, que es una conclusión distinta y falsa.
 */
export default function WindowNote({
  window: win,
  recomputed,
}: {
  window: ComparisonWindow | null | undefined
  /** `true` si la ventana se recalculó en memoria (no es la persistida). */
  recomputed?: boolean
}) {
  if (!win) return null

  const current = period(win.current?.[0] ?? null, win.current?.[1] ?? null)
  const previous = period(win.previous?.[0] ?? null, win.previous?.[1] ?? null)

  if (!win.available) {
    return (
      <div
        role="status"
        className="flex items-start gap-2 rounded-lg border border-[#F0D7A0] bg-[#FFF9EC] px-3 py-2"
      >
        <AlertTriangle size={14} className="mt-px flex-shrink-0 text-[#8A5A00]" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-2xs font-bold text-[#8A5A00]">
            Sin historia suficiente para comparar contra el {win.label}
          </p>
          <p className="mt-0.5 text-2xs leading-relaxed text-nike-ink-soft">{win.reason}</p>
          <p className="tabular mt-1 text-[10px] text-nike-muted">
            Período pedido {current} · comparación {previous}
          </p>
        </div>
      </div>
    )
  }

  return (
    <p className="tabular flex flex-wrap items-center gap-1.5 text-2xs text-nike-muted">
      <CalendarRange size={12} aria-hidden="true" />
      <span>
        Período <span className="font-semibold text-nike-ink-soft">{current}</span> comparado contra
        el {win.label} <span className="font-semibold text-nike-ink-soft">{previous}</span>
      </span>
      {recomputed && (
        <span className="rounded-pill border border-surface-border px-1.5 py-px text-[9px] uppercase tracking-wide">
          recalculado
        </span>
      )}
      {!win.acceleration_available && (
        <span className="text-nike-muted">· sin aceleración: falta un tercer período</span>
      )}
    </p>
  )
}
