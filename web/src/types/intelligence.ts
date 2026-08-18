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

/**
 * Unidad declarada por el backend para cada número que publica.
 *
 * Existe porque en una misma pantalla conviven magnitudes que NO son
 * comparables: un `ratio` (0,62 = +62%) y unos `pp` (puntos porcentuales) se
 * ven iguales si no se declara cuál es cuál. Se tipa abierto (`| string`)
 * porque el vocabulario lo define el backend y puede crecer.
 */
export type Unit =
  | "score_0_1"
  | "score_0_100"
  | "pct"
  | "pp"
  | "ratio"
  | "count"
  | "score_-1_1"
  | string;

// ── Glosario de factores ────────────────────────────────────────────
/**
 * Qué mide cada variable del motor (`backend/app/api/glossary.py`).
 * Viaja adjunto a las respuestas que publican contribuciones al score:
 * `/matches/{id}`, `/products/{id}/matches` y `/retail-media`.
 */
export interface GlossaryTerm {
  name: string;
  label: string;
  definition: string;
  data: string;
  /** Cómo leer un valor alto. */
  high: string;
  /** Cómo leer un valor bajo. */
  low: string;
  /** Peso configurado en `weights.yaml`, si la familia lo tiene. */
  weight?: number | null;
  /** `false` si el backend publica el término sin definición cargada. */
  defined?: boolean;
}

export interface GlossaryGroup {
  label: string;
  description: string;
  terms: GlossaryTerm[];
}

export type GlossaryGroupName = "competitive_match" | "business_importance" | "retail_media";

/** Grupos que devuelve cada endpoint. Todos opcionales: depende del endpoint. */
export type Glossary = Partial<Record<GlossaryGroupName, GlossaryGroup>>;

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
  glossary?: Glossary;
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
  /** Qué mide cada uno de los 7 factores. Sin esto la explicabilidad no explica. */
  glossary?: Glossary;
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
/**
 * Factor ponderado que empujó un score. `contribution` es el porcentaje del
 * score que aportó, y la suma de los `contribution` de un caso da 100.
 *
 * OJO con la diferencia entre `drivers` y `signals`: un driver es una VARIABLE
 * DEL MODELO (0..1, con peso y contribución); una señal es una MÉTRICA OBSERVADA
 * del caso (90% de stock, -0,1% de gap de precio). Antes viajaban mezcladas en
 * el mismo sobre y la UI tenía que adivinar cuál era cuál.
 */
export interface Driver {
  name: string;
  /** Nombre de negocio ya traducido por el backend. */
  label?: string | null;
  value: number | null;
  unit?: Unit | null;
  contribution: number | null;
  /** Sub-señales que produjeron el valor (peso, referencia, combinación…). */
  detail?: Record<string, unknown>;
}

/**
 * Métrica observada del caso: campo hermano de `drivers`, con su unidad.
 * (`signals` en `/api/retail-media` y `/api/opportunities`.)
 */
export interface SignalValue {
  name: string;
  label: string | null;
  value: number | null;
  unit: Unit | null;
}

/**
 * Forma vieja de retail media: un único objeto de contexto con el racional, las
 * métricas crudas y los factores adentro.
 *
 * El backend ya no la emite —`rationale` es un campo propio del ítem, las
 * métricas están en `signals` y `drivers` es la lista de factores—, pero el
 * normalizador la sigue aceptando para que una base servida por un motor viejo
 * no rompa la pantalla.
 *
 * @deprecated Contrato anterior a "retail media agrupado por producto × retailer".
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
  /** Métricas observadas del caso, con unidad. Hermano de `drivers`. */
  signals?: SignalValue[];
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
  /** Qué mide cada driver de Business Importance. Viaja en la propia respuesta. */
  glossary?: Glossary | null;
  limit?: number;
  offset?: number;
}

/**
 * Un competidor dentro del cuadro (producto Nike × retailer).
 *
 * El motor ya no genera una fila por rival: agrupa por cuadro y adjunta el SET
 * competidor completo, porque la decisión de invertir en visibilidad se toma
 * mirando el conjunto y no un rival aislado. Las banderas dicen qué papel jugó
 * cada uno en el score: `is_leader` es el más relevante, `is_price_reference` el
 * peor caso de precio y `is_momentum_reference` el que traccionó el momentum.
 */
export interface RetailMediaCompetitor {
  competitor_product_id: number;
  match_score: number | null;
  /** Peso del competidor al combinar las señales del set (0..1). */
  relevance_weight: number | null;
  stock_pct: number | null;
  price_gap_pct: number | null;
  nike_price: number | null;
  competitor_price: number | null;
  price_basis: string | null;
  momentum: number | null;
  present_at_retailer: boolean;
  is_leader: boolean;
  is_price_reference: boolean;
  is_momentum_reference: boolean;
  product: ProductCard | null;
}

export interface RetailMedia {
  id: number;
  score: number | null;
  recommendation: RetailMediaRecommendation | string | null;
  confidence: Confidence | null;
  /** Racional en prosa. Campo propio del ítem (antes venía dentro de `drivers`). */
  rationale: string | null;
  /** Factores ponderados del score: `contribution` suma 100. */
  drivers: DriversPayload;
  /** Métricas observadas del cuadro, cada una con su unidad. */
  signals: SignalValue[];
  nike_product: ProductCard | null;
  /** Competidor de referencia (el líder del set). El set completo va en `competitors`. */
  competitor_product: ProductCard | null;
  /**
   * Set competidor del cuadro, ordenado por relevancia.
   * Opcional porque `/api/overview` publica la versión corta del ítem (sin el
   * set): es una tira de resumen, no la pantalla de decisión.
   */
  competitors?: RetailMediaCompetitor[];
  competitor_count?: number;
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
  /** Qué mide cada factor de retail media y de business importance. */
  glossary?: Glossary;
  limit?: number;
  offset?: number;
}

// ── Brand / Consumer Intelligence ───────────────────────────────────

/**
 * Ventana de comparación (`?window=month|quarter|year`).
 *
 * `available: false` no es un error: significa que el histórico cargado no
 * alcanza para la ventana pedida. En ese caso el backend NO publica variaciones
 * inventadas y explica el motivo en `reason` — y la UI tiene que mostrarlo, no
 * dejar la pantalla vacía sin decir por qué.
 */
export type WindowKey = "month" | "quarter" | "year";

export interface ComparisonWindow {
  window: WindowKey | string;
  /** "mes anterior" / "trimestre anterior" / "año anterior". */
  label: string;
  window_days: number;
  compare_days: number;
  required_days: number;
  /** `[desde, hasta]` del período actual. */
  current: [string, string] | string[];
  previous: [string, string] | string[];
  prior: [string, string] | string[];
  data_start: string | null;
  data_end: string | null;
  history_days: number | null;
  available: boolean;
  comparison_available: boolean;
  acceleration_available: boolean;
  reason: string | null;
}

/**
 * Un ejemplo de evidencia con su procedencia.
 *
 * `url` es el link al comentario / review / nota original. Cuando es `null`
 * viene `url_status` + `url_reason`: por qué no hay link (privacidad de la señal
 * social agregada, la tabla de reviews no guarda permalink, la mención
 * editorial se guardó sin URL…). La evidencia se muestra IGUAL con el motivo a
 * la vista: "no hay link" y "no hay evidencia" son cosas distintas y el usuario
 * tiene que poder distinguirlas.
 */
export interface EvidenceItem {
  source?: string;
  source_name?: string;
  source_label?: string;
  type?: string;
  type_label?: string;
  url?: string | null;
  url_available?: boolean;
  url_status?: string | null;
  url_reason?: string | null;
  source_policy?: string | null;
  excerpt?: string;
  text?: string;
  quote?: string;
  observed_at?: string;
  date?: string;
  date_field?: string;
  period_start?: string;
  period_end?: string;
  mentions?: number;
  sentiment?: number;
  [key: string]: unknown;
}

export interface EvidenceEnvelope {
  insight_type?: string;
  sources?: string[];
  examples?: EvidenceItem[];
  evidence_count?: number;
  /** Cuántos ejemplos tienen URL navegable y cuántos no. */
  linked_count?: number;
  unlinked_count?: number;
  [key: string]: unknown;
}

/** Ficha de una fuente de datos declarada por el backend (`sources` de brand). */
export interface SourceInfo {
  collector: string;
  source_name: string;
  homepage: string | null;
  access: string | null;
  terms: string | null;
  stores: string | null;
  enabled: boolean;
  table: string | null;
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
  window?: ComparisonWindow;
  /** `true` si la ventana pedida se recalculó en memoria (no es la persistida). */
  recomputed?: boolean;
  sources?: SourceInfo[];
  /** Diccionario `url_status` → explicación de por qué no hay link. */
  url_reasons?: Record<string, string>;
}

/**
 * Fila de `market_signals` con TODO lo necesario para leerla sin adivinar.
 *
 * Las dos familias que conviven acá no son comparables: `momentum` publica un
 * score 0..100 con delta en RATIO (0,62 = +62%) y `shelf` publica un share en %
 * con delta en PUNTOS PORCENTUALES. Mezclarlas en una grilla sin declarar la
 * unidad era el motivo de que la tabla no se entendiera; por eso cada número
 * viene con la suya y las familias se muestran por separado.
 *
 * Los campos enriquecidos son opcionales porque `/api/overview` devuelve la
 * misma entidad con menos columnas (ahí sí viaja `entity_label`).
 */
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

  /** Nombre real de la entidad ("Open Sports"), no "Retailer #5". */
  entity_label?: string | null;
  entity_label_resolved?: boolean;
  entity_type_label?: string | null;

  signal_label?: string | null;
  /** `momentum` | `shelf` | `other`. Separa lo que no se puede comparar. */
  signal_family?: string | null;
  signal_description?: string | null;

  value_unit?: Unit | null;
  value_unit_label?: string | null;
  value_label?: string | null;

  delta_unit?: Unit | null;
  delta_unit_label?: string | null;
  delta_label?: string | null;
  delta_available?: boolean;
  delta_reason?: string | null;
  /** El delta expresado en %, cuando `delta_unit` es un ratio. */
  delta_pct?: number | null;

  acceleration_unit?: Unit | null;
  acceleration_unit_label?: string | null;
  acceleration_available?: boolean;
  acceleration_reason?: string | null;
  acceleration_pct?: number | null;

  /** Volumen absoluto del período (menciones, notas, reviews). */
  volume?: number | null;
  volume_previous?: number | null;
  volume_unit?: string | null;
  volume_available?: boolean;
  volume_reason?: string | null;

  direction?: "up" | "down" | "flat" | string | null;
  confidence?: Confidence | null;
  coverage?: number | null;
  sources?: string[];
  /** `false` cuando la señal no depende de la ventana elegida (ej. share of shelf). */
  window_applies?: boolean;
  window?: ComparisonWindow | null;
}

export interface SignalTypeFacet {
  signal_type: string;
  count: number;
  label: string | null;
  family: string | null;
  value_unit: Unit | null;
  delta_unit: Unit | null;
}

export interface EntityTypeFacet {
  entity_type: string;
  count: number;
  label: string | null;
}

export interface MomentumResponse {
  total?: number;
  items: MarketSignal[];
  signal_types?: SignalTypeFacet[];
  entity_types?: EntityTypeFacet[];
  /** Vocabulario de unidades: `{score_0_100: "score 0..100", …}`. */
  units?: Record<string, string>;
  window?: ComparisonWindow;
  recomputed?: boolean;
}

export interface Topic {
  topic: string | null;
  intent: string | null;
  brand: string | null;
  mentions: number | null;
  mentions_unit?: string | null;
  mentions_previous?: number | null;
  delta?: number | null;
  delta_unit?: Unit | null;
  delta_pct?: number | null;
  delta_available?: boolean;
  delta_reason?: string | null;
  sentiment: number | null;
  sentiment_unit?: Unit | null;
  source_types?: string[];
  evidence?: EvidenceItem[] | EvidenceEnvelope | string | null;
  period_start: string | null;
  period_end: string | null;
  window?: ComparisonWindow | null;
}

export interface TopicsResponse {
  total?: number;
  items: Topic[];
  window?: ComparisonWindow;
  recomputed?: boolean;
  sources?: SourceInfo[];
  url_reasons?: Record<string, string>;
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

/**
 * Estado del motor de inteligencia.
 *
 * `status` responde "¿este motor sirve para algo ahora mismo?":
 *   - `ok`        hay datos utilizables
 *   - `building`  está reconstruyendo su base (arranque en frío del free tier,
 *                 ~46 s). No hay nada roto; hay que esperar.
 *   - `degraded`  hay productos pero el pipeline no produjo su salida
 *   - `empty`     no hay datos y nadie los está cargando (falta DATABASE_URL
 *                 o la ingesta falló)
 *
 * El HTTP es 200 en los cuatro casos: el proceso está vivo y respondiendo.
 */
export type HealthStatus = 'ok' | 'building' | 'degraded' | 'empty';

export interface HealthData {
  status: HealthStatus;
  products: number;
  price_observations: number;
  opportunities: number;
  competitive_matches: number;
  /** `supabase` si el motor tiene DATABASE_URL; `demo` si va a levantar los 45
   *  productos de demostración. Explica de una una base sospechosamente chica. */
  expected_source: 'supabase' | 'demo';
  build?: {
    state: 'building' | 'ready' | 'failed';
    at?: number;
    age_seconds?: number;
    detail?: string;
  };
}

export interface HealthResponse {
  status: HealthStatus;
  /** Opcional en el tipo porque un motor desplegado antes de este cambio no lo
   *  manda: la UI tiene que seguir funcionando contra esa versión. */
  data?: HealthData;
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
