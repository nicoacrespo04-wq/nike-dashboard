'use client'

/**
 * Triaje de una oportunidad: descartar, posponer, asignar, resolver, reabrir.
 *
 * Por qué existe: el Opportunity Center lista decenas de oportunidades y sin
 * una acción encima la pantalla se vuelve ruido en dos semanas. Acá está la
 * diferencia entre un reporte lindo y una herramienta que el equipo abre todos
 * los lunes.
 *
 * Identidad: el triaje NO se guarda contra `opportunity.id` — el pipeline borra
 * y recalcula las oportunidades en cada corrida y ese id no sobrevive. Se
 * guarda contra `entity_key`, el hash estable que devuelve el backend
 * (`app/services/triage.py`). Si una oportunidad llega sin `entity_key`, el
 * triaje no se puede anclar a nada y se dice explícitamente en vez de mostrar
 * botones que van a fallar.
 *
 * Feedback optimista: el estado cambia en pantalla apenas se hace click y se
 * revierte al valor anterior si la API falla, con el error del backend a la
 * vista. Nunca se queda mintiendo.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  AlertCircle,
  Ban,
  CalendarClock,
  Check,
  CheckCircle2,
  Loader2,
  RotateCcw,
  UserRound,
} from 'lucide-react'
import { Badge, EmptyState, Tooltip } from '@/components/ui'
import type { BadgeColors } from '@/components/ui'
import { cn } from '@/lib/utils'

// ── contrato con el backend ─────────────────────────────────

export type TriageState = 'open' | 'snoozed' | 'dismissed' | 'resolved'

/** Fila de `opportunity_triage` tal como la devuelve `/api/triage`. */
export interface TriageRecord {
  entity_key: string
  state: TriageState
  assignee: string | null
  note: string | null
  snooze_until: string | null
  first_seen_at: string | null
  updated_at: string | null
  updated_by: string | null
  /** `true` mientras siga pidiendo atención del equipo (o sea: `open`). */
  actionable: boolean
  /** `true` si no hay fila persistida: nadie la tocó todavía. */
  default: boolean
}

/** Cuerpo del POST de transición. */
export interface TriageUpdate {
  state: TriageState
  assignee?: string
  note?: string
  snooze_until?: string
  updated_by?: string
}

export const TRIAGE_STATES: readonly TriageState[] = ['open', 'snoozed', 'dismissed', 'resolved']

/**
 * Transiciones permitidas. Espejo EXACTO de `TRANSITIONS` en
 * `backend/app/services/triage.py`: la UI no ofrece botones que el backend va a
 * rechazar con 422. Desde `dismissed`/`resolved` lo único que se puede hacer es
 * reabrir — descartar y resolver son decisiones, no estados de paso.
 */
export const TRIAGE_TRANSITIONS: Record<TriageState, readonly TriageState[]> = {
  open: ['open', 'snoozed', 'dismissed', 'resolved'],
  snoozed: ['open', 'snoozed', 'dismissed', 'resolved'],
  dismissed: ['open', 'dismissed'],
  resolved: ['open', 'resolved'],
}

interface TriageStyle {
  label: string
  description: string
  colors: BadgeColors
  /** Atenúa la tarjeta: lo triajeado tiene que verse distinto de lo pendiente. */
  muted: boolean
}

export const TRIAGE_STYLES: Record<TriageState, TriageStyle> = {
  open: {
    label: 'Abierta',
    description: 'Pendiente de decisión: nadie la trabajó todavía.',
    colors: { text: '#1F1F1D', bg: '#F3F3F1', border: '#DCDCD6' },
    muted: false,
  },
  snoozed: {
    label: 'Pospuesta',
    description: 'Fuera de la bandeja hasta la fecha elegida. Después vuelve sola.',
    colors: { text: '#8A5A00', bg: '#FFF4DE', border: '#F0D7A0' },
    muted: true,
  },
  dismissed: {
    label: 'Descartada',
    description: 'No es accionable. Queda registrada, pero no pide más atención.',
    colors: { text: '#5C5C57', bg: '#EDEDEA', border: '#D6D6D0' },
    muted: true,
  },
  resolved: {
    label: 'Resuelta',
    description: 'Ya se actuó sobre esta oportunidad.',
    colors: { text: '#1D6B3F', bg: '#E4F3EA', border: '#B8DCC6' },
    muted: true,
  },
}

/** Estado de una oportunidad que nadie triajeó: `open` virtual, sin fila en la base. */
export function defaultTriage(entityKey: string): TriageRecord {
  return {
    entity_key: entityKey,
    state: 'open',
    assignee: null,
    note: null,
    snooze_until: null,
    first_seen_at: null,
    updated_at: null,
    updated_by: null,
    actionable: true,
    default: true,
  }
}

// ── cliente HTTP (siempre por el proxy, nunca al :8000) ─────

const TRIAGE_PATH = '/api/intelligence/triage'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function isTriageState(value: unknown): value is TriageState {
  return typeof value === 'string' && (TRIAGE_STATES as readonly string[]).includes(value)
}

/** Mensaje de error del backend (`{detail}` de FastAPI o del proxy). */
function detailOf(payload: unknown): string | null {
  if (!isRecord(payload)) return null
  const detail = payload.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const first = detail.find(isRecord)
    return first ? asString(first.msg) : null
  }
  return null
}

function toTriageRecord(payload: unknown, entityKey: string): TriageRecord | null {
  if (!isRecord(payload) || !isTriageState(payload.state)) return null
  return {
    entity_key: asString(payload.entity_key) ?? entityKey,
    state: payload.state,
    assignee: asString(payload.assignee),
    note: asString(payload.note),
    snooze_until: asString(payload.snooze_until),
    first_seen_at: asString(payload.first_seen_at),
    updated_at: asString(payload.updated_at),
    updated_by: asString(payload.updated_by),
    actionable: payload.actionable === true || payload.state === 'open',
    default: payload.default === true,
  }
}

export async function saveTriage(entityKey: string, update: TriageUpdate): Promise<TriageRecord> {
  let response: Response
  try {
    response = await fetch(`${TRIAGE_PATH}/${encodeURIComponent(entityKey)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(update),
    })
  } catch {
    throw new Error('No se pudo contactar el dashboard. Revisá la conexión y volvé a intentar.')
  }

  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    throw new Error(
      detailOf(payload) ??
        `No se pudo guardar el triaje (error ${response.status}). Verificá que el router de triaje esté registrado en el backend.`,
    )
  }

  const record = toTriageRecord(payload, entityKey)
  if (!record) throw new Error('El backend devolvió una respuesta de triaje inesperada.')
  return record
}

// ── helpers de fecha ────────────────────────────────────────

function isoDay(offsetDays: number): string {
  const day = new Date()
  day.setHours(12, 0, 0, 0) // mediodía: inmune al huso al pasar a ISO
  day.setDate(day.getDate() + offsetDays)
  return day.toISOString().slice(0, 10)
}

/** `2026-09-01 23:59:59` → `1 sep 2026`. Sin fecha, `null`. */
function formatDay(value: string | null): string | null {
  if (!value) return null
  const parsed = new Date(value.replace(' ', 'T'))
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString('es-AR', { day: 'numeric', month: 'short', year: 'numeric' })
}

// ── badge ───────────────────────────────────────────────────

export function TriageBadge({ triage }: { triage: TriageRecord }) {
  const style = TRIAGE_STYLES[triage.state]
  const until = formatDay(triage.snooze_until)
  const title =
    triage.state === 'snoozed' && until
      ? `${style.description} Vuelve el ${until}.`
      : style.description

  return (
    <Badge colors={style.colors} title={title}>
      {style.label}
      {triage.state === 'snoozed' && until ? ` · ${until}` : ''}
    </Badge>
  )
}

// ── controles ───────────────────────────────────────────────

type Panel = 'none' | 'snooze' | 'assign'

export interface TriageControlsProps {
  /** Hash estable de la oportunidad. Sin esto no hay dónde anclar el triaje. */
  entityKey: string | null | undefined
  /** Estado actual según el backend. `null` = nadie la tocó (`open`). */
  triage?: TriageRecord | null
  /** Se llama con la fila ya confirmada por el backend. */
  onChange?: (record: TriageRecord) => void
  /** Quién está operando (auditoría). */
  updatedBy?: string
  className?: string
}

export function TriageControls({
  entityKey,
  triage,
  onChange,
  updatedBy,
  className,
}: TriageControlsProps) {
  const [record, setRecord] = useState<TriageRecord>(
    () => triage ?? defaultTriage(entityKey ?? ''),
  )
  const [panel, setPanel] = useState<Panel>('none')
  const [snoozeDate, setSnoozeDate] = useState<string>(() => isoDay(14))
  const [assignee, setAssignee] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // El padre manda: si recarga la lista, el estado local se realinea. Mientras
  // hay un guardado en vuelo no se pisa, para no romper el feedback optimista.
  useEffect(() => {
    if (saving) return
    setRecord(triage ?? defaultTriage(entityKey ?? ''))
    setAssignee(triage?.assignee ?? '')
    // `saving` a propósito fuera de las deps: sólo decide si este sync corre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triage, entityKey])

  const apply = useCallback(
    async (update: TriageUpdate, optimistic: Partial<TriageRecord>) => {
      if (!entityKey || saving) return
      const previous = record
      setRecord({ ...record, ...optimistic, default: false })
      setError(null)
      setSaving(true)
      setPanel('none')
      try {
        const saved = await saveTriage(entityKey, { updated_by: updatedBy, ...update })
        setRecord(saved)
        onChange?.(saved)
      } catch (cause) {
        setRecord(previous) // reversión: la UI nunca miente sobre lo que se guardó
        setError(cause instanceof Error ? cause.message : 'Error inesperado al guardar el triaje.')
      } finally {
        setSaving(false)
      }
    },
    [entityKey, onChange, record, saving, updatedBy],
  )

  if (!entityKey) {
    return (
      <div className={cn('border-t border-surface-border px-4 py-2', className)}>
        <EmptyState
          size="sm"
          icon={<AlertCircle size={16} strokeWidth={1.8} />}
          title="Triaje no disponible"
          description="Esta oportunidad llegó sin `entity_key`, así que no hay identidad estable donde guardar la decisión. Registrá el router de triaje y exponé `entity_key` en /api/opportunities."
        />
      </div>
    )
  }

  const allowed = TRIAGE_TRANSITIONS[record.state]
  const can = (state: TriageState) => allowed.includes(state)
  const style = TRIAGE_STYLES[record.state]

  return (
    <div className={cn('border-t border-surface-border bg-white px-4 py-3', className)}>
      {/* Estado actual + quién lo tiene */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="label-caps">Triaje</span>
        <TriageBadge triage={record} />
        {record.assignee && (
          <Badge tone="neutral" title="Responsable de esta oportunidad">
            <UserRound size={11} aria-hidden="true" />
            {record.assignee}
          </Badge>
        )}
        {saving && (
          <span className="flex items-center gap-1 text-2xs text-nike-muted" role="status">
            <Loader2 size={11} className="animate-spin" aria-hidden="true" />
            Guardando…
          </span>
        )}
      </div>

      {record.note && (
        <p className="mb-2 border-l-2 border-surface-border pl-2 text-2xs italic leading-relaxed text-nike-ink-soft">
          {record.note}
        </p>
      )}

      {/* Acciones */}
      <div className="flex flex-wrap items-center gap-1.5">
        {can('dismissed') && record.state !== 'dismissed' && (
          <TriageButton
            label="Descartar"
            hint="No es accionable. Sale de la bandeja pero queda registrada."
            icon={<Ban size={12} aria-hidden="true" />}
            disabled={saving}
            onClick={() => apply({ state: 'dismissed' }, { state: 'dismissed', actionable: false })}
          />
        )}

        {can('snoozed') && (
          <TriageButton
            label={record.state === 'snoozed' ? 'Cambiar fecha' : 'Posponer'}
            hint="La saca de la bandeja hasta la fecha elegida; después vuelve sola a abierta."
            icon={<CalendarClock size={12} aria-hidden="true" />}
            disabled={saving}
            active={panel === 'snooze'}
            onClick={() => setPanel(panel === 'snooze' ? 'none' : 'snooze')}
          />
        )}

        {can('resolved') && record.state !== 'resolved' && (
          <TriageButton
            label="Resuelta"
            hint="Ya se actuó sobre esta oportunidad."
            icon={<CheckCircle2 size={12} aria-hidden="true" />}
            disabled={saving}
            onClick={() => apply({ state: 'resolved' }, { state: 'resolved', actionable: false })}
          />
        )}

        <TriageButton
          label={record.assignee ? 'Reasignar' : 'Asignar'}
          hint="Deja registrado quién se hace cargo. Vacío = sin responsable."
          icon={<UserRound size={12} aria-hidden="true" />}
          disabled={saving}
          active={panel === 'assign'}
          onClick={() => setPanel(panel === 'assign' ? 'none' : 'assign')}
        />

        {record.state !== 'open' && can('open') && (
          <TriageButton
            label="Reabrir"
            hint="Vuelve a la bandeja como pendiente."
            icon={<RotateCcw size={12} aria-hidden="true" />}
            disabled={saving}
            onClick={() => apply({ state: 'open' }, { state: 'open', actionable: true, snooze_until: null })}
          />
        )}
      </div>

      {/* Posponer: selector de fecha */}
      {panel === 'snooze' && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-surface-muted p-2">
          <label className="text-2xs font-semibold text-nike-ink-soft" htmlFor={`snooze-${entityKey}`}>
            Volver a mostrar el
          </label>
          <input
            id={`snooze-${entityKey}`}
            type="date"
            value={snoozeDate}
            min={isoDay(1)}
            onChange={(event) => setSnoozeDate(event.target.value)}
            className="field-control px-2 py-1 text-xs"
          />
          <TriageButton
            label="Posponer"
            icon={<Check size={12} aria-hidden="true" />}
            disabled={saving || !snoozeDate}
            primary
            onClick={() =>
              apply(
                { state: 'snoozed', snooze_until: snoozeDate },
                { state: 'snoozed', actionable: false, snooze_until: snoozeDate },
              )
            }
          />
        </div>
      )}

      {/* Asignar */}
      {panel === 'assign' && (
        <form
          className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-surface-muted p-2"
          onSubmit={(event) => {
            event.preventDefault()
            const value = assignee.trim()
            apply(
              { state: record.state, assignee: value },
              { assignee: value.length > 0 ? value : null },
            )
          }}
        >
          <label className="text-2xs font-semibold text-nike-ink-soft" htmlFor={`assignee-${entityKey}`}>
            Responsable
          </label>
          <input
            id={`assignee-${entityKey}`}
            type="text"
            value={assignee}
            placeholder="nombre o equipo"
            onChange={(event) => setAssignee(event.target.value)}
            className="field-control px-2 py-1 text-xs"
          />
          <TriageButton label="Guardar" icon={<Check size={12} aria-hidden="true" />} disabled={saving} primary submit />
        </form>
      )}

      {error && (
        <p role="alert" className="mt-2 flex items-start gap-1 text-2xs leading-relaxed text-nike-red">
          <AlertCircle size={12} className="mt-px shrink-0" aria-hidden="true" />
          {error} <span className="text-nike-muted">Se restauró el estado anterior.</span>
        </p>
      )}

      {record.updated_at && !record.default && (
        <p className="mt-2 text-2xs text-nike-muted">
          {style.label} · última actualización {formatDay(record.updated_at)}
          {record.updated_by ? ` por ${record.updated_by}` : ''}
          {record.first_seen_at ? ` · en seguimiento desde ${formatDay(record.first_seen_at)}` : ''}
        </p>
      )}
    </div>
  )
}

// ── botón interno ───────────────────────────────────────────

interface TriageButtonProps {
  label: string
  icon: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  active?: boolean
  primary?: boolean
  submit?: boolean
  hint?: string
}

function TriageButton({
  label,
  icon,
  onClick,
  disabled,
  active,
  primary,
  submit,
  hint,
}: TriageButtonProps) {
  const button = (
    <button
      type={submit ? 'submit' : 'button'}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn(
        'inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-2xs font-semibold',
        'transition-colors duration-fast disabled:cursor-not-allowed disabled:opacity-50',
        primary
          ? 'border-nike-black bg-nike-black text-white hover:bg-nike-ink'
          : 'border-surface-border-strong bg-white text-nike-ink-soft hover:border-nike-black hover:text-nike-ink',
        active && !primary && 'border-nike-black text-nike-ink',
      )}
    >
      {icon}
      {label}
    </button>
  )

  if (!hint) return button
  return (
    <Tooltip content={hint} side="top">
      {button}
    </Tooltip>
  )
}
