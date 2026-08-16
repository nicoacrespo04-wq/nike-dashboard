import type { MarketSignal, MomentumResponse } from '@/types/intelligence'
import { dec, num } from '@/lib/format'
import { deltaHint, formatDelta, formatMagnitude, unitLabel } from '@/lib/intelligence/units'
import { entityLabel, signalTypeLabel } from '@/components/charts/palette'
import { EmptyState, InfoTip, MeterBar } from '@/components/ui'

/**
 * Momentum de marcas, franquicias y retailers.
 *
 * Qué estaba roto y por qué se rehízo entera:
 *
 *  1. **Mezclaba familias que no son comparables.** En la misma grilla convivían
 *     `Share of shelf` (un porcentaje de surtido, con delta en PUNTOS
 *     PORCENTUALES) y `Momentum editorial` (un score 0..100, con delta en RATIO:
 *     0,62 = +62%). Ordenar o comparar esas filas entre sí no significa nada.
 *     Ahora van en bloques separados por `signal_family`.
 *  2. **No decía la unidad de ningún número.** Un "-7,7" y un "0,62" en la misma
 *     columna Δ son cosas distintas y se veían igual. Cada bloque declara la
 *     unidad de su valor y de su variación, y cada celda la respeta.
 *  3. **Mostraba identificadores.** "Retailer #5", "Producto #17",
 *     "Franquicia #1080". El backend ahora resuelve `entity_label` con el nombre
 *     real; el respaldo con el id queda sólo si no pudo resolverlo.
 *  4. **Titulaba "VOLUMEN" una columna que era un score.** El volumen absoluto
 *     (menciones, notas, reviews) es otra cosa y ahora tiene su propia columna,
 *     con la unidad que corresponda y el motivo cuando no está disponible.
 *
 * Server Component: no tiene interacción, así que no viaja al browser como JS.
 */
export default function MomentumTable({ data }: { data: MomentumResponse }) {
  const items = data.items ?? []

  if (items.length === 0) {
    return (
      <EmptyState
        title="Sin señales de momentum"
        description="La tabla market_signals está vacía para Argentina, o la ventana elegida no tiene señales."
        size="sm"
      />
    )
  }

  // Una tabla por familia: `momentum` y `shelf` no comparten escala ni unidad.
  const families = new Map<string, MarketSignal[]>()
  for (const signal of items) {
    const key = signal.signal_family ?? 'other'
    const list = families.get(key) ?? []
    list.push(signal)
    families.set(key, list)
  }

  const order = ['momentum', 'shelf', 'other']
  const blocks = [...families.entries()].sort(
    (a, b) => familyRank(a[0], order) - familyRank(b[0], order),
  )

  return (
    <div className="space-y-5">
      {blocks.map(([family, rows]) => (
        <FamilyBlock key={family} family={family} rows={rows} />
      ))}
    </div>
  )
}

function familyRank(family: string, order: string[]): number {
  const index = order.indexOf(family)
  return index === -1 ? order.length : index
}

const FAMILY_META: Record<string, { title: string; blurb: string }> = {
  momentum: {
    title: 'Momentum de conversación',
    blurb:
      'Score 0..100 de cuánto se habla de cada entidad. La variación es relativa al período anterior.',
  },
  shelf: {
    title: 'Presencia en góndola',
    blurb:
      'Porcentaje del surtido del retailer. La variación son puntos porcentuales, no un porcentaje de cambio.',
  },
  other: {
    title: 'Otras señales',
    blurb: 'Señales producidas por otro módulo: el backend no declaró su unidad.',
  },
}

function FamilyBlock({ family, rows }: { family: string; rows: MarketSignal[] }) {
  const meta = FAMILY_META[family] ?? FAMILY_META['other']
  const first = rows[0]
  const valueUnit = first?.value_unit ?? null
  const deltaUnit = first?.delta_unit ?? null
  const hint = deltaHint(deltaUnit)

  // La barra compara SÓLO dentro de la familia, que es donde la escala existe.
  const maxValue = Math.max(...rows.map((s) => Math.abs(s.value ?? 0)), 1)

  return (
    <section>
      <div className="mb-1.5">
        <h4 className="flex items-center gap-1.5 text-xs font-bold text-nike-ink">
          {meta?.title}
          <span className="tabular text-2xs font-medium text-nike-muted">({rows.length})</span>
        </h4>
        <p className="text-2xs leading-relaxed text-nike-muted">{meta?.blurb}</p>
      </div>

      <div className="nike-table-wrap">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-surface-border text-left text-label uppercase text-nike-muted">
              <th className="py-1.5 pr-3 font-semibold">Entidad</th>
              <th className="py-1.5 pr-3 font-semibold">Señal</th>
              <th className="py-1.5 pr-3 font-semibold">
                {first?.value_label ?? 'Valor'}
                {valueUnit && (
                  <span className="ml-1 font-normal normal-case text-nike-faint">
                    ({unitLabel(valueUnit)})
                  </span>
                )}
              </th>
              <th className="py-1.5 pr-3 text-right font-semibold">
                <span className="inline-flex items-center gap-1">
                  Δ
                  {deltaUnit && (
                    <span className="font-normal normal-case text-nike-faint">
                      ({unitLabel(deltaUnit)})
                    </span>
                  )}
                  {hint && <InfoTip label="Unidad de la variación" content={hint} side="left" />}
                </span>
              </th>
              <th className="py-1.5 text-right font-semibold">Volumen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {rows.slice(0, 15).map((s) => (
              <MomentumRow key={s.id} signal={s} maxValue={maxValue} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function MomentumRow({ signal: s, maxValue }: { signal: MarketSignal; maxValue: number }) {
  // El nombre real. `entityLabel` sólo entra si el backend no pudo resolverlo.
  const name = s.entity_label ?? entityLabel(s.entity_type, s.entity_id)
  const deltaShown = s.delta_available !== false
  const up = (s.delta ?? 0) >= 0

  return (
    <tr>
      <td className="py-1.5 pr-3">
        <span className="block font-semibold text-nike-ink">{name}</span>
        <span className="text-2xs text-nike-muted">
          {s.entity_type_label ?? s.entity_type}
          {s.entity_label_resolved === false ? ' · sin nombre en el catálogo' : ''}
        </span>
      </td>

      <td className="py-1.5 pr-3 text-nike-ink-soft">
        <span className="inline-flex items-center gap-1">
          {s.signal_label ?? signalTypeLabel(s.signal_type)}
          {s.signal_description && (
            <InfoTip
              label={`Qué mide ${s.signal_label ?? s.signal_type}`}
              content={s.signal_description}
              size={11}
            />
          )}
        </span>
      </td>

      <td className="w-32 py-1.5 pr-3">
        <MeterBar value={Math.abs(s.value ?? 0)} max={maxValue} color="#2A78D6" height={6} />
        <span className="tabular text-[10px] text-nike-muted">
          {formatMagnitude(s.value, s.value_unit)}
        </span>
      </td>

      <td className="tabular py-1.5 pr-3 text-right">
        {deltaShown ? (
          <span className="font-bold" style={{ color: up ? '#0A6B0A' : '#8E2020' }}>
            <span aria-hidden="true">{up ? '▲' : '▼'}</span>{' '}
            {formatDelta(s.delta, s.delta_unit, s.delta_pct)}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-2xs italic text-nike-muted">
            sin base
            {s.delta_reason && (
              <InfoTip label="Por qué no hay variación" content={s.delta_reason} size={11} />
            )}
          </span>
        )}
      </td>

      <td className="tabular py-1.5 text-right">
        {s.volume_available && s.volume !== null && s.volume !== undefined ? (
          <>
            <span className="font-semibold text-nike-ink">{num(s.volume)}</span>{' '}
            <span className="text-[10px] text-nike-muted">{s.volume_unit}</span>
            {s.volume_previous !== null && s.volume_previous !== undefined && (
              <span className="block text-[10px] text-nike-muted">
                antes {num(s.volume_previous)}
              </span>
            )}
          </>
        ) : (
          <span className="inline-flex items-center gap-1 text-2xs italic text-nike-muted">
            {s.value_unit === 'pct' ? 'no aplica' : 'sin dato'}
            {s.volume_reason && (
              <InfoTip label="Por qué no hay volumen" content={s.volume_reason} size={11} />
            )}
          </span>
        )}
      </td>
    </tr>
  )
}

/**
 * Resumen de la aceleración, para el pie del panel: cuántas señales tienen
 * derivada segunda y cuántas no, sin fingir un cero donde falta un período.
 */
export function MomentumFooter({ items }: { items: MarketSignal[] }) {
  const withAcceleration = items.filter((s) => s.acceleration_available !== false)
  if (items.length === 0) return null

  const missing = items.length - withAcceleration.length
  const reason = items.find((s) => s.acceleration_available === false)?.acceleration_reason

  return (
    <p className="mt-3 flex items-center gap-1 border-t border-surface-border pt-2 text-2xs text-nike-muted">
      {withAcceleration.length > 0
        ? `${dec(withAcceleration.length, 0)} señal(es) con aceleración calculada`
        : 'Ninguna señal tiene aceleración calculada'}
      {missing > 0 && ` · ${missing} sin ella`}
      {missing > 0 && reason && (
        <InfoTip label="Por qué falta la aceleración" content={reason} size={11} />
      )}
    </p>
  )
}
