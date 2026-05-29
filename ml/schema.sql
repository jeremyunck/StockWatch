-- ============================================================
-- Stock buy-signal training data schema
-- Label rule (v1): label = 1 if intraday HIGH reaches
--   entry_price * 1.05 within the next 5 TRADING days, else 0.
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Daily OHLC bars. The source of truth for both feature
-- computation and label resolution. Refresh once per day.
CREATE TABLE IF NOT EXISTS price_history (
    ticker      TEXT    NOT NULL,
    bar_date    TEXT    NOT NULL,          -- ISO date 'YYYY-MM-DD' (trading day)
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    PRIMARY KEY (ticker, bar_date)
);

-- Point-in-time observations captured by the polling script.
-- One row per ticker per poll. This is the "entry" candidate
-- and the anchor for a label. Store raw inputs; derive features
-- at training time so the feature recipe can change freely.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    captured_at     TEXT    NOT NULL,      -- ISO datetime (entry timestamp)
    bar_date        TEXT    NOT NULL,      -- trading day this snapshot belongs to
    entry_price     REAL    NOT NULL,      -- price at capture; the label anchor
    -- raw indicator inputs (nullable so a partial pull still stores)
    rsi14           REAL,
    macd_line       REAL,
    macd_signal     REAL,
    macd_hist       REAL,
    sma50           REAL,
    sma200          REAL,
    ema20           REAL,
    bb_upper        REAL,
    bb_lower        REAL,
    bb_mid          REAL,
    atr14           REAL,
    vol_avg20       REAL,
    obv             REAL,
    ret_5d          REAL,
    ret_20d         REAL,
    hi_52w          REAL,
    lo_52w          REAL,
    news_count_24h  INTEGER DEFAULT 0,
    UNIQUE (ticker, captured_at)
);

-- Labels resolved retroactively once the 5-trading-day window
-- has fully elapsed. label stays NULL until then.
CREATE TABLE IF NOT EXISTS labels (
    snapshot_id     INTEGER PRIMARY KEY,
    entry_price     REAL    NOT NULL,
    target_price    REAL    NOT NULL,      -- entry_price * 1.05
    window_days     INTEGER NOT NULL DEFAULT 5,
    resolved_at     TEXT,                  -- when the label was computed
    hit_date        TEXT,                  -- trading day target was hit (if any)
    max_high        REAL,                  -- highest high seen in window (diagnostic)
    label           INTEGER,               -- 1 = hit +5%, 0 = did not, NULL = unresolved
    bars_observed   INTEGER DEFAULT 0,     -- trading days seen so far in window
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_snap_ticker_date ON snapshots(ticker, bar_date);
CREATE INDEX IF NOT EXISTS idx_price_ticker_date ON price_history(ticker, bar_date);
CREATE INDEX IF NOT EXISTS idx_labels_unresolved ON labels(label) WHERE label IS NULL;
