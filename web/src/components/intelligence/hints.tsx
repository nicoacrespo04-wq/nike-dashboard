/**
 * Microcopy compartida sobre cómo poner en marcha el motor.
 *
 * Un estado vacío que no dice qué hacer es una pantalla rota con mejor
 * tipografía. Estas dos piezas se repiten en todas las pantallas de
 * INTELLIGENCE para que el vacío siempre venga con la salida.
 */

/** Comando que pobla la base y levanta la API. */
export const PIPELINE_COMMAND =
  'cd backend && python -m app.pipeline && uvicorn app.main:app --port 8000'

/** Comando en mono, pensado para el slot `action` de `EmptyState`. */
export function CommandHint({ command = PIPELINE_COMMAND }: { command?: string }) {
  return (
    <code className="block max-w-full overflow-x-auto rounded-lg border border-surface-border bg-surface-sunken px-3 py-1.5 text-left font-mono text-[10px] leading-relaxed text-nike-muted">
      {command}
    </code>
  )
}
