import type { MarketSignal } from '@/types/intelligence'
import { dec, signed } from '@/lib/format'
import { entityLabel, signalTypeLabel } from '@/components/charts/palette'
import { EmptyState, MeterBar } from '@/components/ui'

/**
 * Tabla de momentum de marcas y franquicias.
 *
 * Server Component: no tiene interacción, así que no hay razón para mandar la
 * tabla al browser como JavaScript ni para volver a pedirla en cada visita.
 */
export default function MomentumTable({ items }: { items: MarketSignal[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin señales de momentum"
        description="La tabla market_signals está vacía para Argentina."
        size="sm"
      />
    )
  }

  const maxValue = Math.max(...items.map((s) => Math.abs(s.value ?? 0)), 1)
  return (
    <div className="nike-table-wrap">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-surface-border text-left text-label uppercase text-nike-muted">
            <th className="py-1.5 pr-3 font-semibold">Entidad</th>
            <th className="py-1.5 pr-3 font-semibold">Señal</th>
            <th className="py-1.5 pr-3 font-semibold">Volumen</th>
            <th className="py-1.5 pr-3 text-right font-semibold">Δ</th>
            <th className="py-1.5 text-right font-semibold">Aceleración</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-border">
          {items.slice(0, 15).map((s) => {
            const accel = s.acceleration ?? 0
            return (
              <tr key={s.id}>
                <td className="py-1.5 pr-3">
                  <span className="block font-semibold text-nike-ink">
                    {entityLabel(s.entity_type, s.entity_id)}
                  </span>
                  <span className="text-2xs text-nike-muted">{s.entity_type}</span>
                </td>
                <td className="py-1.5 pr-3 text-nike-ink-soft">{signalTypeLabel(s.signal_type)}</td>
                <td className="w-28 py-1.5 pr-3">
                  <MeterBar
                    value={Math.abs(s.value ?? 0)}
                    max={maxValue}
                    color="#2A78D6"
                    height={6}
                  />
                  <span className="tabular text-[10px] text-nike-muted">{dec(s.value, 2)}</span>
                </td>
                <td className="tabular py-1.5 pr-3 text-right text-nike-ink-soft">
                  {signed(s.delta, 2)}
                </td>
                <td
                  className="tabular py-1.5 text-right font-bold"
                  style={{ color: accel >= 0 ? '#0A6B0A' : '#8E2020' }}
                >
                  <span aria-hidden="true">{accel >= 0 ? '▲' : '▼'}</span> {signed(accel, 2)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
