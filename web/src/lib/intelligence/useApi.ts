"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/intelligence/api";
import { depsKeyOf } from "@/lib/intelligence/depsKey";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Hay datos en pantalla y se está pidiendo una versión nueva. */
  refreshing: boolean;
  reload: () => void;
}

export interface UseApiOptions<T> {
  enabled?: boolean;
  /**
   * Datos ya resueltos en el servidor (SSR) para `initialKey`.
   * Si la firma de `deps` coincide, el primer fetch del cliente se saltea:
   * la pantalla arranca con contenido y sin round-trip.
   */
  initialData?: T | null;
  /** Firma de `deps` con la que se resolvió `initialData`. */
  initialKey?: string;
}

/**
 * Hook único para consumir el cliente de API.
 *
 * Garantías:
 *  - Una sola request viva por hook: al cambiar las dependencias se aborta la
 *    anterior (`AbortController`) antes de disparar la nueva, así una ráfaga
 *    de tecleo no acumula respuestas fuera de orden.
 *  - `alive` corta además la escritura de estado de una promesa que ya no
 *    corresponde, incluso si el abort llega tarde.
 *  - Si viene `initialData` para la firma actual, no se pide nada: los datos
 *    del servidor son la primera pantalla.
 */
export function useApi<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown>,
  options: UseApiOptions<T> = {},
): AsyncState<T> {
  const { enabled = true, initialData = null, initialKey } = options;
  const key = depsKeyOf(deps);

  // Sólo la primera pasada puede aprovechar el SSR, y sólo si las deps son las
  // mismas con las que el servidor resolvió los datos.
  const seededRef = useRef(
    initialData !== null && initialKey !== undefined && initialKey === key,
  );
  const seeded = seededRef.current;

  const [data, setData] = useState<T | null>(seeded ? initialData : null);
  const [loading, setLoading] = useState<boolean>(enabled && !seeded);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  /** Hay datos en pantalla: el próximo pedido es un refresco, no una carga. */
  const hasDataRef = useRef(seeded);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setRefreshing(false);
      return;
    }
    if (seededRef.current) {
      // Primera pasada con datos del servidor: no se pide nada. Las siguientes
      // (cambio de filtro, `reload()`) sí van a la red.
      seededRef.current = false;
      return;
    }

    const controller = new AbortController();
    let alive = true;
    if (hasDataRef.current) setRefreshing(true);
    else setLoading(true);
    setError(null);

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (!alive) return;
        setData(result);
        hasDataRef.current = true;
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        if (cause instanceof ApiError) setError(cause.message);
        else if (cause instanceof Error) setError(cause.message);
        else setError("Error desconocido consultando el backend.");
      })
      .finally(() => {
        if (!alive) return;
        setLoading(false);
        setRefreshing(false);
      });

    return () => {
      alive = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, loading, error, refreshing, reload };
}

/**
 * Debounce para inputs de búsqueda.
 *
 * Vaciar el campo se propaga al instante: "limpiar el filtro" tiene que
 * sentirse inmediato, y devolver la lista completa nunca es una consulta más
 * cara que la que se estaba tipeando.
 */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    if (value === "" || value === null || value === undefined) {
      setDebounced(value);
      return;
    }
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
