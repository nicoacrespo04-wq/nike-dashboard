/**
 * Firma estable de las dependencias de una consulta.
 *
 * Vive en su propio módulo (sin `'use client'`) a propósito: la calculan tanto
 * el Server Component que resuelve la primera pantalla como el hook `useApi`
 * del browser. Si las dos firmas coinciden, el cliente sabe que ya tiene esos
 * datos y no repite el pedido.
 */
export function depsKeyOf(deps: ReadonlyArray<unknown>): string {
  return JSON.stringify(deps)
}
