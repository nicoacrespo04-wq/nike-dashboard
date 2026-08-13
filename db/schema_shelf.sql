-- Share of Shelf: visibilidad de Nike/Adidas/Puma en buscadores de retailers.
-- Fuente real: retail_media_search_v8.py (RETAIL_MEDIA_search_v8_*.csv).
-- Nike_Visibility: 0-1, posicion relativa del primer resultado de esa marca
-- para ese termino de busqueda en ese retailer (1 = mejor posicion posible).
CREATE TABLE IF NOT EXISTS retail_media_search (
    id              SERIAL PRIMARY KEY,
    fecha_corrida   DATE,
    scraper         TEXT,
    canal           TEXT NOT NULL,   -- retailer: StockCenter, Dexter, Moov, etc.
    marca           TEXT NOT NULL,   -- Nike / Adidas / Puma
    season          TEXT,
    search_term     TEXT NOT NULL,
    nike_visibility NUMERIC,
    type            TEXT,
    category        TEXT,
    level_1         TEXT,
    level_2         TEXT,
    level_3         TEXT,
    division        TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shelf_canal ON retail_media_search(canal);
CREATE INDEX IF NOT EXISTS idx_shelf_marca ON retail_media_search(marca);
CREATE INDEX IF NOT EXISTS idx_shelf_search_term ON retail_media_search(search_term);
