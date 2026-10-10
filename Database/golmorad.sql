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
