/**
 * Cliente HTTP tipado y centralizado contra el backend del Decision Engine.
 *
 * Reglas:
 *  - Toda llamada al backend pasa por acá. Ninguna página hace `fetch` suelto.
 *  - Los errores se normalizan a `ApiError` para que la UI muestre siempre algo útil.
 *  - El backend responde 200 incluso con tablas vacías: los estados vacíos son
 *    un caso normal, no un error.
 */

import type {
  BrandInsightsResponse,
  HealthResponse,
  MatchDetail,
  MatchListResponse,
  MomentumResponse,
  OpportunityListResponse,
  OverviewResponse,
  ProductDetail,
  ProductFilters,
  ProductListResponse,
  ProductMatchesResponse,
  RetailMediaResponse,
  ScoringConfig,
  TopicsResponse,
} from "@/types";

export const API_BASE =
  (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;

  constructor(message: string, status: number, path: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue>;

function buildUrl(path: string, params?: QueryParams): string {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function request<T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> {
  const url = buildUrl(path, params);
  let response: Response;
  try {
    response = await fetch(url, { signal, headers: { Accept: "application/json" }, cache: "no-store" });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(
      `No se pudo contactar el backend en ${API_BASE}. ¿Está levantado? (uvicorn app.main:app --port 8000)`,
      0,
      path,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const raw = (body as { detail: unknown }).detail;
        if (typeof raw === "string") detail = raw;
      }
    } catch {
      /* respuesta sin cuerpo JSON: nos quedamos con el status */
    }
    throw new ApiError(detail, response.status, path);
  }

  return (await response.json()) as T;
}

// ── Sistema ─────────────────────────────────────────────────────────
export const getHealth = (signal?: AbortSignal) =>
  request<HealthResponse>("/api/health", undefined, signal);

export const getScoringConfig = (signal?: AbortSignal) =>
  request<ScoringConfig>("/api/config", undefined, signal);

// ── Overview ────────────────────────────────────────────────────────
export const getOverview = (params?: { country?: string; limit?: number }, signal?: AbortSignal) =>
  request<OverviewResponse>("/api/overview", params, signal);

// ── Productos ───────────────────────────────────────────────────────
export interface ProductQuery extends QueryParams {
  brand?: string;
  franchise?: string;
  category?: string;
  sport?: string;
  use_case?: string;
  gender?: string;
  price_band?: string;
  country?: string;
  retailer?: number;
  q?: string;
  limit?: number;
  offset?: number;
}

export const getProducts = (params?: ProductQuery, signal?: AbortSignal) =>
  request<ProductListResponse>("/api/products", params, signal);

export const getProductFilters = (signal?: AbortSignal) =>
  request<ProductFilters>("/api/products/filters", undefined, signal);

export const getProduct = (id: number, signal?: AbortSignal) =>
  request<ProductDetail>(`/api/products/${id}`, undefined, signal);

// ── Matches competitivos ────────────────────────────────────────────
export const getProductMatches = (
  id: number,
  params?: { limit?: number; with_factors?: boolean },
  signal?: AbortSignal,
) => request<ProductMatchesResponse>(`/api/products/${id}/matches`, params, signal);

export const getMatch = (id: number, signal?: AbortSignal) =>
  request<MatchDetail>(`/api/matches/${id}`, undefined, signal);

export const getMatches = (
  params?: { min_score?: number; limit?: number },
  signal?: AbortSignal,
) => request<MatchListResponse>("/api/matches", params, signal);

// ── Oportunidades ───────────────────────────────────────────────────
export interface OpportunityQuery extends QueryParams {
  family?: string;
  opportunity_type?: string;
  severity?: string;
  country?: string;
  min_importance?: number;
  limit?: number;
  offset?: number;
}

export const getOpportunities = (params?: OpportunityQuery, signal?: AbortSignal) =>
  request<OpportunityListResponse>("/api/opportunities", params, signal);

// ── Retail media ────────────────────────────────────────────────────
export interface RetailMediaQuery extends QueryParams {
  recommendation?: string;
  retailer?: number;
  min_score?: number;
  limit?: number;
  offset?: number;
}

export const getRetailMedia = (params?: RetailMediaQuery, signal?: AbortSignal) =>
  request<RetailMediaResponse>("/api/retail-media", params, signal);

// ── Brand intelligence ──────────────────────────────────────────────
export const getBrandInsights = (
  params?: { country?: string; dimension?: string; brand?: string; min_confidence?: string; limit?: number },
  signal?: AbortSignal,
) => request<BrandInsightsResponse>("/api/brand/insights", params, signal);

export const getBrandMomentum = (
  params?: { country?: string; signal_type?: string; entity_type?: string; limit?: number },
  signal?: AbortSignal,
) => request<MomentumResponse>("/api/brand/momentum", params, signal);

export const getBrandTopics = (
  params?: { country?: string; limit?: number },
  signal?: AbortSignal,
) => request<TopicsResponse>("/api/brand/topics", params, signal);
