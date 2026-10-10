PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS market_candles (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    adjusted_close REAL,
    volume REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(symbol, interval, timestamp, source),
    CHECK(high >= low),
    CHECK(volume >= 0)
);

CREATE INDEX IF NOT EXISTS idx_market_candles_lookup
    ON market_candles(symbol, interval, timestamp DESC);

CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    language TEXT,
    sentiment REAL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_published_at
    ON news_articles(published_at DESC);

CREATE TABLE IF NOT EXISTS sentiment_indices (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    index_name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value INTEGER NOT NULL CHECK(value BETWEEN 0 AND 100),
    classification TEXT NOT NULL,
    source_url TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT,
    UNIQUE(provider, index_name, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_sentiment_observed_at
    ON sentiment_indices(index_name, observed_at DESC);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'success', 'partial', 'failed')),
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_started
    ON sync_runs(dataset, started_at DESC);

-- Paper trading is deliberately isolated from exchange order tables/clients.
CREATE TABLE IF NOT EXISTS paper_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decision_id TEXT,
    order_id TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_decisions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    candle_timestamp TEXT NOT NULL,
    signal TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, interval, candle_timestamp)
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES paper_decisions(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    status TEXT NOT NULL CHECK(status IN ('filled', 'partially_filled', 'rejected')),
    decision_at TEXT NOT NULL,
    fill_at TEXT NOT NULL,
    requested_quantity REAL NOT NULL,
    filled_quantity REAL NOT NULL,
    fill_price REAL,
    fee REAL NOT NULL,
    spread_cost REAL NOT NULL,
    slippage_cost REAL NOT NULL,
    latency_seconds REAL NOT NULL,
    liquidity_limit REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_accounts (
    id TEXT PRIMARY KEY,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    position_quantity REAL NOT NULL DEFAULT 0,
    average_entry REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_control (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    stopped INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_account_marks (
    id TEXT PRIMARY KEY,
    marked_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    position_value REAL NOT NULL
);
