-- Abilitazione estensioni
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- per ricerca fuzzy sui nomi prodotto

-- Categorie prodotti
CREATE TABLE categories (
    id          SERIAL PRIMARY KEY,
    parent_id   INTEGER REFERENCES categories(id),
    name        VARCHAR(100) NOT NULL,
    slug        VARCHAR(100) UNIQUE NOT NULL,
    icon        VARCHAR(50),
    level       INTEGER DEFAULT 0,
    sort_order  INTEGER DEFAULT 0
);

-- Catene supermercati
CREATE TABLE chains (
    id               SERIAL PRIMARY KEY,
    name             VARCHAR(100) NOT NULL,
    slug             VARCHAR(50) UNIQUE NOT NULL,
    logo_url         TEXT,
    has_online_shop  BOOLEAN DEFAULT FALSE,
    shop_url         TEXT,
    integration_type VARCHAR(20) DEFAULT 'redirect', -- redirect, api, none
    is_active        BOOLEAN DEFAULT TRUE
);

-- Punti vendita (geolocalizzati)
CREATE TABLE stores (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chain_id            INTEGER NOT NULL REFERENCES chains(id),
    external_id         VARCHAR(100),
    name                VARCHAR(200),
    address             TEXT,
    city                VARCHAR(100),
    province            VARCHAR(50),
    postal_code         VARCHAR(10),
    coordinates         GEOMETRY(Point, 4326) NOT NULL,
    phone               VARCHAR(20),
    opening_hours       JSONB,
    has_delivery        BOOLEAN DEFAULT FALSE,
    has_click_collect   BOOLEAN DEFAULT FALSE,
    is_active           BOOLEAN DEFAULT TRUE,
    last_verified       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stores_coordinates ON stores USING GIST(coordinates);
CREATE INDEX idx_stores_chain ON stores(chain_id);
-- I negozi virtuali di spesa online (external_id '*-online') sono filtrati con
-- LIKE '%-online' in molte query: il wildcard iniziale non e' indicizzabile,
-- quindi indice parziale che li marca.
CREATE INDEX idx_stores_online ON stores(id) WHERE external_id LIKE '%-online';

-- Prodotti
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    barcode         VARCHAR(50),
    name            VARCHAR(500) NOT NULL,
    brand           VARCHAR(200),
    category_id     INTEGER REFERENCES categories(id),
    description     TEXT,
    image_url       TEXT,
    unit            VARCHAR(10),        -- kg, l, pz, g, ml
    unit_quantity   NUMERIC(10,3),      -- es. 0.5 per 500g
    is_verified     BOOLEAN DEFAULT FALSE,
    source          VARCHAR(30),        -- open_food_facts, pepesto, manual
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL;
CREATE INDEX idx_products_name_trgm ON products USING GIN(name gin_trgm_ops);

-- Alias barcode → prodotto: quando il dedup unisce due prodotti, il barcode
-- del duplicato eliminato resta mappato al superstite. Così i futuri scrape
-- ritrovano il prodotto giusto invece di ricreare un doppione (causa di
-- prezzi stantii e prodotti "splittati" tra un run e l'altro).
CREATE TABLE product_aliases (
    alias_barcode TEXT PRIMARY KEY,
    product_id    UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_product_aliases_product ON product_aliases(product_id);

-- Prezzi (serie temporale — solo il prezzo più recente è is_current=TRUE)
CREATE TABLE prices (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id      UUID NOT NULL REFERENCES products(id),
    store_id        UUID NOT NULL REFERENCES stores(id),
    price           NUMERIC(8,2) NOT NULL,
    original_price  NUMERIC(8,2),
    promo_label     VARCHAR(200),
    promo_expires   DATE,
    price_per_unit  NUMERIC(10,4),      -- prezzo/kg o prezzo/litro
    in_stock        BOOLEAN DEFAULT TRUE,
    is_current      BOOLEAN DEFAULT TRUE,
    source          VARCHAR(30),
    product_url     TEXT,               -- link diretto alla pagina prodotto sul sito della catena
    scraped_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_prices_product_store ON prices(product_id, store_id) WHERE is_current = TRUE;
CREATE INDEX idx_prices_scraped_at ON prices(scraped_at DESC);
-- Indice PIENO su product_id (non parziale): serve al dedup per ri-puntare
-- le FK senza seq-scan dell'intera tabella prezzi (storico compreso).
CREATE INDEX idx_prices_product_id ON prices(product_id);
-- Indice che parte da store_id (solo prezzi correnti): serve all'ottimizzatore
-- lista (optimize-quick) che raggruppa per negozio, evitando seq-scan.
CREATE INDEX idx_prices_store_current ON prices(store_id) WHERE is_current = TRUE;

-- Utenti
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),
    full_name       VARCHAR(200),
    subscription    VARCHAR(20) DEFAULT 'free',  -- free, premium
    sub_expires_at  TIMESTAMPTZ,
    gdpr_consent_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

-- Posizioni salvate dall'utente
CREATE TABLE user_locations (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label       VARCHAR(100),           -- "Casa", "Lavoro"
    address     TEXT,
    coordinates GEOMETRY(Point, 4326),
    is_default  BOOLEAN DEFAULT FALSE
);

-- Liste della spesa
CREATE TABLE shopping_lists (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID REFERENCES users(id) ON DELETE CASCADE,
    name                VARCHAR(200) DEFAULT 'Lista spesa',
    optimization_result JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE list_items (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    list_id     UUID NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
    product_id  UUID REFERENCES products(id),
    product_name VARCHAR(500),          -- fallback se prodotto non nel db
    quantity    NUMERIC(6,2) DEFAULT 1,
    unit        VARCHAR(20),
    is_checked  BOOLEAN DEFAULT FALSE,
    sort_order  INTEGER DEFAULT 0
);

-- Telemetria ricerche (alimenta gli agenti: cosa cercano gli utenti, cosa NON
-- trova risultati = gap di catalogo/feature). Vedi agents/.
CREATE TABLE search_log (
    id          BIGSERIAL PRIMARY KEY,
    query       TEXT NOT NULL,
    n_results   INTEGER NOT NULL DEFAULT 0,
    lat         DOUBLE PRECISION,
    lng         DOUBLE PRECISION,
    radius_km   NUMERIC(5,1),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_search_log_created ON search_log(created_at DESC);
CREATE INDEX idx_search_log_zero ON search_log(lower(query)) WHERE n_results = 0;

-- Alert prezzi
CREATE TABLE price_alerts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id      UUID NOT NULL REFERENCES products(id),
    threshold_price NUMERIC(8,2) NOT NULL,
    radius_km       INTEGER DEFAULT 5,
    is_active       BOOLEAN DEFAULT TRUE,
    last_triggered  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Dati iniziali catene
INSERT INTO chains (name, slug, logo_url, has_online_shop, shop_url, integration_type) VALUES
('Esselunga',   'esselunga',  NULL, TRUE,  'https://www.esselunga.it/area-utente/spesa/home.html', 'redirect'),
('Conad',       'conad',      NULL, TRUE,  'https://www.conad.it/conad/home.html',                  'redirect'),
('Carrefour',   'carrefour',  NULL, TRUE,  'https://www.carrefour.it/spesa-online/',                 'redirect'),
('Coop',        'coop',       NULL, TRUE,  'https://www.cooponline.it',                              'redirect'),
('Lidl',        'lidl',       NULL, FALSE, NULL,                                                      'none'),
('Eurospin',    'eurospin',   NULL, FALSE, NULL,                                                      'none'),
('Pam',         'pam',        NULL, TRUE,  'https://www.pampanorama.it/spesa-online',                'redirect'),
('MD',          'md',         NULL, FALSE, NULL,                                                      'none'),
('Iper',        'iper',       NULL, FALSE, NULL,                                                      'none'),
('Famila',      'famila',     NULL, FALSE, NULL,                                                      'none');

-- Categorie base
INSERT INTO categories (name, slug, level, sort_order) VALUES
('Frutta e Verdura', 'frutta-verdura', 0, 1),
('Carne e Pesce',    'carne-pesce',    0, 2),
('Latticini e Uova', 'latticini-uova', 0, 3),
('Pane e Dolci',     'pane-dolci',     0, 4),
('Pasta e Riso',     'pasta-riso',     0, 5),
('Conserve',         'conserve',       0, 6),
('Bevande',          'bevande',        0, 7),
('Surgelati',        'surgelati',      0, 8),
('Igiene e Casa',    'igiene-casa',    0, 9),
('Altro',            'altro',          0, 10);
