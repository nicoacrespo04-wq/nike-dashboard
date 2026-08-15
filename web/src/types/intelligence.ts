/**
 * Tipos del Competitive & Consumer Intelligence Decision Engine.
 * Espejo exacto de los contratos del backend (backend/CONTRACTS.md + app/schema.sql).
 * Regla: nada de `any`. Lo desconocido es `unknown` y se estrecha antes de usarse.
 */

// ── Primitivas del dominio ──────────────────────────────────────────
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type Confidence = "HIGH" | "MEDIUM" | "LOW";

export type FactorName =
  | "visual"
  | "semantic"
  | "price"
  | "retailer_overlap"
  | "editorial"
  | "social"
  | "reviews";

export type OpportunityFamily =
  | "pricing"
  | "assortment"
  | "distribution"
  | "stock"
  | "retail_media"
  | "competitive_threat"
  | "brand_momentum";

export type RetailMediaRecommendation =
  | "INVEST_IN_RETAIL_MEDIA"
  | "EVALUATE_PRICE_ACTION_BEFORE_MEDIA"
  | "DO_NOT_INCREASE_MEDIA"
  | "CAPTURE_COMPETITOR_STOCKOUT"
  | "PRIORITIZE_RETAIL_MEDIA_OVER_MARKDOWN";

export type BrandDimension =
  | "brand_perception"
  | "product_perception"
  | "price_perception"
  | "availability"
  | "cultural_relevance"
  | "consumer_intent";

// ── Catálogo ────────────────────────────────────────────────────────
export interface ProductCard {
  id: number;
  brand: string | null;
  product_name: string | null;
  franchise: string | null;
  use_case: string | null;
  category: string | null;
  price_band: string | null;
  msrp: number | null;
  image_url: string | null;
  lifecycle_stage: string | null;
}

/** Fila completa de `products` + marca (PRODUCT_CARD_SQL del backend). */
export interface Product {
  id: number;
  brand: string | null;
  is_focus: number | null;
  product_name: string;
  normalized_product_name: string | null;
  franchise: string | null;
  model: string | null;
  version: string | null;
  sku: string | null;
  style_code: string | null;
  category: string | null;
  subcategory: string | null;
  sport: string | null;
  activity: string | null;
  use_case: string | null;
  gender: string | null;
  age_segment: string | null;
  performance_vs_lifestyle: string | null;
  consumer_segment: string | null;
  lifecycle_stage: string | null;
  msrp: number | null;
  price_band: string | null;
  url: string | null;
  image_url: string | null;
  description: string | null;
  launch_date: string | null;
  country_code: string | null;
}

export interface ProductAttribute {
  attr_group: string | null;
  attr_name: string;
  value_text: string | null;
  value_num: number | null;
  confidence: number | null;
  source: string | null;
}

export interface PriceObservation {
  id: number;
  product_id: number;
  retailer_id: number;
  retailer_name: string | null;
  channel: string | null;
  importance: number | null;
  observed_at: string | null;
  full_price: number | null;
  current_price: number | null;
  discount_pct: number | null;
  currency: string | null;
}

export interface StockObservation {
  id: number;
  product_id: number;
  retailer_id: number;
  retailer_name: string | null;
  observed_at: string | null;
  in_stock: number | null;
  availability_pct: number | null;
  sizes_available: number | null;
  sizes_total: number | null;
}

export interface ReviewRow {
  rating: number | null;
  review_count: number | null;
  review_text: string | null;
  source: string | null;
  observed_at: string | null;
}

export interface ProductSummary {
  retailers: number;
  min_price: number | null;
  max_price: number | null;
  avg_price: number | null;
  avg_discount_pct: number | null;
  avg_rating: number | null;
}

export interface ProductDetail extends Product {
  attributes: Record<string, ProductAttribute[]>;
  prices: PriceObservation[];
  stock: StockObservation[];
  reviews: ReviewRow[];
  summary: ProductSummary;
}

export interface Retailer {
  id: number;
  name: string;
  channel: string | null;
  importance: number | null;
  country_code?: string | null;
}

export interface ProductFilters {
  brands: string[];
  franchises: string[];
  categories: string[];
  sports: string[];
  use_cases: string[];
  genders: string[];
  price_bands: string[];
  countries: string[];
  retailers: Retailer[];
}

export interface ProductListResponse {
  total: number;
  items: Product[];
  limit: number;
  offset: number;
}

// ── Matching competitivo ────────────────────────────────────────────
export interface Factor {
  factor: FactorName | string;
  raw_score: number | null;
  weight: number | null;
  contribution: number | null;
  available: boolean;
  detail: Record<string, unknown>;
}

export interface MatchRow {
  id: number;
  match_score: number;
  confidence: Confidence | null;
  coverage: number | null;
  competitor: ProductCard | null;
  factors: Factor[];
  computed_at?: string | null;
}

export interface ProductMatchesResponse {
  product: ProductCard | null;
  matches: MatchRow[];
}

export interface MatchDetail {
  id: number;
  match_score: number;
  confidence: Confidence | null;
  coverage: number | null;
  nike_product: ProductCard | null;
  competitor_product: ProductCard | null;
  factors: Factor[];
  configured_weights: Record<string, number>;
  computed_at?: string | null;
}

export interface MatchListItem {
  id: number;
  match_score: number;
  confidence: Confidence | null;
  nike_product: ProductCard | null;
  competitor_product: ProductCard | null;
}

export interface MatchListResponse {
  total: number;
  items: MatchListItem[];
}

// ── Decisión ────────────────────────────────────────────────────────
export interface Driver {
  name: string;
  value: number | null;
  contribution: number | null;
}

/**
 * Retail media envuelve sus drivers en un objeto de contexto con el racional,
 * las métricas crudas del caso y la lista real de factores adentro.
 */
export interface DriverEnvelope {
  rationale?: string;
  factors?: Driver[];
  [key: string]: unknown;
}

/** Las dos formas que el backend puede emitir. Normalizar con `normalizeDrivers`. */
export type DriversPayload = Array<Driver | DriverEnvelope>;

export interface Recommendation {
  action: string | null;
  rationale: string | null;
  score: number | null;
  confidence: Confidence | null;
  drivers: DriversPayload;
}

export interface Opportunity {
  id: number;
  opportunity_type: string;
  family: OpportunityFamily | string | null;
  severity: Severity | null;
  title: string;
  description: string | null;
  business_importance: number | null;
  confidence: Confidence | null;
  country_code: string | null;
  drivers: DriversPayload;
  nike_product: ProductCard | null;
  competitor_product: ProductCard | null;
  retailer: Retailer | null;
  recommendation: Recommendation | null;
  computed_at?: string | null;
}

export interface FamilyFacet {
  family: string | null;
  n: number;
}
export interface SeverityFacet {
  severity: string | null;
  n: number;
}
export interface TypeFacet {
  opportunity_type: string | null;
  n: number;
}

export interface OpportunityListResponse {
  total: number;
  items: Opportunity[];
  facets: {
    by_family?: FamilyFacet[];
    by_severity?: SeverityFacet[];
    by_type?: TypeFacet[];
  };
  limit?: number;
  offset?: number;
}

export interface RetailMedia {
  id: number;
  score: number | null;
  recommendation: RetailMediaRecommendation | string | null;
  confidence: Confidence | null;
  drivers: DriversPayload;
  nike_product: ProductCard | null;
  competitor_product: ProductCard | null;
  retailer: Retailer | null;
  country_code: string | null;
  computed_at?: string | null;
}

export interface RetailMediaFacet {
  recommendation: string | null;
  n: number;
  avg_score: number | null;
}

export interface RetailMediaResponse {
  total: number;
  items: RetailMedia[];
  facets: { by_recommendation?: RetailMediaFacet[] };
  configured_weights?: Record<string, number>;
  thresholds?: Record<string, number>;
  limit?: number;
  offset?: number;
}

// ── Brand / Consumer Intelligence ───────────────────────────────────
export interface EvidenceItem {
  source?: string;
  url?: string;
  excerpt?: string;
  text?: string;
  quote?: string;
  observed_at?: string;
  date?: string;
  mentions?: number;
  sentiment?: number;
  [key: string]: unknown;
}

export interface EvidenceEnvelope {
  insight_type?: string;
  sources?: string[];
  examples?: EvidenceItem[];
  [key: string]: unknown;
}

export interface BrandInsight {
  id: number;
  country_code: string | null;
  brand_id: number | null;
  brand: string | null;
  dimension: BrandDimension | string | null;
  topic: string | null;
  insight_text: string | null;
  signal_volume: number | null;
  trend: number | null;
  direction: "up" | "down" | "flat" | string | null;
  sentiment: number | null;
  confidence: Confidence | null;
  /**
   * Tres formas posibles: lista de ejemplos, sobre `{sources, examples}` o el
   * texto JSON crudo (así la devuelve `/api/overview`, que no la parsea).
   * Se normaliza siempre con `evidenceOf()`.
   */
  evidence: EvidenceItem[] | EvidenceEnvelope | string | null;
  period_start: string | null;
  period_end: string | null;
  computed_at?: string | null;
}

export interface BrandInsightsResponse {
  total: number;
  items: BrandInsight[];
  taxonomy: Record<string, string[]>;
}

export interface MarketSignal {
  id: number;
  signal_type: string;
  entity_type: string;
  entity_id: string;
  country_code: string | null;
  value: number | null;
  delta: number | null;
  acceleration: number | null;
  period_start: string | null;
  period_end: string | null;
  computed_at?: string | null;
}

export interface MomentumResponse {
  items: MarketSignal[];
}

export interface Topic {
  topic: string | null;
  intent: string | null;
  brand: string | null;
  mentions: number | null;
  sentiment: number | null;
  period_start: string | null;
  period_end: string | null;
}

export interface TopicsResponse {
  items: Topic[];
}

// ── Overview / sistema ──────────────────────────────────────────────
export interface OverviewKpis {
  products: number;
  nike_products: number;
  brands: number;
  retailers: number;
  matches: number;
  opportunities: number;
  critical_opportunities: number;
  high_opportunities: number;
  retail_media_opportunities: number;
  brand_insights: number;
}

export interface OverviewResponse {
  kpis: OverviewKpis;
  top_opportunities: Opportunity[];
  top_risks: Opportunity[];
  retail_media: RetailMedia[];
  competitor_momentum: MarketSignal[];
  top_matches: MatchListItem[];
  brand_highlights: BrandInsight[];
  assortment_gaps: Opportunity[];
}

export interface HealthResponse {
  status: string;
  tables: Record<string, number>;
  empty_tables: string[];
}

/** `/api/config` devuelve el weights.yaml completo. Tipamos lo que consumimos. */
export interface ScoringConfig {
  version?: string;
  competitive_match?: {
    weights?: Record<string, number>;
    min_score_to_persist?: number;
    top_n_per_product?: number;
    confidence_thresholds?: Record<string, number>;
  };
  business_importance?: {
    weights?: Record<string, number>;
    gate_floor?: number;
    severity_thresholds?: Record<string, number>;
  };
  retail_media?: {
    weights?: Record<string, number>;
    thresholds?: Record<string, number>;
  };
  brand_intelligence?: {
    taxonomy?: Record<string, string[]>;
  };
  [key: string]: unknown;
}
