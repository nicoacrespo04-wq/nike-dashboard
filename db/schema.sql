-- ============================================================
-- NIKE DASHBOARD — Schema PostgreSQL
-- Aplicar en Supabase SQL Editor o con psql
-- ============================================================

-- Tabla principal de pricing (replica del CSV combinado)
CREATE TABLE IF NOT EXISTS pricing_data (
    id                          BIGSERIAL PRIMARY KEY,
    fecha_corrida               DATE,
    scraper                     TEXT,
    canal                       TEXT,
    marca                       TEXT,
    season                      TEXT,
    style_color                 TEXT,
    product_code_competitor     TEXT,
    marketing_name              TEXT,
    division                    TEXT,
    category                    TEXT,
    franchise_scrapper          TEXT,
    gender                      TEXT,
    gama_botin                  TEXT,
    plato                       TEXT,
    nddc                        TEXT,
    productcode_competitor      TEXT,
    product_name_competitor     TEXT,
    category_competitor         TEXT,
    division_competitor         TEXT,
    franchise_competitor        TEXT,
    gender_competitor           TEXT,
    size_available_competitor   INTEGER,
    size_available_nike         INTEGER,
    link_pdp_competitor         TEXT,
    competitor_full_price       NUMERIC(12,2),
    competitor_markdown         NUMERIC(12,2),
    competitor_final_price      NUMERIC(12,2),
    competitor_shipping         NUMERIC(12,2),
    competitor_price_shipping   NUMERIC(12,2),
    cuotas_competitor           TEXT,
    nike_full_price             NUMERIC(12,2),
    nike_markdown               NUMERIC(12,2),
    nike_final_price            NUMERIC(12,2),
    nike_shipping               NUMERIC(12,2),
    nike_price_shipping         NUMERIC(12,2),
    cuotas_nike                 TEXT,
    gap_final_price_pct         NUMERIC(10,6),
    gap_full_price_pct          NUMERIC(10,6),
    gap_shipping_pct            NUMERIC(10,6),
    bml_final_price             TEXT,
    bml_full_price              TEXT,
    bml_with_shipping           TEXT,
    bml_cuotas                  TEXT,
    fx_ars_usd                  NUMERIC(12,4),
    competitor_price_usd        NUMERIC(12,4),
    competitor_price_usd_iva    NUMERIC(12,4),
    competitor_price_usd_iva_bf NUMERIC(12,4),
    gap_full_price_usd          NUMERIC(12,4),
    gap_full_price_usd_iva      NUMERIC(12,4),
    gap_full_price_usd_iva_bf   NUMERIC(12,4),
    gap_final_price_usd         NUMERIC(12,4),
    gap_markdown_price_usd      NUMERIC(12,4),
    bml_vs_nikear               TEXT,
    bml_vs_nikear_iva           TEXT,
    bml_vs_nikear_iva_bf        TEXT,
    source_file                 TEXT,
    text_sizes_nike             TEXT,
    text_sizes_competitor       TEXT,
    pdp_nike                    TEXT,
    rango_precio                TEXT,
    precio_sugerido             NUMERIC(12,2),
    price_index                 NUMERIC(10,6),
    silueta                     TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para performance en los dashboards
CREATE INDEX IF NOT EXISTS idx_pricing_marca        ON pricing_data(marca);
CREATE INDEX IF NOT EXISTS idx_pricing_scraper      ON pricing_data(scraper);
CREATE INDEX IF NOT EXISTS idx_pricing_franchise    ON pricing_data(franchise_competitor);
CREATE INDEX IF NOT EXISTS idx_pricing_silueta      ON pricing_data(silueta);
CREATE INDEX IF NOT EXISTS idx_pricing_division     ON pricing_data(division_competitor);
CREATE INDEX IF NOT EXISTS idx_pricing_bml          ON pricing_data(bml_final_price);
CREATE INDEX IF NOT EXISTS idx_pricing_fecha        ON pricing_data(fecha_corrida DESC);
CREATE INDEX IF NOT EXISTS idx_pricing_style_color  ON pricing_data(style_color);
CREATE INDEX IF NOT EXISTS idx_pricing_category     ON pricing_data(category_competitor);
CREATE INDEX IF NOT EXISTS idx_pricing_marca_scraper ON pricing_data(marca, scraper);
CREATE INDEX IF NOT EXISTS idx_pricing_bml_marca    ON pricing_data(bml_final_price, marca);

-- Tabla de log de scraper runs
CREATE TABLE IF NOT EXISTS scrape_runs_nike (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraper_name    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    rows_inserted   INTEGER DEFAULT 0,
    rows_updated    INTEGER DEFAULT 0,
    errors          JSONB DEFAULT '[]',
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_status ON scrape_runs_nike(status, started_at DESC);

-- Tabla de alertas de scrapers (se envían por email)
CREATE TABLE IF NOT EXISTS scraper_alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraper_name    TEXT NOT NULL,
    alert_type      TEXT NOT NULL,  -- scraper_failed | blocked | no_data | price_spike
    severity        TEXT NOT NULL DEFAULT 'warning',  -- info | warning | critical
    message         TEXT NOT NULL,
    context         JSONB DEFAULT '{}',
    sent_email      BOOLEAN DEFAULT FALSE,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_sent     ON scraper_alerts(sent_email) WHERE sent_email = FALSE;
CREATE INDEX IF NOT EXISTS idx_alerts_scraper  ON scraper_alerts(scraper_name, created_at DESC);

-- Vista útil: resumen BML por scraper y marca
CREATE OR REPLACE VIEW v_bml_summary AS
SELECT
    scraper,
    marca,
    COUNT(*)                                                    AS total,
    COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')           AS beat,
    COUNT(*) FILTER (WHERE bml_final_price = 'MEET')           AS meet,
    COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')           AS lose,
    COUNT(*) FILTER (WHERE bml_final_price NOT IN ('BEAT','MEET','LOSE')) AS nd,
    ROUND(AVG(competitor_final_price)::numeric, 0)             AS avg_price,
    ROUND(AVG(gap_final_price_pct)::numeric, 4)                AS avg_gap_pct
FROM pricing_data
GROUP BY scraper, marca;

-- Vista: top franchises competencia
CREATE OR REPLACE VIEW v_franchise_summary AS
SELECT
    franchise_competitor                                        AS franchise,
    marca,
    division_competitor                                         AS division,
    COUNT(*)                                                    AS total_skus,
    ROUND(AVG(competitor_final_price)::numeric, 0)             AS avg_price,
    ROUND(AVG(gap_final_price_pct)::numeric, 4)                AS avg_gap_pct,
    COUNT(*) FILTER (WHERE bml_final_price = 'BEAT')           AS beat,
    COUNT(*) FILTER (WHERE bml_final_price = 'MEET')           AS meet,
    COUNT(*) FILTER (WHERE bml_final_price = 'LOSE')           AS lose,
    ROUND(AVG(size_available_competitor)::numeric, 1)          AS avg_sizes
FROM pricing_data
WHERE franchise_competitor IS NOT NULL AND franchise_competitor <> ''
GROUP BY franchise_competitor, marca, division_competitor;

-- Vista: share of shelf por retailer
CREATE OR REPLACE VIEW v_shelf_share AS
SELECT
    scraper,
    COUNT(*) FILTER (WHERE marca = 'NIKE')                     AS nike_count,
    COUNT(*) FILTER (WHERE marca = 'ADIDAS')                   AS adidas_count,
    COUNT(*) FILTER (WHERE marca = 'Puma')                     AS puma_count,
    COUNT(*)                                                    AS total,
    ROUND(
        (COUNT(*) FILTER (WHERE marca = 'NIKE') * 100.0 / NULLIF(COUNT(*), 0))::numeric, 1
    )                                                           AS nike_share_pct,
    ROUND(
        (COUNT(*) FILTER (WHERE marca = 'ADIDAS') * 100.0 / NULLIF(COUNT(*), 0))::numeric, 1
    )                                                           AS adidas_share_pct,
    ROUND(
        (COUNT(*) FILTER (WHERE marca = 'Puma') * 100.0 / NULLIF(COUNT(*), 0))::numeric, 1
    )                                                           AS puma_share_pct
FROM pricing_data
WHERE scraper NOT LIKE '%AdidasPuma%'
  AND scraper NOT IN ('ADIDAS_7','Puma_AR','nike_ar_general','nike_co_general','nike_us_general','URU','USA')
GROUP BY scraper;
