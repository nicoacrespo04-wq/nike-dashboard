-- ============================================================
-- Competitive & Consumer Intelligence Decision Engine
-- SQLite schema (MVP). Migrable a Postgres sin cambios de modelo.
--
-- Capas:
--   1. Referencia      : brands, countries, retailers
--   2. Catálogo        : products, product_attributes, product_images
--   3. Observaciones   : price_observations, stock_observations, reviews,
--                        editorial_mentions, social_mention_aggregates
--   4. Inteligencia    : competitive_matches, competitive_match_factors,
--                        market_signals, brand_insights
--   5. Decisión        : opportunities, recommendations,
--                        retail_media_opportunities
-- ============================================================

PRAGMA foreign_keys = ON;

-- ── 1. REFERENCIA ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS brands (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    is_focus    INTEGER NOT NULL DEFAULT 0   -- 1 = Nike (marca analizada)
);

CREATE TABLE IF NOT EXISTS countries (
    code        TEXT PRIMARY KEY,            -- 'AR', 'US'
    name        TEXT NOT NULL,
    currency    TEXT
);

CREATE TABLE IF NOT EXISTS retailers (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    country_code TEXT NOT NULL REFERENCES countries(code),
    channel      TEXT NOT NULL,              -- 'D2C' | 'B2B' | 'MARKETPLACE'
    importance   REAL NOT NULL DEFAULT 0.5,  -- 0..1, peso estratégico
    UNIQUE (name, country_code)
);

-- ── 2. CATÁLOGO ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS products (
    id                        INTEGER PRIMARY KEY,
    brand_id                  INTEGER NOT NULL REFERENCES brands(id),
    country_code              TEXT REFERENCES countries(code),

    -- Identificación
    product_name              TEXT NOT NULL,
    normalized_product_name   TEXT,
    franchise                 TEXT,
    model                     TEXT,
    version                   TEXT,
    sku                       TEXT,
    style_code                TEXT,
    launch_date               TEXT,          -- ISO date, aproximada
    lifecycle_stage           TEXT,          -- launch|growth|mature|decline|clearance

    -- Taxonomía
    category                  TEXT,
    subcategory               TEXT,
    sport                     TEXT,
    activity                  TEXT,
    use_case                  TEXT,          -- daily running|race day|trail running|lifestyle|...
    gender                    TEXT,
    age_segment               TEXT,
    performance_vs_lifestyle  TEXT,          -- performance|lifestyle|hybrid
    consumer_segment          TEXT,

    -- Precio de referencia (MSRP en moneda del país)
    msrp                      REAL,
    price_band                TEXT,          -- entry|mid|premium|super-premium

    -- Enlaces
    url                       TEXT,
    image_url                 TEXT,
    description               TEXT,

    enrichment_version        TEXT,
    created_at                TEXT DEFAULT (datetime('now')),
    updated_at                TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_products_brand      ON products(brand_id);
CREATE INDEX IF NOT EXISTS idx_products_franchise  ON products(franchise);
CREATE INDEX IF NOT EXISTS idx_products_use_case   ON products(use_case);
CREATE INDEX IF NOT EXISTS idx_products_country    ON products(country_code);

-- Atributos esparsos (EAV). Permite atributos faltantes sin migraciones.
--   attr_group: 'physical' | 'visual' | 'performance' | 'derived'
CREATE TABLE IF NOT EXISTS product_attributes (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    attr_group  TEXT NOT NULL,
    attr_name   TEXT NOT NULL,
    value_text  TEXT,
    value_num   REAL,
    confidence  REAL DEFAULT 1.0,
    source      TEXT,                        -- scraper|rules|model|manual
    UNIQUE (product_id, attr_name)
);

CREATE INDEX IF NOT EXISTS idx_pattr_product ON product_attributes(product_id);
CREATE INDEX IF NOT EXISTS idx_pattr_name    ON product_attributes(attr_name);

CREATE TABLE IF NOT EXISTS product_images (
    id              INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    url             TEXT,
    is_primary      INTEGER DEFAULT 0,
    embedding       BLOB,                    -- float32 array serializado
    embedding_model TEXT,                    -- 'clip-vit-b32' | 'attribute-fallback'
    embedding_dim   INTEGER
);

-- ── 3. OBSERVACIONES ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS price_observations (
    id            INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    retailer_id   INTEGER NOT NULL REFERENCES retailers(id),
    observed_at   TEXT NOT NULL,             -- ISO date
    full_price    REAL,
    current_price REAL,
    discount_pct  REAL,                      -- 0..100
    currency      TEXT DEFAULT 'ARS'
);

CREATE INDEX IF NOT EXISTS idx_price_prod_ret ON price_observations(product_id, retailer_id);
CREATE INDEX IF NOT EXISTS idx_price_date     ON price_observations(observed_at);

CREATE TABLE IF NOT EXISTS stock_observations (
    id              INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    retailer_id     INTEGER NOT NULL REFERENCES retailers(id),
    observed_at     TEXT NOT NULL,
    in_stock        INTEGER,                 -- 0/1
    availability_pct REAL,                   -- 0..100 (talles disponibles / total)
    sizes_available INTEGER,
    sizes_total     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_stock_prod_ret ON stock_observations(product_id, retailer_id);

CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    retailer_id   INTEGER REFERENCES retailers(id),
    source        TEXT,
    rating        REAL,                      -- 0..5
    review_count  INTEGER,                   -- si es agregado
    review_text   TEXT,                      -- si es individual
    observed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);

-- Menciones editoriales: blogs, medios, guías de compra, rankings.
--   mention_type: 'versus' | 'alternative' | 'same_list' | 'review' | 'ranking'
CREATE TABLE IF NOT EXISTS editorial_mentions (
    id            INTEGER PRIMARY KEY,
    source_name   TEXT,
    url           TEXT,
    title         TEXT,
    published_at  TEXT,
    mention_type  TEXT,
    product_a_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    product_b_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    list_key      TEXT,                      -- agrupa productos de una misma lista
    excerpt       TEXT,
    country_code  TEXT REFERENCES countries(code)
);

CREATE INDEX IF NOT EXISTS idx_edit_a ON editorial_mentions(product_a_id);
CREATE INDEX IF NOT EXISTS idx_edit_b ON editorial_mentions(product_b_id);

-- Señales sociales SIEMPRE agregadas. Nunca a nivel individuo.
CREATE TABLE IF NOT EXISTS social_mention_aggregates (
    id              INTEGER PRIMARY KEY,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    country_code    TEXT REFERENCES countries(code),
    brand_id        INTEGER REFERENCES brands(id),
    product_id      INTEGER REFERENCES products(id) ON DELETE CASCADE,
    co_product_id   INTEGER REFERENCES products(id) ON DELETE CASCADE,
    source_type     TEXT,                    -- forum|social|community|blog_comments
    mention_count   INTEGER DEFAULT 0,
    comention_count INTEGER DEFAULT 0,
    sentiment_score REAL,                    -- -1..1
    intent          TEXT,                    -- want_to_buy|considering|comparing|...
    topic           TEXT,                    -- dimensión de la taxonomía de marca
    sample_evidence TEXT                     -- JSON array de ejemplos públicos
);

CREATE INDEX IF NOT EXISTS idx_social_prod    ON social_mention_aggregates(product_id);
CREATE INDEX IF NOT EXISTS idx_social_copair  ON social_mention_aggregates(product_id, co_product_id);
CREATE INDEX IF NOT EXISTS idx_social_period  ON social_mention_aggregates(period_start, period_end);

-- ── 4. INTELIGENCIA ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS competitive_matches (
    id                    INTEGER PRIMARY KEY,
    nike_product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    competitor_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    match_score           REAL NOT NULL,     -- 0..100, ajustado por evidencia (ranking)
    raw_match_score       REAL,              -- 0..100, sin ajustar (explicación)
    confidence            TEXT,              -- LOW|MEDIUM|HIGH
    coverage              REAL,              -- fracción del peso total con datos
    computed_at           TEXT DEFAULT (datetime('now')),
    UNIQUE (nike_product_id, competitor_product_id)
);

CREATE INDEX IF NOT EXISTS idx_match_nike  ON competitive_matches(nike_product_id);
CREATE INDEX IF NOT EXISTS idx_match_score ON competitive_matches(match_score DESC);

-- Un registro por factor => explicabilidad (feature importance).
CREATE TABLE IF NOT EXISTS competitive_match_factors (
    id           INTEGER PRIMARY KEY,
    match_id     INTEGER NOT NULL REFERENCES competitive_matches(id) ON DELETE CASCADE,
    factor       TEXT NOT NULL,              -- visual|semantic|price|retailer_overlap|editorial|social|reviews
    raw_score    REAL,                       -- 0..1
    weight       REAL,                       -- peso configurado
    contribution REAL,                       -- % del score final (suma 100)
    available    INTEGER NOT NULL DEFAULT 1, -- 0 = sin datos, excluido y renormalizado
    detail       TEXT                        -- JSON con sub-señales
);

CREATE INDEX IF NOT EXISTS idx_factor_match ON competitive_match_factors(match_id);

CREATE TABLE IF NOT EXISTS market_signals (
    id           INTEGER PRIMARY KEY,
    signal_type  TEXT NOT NULL,              -- social_momentum|editorial_momentum|review_momentum|share_of_shelf|promo_intensity
    entity_type  TEXT NOT NULL,              -- product|brand|franchise|retailer
    entity_id    TEXT NOT NULL,
    country_code TEXT REFERENCES countries(code),
    value        REAL,
    delta        REAL,                       -- variación vs período anterior
    acceleration REAL,
    period_start TEXT,
    period_end   TEXT,
    computed_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signal_entity ON market_signals(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_signal_type   ON market_signals(signal_type);

CREATE TABLE IF NOT EXISTS brand_insights (
    id            INTEGER PRIMARY KEY,
    country_code  TEXT REFERENCES countries(code),
    brand_id      INTEGER REFERENCES brands(id),
    dimension     TEXT,                      -- brand_perception|product_perception|price_perception|availability|cultural_relevance|consumer_intent
    topic         TEXT,
    insight_text  TEXT,
    signal_volume INTEGER,
    trend         REAL,                      -- variación %
    direction     TEXT,                      -- up|down|flat
    sentiment     REAL,
    confidence    TEXT,                      -- LOW|MEDIUM|HIGH
    evidence      TEXT,                      -- JSON: fuentes + ejemplos públicos
    period_start  TEXT,
    period_end    TEXT,
    computed_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_insight_country ON brand_insights(country_code);
CREATE INDEX IF NOT EXISTS idx_insight_brand   ON brand_insights(brand_id);

-- ── 5. DECISIÓN ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS opportunities (
    id                    INTEGER PRIMARY KEY,
    opportunity_type      TEXT NOT NULL,     -- ver config/weights.yaml -> opportunity_types
    family                TEXT,              -- pricing|assortment|distribution|stock|retail_media|competitive_threat|brand_momentum
    severity              TEXT,              -- CRITICAL|HIGH|MEDIUM|LOW
    nike_product_id       INTEGER REFERENCES products(id) ON DELETE CASCADE,
    competitor_product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    retailer_id           INTEGER REFERENCES retailers(id),
    country_code          TEXT REFERENCES countries(code),
    title                 TEXT NOT NULL,
    description           TEXT,
    business_importance   REAL,              -- 0..100
    confidence            TEXT,
    drivers               TEXT,              -- JSON [{name, value, contribution}]
    computed_at           TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_opp_importance ON opportunities(business_importance DESC);
CREATE INDEX IF NOT EXISTS idx_opp_type       ON opportunities(opportunity_type);
CREATE INDEX IF NOT EXISTS idx_opp_family     ON opportunities(family);

CREATE TABLE IF NOT EXISTS recommendations (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE CASCADE,
    action         TEXT NOT NULL,
    rationale      TEXT,
    score          REAL,
    confidence     TEXT,
    drivers        TEXT                      -- JSON
);

CREATE TABLE IF NOT EXISTS retail_media_opportunities (
    id                    INTEGER PRIMARY KEY,
    nike_product_id       INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    competitor_product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    retailer_id           INTEGER REFERENCES retailers(id),
    country_code          TEXT REFERENCES countries(code),
    score                 REAL,              -- 0..100
    recommendation        TEXT,              -- INVEST_IN_RETAIL_MEDIA | EVALUATE_PRICE_ACTION_BEFORE_MEDIA | ...
    confidence            TEXT,
    drivers               TEXT,              -- JSON
    computed_at           TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rmo_score ON retail_media_opportunities(score DESC);
